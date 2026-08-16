from __future__ import annotations

import copy
import hashlib
import os
from typing import Any, NoReturn
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdEvent, MdReplicaRun, MdRun, get_session
from schemas import JobCreate
from paths import get_results_dir
from services.stage_review import resolve_output_dir

from services.md.cancel_actuator import cancel_running_md_run
from services.md.chemistry_catalog import ChemistryCatalogError, get_chemistry_catalog
from services.md.feature_gate import molecular_dynamics_feature_enabled
from services.md.launch_contract import MDLaunchError, approved_pack_inventory
from services.md.pause_actuator import pause_running_md_run
from services.md.read_model import md_queue_snapshot, md_run_snapshot
from services.md.state import (
    MdStateError, create_replica_attempt, resume_run,
    retry_replica_attempt,
)

router = APIRouter(prefix="/api/molecular-dynamics", tags=["molecular-dynamics"])


class LifecycleCommand(BaseModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class FailedLaunchDeleteCommand(BaseModel):
    expected_state_version: int = Field(ge=0)


class ResumeCommand(LifecycleCommand):
    pass


class RetryCommand(LifecycleCommand):
    replica_index: int = Field(ge=0, le=63)


def sanitize_materialized_v2_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Turn the immutable server materialization back into a caller-safe v2 request."""
    raw = copy.deepcopy({key: value for key, value in contract.items() if key != "engine_runtime"})
    raw["input"] = dict(raw.get("input") or {})
    for key in ("structure_sha256", "coordinates_sha256", "topology_sha256", "structure_bytes", "coordinates_bytes", "topology_bytes", "topology_closure"):
        raw["input"].pop(key, None)
    raw["chemistry"] = dict(raw.get("chemistry") or {})
    for key in ("assurance", "family", "version", "resolved_preparation", "runtime_identity", "resolved_approved_packs"):
        raw["chemistry"].pop(key, None)
    return raw


async def _pre_replica_terminal_parent(session: AsyncSession, job_id: str) -> tuple[Job, MdRun]:
    job = (await session.execute(select(Job).where(Job.id == job_id).with_for_update())).scalar_one_or_none()
    run = await session.get(MdRun, job_id)
    if job is None or run is None or job.model_id != "molecular_dynamics" or job.mode != "simulate" or job.parent_job_id is not None:
        raise HTTPException(status_code=409, detail={"code": "MD_PARENT_REQUIRED", "message": "MD parent job is required"})
    children = await session.scalar(select(func.count(Job.id)).where(Job.parent_job_id == job_id))
    replicas = await session.scalar(select(func.count(MdReplicaRun.id)).where(MdReplicaRun.md_job_id == job_id))
    artifacts = await session.scalar(select(func.count(JobArtifact.id)).where(JobArtifact.owner_job_id == job_id))
    checkpoints = await session.scalar(
        select(func.count(MdCheckpoint.id))
        .select_from(MdCheckpoint)
        .join(MdAttemptSegment, MdCheckpoint.segment_id == MdAttemptSegment.id)
        .join(MdReplicaRun, MdAttemptSegment.replica_run_id == MdReplicaRun.id)
        .where(MdReplicaRun.md_job_id == job_id, MdCheckpoint.accepted.is_(True))
    )
    if run.phase not in {"failed", "cancelled"} or any((children, replicas, artifacts, checkpoints)):
        raise HTTPException(status_code=409, detail={"code": "MD_PRE_REPLICA_TERMINAL_REQUIRED", "message": "Only a terminal MD parent with no replicas, children, artifacts, or checkpoints is eligible."})
    return job, run


def _canonical_failed_launch_root(job: Job) -> Path:
    """Return a safe direct child of the configured Development results root."""
    root = get_results_dir().expanduser().resolve()
    output = resolve_output_dir(job.output_dir)
    if output is None:
        raise HTTPException(status_code=409, detail={"code": "MD_OUTPUT_ROOT_INVALID", "message": "The failed launch has no canonical result root."})
    try:
        resolved = output.expanduser().resolve(strict=False)
    except OSError as exc:
        raise HTTPException(status_code=409, detail={"code": "MD_OUTPUT_ROOT_INVALID", "message": "The failed launch result root is unsafe."}) from exc
    if output.is_symlink() or resolved.parent != root or resolved == root:
        raise HTTPException(status_code=409, detail={"code": "MD_OUTPUT_ROOT_INVALID", "message": "The failed launch result root is not a canonical Development results child."})
    return resolved


def _trusted_reorchestration_input_resolver(job: Job, contract: dict[str, Any]):
    """Admit only digest-bound snapshots owned by this failed MD result root."""
    root = _canonical_failed_launch_root(job)
    input_root_path = root / "inputs"
    if input_root_path.is_symlink():
        raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "The failed launch input snapshot root is unsafe."})
    try:
        input_root = input_root_path.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "The failed launch input snapshot root is missing."}) from exc
    if not input_root.is_dir() or input_root.parent != root:
        raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "The failed launch input snapshot root is not job-owned."})

    admitted: dict[str, tuple[str, str, int]] = {}
    input_value = contract.get("input")
    input_contract: dict[str, Any] = input_value if isinstance(input_value, dict) else {}
    for field in ("structure", "coordinates", "topology"):
        value = input_contract.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        source = Path(value)
        if source.is_symlink():
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": f"The failed launch {field} snapshot is unsafe."})
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": f"The failed launch {field} snapshot is missing."}) from exc
        if not resolved.is_file() or not resolved.is_relative_to(input_root):
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": f"The failed launch {field} snapshot is not job-owned."})
        expected_sha = input_contract.get(f"{field}_sha256")
        expected_bytes = input_contract.get(f"{field}_bytes")
        digest = hashlib.sha256()
        size = 0
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        if (
            not isinstance(expected_sha, str)
            or type(expected_bytes) is not int
            or expected_sha != digest.hexdigest()
            or expected_bytes != size
        ):
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": f"The failed launch {field} snapshot digest no longer matches its durable contract."})
        admitted[value] = (str(resolved), expected_sha, expected_bytes)

    if not admitted:
        raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "The failed launch has no admissible immutable input snapshot."})

    def resolve(value: str) -> str:
        admitted_entry = admitted.get(value)
        if admitted_entry is None:
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "Re-orchestration requested an input outside the failed launch snapshot."})
        admitted_path, expected_sha, expected_bytes = admitted_entry
        candidate = Path(admitted_path)
        digest = hashlib.sha256()
        size = 0
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != expected_sha or size != expected_bytes:
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": "The failed launch input snapshot changed during re-orchestration."})
        return admitted_path

    return resolve


class ReplicaAttemptCommand(BaseModel):
    replica_index: int = Field(ge=0, le=63)
    attempt: int = Field(ge=0, le=100)
    engine: str = Field(pattern=r"^(gromacs|openmm)$")
    execution_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compatibility_key: str = Field(pattern=r"^[0-9a-f]{64}$")


def _state_http_error(exc: MdStateError) -> NoReturn:
    status = 404 if exc.code.endswith("NOT_FOUND") else 409
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc


def _catalog_view_or_503():
    try:
        return get_chemistry_catalog().view()
    except ChemistryCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics launch service is temporarily unavailable.",
            },
        ) from exc


@router.get("/capabilities")
def molecular_dynamics_capabilities() -> dict:
    view = _catalog_view_or_503()
    profiles = view.list_profiles()
    selectable = [profile["id"] for profile in profiles if profile["states"]["selectable"]]
    return {
        "schema": "bms.md.capabilities.v1",
        "feature_enabled": molecular_dynamics_feature_enabled(),
        "experimental": True,
        "contract_schemas": ["bms.md.job.v2", "bms.md.job.v1"],
        "catalog_schema": "bms.md.chemistry-profile.v1",
        "catalog_digest": view.catalog_digest,
        "automatic_preparation": {
            "available": bool(selectable),
            "selectable_profile_ids": selectable,
            "scope_limited": True,
        },
        "runtime_probe": view.public_probe_summary(),
        "public_refresh_supported": False,
    }


@router.get("/chemistry-profiles")
def list_molecular_dynamics_chemistry_profiles() -> dict:
    view = _catalog_view_or_503()
    profiles = view.list_profiles()
    return {
        "schema": "bms.md.chemistry-profile-inventory.v1",
        "catalog_digest": view.catalog_digest,
        "profiles": profiles,
        "selectable_profile_ids": [
            profile["id"] for profile in profiles if profile["states"]["selectable"]
        ],
        "count": len(profiles),
        "bounded": True,
    }


@router.get("/approved-packs")
def list_molecular_dynamics_approved_packs() -> dict:
    try:
        return approved_pack_inventory()
    except MDLaunchError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/chemistry-profiles/{profile_id}")
def get_molecular_dynamics_chemistry_profile(profile_id: str) -> dict:
    view = _catalog_view_or_503()
    profile = view.get_profile(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MD_CHEMISTRY_PROFILE_UNKNOWN",
                "message": f"Unknown molecular-dynamics chemistry profile: {profile_id}",
            },
        )
    return profile


@router.get("/runs")
async def list_md_runs(
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await md_queue_snapshot(session, limit=limit)


@router.get("/runs/{job_id}")
async def get_md_run(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    payload = await md_run_snapshot(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "MD_RUN_NOT_FOUND", "message": "MD run was not found"})
    return payload


@router.post("/runs/{job_id}/pause")
async def pause_md_run(job_id: str, command: LifecycleCommand,
                       session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await pause_running_md_run(
            session,
            job_id=job_id,
            expected_version=command.expected_state_version,
            idempotency_key=command.idempotency_key,
        )
        await session.commit()
    except MdStateError as exc:
        if exc.code in {"MD_PAUSE_ACTUATION_FAILED", "MD_PAUSE_CHECKPOINT_INVALID"}:
            await session.commit()
        else:
            await session.rollback()
        _state_http_error(exc)
    payload = await md_run_snapshot(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "MD_RUN_NOT_FOUND", "message": "MD run was not found"})
    return payload


@router.post("/runs/{job_id}/resume")
async def resume_md_run(job_id: str, command: ResumeCommand,
                        session: AsyncSession = Depends(get_session)) -> dict:
    try:
        segments = await resume_run(
            session, job_id=job_id, expected_version=command.expected_state_version,
            idempotency_key=command.idempotency_key,
        )
        await session.commit()
    except MdStateError as exc:
        await session.rollback(); _state_http_error(exc)
    return {"schema": "bms.md.resume-receipt.v1", "job_id": job_id,
            "segment_ids": [segment.id for segment in segments]}


@router.post("/runs/{job_id}/cancel")
async def cancel_md_run(job_id: str, command: LifecycleCommand,
                        session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await cancel_running_md_run(
            session,
            job_id=job_id,
            expected_version=command.expected_state_version,
            idempotency_key=command.idempotency_key,
        )
        await session.commit()
    except MdStateError as exc:
        if exc.code == "MD_CANCEL_ACTUATION_FAILED":
            await session.commit()
        else:
            await session.rollback()
        _state_http_error(exc)
    payload = await md_run_snapshot(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "MD_RUN_NOT_FOUND", "message": "MD run was not found"})
    return payload


@router.post("/runs/{job_id}/retry")
async def retry_md_replica(job_id: str, command: RetryCommand,
                           session: AsyncSession = Depends(get_session)) -> dict:
    try:
        replica = await retry_replica_attempt(
            session, job_id=job_id, replica_index=command.replica_index,
            expected_version=command.expected_state_version,
            idempotency_key=command.idempotency_key,
        )
        await session.commit()
    except MdStateError as exc:
        await session.rollback(); _state_http_error(exc)
    return {
        "schema": "bms.md.retry-receipt.v1", "job_id": job_id,
        "replica_run_id": replica.id, "child_job_id": replica.child_job_id,
        "replica_index": replica.replica_index, "attempt": replica.attempt,
    }


@router.post("/runs/{job_id}/reorchestrate", status_code=201)
async def reorchestrate_failed_md_run(job_id: str, command: LifecycleCommand,
                                      session: AsyncSession = Depends(get_session)) -> dict:
    """Create a fresh MD root from an eligible failed pre-replica launch."""
    existing = await session.scalar(select(MdEvent).where(MdEvent.idempotency_key == command.idempotency_key))
    if existing is not None:
        if existing.md_job_id != job_id or existing.event_type != "reorchestrate" or existing.expected_state_version != command.expected_state_version:
            raise HTTPException(status_code=409, detail={"code": "MD_EVENT_CONFLICT", "message": "Idempotency key belongs to a different MD operation."})
        return {"schema": "bms.md.reorchestrate-receipt.v1", "job_id": job_id, "new_job_id": existing.payload.get("new_job_id"), "replayed": True}
    output_creation: dict[str, Any] = {}
    try:
        parent, run = await _pre_replica_terminal_parent(session, job_id)
        if run.state_version != command.expected_state_version:
            raise HTTPException(status_code=409, detail={"code": "MD_STATE_VERSION_CONFLICT", "message": "MD state changed; refresh and retry."})
        raw = sanitize_materialized_v2_contract(dict(run.normalized_request or {}))
        if raw.get("schema") != "bms.md.job.v2":
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_CONTRACT_INVALID", "message": "Only a materialized MD v2 launch can be re-orchestrated."})
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms-md-reorchestrate:{job_id}:{command.idempotency_key}"))
        from fastapi import BackgroundTasks
        from routers.jobs import create_job
        lineage_root = parent.lineage_root_job_id or parent.id
        source_stage_key = parent.stage_mode or parent.child_stage or parent.mode
        input_resolver = _trusted_reorchestration_input_resolver(parent, dict(run.normalized_request or {}))
        created = await create_job(
            JobCreate(name=parent.name, model_id="molecular_dynamics", mode="simulate", params={
                "md_job_spec": raw,
                "lineage_root_job_id": lineage_root,
                "source_stage_job_id": parent.id,
                "source_stage_family": parent.stage_family,
                "source_stage_mode": source_stage_key,
            }, pinned_gpu=parent.pinned_gpu),
            BackgroundTasks(), session, _preallocated_job_id=new_id, _commit=False,
            _md_output_creation=output_creation, _md_input_resolver=input_resolver,
        )
        new_job = await session.get(Job, created.id)
        new_run = await session.get(MdRun, created.id)
        if new_job is None or new_run is None:
            raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_FAILED", "message": "The new MD root was not durably materialized."})
        source_input = (run.normalized_request or {}).get("input") or {}
        new_input = (new_run.normalized_request or {}).get("input") or {}
        for field in ("structure", "coordinates", "topology"):
            if field not in source_input:
                continue
            if (
                new_input.get(f"{field}_sha256") != source_input.get(f"{field}_sha256")
                or new_input.get(f"{field}_bytes") != source_input.get(f"{field}_bytes")
            ):
                raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_INPUT_INVALID", "message": f"The new {field} snapshot does not match the failed launch contract."})
        provenance = dict(new_job.provenance or {})
        provenance["reorchestrated_from_job_id"] = job_id
        new_job.provenance = provenance
        result = await session.execute(update(MdRun).where(MdRun.job_id == job_id, MdRun.state_version == command.expected_state_version).values(state_version=command.expected_state_version + 1))
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "MD_STATE_VERSION_CONFLICT", "message": "MD state changed; refresh and retry."})
        session.add(MdEvent(id=str(uuid.uuid4()), md_job_id=job_id, idempotency_key=command.idempotency_key, event_type="reorchestrate", expected_state_version=command.expected_state_version, resulting_state_version=command.expected_state_version + 1, payload={"new_job_id": new_id}))
        await session.commit()
        return {"schema": "bms.md.reorchestrate-receipt.v1", "job_id": job_id, "new_job_id": new_id, "replayed": False}
    except HTTPException:
        await session.rollback()
        if output_creation.get("created") and isinstance(output_creation.get("path"), Path):
            shutil.rmtree(output_creation["path"], ignore_errors=True)
        raise
    except Exception as exc:
        await session.rollback()
        if output_creation.get("created") and isinstance(output_creation.get("path"), Path):
            shutil.rmtree(output_creation["path"], ignore_errors=True)
        raise HTTPException(status_code=409, detail={"code": "MD_REORCHESTRATE_FAILED", "message": "MD re-orchestration could not be completed."}) from exc


@router.delete("/runs/{job_id}/failed-launch")
async def delete_failed_md_launch(job_id: str, command: FailedLaunchDeleteCommand,
                                  session: AsyncSession = Depends(get_session)) -> dict:
    job, run = await _pre_replica_terminal_parent(session, job_id)
    if run.state_version != command.expected_state_version:
        raise HTTPException(status_code=409, detail={"code": "MD_STATE_VERSION_CONFLICT", "message": "MD state changed; refresh and retry."})
    root = _canonical_failed_launch_root(job)
    quarantine: Path | None = None
    if root.exists():
        quarantine = root.parent / f".bms-md-delete-{root.name}-{uuid.uuid4().hex}"
        os.replace(root, quarantine)
        if quarantine.is_symlink() or not quarantine.is_dir():
            os.replace(quarantine, root)
            raise HTTPException(status_code=409, detail={"code": "MD_OUTPUT_ROOT_CHANGED", "message": "The failed launch result root changed during deletion."})
    try:
        await session.delete(job)
        await session.commit()
    except Exception:
        await session.rollback()
        if quarantine is not None and quarantine.exists() and not root.exists():
            os.replace(quarantine, root)
        raise
    output_deleted = True
    if quarantine is not None:
        try:
            shutil.rmtree(quarantine)
        except OSError:
            # The authoritative path is already quarantined and cannot be
            # mistaken for a live run.  Report cleanup truthfully for repair.
            output_deleted = False
    return {
        "schema": "bms.md.failed-launch-delete.v1",
        "job_id": job_id,
        "deleted": True,
        "output_deleted": output_deleted,
    }


@router.post("/runs/{job_id}/replica-attempts", status_code=201)
async def register_md_replica_attempt(job_id: str, command: ReplicaAttemptCommand,
                                      session: AsyncSession = Depends(get_session)) -> dict:
    try:
        replica, segment = await create_replica_attempt(
            session, job_id=job_id, replica_index=command.replica_index, attempt=command.attempt,
            engine=command.engine, execution_plan_sha256=command.execution_plan_sha256,
            compatibility_key=command.compatibility_key,
        )
        await session.commit()
    except MdStateError as exc:
        await session.rollback(); _state_http_error(exc)
    return {"schema": "bms.md.replica-attempt-receipt.v1", "replica_run_id": replica.id,
            "segment_id": segment.id}

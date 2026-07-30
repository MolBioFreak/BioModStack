from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

from services.md.chemistry_catalog import ChemistryCatalogError, get_chemistry_catalog
from services.md.feature_gate import molecular_dynamics_feature_enabled
from services.md.launch_contract import MDLaunchError, approved_pack_inventory
from services.md.pause_actuator import pause_running_md_run
from services.md.read_model import md_queue_snapshot, md_run_snapshot
from services.md.state import (
    MdStateError, create_replica_attempt, request_cancel, resume_run,
    retry_replica_attempt,
)

router = APIRouter(prefix="/api/molecular-dynamics", tags=["molecular-dynamics"])


class LifecycleCommand(BaseModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ResumeCommand(LifecycleCommand):
    pass


class RetryCommand(LifecycleCommand):
    replica_index: int = Field(ge=0, le=63)


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
        await request_cancel(session, job_id=job_id, expected_version=command.expected_state_version,
                             idempotency_key=command.idempotency_key)
        await session.commit()
    except MdStateError as exc:
        await session.rollback(); _state_http_error(exc)
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

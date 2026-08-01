"""Persisted scheduler-owned FrustraMPNN analysis child jobs.

This is the only API/service creation path for standalone FrustraMPNN analysis.
It snapshots source bytes before a Job is queued and exposes only the two
server-owned launch parameters consumed by the canonical Nextflow entrypoint.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, FrustraMPNNResult, Job
from paths import get_results_dir

from .contracts import canonical_json_bytes, validate_schema
from .structure import (
    StructureNormalizationError,
    derive_mmcif_atom_site_authority,
    normalize_structure_bytes,
    read_structure_bytes,
)

ENVELOPE_KEY = "_frustrampnn_child_v1"
ENVELOPE_SCHEMA = "bms.frustrampnn.scheduler-child.v1"
MODEL_ID = "frustrampnn"
MODE = "analyze"
CHECKPOINT_ID = "megascale.ckpt"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_REQUESTED_OUTPUTS = [
    "structure_map", "raw_csv", "landscape", "summary", "execution_receipt",
]


class FrustraMPNNChildError(ValueError):
    """A child request failed authority validation before it could be queued."""


@dataclass(frozen=True)
class SourceSelection:
    design_id: str | None
    source_job_id: str | None
    source_path: str | None
    source_bytes: bytes
    source_sha256: str
    media_type: str
    source_format: str
    producer_stage: str
    producer_coordinates: dict[str, Any]


def _format_for_name(name: str) -> tuple[str, str, str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdb":
        return "pdb", "chemical/x-pdb", "pdb_coordinates"
    if suffix in {".cif", ".mmcif"}:
        return "mmcif", "chemical/x-mmcif", "mmcif_atom_site"
    raise FrustraMPNNChildError("FrustraMPNN source must be .pdb, .cif, or .mmcif")


def _path_within(path: str, root: str) -> bool:
    candidate = Path(os.path.abspath(path))
    authority = Path(os.path.abspath(root))
    try:
        candidate.relative_to(authority)
    except ValueError:
        return False
    return candidate != authority


def _immutable_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o400)
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    finally:
        os.close(descriptor)


def _snapshot_root(job_id: str) -> Path:
    return Path(get_results_dir()) / f"frustrampnn_{job_id}"


def _candidate_id(selection: SourceSelection, ordinal: int) -> str:
    return selection.design_id or f"upload-{ordinal + 1}"


def _new_invocation_id(job_id: str, ordinal: int) -> str:
    return f"frustrampnn:{job_id}:{ordinal + 1}"


def _attempt_job_id(owner: Job, marker_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:{MODEL_ID}:{MODE}:{owner.id}:{marker_key}"))


async def design_selections(
    session: AsyncSession,
    *,
    source_parent: Job,
    design_ids: Sequence[str],
    expected_sha256: dict[str, str] | None = None,
) -> list[SourceSelection]:
    """Resolve ordered Design authority and read each exact no-follow generation."""

    if not design_ids or len(set(design_ids)) != len(design_ids):
        raise FrustraMPNNChildError("design_ids must be a non-empty ordered set")
    expected_sha256 = expected_sha256 or {}
    result = await session.execute(select(Design).where(Design.id.in_(list(design_ids))))
    by_id = {str(item.id): item for item in result.scalars().all()}
    if set(by_id) != set(design_ids):
        raise FrustraMPNNChildError("one or more selected Designs do not exist")

    owner_ids = {str(item.job_id) for item in by_id.values()}
    owner_result = await session.execute(select(Job).where(Job.id.in_(owner_ids)))
    owners = {str(item.id): item for item in owner_result.scalars().all()}
    selections: list[SourceSelection] = []
    for ordinal, design_id in enumerate(design_ids):
        design = by_id[design_id]
        owner = owners.get(str(design.job_id))
        if owner is None:
            raise FrustraMPNNChildError("selected Design has no persisted owning Job")
        allowed = (
            str(owner.id) == str(source_parent.id)
            or str(owner.parent_job_id or "") == str(source_parent.id)
            or (
                source_parent.batch_id is not None
                and owner.batch_id is not None
                and str(owner.batch_id) == str(source_parent.batch_id)
            )
        )
        if not allowed:
            raise FrustraMPNNChildError("selected Design crosses the source-parent authority boundary")
        source_path = str(design.pdb_path or "")
        owner_root = str(owner.child_output_dir or owner.output_dir or "")
        if not source_path or not owner_root or not _path_within(source_path, owner_root):
            raise FrustraMPNNChildError("selected Design path is outside its owning Job root")
        try:
            payload = read_structure_bytes(source_path)
        except (OSError, StructureNormalizationError) as exc:
            raise FrustraMPNNChildError("selected Design source is missing or unsafe") from exc
        digest = hashlib.sha256(payload).hexdigest()
        supplied = expected_sha256.get(design_id)
        if supplied is not None and supplied != digest:
            raise FrustraMPNNChildError("selected Design source SHA-256 does not match authority")
        source_format, media_type, _ = _format_for_name(source_path)
        selections.append(SourceSelection(
            design_id=design_id,
            source_job_id=str(owner.id),
            source_path=source_path,
            source_bytes=payload,
            source_sha256=digest,
            media_type=media_type,
            source_format=source_format,
            producer_stage=str(design.source_stage or "design"),
            producer_coordinates={
                "selection_ordinal": ordinal,
                "design_name": design.name,
                "source_stage": design.source_stage,
                "source_stage_family": design.source_stage_family,
                "source_stage_mode": design.source_stage_mode,
                "artifact_class": design.artifact_class,
            },
        ))
    return selections


def upload_selection(*, filename: str, payload: bytes, expected_sha256: str | None) -> SourceSelection:
    if not payload:
        raise FrustraMPNNChildError("uploaded structure is empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise FrustraMPNNChildError("uploaded structure exceeds the 64 MiB limit")
    source_format, media_type, _ = _format_for_name(filename)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise FrustraMPNNChildError("uploaded structure SHA-256 does not match expected_sha256")
    return SourceSelection(
        design_id=None,
        source_job_id=None,
        source_path=None,
        source_bytes=bytes(payload),
        source_sha256=digest,
        media_type=media_type,
        source_format=source_format,
        producer_stage="uploaded_structure",
        producer_coordinates={"selection_ordinal": 0, "original_filename": Path(filename).name},
    )


async def create_child_job(
    session: AsyncSession,
    *,
    selections: Sequence[SourceSelection],
    source_parent: Job | None,
    trigger: str,
    supersedes: Job | None = None,
    idempotency_owner: Job | None = None,
    idempotency_marker_key: str | None = None,
    triggered_marker_key: str | None = None,
) -> Job:
    """Atomically publish immutable launch authority and persist one queued child."""

    if not selections:
        raise FrustraMPNNChildError("at least one source selection is required")
    if len(selections) > 1000:
        raise FrustraMPNNChildError("FrustraMPNN child batches are limited to 1000 inputs")
    if bool(idempotency_owner) != bool(idempotency_marker_key):
        raise FrustraMPNNChildError("idempotency owner and marker must be supplied together")

    if idempotency_owner is not None and idempotency_marker_key is not None:
        owner_params = dict(idempotency_owner.params or {})
        recorded_child_id = owner_params.get(idempotency_marker_key)
        if recorded_child_id:
            existing = await session.get(Job, str(recorded_child_id))
            if existing is None or existing.model_id != MODEL_ID or existing.mode != MODE:
                raise FrustraMPNNChildError("FrustraMPNN idempotency marker is inconsistent")
            return existing
        job_id = _attempt_job_id(idempotency_owner, idempotency_marker_key)
        if await session.get(Job, job_id) is not None:
            raise FrustraMPNNChildError("FrustraMPNN child exists without its ownership marker")
    else:
        job_id = str(uuid.uuid4())

    root = _snapshot_root(job_id)
    if root.exists():
        raise FrustraMPNNChildError("allocated child root already exists")
    root.mkdir(parents=True, mode=0o750)
    committed = False
    try:
        lineage: list[dict[str, Any]] = []
        batch_records: list[dict[str, Any]] = []
        stage_outputs: list[str] = []
        for ordinal, selection in enumerate(selections):
            candidate_id = _candidate_id(selection, ordinal)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", candidate_id) is None:
                raise FrustraMPNNChildError("FrustraMPNN candidate identity is not path-safe")
            invocation_id = _new_invocation_id(job_id, ordinal)
            original_suffix = ".cif" if selection.source_format == "mmcif" else ".pdb"
            original_relative = f"inputs/originals/{ordinal:04d}{original_suffix}"
            _immutable_write(root / original_relative, selection.source_bytes)

            source_relative = f"inputs/sources/{ordinal:04d}.pdb"
            source_path = root / source_relative
            temporary_pdb = root / "inputs" / f".{ordinal:04d}.pdb.tmp"
            temporary_map = root / "inputs" / f".{ordinal:04d}.map.tmp"
            identity_authority = (
                derive_mmcif_atom_site_authority(selection.source_bytes)
                if selection.source_format == "mmcif"
                else {
                    "kind": "pdb_self_identity_v1",
                    "identity_domain": "candidate_local",
                    "authority_artifact_sha256": selection.source_sha256,
                }
            )
            normalize_structure_bytes(
                source_bytes=selection.source_bytes,
                input_path=f"source{original_suffix}",
                output_pdb_path=temporary_pdb,
                map_path=temporary_map,
                target_id=job_id,
                parent_job_id=job_id,
                candidate_id=candidate_id,
                identity_authority=identity_authority,
                protein_selection={"mode": "all_protein_entities"},
                selected_model=1,
                altloc_policy="blank_or_explicit:<blank>",
            )
            normalized_payload = temporary_pdb.read_bytes()
            temporary_pdb.unlink()
            temporary_map.unlink()
            normalized_sha256 = hashlib.sha256(normalized_payload).hexdigest()
            _immutable_write(source_path, normalized_payload)

            request_relative = f"inputs/requests/{ordinal:04d}.json"
            request_path = root / request_relative
            request = {
                "schema_name": "workflow_component_request",
                "schema_version": 1,
                "component_id": "frustrampnn",
                "component_contract_version": "1.0",
                "invocation_id": invocation_id,
                "parent_job_id": job_id,
                "parent_workflow_id": "frustrampnn_analysis",
                "candidate_id": candidate_id,
                "source_artifact": {
                    "relative_path": source_relative,
                    "sha256": normalized_sha256,
                    "media_type": "chemical/x-pdb",
                    "producer_stage": selection.producer_stage,
                    "artifact_id": selection.design_id,
                },
                "requiredness": "required",
                "identity_authority": "pdb_coordinates",
                "protein_selection": {"mode": "all_protein_entities"},
                "parameters": {
                    "checkpoint_id": CHECKPOINT_ID,
                    "threshold_policy_id": "frustrampnn_threshold_v1",
                    "selected_model_number": 1,
                    "altloc_policy": "blank_or_explicit:<blank>",
                },
                "requested_outputs": list(_REQUESTED_OUTPUTS),
            }
            validate_schema("workflow_component_request_v1", request)
            request_payload = canonical_json_bytes(request)
            request_sha256 = hashlib.sha256(request_payload).hexdigest()
            _immutable_write(request_path, request_payload)
            batch_records.append({
                "ordinal": ordinal,
                "candidate_id": candidate_id,
                "invocation_id": invocation_id,
                "request_relative_path": request_relative,
                "request_sha256": request_sha256,
                "request_size_bytes": len(request_payload),
                "source_relative_path": source_relative,
                "source_sha256": normalized_sha256,
                "source_size_bytes": len(normalized_payload),
            })
            bundle = root / "frustrampnn" / "results" / candidate_id
            stage_outputs.extend([
                os.fspath(bundle / "frustrampnn_result_manifest_v1.json"),
                os.fspath(bundle / "workflow_component_result_v1.json"),
            ])
            lineage.append({
                "selection_ordinal": ordinal,
                "design_id": selection.design_id,
                "source_job_id": selection.source_job_id,
                "source_path": selection.source_path,
                "snapshot_relative_path": original_relative,
                "size_bytes": len(selection.source_bytes),
                "sha256": selection.source_sha256,
                "media_type": selection.media_type,
                "source_format": selection.source_format,
                "candidate_id": candidate_id,
                "invocation_id": invocation_id,
                "component_request_relative_path": request_relative,
                "component_request_sha256": request_sha256,
                "normalized_source_relative_path": source_relative,
                "normalized_source_sha256": normalized_sha256,
                "producer_coordinates": selection.producer_coordinates,
            })

        batch_manifest = {
            "schema_name": "bms_frustrampnn_scheduler_batch",
            "schema_version": 1,
            "execution_owner_job_id": job_id,
            "records": batch_records,
        }
        batch_payload = canonical_json_bytes(batch_manifest)
        batch_path = root / "inputs" / "frustrampnn_scheduler_batch_v1.json"
        _immutable_write(batch_path, batch_payload)
        source_parent_id = str(source_parent.id) if source_parent else None
        prior = (supersedes.params or {}).get(ENVELOPE_KEY, {}) if supersedes else {}
        envelope = {
            "schema_name": ENVELOPE_SCHEMA,
            "schema_version": 1,
            "execution_owner_job_id": job_id,
            "source_parent_job_id": source_parent_id,
            "source_batch_id": source_parent.batch_id if source_parent else None,
            "trigger": trigger,
            "selection": lineage,
            "component_invocation_ids": [item["invocation_id"] for item in lineage],
            "batch_manifest_relative_path": str(batch_path.relative_to(root)),
            "batch_manifest_size_bytes": len(batch_payload),
            "batch_manifest_sha256": hashlib.sha256(batch_payload).hexdigest(),
            "supersedes_child_job_id": str(supersedes.id) if supersedes else None,
            "prior_invocation_ids": list(prior.get("component_invocation_ids") or []),
            "result_persistence_identity": "(child_job_id, invocation_id)",
        }
        job = Job(
            id=job_id,
            name=f"FrustraMPNN analysis {job_id[:8]}",
            status="queued",
            queue_status="queued",
            model_id=MODEL_ID,
            mode=MODE,
            params={
                "frustrampnn_batch_manifest_path": os.fspath(batch_path.absolute()),
                ENVELOPE_KEY: envelope,
            },
            output_dir=os.fspath(root.absolute()),
            child_output_dir=os.fspath(root.absolute()),
            parent_job_id=source_parent_id,
            source_stage_job_id=source_parent_id,
            source_stage_family=(source_parent.stage_family or source_parent.model_id) if source_parent else "upload",
            source_stage_mode=(source_parent.stage_mode or source_parent.mode) if source_parent else "upload",
            selection_source_type="selected_designs" if source_parent else "uploaded_structure",
            selection_source_job_id=source_parent_id,
            source_selection_count=len(lineage),
            stage_family="frustrampnn",
            stage_mode="analyze",
            child_stage="frustrampnn",
            job_phase="inference",
            batch_id=source_parent.batch_id if source_parent else None,
            batch_name=source_parent.batch_name if source_parent else None,
            lineage_root_job_id=(source_parent.lineage_root_job_id or source_parent.id) if source_parent else job_id,
            stage_outputs={"canonical_frustrampnn": stage_outputs},
            current_stage="frustrampnn",
            max_retries=2,
            retry_count=0,
            paused=False,
            pinned_gpu=None,
            assigned_gpu=None,
            provenance={"frustrampnn_child": envelope},
        )
        session.add(job)
        await session.flush()
        if idempotency_owner is not None and idempotency_marker_key is not None:
            owner_params = dict(idempotency_owner.params or {})
            owner_params[idempotency_marker_key] = job_id
            if triggered_marker_key:
                owner_params[triggered_marker_key] = True
            idempotency_owner.params = owner_params
        await session.commit()
        committed = True
        return job
    except StructureNormalizationError as exc:
        if not committed:
            await session.rollback()
            shutil.rmtree(root, ignore_errors=True)
        raise FrustraMPNNChildError("FrustraMPNN source normalization failed") from exc
    except Exception:
        if not committed:
            await session.rollback()
            shutil.rmtree(root, ignore_errors=True)
        raise


async def create_reanalysis_child(session: AsyncSession, *, prior_child: Job) -> Job:
    envelope = (prior_child.params or {}).get(ENVELOPE_KEY)
    if prior_child.model_id != MODEL_ID or not isinstance(envelope, dict):
        raise FrustraMPNNChildError("reanalyze authority must be a persisted FrustraMPNN child Job")
    if prior_child.queue_status not in {"completed", "failed"}:
        raise FrustraMPNNChildError("reanalyze requires a terminal prior child Job")
    root = Path(os.path.abspath(str(prior_child.child_output_dir or prior_child.output_dir or "")))
    expected_root = Path(os.path.abspath(_snapshot_root(str(prior_child.id))))
    if root != expected_root or root.is_symlink() or not root.is_dir():
        raise FrustraMPNNChildError("prior child root is not the server-owned result authority")
    selections: list[SourceSelection] = []
    for item in envelope.get("selection") or []:
        relative = str(item.get("snapshot_relative_path") or "")
        path = root / relative
        if not relative or not _path_within(os.fspath(path), os.fspath(root)):
            raise FrustraMPNNChildError("prior child source snapshot authority is invalid")
        try:
            payload = read_structure_bytes(path)
        except (OSError, StructureNormalizationError) as exc:
            raise FrustraMPNNChildError("prior child source snapshot is missing or unsafe") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != item.get("sha256") or len(payload) != item.get("size_bytes"):
            raise FrustraMPNNChildError("prior child source snapshot hash/size binding is invalid")
        selections.append(SourceSelection(
            design_id=item.get("design_id"),
            source_job_id=item.get("source_job_id"),
            source_path=item.get("source_path"),
            source_bytes=payload,
            source_sha256=digest,
            media_type=str(item["media_type"]),
            source_format=str(item["source_format"]),
            producer_stage="frustrampnn_reanalysis_source",
            producer_coordinates=dict(item.get("producer_coordinates") or {}),
        ))
    source_parent_id = envelope.get("source_parent_job_id")
    source_parent = await session.get(Job, source_parent_id) if source_parent_id else None
    if source_parent_id and source_parent is None:
        raise FrustraMPNNChildError("prior child source-parent authority no longer exists")
    return await create_child_job(
        session,
        selections=selections,
        source_parent=source_parent,
        trigger="reanalyze",
        supersedes=prior_child,
    )


async def child_receipt(session: AsyncSession, *, child: Job) -> dict[str, Any]:
    if child.model_id != MODEL_ID or ENVELOPE_KEY not in (child.params or {}):
        raise FrustraMPNNChildError("Job is not a persisted FrustraMPNN child")
    results = (await session.execute(
        select(FrustraMPNNResult).where(FrustraMPNNResult.parent_job_id == str(child.id))
    )).scalars().all()
    return {
        "job_id": str(child.id),
        "child_job_id": str(child.id),
        "result_job_id": str(child.id),
        "parent_job_id": child.parent_job_id,
        "status": child.status,
        "queue_status": child.queue_status,
        "assigned_gpu": child.assigned_gpu,
        "retry_count": child.retry_count,
        "max_retries": child.max_retries,
        "error_message": child.error_message,
        "created_at": child.created_at,
        "started_at": child.started_at,
        "completed_at": child.completed_at,
        "lineage": child.params[ENVELOPE_KEY],
        "results": [
            {
                "parent_job_id": result.parent_job_id,
                "invocation_id": result.invocation_id,
                "candidate_id": result.candidate_id,
                "status": result.terminal_result_json["status"],
                "manifest_sha256": result.manifest_sha256,
            }
            for result in results
        ],
    }


__all__ = [
    "ENVELOPE_KEY", "FrustraMPNNChildError", "child_receipt", "create_child_job",
    "create_reanalysis_child", "design_selections", "upload_selection",
]

"""Transactional persistence and lifecycle authority for canonical CM products."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import secrets
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Mapping

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    ConformationalMappingStateLandscapeAnalysisHeader,
    ConformationalMappingStateLandscapeAnalysisPair,
    ConformationalMappingStateLandscapeAnalysisRow,
    FrustraMPNNResult,
    FrustraMPNNLandscapeRow,
    Job,
)
from services.frustrampnn.contracts import validate_schema as validate_frustrampnn_schema
from services.frustrampnn.settings import (
    requested_settings_sha256,
    validate_persisted_requested_settings,
)
from services.scientific_artifacts import publish_json_payload, resolve_json_value

from .contracts import (
    ContractValidationError,
    canonical_json_bytes,
    canonical_sha256,
    validate_contract_bundle,
    validate_schema,
)
from .frustrampnn_adapter import bind_cm_candidate_snapshot_bytes, project_cm_structure_map
from .state_landscape_analysis import (
    MAX_STATE_LANDSCAPE_COMPARISON_ROWS,
    StateLandscapeAnalysisError,
    validate_state_landscape_analysis_binding,
)


RESULT_CONTRACT_BY_BACKEND = {
    "protenix_v2_ensemble": "conformational_mapping_protenix_v1",
    "confornets": "conformational_mapping_confornets_v1",
    "external_import": "conformational_mapping_import_v1",
}
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
RETRYABLE_STATES = frozenset({"failed", "cancelled"})
# A state-analysis artifact cannot materialize more rows than this derivation cap.
# Rejecting larger offsets prevents direct callers from binding unrepresentable
# Python integers into SQLite while retaining every potentially reachable page.
MAX_STATE_LANDSCAPE_ANALYSIS_PAGE_OFFSET = MAX_STATE_LANDSCAPE_COMPARISON_ROWS
_RECORD_TYPES = frozenset(
    {
        "ensemble", "native_manifest", "structure_map", "landscape", "analysis",
        "state_landscape_analysis", "handoff", "resampling", "lineage", "support", "missingness",
        "frustrampnn_result_references", "failure_receipt",
    }
)


class ConformationalPersistenceError(ValueError):
    """Canonical state could not be persisted without partial visibility."""


class StateLandscapeAnalysisProjectionAbsent(ConformationalPersistenceError):
    """The request has no selected immutable state-analysis projection."""


class StateLandscapeAnalysisProjectionAmbiguous(ConformationalPersistenceError):
    """The request has more than one state-analysis projection without a selector."""


def _landscape_provenance(landscape: Mapping[str, Any]) -> dict[str, str]:
    """Preserve legacy v1 truth: retain image identity when present, never invent it."""

    provenance = {
        "raw_csv_sha256": str(landscape["raw_csv_sha256"]),
        "checkpoint_sha256": str(landscape["checkpoint_sha256"]),
        "tool_sha256": str(landscape["tool_sha256"]),
        "threshold_policy_sha256": str(landscape["threshold_policy_sha256"]),
    }
    if landscape.get("container_sha256") is not None:
        provenance["container_sha256"] = str(landscape["container_sha256"])
    return provenance


def issue_request_capability() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def capability_matches(token: str | None, expected_digest: str | None) -> bool:
    import hmac

    return bool(
        token and expected_digest and len(expected_digest) == 64
        and hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), expected_digest)
    )


async def get_request(session: AsyncSession, request_id: str) -> ConformationalMappingRequest | None:
    return (
        await session.execute(
            select(ConformationalMappingRequest).where(
                ConformationalMappingRequest.request_id == request_id
            )
        )
    ).scalar_one_or_none()


async def register_prepared_request(
    session: AsyncSession,
    *,
    job: Job,
    principal_id: str,
    request: Mapping[str, Any],
    coordinate_plan: Mapping[str, Any],
    resume_key: str,
    capability_sha256: str,
) -> ConformationalMappingRequest:
    """Register one job/request/plan identity in the caller's transaction."""

    existing = (
        await session.execute(
            select(ConformationalMappingRequest).where(
                ConformationalMappingRequest.request_sha256 == request["request_sha256"]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.principal_id != principal_id:
            raise ConformationalPersistenceError("request identity belongs to another principal")
        return existing
    result_contract_id = RESULT_CONTRACT_BY_BACKEND.get(request["backend"])
    if result_contract_id is None:
        raise ConformationalPersistenceError("unknown backend has no result contract")
    progress = {
        "phase": "prepared", "completed_coordinates": 0,
        "expected_coordinates": coordinate_plan["expected_cardinality"],
        "request_capability_sha256": capability_sha256,
    }
    record = ConformationalMappingRequest(
        request_id=request["request_id"], job_id=job.id, principal_id=principal_id,
        backend=request["backend"], status="prepared", request_sha256=request["request_sha256"],
        coordinate_plan_sha256=coordinate_plan["coordinate_plan_sha256"], resume_key=resume_key,
        result_contract_id=result_contract_id, request_json=dict(request),
        coordinate_plan_json=dict(coordinate_plan), progress_json=progress,
    )
    # The request owns a non-deferrable SQLite FK to jobs.  The two models do
    # not have an ORM relationship, so SQLAlchemy's unit of work cannot infer
    # their dependency order; flush the parent before making the request
    # insert visible to the database.
    session.add(job)
    await session.flush()
    session.add(record)
    await session.flush()
    return record


async def transition_request(
    session: AsyncSession,
    record: ConformationalMappingRequest,
    *,
    status: str,
    progress: Mapping[str, Any] | None = None,
    failure_receipt: Mapping[str, Any] | None = None,
    flush: bool = True,
) -> None:
    allowed = {
        "prepared": {"queued", "cancelled", "failed"},
        "queued": {"running", "cancelled", "failed"},
        "running": {"completed", "cancelled", "failed"},
        "failed": {"queued"}, "cancelled": {"queued"}, "completed": set(),
    }
    if status not in allowed.get(record.status, set()):
        raise ConformationalPersistenceError(f"invalid lifecycle transition {record.status}->{status}")
    record.status = status
    record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if progress is not None:
        current = dict(record.progress_json or {})
        current.update(progress)
        record.progress_json = current
    if status == "failed":
        if not failure_receipt:
            raise ConformationalPersistenceError("failed state requires an immutable failure receipt")
        record.failure_receipt_json = dict(failure_receipt)
        await _replace_record(
            session, record.request_id, "failure_receipt",
            canonical_sha256(failure_receipt), failure_receipt,
        )
    if status in TERMINAL_STATES:
        record.terminal_at = datetime.now(timezone.utc).replace(tzinfo=None)
    elif status == "queued":
        record.terminal_at = None
        # Failure receipts remain immutable audit records, but they are not the
        # current terminal authority after an admitted retry.
        record.failure_receipt_json = None
    if flush:
        await session.flush()


async def terminalize_failed_request_for_job(
    session: AsyncSession,
    *,
    job_id: str,
) -> bool:
    """Bind a published CM job failure to its still-active request atomically.

    This deliberately does not commit: callers publish the guarded Job terminal
    update and this typed request transition in one transaction.
    """

    job = await session.get(Job, job_id)
    if (
        job is None
        or job.stage_family != "conformational_mapping"
        or job.status != "failed"
    ):
        return False
    record = (
        await session.execute(
            select(ConformationalMappingRequest).where(
                ConformationalMappingRequest.job_id == job_id,
                ConformationalMappingRequest.status.in_(("prepared", "queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return False
    failure_receipt = {
        "schema_name": "cm_failure_receipt",
        "schema_version": 1,
        "request_id": record.request_id,
        "job_id": job.id,
        "terminal_state": "failed",
        "message": str(job.error_message or "canonical job failed"),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    await transition_request(
        session,
        record,
        status="failed",
        progress={"phase": "failed"},
        failure_receipt=failure_receipt,
        flush=False,
    )
    return True


async def terminalize_cancelled_request_for_job(
    session: AsyncSession,
    *,
    job_id: str,
) -> bool:
    """Bind a cancelled CM job to its still-active request."""

    job = await session.get(Job, job_id)
    if (
        job is None
        or job.stage_family != "conformational_mapping"
        or job.status != "cancelled"
    ):
        return False
    record = (
        await session.execute(
            select(ConformationalMappingRequest).where(
                ConformationalMappingRequest.job_id == job_id,
                ConformationalMappingRequest.status.in_(("prepared", "queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return False
    await transition_request(
        session,
        record,
        status="cancelled",
        progress={"phase": "cancelled"},
        flush=False,
    )
    return True


def _contained_file(root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or str(pure) != relative_path or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in relative_path:
        raise ConformationalPersistenceError("manifest contains an unsafe relative path")
    candidate = root / Path(*pure.parts)
    candidate.relative_to(root)
    current = root
    for part in pure.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise ConformationalPersistenceError("manifest artifact is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ConformationalPersistenceError("manifest artifact path contains a symlink")
    if not stat.S_ISREG(os.lstat(candidate).st_mode):
        raise ConformationalPersistenceError("manifest artifact is not a safe regular file")
    return candidate


def _open_pinned_file(path: Path) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise ConformationalPersistenceError("artifact path is not absolute")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(os.sep, directory_flags)
    file_fd: int | None = None
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        file_fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        path_before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return file_fd, parent_fd, leaf, opened, path_before
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
        raise


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _stable_file_measurement(
    path: Path, *, capture_bytes: bool = False,
) -> tuple[str, int, bytes | None]:
    try:
        file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    except OSError as exc:
        raise ConformationalPersistenceError(f"artifact is unavailable: {path}") from exc
    try:
        if not stat.S_ISREG(before.st_mode):
            raise ConformationalPersistenceError("artifact is not a regular file")
        digest = hashlib.sha256()
        size = 0
        captured = bytearray() if capture_bytes else None
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if captured is not None:
                captured.extend(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_file_identity(before, after) or not _same_file_identity(path_before, path_after) or not _same_file_identity(before, visible_after):
            raise ConformationalPersistenceError("artifact path or bytes changed during verification")
        if size != before.st_size:
            raise ConformationalPersistenceError("artifact size changed during verification")
        return digest.hexdigest(), size, bytes(captured) if captured is not None else None
    finally:
        os.close(file_fd)
        os.close(parent_fd)


async def _replace_record(
    session: AsyncSession,
    request_id: str,
    record_type: str,
    record_key: str,
    payload: Mapping[str, Any],
) -> None:
    if record_type not in _RECORD_TYPES:
        raise ConformationalPersistenceError("unknown canonical record type")
    digest = canonical_sha256(payload)
    existing = (
        await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request_id,
                ConformationalMappingRecord.record_type == record_type,
                ConformationalMappingRecord.record_key == record_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_sha256 != digest:
            raise ConformationalPersistenceError("record identity conflicts with previously ingested bytes")
        return
    artifact_reference = await publish_json_payload(
        session,
        owner_kind="conformational_mapping_record",
        owner_id=f"{request_id}:{record_type}:{record_key}",
        role="payload",
        schema_id=f"bms.cm.{record_type}.v1",
        payload=payload,
        source_sha256=digest,
    )
    session.add(
        ConformationalMappingRecord(
            id=str(uuid.uuid4()), request_id=request_id, record_type=record_type,
            record_key=record_key, content_sha256=digest, payload_json=artifact_reference,
        )
    )


async def persist_derived_record(
    session: AsyncSession,
    request_id: str,
    *,
    record_type: str,
    record_key: str,
    payload: Mapping[str, Any],
) -> None:
    """Persist one validated server-produced derived authority idempotently."""

    if record_type == "state_landscape_analysis":
        raise ConformationalPersistenceError(
            "state landscape analysis must be persisted through canonical result-bundle ingestion"
        )
    schema_by_type = {
        "structure_map": "cm_structure_map_v1",
        "landscape": "cm_frustration_landscape_v1",
        "analysis": "cm_analysis_v1",
        "handoff": "cm_mutagenesis_handoff_v1",
    }
    schema = schema_by_type.get(record_type)
    if schema:
        try:
            validate_schema(schema, payload)
        except (ContractValidationError, KeyError, TypeError) as exc:
            raise ConformationalPersistenceError(str(exc)) from exc
    await _replace_record(session, request_id, record_type, record_key, payload)


async def persist_landscape_matrix(
    session: AsyncSession,
    request_id: str,
    landscape: Mapping[str, Any],
) -> None:
    """Persist exactly one candidate matrix, with hash-idempotent retry."""

    validate_schema("cm_frustration_landscape_v1", landscape)
    candidate_id = str(landscape["candidate_id"])
    existing = (
        await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request_id,
                ConformationalMappingRecord.record_type == "landscape",
                ConformationalMappingRecord.record_key == candidate_id,
            )
        )
    ).scalar_one_or_none()
    digest = canonical_sha256(landscape)
    if existing is not None and existing.content_sha256 != digest:
        raise ConformationalPersistenceError("candidate landscape conflicts with persisted matrix")
    if existing is None:
        await _replace_record(session, request_id, "landscape", candidate_id, landscape)
    existing_slot = (
        await session.execute(
            select(ConformationalMappingLandscapeRow.id).where(
                ConformationalMappingLandscapeRow.request_id == request_id,
                ConformationalMappingLandscapeRow.candidate_id == candidate_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_slot is not None:
        return
    for residue in landscape["residues"]:
        for slot in residue["slots"]:
            session.add(ConformationalMappingLandscapeRow(
                id=str(uuid.uuid4()), request_id=request_id, candidate_id=candidate_id,
                entity_instance_id=residue["entity_instance_id"], auth_asym_id=residue["auth_asym_id"],
                auth_seq_id=str(residue["auth_seq_id"]), insertion_code=residue["insertion_code"],
                sequence_index=residue["sequence_index"], wt=residue["wt"],
                mutation_aa=slot["mutation_aa"], score=slot["score"], score_class=slot["class"],
                scoreable=slot["scoreable"], status=slot["status"], reason=slot["reason"],
                provenance_json=_landscape_provenance(landscape),
            ))


async def _preflight_state_landscape_analysis_projection(
    session: AsyncSession,
    request_id: str,
    analysis: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Reject projection identity conflicts before any canonical write is visible."""

    if analysis is None:
        return None
    analysis_id = str(analysis["analysis_id"])
    content_sha256 = canonical_sha256(analysis)
    existing = (
        await session.execute(
            select(ConformationalMappingStateLandscapeAnalysisHeader).where(
                ConformationalMappingStateLandscapeAnalysisHeader.request_id == request_id,
                ConformationalMappingStateLandscapeAnalysisHeader.analysis_id == analysis_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_sha256 != content_sha256:
            raise ConformationalPersistenceError(
                "state landscape analysis projection identity conflicts with persisted payload"
            )
        return None
    return analysis


def _state_analysis_availability(metrics: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Copy artifact availability into a compact query field; never recompute metrics."""

    return {
        str(name): {"status": metric["status"], "reason": metric["reason"]}
        for name, metric in metrics.items()
    }


async def _persist_preflighted_state_landscape_analysis_projection(
    session: AsyncSession,
    request_id: str,
    analysis: Mapping[str, Any] | None,
) -> None:
    """Write one already-bound projection before canonical record visibility."""

    if analysis is None:
        return
    analysis_id = str(analysis["analysis_id"])
    session.add(
        ConformationalMappingStateLandscapeAnalysisHeader(
            request_id=request_id,
            analysis_id=analysis_id,
            content_sha256=canonical_sha256(analysis),
            source_ensemble_sha256=str(analysis["source_ensemble_sha256"]),
            source_landscape_sha256=str(analysis["source_landscape_sha256"]),
            source_structure_map_sha256=str(analysis["source_structure_map_sha256"]),
            comparison_sha256=str(analysis["comparison_sha256"]),
            formula_version=str(analysis["formula_version"]),
            formula_sha256=str(analysis["formula_sha256"]),
            policy_sha256=str(analysis["policy_sha256"]),
            comparison_mode=str(analysis["comparison_mode"]),
            comparison_target_id=str(analysis["comparison_target_id"]),
            comparison_scope=str(analysis["comparison_scope"]),
            reference_backend_coordinates_json=(
                dict(analysis["reference_backend_coordinates"])
                if analysis["reference_backend_coordinates"] is not None else None
            ),
            reference_candidate_id=analysis["reference_candidate_id"],
            pair_count=len(analysis["resolved_pairs"]),
            row_count=len(analysis["rows"]),
            exclusion_count=len(analysis["exclusion_ledger"]),
        )
    )
    for pair in analysis["resolved_pairs"]:
        session.add(
            ConformationalMappingStateLandscapeAnalysisPair(
                request_id=request_id,
                analysis_id=analysis_id,
                pair_id=str(pair["pair_id"]),
                candidate_a_id=str(pair["candidate_a_id"]),
                candidate_b_id=str(pair["candidate_b_id"]),
            )
        )
    for row in analysis["rows"]:
        identity = row["identity"]
        metrics = row["metrics"]
        session.add(
            ConformationalMappingStateLandscapeAnalysisRow(
                id="cm_state_analysis_row_" + canonical_sha256(
                    {"request_id": request_id, "analysis_id": analysis_id, "row": row}
                )[:64],
                request_id=request_id,
                analysis_id=analysis_id,
                pair_id=str(row["pair_id"]),
                candidate_a_id=str(row["candidate_a_id"]),
                candidate_b_id=str(row["candidate_b_id"]),
                target_id=str(identity["target_id"]),
                entity_instance_id=str(identity["entity_instance_id"]),
                auth_asym_id=str(identity["auth_asym_id"]),
                auth_seq_id=int(identity["auth_seq_id"]),
                insertion_code=str(identity["insertion_code"]),
                sequence_index=int(identity["sequence_index"]),
                validated_wt=str(identity["validated_wt"]),
                metrics_json=dict(metrics),
                availability_json=_state_analysis_availability(metrics),
            )
        )
    # Force projection writes before canonical records and terminal visibility.
    await session.flush()


async def ingest_result_bundle(
    session: AsyncSession,
    record: ConformationalMappingRequest,
    *,
    bundle: Mapping[str, Any],
    result_root: Path | str,
) -> None:
    """Validate every manifest/hash/cardinality relation before DB visibility."""

    try:
        core_bundle = {
            key: value for key, value in bundle.items()
            if key in {"cm_request_v1", "cm_complex_snapshot_v1", "cm_native_artifacts_v1",
                       "cm_ensemble_v1", "cm_structure_map_v1",
                       "cm_frustration_landscape_v1", "cm_analysis_v1",
                       "cm_mutagenesis_handoff_v1", "cm_runtime_image_receipt_v1",
                       "cm_protenix_execution_snapshot_v1", "cm_protenix_runtime_attestation_v1"}
        }
        allowed_extensions = {
            "cm_structure_maps", "cm_frustration_landscapes", "cm_mutagenesis_handoffs",
            "cm_resampling_v1", "cm_lineage", "cm_support", "cm_missingness",
            "cm_state_landscape_analyses", "cm_derived_files",
            "cm_frustrampnn_result_references", "frustrampnn_structure_maps",
            "frustrampnn_landscapes", "cm_complex_snapshots",
        }
        unknown = set(bundle) - set(core_bundle) - allowed_extensions
        if unknown:
            raise ConformationalPersistenceError(
                f"unknown canonical result bundle members: {sorted(unknown)}"
            )
        requested_state_analysis = "state_landscape_comparison" in record.request_json
        state_analysis_value = bundle.get("cm_state_landscape_analyses")
        if requested_state_analysis and (
            state_analysis_value is None
            or (isinstance(state_analysis_value, list) and not state_analysis_value)
        ):
            raise ConformationalPersistenceError("requested state landscape analysis is missing")
        validate_contract_bundle(core_bundle)
    except (ContractValidationError, KeyError, TypeError) as exc:
        raise ConformationalPersistenceError(str(exc)) from exc
    ensemble = bundle["cm_ensemble_v1"]
    native = bundle["cm_native_artifacts_v1"]
    if ensemble["request_id"] != record.request_id or ensemble["request_sha256"] != record.request_sha256:
        raise ConformationalPersistenceError("result bundle request identity mismatch")
    if ensemble["backend"] != record.backend:
        raise ConformationalPersistenceError("result bundle backend identity mismatch")
    if native["request_id"] != record.request_id or native["backend"] != record.backend:
        raise ConformationalPersistenceError("native manifest request/backend identity mismatch")
    if ensemble["terminal_status"] != "complete":
        raise ConformationalPersistenceError("only complete canonical ensembles may be ingested")
    plan = record.coordinate_plan_json
    if (
        ensemble["expected_cardinality"] != plan["expected_cardinality"]
        or ensemble["expected_coordinates"] != plan["coordinates"]
        or len(ensemble["candidates"]) != plan["expected_cardinality"]
    ):
        raise ConformationalPersistenceError("result coordinates do not equal stored request authority")
    if ensemble["native_manifest_path"] != "cm_native_artifacts_v1.json":
        raise ConformationalPersistenceError("native manifest path is not canonical")
    if record.backend == "protenix_v2_ensemble":
        runtime_attestations = [
            item for item in native["files"]
            if item.get("candidate_id") is None and item.get("semantic_role") == "runtime_attestation"
        ]
        if len(runtime_attestations) != 1 or ensemble.get("runtime_attestation_sha256") != runtime_attestations[0]["sha256"]:
            raise ConformationalPersistenceError("Protenix runtime attestation is not bound to the ensemble")
    descriptor = ensemble.get("resume_descriptor")
    if ensemble.get("resumable") is True and ensemble.get("resume_key") == "0" * 64:
        raise ConformationalPersistenceError("resumable canonical result has no immutable resume identity")
    if ensemble.get("resumable") is True and not isinstance(descriptor, Mapping):
        raise ConformationalPersistenceError("resumable canonical result has no validated descriptor")
    if ensemble.get("resumable") is False and descriptor is not None:
        raise ConformationalPersistenceError("nonresumable canonical result has an invalid descriptor")
    resume_key_to_persist: str | None = None
    if record.resume_key == "0" * 64 and ensemble.get("resumable") is True:
        resume_key_to_persist = ensemble["resume_key"]
    elif ensemble["resume_key"] != record.resume_key:
        raise ConformationalPersistenceError("result bundle resume identity mismatch")
    global_references = bundle.get("cm_frustrampnn_result_references")
    canonical_global_mode = global_references is not None
    expected_settings_sha256: str | None = None
    expected_snapshot_by_candidate: dict[str, str] = {}
    candidate_snapshot_bindings_by_path: dict[
        str, list[tuple[str, Mapping[str, Any]]]
    ] = {}
    snapshot_by_target: dict[str, Mapping[str, Any]] = {}
    if canonical_global_mode:
        snapshots = bundle.get("cm_complex_snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            raise ConformationalPersistenceError("canonical CM snapshot authority is missing")
        try:
            for snapshot in snapshots:
                if not isinstance(snapshot, Mapping):
                    raise TypeError("CM snapshot is not an object")
                validate_schema("cm_complex_snapshot_v1", snapshot)
                target_id = str(snapshot["target_id"])
                if target_id in snapshot_by_target:
                    raise ValueError("duplicate CM snapshot target")
                snapshot_by_target[target_id] = snapshot
            expected_settings_sha256 = requested_settings_sha256(
                validate_persisted_requested_settings(
                    record.request_json.get("frustrampnn_settings")
                )
            )
        except Exception as exc:
            raise ConformationalPersistenceError(
                "persisted CM FrustraMPNN settings or snapshot authority is invalid"
            ) from exc
        for candidate in ensemble["candidates"]:
            coordinates = candidate.get("backend_coordinates")
            target_id = (
                str(coordinates.get("target_id") or "")
                if isinstance(coordinates, Mapping) else ""
            )
            snapshot = snapshot_by_target.get(target_id)
            if snapshot is None:
                raise ConformationalPersistenceError(
                    "CM candidate has no persisted snapshot authority"
                )
            relative_source = str(candidate.get("authoritative_structure_path") or "")
            candidate_snapshot_bindings_by_path.setdefault(relative_source, []).append(
                (str(candidate["candidate_id"]), snapshot)
            )
    root_input = Path(result_root)
    try:
        root_info = os.lstat(root_input)
    except OSError as exc:
        raise ConformationalPersistenceError("result root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ConformationalPersistenceError("result root is not a real directory")
    root = root_input.resolve(strict=True)
    verified_artifacts: list[tuple[Mapping[str, Any], Path]] = []
    seen_paths: set[str] = set()
    for item in native["files"]:
        relative_path = item["relative_path"]
        if relative_path in seen_paths:
            raise ConformationalPersistenceError("native manifest contains a duplicate path")
        seen_paths.add(relative_path)
        path = _contained_file(root, relative_path)
        digest, byte_count, captured_bytes = _stable_file_measurement(
            path, capture_bytes=relative_path in candidate_snapshot_bindings_by_path
        )
        if digest != item["sha256"] or byte_count != item["bytes"]:
            raise ConformationalPersistenceError("native artifact hash or size mismatch")
        if relative_path in candidate_snapshot_bindings_by_path:
            if captured_bytes is None:
                raise ConformationalPersistenceError(
                    "authoritative candidate bytes were not captured during verification"
                )
            for candidate_id, snapshot in candidate_snapshot_bindings_by_path[relative_path]:
                try:
                    bound_snapshot = bind_cm_candidate_snapshot_bytes(
                        snapshot,
                        candidate_id=candidate_id,
                        source_bytes=captured_bytes,
                        source_suffix=Path(relative_path).suffix,
                        source_relative_path=relative_path,
                    )
                except Exception as exc:
                    raise ConformationalPersistenceError(
                        "CM candidate snapshot authority cannot be reconstructed"
                    ) from exc
                expected_snapshot_by_candidate[candidate_id] = canonical_sha256(
                    bound_snapshot
                )
        verified_artifacts.append((item, path))
    for item in bundle.get("cm_derived_files", []):
        if not isinstance(item, Mapping):
            raise ConformationalPersistenceError("derived artifact index contains a non-object")
        relative_path = str(item.get("relative_path") or "")
        if relative_path in seen_paths:
            raise ConformationalPersistenceError("derived artifact duplicates a native path")
        seen_paths.add(relative_path)
        path = _contained_file(root, relative_path)
        digest, byte_count, _captured_bytes = _stable_file_measurement(path)
        if digest != item.get("sha256") or byte_count != item.get("bytes"):
            raise ConformationalPersistenceError("derived artifact hash or size mismatch")
        verified_artifacts.append((item, path))

    optional_records = {
        "cm_structure_maps": "structure_map", "cm_frustration_landscapes": "landscape",
        "cm_analysis_v1": "analysis", "cm_state_landscape_analyses": "state_landscape_analysis",
        "cm_mutagenesis_handoffs": "handoff", "cm_resampling_v1": "resampling", "cm_lineage": "lineage",
        "cm_support": "support", "cm_missingness": "missingness",
        "cm_frustrampnn_result_references": "frustrampnn_result_references",
    }
    optional_schema = {
        "structure_map": "cm_structure_map_v1",
        "landscape": "cm_frustration_landscape_v1",
        "analysis": "cm_analysis_v1",
        "state_landscape_analysis": "cm_state_landscape_analysis_v1",
        "handoff": "cm_mutagenesis_handoff_v1",
    }
    ensemble_candidate_ids = {item["candidate_id"] for item in ensemble["candidates"]}
    if canonical_global_mode:
        if bundle.get("cm_structure_maps") is not None or bundle.get("cm_frustration_landscapes") is not None:
            raise ConformationalPersistenceError(
                "canonical global FrustraMPNN results cannot coexist with legacy CM landscapes"
            )
        structure_maps = bundle.get("frustrampnn_structure_maps") or []
        landscapes = bundle.get("frustrampnn_landscapes") or []
        try:
            for value in structure_maps:
                validate_frustrampnn_schema("frustrampnn_structure_map_v1", value)
            for value in landscapes:
                validate_frustrampnn_schema("frustrampnn_landscape_v2", value)
            cm_structure_maps_for_persistence = [
                project_cm_structure_map(value, snapshot_by_target[str(value["target_id"])])
                for value in structure_maps
            ]
        except Exception as exc:
            raise ConformationalPersistenceError(
                "canonical global FrustraMPNN result payload is invalid"
            ) from exc
    else:
        if bundle.get("frustrampnn_structure_maps") is not None or bundle.get("frustrampnn_landscapes") is not None:
            raise ConformationalPersistenceError(
                "global FrustraMPNN payloads require canonical result references"
            )
        structure_maps = bundle.get("cm_structure_maps") or []
        landscapes = bundle.get("cm_frustration_landscapes") or []
        cm_structure_maps_for_persistence = structure_maps
    structure_map_ids = {
        str(value.get("candidate_id")) for value in structure_maps
        if isinstance(value, Mapping)
    }
    landscape_ids = {
        str(value.get("candidate_id")) for value in landscapes
        if isinstance(value, Mapping)
    }
    if structure_map_ids != ensemble_candidate_ids or landscape_ids != ensemble_candidate_ids:
        raise ConformationalPersistenceError(
            "derived structure-map and landscape candidate sets must exactly equal the ensemble"
        )
    if canonical_global_mode:
        if not isinstance(global_references, Mapping) or set(global_references) != {
            "schema_name", "schema_version", "parent_job_id", "parent_workflow_id",
            "expected_cardinality", "results",
        }:
            raise ConformationalPersistenceError("canonical FrustraMPNN result references are malformed")
        reference_rows = global_references.get("results")
        if (
            global_references.get("schema_name") != "cm_frustrampnn_result_references"
            or global_references.get("schema_version") != 1
            or global_references.get("parent_job_id") != record.job_id
            or global_references.get("parent_workflow_id") != "conformational_mapping"
            or global_references.get("expected_cardinality") != len(ensemble_candidate_ids)
            or not isinstance(reference_rows, list)
            or len(reference_rows) != len(ensemble_candidate_ids)
        ):
            raise ConformationalPersistenceError("canonical FrustraMPNN result references are unbound")
        expected_reference_keys = {
            "candidate_id", "invocation_id", "source_sha256", "cm_complex_snapshot_sha256",
            "requested_settings_sha256", "effective_settings_sha256", "bundle_relative_path",
            "result_manifest_sha256", "landscape_sha256", "structure_map_sha256",
        }
        references_by_candidate: dict[str, Mapping[str, Any]] = {}
        for reference in reference_rows:
            if not isinstance(reference, Mapping) or set(reference) != expected_reference_keys:
                raise ConformationalPersistenceError("canonical FrustraMPNN result reference is malformed")
            candidate_id = str(reference["candidate_id"])
            if candidate_id in references_by_candidate:
                raise ConformationalPersistenceError("canonical FrustraMPNN result reference is duplicated")
            if (
                reference["requested_settings_sha256"] != expected_settings_sha256
                or reference["cm_complex_snapshot_sha256"]
                != expected_snapshot_by_candidate.get(candidate_id)
            ):
                raise ConformationalPersistenceError(
                    "canonical FrustraMPNN result reference crosses CM settings or snapshot authority"
                )
            references_by_candidate[candidate_id] = reference
        if set(references_by_candidate) != ensemble_candidate_ids:
            raise ConformationalPersistenceError(
                "canonical FrustraMPNN result references do not cover the ensemble"
            )
        persisted_results = list((await session.execute(
            select(FrustraMPNNResult).where(
                FrustraMPNNResult.parent_job_id == record.job_id
            )
        )).scalars().all())
        persisted_by_candidate = {value.candidate_id: value for value in persisted_results}
        if (
            len(persisted_results) != len(ensemble_candidate_ids)
            or len(persisted_by_candidate) != len(persisted_results)
            or set(persisted_by_candidate) != ensemble_candidate_ids
        ):
            raise ConformationalPersistenceError(
                "required canonical FrustraMPNN results are not persisted"
            )
        for candidate_id, reference in references_by_candidate.items():
            persisted = persisted_by_candidate[candidate_id]
            if (
                persisted.parent_workflow_id != "conformational_mapping"
                or persisted.requiredness != "required"
                or persisted.invocation_id != reference["invocation_id"]
                or persisted.source_artifact_sha256 != reference["source_sha256"]
                or persisted.settings_sha256 != reference["requested_settings_sha256"]
                or persisted.effective_settings_sha256 != reference["effective_settings_sha256"]
                or persisted.manifest_sha256 != reference["result_manifest_sha256"]
            ):
                raise ConformationalPersistenceError(
                    "persisted canonical FrustraMPNN result does not match CM reference"
                )
    if not isinstance(bundle.get("cm_analysis_v1"), Mapping):
        raise ConformationalPersistenceError("canonical analysis authority is missing")
    state_analysis_value = bundle.get("cm_state_landscape_analyses")
    proposed_state_analysis: Mapping[str, Any] | None = None
    state_analysis_requested = "state_landscape_comparison" in record.request_json
    if not state_analysis_requested:
        if state_analysis_value is not None and (
            not isinstance(state_analysis_value, list) or state_analysis_value
        ):
            raise ConformationalPersistenceError(
                "state landscape analysis is not authorized without comparison authority"
            )
    elif state_analysis_value is None or (
        isinstance(state_analysis_value, list) and not state_analysis_value
    ):
        raise ConformationalPersistenceError("requested state landscape analysis is missing")
    else:
        state_analyses = state_analysis_value if isinstance(state_analysis_value, list) else [state_analysis_value]
        if len(state_analyses) != 1:
            raise ConformationalPersistenceError("state landscape analysis must materialize exactly once")
        try:
            proposed_state_analysis = state_analyses[0]
            if not isinstance(proposed_state_analysis, Mapping):
                raise TypeError("state landscape analysis is not an object")
            validate_schema("cm_state_landscape_analysis_v1", proposed_state_analysis)
            validate_state_landscape_analysis_binding(
                record.request_json,
                ensemble,
                landscapes,
                structure_maps,
                proposed_state_analysis,
            )
        except (ContractValidationError, StateLandscapeAnalysisError, KeyError, TypeError) as exc:
            raise ConformationalPersistenceError("state landscape analysis binding validation failed") from exc
    # The inline index payload is useful for one-pass validation, but it is not
    # the immutable export. A requested state analysis must bind exactly to the
    # canonical bytes of its registered derived artifact before any record or
    # projection write is permitted.
    if proposed_state_analysis is not None:
        expected_bytes = canonical_json_bytes(proposed_state_analysis)
        expected_path = "derived/cm_state_landscape_analysis_v1.json"
        state_artifacts = [
            item for item, _path in verified_artifacts
            if item.get("semantic_role") == "state_landscape_analysis"
        ]
        if len(state_artifacts) != 1:
            raise ConformationalPersistenceError(
                "state landscape analysis requires exactly one registered immutable artifact"
            )
        state_artifact = state_artifacts[0]
        if (
            state_artifact.get("candidate_id") is not None
            or state_artifact.get("relative_path") != expected_path
            or state_artifact.get("sha256") != canonical_sha256(proposed_state_analysis)
            or state_artifact.get("bytes") != len(expected_bytes)
        ):
            raise ConformationalPersistenceError(
                "state landscape analysis artifact does not bind canonical analysis bytes"
            )

    validated_optional_records: list[tuple[str, str, Mapping[str, Any]]] = []
    for bundle_key, record_type in optional_records.items():
        value = (
            cm_structure_maps_for_persistence
            if bundle_key == "cm_structure_maps"
            else bundle.get(bundle_key)
        )
        if value is None:
            continue
        records = value if isinstance(value, list) else [value]
        for index, payload in enumerate(records):
            if not isinstance(payload, Mapping):
                raise ConformationalPersistenceError(f"{bundle_key} contains a non-object record")
            schema = optional_schema.get(record_type)
            if schema is not None:
                validate_schema(schema, payload)
            if record_type in {"structure_map", "landscape"} and payload.get("candidate_id") not in ensemble_candidate_ids:
                raise ConformationalPersistenceError(
                    f"{record_type} candidate is not authorized by the ensemble"
                )
            validated_optional_records.append((
                record_type,
                str(payload.get("candidate_id") or payload.get("analysis_id") or index),
                payload,
            ))

    record_writes = [
        ("ensemble", "primary", ensemble),
        ("native_manifest", "primary", native),
        *validated_optional_records,
    ]
    for record_type, record_key, payload in record_writes:
        existing = (
            await session.execute(
                select(ConformationalMappingRecord).where(
                    ConformationalMappingRecord.request_id == record.request_id,
                    ConformationalMappingRecord.record_type == record_type,
                    ConformationalMappingRecord.record_key == record_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.content_sha256 != canonical_sha256(payload):
            raise ConformationalPersistenceError("record identity conflicts with previously ingested bytes")

    artifacts_to_create: list[tuple[str, Mapping[str, Any], Path]] = []
    for item, path in verified_artifacts:
        binding = canonical_sha256(
            {"request_id": record.request_id, "relative_path": item["relative_path"]}
        )[:16]
        artifact_id = f"cm_art_{item['sha256']}_{binding}"
        existing = await session.get(ConformationalMappingArtifact, artifact_id)
        if existing is not None:
            if existing.storage_path != str(path):
                raise ConformationalPersistenceError("content artifact identity conflicts with storage")
            continue
        artifacts_to_create.append((artifact_id, item, path))

    landscapes_to_insert = bundle.get("cm_frustration_landscapes") or []
    existing_landscapes = (
        await session.execute(
            select(ConformationalMappingLandscapeRow).where(
                ConformationalMappingLandscapeRow.request_id == record.request_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_landscapes is not None and landscapes_to_insert:
        stored_records = (
            await session.execute(
                select(ConformationalMappingRecord).where(
                    ConformationalMappingRecord.request_id == record.request_id,
                    ConformationalMappingRecord.record_type == "landscape",
                )
            )
        ).scalars().all()
        stored = {row.record_key: row.content_sha256 for row in stored_records}
        incoming = {
            str(value.get("candidate_id") or index): canonical_sha256(value)
            for index, value in enumerate(landscapes_to_insert)
        }
        if incoming != stored:
            raise ConformationalPersistenceError("landscape retry conflicts with persisted matrix")
        landscapes_to_insert = []

    state_analysis_projection = await _preflight_state_landscape_analysis_projection(
        session, record.request_id, proposed_state_analysis,
    )
    await _persist_preflighted_state_landscape_analysis_projection(
        session, record.request_id, state_analysis_projection,
    )
    if resume_key_to_persist is not None:
        record.resume_key = resume_key_to_persist
    await _replace_record(session, record.request_id, "ensemble", "primary", ensemble)
    await _replace_record(session, record.request_id, "native_manifest", "primary", native)
    for record_type, record_key, payload in validated_optional_records:
        await _replace_record(session, record.request_id, record_type, record_key, payload)

    for artifact_id, item, path in artifacts_to_create:
        session.add(
            ConformationalMappingArtifact(
                artifact_id=artifact_id, request_id=record.request_id,
                candidate_id=item.get("candidate_id"), role=item["semantic_role"],
                relative_path=item["relative_path"], storage_path=str(path),
                content_sha256=item["sha256"], size_bytes=item["bytes"],
                media_type=item.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                metadata_json={
                    "backend_coordinates": item.get("backend_coordinates"),
                    "provenance_sha256": item.get("provenance_sha256"),
                },
            )
        )

    for landscape in landscapes_to_insert:
        for residue in landscape["residues"]:
            for slot in residue["slots"]:
                session.add(
                    ConformationalMappingLandscapeRow(
                        id=str(uuid.uuid4()), request_id=record.request_id,
                        candidate_id=landscape["candidate_id"],
                        entity_instance_id=residue["entity_instance_id"],
                        auth_asym_id=residue["auth_asym_id"], auth_seq_id=str(residue["auth_seq_id"]),
                        insertion_code=residue["insertion_code"], sequence_index=residue["sequence_index"],
                        wt=residue["wt"], mutation_aa=slot["mutation_aa"], score=slot["score"],
                        score_class=slot["class"], scoreable=slot["scoreable"],
                        status=slot["status"], reason=slot["reason"],
                        provenance_json=_landscape_provenance(landscape),
                    )
                )
    record.status = "completed"
    record.terminal_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record.updated_at = record.terminal_at
    record.progress_json = {
        **dict(record.progress_json or {}), "phase": "completed",
        "completed_coordinates": ensemble["expected_cardinality"],
    }
    await session.flush()


async def resolve_state_landscape_analysis_projection(
    session: AsyncSession,
    request_id: str,
    *,
    analysis_id: str | None = None,
) -> ConformationalMappingStateLandscapeAnalysisHeader:
    """Resolve exactly one request-local state-analysis projection without reading canonical JSON."""

    statement = select(ConformationalMappingStateLandscapeAnalysisHeader).where(
        ConformationalMappingStateLandscapeAnalysisHeader.request_id == request_id
    )
    if analysis_id is not None:
        statement = statement.where(
            ConformationalMappingStateLandscapeAnalysisHeader.analysis_id == analysis_id
        )
    headers = list((await session.execute(
        statement.order_by(ConformationalMappingStateLandscapeAnalysisHeader.analysis_id)
    )).scalars().all())
    if not headers:
        raise StateLandscapeAnalysisProjectionAbsent("state landscape analysis is absent for request")
    if len(headers) != 1:
        raise StateLandscapeAnalysisProjectionAmbiguous(
            "state landscape analysis selection is ambiguous; analysis_id is required"
        )
    return headers[0]


async def state_landscape_analysis_pair_summaries(
    session: AsyncSession,
    header: ConformationalMappingStateLandscapeAnalysisHeader,
) -> list[ConformationalMappingStateLandscapeAnalysisPair]:
    """Return compact canonical-order pair identities from the normalized projection."""

    statement = select(ConformationalMappingStateLandscapeAnalysisPair).where(
        ConformationalMappingStateLandscapeAnalysisPair.request_id == header.request_id,
        ConformationalMappingStateLandscapeAnalysisPair.analysis_id == header.analysis_id,
    ).order_by(ConformationalMappingStateLandscapeAnalysisPair.pair_id)
    return list((await session.execute(statement)).scalars().all())


async def state_landscape_analysis_artifact(
    session: AsyncSession,
    header: ConformationalMappingStateLandscapeAnalysisHeader,
) -> ConformationalMappingArtifact | None:
    """Return the single request-local immutable state-analysis export."""

    artifacts = list((await session.execute(
        select(ConformationalMappingArtifact).where(
            ConformationalMappingArtifact.request_id == header.request_id,
            ConformationalMappingArtifact.role == "state_landscape_analysis",
            ConformationalMappingArtifact.content_sha256 == header.content_sha256,
        ).order_by(ConformationalMappingArtifact.artifact_id)
    )).scalars().all())
    return artifacts[0] if len(artifacts) == 1 else None


async def paged_state_landscape_analysis_rows(
    session: AsyncSession,
    request_id: str,
    *,
    analysis_id: str | None = None,
    pair_id: str | None = None,
    candidate_id: str | None = None,
    entity_instance_id: str | None = None,
    auth_asym_id: str | None = None,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    offset: int = 0,
    limit: int = 200,
) -> tuple[
    ConformationalMappingStateLandscapeAnalysisHeader,
    list[ConformationalMappingStateLandscapeAnalysisRow],
    bool,
]:
    """Page stored state-analysis rows with a same-query lookahead for terminal pagination."""

    if (
        offset < 0
        or offset > MAX_STATE_LANDSCAPE_ANALYSIS_PAGE_OFFSET
        or limit < 1
        or limit > 1000
    ):
        raise ConformationalPersistenceError("invalid state landscape analysis page")
    if sequence_start is not None and sequence_start < 1:
        raise ConformationalPersistenceError("invalid state landscape analysis sequence range")
    if sequence_end is not None and (
        sequence_end < 1 or (sequence_start is not None and sequence_end < sequence_start)
    ):
        raise ConformationalPersistenceError("invalid state landscape analysis sequence range")
    header = await resolve_state_landscape_analysis_projection(
        session, request_id, analysis_id=analysis_id,
    )
    statement = select(ConformationalMappingStateLandscapeAnalysisRow).where(
        ConformationalMappingStateLandscapeAnalysisRow.request_id == request_id,
        ConformationalMappingStateLandscapeAnalysisRow.analysis_id == header.analysis_id,
    )
    if pair_id is not None:
        statement = statement.where(ConformationalMappingStateLandscapeAnalysisRow.pair_id == pair_id)
    if candidate_id is not None:
        statement = statement.where(or_(
            ConformationalMappingStateLandscapeAnalysisRow.candidate_a_id == candidate_id,
            ConformationalMappingStateLandscapeAnalysisRow.candidate_b_id == candidate_id,
        ))
    if entity_instance_id is not None:
        statement = statement.where(
            ConformationalMappingStateLandscapeAnalysisRow.entity_instance_id == entity_instance_id
        )
    if auth_asym_id is not None:
        statement = statement.where(ConformationalMappingStateLandscapeAnalysisRow.auth_asym_id == auth_asym_id)
    if sequence_start is not None:
        statement = statement.where(ConformationalMappingStateLandscapeAnalysisRow.sequence_index >= sequence_start)
    if sequence_end is not None:
        statement = statement.where(ConformationalMappingStateLandscapeAnalysisRow.sequence_index <= sequence_end)
    statement = statement.order_by(
        ConformationalMappingStateLandscapeAnalysisRow.pair_id,
        ConformationalMappingStateLandscapeAnalysisRow.target_id,
        ConformationalMappingStateLandscapeAnalysisRow.entity_instance_id,
        ConformationalMappingStateLandscapeAnalysisRow.auth_asym_id,
        ConformationalMappingStateLandscapeAnalysisRow.auth_seq_id,
        ConformationalMappingStateLandscapeAnalysisRow.insertion_code,
        ConformationalMappingStateLandscapeAnalysisRow.sequence_index,
        ConformationalMappingStateLandscapeAnalysisRow.validated_wt,
        ConformationalMappingStateLandscapeAnalysisRow.id,
    ).offset(offset).limit(limit + 1)
    rows = list((await session.execute(statement)).scalars().all())
    has_more = len(rows) > limit
    return header, rows[:limit], has_more


async def paged_landscape(
    session: AsyncSession,
    request_id: str,
    *,
    candidate_id: str | None = None,
    entity_instance_id: str | None = None,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    offset: int = 0,
    limit: int = 200,
) -> list[Any]:
    if offset < 0 or limit < 1 or limit > 1000:
        raise ConformationalPersistenceError("invalid landscape page")
    if sequence_start is not None and sequence_start < 1:
        raise ConformationalPersistenceError("invalid landscape sequence range")
    if sequence_end is not None and (
        sequence_end < 1 or (sequence_start is not None and sequence_end < sequence_start)
    ):
        raise ConformationalPersistenceError("invalid landscape sequence range")

    request_record = await session.get(ConformationalMappingRequest, request_id)
    canonical_reference = await session.scalar(
        select(ConformationalMappingRecord).where(
            ConformationalMappingRecord.request_id == request_id,
            ConformationalMappingRecord.record_type == "frustrampnn_result_references",
        )
    )
    if request_record is not None and canonical_reference is not None:
        reference_payload = resolve_json_value(canonical_reference.payload_json)
        reference_rows = reference_payload.get("results") if isinstance(reference_payload, Mapping) else None
        if not isinstance(reference_rows, list) or not reference_rows:
            raise ConformationalPersistenceError(
                "canonical FrustraMPNN landscape references are malformed"
            )
        invocation_ids = {
            str(reference.get("invocation_id") or "")
            for reference in reference_rows
            if isinstance(reference, Mapping)
        }
        if "" in invocation_ids or len(invocation_ids) != len(reference_rows):
            raise ConformationalPersistenceError(
                "canonical FrustraMPNN landscape references are ambiguous"
            )
        global_statement = (
            select(FrustraMPNNLandscapeRow, FrustraMPNNResult.candidate_id)
            .join(
                FrustraMPNNResult,
                (FrustraMPNNResult.parent_job_id == FrustraMPNNLandscapeRow.parent_job_id)
                & (FrustraMPNNResult.invocation_id == FrustraMPNNLandscapeRow.invocation_id),
            )
            .where(
                FrustraMPNNLandscapeRow.parent_job_id == request_record.job_id,
                FrustraMPNNLandscapeRow.invocation_id.in_(invocation_ids),
            )
        )
        if candidate_id:
            global_statement = global_statement.where(FrustraMPNNResult.candidate_id == candidate_id)
        if entity_instance_id:
            global_statement = global_statement.where(
                FrustraMPNNLandscapeRow.entity_instance_id == entity_instance_id
            )
        if sequence_start is not None:
            global_statement = global_statement.where(
                FrustraMPNNLandscapeRow.sequence_index >= sequence_start
            )
        if sequence_end is not None:
            global_statement = global_statement.where(
                FrustraMPNNLandscapeRow.sequence_index <= sequence_end
            )
        result_rows = (await session.execute(
            global_statement.order_by(
                FrustraMPNNResult.candidate_id,
                FrustraMPNNLandscapeRow.entity_instance_id,
                FrustraMPNNLandscapeRow.sequence_index,
                FrustraMPNNLandscapeRow.mutation_aa,
            ).offset(offset).limit(limit)
        )).all()
        return [
            SimpleNamespace(
                id=row.id,
                candidate_id=canonical_candidate_id,
                entity_instance_id=row.entity_instance_id,
                auth_asym_id=row.auth_asym_id,
                auth_seq_id=row.auth_seq_id,
                insertion_code=row.insertion_code,
                sequence_index=row.sequence_index,
                wt=row.wt,
                mutation_aa=row.mutation_aa,
                score=row.score,
                score_class=row.score_class,
                scoreable=row.scoreable,
                status=row.status,
                reason=row.reason,
                provenance_json=resolve_json_value(row.provenance_json),
            )
            for row, canonical_candidate_id in result_rows
        ]

    statement = select(ConformationalMappingLandscapeRow).where(
        ConformationalMappingLandscapeRow.request_id == request_id
    )
    if candidate_id:
        statement = statement.where(ConformationalMappingLandscapeRow.candidate_id == candidate_id)
    if entity_instance_id:
        statement = statement.where(
            ConformationalMappingLandscapeRow.entity_instance_id == entity_instance_id
        )
    if sequence_start is not None:
        statement = statement.where(ConformationalMappingLandscapeRow.sequence_index >= sequence_start)
    if sequence_end is not None:
        statement = statement.where(ConformationalMappingLandscapeRow.sequence_index <= sequence_end)
    statement = statement.order_by(
        ConformationalMappingLandscapeRow.candidate_id,
        ConformationalMappingLandscapeRow.entity_instance_id,
        ConformationalMappingLandscapeRow.sequence_index,
        ConformationalMappingLandscapeRow.mutation_aa,
    ).offset(offset).limit(limit)
    rows = list((await session.execute(statement)).scalars().all())
    for row in rows:
        row.provenance_json = resolve_json_value(row.provenance_json)
    return rows


async def rollback_request_records(session: AsyncSession, request_id: str) -> None:
    """Test/failure seam for request-local rows only; never a broad rollback."""

    await session.execute(
        delete(ConformationalMappingStateLandscapeAnalysisRow).where(
            ConformationalMappingStateLandscapeAnalysisRow.request_id == request_id
        )
    )
    await session.execute(
        delete(ConformationalMappingStateLandscapeAnalysisPair).where(
            ConformationalMappingStateLandscapeAnalysisPair.request_id == request_id
        )
    )
    await session.execute(
        delete(ConformationalMappingStateLandscapeAnalysisHeader).where(
            ConformationalMappingStateLandscapeAnalysisHeader.request_id == request_id
        )
    )
    await session.execute(delete(ConformationalMappingLandscapeRow).where(ConformationalMappingLandscapeRow.request_id == request_id))
    await session.execute(delete(ConformationalMappingArtifact).where(ConformationalMappingArtifact.request_id == request_id))
    await session.execute(delete(ConformationalMappingRecord).where(ConformationalMappingRecord.request_id == request_id))

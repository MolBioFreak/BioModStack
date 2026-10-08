"""Transactional persistence and lifecycle authority for canonical CM products."""

from __future__ import annotations

import hashlib
import mimetypes
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Job,
)

from .contracts import (
    ContractValidationError,
    canonical_sha256,
    validate_contract_bundle,
    validate_schema,
)


RESULT_CONTRACT_BY_BACKEND = {
    "protenix_v2_ensemble": "conformational_mapping_protenix_v1",
    "confornets": "conformational_mapping_confornets_v1",
    "external_import": "conformational_mapping_import_v1",
}
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
RETRYABLE_STATES = frozenset({"failed", "cancelled"})
_RECORD_TYPES = frozenset(
    {
        "ensemble", "native_manifest", "structure_map", "landscape", "analysis",
        "handoff", "resampling", "lineage", "support", "missingness",
        "failure_receipt",
    }
)


class ConformationalPersistenceError(ValueError):
    """Canonical state could not be persisted without partial visibility."""


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
    session.add(job)
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
    await session.flush()


def _contained_file(root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or str(pure) != relative_path or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in relative_path:
        raise ConformationalPersistenceError("manifest contains an unsafe relative path")
    candidate = (root / relative_path).resolve(strict=True)
    candidate.relative_to(root)
    if not candidate.is_file() or candidate.is_symlink():
        raise ConformationalPersistenceError("manifest artifact is not a safe regular file")
    return candidate


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
    session.add(
        ConformationalMappingRecord(
            id=str(uuid.uuid4()), request_id=request_id, record_type=record_type,
            record_key=record_key, content_sha256=digest, payload_json=dict(payload),
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
                provenance_json={
                    "raw_csv_sha256": landscape["raw_csv_sha256"],
                    "checkpoint_sha256": landscape["checkpoint_sha256"],
                    "tool_sha256": landscape["tool_sha256"],
                    "threshold_policy_sha256": landscape["threshold_policy_sha256"],
                },
            ))


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
                       "cm_mutagenesis_handoff_v1"}
        }
        allowed_extensions = {
            "cm_structure_maps", "cm_frustration_landscapes", "cm_mutagenesis_handoffs",
            "cm_resampling_v1", "cm_lineage", "cm_support", "cm_missingness",
            "cm_derived_files",
        }
        unknown = set(bundle) - set(core_bundle) - allowed_extensions
        if unknown:
            raise ConformationalPersistenceError(
                f"unknown canonical result bundle members: {sorted(unknown)}"
            )
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
    descriptor = ensemble.get("resume_descriptor")
    if ensemble.get("resumable") is True and ensemble.get("resume_key") == "0" * 64:
        raise ConformationalPersistenceError("resumable canonical result has no immutable resume identity")
    if ensemble.get("resumable") is True and not isinstance(descriptor, Mapping):
        raise ConformationalPersistenceError("resumable canonical result has no validated descriptor")
    if ensemble.get("resumable") is False and descriptor is not None:
        raise ConformationalPersistenceError("nonresumable canonical result has an invalid descriptor")
    if record.resume_key == "0" * 64 and ensemble.get("resumable") is True:
        record.resume_key = ensemble["resume_key"]
    elif ensemble["resume_key"] != record.resume_key:
        raise ConformationalPersistenceError("result bundle resume identity mismatch")
    root = Path(result_root).resolve(strict=True)
    verified_artifacts: list[tuple[Mapping[str, Any], Path]] = []
    seen_paths: set[str] = set()
    for item in native["files"]:
        relative_path = item["relative_path"]
        if relative_path in seen_paths:
            raise ConformationalPersistenceError("native manifest contains a duplicate path")
        seen_paths.add(relative_path)
        path = _contained_file(root, relative_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ConformationalPersistenceError("native artifact hash or size mismatch")
        verified_artifacts.append((item, path))
    for item in bundle.get("cm_derived_files", []):
        if not isinstance(item, Mapping):
            raise ConformationalPersistenceError("derived artifact index contains a non-object")
        relative_path = str(item.get("relative_path") or "")
        if relative_path in seen_paths:
            raise ConformationalPersistenceError("derived artifact duplicates a native path")
        seen_paths.add(relative_path)
        path = _contained_file(root, relative_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256") or path.stat().st_size != item.get("bytes"):
            raise ConformationalPersistenceError("derived artifact hash or size mismatch")
        verified_artifacts.append((item, path))

    await _replace_record(session, record.request_id, "ensemble", "primary", ensemble)
    await _replace_record(session, record.request_id, "native_manifest", "primary", native)
    optional_records = {
        "cm_structure_maps": "structure_map", "cm_frustration_landscapes": "landscape",
        "cm_analysis_v1": "analysis", "cm_mutagenesis_handoffs": "handoff",
        "cm_resampling_v1": "resampling", "cm_lineage": "lineage",
        "cm_support": "support", "cm_missingness": "missingness",
    }
    optional_schema = {
        "structure_map": "cm_structure_map_v1",
        "landscape": "cm_frustration_landscape_v1",
        "analysis": "cm_analysis_v1",
        "handoff": "cm_mutagenesis_handoff_v1",
    }
    ensemble_candidate_ids = {item["candidate_id"] for item in ensemble["candidates"]}
    structure_map_ids = {
        str(value.get("candidate_id")) for value in bundle.get("cm_structure_maps", [])
        if isinstance(value, Mapping)
    }
    landscape_ids = {
        str(value.get("candidate_id")) for value in bundle.get("cm_frustration_landscapes", [])
        if isinstance(value, Mapping)
    }
    if structure_map_ids != ensemble_candidate_ids or landscape_ids != ensemble_candidate_ids:
        raise ConformationalPersistenceError(
            "derived structure-map and landscape candidate sets must exactly equal the ensemble"
        )
    if not isinstance(bundle.get("cm_analysis_v1"), Mapping):
        raise ConformationalPersistenceError("canonical analysis authority is missing")
    for bundle_key, record_type in optional_records.items():
        value = bundle.get(bundle_key)
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
            await _replace_record(
                session, record.request_id, record_type,
                str(payload.get("candidate_id") or payload.get("analysis_id") or index), payload,
            )

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

    landscapes = bundle.get("cm_frustration_landscapes") or []
    existing_landscapes = (
        await session.execute(
            select(ConformationalMappingLandscapeRow).where(
                ConformationalMappingLandscapeRow.request_id == record.request_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_landscapes is not None and landscapes:
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
            for index, value in enumerate(landscapes)
        }
        if incoming != stored:
            raise ConformationalPersistenceError("landscape retry conflicts with persisted matrix")
        landscapes = []
    for landscape in landscapes:
        validate_schema("cm_frustration_landscape_v1", landscape)
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
                        provenance_json={
                            "raw_csv_sha256": landscape["raw_csv_sha256"],
                            "checkpoint_sha256": landscape["checkpoint_sha256"],
                            "tool_sha256": landscape["tool_sha256"],
                            "threshold_policy_sha256": landscape["threshold_policy_sha256"],
                        },
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
) -> list[ConformationalMappingLandscapeRow]:
    if offset < 0 or limit < 1 or limit > 1000:
        raise ConformationalPersistenceError("invalid landscape page")
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
        if sequence_start < 1:
            raise ConformationalPersistenceError("invalid landscape sequence range")
        statement = statement.where(ConformationalMappingLandscapeRow.sequence_index >= sequence_start)
    if sequence_end is not None:
        if sequence_end < 1 or (sequence_start is not None and sequence_end < sequence_start):
            raise ConformationalPersistenceError("invalid landscape sequence range")
        statement = statement.where(ConformationalMappingLandscapeRow.sequence_index <= sequence_end)
    statement = statement.order_by(
        ConformationalMappingLandscapeRow.candidate_id,
        ConformationalMappingLandscapeRow.entity_instance_id,
        ConformationalMappingLandscapeRow.sequence_index,
        ConformationalMappingLandscapeRow.mutation_aa,
    ).offset(offset).limit(limit)
    return list((await session.execute(statement)).scalars().all())


async def rollback_request_records(session: AsyncSession, request_id: str) -> None:
    """Test/failure seam for request-local rows only; never a broad rollback."""

    await session.execute(delete(ConformationalMappingLandscapeRow).where(ConformationalMappingLandscapeRow.request_id == request_id))
    await session.execute(delete(ConformationalMappingArtifact).where(ConformationalMappingArtifact.request_id == request_id))
    await session.execute(delete(ConformationalMappingRecord).where(ConformationalMappingRecord.request_id == request_id))

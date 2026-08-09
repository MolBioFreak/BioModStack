"""Authenticated typed API for canonical conformational mapping."""

from __future__ import annotations

import hashlib
import httpx
import json
import os
import re
import shutil
import tempfile
import uuid
import copy
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal, Mapping, Sequence, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingSource,
    Job,
    get_session,
)
from paths import (
    get_data_root,
    get_results_dir,
    get_container_dir,
    get_weights_root,
    get_work_dir,
)
from services.conformational_mapping.contracts import candidate_id, canonical_sha256, validate_schema
from services.conformational_mapping.import_stager import (
    ImportStagingError,
    RegisteredArtifact,
    read_registered_artifact,
    stage_registered_artifacts,
    stage_registered_assets,
    verify_registered_artifact,
)
from services.conformational_mapping.import_snapshot import (
    ImportSnapshotError,
    MAX_IMPORT_MMCIF_BYTES,
    build_staged_import_snapshots,
)
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    MAX_STATE_LANDSCAPE_ANALYSIS_PAGE_OFFSET,
    StateLandscapeAnalysisProjectionAbsent,
    StateLandscapeAnalysisProjectionAmbiguous,

    get_request,
    issue_request_capability,
    paged_landscape,
    paged_state_landscape_analysis_rows,
    persist_derived_record,
    register_prepared_request,
    resolve_state_landscape_analysis_projection,
    state_landscape_analysis_artifact,
    state_landscape_analysis_pair_summaries,
    transition_request,
)
from services.conformational_mapping.mutagenesis_handoff import MutagenesisHandoffError, prepare_handoff
from services.conformational_mapping.resampling import ResamplingError, materialize_resampling_pair
from services.conformational_mapping.request_builder import (
    ConformationalMappingRequestError,
    bind_materialized_source_snapshot,
    materialize_trusted_internal_request,
    validate_request_params,
)
from services.job_control import cancel_job_lineage
from services.frustrampnn import runtime as _frustrampnn_runtime


router = APIRouter(prefix="/api/conformational-mapping", tags=["conformational-mapping"])
_PERSONAL_WORKFLOW_PRINCIPAL = "local-personal-workflow"
_APPLICATION_PROXY_HEADER = "X-BMS-CM-Proxy-Secret"
_COOKIE_PREFIX = "bms_cm_access_"
_CONFORNETS_CHAIN_ID = "A"
_CONFORNETS_TEST_CASE_ID = "bms-canonical-monomer"
_CONFORNETS_BENCHMARK_NAME = "biomodstack"


def _confornets_submission_policy() -> dict[str, str]:
    return {
        "chain_id": _CONFORNETS_CHAIN_ID,
        "test_case_id": _CONFORNETS_TEST_CASE_ID,
        "benchmark_name": _CONFORNETS_BENCHMARK_NAME,
    }


def _bind_confornets_submission_policy(settings: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {
        "sequence", "chain_id", "test_case_id", "benchmark_name", "checkpoint", "config",
        "references", "transfer_source", "backend_identity",
    }.intersection(settings)
    if forbidden:
        raise HTTPException(status_code=422, detail="server-owned ConforNets fields may not be supplied")
    return {**dict(settings), **_confornets_submission_policy()}


def _bind_runtime_policy(backend: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(policy)
    if backend != "protenix_v2_ensemble" and values != {"use_default_params": True}:
        raise HTTPException(
            status_code=422,
            detail="runtime cycle/step overrides are supported only by Protenix",
        )
    return values


def _canonical_analysis_policy() -> dict[str, Any]:
    return {
        "sign_zero_epsilon": 0.000001,
        "clash_detector_id": "bms_clash",
        "clash_detector_version": "1",
        "outer_support_minimum": 0.8,
        "inner_support_minimum": 0.6,
        "sign_consistency_minimum": 0.8,
        "clash_free_minimum": 0.9,
        "rank_stability_minimum": 0.6,
        "minimum_common_ranked_universe_size": 3,
    }


def _bind_analysis_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    canonical = _canonical_analysis_policy()
    try:
        supplied = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"))
        expected = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="analysis policy is invalid") from exc
    if supplied != expected:
        raise HTTPException(status_code=422, detail="server-owned analysis policy may not be overridden")
    return canonical


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    notes: str = Field(default="", max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    backend: Literal["protenix_v2_ensemble", "confornets", "external_import"]
    ordered_seeds: list[int] = Field(min_length=1)
    samples_per_seed: int = Field(ge=1, le=100)
    feature_policy: dict[str, Any]
    runtime_policy: dict[str, Any]
    analysis_policy: dict[str, Any]
    registered_snapshot_id: str | None = None
    registered_artifact_ids: list[str] = Field(default_factory=list)
    registered_sequence_id: str | None = None
    registered_reference_ids: list[str] = Field(default_factory=list, max_length=2)
    registered_checkpoint_id: str | None = None
    registered_config_id: str | None = None
    registered_transfer_id: str | None = None
    confornets: dict[str, Any] | None = None
    state_landscape_comparison: dict[str, Any] | None = None


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_row_key: str
    substitution: str = Field(min_length=1, max_length=1)
    structure_map_key: str
    feature_policy: dict[str, Any]
    resampling_settings: dict[str, Any]
    expected_source_hashes: dict[str, str]


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResamplingLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handoff_key: str
    wt_features: dict[str, dict[str, Any]]
    mutant_features: dict[str, dict[str, Any]]
    tool_identity: dict[str, Any]


_SOURCE_KINDS = frozenset({
    "complex_snapshot", "structure_upload", "structure_artifact", "protein_sequence",
    "confornets_checkpoint", "confornets_config", "confornets_state",
})
_SOURCE_MAX_BYTES = {
    "complex_snapshot": 64 * 1024 * 1024,
    "protein_sequence": 16 * 1024 * 1024,
    "confornets_config": 16 * 1024 * 1024,
    "structure_upload": MAX_IMPORT_MMCIF_BYTES,
    "structure_artifact": MAX_IMPORT_MMCIF_BYTES,
    "confornets_checkpoint": 2 * 1024 * 1024 * 1024,
    "confornets_state": 2 * 1024 * 1024 * 1024,
}
_CM_VRAM_ESTIMATE_MB = {
    "external_import": 12_000,
    "confornets": 16_000,
    "protenix_v2_ensemble": 24_000,
}
def _cm_job_admission(backend: str, request_payload: Mapping[str, Any]) -> dict[str, int]:
    sequence_length = 0
    for target in request_payload["targets"]:
        direct_sequence = str(target.get("sequence") or "")
        if direct_sequence:
            sequence_length += len(direct_sequence)
            continue
        sequence_length += sum(
            len(str(entity.get("sequence") or ""))
            for entity in target.get("entities", [])
            if entity.get("entity_type") == "protein"
        )
    return {
        "vram_estimate_mb": _CM_VRAM_ESTIMATE_MB[backend],
        "sequence_length": sequence_length or 300,
    }


def _validated_source_suffix(source_kind: str, filename: str) -> str:
    source_suffix = Path(filename).suffix.lower()
    allowed_suffixes = {
        "structure_upload": {".cif", ".mmcif"},
        "structure_artifact": {".cif", ".mmcif"},
        "complex_snapshot": {".json"},
        "protein_sequence": {".txt", ".fa", ".fasta", ""},
        "confornets_checkpoint": {".pt", ".pth", ".ckpt"},
        "confornets_config": {".json", ".yaml", ".yml"},
        "confornets_state": {".pt", ".pth", ".ckpt"},
    }.get(source_kind)
    if allowed_suffixes is None or source_suffix not in allowed_suffixes:
        detail = (
            "structure sources must be mmCIF (.cif or .mmcif); PDB is not accepted"
            if source_kind in {"structure_upload", "structure_artifact"}
            else "registered source extension is unsupported"
        )
        raise HTTPException(status_code=422, detail=detail)
    return source_suffix


def _validate_upload_source_kind(source_kind: str) -> None:
    if source_kind == "confornets_checkpoint":
        raise HTTPException(
            status_code=422,
            detail="ConforNets checkpoint authority is server-managed",
        )


def _registered_source_format(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return "mmcif"
    return suffix.removeprefix(".") or "unknown"


def _reject_reserved_source_metadata(metadata: Mapping[str, Any]) -> None:
    if any(str(key).startswith("resolved_") for key in metadata) or {
        "normalization_receipt", "provider_receipt",
    }.intersection(metadata):
        raise HTTPException(status_code=422, detail="server source receipts are server-owned")


def _source_authority_path(source_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_id):
        raise ValueError("source authority ID is invalid")
    return get_data_root() / "conformational_mapping" / "source_authority" / f"{source_id}.json"


def _publish_source_authority(
    source: ConformationalMappingSource,
    *,
    authority_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "schema_name": "cm_source_authority_receipt",
        "schema_version": 1,
        "source_id": source.source_id,
        "source_kind": source.source_kind,
        "content_sha256": source.content_sha256,
        "authority_kind": authority_kind,
        "payload": dict(payload),
    }
    receipt = {**unsigned, "receipt_sha256": canonical_sha256(unsigned)}
    destination = _source_authority_path(source.source_id)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{source.source_id}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return receipt


def _read_source_authority(source: ConformationalMappingSource) -> dict[str, Any] | None:
    try:
        path = _source_authority_path(source.source_id)
        file_stat = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_size > 64 * 1024
            or file_stat.st_uid != os.geteuid()
            or file_stat.st_mode & 0o077
        ):
            return None
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or set(receipt) != {
            "schema_name", "schema_version", "source_id", "source_kind", "content_sha256",
            "authority_kind", "payload", "receipt_sha256",
        }:
            return None
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if (
            receipt["schema_name"] != "cm_source_authority_receipt"
            or receipt["schema_version"] != 1
            or receipt["source_id"] != source.source_id
            or receipt["source_kind"] != source.source_kind
            or receipt["content_sha256"] != source.content_sha256
            or not isinstance(receipt["payload"], dict)
            or receipt["receipt_sha256"] != canonical_sha256(unsigned)
        ):
            return None
        return receipt
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _snapshot_chain_ids(snapshots: Sequence[Mapping[str, Any]]) -> list[str]:
    values: list[str] = []
    for snapshot in snapshots:
        mappings = snapshot.get("instance_mappings")
        if not isinstance(mappings, list):
            continue
        for mapping in mappings:
            if not isinstance(mapping, Mapping):
                continue
            chain_id = str(
                mapping.get("output_auth_asym_id") or mapping.get("output_label_asym_id") or ""
            ).strip()
            if chain_id and chain_id not in values:
                values.append(chain_id)
    return values


def _run_record_selected_input(
    source: ConformationalMappingSource,
    *,
    model_id: str | None,
    sample_id: str | None,
    chain_ids: list[str],
) -> dict[str, Any]:
    metadata = source.metadata_json or {}
    label = str(
        metadata.get("name")
        or metadata.get("target_id")
        or source.source_id
    ).strip()[:255]
    if not label:
        label = source.source_id
    record: dict[str, Any] = {
        "source_id": source.source_id,
        "source_kind": source.source_kind,
        "source_label": label,
        "source_sha256": source.content_sha256,
    }
    receipt = _read_source_authority(source)
    receipt_payload = receipt.get("payload") if isinstance(receipt, Mapping) else None
    if (
        isinstance(receipt, Mapping)
        and isinstance(receipt_payload, Mapping)
        and receipt.get("authority_kind") == "rcsb_download"
        and receipt_payload.get("provider") == "RCSB"
        and isinstance(receipt_payload.get("accession"), str)
        and re.fullmatch(r"[A-Z0-9]{4}", str(receipt_payload["accession"]))
    ):
        record["provider"] = "RCSB"
        record["accession"] = str(receipt_payload["accession"])
    if model_id:
        record["model_id"] = model_id
    if sample_id:
        record["sample_id"] = sample_id
    if chain_ids:
        record["chain_ids"] = chain_ids
    return record


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while publishing registered source")
        view = view[written:]


def _server_confornets_identity() -> dict[str, str]:
    image = get_container_dir() / "confornets-canonical.sif"
    if not image.is_file() or image.is_symlink():
        raise HTTPException(status_code=503, detail="canonical ConforNets image is not installed")
    digest = _sha256_path(image)
    return {
        "backend_version": "canonical-4df561a",
        "backend_commit": "4df561a1fbd0fd2b9c7a230fa62957a837d9f72d",
        "runtime_identity": "confornets-canonical-write-ledger-v1",
        "container_digest": f"sha256:{digest}",
        "model_id": "confornets-canonical-v1",
        "feature_identity_sha256": canonical_sha256({"coordinate_emission": "write_time_v1"}),
        "repo_path": "/opt/confornets",
    }


async def _ensure_managed_confornets_checkpoint(
    session: AsyncSession,
) -> ConformationalMappingSource | None:
    """Expose one server-owned checkpoint identity without accepting a host path."""
    checkpoint = get_weights_root() / "openfold3" / "of3-p2-155k.pt"
    if not checkpoint.is_file() or checkpoint.is_symlink():
        return None
    digest = _sha256_path(checkpoint)
    size = checkpoint.stat().st_size
    source_id = f"cm_src_server_confornets_checkpoint_{digest[:32]}"
    existing = await session.get(ConformationalMappingSource, source_id)
    if existing is not None:
        if (
            existing.principal_id != _PERSONAL_WORKFLOW_PRINCIPAL
            or existing.source_kind != "confornets_checkpoint"
            or existing.content_sha256 != digest
            or existing.size_bytes != size
            or existing.storage_root != str(get_weights_root())
            or existing.relative_path != "openfold3/of3-p2-155k.pt"
            or existing.metadata_json.get("managed") is not True
            or existing.metadata_json.get("asset_id") != "confornets.of3p2.checkpoint"
            or not existing.immutable
        ):
            raise HTTPException(status_code=503, detail="managed ConforNets checkpoint identity conflicts")
        return existing
    managed = ConformationalMappingSource(
        source_id=source_id,
        principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
        source_kind="confornets_checkpoint",
        storage_root=str(get_weights_root()),
        relative_path="openfold3/of3-p2-155k.pt",
        content_sha256=digest,
        size_bytes=size,
        metadata_json={
            "managed": True,
            "asset_id": "confornets.of3p2.checkpoint",
            "model_id": "of3-p2-155k",
            "provenance": "server-owned immutable runtime asset",
        },
        immutable=True,
        created_at=datetime.utcnow(),
    )
    session.add(managed)
    await session.flush()
    return managed


async def _managed_checkpoint_for_submission(
    session: AsyncSession,
    requested_source_id: str | None,
) -> ConformationalMappingSource:
    managed = await _ensure_managed_confornets_checkpoint(session)
    if managed is None:
        raise HTTPException(status_code=503, detail="installed managed checkpoint is unavailable")
    if requested_source_id != managed.source_id:
        raise HTTPException(
            status_code=422,
            detail="ConforNets requires the installed managed checkpoint",
        )
    return managed


def _runtime_registry(backend: str) -> dict[str, Any]:
    try:
        analysis_runtime = _frustrampnn_runtime.cm_analysis_runtime_registry_v1(
            get_container_dir()
        )
    except _frustrampnn_runtime.RuntimeValidationError as exc:
        raise HTTPException(
            status_code=503, detail="registered FrustraMPNN runtime is unavailable"
        ) from exc
    if backend == "confornets":
        return {
            "schema_name": "cm_runtime_registry", "schema_version": 1,
            "analysis_runtime": analysis_runtime, **_server_confornets_identity(),
        }
    if backend == "protenix_v2_ensemble":
        image = get_container_dir() / "protenix.sif"
        checkpoint = get_weights_root() / "protenix" / "checkpoint" / "protenix-v2.pt"
        if not image.is_file() or image.is_symlink() or not checkpoint.is_file() or checkpoint.is_symlink():
            raise HTTPException(status_code=503, detail="registered Protenix runtime is unavailable")
        return {
            "schema_name": "cm_runtime_registry", "schema_version": 1,
            "backend_version": "protenix-v2", "backend_commit": "c3bfc365b3e1341a11935eddfe7bfdc308092147",
            "runtime_identity": "installed-protenix-v2", "model_id": "protenix-v2",
            "container_digest": f"sha256:{_sha256_path(image)}",
            "checkpoint_sha256": _sha256_path(checkpoint),
            "checkpoint_relative_path": "checkpoint/protenix-v2.pt",
            "analysis_runtime": analysis_runtime,
        }
    return {
        "schema_name": "cm_runtime_registry", "schema_version": 1,
        "backend_version": "1", "backend_commit": "biomodstack-import-v1",
        "runtime_identity": "descriptor-safe-import-v1", "model_id": "external-import",
        "container_digest": "sha256:" + "0" * 64, "checkpoint_sha256": "0" * 64,
        "analysis_runtime": analysis_runtime,
    }


@router.get("/sources")
async def list_sources(request: Request, session: AsyncSession = Depends(get_session)):
    principal_id = _principal(request)
    managed_checkpoint = await _ensure_managed_confornets_checkpoint(session)
    managed_checkpoint_source_id = managed_checkpoint.source_id if managed_checkpoint is not None else None
    await session.commit()
    rows = (
        await session.execute(
            select(ConformationalMappingSource).where(
                ConformationalMappingSource.immutable.is_(True),
                ConformationalMappingSource.principal_id == principal_id,
            ).order_by(ConformationalMappingSource.created_at, ConformationalMappingSource.source_id)
        )
    ).scalars().all()
    return {"sources": [
        {"source_id": row.source_id, "source_kind": row.source_kind,
         "format": _registered_source_format(row.relative_path),
         "sha256": row.content_sha256, "bytes": row.size_bytes,
         "metadata": row.metadata_json,
         "managed_checkpoint": row.source_id == managed_checkpoint_source_id,
         "authority_receipt": _read_source_authority(row),
         "submission_policy": _confornets_submission_policy() if row.source_kind == "protein_sequence" else None,
         "created_at": row.created_at.isoformat() + "Z"}
        for row in rows
    ]}


@router.post("/sources")
async def register_source(
    request: Request, source_kind: str = Form(...), metadata_json: str = Form("{}"),
    file: UploadFile = File(...), session: AsyncSession = Depends(get_session),
):
    principal_id = _mutation_principal(request)
    if source_kind not in _SOURCE_KINDS:
        raise HTTPException(status_code=422, detail="unsupported conformational-mapping source kind")
    _validate_upload_source_kind(source_kind)
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="source metadata must be valid JSON") from exc
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="source metadata must be an object")
    _reject_reserved_source_metadata(metadata)
    registry = get_data_root() / "conformational_mapping_sources"
    registry.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = registry / f".upload-{uuid.uuid4()}"
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o440)
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _SOURCE_MAX_BYTES[source_kind]:
                raise HTTPException(status_code=413, detail="registered source exceeds its server limit")
            digest.update(chunk)
            _write_all(descriptor, chunk)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="registered source is empty")
    content_sha256 = digest.hexdigest()
    owner_tag = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:8]
    source_id = f"cm_src_{owner_tag}_{source_kind[:10]}_{content_sha256[:32]}"
    existing = await session.get(ConformationalMappingSource, source_id)
    if existing is not None:
        temporary.unlink(missing_ok=True)
        if (
            existing.principal_id != principal_id or existing.source_kind != source_kind
            or existing.content_sha256 != content_sha256 or existing.size_bytes != size
            or not existing.immutable
        ):
            raise HTTPException(status_code=409, detail="registered source identity is not available")
        try:
            verify_registered_artifact(
                _registered(existing), principal_id=principal_id,
                maximum_bytes=2 * 1024 * 1024 * 1024,
            )
        except ImportStagingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "source_id": source_id, "source_kind": source_kind,
            "format": _registered_source_format(existing.relative_path),
            "sha256": content_sha256, "bytes": size, "metadata": existing.metadata_json,
            "authority_receipt": _read_source_authority(existing),
        }
    destination_dir = registry / source_id
    try:
        destination_dir.mkdir(mode=0o750)
    except FileExistsError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="registered source publication raced or is ambiguous") from exc
    try:
        source_suffix = _validated_source_suffix(source_kind, file.filename or "")
    except HTTPException:
        temporary.unlink(missing_ok=True)
        destination_dir.rmdir()
        raise
    destination = destination_dir / f"content{source_suffix}"
    created_destination = False
    try:
        os.link(temporary, destination, follow_symlinks=False)
        created_destination = True
        temporary.unlink()
        directory_fd = os.open(destination_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        destination_dir.rmdir()
        raise
    snapshots: list[Mapping[str, Any]] = []
    if source_kind == "complex_snapshot":
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
            snapshots = payload if isinstance(payload, list) else [payload]
            for snapshot in snapshots:
                validate_schema("cm_complex_snapshot_v1", snapshot)
            metadata = {
                **metadata,
                "target_ids": [value["target_id"] for value in snapshots],
            }
        except Exception as exc:
            if created_destination:
                destination.unlink(missing_ok=True)
                destination_dir.rmdir()
            raise HTTPException(status_code=422, detail=f"invalid complex snapshot: {exc}") from exc
    elif source_kind == "protein_sequence":
        sequence = "".join(destination.read_text(encoding="utf-8").split()).upper()
        if not sequence or any(value not in "ACDEFGHIKLMNPQRSTVWY" for value in sequence):
            if created_destination:
                destination.unlink(missing_ok=True)
                destination_dir.rmdir()
            raise HTTPException(status_code=422, detail="protein sequence source is invalid")
        metadata = {**metadata, "sequence": sequence, "target_id": str(metadata.get("target_id") or source_id)}
    source_record = ConformationalMappingSource(
        source_id=source_id, principal_id=principal_id, source_kind=source_kind,
        storage_root=str(registry), relative_path=f"{source_id}/{destination.name}",
        content_sha256=content_sha256, size_bytes=size, metadata_json=metadata,
        immutable=True, created_at=datetime.utcnow(),
    )
    session.add(source_record)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        destination.unlink(missing_ok=True)
        destination_dir.rmdir()
        raise
    destination_dir.chmod(0o550)
    authority_receipt = None
    if source_kind == "complex_snapshot":
        authority_receipt = _publish_source_authority(
            source_record,
            authority_kind="complex_snapshot_normalization",
            payload={"chain_ids": _snapshot_chain_ids(snapshots)},
        )
    return {
        "source_id": source_id, "source_kind": source_kind,
        "format": _registered_source_format(destination.name),
        "sha256": content_sha256, "bytes": size, "metadata": metadata,
        "authority_receipt": authority_receipt,
    }


def _rcsb_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)


@router.post("/sources/rcsb/{pdb_id}")
async def register_rcsb_mmcif_source(
    pdb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Stream a public RCSB mmCIF into bounded storage, then register it immutably."""
    accession = pdb_id.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", accession):
        raise HTTPException(status_code=422, detail="RCSB accession must be exactly four letters or digits")
    maximum_bytes = _SOURCE_MAX_BYTES["structure_upload"]
    spool = tempfile.SpooledTemporaryFile(max_size=min(maximum_bytes, 8 * 1024 * 1024), mode="w+b")
    prefix = bytearray()
    size = 0
    try:
        try:
            async with _rcsb_http_client() as client:
                async with client.stream("GET", f"https://files.rcsb.org/download/{accession}.cif") as response:
                    if response.status_code == 404:
                        raise HTTPException(status_code=404, detail="RCSB accession was not found")
                    if response.status_code != 200:
                        raise HTTPException(status_code=502, detail="RCSB mmCIF download returned an unexpected status")
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > maximum_bytes:
                            raise HTTPException(status_code=413, detail="RCSB mmCIF exceeds the registered-source limit")
                        if len(prefix) < 4096:
                            prefix.extend(chunk[: 4096 - len(prefix)])
                        spool.write(chunk)
        except HTTPException:
            raise
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="RCSB mmCIF download timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="RCSB mmCIF download failed") from exc
        if not bytes(prefix).lstrip().startswith(b"data_"):
            raise HTTPException(status_code=502, detail="RCSB response is not raw mmCIF")
        spool.seek(0)
        upload = UploadFile(filename=f"{accession}.cif", file=cast(BinaryIO, spool))
        registration = await register_source(
            request=request,
            source_kind="structure_upload",
            metadata_json=json.dumps({"name": f"RCSB {accession}"}),
            file=upload,
            session=session,
        )
        source = await session.get(ConformationalMappingSource, registration["source_id"])
        if source is None or source.principal_id != _principal(request):
            raise HTTPException(status_code=500, detail="registered RCSB source authority is unavailable")
        authority_receipt = _publish_source_authority(
            source,
            authority_kind="rcsb_download",
            payload={
                "provider": "RCSB",
                "accession": accession,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {**registration, "authority_receipt": authority_receipt}
    finally:
        spool.close()


def _principal(request: Request) -> str:
    """CM is a personal-workflow lane: local callers share one owner identity."""
    return _PERSONAL_WORKFLOW_PRINCIPAL


def _mutation_principal(request: Request) -> str:
    return _principal(request)


async def _authorized_record(
    request_id: str,
    request: Request,
    session: AsyncSession,
    *,
    mutation: bool = False,
):
    record = await get_request(session, request_id)
    principal_id = _principal(request)
    if record is None or record.principal_id != principal_id:
        raise HTTPException(status_code=404, detail="conformational-mapping request not found")
    return record


async def _source(
    session: AsyncSession,
    source_id: str | None,
    principal_id: str,
    allowed_kinds: set[str],
) -> ConformationalMappingSource:
    if not source_id:
        raise HTTPException(status_code=422, detail="registered source ID is required")
    source = await session.get(ConformationalMappingSource, source_id)
    if (
        source is None
        or source.principal_id != principal_id
        or source.source_kind not in allowed_kinds
        or not source.immutable
    ):
        raise HTTPException(status_code=403, detail="registered source is unavailable")
    return source


def _registered(source: ConformationalMappingSource) -> RegisteredArtifact:
    return RegisteredArtifact(
        artifact_id=source.source_id, principal_id=source.principal_id,
        storage_root=Path(source.storage_root), relative_path=source.relative_path,
        content_sha256=source.content_sha256, size_bytes=source.size_bytes,
    )


@router.get("/sources/{source_id}/content")
async def source_content(
    source_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    principal_id = _principal(request)
    source = await _source(
        session,
        source_id,
        principal_id,
        {"structure_upload", "structure_artifact"},
    )
    if _registered_source_format(source.relative_path) != "mmcif":
        raise HTTPException(status_code=415, detail="browser preview requires registered mmCIF")
    try:
        payload = read_registered_artifact(
            _registered(source),
            principal_id=principal_id,
            maximum_bytes=64 * 1024 * 1024,
        )
    except ImportStagingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="chemical/x-mmcif",
        headers={
            "ETag": f'"sha256:{source.content_sha256}"',
            "Cache-Control": "private, immutable, max-age=31536000",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _external_import_target_ids(
    sources: list[ConformationalMappingSource],
    *,
    registered_snapshot_id: str | None,
) -> list[str]:
    if registered_snapshot_id:
        raise HTTPException(
            status_code=422,
            detail="external-import snapshots are derived automatically from staged mmCIF bytes",
        )
    if len(sources) != 1:
        raise HTTPException(status_code=422, detail="external import currently requires exactly one mmCIF structure")
    target_ids: list[str] = []
    for source in sources:
        if Path(source.relative_path).suffix.lower() not in {".cif", ".mmcif"}:
            raise HTTPException(status_code=422, detail="external import accepts mmCIF structures only")
        target_id = str((source.metadata_json or {}).get("target_id") or source.source_id).strip()
        if not target_id:
            raise HTTPException(status_code=422, detail="external import target identity is empty")
        target_ids.append(target_id)
    if len(set(target_ids)) != len(target_ids):
        raise HTTPException(status_code=422, detail="external import target identities must be unique")
    return target_ids


def _read_registered_json(source: ConformationalMappingSource) -> Any:
    try:
        payload = read_registered_artifact(
            _registered(source), principal_id=source.principal_id,
            maximum_bytes=_SOURCE_MAX_BYTES["complex_snapshot"],
        )
    except ImportStagingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="registered source is not valid JSON") from exc


def _remove_request_root(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _confornets_snapshot(
    *, target_id: str, sequence: str, chain_id: str,
    source_sha256: str, coordinates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    mappings = []
    for coordinate in coordinates:
        stable_id = candidate_id(coordinate)
        mappings.append({
            "source_entity_id": "1", "source_instance_id": chain_id,
            "runtime_target_id": target_id, "runtime_entity_id": "1",
            "runtime_instance_id": chain_id, "runtime_order": 0,
            "candidate_id": stable_id, "output_entity_id": "1",
            "output_label_asym_id": chain_id, "output_auth_asym_id": chain_id,
            "output_entity_order": 0,
        })
    snapshot = {
        "schema_name": "cm_complex_snapshot", "schema_version": 1,
        "target_id": target_id, "target_order": 0,
        "original_source_path": "registered/protein_sequence",
        "original_source_sha256": source_sha256,
        "normalized_source_sha256": "0" * 64,
        "entities": [{
            "entity_type": "protein", "source_entity_id": "1", "count": 1,
            "ordered_instance_ids": [chain_id], "sequence": sequence,
        }],
        "bonds": [], "instance_mappings": mappings,
        "admission": {
            "token_count": len(sequence), "atom_count": len(sequence) * 4,
            "token_limit": 20000, "conversion_omissions": [],
        },
        "unsupported_fields": [],
    }
    snapshot["normalized_source_sha256"] = canonical_sha256(
        {key: value for key, value in snapshot.items() if key != "normalized_source_sha256"}
    )
    validate_schema("cm_complex_snapshot_v1", snapshot)
    return snapshot


@router.post("/requests", status_code=201)
async def submit_request(
    body: SubmitRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    principal_id = _mutation_principal(request)
    await _ensure_managed_confornets_checkpoint(session)
    params: dict[str, Any] = {
        "backend": body.backend,
        "ordered_seeds": body.ordered_seeds,
        "samples_per_seed": body.samples_per_seed,
        "feature_policy": body.feature_policy,
        "runtime_policy": _bind_runtime_policy(body.backend, body.runtime_policy),
        "analysis_policy": _bind_analysis_policy(body.analysis_policy),
    }
    if body.state_landscape_comparison is not None:
        params["state_landscape_comparison"] = body.state_landscape_comparison
    import_sources: list[ConformationalMappingSource] = []
    confor_sources: list[ConformationalMappingSource] = []
    snapshot_source: ConformationalMappingSource | None = None
    sequence_source: ConformationalMappingSource | None = None
    snapshots: list[Any] = []
    settings: dict[str, Any] = {}
    if body.backend == "protenix_v2_ensemble":
        if (
            body.registered_artifact_ids or body.registered_sequence_id or body.confornets
            or body.registered_reference_ids or body.registered_checkpoint_id
            or body.registered_config_id or body.registered_transfer_id
        ):
            raise HTTPException(status_code=422, detail="inactive backend fields are forbidden")
        snapshot_source = await _source(
            session, body.registered_snapshot_id, principal_id,
            {"complex_snapshot"},
        )
        snapshots = _read_registered_json(snapshot_source)
        if isinstance(snapshots, dict):
            snapshots = [snapshots]
        if not isinstance(snapshots, list) or not snapshots:
            raise HTTPException(status_code=422, detail="registered snapshot bundle is empty")
        if (
            [snapshot.get("target_order") for snapshot in snapshots] != list(range(len(snapshots)))
            or len({snapshot.get("target_id") for snapshot in snapshots}) != len(snapshots)
        ):
            raise HTTPException(status_code=422, detail="registered snapshot order or target identity is invalid")
        snapshot_payload = snapshots
        params["targets"] = [
            {"target_id": snapshot["target_id"], "target_order": index}
            for index, snapshot in enumerate(snapshots)
        ]
        params["protenix_snapshot_id"] = snapshot_source.source_id
    elif body.backend == "external_import":
        if (
            body.registered_sequence_id or body.confornets or body.registered_reference_ids
            or body.registered_checkpoint_id or body.registered_config_id
            or body.registered_transfer_id
        ):
            raise HTTPException(status_code=422, detail="inactive backend fields are forbidden")
        if not body.registered_artifact_ids:
            raise HTTPException(status_code=422, detail="at least one registered structure is required")
        import_sources = [
            await _source(session, source_id, principal_id, {"structure_upload", "structure_artifact"})
            for source_id in body.registered_artifact_ids
        ]
        target_ids = _external_import_target_ids(
            import_sources,
            registered_snapshot_id=body.registered_snapshot_id,
        )
        if body.ordered_seeds != [0] or body.samples_per_seed != 1:
            raise HTTPException(status_code=422, detail="external import requires ordered_seeds [0] and samples_per_seed 1")
        params["targets"] = [
            {"target_id": target_id, "target_order": index}
            for index, target_id in enumerate(target_ids)
        ]
        params["ordered_seeds"] = [0]
        params["samples_per_seed"] = 1
    else:
        if body.registered_snapshot_id or body.registered_artifact_ids:
            raise HTTPException(status_code=422, detail="inactive backend fields are forbidden")
        sequence_source = await _source(session, body.registered_sequence_id, principal_id, {"protein_sequence"})
        checkpoint_source = await _managed_checkpoint_for_submission(
            session, body.registered_checkpoint_id
        )
        references = [
            await _source(session, value, principal_id, {"structure_upload", "structure_artifact"})
            for value in body.registered_reference_ids
        ]
        config_source = (
            await _source(session, body.registered_config_id, principal_id, {"confornets_config"})
            if body.registered_config_id else None
        )
        transfer_source = (
            await _source(session, body.registered_transfer_id, principal_id, {"confornets_state"})
            if body.registered_transfer_id else None
        )
        confor_sources = [sequence_source, checkpoint_source, *references]
        if config_source is not None:
            confor_sources.append(config_source)
        if transfer_source is not None:
            confor_sources.append(transfer_source)
        settings = _bind_confornets_submission_policy(body.confornets or {})
        sequence = str(sequence_source.metadata_json.get("sequence") or "")
        target_id = str(sequence_source.metadata_json.get("target_id") or sequence_source.source_id)
        settings.update(
            {
                "sequence": sequence,
                "checkpoint": {"path": f"registered/{checkpoint_source.source_id}", "sha256": checkpoint_source.content_sha256},
                "config": None if config_source is None else {"path": f"registered/{config_source.source_id}", "sha256": config_source.content_sha256},
                "references": [
                    {
                        "reference_id": source.source_id,
                        "staged_path": f"registered/{source.source_id}",
                        "content_sha256": source.content_sha256,
                        "state": str(source.metadata_json.get("state") or "reference"),
                        "source": "registered_artifact",
                    }
                    for source in references
                ],
                "transfer_source": None if transfer_source is None else {
                    "kind": str(transfer_source.metadata_json.get("kind") or "confornet_state"),
                    "staged_path": f"registered/{transfer_source.source_id}",
                    "content_sha256": transfer_source.content_sha256,
                    "source_test_cases": str(transfer_source.metadata_json.get("source_test_cases") or ""),
                },
                "backend_identity": _server_confornets_identity(),
            }
        )
        params["targets"] = [
            {"target_id": target_id, "target_order": 0, "sequence": sequence, "molecule_type": "protein", "chain_count": 1}
        ]
        params["confornets"] = settings

    if body.backend == "protenix_v2_ensemble":
        if snapshot_source is None:
            raise HTTPException(status_code=422, detail="registered snapshot authority is unavailable")
        selected_source = snapshot_source
        selected_chain_ids = _snapshot_chain_ids(
            [snapshot for snapshot in snapshots if isinstance(snapshot, Mapping)]
        )
    elif body.backend == "external_import":
        selected_source = import_sources[0]
        selected_chain_ids = []
    else:
        if sequence_source is None:
            raise HTTPException(status_code=422, detail="registered sequence authority is unavailable")
        selected_source = sequence_source
        selected_chain_ids = [_CONFORNETS_CHAIN_ID]
    params["run_record"] = {
        "name": body.name.strip(),
        "notes": body.notes.strip(),
        "selected_input": _run_record_selected_input(
            selected_source,
            model_id=None,
            sample_id=None,
            chain_ids=selected_chain_ids,
        ),
    }

    request_id = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:cm:submit:{principal_id}:{body.idempotency_key}"))
        if body.idempotency_key else str(uuid.uuid4())
    )
    submission_sha256 = canonical_sha256(body.model_dump(mode="json", exclude_none=True))
    existing_request = await get_request(session, request_id)
    if existing_request is not None:
        if existing_request.principal_id != principal_id:
            raise HTTPException(status_code=409, detail="idempotent request identity is unavailable")
        existing_job = await session.get(Job, existing_request.job_id)
        if not existing_job or (existing_job.provenance or {}).get("cm_submission_sha256") != submission_sha256:
            raise HTTPException(status_code=409, detail="idempotency key was reused with a different submission")
        return {
            "request_id": existing_request.request_id, "job_id": existing_request.job_id,
            "status": existing_request.status, "backend": existing_request.backend,
            "request_sha256": existing_request.request_sha256,
            "coordinate_plan_sha256": existing_request.coordinate_plan_sha256,
            "expected_cardinality": existing_request.coordinate_plan_json["expected_cardinality"],
            "idempotent_retry": True,
        }
    root = get_results_dir() / f"conformational_mapping_{request_id}"
    staged_import = None
    try:
        if snapshot_source is not None:
            staged_snapshot = stage_registered_assets(
                [_registered(snapshot_source)], principal_id=principal_id,
                destination_root=root / "registered_snapshot",
            )[snapshot_source.source_id]
            snapshot_payload = snapshots if isinstance(snapshots, list) else [snapshots]
            (root / "cm_complex_snapshots_v1.json").write_text(
                json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        if confor_sources:
            staged_assets = stage_registered_assets(
                [_registered(source) for source in confor_sources],
                principal_id=principal_id, destination_root=root / "registered",
            )
            settings = params["confornets"]
            settings["checkpoint"]["path"] = staged_assets[checkpoint_source.source_id].relative_to(root).as_posix()
            if config_source is not None:
                settings["config"]["path"] = staged_assets[config_source.source_id].relative_to(root).as_posix()
            for item, source in zip(settings["references"], references, strict=True):
                item["staged_path"] = staged_assets[source.source_id].relative_to(root).as_posix()
            if transfer_source is not None:
                settings["transfer_source"]["staged_path"] = staged_assets[transfer_source.source_id].relative_to(root).as_posix()
        if import_sources:
            staged_import = stage_registered_artifacts(
                [_registered(source) for source in import_sources], principal_id=principal_id,
                request_id=request_id, destination_root=root / "registered_import",
                maximum_bytes=MAX_IMPORT_MMCIF_BYTES,
            )
            params["import_receipt_id"] = staged_import.receipt["receipt_sha256"]
            params["resolved_import_entries"] = staged_import.receipt["entries"]
        validate_request_params(params)
        materialized = materialize_trusted_internal_request(
            params, output_dir=root, request_id=request_id, principal_id=principal_id,
        )
        request_payload = json.loads(materialized.request_path.read_text(encoding="utf-8"))
        coordinate_plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
        analysis_targets = request_payload["targets"]
        if body.backend == "external_import":
            if staged_import is None:
                raise ImportSnapshotError("external import staging did not produce an immutable receipt")
            import_snapshots = build_staged_import_snapshots(
                staged_root=staged_import.root,
                entries=staged_import.receipt["entries"],
                targets=request_payload["targets"],
                coordinates=coordinate_plan["coordinates"],
            )
            analysis_targets = import_snapshots
            (root / "cm_complex_snapshots_v1.json").write_text(
                json.dumps(import_snapshots, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            normalized_snapshot = import_snapshots[0]
            request_payload, coordinate_plan = bind_materialized_source_snapshot(
                materialized,
                source_snapshot_sha256=canonical_sha256(normalized_snapshot),
                selected_input=_run_record_selected_input(
                    import_sources[0],
                    model_id=str(normalized_snapshot.get("source_model_id") or "") or None,
                    sample_id=None,
                    chain_ids=_snapshot_chain_ids(import_snapshots),
                ),
            )
        if body.backend == "confornets":
            if sequence_source is None:
                raise ConformationalMappingRequestError("registered sequence authority is unavailable")
            confor_snapshot = _confornets_snapshot(
                target_id=request_payload["targets"][0]["target_id"],
                sequence=request_payload["confornets"]["sequence"],
                chain_id=request_payload["confornets"]["chain_id"],
                source_sha256=sequence_source.content_sha256,
                coordinates=coordinate_plan["coordinates"],
            )
            analysis_targets = [confor_snapshot]
            (root / "cm_complex_snapshots_v1.json").write_text(
                json.dumps([confor_snapshot], sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        (root / "cm_runtime_registry_v1.json").write_bytes(
            json.dumps(_runtime_registry(body.backend), sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        # The producer binds installed runtime/container/checkpoint bytes into the
        # full descriptor. Persistence locks that descriptor atomically at first
        # successful ingestion; no partial output is resumable before then.
        resume_key = "0" * 64
        token, token_digest = issue_request_capability()
        job = Job(
            id=request_id, name=body.name.strip(), status="queued", model_id="conformational_mapping",
            mode="map", params=materialized.launch_params, output_dir=str(root), queue_status="queued",
            **_cm_job_admission(body.backend, {"targets": analysis_targets}),
            lineage_root_job_id=request_id, stage_family="conformational_mapping", stage_mode=body.backend,
            provenance={
                "cm_request_sha256": request_payload["request_sha256"],
                "cm_coordinate_plan_sha256": coordinate_plan["coordinate_plan_sha256"],
                "cm_principal_id": principal_id,
                "cm_submission_sha256": submission_sha256,
            },
        )
        cm_record = await register_prepared_request(
            session, job=job, principal_id=principal_id, request=request_payload,
            coordinate_plan=coordinate_plan, resume_key=resume_key,
            capability_sha256=token_digest,
        )
        if cm_record.status == "prepared":
            await transition_request(session, cm_record, status="queued", progress={"phase": "queued"})
        await session.commit()
        response.set_cookie(
            key=f"{_COOKIE_PREFIX}{request_id.replace('-', '_')}", value=token,
            httponly=True, secure=request.url.scheme == "https", samesite="strict",
            path=f"/api/conformational-mapping/requests/{request_id}",
        )
        return {
            "request_id": request_id, "job_id": request_id, "status": "queued",
            "backend": body.backend, "request_sha256": request_payload["request_sha256"],
            "coordinate_plan_sha256": coordinate_plan["coordinate_plan_sha256"],
            "expected_cardinality": coordinate_plan["expected_cardinality"],
        }
    except HTTPException:
        await session.rollback()
        _remove_request_root(root)
        raise
    except (
        ConformationalMappingRequestError,
        ImportStagingError,
        ImportSnapshotError,
        ConformationalPersistenceError,
        OSError,
        KeyError,
        TypeError,
    ) as exc:
        await session.rollback()
        _remove_request_root(root)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/requests/{request_id}")
async def request_status(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session)
    job = await session.get(Job, record.job_id)
    status = record.status
    progress = record.progress_json
    failure_receipt = record.failure_receipt_json if status == "failed" else None
    if job is not None and record.status not in {"completed", "failed", "cancelled"}:
        job_state = str(job.status or job.queue_status or "").lower()
        if job_state == "running" and record.status == "queued":
            status = "running"
            progress = {"phase": "running", "job_stage": job.current_stage}
        elif job_state in {"failed", "cancelled"}:
            status = job_state
            progress = {"phase": job_state}
            failure_receipt = {
                "schema_name": "cm_failure_receipt", "schema_version": 1,
                "request_id": record.request_id, "job_id": job.id,
                "terminal_state": job_state,
                "message": str(job.error_message or f"canonical job {job_state}"),
                "recorded_at": datetime.utcnow().isoformat() + "Z",
            }
    return {
        "request_id": record.request_id, "job_id": record.job_id, "backend": record.backend,
        "status": status, "job_status": job.status if job else None,
        "progress": progress, "failure_receipt": failure_receipt,
        "retry_eligible": status in {"failed", "cancelled"},
        "result_contract_id": record.result_contract_id,
        "run_record": (getattr(record, "request_json", {}) or {}).get("run_record"),
    }


@router.get("/requests/{request_id}/progress")
async def request_progress(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session)
    job = await session.get(Job, record.job_id)
    return {
        "request_id": request_id, "status": record.status,
        "progress": record.progress_json, "job_stage": job.current_stage if job else None,
        "job_progress": job.stage_progress if job else None,
    }


@router.get("/requests/{request_id}/failure-receipts")
async def request_failure_receipts(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    await _authorized_record(request_id, request, session)
    rows = (
        await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request_id,
                ConformationalMappingRecord.record_type == "failure_receipt",
            ).order_by(ConformationalMappingRecord.created_at, ConformationalMappingRecord.record_key)
        )
    ).scalars().all()
    return {
        "request_id": request_id,
        "failure_receipts": [
            {"receipt_id": row.record_key, "sha256": row.content_sha256, "payload": row.payload_json}
            for row in rows
        ],
    }


async def _canonical_record(
    session: AsyncSession, request_id: str, record_type: str, record_key: str | None = None
) -> ConformationalMappingRecord:
    statement = select(ConformationalMappingRecord).where(
        ConformationalMappingRecord.request_id == request_id,
        ConformationalMappingRecord.record_type == record_type,
    )
    if record_key is not None:
        statement = statement.where(ConformationalMappingRecord.record_key == record_key)
    rows = list((await session.execute(statement.order_by(ConformationalMappingRecord.created_at))).scalars().all())
    if len(rows) != 1:
        raise HTTPException(status_code=409, detail=f"canonical {record_type} authority is missing or ambiguous")
    return rows[0]


@router.post("/requests/{request_id}/analysis")
async def compute_analysis(
    request_id: str, _body: AnalysisRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _authorized_record(request_id, request, session)
    analysis = (await _canonical_record(session, request_id, "analysis")).payload_json
    return {"request_id": request_id, "analysis_id": analysis["analysis_id"], "result_count": len(analysis["results"]), "analysis_sha256": canonical_sha256(analysis)}


@router.post("/requests/{request_id}/handoffs", status_code=201)
async def prepare_mutagenesis_handoff(
    request_id: str, body: HandoffRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _authorized_record(request_id, request, session, mutation=True)
    ensemble = (await _canonical_record(session, request_id, "ensemble", "primary")).payload_json
    analysis = (await _canonical_record(session, request_id, "analysis")).payload_json
    structure_map = (
        await _canonical_record(session, request_id, "structure_map", body.structure_map_key)
    ).payload_json
    source_record = await get_request(session, request_id)
    job = await session.get(Job, source_record.job_id if source_record else "")
    if job is None:
        raise HTTPException(status_code=409, detail="canonical source job is missing")
    snapshot_path = Path(job.output_dir) / "cm_complex_snapshots_v1.json"
    try:
        snapshots = json.loads(snapshot_path.read_text(encoding="utf-8"))
        selected_result = next(
            item for item in analysis.get("results", [])
            if item.get("source_row_key") == body.source_row_key
        )
        target_id = selected_result["identity"]["target_id"]
        snapshot = next(value for value in snapshots if value["target_id"] == target_id)
        handoff = prepare_handoff(
            ensemble=ensemble, analysis=analysis, complex_snapshot=snapshot,
            structure_map=structure_map, source_row_key=body.source_row_key,
            substitution=body.substitution.upper(), feature_policy=body.feature_policy,
            resampling_settings=body.resampling_settings,
            expected_source_hashes=body.expected_source_hashes,
        )
        key = handoff["idempotency_key"]
        await persist_derived_record(
            session, request_id, record_type="handoff", record_key=key, payload=handoff,
        )
        await session.commit()
    except (OSError, StopIteration, json.JSONDecodeError, MutagenesisHandoffError, ConformationalPersistenceError, KeyError, TypeError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "request_id": request_id, "prepared_handoff_id": f"cm_handoff_{key}",
        "idempotency_key": key, "scheduler_launch_count": 0,
        "consumer_contract": {
            "adapter_version": handoff["adapter_version"],
            "chain_id": handoff["auth_asym_id"], "author_position": handoff["auth_seq_id"],
            "insertion_code": handoff["insertion_code"], "wt": handoff["validated_wt"],
            "substitution": handoff["substitution"], "mutation_set_string": handoff["mutation_set_string"],
        },
    }


def _resampling_snapshot(snapshot: Mapping[str, Any], *, target_id: str) -> dict[str, Any]:
    value = copy.deepcopy(dict(snapshot))
    value["target_id"] = target_id
    for mapping in value.get("instance_mappings", []):
        mapping["runtime_target_id"] = target_id
        mapping["candidate_id"] = "pending-runtime-candidate"
    value["normalized_source_sha256"] = canonical_sha256(
        {key: item for key, item in value.items() if key != "normalized_source_sha256"}
    )
    return value


@router.post("/requests/{request_id}/resampling", status_code=201)
async def launch_resampling(
    request_id: str, body: ResamplingLaunchRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Atomically materialize and queue one idempotent matched WT/mutant launch."""

    source_record = await _authorized_record(request_id, request, session, mutation=True)
    principal_id = source_record.principal_id
    handoff_row = await _canonical_record(session, request_id, "handoff", body.handoff_key)
    handoff = handoff_row.payload_json
    source_job = await session.get(Job, source_record.job_id)
    if source_job is None or not source_job.output_dir:
        raise HTTPException(status_code=409, detail="canonical source job is missing")
    snapshot_path = Path(source_job.output_dir) / "cm_complex_snapshots_v1.json"
    root: Path | None = None
    root_created = False
    try:
        snapshots = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshots = snapshots if isinstance(snapshots, list) else [snapshots]
        snapshot = next(item for item in snapshots if item["target_id"] == handoff["target_id"])
        pair = materialize_resampling_pair(
            snapshot, handoff, wt_features=body.wt_features,
            mutant_features=body.mutant_features, tool_identity=body.tool_identity,
        )
        child_request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:cm:resampling:{pair['pair_id']}"))
        existing = await get_request(session, child_request_id)
        if existing is not None:
            if existing.principal_id != principal_id:
                raise HTTPException(status_code=409, detail="resampling identity is unavailable")
            return {
                "request_id": child_request_id, "job_id": existing.job_id,
                "source_request_id": request_id, "pair_id": pair["pair_id"],
                "status": existing.status, "idempotent_retry": True,
            }
        wt_target_id = f"{snapshot['target_id']}:wt"
        mutant_target_id = f"{snapshot['target_id']}:mutant:{handoff['mutation_set_id'][:12]}"
        pair_snapshots = [
            _resampling_snapshot(pair["wt_snapshot"], target_id=wt_target_id),
            _resampling_snapshot(pair["mutant_snapshot"], target_id=mutant_target_id),
        ]
        settings = handoff["resampling_settings"]
        params = {
            "backend": "protenix_v2_ensemble",
            "targets": [
                {"target_id": wt_target_id, "target_order": 0},
                {"target_id": mutant_target_id, "target_order": 1},
            ],
            "ordered_seeds": settings["ordered_seeds"],
            "samples_per_seed": settings["samples_per_seed"],
            "feature_policy": handoff["feature_policy"],
            "runtime_policy": settings["runtime_policy"],
            "analysis_policy": source_record.request_json["analysis_policy"],
            "protenix_snapshot_id": f"cm_resampling_pair:{pair['pair_id']}",
        }
        root = get_results_dir() / f"conformational_mapping_{child_request_id}"
        if root.exists():
            raise ConformationalPersistenceError("resampling request root exists without durable authority")
        root_created = True
        materialized = materialize_trusted_internal_request(
            params, output_dir=root, request_id=child_request_id,
            principal_id=principal_id, source_kind="cm_resampling_v1",
        )
        (root / "cm_complex_snapshots_v1.json").write_text(
            json.dumps(pair_snapshots, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        (root / "cm_resampling_pair_request_v1.json").write_text(
            json.dumps(pair, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        (root / "cm_runtime_registry_v1.json").write_text(
            json.dumps(_runtime_registry("protenix_v2_ensemble"), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        request_payload = json.loads(materialized.request_path.read_text(encoding="utf-8"))
        plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
        token, token_digest = issue_request_capability()
        job = Job(
            id=child_request_id, name=f"CM resampling {handoff['mutation_set_string']}",
            status="queued", model_id="conformational_mapping", mode="map",
            params=materialized.launch_params, output_dir=str(root), queue_status="queued",
            **_cm_job_admission("protenix_v2_ensemble", request_payload),
            parent_job_id=source_job.id, lineage_root_job_id=source_job.lineage_root_job_id or source_job.id,
            stage_family="conformational_mapping", stage_mode="resampling",
            provenance={"cm_pair_id": pair["pair_id"], "cm_handoff_key": body.handoff_key},
        )
        child = await register_prepared_request(
            session, job=job, principal_id=principal_id, request=request_payload,
            coordinate_plan=plan, resume_key="0" * 64, capability_sha256=token_digest,
        )
        child.retry_of_request_id = request_id
        await persist_derived_record(
            session, request_id, record_type="resampling", record_key=pair["pair_id"],
            payload={**pair, "child_request_id": child_request_id, "status": "queued"},
        )
        await transition_request(session, child, status="queued", progress={"phase": "queued"})
        await session.commit()
        response_payload = {
            "request_id": child_request_id, "job_id": child_request_id,
            "source_request_id": request_id, "pair_id": pair["pair_id"],
            "status": "queued", "idempotent_retry": False,
        }
        return response_payload
    except HTTPException:
        await session.rollback()
        if root_created and root is not None:
            _remove_request_root(root)
        raise
    except (OSError, StopIteration, json.JSONDecodeError, KeyError, TypeError,
            ResamplingError, ConformationalMappingRequestError,
            ConformationalPersistenceError) as exc:
        await session.rollback()
        if root_created and root is not None:
            _remove_request_root(root)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/requests/{request_id}/cancel")
async def cancel_request(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session, mutation=True)
    if record.status not in {"prepared", "queued", "running"}:
        raise HTTPException(status_code=409, detail="request is not cancellable")
    await cancel_job_lineage(record.job_id, session, error_message="Cancelled through typed CM API")
    await transition_request(session, record, status="cancelled", progress={"phase": "cancelled"})
    await session.commit()
    return {"request_id": request_id, "status": "cancelled"}


@router.post("/requests/{request_id}/retry")
async def retry_request(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session, mutation=True)
    if record.status == "completed":
        raise HTTPException(status_code=409, detail="completed request authority cannot be retried")
    job = await session.get(Job, record.job_id)
    if job is None:
        raise HTTPException(status_code=409, detail="request job is missing")
    if record.status not in {"failed", "cancelled"}:
        job_state = str(job.status or job.queue_status or "").lower()
        if job_state not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="request is not retry eligible")
        await transition_request(
            session, record, status=job_state, progress={"phase": job_state},
            failure_receipt={
                "schema_name": "cm_failure_receipt", "schema_version": 1,
                "request_id": record.request_id, "job_id": job.id,
                "terminal_state": job_state,
                "message": str(job.error_message or f"canonical job {job_state}"),
                "recorded_at": datetime.utcnow().isoformat() + "Z",
            } if job_state == "failed" else None,
        )
    job.status = "queued"
    job.queue_status = "queued"
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.nextflow_run_id = None
    job.retry_count = int(job.retry_count or 0) + 1
    retry_params = dict(job.params or {})
    retry_params["resume_work_dir"] = str(get_work_dir())
    job.params = retry_params
    await transition_request(
        session, record, status="queued",
        progress={"phase": "queued", "completed_coordinates": 0},
    )
    await session.commit()
    return {"request_id": request_id, "job_id": job.id, "status": "queued", "retry_count": job.retry_count}


@router.get("/requests/{request_id}/results")
async def request_results(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session)
    rows = (
        await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request_id
            ).order_by(ConformationalMappingRecord.record_type, ConformationalMappingRecord.record_key)
        )
    ).scalars().all()
    artifacts = (
        await session.execute(
            select(ConformationalMappingArtifact).where(
                ConformationalMappingArtifact.request_id == request_id
            ).order_by(
                ConformationalMappingArtifact.candidate_id,
                ConformationalMappingArtifact.relative_path,
                ConformationalMappingArtifact.artifact_id,
            )
        )
    ).scalars().all()
    return {
        "request_id": request_id, "result_contract_id": record.result_contract_id,
        "records": [
            {"type": row.record_type, "key": row.record_key, "sha256": row.content_sha256, "payload": row.payload_json}
            for row in rows
        ],
        "artifacts": [
            {
                "artifact_id": item.artifact_id, "candidate_id": item.candidate_id,
                "role": item.role, "relative_path": item.relative_path,
                "sha256": item.content_sha256, "bytes": item.size_bytes,
                "media_type": item.media_type, "metadata": item.metadata_json,
            }
            for item in artifacts
        ],
    }


@router.get("/requests/{request_id}/landscape")
async def request_landscape(
    request_id: str, request: Request, candidate_id: str | None = None,
    entity_instance_id: str | None = None,
    sequence_start: int | None = Query(default=None, ge=1),
    sequence_end: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    await _authorized_record(request_id, request, session)
    try:
        rows = await paged_landscape(
            session, request_id, candidate_id=candidate_id,
            entity_instance_id=entity_instance_id,
            sequence_start=sequence_start, sequence_end=sequence_end,
            offset=offset, limit=limit,
        )
    except ConformationalPersistenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "request_id": request_id, "offset": offset, "limit": limit,
        "candidate_id": candidate_id, "entity_instance_id": entity_instance_id,
        "sequence_start": sequence_start, "sequence_end": sequence_end,
        "next_offset": offset + len(rows) if len(rows) == limit else None,
        "rows": [
            {
                "candidate_id": row.candidate_id, "entity_instance_id": row.entity_instance_id,
                "auth_asym_id": row.auth_asym_id, "auth_seq_id": row.auth_seq_id,
                "insertion_code": row.insertion_code, "sequence_index": row.sequence_index,
                "wt": row.wt, "mutation_aa": row.mutation_aa, "score": row.score,
                "class": row.score_class, "scoreable": row.scoreable,
                "status": row.status, "reason": row.reason, "provenance": row.provenance_json,
            }
            for row in rows
        ],
    }


@router.get("/requests/{request_id}/state-landscape-analysis")
async def state_landscape_analysis_summary(
    request_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    analysis_id: str | None = None,
):
    """Expose one compact, request-governed state-analysis header without canonical payload reads."""

    await _authorized_record(request_id, request, session)
    try:
        header = await resolve_state_landscape_analysis_projection(
            session, request_id, analysis_id=analysis_id,
        )
    except StateLandscapeAnalysisProjectionAbsent as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateLandscapeAnalysisProjectionAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    pairs = await state_landscape_analysis_pair_summaries(session, header)
    artifact = await state_landscape_analysis_artifact(session, header)
    if artifact is None:
        raise HTTPException(
            status_code=409,
            detail="state landscape analysis immutable export is unavailable or ambiguous",
        )
    return {
        "request_id": request_id,
        "analysis_id": header.analysis_id,
        "authority": {
            "content_sha256": header.content_sha256,
            "source_ensemble_sha256": header.source_ensemble_sha256,
            "source_landscape_sha256": header.source_landscape_sha256,
            "source_structure_map_sha256": header.source_structure_map_sha256,
            "comparison_sha256": header.comparison_sha256,
            "formula_version": header.formula_version,
            "formula_sha256": header.formula_sha256,
            "policy_sha256": header.policy_sha256,
        },
        "comparison": {
            "mode": header.comparison_mode,
            "target_id": header.comparison_target_id,
            "scope": header.comparison_scope,
            "reference_backend_coordinates": header.reference_backend_coordinates_json,
            "reference_candidate_id": header.reference_candidate_id,
        },
        "counts": {
            "pairs": header.pair_count,
            "rows": header.row_count,
            "exclusions": header.exclusion_count,
        },
        "pairs": [
            {
                "pair_id": pair.pair_id,
                "candidate_a_id": pair.candidate_a_id,
                "candidate_b_id": pair.candidate_b_id,
            }
            for pair in pairs
        ],
        "artifact": None if artifact is None else {
            "artifact_id": artifact.artifact_id,
            "content_sha256": artifact.content_sha256,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
            "download_url": f"/api/conformational-mapping/requests/{request_id}/artifacts/{artifact.artifact_id}",
        },
    }


@router.get("/requests/{request_id}/state-landscape-analysis/rows")
async def state_landscape_analysis_rows(
    request_id: str,
    request: Request,
    analysis_id: str | None = None,
    pair_id: str | None = None,
    candidate_id: str | None = None,
    entity_instance_id: str | None = None,
    auth_asym_id: str | None = None,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    offset: Annotated[int, Query(ge=0, le=MAX_STATE_LANDSCAPE_ANALYSIS_PAGE_OFFSET)] = 0,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
):
    """Page normalized state-analysis rows; all values are persisted artifact projections."""

    if offset > MAX_STATE_LANDSCAPE_ANALYSIS_PAGE_OFFSET:
        raise HTTPException(status_code=422, detail="invalid state landscape analysis page")
    await _authorized_record(request_id, request, session)
    try:
        header, rows, has_more = await paged_state_landscape_analysis_rows(
            session,
            request_id,
            analysis_id=analysis_id,
            pair_id=pair_id,
            candidate_id=candidate_id,
            entity_instance_id=entity_instance_id,
            auth_asym_id=auth_asym_id,
            sequence_start=sequence_start,
            sequence_end=sequence_end,
            offset=offset,
            limit=limit,
        )
    except StateLandscapeAnalysisProjectionAbsent as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateLandscapeAnalysisProjectionAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConformationalPersistenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "request_id": request_id,
        "selected_analysis_id": header.analysis_id,
        "offset": offset,
        "limit": limit,
        "applied_filters": {
            "pair_id": pair_id,
            "candidate_id": candidate_id,
            "entity_instance_id": entity_instance_id,
            "auth_asym_id": auth_asym_id,
            "sequence_start": sequence_start,
            "sequence_end": sequence_end,
        },
        "next_offset": offset + len(rows) if has_more else None,
        "rows": [
            {
                "pair_id": row.pair_id,
                "candidate_a_id": row.candidate_a_id,
                "candidate_b_id": row.candidate_b_id,
                "identity": {
                    "target_id": row.target_id,
                    "entity_instance_id": row.entity_instance_id,
                    "auth_asym_id": row.auth_asym_id,
                    "auth_seq_id": row.auth_seq_id,
                    "insertion_code": row.insertion_code,
                    "sequence_index": row.sequence_index,
                    "validated_wt": row.validated_wt,
                },
                "metrics": row.metrics_json,
                "availability": row.availability_json,
            }
            for row in rows
        ],
    }


@router.get("/requests/{request_id}/lineage")
async def request_lineage(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    await _authorized_record(request_id, request, session)
    rows = (
        await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request_id,
                ConformationalMappingRecord.record_type.in_(["lineage", "handoff", "resampling"]),
            ).order_by(ConformationalMappingRecord.record_type, ConformationalMappingRecord.record_key)
        )
    ).scalars().all()
    return {"request_id": request_id, "lineage": [row.payload_json for row in rows]}


def _resolve_artifact_runtime_alias(path: str | Path) -> Path:
    """Translate a known data-root prefix without dereferencing the artifact leaf."""
    candidate = Path(os.path.abspath(os.path.expanduser(str(path))))
    current_root = Path(os.path.abspath(str(get_data_root())))
    alias_roots = [current_root]
    for variable in ("BMS_CONTAINER_STATE_PATH", "BMS_STATE_DIR", "BMS_DATA"):
        configured_root = os.getenv(variable, "").strip()
        if configured_root:
            alias_roots.append(Path(configured_root))
    alias_roots.extend((Path("/mnt/BioModStack"), Path.home() / ".biomodstack"))

    seen: set[str] = set()
    for raw_root in alias_roots:
        alias_root = Path(os.path.abspath(os.path.expanduser(str(raw_root))))
        if str(alias_root) in seen:
            continue
        seen.add(str(alias_root))
        try:
            relative_path = candidate.relative_to(alias_root)
        except ValueError:
            continue
        remapped = current_root / relative_path
        if remapped.exists() or remapped.is_symlink():
            return remapped
    return candidate


def _open_verified_artifact_descriptor(
    *, storage_path: str, root_path: str, size_bytes: int, content_sha256: str,
) -> int:
    """Open one persisted artifact through the active runtime's data-root alias."""
    root = _resolve_artifact_runtime_alias(root_path).resolve(strict=True)
    lexical_path = _resolve_artifact_runtime_alias(storage_path)
    descriptor: int | None = None
    try:
        descriptor = os.open(lexical_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = Path(f"/proc/self/fd/{descriptor}").resolve(strict=True)
        opened.relative_to(root)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size_bytes:
            raise OSError("artifact is not the registered regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or digest.hexdigest() != content_sha256:
            raise OSError("artifact identity changed")
        return descriptor
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise


def _artifact_byte_range(supplied_range: str | None, size_bytes: int) -> tuple[int, int, int]:
    """Resolve one HTTP byte range, clamping a satisfiable end at EOF."""
    if not supplied_range:
        return 0, size_bytes - 1, 200
    try:
        unit, value = supplied_range.split("=", 1)
        left, right = value.split("-", 1)
        if unit != "bytes" or "," in value or size_bytes <= 0:
            raise ValueError
        if left:
            start = int(left)
            if start < 0 or start >= size_bytes:
                raise ValueError
            end = min(int(right), size_bytes - 1) if right else size_bytes - 1
            if end < start:
                raise ValueError
        else:
            suffix_bytes = int(right)
            if suffix_bytes <= 0:
                raise ValueError
            start = max(0, size_bytes - suffix_bytes)
            end = size_bytes - 1
        return start, end, 206
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=416,
            detail="invalid artifact byte range",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        )


@router.get("/requests/{request_id}/artifacts/{artifact_id}")
async def download_artifact(
    request_id: str, artifact_id: str, request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _authorized_record(request_id, request, session)
    artifact = await session.get(ConformationalMappingArtifact, artifact_id)
    if artifact is None or artifact.request_id != request_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    record = await get_request(session, request_id)
    job = await session.get(Job, record.job_id if record else "")
    if job is None or not job.output_dir:
        raise HTTPException(status_code=409, detail="artifact storage authority is unavailable")
    try:
        descriptor = _open_verified_artifact_descriptor(
            storage_path=artifact.storage_path,
            root_path=job.output_dir,
            size_bytes=artifact.size_bytes,
            content_sha256=artifact.content_sha256,
        )
    except (OSError, ValueError):
        raise HTTPException(status_code=409, detail="artifact byte identity is unavailable")

    try:
        start, end, status_code = _artifact_byte_range(
            request.headers.get("range"), artifact.size_bytes,
        )
    except HTTPException:
        os.close(descriptor)
        raise
    os.lseek(descriptor, start, os.SEEK_SET)
    remaining = end - start + 1

    def content():
        nonlocal remaining
        try:
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("artifact became truncated during transfer")
                remaining -= len(chunk)
                yield chunk
        finally:
            os.close(descriptor)

    headers = {
        "ETag": f'"{artifact.content_sha256}"', "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
        "Content-Disposition": f'attachment; filename="{Path(artifact.relative_path).name.replace(chr(34), "")}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{artifact.size_bytes}"
    return StreamingResponse(content(), status_code=status_code, media_type=artifact.media_type, headers=headers)

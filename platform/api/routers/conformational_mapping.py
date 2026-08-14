"""Authenticated typed API for canonical conformational mapping."""

from __future__ import annotations

import hashlib
import httpx
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import uuid
import copy
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal, Mapping, Sequence, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    ConformationalMappingSource,
    Job,
    get_session,
)
from paths import (
    get_data_root,
    get_results_dir,
    get_container_dir,
    get_weights_root,
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
    validate_materialized_coordinate_plan,
    validate_request_params,
)
from services.conformational_mapping.rcsb_source import (
    RcsbSourceError,
    discover_rcsb_contexts,
    resolve_and_materialize_rcsb_selection,
)
from services.job_control import cancel_job_lineage
from services.frustrampnn import runtime as _frustrampnn_runtime
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)


router = APIRouter(prefix="/api/conformational-mapping", tags=["conformational-mapping"])
_PERSONAL_WORKFLOW_PRINCIPAL = "local-personal-workflow"
_APPLICATION_PROXY_HEADER = "X-BMS-CM-Proxy-Secret"
_COOKIE_PREFIX = "bms_cm_access_"
_CONFORNETS_CHAIN_ID = "A"
_CONFORNETS_TEST_CASE_ID = "bms-canonical-monomer"
_CONFORNETS_BENCHMARK_NAME = "biomodstack"


def _authorization_enabled() -> bool:
    """Return whether the retained CM principal boundary is active."""
    return os.getenv("BMS_CM_AUTHORIZATION_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
    # The wire name stays plural for compatibility with the launcher contract.
    # External import is nevertheless singular in authority: exactly one item
    # is accepted by this bounded collection.
    registered_artifact_ids: list[str] = Field(default_factory=list, max_length=1)
    registered_sequence_id: str | None = None
    registered_reference_ids: list[str] = Field(default_factory=list, max_length=2)
    registered_checkpoint_id: str | None = None
    registered_config_id: str | None = None
    registered_transfer_id: str | None = None
    confornets: dict[str, Any] | None = None
    state_landscape_comparison: dict[str, Any] | None = None
    frustrampnn_settings: FrustraMPNNRequestedSettings = Field(
        default_factory=default_frustrampnn_settings
    )

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_frustrampnn_settings(
        cls, value: Any,
    ) -> FrustraMPNNRequestedSettings:
        return validate_complete_requested_settings(value)


class RcsbSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accession: str | None = Field(default=None, min_length=4, max_length=4)
    model_id: str | None = Field(default=None, min_length=1, max_length=64)
    sample_id: str | None = Field(default=None, min_length=1, max_length=128)
    chain_ids: list[str] = Field(default_factory=list, max_length=128)
    entity_ids: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def _validate_identity_lists(self) -> "RcsbSelection":
        if len(set(self.chain_ids)) != len(self.chain_ids):
            raise ValueError("RCSB chain selection must not contain duplicates")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("RCSB entity selection must not contain duplicates")
        if self.accession is not None:
            normalized = self.accession.strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{4}", normalized):
                raise ValueError("RCSB accession must be exactly four letters or digits")
            self.accession = normalized
        return self


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
    if source_kind == "structure_artifact":
        raise HTTPException(
            status_code=422,
            detail="prior-run artifact authority must use the Your Runs registration endpoint",
        )
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


def _validate_source_authority_payload(
    source: ConformationalMappingSource,
    *,
    authority_kind: str,
    payload: Mapping[str, Any],
) -> None:
    """Validate the small server-owned receipt envelope before it is published."""

    if not authority_kind or len(authority_kind) > 96:
        raise ValueError("source authority kind is invalid")
    expected_source_kind = {
        "rcsb_download": "structure_upload",
        "complex_snapshot_normalization": "complex_snapshot",
        "completed_run_artifact": "structure_artifact",
    }.get(authority_kind)
    if expected_source_kind is None:
        raise ValueError("source authority kind is not governed")
    if source.source_kind != expected_source_kind:
        raise ValueError("source authority kind does not match the registered source kind")
    if authority_kind == "rcsb_download":
        if payload.get("provider") != "RCSB":
            raise ValueError("RCSB receipt provider is invalid")
        accession = str(payload.get("accession") or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", accession):
            raise ValueError("RCSB receipt accession is invalid")
        selection = payload.get("selection")
        selected = RcsbSelection.model_validate(selection)
        if selected.accession != accession:
            raise ValueError("RCSB receipt selection accession does not match its source")
        if (
            selected.model_id is None
            or selected.sample_id != "asymmetric-unit"
            or len(selected.chain_ids) != 1
            or len(selected.entity_ids) != 1
        ):
            raise ValueError("RCSB receipt selection is not fully resolved")
        source_sha256 = str(payload.get("source_sha256") or "")
        download_sha256 = str(payload.get("download_sha256") or "")
        if source_sha256 != source.content_sha256:
            raise ValueError("RCSB receipt source digest disagrees with registered bytes")
        if not re.fullmatch(r"[0-9a-f]{64}", download_sha256):
            raise ValueError("RCSB receipt download digest is invalid")
        if payload.get("materialization") != "selected_asymmetric_unit_context_v1":
            raise ValueError("RCSB receipt materialization identity is invalid")
    elif authority_kind == "complex_snapshot_normalization":
        chain_ids = payload.get("chain_ids")
        if not isinstance(chain_ids, list) or any(
            not isinstance(value, str) or not value for value in chain_ids
        ) or len(set(chain_ids)) != len(chain_ids):
            raise ValueError("complex snapshot receipt chain identity is invalid")
    elif authority_kind == "completed_run_artifact":
        required = {"request_id", "job_id", "artifact_id", "content_sha256"}
        if required.difference(payload) or any(
            not isinstance(payload.get(key), str) or not payload[key]
            for key in required
        ):
            raise ValueError("completed-run artifact receipt identity is incomplete")
        if payload["content_sha256"] != source.content_sha256:
            raise ValueError("completed-run artifact receipt hash disagrees with source")


def _publish_source_authority(
    source: ConformationalMappingSource,
    *,
    authority_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_source_authority_payload(
        source, authority_kind=authority_kind, payload=payload
    )
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
    if destination.exists() or destination.is_symlink():
        existing = _read_source_authority(source)
        if existing is None:
            raise RuntimeError("source authority path exists but is not a valid receipt")
        existing_unsigned = {
            key: value for key, value in existing.items() if key != "receipt_sha256"
        }
        if existing_unsigned != unsigned:
            raise RuntimeError("source authority identity conflicts with an existing receipt")
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{source.source_id}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
        directory_fd = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
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
        _validate_source_authority_payload(
            source,
            authority_kind=str(receipt["authority_kind"]),
            payload=receipt["payload"],
        )
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


def _snapshot_entity_ids(snapshots: Sequence[Mapping[str, Any]]) -> list[str]:
    values: list[str] = []
    for snapshot in snapshots:
        entities = snapshot.get("entities")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            entity_id = str(entity.get("source_entity_id") or "").strip()
            if entity_id and entity_id not in values:
                values.append(entity_id)
    return values


def _source_rcsb_selection(source: ConformationalMappingSource) -> RcsbSelection | None:
    receipt = _read_source_authority(source)
    if not isinstance(receipt, Mapping) or receipt.get("authority_kind") != "rcsb_download":
        return None
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        return None
    selection = payload.get("selection")
    if selection is None:
        return RcsbSelection(accession=str(payload.get("accession") or "").upper())
    try:
        parsed = RcsbSelection.model_validate(selection)
    except (TypeError, ValueError):
        return None
    accession = str(payload.get("accession") or "").upper()
    if parsed.accession is None:
        parsed.accession = accession
    return parsed


def _run_record_selected_input(
    source: ConformationalMappingSource,
    *,
    model_id: str | None,
    sample_id: str | None,
    chain_ids: list[str],
    entity_ids: list[str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source_id": source.source_id,
        "source_kind": source.source_kind,
        "source_label": source.source_id,
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
        selection = _source_rcsb_selection(source)
        if selection is not None:
            model_id = model_id or selection.model_id
            sample_id = sample_id or selection.sample_id
            chain_ids = chain_ids or list(selection.chain_ids)
            entity_ids = entity_ids or list(selection.entity_ids)
    if model_id:
        record["model_id"] = model_id
    if sample_id:
        record["sample_id"] = sample_id
    if chain_ids:
        record["chain_ids"] = chain_ids
    if entity_ids:
        record["entity_ids"] = entity_ids
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
    image_name = os.environ.get("BMS_CM_CONFORNETS_CONTAINER_PATH", "").strip()
    configured_image = (
        Path(image_name)
        if image_name
        else get_container_dir() / "confornets-canonical.sif"
    )
    if configured_image.is_symlink():
        raise HTTPException(
            status_code=503, detail="canonical ConforNets image selector may not be a symlink"
        )
    try:
        image = configured_image.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503, detail="canonical ConforNets image is not installed"
        ) from exc
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
    if managed_checkpoint is not None and managed_checkpoint in session.new:
        await session.commit()
    rows = (
        await session.execute(
            select(ConformationalMappingSource).where(
                ConformationalMappingSource.immutable.is_(True),
                or_(
                    ConformationalMappingSource.principal_id == principal_id,
                    ConformationalMappingSource.source_id == managed_checkpoint_source_id,
                ),
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


_REUSABLE_CM_ARTIFACT_ROLES = frozenset({"authoritative_cif"})
_REUSABLE_CM_MEDIA_TYPES = frozenset({"chemical/x-mmcif", "chemical/mmcif"})


def _verified_reusable_artifact(job: Job, artifact: ConformationalMappingArtifact) -> bool:
    if (
        artifact.role not in _REUSABLE_CM_ARTIFACT_ROLES
        or artifact.media_type not in _REUSABLE_CM_MEDIA_TYPES
        or not job.output_dir
    ):
        return False
    try:
        descriptor = _open_verified_artifact_descriptor(
            storage_path=artifact.storage_path,
            root_path=job.output_dir,
            size_bytes=artifact.size_bytes,
            content_sha256=artifact.content_sha256,
        )
    except (OSError, ValueError, HTTPException):
        return False
    os.close(descriptor)
    return True


@router.get("/runs")
async def list_reusable_runs(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Discover only caller-owned completed runs with reusable verified mmCIF bytes."""
    principal_id = _principal(request)
    rows = (await session.execute(
        select(ConformationalMappingRequest, Job, ConformationalMappingArtifact)
        .join(Job, Job.id == ConformationalMappingRequest.job_id)
        .join(
            ConformationalMappingArtifact,
            ConformationalMappingArtifact.request_id == ConformationalMappingRequest.request_id,
        )
        .where(
            ConformationalMappingRequest.principal_id == principal_id,
            ConformationalMappingRequest.status == "completed",
            Job.status == "completed",
            ConformationalMappingArtifact.role.in_(_REUSABLE_CM_ARTIFACT_ROLES),
        )
        .order_by(ConformationalMappingRequest.created_at.desc(), ConformationalMappingArtifact.artifact_id)
    )).all()
    grouped: dict[str, dict[str, Any]] = {}
    for record, job, artifact in rows:
        if not _verified_reusable_artifact(job, artifact):
            continue
        run = grouped.setdefault(record.request_id, {
            "request_id": record.request_id,
            "job_id": job.id,
            "workflow": "conformational_mapping",
            "name": job.name,
            "status": "completed",
            "backend": record.backend,
            "completed_at": record.terminal_at.isoformat() + "Z" if record.terminal_at else None,
            "artifacts": [],
        })
        run["artifacts"].append({
            "artifact_id": artifact.artifact_id,
            "candidate_id": artifact.candidate_id,
            "name": artifact.candidate_id or artifact.artifact_id,
            "role": artifact.role,
            "artifact_type": artifact.role,
            "format": "mmcif",
            "media_type": artifact.media_type,
            "sha256": artifact.content_sha256,
            "bytes": artifact.size_bytes,
            "available": True,
            "backend_coordinates": (artifact.metadata_json or {}).get("backend_coordinates"),
        })
    return {"runs": list(grouped.values())}


@router.post("/runs/{request_id}/artifacts/{artifact_id}/sources", status_code=201)
async def register_reusable_run_artifact(
    request_id: str,
    artifact_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Issue a source handle bound to one explicitly selected completed-run artifact."""
    record = await _authorized_record(request_id, request, session, mutation=True)
    job = await session.get(Job, record.job_id)
    artifact = await session.get(ConformationalMappingArtifact, artifact_id)
    if (
        job is None
        or record.status != "completed"
        or job.status != "completed"
        or artifact is None
        or artifact.request_id != request_id
        or not _verified_reusable_artifact(job, artifact)
    ):
        raise HTTPException(status_code=409, detail="selected completed-run artifact is unavailable")
    principal_id = _principal(request)
    binding = canonical_sha256({
        "principal_id": principal_id,
        "request_id": request_id,
        "artifact_id": artifact_id,
        "content_sha256": artifact.content_sha256,
    })
    source_id = f"cm_src_{binding[:48]}"
    existing = await session.get(ConformationalMappingSource, source_id)
    if existing is not None:
        authority = _read_source_authority(existing)
        authority_payload = authority.get("payload") if isinstance(authority, Mapping) else None
        if (
            existing.principal_id != principal_id
            or existing.source_kind != "structure_artifact"
            or existing.content_sha256 != artifact.content_sha256
            or existing.size_bytes != artifact.size_bytes
            or not existing.immutable
            or not isinstance(authority, Mapping)
            or authority.get("authority_kind") != "completed_run_artifact"
            or not isinstance(authority_payload, Mapping)
            or authority_payload.get("request_id") != request_id
            or authority_payload.get("artifact_id") != artifact_id
            or authority_payload.get("content_sha256") != artifact.content_sha256
        ):
            raise HTTPException(status_code=409, detail="run artifact source authority is unavailable")
        return {"source_id": existing.source_id, "source_kind": existing.source_kind,
                "sha256": existing.content_sha256, "bytes": existing.size_bytes,
                "format": _registered_source_format(existing.relative_path),
                "metadata": existing.metadata_json, "authority_receipt": authority}
    try:
        root = Path(job.output_dir).resolve(strict=True)
        path = Path(artifact.storage_path).resolve(strict=True)
        relative = path.relative_to(root).as_posix()
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="selected completed-run artifact is outside its job authority") from exc
    source = ConformationalMappingSource(
        source_id=source_id,
        principal_id=principal_id,
        source_kind="structure_artifact",
        storage_root=str(root),
        relative_path=relative,
        content_sha256=artifact.content_sha256,
        size_bytes=artifact.size_bytes,
        metadata_json={
            "name": f"{job.name}: {artifact.candidate_id or artifact.artifact_id}",
            "producer_backend": record.backend,
            "candidate_id": artifact.candidate_id,
            "backend_coordinates": (artifact.metadata_json or {}).get("backend_coordinates"),
        },
        immutable=True,
        created_at=datetime.utcnow(),
    )
    authority_path = _source_authority_path(source_id)
    try:
        session.add(source)
        await session.flush()
        authority = _publish_source_authority(
            source,
            authority_kind="completed_run_artifact",
            payload={
                "request_id": request_id,
                "job_id": job.id,
                "artifact_id": artifact.artifact_id,
                "candidate_id": artifact.candidate_id,
                "content_sha256": artifact.content_sha256,
                "backend_coordinates": (artifact.metadata_json or {}).get("backend_coordinates"),
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        authority_path.unlink(missing_ok=True)
        raise
    return {"source_id": source_id, "source_kind": "structure_artifact",
            "sha256": artifact.content_sha256, "bytes": artifact.size_bytes,
            "format": "mmcif", "metadata": source.metadata_json,
            "authority_receipt": authority}


def _remove_source_publication(destination_dir: Path, destination: Path | None = None) -> None:
    """Remove only the publication directory created by one source attempt."""

    if destination is not None:
        destination.unlink(missing_ok=True)
    if destination_dir.is_symlink():
        destination_dir.unlink(missing_ok=True)
    elif destination_dir.exists():
        destination_dir.rmdir()


def _requested_source_authority(request: Request) -> tuple[str, dict[str, Any]] | None:
    value = getattr(request.state, "_cm_source_authority", None)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=500, detail="source authority request is malformed")
    authority_kind = value.get("authority_kind")
    payload = value.get("payload")
    if not isinstance(authority_kind, str) or not isinstance(payload, Mapping):
        raise HTTPException(status_code=500, detail="source authority request is malformed")
    return authority_kind, dict(payload)


def _existing_rcsb_authority_matches(
    existing: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> bool:
    if not isinstance(existing, Mapping) or existing.get("authority_kind") != "rcsb_download":
        return False
    existing_payload = existing.get("payload")
    if not isinstance(existing_payload, Mapping):
        return False
    return (
        existing_payload.get("provider") == "RCSB"
        and str(existing_payload.get("accession") or "").upper()
        == str(payload.get("accession") or "").upper()
        and existing_payload.get("selection", {}) == payload.get("selection", {})
        and existing_payload.get("source_sha256") == payload.get("source_sha256")
        and existing_payload.get("download_sha256") == payload.get("download_sha256")
        and existing_payload.get("materialization") == payload.get("materialization")
    )


async def _register_source_impl(
    *,
    request: Request,
    source_kind: str,
    metadata_json: str,
    file: UploadFile,
    session: AsyncSession,
) -> dict[str, Any]:
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
    requested_authority = _requested_source_authority(request)
    registry = get_data_root() / "conformational_mapping_sources"
    registry.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = registry / f".upload-{uuid.uuid4()}"
    destination_dir: Path | None = None
    destination: Path | None = None
    authority_path: Path | None = None
    authority_published = False
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o440,
        )
        try:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _SOURCE_MAX_BYTES[source_kind]:
                    raise HTTPException(status_code=413, detail="registered source exceeds its server limit")
                digest.update(chunk)
                _write_all(descriptor, chunk)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if size == 0:
            raise HTTPException(status_code=422, detail="registered source is empty")
        content_sha256 = digest.hexdigest()
        owner_tag = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:8]
        source_id = f"cm_src_{owner_tag}_{source_kind[:10]}_{content_sha256[:32]}"
        existing = await session.get(ConformationalMappingSource, source_id)
        if existing is not None:
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
            authority_receipt = _read_source_authority(existing)
            if requested_authority is not None:
                authority_kind, authority_payload = requested_authority
                if authority_kind != "rcsb_download" or not _existing_rcsb_authority_matches(
                    authority_receipt, authority_payload
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="registered source bytes already have a different authority",
                    )
            elif source_kind == "complex_snapshot" and authority_receipt is None:
                existing_snapshots = _read_registered_json(existing)
                if isinstance(existing_snapshots, Mapping):
                    existing_snapshots = [existing_snapshots]
                if not isinstance(existing_snapshots, list) or not existing_snapshots:
                    raise HTTPException(status_code=409, detail="registered complex snapshot authority is unavailable")
                for snapshot in existing_snapshots:
                    validate_schema("cm_complex_snapshot_v1", snapshot)
                authority_receipt = _publish_source_authority(
                    existing,
                    authority_kind="complex_snapshot_normalization",
                    payload={"chain_ids": _snapshot_chain_ids(existing_snapshots)},
                )
            return {
                "source_id": source_id, "source_kind": source_kind,
                "format": _registered_source_format(existing.relative_path),
                "sha256": content_sha256, "bytes": size, "metadata": existing.metadata_json,
                "authority_receipt": authority_receipt,
            }

        destination_dir = registry / source_id
        try:
            destination_dir.mkdir(mode=0o750)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="registered source publication raced or is ambiguous") from exc
        source_suffix = _validated_source_suffix(source_kind, file.filename or "")
        destination = destination_dir / f"content{source_suffix}"
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        directory_fd = os.open(destination_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

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
                raise HTTPException(status_code=422, detail=f"invalid complex snapshot: {exc}") from exc
        elif source_kind == "protein_sequence":
            sequence = "".join(destination.read_text(encoding="utf-8").split()).upper()
            if not sequence or any(value not in "ACDEFGHIKLMNPQRSTVWY" for value in sequence):
                raise HTTPException(status_code=422, detail="protein sequence source is invalid")
            metadata = {**metadata, "sequence": sequence, "target_id": str(metadata.get("target_id") or source_id)}

        source_record = ConformationalMappingSource(
            source_id=source_id, principal_id=principal_id, source_kind=source_kind,
            storage_root=str(registry), relative_path=f"{source_id}/{destination.name}",
            content_sha256=content_sha256, size_bytes=size, metadata_json=metadata,
            immutable=True, created_at=datetime.utcnow(),
        )
        authority_spec = requested_authority
        if authority_spec is None and source_kind == "complex_snapshot":
            authority_spec = (
                "complex_snapshot_normalization",
                {"chain_ids": _snapshot_chain_ids(snapshots)},
            )
        session.add(source_record)
        if authority_spec is not None:
            authority_path = _source_authority_path(source_id)
            authority_receipt = _publish_source_authority(
                source_record,
                authority_kind=authority_spec[0],
                payload=authority_spec[1],
            )
            authority_published = True
        else:
            authority_receipt = None
        destination_dir.chmod(0o550)
        await session.commit()
        return {
            "source_id": source_id, "source_kind": source_kind,
            "format": _registered_source_format(destination.name),
            "sha256": content_sha256, "bytes": size, "metadata": metadata,
            "authority_receipt": authority_receipt,
        }
    except Exception:
        try:
            await session.rollback()
        finally:
            if authority_path is not None and (authority_published or authority_path.exists()):
                authority_path.unlink(missing_ok=True)
            if destination_dir is not None and destination_dir.exists():
                _remove_source_publication(destination_dir, destination)
        raise
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/sources")
async def register_source(
    request: Request, source_kind: str = Form(...), metadata_json: str = Form("{}"),
    file: UploadFile = File(...), session: AsyncSession = Depends(get_session),
):
    return await _register_source_impl(
        request=request,
        source_kind=source_kind,
        metadata_json=metadata_json,
        file=file,
        session=session,
    )


def _rcsb_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)


async def _download_rcsb_mmcif(accession: str) -> bytes:
    """Download one deposited asymmetric-unit mmCIF under the CM source limit."""

    maximum_bytes = _SOURCE_MAX_BYTES["structure_upload"]
    spool = tempfile.SpooledTemporaryFile(
        max_size=min(maximum_bytes, 8 * 1024 * 1024), mode="w+b"
    )
    prefix = bytearray()
    size = 0
    try:
        try:
            async with _rcsb_http_client() as client:
                async with client.stream(
                    "GET", f"https://files.rcsb.org/download/{accession}.cif"
                ) as response:
                    if response.status_code == 404:
                        raise HTTPException(status_code=404, detail="RCSB accession was not found")
                    if response.status_code != 200:
                        raise HTTPException(
                            status_code=502,
                            detail="RCSB mmCIF download returned an unexpected status",
                        )
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > maximum_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail="RCSB mmCIF exceeds the registered-source limit",
                            )
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
        return spool.read(maximum_bytes + 1)
    finally:
        spool.close()


@router.post("/sources/rcsb/{pdb_id}")
async def register_rcsb_mmcif_source(
    pdb_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    selection: RcsbSelection | None = None,
):
    """Register exact server-resolved RCSB model/chain/entity mmCIF bytes."""

    accession = pdb_id.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", accession):
        raise HTTPException(status_code=422, detail="RCSB accession must be exactly four letters or digits")
    if selection is None:
        selection = RcsbSelection(accession=accession)
    elif selection.accession and selection.accession != accession:
        raise HTTPException(status_code=422, detail="RCSB selection accession does not match the URL accession")
    else:
        selection = selection.model_copy(update={"accession": accession})

    downloaded = await _download_rcsb_mmcif(accession)
    try:
        discovery, resolved_selection, materialized = resolve_and_materialize_rcsb_selection(
            accession,
            downloaded,
            selection.model_dump(mode="json", exclude_none=True),
        )
    except RcsbSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source_sha256 = hashlib.sha256(materialized).hexdigest()
    authority_payload = {
        "provider": "RCSB",
        "accession": accession,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "selection": resolved_selection,
        "source_sha256": source_sha256,
        "download_sha256": hashlib.sha256(downloaded).hexdigest(),
        "materialization": "selected_asymmetric_unit_context_v1",
    }
    metadata = {
        "name": f"RCSB {accession}",
        "rcsb_entry": {
            "accession": accession,
            "title": f"RCSB entry {accession}",
            **discovery,
        },
        **resolved_selection,
    }
    spool = tempfile.SpooledTemporaryFile(
        max_size=min(len(materialized), 8 * 1024 * 1024), mode="w+b"
    )
    try:
        spool.write(materialized)
        spool.seek(0)
        upload = UploadFile(filename=f"{accession}.cif", file=cast(BinaryIO, spool))
        request.state._cm_source_authority = {
            "authority_kind": "rcsb_download",
            "payload": authority_payload,
        }
        try:
            registration = await register_source(
                request=request,
                source_kind="structure_upload",
                metadata_json=json.dumps(metadata),
                file=upload,
                session=session,
            )
        finally:
            try:
                del request.state._cm_source_authority
            except AttributeError:
                pass
        source = await session.get(ConformationalMappingSource, registration["source_id"])
        if source is None:
            raise HTTPException(status_code=500, detail="registered RCSB source authority is unavailable")
        authority_receipt = registration.get("authority_receipt") or _read_source_authority(source)
        # Compatibility wrappers may bypass the normal transactional publisher.
        if authority_receipt is None:
            authority_receipt = _publish_source_authority(
                source, authority_kind="rcsb_download", payload=authority_payload
            )
        return {**registration, "authority_receipt": authority_receipt}
    finally:
        spool.close()


def _rcsb_human_metadata(accession: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    value = payload if isinstance(payload, Mapping) else {}
    struct_value = value.get("struct")
    struct: Mapping[str, Any] = struct_value if isinstance(struct_value, Mapping) else {}
    entry_info_value = value.get("rcsb_entry_info")
    entry_info: Mapping[str, Any] = (
        entry_info_value if isinstance(entry_info_value, Mapping) else {}
    )
    accession_info_value = value.get("rcsb_accession_info")
    accession_info: Mapping[str, Any] = (
        accession_info_value if isinstance(accession_info_value, Mapping) else {}
    )
    experimental = value.get("exptl")
    methods = [
        str(item.get("method"))
        for item in experimental
        if isinstance(item, Mapping) and item.get("method")
    ] if isinstance(experimental, list) else []
    resolution_values = entry_info.get("resolution_combined")
    resolution = (
        resolution_values[0]
        if isinstance(resolution_values, list) and resolution_values
        else resolution_values
    )
    return {
        "accession": accession,
        "title": str(struct.get("title") or value.get("name") or f"RCSB entry {accession}"),
        "experimental_methods": methods,
        "resolution": resolution,
        "deposition_date": accession_info.get("deposit_date"),
        "entity_count": entry_info.get("polymer_entity_count"),
        "assembly_count": entry_info.get("assembly_count"),
    }


async def _rcsb_entry_metadata(accession: str) -> dict[str, Any]:
    try:
        async with _rcsb_http_client() as client:
            response = await client.get(f"https://data.rcsb.org/rest/v1/core/entry/{accession}")
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="RCSB metadata request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="RCSB metadata request failed") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="RCSB accession was not found")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="RCSB metadata returned an unexpected status")
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="RCSB metadata was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=502, detail="RCSB metadata was not an object")
    downloaded = await _download_rcsb_mmcif(accession)
    try:
        discovery = discover_rcsb_contexts(accession, downloaded)
    except RcsbSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**_rcsb_human_metadata(accession, payload), **discovery}


async def _cached_rcsb_entries(
    principal_id: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(ConformationalMappingSource).where(
            ConformationalMappingSource.principal_id == principal_id,
            ConformationalMappingSource.source_kind == "structure_upload",
            ConformationalMappingSource.immutable.is_(True),
        ).order_by(ConformationalMappingSource.created_at.desc())
    )).scalars().all()
    entries: list[dict[str, Any]] = []
    for source in rows:
        receipt = _read_source_authority(source)
        if not isinstance(receipt, Mapping) or receipt.get("authority_kind") != "rcsb_download":
            continue
        payload = receipt.get("payload")
        if not isinstance(payload, Mapping):
            continue
        accession = str(payload.get("accession") or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", accession):
            continue
        stored_entry = source.metadata_json.get("rcsb_entry")
        entry = _rcsb_human_metadata(accession, source.metadata_json)
        if isinstance(stored_entry, Mapping):
            entry.update(stored_entry)
        entry.update({
            "source_id": source.source_id,
            "source_kind": source.source_kind,
            "sha256": source.content_sha256,
            "bytes": source.size_bytes,
            "cached": True,
            "selection": payload.get("selection", {}),
        })
        entries.append(entry)
    return entries


@router.get("/sources/rcsb/cached")
async def list_cached_rcsb_sources(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {"entries": await _cached_rcsb_entries(_principal(request), session)}


@router.get("/sources/rcsb/search")
async def search_rcsb_sources(
    request: Request,
    keyword: str | None = Query(default=None, min_length=2, max_length=200),
    accession: str | None = Query(default=None, min_length=4, max_length=4),
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal_id = _principal(request)
    normalized_accession = accession.strip().upper() if accession else None
    if normalized_accession and not re.fullmatch(r"[A-Z0-9]{4}", normalized_accession):
        raise HTTPException(status_code=422, detail="RCSB accession must be exactly four letters or digits")
    normalized_keyword = keyword.strip() if keyword else None
    if not normalized_accession and not normalized_keyword:
        raise HTTPException(status_code=422, detail="RCSB accession or keyword is required")
    cached = await _cached_rcsb_entries(principal_id, session)
    if normalized_accession:
        cached_match = [entry for entry in cached if entry["accession"] == normalized_accession]
        if cached_match:
            return {"query": normalized_accession, "entries": cached_match, "cached": True}
        entry = await _rcsb_entry_metadata(normalized_accession)
        return {"query": normalized_accession, "entries": [entry], "cached": False}
    lowered = normalized_keyword.casefold()
    cached_matches = [
        entry for entry in cached
        if lowered in str(entry.get("title") or "").casefold()
        or lowered in str(entry.get("accession") or "").casefold()
    ]
    if cached_matches:
        return {"query": normalized_keyword, "entries": cached_matches[:limit], "cached": True}
    search_payload = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": normalized_keyword},
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": limit}},
    }
    try:
        async with _rcsb_http_client() as client:
            response = await client.post(
                "https://search.rcsb.org/rcsbsearch/v2/query", json=search_payload
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="RCSB search request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="RCSB search request failed") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="RCSB search returned an unexpected status")
    try:
        result_payload = response.json()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="RCSB search was not valid JSON") from exc
    result_set = result_payload.get("result_set") if isinstance(result_payload, Mapping) else None
    if not isinstance(result_set, list):
        raise HTTPException(status_code=502, detail="RCSB search result set is invalid")
    entries: list[dict[str, Any]] = []
    for item in result_set[:limit]:
        if not isinstance(item, Mapping):
            continue
        candidate = str(item.get("identifier") or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", candidate):
            continue
        try:
            metadata = await _rcsb_entry_metadata(candidate)
        except HTTPException as exc:
            if exc.status_code == 422:
                continue
            raise
        metadata["score"] = item.get("score")
        entries.append(metadata)
    return {"query": normalized_keyword, "entries": entries, "cached": False}


def _principal(request: Request) -> str:
    """Resolve an authenticated caller or the configured trusted application proxy."""
    if not _authorization_enabled():
        return _PERSONAL_WORKFLOW_PRINCIPAL
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        configured = os.getenv("BMS_CM_TRUSTED_PROXY_SECRET", "")
        supplied = request.headers.get(_APPLICATION_PROXY_HEADER, "")
        if configured and supplied and secrets.compare_digest(configured, supplied):
            return "local-application-operator"
        raise HTTPException(
            status_code=401,
            detail="authenticated conformational-mapping principal required",
        )
    if isinstance(principal, Mapping):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    normalized_roles = {str(role).strip().lower() for role in roles}
    if not actor or not normalized_roles.intersection({"scientist", "operator", "admin"}):
        raise HTTPException(
            status_code=403,
            detail="conformational-mapping scientist/operator role required",
        )
    return str(actor)


def _mutation_principal(request: Request) -> str:
    return _principal(request)


def _capability_cookie_name(request_id: str) -> str:
    return f"{_COOKIE_PREFIX}{request_id.replace('-', '_')}"


async def _authorized_record(
    request_id: str,
    request: Request,
    session: AsyncSession,
    *,
    mutation: bool = False,
):
    record = await get_request(session, request_id)
    if record is None:
        _principal(request)
        raise HTTPException(status_code=404, detail="conformational-mapping request not found")
    if mutation:
        principal_id = _principal(request)
        if record.principal_id != principal_id:
            raise HTTPException(status_code=404, detail="conformational-mapping request not found")
        return record
    principal = getattr(request.state, "authenticated_principal", None)
    supplied = ""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied:
        supplied = request.cookies.get(_capability_cookie_name(request_id), "")
    progress = record.progress_json or {}
    expected = str(
        progress.get("request_capability_sha256")
        or progress.get("capability_sha256")
        or ""
    )
    capability_matches_record = bool(
        expected
        and supplied
        and secrets.compare_digest(
            expected, hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        )
    )
    principal_id = record.principal_id if principal is None and capability_matches_record else _principal(request)
    if record.principal_id != principal_id:
        if not capability_matches_record:
            raise HTTPException(status_code=404, detail="conformational-mapping request not found")
    return record


async def _authorized_state_analysis_summary_record(
    request_id: str,
    request: Request,
    session: AsyncSession,
):
    """Apply the same owner or capability authority as every CM read route."""

    return await _authorized_record(request_id, request, session)


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


def _validate_rcsb_selection_against_snapshots(
    source: ConformationalMappingSource,
    snapshots: Sequence[Mapping[str, Any]],
) -> RcsbSelection | None:
    selection = _source_rcsb_selection(source)
    if selection is None:
        return None
    model_ids = {
        str(snapshot.get("source_model_id") or "").strip()
        for snapshot in snapshots
        if str(snapshot.get("source_model_id") or "").strip()
    }
    chain_ids = set(_snapshot_chain_ids(snapshots))
    entity_ids = set(_snapshot_entity_ids(snapshots))
    if selection.model_id and selection.model_id not in model_ids:
        raise HTTPException(status_code=422, detail="selected RCSB model is not present in the staged source")
    if selection.chain_ids and set(selection.chain_ids) != chain_ids:
        raise HTTPException(
            status_code=422,
            detail="selected RCSB chains do not match the complete staged source context",
        )
    if selection.entity_ids and set(selection.entity_ids) != entity_ids:
        raise HTTPException(
            status_code=422,
            detail="selected RCSB entities do not match the complete staged source context",
        )
    return selection


def _remove_request_root(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.exists():
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
    if "frustrampnn_settings" in body.model_fields_set:
        params["frustrampnn_settings"] = body.frustrampnn_settings.model_dump(
            mode="json", exclude_none=False
        )
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
            rcsb_selection = _validate_rcsb_selection_against_snapshots(
                import_sources[0], import_snapshots
            )
            selected_model_id = str(normalized_snapshot.get("source_model_id") or "") or None
            selected_sample_id = rcsb_selection.sample_id if rcsb_selection else None
            selected_chain_ids = (
                list(rcsb_selection.chain_ids)
                if rcsb_selection and rcsb_selection.chain_ids
                else _snapshot_chain_ids(import_snapshots)
            )
            selected_entity_ids = (
                list(rcsb_selection.entity_ids)
                if rcsb_selection and rcsb_selection.entity_ids
                else _snapshot_entity_ids(import_snapshots)
            )
            request_payload, coordinate_plan = bind_materialized_source_snapshot(
                materialized,
                source_snapshot_sha256=canonical_sha256(normalized_snapshot),
                selected_input=_run_record_selected_input(
                    import_sources[0],
                    model_id=selected_model_id,
                    sample_id=selected_sample_id,
                    chain_ids=selected_chain_ids,
                    entity_ids=selected_entity_ids,
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
        retry_authority = _build_retry_authority(root)
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
                "cm_retry_authority_v1": retry_authority,
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
            key=_capability_cookie_name(request_id), value=token,
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
    except Exception as exc:
        # A database, runtime-registry, or materializer failure must not leave
        # a directory that looks resumable without its durable request row.
        await session.rollback()
        _remove_request_root(root)
        logging.getLogger(__name__).exception("CM request materialization failed")
        raise HTTPException(status_code=500, detail="conformational-mapping request could not be materialized") from exc


def _project_request_state(
    record: ConformationalMappingRequest,
    job: Job | None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Project one read-only status view from the request and its current job."""

    status = str(record.status or "prepared")
    progress = dict(record.progress_json or {})
    failure_receipt = record.failure_receipt_json if status == "failed" else None
    if status == "completed":
        # A historical failure receipt is audit data, not the current state.
        return status, progress, None
    if job is None:
        return status, progress, failure_receipt

    job_state = str(job.status or job.queue_status or "").lower()
    if job_state == "running" and status in {"prepared", "queued", "running"}:
        status = "running"
        progress["phase"] = "running"
    elif job_state == "queued" and status == "prepared":
        status = "queued"
        progress["phase"] = "queued"
    elif job_state in {"failed", "cancelled"} and status not in {"failed", "cancelled"}:
        status = job_state
        progress["phase"] = job_state
        failure_receipt = None
        if job_state == "failed":
            failure_receipt = {
                "schema_name": "cm_failure_receipt", "schema_version": 1,
                "request_id": record.request_id, "job_id": job.id,
                "terminal_state": job_state,
                "message": str(job.error_message or "canonical job failed"),
                "recorded_at": datetime.utcnow().isoformat() + "Z",
            }
    current_stage = getattr(job, "current_stage", None)
    if current_stage:
        progress["job_stage"] = current_stage
    stage_progress = getattr(job, "stage_progress", None)
    if stage_progress is not None:
        progress["job_progress"] = stage_progress
    return status, progress, failure_receipt


@router.get("/requests/{request_id}")
async def request_status(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session)
    job = await session.get(Job, record.job_id)
    status, progress, failure_receipt = _project_request_state(record, job)
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
    status, progress, _failure_receipt = _project_request_state(record, job)
    return {
        "request_id": request_id, "status": status,
        "progress": progress,
        "job_stage": progress.get("job_stage"),
        "job_progress": progress.get("job_progress"),
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
        retry_authority = _build_retry_authority(root)
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
            provenance={
                "cm_pair_id": pair["pair_id"],
                "cm_handoff_key": body.handoff_key,
                "cm_retry_authority_v1": retry_authority,
            },
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
    except Exception as exc:
        await session.rollback()
        if root_created and root is not None:
            _remove_request_root(root)
        logging.getLogger(__name__).exception("CM resampling materialization failed")
        raise HTTPException(status_code=500, detail="conformational-mapping resampling could not be materialized") from exc


@router.post("/requests/{request_id}/cancel")
async def cancel_request(
    request_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    record = await _authorized_record(request_id, request, session, mutation=True)
    if record.status not in {"prepared", "queued", "running"}:
        raise HTTPException(status_code=409, detail="request is not cancellable")
    try:
        async def persist_cancellation_intent() -> None:
            progress = dict(record.progress_json or {})
            progress.update({"phase": "cancellation_requested"})
            record.progress_json = progress
            record.updated_at = datetime.utcnow()
            await session.flush()

        async def persist_terminal_cancellation() -> None:
            await transition_request(
                session,
                record,
                status="cancelled",
                progress={"phase": "cancelled"},
                flush=False,
            )

        await cancel_job_lineage(
            record.job_id,
            session,
            error_message="Cancelled through typed CM API",
            commit=True,
            before_intent_commit=persist_cancellation_intent,
            before_terminal_commit=persist_terminal_cancellation,
        )
    except Exception:
        await session.rollback()
        raise
    return {"request_id": request_id, "status": "cancelled"}


def _clean_retry_launch_params(
    params: Mapping[str, Any], *, attempt_root: Path
) -> dict[str, Any]:
    """Build clean-attempt launch parameters without accepting old work state."""

    cleaned = dict(params)
    for key in (
        "resume_work_dir",
        "work_dir",
        "nextflow_work_dir",
        "nextflow_run_id",
        "resume",
    ):
        cleaned.pop(key, None)
    cleaned["cm_request_path"] = str(attempt_root / "cm_request_v1.json")
    return cleaned


def _verified_retry_documents(record: ConformationalMappingRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    request_payload = dict(record.request_json or {})
    coordinate_plan = dict(record.coordinate_plan_json or {})
    try:
        validate_materialized_coordinate_plan(request_payload, coordinate_plan)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail="persisted CM retry authority is not schema-valid") from exc
    if request_payload.get("request_id") != record.request_id:
        raise HTTPException(status_code=409, detail="persisted CM request identity does not match its record")
    if request_payload.get("backend") != record.backend:
        raise HTTPException(status_code=409, detail="persisted CM backend identity does not match its record")
    request_digest = canonical_sha256(
        {key: value for key, value in request_payload.items() if key != "request_sha256"}
    )
    plan_digest = canonical_sha256(
        {key: value for key, value in coordinate_plan.items() if key != "coordinate_plan_sha256"}
    )
    if request_digest != record.request_sha256 or request_payload.get("request_sha256") != request_digest:
        raise HTTPException(status_code=409, detail="persisted CM request authority is not byte-valid")
    if (
        plan_digest != record.coordinate_plan_sha256
        or coordinate_plan.get("coordinate_plan_sha256") != plan_digest
        or coordinate_plan.get("request_sha256") != request_digest
    ):
        raise HTTPException(status_code=409, detail="persisted CM coordinate plan authority is not byte-valid")
    return request_payload, coordinate_plan


_RETRY_REQUIRED_SIDECARS = frozenset({
    "cm_runtime_registry_v1.json",
    "cm_complex_snapshots_v1.json",
})
_RETRY_OPTIONAL_SIDECARS = frozenset({"cm_resampling_pair_request_v1.json"})
_RETRY_REGISTERED_DIRECTORIES = frozenset({
    "registered_snapshot",
    "registered",
    "registered_import",
})


def _stable_file_identity(path: Path) -> tuple[str, int]:
    """Hash one no-follow regular file while proving its opened identity stayed stable."""

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("retry authority entry is not a regular file")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or size != before.st_size:
            raise OSError("retry authority entry changed while hashing")
        return digest.hexdigest(), size
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _retry_authority_paths(root: Path) -> dict[str, Path]:
    """Enumerate only pre-launch sidecars and registered source bytes."""

    paths: dict[str, Path] = {}
    for name in sorted(_RETRY_REQUIRED_SIDECARS | _RETRY_OPTIONAL_SIDECARS):
        path = root / name
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise OSError(f"unsafe retry authority sidecar: {name}")
            paths[name] = path
    if not _RETRY_REQUIRED_SIDECARS.issubset(paths):
        raise OSError("required retry authority sidecars are unavailable")
    for name in sorted(_RETRY_REGISTERED_DIRECTORIES):
        directory = root / name
        if not directory.exists() and not directory.is_symlink():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise OSError(f"unsafe retry authority directory: {name}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise OSError(f"unsafe retry authority path: {path}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise OSError(f"retry authority path is not a regular file: {path}")
            relative_path = path.relative_to(root).as_posix()
            paths[relative_path] = path
    return paths


def _build_retry_authority(root: Path) -> dict[str, Any]:
    """Seal every retry-copied sidecar/source byte before the first launch."""

    try:
        files = {
            relative_path: {"sha256": digest, "size_bytes": size}
            for relative_path, path in _retry_authority_paths(root).items()
            for digest, size in [_stable_file_identity(path)]
        }
    except OSError as exc:
        raise HTTPException(status_code=409, detail="persisted CM retry authority could not be sealed") from exc
    unsigned = {
        "schema_name": "cm_retry_authority",
        "schema_version": 1,
        "files": files,
    }
    return {**unsigned, "authority_sha256": canonical_sha256(unsigned)}


def _validated_retry_authority(authority: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(authority, Mapping) or set(authority) != {
        "schema_name", "schema_version", "files", "authority_sha256",
    }:
        raise HTTPException(status_code=409, detail="persisted CM retry authority is unavailable")
    unsigned = {key: value for key, value in authority.items() if key != "authority_sha256"}
    if (
        authority.get("schema_name") != "cm_retry_authority"
        or authority.get("schema_version") != 1
        or authority.get("authority_sha256") != canonical_sha256(unsigned)
        or not isinstance(authority.get("files"), Mapping)
    ):
        raise HTTPException(status_code=409, detail="persisted CM retry authority is invalid")
    files: dict[str, dict[str, Any]] = {}
    for raw_relative_path, raw_identity in authority["files"].items():
        relative_path = str(raw_relative_path)
        path = Path(relative_path)
        if (
            not relative_path
            or path.is_absolute()
            or "\\" in relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
            or not isinstance(raw_identity, Mapping)
            or set(raw_identity) != {"sha256", "size_bytes"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(raw_identity.get("sha256") or ""))
            or not isinstance(raw_identity.get("size_bytes"), int)
            or raw_identity["size_bytes"] < 0
        ):
            raise HTTPException(status_code=409, detail="persisted CM retry authority contains an invalid file identity")
        if (
            relative_path not in _RETRY_REQUIRED_SIDECARS | _RETRY_OPTIONAL_SIDECARS
            and path.parts[0] not in _RETRY_REGISTERED_DIRECTORIES
        ):
            raise HTTPException(status_code=409, detail="persisted CM retry authority contains an ungoverned path")
        files[relative_path] = {
            "sha256": str(raw_identity["sha256"]),
            "size_bytes": int(raw_identity["size_bytes"]),
        }
    if not _RETRY_REQUIRED_SIDECARS.issubset(files):
        raise HTTPException(status_code=409, detail="persisted CM retry authority is missing required sidecars")
    return files


def _copy_verified_retry_file(source: Path, destination: Path, identity: Mapping[str, Any]) -> None:
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != identity["size_bytes"]:
            raise OSError("retry authority file size changed")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            _write_all(destination_descriptor, chunk)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or size != identity["size_bytes"] or digest.hexdigest() != identity["sha256"]:
            raise OSError("retry authority file digest changed")
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)


def _copy_clean_retry_authority(
    *,
    source_root: Path,
    attempt_root: Path,
    request_payload: Mapping[str, Any],
    coordinate_plan: Mapping[str, Any],
    persisted_authority: Mapping[str, Any] | None,
) -> None:
    if attempt_root.exists():
        raise HTTPException(status_code=409, detail="clean retry attempt root already exists")
    if source_root.is_symlink() or not source_root.is_dir():
        raise HTTPException(status_code=409, detail="clean retry authority root is unavailable")
    source_root = source_root.resolve(strict=True)
    request_path = source_root / "cm_request_v1.json"
    plan_path = source_root / "cm_coordinate_plan_v1.json"
    if (
        request_path.is_symlink() or not request_path.is_file()
        or plan_path.is_symlink() or not plan_path.is_file()
    ):
        raise HTTPException(status_code=409, detail="clean retry authority documents are unavailable")
    try:
        on_disk_request = json.loads(request_path.read_text(encoding="utf-8"))
        on_disk_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="clean retry authority documents are unreadable") from exc
    if on_disk_request != request_payload or on_disk_plan != coordinate_plan:
        raise HTTPException(status_code=409, detail="retry input files disagree with persisted CM authority")

    expected_files = _validated_retry_authority(persisted_authority)
    try:
        source_files = _retry_authority_paths(source_root)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="persisted CM retry authority contains unsafe source paths") from exc
    if set(source_files) != set(expected_files):
        raise HTTPException(status_code=409, detail="persisted CM retry authority does not match registered source files")

    attempt_root.mkdir(parents=True, mode=0o700)
    try:
        # Reconstruct the two schema-governed documents from their already verified
        # persisted rows instead of reusing mutable source-path bytes after the check.
        (attempt_root / "cm_request_v1.json").write_text(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        (attempt_root / "cm_coordinate_plan_v1.json").write_text(
            json.dumps(coordinate_plan, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        for relative_path, source in source_files.items():
            _copy_verified_retry_file(
                source,
                attempt_root / relative_path,
                expected_files[relative_path],
            )
        try:
            runtime_registry = json.loads(
                (attempt_root / "cm_runtime_registry_v1.json").read_text(encoding="utf-8")
            )
            snapshots = json.loads(
                (attempt_root / "cm_complex_snapshots_v1.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail="persisted CM retry authority sidecars are unreadable") from exc
        if not isinstance(runtime_registry, Mapping):
            raise HTTPException(status_code=409, detail="persisted CM retry authority runtime registry is invalid")
        snapshot_bundle = snapshots if isinstance(snapshots, list) else [snapshots]
        if not snapshot_bundle or any(not isinstance(snapshot, Mapping) for snapshot in snapshot_bundle):
            raise HTTPException(status_code=409, detail="persisted CM retry authority snapshot bundle is invalid")
        try:
            for snapshot in snapshot_bundle:
                validate_schema("cm_complex_snapshot_v1", snapshot)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail="persisted CM retry authority snapshot bundle is not schema-valid") from exc
    except OSError as exc:
        shutil.rmtree(attempt_root, ignore_errors=True)
        raise HTTPException(status_code=409, detail="persisted CM retry authority byte identity is unavailable") from exc
    except Exception:
        shutil.rmtree(attempt_root, ignore_errors=True)
        raise


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
    job_state = str(job.status or job.queue_status or "").lower()
    if record.status not in {"failed", "cancelled"} and job_state not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="request is not retry eligible")
    retry_count = int(job.retry_count or 0) + 1
    if retry_count > int(getattr(job, "max_retries", 2) or 2):
        raise HTTPException(status_code=409, detail="request retry limit is exhausted")
    request_payload, coordinate_plan = _verified_retry_documents(record)
    attempt_job_id = str(uuid.uuid4())
    attempt_root = get_results_dir() / f"conformational_mapping_{request_id}_retry_{retry_count}_{attempt_job_id}"
    attempt_root_created = False
    try:
        source_root = _resolve_artifact_runtime_alias(str(job.output_dir))
        _copy_clean_retry_authority(
            source_root=source_root,
            attempt_root=attempt_root,
            request_payload=request_payload,
            coordinate_plan=coordinate_plan,
            persisted_authority=(job.provenance or {}).get("cm_retry_authority_v1"),
        )
        attempt_root_created = True
        retry_params = _clean_retry_launch_params(dict(job.params or {}), attempt_root=attempt_root)
        provenance = dict(job.provenance or {})
        provenance.update(
            {
                "cm_retry_parent_job_id": job.id,
                "cm_retry_attempt": retry_count,
                "cm_request_sha256": record.request_sha256,
                "cm_coordinate_plan_sha256": record.coordinate_plan_sha256,
            }
        )
        retry_job = Job(
            id=attempt_job_id,
            name=job.name,
            status="queued",
            model_id=job.model_id,
            mode=job.mode,
            params=retry_params,
            output_dir=str(attempt_root),
            queue_status="queued",
            batch_id=job.batch_id,
            batch_name=job.batch_name,
            lineage_root_job_id=job.lineage_root_job_id or job.id,
            parent_job_id=job.id,
            source_stage_job_id=job.id,
            source_stage_family=job.stage_family,
            stage_family=job.stage_family,
            stage_mode=job.stage_mode,
            retry_count=retry_count,
            max_retries=job.max_retries,
            oom_tolerance=job.oom_tolerance,
            sequence_length=job.sequence_length,
            vram_estimate_mb=job.vram_estimate_mb,
            provenance=provenance,
        )
        if record.status not in {"failed", "cancelled"}:
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
        session.add(retry_job)
        await session.flush()
        record.job_id = retry_job.id
        await transition_request(
            session,
            record,
            status="queued",
            progress={"phase": "queued", "completed_coordinates": 0, "retry_attempt": retry_count},
            flush=False,
        )
        await session.commit()
    except HTTPException:
        await session.rollback()
        if attempt_root_created:
            _remove_request_root(attempt_root)
        raise
    except Exception as exc:
        await session.rollback()
        if attempt_root_created:
            _remove_request_root(attempt_root)
        logging.getLogger(__name__).exception("CM clean retry materialization failed")
        raise HTTPException(status_code=500, detail="conformational-mapping retry could not be materialized") from exc
    return {
        "request_id": request_id,
        "job_id": retry_job.id,
        "status": "queued",
        "retry_count": retry_count,
        "parent_job_id": job.id,
    }


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

    await _authorized_state_analysis_summary_record(request_id, request, session)
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

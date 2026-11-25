"""Normalize authorized conformational-mapping submissions before staging."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
from typing import Any, Mapping, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, model_validator
from services.conformational_mapping.request_builder import ConformationalMappingRequestError, validate_request_controls
from database import ConformationalMappingSource
from fastapi import HTTPException, Request
from services.conformational_mapping.import_stager import ImportStagingError, RegisteredArtifact, read_registered_artifact
from services.conformational_mapping.import_snapshot import MAX_IMPORT_MMCIF_BYTES
from services.workflow_request_types import NativeWorkflowDependencyRequest, SubmitRequest
from pathlib import Path
from services.conformational_mapping.contracts import canonical_sha256
from dataclasses import dataclass
from datetime import datetime
from paths import get_container_dir, get_data_root, get_weights_root
from scripts.lib.runtime_image_lifecycle import load_state, object_path
from scripts.lib.shared_runtime_images import verify_image


_PERSONAL_WORKFLOW_PRINCIPAL = "local-personal-workflow"


_APPLICATION_PROXY_HEADER = "X-BMS-CM-Proxy-Secret"


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


_SOURCE_MAX_BYTES = {
    "complex_snapshot": 64 * 1024 * 1024,
    "protein_sequence": 16 * 1024 * 1024,
    "confornets_config": 16 * 1024 * 1024,
    "structure_upload": MAX_IMPORT_MMCIF_BYTES,
    "structure_artifact": MAX_IMPORT_MMCIF_BYTES,
    "confornets_checkpoint": 2 * 1024 * 1024 * 1024,
    "confornets_state": 2 * 1024 * 1024 * 1024,
}


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


def _registered_image_digest(selector: str) -> str:
    """Select approved release bytes, never promote an arbitrary path hash.

    The existing shared-store release manifests (including their legacy env
    projection) are the only installation authority. Explicit selectors may pin
    a retained release; defaults select the current lane. This is read-only:
    discovery neither publishes images nor upgrades historical references.
    """
    root = Path(os.environ.get("BMS_RUNTIME_IMAGE_STORE", "").strip()
                or get_container_dir() / ".image-store")
    configured = os.environ.get(selector, "").strip()
    try:
        if not root.is_absolute():
            raise ValueError("runtime image store must be absolute")
        state = load_state(root)
        if configured:
            candidates = [release["images"][selector]
                          for release in state["releases"].values()
                          if selector in release["images"]
                          and release["images"][selector]["path"] == configured]
            if not candidates:
                raise ValueError("configured image is not a retained managed reference")
            image = candidates[0]
        else:
            lane = os.environ.get("BMS_RUNTIME_IMAGE_LANE", "production")
            image = state["releases"][state["current"][lane]]["images"][selector]
        path = object_path(root, image["sha256"])
        if str(path) != image["path"]:
            raise ValueError("managed image path differs from shared store")
        return verify_image(path, image["sha256"])["sha256"]
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=f"registered runtime image is unavailable: {selector}") from exc


def _server_confornets_identity() -> dict[str, str]:
    digest = _registered_image_digest("BMS_CM_CONFORNETS_CONTAINER_PATH")
    return {
        "backend_version": "canonical-4df561a",
        "backend_commit": "4df561a1fbd0fd2b9c7a230fa62957a837d9f72d",
        "runtime_identity": "confornets-canonical-write-ledger-v1",
        "container_digest": f"sha256:{digest}",
        "model_id": "confornets-canonical-v1",
        "feature_identity_sha256": canonical_sha256({"coordinate_emission": "write_time_v1"}),
        "repo_path": "/opt/confornets",
    }


_confornets_discovery_digest: tuple[tuple[str, int, int, int, int, int], str] | None = None


def _confornets_checkpoint_observation(
    checkpoint: Path,
) -> tuple[str, int, int, int, int, int] | None:
    try:
        observed = checkpoint.lstat()
        fields = (observed.st_dev, observed.st_ino, observed.st_size,
                  observed.st_mtime_ns, observed.st_ctime_ns)
        if not stat.S_ISREG(observed.st_mode) or any(type(value) is not int for value in fields):
            return None
        return (str(checkpoint), *fields)
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def _confornets_discovery_checksum(checkpoint: Path) -> tuple[str, int]:
    global _confornets_discovery_digest
    before = _confornets_checkpoint_observation(checkpoint)
    if before is not None and _confornets_discovery_digest is not None:
        observed, digest = _confornets_discovery_digest
        if before == observed:
            return digest, before[3]
    _confornets_discovery_digest = None
    digest = _sha256_path(checkpoint)
    size = checkpoint.stat().st_size
    after = _confornets_checkpoint_observation(checkpoint)
    # Uncertainty disables reuse, not discovery: keep the existing single read.
    if before is not None and before == after:
        _confornets_discovery_digest = (before, digest)
    return digest, size


async def _read_managed_confornets_checkpoint(
    session: AsyncSession,
    *,
    reuse_discovery_checksum: bool = False,
) -> ConformationalMappingSource | None:
    """Expose one server-owned checkpoint identity without accepting a host path."""
    global _confornets_discovery_digest
    checkpoint = get_weights_root() / "openfold3" / "of3-p2-155k.pt"
    checkpoint = checkpoint.resolve(strict=False)
    if not checkpoint.is_file() or not checkpoint.is_relative_to(get_weights_root().resolve()):
        if reuse_discovery_checksum:
            _confornets_discovery_digest = None
        return None
    if reuse_discovery_checksum:
        digest, size = _confornets_discovery_checksum(checkpoint)
    else:
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
    return managed


async def _managed_checkpoint_for_submission(
    session: AsyncSession,
    requested_source_id: str | None,
) -> ConformationalMappingSource:
    managed = await _read_managed_confornets_checkpoint(session)
    if managed is None:
        raise HTTPException(status_code=503, detail="installed managed checkpoint is unavailable")
    if requested_source_id != managed.source_id:
        raise HTTPException(
            status_code=422,
            detail="ConforNets requires the installed managed checkpoint",
        )
    return managed


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


@dataclass
class _NormalizedCmSubmission:
    params: dict[str, Any]
    request_fields: dict[str, Any]
    import_sources: list[ConformationalMappingSource]
    confor_sources: list[ConformationalMappingSource]
    snapshot_source: ConformationalMappingSource | None
    sequence_source: ConformationalMappingSource | None
    snapshots: list[Any]
    checkpoint_source: ConformationalMappingSource | None
    references: list[ConformationalMappingSource]
    config_source: ConformationalMappingSource | None
    transfer_source: ConformationalMappingSource | None


async def _normalize_cm_submission(
    body: SubmitRequest, principal_id: str, session: AsyncSession,
) -> _NormalizedCmSubmission:
    """Shared read-only controls/source ACL binding before submission writes."""
    checkpoint_source = config_source = transfer_source = None
    references: list[ConformationalMappingSource] = []
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

    try:
        validated = validate_request_controls(params)
    except ConformationalMappingRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _NormalizedCmSubmission(
        params, validated.request_fields, import_sources, confor_sources,
        snapshot_source, sequence_source, snapshots, checkpoint_source, references,
        config_source, transfer_source,
    )


async def normalize_cm_provision_request(
    body: SubmitRequest, request: Request, session: AsyncSession,
) -> NativeWorkflowDependencyRequest:
    """Normalize the real unsaved CM submission without registering/staging it."""
    principal_id = _mutation_principal(request)
    with session.no_autoflush:
        normalized = await _normalize_cm_submission(body, principal_id, session)
    effective = normalized.request_fields
    sources = [*normalized.import_sources, *normalized.confor_sources]
    if normalized.snapshot_source is not None:
        sources.append(normalized.snapshot_source)
    bindings = tuple({
        "role": source.source_kind,
        "source_ref": {"kind": "cm_registered_source", "id": source.source_id},
        "sha256": source.content_sha256,
        "size_bytes": source.size_bytes,
        "required": True,
        "state": "declared",
    } for source in sources)
    return NativeWorkflowDependencyRequest(
        model_id="conformational_mapping", mode="map",
        requested_params=body.model_dump(mode="json"),
        effective_params={"cm_request": copy.deepcopy(effective)},
        entrypoint="workflows/conformational_mapping.nf",
        input_bindings=bindings,
    )


CM_VRAM_ESTIMATE_MB = {
    "external_import": 12_000,
    "confornets": 16_000,
    "protenix_v2_ensemble": 24_000,
}

RETRY_REQUIRED_SIDECARS = frozenset({
    "cm_runtime_registry_v1.json",
    "cm_complex_snapshots_v1.json",
})
RETRY_OPTIONAL_SIDECARS = frozenset({"cm_resampling_pair_request_v1.json"})
RETRY_REGISTERED_DIRECTORIES = frozenset({
    "registered_snapshot",
    "registered",
    "registered_import",
})


def cm_job_admission(backend: str, request_payload: Mapping[str, Any]) -> dict[str, int]:
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
    from native_components import cm_gpu_requirements
    requirements = cm_gpu_requirements(dict(request_payload))
    return {
        "vram_estimate_mb": requirements.get("gpu_memory_mb") or CM_VRAM_ESTIMATE_MB[backend],
        "sequence_length": sequence_length or 300,
    }


def runtime_registry(backend: str) -> dict[str, Any]:
    from services.frustrampnn import runtime as frustrampnn_runtime
    try:
        analysis_runtime = frustrampnn_runtime.cm_analysis_runtime_registry_v1(
            get_container_dir()
        )
    except frustrampnn_runtime.RuntimeValidationError as exc:
        raise HTTPException(
            status_code=503, detail="registered FrustraMPNN runtime is unavailable"
        ) from exc
    if backend == "confornets":
        return {
            "schema_name": "cm_runtime_registry", "schema_version": 1,
            "analysis_runtime": analysis_runtime, **_server_confornets_identity(),
        }
    if backend == "protenix_v2_ensemble":
        digest = _registered_image_digest("BMS_PROTENIX_CONTAINER_PATH")
        checkpoint = get_weights_root() / "protenix" / "checkpoint" / "protenix-v2.pt"
        if not checkpoint.is_file() or checkpoint.is_symlink():
            raise HTTPException(status_code=503, detail="registered Protenix runtime is unavailable")
        return {
            "schema_name": "cm_runtime_registry", "schema_version": 1,
            "backend_version": "protenix-v2", "backend_commit": "c3bfc365b3e1341a11935eddfe7bfdc308092147",
            "runtime_identity": "installed-protenix-v2", "model_id": "protenix-v2",
            "container_digest": f"sha256:{digest}",
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


def stable_file_identity(path: Path) -> tuple[str, int]:
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


def retry_authority_paths(root: Path) -> dict[str, Path]:
    """Enumerate only pre-launch sidecars and registered source bytes."""

    paths: dict[str, Path] = {}
    for name in sorted(RETRY_REQUIRED_SIDECARS | RETRY_OPTIONAL_SIDECARS):
        path = root / name
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise OSError(f"unsafe retry authority sidecar: {name}")
            paths[name] = path
    if not RETRY_REQUIRED_SIDECARS.issubset(paths):
        raise OSError("required retry authority sidecars are unavailable")
    for name in sorted(RETRY_REGISTERED_DIRECTORIES):
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


def build_retry_authority(root: Path) -> dict[str, Any]:
    """Seal every retry-copied sidecar/source byte before the first launch."""

    try:
        files = {
            relative_path: {"sha256": digest, "size_bytes": size}
            for relative_path, path in retry_authority_paths(root).items()
            for digest, size in [stable_file_identity(path)]
        }
    except OSError as exc:
        raise HTTPException(status_code=409, detail="persisted CM retry authority could not be sealed") from exc
    unsigned = {
        "schema_name": "cm_retry_authority",
        "schema_version": 1,
        "files": files,
    }
    return {**unsigned, "authority_sha256": canonical_sha256(unsigned)}

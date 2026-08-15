"""Governed ONT raw-signal representations and capability selection.

Paths remain server-side. Public functions return opaque representation metadata.
Conversion stays fail-closed until the exact local fidelity profile is qualified.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    InputFile,
    OntInstrumentRun,
    OntInstrumentRunEvent,
    OntRawSignalDerivationEvent,
    OntRawSignalDerivationJob,
    OntRawSignalLookup,
    OntRawSignalRepresentation,
)


RepresentationPreference = Literal["auto", "pod5", "blow5"]
BLOW5_PROFILE_ID = "bms.blow5.partitioned-zstd-svb-zd.v2"
EXTERNAL_BLOW5_VALIDATION_PROFILE_ID = "bms.blow5.external-validation.v1"
BLOW5_CONTAINER_ENV = "BMS_ONT_SLOW5TOOLS_IMAGE"
BLOW5_CONTAINER_DIGEST_ENV = "BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST"
BLOW5_CONTAINER_RUNTIME_ENV = "BMS_ONT_CONTAINER_RUNTIME"
BLOW5_CONVERSION_ENABLED_ENV = "BMS_ONT_BLOW5_CONVERSION_QUALIFIED"
BLOW5_STAGING_ROOT_ENV = "BMS_ONT_RAW_SIGNAL_STAGING_ROOT"
BLOW5_MIN_FREE_BYTES_ENV = "BMS_ONT_RAW_SIGNAL_MIN_FREE_BYTES"
BLOW5_ACQUISITION_PRESSURE_ENV = "BMS_ONT_RAW_SIGNAL_ACQUISITION_PRESSURE"
EXTERNAL_POD5_ROOT_ENV = "BMS_ONT_EXTERNAL_POD5_ROOT"
BLOW5_DEFAULT_STAGING_ROOT = "/mnt/BioModStack/ont-raw-signal-staging"
BLOW5_DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024

_RAW_FORMATS = frozenset({"pod5", "slow5", "blow5"})
_TERMINAL_STATES = frozenset({"stopped", "completed", "failed"})
_READY = "ready"
_PREPARABLE = "preparable"
_UNAVAILABLE = "unavailable"
RAW_SIGNAL_MAX_WAVEFORM_SAMPLES = 20_000


def _external_pod5_root() -> Path:
    configured = os.getenv(EXTERNAL_POD5_ROOT_ENV, "").strip()
    if not configured:
        raise RuntimeError(f"{EXTERNAL_POD5_ROOT_ENV} is not configured")
    root = Path(configured).expanduser().resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("external POD5 root must be a real directory")
    return root


def list_external_pod5_candidates() -> list[dict[str, Any]]:
    root = _external_pod5_root()
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.pod5")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        info = path.stat()
        candidates.append({
            "candidate_id": hashlib.sha256(relative.encode("utf-8")).hexdigest(),
            "display_name": relative,
            "size_bytes": info.st_size,
            "modified_at_ns": info.st_mtime_ns,
        })
    return candidates


def resolve_external_pod5_candidate(candidate_id: str) -> Path:
    if not _is_sha256(candidate_id):
        raise KeyError("external POD5 candidate was not found")
    root = _external_pod5_root()
    for candidate in list_external_pod5_candidates():
        if secrets.compare_digest(candidate["candidate_id"], candidate_id):
            path = (root / candidate["display_name"]).resolve(strict=True)
            if path.parent != root and root not in path.parents:
                break
            return path
    raise KeyError("external POD5 candidate was not found")


def _now() -> datetime:
    return datetime.utcnow()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _public_representation(record: OntRawSignalRepresentation) -> dict[str, Any]:
    manifest = record.artifact_manifest if isinstance(record.artifact_manifest, dict) else {}
    raw_artifacts = manifest.get("artifacts")
    artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
    return {
        "representation_id": record.id,
        "run_id": record.run_id,
        "observed_generation": record.observed_generation,
        "role": record.role,
        "source_kind": record.source_kind,
        "format": record.format,
        "source_fidelity": record.source_fidelity,
        "state": record.state,
        "reason_code": record.reason_code,
        "manifest_sha256": record.manifest_sha256,
        "artifact_count": len(artifacts),
        "read_count": record.read_count,
        "profile_id": record.profile_id,
        "compression": dict(record.compression or {}),
        "parent_representation_ids": list(record.parent_representation_ids or []),
        "validation": {
            "source_identity_closed": bool((record.validation_receipts or {}).get("source_preflight")),
            "adjacent_index_validated": bool((record.validation_receipts or {}).get("adjacent_index")),
            "semantic_contract_validated": bool((record.validation_receipts or {}).get("semantic")),
        },
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "created_at": record.created_at.isoformat(),
    }


async def _exact_generation(session: AsyncSession, run_id: str, observed_generation: int) -> tuple[OntInstrumentRun, OntInstrumentRunEvent]:
    if isinstance(observed_generation, bool) or observed_generation < 1:
        raise ValueError("observed_generation must be a positive integer")
    run = await session.get(OntInstrumentRun, run_id)
    if run is None:
        raise KeyError(run_id)
    event = (
        await session.execute(
            select(OntInstrumentRunEvent).where(
                OntInstrumentRunEvent.run_id == run_id,
                OntInstrumentRunEvent.observed_generation == observed_generation,
            )
        )
    ).scalar_one_or_none()
    if event is None:
        raise KeyError(f"{run_id}/{observed_generation}")
    return run, event


def _sealed_manifest(run: OntInstrumentRun, observed_generation: int) -> dict[str, Any]:
    manifest = run.terminal_artifact_manifest
    digest = run.terminal_artifact_manifest_sha256
    if run.state not in _TERMINAL_STATES or not isinstance(manifest, dict) or not _is_sha256(digest):
        raise ValueError("raw-signal representations require a sealed terminal generation")
    if manifest.get("run_id") != run.id or manifest.get("observed_generation") != observed_generation or _digest(manifest) != digest:
        raise ValueError("sealed terminal manifest does not bind the exact requested generation")
    return manifest


def _require_sealed_generation(run: OntInstrumentRun, event: OntInstrumentRunEvent) -> None:
    if event.state not in _TERMINAL_STATES:
        raise ValueError("raw-signal registration requires a sealed terminal generation")
    marker = run.last_minknow_payload if isinstance(run.last_minknow_payload, dict) else {}
    if marker.get("schema") == "bms.ont.external-raw-signal-registration.v1":
        if run.minknow_run_id is not None or event.observed_generation != run.observed_generation:
            raise ValueError("external raw-signal generation identity is invalid")
        return
    _sealed_manifest(run, event.observed_generation)


async def register_native_pod5_generation(session: AsyncSession, *, run_id: str, observed_generation: int) -> list[dict[str, Any]]:
    """Register immutable MinKNOW POD5 shards from the sealed acquisition manifest."""
    run, _event = await _exact_generation(session, run_id, observed_generation)
    manifest = _sealed_manifest(run, observed_generation)
    artifacts = [dict(item) for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("kind") == "pod5"]
    if not artifacts:
        return []
    source_manifest = {
        "schema": "bms.ont.raw-signal-artifacts.v1",
        "run_id": run_id,
        "observed_generation": observed_generation,
        "format": "pod5",
        "source_terminal_manifest_sha256": run.terminal_artifact_manifest_sha256,
        "artifacts": artifacts,
    }
    manifest_sha256 = _digest(source_manifest)
    existing = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.observed_generation == observed_generation,
                OntRawSignalRepresentation.manifest_sha256 == manifest_sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = OntRawSignalRepresentation(
            id=_id("ont-raw-rep"),
            run_id=run_id,
            observed_generation=observed_generation,
            role="source",
            source_kind="minknow_native",
            format="pod5",
            source_fidelity="native_acquisition_evidence",
            state=_UNAVAILABLE,
            reason_code="pod5_acquisition_identity_validation_required",
            artifact_manifest=source_manifest,
            manifest_sha256=manifest_sha256,
            parent_representation_ids=[],
            parent_manifest_sha256s=[],
            compression={},
            runtime_identity={},
            validation_receipts={"terminal_manifest_sha256": run.terminal_artifact_manifest_sha256},
            acquisition_id=(run.last_minknow_payload or {}).get("acquisition_id") if isinstance(run.last_minknow_payload, dict) else None,
            retention_pinned_at=_now(),
            created_at=_now(),
        )
        session.add(existing)
        await session.flush()
    if existing.acquisition_id:
        await request_blow5_derivation(
            session,
            run_id=run_id,
            observed_generation=observed_generation,
            source_representation_id=existing.id,
            consumer_id="ont-terminal-reconciliation",
            preference="auto",
            automatic=True,
        )
    return [_public_representation(existing)]


def _resolve_input_file(record: InputFile, expected_format: str) -> Path:
    suffixes = {"pod5": (".pod5",), "slow5": (".slow5",), "blow5": (".blow5",)}[expected_format]
    candidate = (Path(record.directory) / record.filename).expanduser().resolve(strict=True)
    if not candidate.name.lower().endswith(suffixes) or not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"input file is not a regular {expected_format.upper()} artifact")
    return candidate


def _file_artifact(path: Path, artifact_id: str, *, kind: str) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("raw-signal artifact must be a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        before_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if before_identity != after_identity:
            raise ValueError("raw-signal source changed while it was being registered")
        return {"artifact_id": artifact_id, "kind": kind, "bytes": info.st_size, "sha256": digest.hexdigest(), "path": str(path)}
    finally:
        os.close(fd)


async def register_external_source(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    format: str,
    input_file_id: str,
    index_input_file_id: str | None,
    source_fidelity: str,
) -> dict[str, Any]:
    """Register format-native external evidence using opaque tracked input IDs."""
    normalized_format = str(format).lower()
    if normalized_format not in _RAW_FORMATS:
        raise ValueError("format must be pod5, slow5, or blow5")
    if source_fidelity not in {"unknown", "native", "known_degraded", "verified_exact_samples"}:
        raise ValueError("unsupported source_fidelity")
    run, event = await _exact_generation(session, run_id, observed_generation)
    _require_sealed_generation(run, event)
    source = await session.get(InputFile, input_file_id)
    if source is None:
        raise KeyError(input_file_id)
    source_path = _resolve_input_file(source, normalized_format)
    artifacts = [_file_artifact(source_path, source.id, kind=normalized_format)]
    validation_receipts: dict[str, Any] = {}
    state = _UNAVAILABLE
    reason = "source_validation_required"
    if normalized_format == "blow5":
        if not index_input_file_id:
            raise ValueError("BLOW5 registration requires an adjacent tracked .idx input")
        index_record = await session.get(InputFile, index_input_file_id)
        if index_record is None:
            raise KeyError(index_input_file_id)
        index_path = (Path(index_record.directory) / index_record.filename).expanduser().resolve(strict=True)
        if index_path != Path(f"{source_path}.idx") or not index_path.is_file() or index_path.is_symlink():
            raise ValueError("BLOW5 index must be the adjacent <artifact>.blow5.idx tracked input")
        artifacts.append(_file_artifact(index_path, index_record.id, kind="blow5_index"))
        validation_receipts["adjacent_index"] = True
        reason = "blow5_index_semantic_validation_required"
    manifest = {
        "schema": "bms.ont.raw-signal-artifacts.v1",
        "run_id": run_id,
        "observed_generation": observed_generation,
        "format": normalized_format,
        "external_native": True,
        "artifacts": artifacts,
    }
    manifest_sha256 = _digest(manifest)
    existing = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.observed_generation == observed_generation,
                OntRawSignalRepresentation.manifest_sha256 == manifest_sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = OntRawSignalRepresentation(
            id=_id("ont-raw-rep"), run_id=run_id, observed_generation=observed_generation,
            role="source", source_kind="external_native", format=normalized_format,
            source_fidelity=source_fidelity, state=state, reason_code=reason,
            artifact_manifest=manifest, manifest_sha256=manifest_sha256,
            parent_representation_ids=[], parent_manifest_sha256s=[], compression={},
            runtime_identity={}, validation_receipts=validation_receipts,
            acquisition_id=None, retention_pinned_at=_now(), created_at=_now(),
        )
        session.add(existing)
        await session.flush()
    return _public_representation(existing)


async def create_external_run_registration(
    session: AsyncSession,
    *,
    format: str,
    input_file_id: str,
    index_input_file_id: str | None,
    source_fidelity: str,
    sample_id: str | None,
    experiment_group: str | None,
) -> dict[str, Any]:
    """Create one sealed external generation without MinKNOW or POD5 ancestry."""
    now = _now()
    run_id = _id("ont-external-run")
    marker = {
        "schema": "bms.ont.external-raw-signal-registration.v1",
        "format": format,
        "input_file_id": input_file_id,
        "index_input_file_id": index_input_file_id,
    }
    input_record = await session.get(InputFile, input_file_id)
    if input_record is None:
        raise KeyError(input_file_id)
    source_path = _resolve_input_file(input_record, format)
    source_artifact = _file_artifact(source_path, f"{run_id}:source", kind=format)
    terminal_manifest = {
        "schema": "bms.ont.instrument-terminal-artifacts.v1",
        "schema_version": 1,
        "run_id": run_id,
        "minknow_run_id_sha256": hashlib.sha256(b"").hexdigest(),
        "terminal_state": "completed",
        "observed_generation": 1,
        "artifacts": [{
            "kind": format,
            "path": str(source_path),
            "bytes": source_artifact["bytes"],
            "sha256": source_artifact["sha256"],
        }],
    }
    run = OntInstrumentRun(
        id=run_id,
        position_id="external",
        minknow_run_id=None,
        state="completed",
        observed_at=now,
        observed_generation=1,
        sample_id=sample_id,
        experiment_group=experiment_group,
        kit=None,
        output_directories={},
        output_files={"fastq": [], "pod5": [], "bam": []},
        handoff_ready=False,
        last_minknow_payload=marker,
        terminal_artifact_manifest=terminal_manifest,
        terminal_artifact_manifest_sha256=_digest(terminal_manifest),
        created_at=now,
    )
    event = OntInstrumentRunEvent(
        id=_id("ont-event"),
        run_id=run_id,
        event_type="external_raw_signal_registered",
        state="completed",
        observed_at=now,
        observed_generation=1,
        minknow_payload=marker,
        output_files={"fastq": [], "pod5": [], "bam": []},
    )
    session.add_all((run, event))
    await session.flush()
    representation = await register_external_source(
        session,
        run_id=run_id,
        observed_generation=1,
        format=format,
        input_file_id=input_file_id,
        index_input_file_id=index_input_file_id,
        source_fidelity=source_fidelity,
    )
    return {"run_id": run_id, "observed_generation": 1, "representation": representation}


async def register_external_pod5_candidate(
    session: AsyncSession,
    *,
    candidate_id: str,
    sample_id: str | None,
    experiment_group: str,
) -> dict[str, Any]:
    """Immutably register one server-governed POD5 candidate as a sealed run."""
    if not experiment_group.strip():
        raise ValueError("exact Domain Experiment ID is required")
    source_path = resolve_external_pod5_candidate(candidate_id)
    probe = _file_artifact(source_path, "candidate", kind="pod5")
    input_file_id = str(uuid5(NAMESPACE_URL, f"bms:external-pod5:{probe['sha256']}"))
    tracked = await session.get(InputFile, input_file_id)
    if tracked is None:
        tracked = InputFile(
            id=input_file_id,
            filename=source_path.name,
            file_type="pod5",
            directory=str(source_path.parent),
            size_bytes=probe["bytes"],
        )
        session.add(tracked)
        await session.flush()

    runs = list((await session.execute(
        select(OntInstrumentRun).where(OntInstrumentRun.position_id == "external")
    )).scalars())
    for run in runs:
        marker = run.last_minknow_payload if isinstance(run.last_minknow_payload, dict) else {}
        if marker.get("input_file_id") != input_file_id or run.experiment_group != experiment_group:
            continue
        representations = await list_representations(
            session,
            run_id=run.id,
            observed_generation=run.observed_generation,
        )
        if representations:
            return {
                "run_id": run.id,
                "observed_generation": run.observed_generation,
                "representation": representations[0],
                "already_registered": True,
            }

    result = await create_external_run_registration(
        session,
        format="pod5",
        input_file_id=input_file_id,
        index_input_file_id=None,
        source_fidelity="native",
        sample_id=sample_id,
        experiment_group=experiment_group,
    )
    representation = await session.get(
        OntRawSignalRepresentation,
        result["representation"]["representation_id"],
    )
    manifest = representation.artifact_manifest if representation is not None else {}
    artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    if not artifacts or artifacts[0].get("sha256") != probe["sha256"]:
        raise ValueError("raw-signal source changed between intake validation steps")
    result["already_registered"] = False
    return result


async def list_representations(session: AsyncSession, *, run_id: str, observed_generation: int) -> list[dict[str, Any]]:
    await _exact_generation(session, run_id, observed_generation)
    records = list((await session.execute(
        select(OntRawSignalRepresentation).where(
            OntRawSignalRepresentation.run_id == run_id,
            OntRawSignalRepresentation.observed_generation == observed_generation,
        ).order_by(OntRawSignalRepresentation.created_at, OntRawSignalRepresentation.id)
    )).scalars())
    return [_public_representation(record) for record in records]


def _mode(state: str, reason: str, representation_id: str | None = None) -> dict[str, Any]:
    return {"state": state, "reason_code": reason, "representation_id": representation_id}


async def capabilities(session: AsyncSession, *, run_id: str, observed_generation: int, preference: RepresentationPreference = "auto") -> dict[str, Any]:
    if preference not in {"auto", "pod5", "blow5"}:
        raise ValueError("representation_preference must be auto, pod5, or blow5")
    reps = await list_representations(session, run_id=run_id, observed_generation=observed_generation)
    ready_pod5 = next((item for item in reps if item["format"] == "pod5" and item["state"] == _READY), None)
    ready_blow5 = next((item for item in reps if item["format"] == "blow5" and item["state"] == _READY and item["validation"]["adjacent_index_validated"]), None)
    any_pod5 = next((item for item in reps if item["format"] == "pod5"), None)
    blow5_state = _mode(_READY, "indexed_blow5_ready", ready_blow5["representation_id"]) if ready_blow5 else (
        _mode(_PREPARABLE, "qualified_conversion_required", any_pod5["representation_id"]) if any_pod5 else _mode(_UNAVAILABLE, "no_pod5_or_indexed_blow5")
    )
    pod5_state = _mode(_READY, "qualified_pod5_ready", ready_pod5["representation_id"]) if ready_pod5 else (
        _mode(_PREPARABLE, "pod5_identity_validation_required", any_pod5["representation_id"]) if any_pod5 else _mode(_UNAVAILABLE, "no_pod5_representation")
    )
    selected = ready_pod5 if preference in {"auto", "pod5"} and ready_pod5 else ready_blow5 if preference in {"auto", "blow5"} else None
    selection_reason = "ready_source_preferred" if selected and selected["role"] == "source" else "ready_requested_representation" if selected else "requested_representation_not_ready"
    return {
        "run_id": run_id,
        "observed_generation": observed_generation,
        "representation_preference": preference,
        "selected_representation_id": selected["representation_id"] if selected else None,
        "selected_format": selected["format"] if selected else None,
        "selection_reason_code": selection_reason,
        "modes": {
            "pod5_direct": pod5_state,
            "blow5_indexed": blow5_state,
            "raw_waveform": _mode(_READY, "indexed_blow5_lookup_ready", ready_blow5["representation_id"]) if ready_blow5 else blow5_state,
            "signal_to_read": _mode(_UNAVAILABLE, "qualified_move_table_mapping_absent"),
            "signal_to_reference": _mode(_UNAVAILABLE, "qualified_reference_signal_mapping_absent"),
            "signal_pileup": _mode(_UNAVAILABLE, "qualified_reference_signal_mapping_absent"),
            "igv": _mode(_UNAVAILABLE, "alignment_readiness_is_independent_and_reported_by_alignment_session"),
        },
        "representations": reps,
    }


def _source_bytes(source: OntRawSignalRepresentation) -> int:
    manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    return sum(
        int(item.get("bytes") or 0)
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == "pod5"
    )


def _derivation_resource_snapshot(
    run: OntInstrumentRun,
    source: OntRawSignalRepresentation,
) -> dict[str, Any]:
    snapshot = _resource_snapshot(_source_bytes(source))
    marker = run.last_minknow_payload if isinstance(run.last_minknow_payload, dict) else {}
    if marker.get("schema") == "bms.ont.external-raw-signal-registration.v1":
        snapshot["active_acquisition_pressure"] = "clear"
        snapshot["acquisition_pressure_source"] = "sealed_external_registration"
    return snapshot


def _resource_snapshot(source_bytes: int) -> dict[str, Any]:
    root = Path(os.getenv(BLOW5_STAGING_ROOT_ENV, BLOW5_DEFAULT_STAGING_ROOT)).expanduser()
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    configured_floor = int(os.getenv(BLOW5_MIN_FREE_BYTES_ENV, str(BLOW5_DEFAULT_MIN_FREE_BYTES)))
    required_free = max(configured_floor, int(source_bytes * 2.5) + 10 * 1024 * 1024 * 1024)
    image = os.getenv(BLOW5_CONTAINER_ENV, "").strip()
    image_digest = os.getenv(BLOW5_CONTAINER_DIGEST_ENV, "").strip()
    container_runtime = os.getenv(BLOW5_CONTAINER_RUNTIME_ENV, "docker").strip()
    enabled = os.getenv(BLOW5_CONVERSION_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}
    return {
        "schema": "bms.ont.raw-signal-resource-snapshot.v1",
        "staging_root": str(root),
        "source_bytes": source_bytes,
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "required_free_bytes": required_free,
        "load_average_1m": os.getloadavg()[0],
        "active_acquisition_pressure": os.getenv(BLOW5_ACQUISITION_PRESSURE_ENV, "unknown").strip().lower(),
        "qualified_conversion_enabled": enabled,
        "container_image": image,
        "container_digest": image_digest,
        "container_runtime": container_runtime,
        "worker_uid": os.getuid(),
        "worker_gid": os.getgid(),
    }


def _container_image_ref(snapshot: dict[str, Any]) -> str:
    image = str(snapshot["container_image"])
    digest = str(snapshot["container_digest"])
    if image.startswith("sha256:"):
        if image != f"sha256:{digest}":
            raise ValueError("local raw-signal image ID does not match its pinned digest")
        return image
    return f"{image}@sha256:{digest}"


def _qualification_gate(snapshot: dict[str, Any]) -> str | None:
    if not snapshot["qualified_conversion_enabled"]:
        return "converter_fidelity_profile_not_qualified"
    if not snapshot["container_image"] or not _is_sha256(snapshot["container_digest"]):
        return "converter_runtime_identity_not_pinned"
    if snapshot["container_runtime"] not in {"docker", "podman"} or shutil.which(snapshot["container_runtime"]) is None:
        return "converter_container_runtime_unavailable"
    if snapshot["disk_free_bytes"] < snapshot["required_free_bytes"]:
        return "conversion_capacity_gate_failed"
    if snapshot["active_acquisition_pressure"] != "clear":
        return "acquisition_pressure_not_proven_clear"
    return None


def _runtime_gate(snapshot: dict[str, Any]) -> str | None:
    if not snapshot["container_image"] or not _is_sha256(snapshot["container_digest"]):
        return "validator_runtime_identity_not_pinned"
    if snapshot["container_runtime"] not in {"docker", "podman"} or shutil.which(snapshot["container_runtime"]) is None:
        return "validator_container_runtime_unavailable"
    if snapshot["disk_free_bytes"] < snapshot["required_free_bytes"]:
        return "validation_capacity_gate_failed"
    return None


def _source_paths(source: OntRawSignalRepresentation) -> list[Path]:
    manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    artifacts = [
        item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == "pod5" and item.get("path")
    ]
    if any(not _is_sha256(item.get("sha256")) or int(item.get("bytes") or 0) < 1 for item in artifacts):
        raise ValueError("POD5 source manifest lacks immutable size and digest authority")
    paths = [Path(str(item["path"])) for item in artifacts]
    if not paths:
        raise ValueError("POD5 source has no governed artifact paths")
    return paths


def _conversion_commands(job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, snapshot: dict[str, Any]) -> dict[str, Any]:
    stage = Path(snapshot["staging_root"]) / job.id / f"attempt-{job.attempt}"
    partitions = stage / "partitions"
    outputs = stage / "outputs"
    inputs = _source_paths(source)
    source_manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    artifact_by_path = {
        str(Path(str(item["path"]))): item
        for item in source_manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == "pod5" and item.get("path")
    }
    image_ref = _container_image_ref(snapshot)
    input_args = [f"/inputs/{position}/{path.name}" for position, path in enumerate(inputs)]
    validator_input_args = [
        argument
        for path, mounted_path in zip(inputs, input_args, strict=True)
        for argument in (
            "--pod5", mounted_path,
            "--expected-sha256", str(artifact_by_path[str(path)]["sha256"]),
            "--expected-size", str(artifact_by_path[str(path)]["bytes"]),
        )
    ]
    base = [
        snapshot["container_runtime"], "run", "--rm", "--network=none", "--read-only",
        f"--user={snapshot['worker_uid']}:{snapshot['worker_gid']}",
        "--cpus=4", "--memory=16g", "--pids-limit=256", "--ulimit", "nofile=512:512",
        "--mount", f"type=bind,src={stage},dst=/stage",
    ]
    # Build mounts without a shell. slow5tools degrade is absent by construction.
    bind_args: list[str] = []
    for position, path in enumerate(inputs):
        bind_args.extend(["--mount", f"type=bind,src={path.parent},dst=/inputs/{position},readonly"])
    common = base + bind_args + [image_ref]
    return {
        "stage": str(stage), "partitions": str(partitions), "outputs": str(outputs),
        "routing": str(stage / "routing.json"),
        "source_receipt": str(stage / "source-preflight-receipt.json"),
        "partition_map": str(stage / "partition-map.csv"),
        "common": common,
        "validator_input_args": validator_input_args,
        "source_preflight": common + [
            "python3", "/opt/bms/ont_raw_signal_validate.py", "source-preflight", *validator_input_args,
            "--expected-acquisition-id", source.acquisition_id or "external-native",
            "--partition-map", "/stage/partition-map.csv",
            "--receipt", "/stage/source-preflight-receipt.json",
        ],
        "partition": common + [
            "pod5", "subset", *input_args, "--table", "/stage/partition-map.csv",
            "--read-id-column", "read_id", "--columns", "group",
            "--output", "/stage/partitions", "--template", "{group}.pod5",
            "--threads", "4", "--missing-ok",
        ],
    }


def conversion_partition_groups(commands: dict[str, Any]) -> list[str]:
    receipt = json.loads(Path(commands["source_receipt"]).read_text(encoding="utf-8"))
    raw_groups = receipt.get("groups")
    if receipt.get("status") != "passed" or not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError("source preflight did not produce a partition authority")
    groups: list[str] = []
    for item in raw_groups:
        fingerprint = item.get("fingerprint") if isinstance(item, dict) else None
        if not isinstance(fingerprint, str) or not _is_sha256(fingerprint) or int(item.get("read_count") or 0) < 1:
            raise ValueError("source preflight produced an invalid run-info partition")
        groups.append(fingerprint)
    if len(groups) != len(set(groups)):
        raise ValueError("source preflight produced duplicate run-info partitions")
    return sorted(groups)


def conversion_unit_commands(commands: dict[str, Any], fingerprint: str) -> dict[str, list[str]]:
    if not _is_sha256(fingerprint):
        raise ValueError("conversion unit requires a run-info fingerprint")
    common = list(commands["common"])
    partition = f"/stage/partitions/{fingerprint}.pod5"
    output = f"/stage/outputs/{fingerprint}.blow5"
    return {
        "convert": common + [
            "blue-crab", "p2s", "-c", "zstd", "-s", "svb-zd", "--iop", "1",
            "--threads", "4", "--batchsize", "1000", partition, "-o", output,
        ],
        "quickcheck": common + ["slow5tools", "quickcheck", output],
        "index_create": common + ["slow5tools", "index", output],
    }


def conversion_semantic_command(commands: dict[str, Any], groups: list[str]) -> list[str]:
    outputs = [
        argument
        for fingerprint in groups
        for argument in (
            "--blow5", f"/stage/outputs/{fingerprint}.blow5",
            "--index", f"/stage/outputs/{fingerprint}.blow5.idx",
        )
    ]
    return list(commands["common"]) + [
        "python3", "/opt/bms/ont_raw_signal_validate.py", "semantic-dataset",
        *list(commands["validator_input_args"]), *outputs,
        "--routing", "/stage/routing.json", "--receipt", "/stage/semantic-receipt.json",
    ]


def _external_blow5_validation_commands(job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, snapshot: dict[str, Any]) -> dict[str, Any]:
    blow5, index = _external_blow5_paths(source)
    stage = Path(snapshot["staging_root"]) / job.id / f"attempt-{job.attempt + 1}"
    image_ref = _container_image_ref(snapshot)
    common = [
        snapshot["container_runtime"], "run", "--rm", "--network=none", "--read-only",
        f"--user={snapshot['worker_uid']}:{snapshot['worker_gid']}",
        "--cpus=1", "--memory=2g", "--pids-limit=64", "--ulimit", "nofile=128:128",
        "--mount", f"type=bind,src={blow5.parent},dst=/input,readonly",
        "--mount", f"type=bind,src={stage},dst=/stage",
        image_ref,
    ]
    return {
        "stage": str(stage), "output": str(blow5), "index": str(index),
        "quickcheck": common + ["slow5tools", "quickcheck", f"/input/{blow5.name}"],
        "semantic_validate": common + [
            "python3", "/opt/bms/ont_raw_signal_validate.py", "external-blow5", "--blow5", f"/input/{blow5.name}",
            "--index", f"/input/{index.name}", "--receipt", "/stage/semantic-receipt.json",
        ],
    }


def _external_blow5_paths(source: OntRawSignalRepresentation) -> tuple[Path, Path]:
    manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    raw_artifacts = manifest.get("artifacts")
    artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
    paths = {str(item.get("kind")): Path(str(item.get("path"))).expanduser().resolve() for item in artifacts if isinstance(item, dict) and item.get("path")}
    blow5, index = paths.get("blow5"), paths.get("blow5_index")
    if blow5 is None or index is None or index != Path(f"{blow5}.idx"):
        raise ValueError("external BLOW5 validation requires an adjacent tracked index")
    return blow5, index


async def complete_external_blow5_validation(
    session: AsyncSession,
    job: OntRawSignalDerivationJob,
    source: OntRawSignalRepresentation,
    commands: dict[str, Any],
) -> OntRawSignalRepresentation:
    receipt_path = Path(commands["stage"]) / "semantic-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "passed" or receipt.get("duplicate_read_ids") not in (0, False):
        raise ValueError("external BLOW5 validation receipt did not pass")
    receipts = dict(source.validation_receipts or {})
    receipts.update({"adjacent_index": True, "semantic": receipt})
    source.validation_receipts = receipts
    source.state = "ready"
    source.reason_code = "external_indexed_blow5_validated"
    source.profile_id = EXTERNAL_BLOW5_VALIDATION_PROFILE_ID
    source.published_at = _now()
    job.output_representation_id = source.id
    await session.commit()
    return source


async def request_blow5_derivation(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    source_representation_id: str,
    consumer_id: str,
    preference: RepresentationPreference,
    automatic: bool = False,
) -> dict[str, Any]:
    if preference not in {"auto", "blow5"}:
        raise ValueError("BLOW5 derivation accepts auto or blow5 preference")
    if not consumer_id or len(consumer_id) > 128:
        raise ValueError("consumer_id must be 1-128 characters")
    run, event = await _exact_generation(session, run_id, observed_generation)
    _require_sealed_generation(run, event)
    source = await session.get(OntRawSignalRepresentation, source_representation_id)
    if source is None or source.run_id != run_id or source.observed_generation != observed_generation or source.format not in {"pod5", "blow5"}:
        raise ValueError("source representation is not POD5 or external BLOW5 for the exact dataset generation")
    validation_only = source.format == "blow5"
    if validation_only and source.source_kind != "external_native":
        raise ValueError("validation-only admission is limited to external BLOW5")
    if source.source_kind == "minknow_native" and not source.acquisition_id:
        raise ValueError("native POD5 acquisition identity is absent from the sealed MinKNOW observation")
    existing = (
        await session.execute(select(OntRawSignalDerivationJob).where(
            OntRawSignalDerivationJob.run_id == run_id,
            OntRawSignalDerivationJob.observed_generation == observed_generation,
            OntRawSignalDerivationJob.source_representation_id == source.id,
            OntRawSignalDerivationJob.profile_id == (EXTERNAL_BLOW5_VALIDATION_PROFILE_ID if validation_only else BLOW5_PROFILE_ID),
        ))
    ).scalar_one_or_none()
    if existing is None:
        snapshot = _derivation_resource_snapshot(run, source)
        gate = _runtime_gate(snapshot) if validation_only else _qualification_gate(snapshot)
        state = "deferred" if gate else "requested"
        existing = OntRawSignalDerivationJob(
            id=_id("ont-raw-job"), run_id=run_id, observed_generation=observed_generation,
            source_representation_id=source.id, requested_preference=preference,
            consumer_id=consumer_id, profile_id=EXTERNAL_BLOW5_VALIDATION_PROFILE_ID if validation_only else BLOW5_PROFILE_ID,
            state=state, reason_code=gate or ("external_blow5_validation_requested" if validation_only else "qualified_conversion_requested"),
            resource_snapshot=snapshot, attempt=0, claim_token=None,
            lease_expires_at=None, stage_receipts={}, created_at=_now(), updated_at=_now(), completed_at=_now() if gate else None,
        )
        event = OntRawSignalDerivationEvent(
            id=_id("ont-raw-event"), job_id=existing.id, state=state,
            reason_code=existing.reason_code,
            receipt={
                "automatic_whole_run_conversion": automatic,
                "operation": "external_validation" if validation_only else "pod5_to_blow5_conversion",
                "profile_id": existing.profile_id,
                "resource_snapshot": snapshot,
            }, created_at=_now(),
        )
        session.add(existing)
        await session.flush()
        session.add(event)
        await session.flush()
    elif existing.state in {"deferred", "failed"}:
        snapshot = _derivation_resource_snapshot(run, source)
        gate = _runtime_gate(snapshot) if validation_only else _qualification_gate(snapshot)
        existing.resource_snapshot = snapshot
        existing.updated_at = _now()
        existing.reason_code = gate or ("external_blow5_validation_requested" if validation_only else "qualified_conversion_requested")
        if gate is None:
            existing.state = "requested"
            existing.completed_at = None
            existing.claim_token = None
            existing.lease_expires_at = None
        else:
            existing.state = "deferred"
        session.add(OntRawSignalDerivationEvent(
            id=_id("ont-raw-event"), job_id=existing.id, state=existing.state,
            reason_code=existing.reason_code, receipt={"explicit_reassessment": True, "resource_snapshot": snapshot}, created_at=_now(),
        ))
        await session.flush()
    return {
        "job_id": existing.id, "run_id": existing.run_id,
        "observed_generation": existing.observed_generation, "state": existing.state,
        "reason_code": existing.reason_code, "profile_id": existing.profile_id,
        "resource_snapshot": dict(existing.resource_snapshot or {}),
    }


async def cancel_derivation(session: AsyncSession, job_id: str) -> dict[str, Any]:
    job = await session.get(OntRawSignalDerivationJob, job_id)
    if job is None:
        raise KeyError(job_id)
    if job.state in {"ready", "failed", "cancelled"}:
        return {"job_id": job.id, "state": job.state, "reason_code": job.reason_code}
    job.cancel_requested_at = _now()
    job.updated_at = _now()
    if job.state in {"requested", "deferred"}:
        job.state = "cancelled"
        job.reason_code = "cancelled_before_execution"
        job.completed_at = _now()
        job.claim_token = None
        job.lease_expires_at = None
        session.add(OntRawSignalDerivationEvent(
            id=_id("ont-raw-event"), job_id=job.id, state="cancelled",
            reason_code=job.reason_code, receipt={"child_started": False}, created_at=_now(),
        ))
    await session.commit()
    return {"job_id": job.id, "state": job.state, "reason_code": job.reason_code, "cancel_requested": True}


def _validated_blow5_paths(
    representation: OntRawSignalRepresentation,
    read_id: str | None = None,
) -> tuple[Path, Path]:
    if representation.format != "blow5" or representation.state != "ready":
        raise ValueError("raw waveform requires a ready BLOW5 representation")
    receipts = representation.validation_receipts if isinstance(representation.validation_receipts, dict) else {}
    if not receipts.get("adjacent_index"):
        raise ValueError("raw waveform requires a validated adjacent BLOW5 index")
    manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
    raw_artifacts = manifest.get("artifacts")
    artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
    blow5_artifacts = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == "blow5" and item.get("path")]
    index_artifacts = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == "blow5_index" and item.get("path")]
    if len(blow5_artifacts) == 1 and len(index_artifacts) == 1:
        blow5 = Path(str(blow5_artifacts[0]["path"])).expanduser().resolve()
        index = Path(str(index_artifacts[0]["path"])).expanduser().resolve()
    else:
        if read_id is None:
            raise ValueError("partitioned BLOW5 lookup requires a read ID")
        routing_artifact = next(
            (item for item in artifacts if isinstance(item, dict) and item.get("kind") == "read_routing" and item.get("path")),
            None,
        )
        if routing_artifact is None:
            raise ValueError("partitioned BLOW5 representation lacks a routing artifact")
        routing_path = Path(str(routing_artifact["path"])).expanduser().resolve(strict=True)
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
        fingerprint = (routing.get("read_to_group") or {}).get(read_id)
        group = (routing.get("groups") or {}).get(fingerprint) if isinstance(fingerprint, str) else None
        if (
            not isinstance(group, dict)
            or not _is_sha256(fingerprint)
            or group.get("blow5") != f"{fingerprint}.blow5"
            or group.get("index") != f"{fingerprint}.blow5.idx"
        ):
            raise KeyError(read_id)
        blow5 = routing_path.parent / "outputs" / f"{fingerprint}.blow5"
        index = routing_path.parent / "outputs" / f"{fingerprint}.blow5.idx"
    if blow5 is None or index is None or index != Path(f"{blow5}.idx"):
        raise ValueError("raw waveform representation lacks an adjacent index")
    if not blow5.is_file() or not index.is_file():
        raise ValueError("raw waveform representation artifacts are unavailable")
    return blow5, index


async def request_waveform_lookup(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    representation_id: str,
    read_id: str,
) -> dict[str, Any]:
    if not read_id or len(read_id) > 128 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_." for character in read_id):
        raise ValueError("read_id must be a bounded ONT identifier")
    representation = await session.get(OntRawSignalRepresentation, representation_id)
    if representation is None or representation.run_id != run_id or representation.observed_generation != observed_generation:
        raise ValueError("representation does not belong to the exact run generation")
    _validated_blow5_paths(representation, read_id)
    existing = (await session.execute(select(OntRawSignalLookup).where(
        OntRawSignalLookup.representation_id == representation_id,
        OntRawSignalLookup.read_id == read_id,
    ))).scalar_one_or_none()
    if existing is None:
        existing = OntRawSignalLookup(
            id=_id("ont-waveform"), run_id=run_id, observed_generation=observed_generation,
            representation_id=representation_id, read_id=read_id,
            state="requested", reason_code="requested", receipt={}, created_at=_now(), updated_at=_now(),
        )
        session.add(existing)
        await session.commit()
    return _public_lookup(existing)


def _public_lookup(lookup: OntRawSignalLookup) -> dict[str, Any]:
    return {
        "lookup_id": lookup.id, "run_id": lookup.run_id,
        "observed_generation": lookup.observed_generation,
        "representation_id": lookup.representation_id, "read_id": lookup.read_id,
        "state": lookup.state, "reason_code": lookup.reason_code,
        "sample_count": lookup.sample_count,
        "samples": list(lookup.samples or []) if lookup.state == "ready" else None,
    }


async def get_waveform_lookup(session: AsyncSession, lookup_id: str) -> dict[str, Any]:
    lookup = await session.get(OntRawSignalLookup, lookup_id)
    if lookup is None:
        raise KeyError(lookup_id)
    return _public_lookup(lookup)


async def claim_next_waveform_lookup(session: AsyncSession, *, lease_seconds: int = 120) -> tuple[OntRawSignalLookup, list[str], Path] | None:
    active = (await session.execute(select(OntRawSignalLookup).where(OntRawSignalLookup.state == "running"))).scalars().first()
    if active is not None:
        return None
    lookup = (await session.execute(select(OntRawSignalLookup).where(OntRawSignalLookup.state == "requested").order_by(OntRawSignalLookup.created_at.asc()).limit(1))).scalar_one_or_none()
    if lookup is None:
        return None
    representation = await session.get(OntRawSignalRepresentation, lookup.representation_id)
    if representation is None:
        lookup.state = "failed"
        lookup.reason_code = "representation_missing"
        lookup.completed_at = _now()
        await session.commit()
        return None
    blow5, index = _validated_blow5_paths(representation, lookup.read_id)
    del index
    snapshot = _resource_snapshot(0)
    gate = _runtime_gate(snapshot)
    if gate:
        lookup.state = "failed"
        lookup.reason_code = gate
        lookup.completed_at = _now()
        await session.commit()
        return None
    claim_token = secrets.token_hex(24)
    output_root = Path(snapshot["staging_root"]) / "waveforms" / lookup.id
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / "waveform.json"
    image_ref = _container_image_ref(snapshot)
    command = [
        snapshot["container_runtime"], "run", "--rm", "--network=none", "--read-only",
        f"--user={snapshot['worker_uid']}:{snapshot['worker_gid']}",
        "--cpus=1", "--memory=1g", "--pids-limit=64", "--ulimit", "nofile=128:128",
        "--mount", f"type=bind,src={blow5.parent},dst=/input,readonly",
        "--mount", f"type=bind,src={output_root},dst=/output",
        image_ref, "python", "/opt/bms/ont_raw_signal_lookup.py",
        "--blow5", f"/input/{blow5.name}", "--read-id", lookup.read_id,
        "--max-samples", str(RAW_SIGNAL_MAX_WAVEFORM_SAMPLES), "--output", "/output/waveform.json",
    ]
    lookup.state = "running"
    lookup.reason_code = "leased"
    lookup.claim_token = claim_token
    lookup.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
    lookup.updated_at = _now()
    await session.commit()
    return lookup, command, output


async def finish_waveform_lookup(session: AsyncSession, lookup_id: str, claim_token: str, output: Path, receipt: dict[str, Any]) -> None:
    lookup = await session.get(OntRawSignalLookup, lookup_id)
    if lookup is None or lookup.claim_token != claim_token:
        raise ValueError("waveform lookup lease ownership lost")
    payload = json.loads(output.read_text(encoding="utf-8"))
    samples = payload.get("samples")
    if not isinstance(samples, list) or len(samples) > RAW_SIGNAL_MAX_WAVEFORM_SAMPLES:
        raise ValueError("waveform output violates sample bound")
    lookup.state = "ready"
    lookup.reason_code = "indexed_blow5_lookup_ready"
    lookup.sample_count = int(payload.get("sample_count") or len(samples))
    lookup.samples = samples
    lookup.receipt = receipt
    lookup.claim_token = None
    lookup.lease_expires_at = None
    lookup.completed_at = _now()
    lookup.updated_at = _now()
    await session.commit()


async def fail_waveform_lookup(session: AsyncSession, lookup_id: str, claim_token: str, reason_code: str) -> None:
    lookup = await session.get(OntRawSignalLookup, lookup_id)
    if lookup is None or lookup.claim_token != claim_token:
        return
    lookup.state = "failed"
    lookup.reason_code = reason_code
    lookup.claim_token = None
    lookup.lease_expires_at = None
    lookup.completed_at = _now()
    lookup.updated_at = _now()
    await session.commit()


async def renew_waveform_lookup_lease(session: AsyncSession, lookup_id: str, claim_token: str, *, lease_seconds: int = 120) -> None:
    lookup = await session.get(OntRawSignalLookup, lookup_id)
    if lookup is None or lookup.claim_token != claim_token or lookup.state != "running":
        raise ValueError("waveform lookup lease ownership lost")
    lookup.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
    lookup.updated_at = _now()
    await session.commit()


async def claim_next_derivation(session: AsyncSession, *, lease_seconds: int = 300) -> tuple[OntRawSignalDerivationJob, OntRawSignalRepresentation, dict[str, Any]] | None:
    """Claim one request. SQLite write serialization keeps the one-job policy."""
    active = (await session.execute(select(OntRawSignalDerivationJob).where(
        OntRawSignalDerivationJob.state.in_(("admitted", "partitioning", "converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing"))
    ))).scalars().first()
    if active is not None:
        return None
    job = (await session.execute(select(OntRawSignalDerivationJob).where(
        OntRawSignalDerivationJob.state == "requested"
    ).order_by(OntRawSignalDerivationJob.created_at, OntRawSignalDerivationJob.id).limit(1))).scalar_one_or_none()
    if job is None:
        job = (await session.execute(select(OntRawSignalDerivationJob).where(
            OntRawSignalDerivationJob.state == "deferred",
            OntRawSignalDerivationJob.updated_at < _now() - timedelta(seconds=60),
        ).order_by(OntRawSignalDerivationJob.updated_at, OntRawSignalDerivationJob.id).limit(1))).scalar_one_or_none()
    if job is None:
        return None
    source = await session.get(OntRawSignalRepresentation, job.source_representation_id)
    run = await session.get(OntInstrumentRun, job.run_id)
    if source is None or run is None:
        job.state = "failed"
        job.reason_code = "source_representation_missing" if source is None else "source_run_missing"
        job.completed_at = _now()
        await session.commit()
        return None
    snapshot = _derivation_resource_snapshot(run, source)
    gate = _runtime_gate(snapshot) if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID else _qualification_gate(snapshot)
    if gate:
        job.state = "deferred"
        job.reason_code = gate
        job.resource_snapshot = snapshot
        job.completed_at = _now()
        job.updated_at = _now()
        await session.commit()
        return None
    job.state = "admitted"
    job.reason_code = "qualified_conversion_admitted"
    job.claim_token = _id("ont-raw-claim")
    job.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
    job.resource_snapshot = snapshot
    job.attempt += 1
    job.completed_at = None
    job.updated_at = _now()
    session.add(OntRawSignalDerivationEvent(
        id=_id("ont-raw-event"), job_id=job.id, state=job.state,
        reason_code=job.reason_code, receipt={"claim_token": job.claim_token, "resource_snapshot": snapshot}, created_at=_now(),
    ))
    await session.commit()
    commands = _external_blow5_validation_commands(job, source, snapshot) if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID else _conversion_commands(job, source, snapshot)
    return job, source, commands


async def transition_derivation(session: AsyncSession, job_id: str, claim_token: str, state: str, reason_code: str, receipt: dict[str, Any]) -> OntRawSignalDerivationJob:
    allowed = {"partitioning", "converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing", "ready", "failed", "cancelled"}
    if state not in allowed:
        raise ValueError("invalid raw-signal derivation state")
    job = await session.get(OntRawSignalDerivationJob, job_id)
    if job is None or job.claim_token != claim_token:
        raise ValueError("raw-signal derivation lease ownership lost")
    if job.cancel_requested_at is not None and state != "cancelled":
        raise ValueError("raw-signal derivation cancellation requested")
    if job.lease_expires_at is None or job.lease_expires_at <= _now():
        raise ValueError("raw-signal derivation lease expired")
    job.state = state
    job.reason_code = reason_code
    job.updated_at = _now()
    job.stage_receipts = {**dict(job.stage_receipts or {}), state: receipt}
    if state in {"ready", "failed", "cancelled"}:
        job.completed_at = _now()
        job.lease_expires_at = None
        job.claim_token = None
    else:
        job.lease_expires_at = _now() + timedelta(seconds=300)
    session.add(OntRawSignalDerivationEvent(
        id=_id("ont-raw-event"), job_id=job.id, state=state,
        reason_code=reason_code, receipt=receipt, created_at=_now(),
    ))
    await session.commit()
    return job


async def derivation_cancellation_requested(
    session: AsyncSession,
    job_id: str,
    claim_token: str,
) -> bool:
    job = await session.get(OntRawSignalDerivationJob, job_id)
    return bool(job is not None and job.claim_token == claim_token and job.cancel_requested_at is not None)


async def renew_derivation_lease(
    session: AsyncSession,
    job_id: str,
    claim_token: str,
    *,
    lease_seconds: int = 300,
) -> None:
    job = await session.get(OntRawSignalDerivationJob, job_id)
    if job is None or job.claim_token != claim_token or job.state in {"ready", "failed", "cancelled"}:
        raise ValueError("raw-signal derivation lease ownership lost")
    if job.cancel_requested_at is not None:
        raise ValueError("raw-signal derivation cancellation requested")
    job.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
    job.updated_at = _now()
    await session.commit()


async def close_source_identity(
    session: AsyncSession,
    source_id: str,
    job_id: str,
    claim_token: str,
    receipt_path: str,
) -> OntRawSignalRepresentation:
    job = await session.get(OntRawSignalDerivationJob, job_id)
    source = await session.get(OntRawSignalRepresentation, source_id)
    if job is None or source is None or job.claim_token != claim_token:
        raise ValueError("source preflight lease ownership lost")
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    if receipt.get("status") != "passed" or receipt.get("duplicate_read_ids") not in (0, False):
        raise ValueError("POD5 source preflight did not pass")
    acquisition_ids = receipt.get("acquisition_ids")
    if source.acquisition_id and (
        not isinstance(acquisition_ids, list) or source.acquisition_id not in acquisition_ids
    ):
        raise ValueError("POD5 acquisition identity does not match MinKNOW authority")
    if not isinstance(receipt.get("read_count"), int) or receipt["read_count"] < 1:
        raise ValueError("POD5 source preflight did not establish a non-empty read scope")
    return source


async def publish_derivation(session: AsyncSession, job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, commands: dict[str, Any]) -> OntRawSignalRepresentation:
    stage = Path(commands["stage"])
    publish_root = Path(os.getenv(BLOW5_STAGING_ROOT_ENV, BLOW5_DEFAULT_STAGING_ROOT)).parent / "ont-raw-signal" / job.run_id / str(job.observed_generation)
    final_directory = publish_root / job.id
    if final_directory.is_dir() and not stage.exists():
        outputs = final_directory / "outputs"
        routing_path = final_directory / "routing.json"
        semantic_receipt = final_directory / "semantic-receipt.json"
        recovering_atomic_publication = True
    else:
        outputs = Path(commands["outputs"])
        routing_path = Path(commands["routing"])
        semantic_receipt = stage / "semantic-receipt.json"
        recovering_atomic_publication = False
    if not outputs.is_dir() or not routing_path.is_file() or not semantic_receipt.is_file():
        raise ValueError("validated BLOW5 publication unit is incomplete")
    semantic = json.loads(semantic_receipt.read_text(encoding="utf-8"))
    partition_counts = semantic.get("partition_counts")
    if (
        semantic.get("status") != "passed"
        or semantic.get("duplicate_read_ids") not in (0, False)
        or not isinstance(partition_counts, dict)
        or not partition_counts
        or semantic.get("routing_sha256") != hashlib.sha256(routing_path.read_bytes()).hexdigest()
    ):
        raise ValueError("exhaustive semantic validation receipt did not pass")
    for fingerprint, read_count in partition_counts.items():
        if not _is_sha256(fingerprint) or int(read_count) < 1:
            raise ValueError("semantic receipt contains an invalid conversion partition")
        if not (outputs / f"{fingerprint}.blow5").is_file() or not (outputs / f"{fingerprint}.blow5.idx").is_file():
            raise ValueError("semantic receipt names an incomplete conversion partition")
    publish_root.mkdir(parents=True, exist_ok=True)
    if not recovering_atomic_publication:
        if final_directory.exists():
            raise ValueError("raw-signal publication destination already exists")
        for path in (*sorted(outputs.iterdir()), routing_path, semantic_receipt):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        for directory in (outputs, stage):
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.replace(stage, final_directory)
        outputs = final_directory / "outputs"
        routing_path = final_directory / "routing.json"
    directory_fd = os.open(publish_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    artifacts: list[dict[str, Any]] = []
    for fingerprint, read_count in sorted(partition_counts.items()):
        blow5_artifact = _file_artifact(outputs / f"{fingerprint}.blow5", _id("ont-artifact"), kind="blow5")
        index_artifact = _file_artifact(outputs / f"{fingerprint}.blow5.idx", _id("ont-artifact"), kind="blow5_index")
        blow5_artifact.update({"partition_fingerprint": fingerprint, "read_count": int(read_count)})
        index_artifact.update({"partition_fingerprint": fingerprint})
        artifacts.extend((blow5_artifact, index_artifact))
    artifacts.append(_file_artifact(routing_path, _id("ont-artifact"), kind="read_routing"))
    manifest = {"schema": "bms.ont.raw-signal-artifacts.v1", "run_id": job.run_id, "observed_generation": job.observed_generation, "format": "blow5", "artifacts": artifacts}
    representation = OntRawSignalRepresentation(
        id=_id("ont-raw-rep"), run_id=job.run_id, observed_generation=job.observed_generation,
        role="derived", source_kind="pod5_to_blow5", format="blow5",
        source_fidelity="verified_signal_and_full_common_field_contract_exact", state="ready", reason_code="partitioned_indexed_blow5_ready_native_pod5_retained",
        artifact_manifest=manifest, manifest_sha256=_digest(manifest),
        parent_representation_ids=[source.id], parent_manifest_sha256s=[source.manifest_sha256],
        compression={"record": "zstd", "signal": "svb-zd"},
        runtime_identity={"profile_id": BLOW5_PROFILE_ID, "container_digest": job.resource_snapshot.get("container_digest")},
        validation_receipts={"semantic": semantic, "adjacent_index": True}, profile_id=BLOW5_PROFILE_ID,
        read_count=int(semantic["read_count"]),
        published_at=_now(), created_at=_now(),
    )
    session.add(representation)
    await session.flush()
    job.output_representation_id = representation.id
    await session.commit()
    return representation


async def recover_expired_derivations(session: AsyncSession) -> int:
    """Fail closed on expired work. Partial output is never resumed or published."""
    now = _now()
    expired_lookups = list((await session.execute(select(OntRawSignalLookup).where(
        OntRawSignalLookup.state == "running",
        OntRawSignalLookup.lease_expires_at < now,
    ))).scalars())
    for lookup in expired_lookups:
        lookup.state = "failed"
        lookup.reason_code = "lease_expired"
        lookup.claim_token = None
        lookup.lease_expires_at = None
        lookup.completed_at = now
        lookup.updated_at = now
    rows = list((await session.execute(select(OntRawSignalDerivationJob).where(
        OntRawSignalDerivationJob.state.in_(("admitted", "partitioning", "converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing")),
        OntRawSignalDerivationJob.lease_expires_at < now,
    ))).scalars())
    for row in rows:
        source = await session.get(OntRawSignalRepresentation, row.source_representation_id)
        if row.state == "publishing" and source is not None:
            representation = await session.get(OntRawSignalRepresentation, row.output_representation_id) if row.output_representation_id else None
            if representation is not None and representation.state == "ready":
                row.state = "ready"
                row.reason_code = "publication_commit_recovered"
                row.claim_token = None
                row.lease_expires_at = None
                row.completed_at = now
                row.updated_at = now
                session.add(OntRawSignalDerivationEvent(
                    id=_id("ont-raw-event"), job_id=row.id, state="ready",
                    reason_code=row.reason_code, receipt={"representation_id": representation.id, "recovered_after_db_commit": True}, created_at=now,
                ))
                continue
            commands = _conversion_commands(row, source, dict(row.resource_snapshot or {}))
            final_directory = Path(os.getenv(BLOW5_STAGING_ROOT_ENV, BLOW5_DEFAULT_STAGING_ROOT)).parent / "ont-raw-signal" / row.run_id / str(row.observed_generation) / row.id
            if final_directory.is_dir():
                try:
                    representation = await publish_derivation(session, row, source, commands)
                    row.state = "ready"
                    row.reason_code = "atomic_publication_recovered"
                    row.claim_token = None
                    row.lease_expires_at = None
                    row.completed_at = now
                    row.updated_at = now
                    session.add(OntRawSignalDerivationEvent(
                        id=_id("ont-raw-event"), job_id=row.id, state="ready",
                        reason_code=row.reason_code, receipt={"representation_id": representation.id, "recovered_after_rename": True}, created_at=now,
                    ))
                    continue
                except Exception:
                    pass
        row.state = "failed"
        row.reason_code = "lease_expired_partial_attempt_discarded"
        row.failure_code = row.reason_code
        row.claim_token = None
        row.lease_expires_at = None
        row.completed_at = now
        row.updated_at = now
        session.add(OntRawSignalDerivationEvent(
            id=_id("ont-raw-event"), job_id=row.id, state=row.state,
            reason_code=row.reason_code, receipt={"attempt": row.attempt}, created_at=now,
        ))
    if rows or expired_lookups:
        await session.commit()
    return len(rows) + len(expired_lookups)

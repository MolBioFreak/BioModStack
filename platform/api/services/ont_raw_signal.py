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
from uuid import uuid4

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
BLOW5_PROFILE_ID = "bms.blow5.zstd-svb-zd.v1"
EXTERNAL_BLOW5_VALIDATION_PROFILE_ID = "bms.blow5.external-validation.v1"
BLOW5_CONTAINER_ENV = "BMS_ONT_SLOW5TOOLS_IMAGE"
BLOW5_CONTAINER_DIGEST_ENV = "BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST"
BLOW5_CONTAINER_RUNTIME_ENV = "BMS_ONT_CONTAINER_RUNTIME"
BLOW5_CONVERSION_ENABLED_ENV = "BMS_ONT_BLOW5_CONVERSION_QUALIFIED"
BLOW5_STAGING_ROOT_ENV = "BMS_ONT_RAW_SIGNAL_STAGING_ROOT"
BLOW5_MIN_FREE_BYTES_ENV = "BMS_ONT_RAW_SIGNAL_MIN_FREE_BYTES"
BLOW5_ACQUISITION_PRESSURE_ENV = "BMS_ONT_RAW_SIGNAL_ACQUISITION_PRESSURE"
BLOW5_DEFAULT_STAGING_ROOT = "/mnt/BioModStack/ont-raw-signal-staging"
BLOW5_DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024
RAW_SIGNAL_VALIDATOR = Path(__file__).resolve().parents[3] / "scripts" / "ont_raw_signal_validate.py"
RAW_SIGNAL_LOOKUP = Path(__file__).resolve().parents[3] / "scripts" / "ont_raw_signal_lookup.py"
_RAW_FORMATS = frozenset({"pod5", "slow5", "blow5"})
_TERMINAL_STATES = frozenset({"stopped", "completed", "failed"})
_READY = "ready"
_PREPARABLE = "preparable"
_UNAVAILABLE = "unavailable"
RAW_SIGNAL_MAX_WAVEFORM_SAMPLES = 20_000


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
        terminal_artifact_manifest=None,
        terminal_artifact_manifest_sha256=None,
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
    if not RAW_SIGNAL_VALIDATOR.is_file():
        return "validator_runtime_missing"
    if snapshot["disk_free_bytes"] < snapshot["required_free_bytes"]:
        return "validation_capacity_gate_failed"
    return None


def _source_paths(source: OntRawSignalRepresentation) -> list[Path]:
    manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    paths = [Path(str(item["path"])) for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("kind") == "pod5" and item.get("path")]
    if not paths:
        raise ValueError("POD5 source has no governed artifact paths")
    return paths


def _conversion_commands(job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, snapshot: dict[str, Any]) -> dict[str, Any]:
    stage = Path(snapshot["staging_root"]) / job.id / f"attempt-{job.attempt}"
    output = stage / "result.blow5"
    index = Path(f"{output}.idx")
    inputs = _source_paths(source)
    image_ref = _container_image_ref(snapshot)
    input_args = [f"/inputs/{position}/{path.name}" for position, path in enumerate(inputs)]
    validator_input_args = [item for value in input_args for item in ("--pod5", value)]
    base = [
        snapshot["container_runtime"], "run", "--rm", "--network=none", "--read-only",
        f"--user={snapshot['worker_uid']}:{snapshot['worker_gid']}",
        "--cpus=4", "--memory=16g", "--pids-limit=256", "--ulimit", "nofile=512:512",
        "--mount", f"type=bind,src={stage},dst=/stage",
        "--mount", f"type=bind,src={RAW_SIGNAL_VALIDATOR},dst=/opt/bms/ont_raw_signal_validate.py,readonly",
    ]
    # Build mounts without a shell. slow5tools degrade is absent by construction.
    bind_args: list[str] = []
    for position, path in enumerate(inputs):
        bind_args.extend(["--mount", f"type=bind,src={path.parent},dst=/inputs/{position},readonly"])
    common = base + bind_args + [image_ref]
    return {
        "stage": str(stage), "output": str(output), "index": str(index),
        "source_receipt": str(stage / "source-preflight-receipt.json"),
        "source_preflight": common + [
            "python3", "/opt/bms/ont_raw_signal_validate.py", "source-preflight", *validator_input_args,
            "--expected-acquisition-id", source.acquisition_id or "external-native",
            "--receipt", "/stage/source-preflight-receipt.json",
        ],
        "convert": common + ["blue-crab", "p2s", "-c", "zstd", "-s", "svb-zd", "--iop", "1", "--threads", "4", "--batchsize", "1000", *input_args, "-o", "/stage/result.blow5"],
        "quickcheck": common + ["slow5tools", "quickcheck", "/stage/result.blow5"],
        "index_create": common + ["slow5tools", "index", "/stage/result.blow5"],
        "semantic_validate": common + ["python3", "/opt/bms/ont_raw_signal_validate.py", *validator_input_args, "--blow5", "/stage/result.blow5", "--index", "/stage/result.blow5.idx", "--receipt", "/stage/semantic-receipt.json"],
    }


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
        "--mount", f"type=bind,src={RAW_SIGNAL_VALIDATOR},dst=/opt/bms/ont_raw_signal_validate.py,readonly",
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


async def request_blow5_derivation(session: AsyncSession, *, run_id: str, observed_generation: int, source_representation_id: str, consumer_id: str, preference: RepresentationPreference) -> dict[str, Any]:
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
    if validation_only and source.source_kind != "external_import":
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
        snapshot = _resource_snapshot(_source_bytes(source))
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
                "automatic_whole_run_conversion": False,
                "operation": "external_validation" if validation_only else "pod5_to_blow5_conversion",
                "profile_id": existing.profile_id,
                "resource_snapshot": snapshot,
            }, created_at=_now(),
        )
        session.add_all((existing, event))
        await session.flush()
    elif existing.state == "deferred":
        snapshot = _resource_snapshot(_source_bytes(source))
        gate = _runtime_gate(snapshot) if validation_only else _qualification_gate(snapshot)
        existing.resource_snapshot = snapshot
        existing.updated_at = _now()
        existing.reason_code = gate or ("external_blow5_validation_requested" if validation_only else "qualified_conversion_requested")
        if gate is None:
            existing.state = "requested"
            existing.completed_at = None
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


def _validated_blow5_paths(representation: OntRawSignalRepresentation) -> tuple[Path, Path]:
    if representation.format != "blow5" or representation.state != "ready":
        raise ValueError("raw waveform requires a ready BLOW5 representation")
    receipts = representation.validation_receipts if isinstance(representation.validation_receipts, dict) else {}
    if not receipts.get("adjacent_index"):
        raise ValueError("raw waveform requires a validated adjacent BLOW5 index")
    manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
    raw_artifacts = manifest.get("artifacts")
    artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
    paths = {
        str(item.get("kind")): Path(str(item.get("path"))).expanduser().resolve()
        for item in artifacts
        if isinstance(item, dict) and item.get("kind") in {"blow5", "blow5_index"} and item.get("path")
    }
    blow5 = paths.get("blow5")
    index = paths.get("blow5_index")
    if blow5 is None or index is None or index != Path(f"{blow5}.idx"):
        raise ValueError("raw waveform representation lacks an adjacent index")
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
    _validated_blow5_paths(representation)
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
    blow5, index = _validated_blow5_paths(representation)
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
        "--mount", f"type=bind,src={RAW_SIGNAL_LOOKUP},dst=/opt/bms/ont_raw_signal_lookup.py,readonly",
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
        OntRawSignalDerivationJob.state.in_(("admitted", "converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing"))
    ))).scalars().first()
    if active is not None:
        return None
    job = (await session.execute(select(OntRawSignalDerivationJob).where(
        OntRawSignalDerivationJob.state == "requested"
    ).order_by(OntRawSignalDerivationJob.created_at, OntRawSignalDerivationJob.id).limit(1))).scalar_one_or_none()
    if job is None:
        return None
    source = await session.get(OntRawSignalRepresentation, job.source_representation_id)
    if source is None:
        job.state = "failed"
        job.reason_code = "source_representation_missing"
        job.completed_at = _now()
        await session.commit()
        return None
    snapshot = _resource_snapshot(_source_bytes(source))
    gate = _runtime_gate(snapshot) if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID else _qualification_gate(snapshot)
    if gate:
        job.state = "deferred"
        job.reason_code = gate
        job.resource_snapshot = snapshot
        job.completed_at = _now()
        await session.commit()
        return None
    job.state = "admitted"
    job.reason_code = "qualified_conversion_admitted"
    job.claim_token = _id("ont-raw-claim")
    job.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
    job.resource_snapshot = snapshot
    job.attempt += 1
    job.updated_at = _now()
    session.add(OntRawSignalDerivationEvent(
        id=_id("ont-raw-event"), job_id=job.id, state=job.state,
        reason_code=job.reason_code, receipt={"claim_token": job.claim_token, "resource_snapshot": snapshot}, created_at=_now(),
    ))
    await session.commit()
    commands = _external_blow5_validation_commands(job, source, snapshot) if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID else _conversion_commands(job, source, snapshot)
    return job, source, commands


async def transition_derivation(session: AsyncSession, job_id: str, claim_token: str, state: str, reason_code: str, receipt: dict[str, Any]) -> OntRawSignalDerivationJob:
    allowed = {"converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing", "ready", "failed", "cancelled"}
    if state not in allowed:
        raise ValueError("invalid raw-signal derivation state")
    job = await session.get(OntRawSignalDerivationJob, job_id)
    if job is None or job.claim_token != claim_token:
        raise ValueError("raw-signal derivation lease ownership lost")
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
    if source.acquisition_id and receipt.get("acquisition_id") != source.acquisition_id:
        raise ValueError("POD5 acquisition identity does not match MinKNOW authority")
    if not isinstance(receipt.get("read_count"), int) or receipt["read_count"] < 1:
        raise ValueError("POD5 source preflight did not establish a non-empty read scope")
    source.state = _READY
    source.reason_code = "pod5_acquisition_identity_closed"
    source.read_count = receipt["read_count"]
    source.validation_receipts = {**dict(source.validation_receipts or {}), "source_preflight": receipt}
    await session.commit()
    return source


async def publish_derivation(session: AsyncSession, job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, commands: dict[str, Any]) -> OntRawSignalRepresentation:
    stage = Path(commands["stage"])
    publish_root = Path(os.getenv(BLOW5_STAGING_ROOT_ENV, BLOW5_DEFAULT_STAGING_ROOT)).parent / "ont-raw-signal" / job.run_id / str(job.observed_generation)
    final_directory = publish_root / job.id
    if final_directory.is_dir() and not stage.exists():
        output = final_directory / "result.blow5"
        index = final_directory / "result.blow5.idx"
        semantic_receipt = final_directory / "semantic-receipt.json"
        recovering_atomic_publication = True
    else:
        output = Path(commands["output"])
        index = Path(commands["index"])
        semantic_receipt = stage / "semantic-receipt.json"
        recovering_atomic_publication = False
    if not output.is_file() or not index.is_file() or not semantic_receipt.is_file():
        raise ValueError("validated BLOW5 publication unit is incomplete")
    semantic = json.loads(semantic_receipt.read_text(encoding="utf-8"))
    if semantic.get("status") != "passed" or semantic.get("duplicate_read_ids") not in (0, False):
        raise ValueError("exhaustive semantic validation receipt did not pass")
    publish_root.mkdir(parents=True, exist_ok=True)
    if not recovering_atomic_publication:
        if final_directory.exists():
            raise ValueError("raw-signal publication destination already exists")
        for path in (output, index, semantic_receipt):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        os.replace(stage, final_directory)
        output = final_directory / "result.blow5"
        index = final_directory / "result.blow5.idx"
    directory_fd = os.open(publish_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    artifacts = [_file_artifact(output, _id("ont-artifact"), kind="blow5"), _file_artifact(index, _id("ont-artifact"), kind="blow5_index")]
    manifest = {"schema": "bms.ont.raw-signal-artifacts.v1", "run_id": job.run_id, "observed_generation": job.observed_generation, "format": "blow5", "artifacts": artifacts}
    representation = OntRawSignalRepresentation(
        id=_id("ont-raw-rep"), run_id=job.run_id, observed_generation=job.observed_generation,
        role="derived", source_kind="pod5_to_blow5", format="blow5",
        source_fidelity="verified_signal_and_mapping_contract_exact", state="ready", reason_code="indexed_blow5_ready_native_pod5_retained",
        artifact_manifest=manifest, manifest_sha256=_digest(manifest),
        parent_representation_ids=[source.id], parent_manifest_sha256s=[source.manifest_sha256],
        compression={"record": "zstd", "signal": "svb-zd"},
        runtime_identity={"profile_id": BLOW5_PROFILE_ID, "container_digest": job.resource_snapshot.get("container_digest")},
        validation_receipts={"semantic": semantic, "adjacent_index": True}, profile_id=BLOW5_PROFILE_ID,
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
        OntRawSignalDerivationJob.state.in_(("admitted", "converting", "structural_check", "indexing", "index_validation", "semantic_validation", "publishing")),
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

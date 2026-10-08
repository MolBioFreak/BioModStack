"""Governed ONT raw-signal representations and capability selection.

Paths remain server-side. Public functions return opaque representation metadata.
Conversion stays fail-closed until the exact local fidelity profile is qualified.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import stat
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import event, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_ngs_models import MolBioNGSDomainState, MolBioNGSSample

from database import (
    InputFile,
    OntInstrumentRun,
    OntInstrumentRunEvent,
    OntRawSignalDerivationEvent,
    OntRawSignalDerivationJob,
    OntRawSignalLookup,
    OntRawSignalRepresentation,
)
from services.file_lease_signals import register_lease_break_listener


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
LIVE_CONVERSION_ENABLED_ENV = "BMS_ONT_LIVE_CONVERSION_ENABLED"
RAW_SIGNAL_RETENTION_POLICY_ENV = "BMS_ONT_RAW_SIGNAL_RETENTION_POLICY"
BLOW5_DEFAULT_STAGING_ROOT = "/mnt/BioModStack/ont-raw-signal-staging"
BLOW5_DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024

_SOURCE_LEASE_BREAK = threading.Event()


def _record_source_lease_break() -> None:
    _SOURCE_LEASE_BREAK.set()


if threading.current_thread() is threading.main_thread():
    register_lease_break_listener(_record_source_lease_break)


def source_lease_break_requested() -> bool:
    return _SOURCE_LEASE_BREAK.is_set()

_RAW_FORMATS = frozenset({"pod5", "slow5", "blow5"})
_TERMINAL_STATES = frozenset({"stopped", "completed", "failed"})
_READY = "ready"
_PREPARABLE = "preparable"
_UNAVAILABLE = "unavailable"
RAW_SIGNAL_MAX_WAVEFORM_SAMPLES = 20_000


def live_conversion_enabled() -> bool:
    return os.getenv(LIVE_CONVERSION_ENABLED_ENV, "1").strip().lower() in {"1", "true", "yes", "on"}


def raw_signal_retention_policy() -> Literal["pod5_and_blow5", "blow5_only"]:
    policy = os.getenv(RAW_SIGNAL_RETENTION_POLICY_ENV, "pod5_and_blow5").strip().lower()
    if policy not in {"pod5_and_blow5", "blow5_only"}:
        raise RuntimeError(f"{RAW_SIGNAL_RETENTION_POLICY_ENV} must be pod5_and_blow5 or blow5_only")
    return policy  # type: ignore[return-value]


def retention_disposition() -> str:
    if raw_signal_retention_policy() == "pod5_and_blow5":
        return "retain_pod5_and_blow5"
    return "future_delete_after_verified_blow5_not_active_in_integrated_bms"


def retention_deletion_enabled() -> bool:
    """The integrated BMS build exposes the future policy seam without deleting acquisition evidence."""
    return False


def live_pod5_identity_snapshot(paths: list[str]) -> dict[str, dict[str, int]]:
    """Capture bounded file identity used to recognize a closed MinKNOW POD5 chunk."""
    snapshot: dict[str, dict[str, int]] = {}
    for value in sorted(set(paths)):
        path = Path(value)
        try:
            info = path.lstat()
        except OSError:
            continue
        if path.suffix.lower() != ".pod5" or not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size < 1:
            continue
        snapshot[str(path)] = {
            "device": info.st_dev,
            "inode": info.st_ino,
            "bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }
    return snapshot


def stable_live_pod5_paths(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> list[Path]:
    previous = previous if isinstance(previous, dict) else {}
    current = current if isinstance(current, dict) else {}
    return [Path(path) for path in sorted(current) if previous.get(path) == current[path]]


def _live_output_roots(run: OntInstrumentRun) -> tuple[Path, ...]:
    configured = run.output_directories if isinstance(run.output_directories, dict) else {}
    roots: list[Path] = []
    for key in ("output", "reads"):
        raw = str(configured.get(key) or "").strip()
        if not raw or len(raw) > 2048 or not os.path.isabs(raw):
            continue
        if any(component in {".", ".."} for component in raw.split(os.sep)):
            continue
        root = Path(os.path.abspath(raw))
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _open_absolute_directory_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current_fd = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_live_pod5_candidate(
    path: Path,
    approved_roots: tuple[Path, ...],
    expected: dict[str, int],
    artifact_id: str,
) -> tuple[dict[str, Any], int]:
    """Open and read-lease one live chunk beneath an approved root descriptor."""
    candidate = Path(os.path.abspath(str(path)))
    for root in approved_roots:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if not relative.parts or candidate.suffix.lower() != ".pod5":
            continue
        root_fd = -1
        current_fd = -1
        file_fd = -1
        try:
            root_fd = _open_absolute_directory_nofollow(root)
            current_fd = os.dup(root_fd)
            for component in relative.parts[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current_fd,
                )
                os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(
                relative.parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            info = os.fstat(file_fd)
            observed = tuple(
                getattr(info, attribute)
                for attribute in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            )
            required = tuple(
                int(expected[field])
                for field in ("device", "inode", "bytes", "mtime_ns", "ctime_ns")
            )
            if not stat.S_ISREG(info.st_mode) or observed != required:
                raise ValueError("live POD5 identity changed before registration")
            try:
                fcntl.fcntl(file_fd, fcntl.F_SETLEASE, fcntl.F_RDLCK)
            except OSError as exc:
                raise RuntimeError("live POD5 is still open for writing") from exc
            artifact = _file_artifact(
                candidate,
                artifact_id,
                kind="pod5",
                opened_fd=file_fd,
                governed_root_path=root,
                governed_root_fd=root_fd,
                governed_relative_path=relative.as_posix(),
            )
            return artifact, file_fd
        except BaseException:
            if file_fd >= 0:
                os.close(file_fd)
            raise
        finally:
            if current_fd >= 0:
                os.close(current_fd)
            if root_fd >= 0:
                os.close(root_fd)
    raise ValueError("live POD5 is outside configured MinKNOW output roots")


def _external_pod5_root() -> Path:
    configured = os.getenv(EXTERNAL_POD5_ROOT_ENV, "").strip()
    if not configured:
        raise RuntimeError(f"{EXTERNAL_POD5_ROOT_ENV} is not configured")
    root = Path(configured).expanduser().absolute()
    for component in (root, *root.parents):
        if component.is_symlink():
            raise RuntimeError("external POD5 root must not contain symbolic links")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("external POD5 root must be a real directory")
    return root


def _open_external_pod5_root() -> tuple[Path, int]:
    """Open each root component without following filesystem indirection."""
    configured = os.getenv(EXTERNAL_POD5_ROOT_ENV, "").strip()
    if not configured:
        raise RuntimeError(f"{EXTERNAL_POD5_ROOT_ENV} is not configured")
    expanded = Path(configured).expanduser()
    if ".." in expanded.parts:
        raise RuntimeError("external POD5 root must not contain parent traversal")
    root = expanded.absolute()
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return root, current_fd
    except OSError as exc:
        os.close(current_fd)
        raise RuntimeError("external POD5 root could not be opened without symbolic links") from exc
    except BaseException:
        os.close(current_fd)
        raise


def _candidate_identity(relative: str, info: os.stat_result) -> str:
    return _digest({
        "relative_path": relative,
        "device": info.st_dev,
        "inode": info.st_ino,
        "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    })


def _descriptor_pod5_candidates(directory_fd: int, prefix: Path = Path()) -> list[tuple[str, os.stat_result]]:
    candidates: list[tuple[str, os.stat_result]] = []
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            relative = prefix / entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    candidates.extend(_descriptor_pod5_candidates(child_fd, relative))
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode) and entry.name.lower().endswith(".pod5"):
                candidates.append((relative.as_posix(), info))
    return candidates


def _assert_confined_regular_file(path: Path, root: Path) -> os.stat_result:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("external POD5 candidate path contains a symbolic link")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("external POD5 candidate is not a regular file")
    return info


def list_external_pod5_candidates() -> list[dict[str, Any]]:
    """Return path-opaque POD5 candidates from one descriptor-pinned server root."""
    _root, root_fd = _open_external_pod5_root()
    try:
        return [
            {
                "candidate_id": _candidate_identity(relative, info),
                "display_name": relative,
                "size_bytes": info.st_size,
                "modified_at_ns": info.st_mtime_ns,
            }
            for relative, info in sorted(_descriptor_pod5_candidates(root_fd), key=lambda item: item[0])
        ]
    finally:
        os.close(root_fd)


def _open_descriptor_candidate(candidate_id: str) -> tuple[Path, int, int, str]:
    if not _is_sha256(candidate_id):
        raise KeyError("external POD5 candidate was not found")
    root, root_fd = _open_external_pod5_root()
    try:
        match = next(
            ((relative, info) for relative, info in _descriptor_pod5_candidates(root_fd) if secrets.compare_digest(_candidate_identity(relative, info), candidate_id)),
            None,
        )
        if match is None:
            raise KeyError("external POD5 candidate was not found")
        relative, expected = match
        parts = Path(relative).parts
        parent_fd = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        observed = os.fstat(file_fd)
        if _candidate_identity(relative, observed) != candidate_id or (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
            os.close(file_fd)
            raise ValueError("external POD5 candidate changed before registration")
        return root / relative, file_fd, root_fd, relative
    except BaseException:
        os.close(root_fd)
        raise


def resolve_external_pod5_candidate(candidate_id: str) -> Path:
    path, file_fd, root_fd, _relative = _open_descriptor_candidate(candidate_id)
    os.close(file_fd)
    os.close(root_fd)
    return path


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
        "artifacts": [
            {
                key: artifact.get(key)
                for key in (
                    "artifact_id",
                    "kind",
                    "bytes",
                    "sha256",
                    "partition_fingerprint",
                    "read_count",
                )
                if artifact.get(key) is not None
            }
            for artifact in artifacts
            if isinstance(artifact, dict)
        ],
        "read_count": record.read_count,
        "profile_id": record.profile_id,
        "compression": dict(record.compression or {}),
        "parent_representation_ids": list(record.parent_representation_ids or []),
        "parent_manifest_sha256s": list(record.parent_manifest_sha256s or []),
        "runtime_identity": dict(record.runtime_identity or {}),
        "validation_receipts": dict(record.validation_receipts or {}),
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


def _require_derivable_generation(
    run: OntInstrumentRun,
    event: OntInstrumentRunEvent,
    source: OntRawSignalRepresentation,
) -> None:
    if source.source_kind != "minknow_live":
        _require_sealed_generation(run, event)
        return
    receipts = source.validation_receipts if isinstance(source.validation_receipts, dict) else {}
    if (
        event.state != "running"
        or receipts.get("stable_observations") != 2
        or receipts.get("retention_policy") not in {"pod5_and_blow5", "blow5_only"}
    ):
        raise ValueError("live MinKNOW POD5 derivation requires a stable running-generation receipt")


def _seal_native_pod5_artifacts(terminal_artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for position, terminal_artifact in enumerate(terminal_artifacts):
        path = Path(str(terminal_artifact.get("path", "")))
        sealed = _file_artifact(
            path,
            str(terminal_artifact.get("artifact_id") or f"native-pod5-{position}"),
            kind="pod5",
        )
        if sealed["bytes"] != terminal_artifact.get("bytes") or sealed["sha256"] != terminal_artifact.get("sha256"):
            raise ValueError("native POD5 differs from the sealed terminal manifest")
        artifacts.append(sealed)
    return artifacts


async def register_native_pod5_generation(session: AsyncSession, *, run_id: str, observed_generation: int) -> list[dict[str, Any]]:
    """Register immutable MinKNOW POD5 shards from the sealed acquisition manifest."""
    run, _event = await _exact_generation(session, run_id, observed_generation)
    manifest = _sealed_manifest(run, observed_generation)
    terminal_artifacts = [dict(item) for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("kind") == "pod5"]
    if not terminal_artifacts:
        return []
    artifacts = _seal_native_pod5_artifacts(terminal_artifacts)
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


async def register_live_pod5_chunks(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    stable_paths: list[Path],
    identity_snapshot: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    """Register and queue newly closed MinKNOW POD5 chunks while acquisition continues."""
    if not live_conversion_enabled() or not stable_paths:
        return []
    run, event = await _exact_generation(session, run_id, observed_generation)
    if event.state != "running" or event.observed_generation != run.observed_generation:
        return []
    acquisition_id = (run.last_minknow_payload or {}).get("acquisition_id") if isinstance(run.last_minknow_payload, dict) else None
    if not acquisition_id:
        return []
    prior = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.source_kind == "minknow_live",
            )
        )
    ).scalars().all()
    prior_by_identity = {
        tuple(item.get(field) for field in ("device", "inode", "bytes", "mtime_ns", "ctime_ns")): representation
        for representation in prior
        for item in (representation.artifact_manifest or {}).get("artifacts", [])
        if isinstance(item, dict)
    }
    registered: list[dict[str, Any]] = []
    policy = raw_signal_retention_policy()
    approved_roots = _live_output_roots(run)
    for position, path in enumerate(stable_paths):
        expected = identity_snapshot.get(str(path))
        if not isinstance(expected, dict):
            continue
        try:
            artifact, file_fd = _open_live_pod5_candidate(
                path,
                approved_roots,
                expected,
                f"live-pod5-{observed_generation}-{position}",
            )
        except (OSError, RuntimeError, ValueError):
            continue
        if any(artifact.get(field) != expected.get(field) for field in ("device", "inode", "bytes", "mtime_ns", "ctime_ns")):
            os.close(file_fd)
            continue
        identity = tuple(artifact.get(field) for field in ("device", "inode", "bytes", "mtime_ns", "ctime_ns"))
        existing_representation = prior_by_identity.get(identity)
        if existing_representation is not None:
            try:
                await request_blow5_derivation(
                    session,
                    run_id=run_id,
                    observed_generation=existing_representation.observed_generation,
                    source_representation_id=existing_representation.id,
                    consumer_id="ont-live-minknow-conversion",
                    preference="auto",
                    automatic=True,
                )
            finally:
                os.close(file_fd)
            continue
        _hold_source_descriptors_through_transaction(session, [file_fd])
        manifest = {
            "schema": "bms.ont.raw-signal-live-chunk.v1",
            "run_id": run_id,
            "observed_generation": observed_generation,
            "format": "pod5",
            "acquisition_id": acquisition_id,
            "artifacts": [artifact],
        }
        representation = OntRawSignalRepresentation(
            id=_id("ont-raw-rep"),
            run_id=run_id,
            observed_generation=observed_generation,
            role="source",
            source_kind="minknow_live",
            format="pod5",
            source_fidelity="native_acquisition_live_chunk",
            state=_UNAVAILABLE,
            reason_code="live_pod5_chunk_queued",
            artifact_manifest=manifest,
            manifest_sha256=_digest(manifest),
            parent_representation_ids=[],
            parent_manifest_sha256s=[],
            compression={},
            runtime_identity={},
            validation_receipts={
                "stable_observations": 2,
                "retention_policy": policy,
                "retention_disposition": retention_disposition(),
                "source_deletion_enabled": retention_deletion_enabled(),
            },
            acquisition_id=acquisition_id,
            retention_pinned_at=_now(),
            created_at=_now(),
        )
        session.add(representation)
        await session.flush()
        await request_blow5_derivation(
            session,
            run_id=run_id,
            observed_generation=observed_generation,
            source_representation_id=representation.id,
            consumer_id="ont-live-minknow-conversion",
            preference="auto",
            automatic=True,
        )
        prior_by_identity[identity] = representation
        registered.append(_public_representation(representation))
    return registered


def _resolve_input_file(record: InputFile, expected_format: str) -> Path:
    suffixes = {"pod5": (".pod5",), "slow5": (".slow5",), "blow5": (".blow5",)}[expected_format]
    candidate = (Path(record.directory) / record.filename).expanduser().resolve(strict=True)
    if not candidate.name.lower().endswith(suffixes) or not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"input file is not a regular {expected_format.upper()} artifact")
    return candidate


def _file_artifact(
    path: Path,
    artifact_id: str,
    *,
    kind: str,
    opened_fd: int | None = None,
    governed_root_path: Path | None = None,
    governed_root_fd: int | None = None,
    governed_relative_path: str | None = None,
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags) if opened_fd is None else opened_fd
    close_fd = opened_fd is None
    try:
        os.lseek(fd, 0, os.SEEK_SET)
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
        if governed_root_path is not None and governed_root_fd is not None and governed_relative_path is not None:
            parent_info = os.fstat(governed_root_fd)
            root_path = governed_root_path
            relative_path = governed_relative_path
        else:
            visible = path.lstat()
            visible_identity = (visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns, visible.st_ctime_ns)
            if visible_identity != after_identity:
                raise ValueError("raw-signal source path changed while it was being registered")
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parent_info = os.fstat(parent_fd)
            finally:
                os.close(parent_fd)
            root_path = path.parent
            relative_path = path.name
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "bytes": info.st_size,
            "sha256": digest.hexdigest(),
            "path": str(path),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
            "governed_root_path": str(root_path),
            "governed_root_device": parent_info.st_dev,
            "governed_root_inode": parent_info.st_ino,
            "governed_relative_path": relative_path,
        }
    finally:
        if close_fd:
            os.close(fd)


def _hold_source_descriptors_through_transaction(session: AsyncSession, fds: list[int]) -> None:
    """Close governed source descriptors only after outer commit or rollback."""
    state = {"fds": list(fds)}

    def close_descriptors(_session: Any, transaction: Any) -> None:
        if transaction.parent is not None:
            return
        held = state.pop("fds", [])
        for fd in held:
            os.close(fd)

    event.listen(session.sync_session, "after_transaction_end", close_descriptors)


async def register_external_source(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    format: str,
    input_file_id: str,
    index_input_file_id: str | None,
    source_fidelity: str,
    source_path_override: Path | None = None,
    source_artifact_override: dict[str, Any] | None = None,
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
    source_path = source_path_override or _resolve_input_file(source, normalized_format)
    artifacts = [
        dict(source_artifact_override)
        if source_artifact_override is not None
        else _file_artifact(source_path, source.id, kind=normalized_format)
    ]
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
    external_registration_key: str | None = None,
    candidate_id: str | None = None,
    source_sha256: str | None = None,
    source_path_override: Path | None = None,
    source_artifact_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one sealed external generation without MinKNOW or POD5 ancestry."""
    now = _now()
    run_id = (
        f"ont-external-run-{external_registration_key[:32]}"
        if external_registration_key
        else _id("ont-external-run")
    )
    marker = {
        "schema": "bms.ont.external-raw-signal-registration.v1",
        "format": format,
        "input_file_id": input_file_id,
        "index_input_file_id": index_input_file_id,
        "candidate_id": candidate_id,
        "source_sha256": source_sha256,
        "sample_id": sample_id,
    }
    input_record = await session.get(InputFile, input_file_id)
    if input_record is None:
        raise KeyError(input_file_id)
    source_path = source_path_override or _resolve_input_file(input_record, format)
    source_artifact = (
        dict(source_artifact_override)
        if source_artifact_override is not None
        else _file_artifact(source_path, f"{run_id}:source", kind=format)
    )
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
        external_registration_key=external_registration_key,
        external_source_device=source_artifact.get("device"),
        external_source_inode=source_artifact.get("inode"),
        external_source_bytes=source_artifact.get("bytes"),
        external_source_mtime_ns=source_artifact.get("mtime_ns"),
        external_source_ctime_ns=source_artifact.get("ctime_ns"),
        external_source_root_device=source_artifact.get("governed_root_device"),
        external_source_root_inode=source_artifact.get("governed_root_inode"),
        external_source_relative_path=source_artifact.get("governed_relative_path"),
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
        source_path_override=source_path,
        source_artifact_override=source_artifact,
    )
    return {"run_id": run_id, "observed_generation": 1, "representation": representation}


async def _validate_external_registration_context(
    domain_session: AsyncSession,
    *,
    experiment_group: str,
    sample_id: str | None,
) -> None:
    domain = await domain_session.get(MolBioNGSDomainState, experiment_group)
    if domain is None or domain.current_state_revision_id is None:
        raise ValueError("exact active Domain Experiment is required")
    if sample_id is None:
        return
    sample = await domain_session.get(MolBioNGSSample, sample_id)
    if (
        sample is None
        or sample.global_domain_experiment_id != experiment_group
        or sample.archived_at is not None
    ):
        raise ValueError("sample is not an active member of the exact Domain Experiment")


async def _external_registration_replay(
    session: AsyncSession,
    *,
    registration_key: str,
    candidate_id: str,
    source_sha256: str | None,
    sample_id: str | None,
    experiment_group: str,
) -> dict[str, Any] | None:
    run = (
        await session.execute(
            select(OntInstrumentRun).where(
                OntInstrumentRun.external_registration_key == registration_key
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    marker = run.last_minknow_payload if isinstance(run.last_minknow_payload, dict) else {}
    expected = {
        "candidate_id": candidate_id,
        "sample_id": sample_id,
    }
    if source_sha256 is not None:
        expected["source_sha256"] = source_sha256
    if (
        run.experiment_group != experiment_group
        or run.sample_id != sample_id
        or any(marker.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("external POD5 registration identity conflicts with existing authority")
    representations = await list_representations(
        session,
        run_id=run.id,
        observed_generation=run.observed_generation,
    )
    source = next((item for item in representations if item["role"] == "source"), None)
    if source is None or source["manifest_sha256"] is None:
        raise ValueError("external POD5 registration is incomplete")
    return {
        "run_id": run.id,
        "observed_generation": run.observed_generation,
        "representation": source,
        "already_registered": True,
    }


async def _adopt_legacy_external_registration(
    session: AsyncSession,
    *,
    registration_key: str,
    candidate_id: str,
    source_path: Path,
    source_artifact: dict[str, Any],
    sample_id: str | None,
    experiment_group: str,
) -> dict[str, Any] | None:
    """Upgrade one exact pre-key registration without creating a duplicate run."""
    sample_clause = (
        OntInstrumentRun.sample_id.is_(None)
        if sample_id is None
        else OntInstrumentRun.sample_id == sample_id
    )
    runs = (
        await session.execute(
            select(OntInstrumentRun).where(
                OntInstrumentRun.external_registration_key.is_(None),
                OntInstrumentRun.experiment_group == experiment_group,
                sample_clause,
            )
        )
    ).scalars().all()
    identity_values = {
        "external_source_device": source_artifact["device"],
        "external_source_inode": source_artifact["inode"],
        "external_source_bytes": source_artifact["bytes"],
        "external_source_mtime_ns": source_artifact["mtime_ns"],
        "external_source_ctime_ns": source_artifact["ctime_ns"],
        "external_source_root_device": source_artifact["governed_root_device"],
        "external_source_root_inode": source_artifact["governed_root_inode"],
        "external_source_relative_path": source_artifact["governed_relative_path"],
    }
    matches: list[tuple[OntInstrumentRun, dict[str, Any]]] = []
    for run in runs:
        marker = run.last_minknow_payload if isinstance(run.last_minknow_payload, dict) else {}
        if marker.get("schema") != "bms.ont.external-raw-signal-registration.v1":
            continue
        source = (
            await session.execute(
                select(OntRawSignalRepresentation).where(
                    OntRawSignalRepresentation.run_id == run.id,
                    OntRawSignalRepresentation.observed_generation == run.observed_generation,
                    OntRawSignalRepresentation.role == "source",
                )
            )
        ).scalar_one_or_none()
        artifacts = (
            source.artifact_manifest.get("artifacts", [])
            if source is not None and isinstance(source.artifact_manifest, dict)
            else []
        )
        exact = [
            item
            for item in artifacts
            if isinstance(item, dict)
            and item.get("kind") == "pod5"
            and item.get("path") == str(source_path)
            and item.get("bytes") == source_artifact["bytes"]
            and item.get("sha256") == source_artifact["sha256"]
        ]
        if len(exact) != 1:
            continue
        matches.append((run, marker))
    if len(matches) > 1:
        raise ValueError("ambiguous legacy external POD5 registration authority")
    if not matches:
        return None
    run, marker = matches[0]
    upgraded_marker = {
        **marker,
        "candidate_id": candidate_id,
        "source_sha256": source_artifact["sha256"],
        "sample_id": sample_id,
    }
    for attempt in range(3):
        try:
            async with session.begin_nested():
                adoption = await session.execute(
                    update(OntInstrumentRun)
                    .where(
                        OntInstrumentRun.id == run.id,
                        OntInstrumentRun.external_registration_key.is_(None),
                    )
                    .values(
                        external_registration_key=registration_key,
                        last_minknow_payload=upgraded_marker,
                        **identity_values,
                    )
                    .execution_options(synchronize_session=False)
                )
        except (IntegrityError, OperationalError):
            adoption = None
        session.expire_all()
        replay = await _external_registration_replay(
            session,
            registration_key=registration_key,
            candidate_id=candidate_id,
            source_sha256=source_artifact["sha256"],
            sample_id=sample_id,
            experiment_group=experiment_group,
        )
        if replay is not None:
            return replay
        if adoption is not None and adoption.rowcount == 1:
            raise ValueError("legacy external POD5 adoption was not durably replayable")
        if attempt < 2:
            await asyncio.sleep(0.05 * (attempt + 1))
    raise ValueError("legacy external POD5 registration conflicted with concurrent authority")


async def _backfill_keyed_external_source_identity(
    session: AsyncSession,
    *,
    registration_key: str,
    source_path: Path,
    source_artifact: dict[str, Any],
) -> None:
    run = (
        await session.execute(
            select(OntInstrumentRun).where(
                OntInstrumentRun.external_registration_key == registration_key
            )
        )
    ).scalar_one_or_none()
    if run is None or run.external_source_device is not None:
        return
    source = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.run_id == run.id,
                OntRawSignalRepresentation.observed_generation == run.observed_generation,
                OntRawSignalRepresentation.role == "source",
            )
        )
    ).scalar_one_or_none()
    artifacts = (
        source.artifact_manifest.get("artifacts", [])
        if source is not None and isinstance(source.artifact_manifest, dict)
        else []
    )
    exact = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("kind") == "pod5"
        and item.get("path") == str(source_path)
        and item.get("bytes") == source_artifact["bytes"]
        and item.get("sha256") == source_artifact["sha256"]
    ]
    if len(exact) != 1:
        raise ValueError("legacy external POD5 authority does not match the selected source")
    await session.execute(
        update(OntInstrumentRun)
        .where(
            OntInstrumentRun.id == run.id,
            OntInstrumentRun.external_source_device.is_(None),
        )
        .values(
            external_source_device=source_artifact["device"],
            external_source_inode=source_artifact["inode"],
            external_source_bytes=source_artifact["bytes"],
            external_source_mtime_ns=source_artifact["mtime_ns"],
            external_source_ctime_ns=source_artifact["ctime_ns"],
            external_source_root_device=source_artifact["governed_root_device"],
            external_source_root_inode=source_artifact["governed_root_inode"],
            external_source_relative_path=source_artifact["governed_relative_path"],
        )
        .execution_options(synchronize_session=False)
    )
    session.expire_all()


async def register_external_pod5_candidate(
    session: AsyncSession,
    domain_session: AsyncSession,
    *,
    candidate_id: str,
    sample_id: str | None,
    experiment_group: str,
) -> dict[str, Any]:
    """Immutably register one server-governed POD5 candidate as a sealed run."""
    experiment_group = experiment_group.strip()
    if not experiment_group:
        raise ValueError("exact Domain Experiment ID is required")
    registration_key = _digest({
        "candidate_id": candidate_id,
        "experiment_group": experiment_group,
        "sample_id": sample_id,
    })
    replay = await _external_registration_replay(
        session,
        registration_key=registration_key,
        candidate_id=candidate_id,
        source_sha256=None,
        sample_id=sample_id,
        experiment_group=experiment_group,
    )
    if replay is not None:
        return replay
    # Release the read snapshot before taking serialized SQLite adoption authority.
    await session.rollback()
    await _validate_external_registration_context(
        domain_session,
        experiment_group=experiment_group,
        sample_id=sample_id,
    )
    source_path, source_fd, root_fd, relative_path = _open_descriptor_candidate(candidate_id)
    try:
        probe = _file_artifact(source_path, "candidate", kind="pod5", opened_fd=source_fd)
        root_info = os.fstat(root_fd)
        root_path = source_path
        for _part in Path(relative_path).parts:
            root_path = root_path.parent
        probe.update({
            "governed_root_path": str(root_path),
            "governed_root_device": root_info.st_dev,
            "governed_root_inode": root_info.st_ino,
            "governed_relative_path": relative_path,
        })
    except BaseException:
        os.close(source_fd)
        os.close(root_fd)
        raise
    try:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
    except BaseException:
        os.close(source_fd)
        os.close(root_fd)
        raise
    _hold_source_descriptors_through_transaction(session, [source_fd, root_fd])

    await _backfill_keyed_external_source_identity(
        session,
        registration_key=registration_key,
        source_path=source_path,
        source_artifact=probe,
    )
    replay = await _external_registration_replay(
        session,
        registration_key=registration_key,
        candidate_id=candidate_id,
        source_sha256=probe["sha256"],
        sample_id=sample_id,
        experiment_group=experiment_group,
    )
    if replay is not None:
        return replay

    adopted = await _adopt_legacy_external_registration(
        session,
        registration_key=registration_key,
        candidate_id=candidate_id,
        source_path=source_path,
        source_artifact=probe,
        sample_id=sample_id,
        experiment_group=experiment_group,
    )
    if adopted is not None:
        return adopted

    replay = await _external_registration_replay(
        session,
        registration_key=registration_key,
        candidate_id=candidate_id,
        source_sha256=probe["sha256"],
        sample_id=sample_id,
        experiment_group=experiment_group,
    )
    if replay is not None:
        return replay

    input_file_id = str(uuid5(
        NAMESPACE_URL,
        f"bms:external-pod5:{candidate_id}:{probe['sha256']}",
    ))
    probe["artifact_id"] = input_file_id
    await session.execute(
        sqlite_insert(InputFile.__table__).values(
            id=input_file_id,
            filename=source_path.name,
            file_type="pod5",
            directory=str(source_path.parent),
            size_bytes=probe["bytes"],
        ).on_conflict_do_nothing(index_elements=["id"])
    )
    tracked = await session.get(InputFile, input_file_id)
    if (
        tracked is None
        or tracked.filename != source_path.name
        or tracked.directory != str(source_path.parent)
        or tracked.size_bytes != probe["bytes"]
    ):
        raise ValueError("tracked POD5 identity conflicts with existing authority")

    try:
        async with session.begin_nested():
            result = await create_external_run_registration(
                session,
                format="pod5",
                input_file_id=input_file_id,
                index_input_file_id=None,
                source_fidelity="native",
                sample_id=sample_id,
                experiment_group=experiment_group,
                external_registration_key=registration_key,
                candidate_id=candidate_id,
                source_sha256=probe["sha256"],
                source_path_override=source_path,
                source_artifact_override=probe,
            )
    except IntegrityError:
        replay = await _external_registration_replay(
            session,
            registration_key=registration_key,
            candidate_id=candidate_id,
            source_sha256=probe["sha256"],
            sample_id=sample_id,
            experiment_group=experiment_group,
        )
        if replay is None:
            raise ValueError("external POD5 registration conflicted with concurrent authority")
        return replay

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
    from services.ont_signal_workbench import workbench_capabilities

    signal_capabilities = await workbench_capabilities(
        session, run_id=run_id, observed_generation=observed_generation
    )
    signal_modes = {
        name: {
            **signal_capabilities["modes"][name],
            "representation_id": ready_blow5["representation_id"] if ready_blow5 else None,
        }
        for name in ("signal_to_read", "signal_to_reference", "signal_pileup")
    }
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
            **signal_modes,
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
    elif source.source_kind == "minknow_live":
        snapshot["conversion_mode"] = "live_minknow_pod5_chunk"
        snapshot["active_acquisition_pressure"] = "active"
        snapshot["acquisition_pressure_source"] = "live_minknow_acquisition"
        snapshot["retention_policy"] = raw_signal_retention_policy()
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
    live_chunk = snapshot.get("conversion_mode") == "live_minknow_pod5_chunk"
    if snapshot["active_acquisition_pressure"] != "clear" and not (
        live_chunk and snapshot["active_acquisition_pressure"] == "active" and live_conversion_enabled()
    ):
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


def _external_source_identity(run: OntInstrumentRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    values = {
        "device": run.external_source_device,
        "inode": run.external_source_inode,
        "bytes": run.external_source_bytes,
        "mtime_ns": run.external_source_mtime_ns,
        "ctime_ns": run.external_source_ctime_ns,
        "governed_root_device": run.external_source_root_device,
        "governed_root_inode": run.external_source_root_inode,
        "governed_relative_path": run.external_source_relative_path,
    }
    if any(value is None for value in values.values()):
        return None
    return {
        **{key: int(values[key]) for key in (
            "device", "inode", "bytes", "mtime_ns", "ctime_ns",
            "governed_root_device", "governed_root_inode",
        )},
        "governed_relative_path": str(values["governed_relative_path"]),
    }


def _source_paths(
    source: OntRawSignalRepresentation,
    source_identity: dict[str, Any] | None = None,
) -> list[Path]:
    manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    artifacts = [
        item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == "pod5" and item.get("path")
    ]
    identity_fields = ("device", "inode", "mtime_ns", "ctime_ns")
    if any(
        not _is_sha256(item.get("sha256"))
        or int(item.get("bytes") or 0) < 1
        or (
            source_identity is None
            and any(not isinstance(item.get(field), int) for field in identity_fields)
        )
        for item in artifacts
    ):
        raise ValueError(
            "POD5 source manifest lacks immutable size, digest, and filesystem identity authority"
        )
    paths = [Path(str(item["path"])) for item in artifacts]
    if not paths:
        raise ValueError("POD5 source has no governed artifact paths")
    return paths


def _conversion_commands(
    job: OntRawSignalDerivationJob,
    source: OntRawSignalRepresentation,
    snapshot: dict[str, Any],
    run: OntInstrumentRun | None = None,
) -> dict[str, Any]:
    stage = Path(snapshot["staging_root"]) / job.id / f"attempt-{job.attempt}"
    partitions = stage / "partitions"
    outputs = stage / "outputs"
    source_identity = _external_source_identity(run)
    inputs = _source_paths(source, source_identity)
    source_manifest = source.artifact_manifest if isinstance(source.artifact_manifest, dict) else {}
    artifact_by_path = {
        str(Path(str(item["path"]))): {
            **item,
            **(source_identity or {}),
        }
        for item in source_manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == "pod5" and item.get("path")
    }
    image_ref = _container_image_ref(snapshot)
    source_authorities: list[dict[str, Any]] = []
    input_args: list[str] = []
    for position, path in enumerate(inputs):
        artifact = artifact_by_path[str(path)]
        relative = Path(str(artifact.get("governed_relative_path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("POD5 source lacks valid governed-root relative-path authority")
        root_path = path
        for _part in relative.parts:
            root_path = root_path.parent
        if root_path / relative != path:
            raise ValueError("POD5 source path conflicts with governed-root authority")
        for field in ("governed_root_device", "governed_root_inode"):
            if not isinstance(artifact.get(field), int):
                raise ValueError("POD5 source lacks governed-root inode authority")
        input_args.append(f"/proc/self/fd/unbound-source-{position}")
        source_authorities.append({
            "path": str(path),
            "root_path": str(root_path),
            "relative_path": relative.as_posix(),
            "root_device": artifact["governed_root_device"],
            "root_inode": artifact["governed_root_inode"],
            "device": artifact["device"],
            "inode": artifact["inode"],
            "bytes": artifact["bytes"],
            "mtime_ns": artifact["mtime_ns"],
            "ctime_ns": artifact["ctime_ns"],
        })
    validator_input_args: list[str] = []
    for position, (path, mounted_path) in enumerate(zip(inputs, input_args, strict=True)):
        artifact = artifact_by_path[str(path)]
        validator_input_args.extend((
            "--pod5", mounted_path,
            "--governed-root", f"/proc/self/fd/unbound-root-{position}",
            "--expected-root-device", str(artifact["governed_root_device"]),
            "--expected-root-inode", str(artifact["governed_root_inode"]),
            "--expected-sha256", str(artifact["sha256"]),
            "--expected-size", str(artifact["bytes"]),
            "--expected-device", str(artifact["device"]),
            "--expected-inode", str(artifact["inode"]),
            "--expected-mtime-ns", str(artifact["mtime_ns"]),
            "--expected-ctime-ns", str(artifact["ctime_ns"]),
        ))
    validator_input_args.extend(("--fd-socket", "/stage/source-fd.sock"))
    base = [
        snapshot["container_runtime"], "run", "--rm", "--network=none", "--read-only",
        f"--user={snapshot['worker_uid']}:{snapshot['worker_gid']}",
        "--cpus=4", "--memory=16g", "--pids-limit=256", "--ulimit", "nofile=512:512",
        "--mount", f"type=bind,src={stage},dst=/stage",
    ]
    common = base + [image_ref]
    return {
        "stage": str(stage), "partitions": str(partitions), "outputs": str(outputs),
        "routing": str(stage / "routing.json"),
        "source_receipt": str(stage / "source-preflight-receipt.json"),
        "fd_socket": str(stage / "source-fd.sock"),
        "partition_map": str(stage / "partition-map.csv"),
        "common": common,
        "source_authorities": source_authorities,
        "validator_input_args": validator_input_args,
        "source_preflight": common + [
            "python3", "/opt/bms/ont_raw_signal_validate.py", "source-preflight", *validator_input_args,
            "--expected-acquisition-id", source.acquisition_id or "external-native",
            "--partition-map", "/stage/partition-map.csv",
            "--receipt", "/stage/source-preflight-receipt.json",
        ],
        "partition": common + [
            "python3", "/opt/bms/ont_raw_signal_validate.py", "partition-pod5",
            *validator_input_args, "--table", "/stage/partition-map.csv",
            "--read-id-column", "read_id", "--columns", "group",
            "--output", "/stage/partitions", "--template", "{group}.pod5",
            "--threads", "4", "--missing-ok",
            "--receipt", "/stage/partition-receipt.json",
        ],
    }


def pin_conversion_source_descriptors(commands: dict[str, Any]) -> list[int]:
    """Open exact governed root/source descriptors for the full conversion."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("raw-signal source leases require the main service thread")
    register_lease_break_listener(_record_source_lease_break)
    _SOURCE_LEASE_BREAK.clear()
    pinned: list[int] = []
    try:
        for authority in commands.get("source_authorities", []):
            root_fd = _open_absolute_directory_nofollow(Path(str(authority["root_path"])))
            pinned.append(root_fd)
            root_info = os.fstat(root_fd)
            if (root_info.st_dev, root_info.st_ino) != (
                int(authority["root_device"]),
                int(authority["root_inode"]),
            ):
                raise ValueError("governed raw-signal root identity changed before conversion")
            current_fd = os.dup(root_fd)
            try:
                parts = Path(str(authority["relative_path"])).parts
                for part in parts[:-1]:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=current_fd,
                    )
                    os.close(current_fd)
                    current_fd = next_fd
                file_fd = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            finally:
                os.close(current_fd)
            pinned.append(file_fd)
            info = os.fstat(file_fd)
            observed = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            expected = tuple(
                int(authority[field])
                for field in ("device", "inode", "bytes", "mtime_ns", "ctime_ns")
            )
            if not stat.S_ISREG(info.st_mode) or observed != expected:
                raise ValueError("raw-signal source filesystem identity changed before conversion")
            try:
                fcntl.fcntl(file_fd, fcntl.F_SETLEASE, fcntl.F_RDLCK)
            except OSError as exc:
                raise RuntimeError("raw-signal source read lease is unavailable") from exc
        return pinned
    except BaseException:
        for descriptor in pinned:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


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
    source = await session.get(OntRawSignalRepresentation, source_representation_id)
    if source is None or source.run_id != run_id or source.observed_generation != observed_generation or source.format not in {"pod5", "blow5"}:
        raise ValueError("source representation is not POD5 or external BLOW5 for the exact dataset generation")
    _require_derivable_generation(run, event, source)
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


def _open_descriptor_confined_artifact(
    artifact: Mapping[str, Any], *, authority: str
) -> tuple[Path, int]:
    root_raw = artifact.get("governed_root_path")
    relative_raw = artifact.get("governed_relative_path")
    if not isinstance(root_raw, str) or not os.path.isabs(root_raw):
        raise ValueError(f"{authority} lacks governed descriptor authority")
    if not isinstance(relative_raw, str):
        raise ValueError(f"{authority} lacks governed relative authority")
    relative = Path(relative_raw)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{authority} lacks governed relative authority")
    root = Path(os.path.abspath(root_raw))
    root_fd = current_fd = file_fd = -1
    try:
        root_fd = _open_absolute_directory_nofollow(root)
        root_info = os.fstat(root_fd)
        if (
            root_info.st_dev != artifact.get("governed_root_device")
            or root_info.st_ino != artifact.get("governed_root_inode")
        ):
            raise ValueError(f"{authority} governed root identity diverged")
        current_fd = os.dup(root_fd)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=current_fd,
        )
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_dev != artifact.get("device")
            or info.st_ino != artifact.get("inode")
            or info.st_size != artifact.get("bytes")
            or info.st_mtime_ns != artifact.get("mtime_ns")
            or info.st_ctime_ns != artifact.get("ctime_ns")
        ):
            raise ValueError(f"{authority} descriptor identity diverged")
        expected_path = root.joinpath(*relative.parts)
        if artifact.get("path") != str(expected_path):
            raise ValueError(f"{authority} path diverged from governed descriptor")
        result_fd = file_fd
        file_fd = -1
        return expected_path, result_fd
    except OSError as exc:
        raise ValueError(
            f"{authority} left governed descriptor boundary or used symbolic links"
        ) from exc
    finally:
        for descriptor in (file_fd, current_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


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
        routing_path, routing_fd = _open_descriptor_confined_artifact(
            routing_artifact, authority="routing artifact"
        )
        try:
            if int(routing_artifact.get("bytes", 0)) > 16 * 1024 * 1024:
                raise ValueError("routing artifact exceeds bounded policy")
            routing_bytes = b""
            while chunk := os.read(routing_fd, 1024 * 1024):
                routing_bytes += chunk
            if hashlib.sha256(routing_bytes).hexdigest() != routing_artifact.get("sha256"):
                raise ValueError("routing artifact digest diverged")
            routing = json.loads(routing_bytes.decode("utf-8"))
        finally:
            os.close(routing_fd)
        fingerprint = (routing.get("read_to_group") or {}).get(read_id)
        group = (routing.get("groups") or {}).get(fingerprint) if isinstance(fingerprint, str) else None
        if (
            not isinstance(group, dict)
            or not _is_sha256(fingerprint)
            or group.get("blow5") != f"{fingerprint}.blow5"
            or group.get("index") != f"{fingerprint}.blow5.idx"
        ):
            raise KeyError(read_id)
        blow5_artifact = next(
            (
                item
                for item in blow5_artifacts
                if item.get("partition_fingerprint") == fingerprint
            ),
            None,
        )
        index_artifact = next(
            (
                item
                for item in index_artifacts
                if item.get("partition_fingerprint") == fingerprint
            ),
            None,
        )
        if blow5_artifact is None or index_artifact is None:
            raise ValueError("routing selection is not confined to published partition descriptors")
        blow5, blow5_fd = _open_descriptor_confined_artifact(
            blow5_artifact, authority="partition BLOW5 artifact"
        )
        index, index_fd = _open_descriptor_confined_artifact(
            index_artifact, authority="partition index artifact"
        )
        os.close(blow5_fd)
        os.close(index_fd)
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
    commands = (
        _external_blow5_validation_commands(job, source, snapshot)
        if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID
        else _conversion_commands(job, source, snapshot, run)
    )
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


def _publication_component(value: str, *, authority: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\x00" in value:
        raise ValueError(f"raw-signal publication {authority} is invalid")
    return value


def _open_relative_directory(
    root_fd: int,
    components: tuple[str, ...],
    *,
    create: bool = False,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current_fd = os.dup(root_fd)
    try:
        for raw_component in components:
            component = _publication_component(
                raw_component, authority="directory component"
            )
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_publication_regular(directory_fd: int, name: str) -> int:
    component = _publication_component(name, authority="file component")
    fd = os.open(
        component,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory_fd,
    )
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ValueError("raw-signal publication member is not a regular file")
    return fd


def _read_publication_bytes(fd: int, *, maximum: int) -> bytes:
    before = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    size = 0
    while chunk := os.read(fd, min(1024 * 1024, maximum + 1 - size)):
        chunks.append(chunk)
        size += len(chunk)
        if size > maximum:
            raise ValueError("raw-signal publication member exceeds bounded policy")
    after = os.fstat(fd)
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if tuple(getattr(before, field) for field in identity_fields) != tuple(
        getattr(after, field) for field in identity_fields
    ):
        raise ValueError("raw-signal publication member changed while being read")
    return b"".join(chunks)


def _assert_publication_directory_identity(
    path: Path,
    retained_fd: int,
    *,
    authority: str,
) -> None:
    expected = os.fstat(retained_fd)
    reopened_fd = -1
    try:
        reopened_fd = _open_absolute_directory_nofollow(path)
        observed = os.fstat(reopened_fd)
    except OSError as exc:
        raise ValueError(
            f"raw-signal publication {authority} left descriptor confinement"
        ) from exc
    finally:
        if reopened_fd >= 0:
            os.close(reopened_fd)
    if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError(f"raw-signal publication {authority} identity changed")


async def publish_derivation(session: AsyncSession, job: OntRawSignalDerivationJob, source: OntRawSignalRepresentation, commands: dict[str, Any]) -> OntRawSignalRepresentation:
    configured_value = os.getenv(
        BLOW5_STAGING_ROOT_ENV, BLOW5_DEFAULT_STAGING_ROOT
    )
    configured_root = Path(configured_value).expanduser()
    if not configured_root.is_absolute() or ".." in configured_root.parts:
        raise ValueError("raw-signal publication staging root must be absolute and lexical")
    configured_root = Path(os.path.abspath(configured_root))
    snapshot = job.resource_snapshot if isinstance(job.resource_snapshot, dict) else {}
    expected_stage = configured_root / _publication_component(
        str(job.id), authority="job ID"
    ) / f"attempt-{int(job.attempt)}"
    expected_outputs = expected_stage / "outputs"
    expected_routing = expected_stage / "routing.json"
    if (
        snapshot.get("staging_root") != str(configured_root)
        or commands.get("stage") != str(expected_stage)
        or commands.get("outputs") != str(expected_outputs)
        or commands.get("routing") != str(expected_routing)
    ):
        raise ValueError("raw-signal publication path authority diverged")

    configured_parent = configured_root.parent
    publish_base = configured_parent / "ont-raw-signal"
    publish_root = publish_base / _publication_component(
        str(job.run_id), authority="run ID"
    ) / str(int(job.observed_generation))
    final_directory = publish_root / str(job.id)
    parent_fd = staging_root_fd = stage_parent_fd = stage_fd = -1
    publish_base_fd = publish_root_fd = final_fd = outputs_fd = -1
    recovering_atomic_publication = False
    renamed = False
    try:
        parent_fd = _open_absolute_directory_nofollow(configured_parent)
        staging_root_fd = _open_relative_directory(
            parent_fd, (configured_root.name,)
        )
        stage_parent_fd = _open_relative_directory(
            staging_root_fd, (str(job.id),)
        )
        try:
            stage_fd = _open_relative_directory(
                stage_parent_fd, (f"attempt-{int(job.attempt)}",)
            )
        except FileNotFoundError:
            stage_fd = -1

        publish_base_fd = _open_relative_directory(
            parent_fd, ("ont-raw-signal",), create=True
        )
        publish_root_fd = _open_relative_directory(
            publish_base_fd,
            (str(job.run_id), str(int(job.observed_generation))),
            create=True,
        )
        try:
            final_fd = _open_relative_directory(publish_root_fd, (str(job.id),))
        except FileNotFoundError:
            final_fd = -1

        if stage_fd < 0:
            if final_fd < 0:
                raise ValueError("validated BLOW5 publication unit is incomplete")
            recovering_atomic_publication = True
            unit_fd = final_fd
        else:
            if final_fd >= 0:
                raise ValueError("raw-signal publication destination already exists")
            unit_fd = stage_fd

        outputs_fd = _open_relative_directory(unit_fd, ("outputs",))
        routing_fd = semantic_fd = -1
        try:
            routing_fd = _open_publication_regular(unit_fd, "routing.json")
            semantic_fd = _open_publication_regular(
                unit_fd, "semantic-receipt.json"
            )
            routing_bytes = _read_publication_bytes(
                routing_fd, maximum=16 * 1024 * 1024
            )
            semantic_bytes = _read_publication_bytes(
                semantic_fd, maximum=16 * 1024 * 1024
            )
            semantic = json.loads(semantic_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("validated BLOW5 publication unit is incomplete") from exc
        finally:
            for descriptor in (semantic_fd, routing_fd):
                if descriptor >= 0:
                    os.close(descriptor)

        partition_counts = semantic.get("partition_counts")
        if (
            semantic.get("status") != "passed"
            or semantic.get("duplicate_read_ids") not in (0, False)
            or not isinstance(partition_counts, dict)
            or not partition_counts
            or semantic.get("routing_sha256")
            != hashlib.sha256(routing_bytes).hexdigest()
        ):
            raise ValueError("exhaustive semantic validation receipt did not pass")
        for fingerprint, read_count in partition_counts.items():
            if not _is_sha256(fingerprint) or int(read_count) < 1:
                raise ValueError("semantic receipt contains an invalid conversion partition")
            for suffix in (".blow5", ".blow5.idx"):
                descriptor = -1
                try:
                    descriptor = _open_publication_regular(
                        outputs_fd, f"{fingerprint}{suffix}"
                    )
                except OSError as exc:
                    raise ValueError(
                        "semantic receipt names an incomplete conversion partition"
                    ) from exc
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)

        if not recovering_atomic_publication:
            with os.scandir(outputs_fd) as entries:
                output_names = [entry.name for entry in entries]
            for name in output_names:
                descriptor = _open_publication_regular(outputs_fd, name)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            for name in ("routing.json", "semantic-receipt.json"):
                descriptor = _open_publication_regular(stage_fd, name)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.fsync(outputs_fd)
            os.fsync(stage_fd)
            if os.fstat(stage_parent_fd).st_dev != os.fstat(publish_root_fd).st_dev:
                raise ValueError("raw-signal publication is not on one atomic filesystem")
            _assert_publication_directory_identity(
                configured_parent, parent_fd, authority="configured parent"
            )
            _assert_publication_directory_identity(
                configured_root, staging_root_fd, authority="staging root"
            )
            _assert_publication_directory_identity(
                publish_base, publish_base_fd, authority="publication root"
            )
            _assert_publication_directory_identity(
                publish_root, publish_root_fd, authority="publication generation"
            )
            try:
                os.rename(
                    f"attempt-{int(job.attempt)}",
                    str(job.id),
                    src_dir_fd=stage_parent_fd,
                    dst_dir_fd=publish_root_fd,
                )
                renamed = True
                os.fsync(publish_root_fd)
                _assert_publication_directory_identity(
                    configured_parent, parent_fd, authority="configured parent"
                )
                _assert_publication_directory_identity(
                    configured_root, staging_root_fd, authority="staging root"
                )
                _assert_publication_directory_identity(
                    publish_base, publish_base_fd, authority="publication root"
                )
                _assert_publication_directory_identity(
                    publish_root, publish_root_fd, authority="publication generation"
                )
            except BaseException:
                if renamed:
                    try:
                        os.rename(
                            str(job.id),
                            f"attempt-{int(job.attempt)}",
                            src_dir_fd=publish_root_fd,
                            dst_dir_fd=stage_parent_fd,
                        )
                        os.fsync(stage_parent_fd)
                        os.fsync(publish_root_fd)
                    except OSError:
                        pass
                raise
        else:
            _assert_publication_directory_identity(
                configured_parent, parent_fd, authority="configured parent"
            )
            _assert_publication_directory_identity(
                configured_root, staging_root_fd, authority="staging root"
            )
            _assert_publication_directory_identity(
                publish_base, publish_base_fd, authority="publication root"
            )
            _assert_publication_directory_identity(
                publish_root, publish_root_fd, authority="publication generation"
            )

        governed_fd = unit_fd
        artifacts: list[dict[str, Any]] = []
        for fingerprint, read_count in sorted(partition_counts.items()):
            blow5_name = f"{fingerprint}.blow5"
            index_name = f"{fingerprint}.blow5.idx"
            blow5_fd = _open_publication_regular(outputs_fd, blow5_name)
            try:
                blow5_artifact = _file_artifact(
                    final_directory / "outputs" / blow5_name,
                    _id("ont-artifact"),
                    kind="blow5",
                    opened_fd=blow5_fd,
                    governed_root_path=final_directory,
                    governed_root_fd=governed_fd,
                    governed_relative_path=f"outputs/{blow5_name}",
                )
            finally:
                os.close(blow5_fd)
            index_fd = _open_publication_regular(outputs_fd, index_name)
            try:
                index_artifact = _file_artifact(
                    final_directory / "outputs" / index_name,
                    _id("ont-artifact"),
                    kind="blow5_index",
                    opened_fd=index_fd,
                    governed_root_path=final_directory,
                    governed_root_fd=governed_fd,
                    governed_relative_path=f"outputs/{index_name}",
                )
            finally:
                os.close(index_fd)
            blow5_artifact.update({"partition_fingerprint": fingerprint, "read_count": int(read_count)})
            index_artifact.update({"partition_fingerprint": fingerprint})
            artifacts.extend((blow5_artifact, index_artifact))
        routing_fd = _open_publication_regular(governed_fd, "routing.json")
        try:
            artifacts.append(
                _file_artifact(
                    final_directory / "routing.json",
                    _id("ont-artifact"),
                    kind="read_routing",
                    opened_fd=routing_fd,
                    governed_root_path=final_directory,
                    governed_root_fd=governed_fd,
                    governed_relative_path="routing.json",
                )
            )
        finally:
            os.close(routing_fd)
    except OSError as exc:
        raise ValueError(
            "raw-signal publication left descriptor confinement or used symbolic links"
        ) from exc
    finally:
        for descriptor in (
            outputs_fd,
            final_fd,
            stage_fd,
            publish_root_fd,
            publish_base_fd,
            stage_parent_fd,
            staging_root_fd,
            parent_fd,
        ):
            if descriptor >= 0:
                os.close(descriptor)

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
            run = await session.get(OntInstrumentRun, row.run_id)
            commands = _conversion_commands(
                row,
                source,
                dict(row.resource_snapshot or {}),
                run,
            )
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

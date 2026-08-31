"""Job-scoped, fail-closed ONT alignment-session and bounded read helpers."""

from __future__ import annotations

import base64
import hashlib
import fcntl
import heapq
import json
import math
import mimetypes
import os
import random
import re
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from collections import OrderedDict
from contextlib import contextmanager
from functools import cmp_to_key, lru_cache, wraps
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, cast

import rfc8785
import pysam

from paths import get_analysis_cache_dir, get_results_dir
from services.ont_ngs_contract import DORADO_LOCK_PATH
from services.ngs_molbio_source_authority import SourceBuildRevisionError, source_build_revision
from services.ngs_molbio_runtime_status import NgsMolBioRuntimeAuthorityError, runtime_implementation_record

SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,255}$")
SAFE_CONTIG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
DIMER_TOKENS = ("dimer", "multimer", "concatemer")
MAX_MANIFESTS = 64
MAX_READ_PAGE = 200
MAX_SEQUENCE_PAGE = 20
MAX_READ_CURSOR = 9_999
MAX_READ_SCAN = 10_000
MAX_SORTABLE_READ_CURSOR_BYTES = 1024
ALIGNMENT_PREVIEW_TARGET_READS = 2_000
ALIGNMENT_PREVIEW_POLICY = "primary-read-presentation-v3"
ALIGNMENT_PRESENTATION_POLICY_VERSION = 3
ALIGNMENT_PREVIEW_MAX_BYTES = 32 * 1024 * 1024
ALIGNMENT_PREVIEW_INDEX_MAX_BYTES = 8 * 1024 * 1024
ALIGNMENT_COVERAGE_MAX_BYTES = 4 * 1024 * 1024
ALIGNMENT_PRESENTATION_MANIFEST_MAX_BYTES = 1024 * 1024
ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES = (
    ALIGNMENT_PREVIEW_MAX_BYTES
    + ALIGNMENT_PREVIEW_INDEX_MAX_BYTES
    + ALIGNMENT_COVERAGE_MAX_BYTES
    + ALIGNMENT_PRESENTATION_MANIFEST_MAX_BYTES
)
ALIGNMENT_PRESENTATION_WORK_MAX_BYTES = 256 * 1024 * 1024
ALIGNMENT_PRESENTATION_CACHE_MAX_ENTRIES = 2
ALIGNMENT_PRESENTATION_CACHE_MAX_BYTES = max(
    2 * ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES,
    ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES + ALIGNMENT_PRESENTATION_WORK_MAX_BYTES,
)
ALIGNMENT_PREVIEW_MAX_RECORDS = 20_000
ALIGNMENT_COVERAGE_MAX_BINS = 4_096
ALIGNMENT_PRESENTATION_MAX_SECONDS = 120.0
ALIGNMENT_SELECTED_IDS_MAX_BYTES = 2 * 1024 * 1024
LOCUS_MAX_SPAN = 1_000_000
LOCUS_MAX_READS = 5_000
LOCUS_MAX_RECORDS = 20_000
LOCUS_MAX_BYTES = 64 * 1024 * 1024
LOCUS_MAX_SECONDS = 30.0
LOCUS_INDEX_MAX_BYTES = 16 * 1024 * 1024
LOCUS_MANIFEST_MAX_BYTES = 1024 * 1024
LOCUS_CACHE_MAX_BYTES = 512 * 1024 * 1024
LOCUS_CACHE_MAX_ENTRIES = 128
LOCUS_GENERATION_CONCURRENCY = 1
SORTABLE_READ_FIELDS = frozenset({
    "read_id", "length", "mean_quality", "mapq", "aligned_query_bases",
    "aligned_reference_bases", "inserted_bases", "deleted_bases",
    "skipped_reference_bases", "clipped_bases", "edit_distance",
    "reference_substitution_count", "reference_substitution_rate", "aligned_fraction",
    "clipped_fraction", "reference_disagreement_rate", "sample_count",
    "duration_seconds", "sampling_rate_hz", "current_mean_pa", "current_median_pa",
    "current_stddev_pa", "current_mad_pa", "current_min_pa", "current_max_pa",
    "channel_number", "start_mux", "acquisition_start_seconds",
    "time_since_mux_change_seconds", "median_before_pa", "open_pore_level_pa",
    "minknow_event_rate_per_second", "dorado_emission_rate_bases_per_second",
    "mapped_signal_span_samples", "samples_per_aligned_reference_base",
})
_alignment_preview_lock = threading.Lock()
LINKED_REPORT_ROLES = frozenset(
    {
        "alignment",
        "alignment_index",
        "reference",
        "reference_index",
        "coverage_depth",
        "gc_content",
        "position_gradient",
        "gc_zscore",
        "split_read_density",
        "soft_clip_density",
        "junction_hotspots",
    }
)
SESSION_MODES = frozenset({"primary", "dimer_candidates"})
MANIFEST_SCHEMA = "sequence_qc.manifest.v1"
MANIFEST_SCHEMA_VERSION = 2
SNAPSHOT_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
SNAPSHOT_CACHE_MAX_ENTRIES = 64
SNAPSHOT_CHUNK_BYTES = 1024 * 1024
_snapshot_cache_lock = threading.RLock()
_snapshot_cache_condition = threading.Condition(_snapshot_cache_lock)
_snapshot_cache_owner: tempfile.TemporaryDirectory[str] | None = None
_snapshot_cache_dir: Path | None = None
_snapshot_cache: OrderedDict[str, int] = OrderedDict()
_snapshot_cache_leases: dict[str, int] = {}
_snapshot_cache_bytes = 0
_snapshot_inflight: set[str] = set()
_snapshot_inflight_bytes = 0


class AlignmentSessionError(ValueError):
    """Raised when a requested session or artifact is unsafe or unavailable."""


class _AlignmentDerivativeByteLimit(AlignmentSessionError):
    """Raised when the kernel-enforced derivative file limit is reached."""


class _SnapshotLease:
    def __init__(self, handle: BinaryIO, digest: str) -> None:
        self._handle = handle
        self._digest = digest
        self._closed = False

    def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def fileno(self) -> int:
        return self._handle.fileno()

    def __iter__(self):
        return iter(self._handle)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handle.close()
        finally:
            with _snapshot_cache_condition:
                leases = _snapshot_cache_leases.get(self._digest, 0)
                if leases <= 1:
                    _snapshot_cache_leases.pop(self._digest, None)
                else:
                    _snapshot_cache_leases[self._digest] = leases - 1
                _snapshot_cache_condition.notify_all()

    def __enter__(self) -> _SnapshotLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _open_regular_file_no_symlinks(path: Path) -> BinaryIO:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise AlignmentSessionError("unsafe artifact path")
    descriptor_parts = absolute.parts
    if (
        len(descriptor_parts) >= 6
        and descriptor_parts[1:4] == ("proc", "self", "fd")
        and descriptor_parts[4].isdigit()
    ):
        try:
            descriptor = os.dup(int(descriptor_parts[4]))
        except OSError as exc:
            raise AlignmentSessionError("unsafe artifact path") from exc
        components = descriptor_parts[5:]
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise AlignmentSessionError("unsafe artifact path")
    else:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        components = descriptor_parts[1:]
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            if not final:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise AlignmentSessionError("unsafe artifact: non-regular file")
        return os.fdopen(descriptor, "rb", closefd=True)
    except (OSError, ValueError) as exc:
        os.close(descriptor)
        raise AlignmentSessionError("unsafe artifact path") from exc
    except Exception:
        os.close(descriptor)
        raise


def _open_directory_descriptor_no_symlinks(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) >= 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit():
        try:
            descriptor = os.dup(int(parts[4]))
        except OSError as exc:
            raise AlignmentSessionError("presentation root is unsafe") from exc
        components = parts[5:]
    else:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        components = parts[1:]
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise AlignmentSessionError("presentation root is unsafe")
        for component in components:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except (OSError, ValueError) as exc:
        os.close(descriptor)
        raise AlignmentSessionError("presentation root is unsafe") from exc
    except Exception:
        os.close(descriptor)
        raise


def _snapshot_cache_directory() -> Path:
    global _snapshot_cache_owner, _snapshot_cache_dir
    with _snapshot_cache_condition:
        if _snapshot_cache_dir is None:
            _snapshot_cache_owner = tempfile.TemporaryDirectory(prefix="bms-alignment-snapshots-")
            _snapshot_cache_dir = Path(_snapshot_cache_owner.name)
            _snapshot_cache_dir.chmod(0o700)
        return _snapshot_cache_dir


def _discard_cached_snapshot_locked(digest: str) -> None:
    global _snapshot_cache_bytes
    if _snapshot_cache_leases.get(digest, 0) > 0:
        return
    discarded_size = _snapshot_cache.pop(digest, None)
    if discarded_size is not None:
        _snapshot_cache_bytes = max(0, _snapshot_cache_bytes - discarded_size)
    _snapshot_cache_leases.pop(digest, None)
    if _snapshot_cache_dir is not None:
        (_snapshot_cache_dir / digest).unlink(missing_ok=True)


def _cached_snapshot_locked(expected_sha256: str, expected_size: int) -> BinaryIO | None:
    cached_size = _snapshot_cache.get(expected_sha256)
    if cached_size != expected_size or _snapshot_cache_dir is None:
        return None
    cache_path = _snapshot_cache_dir / expected_sha256
    try:
        snapshot = _open_regular_file_no_symlinks(cache_path)
    except AlignmentSessionError:
        _discard_cached_snapshot_locked(expected_sha256)
        return None
    if os.fstat(snapshot.fileno()).st_size != expected_size:
        snapshot.close()
        _discard_cached_snapshot_locked(expected_sha256)
        return None
    _snapshot_cache.move_to_end(expected_sha256)
    _snapshot_cache_leases[expected_sha256] = _snapshot_cache_leases.get(expected_sha256, 0) + 1
    return cast(BinaryIO, _SnapshotLease(snapshot, expected_sha256))


def _cached_snapshot(expected_sha256: str, expected_size: int) -> BinaryIO | None:
    with _snapshot_cache_condition:
        return _cached_snapshot_locked(expected_sha256, expected_size)


def _evict_for_reservation_locked(required_size: int) -> bool:
    while (
        _snapshot_cache_bytes + _snapshot_inflight_bytes + required_size > SNAPSHOT_CACHE_MAX_BYTES
        or len(_snapshot_cache) + len(_snapshot_inflight) >= SNAPSHOT_CACHE_MAX_ENTRIES
    ):
        evictable = next(
            (digest for digest in _snapshot_cache if _snapshot_cache_leases.get(digest, 0) == 0),
            None,
        )
        if evictable is None:
            return False
        _discard_cached_snapshot_locked(evictable)
    return True


def _reserve_snapshot(digest: str, size: int) -> BinaryIO | None:
    global _snapshot_inflight_bytes
    with _snapshot_cache_condition:
        while True:
            cached = _cached_snapshot_locked(digest, size)
            if cached is not None:
                return cached
            if digest in _snapshot_inflight:
                _snapshot_cache_condition.wait()
                continue
            if _evict_for_reservation_locked(size):
                _snapshot_inflight.add(digest)
                _snapshot_inflight_bytes += size
                return None
            raise AlignmentSessionError("snapshot cache capacity unavailable")
def _release_snapshot_reservation(digest: str, size: int) -> None:
    global _snapshot_inflight_bytes
    with _snapshot_cache_condition:
        if digest in _snapshot_inflight:
            _snapshot_inflight.remove(digest)
            _snapshot_inflight_bytes = max(0, _snapshot_inflight_bytes - size)
        _snapshot_cache_condition.notify_all()


def _publish_snapshot(snapshot: BinaryIO, snapshot_path: Path, digest: str, size: int) -> BinaryIO:
    global _snapshot_cache_bytes, _snapshot_inflight_bytes
    with _snapshot_cache_condition:
        cache_dir = _snapshot_cache_directory()
        cache_path = cache_dir / digest
        os.chmod(snapshot_path, 0o400)
        os.replace(snapshot_path, cache_path)
        snapshot.close()
        try:
            readonly_snapshot = _open_regular_file_no_symlinks(cache_path)
        except Exception:
            cache_path.unlink(missing_ok=True)
            raise
        replaced_size = _snapshot_cache.pop(digest, None)
        if replaced_size is not None:
            _snapshot_cache_bytes -= replaced_size
        _snapshot_cache[digest] = size
        _snapshot_cache_bytes += size
        _snapshot_cache_leases[digest] = 1
        _snapshot_inflight.discard(digest)
        _snapshot_inflight_bytes = max(0, _snapshot_inflight_bytes - size)
        _snapshot_cache_condition.notify_all()
        return cast(BinaryIO, _SnapshotLease(readonly_snapshot, digest))


def verify_current_artifact_bytes(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """Verify the current descriptor-backed source without consulting the snapshot cache."""

    if expected_size < 0 or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise AlignmentSessionError("artifact integrity metadata is invalid")
    source = _open_regular_file_no_symlinks(path)
    try:
        if os.fstat(source.fileno()).st_size != expected_size:
            raise AlignmentSessionError("artifact integrity size mismatch")
        digest = hashlib.sha256()
        copied = 0
        while copied <= expected_size:
            chunk = source.read(min(SNAPSHOT_CHUNK_BYTES, expected_size + 1 - copied))
            if not chunk:
                break
            copied += len(chunk)
            digest.update(chunk)
        if copied != expected_size or digest.hexdigest() != expected_sha256:
            raise AlignmentSessionError("artifact integrity digest mismatch")
    finally:
        source.close()


def open_verified_artifact_snapshot(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> BinaryIO:
    """Return a private exact-byte snapshot bound to the declared digest."""
    if expected_size < 0 or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise AlignmentSessionError("artifact integrity metadata is invalid")
    if expected_size > SNAPSHOT_CACHE_MAX_BYTES:
        raise AlignmentSessionError("artifact exceeds snapshot limit")
    cached = _reserve_snapshot(expected_sha256, expected_size)
    if cached is not None:
        return cached

    source: BinaryIO | None = None
    temporary: BinaryIO | None = None
    temporary_path: Path | None = None
    reservation_active = True
    try:
        source = _open_regular_file_no_symlinks(path)
        cache_dir = _snapshot_cache_directory()
        temporary_file = tempfile.NamedTemporaryFile(mode="w+b", dir=cache_dir, delete=False)
        temporary = cast(BinaryIO, temporary_file)
        temporary_path = Path(temporary_file.name)
        digest = hashlib.sha256()
        copied = 0
        if os.fstat(source.fileno()).st_size != expected_size:
            raise AlignmentSessionError("artifact integrity size mismatch")
        while copied <= expected_size:
            chunk = source.read(min(SNAPSHOT_CHUNK_BYTES, expected_size + 1 - copied))
            if not chunk:
                break
            copied += len(chunk)
            digest.update(chunk)
            temporary.write(chunk)
        if copied != expected_size or digest.hexdigest() != expected_sha256:
            raise AlignmentSessionError("artifact integrity digest mismatch")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.seek(0)
        snapshot = _publish_snapshot(temporary, temporary_path, expected_sha256, expected_size)
        reservation_active = False
        temporary = None
        temporary_path = None
        return snapshot
    except Exception:
        if temporary is not None:
            temporary.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            source.close()
        if reservation_active:
            _release_snapshot_reservation(expected_sha256, expected_size)


def _safe_job_root(
    job_id: str,
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    *,
    pinned_root_descriptor: bool = False,
) -> tuple[str, Path]:
    normalized = job_id.strip()
    if (
        not normalized
        or "/" in normalized
        or "\\" in normalized
        or ".." in normalized
        or not SAFE_JOB_ID_RE.fullmatch(normalized)
    ):
        raise AlignmentSessionError(f"unsafe job_id: {job_id!r}")
    root = Path(results_dir) if results_dir is not None else get_results_dir()
    root = root.expanduser().resolve()
    if job_output_dir is None:
        declared_job_root = root / normalized
    else:
        supplied = Path(job_output_dir).expanduser()
        declared_job_root = supplied if supplied.is_absolute() else root / supplied
    descriptor_match = re.fullmatch(r"/proc/self/fd/([0-9]+)", str(declared_job_root))
    if pinned_root_descriptor:
        if descriptor_match is None:
            raise AlignmentSessionError(f"pinned job root is not a process descriptor for {job_id!r}")
        try:
            descriptor_stat = os.fstat(int(descriptor_match.group(1)))
        except OSError as exc:
            raise AlignmentSessionError(f"pinned job root descriptor is unavailable for {job_id!r}") from exc
        if not stat.S_ISDIR(descriptor_stat.st_mode):
            raise AlignmentSessionError(f"pinned job root descriptor is not a directory for {job_id!r}")
    elif declared_job_root.is_symlink():
        raise AlignmentSessionError(f"unsafe symlink job root for {job_id!r}")
    job_root_resolved = declared_job_root.resolve(strict=True)
    try:
        job_root_resolved.relative_to(root)
    except ValueError as exc:
        raise AlignmentSessionError(f"unsafe job root for {job_id!r}") from exc
    if not stat.S_ISDIR(os.stat(declared_job_root).st_mode):
        raise AlignmentSessionError(f"alignment sessions not found for job_id: {normalized}")
    return normalized, declared_job_root if pinned_root_descriptor else job_root_resolved


def _sha256_file_and_size(path: Path) -> tuple[str, int]:
    """Hash one no-follow descriptor and return its size from the same descriptor."""
    handle = _open_regular_file_no_symlinks(path)
    digest = hashlib.sha256()
    try:
        size_bytes = os.fstat(handle.fileno()).st_size
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest(), size_bytes
    finally:
        handle.close()


def _sha256_file(path: Path) -> str:
    digest, _size_bytes = _sha256_file_and_size(path)
    return digest


def _regular_file_inside(path: Path, job_root: Path) -> tuple[Path | None, str | None]:
    try:
        root = job_root.resolve(strict=True)
        lexical = Path(os.path.abspath(path))
        lexical_root = Path(os.path.abspath(job_root))
        relative = lexical.relative_to(lexical_root)
        current = job_root
        for component in relative.parts:
            current = current / component
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return None, "unsafe artifact: symlink component"
        if not stat.S_ISREG(current.lstat().st_mode):
            return None, "unsafe artifact: non-regular file"
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        return current, None
    except (FileNotFoundError, OSError, ValueError, RuntimeError):
        return None, "unsafe or missing artifact"


def _manifest_records(job_id: str, job_root: Path) -> list[dict[str, Any]]:
    manifests = sorted(job_root.glob("**/qc_manifest.json"))
    if len(manifests) > MAX_MANIFESTS:
        raise AlignmentSessionError(f"too many manifests below job root ({len(manifests)} > {MAX_MANIFESTS})")
    records: list[dict[str, Any]] = []
    for manifest_path in manifests:
        safe_manifest, error = _regular_file_inside(manifest_path, job_root)
        if safe_manifest is None:
            continue
        try:
            payload, _raw_bytes, manifest_digest, manifest_size = _read_bounded_json_nofollow(
                safe_manifest,
                label="alignment-session manifest",
            )
        except AlignmentSessionError:
            continue
        rel_manifest = safe_manifest.relative_to(job_root).as_posix()
        session_metadata = payload.get("alignment_session") if isinstance(payload, dict) else None
        session_mode = session_metadata.get("mode") if isinstance(session_metadata, dict) else None
        records.append({
            "kind": "__manifest_authority__",
            "manifest": rel_manifest,
            "declared_path": "qc_manifest.json",
            "session_mode": session_mode if session_mode in SESSION_MODES else "primary",
            "source_manifest_sha256": manifest_digest,
            "source_manifest_size_bytes": manifest_size,
            "reference_topology": (
                payload.get("summary", {}).get("reference_topology")
                if isinstance(payload, dict) and isinstance(payload.get("summary"), dict)
                else None
            ),
            "path": None,
            "error": None,
        })
        manifest_error: str | None = None
        if not isinstance(payload, dict):
            manifest_error = "manifest root must be a JSON object"
        elif payload.get("artifact_schema_version") != MANIFEST_SCHEMA_VERSION:
            manifest_error = f"unsupported manifest schema version: {payload.get('artifact_schema_version')!r}"
        elif payload.get("schema") != MANIFEST_SCHEMA:
            manifest_error = "manifest schema is not the canonical sequence-QC schema"
        elif payload.get("job_id") != job_id:
            manifest_error = "manifest job_id does not match requested job"
        elif not isinstance(payload.get("workflow_id"), str) or not payload["workflow_id"].strip():
            manifest_error = "manifest workflow_id is missing"
        elif payload.get("input_mode") not in {"fastq", "bam", "pod5"}:
            manifest_error = "manifest input_mode is invalid"
        elif payload.get("analysis_status") != "completed":
            manifest_error = "manifest analysis_status is invalid"
        elif not isinstance(session_metadata, dict):
            manifest_error = "manifest alignment_session metadata is missing"
        elif session_mode not in SESSION_MODES:
            manifest_error = "manifest alignment_session mode is invalid"
        elif re.fullmatch(
            r"[0-9a-f]{64}", str(session_metadata.get("reference_sequence_sha256") or "")
        ) is None:
            manifest_error = "manifest reference_sequence_sha256 is invalid"
        elif not isinstance(payload.get("artifacts"), list):
            manifest_error = "manifest artifacts must be a list"
        if manifest_error is not None:
            records.append(
                {
                    "kind": "__manifest_error__",
                    "manifest": rel_manifest,
                    "declared_path": "qc_manifest.json",
                    "session_mode": session_mode if session_mode in SESSION_MODES else "primary",
                    "path": None,
                    "error": manifest_error,
                    "manifest_error": manifest_error,
                }
            )
            continue
        for item in payload["artifacts"]:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path")
            if item.get("state") not in (None, "present") or not isinstance(raw_path, str) or not raw_path.strip():
                continue
            declared = Path(raw_path)
            if declared.is_absolute():
                records.append(
                    {
                        "kind": str(item.get("kind") or "artifact"),
                        "manifest": rel_manifest,
                        "declared_path": raw_path,
                        "path": None,
                        "error": "unsafe artifact: absolute path",
                    }
                )
                continue
            candidate = safe_manifest.parent / declared
            safe_path, path_error = _regular_file_inside(candidate, job_root)
            records.append(
                {
                    "kind": str(item.get("kind") or "artifact"),
                    "manifest": rel_manifest,
                    "declared_path": declared.as_posix(),
                    "declared_sha256": item.get("sha256"),
                    "declared_size_bytes": item.get("size_bytes"),
                    "workflow_id": payload.get("workflow_id"),
                    "input_mode": payload.get("input_mode"),
                    "session_mode": (
                        payload.get("alignment_session", {}).get("mode")
                        if isinstance(payload.get("alignment_session"), dict)
                        else None
                    ),
                    "reference_sequence_sha256": (
                        payload.get("alignment_session", {}).get("reference_sequence_sha256")
                        if isinstance(payload.get("alignment_session"), dict)
                        else None
                    ),
                    "source_reference_sequence_sha256": (
                        payload.get("alignment_session", {}).get("source_reference_sequence_sha256")
                        if isinstance(payload.get("alignment_session"), dict)
                        else None
                    ),
                    "source_manifest_sha256": manifest_digest,
                    "source_manifest_size_bytes": manifest_size,
                    "reference_topology": (
                        payload.get("reference", {}).get("topology")
                        if isinstance(payload.get("reference"), dict)
                        else payload.get("summary", {}).get("reference_topology")
                        if isinstance(payload.get("summary"), dict)
                        else None
                    ),
                    "path": safe_path,
                    "error": path_error,
                }
            )
    return records


def _is_dimer(record: dict[str, Any]) -> bool:
    mode = record.get("session_mode")
    if mode == "dimer_candidates":
        return True
    if mode == "primary":
        return False
    if str(record.get("kind") or "").lower() in {"dimer_alignment_bam", "dimer_alignment_bai"}:
        return True
    label = f"{record.get('manifest', '')}/{record.get('declared_path', '')}".lower()
    return any(token in label for token in DIMER_TOKENS)


def _dimer_mode_conflict(record: dict[str, Any]) -> bool:
    if record.get("session_mode") != "primary":
        return False
    if str(record.get("kind") or "").lower() in {"dimer_alignment_bam", "dimer_alignment_bai"}:
        return True
    label = f"{record.get('manifest', '')}/{record.get('declared_path', '')}".lower()
    return any(token in label for token in DIMER_TOKENS)


def _artifact_role(kind: str) -> str | None:
    normalized = kind.lower()
    if normalized in {"alignment_bam", "bam", "dimer_alignment_bam"}:
        return "alignment"
    if normalized in {"alignment_bai", "alignment_index", "bam_index", "dimer_alignment_bai"}:
        return "alignment_index"
    if normalized in {"reference", "reference_fasta"}:
        return "reference"
    if normalized in {"reference_index", "fasta_index", "reference_fai"}:
        return "reference_index"
    if normalized == "igv_coverage_depth":
        return "coverage_depth"
    if normalized == "igv_gc_content":
        return "gc_content"
    if normalized == "igv_position_gradient":
        return "position_gradient"
    if normalized == "igv_gc_zscore":
        return "gc_zscore"
    if normalized == "igv_split_read_density":
        return "split_read_density"
    if normalized == "igv_softclip_density":
        return "soft_clip_density"
    if normalized == "igv_junction_hotspots":
        return "junction_hotspots"
    if normalized == "igv_report":
        return "report"
    if normalized == "igv_track_config":
        return "track_config"
    return None


def _pick_bundle(records: list[dict[str, Any]], mode: str) -> tuple[dict[str, dict[str, Any]], str | None]:
    scoped = [record for record in records if _is_dimer(record) == (mode == "dimer_candidates")]
    manifests = sorted(
        {record["manifest"] for record in scoped},
        key=lambda value: ("fastq_qc" not in value.lower(), value),
    )
    unsafe_reason: str | None = None
    for manifest in manifests:
        by_role: dict[str, dict[str, Any]] = {}
        for record in scoped:
            if record["manifest"] != manifest:
                continue
            if record.get("manifest_error"):
                unsafe_reason = str(record["manifest_error"])
                continue
            if _dimer_mode_conflict(record):
                unsafe_reason = "contradictory primary session mode and dimer artifact metadata"
                continue
            role = _artifact_role(record["kind"])
            if role is None:
                continue
            if role in by_role:
                unsafe_reason = f"duplicate artifact role: {role}"
                continue
            if record.get("path") is None:
                unsafe_reason = str(record.get("error") or "unsafe artifact")
            by_role[role] = record
        if "alignment" in by_role or unsafe_reason:
            return by_role, unsafe_reason
    return {}, None


def _runtime_stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@dataclass(frozen=True)
class _PinnedSamtoolsCommand:
    argv: tuple[str, ...]
    pass_fds: tuple[int, ...]
    runtime_sha256: str | None
    runtime_size: int | None
    runtime_path: Path | None = None
    runtime_identity: tuple[int, int, int, int, int] | None = None
    runtime_directory_fd: int | None = None
    runtime_directory_identity: tuple[int, int, int, int, int] | None = None

    def __iter__(self):
        return iter(self.argv)

    def verify_runtime(self) -> None:
        authority = (
            self.runtime_sha256,
            self.runtime_size,
            self.runtime_path,
            self.runtime_identity,
            self.runtime_directory_fd,
            self.runtime_directory_identity,
        )
        if all(value is None for value in authority) and not self.pass_fds:
            return
        if any(value is None for value in authority) or not self.pass_fds:
            raise AlignmentSessionError("pinned NGS runtime authority is incomplete")
        assert self.runtime_path is not None
        assert self.runtime_identity is not None
        assert self.runtime_directory_fd is not None
        assert self.runtime_directory_identity is not None
        runtime_fd = self.pass_fds[0]
        metadata = os.fstat(runtime_fd)
        path_metadata = os.stat(self.runtime_path, follow_symlinks=False)
        directory_metadata = os.fstat(self.runtime_directory_fd)
        directory_path_metadata = os.stat(self.runtime_path.parent, follow_symlinks=False)
        if (
            _runtime_stat_identity(metadata) != self.runtime_identity
            or _runtime_stat_identity(path_metadata) != self.runtime_identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != self.runtime_size
            or stat.S_IMODE(metadata.st_mode) & 0o222
            or _runtime_stat_identity(directory_metadata) != self.runtime_directory_identity
            or _runtime_stat_identity(directory_path_metadata) != self.runtime_directory_identity
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or stat.S_IMODE(directory_metadata.st_mode) & 0o222
        ):
            raise AlignmentSessionError("private pinned NGS runtime snapshot is unsafe")


_samtools_runtime_lock = threading.RLock()
_samtools_runtime_cache: dict[tuple[str, str], _PinnedSamtoolsCommand] = {}


def _clear_samtools_runtime_cache() -> None:
    with _samtools_runtime_lock:
        commands = tuple(_samtools_runtime_cache.values())
        _samtools_runtime_cache.clear()
    for command in commands:
        for descriptor in command.pass_fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if command.runtime_directory_fd is not None:
            try:
                os.close(command.runtime_directory_fd)
            except OSError:
                pass


def _open_nofollow(path: Path, *, directory: bool, label: str) -> int:
    raw = os.fspath(path)
    if not path.is_absolute() or not raw or "\x00" in raw:
        raise AlignmentSessionError(f"{label} path is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    leaf_flags = directory_flags if directory else (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    current = os.open(os.sep, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=current)
            os.close(current)
            current = child
        descriptor = os.open(path.parts[-1], leaf_flags, dir_fd=current)
    except OSError as exc:
        raise AlignmentSessionError(f"{label} cannot be opened without following symlinks") from exc
    finally:
        os.close(current)
    metadata = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        os.close(descriptor)
        raise AlignmentSessionError(f"{label} has the wrong file type")
    return descriptor


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _private_runtime_snapshot(
    source_fd: int,
    *,
    directory: Path,
    expected_digest: str,
) -> tuple[int, int, Path, tuple[int, int, int, int, int], int, tuple[int, int, int, int, int]]:
    """Copy one stable source generation into one private named read-only image."""
    metadata_before = os.fstat(source_fd)
    process_directory = directory / f".bms-ngs-runtime-{expected_digest}"
    runtime_path = process_directory / "runtime.sif"
    if process_directory.exists():
        source_digest = _sha256_descriptor(source_fd)
        metadata_after = os.fstat(source_fd)
        if (
            _runtime_stat_identity(metadata_before) != _runtime_stat_identity(metadata_after)
            or source_digest != expected_digest
        ):
            raise AlignmentSessionError("pinned NGS runtime digest does not match the canonical lock")
        runtime_fd = _open_nofollow(runtime_path, directory=False, label="private pinned NGS runtime")
        directory_fd = _open_nofollow(
            process_directory,
            directory=True,
            label="private pinned NGS runtime directory",
        )
        try:
            private_metadata = os.fstat(runtime_fd)
            directory_metadata = os.fstat(directory_fd)
            if (
                _sha256_descriptor(runtime_fd) != expected_digest
                or not stat.S_ISREG(private_metadata.st_mode)
                or private_metadata.st_size != metadata_after.st_size
                or stat.S_IMODE(private_metadata.st_mode) & 0o222
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_IMODE(directory_metadata.st_mode) & 0o222
            ):
                raise AlignmentSessionError("private pinned NGS runtime snapshot is unsafe")
            return (
                runtime_fd,
                private_metadata.st_size,
                runtime_path,
                _runtime_stat_identity(private_metadata),
                directory_fd,
                _runtime_stat_identity(directory_metadata),
            )
        except Exception:
            os.close(runtime_fd)
            os.close(directory_fd)
            raise
    digest = hashlib.sha256()
    size = 0
    staging_directory = Path(
        tempfile.mkdtemp(prefix=f".bms-ngs-runtime-{expected_digest}.partial-", dir=directory)
    )
    os.chmod(staging_directory, 0o700)
    temporary_path = staging_directory / "runtime.sif"
    runtime_fd: int | None = None
    directory_fd: int | None = None
    try:
        with temporary_path.open("xb") as snapshot:
            while chunk := os.pread(source_fd, SNAPSHOT_CHUNK_BYTES, size):
                snapshot.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            metadata_after = os.fstat(source_fd)
            if _runtime_stat_identity(metadata_before) != _runtime_stat_identity(metadata_after):
                raise AlignmentSessionError("pinned NGS runtime changed during snapshot validation")
            if size != metadata_after.st_size or digest.hexdigest() != expected_digest:
                raise AlignmentSessionError("pinned NGS runtime digest does not match the canonical lock")
            os.fchmod(snapshot.fileno(), 0o400)
        os.chmod(staging_directory, 0o500)
        try:
            os.rename(staging_directory, process_directory)
        except OSError:
            if not process_directory.exists():
                raise
            os.chmod(staging_directory, 0o700)
            temporary_path.unlink(missing_ok=True)
            staging_directory.rmdir()
            return _private_runtime_snapshot(
                source_fd,
                directory=directory,
                expected_digest=expected_digest,
            )
        runtime_fd = _open_nofollow(runtime_path, directory=False, label="private pinned NGS runtime")
        directory_fd = _open_nofollow(process_directory, directory=True, label="private pinned NGS runtime directory")
        runtime_identity = _runtime_stat_identity(os.fstat(runtime_fd))
        directory_identity = _runtime_stat_identity(os.fstat(directory_fd))
        private_metadata = os.fstat(runtime_fd)
        if (
            not stat.S_ISREG(private_metadata.st_mode)
            or private_metadata.st_size != size
            or stat.S_IMODE(private_metadata.st_mode) & 0o222
        ):
            raise AlignmentSessionError("private pinned NGS runtime snapshot is unsafe")
        return runtime_fd, size, runtime_path, runtime_identity, directory_fd, directory_identity
    except Exception:
        if runtime_fd is not None:
            os.close(runtime_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        try:
            os.chmod(staging_directory, 0o700)
            temporary_path.unlink(missing_ok=True)
            staging_directory.rmdir()
        except OSError:
            pass
        raise


def _ngs_runtime_identity() -> tuple[str, str]:
    try:
        lock = json.loads(DORADO_LOCK_PATH.read_text(encoding="utf-8"))
        expected_digest = lock["dorado"]["sif_sha256"]
        expected_version = lock["scientific_tools"]["samtools"]["version"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AlignmentSessionError("canonical NGS runtime lock is unavailable") from exc
    if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise AlignmentSessionError("canonical NGS runtime digest is invalid")
    if expected_version != "1.24":
        raise AlignmentSessionError("canonical NGS samtools version is not 1.24")
    return expected_digest, expected_version


def _samtools_command() -> _PinnedSamtoolsCommand:
    runtime_raw = os.environ.get("BMS_NGS_RUNTIME_SIF", "").strip()
    runtime_sif = Path(runtime_raw).expanduser()
    if not runtime_sif.is_absolute():
        raise AlignmentSessionError("pinned NGS samtools runtime path is invalid")
    key = (os.fspath(runtime_sif), "descriptor-only")
    with _samtools_runtime_lock:
        cached = _samtools_runtime_cache.get(key)
        if cached is not None:
            cached.verify_runtime()
            return cached
        apptainer = shutil.which("apptainer")
        if not apptainer:
            raise AlignmentSessionError("Apptainer is unavailable for the pinned NGS runtime")
        expected_digest, expected_version = _ngs_runtime_identity()
        source_fd = _open_nofollow(runtime_sif, directory=False, label="pinned NGS runtime")
        runtime_fd: int | None = None
        directory_fd: int | None = None
        runtime_path: Path | None = None
        try:
            (
                runtime_fd,
                runtime_size,
                runtime_path,
                runtime_identity,
                directory_fd,
                directory_identity,
            ) = _private_runtime_snapshot(
                source_fd,
                directory=runtime_sif.parent,
                expected_digest=expected_digest,
            )
        finally:
            os.close(source_fd)
        if runtime_fd is None or directory_fd is None or runtime_path is None:
            raise AlignmentSessionError("private pinned NGS runtime snapshot is unavailable")
        try:
            command = _PinnedSamtoolsCommand(
                argv=(
                    apptainer,
                    "exec",
                    "--no-home",
                    "--pid",
                    "--net",
                    "--network",
                    "none",
                    f"/proc/self/fd/{runtime_fd}",
                    "samtools",
                ),
                pass_fds=(runtime_fd,),
                runtime_sha256=expected_digest,
                runtime_size=runtime_size,
                runtime_path=runtime_path,
                runtime_identity=runtime_identity,
                runtime_directory_fd=directory_fd,
                runtime_directory_identity=directory_identity,
            )
            try:
                command.verify_runtime()
                probe = subprocess.run(
                    [*command, "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    pass_fds=command.pass_fds,
                )
                command.verify_runtime()
            except (OSError, subprocess.SubprocessError) as exc:
                raise AlignmentSessionError("pinned NGS runtime samtools probe failed") from exc
            if not probe.stdout.startswith(f"samtools {expected_version}\n"):
                raise AlignmentSessionError("pinned NGS runtime samtools version mismatch")
            _samtools_runtime_cache[key] = command
            return command
        except Exception:
            os.close(runtime_fd)
            os.close(directory_fd)
            raise


def _run_pinned_samtools(
    command: _PinnedSamtoolsCommand,
    args: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    command.verify_runtime()
    completed = subprocess.run([*command, *args], **kwargs)
    command.verify_runtime()
    return completed


def _descriptor_path(descriptor: int) -> str:
    return f"/proc/self/fd/{descriptor}"


def _fasta_contigs_from_handle(handle: BinaryIO) -> tuple[dict[str, tuple[int, str]], bytes]:
    contigs: dict[str, tuple[int, str]] = {}
    current: str | None = None
    length = 0
    digest = hashlib.md5(usedforsecurity=False)
    normalized_reference = bytearray()
    handle.seek(0)
    for raw_line in handle:
        line = raw_line.decode("ascii").strip()
        if not line:
            continue
        if line.startswith(">"):
            if current is not None:
                contigs[current] = (length, digest.hexdigest())
            current = line[1:].split()[0]
            length = 0
            digest = hashlib.md5(usedforsecurity=False)
        elif current is None:
            raise AlignmentSessionError("reference FASTA sequence precedes header")
        else:
            normalized = line.upper().encode("ascii")
            length += len(normalized)
            digest.update(normalized)
            normalized_reference.extend(normalized)
    if current is not None:
        contigs[current] = (length, digest.hexdigest())
    return contigs, bytes(normalized_reference)


def _fasta_contigs(
    reference: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, tuple[int, str]]:
    if expected_sha256 is None or expected_size is None:
        expected_sha256, expected_size = _sha256_file_and_size(reference)
    handle = open_verified_artifact_snapshot(
        reference,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )
    try:
        contigs, _normalized_reference = _fasta_contigs_from_handle(handle)
        return contigs
    finally:
        handle.close()


def _validate_alignment_bundle(
    bam: Path,
    index: Path,
    reference: Path,
    manifest_reference_sha256: str | None,
    source_reference_sha256: str | None = None,
    mode: str = "primary",
    bam_sha256: str | None = None,
    bam_size: int | None = None,
    index_sha256: str | None = None,
    index_size: int | None = None,
    reference_sha256: str | None = None,
    reference_size: int | None = None,
) -> tuple[bool, str | None]:
    samtools = _samtools_command()
    samtools.verify_runtime()
    snapshots: list[BinaryIO] = []
    try:
        if bam_sha256 is None or bam_size is None:
            bam_sha256, bam_size = _sha256_file_and_size(bam)
        if index_sha256 is None or index_size is None:
            index_sha256, index_size = _sha256_file_and_size(index)
        if reference_sha256 is None or reference_size is None:
            reference_sha256, reference_size = _sha256_file_and_size(reference)
        bam_snapshot = open_verified_artifact_snapshot(bam, expected_size=bam_size, expected_sha256=bam_sha256)
        snapshots.append(bam_snapshot)
        index_snapshot = open_verified_artifact_snapshot(index, expected_size=index_size, expected_sha256=index_sha256)
        snapshots.append(index_snapshot)
        reference_snapshot = open_verified_artifact_snapshot(
            reference,
            expected_size=reference_size,
            expected_sha256=reference_sha256,
        )
        snapshots.append(reference_snapshot)
        bam_path = _descriptor_path(bam_snapshot.fileno())
        index_path = _descriptor_path(index_snapshot.fileno())
        pass_fds = (*samtools.pass_fds, *(snapshot.fileno() for snapshot in snapshots))
        _run_pinned_samtools(
            samtools,
            ["quickcheck", "-v", bam_path],
            check=True,
            capture_output=True,
            timeout=30,
            pass_fds=pass_fds,
        )
        _run_pinned_samtools(
            samtools,
            ["idxstats", "-X", bam_path, index_path],
            check=True,
            capture_output=True,
            timeout=30,
            pass_fds=pass_fds,
        )
        header = _run_pinned_samtools(
            samtools,
            ["view", "-H", bam_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            pass_fds=pass_fds,
        )
        bam_contigs: dict[str, tuple[int, str | None]] = {}
        for line in header.stdout.splitlines():
            if not line.startswith("@SQ\t"):
                continue
            fields = dict(field.split(":", 1) for field in line.split("\t")[1:] if ":" in field)
            if fields.get("SN") and fields.get("LN", "").isdigit():
                bam_contigs[fields["SN"]] = (int(fields["LN"]), fields.get("M5"))
        reference_contigs, normalized_reference = _fasta_contigs_from_handle(reference_snapshot)
        observed_sha256 = hashlib.sha256(normalized_reference).hexdigest()
    except (AlignmentSessionError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return False, f"alignment bundle validation failed: {type(exc).__name__}"
    finally:
        for snapshot in reversed(snapshots):
            snapshot.close()
    if not bam_contigs or set(bam_contigs) != set(reference_contigs):
        return False, "alignment/reference contig names or lengths do not match"
    if manifest_reference_sha256 != observed_sha256:
        return False, "exact reference identity manifest binding does not match the reference artifact"
    if mode == "dimer_candidates":
        midpoint = len(normalized_reference) // 2
        if (
            len(normalized_reference) % 2 != 0
            or normalized_reference[:midpoint] != normalized_reference[midpoint:]
            or not isinstance(source_reference_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_reference_sha256) is None
            or hashlib.sha256(normalized_reference[:midpoint]).hexdigest() != source_reference_sha256
        ):
            return False, "dimer reference is not derived from the authorized source reference"
    for contig, (bam_length, bam_md5) in bam_contigs.items():
        reference_length, reference_md5 = reference_contigs[contig]
        if bam_length != reference_length:
            return False, "alignment/reference contig names or lengths do not match"
        if not isinstance(bam_md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", bam_md5):
            if manifest_reference_sha256 != observed_sha256:
                return False, f"exact reference identity cannot be proven for contig {contig}"
            continue
        if bam_md5.lower() != reference_md5:
            return False, f"exact reference identity mismatch for contig {contig}"
    return True, None


def _artifact_descriptor(job_id: str, record: dict[str, Any], role: str) -> dict[str, Any]:
    path = record["path"]
    if not isinstance(path, Path):
        raise AlignmentSessionError(str(record.get("error") or "unsafe artifact"))
    observed_digest, observed_size = _sha256_file_and_size(path)
    declared_digest = record.get("declared_sha256")
    declared_size = record.get("declared_size_bytes")
    integrity_valid = (
        isinstance(declared_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", declared_digest) is not None
        and declared_digest == observed_digest
        and isinstance(declared_size, int)
        and declared_size == observed_size
    )
    identity = hashlib.sha256(
        f"{job_id}\0{record['manifest']}\0{role}\0{record['declared_path']}\0{observed_digest}".encode("utf-8")
    ).hexdigest()
    suffix = path.suffix.lower()
    mime_type = (
        "application/octet-stream" if suffix in {".bam", ".bai", ".csi"}
        else "text/x-vcf" if suffix == ".vcf"
        else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return {
        "artifact_id": identity,
        "_kind": str(record["kind"]),
        "role": role,
        "url": f"/api/jobs/{job_id}/alignment-artifacts/{identity}",
        "sha256": observed_digest,
        "size_bytes": observed_size,
        "declared_sha256": declared_digest,
        "declared_size_bytes": declared_size,
        "observed_sha256": observed_digest,
        "observed_size_bytes": observed_size,
        "integrity_valid": integrity_valid,
        "manifest": record["manifest"],
        "mime_type": mime_type,
        "range_capable": True,
        "source_manifest_sha256": record.get("source_manifest_sha256"),
        "_path": path,
    }


def _session_records(
    job_id: str,
    job_root: Path,
    source_reference_sha256: str,
    workflow_id: str,
    input_mode: str,
) -> list[dict[str, Any]]:
    if re.fullmatch(r"[0-9a-f]{64}", source_reference_sha256) is None:
        raise AlignmentSessionError("authorized source reference identity is required")
    records = _manifest_records(job_id, job_root)
    for record in records:
        if record.get("kind") == "__manifest_authority__":
            continue
        if record.get("manifest_error"):
            continue
        if record.get("workflow_id") != workflow_id:
            record["error"] = "manifest workflow_id does not match authorized job provenance"
            record["manifest_error"] = record["error"]
        elif record.get("input_mode") != input_mode:
            record["error"] = "manifest input_mode does not match authorized job provenance"
            record["manifest_error"] = record["error"]
    modes = ["primary"]
    if any(_is_dimer(record) for record in records):
        modes.append("dimer_candidates")
    sessions: list[dict[str, Any]] = []
    for mode in modes:
        bundle, unsafe_reason = _pick_bundle(records, mode)
        artifacts: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        if unsafe_reason:
            errors.append(unsafe_reason)
        for role, record in bundle.items():
            try:
                artifacts[role] = _artifact_descriptor(job_id, record, role)
                if artifacts[role]["integrity_valid"] is not True:
                    errors.append(f"{role.replace('_', ' ')} manifest integrity is missing or invalid")
            except AlignmentSessionError as exc:
                errors.append(str(exc))
        for required_role in ("alignment", "alignment_index", "reference", "reference_index"):
            if required_role not in artifacts:
                errors.append(f"missing {required_role.replace('_', ' ')}")
        if not errors:
            manifest_source_reference_sha256 = bundle["alignment"].get(
                "source_reference_sequence_sha256"
            )
            if mode == "primary":
                manifest_source_reference_sha256 = bundle["alignment"].get(
                    "reference_sequence_sha256"
                )
            if manifest_source_reference_sha256 != source_reference_sha256:
                errors.append("manifest source reference identity does not match authorized job provenance")
        if not errors:
            valid, reason = _validate_alignment_bundle(
                artifacts["alignment"]["_path"],
                artifacts["alignment_index"]["_path"],
                artifacts["reference"]["_path"],
                bundle["alignment"].get("reference_sequence_sha256"),
                source_reference_sha256,
                mode,
                bam_sha256=artifacts["alignment"]["sha256"],
                bam_size=artifacts["alignment"]["size_bytes"],
                index_sha256=artifacts["alignment_index"]["sha256"],
                index_size=artifacts["alignment_index"]["size_bytes"],
                reference_sha256=artifacts["reference"]["sha256"],
                reference_size=artifacts["reference"]["size_bytes"],
            )
            if not valid:
                errors.append(reason or "alignment bundle validation failed")
        reference_contig: str | None = None
        reference_length: int | None = None
        reference_artifact = artifacts.get("reference")
        if reference_artifact is not None:
            try:
                reference_contigs = _fasta_contigs(
                    reference_artifact["_path"],
                    expected_sha256=reference_artifact["sha256"],
                    expected_size=reference_artifact["size_bytes"],
                )
                if len(reference_contigs) == 1:
                    reference_contig = next(iter(reference_contigs))
                    reference_length = reference_contigs[reference_contig][0]
                elif not errors:
                    errors.append("a single authoritative reference contig is required")
            except (AlignmentSessionError, OSError, UnicodeError) as exc:
                if not errors:
                    errors.append(f"reference contig inspection failed: {type(exc).__name__}")
        session_seed = f"{job_id}\0{mode}\0" + "\0".join(
            artifacts[role]["artifact_id"] for role in sorted(artifacts)
        )
        session_id = hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:24]
        sequence_manifest_sha256 = next(
            (record.get("source_manifest_sha256") for record in records if record.get("manifest") == "fastq_qc/qc_manifest.json"),
            None,
        )
        verification_manifest_sha256 = next(
            (record.get("source_manifest_sha256") for record in records if record.get("manifest") == "verification/qc_manifest.json"),
            None,
        )
        fallback_manifest_sha256 = next(
            (record.get("source_manifest_sha256") for record in records if isinstance(record.get("source_manifest_sha256"), str)),
            None,
        )
        complete_manifest_authority = (
            isinstance(sequence_manifest_sha256 or fallback_manifest_sha256, str)
            if workflow_id == "ont_plasmid_qc" and input_mode == "bam"
            else (
                isinstance(sequence_manifest_sha256, str)
                and isinstance(verification_manifest_sha256, str)
            )
        )
        sequence_manifest_sha256 = sequence_manifest_sha256 or fallback_manifest_sha256
        verification_manifest_sha256 = verification_manifest_sha256 or fallback_manifest_sha256
        reference_topology = next(
            (record.get("reference_topology") for record in records if record.get("reference_topology") in {"linear", "circular"}),
            None,
        )
        if reference_topology not in {"linear", "circular"} and not errors:
            errors.append("reference topology authority is missing")
        alignment_pair_sha256 = None
        if "alignment" in artifacts and "alignment_index" in artifacts:
            alignment_pair_sha256 = hashlib.sha256(
                b"bms.ngs.alignment-pair.v1\0" + rfc8785.dumps({
                    "alignment_sha256": artifacts["alignment"]["sha256"],
                    "alignment_index_sha256": artifacts["alignment_index"]["sha256"],
                })
            ).hexdigest()
        reference = None
        if not errors and reference_contig is not None and reference_length is not None:
            reference = {
                "contig": reference_contig,
                "length_bp": reference_length,
                "topology": reference_topology,
                "normalized_sequence_sha256": bundle["alignment"].get("reference_sequence_sha256"),
                "fasta_sha256": artifacts["reference"]["sha256"],
                "fai_sha256": artifacts["reference_index"]["sha256"],
            }
        sessions.append(
            {
                "schema": "bms.ngs.alignment-session.v1",
                "session_id": session_id,
                "job_id": job_id,
                "mode": mode,
                "reference_contig": reference_contig,
                "ready": not errors,
                "unavailable_reason": "; ".join(dict.fromkeys(errors)) or None,
                "artifacts": artifacts,
                "reads_url": f"/api/jobs/{job_id}/reads?session_id={session_id}" if not errors else None,
                "sequence_qc_manifest_sha256": sequence_manifest_sha256 if not errors else None,
                "verification_manifest_sha256": verification_manifest_sha256 if not errors else None,
                "reference": reference,
                "alignment_pair_sha256": alignment_pair_sha256 if not errors else None,
                "_complete_manifest_authority": complete_manifest_authority,
            }
        )
    return sessions


def _public_session(session: dict[str, Any], package_artifact_set_sha256: str | None) -> dict[str, Any]:
    production_package_authority = package_artifact_set_sha256 is not None
    if production_package_authority and session.get("_complete_manifest_authority") is not True:
        raise AlignmentSessionError("complete session manifest authority is required")
    if package_artifact_set_sha256 is None:
        package_artifact_set_sha256 = hashlib.sha256(rfc8785.dumps([
            {"role": role, "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]}
            for role, artifact in sorted(session["artifacts"].items())
        ])).hexdigest()
    if re.fullmatch(r"[0-9a-f]{64}", package_artifact_set_sha256) is None:
        raise AlignmentSessionError("persisted package artifact-set authority is invalid")
    ready = session["ready"] is True
    artifact_keys = {
        "artifact_id", "url", "sha256", "size_bytes", "mime_type", "range_capable",
        "source_manifest_sha256",
    }
    artifacts = {
        role: {key: value for key, value in artifact.items() if key in artifact_keys}
        for role, artifact in session["artifacts"].items()
    } if ready else {}
    return {
        "schema": "bms.ngs.alignment-session.v1",
        "session_id": session["session_id"],
        "job_id": session["job_id"],
        "mode": session["mode"],
        "ready": ready,
        "unavailable_reason": session["unavailable_reason"],
        "reads_url": session["reads_url"] if ready else None,
        "sequence_qc_manifest_sha256": session["sequence_qc_manifest_sha256"] if ready else None,
        "verification_manifest_sha256": session["verification_manifest_sha256"] if ready else None,
        "artifact_set_sha256": package_artifact_set_sha256 if ready else None,
        "reference": session["reference"] if ready else None,
        "artifacts": artifacts,
        "alignment_pair_sha256": session["alignment_pair_sha256"] if ready else None,
    }


def build_alignment_sessions(
    job_id: str,
    *,
    source_reference_sha256: str,
    package_artifact_set_sha256: str | None = None,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> list[dict[str, Any]]:
    safe_job_id, job_root = _safe_job_root(
        job_id,
        results_dir,
        job_output_dir,
        pinned_root_descriptor=pinned_root_descriptor,
    )
    return [
        _public_session(session, package_artifact_set_sha256)
        for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode)
    ]


def resolve_alignment_session(
    job_id: str,
    session_id: str,
    *,
    source_reference_sha256: str,
    package_artifact_set_sha256: str | None = None,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> dict[str, Any]:
    safe_job_id, job_root = _safe_job_root(
        job_id, results_dir, job_output_dir, pinned_root_descriptor=pinned_root_descriptor,
    )
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["session_id"] == session_id:
            return _public_session(session, package_artifact_set_sha256)
    raise AlignmentSessionError(f"alignment session not found for job_id: {safe_job_id}")


def _resolve_internal_artifact(
    job_id: str,
    artifact_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
        raise AlignmentSessionError("alignment artifact not found")
    safe_job_id, job_root = _safe_job_root(
        job_id, results_dir, job_output_dir, pinned_root_descriptor=pinned_root_descriptor,
    )
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["ready"] is not True:
            continue
        for artifact in session["artifacts"].values():
            if artifact["artifact_id"] == artifact_id and artifact["integrity_valid"] is True:
                return artifact["_path"], artifact
    raise AlignmentSessionError("alignment artifact not found")


def resolve_alignment_artifact(
    job_id: str,
    artifact_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
) -> Path:
    return _resolve_internal_artifact(
        job_id,
        artifact_id,
        source_reference_sha256=source_reference_sha256,
        workflow_id=workflow_id,
        input_mode=input_mode,
        results_dir=results_dir,
        job_output_dir=job_output_dir,
    )[0]


_PACKAGE_ARTIFACT_ROLES = {
    "alignment_bam": "alignment",
    "alignment_bai": "alignment_index",
    "igv_coverage_depth": "coverage_depth",
    "igv_gc_content": "gc_content",
    "igv_gc_zscore": "gc_zscore",
    "igv_junction_hotspots": "junction_hotspots",
    "igv_position_gradient": "position_gradient",
    "reference": "reference",
    "reference_index": "reference_index",
    "igv_report": "report",
    "igv_softclip_density": "soft_clip_density",
    "igv_split_read_density": "split_read_density",
    "igv_track_config": "track_config",
}
_PACKAGE_ARTIFACT_METADATA = {
    "sequence_qc_manifest": ("authority", 1, "attachment", "json"),
    "reference": ("reference", 2, "inline", "fasta"),
    "modified_bases": ("optional_evidence", 3, "none", None),
    "reference_index": ("reference", 4, "inline", "fai"),
    "summary": ("qc_metrics", 5, "attachment", "tsv"),
    "read_lengths": ("qc_metrics", 6, "attachment", "tsv"),
    "alignment_stats": ("qc_metrics", 7, "attachment", "tsv"),
    "coverage": ("qc_metrics", 8, "attachment", "tsv"),
    "per_base_support": ("qc_metrics", 9, "attachment", "tsv"),
    "consensus": ("consensus", 10, "attachment", "fasta"),
    "consensus_index": ("consensus", 11, "attachment", "fai"),
    "consensus_log": ("audit_log", 12, "attachment", "log"),
    "alignment_bam": ("alignment", 13, "inline", "bam"),
    "alignment_bai": ("alignment", 14, "inline", "bai"),
    "igv_coverage_depth": ("viewer_auxiliary", 15, "inline", "bedgraph"),
    "igv_position_gradient": ("viewer_auxiliary", 16, "inline", "bedgraph"),
    "igv_gc_content": ("viewer_auxiliary", 17, "inline", "bedgraph"),
    "igv_gc_zscore": ("viewer_auxiliary", 18, "inline", "bedgraph"),
    "igv_split_read_density": ("viewer_auxiliary", 19, "inline", "bedgraph"),
    "igv_softclip_density": ("viewer_auxiliary", 20, "inline", "bedgraph"),
    "igv_junction_hotspots": ("viewer_auxiliary", 21, "inline", "bed"),
    "igv_report_sites_bed": ("viewer_auxiliary", 22, "inline", "bed"),
    "igv_report_sites_tsv": ("viewer_auxiliary", 23, "inline", "tsv"),
    "igv_track_config": ("viewer_auxiliary", 24, "inline", "json"),
    "igv_report": ("report", 25, "attachment", "html"),
    "log": ("audit_log", 26, "attachment", "log"),
    "construct_verification_manifest": ("authority", 28, "attachment", "json"),
    "verification_summary": ("verification", 29, "attachment", "tsv"),
    "normalized_variants": ("verification", 30, "attachment", "vcf"),
    "per_base_metrics": ("verification", 31, "attachment", "tsv"),
    "human_evidence_report": ("report", 32, "attachment", "html"),
    "observed_consensus": ("consensus", 33, "attachment", "fasta"),
    "source_read_provenance": ("source_input", 34, "attachment", "json"),
    "source_reads_fastq": ("source_input", 35, "attachment", "fastq.gz"),
    "signal_data": ("optional_evidence", 36, "none", None),
}


def _package_artifact_metadata(kind: str, source: str) -> tuple[str, int, str, str | None]:
    role, display_order, disposition, extension = _PACKAGE_ARTIFACT_METADATA.get(
        kind,
        ("optional_evidence", 256, "attachment", None),
    )
    if kind == "log" and source == "construct_verification":
        display_order = 27
    return role, display_order, disposition, extension

def _package_artifact_descriptor(
    job_id: str,
    job_root: Path,
    path: Path,
    *,
    kind: str,
    source: str,
    declared_sha256: str | None = None,
    declared_size_bytes: int | None = None,
    observed_sha256: str | None = None,
    observed_size_bytes: int | None = None,
    manifest_sha256: str | None = None,
    role: str | None = None,
    owner_scope: str = "result_root",
    managed_input_path: Path | None = None,
    display_order_override: int | None = None,
) -> dict[str, Any]:
    resolved_path = path.resolve(strict=True)
    if owner_scope == "managed_input_snapshot":
        if managed_input_path is None or resolved_path != managed_input_path.resolve(strict=True):
            raise AlignmentSessionError("managed source input is not the exact persisted snapshot")
        relative = None
    else:
        try:
            relative_path = resolved_path.relative_to(job_root.resolve())
        except ValueError as exc:
            raise AlignmentSessionError("NGS package artifact escapes the persisted job root") from exc
        if any(part in {"", ".", ".."} for part in relative_path.parts):
            raise AlignmentSessionError("NGS package artifact path is unsafe")
        relative = relative_path.as_posix()
    if observed_sha256 is None or observed_size_bytes is None:
        observed_sha256, observed_size = _sha256_file_and_size(path)
    else:
        observed_size = observed_size_bytes
    if declared_sha256 is not None and declared_sha256 != observed_sha256:
        raise AlignmentSessionError(f"NGS package artifact digest mismatch: {kind}")
    if declared_size_bytes is not None and declared_size_bytes != observed_size:
        raise AlignmentSessionError(f"NGS package artifact size mismatch: {kind}")
    identity_role = role or _PACKAGE_ARTIFACT_ROLES.get(kind, kind)
    artifact_id = hashlib.sha256(
        f"{job_id}\0{manifest_sha256 or ''}\0{identity_role}\0{relative or ''}\0{observed_sha256}".encode("utf-8")
    ).hexdigest()
    scientific_role, display_order, content_disposition, filename_extension = _package_artifact_metadata(kind, source)
    if display_order_override is not None:
        display_order = display_order_override
    suffix = path.suffix.lower()
    mime_type = (
        "application/octet-stream" if suffix in {".bam", ".bai", ".csi"}
        else "text/x-vcf" if suffix == ".vcf"
        else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return {
        "kind": kind,
        "source": source,
        "relative_path": relative,
        "state": "present",
        "artifact_id": artifact_id,
        "owner_scope": owner_scope,
        "scientific_role": scientific_role,
        "display_order": display_order,
        "content_disposition": content_disposition,
        "filename_extension": filename_extension,
        "sha256": observed_sha256,
        "size_bytes": observed_size,
        "mime_type": mime_type,
        "url": f"/api/jobs/{job_id}/ngs-artifacts/{artifact_id}",
        "range_capable": True,
        "unavailable_reason": None,
        "_path": path,
    }


def _read_bounded_json_nofollow(
    path: Path,
    *,
    label: str,
    max_bytes: int = 10 * 1024 * 1024,
) -> tuple[dict[str, Any], bytes, str, int]:
    handle = _open_regular_file_no_symlinks(path)
    try:
        size_bytes = os.fstat(handle.fileno()).st_size
        if size_bytes < 2 or size_bytes > max_bytes:
            raise AlignmentSessionError(f"{label} size is invalid")
        raw_bytes = handle.read(max_bytes + 1)
    finally:
        handle.close()
    if len(raw_bytes) != size_bytes:
        raise AlignmentSessionError(f"{label} changed while it was read")
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AlignmentSessionError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AlignmentSessionError(f"{label} must be a JSON object")
    return payload, raw_bytes, hashlib.sha256(raw_bytes).hexdigest(), size_bytes


def _manifest_package_artifacts(
    job_id: str,
    job_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    source: str,
    manifest_sha256: str,
    manifest_size_bytes: int,
    managed_input_path: Path | None = None,
) -> list[dict[str, Any]]:
    descriptors = [
        _package_artifact_descriptor(
            job_id,
            job_root,
            manifest_path,
            kind=f"{source}_manifest",
            source=source,
            observed_sha256=manifest_sha256,
            observed_size_bytes=manifest_size_bytes,
            manifest_sha256=manifest_sha256,
            role=f"{source}_manifest",
        )
    ]
    log_display_order = 26
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        if artifact.get("state") != "present":
            artifact_kind = str(artifact.get("kind") or "artifact")
            scientific_role, display_order, content_disposition, filename_extension = _package_artifact_metadata(
                artifact_kind,
                source,
            )
            descriptors.append(
                {
                    "kind": artifact_kind,
                    "source": source,
                    "relative_path": None,
                    "state": str(artifact.get("state") or "unavailable"),
                    "artifact_id": None,
                    "owner_scope": "managed_input_snapshot" if artifact.get("kind") == "source_reads_fastq" else "result_root",
                    "scientific_role": scientific_role,
                    "display_order": display_order,
                    "content_disposition": content_disposition,
                    "filename_extension": filename_extension,
                    "sha256": None,
                    "size_bytes": None,
                    "mime_type": None,
                    "url": None,
                    "range_capable": False,
                    "unavailable_reason": artifact.get("unavailable_reason") or artifact.get("missing_reason"),
                }
            )
            continue
        if artifact.get("integrity_valid") is not True:
            raise AlignmentSessionError(f"NGS package artifact integrity is invalid: {artifact.get('kind')}")
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise AlignmentSessionError("present NGS package artifact has no path")
        display_order_override = None
        if str(artifact.get("kind") or "artifact") == "log":
            display_order_override = log_display_order
            log_display_order += 1
        descriptors.append(
            _package_artifact_descriptor(
                job_id,
                job_root,
                manifest_path.parent / raw_path,
                kind=str(artifact.get("kind") or "artifact"),
                source=source,
                declared_sha256=artifact.get("declared_sha256"),
                declared_size_bytes=artifact.get("declared_size_bytes"),
                manifest_sha256=manifest_sha256,
                role=_PACKAGE_ARTIFACT_ROLES.get(str(artifact.get("kind") or "artifact"), str(artifact.get("kind") or "artifact")),
                owner_scope="managed_input_snapshot" if artifact.get("kind") == "source_reads_fastq" else "result_root",
                managed_input_path=managed_input_path,
                display_order_override=display_order_override,
            )
        )
    return descriptors


def _stable_file_identity(path: str | Path, *, label: str) -> tuple[str, int]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise AlignmentSessionError(f"{label} path is invalid")
    descriptor = _open_nofollow(candidate, directory=False, label=label)
    try:
        before = _runtime_stat_identity(os.fstat(descriptor))
        digest = _sha256_descriptor(descriptor)
        after_metadata = os.fstat(descriptor)
        after = _runtime_stat_identity(after_metadata)
        if before != after:
            raise AlignmentSessionError(f"{label} changed during identity verification")
        return digest, after_metadata.st_size
    finally:
        os.close(descriptor)


def _manifest_artifact_identity(manifest: dict[str, Any], kind: str) -> tuple[str, int] | None:
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict) or artifact.get("kind") != kind:
            continue
        digest = artifact.get("actual_sha256") or artifact.get("sha256")
        size = artifact.get("size_bytes")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) and isinstance(size, int):
            return digest, size
    return None


def _verification_input_identity(manifest: dict[str, Any], role: str) -> tuple[str, int] | None:
    inputs = manifest.get("inputs")
    evidence = inputs.get(role) if isinstance(inputs, dict) else None
    digest = evidence.get("sha256") if isinstance(evidence, dict) else None
    size = evidence.get("size_bytes") if isinstance(evidence, dict) else None
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) and isinstance(size, int):
        return digest, size
    return None


def _sequence_manifest_candidates(job_root: Path, input_mode: str) -> tuple[Path, ...]:
    canonical = job_root / "fastq_qc" / "qc_manifest.json"
    if input_mode == "fastq":
        return (canonical,)
    return (canonical, job_root / "qc_manifest.json")


def build_ngs_package_artifacts(
    job_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str,
    input_mode: str,
    source_input_path: str | Path,
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> list[dict[str, Any]]:
    """Build a digest-bound inventory from canonical persisted NGS manifests."""
    from services.sequence_qc_manifest import SequenceQcManifestError, load_sequence_qc_manifest

    safe_job_id, job_root = _safe_job_root(
        job_id,
        results_dir,
        job_output_dir,
        pinned_root_descriptor=pinned_root_descriptor,
    )
    if re.fullmatch(r"[0-9a-f]{64}", source_reference_sha256) is None:
        raise AlignmentSessionError("authorized source reference identity is required")
    source_input_identity = (
        _stable_file_identity(source_input_path, label="persisted canonical source input")
        if input_mode in {"fastq", "bam"}
        else None
    )
    sequence_candidates = _sequence_manifest_candidates(job_root, input_mode)
    sequence_path = next((path for path in sequence_candidates if path.is_file() and not path.is_symlink()), None)
    if sequence_path is None:
        raise AlignmentSessionError("canonical sequence-QC manifest is unavailable")
    sequence_document, sequence_bytes, sequence_digest, sequence_size = _read_bounded_json_nofollow(
        sequence_path,
        label="canonical sequence-QC manifest",
    )
    try:
        sequence_manifest = load_sequence_qc_manifest(
            sequence_path,
            raw_bytes=sequence_bytes,
            manifest_document=sequence_document,
            expected_job_id=safe_job_id,
            expected_workflow_id=workflow_id,
            expected_input_mode=input_mode,
            expected_analysis_status="completed",
        )
    except SequenceQcManifestError as exc:
        raise AlignmentSessionError(str(exc)) from exc
    reference_binding = sequence_manifest.get("alignment_session")
    if (
        not isinstance(reference_binding, dict)
        or reference_binding.get("reference_sequence_sha256") != source_reference_sha256
    ):
        raise AlignmentSessionError("sequence-QC reference identity does not match persisted Job")
    descriptors = _manifest_package_artifacts(
        safe_job_id,
        job_root,
        sequence_path,
        sequence_manifest,
        source="sequence_qc",
        manifest_sha256=sequence_digest,
        manifest_size_bytes=sequence_size,
        managed_input_path=Path(source_input_path),
    )

    verification_path = job_root / "verification" / "qc_manifest.json"
    if verification_path.is_file() and not verification_path.is_symlink():
        verification_document, verification_bytes, verification_digest, verification_size = _read_bounded_json_nofollow(
            verification_path,
            label="construct-verification manifest",
        )
        try:
            verification_manifest = load_sequence_qc_manifest(
                verification_path,
                raw_bytes=verification_bytes,
                manifest_document=verification_document,
            )
        except SequenceQcManifestError as exc:
            raise AlignmentSessionError(str(exc)) from exc
        if (
            verification_manifest.get("schema") != "biomodstack.construct_verification.v2"
            or verification_manifest.get("artifact_schema_version") != 2
            or "MALFORMED_VERIFICATION_MANIFEST" in verification_manifest.get("reason_codes", [])
        ):
            raise AlignmentSessionError("construct-verification manifest schema is invalid")
        sequence_identities = {
            "alignment": _manifest_artifact_identity(sequence_manifest, "alignment_bam"),
            "alignment_index": _manifest_artifact_identity(sequence_manifest, "alignment_bai"),
            "alignment_stats": _manifest_artifact_identity(sequence_manifest, "alignment_stats"),
            "reference": _manifest_artifact_identity(sequence_manifest, "reference"),
        }
        for role, expected in sequence_identities.items():
            if expected is None or _verification_input_identity(verification_manifest, role) != expected:
                raise AlignmentSessionError(
                    f"construct-verification {role} identity does not match job-bound sequence-QC evidence"
                )
        verification_reference = verification_manifest.get("inputs", {}).get("reference", {})
        if (
            not isinstance(verification_reference, dict)
            or verification_reference.get("normalized_sequence_sha256") != source_reference_sha256
        ):
            raise AlignmentSessionError("construct-verification reference identity does not match persisted Job")
        if input_mode == "fastq" and (
            source_input_identity is None
            or _verification_input_identity(verification_manifest, "source_reads") != source_input_identity
        ):
            raise AlignmentSessionError("construct-verification source input does not match persisted Job")
        descriptors.extend(
            _manifest_package_artifacts(
                safe_job_id,
                job_root,
                verification_path,
                verification_manifest,
                source="construct_verification",
                manifest_sha256=verification_digest,
                manifest_size_bytes=verification_size,
                managed_input_path=Path(source_input_path),
            )
        )

    observed_state_path = job_root / "fastq_qc" / "construct_verification_input" / "observed_state.json"
    if observed_state_path.is_file() and not observed_state_path.is_symlink():
        observed_state, _observed_bytes, observed_digest, observed_size = _read_bounded_json_nofollow(
            observed_state_path,
            label="source-read provenance",
            max_bytes=1024 * 1024,
        )
        descriptors.append(
            _package_artifact_descriptor(
                safe_job_id,
                job_root,
                observed_state_path,
                kind="source_read_provenance",
                source="construct_verification_input",
                observed_sha256=observed_digest,
                observed_size_bytes=observed_size,
                manifest_sha256=observed_digest,
                role="source_read_provenance",
                )
        )
        reads_path = observed_state.get("source_reads_path") if isinstance(observed_state, dict) else None
        reads_digest = observed_state.get("source_reads_sha256") if isinstance(observed_state, dict) else None
        if input_mode == "fastq":
            if source_input_identity is None:
                raise AlignmentSessionError("persisted FASTQ source identity is unavailable")
            reads_relative = Path(reads_path) if isinstance(reads_path, str) else None
            if (
                reads_relative is None
                or reads_relative.is_absolute()
                or any(part in {"", ".", ".."} for part in reads_relative.parts)
                or not isinstance(reads_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", reads_digest) is None
                or reads_digest != source_input_identity[0]
            ):
                raise AlignmentSessionError("retained FASTQ provenance does not match persisted source input")
            descriptor = _package_artifact_descriptor(
                safe_job_id,
                job_root,
                Path(source_input_path),
                kind="source_reads_fastq",
                source="construct_verification_input",
                declared_sha256=reads_digest,
                manifest_sha256=observed_digest,
                role="source_reads",
                owner_scope="managed_input_snapshot",
                managed_input_path=Path(source_input_path),
            )
            if descriptor.get("size_bytes") != source_input_identity[1]:
                raise AlignmentSessionError("retained FASTQ size does not match persisted source input")
            descriptors.append(descriptor)

    if input_mode == "fastq":
        scientific_role, display_order, content_disposition, filename_extension = _package_artifact_metadata(
            "signal_data",
            "input_mode",
        )
        descriptors.append(
            {
                "kind": "signal_data",
                "source": "input_mode",
                "relative_path": None,
                "state": "not_applicable_to_input_mode",
                "artifact_id": None,
                "owner_scope": "result_root",
                "scientific_role": scientific_role,
                "display_order": display_order,
                "content_disposition": content_disposition,
                "filename_extension": filename_extension,
                "sha256": None,
                "size_bytes": None,
                "mime_type": None,
                "url": None,
                "range_capable": False,
                "unavailable_reason": "FASTQ input has no retained raw signal artifact",
            }
        )
    deduplicated: dict[tuple[str, str, str, str | None, int | None], dict[str, Any]] = {}
    for descriptor in descriptors:
        key = (
            str(descriptor["source"]), str(descriptor["kind"]), str(descriptor["state"]),
            descriptor.get("sha256"), descriptor.get("size_bytes"),
        )
        if key in deduplicated:
            raise AlignmentSessionError("NGS package contains a duplicate five-field artifact record")
        deduplicated[key] = descriptor
    return [
        {key: value for key, value in descriptor.items() if key != "_path"}
        for descriptor in deduplicated.values()
    ]


def resolve_ngs_package_artifact(
    job_id: str,
    artifact_id: str,
    **authority: Any,
) -> tuple[Path, dict[str, Any]]:
    if re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
        raise AlignmentSessionError("NGS package artifact not found")
    inventory = build_ngs_package_artifacts(job_id, **authority)
    for artifact in inventory:
        if artifact.get("artifact_id") != artifact_id or artifact.get("state") != "present":
            continue
        if artifact.get("owner_scope") == "managed_input_snapshot":
            source_input_path = authority.get("source_input_path")
            if not isinstance(source_input_path, str) or not source_input_path:
                break
            path = Path(source_input_path)
        else:
            _, job_root = _safe_job_root(
                job_id,
                authority.get("results_dir"),
                authority.get("job_output_dir"),
                pinned_root_descriptor=authority.get("pinned_root_descriptor") is True,
            )
            relative = artifact.get("relative_path")
            if not isinstance(relative, str):
                break
            path = job_root / relative
        observed_digest, observed_size = _sha256_file_and_size(path)
        if observed_digest != artifact.get("sha256") or observed_size != artifact.get("size_bytes"):
            raise AlignmentSessionError("NGS package artifact changed after authority validation")
        return path, artifact
    raise AlignmentSessionError("NGS package artifact not found")


def resolve_alignment_artifact_by_role(
    job_id: str,
    mode: str,
    role: str,
    sha256: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Resolve one digest-bound artifact from an exact ready session."""
    if (
        mode not in SESSION_MODES
        or role not in LINKED_REPORT_ROLES
        or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
    ):
        raise AlignmentSessionError("alignment artifact not found")
    safe_job_id, job_root = _safe_job_root(
        job_id, results_dir, job_output_dir, pinned_root_descriptor=pinned_root_descriptor,
    )
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["mode"] != mode or session["ready"] is not True:
            continue
        artifact = session["artifacts"].get(role)
        if (
            artifact is not None
            and artifact["integrity_valid"] is True
            and artifact["sha256"] == sha256
        ):
            return artifact["_path"], artifact
    raise AlignmentSessionError("alignment artifact not found")


def resolve_session_alignment_bundle(
    job_id: str,
    session_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
    pinned_root_descriptor: bool = False,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    safe_job_id, job_root = _safe_job_root(
        job_id, results_dir, job_output_dir, pinned_root_descriptor=pinned_root_descriptor,
    )
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["session_id"] == session_id and session["ready"]:
            alignment = session["artifacts"]["alignment"]
            index = session["artifacts"]["alignment_index"]
            return alignment["_path"], alignment, index["_path"], index
    raise AlignmentSessionError("ready alignment session not found")


def source_stat_identity(path: Path) -> dict[str, int]:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit():
        try:
            descriptor = os.dup(int(parts[4]))
        except OSError as exc:
            raise AlignmentSessionError("source artifact identity is unavailable") from exc
        handle = os.fdopen(descriptor, "rb", closefd=True)
    else:
        handle = _open_regular_file_no_symlinks(path)
    try:
        observed = os.fstat(handle.fileno())
        return {
            "device": observed.st_dev,
            "inode": observed.st_ino,
            "size_bytes": observed.st_size,
            "mtime_ns": observed.st_mtime_ns,
            "ctime_ns": observed.st_ctime_ns,
        }
    finally:
        handle.close()


def _canonical_stat_identity(identity: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(int(value)) for key, value in sorted(identity.items())}


@contextmanager
def open_presentation_source_bundle(
    package: dict[str, Any],
    pinned_result_root: Path,
) -> Iterator[tuple[Path, Path, dict[str, int], dict[str, int]]]:
    manifest = package.get("manifest")
    if not isinstance(manifest, dict):
        raise AlignmentSessionError("alignment presentation source authority is unavailable")
    alignment_relative = manifest.get("source_alignment_relative_path")
    index_relative = manifest.get("source_index_relative_path")
    if not isinstance(alignment_relative, str) or not isinstance(index_relative, str):
        raise AlignmentSessionError("alignment presentation source paths are unavailable")
    alignment_path = pinned_result_root / _safe_presentation_relative_path(
        alignment_relative, pinned_result_root, pinned_result_root,
    )
    index_path = pinned_result_root / _safe_presentation_relative_path(
        index_relative, pinned_result_root, pinned_result_root,
    )
    alignment_handle = _open_regular_file_no_symlinks(alignment_path)
    index_handle: BinaryIO | None = None
    try:
        index_handle = _open_regular_file_no_symlinks(index_path)
        alignment_identity = source_stat_identity(Path(f"/proc/self/fd/{alignment_handle.fileno()}"))
        index_identity = source_stat_identity(Path(f"/proc/self/fd/{index_handle.fileno()}"))
        expected_alignment_identity = {key: int(value) for key, value in manifest["source_identity"].items()}
        expected_index_identity = {key: int(value) for key, value in manifest["source_index_identity"].items()}
        if (
            alignment_identity != expected_alignment_identity
            or index_identity != expected_index_identity
            or alignment_identity["size_bytes"] != manifest.get("source_alignment_size_bytes")
            or index_identity["size_bytes"] != manifest.get("source_index_size_bytes")
        ):
            raise AlignmentSessionError("alignment presentation source identity changed")
        yield (
            Path(f"/proc/self/fd/{alignment_handle.fileno()}"),
            Path(f"/proc/self/fd/{index_handle.fileno()}"),
            alignment_identity,
            index_identity,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AlignmentSessionError("alignment presentation source identity is invalid") from exc
    finally:
        if index_handle is not None:
            index_handle.close()
        alignment_handle.close()


def _verify_descriptor(handle: BinaryIO, expected_size: int, expected_sha256: str) -> dict[str, int]:
    observed = os.fstat(handle.fileno())
    identity = {
        "device": observed.st_dev, "inode": observed.st_ino, "size_bytes": observed.st_size,
        "mtime_ns": observed.st_mtime_ns, "ctime_ns": observed.st_ctime_ns,
    }
    if observed.st_size != expected_size:
        raise AlignmentSessionError("artifact integrity size mismatch")
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(SNAPSHOT_CHUNK_BYTES), b""):
        digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise AlignmentSessionError("artifact integrity digest mismatch")
    handle.seek(0)
    return identity


def _derived_metadata(
    path: Path, kind: str, source_manifest_sha256: str,
    *, digest: str | None = None, size: int | None = None,
) -> dict[str, Any]:
    if digest is None or size is None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
    extension = (
        "json" if kind.endswith("manifest")
        else "bam.bai" if kind.endswith("index")
        else "bedgraph" if "coverage" in kind
        else "bam"
    )
    return {
        "artifact_id": f"{kind}-{digest}", "kind": kind, "sha256": digest, "size_bytes": size,
        "mime_type": (
            "application/json" if extension == "json"
            else "text/plain" if extension == "bedgraph"
            else "application/octet-stream"
        ),
        "range_capable": True, "content_disposition": "inline", "filename_extension": extension,
        "source_manifest_sha256": source_manifest_sha256, "_path": path,
    }


def _rank_read(source_sha256: str, read_id: str) -> str:
    return hashlib.sha256(f"{source_sha256}\0{read_id}".encode("utf-8")).hexdigest()


def _record_sort_key(read: Any) -> tuple[int, int, str, int]:
    return (
        read.reference_id if read.reference_id >= 0 else 2**31,
        read.reference_start if read.reference_start >= 0 else 2**31,
        read.query_name or "", read.flag,
    )


_BOUNDED_BAM_WRITER = r"""
import json
import resource
import sys

import pysam

source_path, output_path, ids_path, index_path, contig, start, end, include_supplementary, byte_limit = sys.argv[1:]
limit = int(byte_limit)
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
with open(ids_path, "r", encoding="utf-8") as handle:
    selected_ids = set(json.load(handle))
open_kwargs = {} if index_path == "-" else {"index_filename": index_path}
written = 0
with pysam.AlignmentFile(source_path, "rb", **open_kwargs) as source:
    with pysam.AlignmentFile(output_path, "wb", header=source.header) as output:
        records = (
            source.fetch(until_eof=True)
            if contig == "-"
            else source.fetch(contig, int(start) - 1, int(end))
        )
        for record in records:
            if record.query_name not in selected_ids or record.is_unmapped or record.is_secondary:
                continue
            if include_supplementary != "1" and record.is_supplementary:
                continue
            output.write(record)
            written += 1
print(written)
"""


def _path_descriptor_fds(*paths: Path | None) -> tuple[int, ...]:
    descriptors: set[int] = set()
    for path in paths:
        if path is None:
            continue
        parts = path.parts
        if len(parts) >= 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit():
            descriptors.add(int(parts[4]))
    return tuple(sorted(descriptors))


def _write_bam_for_ids_bounded(
    path: Path,
    source_path: Path,
    ids: list[str],
    *,
    byte_limit: int,
    deadline: float,
    label: str,
    index_path: Path | None = None,
    contig: str | None = None,
    start: int | None = None,
    end: int | None = None,
    include_supplementary: bool = False,
) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AlignmentSessionError(f"{label} time limit exceeded")
    ids_path = path.parent / ".selected-read-ids.json"
    ids_bytes = rfc8785.dumps(ids)
    if len(ids_bytes) > ALIGNMENT_SELECTED_IDS_MAX_BYTES:
        raise AlignmentSessionError(f"{label} selected read identities exceed the byte limit")
    ids_path.write_bytes(ids_bytes)
    path.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _BOUNDED_BAM_WRITER,
                os.fspath(source_path),
                os.fspath(path),
                os.fspath(ids_path),
                os.fspath(index_path) if index_path is not None else "-",
                contig or "-",
                str(start or 0),
                str(end or 0),
                "1" if include_supplementary else "0",
                str(byte_limit),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=remaining,
            pass_fds=_path_descriptor_fds(source_path, index_path, path, ids_path),
        )
    except subprocess.TimeoutExpired as exc:
        raise AlignmentSessionError(f"{label} time limit exceeded") from exc
    except OSError as exc:
        raise AlignmentSessionError(f"{label} BAM generation failed") from exc
    finally:
        ids_path.unlink(missing_ok=True)
    observed_size = path.stat().st_size if path.exists() else 0
    byte_limited = (
        observed_size >= byte_limit
        or "File too large" in result.stderr
        or result.returncode == -signal.SIGXFSZ
    )
    if result.returncode != 0:
        if byte_limited:
            raise _AlignmentDerivativeByteLimit(f"{label} byte ceiling exceeded")
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AlignmentSessionError(f"{label} BAM generation failed: {detail}")
    if observed_size > byte_limit:
        raise _AlignmentDerivativeByteLimit(f"{label} byte ceiling exceeded")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise AlignmentSessionError(f"{label} BAM generation returned an invalid receipt") from exc


def _index_bam_with_deadline(
    path: Path,
    *,
    deadline: float,
    label: str,
    byte_limit: int | None = None,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AlignmentSessionError(f"{label} time limit exceeded")
    parts = path.parts
    pass_fds: tuple[int, ...] = ()
    if len(parts) >= 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit():
        pass_fds = (int(parts[4]),)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import resource,sys,pysam; "
                    "limit=int(sys.argv[2]); "
                    "resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit)) if limit else None; "
                    "pysam.index(sys.argv[1])"
                ),
                os.fspath(path),
                str(byte_limit or 0),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=remaining,
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired as exc:
        raise AlignmentSessionError(f"{label} time limit exceeded") from exc
    except OSError as exc:
        raise AlignmentSessionError(f"{label} indexing failed") from exc
    index_path = Path(f"{path}.bai")
    observed_size = index_path.stat().st_size if index_path.exists() else 0
    byte_limited = (
        byte_limit is not None
        and (
            observed_size >= byte_limit
            or "File too large" in result.stderr
            or result.returncode == -signal.SIGXFSZ
        )
    )
    if result.returncode != 0:
        if byte_limited:
            raise AlignmentSessionError(f"{label} index byte ceiling exceeded")
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AlignmentSessionError(f"{label} indexing failed: {detail}")
    if byte_limit is not None and observed_size > byte_limit:
        raise AlignmentSessionError(f"{label} index byte ceiling exceeded")


def _selected_set_digest(ids: list[str]) -> str:
    return hashlib.sha256(rfc8785.dumps(sorted(ids))).hexdigest()


def _creation_authority() -> tuple[str, str | None]:
    try:
        revision = source_build_revision()
        tree = runtime_implementation_record().get("successor_source_tree")
    except (SourceBuildRevisionError, NgsMolBioRuntimeAuthorityError, OSError, RuntimeError, ValueError) as exc:
        raise AlignmentSessionError("derived-artifact creation revision authority is unavailable") from exc
    if not isinstance(tree, str) or re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        tree = None
    return revision, tree


def _presentation_root(cache_root: Path | None, source_bam: Path | None = None) -> Path:
    if cache_root is not None:
        return Path(os.path.abspath(cache_root))
    if source_bam is None:
        raise AlignmentSessionError("pinned presentation root is required")
    return Path(os.path.abspath(source_bam.parent / ".alignment-presentations"))


@contextmanager
def open_presentation_authority_root(cache_root: Path, *, create: bool) -> Iterator[Path]:
    requested = Path(os.path.abspath(cache_root))
    parts = requested.parts
    if len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit():
        if not stat.S_ISDIR(os.fstat(int(parts[4])).st_mode):
            raise AlignmentSessionError("presentation root is unsafe")
        yield requested
        return
    parent_descriptor = _open_directory_descriptor_no_symlinks(requested.parent)
    child_descriptor: int | None = None
    try:
        if create:
            try:
                os.mkdir(requested.name, mode=0o750, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
        try:
            child_descriptor = os.open(
                requested.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise AlignmentSessionError("presentation root is unsafe or unavailable") from exc
        yield Path(f"/proc/self/fd/{child_descriptor}")
    finally:
        if child_descriptor is not None:
            os.close(child_descriptor)
        os.close(parent_descriptor)


def _safe_presentation_relative_path(value: str | None, fallback: Path, root: Path) -> str:
    if value is None:
        try:
            value = fallback.relative_to(root).as_posix()
        except ValueError as exc:
            raise AlignmentSessionError("presentation source path is outside result authority") from exc
    relative = Path(value)
    if not value or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise AlignmentSessionError("presentation source relative path is unsafe")
    return relative.as_posix()


@contextmanager
def _open_presentation_namespace(root: Path, job_id: str, session_id: str, *, create: bool) -> Iterator[Path]:
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", job_id) is None or job_id in {".", ".."}:
        raise AlignmentSessionError("presentation job namespace is invalid")
    if re.fullmatch(r"[0-9a-f]{24}", session_id) is None:
        raise AlignmentSessionError("presentation session namespace is invalid")
    descriptor = _open_directory_descriptor_no_symlinks(root)
    try:
        for component in (job_id, session_id):
            if create:
                try:
                    os.mkdir(component, mode=0o750, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as exc:
                raise AlignmentSessionError("presentation namespace is unsafe or unavailable") from exc
            os.close(descriptor)
            descriptor = child
        yield Path(f"/proc/self/fd/{descriptor}")
    finally:
        os.close(descriptor)


def _pin_presentation_root(*, create: bool):
    def decorate(function: Any) -> Any:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            raw_root = kwargs.get("cache_root")
            if not isinstance(raw_root, Path):
                raise AlignmentSessionError("pinned presentation root is required")
            requested = Path(os.path.abspath(raw_root))
            parts = requested.parts
            already_pinned = len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit()
            if create:
                bam = Path(args[0]) if args else Path(kwargs["bam"])
                index = Path(kwargs["index"])
                kwargs["source_alignment_relative_path"] = _safe_presentation_relative_path(
                    kwargs.get("source_alignment_relative_path"), bam, requested.parent,
                )
                kwargs["source_index_relative_path"] = _safe_presentation_relative_path(
                    kwargs.get("source_index_relative_path"), index, requested.parent,
                )
            if already_pinned:
                return function(*args, **kwargs)
            with open_presentation_authority_root(requested, create=create) as pinned_root:
                if create:
                    job_id = str(kwargs.get("job_id") or "")
                    session_id = str(kwargs.get("session_id") or "")
                else:
                    job_id = str(args[0]) if args else str(kwargs.get("job_id") or "")
                    session_id = str(args[1]) if len(args) > 1 else str(kwargs.get("session_id") or "")
                with _open_presentation_namespace(pinned_root, job_id, session_id, create=create) as namespace_root:
                    kwargs["cache_root"] = pinned_root
                    kwargs["presentation_namespace_root"] = namespace_root
                    result = function(*args, **kwargs)
                if isinstance(result, dict):
                    manifest = result.get("manifest")
                    authority_sha256 = manifest.get("authority_sha256") if isinstance(manifest, dict) else None
                    if not isinstance(authority_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", authority_sha256) is None:
                        raise AlignmentSessionError("presentation package authority is invalid")
                    result_root = requested / job_id / session_id / authority_sha256
                    for key in ("bam_path", "index_path", "coverage_path", "manifest_path"):
                        path = result.get(key)
                        if isinstance(path, Path):
                            result[key] = result_root / path.name
                return result
        return wrapped
    return decorate


def _locus_cache_root(cache_root: Path | None) -> Path:
    return Path(os.path.abspath(cache_root or (get_analysis_cache_dir() / "ngs_alignment_locus_slices")))


def _pin_locus_cache_root(*, create: bool):
    def decorate(function: Any) -> Any:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            requested = _locus_cache_root(kwargs.get("cache_root"))
            parts = requested.parts
            already_pinned = len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit()
            if already_pinned:
                return function(*args, **kwargs)
            with open_presentation_authority_root(requested, create=create) as pinned_root:
                kwargs["cache_root"] = pinned_root
                result = function(*args, **kwargs)
                if isinstance(result, dict):
                    slice_id = result.get("slice_id")
                    if not isinstance(slice_id, str) or re.fullmatch(r"[0-9a-f]{64}", slice_id) is None:
                        raise AlignmentSessionError("locus slice package identity is invalid")
                    result_root = requested / slice_id
                    for key in ("bam_path", "index_path", "manifest_path"):
                        path = result.get(key)
                        if isinstance(path, Path):
                            result[key] = result_root / path.name
                return result
        return wrapped
    return decorate


def _acquire_locus_generation_slot(root: Path, slice_id: str) -> tuple[int, BinaryIO]:
    root.mkdir(parents=True, exist_ok=True)
    parts = root.parts
    if not (len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit()):
        raise AlignmentSessionError("locus cache root is not pinned")
    root_fd = int(parts[4])
    slot = int(slice_id, 16) % LOCUS_GENERATION_CONCURRENCY
    descriptor = os.open(
        f".generation-slot-{slot}.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o640,
        dir_fd=root_fd,
    )
    handle = os.fdopen(descriptor, "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return slot, handle
    except BlockingIOError as exc:
        handle.close()
        raise AlignmentSessionError("locus slice concurrency limit exceeded") from exc


def _remove_locus_transient(root_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(name, dir_fd=root_fd)
    else:
        os.unlink(name, dir_fd=root_fd)


def _cleanup_legacy_locus_transients(root: Path, root_fd: int) -> None:
    for candidate in root.iterdir():
        name = candidate.name
        if (
            re.fullmatch(r"\.[0-9a-f]{64}\.lock", name)
            or re.fullmatch(r"\.[0-9a-f]{64}-.*", name)
            or name.startswith(".orphan-")
        ):
            _remove_locus_transient(root_fd, name)


def _cleanup_locus_cache(
    root: Path,
    *,
    active: Path | None = None,
    reserve_bytes: int = 0,
    reserve_entries: int = 0,
) -> None:
    entries: list[tuple[int, str, Path, int]] = []
    for candidate in root.iterdir():
        if candidate == active or not candidate.is_dir() or candidate.name.startswith("."):
            continue
        files = [path for path in candidate.iterdir() if path.is_file() and not path.is_symlink()]
        entries.append((
            (candidate / "manifest.json").stat().st_mtime_ns
            if (candidate / "manifest.json").is_file() else candidate.stat().st_mtime_ns,
            candidate.name,
            candidate,
            sum(path.stat().st_size for path in files),
        ))
    active_size = (
        sum(path.stat().st_size for path in active.iterdir() if path.is_file() and not path.is_symlink())
        if active is not None and active.is_dir() else 0
    )
    total = active_size + reserve_bytes + sum(item[3] for item in entries)
    count = int(active is not None and active.is_dir()) + reserve_entries + len(entries)
    for _mtime, _name, candidate, size in sorted(entries):
        if total <= LOCUS_CACHE_MAX_BYTES and count <= LOCUS_CACHE_MAX_ENTRIES:
            break
        shutil.rmtree(candidate, ignore_errors=True)
        total -= size
        count -= 1
    if total > LOCUS_CACHE_MAX_BYTES or count > LOCUS_CACHE_MAX_ENTRIES:
        raise AlignmentSessionError("locus slice cache capacity unavailable")


def _presentation_entry_size(candidate: Path) -> int | None:
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    expected = {
        "alignment-preview.bam",
        "alignment-preview.bam.bai",
        "full-source-primary-coverage.bedgraph",
        "manifest.json",
    }
    size = 0
    observed: set[str] = set()
    try:
        for child in candidate.iterdir():
            if child.is_symlink() or not child.is_file():
                return None
            observed.add(child.name)
            size += child.stat().st_size
    except OSError:
        return None
    return size if observed == expected else None


def _cleanup_presentation_namespace(
    namespace: Path,
    namespace_fd: int,
    *,
    active: Path | None = None,
    protected_names: set[str] | None = None,
    reserve_bytes: int = 0,
    reserve_entries: int = 0,
) -> None:
    protected = set(protected_names or ())
    if active is not None:
        protected.add(active.name)
    for candidate in list(namespace.iterdir()):
        name = candidate.name
        if (
            name in {".generation.tmp", ".generation.orphan"}
            or re.fullmatch(r"\.[0-9a-f]{64}-.*", name)
            or name.startswith(".orphan-")
        ):
            _remove_locus_transient(namespace_fd, name)
    entries: list[tuple[int, str, int]] = []
    protected_size = 0
    protected_count = 0
    for candidate in list(namespace.iterdir()):
        if re.fullmatch(r"[0-9a-f]{64}", candidate.name) is None:
            continue
        size = _presentation_entry_size(candidate)
        if (
            size is None
            or _load_derived_package(
                candidate,
                expected_authority_sha256=candidate.name,
            ) is None
        ):
            _remove_locus_transient(namespace_fd, candidate.name)
            continue
        if candidate.name in protected:
            protected_size += size
            protected_count += 1
            continue
        manifest = candidate / "manifest.json"
        mtime = manifest.stat().st_mtime_ns if manifest.is_file() else candidate.stat().st_mtime_ns
        entries.append((mtime, candidate.name, size))
    total = protected_size + reserve_bytes + sum(item[2] for item in entries)
    count = protected_count + reserve_entries + len(entries)
    for _mtime, name, size in sorted(entries):
        if (
            total <= ALIGNMENT_PRESENTATION_CACHE_MAX_BYTES
            and count <= ALIGNMENT_PRESENTATION_CACHE_MAX_ENTRIES
        ):
            break
        _remove_locus_transient(namespace_fd, name)
        total -= size
        count -= 1
    if (
        total > ALIGNMENT_PRESENTATION_CACHE_MAX_BYTES
        or count > ALIGNMENT_PRESENTATION_CACHE_MAX_ENTRIES
    ):
        raise AlignmentSessionError("alignment presentation cache capacity unavailable")


def _presentation_names_for_manifest(namespace: Path, manifest_sha256: str | None) -> set[str]:
    if manifest_sha256 is None:
        return set()
    names: set[str] = set()
    for candidate in namespace.iterdir():
        if re.fullmatch(r"[0-9a-f]{64}", candidate.name) is None:
            continue
        manifest = candidate / "manifest.json"
        if manifest.is_file() and not manifest.is_symlink():
            try:
                if hashlib.sha256(manifest.read_bytes()).hexdigest() == manifest_sha256:
                    names.add(candidate.name)
            except OSError:
                continue
    return names


def _load_derived_package(
    directory: Path,
    *,
    expected_authority_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any] | None:
    manifest_path = directory / "manifest.json"
    paths = {
        "bam": directory / "alignment-preview.bam",
        "index": directory / "alignment-preview.bam.bai",
        "coverage": directory / "full-source-primary-coverage.bedgraph",
    }
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
            return None
        manifest = json.loads(manifest_bytes)
        if manifest.get("schema") != "bms.ngs.alignment-presentation-manifest.v3":
            return None
        authority = manifest.get("authority")
        if not isinstance(authority, dict):
            return None
        authority_digest = hashlib.sha256(rfc8785.dumps(authority)).hexdigest()
        directory_authority = expected_authority_sha256 or directory.name
        if manifest.get("authority_sha256") != authority_digest or directory_authority != authority_digest:
            return None
        metadata = {}
        for key, path in paths.items():
            if not path.is_file() or path.is_symlink():
                return None
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
            declared = manifest["outputs"][key]
            if declared != {"sha256": digest, "size_bytes": size}:
                return None
            metadata[key] = _derived_metadata(
                path,
                {"bam": "alignment_preview", "index": "alignment_preview_index", "coverage": "full_source_primary_coverage"}[key],
                manifest["source_manifest_sha256"],
                digest=digest, size=size,
            )
            metadata[key].update({
                "source_alignment_sha256": manifest["source_alignment_sha256"],
                "source_index_sha256": manifest["source_index_sha256"],
                "policy": ALIGNMENT_PREVIEW_POLICY,
            })
        return {
            "bam_path": paths["bam"], "bam_metadata": metadata["bam"],
            "index_path": paths["index"], "index_metadata": metadata["index"],
            "coverage_path": paths["coverage"], "coverage_metadata": metadata["coverage"],
            "manifest_path": manifest_path,
            "manifest_metadata": _derived_metadata(
                manifest_path, "alignment_presentation_manifest", manifest["source_manifest_sha256"],
                digest=manifest_digest, size=len(manifest_bytes),
            ),
            "manifest": manifest,
        }
    except (KeyError, OSError, TypeError, ValueError):
        return None


@_pin_presentation_root(create=False)
def resolve_cached_alignment_presentation(
    job_id: str,
    session_id: str,
    *,
    cache_root: Path | None = None,
    expected_authority_sha256: str,
    expected_manifest_sha256: str,
    presentation_namespace_root: Path | None = None,
) -> dict[str, Any]:
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_authority_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None
    ):
        raise AlignmentSessionError("alignment presentation manifest authority is invalid")
    namespace = presentation_namespace_root or (_presentation_root(cache_root) / job_id / session_id)
    candidate = namespace / expected_authority_sha256
    if candidate.is_symlink() or not candidate.is_dir():
        raise AlignmentSessionError("alignment presentation manifest authority is invalid")
    with open_presentation_authority_root(candidate, create=False) as pinned_candidate:
        package = _load_derived_package(
            pinned_candidate,
            expected_authority_sha256=expected_authority_sha256,
            expected_manifest_sha256=expected_manifest_sha256,
        )
    if (
        package is None
        or package["manifest"].get("job_id") != job_id
        or package["manifest"].get("session_id") != session_id
        or package["manifest"].get("authority_sha256") != expected_authority_sha256
    ):
        raise AlignmentSessionError("alignment presentation manifest authority is invalid")
    return package


def _serialize_alignment_presentation_generation(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        lock_root = get_analysis_cache_dir() / "ngs_alignment_presentation_generation"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_handle = (lock_root / ".generation.lock").open("a+b")
        try:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AlignmentSessionError("alignment presentation concurrency limit exceeded") from exc
            return function(*args, **kwargs)
        finally:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
    return wrapped


@_serialize_alignment_presentation_generation
@_pin_presentation_root(create=True)
def build_alignment_presentation(
    bam: Path, *, bam_sha256: str, bam_size_bytes: int, index: Path,
    index_sha256: str, index_size_bytes: int, source_manifest_sha256: str,
    job_id: str, session_id: str, mode: str, cache_root: Path | None = None,
    artifact_set_sha256: str | None = None, alignment_pair_sha256: str | None = None,
    source_alignment_relative_path: str | None = None,
    source_index_relative_path: str | None = None,
    expected_manifest_sha256: str | None = None,
    presentation_namespace_root: Path | None = None,
    target_reads: int = ALIGNMENT_PREVIEW_TARGET_READS,
    max_output_bytes: int = ALIGNMENT_PREVIEW_MAX_BYTES,
    max_coverage_bins: int = ALIGNMENT_COVERAGE_MAX_BINS,
    max_seconds: float = ALIGNMENT_PRESENTATION_MAX_SECONDS,
) -> dict[str, Any]:
    if (
        target_reads < 1 or target_reads > 10_000
        or max_output_bytes < 1 or max_output_bytes > ALIGNMENT_PREVIEW_MAX_BYTES
        or max_coverage_bins < 1 or max_coverage_bins > ALIGNMENT_COVERAGE_MAX_BINS
        or not math.isfinite(max_seconds) or max_seconds <= 0
        or max_seconds > ALIGNMENT_PRESENTATION_MAX_SECONDS
    ):
        raise AlignmentSessionError("alignment presentation policy is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", source_manifest_sha256) is None or mode not in SESSION_MODES:
        raise AlignmentSessionError("alignment presentation authority is invalid")
    if not isinstance(source_alignment_relative_path, str) or not isinstance(source_index_relative_path, str):
        raise AlignmentSessionError("alignment presentation source paths are unavailable")
    creation_revision, creation_source_tree = _creation_authority()
    admitted_source_identity = source_stat_identity(bam)
    admitted_index_identity = source_stat_identity(index)
    authority = {
        "schema": "bms.ngs.alignment-presentation-authority.v3", "job_id": job_id,
        "session_id": session_id, "mode": mode, "source_manifest_sha256": source_manifest_sha256,
        "source_alignment_sha256": bam_sha256, "source_alignment_size_bytes": bam_size_bytes,
        "source_index_sha256": index_sha256, "source_index_size_bytes": index_size_bytes,
        "source_alignment_relative_path": source_alignment_relative_path,
        "source_index_relative_path": source_index_relative_path,
        "source_identity": _canonical_stat_identity(admitted_source_identity),
        "source_index_identity": _canonical_stat_identity(admitted_index_identity),
        "artifact_set_sha256": artifact_set_sha256, "alignment_pair_sha256": alignment_pair_sha256,
        "creation_revision": creation_revision, "creation_source_tree": creation_source_tree,
        "policy": {"id": ALIGNMENT_PREVIEW_POLICY, "version": ALIGNMENT_PRESENTATION_POLICY_VERSION,
                   "target_reads": target_reads, "max_preview_bytes": max_output_bytes,
                   "max_coverage_bins": max_coverage_bins, "max_seconds": max_seconds},
    }
    cache_key = hashlib.sha256(rfc8785.dumps(authority)).hexdigest()
    root_parent = _presentation_root(cache_root, bam)
    namespace = presentation_namespace_root or (root_parent / job_id / session_id)
    destination = namespace / cache_key
    namespace_parts = namespace.parts
    if not (len(namespace_parts) == 5 and namespace_parts[1:4] == ("proc", "self", "fd") and namespace_parts[4].isdigit()):
        raise AlignmentSessionError("presentation namespace is not pinned")
    lock_fd = os.open(
        ".generation.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o640,
        dir_fd=int(namespace_parts[4]),
    )
    with os.fdopen(lock_fd, "a+b") as producer_lock:
        try:
            fcntl.flock(producer_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlignmentSessionError("alignment presentation generation is already in progress") from exc
        namespace_fd = int(namespace_parts[4])
        protected_names = _presentation_names_for_manifest(namespace, expected_manifest_sha256)
        _cleanup_presentation_namespace(
            namespace,
            namespace_fd,
            protected_names=protected_names,
        )
        if destination.exists() or destination.is_symlink():
            if expected_manifest_sha256 is not None:
                if destination.is_symlink():
                    raise AlignmentSessionError("alignment presentation destination is unsafe")
                with open_presentation_authority_root(destination, create=False) as pinned_destination:
                    cached = _load_derived_package(
                        pinned_destination,
                        expected_authority_sha256=cache_key,
                        expected_manifest_sha256=expected_manifest_sha256,
                    )
                if cached is not None and cached["manifest"].get("authority_sha256") == cache_key:
                    _cleanup_presentation_namespace(
                        namespace,
                        namespace_fd,
                        active=destination,
                        protected_names=protected_names,
                    )
                    return cached
                raise AlignmentSessionError("alignment presentation manifest authority is invalid")
            _remove_locus_transient(namespace_fd, cache_key)
        _cleanup_presentation_namespace(
            namespace,
            namespace_fd,
            protected_names=protected_names,
            reserve_bytes=ALIGNMENT_PRESENTATION_WORK_MAX_BYTES,
            reserve_entries=1,
        )
        os.mkdir(".generation.tmp", mode=0o750, dir_fd=namespace_fd)
        temporary = namespace / ".generation.tmp"
        source_handle = index_handle = None
        candidate_db: sqlite3.Connection | None = None
        deadline = time.monotonic() + max_seconds
        try:
            source_handle = _open_regular_file_no_symlinks(bam)
            source_identity = _verify_descriptor(source_handle, bam_size_bytes, bam_sha256)
            index_handle = _open_regular_file_no_symlinks(index)
            index_identity = _verify_descriptor(index_handle, index_size_bytes, index_sha256)
            if source_identity != admitted_source_identity or index_identity != admitted_index_identity:
                raise AlignmentSessionError("alignment presentation source changed during materialization")
            candidate_db_path = temporary / "preview-candidates.sqlite3"
            candidate_db = sqlite3.connect(candidate_db_path)
            candidate_db.execute("PRAGMA page_size=4096")
            candidate_db.execute(
                f"PRAGMA max_page_count={ALIGNMENT_PRESENTATION_WORK_MAX_BYTES // 4096}"
            )
            candidate_db.execute("PRAGMA journal_mode=OFF")
            candidate_db.execute("PRAGMA synchronous=OFF")
            candidate_db.execute("PRAGMA temp_store=FILE")
            candidate_db.execute("PRAGMA cache_size=-8192")
            candidate_db.execute(
                "CREATE TABLE candidates (read_id TEXT PRIMARY KEY, contig TEXT NOT NULL, "
                "tile INTEGER NOT NULL, strand TEXT NOT NULL, rank TEXT NOT NULL) WITHOUT ROWID"
            )
            candidate_db.execute(
                "CREATE INDEX candidate_stratum_rank ON candidates (contig, tile, strand, rank, read_id)"
            )
            with pysam.AlignmentFile(_descriptor_path(source_handle.fileno()), "rb") as source:
                header = source.header.to_dict()
                references = list(zip(source.references, source.lengths, strict=True))
                bin_width = max(1, math.ceil(sum(length for _name, length in references) / max_coverage_bins))
                tile_widths = {name: max(1, math.ceil(length / 64)) for name, length in references}
                coverage = {name: [0] * math.ceil(length / bin_width) for name, length in references}
                source_records = source_alignment_records = forward = reverse = 0
                source_record_counts = {
                    "mapped_primary": 0, "secondary": 0,
                    "supplementary": 0, "unmapped": 0,
                }
                flag_counts: dict[str, int] = {}
                source_contig_counts: dict[str, int] = {}
                source_strand_counts = {"forward": 0, "reverse": 0}
                for read in source.fetch(until_eof=True):
                    if time.monotonic() > deadline:
                        raise AlignmentSessionError("alignment presentation time limit exceeded")
                    source_alignment_records += 1
                    if read.is_unmapped:
                        source_record_counts["unmapped"] += 1
                        continue
                    if read.is_secondary:
                        source_record_counts["secondary"] += 1
                        continue
                    if read.is_supplementary:
                        source_record_counts["supplementary"] += 1
                        continue
                    source_record_counts["mapped_primary"] += 1
                    if not read.query_name:
                        continue
                    source_records += 1
                    strand = "reverse" if read.is_reverse else "forward"
                    forward += int(not read.is_reverse)
                    reverse += int(read.is_reverse)
                    source_strand_counts[strand] += 1
                    flag_counts[str(read.flag)] = flag_counts.get(str(read.flag), 0) + 1
                    contig = source.get_reference_name(read.reference_id)
                    source_contig_counts[contig] = source_contig_counts.get(contig, 0) + 1
                    candidate_db.execute(
                        "INSERT OR IGNORE INTO candidates(read_id, contig, tile, strand, rank) VALUES (?, ?, ?, ?, ?)",
                        (
                            read.query_name,
                            contig,
                            max(0, read.reference_start) // tile_widths[contig],
                            strand,
                            _rank_read(bam_sha256, read.query_name),
                        ),
                    )
                    for block_start, block_end in read.get_blocks():
                        first_bin = block_start // bin_width
                        last_bin = (block_end - 1) // bin_width
                        for bin_index in range(first_bin, last_bin + 1):
                            left = max(block_start, bin_index * bin_width)
                            right = min(block_end, (bin_index + 1) * bin_width)
                            coverage[contig][bin_index] += max(0, right - left)
            candidate_db.commit()
            stratum_counts = {
                (str(contig), int(tile), str(strand)): int(count)
                for contig, tile, strand, count in candidate_db.execute(
                    "SELECT contig, tile, strand, COUNT(*) FROM candidates "
                    "GROUP BY contig, tile, strand ORDER BY contig, tile, strand"
                )
            }
            source_primary_read_count = sum(stratum_counts.values())
            strata = sorted(stratum_counts)
            quotas = {stratum: 0 for stratum in strata}
            if target_reads >= len(strata):
                quotas = {stratum: 1 for stratum in strata}
                remaining = target_reads - len(strata)
            else:
                remaining = target_reads
            capacities = {stratum: stratum_counts[stratum] - quotas[stratum] for stratum in strata}
            total_capacity = sum(capacities.values())
            shares = []
            if remaining and total_capacity:
                for stratum in strata:
                    exact = remaining * capacities[stratum] / total_capacity
                    whole = math.floor(exact)
                    quotas[stratum] += whole
                    shares.append((exact - whole, stratum))
                leftover = remaining - sum(math.floor(remaining * capacities[s] / total_capacity) for s in strata)
                for _fraction, stratum in sorted(shares, key=lambda item: (-item[0], item[1]))[:leftover]:
                    quotas[stratum] += 1
            selected_ids_set: set[str] = set()
            selected_strata: dict[str, tuple[str, int, str]] = {}
            for stratum in strata:
                contig, tile, strand = stratum
                for (read_id,) in candidate_db.execute(
                    "SELECT read_id FROM candidates WHERE contig = ? AND tile = ? AND strand = ? "
                    "ORDER BY rank, read_id LIMIT ?",
                    (contig, tile, strand, quotas[stratum]),
                ):
                    selected_ids_set.add(str(read_id))
                    selected_strata[str(read_id)] = stratum
            candidate_db.close()
            candidate_db = None
            candidate_db_path.unlink()
            selected: dict[str, list[Any]] = {read_id: [] for read_id in selected_ids_set}
            selected_record_total = 0
            source_handle.seek(0)
            with pysam.AlignmentFile(_descriptor_path(source_handle.fileno()), "rb") as source:
                for read in source.fetch(until_eof=True):
                    if time.monotonic() > deadline:
                        raise AlignmentSessionError("alignment presentation time limit exceeded")
                    if (
                        read.query_name in selected and not read.is_unmapped
                        and not read.is_secondary and not read.is_supplementary
                    ):
                        selected[read.query_name].append(read)
                        selected_record_total += 1
                        if selected_record_total > ALIGNMENT_PREVIEW_MAX_RECORDS:
                            evicted = max(
                                (read_id for read_id, records in selected.items() if records),
                                key=lambda value: _rank_read(bam_sha256, value),
                            )
                            selected_record_total -= len(selected[evicted])
                            selected.pop(evicted)
            coverage_path = temporary / "full-source-primary-coverage.bedgraph"
            with coverage_path.open("w", encoding="utf-8", newline="\n") as output:
                for contig, length in references:
                    for bin_index, aligned_bases in enumerate(coverage[contig]):
                        if aligned_bases:
                            start = bin_index * bin_width
                            end = min(length, start + bin_width)
                            output.write(f"{contig}\t{start}\t{end}\t{aligned_bases / (end - start):.6f}\n")
            if coverage_path.stat().st_size > ALIGNMENT_COVERAGE_MAX_BYTES:
                raise AlignmentSessionError("alignment presentation coverage byte ceiling exceeded")
            retained_ids = sorted(selected, key=lambda value: _rank_read(bam_sha256, value))
            preview_path = temporary / "alignment-preview.bam"
            while True:
                try:
                    selected_record_count = _write_bam_for_ids_bounded(
                        preview_path,
                        Path(_descriptor_path(source_handle.fileno())),
                        retained_ids,
                        byte_limit=max_output_bytes,
                        deadline=deadline,
                        label="alignment presentation",
                    )
                    break
                except _AlignmentDerivativeByteLimit:
                    if not retained_ids:
                        raise AlignmentSessionError("alignment preview byte ceiling is too small")
                    retained_ids.pop()
            if time.monotonic() > deadline:
                raise AlignmentSessionError("alignment presentation time limit exceeded")
            _index_bam_with_deadline(
                preview_path,
                deadline=deadline,
                label="alignment presentation",
                byte_limit=ALIGNMENT_PREVIEW_INDEX_MAX_BYTES,
            )
            preview_index = Path(f"{preview_path}.bai")
            if preview_index.stat().st_size > ALIGNMENT_PREVIEW_INDEX_MAX_BYTES:
                raise AlignmentSessionError("alignment presentation index byte ceiling exceeded")
            outputs = {}
            for key, path in (("bam", preview_path), ("index", preview_index), ("coverage", coverage_path)):
                digest, size = _sha256_file_and_size(path)
                outputs[key] = {"sha256": digest, "size_bytes": size}
            selected_forward = sum(not record.is_reverse for read_id in retained_ids for record in selected[read_id])
            selected_reverse = sum(record.is_reverse for read_id in retained_ids for record in selected[read_id])
            selected_contig_counts: dict[str, int] = {}
            selected_stratum_counts: dict[str, int] = {}
            for read_id in retained_ids:
                contig, tile, strand = selected_strata[read_id]
                selected_contig_counts[contig] = selected_contig_counts.get(contig, 0) + 1
                key = f"{contig}:{tile}:{strand}"
                selected_stratum_counts[key] = selected_stratum_counts.get(key, 0) + 1
            unrepresented = [
                f"{contig}:{tile}:{strand}" for contig, tile, strand in strata
                if selected_stratum_counts.get(f"{contig}:{tile}:{strand}", 0) == 0
            ]
            manifest = {
                "schema": "bms.ngs.alignment-presentation-manifest.v3", "authority_sha256": cache_key,
                "authority": authority,
                "job_id": job_id, "session_id": session_id, "mode": mode,
                "source_manifest_sha256": source_manifest_sha256,
                "package_manifest_sha256": source_manifest_sha256,
                "artifact_set_sha256": artifact_set_sha256,
                "alignment_pair_sha256": alignment_pair_sha256,
                "creation_revision": creation_revision, "creation_source_tree": creation_source_tree,
                "source_alignment_sha256": bam_sha256, "source_alignment_size_bytes": bam_size_bytes,
                "source_index_sha256": index_sha256, "source_index_size_bytes": index_size_bytes,
                "source_alignment_relative_path": source_alignment_relative_path,
                "source_index_relative_path": source_index_relative_path,
                "source_identity": _canonical_stat_identity(source_identity),
                "source_index_identity": _canonical_stat_identity(index_identity),
                "policy": authority["policy"],
                "generation_limits": {"max_seconds": max_seconds, "max_concurrent_generations": 1},
                "runtime": {"pysam_version": pysam.__version__},
                "selected_read_set_sha256": _selected_set_digest(retained_ids),
                "selection_unit": "unique mapped primary read ID",
                "inclusion_rules": ["mapped", "primary", "query_name present"],
                "exclusion_rules": ["secondary", "supplementary", "unmapped", "missing query_name"],
                "selected_read_count": len(retained_ids),
                "selected_alignment_record_count": selected_record_count,
                "selected_record_counts": {
                    "mapped_primary": selected_record_count, "secondary": 0,
                    "supplementary": 0, "unmapped": 0,
                },
                "source_alignment_record_count": source_alignment_records,
                "source_record_counts": source_record_counts,
                "source_primary_mapped_read_count": source_primary_read_count,
                "source_primary_mapped_alignment_record_count": source_records,
                "source_strand_counts": {"forward": forward, "reverse": reverse},
                "selected_strand_counts": {"forward": selected_forward, "reverse": selected_reverse},
                "source_flag_counts": flag_counts, "coverage_bin_width": bin_width,
                "coverage_semantics": "mean primary mapped alignment depth from full source",
                "tile_policy": {"id": "reference-tile-strand-largest-remainder", "version": 1,
                                "tiles_per_contig": 64, "tile_widths_bp": tile_widths},
                "source_contig_counts": source_contig_counts,
                "selected_contig_counts": selected_contig_counts,
                "selected_stratum_counts": selected_stratum_counts,
                "unrepresented_strata": unrepresented,
                "output_byte_ceiling": max_output_bytes, "outputs": outputs,
            }
            manifest_bytes = rfc8785.dumps(manifest)
            if len(manifest_bytes) > ALIGNMENT_PRESENTATION_MANIFEST_MAX_BYTES:
                raise AlignmentSessionError("alignment presentation manifest byte ceiling exceeded")
            package_size = len(manifest_bytes) + sum(item["size_bytes"] for item in outputs.values())
            if package_size > ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES:
                raise AlignmentSessionError("alignment presentation package byte ceiling exceeded")
            manifest_handle = (temporary / "manifest.json").open("wb")
            try:
                manifest_handle.write(manifest_bytes)
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            finally:
                manifest_handle.close()
            os.rename(".generation.tmp", cache_key, src_dir_fd=namespace_fd, dst_dir_fd=namespace_fd)
            directory_fd = os.dup(namespace_fd)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            _remove_locus_transient(namespace_fd, ".generation.tmp")
            if isinstance(exc, AlignmentSessionError):
                raise
            raise AlignmentSessionError(f"alignment presentation generation failed: {exc}") from exc
        finally:
            if candidate_db is not None:
                candidate_db.close()
            if index_handle is not None:
                index_handle.close()
            if source_handle is not None:
                source_handle.close()
        with open_presentation_authority_root(destination, create=False) as pinned_destination:
            package = _load_derived_package(
                pinned_destination,
                expected_authority_sha256=cache_key,
            )
        if package is None:
            raise AlignmentSessionError("alignment presentation failed integrity validation")
        _cleanup_presentation_namespace(
            namespace,
            namespace_fd,
            active=destination,
            protected_names=protected_names,
        )
        return package


def build_alignment_preview(
    bam: Path, *, bam_sha256: str, bam_size_bytes: int, index: Path, index_sha256: str,
    index_size_bytes: int, cache_root: Path | None = None,
    target_reads: int = ALIGNMENT_PREVIEW_TARGET_READS,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    """Compatibility wrapper for the governed presentation package."""
    package = build_alignment_presentation(
        bam, bam_sha256=bam_sha256, bam_size_bytes=bam_size_bytes, index=index,
        index_sha256=index_sha256, index_size_bytes=index_size_bytes,
        source_manifest_sha256=hashlib.sha256(
            rfc8785.dumps({"bam": bam_sha256, "bai": index_sha256})
        ).hexdigest(),
        job_id="legacy-preview", session_id=hashlib.sha256(bam_sha256.encode()).hexdigest()[:24],
        mode="primary", cache_root=cache_root, target_reads=target_reads,
    )
    return package["bam_path"], package["bam_metadata"], package["index_path"], package["index_metadata"]


@_pin_locus_cache_root(create=True)
def build_alignment_locus_slice(
    bam: Path, *, bam_sha256: str, bam_size_bytes: int, index: Path, index_sha256: str,
    index_size_bytes: int, source_identity: dict[str, int], source_index_identity: dict[str, int],
    source_manifest_sha256: str, presentation_authority_sha256: str,
    presentation_manifest_sha256: str,
    job_id: str, session_id: str, contig: str, start: int, end: int, max_reads: int,
    cache_root: Path | None = None, max_records: int = LOCUS_MAX_RECORDS,
    max_output_bytes: int = LOCUS_MAX_BYTES, max_seconds: float = LOCUS_MAX_SECONDS,
) -> dict[str, Any]:
    creation_revision, creation_source_tree = _creation_authority()
    if not SAFE_CONTIG_RE.fullmatch(contig):
        raise AlignmentSessionError("unsafe contig")
    if start < 1 or end < start or end - start + 1 > LOCUS_MAX_SPAN:
        raise AlignmentSessionError("locus span is invalid")
    if (
        max_reads < 1 or max_reads > LOCUS_MAX_READS or max_records < 1
        or max_records > LOCUS_MAX_RECORDS
        or max_output_bytes < 1 or max_output_bytes > LOCUS_MAX_BYTES
        or not math.isfinite(max_seconds) or max_seconds <= 0 or max_seconds > LOCUS_MAX_SECONDS
    ):
        raise AlignmentSessionError("locus policy is invalid")
    if source_stat_identity(bam) != source_identity or source_stat_identity(index) != source_index_identity:
        raise AlignmentSessionError("source identity mismatch")
    policy = {
        "id": "bounded-full-source-locus-slice", "version": 1,
        "max_reads": max_reads, "max_records": max_records,
        "max_bytes": max_output_bytes, "max_span_bp": LOCUS_MAX_SPAN,
        "max_seconds": max_seconds,
    }
    authority = {
        "schema": "bms.ngs.alignment-locus-authority.v2", "job_id": job_id, "session_id": session_id,
        "presentation_authority_sha256": presentation_authority_sha256,
        "presentation_manifest_sha256": presentation_manifest_sha256,
        "source_manifest_sha256": source_manifest_sha256, "source_alignment_sha256": bam_sha256,
        "source_alignment_size_bytes": bam_size_bytes, "source_index_sha256": index_sha256,
        "source_index_size_bytes": index_size_bytes, "contig": contig, "start_1based": start,
        "end_1based": end, "source_identity": _canonical_stat_identity(source_identity),
        "source_index_identity": _canonical_stat_identity(source_index_identity), "policy": policy,
    }
    slice_id = hashlib.sha256(rfc8785.dumps(authority)).hexdigest()
    cache_base = _locus_cache_root(cache_root)
    root = cache_base / slice_id
    manifest_path = root / "manifest.json"
    deadline = time.monotonic() + max_seconds
    slot, slot_handle = _acquire_locus_generation_slot(cache_base, slice_id)
    try:
        cache_parts = cache_base.parts
        if not (len(cache_parts) == 5 and cache_parts[1:4] == ("proc", "self", "fd") and cache_parts[4].isdigit()):
            raise AlignmentSessionError("locus cache root is not pinned")
        cache_fd = int(cache_parts[4])
        temporary_name = f".generation-slot-{slot}.tmp"
        orphan_name = f".generation-slot-{slot}.orphan"
        _remove_locus_transient(cache_fd, temporary_name)
        _remove_locus_transient(cache_fd, orphan_name)
        _cleanup_legacy_locus_transients(cache_base, cache_fd)
        if root.exists() or root.is_symlink():
            os.rename(slice_id, orphan_name, src_dir_fd=cache_fd, dst_dir_fd=cache_fd)
            _remove_locus_transient(cache_fd, orphan_name)
        _cleanup_locus_cache(
            cache_base,
            reserve_bytes=(
                max_output_bytes
                + LOCUS_INDEX_MAX_BYTES
                + LOCUS_MANIFEST_MAX_BYTES
                + ALIGNMENT_SELECTED_IDS_MAX_BYTES
            ),
            reserve_entries=1,
        )
        os.mkdir(temporary_name, mode=0o750, dir_fd=cache_fd)
        temporary = cache_base / temporary_name
        try:
            candidate_heap: list[tuple[int, str]] = []
            overlapping_ids: set[str] = set()
            with pysam.AlignmentFile(
                str(bam), "rb", index_filename=str(index),
            ) as source:
                header = source.header.to_dict()
                for read in source.fetch(contig, start - 1, end):
                    if time.monotonic() > deadline:
                        raise AlignmentSessionError("locus slice time limit exceeded")
                    if read.is_unmapped or read.is_secondary or read.is_supplementary or not read.query_name:
                        continue
                    if read.query_name in overlapping_ids:
                        continue
                    overlapping_ids.add(read.query_name)
                    rank = int(_rank_read(bam_sha256, read.query_name), 16)
                    item = (-rank, read.query_name)
                    if len(candidate_heap) < max_reads:
                        heapq.heappush(candidate_heap, item)
                    elif rank < -candidate_heap[0][0]:
                        heapq.heapreplace(candidate_heap, item)
            overlapping = len(overlapping_ids)
            active_ids = {read_id for _rank, read_id in candidate_heap}
            candidates: dict[str, list[Any]] = {}
            total_records = 0
            record_cap_applied = False
            with pysam.AlignmentFile(
                str(bam), "rb", index_filename=str(index),
            ) as source:
                for read in source.fetch(contig, start - 1, end):
                    if time.monotonic() > deadline:
                        raise AlignmentSessionError("locus slice time limit exceeded")
                    if read.query_name not in active_ids or read.is_unmapped or read.is_secondary:
                        continue
                    candidates.setdefault(read.query_name, []).append(read)
                    total_records += 1
                    while total_records > max_records and candidates:
                        evicted = max(candidates, key=lambda read_id: _rank_read(bam_sha256, read_id))
                        total_records -= len(candidates.pop(evicted))
                        active_ids.discard(evicted)
                        record_cap_applied = True
            selected_ids = sorted(candidates, key=lambda read_id: _rank_read(bam_sha256, read_id))
            locus_bam = temporary / "locus.bam"
            byte_cap_applied = False
            while True:
                try:
                    selected_records = _write_bam_for_ids_bounded(
                        locus_bam,
                        bam,
                        selected_ids,
                        byte_limit=max_output_bytes,
                        deadline=deadline,
                        label="locus slice",
                        index_path=index,
                        contig=contig,
                        start=start,
                        end=end,
                        include_supplementary=True,
                    )
                    break
                except _AlignmentDerivativeByteLimit:
                    if not selected_ids:
                        raise AlignmentSessionError("locus slice byte ceiling is too small")
                    selected_ids.pop()
                    byte_cap_applied = True
            if time.monotonic() > deadline:
                raise AlignmentSessionError("locus slice time limit exceeded")
            _index_bam_with_deadline(
                locus_bam,
                deadline=deadline,
                label="locus slice",
                byte_limit=LOCUS_INDEX_MAX_BYTES,
            )
            locus_index = Path(f"{locus_bam}.bai")
            if locus_index.stat().st_size > LOCUS_INDEX_MAX_BYTES:
                raise AlignmentSessionError("locus slice index byte ceiling exceeded")
            if source_stat_identity(bam) != source_identity or source_stat_identity(index) != source_index_identity:
                raise AlignmentSessionError("source identity changed during locus slice generation")
            receipt = {
                "schema": "bms.ngs.alignment-locus-slice-manifest.v2", "slice_id": slice_id,
                "job_id": job_id, "session_id": session_id,
                "presentation_authority_sha256": presentation_authority_sha256,
                "presentation_manifest_sha256": presentation_manifest_sha256,
                "source_manifest_sha256": source_manifest_sha256,
                "source_alignment_sha256": bam_sha256,
                "source_alignment_size_bytes": bam_size_bytes,
                "source_index_sha256": index_sha256,
                "source_index_size_bytes": index_size_bytes,
                "source_identity": _canonical_stat_identity(source_identity),
                "source_index_identity": _canonical_stat_identity(source_index_identity),
                "contig": contig, "start_1based": start, "end_1based": end,
                "overlapping_read_count": overlapping, "selected_read_count": len(selected_ids),
                "selected_record_count": selected_records,
                "capped": len(selected_ids) < overlapping or record_cap_applied or byte_cap_applied,
                "cap_reasons": [
                    reason for reason, applied in (
                        ("read_limit", overlapping > max_reads),
                        ("record_limit", record_cap_applied),
                        ("byte_limit", byte_cap_applied),
                    ) if applied
                ],
                "selected_read_set_sha256": _selected_set_digest(selected_ids),
                "creation_revision": creation_revision, "creation_source_tree": creation_source_tree,
                "selection_unit": "unique mapped primary read ID with associated supplementary records",
                "inclusion_rules": [
                    "mapped primary overlaps requested locus",
                    "associated mapped supplementary for admitted primary identity",
                    "query_name present",
                ],
                "exclusion_rules": [
                    "secondary", "unmapped", "missing query_name", "primary outside requested locus",
                ],
                "flag_policy": "primary-overlap admission; associated supplementary retained; secondary and unmapped excluded",
                "selected_record_counts": {
                    "primary": sum(
                        not record.is_supplementary for read_id in selected_ids for record in candidates[read_id]
                    ),
                    "supplementary": sum(
                        record.is_supplementary for read_id in selected_ids for record in candidates[read_id]
                    ),
                    "secondary": 0, "unmapped": 0,
                    "forward": sum(
                        not record.is_reverse for read_id in selected_ids for record in candidates[read_id]
                    ),
                    "reverse": sum(
                        record.is_reverse for read_id in selected_ids for record in candidates[read_id]
                    ),
                },
                "policy": policy,
                "generation_limits": {
                    "max_seconds": max_seconds,
                    "max_concurrent_generations": LOCUS_GENERATION_CONCURRENCY,
                },
                "outputs": {},
            }
            for key, path in (("bam", locus_bam), ("index", locus_index)):
                digest, size = _sha256_file_and_size(path)
                receipt["outputs"][key] = {"sha256": digest, "size_bytes": size}
            manifest_bytes = rfc8785.dumps(receipt)
            if len(manifest_bytes) > LOCUS_MANIFEST_MAX_BYTES:
                raise AlignmentSessionError("locus slice manifest byte ceiling exceeded")
            manifest_handle = (temporary / "manifest.json").open("wb")
            try:
                manifest_handle.write(manifest_bytes)
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            finally:
                manifest_handle.close()
            os.rename(temporary_name, slice_id, src_dir_fd=cache_fd, dst_dir_fd=cache_fd)
            directory_fd = os.dup(cache_fd)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            _cleanup_locus_cache(cache_base, active=root)
        except Exception as exc:
            _remove_locus_transient(cache_fd, temporary_name)
            if isinstance(exc, AlignmentSessionError):
                raise
            raise AlignmentSessionError(f"locus slice generation failed: {exc}") from exc
    finally:
        try:
            fcntl.flock(slot_handle.fileno(), fcntl.LOCK_UN)
        finally:
            slot_handle.close()
    return resolve_cached_alignment_locus_slice(slice_id, cache_root=cache_root)


@_pin_locus_cache_root(create=False)
def resolve_cached_alignment_locus_slice(slice_id: str, *, cache_root: Path | None = None) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", slice_id) is None:
        raise AlignmentSessionError("alignment locus slice not found")
    root = _locus_cache_root(cache_root) / slice_id
    manifest_path, bam_path, index_path = root / "manifest.json", root / "locus.bam", root / "locus.bam.bai"
    if not all(path.is_file() and not path.is_symlink() for path in (manifest_path, bam_path, index_path)):
        raise AlignmentSessionError("alignment locus slice not found")
    receipt = json.loads(manifest_path.read_bytes())
    if receipt.get("slice_id") != slice_id:
        raise AlignmentSessionError("alignment locus slice integrity mismatch")
    authority = {
        "schema": "bms.ngs.alignment-locus-authority.v2",
        "job_id": receipt.get("job_id"),
        "session_id": receipt.get("session_id"),
        "presentation_authority_sha256": receipt.get("presentation_authority_sha256"),
        "presentation_manifest_sha256": receipt.get("presentation_manifest_sha256"),
        "source_manifest_sha256": receipt.get("source_manifest_sha256"),
        "source_alignment_sha256": receipt.get("source_alignment_sha256"),
        "source_alignment_size_bytes": receipt.get("source_alignment_size_bytes"),
        "source_index_sha256": receipt.get("source_index_sha256"),
        "source_index_size_bytes": receipt.get("source_index_size_bytes"),
        "contig": receipt.get("contig"),
        "start_1based": receipt.get("start_1based"),
        "end_1based": receipt.get("end_1based"),
        "source_identity": receipt.get("source_identity"),
        "source_index_identity": receipt.get("source_index_identity"),
        "policy": receipt.get("policy"),
    }
    if hashlib.sha256(rfc8785.dumps(authority)).hexdigest() != slice_id:
        raise AlignmentSessionError("alignment locus slice authority mismatch")
    for key, path in (("bam", bam_path), ("index", index_path)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if receipt["outputs"][key] != {"sha256": digest, "size_bytes": path.stat().st_size}:
            raise AlignmentSessionError("alignment locus slice integrity mismatch")
    os.utime(manifest_path, None)
    source_manifest = receipt["source_manifest_sha256"]
    return {"slice_id": slice_id, "bam_path": bam_path, "index_path": index_path,
            "manifest_path": manifest_path, "receipt": receipt,
            "bam_metadata": _derived_metadata(bam_path, "alignment_locus_slice", source_manifest),
            "index_metadata": _derived_metadata(index_path, "alignment_locus_slice_index", source_manifest),
            "manifest_metadata": _derived_metadata(manifest_path, "alignment_locus_slice_manifest", source_manifest)}


def _iter_sam_lines(
    bam: Path,
    *,
    bam_sha256: str | None = None,
    bam_size_bytes: int | None = None,
    index: Path | None = None,
    index_sha256: str | None = None,
    index_size_bytes: int | None = None,
    contig: str | None = None,
    start: int | None = None,
    end: int | None = None,
    primary_only: bool = False,
    scan_limit: int = MAX_READ_SCAN,
) -> Iterator[str]:
    if scan_limit < 1 or scan_limit > LOCUS_MAX_RECORDS:
        raise AlignmentSessionError("read scan limit is invalid")
    samtools = _samtools_command()
    samtools.verify_runtime()
    snapshots: list[BinaryIO] = []
    try:
        if bam_sha256 is None or bam_size_bytes is None:
            bam_sha256, bam_size_bytes = _sha256_file_and_size(bam)
        bam_snapshot = open_verified_artifact_snapshot(bam, expected_size=bam_size_bytes, expected_sha256=bam_sha256)
        snapshots.append(bam_snapshot)
        command = [*samtools, "view"]
        if primary_only:
            command.extend(["-F", "2308"])
        if index is not None:
            if index_sha256 is None or index_size_bytes is None:
                index_sha256, index_size_bytes = _sha256_file_and_size(index)
            index_snapshot = open_verified_artifact_snapshot(
                index,
                expected_size=index_size_bytes,
                expected_sha256=index_sha256,
            )
            snapshots.append(index_snapshot)
            command.extend(["-X", _descriptor_path(bam_snapshot.fileno()), _descriptor_path(index_snapshot.fileno())])
        else:
            command.append(_descriptor_path(bam_snapshot.fileno()))
        if contig is not None:
            if not SAFE_CONTIG_RE.fullmatch(contig):
                raise AlignmentSessionError("unsafe contig")
            if start is not None or end is not None:
                if start is None or end is None or start < 1 or end < start:
                    raise AlignmentSessionError("invalid read locus")
                command.append(f"{contig}:{start}-{end}")
            else:
                command.append(contig)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(*samtools.pass_fds, *(snapshot.fileno() for snapshot in snapshots)),
        )
    except Exception:
        for snapshot in reversed(snapshots):
            snapshot.close()
        raise
    assert process.stdout is not None
    lines: list[str] = []
    bounded_stop = False
    return_code: int | None = None
    stderr = ""
    try:
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            if len(lines) > scan_limit:
                bounded_stop = True
                break
    finally:
        process.stdout.close()
        if bounded_stop and process.poll() is None:
            process.terminate()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=5)
        if process.stderr is not None:
            stderr = process.stderr.read()
            process.stderr.close()
        samtools.verify_runtime()
        for snapshot in reversed(snapshots):
            snapshot.close()
    if not bounded_stop and return_code != 0:
        raise AlignmentSessionError(f"samtools read inspection failed: {stderr.strip() or 'unknown error'}")
    yield from lines


def _mean_quality(quality: str) -> float | None:
    if not quality or quality == "*":
        return None
    return sum(ord(char) - 33 for char in quality) / len(quality)


def _alignment_quality_metrics(cigar: str, optional_fields: list[str], read_length: int | None) -> dict[str, Any]:
    metric_names = (
        "aligned_query_bases", "aligned_reference_bases", "inserted_bases", "deleted_bases", "skipped_reference_bases",
        "clipped_bases", "edit_distance", "reference_substitution_count", "aligned_fraction",
        "clipped_fraction", "reference_substitution_rate", "reference_disagreement_rate",
    )
    valid_cigar = re.fullmatch(
        r"(?:[1-9]\d*H)?(?:[1-9]\d*S)?(?:[1-9]\d*[MIDNP=X])+(?:[1-9]\d*S)?(?:[1-9]\d*H)?",
        cigar,
    )
    operations = [(int(length), operation) for length, operation in re.findall(r"(\d+)([MIDNSHP=X])", cigar)]
    query_sequence_bases = sum(
        length for length, operation in operations if operation in {"M", "I", "S", "=", "X"}
    )
    if (
        valid_cigar is None
        or not operations
        or (read_length is not None and (read_length <= 0 or query_sequence_bases != read_length))
    ):
        return {name: None for name in metric_names}

    counts = {operation: sum(length for length, candidate in operations if candidate == operation) for operation in "MIDNSHP=X"}
    aligned_query_bases = counts["M"] + counts["="] + counts["X"] + counts["I"]
    aligned_reference_bases = counts["M"] + counts["="] + counts["X"] + counts["D"]
    clipped_bases = counts["S"] + counts["H"]
    original_query_length = read_length + counts["H"] if read_length is not None else None
    disagreement_denominator = aligned_reference_bases + counts["I"]

    nm_fields = [field for field in optional_fields if field.startswith("NM:")]
    edit_distance = None
    if len(nm_fields) == 1 and re.fullmatch(r"NM:i:\d+", nm_fields[0]):
        candidate_edit_distance = int(nm_fields[0][5:])
        minimum_edit_distance = counts["I"] + counts["D"] + counts["X"]
        if minimum_edit_distance <= candidate_edit_distance <= disagreement_denominator:
            edit_distance = candidate_edit_distance
    reference_substitution_count = (
        None if edit_distance is None else edit_distance - counts["I"] - counts["D"]
    )
    return {
        "aligned_query_bases": aligned_query_bases,
        "aligned_reference_bases": aligned_reference_bases,
        "inserted_bases": counts["I"],
        "deleted_bases": counts["D"],
        "skipped_reference_bases": counts["N"],
        "clipped_bases": clipped_bases,
        "edit_distance": edit_distance,
        "reference_substitution_count": reference_substitution_count,
        "aligned_fraction": aligned_query_bases / original_query_length if original_query_length else None,
        "clipped_fraction": clipped_bases / original_query_length if original_query_length else None,
        "reference_substitution_rate": (
            reference_substitution_count / disagreement_denominator
            if reference_substitution_count is not None and disagreement_denominator > 0 else None
        ),
        "reference_disagreement_rate": (
            edit_distance / disagreement_denominator
            if edit_distance is not None and disagreement_denominator > 0 else None
        ),
    }


def _sam_line_to_read(line: str, *, include_sequence: bool) -> dict[str, Any] | None:
    fields = line.split("\t")
    if len(fields) < 11:
        return None
    flag = int(fields[1])
    sequence = fields[9]
    quality = fields[10]
    read_length = None if sequence == "*" else len(sequence)
    cigar = None if fields[5] == "*" else fields[5]
    start_1based = int(fields[3]) if fields[3].isdigit() and int(fields[3]) > 0 else None
    alignment_end_1based = None
    if start_1based is not None and cigar is not None and re.fullmatch(
        r"(?:[1-9]\d*H)?(?:[1-9]\d*S)?(?:[1-9]\d*[MIDNP=X])+(?:[1-9]\d*S)?(?:[1-9]\d*H)?",
        cigar,
    ):
        reference_span = sum(
            int(length)
            for length, operation in re.findall(r"(\d+)([MIDNSHP=X])", cigar)
            if operation in {"M", "D", "N", "=", "X"}
        )
        if reference_span > 0:
            alignment_end_1based = start_1based + reference_span - 1
    row: dict[str, Any] = {
        "read_id": fields[0],
        "length": read_length,
        "mean_quality": _mean_quality(quality),
        "contig": None if fields[2] == "*" else fields[2],
        "start_1based": start_1based,
        "alignment_end_1based": alignment_end_1based,
        "strand": "-" if flag & 16 else "+",
        "mapq": int(fields[4]) if fields[4].isdigit() else None,
        "cigar": cigar,
        "flags": flag,
        "unmapped": bool(flag & 4),
        **_alignment_quality_metrics(fields[5], fields[11:], read_length),
    }
    if include_sequence:
        row["sequence"] = None if sequence == "*" else sequence
        row["quality"] = None if quality == "*" else quality
    return row


def _empty_dorado_move_metrics() -> dict[str, int | float | None]:
    return {
        "dorado_move_stride_samples": None,
        "dorado_emitted_bases": None,
        "mapped_signal_start_sample": None,
        "mapped_signal_end_sample": None,
        "mapped_signal_span_samples": None,
        "dorado_emission_rate_bases_per_second": None,
        "samples_per_aligned_reference_base": None,
    }


def dorado_move_metrics(
    optional_fields: list[str],
    *,
    signal_sample_count: int | None,
    sampling_rate_hz: int | float | None,
    aligned_reference_bases: int | None,
) -> dict[str, int | float | None]:
    """Decode exact Dorado move authority without claiming physical events."""
    move_fields = [field for field in optional_fields if field.startswith("mv:")]
    ts_fields = [field for field in optional_fields if field.startswith("ts:")]
    ns_fields = [field for field in optional_fields if field.startswith("ns:")]
    if len(move_fields) != 1 or len(ts_fields) != 1 or len(ns_fields) != 1:
        return _empty_dorado_move_metrics()
    if (
        re.fullmatch(r"mv:B:c,(?:\d+,)*\d+", move_fields[0]) is None
        or re.fullmatch(r"ts:i:\d+", ts_fields[0]) is None
        or re.fullmatch(r"ns:i:\d+", ns_fields[0]) is None
    ):
        return _empty_dorado_move_metrics()
    encoded = [int(value) for value in move_fields[0].split(",")[1:]]
    if len(encoded) < 2:
        return _empty_dorado_move_metrics()
    stride, moves = encoded[0], encoded[1:]
    start_sample = int(ts_fields[0][5:])
    end_sample = int(ns_fields[0][5:])
    if (
        stride < 1
        or any(value < 0 or value > 127 for value in moves)
        or signal_sample_count is None
        or isinstance(signal_sample_count, bool)
        or signal_sample_count < 1
        or start_sample < 0
        or end_sample <= start_sample
        or end_sample > signal_sample_count
        or sampling_rate_hz is None
        or isinstance(sampling_rate_hz, bool)
        or not math.isfinite(float(sampling_rate_hz))
        or float(sampling_rate_hz) <= 0
    ):
        return _empty_dorado_move_metrics()
    emitted_bases = sum(moves)
    span = end_sample - start_sample
    return {
        "dorado_move_stride_samples": stride,
        "dorado_emitted_bases": emitted_bases,
        "mapped_signal_start_sample": start_sample,
        "mapped_signal_end_sample": end_sample,
        "mapped_signal_span_samples": span,
        "dorado_emission_rate_bases_per_second": emitted_bases * float(sampling_rate_hz) / span,
        "samples_per_aligned_reference_base": (
            span / aligned_reference_bases
            if isinstance(aligned_reference_bases, int) and not isinstance(aligned_reference_bases, bool)
            and aligned_reference_bases > 0 else None
        ),
    }


def read_locus_primary_rows(
    bam: Path,
    *,
    bam_sha256: str,
    bam_size_bytes: int,
    index: Path,
    index_sha256: str,
    index_size_bytes: int,
) -> list[dict[str, Any]]:
    """Read every admitted primary identity from one immutable bounded locus slice."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in _iter_sam_lines(
        bam, bam_sha256=bam_sha256, bam_size_bytes=bam_size_bytes,
        index=index, index_sha256=index_sha256, index_size_bytes=index_size_bytes,
        primary_only=True, scan_limit=LOCUS_MAX_RECORDS,
    ):
        fields = line.split("\t")
        row = _sam_line_to_read(line, include_sequence=False)
        if row is None:
            continue
        read_id = str(row["read_id"])
        if read_id in seen:
            raise AlignmentSessionError("locus slice contains duplicate primary read identities")
        seen.add(read_id)
        row["_dorado_optional_fields"] = fields[11:]
        rows.append(row)
        if len(rows) > LOCUS_MAX_READS:
            raise AlignmentSessionError("locus slice exceeds the admitted read population")
    return rows


def enrich_locus_read_metrics(
    reads: list[dict[str, Any]], raw_metrics: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for source in reads:
        row = dict(source)
        optional_fields = list(row.pop("_dorado_optional_fields", []))
        raw = raw_metrics.get(str(row["read_id"]))
        if raw is not None:
            row.update(dict(raw))
        row.update(dorado_move_metrics(
            optional_fields,
            signal_sample_count=row.get("sample_count"),
            sampling_rate_hz=row.get("sampling_rate_hz"),
            aligned_reference_bases=row.get("aligned_reference_bases"),
        ))
        enriched.append(row)
    return enriched


def _sortable_read_query_sha256(
    *, authority_sha256: str, sort_by: str, sort_direction: str, query: str,
    metric_min: float | None, metric_max: float | None,
) -> str:
    payload = {
        "schema": "bms.ngs.sortable-read-query.v1",
        "authority_sha256": authority_sha256,
        "sort_by": sort_by,
        "sort_direction": sort_direction,
        "query": query,
        "metric_min": metric_min,
        "metric_max": metric_max,
        "null_order": "last",
        "tie_breaker": ["read_id", "start_1based", "flags"],
    }
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def _sortable_read_cursor(query_sha256: str, offset: int) -> str:
    encoded = json.dumps(
        {"v": 1, "q": query_sha256, "o": offset},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _parse_sortable_read_cursor(cursor: str | None, query_sha256: str) -> int:
    if cursor in (None, ""):
        return 0
    if len(str(cursor).encode("ascii", errors="ignore")) > MAX_SORTABLE_READ_CURSOR_BYTES:
        raise AlignmentSessionError("sortable read cursor is invalid")
    try:
        token = str(cursor)
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.b64decode(token + padding, altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise AlignmentSessionError("sortable read cursor is invalid") from exc
    offset = payload.get("o") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "q", "o"}
        or payload.get("v") != 1
        or payload.get("q") != query_sha256
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or offset > LOCUS_MAX_READS
    ):
        raise AlignmentSessionError("sortable read cursor does not match the query authority")
    return offset


def sort_locus_read_metrics_page(
    reads: list[dict[str, Any]], *, authority_sha256: str, sort_by: str,
    sort_direction: str, query: str | None, metric_min: float | None,
    metric_max: float | None, cursor: str | None, limit: int,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", authority_sha256):
        raise AlignmentSessionError("sortable read authority is invalid")
    if sort_by not in SORTABLE_READ_FIELDS or sort_direction not in {"asc", "desc"}:
        raise AlignmentSessionError("sortable read order is invalid")
    if limit < 1 or limit > MAX_READ_PAGE:
        raise AlignmentSessionError("sortable read limit is invalid")
    if metric_min is not None and (isinstance(metric_min, bool) or not math.isfinite(metric_min)):
        raise AlignmentSessionError("sortable read metric minimum is invalid")
    if metric_max is not None and (isinstance(metric_max, bool) or not math.isfinite(metric_max)):
        raise AlignmentSessionError("sortable read metric maximum is invalid")
    if metric_min is not None and metric_max is not None and metric_min > metric_max:
        raise AlignmentSessionError("sortable read metric range is invalid")
    needle = (query or "").strip().lower()
    if len(needle) > 255:
        raise AlignmentSessionError("sortable read query is invalid")

    filtered: list[dict[str, Any]] = []
    for row in reads:
        if needle and needle not in str(row.get("read_id", "")).lower():
            continue
        value = row.get(sort_by)
        if metric_min is not None or metric_max is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                continue
            if metric_min is not None and float(value) < metric_min:
                continue
            if metric_max is not None and float(value) > metric_max:
                continue
        filtered.append(row)

    def compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
        left_value, right_value = left.get(sort_by), right.get(sort_by)
        left_null, right_null = left_value is None, right_value is None
        if left_null != right_null:
            return 1 if left_null else -1
        if not left_null and left_value != right_value:
            direction = 1 if sort_direction == "asc" else -1
            return direction if left_value > right_value else -direction
        left_tie = (str(left.get("read_id", "")), int(left.get("start_1based") or 0), int(left.get("flags") or 0))
        right_tie = (str(right.get("read_id", "")), int(right.get("start_1based") or 0), int(right.get("flags") or 0))
        return (left_tie > right_tie) - (left_tie < right_tie)

    filtered.sort(key=cmp_to_key(compare))
    query_sha256 = _sortable_read_query_sha256(
        authority_sha256=authority_sha256, sort_by=sort_by, sort_direction=sort_direction,
        query=needle, metric_min=metric_min, metric_max=metric_max,
    )
    offset = _parse_sortable_read_cursor(cursor, query_sha256)
    if offset > len(filtered):
        raise AlignmentSessionError("sortable read cursor exceeds the result population")
    page = filtered[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "reads": page,
        "next_cursor": _sortable_read_cursor(query_sha256, next_offset) if next_offset < len(filtered) else None,
        "filtered_read_count": len(filtered),
        "null_order": "last",
        "tie_breaker": ["read_id", "start_1based", "flags"],
    }


def read_bam_page(
    bam: Path,
    *,
    bam_sha256: str | None = None,
    bam_size_bytes: int | None = None,
    index: Path | None = None,
    index_sha256: str | None = None,
    index_size_bytes: int | None = None,
    contig: str | None = None,
    start: int | None = None,
    end: int | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    include_sequence: bool = False,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_READ_PAGE:
        raise AlignmentSessionError(f"limit must be between 1 and {MAX_READ_PAGE}")
    if include_sequence and limit > MAX_SEQUENCE_PAGE:
        raise AlignmentSessionError(f"sequence detail limit must not exceed {MAX_SEQUENCE_PAGE}")
    offset = 0
    if cursor not in (None, ""):
        if not str(cursor).isdigit():
            raise AlignmentSessionError("invalid read cursor")
        offset = int(str(cursor))
        if offset > MAX_READ_CURSOR:
            raise AlignmentSessionError(f"read cursor must not exceed {MAX_READ_CURSOR}")
    query = (q or "").strip().lower()
    reads: list[dict[str, Any]] = []
    matched = 0
    scanned = 0
    has_more = False
    scan_truncated = False
    iterator = _iter_sam_lines(
        bam,
        bam_sha256=bam_sha256,
        bam_size_bytes=bam_size_bytes,
        index=index,
        index_sha256=index_sha256,
        index_size_bytes=index_size_bytes,
        contig=contig,
        start=start,
        end=end,
    )
    try:
        for line in iterator:
            row = _sam_line_to_read(line, include_sequence=include_sequence)
            if row is None:
                continue
            scanned += 1
            if scanned > MAX_READ_SCAN:
                scan_truncated = True
                break
            read_id = row["read_id"]
            if query and query not in read_id.lower():
                continue
            if matched < offset:
                matched += 1
                continue
            if len(reads) >= limit:
                has_more = True
                break
            reads.append(row)
            matched += 1
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    return {
        "reads": reads,
        "next_cursor": str(offset + len(reads)) if has_more else None,
        "limit": limit,
        "sequence_included": include_sequence,
        "scan_truncated": scan_truncated,
    }


def read_bam_exact(
    bam: Path,
    read_id: str,
    *,
    bam_sha256: str | None = None,
    bam_size_bytes: int | None = None,
    index: Path | None = None,
    index_sha256: str | None = None,
    index_size_bytes: int | None = None,
    contig: str | None = None,
    start: int | None = None,
    end: int | None = None,
) -> dict[str, Any]:
    """Find one exact read name without confusing bounded scan exhaustion with absence."""
    scanned = 0
    iterator = _iter_sam_lines(
        bam,
        bam_sha256=bam_sha256,
        bam_size_bytes=bam_size_bytes,
        index=index,
        index_sha256=index_sha256,
        index_size_bytes=index_size_bytes,
        contig=contig,
        start=start,
        end=end,
    )
    try:
        for line in iterator:
            row = _sam_line_to_read(line, include_sequence=True)
            if row is None:
                continue
            scanned += 1
            if scanned > MAX_READ_SCAN:
                return {"read": None, "scan_truncated": True}
            if row["read_id"] == read_id:
                return {"read": row, "scan_truncated": False}
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    return {"read": None, "scan_truncated": False}

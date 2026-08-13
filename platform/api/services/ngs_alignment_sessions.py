"""Job-scoped, fail-closed ONT alignment-session and bounded read helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Iterator, cast

from paths import get_results_dir
from services.ont_ngs_contract import DORADO_LOCK_PATH

SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,255}$")
SAFE_CONTIG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
DIMER_TOKENS = ("dimer", "multimer", "concatemer")
MAX_MANIFESTS = 64
MAX_READ_PAGE = 200
MAX_SEQUENCE_PAGE = 20
MAX_READ_CURSOR = 9_999
MAX_READ_SCAN = 10_000
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
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, component in enumerate(absolute.parts[1:]):
            final = index == len(absolute.parts[1:]) - 1
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
    if declared_job_root.is_symlink():
        raise AlignmentSessionError(f"unsafe symlink job root for {job_id!r}")
    job_root = declared_job_root.resolve()
    try:
        job_root.relative_to(root)
    except ValueError as exc:
        raise AlignmentSessionError(f"unsafe job root for {job_id!r}") from exc
    if not job_root.exists() or not job_root.is_dir():
        raise AlignmentSessionError(f"alignment sessions not found for job_id: {normalized}")
    return normalized, job_root


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
        relative = lexical.relative_to(root)
        current = root
        for component in relative.parts:
            current = current / component
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return None, "unsafe artifact: symlink component"
        if not stat.S_ISREG(current.lstat().st_mode):
            return None, "unsafe artifact: non-regular file"
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        return resolved, None
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
            payload, _raw_bytes, _digest, _size = _read_bounded_json_nofollow(
                safe_manifest,
                label="alignment-session manifest",
            )
        except AlignmentSessionError:
            continue
        rel_manifest = safe_manifest.relative_to(job_root).as_posix()
        session_metadata = payload.get("alignment_session") if isinstance(payload, dict) else None
        session_mode = session_metadata.get("mode") if isinstance(session_metadata, dict) else None
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
    mime_type = "application/octet-stream" if path.suffix.lower() in {".bam", ".bai", ".csi"} else (
        mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return {
        "artifact_id": identity,
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
        for required_role in ("alignment", "alignment_index", "reference"):
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
                elif not errors:
                    errors.append("a single authoritative reference contig is required")
            except (AlignmentSessionError, OSError, UnicodeError) as exc:
                if not errors:
                    errors.append(f"reference contig inspection failed: {type(exc).__name__}")
        session_seed = f"{job_id}\0{mode}\0" + "\0".join(
            artifacts[role]["artifact_id"] for role in sorted(artifacts)
        )
        session_id = hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:24]
        sessions.append(
            {
                "session_id": session_id,
                "job_id": job_id,
                "mode": mode,
                "reference_contig": reference_contig,
                "ready": not errors,
                "unavailable_reason": "; ".join(dict.fromkeys(errors)) or None,
                "artifacts": artifacts,
                "reads_url": f"/api/jobs/{job_id}/reads?session_id={session_id}",
            }
        )
    return sessions


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in session.items() if key != "artifacts"}
    public["artifacts"] = {
        role: {key: value for key, value in artifact.items() if key != "_path"}
        for role, artifact in session["artifacts"].items()
    }
    return public


def build_alignment_sessions(
    job_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
    return [
        _public_session(session)
        for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode)
    ]


def resolve_alignment_session(
    job_id: str,
    session_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["session_id"] == session_id:
            return _public_session(session)
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
) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
        raise AlignmentSessionError("alignment artifact not found")
    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
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
) -> dict[str, Any]:
    try:
        relative_path = path.relative_to(job_root)
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
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "kind": kind,
        "source": source,
        "relative_path": relative,
        "state": "present",
        "sha256": observed_sha256,
        "size_bytes": observed_size,
        "mime_type": mime_type,
        "url": f"/api/jobs/{job_id}/ngs-artifacts/{observed_sha256}",
        "range_capable": True,
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
        )
    ]
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        if artifact.get("state") != "present":
            descriptors.append(
                {
                    "kind": str(artifact.get("kind") or "artifact"),
                    "source": source,
                    "relative_path": None,
                    "state": str(artifact.get("state") or "unavailable"),
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
        descriptors.append(
            _package_artifact_descriptor(
                job_id,
                job_root,
                manifest_path.parent / raw_path,
                kind=str(artifact.get("kind") or "artifact"),
                source=source,
                declared_sha256=artifact.get("declared_sha256"),
                declared_size_bytes=artifact.get("declared_size_bytes"),
            )
        )
    return descriptors


def build_ngs_package_artifacts(
    job_id: str,
    *,
    source_reference_sha256: str,
    workflow_id: str,
    input_mode: str,
    results_dir: str | Path | None = None,
    job_output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Build a digest-bound inventory from canonical persisted NGS manifests."""
    from services.sequence_qc_manifest import SequenceQcManifestError, load_sequence_qc_manifest

    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
    if re.fullmatch(r"[0-9a-f]{64}", source_reference_sha256) is None:
        raise AlignmentSessionError("authorized source reference identity is required")
    sequence_candidates = (job_root / "fastq_qc" / "qc_manifest.json", job_root / "qc_manifest.json")
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
        descriptors.extend(
            _manifest_package_artifacts(
                safe_job_id,
                job_root,
                verification_path,
                verification_manifest,
                source="construct_verification",
                manifest_sha256=verification_digest,
                manifest_size_bytes=verification_size,
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
            )
        )
        reads_path = observed_state.get("source_reads_path") if isinstance(observed_state, dict) else None
        reads_digest = observed_state.get("source_reads_sha256") if isinstance(observed_state, dict) else None
        if input_mode == "fastq":
            reads_relative = Path(reads_path) if isinstance(reads_path, str) else None
            if (
                reads_relative is None
                or reads_relative.is_absolute()
                or any(part in {"", ".", ".."} for part in reads_relative.parts)
                or not isinstance(reads_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", reads_digest) is None
            ):
                raise AlignmentSessionError("retained FASTQ provenance is incomplete")
            descriptors.append(
                _package_artifact_descriptor(
                    safe_job_id,
                    job_root,
                    observed_state_path.parent / reads_relative,
                    kind="source_reads_fastq",
                    source="construct_verification_input",
                    declared_sha256=reads_digest,
                )
            )

    if input_mode == "fastq":
        descriptors.append(
            {
                "kind": "signal_data",
                "source": "input_mode",
                "relative_path": None,
                "state": "not_applicable_to_input_mode",
                "sha256": None,
                "size_bytes": None,
                "mime_type": None,
                "url": None,
                "range_capable": False,
                "unavailable_reason": "FASTQ input has no retained raw signal artifact",
            }
        )
    deduplicated: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for descriptor in descriptors:
        key = (str(descriptor["kind"]), descriptor.get("sha256"), descriptor.get("state"))
        deduplicated.setdefault(key, descriptor)
    return [
        {key: value for key, value in descriptor.items() if key != "_path"}
        for descriptor in deduplicated.values()
    ]


def resolve_ngs_package_artifact(
    job_id: str,
    sha256: str,
    **authority: Any,
) -> tuple[Path, dict[str, Any]]:
    if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise AlignmentSessionError("NGS package artifact not found")
    inventory = build_ngs_package_artifacts(job_id, **authority)
    for artifact in inventory:
        if artifact.get("sha256") != sha256 or artifact.get("state") != "present":
            continue
        _, job_root = _safe_job_root(
            job_id,
            authority.get("results_dir"),
            authority.get("job_output_dir"),
        )
        relative = artifact.get("relative_path")
        if not isinstance(relative, str):
            break
        path = job_root / relative
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
) -> tuple[Path, dict[str, Any]]:
    """Resolve one digest-bound artifact from an exact ready session."""
    if (
        mode not in SESSION_MODES
        or role not in LINKED_REPORT_ROLES
        or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
    ):
        raise AlignmentSessionError("alignment artifact not found")
    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
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
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    safe_job_id, job_root = _safe_job_root(job_id, results_dir, job_output_dir)
    for session in _session_records(safe_job_id, job_root, source_reference_sha256, workflow_id, input_mode):
        if session["session_id"] == session_id and session["ready"]:
            alignment = session["artifacts"]["alignment"]
            index = session["artifacts"]["alignment_index"]
            return alignment["_path"], alignment, index["_path"], index
    raise AlignmentSessionError("ready alignment session not found")


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
) -> Iterator[str]:
    samtools = _samtools_command()
    samtools.verify_runtime()
    snapshots: list[BinaryIO] = []
    try:
        if bam_sha256 is None or bam_size_bytes is None:
            bam_sha256, bam_size_bytes = _sha256_file_and_size(bam)
        bam_snapshot = open_verified_artifact_snapshot(bam, expected_size=bam_size_bytes, expected_sha256=bam_sha256)
        snapshots.append(bam_snapshot)
        command = [*samtools, "view"]
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
            if len(lines) > MAX_READ_SCAN:
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


def _sam_line_to_read(line: str, *, include_sequence: bool) -> dict[str, Any] | None:
    fields = line.split("\t")
    if len(fields) < 11:
        return None
    flag = int(fields[1])
    sequence = fields[9]
    quality = fields[10]
    row: dict[str, Any] = {
        "read_id": fields[0],
        "length": None if sequence == "*" else len(sequence),
        "mean_quality": _mean_quality(quality),
        "contig": None if fields[2] == "*" else fields[2],
        "start_1based": int(fields[3]) if fields[3].isdigit() and int(fields[3]) > 0 else None,
        "strand": "-" if flag & 16 else "+",
        "mapq": int(fields[4]) if fields[4].isdigit() else None,
        "cigar": None if fields[5] == "*" else fields[5],
        "flags": flag,
        "unmapped": bool(flag & 4),
    }
    if include_sequence:
        row["sequence"] = None if sequence == "*" else sequence
        row["quality"] = None if quality == "*" else quality
    return row


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

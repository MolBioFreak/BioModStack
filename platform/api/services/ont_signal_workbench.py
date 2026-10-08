"""Governed move-table, Squigualiser mapping, bounded view, and viewer-session contracts."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import stat
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import and_, event, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    InputFile,
    Job,
    OntExternalMoveBamRegistrationReceipt,
    OntInstrumentRunEvent,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalCalibrationArtifact,
    OntSignalCalibrationJob,
    OntSignalMappingArtifact,
    OntSignalMappingEvent,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSignalViewerSession,
    OntSquigualiserViewJob,
)
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateRevision,
    MolBioNGSGlobalBinding,
    MolBioNGSReferenceArtifact,
    MolBioNGSReferenceRevision,
)
from paths import get_allowed_roots, get_results_dir
from services import ngs_alignment_sessions

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_EXTERNAL_MOVE_SOURCE_ATTEMPTS = 3
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CONTIG = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
LEASE_SECONDS = 300
MAX_VIEW_HTML_BYTES = 48 * 1024 * 1024
MAX_VIEW_SVG_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_REGION_BP = 250_000
MAX_BASE_LIMIT = 100_000
MAX_SAMPLE_LIMIT = 2_000_000
MAX_PILEUP_READS = 100
MIN_CALIBRATION_READS = 1
MAX_CALIBRATION_READS = 100
CALIBRATION_CANDIDATE_KMER_LENGTH = 9
EXTERNAL_MOVE_BAM_ROOT_ENV = "BMS_ONT_EXTERNAL_MOVE_BAM_ROOT"
EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV = "BMS_ONT_EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE"
EXTERNAL_MOVE_BAM_CANDIDATE_KEY_BYTES = 32
EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE = "external move-BAM source is unavailable"
MAX_EXTERNAL_MOVE_BAM_CANDIDATES = 1000
MAX_EXTERNAL_MOVE_BAM_DEPTH = 8
MAX_EXTERNAL_MOVE_BAM_VISITED_ENTRIES = 10_000


class OntSignalError(ValueError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _public_time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_OMIT = object()
_PUBLIC_PATH_TEXT = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"file://[^\s\"',;\]\[()]+|"
    r"[A-Za-z]:[\\/][^\s\"',;\]\[()]+|"
    r"/(?!api/|ngs\?)[^\s\"',;\]\[()]+|"
    r"(?:\.{1,2}[\\/]|[A-Za-z0-9_.-]+[\\/])+[^\s\"',;\]\[()]+|"
    r"[A-Za-z0-9_.-]+\.(?:bam|ubam|bai|blow5|slow5|pod5|fast5|fastq|fq|fasta|fa|paf|gz|tbi|txt|json|html|svg|bed|csv|tsv|log)"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _public_clean(value: Any) -> Any:
    """Recursively remove every filesystem locator from a public projection."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if (
                _PUBLIC_PATH_TEXT.search(key)
                or "path" in normalized
                or "directory" in normalized
                or "filename" in normalized
                or normalized in {"managed_outputs", "managed_output", "output_files"}
            ):
                continue
            public_nested = _public_clean(nested)
            if public_nested is not _OMIT:
                cleaned[key] = public_nested
        return cleaned
    if isinstance(value, (list, tuple)):
        return [cleaned for item in value if (cleaned := _public_clean(item)) is not _OMIT]
    if isinstance(value, str):
        return _PUBLIC_PATH_TEXT.sub("[redacted-path]", value)
    return value


def _public_json(value: Any) -> Any:
    cleaned = _public_clean(value)
    return None if cleaned is _OMIT else cleaned


def _lexical_absolute_path(value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048 or not os.path.isabs(raw):
        return None
    if any(component in {"", ".", ".."} for component in raw.split(os.sep)[1:]):
        return None
    return Path(os.path.abspath(raw))


def _open_absolute_directory_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_job_owned_input(item: InputFile, source_job: Job) -> tuple[int, Path]:
    """Open a tracked job output beneath configured roots without following links."""
    filename = str(item.filename or "")
    if not filename or filename in {".", ".."} or Path(filename).name != filename:
        raise OntSignalError("tracked move-table input authority is invalid")
    directory = _lexical_absolute_path(item.directory)
    job_root = _lexical_absolute_path(source_job.child_output_dir or source_job.output_dir)
    if directory is None or job_root is None:
        raise OntSignalError("completed source job has no canonical owned output root")
    candidate = directory / filename
    approved_roots = tuple(
        root for raw_root in get_allowed_roots().values()
        if (root := _lexical_absolute_path(raw_root)) is not None
    )
    if not approved_roots:
        raise OntSignalError("configured move-table input roots are unavailable")
    if not any(job_root == root or root in job_root.parents for root in approved_roots):
        raise OntSignalError("completed source job output is outside approved roots")
    try:
        relative = candidate.relative_to(job_root)
    except ValueError as exc:
        raise OntSignalError("completed source job does not own the selected tracked input") from exc
    if not relative.parts:
        raise OntSignalError("completed source job does not own the selected tracked input")
    try:
        root_fd = _open_absolute_directory_nofollow(job_root)
        parent_fd = root_fd
        try:
            for component in relative.parts[:-1]:
                child_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
                if parent_fd != root_fd:
                    os.close(parent_fd)
                parent_fd = child_fd
            file_fd = os.open(
                relative.parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                os.close(file_fd)
                raise OntSignalError("move-table source must be a retained regular file")
            return file_fd, candidate
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)
            os.close(root_fd)
    except OntSignalError:
        raise
    except OSError as exc:
        raise OntSignalError("tracked move-table input cannot be opened without following symbolic links") from exc


def _open_job_owned_relative(source_job: Job, raw_relative: Any) -> int:
    """Open one producer receipt beneath the exact no-follow job output root."""
    job_root = _lexical_absolute_path(source_job.child_output_dir or source_job.output_dir)
    relative = Path(str(raw_relative or ""))
    if (
        job_root is None
        or relative.is_absolute()
        or not relative.parts
        or any(component in {"", ".", ".."} for component in relative.parts)
    ):
        raise OntSignalError("producer runtime provenance path authority is invalid")
    root_fd = _open_absolute_directory_nofollow(job_root)
    parent_fd = root_fd
    try:
        for component in relative.parts[:-1]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            if parent_fd != root_fd:
                os.close(parent_fd)
            parent_fd = child_fd
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OntSignalError("producer runtime provenance is not a retained regular file")
        return descriptor
    except OntSignalError:
        raise
    except OSError as exc:
        raise OntSignalError("producer runtime provenance cannot be opened without following symbolic links") from exc
    finally:
        if parent_fd != root_fd:
            os.close(parent_fd)
        os.close(root_fd)


def _read_bounded_descriptor(descriptor: int, *, limit: int, label: str) -> tuple[bytes, str]:
    before = os.fstat(descriptor)
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    total = 0
    while chunk := os.read(descriptor, min(64 * 1024, limit + 1 - total)):
        total += len(chunk)
        if total > limit:
            raise OntSignalError(f"{label} exceeds bounded policy")
        digest.update(chunk)
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise OntSignalError(f"{label} changed while reading")
    return b"".join(chunks), digest.hexdigest()


def _derive_source_runtime_identity(source_job: Job, artifact_sha256: str) -> dict[str, Any]:
    """Seal exact producer-owned Dorado runtime/model/move evidence."""
    provenance = source_job.provenance if isinstance(source_job.provenance, dict) else {}
    params = source_job.params if isinstance(source_job.params, dict) else {}
    if (
        "source_instrument_run_id" not in params
        or "source_instrument_observed_generation" not in params
    ):
        return {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": source_job.id,
            "source_bam_sha256": artifact_sha256,
            "reason_code": "producer_lineage_unavailable",
            "requires_independent_move_validation": True,
        }
    anchor = provenance.get("ont_dorado_terminal_products")
    if anchor is None:
        return {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": source_job.id,
            "source_bam_sha256": artifact_sha256,
            "reason_code": "producer_runtime_provenance_unavailable",
            "requires_independent_move_validation": True,
        }
    if not isinstance(anchor, dict):
        raise OntSignalError("producer runtime provenance anchor is malformed")
    identities = anchor.get("identities")
    products = anchor.get("products")
    runtime_product = products.get("dorado_runtime_provenance") if isinstance(products, dict) else None
    if (
        anchor.get("schema") != "biomodstack.ont_dorado_terminal_products.v1"
        or anchor.get("stage") != "dorado_demux"
        or not isinstance(identities, dict)
        or not isinstance(runtime_product, dict)
    ):
        raise OntSignalError("producer runtime provenance anchor is incomplete")
    expected_receipt_sha256 = runtime_product.get("sha256")
    if not isinstance(expected_receipt_sha256, str) or not HEX64.fullmatch(expected_receipt_sha256):
        raise OntSignalError("producer runtime provenance digest authority is invalid")
    descriptor = _open_job_owned_relative(source_job, runtime_product.get("path"))
    try:
        raw, observed_receipt_sha256 = _read_bounded_descriptor(
            descriptor, limit=64 * 1024, label="producer runtime provenance"
        )
    finally:
        os.close(descriptor)
    if observed_receipt_sha256 != expected_receipt_sha256:
        raise OntSignalError("producer runtime provenance digest authority diverged")
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OntSignalError("producer runtime provenance is malformed") from exc
    if not isinstance(receipt, dict):
        raise OntSignalError("producer runtime provenance is malformed")
    calls = receipt.get("calls_bam")
    move_tags = calls.get("move_tags") if isinstance(calls, dict) else None
    params = source_job.params if isinstance(source_job.params, dict) else {}
    model_id = identities.get("model_id")
    runtime_sha256 = identities.get("runtime_sha256")
    read_count = calls.get("read_count") if isinstance(calls, dict) else None
    if (
        receipt.get("schema") != "biomodstack.dorado_runtime_provenance.v1"
        or receipt.get("mode") != "simplex"
        or identities.get("mode") != "simplex"
        or receipt.get("emit_moves") is not True
        or params.get("emit_moves") is not True
        or not isinstance(model_id, str)
        or not model_id
        or receipt.get("model_id") != model_id
        or params.get("dorado_resolved_model_id") != model_id
        or not isinstance(runtime_sha256, str)
        or not HEX64.fullmatch(runtime_sha256)
        or receipt.get("runtime_sha256") != runtime_sha256
        or not isinstance(calls, dict)
        or calls.get("sha256") != artifact_sha256
        or identities.get("calls_bam_sha256") != artifact_sha256
        or isinstance(read_count, bool)
        or not isinstance(read_count, int)
        or read_count <= 0
        or identities.get("read_count") != read_count
        or not isinstance(move_tags, dict)
        or move_tags != {"mv": read_count, "ts": read_count, "ns": read_count}
        or not isinstance(calls.get("read_inventory_sha256"), str)
        or not HEX64.fullmatch(calls["read_inventory_sha256"])
    ):
        raise OntSignalError("producer runtime/model/emit-moves provenance is inconsistent")
    return {
        "schema": "bms.ont-move-source-producer-runtime.v1",
        "authority_state": "known",
        "source_job_id": source_job.id,
        "source_bam_sha256": artifact_sha256,
        "runtime_provenance_sha256": observed_receipt_sha256,
        "runtime_sha256": runtime_sha256,
        "basecall_model_id": model_id,
        "emit_moves": True,
        "read_count": read_count,
        "read_inventory_sha256": calls["read_inventory_sha256"],
        "move_tag_counts": move_tags,
    }


def _stable_descriptor_identity(descriptor: int) -> tuple[str, int]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise OntSignalError("move-table source must be a retained regular file")
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    with os.fdopen(os.dup(descriptor), "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    after = os.fstat(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or after.st_size <= 0:
        raise OntSignalError("move-table source identity changed during registration")
    return digest.hexdigest(), after.st_size


def _stable_file_identity(path: Path) -> tuple[str, int]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        return _stable_descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


def _open_external_move_bam_root() -> tuple[Path, int, os.stat_result]:
    configured = os.getenv(EXTERNAL_MOVE_BAM_ROOT_ENV, "").strip()
    root = _lexical_absolute_path(configured)
    if root is None:
        raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE)
    try:
        descriptor = _open_absolute_directory_nofollow(root)
    except OSError as exc:
        raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE) from exc
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE)
    return root, descriptor, info


def _read_external_move_bam_candidate_key() -> bytes:
    configured = os.getenv(EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV, "").strip()
    key_path = _lexical_absolute_path(configured)
    if key_path is None or key_path.name in {"", ".", ".."}:
        raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE)
    parent_fd: int | None = None
    key_fd: int | None = None
    try:
        parent_fd = _open_absolute_directory_nofollow(key_path.parent)
        key_fd = os.open(
            key_path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        info = os.fstat(key_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE)
        key = os.read(key_fd, EXTERNAL_MOVE_BAM_CANDIDATE_KEY_BYTES + 1)
        if len(key) != EXTERNAL_MOVE_BAM_CANDIDATE_KEY_BYTES:
            raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE)
        return key
    except OntSignalError:
        raise
    except OSError as exc:
        raise OntSignalError(EXTERNAL_MOVE_BAM_SOURCE_UNAVAILABLE) from exc
    finally:
        if key_fd is not None:
            os.close(key_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _descriptor_move_bam_candidates(
    directory_fd: int,
    *,
    prefix: Path = Path(),
    depth: int = 0,
    candidates: list[tuple[str, os.stat_result]] | None = None,
    visited_entries: list[int] | None = None,
) -> list[tuple[str, os.stat_result]]:
    if depth > MAX_EXTERNAL_MOVE_BAM_DEPTH:
        raise OntSignalError("external move-BAM candidate traversal exceeds bounded depth")
    found = [] if candidates is None else candidates
    visited = [0] if visited_entries is None else visited_entries
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                visited[0] += 1
                if visited[0] > MAX_EXTERNAL_MOVE_BAM_VISITED_ENTRIES:
                    raise OntSignalError(
                        "external move-BAM traversal exceeds bounded visited-entry policy"
                    )
                relative = prefix / entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=directory_fd,
                    )
                    try:
                        _descriptor_move_bam_candidates(
                            child_fd,
                            prefix=relative,
                            depth=depth + 1,
                            candidates=found,
                            visited_entries=visited,
                        )
                    finally:
                        os.close(child_fd)
                elif (
                    stat.S_ISREG(info.st_mode)
                    and info.st_size > 0
                    and entry.name.lower().endswith(".bam")
                ):
                    found.append((relative.as_posix(), info))
                    if len(found) > MAX_EXTERNAL_MOVE_BAM_CANDIDATES:
                        raise OntSignalError("external move-BAM candidate catalog exceeds bounded policy")
    except OntSignalError:
        raise
    except OSError as exc:
        raise OntSignalError("external move-BAM candidate catalog could not be read safely") from exc
    return found


def _external_move_bam_candidate_body(
    relative: str, root_info: os.stat_result, file_info: os.stat_result
) -> dict[str, Any]:
    return {
        "schema": "bms.ont-external-move-bam-candidate.v1",
        "server_relative_path": relative,
        "root_device": root_info.st_dev,
        "root_inode": root_info.st_ino,
        "file_device": file_info.st_dev,
        "file_inode": file_info.st_ino,
        "file_size": file_info.st_size,
        "file_mtime_ns": file_info.st_mtime_ns,
        "file_ctime_ns": file_info.st_ctime_ns,
    }


def _external_move_bam_candidate_id(
    relative: str,
    root_info: os.stat_result,
    file_info: os.stat_result,
    candidate_key: bytes,
) -> str:
    return hmac.new(
        candidate_key,
        _canonical(_external_move_bam_candidate_body(relative, root_info, file_info)),
        hashlib.sha256,
    ).hexdigest()


def list_external_move_bam_candidates() -> list[dict[str, Any]]:
    """List external BAMs without hashing bytes or publishing server paths."""
    _root, root_fd, root_info = _open_external_move_bam_root()
    try:
        candidate_key = _read_external_move_bam_candidate_key()
        return [
            {
                "candidate_id": _external_move_bam_candidate_id(
                    relative, root_info, info, candidate_key
                ),
                "display_name": Path(relative).name,
                "size_bytes": info.st_size,
                "modified_at_ns": info.st_mtime_ns,
            }
            for relative, info in sorted(
                _descriptor_move_bam_candidates(root_fd), key=lambda item: item[0]
            )
        ]
    finally:
        os.close(root_fd)


def _open_external_move_bam_candidate(
    candidate_id: str,
    candidate_key: bytes,
) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    if not HEX64.fullmatch(candidate_id):
        raise KeyError("external move-BAM candidate not found")
    _root, root_fd, root_info = _open_external_move_bam_root()
    file_fd: int | None = None
    try:
        match = next(
            (
                (relative, info)
                for relative, info in _descriptor_move_bam_candidates(root_fd)
                if hmac.compare_digest(
                    _external_move_bam_candidate_id(
                        relative, root_info, info, candidate_key
                    ),
                    candidate_id,
                )
            ),
            None,
        )
        if match is None:
            raise KeyError("external move-BAM candidate not found")
        relative, _listed_info = match
        parts = Path(relative).parts
        parent_fd = os.dup(root_fd)
        try:
            for component in parts[:-1]:
                child_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = child_fd
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise OntSignalError("external move-BAM candidate cannot be reopened without following symbolic links") from exc
        finally:
            os.close(parent_fd)
        observed = os.fstat(file_fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_size <= 0
            or not hmac.compare_digest(
                _external_move_bam_candidate_id(
                    relative, root_info, observed, candidate_key
                ),
                candidate_id,
            )
        ):
            os.close(file_fd)
            file_fd = None
            raise OntSignalError("external move-BAM candidate identity changed before registration")
        assert file_fd is not None
        return file_fd, root_fd, relative, root_info, observed
    except BaseException:
        if file_fd is not None:
            os.close(file_fd)
        os.close(root_fd)
        raise


def _seal_external_move_bam_candidate(
    candidate_id: str,
) -> tuple[dict[str, Any], list[int]]:
    candidate_key = _read_external_move_bam_candidate_key()
    descriptor, root_descriptor, relative, root_info, before = _open_external_move_bam_candidate(
        candidate_id, candidate_key
    )
    try:
        artifact_sha256, size_bytes = _stable_descriptor_identity(descriptor)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        )
        if (
            before_identity != after_identity
            or size_bytes != after.st_size
            or not hmac.compare_digest(
                _external_move_bam_candidate_id(
                    relative, root_info, after, candidate_key
                ),
                candidate_id,
            )
        ):
            raise OntSignalError("external move-BAM candidate identity changed during registration")
        return {
            "candidate_id": candidate_id,
            "server_relative_path": relative,
            "root_device": root_info.st_dev,
            "root_inode": root_info.st_ino,
            "file_device": after.st_dev,
            "file_inode": after.st_ino,
            "file_mtime_ns": after.st_mtime_ns,
            "file_ctime_ns": after.st_ctime_ns,
            "artifact_sha256": artifact_sha256,
            "artifact_size_bytes": size_bytes,
        }, [root_descriptor, descriptor]
    except BaseException:
        os.close(descriptor)
        os.close(root_descriptor)
        raise


def _hold_external_move_bam_descriptors_through_transaction(
    session: AsyncSession,
    descriptors: list[int],
) -> None:
    """Close selected external source descriptors after the outer transaction ends."""
    state = {"descriptors": list(descriptors)}

    def close_descriptors(_session: Any, transaction: Any) -> None:
        if transaction.parent is not None:
            return
        held = state.pop("descriptors", [])
        for descriptor in held:
            os.close(descriptor)

    event.listen(session.sync_session, "after_transaction_end", close_descriptors)


async def _seal_external_move_bam_candidate_async(
    candidate_id: str,
) -> tuple[dict[str, Any], list[int]]:
    """Transfer descriptor ownership from a thread without a cancellation gap."""
    worker = asyncio.create_task(
        asyncio.to_thread(_seal_external_move_bam_candidate, candidate_id)
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            _sealed, descriptors = await worker
        except BaseException:
            pass
        else:
            for descriptor in descriptors:
                os.close(descriptor)
        raise


def _hash_job_owned_input(item: InputFile, source_job: Job) -> tuple[str, int]:
    descriptor, _admitted_path = _open_job_owned_input(item, source_job)
    try:
        return _stable_descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


async def _hash_job_owned_input_async(item: InputFile, source_job: Job) -> tuple[str, int]:
    return await asyncio.to_thread(_hash_job_owned_input, item, source_job)


async def resolve_managed_bed_authority(
    session: AsyncSession,
    artifact_id: str,
) -> tuple[Path, dict[str, Any]]:
    """Resolve one uniquely completed-job-owned BED through pinned root descriptors."""
    tracked = await session.get(InputFile, artifact_id)
    if (
        tracked is None
        or str(tracked.file_type or "").lower() != "bed"
        or not str(tracked.filename or "").lower().endswith(".bed")
    ):
        raise OntSignalError("managed BED authority is unavailable or has the wrong media type")
    completed_jobs = list(
        (await session.execute(select(Job).where(Job.status == "completed"))).scalars()
    )
    admitted: list[tuple[Job, int, Path]] = []
    for job in completed_jobs:
        try:
            descriptor, path = _open_job_owned_input(tracked, job)
        except OntSignalError:
            continue
        admitted.append((job, descriptor, path))
    if len(admitted) != 1:
        for _job, descriptor, _path in admitted:
            os.close(descriptor)
        raise OntSignalError(
            "managed BED must have one completed job owner under approved roots"
        )
    source_job, descriptor, path = admitted[0]
    try:
        sha256, size_bytes = await asyncio.to_thread(
            _stable_descriptor_identity, descriptor
        )
    finally:
        os.close(descriptor)
    if tracked.size_bytes is not None and tracked.size_bytes != size_bytes:
        raise OntSignalError("managed BED tracked size authority diverged")
    return path, {
        "artifact_id": tracked.id,
        "source_job_id": source_job.id,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _source_public(row: OntMoveTableSource) -> dict[str, Any]:
    return {
        "move_source_id": row.id,
        "run_id": row.run_id,
        "observed_generation": row.observed_generation,
        "raw_representation_id": row.raw_representation_id,
        "artifact_id": row.input_file_id,
        "artifact_sha256": row.artifact_sha256,
        "artifact_size_bytes": row.artifact_size_bytes,
        "bam_header_sha256": row.bam_header_sha256,
        "record_count": row.record_count,
        "unique_read_count": row.unique_read_count,
        "tag_counts": {"mv": row.mv_tag_count, "ts": row.ts_tag_count, "ns": row.ns_tag_count},
        "basecall_model_id": row.basecall_model_id,
        "molecule_type": row.molecule_type,
        "source_job_id": row.source_job_id,
        "external_registration_receipt_id": row.external_registration_receipt_id,
        "attempt_number": row.attempt_number,
        "predecessor_move_source_id": row.predecessor_move_source_id,
        "source_runtime_identity": _public_json(row.source_runtime_identity),
        "read_inventory_sha256": row.read_inventory_sha256,
        "state": row.validation_state,
        "reason_code": row.reason_code,
        "validation_receipt": _public_json(row.validation_receipt),
        "created_at": _public_time(row.created_at),
        "validated_at": _public_time(row.validated_at),
    }


def _mapping_profile_base_shift_authority(
    row: OntSignalMappingProfile,
) -> dict[str, Any]:
    receipt = row.approval_receipt if isinstance(row.approval_receipt, dict) else {}
    value = receipt.get("base_shift_value")
    if isinstance(value, bool) or not isinstance(value, int) or not -64 <= value <= 64:
        raise OntSignalError("mapping profile lacks approved base-shift authority")
    identity = {
        "schema": "bms.ont-signal-profile-base-shift.v1",
        "mapping_profile_id": row.id,
        "calibration_artifact_id": row.calibration_artifact_id,
        "molecule_type": row.molecule_type,
        "basecall_model_id": row.basecall_model_id,
        "kmer_length": row.kmer_length,
        "signal_move_offset": row.signal_move_offset,
        "effective_value": value,
        "approval_receipt": receipt,
    }
    return {**identity, "profile_sha256": _digest(identity)}


def _profile_public(row: OntSignalMappingProfile) -> dict[str, Any]:
    base_shift = _mapping_profile_base_shift_authority(row)
    return {
        "mapping_profile_id": row.id,
        "name": row.name,
        "molecule_type": row.molecule_type,
        "basecall_model_id": row.basecall_model_id,
        "kmer_length": row.kmer_length,
        "signal_move_offset": row.signal_move_offset,
        "base_shift_value": base_shift["effective_value"],
        "parameter_source": row.parameter_source,
        "calibration_artifact_id": row.calibration_artifact_id,
        "primary_alignment_policy": row.primary_alignment_policy,
        "minimum_mapq": row.minimum_mapq,
        "include_supplementary": False,
        "read_set_selection": row.read_set_selection,
        "approval_receipt": _public_json(row.approval_receipt),
        "approved_at": _public_time(row.approved_at),
        "approved_by": row.approved_by,
    }


def _artifact_public(row: OntSignalMappingArtifact) -> dict[str, Any]:
    return {
        "mapping_artifact_id": row.id,
        "mapping_job_id": row.mapping_job_id,
        "kind": row.kind,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "media_type": row.media_type,
        "parent_identities": _public_json(row.parent_identities),
        "runtime_identity": _public_json(row.runtime_identity),
        "validation_receipt": _public_json(row.validation_receipt),
        "created_at": _public_time(row.created_at),
    }


def _external_move_bam_receipt_body(
    receipt: OntExternalMoveBamRegistrationReceipt,
) -> dict[str, Any]:
    return {
        "candidate_id": receipt.candidate_id,
        "server_relative_path": receipt.server_relative_path,
        "root_device": receipt.root_device,
        "root_inode": receipt.root_inode,
        "file_device": receipt.file_device,
        "file_inode": receipt.file_inode,
        "file_mtime_ns": receipt.file_mtime_ns,
        "file_ctime_ns": receipt.file_ctime_ns,
        "artifact_sha256": receipt.artifact_sha256,
        "artifact_size_bytes": receipt.artifact_size_bytes,
        "run_id": receipt.run_id,
        "observed_generation": receipt.observed_generation,
        "raw_representation_id": receipt.raw_representation_id,
        "molecule_type": receipt.molecule_type,
    }


def _external_move_bam_runtime_identity(artifact_sha256: str) -> dict[str, Any]:
    return {
        "schema": "bms.ont-move-source-producer-runtime.v1",
        "authority_state": "legacy_unknown",
        "source_job_id": None,
        "source_bam_sha256": artifact_sha256,
        "reason_code": "producer_runtime_provenance_unavailable",
        "requires_independent_move_validation": True,
    }


async def _replay_external_move_bam_registration(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    candidate_id: str,
    molecule_type: str,
) -> dict[str, Any] | None:
    receipt = (
        await session.execute(
            select(OntExternalMoveBamRegistrationReceipt).where(
                OntExternalMoveBamRegistrationReceipt.run_id == run_id,
                OntExternalMoveBamRegistrationReceipt.observed_generation == observed_generation,
                OntExternalMoveBamRegistrationReceipt.raw_representation_id == raw_representation_id,
                OntExternalMoveBamRegistrationReceipt.candidate_id == candidate_id,
                OntExternalMoveBamRegistrationReceipt.molecule_type == molecule_type,
            )
        )
    ).scalar_one_or_none()
    if receipt is None:
        return None
    receipt_body = _external_move_bam_receipt_body(receipt)
    if receipt.id != f"ont-external-move-{_digest(receipt_body)}":
        raise OntSignalError("external move-BAM registration receipt replay authority diverged")
    source = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.external_registration_receipt_id == receipt.id,
                OntMoveTableSource.attempt_number == 1,
                OntMoveTableSource.predecessor_move_source_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    artifact_sha256 = receipt.artifact_sha256
    expected_input_file_id = f"ont-ext-bam-{artifact_sha256[:24]}"
    if (
        source is None
        or source.run_id != run_id
        or source.observed_generation != observed_generation
        or source.raw_representation_id != raw_representation_id
        or source.input_file_id != expected_input_file_id
        or source.source_job_id is not None
        or source.artifact_sha256 != artifact_sha256
        or source.artifact_size_bytes != receipt.artifact_size_bytes
        or source.molecule_type != molecule_type
        or source.source_runtime_identity != _external_move_bam_runtime_identity(artifact_sha256)
    ):
        raise OntSignalError("move-source replay authority diverged from retained evidence")
    return _source_public(source)


async def register_external_move_bam_candidate(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    candidate_id: str,
    molecule_type: str,
) -> dict[str, Any]:
    if molecule_type not in {"dna", "rna"}:
        raise OntSignalError("molecule_type must be dna or rna")
    replay = await _replay_external_move_bam_registration(
        session,
        run_id=run_id,
        observed_generation=observed_generation,
        raw_representation_id=raw_representation_id,
        candidate_id=candidate_id,
        molecule_type=molecule_type,
    )
    if replay is not None:
        return replay
    representation = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.id == raw_representation_id,
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.observed_generation == observed_generation,
            )
        )
    ).scalar_one_or_none()
    if representation is None:
        raise KeyError("raw representation not found")
    receipts = representation.validation_receipts if isinstance(representation.validation_receipts, dict) else {}
    if representation.format != "blow5" or representation.state != "ready" or receipts.get("adjacent_index") is not True:
        raise OntSignalError("ready indexed BLOW5 authority is required")

    sealed, retained_descriptors = await _seal_external_move_bam_candidate_async(candidate_id)
    transaction = session.sync_session.get_transaction()
    owns_autobegin = (
        transaction is not None and transaction.origin.name == "AUTOBEGIN"
    )
    if owns_autobegin:
        await session.rollback()
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
        except BaseException:
            for descriptor in retained_descriptors:
                os.close(descriptor)
            raise
    try:
        _hold_external_move_bam_descriptors_through_transaction(
            session, retained_descriptors
        )
    except BaseException:
        for descriptor in retained_descriptors:
            os.close(descriptor)
        raise
    if owns_autobegin:
        replay = await _replay_external_move_bam_registration(
            session,
            run_id=run_id,
            observed_generation=observed_generation,
            raw_representation_id=raw_representation_id,
            candidate_id=candidate_id,
            molecule_type=molecule_type,
        )
        if replay is not None:
            return replay
        representation = (
            await session.execute(
                select(OntRawSignalRepresentation).where(
                    OntRawSignalRepresentation.id == raw_representation_id,
                    OntRawSignalRepresentation.run_id == run_id,
                    OntRawSignalRepresentation.observed_generation == observed_generation,
                )
            )
        ).scalar_one_or_none()
        if representation is None:
            raise KeyError("raw representation not found")
        receipts = (
            representation.validation_receipts
            if isinstance(representation.validation_receipts, dict)
            else {}
        )
        if (
            representation.format != "blow5"
            or representation.state != "ready"
            or receipts.get("adjacent_index") is not True
        ):
            raise OntSignalError("ready indexed BLOW5 authority is required")
    receipt_body = {
        **sealed,
        "run_id": run_id,
        "observed_generation": observed_generation,
        "raw_representation_id": raw_representation_id,
        "molecule_type": molecule_type,
    }
    receipt_id = f"ont-external-move-{_digest(receipt_body)}"
    artifact_sha256 = str(sealed["artifact_sha256"])
    artifact_size_bytes = int(sealed["artifact_size_bytes"])
    input_file_id = f"ont-ext-bam-{artifact_sha256[:24]}"
    runtime_identity = _external_move_bam_runtime_identity(artifact_sha256)
    now = _now()
    await session.execute(
        sqlite_insert(InputFile)
        .values(
            id=input_file_id,
            filename=f"external-{artifact_sha256}.bam",
            file_type="bam",
            directory="",
            size_bytes=artifact_size_bytes,
            uploaded_at=now,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    tracked = await session.get(InputFile, input_file_id)
    if (
        tracked is None
        or tracked.directory != ""
        or tracked.filename != f"external-{artifact_sha256}.bam"
        or tracked.file_type != "bam"
        or tracked.size_bytes != artifact_size_bytes
    ):
        raise OntSignalError("external move-BAM opaque artifact replay authority diverged")
    await session.execute(
        sqlite_insert(OntExternalMoveBamRegistrationReceipt)
        .values(id=receipt_id, **receipt_body, created_at=now)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    receipt = await session.get(OntExternalMoveBamRegistrationReceipt, receipt_id)
    if receipt is None or any(
        getattr(receipt, key) != value for key, value in receipt_body.items()
    ):
        raise OntSignalError("external move-BAM registration receipt replay authority diverged")

    existing = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.run_id == run_id,
                OntMoveTableSource.observed_generation == observed_generation,
                OntMoveTableSource.artifact_sha256 == artifact_sha256,
                OntMoveTableSource.attempt_number == 1,
                OntMoveTableSource.predecessor_move_source_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.raw_representation_id != raw_representation_id
            or existing.input_file_id != input_file_id
            or existing.source_job_id is not None
            or existing.external_registration_receipt_id != receipt_id
            or existing.artifact_size_bytes != artifact_size_bytes
            or existing.molecule_type != molecule_type
            or existing.source_runtime_identity != runtime_identity
        ):
            raise OntSignalError("move-source replay authority diverged from retained evidence")
        return _source_public(existing)

    await session.execute(
        sqlite_insert(OntMoveTableSource)
        .values(
            id=_id("ont-moves"),
            run_id=run_id,
            observed_generation=observed_generation,
            raw_representation_id=raw_representation_id,
            input_file_id=input_file_id,
            source_job_id=None,
            external_registration_receipt_id=receipt_id,
            artifact_sha256=artifact_sha256,
            artifact_size_bytes=artifact_size_bytes,
            molecule_type=molecule_type,
            source_runtime_identity=runtime_identity,
            attempt_number=1,
            predecessor_move_source_id=None,
            validation_state="requested",
            reason_code="move_source_validation_requested",
            validation_receipt={
                "raw_manifest_sha256": representation.manifest_sha256,
                "external_registration_receipt_id": receipt_id,
            },
            created_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "run_id",
                "observed_generation",
                "artifact_sha256",
                "attempt_number",
            ]
        )
    )
    winner = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.run_id == run_id,
                OntMoveTableSource.observed_generation == observed_generation,
                OntMoveTableSource.artifact_sha256 == artifact_sha256,
                OntMoveTableSource.attempt_number == 1,
                OntMoveTableSource.predecessor_move_source_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if winner is None:
        raise OntSignalError("external move-source durable registration winner is unavailable")
    if (
        winner.raw_representation_id != raw_representation_id
        or winner.input_file_id != input_file_id
        or winner.source_job_id is not None
        or winner.external_registration_receipt_id != receipt_id
        or winner.artifact_size_bytes != artifact_size_bytes
        or winner.molecule_type != molecule_type
        or winner.source_runtime_identity != runtime_identity
    ):
        raise OntSignalError("move-source replay authority diverged from retained evidence")
    return _source_public(winner)


async def register_move_source(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    input_file_id: str,
    molecule_type: str,
    source_job_id: str | None,
    external_registration_receipt_id: str | None,
    source_runtime_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if molecule_type not in {"dna", "rna"}:
        raise OntSignalError("molecule_type must be dna or rna")
    if source_runtime_identity is not None:
        raise OntSignalError("caller-supplied runtime identity cannot attest producer authority")
    if external_registration_receipt_id is not None:
        raise OntSignalError("external registration receipts are not supported without durable authority")
    if source_job_id is None:
        raise OntSignalError("a completed source job authority is required")
    representation = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.id == raw_representation_id,
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.observed_generation == observed_generation,
            )
        )
    ).scalar_one_or_none()
    if representation is None:
        raise KeyError("raw representation not found")
    receipts = representation.validation_receipts if isinstance(representation.validation_receipts, dict) else {}
    if representation.format != "blow5" or representation.state != "ready" or receipts.get("adjacent_index") is not True:
        raise OntSignalError("ready indexed BLOW5 authority is required")
    tracked = await session.get(InputFile, input_file_id)
    if tracked is None:
        raise KeyError("tracked move-table input not found")
    if not tracked.filename.lower().endswith((".bam", ".ubam")):
        raise OntSignalError("tracked move-table source must be BAM")
    source_job = await session.get(Job, source_job_id)
    if source_job is None or source_job.status != "completed":
        raise OntSignalError("completed source job authority is required")
    source_params = source_job.params if isinstance(source_job.params, dict) else {}
    has_bound_run = "source_instrument_run_id" in source_params
    has_bound_generation = "source_instrument_observed_generation" in source_params
    source_generation = source_params.get("source_instrument_observed_generation")
    if has_bound_run != has_bound_generation or (
        has_bound_run
        and (
            source_params.get("source_instrument_run_id") != run_id
            or isinstance(source_generation, bool)
            or source_generation != observed_generation
        )
    ):
        raise OntSignalError("completed source job run generation does not match requested authority")
    artifact_sha256, size_bytes = await _hash_job_owned_input_async(tracked, source_job)
    if tracked.size_bytes is not None and tracked.size_bytes != size_bytes:
        raise OntSignalError("tracked move-table input size authority diverged")
    derived_runtime_identity = await asyncio.to_thread(
        _derive_source_runtime_identity, source_job, artifact_sha256
    )
    existing = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.run_id == run_id,
                OntMoveTableSource.observed_generation == observed_generation,
                OntMoveTableSource.artifact_sha256 == artifact_sha256,
                OntMoveTableSource.attempt_number == 1,
                OntMoveTableSource.predecessor_move_source_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.raw_representation_id != raw_representation_id
            or existing.input_file_id != input_file_id
            or existing.source_job_id != source_job_id
            or existing.molecule_type != molecule_type
            or existing.source_runtime_identity != derived_runtime_identity
        ):
            raise OntSignalError("move-source replay authority diverged from retained evidence")
        return _source_public(existing)
    candidate_id = _id("ont-moves")
    await session.execute(
        sqlite_insert(OntMoveTableSource)
        .values(
            id=candidate_id,
            run_id=run_id,
            observed_generation=observed_generation,
            raw_representation_id=raw_representation_id,
            input_file_id=input_file_id,
            source_job_id=source_job_id,
            external_registration_receipt_id=external_registration_receipt_id,
            artifact_sha256=artifact_sha256,
            artifact_size_bytes=size_bytes,
            molecule_type=molecule_type,
            source_runtime_identity=derived_runtime_identity,
            attempt_number=1,
            predecessor_move_source_id=None,
            validation_state="requested",
            reason_code="move_source_validation_requested",
            validation_receipt={"raw_manifest_sha256": representation.manifest_sha256},
            created_at=_now(),
        )
        .on_conflict_do_nothing(
            index_elements=[
                "run_id",
                "observed_generation",
                "artifact_sha256",
                "attempt_number",
            ]
        )
    )
    winner = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.run_id == run_id,
                OntMoveTableSource.observed_generation == observed_generation,
                OntMoveTableSource.artifact_sha256 == artifact_sha256,
                OntMoveTableSource.attempt_number == 1,
                OntMoveTableSource.predecessor_move_source_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if winner is None:
        raise OntSignalError("move-source durable registration winner is unavailable")
    if (
        winner.raw_representation_id != raw_representation_id
        or winner.input_file_id != input_file_id
        or winner.source_job_id != source_job_id
        or winner.external_registration_receipt_id != external_registration_receipt_id
        or winner.artifact_size_bytes != size_bytes
        or winner.molecule_type != molecule_type
        or winner.source_runtime_identity != derived_runtime_identity
    ):
        raise OntSignalError("move-source replay authority diverged from retained evidence")
    return _source_public(winner)


def _is_exact_preserved_retry_exhaustion(
    predecessor: OntMoveTableSource,
) -> bool:
    receipt = (
        predecessor.validation_receipt
        if isinstance(predecessor.validation_receipt, dict)
        else {}
    )
    retry = receipt.get("retry")
    if (
        predecessor.reason_code != "runtime_validation_failed_retry_exhausted"
        or not isinstance(retry, dict)
        or set(retry) != {"max_attempts", "failures"}
        or retry.get("max_attempts") != 3
    ):
        return False
    failures = retry.get("failures")
    if not isinstance(failures, list) or len(failures) != 3:
        return False
    for expected_attempt, failure in enumerate(failures, start=1):
        if (
            not isinstance(failure, dict)
            or set(failure)
            != {"attempt", "failed_at", "failure_code", "message_sha256"}
            or failure.get("attempt") != expected_attempt
            or not isinstance(failure.get("failed_at"), str)
            or not failure["failed_at"]
            or not isinstance(failure.get("failure_code"), str)
            or not failure["failure_code"]
            or not isinstance(failure.get("message_sha256"), str)
            or HEX64.fullmatch(failure["message_sha256"]) is None
        ):
            return False
    return True


async def _validated_failed_external_move_source(
    session: AsyncSession,
    predecessor_move_source_id: str,
) -> tuple[
    OntMoveTableSource,
    OntExternalMoveBamRegistrationReceipt,
    OntRawSignalRepresentation,
]:
    predecessor = await session.get(OntMoveTableSource, predecessor_move_source_id)
    if predecessor is None:
        raise KeyError("move source not found")
    if predecessor.attempt_number >= MAX_EXTERNAL_MOVE_SOURCE_ATTEMPTS:
        raise OntSignalError("fresh move-source attempt limit reached")
    if (
        predecessor.validation_state != "failed"
        or (
            predecessor.validated_at is None
            and not _is_exact_preserved_retry_exhaustion(predecessor)
        )
        or predecessor.source_job_id is not None
        or predecessor.external_registration_receipt_id is None
        or predecessor.claim_token is not None
        or predecessor.lease_expires_at is not None
        or predecessor.attempt_number < 1
    ):
        raise OntSignalError(
            "fresh attempts require an unclaimed terminal failed external move source"
        )
    receipt = await session.get(
        OntExternalMoveBamRegistrationReceipt,
        predecessor.external_registration_receipt_id,
    )
    representation = await session.get(
        OntRawSignalRepresentation,
        predecessor.raw_representation_id,
    )
    tracked = await session.get(InputFile, predecessor.input_file_id)
    if receipt is None or representation is None or tracked is None:
        raise OntSignalError("retained external move-source authority is unavailable")
    receipt_body = _external_move_bam_receipt_body(receipt)
    expected_receipt_id = f"ont-external-move-{_digest(receipt_body)}"
    expected_input_file_id = f"ont-ext-bam-{receipt.artifact_sha256[:24]}"
    source_receipt = (
        predecessor.validation_receipt
        if isinstance(predecessor.validation_receipt, dict)
        else {}
    )
    if (
        receipt.id != expected_receipt_id
        or receipt.run_id != predecessor.run_id
        or receipt.observed_generation != predecessor.observed_generation
        or receipt.raw_representation_id != predecessor.raw_representation_id
        or receipt.artifact_sha256 != predecessor.artifact_sha256
        or receipt.artifact_size_bytes != predecessor.artifact_size_bytes
        or receipt.molecule_type != predecessor.molecule_type
        or predecessor.input_file_id != expected_input_file_id
        or tracked.id != expected_input_file_id
        or tracked.directory != ""
        or tracked.filename != f"external-{receipt.artifact_sha256}.bam"
        or tracked.file_type != "bam"
        or tracked.size_bytes != receipt.artifact_size_bytes
        or representation.run_id != predecessor.run_id
        or representation.observed_generation != predecessor.observed_generation
        or source_receipt.get("raw_manifest_sha256") != representation.manifest_sha256
        or source_receipt.get("external_registration_receipt_id") != receipt.id
        or predecessor.source_runtime_identity
        != _external_move_bam_runtime_identity(receipt.artifact_sha256)
    ):
        raise OntSignalError("retained external move-source authority diverged")
    return predecessor, receipt, representation


def _fresh_move_source_attempt_receipt(
    predecessor: OntMoveTableSource,
    representation: OntRawSignalRepresentation,
) -> dict[str, Any]:
    return {
        "schema": "bms.ont-move-source-fresh-attempt.v1",
        "predecessor_move_source_id": predecessor.id,
        "raw_manifest_sha256": representation.manifest_sha256,
        "external_registration_receipt_id": (
            predecessor.external_registration_receipt_id
        ),
    }


def _validate_fresh_move_source_attempt_winner(
    winner: OntMoveTableSource,
    predecessor: OntMoveTableSource,
    expected_receipt: Mapping[str, Any],
) -> None:
    if (
        winner.predecessor_move_source_id != predecessor.id
        or winner.attempt_number != predecessor.attempt_number + 1
        or winner.run_id != predecessor.run_id
        or winner.observed_generation != predecessor.observed_generation
        or winner.raw_representation_id != predecessor.raw_representation_id
        or winner.input_file_id != predecessor.input_file_id
        or winner.source_job_id is not None
        or winner.external_registration_receipt_id
        != predecessor.external_registration_receipt_id
        or winner.artifact_sha256 != predecessor.artifact_sha256
        or winner.artifact_size_bytes != predecessor.artifact_size_bytes
        or winner.molecule_type != predecessor.molecule_type
        or winner.source_runtime_identity != predecessor.source_runtime_identity
        or winner.validation_state != "requested"
        or winner.reason_code != "fresh_move_source_attempt_requested"
        or winner.validation_receipt != dict(expected_receipt)
        or winner.claim_token is not None
        or winner.lease_expires_at is not None
        or winner.validated_at is not None
        or any(
            value is not None
            for value in (
                winner.bam_header_sha256,
                winner.record_count,
                winner.unique_read_count,
                winner.mv_tag_count,
                winner.ts_tag_count,
                winner.ns_tag_count,
                winner.basecall_model_id,
                winner.read_inventory_sha256,
            )
        )
    ):
        raise OntSignalError("fresh move-source attempt replay authority diverged")


async def request_fresh_external_move_source_attempt(
    session: AsyncSession,
    *,
    predecessor_move_source_id: str,
) -> dict[str, Any]:
    if not OPAQUE_ID.fullmatch(predecessor_move_source_id):
        raise OntSignalError("move source must be an opaque governed ID")
    transaction = session.sync_session.get_transaction()
    if transaction is None:
        await session.execute(text("BEGIN IMMEDIATE"))
    elif transaction.origin.name == "AUTOBEGIN":
        await session.rollback()
        await session.execute(text("BEGIN IMMEDIATE"))

    predecessor, _external_receipt, representation = (
        await _validated_failed_external_move_source(
            session,
            predecessor_move_source_id,
        )
    )
    expected_receipt = _fresh_move_source_attempt_receipt(
        predecessor,
        representation,
    )
    existing = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.predecessor_move_source_id == predecessor.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        _validate_fresh_move_source_attempt_winner(
            existing,
            predecessor,
            expected_receipt,
        )
        return _source_public(existing)

    await session.execute(
        sqlite_insert(OntMoveTableSource)
        .values(
            id=_id("ont-moves"),
            run_id=predecessor.run_id,
            observed_generation=predecessor.observed_generation,
            raw_representation_id=predecessor.raw_representation_id,
            input_file_id=predecessor.input_file_id,
            source_job_id=None,
            external_registration_receipt_id=(
                predecessor.external_registration_receipt_id
            ),
            artifact_sha256=predecessor.artifact_sha256,
            artifact_size_bytes=predecessor.artifact_size_bytes,
            bam_header_sha256=None,
            record_count=None,
            unique_read_count=None,
            mv_tag_count=None,
            ts_tag_count=None,
            ns_tag_count=None,
            basecall_model_id=None,
            molecule_type=predecessor.molecule_type,
            source_runtime_identity=predecessor.source_runtime_identity,
            read_inventory_sha256=None,
            validation_state="requested",
            reason_code="fresh_move_source_attempt_requested",
            validation_receipt=expected_receipt,
            claim_token=None,
            lease_expires_at=None,
            created_at=_now(),
            validated_at=None,
            attempt_number=predecessor.attempt_number + 1,
            predecessor_move_source_id=predecessor.id,
        )
        .on_conflict_do_nothing(index_elements=["predecessor_move_source_id"])
    )
    winner = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.predecessor_move_source_id == predecessor.id
            )
        )
    ).scalar_one_or_none()
    if winner is None:
        raise OntSignalError("fresh move-source attempt winner is unavailable")
    _validate_fresh_move_source_attempt_winner(
        winner,
        predecessor,
        expected_receipt,
    )
    return _source_public(winner)


async def list_move_sources(session: AsyncSession, *, run_id: str, observed_generation: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(OntMoveTableSource)
            .where(OntMoveTableSource.run_id == run_id, OntMoveTableSource.observed_generation == observed_generation)
            .order_by(OntMoveTableSource.created_at, OntMoveTableSource.id)
        )
    ).scalars()
    return [_source_public(row) for row in rows]


async def list_mapping_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(OntSignalMappingProfile).order_by(OntSignalMappingProfile.approved_at))).scalars()
    return [_profile_public(row) for row in rows]


def _calibration_artifact_public(row: OntSignalCalibrationArtifact) -> dict[str, Any]:
    return {
        "calibration_artifact_id": row.id,
        "raw_representation_id": row.raw_representation_id,
        "move_source_id": row.move_source_id,
        "basecall_model_id": row.basecall_model_id,
        "sample_selection": _public_json(row.sample_selection),
        "recommended_kmer_length": row.recommended_kmer_length,
        "recommended_signal_move_offset": row.recommended_signal_move_offset,
        "score_evidence": _public_json(row.score_evidence),
        "runtime_identity": _public_json(row.runtime_identity),
        "parent_sha256s": _public_json(row.parent_sha256s),
        "artifact_sha256": row.artifact_sha256,
        "created_at": _public_time(row.created_at),
    }


async def list_calibration_artifacts(session: AsyncSession, *, move_source_id: str | None = None) -> list[dict[str, Any]]:
    statement = select(OntSignalCalibrationArtifact)
    if move_source_id:
        statement = statement.where(OntSignalCalibrationArtifact.move_source_id == move_source_id)
    rows = (await session.execute(statement.order_by(OntSignalCalibrationArtifact.created_at))).scalars()
    return [_calibration_artifact_public(row) for row in rows]


async def _calibration_public(session: AsyncSession, row: OntSignalCalibrationJob) -> dict[str, Any]:
    artifact = None if row.calibration_artifact_id is None else await session.get(OntSignalCalibrationArtifact, row.calibration_artifact_id)
    if (row.state == "ready") != (artifact is not None):
        raise OntSignalError("calibration state and immutable artifact publication diverged")
    return {
        "calibration_job_id": row.id,
        "run_id": row.run_id,
        "observed_generation": row.observed_generation,
        "raw_representation_id": row.raw_representation_id,
        "move_source_id": row.move_source_id,
        "sample_count": row.sample_count,
        "request_fingerprint": row.request_fingerprint,
        "state": row.state,
        "reason_code": row.reason_code,
        "attempt": row.attempt,
        "resource_snapshot": _public_json(row.resource_snapshot),
        "stage_receipts": _public_json(row.stage_receipts),
        "failure_code": row.failure_code,
        "failure_message": _public_json(row.failure_message),
        "artifact": None if artifact is None else _calibration_artifact_public(artifact),
        "created_at": _public_time(row.created_at),
        "updated_at": _public_time(row.updated_at),
        "completed_at": _public_time(row.completed_at),
    }


async def create_calibration_job(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    move_source_id: str,
    sample_count: int,
) -> dict[str, Any]:
    if not OPAQUE_ID.fullmatch(raw_representation_id) or not OPAQUE_ID.fullmatch(move_source_id):
        raise OntSignalError("calibration parents must be opaque governed IDs")
    if not MIN_CALIBRATION_READS <= sample_count <= MAX_CALIBRATION_READS:
        raise OntSignalError("calibration sample count is outside bounded policy")
    representation = await session.get(OntRawSignalRepresentation, raw_representation_id)
    source = await session.get(OntMoveTableSource, move_source_id)
    receipts = {} if representation is None or not isinstance(representation.validation_receipts, dict) else representation.validation_receipts
    if representation is None or source is None or (
        representation.run_id != run_id or representation.observed_generation != observed_generation
        or representation.format != "blow5" or representation.state != "ready"
        or receipts.get("adjacent_index") is not True
        or source.run_id != run_id or source.observed_generation != observed_generation
        or source.raw_representation_id != representation.id or source.validation_state != "ready"
        or not source.basecall_model_id or not source.read_inventory_sha256
    ):
        raise OntSignalError("calibration parents do not share one exact ready indexed-BLOW5 generation")
    request_identity = {
        "schema": "bms.ont-signal-calibration-request.v1",
        "run_id": run_id,
        "observed_generation": observed_generation,
        "raw_representation_id": raw_representation_id,
        "move_source_id": move_source_id,
        "sample_count": sample_count,
        "candidate_kmer_length": CALIBRATION_CANDIDATE_KMER_LENGTH,
        "squigualiser_version": "0.7.0",
        "squigualiser_commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e",
    }
    fingerprint = _digest(request_identity)
    parents = {
        "raw_manifest_sha256": representation.manifest_sha256,
        "raw_artifacts": representation.artifact_manifest,
        "move_bam_sha256": source.artifact_sha256,
        "move_read_inventory_sha256": source.read_inventory_sha256,
        "basecall_model_id": source.basecall_model_id,
        "molecule_type": source.molecule_type,
    }
    row_id = f"ont-signal-calibration-{fingerprint}"
    now = _now()
    await session.execute(
        sqlite_insert(OntSignalCalibrationJob)
        .values(
            id=row_id,
            run_id=run_id,
            observed_generation=observed_generation,
            raw_representation_id=raw_representation_id,
            move_source_id=move_source_id,
            sample_count=sample_count,
            request_fingerprint=fingerprint,
            state="requested",
            reason_code="calibration_requested",
            attempt=0,
            resource_snapshot={"request": request_identity, "parents": parents},
            stage_receipts={"request_identity_sha256": fingerprint},
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["request_fingerprint"])
    )
    row = (
        await session.execute(
            select(OntSignalCalibrationJob).where(
                OntSignalCalibrationJob.request_fingerprint == fingerprint
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise OntSignalError("calibration durable registration winner is unavailable")
    if (
        row.run_id,
        row.observed_generation,
        row.raw_representation_id,
        row.move_source_id,
        row.sample_count,
    ) != (
        run_id,
        observed_generation,
        raw_representation_id,
        move_source_id,
        sample_count,
    ):
        raise OntSignalError("calibration request fingerprint is bound to different authority")
    return await _calibration_public(session, row)


async def get_calibration_job(session: AsyncSession, calibration_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSignalCalibrationJob, calibration_job_id)
    if row is None:
        raise KeyError("calibration job not found")
    return await _calibration_public(session, row)


async def _fresh_row(session: AsyncSession, model: Any, row_id: str) -> Any:
    return (
        await session.execute(
            select(model).where(model.id == row_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def cancel_calibration_job(session: AsyncSession, calibration_job_id: str) -> dict[str, Any]:
    row = await _fresh_row(session, OntSignalCalibrationJob, calibration_job_id)
    if row is None:
        raise KeyError("calibration job not found")
    for _attempt in range(2):
        if row.state in {"ready", "failed", "cancelled"}:
            return await _calibration_public(session, row)
        if row.state == "requested":
            cancelled_at = _now()
            receipts = {
                **(row.stage_receipts or {}),
                "cancellation": {
                    "requested_at": cancelled_at.isoformat(),
                    "disposition": "cancelled_before_claim",
                },
            }
            result = await session.execute(
                update(OntSignalCalibrationJob)
                .where(
                    OntSignalCalibrationJob.id == calibration_job_id,
                    OntSignalCalibrationJob.state == "requested",
                )
                .values(
                    state="cancelled",
                    reason_code="cancelled_before_claim",
                    cancel_requested_at=cancelled_at,
                    completed_at=cancelled_at,
                    updated_at=cancelled_at,
                    stage_receipts=receipts,
                )
                .execution_options(synchronize_session=False)
            )
        elif row.state == "running" and row.cancel_requested_at is None:
            requested_at = _now()
            receipts = {
                **(row.stage_receipts or {}),
                "cancellation": {
                    "requested_at": requested_at.isoformat(),
                    "disposition": "worker_termination_requested",
                },
            }
            result = await session.execute(
                update(OntSignalCalibrationJob)
                .where(
                    OntSignalCalibrationJob.id == calibration_job_id,
                    OntSignalCalibrationJob.state == "running",
                    OntSignalCalibrationJob.cancel_requested_at.is_(None),
                )
                .values(
                    reason_code="cancellation_requested",
                    cancel_requested_at=requested_at,
                    updated_at=requested_at,
                    stage_receipts=receipts,
                )
                .execution_options(synchronize_session=False)
            )
        else:
            return await _calibration_public(session, row)
        row = await _fresh_row(session, OntSignalCalibrationJob, calibration_job_id)
        if result.rowcount == 1 or row is None:
            break
    if row is None:
        raise KeyError("calibration job not found")
    return await _calibration_public(session, row)


async def create_mapping_profile(
    session: AsyncSession,
    *,
    name: str,
    molecule_type: str,
    basecall_model_id: str,
    kmer_length: int,
    signal_move_offset: int,
    parameter_source: str,
    calibration_artifact_id: str | None,
    minimum_mapq: int,
    read_set_selection: str,
    approval_receipt: Mapping[str, Any],
    approved_by: str | None,
    base_shift_value: int = 0,
) -> dict[str, Any]:
    if parameter_source != "approved_calibration":
        raise OntSignalError("v1 mapping profiles require approved calibration authority")
    if molecule_type not in {"dna", "rna"} or not 1 <= kmer_length <= 32 or not -64 <= signal_move_offset <= 64:
        raise OntSignalError("mapping profile parameters are outside bounded policy")
    if minimum_mapq != 0 or read_set_selection != "immutable_full_set":
        raise OntSignalError("mapping profile must use primary-only MAPQ 0 immutable full-set policy")
    if isinstance(base_shift_value, bool) or not -64 <= base_shift_value <= 64:
        raise OntSignalError("mapping profile base shift is outside bounded policy")
    receipt = dict(approval_receipt)
    if receipt.get("approved") is not True:
        raise OntSignalError("literal operator approval receipt is required")
    if calibration_artifact_id is None:
        raise OntSignalError("governed calibration artifact is required")
    calibration = await session.get(OntSignalCalibrationArtifact, calibration_artifact_id)
    if calibration is None:
        raise OntSignalError("governed calibration artifact is required")
    calibration_source = await session.get(OntMoveTableSource, calibration.move_source_id)
    if (
        calibration_source is None
        or calibration_source.validation_state != "ready"
        or calibration_source.molecule_type != molecule_type
        or calibration_source.basecall_model_id != basecall_model_id.strip()
        or calibration.basecall_model_id != basecall_model_id.strip()
        or calibration.recommended_kmer_length != kmer_length
        or calibration.recommended_signal_move_offset != signal_move_offset
    ):
        raise OntSignalError("profile parameters do not equal approved calibration evidence")
    producer = (await session.execute(select(OntSignalCalibrationJob).where(
        OntSignalCalibrationJob.calibration_artifact_id == calibration.id,
        OntSignalCalibrationJob.state == "ready",
        OntSignalCalibrationJob.raw_representation_id == calibration.raw_representation_id,
        OntSignalCalibrationJob.move_source_id == calibration.move_source_id,
    ))).scalar_one_or_none()
    if producer is None:
        raise OntSignalError("calibration artifact has no ready governed producer")
    if receipt.get("calibration_artifact_sha256") != calibration.artifact_sha256:
        raise OntSignalError("approval receipt does not bind the governed calibration artifact")
    receipt_shift = receipt.get("base_shift_value", base_shift_value)
    if receipt_shift != base_shift_value:
        raise OntSignalError("approval receipt base shift diverges from the typed profile value")
    receipt["base_shift_value"] = base_shift_value
    profile_filter = (
        OntSignalMappingProfile.basecall_model_id == basecall_model_id.strip(),
        OntSignalMappingProfile.molecule_type == molecule_type,
        OntSignalMappingProfile.kmer_length == kmer_length,
        OntSignalMappingProfile.signal_move_offset == signal_move_offset,
        OntSignalMappingProfile.parameter_source == parameter_source,
        OntSignalMappingProfile.calibration_artifact_id == calibration_artifact_id,
        OntSignalMappingProfile.minimum_mapq == 0,
        OntSignalMappingProfile.include_supplementary.is_(False),
        OntSignalMappingProfile.read_set_selection == "immutable_full_set",
    )
    identity = {
        "basecall_model_id": basecall_model_id.strip(),
        "molecule_type": molecule_type,
        "kmer_length": kmer_length,
        "signal_move_offset": signal_move_offset,
        "calibration_artifact_id": calibration_artifact_id,
        "minimum_mapq": minimum_mapq,
        "read_set_selection": read_set_selection,
    }
    now = _now()
    await session.execute(
        sqlite_insert(OntSignalMappingProfile)
        .values(
            id=f"ont-signal-profile-{_digest(identity)}",
            name=name.strip(),
            molecule_type=molecule_type,
            basecall_model_id=basecall_model_id.strip(),
            kmer_length=kmer_length,
            signal_move_offset=signal_move_offset,
            parameter_source=parameter_source,
            calibration_artifact_id=calibration_artifact_id,
            primary_alignment_policy="primary_only",
            minimum_mapq=minimum_mapq,
            include_supplementary=False,
            read_set_selection=read_set_selection,
            approval_receipt=receipt,
            approved_at=now,
            approved_by=approved_by,
            created_at=now,
        )
        .on_conflict_do_nothing()
    )
    profile = (
        await session.execute(select(OntSignalMappingProfile).where(*profile_filter))
    ).scalar_one_or_none()
    if profile is None:
        raise OntSignalError("mapping-profile durable registration winner is unavailable")
    if (
        profile.name != name.strip()
        or profile.approval_receipt != receipt
        or profile.approved_by != approved_by
    ):
        raise OntSignalError("mapping-profile replay authority diverged")
    return _profile_public(profile)


async def _resolve_domain_revision_authority(
    domain_session: AsyncSession,
    global_domain_experiment_id: str,
) -> dict[str, Any]:
    state = await domain_session.get(MolBioNGSDomainState, global_domain_experiment_id)
    if state is None or not state.current_state_revision_id or not state.current_binding_revision_id:
        raise OntSignalError("authoritative Domain Experiment state revision is unavailable")
    revision = await domain_session.get(
        MolBioNGSDomainStateRevision, state.current_state_revision_id
    )
    binding = await domain_session.get(
        MolBioNGSGlobalBinding, state.current_binding_revision_id
    )
    if (
        revision is None
        or binding is None
        or revision.global_domain_experiment_id != global_domain_experiment_id
        or binding.global_domain_experiment_id != global_domain_experiment_id
        or revision.binding_revision_id != binding.binding_revision_id
        or revision.global_domain_experiment_revision_id
        != binding.global_domain_experiment_revision_id
    ):
        raise OntSignalError("authoritative Domain Experiment revision binding diverged")
    digest_fields = {
        "state_revision_sha256": revision.payload_sha256,
        "membership_graph_sha256": revision.membership_graph_sha256,
        "binding_revision_digest": binding.global_domain_experiment_revision_digest,
    }
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
        for value in digest_fields.values()
    ):
        raise OntSignalError("authoritative Domain Experiment revision digests are invalid")
    if isinstance(state.head_generation, bool) or not isinstance(state.head_generation, int):
        raise OntSignalError("authoritative Domain Experiment generation is invalid")
    return {
        "schema": "bms.molbio.domain-revision-authority.v1",
        "global_domain_experiment_id": global_domain_experiment_id,
        "state_revision_id": revision.id,
        **digest_fields,
        "binding_revision_id": binding.binding_revision_id,
        "head_generation": int(state.head_generation),
    }
async def _resolve_reference_authority(
    domain_session: AsyncSession,
    reference_revision_id: str,
) -> tuple[MolBioNGSReferenceRevision, MolBioNGSReferenceArtifact]:
    revision = await domain_session.get(MolBioNGSReferenceRevision, reference_revision_id)
    if revision is None:
        raise OntSignalError("managed immutable reference revision is unavailable")
    artifact = await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
    if artifact is None or artifact.reference_id != revision.reference_id:
        raise OntSignalError("managed reference artifact authority is unavailable")
    if revision.canonical_fasta_sha256 != artifact.sha256 or revision.canonical_fasta_size_bytes != artifact.size_bytes:
        raise OntSignalError("managed reference revision digest authority diverged")
    return revision, artifact


def _alignment_authority(job: Job) -> dict[str, str]:
    params = job.params if isinstance(job.params, dict) else {}
    authority = {
        "source_reference_sha256": params.get("reference_sequence_sha256"),
        "workflow_id": params.get("ont_workflow_id") or params.get("workflow_id"),
        "input_mode": params.get("ont_input_mode") or params.get("input_mode"),
    }
    if not all(isinstance(value, str) and value for value in authority.values()):
        raise OntSignalError("alignment job provenance is incomplete")
    return {key: str(value) for key, value in authority.items()}


async def _require_exact_alignment_read_set_binding(
    session: AsyncSession,
    *,
    alignment_job: Job,
    move_source: OntMoveTableSource,
    run_id: str,
    observed_generation: int,
) -> dict[str, Any]:
    alignment_params = alignment_job.params if isinstance(alignment_job.params, dict) else {}
    source_job = (
        await session.get(Job, move_source.source_job_id)
        if move_source.source_job_id is not None
        else None
    )
    source_params = source_job.params if source_job is not None and isinstance(source_job.params, dict) else {}
    dataset_id = source_params.get("dataset_id")
    alignment_generation = alignment_params.get("source_instrument_observed_generation")
    if (
        not isinstance(dataset_id, str)
        or not dataset_id
        or alignment_params.get("dataset_id") != dataset_id
        or alignment_params.get("source_instrument_run_id") != run_id
        or isinstance(alignment_generation, bool)
        or alignment_generation != observed_generation
        or alignment_params.get("source_read_inventory_sha256")
        != move_source.read_inventory_sha256
    ):
        raise OntSignalError(
            "alignment dataset, run generation, and read inventory do not equal the selected signal authority"
        )
    return {
        "dataset_id": dataset_id,
        "run_id": run_id,
        "observed_generation": observed_generation,
        "read_inventory_sha256": move_source.read_inventory_sha256,
    }


def _require_primary_alignment_session(alignment: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(alignment)
    if value.get("ready") is not True or value.get("mode") != "primary":
        raise OntSignalError("signal-to-reference mapping requires a ready primary alignment session")
    return value


async def _resolve_primary_alignment_session_async(
    alignment_job_id: str,
    alignment_session_id: str,
    authority: Mapping[str, str],
    job_output_dir: str | None,
) -> dict[str, Any]:
    alignment = await asyncio.to_thread(
        ngs_alignment_sessions.resolve_alignment_session,
        alignment_job_id,
        alignment_session_id,
        **dict(authority),
        job_output_dir=job_output_dir,
    )
    return _require_primary_alignment_session(alignment)


async def create_mapping_job(
    session: AsyncSession,
    domain_session: AsyncSession,
    *,
    mode: str,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    move_source_id: str,
    mapping_profile_id: str,
    reference_revision_id: str | None,
    alignment_job_id: str | None,
    alignment_session_id: str | None,
) -> dict[str, Any]:
    if mode not in {"signal_to_read", "signal_to_reference"}:
        raise OntSignalError("unsupported mapping mode")
    representation = await session.get(OntRawSignalRepresentation, raw_representation_id)
    source = await session.get(OntMoveTableSource, move_source_id)
    profile = await session.get(OntSignalMappingProfile, mapping_profile_id)
    if representation is None or source is None or profile is None:
        raise OntSignalError("mapping parent authority is unavailable")
    if (
        representation.run_id != run_id or representation.observed_generation != observed_generation
        or representation.format != "blow5" or representation.state != "ready"
        or source.run_id != run_id or source.observed_generation != observed_generation
        or source.raw_representation_id != raw_representation_id or source.validation_state != "ready"
    ):
        raise OntSignalError("mapping parents do not share one ready run-generation authority")
    if source.basecall_model_id != profile.basecall_model_id or source.molecule_type != profile.molecule_type:
        raise OntSignalError("move-table model is incompatible with the approved mapping profile")
    if (
        profile.parameter_source != "approved_calibration"
        or profile.calibration_artifact_id is None
        or profile.primary_alignment_policy != "primary_only"
        or profile.minimum_mapq != 0
        or profile.include_supplementary
        or profile.read_set_selection != "immutable_full_set"
    ):
        raise OntSignalError("mapping profile does not use the calibrated fixed v1 primary full-set policy")
    calibration = await session.get(OntSignalCalibrationArtifact, profile.calibration_artifact_id)
    producer = None if calibration is None else (await session.execute(select(OntSignalCalibrationJob).where(
        OntSignalCalibrationJob.calibration_artifact_id == calibration.id,
        OntSignalCalibrationJob.state == "ready",
        OntSignalCalibrationJob.raw_representation_id == raw_representation_id,
        OntSignalCalibrationJob.move_source_id == move_source_id,
    ))).scalar_one_or_none()
    if (
        calibration is None
        or producer is None
        or calibration.raw_representation_id != raw_representation_id
        or calibration.move_source_id != move_source_id
        or calibration.basecall_model_id != source.basecall_model_id
        or calibration.recommended_kmer_length != profile.kmer_length
        or calibration.recommended_signal_move_offset != profile.signal_move_offset
    ):
        raise OntSignalError("approved calibration profile is not exact for the selected signal and move parents")
    parent_mapping_job_id = None
    reference_identity: dict[str, Any] = {}
    domain_revision: dict[str, Any] | None = None
    if mode == "signal_to_reference":
        if not all((reference_revision_id, alignment_job_id, alignment_session_id)):
            raise OntSignalError("reference revision and alignment-session authority are required")
        revision, reference_artifact = await _resolve_reference_authority(domain_session, str(reference_revision_id))
        alignment_job = await session.get(Job, alignment_job_id)
        if alignment_job is None or alignment_job.status != "completed":
            raise OntSignalError("completed governed alignment job is required")
        alignment_input_binding = await _require_exact_alignment_read_set_binding(
            session,
            alignment_job=alignment_job,
            move_source=source,
            run_id=run_id,
            observed_generation=observed_generation,
        )
        domain_revision = await _resolve_domain_revision_authority(
            domain_session, revision.global_domain_experiment_id
        )
        authority = _alignment_authority(alignment_job)
        if authority["source_reference_sha256"] != revision.normalized_sequence_sha256:
            raise OntSignalError("alignment job reference does not equal the managed reference revision")
        alignment = await _resolve_primary_alignment_session_async(
            alignment_job.id,
            str(alignment_session_id),
            authority,
            getattr(alignment_job, "child_output_dir", None) or alignment_job.output_dir,
        )
        parent = (
            await session.execute(
                select(OntSignalMappingJob).where(
                    OntSignalMappingJob.mode == "signal_to_read",
                    OntSignalMappingJob.raw_representation_id == raw_representation_id,
                    OntSignalMappingJob.move_source_id == move_source_id,
                    OntSignalMappingJob.mapping_profile_id == mapping_profile_id,
                    OntSignalMappingJob.state == "ready",
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise OntSignalError("ready signal-to-read mapping is required")
        parent_mapping_job_id = parent.id
        reference_identity = {
            "reference_revision_id": revision.id,
            "reference_artifact_id": reference_artifact.id,
            "reference_fasta_sha256": reference_artifact.sha256,
            "contig_inventory_sha256": revision.contig_manifest_sha256,
            "domain_revision": domain_revision,
            "alignment_session_id": alignment_session_id,
            "alignment_artifacts": alignment.get("artifacts"),
            "alignment_input_binding": alignment_input_binding,
        }
    elif any((reference_revision_id, alignment_job_id, alignment_session_id)):
        raise OntSignalError("signal-to-read mapping cannot accept reference alignment parents")
    parents = {
        "raw_manifest_sha256": representation.manifest_sha256,
        "raw_artifacts": representation.artifact_manifest,
        "move_bam_sha256": source.artifact_sha256,
        "move_read_inventory_sha256": source.read_inventory_sha256,
        "mapping_profile_id": profile.id,
        "calibration_artifact_id": calibration.id,
        "calibration_artifact_sha256": calibration.artifact_sha256,
        **reference_identity,
    }
    request_identity = {
        "schema": "bms.ont-signal-mapping-request.v1",
        "mode": mode,
        "run_id": run_id,
        "observed_generation": observed_generation,
        "raw_representation_id": raw_representation_id,
        "move_source_id": move_source_id,
        "mapping_profile_id": mapping_profile_id,
        "reference_revision_id": reference_revision_id,
        "alignment_job_id": alignment_job_id,
        "alignment_session_id": alignment_session_id,
        "parent_mapping_job_id": parent_mapping_job_id,
        "domain_revision": domain_revision,
        "parents_sha256": _digest(parents),
    }
    fingerprint = _digest(request_identity)
    job_id = f"ont-signal-map-{fingerprint}"
    now = _now()
    inserted = await session.execute(
        sqlite_insert(OntSignalMappingJob)
        .values(
            id=job_id,
            mode=mode,
            run_id=run_id,
            observed_generation=observed_generation,
            raw_representation_id=raw_representation_id,
            move_source_id=move_source_id,
            mapping_profile_id=mapping_profile_id,
            reference_revision_id=reference_revision_id,
            alignment_job_id=alignment_job_id,
            alignment_session_id=alignment_session_id,
            parent_mapping_job_id=parent_mapping_job_id,
            request_fingerprint=fingerprint,
            state="requested",
            reason_code=f"{mode}_mapping_requested",
            attempt=0,
            resource_snapshot={"request": request_identity, "parents": parents},
            stage_receipts={"request_identity_sha256": fingerprint},
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["request_fingerprint"])
    )
    if inserted.rowcount == 1:
        await session.execute(
            sqlite_insert(OntSignalMappingEvent)
            .values(
                id=f"ont-signal-event-request-{fingerprint}",
                job_id=job_id,
                state="requested",
                reason_code=f"{mode}_mapping_requested",
                receipt={"parents_sha256": _digest(parents)},
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
    job = (
        await session.execute(
            select(OntSignalMappingJob).where(
                OntSignalMappingJob.request_fingerprint == fingerprint
            )
        )
    ).scalar_one()
    expected_identity = (
        mode, run_id, observed_generation, raw_representation_id, move_source_id,
        mapping_profile_id, reference_revision_id, alignment_job_id,
        alignment_session_id, parent_mapping_job_id,
    )
    actual_identity = (
        job.mode, job.run_id, job.observed_generation, job.raw_representation_id,
        job.move_source_id, job.mapping_profile_id, job.reference_revision_id,
        job.alignment_job_id, job.alignment_session_id, job.parent_mapping_job_id,
    )
    if actual_identity != expected_identity:
        raise OntSignalError("mapping request fingerprint is bound to different authority")
    return await get_mapping_job(session, job.id)


async def get_mapping_job(session: AsyncSession, job_id: str) -> dict[str, Any]:
    job = await session.get(OntSignalMappingJob, job_id)
    if job is None:
        raise KeyError("mapping job not found")
    artifacts = (
        await session.execute(select(OntSignalMappingArtifact).where(OntSignalMappingArtifact.mapping_job_id == job.id).order_by(OntSignalMappingArtifact.kind))
    ).scalars()
    return {
        "mapping_job_id": job.id, "mode": job.mode, "run_id": job.run_id,
        "observed_generation": job.observed_generation, "raw_representation_id": job.raw_representation_id,
        "move_source_id": job.move_source_id, "mapping_profile_id": job.mapping_profile_id,
        "reference_revision_id": job.reference_revision_id, "alignment_job_id": job.alignment_job_id,
        "alignment_session_id": job.alignment_session_id, "parent_mapping_job_id": job.parent_mapping_job_id,
        "domain_revision": _public_json(
            ((job.resource_snapshot or {}).get("parents") or {}).get("domain_revision")
        ),
        "request_fingerprint": job.request_fingerprint,
        "state": job.state, "reason_code": job.reason_code, "attempt": job.attempt,
        "resource_snapshot": _public_json(job.resource_snapshot), "stage_receipts": _public_json(job.stage_receipts),
        "failure_code": job.failure_code, "failure_message": _public_json(job.failure_message),
        "artifacts": [_artifact_public(row) for row in artifacts],
        "created_at": _public_time(job.created_at), "updated_at": _public_time(job.updated_at),
        "completed_at": _public_time(job.completed_at),
    }


async def cancel_mapping_job(session: AsyncSession, job_id: str) -> dict[str, Any]:
    job = await _fresh_row(session, OntSignalMappingJob, job_id)
    if job is None:
        raise KeyError("mapping job not found")
    for _attempt in range(2):
        if job.state in {"ready", "failed", "cancelled"}:
            return await get_mapping_job(session, job.id)
        if job.state == "requested":
            requested_at = _now()
            disposition = "cancelled_before_claim"
            receipts = {
                **(job.stage_receipts or {}),
                "cancellation": {
                    "requested_at": requested_at.isoformat(),
                    "disposition": disposition,
                },
            }
            result = await session.execute(
                update(OntSignalMappingJob)
                .where(OntSignalMappingJob.id == job_id, OntSignalMappingJob.state == "requested")
                .values(
                    state="cancelled",
                    reason_code=disposition,
                    cancel_requested_at=requested_at,
                    completed_at=requested_at,
                    updated_at=requested_at,
                    stage_receipts=receipts,
                )
                .execution_options(synchronize_session=False)
            )
        elif job.state == "running" and job.cancel_requested_at is None:
            requested_at = _now()
            disposition = "worker_termination_requested"
            receipts = {
                **(job.stage_receipts or {}),
                "cancellation": {
                    "requested_at": requested_at.isoformat(),
                    "disposition": disposition,
                },
            }
            result = await session.execute(
                update(OntSignalMappingJob)
                .where(
                    OntSignalMappingJob.id == job_id,
                    OntSignalMappingJob.state == "running",
                    OntSignalMappingJob.cancel_requested_at.is_(None),
                )
                .values(
                    reason_code="cancellation_requested",
                    cancel_requested_at=requested_at,
                    updated_at=requested_at,
                    stage_receipts=receipts,
                )
                .execution_options(synchronize_session=False)
            )
        else:
            return await get_mapping_job(session, job.id)
        if result.rowcount == 1:
            await session.execute(
                sqlite_insert(OntSignalMappingEvent)
                .values(
                    id=f"ont-signal-event-cancel-{_digest({'job_id': job.id, 'command': 'cancel'})}",
                    job_id=job.id,
                    state="cancelled" if disposition == "cancelled_before_claim" else "running",
                    reason_code="cancelled_before_claim" if disposition == "cancelled_before_claim" else "cancellation_requested",
                    receipt={"disposition": disposition},
                    created_at=requested_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
        job = await _fresh_row(session, OntSignalMappingJob, job_id)
        if result.rowcount == 1 or job is None:
            break
    if job is None:
        raise KeyError("mapping job not found")
    return await get_mapping_job(session, job.id)


def normalize_render_params(raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "strand", "signal_units", "scale", "base_shift_source", "base_shift_value",
        "fixed_width", "base_width", "point_size", "base_limit", "signal_sample_limit",
        "pileup_read_limit", "loose_bound", "show_samples", "show_base_colours",
        "remove_signal_outliers", "managed_bed_artifact_id",
    }
    if set(raw) - allowed:
        raise OntSignalError("render parameters contain unsupported fields")
    result = {
        "strand": raw.get("strand", "forward"), "signal_units": raw.get("signal_units", "pA"),
        "scale": raw.get("scale", "none"), "base_shift_source": raw.get("base_shift_source", "profile"),
        "base_shift_value": int(raw.get("base_shift_value", 0)), "fixed_width": bool(raw.get("fixed_width", False)),
        "base_width": int(raw.get("base_width", 10)), "point_size": float(raw.get("point_size", 0.5)),
        "base_limit": int(raw.get("base_limit", 1000)), "signal_sample_limit": int(raw.get("signal_sample_limit", 100000)),
        "pileup_read_limit": int(raw.get("pileup_read_limit", 20)), "loose_bound": bool(raw.get("loose_bound", False)),
        "show_samples": bool(raw.get("show_samples", True)), "show_base_colours": bool(raw.get("show_base_colours", True)),
        "remove_signal_outliers": bool(raw.get("remove_signal_outliers", False)),
        "managed_bed_artifact_id": raw.get("managed_bed_artifact_id"),
    }
    if result["strand"] not in {"forward", "reverse"} or result["signal_units"] not in {"pA", "raw_adc"}:
        raise OntSignalError("render strand or signal units are invalid")
    if result["scale"] not in {"none", "medmad", "znorm", "scaledpA"}:
        raise OntSignalError("render scale is invalid")
    if result["base_shift_source"] not in {"profile", "explicit"}:
        raise OntSignalError("base-shift source is invalid")
    if result["base_shift_source"] == "profile" and result["base_shift_value"] != 0:
        raise OntSignalError("profile-sourced base shift cannot carry an explicit value")
    if not -64 <= result["base_shift_value"] <= 64 or not 1 <= result["base_width"] <= 100:
        raise OntSignalError("render geometry is outside bounded policy")
    point_size = result["point_size"]
    if (
        not 0.5 <= point_size <= 10
        or (point_size != 0.5 and not point_size.is_integer())
        or not 1 <= result["base_limit"] <= MAX_BASE_LIMIT
    ):
        raise OntSignalError("render point/base limit is outside bounded policy")
    if not 1 <= result["signal_sample_limit"] <= MAX_SAMPLE_LIMIT or not 1 <= result["pileup_read_limit"] <= MAX_PILEUP_READS:
        raise OntSignalError("render sample/read limit is outside bounded policy")
    if result["managed_bed_artifact_id"] is not None and not OPAQUE_ID.fullmatch(str(result["managed_bed_artifact_id"])):
        raise OntSignalError("managed BED artifact ID is invalid")
    return result


async def create_view_job(
    session: AsyncSession,
    *, mapping_artifact_id: str, mode: str, read_id: str | None,
    reference_contig: str | None, reference_start: int | None, reference_end: int | None,
    render_params: Mapping[str, Any],
) -> dict[str, Any]:
    if mode not in {"read", "reference", "pileup"}:
        raise OntSignalError("unsupported Squigualiser view mode")
    artifact = await session.get(OntSignalMappingArtifact, mapping_artifact_id)
    if artifact is None:
        raise OntSignalError("validated mapping artifact is unavailable")
    mapping = await session.get(OntSignalMappingJob, artifact.mapping_job_id)
    if mapping is None or mapping.state != "ready":
        raise OntSignalError("ready mapping authority is required")
    if mode == "read":
        if mapping.mode != "signal_to_read" or artifact.kind != "reform_paf":
            raise OntSignalError("read view requires a signal-to-read reform mapping artifact")
        if not read_id or not OPAQUE_ID.fullmatch(read_id):
            raise OntSignalError("an exact governed read ID is required")
        if any(value is not None for value in (reference_contig, reference_start, reference_end)):
            raise OntSignalError("read view cannot accept a reference region")
    else:
        if read_id is not None or not reference_contig or not CONTIG.fullmatch(reference_contig):
            raise OntSignalError("reference view requires one governed contig region")
        if reference_start is None or reference_end is None or reference_start < 1 or reference_end < reference_start or reference_end - reference_start + 1 > MAX_REGION_BP:
            raise OntSignalError("reference region is outside bounded policy")
        if mapping.mode != "signal_to_reference":
            raise OntSignalError("signal-to-reference mapping is required")
    normalized = normalize_render_params(render_params)
    if normalized["base_shift_source"] == "profile":
        profile = await session.get(OntSignalMappingProfile, mapping.mapping_profile_id)
        if profile is None:
            raise OntSignalError("mapping profile authority is unavailable")
        authority = _mapping_profile_base_shift_authority(profile)
        normalized.update(
            {
                "base_shift_profile_id": authority["mapping_profile_id"],
                "base_shift_profile_sha256": authority["profile_sha256"],
                "base_shift_effective_value": authority["effective_value"],
            }
        )
    if mode == "pileup" and normalized["loose_bound"]:
        raise OntSignalError("pileup loose bounds are unsupported by pinned Squigualiser v0.7.0")
    managed_bed_id = normalized.get("managed_bed_artifact_id")
    if managed_bed_id is not None:
        _bed_path, bed_identity = await resolve_managed_bed_authority(
            session, str(managed_bed_id)
        )
        normalized.update(
            {
                "managed_bed_source_job_id": bed_identity["source_job_id"],
                "managed_bed_sha256": bed_identity["sha256"],
                "managed_bed_size_bytes": bed_identity["size_bytes"],
            }
        )
    identity = {
        "mapping_artifact_id": mapping_artifact_id, "mapping_sha256": artifact.sha256,
        "mode": mode, "read_id": read_id, "reference_contig": reference_contig,
        "reference_start": reference_start, "reference_end": reference_end, "render_params": normalized,
    }
    fingerprint = _digest(identity)
    view_id = f"ont-squig-view-{fingerprint}"
    now = _now()
    await session.execute(
        sqlite_insert(OntSquigualiserViewJob)
        .values(
            id=view_id,
            mapping_artifact_id=mapping_artifact_id,
            mode=mode,
            read_id=read_id,
            reference_contig=reference_contig,
            reference_start=reference_start,
            reference_end=reference_end,
            render_params=normalized,
            request_fingerprint=fingerprint,
            state="requested",
            reason_code="squigualiser_view_requested",
            attempt=0,
            output_manifest={},
            render_receipt={"request_identity_sha256": fingerprint},
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["request_fingerprint"])
    )
    view = (
        await session.execute(
            select(OntSquigualiserViewJob).where(
                OntSquigualiserViewJob.request_fingerprint == fingerprint
            )
        )
    ).scalar_one()
    expected_identity = (
        mapping_artifact_id, mode, read_id, reference_contig,
        reference_start, reference_end, normalized,
    )
    actual_identity = (
        view.mapping_artifact_id, view.mode, view.read_id, view.reference_contig,
        view.reference_start, view.reference_end, view.render_params,
    )
    if actual_identity != expected_identity:
        raise OntSignalError("view request fingerprint is bound to different authority")
    return _view_public(view)


def _view_public(row: OntSquigualiserViewJob) -> dict[str, Any]:
    output = row.output_manifest if isinstance(row.output_manifest, dict) else {}
    cleaned_output = _public_json(output)
    cleaned_output = cleaned_output if isinstance(cleaned_output, dict) else {}
    artifacts = []
    for item in output.get("artifacts", []):
        public = _public_json(item)
        public = public if isinstance(public, dict) else {}
        if row.state == "ready" and isinstance(item.get("artifact_id"), str):
            public["url"] = f"/api/ont/signal-workbench/views/{row.id}/artifacts/{item['artifact_id']}"
        artifacts.append(public)
    cleaned_output["artifacts"] = artifacts
    return {
        "view_job_id": row.id, "mapping_artifact_id": row.mapping_artifact_id, "mode": row.mode,
        "read_id": row.read_id, "reference_region": None if row.reference_contig is None else {
            "contig": row.reference_contig, "start": row.reference_start, "end": row.reference_end,
        },
        "render_params": _public_json(row.render_params), "request_fingerprint": row.request_fingerprint,
        "state": row.state, "reason_code": row.reason_code,
        "output_manifest": cleaned_output, "render_receipt": _public_json(row.render_receipt),
        "failure_code": row.failure_code, "failure_message": _public_json(row.failure_message),
        "created_at": _public_time(row.created_at), "updated_at": _public_time(row.updated_at),
        "completed_at": _public_time(row.completed_at),
    }


async def get_view_job(session: AsyncSession, view_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSquigualiserViewJob, view_job_id)
    if row is None:
        raise KeyError("view job not found")
    return _view_public(row)


async def cancel_view_job(session: AsyncSession, view_job_id: str) -> dict[str, Any]:
    row = await _fresh_row(session, OntSquigualiserViewJob, view_job_id)
    if row is None:
        raise KeyError("view job not found")
    for _attempt in range(2):
        if row.state in {"ready", "failed", "cancelled"}:
            return _view_public(row)
        if row.state == "requested":
            requested_at = _now()
            disposition = "cancelled_before_claim"
            receipt = {
                **(row.render_receipt or {}),
                "cancellation": {
                    "requested_at": requested_at.isoformat(),
                    "disposition": disposition,
                },
            }
            result = await session.execute(
                update(OntSquigualiserViewJob)
                .where(
                    OntSquigualiserViewJob.id == view_job_id,
                    OntSquigualiserViewJob.state == "requested",
                )
                .values(
                    state="cancelled",
                    reason_code=disposition,
                    cancel_requested_at=requested_at,
                    completed_at=requested_at,
                    updated_at=requested_at,
                    render_receipt=receipt,
                )
                .execution_options(synchronize_session=False)
            )
        elif row.state == "running" and row.cancel_requested_at is None:
            requested_at = _now()
            receipt = {
                **(row.render_receipt or {}),
                "cancellation": {
                    "requested_at": requested_at.isoformat(),
                    "disposition": "worker_termination_requested",
                },
            }
            result = await session.execute(
                update(OntSquigualiserViewJob)
                .where(
                    OntSquigualiserViewJob.id == view_job_id,
                    OntSquigualiserViewJob.state == "running",
                    OntSquigualiserViewJob.cancel_requested_at.is_(None),
                )
                .values(
                    reason_code="cancellation_requested",
                    cancel_requested_at=requested_at,
                    updated_at=requested_at,
                    render_receipt=receipt,
                )
                .execution_options(synchronize_session=False)
            )
        else:
            return _view_public(row)
        row = await _fresh_row(session, OntSquigualiserViewJob, view_job_id)
        if result.rowcount == 1 or row is None:
            break
    if row is None:
        raise KeyError("view job not found")
    return _view_public(row)


def _open_managed_output_descriptor(relative: str) -> int:
    root = Path(os.path.abspath(get_results_dir() / "ont_signal_workbench"))
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or not candidate_relative.parts or any(part in {"", ".", ".."} for part in candidate_relative.parts):
        raise OntSignalError("view artifact path is invalid")
    root_fd = _open_absolute_directory_nofollow(root)
    parent_fd = root_fd
    try:
        for component in candidate_relative.parts[:-1]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            if parent_fd != root_fd:
                os.close(parent_fd)
            parent_fd = child_fd
        descriptor = os.open(
            candidate_relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OntSignalError("view artifact is not a retained regular file")
        return descriptor
    except OSError as exc:
        raise OntSignalError("view artifact cannot be opened without following links") from exc
    finally:
        if parent_fd != root_fd:
            os.close(parent_fd)
        os.close(root_fd)


def _read_verified_view_artifact(item: Mapping[str, Any]) -> bytes:
    media_type = str(item.get("media_type") or "")
    limit = MAX_VIEW_SVG_BYTES if media_type == "image/svg+xml" else MAX_VIEW_HTML_BYTES
    expected_size = item.get("size_bytes")
    expected_sha256 = item.get("sha256")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or expected_size > limit
        or not isinstance(expected_sha256, str)
        or not HEX64.fullmatch(expected_sha256)
    ):
        raise OntSignalError("view artifact integrity metadata is invalid")
    descriptor = _open_managed_output_descriptor(
        str(item.get("managed_relative_path") or "")
    )
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - total)):
            total += len(chunk)
            if total > limit:
                raise OntSignalError("view artifact exceeds bounded serving policy")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or total != expected_size
        or digest.hexdigest() != expected_sha256
    ):
        raise OntSignalError("view artifact integrity changed")
    return b"".join(chunks)


async def resolve_view_artifact(session: AsyncSession, view_job_id: str, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
    row = await session.get(OntSquigualiserViewJob, view_job_id)
    if row is None or row.state != "ready":
        raise KeyError("ready view not found")
    output = row.output_manifest if isinstance(row.output_manifest, dict) else {}
    for item in output.get("artifacts", []):
        if item.get("artifact_id") != artifact_id:
            continue
        return await asyncio.to_thread(_read_verified_view_artifact, item), item
    raise KeyError("view artifact not found")


async def workbench_capabilities(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    alignment_job_id: str | None = None,
    alignment_session_id: str | None = None,
    reference_revision_id: str | None = None,
) -> dict[str, Any]:
    reference_scope = (alignment_job_id, alignment_session_id, reference_revision_id)
    if any(value is not None for value in reference_scope) and not all(
        isinstance(value, str) and value for value in reference_scope
    ):
        raise OntSignalError("exact reference capability authority is incomplete")
    representations = list((await session.execute(select(OntRawSignalRepresentation).where(
        OntRawSignalRepresentation.run_id == run_id,
        OntRawSignalRepresentation.observed_generation == observed_generation,
    ))).scalars())
    blow5 = next((row for row in representations if row.format == "blow5" and row.state == "ready" and isinstance(row.validation_receipts, dict) and row.validation_receipts.get("adjacent_index") is True), None)
    source = None if blow5 is None else (await session.execute(select(OntMoveTableSource).where(
        OntMoveTableSource.raw_representation_id == blow5.id,
        OntMoveTableSource.validation_state == "ready",
        OntMoveTableSource.basecall_model_id.is_not(None),
        OntMoveTableSource.basecall_model_id != "",
        OntMoveTableSource.read_inventory_sha256.is_not(None),
        OntMoveTableSource.read_inventory_sha256 != "",
    ).order_by(OntMoveTableSource.validated_at.desc()))).scalars().first()
    calibration_job = None if source is None else (await session.execute(select(OntSignalCalibrationJob).where(
        OntSignalCalibrationJob.raw_representation_id == source.raw_representation_id,
        OntSignalCalibrationJob.move_source_id == source.id,
        OntSignalCalibrationJob.state == "ready",
        OntSignalCalibrationJob.calibration_artifact_id.is_not(None),
    ).order_by(OntSignalCalibrationJob.completed_at.desc(), OntSignalCalibrationJob.id.desc()))).scalars().first()
    calibration_artifact = None if calibration_job is None else await session.get(OntSignalCalibrationArtifact, calibration_job.calibration_artifact_id)
    if source is not None and calibration_artifact is not None and (
        calibration_artifact.raw_representation_id != source.raw_representation_id
        or calibration_artifact.move_source_id != source.id
        or calibration_artifact.basecall_model_id != source.basecall_model_id
    ):
        calibration_artifact = None
        calibration_job = None
    approved_profile = None
    if source is not None and calibration_artifact is not None:
        candidates = list((await session.execute(select(OntSignalMappingProfile).where(
            OntSignalMappingProfile.basecall_model_id == source.basecall_model_id,
            OntSignalMappingProfile.molecule_type == source.molecule_type,
            OntSignalMappingProfile.parameter_source == "approved_calibration",
            OntSignalMappingProfile.calibration_artifact_id == calibration_artifact.id,
            OntSignalMappingProfile.primary_alignment_policy == "primary_only",
            OntSignalMappingProfile.minimum_mapq == 0,
            OntSignalMappingProfile.include_supplementary.is_(False),
            OntSignalMappingProfile.read_set_selection == "immutable_full_set",
        ).order_by(OntSignalMappingProfile.approved_at.desc()))).scalars())
        approved_profile = next((profile for profile in candidates if (
            profile.kmer_length == calibration_artifact.recommended_kmer_length
            and profile.signal_move_offset == calibration_artifact.recommended_signal_move_offset
        )), None)
    read_mapping = None
    if blow5 is not None and source is not None and approved_profile is not None:
        read_mapping = (await session.execute(select(OntSignalMappingJob).where(
            OntSignalMappingJob.raw_representation_id == blow5.id,
            OntSignalMappingJob.move_source_id == source.id,
            OntSignalMappingJob.mapping_profile_id == approved_profile.id,
            OntSignalMappingJob.mode == "signal_to_read",
            OntSignalMappingJob.state == "ready",
        ).order_by(OntSignalMappingJob.completed_at.desc()))).scalars().first()
    reference_filters = [
        OntSignalMappingJob.parent_mapping_job_id == read_mapping.id if read_mapping is not None else False,
        OntSignalMappingJob.mode == "signal_to_reference",
        OntSignalMappingJob.state == "ready",
        OntSignalMappingJob.run_id == run_id,
        OntSignalMappingJob.observed_generation == observed_generation,
    ]
    if all(value is not None for value in reference_scope):
        reference_filters.extend([
            OntSignalMappingJob.alignment_job_id == alignment_job_id,
            OntSignalMappingJob.alignment_session_id == alignment_session_id,
            OntSignalMappingJob.reference_revision_id == reference_revision_id,
        ])
    reference_mapping = None if read_mapping is None or not all(
        value is not None for value in reference_scope
    ) else (
        await session.execute(
            select(OntSignalMappingJob)
            .where(*reference_filters)
            .order_by(OntSignalMappingJob.completed_at.desc())
        )
    ).scalars().first()
    if blow5 is None:
        read_mode = {"state": "unavailable", "reason_code": "indexed_blow5_authority_missing"}
    elif source is None:
        read_mode = {"state": "unavailable", "reason_code": "compatible_move_table_source_missing"}
    elif read_mapping is None:
        read_mode = {"state": "preparable", "reason_code": "validated_move_source_ready"}
    else:
        read_mode = {"state": "ready", "reason_code": "validated_reform_mapping_ready"}
    if read_mapping is None:
        reference_mode = {"state": "unavailable", "reason_code": "signal_to_read_mapping_missing"}
    elif reference_mapping is None:
        reference_mode = {"state": "preparable", "reason_code": "governed_reference_alignment_required"}
    else:
        reference_mode = {"state": "ready", "reason_code": "validated_realign_mapping_ready"}
    return {
        "run_id": run_id, "observed_generation": observed_generation,
        "resolved": {
            "raw_representation_id": blow5.id if blow5 else None,
            "move_source_id": source.id if source else None,
            "mapping_profile_id": read_mapping.mapping_profile_id if read_mapping else (approved_profile.id if approved_profile else None),
            "calibration_job_id": calibration_job.id if calibration_job else None,
            "calibration_artifact_id": calibration_artifact.id if calibration_artifact else None,
            "signal_to_read_mapping_job_id": read_mapping.id if read_mapping else None,
            "signal_to_reference_mapping_job_id": reference_mapping.id if reference_mapping else None,
        },
        "modes": {
            "igv": {"state": "independent", "reason_code": "alignment_session_scoped"},
            "raw_waveform": {"state": "ready" if blow5 else "unavailable", "reason_code": "indexed_blow5_ready" if blow5 else "indexed_blow5_authority_missing"},
            "signal_to_read": read_mode,
            "signal_to_reference": reference_mode,
            "signal_pileup": {"state": "ready" if reference_mapping else "unavailable", "reason_code": "bounded_rendering_ready" if reference_mapping else "signal_to_reference_mapping_missing"},
        },
    }


async def create_viewer_session(
    session: AsyncSession,
    *, dataset_id: str, run_id: str, observed_generation: int,
    alignment_job_id: str | None, alignment_session_id: str | None,
    reference_revision_id: str | None, contig: str | None,
    locus_start: int | None, locus_end: int | None, selected_read_id: str | None,
    igv_state: Mapping[str, Any], signal_state: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_viewer_selection_authority(
        contig=contig,
        locus_start=locus_start,
        locus_end=locus_end,
        selected_read_id=selected_read_id,
    )
    if not alignment_job_id:
        raise OntSignalError(
            "dataset and run viewer authority requires a completed job authority"
        )
    if (alignment_session_id is None) != (reference_revision_id is None):
        raise OntSignalError(
            "alignment session and reference revision authority must be supplied together"
        )
    event = (await session.execute(select(OntInstrumentRunEvent).where(
        OntInstrumentRunEvent.run_id == run_id,
        OntInstrumentRunEvent.observed_generation == observed_generation,
    ))).scalar_one_or_none()
    if event is None:
        raise KeyError("run generation not found")
    alignment_job = await session.get(Job, str(alignment_job_id))
    if alignment_job is None or alignment_job.status != "completed":
        raise OntSignalError("completed alignment authority is unavailable")
    params = alignment_job.params if isinstance(alignment_job.params, dict) else {}
    bound_generation = params.get("source_instrument_observed_generation")
    if params.get("dataset_id") != dataset_id:
        raise OntSignalError("dataset does not equal the completed alignment authority")
    if (
        params.get("source_instrument_run_id") != run_id
        or isinstance(bound_generation, bool)
        or bound_generation != observed_generation
    ):
        raise OntSignalError("run generation does not equal the completed alignment authority")
    if reference_revision_id is not None:
        if params.get("ngs_reference_revision_id") != reference_revision_id:
            raise OntSignalError("reference revision does not equal the completed alignment authority")
        alignment = await _resolve_primary_alignment_session_async(
            alignment_job.id,
            str(alignment_session_id),
            _alignment_authority(alignment_job),
            alignment_job.child_output_dir or alignment_job.output_dir,
        )
        if alignment.get("ready") is not True:
            raise OntSignalError("alignment session authority is not ready")
    capabilities = await workbench_capabilities(
        session,
        run_id=run_id,
        observed_generation=observed_generation,
        alignment_job_id=alignment_job_id if alignment_session_id is not None else None,
        alignment_session_id=alignment_session_id,
        reference_revision_id=reference_revision_id,
    )
    resolved = capabilities["resolved"]
    if alignment_session_id is None:
        resolved["signal_to_reference_mapping_job_id"] = None
        capabilities["modes"]["signal_to_reference"] = {
            "state": "unavailable",
            "reason_code": "viewer_alignment_authority_absent",
        }
        capabilities["modes"]["signal_pileup"] = {
            "state": "unavailable",
            "reason_code": "viewer_alignment_authority_absent",
        }
    for key in (
        "signal_to_read_mapping_job_id",
        "signal_to_reference_mapping_job_id",
    ):
        mapping_id = resolved.get(key)
        if mapping_id is None:
            continue
        mapping = await session.get(OntSignalMappingJob, str(mapping_id))
        if (
            mapping is None
            or mapping.state != "ready"
            or mapping.run_id != run_id
            or mapping.observed_generation != observed_generation
        ):
            raise OntSignalError("resolved mapping parent diverges from viewer run authority")
        if mapping.mode == "signal_to_reference" and (
            mapping.alignment_job_id != alignment_job_id
            or mapping.alignment_session_id != alignment_session_id
            or mapping.reference_revision_id != reference_revision_id
        ):
            raise OntSignalError(
                "resolved reference mapping parent diverges from viewer alignment authority"
            )
    viewer = OntSignalViewerSession(
        id=_id("ont-viewer"), dataset_id=dataset_id, run_id=run_id, observed_generation=observed_generation,
        alignment_job_id=alignment_job_id, alignment_session_id=alignment_session_id,
        reference_revision_id=reference_revision_id, raw_representation_id=resolved["raw_representation_id"],
        move_source_id=resolved["move_source_id"], mapping_profile_id=resolved["mapping_profile_id"],
        contig=contig, locus_start=locus_start, locus_end=locus_end, selected_read_id=selected_read_id,
        igv_state=dict(igv_state), signal_state=dict(signal_state),
        revision=1, created_at=_now(), updated_at=_now(),
    )
    await _validate_viewer_state_authority(
        session,
        viewer,
        contig=contig,
        locus_start=locus_start,
        locus_end=locus_end,
        selected_read_id=selected_read_id,
        igv_state=igv_state,
        signal_state=signal_state,
    )
    session.add(viewer)
    await session.flush()
    return _viewer_public(viewer)


def _viewer_public(row: OntSignalViewerSession) -> dict[str, Any]:
    return {
        "viewer_session_id": row.id, "dataset_id": row.dataset_id, "run_id": row.run_id,
        "observed_generation": row.observed_generation, "alignment_job_id": row.alignment_job_id,
        "alignment_session_id": row.alignment_session_id, "reference_revision_id": row.reference_revision_id,
        "raw_representation_id": row.raw_representation_id, "move_source_id": row.move_source_id,
        "mapping_profile_id": row.mapping_profile_id, "contig": row.contig,
        "locus_start": row.locus_start, "locus_end": row.locus_end,
        "selected_read_id": row.selected_read_id, "igv_state": _public_json(row.igv_state),
        "signal_state": _public_json(row.signal_state), "revision": row.revision,
        "created_at": _public_time(row.created_at), "updated_at": _public_time(row.updated_at),
        "reopen_url": f"/ngs?view=workbench&viewer_session_id={row.id}",
    }


async def get_viewer_session(session: AsyncSession, viewer_session_id: str) -> dict[str, Any]:
    row = await session.get(OntSignalViewerSession, viewer_session_id)
    if row is None:
        raise KeyError("viewer session not found")
    return _viewer_public(row)


def _validate_viewer_selection_authority(
    *,
    contig: str | None,
    locus_start: int | None,
    locus_end: int | None,
    selected_read_id: str | None,
) -> None:
    locus_values = (contig, locus_start, locus_end)
    if any(value is not None for value in locus_values) and not all(
        value is not None for value in locus_values
    ):
        raise OntSignalError("viewer locus authority is incomplete")
    if contig is not None and (
        not CONTIG.fullmatch(contig)
        or locus_start is None
        or locus_start < 1
        or locus_end is None
        or locus_end < locus_start
    ):
        raise OntSignalError("viewer locus authority is invalid")
    if selected_read_id is not None and not OPAQUE_ID.fullmatch(selected_read_id):
        raise OntSignalError("viewer selected read authority is invalid")


async def _validate_viewer_state_authority(
    session: AsyncSession,
    row: OntSignalViewerSession,
    *,
    contig: str | None,
    locus_start: int | None,
    locus_end: int | None,
    selected_read_id: str | None,
    igv_state: Mapping[str, Any],
    signal_state: Mapping[str, Any],
) -> None:
    _validate_viewer_selection_authority(
        contig=contig,
        locus_start=locus_start,
        locus_end=locus_end,
        selected_read_id=selected_read_id,
    )

    for state_key, expected in (
        ("alignment_job_id", row.alignment_job_id),
        ("alignment_session_id", row.alignment_session_id),
        ("reference_revision_id", row.reference_revision_id),
    ):
        if state_key in igv_state and igv_state[state_key] != expected:
            raise OntSignalError("IGV state diverges from viewer alignment authority")
    if "selected_read_id" in signal_state and signal_state["selected_read_id"] != selected_read_id:
        raise OntSignalError("signal state selected read diverges from viewer authority")

    async def require_mapping(mapping_id: Any, *, reference: bool) -> OntSignalMappingJob | None:
        if mapping_id is None:
            return None
        if not isinstance(mapping_id, str) or not mapping_id:
            raise OntSignalError("saved mapping reference is invalid")
        mapping = await session.get(OntSignalMappingJob, mapping_id)
        if (
            mapping is None
            or mapping.state != "ready"
            or mapping.run_id != row.run_id
            or mapping.observed_generation != row.observed_generation
            or (row.raw_representation_id is not None and mapping.raw_representation_id != row.raw_representation_id)
            or (row.move_source_id is not None and mapping.move_source_id != row.move_source_id)
            or (row.mapping_profile_id is not None and mapping.mapping_profile_id != row.mapping_profile_id)
        ):
            raise OntSignalError("saved mapping diverges from viewer immutable authority")
        if reference:
            if (
                mapping.mode != "signal_to_reference"
                or mapping.alignment_job_id != row.alignment_job_id
                or mapping.alignment_session_id != row.alignment_session_id
                or mapping.reference_revision_id != row.reference_revision_id
            ):
                raise OntSignalError("saved reference mapping diverges from viewer alignment authority")
        elif mapping.mode != "signal_to_read":
            raise OntSignalError("saved read mapping diverges from viewer authority")
        return mapping

    read_mapping = await require_mapping(
        signal_state.get("read_mapping_job_id"), reference=False
    )
    reference_mapping = await require_mapping(
        signal_state.get("reference_mapping_job_id"), reference=True
    )
    if reference_mapping is not None and (
        read_mapping is None
        or reference_mapping.parent_mapping_job_id != read_mapping.id
    ):
        raise OntSignalError("saved reference mapping chain diverges from the saved read mapping")

    view_job_id = signal_state.get("view_job_id")
    if view_job_id is not None:
        if not isinstance(view_job_id, str) or not view_job_id:
            raise OntSignalError("saved view reference is invalid")
        view = await session.get(OntSquigualiserViewJob, view_job_id)
        if view is None or view.state == "cancelled":
            raise OntSignalError("saved view does not belong to viewer immutable authority")
        artifact = await session.get(OntSignalMappingArtifact, view.mapping_artifact_id)
        if artifact is None:
            raise OntSignalError("saved view does not belong to viewer immutable authority")
        try:
            mapping = await require_mapping(
                artifact.mapping_job_id,
                reference=view.mode in {"reference", "pileup"},
            )
        except OntSignalError as exc:
            raise OntSignalError("saved view does not belong to viewer immutable authority") from exc
        if mapping is None:
            raise OntSignalError("saved view does not belong to viewer immutable authority")
        if view.mode == "read" and view.read_id != selected_read_id:
            raise OntSignalError("saved view selected read diverges from viewer authority")
        if view.mode in {"reference", "pileup"} and (
            view.reference_contig != contig
            or view.reference_start != locus_start
            or view.reference_end != locus_end
        ):
            raise OntSignalError("saved view locus diverges from viewer authority")


async def update_viewer_session(
    session: AsyncSession, viewer_session_id: str, *, expected_revision: int,
    contig: str | None, locus_start: int | None, locus_end: int | None,
    selected_read_id: str | None, igv_state: Mapping[str, Any], signal_state: Mapping[str, Any],
) -> dict[str, Any]:
    row = await session.get(OntSignalViewerSession, viewer_session_id)
    if row is None:
        raise KeyError("viewer session not found")
    await _validate_viewer_state_authority(
        session,
        row,
        contig=contig,
        locus_start=locus_start,
        locus_end=locus_end,
        selected_read_id=selected_read_id,
        igv_state=igv_state,
        signal_state=signal_state,
    )
    updated_at = _now()
    result = await session.execute(
        update(OntSignalViewerSession)
        .where(
            OntSignalViewerSession.id == viewer_session_id,
            OntSignalViewerSession.revision == expected_revision,
        )
        .values(
            contig=contig,
            locus_start=locus_start,
            locus_end=locus_end,
            selected_read_id=selected_read_id,
            igv_state=dict(igv_state),
            signal_state=dict(signal_state),
            revision=expected_revision + 1,
            updated_at=updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise OntSignalError("viewer session changed concurrently")
    refreshed = await _fresh_row(session, OntSignalViewerSession, viewer_session_id)
    if refreshed is None:
        raise KeyError("viewer session not found")
    return _viewer_public(refreshed)

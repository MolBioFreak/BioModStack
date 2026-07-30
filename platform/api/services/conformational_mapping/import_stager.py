"""Descriptor-safe staging for authenticated conformational-map imports."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from paths import resolve_runtime_data_path
from .contracts import candidate_id, canonical_json_bytes, canonical_sha256, validate_schema
from .import_snapshot import (
    ImportSnapshotError,
    MAX_IMPORT_MMCIF_BYTES,
    normalized_import_snapshot_sha256,
    read_staged_import_file_at,
)


MAX_IMPORT_FILES = 256
MAX_IMPORT_BYTES = 2 * 1024 * 1024 * 1024
_ALLOWED_SUFFIXES = {".cif", ".mmcif", ".pdb"}


class ImportStagingError(ValueError):
    """An import could not be authenticated and copied without ambiguity."""



@dataclass(frozen=True)
class RegisteredArtifact:
    artifact_id: str
    principal_id: str
    storage_root: Path
    relative_path: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class StagedImport:
    root: Path
    receipt_path: Path
    receipt: dict[str, Any]


def verify_registered_artifact(
    artifact: RegisteredArtifact, *, principal_id: str, maximum_bytes: int = MAX_IMPORT_BYTES
) -> tuple[str, int]:
    """Revalidate a registered regular file without materializing its bytes."""

    descriptor, before = _open_registered(artifact)
    try:
        if before.st_size > maximum_bytes:
            raise ImportStagingError("registered artifact exceeds the allowed byte limit")
        digest, size_bytes, _prefix = _digest_fd(descriptor, maximum_bytes=maximum_bytes)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ImportStagingError("registered artifact changed during verification")
        if digest != artifact.content_sha256 or size_bytes != artifact.size_bytes:
            raise ImportStagingError("registered artifact identity changed")
        return digest, size_bytes
    finally:
        os.close(descriptor)


def read_registered_artifact(
    artifact: RegisteredArtifact, *, principal_id: str, maximum_bytes: int = MAX_IMPORT_BYTES
) -> bytes:
    """Read immutable registered bytes through a no-follow descriptor and revalidate identity."""

    descriptor, before = _open_registered(artifact)
    try:
        if before.st_size > maximum_bytes:
            raise ImportStagingError("registered artifact exceeds the allowed byte limit")
        digest, size_bytes, _prefix = _digest_fd(descriptor, maximum_bytes=maximum_bytes)
        if digest != artifact.content_sha256 or size_bytes != artifact.size_bytes:
            raise ImportStagingError("registered artifact identity changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = size_bytes
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ImportStagingError("registered artifact became truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ImportStagingError("registered artifact changed during reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def stage_registered_assets(
    artifacts: Sequence[RegisteredArtifact],
    *,
    principal_id: str,
    destination_root: Path | str,
    maximum_bytes: int = MAX_IMPORT_BYTES,
) -> dict[str, Path]:
    """Copy immutable non-structure runtime assets by registered identity."""

    destination = Path(destination_root)
    destination.mkdir(parents=True, exist_ok=False)
    staged: dict[str, Path] = {}
    try:
        for artifact in artifacts:
            if artifact.artifact_id in staged:
                raise ImportStagingError("runtime asset identity is duplicated")
            descriptor, before = _open_registered(artifact)
            try:
                if before.st_size > maximum_bytes:
                    raise ImportStagingError("registered runtime asset exceeds the allowed byte limit")
                digest, size_bytes, _prefix = _digest_fd(descriptor, maximum_bytes=maximum_bytes)
                if digest != artifact.content_sha256 or size_bytes != artifact.size_bytes:
                    raise ImportStagingError("registered runtime asset identity changed")
                suffix = Path(artifact.relative_path).suffix.lower()
                output = destination / f"{artifact.artifact_id}{suffix}"
                output_fd = os.open(
                    output,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o440,
                )
                copied = hashlib.sha256()
                copied_size = 0
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        if copied_size + len(chunk) > maximum_bytes:
                            raise ImportStagingError(
                                "registered runtime asset exceeds the byte limit during staging"
                            )
                        _write_all(output_fd, chunk)
                        copied.update(chunk)
                        copied_size += len(chunk)
                    os.fsync(output_fd)
                finally:
                    os.close(output_fd)
                after = os.fstat(descriptor)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
                ):
                    raise ImportStagingError("registered runtime asset changed during staging")
                if copied.hexdigest() != digest or copied_size != size_bytes:
                    raise ImportStagingError("staged runtime asset rehash mismatch")
                staged[artifact.artifact_id] = output
            finally:
                os.close(descriptor)
        return staged
    except Exception:
        for path in destination.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
        destination.rmdir()
        raise


def _digest_fd(descriptor: int, *, maximum_bytes: int = MAX_IMPORT_BYTES) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if len(prefix) < 256 * 1024:
            prefix.extend(chunk[: 256 * 1024 - len(prefix)])
        if total > maximum_bytes:
            raise ImportStagingError("registered artifact exceeds the import byte limit")
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), total, bytes(prefix)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while staging registered artifact")
        view = view[written:]


def _canonical_relative(value: str) -> PurePosixPath:
    if not value or "\\" in value or any(token in value for token in ("*", "?", "[", "]", "{", "}", ";", "|", "`", "$", "\n", "\r", "%")):
        raise ImportStagingError("registered artifact metadata contains an unsafe path token")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ImportStagingError("registered artifact metadata is not a canonical relative path")
    return path


def _resolve_registered_storage_root(storage_root: Path) -> Path:
    """Translate the configured container state root before opening a registered source.

    Source descriptors are immutable and may have been registered by the
    container runtime.  Native Development owns the same state tree at
    ``BMS_STATE_DIR``; resolve that configured alias lexically first so the
    subsequent descriptor traversal can still enforce no-follow semantics.
    """
    container_root = os.environ.get("BMS_CONTAINER_STATE_PATH", "").strip()
    native_root = os.environ.get("BMS_STATE_DIR", "").strip()
    if container_root and native_root:
        try:
            suffix = storage_root.relative_to(Path(container_root))
        except ValueError:
            pass
        else:
            return Path(native_root) / suffix
    return resolve_runtime_data_path(storage_root)


def _open_registered(artifact: RegisteredArtifact) -> tuple[int, os.stat_result]:
    relative = _canonical_relative(artifact.relative_path)
    root = _resolve_registered_storage_root(artifact.storage_root).resolve(strict=True)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    current_fd = root_fd
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            os.close(descriptor)
            raise ImportStagingError("registered artifact is not a regular file")
        return descriptor, source_stat
    except OSError as exc:
        raise ImportStagingError("registered artifact is unavailable or unsafe") from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def _inspect_structure(suffix: str, prefix: bytes) -> None:
    text = prefix.decode("utf-8", errors="strict").lstrip()
    if suffix in {".cif", ".mmcif"}:
        if not text.startswith("data_") or "_atom_site." not in text:
            raise ImportStagingError("registered artifact content is not an mmCIF coordinate file")
    elif suffix == ".pdb":
        lines = text.splitlines()
        if not any(line.startswith(("ATOM  ", "HETATM")) for line in lines):
            raise ImportStagingError("registered artifact content is not a PDB coordinate file")
    else:
        raise ImportStagingError("registered artifact extension is unsupported")


def _atomic_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".cm_import_receipt.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(receipt)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def stage_registered_artifacts(
    artifacts: Sequence[RegisteredArtifact],
    *,
    principal_id: str,
    request_id: str,
    destination_root: Path | str,
    maximum_bytes: int = MAX_IMPORT_BYTES,
) -> StagedImport:
    """Copy registered IDs into an immutable request-owned directory.

    Descriptor identity, content identity and order are checked before a receipt
    is published. The caller schedules only after this returns.
    """

    if not artifacts or len(artifacts) > MAX_IMPORT_FILES:
        raise ImportStagingError("import artifact cardinality is outside the allowed range")
    artifact_ids = [item.artifact_id for item in artifacts]
    if any(not value for value in artifact_ids) or len(set(artifact_ids)) != len(artifact_ids):
        raise ImportStagingError("registered artifact IDs must be nonempty and unique")

    destination = Path(destination_root)
    if destination.exists():
        raise ImportStagingError("import destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{request_id}.", dir=destination.parent))
    entries: list[dict[str, Any]] = []
    content_hashes: set[str] = set()
    try:
        for index, artifact in enumerate(artifacts):
            descriptor, before = _open_registered(artifact)
            try:
                if before.st_size > maximum_bytes:
                    raise ImportStagingError("registered artifact exceeds the import byte limit")
                digest, size_bytes, prefix = _digest_fd(descriptor, maximum_bytes=maximum_bytes)
                suffix = Path(artifact.relative_path).suffix.lower()
                _inspect_structure(suffix, prefix)
                if digest != artifact.content_sha256 or size_bytes != artifact.size_bytes:
                    raise ImportStagingError("registered artifact identity changed before staging")
                if digest in content_hashes:
                    raise ImportStagingError("duplicate content in one import receipt is forbidden")
                content_hashes.add(digest)
                relative_path = f"structures/{index:06d}_{digest[:16]}{suffix}"
                destination_path = staging / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                output_fd = os.open(destination_path, flags, 0o440)
                copied_digest = hashlib.sha256()
                copied_size = 0
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        if copied_size + len(chunk) > maximum_bytes:
                            raise ImportStagingError(
                                "registered artifact exceeds the import byte limit during staging"
                            )
                        _write_all(output_fd, chunk)
                        copied_digest.update(chunk)
                        copied_size += len(chunk)
                    os.fsync(output_fd)
                finally:
                    os.close(output_fd)
                after = os.fstat(descriptor)
                identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
                    raise ImportStagingError("registered artifact changed during staging")
                if copied_digest.hexdigest() != digest or copied_size != size_bytes:
                    raise ImportStagingError("staged artifact rehash mismatch")
                entries.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "source_content_sha256": digest,
                        "source_size_bytes": size_bytes,
                        "staged_index": index,
                        "destination_relative_path": relative_path,
                        "staged_content_sha256": digest,
                        "staged_size_bytes": size_bytes,
                        "source_descriptor_identity": {
                            "device": before.st_dev,
                            "inode": before.st_ino,
                            "mtime_ns": before.st_mtime_ns,
                        },
                    }
                )
            finally:
                os.close(descriptor)
        receipt_without_hash = {
            "schema_name": "cm_import_receipt",
            "schema_version": 1,
            "request_id": request_id,
            "principal_id": principal_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "entries": entries,
        }
        receipt = {**receipt_without_hash, "receipt_sha256": canonical_sha256(receipt_without_hash)}
        _atomic_receipt(staging / "cm_import_receipt_v1.json", receipt)
        os.replace(staging, destination)
        return StagedImport(destination, destination / "cm_import_receipt_v1.json", receipt)
    except Exception:
        for path in sorted(staging.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        staging.rmdir()
        raise


def _read_staged_finalization_inputs(
    root: Path,
    *,
    request_id: object,
) -> tuple[bytes, dict[str, Any], bytes]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise ImportStagingError("immutable import root is missing or unsafe") from exc
    try:
        receipt_bytes = read_staged_import_file_at(
            root_descriptor, PurePosixPath("cm_import_receipt_v1.json"), maximum_bytes=1024 * 1024,
        )
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        supplied_hash = receipt.get("receipt_sha256")
        expected_hash = canonical_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        entries = receipt.get("entries")
        if (
            supplied_hash != expected_hash or receipt.get("request_id") != request_id
            or not isinstance(entries, list) or len(entries) != 1
        ):
            raise ImportStagingError("import receipt identity or cardinality mismatch")
        relative = _canonical_relative(str(entries[0].get("destination_relative_path") or ""))
        artifact_bytes = read_staged_import_file_at(
            root_descriptor, relative, maximum_bytes=MAX_IMPORT_MMCIF_BYTES,
        )
        return receipt_bytes, receipt, artifact_bytes
    except (ImportSnapshotError, UnicodeDecodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ImportStagingError):
            raise
        raise ImportStagingError("immutable import receipt or artifact is invalid") from exc
    finally:
        os.close(root_descriptor)


def finalize_staged_import(
    request: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    staged_root: Path | str,
    output_root: Path | str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish import manifests solely from the immutable staging receipt."""

    root = Path(staged_root)
    receipt_bytes, receipt, artifact_bytes = _read_staged_finalization_inputs(
        root, request_id=request.get("request_id"),
    )
    supplied_hash = receipt.get("receipt_sha256")
    expected_hash = canonical_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    if supplied_hash != expected_hash or receipt.get("request_id") != request.get("request_id"):
        raise ImportStagingError("import receipt identity mismatch")
    validate_schema("cm_request_v1", request)
    if request.get("import_receipt_id") != supplied_hash:
        raise ImportStagingError("request import receipt identity does not match staged receipt")
    validate_schema("cm_complex_snapshot_v1", snapshot)
    snapshot_hash = canonical_sha256(snapshot)
    if request.get("source_snapshot_sha256") != snapshot_hash:
        raise ImportStagingError("import snapshot does not match the request-bound identity")
    if snapshot.get("normalized_source_sha256") != normalized_import_snapshot_sha256(snapshot):
        raise ImportStagingError("import snapshot normalized identity mismatch")
    targets = request.get("targets")
    entries = receipt.get("entries")
    if (
        not isinstance(entries, list) or not isinstance(targets, list)
        or len(entries) != 1 or len(targets) != 1
        or snapshot.get("target_id") != targets[0].get("target_id")
        or snapshot.get("target_order") != targets[0].get("target_order")
    ):
        raise ImportStagingError("import receipt/target cardinality mismatch")
    files: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    validated_artifact: tuple[str, bytes] | None = None
    for target, entry in zip(targets, entries, strict=True):
        coordinates = {
            "backend": "external_import",
            "target_id": target["target_id"],
            "staged_index": entry["staged_index"],
            "source_content_sha256": entry["source_content_sha256"],
            "staged_receipt_sha256": supplied_hash,
        }
        stable_id = candidate_id(coordinates)
        relative_path = str(entry["destination_relative_path"])
        if Path(relative_path).suffix.lower() not in {".cif", ".mmcif"}:
            raise ImportStagingError("external import finalization accepts mmCIF only")
        digest = hashlib.sha256(artifact_bytes).hexdigest()
        if digest != entry["staged_content_sha256"] or len(artifact_bytes) != entry["staged_size_bytes"]:
            raise ImportStagingError("staged import byte identity mismatch")
        if (
            snapshot.get("original_source_path") != f"registered_import/{relative_path}"
            or snapshot.get("original_source_sha256") != digest
        ):
            raise ImportStagingError("import snapshot source identity mismatch")
        validated_artifact = (relative_path, artifact_bytes)
        files.append(
            {
                "relative_path": relative_path,
                "sha256": digest,
                "bytes": len(artifact_bytes),
                "media_type": "chemical/x-mmcif",
                "semantic_role": "authoritative_cif",
                "candidate_id": stable_id,
                "backend_coordinates": coordinates,
                "provenance_sha256": supplied_hash,
                "related_paths": ["cm_import_receipt_v1.json"],
            }
        )
        candidates.append(
            {
                "candidate_id": stable_id,
                "backend_coordinates": coordinates,
                "authoritative_structure_path": relative_path,
                "authoritative_structure_sha256": digest,
                "sidecar_paths": [],
            }
        )
    files.append(
        {
            "relative_path": "cm_import_receipt_v1.json",
            "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "bytes": len(receipt_bytes),
            "media_type": "application/json",
            "semantic_role": "receipt",
            "candidate_id": None,
            "backend_coordinates": None,
            "provenance_sha256": supplied_hash,
            "related_paths": [entry["destination_relative_path"] for entry in entries],
        }
    )
    for item in files:
        item["relative_path"] = f"native/{item['relative_path']}"
        item["related_paths"] = [f"native/{path}" for path in item["related_paths"]]
    for candidate in candidates:
        candidate["authoritative_structure_path"] = f"native/{candidate['authoritative_structure_path']}"
        candidate["sidecar_paths"] = [f"native/{path}" for path in candidate["sidecar_paths"]]
    native = {
        "schema_name": "cm_native_artifacts",
        "schema_version": 1,
        "request_id": request["request_id"],
        "backend": "external_import",
        "settings_sha256": canonical_sha256(request["runtime_policy"]),
        "files": files,
    }
    validate_schema("cm_native_artifacts_v1", native)
    native_hash = canonical_sha256(native)
    timestamp = str(receipt["created_at"])
    ensemble = {
        "schema_name": "cm_ensemble",
        "schema_version": 1,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "source_snapshot_sha256": snapshot_hash,
        "backend": "external_import",
        "runtime_identity": "descriptor-safe-import-v1",
        "container_digest": "sha256:" + "0" * 64,
        "checkpoint_sha256": "0" * 64,
        "feature_policy_sha256": canonical_sha256(request["feature_policy"]),
        "expected_cardinality": len(candidates),
        "expected_coordinates": [item["backend_coordinates"] for item in candidates],
        "candidates": candidates,
        "native_manifest_path": "cm_native_artifacts_v1.json",
        "native_manifest_sha256": native_hash,
        "warnings": ["Imported hypotheses retain source provenance and are not model-generated."],
        "omissions": [],
        "terminal_status": "complete",
        "started_at": timestamp,
        "completed_at": timestamp,
        "command": ["descriptor-safe-import-v1"],
        "resume_key": "0" * 64,
        "resumable": False,
        "resume_descriptor": None,
    }
    snapshot_candidate_ids = {item["candidate_id"] for item in snapshot["instance_mappings"]}
    if snapshot_candidate_ids != {item["candidate_id"] for item in candidates}:
        raise ImportStagingError("import snapshot candidate identity mismatch")
    validate_schema("cm_ensemble_v1", ensemble)
    output = Path(output_root)
    if output.exists():
        raise ImportStagingError("canonical output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        if validated_artifact is None:
            raise ImportStagingError("validated import artifact is missing")
        relative_path, artifact_bytes = validated_artifact
        native_artifact = temporary / "native" / relative_path
        native_artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact_fd = os.open(
            native_artifact,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o440,
        )
        try:
            _write_all(artifact_fd, artifact_bytes)
            os.fsync(artifact_fd)
        finally:
            os.close(artifact_fd)
        receipt_output = temporary / "native" / "cm_import_receipt_v1.json"
        receipt_fd = os.open(
            receipt_output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o440,
        )
        try:
            _write_all(receipt_fd, receipt_bytes)
            os.fsync(receipt_fd)
        finally:
            os.close(receipt_fd)
        _atomic_receipt(temporary / "cm_native_artifacts_v1.json", native)
        _atomic_receipt(temporary / "cm_ensemble_v1.json", ensemble)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return native, ensemble

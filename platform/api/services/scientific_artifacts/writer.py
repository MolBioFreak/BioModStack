"""Durable Parquet materialization and integrity verification."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping, Sequence
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from paths import get_data_root
from .contracts import (
    ARTIFACT_ROW_REFERENCE_SCHEMA,
    ARTIFACT_TABLE_SCHEMA,
    artifact_reference,
    canonical_json_bytes,
    require_artifact_reference,
    require_row_reference,
)


class ScientificArtifactError(RuntimeError):
    """Artifact bytes or metadata failed a closed integrity check."""


@dataclass(frozen=True)
class InstalledArtifact:
    artifact_id: str
    owner_kind: str
    owner_id: str
    role: str
    schema_id: str
    schema_version: int
    relative_path: str
    storage_path: Path
    content_sha256: str
    size_bytes: int
    row_count: int
    column_schema_sha256: str
    newly_installed: bool = False
    media_type: str = "application/vnd.apache.parquet"
    transaction_id: str | None = None

    def reference(self) -> dict[str, Any]:
        return artifact_reference(
            artifact_id=self.artifact_id,
            owner_kind=self.owner_kind,
            owner_id=self.owner_id,
            role=self.role,
            schema_id=self.schema_id,
            schema_version=self.schema_version,
            content_sha256=self.content_sha256,
            size_bytes=self.size_bytes,
            row_count=self.row_count,
            relative_path=self.relative_path,
        )


def artifact_root(root: Path | str | None = None) -> Path:
    configured = root or os.getenv("BMS_SCIENTIFIC_ARTIFACT_ROOT")
    return Path(configured).expanduser().resolve() if configured else (get_data_root() / "scientific_artifacts").resolve()


def _schema_digest(schema: pa.Schema) -> str:
    fields = [{"name": field.name, "type": str(field.type), "nullable": field.nullable} for field in schema]
    return hashlib.sha256(canonical_json_bytes(fields)).hexdigest()


def _content_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _lock_path(destination: Path) -> Path:
    return destination.parent / ".publication.lock"


def _ownership_path(destination: Path) -> Path:
    return destination.parent / ".publication-ownership.json"


@contextmanager
def _artifact_lock(destination: Path):
    """Serialize publication and rollback cleanup across API processes."""
    descriptor = os.open(_lock_path(destination), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_ownership(destination: Path) -> dict[str, list[str]]:
    path = _ownership_path(destination)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScientificArtifactError("artifact ownership ledger is unreadable") from exc
    if not isinstance(payload, dict) or any(
        not isinstance(name, str)
        or not isinstance(claims, list)
        or any(not isinstance(claim, str) for claim in claims)
        for name, claims in payload.items()
    ):
        raise ScientificArtifactError("artifact ownership ledger is invalid")
    return payload


def _write_ownership(destination: Path, ownership: dict[str, list[str]]) -> None:
    path = _ownership_path(destination)
    if not ownership:
        if path.exists():
            path.unlink()
            _fsync_directory(path.parent)
        return
    staging = path.with_name(f".{path.name}.staging-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with staging.open("x", encoding="utf-8") as handle:
            json.dump(ownership, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        _fsync_directory(path.parent)
    finally:
        staging.unlink(missing_ok=True)


def _artifact_id(owner_kind: str, owner_id: str, role: str, source_sha256: str) -> str:
    payload = canonical_json_bytes([owner_kind, owner_id, role, source_sha256, ARTIFACT_TABLE_SCHEMA])
    return "sci_" + hashlib.sha256(payload).hexdigest()


def install_parquet_rows(
    *,
    root: Path | str | None,
    owner_kind: str,
    owner_id: str,
    role: str,
    schema_id: str,
    schema_version: int,
    source_sha256: str,
    rows: Iterable[Mapping[str, Any]],
    schema: pa.Schema,
    transaction_id: str | None = None,
) -> InstalledArtifact:
    """Write deterministic Parquet bytes and atomically install one artifact."""
    materialized = [dict(row) for row in rows]
    table = pa.Table.from_pylist(materialized, schema=schema)
    destination_root = artifact_root(root)
    artifact_id = _artifact_id(owner_kind, owner_id, role, source_sha256)
    folder = destination_root / "by-id"
    folder.mkdir(parents=True, exist_ok=True)
    relative_path = str((folder / f"{artifact_id}.parquet").relative_to(destination_root))
    destination = destination_root / relative_path
    staging = destination.with_name(
        f".{destination.name}.staging-{os.getpid()}-{uuid.uuid4().hex}"
    )
    try:
        pq.write_table(
            table,
            staging,
            compression="zstd",
            use_dictionary=True,
            write_statistics=True,
            version="2.6",
        )
        with staging.open("rb") as handle:
            os.fsync(handle.fileno())
        content_sha256, size_bytes = _content_digest(staging)
        publication_claim: str | None = None
        with _artifact_lock(destination):
            ownership = _read_ownership(destination)
            try:
                os.link(staging, destination)
            except FileExistsError:
                existing_sha256, existing_size = _content_digest(destination)
                if (existing_sha256, existing_size) != (content_sha256, size_bytes):
                    raise ScientificArtifactError(
                        f"immutable artifact conflict: {destination}"
                    )
                newly_installed = False
            else:
                newly_installed = True
                _fsync_directory(destination.parent)
            if transaction_id is not None:
                claims = ownership.get(destination.name)
                if newly_installed:
                    claims = []
                    ownership[destination.name] = claims
                if claims is not None:
                    if transaction_id not in claims:
                        claims.append(transaction_id)
                    publication_claim = transaction_id
                    _write_ownership(destination, ownership)
        metadata = pq.read_metadata(destination)
        if metadata.num_rows != len(materialized):
            raise ScientificArtifactError("installed Parquet row count changed during verification")
        verified_sha256, verified_size = _content_digest(destination)
        if (verified_sha256, verified_size) != (content_sha256, size_bytes):
            raise ScientificArtifactError("installed Parquet bytes changed during verification")
        return InstalledArtifact(
            artifact_id=artifact_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            role=role,
            schema_id=schema_id,
            schema_version=int(schema_version),
            relative_path=relative_path,
            storage_path=destination,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            row_count=len(materialized),
            column_schema_sha256=_schema_digest(pq.read_schema(destination)),
            newly_installed=newly_installed,
            transaction_id=publication_claim,
        )
    finally:
        staging.unlink(missing_ok=True)


def finalize_artifact_publication(
    artifact: InstalledArtifact, *, committed: bool
) -> bool:
    """Resolve one transaction claim and delete only an unowned exact artifact."""
    if artifact.transaction_id is None:
        return False
    path = artifact.storage_path
    with _artifact_lock(path):
        ownership = _read_ownership(path)
        claims = ownership.get(path.name)
        if claims is None or artifact.transaction_id not in claims:
            return False
        claims.remove(artifact.transaction_id)
        if committed:
            ownership.pop(path.name, None)
            _write_ownership(path, ownership)
            return False
        if claims:
            _write_ownership(path, ownership)
            return False
        ownership.pop(path.name, None)
        if path.is_symlink() or not path.is_file():
            _write_ownership(path, ownership)
            return False
        if _content_digest(path) != (artifact.content_sha256, artifact.size_bytes):
            _write_ownership(path, ownership)
            return False
        path.unlink()
        _write_ownership(path, ownership)
        _fsync_directory(path.parent)
        return True


def guarded_delete_new_artifact(artifact: InstalledArtifact) -> bool:
    """Backward-compatible rollback finalizer for a publication claim."""
    return finalize_artifact_publication(artifact, committed=False)


def verify_artifact(artifact: InstalledArtifact | Mapping[str, Any], *, root: Path | str | None = None) -> Path:
    with verified_artifact_snapshot(artifact, root=root):
        pass
    relative_path, _expected_sha, _expected_size = _artifact_receipt_identity(artifact)
    return artifact_root(root) / relative_path


def _artifact_receipt_identity(
    artifact: InstalledArtifact | Mapping[str, Any],
) -> tuple[str, str, int]:
    if isinstance(artifact, InstalledArtifact):
        relative_path = artifact.relative_path
        expected_sha = artifact.content_sha256
        expected_size = artifact.size_bytes
    else:
        if artifact.get("schema") == ARTIFACT_ROW_REFERENCE_SCHEMA:
            reference = require_row_reference(artifact)
        else:
            reference = require_artifact_reference(artifact)
        relative_path = str(reference["relative_path"])
        expected_sha = str(reference["content_sha256"])
        expected_size = int(reference["size_bytes"])
    return relative_path, expected_sha, expected_size


@contextmanager
def verified_artifact_snapshot(
    artifact: InstalledArtifact | Mapping[str, Any],
    *,
    root: Path | str | None = None,
):
    """Yield a receipt-verified descriptor path without following any component."""
    relative_path, expected_sha, expected_size = _artifact_receipt_identity(artifact)
    base = artifact_root(root)
    relative = Path(relative_path)
    if (
        not relative_path
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ScientificArtifactError("artifact path escapes managed root")
    descriptors: list[int] = []
    try:
        current = os.open(
            base,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(current)
        leaf = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=current,
        )
        descriptors.append(leaf)
        metadata = os.fstat(leaf)
        if not stat.S_ISREG(metadata.st_mode):
            raise ScientificArtifactError("artifact is missing or is not a regular verified file")
        if metadata.st_size != expected_size:
            raise ScientificArtifactError("artifact bytes do not match its receipt")
        digest = hashlib.sha256()
        os.lseek(leaf, 0, os.SEEK_SET)
        while True:
            block = os.read(leaf, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        os.lseek(leaf, 0, os.SEEK_SET)
        if digest.hexdigest() != expected_sha:
            raise ScientificArtifactError("artifact bytes do not match its receipt")
        yield Path(f"/proc/self/fd/{leaf}")
    except OSError as exc:
        raise ScientificArtifactError("artifact path contains a symlink or unsafe component") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_rows(artifact: InstalledArtifact | Mapping[str, Any], *, root: Path | str | None = None, max_rows: int = 1_000_000) -> list[dict[str, Any]]:
    with verified_artifact_snapshot(artifact, root=root) as path:
        table = pq.read_table(path)
        if table.num_rows > max_rows:
            raise ScientificArtifactError("artifact read exceeds configured row limit")
        return [dict(row) for row in table.to_pylist()]

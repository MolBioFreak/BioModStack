"""Durable Parquet materialization and integrity verification."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

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
    staging = destination.with_name(f".{destination.name}.staging-{os.getpid()}")
    if staging.exists():
        staging.unlink()
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
        newly_installed = not destination.exists()
        if not newly_installed:
            existing_sha256, existing_size = _content_digest(destination)
            if (existing_sha256, existing_size) != (content_sha256, size_bytes):
                raise ScientificArtifactError(f"immutable artifact conflict: {destination}")
            staging.unlink()
        else:
            os.replace(staging, destination)
            _fsync_directory(destination.parent)
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
        )
    finally:
        staging.unlink(missing_ok=True)


def guarded_delete_new_artifact(artifact: InstalledArtifact) -> bool:
    """Delete only bytes installed here and still matching their receipt."""
    if not artifact.newly_installed:
        return False
    path = artifact.storage_path
    root = artifact_root()
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return False
    if path.is_symlink() or not path.is_file():
        return False
    if _content_digest(path) != (artifact.content_sha256, artifact.size_bytes):
        return False
    path.unlink()
    _fsync_directory(path.parent)
    return True


def verify_artifact(artifact: InstalledArtifact | Mapping[str, Any], *, root: Path | str | None = None) -> Path:
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
    base = artifact_root(root)
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ScientificArtifactError("artifact path escapes managed root") from exc
    if path.is_symlink() or not path.is_file():
        raise ScientificArtifactError("artifact is missing or is not a regular file")
    observed_sha, observed_size = _content_digest(path)
    if (observed_sha, observed_size) != (expected_sha, expected_size):
        raise ScientificArtifactError("artifact bytes do not match its receipt")
    return path


def read_rows(artifact: InstalledArtifact | Mapping[str, Any], *, root: Path | str | None = None, max_rows: int = 1_000_000) -> list[dict[str, Any]]:
    path = verify_artifact(artifact, root=root)
    table = pq.read_table(path)
    if table.num_rows > max_rows:
        raise ScientificArtifactError("artifact read exceeds configured row limit")
    return [dict(row) for row in table.to_pylist()]

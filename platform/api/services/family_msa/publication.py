"""Retain immutable artifact bytes in the existing Experiment artifact store.

The caller owns authorization, source acquisition, transaction commit, result
manifest and publication-intent acknowledgement. A file installed before a DB
rollback is an unreferenced content-addressed blob, not a committed attachment.
Never remove blobs here: other projects may reference the same content.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol


class _Readable(Protocol):
    def read(self, n: int) -> bytes: ...

from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentArtifact, ExperimentArtifactBlob, ExperimentResource
from experiment_operations import _controlled_root, _safe_child
from experiment_services import _resource, canonical_json, now


class CustodyError(Exception):
    """Retained bytes are missing, corrupt, or disagree with their authority."""


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _root() -> Path:
    return _controlled_root("BMS_EXPERIMENT_ARTIFACT_ROOT", "experiment-artifacts")


def install_blob(source: _Readable, *, expected_sha256: str | None = None,
                 expected_size: int | None = None) -> tuple[str, int, str]:
    """Atomically install bytes; verify both an optional source authority and reuse.

    `source` must already have been resolved and authorized by the caller; this
    helper never accepts a client-supplied path or URL. Same-directory linking
    prevents one writer replacing another's immutable content.
    """
    root = _root()
    staging = root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="family-msa-", dir=staging)
    temp = Path(temp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as dest:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        sha = digest.hexdigest()
        if (expected_sha256 is not None and expected_sha256 != sha) or (
            expected_size is not None and expected_size != size
        ):
            raise CustodyError("source bytes disagree with their recorded identity")
        key = f"sha256/{sha[:2]}/{sha}"
        target = _safe_child(root, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(temp, target)
        except FileExistsError:
            pass
        if _digest_file(target) != (sha, size):
            raise CustodyError("retained blob does not match its identity")
        dir_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return sha, size, key
    finally:
        temp.unlink(missing_ok=True)


async def retain_artifact(
    session: AsyncSession, *, source: BinaryIO, workspace_id: str,
    lifecycle_owner_id: str, logical_role: str, logical_key: str,
    schema_name: str, schema_version: str, media_type: str,
    provenance: dict, expected_sha256: str | None = None,
    expected_size: int | None = None, resource_id: str | None = None,
) -> ExperimentArtifact:
    """Install and catalog a PM-owned artifact in the caller's transaction.

    A stable resource_id permits retry after an uncertain commit; conflicting
    replay fails instead of replacing an existing artifact. The owner and
    workspace must be checked by the existing PM authorization path first.
    """
    installed = await asyncio.to_thread(
        install_blob, source, expected_sha256=expected_sha256, expected_size=expected_size
    )
    return await _catalog_artifact(session, installed=installed, workspace_id=workspace_id,
        lifecycle_owner_id=lifecycle_owner_id, logical_role=logical_role, logical_key=logical_key,
        schema_name=schema_name, schema_version=schema_version, media_type=media_type,
        provenance=provenance, resource_id=resource_id)


async def _catalog_artifact(session: AsyncSession, *, installed: tuple[str, int, str],
    workspace_id: str, lifecycle_owner_id: str, logical_role: str, logical_key: str,
    schema_name: str, schema_version: str, media_type: str, provenance: dict,
    resource_id: str | None) -> ExperimentArtifact:
    sha, size, key = installed
    # Acquire the catalog row before any reads. SQLite serializes competing
    # writers here; reading first can leave a stale snapshot that cannot upgrade
    # to a write transaction. The uniqueness constraint arbitrates other DBs.
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise CustodyError(f"unsupported artifact catalog dialect: {dialect}")
    await session.execute(
        insert(ExperimentArtifactBlob).values(
            sha256=sha, size_bytes=size, media_type=media_type,
            storage_key=key, state="present", verified_at=now(), created_at=now(),
        ).on_conflict_do_nothing(index_elements=["sha256"])
    )
    blob = await session.get(ExperimentArtifactBlob, sha)
    if blob is None or blob.size_bytes != size or blob.storage_key != key:
        raise CustodyError("blob catalog conflicts with retained bytes")
    blob.state = "present"
    blob.verified_at = now()
    # The shared blob's media type cannot identify two artifacts with identical
    # bytes but different representations. Bind it to each artifact instead.
    identity_json = canonical_json({"source": provenance, "media_type": media_type})
    existing = await session.get(ExperimentArtifact, resource_id) if resource_id else None
    if existing is not None:
        resource = await session.get(ExperimentResource, resource_id)
        if (resource is None or resource.workspace_id != workspace_id
                or resource.lifecycle_owner_id != lifecycle_owner_id
                or existing.blob_sha256 != sha or existing.logical_role != logical_role
                or existing.logical_key != logical_key or existing.schema_name != schema_name
                or existing.schema_version != schema_version
                or (existing.provenance_json != identity_json and not (
                    existing.provenance_json == canonical_json(provenance)
                    and blob.media_type == media_type
                ))):
            raise CustodyError("artifact replay conflicts with existing identity")
        return existing
    resource = await _resource(session, kind="artifact", workspace_id=workspace_id,
                               lifecycle_owner_id=lifecycle_owner_id, resource_id=resource_id)
    artifact = ExperimentArtifact(resource_id=resource.id, blob_sha256=sha,
                                  logical_role=logical_role, logical_key=logical_key,
                                  schema_name=schema_name, schema_version=schema_version,
                                  provenance_json=identity_json)
    session.add(artifact)
    await session.flush()
    return artifact


async def retained_path(session: AsyncSession, artifact: ExperimentArtifact) -> Path:
    """Resolve a previously authorized artifact, verifying bytes on read."""
    blob = await session.get(ExperimentArtifactBlob, artifact.blob_sha256)
    if blob is None or blob.state != "present":
        raise CustodyError("retained artifact is unavailable")
    path = _safe_child(_root(), blob.storage_key)
    try:
        actual = await asyncio.to_thread(_digest_file, path)
    except FileNotFoundError as exc:
        raise CustodyError("retained artifact is unavailable") from exc
    if actual != (blob.sha256, blob.size_bytes):
        raise CustodyError("retained artifact bytes are corrupt")
    return path


def _install_job_blob(path_value, expected_sha256, expected_size, root):
    """Open beneath the physical root and verify the exact streamed snapshot."""
    from services.global_experiments.adapters import (
        AdapterError, _open_canonical_root, _open_directory_at, _same_file_identity, _sha256,
    )
    digest = _sha256(expected_sha256, "artifact sha256") if expected_sha256 is not None else None
    if not isinstance(path_value, str) or not path_value.strip() or path_value != path_value.strip():
        raise AdapterError("source_contract_invalid", "native artifact path is unavailable")
    root = Path(root).expanduser()
    candidate = Path(path_value).expanduser()
    try:
        relative = candidate.relative_to(root) if candidate.is_absolute() else candidate
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("native artifact path traverses its canonical root")
        directory_fd = _open_canonical_root(root)
        try:
            for component in relative.parts[:-1]:
                child = _open_directory_at(directory_fd, component)
                os.close(directory_fd)
                directory_fd = child
            name = relative.parts[-1]
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(observed.st_mode):
                raise OSError("native artifact is not a regular no-follow file")
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                         getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        try:
            opened = os.fstat(fd)
            if not _same_file_identity(observed, opened) or not stat.S_ISREG(opened.st_mode):
                raise OSError("native artifact changed while it was opened")
            if expected_size is not None and (isinstance(expected_size, bool) or
                    not isinstance(expected_size, int) or expected_size != opened.st_size):
                raise AdapterError("source_digest_mismatch", "native artifact size does not match authority")

            class VerifiedStream:
                count = 0
                hasher = hashlib.sha256()

                def read(self, n):
                    chunk = os.read(fd, n)
                    self.count += len(chunk)
                    self.hasher.update(chunk)
                    if not chunk:
                        after = os.fstat(fd)
                        if (not _same_file_identity(opened, after) or after.st_size != opened.st_size
                                or after.st_mtime_ns != opened.st_mtime_ns
                                or after.st_ctime_ns != opened.st_ctime_ns or self.count != opened.st_size):
                            raise AdapterError("source_artifact_unavailable", "native artifact changed during verification")
                        if digest is not None and self.hasher.hexdigest() != digest:
                            raise AdapterError("source_digest_mismatch", "native artifact digest does not match authority")
                    return chunk

            return install_blob(VerifiedStream(), expected_sha256=digest, expected_size=expected_size)
        finally:
            os.close(fd)
    except AdapterError:
        raise
    except (OSError, ValueError) as exc:
        raise AdapterError("source_artifact_unavailable", "native artifact could not be verified") from exc


async def retain_job_artifact(
    core_session: AsyncSession, experiment_session: AsyncSession, *,
    job_id: str, artifact_id: str, workspace_id: str, lifecycle_owner_id: str,
    resource_id: str, logical_role: str, schema_name: str, schema_version: str,
) -> ExperimentArtifact:
    """Copy an authorized JobArtifact into PM custody in the caller's transaction.

    Authorization, selection of the native result manifest and its full artifact
    set, receipt/lineage creation, and commit belong to the caller. This primitive
    does not change the Job or imply that a complete result was attached.
    """
    from database import Job, JobArtifact
    from services.job_result_roots import resolve_persisted_job_result_root

    job = await core_session.get(Job, job_id)
    source = await core_session.get(JobArtifact, artifact_id)
    if job is None or source is None or source.owner_job_id != job_id:
        raise CustodyError("JobArtifact is not owned by the selected Job")
    root = resolve_persisted_job_result_root(job)
    installed = await asyncio.to_thread(
        _install_job_blob, source.storage_path, source.sha256, source.bytes, root,
    )
    return await _catalog_artifact(
        experiment_session, installed=installed, workspace_id=workspace_id,
        lifecycle_owner_id=lifecycle_owner_id, resource_id=resource_id,
        logical_role=logical_role, logical_key=source.logical_path,
        schema_name=schema_name, schema_version=schema_version,
        media_type=source.media_type,
        provenance={"job_id": job_id, "job_artifact_id": artifact_id,
                    "attempt": source.attempt},
    )

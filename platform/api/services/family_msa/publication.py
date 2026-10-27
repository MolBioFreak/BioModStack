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
import tempfile
from pathlib import Path
from typing import BinaryIO

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


def install_blob(source: BinaryIO, *, expected_sha256: str | None = None,
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
    sha, size, key = await asyncio.to_thread(
        install_blob, source, expected_sha256=expected_sha256, expected_size=expected_size
    )
    existing = await session.get(ExperimentArtifact, resource_id) if resource_id else None
    if existing is not None:
        resource = await session.get(ExperimentResource, resource_id)
        if (resource is None or resource.workspace_id != workspace_id
                or resource.lifecycle_owner_id != lifecycle_owner_id
                or existing.blob_sha256 != sha or existing.logical_role != logical_role
                or existing.logical_key != logical_key or existing.schema_name != schema_name
                or existing.schema_version != schema_version
                or existing.provenance_json != canonical_json(provenance)):
            raise CustodyError("artifact replay conflicts with existing identity")
        return existing
    blob = await session.get(ExperimentArtifactBlob, sha)
    if blob is None:
        blob = ExperimentArtifactBlob(sha256=sha, size_bytes=size, media_type=media_type,
                                      storage_key=key, state="present", verified_at=now())
        session.add(blob)
        await session.flush()
    elif blob.size_bytes != size or blob.storage_key != key:
        raise CustodyError("blob catalog conflicts with retained bytes")
    else:
        blob.state = "present"
        blob.verified_at = now()
    resource = await _resource(session, kind="artifact", workspace_id=workspace_id,
                               lifecycle_owner_id=lifecycle_owner_id, resource_id=resource_id)
    artifact = ExperimentArtifact(resource_id=resource.id, blob_sha256=sha,
                                  logical_role=logical_role, logical_key=logical_key,
                                  schema_name=schema_name, schema_version=schema_version,
                                  provenance_json=canonical_json(provenance))
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

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database import JobArtifact, MdAttemptSegment, MdReplicaRun, MdRun
from services.md.results import (
    MDJobRecord, MDResultError, _load_inventory, apply_completion_barrier, completion_barrier,
)


_ARTIFACT_NAMESPACE = uuid.UUID("c87eb7cb-9684-470a-9b63-10b9500beef1")


async def _ingest_durable_artifacts(job: MDJobRecord, session: Any) -> None:
    root, _aggregate, inventory = _load_inventory(job)
    replicas = list((await session.scalars(
        select(MdReplicaRun).where(MdReplicaRun.md_job_id == job.id)
    )).all())
    replicas_by_index = {item.replica_index: item for item in replicas}
    segments = list((await session.scalars(
        select(MdAttemptSegment)
        .join(MdReplicaRun, MdReplicaRun.id == MdAttemptSegment.replica_run_id)
        .where(MdReplicaRun.md_job_id == job.id)
        .order_by(MdAttemptSegment.replica_run_id, MdAttemptSegment.segment_index.desc())
    )).all())
    latest_segment: dict[str, MdAttemptSegment] = {}
    for segment in segments:
        latest_segment.setdefault(segment.replica_run_id, segment)

    id_by_artifact = {
        item.artifact_id: str(uuid.uuid5(
            _ARTIFACT_NAMESPACE,
            f"{job.id}:{item.replica_index}:{item.path.relative_to(root).as_posix()}:{item.sha256}",
        ))
        for item in inventory
    }
    trajectories = {
        (item.replica_index, item.sha256): item
        for item in inventory if item.semantic_role == "analysis_trajectory"
    }
    existing_rows = list((await session.scalars(
        select(JobArtifact).where(JobArtifact.owner_job_id.in_([
            replica.child_job_id for replica in replicas if replica.child_job_id
        ]))
    )).all()) if replicas else []
    existing = {(row.owner_job_id, row.attempt, row.logical_path): row for row in existing_rows}

    for item in inventory:
        replica = replicas_by_index.get(item.replica_index)
        segment = latest_segment.get(replica.id) if replica is not None else None
        if replica is None or not replica.child_job_id or segment is None:
            raise MDResultError(
                "MD_ARTIFACT_PROVENANCE_INVALID",
                f"MD artifact replica {item.replica_index} lacks durable attempt/segment lineage",
                409,
            )
        logical_path = item.path.relative_to(root).as_posix()
        if item.bytes <= 0:
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD artifacts must be non-empty", 409)
        sources: list[dict[str, str]] = []
        if item.semantic_role == "trajectory_frame_map":
            source_digest = item.source_trajectory_sha256
            source = trajectories.get((item.replica_index, source_digest)) if source_digest else None
            if source is None:
                raise MDResultError(
                    "MD_ARTIFACT_PROVENANCE_INVALID",
                    "MD trajectory frame map lacks its exact trajectory source",
                    409,
                )
            sources.append({"artifact_id": id_by_artifact[source.artifact_id], "sha256": source.sha256})
        semantic_role = item.semantic_role or f"md_artifact:{item.name}"
        provenance = {
            "schema": "bms.md.artifact-provenance.v1",
            "md_job_id": job.id,
            "replica_run_id": replica.id,
            "segment_id": segment.id,
            "semantic_role": semantic_role,
            "sources": sources,
        }
        key = (replica.child_job_id, replica.attempt, logical_path)
        prior = existing.get(key)
        expected = (
            id_by_artifact[item.artifact_id], item.sha256, item.bytes,
            str(item.path), provenance,
        )
        if prior is not None:
            actual = (prior.id, prior.sha256, prior.bytes, prior.storage_path, prior.provenance)
            if actual != expected:
                raise MDResultError(
                    "MD_COMPLETION_CONFLICT",
                    f"Durable MD artifact replay conflicts at {logical_path}",
                    409,
                )
            continue
        media_type = mimetypes.guess_type(Path(logical_path).name)[0] or "application/octet-stream"
        session.add(JobArtifact(
            id=expected[0], owner_job_id=replica.child_job_id, attempt=replica.attempt,
            logical_path=logical_path, storage_path=str(item.path), sha256=item.sha256,
            bytes=item.bytes, media_type=media_type, provenance=provenance,
        ))
    await session.flush()


def validate_md_completion(job: MDJobRecord) -> dict[str, Any]:
    """Validate the complete immutable MD generation without mutating job state."""

    return completion_barrier(job)


async def validate_and_finalize_md_job(job: MDJobRecord, session: Any) -> dict[str, Any]:
    """Apply the MD-specific terminal barrier to the caller's current DB transaction."""

    snapshot = apply_completion_barrier(job)
    await _ingest_durable_artifacts(job, session)
    run = await session.get(MdRun, job.id)
    if run is None:
        raise MDResultError("MD_COMPLETION_CONFLICT", "Authoritative MD run state is missing", 409)
    run.phase = "completed"
    run.verification_status = "verified"
    run.state_version += 1
    run.controls_blocked = False
    return snapshot

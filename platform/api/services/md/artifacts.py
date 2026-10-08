"""Fail-closed durable provenance projection for MD-owned artifacts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, NoReturn, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdReplicaRun

SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROVENANCE_SCHEMA = "bms.md.artifact-provenance.v1"


class MdArtifactProvenanceError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.code = "MD_ARTIFACT_PROVENANCE_INVALID"


@dataclass(frozen=True)
class DurableArtifactProjection:
    payload: dict[str, Any]
    checkpoint_artifact_ids: dict[str, str]


def _invalid(message: str) -> NoReturn:
    raise MdArtifactProvenanceError(message)


def _relative_logical_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return path.as_posix() == value and all(part not in {"", ".", ".."} for part in path.parts)


async def project_durable_md_artifacts(
    session: AsyncSession,
    *,
    job_id: str,
    replicas: Sequence[MdReplicaRun],
    segments: Sequence[MdAttemptSegment],
    checkpoints: Sequence[MdCheckpoint],
) -> DurableArtifactProjection:
    """Project only artifacts with exact run/attempt/segment/source bindings.

    Legacy MD jobs have no ``MdRun`` and never call this function. For a durable
    run, an empty artifact ledger remains explicitly absent; any present but
    incomplete ledger fails closed instead of being reported as provenance.
    """
    jobs = list((await session.scalars(
        select(Job).where(or_(Job.id == job_id, Job.parent_job_id == job_id))
    )).all())
    jobs_by_id = {item.id: item for item in jobs}
    artifacts = list((await session.scalars(
        select(JobArtifact)
        .where(JobArtifact.owner_job_id.in_(jobs_by_id) if jobs_by_id else False)
        .order_by(JobArtifact.created_at, JobArtifact.id)
    )).all())
    if not artifacts:
        return DurableArtifactProjection(
            payload={
                "schema": "bms.md.artifact-provenance-set.v1",
                "status": "absent",
                "artifacts": [],
            },
            checkpoint_artifact_ids={},
        )

    replicas_by_id = {item.id: item for item in replicas}
    segments_by_id = {item.id: item for item in segments}
    checkpoints_by_id = {item.id: item for item in checkpoints}
    artifacts_by_id = {item.id: item for item in artifacts}
    if len(artifacts_by_id) != len(artifacts):
        _invalid("durable MD artifact identities are not unique")

    rows: list[dict[str, Any]] = []
    checkpoint_artifact_ids: dict[str, str] = {}
    source_ids_by_artifact: dict[str, list[str]] = {}
    for artifact in artifacts:
        provenance = artifact.provenance
        if not isinstance(provenance, Mapping) or provenance.get("schema") != PROVENANCE_SCHEMA:
            _invalid(f"artifact {artifact.id} lacks the durable MD provenance contract")
        if provenance.get("md_job_id") != job_id:
            _invalid(f"artifact {artifact.id} is bound to another MD run")
        if not _relative_logical_path(artifact.logical_path):
            _invalid(f"artifact {artifact.id} has a non-portable logical path")
        if (
            not isinstance(artifact.sha256, str)
            or SHA256.fullmatch(artifact.sha256) is None
            or isinstance(artifact.bytes, bool)
            or not isinstance(artifact.bytes, int)
            or artifact.bytes <= 0
        ):
            _invalid(f"artifact {artifact.id} has invalid immutable content identity")

        replica_id = provenance.get("replica_run_id")
        segment_id = provenance.get("segment_id")
        role = provenance.get("semantic_role")
        replica = replicas_by_id.get(replica_id) if isinstance(replica_id, str) else None
        segment = segments_by_id.get(segment_id) if isinstance(segment_id, str) else None
        if replica is None or segment is None or segment.replica_run_id != replica.id:
            _invalid(f"artifact {artifact.id} has foreign or incomplete replica/segment lineage")
        owner = jobs_by_id.get(artifact.owner_job_id)
        if owner is None:
            _invalid(f"artifact {artifact.id} owner is outside the MD job lineage")
        if owner.id == replica.child_job_id and artifact.attempt != replica.attempt:
            _invalid(f"artifact {artifact.id} attempt differs from its replica attempt")
        if owner.child_stage == "md_replica" and owner.id != replica.child_job_id:
            _invalid(f"artifact {artifact.id} is owned by a different replica child")
        if not isinstance(role, str) or not role:
            _invalid(f"artifact {artifact.id} has no semantic role")

        raw_sources = provenance.get("sources")
        if not isinstance(raw_sources, list):
            _invalid(f"artifact {artifact.id} source ledger is missing")
        sources: list[dict[str, str]] = []
        seen_sources: set[str] = set()
        for source in raw_sources:
            if not isinstance(source, Mapping) or set(source) != {"artifact_id", "sha256"}:
                _invalid(f"artifact {artifact.id} has a malformed source ledger row")
            source_id, source_sha256 = source.get("artifact_id"), source.get("sha256")
            if (
                not isinstance(source_id, str)
                or source_id == artifact.id
                or source_id in seen_sources
                or not isinstance(source_sha256, str)
                or SHA256.fullmatch(source_sha256) is None
            ):
                _invalid(f"artifact {artifact.id} source identity is invalid")
            seen_sources.add(source_id)
            sources.append({"artifact_id": source_id, "sha256": source_sha256})
        source_ids_by_artifact[artifact.id] = [item["artifact_id"] for item in sources]

        checkpoint_id = provenance.get("checkpoint_id")
        if checkpoint_id is not None:
            if role != "checkpoint":
                _invalid(f"non-checkpoint artifact {artifact.id} carries a checkpoint identity")
            checkpoint = checkpoints_by_id.get(checkpoint_id) if isinstance(checkpoint_id, str) else None
            if (
                checkpoint is None
                or checkpoint.segment_id != segment.id
                or checkpoint.relative_path != artifact.logical_path
                or checkpoint.sha256 != artifact.sha256
                or checkpoint.bytes != artifact.bytes
            ):
                _invalid(f"artifact {artifact.id} checkpoint binding is not exact")
            if checkpoint.id in checkpoint_artifact_ids:
                _invalid(f"checkpoint {checkpoint.id} has more than one durable artifact")
            checkpoint_artifact_ids[checkpoint.id] = artifact.id
        elif role == "checkpoint":
            _invalid(f"checkpoint artifact {artifact.id} has no checkpoint identity")

        rows.append({
            "id": artifact.id,
            "owner_job_id": artifact.owner_job_id,
            "attempt": artifact.attempt,
            "logical_path": artifact.logical_path,
            "sha256": artifact.sha256,
            "bytes": artifact.bytes,
            "media_type": artifact.media_type,
            "semantic_role": role,
            "replica_run_id": replica.id,
            "replica_index": replica.replica_index,
            "segment_id": segment.id,
            "checkpoint_id": checkpoint_id,
            "sources": sources,
        })

    rows_by_id = {row["id"]: row for row in rows}
    for row in rows:
        if row["semantic_role"] == "trajectory_frame_map" and len(row["sources"]) != 1:
            _invalid(f"trajectory frame map {row['id']} requires exactly one trajectory source")
        for source in row["sources"]:
            source_artifact = artifacts_by_id.get(source["artifact_id"])
            if source_artifact is None or source_artifact.sha256 != source["sha256"]:
                _invalid(f"artifact {row['id']} source does not match a durable artifact")
            source_provenance = source_artifact.provenance
            if not isinstance(source_provenance, Mapping) or source_provenance.get("md_job_id") != job_id:
                _invalid(f"artifact {row['id']} source belongs to another MD run")
            if row["semantic_role"] == "trajectory_frame_map":
                source_row = rows_by_id[source_artifact.id]
                if (
                    source_row["semantic_role"] != "analysis_trajectory"
                    or source_row["replica_run_id"] != row["replica_run_id"]
                ):
                    _invalid(f"trajectory frame map {row['id']} source is not its replica trajectory")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visiting:
            _invalid("durable MD artifact source lineage is cyclic")
        if artifact_id in visited:
            return
        visiting.add(artifact_id)
        for source_id in source_ids_by_artifact[artifact_id]:
            visit(source_id)
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in source_ids_by_artifact:
        visit(artifact_id)

    return DurableArtifactProjection(
        payload={
            "schema": "bms.md.artifact-provenance-set.v1",
            "status": "bound",
            "artifacts": rows,
        },
        checkpoint_artifact_ids=checkpoint_artifact_ids,
    )


async def resolve_resume_checkpoint_artifacts(
    session: AsyncSession,
    *,
    job_id: str,
    replicas: Sequence[MdReplicaRun] | None = None,
) -> dict[str, tuple[MdCheckpoint, str]]:
    """Resolve one exact artifact-backed latest checkpoint per paused replica."""
    replica_rows = list(replicas) if replicas is not None else list((await session.scalars(
        select(MdReplicaRun).where(MdReplicaRun.md_job_id == job_id)
    )).all())
    segments = list((await session.scalars(
        select(MdAttemptSegment)
        .join(MdReplicaRun, MdReplicaRun.id == MdAttemptSegment.replica_run_id)
        .where(MdReplicaRun.md_job_id == job_id)
    )).all())
    checkpoints = list((await session.scalars(
        select(MdCheckpoint)
        .join(MdAttemptSegment, MdCheckpoint.segment_id == MdAttemptSegment.id)
        .join(MdReplicaRun, MdReplicaRun.id == MdAttemptSegment.replica_run_id)
        .where(MdReplicaRun.md_job_id == job_id)
    )).all())
    projection = await project_durable_md_artifacts(
        session, job_id=job_id, replicas=replica_rows, segments=segments, checkpoints=checkpoints,
    )
    segment_replica = {segment.id: segment.replica_run_id for segment in segments}
    paused = [replica for replica in replica_rows if replica.active and replica.state == "paused"]
    resolved: dict[str, tuple[MdCheckpoint, str]] = {}
    for replica in paused:
        candidates = [
            checkpoint for checkpoint in checkpoints
            if checkpoint.accepted and segment_replica.get(checkpoint.segment_id) == replica.id
        ]
        candidates.sort(key=lambda item: (float(item.time_ps), item.created_at, item.id), reverse=True)
        if not candidates:
            _invalid(f"paused replica {replica.id} has no accepted checkpoint")
        checkpoint = candidates[0]
        artifact_id = projection.checkpoint_artifact_ids.get(checkpoint.id)
        if artifact_id is None:
            _invalid(f"paused replica {replica.id} latest checkpoint has no exact durable artifact")
        resolved[replica.id] = (checkpoint, artifact_id)
    return resolved

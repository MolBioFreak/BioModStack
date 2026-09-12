from __future__ import annotations

from datetime import datetime
import mimetypes
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database import Job, JobArtifact, MdAttemptSegment, MdReplicaRun, MdRun
from services.md.results import (
    MDJobRecord, MDResultError, _load_inventory, apply_completion_barrier, completion_barrier,
)


_ARTIFACT_NAMESPACE = uuid.UUID("c87eb7cb-9684-470a-9b63-10b9500beef1")


async def _ingest_durable_artifacts(job: MDJobRecord, session: Any) -> None:
    if job.params is None:
        raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD requested protocol is missing", 409)
    root, _aggregate, inventory = _load_inventory(job)
    replicas = list((await session.scalars(
        select(MdReplicaRun).where(MdReplicaRun.md_job_id == job.id)
        .order_by(MdReplicaRun.replica_index, MdReplicaRun.attempt.desc())
    )).all())
    replicas_by_index: dict[int, MdReplicaRun] = {}
    for replica in replicas:
        replicas_by_index.setdefault(replica.replica_index, replica)
    replicas = list(replicas_by_index.values())
    if {replica.child_job_id for replica in replicas} != set(_aggregate["lineage"]["child_ids"]):
        raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD result children do not match latest durable replica attempts", 409)
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
    frame_endpoints: dict[int, tuple[int, float, int, float]] = {}
    for item in inventory:
        if item.semantic_role != "trajectory_frame_map":
            continue
        try:
            frame_map = json.loads(item.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map artifact cannot be decoded", 409) from exc
        frames = frame_map.get("frames") if isinstance(frame_map, dict) else None
        if not isinstance(frames, list) or not frames:
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map artifact has no governed frames", 409)
        first, last = frames[0], frames[-1]
        if not isinstance(first, dict) or not isinstance(last, dict):
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map artifact has invalid endpoint records", 409)
        try:
            frame_endpoints[item.replica_index] = (
                int(first["step"]), float(first["time_ps"]),
                int(last["step"]), float(last["time_ps"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map artifact has invalid endpoint values", 409) from exc

    for replica_index, endpoint in frame_endpoints.items():
        replica = replicas_by_index.get(replica_index)
        segment = latest_segment.get(replica.id) if replica is not None else None
        if segment is None:
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map endpoint has no durable segment owner", 409)
        # Frame endpoints describe saved samples of the whole append trajectory,
        # not segment boundaries (a checkpoint may lie between saved frames).
        production = job.params["md_job_spec"]["stages"]["production"]
        end_step = int(production["steps"])
        end_time_ps = end_step * float(production["timestep_fs"]) / 1000.0
        if not (0 <= endpoint[0] <= endpoint[2] <= end_step and 0 <= endpoint[1] <= endpoint[3] <= end_time_ps):
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD frame-map lies outside completed production bounds", 409)
        if ((segment.end_step is not None and segment.end_step != end_step)
                or (segment.end_time_ps is not None and segment.end_time_ps != end_time_ps)):
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD production endpoint conflicts with durable segment state", 409)
        if segment.source_checkpoint_id is None:
            if segment.start_step not in (None, 0) or segment.start_time_ps not in (None, 0.0):
                raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD initial segment has inconsistent start bounds", 409)
            segment.start_step, segment.start_time_ps = 0, 0.0
        elif (segment.start_step is None or segment.start_time_ps is None
                or not 0 <= segment.start_step <= end_step
                or not 0 <= segment.start_time_ps <= end_time_ps):
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD continuation segment lacks valid checkpoint start bounds", 409)
        segment.end_step, segment.end_time_ps = end_step, end_time_ps
        segment.state = "completed"

    # Native manifests may legitimately declare several roles for one file
    # (for example final coordinates also serve as representative structure).
    # JobArtifact owns bytes by logical path, not one row per manifest alias.
    # Keep every declaration in the unchanged native manifest and retain the
    # complete role set in provenance while registering the file only once.
    by_file: dict[tuple[int, str], list[Any]] = {}
    for item in inventory:
        key = (item.replica_index, item.path.relative_to(root).as_posix())
        by_file.setdefault(key, []).append(item)
    for aliases in by_file.values():
        item = aliases[0]
        if any((alias.sha256, alias.bytes, alias.path) != (item.sha256, item.bytes, item.path)
               for alias in aliases):
            raise MDResultError("MD_ARTIFACT_PROVENANCE_INVALID", "MD file aliases have conflicting byte identity", 409)
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
        if len(aliases) > 1:
            provenance["semantic_roles"] = sorted({
                alias.semantic_role or f"md_artifact:{alias.name}" for alias in aliases
            })
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
    # Require the exact current native children after ALL replica/analysis
    # contracts passed. Shared finalize_component_projection owns host promotion
    # after the caller marks this parent completed in the same transaction.
    for key, stage in (('replica_child_ids', 'md_replica'), ('analysis_child_ids', 'md_analysis')):
        for child_id in snapshot[key]:
            child = await session.get(Job, child_id)
            if child is None or child.parent_job_id != job.id or child.child_stage != stage:
                raise MDResultError('MD_COMPLETION_CONFLICT', 'Native completion child projection is missing', 409)
            control = (child.provenance or {}).get('component_projection')
            if control is not None:
                if (control.get('root_job_id') != job.id
                        or control.get('state') != 'completed'
                        or not (control.get('result') or {}).get('references')):
                    raise MDResultError('MD_COMPLETION_CONFLICT', 'Native completion lacks validated component evidence', 409)
    # Close the exact accepted replicas and their latest segments now, not on a
    # later background reconciliation. Historical failed attempts stay untouched.
    replicas = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job.id,
        MdReplicaRun.child_job_id.in_(snapshot['replica_child_ids']),
    ))).all())
    if {replica.child_job_id for replica in replicas} != set(snapshot['replica_child_ids']):
        raise MDResultError('MD_COMPLETION_CONFLICT', 'Native completion replica lineage is incomplete', 409)
    completed_at = getattr(job, 'completed_at', None) or datetime.utcnow()
    for replica in replicas:
        if replica.state in {'failed', 'cancelled', 'orphaned'}:
            raise MDResultError('MD_COMPLETION_CONFLICT', 'Native completion would rewrite a failed replica', 409)
        replica.state = 'completed'
        replica.active = False
        replica.completed_at = replica.completed_at or completed_at
        segment = await session.scalar(select(MdAttemptSegment).where(
            MdAttemptSegment.replica_run_id == replica.id,
        ).order_by(MdAttemptSegment.segment_index.desc()).limit(1))
        if segment is None:
            raise MDResultError('MD_COMPLETION_CONFLICT', 'Native completion segment is missing', 409)
        segment.state = 'completed'
        segment.completed_at = segment.completed_at or completed_at
    run = await session.get(MdRun, job.id)
    if run is None:
        raise MDResultError("MD_COMPLETION_CONFLICT", "Authoritative MD run state is missing", 409)
    if run.phase != "completed" or run.verification_status != "verified":
        run.state_version += 1
    run.phase = "completed"
    run.verification_status = "verified"
    run.controls_blocked = False
    return snapshot

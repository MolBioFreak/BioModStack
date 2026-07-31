"""Bounded durable MD read model for queue and run-detail surfaces."""
from __future__ import annotations

from collections import Counter, defaultdict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, MdAttemptSegment, MdCheckpoint, MdEvent, MdReplicaRun, MdRun
from services.md.state import RETRYABLE_INFRASTRUCTURE_FAILURES
from services.md.artifacts import (
    MdArtifactProvenanceError, project_durable_md_artifacts, resolve_resume_checkpoint_artifacts,
)
from services.md.pause_actuator import _checkpoint_roots


def _actions(phase: str, has_checkpoint: bool, retryable: bool, *, pause_ready: bool = False) -> list[str]:
    actions: list[str] = []
    if phase == "replicas_running" and pause_ready:
        actions.append("pause")
    if phase == "paused" and has_checkpoint:
        actions.append("resume_dynamics")
    if phase in {"failed", "partial", "reconciling"} and retryable:
        actions.append("retry_dynamics")
    if phase not in {"completed", "partial", "failed", "cancelled", "cancelling"}:
        actions.append("cancel")
    return actions


def _worker_pause_ready(child: Job | None) -> bool:
    if child is None or not child.nextflow_run_id or not child.stage_work_dir:
        return False
    try:
        roots = _checkpoint_roots(child)
    except (OSError, TypeError, ValueError):
        return False
    return any(
        (root / "production" / "production.cpt").is_file()
        and not (root / "production" / "production.cpt").is_symlink()
        for root in roots
    )


def _replica_count(request: dict, fallback: int) -> int:
    replicas = request.get("replicas")
    if isinstance(replicas, dict):
        value = replicas.get("count")
    else:
        value = replicas
    return int(value) if isinstance(value, int) and value >= 0 else fallback


def _requested_time_ps(request: dict) -> float:
    durations = request.get("protocol", {}).get("stage_durations_ps", {})
    if isinstance(durations, dict) and durations:
        return sum(float(value) for value in durations.values() if isinstance(value, (int, float)))
    stages = request.get("stages")
    if not isinstance(stages, dict):
        return 0.0
    production = stages.get("production")
    if not isinstance(production, dict):
        return 0.0
    timestep_fs = production.get("timestep_fs")
    if isinstance(timestep_fs, bool) or not isinstance(timestep_fs, (int, float)):
        return 0.0
    dynamic_steps = 0
    for stage_name in ("nvt", "npt", "production"):
        stage = stages.get(stage_name)
        if not isinstance(stage, dict) or stage.get("enabled") is not True:
            continue
        steps = stage.get("steps")
        if not isinstance(steps, bool) and isinstance(steps, int) and steps >= 0:
            dynamic_steps += steps
    return float(dynamic_steps) * float(timestep_fs) / 1000.0


async def md_queue_snapshot(session: AsyncSession, *, limit: int) -> dict:
    """Return a hard-limited MD-only operational projection without detail collections."""
    rows = list((await session.execute(
        select(MdRun, Job)
        .join(Job, Job.id == MdRun.job_id)
        .order_by(MdRun.updated_at.desc(), MdRun.job_id)
        .limit(limit)
    )).all())
    job_ids = [run.job_id for run, _job in rows]

    replica_summary: dict[str, Counter[str]] = defaultdict(Counter)
    active_replica_counts: dict[str, int] = defaultdict(int)
    simulated_time_ps: dict[str, float] = {}
    retryable_jobs: set[str] = set()
    pause_ready_jobs: set[str] = set()
    if job_ids:
        summary_rows = (await session.execute(
            select(MdReplicaRun.md_job_id, MdReplicaRun.state, func.count(MdReplicaRun.id))
            .where(MdReplicaRun.md_job_id.in_(job_ids), MdReplicaRun.active.is_(True))
            .group_by(MdReplicaRun.md_job_id, MdReplicaRun.state)
        )).all()
        for job_id, state, count in summary_rows:
            replica_summary[job_id][state] = int(count)
            active_replica_counts[job_id] += int(count)

        time_rows = (await session.execute(
            select(MdReplicaRun.md_job_id, func.max(MdAttemptSegment.end_time_ps))
            .join(MdAttemptSegment, MdAttemptSegment.replica_run_id == MdReplicaRun.id)
            .where(MdReplicaRun.md_job_id.in_(job_ids))
            .group_by(MdReplicaRun.md_job_id)
        )).all()
        simulated_time_ps = {job_id: float(value or 0.0) for job_id, value in time_rows}


        retryable_rows = list((await session.scalars(
            select(MdReplicaRun)
            .where(
                MdReplicaRun.md_job_id.in_(job_ids),
                MdReplicaRun.active.is_(False),
                MdReplicaRun.state.in_({"failed", "orphaned"}),
            )
        )).all())
        retryable_jobs = {
            item.md_job_id for item in retryable_rows
            if str((item.failure or {}).get("code") or "") in RETRYABLE_INFRASTRUCTURE_FAILURES
        }

        ready_rows = (await session.execute(
            select(MdReplicaRun.md_job_id, func.count(MdReplicaRun.id))
            .join(Job, Job.id == MdReplicaRun.child_job_id)
            .where(
                MdReplicaRun.md_job_id.in_(job_ids),
                MdReplicaRun.active.is_(True),
                MdReplicaRun.state == "running",
                Job.nextflow_run_id.is_not(None),
                Job.stage_work_dir.is_not(None),
            )
            .group_by(MdReplicaRun.md_job_id)
        )).all()
        ready_counts = {job_id: int(count) for job_id, count in ready_rows}
        pause_ready_jobs = {
            job_id for job_id in job_ids
            if replica_summary[job_id].get("running", 0) > 0
            and ready_counts.get(job_id, 0) == replica_summary[job_id].get("running", 0)
            and set(replica_summary[job_id]).issubset({"running", "completed"})
        }
        for job_id in tuple(pause_ready_jobs):
            running = list((await session.scalars(select(MdReplicaRun).where(
                MdReplicaRun.md_job_id == job_id,
                MdReplicaRun.active.is_(True),
                MdReplicaRun.state == "running",
            ))).all())
            children = [
                await session.get(Job, replica.child_job_id) if replica.child_job_id else None
                for replica in running
            ]
            if not children or not all(_worker_pause_ready(child) for child in children):
                pause_ready_jobs.discard(job_id)

    queue_rows = []
    for run, job in rows:
        chemistry = run.normalized_request.get("chemistry", {})
        has_checkpoint = False
        if run.phase == "paused":
            run_replicas = list((await session.scalars(select(MdReplicaRun).where(
                MdReplicaRun.md_job_id == run.job_id,
            ))).all())
            paused_ids = {item.id for item in run_replicas if item.active and item.state == "paused"}
            active_replicas = [item for item in run_replicas if item.active]
            barrier_complete = bool(paused_ids) and all(
                item.state in {"paused", "completed"} for item in active_replicas
            )
            try:
                resolved = await resolve_resume_checkpoint_artifacts(
                    session, job_id=run.job_id, replicas=run_replicas,
                )
                has_checkpoint = barrier_complete and set(resolved) == paused_ids
            except MdArtifactProvenanceError:
                has_checkpoint = False
        queue_rows.append({
            "job_id": run.job_id,
            "name": job.name,
            "job_status": job.status,
            "queue_status": job.queue_status,
            "phase": run.phase,
            "state_version": run.state_version,
            "engine": run.normalized_request.get("engine"),
            "replica_count": _replica_count(
                run.normalized_request, active_replica_counts[run.job_id]
            ),
            "replica_summary": dict(replica_summary[run.job_id]),
            "simulated_time_ps": simulated_time_ps.get(run.job_id, 0.0),
            "requested_time_ps": _requested_time_ps(run.normalized_request),
            "checkpoint_available": has_checkpoint,
            "allowed_actions": _actions(
                run.phase, has_checkpoint, run.job_id in retryable_jobs,
                pause_ready=run.job_id in pause_ready_jobs,
            ),
            "chemistry": {
                "profile_id": run.chemistry_profile_id,
                "assurance": run.chemistry_assurance,
                "verification_status": run.verification_status,
                "requested_scope": chemistry.get("requested_scope"),
            },
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        })
    return {
        "schema": "bms.md.queue.v1",
        "bounded": True,
        "limit": limit,
        "count": len(queue_rows),
        "runs": queue_rows,
    }


async def md_run_snapshot(session: AsyncSession, job_id: str) -> dict | None:
    run = await session.get(MdRun, job_id)
    job = await session.get(Job, job_id)
    if run is None or job is None:
        return None
    replicas = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id
    ).order_by(MdReplicaRun.replica_index, MdReplicaRun.attempt))).all())
    replica_ids = [item.id for item in replicas]
    segments = list((await session.scalars(select(MdAttemptSegment).where(
        MdAttemptSegment.replica_run_id.in_(replica_ids) if replica_ids else False
    ).order_by(MdAttemptSegment.replica_run_id, MdAttemptSegment.segment_index))).all())
    segment_ids = [item.id for item in segments]
    checkpoints = list((await session.scalars(select(MdCheckpoint).where(
        MdCheckpoint.segment_id.in_(segment_ids) if segment_ids else False,
        MdCheckpoint.accepted.is_(True),
    ).order_by(MdCheckpoint.time_ps))).all())
    artifact_projection = await project_durable_md_artifacts(
        session,
        job_id=job_id,
        replicas=replicas,
        segments=segments,
        checkpoints=checkpoints,
    )
    checkpoint_artifact_ids = artifact_projection.checkpoint_artifact_ids
    segment_replica = {item.id: item.replica_run_id for item in segments}
    paused_ids = {item.id for item in replicas if item.active and item.state == "paused"}
    covered_ids: set[str] = set()
    for replica_id in paused_ids:
        candidates = [
            checkpoint for checkpoint in checkpoints
            if segment_replica.get(checkpoint.segment_id) == replica_id
        ]
        candidates.sort(key=lambda item: (float(item.time_ps), item.created_at, item.id), reverse=True)
        if candidates and candidates[0].id in checkpoint_artifact_ids:
            covered_ids.add(replica_id)
    active_replicas = [item for item in replicas if item.active]
    barrier_complete = bool(paused_ids) and all(
        item.state in {"paused", "completed"} for item in active_replicas
    )
    checkpoint_available = barrier_complete and covered_ids == paused_ids
    events = list((await session.scalars(select(MdEvent).where(
        MdEvent.md_job_id == job_id
    ).order_by(MdEvent.created_at.desc()).limit(100))).all())
    summary = Counter(item.state for item in replicas if item.active)
    retryable = any(
        not item.active
        and item.state in {"failed", "orphaned"}
        and str((item.failure or {}).get("code") or "") in RETRYABLE_INFRASTRUCTURE_FAILURES
        for item in replicas
    )
    running_replicas = [item for item in active_replicas if item.state == "running"]
    pause_ready = bool(running_replicas) and all(
        item.state in {"running", "completed"} for item in active_replicas
    )
    if pause_ready:
        for replica in running_replicas:
            child = await session.get(Job, replica.child_job_id) if replica.child_job_id else None
            if not _worker_pause_ready(child):
                pause_ready = False
                break
    chemistry = run.normalized_request.get("chemistry", {})
    requested_ps = _requested_time_ps(run.normalized_request)
    completed_ps = max((float(item.end_time_ps or 0.0) for item in segments), default=0.0)
    return {
        "schema": "bms.md.run-detail.v1",
        "job_id": job_id,
        "job_status": job.status,
        "queue_status": job.queue_status,
        "phase": run.phase,
        "state_version": run.state_version,
        "chemistry": {
            "profile_id": run.chemistry_profile_id,
            "profile_sha256": run.chemistry_profile_sha256,
            "assurance": run.chemistry_assurance,
            "verification_status": run.verification_status,
            "requested_scope": chemistry.get("requested_scope"),
        },
        "engine": run.normalized_request.get("engine"),
        "replica_count": _replica_count(run.normalized_request, len(replicas)),
        "replica_summary": dict(summary),
        "simulated_time_ps": completed_ps,
        "requested_time_ps": requested_ps,
        "checkpoint_available": checkpoint_available,
        "allowed_actions": _actions(
            run.phase, checkpoint_available, retryable, pause_ready=pause_ready,
        ),
        "replicas": [{
            "id": item.id, "replica_index": item.replica_index, "attempt": item.attempt,
            "state": item.state, "active": item.active, "engine": item.engine,
            "failure": item.failure,
            "retry_eligible": (
                not item.active
                and item.state in {"failed", "orphaned"}
                and str((item.failure or {}).get("code") or "") in RETRYABLE_INFRASTRUCTURE_FAILURES
            ),
        } for item in replicas],
        "segments": [{
            "id": item.id, "replica_run_id": item.replica_run_id,
            "segment_index": item.segment_index, "state": item.state,
            "source_segment_id": item.source_segment_id,
            "source_checkpoint_id": item.source_checkpoint_id,
            "start_step": item.start_step, "end_step": item.end_step,
            "start_time_ps": item.start_time_ps, "end_time_ps": item.end_time_ps,
        } for item in segments],
        "checkpoints": [{
            "id": item.id, "segment_id": item.segment_id, "logical_role": item.logical_role,
            "relative_path": item.relative_path, "sha256": item.sha256, "bytes": item.bytes,
            "step": item.step, "time_ps": item.time_ps,
            "artifact_id": checkpoint_artifact_ids.get(item.id),
        } for item in checkpoints],
        "artifact_provenance": artifact_projection.payload,
        "events": [{
            "id": item.id, "event_type": item.event_type,
            "state_version": item.resulting_state_version, "payload": item.payload,
            "created_at": item.created_at,
        } for item in events],
    }

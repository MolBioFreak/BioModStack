"""Authoritative transactional state machine for Molecular Dynamics lifecycles."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdEvent, MdReplicaRun, MdRun,
)
from services.md.artifacts import MdArtifactProvenanceError, resolve_resume_checkpoint_artifacts

TERMINAL_REPLICA_STATES = frozenset({"completed", "failed", "cancelled", "orphaned"})
ACTIVE_REPLICA_STATES = frozenset({"queued", "launching", "running", "checkpointing", "paused", "cancelling"})
TERMINAL_PHASES = frozenset({"completed", "partial", "failed", "cancelled"})
RETRYABLE_INFRASTRUCTURE_FAILURES = frozenset({
    "spawn_rejected", "worker_lost", "scheduler_transient", "runtime_transient",
})


class MdStateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _replay_event(
    session: AsyncSession, *, job_id: str, idempotency_key: str,
    event_type: str, expected_version: int,
) -> MdEvent | None:
    existing = await session.scalar(select(MdEvent).where(MdEvent.idempotency_key == idempotency_key))
    if existing is None:
        return None
    if (
        existing.md_job_id != job_id
        or existing.event_type != event_type
        or existing.expected_state_version != expected_version
    ):
        raise MdStateError("MD_IDEMPOTENCY_CONFLICT", "idempotency key belongs to another operation")
    return existing


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def create_md_run(session: AsyncSession, *, job: Job, normalized_request: dict[str, Any]) -> MdRun:
    if normalized_request.get("schema") != "bms.md.job.v2":
        raise MdStateError("MD_CONTRACT_UNSUPPORTED", "durable lifecycle requires bms.md.job.v2")
    chemistry = normalized_request["chemistry"]
    assurance = chemistry.get("assurance")
    if not isinstance(assurance, str) or not assurance:
        raise MdStateError("MD_CONTRACT_INVALID", "normalized chemistry assurance is missing")
    run = MdRun(
        job_id=job.id,
        normalized_request=normalized_request,
        request_sha256=canonical_sha256(normalized_request),
        phase="validating",
        state_version=0,
        chemistry_profile_id=chemistry["profile_id"],
        chemistry_profile_sha256=chemistry["profile_sha256"],
        chemistry_assurance=assurance,
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise MdStateError("MD_RUN_ALREADY_EXISTS", "MD run already exists") from exc
    return run


async def append_event_cas(
    session: AsyncSession, *, job_id: str, idempotency_key: str, event_type: str,
    expected_version: int, next_phase: str | None = None, payload: dict[str, Any] | None = None,
    block_controls: bool | None = None,
) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "durable MD run not found")
    existing = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type=event_type, expected_version=expected_version,
    )
    if existing is not None:
        return run

    values: dict[str, Any] = {"state_version": expected_version + 1, "updated_at": datetime.utcnow()}
    if next_phase is not None:
        values["phase"] = next_phase
    if block_controls is not None:
        values["controls_blocked"] = block_controls
    result = await session.execute(
        update(MdRun).where(MdRun.job_id == job_id, MdRun.state_version == expected_version).values(**values)
    )
    if result.rowcount != 1:
        raise MdStateError("MD_STATE_VERSION_CONFLICT", "MD state changed; refresh and retry")
    event = MdEvent(
        id=str(uuid.uuid4()), md_job_id=job_id, idempotency_key=idempotency_key,
        event_type=event_type, expected_state_version=expected_version,
        resulting_state_version=expected_version + 1, payload=payload or {},
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise MdStateError("MD_EVENT_CONFLICT", "event idempotency key conflicted") from exc
    return await session.get(MdRun, job_id, populate_existing=True)


async def create_replica_attempt(
    session: AsyncSession, *, job_id: str, replica_index: int, attempt: int, engine: str,
    execution_plan_sha256: str, compatibility_key: str, child_job_id: str | None = None,
) -> tuple[MdReplicaRun, MdAttemptSegment]:
    run = await session.get(MdRun, job_id)
    if run is None or run.controls_blocked or run.phase in TERMINAL_PHASES | {"checkpointing", "cancelling"}:
        raise MdStateError("MD_NEW_SEGMENTS_BLOCKED", "parent does not admit a new replica attempt")
    active = await session.scalar(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id, MdReplicaRun.replica_index == replica_index,
        MdReplicaRun.active.is_(True),
    ))
    if active is not None:
        raise MdStateError("MD_REPLICA_ALREADY_ACTIVE", "replica already has an active attempt")
    replica = MdReplicaRun(
        id=str(uuid.uuid4()), child_job_id=child_job_id, md_job_id=job_id, replica_index=replica_index,
        attempt=attempt, engine=engine, state="queued", active=True,
    )
    segment = MdAttemptSegment(
        id=str(uuid.uuid4()), replica_run_id=replica.id, segment_index=0, state="queued",
        execution_plan_sha256=execution_plan_sha256, compatibility_key=compatibility_key,
    )
    session.add(replica)
    await session.flush()
    session.add(segment)
    await session.flush()
    return replica, segment


async def request_pause(session: AsyncSession, *, job_id: str, expected_version: int, idempotency_key: str) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "MD run was not found")
    existing = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="pause_requested", expected_version=expected_version,
    )
    if existing is not None:
        return run
    if run.phase not in {"replicas_queued", "replicas_running", "checkpointing"}:
        raise MdStateError("MD_PAUSE_UNAVAILABLE", "run cannot be paused in its current phase")
    return await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key, event_type="pause_requested",
        expected_version=expected_version, next_phase="checkpointing", block_controls=True,
    )


async def accept_checkpoint(
    session: AsyncSession, *, segment_id: str, logical_role: str, relative_path: str,
    sha256: str, bytes_: int, step: int, time_ps: float, compatibility_key: str,
) -> MdCheckpoint:
    segment = await session.get(MdAttemptSegment, segment_id)
    if segment is None:
        raise MdStateError("MD_SEGMENT_NOT_FOUND", "checkpoint segment was not found")
    if segment.compatibility_key != compatibility_key:
        raise MdStateError("MD_CHECKPOINT_INCOMPATIBLE", "checkpoint compatibility key differs")
    if len(sha256) != 64 or bytes_ <= 0 or step < 0 or time_ps < 0:
        raise MdStateError("MD_CHECKPOINT_INVALID", "checkpoint metadata is invalid")
    checkpoint = MdCheckpoint(
        id=str(uuid.uuid4()), segment_id=segment_id, logical_role=logical_role,
        relative_path=relative_path, sha256=sha256, bytes=bytes_, step=step,
        time_ps=time_ps, compatibility_key=compatibility_key, accepted=True,
    )
    session.add(checkpoint); await session.flush(); return checkpoint


async def finalize_pause(session: AsyncSession, *, job_id: str, expected_version: int, idempotency_key: str) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "durable MD run not found")
    existing = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="pause_completed", expected_version=expected_version,
    )
    if existing is not None:
        return run
    if run.phase != "checkpointing" or not run.controls_blocked:
        raise MdStateError("MD_PAUSE_TRANSITION_INVALID", "pause can finalize only from checkpointing")
    replicas = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id, MdReplicaRun.active.is_(True)
    ))).all())
    if not replicas or any(item.state not in {"paused", "completed"} for item in replicas):
        raise MdStateError("MD_PAUSE_INCOMPLETE", "all targeted descendants are not checkpointed and stopped")
    paused_ids = {item.id for item in replicas if item.state == "paused"}
    try:
        resolved = await resolve_resume_checkpoint_artifacts(
            session, job_id=job_id, replicas=replicas,
        )
    except MdArtifactProvenanceError as exc:
        raise MdStateError("MD_PAUSE_INCOMPLETE", "exact durable checkpoint artifacts are missing") from exc
    if set(resolved) != paused_ids:
        raise MdStateError("MD_PAUSE_INCOMPLETE", "exact checkpoint coverage is incomplete")
    return await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key, event_type="pause_completed",
        expected_version=expected_version, next_phase="paused", block_controls=False,
    )


async def resume_replica(
    session: AsyncSession, *, job_id: str, replica_run_id: str, checkpoint_id: str,
) -> MdAttemptSegment:
    run = await session.get(MdRun, job_id)
    replica = await session.get(MdReplicaRun, replica_run_id)
    checkpoint = await session.get(MdCheckpoint, checkpoint_id)
    if run is None or run.phase != "paused" or run.controls_blocked or replica is None or checkpoint is None:
        raise MdStateError("MD_RESUME_UNAVAILABLE", "resume preconditions are not met")
    if replica.md_job_id != job_id or not checkpoint.accepted:
        raise MdStateError("MD_CHECKPOINT_INCOMPATIBLE", "checkpoint is not accepted for this lineage")
    source = await session.get(MdAttemptSegment, checkpoint.segment_id)
    if source is None or source.replica_run_id != replica_run_id:
        raise MdStateError("MD_CHECKPOINT_INCOMPATIBLE", "checkpoint belongs to another replica")
    next_index = int((await session.scalar(select(func.max(MdAttemptSegment.segment_index)).where(
        MdAttemptSegment.replica_run_id == replica_run_id))) or 0) + 1
    segment = MdAttemptSegment(
        id=str(uuid.uuid4()), replica_run_id=replica_run_id, segment_index=next_index,
        state="queued", source_segment_id=source.id, source_checkpoint_id=checkpoint.id,
        execution_plan_sha256=source.execution_plan_sha256,
        compatibility_key=source.compatibility_key,
        start_step=checkpoint.step, start_time_ps=checkpoint.time_ps,
    )
    replica.state = "queued"; session.add(segment); await session.flush(); return segment


async def resume_run(
    session: AsyncSession, *, job_id: str, expected_version: int, idempotency_key: str,
) -> list[MdAttemptSegment]:
    existing_event = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="resume_requested", expected_version=expected_version,
    )
    if existing_event is not None:
        segment_ids = [str(value) for value in (existing_event.payload or {}).get("segment_ids", [])]
        segments = list((await session.scalars(select(MdAttemptSegment).where(
            MdAttemptSegment.id.in_(segment_ids)
        ))).all()) if segment_ids else []
        by_id = {segment.id: segment for segment in segments}
        if len(by_id) != len(segment_ids):
            raise MdStateError("MD_STATE_CORRUPT", "resume event has incomplete continuation lineage")
        return [by_id[segment_id] for segment_id in segment_ids]
    run = await session.get(MdRun, job_id)
    if run is None or run.phase != "paused" or run.controls_blocked:
        raise MdStateError("MD_RESUME_UNAVAILABLE", "run is not durably paused")
    active_replicas = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id, MdReplicaRun.active.is_(True),
    ).order_by(MdReplicaRun.replica_index))).all())
    if not active_replicas or any(item.state not in {"paused", "completed"} for item in active_replicas):
        raise MdStateError("MD_RESUME_BARRIER_INCOMPLETE", "all active replicas must be paused or completed")
    replicas = [item for item in active_replicas if item.state == "paused"]
    if not replicas:
        raise MdStateError("MD_RESUME_UNAVAILABLE", "run has no active paused replicas")
    try:
        resolved = await resolve_resume_checkpoint_artifacts(
            session, job_id=job_id, replicas=replicas,
        )
    except MdArtifactProvenanceError as exc:
        raise MdStateError(
            "MD_RESUME_CHECKPOINT_UNVERIFIED",
            "every paused replica requires one exact durable checkpoint artifact",
        ) from exc
    if set(resolved) != {replica.id for replica in replicas}:
        raise MdStateError("MD_RESUME_CHECKPOINT_UNVERIFIED", "checkpoint coverage is incomplete")
    continuations: list[MdAttemptSegment] = []
    for replica in replicas:
        checkpoint, artifact_id = resolved[replica.id]
        continuations.append(await resume_replica(
            session, job_id=job_id, replica_run_id=replica.id, checkpoint_id=checkpoint.id,
        ))
        continuation = continuations[-1]
        if not replica.child_job_id:
            raise MdStateError("MD_RESUME_CHILD_MISSING", "replica scheduler job is missing")
        child = await session.get(Job, replica.child_job_id)
        if child is None:
            raise MdStateError("MD_RESUME_CHILD_MISSING", "replica scheduler job is missing")
        artifact = await session.get(JobArtifact, artifact_id)
        if artifact is None:
            raise MdStateError("MD_RESUME_CHECKPOINT_UNVERIFIED", "checkpoint artifact disappeared")
        child_params = copy.deepcopy(child.params or {})
        storage_path = Path(artifact.storage_path)
        provenance = artifact.provenance if isinstance(artifact.provenance, dict) else {}
        recorded_output_dir = provenance.get("replica_output_dir")
        if isinstance(recorded_output_dir, str) and recorded_output_dir:
            resume_output_dir = Path(recorded_output_dir)
        else:
            relative_parts = Path(checkpoint.relative_path).parts
            worker_root = storage_path
            for _part in relative_parts:
                worker_root = worker_root.parent
            resume_output_dir = worker_root / relative_parts[0]
        child_params.update({
            "md_resume_checkpoint": artifact.storage_path,
            "md_resume_checkpoint_sha256": checkpoint.sha256,
            "md_resume_output_dir": str(resume_output_dir),
            "md_resume_segment_id": continuation.id,
        })
        child.params = child_params
        child.status = "queued"
        child.queue_status = "queued"
        child.paused = False
        child.completed_at = None
        child.error_message = None
    parent = await session.get(Job, job_id)
    if parent is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "parent scheduler job is missing")
    parent.status = "running"
    parent.queue_status = "running"
    parent.paused = False
    parent.completed_at = None
    await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="resume_requested", expected_version=expected_version,
        next_phase="replicas_queued", payload={"segment_ids": [item.id for item in continuations]},
    )
    return continuations


async def retry_replica_attempt(
    session: AsyncSession, *, job_id: str, replica_index: int,
    expected_version: int, idempotency_key: str,
) -> MdReplicaRun:
    existing_event = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="retry_requested", expected_version=expected_version,
    )
    if existing_event is not None:
        replica_id = str((existing_event.payload or {}).get("replica_run_id") or "")
        replay = await session.get(MdReplicaRun, replica_id)
        if replay is None:
            raise MdStateError("MD_STATE_CORRUPT", "retry event has no replica attempt")
        return replay

    run = await session.get(MdRun, job_id)
    if run is None or run.controls_blocked or run.phase not in {"failed", "partial", "reconciling"}:
        raise MdStateError("MD_RETRY_UNAVAILABLE", "run does not admit a dynamics retry")
    previous = await session.scalar(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id, MdReplicaRun.replica_index == replica_index,
    ).order_by(MdReplicaRun.attempt.desc()).limit(1))
    if previous is None or previous.active or previous.state not in {"failed", "orphaned"}:
        raise MdStateError("MD_RETRY_UNAVAILABLE", "replica has no retryable terminal attempt")
    failure_code = str((previous.failure or {}).get("code") or "")
    if failure_code not in RETRYABLE_INFRASTRUCTURE_FAILURES:
        raise MdStateError(
            "MD_RETRY_REVIEW_REQUIRED",
            "only allowlisted infrastructure failures may be retried without scientific review",
        )
    source = await session.scalar(select(MdAttemptSegment).where(
        MdAttemptSegment.replica_run_id == previous.id,
    ).order_by(MdAttemptSegment.segment_index.desc()).limit(1))
    previous_child = await session.get(Job, previous.child_job_id) if previous.child_job_id else None
    parent_job = await session.get(Job, job_id)
    if source is None or previous_child is None or parent_job is None or not parent_job.output_dir:
        raise MdStateError("MD_STATE_CORRUPT", "retry source lineage is incomplete")

    next_attempt = previous.attempt + 1
    parent_output_root = Path(parent_job.output_dir).expanduser().resolve()
    retry_output_dir = (
        parent_output_root
        / "md_retry_attempts"
        / f"replica_{replica_index:03d}"
        / f"attempt_{next_attempt:03d}"
    ).resolve()
    if retry_output_dir == parent_output_root or parent_output_root not in retry_output_dir.parents:
        raise MdStateError("MD_STATE_CORRUPT", "retry output lineage escapes the parent result root")
    child_params = copy.deepcopy(previous_child.params or {})
    child_params["md_attempt"] = next_attempt
    child_id = str(uuid.uuid4())
    child = Job(
        id=child_id,
        name=f"{previous_child.name} retry {next_attempt}",
        status="queued",
        queue_status="queued",
        model_id=previous_child.model_id,
        mode=previous_child.mode,
        params=child_params,
        output_dir=str(retry_output_dir),
        parent_job_id=job_id,
        batch_id=previous_child.batch_id or job_id,
        batch_name=previous_child.batch_name,
        lineage_root_job_id=previous_child.lineage_root_job_id or job_id,
        child_stage="md_replica",
        priority=previous_child.priority,
        vram_estimate_mb=previous_child.vram_estimate_mb,
        max_retries=previous_child.max_retries,
        oom_tolerance=previous_child.oom_tolerance,
    )
    replica = MdReplicaRun(
        id=str(uuid.uuid4()), child_job_id=child_id, md_job_id=job_id,
        replica_index=replica_index, attempt=next_attempt, engine=previous.engine,
        state="queued", active=True,
    )
    segment = MdAttemptSegment(
        id=str(uuid.uuid4()), replica_run_id=replica.id, segment_index=0, state="queued",
        execution_plan_sha256=source.execution_plan_sha256,
        compatibility_key=source.compatibility_key,
    )
    session.add(child)
    await session.flush()
    session.add(replica)
    await session.flush()
    session.add(segment)
    await session.flush()
    parent_job.status = "running"; parent_job.queue_status = "running"
    parent_job.completed_at = None; parent_job.error_message = None
    await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="retry_requested", expected_version=expected_version,
        next_phase="replicas_queued",
        payload={"replica_run_id": replica.id, "child_job_id": child_id,
                 "replica_index": replica_index, "attempt": next_attempt},
    )
    return replica


async def request_cancel(session: AsyncSession, *, job_id: str, expected_version: int, idempotency_key: str) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "MD run was not found")
    existing = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="cancel_requested", expected_version=expected_version,
    )
    if existing is not None:
        return run
    if run.phase in TERMINAL_PHASES:
        raise MdStateError("MD_CANCEL_UNAVAILABLE", "terminal run cannot be cancelled")
    return await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key, event_type="cancel_requested",
        expected_version=expected_version, next_phase="cancelling", block_controls=True,
    )


async def finalize_cancel(session: AsyncSession, *, job_id: str, expected_version: int, idempotency_key: str) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "MD run was not found")
    existing = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="cancel_completed", expected_version=expected_version,
    )
    if existing is not None:
        return run
    if run.phase != "cancelling" or not run.controls_blocked:
        raise MdStateError("MD_CANCEL_TRANSITION_INVALID", "cancel can finalize only from cancelling")
    states = list((await session.scalars(select(MdReplicaRun.state).where(MdReplicaRun.md_job_id == job_id))).all())
    if any(state not in TERMINAL_REPLICA_STATES for state in states):
        raise MdStateError("MD_CANCEL_INCOMPLETE", "descendants are not terminal or orphan-classified")
    job = await session.get(Job, job_id)
    if job is not None:
        job.status = "cancelled"; job.queue_status = "completed"; job.completed_at = datetime.utcnow()
    return await append_event_cas(
        session, job_id=job_id, idempotency_key=idempotency_key, event_type="cancel_completed",
        expected_version=expected_version, next_phase="cancelled", block_controls=True,
    )

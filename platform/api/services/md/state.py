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
from scripts.bms_md.contract import RETRYABLE_INFRASTRUCTURE_FAILURES


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
    """Persist native intent, then ask the shared attempt owner for replacement.

    The intent commits before transport. Response loss never creates another
    local scheduler job: replay uses the same operation and retained shared edge.
    """
    existing_event = await _replay_event(
        session, job_id=job_id, idempotency_key=idempotency_key,
        event_type="retry_requested", expected_version=expected_version,
    )
    parent_job = await session.get(Job, job_id)
    if parent_job is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "parent scheduler job is missing")
    if existing_event is not None:
        intent = dict(existing_event.payload or {})
        if intent.get('replica_index') != replica_index:
            raise MdStateError("MD_IDEMPOTENCY_CONFLICT", "retry key belongs to another replica")
        replica = await session.get(MdReplicaRun, intent.get('replica_run_id'))
        if replica is None or not intent.get('source_child_job_id'):
            raise MdStateError("MD_STATE_CORRUPT", "retry event has no shared attempt intent")
        if intent.get('shared_retry_receipt'):
            return replica
    else:
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
            raise MdStateError("MD_RETRY_REVIEW_REQUIRED",
                "only allowlisted infrastructure failures may be retried without scientific review")
        source = await session.scalar(select(MdAttemptSegment).where(
            MdAttemptSegment.replica_run_id == previous.id,
        ).order_by(MdAttemptSegment.segment_index.desc()).limit(1))
        previous_child = await session.get(Job, previous.child_job_id) if previous.child_job_id else None
        if source is None or previous_child is None:
            raise MdStateError("MD_STATE_CORRUPT", "retry source lineage is incomplete")
        replica = MdReplicaRun(
            id=str(uuid.uuid4()), child_job_id=None, md_job_id=job_id,
            replica_index=replica_index, attempt=previous.attempt + 1, engine=previous.engine,
            state="queued", active=True,
        )
        intent = dict(replica_run_id=replica.id, replica_index=replica_index,
            attempt=replica.attempt, source_replica_run_id=previous.id,
            source_child_job_id=previous_child.id, failure_code=failure_code,
            execution_target_id=parent_job.execution_target_id,
            remote_attempt_id=parent_job.remote_attempt_id)
        # CAS before any external effect. A pending operation blocks new controls
        # but remains explicitly reconciling, not falsely running.
        await append_event_cas(session, job_id=job_id, idempotency_key=idempotency_key,
            event_type="retry_requested", expected_version=expected_version,
            next_phase="reconciling", block_controls=True, payload=intent)
        session.add(replica)
        await session.flush()
        session.add(MdAttemptSegment(id=str(uuid.uuid4()), replica_run_id=replica.id,
            segment_index=0, state="queued", execution_plan_sha256=source.execution_plan_sha256,
            compatibility_key=source.compatibility_key, source_segment_id=source.id))
        await session.flush()
    # The same committed intent is the retry outbox on both placements.
    replica_id = replica.id
    await session.commit()
    from services.remote_execution.executor import retry_component_execution
    try:
        receipt = await retry_component_execution(session, parent_job,
            component_id=intent['source_child_job_id'], operation_id=idempotency_key,
            actor='md-retry:' + job_id, failure_code=intent['failure_code'])
    except Exception as exc:
        raise MdStateError("MD_RETRY_ACTUATION_UNCERTAIN",
            "shared retry remains pending; replay the same idempotency key to reconcile") from exc
    replica = await session.get(MdReplicaRun, replica_id, populate_existing=True)
    event = await _replay_event(session, job_id=job_id, idempotency_key=idempotency_key,
        event_type='retry_requested', expected_version=expected_version)
    if event is None or replica is None:
        raise MdStateError("MD_STATE_CORRUPT", "committed retry intent disappeared")
    event.payload = dict(intent, pending_transport_receipt=receipt)
    await bind_retry_child_projection(session, parent=parent_job, event=event)
    # Local acceptance queues the SAME root for scheduler readmission. Do not
    # claim running or require an as-yet unobserved child Job as acknowledgement.
    await session.flush()
    return replica

async def bind_retry_child_projection(session: AsyncSession, *, parent: Job,
                                      event: MdEvent, apply: bool = True) -> bool:
    """Bind an observed shared child, never insert or schedule a shadow Job."""
    intent = dict(event.payload or {})
    if intent.get('shared_retry_receipt'):
        return True
    receipt = intent.get('pending_transport_receipt') or {}
    # Raw shared edges name the predecessor in component_id; child_job_id is
    # their replacement. Normalized transport receipts name the replacement
    # directly in component_id. Never bind the predecessor as the new attempt.
    native_id = (receipt.get('child_job_id') if receipt.get('replacement') else
                 receipt.get('component_id') or receipt.get('child_job_id'))
    if not native_id:
        return False
    candidates = list((await session.scalars(select(Job).where(
        Job.parent_job_id == parent.id, Job.child_stage == 'md_replica',
    ))).all())
    child = next((item for item in candidates
        if (item.provenance or {}).get('component_id') == native_id
        or item.id == native_id), None)
    if child is None:
        return False
    params = child.params or {}
    control = (child.provenance or {}).get('component_projection') or {}
    if (child.execution_target_id != parent.execution_target_id
            or child.id == intent.get('source_child_job_id')
            or control.get('root_job_id') != parent.id
            or (parent.execution_target_id and control.get('attempt_id') != parent.remote_attempt_id)
            or params.get('md_attempt') != intent['attempt']
            or params.get('md_replica_index') != intent['replica_index']):
        raise MdStateError('MD_STATE_CORRUPT', 'retry child projection conflicts with native intent')
    replica = await session.get(MdReplicaRun, intent['replica_run_id'])
    if replica is None:
        raise MdStateError('MD_STATE_CORRUPT', 'retry replica projection is missing')
    if apply:
        replica.child_job_id = child.id
        event.payload = dict(intent, child_job_id=child.id,
            shared_retry_receipt=dict(receipt, child_job_id=child.id, component_id=native_id))
        run = await session.get(MdRun, parent.id)
        if run is not None:
            run.controls_blocked = False
    return True


async def reconcile_component_projection(session: AsyncSession, parent: Job,
                                         children: Iterable[Job]) -> None:
    """Native projection after the shared importer authenticates attempt custody.

    No Job creation, queue admission or science execution occurs here. The native
    completion owner still requires aggregate, mandatory analyses and barrier.
    """
    if parent.model_id != 'molecular_dynamics' or parent.mode != 'simulate':
        return
    run = await session.get(MdRun, parent.id)
    if run is None:
        raise MdStateError('MD_RUN_NOT_FOUND', 'component projection requires its native MD run')
    rows = sorted((child for child in children if child.child_stage == 'md_replica'),
        key=lambda child: ((child.params or {}).get('md_replica_index', -1),
                           (child.params or {}).get('md_attempt', -1)))
    if not rows:
        return
    existing = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == parent.id))).all())
    by_identity = {(item.replica_index, item.attempt): item for item in existing}
    # Retire active projections before replacing an index (unique active index).
    for item in existing:
        item.active = False
    await session.flush()
    for child in rows:
        params = child.params or {}
        index, attempt = params.get('md_replica_index'), params.get('md_attempt')
        if (child.parent_job_id != parent.id or child.execution_target_id != parent.execution_target_id
                or not (child.provenance or {}).get('component_projection')
                or type(index) is not int or type(attempt) is not int or attempt < 0
                or not 0 <= index < int(run.normalized_request['replicas'])
                or params.get('md_replica_seed') != int(run.normalized_request['random_seed']) + index
                or params.get('md_engine') != run.normalized_request['engine']):
            raise MdStateError('MD_STATE_CORRUPT', 'projected MD request changed native replica identity')
        plan_sha, compatibility = params.get('md_execution_plan_sha256'), params.get('md_compatibility_key')
        if any(not isinstance(value, str) or len(value) != 64
               or any(char not in '0123456789abcdef' for char in value)
               for value in (plan_sha, compatibility)):
            raise MdStateError('MD_STATE_CORRUPT', 'projected MD request lacks native segment identity')
        replica = by_identity.get((index, attempt))
        if replica is None:
            replica = MdReplicaRun(id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"bms-md-component:{parent.id}:{index}:{attempt}")), md_job_id=parent.id,
                replica_index=index, attempt=attempt, engine=params['md_engine'], active=False)
            session.add(replica)
            by_identity[index, attempt] = replica
        if replica.child_job_id not in {None, child.id}:
            raise MdStateError('MD_STATE_CORRUPT', 'native attempt already belongs to another projected child')
        replica.child_job_id = child.id
        control = (child.provenance or {}).get('component_projection') or {}
        raw_state = control.get('state', child.status)
        replica.state = ({'execution_finished': 'running', 'uncertain': 'orphaned'}
                         .get(raw_state, raw_state))
        if replica.state not in TERMINAL_REPLICA_STATES | ACTIVE_REPLICA_STATES:
            raise MdStateError('MD_STATE_CORRUPT', 'unknown projected native component state')
        if replica.state in TERMINAL_REPLICA_STATES:
            replica.completed_at = replica.completed_at or child.completed_at
            if replica.state in {'failed', 'orphaned'} and (replica.failure is None or (
                    control.get('failure_receipt') is not None
                    and (replica.failure or {}).get('code') == 'execution_failed'
                    and (replica.failure or {}).get('source') == 'worker_terminal')):
                from services.md.reconcile import _failure_from_child
                replica.failure = _failure_from_child(child, replica.state)
        await session.flush()
        segment = await session.scalar(select(MdAttemptSegment).where(
            MdAttemptSegment.replica_run_id == replica.id,
            MdAttemptSegment.segment_index == 0))
        if segment is None:
            segment = MdAttemptSegment(id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"bms-md-component-segment:{replica.id}:0")), replica_run_id=replica.id,
                segment_index=0, execution_plan_sha256=plan_sha, compatibility_key=compatibility,
                state=replica.state)
            session.add(segment)
        elif (segment.execution_plan_sha256, segment.compatibility_key) != (plan_sha, compatibility):
            raise MdStateError('MD_STATE_CORRUPT', 'native segment identity changed on projection replay')
        # Never rewrite earlier checkpoint segments; latest segment owns progress.
        latest = await session.scalar(select(MdAttemptSegment).where(
            MdAttemptSegment.replica_run_id == replica.id).order_by(MdAttemptSegment.segment_index.desc()).limit(1))
        if latest is not None:
            latest.state = replica.state
            if replica.state in TERMINAL_REPLICA_STATES:
                latest.completed_at = latest.completed_at or child.completed_at
    latest_by_index = {}
    for item in sorted(by_identity.values(), key=lambda item: item.attempt):
        latest_by_index[item.replica_index] = item
    for item in latest_by_index.values():
        item.active = item.state not in TERMINAL_REPLICA_STATES
    events = list((await session.scalars(select(MdEvent).where(
        MdEvent.md_job_id == parent.id, MdEvent.event_type == 'retry_requested'))).all())
    for event in events:
        if (event.payload or {}).get('source_child_job_id'):
            await bind_retry_child_projection(session, parent=parent, event=event)
    await session.flush()


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
    if run.phase == "cancelling":
        if expected_version != run.state_version:
            raise MdStateError("MD_STATE_VERSION_CONFLICT", "state version changed")
        # The intent is already durable. A fresh key after API/client restart must
        # re-enter the external actuator without appending a second transition.
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

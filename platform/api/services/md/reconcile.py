"""Lease-owned reconciliation between generic child jobs and durable MD state."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, MdAttemptSegment, MdReconcilerLease, MdReplicaRun, MdRun
from services.md.state import (
    RETRYABLE_INFRASTRUCTURE_FAILURES,
    TERMINAL_PHASES,
    MdStateError,
    append_event_cas,
    finalize_pause,
)

logger = logging.getLogger(__name__)

_TERMINAL_REPLICA_STATES = {"completed", "failed", "cancelled", "orphaned"}


def _completed_segment_bounds(parent: Job | None, replica: MdReplicaRun) -> tuple[int, float] | None:
    """Read terminal production bounds only from the validated replica publication."""

    if parent is None or not parent.output_dir:
        return None
    try:
        root = Path(parent.output_dir).expanduser().resolve(strict=True)
        manifest = root / "replicas" / f"replica_{replica.replica_index}" / "manifest.json"
        if manifest.is_symlink() or not manifest.is_file():
            return None
        manifest.resolve(strict=True).relative_to(root)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema") != "bms.md.run.v1"
        or payload.get("job_id") != parent.id
        or payload.get("replica_index") != replica.replica_index
    ):
        return None
    config = payload.get("config")
    stages = payload.get("stages")
    if not isinstance(config, dict) or not isinstance(stages, dict):
        return None
    production = config.get("stages", {}).get("production")
    observed = stages.get("production")
    if not isinstance(production, dict) or not isinstance(observed, dict):
        return None
    if production.get("enabled") is not True or observed.get("status") != "completed":
        return None
    steps = production.get("steps")
    timestep_fs = production.get("timestep_fs")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        return None
    if not isinstance(timestep_fs, (int, float)) or isinstance(timestep_fs, bool) or timestep_fs <= 0:
        return None
    return steps, float(steps * timestep_fs / 1000.0)


def _project_child(job: Job) -> str:
    status = str(job.status)
    if status in {"completed", "failed", "cancelled"}:
        return status
    if str(job.queue_status) == "failed":
        return "failed"
    if bool(job.paused) or job.queue_status == "paused":
        return "paused"
    if status == "running" or job.queue_status == "running":
        return "running"
    return "queued"


def _failure_from_child(job: Job | None, projected: str) -> dict | None:
    if job is None or projected not in {"failed", "orphaned"}:
        return None
    message = str(job.error_message or "worker ended without a failure receipt")[:2000]
    receipt = (job.provenance or {}).get("failure_receipt")
    if isinstance(receipt, dict):
        code = str(receipt.get("code") or "")
        source = str(receipt.get("source") or "")
        if code in RETRYABLE_INFRASTRUCTURE_FAILURES and source == "scheduler_launch":
            return {"code": code, "message": str(receipt.get("message") or message)[:2000], "source": source}
    if projected == "failed" and str(job.status) not in {"failed", "cancelled"} and str(job.queue_status) == "failed":
        return {"code": "spawn_rejected", "message": message, "source": "scheduler_launch"}
    return {"code": "execution_failed", "message": message, "source": "worker_terminal"}


def _phase(run: MdRun, states: list[str]) -> str:
    if run.phase in TERMINAL_PHASES | {"checkpointing", "paused", "cancelling"} or not states:
        return run.phase
    if any(state == "running" for state in states):
        return "replicas_running"
    if any(state in {"queued", "launching"} for state in states):
        return "replicas_queued"
    if all(state == "completed" for state in states):
        return "finalizing"
    if all(state in {"completed", "failed", "cancelled", "orphaned"} for state in states):
        return "partial" if any(state == "completed" for state in states) else "failed"
    return "reconciling"


async def acquire_reconciler_lease(
    session: AsyncSession, *, owner_id: str, lease_seconds: int = 60,
) -> bool:
    now = datetime.utcnow(); expires = now + timedelta(seconds=lease_seconds)
    statement = sqlite_insert(MdReconcilerLease).values(
        name="md-lifecycle", owner_id=owner_id, expires_at=expires, updated_at=now,
    ).on_conflict_do_update(
        index_elements=[MdReconcilerLease.name],
        set_={"owner_id": owner_id, "expires_at": expires, "updated_at": now},
        where=or_(
            MdReconcilerLease.owner_id == owner_id,
            MdReconcilerLease.expires_at <= now,
        ),
    )
    result = await session.execute(statement)
    return result.rowcount == 1


async def reconcile_md_state(
    session: AsyncSession, *, owner_id: str, apply: bool = False,
) -> dict:
    if apply and not await acquire_reconciler_lease(session, owner_id=owner_id):
        raise RuntimeError("MD_RECONCILER_LEASE_UNAVAILABLE")
    stale_segment_runs = (
        select(MdReplicaRun.md_job_id)
        .join(MdAttemptSegment, MdAttemptSegment.replica_run_id == MdReplicaRun.id)
        .where(~MdAttemptSegment.state.in_(_TERMINAL_REPLICA_STATES | {"paused"}))
    )
    runs = list((await session.scalars(select(MdRun).where(or_(
        ~MdRun.phase.in_(TERMINAL_PHASES),
        MdRun.job_id.in_(stale_segment_runs),
    )))).all())
    changes: list[dict] = []
    planned: list[tuple[MdRun, Job | None, list[tuple[MdReplicaRun, MdAttemptSegment | None, str]], str]] = []
    for run in runs:
        parent = await session.get(Job, run.job_id)
        replicas = list((await session.scalars(select(MdReplicaRun).where(
            MdReplicaRun.md_job_id == run.job_id
        ))).all())
        projections: list[tuple[MdReplicaRun, MdAttemptSegment | None, str]] = []
        effective: list[str] = []
        for replica in replicas:
            child = await session.get(Job, replica.child_job_id) if replica.child_job_id else None
            projected = _project_child(child) if child is not None else replica.state
            segment = await session.scalar(
                select(MdAttemptSegment)
                .where(MdAttemptSegment.replica_run_id == replica.id)
                .order_by(MdAttemptSegment.segment_index.desc())
            )
            effective.append(projected)
            segment_stale = (
                projected in _TERMINAL_REPLICA_STATES
                and segment is not None
                and segment.state not in _TERMINAL_REPLICA_STATES
            )
            projected_failure = (
                _failure_from_child(child, projected) if replica.failure is None else None
            )
            if projected != replica.state or segment_stale or projected_failure is not None:
                projections.append((replica, segment, projected))
            if projected != replica.state:
                changes.append({"kind": "replica_state", "job_id": run.job_id,
                                "replica_run_id": replica.id, "from": replica.state, "to": projected})
            if projected_failure is not None:
                changes.append({"kind": "replica_failure", "job_id": run.job_id,
                                "replica_run_id": replica.id, "failure": projected_failure})
            if segment_stale and segment is not None:
                changes.append({"kind": "segment_state", "job_id": run.job_id,
                                "segment_id": segment.id, "from": segment.state, "to": projected})
        next_phase = _phase(run, effective)
        if next_phase != run.phase:
            changes.append({"kind": "parent_phase", "job_id": run.job_id,
                            "from": run.phase, "to": next_phase})
        planned.append((run, parent, projections, next_phase))
    receipt = {"schema": "bms.md.reconciliation.v1", "dry_run": not apply,
               "owner_id": owner_id, "change_count": len(changes), "changes": changes}
    receipt["plan_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not apply:
        return receipt
    for run, parent, projections, next_phase in planned:
        for replica, segment, state in projections:
            replica.state = state
            if state in _TERMINAL_REPLICA_STATES:
                if replica.failure is None:
                    child = await session.get(Job, replica.child_job_id) if replica.child_job_id else None
                    replica.failure = _failure_from_child(child, state)
                replica.active = False
                replica.completed_at = datetime.utcnow()
                if segment is not None and segment.state not in _TERMINAL_REPLICA_STATES:
                    segment.state = state
                    segment.completed_at = datetime.utcnow()
                    if state == "completed":
                        bounds = _completed_segment_bounds(parent, replica)
                        if bounds is not None:
                            segment.end_step, segment.end_time_ps = bounds
        if run.phase == "checkpointing":
            try:
                await finalize_pause(
                    session,
                    job_id=run.job_id,
                    expected_version=run.state_version,
                    idempotency_key=f"reconcile-pause:{run.job_id}:{run.state_version}",
                )
                continue
            except MdStateError as exc:
                if exc.code not in {"MD_PAUSE_INCOMPLETE"}:
                    raise
        if next_phase != run.phase:
            await append_event_cas(
                session, job_id=run.job_id,
                idempotency_key=f"reconcile:{run.job_id}:{run.state_version}:{next_phase}",
                event_type="reconciled", expected_version=run.state_version,
                next_phase=next_phase, payload={"replica_updates": len(projections)},
            )
    await session.flush()
    receipt["applied"] = True
    return receipt


class MdReconcilerWorker:
    """Periodically reconciles persisted MD lineage after worker or API restarts."""

    def __init__(self, db_session_factory, *, poll_interval: float = 5.0, owner_id: str | None = None):
        self.db_session_factory = db_session_factory
        self.poll_interval = poll_interval
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> dict:
        async with self.db_session_factory() as session:
            try:
                receipt = await reconcile_md_state(session, owner_id=self.owner_id, apply=True)
                await session.commit()
                return receipt
            except Exception:
                await session.rollback()
                raise

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except RuntimeError as exc:
                if str(exc) != "MD_RECONCILER_LEASE_UNAVAILABLE":
                    logger.exception("MD reconciliation failed")
            except Exception:
                logger.exception("MD reconciliation failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="md-reconciler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

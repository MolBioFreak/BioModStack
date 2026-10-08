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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, MdReconcilerLease, MdReplicaRun, MdRun
from services.md.state import TERMINAL_PHASES, MdStateError, append_event_cas, finalize_pause

logger = logging.getLogger(__name__)


def _project_child(job: Job) -> str:
    status = str(job.status)
    if status in {"completed", "failed", "cancelled"}:
        return status
    if bool(job.paused) or job.queue_status == "paused":
        return "paused"
    if status == "running" or job.queue_status == "running":
        return "running"
    return "queued"


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
    lease = await session.get(MdReconcilerLease, "md-lifecycle")
    if lease is None:
        session.add(MdReconcilerLease(name="md-lifecycle", owner_id=owner_id,
                                      expires_at=expires, updated_at=now))
        await session.flush(); return True
    if lease.owner_id != owner_id and lease.expires_at > now:
        return False
    lease.owner_id = owner_id; lease.expires_at = expires; lease.updated_at = now
    await session.flush(); return True


async def reconcile_md_state(
    session: AsyncSession, *, owner_id: str, apply: bool = False,
) -> dict:
    if apply and not await acquire_reconciler_lease(session, owner_id=owner_id):
        raise RuntimeError("MD_RECONCILER_LEASE_UNAVAILABLE")
    runs = list((await session.scalars(select(MdRun).where(~MdRun.phase.in_(TERMINAL_PHASES)))).all())
    changes: list[dict] = []
    planned: list[tuple[MdRun, list[tuple[MdReplicaRun, str]], str]] = []
    for run in runs:
        replicas = list((await session.scalars(select(MdReplicaRun).where(
            MdReplicaRun.md_job_id == run.job_id
        ))).all())
        projections: list[tuple[MdReplicaRun, str]] = []
        effective: list[str] = []
        for replica in replicas:
            child = await session.get(Job, replica.child_job_id) if replica.child_job_id else None
            projected = _project_child(child) if child is not None else replica.state
            effective.append(projected)
            if projected != replica.state:
                projections.append((replica, projected))
                changes.append({"kind": "replica_state", "job_id": run.job_id,
                                "replica_run_id": replica.id, "from": replica.state, "to": projected})
        next_phase = _phase(run, effective)
        if next_phase != run.phase:
            changes.append({"kind": "parent_phase", "job_id": run.job_id,
                            "from": run.phase, "to": next_phase})
        planned.append((run, projections, next_phase))
    receipt = {"schema": "bms.md.reconciliation.v1", "dry_run": not apply,
               "owner_id": owner_id, "change_count": len(changes), "changes": changes}
    receipt["plan_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not apply:
        return receipt
    for run, projections, next_phase in planned:
        for replica, state in projections:
            replica.state = state
            if state in {"completed", "failed", "cancelled", "orphaned"}:
                replica.active = False
                replica.completed_at = datetime.utcnow()
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

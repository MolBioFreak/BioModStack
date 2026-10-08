from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, MdAttemptSegment, MdReplicaRun
from services.nextflow import cancel_nextflow_job

from .state import MdStateError, finalize_cancel, request_cancel

CancelWorker = Callable[[str], Awaitable[bool]]


async def _cancel_md_worker(nextflow_run_id: str) -> bool:
    return await cancel_nextflow_job(nextflow_run_id, graceful_timeout_seconds=120.0)


async def cancel_running_md_run(
    session: AsyncSession,
    *,
    job_id: str,
    expected_version: int,
    idempotency_key: str,
    cancel_worker: CancelWorker = _cancel_md_worker,
):
    parent = await session.get(Job, job_id)
    if parent is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "MD run was not found")

    replicas = list((await session.scalars(
        select(MdReplicaRun).where(
            MdReplicaRun.md_job_id == job_id,
            MdReplicaRun.active.is_(True),
        )
    )).all())
    children: list[Job] = []
    targets: list[str] = []
    if parent.nextflow_run_id:
        targets.append(str(parent.nextflow_run_id))
    for replica in replicas:
        if not replica.child_job_id:
            raise MdStateError(
                "MD_CANCEL_ACTUATION_FAILED",
                "active replica worker identity is incomplete",
            )
        child = await session.get(Job, replica.child_job_id)
        if child is None or not child.nextflow_run_id:
            raise MdStateError(
                "MD_CANCEL_ACTUATION_FAILED",
                "active replica worker identity is incomplete",
            )
        children.append(child)
        target = str(child.nextflow_run_id)
        if target not in targets:
            targets.append(target)

    run = await request_cancel(
        session,
        job_id=job_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    await session.flush()
    await session.commit()

    for target in targets:
        if not await cancel_worker(target):
            raise MdStateError(
                "MD_CANCEL_ACTUATION_FAILED",
                f"workflow adapter did not confirm cancellation for {target}",
            )

    now = datetime.utcnow()
    terminal_segment_states = {"completed", "failed", "cancelled", "paused", "orphaned"}
    for replica, child in zip(replicas, children, strict=True):
        replica.active = False
        replica.state = "cancelled"
        replica.ended_at = now
        child.status = "cancelled"
        child.queue_status = "completed"
        child.completed_at = now
        segments = list((await session.scalars(
            select(MdAttemptSegment).where(MdAttemptSegment.replica_run_id == replica.id)
        )).all())
        for segment in segments:
            if segment.state not in terminal_segment_states:
                segment.state = "cancelled"
                segment.ended_at = now

    run = await finalize_cancel(
        session,
        job_id=job_id,
        expected_version=run.state_version,
        idempotency_key=f"{idempotency_key}:completed",
    )
    return run

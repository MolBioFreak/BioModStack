from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, MdAttemptSegment, MdReplicaRun
from services.nextflow import cancel_nextflow_job, get_running_jobs

from .state import MdStateError, finalize_cancel, request_cancel

CancelWorker = Callable[[str], Awaitable[bool]]
WorkerIsRunning = Callable[[str], bool]


async def _cancel_md_worker(nextflow_run_id: str) -> bool:
    return await cancel_nextflow_job(nextflow_run_id, graceful_timeout_seconds=120.0)


def _md_worker_is_running(job_id: str) -> bool:
    return str(job_id) in get_running_jobs()


async def cancel_running_md_run(
    session: AsyncSession,
    *,
    job_id: str,
    expected_version: int,
    idempotency_key: str,
    cancel_worker: CancelWorker = _cancel_md_worker,
    worker_is_running: WorkerIsRunning = _md_worker_is_running,
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
    # Attempt-local children are observations, not independent runner handles.
    # The existing lineage owner withdraws queued work and stops the retained
    # root with its attempt/source fences; it also retains unverified-stop evidence.
    projected = list((await session.scalars(select(Job).where(
        Job.parent_job_id == job_id,
    ))).all())
    shared = bool(
        (parent.provenance or {}).get("component_context_path")
        or parent.remote_attempt_id
        or any((child.provenance or {}).get("component_projection") for child in projected)
    )
    if shared or (parent.status == "queued" and not parent.nextflow_run_id):
        from services.job_control import cancel_job_lineage

        async def persist_native_intent() -> None:
            await request_cancel(session, job_id=job_id, expected_version=expected_version,
                                 idempotency_key=idempotency_key)

        parent, lineage = await cancel_job_lineage(
            job_id, session, commit=False, before_intent_commit=persist_native_intent,
        )
        receipt = (parent.params or {}).get("cancellation_receipt") or {}
        unverified = bool(parent.nextflow_run_id or parent.remote_attempt_id or parent.started_at) and not receipt.get("remote_stop_verified")
        now = datetime.utcnow()
        for replica in replicas:
            replica.active = False
            replica.state = "orphaned" if unverified else "cancelled"
            replica.completed_at = now
            if unverified:
                replica.failure = {"code": "cancel_stop_unverified", "source": "root_cancellation"}
            segments = list((await session.scalars(select(MdAttemptSegment).where(
                MdAttemptSegment.replica_run_id == replica.id,
            ))).all())
            for segment in segments:
                if segment.state not in {"completed", "failed", "cancelled", "orphaned"}:
                    segment.state = replica.state
                    segment.completed_at = now
        from database import MdRun
        run = await session.get(MdRun, job_id, populate_existing=True)
        return await finalize_cancel(session, job_id=job_id, expected_version=run.state_version,
                                     idempotency_key=f"{idempotency_key}:completed")

    children: list[Job] = []
    targets: list[str] = []
    processless_pre_replica = False
    terminal_job_statuses = {"completed", "failed", "cancelled", "canceled"}
    if parent.status not in terminal_job_statuses:
        if not parent.nextflow_run_id:
            if replicas:
                raise MdStateError(
                    "MD_CANCEL_ACTUATION_FAILED",
                    "active parent workflow identity is incomplete",
                )
            if worker_is_running(job_id):
                targets.append(str(job_id))
            else:
                processless_pre_replica = True
        else:
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
    if processless_pre_replica:
        provenance = dict(parent.provenance or {})
        provenance["md_cancel_receipt"] = {
            "code": "processless_pre_replica",
            "worker_created": False,
            "replica_created": False,
            "observed_at": now.isoformat() + "Z",
        }
        parent.provenance = provenance
    terminal_segment_states = {"completed", "failed", "cancelled", "paused", "orphaned"}
    for replica, child in zip(replicas, children, strict=True):
        replica.active = False
        replica.state = "cancelled"
        replica.completed_at = now
        child.status = "cancelled"
        child.queue_status = "completed"
        child.completed_at = now
        segments = list((await session.scalars(
            select(MdAttemptSegment).where(MdAttemptSegment.replica_run_id == replica.id)
        )).all())
        for segment in segments:
            if segment.state not in terminal_segment_states:
                segment.state = "cancelled"
                segment.completed_at = now

    run = await finalize_cancel(
        session,
        job_id=job_id,
        expected_version=run.state_version,
        idempotency_key=f"{idempotency_key}:completed",
    )
    return run

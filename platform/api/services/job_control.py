"""
Job control helpers shared across routers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Iterable, Optional, Sequence
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, ExecutionTarget, Job, MdRun
from schemas import JobStatus
from services.execution_ownership import release_scheduler_gpu_assignment
from services.nextflow import cancel_nextflow_job

logger = logging.getLogger(__name__)

_ACTIVE_QUEUE_STATUSES = {"queued", "running", "paused", "pending_msa"}
_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.AWAITING_INPUT.value,
}


async def get_md_owned_job_ids(session: AsyncSession) -> set[str]:
    roots = set((await session.scalars(select(MdRun.job_id))).all())
    if not roots:
        return set()
    descendants = set((await session.scalars(select(Job.id).where(
        (Job.parent_job_id.in_(roots)) | (Job.child_stage == "md_replica")
    ))).all())
    return roots | descendants


async def reject_generic_md_lifecycle_control(job_id: str, session: AsyncSession) -> None:
    """Prevent generic controls from bypassing durable MD lifecycle semantics."""
    # FastAPI always injects a real AsyncSession.  A few internal/unit callers
    # invoke route functions directly without dependency injection; those
    # callers cannot resolve MD ownership and must not crash before the route's
    # own fail-closed guards run.
    if not callable(getattr(session, "get", None)):
        return
    job = await session.get(Job, job_id)
    root_id = job.parent_job_id if job is not None and job.parent_job_id else job_id
    if await session.get(MdRun, root_id) is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MD_LIFECYCLE_CONTROL_REQUIRED",
                "message": "Use the Molecular Dynamics lifecycle operation for this run",
                "md_job_id": root_id,
            },
        )


def _job_is_cancelable(job: Job) -> bool:
    status = str(getattr(job, "status", "") or "").strip().lower()
    queue_status = str(getattr(job, "queue_status", "") or "").strip().lower()
    awaiting_input = bool(getattr(job, "awaiting_input", False))
    nextflow_run_id = getattr(job, "nextflow_run_id", None)
    completed_at = getattr(job, "completed_at", None)
    return (
        status in _ACTIVE_JOB_STATUSES
        or queue_status in _ACTIVE_QUEUE_STATUSES
        or awaiting_input
        or (bool(nextflow_run_id) and completed_at is None)
    )


def _lineage_has_cancelable_jobs(jobs: Iterable[Job]) -> bool:
    return any(_job_is_cancelable(job) for job in jobs)


def _sort_jobs_for_cancellation(jobs: Iterable[Job], depth_by_id: dict[str, int]) -> list[Job]:
    return sorted(
        jobs,
        key=lambda job: (
            depth_by_id.get(job.id, 0),
            job.created_at or datetime.min,
        ),
        reverse=True,
    )


async def _load_job_lineage(
    session: AsyncSession,
    root_job_id: str,
) -> tuple[Job | None, list[Job], dict[str, int]]:
    result = await session.execute(select(Job).where(Job.id == root_job_id))
    root_job = result.scalar_one_or_none()
    if not root_job:
        return None, [], {}

    jobs_by_id: dict[str, Job] = {root_job.id: root_job}
    depth_by_id: dict[str, int] = {root_job.id: 0}
    frontier = [root_job.id]

    while frontier:
        child_result = await session.execute(select(Job).where(Job.parent_job_id.in_(frontier)))
        children = child_result.scalars().all()
        frontier = []
        for child in children:
            if child.id in jobs_by_id:
                continue
            jobs_by_id[child.id] = child
            depth_by_id[child.id] = depth_by_id.get(child.parent_job_id or "", 0) + 1
            frontier.append(child.id)

    lineage = _sort_jobs_for_cancellation(jobs_by_id.values(), depth_by_id)
    return root_job, lineage, depth_by_id


async def cancel_job_lineage(
    job_id: str,
    session: AsyncSession,
    *,
    error_message: str = "Cancelled by user",
    commit: bool = True,
    before_intent_commit: Callable[[], Awaitable[None]] | None = None,
    before_terminal_commit: Callable[[], Awaitable[None]] | None = None,
) -> tuple[Job, list[Job]]:
    root_job, lineage, _ = await _load_job_lineage(session, job_id)
    if not root_job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not _lineage_has_cancelable_jobs(lineage):
        # A cancellation request for an already-cancelled lineage is idempotent:
        # accept it so stale cancellation metadata can be normalized below.
        if not all(str(job.status or "").strip().lower() == JobStatus.CANCELLED.value for job in lineage):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel job with status: {root_job.status}",
            )

    request_time = datetime.utcnow()
    cancellable = [job for job in lineage if _job_is_cancelable(job)]
    cancellable_ids = {str(job.id) for job in cancellable}
    already_cancelled = [
        job for job in lineage
        if str(job.id) not in cancellable_ids
        and str(job.status or "").strip().lower() == JobStatus.CANCELLED.value
    ]
    intent_statuses = {
        str(job.id): str(job.status or "")
        for job in cancellable
    }
    intent_queue_statuses = {str(job.id): job.queue_status for job in cancellable}
    intent_completed_at = {str(job.id): job.completed_at for job in cancellable}

    # Publish durable intent before asking the external owner to stop anything.
    # A process/unit result cannot grant terminal cancellation authority.
    intent_conflicts: list[str] = []
    for job in cancellable:
        params = json.loads(job.params) if isinstance(job.params, str) else dict(job.params or {})
        params["cancellation_receipt"] = {
            "schema": "bms.workflow-cancellation.v1",
            "state": "requested",
            "requested_at": request_time.isoformat() + "Z",
            "run_identity": str(job.nextflow_run_id or ""),
        }
        intent_update = await session.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.status == intent_statuses[str(job.id)],
                Job.queue_status == intent_queue_statuses[str(job.id)],
                Job.completed_at == intent_completed_at[str(job.id)],
            )
            .values(params=params, queue_status="cancelling")
            .execution_options(synchronize_session=False)
        )
        if intent_update.rowcount != 1:
            intent_conflicts.append(str(job.id))
    if intent_conflicts:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANCELLATION_INTENT_CONFLICT",
                "message": "Producer state changed before cancellation intent publication",
                "job_ids": intent_conflicts,
            },
        )
    for job in cancellable:
        await session.refresh(job)
    if before_intent_commit is not None:
        await before_intent_commit()
    # This is an intentional saga boundary even for commit=False callers. The
    # canonical core intent must survive a process crash during physical stop;
    # commit=False continues to leave the later terminal publication to its
    # caller-owned transaction.
    await session.commit()

    incomplete: list[str] = []
    for job in cancellable:
        if not job.nextflow_run_id:
            continue
        try:
            stopped_and_empty = await cancel_nextflow_job(job.nextflow_run_id)
        except Exception as exc:
            logger.warning("[CANCEL] Failed to stop owned unit for %s: %s", job.id, exc)
            stopped_and_empty = False
        if not stopped_and_empty:
            incomplete.append(job.id)

    if incomplete:
        if commit:
            await session.commit()
        else:
            await session.flush()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANCELLATION_INCOMPLETE",
                "message": "Cancellation remains pending until every owned unit is inactive and its cgroup is empty",
                "job_ids": incomplete,
            },
        )

    completed_at = datetime.utcnow()
    terminal_conflicts: list[str] = []
    for job in [*cancellable, *already_cancelled]:
        await session.refresh(job)
        expected_status = (
            JobStatus.CANCELLED.value
            if str(job.id) not in cancellable_ids
            else intent_statuses[str(job.id)]
        )
        params = release_scheduler_gpu_assignment(job.params)
        receipt = dict(params.get("cancellation_receipt") or {})
        terminal_time = job.completed_at or completed_at
        receipt.update(
            {
                "schema": "bms.workflow-cancellation.v1",
                "state": "completed",
                "completed_at": terminal_time.isoformat() + "Z",
                "run_identity": str(job.nextflow_run_id or receipt.get("run_identity") or ""),
            }
        )
        params["cancellation_receipt"] = receipt
        predicates = [
            Job.id == job.id,
            Job.status == expected_status,
        ]
        if str(job.id) in cancellable_ids:
            predicates.extend(
                (
                    Job.queue_status == "cancelling",
                    Job.completed_at == intent_completed_at[str(job.id)],
                )
            )
        terminalized = await session.execute(
            update(Job)
            .where(*predicates)
            .values(
                status=JobStatus.CANCELLED.value,
                queue_status="cancelled",
                paused=False,
                assigned_gpu=None,
                awaiting_input=False,
                awaiting_stage=None,
                awaiting_payload={},
                retry_count=0,
                current_stage=None,
                stage_progress=None,
                completed_at=terminal_time,
                error_message=job.error_message or error_message,
                params=params,
            )
            .execution_options(synchronize_session=False)
        )
        if terminalized.rowcount != 1:
            terminal_conflicts.append(str(job.id))
        elif job.execution_target_id:
            await session.execute(
                update(ExecutionTarget)
                .where(
                    ExecutionTarget.id == str(job.execution_target_id),
                    ExecutionTarget.leased_job_id == str(job.id),
                )
                .values(
                    leased_job_id=None,
                    lease_acquired_at=None,
                    updated_at=completed_at,
                )
                .execution_options(synchronize_session=False)
            )

    if terminal_conflicts:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANCELLATION_TERMINAL_CONFLICT",
                "message": "Producer terminal state won before cancellation publication",
                "job_ids": terminal_conflicts,
            },
        )
    for job in [*cancellable, *already_cancelled]:
        await session.refresh(job)

    from services.rfd3_local_redesign import terminalize_cancelled_request_for_job

    for job in [*cancellable, *already_cancelled]:
        await terminalize_cancelled_request_for_job(session, job_id=str(job.id))

    if before_terminal_commit is not None:
        await before_terminal_commit()

    from services.conformational_mapping.persistence import (
        terminalize_cancelled_request_for_job as terminalize_cancelled_cm_request_for_job,
    )

    for job in [*cancellable, *already_cancelled]:
        await terminalize_cancelled_cm_request_for_job(session, job_id=str(job.id))

    if commit:
        await session.commit()
    else:
        await session.flush()
    return root_job, lineage


async def _force_launch_with_session(
    session: AsyncSession,
    job_id: str,
    gpu_id: int,
    allowed_queue_statuses: Sequence[str],
) -> Job:
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.queue_status not in allowed_queue_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be {', '.join(allowed_queue_statuses)} to force-run (current: {job.queue_status})",
        )

    params = json.loads(job.params) if isinstance(job.params, str) else dict(job.params or {})
    params["gpu_id"] = gpu_id
    params["operator_force_run"] = True

    # Manual placement is a scheduler input, not an alternate launcher.  Persist
    # the pin and return the job to the queue so the GPU orchestrator remains the
    # sole owner of admission, VRAM/concurrency checks, and process launch.
    job.params = params
    job.pinned_gpu = gpu_id
    job.queue_status = "queued"
    job.status = "queued"
    job.assigned_gpu = None
    job.started_at = None
    job.paused = False

    await session.commit()
    return job


async def force_launch_job(
    job_id: str,
    gpu_id: int,
    allowed_queue_statuses: Sequence[str],
    session: Optional[AsyncSession] = None,
) -> Job:
    if session is None:
        async with async_session() as session:
            return await _force_launch_with_session(session, job_id, gpu_id, allowed_queue_statuses)
    return await _force_launch_with_session(session, job_id, gpu_id, allowed_queue_statuses)

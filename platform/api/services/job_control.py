"""
Job control helpers shared across routers.
"""

from __future__ import annotations

from fastapi import BackgroundTasks
from database import Design, ExecutionTarget
from services.binder_continuation import _awaiting_stage_to_resume_hint, _build_antibody_iteration_job, _build_selected_loop_scope, _derive_source_stage_payload, _materialize_antibody_selection, _native_gate_resume_params, _resolve_antibody_root_job, _should_spawn_antibody_refinement_on_resume
from services.job_submission import _raise_if_workflow_launches_disabled, _require_prepared_remote_review, submit_job
from services import ont_submission_trust

from datetime import datetime
from typing import Awaitable, Callable, Iterable, Optional, Sequence
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, Job, MdRun
from schemas import JobStatus
from services.execution_ownership import release_scheduler_gpu_assignment
from services.nextflow import cancel_nextflow_job
from typing import List, Dict, Any
from pathlib import Path
from services.job_requests import _coerce_nonempty_text
from services.job_readback import resolve_output_dir

logger = logging.getLogger(__name__)

_ACTIVE_QUEUE_STATUSES = {"queued", "running", "paused", "pending_msa"}
_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.AWAITING_INPUT.value,
}


def visible_job_clause():
    """Public Job surfaces only; never apply to claims, recovery or path custody."""
    from sqlalchemy import func
    return func.coalesce(Job.params["permanent_delete_cleanup"].as_boolean(), False).is_(False)


def checkpoint_continuation_predicates(job: Job) -> tuple:
    """Use in the local Continue UPDATE, after native checkpoint validation.

    Match both the captured queue/params and absence of current cancel intent;
    a fresh read of a cancelling row must not authorize continuation either.
    """
    from sqlalchemy import func
    return (
        Job.id == job.id, Job.status == "awaiting_input", Job.awaiting_input.is_(True),
        Job.queue_status == job.queue_status, Job.queue_status != "cancelling",
        Job.params == job.params,
        func.coalesce(Job.params["cancellation_receipt"]["state"].as_string(), "").notin_(("requested", "completed")),
        Job.awaiting_payload == job.awaiting_payload, Job.provenance == job.provenance,
    )


async def retain_permanent_delete_cleanup(session: AsyncSession, job: Job) -> bool:
    """Retain the SAME cancelled Job as hidden cleanup custody, not new work.

    The delete route removes user records but must exclude this row and its
    output paths from physical deletion when True. List/detail routes hide the
    params marker. The existing reconciler drains this owner, then removes
    its unused output paths and the hidden row.
    """
    from services.remote_execution.claims import outstanding_claim_clause
    await session.refresh(job)
    if not job.execution_target_id:
        return False
    outstanding = await session.scalar(select(Job.id).where(Job.id == job.id, outstanding_claim_clause()))
    receipt = (job.params or {}).get("cancellation_receipt") or {}
    if not outstanding and receipt.get("remote_stop_verified") is True:
        return False
    params = dict(job.params or {})
    params["permanent_delete_cleanup"] = True
    changed = await session.execute(update(Job).where(
        Job.id == job.id, Job.status == job.status, Job.params == job.params,
        Job.provenance == job.provenance, Job.remote_attempt_id == job.remote_attempt_id,
    ).values(params=params, parent_job_id=None, status="cancelled", queue_status="cancelled",
             awaiting_input=False, awaiting_stage=None, awaiting_payload={}).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(status_code=409, detail="Deletion owner changed during cleanup handoff")
    await session.refresh(job)
    return True


async def cleanup_permanent_delete_outputs(session: AsyncSession, job: Job) -> None:
    """Reuse the permanent route's directory planner, protecting overlapping custody.

    Leave this Job in the transaction until filesystem cleanup succeeds so a
    controller interruption or filesystem error is retried by reconciliation.
    Project artifact custody is independent and is never part of Job deletion.
    """
    from pathlib import Path
    import shutil
    from experiment_operations import _controlled_root
    from services.scientific_artifacts.writer import artifact_root

    refs = await _load_remaining_output_dir_refs(session, {str(job.id)})
    candidates, _ = _plan_output_dir_cleanup([job.output_dir, job.child_output_dir], refs)
    protected = [Path(path) for path in refs]
    protected.extend((_controlled_root("BMS_EXPERIMENT_ARTIFACT_ROOT", "experiment-artifacts").resolve(),
                      artifact_root().resolve()))
    for raw in candidates:
        path = Path(raw)
        if any(path == other or path in other.parents or other in path.parents for other in protected):
            continue
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass


async def finalize_permanent_delete_cleanup(session: AsyncSession, job_id: str) -> bool:
    """Finish requested deletion only after the existing cancellation owner drains.

    The existing attempt guard excludes concurrent local transfers; the writer
    transaction keeps output references stable through cleanup and row removal.
    No new stop proof or user-facing admission condition is introduced.
    """
    from database import Design
    from services.remote_execution.claims import outstanding_claim_clause
    from services.remote_execution.executor import _controller_attempt_guard

    with _controller_attempt_guard(job_id) as owned:
        if not owned:
            return False
        # Acquire SQLite's writer fence before loading references or touching bytes.
        result = await session.execute(update(Job).where(
            Job.id == job_id, Job.params["permanent_delete_cleanup"].as_boolean().is_(True),
            Job.status == "cancelled", Job.remote_state == "cancelled",
            Job.params["cancellation_receipt"]["state"].as_string() == "completed",
            ~outstanding_claim_clause(),
        ).values(queue_status="cancelled").execution_options(synchronize_session=False))
        if result.rowcount != 1:
            await session.rollback()
            return False
        job = await session.get(Job, job_id, populate_existing=True)
        await cleanup_permanent_delete_outputs(session, job)
        await session.execute(Design.__table__.delete().where(Design.job_id == job_id))
        await session.delete(job)
        await session.commit()
        return True


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


async def pause_job(job_id: str, session: AsyncSession) -> Job:
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id, visible_job_clause()))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.queue_status not in ['queued', 'running']:
        raise HTTPException(status_code=400, detail=f"Cannot pause job with status: {job.queue_status}")
    if job.execution_target_id and job.queue_status == 'running':
        raise HTTPException(
            status_code=409,
            detail="Running remote Jobs cannot be paused; cancel them or wait for completion",
        )

    job.paused = True
    job.queue_status = 'paused'
    await session.commit()
    return job


async def resume_job(job_id: str, session: AsyncSession) -> Job:
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id, visible_job_clause()))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.paused:
        raise HTTPException(status_code=400, detail="Job is not paused")

    job.paused = False
    job.queue_status = 'queued'
    await session.commit()
    return job


async def release_job_gpu(job_id: str, session: AsyncSession) -> tuple[Job, int | None]:
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id, visible_job_clause()))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.execution_target_id:
        raise HTTPException(
            status_code=409,
            detail="Remote Jobs release their GPU only through remote terminal reconciliation",
        )

    released_gpu = job.assigned_gpu
    job.assigned_gpu = None
    await session.commit()
    return job, released_gpu


async def retry_job(job_id: str, session: AsyncSession) -> Job:
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id, visible_job_clause()))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    launch_context_id = (getattr(job, "provenance", None) or {}).get("launch_context_id")
    if launch_context_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_BOUND_REORCHESTRATION_REQUIRED",
                "message": (
                    "Project-bound Jobs must be retried through the owning Domain Run Group "
                    "with a replacement preparation, attempt, and launch context."
                ),
                "launch_context_id": launch_context_id,
            },
        )

    if job.queue_status != 'failed':
        raise HTTPException(status_code=400, detail="Can only retry failed jobs")

    if job.retry_count >= job.max_retries:
        raise HTTPException(status_code=400, detail=f"Max retries ({job.max_retries}) exceeded")

    job.retry_count += 1
    job.status = 'queued'
    job.queue_status = 'queued'
    job.paused = False
    job.error_message = None
    job.assigned_gpu = None
    job.started_at = None
    job.completed_at = None
    job.current_stage = None
    job.stage_progress = None
    if getattr(job, "execution_target_id", None):
        provenance = dict(getattr(job, "provenance", None) or {})
        prior_receipt = provenance.pop("remote_execution_receipt", None)
        provenance.pop("remote_execution_assignment", None)
        if isinstance(prior_receipt, dict):
            history = list(provenance.get("remote_execution_attempt_history") or [])
            history.append(prior_receipt)
            provenance["remote_execution_attempt_history"] = history
        job.provenance = provenance
        job.nextflow_run_id = None
        job.remote_attempt_id = None
        job.execution_bundle_sha256 = None
        job.remote_state = "queued"
        job.params = release_scheduler_gpu_assignment(job.params)
    await session.commit()
    return job


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

    # Capture the authority whose writer stop is about to be observed. Refresh
    # after I/O must not turn proof for an old attempt into successor authority.
    remote_fences = {}
    for job in [*cancellable, *already_cancelled]:
        if job.execution_target_id:
            fields = ("execution_target_id", "remote_attempt_id", "nextflow_run_id",
                      "execution_source_revision", "execution_source_tree",
                      "execution_bundle_sha256", "provenance")
            remote_fences[str(job.id)] = {field: getattr(job, field) for field in fields}

    # Operator cancellation is terminal on request, but terminal UI state is
    # not writer quiescence. Retain remote leases for the existing reconciler.
    remote_stop_unverified: list[str] = []
    stopped_run_ids: set[str] = set()
    for job in cancellable:
        if not job.nextflow_run_id:
            continue
        try:
            stopped_and_empty = await cancel_nextflow_job(job.nextflow_run_id)
        except Exception as exc:
            logger.warning("[CANCEL] Failed to stop owned unit for %s: %s", job.id, exc)
            stopped_and_empty = False
        if not stopped_and_empty:
            remote_stop_unverified.append(str(job.id))
        else:
            stopped_run_ids.add(str(job.id))
    if remote_stop_unverified:
        logger.warning(
            "[CANCEL] Terminalizing with unverified remote stop; reconciler owns remote cleanup: %s",
            remote_stop_unverified,
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
                # Whether the owned unit reported inactive-and-empty before the
                # terminal publication. False means the reconciler still owns
                # remote cleanup for this job.
                "remote_stop_verified": (
                    str(job.id) in stopped_run_ids
                    or (str(job.id) not in cancellable_ids and
                        receipt.get("remote_stop_verified") is True)
                ),
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
        remote_fence = remote_fences.get(str(job.id))
        if remote_fence:
            # Terminal UI publication cannot acquire a changed attempt.
            predicates.extend(getattr(Job, field) == value for field, value in remote_fence.items())
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
        if (not job.execution_target_id and job.output_dir
                and str(job.id) in stopped_run_ids):
            # The existing cancellation owner has proved the local writer stopped
            # and won terminal publication. Observe bytes without success handling.
            from services.nextflow import observe_local_terminal_binder_results

            await observe_local_terminal_binder_results(session, str(job.id), job.output_dir)

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

    if job.awaiting_stage == "remote_results" or job.remote_state in {
        "results_available", "returning", "result_pull_failed"
    }:
        raise HTTPException(status_code=409, detail="Remote execution is finished; use Pull results")

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


def _normalize_output_dir_reference(value: Any) -> Optional[str]:
    text = _coerce_nonempty_text(value)
    if not text:
        return None

    output_path = resolve_output_dir(text)
    if output_path is None:
        return None

    try:
        return str(output_path.expanduser().resolve(strict=False))
    except TypeError:
        return str(output_path.expanduser().resolve())


async def _load_remaining_output_dir_refs(
    session: AsyncSession,
    excluded_job_ids: set[str],
) -> Dict[str, List[Dict[str, str]]]:
    rows = (
        await session.execute(
            select(Job.id, Job.name, Job.output_dir, Job.child_output_dir, Job.source_selection_manifest_path)
        )
    ).all()

    refs: Dict[str, List[Dict[str, str]]] = {}
    for job_id, job_name, output_dir, child_output_dir, selection_manifest in rows:
        if str(job_id) in excluded_job_ids:
            continue

        for field_name, raw_path in (
            ("output_dir", output_dir),
            ("child_output_dir", child_output_dir),
            # Ordinary successors own selected inputs even when those bytes
            # are retained beneath the deleted producer's old output root.
            ("source_selection_manifest_path", selection_manifest),
        ):
            normalized = _normalize_output_dir_reference(raw_path)
            if not normalized:
                continue
            refs.setdefault(normalized, []).append(
                {
                    "job_id": str(job_id),
                    "job_name": str(job_name or ""),
                    "field": field_name,
                }
            )

    return refs


def _plan_output_dir_cleanup(
    candidate_dirs: List[str | None],
    remaining_refs: Dict[str, List[Dict[str, str]]],
) -> tuple[List[str], List[Dict[str, Any]]]:
    deletable: List[str] = []
    preserved: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for candidate in candidate_dirs:
        normalized = _normalize_output_dir_reference(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        candidate_path = Path(normalized)
        refs = [ref for raw, owners in remaining_refs.items()
                if (candidate_path == Path(raw) or candidate_path in Path(raw).parents
                    or Path(raw) in candidate_path.parents) for ref in owners]
        if refs:
            preserved.append(
                {
                    "path": normalized,
                    "referenced_by": refs,
                }
            )
            continue

        deletable.append(normalized)

    return deletable, preserved


def _resume_defaults_from_awaiting_payload(payload: Optional[Dict[str, Any]]) -> tuple[dict[str, Any], Optional[str], Optional[str]]:
    payload_dict = payload if isinstance(payload, dict) else {}

    param_overrides = payload_dict.get("resume_param_overrides")
    if not isinstance(param_overrides, dict):
        param_overrides = {}

    from_stage = payload_dict.get("resume_from_stage")
    if isinstance(from_stage, str):
        from_stage = from_stage.strip() or None
    else:
        from_stage = None

    name_suffix = payload_dict.get("resume_name_suffix")
    if isinstance(name_suffix, str):
        name_suffix = name_suffix.strip() or None
    else:
        name_suffix = None

    return dict(param_overrides), from_stage, name_suffix


async def resubmit_failed_job(job_id, session, placement, *, authorize, grant_access):
    _raise_if_workflow_launches_disabled("resubmit workflow jobs")
    await reject_generic_md_lifecycle_control(job_id, session)
    # Find original job
    result = await session.execute(select(Job).where(Job.id == job_id))
    original_job = result.scalar_one_or_none()

    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    if original_job.model_id == "nanopore":
        if not authorize(original_job.id, original_job.provenance):
            raise HTTPException(status_code=403, detail="alignment access denied")
    launch_context_id = (original_job.provenance or {}).get("launch_context_id")
    if launch_context_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_BOUND_REORCHESTRATION_REQUIRED",
                "message": (
                    "Project-bound Jobs must be resubmitted through the owning Domain Run Group "
                    "with a new preparation, attempt, and launch context."
                ),
                "launch_context_id": launch_context_id,
            },
        )

    # Only allow resubmit for failed or cancelled jobs
    if original_job.status not in [JobStatus.FAILED.value, JobStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resubmit job with status: {original_job.status}. Only failed or cancelled jobs can be resubmitted."
        )

    from services.job_submission import create_resubmitted_job
    return await create_resubmitted_job(
        original_job, job_id, session, placement, grant_access,
    )


async def resume_workflow_job(job_id, session, request, from_stage, background_tasks, *, authorize, grant_access):
    from services import core_protein_scientific_contract as scientific_contract
    try:
        if request is not None:
            scientific_contract.reject_reserved_marker(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _raise_if_workflow_launches_disabled("resume workflow jobs")
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.model_id == "nanopore":
        if not authorize(job.id, job.provenance):
            raise HTTPException(status_code=403, detail="alignment access denied")

    if job.awaiting_stage == "remote_results" or job.remote_state in {
        "results_available", "returning", "result_pull_failed"
    }:
        raise HTTPException(status_code=409, detail="Remote execution is finished; use Pull results, not Resume")

    checkpoint = (job.awaiting_payload or {}).get("component_checkpoint")
    if checkpoint:
        if not job.awaiting_input or job.status != "awaiting_input":
            raise HTTPException(status_code=409, detail="Checkpoint is not available for a new continuation")
        if (request is None or request.checkpoint_id != checkpoint.get("checkpoint_id")
                or request.checkpoint_sha256 != checkpoint.get("checkpoint_sha256")
                or not request.checkpoint_decision):
            raise HTTPException(status_code=422, detail="Explicit artifact-set-bound checkpoint decision required")
        if request.param_overrides or request.from_stage or from_stage:
            raise HTTPException(status_code=422, detail="Checkpoint continuation uses the bound native stage/settings")
        from services.remote_execution.executor import (prepare_checkpoint_successor,
            checkpoint_successor_id, RemoteExecutionError)
        selected_target = (request.execution_target_id if "execution_target_id" in request.model_fields_set
                           else job.execution_target_id)
        binding = dict(source_job_id=job.id, checkpoint_id=request.checkpoint_id,
            checkpoint_sha256=request.checkpoint_sha256, decision=request.checkpoint_decision,
            execution_target_id=selected_target, name_suffix=request.name_suffix or "_continued")
        try:
            existing_id = checkpoint_successor_id(job, binding)
            if existing_id:
                if not await session.scalar(select(Job.id).where(Job.id == existing_id, visible_job_clause())):
                    raise RemoteExecutionError("Checkpoint successor is no longer available")
                return dict(original_job_id=job.id, new_job_id=existing_id,
                    execution_target_id=selected_target, resume_stage_mode="checkpoint_successor")
            if selected_target != job.execution_target_id:
                prepared = await prepare_checkpoint_successor(session, job, checkpoint,
                    request.checkpoint_decision, selected_target, name_suffix=binding["name_suffix"])
                prepared.execution_policy = request.execution_policy
                prepared.launch_context_id = request.launch_context_id
                context = dict(original_job_id=job.id, execution_target_id=selected_target,
                    resume_stage_mode="checkpoint_successor")
                _require_prepared_remote_review(prepared, context)
                launched = await submit_job(prepared, background_tasks or BackgroundTasks(), session)
                return {**context, "new_job_id": launched.id}
        except RemoteExecutionError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job.execution_target_id:
            from services.remote_execution.executor import request_remote_checkpoint_resume, RemoteExecutionError
            try:
                return await request_remote_checkpoint_resume(session, job, checkpoint, request.checkpoint_decision)
            except RemoteExecutionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        from services.nextflow import local_checkpoint_resume
        provenance = dict(job.provenance or {})
        pending = dict(checkpoint_id=request.checkpoint_id, checkpoint_sha256=request.checkpoint_sha256,
                       decision=request.checkpoint_decision, attempt_id=checkpoint.get("attempt_id"))
        job.provenance = {**provenance, "component_checkpoint_resume": pending}
        try:
            retained = local_checkpoint_resume(job)
            _, context, runtime, trusted_checkpoint, _ = retained
            state = runtime.root_state()
            if (not state or state['state'] != 'paused' or not state.get('quiescent')
                    or state.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
                raise ValueError('Local checkpoint requires same-boot quiescent owner')
            from services.nextflow import compile_component_checkpoint_continuation
            compile_component_checkpoint_continuation(context, trusted_checkpoint, request.checkpoint_decision)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            job.provenance = provenance
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Only an ordinary scheduler claim can supply a continuation lease.
        from sqlalchemy import update
        values = dict(status="queued", queue_status="queued", paused=False, assigned_gpu=None,
                      started_at=None, completed_at=None, nextflow_run_id=None,
                      pinned_gpu=(context.get('resources') or {}).get('gpu_id'),
                      awaiting_input=False, provenance=dict(job.provenance))
        job.provenance = provenance
        with session.no_autoflush:
            claimed = await session.execute(update(Job).where(
                *checkpoint_continuation_predicates(job),
                Job.decision_history == job.decision_history,
            ).values(**values).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            await session.rollback()
            raise HTTPException(status_code=409, detail='Checkpoint owner changed while queueing')
        await session.commit()
        return dict(job_id=job.id, checkpoint_id=request.checkpoint_id, state='queued', execution_target_id=None)

    if job.status not in ["failed", "cancelled", JobStatus.AWAITING_INPUT.value] and not job.awaiting_input:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume job with status: {job.status}"
        )

    # Resolve placement before filesystem writes or domain continuation side effects.
    source_target_id = job.execution_target_id or None
    execution_target_id = source_target_id
    if request and "execution_target_id" in request.model_fields_set:
        execution_target_id = request.execution_target_id
        if execution_target_id is not None:
            execution_target_id = execution_target_id.strip()
            if not execution_target_id:
                raise HTTPException(status_code=422, detail="Use null to select Local execution")
    target_changed = execution_target_id != source_target_id
    # Mirrors StructurePredictionUiState's GPU launcher model/mode mapping.
    placement_supported = (
        job.model_id in {"boltz2", "protenix", "esmfold2"} and job.mode in {"predict", "complex"}
    ) or (job.model_id == "boltz_cp_experimental" and job.mode == "design") or job.model_id == "nanopore"
    fresh_execution_supported = (
        placement_supported
        and job.status in {"failed", "cancelled"}
        and not job.awaiting_input
        and (not (job.parent_job_id or job.child_stage) or (
            job.model_id == "nanopore" and bool((job.params or {}).get("barcode_mapping_binding")
                                              or (job.params or {}).get("pooled_assignment_release_binding"))))
    )
    if target_changed and not fresh_execution_supported:
        raise HTTPException(
            status_code=422,
            detail="Execution placement changes require a terminal structure root; child and interactive continuations must retain their target",
        )
    cached_ont = job.model_id == "nanopore" and not target_changed
    execution_source_revision = job.execution_source_revision if cached_ont else None
    execution_source_tree = job.execution_source_tree if cached_ont else None
    source_changed = False
    if execution_target_id:
        from services.remote_execution.targets import target_eligible
        target = await session.get(ExecutionTarget, execution_target_id, populate_existing=True)
        if target is None or not target_eligible(target):
            raise HTTPException(status_code=422, detail="execution_target_id is not an active ready execution target")
        if not cached_ont and not target_changed and (not job.execution_source_revision or not job.execution_source_tree):
            raise HTTPException(status_code=409, detail="Remote source Job is missing its immutable source identity")
        try:
            from services.remote_execution.bundle import current_source_identity
            if not cached_ont:
                execution_source_revision, execution_source_tree = current_source_identity()
        except Exception as exc:
            logger.exception("Unable to capture the re-orchestrated Job source identity")
            raise HTTPException(status_code=503, detail="Committed BMS source identity is unavailable") from exc
        source_changed = bool(source_target_id) and (
            execution_source_revision != job.execution_source_revision
            or execution_source_tree != job.execution_source_tree
        )
        if source_changed and not fresh_execution_supported:
            raise HTTPException(
                status_code=409,
                detail="BMS source changed; child, interactive, and unsupported-domain continuations cannot reuse historical execution state. Submit a new supported root job with the current source instead.",
            )
    fresh_execution = target_changed or source_changed

    completed = job.completed_stages or []
    # Relaxed restriction: Allow resume even if no stages completed (start from scratch with cache)

    # Allow body payload to override query-provided from_stage.
    effective_from_stage = (request.from_stage if request and request.from_stage else from_stage)
    if isinstance(effective_from_stage, str):
        effective_from_stage = effective_from_stage.strip() or None
    requested_overrides = dict(request.param_overrides) if request else {}
    requested_overrides.pop("permanent_delete_cleanup", None)
    if "execution_target_id" in requested_overrides:
        raise HTTPException(status_code=422, detail="execution_target_id must be a top-level resume field")
    requested_name_suffix = request.name_suffix if request else None

    # Prevent callers from overriding resume control fields directly.
    reserved_resume_keys = {"resume_job_id", "resume_work_dir", "resume_source_dir", "resume_stage_work_dir"}
    payload_resume_overrides: dict[str, Any] = {}
    payload_resume_from_stage: Optional[str] = None
    payload_name_suffix: Optional[str] = None
    if job.awaiting_input:
        payload_resume_overrides, payload_resume_from_stage, payload_name_suffix = _resume_defaults_from_awaiting_payload(
            job.awaiting_payload
        )
    if job.model_id == "nanopore":
        forbidden_resume_overrides = sorted(set(requested_overrides) | set(payload_resume_overrides))
        if forbidden_resume_overrides:
            raise HTTPException(
                status_code=422,
                detail="Nanopore resume does not accept parameter overrides: " + ", ".join(forbidden_resume_overrides),
            )
        reserved_resume_keys |= ont_submission_trust.ONT_SERVER_CONTROLLED_PARAMS
    param_overrides = {
        key: value
        for key, value in requested_overrides.items()
        if key not in reserved_resume_keys
    }
    payload_resume_overrides = {
        key: value
        for key, value in payload_resume_overrides.items()
        if key not in reserved_resume_keys
    }
    if not effective_from_stage and payload_resume_from_stage:
        effective_from_stage = payload_resume_from_stage
    if not requested_name_suffix and payload_name_suffix:
        requested_name_suffix = payload_name_suffix
    merged_resume_defaults = dict(payload_resume_overrides)
    merged_resume_defaults.update(param_overrides)
    param_overrides = merged_resume_defaults

    param_overrides, effective_from_stage = _native_gate_resume_params(job, param_overrides, effective_from_stage)

    if _should_spawn_antibody_refinement_on_resume(job):
        if background_tasks is None:
            background_tasks = BackgroundTasks()

        source_job, root_job = await _resolve_antibody_root_job(session, job.id)
        stage_design_result = await session.execute(
            select(Design)
            .where(
                Design.job_id == job.id,
                Design.source_stage == job.awaiting_stage,
            )
            .order_by(Design.created_at.asc(), Design.name.asc())
        )
        selected_designs = stage_design_result.scalars().all()
        resume_source_hint = _awaiting_stage_to_resume_hint(job.awaiting_stage) or "generator"
        resume_source_label = "BoltzGen" if resume_source_hint == "boltzgen" else "PPIFlow"

        if not selected_designs:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{resume_source_label} review outputs are not materialized yet. "
                    "Open the job in Results Viewer once review rows appear, or retry after the gate payload refreshes."
                ),
            )

        design_ids = [design.id for design in selected_designs]
        selection_dir = _materialize_antibody_selection(root_job, source_job, selected_designs, "continue_review")
        launch_request = _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action="ui_refinement",
            selection_dir=selection_dir,
            design_ids=design_ids,
            name_suffix=requested_name_suffix or "continued",
            param_overrides=param_overrides,
            selected_designs=selected_designs,
        )
        if isinstance(launch_request.params, dict):
            source_stage_payload = _derive_source_stage_payload(source_job, selected_designs, selection_dir)
            launch_request.params.update({
                "lineage_root_job_id": root_job.id,
                "stage_family": launch_request.params.get("stage_family"),
                "stage_mode": launch_request.params.get("stage_mode"),
                "selection_source_type": "review_gate",
                "selection_source_job_id": source_job.id,
                "selection_dataset_name": None,
                "selected_loop_scope": _build_selected_loop_scope(launch_request.params),
                "interactive_gate_continue": True,
                **source_stage_payload,
            })

        _require_prepared_remote_review(launch_request, {
            'message': f'{resume_source_label} review resumed into Antibody Refinement.',
            'original_job_id': job_id, 'resume_from_stage': resume_source_hint,
            'resume_stage_mode': 'spawn_refinement', 'preserved_stages': [],
            'resume_stage_note': f'Paused {resume_source_label} review launches a refinement-compatible follow-on job using the filtered review cohort.',
            'applied_overrides': sorted(param_overrides.keys()),
        })
        launched_job = await submit_job(launch_request, background_tasks, session)
        history = list(job.decision_history or [])
        history.append({
            "stage": job.awaiting_stage,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "new_job_id": launched_job.id,
            "from_stage": resume_source_hint,
            "applied_overrides": sorted(param_overrides.keys()),
            "resume_mode": "spawn_refinement",
        })
        job.decision_history = history
        await session.commit()

        return {
            "message": f"{resume_source_label} review resumed into Antibody Refinement.",
            "original_job_id": job_id,
            "new_job_id": launched_job.id,
            "new_job_name": launched_job.name,
            "resume_from_stage": resume_source_hint,
            "resume_stage_mode": "spawn_refinement",
            "resume_stage_note": f"Paused {resume_source_label} review launches a refinement-compatible follow-on job using the filtered review cohort.",
            "preserved_stages": [],
            "applied_overrides": sorted(param_overrides.keys()),
        }

    # Only cached continuations retain their original declaration; fresh
    # re-orchestration re-admits below before directories or rows are written.
    trusted_declaration = (job.provenance or {}).get('fampnn_analysis_declaration')
    if not fresh_execution and trusted_declaration is not None:
        from services.fampnn_policy_admission import guard_cached_declaration
        try:
            guard_cached_declaration(trusted_declaration, job.params or {}, param_overrides)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    from services.job_submission import create_resumed_job
    return await create_resumed_job(
        job, job_id, session, requested_name_suffix, param_overrides, effective_from_stage, fresh_execution,
         target_changed, source_changed, execution_target_id, execution_source_revision,
         execution_source_tree, grant_access,
    )

"""Job stage callbacks, review gates and child progress."""
from __future__ import annotations

import logging
from fastapi import APIRouter

import json
import re
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from fastapi import Body, Depends, HTTPException, Request
from schemas import CandidateRef, JobStatus
from database import Job, get_session
from pathlib import Path
from services.job_readback import SavedReviewFilterSet, _resolve_stage_state_for_response, _saved_review_filter_entries
from services.job_submission import _candidate_child_batch_aliases
from services.job_requests import _coerce_nonempty_text, _dedupe_preserve_order
from datetime import datetime
from services.stage_review import ensure_stage_review_rows, gate_file_for_stage, nextflow_history_status_for_run_dir, refresh_gate_payload, resolve_nextflow_run_dir
from sqlalchemy import func, or_, select, update
from services.job_stage_progress import project_execution_stages
from services.job_control import reject_generic_md_lifecycle_control
from services import stage_reporting


router = APIRouter()
logger = logging.getLogger(__name__)


UUID_SUFFIX_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _write_gate_snapshot(job: Job) -> None:
    if not job.awaiting_stage or not isinstance(job.awaiting_payload, dict):
        return
    gate_file = gate_file_for_stage(job)
    if gate_file is None:
        return
    gate_file.parent.mkdir(parents=True, exist_ok=True)
    gate_file.write_text(json.dumps({
        "awaiting_stage": job.awaiting_stage,
        "awaiting_payload": job.awaiting_payload,
        "written_at": datetime.utcnow().isoformat() + "Z",
    }, indent=2))


def _canonical_child_batch_key(batch_name: Any, parent_job_id: Any = None) -> str:
    batch_text = _coerce_nonempty_text(batch_name)
    if batch_text:
        match = UUID_SUFFIX_RE.search(batch_text)
        if match:
            return match.group(1).lower()
        return batch_text

    parent_text = _coerce_nonempty_text(parent_job_id)
    if parent_text:
        return parent_text.lower()
    return ""


def _logical_child_key(child: Job) -> tuple[str, str, str]:
    batch_key = _canonical_child_batch_key(child.batch_name, child.parent_job_id) or child.id
    stage_key = _coerce_nonempty_text(child.child_stage) or ""
    params = child.params if isinstance(child.params, dict) else {}
    for key_name in ("job_index", "batch_index"):
        raw_value = params.get(key_name)
        if raw_value in (None, ""):
            continue
        return batch_key, stage_key, f"{key_name}:{raw_value}"
    name_key = _coerce_nonempty_text(child.name) or child.id
    return batch_key, stage_key, name_key


def _child_progress_rank(child: Job) -> tuple[int, int]:
    progress = _coerce_nonempty_text(getattr(child, "stage_progress", None)) or ""
    match = re.search(r"(\d+)\s*/\s*(\d+)", progress)
    if not match:
        return (0, 0)
    try:
        completed = int(match.group(1))
        total = int(match.group(2))
    except (TypeError, ValueError):
        return (0, 0)
    return (completed, total if total > 0 else 0)


def _child_attempt_rank(child: Job) -> tuple[int, int, int, datetime]:
    status = str(getattr(child, "status", "") or "").strip().lower()
    status_rank = {
        "completed": 5,
        "running": 4,
        "awaiting_input": 4,
        "queued": 3,
        "pending": 3,
        "failed": 2,
        "cancelled": 1,
    }.get(status, 0)
    completed, total = _child_progress_rank(child)
    created_at = child.created_at or datetime.min
    return status_rank, completed, total, created_at


def _dedupe_child_attempts(children: List[Job]) -> List[Job]:
    latest_by_key: Dict[tuple[str, str, str], Job] = {}
    for child in children:
        key = _logical_child_key(child)
        current = latest_by_key.get(key)
        if current is None:
            latest_by_key[key] = child
            continue

        if _child_attempt_rank(child) >= _child_attempt_rank(current):
            latest_by_key[key] = child

    return list(latest_by_key.values())


def _reconcile_child_jobs_from_history(children: List[Job]) -> int:
    """
    Opportunistically reconcile stale child-job statuses from Nextflow history.

    Spawn/wait parent tasks poll this endpoint live. If child jobs have already
    exited `OK`/`ERR` but their DB status is still `running`, the parent can hang
    indefinitely. On read, upgrade those stale child records to terminal state.
    """
    if not children:
        return 0

    stale = [
        child for child in children
        if child.status in {
            JobStatus.RUNNING.value,
            JobStatus.AWAITING_INPUT.value,
        }
    ]
    if not stale:
        return 0

    updated = 0
    for child in stale:
        run_dir = resolve_nextflow_run_dir(child.child_output_dir or child.output_dir)
        if not run_dir:
            continue
        status_token = nextflow_history_status_for_run_dir(run_dir, str(child.id))
        if status_token == "OK":
            child.status = JobStatus.COMPLETED.value
            child.queue_status = "completed"
            child.awaiting_input = False
            child.awaiting_stage = None
            child.awaiting_payload = {}
            resolved_output_dir = str(run_dir)
            if child.child_output_dir != resolved_output_dir:
                child.child_output_dir = resolved_output_dir
            if not child.completed_at:
                child.completed_at = datetime.utcnow()
            child.current_stage = "Complete"
            updated += 1
        elif status_token == "ERR":
            child.status = JobStatus.FAILED.value
            child.queue_status = "failed"
            child.awaiting_input = False
            child.awaiting_stage = None
            child.awaiting_payload = {}
            resolved_output_dir = str(run_dir)
            if child.child_output_dir != resolved_output_dir:
                child.child_output_dir = resolved_output_dir
            if not child.completed_at:
                child.completed_at = datetime.utcnow()
            if not child.error_message:
                child.error_message = "Reconciled from .nextflow/history (ERR)"
            updated += 1
    return updated


class OpenStageGateRequest(BaseModel):
    """Internal workflow request to mark a job as awaiting user input."""
    payload: Dict[str, Any] = Field(default_factory=dict)


class SaveReviewFilterSetRequest(BaseModel):
    """Persist a named frozen review dataset on the parent job."""
    name: Optional[str] = None
    visible_count: Optional[int] = None
    source_total_count: Optional[int] = None
    design_ids: List[str] = Field(default_factory=list)
    filter_state: Dict[str, Any] = Field(default_factory=dict)
    candidate_refs: list[CandidateRef] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    ranking: Dict[str, Any] = Field(default_factory=dict)
    final_designation: Dict[str, Any] | None = None


class SaveReviewFilterSetResponse(BaseModel):
    message: str
    filter_set: SavedReviewFilterSet
    filter_sets: List[SavedReviewFilterSet]


class DeleteReviewFilterSetResponse(BaseModel):
    message: str
    filter_sets: List[SavedReviewFilterSet]


async def _resolve_child_lineage_context(
    session: AsyncSession,
    parent_id: str,
    batch_name: Optional[str] = None,
) -> tuple[Optional[Job], List[str], List[str], str]:
    """
    Resolve the full parent lineage and child batch aliases for resume-aware
    child lookups.

    Legacy antibody runs stored child jobs under ``antibody_batch_<root_id>``,
    while newer resumes reconstruct ``<job_name>_<root_id>``. Querying only the
    current batch_name misses the original children entirely. Use the stored
    resume root plus any observed child batch names across that lineage.
    """
    from sqlalchemy import or_

    parent_job = await session.get(Job, parent_id)
    if parent_job is None:
        return None, [parent_id], _dedupe_preserve_order([batch_name] if batch_name else []), parent_id

    parent_params = parent_job.params if isinstance(parent_job.params, dict) else {}
    root_job_id = _coerce_nonempty_text(parent_params.get("resume_root_job_id")) or parent_id

    lineage_result = await session.execute(
        select(Job.id).where(
            or_(
                Job.id == root_job_id,
                func.json_extract(Job.params, "$.resume_root_job_id") == root_job_id,
            )
        )
    )
    parent_ids = _dedupe_preserve_order(
        [parent_id, root_job_id]
        + [str(job_id) for job_id in lineage_result.scalars().all() if _coerce_nonempty_text(job_id)]
    )

    batch_aliases = _candidate_child_batch_aliases(
        job=parent_job,
        root_job_id=root_job_id,
        provided_batch_name=batch_name,
    )
    if parent_ids:
        batch_rows = await session.execute(
            select(Job.batch_name)
            .where(
                Job.parent_job_id.in_(parent_ids),
                Job.batch_name.isnot(None),
            )
            .order_by(Job.created_at.asc())
        )
        batch_aliases = _dedupe_preserve_order(
            batch_aliases
            + [batch for batch in batch_rows.scalars().all() if _coerce_nonempty_text(batch)]
        )

    return parent_job, parent_ids, batch_aliases, root_job_id


def _normalize_design_ids(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        design_id = value.strip()
        if not design_id or design_id in seen:
            continue
        seen.add(design_id)
        normalized.append(design_id)
    return normalized


def _persist_saved_review_filter_entries(job: Job, entries: List[Dict[str, Any]]) -> None:
    pruned_entries = list(entries[:50])
    job.saved_selection_sets = pruned_entries

    if isinstance(job.awaiting_payload, dict):
        payload = dict(job.awaiting_payload or {})
        payload["review_filter_sets"] = pruned_entries
        job.awaiting_payload = refresh_gate_payload(payload, job.output_dir)
        _write_gate_snapshot(job)


def _iter_saved_review_filter_sets(job: Optional[Job]) -> List[SavedReviewFilterSet]:
    saved_sets: List[SavedReviewFilterSet] = []
    for entry in _saved_review_filter_entries(job):
        try:
            saved_sets.append(SavedReviewFilterSet.model_validate(entry))
        except Exception:
            continue
    return saved_sets


def _anchor_dorado_demux_products(job: Job) -> dict[str, Any]:
    from services.ont_ngs_completion import prepare_dorado_demux_products
    root = Path(str(job.output_dir or "")).expanduser()
    return prepare_dorado_demux_products(job, read_root=root, persisted_root=root.absolute())


def _stage_callback_has_authority(job: Job) -> bool:
    if job.awaiting_input:
        return False
    if job.status == "running" and job.queue_status == "running":
        return True
    # Stage reports are metadata, not a started/completed Job publication. An
    # authenticated worker may report immediately after spawn, before its SSH
    # start response arrives. Only the current immutable launch may do this.
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    return bool(
        job.execution_target_id and job.status == "queued" and job.queue_status == "preparing"
        and job.remote_state in {"launch_requested", "launch_uncertain"}
        and job.remote_attempt_id
        and job.nextflow_run_id == f"remote:{job.remote_attempt_id}"
        and receipt.get("attempt_id") == job.remote_attempt_id
    )


async def _publish_generic_stage_terminal(
    *,
    session: AsyncSession,
    job_id: str,
    stage: str,
    status: str,
    outputs: list[str],
    token: str,
) -> dict[str, Any]:
    terminal = {"status": status, "outputs": list(outputs)}
    for _attempt in range(4):
        result = await session.execute(select(Job).where(Job.id == job_id))
        current = result.scalar_one_or_none()
        if current is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not stage_reporting.token_is_authorized(current.provenance, token):
            raise HTTPException(status_code=403, detail="invalid workflow stage credential")
        if not _stage_callback_has_authority(current):
            raise HTTPException(status_code=409, detail="workflow stage callback lost active-job authority")

        original_completed = current.completed_stages
        original_outputs = current.stage_outputs
        original_provenance = current.provenance
        original_current_stage = current.current_stage
        original_stage_progress = current.stage_progress
        completed = list(original_completed or [])
        stage_outputs = dict(original_outputs or {})
        provenance = dict(original_provenance or {})
        terminal_states = dict(provenance.get("stage_terminal_states") or {})
        existing_terminal = terminal_states.get(stage)
        if existing_terminal is not None and existing_terminal != terminal:
            raise HTTPException(status_code=409, detail="workflow stage terminal state is immutable")
        if status == "complete":
            if stage not in completed:
                completed.append(stage)
        elif stage in completed:
            raise HTTPException(status_code=409, detail="completed workflow stage cannot be reclassified")
        stage_outputs[stage] = list(outputs)
        terminal_states[stage] = terminal
        provenance["stage_terminal_states"] = terminal_states
        current_stage = None if original_current_stage == stage else original_current_stage

        predicates = [
            Job.id == job_id,
            Job.status == current.status,
            Job.queue_status == current.queue_status,
            *([
                Job.remote_attempt_id == current.remote_attempt_id,
                Job.nextflow_run_id == current.nextflow_run_id,
                Job.remote_state == current.remote_state,
            ] if getattr(current, "execution_target_id", None) else []),
            Job.awaiting_input.is_(False),
            Job.completed_stages.is_(None) if original_completed is None else Job.completed_stages == original_completed,
            Job.stage_outputs.is_(None) if original_outputs is None else Job.stage_outputs == original_outputs,
            Job.provenance.is_(None) if original_provenance is None else Job.provenance == original_provenance,
            Job.current_stage.is_(None) if original_current_stage is None else Job.current_stage == original_current_stage,
            Job.stage_progress.is_(None) if original_stage_progress is None else Job.stage_progress == original_stage_progress,
        ]
        session.expunge(current)
        published = await session.execute(
            update(Job)
            .where(*predicates)
            .values(
                completed_stages=completed,
                stage_outputs=stage_outputs,
                provenance=provenance,
                current_stage=current_stage,
            )
        )
        if published.rowcount == 1:
            await session.commit()
            return {
                "message": f"Stage '{stage}' marked {status}",
                "job_id": job_id,
                "stage": stage,
                "status": status,
                "completed_stages": completed,
                "outputs_count": len(outputs),
            }
        await session.rollback()
    raise HTTPException(status_code=409, detail="workflow stage callback conflicted with concurrent publication")


@router.post("/{job_id}/stage-complete")
async def report_stage_complete(
    job_id: str,
    request: Request,
    stage: str,
    outputs: List[str] = [],
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has completed.
    Called by Nextflow workflows after each stage finishes.
    """
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id).with_for_update())
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    authorization = str(request.headers.get("authorization") or "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not stage_reporting.token_is_authorized(job.provenance, token):
        raise HTTPException(status_code=403, detail="invalid workflow stage credential")

    if job.model_id == "nanopore" and stage == "clone_validation":
        stage = "wf_clone_validation"

    if stage == "dorado_demux":
        provenance = dict(job.provenance or {})
        callback_digest = str(provenance.get(stage_reporting.PROVENANCE_DIGEST_KEY) or "")
        existing = provenance.get("ont_dorado_terminal_products")
        if existing is None and job.status != "running":
            raise HTTPException(status_code=409, detail="terminal Dorado products can be anchored only by an active workflow")
        anchor = _anchor_dorado_demux_products(job)
        if existing is not None and existing != anchor:
            raise HTTPException(status_code=409, detail="terminal Dorado product anchor is immutable")
        provenance["ont_dorado_terminal_products"] = anchor
        # The demux callback is the terminal trust transition for a barcoded
        # basecall job. Revoke its launch-scoped credential in the same commit
        # that persists the immutable anchor so the transition is single-use.
        provenance.pop(stage_reporting.PROVENANCE_DIGEST_KEY, None)
        completed = list(job.completed_stages or [])
        if stage not in completed:
            completed.append(stage)
        stage_outputs = dict(job.stage_outputs or {})
        stage_outputs[stage] = outputs
        published = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING.value,
                Job.provenance[stage_reporting.PROVENANCE_DIGEST_KEY].as_string() == callback_digest,
            )
            .values(
                provenance=provenance,
                completed_stages=completed,
                stage_outputs=stage_outputs,
                current_stage=None,
            )
        )
        await session.commit()
        if published.rowcount != 1:
            raise HTTPException(status_code=409, detail="terminal Dorado anchor transition was already consumed")
        logger.info(f"Job {job_id}: Stage '{stage}' completed with {len(outputs)} outputs")
        return {
            "message": f"Stage '{stage}' marked complete",
            "job_id": job_id,
            "completed_stages": completed,
            "outputs_count": len(outputs),
        }
    
    response = await _publish_generic_stage_terminal(
        session=session,
        job_id=job_id,
        stage=stage,
        status="complete",
        outputs=list(outputs),
        token=token,
    )
    logger.info("Job %s: Stage '%s' completed with %s outputs", job_id, stage, len(outputs))
    return response


@router.post("/{job_id}/stage-terminal")
async def report_stage_terminal(
    job_id: str,
    request: Request,
    stage: str,
    status: str,
    outputs: List[str] = [],
    session: AsyncSession = Depends(get_session),
):
    """Persist a non-success terminal state for an optional or unrequested stage."""

    if status not in {"failed", "not_requested"}:
        raise HTTPException(status_code=422, detail="unsupported workflow stage terminal status")
    result = await session.execute(select(Job).where(Job.id == job_id).with_for_update())
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    authorization = str(request.headers.get("authorization") or "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not stage_reporting.token_is_authorized(job.provenance, token):
        raise HTTPException(status_code=403, detail="invalid workflow stage credential")

    response = await _publish_generic_stage_terminal(
        session=session,
        job_id=job_id,
        stage=stage,
        status=status,
        outputs=list(outputs),
        token=token,
    )
    logger.info("Job %s: Stage '%s' terminal state is %s", job_id, stage, status)
    return response


@router.post("/{job_id}/stage-gates/{stage}/open")
async def open_stage_gate(
    job_id: str,
    stage: str,
    request: Optional[OpenStageGateRequest] = Body(default=None),
    session: AsyncSession = Depends(get_session)
):
    """Mark a job as awaiting user input at a named workflow gate."""
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.remote_attempt_id or (job.awaiting_payload or {}).get("component_checkpoint"):
        raise HTTPException(status_code=409,
            detail="Component review gates are published by the owned runtime checkpoint, not host callbacks")
    payload = dict(request.payload or {}) if request else {}
    payload["stage"] = stage
    payload = refresh_gate_payload(payload, job.output_dir)

    job.awaiting_input = True
    job.awaiting_stage = stage
    job.awaiting_payload = payload
    job.current_stage = stage
    _write_gate_snapshot(job)
    await session.commit()

    # Results views only read review rows (they no longer create them on GET), so the
    # worker that opens the gate publishes them. Best effort: the gate is already open.
    try:
        await ensure_stage_review_rows(session, job)
    except Exception:
        logger.exception("Job %s: review rows for gate '%s' were not published; the gate stays open", job_id, stage)
        await session.rollback()

    logger.info("Job %s opened interactive gate '%s'", job_id, stage)

    return {
        "message": f"Stage gate '{stage}' opened",
        "job_id": job_id,
        "awaiting_stage": stage,
        "awaiting_payload": payload,
    }


@router.get("/{job_id}/stage-gates")
async def get_stage_gates(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Return the current interactive gate state for a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job_id,
        "awaiting_input": bool(job.awaiting_input),
        "awaiting_stage": job.awaiting_stage,
        "awaiting_payload": job.awaiting_payload or {},
        "decision_history": job.decision_history or [],
    }


@router.post("/{job_id}/review-filter-sets", response_model=SaveReviewFilterSetResponse)
async def save_review_filter_set(
    job_id: str,
    request: SaveReviewFilterSetRequest,
    session: AsyncSession = Depends(get_session),
):
    """Persist a named frozen review dataset for review or completed generator workflows."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if request.candidate_refs:
        from services.binder_continuation import resolve_root
        from services.binder_selected_round import resolve_ref
        _, root = await resolve_root(session, job.id)
        try:
            for ref in request.candidate_refs:
                await resolve_ref(session, root, ref, snapshot=False)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
    filter_sets = _saved_review_filter_entries(job)

    filter_name = str(request.name or "").strip() or f"Saved dataset {len(filter_sets) + 1}"
    saved_entry = {
        "id": str(uuid.uuid4()),
        "name": filter_name,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "visible_count": request.visible_count,
        "source_total_count": request.source_total_count,
        "design_ids": _normalize_design_ids(request.design_ids),
        "filter_state": dict(request.filter_state or {}),
        "candidate_refs": [r.model_dump(mode="json", exclude_none=True) for r in request.candidate_refs],
        "revision": request.revision, "ranking": dict(request.ranking),
        "final_designation": request.final_designation,
    }
    filter_sets.insert(0, saved_entry)
    _persist_saved_review_filter_entries(job, filter_sets)
    await session.commit()

    saved_models = _iter_saved_review_filter_sets(job)
    return SaveReviewFilterSetResponse(
        message=f"Saved review dataset '{filter_name}'.",
        filter_set=SavedReviewFilterSet.model_validate(saved_entry),
        filter_sets=saved_models,
    )


@router.delete("/{job_id}/review-filter-sets/{filter_set_id}", response_model=DeleteReviewFilterSetResponse)
async def delete_review_filter_set(
    job_id: str,
    filter_set_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a saved review dataset from a review or completed generator job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    filter_sets = _saved_review_filter_entries(job)
    next_sets = [entry for entry in filter_sets if str(entry.get("id") or "") != filter_set_id]
    if len(next_sets) == len(filter_sets):
        raise HTTPException(status_code=404, detail="Saved review dataset not found")

    _persist_saved_review_filter_entries(job, next_sets)
    await session.commit()

    return DeleteReviewFilterSetResponse(
        message="Deleted saved review dataset.",
        filter_sets=_iter_saved_review_filter_sets(job),
    )


@router.get("/{parent_id}/children/status")
async def get_children_status(
    parent_id: str,
    stage: Optional[str] = None,
    batch_name: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Get aggregate status of all children spawned by a parent job.
    
    Used by wait_for_children.py to poll until all children complete.
    
    Args:
        parent_id: Parent job ID to search for children
        stage: Optional filter by child_stage (e.g., 'rfantibody', 'fampnn', 'boltz2')
        batch_name: Optional batch_name to search (for resume scenarios where new parent
                    ID differs from original but batch_name is preserved)
    
    Resume Support:
        When a job is resumed, it gets a new parent_id but keeps the same batch_name.
        By passing batch_name, we can find children from the original run even when
        the parent_id has changed.
    
    Returns:
        - total: Total child count
        - completed/failed/running/pending: Status breakdown
        - all_done: True when all children finished (completed or failed)
        - child_output_dirs: List of output directories for aggregation
        - success_rate: Percentage of children that completed successfully
    """
    from sqlalchemy import or_

    _, parent_ids, batch_aliases, _ = await _resolve_child_lineage_context(
        session,
        parent_id,
        batch_name=batch_name,
    )

    filters = []
    if parent_ids:
        filters.append(Job.parent_job_id.in_(parent_ids))
    if batch_aliases:
        filters.append(Job.batch_name.in_(batch_aliases))

    query = select(Job)
    if filters:
        query = query.where(or_(*filters))
    else:
        query = query.where(Job.parent_job_id == parent_id)
    if stage:
        query = query.where(Job.child_stage == stage)

    from services.job_control import visible_job_clause
    result = await session.execute(query.where(visible_job_clause()))
    children = result.scalars().all()

    if not children:
        return {
            "parent_id": parent_id,
            "stage": stage,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "running": 0,
            "pending": 0,
            "all_done": True,
            "child_output_dirs": [],
            "child_output_dirs_all": [],
            "success_rate": 100.0
        }

    # Collapse multiple attempts for the same logical child slot down to the
    # latest attempt so resume/retry bookkeeping stays deterministic.
    deduped_children = _dedupe_child_attempts(children)

    completed = [c for c in deduped_children if c.status == "completed"]
    failed = [c for c in deduped_children if c.status == "failed"]
    cancelled = [c for c in deduped_children if c.status == "cancelled"]
    running = [c for c in deduped_children if c.status == "running"]
    pending = [c for c in deduped_children if c.status in ["queued", "pending"]]

    all_done = all(c.status in ["completed", "failed", "cancelled"] for c in deduped_children)

    def _dedupe_preserve_order(values: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out

    all_output_dirs = _dedupe_preserve_order(
        [
            c.child_output_dir or c.output_dir
            for c in completed
            if (c.child_output_dir or c.output_dir)
        ]
    )
    resumed_lineage_has_foreign_completions = bool(
        batch_name and any((c.parent_job_id or "") != parent_id for c in completed)
    )
    if resumed_lineage_has_foreign_completions:
        # A resumed parent must be able to recollect outputs from completed
        # children that belong to an earlier parent attempt in the same batch.
        output_dirs = list(all_output_dirs)
    else:
        # Default collection set excludes already-aggregated children.
        output_dirs = _dedupe_preserve_order(
            [
                c.child_output_dir or c.output_dir
                for c in completed
                if (c.child_output_dir or c.output_dir) and not c.aggregated_by_parent
            ]
        )

    total = len(deduped_children)
    success_rate = (len(completed) / total * 100) if total > 0 else 0

    return {
        "parent_id": parent_id,
        "stage": stage,
        "total": total,
        "completed": len(completed),
        "failed": len(failed),
        "cancelled": len(cancelled),
        "running": len(running),
        "pending": len(pending),
        "all_done": all_done,
        "child_output_dirs": output_dirs,
        "child_output_dirs_all": all_output_dirs,
        "success_rate": round(success_rate, 1),
        "child_ids": [c.id for c in deduped_children],
        "children": [
            {
                "job_id": c.id,
                "name": c.name,
                "status": c.status,
                "parent_job_id": c.parent_job_id,
                "batch_name": c.batch_name,
                "output_dir": c.child_output_dir or c.output_dir,
                "stage_work_dir": c.stage_work_dir,
                "stage_progress": c.stage_progress,
                "aggregated_by_parent": bool(c.aggregated_by_parent),
                "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
                "completed_at": c.completed_at.isoformat() + "Z" if c.completed_at else None,
                "job_index": (c.params or {}).get("job_index") if isinstance(c.params, dict) else None,
                "batch_index": (c.params or {}).get("batch_index") if isinstance(c.params, dict) else None,
                "assigned_gpu": c.assigned_gpu,
                "pinned_gpu": c.pinned_gpu,
            }
            for c in deduped_children
        ],
    }


@router.post("/{parent_id}/children/mark-aggregated")
async def mark_children_aggregated(
    parent_id: str,
    stage: Optional[str] = None,
    batch_name: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Mark all completed children as aggregated by parent.
    Prevents double-collection when polling multiple times.
    """
    from sqlalchemy import or_

    await reject_generic_md_lifecycle_control(parent_id, session)

    _, parent_ids, batch_aliases, _ = await _resolve_child_lineage_context(
        session,
        parent_id,
        batch_name=batch_name,
    )
    filters = []
    if parent_ids:
        filters.append(Job.parent_job_id.in_(parent_ids))
    if batch_aliases:
        filters.append(Job.batch_name.in_(batch_aliases))

    query = select(Job).where(
        Job.status == "completed",
        Job.aggregated_by_parent == False
    )
    if filters:
        query = query.where(or_(*filters))
    else:
        query = query.where(Job.parent_job_id == parent_id)
    
    if stage:
        query = query.where(Job.child_stage == stage)
    
    result = await session.execute(query)
    children = result.scalars().all()
    
    for child in children:
        child.aggregated_by_parent = True
    
    await session.commit()
    
    return {
        "marked_count": len(children),
        "parent_id": parent_id,
        "stage": stage,
        "batch_name": batch_name,
    }


async def _publish_generic_stage_start(
    *,
    session: AsyncSession,
    job_id: str,
    stage: str,
    token: str,
) -> dict[str, Any]:
    if not stage or len(stage) > 128:
        raise HTTPException(status_code=422, detail="workflow stage identity is invalid")
    for _attempt in range(4):
        result = await session.execute(select(Job).where(Job.id == job_id))
        current = result.scalar_one_or_none()
        if current is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not stage_reporting.token_is_authorized(current.provenance, token):
            raise HTTPException(status_code=403, detail="invalid workflow stage credential")
        if not _stage_callback_has_authority(current):
            raise HTTPException(status_code=409, detail="workflow stage callback lost active-job authority")

        original_current_stage = current.current_stage
        original_stage_progress = current.stage_progress
        original_completed = current.completed_stages
        original_outputs = current.stage_outputs
        original_provenance = current.provenance
        terminal_states = (
            original_provenance.get("stage_terminal_states")
            if isinstance(original_provenance, dict)
            else None
        )
        if (
            (isinstance(terminal_states, dict) and stage in terminal_states)
            or stage in (original_completed or [])
        ):
            raise HTTPException(status_code=409, detail="terminal workflow stage cannot restart")
        if original_current_stage == stage:
            return {"message": f"Stage '{stage}' started", "job_id": job_id}

        predicates = [
            Job.id == job_id,
            Job.status == current.status,
            Job.queue_status == current.queue_status,
            *([
                Job.remote_attempt_id == current.remote_attempt_id,
                Job.nextflow_run_id == current.nextflow_run_id,
                Job.remote_state == current.remote_state,
            ] if getattr(current, "execution_target_id", None) else []),
            Job.awaiting_input.is_(False),
            Job.current_stage.is_(None) if original_current_stage is None else Job.current_stage == original_current_stage,
            Job.stage_progress.is_(None) if original_stage_progress is None else Job.stage_progress == original_stage_progress,
            Job.completed_stages.is_(None) if original_completed is None else Job.completed_stages == original_completed,
            Job.stage_outputs.is_(None) if original_outputs is None else Job.stage_outputs == original_outputs,
            Job.provenance.is_(None) if original_provenance is None else Job.provenance == original_provenance,
        ]
        session.expunge(current)
        published = await session.execute(
            update(Job).where(*predicates).values(current_stage=stage)
        )
        if published.rowcount == 1:
            await session.commit()
            return {"message": f"Stage '{stage}' started", "job_id": job_id}
        await session.rollback()
    raise HTTPException(status_code=409, detail="workflow stage start conflicted with concurrent publication")


@router.post("/{job_id}/stage-start")
async def report_stage_start(
    job_id: str,
    request: Request,
    stage: str,
    session: AsyncSession = Depends(get_session)
):
    """Publish one authenticated workflow stage start through snapshot CAS."""

    await reject_generic_md_lifecycle_control(job_id, session)
    authorization = str(request.headers.get("authorization") or "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=403, detail="invalid workflow stage credential")
    response = await _publish_generic_stage_start(
        session=session,
        job_id=job_id,
        stage=stage,
        token=token,
    )
    logger.info("Job %s: Stage '%s' started", job_id, stage)
    return response


@router.get("/{job_id}/stages")
async def get_job_stages(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get stage progress for a job.
    Returns current stage, completed stages, and output paths.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    execution_stages = project_execution_stages(job)
    all_stages = [stage['id'] for stage in execution_stages]
    completed, stage_outputs = _resolve_stage_state_for_response(job)

    return {
        "job_id": job_id,
        "mode": job.mode,
        "all_stages": all_stages,
        "execution_stages": execution_stages,
        "current_stage": job.awaiting_stage if job.awaiting_input and job.awaiting_stage else job.current_stage,
        "completed_stages": completed,
        "stage_outputs": stage_outputs,
        # Allow resume if failed/cancelled, even if no stages fully completed (rely on cache)
        "can_resume": job.status in ["failed", "cancelled", JobStatus.AWAITING_INPUT.value] or bool(job.awaiting_input)
    }

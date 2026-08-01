"""Result-ingestion finalization and conservative repair of stale job state.

The helpers in this module intentionally use existing Job fields.  Integrity detail is
stored under ``Job.provenance.result_integrity`` so old databases do not need a schema
migration to distinguish validated completion from partial/failed ingestion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job


@dataclass(frozen=True)
class FinalizationResult:
    completed: bool
    design_count: int
    integrity_state: str


@dataclass(frozen=True)
class RepairChange:
    code: str
    record_type: str
    record_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    detail: str
    disposition: str = "repair"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairReport:
    applied: bool
    apply_requested: bool
    changes: list[RepairChange]

    def to_dict(self) -> dict[str, Any]:
        unresolved_count = sum(change.disposition == "unresolved" for change in self.changes)
        superseded_count = sum(change.disposition == "superseded" for change in self.changes)
        applied_change_count = sum(change.disposition == "repair" for change in self.changes)
        return {
            # "applied" means at least one guarded publication reached the database,
            # never merely that --apply was requested.
            "applied": self.applied,
            "apply_requested": self.apply_requested,
            "change_count": len(self.changes),
            "applied_change_count": applied_change_count if self.applied else 0,
            "unresolved_count": unresolved_count,
            "superseded_count": superseded_count,
            "changes": [change.to_dict() for change in self.changes],
        }


def _integrity_provenance(job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    provenance = dict(job.provenance) if isinstance(job.provenance, dict) else {}
    provenance["result_integrity"] = payload
    return provenance


def job_expects_design_results(job: Job) -> bool:
    """Return whether a successful workflow is expected to publish Design rows."""
    params = job.params if isinstance(job.params, dict) else {}
    explicit = params.get("result_integrity_requires_designs")
    if isinstance(explicit, bool):
        return explicit

    def normalized_identifier(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")

    aliases = {
        "af2": "alphafold2",
        "af_3": "af3",
        "alpha_fold": "alphafold",
        "alpha_fold2": "alphafold2",
        "alpha_fold_3": "alphafold3",
        "boltz_2": "boltz2",
        "esm_fold": "esmfold",
        "protein_mpnn": "proteinmpnn",
        "rf_diffusion": "rfdiffusion",
    }
    model = normalized_identifier(job.model_id)
    mode = normalized_identifier(job.mode)
    model = aliases.get(model, model)
    mode = aliases.get(mode, mode)
    design_model_ids = {
        "af3",
        "alphafold",
        "alphafold2",
        "alphafold3",

        "binder_design",
        "boltz2",
        "boltz_cp_experimental",
        "boltzgen",
        "chai",
        "confornets_experimental",
        "esmfold",
        "esmfold2",
        "esmfold2_experimental",
        "fampnn",
        "frustrampnn",
        "ligandmpnn",
        "ppiflow",
        "protein_local_redesign",
        "protein_hunter_experimental",
        "protein_modification_experimental",
        "proteinmpnn",
        "protenix",
        "rf3",
        "rfantibody",
        "rfdiffusion",
        "rfdesign",
    }
    return model in design_model_ids or mode == "structure_prediction"


async def _design_count(session: AsyncSession, job_id: str) -> int:
    """Count direct designs plus immediate-child designs shown for a parent."""
    return int(
        (
            await session.scalar(
                select(func.count(Design.id))
                .outerjoin(Job, Design.job_id == Job.id)
                .where(or_(Design.job_id == job_id, Job.parent_job_id == job_id))
            )
        )
        or 0
    )


async def _existing_designs_are_usable(
    session: AsyncSession, job_id: str, output_dir: str
) -> bool:
    """Validate direct and immediate-child artifacts against each owning job root."""
    rows = list(
        (
            await session.execute(
                select(Design, Job)
                .join(Job, Design.job_id == Job.id)
                .where(or_(Design.job_id == job_id, Job.parent_job_id == job_id))
            )
        ).all()
    )
    if not rows:
        return False
    for design, owner in rows:
        artifact_root = owner.child_output_dir or owner.output_dir or output_dir
        if not artifact_root or not str(design.name or "").strip() or not str(design.pdb_path or "").strip():
            return False
        output_root = Path(artifact_root).expanduser().resolve()
        pdb_path = Path(str(design.pdb_path).strip()).expanduser()
        resolved = pdb_path.resolve() if pdb_path.is_absolute() else (output_root / pdb_path).resolve()
        if not resolved.is_relative_to(output_root) or not resolved.is_file():
            return False
    return True


async def _binder_design_rows_are_authoritative(
    session: AsyncSession, job_id: str, output_dir: str
) -> bool:
    rows = list(
        (
            await session.execute(
                select(Design, Job)
                .join(Job, Design.job_id == Job.id)
                .where(or_(Design.job_id == job_id, Job.parent_job_id == job_id))
            )
        ).all()
    )
    if not rows:
        return False
    for design, owner in rows:
        if (
            design.review_profile_id != "binder_design_v1"
            or design.artifact_class != "validated_binder_complex"
            or not isinstance(design.ipsae, (int, float))
            or not math.isfinite(float(design.ipsae))
            or not 0.0 <= float(design.ipsae) <= 1.0
            or not str(design.aligned_error_format or "").strip()
            or not str(design.aligned_error_path or "").strip()
        ):
            return False
        artifact_root = owner.child_output_dir or owner.output_dir or output_dir
        if not artifact_root:
            return False
        output_root = Path(artifact_root).expanduser().resolve()
        pae_path = Path(str(design.aligned_error_path)).expanduser()
        resolved_pae = pae_path.resolve() if pae_path.is_absolute() else (output_root / pae_path).resolve()
        if not resolved_pae.is_relative_to(output_root) or not resolved_pae.is_file():
            return False
    return True


async def finalize_successful_job(
    job: Job,
    output_dir: str,
    session: AsyncSession,
    *,
    ingest_fn: Optional[Callable[..., Awaitable[int]]] = None,
    epitope_residues: Optional[list[str]] = None,
) -> FinalizationResult:
    """Ingest, validate, and commit results before exposing terminal completion."""
    job_id = str(job.id)
    if ingest_fn is None:
        from services.result_ingester import ingest_job_results

        ingest_fn = ingest_job_results

    # Use conditional DB transitions rather than stale ORM assignments.  Cancellation
    # and review gates are authoritative even if they change between awaits.
    await session.refresh(job)
    if job.status == "cancelled" or job.awaiting_input:
        state = "cancelled" if job.status == "cancelled" else "awaiting_input"
        return FinalizationResult(False, await _design_count(session, job_id), state)

    running_transition = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.queue_status == "running",
            Job.awaiting_input.is_(False),
        )
        .values(status="running", queue_status="running", completed_at=None, error_message=None)
    )
    if running_transition.rowcount != 1:
        await session.rollback()
        job = await session.get(Job, job_id)
        state = "cancelled" if job is not None and job.status == "cancelled" else "awaiting_input"
        return FinalizationResult(False, await _design_count(session, job_id), state)
    await session.refresh(job)

    try:
        ingested_count = await ingest_fn(
            job_id,
            output_dir,
            session,
            epitope_residues=epitope_residues,
            commit=False,
        )
        count = await _design_count(session, job_id)
        idempotent_prior_results = False
        if job_expects_design_results(job):
            if count == 0:
                raise RuntimeError("workflow completed but result ingestion produced no designs")
            usable_results = await _existing_designs_are_usable(session, job_id, output_dir)
            if not usable_results:
                raise RuntimeError("workflow result rows lack usable, contained PDB artifacts")
            if str(job.model_id or "").strip().lower() == "binder_design":
                authoritative = await _binder_design_rows_are_authoritative(session, job_id, output_dir)
                if not authoritative:
                    raise RuntimeError(
                        "binder workflow completed without authoritative PAE-derived ipSAE result rows"
                    )
            idempotent_prior_results = int(ingested_count or 0) <= 0
    except Exception as exc:
        await session.rollback()
        job = await session.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"job disappeared during result finalization: {job_id}") from exc
        if job.status == "cancelled":
            return FinalizationResult(False, await _design_count(session, job_id), "cancelled")
        count = await _design_count(session, job_id)
        partial = count > 0
        message = str(exc) or exc.__class__.__name__
        integrity_state = str(getattr(exc, "integrity_state", "ingestion_failed"))
        no_candidates = integrity_state == "no_candidates" and count == 0
        details = {
            "state": integrity_state if no_candidates else "ingestion_failed",
            "partial": partial,
            "design_count": count,
            "error": message,
        }
        if no_candidates:
            details["reason"] = getattr(exc, "reason", {"code": "no_candidates", "message": message})
        failure_provenance = _integrity_provenance(
            job,
            details,
        )
        failure = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "running",
                Job.queue_status == "running",
                Job.awaiting_input.is_(False),
            )
            .values(
                status="failed",
                queue_status="failed",
                paused=False,
                assigned_gpu=None,
                current_stage="No Candidates" if no_candidates else "Result Ingestion Failed",
                stage_progress=None,
                completed_at=datetime.utcnow(),
                error_message=(f"No candidates: {message}" if no_candidates else f"Result ingestion failed: {message}"),
                provenance=failure_provenance,
            )
        )
        if failure.rowcount != 1:
            await session.rollback()
            job = await session.get(Job, job_id)
            state = "cancelled" if job is not None and job.status == "cancelled" else "awaiting_input"
            return FinalizationResult(False, count, state)
        await session.commit()
        await session.refresh(job)
        return FinalizationResult(False, count, "no_candidates" if no_candidates else "ingestion_failed")

    # Ingesters may commit internally.  Publish completion with a conditional DB
    # update: a cancellation or review gate committed after ingestion wins.
    await session.refresh(job)
    provenance = _integrity_provenance(
        job,
        {
            "state": "validated",
            "partial": False,
            "design_count": count,
            "idempotent_prior_results": idempotent_prior_results,
        },
    )
    completion = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.queue_status == "running",
            Job.awaiting_input.is_(False),
        )
        .values(
            status="completed",
            queue_status="completed",
            paused=False,
            assigned_gpu=None,
            current_stage="Complete",
            stage_progress=None,
            error_message=None,
            completed_at=datetime.utcnow(),
            provenance=provenance,
        )
    )
    if completion.rowcount != 1:
        await session.rollback()
        job = await session.get(Job, job_id)
        state = "cancelled" if job is not None and job.status == "cancelled" else "awaiting_input"
        return FinalizationResult(False, count, state)
    await session.commit()
    await session.refresh(job)
    return FinalizationResult(True, count, "validated")


def _job_state(job: Job) -> dict[str, Any]:
    return {
        "parent_job_id": job.parent_job_id,
        "status": job.status,
        "queue_status": job.queue_status,
        "awaiting_input": bool(job.awaiting_input),
        "awaiting_stage": job.awaiting_stage,
        "awaiting_payload": job.awaiting_payload or {},
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "paused": bool(job.paused),
        "assigned_gpu": job.assigned_gpu,
        "retry_count": job.retry_count,
        "current_stage": job.current_stage,
        "stage_progress": job.stage_progress,
        "error_message": job.error_message,
        "provenance": job.provenance,
    }


def _set_integrity_failure(after: dict[str, Any], job: Job, *, error: str, partial: bool, design_count: int) -> None:
    after.update(
        status="failed",
        queue_status="failed",
        awaiting_input=False,
        awaiting_stage=None,
        awaiting_payload={},
        paused=False,
        clear_assigned_gpu=True,
        retry_count=0,
        current_stage="Result Integrity Failed",
        stage_progress=None,
        error_message=error,
    )
    # Do not manufacture completion time: derive it from persisted fields so
    # report/apply runs are deterministic and idempotent.
    timestamp = getattr(job, "updated_at", None) or job.created_at
    if timestamp is not None:
        after["set_completed_at"] = timestamp
    provenance = dict(job.provenance) if isinstance(job.provenance, dict) else {}
    provenance["result_integrity"] = {
        "state": "ingestion_failed",
        "partial": partial,
        "design_count": design_count,
        "error": error,
    }
    after["provenance"] = provenance


def _job_state_values(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return only the state deltas a repair intentionally publishes."""
    fields = (
        "status", "queue_status", "awaiting_input", "awaiting_stage", "awaiting_payload",
        "paused", "retry_count", "current_stage", "stage_progress", "error_message",
        "provenance", "parent_job_id",
    )
    values = {
        field: after[field]
        for field in fields
        if field in after and after[field] != before.get(field)
    }
    if after.get("clear_completed_at") and before.get("completed_at") is not None:
        values["completed_at"] = None
    if after.get("clear_assigned_gpu") and before.get("assigned_gpu") is not None:
        values["assigned_gpu"] = None
    if "set_completed_at" in after:
        values["completed_at"] = after["set_completed_at"]
    return values


def _job_state_guards(job: Job) -> tuple[Any, ...]:
    """Require the complete repair-relevant snapshot to remain unchanged."""
    fields = (
        "parent_job_id", "status", "queue_status", "awaiting_input", "awaiting_stage",
        "awaiting_payload", "completed_at", "paused", "assigned_gpu", "retry_count",
        "current_stage", "stage_progress", "error_message", "provenance",
    )
    predicates = []
    for field in fields:
        column = getattr(Job, field)
        value = getattr(job, field)
        predicates.append(column.is_(None) if value is None else column == value)
    return tuple(predicates)


def _apply_job_state(job: Job, after: dict[str, Any]) -> None:
    for field in (
        "status",
        "queue_status",
        "awaiting_input",
        "awaiting_stage",
        "awaiting_payload",
        "paused",
        "retry_count",
        "current_stage",
        "stage_progress",
        "error_message",
        "provenance",
        "parent_job_id",
    ):
        if field in after:
            setattr(job, field, after[field])
    if after.get("clear_completed_at"):
        job.completed_at = None
    if after.get("clear_assigned_gpu"):
        job.assigned_gpu = None
    if "set_completed_at" in after:
        job.completed_at = after["set_completed_at"]


async def repair_result_state(session: AsyncSession, *, apply: bool = False) -> RepairReport:
    """Plan or apply idempotent repairs for contradictory result/job state."""
    jobs = list((await session.execute(select(Job).order_by(Job.created_at, Job.id))).scalars().all())
    job_ids = {str(job.id) for job in jobs}
    raw_design_counts = {
        str(job_id): int(count)
        for job_id, count in (
            await session.execute(select(Design.job_id, func.count(Design.id)).group_by(Design.job_id))
        ).all()
    }
    # Only credit results the viewer can actually open.  This applies both to a
    # job's direct rows and to immediate child rows presented under its parent.
    design_counts: dict[str, int] = {}
    for job in jobs:
        job_id = str(job.id)
        if raw_design_counts.get(job_id, 0) <= 0:
            continue
        artifact_root = job.child_output_dir or job.output_dir
        if artifact_root and await _existing_designs_are_usable(session, job_id, artifact_root):
            design_counts[job_id] = raw_design_counts[job_id]
    # The Data Viewer presents immediate child designs as parent results. Mirror
    # that established visibility contract only after validating child artifacts.
    for child in jobs:
        if child.parent_job_id:
            parent_id = str(child.parent_job_id)
            design_counts[parent_id] = design_counts.get(parent_id, 0) + design_counts.get(str(child.id), 0)
    changes: list[RepairChange] = []
    pending_repairs: list[tuple[int, Job, dict[str, Any], dict[str, Any]]] = []

    for job in jobs:
        before = _job_state(job)
        after = dict(before)
        code: Optional[str] = None
        detail = ""

        missing_parent = bool(job.parent_job_id and str(job.parent_job_id) not in job_ids)
        disposition = "repair"

        if job.status == "cancelled" and (
            missing_parent
            or job.queue_status != "cancelled"
            or job.awaiting_input
            or job.awaiting_stage is not None
            or bool(job.awaiting_payload)
            or job.paused
            or job.assigned_gpu is not None
            or (job.retry_count or 0) != 0
            or job.current_stage is not None
            or job.stage_progress is not None
        ):
            code = "cancelled_queue_mismatch"
            detail = "cancelled state is authoritative over orphan, queued/retry, and awaiting-input state"
            after.update(
                status="cancelled",
                queue_status="cancelled",
                awaiting_input=False,
                awaiting_stage=None,
                awaiting_payload={},
                paused=False,
                clear_assigned_gpu=True,
                retry_count=0,
                current_stage=None,
                stage_progress=None,
            )
            if missing_parent:
                after["parent_job_id"] = None
        elif missing_parent:
            code = "orphan_child"
            detail = f"parent job {job.parent_job_id} does not exist"
            missing_parent_id = str(job.parent_job_id)
            _set_integrity_failure(
                after,
                job,
                error=f"State repair: orphan child references missing parent {missing_parent_id}",
                partial=design_counts.get(str(job.id), 0) > 0,
                design_count=design_counts.get(str(job.id), 0),
            )
            after["parent_job_id"] = None
        elif job.queue_status == "queued" and job.status not in {"queued", "cancelled"}:
            code = "retry_state_mismatch"
            detail = "a requeued retry must clear terminal state"
            after.update(
                status="queued",
                error_message=None,
                clear_completed_at=True,
                clear_assigned_gpu=True,
            )
        elif job.awaiting_input and not (job.awaiting_payload or {}):
            from services.stage_review import load_review_gate_snapshot

            stage, payload = load_review_gate_snapshot(
                job.child_output_dir or job.output_dir,
                job.awaiting_stage,
            )
            code = "missing_awaiting_payload"
            detail = "restored awaiting review payload from the persisted gate snapshot"
            if payload:
                after.update(
                    status="awaiting_input",
                    queue_status="completed",
                    awaiting_stage=stage or job.awaiting_stage,
                    awaiting_payload=payload,
                )
            else:
                _set_integrity_failure(
                    after,
                    job,
                    error="State repair: awaiting-input job has no persisted review payload",
                    partial=design_counts.get(str(job.id), 0) > 0,
                    design_count=design_counts.get(str(job.id), 0),
                )
                detail = "no gate snapshot exists; marked explicit integrity failure"
        elif job.status == "completed":
            count = design_counts.get(str(job.id), 0)
            if job_expects_design_results(job) and count == 0:
                code = "completed_without_results"
                detail = "completed design workflow has no ingested designs"
                _set_integrity_failure(
                    after,
                    job,
                    error="State repair: completed workflow has no ingested designs/results",
                    partial=False,
                    design_count=0,
                )
            else:
                if job.completed_at is None:
                    timestamp = getattr(job, "updated_at", None) or job.created_at
                    code = "completed_without_timestamp"
                    if timestamp is not None:
                        detail = "restored completion timestamp from stable existing job metadata"
                        after["set_completed_at"] = timestamp
                    else:
                        disposition = "unresolved"
                        detail = "completed job has no trustworthy existing timestamp to restore"
                elif job.queue_status != "completed":
                    code = "completed_queue_mismatch"
                    detail = "validated completed job had contradictory queue state"
                if job.queue_status != "completed":
                    after["queue_status"] = "completed"
                    if code == "completed_without_timestamp":
                        detail += "; normalized contradictory queue state in the same pass"
        elif job.status == "failed" and job.queue_status != "failed":
            code = "failed_queue_mismatch"
            detail = "failed job had contradictory queue state"
            after["queue_status"] = "failed"
        elif job.status == "running" and job.queue_status != "running":
            code = "running_queue_mismatch"
            detail = "running job had contradictory queue state"
            after["queue_status"] = "running"

        if code is not None:
            public_after = {
                key: value
                for key, value in after.items()
                if not key.startswith(("clear_", "set_"))
            }
            if "set_completed_at" in after:
                public_after["completed_at"] = after["set_completed_at"].isoformat()
            changes.append(
                RepairChange(
                    code,
                    "job",
                    str(job.id),
                    before,
                    public_after,
                    detail,
                    disposition,
                )
            )
            if apply and disposition == "repair":
                # Keep the loaded ORM row clean. A conditional SQL update below
                # makes a concurrent operator action authoritative.
                pending_repairs.append((len(changes) - 1, job, before, after))

    orphan_design_ids = list(
        (
            await session.execute(
                select(Design.id).where(~Design.job_id.in_(job_ids))
            )
        ).scalars().all()
    )
    for design_id in orphan_design_ids:
        changes.append(
            RepairChange(
                "orphan_design",
                "design",
                str(design_id),
                {"present": True},
                {"present": True},
                "design references a missing job; report-only finding remains for operator review",
                "unresolved",
            )
        )

    if apply:
        for index, job, before, after in pending_repairs:
            # Capture a complete snapshot guard before expunging the stale ORM row.
            # A repair only publishes its intentional deltas if no repair-relevant
            # field changed after planning.
            job_id = str(job.id)
            guards = _job_state_guards(job)
            values = _job_state_values(before, after)
            session.expunge(job)
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, *guards)
                .values(**values)
            )
            if result.rowcount != 1:
                change = changes[index]
                current = await session.get(Job, job_id)
                if current is not None and (current.status == "cancelled" or current.awaiting_input):
                    detail = f"{change.detail}; superseded by concurrent authoritative state"
                    disposition = "superseded"
                elif current is None:
                    detail = f"{change.detail}; record disappeared before guarded repair publication"
                    disposition = "unresolved"
                else:
                    detail = f"{change.detail}; guarded repair was not published because the record changed concurrently"
                    disposition = "unresolved"
                changes[index] = RepairChange(
                    change.code,
                    change.record_type,
                    change.record_id,
                    change.before,
                    change.after,
                    detail,
                    disposition,
                )
        await session.commit()
    else:
        await session.rollback()
    return RepairReport(
        apply and any(change.disposition == "repair" for change in changes),
        apply,
        changes,
    )

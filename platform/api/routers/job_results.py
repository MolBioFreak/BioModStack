"""Job results, readback, artifacts and logs."""
from __future__ import annotations

import logging
from fastapi import APIRouter

import asyncio
import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from schemas import BC2NavigationResponse, BinderNavigationJob, BinderNavigationJobList, ExecutionPolicy, JobList, JobResponse, JobStatus, JobSummaryList, JobSummaryResponse
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request, Response
from database import ConformationalMappingRequest, Design, FrustraMPNNResult, Job, get_session
from services.core_protein_execution_settings import ExecutionSettings
from fastapi.responses import FileResponse
from pathlib import Path
from services.stage_review import REVIEWABLE_STAGES, has_stage_gate, load_review_gate_snapshot, nextflow_history_status, refresh_gate_payload
from types import SimpleNamespace
from services.job_requests import _dedupe_preserve_order
from services.job_readback import _public_job_output_dir, _public_job_params, _resolve_requested_design_count, _serialized_saved_review_filter_sets, read_job, resolve_output_dir
from runtime_policy import acquire_workflow_mutation_lease, run_with_workflow_mutation_lease
from sqlalchemy import and_, func, or_, select
from datetime import datetime
from services.rfd3_generation import file_sha256
from paths import get_code_root, get_work_dir, resolve_runtime_data_path, to_allowed_relative
from services.binder_navigation_jobs import navigation_columns, navigation_job
from services.job_stage_progress import project_execution_stages
from urllib.parse import quote


router = APIRouter()
logger = logging.getLogger(__name__)


CODE_ROOT = get_code_root()


PRESERVED_GATE_PAYLOAD_KEYS = ("review_filter_sets",)


def _merge_preserved_gate_payload(
    gate_payload: Optional[Dict[str, Any]],
    existing_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(gate_payload or {})
    existing = existing_payload if isinstance(existing_payload, dict) else {}
    for key in PRESERVED_GATE_PAYLOAD_KEYS:
        if key in merged:
            continue
        preserved_value = existing.get(key)
        if isinstance(preserved_value, list) and preserved_value:
            merged[key] = preserved_value
    return merged


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _terminal_closeout_design_count(output_path: Path) -> int:
    counts: List[int] = []

    final_designs_path = output_path / "final_designs.txt"
    if final_designs_path.exists():
        try:
            counts.append(
                sum(
                    1
                    for line in final_designs_path.read_text(errors="ignore").splitlines()
                    if line.strip()
                )
            )
        except Exception:
            pass

    report_path = output_path / "terminal_closeout_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(errors="ignore"))
            status = str(report.get("status") or "").strip().lower()
            report_count = _positive_int(
                report.get("total_terminal_designs")
                or report.get("final_design_count")
                or report.get("design_count")
            )
            if status in {"complete", "completed"} and report_count > 0:
                counts.append(report_count)
        except Exception:
            pass

    for relative_dir in (
        Path("run") / "rfantibody",
        Path("collected") / "rfantibody_raw",
        Path("results"),
    ):
        artifact_dir = output_path / relative_dir
        if artifact_dir.exists():
            try:
                counts.append(len(list(artifact_dir.glob("*.pdb"))))
            except Exception:
                pass

    return max(counts) if counts else 0


def _has_terminal_closeout_completion(job: Job) -> bool:
    """Return True when terminal artifacts prove a completed closeout despite history ERR.

    Some RFantibody runs can finish artifact generation and then trip a terminal
    closeout bookkeeping/self-copy error, leaving .nextflow/history as ERR. Do
    not let response-time reconciliation demote a job once closeout artifacts
    prove non-zero terminal designs were published.
    """
    output_path = resolve_output_dir(job.output_dir) if job.output_dir else None
    if not output_path or not output_path.exists():
        return False
    return _terminal_closeout_design_count(output_path) > 0


def _infer_gate_stage_from_files(job: Job) -> Optional[str]:
    output_path = resolve_output_dir(job.output_dir) if job.output_dir else None
    if not output_path:
        return None
    gates_dir = output_path / "gates"
    if not gates_dir.exists():
        return None
    gate_files = sorted(gates_dir.glob("gate_*.json"))
    if not gate_files:
        return None
    stem = gate_files[-1].stem
    if stem.startswith("gate_"):
        return stem[len("gate_"):]
    return None


def _repair_job_for_response(job: Job) -> bool:
    if job.execution_target_id and (job.awaiting_stage == "remote_results" or job.remote_state in {
        "results_available", "returning", "result_pull_failed"
    }):
        return False
    changed = False

    gate_stage, gate_payload = load_review_gate_snapshot(job.output_dir, job.awaiting_stage)
    gate_payload = _merge_preserved_gate_payload(gate_payload, job.awaiting_payload or {})
    if gate_stage and job.awaiting_stage != gate_stage:
        job.awaiting_stage = gate_stage
        changed = True
    if gate_payload and gate_payload != (job.awaiting_payload or {}):
        job.awaiting_payload = gate_payload
        changed = True

    if job.parent_job_id and job.child_stage:
        history_status = nextflow_history_status(job)
        if history_status == "OK":
            if job.status != JobStatus.COMPLETED.value:
                job.status = JobStatus.COMPLETED.value
                changed = True
            if job.queue_status != "completed":
                job.queue_status = "completed"
                changed = True
            if job.awaiting_input:
                job.awaiting_input = False
                changed = True
            if job.awaiting_stage is not None:
                job.awaiting_stage = None
                changed = True
            if job.awaiting_payload:
                job.awaiting_payload = {}
                changed = True
            if not job.completed_at:
                job.completed_at = datetime.utcnow()
                changed = True
            return changed
        if history_status == "ERR":
            if job.status != JobStatus.FAILED.value:
                job.status = JobStatus.FAILED.value
                changed = True
            if job.queue_status != "failed":
                job.queue_status = "failed"
                changed = True
            if job.awaiting_input:
                job.awaiting_input = False
                changed = True
            if job.awaiting_stage is not None:
                job.awaiting_stage = None
                changed = True
            if job.awaiting_payload:
                job.awaiting_payload = {}
                changed = True
            if not job.completed_at:
                job.completed_at = datetime.utcnow()
                changed = True
            return changed

    if job.awaiting_payload:
        repaired_payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
        if repaired_payload != (job.awaiting_payload or {}):
            job.awaiting_payload = repaired_payload
            changed = True

    history_status = nextflow_history_status(job)
    gate_present = has_stage_gate(job)
    if not job.awaiting_stage and gate_present:
        inferred_stage = _infer_gate_stage_from_files(job)
        if inferred_stage:
            job.awaiting_stage = inferred_stage
            changed = True

    if history_status == "ERR" and not gate_present and _has_terminal_closeout_completion(job):
        if job.status != JobStatus.COMPLETED.value:
            job.status = JobStatus.COMPLETED.value
            changed = True
        if job.queue_status != "completed":
            job.queue_status = "completed"
            changed = True
        if job.current_stage != "Complete":
            job.current_stage = "Complete"
            changed = True
        if job.stage_progress is not None:
            job.stage_progress = None
            changed = True
        if job.awaiting_stage is not None:
            job.awaiting_stage = None
            changed = True
        if job.awaiting_payload:
            job.awaiting_payload = {}
            changed = True
        if job.awaiting_input:
            job.awaiting_input = False
            changed = True
        if job.error_message:
            job.error_message = None
            changed = True
        if not job.completed_at:
            job.completed_at = datetime.utcnow()
            changed = True
        return changed

    if history_status == "OK" and not gate_present and not job.awaiting_input:
        if job.status != JobStatus.COMPLETED.value:
            job.status = JobStatus.COMPLETED.value
            changed = True
        if job.queue_status != "completed":
            job.queue_status = "completed"
            changed = True
        if job.current_stage != "Complete":
            job.current_stage = "Complete"
            changed = True
        if job.stage_progress is not None:
            job.stage_progress = None
            changed = True
        if job.awaiting_stage is not None:
            job.awaiting_stage = None
            changed = True
        if job.awaiting_payload:
            job.awaiting_payload = {}
            changed = True
        if job.error_message:
            job.error_message = None
            changed = True
        if not job.completed_at:
            job.completed_at = datetime.utcnow()
            changed = True
        return changed

    stale_failed = str(job.error_message or "").startswith(
        "Reconciled as failed: no active process and no terminal .nextflow/history status"
    )
    if history_status == "ERR":
        if job.status != JobStatus.FAILED.value:
            job.status = JobStatus.FAILED.value
            changed = True
        if job.queue_status != "failed":
            job.queue_status = "failed"
            changed = True
        replacement_error = str(job.error_message or "").strip()
        if not replacement_error or stale_failed:
            replacement_error = "Reconciled as failed: terminal .nextflow/history status ERR"
        if replacement_error != (job.error_message or ""):
            job.error_message = replacement_error
            changed = True
        if not job.completed_at:
            job.completed_at = datetime.utcnow()
            changed = True
        return changed
    if history_status == "OK" and (job.awaiting_input or gate_present or stale_failed):
        if not job.awaiting_input:
            job.awaiting_input = True
            changed = True
        if job.awaiting_stage:
            job.current_stage = job.awaiting_stage
        if job.status != JobStatus.AWAITING_INPUT.value:
            job.status = JobStatus.AWAITING_INPUT.value
            changed = True
        if job.queue_status != "paused":
            job.queue_status = "paused"
            changed = True
        if job.error_message:
            job.error_message = None
            changed = True

    return changed


def _review_candidate_count_cached(job: Job) -> Optional[int]:
    if not job.awaiting_input:
        return None
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return None
    payload = job.awaiting_payload if isinstance(job.awaiting_payload, dict) else {}
    if stage in {"post_fampnn", "post_boltzgen", "post_ppiflow_generator", "post_caliby"}:
        filtered_count = payload.get("filtered_candidate_count")
        if isinstance(filtered_count, int) and filtered_count > 0:
            return filtered_count
    candidate_count = payload.get("candidate_count")
    if isinstance(candidate_count, int) and candidate_count >= 0:
        return candidate_count
    return None


def _scientific_family_ids(job_id: str):
    """Read persisted scientific edges, never rewrite scheduler parentage.

    The existing Jobs listing remains the paged readback owner. Traverse on the
    server so reopening an old round does not download unrelated job history.
    """
    from sqlalchemy import union_all

    def text_value(value):
        return func.nullif(value, "")

    root = func.coalesce(text_value(Job.lineage_root_job_id),
                         text_value(Job.params["lineage_root_job_id"].as_string()),
                         text_value(Job.params["iteration_source_root_job_id"].as_string()))
    source = func.coalesce(text_value(Job.selection_source_job_id),
                           text_value(Job.source_stage_job_id),
                           text_value(Job.params["selection_source_job_id"].as_string()),
                           text_value(Job.params["source_stage_job_id"].as_string()),
                           text_value(Job.params["iteration_source_job_id"].as_string()))
    # Only legacy records without scientific references use scheduler edges.
    legacy = select(Job.id.label("child"), Job.parent_job_id.label("source")).where(
        root.is_(None), source.is_(None), Job.parent_job_id.is_not(None))
    edges = union_all(select(Job.id.label("child"), root.label("source")).where(root.is_not(None)),
                      select(Job.id.label("child"), source.label("source")).where(source.is_not(None)),
                      legacy).cte("scientific_edges")
    family = select(Job.id).where(Job.id == job_id).cte("scientific_family", recursive=True)
    family = family.union(
        select(edges.c.source).join(family, edges.c.child == family.c.id),
        select(edges.c.child).join(family, edges.c.source == family.c.id),
    )
    return select(family.c.id)


async def list_jobs(
    status: Optional[JobStatus] = None,
    q: Optional[str] = None,
    model_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    model_ids: Optional[list[str]] = Query(None),
    q_ignore_case_id: bool = False,  # NGS historically searches IDs case-insensitively
    exclude_ngs: bool = False,  # Dashboard Show NGS Jobs filter, before paging/count
    exclude_alignments: bool = False,  # Family-MSA display filter, before paging/count
    include_children: bool = False,  # New param: show child jobs if True
    scientific_family_job_id: Optional[str] = None,
    summary: bool = False,  # Mobile/list views: omit heavyweight detail fields until a job is opened
    projection: Optional[Literal["binder_navigation"]] = None,
    session: AsyncSession = Depends(get_session),
    *,
    request: Request = None,
):
    """List jobs with optional filters.

    Set ``summary=true`` for mobile/dashboard/list views. It preserves the fields
    needed to render and select jobs while omitting heavyweight params,
    provenance, review sets, stage outputs, and decision payloads that can make
    the recent-jobs pane parse multi-megabyte responses before any row appears.
    Full detail remains available from ``GET /api/jobs/{id}``.
    """
    # Optimized query: fetch jobs and design counts in one go
    # This replaces the N+1 query loop with a single GROUP BY query
    summary_columns = (
        Job.id,
        Job.name,
        Job.status,
        Job.model_id,
        Job.mode,
        Job.created_at,
        Job.started_at,
        Job.completed_at,
        Job.output_dir,
        Job.error_message,
        Job.batch_id,
        Job.batch_name,
        Job.parent_job_id,
        Job.child_stage,
        Job.lineage_root_job_id,
        Job.stage_family,
        Job.stage_mode,
        Job.source_stage_job_id,
        Job.source_stage_family,
        Job.source_stage_mode,
        Job.source_selection_count,
        Job.selected_input_artifact_class,
        Job.selected_input_schema_version,
        Job.selection_source_type,
        Job.selection_source_job_id,
        Job.selection_dataset_name,
        Job.pinned_gpu,
        Job.execution_target_id,
        Job.execution_source_revision,
        Job.execution_source_tree,
        Job.execution_bundle_sha256,
        Job.remote_attempt_id,
        Job.remote_state,
        Job.current_stage,
        Job.completed_stages,
        Job.awaiting_input,
        Job.awaiting_stage,
        # Keep summary rows lightweight while preserving execution-policy parity.
        Job.params["remote_result_policy"].as_string().label("remote_result_policy"),
        Job.provenance[("execution_plan_approval", "plan", "metadata", "static_components")].label("stage_plan_components"),
        Job.provenance[("remote_execution_assignment", "resources", "components")].label("stage_assigned_components"),
        Job.provenance["stage_terminal_states"].label("stage_terminal_states"),
        Job.provenance[("remote_execution_receipt", "received_manifest_sha256")].label("remote_received_manifest_sha256"),
        Job.provenance[("remote_execution_receipt", "result_manifest_sha256")].label("remote_result_manifest_sha256"),
    )
    # Use identical predicates for the bounded page and its total. These are
    # presentation filters, not changes to execution/status authority.
    from services.job_control import visible_job_clause
    filters = [visible_job_clause()]
    if scientific_family_job_id:
        filters.append(Job.id.in_(_scientific_family_ids(scientific_family_job_id)))
    if not include_children:
        filters.append(Job.parent_job_id.is_(None))
    if status:
        status_filter = Job.status == status.value
        if status.value == "awaiting_input":
            status_filter = or_(status_filter, Job.awaiting_input.is_(True))
        filters.append(status_filter)
    if model_id:
        filters.append(Job.model_id == model_id)
    if isinstance(model_ids, list) and model_ids:
        filters.append(Job.model_id.in_(model_ids))
    if mode:
        filters.append(Job.mode == mode)
    if q:
        # Dashboard search was a literal substring, not a SQL wildcard pattern.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if q_ignore_case_id:
            # SQLite lower/NOCASE only handles ASCII. Preserve the NGS browser's
            # Unicode lowercase literal search without materializing history.
            connection = await session.connection()
            await connection.run_sync(lambda conn: conn.connection.dbapi_connection.create_function(
                "ngs_lower", 1, lambda value: str(value or "").lower(), deterministic=True))
            filters.append(or_(func.instr(func.ngs_lower(Job.name), q.lower()) > 0,
                               func.instr(func.ngs_lower(Job.id), q.lower()) > 0))
        else:
            filters.append(or_(Job.name.ilike(f"%{escaped}%", escape="\\"), func.instr(Job.id, q) > 0))
    if exclude_ngs:
        # Match ngsResultRouting.isNgsJob, including legacy missing-model rows.
        ngs_models = ("nanopore", "ont_basecall_dna", "ont_basecall_rna", "ont_plasmid_qc",
                      "ont_construct_screening", "ont_methylation_analysis", "ont_fastq_qc",
                      "ont_pooled_reference_assignment", "wf_clone_validation")
        ngs_workflows = (*ngs_models[1:], "basecall_dna", "basecall_rna", "plasmid_qc",
                         "construct_screening", "methylation_analysis", "fastq_qc",
                         "pooled_reference_assignment", "pooled-reference-assignment", "wf_clone", "clone_validation")
        # JS routeIdentity trims ECMAScript whitespace. Preserve its truthy
        # ont_workflow_id || workflow_id fallback for legacy JSON false/zero.
        whitespace = " \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
        model_identity = func.lower(func.trim(func.coalesce(Job.model_id, ""), whitespace))
        workflow_identity = func.lower(func.trim(func.coalesce(
            func.nullif(func.nullif(Job.params["ont_workflow_id"].as_string(), ""), 0),
            Job.params["workflow_id"].as_string(), ""), whitespace))
        mode_identity = func.lower(func.trim(func.coalesce(Job.mode, ""), whitespace))
        filters.append(~or_(model_identity.in_(ngs_models), and_(
            model_identity == "", or_(workflow_identity.in_(ngs_workflows),
                                      mode_identity.in_((*ngs_workflows, "nanopore_methylation"))))))
    if exclude_alignments:
        # Match jobCategories.jobCategory: family MSA only, not folding msa_batch.
        whitespace = " \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
        model_identity = func.lower(func.trim(func.coalesce(Job.model_id, ""), whitespace))
        filters.append(~model_identity.in_(("family_msa_protein", "family_msa_nucleic")))
    if projection == "binder_navigation":
        rows = (await session.execute(select(*navigation_columns()).where(*filters).order_by(
            Job.created_at.desc(), Job.id).limit(limit).offset(offset))).mappings().all()
        total = (await session.execute(select(func.count(Job.id)).where(*filters))).scalar_one()
        return BinderNavigationJobList(jobs=[navigation_job(row) for row in rows], total=total)
    if summary:
        # Bound the Jobs scope before counting Designs. The existing job_id
        # index serves one exact count per displayed row, not a global GROUP BY
        # over retained scientific history on every recurring/conditional read.
        page = select(*summary_columns).where(*filters).order_by(
            Job.created_at.desc()).limit(limit).offset(offset).subquery()
        page_design_count = select(func.count(Design.id)).where(
            Design.job_id == page.c.id).correlate(page).scalar_subquery()
        query = select(page, page_design_count.label("design_count")).order_by(page.c.created_at.desc())
    else:
        # Count Designs for the page only. A global GROUP BY over the Designs
        # table made every Jobs list read the whole retained design history.
        page_ids = (
            select(Job.id)
            .where(*filters)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
            .subquery()
        )
        design_count = select(func.count(Design.id)).where(
            Design.job_id == Job.id).correlate(Job).scalar_subquery()
        query = (
            select(Job, design_count.label("design_count"))
            .where(Job.id.in_(select(page_ids.c.id)))
            .order_by(Job.created_at.desc())
        )
    result = await session.execute(query)
    if summary:
        rows = [
            (
                SimpleNamespace(**{key: value for key, value in row.items() if key != "design_count"}),
                row["design_count"],
            )
            for row in result.mappings().all()
        ]
    else:
        rows = result.all()

    listed_job_ids = [job.id for job, _design_count in rows]
    frustrampnn_count_by_job: dict[str, int] = {}
    if listed_job_ids:
        frustrampnn_counts = await session.execute(
            select(FrustraMPNNResult.parent_job_id, func.count(FrustraMPNNResult.invocation_id))
            .where(FrustraMPNNResult.parent_job_id.in_(listed_job_ids))
            .group_by(FrustraMPNNResult.parent_job_id)
        )
        frustrampnn_count_by_job = {
            str(parent_job_id): int(result_count)
            for parent_job_id, result_count in frustrampnn_counts.all()
        }
    from services.rfd4_proteina_results import native_result_counts
    native_count_by_job = await native_result_counts(session, [
        job.id for job, count in rows
        if not count and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}])
    conformational_mapping_request_id_by_job: dict[str, str] = {}
    if listed_job_ids:
        cm_request_rows = await session.execute(
            select(ConformationalMappingRequest.job_id, ConformationalMappingRequest.request_id)
            .where(ConformationalMappingRequest.job_id.in_(listed_job_ids))
        )
        conformational_mapping_request_id_by_job = {
            str(job_id): str(request_id)
            for job_id, request_id in cm_request_rows.all()
        }
    child_design_count_by_parent: dict[str, int] = {}
    child_count_ids = (listed_job_ids if not summary else [
        job.id for job, design_count in rows
        if not design_count and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}
    ])
    if child_count_ids:
        child_count_result = await session.execute(
            select(Job.parent_job_id, func.count(Design.id))
            .join(Design, Design.job_id == Job.id)
            .where(Job.parent_job_id.in_(child_count_ids))
            .group_by(Job.parent_job_id)
        )
        child_design_count_by_parent = {
            str(parent_job_id): int(design_count or 0)
            for parent_job_id, design_count in child_count_result.all()
            if parent_job_id is not None
        }
    
    # Count the same filtered scope, not just the current page.
    count_query = select(func.count(Job.id)).where(*filters)
    total = (await session.execute(count_query)).scalar()

    
    job_responses = []
    for job, design_count in rows:
        frustrampnn_result_count = frustrampnn_count_by_job.get(job.id, 0)
        completed_stages = _dedupe_preserve_order(list(job.completed_stages or []))
        if not summary and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}:
            review_count = _review_candidate_count_cached(job)
            if (design_count or 0) == 0 and review_count is not None:
                design_count = review_count
        if (design_count or 0) == 0 and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}:
            child_design_count = child_design_count_by_parent.get(job.id)
            if child_design_count:
                design_count = child_design_count
        
        public_fields: dict[str, Any] = dict(
            id=job.id,
            name=job.name,
            status=job.status,
            model_id=job.model_id,
            mode=job.mode,
            execution_policy=ExecutionPolicy.from_params(
                {"remote_result_policy": job.remote_result_policy} if summary else job.params
            ),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            output_dir=_public_job_output_dir(job),
            error_message=job.error_message,
            design_count=design_count,  # Now joined from DB
            batch_id=job.batch_id,
            batch_name=job.batch_name,
            parent_job_id=job.parent_job_id,
            child_stage=job.child_stage,
            lineage_root_job_id=job.lineage_root_job_id,
            stage_family=job.stage_family,
            stage_mode=job.stage_mode,
            source_stage_job_id=job.source_stage_job_id,
            source_stage_family=job.source_stage_family,
            source_stage_mode=job.source_stage_mode,
            source_selection_count=job.source_selection_count,
            selected_input_artifact_class=job.selected_input_artifact_class,
            selected_input_schema_version=job.selected_input_schema_version,
            selection_source_type=job.selection_source_type,
            selection_source_job_id=job.selection_source_job_id,
            selection_dataset_name=job.selection_dataset_name,
            pinned_gpu=job.pinned_gpu,
            execution_target_id=job.execution_target_id,
            execution_source_revision=job.execution_source_revision,
            execution_source_tree=job.execution_source_tree,
            execution_bundle_sha256=job.execution_bundle_sha256,
            remote_attempt_id=job.remote_attempt_id,
            remote_state=job.remote_state,
            execution_stages=project_execution_stages(job),
            current_stage=job.current_stage,
            completed_stages=completed_stages,
            awaiting_input=job.awaiting_input,
            awaiting_stage=job.awaiting_stage,
            frustrampnn_result_count=frustrampnn_result_count,
            native_result_count=native_count_by_job.get(job.id, 0),
            frustrampnn_reopen_destination=(
                {"surface": "frustrampnn-workbench", "params": {"job_id": job.id}}
                if frustrampnn_result_count else None
            ),
            conformational_mapping_request_id=conformational_mapping_request_id_by_job.get(str(job.id)),
        )
        if summary:
            received_digest = job.remote_received_manifest_sha256
            public_fields["remote_results_received"] = (
                isinstance(received_digest, str) and bool(received_digest)
                and received_digest == job.remote_result_manifest_sha256
            )
            job_responses.append(JobSummaryResponse(**public_fields))
        else:
            job_responses.append(JobResponse(
                **public_fields,
                params=_public_job_params(job),
                requested_design_count=_resolve_requested_design_count(job),
                source_selection_manifest_path=job.source_selection_manifest_path,
                selected_loop_scope=job.selected_loop_scope,
                provenance=job.provenance,
                saved_selection_sets=_serialized_saved_review_filter_sets(job),
                stage_outputs=dict(job.stage_outputs or {}),
                awaiting_payload=job.awaiting_payload,
                decision_history=job.decision_history,
            ))
    
    result = (JobSummaryList if summary else JobList)(jobs=job_responses, total=total)
    if summary and request is not None:
        # Validate/project every request before comparing: counts, removals and
        # all visible fields participate, with no stale server-side cache.
        body = result.model_dump_json().encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie, Authorization"}
        validators = request.headers.get("if-none-match", "").split(",")
        if any(value.strip().removeprefix("W/") in {etag, "*"} for value in validators):
            return Response(status_code=304, headers=headers)
        return Response(content=body, media_type="application/json", headers=headers)
    return result


@router.post("/{job_id}/remote-diagnostics/pull", response_model=JobResponse, status_code=202)
async def pull_remote_job_diagnostics(
    job_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    from services.remote_execution.executor import RemoteExecutionError, request_remote_diagnostic_pull

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        await request_remote_diagnostic_pull(session, job, background_tasks)
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.refresh(job)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/remote-results/pull", response_model=JobResponse, status_code=202)
async def pull_remote_job_results(
    job_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    from services.remote_execution.executor import RemoteExecutionError, request_remote_result_pull

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        await request_remote_result_pull(session, job, background_tasks)
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.refresh(job)
    return JobResponse.model_validate(job)


def _rfd3_generation_range(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "max": None}
    return {"min": min(values), "mean": round(sum(values) / len(values), 6), "max": max(values)}


@router.get("/{job_id}/rfd3-generation")
async def get_rfd3_generation_result(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    offset: int = 0,
    limit: int | None = None,
):
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_modification_experimental"
        or str(job.mode or "").strip().lower() != "de_novo_design"
    ):
        raise HTTPException(status_code=404, detail="RFD3 generation job not found")
    from services.rfd3_generation_results import read_generation_results
    return await read_generation_results(job, session, offset=offset, limit=limit)


@router.get("/{job_id}/rfd3-generation/artifacts/{candidate_id}/{role}")
async def get_rfd3_generation_artifact(
    job_id: str,
    candidate_id: str,
    role: str,
    session: AsyncSession = Depends(get_session),
):
    if role not in {"candidate_structure", "candidate_metadata"}:
        raise HTTPException(status_code=404, detail="RFD3 generation artifact role not found")
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    design = (
        await session.execute(select(Design).where(Design.job_id == job_id, Design.name == candidate_id))
    ).scalar_one_or_none()
    if job is None or design is None or not isinstance(design.provenance, dict):
        raise HTTPException(status_code=404, detail="RFD3 generation artifact not found")
    artifacts = design.provenance.get("artifacts")
    descriptor = next(
        (item for item in artifacts if isinstance(item, dict) and item.get("role") == role),
        None,
    ) if isinstance(artifacts, list) else None
    if not isinstance(descriptor, dict):
        raise HTTPException(status_code=404, detail="RFD3 generation artifact not found")
    root = resolve_runtime_data_path(str(job.output_dir or ""))
    relative = Path(str(descriptor.get("relative_path") or ""))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=409, detail="RFD3 generation artifact path is unsafe")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(status_code=409, detail="RFD3 generation artifact path contains a symlink")
    path = current.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size != descriptor.get("bytes"):
        raise HTTPException(status_code=410, detail="RFD3 generation artifact is unavailable")
    if file_sha256(path) != descriptor.get("sha256"):
        raise HTTPException(status_code=409, detail="RFD3 generation artifact hash mismatch")
    headers = {"Content-Encoding": "gzip"} if path.name.endswith(".gz") else None
    media_type = "chemical/x-mmcif" if path.name.endswith(".cif.gz") else str(descriptor.get("media_type"))
    # HTTP clients decode Content-Encoding before saving the attachment.
    filename = path.name.removesuffix(".gz") if headers else path.name
    return FileResponse(str(path), media_type=media_type, filename=filename, headers=headers)


@router.get("/{job_id}", response_model=JobResponse | BinderNavigationJob)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    projection: Optional[Literal["binder_navigation"]] = None,
):
    """Get details of a specific job."""
    job = await read_job(session, job_id, projection)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/generation-results")
async def get_native_generation_results(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """Read native generation accounting, including valid zero-yield runs."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    from services.rfd4_proteina_results import has_native_publication
    if job.model_id == "boltzgen" and job.mode in {"protein_binder", "nanobody_binder", "peptide_binder", "antibody_binder", "ligand_binder", "ntp_binder", "scaffold_around_ligand", "backbone_docking"}:
        from services.boltzgen_candidate_publication import read_published_generation_page
    elif job.model_id == "ppiflow" and job.mode in {"protein_binder", "antibody_binder", "nanobody_binder"}:
        from services.ppiflow_generation import read_published_generation_page
    elif has_native_publication(job):
        from services.rfd4_proteina_results import read_published_generation_page
    else:
        raise HTTPException(status_code=400, detail="Job is not a supported native generation mode")
    try:
        return await read_published_generation_page(job, session, offset=offset, limit=limit)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("Native generation results unavailable for job %s: %s", job_id, exc)
        raise HTTPException(status_code=409, detail="Native generation results unavailable") from exc


@router.get("/{job_id}/bindcraft2-navigation", response_model=BC2NavigationResponse)
async def get_bindcraft2_navigation(
    job_id: str,
    arm: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Compact navigation, with the same fresh publication authority as results."""
    from services.bindcraft2_result_readback import read_bindcraft2_navigation
    from services.bindcraft2_publication import PublicationError
    from services.bindcraft2_native_results import NativeResultError

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.model_id != "bindcraft2":
        raise HTTPException(status_code=400, detail="Job is not a BindCraft2 campaign")
    try:
        return await read_bindcraft2_navigation(job, session, arm=arm)
    except (PublicationError, NativeResultError, OSError) as exc:
        logger.warning("BC2 native navigation unavailable for job %s: %s", job_id, exc)
        raise HTTPException(status_code=409, detail="Verified native results unavailable") from exc


@router.get("/{job_id}/bindcraft2-results")
async def get_bindcraft2_native_results(
    job_id: str,
    stage: Literal["trajectory", "draw", "retained", "attempt", "document"] = "trajectory",
    arm: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Paginate verified, model-owned BC2 records; never project them as Designs."""
    from services.bindcraft2_result_readback import read_bindcraft2_result_page
    from services.bindcraft2_publication import PublicationError
    from services.bindcraft2_native_results import NativeResultError

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.model_id != "bindcraft2":
        raise HTTPException(status_code=400, detail="Job is not a BindCraft2 campaign")
    try:
        return await read_bindcraft2_result_page(
            job, session, arm=arm, stage=stage, offset=offset, limit=limit,
        )
    except (PublicationError, NativeResultError, OSError) as exc:
        logger.warning("BC2 native results unavailable for job %s: %s", job_id, exc)
        raise HTTPException(status_code=409, detail="Verified native results unavailable") from exc


@router.get("/{job_id}/bindcraft2-results/trajectory")
async def get_bindcraft2_native_trajectory(
    job_id: str,
    design: str = Query(..., min_length=1),
    arm: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """Read recorded native trajectory updates from the verified publication."""
    from services.bindcraft2_result_readback import read_bindcraft2_trajectory
    from services.bindcraft2_publication import PublicationError
    from services.bindcraft2_native_results import NativeResultError

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.model_id != "bindcraft2":
        raise HTTPException(status_code=400, detail="Job is not a BindCraft2 campaign")
    try:
        return await read_bindcraft2_trajectory(
            job, session, design=design, arm=arm, offset=offset, limit=limit,
        )
    except (PublicationError, NativeResultError, OSError) as exc:
        logger.warning("BC2 native trajectory unavailable for job %s: %s", job_id, exc)
        raise HTTPException(status_code=409, detail="Verified native trajectory unavailable") from exc


@router.get("/{job_id}/execution-settings", response_model=ExecutionSettings)
async def job_execution_settings(job_id: str, session: AsyncSession = Depends(get_session)):
    from services.core_protein_execution_settings import verify_receipts
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return verify_receipts(job)


@router.post("/{job_id}/reingest")
async def reingest_job_results(
    job_id: str,
    include_children: bool = Query(True, description="Include child jobs when re-ingesting"),
    session: AsyncSession = Depends(get_session)
):
    """
    Re-ingest design results for a job.
    
    Deletes all existing Design entries for this job and re-runs the result
    ingester to pick up any new metrics from confidence JSON files.
    
    Useful when:
    - Job was ingested with old code that didn't extract all metrics
    - New metric extraction logic was added
    - Need to refresh design data without re-running the pipeline
    """
    from sqlalchemy import delete
    from services.result_ingester import ingest_job_results
    
    async def delete_for_reingest(job_id_to_delete: str) -> int:
        existing_count = (await session.execute(
            select(func.count(Design.id)).where(Design.job_id == job_id_to_delete)
        )).scalar()
        await session.execute(delete(Design).where(Design.job_id == job_id_to_delete))
        return existing_count or 0

    try:
        # Fetch job
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Build job list (parent + children)
        job_ids = [job_id]
        jobs_to_ingest = {job_id: job.child_output_dir or job.output_dir}
        if include_children:
            child_result = await session.execute(select(Job).where(Job.parent_job_id == job_id))
            child_jobs = child_result.scalars().all()
            for child in child_jobs:
                job_ids.append(child.id)
                jobs_to_ingest[child.id] = child.child_output_dir or child.output_dir
        
        total_deleted = 0
        total_created = 0
        
        for jid in job_ids:
            output_dir = jobs_to_ingest.get(jid)
            if not output_dir:
                logger.warning(f"[REINGEST] Skipping job {jid}: no output_dir")
                continue
            
            try:
                deleted_count = await delete_for_reingest(jid)
                new_count = await ingest_job_results(jid, output_dir, session, commit=False)
                if new_count <= 0:
                    raise ValueError("re-ingestion produced no validated designs; preserving existing results")
                await session.commit()
                total_deleted += deleted_count
                total_created += new_count
                logger.info(f"[REINGEST] Re-ingested {new_count} designs for job {jid}")
            except Exception as e:
                await session.rollback()
                logger.error(f"[REINGEST] Error re-ingesting job {jid}: {e}")
        
        return {
            "message": f"Re-ingested {total_created} designs (deleted {total_deleted} old entries)",
            "job_id": job_id,
            "designs_deleted": total_deleted,
            "designs_created": total_created
        }
    except Exception as e:
        logger.exception(f"[REINGEST] Failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/annotate-cdrs")
async def annotate_cdr_regions(
    job_id: str,
    include_children: bool = Query(True, description="Include designs from child jobs (for parent exploration jobs)"),
    session: AsyncSession = Depends(get_session)
):
    """
    Annotate CDR regions for all designs in a job using ANARCII.
    
    If include_children is True (default), also annotates designs from child jobs
    that have this job as their parent. This is needed for exploration mode where
    designs are spread across spawned child validation jobs.
    
    This runs in the background (~1-2 minutes for large jobs).
    Returns immediately with status "started".
    """
    from services.cdr_annotation_tasks import annotate_and_update_designs
    
    # Fetch job
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get designs - include children if requested
    if include_children:
        # Get all child job IDs for this parent
        child_query = select(Job.id).where(Job.parent_job_id == job_id)
        child_result = await session.execute(child_query)
        child_job_ids = [row[0] for row in child_result.all()]
        
        # Include both parent and children
        all_job_ids = [job_id] + child_job_ids
        designs_result = await session.execute(
            select(Design).where(Design.job_id.in_(all_job_ids))
        )
    else:
        designs_result = await session.execute(
            select(Design).where(Design.job_id == job_id)
        )
    designs = designs_result.scalars().all()
    
    if not designs:
        return {
            "message": "No designs available for CDR annotation yet",
            "job_id": job_id,
            "status": "skipped",
            "pending": 0,
            "total": 0
        }
    
    # Always re-annotate ALL designs (allows fixing bad annotations)
    designs_to_annotate = list(designs)
    
    # Collect PDB paths and design IDs for background processing
    pdb_paths = [d.pdb_path for d in designs_to_annotate if d.pdb_path]
    design_ids = [d.id for d in designs_to_annotate if d.pdb_path]
    total_count = len(designs)
    
    logger.info(f"[CDR ANNOTATE] Starting background annotation on {len(pdb_paths)} designs for job {job_id}")
    mutation_lease = acquire_workflow_mutation_lease()
    try:
        asyncio.create_task(
            run_with_workflow_mutation_lease(
                mutation_lease,
                annotate_and_update_designs(pdb_paths, design_ids, job_id=str(job_id)),
            )
        )
    except BaseException:
        mutation_lease.close()
        raise
    
    return {
        "message": f"CDR annotation started for {len(pdb_paths)} designs (running in background)",
        "job_id": job_id,
        "status": "started",
        "pending": len(pdb_paths),
        "total": total_count
    }


@router.get("/{job_id}/structure-files")
async def list_structure_files(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    checkpoint_sha256: Optional[str] = None,
):
    """List native structures; a digest explicitly retrieves only its review set."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if checkpoint_sha256 is not None:
        from component_runtime import ResultReference
        checkpoint = (job.awaiting_payload or {}).get('component_checkpoint')
        if not checkpoint or checkpoint.get('checkpoint_sha256') != checkpoint_sha256:
            raise HTTPException(status_code=409, detail='Explicit current checkpoint digest required')
        try:
            if job.execution_target_id:
                from services.remote_execution.executor import retrieve_remote_checkpoint_review
                root = await retrieve_remote_checkpoint_review(session, job, checkpoint)
            else:
                context = json.loads(Path((job.provenance or {})['component_context_path']).read_text())
                root = Path(context['artifact_root'])
            artifacts = []
            for item in checkpoint['artifacts']:
                path = ResultReference(**item).resolve(root)
                artifacts.append({**item, 'path': to_allowed_relative(path), 'name': path.stem,
                                  'filename': path.name, 'type': path.suffix.lstrip('.')})
            structures = [item for item in artifacts if item['type'] in {'pdb', 'cif'}]
            return dict(structures=structures, count=len(structures), artifacts=artifacts,
                        checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=checkpoint_sha256)
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not job.output_dir:
        return {"structures": []}
    
    output_path = resolve_output_dir(job.output_dir)
    if not output_path or not output_path.exists():
        return {"structures": []}
    
    structures = []
    
    # Find all PDB files
    for pdb_file in output_path.glob("**/*.pdb"):
        rel_path = to_allowed_relative(pdb_file)
        structures.append({
            "name": pdb_file.stem,
            "filename": pdb_file.name,
            "path": rel_path,
            "type": "pdb",
            "size_bytes": pdb_file.stat().st_size,
        })
    
    # Find all CIF files
    for cif_file in output_path.glob("**/*.cif"):
        rel_path = to_allowed_relative(cif_file)
        structures.append({
            "name": cif_file.stem,
            "filename": cif_file.name,
            "path": rel_path,
            "type": "cif",
            "size_bytes": cif_file.stat().st_size,
        })
    
    # Sort by name
    structures.sort(key=lambda x: x["name"])
    
    return {"structures": structures, "count": len(structures)}


@router.get('/{job_id}/remote-artifacts')
async def list_remote_artifacts(job_id: str, diagnostics: bool = False, session: AsyncSession = Depends(get_session)):
    """Browse only the sealed, current Job-bound archive; retained entries are explicit history."""
    import asyncio
    from urllib.parse import urlencode, quote
    from services.remote_execution.executor import retained_result_view, RemoteExecutionError

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, 'Job not found')
    try:
        _, relative, manifest, status = await asyncio.to_thread(retained_result_view, job, diagnostics=diagnostics)
    except (RemoteExecutionError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        'attempt_id': status.attempt_id, 'generation': status.generation,
        'result_manifest_sha256': status.result_manifest_sha256,
        'kind': 'diagnostics' if diagnostics else 'current',
        'artifacts': [{
            'relative_path': a.relative_path, 'size_bytes': a.size_bytes, 'sha256': a.sha256,
            'scope': 'current' if Path(a.relative_path).is_relative_to(relative) else 'history',
            'download_url': f'/api/jobs/{quote(job_id, safe="")}/remote-artifacts/download?' + urlencode({
                'path': a.relative_path, 'diagnostics': str(diagnostics).lower(),
                'attempt_id': status.attempt_id, 'manifest_sha256': status.result_manifest_sha256,
            }),
        } for a in manifest.artifacts],
    }


@router.get('/{job_id}/remote-artifacts/download')
async def download_remote_artifact(job_id: str, path: str, attempt_id: str, manifest_sha256: str,
                                   diagnostics: bool = False, session: AsyncSession = Depends(get_session)):
    import asyncio
    from fastapi.responses import FileResponse
    from services.remote_execution.executor import retained_result_view, retained_result_file, RemoteExecutionError

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, 'Job not found')
    try:
        root, _, manifest, status = await asyncio.to_thread(retained_result_view, job, diagnostics=diagnostics)
        if status.attempt_id != attempt_id or status.result_manifest_sha256 != manifest_sha256:
            raise RemoteExecutionError('Requested archive identity is no longer current')
        selected = await asyncio.to_thread(retained_result_file, root, manifest, path)
    except (RemoteExecutionError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(selected, filename=selected.name, media_type='application/octet-stream')


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    tail: int = 200,
    session: AsyncSession = Depends(get_session)
):
    """
    Get log output for a job.
    
    Strategy:
    1. Read the saved nextflow.log from job's output_dir (most reliable)
    2. Parse work directory hashes from that log to find .command.log/.command.err
    3. Fallback to CODE_ROOT/.nextflow.log if no saved log exists
    
    Work dir resolution is wrapped in a timeout to prevent hanging on slow filesystems.
    """
    import asyncio
    import concurrent.futures
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    logs_data = {
        "job_id": job_id,
        "job_name": job.name,
        "status": job.status,
        "command_log": None,
        "command_err": None,
        "nextflow_log": None,
        "exit_code": None,
        "parsed_error": None,
        "nextflow_log_source": None,
    }

    if job.execution_target_id:
        logs_data["nextflow_log_source"] = "remote_pending"

        def _bounded_remote_log(path: Path) -> str | None:
            if not path.is_file():
                return None
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 1_048_576))
                    content = handle.read(1_048_576).decode("utf-8", errors="replace")
                bounded_tail = max(1, min(int(tail), 5000))
                return "\n".join(content.splitlines()[-bounded_tail:])
            except OSError:
                return None

        from services.remote_execution.executor import (
            retained_result_view, retained_result_file, remote_live_logs,
            RemoteExecutionError, RemoteTransportError, TERMINAL_REMOTE_STATES,
        )
        diagnostics = job.status in {'failed', 'cancelled'}
        receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
        active = (job.status in {'queued', 'running', 'awaiting_input'}
                  and receipt.get('state') not in TERMINAL_REMOTE_STATES
                  and job.remote_state not in {'results_available', 'result_pull_failed',
                                               'returning', 'validating_return', 'ingested'})
        try:
            if active:
                logs_data.update(await remote_live_logs(session, job, tail=tail))
            else:
                root, relative, manifest, status = await asyncio.to_thread(retained_result_view, job, diagnostics=diagnostics)
                logs_data['exit_code'] = status.exit_code
                logs_data['remote_result_identity'] = {
                    'attempt_id': status.attempt_id, 'generation': status.generation,
                    'result_manifest_sha256': status.result_manifest_sha256,
                    'kind': 'diagnostics' if diagnostics else 'current',
                }
                for field, name in [('nextflow_log', 'nextflow.log'), ('command_log', 'supervisor.log')]:
                    member = (relative / '_remote' / name).as_posix()
                    if any(a.relative_path == member for a in manifest.artifacts):
                        path = await asyncio.to_thread(retained_result_file, root, manifest, member)
                        logs_data[field] = await asyncio.to_thread(_bounded_remote_log, path)
                logs_data['nextflow_log_source'] = 'remote_diagnostics' if diagnostics else 'remote_returned'
        except (RemoteExecutionError, RemoteTransportError, OSError, ValueError, KeyError) as exc:
            logs_data['remote_read_error'] = str(exc)[:1000]
        logs_data["parsed_error"] = extract_error_from_logs(
            logs_data["command_log"],
            None,
        )
        if not logs_data["parsed_error"] and logs_data["nextflow_log"]:
            logs_data["parsed_error"] = extract_error_from_logs(logs_data["nextflow_log"], None)
        if not logs_data["parsed_error"] and job.error_message:
            logs_data["parsed_error"] = str(job.error_message)[:4000]
        return logs_data
    
    # --- Step 1: Find the nextflow log for THIS job ---
    nf_log_content = None
    output_path = None
    nf_log_candidates = []
    
    if job.output_dir:
        output_path = resolve_output_dir(job.output_dir)
        if output_path:
            nf_log_candidates.append(output_path / "nextflow.log")
            nf_log_candidates.append(output_path / ".nextflow.log")
    
    # A global Nextflow log is only a legacy diagnostic for non-MD jobs.  It is
    # not job-owned and can otherwise disclose another MD launch's failure.
    if job.model_id != "molecular_dynamics":
        nf_log_candidates.append(CODE_ROOT / ".nextflow.log")
    
    for nf_path in nf_log_candidates:
        if nf_path and nf_path.exists():
            try:
                with open(nf_path, 'r') as f:
                    nf_log_content = f.read()
                    lines = nf_log_content.split('\n')
                    logs_data["nextflow_log"] = "\n".join(lines[-tail:])
                    logs_data["nextflow_log_source"] = "job_output" if output_path and nf_path.parent == output_path else "legacy_global"
                break
            except Exception:
                continue
    
    # --- Step 2: Extract work dir hashes and read command logs ---
    # This touches the filesystem which may be slow, so wrap in a timeout.
    def _resolve_work_dir_logs(nf_content: str, tail_lines: int) -> dict:
        """Blocking function to resolve work dir logs. Runs in executor with timeout."""
        result = {"command_log": None, "command_err": None, "exit_code": None}
        if not nf_content:
            return result
        
        import re
        work_dir = get_work_dir()
        
        # Match Nextflow task hash patterns: [xx/yyyyyy]
        hash_pattern = re.compile(r'\[([0-9a-f]{2}/[0-9a-f]{6,})\]')
        workdir_pattern = re.compile(r'work[Dd]ir[:\s]+\S*?/work/([0-9a-f]{2}/[0-9a-f]{6,})')
        
        found_hashes = set()
        for match in hash_pattern.finditer(nf_content):
            found_hashes.add(match.group(1))
        for match in workdir_pattern.finditer(nf_content):
            found_hashes.add(match.group(1))
        
        # Resolve full paths from hashes
        work_dirs_found = []
        for hash_prefix in found_hashes:
            parts = hash_prefix.split('/')
            if len(parts) == 2:
                candidate_dir = work_dir / parts[0]
                try:
                    if candidate_dir.exists():
                        for entry in candidate_dir.iterdir():
                            if entry.name.startswith(parts[1]) and entry.is_dir():
                                work_dirs_found.append(entry)
                except OSError:
                    continue
        
        # Sort by modification time (most recent first)
        try:
            work_dirs_found.sort(
                key=lambda d: d.stat().st_mtime if d.exists() else 0,
                reverse=True
            )
        except OSError:
            pass
        
        # Read command logs from the most recent work dir
        for task_dir in work_dirs_found:
            try:
                cmd_log = task_dir / ".command.log"
                cmd_err = task_dir / ".command.err"
                exit_file = task_dir / ".exitcode"
                
                if cmd_log.exists():
                    with open(cmd_log, 'r') as f:
                        lines = f.readlines()
                        result["command_log"] = "".join(lines[-tail_lines:])
                
                if cmd_err.exists():
                    with open(cmd_err, 'r') as f:
                        err_content = f.read()
                        if err_content.strip():
                            result["command_err"] = err_content
                
                if exit_file.exists():
                    with open(exit_file, 'r') as f:
                        try:
                            result["exit_code"] = int(f.read().strip())
                        except ValueError:
                            pass
                
                if result["command_log"] or result["command_err"]:
                    break
            except OSError:
                continue
        
        return result
    
    # Run the blocking filesystem operations with a 3-second timeout
    try:
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            work_dir_result = await asyncio.wait_for(
                loop.run_in_executor(pool, _resolve_work_dir_logs, nf_log_content, tail),
                timeout=3.0
            )
        logs_data["command_log"] = work_dir_result["command_log"]
        logs_data["command_err"] = work_dir_result["command_err"]
        logs_data["exit_code"] = work_dir_result["exit_code"]
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"Work dir log resolution timed out or failed for job {job_id}: {e}")
    
    # --- Step 3: Parse error summary ---
    # Try command logs first, then fall back to nextflow log for error extraction
    logs_data["parsed_error"] = extract_error_from_logs(
        logs_data["command_log"],
        logs_data["command_err"]
    )
    
    # If no error from command logs but we have nextflow log, extract from there
    if not logs_data["parsed_error"] and nf_log_content:
        logs_data["parsed_error"] = extract_error_from_logs(
            nf_log_content, None
        )
    
    return logs_data


def extract_error_from_logs(
    command_log: Optional[str] = None,
    command_err: Optional[str] = None,
) -> Optional[str]:
    """
    Extract meaningful error message from log files.
    
    Looks for common error patterns:
    - Python tracebacks
    - CUDA errors
    - OOM messages
    - Permission errors
    """
    if not command_log and not command_err:
        return None
    
    combined = (command_log or "") + "\n" + (command_err or "")
    lines = combined.split('\n')
    
    # Priority patterns to search for
    error_patterns = [
        "CUDA out of memory",
        "CUDA error:",
        "RuntimeError:",
        "OSError:",
        "FileNotFoundError:",
        "ModuleNotFoundError:",
        "OOM",
        "Killed",
        "Traceback (most recent call last):",
        "Error:",
        "bad substitution",
    ]
    
    # Find the most relevant error
    for pattern in error_patterns:
        for i, line in enumerate(lines):
            if pattern in line:
                # Return context around the error (5 lines before, 10 after)
                start = max(0, i - 2)
                end = min(len(lines), i + 8)
                return "\n".join(lines[start:end]).strip()
    
    # Fallback: return last 5 non-empty lines
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        return "\n".join(non_empty[-5:]).strip()
    
    return None

"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body, Request, Response
from fastapi.params import Depends as DependsParam
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, or_, and_
from sqlalchemy.exc import OperationalError
from typing import Optional, List, Dict, Any, Literal
from types import SimpleNamespace
from copy import deepcopy
import asyncio
import uuid
import os
import hashlib
import logging
import json
import shutil
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

from antibody_pipeline_contract import (
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import (
    current_launch_context_id,
    get_session,
    Job,
    ExecutionTarget,
    Design,
    FrustraMPNNResult,
    ConformationalMappingRequest,
)
from experiment_database import get_experiment_session
from services.job_stage_progress import project_execution_stages
from services.result_contracts import build_review_artifact_manifest, resolve_result_contract
from paths import (
    get_code_root,
    get_data_root,
    get_results_dir,
    get_work_dir,
    resolve_allowed_path,
    resolve_runtime_data_path,
    to_allowed_relative,
)
from services.binder_source_materialization import StructureSourceRequest
from runtime_policy import (
    acquire_workflow_mutation_lease,
    run_with_workflow_mutation_lease,
)
from schemas import ExecutionPolicy, JobCreate, JobResponse, JobList, JobSummaryResponse, JobSummaryList, JobStatus
from schemas import BinderNavigationJob, BinderNavigationJobList, BC2NavigationResponse
from services.binder_navigation_jobs import navigation_columns, navigation_job
from services.job_control import cancel_job_lineage, reject_generic_md_lifecycle_control
from services import alignment_access, ont_submission_trust, stage_reporting, ont_ngs_contract
from services.proteinbase_importer import import_proteinbase_bundle
from services.rfd3_generation import (
    file_sha256,
)
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    resolve_launch_context,
    validate_bound_job_request,
    workflow_pinned_gpu,
)
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)

from model_registry import get_registry
from services.stage_review import (
    REVIEWABLE_STAGES,
    ensure_stage_review_rows,
    gate_file_for_stage,
    has_stage_gate,
    load_review_gate_snapshot,
    nextflow_history_status,
    nextflow_history_status_for_run_dir,
    resolve_nextflow_run_dir,
    refresh_gate_payload,
)
from services.job_requests import (
    JobExecutionPlanPreview,
    _coerce_nonempty_text,
    _dedupe_preserve_order,
    _execution_plan_preview,
    _is_antibody_launch,
    _normalize_antibody_job_params,
    _normalize_antibody_runtime_paths,
    _normalize_boltz_cp_params_for_validation,
    _normalize_nanopore_runtime_paths,
    _normalize_structure_geometry_params,
    _normalize_structure_prediction_pred_method,
    _normalize_structure_runtime_paths,
    _selection_manifest_path,
    _should_normalize_antibody_job_params,
)
from services.job_readback import (
    _public_job_output_dir,
    _public_job_params,
    _resolve_requested_design_count,
    _resolve_stage_state_for_response,
    _saved_review_filter_entries,
    _serialized_saved_review_filter_sets,
    read_job,
    resolve_output_dir,
)
from services.job_readback import (
    SavedReviewFilterSet,
)
from services.job_control import (
    _load_remaining_output_dir_refs,
    _plan_output_dir_cleanup,
)
from services.binder_continuation import (
    AA_CODES,
    _awaiting_stage_to_resume_hint,
    _build_antibody_iteration_job,
    _build_selected_loop_scope,
    _create_antibody_selection_dir,
    _create_generic_selection_dir,
    _derive_source_stage_payload,
    _extract_chain_records_from_pdb,
    _is_protein_local_redesign_job,
    _materialize_antibody_selection,
    _materialize_protein_local_selection,
    _native_gate_resume_params,
    _prune_iteration_params,
    _resolve_antibody_root_job,
    _resolve_design_structure_path,
    _should_spawn_antibody_refinement_on_resume,
    _validate_selected_design_owners,
    _write_selection_manifest,
)
from services.job_submission import (
    _ensure_job_resume_identity,
    preview_submission,
    _raise_if_workflow_launches_disabled,
    _require_prepared_remote_review,
    _validate_antibody_runtime_paths,
    _validate_fampnn_checkpoint_requirements,
    _validate_protenix_checkpoint_requirements,
    _validate_protenix_template_requirements,
    submit_job,
)
async def _visible_job_path(request: Request, session: AsyncSession = Depends(get_session)):
    from services.job_control import visible_job_clause
    for key in ("job_id", "parent_id"):
        identifier = request.path_params.get(key)
        if identifier and await session.scalar(select(Job.id).where(
                Job.id == identifier, ~visible_job_clause())):
            raise HTTPException(status_code=404, detail="Job not found")


router = APIRouter(dependencies=[Depends(_visible_job_path)])

# Project root for resolving code-relative paths
CODE_ROOT = get_code_root()
UUID_SUFFIX_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
PRESERVED_GATE_PAYLOAD_KEYS = ("review_filter_sets",)
BOLTZ_ITERATION_FORWARD_KEYS = (
    "boltz_use_msa",
    "boltz_sampling_steps",
    "boltz_recycling_steps",
    "boltz_num_samples",
    "boltz_diffusion_samples",
    "boltz_max_parallel_samples",
    "boltz_use_potentials",
    "boltz_step_scale",
    "boltz_method",
    "boltz_predict_affinity",
    "boltz_sampling_steps_affinity",
    "boltz_diffusion_samples_affinity",
    "boltz_affinity_mw_correction",
    "boltz_anchor_target",
    "boltz_anchor_strict",
    "boltz_target_geometry_mode",
    "boltz_extra_config",
    "msa_preset",
    "msa_use_gpu",
    "msa_local_db",
    "msa_cache_dir",
    "msa_threads",
    "colabfold_api_host",
    "colabfold_api_min_interval",
    "colabfold_api_poll_interval",
    "msa_gpu_mode",
    "msa_gpu_threshold",
    "msa_preferred_gpus",
    "msa_excluded_gpus",
    "msa_gpu_server_mode",
    "msa_gpu_server_wait_timeout",
    "msa_gpu_server_db_load_mode",
    "msa_gpu_server_startup_wait",
)
def _copy_present_params(source: Dict[str, Any], dest: Dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in source:
            dest[key] = source[key]


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


def _retain_ont_replay_lineage(source: Job, replay: Job) -> None:
    """Copy data ancestry, never historical approval, leases or attempt state."""
    params = dict(replay.params or {})
    params["reorchestrated_from_job_id"] = source.id
    for key in (
        "lineage_root_job_id", "stage_family", "stage_mode", "source_stage_job_id",
        "source_stage_family", "source_stage_mode", "selection_source_type",
        "selection_source_job_id", "source_selection_count",
    ):
        value = params.get(key, (source.params or {}).get(key, getattr(source, key, None)))
        if key == "lineage_root_job_id":
            value = value or source.id
        if value is not None:
            setattr(replay, key, value)
            params[key] = value
    independent = bool(params.get("barcode_mapping_binding") or params.get("pooled_assignment_release_binding"))
    replay.parent_job_id = None if independent else source.parent_job_id
    replay.child_stage = None if independent else source.child_stage
    replay.params = params


class ResubmitJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    execution_target_id: str | None = Field(default=None, min_length=1, max_length=128)
    execution_policy: ExecutionPolicy | None = None


class ResumeJobRequest(BaseModel):
    """Resume overrides; omitted placement inherits, explicit null selects Local."""
    checkpoint_id: Optional[str] = None
    checkpoint_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_decision: Optional[Dict[str, Any]] = None
    execution_target_id: Optional[str] = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    launch_context_id: Optional[str] = None
    from_stage: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    name_suffix: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def reject_scientific_revision_input(cls, data: Any) -> Any:
        from services.core_protein_scientific_contract import reject_reserved_marker
        reject_reserved_marker(data)
        return data


class ContinueProteinLocalReviewRequest(BaseModel):
    """Continue a paused protein local redesign review using a selected subset."""
    design_ids: List[str] = Field(default_factory=list)
    name_suffix: Optional[str] = None


class OpenStageGateRequest(BaseModel):
    """Internal workflow request to mark a job as awaiting user input."""
    payload: Dict[str, Any] = Field(default_factory=dict)


from schemas import CandidateRef


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


class ProteinBaseBundleImportRequest(BaseModel):
    """Request payload for importing a ProteinBase JSONL bundle into the job/design viewer."""
    bundle_path: str = Field(..., min_length=1)
    dataset_name: str = Field(..., min_length=1)
    job_name: Optional[str] = Field(default=None)


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


class AntibodyCdrIndelConfig(BaseModel):
    """Configuration for viewer-launched CDR indel rounds."""
    loop_ids: List[str] = Field(default_factory=list)
    variants_per_design: int = Field(default=8, ge=1, le=200)
    allow_insertions: bool = True
    allow_deletions: bool = True
    indel_sizes: List[int] = Field(default_factory=lambda: [1])
    indel_probability: float = Field(default=1.0, ge=0.0, le=1.0)
    allowed_aas: List[str] = Field(default_factory=list)
    blocked_aas: List[str] = Field(default_factory=list)
    predictor: str = Field(default="protenix")
    msa_provider: str = Field(default="colabfold_api")


class ManualMutagenesisConfig(BaseModel):
    """Configuration for explicit user-provided mutation rounds."""
    chain_id: Optional[str] = None
    mutation_sets: List[str] = Field(default_factory=list)
    predictor: str = Field(default="protenix")
    msa_provider: str = Field(default="colabfold_api")


class AntibodyIterationLaunchRequest(BaseModel):
    """Launch a new antibody round from selected design structures."""
    source_job_id: str = Field(..., min_length=1)
    design_ids: List[str] = Field(default_factory=list)
    review_filter_set_id: Optional[str] = None
    action: str = Field(..., min_length=1)
    name_suffix: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    cdr_indel_config: Optional[AntibodyCdrIndelConfig] = None
    manual_mutagenesis_config: Optional[ManualMutagenesisConfig] = None
    frustrampnn_settings: Optional[FrustraMPNNRequestedSettings] = None

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_frustrampnn_settings(
        cls,
        value: Any,
    ) -> Optional[FrustraMPNNRequestedSettings]:
        if value is None:
            return None
        return validate_complete_requested_settings(value)


class AntibodyIterationLaunchResponse(BaseModel):
    """Response for viewer-driven antibody iteration actions."""

    model_config = ConfigDict(extra="forbid")

    message: str
    action: str
    source_job_id: str
    root_job_id: str
    selection_dir: str
    selected_design_count: int
    launched_job: JobResponse
    launched_jobs: List[JobResponse] = Field(default_factory=list)
    fanout_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ManualMutagenesisLaunchRequest(BaseModel):
    """Launch explicit manual mutation sets from selected structures."""
    source_job_id: str = Field(..., min_length=1)
    design_ids: List[str] = Field(default_factory=list)
    review_filter_set_id: Optional[str] = None
    config: ManualMutagenesisConfig
    name_suffix: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)


class ResequenceLaunchRequest(BaseModel):
    """Resequence and validate exact structures from any generator."""
    sources: List[StructureSourceRequest] = Field(default_factory=list, max_length=500)
    backbone_input_pdbs: Optional[str] = None
    retained_params: Dict[str, Any] = Field(default_factory=dict)
    execution_target_id: Optional[str] = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    launch_context_id: Optional[str] = None
    seq_method: Literal["fampnn", "mpnn", "ligandmpnn", "caliby", "protonpottsmpnn"] = "fampnn"
    designer_mode: Optional[str] = None
    designer_settings: Dict[str, Any] = Field(default_factory=dict)
    validator_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    design_chains: str = Field(..., min_length=1)
    fixed_positions: str = ""
    designable_positions: str = ""
    structure_validators: List[str] = Field(default_factory=lambda: ["protenix_v2"])
    msa_provider: Literal["colabfold_api", "neurosnap_api"] = "colabfold_api"
    name: str = Field("resequence", min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_source(self):
        if not self.sources and not str(self.backbone_input_pdbs or "").strip():
            raise ValueError("Resequence requires structures or a retained basket")
        return self


class ManualMutagenesisLaunchResponse(BaseModel):
    """Response for viewer-driven manual mutation launches."""
    message: str
    source_job_id: str
    selected_design_count: int
    variant_count: int
    launched_job: JobResponse


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


def _candidate_child_batch_aliases(
    *,
    job: Optional[Job],
    root_job_id: str,
    provided_batch_name: Optional[str] = None,
) -> List[str]:
    aliases: List[str] = []

    def add(value: Any) -> None:
        text = _coerce_nonempty_text(value)
        if text and text not in aliases:
            aliases.append(text)

    add(provided_batch_name)

    if job is None:
        return aliases

    params = job.params if isinstance(job.params, dict) else {}
    add(job.batch_name)
    add(params.get("batch_name"))

    job_name = _coerce_nonempty_text(params.get("job_name")) or _coerce_nonempty_text(job.name)
    if job_name:
        add(f"{job_name}_{root_job_id}")

    model_id_normalized = (job.model_id or "").strip().lower()
    if _is_antibody_launch(job.model_id, params):
        add(f"antibody_batch_{root_job_id}")
    elif model_id_normalized == "boltzgen":
        add(_coerce_nonempty_text(params.get("name")) or "boltzgen_campaign")

    return aliases


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


async def _resolve_resume_child_batch_name(
    session: AsyncSession,
    job: Job,
    root_job_id: str,
) -> Optional[str]:
    parent_ids = _dedupe_preserve_order([root_job_id, job.id])
    original_rows = await session.execute(
        select(Job.batch_name)
        .where(
            Job.parent_job_id == root_job_id,
            Job.batch_name.isnot(None),
        )
        .order_by(Job.created_at.asc())
    )
    original_batch_names = [
        batch_name
        for batch_name in original_rows.scalars().all()
        if _coerce_nonempty_text(batch_name)
    ]
    if original_batch_names:
        return original_batch_names[0]

    lineage_rows = await session.execute(
        select(Job.batch_name)
        .where(
            Job.parent_job_id.in_(parent_ids),
            Job.batch_name.isnot(None),
        )
        .order_by(Job.created_at.asc())
    )
    lineage_batch_names = [
        batch_name
        for batch_name in lineage_rows.scalars().all()
        if _coerce_nonempty_text(batch_name)
    ]
    if lineage_batch_names:
        return lineage_batch_names[0]

    aliases = _candidate_child_batch_aliases(job=job, root_job_id=root_job_id)
    return aliases[0] if aliases else None


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


def _resolve_saved_review_filter_set(
    review_filter_set_id: Optional[str],
    candidate_jobs: List[Optional[Job]],
) -> Optional[SavedReviewFilterSet]:
    filter_set_id = str(review_filter_set_id or "").strip()
    if not filter_set_id:
        return None

    seen_job_ids: set[str] = set()
    for job in candidate_jobs:
        if job is None or job.id in seen_job_ids:
            continue
        seen_job_ids.add(job.id)
        for saved_set in _iter_saved_review_filter_sets(job):
            if saved_set.id == filter_set_id:
                return saved_set

    raise HTTPException(status_code=404, detail="Saved review dataset not found.")


def _resolve_launch_design_ids(
    requested_design_ids: List[str],
    saved_filter_set: Optional[SavedReviewFilterSet],
) -> List[str]:
    design_ids = _normalize_design_ids(requested_design_ids)
    if design_ids:
        return design_ids
    if saved_filter_set is None:
        return []

    saved_design_ids = _normalize_design_ids(saved_filter_set.design_ids)
    if not saved_design_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Saved review dataset '{saved_filter_set.name}' does not have frozen design membership. "
                "Re-save the dataset from the review UI before launching it."
            ),
        )
    return saved_design_ids


AA_CODES_REVERSE = {value: key for key, value in AA_CODES.items()}
PDB_BACKBONE_ATOMS = {"N", "CA", "C", "O", "OXT"}

STANDARD_AMINO_ACIDS = list("ACDEFGHIKLMNPQRSTVWY")
DEFAULT_CDR_POSITION_RANGES = {
    "H1": (27, 38),
    "H2": (56, 65),
    "H3": (105, 117),
    "L1": (27, 38),
    "L2": (56, 65),
    "L3": (105, 117),
}


def _parse_chain_list(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        values = [part.strip() for part in str(raw_value).split(",")]
    return [str(value).strip().upper() for value in values if str(value).strip()]


def _extract_chain_records_from_structure(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    if path.suffix.lower() not in {'.cif', '.mmcif'}:
        return _extract_chain_records_from_pdb(path)
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict

    data = MMCIF2Dict(str(path))
    def column(*names: str) -> list[str]:
        for name in names:
            if name in data:
                return data[name]
        return []
    atoms = column('_atom_site.auth_atom_id', '_atom_site.label_atom_id')
    residues = column('_atom_site.auth_comp_id', '_atom_site.label_comp_id')
    chains = column('_atom_site.auth_asym_id', '_atom_site.label_asym_id')
    numbers = column('_atom_site.auth_seq_id', '_atom_site.label_seq_id')
    insertions = column('_atom_site.pdbx_PDB_ins_code')
    groups = column('_atom_site.group_PDB')
    if not (len(atoms) == len(residues) == len(chains) == len(numbers) == len(groups)):
        return {}
    if not insertions:
        insertions = ['?'] * len(atoms)
    if len(insertions) != len(atoms):
        return {}
    records: Dict[str, List[Dict[str, Any]]] = {}
    seen: Dict[str, set[tuple[int, str]]] = {}
    for group, atom, residue, chain, number, insertion in zip(
            groups, atoms, residues, chains, numbers, insertions):
        if group != 'ATOM' or atom != 'CA' or residue not in AA_CODES:
            continue
        try:
            resseq = int(number)
        except ValueError:
            continue
        chain_id = chain.upper()
        icode = '' if insertion in {'.', '?'} else insertion
        key = (resseq, icode)
        if key in seen.setdefault(chain_id, set()):
            continue
        seen[chain_id].add(key)
        records.setdefault(chain_id, []).append({'resseq': resseq, 'icode': icode,
                                                  'aa': AA_CODES[residue]})
    return records


def _resolve_loop_region_map(root_job: Job) -> Dict[str, tuple[int, int]]:
    params = root_job.params if isinstance(root_job.params, dict) else {}
    region_map: Dict[str, tuple[int, int]] = {}

    manual_defs = params.get("manual_cdr_definitions")
    if isinstance(manual_defs, list):
        for entry in manual_defs:
            if not isinstance(entry, dict):
                continue
            loop_id = str(entry.get("id") or "").strip().upper()
            residues = entry.get("residues") or []
            residue_numbers: List[int] = []
            for residue in residues:
                match = str(residue).strip().upper()
                parsed = match[1:] if match[:1].isalpha() else match
                if parsed.isdigit():
                    residue_numbers.append(int(parsed))
            if loop_id and residue_numbers:
                region_map[loop_id] = (min(residue_numbers), max(residue_numbers))

    return region_map


def _resolve_loop_target_chain(root_job: Job, loop_ids: List[str], available_chain_ids: List[str]) -> str:
    loop_prefixes = {loop_id[:1].upper() for loop_id in loop_ids if loop_id}
    if not loop_prefixes:
        raise HTTPException(status_code=422, detail="At least one CDR loop must be selected for indel generation.")
    if len(loop_prefixes) != 1:
        raise HTTPException(
            status_code=422,
            detail="CDR indel rounds currently require all selected loops to belong to the same chain family (all H or all L).",
        )

    prefix = next(iter(loop_prefixes))
    antibody_chains = _parse_chain_list((root_job.params or {}).get("antibody_chains") if isinstance(root_job.params, dict) else None)
    candidate = None
    if prefix == "H" and antibody_chains:
        candidate = antibody_chains[0]
    elif prefix == "L" and len(antibody_chains) > 1:
        candidate = antibody_chains[1]

    if candidate and candidate in available_chain_ids:
        return candidate

    if len(available_chain_ids) == 1:
        return available_chain_ids[0]

    for chain_id in available_chain_ids:
        if chain_id.startswith(prefix):
            return chain_id

    raise HTTPException(
        status_code=422,
        detail=f"Could not resolve binder chain for selected loops {', '.join(loop_ids)} from chains {', '.join(available_chain_ids)}.",
    )


def _build_mutation_regions(
    chain_records: List[Dict[str, Any]],
    region_map: Dict[str, tuple[int, int]],
    loop_ids: List[str],
) -> Dict[str, tuple[int, int]]:
    regions: Dict[str, tuple[int, int]] = {}
    for loop_id in loop_ids:
        loop_key = loop_id.strip().upper()
        bounds = region_map.get(loop_key) or DEFAULT_CDR_POSITION_RANGES.get(loop_key)
        if not bounds:
            continue
        start_resseq, end_resseq = bounds
        positions = [
            index
            for index, residue in enumerate(chain_records, start=1)
            if start_resseq <= int(residue["resseq"]) <= end_resseq
        ]
        if positions:
            regions[loop_key] = (min(positions), max(positions))
    return regions


def _detect_loop_sequence_regions(
    design_path: Path,
    binder_chain_id: str,
    loop_ids: List[str],
) -> Dict[str, tuple[int, int]]:
    try:
        from services.cdr_annotator import annotate_pdb
    except Exception:
        return {}

    prefixes = {loop_id[:1].upper() for loop_id in loop_ids if loop_id}
    if len(prefixes) != 1:
        return {}

    preferred_key = next(iter(prefixes))
    try:
        annotation = annotate_pdb(str(design_path), preferred_chains={preferred_key: binder_chain_id})
    except Exception:
        return {}
    if not annotation:
        return {}

    detected: Dict[str, tuple[int, int]] = {}
    for loop_id in loop_ids:
        attr_name = f"cdr_{loop_id.strip().lower()}_seq_range"
        seq_range = getattr(annotation, attr_name, None)
        if not seq_range or len(seq_range) != 2:
            continue
        try:
            start = int(seq_range[0]) + 1
            end = int(seq_range[1]) + 1
        except (TypeError, ValueError):
            continue
        if start > 0 and end >= start:
            detected[loop_id.strip().upper()] = (start, end)
    return detected


def _normalize_aa_pool(allowed_aas: List[str], blocked_aas: List[str]) -> List[str]:
    allowed = [aa.strip().upper() for aa in allowed_aas if aa and aa.strip().upper() in STANDARD_AMINO_ACIDS]
    blocked = {aa.strip().upper() for aa in blocked_aas if aa and aa.strip().upper() in STANDARD_AMINO_ACIDS}
    pool = allowed if allowed else STANDARD_AMINO_ACIDS
    filtered = [aa for aa in pool if aa not in blocked]
    return filtered or STANDARD_AMINO_ACIDS


MANUAL_MUTATION_TOKEN_RE = re.compile(r"^([A-Za-z])(\d+)([A-Za-z])$")


def _resolve_manual_mutagenesis_chain(
    requested_chain: Optional[str],
    source_job: Job,
    available_chain_ids: List[str],
) -> str:
    normalized_requested = str(requested_chain or "").strip().upper()
    if normalized_requested:
        if normalized_requested not in available_chain_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Requested chain '{normalized_requested}' is not present in the selected structure. "
                    f"Available chains: {', '.join(available_chain_ids)}."
                ),
            )
        return normalized_requested

    antibody_chains = _parse_chain_list((source_job.params or {}).get("antibody_chains") if isinstance(source_job.params, dict) else None)
    for chain_id in antibody_chains:
        if chain_id in available_chain_ids:
            return chain_id

    antigen_chains = _parse_chain_list((source_job.params or {}).get("antigen_chains") if isinstance(source_job.params, dict) else None)
    non_antigen_candidates = [chain_id for chain_id in available_chain_ids if chain_id not in set(antigen_chains)]
    if antigen_chains and len(non_antigen_candidates) == 1:
        return non_antigen_candidates[0]

    if len(available_chain_ids) == 1:
        return available_chain_ids[0]

    raise HTTPException(
        status_code=422,
        detail=(
            "Selected designs contain multiple protein chains. Specify a chain ID for manual mutagenesis "
            f"(available: {', '.join(available_chain_ids)})."
        ),
    )


def _parse_manual_mutation_set(raw_mutation_set: str) -> List[Dict[str, Any]]:
    tokens = [
        token.strip().upper()
        for token in re.split(r"[\s,;]+", str(raw_mutation_set or "").strip())
        if token.strip()
    ]
    if not tokens:
        raise HTTPException(status_code=422, detail="Each manual mutation set must contain at least one substitution like S31Y.")

    parsed: List[Dict[str, Any]] = []
    seen_positions: set[int] = set()
    for token in tokens:
        match = MANUAL_MUTATION_TOKEN_RE.match(token)
        if not match:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported manual mutation token '{token}'. Use substitutions like S31Y or comma-separated sets like S31Y,K58R.",
            )
        from_aa, pos_raw, to_aa = match.groups()
        position = int(pos_raw)
        if from_aa not in STANDARD_AMINO_ACIDS or to_aa not in STANDARD_AMINO_ACIDS:
            raise HTTPException(status_code=422, detail=f"Mutation token '{token}' contains a non-standard amino acid code.")
        if from_aa == to_aa:
            raise HTTPException(status_code=422, detail=f"Mutation token '{token}' does not change the residue.")
        if position in seen_positions:
            raise HTTPException(status_code=422, detail=f"Manual mutation set '{raw_mutation_set}' mutates position {position} more than once.")
        seen_positions.add(position)
        parsed.append(
            {
                "from": from_aa,
                "to": to_aa,
                "position": position,
                "summary": f"{from_aa}{position}{to_aa}",
            }
        )

    return sorted(parsed, key=lambda entry: int(entry["position"]))


def _generate_manual_mutagenesis_variants(
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
) -> List[Dict[str, Any]]:
    raw_mutation_sets = [entry.strip() for entry in config.mutation_sets if isinstance(entry, str) and entry.strip()]
    if not raw_mutation_sets:
        raise HTTPException(status_code=422, detail="Add at least one manual mutation set before launching.")

    parsed_mutation_sets = [_parse_manual_mutation_set(entry) for entry in raw_mutation_sets]
    variants: List[Dict[str, Any]] = []

    for design in designs:
        design_path = _resolve_design_structure_path(design.pdb_path)
        chain_records = _extract_chain_records_from_structure(design_path)
        if not chain_records:
            raise HTTPException(status_code=422, detail=f"Could not extract protein chains from '{design.name}'.")

        available_chain_ids = list(chain_records.keys())
        binder_chain_id = _resolve_manual_mutagenesis_chain(config.chain_id, source_job, available_chain_ids)
        binder_records = chain_records.get(binder_chain_id) or []
        base_sequence = "".join(record["aa"] for record in binder_records)
        if not base_sequence:
            raise HTTPException(status_code=422, detail=f"Could not extract a mutable sequence for chain '{binder_chain_id}' in '{design.name}'.")

        for idx, mutation_set in enumerate(parsed_mutation_sets, start=1):
            seq_chars = list(base_sequence)
            mutation_meta: List[Dict[str, Any]] = []
            summary_tokens: List[str] = []
            fixed_spec_tokens: List[str] = []
            for mutation in mutation_set:
                position = int(mutation["position"])
                if position < 1 or position > len(seq_chars):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Mutation '{mutation['summary']}' is outside the length of chain '{binder_chain_id}' "
                            f"for '{design.name}' (length {len(seq_chars)})."
                        ),
                    )
                wildtype = seq_chars[position - 1]
                if wildtype != mutation["from"]:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Mutation '{mutation['summary']}' does not match '{design.name}' chain '{binder_chain_id}'. "
                            f"Expected {wildtype}{position}{mutation['to']}."
                        ),
                    )
                seq_chars[position - 1] = mutation["to"]
                mutation_meta.append(
                    {
                        "type": "substitution",
                        "position": position,
                        "from": mutation["from"],
                        "to": mutation["to"],
                        "summary": mutation["summary"],
                    }
                )
                summary_tokens.append(mutation["summary"])
                try:
                    residue_record = binder_records[position - 1]
                except IndexError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Could not map mutation '{mutation['summary']}' onto '{design.name}' chain '{binder_chain_id}'.",
                    ) from exc
                fixed_spec_tokens.append(f"{binder_chain_id}{int(residue_record['resseq'])}")

            variant_sequence = "".join(seq_chars)
            complex_components = []
            for chain_id, records in chain_records.items():
                sequence = variant_sequence if chain_id == binder_chain_id else "".join(record["aa"] for record in records)
                complex_components.append(
                    {
                        "type": "protein",
                        "id": chain_id,
                        "sequence": sequence,
                    }
                )

            summary = "_".join(summary_tokens)
            variants.append(
                {
                    "name": f"{design.name}_{summary}_{idx}",
                    "sequence": variant_sequence,
                    "complex_components": complex_components,
                    "source_design_id": design.id,
                    "source_design_name": design.name,
                    "source_design_job_id": design.job_id,
                    "source_stage_family": design.stage_family,
                    "source_stage_mode": design.stage_mode,
                    "lineage_root_job_id": design.lineage_root_job_id,
                    "parent_design_id": design.parent_design_id,
                    "origin_design_id": design.origin_design_id,
                    "origin_backbone_design_id": design.origin_backbone_design_id,
                    "selected_loop_scope": design.selected_loop_scope,
                    "source_pdb_path": str(design_path),
                    "binder_chain_id": binder_chain_id,
                    "mutation": mutation_meta,
                    "locked_positions_spec": ",".join(fixed_spec_tokens),
                }
            )

    return variants


def _write_mutated_seed_pdb(
    source_path: Path,
    dest_path: Path,
    binder_chain_id: str,
    mutation_meta: List[Dict[str, Any]],
) -> None:
    chain_records = _extract_chain_records_from_pdb(source_path)
    binder_records = chain_records.get(binder_chain_id) or []
    if not binder_records:
        raise HTTPException(
            status_code=422,
            detail=f"Could not find chain '{binder_chain_id}' in seed structure '{source_path.name}'.",
        )

    residue_updates: Dict[tuple[int, str], str] = {}
    for mutation in mutation_meta:
        if str(mutation.get("type")).lower() != "substitution":
            continue
        position = int(mutation["position"])
        if position < 1 or position > len(binder_records):
            raise HTTPException(
                status_code=422,
                detail=f"Mutation position {position} is outside chain '{binder_chain_id}' in '{source_path.name}'.",
            )
        residue_record = binder_records[position - 1]
        to_three = AA_CODES_REVERSE.get(str(mutation["to"]).upper())
        if not to_three:
            raise HTTPException(status_code=422, detail=f"Unsupported amino acid '{mutation['to']}' in mutation seed.")
        residue_updates[(int(residue_record["resseq"]), str(residue_record.get("icode") or ""))] = to_three

    with source_path.open() as src, dest_path.open("w") as dst:
        for line in src:
            if line.startswith(("ATOM  ", "HETATM")) and len(line) >= 27:
                chain_id = (line[21].strip() or "_").upper()
                if chain_id == binder_chain_id:
                    try:
                        resseq = int(line[22:26].strip())
                    except ValueError:
                        resseq = None
                    icode = line[26].strip()
                    if resseq is not None and (resseq, icode) in residue_updates:
                        atom_name = line[12:16].strip()
                        if atom_name not in PDB_BACKBONE_ATOMS:
                            continue
                        resname = residue_updates[(resseq, icode)]
                        line = f"{line[:17]}{resname:>3}{line[20:]}"
            dst.write(line)


def _materialize_substitution_seed_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    variants: List[Dict[str, Any]],
    action: str,
) -> tuple[Path, Path]:
    selection_dir = _create_antibody_selection_dir(action)
    manifest_items: List[Dict[str, Any]] = []
    fixed_positions_by_pdb: Dict[str, str] = {}

    for idx, variant in enumerate(variants, start=1):
        source_path = Path(str(variant["source_pdb_path"]))
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(variant["name"]))[:120]
        dest_path = selection_dir / f"{idx:03d}_{safe_name}.pdb"
        _write_mutated_seed_pdb(
            source_path=source_path,
            dest_path=dest_path,
            binder_chain_id=str(variant["binder_chain_id"]),
            mutation_meta=list(variant.get("mutation") or []),
        )
        manifest_items.append({
            "variant_name": variant["name"],
            "design_id": variant.get("source_design_id"),
            "design_name": variant.get("source_design_name"),
            "design_job_id": variant.get("source_design_job_id"),
            "design_stage_family": variant.get("source_stage_family"),
            "design_stage_mode": variant.get("source_stage_mode"),
            "lineage_root_job_id": variant.get("lineage_root_job_id"),
            "parent_design_id": variant.get("parent_design_id"),
            "origin_design_id": variant.get("origin_design_id"),
            "origin_backbone_design_id": variant.get("origin_backbone_design_id"),
            "source_design_name": variant.get("source_design_name"),
            "source_pdb_path": str(source_path),
            "selection_pdb_path": str(dest_path),
            "selected_loop_scope": variant.get("selected_loop_scope"),
            "binder_chain_id": variant.get("binder_chain_id"),
            "mutation": variant.get("mutation"),
        })
        if variant.get("locked_positions_spec"):
            fixed_positions_by_pdb[dest_path.stem] = str(variant["locked_positions_spec"])

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
        fixed_positions_by_pdb=fixed_positions_by_pdb,
    )
    return selection_dir, selection_dir / "mutation_fixed_positions.json"


def _build_manual_mutagenesis_iteration_job(
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    predictor = str(config.predictor or "protenix").strip().lower()
    if predictor == "boltz":
        predictor = "boltz2"
    if predictor not in {"protenix", "boltz2"}:
        raise HTTPException(status_code=422, detail="Manual mutagenesis currently supports predictor='protenix' or 'boltz2'.")

    variants = _generate_manual_mutagenesis_variants(source_job, designs, config)

    base_params = _prune_iteration_params(source_job.params if isinstance(source_job.params, dict) else {})
    requested_msa_provider = str(config.msa_provider or "local").strip().lower()
    if requested_msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(status_code=422, detail="msa_provider must be 'local' or 'colabfold_api'.")

    from services.msa_policy import resolve_search_backend
    try:
        effective_msa_provider = resolve_search_backend(requested_msa_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    launch_params: Dict[str, Any] = {
        "pred_method": "protenix" if predictor == "protenix" else "boltz",
        "mutagenesis_variants": variants,
        "msa_force_refresh": True,
        "msa_provider": effective_msa_provider,
        "pinned_gpus": base_params.get("pinned_gpus"),
        "lock_gpus": base_params.get("lock_gpus"),
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": (
            ((source_job.params or {}).get("iteration_source_root_job_id") if isinstance(source_job.params, dict) else None)
            or source_job.parent_job_id
            or source_job.id
        ),
        "iteration_source_design_ids": [design.id for design in designs],
        "iteration_action": "manual_mutagenesis_round",
        "manual_mutagenesis_config": config.model_dump(),
        "run_frustrampnn": False,
    }

    if predictor == "protenix":
        for key in (
            "protenix_model_weights",
            "protenix_seeds",
            "protenix_n_sample",
            "protenix_n_step",
            "protenix_n_cycle",
            "protenix_use_msa",
            "protenix_use_template",
            "protenix_enable_cache",
            "protenix_enable_fusion",
            "protenix_msa_backend",
            "protenix_auto_oom_retry",
            "protenix_oom_retry_attempts",
            "msa_preset",
            "msa_use_gpu",
            "msa_local_db",
            "msa_cache_dir",
            "msa_threads",
            "colabfold_api_host",
            "colabfold_api_min_interval",
            "colabfold_api_poll_interval",
            "msa_gpu_mode",
            "msa_gpu_threshold",
            "msa_preferred_gpus",
            "msa_excluded_gpus",
            "msa_gpu_server_mode",
            "msa_gpu_server_wait_timeout",
            "msa_gpu_server_db_load_mode",
            "msa_gpu_server_startup_wait",
        ):
            if key in base_params:
                launch_params[key] = base_params[key]
    else:
        _copy_present_params(base_params, launch_params, BOLTZ_ITERATION_FORWARD_KEYS)

    if param_overrides:
        launch_params.update(dict(param_overrides))

    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else "manual_mutagenesis"
    model_id = "protenix" if predictor == "protenix" else "boltz2"
    launch_request = JobCreate(
        name=f"{source_job.name}_{suffix}",
        model_id=model_id,
        mode="complex",
        params=launch_params,
        execution_target_id=source_job.execution_target_id,
        execution_policy=ExecutionPolicy.from_params(source_job.params),
        pinned_gpu=None,
    )
    message_note = ""
    if requested_msa_provider == "colabfold_api":
        message_note = " ColabFold API batch admission remains blocked; no local fallback."
    return launch_request, len(variants), message_note


def _build_manual_mutagenesis_seeded_refinement_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    variants = _generate_manual_mutagenesis_variants(source_job, designs, config)
    selection_dir, fixed_json_path = _materialize_substitution_seed_selection(
        root_job=root_job,
        source_job=source_job,
        designs=designs,
        variants=variants,
        action="mutation_seeded_refinement",
    )
    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ui_refinement",
        selection_dir=selection_dir,
        design_ids=[str(variant.get("source_design_id")) for variant in variants if variant.get("source_design_id")],
        name_suffix=name_suffix or "mutation_seeded_refinement",
        param_overrides={
            **dict(param_overrides or {}),
            "manual_mutation_mode": "seeded_refinement",
            "manual_mutation_method": "explicit_substitutions",
            "manual_mutation_fixed_positions_json": str(fixed_json_path),
        },
        selected_designs=designs,
    )
    return launch_request, len(variants), ""


def _generate_cdr_indel_variants(
    base_sequence: str,
    base_name: str,
    regions: List[tuple[int, int]],
    config: AntibodyCdrIndelConfig,
) -> List[Dict[str, Any]]:
    if not base_sequence:
        raise HTTPException(status_code=422, detail=f"Selected design '{base_name}' is missing a binder sequence.")
    if not regions:
        raise HTTPException(status_code=422, detail=f"Could not map selected CDR loops onto '{base_name}'.")
    if not config.allow_insertions and not config.allow_deletions:
        raise HTTPException(status_code=422, detail="Enable insertions, deletions, or both for a CDR indel round.")

    indel_sizes = sorted({size for size in config.indel_sizes if isinstance(size, int) and size > 0})
    if not indel_sizes:
        raise HTTPException(status_code=422, detail="At least one positive indel size is required.")

    position_pool = sorted({pos for start, end in regions for pos in range(start, end + 1)})
    if not position_pool:
        raise HTTPException(status_code=422, detail=f"No mutable positions were found in the requested CDR loops for '{base_name}'.")

    aa_pool = _normalize_aa_pool(config.allowed_aas, config.blocked_aas)
    variants: List[Dict[str, Any]] = []
    seen_sequences = {base_sequence}

    max_attempts = max(config.variants_per_design * 20, 50)
    attempts = 0
    while len(variants) < config.variants_per_design and attempts < max_attempts:
        attempts += 1
        seq_chars = list(base_sequence)
        mutation_meta: Dict[str, Any] | None = None
        choose_indel = random.random() <= config.indel_probability
        if not choose_indel:
            continue

        allowed_ops: List[str] = []
        if config.allow_insertions:
            allowed_ops.append("insertion")
        if config.allow_deletions:
            allowed_ops.append("deletion")
        op = random.choice(allowed_ops)
        size = random.choice(indel_sizes)

        if op == "insertion":
            pos = random.choice(position_pool)
            inserted = "".join(random.choice(aa_pool) for _ in range(size))
            seq_chars[pos:pos] = list(inserted)
            mutation_meta = {
                "type": "insertion",
                "position": pos,
                "from": "",
                "to": inserted,
                "summary": f"ins{pos}{inserted}",
            }
        else:
            delete_candidates = [
                pos
                for pos in position_pool
                if any(start <= pos and (pos + size - 1) <= end for start, end in regions)
                and (pos + size - 1) <= len(base_sequence)
            ]
            if not delete_candidates:
                continue
            pos = random.choice(delete_candidates)
            deleted = "".join(seq_chars[pos - 1:pos - 1 + size])
            del seq_chars[pos - 1:pos - 1 + size]
            mutation_meta = {
                "type": "deletion",
                "position": pos,
                "from": deleted,
                "to": "",
                "summary": f"del{pos}-{pos + size - 1}" if size > 1 else f"del{pos}{deleted}",
            }

        variant_sequence = "".join(seq_chars)
        if not variant_sequence or variant_sequence in seen_sequences:
            continue

        seen_sequences.add(variant_sequence)
        variants.append(
            {
                "name": f"{base_name}_{mutation_meta['summary']}_{len(variants) + 1}",
                "sequence": variant_sequence,
                "mutation": mutation_meta,
            }
        )

    if not variants:
        raise HTTPException(
            status_code=422,
            detail=f"Could not generate unique CDR indel variants for '{base_name}' with the requested settings.",
        )

    return variants


def _build_cdr_indel_iteration_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: AntibodyCdrIndelConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
    *,
    seed_refinement_trigger: Optional[Dict[str, Any]] = None,
    iteration_action: str = "cdr_indel_round",
) -> tuple[JobCreate, int, str]:
    loop_ids = [loop_id.strip().upper() for loop_id in config.loop_ids if isinstance(loop_id, str) and loop_id.strip()]
    if not loop_ids:
        raise HTTPException(status_code=422, detail="Select at least one CDR loop for the indel round.")

    predictor = str(config.predictor or "protenix").strip().lower()
    if predictor not in {"protenix", "boltz2"}:
        raise HTTPException(status_code=422, detail="CDR indel rounds currently support predictor='protenix' or 'boltz2'.")

    msa_provider = str(config.msa_provider or "local").strip().lower()
    if msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(status_code=422, detail="msa_provider must be 'local' or 'colabfold_api'.")

    region_map = _resolve_loop_region_map(root_job)
    variants: List[Dict[str, Any]] = []

    for design in designs:
        design_path = _resolve_design_structure_path(design.pdb_path)
        chain_records = _extract_chain_records_from_structure(design_path)
        if not chain_records:
            raise HTTPException(status_code=422, detail=f"Could not extract chain sequences from '{design.name}'.")

        available_chain_ids = list(chain_records.keys())
        binder_chain_id = _resolve_loop_target_chain(root_job, loop_ids, available_chain_ids)
        binder_records = chain_records.get(binder_chain_id) or []
        fallback_regions = _build_mutation_regions(binder_records, region_map, loop_ids)
        detected_regions = _detect_loop_sequence_regions(design_path, binder_chain_id, loop_ids)
        mutation_regions = [
            detected_regions.get(loop_id) or fallback_regions.get(loop_id)
            for loop_id in loop_ids
            if detected_regions.get(loop_id) or fallback_regions.get(loop_id)
        ]
        if not mutation_regions:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Could not resolve CDR regions for '{design.name}'. "
                    "Provide manual CDR residue definitions or use a structure with detectable antibody numbering."
                ),
            )
        base_sequence = "".join(record["aa"] for record in binder_records)

        design_variants = _generate_cdr_indel_variants(
            base_sequence=base_sequence,
            base_name=design.name,
            regions=mutation_regions,
            config=config,
        )

        for variant in design_variants:
            complex_components = []
            for chain_id, records in chain_records.items():
                sequence = variant["sequence"] if chain_id == binder_chain_id else "".join(record["aa"] for record in records)
                complex_components.append({
                    "type": "protein",
                    "id": chain_id,
                    "sequence": sequence,
                })
            variants.append(
                {
                    "name": variant["name"],
                    "sequence": variant["sequence"],
                    "complex_components": complex_components,
                    "source_design_id": design.id,
                    "source_design_name": design.name,
                    "binder_chain_id": binder_chain_id,
                    "loop_ids": loop_ids,
                    "mutation": variant["mutation"],
                }
            )

    if not variants:
        raise HTTPException(status_code=422, detail="No CDR indel variants were generated from the selected designs.")

    from services.msa_policy import resolve_search_backend
    try:
        effective_msa_provider = resolve_search_backend(msa_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    base_params = _prune_iteration_params(root_job.params if isinstance(root_job.params, dict) else {})
    launch_params: Dict[str, Any] = {
        "pred_method": "protenix" if predictor == "protenix" else "boltz",
        "mutagenesis_variants": variants,
        "msa_force_refresh": True,
        "msa_provider": effective_msa_provider,
        "pinned_gpus": base_params.get("pinned_gpus"),
        "lock_gpus": base_params.get("lock_gpus"),
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": root_job.id,
        "iteration_source_design_ids": [design.id for design in designs],
        "iteration_action": iteration_action,
        "cdr_indel_config": config.model_dump(),
        "run_frustrampnn": False,
    }
    if seed_refinement_trigger:
        launch_params["mutation_seed_refinement_trigger"] = seed_refinement_trigger

    if predictor == "protenix":
        for key in (
            "protenix_model_weights",
            "protenix_seeds",
            "protenix_n_sample",
            "protenix_n_step",
            "protenix_n_cycle",
            "protenix_use_msa",
            "protenix_use_template",
            "protenix_enable_cache",
            "protenix_enable_fusion",
            "protenix_msa_backend",
            "protenix_auto_oom_retry",
            "protenix_oom_retry_attempts",
            "msa_preset",
            "msa_use_gpu",
            "msa_local_db",
            "msa_cache_dir",
            "msa_threads",
            "colabfold_api_host",
            "colabfold_api_min_interval",
            "colabfold_api_poll_interval",
            "msa_gpu_mode",
            "msa_gpu_threshold",
            "msa_preferred_gpus",
            "msa_excluded_gpus",
            "msa_gpu_server_mode",
            "msa_gpu_server_wait_timeout",
            "msa_gpu_server_db_load_mode",
            "msa_gpu_server_startup_wait",
        ):
            if key in base_params:
                launch_params[key] = base_params[key]
    else:
        _copy_present_params(base_params, launch_params, BOLTZ_ITERATION_FORWARD_KEYS)

    if param_overrides:
        cleaned_overrides = dict(param_overrides)
        for key in ("epitope_residues", "target_pdb"):
            value = cleaned_overrides.get(key)
            if value is None or (isinstance(value, str) and (not value.strip() or value.strip() == "refinement_mode")):
                cleaned_overrides.pop(key, None)
        if not cleaned_overrides.get("selected_residues"):
            cleaned_overrides.pop("selected_residues", None)
        launch_params.update(cleaned_overrides)

    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else "cdr_indel_round"
    model_id = "protenix" if predictor == "protenix" else "boltz2"
    launch_request = JobCreate(
        name=f"{root_job.name}_{suffix}",
        model_id=model_id,
        mode="complex",
        params=launch_params,
        execution_target_id=root_job.execution_target_id,
        execution_policy=ExecutionPolicy.from_params(root_job.params),
        pinned_gpu=None,
    )
    message_note = ""
    if msa_provider == "colabfold_api" and effective_msa_provider == "local":
        message_note = " ColabFold API was downgraded to local MSA because the indel round generated multiple variants."
    return launch_request, len(variants), message_note


def _build_cdr_indel_seeded_refinement_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: AntibodyCdrIndelConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    trigger_payload = {
        "source_job_id": source_job.id,
        "root_job_id": root_job.id,
        "name_suffix": name_suffix or "mutation_seeded_refinement",
        "param_overrides": dict(param_overrides or {}),
        "manual_mutation_mode": "seeded_refinement",
        "manual_mutation_method": "cdr_indels",
    }
    return _build_cdr_indel_iteration_job(
        root_job=root_job,
        source_job=source_job,
        designs=designs,
        config=config,
        name_suffix=name_suffix or "mutation_seed_builder",
        param_overrides=param_overrides,
        seed_refinement_trigger=trigger_payload,
        iteration_action="mutation_seed_build",
    )


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


@router.get("", response_model=JobList | JobSummaryList | BinderNavigationJobList)
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


@router.post("/imports/proteinbase", response_model=JobResponse, status_code=201)
async def import_proteinbase_bundle_job(
    request: ProteinBaseBundleImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Import an uploaded ProteinBase CSV or JSONL bundle as a completed job."""
    try:
        resolved_bundle_path = resolve_allowed_path(request.bundle_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied to this import bundle path") from exc

    if not resolved_bundle_path.exists():
        raise HTTPException(status_code=404, detail="Import bundle not found")
    if not resolved_bundle_path.is_file():
        raise HTTPException(status_code=400, detail="Import bundle path must point to a file")

    try:
        job = await import_proteinbase_bundle(
            session=session,
            bundle_path=resolved_bundle_path,
            dataset_name=request.dataset_name,
            job_name=request.job_name,
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle must be UTF-8 text: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle is invalid: {exc}") from exc

    await session.refresh(job)

    design_count = (
        await session.execute(select(func.count(Design.id)).where(Design.job_id == job.id))
    ).scalar_one()

    try:
        from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

        schedule_viewer_minimum_analyses_for_job(str(job.id))
    except Exception:
        pass

    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=_public_job_params(job),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=_public_job_output_dir(job),
        error_message=job.error_message,
        design_count=design_count or 0,
        requested_design_count=_resolve_requested_design_count(job),
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
        source_selection_manifest_path=job.source_selection_manifest_path,
        source_selection_count=job.source_selection_count,
        selected_input_artifact_class=job.selected_input_artifact_class,
        selected_input_schema_version=job.selected_input_schema_version,
        selection_source_type=job.selection_source_type,
        selection_source_job_id=job.selection_source_job_id,
        selection_dataset_name=job.selection_dataset_name,
        selected_loop_scope=job.selected_loop_scope,
        provenance=job.provenance,
        saved_selection_sets=_serialized_saved_review_filter_sets(job),
        pinned_gpu=job.pinned_gpu,
        execution_target_id=job.execution_target_id,
        execution_source_revision=job.execution_source_revision,
        execution_source_tree=job.execution_source_tree,
        execution_bundle_sha256=job.execution_bundle_sha256,
        remote_attempt_id=job.remote_attempt_id,
        remote_state=job.remote_state,
        execution_stages=project_execution_stages(job),
        current_stage=job.current_stage,
        completed_stages=job.completed_stages,
        stage_outputs=job.stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
    )


@router.post('/execution-plan/preview', response_model=JobExecutionPlanPreview)
async def preview_job_execution_plan(
    job_data: JobCreate, session: AsyncSession = Depends(get_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
):
    """Browser and agent use the exact same typed, nonexecuting preview."""
    return await preview_submission(job_data, session, experiment_session)


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


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _preallocated_job_id: Any = Depends(lambda: None),
    _commit: Any = Depends(lambda: True),
    _skip_parent_lineage_update: Any = Depends(lambda: False),
    _md_output_creation: Any = Depends(lambda: None),
    _md_input_resolver: Any = Depends(lambda: None),
    _typed_md_project_launch: Any = Depends(lambda: None),
    experiment_session: AsyncSession = Depends(get_experiment_session),
    _approved_execution_plan: Any = Depends(lambda: None),
) -> JobResponse:
    """Canonical Job submission, optionally bound by one opaque launch context."""
    return await submit_job(
        job_data,
        background_tasks,
        session,
        _preallocated_job_id=_preallocated_job_id,
        _commit=_commit,
        _md_output_creation=_md_output_creation,
        _md_input_resolver=_md_input_resolver,
        _typed_md_project_launch=(
            None
            if isinstance(_typed_md_project_launch, DependsParam)
            else _typed_md_project_launch
        ),
        experiment_session=experiment_session,
        _approved_execution_plan=_approved_execution_plan,
    )


@router.post("/antibody-iteration/from-designs", response_model=AntibodyIterationLaunchResponse, status_code=201)
async def launch_antibody_iteration_from_designs(
    request: AntibodyIterationLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Launch a focused antibody iteration round from selected design structures."""
    source_job, root_job = await _resolve_antibody_root_job(session, request.source_job_id)
    saved_filter_set = _resolve_saved_review_filter_set(
        request.review_filter_set_id,
        [source_job, root_job],
    )
    design_ids = _resolve_launch_design_ids(request.design_ids, saved_filter_set)
    if not design_ids:
        raise HTTPException(
            status_code=422,
            detail="Select at least one design or load a saved review dataset before launching a new round.",
        )

    result = await session.execute(select(Design).where(Design.id.in_(design_ids)))
    found_designs = result.scalars().all()
    design_by_id = {design.id: design for design in found_designs}
    missing_designs = [design_id for design_id in design_ids if design_id not in design_by_id]
    if missing_designs:
        raise HTTPException(
            status_code=404,
            detail=f"Some selected designs were not found: {', '.join(missing_designs[:10])}",
        )

    action = request.action.strip().lower()
    ordered_designs = [design_by_id[design_id] for design_id in design_ids]
    await _validate_selected_design_owners(session, source_job, root_job, ordered_designs)
    if action == "frustrampnn":
        if (
            request.param_overrides
            or request.cdr_indel_config is not None
            or request.manual_mutagenesis_config is not None
            or request.name_suffix is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="FrustraMPNN runtime, path, GPU, and naming overrides are server-owned.",
            )
        from services.frustrampnn.jobs import (
            FrustraMPNNChildError,
            design_selections as resolve_frustrampnn_selections,
        )
        from services.structure_dataset_fanout import StructureDatasetFanoutError
        from routers.frustrampnn import _fanout_design_selections

        try:
            selections = await resolve_frustrampnn_selections(
                session,
                source_parent=source_job,
                design_ids=design_ids,
            )
            requested_settings = (
                request.frustrampnn_settings or default_frustrampnn_settings()
            )
            fanout = await _fanout_design_selections(
                session,
                parent=source_job,
                selections=selections,
                trigger="antibody_iteration",
                requested_settings=requested_settings,
            )
        except (FrustraMPNNChildError, StructureDatasetFanoutError) as exc:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        launched_jobs = [await get_job(str(child.id), session) for child in fanout.child_jobs]
        launched_job = launched_jobs[0]
        return AntibodyIterationLaunchResponse(
            message=(
                f"Queued {len(launched_jobs)} FrustraMPNN scheduler jobs for "
                f"{len(ordered_designs)} selected designs."
            ),
            action=action,
            source_job_id=source_job.id,
            root_job_id=root_job.id,
            selection_dir=str(fanout.child_jobs[0].output_dir),
            selected_design_count=len(ordered_designs),
            launched_job=launched_job,
            launched_jobs=launched_jobs,
            fanout_id=fanout.fanout_id,
        )

    selection_dir = _materialize_antibody_selection(root_job, source_job, ordered_designs, request.action)
    variant_note = ""
    variant_count = len(ordered_designs)
    if action == "cdr_indel_round":
        if request.cdr_indel_config is None:
            raise HTTPException(status_code=422, detail="cdr_indel_config is required for action 'cdr_indel_round'.")
        launch_request, variant_count, variant_note = _build_cdr_indel_iteration_job(
            root_job=root_job,
            source_job=source_job,
            designs=ordered_designs,
            config=request.cdr_indel_config,
            name_suffix=request.name_suffix,
            param_overrides=request.param_overrides,
        )
    elif action == "mutation_seeded_refinement":
        if request.manual_mutagenesis_config is not None:
            launch_request, variant_count, variant_note = _build_manual_mutagenesis_seeded_refinement_job(
                root_job=root_job,
                source_job=source_job,
                designs=ordered_designs,
                config=request.manual_mutagenesis_config,
                name_suffix=request.name_suffix,
                param_overrides=request.param_overrides,
            )
        elif request.cdr_indel_config is not None:
            launch_request, variant_count, variant_note = _build_cdr_indel_seeded_refinement_job(
                root_job=root_job,
                source_job=source_job,
                designs=ordered_designs,
                config=request.cdr_indel_config,
                name_suffix=request.name_suffix,
                param_overrides=request.param_overrides,
            )
        else:
            raise HTTPException(
                status_code=422,
                detail="mutation_seeded_refinement requires manual_mutagenesis_config or cdr_indel_config.",
            )
    else:
        launch_request = _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action=action,
            selection_dir=selection_dir,
            design_ids=design_ids,
            name_suffix=request.name_suffix,
            param_overrides=request.param_overrides,
            saved_filter_set=saved_filter_set,
            selected_designs=ordered_designs,
        )
    if isinstance(launch_request.params, dict):
        source_stage_payload = _derive_source_stage_payload(source_job, ordered_designs, selection_dir)
        launch_request.params.update({
            "lineage_root_job_id": root_job.id,
            "stage_family": launch_request.params.get("stage_family"),
            "stage_mode": launch_request.params.get("stage_mode"),
            "selection_source_type": "saved_dataset" if saved_filter_set is not None else "selected_designs",
            "selection_source_job_id": source_job.id,
            "selection_dataset_name": saved_filter_set.name if saved_filter_set is not None else None,
            "selected_loop_scope": _build_selected_loop_scope(launch_request.params),
            **source_stage_payload,
        })
    launch_selection_dir = str(launch_request.params.get("iteration_selection_dir") or selection_dir)
    _require_prepared_remote_review(launch_request, {
        'message': f"Launched antibody iteration action '{action}' from {len(ordered_designs)} selected designs.",
        'action': action, 'source_job_id': source_job.id, 'root_job_id': root_job.id,
        'selection_dir': launch_selection_dir, 'selected_design_count': len(ordered_designs),
    })
    launched_job = await submit_job(launch_request, background_tasks, session)
    selection_source_note = (
        f" using saved dataset '{saved_filter_set.name}'"
        if saved_filter_set is not None and not _normalize_design_ids(request.design_ids)
        else ""
    )

    return AntibodyIterationLaunchResponse(
        message=(
            f"Launched antibody iteration action '{action}' from {len(ordered_designs)} selected designs{selection_source_note}."
            + (f" Generated {variant_count} indel variants." if action == "cdr_indel_round" else "")
            + variant_note
        ),
        action=action,
        source_job_id=source_job.id,
        root_job_id=root_job.id,
        selection_dir=launch_selection_dir,
        selected_design_count=len(ordered_designs),
        launched_job=launched_job,
    )


@router.post("/mutagenesis/from-designs", response_model=ManualMutagenesisLaunchResponse, status_code=201)
async def launch_manual_mutagenesis_from_designs(
    request: ManualMutagenesisLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Launch an explicit manual mutation batch from selected structure designs."""
    source_job = await session.get(Job, request.source_job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail="Source job not found.")

    root_job: Optional[Job] = None
    try:
        _, root_job = await _resolve_antibody_root_job(session, request.source_job_id)
    except HTTPException:
        root_job = None

    saved_filter_set = _resolve_saved_review_filter_set(
        request.review_filter_set_id,
        [source_job, root_job],
    )
    design_ids = _resolve_launch_design_ids(request.design_ids, saved_filter_set)
    if not design_ids:
        raise HTTPException(
            status_code=422,
            detail="Select at least one design or load a saved review dataset before launching a new round.",
        )

    result = await session.execute(select(Design).where(Design.id.in_(design_ids)))
    found_designs = result.scalars().all()
    design_by_id = {design.id: design for design in found_designs}
    missing_designs = [design_id for design_id in design_ids if design_id not in design_by_id]
    if missing_designs:
        raise HTTPException(
            status_code=404,
            detail=f"Some selected designs were not found: {', '.join(missing_designs[:10])}",
        )

    ordered_designs = [design_by_id[design_id] for design_id in design_ids]
    await _validate_selected_design_owners(session, source_job, root_job, ordered_designs)
    launch_request, variant_count, variant_note = _build_manual_mutagenesis_iteration_job(
        source_job=source_job,
        designs=ordered_designs,
        config=request.config,
        name_suffix=request.name_suffix,
        param_overrides=request.param_overrides,
    )
    _require_prepared_remote_review(launch_request, {
        'message': f'Launched manual mutagenesis from {len(ordered_designs)} selected designs. Generated {variant_count} explicit variants.',
        'source_job_id': source_job.id, 'selected_design_count': len(ordered_designs),
        'variant_count': variant_count,
    })
    launched_job = await submit_job(launch_request, background_tasks, session)
    selection_source_note = (
        f" using saved dataset '{saved_filter_set.name}'"
        if saved_filter_set is not None and not _normalize_design_ids(request.design_ids)
        else ""
    )

    return ManualMutagenesisLaunchResponse(
        message=(
            f"Launched manual mutagenesis from {len(ordered_designs)} selected designs{selection_source_note}."
            f" Generated {variant_count} explicit variants."
            + variant_note
        ),
        source_job_id=source_job.id,
        selected_design_count=len(ordered_designs),
        variant_count=variant_count,
        launched_job=launched_job,
    )


def _structure_stem(source) -> str:
    """File name without any structure extension (.cif.gz, .pdb, ...).

    FAMPNN strips .pdb/.cif from its fixed-position row names but not from the staged
    file stem, so a name like design.cif.pdb would miss its own row.
    """
    name = Path(str(source)).name
    while True:
        base, ext = os.path.splitext(name)
        if ext.lower() not in {".gz", ".cif", ".mmcif", ".pdb", ".ent", ".bcif"} or not base:
            return name
        name = base


async def _prepare_resequence_job(request: ResequenceLaunchRequest, session: AsyncSession) -> JobCreate:
    """Acquire exact cold-portable sources and only authored scientific settings."""
    from services.binder_source_materialization import (prepare_resequence_sources,
        prepare_resequence_existing_basket, begin_resequence_preparation, resequence_submission_guard)
    from routers.files import get_governed_ngs_result_roots
    from services.resequence_settings import designer_params, validator_params
    from services.resequence_designers import build_template

    selection_dir = _create_generic_selection_dir("protein_local_redesign", "resequence")
    template_path = selection_dir.with_name(selection_dir.name + "_designer.json")
    try:
        async with resequence_submission_guard(request.model_dump(mode="json"), session):
            roots = await get_governed_ngs_result_roots(session)
            if request.sources:
                roster = await prepare_resequence_sources(request.sources, session, selection_dir, roots)
            elif request.backbone_input_pdbs:
                roster = await prepare_resequence_existing_basket(request.backbone_input_pdbs,
                    request.retained_params, session, selection_dir, roots)
            else:
                raise ValueError("Resequence requires structures or a retained basket")
            if not roster:
                raise ValueError("Resequence requires at least one structure")
            params: Dict[str, Any] = dict(backbone_input_pdbs=str(selection_dir), resequence_sources=roster,
                design_chains=request.design_chains, seq_method=request.seq_method,
                structure_validators=request.structure_validators, protenix_msa_backend=request.msa_provider)
            if request.fixed_positions.strip():
                params["fixed_positions"] = request.fixed_positions.strip()
            if request.designable_positions.strip():
                params["designable_positions"] = request.designable_positions.strip()
            if request.seq_method in ("ligandmpnn", "caliby", "protonpottsmpnn"):
                retained = request.retained_params
                retained_template = retained.get("designer_template")
                same_designer = retained.get("seq_method") == request.seq_method
                retained_bytes = None
                retained_mode = retained.get("designer_mode")
                if "designer_settings" not in request.model_fields_set and retained_template and same_designer:
                    retained_bytes = resolve_allowed_path(
                        to_allowed_relative(Path(retained_template)) if Path(retained_template).is_absolute()
                        else retained_template).read_bytes()
                    native_template = json.loads(retained_bytes)
                    retained_mode = retained_mode or native_template.get("mode") or (
                        "ensemble_design" if request.seq_method == "caliby" else None)
                same_mode = (not request.designer_mode or retained_mode == request.designer_mode
                             or request.seq_method == "protonpottsmpnn")
                if retained_bytes is not None and same_mode:
                    template_path.write_bytes(retained_bytes)
                else:
                    template = build_template(request.seq_method, mode=request.designer_mode,
                        design_chains=request.design_chains, settings=request.designer_settings)
                    template_path.write_text(json.dumps(template, sort_keys=True))
                params["designer_template"] = str(template_path)
                if request.designer_mode:
                    params["designer_mode"] = request.designer_mode
            else:
                params.update(designer_params(request.seq_method, request.designer_settings))
            params.update(validator_params(request.structure_validators, request.validator_settings))
            params["resequence_preparation"] = begin_resequence_preparation(selection_dir)
            return JobCreate(name=request.name, model_id="protein_modification_experimental",
                mode="region_redesign", params=params, execution_target_id=request.execution_target_id,
                execution_policy=request.execution_policy, launch_context_id=request.launch_context_id)
    except BaseException as exc:
        shutil.rmtree(selection_dir, ignore_errors=True)
        template_path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, OSError, KeyError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise


@router.post("/resequence/from-structures/cleanup")
async def cleanup_resequence_from_structures(request: Dict[str, Any], session: AsyncSession = Depends(get_session)):
    from services.binder_source_materialization import cleanup_resequence_preparation
    try:
        return {"cleaned": await cleanup_resequence_preparation(request["preparation"], session)}
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/resequence/from-structures", response_model=JobResponse, status_code=201)
async def launch_resequence_from_structures(
    request: ResequenceLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Resequence and validate exact structures from any generator (RFD4, RFD3, DISCO, ...) on this system.

    Sources are the same exact-structure references the continuation links use, so the source
    job's model does not matter. Fixed or designable positions are caller-selected; with neither
    given every residue of the design chains is designable.
    """
    return await submit_job(await _prepare_resequence_job(request, session), background_tasks, session)


@router.post("/resequence/from-structures/prepare")
async def prepare_resequence_from_structures(
    request: ResequenceLaunchRequest,
    session: AsyncSession = Depends(get_session),
):
    """The same staged request as the launch above, returned for the launcher to place and submit.

    A worker launch needs the reviewed plan to cover the exact staged structures, so staging happens first and the
    launcher then submits this body with the chosen execution target through the normal plan-approval flow.
    """
    return (await _prepare_resequence_job(request, session)).model_dump(mode="json", exclude_unset=True)


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


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a job and any active descendant jobs it spawned."""
    await reject_generic_md_lifecycle_control(job_id, session)
    job, lineage = await cancel_job_lineage(job_id, session)
    return {
        "message": "Job cancelled",
        "job_id": job_id,
        "jobs_cancelled": len(lineage),
        "root_job_name": job.name,
    }


@router.delete("/{job_id}/permanent")
async def delete_job_permanently(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Permanently delete a job and ALL its data.
    
    DEBUG FEATURE - Removes:
    - Job from database
    - All child jobs from database
    - All designs from database  
    - Output directory (bms_results/...)
    - Work directory (work/...)
    
    This is irreversible!
    """
    await reject_generic_md_lifecycle_control(job_id, session)
    import shutil
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel any active lineage members before removing their DB rows/files.
    deletion_lineage = [job]
    try:
        _, deletion_lineage = await cancel_job_lineage(job_id, session, error_message="Deleted by user")
    except HTTPException as exc:
        if exc.status_code not in {400, 404}:
            raise
    
    job_name = job.name
    output_dir = job.output_dir
    
    # Delete designs associated with this job
    from database import Design
    await session.execute(
        Design.__table__.delete().where(Design.job_id == job_id)
    )
    
    # Delete child jobs first
    child_jobs = [member for member in deletion_lineage if member.id != job_id]
    from services.job_control import retain_permanent_delete_cleanup
    retained_ids = set()
    for member in deletion_lineage:
        if await retain_permanent_delete_cleanup(session, member):
            retained_ids.add(member.id)

    child_output_dirs = []
    for child in child_jobs:
        if child.output_dir:
            child_output_dirs.append(child.output_dir)

    delete_job_ids = {job.id, *(str(child.id) for child in child_jobs)} - retained_ids
    remaining_output_refs = await _load_remaining_output_dir_refs(session, delete_job_ids)
    output_dirs_to_delete, preserved_output_dirs = _plan_output_dir_cleanup(
        [output_dir, *child_output_dirs],
        remaining_output_refs,
    )

    for preserved in preserved_output_dirs:
        logger.warning(
            "[DELETE] Preserving shared output dir %s because it is still referenced by %s",
            preserved["path"],
            ", ".join(
                f"{ref['job_id']}:{ref['field']}"
                for ref in preserved.get("referenced_by", [])
            ) or "unknown jobs",
        )

    for child in child_jobs:
        # Delete child's designs
        await session.execute(
            Design.__table__.delete().where(Design.job_id == child.id)
        )
        if child.id not in retained_ids:
            await session.delete(child)

    # Retained hidden rows remain the existing stop/claim cleanup owner.
    if job.id not in retained_ids:
        await session.delete(job)
    await session.commit()
    
    # Delete output directories
    deleted_paths = []
    for raw_path in output_dirs_to_delete:
        output_path = resolve_output_dir(raw_path)
        if output_path and output_path.exists():
            try:
                shutil.rmtree(output_path)
                deleted_paths.append(str(output_path))
            except Exception as e:
                print(f"Warning: Failed to delete output dir {output_path}: {e}")
    
    return {
        "message": f"Job '{job_name}' permanently deleted",
        "job_id": job_id,
        "children_deleted": len(child_jobs),
        "directories_deleted": deleted_paths,
        "directories_preserved": preserved_output_dirs,
    }


from services.core_protein_execution_settings import ExecutionSettings


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


@router.post("/{job_id}/resubmit")
async def resubmit_job(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    placement: ResubmitJobRequest | None = Body(default=None),
):

    """
    Resubmit a failed or cancelled job with the same parameters.
    Creates a new job with a fresh ID but copies all settings from the original.
    """
    _raise_if_workflow_launches_disabled("resubmit workflow jobs")
    await reject_generic_md_lifecycle_control(job_id, session)
    # Find original job
    result = await session.execute(select(Job).where(Job.id == job_id))
    original_job = result.scalar_one_or_none()
    
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    if original_job.model_id == "nanopore":
        if not alignment_access.request_is_authorized(request, original_job.id, original_job.provenance):
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
    
    # Build resubmit name
    resubmit_suffix = "_resubmit"
    base_name = original_job.name
    # Handle multiple resubmits by not doubling suffix
    if base_name.endswith(resubmit_suffix):
        new_name = base_name
    else:
        new_name = f"{base_name}{resubmit_suffix}"
    
    # Create new job with same params - GPU orchestrator will pick it up
    import uuid
    import os
    from pathlib import Path
    
    # Create new output directory for resubmitted job
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = str(get_results_dir() / f"{new_name}_{timestamp}")
    if original_job.model_id in {'family_msa_protein', 'family_msa_nucleic'}:
        output_dir = str(get_results_dir() / f"{new_name}_{timestamp}_{uuid.uuid4().hex}")

    resubmit_params = deepcopy(original_job.params) if isinstance(original_job.params, dict) else {}
    from scripts.lib.protein_cad_settings import replay_cad_settings
    resubmit_params = replay_cad_settings(original_job.model_id, original_job.mode,
                                          resubmit_params, resubmit_params)
    if original_job.model_id == "nanopore":
        resubmit_params = ont_submission_trust.fresh_computation_params(resubmit_params)
        resubmit_params = ont_ngs_contract.replay_expected_plasmid_size(
            resubmit_params, original_job.provenance, mode=original_job.mode,
        )
    resubmit_params.pop("remote_result_policy", None)
    from services.msa_policy import apply_msa_policy
    try:
        resubmit_params = apply_msa_policy(original_job.model_id, resubmit_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resubmit_params = _normalize_nanopore_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_antibody_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_structure_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_structure_geometry_params(resubmit_params)
    resubmit_params = _normalize_antibody_job_params(resubmit_params)
    resubmit_workflow_id = str(
        resubmit_params.get("ont_workflow_id")
        or resubmit_params.get("ont_request_workflow_id")
        or resubmit_params.get("workflow_id")
        or ""
    ).strip()
    resubmit_input_mode = str(
        resubmit_params.get("ont_input_mode") or resubmit_params.get("input_mode") or ""
    ).strip()
    if (
        original_job.model_id == "nanopore"
        and resubmit_workflow_id == "ont_fastq_qc"
        and resubmit_input_mode == "fastq"
    ):
        resubmit_params = ont_ngs_contract.normalize_ont_launch_params(
            "ont_fastq_qc", resubmit_params
        )
    # MSA refresh/cache intent is scientific input; replay never rewrites it.

    _validate_protenix_template_requirements(original_job.model_id, resubmit_params)
    _validate_protenix_checkpoint_requirements(original_job.model_id, resubmit_params)
    _validate_fampnn_checkpoint_requirements(original_job.model_id, resubmit_params)
    _validate_antibody_runtime_paths(original_job.model_id, resubmit_params)

    from services.gpu_orchestrator import estimate_vram, estimate_protenix_tokens
    resubmit_sequence_length = original_job.sequence_length or 300
    if original_job.model_id == "protenix":
        resubmit_sequence_length = estimate_protenix_tokens(
            resubmit_params,
            resubmit_sequence_length,
        )
    resubmit_vram_estimate = estimate_vram(
        original_job.model_id,
        resubmit_sequence_length,
        resubmit_params,
    )
    if original_job.model_id in {'family_msa_protein', 'family_msa_nucleic'}:
        for key in ('gpu_id', 'pinned_gpu', 'pinned_gpus', 'cpus_per_gpu'):
            resubmit_params.pop(key, None)
    if original_job.model_id == "nanopore" and resubmit_workflow_id == "ont_fastq_qc" and resubmit_input_mode == "fastq":
        resubmit_vram_estimate = 0
        for key in ("gpu_id", "pinned_gpu", "pinned_gpus", "cpus_per_gpu"):
            resubmit_params.pop(key, None)
    resubmit_selected_input_artifact_class = normalize_antibody_artifact_class(
        resubmit_params.get("selected_input_artifact_class")
    )
    resubmit_selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        resubmit_params.get("selected_input_schema_version")
    )

    from services import core_protein_scientific_contract as scientific_contract
    try:
        source_revision = scientific_contract.revision_for_job(original_job)
        # Every fresh attempt uses current admission, including former children.

        resubmit_params.pop(scientific_contract.REVISION_KEY, None)
        resubmit_params.pop('fampnn_analysis_declaration', None)
        resubmit_params.pop('fampnn_analysis_policy', None)
        scientific_contract.reject_reserved_marker(resubmit_params)
        resubmit_revision = scientific_contract.admission_revision(
            original_job.model_id, original_job.mode,
        )

        if resubmit_revision is not None:
            errors = get_registry().validate_job_params(
                original_job.model_id, original_job.mode,
                _normalize_boltz_cp_params_for_validation(original_job.model_id, resubmit_params),
            )
            if errors:
                raise HTTPException(status_code=422, detail={"validation_errors": errors})
        resubmit_params, resubmit_provenance = scientific_contract.admitted_payload(
            resubmit_params, {}, resubmit_revision,
        )
        resubmit_provenance['parameter_diagnostics'] = get_registry().parameter_diagnostics(
            original_job.model_id, resubmit_params)
        original_round = (original_job.provenance or {}).get('binder_round_request')
        if original_round is not None:
            resubmit_provenance['binder_round_request'] = deepcopy(original_round)
        original_sequence = (original_job.provenance or {}).get('sequence_design_request')
        if original_sequence is not None:
            resubmit_provenance['sequence_design_request'] = deepcopy(original_sequence)
        original_request = (original_job.provenance or {}).get('core_protein_requested_params')
        if original_request is not None:
            resubmit_provenance['core_protein_requested_params'] = deepcopy(original_request)
        if resubmit_revision is not None:
            from services.fampnn_policy_admission import compile_declaration, overrides_from_declaration
            trusted_declaration = (original_job.provenance or {}).get('fampnn_analysis_declaration')
            declaration = compile_declaration(original_job.model_id, original_job.mode, resubmit_params,
                overrides_from_declaration(trusted_declaration),
                parent=await session.get(Job, original_job.parent_job_id) if original_job.parent_job_id else None)
            if declaration is not None:
                resubmit_params['fampnn_analysis_declaration'] = declaration
                resubmit_provenance['fampnn_analysis_declaration'] = declaration
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    resubmit_target = original_job.execution_target_id
    resubmit_source_revision = original_job.execution_source_revision
    resubmit_source_tree = original_job.execution_source_tree
    if original_job.model_id == "nanopore":
        resubmit_target = placement.execution_target_id if isinstance(placement, ResubmitJobRequest) else None
        resubmit_source_revision = resubmit_source_tree = None
        if resubmit_target:
            from services.remote_execution.targets import target_eligible
            target = await session.get(ExecutionTarget, resubmit_target, populate_existing=True)
            if target is None or not target_eligible(target):
                raise HTTPException(status_code=422, detail="execution_target_id is not an active ready execution target")
            from services.remote_execution.bundle import current_source_identity
            resubmit_source_revision, resubmit_source_tree = current_source_identity()

    new_job = Job(
        id=str(uuid.uuid4()),
        name=new_name,
        model_id=original_job.model_id,
        mode=original_job.mode,
        params={**resubmit_params, "remote_result_policy":
                "automatic" if (original_job.params or {}).get("remote_result_policy") == "automatic" else "manual"},
        provenance=resubmit_provenance,
        status=JobStatus.QUEUED.value,
        created_at=datetime.utcnow(),
        output_dir=output_dir,
        # Preserve batch info if any
        batch_id=original_job.batch_id,
        batch_name=original_job.batch_name,
        selected_input_artifact_class=resubmit_selected_input_artifact_class,
        selected_input_schema_version=resubmit_selected_input_schema_version,
        execution_target_id=resubmit_target,
        execution_source_revision=resubmit_source_revision,
        execution_source_tree=resubmit_source_tree,
        # GPU Orchestrator fields - let orchestrator pick it up
        queue_status='queued',
        vram_estimate_mb=resubmit_vram_estimate,
        sequence_length=resubmit_sequence_length,
        pinned_gpu=(None if original_job.model_id in {'family_msa_protein', 'family_msa_nucleic'}
                    else original_job.pinned_gpu),
        priority=0,
        paused=False,
        retry_count=0,
        max_retries=2,
    )

    if new_job.model_id in {'family_msa_protein', 'family_msa_nucleic'} and resubmit_params.get('managed_operation'):
        from services.family_msa.operations import ManagedOperation, bind_request
        from experiment_database import experiment_session_factory
        operation = ManagedOperation.model_validate(resubmit_params['managed_operation'])
        async with experiment_session_factory() as project_session:
            rebound = await bind_request(project_session, request=operation.request, project_id=operation.project_id,
                shared_states=operation.shared_states, job_id=new_job.id)
        new_job.params = {**new_job.params, 'managed_operation': rebound.model_dump(mode='json')}

    if new_job.model_id == "nanopore":
        _retain_ont_replay_lineage(original_job, new_job)
        output_dir = str(get_results_dir() / new_job.id)
        new_job.output_dir = output_dir
        if isinstance(placement, ResubmitJobRequest) and placement.execution_policy is not None:
            new_job.params = {**new_job.params, "remote_result_policy": placement.execution_policy.remote_result_policy}
        new_job.provenance = alignment_access.grant_alignment_access(
            new_job.id,
            new_job.provenance,
            response,
            request,
        )
    os.makedirs(output_dir, exist_ok=True)
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)
    
    return {
        "message": "Job resubmitted successfully",
        "original_job_id": job_id,
        "new_job_id": new_job.id,
        "new_job_name": new_job.name
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RE-INGESTION ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE CHECKPOINTING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
# CHILD JOB TRACKING (Spawn-Wait-Aggregate Pattern)
# ═══════════════════════════════════════════════════════════════════════════════

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


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: str,
    request_context: Request,
    response: Response,
    from_stage: str = None,
    request: Optional[ResumeJobRequest] = Body(default=None),
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Resume a failed job from a checkpoint.
    
    If from_stage is specified, it is recorded as a stage hint for cache-based
    resume behavior. The underlying Nextflow resume remains cache-driven.
    """
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
        if not alignment_access.request_is_authorized(request_context, job.id, job.provenance):
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
                from services.job_control import visible_job_clause
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
        from services.nextflow import _local_checkpoint_resume
        provenance = dict(job.provenance or {})
        pending = dict(checkpoint_id=request.checkpoint_id, checkpoint_sha256=request.checkpoint_sha256,
                       decision=request.checkpoint_decision, attempt_id=checkpoint.get("attempt_id"))
        job.provenance = {**provenance, "component_checkpoint_resume": pending}
        try:
            retained = _local_checkpoint_resume(job)
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
        from services.job_control import checkpoint_continuation_predicates
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

    # Determine work directory for resumption
    # We use the shared 'work' directory in project root by default
    # This allows Nextflow to find cached tasks from the previous run
    resume_work_dir = "work"
    
    # Create new job with resume info
    import uuid
    new_job_id = str(uuid.uuid4())
    base_name = job.name.replace("_resubmit", "").replace("_resumed", "")
    suffix = requested_name_suffix.strip() if requested_name_suffix else "_resumed"
    if not suffix.startswith("_"):
        suffix = f"_{suffix}"
    new_name = f"{base_name}{suffix}"
    
    # True resume should keep the original execution directory so Nextflow can
    # reuse cached task hashes that depend on params.out_dir/publishDir paths.
    output_dir = str(get_results_dir() / new_job_id) if fresh_execution else job.output_dir

    source_params = _normalize_structure_geometry_params(job.params or {})
    if _should_normalize_antibody_job_params(job.model_id, job.mode, source_params):
        source_params = _normalize_antibody_job_params(source_params)
    merged_params = {**source_params, **param_overrides}
    from scripts.lib.protein_cad_settings import replay_cad_settings
    merged_params = replay_cad_settings(job.model_id, job.mode, source_params, merged_params, overrides=param_overrides)
    # Use the existing prospective complex interpretation, as fresh admission does.
    # The source Job (linked by resume_job_id) retains the original selector token.
    if (
        str(job.mode or "").strip().lower() == "complex"
        and not _should_normalize_antibody_job_params(job.model_id, job.mode, merged_params)
        and str(merged_params.get("pred_method") or "").strip().lower() in {"both", "all", "boltz_protenix"}
    ):
        merged_params = _normalize_structure_prediction_pred_method(job.model_id, job.mode, merged_params)
    if job.model_id == "nanopore":
        merged_params = ont_ngs_contract.replay_expected_plasmid_size(
            merged_params, job.provenance, mode=job.mode,
        )
        if fresh_execution:
            merged_params = ont_submission_trust.fresh_computation_params(merged_params)
    from services.msa_policy import apply_msa_policy
    try:
        merged_params = apply_msa_policy(job.model_id, merged_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if fresh_execution:
        # Placement or source drift starts fresh, never reusing historical runtime/cache state.
        runtime_keys = {
            "batch_name", "job_name", "out_dir", "output_dir", "work_dir", "stage_work_dir",
            "cache_dir", "nxf_cache_dir", "NXF_CACHE_DIR", "lineage_root_job_id",
            "execution_target_id", "execution_source_revision", "execution_source_tree",
            "data_root", "code_root", "weights_root", "container_dir", "msa_cache_dir",
        }
        hardware_keys = {
            "pinned_gpus", "gpu_ids", "bcp_gpu_ids", "gpu_id", "gpu_device",
            "cuda_visible_devices", "CUDA_VISIBLE_DEVICES", "msa_preferred_gpu_ids",
            "msa_excluded_gpus",
        }
        merged_params = {
            key: value for key, value in merged_params.items()
            if not key.startswith("resume_") and key not in runtime_keys
            and (not target_changed or key not in hardware_keys or key in param_overrides)
        }
        merged_params["reorchestrated_from_job_id"] = job_id
    merged_params = _ensure_job_resume_identity(
        job_name=new_name if fresh_execution else (job.params or {}).get("job_name") or base_name,
        job_id=new_job_id if fresh_execution else _coerce_nonempty_text(merged_params.get("resume_root_job_id")) or job_id,
        model_id=job.model_id,
        mode=job.mode,
        params=merged_params,
    )
    resolved_child_batch_name = await _resolve_resume_child_batch_name(
        session,
        job,
        _coerce_nonempty_text(merged_params.get("resume_root_job_id")) or job_id,
    )
    if resolved_child_batch_name and not fresh_execution:
        merged_params["batch_name"] = resolved_child_batch_name
    if "remote_result_policy" in param_overrides or "execution_policy" in param_overrides:
        raise HTTPException(status_code=422, detail="execution policy is not a scientific override")
    merged_params.pop("remote_result_policy", None)
    merged_params = _normalize_antibody_runtime_paths(job.model_id, merged_params)
    merged_params = _normalize_structure_runtime_paths(job.model_id, merged_params)
    merged_params = _normalize_structure_geometry_params(merged_params)
    if _should_normalize_antibody_job_params(job.model_id, job.mode, merged_params):
        merged_params = _normalize_antibody_job_params(merged_params)
    if job.model_id == "boltz_cp_experimental":
        # An explicit workflow override must replace the parent's public value.
        for public_key, workflow_key in (("cp_topology", "bcp_cp_topology"), ("size_cp", "bcp_size_cp")):
            if public_key in param_overrides or workflow_key in param_overrides:
                merged_params[public_key] = param_overrides.get(public_key, param_overrides.get(workflow_key))
                merged_params.pop(workflow_key, None)
        merged_params["cp_topology"] = merged_params.get(
            "cp_topology", merged_params.get("bcp_cp_topology", "2d")
        )
        merged_params.pop("bcp_cp_topology", None)
    _validate_antibody_runtime_paths(job.model_id, merged_params)
    resume_selected_input_artifact_class = normalize_antibody_artifact_class(
        merged_params.get("selected_input_artifact_class")
    )
    resume_selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        merged_params.get("selected_input_schema_version")
    )

    try:
        # Only true cached resume stays in its original scientific cohort.
        # Overrides may not supply authority (including review-payload defaults).
        scientific_contract.reject_reserved_marker(param_overrides)
        source_revision = scientific_contract.revision_for_job(job)
        resume_revision = source_revision
        merged_params.pop(scientific_contract.REVISION_KEY, None)
        merged_params.pop('fampnn_analysis_declaration', None)
        merged_params.pop('fampnn_analysis_policy', None)
        scientific_contract.reject_reserved_marker(merged_params)
        if fresh_execution:
            # Match fresh resubmission: current admission, no silent downgrade,
            # and current registry validation rather than historical authority.
            resume_revision = scientific_contract.admission_revision(job.model_id, job.mode)

            if resume_revision is not None:
                errors = get_registry().validate_job_params(
                    job.model_id, job.mode,
                    _normalize_boltz_cp_params_for_validation(job.model_id, merged_params),
                )
                if errors:
                    raise HTTPException(status_code=422, detail={"validation_errors": errors})
        trusted_declaration = (job.provenance or {}).get('fampnn_analysis_declaration')
        merged_params, resume_provenance = scientific_contract.admitted_payload(
            merged_params, {}, resume_revision,
        )
        resume_provenance['parameter_diagnostics'] = get_registry().parameter_diagnostics(
            job.model_id, merged_params)
        original_request = (job.provenance or {}).get('core_protein_requested_params')
        if original_request is not None:
            resume_provenance['core_protein_requested_params'] = {
                **deepcopy(original_request), **deepcopy(param_overrides),
            }
        if fresh_execution and resume_revision is not None:
            from services.fampnn_policy_admission import compile_declaration, overrides_from_declaration
            declaration = compile_declaration(
                job.model_id, job.mode, merged_params,
                overrides_from_declaration(trusted_declaration),
                parent=await session.get(Job, job.parent_job_id) if job.parent_job_id else None,
            )
            if declaration is not None:
                merged_params['fampnn_analysis_declaration'] = declaration
                resume_provenance['fampnn_analysis_declaration'] = declaration
        elif not fresh_execution and trusted_declaration is not None:
            merged_params['fampnn_analysis_declaration'] = deepcopy(trusted_declaration)
            resume_provenance['fampnn_analysis_declaration'] = deepcopy(trusted_declaration)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    os.makedirs(output_dir, exist_ok=True)
    new_job = Job(
        id=new_job_id,
        name=new_name,
        status="queued",
        model_id=job.model_id,
        mode=job.mode,
        provenance=resume_provenance,
        params={
            **merged_params,
            "remote_result_policy": "automatic" if (job.params or {}).get("remote_result_policy") == "automatic" else "manual",
            **({} if fresh_execution else {
                "resume_job_id": job_id,
                "resume_work_dir": resume_work_dir,
                "resume_source_dir": job.output_dir,
                "resume_requested_stage": effective_from_stage,
            }),
        },
        output_dir=output_dir,
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        selected_input_artifact_class=resume_selected_input_artifact_class,
        selected_input_schema_version=resume_selected_input_schema_version,
        parent_job_id=job.parent_job_id,
        child_stage=job.child_stage,
        # Don't copy completed_stages/stage_outputs - they will be re-populated
        # as the resumed workflow re-emits cached results
        completed_stages=[], 
        stage_outputs={},
        
        # GPU Orchestrator fields - critical for scheduling
        queue_status='queued',
        vram_estimate_mb=job.vram_estimate_mb,
        sequence_length=job.sequence_length,
        pinned_gpu=None if target_changed else job.pinned_gpu,
        execution_target_id=execution_target_id,
        execution_source_revision=execution_source_revision,
        execution_source_tree=execution_source_tree,
        lineage_root_job_id=new_job_id if fresh_execution else job.lineage_root_job_id,
        priority=job.priority,
    )

    history = list(job.decision_history or [])
    if job.awaiting_input:
        history.append({
            "stage": job.awaiting_stage,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "new_job_id": new_job_id,
            "from_stage": effective_from_stage,
            "applied_overrides": sorted(param_overrides.keys()),
        })
        job.decision_history = history
    
    if new_job.model_id == "nanopore":
        _retain_ont_replay_lineage(job, new_job)
        new_job.provenance = alignment_access.grant_alignment_access(
            new_job.id,
            new_job.provenance,
            response,
            request_context,
        )
    session.add(new_job)
    await session.commit()
    
    logger.info(
        f"Job {job_id} resumed as {new_job_id} using work dir '{resume_work_dir}'"
        + (f" (requested_stage_hint={effective_from_stage})" if effective_from_stage else "")
    )
    
    return {
        "message": "Job re-orchestrated with current source and a fresh cache" if fresh_execution else f"Job resumed. Checking cache in '{resume_work_dir}'",
        "execution_target_id": execution_target_id,
        "placement_changed": target_changed,
        "source_changed": source_changed,
        "fresh_execution": fresh_execution,
        "original_job_id": job_id,
        "new_job_id": new_job_id,
        "new_job_name": new_name,
        "resume_from_stage": effective_from_stage or "auto",
        "resume_stage_mode": "fresh" if fresh_execution else "hint",
        "resume_stage_note": (
            "BMS source changed; prior task caches are not reused." if source_changed
            else "Execution target changed; prior task caches are not reused." if target_changed
            else "Stage selection is advisory; cache hits determine exact task reuse."
        ),
        "preserved_stages": [],
        "applied_overrides": sorted(param_overrides.keys())
    }


@router.post("/{job_id}/continue-protein-local-review")
async def continue_protein_local_review(
    job_id: str,
    request_context: Request,
    response: Response,
    request: ContinueProteinLocalReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Resume a paused protein-local-redesign job from a filtered subset."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _is_protein_local_redesign_job(job):
        raise HTTPException(status_code=422, detail="This continue endpoint only supports Protein Local Redesign jobs.")
    if not job.awaiting_input or not job.awaiting_stage:
        raise HTTPException(status_code=422, detail="Protein local redesign job is not currently paused for review.")

    selected_ids = [str(design_id).strip() for design_id in request.design_ids if str(design_id).strip()]
    if not selected_ids:
        raise HTTPException(status_code=422, detail="Select at least one design before continuing the workflow.")

    design_result = await session.execute(
        select(Design)
        .where(
            Design.job_id == job_id,
            Design.id.in_(selected_ids),
        )
        .order_by(Design.created_at.asc(), Design.name.asc())
    )
    selected_designs = design_result.scalars().all()

    if len(selected_designs) != len(set(selected_ids)):
        found_ids = {design.id for design in selected_designs}
        missing = [design_id for design_id in selected_ids if design_id not in found_ids]
        raise HTTPException(
            status_code=422,
            detail=f"Some selected designs could not be resolved for this paused job: {', '.join(missing[:5])}",
        )

    selection_dir = _materialize_protein_local_selection(job, job, selected_designs, "continue_review")
    source_stage_payload = _derive_source_stage_payload(job, selected_designs, selection_dir)
    output_path = Path(job.output_dir)
    if not output_path.is_absolute():
        output_path = get_data_root() / output_path
    region_manifest_path = output_path / "inputs" / "protein_local_redesign" / "region_manifest.json"
    param_overrides: Dict[str, Any] = {
        **source_stage_payload,
        "selected_input_dir": str(selection_dir),
        "selected_input_manifest": str(_selection_manifest_path(selection_dir)),
    }
    from_stage = None

    if job.awaiting_stage == "post_rfantibody":
        param_overrides.update({
            "plr_backbone_input_pdbs": str(selection_dir),
            "plr_region_manifest": str(region_manifest_path),
        })
        from_stage = "rfantibody"
    elif job.awaiting_stage == "post_fampnn":
        param_overrides["plr_sequence_input_pdbs"] = str(selection_dir)
        from_stage = "fampnn"
    elif job.awaiting_stage == "post_structure_validation":
        param_overrides.update({
            "plr_validation_input_pdbs": str(selection_dir),
            "plr_final_candidate_dir": str(selection_dir),
        })
        from_stage = "structure_validation"
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported PLR review stage '{job.awaiting_stage}'.")

    return await resume_job(
        job_id=job_id,
        request_context=request_context,
        response=response,
        from_stage=from_stage,
        request=ResumeJobRequest(
            from_stage=from_stage,
            param_overrides=param_overrides,
            name_suffix=request.name_suffix or "_continued",
        ),
        session=session,
    )


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

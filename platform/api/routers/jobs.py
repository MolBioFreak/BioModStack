"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from typing import Optional, List, Dict, Any
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
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from database import get_session, Job, Design
from paths import (
    get_code_root,
    get_data_root,
    get_inputs_dir,
    get_results_dir,
    get_work_dir,
    resolve_allowed_path,
    to_allowed_relative,
)
from schemas import JobCreate, JobResponse, JobList, JobStatus
from services.nextflow import launch_nextflow_job, cancel_nextflow_job

from model_registry import get_registry
from services.stage_review import (
    REVIEWABLE_STAGES,
    gate_file_for_stage,
    has_stage_gate,
    infer_antibody_stage_state,
    nextflow_history_status,
    resolve_nextflow_run_dir,
    refresh_gate_payload,
)

router = APIRouter()

# Project root for resolving code-relative paths
CODE_ROOT = get_code_root()
DEFAULT_FAMPNN_CHECKPOINT = "fampnn_0_0.pt"
DEFAULT_PPIFLOW_CHECKPOINT = "nanobody"


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _resolve_requested_design_count(job: Job) -> Optional[int]:
    params = job.params if isinstance(job.params, dict) else {}
    for key in (
        "rfantibody_num_designs",
        "rfd_num_designs",
        "num_designs",
        "num_samples",
    ):
        if key in params:
            count = _coerce_positive_int(params.get(key))
            if count is not None:
                return count
    return None


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
            JobStatus.QUEUED.value,
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
        history_path = run_dir / ".nextflow" / "history"
        if not history_path.exists():
            continue
        try:
            lines = history_path.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        if not lines:
            continue
        parts = lines[-1].split("\t")
        status_token = parts[3].strip().upper() if len(parts) > 3 else ""
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


class ResumeJobRequest(BaseModel):
    """Optional payload for resume calls that need runtime param overrides."""
    from_stage: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    name_suffix: Optional[str] = None


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


class SavedReviewFilterSet(BaseModel):
    """Saved review dataset stored in awaiting_payload."""
    id: str
    name: str
    created_at: str
    visible_count: Optional[int] = None
    source_total_count: Optional[int] = None
    design_ids: List[str] = Field(default_factory=list)
    filter_state: Dict[str, Any] = Field(default_factory=dict)


class SaveReviewFilterSetResponse(BaseModel):
    message: str
    filter_set: SavedReviewFilterSet
    filter_sets: List[SavedReviewFilterSet]


class DeleteReviewFilterSetResponse(BaseModel):
    message: str
    filter_sets: List[SavedReviewFilterSet]


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
    msa_provider: str = Field(default="local")


class ManualMutagenesisConfig(BaseModel):
    """Configuration for explicit user-provided mutation rounds."""
    chain_id: Optional[str] = None
    mutation_sets: List[str] = Field(default_factory=list)
    predictor: str = Field(default="protenix")
    msa_provider: str = Field(default="local")


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


class AntibodyIterationLaunchResponse(BaseModel):
    """Response for viewer-driven antibody iteration actions."""
    message: str
    action: str
    source_job_id: str
    root_job_id: str
    selection_dir: str
    selected_design_count: int
    launched_job: JobResponse


class ManualMutagenesisLaunchRequest(BaseModel):
    """Launch explicit manual mutation sets from selected structures."""
    source_job_id: str = Field(..., min_length=1)
    design_ids: List[str] = Field(default_factory=list)
    review_filter_set_id: Optional[str] = None
    config: ManualMutagenesisConfig
    name_suffix: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)


class ManualMutagenesisLaunchResponse(BaseModel):
    """Response for viewer-driven manual mutation launches."""
    message: str
    source_job_id: str
    selected_design_count: int
    variant_count: int
    launched_job: JobResponse


NANOPORE_PATH_PARAM_KEYS = {
    "pod5_dir",
    "bam_path",
    "fastq_path",
    "reference_fasta",
    "wf_clone_workflow_dir",
}


def resolve_output_dir(output_dir: str) -> Optional[Path]:
    if not output_dir:
        return None
    output_path = Path(output_dir)
    if output_path.is_absolute():
        return output_path
    return get_data_root() / output_dir


def count_structure_files(output_dir: str) -> int:
    """Count PDB and CIF structure files in a job output directory."""
    try:
        output_path = resolve_output_dir(output_dir)
        if not output_path or not output_path.exists():
            return 0
        
        pdb_count = len(list(output_path.glob("**/*.pdb")))
        cif_count = len(list(output_path.glob("**/*.cif")))
        return pdb_count + cif_count
    except Exception:
        return 0


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
    changed = False

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

    stale_failed = str(job.error_message or "").startswith(
        "Reconciled as failed: no active process and no terminal .nextflow/history status"
    )
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


def _review_candidate_count(job: Job) -> Optional[int]:
    if not job.awaiting_input:
        return None
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return None
    payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
    if stage == "post_fampnn":
        filtered_count = payload.get("filtered_candidate_count")
        if isinstance(filtered_count, int) and filtered_count > 0:
            return filtered_count
    candidate_count = payload.get("candidate_count")
    if isinstance(candidate_count, int) and candidate_count >= 0:
        return candidate_count
    return None


def _review_candidate_count_cached(job: Job) -> Optional[int]:
    if not job.awaiting_input:
        return None
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return None
    payload = job.awaiting_payload if isinstance(job.awaiting_payload, dict) else {}
    if stage == "post_fampnn":
        filtered_count = payload.get("filtered_candidate_count")
        if isinstance(filtered_count, int) and filtered_count > 0:
            return filtered_count
    candidate_count = payload.get("candidate_count")
    if isinstance(candidate_count, int) and candidate_count >= 0:
        return candidate_count
    return None


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_antibody_job_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {}

    normalized = dict(params)
    structure_validator = str(
        normalized.get("structure_validator")
        or normalized.get("validation_predictor")
        or normalized.get("pred_method")
        or "boltz2"
    ).strip().lower()
    if structure_validator == "boltz":
        structure_validator = "boltz2"
    if structure_validator not in {"boltz2", "protenix"}:
        structure_validator = "boltz2"
    normalized["structure_validator"] = structure_validator

    canonical_post_validation = normalized.get("run_post_validation_maturation")
    legacy_post_boltz = normalized.get("run_post_boltz_maturation")
    if canonical_post_validation is None and legacy_post_boltz is not None:
        canonical_post_validation = _to_bool(legacy_post_boltz)
    if canonical_post_validation is not None:
        normalized["run_post_validation_maturation"] = _to_bool(canonical_post_validation)
        normalized["run_post_boltz_maturation"] = normalized["run_post_validation_maturation"]

    canonical_thermompnn = normalized.get("run_thermompnn")
    legacy_stability = normalized.get("run_stability_scoring")

    if canonical_thermompnn is None and legacy_stability is not None:
        canonical_thermompnn = _to_bool(legacy_stability)
    if canonical_thermompnn is not None:
        normalized["run_thermompnn"] = _to_bool(canonical_thermompnn)
        normalized["run_stability_scoring"] = normalized["run_thermompnn"]

    ppiflow_checkpoint = str(normalized.get("ppiflow_checkpoint") or "").strip()
    if not ppiflow_checkpoint and (
        _to_bool(normalized.get("run_maturation"))
        or _to_bool(normalized.get("run_post_validation_maturation"))
        or _to_bool(normalized.get("run_post_boltz_maturation"))
    ):
        normalized["ppiflow_checkpoint"] = DEFAULT_PPIFLOW_CHECKPOINT

    gate_stage = normalized.get("interactive_gate_stage")
    if isinstance(gate_stage, str):
        normalized_gate_stage = gate_stage.strip().lower()
        if normalized_gate_stage == "post_boltz_validation":
            normalized_gate_stage = "post_structure_validation"
        if normalized_gate_stage not in {"post_rfantibody", "post_fampnn", "post_structure_validation"}:
            normalized_gate_stage = "post_fampnn"
        normalized["interactive_gate_stage"] = normalized_gate_stage

    return normalized


def _is_antibody_launch(model_id: str, params: Optional[Dict[str, Any]]) -> bool:
    model_id_normalized = (model_id or "").strip().lower()
    params = params if isinstance(params, dict) else {}
    mode_normalized = str(params.get("rfd_mode") or "").strip().lower()
    return (
        model_id_normalized in {
            "template_antibody_denovo",
            "antibody_denovo",
            "antibody_child",
            "rfantibody_child",
        }
        or mode_normalized in {"antibody_denovo_pipeline", "rfantibody_backbone"}
    )


def _normalize_antibody_runtime_paths(model_id: str, params: dict) -> dict:
    if not _is_antibody_launch(model_id, params) or not isinstance(params, dict):
        return params

    normalized = dict(params)
    for key in ("target_pdb", "framework_pdb"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _resolve_alias_path_for_runtime(value)
    return normalized


def _validate_antibody_runtime_paths(model_id: str, params: dict) -> None:
    if not _is_antibody_launch(model_id, params) or not isinstance(params, dict):
        return

    # Skip target PDB validation for iteration/refinement jobs that bypass RFA
    skip_rfa = params.get("skip_rfantibody") or params.get("iteration_action") in ("ui_refinement",)
    target_pdb = str(params.get("target_pdb") or "").strip()
    if target_pdb and not skip_rfa and not Path(target_pdb).exists():
        raise HTTPException(
            status_code=422,
            detail=f"Target PDB not found: {target_pdb}",
        )

    framework_type = str(params.get("framework_type") or "").strip().lower()
    framework_pdb = str(params.get("framework_pdb") or "").strip()
    if framework_type == "custom":
        if not framework_pdb:
            raise HTTPException(
                status_code=422,
                detail="Custom framework selected, but no framework_pdb was provided.",
            )
        if not Path(framework_pdb).exists():
            raise HTTPException(
                status_code=422,
                detail=f"Custom framework PDB not found: {framework_pdb}",
            )
        try:
            chains = set()
            with Path(framework_pdb).open("r") as handle:
                for line in handle:
                    if line.startswith(("ATOM", "HETATM")):
                        chain = line[21:22].strip()
                        if chain:
                            chains.add(chain)
            if not ({"H", "L"} & chains):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Custom framework must be HLT-style with antibody chains labeled H or L. "
                        f"Found chains: {sorted(chains)} in {framework_pdb}"
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to validate framework PDB {framework_pdb}: {exc}",
            ) from exc


def _validate_fampnn_checkpoint_requirements(model_id: str, params: dict) -> None:
    if not isinstance(params, dict):
        return

    model_id_normalized = (model_id or "").strip().lower()
    runs_fampnn = _to_bool(params.get("seq_design_fampnn")) or model_id_normalized in {
        "template_antibody_denovo",
        "fampnn_child",
    }
    runs_maturation = (
        _to_bool(params.get("run_maturation"))
        or _to_bool(params.get("run_post_validation_maturation"))
        or _to_bool(params.get("run_post_boltz_maturation"))
    )
    needs_fampnn_checkpoint = (
        runs_fampnn
        or runs_maturation
    )
    if not needs_fampnn_checkpoint:
        return

    checkpoint = str(params.get("fampnn_checkpoint") or "").strip()
    checkpoint_path = str(params.get("fampnn_checkpoint_path") or "").strip()
    if checkpoint or checkpoint_path:
        return

    params["fampnn_checkpoint"] = DEFAULT_FAMPNN_CHECKPOINT


def _is_meaningful_param_value(value: object) -> bool:
    """Treat empty/sentinel strings as unset."""
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized not in {"", "null", "none", "undefined", "n/a", "na"}
    return bool(value)


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _looks_like_antibody_job(job: Optional[Job]) -> bool:
    if job is None:
        return False
    model_id = (job.model_id or "").strip().lower()
    mode = (job.mode or "").strip().lower()
    params = job.params if isinstance(job.params, dict) else {}
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    has_antibody_params = any(_is_meaningful_param_value(params.get(key)) for key in ("framework_type", "antibody_chains", "epitope_residues"))
    return (
        model_id in {"template_antibody_denovo", "antibody_denovo", "antibody_child"}
        or "antibody" in model_id
        or "antibody" in mode
        or rfd_mode == "antibody_denovo_pipeline"
        or has_antibody_params
    )


async def _resolve_antibody_root_job(session: AsyncSession, source_job_id: str) -> tuple[Job, Job]:
    source_job = await session.get(Job, source_job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail=f"Source job '{source_job_id}' not found")

    # Viewer-launched refinement/iteration rounds are top-level jobs, so parent_job_id does not
    # capture scientific lineage. Prefer the explicit iteration root when it exists.
    iteration_root_id: Optional[str] = None
    if isinstance(source_job.params, dict):
        for key in ("iteration_source_root_job_id", "iteration_source_job_id"):
            value = source_job.params.get(key)
            if isinstance(value, str) and value.strip():
                iteration_root_id = value.strip()
                break

    if iteration_root_id:
        explicit_root = await session.get(Job, iteration_root_id)
        if explicit_root is not None and _looks_like_antibody_job(explicit_root):
            return source_job, explicit_root

    lineage: List[Job] = []
    visited: set[str] = set()
    current: Optional[Job] = source_job
    while current is not None and current.id not in visited:
        lineage.append(current)
        visited.add(current.id)
        if not current.parent_job_id:
            break
        current = await session.get(Job, current.parent_job_id)

    antibody_jobs = [job for job in lineage if _looks_like_antibody_job(job)]
    root_job = antibody_jobs[-1] if antibody_jobs else lineage[-1]
    if not _looks_like_antibody_job(root_job):
        raise HTTPException(
            status_code=422,
            detail="Selected job is not part of an antibody workflow lineage.",
        )
    return source_job, root_job


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


def _iter_saved_review_filter_sets(job: Optional[Job]) -> List[SavedReviewFilterSet]:
    if job is None or not isinstance(job.awaiting_payload, dict):
        return []
    raw_sets = job.awaiting_payload.get("review_filter_sets")
    if not isinstance(raw_sets, list):
        return []

    saved_sets: List[SavedReviewFilterSet] = []
    for entry in raw_sets:
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


def _resolve_design_structure_path(raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=422, detail="Selected design is missing a structure path.")

    raw = Path(raw_path)
    candidates: List[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([
            (get_data_root() / raw),
            (CODE_ROOT / raw),
            raw.resolve(),
        ])

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            continue
        if resolved.exists():
            return resolved

    raise HTTPException(
        status_code=422,
        detail=f"Selected design structure path does not exist: {raw_path}",
    )


def _prune_iteration_params(base_params: Dict[str, Any]) -> Dict[str, Any]:
    pruned = deepcopy(base_params) if isinstance(base_params, dict) else {}
    for key in {
        "job_id",
        "run_id",
        "api_url",
        "out_dir",
        "output_dir",
        "interactive_gate_continue",
        "fampnn_collected_pdbs",
        "rfantibody_input_pdbs",
        "skip_rfantibody",
        "skip_fampnn",
        "iteration_source_job_id",
        "iteration_source_root_job_id",
        "iteration_source_design_ids",
        "iteration_action",
        "iteration_selection_dir",
        "manual_mutation_fixed_positions_json",
        "manual_mutation_mode",
        "manual_mutation_method",
        "mutation_seed_refinement_trigger",
        "mutation_variant",
    }:
        pruned.pop(key, None)
    return _normalize_antibody_job_params(pruned)


AA_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}

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


def _extract_chain_records_from_pdb(pdb_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    chain_records: Dict[str, List[Dict[str, Any]]] = {}
    seen_residues: Dict[str, set[tuple[int, str]]] = {}
    with pdb_path.open() as handle:
        for line in handle:
            if not line.startswith("ATOM") or len(line) < 27:
                continue
            if line[12:16].strip() != "CA":
                continue
            res_name = line[17:20].strip()
            chain_id = (line[21].strip() or "_").upper()
            aa = AA_CODES.get(res_name)
            if aa is None:
                continue
            try:
                resseq = int(line[22:26].strip())
            except ValueError:
                continue
            icode = line[26].strip()
            residue_key = (resseq, icode)
            if chain_id not in chain_records:
                chain_records[chain_id] = []
                seen_residues[chain_id] = set()
            if residue_key in seen_residues[chain_id]:
                continue
            seen_residues[chain_id].add(residue_key)
            chain_records[chain_id].append(
                {
                    "resseq": resseq,
                    "icode": icode,
                    "aa": aa,
                }
            )
    return chain_records


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
        chain_records = _extract_chain_records_from_pdb(design_path)
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
                    "source_pdb_path": str(design_path),
                    "binder_chain_id": binder_chain_id,
                    "mutation": mutation_meta,
                    "locked_positions_spec": ",".join(fixed_spec_tokens),
                }
            )

    return variants


def _create_antibody_selection_dir(action: str) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / "antibody"
    selection_root.mkdir(parents=True, exist_ok=True)
    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)
    return selection_dir


def _write_seeded_refinement_metadata(
    selection_dir: Path,
    root_job: Job,
    source_job: Job,
    action: str,
    manifest_items: List[Dict[str, Any]],
    fixed_positions_by_pdb: Optional[Dict[str, str]] = None,
) -> None:
    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "root_job_id": root_job.id,
        "source_job_id": source_job.id,
        "design_count": len(manifest_items),
        "designs": manifest_items,
    }
    (selection_dir / "selection_manifest.json").write_text(json.dumps(manifest, indent=2))
    if fixed_positions_by_pdb:
        (selection_dir / "mutation_fixed_positions.json").write_text(json.dumps(fixed_positions_by_pdb, indent=2, sort_keys=True))


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
            "source_design_id": variant.get("source_design_id"),
            "source_design_name": variant.get("source_design_name"),
            "source_pdb_path": str(source_path),
            "selection_pdb_path": str(dest_path),
            "binder_chain_id": variant.get("binder_chain_id"),
            "mutation": variant.get("mutation"),
        })
        if variant.get("locked_positions_spec"):
            fixed_positions_by_pdb[dest_path.stem] = str(variant["locked_positions_spec"])

    _write_seeded_refinement_metadata(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        fixed_positions_by_pdb=fixed_positions_by_pdb,
    )
    return selection_dir, selection_dir / "mutation_fixed_positions.json"


def _sequence_positions_from_mutation_meta(mutation_meta: Any) -> List[int]:
    if isinstance(mutation_meta, list):
        positions = []
        for mutation in mutation_meta:
            if not isinstance(mutation, dict):
                continue
            if str(mutation.get("type")).lower() == "substitution":
                try:
                    positions.append(int(mutation["position"]))
                except (TypeError, ValueError):
                    continue
        return sorted(set(positions))

    if not isinstance(mutation_meta, dict):
        return []

    mutation_type = str(mutation_meta.get("type") or "").strip().lower()
    try:
        position = int(mutation_meta.get("position"))
    except (TypeError, ValueError):
        return []

    if mutation_type == "substitution":
        return [position]
    if mutation_type == "insertion":
        inserted = str(mutation_meta.get("to") or "")
        if not inserted:
            return []
        start = position + 1
        return list(range(start, start + len(inserted)))
    return []


def _locked_spec_from_design_pdb(
    pdb_path: Path,
    binder_chain_id: str,
    mutation_meta: Any,
) -> str:
    chain_records = _extract_chain_records_from_pdb(pdb_path)
    binder_records = chain_records.get(str(binder_chain_id).strip().upper()) or []
    if not binder_records:
        return ""

    spec_tokens: List[str] = []
    for position in _sequence_positions_from_mutation_meta(mutation_meta):
        if 1 <= position <= len(binder_records):
            resseq = int(binder_records[position - 1]["resseq"])
            spec_tokens.append(f"{str(binder_chain_id).strip().upper()}{resseq}")
    return ",".join(sorted(set(spec_tokens)))


def _materialize_seed_selection_from_completed_designs(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    design_job_map: Dict[str, Job],
    action: str,
) -> tuple[Path, Path]:
    selection_dir = _create_antibody_selection_dir(action)
    manifest_items: List[Dict[str, Any]] = []
    fixed_positions_by_pdb: Dict[str, str] = {}

    for idx, design in enumerate(designs, start=1):
        source_path = _resolve_design_structure_path(design.pdb_path)
        dest_path = selection_dir / f"{idx:03d}_{design.id}.pdb"
        try:
            os.symlink(source_path, dest_path)
        except OSError:
            shutil.copy2(source_path, dest_path)

        design_job = design_job_map.get(design.job_id)
        params = design_job.params if design_job and isinstance(design_job.params, dict) else {}
        variant_meta = params.get("mutation_variant") if isinstance(params.get("mutation_variant"), dict) else {}
        fixed_spec = ""
        if variant_meta:
            fixed_spec = _locked_spec_from_design_pdb(
                pdb_path=source_path,
                binder_chain_id=str(variant_meta.get("binder_chain_id") or ""),
                mutation_meta=variant_meta.get("mutation"),
            )

        manifest_items.append({
            "design_id": design.id,
            "design_name": design.name,
            "design_job_id": design.job_id,
            "source_pdb_path": str(source_path),
            "selection_pdb_path": str(dest_path),
            "mutation_variant": variant_meta,
        })
        if fixed_spec:
            fixed_positions_by_pdb[dest_path.stem] = fixed_spec

    _write_seeded_refinement_metadata(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
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

    effective_msa_provider = "local" if requested_msa_provider == "colabfold_api" else requested_msa_provider
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
        "openmm_enabled": False,
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
        for key in (
            "boltz_use_msa",
            "boltz_sampling_steps",
            "boltz_recycling_steps",
            "boltz_num_samples",
            "boltz_use_potentials",
            "boltz_step_scale",
            "boltz_predict_affinity",
            "boltz_diffusion_samples_affinity",
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

    if param_overrides:
        launch_params.update(dict(param_overrides))

    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else "manual_mutagenesis"
    model_id = "protenix" if predictor == "protenix" else "boltz2"
    launch_request = JobCreate(
        name=f"{source_job.name}_{suffix}",
        model_id=model_id,
        mode="complex",
        params=launch_params,
        pinned_gpu=source_job.pinned_gpu,
    )
    message_note = ""
    if requested_msa_provider == "colabfold_api":
        message_note = " ColabFold API was downgraded to local MSA because batch mutagenesis jobs do not support server-backed MSA yet."
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
        chain_records = _extract_chain_records_from_pdb(design_path)
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

    effective_msa_provider = msa_provider
    if len(variants) > 1 and msa_provider == "colabfold_api":
        effective_msa_provider = "local"

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
        "openmm_enabled": False,
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
        for key in (
            "boltz_use_msa",
            "boltz_sampling_steps",
            "boltz_recycling_steps",
            "boltz_num_samples",
            "boltz_use_potentials",
            "boltz_step_scale",
            "boltz_predict_affinity",
            "boltz_diffusion_samples_affinity",
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
        pinned_gpu=root_job.pinned_gpu,
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


def _materialize_antibody_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    action: str,
) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / "antibody"
    selection_root.mkdir(parents=True, exist_ok=True)

    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)

    manifest_items: List[Dict[str, Any]] = []
    for idx, design in enumerate(designs, start=1):
        if not design.pdb_path:
            raise HTTPException(
                status_code=422,
                detail=f"Design '{design.name}' is missing a structure path.",
            )

        source_path = _resolve_design_structure_path(design.pdb_path)
        if source_path.suffix.lower() != ".pdb":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Design '{design.name}' is backed by '{source_path.name}', not a PDB file. "
                    "Antibody iteration actions currently require PDB-backed selections."
                ),
            )

        dest_path = selection_dir / f"{idx:03d}_{design.id}.pdb"
        try:
            os.symlink(source_path, dest_path)
        except OSError:
            shutil.copy2(source_path, dest_path)

        manifest_items.append({
            "design_id": design.id,
            "design_name": design.name,
            "design_job_id": design.job_id,
            "source_pdb_path": str(source_path),
            "selection_pdb_path": str(dest_path),
        })

    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "root_job_id": root_job.id,
        "source_job_id": source_job.id,
        "design_count": len(manifest_items),
        "designs": manifest_items,
    }
    (selection_dir / "selection_manifest.json").write_text(json.dumps(manifest, indent=2))
    return selection_dir


def _build_antibody_iteration_job(
    root_job: Job,
    source_job: Job,
    action: str,
    selection_dir: Path,
    design_ids: List[str],
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> JobCreate:
    action = action.strip().lower()
    action_map = {
        "validate_boltz2": {
            "suffix": "validate_boltz2",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": True,
                "structure_validator": "boltz2",
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "validate_protenix": {
            "suffix": "validate_protenix",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": True,
                "structure_validator": "protenix",
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "ppiflow_maturation": {
            "suffix": "ppiflow_maturation",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_post_validation_maturation": True,
                "run_post_boltz_maturation": True,
                "run_maturation": True,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "fampnn_redesign": {
            "suffix": "fampnn_redesign",
            "params": {
                "skip_rfantibody": True,
                "rfantibody_input_pdbs": str(selection_dir),
                "fampnn_collected_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_fampnn",
            },
        },
        "frustrampnn": {
            "suffix": "frustrampnn",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": True,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "ui_refinement": {
            "suffix": "refinement",
            "params": {},
        },
    }
    if action not in action_map:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported antibody iteration action '{action}'. "
                "Allowed: validate_boltz2, validate_protenix, ppiflow_maturation, fampnn_redesign, frustrampnn, cdr_indel_round, mutation_seeded_refinement, ui_refinement."
            ),
        )

    launch_params = _prune_iteration_params(root_job.params if isinstance(root_job.params, dict) else {})
    launch_params.update({
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": root_job.id,
        "iteration_source_design_ids": design_ids,
        "iteration_action": action,
        "iteration_selection_dir": str(selection_dir),
        "interactive_gate_continue": False,
    })
    
    # Preserve epitope residue configurations for contact calculations during refinement
    if isinstance(root_job.params, dict):
        for key in ["epitope_residues", "selected_residues"]:
            if key in root_job.params:
                launch_params[key] = root_job.params[key]
                
    launch_params.update(action_map[action]["params"])

    for key in ["rfantibody_input_pdbs", "fampnn_collected_pdbs"]:
        if launch_params.get(key) is None:
            launch_params.pop(key, None)

    if param_overrides:
        launch_params.update(param_overrides)

    if action == "ui_refinement":
        refinement_screen_keys = {
            "enable_rfantibody_filter",
            "rfantibody_min_epitope_contacts",
            "rfantibody_max_epitope_distance",
            "rfantibody_min_target_contacts",
            "rfantibody_max_target_distance",
            "rfantibody_max_epitope_centroid_distance",
            "rfantibody_contact_distance_threshold",
            "rfantibody_target_contact_distance_threshold",
        }

        def _invalid_refinement_value(value: Any) -> bool:
            return value is None or (isinstance(value, str) and (not value.strip() or value.strip() == "refinement_mode"))

        def _has_explicit_refinement_screen_request(overrides: Dict[str, Any]) -> bool:
            if overrides.get("enable_rfantibody_filter") is True:
                return True
            for key in (
                "rfantibody_min_epitope_contacts",
                "rfantibody_max_epitope_distance",
                "rfantibody_min_target_contacts",
                "rfantibody_max_target_distance",
                "rfantibody_max_epitope_centroid_distance",
            ):
                if key in overrides and not _invalid_refinement_value(overrides.get(key)):
                    return True
            return False

        has_refinement_screen_override = _has_explicit_refinement_screen_request(param_overrides or {})

        def _pick_refinement_context(key: str) -> Any:
            for job in (root_job, source_job):
                params = job.params if isinstance(job.params, dict) else {}
                candidate = params.get(key)
                if not _invalid_refinement_value(candidate):
                    return candidate
            return None

        for key in ("target_pdb", "epitope_residues", "selected_residues", "antigen_chains"):
            if _invalid_refinement_value(launch_params.get(key)):
                fallback = _pick_refinement_context(key)
                if fallback is None:
                    launch_params.pop(key, None)
                else:
                    launch_params[key] = fallback

        launch_params["skip_rfantibody"] = True
        
        # If the UI mapped sequence design (like FAMPNN), the inputs go to rfantibody_input_pdbs.
        # If sequence design is fully skipped (starting at validation/maturation), inputs go to fampnn_collected_pdbs.
        if launch_params.get("seq_design_fampnn") or launch_params.get("seq_design_antifold") or launch_params.get("seq_design_proteinmpnn"):
            launch_params["rfantibody_input_pdbs"] = str(selection_dir)
            launch_params["fampnn_collected_pdbs"] = None
        else:
            launch_params["fampnn_collected_pdbs"] = str(selection_dir)
            launch_params["rfantibody_input_pdbs"] = None

        # UI refinement starts from hand-selected structures. Do not reapply the
        # coarse RFantibody screen unless the caller explicitly overrides it.
        if not has_refinement_screen_override:
            launch_params["enable_rfantibody_filter"] = False
            for key in refinement_screen_keys - {"enable_rfantibody_filter"}:
                launch_params.pop(key, None)

    launch_params = _normalize_antibody_job_params(launch_params)
    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else action_map[action]["suffix"]
    job_name = f"{root_job.name}_{suffix}"

    return JobCreate(
        name=job_name,
        model_id="template_antibody_denovo",
        mode="antibody_denovo_pipeline",
        params=launch_params,
        pinned_gpu=root_job.pinned_gpu,
    )


def _to_stage_output_path(path: Path) -> str:
    try:
        return to_allowed_relative(path)
    except Exception:
        return str(path)


def _stage_output_exists(output_path: Optional[Path], output_value: str) -> bool:
    if not output_value or not isinstance(output_value, str):
        return False

    raw = Path(output_value)
    candidates: List[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(get_data_root() / output_value)
        if output_path is not None:
            candidates.append(output_path / output_value)
            try:
                output_rel = to_allowed_relative(output_path)
                if output_value.startswith(f"{output_rel}/"):
                    suffix = output_value[len(output_rel) + 1 :]
                    candidates.append(output_path / suffix)
            except Exception:
                pass

    for candidate in candidates:
        try:
            if candidate.exists():
                return True
        except Exception:
            continue
    return False


def _sanitize_nanopore_stage_outputs(
    stage_outputs: Dict[str, List[str]],
    output_dir: Optional[str],
) -> Dict[str, List[str]]:
    output_path = resolve_output_dir(output_dir or "")
    cleaned: Dict[str, List[str]] = {}
    for stage, outputs in (stage_outputs or {}).items():
        if not isinstance(outputs, list):
            continue
        filtered = [
            value
            for value in outputs
            if isinstance(value, str) and _stage_output_exists(output_path, value)
        ]
        deduped = _dedupe_preserve_order(filtered)
        if deduped:
            cleaned[stage] = deduped
    return cleaned


def _resolve_nanopore_fastq_qc_mode(params: Optional[dict]) -> tuple[bool, bool]:
    """Return (enabled, use_legacy_multimer_stages)."""
    if not isinstance(params, dict):
        return False, False

    run_fastq_qc = params.get("run_fastq_qc")
    if isinstance(run_fastq_qc, bool):
        return run_fastq_qc, False

    run_multimer_qc = params.get("run_multimer_qc")
    if isinstance(run_multimer_qc, bool):
        return run_multimer_qc, True

    return False, False


def _resolve_nanopore_bam_realign(params: Optional[dict]) -> bool:
    if not isinstance(params, dict):
        return False
    return _to_bool(params.get("bam_force_realign"))


def _infer_nanopore_stage_outputs(
    output_dir: Optional[str],
    params: Optional[dict] = None,
) -> Dict[str, List[str]]:
    output_path = resolve_output_dir(output_dir or "")
    if not output_path or not output_path.exists():
        return {}

    expected = {
        "dorado_basecall": [
            "basecall/calls.bam",
            "basecall/basecall.log",
            "basecall/sequencing_summary.tsv",
        ],
        "dorado_align": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/align.log",
        ],
        "bam_prepare": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/bam_prepare.log",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/reference_prepare.log",
        ],
        "fastq_align": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/fastq_align.log",
        ],
        "fastq_qc": [
            "fastq_qc/read_lengths.tsv",
            "fastq_qc/fastq_qc_summary.tsv",
            "fastq_qc/fastq_alignment_stats.tsv",
            "fastq_qc/fastq_coverage.tsv",
            "fastq_qc/igv_coverage_depth.bedgraph",
            "fastq_qc/igv_position_gradient.bedgraph",
            "fastq_qc/igv_gc_content.bedgraph",
            "fastq_qc/igv_gc_zscore.bedgraph",
            "fastq_qc/igv_split_read_density.bedgraph",
            "fastq_qc/igv_softclip_density.bedgraph",
            "fastq_qc/igv_junction_hotspots.bed",
            "fastq_qc/igv_report_sites.bed",
            "fastq_qc/igv_report_sites.tsv",
            "fastq_qc/igv_track_config.json",
            "fastq_qc/igv_report.html",
            "fastq_qc/igv_report.log",
            "fastq_qc/fastq_consensus.fasta",
            "fastq_qc/fastq_consensus.fasta.fai",
            "fastq_qc/fastq_consensus.log",
            "fastq_qc/fastq_qc.log",
        ],
        "modkit": [
            "methylation/methylation.bed",
            "methylation/pileup.log",
            "methylation/modkit_summary.tsv",
            "methylation/summary.log",
        ],
        "multimer_qc": [
            "multimer_qc/read_lengths.tsv",
            "multimer_qc/multimer_summary.tsv",
            "multimer_qc/multimer_candidates.tsv",
            "multimer_qc/multimer_qc.log",
        ],
        "dimer_analysis": [
            "multimer_qc/dimer_candidates.fastq",
            "multimer_qc/dimer_candidates.fasta",
            "multimer_qc/dimer_read_lengths.tsv",
            "multimer_qc/dimer_analysis_summary.tsv",
            "multimer_qc/dimer_analysis.log",
            "multimer_qc/dimer_breakpoint_call.tsv",
            "multimer_qc/dimer_evidence_by_position.tsv",
            "multimer_qc/dimer_read_events.tsv",
            "multimer_qc/dimer_breakpoint_sequences.tsv",
            "multimer_qc/dimer_secondary_anomalies.tsv",
            "multimer_qc/dimer_secondary_summary.tsv",
            "multimer_qc/dimer_diagnostics.tar.gz",
            "multimer_qc/dimer_candidates.aligned.bam",
            "multimer_qc/dimer_candidates.aligned.bam.bai",
            "multimer_qc/dimer_reference.fasta",
            "multimer_qc/dimer_reference.fasta.fai",
            "multimer_qc/dominant_dimer_consensus.fasta",
            "multimer_qc/dominant_dimer_consensus.log",
            "multimer_qc/dominant_dimer_consensus_metadata.tsv",
            # Legacy fallback artifacts (older runs)
            "multimer_qc/dimer_breakpoint_screen.tsv",
            "multimer_qc/dimer_junction_clusters.tsv",
            "multimer_qc/dimer_junction_events.tsv",
            "multimer_qc/dimer_read_junctions.tsv",
            "multimer_qc/dimer_read_ledger.tsv",
            "multimer_qc/dimer_single_ref_split_profile.tsv",
        ],
        "wf_clone_validation": [
            "assembly/wf_clone.log",
            "assembly/wf_clone_out",
            "assembly/wf_clone_out/wf-clone-validation-report.html",
            "assembly/wf_clone_out/sample_status.txt",
        ],
    }

    allowed_stages = set(expected.keys())
    if isinstance(params, dict):
        has_pod5 = _is_meaningful_param_value(params.get("pod5_dir"))
        has_bam = _is_meaningful_param_value(params.get("bam_path"))
        has_fastq = _is_meaningful_param_value(params.get("fastq_path"))
        has_reference = _is_meaningful_param_value(params.get("reference_fasta"))
        fastq_qc_enabled, legacy_multimer_mode = _resolve_nanopore_fastq_qc_mode(params)
        bam_force_realign = _resolve_nanopore_bam_realign(params)

        allowed_stages = set()
        if has_pod5:
            allowed_stages.add("dorado_basecall")
        if has_pod5 and has_reference:
            allowed_stages.add("dorado_align")
        if has_bam and has_reference and bam_force_realign:
            allowed_stages.add("dorado_align")
        if has_bam:
            allowed_stages.add("bam_prepare")
        if has_pod5 and not has_reference:
            allowed_stages.add("bam_prepare")
        if has_fastq and has_reference:
            allowed_stages.add("fastq_align")
        if fastq_qc_enabled and has_fastq and has_reference and not legacy_multimer_mode:
            allowed_stages.add("fastq_qc")
        if params.get("run_modkit") is not False and (has_pod5 or has_bam):
            allowed_stages.add("modkit")
        if fastq_qc_enabled and legacy_multimer_mode and has_fastq:
            allowed_stages.add("multimer_qc")
        if fastq_qc_enabled and legacy_multimer_mode and has_fastq and has_reference:
            allowed_stages.add("dimer_analysis")
        if params.get("run_assembly") is True and (has_pod5 or has_bam):
            allowed_stages.add("wf_clone_validation")

    inferred: Dict[str, List[str]] = {}
    for stage, rel_paths in expected.items():
        if stage not in allowed_stages:
            continue
        found: List[str] = []
        for rel in rel_paths:
            p = output_path / rel
            if p.exists():
                found.append(_to_stage_output_path(p))
        if found:
            inferred[stage] = _dedupe_preserve_order(found)
    return inferred


def _resolve_stage_state_for_response(job: Job) -> tuple[List[str], Dict[str, List[str]]]:
    completed = _dedupe_preserve_order(list(job.completed_stages or []))
    stage_outputs = dict(job.stage_outputs or {})

    if job.mode in ["methylation_analysis", "nanopore_methylation"]:
        stage_outputs = _sanitize_nanopore_stage_outputs(stage_outputs, job.output_dir)
        inferred_outputs = _infer_nanopore_stage_outputs(job.output_dir, job.params)
        for stage, outputs in inferred_outputs.items():
            existing = stage_outputs.get(stage)
            if isinstance(existing, list):
                merged = _dedupe_preserve_order([*existing, *outputs])
            else:
                merged = outputs
            stage_outputs[stage] = merged
            if merged and stage not in completed:
                completed.append(stage)

    completed, stage_outputs = infer_antibody_stage_state(job, completed, stage_outputs)

    return completed, stage_outputs


def _has_protenix_template_db(mmcif_dir: Path) -> bool:
    if not mmcif_dir.is_dir():
        return False
    patterns = ("*.cif", "*.mmcif", "*.cif.gz", "*.mmcif.gz")
    for pattern in patterns:
        try:
            next(mmcif_dir.rglob(pattern))
            return True
        except StopIteration:
            continue
    return False


def _validate_protenix_template_requirements(model_id: str, params: dict) -> None:
    if model_id != "protenix":
        return
    if not _to_bool(params.get("protenix_use_template", False)):
        return

    code_root_raw = params.get("code_root") or os.getenv("BMS_HOME")
    code_root = Path(code_root_raw).expanduser() if code_root_raw else get_code_root()
    mmcif_dir = code_root / ".protenix_cache" / "mmcif"

    if _has_protenix_template_db(mmcif_dir):
        return

    raise HTTPException(
        status_code=422,
        detail={
            "validation_errors": [
                (
                    "Protenix template mode requires an mmCIF database, but none was found at "
                    f"{mmcif_dir}. Disable protenix_use_template or populate that directory "
                    "before submitting."
                )
            ]
        },
    )


def _resolve_alias_path_for_runtime(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    expanded = os.path.expanduser(raw)
    if Path(expanded).is_absolute():
        return expanded
    try:
        return str(resolve_allowed_path(raw))
    except ValueError:
        return raw


def _normalize_nanopore_runtime_paths(model_id: str, params: dict) -> dict:
    if model_id != "nanopore" or not isinstance(params, dict):
        return params
    normalized = dict(params)
    for key in NANOPORE_PATH_PARAM_KEYS:
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _resolve_alias_path_for_runtime(value)
    return normalized


def _normalize_nanopore_modbase_for_validation(
    registry,
    model_id: str,
    params: dict,
) -> dict:
    """
    Normalize known modbase aliases before schema validation.

    This prevents launcher/API 422 regressions when frontend and backend are on
    slightly different nanopore enum revisions (e.g. canonical vs legacy alias).
    """
    if model_id != "nanopore" or not isinstance(params, dict):
        return params

    raw_value = params.get("modified_bases")
    if not isinstance(raw_value, str):
        return params

    cleaned_value = " ".join(raw_value.strip().split())
    normalized = dict(params)
    normalized["modified_bases"] = cleaned_value

    model = registry.get_model(model_id)
    if not model:
        return normalized

    modbase_param = next((p for p in model.params if p.name == "modified_bases"), None)
    if not modbase_param or not modbase_param.enum:
        return normalized

    enum_values = set(modbase_param.enum)
    canonical = "6mA 4mC_5mC"
    legacy = "6mA 5mC"

    if cleaned_value == canonical and canonical not in enum_values and legacy in enum_values:
        normalized["modified_bases"] = legacy
    elif cleaned_value == legacy and legacy not in enum_values and canonical in enum_values:
        normalized["modified_bases"] = canonical

    return normalized


@router.get("", response_model=JobList)
async def list_jobs(
    status: Optional[JobStatus] = None,
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    include_children: bool = False,  # New param: show child jobs if True
    session: AsyncSession = Depends(get_session)
):
    """List all jobs with optional status filter and search query."""
    # Optimized query: fetch jobs and design counts in one go
    # This replaces the N+1 query loop with a single GROUP BY query
    query = (
        select(Job, func.count(Design.id).label("design_count"))
        .outerjoin(Design, Design.job_id == Job.id)
        .group_by(Job.id)
        .order_by(Job.created_at.desc())
    )
    
    # Filter out child jobs by default (show only parent/top-level jobs)
    if not include_children:
        query = query.where(Job.parent_job_id == None)
    
    if status:
        query = query.where(Job.status == status.value)
    
    if q:
        query = query.where(Job.name.ilike(f"%{q}%"))
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    rows = result.all()
    
    # Get total count (for pagination) - also exclude children
    count_query = select(func.count(Job.id))
    if not include_children:
        count_query = count_query.where(Job.parent_job_id == None)
    if status:
        count_query = count_query.where(Job.status == status.value)
    if q:
        count_query = count_query.where(Job.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar()

    
    job_responses = []
    for job, design_count in rows:
        completed_stages = _dedupe_preserve_order(list(job.completed_stages or []))
        stage_outputs = dict(job.stage_outputs or {})
        review_count = _review_candidate_count_cached(job)
        if (design_count or 0) == 0 and review_count is not None:
            design_count = review_count
        
        job_responses.append(JobResponse(
            id=job.id,
            name=job.name,
            status=job.status,
            model_id=job.model_id,
            mode=job.mode,
            params=job.params,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            output_dir=job.output_dir,
            error_message=job.error_message,
            design_count=design_count,  # Now joined from DB
            requested_design_count=_resolve_requested_design_count(job),
            batch_id=job.batch_id,
            batch_name=job.batch_name,
            parent_job_id=job.parent_job_id,
            child_stage=job.child_stage,
            current_stage=job.current_stage,
            completed_stages=completed_stages,
            stage_outputs=stage_outputs,
            awaiting_input=job.awaiting_input,
            awaiting_stage=job.awaiting_stage,
            awaiting_payload=job.awaiting_payload,
            decision_history=job.decision_history,
        ))
    
    return JobList(jobs=job_responses, total=total)


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Create and queue a new pipeline job."""
    registry = get_registry()

    # Keep model schema in sync with disk changes during long-lived API sessions.
    try:
        registry.reload()
    except Exception as e:
        logger.warning(f"Failed to reload model registry before validation: {e}")

    if isinstance(job_data.params, dict):
        job_data.params = _normalize_nanopore_modbase_for_validation(
            registry,
            job_data.model_id,
            job_data.params,
        )
        # Convert browse-alias paths (e.g. downloads/...) to host absolute paths for runtime.
        job_data.params = _normalize_nanopore_runtime_paths(job_data.model_id, job_data.params)
        job_data.params = _normalize_antibody_runtime_paths(job_data.model_id, job_data.params)
        job_data.params = _normalize_antibody_job_params(job_data.params)
    
    # Skip validation for template jobs and mutagenesis batches
    # Mutagenesis uses mutagenesis_variants array instead of top-level sequence
    is_mutagenesis = 'mutagenesis_variants' in job_data.params
    if not job_data.model_id.startswith('template_') and not is_mutagenesis:
        # Validate model and mode
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, job_data.params)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

    _validate_protenix_template_requirements(job_data.model_id, job_data.params)
    _validate_fampnn_checkpoint_requirements(job_data.model_id, job_data.params)
    _validate_antibody_runtime_paths(job_data.model_id, job_data.params)

    if job_data.parent_job_id and job_data.child_stage and job_data.name:
        existing_child_result = await session.execute(
            select(Job)
            .where(
                Job.parent_job_id == job_data.parent_job_id,
                Job.child_stage == job_data.child_stage,
                Job.name == job_data.name,
            )
            .order_by(Job.created_at.asc())
        )
        existing_child = existing_child_result.scalars().first()
        if existing_child is not None:
            logger.info(
                "[QUEUE] Reusing existing child job %s for parent=%s stage=%s name=%s",
                existing_child.id,
                job_data.parent_job_id,
                job_data.child_stage,
                job_data.name,
            )
            return JobResponse(
                id=existing_child.id,
                name=existing_child.name,
                status=existing_child.status,
                model_id=existing_child.model_id,
                mode=existing_child.mode,
                params=existing_child.params,
                created_at=existing_child.created_at,
                started_at=existing_child.started_at,
                completed_at=existing_child.completed_at,
                output_dir=existing_child.output_dir,
                error_message=existing_child.error_message,
                design_count=0,
                batch_id=existing_child.batch_id,
                batch_name=existing_child.batch_name,
                parent_job_id=existing_child.parent_job_id,
                child_stage=existing_child.child_stage,
                awaiting_input=existing_child.awaiting_input,
                awaiting_stage=existing_child.awaiting_stage,
                awaiting_payload=existing_child.awaiting_payload,
                decision_history=existing_child.decision_history,
            )
    
    # Detect complex components for logging (info level)
    if 'complex_components' in job_data.params:
        logger.info(f"Job contains {len(job_data.params['complex_components'])} complex components")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FORCE FLAGS FOR RFANTIBODY BACKBONE MODE
    # ═══════════════════════════════════════════════════════════════════════════
    if job_data.params.get('rfd_mode') == 'rfantibody_backbone':
        job_data.params['seq_design_fampnn'] = False
        job_data.params['seq_design_antifold'] = False
        job_data.params['seq_design_proteinmpnn'] = False
        job_data.params['run_structure_validation'] = False
        job_data.params['run_immunogenicity_scoring'] = False
        job_data.params['run_thermompnn'] = False
        job_data.params['run_stability_scoring'] = False
        job_data.params['run_maturation'] = False
        logger.info("[QUEUE] rfantibody_backbone mode detected. Silencing downstream validation flags.")

    # ═══════════════════════════════════════════════════════════════════════════
    # JOB MULTIPLIER: Create N separate jobs for multi-GPU distribution
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Check for mutagenesis batch submission (variants array)
    # This allows a single API call to create MSA batch job + N inference jobs
    mutagenesis_variants = job_data.params.pop('mutagenesis_variants', None)
    
    # Extract num_parallel_jobs (job multiplier) and remove from params passed to Nextflow
    # This way each individual Nextflow run does 1 simulation, but we create N jobs
    num_jobs = job_data.params.pop('num_parallel_jobs', 1)
    if mutagenesis_variants and len(mutagenesis_variants) > 0:
        # Mutagenesis mode: num_jobs = number of variants
        num_jobs = len(mutagenesis_variants)
        logger.info(f"[MUTAGENESIS] Detected {num_jobs} variants in batch submission")
    elif num_jobs is None or num_jobs < 1:
        num_jobs = 1

    # ColabFold API mode is currently scoped to single structure-prediction jobs.
    msa_provider = str(job_data.params.get("msa_provider", "local") or "local").strip().lower()
    if msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid msa_provider '{msa_provider}'. Allowed: local, colabfold_api",
        )

    if msa_provider == "colabfold_api":
        is_structure_model = job_data.model_id in {"boltz2", "rf3", "protenix"}
        is_structure_mode = job_data.mode in {"predict", "complex"}
        if not is_structure_model or not is_structure_mode:
            raise HTTPException(
                status_code=422,
                detail=(
                    "msa_provider=colabfold_api is currently supported only for "
                    "single structure_prediction jobs (boltz2/rf3/protenix predict|complex)."
                ),
            )
        if mutagenesis_variants:
            raise HTTPException(
                status_code=422,
                detail="msa_provider=colabfold_api is not yet supported for mutagenesis batch jobs.",
            )
        if num_jobs > 1:
            raise HTTPException(
                status_code=422,
                detail="msa_provider=colabfold_api currently requires num_parallel_jobs=1.",
            )
    
    # Create output directory (base for all jobs in batch)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_output_dir = str(get_results_dir() / f"{job_data.name}_{timestamp}")
    os.makedirs(base_output_dir, exist_ok=True)
    
    # Extract sequence length for VRAM estimation (same for all jobs in batch)
    # PRIORITY: 1) job_data.sequence_length (explicit), 2) extract from params, 3) fallback
    sequence_length = job_data.sequence_length  # May be explicitly set by spawn scripts
    
    if sequence_length is None:
        # Try to extract from params
        if 'sequence_input' in job_data.params and job_data.params['sequence_input']:
            sequence_length = len(job_data.params['sequence_input'])
        elif 'complex_components' in job_data.params:
            # For complexes, use the longest chain
            max_len = 0
            for comp in job_data.params['complex_components']:
                if comp.get('type') == 'protein' and comp.get('sequence'):
                    max_len = max(max_len, len(comp['sequence']))
            if max_len > 0:
                sequence_length = max_len
    
    if sequence_length is None:
        sequence_length = 300  # Default fallback
    
    # Estimate VRAM based on model type
    from services.gpu_orchestrator import estimate_vram, estimate_protenix_tokens
    estimate_model_id = job_data.model_id
    if job_data.mode == "maturation_child":
        estimate_model_id = "maturation_child"
    if estimate_model_id == "protenix":
        sequence_length = estimate_protenix_tokens(job_data.params, sequence_length)
    vram_estimate = estimate_vram(estimate_model_id, sequence_length, job_data.params)

    # ─── CPU-only override: orchestration/launcher jobs should not consume GPU slots ─────
    if job_data.mode == "antibody_denovo_pipeline" and str(job_data.params.get("parallel_mode") or "").strip().lower() == "full_orchestrator":
        vram_estimate = 0
        job_data.pinned_gpu = None
        logger.info(f"[QUEUE] Orchestrator parent job '{job_data.name}': CPU-only launcher, vram_estimate=0")

    # ─── CPU-only override: FASTQ-only nanopore jobs don't need a GPU ─────
    if job_data.model_id == "nanopore" and isinstance(job_data.params, dict):
        has_pod5 = bool((job_data.params.get("pod5_dir") or "").strip())
        has_bam = bool((job_data.params.get("bam_path") or "").strip())
        has_fastq = bool((job_data.params.get("fastq_path") or "").strip())
        if has_fastq and not has_pod5 and not has_bam:
            vram_estimate = 0
            job_data.pinned_gpu = None
            logger.info(f"[QUEUE] FASTQ-only nanopore job '{job_data.name}': CPU-only, vram_estimate=0")
    
    # Generate batch_id if creating multiple jobs
    batch_id = str(uuid.uuid4()) if num_jobs > 1 else None
    batch_name = job_data.name if num_jobs > 1 else None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH JOB: For multi-sequence jobs, create MSA job first
    # ═══════════════════════════════════════════════════════════════════════════
    msa_job = None
    needs_msa = False
    sequences_for_msa = []
    
    # Logging MSA batch intent

    
    # Mutagenesis: generate per-variant MSAs when using MSA
    if mutagenesis_variants and num_jobs > 1:
        use_msa = job_data.params.get('boltz_use_msa', True) or job_data.params.get('rf3_use_msa', False)
        if use_msa:
            sequences_for_msa = []
            for idx, variant in enumerate(mutagenesis_variants):
                seq = variant.get('sequence')
                if not seq:
                    continue
                name = variant.get('name', f'var_{idx + 1}')
                sequences_for_msa.append({'name': name, 'sequence': seq})
            # Always regenerate MSAs for mutagenesis variants
            job_data.params['msa_force_refresh'] = True
            # Ensure no shared reference MSA is used
            job_data.params.pop('msa_reference_sequence', None)
            needs_msa = len(sequences_for_msa) > 0
            if needs_msa:
                logger.info(f"[MSA BATCH] Mutagenesis mode: {len(sequences_for_msa)} per-variant MSAs")
    # Legacy: single reference MSA for multi-sequence batches
    if not needs_msa and job_data.params.get('msa_reference_sequence') and num_jobs > 1:
        needs_msa = True
        sequences_for_msa = [{
            'name': 'reference_msa',
            'sequence': job_data.params['msa_reference_sequence']
        }]
        logger.info(f"[MSA BATCH] Reference MSA for {num_jobs} variants")
    elif num_jobs > 1 and 'sequence_input' in job_data.params:
        # Multiple inference jobs with potentially different sequences
        # For now, skip MSA batching if all use same sequence (normal parallel runs)
        pass
    elif 'boltz_use_msa' in job_data.params and job_data.params.get('boltz_use_msa', True):
        # Single job that needs MSA - handled by normal flow (no separate MSA job)
        pass
    
    if needs_msa and sequences_for_msa:
        import json as json_lib
        msa_job_id = str(uuid.uuid4())
        msa_output_dir = str(Path(base_output_dir) / "msa_batch")
        os.makedirs(msa_output_dir, exist_ok=True)
        
        msa_job = Job(
            id=msa_job_id,
            name=f"{job_data.name}_msa",
            model_id='msa_batch',
            mode='msa_generation',
            params={
                'sequences': sequences_for_msa,
                'sequences_json': json_lib.dumps(sequences_for_msa),
                'reference_sequence': job_data.params.get('msa_reference_sequence'),
                'msa_force_refresh': job_data.params.get('msa_force_refresh', False),
                'msa_cache_only': job_data.params.get('msa_cache_only', False),
                'msa_use_gpu': job_data.params.get('msa_use_gpu', True),
                'msa_max_seqs': job_data.params.get('msa_max_seqs'),
                'msa_preset': job_data.params.get('msa_preset', 'fast'),
                'msa_use_expand': job_data.params.get('msa_use_expand'),
                'msa_use_env': job_data.params.get('msa_use_env'),
                'msa_num_iterations': job_data.params.get('msa_num_iterations'),
                'msa_evalue': job_data.params.get('msa_evalue'),
                'msa_min_seq_id': job_data.params.get('msa_min_seq_id'),
                'msa_min_coverage': job_data.params.get('msa_min_coverage'),
                'msa_taxon_list': job_data.params.get('msa_taxon_list'),
                'msa_min_depth_warning': job_data.params.get('msa_min_depth_warning'),
                'msa_min_depth_fail': job_data.params.get('msa_min_depth_fail'),
                'msa_gpu_mode': job_data.params.get('msa_gpu_mode'),
                'msa_gpu_threshold': job_data.params.get('msa_gpu_threshold'),
                'msa_preferred_gpus': job_data.params.get('msa_preferred_gpus'),
                'msa_excluded_gpus': job_data.params.get('msa_excluded_gpus'),
                'msa_gpu_server_mode': job_data.params.get('msa_gpu_server_mode'),
                'msa_gpu_server_wait_timeout': job_data.params.get('msa_gpu_server_wait_timeout'),
                'msa_gpu_server_db_load_mode': job_data.params.get('msa_gpu_server_db_load_mode'),
                'msa_gpu_server_startup_wait': job_data.params.get('msa_gpu_server_startup_wait'),
                # BATCH-STAGE-GATE: Store FrustraMPNN flag for post-batch execution
                'run_frustrampnn_batch': job_data.params.get('run_frustrampnn', False),
            },
            output_dir=msa_output_dir,
            status=JobStatus.QUEUED.value,
            batch_id=batch_id,
            batch_name=batch_name,
            queue_status='queued',
            vram_estimate_mb=3000,  # MSA uses ~3GB VRAM
            sequence_length=len(sequences_for_msa[0]['sequence']) if sequences_for_msa else 300,
            priority=10,  # HIGH priority - unblocks inference jobs
            job_phase='msa_generation',
            msa_sequences=sequences_for_msa,
        )
        session.add(msa_job)
        logger.info(f"[MSA BATCH] Created MSA batch job {msa_job_id[:8]}... for {len(sequences_for_msa)} sequences")
    
    logger.info(f"[QUEUE] Creating {num_jobs} job(s) for '{job_data.name}': model={job_data.model_id}, seq_len={sequence_length}, vram_est={vram_estimate}MB")
    
    created_jobs = []
    first_job = None
    
    for i in range(num_jobs):
        job_id = str(uuid.uuid4())
        
        # For multiple jobs: use sim_1, sim_2, etc. subdirectories (or variant names for mutagenesis)
        if mutagenesis_variants and i < len(mutagenesis_variants):
            # Mutagenesis mode: use variant name and sequence
            variant = mutagenesis_variants[i]
            job_name = f"{job_data.name}_{variant.get('name', f'var_{i+1}')}"
            output_dir = str(Path(base_output_dir) / variant.get('name', f'var_{i+1}'))
            # Override sequence with variant-specific sequence
            job_params = {**job_data.params}
            job_params['sequence'] = variant.get('sequence')
            job_params['sequence_name'] = variant.get('name', f'var_{i+1}')
            job_params['mutation_variant'] = {
                'name': variant.get('name'),
                'source_design_id': variant.get('source_design_id'),
                'source_design_name': variant.get('source_design_name'),
                'binder_chain_id': variant.get('binder_chain_id'),
                'mutation': variant.get('mutation'),
                'loop_ids': variant.get('loop_ids'),
                'locked_positions_spec': variant.get('locked_positions_spec'),
            }
            
            # BATCH-STAGE-GATE: Remove per-variant FrustraMPNN
            # FrustraMPNN runs as a post-batch phase after ALL variants complete
            # This prevents GPU contention and enables single-model-load optimization
            job_params.pop('run_frustrampnn', None)
            
            variant_complex_components = variant.get('complex_components')
            # Construct complex_components for BoltzFromComplex if any non-protein components present
            # The ligands array contains ALL complex components: ligands, ions, DNA, RNA, peptides
            ligand_components = job_params.pop('ligands', [])
            
            if variant_complex_components:
                job_params['complex_components'] = variant_complex_components
                logger.info(
                    f"[MUTAGENESIS] Using variant-specific complex_components with "
                    f"{len(variant_complex_components)} entries for variant {variant.get('name')}"
                )
            # Check if any components need the complex workflow (DNA, RNA, ligands, ions, peptides)
            elif ligand_components:
                # Build complex_components array: protein + all other components
                complex_comps = [
                    {'type': 'protein', 'id': 'A', 'sequence': variant.get('sequence')}
                ]
                # Add all components from ligands array (DNA, RNA, ligands, ions, peptides)
                for comp in ligand_components:
                    comp_type = comp.get('type', 'ligand')
                    comp_entry = {
                        'type': comp_type,
                        'id': comp.get('id', 'X'),
                    }
                    # Add sequence for nucleic acids and peptides
                    if comp_type in ('dna', 'rna', 'peptide', 'protein') and comp.get('sequence'):
                        comp_entry['sequence'] = comp.get('sequence')
                    # Add CCD for standard ligands/ions
                    if comp.get('ccd'):
                        comp_entry['ccd'] = comp.get('ccd')
                    # Add SMILES for custom ligands
                    if comp.get('smiles'):
                        comp_entry['smiles'] = comp.get('smiles')
                    complex_comps.append(comp_entry)
                
                job_params['complex_components'] = complex_comps
                logger.info(f"[MUTAGENESIS] Built complex_components with {len(complex_comps)} entries for variant {variant.get('name')}")
        elif num_jobs > 1:
            job_name = f"{job_data.name}_sim{i+1}"
            output_dir = str(Path(base_output_dir) / f"sim_{i+1}")
            job_params = job_data.params
        else:
            job_name = job_data.name
            output_dir = base_output_dir
            job_params = job_data.params

        if msa_job:
            sequence_for_hash = str(job_params.get('sequence') or job_params.get('sequence_input') or '')
            reference_for_hash = str(job_data.params.get('msa_reference_sequence') or '')
            hash_source = reference_for_hash or sequence_for_hash
            if hash_source:
                job_params = {
                    **job_params,
                    'msa_sequence_hash': hashlib.sha256(hash_source.encode()).hexdigest(),
                }

        os.makedirs(output_dir, exist_ok=True)
        
        # Determine queue status: if MSA job exists, this job waits for it
        initial_queue_status = 'pending_msa' if msa_job else 'queued'
        parent_msa_id = msa_job.id if msa_job else None
        
        # Use parent_job_id from request (spawn-wait-collect) or MSA parent
        effective_parent_id = job_data.parent_job_id or parent_msa_id
        
        # Use batch_id/batch_name from request if provided (child jobs), else use local values
        effective_batch_id = job_data.batch_id or batch_id
        effective_batch_name = job_data.batch_name or batch_name
        
        # Use sequence_length from request if provided (child jobs)
        effective_seq_length = job_data.sequence_length or sequence_length
        
        # Create job record with queue fields
        job = Job(
            id=job_id,
            name=job_name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_params,  # Variant-specific params for mutagenesis
            output_dir=output_dir,
            status=JobStatus.QUEUED.value,
            # Batch grouping for job sets
            batch_id=effective_batch_id,
            batch_name=effective_batch_name,
            # GPU Orchestrator fields
            queue_status=initial_queue_status,
            vram_estimate_mb=vram_estimate,
            sequence_length=effective_seq_length,
            pinned_gpu=job_data.pinned_gpu,  # User-specified GPU pin from frontend
            priority=0,  # Default priority
            paused=False,
            retry_count=0,
            max_retries=2,
            oom_tolerance='allow',
            # Parent-child linking (spawn-wait-collect or MSA)
            parent_job_id=effective_parent_id,
            child_stage=job_data.child_stage,  # Stage identifier for filtering
            job_phase='inference',
        )
        session.add(job)
        created_jobs.append(job)
        
        if first_job is None:
            first_job = job
    
    await session.commit()
    
    # Refresh first job for response
    await session.refresh(first_job)
    
    if num_jobs > 1:
        logger.info(f"[BATCH] Created batch {batch_id[:8]}... with {num_jobs} jobs: {[j.name for j in created_jobs]}")
    
    # NOTE: Jobs are now launched by the GPU Orchestrator, not directly here
    # The orchestrator polls for queued jobs and assigns them to GPUs
    # based on VRAM availability and bin-packing algorithm.
    # Each job in the batch gets its own GPU assignment.
    
    return JobResponse(
        id=first_job.id,
        name=first_job.name,
        status=first_job.status,
        model_id=first_job.model_id,
        mode=first_job.mode,
        params=first_job.params,
        created_at=first_job.created_at,
        started_at=first_job.started_at,
        completed_at=first_job.completed_at,
        output_dir=first_job.output_dir,
        error_message=first_job.error_message,
        design_count=0,
        batch_id=first_job.batch_id,
        batch_name=first_job.batch_name,
        parent_job_id=first_job.parent_job_id,
        child_stage=first_job.child_stage,
        awaiting_input=first_job.awaiting_input,
        awaiting_stage=first_job.awaiting_stage,
        awaiting_payload=first_job.awaiting_payload,
        decision_history=first_job.decision_history,
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

    ordered_designs = [design_by_id[design_id] for design_id in design_ids]
    selection_dir = _materialize_antibody_selection(root_job, source_job, ordered_designs, request.action)
    action = request.action.strip().lower()
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
        )
    launch_selection_dir = str(launch_request.params.get("iteration_selection_dir") or selection_dir)
    launched_job = await create_job(launch_request, background_tasks, session)
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
    launch_request, variant_count, variant_note = _build_manual_mutagenesis_iteration_job(
        source_job=source_job,
        designs=ordered_designs,
        config=request.config,
        name_suffix=request.name_suffix,
        param_overrides=request.param_overrides,
    )
    launched_job = await create_job(launch_request, background_tasks, session)
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


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get details of a specific job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_changed = _repair_job_for_response(job)
    
    # Get design count
    design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
    design_count = (await session.execute(design_count_query)).scalar()
    review_count = _review_candidate_count(job)
    if (design_count or 0) == 0 and review_count is not None:
        design_count = review_count
    if (design_count or 0) == 0 and job.status in [JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value] and job.output_dir:
        design_count = count_structure_files(job.output_dir)
    completed_stages, stage_outputs = _resolve_stage_state_for_response(job)

    if job_changed:
        await session.commit()
    
    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=job.params,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=job.output_dir,
        error_message=job.error_message,
        design_count=design_count or 0,
        requested_design_count=_resolve_requested_design_count(job),
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        parent_job_id=job.parent_job_id,
        child_stage=job.child_stage,
        current_stage=job.current_stage,
        completed_stages=completed_stages,
        stage_outputs=stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
    )


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a running or queued job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in [JobStatus.QUEUED.value, JobStatus.RUNNING.value]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel job with status: {job.status}"
        )
    
    # Cancel the Nextflow process if running
    if job.nextflow_run_id:
        await cancel_nextflow_job(job.nextflow_run_id)
    
    job.status = JobStatus.CANCELLED.value
    job.completed_at = datetime.utcnow()
    await session.commit()
    
    return {"message": "Job cancelled", "job_id": job_id}


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
    import shutil
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel if running
    if job.status == JobStatus.RUNNING.value and job.nextflow_run_id:
        try:
            await cancel_nextflow_job(job.nextflow_run_id)
        except:
            pass  # Continue with deletion even if cancel fails
    
    job_name = job.name
    output_dir = job.output_dir
    
    # Delete designs associated with this job
    from database import Design
    await session.execute(
        Design.__table__.delete().where(Design.job_id == job_id)
    )
    
    # Delete child jobs first
    child_result = await session.execute(
        select(Job).where(Job.parent_job_id == job_id)
    )
    child_jobs = child_result.scalars().all()
    
    child_output_dirs = []
    for child in child_jobs:
        if child.output_dir:
            child_output_dirs.append(child.output_dir)
        # Delete child's designs
        await session.execute(
            Design.__table__.delete().where(Design.job_id == child.id)
        )
        await session.delete(child)
    
    # Delete the job itself
    await session.delete(job)
    await session.commit()
    
    # Delete output directories
    deleted_paths = []
    if output_dir:
        output_path = resolve_output_dir(output_dir)
        if output_path and output_path.exists():
            try:
                shutil.rmtree(output_path)
                deleted_paths.append(str(output_path))
            except Exception as e:
                print(f"Warning: Failed to delete output dir {output_path}: {e}")
    
    for child_dir in child_output_dirs:
        child_path = resolve_output_dir(child_dir)
        if child_path and child_path.exists():
            try:
                shutil.rmtree(child_path)
                deleted_paths.append(str(child_path))
            except Exception as e:
                print(f"Warning: Failed to delete child output dir {child_path}: {e}")
    
    return {
        "message": f"Job '{job_name}' permanently deleted",
        "job_id": job_id,
        "children_deleted": len(child_jobs),
        "directories_deleted": deleted_paths
    }


@router.post("/{job_id}/resubmit")
async def resubmit_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):

    """
    Resubmit a failed or cancelled job with the same parameters.
    Creates a new job with a fresh ID but copies all settings from the original.
    """
    # Find original job
    result = await session.execute(select(Job).where(Job.id == job_id))
    original_job = result.scalar_one_or_none()
    
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
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
    os.makedirs(output_dir, exist_ok=True)
    
    resubmit_params = deepcopy(original_job.params) if isinstance(original_job.params, dict) else {}
    resubmit_params = _normalize_nanopore_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_antibody_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_antibody_job_params(resubmit_params)
    if resubmit_params.get("msa_force_refresh") is True:
        # Resubmits should reuse cache by default unless user explicitly
        # starts a fresh job with force-refresh enabled.
        resubmit_params["msa_force_refresh"] = False
        logger.info(f"[RESUBMIT] Cleared msa_force_refresh for resubmitted job {job_id}")

    _validate_protenix_template_requirements(original_job.model_id, resubmit_params)
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

    new_job = Job(
        id=str(uuid.uuid4()),
        name=new_name,
        model_id=original_job.model_id,
        mode=original_job.mode,
        params=resubmit_params,
        status=JobStatus.QUEUED.value,
        created_at=datetime.utcnow(),
        output_dir=output_dir,
        # Preserve batch info if any
        batch_id=original_job.batch_id,
        batch_name=original_job.batch_name,
        # GPU Orchestrator fields - let orchestrator pick it up
        queue_status='queued',
        vram_estimate_mb=resubmit_vram_estimate,
        sequence_length=resubmit_sequence_length,
        priority=0,
        paused=False,
        retry_count=0,
        max_retries=2,
    )

    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)
    
    # No need to manually queue - GPU orchestrator picks up jobs with queue_status='queued'
    
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
    
    async def delete_with_retry(job_id_to_delete: str, retries: int = 3) -> int:
        for attempt in range(1, retries + 1):
            try:
                existing_count = (await session.execute(
                    select(func.count(Design.id)).where(Design.job_id == job_id_to_delete)
                )).scalar()
                await session.execute(delete(Design).where(Design.job_id == job_id_to_delete))
                await session.commit()
                return existing_count or 0
            except OperationalError as e:
                await session.rollback()
                if "locked" in str(e).lower() and attempt < retries:
                    logger.warning(f"[REINGEST] DB locked, retrying delete ({attempt}/{retries}) for {job_id_to_delete}")
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise

    try:
        # Fetch job
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Build job list (parent + children)
        job_ids = [job_id]
        jobs_to_ingest = {job_id: job.output_dir}
        if include_children:
            child_result = await session.execute(select(Job).where(Job.parent_job_id == job_id))
            child_jobs = child_result.scalars().all()
            for child in child_jobs:
                job_ids.append(child.id)
                jobs_to_ingest[child.id] = child.output_dir
        
        total_deleted = 0
        total_created = 0
        
        for jid in job_ids:
            output_dir = jobs_to_ingest.get(jid)
            if not output_dir:
                logger.warning(f"[REINGEST] Skipping job {jid}: no output_dir")
                continue
            
            deleted_count = await delete_with_retry(jid)
            total_deleted += deleted_count
            
            try:
                new_count = await ingest_job_results(jid, output_dir, session)
                total_created += new_count
                logger.info(f"[REINGEST] Re-ingested {new_count} designs for job {jid}")
            except Exception as e:
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
    asyncio.create_task(
        annotate_and_update_designs(pdb_paths, design_ids, job_id=str(job_id))
    )
    
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

@router.post("/{job_id}/stage-complete")
async def report_stage_complete(
    job_id: str,
    stage: str,
    outputs: List[str] = [],
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has completed.
    Called by Nextflow workflows after each stage finishes.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Update completed stages
    completed = job.completed_stages or []
    if stage not in completed:
        completed.append(stage)
    job.completed_stages = completed
    
    # Update stage outputs
    stage_outputs = job.stage_outputs or {}
    stage_outputs[stage] = outputs
    job.stage_outputs = stage_outputs
    
    # Clear current stage (will be set when next stage starts)
    job.current_stage = None
    
    await session.commit()
    
    logger.info(f"Job {job_id}: Stage '{stage}' completed with {len(outputs)} outputs")
    
    return {
        "message": f"Stage '{stage}' marked complete",
        "job_id": job_id,
        "completed_stages": completed,
        "outputs_count": len(outputs)
    }


@router.post("/{job_id}/stage-gates/{stage}/open")
async def open_stage_gate(
    job_id: str,
    stage: str,
    request: Optional[OpenStageGateRequest] = Body(default=None),
    session: AsyncSession = Depends(get_session)
):
    """Mark a job as awaiting user input at a named workflow gate."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = dict(request.payload or {}) if request else {}
    payload["stage"] = stage
    payload = refresh_gate_payload(payload, job.output_dir)

    job.awaiting_input = True
    job.awaiting_stage = stage
    job.awaiting_payload = payload
    job.current_stage = stage
    await session.commit()

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

    job_changed = _repair_job_for_response(job)
    if job_changed:
        await session.commit()

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
    """Persist a named frozen review dataset for paused review workflows."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = dict(job.awaiting_payload or {})
    existing_sets = payload.get("review_filter_sets")
    filter_sets = list(existing_sets) if isinstance(existing_sets, list) else []

    filter_name = str(request.name or "").strip() or f"Saved dataset {len(filter_sets) + 1}"
    saved_entry = {
        "id": str(uuid.uuid4()),
        "name": filter_name,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "visible_count": request.visible_count,
        "source_total_count": request.source_total_count,
        "design_ids": _normalize_design_ids(request.design_ids),
        "filter_state": dict(request.filter_state or {}),
    }
    filter_sets.insert(0, saved_entry)
    payload["review_filter_sets"] = filter_sets[:50]
    job.awaiting_payload = refresh_gate_payload(payload, job.output_dir)
    await session.commit()

    saved_models = [SavedReviewFilterSet.model_validate(entry) for entry in payload["review_filter_sets"]]
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
    """Remove a saved review dataset from a paused parent job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = dict(job.awaiting_payload or {})
    existing_sets = payload.get("review_filter_sets")
    filter_sets = list(existing_sets) if isinstance(existing_sets, list) else []
    next_sets = [entry for entry in filter_sets if str(entry.get("id") or "") != filter_set_id]
    if len(next_sets) == len(filter_sets):
        raise HTTPException(status_code=404, detail="Saved review dataset not found")

    payload["review_filter_sets"] = next_sets
    job.awaiting_payload = refresh_gate_payload(payload, job.output_dir)
    await session.commit()

    return DeleteReviewFilterSetResponse(
        message="Deleted saved review dataset.",
        filter_sets=[SavedReviewFilterSet.model_validate(entry) for entry in next_sets],
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
    # Prefer exact parent matches. Only fall back to batch_name for resume cases
    # where the current parent has no children of its own yet.
    query = select(Job).where(Job.parent_job_id == parent_id)
    if stage:
        query = query.where(Job.child_stage == stage)

    result = await session.execute(query)
    children = result.scalars().all()

    if not children and batch_name:
        fallback_query = select(Job).where(Job.batch_name == batch_name)
        if stage:
            fallback_query = fallback_query.where(Job.child_stage == stage)
        fallback_result = await session.execute(fallback_query)
        children = fallback_result.scalars().all()

    reconciled = _reconcile_child_jobs_from_history(children)
    if reconciled:
        await session.commit()

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

    # Guard against duplicate records and make resume behavior deterministic.
    child_map = {child.id: child for child in children}
    deduped_children = list(child_map.values())

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
                "status": c.status,
                "output_dir": c.child_output_dir or c.output_dir,
                "aggregated_by_parent": bool(c.aggregated_by_parent),
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

    if batch_name:
        query = select(Job).where(
            or_(
                Job.parent_job_id == parent_id,
                Job.batch_name == batch_name
            ),
            Job.status == "completed",
            Job.aggregated_by_parent == False
        )
    else:
        query = select(Job).where(
            Job.parent_job_id == parent_id,
            Job.status == "completed",
            Job.aggregated_by_parent == False
        )
    
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


@router.post("/{job_id}/stage-start")
async def report_stage_start(
    job_id: str,
    stage: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has started.
    Called by Nextflow workflows when entering a new stage.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.current_stage = stage
    await session.commit()
    
    logger.info(f"Job {job_id}: Stage '{stage}' started")
    
    return {"message": f"Stage '{stage}' started", "job_id": job_id}


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
    
    display_stages = []
    
    if job.mode in ["antibody_denovo", "antibody_denovo_pipeline"]:
        # Dynamic stage construction for antibody workflow
        display_stages.append("rfantibody")
        
        # Check params for sequence design steps (default to true if not present, matching nextflow logic)
        params = _normalize_antibody_job_params(job.params or {})
        
        # Note: In nextflow 'null' means true for these flags due to how they are processed
        run_fampnn = params.get("seq_design_fampnn")
        if run_fampnn is None or run_fampnn is True:
            display_stages.append("fampnn")
            
        run_antifold = params.get("seq_design_antifold")
        if run_antifold is None or run_antifold is True:
            display_stages.append("antifold")
            
        run_proteinmpnn = params.get("seq_design_proteinmpnn")
        if run_proteinmpnn is None or run_proteinmpnn is True:
            display_stages.append("proteinmpnn")

        if params.get("run_maturation") is True:
            display_stages.append("maturation")
            
        # Validation stages
        if params.get("run_structure_validation") is not False:
            display_stages.append("structure_validation")

        if params.get("run_post_validation_maturation") is True:
            display_stages.append("maturation_post_validation")
            
        if params.get("run_immunogenicity_scoring") is not False:
             display_stages.append("antiberty")
             
        if params.get("run_thermompnn") is not False:
             display_stages.append("thermompnn")
             
    else:
        # Nanopore stage list is dynamic based on params.
        if job.mode in ["methylation_analysis", "nanopore_methylation"]:
            np_params = job.params or {}
            display_stages = []
            has_pod5 = _is_meaningful_param_value(np_params.get("pod5_dir"))
            has_bam = _is_meaningful_param_value(np_params.get("bam_path"))
            has_fastq = _is_meaningful_param_value(np_params.get("fastq_path"))
            has_reference = _is_meaningful_param_value(np_params.get("reference_fasta"))
            fastq_qc_enabled, legacy_multimer_mode = _resolve_nanopore_fastq_qc_mode(np_params)
            bam_force_realign = _resolve_nanopore_bam_realign(np_params)

            if has_pod5:
                display_stages.append("dorado_basecall")

            if has_pod5 and has_reference:
                display_stages.append("dorado_align")

            if has_bam and has_reference:
                if bam_force_realign:
                    display_stages.append("dorado_align")
                else:
                    display_stages.append("bam_prepare")

            if (has_bam and not has_reference) or (has_pod5 and not has_reference):
                display_stages.append("bam_prepare")

            if has_fastq and has_reference:
                display_stages.append("fastq_align")
            if fastq_qc_enabled and has_fastq and has_reference and not legacy_multimer_mode:
                display_stages.append("fastq_qc")

            # Modkit only for POD5/BAM — FASTQ lacks methylation tags (MM/ML)
            if np_params.get("run_modkit") is not False and (has_pod5 or has_bam):
                display_stages.append("modkit")

            # Legacy multimer/dimer stage labels for old runs.
            if fastq_qc_enabled and legacy_multimer_mode and has_fastq:
                display_stages.append("multimer_qc")
            if fastq_qc_enabled and legacy_multimer_mode and has_fastq and has_reference:
                display_stages.append("dimer_analysis")

            if np_params.get("run_assembly") is True and (has_pod5 or has_bam):
                display_stages.append("wf_clone_validation")
        else:
            # Fallback for other modes
            all_stages_map = {
                "binder_denovo": ["rfdiffusion", "proteinmpnn", "boltz2"],
                "monomer_denovo": ["rfdiffusion", "proteinmpnn", "af2"],
                "oligo_design": ["rfdpoly", "nampnn"],
            }
            display_stages = all_stages_map.get(job.mode, [])

    all_stages = _dedupe_preserve_order(display_stages)
    completed = _dedupe_preserve_order(list(job.completed_stages or []))
    stage_outputs = dict(job.stage_outputs or {})
    if job.awaiting_input and job.awaiting_stage and job.awaiting_stage not in all_stages:
        all_stages.append(job.awaiting_stage)

    if job.mode in ["methylation_analysis", "nanopore_methylation"]:
        stage_outputs = _sanitize_nanopore_stage_outputs(stage_outputs, job.output_dir)
        # Merge filesystem-derived outputs so UI remains useful even when stage-report calls fail.
        inferred_outputs = _infer_nanopore_stage_outputs(job.output_dir, job.params)
        for stage, outputs in inferred_outputs.items():
            existing = stage_outputs.get(stage)
            if isinstance(existing, list):
                merged = _dedupe_preserve_order([*existing, *outputs])
            else:
                merged = outputs
            stage_outputs[stage] = merged
            if merged and stage not in completed:
                completed.append(stage)

        # If pipeline exited successfully, remaining planned stages are considered complete.
        if job.status == JobStatus.COMPLETED.value:
            completed = _dedupe_preserve_order([*completed, *all_stages])

    completed, stage_outputs = infer_antibody_stage_state(job, completed, stage_outputs)

    return {
        "job_id": job_id,
        "mode": job.mode,
        "all_stages": all_stages,
        "current_stage": job.awaiting_stage if job.awaiting_input and job.awaiting_stage else job.current_stage,
        "completed_stages": completed,
        "stage_outputs": stage_outputs,
        # Allow resume if failed/cancelled, even if no stages fully completed (rely on cache)
        "can_resume": job.status in ["failed", "cancelled", JobStatus.AWAITING_INPUT.value] or bool(job.awaiting_input)
    }


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: str,
    from_stage: str = None,
    request: Optional[ResumeJobRequest] = Body(default=None),
    session: AsyncSession = Depends(get_session)
):
    """
    Resume a failed job from a checkpoint.
    
    If from_stage is specified, it is recorded as a stage hint for cache-based
    resume behavior. The underlying Nextflow resume remains cache-driven.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ["failed", "cancelled", JobStatus.AWAITING_INPUT.value] and not job.awaiting_input:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume job with status: {job.status}"
        )
    
    completed = job.completed_stages or []
    # Relaxed restriction: Allow resume even if no stages completed (start from scratch with cache)

    # Allow body payload to override query-provided from_stage.
    effective_from_stage = (request.from_stage if request and request.from_stage else from_stage)
    if isinstance(effective_from_stage, str):
        effective_from_stage = effective_from_stage.strip() or None
    requested_overrides = dict(request.param_overrides) if request else {}
    requested_name_suffix = request.name_suffix if request else None

    # Prevent callers from overriding resume control fields directly.
    reserved_resume_keys = {"resume_job_id", "resume_work_dir", "resume_source_dir"}
    param_overrides = {
        key: value
        for key, value in requested_overrides.items()
        if key not in reserved_resume_keys
    }

    if job.awaiting_input:
        awaiting_payload = dict(job.awaiting_payload or {})
        candidate_dir = awaiting_payload.get("candidate_dir")
        if candidate_dir and job.awaiting_stage == "post_rfantibody":
            param_overrides.setdefault("rfantibody_input_pdbs", candidate_dir)
        if candidate_dir and job.awaiting_stage == "post_fampnn":
            param_overrides.setdefault("fampnn_collected_pdbs", candidate_dir)
        param_overrides.setdefault("interactive_gate_continue", True)
        param_overrides.setdefault("interactive_swa", _to_bool((job.params or {}).get("interactive_swa")))
        param_overrides.setdefault("interactive_gating", _to_bool((job.params or {}).get("interactive_gating")))
        if not effective_from_stage and job.awaiting_stage == "post_rfantibody":
            effective_from_stage = "rfantibody"
        elif not effective_from_stage and job.awaiting_stage == "post_fampnn":
            effective_from_stage = "fampnn"
        elif not effective_from_stage and job.awaiting_stage == "post_structure_validation":
            effective_from_stage = "structure_validation"
    
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
    
    # Generate output directory for the resumed job
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = str(get_results_dir() / f"{new_name}_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)
    
    merged_params = {
        **_normalize_antibody_job_params(job.params or {}),
        **param_overrides,
    }
    merged_params = _normalize_antibody_runtime_paths(job.model_id, merged_params)
    merged_params = _normalize_antibody_job_params(merged_params)
    _validate_antibody_runtime_paths(job.model_id, merged_params)

    new_job = Job(
        id=new_job_id,
        name=new_name,
        status="queued",
        model_id=job.model_id,
        mode=job.mode,
        params={
            **merged_params,
            "resume_job_id": job_id,
            "resume_work_dir": resume_work_dir,
            "resume_source_dir": job.output_dir,  # For NXF_CACHE_DIR session isolation
            "resume_requested_stage": effective_from_stage,
            # We don't need manual stage skipping params because we use -resume
        },
        output_dir=output_dir,
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        # Don't copy completed_stages/stage_outputs - they will be re-populated
        # as the resumed workflow re-emits cached results
        completed_stages=[], 
        stage_outputs={},
        
        # GPU Orchestrator fields - critical for scheduling
        queue_status='queued',
        vram_estimate_mb=job.vram_estimate_mb,
        sequence_length=job.sequence_length,
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
    
    session.add(new_job)
    await session.commit()
    
    logger.info(
        f"Job {job_id} resumed as {new_job_id} using work dir '{resume_work_dir}'"
        + (f" (requested_stage_hint={effective_from_stage})" if effective_from_stage else "")
    )
    
    return {
        "message": f"Job resumed. Checking cache in '{resume_work_dir}'",
        "original_job_id": job_id,
        "new_job_id": new_job_id,
        "new_job_name": new_name,
        "resume_from_stage": effective_from_stage or "auto",
        "resume_stage_mode": "hint",
        "resume_stage_note": "Stage selection is advisory; cache hits determine exact task reuse.",
        "preserved_stages": [],
        "applied_overrides": sorted(param_overrides.keys())
    }


@router.get("/{job_id}/structure-files")
async def list_structure_files(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """List all PDB and CIF structure files for a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
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
    }
    
    # --- Step 1: Find the nextflow log for THIS job ---
    nf_log_content = None
    nf_log_candidates = []
    
    if job.output_dir:
        output_path = resolve_output_dir(job.output_dir)
        if output_path:
            nf_log_candidates.append(output_path / "nextflow.log")
            nf_log_candidates.append(output_path / ".nextflow.log")
    
    # Fallback: global .nextflow.log (may be from a different job)
    nf_log_candidates.append(CODE_ROOT / ".nextflow.log")
    
    for nf_path in nf_log_candidates:
        if nf_path and nf_path.exists():
            try:
                with open(nf_path, 'r') as f:
                    nf_log_content = f.read()
                    lines = nf_log_content.split('\n')
                    logs_data["nextflow_log"] = "\n".join(lines[-tail:])
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


def extract_error_from_logs(command_log: str = None, command_err: str = None) -> str:
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


@router.get("/{job_id}/docking-results")
async def get_docking_results(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get docking results for a completed DiffDock or Uni-Dock job.
    
    Returns list of pose files with scores, sorted by rank.
    Handles both DiffDock (SDF with confidence) and Uni-Dock (PDB with affinity).
    """
    import re
    import json
    from pathlib import Path
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        return {"sdfs": [], "message": "No output directory configured"}
    
    output_path = resolve_output_dir(job.output_dir)
    if not output_path:
        return {"sdfs": [], "message": "No output directory configured"}
    
    # Check both DiffDock and Uni-Dock directories
    diffdock_dir = output_path / "run" / "diffdock" / "results"
    unidock_dir = output_path / "run" / "unidock" / "filtered"
    
    sdfs = []
    engines_used = []
    
    # Parse DiffDock results
    if diffdock_dir.exists():
        engines_used.append('diffdock')
        for sdf_file in diffdock_dir.rglob("*.sdf"):
            # Parse confidence from filename like "rank1_confidence-1.92.sdf"
            confidence = None
            match = re.search(r'confidence(-?\d+\.?\d*)', sdf_file.name)
            if match:
                confidence = float(match.group(1))
            
            # Parse rank
            rank = None
            rank_match = re.search(r'rank(\d+)', sdf_file.name)
            if rank_match:
                rank = int(rank_match.group(1))
            
            sdfs.append({
                "engine": "diffdock",
                "name": sdf_file.name,
                "path": to_allowed_relative(sdf_file),
                "absolute_path": str(sdf_file),
                "confidence": confidence,
                "affinity": None,
                "rank": rank,
                "complex_name": sdf_file.parent.name
            })
    
    # Parse Uni-Dock results
    if unidock_dir.exists():
        engines_used.append('unidock')
        scores_file = unidock_dir / "scores.json"
        
        # Load scores if available
        score_map = {}
        if scores_file.exists():
            try:
                scores_data = json.loads(scores_file.read_text())
                for entry in scores_data:
                    score_map[entry['pdb_file']] = entry
            except Exception as e:
                print(f"Warning: Failed to parse Uni-Dock scores: {e}")
        
        for pdb_file in unidock_dir.glob("*.pdb"):
            entry = score_map.get(pdb_file.name, {})
            
            sdfs.append({
                "engine": "unidock",
                "name": pdb_file.name,
                "path": to_allowed_relative(pdb_file),
                "absolute_path": str(pdb_file),
                "confidence": None,
                "affinity": entry.get('affinity_kcal_mol'),
                "rank": entry.get('rank'),
                "ligand": entry.get('ligand'),
                "pose": entry.get('pose'),
            })
    
    if not sdfs:
        return {"sdfs": [], "message": "No docking results found"}
    
    # Sort by rank (handle None correctly)
    sdfs = sorted(sdfs, key=lambda x: x.get('rank') if x.get('rank') is not None else 999)
    
    return {
        "sdfs": sdfs,
        "total": len(sdfs),
        "engines": engines_used,
        "is_dual_mode": len(engines_used) == 2,
        "job_id": job_id,
        "output_dir": job.output_dir
    }


@router.get("/{job_id}/docking-results/{filename}")
async def get_sdf_content(
    job_id: str,
    filename: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get the content of a specific docking result file for 3D visualization.
    Handles both DiffDock SDF files and Uni-Dock PDB files.
    """
    from pathlib import Path
    from fastapi.responses import PlainTextResponse
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Security: validate filename
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    output_path = resolve_output_dir(job.output_dir)
    if not output_path:
        raise HTTPException(status_code=404, detail="No output directory configured")
    
    # Search both DiffDock and Uni-Dock directories
    diffdock_dir = output_path / "run" / "diffdock" / "results"
    unidock_dir = output_path / "run" / "unidock" / "filtered"
    
    found_files = []
    
    # Search DiffDock results
    if diffdock_dir.exists():
        found_files.extend(list(diffdock_dir.rglob(filename)))
    
    # Search Uni-Dock results
    if unidock_dir.exists():
        found_files.extend(list(unidock_dir.glob(filename)))
    
    if not found_files:
        raise HTTPException(status_code=404, detail="Docking result file not found")
    
    file_path = found_files[0]
    content = file_path.read_text()
    
    # Set appropriate media type based on file extension
    if file_path.suffix.lower() == ".sdf":
        media_type = "chemical/x-mdl-sdfile"
    elif file_path.suffix.lower() == ".pdb":
        media_type = "chemical/x-pdb"
    else:
        media_type = "text/plain"
    
    return PlainTextResponse(content, media_type=media_type)


@router.get("/{job_id}/protein-pdb")
async def get_protein_pdb(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get the protein PDB content for a docking job.
    Looks for PDB in inputs directory or from job params.
    """
    from pathlib import Path
    from fastapi.responses import PlainTextResponse
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Try multiple locations for the PDB file
    pdb_content = None
    
    # 1. Check job params for explicit protein_pdb path
    if job.params and job.params.get('protein_pdb'):
        try:
            pdb_path = resolve_allowed_path(str(job.params['protein_pdb']))
        except ValueError:
            pdb_path = Path(job.params['protein_pdb'])
            if not pdb_path.is_absolute():
                pdb_path = CODE_ROOT / pdb_path
        if pdb_path.exists():
            pdb_content = pdb_path.read_text()
    
    # 2. Check inputs directory in output_dir
    if not pdb_content and job.output_dir:
        output_path = resolve_output_dir(job.output_dir)
        inputs_dir = output_path / "inputs" if output_path else None
        if inputs_dir and inputs_dir.exists():
            pdb_files = list(inputs_dir.glob("*.pdb"))
            if pdb_files:
                pdb_content = pdb_files[0].read_text()
    
    # 3. Check diffdock prep directory
    if not pdb_content and job.output_dir:
        output_path = resolve_output_dir(job.output_dir)
        prep_dir = output_path / "run" / "diffdock" / "prep" if output_path else None
        if prep_dir and prep_dir.exists():
            pdb_files = list(prep_dir.rglob("*.pdb"))
            if pdb_files:
                pdb_content = pdb_files[0].read_text()
    
    if not pdb_content:
        raise HTTPException(status_code=404, detail="Protein PDB not found")
    
    return PlainTextResponse(pdb_content, media_type="chemical/x-pdb")


@router.get("/{job_id}/docking-comparison")
async def get_docking_comparison(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get docking comparison data for a dual-docking job.
    
    Returns comparison JSON with RMSD values, agreement status, and consensus poses.
    """
    import json
    from pathlib import Path
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output directory configured")
    
    # Look for comparison.json from dual docking
    output_path = resolve_output_dir(job.output_dir)
    comparison_file = output_path / "run" / "docking_comparison" / "comparison.json" if output_path else None
    
    if not comparison_file or not comparison_file.exists():
        raise HTTPException(status_code=404, detail="No comparison data found for this job")
    
    try:
        comparison_data = json.loads(comparison_file.read_text())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse comparison data: {e}")
    
    return {
        "comparison": comparison_data,
        "job_id": job_id
    }

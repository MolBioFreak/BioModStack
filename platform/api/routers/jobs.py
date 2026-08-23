"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.exc import OperationalError
from typing import Optional, List, Dict, Any, Callable, NoReturn, cast
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
from pydantic import BaseModel, Field, ValidationError, field_validator
from jsonschema.exceptions import SchemaError

logger = logging.getLogger(__name__)

from antibody_pipeline_contract import (
    ANTIBODY_REFINEMENT_PIPELINE,
    ANTIBODY_PIPELINE_CONTRACT_VERSION,
    BACKBONE_COMPLEX,
    SEQUENCE_DESIGNED_COMPLEX,
    infer_antibody_artifact_class_from_stage,
    infer_selected_input_artifact_class,
    is_antibody_pipeline_mode,
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import (
    current_launch_context_id,
    get_session,
    Job,
    Design,
    FrustraMPNNResult,
    RFD3LocalRedesignRequest,
    RFD3LocalRedesignCandidate,
    RFD3LocalRedesignArtifact,
)
from experiment_database import get_experiment_session
from experiment_models import ExperimentRunAttempt
from services.result_contracts import build_review_artifact_manifest, resolve_result_contract
from paths import (
    get_code_root,
    get_data_root,
    get_allowed_roots,
    get_inputs_dir,
    get_results_dir,
    get_work_dir,
    resolve_allowed_path,
    resolve_runtime_data_path,
    to_allowed_relative,
)
from runtime_policy import workflow_launch_block_detail, workflow_launches_allowed
from schemas import JobCreate, JobResponse, JobList, JobStatus
from services.job_control import cancel_job_lineage, reject_generic_md_lifecycle_control
from services import alignment_access, ont_submission_trust, stage_reporting, ont_ngs_contract
from services.ont_barcode_units import load_barcode_units
from services.md.chemistry_catalog import ChemistryCatalogError, ChemistryProfileSelectionError
from services.md.feature_gate import require_molecular_dynamics_feature
from services.md.launch_contract import MDLaunchError, materialize_md_job_spec, normalize_md_job_spec
from services.md.results import expected_analysis_implementation_sha256
from services.md.state import MdStateError, create_md_run, create_replica_attempt
from services.proteinbase_importer import import_proteinbase_bundle
from services.nextflow import normalize_plr_input_pdb_path, normalize_plr_structure_validators
from services.rfd3_local_redesign import (
    normalize_local_redesign_params,
    materialize_local_redesign_request,
    prepare_local_redesign_scheduler_params,
)
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    claim_launch_context,
    consume_launch_context,
    publish_launch_context_binding,
    release_launch_context_claim,
    resolve_launch_context,
    resolve_launch_context_for_display,
    validate_bound_job,
    validate_bound_job_request,
    workflow_pinned_gpu,
)
from scripts.rfd3_local_redesign.contract import ContractError, request_sha256
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    RequestedSettingsPayloadError,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)

from model_registry import get_registry
from services.stage_review import (
    REVIEWABLE_STAGES,
    gate_file_for_stage,
    has_stage_gate,
    infer_antibody_stage_state,
    load_review_gate_snapshot,
    nextflow_history_status,
    nextflow_history_status_for_run_dir,
    resolve_nextflow_run_dir,
    refresh_gate_payload,
)
from services.boltz_cp_shard_plans import (
    BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
    coerce_boltz_cp_shard_plan_id,
    get_boltz_cp_logical_size_cp,
    get_boltz_cp_shard_plan_catalog,
    infer_boltz_cp_shard_plan_id,
    largest_square_divisor as boltz_cp_largest_square_divisor,
)

router = APIRouter()

# Project root for resolving code-relative paths
CODE_ROOT = get_code_root()
DEFAULT_FAMPNN_CHECKPOINT = "fampnn_0_0.pt"
DEFAULT_PPIFLOW_CHECKPOINT = "nanobody"
UUID_SUFFIX_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
PRESERVED_GATE_PAYLOAD_KEYS = ("review_filter_sets",)
PPI_FLOW_STAGE_FLAG_KEYS = {
    "run_ppiflow_backbone_refine",
    "run_ppiflow_maturation",
    "run_maturation",
    "run_post_validation_maturation",
    "run_post_boltz_maturation",
}
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
ANTIBODY_ITERATION_ACTION_LABELS = {
    "validate_boltz2": "Boltz-2 validation",
    "validate_protenix": "Protenix validation",
    "ppiflow_backbone_refine": "PPIFlow backbone refinement",
    "ppiflow_maturation": "PPIFlow maturation",
    "fampnn_redesign": "FAMPNN redesign",
    "frustrampnn": "Frustration analysis",
    "ui_refinement": "refinement launch",
}


def _raise_if_workflow_launches_disabled(action: str) -> None:
    if workflow_launches_allowed():
        return
    raise HTTPException(status_code=409, detail=workflow_launch_block_detail(action))


def _raise_md_launch_http_error(exc: Exception) -> NoReturn:
    """Map expected MD launch failures to one typed, client-safe HTTP contract."""

    if isinstance(exc, MDLaunchError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if isinstance(exc, ChemistryProfileSelectionError):
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if isinstance(exc, ChemistryCatalogError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics launch service is temporarily unavailable.",
            },
        ) from exc
    if isinstance(exc, (OSError, json.JSONDecodeError, SchemaError)):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics launch service is temporarily unavailable.",
            },
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MD_JOB_CONTRACT_INVALID",
                "message": "The molecular-dynamics job contract is invalid.",
            },
        ) from exc
    raise exc


_MD_OUTPUT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MD_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MD_ANALYSIS_SIF_SHA256 = "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"
_MD_TERMINAL_STATES = {"completed", "failed", "cancelled"}


def _md_output_path_forbidden() -> MDLaunchError:
    return MDLaunchError(
        "MD_OUTPUT_PATH_FORBIDDEN",
        "The molecular-dynamics output path is not permitted.",
        status_code=403,
    )


def _prepare_md_output_dir(job_name: str, timestamp: str, preallocated_job_id: str | None = None) -> tuple[Path, bool]:
    """Create one contained MD output directory and report whether this call owns it."""

    safe_name = str(job_name or "").strip()
    if not _MD_OUTPUT_SLUG.fullmatch(safe_name):
        raise _md_output_path_forbidden()

    try:
        results_root = get_results_dir().expanduser().resolve()
        # Typed internal replays have a deterministic identity.  Their result
        # root must be a direct, canonical child of Development's results root.
        output_path = results_root / (preallocated_job_id or f"{safe_name}_{timestamp}")
        resolved_output = output_path.resolve()
        if output_path.is_symlink() or not resolved_output.is_relative_to(results_root):
            raise _md_output_path_forbidden()
        try:
            output_path.mkdir(parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            if output_path.is_symlink() or not output_path.is_dir():
                raise _md_output_path_forbidden()
            if preallocated_job_id is not None:
                raise MDLaunchError(
                    "MD_OUTPUT_COLLISION",
                    "The deterministic MD re-orchestration output root already exists without a durable replay receipt",
                    status_code=409,
                )
            created = False
        resolved_output = output_path.resolve()
        if not resolved_output.is_relative_to(results_root):
            raise _md_output_path_forbidden()
        return resolved_output, created
    except MDLaunchError:
        raise
    except (OSError, ValueError) as exc:
        raise _md_output_path_forbidden() from exc


def _cleanup_call_owned_md_output(output_dir: Path, *, created: bool) -> None:
    """Remove only empty MD contract directories owned by this failed call."""

    if not created:
        return
    for candidate in (output_dir / "inputs", output_dir):
        try:
            candidate.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _md_analysis_error(code: str, message: str, *, status_code: int = 422) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _md_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md_analysis_gpu_requested(job_data: JobCreate) -> bool:
    if job_data.pinned_gpu is not None:
        return True
    params = job_data.params if isinstance(job_data.params, dict) else {}
    if params.get("gpu_id") not in (None, ""):
        return True
    pinned_gpus = params.get("pinned_gpus")
    return pinned_gpus not in (None, "", [])


def _md_contained_file(root: Path, raw_path: Any, *, code: str, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise _md_analysis_error(code, f"{label} is required")
    candidate = Path(raw_path).expanduser()
    if candidate.is_symlink():
        raise _md_analysis_error(code, f"{label} must not be a symbolic link", status_code=409)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise _md_analysis_error(code, f"{label} is not contained by the MD parent", status_code=409) from exc
    if not resolved.is_file():
        raise _md_analysis_error(code, f"{label} is unavailable", status_code=409)
    return resolved


async def _validate_md_analysis_child(job_data: JobCreate, session: AsyncSession) -> None:
    """Bind one internal CPU analysis attempt to immutable completed parent dynamics."""

    if job_data.child_stage != "md_analysis" or not job_data.parent_job_id:
        raise _md_analysis_error(
            "MD_ANALYSIS_PARENT_REQUIRED",
            "An analysis child requires an MD parent and child_stage=md_analysis.",
        )
    parent = await session.get(Job, job_data.parent_job_id)
    if parent is None:
        raise _md_analysis_error("MD_ANALYSIS_PARENT_NOT_FOUND", "The MD analysis parent does not exist.", status_code=404)
    if parent.model_id != "molecular_dynamics" or parent.mode != "simulate":
        raise _md_analysis_error("MD_ANALYSIS_PARENT_INVALID", "The analysis parent is not an MD coordinator.")
    if str(parent.status or "").strip().lower() in _MD_TERMINAL_STATES:
        raise _md_analysis_error("MD_ANALYSIS_PARENT_TERMINAL", "The MD analysis parent is already terminal.", status_code=409)
    if not parent.output_dir:
        raise _md_analysis_error("MD_ANALYSIS_PARENT_INVALID", "The MD analysis parent has no result root.", status_code=409)

    parent_root = Path(parent.output_dir).expanduser().resolve()
    work_item_path = _md_contained_file(
        parent_root,
        job_data.params.get("md_analysis_work_item"),
        code="MD_ANALYSIS_WORK_ITEM_INVALID",
        label="The MD analysis work item",
    )
    try:
        work_item = json.loads(work_item_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _md_analysis_error("MD_ANALYSIS_WORK_ITEM_INVALID", "The MD analysis work item is invalid.") from exc
    if not isinstance(work_item, dict) or work_item.get("schema") != "bms.md.analysis-work-item.v1":
        raise _md_analysis_error("MD_ANALYSIS_WORK_ITEM_INVALID", "The MD analysis work item schema is invalid.")
    replica_index = work_item.get("replica_index")
    if isinstance(replica_index, bool) or not isinstance(replica_index, int) or replica_index < 0:
        raise _md_analysis_error("MD_ANALYSIS_WORK_ITEM_INVALID", "The MD analysis replica index is invalid.")
    if work_item.get("job_id") != parent.id:
        raise _md_analysis_error("MD_ANALYSIS_PARENT_MISMATCH", "The analysis work item belongs to another MD parent.")

    aggregate_path = parent_root / "manifest.json"
    try:
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _md_analysis_error("MD_ANALYSIS_REPLICAS_INCOMPLETE", "The completed MD aggregate manifest is unavailable.", status_code=409) from exc
    aggregate_replicas = aggregate.get("replicas") if isinstance(aggregate, dict) else None
    if (
        not isinstance(aggregate, dict)
        or aggregate.get("schema") != "bms.md.aggregate.v1"
        or aggregate.get("status") != "completed"
        or aggregate.get("job_id") != parent.id
        or not isinstance(aggregate_replicas, list)
        or not any(isinstance(item, dict) and item.get("replica_index") == replica_index for item in aggregate_replicas)
    ):
        raise _md_analysis_error("MD_ANALYSIS_REPLICAS_INCOMPLETE", "The MD replica aggregate is not complete.", status_code=409)

    expected_manifest = (parent_root / "replicas" / f"replica_{replica_index}" / "manifest.json").resolve()
    replica_manifest = _md_contained_file(
        parent_root,
        work_item.get("manifest"),
        code="MD_ANALYSIS_REPLICA_INVALID",
        label="The MD replica manifest",
    )
    if replica_manifest != expected_manifest:
        raise _md_analysis_error("MD_ANALYSIS_REPLICA_INVALID", "The MD replica manifest path is not canonical.", status_code=409)
    expected_sha256 = work_item.get("manifest_sha256")
    if not isinstance(expected_sha256, str) or not _MD_SHA256.fullmatch(expected_sha256) or _md_sha256(replica_manifest) != expected_sha256:
        raise _md_analysis_error("MD_ANALYSIS_REPLICA_CHECKSUM_MISMATCH", "The MD replica manifest checksum is invalid.", status_code=409)
    try:
        replica = json.loads(replica_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _md_analysis_error("MD_ANALYSIS_REPLICA_INVALID", "The MD replica manifest is invalid.", status_code=409) from exc
    if (
        not isinstance(replica, dict)
        or replica.get("schema") != "bms.md.run.v1"
        or replica.get("status") != "completed"
        or replica.get("job_id") != parent.id
        or replica.get("replica_index") != replica_index
        or not isinstance(replica.get("artifacts"), dict)
        or not replica["artifacts"]
    ):
        raise _md_analysis_error("MD_ANALYSIS_REPLICA_INVALID", "The MD replica is not complete.", status_code=409)
    for artifact in replica["artifacts"].values():
        if not isinstance(artifact, dict):
            raise _md_analysis_error("MD_ANALYSIS_REPLICA_INVALID", "An MD replica artifact record is invalid.", status_code=409)
        artifact_path = _md_contained_file(
            replica_manifest.parent,
            str(replica_manifest.parent / str(artifact.get("path") or "")),
            code="MD_ANALYSIS_REPLICA_INVALID",
            label="An MD replica artifact",
        )
        artifact_bytes = artifact.get("bytes")
        artifact_sha256 = artifact.get("sha256")
        if (
            isinstance(artifact_bytes, bool)
            or not isinstance(artifact_bytes, int)
            or artifact_bytes < 0
            or not isinstance(artifact_sha256, str)
            or not _MD_SHA256.fullmatch(artifact_sha256)
            or artifact_path.stat().st_size != artifact_bytes
            or _md_sha256(artifact_path) != artifact_sha256
        ):
            raise _md_analysis_error("MD_ANALYSIS_REPLICA_CHECKSUM_MISMATCH", "An MD replica artifact checksum is invalid.", status_code=409)

    runtime_sha256 = job_data.params.get("md_analysis_sif_sha256")
    if runtime_sha256 != _MD_ANALYSIS_SIF_SHA256:
        raise _md_analysis_error("MD_ANALYSIS_RUNTIME_INVALID", "The qualified MD analysis runtime identity is required.")
    job_data.params.update(
        {
            "md_replica_index": replica_index,
            "md_replica_manifest": str(replica_manifest),
            "md_replica_manifest_sha256": expected_sha256,
            "md_aggregate_manifest_sha256": _md_sha256(aggregate_path),
            "md_analysis_implementation_sha256": expected_analysis_implementation_sha256(),
        }
    )


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


def _coerce_nonempty_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _copy_present_params(source: Dict[str, Any], dest: Dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in source:
            dest[key] = source[key]


def _format_artifact_identity(artifact_class: Optional[str], stage_family: Optional[str], stage_mode: Optional[str]) -> str:
    parts: List[str] = []
    normalized_artifact_class = normalize_antibody_artifact_class(artifact_class)
    if normalized_artifact_class:
        parts.append(normalized_artifact_class)
    stage_identity = _format_stage_identity(stage_family, stage_mode)
    if stage_identity and stage_identity != "unknown":
        parts.append(stage_identity)
    return " from ".join(parts) if parts else "unknown"


def _child_job_has_reusable_outputs(job: Any) -> bool:
    child_stage = (_coerce_nonempty_text(getattr(job, "child_stage", None)) or "").strip().lower()
    if child_stage not in {"maturation", "backbone_refine"}:
        return True

    output_dir = _coerce_nonempty_text(getattr(job, "output_dir", None))
    if not output_dir:
        return False

    output_path = Path(output_dir).expanduser()
    if not output_path.exists():
        return False

    try:
        for pdb_path in output_path.rglob("*.pdb"):
            name = pdb_path.name
            if "ppiflow" not in name.lower():
                continue
            if name.endswith("_enriched_complex.pdb"):
                continue
            return True
    except OSError as exc:
        logger.warning("Failed to inspect child output dir %s for reusable outputs: %s", output_path, exc)
        return False

    return False


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


def _job_uses_child_batches(model_id: str, mode: str, params: dict) -> bool:
    if not isinstance(params, dict):
        return False

    parallel_mode = _coerce_nonempty_text(params.get("parallel_mode"))
    if parallel_mode and parallel_mode.lower() == "full_orchestrator":
        return True

    if is_antibody_pipeline_mode(mode):
        return True


    if model_id == "boltzgen" and bool(params.get("boltzgen_parallel_mode")):
        return True

    return False


def _ensure_job_resume_identity(
    *,
    job_name: str,
    job_id: str,
    model_id: str,
    mode: str,
    params: dict,
) -> dict:
    normalized = dict(params or {})
    normalized.setdefault("job_name", job_name)

    if not _job_uses_child_batches(model_id, mode, normalized):
        return normalized

    root_job_id = _coerce_nonempty_text(normalized.get("resume_root_job_id")) or job_id
    normalized["resume_root_job_id"] = root_job_id

    if not _coerce_nonempty_text(normalized.get("batch_name")):
        batch_prefix = _coerce_nonempty_text(normalized.get("job_name")) or job_name or "job"
        normalized["batch_name"] = f"{batch_prefix}_{root_job_id}"

    return normalized


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


class ResumeJobRequest(BaseModel):
    """Optional payload for resume calls that need runtime param overrides."""
    from_stage: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    name_suffix: Optional[str] = None


class ContinueProteinLocalReviewRequest(BaseModel):
    """Continue a paused protein local redesign review using a selected subset."""
    design_ids: List[str] = Field(default_factory=list)
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
            select(Job.id, Job.name, Job.output_dir, Job.child_output_dir)
        )
    ).all()

    refs: Dict[str, List[Dict[str, str]]] = {}
    for job_id, job_name, output_dir, child_output_dir in rows:
        if str(job_id) in excluded_job_ids:
            continue

        for field_name, raw_path in (
            ("output_dir", output_dir),
            ("child_output_dir", child_output_dir),
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

        refs = remaining_refs.get(normalized, [])
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


def _review_candidate_count(job: Job) -> Optional[int]:
    if not job.awaiting_input:
        return None
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return None
    payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
    if stage in {"post_fampnn", "post_boltzgen", "post_ppiflow_generator", "post_caliby"}:
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
    if stage in {"post_fampnn", "post_boltzgen", "post_ppiflow_generator", "post_caliby"}:
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
    if not normalized.get("antibody_chains") and normalized.get("binder_chains"):
        normalized["antibody_chains"] = str(normalized.get("binder_chains")).strip()
    if not normalized.get("antigen_chains") and normalized.get("target_chains"):
        normalized["antigen_chains"] = str(normalized.get("target_chains")).strip()
    if not normalized.get("antibody_chains") and normalized.get("antibody_chain"):
        normalized["antibody_chains"] = str(normalized.get("antibody_chain")).strip()
    if not normalized.get("antigen_chains") and normalized.get("antigen_chain"):
        normalized["antigen_chains"] = str(normalized.get("antigen_chain")).strip()

    structure_validator = str(
        normalized.get("structure_validator")
        or normalized.get("validation_predictor")
        or normalized.get("pred_method")
        or "boltz2"
    ).strip().lower()
    if structure_validator == "boltz":
        structure_validator = "boltz2"
    if structure_validator not in {"boltz2", "protenix", "esmfold2"}:
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
        _to_bool(normalized.get("run_ppiflow_backbone_refine"))
        or _to_bool(normalized.get("run_ppiflow_maturation"))
        or _to_bool(normalized.get("run_maturation"))
        or _to_bool(normalized.get("run_post_validation_maturation"))
        or _to_bool(normalized.get("run_post_boltz_maturation"))
    ):
        normalized["ppiflow_checkpoint"] = DEFAULT_PPIFLOW_CHECKPOINT

    gate_stage = normalized.get("interactive_gate_stage")
    if isinstance(gate_stage, str):
        normalized_gate_stage = gate_stage.strip().lower()
        if normalized_gate_stage == "post_boltz_validation":
            normalized_gate_stage = "post_structure_validation"
        if normalized_gate_stage not in {"post_rfantibody", "post_boltzgen", "post_ppiflow_generator", "post_fampnn", "post_caliby", "post_structure_validation"}:
            normalized_gate_stage = "post_fampnn"
        normalized["interactive_gate_stage"] = normalized_gate_stage

    if "rfantibody_screen_reference_scope" in normalized:
        screen_scope = str(normalized.get("rfantibody_screen_reference_scope") or "").strip().lower()
        if screen_scope in {"whole", "full", "framework", "framework_inclusive", "whole_antibody"}:
            normalized["rfantibody_screen_reference_scope"] = "whole_antibody"
        else:
            normalized["rfantibody_screen_reference_scope"] = "cdr_loops"

    if "ppiflow_stage_mode" in normalized:
        normalized["ppiflow_stage_mode"] = str(normalized.get("ppiflow_stage_mode") or "").strip().lower() or None
        if normalized["ppiflow_stage_mode"] in {"post_rfantibody", "backbone_refine", "post_ppiflow"}:
            normalized["run_ppiflow_backbone_refine"] = True
        elif normalized["ppiflow_stage_mode"] in {"post_fampnn", "maturation"}:
            normalized["run_ppiflow_maturation"] = True
            normalized["run_maturation"] = True
        elif normalized["ppiflow_stage_mode"] == "both":
            normalized["run_ppiflow_backbone_refine"] = True
            normalized["run_ppiflow_maturation"] = True
            normalized["run_maturation"] = True
        if normalized["ppiflow_stage_mode"] == "post_ppiflow":
            if normalized.get("ppiflow_require_anchors") in (None, ""):
                normalized["ppiflow_require_anchors"] = False
            if normalized.get("ppiflow_stage_target") in (None, ""):
                normalized["ppiflow_stage_target"] = "post_ppiflow"
    ppiflow_tuning_profile = _normalize_ppiflow_tuning_profile(normalized.get("ppiflow_tuning_profile"))
    if ppiflow_tuning_profile is not None:
        inferred_ppiflow_stage_mode = _infer_ppiflow_stage_mode_from_flags(normalized)
        if ppiflow_tuning_profile == "stage_optimized" and inferred_ppiflow_stage_mode == "both":
            ppiflow_tuning_profile = "manual"
        normalized["ppiflow_tuning_profile"] = ppiflow_tuning_profile
        if ppiflow_tuning_profile == "stage_optimized":
            normalized.update(_stage_optimized_ppiflow_defaults(inferred_ppiflow_stage_mode))
    if "ppiflow_stage_target" in normalized:
        normalized["ppiflow_stage_target"] = str(normalized.get("ppiflow_stage_target") or "").strip().lower() or None
    ppiflow_objective_mode = str(normalized.get("ppiflow_objective_mode") or "").strip().lower() or None
    if ppiflow_objective_mode not in {None, "selected_interface", "loop_target", "loop_epitope", "balanced"}:
        ppiflow_objective_mode = None
    ppiflow_stage_enabled = (
        _to_bool(normalized.get("run_ppiflow_backbone_refine"))
        or _to_bool(normalized.get("run_ppiflow_maturation"))
        or _to_bool(normalized.get("run_maturation"))
    )
    if ppiflow_stage_enabled and ppiflow_objective_mode is None:
        ppiflow_objective_mode = "balanced"
    if ppiflow_objective_mode is not None:
        normalized["ppiflow_objective_mode"] = ppiflow_objective_mode
    objective_threshold = normalized.get("ppiflow_objective_threshold")
    if objective_threshold in ("", None):
        if ppiflow_objective_mode and ppiflow_objective_mode != "selected_interface":
            normalized["ppiflow_objective_threshold"] = 0.0
    else:
        try:
            normalized["ppiflow_objective_threshold"] = float(objective_threshold)
        except (TypeError, ValueError):
            normalized.pop("ppiflow_objective_threshold", None)
    for key in ("ppiflow_backbone_region_mode", "ppiflow_maturation_region_mode", "ppiflow_region_mode"):
        if key in normalized:
            value = str(normalized.get(key) or "").strip().lower()
            if value in {"framework", "framework_only"}:
                value = "framework_only"
            elif value in {"all_antibody", "whole_antibody", "full_antibody"}:
                value = "all_antibody"
            elif value == "all_cdrs":
                value = "all_cdrs"
            else:
                value = "selected_cdrs"
            normalized[key] = value
    if "selected_loop_scope" in normalized:
        normalized["selected_loop_scope"] = _normalize_selected_loop_scope(normalized.get("selected_loop_scope"))

    selected_input_dir = _coerce_nonempty_text(normalized.get("selected_input_dir"))
    if not selected_input_dir:
        for key in ("iteration_selection_dir", "rfantibody_input_pdbs", "fampnn_collected_pdbs"):
            selected_input_dir = _coerce_nonempty_text(normalized.get(key))
            if selected_input_dir:
                break
    if selected_input_dir:
        normalized["selected_input_dir"] = selected_input_dir
        normalized.setdefault("iteration_selection_dir", selected_input_dir)
        selected_input_manifest = (
            _coerce_nonempty_text(normalized.get("selected_input_manifest"))
            or _coerce_nonempty_text(normalized.get("source_selection_manifest_path"))
            or str(_selection_manifest_path(Path(selected_input_dir).expanduser()))
        )
        normalized["selected_input_manifest"] = selected_input_manifest
        normalized["source_selection_manifest_path"] = selected_input_manifest

    if not _coerce_nonempty_text(normalized.get("selected_input_source_job_id")):
        selected_input_source_job_id = (
            _coerce_nonempty_text(normalized.get("source_stage_job_id"))
            or _coerce_nonempty_text(normalized.get("selection_source_job_id"))
            or _coerce_nonempty_text(normalized.get("iteration_source_job_id"))
        )
        if selected_input_source_job_id:
            normalized["selected_input_source_job_id"] = selected_input_source_job_id

    if not _coerce_nonempty_text(normalized.get("selected_input_stage_family")) and _coerce_nonempty_text(normalized.get("source_stage_family")):
        normalized["selected_input_stage_family"] = normalized.get("source_stage_family")
    if not _coerce_nonempty_text(normalized.get("selected_input_stage_mode")) and _coerce_nonempty_text(normalized.get("source_stage_mode")):
        normalized["selected_input_stage_mode"] = normalized.get("source_stage_mode")

    selected_input_artifact_class = infer_selected_input_artifact_class(
        selected_input_artifact_class=normalized.get("selected_input_artifact_class"),
        selected_input_stage_family=normalized.get("selected_input_stage_family"),
        selected_input_stage_mode=normalized.get("selected_input_stage_mode"),
        rfantibody_input_pdbs=normalized.get("rfantibody_input_pdbs"),
        fampnn_collected_pdbs=normalized.get("fampnn_collected_pdbs"),
    )
    selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        normalized.get("selected_input_schema_version")
    )
    if selected_input_artifact_class:
        normalized["selected_input_artifact_class"] = selected_input_artifact_class
        normalized["selected_input_schema_version"] = (
            selected_input_schema_version or ANTIBODY_PIPELINE_CONTRACT_VERSION
        )
    elif selected_input_schema_version is not None:
        normalized["selected_input_schema_version"] = selected_input_schema_version

    if _coerce_nonempty_text(normalized.get("selected_input_manifest")) and not _coerce_nonempty_text(normalized.get("source_selection_manifest_path")):
        normalized["source_selection_manifest_path"] = normalized.get("selected_input_manifest")

    if normalized.get("source_selection_count") in (None, "", 0):
        design_ids = normalized.get("iteration_source_design_ids")
        if isinstance(design_ids, list) and design_ids:
            normalized["source_selection_count"] = len(design_ids)

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
        or is_antibody_pipeline_mode(mode_normalized)
        or mode_normalized == "rfantibody_backbone"
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


def _normalize_structure_runtime_paths(model_id: str, params: dict) -> dict:
    if model_id not in {"protenix", "boltz2", "rf3"} or not isinstance(params, dict):
        return params

    normalized = dict(params)
    for key in ("target_pdb", "fixed_target_source_path"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _resolve_alias_path_for_runtime(value)
    return normalized


MIN_BOLTZ_NO_MSA_RECYCLING_STEPS = 3
MIN_BOLTZ_NO_MSA_SAMPLING_STEPS = 50
STRUCTURE_PREDICTION_COMPLEX_RF3_ERROR = "RF3 is predict-only and cannot be launched in complex mode."
BOLTZ_CP_STRUCTURE_LAUNCHER_INPUT_SENTINEL = "__boltz_cp_structure_launcher_input__"



def _largest_square_divisor(value_count: int, requested_size_cp: Optional[int] = None) -> int:
    return boltz_cp_largest_square_divisor(value_count, requested_size_cp)


def _get_boltz_cp_catalog_physical_gpu_count() -> int:
    """Return the discovered host GPU count for UI catalog previews without DALAB ordinal assumptions."""
    for env_key in ("BMS_BOLTZ_CP_CATALOG_GPU_COUNT", "BMS_MAX_PHYSICAL_GPUS"):
        raw_value = os.getenv(env_key)
        if raw_value in (None, ""):
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed

    try:
        from services.gpu_metadata import GPU_METADATA

        discovered_count = len(GPU_METADATA)
    except Exception as exc:
        logger.debug("Could not discover GPU count for Boltz-CP shard-plan catalog: %s", exc)
        discovered_count = 0
    return max(1, discovered_count)


@router.get("/boltz-cp/shard-plans")
def list_boltz_cp_shard_plans() -> Dict[str, Any]:
    return get_boltz_cp_shard_plan_catalog(max_physical_gpu_count=_get_boltz_cp_catalog_physical_gpu_count())


def _default_structure_prediction_pred_method(model_id: str) -> str:
    normalized_model_id = str(model_id or "").strip().lower()
    if normalized_model_id == "rf3":
        return "rf3"
    if normalized_model_id == "protenix":
        return "protenix"
    return "boltz"


def _normalize_structure_prediction_pred_method(
    model_id: str,
    mode: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {} if params is None else params

    normalized = dict(params)
    normalized_mode = str(mode or "").strip().lower()
    normalized_model_id = str(model_id or "").strip().lower()

    structure_modes = {"predict", "complex", "structure_prediction", "structure_validation"}
    structure_models = {"boltz2", "protenix", "rf3", "template_structure_prediction"}
    if normalized_mode not in structure_modes and normalized_model_id not in structure_models:
        return normalized

    requested_pred_method = str(normalized.get("pred_method") or "").strip().lower()
    if requested_pred_method == "boltz2":
        requested_pred_method = "boltz"
    if not requested_pred_method:
        requested_pred_method = _default_structure_prediction_pred_method(normalized_model_id)

    if normalized_mode == "complex":
        if requested_pred_method == "rf3" or normalized_model_id == "rf3":
            raise HTTPException(
                status_code=422,
                detail={"validation_errors": [STRUCTURE_PREDICTION_COMPLEX_RF3_ERROR]},
            )
        if requested_pred_method in {"both", "all", "boltz_protenix"}:
            normalized["pred_method"] = "boltz_protenix"
            return normalized
        if requested_pred_method == "protenix":
            normalized["pred_method"] = "protenix"
            return normalized
        normalized["pred_method"] = "boltz"
        return normalized

    normalized["pred_method"] = requested_pred_method
    return normalized


def _frustrampnn_param_error(
    field: str,
    message: str,
    *,
    error_type: str,
    suffix: tuple[str | int, ...] = (),
) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[
            {
                "type": error_type,
                "loc": ["body", "params", field, *suffix],
                "msg": message,
            }
        ],
    )


def _normalize_frustrampnn_settings(
    model_id: str,
    mode: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Normalize the global typed FrustraMPNN contract before Job persistence."""

    normalized = {} if params is None else dict(params)
    fields = {
        "run_frustrampnn",
        "frustrampnn_requiredness",
        "frustrampnn_settings",
        "frustrampnn_settings_value_origin",
    }
    if not fields.intersection(normalized):
        return normalized
    if "frustrampnn_settings_value_origin" in normalized:
        raise _frustrampnn_param_error(
            "frustrampnn_settings_value_origin",
            "frustrampnn_settings_value_origin is server-authored",
            error_type="value_error.frustrampnn_origin",
        )

    enabled = normalized.get("run_frustrampnn", False)
    if type(enabled) is not bool:
        raise _frustrampnn_param_error(
            "run_frustrampnn",
            "run_frustrampnn must be a boolean",
            error_type="bool_type",
        )
    normalized["run_frustrampnn"] = enabled
    requiredness = normalized.get("frustrampnn_requiredness", "required")
    if requiredness != "required":
        raise _frustrampnn_param_error(
            "frustrampnn_requiredness",
            "frustrampnn_requiredness must remain required",
            error_type="literal_error",
        )
    normalized["frustrampnn_requiredness"] = "required"
    supplied = "frustrampnn_settings" in normalized
    if not enabled:
        if supplied:
            raise _frustrampnn_param_error(
                "frustrampnn_settings",
                "frustrampnn_settings requires run_frustrampnn=true",
                error_type="value_error.frustrampnn_disabled",
            )
        return normalized
    if not supplied:
        settings = default_frustrampnn_settings()
    else:
        try:
            settings = validate_complete_requested_settings(
                normalized["frustrampnn_settings"]
            )
        except RequestedSettingsPayloadError as exc:
            raise _frustrampnn_param_error(
                "frustrampnn_settings",
                str(exc),
                error_type="value_error.frustrampnn_settings",
                suffix=exc.location,
            ) from exc
        except ValidationError as exc:
            error = exc.errors(include_url=False, include_context=False)[0]
            raise _frustrampnn_param_error(
                "frustrampnn_settings",
                str(error["msg"]),
                error_type=str(error["type"]),
                suffix=tuple(error["loc"]),
            ) from exc
    normalized["frustrampnn_settings"] = settings.model_dump(
        mode="json", exclude_none=False
    )
    return normalized


def _normalize_boltz_cp_params_for_validation(
    model_id: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if model_id != "boltz_cp_experimental" or not isinstance(params, dict):
        return {} if params is None else params

    normalized = dict(params)

    input_path = _coerce_nonempty_text(normalized.get("input_path")) or _coerce_nonempty_text(
        normalized.get("bcp_input_path")
    )
    if not input_path:
        has_structure_launcher_inputs = any(
            normalized.get(key)
            for key in (
                "sequence",
                "complex_components",
                "sequence_batch_entries",
                "fixed_target_source_path",
            )
        ) or normalized.get("structure_launch_variant") == "boltz_cp_experimental"
        if has_structure_launcher_inputs:
            input_path = BOLTZ_CP_STRUCTURE_LAUNCHER_INPUT_SENTINEL
    if input_path:
        normalized["input_path"] = input_path

    shard_plan_id = coerce_boltz_cp_shard_plan_id(
        normalized.get("shard_plan_id") or normalized.get("bcp_shard_plan_id")
    )
    if shard_plan_id is None:
        shard_plan_id = infer_boltz_cp_shard_plan_id(
            normalized.get("size_cp") or normalized.get("bcp_size_cp"),
            default=BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
        )
    if shard_plan_id:
        normalized["shard_plan_id"] = shard_plan_id

    gpu_ids = _coerce_nonempty_text(normalized.get("gpu_ids")) or _coerce_nonempty_text(
        normalized.get("bcp_gpu_ids")
    )
    if not gpu_ids and isinstance(normalized.get("pinned_gpus"), list) and normalized["pinned_gpus"]:
        gpu_ids = ",".join(str(gpu_id).strip() for gpu_id in normalized["pinned_gpus"] if str(gpu_id).strip())
    if gpu_ids:
        normalized["gpu_ids"] = gpu_ids

    alias_mappings = {
        "shard_plan_id": "bcp_shard_plan_id",
        "input_format": "bcp_input_format",
        "output_format": "bcp_output_format",
        "write_full_pae": "bcp_write_full_pae",
        "confidence_prediction": "bcp_confidence_prediction",
        "recycling_steps": "bcp_recycling_steps",
        "sampling_steps": "bcp_sampling_steps",
        "diffusion_samples": "bcp_diffusion_samples",
        "max_msa_seqs": "bcp_max_msa_seqs",
        "max_parallel_samples": "bcp_max_parallel_samples",
        "precision": "bcp_precision",
        "seed": "bcp_seed",
        "backend": "bcp_backend",
        "triattn_backend": "bcp_triattn_backend",
        "context_store_mode": "bcp_context_store_mode",
        "context_store_root": "bcp_context_store_root",
        "context_query_tile_tokens": "bcp_context_query_tile_tokens",
        "context_store_logical_size_cp": "bcp_context_store_logical_size_cp",
        "context_store_pair_tile_tokens": "bcp_context_store_pair_tile_tokens",
        "context_store_key_tile_tokens": "bcp_context_store_key_tile_tokens",
    }
    for canonical_key, alias_key in alias_mappings.items():
        if canonical_key not in normalized and alias_key in normalized:
            normalized[canonical_key] = normalized[alias_key]

    if "recycling_steps" not in normalized and "boltz_recycling_steps" in normalized:
        normalized["recycling_steps"] = normalized["boltz_recycling_steps"]
    if "sampling_steps" not in normalized and "boltz_sampling_steps" in normalized:
        normalized["sampling_steps"] = normalized["boltz_sampling_steps"]
    if "diffusion_samples" not in normalized and "boltz_num_samples" in normalized:
        normalized["diffusion_samples"] = normalized["boltz_num_samples"]

    return normalized


def _normalize_boltz_no_msa_quality_params(
    model_id: str,
    mode: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if model_id != "boltz2" or mode not in {"predict", "complex"} or not isinstance(params, dict):
        return {} if not isinstance(params, dict) else params

    normalized = dict(params)
    if "boltz_use_msa" not in normalized or _to_bool(normalized.get("boltz_use_msa")):
        return normalized

    sampling_steps = _coerce_positive_int(normalized.get("boltz_sampling_steps"))
    recycling_steps = _coerce_positive_int(normalized.get("boltz_recycling_steps"))

    if sampling_steps is not None and sampling_steps < MIN_BOLTZ_NO_MSA_SAMPLING_STEPS:
        normalized["boltz_sampling_steps"] = MIN_BOLTZ_NO_MSA_SAMPLING_STEPS
    if recycling_steps is not None and recycling_steps < MIN_BOLTZ_NO_MSA_RECYCLING_STEPS:
        normalized["boltz_recycling_steps"] = MIN_BOLTZ_NO_MSA_RECYCLING_STEPS

    return normalized


def _supports_colabfold_api_single_job(model_id: str, mode: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()

    if normalized_model == "boltz_cp_experimental":
        return normalized_mode == "design"

    return normalized_model in {"boltz2", "rf3", "protenix"} and normalized_mode in {"predict", "complex"}


def _default_msa_provider_for_job(model_id: str, mode: str) -> str:
    """Default supported structure jobs to the remote ColabFold service."""
    if _supports_colabfold_api_single_job(model_id, mode):
        return "colabfold_api"
    return "local"


def _normalize_target_geometry_mode(raw: Any) -> Optional[str]:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    aliases = {
        "anchor": "conditioned",
        "anchored": "conditioned",
        "template": "conditioned",
        "templated": "conditioned",
        "fixed": "frozen",
        "hard_fixed": "frozen",
        "hard_frozen": "frozen",
    }
    value = aliases.get(value, value)
    if value in {"flexible", "conditioned", "frozen"}:
        return value
    return None


def _normalize_structure_geometry_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return {}

    normalized = dict(params)
    global_mode = _normalize_target_geometry_mode(normalized.get("target_geometry_mode"))
    boltz_mode = _normalize_target_geometry_mode(
        normalized.get("boltz_target_geometry_mode") or global_mode
    )
    protenix_mode = _normalize_target_geometry_mode(
        normalized.get("protenix_target_geometry_mode") or global_mode
    )

    if boltz_mode is None and _to_bool(normalized.get("boltz_anchor_target")):
        boltz_mode = "conditioned"
    if protenix_mode is None and (
        _to_bool(normalized.get("protenix_anchor_target"))
        or _to_bool(normalized.get("protenix_use_template"))
    ):
        protenix_mode = "conditioned"

    if global_mode is None:
        derived_modes = {mode for mode in (boltz_mode, protenix_mode) if mode is not None}
        if len(derived_modes) == 1:
            global_mode = next(iter(derived_modes))

    if boltz_mode is not None:
        normalized["boltz_target_geometry_mode"] = boltz_mode
        normalized["boltz_anchor_target"] = boltz_mode in {"conditioned", "frozen"}

    if protenix_mode is not None:
        normalized["protenix_target_geometry_mode"] = protenix_mode
        normalized["protenix_anchor_target"] = protenix_mode in {"conditioned", "frozen"}
        normalized["protenix_use_template"] = (
            protenix_mode in {"conditioned", "frozen"}
            or _to_bool(normalized.get("protenix_use_template"))
        )

    if global_mode is not None:
        normalized["target_geometry_mode"] = global_mode

    threshold = normalized.get("target_template_threshold_angstrom")
    if threshold not in (None, ""):
        try:
            normalized["target_template_threshold_angstrom"] = float(threshold)
        except (TypeError, ValueError):
            normalized.pop("target_template_threshold_angstrom", None)

    strict_target_rmsd = normalized.get("strict_target_rmsd")
    if strict_target_rmsd not in (None, ""):
        try:
            normalized["strict_target_rmsd"] = float(strict_target_rmsd)
        except (TypeError, ValueError):
            normalized.pop("strict_target_rmsd", None)

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


def _normalize_stage_family(value: Any) -> Optional[str]:
    text = _coerce_nonempty_text(value)
    if not text:
        return None
    return text.strip().lower()


def _normalize_ppiflow_tuning_profile(value: Any) -> Optional[str]:
    text = _coerce_nonempty_text(value)
    if not text:
        return None
    normalized = text.strip().lower()
    if normalized in {"stage_optimized", "manual"}:
        return normalized
    return None


def _infer_ppiflow_stage_mode_from_flags(params: Dict[str, Any]) -> Optional[str]:
    explicit_stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    if explicit_stage_mode:
        return explicit_stage_mode
    runs_backbone = _to_bool(params.get("run_ppiflow_backbone_refine"))
    runs_maturation = _to_bool(params.get("run_ppiflow_maturation")) or _to_bool(params.get("run_maturation"))
    if runs_backbone and runs_maturation:
        return "both"
    if runs_backbone:
        return "post_rfantibody"
    if runs_maturation:
        return "post_fampnn"
    return None


def _stage_optimized_ppiflow_defaults(stage_mode: Optional[str]) -> Dict[str, Any]:
    normalized_stage_mode = _normalize_stage_family(stage_mode)
    if normalized_stage_mode in {"post_rfantibody", "backbone_refine", "post_ppiflow"}:
        return {
            "ppiflow_start_t": 0.55,
            "ppiflow_samples_per_target": 7,
            "ppiflow_require_anchors": False,
            "ppiflow_objective_mode": "loop_epitope",
            "ppiflow_objective_threshold": 0.0,
        }
    if normalized_stage_mode in {"post_fampnn", "maturation"}:
        return {
            "ppiflow_start_t": 0.8,
            "ppiflow_samples_per_target": 4,
            "ppiflow_require_anchors": True,
            "ppiflow_objective_mode": "balanced",
            "ppiflow_objective_threshold": 0.0,
        }
    return {}


def _normalize_selected_loop_scope(raw_value: Any) -> Optional[Dict[str, Any]]:
    if raw_value is None:
        return None

    if isinstance(raw_value, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in raw_value.items():
            if value in (None, "", [], {}, ()):
                continue
            cleaned[key] = value
        return cleaned or None

    if isinstance(raw_value, (list, tuple, set)):
        loops = _dedupe_preserve_order(
            [str(item).strip().upper() for item in raw_value if str(item).strip()]
        )
        return {"selected_loops": loops} if loops else None

    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        if any(sep in text for sep in {",", ";", " ", "|"}):
            loops = _dedupe_preserve_order(
                [part.strip().upper() for part in re.split(r"[,\s;|]+", text) if part.strip()]
            )
            if loops:
                return {"selected_loops": loops}
        return {"value": text}

    return {"value": raw_value}


def _build_selected_loop_scope(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(params, dict):
        return None

    scope: Dict[str, Any] = {}
    for key in (
        "interactive_gate_stage",
        "rfantibody_screen_reference_scope",
        "antibody_design_mode",
        "ppiflow_stage_mode",
        "ppiflow_region_mode",
        "ppiflow_backbone_region_mode",
        "ppiflow_maturation_region_mode",
        "iteration_action",
        "stage_family",
        "stage_mode",
        "selection_source_type",
    ):
        value = params.get(key)
        if value not in (None, "", [], {}, ()):
            scope[key] = value

    for key in (
        "ppiflow_selected_loops",
        "ppiflow_backbone_loop_scope",
        "ppiflow_maturation_loop_scope",
        "selected_loops",
        "loop_ids",
        "selected_residues",
        "antibody_design_loops",
    ):
        normalized = _normalize_selected_loop_scope(params.get(key))
        if normalized:
            scope[key] = normalized

    if params.get("cdr_positions_by_loop") not in (None, "", [], {}, ()):
        scope["cdr_positions_by_loop"] = params.get("cdr_positions_by_loop")

    return scope or None


def _derive_iteration_stage_metadata(action: str, params: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    action_normalized = (action or "").strip().lower()
    explicit_stage_family = _normalize_stage_family(params.get("stage_family"))
    explicit_stage_mode = _normalize_stage_family(params.get("stage_mode"))
    explicit_ppiflow_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))

    if explicit_stage_family or explicit_stage_mode:
        return explicit_stage_family, explicit_stage_mode
    if explicit_ppiflow_mode:
        return "ppiflow", explicit_ppiflow_mode

    if action_normalized == "ppiflow_backbone_refine":
        return "ppiflow", "backbone_refine"
    if action_normalized == "ppiflow_maturation":
        return "ppiflow", "maturation"
    if action_normalized == "fampnn_redesign":
        return "fampnn", "redesign"
    if action_normalized == "caliby_redesign":
        return "caliby", "redesign"
    if action_normalized in {"validate_boltz2", "validate_protenix"}:
        return "validation", action_normalized.replace("validate_", "")
    if action_normalized == "frustrampnn":
        return "frustrampnn", "screening"
    if action_normalized == "ui_refinement":
        gate_stage = _normalize_stage_family(params.get("interactive_gate_stage"))
        return "refinement", gate_stage or "interactive"
    if action_normalized in {"cdr_indel_round", "mutation_seeded_refinement", "mutation_seed_build", "manual_mutagenesis_round"}:
        return "mutation", action_normalized
    return _normalize_stage_family(params.get("child_stage")), action_normalized or None


def _format_stage_identity(stage_family: Optional[str], stage_mode: Optional[str]) -> str:
    family = _normalize_stage_family(stage_family)
    mode = _normalize_stage_family(stage_mode)
    if family and mode:
        return f"{family}/{mode}"
    if family:
        return family
    if mode:
        return mode
    return "unknown"


def _review_stage_to_canonical_stage(stage: Any) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalize_stage_family(stage)
    if normalized == "post_rfantibody":
        return "rfantibody", "post_rfantibody"
    if normalized == "post_boltzgen":
        return "boltzgen", "post_boltzgen"
    if normalized == "post_ppiflow_generator":
        return "ppiflow", "generator_backbone_refine"
    if normalized == "post_fampnn":
        return "fampnn", "post_fampnn"
    if normalized == "post_caliby":
        return "caliby", "post_caliby"
    if normalized == "post_structure_validation":
        return "validation", "post_structure_validation"
    return None, None


def _awaiting_stage_to_resume_hint(stage: Any) -> Optional[str]:
    normalized = _normalize_stage_family(stage)
    if normalized == "post_rfantibody":
        return "rfantibody"
    if normalized == "post_boltzgen":
        return "boltzgen"
    if normalized == "post_ppiflow_generator":
        return "ppiflow"
    if normalized == "post_fampnn":
        return "fampnn"
    if normalized == "post_caliby":
        return "caliby"
    if normalized in {"post_structure_validation", "pre_protenix_msa"}:
        return "structure_validation"
    return None


def _resolve_design_stage_metadata(design: Any) -> tuple[Optional[str], Optional[str]]:
    family = (
        _normalize_stage_family(getattr(design, "stage_family", None))
        or _normalize_stage_family(getattr(design, "source_stage_family", None))
    )
    mode = (
        _normalize_stage_family(getattr(design, "stage_mode", None))
        or _normalize_stage_family(getattr(design, "source_stage_mode", None))
    )
    review_stage_family, review_stage_mode = _review_stage_to_canonical_stage(
        getattr(design, "source_stage", None)
    )
    return family or review_stage_family, mode or review_stage_mode


def _resolve_design_artifact_class(design: Any) -> Optional[str]:
    explicit = normalize_antibody_artifact_class(getattr(design, "artifact_class", None))
    if explicit:
        return explicit
    family, mode = _resolve_design_stage_metadata(design)
    return infer_antibody_artifact_class_from_stage(family, mode)


def _requires_immediate_ppiflow_backbone_refine(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized == "ppiflow_backbone_refine":
        return True
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    if stage_mode == "post_ppiflow":
        return False
    return _to_bool(params.get("run_ppiflow_backbone_refine")) or stage_mode in {
        "post_rfantibody",
        "backbone_refine",
        "both",
    }


def _requires_direct_ppiflow_maturation(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized == "ppiflow_maturation":
        return True
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    if not (
        _to_bool(params.get("run_ppiflow_maturation"))
        or _to_bool(params.get("run_maturation"))
        or stage_mode in {"post_fampnn", "maturation", "both"}
    ):
        return False
    return not (
        _to_bool(params.get("seq_design_fampnn"))
        or _to_bool(params.get("seq_design_antifold"))
        or _to_bool(params.get("seq_design_proteinmpnn"))
        or _to_bool(params.get("seq_design_caliby"))
    )


def _requires_post_ppiflow_backbone_reattempt(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    return stage_mode == "post_ppiflow"


def _validate_antibody_iteration_source_compatibility(action: str, params: Dict[str, Any]) -> None:
    source_stage_family = _normalize_stage_family(
        params.get("selected_input_stage_family") or params.get("source_stage_family")
    )
    source_stage_mode = _normalize_stage_family(
        params.get("selected_input_stage_mode") or params.get("source_stage_mode")
    )
    source_artifact_class = infer_selected_input_artifact_class(
        selected_input_artifact_class=params.get("selected_input_artifact_class"),
        selected_input_stage_family=source_stage_family,
        selected_input_stage_mode=source_stage_mode,
        rfantibody_input_pdbs=params.get("rfantibody_input_pdbs"),
        fampnn_collected_pdbs=params.get("fampnn_collected_pdbs"),
    )
    source_identity = _format_artifact_identity(source_artifact_class, source_stage_family, source_stage_mode)
    action_label = ANTIBODY_ITERATION_ACTION_LABELS.get((action or "").strip().lower(), action or "launch")

    if _requires_immediate_ppiflow_backbone_refine(action, params) and source_artifact_class not in {None, BACKBONE_COMPLEX}:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} only accepts backbone-complex inputs. "
                f"Selected input is {source_identity}. "
                "Use FAMPNN redesign first, then optionally run post-FA-MPNN PPIFlow maturation."
            ),
        )
    if _requires_immediate_ppiflow_backbone_refine(action, params) and source_stage_family == "ppiflow":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} does not accept recursive PPIFlow backbone-refine inputs. "
                f"Selected input is {source_identity}. "
                "Use post-PPIFlow reattempt mode for loop-selective retries, or return to sequence design/maturation."
            ),
        )

    if _requires_post_ppiflow_backbone_reattempt(action, params) and (
        source_artifact_class != BACKBONE_COMPLEX or source_stage_family != "ppiflow"
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} in post-PPIFlow reattempt mode only accepts PPIFlow-derived backbone-complex inputs. "
                f"Selected input is {source_identity}. "
                "Choose RFantibody backbone refine for RF outputs, or switch this relaunch to FA-MPNN/post-FA-MPNN maturation."
            ),
        )

    if _requires_direct_ppiflow_maturation(action, params) and source_artifact_class != SEQUENCE_DESIGNED_COMPLEX:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} requires sequence-designed complex inputs when it runs directly on the selected structures. "
                f"Selected input is {source_identity}. "
                "If you are relaunching from PPIFlow backbone outputs, include sequence design first or use FAMPNN redesign."
            ),
        )


def _derive_job_stage_tags(model_id: str, mode: str, params: Dict[str, Any], child_stage: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(params, dict):
        params = {}

    model_normalized = (model_id or "").strip().lower()
    mode_normalized = (mode or "").strip().lower()
    trusted_iteration_identity = (
        model_normalized in {"rfantibody", "ppiflow", "fampnn_child", "maturation_child"}
        or mode_normalized in {"antibody_design", "maturation_child"}
    )
    action = _coerce_nonempty_text(params.get("iteration_action"))
    if action and trusted_iteration_identity:
        family, stage_mode = _derive_iteration_stage_metadata(action, params)
        if family or stage_mode:
            return family, stage_mode

    child_stage_normalized = _normalize_stage_family(child_stage)
    if child_stage_normalized and trusted_iteration_identity:
        return child_stage_normalized, child_stage_normalized

    if mode_normalized == "maturation_child":
        return "ppiflow", "maturation"
    if model_normalized == "boltzgen":
        boltzgen_mode = str(params.get("boltzgen_mode") or mode_normalized or "").strip().lower()
        return "boltzgen", boltzgen_mode or "generation"
    if model_normalized == "ppiflow":
        ppiflow_mode = str(
            params.get("stage_mode")
            or params.get("ppiflow_stage_mode")
            or mode_normalized
            or ""
        ).strip().lower()
        return "ppiflow", ppiflow_mode or "generator_backbone_refine"
    if model_normalized == "caliby_experimental":
        caliby_mode = str(
            params.get("stage_mode")
            or params.get("caliby_task")
            or mode_normalized
            or ""
        ).strip().lower()
        return "caliby", caliby_mode or "sequence_design"
    if model_normalized == "protein_hunter_experimental":
        protein_hunter_backend = str(
            params.get("ph_backend")
            or params.get("backend")
            or ""
        ).strip().lower()
        protein_hunter_task = str(
            params.get("stage_mode")
            or params.get("ph_task")
            or params.get("task")
            or mode_normalized
            or ""
        ).strip().lower()
        joined_mode = "_".join(part for part in (protein_hunter_backend, protein_hunter_task) if part)
        return "protein_hunter", joined_mode or "generation"
    if "fampnn" in model_normalized:
        return "fampnn", mode_normalized or "sequence_design"
    if model_normalized in {"protenix", "boltz2", "rf3"} and mode_normalized in {"predict", "complex"}:
        return "validation", model_normalized
    if "antibody" in mode_normalized or "antibody" in model_normalized:
        return "antibody", mode_normalized or model_normalized
    return None, None


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


def _looks_like_antibody_job(job: Optional[Job]) -> bool:
    if job is None:
        return False
    model_id = (job.model_id or "").strip().lower()
    mode = (job.mode or "").strip().lower()
    params = job.params if isinstance(job.params, dict) else {}
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    boltzgen_mode = str(params.get("boltzgen_mode") or mode or "").strip().lower()
    framework_type = str(params.get("framework_type") or "").strip().lower()
    has_antibody_params = any(_is_meaningful_param_value(params.get(key)) for key in ("framework_type", "antibody_chains", "epitope_residues"))
    is_boltzgen_nanobody = (
        model_id == "boltzgen"
        and (
            boltzgen_mode in {"nanobody_binder", "antibody_binder"}
            or framework_type == "nanobody"
        )
    )
    is_caliby_antibody = model_id == "caliby_experimental" and has_antibody_params
    return (
        model_id in {"template_antibody_denovo", "antibody_denovo", "antibody_child"}
        or "antibody" in model_id
        or "antibody" in mode
        or is_antibody_pipeline_mode(rfd_mode)
        or has_antibody_params
        or is_boltzgen_nanobody
        or is_caliby_antibody
    )


def _should_spawn_antibody_refinement_on_resume(job: Optional[Job]) -> bool:
    if job is None or not getattr(job, "awaiting_input", False):
        return False
    awaiting_stage = str(getattr(job, "awaiting_stage", "") or "").strip().lower()
    model_id = str(getattr(job, "model_id", "") or "").strip().lower()
    if awaiting_stage == "post_boltzgen" and model_id == "boltzgen":
        return _looks_like_antibody_job(job)
    if awaiting_stage == "post_ppiflow_generator" and model_id == "ppiflow":
        return _looks_like_antibody_job(job)
    return False


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
            detail="Selected job is not part of an antibody or nanobody refinement-compatible lineage.",
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


def _saved_review_filter_entries(job: Optional[Job]) -> List[Dict[str, Any]]:
    if job is None:
        return []

    saved_entries = getattr(job, "saved_selection_sets", None)
    if isinstance(saved_entries, list):
        return list(saved_entries)

    if isinstance(job.awaiting_payload, dict):
        raw_sets = job.awaiting_payload.get("review_filter_sets")
        if isinstance(raw_sets, list):
            return list(raw_sets)

    return []


def _persist_saved_review_filter_entries(job: Job, entries: List[Dict[str, Any]]) -> None:
    pruned_entries = list(entries[:50])
    job.saved_selection_sets = pruned_entries

    if isinstance(job.awaiting_payload, dict):
        payload = dict(job.awaiting_payload or {})
        payload["review_filter_sets"] = pruned_entries
        job.awaiting_payload = refresh_gate_payload(payload, job.output_dir)
        _write_gate_snapshot(job)


def _serialized_saved_review_filter_sets(job: Optional[Job]) -> Optional[List[Dict[str, Any]]]:
    entries = _saved_review_filter_entries(job)
    return entries or None


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
        "batch_name",
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
        "selected_input_dir",
        "selected_input_manifest",
        "selected_input_artifact_class",
        "selected_input_schema_version",
        "selected_input_source_job_id",
        "selected_input_stage_family",
        "selected_input_stage_mode",
        "lineage_root_job_id",
        "stage_family",
        "stage_mode",
        "selection_source_type",
        "selection_source_job_id",
        "selection_dataset_name",
        "selected_loop_scope",
        "ppiflow_stage_mode",
        "ppiflow_tuning_profile",
        "ppiflow_stage_target",
        "ppiflow_selected_loops",
        "ppiflow_objective_mode",
        "ppiflow_objective_threshold",
        "manual_mutation_fixed_positions_json",
        "manual_mutation_mode",
        "manual_mutation_method",
        "mutation_seed_refinement_trigger",
        "mutation_variant",
        "resume_job_id",
        "resume_root_job_id",
        "resume_work_dir",
        "resume_source_dir",
        "resume_stage_work_dir",
        "resume_requested_stage",
        "resume_param_overrides",
        "resume_from_stage",
        "resume_name_suffix",
        "resume_lock_retry_attempts",
        *PPI_FLOW_STAGE_FLAG_KEYS,
    }:
        pruned.pop(key, None)
    return _normalize_antibody_job_params(_normalize_structure_geometry_params(pruned))


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


def _create_antibody_selection_dir(action: str) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / "antibody"
    selection_root.mkdir(parents=True, exist_ok=True)
    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)
    return selection_dir


def _create_generic_selection_dir(namespace: str, action: str) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / namespace
    selection_root.mkdir(parents=True, exist_ok=True)
    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)
    return selection_dir


def _link_selection_input(source_path: Path, dest_path: Path) -> str:
    try:
        rel_target = os.path.relpath(source_path, start=dest_path.parent)
        os.symlink(rel_target, dest_path)
        return "symlink"
    except OSError as symlink_error:
        try:
            os.link(source_path, dest_path)
            return "hardlink"
        except OSError as hardlink_error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to materialize a reference-only selection set without copying data. "
                    f"Symlink error: {symlink_error}. Hardlink error: {hardlink_error}."
                ),
            ) from hardlink_error



def _selection_manifest_path(selection_dir: Path) -> Path:
    return selection_dir / "selection_manifest.json"


def _derive_source_stage_payload(
    source_job: Job,
    designs: List[Design],
    selection_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    source_job_ids = _dedupe_preserve_order([str(design.job_id) for design in designs if getattr(design, "job_id", None)])
    design_stage_metadata = [_resolve_design_stage_metadata(design) for design in designs]
    source_stage_families = _dedupe_preserve_order([
        family
        for family, _ in design_stage_metadata
        if family
    ])
    source_stage_modes = _dedupe_preserve_order([
        mode
        for _, mode in design_stage_metadata
        if mode
    ])

    job_stage_family = _normalize_stage_family(getattr(source_job, "stage_family", None))
    job_stage_mode = _normalize_stage_family(getattr(source_job, "stage_mode", None))
    if not job_stage_family and not job_stage_mode:
        job_stage_family, job_stage_mode = _derive_job_stage_tags(
            getattr(source_job, "model_id", None),
            getattr(source_job, "mode", None),
            source_job.params if isinstance(getattr(source_job, "params", None), dict) else {},
            getattr(source_job, "child_stage", None),
        )

    source_stage_job_id = source_job_ids[0] if len(source_job_ids) == 1 else source_job.id
    source_stage_family = source_stage_families[0] if len(source_stage_families) == 1 else job_stage_family
    source_stage_mode = source_stage_modes[0] if len(source_stage_modes) == 1 else job_stage_mode
    design_artifact_classes = _dedupe_preserve_order(
        [
            artifact_class
            for artifact_class in (_resolve_design_artifact_class(design) for design in designs)
            if artifact_class
        ]
    )
    selected_input_artifact_class = (
        design_artifact_classes[0]
        if len(design_artifact_classes) == 1
        else infer_antibody_artifact_class_from_stage(source_stage_family, source_stage_mode)
    )
    source_selection_manifest_path = str(_selection_manifest_path(selection_dir)) if selection_dir is not None else None

    return {
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_selection_manifest_path": source_selection_manifest_path,
        "source_selection_count": len(designs),
        "selected_input_dir": str(selection_dir) if selection_dir is not None else None,
        "selected_input_manifest": source_selection_manifest_path,
        "selected_input_stage_family": source_stage_family,
        "selected_input_stage_mode": source_stage_mode,
        "selected_input_source_job_id": source_stage_job_id,
        "selected_input_artifact_class": selected_input_artifact_class,
        "selected_input_schema_version": (
            ANTIBODY_PIPELINE_CONTRACT_VERSION if selected_input_artifact_class else None
        ),
    }


def _build_selection_manifest_item(
    design: Design,
    *,
    source_path: Path,
    selection_path: Path,
    selection_entry_mode: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    design_stage_family, design_stage_mode = _resolve_design_stage_metadata(design)
    design_artifact_class = _resolve_design_artifact_class(design)
    item: Dict[str, Any] = {
        "design_id": design.id,
        "design_name": design.name,
        "design_job_id": design.job_id,
        "design_stage_family": design_stage_family,
        "design_stage_mode": design_stage_mode,
        "design_artifact_class": design_artifact_class,
        "design_artifact_schema_version": (
            getattr(design, "artifact_schema_version", None)
            or (ANTIBODY_PIPELINE_CONTRACT_VERSION if design_artifact_class else None)
        ),
        "lineage_root_job_id": design.lineage_root_job_id,
        "parent_design_id": design.parent_design_id,
        "origin_design_id": design.origin_design_id,
        "origin_backbone_design_id": design.origin_backbone_design_id,
        "source_design_name": design.name,
        "source_pdb_path": str(source_path),
        "selection_pdb_path": str(selection_path),
        "selected_loop_scope": design.selected_loop_scope,
    }
    if selection_entry_mode:
        item["selection_entry_mode"] = selection_entry_mode
    if extra:
        for key, value in extra.items():
            if value not in (None, "", [], {}, ()):
                item[key] = value
    return item


def _write_selection_manifest(
    selection_dir: Path,
    root_job: Job,
    source_job: Job,
    action: str,
    manifest_items: List[Dict[str, Any]],
    source_stage_payload: Optional[Dict[str, Any]] = None,
    fixed_positions_by_pdb: Optional[Dict[str, str]] = None,
) -> None:
    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "root_job_id": root_job.id,
        "source_job_id": source_job.id,
        "design_count": len(manifest_items),
        "source_stage_job_id": (source_stage_payload or {}).get("source_stage_job_id"),
        "source_stage_family": (source_stage_payload or {}).get("source_stage_family"),
        "source_stage_mode": (source_stage_payload or {}).get("source_stage_mode"),
        "selected_input_artifact_class": (source_stage_payload or {}).get("selected_input_artifact_class"),
        "selected_input_schema_version": (source_stage_payload or {}).get("selected_input_schema_version"),
        "source_selection_manifest_path": str(_selection_manifest_path(selection_dir)),
        "source_selection_count": len(manifest_items),
        "designs": manifest_items,
    }
    _selection_manifest_path(selection_dir).write_text(json.dumps(manifest, indent=2))
    if fixed_positions_by_pdb:
        (selection_dir / "mutation_fixed_positions.json").write_text(json.dumps(fixed_positions_by_pdb, indent=2, sort_keys=True))


def _write_seeded_refinement_metadata(
    selection_dir: Path,
    root_job: Job,
    source_job: Job,
    action: str,
    manifest_items: List[Dict[str, Any]],
    fixed_positions_by_pdb: Optional[Dict[str, str]] = None,
) -> None:
    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload={
            "source_stage_job_id": source_job.id,
            "source_stage_family": _normalize_stage_family(getattr(source_job, "stage_family", None)),
            "source_stage_mode": _normalize_stage_family(getattr(source_job, "stage_mode", None)),
        },
        fixed_positions_by_pdb=fixed_positions_by_pdb,
    )


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
        link_mode = _link_selection_input(source_path, dest_path)

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

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
            extra={"mutation_variant": variant_meta},
        ))
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
        link_mode = _link_selection_input(source_path, dest_path)

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
        ))

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
    )
    return selection_dir


def _candidate_sidecar_paths(design: Design, source_path: Path) -> List[Path]:
    candidates: List[Path] = []
    if design.json_path:
        candidates.append(_resolve_design_structure_path(design.json_path))
    else:
        sibling_json = source_path.with_suffix(".json")
        if sibling_json.exists():
            candidates.append(sibling_json.resolve())
    for suffix in (".cif", ".npz"):
        candidate = source_path.with_suffix(suffix)
        if candidate.exists():
            candidates.append(candidate.resolve())
    pae_candidate = source_path.with_suffix(".pae.npz")
    if pae_candidate.exists():
        candidates.append(pae_candidate.resolve())
    unique_paths: List[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve())
        if normalized in seen or not candidate.exists():
            continue
        seen.add(normalized)
        unique_paths.append(candidate)
    return unique_paths


def _materialize_protein_local_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    action: str,
) -> Path:
    selection_dir = _create_generic_selection_dir("protein_local_redesign", action)

    manifest_items: List[Dict[str, Any]] = []
    for idx, design in enumerate(designs, start=1):
        if not design.pdb_path:
            raise HTTPException(
                status_code=422,
                detail=f"Design '{design.name}' is missing a structure path.",
            )
        source_path = _resolve_design_structure_path(design.pdb_path)
        dest_name = source_path.name
        dest_path = selection_dir / dest_name
        if dest_path.exists():
            dest_path = selection_dir / f"{idx:03d}_{dest_name}"
        link_mode = _link_selection_input(source_path, dest_path)

        for sidecar in _candidate_sidecar_paths(design, source_path):
            sidecar_dest = selection_dir / sidecar.name
            if sidecar_dest.exists():
                sidecar_dest = selection_dir / f"{idx:03d}_{sidecar.name}"
            _link_selection_input(sidecar, sidecar_dest)

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
        ))

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
    )
    return selection_dir


def _build_antibody_iteration_job(
    root_job: Job,
    source_job: Job,
    action: str,
    selection_dir: Path,
    design_ids: List[str],
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
    saved_filter_set: Optional[SavedReviewFilterSet] = None,
    selected_designs: Optional[List[Design]] = None,
) -> JobCreate:
    action = action.strip().lower()
    action_map = {
        "validate_boltz2": {
            "suffix": "validate_boltz2",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": False,
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
        "ppiflow_backbone_refine": {
            "suffix": "ppiflow_backbone_refine",
            "params": {
                "skip_rfantibody": True,
                "rfantibody_input_pdbs": str(selection_dir),
                "fampnn_collected_pdbs": None,
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_ppiflow_backbone_refine": True,
                "run_ppiflow_maturation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "run_thermompnn": False,
                "run_stability_scoring": False,
                "run_af2_backprop": False,
                "openmm_enabled": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_fampnn",
                "ppiflow_stage_mode": "post_rfantibody",
                "ppiflow_tuning_profile": "stage_optimized",
                "ppiflow_stage_target": "post_rfantibody",
                "ppiflow_rotamer_enrichment_enabled": True,
                "ppiflow_require_anchors": True,
                "ppiflow_rotamer_shell_cutoff": 20.0,
                "maturation_anchor_distance_cutoff": 12.0,
            },
        },
        "ppiflow_maturation": {
            "suffix": "ppiflow_maturation",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_ppiflow_backbone_refine": False,
                "run_ppiflow_maturation": True,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
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
                "ppiflow_stage_mode": "post_fampnn",
                "ppiflow_tuning_profile": "stage_optimized",
                "ppiflow_stage_target": "post_fampnn",
                "ppiflow_rotamer_enrichment_enabled": True,
                "ppiflow_require_anchors": True,
                "ppiflow_rotamer_shell_cutoff": 20.0,
                "maturation_anchor_distance_cutoff": 12.0,
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
                "Allowed: validate_boltz2, validate_protenix, ppiflow_backbone_refine, ppiflow_maturation, fampnn_redesign, frustrampnn, cdr_indel_round, mutation_seeded_refinement, ui_refinement."
            ),
        )

    launch_params = _prune_iteration_params(root_job.params if isinstance(root_job.params, dict) else {})
    source_stage_payload = _derive_source_stage_payload(source_job, selected_designs or [], selection_dir)
    launch_params.update({
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": root_job.id,
        "iteration_source_design_ids": design_ids,
        "iteration_action": action,
        "iteration_selection_dir": str(selection_dir),
        "interactive_gate_continue": False,
        **source_stage_payload,
    })
    
    # Preserve epitope residue configurations for contact calculations during refinement
    if isinstance(root_job.params, dict):
        for key in ["epitope_residues", "selected_residues"]:
            if key in root_job.params:
                launch_params[key] = root_job.params[key]
                
    launch_params.update(action_map[action]["params"])

    stage_family, stage_mode = _derive_iteration_stage_metadata(action, launch_params)
    launch_params.update({
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": root_job.id,
        "selection_source_job_id": source_job.id,
        "selection_source_type": "saved_dataset" if saved_filter_set is not None else "selected_designs",
        "selection_dataset_name": saved_filter_set.name if saved_filter_set is not None else None,
        "selected_loop_scope": _build_selected_loop_scope(launch_params),
        "ppiflow_selected_loops": launch_params.get("ppiflow_selected_loops"),
        "source_stage_job_id": source_stage_payload.get("source_stage_job_id"),
        "source_stage_family": source_stage_payload.get("source_stage_family"),
        "source_stage_mode": source_stage_payload.get("source_stage_mode"),
        "source_selection_manifest_path": source_stage_payload.get("source_selection_manifest_path"),
        "source_selection_count": source_stage_payload.get("source_selection_count"),
        "selected_input_dir": source_stage_payload.get("selected_input_dir"),
        "selected_input_manifest": source_stage_payload.get("selected_input_manifest"),
        "selected_input_stage_family": source_stage_payload.get("selected_input_stage_family"),
        "selected_input_stage_mode": source_stage_payload.get("selected_input_stage_mode"),
        "selected_input_source_job_id": source_stage_payload.get("selected_input_source_job_id"),
        "selected_input_artifact_class": source_stage_payload.get("selected_input_artifact_class"),
        "selected_input_schema_version": source_stage_payload.get("selected_input_schema_version"),
    })

    for key in ["rfantibody_input_pdbs", "fampnn_collected_pdbs"]:
        if launch_params.get(key) is None:
            launch_params.pop(key, None)

    if param_overrides:
        launch_params.update(param_overrides)
        stage_family, stage_mode = _derive_iteration_stage_metadata(action, launch_params)
        launch_params.update({
            "stage_family": stage_family,
            "stage_mode": stage_mode,
            "selected_loop_scope": _build_selected_loop_scope(launch_params),
        })

    if action == "ui_refinement":
        refinement_screen_keys = {
            "enable_rfantibody_filter",
            "rfantibody_screen_reference_scope",
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
        
        requested_ppiflow_stage_mode = _normalize_stage_family(launch_params.get("ppiflow_stage_mode"))
        requests_backbone_refine = (
            _to_bool(launch_params.get("run_ppiflow_backbone_refine"))
            or requested_ppiflow_stage_mode in {"post_rfantibody", "backbone_refine", "post_ppiflow", "both"}
        )

        # Sequence-design and sequence-free backbone-retry launches both start from backbone-style inputs.
        # Direct maturation/validation-only launches start from sequence-conditioned collected structures.
        if (
            launch_params.get("seq_design_fampnn")
            or launch_params.get("seq_design_antifold")
            or launch_params.get("seq_design_proteinmpnn")
            or launch_params.get("seq_design_caliby")
            or requests_backbone_refine
        ):
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

    launch_params = _normalize_antibody_job_params(_normalize_structure_geometry_params(launch_params))
    _validate_antibody_iteration_source_compatibility(action, launch_params)
    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else action_map[action]["suffix"]
    job_name = f"{root_job.name}_{suffix}"

    launch_params["rfd_mode"] = ANTIBODY_REFINEMENT_PIPELINE

    return JobCreate(
        name=job_name,
        model_id="template_antibody_denovo",
        mode=ANTIBODY_REFINEMENT_PIPELINE,
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

    # Legacy persisted jobs may carry run_multimer_qc. New requests use run_fastq_qc only.
    run_multimer_qc = params.get("run_multimer_qc")
    if isinstance(run_multimer_qc, bool):
        return run_multimer_qc, True

    return False, False


def _resolve_nanopore_bam_realign(params: Optional[dict]) -> bool:
    if not isinstance(params, dict):
        return False
    return _to_bool(params.get("bam_force_realign"))


NANOPORE_STAGE_RESPONSE_MODES = frozenset(
    {
        "basecall_dna",
        "basecall_rna",
        "plasmid_qc",
        "construct_screening",
        "methylation_analysis",
        "nanopore_methylation",
        "fastq_qc",
        "clone_validation",
    }
)


def _uses_nanopore_stage_response(job: Job) -> bool:
    return job.model_id == "nanopore" and job.mode in NANOPORE_STAGE_RESPONSE_MODES


def _planned_nanopore_stages(params: Optional[dict]) -> List[str]:
    np_params = params if isinstance(params, dict) else {}
    display_stages: List[str] = []
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
        display_stages.append("dorado_align" if bam_force_realign else "bam_prepare")
    if (has_bam and not has_reference) or (has_pod5 and not has_reference):
        display_stages.append("bam_prepare")
    if has_fastq and has_reference:
        display_stages.append("fastq_align")
    if fastq_qc_enabled and has_fastq and has_reference and not legacy_multimer_mode:
        display_stages.append("fastq_qc")
    if np_params.get("run_modkit") is not False and (has_pod5 or has_bam):
        display_stages.append("modkit")
    if fastq_qc_enabled and legacy_multimer_mode and has_fastq:
        display_stages.append("multimer_qc")
    if fastq_qc_enabled and legacy_multimer_mode and has_fastq and has_reference:
        display_stages.append("dimer_analysis")
    if np_params.get("run_assembly") is True and (has_pod5 or has_bam or has_fastq):
        display_stages.append("wf_clone_validation")

    return _dedupe_preserve_order(display_stages)


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
            "assembly/runtime_provenance.json",
            "assembly/wf_clone_out",
            "assembly/wf_clone_out/wf-clone-validation-report.html",
            "assembly/wf_clone_out/sample_status.txt",
            "assembly/adapter/adapter_manifest.json",
            "verification/qc_manifest.json",
            "verification/verification_summary.tsv",
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
        if params.get("run_assembly") is True and (has_pod5 or has_bam or has_fastq):
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

    if _uses_nanopore_stage_response(job):
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


def _resolve_protenix_weights_dir(params: dict) -> Path:
    protenix_weights_raw = (
        params.get("protenix_weights")
        or os.getenv("BMS_PROTENIX_WEIGHTS")
        or (
            Path(os.getenv("BMS_WEIGHTS", "")).expanduser() / "protenix"
            if os.getenv("BMS_WEIGHTS")
            else None
        )
    )
    return (
        Path(protenix_weights_raw).expanduser()
        if protenix_weights_raw
        else get_data_root() / "weights" / "protenix"
    )


def _validate_protenix_template_requirements(model_id: str, params: dict) -> None:
    if model_id != "protenix":
        return
    if not _to_bool(params.get("protenix_use_template", False)):
        return

    fixed_target_source_path = str(params.get("fixed_target_source_path") or "").strip()
    fixed_target_source_chains = str(params.get("fixed_target_source_chains") or "").strip()
    if fixed_target_source_path and fixed_target_source_chains:
        # Anchored/fixed-target Protenix runs extract a task-local template DB from the
        # supplied target structure, so they do not require the shared global mmCIF cache.
        return

    mmcif_dir = _resolve_protenix_weights_dir(params) / "mmcif"

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


def _validate_protenix_checkpoint_requirements(model_id: str, params: dict) -> None:
    model_normalized = (model_id or "").strip().lower()
    pred_method = str(params.get("pred_method", "")).strip().lower()
    structure_validator = str(params.get("structure_validator", "")).strip().lower()
    predictor = str(params.get("predictor", "")).strip().lower()
    uses_protenix = (
        model_normalized == "protenix"
        or pred_method == "protenix"
        or structure_validator == "protenix"
        or predictor == "protenix"
    )
    if not uses_protenix:
        return

    selected_model = str(params.get("protenix_model_weights") or "protenix-v2").strip()
    if selected_model != "protenix-v2":
        raise HTTPException(
            status_code=422,
            detail={
                "validation_errors": [
                    "Protenix is pinned to the V2 checkpoint; set protenix_model_weights to protenix-v2."
                ]
            },
        )

    checkpoint_path = _resolve_protenix_weights_dir(params) / "checkpoint" / "protenix-v2.pt"
    if checkpoint_path.exists():
        return

    raise HTTPException(
        status_code=422,
        detail={
            "validation_errors": [
                (
                    "Protenix v2 is required, but the shared checkpoint was not found at "
                    f"{checkpoint_path}. Stage protenix-v2.pt in the shared Protenix weights directory before submitting."
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


def _resolve_md_input_path_for_runtime(value: str) -> str:
    """Resolve one MD input only when it is an existing file below an allowed root."""

    raw = str(value or "").strip()
    forbidden = MDLaunchError(
        "MD_INPUT_PATH_FORBIDDEN",
        "The molecular-dynamics input path is not available from an allowed data root.",
        status_code=403,
    )
    if not raw:
        raise forbidden
    expanded = Path(os.path.expanduser(raw))
    try:
        if expanded.is_absolute():
            resolved = expanded.resolve()
            if not any(
                resolved.is_relative_to(root.resolve())
                for root in get_allowed_roots().values()
            ):
                raise forbidden
        else:
            resolved = resolve_allowed_path(raw).resolve()
    except (OSError, ValueError) as exc:
        if isinstance(exc, MDLaunchError):
            raise
        raise forbidden from exc
    if not resolved.is_file():
        raise MDLaunchError(
            "MD_INPUT_MISSING",
            "A molecular-dynamics input disappeared before launch materialization.",
            status_code=409,
        )
    return str(resolved)


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


def _validate_plr_validator_availability(registry, params: dict) -> None:
    selected = params.get("structure_validators")
    if not isinstance(selected, list):
        return
    registry_ids = {
        "boltz2": "boltz2",
        "esmfold2": "esmfold2",
        "protenix_v2": "protenix",
    }
    unavailable = [
        validator
        for validator in selected
        if registry.get_model(registry_ids[validator]) is None
    ]
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail=f"Selected structure validators are disabled or unavailable: {', '.join(unavailable)}",
        )


def _is_protein_local_redesign_job(job: Job) -> bool:
    return job.model_id == "protein_local_redesign" or (
        job.model_id == "protein_modification_experimental" and job.mode == "region_redesign"
    )


@router.get("", response_model=JobList)
async def list_jobs(
    status: Optional[JobStatus] = None,
    q: Optional[str] = None,
    model_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_children: bool = False,  # New param: show child jobs if True
    summary: bool = False,  # Mobile/list views: omit heavyweight detail fields until a job is opened
    session: AsyncSession = Depends(get_session)
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
        Job.current_stage,
        Job.completed_stages,
        Job.awaiting_input,
        Job.awaiting_stage,
    )
    selected_entities = summary_columns if summary else (Job,)
    design_counts = (
        select(
            Design.job_id.label("job_id"),
            func.count(Design.id).label("design_count"),
        )
        .group_by(Design.job_id)
        .subquery()
    )
    query = (
        select(
            *selected_entities,
            func.coalesce(design_counts.c.design_count, 0).label("design_count"),
        )
        .outerjoin(design_counts, design_counts.c.job_id == Job.id)
        .order_by(Job.created_at.desc())
    )
    
    # Filter out child jobs by default (show only parent/top-level jobs)
    if not include_children:
        query = query.where(Job.parent_job_id == None)
    
    if status:
        query = query.where(Job.status == status.value)

    if model_id:
        query = query.where(Job.model_id == model_id)

    if mode:
        query = query.where(Job.mode == mode)
    
    if q:
        query = query.where(Job.name.ilike(f"%{q}%"))
    
    query = query.limit(limit).offset(offset)
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
    child_design_count_by_parent: dict[str, int] = {}
    if listed_job_ids:
        child_count_result = await session.execute(
            select(Job.parent_job_id, func.count(Design.id))
            .join(Design, Design.job_id == Job.id)
            .where(Job.parent_job_id.in_(listed_job_ids))
            .group_by(Job.parent_job_id)
        )
        child_design_count_by_parent = {
            str(parent_job_id): int(design_count or 0)
            for parent_job_id, design_count in child_count_result.all()
            if parent_job_id is not None
        }
    
    # Get total count (for pagination) - also exclude children
    count_query = select(func.count(Job.id))
    if not include_children:
        count_query = count_query.where(Job.parent_job_id == None)
    if status:
        count_query = count_query.where(Job.status == status.value)
    if model_id:
        count_query = count_query.where(Job.model_id == model_id)
    if mode:
        count_query = count_query.where(Job.mode == mode)
    if q:
        count_query = count_query.where(Job.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar()

    
    job_responses = []
    for job, design_count in rows:
        frustrampnn_result_count = frustrampnn_count_by_job.get(job.id, 0)
        completed_stages = _dedupe_preserve_order(list(job.completed_stages or []))
        stage_outputs = {} if summary else dict(job.stage_outputs or {})
        if not summary and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}:
            review_count = _review_candidate_count_cached(job)
            if (design_count or 0) == 0 and review_count is not None:
                design_count = review_count
            if (design_count or 0) == 0:
                child_design_count = child_design_count_by_parent.get(job.id)
                if child_design_count:
                    design_count = child_design_count
        
        job_responses.append(JobResponse(
            id=job.id,
            name=job.name,
            status=job.status,
            model_id=job.model_id,
            mode=job.mode,
            params={} if summary else _public_job_params(job),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            output_dir=_public_job_output_dir(job),
            error_message=job.error_message,
            design_count=design_count,  # Now joined from DB
            requested_design_count=None if summary else _resolve_requested_design_count(job),
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
            source_selection_manifest_path=None if summary else job.source_selection_manifest_path,
            source_selection_count=job.source_selection_count,
            selected_input_artifact_class=job.selected_input_artifact_class,
            selected_input_schema_version=job.selected_input_schema_version,
            selection_source_type=job.selection_source_type,
            selection_source_job_id=job.selection_source_job_id,
            selection_dataset_name=job.selection_dataset_name,
            selected_loop_scope=None if summary else job.selected_loop_scope,
            provenance=None if summary else job.provenance,
            saved_selection_sets=None if summary else _serialized_saved_review_filter_sets(job),
            pinned_gpu=job.pinned_gpu,
            current_stage=job.current_stage,
            completed_stages=completed_stages,
            stage_outputs={} if summary else stage_outputs,
            awaiting_input=job.awaiting_input,
            awaiting_stage=job.awaiting_stage,
            awaiting_payload={} if summary else job.awaiting_payload,
            decision_history=[] if summary else job.decision_history,
            frustrampnn_result_count=frustrampnn_result_count,
            frustrampnn_reopen_destination=(
                {"surface": "frustrampnn-workbench", "params": {"job_id": job.id}}
                if frustrampnn_result_count else None
            ),
        ))
    
    return JobList(jobs=job_responses, total=total)


@router.post("/imports/proteinbase", response_model=JobResponse, status_code=201)
async def import_proteinbase_bundle_job(
    request: ProteinBaseBundleImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Import an uploaded ProteinBase JSONL bundle as a synthetic completed job."""
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
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle is not valid JSONL: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle must be UTF-8 text: {exc}") from exc

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
        current_stage=job.current_stage,
        completed_stages=job.completed_stages,
        stage_outputs=job.stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
    )


def _build_msa_batch_child_params(
    source_params: Dict[str, Any],
    sequences_for_msa: List[Dict[str, Any]],
    source_model_id: Any = None,
    source_mode: Any = None,
) -> Dict[str, Any]:
    child_params = {
        'sequences': sequences_for_msa,
        'sequences_json': json.dumps(sequences_for_msa),
        'reference_sequence': source_params.get('msa_reference_sequence'),
        'msa_force_refresh': source_params.get('msa_force_refresh', False),
        'msa_cache_only': source_params.get('msa_cache_only', False),
        'msa_use_gpu': source_params.get('msa_use_gpu', True),
        'msa_max_seqs': source_params.get('msa_max_seqs'),
        'msa_preset': source_params.get('msa_preset', 'fast'),
        'msa_use_expand': source_params.get('msa_use_expand'),
        'msa_use_env': source_params.get('msa_use_env'),
        'msa_num_iterations': source_params.get('msa_num_iterations'),
        'msa_evalue': source_params.get('msa_evalue'),
        'msa_min_seq_id': source_params.get('msa_min_seq_id'),
        'msa_min_coverage': source_params.get('msa_min_coverage'),
        'msa_taxon_list': source_params.get('msa_taxon_list'),
        'msa_min_depth_warning': source_params.get('msa_min_depth_warning'),
        'msa_min_depth_fail': source_params.get('msa_min_depth_fail'),
        'msa_gpu_mode': source_params.get('msa_gpu_mode'),
        'msa_gpu_threshold': source_params.get('msa_gpu_threshold'),
        'msa_preferred_gpus': source_params.get('msa_preferred_gpus'),
        'msa_excluded_gpus': source_params.get('msa_excluded_gpus'),
        'msa_gpu_server_mode': source_params.get('msa_gpu_server_mode'),
        'msa_gpu_server_wait_timeout': source_params.get('msa_gpu_server_wait_timeout'),
        'msa_gpu_server_db_load_mode': source_params.get('msa_gpu_server_db_load_mode'),
        'msa_gpu_server_startup_wait': source_params.get('msa_gpu_server_startup_wait'),
        'msa_local_db': source_params.get('msa_local_db'),
        'msa_cache_dir': source_params.get('msa_cache_dir'),
        'msa_threads': source_params.get('msa_threads'),
        'msa_target_shard_mode': source_params.get('msa_target_shard_mode'),
        'msa_target_shards': source_params.get('msa_target_shards'),
        'msa_target_shard_min_size_gb': source_params.get('msa_target_shard_min_size_gb'),
    }
    return child_params


def _standard_job_output_dir(name: str, timestamp: str, preallocated_job_id: str | None) -> Path:
    if preallocated_job_id is not None:
        return get_results_dir() / preallocated_job_id
    return get_results_dir() / f"{name}_{timestamp}"


async def _create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _preallocated_job_id: Any = Depends(lambda: None),
    _commit: Any = Depends(lambda: True),
    _md_output_creation: Any = Depends(lambda: None),
    _md_input_resolver: Any = Depends(lambda: None),
    _trusted_workflow_adapter: Any = Depends(lambda: False),
):
    """Create and queue a new pipeline job."""
    require_molecular_dynamics_feature(job_data.model_id)
    _raise_if_workflow_launches_disabled("create new workflow jobs")
    md_input_resolver: Callable[[str], str] = (
        cast(Callable[[str], str], _md_input_resolver)
        if callable(_md_input_resolver)
        else _resolve_md_input_path_for_runtime
    )
    # BindCraft is the only retired workflow.  The dedicated antibody/de-novo
    # launcher remains supported and resolves to workflows/antibody_denovo.nf.
    retired_model_ids = {
        "bind" + "craft",
    }
    retired_modes = {
        "bind" + "craft",
    }
    normalized_model_id = str(job_data.model_id or "").strip().lower()
    normalized_mode = str(job_data.mode or "").strip().lower()
    if normalized_model_id == "protein_modification_experimental" and normalized_mode == "region_redesign":
        try:
            job_data.params = normalize_plr_structure_validators(job_data.params or {})
            job_data.params = normalize_plr_input_pdb_path(job_data.params, resolve_relative=resolve_allowed_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        input_pdb = (job_data.params or {}).get("input_pdb")
        if isinstance(input_pdb, str) and input_pdb.strip() and not Path(input_pdb).is_file():
            raise HTTPException(
                status_code=422,
                detail=f"PLR input_pdb provisioned file is missing: {input_pdb}",
            )
    if normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":
        if "workflow_adapter" in (job_data.params or {}) and _trusted_workflow_adapter is not True:
            raise HTTPException(
                status_code=422,
                detail={
                    "local_redesign_contract_error": "workflow_adapter is server-owned for native RFD3"
                },
            )
        try:
            job_data.params = normalize_plr_input_pdb_path(job_data.params or {}, resolve_relative=resolve_allowed_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        input_pdb = (job_data.params or {}).get("input_pdb")
        if isinstance(input_pdb, str) and input_pdb.strip() and not Path(input_pdb).is_file():
            raise HTTPException(
                status_code=422,
                detail=f"PLR input_pdb provisioned file is missing: {input_pdb}",
            )
        pinned_gpu = job_data.pinned_gpu
        if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "local_redesign_contract_error": (
                        "native RFD3 local redesign requires one explicit non-negative pinned_gpu"
                    )
                },
            )
        from routers.gpu import get_gpu_stats_with_error

        live_gpus, gpu_error = await asyncio.to_thread(get_gpu_stats_with_error, True)
        valid_gpu_indices = sorted({int(gpu.index) for gpu in live_gpus})
        if gpu_error or pinned_gpu not in valid_gpu_indices:
            raise HTTPException(
                status_code=422,
                detail={
                    "local_redesign_contract_error": "native RFD3 pinned_gpu is absent from an error-free live physical GPU inventory",
                    "pinned_gpu": pinned_gpu,
                    "valid_gpu_indices": valid_gpu_indices,
                    "gpu_error": gpu_error,
                },
            )
    if normalized_model_id == "frustrampnn":
        raise HTTPException(
            status_code=422,
            detail="FrustraMPNN jobs must use the typed server-owned analysis endpoints.",
        )
    if normalized_model_id == "molecular_dynamics" and normalized_mode == "analyze" and _md_analysis_gpu_requested(job_data):
        raise _md_analysis_error(
            "MD_ANALYSIS_GPU_FORBIDDEN",
            "MD analysis children are CPU-only and cannot request a GPU assignment.",
        )
    if normalized_model_id == "conformational_mapping":
        raise HTTPException(
            status_code=403,
            detail=(
                "Generic conformational-mapping launch is disabled: this endpoint has no "
                "authenticated principal or server-owned artifact registry. Use a separately "
                "authorized typed/internal launcher."
            ),
        )
    if normalized_model_id in retired_model_ids or normalized_mode in retired_modes:
        raise HTTPException(status_code=410, detail="This retired workflow has been permanently removed.")
    if str(job_data.model_id or "").strip().lower() == "caliby_experimental":
        raise HTTPException(
            status_code=410,
            detail="Standalone Caliby is retired; select Caliby inside a supported parent design workflow.",
        )
    reserved_review_keys = {
        "review_profile_id",
        "review_contract_version",
        "review_contract_source",
        "review_role_map",
        "review_artifact_manifest",
    }
    forged_review_keys = sorted(reserved_review_keys.intersection(job_data.params or {}))
    if forged_review_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Review authority fields are server-controlled: {', '.join(forged_review_keys)}",
        )
    registry = get_registry()

    # Keep model schema in sync with disk changes during long-lived API sessions.
    try:
        registry.reload()
    except Exception as e:
        logger.warning(f"Failed to reload model registry before validation: {e}")

    if normalized_model_id == "protein_modification_experimental" and normalized_mode == "region_redesign":
        _validate_plr_validator_availability(registry, job_data.params or {})

    if job_data.model_id == "nanopore" and not ont_submission_trust.is_trusted_ont_job_creation():
        raise HTTPException(
            status_code=422,
            detail="Nanopore jobs must be submitted through the typed /api/ont/ngs submission endpoints",
        )
    capability_digest: str | None = None
    if job_data.model_id == "nanopore":
        capability_digest = ont_submission_trust.alignment_capability_digest()
        if not capability_digest or len(capability_digest) != 64:
            raise HTTPException(status_code=500, detail="trusted Nanopore submission is missing alignment authorization")

    if isinstance(job_data.params, dict):
        job_data.params = _normalize_nanopore_modbase_for_validation(
            registry,
            job_data.model_id,
            job_data.params,
        )
        job_data.params = _normalize_structure_prediction_pred_method(
            job_data.model_id,
            job_data.mode,
            job_data.params,
        )
        job_data.params = _normalize_frustrampnn_settings(
            job_data.model_id,
            job_data.mode,
            job_data.params,
        )
        # Convert browse-alias paths (e.g. downloads/...) to host absolute paths for runtime.
        job_data.params = _normalize_nanopore_runtime_paths(job_data.model_id, job_data.params)
        job_data.params = _normalize_antibody_runtime_paths(job_data.model_id, job_data.params)
        job_data.params = _normalize_structure_runtime_paths(job_data.model_id, job_data.params)
        job_data.params = _normalize_structure_geometry_params(job_data.params)
        job_data.params = _normalize_boltz_no_msa_quality_params(job_data.model_id, job_data.mode, job_data.params)
        is_protein_local_redesign = (
            normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign"
        ) or (
            normalized_model_id == "protein_modification_experimental" and normalized_mode == "region_redesign"
        )
        if not is_protein_local_redesign:
            job_data.params = _normalize_antibody_job_params(job_data.params)

        if normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":
            try:
                if "workflow_adapter" in job_data.params:
                    normalized_local_params = prepare_local_redesign_scheduler_params(
                        job_data.params,
                        job_name=job_data.name,
                    )
                else:
                    normalized_local_params, _local_request, _local_digest = normalize_local_redesign_params(
                        job_data.params,
                        job_name=job_data.name,
                    )
            except ContractError as exc:
                raise HTTPException(status_code=422, detail={"local_redesign_contract_error": str(exc)}) from exc
            job_data.params = normalized_local_params

        if job_data.model_id == "molecular_dynamics" and job_data.mode == "simulate":
            try:
                # Validate the caller-owned request without replacing it with the
                # server-resolved preview.  Materialization below performs the one
                # authoritative resolution after the durable job id/output root
                # exist.  Feeding the preview back into materialization would make
                # our own resolved chemistry fields look forged by the caller.
                normalize_md_job_spec(
                    params=job_data.params,
                    job_id="validation-preview",
                    resolve_runtime_path=md_input_resolver,
                )
            except (
                MDLaunchError,
                ChemistryCatalogError,
                ChemistryProfileSelectionError,
                OSError,
                SchemaError,
                ValueError,
            ) as exc:
                _raise_md_launch_http_error(exc)
    
    # Skip validation for template jobs and mutagenesis batches
    # Mutagenesis uses mutagenesis_variants array instead of top-level sequence
    validation_params = _normalize_boltz_cp_params_for_validation(job_data.model_id, job_data.params)
    is_mutagenesis = 'mutagenesis_variants' in job_data.params
    if not job_data.model_id.startswith('template_') and not is_mutagenesis:
        # Validate model and mode
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, validation_params)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

    _validate_protenix_template_requirements(job_data.model_id, job_data.params)
    _validate_protenix_checkpoint_requirements(job_data.model_id, job_data.params)
    _validate_fampnn_checkpoint_requirements(job_data.model_id, job_data.params)
    _validate_antibody_runtime_paths(job_data.model_id, job_data.params)
    if normalized_model_id == "molecular_dynamics" and normalized_mode == "analyze":
        await _validate_md_analysis_child(job_data, session)

    if job_data.parent_job_id and job_data.child_stage and job_data.name:
        existing_child_result = await session.execute(
            select(Job)
            .where(
                Job.parent_job_id == job_data.parent_job_id,
                Job.child_stage == job_data.child_stage,
                Job.name == job_data.name,
            )
            .order_by(Job.created_at.desc())
        )
        existing_children = existing_child_result.scalars().all()
        for existing_child in existing_children:
            if existing_child.status in {
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
            }:
                logger.info(
                    "[QUEUE] Existing child job %s for parent=%s stage=%s name=%s is %s; creating a new attempt",
                    existing_child.id,
                    job_data.parent_job_id,
                    job_data.child_stage,
                    job_data.name,
                    existing_child.status,
                )
                continue
            if not _child_job_has_reusable_outputs(existing_child):
                logger.info(
                    "[QUEUE] Existing child job %s for parent=%s stage=%s name=%s has no reusable final outputs in %s; creating a new attempt",
                    existing_child.id,
                    job_data.parent_job_id,
                    job_data.child_stage,
                    job_data.name,
                    existing_child.output_dir,
                )
                continue

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
                params=_public_job_params(existing_child),
                created_at=existing_child.created_at,
                started_at=existing_child.started_at,
                completed_at=existing_child.completed_at,
                output_dir=_public_job_output_dir(existing_child),
                error_message=existing_child.error_message,
                design_count=0,
                batch_id=existing_child.batch_id,
                batch_name=existing_child.batch_name,
                parent_job_id=existing_child.parent_job_id,
                child_stage=existing_child.child_stage,
                lineage_root_job_id=existing_child.lineage_root_job_id,
                stage_family=existing_child.stage_family,
                stage_mode=existing_child.stage_mode,
                source_stage_job_id=existing_child.source_stage_job_id,
                source_stage_family=existing_child.source_stage_family,
                source_stage_mode=existing_child.source_stage_mode,
                source_selection_manifest_path=existing_child.source_selection_manifest_path,
                source_selection_count=existing_child.source_selection_count,
                selected_input_artifact_class=existing_child.selected_input_artifact_class,
                selected_input_schema_version=existing_child.selected_input_schema_version,
                selection_source_type=existing_child.selection_source_type,
                selection_source_job_id=existing_child.selection_source_job_id,
                selection_dataset_name=existing_child.selection_dataset_name,
                selected_loop_scope=existing_child.selected_loop_scope,
                provenance=existing_child.provenance,
                saved_selection_sets=_serialized_saved_review_filter_sets(existing_child),
                pinned_gpu=existing_child.pinned_gpu,
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
        job_data.params['seq_design_caliby'] = False
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
    if job_data.model_id == "nanopore" and num_jobs != 1:
        raise HTTPException(status_code=422, detail="Nanopore submissions must create exactly one authorized job")

    # ColabFold API is the default for supported structure-prediction jobs.
    # Existing single-job validation below makes local MSA an explicit override for batches.
    default_msa_provider = _default_msa_provider_for_job(job_data.model_id, job_data.mode)
    msa_provider = str(job_data.params.get("msa_provider", default_msa_provider) or default_msa_provider).strip().lower()
    if msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid msa_provider '{msa_provider}'. Allowed: local, colabfold_api",
        )
    job_data.params["msa_provider"] = msa_provider

    if msa_provider == "colabfold_api":
        if not _supports_colabfold_api_single_job(job_data.model_id, job_data.mode):
            raise HTTPException(
                status_code=422,
                detail=(
                    "msa_provider=colabfold_api is currently supported only for single-job "
                    "structure launches (boltz2/rf3/protenix predict|complex, "
                    "boltz_cp_experimental design)."
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
    
    preallocated_job_id = _preallocated_job_id if isinstance(_preallocated_job_id, str) else None
    if preallocated_job_id is not None:
        if num_jobs != 1 or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            preallocated_job_id,
        ):
            raise HTTPException(status_code=500, detail="Invalid internal preallocated Job identity")

    # Create output directory (base for all jobs in batch). Internal typed
    # launchers use the deterministic Job ID so concurrent idempotent callers
    # cannot leave timestamp-split loser directories outside the DB transaction.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    is_md_launch = job_data.model_id == "molecular_dynamics" and job_data.mode == "simulate"
    md_output_dir_created = False
    if is_md_launch:
        try:
            md_output_path, md_output_dir_created = _prepare_md_output_dir(
                job_data.name, timestamp, preallocated_job_id,
            )
        except MDLaunchError as exc:
            _raise_md_launch_http_error(exc)
        base_output_dir = str(md_output_path)
        if isinstance(_md_output_creation, dict):
            _md_output_creation.update({"path": md_output_path, "created": md_output_dir_created})
    else:
        # Presentation-only names must not split one deterministic Job's
        # filesystem ownership across concurrent equivalent requests.
        base_output_dir = str(
            _standard_job_output_dir(job_data.name, timestamp, preallocated_job_id)
        )
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
    if is_antibody_pipeline_mode(job_data.mode) and str(job_data.params.get("parallel_mode") or "").strip().lower() == "full_orchestrator":
        vram_estimate = 0
        job_data.pinned_gpu = None
        logger.info(f"[QUEUE] Orchestrator parent job '{job_data.name}': CPU-only launcher, vram_estimate=0")
    if job_data.model_id == "molecular_dynamics" and job_data.mode in {"simulate", "analyze"}:
        vram_estimate = 0
        job_data.pinned_gpu = None
        logger.info(f"[QUEUE] MD {job_data.mode} job '{job_data.name}': CPU-only, vram_estimate=0")

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
        msa_job_id = str(uuid.uuid4())
        msa_output_dir = str(Path(base_output_dir) / "msa_batch")
        os.makedirs(msa_output_dir, exist_ok=True)
        
        msa_job = Job(
            id=msa_job_id,
            name=f"{job_data.name}_msa",
            model_id='msa_batch',
            mode='msa_generation',
            params=_build_msa_batch_child_params(
                source_params=job_data.params,
                sequences_for_msa=sequences_for_msa,
                source_model_id=job_data.model_id,
                source_mode=job_data.mode,
            ),
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
        job_id = preallocated_job_id or str(uuid.uuid4())
        
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
            job_params = dict(job_data.params)

        if is_md_launch:
            try:
                job_params = materialize_md_job_spec(
                    params=job_params,
                    job_id=job_id,
                    output_dir=Path(output_dir),
                    resolve_runtime_path=md_input_resolver,
                )
            except (
                MDLaunchError,
                ChemistryCatalogError,
                ChemistryProfileSelectionError,
                OSError,
                SchemaError,
                ValueError,
            ) as exc:
                _cleanup_call_owned_md_output(
                    Path(base_output_dir),
                    created=md_output_dir_created,
                )
                _raise_md_launch_http_error(exc)
            job_params["job_name"] = job_name

        if normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":
            try:
                job_params, _local_request, _local_digest, _local_request_path = materialize_local_redesign_request(
                    job_params,
                    output_dir=output_dir,
                    job_id=job_id,
                )
            except ContractError as exc:
                _cleanup_call_owned_md_output(
                    Path(base_output_dir),
                    created=md_output_dir_created,
                )
                raise HTTPException(status_code=422, detail={"local_redesign_contract_error": str(exc)}) from exc

        if isinstance(job_params, dict):
            job_params = _ensure_job_resume_identity(
                job_name=job_name,
                job_id=job_id,
                model_id=job_data.model_id,
                mode=job_data.mode,
                params=job_params,
            )
            resume_source_dir = _coerce_nonempty_text(job_params.get("resume_source_dir"))
            if resume_source_dir and num_jobs == 1:
                output_dir = str(Path(resume_source_dir).expanduser())

        if msa_job:
            sequence_for_hash = str(job_params.get('sequence') or job_params.get('sequence_input') or '')
            reference_for_hash = str(job_data.params.get('msa_reference_sequence') or '')
            hash_source = reference_for_hash or sequence_for_hash
            if hash_source:
                job_params = {
                    **job_params,
                    'msa_sequence_hash': hashlib.sha256(hash_source.encode()).hexdigest(),
                }

        provenance_lineage_root = _coerce_nonempty_text(
            job_params.get("lineage_root_job_id")
            or job_params.get("iteration_source_root_job_id")
            or job_params.get("resume_root_job_id")
            or job_data.parent_job_id
            or job_id
        )
        provenance_stage_family, provenance_stage_mode = _derive_job_stage_tags(
            job_data.model_id,
            job_data.mode,
            job_params if isinstance(job_params, dict) else {},
            job_data.child_stage,
        )
        provenance_selection_scope = _build_selected_loop_scope(job_params if isinstance(job_params, dict) else {})
        provenance_selection_source_type = _coerce_nonempty_text(
            job_params.get("selection_source_type") if isinstance(job_params, dict) else None
        )
        provenance_selection_source_job_id = _coerce_nonempty_text(
            job_params.get("selection_source_job_id")
            if isinstance(job_params, dict)
            else None
        ) or _coerce_nonempty_text(
            job_params.get("iteration_source_job_id") if isinstance(job_params, dict) else None
        ) or _coerce_nonempty_text(job_data.parent_job_id)
        provenance_selection_dataset_name = _coerce_nonempty_text(
            job_params.get("selection_dataset_name") if isinstance(job_params, dict) else None
        )
        provenance_source_stage_job_id = _coerce_nonempty_text(
            job_params.get("source_stage_job_id") if isinstance(job_params, dict) else None
        ) or _coerce_nonempty_text(
            job_params.get("selected_input_source_job_id") if isinstance(job_params, dict) else None
        )
        provenance_source_stage_family = _normalize_stage_family(
            job_params.get("source_stage_family") if isinstance(job_params, dict) else None
        ) or _normalize_stage_family(
            job_params.get("selected_input_stage_family") if isinstance(job_params, dict) else None
        )
        provenance_source_stage_mode = _normalize_stage_family(
            job_params.get("source_stage_mode") if isinstance(job_params, dict) else None
        ) or _normalize_stage_family(
            job_params.get("selected_input_stage_mode") if isinstance(job_params, dict) else None
        )
        provenance_source_selection_manifest_path = _coerce_nonempty_text(
            job_params.get("source_selection_manifest_path") if isinstance(job_params, dict) else None
        ) or _coerce_nonempty_text(
            job_params.get("selected_input_manifest") if isinstance(job_params, dict) else None
        )
        provenance_source_selection_count = _coerce_positive_int(
            job_params.get("source_selection_count") if isinstance(job_params, dict) else None
        )
        provenance_selected_input_artifact_class = normalize_antibody_artifact_class(
            job_params.get("selected_input_artifact_class") if isinstance(job_params, dict) else None
        )
        provenance_selected_input_schema_version = normalize_antibody_pipeline_contract_version(
            job_params.get("selected_input_schema_version") if isinstance(job_params, dict) else None
        )
        provenance_payload = {
            "job_id": job_id,
            "job_name": job_name,
            "model_id": job_data.model_id,
            "mode": job_data.mode,
            "ont_request_workflow_id": job_params.get("ont_request_workflow_id") if isinstance(job_params, dict) else None,
            "ont_workflow_id": job_params.get("ont_workflow_id") if isinstance(job_params, dict) else None,
            "ont_model_mode": job_params.get("ont_model_mode") if isinstance(job_params, dict) else None,
            "ont_input_mode": job_params.get("ont_input_mode") if isinstance(job_params, dict) else None,
            "ont_input_provenance": job_params.get("ont_input_provenance") if isinstance(job_params, dict) else None,
            "parent_job_id": job_data.parent_job_id,
            "child_stage": job_data.child_stage,
            "lineage_root_job_id": provenance_lineage_root,
            "stage_family": provenance_stage_family,
            "stage_mode": provenance_stage_mode,
            "selection_source_type": provenance_selection_source_type,
            "selection_source_job_id": provenance_selection_source_job_id,
            "selection_dataset_name": provenance_selection_dataset_name,
            "selected_loop_scope": provenance_selection_scope,
            "source_stage_job_id": provenance_source_stage_job_id,
            "source_stage_family": provenance_source_stage_family,
            "source_stage_mode": provenance_source_stage_mode,
            "source_selection_manifest_path": provenance_source_selection_manifest_path,
            "source_selection_count": provenance_source_selection_count,
            "selected_input_artifact_class": provenance_selected_input_artifact_class,
            "selected_input_schema_version": provenance_selected_input_schema_version,
            "iteration_action": job_params.get("iteration_action") if isinstance(job_params, dict) else None,
        }
        if job_data.model_id == "nanopore":
            provenance_payload[alignment_access.PROVENANCE_DIGEST_KEY] = capability_digest
            provenance_payload[alignment_access.PROVENANCE_SCHEME_KEY] = alignment_access.SCHEME

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
            lineage_root_job_id=provenance_lineage_root,
            stage_family=provenance_stage_family,
            stage_mode=provenance_stage_mode,
            source_stage_job_id=provenance_source_stage_job_id,
            source_stage_family=provenance_source_stage_family,
            source_stage_mode=provenance_source_stage_mode,
            source_selection_manifest_path=provenance_source_selection_manifest_path,
            source_selection_count=provenance_source_selection_count,
            selected_input_artifact_class=provenance_selected_input_artifact_class,
            selected_input_schema_version=provenance_selected_input_schema_version,
            selection_source_type=provenance_selection_source_type,
            selection_source_job_id=provenance_selection_source_job_id,
            selection_dataset_name=provenance_selection_dataset_name,
            selected_loop_scope=provenance_selection_scope,
            provenance=provenance_payload,
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
        if normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":
            local_request = job_params.get("rfd3_request") if isinstance(job_params, dict) else None
            local_request_id = job_params.get("rfd3_request_id") if isinstance(job_params, dict) else None
            if not isinstance(local_request, dict) or not local_request_id:
                raise HTTPException(status_code=500, detail="materialized local-redesign request is incomplete")
            session.add(
                RFD3LocalRedesignRequest(
                    request_id=str(local_request_id),
                    job_id=job.id,
                    schema_version=int(local_request.get("schema_version", 1)),
                    request_sha256=str(job_params.get("rfd3_request_sha256")),
                    profile_id=str(local_request.get("profile_id")),
                    profile_registry_sha256=str(local_request.get("profile_registry_sha256")),
                    redesign_mode=str(local_request.get("redesign_mode")),
                    sequence_policy=str(local_request.get("sequence_policy")),
                    status="prepared",
                    request_json=local_request,
                )
            )
        if is_md_launch and job_params.get("md_job_spec", {}).get("schema") == "bms.md.job.v2":
            await create_md_run(
                session,
                job=job,
                normalized_request=job_params["md_job_spec"],
            )
        if job_data.model_id == "molecular_dynamics" and job_data.mode == "replica":
            try:
                await create_replica_attempt(
                    session,
                    job_id=str(effective_parent_id or ""),
                    child_job_id=job.id,
                    replica_index=int(job_params["md_replica_index"]),
                    attempt=int(job_params.get("md_attempt", 0)),
                    engine=str(job_params["md_engine"]),
                    execution_plan_sha256=str(job_params["md_execution_plan_sha256"]),
                    compatibility_key=str(job_params["md_compatibility_key"]),
                )
            except (KeyError, TypeError, ValueError, MdStateError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": getattr(exc, "code", "MD_REPLICA_REGISTRATION_INVALID"),
                            "message": str(exc)},
                ) from exc
        created_jobs.append(job)
        
        if first_job is None:
            first_job = job
    
    if _commit is False:
        await session.flush()
    else:
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
        params=_public_job_params(first_job),
        created_at=first_job.created_at,
        started_at=first_job.started_at,
        completed_at=first_job.completed_at,
        output_dir=_public_job_output_dir(first_job),
        error_message=first_job.error_message,
        design_count=0,
        batch_id=first_job.batch_id,
        batch_name=first_job.batch_name,
        parent_job_id=first_job.parent_job_id,
        child_stage=first_job.child_stage,
        lineage_root_job_id=first_job.lineage_root_job_id,
        stage_family=first_job.stage_family,
        stage_mode=first_job.stage_mode,
        source_stage_job_id=first_job.source_stage_job_id,
        source_stage_family=first_job.source_stage_family,
        source_stage_mode=first_job.source_stage_mode,
        source_selection_manifest_path=first_job.source_selection_manifest_path,
        source_selection_count=first_job.source_selection_count,
        selected_input_artifact_class=first_job.selected_input_artifact_class,
        selected_input_schema_version=first_job.selected_input_schema_version,
        selection_source_type=first_job.selection_source_type,
        selection_source_job_id=first_job.selection_source_job_id,
        selection_dataset_name=first_job.selection_dataset_name,
        selected_loop_scope=first_job.selected_loop_scope,
        provenance=first_job.provenance,
        saved_selection_sets=_serialized_saved_review_filter_sets(first_job),
        pinned_gpu=first_job.pinned_gpu,
        awaiting_input=first_job.awaiting_input,
        awaiting_stage=first_job.awaiting_stage,
        awaiting_payload=first_job.awaiting_payload,
        decision_history=first_job.decision_history,
    )


def _launch_context_http_error(exc: LaunchContextError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _preallocated_job_id: Any = Depends(lambda: None),
    _commit: Any = Depends(lambda: True),
    _skip_parent_lineage_update: Any = Depends(lambda: False),
    experiment_session: AsyncSession = Depends(get_experiment_session),
) -> JobResponse:
    """Canonical Job submission, optionally bound by one opaque launch context."""
    launch_context_id = str(job_data.launch_context_id or "").strip()
    if not launch_context_id:
        return await _create_job(
            job_data,
            background_tasks,
            session,
            _preallocated_job_id,
            _commit,
            _skip_parent_lineage_update,
        )
    if current_launch_context_id.get() != launch_context_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "launch_context_transport_mismatch", "message": "Launch context header and body must match."},
        )
    if _commit is False:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "launch_context_noncanonical_submission",
                "message": "Launch contexts can bind only a committed canonical Job submission.",
            },
        )

    try:
        preview_context = await resolve_launch_context(experiment_session, launch_context_id)
        if preview_context.contract_version == "2":
            prepared_attempt = await experiment_session.get(ExperimentRunAttempt, preview_context.run_attempt_id)
            if prepared_attempt is None:
                raise LaunchContextError("launch_context_binding_invalid", "Reserved attempt is unavailable.", status_code=409)
            _preallocated_job_id = prepared_attempt.scheduler_job_id
        job_data.params = await validate_bound_job_request(
            experiment_session,
            preview_context,
            job_name=job_data.name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=dict(job_data.params or {}),
            pinned_gpu=job_data.pinned_gpu,
        )
        context, claim_token = await claim_launch_context(experiment_session, launch_context_id)
        await experiment_session.commit()
    except LaunchContextError as exc:
        await experiment_session.rollback()
        if exc.code == "launch_context_consumed":
            context = await resolve_launch_context_for_display(experiment_session, launch_context_id)
            if context.canonical_job_id:
                existing_job = await session.get(Job, context.canonical_job_id)
                if existing_job is not None:
                    await validate_bound_job(experiment_session, context, existing_job)
                    binding = json.loads(context.binding_receipt_json or "{}")
                    from routers.project_manager import _project_bound_job
                    await _project_bound_job(experiment_session, session, context, existing_job, binding)
                    await experiment_session.commit()
                    try:
                        await publish_launch_context_binding(
                            session,
                            context=context,
                            job=existing_job,
                            binding=binding,
                        )
                    except LaunchContextError as publish_exc:
                        raise _launch_context_http_error(publish_exc) from publish_exc
                    return JobResponse.model_validate(existing_job).model_copy(update={
                        "params": _public_job_params(existing_job),
                        "output_dir": _public_job_output_dir(existing_job),
                        "pinned_gpu": existing_job.pinned_gpu,
                        "launch_context_id": context.launch_context_id,
                        "launch_context_binding": binding,
                        "return_uri": context.return_uri,
                    })
        if exc.code == "launch_context_claimed":
            context = await resolve_launch_context_for_display(experiment_session, launch_context_id)
            tagged_jobs = (await session.scalars(
                select(Job).where(func.json_extract(Job.provenance, "$.launch_context_id") == launch_context_id).limit(2)
            )).all()
            if len(tagged_jobs) == 1 and context.claim_token:
                existing_job = tagged_jobs[0]
                await validate_bound_job(experiment_session, context, existing_job)
                context, binding = await consume_launch_context(
                    experiment_session,
                    launch_context_id=launch_context_id,
                    claim_token=context.claim_token,
                    canonical_job_id=existing_job.id,
                    canonical_batch_id=None,
                )
                from routers.project_manager import _project_bound_job
                await _project_bound_job(experiment_session, session, context, existing_job, binding)
                await experiment_session.commit()
                try:
                    await publish_launch_context_binding(
                        session,
                        context=context,
                        job=existing_job,
                        binding=binding,
                    )
                except LaunchContextError as publish_exc:
                    raise _launch_context_http_error(publish_exc) from publish_exc
                return JobResponse.model_validate(existing_job).model_copy(update={
                    "params": _public_job_params(existing_job),
                    "output_dir": _public_job_output_dir(existing_job),
                    "pinned_gpu": existing_job.pinned_gpu,
                    "launch_context_id": context.launch_context_id,
                    "launch_context_binding": binding,
                    "return_uri": context.return_uri,
                })
        raise _launch_context_http_error(exc) from exc

    try:
        response = await _create_job(
            job_data,
            background_tasks,
            session,
            _preallocated_job_id,
            _commit,
            _skip_parent_lineage_update,
            None,
            True,
        )
    except HTTPException:
        await session.rollback()
        try:
            await release_launch_context_claim(
                experiment_session,
                launch_context_id=launch_context_id,
                claim_token=claim_token,
            )
            await experiment_session.commit()
        except Exception:
            await experiment_session.rollback()
            logger.exception("Failed to release launch-context claim after rejected Job submission")
        raise
    except Exception:
        await session.rollback()
        # Unknown post-commit failures keep the durable claim fail-closed.
        raise

    try:
        created_job = await session.get(Job, str(response.id))
        if created_job is None:
            raise LaunchContextError("launch_context_job_unavailable", "Created Job cannot be resolved.", status_code=409)
        await validate_bound_job(experiment_session, context, created_job)
        consumed, binding = await consume_launch_context(
            experiment_session,
            launch_context_id=launch_context_id,
            claim_token=claim_token,
            canonical_job_id=str(response.id),
            canonical_batch_id=response.batch_id,
        )
        from routers.project_manager import _project_bound_job
        created_job = await session.get(Job, str(response.id))
        if created_job is None:
            raise LaunchContextError("launch_context_job_unavailable", "Created Job cannot be projected.", status_code=409)
        await _project_bound_job(experiment_session, session, consumed, created_job, binding)
        await experiment_session.commit()
        await publish_launch_context_binding(
            session,
            context=consumed,
            job=created_job,
            binding=binding,
        )
    except LaunchContextError as exc:
        await experiment_session.rollback()
        try:
            await release_launch_context_claim(
                experiment_session,
                launch_context_id=launch_context_id,
                claim_token=claim_token,
            )
            await experiment_session.commit()
        except Exception:
            await experiment_session.rollback()
        # The Job may already be durable. Never claim experiment membership without
        # the verified binding receipt; the claimed context remains fail-closed.
        raise _launch_context_http_error(exc) from exc

    return response.model_copy(
        update={
            "launch_context_id": consumed.launch_context_id,
            "launch_context_binding": binding,
            "return_uri": context.return_uri,
        }
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
            create_child_job as create_frustrampnn_child_job,
            design_selections as resolve_frustrampnn_selections,
        )

        try:
            selections = await resolve_frustrampnn_selections(
                session,
                source_parent=source_job,
                design_ids=design_ids,
            )
            child = await create_frustrampnn_child_job(
                session,
                selections=selections,
                source_parent=source_job,
                trigger="antibody_iteration",
                requested_settings=(
                    request.frustrampnn_settings or default_frustrampnn_settings()
                ),
            )
        except FrustraMPNNChildError as exc:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        launched_job = await get_job(str(child.id), session)
        return AntibodyIterationLaunchResponse(
            message=f"Queued FrustraMPNN analysis for {len(ordered_designs)} selected designs.",
            action=action,
            source_job_id=source_job.id,
            root_job_id=root_job.id,
            selection_dir=str(child.output_dir),
            selected_design_count=len(ordered_designs),
            launched_job=launched_job,
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


def _rfd3_public_json(value: Any, *, field: str | None = None) -> Any:
    """Project stored RFD3 provenance without exposing absolute host paths."""

    if isinstance(value, dict):
        return {key: _rfd3_public_json(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_rfd3_public_json(item, field=field) for item in value]
    field_name = str(field or "").lower()
    is_path_field = field_name in {
        "input",
        "input_structure",
        "input_pdb",
        "input_cif",
        "plr_input_pdb",
        "rfd3_request_path",
        "path",
        "paths",
        "file",
        "files",
        "dir",
        "directory",
        "directories",
        "filepath",
        "filepaths",
        "dirname",
        "dirnames",
        "storage_path",
    } or field_name.endswith(
        (
            "_path",
            "_paths",
            "_file",
            "_files",
            "_dir",
            "_directory",
            "_directories",
            "_filepath",
            "_filepaths",
            "_dirname",
            "_dirnames",
        )
    )
    if isinstance(value, str) and is_path_field:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate.name
    return value


def _is_native_rfd3_job(job: Any) -> bool:
    return str(job.model_id or "").strip().lower() == "protein_local_redesign"


def _public_job_output_dir(job: Any) -> str | None:
    if _is_native_rfd3_job(job):
        return None
    return job.output_dir


def _public_job_params(job: Any) -> dict[str, Any]:
    from services.execution_ownership import strip_execution_metadata
    from services.resource_usage_evidence import strip_resource_execution_metadata

    params = strip_execution_metadata(strip_resource_execution_metadata(job.params))
    if _is_native_rfd3_job(job):
        return _rfd3_public_json(params)
    return params


@router.get("/{job_id}/rfd3-local-redesign")
async def get_rfd3_local_redesign_result(job_id: str, session: AsyncSession = Depends(get_session)):
    """Return the typed local-redesign request and result projection."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or str(job.model_id or "").strip().lower() != "protein_local_redesign":
        raise HTTPException(status_code=404, detail="RFD3 local-redesign job not found")
    request = (
        await session.execute(select(RFD3LocalRedesignRequest).where(RFD3LocalRedesignRequest.job_id == job_id))
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="RFD3 local-redesign request is not available")
    candidates = (
        await session.execute(
            select(RFD3LocalRedesignCandidate)
            .where(RFD3LocalRedesignCandidate.request_id == request.request_id)
            .order_by(RFD3LocalRedesignCandidate.candidate_id)
        )
    ).scalars().all()
    artifacts = (
        await session.execute(
            select(RFD3LocalRedesignArtifact)
            .where(RFD3LocalRedesignArtifact.request_id == request.request_id)
            .order_by(RFD3LocalRedesignArtifact.relative_path)
        )
    ).scalars().all()
    artifact_roles = {row.role for row in artifacts}
    candidate_ids = {row.candidate_id for row in candidates}
    trajectory_roles_by_candidate = {
        candidate_id: {
            row.role
            for row in artifacts
            if row.candidate_id == candidate_id and row.role in {"denoised_trajectory", "noisy_trajectory"}
        }
        for candidate_id in candidate_ids
    }
    request_execution = request.request_json.get("execution", {}) if isinstance(request.request_json, dict) else {}
    trajectories_requested = isinstance(request_execution, dict) and request_execution.get("dump_trajectories") is True
    trajectories_available = bool(candidate_ids) and all(
        roles == {"denoised_trajectory", "noisy_trajectory"}
        for roles in trajectory_roles_by_candidate.values()
    )
    public_request = _rfd3_public_json(request.request_json)
    return {
        "schema": "bms.rfd3.local-redesign.read-model.v1",
        "job_id": str(job.id),
        "capabilities": {
            "source_structure": "source_structure" in artifact_roles,
            "candidate_structures": bool(candidate_ids) and all(
                any(row.candidate_id == candidate_id and row.role == "structure" for row in artifacts)
                for candidate_id in candidate_ids
            ),
            "native_metadata": bool(candidate_ids) and all(
                any(row.candidate_id == candidate_id and row.role == "native_prediction_metadata" for row in artifacts)
                for candidate_id in candidate_ids
            ),
            "trajectories": {
                "requested": trajectories_requested,
                "available": trajectories_available,
                "reason": (
                    "produced"
                    if trajectories_available
                    else "not_requested"
                    if not trajectories_requested
                    else "requested_artifacts_unavailable"
                ),
            },
        },
        "request": {
            "request_id": request.request_id,
            "schema_version": request.schema_version,
            "request_sha256": request.request_sha256,
            "profile_id": request.profile_id,
            "profile_registry_sha256": request.profile_registry_sha256,
            "redesign_mode": request.redesign_mode,
            "sequence_policy": request.sequence_policy,
            "status": request.status,
            "request": public_request,
            "request_path_scope": "basename",
            "provenance_path_scope": "basename",
            "preparation_receipt": _rfd3_public_json(request.preparation_receipt_json),
            "runtime_identity": _rfd3_public_json(request.runtime_identity_json),
            "result_manifest_sha256": request.result_manifest_sha256,
            "failure_receipt": _rfd3_public_json(request.failure_receipt_json),
            "created_at": request.created_at.isoformat() if request.created_at else None,
            "updated_at": request.updated_at.isoformat() if request.updated_at else None,
            "terminal_at": request.terminal_at.isoformat() if request.terminal_at else None,
        },
        "candidates": [
            {
                "candidate_id": row.candidate_id,
                "result_set": row.result_set,
                "stage": row.stage,
                "status": row.status,
                "artifact_manifest_sha256": row.artifact_manifest_sha256,
                "metrics": _rfd3_public_json(row.metrics_json),
                "metadata": _rfd3_public_json(row.metadata_json),
            }
            for row in candidates
        ],
        "artifacts": [
            {
                "artifact_id": row.artifact_id,
                "candidate_id": row.candidate_id,
                "role": row.role,
                "relative_path": row.relative_path,
                "sha256": row.content_sha256,
                "bytes": row.size_bytes,
                "media_type": row.media_type,
                "metadata": _rfd3_public_json(row.metadata_json),
            }
            for row in artifacts
        ],
    }


@router.get("/{job_id}/rfd3-local-redesign/artifacts/{artifact_id}")
async def get_rfd3_local_redesign_artifact(
    job_id: str,
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Stream one hash-checked local-redesign artifact."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or str(job.model_id or "").strip().lower() != "protein_local_redesign":
        raise HTTPException(status_code=404, detail="RFD3 local-redesign job not found")
    artifact = (
        await session.execute(
            select(RFD3LocalRedesignArtifact).where(
                RFD3LocalRedesignArtifact.artifact_id == artifact_id,
                RFD3LocalRedesignArtifact.request_id.in_(
                    select(RFD3LocalRedesignRequest.request_id).where(RFD3LocalRedesignRequest.job_id == job_id)
                ),
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="RFD3 local-redesign artifact not found")

    if artifact.role == "source_structure":
        stored_source = Path(artifact.storage_path).expanduser()
        if stored_source.is_symlink():
            raise HTTPException(status_code=409, detail="RFD3 local-redesign source artifact path contains a symlink")
        path = resolve_runtime_data_path(stored_source)
        data_root = get_data_root().resolve()
        if not path.is_relative_to(data_root) or path != stored_source.resolve():
            raise HTTPException(status_code=409, detail="RFD3 local-redesign source artifact path binding is invalid")
    else:
        output_root = resolve_runtime_data_path(str(job.output_dir or ""))
        relative = Path(artifact.relative_path)
        if (
            relative.is_absolute()
            or relative.as_posix() != artifact.relative_path
            or any(part in {"", ".", ".."} for part in relative.parts)
            or "\\" in artifact.relative_path
        ):
            raise HTTPException(status_code=409, detail="RFD3 local-redesign artifact path is unsafe")
        current = output_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise HTTPException(status_code=409, detail="RFD3 local-redesign artifact path contains a symlink")
        path = current.resolve()
        if not path.is_relative_to(output_root.resolve()) or path != Path(artifact.storage_path).resolve():
            raise HTTPException(status_code=409, detail="RFD3 local-redesign artifact path binding is invalid")

    if not path.is_file() or path.is_symlink() or path.stat().st_size != artifact.size_bytes:
        raise HTTPException(status_code=410, detail="RFD3 local-redesign artifact is unavailable")
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.content_sha256:
        raise HTTPException(status_code=409, detail="RFD3 local-redesign artifact hash mismatch")
    response_media_type = "chemical/x-mmcif" if path.name.endswith(".cif.gz") else artifact.media_type
    response_headers = {"Content-Encoding": "gzip"} if path.name.endswith(".cif.gz") else None
    return FileResponse(
        str(path),
        media_type=response_media_type,
        filename=path.name,
        headers=response_headers,
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

    # Get design count
    design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
    design_count = (await session.execute(design_count_query)).scalar()
    if (design_count or 0) == 0:
        child_design_count_query = (
            select(func.count(Design.id))
            .select_from(Job)
            .join(Design, Design.job_id == Job.id)
            .where(Job.parent_job_id == job.id)
        )
        child_design_count = (await session.execute(child_design_count_query)).scalar()
        if child_design_count:
            design_count = child_design_count
    review_count = _review_candidate_count(job)
    if (design_count or 0) == 0 and review_count is not None:
        design_count = review_count
    result_output_dir = job.child_output_dir or job.output_dir
    if (design_count or 0) == 0 and job.status in [JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value] and result_output_dir:
        design_count = count_structure_files(result_output_dir)
    completed_stages, stage_outputs = _resolve_stage_state_for_response(job)
    frustrampnn_result_count = int((await session.execute(
        select(func.count(FrustraMPNNResult.invocation_id)).where(FrustraMPNNResult.parent_job_id == job.id)
    )).scalar_one())

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
        current_stage=job.current_stage,
        completed_stages=completed_stages,
        stage_outputs=stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
        frustrampnn_result_count=frustrampnn_result_count,
        frustrampnn_reopen_destination=(
            {"surface": "frustrampnn-workbench", "params": {"job_id": job.id}}
            if frustrampnn_result_count else None
        ),
    )


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
    try:
        await cancel_job_lineage(job_id, session, error_message="Deleted by user")
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
    child_result = await session.execute(
        select(Job).where(Job.parent_job_id == job_id)
    )
    child_jobs = child_result.scalars().all()
    
    child_output_dirs = []
    for child in child_jobs:
        if child.output_dir:
            child_output_dirs.append(child.output_dir)

    delete_job_ids = {job.id, *(str(child.id) for child in child_jobs)}
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
        await session.delete(child)
    
    # Delete the job itself
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


@router.post("/{job_id}/resubmit")
async def resubmit_job(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session)
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
    os.makedirs(output_dir, exist_ok=True)
    
    resubmit_params = deepcopy(original_job.params) if isinstance(original_job.params, dict) else {}
    resubmit_params = _normalize_nanopore_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_antibody_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_structure_runtime_paths(original_job.model_id, resubmit_params)
    resubmit_params = _normalize_structure_geometry_params(resubmit_params)
    resubmit_params = _normalize_antibody_job_params(resubmit_params)
    if resubmit_params.get("msa_force_refresh") is True:
        # Resubmits should reuse cache by default unless user explicitly
        # starts a fresh job with force-refresh enabled.
        resubmit_params["msa_force_refresh"] = False
        logger.info(f"[RESUBMIT] Cleared msa_force_refresh for resubmitted job {job_id}")

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
    resubmit_selected_input_artifact_class = normalize_antibody_artifact_class(
        resubmit_params.get("selected_input_artifact_class")
    )
    resubmit_selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        resubmit_params.get("selected_input_schema_version")
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
        selected_input_artifact_class=resubmit_selected_input_artifact_class,
        selected_input_schema_version=resubmit_selected_input_schema_version,
        # GPU Orchestrator fields - let orchestrator pick it up
        queue_status='queued',
        vram_estimate_mb=resubmit_vram_estimate,
        sequence_length=resubmit_sequence_length,
        priority=0,
        paused=False,
        retry_count=0,
        max_retries=2,
    )

    if new_job.model_id == "nanopore":
        new_job.provenance = alignment_access.grant_alignment_access(
            new_job.id,
            new_job.provenance,
            response,
            request,
        )
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


def _anchor_dorado_demux_products(job: Job) -> dict[str, Any]:
    """Build the immutable server-side anchor for one terminal Dorado demux stage."""
    if job.model_id != "nanopore" or job.mode != "basecall_dna" or str((job.params or {}).get("barcode_kit") or "") != "SQK-RBK114-96":
        raise HTTPException(status_code=422, detail="dorado_demux is valid only for locked barcoded DNA jobs")
    root = Path(str(job.output_dir or "")).expanduser()
    if root.is_symlink():
        raise HTTPException(status_code=409, detail="Dorado result root symlink is forbidden")
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Dorado result root is unavailable") from exc
    expected = {
        "demux_manifest": root / "demux" / "demux_manifest.json",
        "barcode_units_manifest": root / "demux" / "per_barcode_units.json",
        "dorado_preflight": root / "basecall" / "dorado_preflight.json",
        "dorado_runtime_provenance": root / "basecall" / "dorado_runtime_provenance.json",
    }
    for label, path in expected.items():
        if path.is_symlink() or not path.is_file():
            raise HTTPException(status_code=409, detail=f"terminal Dorado product is unavailable or unsafe: {label}")
        try:
            resolved_product = path.resolve(strict=True)
            relative_product = resolved_product.relative_to(root)
            cursor = root
            for part in relative_product.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError(f"terminal Dorado product contains a symlink: {label}")
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"terminal Dorado product escapes result root: {label}") from exc
    try:
        product_bytes = {label: path.read_bytes() for label, path in expected.items()}
        product_digests = {
            label: hashlib.sha256(payload).hexdigest() for label, payload in product_bytes.items()
        }
        demux = json.loads(product_bytes["demux_manifest"].decode("utf-8"))
        preflight = json.loads(product_bytes["dorado_preflight"].decode("utf-8"))
        runtime = json.loads(product_bytes["dorado_runtime_provenance"].decode("utf-8"))
        unit_catalog = json.loads(product_bytes["barcode_units_manifest"].decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="terminal Dorado product is unreadable or malformed") from exc
    if not all(isinstance(payload, dict) for payload in (demux, preflight, runtime, unit_catalog)):
        raise HTTPException(status_code=409, detail="terminal Dorado product documents must be JSON objects")
    # Narrow JSON values for both runtime safety and static analysis.
    demux = dict(demux)
    preflight = dict(preflight)
    runtime = dict(runtime)
    unit_catalog = dict(unit_catalog)
    preflight_sha256 = product_digests["dorado_preflight"]
    params = dict(job.params or {})
    expected_lock_sha256 = str(params.get("dorado_lock_sha256") or "").strip().lower()
    expected_model_id = str(params.get("dorado_resolved_model_id") or "").strip()
    expected_mode = str(params.get("dorado_basecall_mode") or "").strip().lower()
    try:
        approved_lock_bytes = ont_ngs_contract.DORADO_LOCK_PATH.read_bytes()
        approved_lock = json.loads(approved_lock_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="approved Dorado lock is unavailable or malformed") from exc
    if not isinstance(approved_lock, dict) or hashlib.sha256(approved_lock_bytes).hexdigest() != expected_lock_sha256:
        raise HTTPException(status_code=409, detail="terminal Dorado job lock is not the approved lock")
    approved_dorado = approved_lock.get("dorado")
    approved_models = approved_lock.get("models")
    if not isinstance(approved_dorado, dict) or not isinstance(approved_models, dict):
        raise HTTPException(status_code=409, detail="approved Dorado lock has invalid runtime/model sections")
    approved_dna_models = approved_models.get("dna")
    if not isinstance(approved_dna_models, dict):
        raise HTTPException(status_code=409, detail="approved Dorado lock has no DNA model section")
    approved_model = next(
        (entry for entry in approved_dna_models.values() if isinstance(entry, dict) and entry.get("id") == expected_model_id),
        None,
    )
    if not isinstance(approved_model, dict):
        raise HTTPException(status_code=409, detail="terminal Dorado model is not retained by the approved lock")
    preflight_lock = preflight.get("lock")
    if not isinstance(preflight_lock, dict):
        preflight_lock = {}
    preflight_lock_sha256 = str(preflight_lock.get("sha256") or "").lower()
    preflight_selection = preflight.get("selection")
    if not isinstance(preflight_selection, dict):
        preflight_selection = {}
    preflight_runtime = preflight.get("runtime")
    if not isinstance(preflight_runtime, dict):
        preflight_runtime = {}
    runtime_assets = preflight_runtime.get("assets")
    if not isinstance(runtime_assets, dict):
        runtime_assets = {}
    runtime_sif = runtime_assets.get("runtime_sif")
    if not isinstance(runtime_sif, dict):
        runtime_sif = {}
    runtime_calls = runtime.get("calls_bam")
    if not isinstance(runtime_calls, dict):
        runtime_calls = {}
    demux_source = demux.get("source_calls")
    if not isinstance(demux_source, dict):
        demux_source = {}
    anchored_read_count = runtime_calls.get("read_count")
    if isinstance(anchored_read_count, bool) or not isinstance(anchored_read_count, int) or anchored_read_count < 0:
        raise HTTPException(status_code=409, detail="terminal Dorado calls read count is invalid")
    preflight_barcoding = preflight.get("barcoding")
    if not isinstance(preflight_barcoding, dict):
        preflight_barcoding = {}
    demux_units = demux.get("units")
    catalog_units = unit_catalog.get("units")
    if (
        demux.get("schema") != "biomodstack.dorado_demux.v1"
        or preflight.get("schema") != "biomodstack.dorado_preflight.v1"
        or runtime.get("schema") != "biomodstack.dorado_runtime_provenance.v1"
        or unit_catalog.get("schema") != "biomodstack.dorado_barcode_units.v1"
        or demux.get("preflight_sha256") != preflight_sha256
        or runtime.get("preflight_sha256") != preflight_sha256
        or not re.fullmatch(r"[0-9a-f]{64}", expected_lock_sha256)
        or preflight_lock_sha256 != expected_lock_sha256
        or not expected_model_id
        or preflight_selection.get("model_id") != expected_model_id
        or preflight_selection.get("molecule") != "dna"
        or preflight_selection.get("model_aggregate_sha256") != approved_model.get("aggregate_sha256")
        or preflight_selection.get("quality") != params.get("dorado_quality_mode")
        or preflight_selection.get("modified_bases") != "none"
        or preflight_selection.get("modified_bases_model_id") is not None
        or preflight_selection.get("stereo_model_id") is not None
        or runtime.get("model_id") != expected_model_id
        or expected_mode != "simplex"
        or preflight_selection.get("mode") != expected_mode
        or runtime.get("mode") != expected_mode
        or preflight_barcoding.get("kit") != params.get("barcode_kit")
        or preflight_runtime.get("version") != approved_dorado.get("version")
        or runtime.get("runtime_sha256") != approved_dorado.get("sif_sha256")
        or runtime_sif.get("sha256") != runtime.get("runtime_sha256")
        or preflight_runtime.get("sif_sha256") != runtime.get("runtime_sha256")
        or runtime_calls.get("sha256") != demux_source.get("sha256")
        or runtime_calls.get("read_count") != demux_source.get("read_count")
        or runtime_calls.get("read_count") != demux.get("total_reads")
        or not isinstance(demux_units, list)
        or demux_units != catalog_units
    ):
        raise HTTPException(status_code=409, detail="terminal Dorado product identities are inconsistent")
    try:
        anchored_units = load_barcode_units(
            expected["demux_manifest"],
            root,
            expected_manifest_sha256=product_digests["demux_manifest"],
            expected_source_calls_sha256=str(runtime_calls.get("sha256") or ""),
            expected_preflight_sha256=preflight_sha256,
        )
        catalog_verified_units = load_barcode_units(
            expected["barcode_units_manifest"],
            root,
            expected_manifest_sha256=product_digests["barcode_units_manifest"],
            expected_source_calls_sha256=str(runtime_calls.get("sha256") or ""),
            expected_preflight_sha256=preflight_sha256,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="terminal Dorado barcode units are inconsistent") from exc
    identity_fields = (
        "unit_id", "bam_sha256", "read_count", "unit_manifest_sha256",
        "source_calls_sha256", "preflight_sha256",
    )
    if [tuple(unit[field] for field in identity_fields) for unit in anchored_units] != [
        tuple(unit[field] for field in identity_fields) for unit in catalog_verified_units
    ]:
        raise HTTPException(status_code=409, detail="terminal Dorado unit catalog does not match demux products")
    return {
        "schema": "biomodstack.ont_dorado_terminal_products.v1",
        "stage": "dorado_demux",
        "identities": {
            "lock_sha256": expected_lock_sha256,
            "model_id": expected_model_id,
            "mode": expected_mode,
            "runtime_sha256": str(runtime.get("runtime_sha256")),
            "calls_bam_sha256": str(runtime_calls.get("sha256")),
            "read_count": anchored_read_count,
            "unit_count": len(anchored_units),
        },
        "products": {
            label: {"path": path.relative_to(root).as_posix(), "sha256": product_digests[label]}
            for label, path in expected.items()
        },
    }


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
    
    # Update completed stages
    completed = job.completed_stages or []
    if stage not in completed:
        completed.append(stage)
    job.completed_stages = completed
    
    # Update stage outputs
    stage_outputs = job.stage_outputs or {}
    stage_outputs[stage] = outputs
    job.stage_outputs = stage_outputs

    provenance = dict(job.provenance or {})
    terminal_states = dict(provenance.get("stage_terminal_states") or {})
    existing_terminal = terminal_states.get(stage)
    complete_terminal = {"status": "complete", "outputs": list(outputs)}
    if existing_terminal is not None and existing_terminal != complete_terminal:
        raise HTTPException(status_code=409, detail="workflow stage terminal state is immutable")
    terminal_states[stage] = complete_terminal
    provenance["stage_terminal_states"] = terminal_states
    job.provenance = provenance
    
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

    completed = list(job.completed_stages or [])
    if stage in completed:
        raise HTTPException(status_code=409, detail="completed workflow stage cannot be reclassified")
    terminal = {"status": status, "outputs": list(outputs)}
    provenance = dict(job.provenance or {})
    terminal_states = dict(provenance.get("stage_terminal_states") or {})
    existing = terminal_states.get(stage)
    if existing is not None and existing != terminal:
        raise HTTPException(status_code=409, detail="workflow stage terminal state is immutable")
    terminal_states[stage] = terminal
    provenance["stage_terminal_states"] = terminal_states
    job.provenance = provenance

    stage_outputs = dict(job.stage_outputs or {})
    stage_outputs[stage] = list(outputs)
    job.stage_outputs = stage_outputs
    if job.current_stage == stage:
        job.current_stage = None
    await session.commit()
    logger.info("Job %s: Stage '%s' terminal state is %s", job_id, stage, status)
    return {
        "message": f"Stage '{stage}' marked {status}",
        "job_id": job_id,
        "stage": stage,
        "status": status,
        "outputs_count": len(outputs),
    }


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

    payload = dict(request.payload or {}) if request else {}
    payload["stage"] = stage
    payload = refresh_gate_payload(payload, job.output_dir)

    job.awaiting_input = True
    job.awaiting_stage = stage
    job.awaiting_payload = payload
    job.current_stage = stage
    _write_gate_snapshot(job)
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

    result = await session.execute(query)
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


@router.post("/{job_id}/stage-start")
async def report_stage_start(
    job_id: str,
    request: Request,
    stage: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has started.
    Called by Nextflow workflows when entering a new stage.
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
    
    if job.mode in ["antibody_denovo"] or is_antibody_pipeline_mode(job.mode):
        # Dynamic stage construction for antibody workflow
        display_stages.append("rfantibody")
        
        # Check params for sequence design steps (default to true if not present, matching nextflow logic)
        params = _normalize_antibody_job_params(_normalize_structure_geometry_params(job.params or {}))
        
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

        run_caliby = params.get("seq_design_caliby")
        if run_caliby is True:
            display_stages.append("caliby")

        if params.get("run_maturation") is True:
            display_stages.append("maturation")
            ppiflow_mode = str(params.get("ppiflow_stage_mode") or "").strip().lower()
            iteration_action = str(params.get("iteration_action") or "").strip().lower()
            if ppiflow_mode == "backbone_refine" or iteration_action == "ppiflow_backbone_refine":
                display_stages.append("ppiflow_backbone_refine")
            if ppiflow_mode == "maturation" or iteration_action == "ppiflow_maturation":
                display_stages.append("ppiflow_maturation")
            
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
        # Nanopore stage inventory is dynamic across every typed ONT mode.
        if _uses_nanopore_stage_response(job):
            display_stages = _planned_nanopore_stages(job.params)
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

    if _uses_nanopore_stage_response(job):
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
    _raise_if_workflow_launches_disabled("resume workflow jobs")
    await reject_generic_md_lifecycle_control(job_id, session)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.model_id == "nanopore":
        if not alignment_access.request_is_authorized(request_context, job.id, job.provenance):
            raise HTTPException(status_code=403, detail="alignment access denied")
    
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

    if job.awaiting_input:
        awaiting_payload = dict(job.awaiting_payload or {})
        candidate_dir = awaiting_payload.get("candidate_dir")
        output_path = Path(job.output_dir)
        if not output_path.is_absolute():
            output_path = get_data_root() / output_path
        if candidate_dir and _is_protein_local_redesign_job(job):
            if job.awaiting_stage == "post_rfantibody":
                param_overrides.setdefault("plr_backbone_input_pdbs", candidate_dir)
                param_overrides.setdefault(
                    "plr_region_manifest",
                    str(output_path / "inputs" / "protein_local_redesign" / "region_manifest.json"),
                )
            elif job.awaiting_stage == "post_fampnn":
                param_overrides.setdefault("plr_sequence_input_pdbs", candidate_dir)
            elif job.awaiting_stage == "post_structure_validation":
                param_overrides.setdefault("plr_validation_input_pdbs", candidate_dir)
                param_overrides.setdefault("plr_final_candidate_dir", candidate_dir)
        else:
            if candidate_dir and job.awaiting_stage == "post_rfantibody":
                param_overrides.setdefault("rfantibody_input_pdbs", candidate_dir)
            if candidate_dir and job.awaiting_stage == "post_fampnn":
                param_overrides.setdefault("fampnn_collected_pdbs", candidate_dir)
            if candidate_dir and job.awaiting_stage == "post_caliby":
                param_overrides.setdefault("selected_input_dir", candidate_dir)
                param_overrides.setdefault("selected_input_stage_family", "caliby")
                param_overrides.setdefault("selected_input_stage_mode", "post_caliby")
                param_overrides.setdefault("selected_input_artifact_class", SEQUENCE_DESIGNED_COMPLEX)
                param_overrides.setdefault("selected_input_schema_version", ANTIBODY_PIPELINE_CONTRACT_VERSION)
        if job.awaiting_stage in {"post_rfantibody", "post_ppiflow_generator", "post_fampnn", "post_caliby", "post_structure_validation"}:
            param_overrides.setdefault("interactive_gate_continue", True)
            param_overrides.setdefault("interactive_swa", _to_bool((job.params or {}).get("interactive_swa")))
            param_overrides.setdefault("interactive_gating", _to_bool((job.params or {}).get("interactive_gating")))
        if not effective_from_stage:
            effective_from_stage = _awaiting_stage_to_resume_hint(job.awaiting_stage)

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

        launched_job = await create_job(launch_request, background_tasks, session)
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
    output_dir = job.output_dir
    os.makedirs(output_dir, exist_ok=True)
    
    merged_params = {
        **_normalize_antibody_job_params(_normalize_structure_geometry_params(job.params or {})),
        **param_overrides,
    }
    merged_params = _ensure_job_resume_identity(
        job_name=(job.params or {}).get("job_name") or base_name,
        job_id=_coerce_nonempty_text(merged_params.get("resume_root_job_id")) or job_id,
        model_id=job.model_id,
        mode=job.mode,
        params=merged_params,
    )
    resolved_child_batch_name = await _resolve_resume_child_batch_name(
        session,
        job,
        _coerce_nonempty_text(merged_params.get("resume_root_job_id")) or job_id,
    )
    if resolved_child_batch_name:
        merged_params["batch_name"] = resolved_child_batch_name
    merged_params = _normalize_antibody_runtime_paths(job.model_id, merged_params)
    merged_params = _normalize_structure_runtime_paths(job.model_id, merged_params)
    merged_params = _normalize_structure_geometry_params(merged_params)
    merged_params = _normalize_antibody_job_params(merged_params)
    _validate_antibody_runtime_paths(job.model_id, merged_params)
    resume_selected_input_artifact_class = normalize_antibody_artifact_class(
        merged_params.get("selected_input_artifact_class")
    )
    resume_selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        merged_params.get("selected_input_schema_version")
    )

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
        pinned_gpu=job.pinned_gpu,
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
        "nextflow_log_source": None,
    }
    
    # --- Step 1: Find the nextflow log for THIS job ---
    nf_log_content = None
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

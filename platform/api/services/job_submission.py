"""Canonical Job submission: admission, launch-context binding and selected child batches."""

from fastapi import HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Dict, Any, Callable, Mapping, NoReturn, Literal, cast
from dataclasses import dataclass
from copy import deepcopy
import asyncio
import uuid
import os
import hashlib
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel
from jsonschema.exceptions import SchemaError
from antibody_pipeline_contract import (
    is_antibody_pipeline_mode,
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import current_launch_context_id, Job, ExecutionTarget
from experiment_models import ExperimentRunAttempt
from services.job_stage_progress import project_execution_stages
from paths import get_data_root, get_allowed_roots, get_inputs_dir, get_results_dir, resolve_allowed_path
from runtime_policy import workflow_launch_block_detail, workflow_launches_allowed
from schemas import JobCreate, JobResponse, JobStatus
from services import alignment_access, ont_submission_trust
from services.md.chemistry_catalog import ChemistryCatalogError, ChemistryProfileSelectionError
from services.md.feature_gate import require_molecular_dynamics_feature
from services.md.launch_contract import MDLaunchError, materialize_md_job_spec
from services.md.results import expected_analysis_implementation_sha256
from services.md.state import MdStateError, create_md_run, create_replica_attempt
from services.nextflow import normalize_plr_input_pdb_path, normalize_plr_structure_validators
from services.rfd3_generation import GenerationContractError, file_sha256, materialize_generation_request
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
)
from model_registry import get_registry
from services.ligandmpnn_design import MODES as LIGANDMPNN_DESIGN_MODES
from services.job_requests import (
    ApprovedExecutionPlan,
    _coerce_nonempty_text,
    _coerce_positive_int,
    _execution_plan_preview,
    _is_antibody_launch,
    _normalize_boltz_cp_params_for_validation,
    _normalize_stage_family,
    _raise_md_launch_http_error,
    _resolve_md_input_path_for_runtime,
    _to_bool,
    normalize_job_request,
)
from services.job_readback import (
    _public_job_output_dir,
    _public_job_params,
    _serialized_saved_review_filter_sets,
)
from services.bindcraft2_launch import _bc2_prepared_action_output
from services.binder_continuation import (
    _awaiting_stage_to_resume_hint,
    _build_selected_loop_scope,
    _derive_job_stage_tags,
    _mutagenesis_variant_job_params,
)

logger = logging.getLogger(__name__)


DEFAULT_FAMPNN_CHECKPOINT = "fampnn_0_0.pt"


def _raise_if_workflow_launches_disabled(action: str) -> None:
    if workflow_launches_allowed():
        return
    raise HTTPException(status_code=409, detail=workflow_launch_block_detail(action))


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


def _managed_resume_output_dir(value: Any) -> str | None:
    """Validate a resume destination without creating or relocating any data.

    Resume outputs belong below this deployment's results root. Input/weight
    roots being readable does not authorize writing workflow outputs into them.
    """
    if value is None:
        return None
    def refuse(code: str, message: str) -> NoReturn:
        raise HTTPException(status_code=422, detail={"code": code, "message": message})
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        refuse("RESUME_SOURCE_INVALID", "resume_source_dir must name an existing managed result directory.")
    try:
        path = Path(value).expanduser()
        root = get_results_dir().resolve()
        if not path.is_absolute() or ".." in path.parts:
            refuse("RESUME_SOURCE_INVALID", "resume_source_dir must be an absolute managed result path without traversal.")
        if any(part.is_symlink() for part in (path, *path.parents)):
            refuse("RESUME_SOURCE_SYMLINK", "resume_source_dir must not traverse a symlink.")
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            refuse("RESUME_SOURCE_OUTSIDE_MANAGED_STORAGE",
                   "resume_source_dir is outside this deployment's managed results; select a current retained job to resume.")
        if not resolved.is_dir():
            refuse("RESUME_SOURCE_UNAVAILABLE", "The managed resume result directory is missing or is not a directory.")
        return str(resolved)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "RESUME_SOURCE_UNAVAILABLE",
            "message": "The managed resume result directory cannot be inspected."}) from exc


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
    return ""


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


def _resolve_job_sequence_length(
    explicit_length: int | None,
    params: Mapping[str, Any],
) -> int:
    if explicit_length is not None:
        return explicit_length
    for key in ("sequence", "sequence_input"):
        sequence = params.get(key)
        if isinstance(sequence, str) and sequence:
            return len(sequence)
    components = params.get("complex_components")
    if isinstance(components, list):
        lengths = [
            len(component["sequence"])
            for component in components
            if isinstance(component, Mapping)
            and component.get("type") == "protein"
            and isinstance(component.get("sequence"), str)
            and component["sequence"]
        ]
        if lengths:
            return max(lengths)
    return 300


class PreparedJobReview(BaseModel):
    code: Literal['remote_prepared_job_review_required'] = 'remote_prepared_job_review_required'
    job_request: JobCreate
    response_context: dict[str, Any]


def _require_prepared_remote_review(request: JobCreate, response_context: dict[str, Any]) -> None:
    """An explicit action may prepare selections, but cannot approve new science.

    The caller forwards this exact request to the pure shared preview/submit path;
    it must not repeat selection materialization or silently copy old approval.
    """
    if request.execution_target_id:
        raise HTTPException(status_code=409, detail=PreparedJobReview(
            job_request=request, response_context=response_context).model_dump(mode='json'))


async def _prepare_selected_structure(job_data: JobCreate, session):
    """Retain source bytes independently of placement and scientific settings."""
    from services.native_generation_sources import prepare_native_sources
    await prepare_native_sources(job_data, session)
    if job_data.source_structure is None:
        return None
    from services.binder_source_materialization import resolve_structure_source
    from routers.files import (materialize_structure, get_governed_ngs_result_roots,
        _under_persisted_ngs_root, _reject_governed_ngs_artifact, _sha256_regular_file_nofollow)
    source = job_data.source_structure
    try:
        path, identity = await resolve_structure_source(source, session)
        governed_roots = await get_governed_ngs_result_roots(session)
        if _under_persisted_ngs_root(path, governed_roots):
            raise HTTPException(403, "Access denied to this structure")
        _reject_governed_ngs_artifact(path)
        if source.path is None or not (path.parent.name.startswith('structure-') and source.expected_sha256):
            prepared = await materialize_structure(source, session, governed_roots=governed_roots)
            old_path = str(path)
            consumed = str(resolve_allowed_path(prepared['path']))
            def retained(value):
                if isinstance(value, dict):
                    return {key: retained(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [retained(item) for item in value]
                return consumed if isinstance(value, str) and value in {old_path, source.path} else value
            job_data.params = retained(job_data.params)
            job_data.source_structure = type(source).model_validate(prepared['source_structure'])
        else:
            # Prepared originals remain producer-bound, including compressed
            # native artifacts. No historical bytes are opened on replay.
            digest, _ = _sha256_regular_file_nofollow(path)
            for expected in (identity.get('artifact_sha256'), source.expected_sha256):
                if expected is not None and digest != expected:
                    raise ValueError('Selected source bytes differ from the exact document digest')
        return identity
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _guard_resequence_submission(function):
    """Acquire one ordered source union before canonical or prepared-batch work."""
    from functools import wraps
    from inspect import signature
    parameters = signature(function)

    @wraps(function)
    async def guarded(*args, **kwargs):
        from services.binder_source_materialization import resequence_submission_guard
        bound = parameters.bind(*args, **kwargs).arguments
        requests = bound.get("requests")
        value = ([item.model_dump(mode="json") for item in requests] if requests is not None
                 else bound["job_data"].model_dump(mode="json"))
        async with resequence_submission_guard(value, bound["session"]):
            return await function(*args, **kwargs)
    return guarded


async def _resolve_successor_submission(session, job_data, _bound_launch_context_id):
    successor_source = None
    successor_request_digest = None
    successor_binding = job_data.params.get("checkpoint_successor")
    if successor_binding is not None:
        from services.remote_execution.executor import (prepare_checkpoint_successor,
            checkpoint_successor_id, RemoteExecutionError)
        try:
            from component_runtime import digest
            supplied = job_data.model_dump(mode="json", exclude={"execution_plan_approval"})
            supplied["params"].pop("permanent_delete_cleanup", None)
            if _bound_launch_context_id:
                from services.resource_usage_evidence import strip_resource_execution_metadata
                supplied["params"] = strip_resource_execution_metadata(supplied["params"])
            successor_request_digest = digest(supplied)
            if not isinstance(successor_binding, dict):
                raise RemoteExecutionError("Invalid checkpoint successor binding")
            from services.job_control import visible_job_clause
            successor_source = await session.scalar(select(Job).where(
                Job.id == successor_binding.get("source_job_id"), visible_job_clause()))
            if successor_source is None:
                raise RemoteExecutionError("Checkpoint source is unavailable")
            existing_id = checkpoint_successor_id(successor_source, successor_binding)
            if existing_id:
                existing = await session.scalar(select(Job).where(Job.id == existing_id, visible_job_clause()))
                if existing is None:
                    raise RemoteExecutionError("Checkpoint successor is no longer available")
                if (existing.provenance or {}).get("checkpoint_successor_request_sha256") != successor_request_digest:
                    raise RemoteExecutionError("Checkpoint successor retry request changed")
                return successor_binding, successor_source, successor_request_digest, JobResponse.model_validate(existing)
            checkpoint = (successor_source.awaiting_payload or {}).get("component_checkpoint") or {}
            if (successor_binding.get("checkpoint_id") != checkpoint.get("checkpoint_id") or
                    successor_binding.get("checkpoint_sha256") != checkpoint.get("checkpoint_sha256")):
                raise RemoteExecutionError("Checkpoint successor binding changed")
            prepared = await prepare_checkpoint_successor(session, successor_source, checkpoint,
                successor_binding["decision"], successor_binding["execution_target_id"],
                name_suffix=successor_binding["name_suffix"])
            # Policy and Project context are authored at placement, not native scientific settings.
            prepared.execution_policy = job_data.execution_policy
            prepared.launch_context_id = job_data.launch_context_id
            expected = prepared.model_dump(mode="json", exclude={"execution_plan_approval"})
            if supplied != expected:
                raise RemoteExecutionError("Checkpoint successor request changed; prepare it again")
        except (RemoteExecutionError, KeyError, TypeError, ValueError) as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return successor_binding, successor_source, successor_request_digest, None


async def _prepare_remote_model_request(job_data, normalized_model_id, normalized_mode, selected_execution_target, original_requested_params):
    if (normalized_model_id == 'protonpottsmpnn' and normalized_mode == 'redesign'
            and selected_execution_target is not None
            and not job_data.params.get('protonpottsmpnn_design_request')):
        from services.protonpottsmpnn_design import prepare_for_job
        job_data = normalize_job_request(job_data)
        prepared = get_inputs_dir() / 'protonpottsmpnn-design' / str(uuid.uuid4())
        try:
            job_data.params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_data.params, prepared,
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    if (normalized_model_id == 'ligandmpnn' and normalized_mode in LIGANDMPNN_DESIGN_MODES
            and selected_execution_target is not None
            and not job_data.params.get('ligandmpnn_design_request')):
        from services.ligandmpnn_design import prepare_for_job
        job_data = normalize_job_request(job_data)
        prepared = get_inputs_dir() / 'ligandmpnn-design' / str(uuid.uuid4())
        try:
            job_data.params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_data.params, prepared,
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        job_data.execution_plan_approval = None
        _require_prepared_remote_review(job_data, {'model_id': normalized_model_id, 'mode': normalized_mode})

    if (normalized_model_id == 'caliby_experimental'
            and normalized_mode in {'ensemble_design', 'sidechain_pack'}
            and selected_execution_target is not None
            and not job_data.params.get('caliby_request_dir')):
        from services.caliby_native import prepare_for_job
        job_data = normalize_job_request(job_data)
        prepared = get_inputs_dir() / 'caliby-native' / str(uuid.uuid4())
        try:
            job_data.params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_data.params, prepared,
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        job_data.execution_plan_approval = None
        _require_prepared_remote_review(job_data, {'model_id': normalized_model_id, 'mode': normalized_mode})

    if (normalized_model_id == 'ppiflow' and selected_execution_target is not None
            and not job_data.params.get('ppiflow_generation_request')):
        from services.ppiflow_generation import prepare_ppiflow_generation_request, parameter_contract
        job_data = normalize_job_request(job_data)
        names = {field['name'] for field in parameter_contract(normalized_mode)}
        prepared = get_inputs_dir() / 'ppiflow-generation' / str(uuid.uuid4())
        try:
            job_data.params.update(await asyncio.to_thread(
                prepare_ppiflow_generation_request, normalized_mode, job_data.params, prepared,
                allowed_roots=tuple(get_allowed_roots().values()),
                requested_settings={key: value for key, value in original_requested_params.items() if key in names},
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Reuse the existing remote prepared-request review. Only these retained
        # bytes are approved; the selected endpoint is not replayed on approval.
        job_data.execution_plan_approval = None
        _require_prepared_remote_review(job_data, {'model_id': 'ppiflow', 'mode': normalized_mode})

    if (normalized_model_id == 'boltzgen'
            and normalized_mode in {'protein_binder', 'peptide_binder', 'nanobody_binder', 'antibody_binder', 'ligand_binder', 'ntp_binder', 'scaffold_around_ligand', 'backbone_docking'}
            and selected_execution_target is not None
            and not (job_data.params.get('boltzgen_yaml_config') and job_data.params.get('boltzgen_prepared_sha256'))):
        from services.boltzgen_scaffolding import prepare_boltzgen_generation_input
        job_data = normalize_job_request(job_data)
        prepared = get_inputs_dir() / 'boltzgen-generation' / str(uuid.uuid4())
        try:
            job_data.params = await prepare_boltzgen_generation_input(job_data.params, prepared,
                allowed_input_roots=tuple(get_allowed_roots().values()))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job_data.execution_plan_approval = None
        _require_prepared_remote_review(job_data, {'model_id': 'boltzgen', 'mode': normalized_mode})
    return job_data


async def _find_reusable_child(session, job_data):
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
                execution_stages=project_execution_stages(existing_child),
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
                execution_target_id=existing_child.execution_target_id,
                execution_source_revision=existing_child.execution_source_revision,
                execution_source_tree=existing_child.execution_source_tree,
                execution_bundle_sha256=existing_child.execution_bundle_sha256,
                remote_attempt_id=existing_child.remote_attempt_id,
                remote_state=existing_child.remote_state,
                awaiting_input=existing_child.awaiting_input,
                awaiting_stage=existing_child.awaiting_stage,
                awaiting_payload=existing_child.awaiting_payload,
                decision_history=existing_child.decision_history,
            )


def _create_batch_msa_job(session, job_data, num_jobs, mutagenesis_variants, msa_provider,
                          base_output_dir, batch_id, batch_name, inherited_source_revision, inherited_source_tree):
    msa_job = None
    needs_msa = False
    sequences_for_msa = []

    # Logging MSA batch intent


    # Mutagenesis: generate per-variant MSAs when using MSA
    if mutagenesis_variants and num_jobs > 1 and msa_provider not in {'colabfold_api', 'neurosnap_api'}:
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
            params={**_build_msa_batch_child_params(
                source_params=job_data.params,
                sequences_for_msa=sequences_for_msa,
                source_model_id=job_data.model_id,
                source_mode=job_data.mode,
            ), "remote_result_policy": job_data.execution_policy.remote_result_policy},
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
            execution_target_id=job_data.execution_target_id,
            execution_source_revision=inherited_source_revision,
            execution_source_tree=inherited_source_tree,
        )
        session.add(msa_job)
        logger.info(f"[MSA BATCH] Created MSA batch job {msa_job_id[:8]}... for {len(sequences_for_msa)} sequences")
    return msa_job


async def _materialize_job_inputs(job_params, job_id, output_dir, normalized_model_id, normalized_mode,
                                  execution_preview, original_requested_params, bc2_action_source, bc2_action_prepared):
    if normalized_model_id == 'protonpottsmpnn' and normalized_mode == 'redesign':
        from services.protonpottsmpnn_design import prepare_for_job
        try:
            job_params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_params,
                Path(output_dir) / 'inputs' / 'protonpottsmpnn-design',
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
                retain_prepared=execution_preview is not None,
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    if normalized_model_id == 'ligandmpnn' and normalized_mode in LIGANDMPNN_DESIGN_MODES:
        from services.ligandmpnn_design import prepare_for_job
        try:
            job_params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_params,
                Path(output_dir) / 'inputs' / 'ligandmpnn-design',
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
                retain_prepared=execution_preview is not None,
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    if (normalized_model_id == 'caliby_experimental'
            and normalized_mode in {'ensemble_design', 'sidechain_pack'}):
        from services.caliby_native import prepare_for_job
        try:
            job_params.update(await asyncio.to_thread(
                prepare_for_job, normalized_mode, job_params,
                Path(output_dir) / 'inputs' / 'caliby-native',
                allowed_roots=(*get_allowed_roots().values(), get_data_root(), get_inputs_dir(), get_results_dir()),
                retain_prepared=execution_preview is not None,
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    if normalized_model_id == 'ppiflow':
        from services.ppiflow_generation import prepare_ppiflow_generation_request, parameter_contract
        names = {field['name'] for field in parameter_contract(normalized_mode)}
        try:
            job_params.update(await asyncio.to_thread(
                prepare_ppiflow_generation_request, normalized_mode, job_params,
                Path(output_dir) / 'inputs' / 'ppiflow-generation',
                allowed_roots=tuple(get_allowed_roots().values()),
                source_identity={'job_id': job_id},
                retain_prepared=execution_preview is not None,
                requested_settings={key: value for key, value in original_requested_params.items() if key in names},
            ))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if normalized_model_id == 'boltzgen' and normalized_mode in {
            'protein_binder', 'peptide_binder', 'nanobody_binder', 'antibody_binder', 'ligand_binder', 'ntp_binder', 'scaffold_around_ligand', 'backbone_docking'}:
        from services.boltzgen_scaffolding import prepare_boltzgen_generation_input
        try:
            job_params = await prepare_boltzgen_generation_input(job_params,
                Path(output_dir) / 'inputs' / 'boltzgen-generation',
                allowed_input_roots=tuple(get_allowed_roots().values()),
                retain_prepared=execution_preview is not None)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if normalized_model_id == 'bindcraft2' and normalized_mode == 'campaign':
        from services.bindcraft2_launch import materialize_campaign
        try:
            job_params = {
                **job_params,
                **await asyncio.to_thread(
                    materialize_campaign,
                    job_params['bindcraft2_settings'],
                    Path(output_dir),
                    preview_digest=job_params['bc2_preview_digest'],
                ),
            }
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if bc2_action_source is not None and not bc2_action_prepared:
        from services.bindcraft2_launch import materialize_native_action
        from services.bindcraft2_publication import published_campaign_immutable
        try:
            job_params = {**job_params, **await asyncio.to_thread(
                materialize_native_action, Path(bc2_action_source.output_dir), Path(output_dir),
                operation=normalized_mode, options=job_params.get('bc2_action_options', {}),
                source_job_id=bc2_action_source.id,
                source_campaign_immutable=published_campaign_immutable(bc2_action_source),
            )}
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    selected_generator = (job_params.get('generator')
        if normalized_model_id == 'protein_modification_experimental' and normalized_mode == 'de_novo_design'
        else normalized_model_id)
    if selected_generator in {'rfd4_proteina', 'complexa'}:
        native_mode = normalized_mode if selected_generator == normalized_model_id else 'generate'
        if selected_generator == 'rfd4_proteina':
            from services.rfd4_proteina import bind_for_job
        else:
            from services.complexa import bind_for_job
        job_params, generated = bind_for_job(native_mode, job_params, output_dir)
        for item in generated:
            item.materialize(Path(output_dir))
    return job_params


def _attach_execution_approval(provenance_payload, job_data, job_params, output_dir, execution_preview, _approved_execution_plan):
    if execution_preview is not None:
        if 'input_identities' not in execution_preview:
            # Typed MD already approved its resolved structure and frozen
            # profile; its trusted materializer just sealed the derived MD
            # config. Bind that actual closure, not a second approval or an
            # invented empty generic preview.
            if not (job_data.model_id == 'molecular_dynamics'
                    and isinstance(_approved_execution_plan, ApprovedExecutionPlan)):
                raise HTTPException(status_code=409, detail='Native approval lacks input authority')
            from scripts.lib.portable_inputs import discover_native_input_references
            import yaml
            input_request = {'model_id': job_data.model_id, 'mode': job_data.mode,
                'params': deepcopy(job_params), 'output_dir': str(output_dir)}
            approved_inputs = discover_native_input_references(job_data.model_id, job_data.mode,
                job_params, (), output_dir=Path(output_dir),
                allowed_roots=(get_data_root(), get_inputs_dir(), get_results_dir()),
                yaml_loader=yaml.safe_load)
        else:
            approved_inputs = execution_preview['input_identities']
            input_request = {'model_id': execution_preview['request']['model_id'],
                'mode': execution_preview['request']['mode'],
                'params': execution_preview['request']['params'],
                'output_dir': execution_preview['plan']['native_parameters_json']['out_dir']}
        provenance_payload['execution_plan_approval'] = {
            'approval_digest': execution_preview['approval_digest'],
            'plan_sha256': execution_preview['plan']['plan_sha256'],
            'plan': execution_preview['plan'],
            'source_identity': execution_preview['plan']['source_identity'],
            'input_identities': approved_inputs,
            'input_request': input_request,
            'deferred_preparation': execution_preview['deferred_preparation'],
            'declared_expansions': execution_preview.get('declared_expansions', []),
            **({'expansion_approval': execution_preview['expansion_approval']}
               if 'expansion_approval' in execution_preview else {}),
        }


async def _persist_submitted_job(session, job, job_data, job_params, successor_source, successor_request_digest,
                                 successor_binding, execution_preview, original_requested_params, is_md_launch):
    if successor_source is not None:
        job.provenance = {**(job.provenance or {}),
            "checkpoint_successor_request_sha256": successor_request_digest,
            "checkpoint_successor_selection_sha256": file_sha256(Path(job.source_selection_manifest_path))}
        from services.remote_execution.executor import claim_checkpoint_successor, RemoteExecutionError
        try:
            await claim_checkpoint_successor(session, successor_source, job, successor_binding)
        except RemoteExecutionError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.add(job)
    if (successor_source is None and execution_preview is not None and original_requested_params.get('interactive_gate_continue') is True
            and original_requested_params.get('selection_source_type') == 'review_gate'):
        review_source = await session.get(Job, original_requested_params.get('selection_source_job_id'))
        if review_source is None or not review_source.awaiting_input:
            raise HTTPException(status_code=409, detail='The source review gate is no longer available')
        review_source.decision_history = [*(review_source.decision_history or []), {
            'stage': review_source.awaiting_stage, 'timestamp': datetime.utcnow().isoformat() + 'Z',
            'new_job_id': job.id, 'resume_mode': 'spawn_refinement',
            'from_stage': _awaiting_stage_to_resume_hint(review_source.awaiting_stage) or 'generator',
            'approval_digest': execution_preview['approval_digest'],
        }]
    if is_md_launch:
        await create_md_run(
            session,
            job=job,
            normalized_request=job_params["md_job_spec"],
        )
    if job_data.model_id == "molecular_dynamics" and job_data.mode == "replica":
        try:
            await create_replica_attempt(
                session,
                job_id=str(job.parent_job_id or ""),
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


@_guard_resequence_submission
async def _create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    _preallocated_job_id: Any = None,
    _commit: Any = True,
    _md_output_creation: Any = None,
    _md_input_resolver: Any = None,
    _trusted_workflow_adapter: Any = False,
    _approved_execution_plan: Any = None,
    _bound_launch_context_id: str | None = None,
):
    """Create and queue a new pipeline job."""
    successor_binding, successor_source, successor_request_digest, replay = await _resolve_successor_submission(
        session, job_data, _bound_launch_context_id)
    if replay is not None:
        return replay
    from services.binder_round import bind_step
    round_step_metadata, _preallocated_job_id, round_existing = await bind_step(
        session, job_data, _preallocated_job_id, project_bound=bool(_bound_launch_context_id))
    if round_existing is not None:
        return JobResponse.model_validate(round_existing)
    if job_data.binder_round is not None:
        from services.binder_round_inputs import normalize_request
        try:
            job_data.binder_round = normalize_request(job_data.binder_round)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if job_data.sequence_design is not None:
        from services.sequence_round_inputs import normalize_request as normalize_sequence_request
        try:
            job_data.sequence_design = normalize_sequence_request(job_data.sequence_design)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    # Reject stale/foreign resume paths before preview, job rows or output writes.
    _managed_resume_output_dir(job_data.params.get("resume_source_dir"))
    from services import core_protein_scientific_contract as scientific_contract
    try:
        # Also covers internal callers that mutate/model_construct JobCreate,
        # including template and mutagenesis registry-validation bypasses.
        scientific_contract.reject_reserved_marker(job_data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from copy import deepcopy
    selected_structure_identity = await _prepare_selected_structure(job_data, session)
    approval_request = job_data.model_copy(deep=True)
    execution_preview = None
    original_requested_params = deepcopy(job_data.params)
    from services.msa_policy import apply_msa_policy
    try:
        job_data.params = apply_msa_policy(job_data.model_id, job_data.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    inherited_source_revision: str | None = None
    if (normalized_model_id, normalized_mode) == ('esmfold2', 'blind_pose'):
        from services.binder_blind_pose_trust import is_selected_submission
        from services.binder_blind_pose_selected import is_retained_selected_submission
        if not is_selected_submission() and not await is_retained_selected_submission(job_data, session):
            raise HTTPException(status_code=422, detail='Blind pose requires the selected Design route')
    if (normalized_model_id, normalized_mode) == ('ligandmpnn', 'interface_context'):
        from services.ligandmpnn_interface_selection import selected_submission
        from services.ligandmpnn_interface_publication import is_retained_selected_submission
        if not selected_submission.get() and not await is_retained_selected_submission(job_data, session):
            raise HTTPException(status_code=403, detail='Use the selected interface-context route')
    inherited_source_tree: str | None = None
    selected_execution_target: ExecutionTarget | None = None
    execution_parent: Job | None = None
    if job_data.parent_job_id:
        execution_parent = await session.get(Job, str(job_data.parent_job_id))
        if execution_parent is not None:
            parent_target_id = str(execution_parent.execution_target_id or "").strip() or None
            requested_target_id = str(job_data.execution_target_id or "").strip() or None
            if "execution_target_id" in job_data.model_fields_set and requested_target_id != parent_target_id:
                raise HTTPException(
                    status_code=422,
                    detail="Child execution_target_id must match the parent Job",
                )
            job_data.execution_target_id = parent_target_id
            inherited_source_revision = execution_parent.execution_source_revision
            inherited_source_tree = execution_parent.execution_source_tree
    if job_data.execution_target_id:
        selected_execution_target = await session.get(
            ExecutionTarget,
            str(job_data.execution_target_id),
            populate_existing=True,
        )
        from services.remote_execution.targets import target_eligible
        if selected_execution_target is None or not target_eligible(selected_execution_target):
            raise HTTPException(
                status_code=422,
                detail="execution_target_id is not an active ready execution target",
            )
        if execution_parent is not None:
            if not inherited_source_revision or not inherited_source_tree:
                raise HTTPException(
                    status_code=409,
                    detail="Remote parent Job is missing its immutable source identity",
                )
        else:
            try:
                from services.remote_execution.bundle import current_source_identity

                inherited_source_revision, inherited_source_tree = current_source_identity()
            except Exception as exc:
                logger.exception("Unable to capture the remote Job source identity")
                raise HTTPException(
                    status_code=503,
                    detail="Committed BMS source identity is unavailable",
                ) from exc
    job_data = await _prepare_remote_model_request(
        job_data, normalized_model_id, normalized_mode, selected_execution_target, original_requested_params)

    bc2_action_source = None
    bc2_action_prepared = False
    if normalized_model_id == 'bindcraft2':
        from services.bindcraft2_runtime import NATIVE_ACTIONS
        if normalized_mode in NATIVE_ACTIONS:
            # Resolve the source and native options before preparing review bytes.
            # Scientific lineage does not imply placement inheritance.
            job_data.params = normalize_job_request(job_data).params
            source_id = job_data.params.get('bc2_source_job_id')
            bc2_action_source = await session.get(Job, source_id) if source_id else None
            if bc2_action_source is None:
                raise HTTPException(status_code=404, detail='BC2 native action source Job not found')
            if bc2_action_source.model_id != 'bindcraft2' or not bc2_action_source.output_dir:
                raise HTTPException(status_code=422, detail='BC2 native actions require a BC2 source Job output')
            if (job_data.params.get('resume_source_dir') or job_data.params.get('mutagenesis_variants')
                    or job_data.params.get('num_parallel_jobs', 1) not in (None, 1)):
                raise HTTPException(status_code=422, detail='BC2 native actions own one new Job output')
            job_data.params['lineage_root_job_id'] = bc2_action_source.lineage_root_job_id or bc2_action_source.id
            job_data.params['selection_source_job_id'] = bc2_action_source.id
            job_data.params['selection_source_type'] = 'native_campaign'
            if normalized_mode != 'resume':
                job_data.pinned_gpu = None
            if job_data.params.get('bc2_compilation'):
                try:
                    prepared_id, prepared_output = _bc2_prepared_action_output(job_data.params, normalized_mode)
                except (OSError, ValueError, KeyError) as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                if isinstance(_preallocated_job_id, str) and _preallocated_job_id != prepared_id:
                    raise HTTPException(status_code=409, detail='BC2 prepared Job identity changed')
                existing_owner = await session.scalar(select(Job.id).where(
                    (Job.id == prepared_id) | (Job.output_dir == str(prepared_output))).limit(1))
                if existing_owner is not None:
                    raise HTTPException(status_code=409, detail='BC2 prepared output already belongs to a Job')
                _preallocated_job_id = prepared_id
                bc2_action_prepared = True
            elif selected_execution_target is not None:
                # Allocate through the existing controller-only identity seam.
                # The response contains a receipt reference, not a writable output
                # override. Approval must bind these exact already-prepared bytes.
                from services.bindcraft2_launch import materialize_native_action
                from services.bindcraft2_publication import published_campaign_immutable
                if not isinstance(_preallocated_job_id, str):
                    _preallocated_job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms-bc2-action:' + str(uuid.uuid4())))
                output = _standard_job_output_dir(job_data.name, '', _preallocated_job_id).resolve()
                try:
                    job_data.params.update(await asyncio.to_thread(
                        materialize_native_action, Path(bc2_action_source.output_dir), output,
                        operation=normalized_mode, options=job_data.params.get('bc2_action_options', {}),
                        source_job_id=bc2_action_source.id,
                        source_campaign_immutable=published_campaign_immutable(bc2_action_source)))
                except (OSError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                job_data.execution_plan_approval = None
                _require_prepared_remote_review(job_data, {'source_job_id': bc2_action_source.id})
            elif not isinstance(_preallocated_job_id, str):
                # Same-name local actions must not share a timestamp output.
                _preallocated_job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms-bc2-action:' + str(uuid.uuid4())))

    if selected_execution_target is not None:
        approval_request.execution_target_id = job_data.execution_target_id
        if isinstance(_approved_execution_plan, ApprovedExecutionPlan):
            from component_runtime import canonical_bytes
            if canonical_bytes(approval_request.model_dump(mode='json')) != _approved_execution_plan.request_json:
                raise HTTPException(status_code=409, detail='Trusted native preview request changed')
            execution_preview = json.loads(_approved_execution_plan.preview_json)
            if 'expansion_approval' in execution_preview:
                from services.declared_job_expansion import approve_derived
                expansion_parent = await session.get(Job, execution_preview['expansion_approval']['parent_job_id'])
                if expansion_parent is None:
                    raise HTTPException(status_code=409, detail='Reviewed expansion parent is unavailable')
                try:
                    refreshed = await approve_derived(approval_request, expansion_parent, session)
                except (ValueError, OSError) as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                if refreshed.preview_json != _approved_execution_plan.preview_json:
                    raise HTTPException(status_code=409, detail='Derived child inputs or plan changed before admission')
        else:
            if not job_data.execution_plan_approval:
                raise HTTPException(status_code=409, detail='Remote submission requires explicit execution-plan preview approval')
            try:
                from services.declared_job_expansion import declarations
                expansions = await declarations(approval_request, session, lock=True)
                execution_preview = await asyncio.to_thread(_execution_plan_preview, approval_request, expansions)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not execution_preview['admissible']:
            raise HTTPException(status_code=422, detail={'message': 'Selected execution plan is unsupported', 'blockers': execution_preview['blockers']})
        if (not isinstance(_approved_execution_plan, ApprovedExecutionPlan)
                and execution_preview['approval_digest'] != job_data.execution_plan_approval):
            raise HTTPException(status_code=409, detail='Execution plan approval is stale; preview and approve the current request')
        source = execution_preview['plan']['source_identity']
        if (source['revision'], source['tree']) != (inherited_source_revision, inherited_source_tree):
            raise HTTPException(status_code=409, detail='Execution preview source changed before admission')
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
    if normalized_model_id == "frustrampnn":
        from services.frustrampnn.jobs import load_prepared_child, child_receipt
        try:
            child = load_prepared_child(job_data)
            if isinstance(_preallocated_job_id, str) and child.id != _preallocated_job_id:
                raise ValueError("FrustraMPNN snapshot differs from its reserved Project Job identity")
            await child_receipt(session, child=child)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        existing = await session.get(Job, child.id)
        if existing is not None:
            return JobResponse.model_validate(existing)
        child.params = dict(job_data.params)
        child.execution_source_revision = inherited_source_revision
        child.execution_source_tree = inherited_source_tree
        provenance = dict(child.provenance or {})
        if _bound_launch_context_id:
            provenance['launch_context_id'] = _bound_launch_context_id
        if execution_preview is not None:
            provenance['execution_plan_approval'] = {
                'approval_digest': execution_preview['approval_digest'],
                'plan_sha256': execution_preview['plan']['plan_sha256'],
                'plan': execution_preview['plan'],
                'source_identity': execution_preview['plan']['source_identity'],
                'input_identities': execution_preview['input_identities'],
                'input_request': {'model_id': job_data.model_id, 'mode': job_data.mode,
                                  'params': execution_preview['request']['params'],
                                  'output_dir': child.output_dir},
                'deferred_preparation': execution_preview['deferred_preparation'],
                'declared_expansions': execution_preview.get('declared_expansions', []),
            }
        child.provenance = provenance
        session.add(child)
        await session.flush()
        if _commit is not False:
            await session.commit()
        await session.refresh(child)
        return JobResponse.model_validate(child)
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
    if normalized_model_id == "caliby_experimental":
        from services.caliby_native import SUPPORTED_MODES
        if normalized_mode not in SUPPORTED_MODES:
            raise HTTPException(410, detail="Historical Caliby design mode remains retired")
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

    normalized_request = normalize_job_request(
        job_data, registry=registry, md_input_resolver=md_input_resolver,
    )
    job_data.params = normalized_request.params
    if normalized_model_id in {'family_msa_protein', 'family_msa_nucleic'}:
        job_data.model_id, job_data.mode = normalized_request.model_id, normalized_request.mode
    is_mutagenesis = 'mutagenesis_variants' in job_data.params

    _validate_protenix_template_requirements(job_data.model_id, job_data.params)
    _validate_protenix_checkpoint_requirements(job_data.model_id, job_data.params)
    _validate_fampnn_checkpoint_requirements(job_data.model_id, job_data.params)
    _validate_antibody_runtime_paths(job_data.model_id, job_data.params)
    if normalized_model_id == "molecular_dynamics" and normalized_mode == "analyze":
        await _validate_md_analysis_child(job_data, session)

    try:
        scientific_revision = scientific_contract.admission_revision(
            normalized_model_id, normalized_mode,
        )
        if scientific_revision == 1 and normalized_model_id in {'boltzgen', 'boltzgen_child'}:
            from services.boltzgen_request_compatibility import compile_boltzgen_settings
            job_data.params = compile_boltzgen_settings(job_data.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    child = await _find_reusable_child(session, job_data)
    if child is not None:
        return child

    # New declarations are compiled only after historical-child reuse has
    # returned, but before any new job/output directory can be written.
    try:
        from services.fampnn_policy_admission import compile_declaration
        fampnn_declaration = None
        if scientific_revision is None and job_data.fampnn_analysis_overrides is not None:
            raise ValueError('FA-MPNN analysis overrides require a supported core-protein caller')
        if scientific_revision is not None:
            scientific_parent = await session.get(Job, job_data.parent_job_id) if job_data.parent_job_id else None
            fampnn_declaration = compile_declaration(
                normalized_model_id, normalized_mode, job_data.params,
                job_data.fampnn_analysis_overrides.model_dump() if job_data.fampnn_analysis_overrides is not None else None,
                parent=scientific_parent,
            )
            if normalized_model_id == 'fampnn_child' and fampnn_declaration and 'materialization' in fampnn_declaration:
                settings = fampnn_declaration['materialization']['settings']
                for key, value in settings.items():
                    if key in job_data.params and job_data.params[key] != value:
                        raise ValueError(f'child {key} conflicts with parent declaration')
                job_data.params.update(settings)
                job_data.params['fampnn_constraint_mode'] = 'antibody'
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    # Local search is disabled; API batch admission remains fail-closed.
    default_msa_provider = _default_msa_provider_for_job(job_data.model_id, job_data.mode)
    msa_provider = str(job_data.params.get("msa_provider", default_msa_provider) or default_msa_provider).strip().lower()
    if msa_provider not in {"", "colabfold_api", "neurosnap_api"}:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid msa_provider '{msa_provider}'. Enabled search backends: colabfold_api, neurosnap_api; local search is disabled.",
        )
    if msa_provider:
        job_data.params["msa_provider"] = msa_provider

    from services.msa_policy import requires_msa_search
    if msa_provider in {"colabfold_api", "neurosnap_api"} and requires_msa_search(job_data.model_id, job_data.params):
        if not (_supports_colabfold_api_single_job(job_data.model_id, job_data.mode)
                or (job_data.model_id in {"esmfold2", "esmfold2_experimental"}
                    and job_data.mode in {"predict", "complex"})):
            raise HTTPException(
                status_code=422,
                detail=(
                    "API MSA preparation is supported for "
                    "structure launches (boltz2/rf3/protenix/esmfold2/esmfold2_experimental predict|complex, "
                    "boltz_cp_experimental design)."
                ),
            )
        # Each canonical variant uses the same hosted launch preparation as a
        # direct Job. Never create a legacy local-search MSA batch for it.
        from services.msa_provider_setup import preflight_msa_provider
        try:
            preflight_msa_provider(job_data.model_id, job_data.params)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    validated_variant_params = None
    if is_mutagenesis and scientific_revision is not None:
        # Validate the complete expansion before even the MSA dependency row is
        # added. Keep legacy/out-of-scope batch admission behavior unchanged.
        if mutagenesis_variants:
            if not isinstance(mutagenesis_variants, list) or any(
                not isinstance(variant, dict) for variant in mutagenesis_variants
            ):
                raise HTTPException(status_code=422, detail="mutagenesis_variants must contain parameter objects")
            validated_variant_params = [
                _mutagenesis_variant_job_params(job_data.params, variant, i)
                for i, variant in enumerate(mutagenesis_variants)
            ]
        for generated_params in validated_variant_params or [job_data.params]:
            errors = registry.validate_job_params(
                job_data.model_id, job_data.mode,
                _normalize_boltz_cp_params_for_validation(job_data.model_id, generated_params),
            )
            if errors:
                raise HTTPException(status_code=422, detail={"validation_errors": errors})
            _validate_protenix_template_requirements(job_data.model_id, generated_params)
            _validate_protenix_checkpoint_requirements(job_data.model_id, generated_params)
            _validate_fampnn_checkpoint_requirements(job_data.model_id, generated_params)
            _validate_antibody_runtime_paths(job_data.model_id, generated_params)

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

    # Extract sequence length for VRAM estimation (same for all jobs in batch).
    sequence_length = _resolve_job_sequence_length(
        job_data.sequence_length,
        job_data.params,
    )

    # Estimate VRAM based on model type
    from services.gpu_orchestrator import estimate_vram, estimate_protenix_tokens
    estimate_model_id = job_data.model_id
    if job_data.mode == "maturation_child":
        estimate_model_id = "maturation_child"
    if estimate_model_id == "protenix":
        sequence_length = estimate_protenix_tokens(job_data.params, sequence_length)
    vram_estimate = estimate_vram(estimate_model_id, sequence_length, job_data.params)
    if (successor_source is not None and successor_source.awaiting_stage == "post_structure_validation"
            and job_data.params.get("plr_validation_input_pdbs")):
        # The native remaining plan is FinalizeProteinLocalSelectedResults:
        # byte publication only, not a second designer/validator GPU launch.
        vram_estimate = 0
        job_data.pinned_gpu = None

    # Family alignment never requests a GPU, even when a cloned request has a pin.
    if normalized_model_id in {'family_msa_protein', 'family_msa_nucleic'}:
        job_data.pinned_gpu = None
        for key in ('gpu_id', 'pinned_gpu', 'pinned_gpus', 'cpus_per_gpu'):
            job_data.params.pop(key, None)

    # ─── CPU-only override: orchestration/launcher jobs should not consume GPU slots ─────
    if is_antibody_pipeline_mode(job_data.mode) and str(job_data.params.get("parallel_mode") or "").strip().lower() == "full_orchestrator":
        vram_estimate = 0
        job_data.pinned_gpu = None
        logger.info(f"[QUEUE] Orchestrator parent job '{job_data.name}': CPU-only launcher, vram_estimate=0")
    if bc2_action_source is not None and normalized_mode != 'resume':
        vram_estimate = 0
        job_data.pinned_gpu = None
    if job_data.model_id == "molecular_dynamics" and job_data.mode == "analyze":
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
        elif str(job_data.params.get("dorado_device") or "").strip().lower() == "cpu":
            vram_estimate = 0
            job_data.pinned_gpu = None
            logger.info(f"[QUEUE] CPU Dorado nanopore job '{job_data.name}': vram_estimate=0")

    # Generate batch_id if creating multiple jobs
    batch_id = str(uuid.uuid4()) if num_jobs > 1 or original_requested_params.get('mutation_seed_refinement_trigger') else None
    batch_name = job_data.name if num_jobs > 1 else None

    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH JOB: For multi-sequence jobs, create MSA job first
    # ═══════════════════════════════════════════════════════════════════════════
    msa_job = _create_batch_msa_job(
        session, job_data, num_jobs, mutagenesis_variants, msa_provider, base_output_dir,
        batch_id, batch_name, inherited_source_revision, inherited_source_tree)

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
            job_params = (dict(validated_variant_params[i]) if validated_variant_params is not None
                          else _mutagenesis_variant_job_params(job_data.params, variant, i))
        elif num_jobs > 1:
            job_name = f"{job_data.name}_sim{i+1}"
            output_dir = str(Path(base_output_dir) / f"sim_{i+1}")
            job_params = job_data.params
        else:
            job_name = job_data.name
            output_dir = base_output_dir
            job_params = dict(job_data.params)

        job_params = await _materialize_job_inputs(
            job_params, job_id, output_dir, normalized_model_id, normalized_mode, execution_preview,
            original_requested_params, bc2_action_source, bc2_action_prepared)

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
            from services.gpu_orchestrator import md_root_vram_estimate
            vram_estimate = md_root_vram_estimate(job_params, vram_estimate,
                selected_plan=execution_preview['plan'] if execution_preview is not None else None)

        if (
            normalized_model_id == "protein_modification_experimental"
            and normalized_mode == "de_novo_design"
            and str(job_params.get("generator") or "rfd3").strip().lower() == "rfd3"
        ):
            try:
                job_params, _generation_request_path = materialize_generation_request(
                    job_params,
                    output_dir=output_dir,
                    job_id=job_id,
                )
            except GenerationContractError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"rfd3_generation_contract_error": str(exc)},
                ) from exc

        if isinstance(job_params, dict):
            job_params = _ensure_job_resume_identity(
                job_name=job_name,
                job_id=job_id,
                model_id=job_data.model_id,
                mode=job_data.mode,
                params=job_params,
            )
            resume_source_dir = _managed_resume_output_dir(job_params.get("resume_source_dir"))
            if resume_source_dir and num_jobs == 1:
                output_dir = resume_source_dir

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
        if selected_structure_identity and not job_data.parent_job_id:
            provenance_lineage_root = selected_structure_identity.get('lineage_root_job_id') or provenance_lineage_root
            provenance_selection_source_job_id = selected_structure_identity.get('owner_job_id')
            provenance_source_stage_job_id = provenance_selection_source_job_id
            provenance_selection_source_type = 'structure'
            provenance_source_selection_count = 1
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

        if job_params.get("resume_source_dir") and num_jobs == 1:
            # Recheck at the write boundary; never mkdir a missing resume root.
            output_dir = _managed_resume_output_dir(job_params["resume_source_dir"])
        else:
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

        job_params, provenance_payload = scientific_contract.admitted_payload(
            job_params, provenance_payload, scientific_revision,
        )
        if fampnn_declaration is not None:
            from copy import deepcopy
            job_params['fampnn_analysis_declaration'] = deepcopy(fampnn_declaration)
            provenance_payload['fampnn_analysis_declaration'] = deepcopy(fampnn_declaration)

        if any(key in job_params for key in ('msa_provider', 'protenix_msa_backend')):
            from services.msa_policy import POLICY
            provenance_payload['msa_search_policy'] = {
                'revision': POLICY['revision'],
                'requested': {key: original_requested_params[key] for key in
                              ('msa_provider', 'protenix_msa_backend') if key in original_requested_params},
                'effective': {key: job_params[key] for key in
                              ('msa_provider', 'protenix_msa_backend') if key in job_params},
                'disclosure': POLICY['disclosure'],
                'provider_database_version': None,
            }

        # The existing compiler provenance slot carries request origin for every
        # supported typed workflow, not only core-protein scientific revisions.
        # In particular MD materialization and NGS path normalization must not
        # replace the submitted settings with scheduler-effective values.
        provenance_payload['core_protein_requested_params'] = deepcopy(original_requested_params)
        provenance_payload['parameter_diagnostics'] = get_registry().parameter_diagnostics(
            job_data.model_id, original_requested_params)
        if job_data.source_structure is not None:
            provenance_payload['source_structure'] = job_data.source_structure.model_dump(mode='json', exclude_none=True)
            provenance_payload['source_structure_identity'] = selected_structure_identity
        if job_data.binder_round is not None:
            provenance_payload['binder_round_request'] = job_data.binder_round.model_dump(mode='json')
        if job_data.sequence_design is not None:
            provenance_payload['sequence_design_request'] = job_data.sequence_design.model_dump(mode='json')
        if round_step_metadata is not None:
            provenance_payload['binder_round_step'] = deepcopy(round_step_metadata)
            # A retained invocation owns ancestry independently of native params.
            provenance_lineage_root = round_step_metadata['root_job_id']
            provenance_source_stage_job_id = round_step_metadata['source_job_id']
            provenance_selection_source_job_id = round_step_metadata['source_job_id']
            provenance_source_selection_count = len(round_step_metadata.get('source_refs') or [round_step_metadata.get('source_ref')])
            if round_step_metadata.get('kind') == 'general_sequence_design':
                # Server-bound retained step owns ancestry even for native models
                # whose closed scientific request has no lineage parameters.
                provenance_lineage_root = round_step_metadata['root_job_id']
                provenance_source_stage_job_id = round_step_metadata['source_job_id']
                provenance_selection_source_job_id = round_step_metadata['source_job_id']
                provenance_selection_source_type = 'selected_designs'
                provenance_source_selection_count = 1
                provenance_source_selection_manifest_path = round_step_metadata['source_binding']['selection_manifest']

        _attach_execution_approval(provenance_payload, job_data, job_params, output_dir,
                                   execution_preview, _approved_execution_plan)

        # Create job record with queue fields
        job = Job(
            id=job_id,
            name=job_name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params={**job_params, "remote_result_policy": job_data.execution_policy.remote_result_policy},
            # Execution policy is persisted only after scientific admission.
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
            provenance=({**(provenance_payload or {}), 'launch_context_id': _bound_launch_context_id}
                        if _bound_launch_context_id else provenance_payload),
            execution_target_id=job_data.execution_target_id,
            execution_source_revision=inherited_source_revision,
            execution_source_tree=inherited_source_tree,
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
        await _persist_submitted_job(
            session, job, job_data, job_params, successor_source, successor_request_digest,
            successor_binding, execution_preview, original_requested_params, is_md_launch)
        created_jobs.append(job)

        if first_job is None:
            first_job = job

    if execution_preview is not None and execution_preview.get('declared_expansions'):
        from services.declared_job_expansion import retain
        retain(msa_job or first_job, created_jobs, execution_preview)

    if _commit is False:
        await session.flush()
    else:
        await session.commit()

    # Refresh first job for response
    if first_job is None:
        raise HTTPException(status_code=500, detail="Job creation produced no canonical Job")
    await session.refresh(first_job)

    if num_jobs > 1:
        logger.info(f"[BATCH] Created batch {batch_id[:8]}... with {num_jobs} jobs: {[j.name for j in created_jobs]}")

    # NOTE: Jobs are now launched by the GPU Orchestrator, not directly here
    # The orchestrator polls for queued jobs and assigns them to GPUs
    # based on VRAM availability and bin-packing algorithm.
    # Each job in the batch gets its own GPU assignment.

    return JobResponse(
        id=first_job.id,
        execution_stages=project_execution_stages(first_job),
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
        execution_target_id=first_job.execution_target_id,
        execution_source_revision=first_job.execution_source_revision,
        execution_source_tree=first_job.execution_source_tree,
        execution_bundle_sha256=first_job.execution_bundle_sha256,
        remote_attempt_id=first_job.remote_attempt_id,
        remote_state=first_job.remote_state,
        awaiting_input=first_job.awaiting_input,
        awaiting_stage=first_job.awaiting_stage,
        awaiting_payload=first_job.awaiting_payload,
        decision_history=first_job.decision_history,
    )


_TYPED_MD_INTENT_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "source_ref",
        "expected_source_sha256",
        "chemistry_profile_id",
        "chemistry_profile_sha256",
        "catalog_digest",
        "requested_settings",
    }
)


@dataclass(frozen=True, slots=True)
class TypedMdProjectLaunch:
    """Server-owned handoff from the sealed typed MD route to canonical Job creation."""

    request_schema_version: str
    intent: Mapping[str, Any]
    preview: Mapping[str, Any]
    preview_digest: str
    md_job_spec: Mapping[str, Any]
    source_token: str
    source_params: Mapping[str, Any] | None = None


def _canonical_typed_md_document(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _typed_md_adapter_error(message: str) -> LaunchContextError:
    return LaunchContextError(
        "launch_context_typed_md_adapter_invalid",
        message,
        status_code=409,
    )


async def _validated_typed_md_project_params(
    *,
    experiment_session: AsyncSession,
    context: Any,
    job_data: JobCreate,
    adapter: Any,
    md_input_resolver: Any,
) -> dict[str, Any]:
    """Bind current prepared MD authority to its server-compiled effective request."""

    if not isinstance(adapter, TypedMdProjectLaunch):
        raise _typed_md_adapter_error("Typed MD launch requires its sealed internal adapter.")
    if (
        adapter.request_schema_version != "bms.md.launch-request.v1"
        or job_data.model_id != "molecular_dynamics"
        or job_data.mode != "simulate"
        or job_data.launch_context_id != context.launch_context_id
        or not callable(md_input_resolver)
    ):
        raise _typed_md_adapter_error(
            "Typed MD adapter route, context, model, mode, or resolver authority is invalid."
        )

    intent = deepcopy(dict(adapter.intent))
    preview = deepcopy(dict(adapter.preview))
    md_job_spec = deepcopy(dict(adapter.md_job_spec))
    source_params = deepcopy(dict(adapter.source_params or {}))
    supplied_params = dict(job_data.params or {})
    native = intent.get("schema_version") == "bms.md.launch-intent.v2"
    if native:
        from services.md.starting_structures import MdNativeLaunchIntent
        authority_fields = set(MdNativeLaunchIntent.model_fields) - {
            "name", "launch_context_id", "execution_target_id", "execution_policy", "return_to",
        }
    else:
        authority_fields = _TYPED_MD_INTENT_AUTHORITY_FIELDS
    optional_navigation = {'return_to'}
    required_intent = authority_fields | {
        'name', 'launch_context_id', 'execution_target_id', 'execution_policy'}
    if not required_intent <= set(intent) or set(intent) - required_intent - optional_navigation:
        raise _typed_md_adapter_error("Typed MD intent authority is not the sealed schema.")
    from services.md.starting_structures import MdLaunchIntent, MdNativeLaunchIntent, StartingStructureSourceRef
    try:
        (MdNativeLaunchIntent if native else MdLaunchIntent).model_validate(intent)
    except ValueError as exc:
        raise _typed_md_adapter_error('Typed MD intent is invalid.') from exc
    if (
        intent.get("execution_target_id") != job_data.execution_target_id
        or intent.get("execution_policy") != job_data.execution_policy.model_dump(mode='json')
        or preview.get("execution_target_id") != intent.get("execution_target_id")
        or preview.get("execution_policy") != intent.get("execution_policy")
    ):
        raise _typed_md_adapter_error('Typed MD placement or execution policy changed after preview.')
    if (
        intent.get("schema_version") != ("bms.md.launch-intent.v2" if native else "bms.md.launch-intent.v1")
        or intent.get("launch_context_id") != context.launch_context_id
        or _canonical_typed_md_document(supplied_params)
        != _canonical_typed_md_document({**source_params, "md_job_spec": md_job_spec})
    ):
        raise _typed_md_adapter_error(
            "Typed MD request contains caller-owned or divergent server fields."
        )

    intent_authority = {
        key: intent[key] for key in authority_fields
    }
    source_ref = intent_authority.get("source_ref")
    preview_source = preview.get("source")
    preview_chemistry = preview.get("chemistry")
    effective_request = preview.get("effective_request")
    if not native and (
        not isinstance(source_ref, dict)
        or source_ref.get("kind") not in {"design", "candidate"}
        or not isinstance(source_ref.get("id"), str)
        or not source_ref["id"]
        or preview.get("schema_version") != "bms.md.launch-preview.v1"
        or preview.get("preview_digest") != adapter.preview_digest
        or not isinstance(preview_source, dict)
        or preview_source.get("source_ref") != source_ref
        or preview_source.get("sha256") != intent_authority["expected_source_sha256"]
        or preview.get("requested_settings") != intent_authority["requested_settings"]
        or not isinstance(preview_chemistry, dict)
        or preview_chemistry.get("profile_id") != intent_authority["chemistry_profile_id"]
        or preview_chemistry.get("profile_sha256")
        != intent_authority["chemistry_profile_sha256"]
        or preview_chemistry.get("catalog_digest") != intent_authority["catalog_digest"]
        or preview_chemistry.get("admitted") is not True
        or preview.get("blockers") != []
        or not isinstance(effective_request, dict)
    ):
        raise _typed_md_adapter_error(
            "Typed MD preview no longer matches its source, settings, profile, or catalog authority."
        )

    expected_execution = deepcopy(effective_request.get("execution"))
    if not isinstance(expected_execution, dict):
        raise _typed_md_adapter_error("Typed MD effective execution authority is invalid.")
    expected_execution.pop("placement_authority", None)
    expected_execution["gpu_id"] = "0"
    spec_input = md_job_spec.get("input")
    spec_chemistry = md_job_spec.get("chemistry")
    if not native and (
        set(md_job_spec)
        != {
            "schema",
            "job_id",
            "engine",
            "replicas",
            "random_seed",
            "input",
            "chemistry",
            "preparation",
            "stages",
            "execution",
        }
        or md_job_spec.get("schema") != "bms.md.job.v2"
        or md_job_spec.get("job_id") != "assigned-by-server"
        or not isinstance(spec_input, dict)
        or set(spec_input) != {"structure"}
        or spec_input.get("structure") != adapter.source_token
        or not isinstance(adapter.source_token, str)
        or not adapter.source_token.startswith("bms-md-starting-structure:")
        or not isinstance(spec_chemistry, dict)
        or spec_chemistry.get("profile_id") != intent_authority["chemistry_profile_id"]
        or spec_chemistry.get("profile_sha256")
        != intent_authority["chemistry_profile_sha256"]
        or spec_chemistry.get("catalog_digest") != intent_authority["catalog_digest"]
        or set(spec_chemistry)
        != {"profile_id", "profile_sha256", "catalog_digest", "requested_scope"}
        or not isinstance(spec_chemistry.get("requested_scope"), str)
        or not spec_chemistry["requested_scope"]
        or md_job_spec.get("engine") != effective_request.get("engine")
        or md_job_spec.get("replicas") != effective_request.get("replicas")
        or md_job_spec.get("random_seed") != effective_request.get("random_seed")
        or md_job_spec.get("preparation") != effective_request.get("preparation")
        or md_job_spec.get("stages") != effective_request.get("stages")
        or md_job_spec.get("execution") != expected_execution
    ):
        raise _typed_md_adapter_error(
            "Server-compiled MD v2 authority does not match the current typed preview."
        )

    if native:
        if (
            preview.get("schema_version") != "bms.md.launch-preview.v2"
            or preview.get("preview_digest") != adapter.preview_digest
            or preview.get("blockers") != []
            or md_job_spec.get("schema") != "bms.md.job.v3"
            or _canonical_typed_md_document(md_job_spec)
            != _canonical_typed_md_document(preview.get("effective_request"))
            or source_params.get("md_source_provenance", {}).get("native_input_identity")
            != preview.get("input_identity")
            or source_params.get("intent") != intent
        ):
            raise _typed_md_adapter_error("Native MD effective request or input identity changed after preview.")

    from experiment_models import (
        ExperimentAggregateHead,
        ExperimentRevision,
        ExperimentValidation,
        ExperimentWorkflowPreparation,
    )
    from routers.molecular_dynamics import _launch_context_source_refs

    preparation = await experiment_session.get(
        ExperimentWorkflowPreparation, context.preparation_id
    )
    workflow = await experiment_session.get(ExperimentAggregateHead, context.workflow_id)
    revision = await experiment_session.get(
        ExperimentRevision, context.workflow_revision_id
    )
    domain = await experiment_session.get(
        ExperimentAggregateHead, context.domain_experiment_id
    )
    domain_revision = await experiment_session.get(
        ExperimentRevision,
        domain.current_revision_id if domain is not None else "",
    )
    validation = await experiment_session.get(
        ExperimentValidation,
        preparation.validation_resource_id if preparation is not None else "",
    )
    if (
        preparation is None
        or workflow is None
        or revision is None
        or domain is None
        or domain_revision is None
        or validation is None
        or preparation.resource_id != context.preparation_id
        or preparation.workspace_id != context.project_id
        or preparation.workflow_revision_id != context.workflow_revision_id
        or preparation.normalized_request_sha256 != context.normalized_request_sha256
        or preparation.validation_status != "valid"
        or preparation.validation_resource_id != context.validation_receipt_id
        or hashlib.sha256(preparation.validation_receipt_json.encode()).hexdigest()
        != context.validation_receipt_sha256
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256
        != hashlib.sha256(validation.receipt_json.encode()).hexdigest()
        or workflow.parent_id != context.domain_experiment_id
        or workflow.current_revision_id != context.workflow_revision_id
        or revision.subject_id != context.workflow_id
    ):
        raise _typed_md_adapter_error(
            "Typed MD context hierarchy, revision, preparation, or validation authority diverged."
        )
    try:
        normalized_request = json.loads(preparation.normalized_request_json)
        workflow_payload = json.loads(revision.canonical_payload)
        domain_payload = json.loads(domain_revision.canonical_payload)
        validation_receipt = json.loads(preparation.validation_receipt_json)
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _typed_md_adapter_error(
            "Typed MD preparation is no longer current and usable."
        ) from exc
    if not all(
        isinstance(value, dict)
        for value in (
            normalized_request,
            workflow_payload,
            domain_payload,
            validation_receipt,
            scheduler_payload,
        )
    ):
        raise _typed_md_adapter_error("Typed MD prepared authority has an invalid shape.")
    scheduler = workflow_payload.get("scheduler")
    expected_params = scheduler.get("params") if isinstance(scheduler, dict) else None
    expected_science = ({key: value for key, value in expected_params.items()
                         if key not in optional_navigation} if isinstance(expected_params, dict) else None)
    bound_refs = _launch_context_source_refs(normalized_request, workflow_payload)
    if (
        _canonical_typed_md_document(normalized_request)
        != preparation.normalized_request_json
        or hashlib.sha256(preparation.normalized_request_json.encode()).hexdigest()
        != preparation.normalized_request_sha256
        or _canonical_typed_md_document(workflow_payload) != revision.canonical_payload
        or hashlib.sha256(revision.canonical_payload.encode()).hexdigest()
        != revision.payload_sha256
        or normalized_request.get("workflow_revision_id")
        != context.workflow_revision_id
        or normalized_request.get("workflow") != workflow_payload
        or not isinstance(normalized_request.get("input_dataset_revision_ids"), list)
        or not isinstance(normalized_request.get("input_authority"), dict)
        or validation_receipt.get("status") != "valid"
        or validation_receipt.get("workflow_revision_id")
        != context.workflow_revision_id
        or validation_receipt.get("normalized_request_sha256")
        != context.normalized_request_sha256
        or scheduler_payload != scheduler
        or _canonical_typed_md_document(scheduler_payload)
        != preparation.scheduler_payload_json
        or domain_payload.get("domain_kind") != "protein_in_silico"
        or workflow_payload.get("adapter_id") != "molecular-dynamics.v2"
        or not isinstance(scheduler, dict)
        or scheduler.get("model_id") != "molecular_dynamics"
        or scheduler.get("mode") != "simulate"
        or not isinstance(expected_params, dict)
        or set(expected_science) != authority_fields
        or _canonical_typed_md_document(expected_science)
        != _canonical_typed_md_document(intent_authority)
        or (not native and (
            len(bound_refs) != 1
            or bound_refs[0] != StartingStructureSourceRef.model_validate(source_ref)
        ))
    ):
        raise _typed_md_adapter_error(
            "Typed MD intent does not match the prepared Project authority."
        )

    canonical_params = await validate_bound_job_request(
        experiment_session,
        context,
        job_name=job_data.name,
        model_id=job_data.model_id,
        mode=job_data.mode,
        params=deepcopy(expected_params),
        pinned_gpu=job_data.pinned_gpu,
    )
    return {**canonical_params, **source_params, "md_job_spec": md_job_spec}


def _launch_context_http_error(exc: LaunchContextError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


async def _reserve_selected_run_group(experiment_session, *, group_id, domain_id):
    from services.ngs_molbio_n5 import reserve_run_group, ResourceAdmissionDenied
    try:
        return await reserve_run_group(experiment_session, group_id=group_id,
                                       domain_id=domain_id, actor='native-child-submission')
    except ResourceAdmissionDenied as exc:
        raise LaunchContextError(exc.code, exc.reason, status_code=409) from exc


async def submit_selected_child_jobs(
    requests: list[JobCreate], background_tasks: BackgroundTasks,
    session: AsyncSession, experiment_session: AsyncSession, *,
    destination_launch_context_id: str | None, idempotency_key: str,
    response_context: dict[str, Any],
    preallocated_attempt_ids: list[str] | None = None,
) -> list[JobResponse]:
    """Submit native selected requests with one destination attempt per child.

    Preparation and publication reuse the Project transaction owners. Core Jobs
    are inserted as one transaction, and no child is scheduler-visible until all
    Project bindings have committed. This is the existing two-store handoff, not
    a distributed transaction or a replacement scheduler.
    """
    from services.global_experiments.launch_contexts import prepare_child_launch_contexts
    from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS

    prepared = None
    try:
        if destination_launch_context_id:
            prepared = await prepare_child_launch_contexts(
                experiment_session, destination_launch_context_id=destination_launch_context_id,
                job_requests=requests,
                native_entrypoints=[MODEL_MODE_WORKFLOW_ENTRYPOINTS.get((r.model_id, r.mode)) for r in requests],
                idempotency_key=idempotency_key, core_session=session,
                preallocated_attempt_ids=preallocated_attempt_ids)
            requests = [JobCreate.model_validate(child['job_request']) for child in prepared['children']]
        remote = any(item.execution_target_id for item in requests)
        if remote:
            # Preserve the actual normalized child requests across review. Their
            # source snapshots and destination attempts are not reacquired.
            if prepared:
                await experiment_session.commit()
            if len(requests) == 1:
                _require_prepared_remote_review(requests[0], response_context)
            else:
                raise HTTPException(409, {'code': 'remote_prepared_job_review_required',
                    'job_requests': [item.model_dump(mode='json') for item in requests],
                    'response_context': response_context})
        if not prepared:
            responses = [await submit_job(item, background_tasks, session, _commit=False) for item in requests]
            await session.commit()
            return responses
        destination = await resolve_launch_context_for_display(experiment_session, destination_launch_context_id)
        await _reserve_selected_run_group(experiment_session, group_id=prepared['run_group_id'],
                                          domain_id=destination.domain_experiment_id)
        return await _create_prepared_child_batch(requests, background_tasks, session, experiment_session)
    except LaunchContextError as exc:
        await session.rollback()
        await experiment_session.rollback()
        raise _launch_context_http_error(exc) from exc
    except HTTPException as exc:
        if not (exc.status_code == 409 and isinstance(exc.detail, dict)
                and exc.detail.get('code') == 'remote_prepared_job_review_required'):
            await session.rollback()
            if prepared:
                await experiment_session.rollback()
        raise
    except Exception:
        await session.rollback()
        if destination_launch_context_id:
            await experiment_session.rollback()
        raise


@_guard_resequence_submission
async def _create_prepared_child_batch(requests, background_tasks, session, experiment_session, *, native_job_kwargs=None):
    from services.global_experiments.launch_contexts import validate_prepared_child_job_request
    from services.global_experiments.launch_contexts import _project_bound_job
    pending = []
    # The incoming header describes the destination, not any child. Explicit
    # server-owned provenance below replaces that transport-only inheritance;
    # never manufacture a child HTTP header or modify the destination context.
    transport_token = current_launch_context_id.set(None)
    try:
        for original in requests:
            item = original.model_copy(deep=True)
            context = await resolve_launch_context_for_display(experiment_session, item.launch_context_id)
            attempt = await experiment_session.get(ExperimentRunAttempt, context.run_attempt_id)
            await validate_prepared_child_job_request(experiment_session, context, item)
            existing = await session.get(Job, attempt.scheduler_job_id)
            if existing is not None:
                await validate_bound_job(experiment_session, context, existing)
                pending.append((context, context.claim_token, existing, JobResponse.model_validate(existing)))
                continue
            native_kwargs = dict(native_job_kwargs or {})
            approved = native_kwargs.get('_approved_execution_plan')
            if isinstance(approved, ApprovedExecutionPlan):
                from component_runtime import canonical_bytes
                if canonical_bytes(item.model_dump(mode='json')) != approved.request_json:
                    raise HTTPException(409, 'Trusted native preview request changed')
            context, claim = await claim_launch_context(experiment_session, item.launch_context_id)
            item.params = await validate_bound_job_request(experiment_session, context,
                job_name=item.name, model_id=item.model_id, mode=item.mode,
                params=dict(item.params), pinned_gpu=item.pinned_gpu)
            if isinstance(approved, ApprovedExecutionPlan):
                native_kwargs['_approved_execution_plan'] = ApprovedExecutionPlan(
                    canonical_bytes(item.model_dump(mode='json')), approved.preview_json)
            response = await _create_job(item, background_tasks, session,
                _preallocated_job_id=attempt.scheduler_job_id, _commit=False,
                _trusted_workflow_adapter=True, _bound_launch_context_id=context.launch_context_id, **native_kwargs)
            job = await session.get(Job, response.id)
            await validate_bound_job(experiment_session, context, job)
            pending.append((context, claim, job, response))
        # No partial core Job set is committed if any later child fails. Durable
        # claims precede the core commit just as on canonical single submission.
        await experiment_session.commit()
        await session.commit()
        bindings = []
        for context, claim, job, response in pending:
            if context.canonical_job_id:
                binding = json.loads(context.binding_receipt_json)
            else:
                context, binding = await consume_launch_context(experiment_session,
                    launch_context_id=context.launch_context_id, claim_token=claim,
                    canonical_job_id=job.id, canonical_batch_id=job.batch_id)
            await _project_bound_job(experiment_session, session, context, job, binding)
            bindings.append((context, job, response, binding))
        await experiment_session.commit()
        for context, job, response, binding in bindings:
            await publish_launch_context_binding(session, context=context, job=job, binding=binding, commit=False)
        await session.commit()
        return [response.model_copy(update={'launch_context_id': context.launch_context_id,
            'launch_context_binding': binding, 'return_uri': context.return_uri})
            for context, job, response, binding in bindings]
    finally:
        current_launch_context_id.reset(transport_token)


@_guard_resequence_submission
async def submit_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    _preallocated_job_id: Any = None,
    _commit: Any = True,
    _md_output_creation: Any = None,
    _md_input_resolver: Any = None,
    _typed_md_project_launch: Any = None,
    experiment_session: AsyncSession | None = None,
    _approved_execution_plan: Any = None,
) -> JobResponse:
    """Canonical Job submission, optionally bound by one opaque launch context."""
    launch_context_id = str(job_data.launch_context_id or "").strip()
    typed_md_project_launch = _typed_md_project_launch
    if not launch_context_id:
        if typed_md_project_launch is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "launch_context_typed_md_adapter_invalid",
                    "message": "Typed MD Project authority requires one launch context.",
                },
            )
        return await _create_job(
            job_data,
            background_tasks,
            session,
            _preallocated_job_id=_preallocated_job_id,
            _commit=_commit,
            _md_output_creation=_md_output_creation,
            _md_input_resolver=_md_input_resolver,
            _approved_execution_plan=_approved_execution_plan,
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
        if typed_md_project_launch is None:
            from services.global_experiments.launch_contexts import validate_prepared_child_job_request
            retained_preview = None
            if await validate_prepared_child_job_request(experiment_session, preview_context, job_data):
                if job_data.execution_target_id:
                    # Review covers the immutable child request before the Project
                    # owner adds reservation metadata. Reuse the same approved-plan
                    # handoff as other native owners, without changing science.
                    from component_runtime import canonical_bytes
                    if isinstance(_approved_execution_plan, ApprovedExecutionPlan):
                        if canonical_bytes(job_data.model_dump(mode='json')) != _approved_execution_plan.request_json:
                            raise HTTPException(409, 'Trusted native preview request changed')
                        retained_preview = json.loads(_approved_execution_plan.preview_json)
                    else:
                        from services.declared_job_expansion import declarations
                        if not job_data.execution_plan_approval:
                            raise HTTPException(409, 'Remote submission requires explicit execution-plan preview approval')
                        try:
                            expansions = await declarations(job_data, session, lock=True)
                            retained_preview = await asyncio.to_thread(_execution_plan_preview, job_data, expansions)
                        except (ValueError, OSError) as exc:
                            raise HTTPException(422, str(exc)) from exc
                        if retained_preview['approval_digest'] != job_data.execution_plan_approval:
                            raise HTTPException(409, 'Execution plan approval is stale; preview and approve the current request')
                from experiment_models import ExperimentWorkflowRun
                run = await experiment_session.get(ExperimentWorkflowRun, prepared_attempt.workflow_run_id)
                await _reserve_selected_run_group(experiment_session, group_id=run.run_group_id,
                    domain_id=preview_context.domain_experiment_id)
            job_data.params = await validate_bound_job_request(
                experiment_session,
                preview_context,
                job_name=job_data.name,
                model_id=job_data.model_id,
                mode=job_data.mode,
                params=dict(job_data.params or {}),
                pinned_gpu=job_data.pinned_gpu,
            )
            if retained_preview is not None:
                from component_runtime import canonical_bytes
                _approved_execution_plan = ApprovedExecutionPlan(
                    canonical_bytes(job_data.model_dump(mode='json')), canonical_bytes(retained_preview))
        else:
            from component_runtime import canonical_bytes
            if isinstance(_approved_execution_plan, ApprovedExecutionPlan):
                if canonical_bytes(job_data.model_dump(mode='json')) != _approved_execution_plan.request_json:
                    raise _typed_md_adapter_error('Trusted remote MD request changed before Project binding.')
            job_data.params = await _validated_typed_md_project_params(
                experiment_session=experiment_session,
                context=preview_context,
                job_data=job_data,
                adapter=typed_md_project_launch,
                md_input_resolver=_md_input_resolver,
            )
            if isinstance(_approved_execution_plan, ApprovedExecutionPlan):
                # The existing adapter verified identical source/profile/science;
                # only its server-owned Project materialization fields were added.
                _approved_execution_plan = ApprovedExecutionPlan(
                    canonical_bytes(job_data.model_dump(mode='json')),
                    _approved_execution_plan.preview_json)
        if _preallocated_job_id:
            existing_job = await session.get(Job, str(_preallocated_job_id))
            if existing_job is not None:
                raise LaunchContextError(
                    "launch_context_claimed",
                    "The canonical Job already exists for this launch context.",
                    status_code=409,
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
                    from services.global_experiments.launch_contexts import _project_bound_job
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
                from services.global_experiments.launch_contexts import _project_bound_job
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
            _preallocated_job_id=_preallocated_job_id,
            _commit=_commit,
            _md_output_creation=_md_output_creation,
            _md_input_resolver=_md_input_resolver,
            _approved_execution_plan=_approved_execution_plan,
            _trusted_workflow_adapter=True,
            _bound_launch_context_id=launch_context_id,
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
        from services.global_experiments.launch_contexts import _project_bound_job
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


async def preview_submission(job_data: JobCreate, session: AsyncSession, experiment_session: AsyncSession | None = None):
    """Prepare the request for its existing nonexecuting preview."""
    _managed_resume_output_dir(job_data.params.get("resume_source_dir"))
    job_data = job_data.model_copy(deep=True)
    await _prepare_selected_structure(job_data, session)
    if job_data.launch_context_id:
        if current_launch_context_id.get() != job_data.launch_context_id:
            raise HTTPException(status_code=409, detail='Launch context header and body must match')
        try:
            context = await resolve_launch_context(experiment_session, job_data.launch_context_id)
            from services.global_experiments.launch_contexts import validate_prepared_child_job_request
            native_child = await validate_prepared_child_job_request(experiment_session, context, job_data)
            job_data.params = await validate_bound_job_request(
                experiment_session, context, job_name=job_data.name,
                model_id=job_data.model_id, mode=job_data.mode,
                params=job_data.params, pinned_gpu=job_data.pinned_gpu,
                attach_resource_authority=not native_child)
        except LaunchContextError as exc:
            raise _launch_context_http_error(exc) from exc
    from services.remote_execution.targets import target_eligible
    if job_data.execution_target_id:
        target = await session.get(ExecutionTarget, job_data.execution_target_id, populate_existing=True)
        if target is None or not target_eligible(target):
            raise HTTPException(status_code=422, detail='execution_target_id is not an active ready execution target')
    try:
        from services.declared_job_expansion import declarations
        expansions = await declarations(job_data, session)
        return await asyncio.to_thread(_execution_plan_preview, job_data, expansions)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

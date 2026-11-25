"""Job request normalization and execution-plan preview."""

from pydantic import ConfigDict, model_validator
from schemas import ExecutionPolicy

from fastapi import HTTPException
from typing import Optional, List, Dict, Any, NoReturn, Literal
from dataclasses import dataclass
import os
import hashlib
import logging
import json
import re
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from jsonschema.exceptions import SchemaError
from antibody_pipeline_contract import (
    ANTIBODY_PIPELINE_CONTRACT_VERSION,
    infer_selected_input_artifact_class,
    is_antibody_pipeline_mode,
    normalize_antibody_pipeline_contract_version,
)
from paths import get_allowed_roots, resolve_allowed_path
from schemas import JobCreate
from services.md.chemistry_catalog import ChemistryCatalogError, ChemistryProfileSelectionError
from services.md.launch_contract import MDLaunchError, normalize_md_job_spec
from services.nextflow import normalize_plr_input_pdb_path, normalize_plr_structure_validators
from services.frustrampnn.settings import (
    RequestedSettingsPayloadError,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)
from model_registry import get_registry
from services.ligandmpnn_design import MODES as LIGANDMPNN_DESIGN_MODES

logger = logging.getLogger(__name__)


DEFAULT_PPIFLOW_CHECKPOINT = "nanobody"


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


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _coerce_nonempty_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


NANOPORE_PATH_PARAM_KEYS = {
    "pod5_dir",
    "bam_path",
    "fastq_path",
    "reference_fasta",
    "wf_clone_workflow_dir",
}


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _should_normalize_antibody_job_params(
    model_id: str,
    mode: str,
    params: Optional[Dict[str, Any]],
) -> bool:
    normalized_model_id = str(model_id or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    # A model-owned native request is never an antibody-pipeline parameter bag.
    # In particular these distinct model routes must not acquire implicit
    # validator, chain, gate, or PPIFlow defaults during shared job admission.
    if normalized_model_id in {"bindcraft2", "ligandmpnn"}:
        return False
    if (
        normalized_model_id in {"boltz2", "protenix", "esmfold2", "rf3", "template_structure_prediction"}
        and normalized_mode in {"predict", "complex", "structure_prediction"}
        and not _is_antibody_launch(model_id, params)
    ):
        return False
    if (
        normalized_model_id == "protein_local_redesign"
        and normalized_mode == "local_redesign"
    ) or (
        normalized_model_id == "protein_modification_experimental"
        and normalized_mode == "region_redesign"
    ):
        return False
    return True


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
        raise HTTPException(status_code=422, detail=f"Unsupported structure validator: {structure_validator}")
    normalized["structure_validator"] = structure_validator

    canonical_post_validation = normalized.get("run_post_validation_maturation")
    legacy_post_boltz = normalized.get("run_post_boltz_maturation")
    if canonical_post_validation is None and legacy_post_boltz is not None:
        canonical_post_validation = _to_bool(legacy_post_boltz)
    if canonical_post_validation is not None:
        normalized["run_post_validation_maturation"] = _to_bool(canonical_post_validation)
        normalized["run_post_boltz_maturation"] = normalized["run_post_validation_maturation"]

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
            raise HTTPException(status_code=422, detail=f"Unsupported interactive gate stage: {normalized_gate_stage}")
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
            for key, value in _stage_optimized_ppiflow_defaults(inferred_ppiflow_stage_mode).items():
                normalized.setdefault(key, value)
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
    if model_id not in {"protenix", "boltz2", "rf3", "fampnn", "proteinmpnn"} or not isinstance(params, dict):
        return params

    normalized = dict(params)
    keys = (("input_pdb", "mpnn_bias_AA_jsonl") if model_id == "proteinmpnn" else
            ("input_pdb", "fampnn_checkpoint_path") if model_id == "fampnn" else ("target_pdb", "fixed_target_source_path"))
    for key in keys:
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _resolve_alias_path_for_runtime(value)
    if model_id == 'proteinmpnn':
        from scripts.prep_mpnn_designs import validate_generic_input
        try:
            validate_generic_input(normalized.get('input_pdb') or '', normalized)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail={'validation_errors': [str(exc)]}) from exc
    return normalized


MIN_BOLTZ_NO_MSA_RECYCLING_STEPS = 3


MIN_BOLTZ_NO_MSA_SAMPLING_STEPS = 50


STRUCTURE_PREDICTION_COMPLEX_RF3_ERROR = "RF3 is predict-only and cannot be launched in complex mode."


BOLTZ_CP_STRUCTURE_LAUNCHER_INPUT_SENTINEL = "__boltz_cp_structure_launcher_input__"


def _largest_square_divisor(value_count: int, requested_size_cp: Optional[int] = None) -> int:
    if value_count < 1:
        return 1

    requested = requested_size_cp if requested_size_cp and requested_size_cp > 0 else value_count
    best = 1
    for candidate in range(1, value_count + 1):
        if value_count % candidate != 0:
            continue
        root = int(candidate ** 0.5)
        if root * root != candidate or candidate > requested:
            continue
        best = candidate
    return best


def _default_structure_prediction_pred_method(model_id: str) -> str:
    normalized_model_id = str(model_id or "").strip().lower()
    if normalized_model_id == "rf3":
        return "rf3"
    if normalized_model_id == "protenix":
        return "protenix"
    if normalized_model_id == "esmfold2":
        return "esmfold2"
    return "boltz"


def _canonical_structure_method(method):
    return {'both': 'boltz_protenix', 'all': 'boltz_protenix', 'boltz2': 'boltz'}.get(method, method)


def structure_template_target(params):
    """Resolve only local model owners. Paid submissions require their own approval path."""
    method = _canonical_structure_method(str(params.get('pred_method') or 'boltz').strip().lower())
    owners = {'boltz': 'boltz2', 'boltz_protenix': 'boltz2', 'protenix': 'protenix',
              'esmfold2': 'esmfold2', 'fold_cp': 'boltz_cp_experimental'}
    if method not in owners:
        raise ValueError(f'Unsupported structure template predictor: {method}; use a supported model-owned route')
    complex_input = any(params.get(key) for key in ('complex_components', 'complex_json_path', 'complex_batch_dir'))
    mode = 'design' if method == 'fold_cp' else 'complex' if complex_input and method != 'esmfold2' else 'predict'
    native_params = {**params, 'pred_method': method}
    if method == 'fold_cp':
        native_params.pop('pred_method')  # The dedicated design route owns the predictor.
    return owners[method], mode, native_params


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

    requested_pred_method = _canonical_structure_method(str(normalized.get("pred_method") or "").strip().lower())
    if not requested_pred_method:
        requested_pred_method = _default_structure_prediction_pred_method(normalized_model_id)

    if requested_pred_method not in {"boltz", "protenix", "esmfold2", "rf3", "boltz_protenix"}:
        raise HTTPException(status_code=422, detail={"validation_errors": [
            f"Unsupported structure predictor: {requested_pred_method}; explicit selections are never replaced"
        ]})
    if normalized_model_id == "esmfold2" and requested_pred_method == "boltz_protenix":
        raise HTTPException(status_code=422, detail={"validation_errors": [
            "ESMFold2 cannot execute boltz_protenix; use the Boltz or Protenix prediction route"
        ]})
    if normalized_mode != "complex" and (normalized_model_id == "rf3" or requested_pred_method == "rf3"):
        raise HTTPException(status_code=422, detail={"validation_errors": [
            "Standalone RF3 sequence prediction is not implemented by the native structure_prediction workflow; "
            "retained PDB-input RF3 design/validation components are not a sequence-prediction substitute."
        ]})

    if normalized_mode == "complex":
        if requested_pred_method == "rf3" or normalized_model_id == "rf3":
            raise HTTPException(
                status_code=422,
                detail={"validation_errors": [STRUCTURE_PREDICTION_COMPLEX_RF3_ERROR]},
            )

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

    gpu_ids = _coerce_nonempty_text(normalized.get("gpu_ids")) or _coerce_nonempty_text(
        normalized.get("bcp_gpu_ids")
    )
    if not gpu_ids and isinstance(normalized.get("pinned_gpus"), list) and normalized["pinned_gpus"]:
        gpu_ids = ",".join(str(gpu_id).strip() for gpu_id in normalized["pinned_gpus"] if str(gpu_id).strip())
    if gpu_ids:
        normalized["gpu_ids"] = gpu_ids

    cp_topology = normalized.get("cp_topology", normalized.get("bcp_cp_topology", "2d"))
    if cp_topology not in ("2d", "1d"):
        raise HTTPException(status_code=422, detail="Fold-CP cp_topology must be one of: 2d, 1d")
    normalized["cp_topology"] = cp_topology
    size_cp = _coerce_positive_int(normalized.get("size_cp")) or _coerce_positive_int(
        normalized.get("bcp_size_cp")
    )
    requested_cp = normalized.get("size_cp", normalized.get("bcp_size_cp"))
    if requested_cp not in (None, "") or cp_topology == "1d":
        from services.nextflow import derive_boltz_cp_gpu_launch_settings
        try:
            gpu_ids, size_cp = derive_boltz_cp_gpu_launch_settings(
                pinned_gpus=normalized.get("pinned_gpus"),
                requested_size_cp=requested_cp,
                fallback_gpu_ids=gpu_ids,
                cp_topology=cp_topology,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if gpu_ids:
            normalized["gpu_ids"] = gpu_ids
    elif gpu_ids:
        gpu_count = len([item for item in gpu_ids.split(",") if item.strip()])
        if gpu_count > 0:
            size_cp = _largest_square_divisor(gpu_count, size_cp)
    if size_cp is not None:
        normalized["size_cp"] = size_cp

    alias_mappings = {
        "input_format": "bcp_input_format",
        "output_format": "bcp_output_format",
        "write_full_pae": "bcp_write_full_pae",
        "recycling_steps": "bcp_recycling_steps",
        "sampling_steps": "bcp_sampling_steps",
        "diffusion_samples": "bcp_diffusion_samples",
        "seed": "bcp_seed",
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

    for retired_key in tuple(normalized):
        if retired_key in {
            "backend",
            "shard_plan_id",
            "confidence_prediction",
            "max_msa_seqs",
            "max_parallel_samples",
            "precision",
            "triattn_backend",
            "context_store_mode",
            "context_store_root",
            "context_query_tile_tokens",
            "context_store_logical_size_cp",
            "context_store_pair_tile_tokens",
            "context_store_key_tile_tokens",
            "context_store_projection_cache_byte_budget",
            "projection_cache_byte_budget",
        } or retired_key.startswith("bcp_context_") or retired_key in {
            "bcp_backend",
            "bcp_shard_plan_id",
            "bcp_confidence_prediction",
            "bcp_max_msa_seqs",
            "bcp_max_parallel_samples",
            "bcp_precision",
            "bcp_triattn_backend",
        }:
            normalized.pop(retired_key, None)

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

    # Quality recommendations are not scientific admission bounds. The native
    # schema validates supported values; never rewrite an explicit request.
    if sampling_steps is not None and sampling_steps < MIN_BOLTZ_NO_MSA_SAMPLING_STEPS:
        logger.info("No-MSA Boltz sampling recommendation: %s (requested %s retained)",
                    MIN_BOLTZ_NO_MSA_SAMPLING_STEPS, sampling_steps)
    if recycling_steps is not None and recycling_steps < MIN_BOLTZ_NO_MSA_RECYCLING_STEPS:
        logger.info("No-MSA Boltz recycling recommendation: %s (requested %s retained)",
                    MIN_BOLTZ_NO_MSA_RECYCLING_STEPS, recycling_steps)

    return normalized


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
    if protenix_mode is None and _to_bool(normalized.get("protenix_anchor_target")):
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
        # Generic template search and selected-target anchoring are independent.
        # The native caller enables template features for anchoring itself;
        # do not turn an explicit false (or omitted search) into a corpus search.

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


def _selection_manifest_path(selection_dir: Path) -> Path:
    return selection_dir / "selection_manifest.json"


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


def normalize_job_request(job_data: JobCreate, *, registry=None, md_input_resolver=None,
                          native_entrypoint: str | None = None) -> JobCreate:
    """Existing prequeue scientific normalization, also used by provision preview.

    Return a detached request. No Job/output materialization, resource probe,
    provider request or queue insertion belongs to this shared request step.
    Runtime presence/readiness remains the materialization owner's concern.
    """
    job_data = job_data.model_copy(deep=True)
    job_data.params.pop("permanent_delete_cleanup", None)
    registry = registry or get_registry()
    if job_data.sequence_design is not None:
        from services.sequence_round_inputs import normalize_request as normalize_sequence_request
        try:
            job_data.sequence_design = normalize_sequence_request(job_data.sequence_design, registry)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if job_data.binder_round is not None:
        from services.binder_round_inputs import normalize_request
        try:
            job_data.binder_round = normalize_request(job_data.binder_round, registry)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    md_input_resolver = md_input_resolver or _resolve_md_input_path_for_runtime
    normalized_model_id = str(job_data.model_id or "").strip().lower()
    normalized_mode = str(job_data.mode or "").strip().lower()
    if (normalized_model_id, normalized_mode) == ('template_structure_prediction', 'structure_prediction'):
        try:
            job_data.model_id, job_data.mode, job_data.params = structure_template_target(job_data.params)
        except ValueError as exc:
            raise HTTPException(422, detail={'validation_errors': [str(exc)]}) from exc
        normalized_model_id, normalized_mode = job_data.model_id, job_data.mode
    if normalized_model_id in {'family_msa_protein', 'family_msa_nucleic'}:
        from services.family_msa.requests import MODE, prepare_request
        from services.resource_usage_evidence import strip_resource_execution_metadata
        from services.execution_ownership import strip_execution_metadata
        native_params = strip_execution_metadata(strip_resource_execution_metadata(job_data.params))
        adapter = native_params.pop('workflow_adapter', None)
        metadata = {key: value for key, value in job_data.params.items() if key not in native_params and key != 'workflow_adapter'}
        if adapter is not None:
            metadata['workflow_adapter'] = adapter
        try:
            selected, payload = prepare_request(native_params)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if selected != normalized_model_id or normalized_mode != MODE:
            raise HTTPException(422, detail='Family alignment model or mode does not match request')
        job_data.model_id = selected
        job_data.mode = MODE
        job_data.params = {**payload, **metadata}
        return job_data
    from services.sequence_designer_settings import normalize_historical_sequence_settings
    job_data.params = normalize_historical_sequence_settings(normalized_model_id, job_data.params)
    if normalized_model_id == 'frustrampnn':
        from services.frustrampnn.jobs import load_prepared_child
        try:
            load_prepared_child(job_data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return job_data
    if normalized_model_id == 'protonpottsmpnn':
        from services.protonpottsmpnn_design import normalize_design_params, REQUEST_FIELD
        try:
            values = dict(job_data.params)
            # Placement is system-owned: remote workers use their allocated GPU;
            # explicit Local must not inherit a parent's CUDA placement.
            values['protonpottsmpnn_device'] = ('cuda' if job_data.execution_target_id
                else os.environ.get('BMS_PROTONPOTTSMPNN_DEVICE', 'cpu'))
            if not values.get(REQUEST_FIELD) and values.get('target_pdb'):
                values['target_pdb'] = _resolve_alias_path_for_runtime(values['target_pdb'])
            job_data.params = normalize_design_params(normalized_mode, values)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        return job_data
    if normalized_model_id == 'ligandmpnn' and normalized_mode in LIGANDMPNN_DESIGN_MODES:
        from services.ligandmpnn_design import normalize_design_params
        transport_keys = {'ligandmpnn_design_request', 'ligandmpnn_design_input',
                          'num_parallel_jobs', 'job_name', 'workflow_adapter',
                          '_global_resource_admission', '_global_dispatch_authority'}
        transport = {key: value for key, value in job_data.params.items() if key in transport_keys}
        values = {key: value for key, value in job_data.params.items() if key not in transport_keys}
        try:
            if not transport.get('ligandmpnn_design_request'):
                for key in ('target_pdb', 'ligand_pdb'):
                    if values.get(key):
                        values[key] = _resolve_alias_path_for_runtime(values[key])
            science = normalize_design_params(normalized_mode, values)
            job_data.params = {**science, **transport}
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        errors = registry.validate_job_params(job_data.model_id, normalized_mode, science)
        if errors:
            raise HTTPException(422, detail={'validation_errors': errors})
        return job_data
    if normalized_model_id == 'caliby_experimental':
        from services.caliby_native import SUPPORTED_MODES, normalize_request
        if normalized_mode not in SUPPORTED_MODES:
            raise HTTPException(410, 'Historical Caliby design mode remains retired')
        transport_keys = {'caliby_request_dir', 'num_parallel_jobs', 'job_name',
                          'workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'}
        transport = {key: value for key, value in job_data.params.items() if key in transport_keys}
        try:
            science = normalize_request(normalized_mode, {
                key: value for key, value in job_data.params.items() if key not in transport_keys
            })
            if not transport.get('caliby_request_dir'):
                states = (science['structures'] if normalized_mode == 'sidechain_pack' else
                          [state for group in science['ensembles'] for state in group['states']])
                for state in states:
                    state['path'] = _resolve_alias_path_for_runtime(state['path'])
            job_data.params = {**science, **transport}
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        errors = registry.validate_job_params(job_data.model_id, normalized_mode, science)
        if errors:
            raise HTTPException(422, detail={'validation_errors': errors})
        # This model owns normalization; antibody/MSA defaults do not apply.
        return job_data
    if normalized_model_id == 'ppiflow':
        from services.ppiflow_generation import normalize_ppiflow_generation_params
        # Initial generation owns its native settings. The old antibody-parent
        # partial-flow normalizer must not inject refinement defaults here.
        transport_keys = {'ppiflow_generation_request', 'num_parallel_jobs', 'job_name',
                          'workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'}
        transport = {key: value for key, value in job_data.params.items() if key in transport_keys}
        try:
            science = normalize_ppiflow_generation_params(normalized_mode, {
                key: value for key, value in job_data.params.items() if key not in transport_keys
            })
            for key in ('target_pdb', 'framework_pdb', 'input_csv'):
                if science.get(key):
                    science[key] = _resolve_alias_path_for_runtime(science[key])
            job_data.params = {**science, **transport}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, science)
        if errors:
            raise HTTPException(status_code=422, detail={'validation_errors': errors})
        return job_data
    if normalized_model_id == 'boltzgen':
        from services.boltzgen_request_compatibility import (
            BOLTZGEN_GENERATION_PROTOCOLS, normalize_boltzgen_generation_request, parameter_contract,
        )
        if normalized_mode in BOLTZGEN_GENERATION_PROTOCOLS:
            controller = {key: value for key, value in job_data.params.items()
                          if key in {'num_parallel_jobs', 'job_name', 'workflow_adapter',
                                     '_global_resource_admission', '_global_dispatch_authority'}}
            try:
                science = normalize_boltzgen_generation_request(normalized_mode, {
                    key: value for key, value in job_data.params.items() if key not in controller
                })
                for field in parameter_contract(normalized_mode):
                    key = field['name']
                    if field['type'] == 'file' and science.get(key):
                        science[key] = _resolve_alias_path_for_runtime(science[key])
                job_data.params = {**science, **controller}
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            errors = registry.validate_job_params(job_data.model_id, job_data.mode, science)
            if errors:
                raise HTTPException(status_code=422, detail={'validation_errors': errors})
            # The separate native contract already owns its complete defaults.
            # Legacy structure/antibody normalization adds unrelated fields and
            # makes a saved request fail its own closed native contract on replay.
            return job_data
    if normalized_model_id in {'binder_refinement', 'caliby_binder'}:
        from copy import deepcopy
        definition = registry.get_internal_model_definition(normalized_model_id)
        if definition is not None:
            for field in definition.params:
                if field.default is not None:
                    job_data.params.setdefault(field.name, deepcopy(field.default))
    if (normalized_model_id, normalized_mode) == ('bindcraft2', 'campaign'):
        from services.bindcraft2_typed import normalize_new_campaign_request
        try:
            job_data.params['bindcraft2_settings'] = normalize_new_campaign_request(
                job_data.params.get('bindcraft2_settings'))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if normalized_model_id == 'bindcraft2' and normalized_mode != 'campaign':
        from services.bindcraft2_runtime import NATIVE_ACTIONS, validate_action_options
        if normalized_mode in NATIVE_ACTIONS:
            try:
                validate_action_options(normalized_mode, job_data.params.get('bc2_action_options', {}))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (normalized_model_id, normalized_mode) == ('conformational_mapping', 'map') and job_data.params.get('cm_request_path'):
        from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS
        native_entrypoint = native_entrypoint or MODEL_MODE_WORKFLOW_ENTRYPOINTS[(normalized_model_id, normalized_mode)]
        # The materialized CM document owns all scientific normalization. Generic
        # structure/Frustra defaults are not part of that sealed native contract.
        errors = registry.validate_job_params(job_data.model_id, job_data.mode,
            job_data.params, native_entrypoint=native_entrypoint)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
        return job_data
    if (normalized_model_id, normalized_mode) == ('antibody_denovo', 'nanobody_binder'):
        from services.boltzgen_request_compatibility import compile_boltzgen_settings
        try:
            job_data.params = compile_boltzgen_settings(job_data.params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if normalized_model_id == "protein_modification_experimental" and normalized_mode == "region_redesign":
        job_data.params = normalize_plr_structure_validators(job_data.params or {})
        job_data.params = normalize_plr_input_pdb_path(job_data.params, resolve_relative=resolve_allowed_path)
    elif normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":
        job_data.params = normalize_plr_input_pdb_path(job_data.params or {}, resolve_relative=resolve_allowed_path)
    if isinstance(job_data.params, dict):
        if normalized_model_id in {'fampnn', 'proteinmpnn'}:
            internal = {'sequence_design_engine', 'sequence_design_mode',
                        'sequence_design_settings_path'} & job_data.params.keys()
            if internal:
                raise HTTPException(status_code=422, detail={
                    'validation_errors': ['Server-owned sequence selection: ' + ', '.join(sorted(internal))]})
        selected_generator = (job_data.params.get('generator')
            if normalized_model_id == 'protein_modification_experimental' and normalized_mode == 'de_novo_design'
            else normalized_model_id)
        if selected_generator == 'laproteina' and normalized_model_id == 'laproteina':
            from services.laproteina_evaluation import normalize_params
            try:
                job_data.params = normalize_params(normalized_mode, job_data.params)
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if selected_generator in {'rfd4_proteina', 'complexa'}:
            native_mode = normalized_mode if selected_generator == normalized_model_id else 'generate'
            try:
                if selected_generator == 'rfd4_proteina':
                    from services.rfd4_proteina import normalize_params
                    job_data.params = normalize_params(native_mode, job_data.params)
                else:
                    from services.complexa import normalize_job_request as normalize_complexa
                    job_data.params = {**job_data.params,
                        'complexa_request': normalize_complexa(native_mode, job_data.params['complexa_request'])}
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        if _should_normalize_antibody_job_params(
            normalized_model_id,
            normalized_mode,
            job_data.params,
        ):
            job_data.params = _normalize_antibody_job_params(job_data.params)

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
    if job_data.model_id == "boltz_cp_experimental":
        # Persist the effective public setting for saved requests, clones and retries.
        job_data.params["cp_topology"] = validation_params["cp_topology"]
        job_data.params.pop("bcp_cp_topology", None)
    is_mutagenesis = 'mutagenesis_variants' in job_data.params
    if native_entrypoint is not None or (not job_data.model_id.startswith('template_') and not is_mutagenesis):
        # Trusted selected-native callers retain schema validation for aliases;
        # ordinary DTO callers cannot opt into internal model admission.
        native_kwargs = {'native_entrypoint': native_entrypoint} if native_entrypoint is not None else {}
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, validation_params, **native_kwargs)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

    from scripts.lib.protein_cad_settings import bind_new_cad_settings
    job_data.params = bind_new_cad_settings(job_data.model_id, job_data.mode, job_data.params)
    return job_data


class JobExecutionPlanPreview(BaseModel):
    """Read-only browser/agent approval authority; not an executable plan."""
    schema_name: Literal['bms.job.execution-preview.v1'] = Field(alias='schema')
    approval_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    admissible: bool
    request: dict
    plan: dict
    input_identities: list[dict]
    generated_inputs: list[dict]
    declared_expansions: list[dict] = Field(default_factory=list)
    deferred_preparation: list[str]
    blockers: list[dict]
    native_review: dict | None = None


@dataclass(frozen=True)
class ApprovedExecutionPlan:
    """Server-only native preview handoff; never a public parent-id bypass."""
    request_json: bytes
    preview_json: bytes


def _execution_plan_preview(job_data: JobCreate, declared_expansions=None) -> dict[str, Any]:
    """Read-only native compiler authority, separate from prepared execution.

    Never bind an executable MSA package here. Only the known hosted adapter's
    declared service edge can defer its input-roster blocker until dispatch.
    """
    from dataclasses import replace
    from component_runtime import digest
    from services.nextflow import compile_workflow_provision_request, cm_runtime_references
    from paths import get_data_root, get_inputs_dir, get_results_dir
    from scripts.lib.portable_inputs import discover_native_input_references
    import yaml

    request = job_data.model_copy(deep=True)
    if request.params.get('mutation_seed_refinement_trigger') and declared_expansions is None:
        raise ValueError('Mutation seed preview requires resolved parent expansion authority')
    request.execution_plan_approval = None
    invocation = compile_workflow_provision_request(request)
    plan = invocation.execution_plan
    if plan is None:
        raise ValueError('Native compiler did not produce a selected execution plan')
    metadata = plan.metadata
    roles = {row.role_id: row for row in metadata.artifact_roles}
    resolvable = set()
    for service in metadata.external_services:
        if (service.logical_id in {'protenix:msa', 'boltz2:msa'}
                and service.provider in {'colabfold_api', 'neurosnap_api'}
                and 'platform/api/services/model_msa_handoff.py' in service.authority
                and service.state in {'unresolved', 'planned', 'supplied_or_prepared'}
                and service.input_role_ids and service.output_role_ids
                and all(key in roles for key in (*service.input_role_ids, *service.output_role_ids))
                and all(roles[key].category == 'native_chain_alignments'
                        for key in service.output_role_ids)):
            resolvable.add(service.logical_id)
    deferred = tuple(row for row in metadata.blockers if row.field == 'external_service_roles' and (
        (row.component_or_dependency_id in resolvable
         and row.needed_authority == 'platform/api/services/model_msa_handoff.py; biomodstack_msa_handoff.py')
        or (row.component_or_dependency_id == 'GenerateLocalMSA' and 'boltz2:msa' in resolvable
            and row.needed_authority == 'scripts/run_local_msa.py')))
    remaining = tuple(row for row in metadata.blockers if row not in deferred)
    admissible = replace(metadata, blockers=remaining).complete

    # Existing typed native source-roster authority, not arbitrary path walking.
    # Compiler-generated payloads are bound below without writing preview files.
    inputs = discover_native_input_references(
        request.model_id, request.mode, request.params, (),
        output_dir=Path(invocation.native_parameters['out_dir']),
        allowed_roots=(get_data_root(), get_inputs_dir(), get_results_dir()),
        yaml_loader=yaml.safe_load,
        runtime_references=cm_runtime_references(request.params)
        if request.model_id == 'conformational_mapping' else None)
    authority = {
        'schema': 'bms.job.execution-preview.v1',
        'request': request.model_dump(mode='json', exclude={'execution_plan_approval'}),
        'plan': plan.to_dict(),
        'input_identities': inputs,
        'declared_expansions': declared_expansions or [],
        'generated_inputs': [{ 'relative_path': item.relative_path,
            'sha256': hashlib.sha256(item.payload).hexdigest() }
            for item in invocation.generated_inputs],
    }
    from services.native_generation_sources import native_review
    return {**authority, 'native_review': native_review(request),
        'approval_digest': digest(authority), 'admissible': admissible,
        'deferred_preparation': [row.component_or_dependency_id for row in deferred],
        'blockers': replace(metadata, blockers=remaining).to_dict()['blockers']}


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

"""Applicability projections of native owners for generated Shape monomers.

No generated source identifiers, stage counts, placement, or profile overrides
are operator settings here. Registry definitions remain authoritative for the
sequence designers and predictors; Caliby uses its ordinary ensemble contract.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model

from services.caliby_native import Conformer, EnsembleDesign


class RFD3SamplerSettings(BaseModel):
    """Pinned rc-foundry 0.1.9 sampler/engine, non-motif Shape subset.

    Native mappings are published beside each field. Defaults resolve the
    installed checkpoint and rfdiffusion3.yaml over SampleDiffusionConfig;
    declaration-only, motif-only and private source fields are not controls.
    """
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    num_timesteps: int = Field(default=200, ge=10, le=500)
    min_t: float = 0.0
    max_t: float = 1.0
    sigma_data: float = 16.0
    s_min: float = 0.0004
    s_max: float = 160.0
    p: float = 7.0
    gamma_0: float = 0.6
    gamma_min: float = 1.0
    noise_scale: float = 1.003
    step_scale: float = 1.5
    center_option: Literal["all", "diffuse"] = "all"
    s_trans: float = 1.0
    allow_realignment: bool = False
    use_classifier_free_guidance: bool = False
    cfg_scale: float = 1.5
    cfg_t_max: float | None = None
    read_sequence_from_sequence_head: bool = True
    dump_trajectories: bool = False
    align_trajectory_structures: bool = False
    low_memory_mode: bool = False


# Caliby input and global projections retain the native field annotations,
# defaults, nested types and constraints rather than copying numerical rules.
CalibySettings = create_model(
    "ShapeCalibySettings", __config__=ConfigDict(extra="forbid", strict=True),
    **{key: (field.annotation, deepcopy(field)) for key, field in EnsembleDesign.model_fields.items()
       if key not in {"ensembles", "task", "schema_version", "num_seqs_per_pdb"}},
)
CalibyInputSettings = create_model(
    "ShapeCalibyInputSettings", __config__=ConfigDict(extra="forbid", strict=True),
    **{key: (field.annotation, deepcopy(field)) for key, field in Conformer.model_fields.items()
       if key not in {"state_id", "path"}},
)

SEQUENCE_ENGINES = ("proteinmpnn", "fampnn", "caliby_experimental")
# Explicitly inapplicable fields, not a narrow list of convenient controls.
_SEQUENCE_EXCLUDED = {
    "input_pdb", "seqs_per_design", "binder_chains", "target_chains",
    "design_chain", "target_chain", "fampnn_fix_target_sidechains",
    "mpnn_extra_config", "fampnn_extra_config",
}
_MSA_KEYS = (
    "msa_provider", "colabfold_use_env", "colabfold_use_filter",
    "colabfold_use_templates", "colabfold_pairing_mode", "colabfold_pairing_strategy",
    "msa_neurosnap_coverage_percent", "msa_neurosnap_identity_percent",
    "msa_neurosnap_max_sequences", "msa_neurosnap_force_uppercase", "msa_neurosnap_pad_sequences",
)
_VALIDATORS = {
    "esmfold2": ("esmfold2", "predict", (
        "model_variant", "model_id_or_path", "local_files_only", "num_loops",
        "num_sampling_steps", "num_diffusion_samples", "seed", "esmf_use_msa",
        "msa_path", "msa_format", "msa_max_sequences", "msa_remove_insertions", *_MSA_KEYS)),
    "boltz2": ("boltz2", "predict", (
        "boltz_recycling_steps", "boltz_diffusion_samples", "boltz_max_parallel_samples",
        "boltz_sampling_steps", "boltz_use_potentials", "boltz_use_msa", "boltz_method", *_MSA_KEYS)),
    "protenix_v2": ("protenix", "predict", (
        "protenix_model_weights", "protenix_use_msa", "protenix_msa_backend",
        "protenix_use_template", "protenix_seeds", "protenix_n_sample",
        "protenix_n_step", "protenix_n_cycle", *_MSA_KEYS)),
}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _model(model_id: str):
    from model_registry import get_registry
    model = get_registry().get_internal_model_definition(model_id)
    if model is None or not model.enabled:
        raise ValueError(f"Shape model is unavailable: {model_id}")
    return model


def _registry_schema(params: list[dict], model_id: str) -> dict:
    properties = {}
    for param in params:
        kind = param["type"]
        field = {"type": {"file": "string", "directory": "string", "text": "string",
                           "textarea": "string", "float": "number", "string_list": "array"}.get(kind, kind)}
        if kind == "string_list":
            field["items"] = {"type": "string"}
        for key in ("description", "enum", "minimum", "maximum", "pattern"):
            if param.get(key) is not None:
                field[key] = deepcopy(param[key])
        if param.get("default") is not None:
            field["default"] = deepcopy(param["default"])
        if ((not param.get("required") and param.get("default") is None
             and param["name"] != "fampnn_seed")
                or model_id == "fampnn" and param["name"] == "fampnn_psce_threshold"):
            field = {"anyOf": [field, {"type": "null"}], **({"default": param["default"]} if param.get("default") is not None else {})}
        properties[param["name"]] = field
    return {"type": "object", "additionalProperties": False, "properties": properties}


def _definition(model_id: str, mode: str, params: list[dict], schema: dict,
                defaults: dict, contextual: dict | None = None) -> dict:
    contextual = contextual or {}
    definition = {"model_id": model_id, "mode": mode,
                  "model_version": _model(model_id).version,
                  "params": params, "json_schema": schema}
    return {**definition, "schema_sha256": _hash(definition),
            "initial_values": {**defaults, **contextual}, "contextual_defaults": contextual,
            "contextual_default_reason": "Preserve existing Shape invocation defaults; explicit operator values take precedence."}


def _typed_params(schema: dict, defaults: dict) -> list[dict]:
    return [{"name": key, "type": value.get("type", "number" if key == "cfg_t_max" else "object"),
             "default": deepcopy(defaults.get(key)), "required": False,
             "description": value.get("description", value.get("title", key)),
             **{k: deepcopy(value[k]) for k in ("enum", "minimum", "maximum") if k in value}}
            for key, value in schema["properties"].items()]


def sequence_definition(engine: str, sequence_count: int = 1) -> dict[str, Any]:
    if engine not in SEQUENCE_ENGINES:
        raise ValueError("unsupported Shape sequence engine")
    model = _model(engine)
    if engine == "caliby_experimental":
        schema = CalibySettings.model_json_schema()
        defaults = CalibySettings().model_dump(mode="json")
        definition = _definition(engine, "ensemble_design", _typed_params(schema, defaults), schema, defaults)
        definition["input_settings_schema"] = CalibyInputSettings.model_json_schema()
        definition["schema_sha256"] = _hash({"settings": schema, "input_settings": definition["input_settings_schema"], "model_version": model.version})
    else:
        mode = next(m for m in model.modes if m.id == "design")
        params = [p.model_dump(mode="json") for p in model.params
                  if p.name in mode.params and p.name not in _SEQUENCE_EXCLUDED]
        defaults = {p["name"]: deepcopy(p["default"]) for p in params if p.get("default") is not None}
        contextual = {}
        if engine == "fampnn":
            contextual = {"fampnn_seq_only": True, "fampnn_repack_last": False,
                          "fampnn_exclude_cys": False, "fampnn_batch_size": sequence_count,
                          "fampnn_checkpoint": "fampnn_0_3.pt", "fampnn_presort_by_length": False}
            # Omission continues using the existing per-backbone derived seed.
            defaults.pop("fampnn_seed", None)
            for param in params:
                if param["name"] == "fampnn_seed":
                    param["default"] = None
                    param["description"] += " Omit to retain Shape's per-backbone derived seed."
        definition = _definition(engine, "design", params, _registry_schema(params, engine), defaults, contextual)
    return {"engine": engine, **definition}


def _validate_registry(definition: dict, requested: Mapping[str, object], effective: dict) -> None:
    from model_registry import get_registry
    if set(requested) - set(definition["json_schema"]["properties"]):
        raise ValueError(f"unsupported Shape setting for {definition['model_id']}")
    params = {"input_pdb": "shape_backbone.pdb", "sequence": "A", **effective}
    errors = get_registry().validate_job_params(definition["model_id"], definition["mode"], params)
    if errors:
        raise ValueError("; ".join(errors))


def resolve_sequence(engine: str, requested: Mapping[str, object], sequence_count: int,
                     input_settings: Mapping[str, object] | None = None) -> tuple[dict, dict, dict]:
    definition = sequence_definition(engine, sequence_count)
    from services.sequence_designer_settings import normalize_historical_sequence_settings
    normalized = normalize_historical_sequence_settings(engine, dict(requested))
    # Known historical aliases migrate before contextual defaults. An unknown
    # native escape-hatch token is not dropped or silently reinterpreted.
    if normalized.get("fampnn_extra_config") == "":
        normalized.pop("fampnn_extra_config")
    effective = {**deepcopy(definition["initial_values"]), **normalized}
    if engine == "caliby_experimental":
        effective = CalibySettings.model_validate(effective).model_dump(mode="json")
        inputs = CalibyInputSettings.model_validate(input_settings or {}).model_dump(mode="json")
        # Exercise the actual operation owner with adapter-owned source bindings.
        EnsembleDesign.model_validate({**effective, "num_seqs_per_pdb": sequence_count,
            "ensembles": [{"ensemble_id": "generated", "states": [
                {"state_id": "generated", "path": "shape_backbone.pdb", **inputs}]}]})
    else:
        if input_settings:
            raise ValueError("sequence_input_settings applies only to Caliby")
        _validate_registry(definition, normalized, effective)
        inputs = {}
    identity = {key: deepcopy(definition[key]) for key in (
        "engine", "model_version", "schema_sha256", "initial_values", "contextual_defaults", "contextual_default_reason")}
    return effective, inputs, identity


def validator_definition(validator: str, seed: int = 0) -> dict:
    model_id, mode, keys = _VALIDATORS[validator]
    model = _model(model_id)
    by_name = {p.name: p for p in model.params}
    # Include subsequently completed model-owned native fields in this exact
    # prediction mode. Do not fork a stale, short Shape-only parameter menu.
    mode_fields = next(item.params for item in model.modes if item.id == mode)
    prefix = {"boltz2": "boltz_", "protenix_v2": "protenix_", "esmfold2": "esmf_"}[validator]
    additional = [key for key in mode_fields if key.startswith(prefix)
                  and key not in keys and key not in {"boltz_extra_config"}]
    params = [by_name[key].model_dump(mode="json") for key in (*keys, *additional)]
    defaults = {p["name"]: deepcopy(p["default"]) for p in params if p.get("default") is not None}
    contextual = {
        "esmfold2": {"esmf_use_msa": False, "seed": seed},
        "boltz2": {"boltz_use_msa": False, "boltz_sampling_steps": 50, "boltz_max_parallel_samples": 5},
        "protenix_v2": {"protenix_use_msa": False, "protenix_use_template": False, "protenix_seeds": str(seed)},
    }[validator]
    return _definition(model_id, mode, params, _registry_schema(params, model_id), defaults, contextual)


def rfd3_definition() -> dict:
    schema = RFD3SamplerSettings.model_json_schema()
    defaults = RFD3SamplerSettings().model_dump(mode="json")
    params = _typed_params(schema, defaults)
    for param in params:
        param["native_mapping"] = (param["name"] if param["name"] in {
            "read_sequence_from_sequence_head", "dump_trajectories", "align_trajectory_structures", "low_memory_mode"
        } else "inference_sampler." + param["name"])
    return _definition("protein_modification_experimental", "shape_blueprint", params, schema, defaults)


def settings_definition() -> dict:
    return {"schema": "bms_shape_settings_v1", "rfd3": rfd3_definition(),
            "sequence_engines": list(SEQUENCE_ENGINES),
            "validators": {key: validator_definition(key) for key in _VALIDATORS}}


def resolve_native(rfd3: Mapping[str, object], validators: Mapping[str, Mapping[str, object]],
                   selected: tuple[str, ...], seed: int) -> tuple[dict, dict, dict]:
    effective_rfd3 = RFD3SamplerSettings.model_validate(rfd3).model_dump(mode="json")
    active = {"esmfold2", *selected}
    if set(validators) - active:
        raise ValueError("validator_settings contains an inactive or unknown predictor")
    effective_validators, identities = {}, {"rfd3": rfd3_definition()["schema_sha256"]}
    for validator in sorted(active):
        definition = validator_definition(validator, seed)
        requested = validators.get(validator, {})
        effective = {**deepcopy(definition["initial_values"]), **deepcopy(dict(requested))}
        _validate_registry(definition, requested, effective)
        effective_validators[validator] = effective
        identities[validator] = definition["schema_sha256"]
    return effective_rfd3, effective_validators, identities

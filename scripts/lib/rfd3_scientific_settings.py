"""Portable scientific inventory for the selected rc-foundry 0.1.9 runtime.

Defaults are observations of inference_engine/rfdiffusion3.yaml, not overrides
of old requests. None for sampler fields delegates to native checkpoint config.
Scheduler placement and source artifact ownership do not belong here.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

INVENTORY_SCHEMA = "bms.rfd3.scientific-settings.v1"


def _field(kind, default, *, native=None, choices=None, minimum=None, authority="operator"):
    result = {"type": kind, "default": default, "authority": authority,
              "default_source": "rc-foundry==0.1.9 inference_engine/rfdiffusion3.yaml"}
    if native is not None:
        result["native"] = native
    if choices is not None:
        result["enum"] = choices
    if minimum is not None:
        result["minimum"] = minimum
    return result


SAMPLER_FIELDS = {
    "kind": _field("string", "default", choices=["default", "symmetry"]),
    "num_timesteps": _field("integer", 200, minimum=1),
    "step_scale": _field("number", 1.5),
    "noise_scale": _field("number", 1.003),
    "p": _field("number", 7),
    "gamma_0": _field("number", 0.6),
    "gamma_min": _field("number", 1.0),
    "s_trans": _field("number", 1.0),
    "s_jitter_origin": _field("number", 0.0),
    "center_option": _field("string", "all", choices=["all", "motif", "diffuse"]),
    "allow_realignment": _field("boolean", False),
    "use_classifier_free_guidance": _field("boolean", False),
    "cfg_features": _field("string_array", ["active_donor", "active_acceptor", "ref_atomwise_rasa"]),
    "cfg_scale": _field("number", 1.5),
    "cfg_t_max": _field("number", None),
}
# These consumed sampler fields are inherited from checkpoint config rather
# than repeated in the inference YAML. Expose them without forcing an override.
for _key, _definition in {
    "min_t": _field("number", 0), "max_t": _field("number", 1),
    "sigma_data": _field("number", 16), "s_min": _field("number", 4e-4),
    "s_max": _field("number", 160),
    "fraction_of_steps_to_fix_motif": _field("number", 0.0),
    "insert_motif_at_end": _field("boolean", True),
}.items():
    _definition.update(default_source="checkpoint config (shown value: rc-foundry 0.1.9 dataclass fallback)", hydra_add=True)
    SAMPLER_FIELDS[_key] = _definition

EXECUTION_FIELDS = {
    "diffusion_batch_size": _field("integer", 1, minimum=1),
    "inference_sampler": {"type": "object", "properties": SAMPLER_FIELDS},
    "low_memory_mode": _field("boolean", False),
    "cleanup_guideposts": _field("boolean", True),
    "cleanup_virtual_atoms": _field("boolean", True),
    "read_sequence_from_sequence_head": _field("boolean", True),
    "align_trajectory_structures": _field("boolean", False),
    "dump_prediction_metadata_json": _field("boolean", True),
    "dump_trajectories": _field("boolean", False),
    "write_full_json": _field("boolean", True, native="output_full_json", authority="profile"),
}
EXECUTION_FIELDS["diffusion_batch_size"]["default_source"] = "BMS historical batch1 profile (native default 8)"
EXECUTION_FIELDS["write_full_json"]["reason"] = "Complete native metadata is required by typed result ingestion."
GENERATION_FIELDS = {
    "min_length": _field("integer", 100, minimum=1),
    "max_length": _field("integer", 200, minimum=1),
    "num_designs": _field("integer", 8, minimum=1),
}


def integer(value: Any, field: str, minimum=1, maximum=None, *, coerce=False) -> int:
    """Keep existing API fractional coercion until its explicit policy decision."""
    if isinstance(value, bool) or (not coerce and not isinstance(value, int)):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{field} is outside its valid range")
    return result


def _validate(value, definition, key):
    import math
    if value is None and key.startswith("inference_sampler."):
        return value  # native checkpoint-derived fallback
    kind = definition["type"]
    if kind == "integer":
        value = integer(value, key, definition.get("minimum", 1))
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric")
    elif kind == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    elif kind == "string" and not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    elif kind == "string_array" and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
        raise ValueError(f"{key} must be a string array")
    if "enum" in definition and value not in definition["enum"]:
        raise ValueError(f"{key} must be one of {definition['enum']}")
    return value


def execution_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project only explicitly supplied settings; don't widen legacy defaults."""
    result = {}
    for key, definition in EXECUTION_FIELDS.items():
        if key not in raw:
            continue
        value = raw[key]
        if key == "inference_sampler":
            if not isinstance(value, Mapping) or set(value) - SAMPLER_FIELDS.keys():
                raise ValueError("unsupported inference_sampler settings")
            value = {k: _validate(v, SAMPLER_FIELDS[k], f"inference_sampler.{k}") for k, v in value.items()}
        else:
            value = _validate(value, definition, key)
        result[key] = value
    return result


def batch_plan(total: int, batch_size: int = 1) -> list[dict[str, int]]:
    integer(total, "num_designs")
    integer(batch_size, "diffusion_batch_size")
    full, remainder = divmod(total, batch_size)
    plan = []
    if full:
        plan.append({"diffusion_batch_size": batch_size, "n_batches": full})
    if remainder:
        plan.append({"diffusion_batch_size": remainder, "n_batches": 1})
    return plan


def native_overrides(execution: Mapping[str, Any]) -> list[str]:
    settings = execution_settings(execution)
    settings.setdefault("write_full_json", True)
    values = []
    for key, value in settings.items():
        if key == "diffusion_batch_size":
            continue
        if key == "inference_sampler":
            values.extend(f"{'++' if SAMPLER_FIELDS[k].get('hydra_add') else ''}inference_sampler.{k}={json.dumps(v, separators=(',', ':'))}" for k, v in value.items())
        else:
            values.append(f"{EXECUTION_FIELDS[key].get('native', key)}={json.dumps(value)}")
    values.append(f"seed={json.dumps(execution.get('seed'))}")
    return values


def task_dispatch(mode: str, native: Mapping[str, Any]) -> dict[str, Any]:
    """Report installed presence-based dispatch, never rewrite contradictory intent."""
    effective = "partial_diffusion" if native.get("partial_t") is not None else "contig"
    return {"requested": mode, "effective": effective,
            "conflicting_insertion_partial": mode == "minimal_insertion" and effective == "partial_diffusion"}


def scientific_inventory() -> dict[str, Any]:
    return {"schema": INVENTORY_SCHEMA, "runtime": "rc-foundry==0.1.9",
            "tasks": ["unconditional_monomer", "partial_diffusion", "minimal_insertion", "native_recall"],
            "generation": GENERATION_FIELDS, "execution": EXECUTION_FIELDS, "conditioning": CONDITION_FIELDS,
            "count_semantics": "num_designs is the requested total; n_batches is derived, with an exact remainder invocation",
            "fractional_input_policy": "historical API coercion retained pending explicit decision"}


# Literal installed InputSpecification fields. Source path binding is separate.
CONDITION_FIELDS = {
    **{key: {"type": "selection", "native": key} for key in (
        "contig", "unindex", "select_fixed_atoms", "select_unfixed_sequence",
        "select_hotspots", "select_hbond_donor", "select_hbond_acceptor",
        "select_buried", "select_exposed", "select_partially_buried")},
    "ligand": {"type": "string"},
    "length": {"type": "string"},
    "ori_token": {"type": "vector3"},
    "infer_ori_strategy": {"type": "string"},
    "plddt_enhanced": {"type": "boolean"},
    "is_non_loopy": {"type": "boolean"},
    "redesign_motif_sidechains": {"type": "boolean_or_string"},
}


def conditioning_settings(raw):
    if not isinstance(raw, Mapping) or set(raw) - set(CONDITION_FIELDS):
        raise ValueError("unsupported RFD3 conditioning fields")
    result = {}
    for key, value in raw.items():
        kind = CONDITION_FIELDS[key]["type"]
        if value is None:
            result[key] = value
        elif kind == "selection":
            if isinstance(value, list):
                if not all(isinstance(v, str) for v in value):
                    raise ValueError(f"{key} selector list must contain strings")
                value = ",".join(value)
            if not isinstance(value, (str, bool, Mapping)):
                raise ValueError(f"{key} requires native string, boolean or atom dictionary")
            if isinstance(value, Mapping):
                if not all(isinstance(k, str) and (isinstance(v, str) or
                    (isinstance(v, list) and all(isinstance(a, str) for a in v))) for k, v in value.items()):
                    raise ValueError(f"{key} atom dictionary values must be atom strings/lists")
                value = dict(value)
            result[key] = value
        elif kind == "vector3":
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"{key} requires three coordinates")
            result[key] = [_validate(v, {"type": "number"}, key) for v in value]
        elif kind == "boolean_or_string":
            if not isinstance(value, (bool, str)):
                raise ValueError(f"{key} requires a boolean or native selector")
            result[key] = value
        else:
            result[key] = _validate(value, CONDITION_FIELDS[key], key)
    return result

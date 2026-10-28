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
    "skip_existing": _field("boolean", True),
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
# Help is presentation-only, qualified against the published 0.1.9 wheel.
# Do not use this inventory to change sampler defaults or checkpoint precedence.
SAMPLER_SOURCE = "rfd3/model/inference_sampler.py"
SAMPLER_SOURCE_SHA256 = "e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f"
_SAMPLER_HELP = {
    "kind": ("Sampler", "Choose the default motif-aware or symmetry sampler. Symmetry sampling does not run classifier-free guidance."),
    "num_timesteps": ("Noise-schedule points", "Number of points in the inference noise schedule; updates run between consecutive points."),
    "step_scale": ("Denoising step scale", "Dimensionless multiplier of each coordinate update. The inference YAML notes that higher values trade diversity for designability."),
    "noise_scale": ("Stochastic noise scale", "Dimensionless multiplier of Gaussian noise injected during each diffusion update; fixed motif atoms receive no injected noise."),
    "p": ("Noise-schedule exponent", "Power shaping interpolation between maximum and minimum structural noise."),
    "gamma_0": ("Noise inflation", "Dimensionless inflation of the previous noise level when the next noise level exceeds gamma_min; zero removes this stochastic inflation."),
    "gamma_min": ("Noise-inflation threshold", "Structural noise threshold for gamma_0; inflation is zero at or below this threshold."),
    "s_trans": ("Translation augmentation (Å)", "Standard deviation of random translation during realignment augmentation; used when allow_realignment is enabled and the motif-fixing interval permits translation."),
    "s_jitter_origin": ("Motif-origin jitter (Å)", "Initial Gaussian translation standard deviation for fixed motif atoms; zero skips origin jitter."),
    "center_option": ("Realignment center", "Center all atoms, fixed motif atoms, or diffused atoms during realignment. Without motif atoms the native sampler centers all atoms."),
    "allow_realignment": ("Allow realignment", "Enable rotation/translation augmentation around the motif during diffusion and final motif alignment."),
    "use_classifier_free_guidance": ("Classifier-free guidance", "Blend conditional and reference denoising updates in the default sampler; the symmetry sampler does not use this branch."),
    "cfg_features": ("Guidance reference features", "Native features zeroed in the reference classifier-free-guidance pass."),
    "cfg_scale": ("Guidance strength", "Dimensionless multiplier of the conditional/reference difference; one leaves the conditional update unchanged."),
    "cfg_t_max": ("Guidance noise cutoff", "The default sampler applies guidance when the next structural noise level exceeds this value. A resolved native null removes the cutoff; an explicit request null delegates to checkpoint configuration."),
    "min_t": ("Schedule start", "Start of the unit schedule interpolation interval, before conversion to structural noise."),
    "max_t": ("Schedule end", "End of the unit schedule interpolation interval, before conversion to structural noise."),
    "sigma_data": ("Data noise scale", "Scale multiplying the structural noise schedule and used to rescale saved noisy trajectories."),
    "s_min": ("Minimum schedule noise", "Lower structural noise parameter before multiplication by sigma_data."),
    "s_max": ("Maximum schedule noise", "Upper structural noise parameter before multiplication by sigma_data."),
    "fraction_of_steps_to_fix_motif": ("Initial motif-fixing fraction", "Fraction of schedule transitions used to keep motif positioning fixed during realignment augmentation."),
    "insert_motif_at_end": ("Reinsert motif at end", "Reinsert ground-truth motif coordinates during final realignment when motif atoms are present and realignment is enabled."),
}
_EXECUTION_HELP = {
    "diffusion_batch_size": ("Diffusion batch size", "Number of samples per native diffusion batch; BMS plans an exact remainder batch for the requested total."),
    "inference_sampler": ("Sampler and classifier-free guidance", "Omitted sampler values inherit the native configuration. Explicit null delegates to the checkpoint; displayed YAML/dataclass references are not a checkpoint readback."),
    "low_memory_mode": ("Low-memory tokenization", "Use the native memory-efficient tokenization path."),
    "skip_existing": ("Skip existing outputs", "Skip examples for which native output already exists."),
    "cleanup_guideposts": ("Remove guideposts", "Remove native guidepost atoms from written structures."),
    "cleanup_virtual_atoms": ("Remove virtual atoms", "Remove native virtual atoms from written structures."),
    "read_sequence_from_sequence_head": ("Read sequence head", "Use the model sequence-head predictions for written residue identities."),
    "align_trajectory_structures": ("Align trajectory structures", "Align native trajectory structures for output."),
    "dump_prediction_metadata_json": ("Write prediction metadata", "Write native prediction metadata as JSON."),
    "dump_trajectories": ("Write diffusion trajectories", "Retain native noisy and denoised diffusion trajectories."),
    "write_full_json": ("Write complete native metadata", "BMS profile retains full native JSON for typed result ingestion; this is profile-owned, not a second sampler setting."),
}
for _fields, _help, _source in ((SAMPLER_FIELDS, _SAMPLER_HELP, SAMPLER_SOURCE),
                               (EXECUTION_FIELDS, _EXECUTION_HELP, "rfd3/engine.py")):
    for _key, (_title, _effect) in _help.items():
        _fields[_key].update(title=_title, description=f"{_effect} Source: rc-foundry==0.1.9 {_source}.",
                             **{"x-help-source": f"rc-foundry==0.1.9:{_source}"})

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
    "symmetry": {"type": "symmetry", "native": "symmetry", "properties": {
        "id": {"type": ["string", "null"], "default": None,
               "description": "Native symmetry group: Cn, Dn, T, O or I."},
        "is_unsym_motif": {"type": ["string", "null"], "default": None,
                           "description": "Native contig/ligand names not to symmetrize."},
        "is_symmetric_motif": {"type": "boolean", "default": True},
    }, "additionalProperties": True},
    "extra": {"type": "object", "additionalProperties": True,
              "description": "Native output metadata, not conditioning features; runtime adds task/example identifiers."},
    "dialect": {"type": "integer",
                "description": "Native parser dispatch: values below 2 select legacy schema; otherwise release schema. Not a sampling option."},
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
        elif kind == "symmetry":
            if not isinstance(value, Mapping):
                raise ValueError("symmetry requires the native SymmetryConfig object")
            # Native SymmetryConfig permits additional options; do not invent a
            # second group/applicability policy here. Its parser is authoritative.
            result[key] = dict(value)
        elif kind == "object":
            if not isinstance(value, Mapping):
                raise ValueError(f"{key} requires a metadata object")
            result[key] = dict(value)
        elif key == "dialect":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("dialect requires an integer")
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

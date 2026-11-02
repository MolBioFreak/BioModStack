"""Designer and validator settings the Resequence loop forwards to its workflow.

Only settings the loop workflow actually consumes are listed. Each value is checked by the model registry,
the same owner that checks a direct launch of that engine, so ranges, enums and types stay model-owned and
no default is invented here. Source structures, chains and positions belong to the loop and are refused.
"""
from __future__ import annotations

from typing import Any

# engine -> (registry model, registry mode, accepted setting names)
DESIGNERS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "mpnn": ("proteinmpnn", "design", (
        "seqs_per_design", "mpnn_temperature", "mpnn_omitAAs", "mpnn_checkpoint_type", "mpnn_checkpoint_model",
        "mpnn_backbone_noise", "mpnn_num_connections", "mpnn_output_intermediates", "mpnn_relax_max_cycles",
        "mpnn_relax_output", "mpnn_relax_seqs_per_cycle", "mpnn_relax_convergence_rmsd",
        "mpnn_relax_convergence_score", "mpnn_relax_convergence_max_cycles",
    )),
    "fampnn": ("fampnn", "design", (
        "seqs_per_design", "fampnn_checkpoint", "fampnn_seed", "fampnn_presort_by_length", "fampnn_timestep_mode",
        "fampnn_timestep_start", "fampnn_timestep_end", "fampnn_scn_num_steps", "fampnn_scn_timestep_mode",
        "fampnn_scn_timestep_start", "fampnn_scn_timestep_end", "fampnn_scn_step_scale", "fampnn_scn_s_churn",
        "fampnn_scn_s_noise", "fampnn_scn_s_t_min", "fampnn_scn_s_t_max", "fampnn_temperature", "fampnn_exclude_cys",
        "fampnn_psce_threshold", "fampnn_mutation_top_n", "fampnn_mutation_min_log_odds_delta", "fampnn_seq_only",
        "fampnn_num_steps", "fampnn_batch_size", "fampnn_repack_last",
    )),
}

# validator -> (registry model, registry mode, accepted names, catalog name -> workflow name)
VALIDATORS: dict[str, tuple[str, str, tuple[str, ...], dict[str, str]]] = {
    "protenix_v2": ("protenix", "predict", (
        "protenix_n_sample", "protenix_n_step", "protenix_n_cycle", "protenix_seeds", "protenix_use_msa",
    ), {}),
    "boltz2": ("boltz2", "predict", (
        "boltz_recycling_steps", "boltz_sampling_steps", "boltz_diffusion_samples", "boltz_max_parallel_samples",
        "boltz_seed", "boltz_use_potentials", "boltz_method",
    ), {}),
    "esmfold2": ("esmfold2", "predict", (
        "model_variant", "num_loops", "num_sampling_steps", "num_diffusion_samples", "seed",
    ), {name: "esmf_" + name for name in ("model_variant", "num_loops", "num_sampling_steps",
                                          "num_diffusion_samples", "seed")}),
}


def _checked(label: str, model_id: str, mode: str, allowed: tuple[str, ...], settings: dict[str, Any]) -> dict[str, Any]:
    settings = dict(settings or {})
    unknown = [name for name in settings if name not in allowed]
    if unknown:
        raise ValueError(f"{label}: {', '.join(sorted(unknown))} "
                         f"{'is' if len(unknown) == 1 else 'are'} not a setting the loop passes to this engine")
    if not settings:
        return {}
    from model_registry import get_registry

    registry = get_registry()
    model = registry.get_model(model_id)
    if model is None:
        raise ValueError(f"{label}: {model_id} is unavailable")
    # The loop supplies the structure or sequence; give required inputs a placeholder so only settings are judged.
    stubs = {param.name: "A" for param in model.params if param.required and param.name not in settings}
    errors = registry.validate_job_params(model_id, mode, {**stubs, **settings})
    if errors:
        raise ValueError(f"{label}: " + "; ".join(errors))
    return settings


def designer_params(engine: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Workflow parameters for ProteinMPNN or FAMPNN settings."""
    model_id, mode, allowed = DESIGNERS[engine]
    return _checked(model_id, model_id, mode, allowed, settings)


def validator_params(selected: list[str], settings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Workflow parameters for the selected validators' settings; settings of unselected validators are not sent."""
    unknown = [name for name in (settings or {}) if name not in VALIDATORS]
    if unknown:
        raise ValueError(f"{', '.join(sorted(unknown))} is not a loop validator")
    params: dict[str, Any] = {}
    for validator in selected:
        if validator not in VALIDATORS or not (settings or {}).get(validator):
            continue
        model_id, mode, allowed, rename = VALIDATORS[validator]
        checked = _checked(model_id, model_id, mode, allowed, settings[validator])
        params.update({rename.get(name, name): value for name, value in checked.items()})
    return params

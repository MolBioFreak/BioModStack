"""Model-owned La-Proteina profiles, native controls and pinned build seams.

No model imports at API discovery time. Native YAML remains the default authority.
The build seams select upstream features and observe outputs, not a BMS sampler.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE_REVISION = "cde5de3ead6e4d76f367da6dc5174be9913ef6ca"
RUNTIME_RELEASE = "laproteina-bms-2"
PROFILES = {
    "ucond_notri": ("LD1_ucond_notri_512.ckpt", "AE1_ucond_512.ckpt"),
    "ucond_tri": ("LD2_ucond_tri_512.ckpt", "AE1_ucond_512.ckpt"),
    "ucond_notri_long": ("LD3_ucond_notri_800.ckpt", "AE2_ucond_800.ckpt"),
    "motif_idx_aa": ("LD4_motif_idx_aa.ckpt", "AE3_motif.ckpt"),
    "motif_idx_tip": ("LD5_motif_idx_tip.ckpt", "AE3_motif.ckpt"),
    "motif_uidx_aa": ("LD6_motif_uidx_aa.ckpt", "AE3_motif.ckpt"),
    "motif_uidx_tip": ("LD7_motif_uidx_tip.ckpt", "AE3_motif.ckpt"),
}


def resolve_profile(preset: str) -> dict:
    # These are the historically supported BMS names; do not guess a replacement.
    ld, ae = PROFILES[preset]
    return {"preset": preset, "config_name": "inference_" + preset,
            "ld": ld, "ae": ae,
            "task": "unconditional" if preset.startswith("ucond_") else "motif_scaffolding",
            "feature_mode": "indexed_tip" if preset == "motif_idx_tip" else "all_atom",
            "source_revision": SOURCE_REVISION, "runtime_release": RUNTIME_RELEASE}


# Closed typed scalar inventory consumed by existing CAD schema/preparer/UI.
# Defaults are inherited by native composition, not copied into another sampler.
# Optional means absent => preset-derived; explicit False/0/None are preserved.
CONTROLS = {
    "seed": {"type": "integer", "native": "seed", "default": 5},
    "num_steps": {"type": "integer", "native": "generation.args.nsteps", "default": 400},
    "self_cond": {"type": "boolean", "native": "generation.args.self_cond", "default": True},
    "save_trajectory_every": {"type": "integer", "native": "generation.args.save_trajectory_every", "default": 0},
    "max_nsamples_per_batch": {"type": "integer", "native": "generation.dataset.max_nsamples_per_batch", "default_by_task": {"unconditional": 10, "motif_scaffolding": 5}},
}
for _modality in ("bb_ca", "local_latents"):
    for _name, _type, _choices in (
        ("schedule.mode", "string", ["uniform", "power", "log"]),
        ("schedule.p", "number", None),
        ("gt.mode", "string", ["1-t/t", "tan", "1/t"]),
        ("gt.p", "number", None), ("gt.clamp_val", ["number", "null"], None),
        ("simulation_step_params.sampling_mode", "string", ["vf", "sc", "vf_ss", "vf_ss_sc_sn"]),
        ("simulation_step_params.sc_scale_noise", "number", None),
        ("simulation_step_params.sc_scale_score", "number", None),
        ("simulation_step_params.t_lim_ode", "number", None),
        ("simulation_step_params.t_lim_ode_below", "number", None),
        ("simulation_step_params.center_every_step", "boolean", None),
    ):
        _key = _modality + "_" + _name.replace("simulation_step_params.", "").replace(".", "_")
        CONTROLS[_key] = {"type": _type, "native": f"generation.model.{_modality}.{_name}"}
        if _choices:
            CONTROLS[_key]["enum"] = _choices

MOTIF_CONTROLS = {
    "motif_atom_spec": {"type": ["string", "null"], "native": "motif_atom_spec"},
    "contig_string": {"type": "string", "native": "contig_string"},
    "segment_order": {"type": "string", "native": "segment_order"},
    "atom_selection_mode": {"type": "string", "native": "atom_selection_mode",
                            "enum": ["all_atom", "ca", "bb3o", "tip_atoms"]},
    "motif_min_length": {"type": ["integer", "null"], "native": "motif_min_length"},
    "motif_max_length": {"type": ["integer", "null"], "native": "motif_max_length"},
}


# Meaning authority: pinned product_space_flow_matcher.get_schedule/get_gt,
# rdn_flow_matcher.simulation_step, gen_dataset and motif_utils.parse_motif.
_CONTROL_HELP = {
    "seed": ("Random seed", "Integer seed for native generation."),
    "num_steps": ("Integration steps", "Number of vector-field/score integration steps."),
    "self_cond": ("Self-conditioning", "Feed the preceding clean-sample prediction back to the network."),
    "save_trajectory_every": ("Trajectory interval", "Retain post-step states every N integration steps; zero disables retention."),
    "max_nsamples_per_batch": ("Samples per batch", "Maximum samples grouped into one native generation batch; does not change the requested total."),
    "schedule_mode": ("Time-grid schedule", "Partition the unit time interval uniformly, by a power, or logarithmically."),
    "schedule_p": ("Time-grid exponent", "Dimensionless shape parameter for power/log schedules; unused by uniform scheduling."),
    "gt_mode": ("Noise-injection schedule", "Select the native g(t) function used by stochastic integration."),
    "gt_p": ("Noise-injection parameter", "Dimensionless p parameter of the selected native g(t) function."),
    "gt_clamp_val": ("Noise-injection ceiling", "Upper clamp on g(t); null removes the upper clamp."),
    "sampling_mode": ("Integration method", "vf: vector-field ODE; sc: stochastic score sampling; vf_ss: score-scaled ODE; vf_ss_sc_sn: score-scaled ODE with stochastic noise. Native endpoint switches still apply."),
    "sc_scale_noise": ("Stochastic noise scale", "Dimensionless factor inside sqrt(2*g(t)*scale*dt); used by sc and the interior of vf_ss_sc_sn, not plain vf."),
    "sc_scale_score": ("Score scale", "Dimensionless multiplier of the predicted score in score-scaled ODE updates; native endpoint branches can use their own fixed scale."),
    "t_lim_ode": ("Late ODE switch", "Unit-interval time threshold above which sc and vf_ss_sc_sn switch to the native late ODE branch."),
    "t_lim_ode_below": ("Early stochastic switch", "Unit-interval time threshold below which vf_ss and vf_ss_sc_sn use the native early stochastic branch."),
    "center_every_step": ("Center each step", "Subtract the masked mean after each integration update for this modality."),
    "motif_atom_spec": ("Motif atoms", "Atom-level selectors, for example A64: [O, CG]; overrides residue/range extraction. Null uses contig-based extraction."),
    "contig_string": ("Motif contig", "Native residue ranges and scaffold segments, for example A1-7/A28-79; used when motif_atom_spec is null or omitted."),
    "segment_order": ("Segment order", "Native segment-order string retained in the motif CSV for residue/range extraction."),
    "atom_selection_mode": ("Motif atom selection", "ca selects CA; bb3o selects N/CA/C/O; all_atom selects available atoms; tip_atoms selects sidechain tips. Unused with motif_atom_spec."),
    "motif_min_length": ("Minimum motif/scaffold length", "Lower residue-count bound passed to the native motif parser; null leaves that bound unset."),
    "motif_max_length": ("Maximum motif/scaffold length", "Upper residue-count bound passed to the native motif parser; null leaves that bound unset."),
}


# Discovery-only snapshot of Hydra 1.3.1 composition at SOURCE_REVISION.
# Verified against all seven upstream configs; never used to construct a request.
_PROFILE_COMMON = {
    "seed": 5, "num_steps": 400, "self_cond": True, "save_trajectory_every": 0,
    "bb_ca_schedule_mode": "log", "bb_ca_schedule_p": 2.0, "bb_ca_gt_mode": "1/t",
    "bb_ca_gt_p": 1.0, "bb_ca_gt_clamp_val": None, "bb_ca_sampling_mode": "sc",
    "bb_ca_sc_scale_noise": 0.1, "bb_ca_sc_scale_score": 1.0,
    "bb_ca_t_lim_ode": 0.98, "bb_ca_t_lim_ode_below": 0.02,
    "local_latents_schedule_mode": "power", "local_latents_schedule_p": 2.0,
    "local_latents_gt_mode": "tan", "local_latents_gt_p": 1.0,
    "local_latents_gt_clamp_val": None, "local_latents_sampling_mode": "sc",
    "local_latents_sc_scale_noise": 0.1, "local_latents_sc_scale_score": 1.0,
    "local_latents_t_lim_ode": 0.98, "local_latents_t_lim_ode_below": 0.02,
    "local_latents_center_every_step": False,
}


def native_profile_defaults() -> dict:
    """Informational source defaults, before BMS or user overrides, not execution state."""
    profiles = {}
    for name in PROFILES:
        unconditional = name.startswith("ucond_")
        values = {**_PROFILE_COMMON, "max_nsamples_per_batch": 10 if unconditional else 5,
                  "bb_ca_center_every_step": unconditional}
        if name == "ucond_notri_long":
            values.update(bb_ca_sc_scale_noise=0.15, local_latents_sc_scale_noise=0.05)
        profiles[name] = values
    return {"source_revision": SOURCE_REVISION, "composition": "upstream Hydra 1.3.1 profile before BMS and user overrides",
            "scope": "Discovery only; motif task fields depend on the selected task and are not included.",
            "profiles": profiles}


def native_task_catalog() -> dict:
    """Lossless generated snapshot of the pinned native motif dictionary.

    Discovery only: never merge these values into authored sparse overrides.
    Regenerate from configs/generation/motif_dict.yaml at SOURCE_REVISION;
    source_sha256 qualifies the original YAML, not a rewritten catalog.
    """
    return json.loads(Path(__file__).with_name("laproteina_native_tasks.json").read_text())


def settings_schema() -> dict:
    """Typed meanings; profile observations are separate from schema defaults."""
    properties = {}
    for key, spec in (CONTROLS | MOTIF_CONTROLS).items():
        modality = next((prefix for prefix in ("bb_ca_", "local_latents_") if key.startswith(prefix)), "")
        title, help_text = _CONTROL_HELP[key[len(modality):]]
        if modality:
            title = ("Backbone · " if modality == "bb_ca_" else "Local latents · ") + title
        applicability = ("Motif scaffolding: custom source or explicitly authored named-task override. Omitted named-task fields retain the composed motif dictionary."
                         if key in MOTIF_CONTROLS else "Unconditional and motif generation.")
        properties[key] = {**{k: v for k, v in spec.items() if k in {"type", "enum"}},
                           "title": title, "description": f"{help_text} {applicability} Native: {spec['native']}.",
                           "x-native-key": spec["native"], "x-applicability": applicability,
                           "x-default-source": ("native_profile_defaults; upstream composition before overrides" if key in CONTROLS
                                                else "selected motif task composition; not resolved by discovery")}
    for key, label in {
        "bb_ca_gt_clamp_val": "No upper clamp (null)",
        "local_latents_gt_clamp_val": "No upper clamp (null)",
        "motif_atom_spec": "Contig-based extraction (null)",
        "motif_min_length": "No lower length bound (null)",
        "motif_max_length": "No upper length bound (null)",
    }.items():
        properties[key]["x-null-label"] = label
    return {"type": "object", "additionalProperties": False, "properties": properties}


EVALUATION_CONTROLS = {
    **{key: {"type": "boolean"} for key in (
        "compute_designability", "compute_codesignability", "compute_co_sequence_recovery",
        "designability_motif_eval", "codesignability_motif_eval", "compute_motif_rmsd",
        "keep_folding_outputs")},
    **{key: {"type": "array", "items": {"type": "string", "enum": ["ca", "bb3o", "all_atom"]}}
       for key in ("designability_modes", "codesignability_modes", "motif_rmsd_modes")},
    # The pin mentions other models, but only imports/implements run_esmfold.
    **{key: {"type": "array", "items": {"type": "string", "enum": ["esmfold"]}}
       for key in ("designability_folding_models", "codesignability_folding_models")},
}


def evaluation_schema() -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["samples_slot", "config_slot", "job_id", "ncpus"],
            "properties": {
                "samples_slot": {"type": "string", "minLength": 1, "x-native-source-kind": "file", "description": "Retained native sample directory, including motif CSV."},
                "config_slot": {"type": "string", "minLength": 1, "x-native-source-kind": "file", "description": "Original composed generation configuration."},
                "job_id": {"type": "integer"}, "ncpus": {"type": "integer"},
                "metrics": {"type": "object", "additionalProperties": False, "properties": EVALUATION_CONTROLS}}}


def cad_parameter_definitions() -> list[dict]:
    """Additive typed catalog leaves for the existing CAD model registry.

    Existing CAD owns num_steps and the older motif/source fields. Optional
    controls deliberately have no synthetic default: native profile inheritance
    remains authoritative, including the long-chain noise values.
    """
    fields = {k: v for k, v in CONTROLS.items() if k != "num_steps"}
    fields["motif_atom_spec"] = MOTIF_CONTROLS["motif_atom_spec"]
    result = []
    for order, (key, spec) in enumerate(fields.items(), start=100):
        kind = spec["type"]
        if isinstance(kind, list):
            kind = next(t for t in kind if t != "null")
        item = {"name": "laproteina_" + key, "type": "float" if kind == "number" else kind,
                "description": "Native " + spec["native"] + "; omitted values inherit the selected preset.",
                "required": False, "ui_group": "La-Proteina", "ui_order": order}
        if "enum" in spec:
            item["enum"] = spec["enum"]
        result.append(item)
    return result


def apply_controls(config, settings: dict) -> None:
    from omegaconf import OmegaConf
    for key, spec in CONTROLS.items():
        if key in settings:
            OmegaConf.update(config, spec["native"], settings[key], merge=False)


def apply_runtime_patches(repo: Path) -> None:
    """Build-time only, exact pinned-source substitutions; never edit at inference."""
    def replace(relative, old, new, count=1):
        path = repo / relative
        text = path.read_text()
        if text.count(old) != count:
            raise ValueError(f"Pinned build seam mismatch: {relative}: {old!r}")
        path.write_text(text.replace(old, new))

    replace("proteinfoundation/nn/feature_factory.py",
            '            elif f == "bulk_all_atom_xmotif" or f == "x_motif":',
            '            elif f == "x_motif" and os.environ.get("LAPROTEINA_MOTIF_FEATURE") == "indexed_tip":\n'
            '                return XmotifBulkTipSeqFeat(**kwargs)\n'
            '            elif f == "bulk_all_atom_xmotif" or f == "x_motif":')
    # Native trajectory option was hardcoded to zero and its storage discarded.
    # Observe post-step CA/latent tensors without decoding or drawing extra RNG.
    replace("proteinfoundation/proteina.py", "        save_trajectory_every = 0",
            '        save_trajectory_every = self.inf_cfg.args.get("save_trajectory_every", 0)')
    replace("proteinfoundation/flow_matching/product_space_flow_matcher.py",
            "            [{} for _ in range(nsamples)]", "            trajectory = []")
    replace("proteinfoundation/flow_matching/product_space_flow_matcher.py",
            '            additional_info = {\n                "mask": mask,\n            }',
            '            additional_info = {\n                "mask": mask,\n            }\n'
            '            if save_trajectory_every > 0:\n'
            '                additional_info["trajectory"] = trajectory')
    replace("proteinfoundation/flow_matching/product_space_flow_matcher.py",
            '                    simulation_step_params=simulation_step_params,\n                )',
            '                    simulation_step_params=simulation_step_params,\n                )\n'
            '                if save_trajectory_every > 0 and (step + 1) % save_trajectory_every == 0:\n'
            '                    trajectory.append({"step": step + 1, "state": {\n'
            '                        key: value.detach().cpu().clone() for key, value in x.items()}})')
    replace("proteinfoundation/proteina.py", "        # Format the generated samples back to proteins",
            '        if save_trajectory_every > 0:\n'
            '            from pathlib import Path\n'
            '            trajectory_root = Path("native_trajectories")\n'
            '            trajectory_root.mkdir(exist_ok=True)\n'
            '            torch.save({"batch_index": batch_idx, "mask": extra_info["mask"].detach().cpu(),\n'
            '                        "trajectory": extra_info.pop("trajectory")},\n'
            '                       trajectory_root / f"batch_{batch_idx}.pt")\n\n'
            '        # Format the generated samples back to proteins')
    # Writer-side exact index: emitted after each original PDB write, preserving
    # tensor amino-acid identities even where a residue has no written atoms.
    replace("proteinfoundation/generate.py",
            '    predictions = [sample for sublist in predictions for sample in sublist]',
            '    sample_membership = [(batch_index, sample_index)\n'
            '                         for batch_index, batch in enumerate(predictions)\n'
            '                         for sample_index in range(len(batch))]\n'
            '    predictions = [sample for sublist in predictions for sample in sublist]', count=2)
    replace("proteinfoundation/generate.py", '            no_indexing=True,\n        )',
            '            no_indexing=True,\n        )\n'
            '        from openfold.np.residue_constants import restypes_with_x\n'
            '        aatype = residue_type.detach().cpu().tolist()\n'
            '        batch_index, sample_index = sample_membership[j]\n'
            '        record = {"native_sample_id": dir_name, "job_id": job_id,\n'
            '                  "prediction_index": j, "batch_index": batch_index, "sample_index": sample_index,\n'
            '                  "structure_path": os.path.relpath(pdb_path, root_path),\n'
            '                  "aatype": aatype, "sequence": "".join(restypes_with_x[i] for i in aatype)}\n'
            '        with open(os.path.join(root_path, "sample_index.jsonl"), "a") as index_file:\n'
            '            index_file.write(json.dumps(record) + "\\n")', count=2)
    (repo / "bms-runtime.json").write_text(json.dumps({
        "source_revision": SOURCE_REVISION, "runtime_release": RUNTIME_RELEASE,
        "seams": ["indexed-tip feature dispatch", "post-step latent trajectory observation", "native writer sample index"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-runtime-patches", type=Path, required=True)
    apply_runtime_patches(parser.parse_args().apply_runtime_patches)

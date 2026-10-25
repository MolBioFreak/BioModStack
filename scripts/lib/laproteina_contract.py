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


def settings_schema() -> dict:
    """Native advanced settings; the existing CAD owner owns source/path fields."""
    return {"type": "object", "additionalProperties": False,
            "properties": {key: {k: v for k, v in spec.items() if k in {"type", "enum"}}
                           for key, spec in (CONTROLS | MOTIF_CONTROLS).items()}}


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
            "required": ["source_directory", "config_path", "job_id", "ncpus"],
            "properties": {
                "source_directory": {"type": "string", "description": "Retained native output directory, including motif CSV when applicable."},
                "config_path": {"type": "string", "description": "Retained composed native generation config."},
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

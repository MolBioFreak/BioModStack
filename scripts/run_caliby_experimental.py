#!/usr/bin/env python3
"""Run the two standalone native operations without binder reinterpretation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from caliby_runtime import dump_json, load_caliby_model, preflight_caliby_runtime


def sampling_overrides(request):
    result = {"scn_packing_cfg": {"num_steps": request["scn_num_steps"], "step_scale": request["scn_step_scale"]}}
    if request["task"] == "ensemble_design":
        result.update({
            "ensemble_ignore_res_idx_mismatch": request["ensemble_ignore_res_idx_mismatch"],
            "gaussian_conformers_cfg": {"n_conformers": request["gaussian_n_conformers"], "noise_std": request["gaussian_noise_std"]},
            "potts_sampling_cfg": {"regularization": request["potts_regularization"], "potts_sweeps": request["potts_sweeps"],
                                   "potts_proposal": request["potts_proposal"], "rejection_step": request["potts_rejection_step"],
                                   "potts_only_cond": request["potts_only_cond"]},
        })
    return result


def run(request_dir: Path, output_dir: Path):
    document = json.loads((request_dir / "request.json").read_text())
    request = document["effective"]
    task = request["task"]
    if request.get("schema_version") != 1 or task not in {"ensemble_design", "sidechain_pack"}:
        raise ValueError("Only versioned ensemble_design and sidechain_pack are supported")
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime = preflight_caliby_runtime(task=task, model_name=request.get("model_name", ""),
                                       packer_model_name=request.get("packer_model_name"))
    from caliby.api import _merge_sampling_cfg
    from omegaconf import OmegaConf

    model = load_caliby_model(runtime["model_name"])
    kwargs = {"batch_size": request["batch_size"], "num_workers": request["num_workers"],
              "sampling_overrides": sampling_overrides(request)}
    if task == "ensemble_design":
        kwargs.update({key: request[key] for key in ("num_seqs_per_pdb", "omit_aas", "temperature", "verbose")})
    effective_sampling = OmegaConf.to_container(_merge_sampling_cfg(model.sampling_cfg, **kwargs), resolve=True)

    def input_path(state):
        path = (request_dir / state["path"]).resolve()
        if not path.is_relative_to(request_dir.resolve()):
            raise ValueError("Input path escapes prepared Caliby tree")
        return str(path)

    state_by_native_id = {}
    if task == "ensemble_design":
        import pandas as pd
        mapping, constraints = {}, []
        for ensemble in request["ensembles"]:
            mapping[ensemble["ensemble_id"]] = [input_path(state) for state in ensemble["states"]]
            for index, state in enumerate(ensemble["states"]):
                native_id = Path(state["path"]).stem
                state_by_native_id[native_id] = {"ensemble_id": ensemble["ensemble_id"], "state_id": state["state_id"],
                                                "primary": index == 0, "conditioning_states": ensemble["states"]}
                constraints.append({"pdb_key": native_id, **{key: state[key] for key in
                                    ("fixed_pos_seq", "fixed_pos_scn", "fixed_pos_override_seq", "pos_restrict_aatype", "symmetry_pos")}})
        results = model.ensemble_sample(mapping, out_dir=str(output_dir / "native"),
                                        pos_constraint_df=pd.DataFrame(constraints),
                                        use_primary_res_type=request["use_primary_res_type"], **kwargs)
    else:
        for state in request["structures"]:
            state_by_native_id[Path(state["path"]).stem] = {"state_id": state["state_id"]}
        # Native run_sidechain_packing fixes seq_cond_mask to token_resolved_mask;
        # its decoder constructs outputs from the original encoded_seq, not samples.
        results = model.sidechain_pack([input_path(state) for state in request["structures"]],
                                       out_dir=str(output_dir / "native"), **kwargs)

    dump_json(output_dir / "native_results.json", results)
    records = []
    for index, native_id in enumerate(results.get("example_id", [])):
        native_path = Path(results["out_pdb"][index]).resolve()
        relative_path = native_path.relative_to(output_dir.resolve()).as_posix()
        row = {key: values[index] for key, values in results.items()}
        records.append({"record_id": str(index), "native": row, "structure_path": relative_path,
                        "source": state_by_native_id.get(native_id),
                        "operation": task})
    payload = {"schema": "bms.caliby-native-results.v1", "task": task, "request": document,
               "effective_sampling": effective_sampling, "runtime": runtime, "records": records}
    dump_json(output_dir / "caliby_results.json", payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    run(Path(args.request_dir).resolve(), Path(args.output_dir).resolve())


if __name__ == "__main__":
    main()

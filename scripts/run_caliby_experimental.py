#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from caliby_runtime import (
    build_conformer_mapping,
    collect_structure_paths,
    dump_json,
    filter_structure_paths_by_name,
    load_caliby_model,
    load_constraints_dataframe,
    load_json,
    maybe_clean_inputs,
    maybe_run_self_consistency,
    normalize_sampling_results,
    parse_omit_aas,
    read_name_list,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Caliby experimental sequence-design tasks and normalize outputs for BMS.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    request = load_json(Path(args.request).resolve())
    output_dir = Path(args.output_dir).resolve()
    raw_pdb_dir = output_dir / "raw" / "pdbs"
    raw_meta_dir = output_dir / "raw" / "metadata"
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)

    task = str(request.get("task") or "sequence_design").strip().lower()
    model_name = str(request.get("model_name") or "soluble_caliby_v1").strip()
    packer_model = str(request.get("packer_model_name") or "caliby_packer_010").strip()
    batch_size = int(request.get("batch_size") or 4)
    num_workers = int(request.get("num_workers") or 8)
    clean_num_workers = int(request.get("clean_num_workers") or 2)
    temperature = float(request.get("temperature") or 0.1)
    num_seqs_per_pdb = int(request.get("num_seqs_per_pdb") or 4)
    sampling_overrides = request.get("sampling_overrides") or {}
    omit_aas = parse_omit_aas(request.get("omit_aas"))
    name_filter = read_name_list(Path(str(request.get("pdb_name_list")))) if request.get("pdb_name_list") else set()
    pos_constraint_df = load_constraints_dataframe(Path(str(request.get("pos_constraint_csv")))) if request.get("pos_constraint_csv") else None

    prefix = "".join(
        ch if ch.isalnum() else "_"
        for ch in str(request.get("job_name") or "caliby_experimental").strip()
    ).strip("_") or "caliby_experimental"

    run_dir = output_dir / "caliby_output"
    run_dir.mkdir(parents=True, exist_ok=True)

    if task in {"sequence_design", "sidechain_pack"}:
        input_dir = Path(str(request.get("input_pdb_dir"))).resolve()
        pdb_paths = filter_structure_paths_by_name(collect_structure_paths(input_dir), name_filter)
        cleaned_pdb_paths = maybe_clean_inputs(
            pdb_paths=pdb_paths,
            cleaned_dir=run_dir / "cleaned_pdbs",
            num_workers=clean_num_workers,
        )
    else:
        cleaned_pdb_paths = []

    if task == "sequence_design":
        model = load_caliby_model(model_name)
        results = model.sample(
            cleaned_pdb_paths,
            out_dir=str(run_dir / "sequence_design"),
            num_seqs_per_pdb=num_seqs_per_pdb,
            batch_size=batch_size,
            omit_aas=omit_aas,
            num_workers=num_workers,
            temperature=temperature,
            pos_constraint_df=pos_constraint_df,
            sampling_overrides=sampling_overrides,
        )
        self_consistency = maybe_run_self_consistency(
            model=model,
            designed_paths=list(results.get("out_pdb", [])),
            output_dir=run_dir / "self_consistency",
            enabled=bool(request.get("run_self_consistency_eval")),
            num_models=int(request.get("self_consistency_num_models") or 5),
            num_recycles=int(request.get("self_consistency_num_recycles") or 3),
            use_multimer=bool(request.get("self_consistency_use_multimer")),
        )
        manifest = normalize_sampling_results(
            results=results,
            output_pdb_dir=raw_pdb_dir,
            output_meta_dir=raw_meta_dir,
            prefix=prefix,
            source="caliby",
            stage_mode=task,
            extra_metadata={"caliby_model": model_name},
            self_consistency=self_consistency,
        )
    elif task == "ensemble_design":
        conformer_dir = Path(str(request.get("conformer_dir"))).resolve()
        pdb_to_conformers = build_conformer_mapping(conformer_dir, name_filter)
        model = load_caliby_model(model_name)
        results = model.ensemble_sample(
            pdb_to_conformers,
            out_dir=str(run_dir / "ensemble_design"),
            num_seqs_per_pdb=num_seqs_per_pdb,
            batch_size=batch_size,
            omit_aas=omit_aas,
            num_workers=num_workers,
            temperature=temperature,
            pos_constraint_df=pos_constraint_df,
            sampling_overrides=sampling_overrides,
        )
        self_consistency = maybe_run_self_consistency(
            model=model,
            designed_paths=list(results.get("out_pdb", [])),
            output_dir=run_dir / "self_consistency",
            enabled=bool(request.get("run_self_consistency_eval")),
            num_models=int(request.get("self_consistency_num_models") or 5),
            num_recycles=int(request.get("self_consistency_num_recycles") or 3),
            use_multimer=bool(request.get("self_consistency_use_multimer")),
        )
        manifest = normalize_sampling_results(
            results=results,
            output_pdb_dir=raw_pdb_dir,
            output_meta_dir=raw_meta_dir,
            prefix=prefix,
            source="caliby",
            stage_mode=task,
            extra_metadata={"caliby_model": model_name},
            self_consistency=self_consistency,
        )
    elif task == "sidechain_pack":
        packer = load_caliby_model(packer_model)
        results = packer.sidechain_pack(
            cleaned_pdb_paths,
            out_dir=str(run_dir / "sidechain_pack"),
            batch_size=batch_size,
            num_workers=num_workers,
            sampling_overrides=sampling_overrides,
        )
        manifest = normalize_sampling_results(
            results={**results, "seq": [], "U": [], "input_seq": []},
            output_pdb_dir=raw_pdb_dir,
            output_meta_dir=raw_meta_dir,
            prefix=prefix,
            source="caliby",
            stage_mode=task,
            extra_metadata={"caliby_packer_model": packer_model},
        )
    else:
        raise ValueError(f"Unsupported Caliby experimental task: {task}")

    dump_json(output_dir / "design_manifest.json", manifest)


if __name__ == "__main__":
    main()


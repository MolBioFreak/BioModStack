#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from caliby_runtime import (
    collect_structure_paths,
    load_caliby_model,
    load_constraints_dataframe,
    maybe_clean_inputs,
    maybe_run_self_consistency,
    normalize_sampling_results,
    parse_omit_aas,
    parse_bool,
    remap_constraint_dataframe_to_cleaned_paths,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Caliby sequence design on a directory of antibody/nanobody candidate structures.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-name", default="soluble_caliby_v1")
    parser.add_argument("--num-seqs-per-pdb", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--clean-num-workers", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--omit-aas", default="C")
    parser.add_argument("--pos-constraint-csv", default="")
    parser.add_argument("--sampling-overrides-json", default="")
    parser.add_argument("--run-self-consistency-eval", type=parse_bool, default=False)
    parser.add_argument("--self-consistency-num-models", type=int, default=5)
    parser.add_argument("--self-consistency-num-recycles", type=int, default=3)
    parser.add_argument("--self-consistency-use-multimer", type=parse_bool, default=False)
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    raw_pdb_dir = output_dir
    raw_meta_dir = output_dir
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)

    pdb_paths = collect_structure_paths(input_dir)
    cleaned = maybe_clean_inputs(
        pdb_paths=pdb_paths,
        cleaned_dir=output_dir / "cleaned_pdbs",
        num_workers=args.clean_num_workers,
    )
    model = load_caliby_model(args.model_name)
    pos_constraint_df = load_constraints_dataframe(Path(args.pos_constraint_csv).resolve()) if args.pos_constraint_csv else None
    pos_constraint_df = remap_constraint_dataframe_to_cleaned_paths(
        pos_constraint_df,
        original_paths=pdb_paths,
        cleaned_paths=cleaned,
    )
    sampling_overrides = json.loads(args.sampling_overrides_json) if args.sampling_overrides_json.strip() else {}
    results = model.sample(
        cleaned,
        out_dir=str(output_dir / "designed"),
        num_seqs_per_pdb=args.num_seqs_per_pdb,
        batch_size=args.batch_size,
        omit_aas=parse_omit_aas(args.omit_aas),
        num_workers=args.num_workers,
        temperature=args.temperature,
        pos_constraint_df=pos_constraint_df,
        sampling_overrides=sampling_overrides,
    )
    self_consistency = maybe_run_self_consistency(
        model=model,
        designed_paths=list(results.get("out_pdb", [])),
        output_dir=output_dir / "self_consistency",
        enabled=bool(args.run_self_consistency_eval),
        num_models=max(1, args.self_consistency_num_models),
        num_recycles=max(1, args.self_consistency_num_recycles),
        use_multimer=bool(args.self_consistency_use_multimer),
    )

    manifest = normalize_sampling_results(
        results=results,
        output_pdb_dir=output_dir,
        output_meta_dir=output_dir,
        prefix="caliby",
        source="caliby",
        stage_mode="sequence_design",
        extra_metadata={"caliby_model": args.model_name},
        self_consistency=self_consistency,
    )

    (output_dir / "caliby_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    jsonl_path = output_dir / "caliby_metadata.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            metadata_path = Path(str(item["metadata_path"]))
            handle.write(metadata_path.read_text(encoding="utf-8").strip())
            handle.write("\n")


if __name__ == "__main__":
    main()

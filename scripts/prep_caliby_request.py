#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from caliby_runtime import dump_json, parse_bool


def _copy_optional_file(path_value: str, input_dir: Path) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    source = Path(raw).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")
    target = input_dir / source.name
    if source != target:
        shutil.copy2(source, target)
    return str(target)


def _copy_optional_dir(path_value: str, input_dir: Path, subdir_name: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    source = Path(raw).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"Input directory not found: {source}")
    target = input_dir / subdir_name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return str(target)


def _load_json_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile Caliby experimental params into a normalized request.")
    parser.add_argument("--job-id", default="unknown")
    parser.add_argument("--job-name", default="caliby_experimental")
    parser.add_argument("--task", required=True, choices=["sequence_design", "ensemble_design", "sidechain_pack"])
    parser.add_argument("--input-pdb-dir", default="")
    parser.add_argument("--conformer-dir", default="")
    parser.add_argument("--pdb-name-list", default="")
    parser.add_argument("--pos-constraint-csv", default="")
    parser.add_argument("--model-name", default="soluble_caliby_v1")
    parser.add_argument("--packer-model-name", default="caliby_packer_010")
    parser.add_argument("--num-seqs-per-pdb", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--clean-num-workers", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--omit-aas", default="C")
    parser.add_argument("--run-self-consistency-eval", type=parse_bool, default=False)
    parser.add_argument("--self-consistency-num-models", type=int, default=5)
    parser.add_argument("--self-consistency-num-recycles", type=int, default=3)
    parser.add_argument("--self-consistency-use-multimer", type=parse_bool, default=False)
    parser.add_argument("--sampling-overrides-json", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dir", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)

    copied_pdb_dir = _copy_optional_dir(args.input_pdb_dir, input_dir, "native_pdbs")
    copied_conformer_dir = _copy_optional_dir(args.conformer_dir, input_dir, "conformers")
    copied_name_list = _copy_optional_file(args.pdb_name_list, input_dir)
    copied_constraint_csv = _copy_optional_file(args.pos_constraint_csv, input_dir)

    if args.task in {"sequence_design", "sidechain_pack"} and not copied_pdb_dir:
        raise ValueError(f"{args.task} requires --input-pdb-dir")
    if args.task == "ensemble_design" and not copied_conformer_dir:
        raise ValueError("ensemble_design requires --conformer-dir")

    request = {
        "job_id": args.job_id,
        "job_name": args.job_name,
        "task": args.task,
        "input_pdb_dir": copied_pdb_dir,
        "conformer_dir": copied_conformer_dir,
        "pdb_name_list": copied_name_list,
        "pos_constraint_csv": copied_constraint_csv,
        "model_name": args.model_name,
        "packer_model_name": args.packer_model_name,
        "num_seqs_per_pdb": args.num_seqs_per_pdb,
        "batch_size": args.batch_size,
        "num_workers": args.num_workers,
        "clean_num_workers": args.clean_num_workers,
        "temperature": args.temperature,
        "omit_aas": args.omit_aas,
        "run_self_consistency_eval": args.run_self_consistency_eval,
        "self_consistency_num_models": args.self_consistency_num_models,
        "self_consistency_num_recycles": args.self_consistency_num_recycles,
        "self_consistency_use_multimer": args.self_consistency_use_multimer,
        "sampling_overrides": _load_json_text(args.sampling_overrides_json),
    }

    dump_json(Path(args.output).resolve(), request)


if __name__ == "__main__":
    main()


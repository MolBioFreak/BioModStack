#!/usr/bin/env python3
"""Stage a BioModStack ConforNets experimental monomer request.

This script builds the upstream ConforNets benchmark asset layout for a single
protein chain and writes a request JSON consumed by run_confornets_inference.py.
It does not fabricate model outputs; it only validates and stages real inputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

VALID_AA = set("ACDEFGHIKLMNPQRSTVWYBXZUO")
VALID_TASKS = {"diversity", "mse", "transfer"}


def _bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_id(value: str | None, default: str) -> str:
    text = (value or default).strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text or default


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_sequence(sequence: str) -> str:
    seq = re.sub(r"\s+", "", sequence or "").upper()
    if not seq:
        raise SystemExit("ConforNets requires a non-empty single-chain protein sequence")
    if any(separator in seq for separator in (":", ",", ";", "/")):
        raise SystemExit("ConforNets experimental workflow is monomer-only; multi-chain separators are not allowed")
    invalid = sorted(set(seq) - VALID_AA)
    if invalid:
        raise SystemExit(f"Sequence contains non-amino-acid characters: {''.join(invalid)}")
    return seq


def _stage_reference(source: str | None, name: str, ref_dir: Path) -> dict[str, Any] | None:
    if not source:
        return None
    src = Path(source).expanduser()
    if not src.exists() or not src.is_file():
        raise SystemExit(f"Reference structure not found: {source}")
    suffix = src.suffix.lower()
    if suffix not in {".pdb", ".cif", ".mmcif"}:
        raise SystemExit(f"Reference structure must be PDB/mmCIF, got {source}")
    upstream_suffix = ".cif" if suffix == ".mmcif" else suffix
    safe_name = _safe_id(name, src.stem)
    dst = ref_dir / f"{safe_name}{upstream_suffix}"
    ref_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {
        "name": safe_name,
        "source_path": str(src.resolve()),
        "staged_path": str(dst.resolve()),
        "sha256": _sha256_file(dst),
    }


def _write_query_json(query_path: Path, query_id: str, sequence: str, chain_id: str) -> None:
    payload = {
        "chains": [
            {
                "molecule_type": "PROTEIN",
                "chain_ids": [chain_id],
                "sequence": sequence,
            }
        ]
    }
    query_path.parent.mkdir(parents=True, exist_ok=True)
    query_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_references_csv(path: Path, test_case: str, references: list[dict[str, Any]]) -> None:
    fieldnames = ["test_case", "pdbidchain1", "pdbidchain2"]
    row = {"test_case": test_case, "pdbidchain1": "", "pdbidchain2": ""}
    for index, ref in enumerate(references[:2], start=1):
        row[f"pdbidchain{index}"] = ref["name"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    task = (args.task or "diversity").strip().lower()
    if task not in VALID_TASKS:
        raise SystemExit(f"Unsupported ConforNets task {task!r}; expected one of {sorted(VALID_TASKS)}")

    k_confornets = int(args.k_confornets)
    if task == "diversity" and k_confornets < 2:
        raise SystemExit(
            "ConforNets diversity requires at least 2 ConforNets; "
            "k_confornets=1 produces no pairwise diversity loss/gradient upstream"
        )

    sequence = _validate_sequence(args.sequence)
    chain_id = _safe_id(args.chain_id or "A", "A")[:8]
    benchmark = _safe_id(args.benchmark_name or "bms_confornets", "bms_confornets")
    test_case = _safe_id(args.test_case_name or "monomer_case", "monomer_case")
    query_id = test_case

    assets_dir = Path(args.assets_dir).resolve()
    bench_dir = assets_dir / benchmark
    tc_dir = bench_dir / "test_cases" / test_case
    ref_dir = tc_dir / "reference"
    query_dir = tc_dir / "query"
    bench_dir.mkdir(parents=True, exist_ok=True)

    references = []
    staged_ref_1 = _stage_reference(args.reference_pdb_1, args.reference_name_1 or "ref_a", ref_dir)
    staged_ref_2 = _stage_reference(args.reference_pdb_2, args.reference_name_2 or "ref_b", ref_dir)
    references = [ref for ref in (staged_ref_1, staged_ref_2) if ref is not None]

    if task == "mse" and not references:
        raise SystemExit("ConforNets MSE steering requires at least one reference structure")
    if len(references) > 2:
        raise SystemExit("Upstream ConforNets supports at most two reference states per test case")
    if task == "transfer" and not (args.confornet_path or args.mse_dir):
        raise SystemExit("ConforNets transfer requires --confornet-path or --mse-dir")

    _write_query_json(query_dir / f"{query_id}.json", query_id, sequence, chain_id)
    _write_references_csv(bench_dir / "references.csv", test_case, references)
    (tc_dir / "align_metric_info.json").write_text(
        json.dumps({"queries": {}, "references": {}}, indent=2),
        encoding="utf-8",
    )
    (assets_dir / "config.yaml").write_text(
        yaml.safe_dump({"defaults": {"rmsd_threshold": 3.0}, "benchmarks": {benchmark: {"rmsd_threshold": 3.0}}}, sort_keys=True),
        encoding="utf-8",
    )

    request = {
        "schema_version": 1,
        "workflow": "confornets_experimental",
        "job_id": args.job_id or "unknown",
        "job_name": args.job_name or "confornets_experimental",
        "task": task,
        "benchmark": benchmark,
        "test_case": test_case,
        "query_id": query_id,
        "assets_dir": str(assets_dir),
        "sequence": sequence,
        "chain_id": chain_id,
        "references": references,
        "params": {
            "checkpoint_path": args.checkpoint_path,
            "config_yaml": args.config_yaml or "",
            "confornets_repo_path": args.confornets_repo_path,
            "skip_msa": _bool(args.skip_msa),
            "num_runs": int(args.num_runs),
            "k_confornets": k_confornets,
            "num_samples": int(args.num_samples),
            "max_steps": int(args.max_steps),
            "save_steps": args.save_steps,
            "num_recycles": int(args.num_recycles),
            "num_diffusion_steps": int(args.num_diffusion_steps),
            "lr": float(args.lr),
            "grad_clip": float(args.grad_clip),
            "compute_confidence": _bool(args.compute_confidence),
            "save_full_confidence": _bool(args.save_full_confidence),
            "confornet_path": args.confornet_path or "",
            "mse_dir": args.mse_dir or "",
            "source_test_cases": args.source_test_cases or "",
        },
        "input_hashes": {
            "sequence_sha256": _sha256_text(sequence),
            "references": {ref["name"]: ref["sha256"] for ref in references},
        },
        "upstream_contract": {
            "monomer_only": True,
            "max_reference_states": 2,
            "outputs_are_real_upstream_artifacts": True,
        },
    }
    return request


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage a BioModStack ConforNets experimental monomer request")
    parser.add_argument("--job-id", default="unknown")
    parser.add_argument("--job-name", default="confornets_experimental")
    parser.add_argument("--task", default="diversity", choices=sorted(VALID_TASKS))
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--chain-id", default="A")
    parser.add_argument("--benchmark-name", default="bms_confornets")
    parser.add_argument("--test-case-name", default="monomer_case")
    parser.add_argument("--reference-pdb-1", default="")
    parser.add_argument("--reference-name-1", default="ref_a")
    parser.add_argument("--reference-pdb-2", default="")
    parser.add_argument("--reference-name-2", default="ref_b")
    parser.add_argument("--checkpoint-path", required=True)
    parser.add_argument("--config-yaml", default="")
    parser.add_argument("--confornets-repo-path", required=True)
    parser.add_argument("--skip-msa", default="false")
    parser.add_argument("--num-runs", type=int, default=2)
    parser.add_argument("--k-confornets", type=int, default=2)
    parser.add_argument("--num-samples", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=21)
    parser.add_argument("--save-steps", default="5,10,15,20")
    parser.add_argument("--num-recycles", type=int, default=0)
    parser.add_argument("--num-diffusion-steps", type=int, default=200)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--grad-clip", type=float, default=10.0)
    parser.add_argument("--compute-confidence", default="false")
    parser.add_argument("--save-full-confidence", default="false")
    parser.add_argument("--confornet-path", default="")
    parser.add_argument("--mse-dir", default="")
    parser.add_argument("--source-test-cases", default="")
    parser.add_argument("--assets-dir", default="confornets_assets")
    parser.add_argument("--output", default="confornets_request.json")
    args = parser.parse_args()

    request = build_request(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request, indent=2), encoding="utf-8")
    print(f"Wrote ConforNets request: {output}")
    print(f"Staged assets: {request['assets_dir']}/{request['benchmark']}")


if __name__ == "__main__":
    main()

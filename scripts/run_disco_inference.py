#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value).strip())
    return cleaned.strip("_") or "pcad"


def _load_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_sequence(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            continue
        lines.append("".join(ch for ch in stripped if ch.isalpha() or ch == ":"))
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DISCO inference and normalize outputs for BMS.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    disco = request["disco"]
    output_dir = Path(args.output_dir).resolve()
    raw_pdb_dir = output_dir / "raw" / "pdbs"
    raw_meta_dir = output_dir / "raw" / "metadata"
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "disco_output"
    run_dir.mkdir(parents=True, exist_ok=True)

    input_json_path = disco.get("input_json_path") or disco.get("compiled_input_json")
    if not input_json_path:
        raise ValueError("DISCO request does not contain an input_json_path")

    cmd = [
        sys.executable,
        "runner/inference.py",
        f"experiment={disco.get('experiment') or 'designable'}",
        f"effort={disco.get('effort') or 'fast'}",
        f"input_json_path={input_json_path}",
        f"dump_dir={run_dir}",
    ]
    if disco.get("checkpoint_path"):
        cmd.append(f"load_checkpoint_path={disco['checkpoint_path']}")
    if disco.get("use_deepspeed_evo_attention"):
        cmd.append("use_deepspeed_evo_attention=true")
    else:
        cmd.append("use_deepspeed_evo_attention=false")
    if disco.get("seeds"):
        seeds = ",".join(str(seed) for seed in disco["seeds"])
        cmd.append(f"seeds=[{seeds}]")
    else:
        cmd.append(f"num_inference_seeds={int(disco.get('num_inference_seeds') or 8)}")

    env = os.environ.copy()
    if disco.get("cutlass_path"):
        env["CUTLASS_PATH"] = disco["cutlass_path"]

    log_path = output_dir / "disco.log"
    with log_path.open("w", encoding="utf-8") as log_handle:
        subprocess.run(cmd, cwd="/opt/disco", env=env, check=True, stdout=log_handle, stderr=subprocess.STDOUT)

    prefix = _sanitize_name(request["job_name"])
    manifest: list[dict[str, object]] = []
    pdb_files = sorted(run_dir.rglob("*.pdb"))

    for index, pdb_path in enumerate(pdb_files, start=1):
        source_name = pdb_path.stem
        design_id = f"{prefix}_disco_{index:04d}"
        target_pdb = raw_pdb_dir / f"{design_id}.pdb"
        shutil.copy2(pdb_path, target_pdb)

        sequence_txt = next(iter(sorted((run_dir / "sequences").glob(f"{source_name}*.txt"))), None)
        sequence = _load_sequence(sequence_txt)
        summary_path = next(iter(sorted(run_dir.rglob(f"{source_name}_summary_confidence_sample_*.json"))), None)
        summary = _load_summary(summary_path) if summary_path else {}

        metadata = {
            "design_id": design_id,
            "designed_sequence": sequence,
            "source": "disco",
            "source_model": "DISCO",
            "generator_family": "protein_cad_experimental",
            "generator_mode": request["task"],
            "source_name": source_name,
            "ranking_score": summary.get("ranking_score"),
            "ptm": summary.get("ptm"),
            "plddt": summary.get("plddt"),
        }
        metadata.update(summary)

        metadata_path = raw_meta_dir / f"generator_{design_id}.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        manifest.append(
            {
                "design_id": design_id,
                "sequence": sequence,
                "structure_path": str(target_pdb),
                "metadata_path": str(metadata_path),
                "source_name": source_name,
            }
        )

    (output_dir / "design_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

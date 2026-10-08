#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",
}


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value).strip())
    return cleaned.strip("_") or "pcad"


def _extract_sequence_from_pdb(path: Path) -> str:
    residues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ATOM"):
            continue
        resname = line[17:20].strip().upper()
        chain_id = line[21].strip() or "A"
        resseq = line[22:27].strip()
        key = (chain_id, resseq)
        if key in seen:
            continue
        seen.add(key)
        residues.append(THREE_TO_ONE.get(resname, "X"))
    return "".join(residues)


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _configure_laproteina(request: dict) -> tuple[str, str]:
    upstream_root = Path(os.environ.get("LAPROTEINA_REPO", "/opt/laproteina")).resolve()
    configs_root = upstream_root / "configs"
    custom_root = configs_root / "bms"
    custom_root.mkdir(parents=True, exist_ok=True)
    _copy_tree(configs_root / "generation", custom_root / "generation")

    task = request["task"]
    laproteina = request["laproteina"]
    target_lengths = request["target_lengths"]

    if task == "unconditional":
        preset_map = {
            "ucond_tri": "inference_ucond_tri",
            "ucond_notri": "inference_ucond_notri",
            "ucond_notri_long": "inference_ucond_notri_long",
        }
        base_config_name = preset_map.get(laproteina["preset"], "inference_ucond_tri")
        base_inference = yaml.safe_load((configs_root / f"{base_config_name}.yaml").read_text(encoding="utf-8"))
        generation_cfg = yaml.safe_load((configs_root / "generation" / "uncond_codes.yaml").read_text(encoding="utf-8"))
        generation_cfg["dataset"]["nlens_cfg"]["nres_lens"] = target_lengths
        generation_cfg["dataset"]["nsamples"] = int(laproteina["samples_per_length"])
        generation_cfg["args"]["nsteps"] = int(laproteina["num_steps"])
        _write_yaml(custom_root / "generation" / "bms_uncond_codes.yaml", generation_cfg)
        base_inference["defaults"] = ["inference_base", {"generation": "bms_uncond_codes"}, "_self_"]
        config_name = "inference_bms"
    else:
        preset_map = {
            "motif_idx_aa": "inference_motif_idx_aa",
            "motif_idx_tip": "inference_motif_idx_tip",
            "motif_uidx_aa": "inference_motif_uidx_aa",
            "motif_uidx_tip": "inference_motif_uidx_tip",
        }
        base_config_name = preset_map.get(laproteina["preset"], "inference_motif_idx_aa")
        base_inference = yaml.safe_load((configs_root / f"{base_config_name}.yaml").read_text(encoding="utf-8"))
        generation_cfg = yaml.safe_load((configs_root / "generation" / "motif.yaml").read_text(encoding="utf-8"))
        motif_dict_cfg = yaml.safe_load((configs_root / "generation" / "motif_dict.yaml").read_text(encoding="utf-8"))
        generation_cfg["dataset"]["nsamples"] = int(request["num_designs"])
        generation_cfg["args"]["nsteps"] = int(laproteina["num_steps"])
        generation_cfg["defaults"] = [{"motif_dict": "bms_motif_dict"}, "_self_"]

        motif_task_name = laproteina.get("motif_task_name") or "BMS_CUSTOM"
        if laproteina.get("motif_pdb") and laproteina.get("contig_string"):
            motif_dict_cfg.setdefault("dataset", {}).setdefault("motif_dict_cfg", {})
            motif_dict_cfg["dataset"]["motif_dict_cfg"][motif_task_name] = {
                "contig_string": laproteina["contig_string"],
                "motif_pdb_path": laproteina["motif_pdb"],
                "motif_only": True,
                "motif_min_length": laproteina["motif_min_length"] or min(target_lengths),
                "motif_max_length": laproteina["motif_max_length"] or max(target_lengths),
                "segment_order": laproteina["segment_order"] or "A",
                "atom_selection_mode": laproteina["atom_selection_mode"] or "all_atom",
            }
        elif motif_task_name not in motif_dict_cfg.get("dataset", {}).get("motif_dict_cfg", {}):
            raise ValueError(f"La-Proteina motif task not found: {motif_task_name}")

        generation_cfg["dataset"]["motif_task_name"] = motif_task_name
        _write_yaml(custom_root / "generation" / "bms_motif.yaml", generation_cfg)
        _write_yaml(custom_root / "generation" / "bms_motif_dict.yaml", motif_dict_cfg)
        base_inference["defaults"] = ["inference_base", {"generation": "bms_motif"}, "_self_"]
        base_inference.setdefault("generation", {}).setdefault("dataset", {})["motif_task_name"] = motif_task_name
        config_name = "inference_bms"

    checkpoint_dir = laproteina.get("checkpoint_dir")
    if checkpoint_dir:
        base_inference["ckpt_path"] = checkpoint_dir
        if base_inference.get("autoencoder_ckpt_path", "").startswith("./checkpoints_laproteina/"):
            base_inference["autoencoder_ckpt_path"] = str(Path(checkpoint_dir) / Path(base_inference["autoencoder_ckpt_path"]).name)

    _write_yaml(custom_root / f"{config_name}.yaml", base_inference)
    return str(upstream_root), config_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Run La-Proteina inference and normalize outputs for BMS.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir).resolve()
    raw_pdb_dir = output_dir / "raw" / "pdbs"
    raw_meta_dir = output_dir / "raw" / "metadata"
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)
    upstream_root, config_name = _configure_laproteina(request)
    laproteina = request["laproteina"]

    env = os.environ.copy()
    if laproteina.get("data_path"):
        env["DATA_PATH"] = laproteina["data_path"]

    inference_root = Path(upstream_root) / "inference" / config_name
    if request["task"] == "motif_scaffolding":
        motif_name = laproteina.get("motif_task_name") or "BMS_CUSTOM"
        inference_root = Path(upstream_root) / "inference" / f"{config_name}_{motif_name}"
    existing_samples = {path.name for path in inference_root.glob("job_*")} if inference_root.exists() else set()

    cmd = [
        sys.executable,
        str(Path(upstream_root) / "proteinfoundation" / "generate.py"),
        "--config_name",
        config_name,
        "--config_subdir",
        "bms",
    ]

    log_path = output_dir / "laproteina.log"
    with log_path.open("w", encoding="utf-8") as log_handle:
        subprocess.run(cmd, cwd=Path(upstream_root), env=env, check=True, stdout=log_handle, stderr=subprocess.STDOUT)

    manifest: list[dict[str, object]] = []
    prefix = _sanitize_name(request["job_name"])
    sample_dirs = sorted(
        [
            path
            for path in inference_root.glob("job_*")
            if path.is_dir() and path.name not in existing_samples
        ]
    )
    for index, sample_dir in enumerate(sample_dirs, start=1):
        pdb_candidates = sorted(sample_dir.glob("*.pdb"))
        if not pdb_candidates:
            continue
        source_pdb = pdb_candidates[0]
        design_id = f"{prefix}_laproteina_{index:04d}"
        target_pdb = raw_pdb_dir / f"{design_id}.pdb"
        shutil.copy2(source_pdb, target_pdb)
        sequence = _extract_sequence_from_pdb(target_pdb)
        metadata = {
            "design_id": design_id,
            "designed_sequence": sequence,
            "source": "laproteina",
            "source_model": "La-Proteina",
            "generator_family": "protein_cad_experimental",
            "generator_mode": request["task"],
            "source_sample_dir": str(sample_dir),
            "length": len(sequence),
        }
        metadata_path = raw_meta_dir / f"generator_{design_id}.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        manifest.append(
            {
                "design_id": design_id,
                "sequence": sequence,
                "structure_path": str(target_pdb),
                "metadata_path": str(metadata_path),
                "source_sample_dir": str(sample_dir),
            }
        )

    (output_dir / "design_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

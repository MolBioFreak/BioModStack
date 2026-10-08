#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import gemmi


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value).strip())
    return cleaned.strip("_") or "protein_hunter"


def _resolve_input_path(input_dir: Path, raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate)
    staged = input_dir / candidate.name
    return str(staged if staged.exists() else candidate)


def _resolve_container_weight_path(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.exists():
        return str(candidate)
    host_weights = os.environ.get("BMS_HOST_WEIGHTS", "").strip()
    mounted_weights = os.environ.get("BMS_MOUNTED_WEIGHTS", "/weights").strip() or "/weights"
    if host_weights:
        try:
            relative = candidate.relative_to(host_weights)
        except ValueError:
            return text
        remapped = Path(mounted_weights) / relative
        return str(remapped)
    return text


def _ensure_workdir(output_dir: Path) -> Path:
    work_dir = output_dir / "protein_hunter_workspace"
    work_dir.mkdir(parents=True, exist_ok=True)
    ligandmpnn_link = work_dir / "LigandMPNN"
    if not ligandmpnn_link.exists():
        ligandmpnn_link.symlink_to(Path("/opt/Protein-Hunter/LigandMPNN"))
    return work_dir


def _structure_sequence(path: Path, preferred_chain: str | None = None) -> str:
    structure = gemmi.read_structure(str(path))
    model = structure[0]
    residues: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    chain_names: Iterable[str]
    if preferred_chain:
        chain_names = [preferred_chain]
    else:
        chain_names = [chain.name for chain in model]
    for chain_name in chain_names:
        chain = model.find_chain(chain_name)
        if chain is None:
            continue
        for residue in chain:
            if residue.het_flag not in ("A", ""):
                continue
            key = (chain_name, residue.seqid.num.__str__(), residue.name)
            if key in seen:
                continue
            seen.add(key)
            residues.append(gemmi.find_tabulated_residue(residue.name).one_letter_code or "X")
    return "".join(residues)


def _convert_cif_to_pdb(source_cif: Path, target_pdb: Path) -> None:
    structure = gemmi.read_structure(str(source_cif))
    structure.write_pdb(str(target_pdb))


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _copy_or_convert_structure(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.suffix.lower() == ".cif":
        _convert_cif_to_pdb(source_path, target_path)
    else:
        shutil.copy2(source_path, target_path)


def _normalize_boltz_outputs(run_root: Path, output_dir: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    raw_pdb_dir = output_dir / "raw" / "pdbs"
    raw_meta_dir = output_dir / "raw" / "metadata"
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)

    prefix = _sanitize_name(request["job_name"])
    manifest: list[dict[str, Any]] = []

    high_rows = _read_csv_rows(run_root / "summary_high_iptm.csv")
    if high_rows:
        for index, row in enumerate(high_rows, start=1):
            source_pdb = run_root / "high_iptm_pdb" / str(row.get("pdb_filename") or "")
            if not source_pdb.exists():
                continue
            design_id = f"{prefix}_protein_hunter_boltz_{index:04d}"
            target_pdb = raw_pdb_dir / f"{design_id}.pdb"
            _copy_or_convert_structure(source_pdb, target_pdb)
            metadata = {
                "design_id": design_id,
                "source": "protein_hunter",
                "source_model": "Protein Hunter (Boltz)",
                "generator_family": "protein_hunter_experimental",
                "generator_backend": "boltz",
                "generator_mode": request["task"],
                "designed_sequence": row.get("sequence") or _structure_sequence(target_pdb, preferred_chain="A"),
                "iptm": _coerce_float(row.get("iptm")),
                "plddt": _coerce_float(row.get("plddt")),
                "iplddt": _coerce_float(row.get("iplddt")),
                "alanine_count": _coerce_float(row.get("alanine_count")),
                "run_id": row.get("run_id"),
                "cycle": row.get("cycle"),
                "source_structure": str(source_pdb),
            }
            metadata_path = raw_meta_dir / f"generator_{design_id}.json"
            _dump_json(metadata_path, metadata)
            manifest.append({
                "design_id": design_id,
                "sequence": metadata["designed_sequence"],
                "structure_path": str(target_pdb),
                "metadata_path": str(metadata_path),
                "source_structure": str(source_pdb),
            })
        return manifest

    summary_rows = _read_csv_rows(run_root / "summary_all_runs.csv")
    for index, row in enumerate(summary_rows, start=1):
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        source_pdb = run_root / "0_protein_hunter_design" / f"run_{run_id}" / f"{request['job_name']}_run_{run_id}_best_structure.pdb"
        if not source_pdb.exists():
            continue
        design_id = f"{prefix}_protein_hunter_boltz_{index:04d}"
        target_pdb = raw_pdb_dir / f"{design_id}.pdb"
        _copy_or_convert_structure(source_pdb, target_pdb)
        metadata = {
            "design_id": design_id,
            "source": "protein_hunter",
            "source_model": "Protein Hunter (Boltz)",
            "generator_family": "protein_hunter_experimental",
            "generator_backend": "boltz",
            "generator_mode": request["task"],
            "designed_sequence": row.get("best_seq") or _structure_sequence(target_pdb, preferred_chain="A"),
            "best_iptm": _coerce_float(row.get("best_iptm")),
            "best_plddt": _coerce_float(row.get("best_plddt")),
            "best_cycle": row.get("best_cycle"),
            "run_id": run_id,
            "source_structure": str(source_pdb),
        }
        metadata_path = raw_meta_dir / f"generator_{design_id}.json"
        _dump_json(metadata_path, metadata)
        manifest.append({
            "design_id": design_id,
            "sequence": metadata["designed_sequence"],
            "structure_path": str(target_pdb),
            "metadata_path": str(metadata_path),
            "source_structure": str(source_pdb),
        })
    return manifest


def _normalize_chai_outputs(run_root: Path, output_dir: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    raw_pdb_dir = output_dir / "raw" / "pdbs"
    raw_meta_dir = output_dir / "raw" / "metadata"
    raw_pdb_dir.mkdir(parents=True, exist_ok=True)
    raw_meta_dir.mkdir(parents=True, exist_ok=True)

    prefix = _sanitize_name(request["job_name"])
    manifest: list[dict[str, Any]] = []

    high_rows = _read_csv_rows(run_root / "summary_high_iptm.csv")
    if high_rows:
        for index, row in enumerate(high_rows, start=1):
            source_cif = run_root / "high_iptm_cif" / str(row.get("cif_filename") or "")
            if not source_cif.exists():
                continue
            design_id = f"{prefix}_protein_hunter_chai_{index:04d}"
            target_pdb = raw_pdb_dir / f"{design_id}.pdb"
            _copy_or_convert_structure(source_cif, target_pdb)
            metadata = {
                "design_id": design_id,
                "source": "protein_hunter",
                "source_model": "Protein Hunter (Chai)",
                "generator_family": "protein_hunter_experimental",
                "generator_backend": "chai",
                "generator_mode": request["task"],
                "designed_sequence": row.get("sequence") or _structure_sequence(target_pdb, preferred_chain="A"),
                "iptm": _coerce_float(row.get("iptm")),
                "plddt": _coerce_float(row.get("plddt")),
                "ipae": _coerce_float(row.get("ipae")),
                "alanine_count": _coerce_float(row.get("alanine_count")),
                "run_id": row.get("run_id"),
                "cycle": row.get("cycle"),
                "source_structure": str(source_cif),
            }
            metadata_path = raw_meta_dir / f"generator_{design_id}.json"
            _dump_json(metadata_path, metadata)
            manifest.append({
                "design_id": design_id,
                "sequence": metadata["designed_sequence"],
                "structure_path": str(target_pdb),
                "metadata_path": str(metadata_path),
                "source_structure": str(source_cif),
            })
        return manifest

    fallback_cifs = sorted(run_root.glob("run_*/best.cif"))
    for index, source_cif in enumerate(fallback_cifs, start=1):
        design_id = f"{prefix}_protein_hunter_chai_{index:04d}"
        target_pdb = raw_pdb_dir / f"{design_id}.pdb"
        _copy_or_convert_structure(source_cif, target_pdb)
        metadata = {
            "design_id": design_id,
            "source": "protein_hunter",
            "source_model": "Protein Hunter (Chai)",
            "generator_family": "protein_hunter_experimental",
            "generator_backend": "chai",
            "generator_mode": request["task"],
            "designed_sequence": _structure_sequence(target_pdb, preferred_chain="A"),
            "source_structure": str(source_cif),
        }
        metadata_path = raw_meta_dir / f"generator_{design_id}.json"
        _dump_json(metadata_path, metadata)
        manifest.append({
            "design_id": design_id,
            "sequence": metadata["designed_sequence"],
            "structure_path": str(target_pdb),
            "metadata_path": str(metadata_path),
            "source_structure": str(source_cif),
        })
    return manifest


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _run_subprocess(cmd: list[str], cwd: Path, log_path: Path, env: dict[str, str]) -> None:
    with log_path.open("w", encoding="utf-8") as log_handle:
        subprocess.run(cmd, cwd=cwd, env=env, check=True, stdout=log_handle, stderr=subprocess.STDOUT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Protein Hunter generation and normalize outputs for BMS.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    request = _load_json(Path(args.request).resolve())
    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    work_dir = _ensure_workdir(output_dir)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/opt/Protein-Hunter:{env.get('PYTHONPATH', '')}".rstrip(":")

    backend = str(request.get("backend") or "boltz").strip().lower()
    job_name = _sanitize_name(str(request.get("job_name") or "protein_hunter"))

    if backend == "boltz":
        run_root = work_dir / "results_boltz" / job_name
        run_root.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "/opt/Protein-Hunter/boltz_ph/design.py",
            "--name", job_name,
            "--mode", "unconditional" if request.get("task") == "unconditional" else "binder",
            "--num_designs", str(int(request.get("num_designs") or 4)),
            "--num_cycles", str(int(request.get("num_cycles") or 7)),
            "--gpu_id", "0",
            "--min_protein_length", str(int(request.get("min_protein_length") or 90)),
            "--max_protein_length", str(int(request.get("max_protein_length") or 150)),
            "--percent_X", str(int(request.get("percent_x") or 50)),
            "--msa_mode", str(request.get("msa_mode") or "mmseqs"),
            "--temperature", str(float(request.get("temperature") or 0.1)),
            "--high_iptm_threshold", str(float(request.get("high_iptm_threshold") or 0.7)),
            "--high_plddt_threshold", str(float(request.get("high_plddt_threshold") or 0.8)),
            "--boltz_model_version", str(request.get("boltz_model_version") or "boltz2"),
            "--boltz_model_path", _resolve_container_weight_path(str(request.get("boltz_model_path") or "/weights/boltz/boltz2_conf.ckpt")),
            "--ccd_path", _resolve_container_weight_path(str(request.get("boltz_ccd_path") or "/weights/boltz/mols")),
            "--save_dir", str(run_root),
        ]
        if request.get("seed_binder_sequence"):
            cmd.extend(["--seq", str(request["seed_binder_sequence"])])
        if request.get("alanine_bias"):
            cmd.append("--alanine_bias")
        if request.get("cyclic"):
            cmd.append("--cyclic")
        if request.get("target_protein_sequences"):
            cmd.extend(["--protein_seqs", str(request["target_protein_sequences"])])
        if request.get("contact_residues"):
            cmd.extend(["--contact_residues", str(request["contact_residues"])])
        if request.get("ligand_smiles"):
            cmd.extend(["--ligand_smiles", str(request["ligand_smiles"])])
        if request.get("ligand_ccd"):
            cmd.extend(["--ligand_ccd", str(request["ligand_ccd"])])
        if request.get("nucleic_sequence"):
            cmd.extend(["--nucleic_seq", str(request["nucleic_sequence"])])
            cmd.extend(["--nucleic_type", str(request.get("nucleic_type") or "rna")])
        template_path = _resolve_input_path(input_dir, str(request.get("target_template_path") or ""))
        if template_path:
            cmd.extend(["--template_path", template_path])
        template_chain = str(request.get("target_template_chain_id") or "").strip()
        if template_chain:
            cmd.extend(["--template_cif_chain_id", template_chain])
        _run_subprocess(cmd, cwd=work_dir, log_path=output_dir / "protein_hunter.log", env=env)
        manifest = _normalize_boltz_outputs(run_root, output_dir, request)
    else:
        run_root = work_dir / "results_chai" / job_name
        run_root.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "/opt/Protein-Hunter/chai_ph/design.py",
            "--jobname", job_name,
            "--percent_X", str(int(request.get("percent_x") or 50)),
            "--min_protein_length", str(int(request.get("min_protein_length") or 90)),
            "--max_protein_length", str(int(request.get("max_protein_length") or 150)),
            "--n_trials", str(int(request.get("num_designs") or 4)),
            "--n_cycles", str(int(request.get("num_cycles") or 7)),
            "--n_recycles", str(int(request.get("chai_num_recycles") or 3)),
            "--n_diff_steps", str(int(request.get("chai_num_diff_steps") or 200)),
            "--hysteresis_mode", str(request.get("chai_hysteresis_mode") or "templates"),
            "--temperature", str(float(request.get("temperature") or 0.1)),
            "--high_iptm_threshold", str(float(request.get("high_iptm_threshold") or 0.7)),
            "--high_plddt_threshold", str(float(request.get("high_plddt_threshold") or 0.8)),
            "--gpu_id", "0",
        ]
        if request.get("seed_binder_sequence"):
            cmd.extend(["--seq", str(request["seed_binder_sequence"])])
        if request.get("alanine_bias"):
            cmd.append("--alanine_bias")
        if request.get("cyclic"):
            cmd.append("--cyclic")
        task = str(request.get("task") or "").strip().lower()
        if task == "protein_binder" and request.get("target_protein_sequences"):
            cmd.extend(["--target_seq", str(request["target_protein_sequences"])])
        elif task == "ligand_binder" and request.get("ligand_smiles"):
            cmd.extend(["--target_seq", str(request["ligand_smiles"])])
        target_pdb = _resolve_input_path(input_dir, str(request.get("target_pdb") or ""))
        target_pdb_chain = str(request.get("target_pdb_chain") or "").strip()
        if target_pdb:
            cmd.extend(["--target_pdb", target_pdb])
        if target_pdb_chain:
            cmd.extend(["--target_pdb_chain", target_pdb_chain])
        if request.get("chai_repredict", True):
            cmd.append("--repredict")
        _run_subprocess(cmd, cwd=work_dir, log_path=output_dir / "protein_hunter.log", env=env)
        manifest = _normalize_chai_outputs(run_root, output_dir, request)

    _dump_json(output_dir / "design_manifest.json", manifest)


if __name__ == "__main__":
    main()

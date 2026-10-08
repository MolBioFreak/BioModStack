#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",
}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _copy_optional_file(path_value: str, input_dir: Path) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    source = Path(raw).expanduser().resolve()
    if not source.exists():
        return raw
    target = input_dir / source.name
    if source != target:
        shutil.copy2(source, target)
    return target.name


def _normalize_text(value: str) -> str:
    return str(value or "").strip()


def _normalize_sequence(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip().upper() if ch.isalpha() or ch in {":"})


def _extract_pdb_sequence(path_value: str, requested_chain: str) -> tuple[str, str]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Protein Hunter target PDB does not exist: {path}")
    residues_by_chain: dict[str, list[tuple[tuple[str, str], str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("ATOM") or len(line) < 27:
            continue
        chain = line[21].strip() or "_"
        residue_key = (chain, line[22:26].strip(), line[26].strip())
        if residue_key in seen:
            continue
        seen.add(residue_key)
        residue_name = line[17:20].strip().upper()
        if residue_name not in _THREE_TO_ONE:
            raise ValueError(
                f"Protein Hunter cannot derive an authoritative sequence from nonstandard residue "
                f"{residue_name!r} at {chain}{residue_key[1]}{residue_key[2]}"
            )
        residues_by_chain.setdefault(chain, []).append(((residue_key[1], residue_key[2]), _THREE_TO_ONE[residue_name]))

    selected = requested_chain.strip()
    if selected:
        if selected not in residues_by_chain:
            raise ValueError(f"target_pdb_chain {selected!r} is absent; available chains: {sorted(residues_by_chain)}")
    elif len(residues_by_chain) == 1:
        selected = next(iter(residues_by_chain))
    else:
        raise ValueError(
            "Protein Hunter target_pdb_chain is required for a multi-chain target PDB; "
            f"available chains: {sorted(residues_by_chain)}"
        )
    sequence = "".join(amino_acid for _key, amino_acid in residues_by_chain.get(selected, []))
    if not sequence:
        raise ValueError(f"No protein residues found in target PDB chain {selected!r}")
    return sequence, selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile BMS Protein Hunter params into a normalized request.")
    parser.add_argument("--job-id", default="unknown")
    parser.add_argument("--job-name", default="protein_hunter_experimental")
    parser.add_argument("--backend", required=True, choices=["boltz", "chai"])
    parser.add_argument("--task", required=True, choices=["protein_binder", "unconditional", "ligand_binder", "nucleic_binder"])
    parser.add_argument("--num-designs", type=int, required=True)
    parser.add_argument("--num-cycles", type=int, required=True)
    parser.add_argument("--min-protein-length", type=int, required=True)
    parser.add_argument("--max-protein-length", type=int, required=True)
    parser.add_argument("--percent-x", type=int, default=50)
    parser.add_argument("--seed-binder-sequence", default="")
    parser.add_argument("--target-protein-sequences", default="")
    parser.add_argument("--target-pdb", default="")
    parser.add_argument("--target-pdb-chain", default="")
    parser.add_argument("--target-template-path", default="")
    parser.add_argument("--target-template-chain-id", default="")
    parser.add_argument("--ligand-smiles", default="")
    parser.add_argument("--ligand-ccd", default="")
    parser.add_argument("--nucleic-sequence", default="")
    parser.add_argument("--nucleic-type", default="rna", choices=["dna", "rna"])
    parser.add_argument("--contact-residues", default="")
    parser.add_argument("--cyclic", type=_parse_bool, default=False)
    parser.add_argument("--alanine-bias", type=_parse_bool, default=True)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--high-iptm-threshold", type=float, default=0.7)
    parser.add_argument("--high-plddt-threshold", type=float, default=0.8)
    parser.add_argument("--msa-mode", default="mmseqs", choices=["single", "mmseqs"])
    parser.add_argument("--boltz-model-version", default="boltz2", choices=["boltz1", "boltz2"])
    parser.add_argument("--boltz-model-path", default="")
    parser.add_argument("--boltz-ccd-path", default="")
    parser.add_argument("--chai-hysteresis-mode", default="templates", choices=["templates", "esm", "partial_diffusion", "none"])
    parser.add_argument("--chai-num-recycles", type=int, default=3)
    parser.add_argument("--chai-num-diff-steps", type=int, default=200)
    parser.add_argument("--chai-repredict", type=_parse_bool, default=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dir", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)

    copied_target_pdb = _copy_optional_file(args.target_pdb, input_dir)
    copied_template_path = _copy_optional_file(args.target_template_path, input_dir)

    task = _normalize_text(args.task)
    backend = _normalize_text(args.backend)
    target_protein_sequences = _normalize_sequence(args.target_protein_sequences)
    target_pdb_chain = _normalize_text(args.target_pdb_chain)
    if not target_protein_sequences and _normalize_text(args.target_pdb):
        target_protein_sequences, target_pdb_chain = _extract_pdb_sequence(args.target_pdb, target_pdb_chain)
    ligand_smiles = _normalize_text(args.ligand_smiles)
    ligand_ccd = _normalize_text(args.ligand_ccd)
    nucleic_sequence = _normalize_sequence(args.nucleic_sequence).replace(":", "")

    if task == "protein_binder":
        if backend == "boltz" and not target_protein_sequences:
            raise ValueError("Protein Hunter Boltz protein_binder requires target_protein_sequences")
        if backend == "chai" and not (target_protein_sequences or (copied_target_pdb and _normalize_text(args.target_pdb_chain))):
            raise ValueError("Protein Hunter Chai protein_binder requires target_protein_sequences or target_pdb + target_pdb_chain")
        if backend == "chai" and ":" in target_protein_sequences:
            raise ValueError("Protein Hunter Chai currently supports a single protein target sequence in BMS")

    if task == "ligand_binder":
        if not (ligand_smiles or ligand_ccd):
            raise ValueError("Protein Hunter ligand_binder requires ligand_smiles or ligand_ccd")
        if backend == "chai" and not ligand_smiles:
            raise ValueError("Protein Hunter Chai ligand_binder currently requires ligand_smiles")

    if task == "nucleic_binder":
        if not nucleic_sequence:
            raise ValueError("Protein Hunter nucleic_binder requires nucleic_sequence")
        if backend == "chai":
            raise ValueError("Protein Hunter Chai nucleic_binder is not exposed in the first BMS cut")

    request = {
        "job_id": _normalize_text(args.job_id),
        "job_name": _normalize_text(args.job_name),
        "backend": backend,
        "task": task,
        "num_designs": int(args.num_designs),
        "num_cycles": int(args.num_cycles),
        "min_protein_length": int(args.min_protein_length),
        "max_protein_length": int(args.max_protein_length),
        "percent_x": int(args.percent_x),
        "seed_binder_sequence": _normalize_sequence(args.seed_binder_sequence).replace(":", ""),
        "target_protein_sequences": target_protein_sequences,
        "target_pdb": copied_target_pdb,
        "target_pdb_chain": target_pdb_chain,
        "target_template_path": copied_template_path or _normalize_text(args.target_template_path),
        "target_template_chain_id": _normalize_text(args.target_template_chain_id),
        "ligand_smiles": ligand_smiles,
        "ligand_ccd": ligand_ccd,
        "nucleic_sequence": nucleic_sequence,
        "nucleic_type": _normalize_text(args.nucleic_type) or "rna",
        "contact_residues": _normalize_text(args.contact_residues),
        "cyclic": bool(args.cyclic),
        "alanine_bias": bool(args.alanine_bias),
        "temperature": float(args.temperature),
        "high_iptm_threshold": float(args.high_iptm_threshold),
        "high_plddt_threshold": float(args.high_plddt_threshold),
        "msa_mode": _normalize_text(args.msa_mode) or "mmseqs",
        "boltz_model_version": _normalize_text(args.boltz_model_version) or "boltz2",
        "boltz_model_path": _normalize_text(args.boltz_model_path),
        "boltz_ccd_path": _normalize_text(args.boltz_ccd_path),
        "chai_hysteresis_mode": _normalize_text(args.chai_hysteresis_mode) or "templates",
        "chai_num_recycles": int(args.chai_num_recycles),
        "chai_num_diff_steps": int(args.chai_num_diff_steps),
        "chai_repredict": bool(args.chai_repredict),
    }

    Path(args.output).resolve().write_text(json.dumps(request, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

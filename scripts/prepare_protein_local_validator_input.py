#!/usr/bin/env python3
"""Build deterministic Protein Local Redesign validator inputs from one PDB."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import OrderedDict
from pathlib import Path


AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M", "SEC": "U", "PYL": "O", "HYP": "P",
}


def candidate_id(path: Path) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")
    return normalized or "candidate"


def extract_protein_components(path: Path) -> list[dict[str, str]]:
    chains: OrderedDict[str, OrderedDict[tuple[str, str], str]] = OrderedDict()
    saw_model = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        record = line[:6].strip()
        if record == "MODEL":
            if saw_model:
                break
            saw_model = True
            continue
        if record == "ENDMDL":
            break
        if record != "ATOM":
            continue
        residue = line[17:20].strip().upper()
        if residue not in AA3_TO_1:
            raise ValueError(f"unsupported protein residue {residue!r} in {path}")
        chain = line[21:22].strip() or "A"
        residue_key = (line[22:26].strip(), line[26:27].strip())
        chains.setdefault(chain, OrderedDict()).setdefault(residue_key, AA3_TO_1[residue])

    components = [
        {
            "chain_id": chain,
            "molecule_type": "protein",
            "sequence": "".join(residues.values()),
        }
        for chain, residues in chains.items()
        if residues
    ]
    if not components:
        raise ValueError(f"PDB contains no supported protein residues: {path}")
    return components


def build_contract(
    path: Path, model_seeds: list[int], requested_candidate_id: str | None = None
) -> dict[str, object]:
    resolved = path.resolve()
    components = extract_protein_components(resolved)
    name = candidate_id(Path(requested_candidate_id)) if requested_candidate_id else candidate_id(resolved)
    protenix_sequences = [
        {"proteinChain": {"sequence": component["sequence"], "count": 1}}
        for component in components
    ]
    return {
        "schema_version": 1,
        "candidate_id": name,
        "source_file": resolved.name,
        "source_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "components": components,
        "protenix_input": [{
            "name": name,
            "modelSeeds": model_seeds,
            "sequences": protenix_sequences,
        }],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-pdb", required=True, type=Path)
    parser.add_argument("--candidate-id")
    parser.add_argument("--contract-out", required=True, type=Path)
    parser.add_argument("--protenix-out", required=True, type=Path)
    parser.add_argument("--model-seeds", default="42")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seeds = [int(value.strip()) for value in args.model_seeds.split(",") if value.strip()]
    if not seeds:
        raise ValueError("at least one Protenix model seed is required")
    contract = build_contract(args.input_pdb, seeds, args.candidate_id)
    args.contract_out.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    args.protenix_out.write_text(json.dumps(contract["protenix_input"], indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

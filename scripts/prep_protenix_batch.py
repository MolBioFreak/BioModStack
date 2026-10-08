#!/usr/bin/env python3
"""
Prepare a multi-entry Protenix input JSON from antibody design PDB files.

Each input PDB becomes one Protenix job entry with one proteinChain entity per
observed chain, preserving chain order from the source structure.
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


AA_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}


def extract_chain_sequences(pdb_path: Path) -> list[tuple[str, str]]:
    chain_sequences: "OrderedDict[str, list[str]]" = OrderedDict()
    seen_residues: dict[str, set[tuple[int, str]]] = {}

    with pdb_path.open() as handle:
        for line in handle:
            if not line.startswith("ATOM") or len(line) < 27:
                continue
            if line[12:16].strip() != "CA":
                continue

            res_name = line[17:20].strip()
            chain_id = line[21].strip() or "_"
            try:
                res_num = int(line[22:26].strip())
            except ValueError:
                continue
            insertion_code = line[26].strip()

            aa = AA_CODES.get(res_name)
            if aa is None:
                continue

            if chain_id not in chain_sequences:
                chain_sequences[chain_id] = []
                seen_residues[chain_id] = set()

            residue_key = (res_num, insertion_code)
            if residue_key in seen_residues[chain_id]:
                continue

            seen_residues[chain_id].add(residue_key)
            chain_sequences[chain_id].append(aa)

    return [(chain_id, "".join(seq)) for chain_id, seq in chain_sequences.items() if seq]


def build_entry(pdb_path: Path, seeds: list[int]) -> dict:
    chain_sequences = extract_chain_sequences(pdb_path)
    if not chain_sequences:
        raise ValueError(f"No amino-acid chain sequences found in {pdb_path}")

    return {
        "name": pdb_path.stem,
        "modelSeeds": seeds,
        "sequences": [
            {
                "proteinChain": {
                    "id": [chain_id],
                    "sequence": sequence,
                    "count": 1,
                }
            }
            for chain_id, sequence in chain_sequences
        ],
    }


def parse_seeds(raw: str) -> list[int]:
    seeds: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        seeds.append(int(token))
    return seeds or [42]


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Protenix batch input JSON from PDB files")
    parser.add_argument("--pdb_files", nargs="+", required=True, help="Input antibody design PDB files")
    parser.add_argument("--out_json", required=True, help="Output Protenix JSON path")
    parser.add_argument("--seeds", default="42", help="Comma-separated model seeds")
    args = parser.parse_args()

    pdb_files = [Path(path).expanduser().resolve() for path in args.pdb_files]
    seeds = parse_seeds(args.seeds)

    entries = [build_entry(pdb_path, seeds) for pdb_path in pdb_files]

    out_path = Path(args.out_json).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as handle:
        json.dump(entries, handle, indent=2)

    print(f"[prep_protenix_batch] Wrote {len(entries)} entries to {out_path}")


if __name__ == "__main__":
    main()

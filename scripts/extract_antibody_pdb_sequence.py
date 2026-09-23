#!/usr/bin/env python3
"""Extract observed CA sequence from one PDB model, retaining chain/residue identity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

AA = dict(zip(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(),
    "ARNDCQEGHILKMFPSTWYV",
))


def extract(path: Path) -> dict:
    chains: dict[str, dict[tuple[int, str], str]] = {}
    model_seen = False
    ended = False
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read PDB {path}: {exc}") from exc
    for line in lines:
        if line.startswith("MODEL "):
            if model_seen:
                break
            model_seen = True
            continue
        if line.startswith("ENDMDL"):
            ended = True
            break
        if ended or not line.startswith("ATOM  "):
            continue
        if len(line) < 27:
            raise ValueError(f"Truncated ATOM record in {path}")
        if line[12:16].strip() != "CA" or line[16] not in (" ", "A"):
            continue
        chain = line[21]
        name = line[17:20].strip()
        number = line[22:26].strip()
        icode = line[26].strip()
        if not chain.strip() or not number or name not in AA:
            raise ValueError(f"Invalid CA residue identity {chain!r}:{number}{icode} {name!r} in {path}")
        try:
            index = int(number)
        except ValueError as exc:
            raise ValueError(f"Invalid residue number {number!r} in {path}") from exc
        residues = chains.setdefault(chain, {})
        key = (index, icode)
        aa = AA[name]
        if key in residues and residues[key] != aa:
            raise ValueError(f"Conflicting CA residue {chain}:{index}{icode} in {path}")
        residues[key] = aa
    if not chains:
        raise ValueError(f"No valid CA residues in {path}")
    return {"chains": [
        {"chain": chain, "sequence": "".join(residues.values()),
         "residues": [{"number": number, "insertion_code": icode, "aa": aa}
                      for (number, icode), aa in residues.items()]}
        for chain, residues in chains.items()
    ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdb", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.pdb)))


if __name__ == "__main__":
    main()

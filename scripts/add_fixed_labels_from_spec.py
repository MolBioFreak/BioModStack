#!/usr/bin/env python3
"""Mark fixed/designable residues for ProteinMPNN from a redesign manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Set, Tuple


ResidueToken = Tuple[str, int, str]


def parse_position_spec(spec: str) -> Set[ResidueToken]:
    result: Set[ResidueToken] = set()
    tokens = [token.strip() for token in str(spec or "").split(",") if token.strip()]
    pattern = re.compile(r"(?P<chain>[A-Za-z0-9])(?P<start>-?\d+)(?P<icode_start>[A-Za-z]?)(?:-(?P<end>-?\d+)(?P<icode_end>[A-Za-z]?))?")
    for token in tokens:
        match = pattern.fullmatch(token)
        if not match:
            continue
        chain = match.group("chain")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        icode_start = (match.group("icode_start") or "").strip()
        icode_end = (match.group("icode_end") or icode_start).strip()
        if start == end and icode_start == icode_end:
            result.add((chain, start, icode_start))
            continue
        lower = min(start, end)
        upper = max(start, end)
        for resnum in range(lower, upper + 1):
            result.add((chain, resnum, ""))
    return result


def residue_token_from_line(line: str) -> ResidueToken | None:
    if not line.startswith(("ATOM", "HETATM")):
        return None
    chain = line[21].strip()
    raw_resnum = line[22:26].strip()
    if not chain or not raw_resnum or not raw_resnum.lstrip("-").isdigit():
        return None
    return chain, int(raw_resnum), line[26].strip()


def add_labels(input_pdb: Path, output_pdb: Path, designable_tokens: Set[ResidueToken]) -> None:
    output_lines = []
    with input_pdb.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith(("ATOM", "HETATM")):
                token = residue_token_from_line(raw)
                designable = token in designable_tokens or (token and (token[0], token[1], "") in designable_tokens)
                new_bfactor = "  1.00" if designable else "  0.00"
                if len(raw) >= 66:
                    raw = raw[:60] + new_bfactor + raw[66:]
                else:
                    raw = raw.rstrip("\n").ljust(60) + new_bfactor + "\n"
            output_lines.append(raw)
    output_pdb.write_text("".join(output_lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply ProteinMPNN fixed labels from protein-local-redesign manifest")
    parser.add_argument("--input_dir", required=True, help="Input directory containing PDB files")
    parser.add_argument("--output_dir", required=True, help="Output directory for labeled PDB files")
    parser.add_argument("--manifest", required=True, help="Region manifest JSON")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    designable_tokens = parse_position_spec(manifest.get("movable_positions_spec", ""))

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdb_files = sorted(input_dir.glob("*.pdb"))
    for pdb_path in pdb_files:
        add_labels(pdb_path, output_dir / pdb_path.name, designable_tokens)
        print(f"Labeled {pdb_path.name}")


if __name__ == "__main__":
    main()

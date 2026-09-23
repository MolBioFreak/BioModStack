#!/usr/bin/env python3
"""Fail-closed PPIFlow chain-role and hotspot admission before native sampling."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def chain_ids(path: Path) -> set[str]:
    chains = set()
    for line in path.read_text().splitlines():
        if line.startswith("ATOM  "):
            if len(line) < 27 or not line[21].strip():
                raise ValueError("Malformed or blank-chain ATOM record")
            chains.add(line[21])
    if not chains:
        raise ValueError("No ATOM chains in PDB")
    return chains


def roles(path: Path, heavy: str, light: str, antigen: str, hotspots: str = "") -> str:
    present = chain_ids(path)
    for role, chain in (("heavy", heavy), ("light", light)):
        if role == "heavy" and not chain:
            raise ValueError("Missing heavy-chain role")
        if chain and (len(chain) != 1 or chain not in present):
            raise ValueError(f"Requested {role} chain {chain!r} absent from structure {sorted(present)}")
    if light and light == heavy:
        raise ValueError("Heavy and light chains overlap")
    binder = {heavy, light} - {""}
    if antigen:
        if len(antigen) != 1 or antigen not in present:
            raise ValueError(f"Requested antigen chain {antigen!r} absent or unsupported by single-chain native sampler")
        if antigen in binder:
            raise ValueError("Antigen and antibody chain roles overlap")
    else:
        candidates = present - binder
        if len(candidates) != 1:
            raise ValueError(f"Antigen chain role ambiguous: {sorted(candidates)}; specify one exact chain")
        antigen = candidates.pop()
    for token in filter(None, (item.strip() for item in hotspots.split(","))):
        match = re.fullmatch(r"([A-Za-z0-9])(-?\d+)([A-Za-z]?)", token)
        if not match or match.group(1) != antigen:
            raise ValueError(f"Hotspot {token!r} does not identify the selected antigen chain {antigen!r}")
        number, icode = int(match.group(2)), match.group(3)
        if not any(line.startswith("ATOM  ") and len(line) >= 27 and line[21] == antigen
                   and line[22:26].strip() == str(number) and line[26].strip() == icode
                   for line in path.read_text().splitlines()):
            raise ValueError(f"Hotspot {token!r} absent from selected antigen structure")
    return antigen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdb", type=Path, required=True)
    parser.add_argument("--heavy", required=True)
    parser.add_argument("--light", default="")
    parser.add_argument("--antigen", default="")
    parser.add_argument("--hotspots", default="")
    args = parser.parse_args()
    print(roles(args.pdb, args.heavy, args.light, args.antigen, args.hotspots))


if __name__ == "__main__":
    main()

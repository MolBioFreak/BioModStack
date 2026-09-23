#!/usr/bin/env python3
"""Caliby positional masks for an explicitly selected binder/target complex.

This does not infer an interface or silently select a chain. The optional
explicit design positions allow the caller to supply a qualified region mask.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from prep_antibody_constraints import get_ranges, parse_pdb_chains


def _chains(value: str) -> list[str]:
    chains = [item.strip() for item in value.split(",")]
    if not chains or any(len(item) != 1 or not item.isalnum() for item in chains) or len(set(chains)) != len(chains):
        raise ValueError("chain IDs must be distinct single-character PDB chain IDs")
    return chains


def build_constraints(input_dir: Path, binder_chains: str, target_chains: str, design_positions: str = "") -> list[dict[str, str]]:
    binders = _chains(binder_chains)
    targets = _chains(target_chains)
    if set(binders) & set(targets):
        raise ValueError("binder and target chains overlap")
    pdbs = sorted(input_dir.glob("*.pdb"))
    if not pdbs:
        raise ValueError("no PDB candidates found for Caliby")
    # Explicit positions only; no invented geometric interface cutoff.
    selected: dict[str, set[int]] = {}
    if design_positions:
        from prep_antibody_constraints import parse_chain_position_spec
        if any(not re.fullmatch(r"[A-Za-z][0-9]+(?:-[0-9]+)?", token.strip())
               for token in design_positions.split(",")):
            raise ValueError("invalid binder design position")
        selected = {key: set(value) for key, value in parse_chain_position_spec(design_positions).items()}
        if not selected or not set(selected) <= set(binders):
            raise ValueError("design positions must belong to selected binder chains")
    rows = []
    for pdb in pdbs:
        observed = parse_pdb_chains(pdb)
        if not set(binders + targets) <= set(observed):
            raise ValueError(f"{pdb.name}: selected binder or target chain is absent")
        if selected and any(not positions <= set(observed[chain]) for chain, positions in selected.items()):
            raise ValueError(f"{pdb.name}: design positions are not present in the binder")
        if selected and not any(selected.values()):
            raise ValueError("no binder positions selected for design")
        fixed = []
        for chain, positions in sorted(observed.items()):
            residues = set(positions)
            locked = residues - selected.get(chain, set()) if chain in binders and selected else (
                set() if chain in binders else residues
            )
            for start, end in get_ranges(locked):
                fixed.append(f"{chain}{start}-{end}")
        rows.append({
            "pdb_key": pdb.stem,
            "fixed_pos_seq": ",".join(fixed),
            "fixed_pos_scn": ",".join(fixed),
            "fixed_pos_override_seq": "",
            "pos_restrict_aatype": "",
            "symmetry_pos": "",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--binder-chains", required=True)
    parser.add_argument("--target-chains", required=True)
    parser.add_argument("--design-positions", default="")
    args = parser.parse_args()
    rows = build_constraints(Path(args.input_dir), args.binder_chains, args.target_chains, args.design_positions)
    with Path(args.out_csv).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Extract target-chain structural templates from design PDBs.

Each input design PDB becomes one target-only mmCIF template that preserves the
original chain IDs and residue numbering. The output manifest can be consumed by
validator prep scripts to anchor only the experimental target chains while
leaving binder chains flexible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from Bio.PDB import MMCIFIO, PDBParser, Select


class _ChainSelect(Select):
    def __init__(self, allowed_chains: set[str]):
        self.allowed_chains = allowed_chains

    def accept_chain(self, chain) -> int:
        return 1 if chain.id in self.allowed_chains else 0


def parse_chain_csv(raw: str) -> list[str]:
    chains = [token.strip() for token in (raw or "").split(",") if token.strip()]
    if not chains:
        raise ValueError("At least one target chain ID is required")
    return chains


def extract_templates(pdb_paths: list[Path], target_chains: list[str], out_dir: Path) -> dict[str, dict[str, object]]:
    parser = PDBParser(QUIET=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    requested = set(target_chains)

    for pdb_path in pdb_paths:
        structure = parser.get_structure(pdb_path.stem, str(pdb_path))
        present = []
        for model in structure:
            for chain in model:
                if chain.id in requested and chain.id not in present:
                    present.append(chain.id)

        if not present:
            raise ValueError(
                f"No requested target chains {sorted(requested)} were found in {pdb_path}"
            )

        out_path = out_dir / f"{pdb_path.stem}_target_template.cif"
        io = MMCIFIO()
        io.set_structure(structure)
        io.save(str(out_path), select=_ChainSelect(set(present)))
        manifest[pdb_path.stem] = {
            "cif": str(out_path.resolve()),
            "chains": present,
        }

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract target-only mmCIF templates from design PDBs")
    parser.add_argument("--pdb_files", nargs="+", required=True, help="Input design PDB files")
    parser.add_argument("--target_chains", required=True, help="Comma-separated target chain IDs")
    parser.add_argument("--out_dir", required=True, help="Directory for extracted target templates")
    parser.add_argument("--manifest", required=True, help="Output JSON manifest path")
    args = parser.parse_args()

    pdb_paths = [Path(path).expanduser() for path in args.pdb_files]
    target_chains = parse_chain_csv(args.target_chains)
    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()

    manifest = extract_templates(pdb_paths, target_chains, out_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        f"[extract_target_templates] Wrote {len(manifest)} target template(s) to {out_dir}"
    )


if __name__ == "__main__":
    main()

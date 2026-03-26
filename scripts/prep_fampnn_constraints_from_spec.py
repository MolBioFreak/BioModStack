#!/usr/bin/env python3
"""Generate FA-MPNN constraint CSVs from a protein-local-redesign manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FA-MPNN fixed-position CSV from a redesign manifest")
    parser.add_argument("--input_dir", required=True, help="Directory containing prepared PDB files")
    parser.add_argument("--manifest", required=True, help="Region manifest JSON")
    parser.add_argument("--out_csv", required=True, help="Output CSV path")
    parser.add_argument("--fix_sidechains", action="store_true", help="Fix sidechains for all fixed positions")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    fixed_spec = str(manifest.get("fixed_positions_spec", "") or "")
    fixed_sidechains = fixed_spec if args.fix_sidechains else ""

    pdb_files = sorted(input_dir.glob("*.pdb"))
    with Path(args.out_csv).open("w", encoding="utf-8") as handle:
        handle.write("pdb,fixed_seq_positions,fixed_sidechains\n")
        for pdb_path in pdb_files:
            handle.write(f'"{pdb_path.stem}","{fixed_spec}","{fixed_sidechains}"\n')

    print(f"Wrote constraints for {len(pdb_files)} PDBs to {args.out_csv}")


if __name__ == "__main__":
    main()

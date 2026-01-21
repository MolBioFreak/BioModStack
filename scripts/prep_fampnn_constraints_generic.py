#!/usr/bin/env python3
"""
Generate an empty FAMPNN constraints CSV (no fixed positions).
Used when running FAMPNN in generic mode (non-antibody workflows).
"""
import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate generic FAMPNN constraint CSV")
    parser.add_argument("--input_dir", required=True, help="Directory containing PDB files")
    parser.add_argument("--out_csv", required=True, help="Output CSV path")
    args = parser.parse_args()

    pdb_dir = Path(args.input_dir)
    pdb_files = sorted(pdb_dir.glob("*.pdb"))

    with open(args.out_csv, "w", encoding="utf-8") as csv_out:
        csv_out.write("pdb,fixed_seq_positions,fixed_sidechains\n")
        for pdb_file in pdb_files:
            pdb_name = pdb_file.stem
            csv_out.write(f"\"{pdb_name}\",\"\",\"\"\n")

    print(f"Wrote generic FAMPNN constraints to {args.out_csv} for {len(pdb_files)} PDBs")


if __name__ == "__main__":
    main()

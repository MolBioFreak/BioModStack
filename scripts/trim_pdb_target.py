#!/usr/bin/env python3
"""
Trim a PDB target to a residue range around hotspot residues.

Usage:
    python trim_pdb_target.py INPUT.pdb OUTPUT.pdb --start 150 --end 350 [--chain A]

This keeps ATOM/HETATM records in the specified residue range for the given
chain, plus any TER/END records. Residue numbering is preserved (not renumbered).

Used for diagnostic resubmission to test whether target size correlates with
RFdiffusion degenerate rotation matrix crashes (RFantibody #84).
"""
import argparse
import sys
from pathlib import Path


def trim_pdb(input_path: str, output_path: str, start: int, end: int, chain: str = "A"):
    kept = 0
    skipped = 0
    with open(input_path, "r") as fin, open(output_path, "w") as fout:
        for line in fin:
            record = line[:6].strip()
            if record in ("ATOM", "HETATM"):
                pdb_chain = line[21]
                try:
                    resnum = int(line[22:26].strip())
                except ValueError:
                    # insertion codes or weird numbering — keep if chain matches
                    if pdb_chain == chain:
                        fout.write(line)
                        kept += 1
                    continue

                if pdb_chain == chain and start <= resnum <= end:
                    fout.write(line)
                    kept += 1
                else:
                    skipped += 1
            elif record in ("TER", "END"):
                fout.write(line)
            elif record in ("HEADER", "TITLE", "REMARK", "CRYST1"):
                fout.write(line)

    # Summary
    total_residues = end - start + 1
    print(f"Trimmed {input_path} -> {output_path}")
    print(f"  Chain: {chain}, Residues: {start}-{end} ({total_residues} residues)")
    print(f"  Kept {kept} atom lines, skipped {skipped}")


def main():
    parser = argparse.ArgumentParser(description="Trim PDB to a residue range")
    parser.add_argument("input", help="Input PDB file")
    parser.add_argument("output", help="Output PDB file")
    parser.add_argument("--start", type=int, required=True, help="Start residue number (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="End residue number (inclusive)")
    parser.add_argument("--chain", default="A", help="Chain to keep (default: A)")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    trim_pdb(args.input, args.output, args.start, args.end, args.chain)


if __name__ == "__main__":
    main()

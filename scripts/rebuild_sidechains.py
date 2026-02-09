"""
Rebuild missing nucleobase sidechains on NA-MPNN backbone-only PDBs.

NA-MPNN outputs backbone-only PDBs (12 atoms/residue: P, OP1, OP2, O5', C5',
C4', O4', C3', O3', C2', O2', C1'). PyRosetta's pose_from_pdb() automatically
restores missing sidechain atoms from its internal rotamer libraries.

This completes the paper's pipeline: RFDpoly → NA-MPNN → PyRosetta rebuild.
See: Favor et al. 2025, "De novo design of RNA and nucleoprotein complexes"

Usage:
    python rebuild_sidechains.py --input_dir ./nampnn_backbones --out_dir ./rebuilt
"""

import pyrosetta
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Restore nucleobase sidechains to NA-MPNN backbone-only PDBs"
    )
    parser.add_argument("--input_dir", required=True, help="Dir with backbone-only PDBs")
    parser.add_argument("--out_dir", default="./rebuilt", help="Output dir for full-atom PDBs")
    parser.add_argument("--out_prefix", default="", help="Prefix for output filenames (avoids collision with inputs)")
    parser.add_argument("--metrics_out", default=None, help="Optional JSON metrics output path")
    args = parser.parse_args()

    pyrosetta.init("-out:levels all:error")

    input_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdb_files = sorted(input_dir.glob("*.pdb"))
    results = []

    for pdb_file in pdb_files:
        # Skip our own output files if writing to same directory
        if args.out_prefix and pdb_file.name.startswith(args.out_prefix):
            continue
        try:
            pose = pyrosetta.pose_from_pdb(str(pdb_file))
            output_path = out_dir / f"{args.out_prefix}{pdb_file.name}"
            pose.dump_pdb(str(output_path))

            n_residues = pose.total_residue()
            # Count atoms in the output file
            atom_count = sum(1 for line in open(output_path) if line.startswith("ATOM"))

            print(f"Rebuilt: {pdb_file.name} → {n_residues} residues, {atom_count} atoms")
            results.append({
                "name": pdb_file.name,
                "residues": n_residues,
                "atoms": atom_count,
                "atoms_per_residue": round(atom_count / n_residues, 1) if n_residues > 0 else 0,
                "status": "success",
            })
        except Exception as e:
            print(f"ERROR rebuilding {pdb_file.name}: {e}")
            results.append({
                "name": pdb_file.name,
                "status": "error",
                "error": str(e),
            })

    if args.metrics_out:
        with open(args.metrics_out, "w") as f:
            json.dump({"rebuilt_designs": results, "total": len(results)}, f, indent=2)

    print(f"\nRebuilt {sum(1 for r in results if r['status'] == 'success')}/{len(results)} PDBs")


if __name__ == "__main__":
    main()

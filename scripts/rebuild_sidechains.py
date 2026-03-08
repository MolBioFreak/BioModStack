"""
Rebuild missing nucleobase sidechains on NA-MPNN backbone-only PDBs.

NA-MPNN outputs backbone-only PDBs (12 atoms/residue: P, OP1, OP2, O5', C5',
C4', O4', C3', O3', C2', O2', C1'). PyRosetta's pose_from_pdb() automatically
restores missing sidechain atoms from its internal rotamer libraries,
then a quick repack optimizes placement.

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
    parser.add_argument("--nampnn_fasta_dir", default=None, help="Dir with NA-MPNN .fa files for confidence metrics")
    args = parser.parse_args()

    pyrosetta.init("-out:levels all:error")

    input_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdb_files = sorted(input_dir.glob("*.pdb"))
    results = []

    # Parse NA-MPNN FASTA headers for quality metrics if available
    nampnn_metrics = {}
    if args.nampnn_fasta_dir:
        nampnn_fasta_dir = Path(args.nampnn_fasta_dir)
        for fa_file in nampnn_fasta_dir.glob("*.fa"):
            _parse_nampnn_fasta(fa_file, nampnn_metrics)

    # Set up repack score function and task factory
    sfxn = pyrosetta.create_score_function("ref2015")
    from pyrosetta.rosetta.core.pack.task import TaskFactory
    from pyrosetta.rosetta.core.pack.task.operation import RestrictToRepacking
    tf = TaskFactory()
    tf.push_back(RestrictToRepacking())

    for pdb_file in pdb_files:
        # Skip our own output files if writing to same directory
        if args.out_prefix and pdb_file.name.startswith(args.out_prefix):
            continue
        try:
            pose = pyrosetta.pose_from_pdb(str(pdb_file))

            # Quick repack for optimal sidechain placement
            # Per paper: PyRosetta threading + "quick repacking"
            packer = pyrosetta.rosetta.protocols.minimization_packing.PackRotamersMover(sfxn)
            packer.task_factory(tf)
            packer.apply(pose)

            # Restore NA-MPNN confidence B-factors if available
            # NA-MPNN writes exp(-loss_per_residue) into B-factors of its output PDBs.
            # PyRosetta's pose_from_pdb() + dump_pdb() resets them to 0.00.
            # We restore from the original backbone PDB.
            _restore_bfactors_from_source(pdb_file, pose)

            output_path = out_dir / f"{args.out_prefix}{pdb_file.name}"
            pose.dump_pdb(str(output_path))

            n_residues = pose.total_residue()
            atom_count = sum(1 for line in open(output_path) if line.startswith("ATOM"))

            # Look up NA-MPNN metrics for this design
            design_key = pdb_file.stem
            design_metrics = nampnn_metrics.get(design_key, {})

            print(f"Rebuilt: {pdb_file.name} → {n_residues} residues, {atom_count} atoms")
            result = {
                "name": pdb_file.name,
                "residues": n_residues,
                "atoms": atom_count,
                "atoms_per_residue": round(atom_count / n_residues, 1) if n_residues > 0 else 0,
                "status": "success",
            }
            if design_metrics:
                result.update(design_metrics)
            results.append(result)
        except Exception as e:
            print(f"ERROR rebuilding {pdb_file.name}: {e}")
            results.append({
                "name": pdb_file.name,
                "status": "error",
                "error": str(e),
            })

    if args.metrics_out:
        with open(args.metrics_out, "w") as f:
            json.dump({
                "rebuilt_designs": results,
                "total": len(results),
                "nampnn_metrics": nampnn_metrics,
            }, f, indent=2)

    print(f"\nRebuilt {sum(1 for r in results if r['status'] == 'success')}/{len(results)} PDBs")


def _restore_bfactors_from_source(source_pdb: Path, pose):
    """
    Restore B-factor values from NA-MPNN backbone PDB into the rebuilt pose.
    NA-MPNN stores exp(-loss_per_residue) in B-factors — these are design confidence scores.
    """
    try:
        bfactors = {}
        with open(source_pdb) as f:
            for line in f:
                if line.startswith("ATOM"):
                    chain = line[21]
                    resnum = line[22:26].strip()
                    bfactor = float(line[60:66].strip())
                    key = f"{chain}{resnum}"
                    if key not in bfactors:
                        bfactors[key] = bfactor

        pdb_info = pose.pdb_info()
        for i in range(1, pose.total_residue() + 1):
            chain = pdb_info.chain(i)
            resnum = str(pdb_info.number(i))
            key = f"{chain}{resnum}"
            bfactor = bfactors.get(key, 0.0)
            for j in range(1, pose.residue(i).natoms() + 1):
                pdb_info.bfactor(i, j, bfactor)
    except Exception as e:
        print(f"Warning: could not restore B-factors: {e}")


def _parse_nampnn_fasta(fa_path: Path, metrics_dict: dict):
    """
    Parse NA-MPNN FASTA headers for quality metrics.
    Header format: >{name}, id={id}, T={temp}, seed={seed}, overall_confidence={conf} seq_rec={rec}
    """
    try:
        with open(fa_path) as f:
            for line in f:
                if line.startswith(">"):
                    header = line.strip().lstrip(">")
                    parts = header.split(",")
                    name = parts[0].strip()

                    metrics = {}
                    for part in parts[1:]:
                        part = part.strip()
                        # Handle space-separated key=value pairs (e.g. "overall_confidence=0.33 seq_rec=0.50")
                        for kv in part.split():
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                k = k.strip()
                                try:
                                    metrics[k] = float(v.strip())
                                except ValueError:
                                    metrics[k] = v.strip()

                    if metrics:
                        # Key by design name (e.g., "design_0_1" from FASTA id field)
                        design_id = metrics.get("id", name)
                        # The backbone PDB is named {name}_{id}.pdb
                        pdb_key = f"{name}_{int(design_id)}" if isinstance(design_id, float) else f"{name}_{design_id}"
                        metrics_dict[pdb_key] = metrics
    except Exception as e:
        print(f"Warning: could not parse FASTA metrics from {fa_path}: {e}")


if __name__ == "__main__":
    main()

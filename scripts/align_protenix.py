#!/usr/bin/env python3
"""
Align Protenix structure predictions to the source design structures.

Consumes Protenix confidence JSON + CIF outputs, writes aligned PDB files and
augmented confidence JSONs with RMSD fields preserved for downstream ingestion.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Iterable

from Bio.PDB import MMCIFParser, PDBIO, PDBParser, Superimposer


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(__name__)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("alignment_protenix.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logger


logger = setup_logging()


def get_chain_ids(structure) -> list[str]:
    chain_ids = []
    seen = set()
    for model in structure:
        for chain in model:
            if chain.id in seen:
                continue
            seen.add(chain.id)
            chain_ids.append(chain.id)
    return chain_ids


def remap_mobile_chain_ids_by_order(ref_structure, mobile_structure) -> bool:
    ref_ids = get_chain_ids(ref_structure)
    mobile_ids = get_chain_ids(mobile_structure)
    if len(ref_ids) != len(mobile_ids):
        return False

    for model in mobile_structure:
        for chain in model:
            try:
                idx = mobile_ids.index(chain.id)
            except ValueError:
                return False
            chain.id = ref_ids[idx]
    return True


def get_ca_atoms_by_key(structure, chains: Iterable[str] | None = None):
    allowed = set(chains) if chains is not None else None
    ca_atoms = {}
    for model in structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            for residue in chain:
                if "CA" not in residue:
                    continue
                _, resseq, icode = residue.id
                key = (chain.id, int(resseq), str(icode).strip())
                ca_atoms[key] = residue["CA"]
    return ca_atoms


def get_ca_atoms_by_chain_order(structure, chains: Iterable[str] | None = None):
    allowed = set(chains) if chains is not None else None
    ca_atoms = {}
    for model in structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            ordered = []
            for residue in chain:
                if "CA" not in residue:
                    continue
                ordered.append(residue["CA"])
            if ordered:
                ca_atoms[chain.id] = ordered
    return ca_atoms


def renumber_mobile_residues_by_order(ref_structure, mobile_structure, chains: Iterable[str] | None = None) -> bool:
    allowed = set(chains) if chains is not None else None
    changed = False
    ref_by_chain = {}
    mobile_by_chain = {}

    for model in ref_structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            residues = [res for res in chain if "CA" in res]
            if residues:
                ref_by_chain[chain.id] = residues

    for model in mobile_structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            residues = [res for res in chain if "CA" in res]
            if residues:
                mobile_by_chain[chain.id] = residues

    for chain_id in sorted(set(ref_by_chain) & set(mobile_by_chain)):
        ref_residues = ref_by_chain[chain_id]
        mobile_residues = mobile_by_chain[chain_id]
        if len(ref_residues) != len(mobile_residues):
            continue
        for ref_residue, mobile_residue in zip(ref_residues, mobile_residues):
            if mobile_residue.id != ref_residue.id:
                mobile_residue.id = ref_residue.id
                changed = True

    return changed


def get_matched_ca_atoms(ref_structure, mobile_structure, chains: list[str] | None = None):
    ref_map = get_ca_atoms_by_key(ref_structure, chains=chains)
    mobile_map = get_ca_atoms_by_key(mobile_structure, chains=chains)
    common_keys = sorted(set(ref_map.keys()) & set(mobile_map.keys()), key=lambda k: (k[0], k[1], k[2]))

    if len(common_keys) < 3:
        ref_ordered = get_ca_atoms_by_chain_order(ref_structure, chains=chains)
        mobile_ordered = get_ca_atoms_by_chain_order(mobile_structure, chains=chains)
        shared_chains = [chain_id for chain_id in ref_ordered if chain_id in mobile_ordered]

        if shared_chains:
            ref_atoms = []
            mobile_atoms = []
            for chain_id in shared_chains:
                ref_chain_atoms = ref_ordered[chain_id]
                mobile_chain_atoms = mobile_ordered[chain_id]
                if len(ref_chain_atoms) != len(mobile_chain_atoms):
                    continue
                ref_atoms.extend(ref_chain_atoms)
                mobile_atoms.extend(mobile_chain_atoms)
            if len(ref_atoms) >= 3 and len(ref_atoms) == len(mobile_atoms):
                return ref_atoms, mobile_atoms

        region = f"chains {chains}" if chains is not None else "all chains"
        raise ValueError(
            f"Insufficient matched CA atoms in {region}: "
            f"matched={len(common_keys)} ref={len(ref_map)} mobile={len(mobile_map)}"
        )

    ref_atoms = [ref_map[key] for key in common_keys]
    mobile_atoms = [mobile_map[key] for key in common_keys]
    return ref_atoms, mobile_atoms


def rmsd_without_refit(ref_atoms, mobile_atoms) -> float:
    if len(ref_atoms) != len(mobile_atoms):
        raise ValueError(f"RMSD atom count mismatch: {len(ref_atoms)} vs {len(mobile_atoms)}")
    if not ref_atoms:
        raise ValueError("No atoms provided for RMSD computation")

    squared = 0.0
    for ref_atom, mobile_atom in zip(ref_atoms, mobile_atoms):
        diff = ref_atom.coord - mobile_atom.coord
        squared += float(diff[0] ** 2 + diff[1] ** 2 + diff[2] ** 2)
    return math.sqrt(squared / len(ref_atoms))


def parse_design_name(json_file: Path) -> tuple[str, Path]:
    stem = json_file.stem
    if "_summary_confidence_sample_" in stem:
        base_name, sample_rank = stem.rsplit("_summary_confidence_sample_", 1)
        design_name = f"{base_name}_sample_{sample_rank}"
        cif_path = json_file.with_name(f"{design_name}.cif")
        return design_name, cif_path

    # Legacy confidence.json in per-design directories.
    design_name = json_file.parent.name
    cif_files = sorted(json_file.parent.glob("*.cif"))
    if not cif_files:
        raise FileNotFoundError(f"No CIF file found for {json_file}")
    return design_name, cif_files[0]


def align_structure(args):
    design_path, json_path, output_dir, design_type, binder_chains_arg, target_chains_arg = args

    try:
        design_name, cif_path = parse_design_name(json_path)
        if not design_path.exists():
            raise FileNotFoundError(f"Design PDB not found for {design_name}: {design_path}")
        if not cif_path.exists():
            raise FileNotFoundError(f"Protenix CIF not found for {design_name}: {cif_path}")

        ref_structure = PDBParser(QUIET=True).get_structure("design", design_path)
        mobile_structure = MMCIFParser(QUIET=True).get_structure("protenix", cif_path)
        ref_chain_ids = set(get_chain_ids(ref_structure))
        mobile_chain_ids = set(get_chain_ids(mobile_structure))

        if design_type == "binder":
            binder_chains = [c.strip() for c in binder_chains_arg.split(",") if c.strip()] or ["H", "L"]
            target_chains = [c.strip() for c in target_chains_arg.split(",") if c.strip()] or ["T"]
            expected = set(binder_chains + target_chains)
            if not (expected <= ref_chain_ids and expected <= mobile_chain_ids):
                if remap_mobile_chain_ids_by_order(ref_structure, mobile_structure):
                    mobile_chain_ids = set(get_chain_ids(mobile_structure))
                shared = sorted(ref_chain_ids & mobile_chain_ids)
                if len(shared) >= 2:
                    binder_chains = [shared[0]]
                    target_chains = [shared[1]]
                    logger.warning(
                        "Expected binder/target chains were not found for %s. Falling back to shared chains: binder=%s target=%s",
                        design_name,
                        binder_chains,
                        target_chains,
                    )
                elif len(shared) == 1:
                    ref_atoms, mobile_atoms = get_matched_ca_atoms(ref_structure, mobile_structure, None)
                    superimposer = Superimposer()
                    superimposer.set_atoms(ref_atoms, mobile_atoms)
                    superimposer.apply(mobile_structure.get_atoms())
                    rmsd_data = {
                        "rmsd_overall": round(superimposer.rms, 2),
                        "protenix_overall_rmsd": round(superimposer.rms, 2),
                    }
                    out_pdb = output_dir / f"{design_name}.pdb"
                    out_cif = output_dir / f"{design_name}.cif"
                    out_json = output_dir / json_path.name
                    io = PDBIO()
                    io.set_structure(mobile_structure)
                    io.save(str(out_pdb))
                    shutil.copy2(cif_path, out_cif)
                    with json_path.open("r") as handle:
                        metrics = json.load(handle)
                    metrics.update(rmsd_data)
                    metrics["validator"] = "protenix"
                    metrics["aligned_pdb"] = out_pdb.name
                    metrics["source_cif"] = out_cif.name
                    with out_json.open("w") as handle:
                        json.dump(metrics, handle, indent=2)
                    return design_name, None
                else:
                    raise ValueError(
                        f"Could not determine binder/target chains. "
                        f"design_chains={sorted(ref_chain_ids)} protenix_chains={sorted(mobile_chain_ids)}"
                    )

            renumber_mobile_residues_by_order(
                ref_structure,
                mobile_structure,
                chains=sorted(set(binder_chains + target_chains)),
            )

            ref_target, mobile_target = get_matched_ca_atoms(ref_structure, mobile_structure, target_chains)
            superimposer = Superimposer()
            superimposer.set_atoms(ref_target, mobile_target)
            superimposer.apply(mobile_structure.get_atoms())
            rmsd_target = superimposer.rms

            ref_all, mobile_all = get_matched_ca_atoms(ref_structure, mobile_structure, None)
            rmsd_overall = rmsd_without_refit(ref_all, mobile_all)

            ref_binder, mobile_binder = get_matched_ca_atoms(ref_structure, mobile_structure, binder_chains)
            rmsd_binder = rmsd_without_refit(ref_binder, mobile_binder)

            rmsd_data = {
                "rmsd_overall": round(rmsd_overall, 2),
                "rmsd_target": round(rmsd_target, 2),
                "rmsd_binder": round(rmsd_binder, 2),
                "protenix_overall_rmsd": round(rmsd_overall, 2),
                "protenix_target_rmsd": round(rmsd_target, 2),
                "protenix_binder_rmsd": round(rmsd_binder, 2),
            }
        else:
            renumber_mobile_residues_by_order(ref_structure, mobile_structure, chains=None)
            ref_atoms, mobile_atoms = get_matched_ca_atoms(ref_structure, mobile_structure, None)
            superimposer = Superimposer()
            superimposer.set_atoms(ref_atoms, mobile_atoms)
            superimposer.apply(mobile_structure.get_atoms())
            rmsd_data = {
                "rmsd_overall": round(superimposer.rms, 2),
                "protenix_overall_rmsd": round(superimposer.rms, 2),
            }

        out_pdb = output_dir / f"{design_name}.pdb"
        out_cif = output_dir / f"{design_name}.cif"
        out_json = output_dir / json_path.name

        io = PDBIO()
        io.set_structure(mobile_structure)
        io.save(str(out_pdb))
        shutil.copy2(cif_path, out_cif)

        with json_path.open("r") as handle:
            metrics = json.load(handle)
        metrics.update(rmsd_data)
        metrics["validator"] = "protenix"
        metrics["aligned_pdb"] = out_pdb.name
        metrics["source_cif"] = out_cif.name

        with out_json.open("w") as handle:
            json.dump(metrics, handle, indent=2)

        return design_name, None

    except Exception as exc:
        logger.error("Failed %s: %s", json_path.name, exc)
        return json_path.name, str(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Align Protenix predictions to source designs")
    parser.add_argument("--design_dir", type=Path, required=True, help="Directory with source design PDBs")
    parser.add_argument("--protenix_dir", type=Path, required=True, help="Directory with Protenix predictions")
    parser.add_argument("--output_dir", type=Path, required=True, help="Directory for aligned outputs")
    parser.add_argument("--design_type", choices=["binder", "monomer"], required=True)
    parser.add_argument("--binder_chains", default="", help="Comma-separated binder chains")
    parser.add_argument("--target_chains", default="", help="Comma-separated target chains")
    parser.add_argument("--ncpus", type=int, default=1, help="Parallel worker count")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    design_files = {}
    for design_file in args.design_dir.glob("*.pdb"):
        design_files[design_file.stem] = design_file
    logger.info("Found %d design files", len(design_files))

    json_files = sorted(args.protenix_dir.rglob("*_summary_confidence_sample_*.json"))
    if not json_files:
        json_files = sorted(path for path in args.protenix_dir.rglob("confidence.json"))

    if not json_files:
        logger.error("No Protenix confidence JSONs found in %s", args.protenix_dir)
        raise SystemExit(1)

    tasks = []
    for json_file in json_files:
        try:
            design_name, _ = parse_design_name(json_file)
        except Exception as exc:
            logger.warning("Skipping %s: %s", json_file.name, exc)
            continue

        base_name = design_name.rsplit("_sample_", 1)[0]
        design_path = design_files.get(base_name)
        if design_path is None:
            logger.warning("No source design PDB found for %s", design_name)
            continue

        tasks.append(
            (
                design_path,
                json_file,
                args.output_dir,
                args.design_type,
                args.binder_chains,
                args.target_chains,
            )
        )

    if not tasks:
        logger.error("No alignable Protenix tasks were found")
        raise SystemExit(1)

    if args.ncpus > 1:
        with Pool(processes=args.ncpus) as pool:
            results = pool.map(align_structure, tasks)
    else:
        results = [align_structure(task) for task in tasks]

    failures = [name for name, error in results if error]
    logger.info(
        "Alignment complete: %d succeeded, %d failed",
        len(results) - len(failures),
        len(failures),
    )
    if failures:
        logger.warning("Failed alignments: %s", ", ".join(failures[:20]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

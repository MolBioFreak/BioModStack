#!/usr/bin/env python3
import sys
import os
import re
import json
import logging
import math
import shutil
from pathlib import Path
import argparse
from multiprocessing import Pool
from Bio.PDB import PDBParser, PDBIO, Superimposer

def setup_logging():
    """Configure logging"""
    logger = logging.getLogger(__name__)
    log_file = "alignment.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logger

logger = setup_logging()


def copy_optional_file(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True

def get_chain_ids(structure):
    chain_ids = []
    for model in structure:
        for chain in model:
            chain_ids.append(chain.id)
    return sorted(set(chain_ids))


def get_ca_atoms_by_key(structure, chains=None):
    """
    Collect CA atoms keyed by (chain_id, resseq, insertion_code).
    Using keyed intersections avoids misalignment when residue lists differ.
    """
    ca_atoms = {}
    for model in structure:
        for chain in model:
            if chains is not None and chain.id not in chains:
                continue
            for residue in chain:
                if 'CA' not in residue:
                    continue
                _, resseq, icode = residue.id
                key = (chain.id, int(resseq), str(icode).strip())
                ca_atoms[key] = residue['CA']
    return ca_atoms


def get_matched_ca_atoms(ref_structure, mobile_structure, chains=None):
    """
    Match CA atoms by residue identifiers so RMSD can be computed robustly even
    when one structure has missing residues.
    """
    ref_map = get_ca_atoms_by_key(ref_structure, chains=chains)
    mobile_map = get_ca_atoms_by_key(mobile_structure, chains=chains)
    common_keys = sorted(set(ref_map.keys()) & set(mobile_map.keys()), key=lambda k: (k[0], k[1], k[2]))

    if len(common_keys) < 3:
        region = f"chains {chains}" if chains is not None else "all chains"
        raise ValueError(
            f"Insufficient matched CA atoms in {region}: "
            f"matched={len(common_keys)} ref={len(ref_map)} mobile={len(mobile_map)}"
        )

    ref_atoms = [ref_map[key] for key in common_keys]
    mobile_atoms = [mobile_map[key] for key in common_keys]
    return ref_atoms, mobile_atoms


def rmsd_without_refit(ref_atoms, mobile_atoms):
    """Compute RMSD between atom lists in their current coordinates."""
    if len(ref_atoms) != len(mobile_atoms):
        raise ValueError(f"RMSD atom count mismatch: {len(ref_atoms)} vs {len(mobile_atoms)}")
    if not ref_atoms:
        raise ValueError("No atoms provided for RMSD computation")

    squared = 0.0
    for ref_atom, mobile_atom in zip(ref_atoms, mobile_atoms):
        diff = ref_atom.coord - mobile_atom.coord
        squared += float(diff[0] ** 2 + diff[1] ** 2 + diff[2] ** 2)
    return math.sqrt(squared / len(ref_atoms))

def align_structures(args):
    """Align Boltz structure to Design template with chain-specific handling"""
    (design_path, boltz_path, out_pdb, src_json, dst_json, src_pae, dst_pae,
     fold_id, seq_id, design_type, binder_chains_arg, target_chains_arg,
     geometry_mode, strict_target_rmsd) = args
    
    try:
        parser = PDBParser(QUIET=True)
        ref_structure = parser.get_structure("design", design_path)
        boltz_structure = parser.get_structure("boltz", boltz_path)

        if design_type == 'binder':
            binder_chains = [c.strip() for c in binder_chains_arg.split(',')] if binder_chains_arg else ['A']
            target_chains = [c.strip() for c in target_chains_arg.split(',')] if target_chains_arg else ['B']

            ref_chain_ids = set(get_chain_ids(ref_structure))
            boltz_chain_ids = set(get_chain_ids(boltz_structure))
            if not (set(binder_chains + target_chains) <= ref_chain_ids and set(binder_chains + target_chains) <= boltz_chain_ids):
                shared = sorted(ref_chain_ids & boltz_chain_ids)
                if len(shared) >= 2:
                    binder_chains, target_chains = [shared[0]], [shared[1]]
                    logger.warning(
                        "Expected binder/target chains were not found for %s. "
                        "Falling back to shared chains: binder=%s target=%s",
                        boltz_path.name,
                        binder_chains,
                        target_chains,
                    )
                else:
                    raise ValueError(
                        f"Could not find two shared chains for binder alignment. "
                        f"design_chains={sorted(ref_chain_ids)} boltz_chains={sorted(boltz_chain_ids)}"
                    )

            # 1. Align target chain for final structure transform
            ref_target, boltz_target = get_matched_ca_atoms(ref_structure, boltz_structure, target_chains)
            
            superimposer = Superimposer()
            superimposer.set_atoms(ref_target, boltz_target)
            superimposer.apply(boltz_structure.get_atoms())
            rmsd_target = superimposer.rms 

            # 2. Calculate overall RMSD after target-based alignment
            ref_all_ca, boltz_all_ca = get_matched_ca_atoms(ref_structure, boltz_structure, chains=None)
            rmsd_overall = rmsd_without_refit(ref_all_ca, boltz_all_ca)

            # 3. Calculate binder RMSD after target-based alignment
            ref_binder, boltz_binder = get_matched_ca_atoms(ref_structure, boltz_structure, binder_chains)
            rmsd_binder = rmsd_without_refit(ref_binder, boltz_binder)

            rmsd_data = {
                "boltz_overall_rmsd": round(rmsd_overall, 2),
                "boltz_target_rmsd": round(rmsd_target, 2),
                "boltz_binder_rmsd": round(rmsd_binder, 2)
            }
            if strict_target_rmsd is not None and rmsd_target > strict_target_rmsd:
                raise ValueError(
                    f"anchored target drift {rmsd_target:.2f}A exceeded threshold {strict_target_rmsd:.2f}A"
                )

        else:  # Monomer design
            ref_atoms, boltz_atoms = get_matched_ca_atoms(ref_structure, boltz_structure, chains=None)
            
            superimposer = Superimposer()
            superimposer.set_atoms(ref_atoms, boltz_atoms)
            superimposer.apply(boltz_structure.get_atoms())
            
            rmsd_data = {
                "boltz_overall_rmsd": round(superimposer.rms, 2)
            }

        # Save aligned structure (always chain B aligned for binder)
        io = PDBIO()
        io.set_structure(boltz_structure)
        io.save(str(out_pdb))

        # Build output JSON even when confidence JSON is missing.
        data = {}
        if src_json.exists():
            try:
                with open(src_json, 'r') as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not parse confidence JSON {src_json}: {e}")
        else:
            logger.warning(f"Confidence JSON not found for {boltz_path.name}: expected {src_json}")

        if design_type == 'binder':
            out_json = {
                "fold_id": fold_id,
                "seq_id": seq_id,
                "description": boltz_path.name,
                "validation_geometry_mode": geometry_mode,
                "target_anchor_enabled": geometry_mode == "anchored",
                "target_anchor_strict": strict_target_rmsd is not None,
                "boltz_overall_rmsd": round(rmsd_data.get("boltz_overall_rmsd", 0), 2),
                "boltz_target_rmsd": round(rmsd_data.get("boltz_target_rmsd", 0), 2),
                "boltz_binder_rmsd": round(rmsd_data.get("boltz_binder_rmsd", 0), 2),
                "boltz_conf_score": round(data.get("confidence_score", 0), 3),
                "boltz_ptm": round(data.get("ptm", 0), 3),
                "boltz_ptm_interface": round(data.get("iptm", 0), 3),
                "boltz_plddt": round(data.get("complex_plddt", 0), 3),
                "boltz_plddt_interface": round(data.get("complex_iplddt", 0), 3),
                "boltz_pde": round(data.get("complex_pde", 0), 2),
                "boltz_pde_interface": round(data.get("complex_ipde", 0), 2)
            }
        else:
            out_json = {
                "fold_id": fold_id,
                "seq_id": seq_id,
                "description": boltz_path.name,
                "validation_geometry_mode": geometry_mode,
                "target_anchor_enabled": geometry_mode == "anchored",
                "target_anchor_strict": strict_target_rmsd is not None,
                "boltz_overall_rmsd": round(rmsd_data.get("boltz_overall_rmsd", 0), 2),
                "boltz_conf_score": round(data.get("confidence_score", 0), 3),
                "boltz_ptm": round(data.get("ptm", 0), 3),
                "boltz_plddt": round(data.get("complex_plddt", 0), 3),
                "boltz_pde": round(data.get("complex_pde", 0), 2),
            }

        if not src_pae.exists():
            raise FileNotFoundError(f"Boltz PAE NPZ not found for {boltz_path.name}: expected {src_pae}")
        copied_pae = copy_optional_file(src_pae, dst_pae)
        if not copied_pae:
            raise FileNotFoundError(f"Boltz PAE NPZ could not be copied for {boltz_path.name}: {src_pae}")
        out_json['aligned_error_artifact'] = dst_pae.name
        out_json['aligned_error_format'] = 'boltz_pae_npz'

        with open(dst_json, 'w') as f:
            json.dump(out_json, f, indent=2)

        return (boltz_path.name, rmsd_data.get('boltz_overall_rmsd'), None)

    except Exception as e:
        logger.error(f"Failed {boltz_path.name}: {str(e)}")
        return (boltz_path.name, None, str(e))

def main():
    parser = argparse.ArgumentParser(description="Align Boltz predictions to designs")
    parser.add_argument("--design_dir", type=Path, required=True, 
                      help="Directory with Design PDBs (fold_*_seq_*.pdb)")
    parser.add_argument("--boltz_dir", type=Path, required=True,
                      help="Directory with Boltz PDBs and JSONs (fold_*_seq_*_boltzpred.*)")
    parser.add_argument("--output_dir", type=Path, default="aligned",
                      help="Output directory for results")
    parser.add_argument("--design_type", choices=['binder', 'monomer'], required=True,
                      help="Design type: 'binder' (A/B chains) or 'monomer (A chain)'")
    parser.add_argument("--binder_chains", type=str, default="",
                      help="Comma separated list of binder chains (e.g. H,L or A)")
    parser.add_argument("--target_chains", type=str, default="",
                      help="Comma separated list of target chains (e.g. T or B)")
    parser.add_argument("--geometry_mode", choices=['flexible', 'anchored'], default='flexible',
                      help="Whether validation was run in flexible or anchored target mode")
    parser.add_argument("--strict_target_rmsd", type=float, default=None,
                      help="Fail anchored outputs whose target RMSD exceeds this threshold")
    parser.add_argument("--ncpus", type=int, default=1,
                      help="Number of CPUs for parallel processing")
    args = parser.parse_args()
    
    # Validate input directories
    if not args.design_dir.exists():
        logger.error(f"Design directory not found: {args.design_dir}")
        sys.exit(1)
        
    if not args.boltz_dir.exists():
        logger.error(f"Boltz directory not found: {args.boltz_dir}")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Map Design files - match any PDB that is NOT a boltzpred
    design_files = {}
    for design_file in args.design_dir.glob("*.pdb"):
        # Skip boltzpred files
        if "_boltzpred" in design_file.name:
            continue
        # Use the filename stem as the key
        design_files[design_file.stem] = design_file
    
    logger.info(f"Found {len(design_files)} design files")
    
    # Prepare processing tasks - match boltzpred files to their designs
    tasks = []
    for boltz_file in args.boltz_dir.glob("*_boltzpred.pdb"):
        # Extract the base name by removing _boltzpred suffix
        base_name = boltz_file.stem.replace("_boltzpred", "")
        
        if base_name not in design_files:
            logger.warning(f"No design file for {base_name}, skipping {boltz_file.name}")
            continue
        
        # Try to extract fold_id and seq_id from the filename
        # Support both "fold_X_seq_Y" and "..._model_X_seq_Y" patterns
        fold_id = 0
        seq_id = 0
        legacy_match = re.search(r"fold_(\d+)_seq_(\d+)", base_name)
        new_match = re.search(r"model_(\d+)_seq_(\d+)", base_name)
        if legacy_match:
            fold_id = int(legacy_match.group(1))
            seq_id = int(legacy_match.group(2))
        elif new_match:
            fold_id = int(new_match.group(1))
            seq_id = int(new_match.group(2))
            
        # Generate paths
        src_json = args.boltz_dir / f"{boltz_file.stem}.json"
        src_pae = args.boltz_dir / f"{boltz_file.stem}.pae.npz"
        out_pdb = args.output_dir / f"{boltz_file.stem}.pdb"
        dst_json = args.output_dir / f"{boltz_file.stem}.json"
        dst_pae = args.output_dir / f"{boltz_file.stem}.pae.npz"

        tasks.append((
            design_files[base_name],
            boltz_file,
            out_pdb,
            src_json,
            dst_json,
            src_pae,
            dst_pae,
            fold_id,
            seq_id,
            args.design_type,
            args.binder_chains,
            args.target_chains,
            args.geometry_mode,
            args.strict_target_rmsd,
        ))

    if not tasks:
        logger.error("No Boltz prediction files matched design files for RMSD alignment")
        sys.exit(1)
    
    # Log processing start
    logger.info(f"Starting alignment of {len(tasks)} Boltz structures")
    logger.info(f"Using design directory: {args.design_dir}")
    logger.info(f"Output directory: {args.output_dir}")
    
    # Process tasks in parallel
    with Pool(args.ncpus) as pool:
        results = pool.map(align_structures, tasks)
    
    # Report summary
    successes = sum(1 for r in results if r[1] is not None)
    failures = len(results) - successes
    
    logger.info("\n=== Alignment Summary ===")
    logger.info(f"Total structures processed: {len(tasks)}")
    logger.info(f"Successful alignments: {successes}")
    logger.info(f"Failed alignments: {failures}")
    
    if failures > 0:
        logger.info("\nFailed cases:")
        for name, _, error in results:
            if error:
                logger.info(f"  {name}: {error}")
    if successes == 0:
        logger.error("RMSD alignment produced zero successful outputs")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1)

#!/usr/bin/env python3
"""Prepare explicit ProteinMPNN chain roles without rewriting B-factors."""

import argparse
from pathlib import Path
from collections import defaultdict


def parse_pdb_chains(pdb_path):
    """Parse PDB to get list of chain IDs."""
    chains = set()
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                chain_id = line[21]
                chains.add(chain_id)
    return list(chains)


def add_fixed_labels_to_pdb(input_pdb, output_pdb, chains_to_fix):
    """Transport real chain roles; preserve geometry and input residue locks."""
    if __package__:
        from .proteinmpnn_native_binding import prepare, pdb_domain
    else:
        from proteinmpnn_native_binding import prepare, pdb_domain
    chains = list(dict.fromkeys(r[0] for r in pdb_domain(input_pdb, strict=False)))
    prepare(input_pdb, output_pdb, {
        'design_chain': [c for c in chains if c not in chains_to_fix],
        'target_chain': [c for c in chains if c in chains_to_fix],
    }, strict=False)
    return len(chains_to_fix)


def main():
    parser = argparse.ArgumentParser(
        description="Add FIXED labels to PDB files for ProteinMPNN (locks all chains except H/L)"
    )
    parser.add_argument("--input_dir", required=True, help="Directory containing input PDB files")
    parser.add_argument("--output_dir", required=True, help="Directory for output PDB files with FIXED labels")
    parser.add_argument("--designable_chains", default="H,L", 
                        help="Comma-separated list of chains that should be designed (default: H,L)")
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    designable_chains = set(args.designable_chains.split(','))
    
    pdb_files = list(input_dir.glob("*.pdb"))
    print(f"Processing {len(pdb_files)} PDB files from {input_dir}")
    print(f"Designable chains (NOT fixed): {designable_chains}")
    
    for pdb in pdb_files:
        # Get chains in this PDB
        all_chains = parse_pdb_chains(pdb)
        
        # Fix everything except designable chains
        chains_to_fix = [c for c in all_chains if c not in designable_chains]
        
        output_pdb = output_dir / pdb.name
        num_fixed = add_fixed_labels_to_pdb(pdb, output_pdb, chains_to_fix)
        
        if chains_to_fix:
            print(f"  {pdb.name}: Fixed chains {chains_to_fix}, designed chains {[c for c in all_chains if c in designable_chains]}")
        else:
            print(f"  {pdb.name}: All chains designable (no chains to fix)")
    
    print(f"\nWrote {len(pdb_files)} PDB files with FIXED labels to {output_dir}")


if __name__ == "__main__":
    main()

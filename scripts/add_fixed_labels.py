#!/usr/bin/env python3
"""
Add FIXED labels to PDB files for ProteinMPNN.

This script marks residues in specified chains (or all chains except H/L) 
as FIXED by modifying the B-factor column to 0.00 for fixed residues
and 1.00 for designable residues.

ProteinMPNN's dl_interface_design scripts detect these B-factor values
to determine which residues to keep fixed during sequence design.

Usage for antibody design:
    python add_fixed_labels.py --input_dir ./pdbs --output_dir ./fixed_pdbs

This will fix all chains EXCEPT H and L (i.e., Chain T = target antigen).
"""

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
    """
    Add FIXED labels to a PDB file by modifying B-factor values.
    
    Fixed residues get B-factor = 0.00
    Designable residues get B-factor = 1.00
    
    This convention is used by dl_binder_design ProteinMPNN scripts.
    """
    output_lines = []
    
    with open(input_pdb, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                chain_id = line[21]
                
                # Determine if this residue should be fixed
                if chain_id in chains_to_fix:
                    # FIXED: Set B-factor to 0.00
                    new_bfactor = "  0.00"
                else:
                    # DESIGNABLE: Set B-factor to 1.00
                    new_bfactor = "  1.00"
                
                # PDB format: B-factor is columns 61-66 (6 chars, right-justified)
                # Full line structure: cols 1-60, then 61-66 (bfactor), then rest
                if len(line) >= 66:
                    new_line = line[:60] + new_bfactor + line[66:]
                else:
                    # Short line, pad and add bfactor
                    padded = line.rstrip().ljust(60)
                    new_line = padded + new_bfactor + "\n"
                
                output_lines.append(new_line)
            else:
                output_lines.append(line)
    
    with open(output_pdb, 'w') as f:
        f.writelines(output_lines)
    
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

import argparse
import json
import os
from pathlib import Path
from collections import defaultdict

def parse_pdb_chains(pdb_path):
    """
    Parses a PDB file to identify available chains and their residue ranges.
    Returns a dictionary of chain_id -> list of residue numbers (integers).
    """
    chains = defaultdict(list)
    seen_residues = set()

    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                chain_id = line[21]
                # PDB residue number (cols 23-26) + insertion code (col 27)
                # We typically rely on residue number for ranges
                try:
                    res_seq = int(line[22:26])
                except ValueError:
                    continue # Skip strange numbering if any
                
                unique_id = (chain_id, res_seq)
                if unique_id not in seen_residues:
                    chains[chain_id].append(res_seq)
                    seen_residues.add(unique_id)
    
    # Sort residue numbers for each chain
    for chain in chains:
        chains[chain].sort()
        
    return chains

def get_ranges(numbers):
    """
    Converts a sorted list of numbers into a list of ranges (start, end).
    e.g., [1, 2, 3, 5, 6] -> [(1, 3), (5, 6)]
    """
    if not numbers:
        return []
    
    ranges = []
    start = numbers[0]
    prev = numbers[0]
    
    for x in numbers[1:]:
        if x != prev + 1:
            ranges.append((start, prev))
            start = x
        prev = x
    ranges.append((start, prev))
    
    return ranges

def main():
    parser = argparse.ArgumentParser(description="Generate constraints for Antibody Design (Fix Chain T)")
    parser.add_argument("--input_dir", required=True, help="Directory containing PDB files")
    parser.add_argument("--out_fampnn", required=True, help="Output CSV file for FAMPNN")
    parser.add_argument("--out_mpnn", required=True, help="Output JSON file for ProteinMPNN")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    pdb_files = list(input_dir.glob("*.pdb"))
    
    # Data structures for outputs
    mpnn_fixed_chains = {} # { "pdb_name": ["T", "C", ...] }
    fampnn_lines = ["pdb,fixed_seq_positions,fixed_sidechains\n"]

    print(f"Processing {len(pdb_files)} PDBs from {input_dir}...")

    for pdb in pdb_files:
        pdb_name = pdb.stem
        chains = parse_pdb_chains(pdb)
        found_chains = list(chains.keys())
        
        # -------------------------------------------------------------
        # POLICY: FIX EVERYTHING EXCEPT CHAINS H AND L
        # This implicitly fixes Chain T (Antigen) and any other context.
        # -------------------------------------------------------------
        
        chains_to_fix = [c for c in found_chains if c not in ['H', 'L']]
        
        # --- 1. ProteinMPNN Constraints (Fixed Chains) ---
        if chains_to_fix:
            mpnn_fixed_chains[pdb_name] = chains_to_fix
        
        # --- 2. FAMPNN Constraints (Fixed Residue Ranges) ---
        # Format: chainA1-100,chainB20-50
        
        fampnn_constraints = []
        for chain in chains_to_fix:
            residues = chains[chain]
            ranges = get_ranges(residues)
            for r_start, r_end in ranges:
                fampnn_constraints.append(f"{chain}{r_start}-{r_end}")
        
        # Join with commas
        fampnn_str = ",".join(fampnn_constraints)
        
        # Write to FAMPNN CSV list (Sequence AND Sidechain both fixed for antigen)
        # Note: If fampnn_str is empty (no target), we leave it empty ""
        fampnn_lines.append(f'"{pdb_name}","{fampnn_str}","{fampnn_str}"\n')

    # --- Write Outputs ---
    
    # 1. ProteinMPNN JSON
    with open(args.out_mpnn, 'w') as f:
        json.dump(mpnn_fixed_chains, f, indent=2)
    print(f"Wrote ProteinMPNN constraints to {args.out_mpnn}")

    # 2. FAMPNN CSV
    with open(args.out_fampnn, 'w') as f:
        f.writelines(fampnn_lines)
    print(f"Wrote FAMPNN constraints to {args.out_fampnn}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Generate constraints for Antibody Sequence Design.

This script parses HLT-formatted PDB files from RFantibody and generates
fixed residue constraints for FAMPNN and ProteinMPNN based on the selected
design mode.

Design Modes:
- cdr_only (default): Only CDR loops are designable, framework fixed
- cdr_selective: Only selected CDR loops are designable (e.g., H3 only)
- framework_allowed: CDRs + framework designable, but VHH tetrad protected
- full_design: Everything designable (expert mode)

HLT Format REMARK labels:
  REMARK PDBinfo-LABEL:   27 H1
  REMARK PDBinfo-LABEL:   56 H2
  etc.
"""

import argparse
import json
import os
from pathlib import Path
from collections import defaultdict


# IMGT positions for VHH tetrad (FR2 hydrophilic substitutions)
# These positions are critical for VHH solubility and should typically be preserved
VHH_TETRAD_IMGT_POSITIONS = [37, 44, 45, 47]


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
                try:
                    res_seq = int(line[22:26])
                except ValueError:
                    continue
                
                unique_id = (chain_id, res_seq)
                if unique_id not in seen_residues:
                    chains[chain_id].append(res_seq)
                    seen_residues.add(unique_id)
    
    for chain in chains:
        chains[chain].sort()
        
    return chains


def parse_hlt_cdr_labels(pdb_path):
    """
    Parse CDR positions from HLT REMARK PDBinfo-LABEL lines.
    
    Returns:
        dict: {'H1': [27,28,...], 'H2': [...], 'H3': [...], 'L1': [...], ...}
    """
    cdr_dict = {'H1': [], 'H2': [], 'H3': [], 'L1': [], 'L2': [], 'L3': []}
    
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('REMARK PDBinfo-LABEL:'):
                parts = line.split()
                # Format: REMARK PDBinfo-LABEL: <res_num> <loop_id>
                # parts[0] = 'REMARK', parts[1] = 'PDBinfo-LABEL:', parts[2] = res_num, parts[3] = loop_id
                if len(parts) >= 4:
                    try:
                        res_num = int(parts[2])
                        loop_id = parts[3].upper()  # H1, H2, H3, L1, L2, L3
                        if loop_id in cdr_dict:
                            cdr_dict[loop_id].append(res_num)
                    except (ValueError, IndexError):
                        continue
    
    # Sort each CDR's residues
    for loop in cdr_dict:
        cdr_dict[loop].sort()
    
    return cdr_dict


def get_chain_sequence(pdb_path, chain_id):
    """Extract amino acid sequence for a specific chain."""
    aa_codes = {
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    }
    
    residues = {}  # res_num -> aa_code
    
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') and line[21] == chain_id:
                res_name = line[17:20].strip()
                try:
                    res_num = int(line[22:26])
                except ValueError:
                    continue
                
                if res_num not in residues and res_name in aa_codes:
                    residues[res_num] = aa_codes[res_name]
    
    return residues


def estimate_vhh_tetrad_positions(chain_residues, chain_id):
    """
    Estimate VHH tetrad positions based on IMGT numbering.
    
    For VHH/nanobodies, the FR2 tetrad is at IMGT positions 37, 44, 45, 47.
    These correspond roughly to PDB positions ~37, ~44, ~45, ~47 if numbering
    starts at 1. For chains with different numbering, we estimate based on
    relative position.
    
    Returns:
        list: PDB residue numbers corresponding to tetrad positions
    """
    if not chain_residues:
        return []
    
    # Get sorted residue numbers
    sorted_residues = sorted(chain_residues.keys())
    if len(sorted_residues) < 50:  # Too short to be a VHH
        return []
    
    # Calculate offset (difference between actual numbering and 1-indexed)
    first_residue = sorted_residues[0]
    offset = first_residue - 1
    
    # Map IMGT positions to actual PDB residue numbers
    tetrad_positions = []
    for imgt_pos in VHH_TETRAD_IMGT_POSITIONS:
        pdb_pos = imgt_pos + offset
        if pdb_pos in chain_residues:
            tetrad_positions.append(pdb_pos)
    
    return tetrad_positions


def get_ranges(numbers):
    """
    Converts a sorted list of numbers into a list of ranges (start, end).
    e.g., [1, 2, 3, 5, 6] -> [(1, 3), (5, 6)]
    """
    if not numbers:
        return []
    
    numbers = sorted(set(numbers))
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


def compute_fixed_residues(mode, chains_data, cdr_dict, tetrad_positions, 
                           selected_loops, protect_tetrad, antibody_chains):
    """
    Compute which residues should be fixed based on design mode.
    
    Args:
        mode: 'cdr_only', 'cdr_selective', 'framework_allowed', 'full_design'
        chains_data: dict of chain_id -> list of residue numbers
        cdr_dict: dict of CDR loop -> list of residue numbers
        tetrad_positions: list of tetrad residue positions (chain H)
        selected_loops: list of CDR loops to design (for cdr_selective)
        protect_tetrad: bool, whether to always fix tetrad positions
        antibody_chains: list of antibody chain IDs (e.g., ['H', 'L'])
    
    Returns:
        dict: chain_id -> list of FIXED residue numbers
    """
    fixed_residues = {}
    
    # Always fix target chain(s) completely
    target_chains = [c for c in chains_data.keys() if c not in antibody_chains]
    for chain in target_chains:
        fixed_residues[chain] = chains_data[chain]
    
    # Get all CDR residues as a set for quick lookup
    all_cdr_residues = set()
    for loop, residues in cdr_dict.items():
        all_cdr_residues.update(residues)
    
    # Get selected CDR residues (for cdr_selective mode)
    selected_cdr_residues = set()
    for loop in selected_loops:
        if loop in cdr_dict:
            selected_cdr_residues.update(cdr_dict[loop])
    
    # Process each antibody chain
    for chain in antibody_chains:
        if chain not in chains_data:
            continue
            
        all_residues = set(chains_data[chain])
        
        if mode == 'cdr_only':
            # Fix everything EXCEPT CDRs
            fixed = all_residues - all_cdr_residues
            
        elif mode == 'cdr_selective':
            # Fix everything EXCEPT selected CDRs
            fixed = all_residues - selected_cdr_residues
            
        elif mode == 'framework_allowed':
            # Everything designable, but protect tetrad if requested
            fixed = set()
            if protect_tetrad and chain == 'H':
                fixed.update(tetrad_positions)
                
        elif mode == 'full_design':
            # Nothing fixed on antibody chains
            fixed = set()
            
        else:
            # Default to cdr_only
            fixed = all_residues - all_cdr_residues
        
        # Always protect tetrad if requested (except in full_design)
        if protect_tetrad and chain == 'H' and mode != 'full_design':
            fixed.update(tetrad_positions)
        
        if fixed:
            fixed_residues[chain] = sorted(fixed)
    
    return fixed_residues


def main():
    parser = argparse.ArgumentParser(
        description="Generate constraints for Antibody Sequence Design"
    )
    parser.add_argument("--input_dir", required=True, 
                        help="Directory containing PDB files")
    parser.add_argument("--out_fampnn", required=True, 
                        help="Output CSV file for FAMPNN")
    parser.add_argument("--out_mpnn", required=True, 
                        help="Output JSON file for ProteinMPNN")
    
    # Design mode arguments
    parser.add_argument("--design_mode", default="cdr_only",
                        choices=["cdr_only", "cdr_selective", "framework_allowed", "full_design"],
                        help="Design mode (default: cdr_only)")
    parser.add_argument("--design_loops", default="H1,H2,H3,L1,L2,L3",
                        help="Comma-separated list of CDR loops to design (for cdr_selective mode)")
    parser.add_argument("--protect_tetrad", default="true",
                        help="Protect VHH tetrad positions in FR2 (default: true)")
    parser.add_argument("--antibody_chains", default="H,L",
                        help="Comma-separated list of antibody chain IDs (default: H,L)")
    
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    pdb_files = list(input_dir.glob("*.pdb"))
    
    # Parse arguments
    design_mode = args.design_mode
    selected_loops = [l.strip().upper() for l in args.design_loops.split(',')]
    protect_tetrad = args.protect_tetrad.lower() in ('true', '1', 'yes')
    antibody_chains = [c.strip().upper() for c in args.antibody_chains.split(',')]
    
    # Data structures for outputs
    mpnn_fixed_chains = {}
    fampnn_lines = ["pdb,fixed_seq_positions,fixed_sidechains\n"]

    print(f"Processing {len(pdb_files)} PDBs from {input_dir}...")
    print(f"Design mode: {design_mode}")
    print(f"Selected loops: {selected_loops}")
    print(f"Protect VHH tetrad: {protect_tetrad}")
    print(f"Antibody chains: {antibody_chains}")

    for pdb in pdb_files:
        pdb_name = pdb.stem
        chains_data = parse_pdb_chains(pdb)
        
        # Parse CDR labels from HLT format
        cdr_dict = parse_hlt_cdr_labels(pdb)
        
        # Check if we found any CDR labels
        has_cdr_labels = any(len(v) > 0 for v in cdr_dict.values())
        
        if not has_cdr_labels:
            # Fallback: If no HLT labels, use heuristic (fix entire target chain)
            print(f"  {pdb_name}: No HLT CDR labels found, using chain-based fallback")
            chains_to_fix = [c for c in chains_data.keys() if c not in antibody_chains]
            
            # For FAMPNN
            fampnn_constraints = []
            for chain in chains_to_fix:
                residues = chains_data[chain]
                ranges = get_ranges(residues)
                for r_start, r_end in ranges:
                    fampnn_constraints.append(f"{chain}{r_start}-{r_end}")
            
            fampnn_str = ",".join(fampnn_constraints)
            fampnn_lines.append(f'"{pdb_name}","{fampnn_str}","{fampnn_str}"\n')
            
            # For ProteinMPNN
            if chains_to_fix:
                mpnn_fixed_chains[pdb_name] = chains_to_fix
            
            continue
        
        # Get VHH tetrad positions
        if 'H' in chains_data:
            chain_h_residues = get_chain_sequence(pdb, 'H')
            tetrad_positions = estimate_vhh_tetrad_positions(chain_h_residues, 'H')
        else:
            tetrad_positions = []
        
        # Compute fixed residues based on mode
        fixed_residues = compute_fixed_residues(
            mode=design_mode,
            chains_data=chains_data,
            cdr_dict=cdr_dict,
            tetrad_positions=tetrad_positions,
            selected_loops=selected_loops,
            protect_tetrad=protect_tetrad,
            antibody_chains=antibody_chains
        )
        
        # Generate FAMPNN constraints (chain + residue ranges)
        fampnn_constraints = []
        for chain, residues in sorted(fixed_residues.items()):
            ranges = get_ranges(residues)
            for r_start, r_end in ranges:
                fampnn_constraints.append(f"{chain}{r_start}-{r_end}")
        
        fampnn_str = ",".join(fampnn_constraints)
        fampnn_lines.append(f'"{pdb_name}","{fampnn_str}","{fampnn_str}"\n')
        
        # Generate ProteinMPNN constraints (entire chains)
        # For MPNN, we still use chain-level fixing for target chains
        target_chains = [c for c in chains_data.keys() if c not in antibody_chains]
        if target_chains:
            mpnn_fixed_chains[pdb_name] = target_chains
        
        # Report summary
        cdr_count = sum(len(v) for v in cdr_dict.values())
        fixed_count = sum(len(v) for v in fixed_residues.values())
        print(f"  {pdb_name}: {cdr_count} CDR residues, {fixed_count} fixed residues, tetrad: {tetrad_positions}")

    # Write outputs
    with open(args.out_mpnn, 'w') as f:
        json.dump(mpnn_fixed_chains, f, indent=2)
    print(f"Wrote ProteinMPNN constraints to {args.out_mpnn}")

    with open(args.out_fampnn, 'w') as f:
        f.writelines(fampnn_lines)
    print(f"Wrote FAMPNN constraints to {args.out_fampnn}")


if __name__ == "__main__":
    main()

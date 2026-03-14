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
import re
from pathlib import Path
from collections import defaultdict


# IMGT positions for VHH tetrad (FR2 hydrophilic substitutions)
# These positions are critical for VHH solubility and should typically be preserved
VHH_TETRAD_IMGT_POSITIONS = [37, 44, 45, 47]

# Framework contact hotspots from Zavrtanik et al. 2018
# These FR residues frequently mediate antigen contacts in nanobodies
FR_CONTACT_POSITIONS = {
    'fr2': [37, 42, 44, 45, 47],      # FR2 contacts (includes tetrad)
    'de_loop': [72, 73, 74, 75],      # DE loop
    'fr3': [82, 83, 84, 85, 86, 87],  # FR3 contacts
    'fr4': [101, 102, 103],           # FR4 contacts
}

# Cysteine positions for disulfide bonds (IMGT numbering)
DISULFIDE_POSITIONS = [23, 104]  # Conserved VH/VHH disulfide


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


def parse_chain_position_spec(spec):
    """
    Parse chain-specific positions, e.g., "H27,H30-32,L50".
    Returns dict: chain -> sorted list of positions.
    """
    if not spec:
        return {}
    mapping = {}
    for token in spec.split(','):
        token = token.strip()
        if not token:
            continue
        match = re.match(r'^([A-Za-z])(\d+)(?:-(\d+))?$', token)
        if not match:
            print(f"Warning: Invalid extra_fixed_positions token '{token}', skipping")
            continue
        chain, start, end = match.groups()
        start = int(start)
        end = int(end) if end else start
        mapping.setdefault(chain.upper(), set()).update(range(start, end + 1))
    return {k: sorted(v) for k, v in mapping.items()}


def merge_chain_position_maps(*maps):
    merged = defaultdict(set)
    for mapping in maps:
        if not mapping:
            continue
        for chain, residues in mapping.items():
            merged[str(chain).upper()].update(int(residue) for residue in residues)
    return {chain: sorted(values) for chain, values in merged.items()}


def resolve_per_pdb_fixed_positions(pdb_name, mapping):
    if not mapping:
        return {}
    if pdb_name in mapping:
        return mapping[pdb_name]

    prefix_matches = [
        (key, value)
        for key, value in mapping.items()
        if pdb_name.startswith(f"{key}_") or pdb_name.startswith(f"{key}-") or pdb_name == key
    ]
    if not prefix_matches:
        return {}
    prefix_matches.sort(key=lambda item: len(item[0]), reverse=True)
    return prefix_matches[0][1]


def compute_fixed_residues(mode, chains_data, cdr_dict, tetrad_positions,
                           selected_loops, protect_tetrad, antibody_chains,
                           extra_protected_positions=None, extra_fixed_by_chain=None,
                           cdr_override_by_chain=None,
                           lock_target_chains=True,
                           lock_antibody_framework=True):
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
        extra_protected_positions: list of additional PDB positions to protect on chain H
        extra_fixed_by_chain: dict of chain_id -> list of PDB positions to always fix
        cdr_override_by_chain: optional dict of chain_id -> PDB residue numbers to treat as CDRs
    
    Returns:
        dict: chain_id -> list of FIXED residue numbers
    """
    fixed_residues = {}
    extra_protected = set(extra_protected_positions or [])
    extra_fixed_by_chain = extra_fixed_by_chain or {}
    
    # By default, keep non-antibody chains sequence-locked so redesign never
    # mutates the experimental antigen or other partner chains.
    target_chains = [c for c in chains_data.keys() if c not in antibody_chains]
    if lock_target_chains:
        for chain in target_chains:
            fixed_residues[chain] = chains_data[chain]
    
    cdr_override_by_chain = cdr_override_by_chain or {}

    def build_cdr_sets_by_chain():
        all_by_chain = {}
        selected_by_chain = {}
        heavy_chain = antibody_chains[0] if antibody_chains else None
        light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None

        for loop, residues in cdr_dict.items():
            loop_id = loop.upper()
            if loop_id.startswith("H") and heavy_chain:
                chain_id = heavy_chain
            elif loop_id.startswith("L") and light_chain:
                chain_id = light_chain
            else:
                continue

            all_by_chain.setdefault(chain_id, set()).update(residues)
            if loop_id in selected_loops:
                selected_by_chain.setdefault(chain_id, set()).update(residues)

        return all_by_chain, selected_by_chain

    cdr_by_chain, selected_cdr_by_chain = build_cdr_sets_by_chain()
    
    # Process each antibody chain
    for chain in antibody_chains:
        if chain not in chains_data:
            continue
            
        all_residues = set(chains_data[chain])
        detected_all_cdr_residues = set(cdr_by_chain.get(chain, set()))
        detected_selected_cdr_residues = set(selected_cdr_by_chain.get(chain, set()))
        
        # Prefer loop labels recovered from the actual structure. Chain-level
        # overrides from the original custom loop request are only a fallback
        # when the current PDB no longer exposes HLT/ANARCII loop labels.
        if detected_all_cdr_residues:
            all_cdr_residues = detected_all_cdr_residues
            selected_cdr_residues = detected_selected_cdr_residues
        elif chain in cdr_override_by_chain and cdr_override_by_chain[chain]:
            all_cdr_residues = set(cdr_override_by_chain[chain])
            selected_cdr_residues = set(all_cdr_residues)
        else:
            all_cdr_residues = detected_all_cdr_residues
            selected_cdr_residues = detected_selected_cdr_residues

        if mode == 'cdr_only':
            # Fix everything EXCEPT CDRs
            fixed = all_residues - all_cdr_residues if lock_antibody_framework else set()
            
        elif mode == 'cdr_selective':
            # Fix everything EXCEPT selected CDRs
            fixed = all_residues - selected_cdr_residues if lock_antibody_framework else (all_cdr_residues - selected_cdr_residues)
            
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
        
        # Add user-specified extra protected positions (on chain H only)
        if chain == 'H' and mode != 'full_design':
            valid_extra = extra_protected & all_residues
            fixed.update(valid_extra)

        # Add chain-specific extra fixed positions (e.g., anchors)
        if chain in extra_fixed_by_chain:
            valid_extra = set(extra_fixed_by_chain[chain]) & all_residues
            fixed.update(valid_extra)
        
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
    parser.add_argument("--protected_positions", default="",
                        help="Comma-separated list of additional IMGT positions to protect (e.g., '23,72,73,104')")
    parser.add_argument("--extra_fixed_positions", default="",
                        help="Chain-specific PDB positions to always fix (e.g., 'H27,H30-33,L50')")
    parser.add_argument("--extra_fixed_positions_json", default="",
                        help="Optional JSON mapping PDB stem -> extra_fixed_positions spec")
    parser.add_argument("--cdr_positions", default="",
                        help="Chain-specific CDR positions to use (e.g., 'H26-33,L50-58'). Overrides HLT labels.")
    parser.add_argument("--cdr_positions_by_loop", default="",
                        help="Path to JSON mapping loop IDs (H1,H2,...) to positions.")
    parser.add_argument("--protect_fr_contacts", default="false",
                        help="Protect all FR contact hotspots from Zavrtanik 2018 (default: false)")
    parser.add_argument("--protect_disulfides", default="true",
                        help="Protect conserved disulfide cysteines (default: true)")
    parser.add_argument("--lock_target_chains", default="true",
                        help="Fix all non-antibody chains during redesign (default: true)")
    parser.add_argument("--lock_antibody_framework", default="true",
                        help="Keep non-CDR antibody framework fixed in CDR-focused modes (default: true)")
    
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    pdb_files = list(input_dir.glob("*.pdb"))
    
    # Parse arguments
    design_mode = args.design_mode
    selected_loops = [l.strip().upper() for l in args.design_loops.split(',')]
    protect_tetrad = args.protect_tetrad.lower() in ('true', '1', 'yes')
    antibody_chains = [c.strip().upper() for c in args.antibody_chains.split(',')]
    lock_target_chains = args.lock_target_chains.lower() in ('true', '1', 'yes')
    lock_antibody_framework = args.lock_antibody_framework.lower() in ('true', '1', 'yes')
    
    # Parse additional protection options
    protect_fr_contacts = args.protect_fr_contacts.lower() in ('true', '1', 'yes')
    protect_disulfides = args.protect_disulfides.lower() in ('true', '1', 'yes')
    
    # Build extra protected positions list (IMGT numbering)
    extra_protected_imgt = []
    extra_fixed_by_chain = parse_chain_position_spec(args.extra_fixed_positions)
    per_pdb_extra_fixed_positions = {}
    if args.extra_fixed_positions_json:
        try:
            with open(args.extra_fixed_positions_json, 'r') as f:
                raw_mapping = json.load(f)
            if isinstance(raw_mapping, dict):
                for pdb_name, spec in raw_mapping.items():
                    per_pdb_extra_fixed_positions[str(pdb_name)] = parse_chain_position_spec(str(spec or ""))
        except Exception as e:
            print(f"Warning: Failed to read extra_fixed_positions_json: {e}")
    cdr_override_by_chain = parse_chain_position_spec(args.cdr_positions)
    cdr_positions_by_loop = {}
    if args.cdr_positions_by_loop:
        try:
            with open(args.cdr_positions_by_loop, 'r') as f:
                cdr_positions_by_loop = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to read cdr_positions_by_loop: {e}")
    
    # Add user-specified positions
    if args.protected_positions.strip():
        for pos_str in args.protected_positions.split(','):
            try:
                extra_protected_imgt.append(int(pos_str.strip()))
            except ValueError:
                print(f"Warning: Invalid protected position '{pos_str}', skipping")
    
    # Add FR contact hotspots if requested
    if protect_fr_contacts:
        for region, positions in FR_CONTACT_POSITIONS.items():
            extra_protected_imgt.extend(positions)
    
    # Add disulfide cysteines if requested
    if protect_disulfides:
        extra_protected_imgt.extend(DISULFIDE_POSITIONS)
    
    extra_protected_imgt = list(set(extra_protected_imgt))  # Remove duplicates
    
    # Data structures for outputs
    mpnn_fixed_chains = {}
    fampnn_lines = ["pdb,fixed_seq_positions,fixed_sidechains\n"]

    print(f"Processing {len(pdb_files)} PDBs from {input_dir}...")
    print(f"Design mode: {design_mode}")
    print(f"Selected loops: {selected_loops}")
    print(f"Protect VHH tetrad: {protect_tetrad}")
    print(f"Antibody chains: {antibody_chains}")
    print(f"Lock target chains: {lock_target_chains}")
    print(f"Lock antibody framework: {lock_antibody_framework}")
    if extra_protected_imgt:
        print(f"Extra protected IMGT positions: {sorted(extra_protected_imgt)}")
    if extra_fixed_by_chain:
        print(f"Extra fixed positions by chain: {extra_fixed_by_chain}")
    if per_pdb_extra_fixed_positions:
        print(f"Per-PDB extra fixed positions loaded: {len(per_pdb_extra_fixed_positions)} entries")
    if cdr_override_by_chain:
        print(f"CDR override positions by chain: {cdr_override_by_chain}")
    if cdr_positions_by_loop:
        print(f"CDR override positions by loop: {cdr_positions_by_loop.keys()}")

    for pdb in pdb_files:
        pdb_name = pdb.stem
        chains_data = parse_pdb_chains(pdb)
        effective_extra_fixed_by_chain = merge_chain_position_maps(
            extra_fixed_by_chain,
            resolve_per_pdb_fixed_positions(pdb_name, per_pdb_extra_fixed_positions),
        )
        
        # Parse CDR labels from HLT format
        cdr_dict = parse_hlt_cdr_labels(pdb)
        loop_override = {k: list(map(int, v)) for k, v in cdr_positions_by_loop.items() if v} if cdr_positions_by_loop else {}
        if loop_override:
            cdr_dict = loop_override
        
        effective_antibody_chains = list(antibody_chains)
        override_chains = [chain for chain, residues in cdr_override_by_chain.items() if residues and chain in chains_data]
        if override_chains and not any(chain in chains_data for chain in effective_antibody_chains):
            # PPIFlow partial-flow backbones are often relabeled to A/B. If ANARCII
            # did not recover loop-by-loop labels, the chain-level CDR override from
            # IdentifyAnchorResidues is the best source of truth for which chain is
            # antibody and should remain designable.
            effective_antibody_chains = override_chains

        # Check if we found any CDR labels or override positions
        has_cdr_labels = any(len(v) > 0 for v in cdr_dict.values()) or bool(cdr_override_by_chain)
        
        if not has_cdr_labels:
            # Fallback: If no HLT labels, use heuristic (fix entire target chain)
            print(f"  {pdb_name}: No HLT CDR labels found, using chain-based fallback")
            chains_to_fix = [c for c in chains_data.keys() if c not in effective_antibody_chains] if lock_target_chains else []
            
            # For FAMPNN
            fampnn_constraints = []
            for chain in chains_to_fix:
                residues = chains_data[chain]
                ranges = get_ranges(residues)
                for r_start, r_end in ranges:
                    fampnn_constraints.append(f"{chain}{r_start}-{r_end}")

            # Add extra fixed positions (anchors) if provided
            for chain, residues in effective_extra_fixed_by_chain.items():
                if chain not in chains_data:
                    continue
                valid = set(residues) & set(chains_data[chain])
                for r_start, r_end in get_ranges(valid):
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
            
            # Convert extra protected IMGT positions to PDB positions
            # Using same offset calculation as tetrad
            if chain_h_residues and extra_protected_imgt:
                sorted_residues = sorted(chain_h_residues.keys())
                offset = sorted_residues[0] - 1 if sorted_residues else 0
                extra_protected_pdb = [pos + offset for pos in extra_protected_imgt 
                                       if (pos + offset) in chain_h_residues]
            else:
                extra_protected_pdb = []
        else:
            tetrad_positions = []
            extra_protected_pdb = []
        
        # Compute fixed residues based on mode
        fixed_residues = compute_fixed_residues(
            mode=design_mode,
            chains_data=chains_data,
            cdr_dict=cdr_dict,
            tetrad_positions=tetrad_positions,
            selected_loops=selected_loops,
            protect_tetrad=protect_tetrad,
            antibody_chains=effective_antibody_chains,
            extra_protected_positions=extra_protected_pdb,
            extra_fixed_by_chain=effective_extra_fixed_by_chain,
            cdr_override_by_chain=cdr_override_by_chain,
            lock_target_chains=lock_target_chains,
            lock_antibody_framework=lock_antibody_framework,
        )
        
        designable_counts = {}
        for chain in effective_antibody_chains:
            if chain not in chains_data:
                continue
            all_residues = set(chains_data[chain])
            fixed = set(fixed_residues.get(chain, []))
            designable_counts[chain] = len(all_residues - fixed)

        if design_mode in {"cdr_only", "cdr_selective"} and designable_counts and all(count == 0 for count in designable_counts.values()):
            raise RuntimeError(
                f"{pdb_name}: no antibody residues remain designable after constraint generation "
                f"(antibody_chains={effective_antibody_chains}, override_chains={sorted(override_chains)})"
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
        target_chains = [c for c in chains_data.keys() if c not in effective_antibody_chains]
        if target_chains:
            mpnn_fixed_chains[pdb_name] = target_chains
        
        # Report summary
        cdr_count = sum(len(v) for v in cdr_dict.values()) or sum(len(v) for v in cdr_override_by_chain.values())
        fixed_count = sum(len(v) for v in fixed_residues.values())
        print(
            f"  {pdb_name}: {cdr_count} CDR residues, {fixed_count} fixed residues, "
            f"designable={designable_counts}, tetrad: {tetrad_positions}"
        )

    # Write outputs
    with open(args.out_mpnn, 'w') as f:
        json.dump(mpnn_fixed_chains, f, indent=2)
    print(f"Wrote ProteinMPNN constraints to {args.out_mpnn}")

    with open(args.out_fampnn, 'w') as f:
        f.writelines(fampnn_lines)
    print(f"Wrote FAMPNN constraints to {args.out_fampnn}")


if __name__ == "__main__":
    main()

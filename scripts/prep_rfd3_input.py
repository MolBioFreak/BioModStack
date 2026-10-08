#!/usr/bin/env python3
"""
Convert RFdiffusion contig parameters to RFD3 JSON input format.

This script bridges the gap between ProteinDJ's RFD parameter style
and RFdiffusion3's JSON specification format.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Optional


def parse_contig_length(contigs: str) -> Optional[int]:
    """
    Extract length specification from RFD contig string.
    
    Examples:
        '[80-80]' -> 80
        '[50-100]' -> (50, 100) range
        '[A17-145/0 50-100]' -> 50-100 for designed region
    """
    # Match patterns like '50-100' or '80-80'
    length_pattern = r'(\d+)-(\d+)'
    matches = re.findall(length_pattern, contigs)
    
    if not matches:
        return None
    
    # Take the last match (usually the design length for binders)
    min_len, max_len = int(matches[-1][0]), int(matches[-1][1])
    
    if min_len == max_len:
        return min_len
    else:
        # Return as string for range
        return f"{min_len}-{max_len}"


def parse_chain_residues(contigs: str) -> dict:
    """
    Parse chain/residue specifications from contigs.
    
    Examples:
        '[A17-145/0 50-100]' -> {'A': [(17, 145)]}
    """
    conditioning = {}
    
    # Match patterns like 'A17-145' or 'B1-50'
    chain_pattern = r'([A-Z])(\d+)-(\d+)'
    matches = re.findall(chain_pattern, contigs)
    
    for chain, start, end in matches:
        if chain not in conditioning:
            conditioning[chain] = []
        conditioning[chain].append({
            'start': int(start),
            'end': int(end)
        })
    
    return conditioning


def build_rfd3_spec(
    mode: str,
    contigs: str,
    input_pdb: Optional[str] = None,
    hotspots: Optional[str] = None,
    num_designs: int = 1
) -> dict:
    """
    Build RFD3 JSON specification from RFD parameters.
    
    Args:
        mode: RFD mode (monomer_denovo, binder_denovo, etc.)
        contigs: RFD contig specification string
        input_pdb: Path to input PDB for conditional generation
        hotspots: Hotspot residue specification
        num_designs: Number of designs to generate
    
    Returns:
        Dictionary suitable for RFD3 JSON input
    """
    spec = {}
    
    # Mode-specific handling
    if 'denovo' in mode:
        # Unconditional or simple conditional
        length = parse_contig_length(contigs)
        if length:
            spec['length'] = length
    
    if input_pdb:
        spec['input'] = str(Path(input_pdb).absolute())
        
        # Parse chain conditioning from contigs
        chain_cond = parse_chain_residues(contigs)
        if chain_cond:
            spec['conditioning'] = {'chains': chain_cond}
    
    if hotspots:
        # Parse hotspot format '[A56,A115,A123]' -> list
        hotspot_pattern = r'([A-Z])(\d+)'
        matches = re.findall(hotspot_pattern, hotspots)
        if matches:
            spec['hotspots'] = [{'chain': c, 'residue': int(r)} for c, r in matches]
    
    return spec


def main():
    parser = argparse.ArgumentParser(
        description='Convert RFD parameters to RFD3 JSON input format'
    )
    parser.add_argument('--mode', required=True,
                        help='RFD mode (e.g., monomer_denovo, binder_denovo)')
    parser.add_argument('--contigs', required=True,
                        help='RFD contig specification string')
    parser.add_argument('--input-pdb', default=None,
                        help='Path to input PDB for conditional generation')
    parser.add_argument('--hotspots', default=None,
                        help='Hotspot residue specification')
    parser.add_argument('--num-designs', type=int, default=1,
                        help='Number of designs to generate')
    parser.add_argument('--design-startnum', type=int, default=0,
                        help='Starting design number')
    parser.add_argument('--output', required=True,
                        help='Output JSON file path')
    
    args = parser.parse_args()
    
    # Build specification
    spec = build_rfd3_spec(
        mode=args.mode,
        contigs=args.contigs,
        input_pdb=args.input_pdb,
        hotspots=args.hotspots,
        num_designs=args.num_designs
    )
    
    # Create design key name
    design_key = f"{args.mode}_{args.design_startnum}"
    
    # Write output
    output_data = {design_key: spec}
    
    with open(args.output, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print(f"Created RFD3 input: {args.output}")
    print(f"Specification: {json.dumps(output_data, indent=2)}")


if __name__ == '__main__':
    main()

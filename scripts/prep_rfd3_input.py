#!/usr/bin/env python3
"""
Convert RFdiffusion contig parameters to RFD3 JSON input format.

This script bridges the gap between BioModStack's RFD parameter style
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


def convert_legacy_contig_to_rfd3(contigs: str) -> str:
    """
    Convert legacy RFD contig format to RFD3 dialect 2 format.
    
    Legacy format: '[A17-145/0 50-100]' 
    RFD3 format:   '50-100,/0,A17-145'
    
    For monomer: '[50-100]' -> '50-100'
    For binder:  '[A17-145/0 50-100]' -> '50-100,/0,A17-145'
    """
    # Remove brackets
    contigs = contigs.strip('[]')
    
    # Check if this is a binder contig (has chain specification)
    chain_pattern = r'([A-Z])(\d+)-(\d+)/0\s+(\d+)-(\d+)'
    match = re.match(chain_pattern, contigs)
    
    if match:
        chain, start, end, len_min, len_max = match.groups()
        # RFD3 format: binder_length,/0,chain_residues
        return f"{len_min}-{len_max},/0,{chain}{start}-{end}"
    
    # Simple monomer format
    return contigs


def build_rfd3_spec(
    mode: str,
    contigs: str,
    input_pdb: Optional[str] = None,
    hotspots: Optional[str] = None,
    num_designs: int = 1
) -> dict:
    """
    Build RFD3 JSON specification from RFD parameters.
    
    Uses RFD3 dialect 2 format as documented in Foundry:
    https://github.com/RosettaCommons/foundry/blob/production/models/rfd3/docs/protein_binder_design.md
    
    Args:
        mode: RFD mode (monomer_denovo, binder_denovo, etc.)
        contigs: RFD contig specification string
        input_pdb: Path to input PDB for conditional generation
        hotspots: Hotspot residue specification
        num_designs: Number of designs to generate
    
    Returns:
        Dictionary suitable for RFD3 JSON input (dialect 2 format)
    """
    spec = {
        'dialect': 2,
    }
    
    # Convert legacy contig format to RFD3 format
    rfd3_contig = convert_legacy_contig_to_rfd3(contigs)
    spec['contig'] = rfd3_contig
    
    if input_pdb:
        spec['input'] = str(Path(input_pdb).absolute())
    
    if hotspots:
        # Parse hotspot format '[A56,A115,A123]' and convert to RFD3 select_hotspots
        # RFD3 wants: {"A56": "CA", "A115": "CA", ...} with atom names
        # Default to CA (alpha carbon) if no atoms specified
        hotspots = hotspots.strip('[]')
        hotspot_pattern = r'([A-Z])(\d+)'
        matches = re.findall(hotspot_pattern, hotspots)
        if matches:
            # Use CA (alpha carbon) as default atom for each residue
            spec['select_hotspots'] = {f"{chain}{res}": "CA" for chain, res in matches}
            # Use hotspots strategy when select_hotspots are provided
            if 'binder' in mode:
                spec['infer_ori_strategy'] = 'hotspots'
    elif 'binder' in mode:
        # For binder design without explicit hotspots, use centroid strategy
        # (hotspots strategy requires select_hotspots which we don't have)
        spec['infer_ori_strategy'] = 'centroid'
    
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

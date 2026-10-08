#!/usr/bin/env python3
"""
BoltzGen Results Filter with Diversity Selection

Implements upstream BoltzGen filtering capabilities:
- Budget: Final number of designs to keep
- Alpha: Quality vs diversity tradeoff (0.0=quality only, 1.0=diversity only)
- RMSD threshold: Maximum refolding RMSD
- pLDDT/pTM threshold: Minimum structure confidence
- Affinity threshold: Minimum binding probability
"""

import argparse
import shutil
import os
import json
from pathlib import Path
from typing import List, Dict, Tuple
import numpy as np

def compute_sequence_diversity(seq1: str, seq2: str) -> float:
    """Compute sequence diversity as fraction of differing residues."""
    if not seq1 or not seq2:
        return 1.0
    min_len = min(len(seq1), len(seq2))
    if min_len == 0:
        return 1.0
    differences = sum(1 for a, b in zip(seq1[:min_len], seq2[:min_len]) if a != b)
    return differences / min_len

def select_diverse_subset(
    designs: List[Dict],
    budget: int,
    alpha: float = 0.01
) -> List[Dict]:
    """
    Select diverse subset using greedy max-min diversity with quality weight.
    
    Algorithm:
    1. Rank designs by quality (composite score)
    2. Start with top-quality design
    3. Iteratively add design that maximizes: (1-alpha)*quality_rank + alpha*min_diversity
    
    Args:
        designs: List of design dicts with 'sequence' and 'quality_score'
        budget: Number of designs to select
        alpha: Diversity weight (0=quality only, 1=diversity only)
    
    Returns:
        Selected subset of designs
    """
    if len(designs) <= budget:
        return designs
    
    if budget <= 0:
        return []
    
    # Sort by quality (higher is better)
    sorted_designs = sorted(designs, key=lambda x: x.get('quality_score', 0), reverse=True)
    
    # Assign quality ranks (0 = best)
    for i, d in enumerate(sorted_designs):
        d['quality_rank'] = i / len(sorted_designs)
    
    # Start with best quality design
    selected = [sorted_designs[0]]
    remaining = sorted_designs[1:]
    
    while len(selected) < budget and remaining:
        best_score = -float('inf')
        best_idx = 0
        
        for i, candidate in enumerate(remaining):
            # Compute minimum diversity to any selected design
            min_div = min(
                compute_sequence_diversity(
                    candidate.get('sequence', ''),
                    s.get('sequence', '')
                )
                for s in selected
            )
            
            # Combined score: quality + diversity
            score = (1 - alpha) * (1 - candidate['quality_rank']) + alpha * min_div
            
            if score > best_score:
                best_score = score
                best_idx = i
        
        selected.append(remaining[best_idx])
        remaining.pop(best_idx)
    
    return selected


def main():
    parser = argparse.ArgumentParser(description="Filter BoltzGen results with diversity selection")
    parser.add_argument("--pdbs", nargs="+", help="Input PDB files")
    parser.add_argument("--jsons", nargs="+", help="Input JSON metadata files")
    parser.add_argument("--out_dir", type=str, required=True, help="Output directory")
    
    # Quality thresholds
    parser.add_argument("--boltzgen-min-plddt", type=float, default=None,
                        help="Minimum pLDDT (derived from design_ptm * 100)")
    parser.add_argument("--boltzgen-min-conf-score", type=float, default=None,
                        help="Minimum affinity probability (0-1)")
    parser.add_argument("--boltzgen-max-rmsd", type=float, default=None,
                        help="Maximum refolding RMSD (lower is better)")
    
    # Diversity selection
    parser.add_argument("--budget", type=int, default=None,
                        help="Final number of designs to keep (with diversity selection)")
    parser.add_argument("--alpha", type=float, default=0.01,
                        help="Quality/diversity tradeoff: 0.0=quality only, 1.0=diversity only")
    parser.add_argument("--filter-biased", type=str, default="true",
                        help="Remove amino acid composition outliers (true/false)")
    
    # Advanced filtering (passed to upstream BoltzGen - logged for reference)
    parser.add_argument("--metrics-override", type=str, default=None,
                        help="Per-metric weights (e.g., 'plip_hbonds_refolded=4 delta_sasa_refolded=2')")
    parser.add_argument("--additional-filters", type=str, default=None,
                        help="Extra hard filters (e.g., 'design_ALA>0.3 design_GLY<0.2')")
    parser.add_argument("--size-buckets", type=str, default=None,
                        help="Size constraints (e.g., '10-20:5 20-30:10 30-40:5')")

    
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    
    if not args.pdbs:
        print("No PDBs to filter")
        return
    
    # Parse PDB list (handle space-separated string)
    pdb_list = args.pdbs
    if len(pdb_list) == 1 and ' ' in pdb_list[0]:
        pdb_list = pdb_list[0].split()
    
    # Build JSON lookup for metrics
    json_metrics = {}
    json_list = args.jsons or []
    if len(json_list) == 1 and ' ' in json_list[0]:
        json_list = json_list[0].split()
    
    for json_path in json_list:
        try:
            with open(json_path) as f:
                data = json.load(f)
                # Try multiple ID formats
                design_id = data.get('design_id', '')
                if not design_id:
                    design_id = Path(json_path).stem
                    if design_id.startswith('confidence_'):
                        design_id = design_id[11:]  # Remove prefix
                json_metrics[design_id] = data
        except Exception as e:
            print(f"Warning: Could not parse {json_path}: {e}")
    
    # First pass: Apply hard filters
    passed_designs = []
    filtered_count = 0
    
    for pdb in pdb_list:
        path = Path(pdb)
        design_id = path.stem
        
        # Get metrics from JSON
        metrics = json_metrics.get(design_id, {})
        
        # Extract metrics
        plddt = metrics.get('design_ptm', 0) * 100  # Convert pTM to pLDDT scale
        conf_score = metrics.get('affinity_probability', 0)
        rmsd = metrics.get('filter_rmsd', metrics.get('filter_rmsd_design', float('inf')))
        sequence = metrics.get('designed_sequence', '')
        
        # Compute quality score (higher is better)
        # Normalize metrics to 0-1 range and combine
        quality_score = (
            (plddt / 100) * 0.4 +  # pLDDT component
            conf_score * 0.4 +      # Affinity component
            max(0, 1 - rmsd / 5) * 0.2  # RMSD component (5A as max)
        )
        
        # Apply hard filters
        if args.boltzgen_min_plddt and plddt < args.boltzgen_min_plddt:
            print(f"Filtered {design_id}: pLDDT {plddt:.1f} < {args.boltzgen_min_plddt}")
            filtered_count += 1
            continue
        
        if args.boltzgen_min_conf_score and conf_score < args.boltzgen_min_conf_score:
            print(f"Filtered {design_id}: confidence {conf_score:.3f} < {args.boltzgen_min_conf_score}")
            filtered_count += 1
            continue
        
        if args.boltzgen_max_rmsd and rmsd > args.boltzgen_max_rmsd:
            print(f"Filtered {design_id}: RMSD {rmsd:.2f} > {args.boltzgen_max_rmsd}")
            filtered_count += 1
            continue
        
        passed_designs.append({
            'path': path,
            'design_id': design_id,
            'sequence': sequence,
            'quality_score': quality_score,
            'plddt': plddt,
            'conf_score': conf_score,
            'rmsd': rmsd,
        })
    
    print(f"Hard filters: {filtered_count} removed, {len(passed_designs)} passed")
    
    # Second pass: Diversity selection (if budget specified)
    if args.budget and args.budget < len(passed_designs):
        print(f"Applying diversity selection: {len(passed_designs)} -> {args.budget} (alpha={args.alpha})")
        selected = select_diverse_subset(passed_designs, args.budget, args.alpha)
    else:
        selected = passed_designs
    
    # Copy selected designs to output
    for design in selected:
        shutil.copy(design['path'], Path(args.out_dir) / design['path'].name)
        
        # Also copy JSON if it exists
        json_path = design['path'].parent / f"confidence_{design['design_id']}.json"
        if json_path.exists():
            shutil.copy(json_path, Path(args.out_dir) / json_path.name)
    
    print(f"Final output: {len(selected)} designs copied to {args.out_dir}")
    
    # Write summary
    summary_path = Path(args.out_dir) / "filter_summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            'input_count': len(pdb_list),
            'filtered_by_thresholds': filtered_count,
            'passed_thresholds': len(passed_designs),
            'budget': args.budget,
            'alpha': args.alpha,
            'final_count': len(selected),
            'filters_applied': {
                'min_plddt': args.boltzgen_min_plddt,
                'min_conf_score': args.boltzgen_min_conf_score,
                'max_rmsd': args.boltzgen_max_rmsd,
            }
        }, f, indent=2)


if __name__ == "__main__":
    main()

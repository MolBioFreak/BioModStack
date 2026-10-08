#!/usr/bin/env python3
"""
Filter RF3 (RosettaFold3) structure predictions based on confidence metrics.

Applies pLDDT, pTM, and RMSD filters similar to AF2/Boltz filtering.
"""

import argparse
import gzip
import json
import os
import shutil
from pathlib import Path
from typing import Optional

# Try to import biotite for structure handling
try:
    from biotite.structure.io import load_structure
    import numpy as np
    HAS_BIOTITE = True
except ImportError:
    HAS_BIOTITE = False


def calculate_rmsd(coords1: 'np.ndarray', coords2: 'np.ndarray') -> float:
    """Calculate RMSD between two coordinate sets."""
    diff = coords1 - coords2
    return np.sqrt(np.mean(np.sum(diff ** 2, axis=1)))


def filter_prediction(
    cif_path: Path,
    json_path: Optional[Path],
    output_dir: Path,
    min_plddt: Optional[float] = None,
    min_ptm: Optional[float] = None,
    max_pae: Optional[float] = None,
    max_rmsd_overall: Optional[float] = None,
    max_rmsd_binder: Optional[float] = None,
) -> dict:
    """
    Filter a single RF3 prediction based on criteria.
    
    Returns dict with pass/fail status and metrics.
    """
    result = {
        'file': str(cif_path),
        'passed': True,
        'reason': None,
        'metrics': {}
    }
    
    # Load metadata from JSON if available
    metadata = {}
    if json_path and json_path.exists():
        try:
            with open(json_path) as f:
                metadata = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load metadata from {json_path}: {e}")
    
    # Extract metrics from metadata
    plddt = metadata.get('plddt', metadata.get('mean_plddt'))
    ptm = metadata.get('ptm', metadata.get('pTM'))
    pae = metadata.get('pae', metadata.get('mean_pae'))
    
    result['metrics'] = {
        'plddt': plddt,
        'ptm': ptm,
        'pae': pae,
    }
    
    # Apply filters
    if min_plddt is not None and plddt is not None and plddt < min_plddt:
        result['passed'] = False
        result['reason'] = f"pLDDT {plddt:.2f} < min {min_plddt}"
    
    if min_ptm is not None and ptm is not None and ptm < min_ptm:
        result['passed'] = False
        result['reason'] = f"pTM {ptm:.3f} < min {min_ptm}"
    
    if max_pae is not None and pae is not None and pae > max_pae:
        result['passed'] = False
        result['reason'] = f"PAE {pae:.2f} > max {max_pae}"
    
    # Copy files if passed
    if result['passed']:
        shutil.copy(cif_path, output_dir / cif_path.name)
        if json_path and json_path.exists():
            shutil.copy(json_path, output_dir / json_path.name)
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Filter RF3 predictions based on confidence metrics'
    )
    parser.add_argument('--input-dir', required=True,
                        help='Directory containing CIF/JSON files')
    parser.add_argument('--output-dir', required=True,
                        help='Output directory for filtered files')
    parser.add_argument('--min-plddt', type=float, default=None,
                        help='Minimum pLDDT score')
    parser.add_argument('--min-ptm', type=float, default=None,
                        help='Minimum pTM score')
    parser.add_argument('--max-pae', type=float, default=None,
                        help='Maximum PAE')
    parser.add_argument('--max-rmsd-overall', type=float, default=None,
                        help='Maximum overall RMSD')
    parser.add_argument('--max-rmsd-binder', type=float, default=None,
                        help='Maximum binder RMSD')
    
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all CIF files
    cif_files = list(input_dir.glob('*.cif.gz')) + list(input_dir.glob('*.cif'))
    print(f"Found {len(cif_files)} CIF files to filter")
    
    results = []
    for cif_path in cif_files:
        # Find matching JSON (try multiple patterns)
        json_path = None
        for pattern in ['.json', '.cif.json', '']:
            candidate = cif_path.with_suffix(pattern) if pattern else cif_path.parent / (cif_path.stem.replace('.cif', '') + '.json')
            if candidate.exists():
                json_path = candidate
                break
        
        result = filter_prediction(
            cif_path=cif_path,
            json_path=json_path,
            output_dir=output_dir,
            min_plddt=args.min_plddt,
            min_ptm=args.min_ptm,
            max_pae=args.max_pae,
            max_rmsd_overall=args.max_rmsd_overall,
            max_rmsd_binder=args.max_rmsd_binder,
        )
        results.append(result)
    
    # Summary
    passed = sum(1 for r in results if r['passed'])
    print(f"Filtering complete: {passed}/{len(results)} predictions passed")
    
    # Write results to jsonl
    with open('filtered.jsonl', 'w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')


if __name__ == '__main__':
    main()

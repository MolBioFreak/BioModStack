#!/usr/bin/env python3
"""
filter_unidock.py - Filter Uni-Dock poses by affinity and convert to PDB

Features:
- Filters poses based on affinity threshold
- Converts PDBQT to PDB format for visualization
- Generates scores.json for API consumption

Usage:
    python filter_unidock.py --poses_dir ./poses --scores_csv scores.csv \
        --affinity_threshold -7.0 --out_dir ./filtered
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Dict


def pdbqt_to_pdb(pdbqt_content: str) -> str:
    """
    Convert PDBQT format to PDB format.
    
    PDBQT has extra columns for partial charges and atom types that
    need to be stripped for standard PDB format.
    """
    pdb_lines = []
    
    for line in pdbqt_content.split('\n'):
        # Skip Uni-Dock specific remarks
        if line.startswith('REMARK VINA') or line.startswith('REMARK INTER'):
            continue
        
        # Convert ATOM/HETATM lines
        if line.startswith('ATOM') or line.startswith('HETATM'):
            # PDBQT format: 
            #   columns 1-66 same as PDB
            #   column 67-76: partial charge
            #   column 77-78: atom type
            # Truncate to standard PDB (first 66 chars)
            pdb_line = line[:66] if len(line) > 66 else line
            # Pad to proper length if needed
            pdb_lines.append(pdb_line.ljust(66))
        elif line.startswith('MODEL') or line.startswith('ENDMDL') or line.startswith('END'):
            pdb_lines.append(line)
        elif line.startswith('TER'):
            pdb_lines.append(line[:66] if len(line) > 66 else line)
        elif line.startswith('CONECT'):
            pdb_lines.append(line)
    
    return '\n'.join(pdb_lines)


def extract_pose_from_pdbqt(pdbqt_path: Path, pose_number: int = 1) -> str:
    """
    Extract a specific pose (MODEL) from a multi-model PDBQT file.
    
    Args:
        pdbqt_path: Path to PDBQT file
        pose_number: 1-indexed pose number to extract
        
    Returns:
        PDBQT content for the specified pose
    """
    content = pdbqt_path.read_text()
    
    # If no MODEL/ENDMDL markers, return entire content
    if 'MODEL' not in content:
        return content
    
    models = []
    current_model = []
    in_model = False
    
    for line in content.split('\n'):
        if line.startswith('MODEL'):
            in_model = True
            current_model = [line]
        elif line.startswith('ENDMDL'):
            current_model.append(line)
            models.append('\n'.join(current_model))
            in_model = False
            current_model = []
        elif in_model:
            current_model.append(line)
    
    if pose_number <= len(models):
        return models[pose_number - 1]
    else:
        return content  # Fallback to entire file


def main():
    parser = argparse.ArgumentParser(description='Filter and convert Uni-Dock poses')
    parser.add_argument('--poses_dir', required=True, help='Directory with PDBQT poses')
    parser.add_argument('--scores_csv', required=True, help='Scores CSV from parse_unidock_scores.py')
    parser.add_argument('--affinity_threshold', type=float, default=-7.0,
                        help='Affinity threshold in kcal/mol (default: -7.0)')
    parser.add_argument('--out_dir', default='.', help='Output directory')
    parser.add_argument('--max_poses', type=int, default=50,
                        help='Maximum poses to output (default: 50)')
    
    args = parser.parse_args()
    
    poses_dir = Path(args.poses_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Read scores
    scores_file = Path(args.scores_csv)
    if not scores_file.exists():
        print(f"ERROR: Scores file not found: {scores_file}", file=sys.stderr)
        sys.exit(1)
    
    poses = []
    with open(scores_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            poses.append({
                'rank': int(row['rank']),
                'ligand': row['ligand'],
                'pose': int(row.get('pose', 1)),
                'affinity_kcal_mol': float(row['affinity_kcal_mol']),
                'file': row['file'],
            })
    
    print(f"Read {len(poses)} poses from {scores_file}")
    
    # Filter by affinity threshold
    filtered = [p for p in poses if p['affinity_kcal_mol'] <= args.affinity_threshold]
    print(f"Filtered to {len(filtered)} poses with affinity <= {args.affinity_threshold} kcal/mol")
    
    # Limit number of poses
    if len(filtered) > args.max_poses:
        filtered = filtered[:args.max_poses]
        print(f"Limited to top {args.max_poses} poses")
    
    # Convert to PDB and update records
    output_records = []
    for pose in filtered:
        pdbqt_file = poses_dir / pose['file']
        if not pdbqt_file.exists():
            # Try parent directory
            pdbqt_file = poses_dir.parent / pose['file']
        
        if not pdbqt_file.exists():
            print(f"WARNING: PDBQT file not found: {pose['file']}", file=sys.stderr)
            continue
        
        # Extract specific pose
        pdbqt_content = extract_pose_from_pdbqt(pdbqt_file, pose['pose'])
        
        # Convert to PDB
        pdb_content = pdbqt_to_pdb(pdbqt_content)
        
        # Write PDB file
        pdb_name = f"{pose['ligand']}_pose{pose['pose']}_rank{pose['rank']}.pdb"
        pdb_path = out_dir / pdb_name
        pdb_path.write_text(pdb_content)
        
        output_records.append({
            'pdb_file': pdb_name,
            'ligand': pose['ligand'],
            'pose': pose['pose'],
            'rank': pose['rank'],
            'affinity_kcal_mol': pose['affinity_kcal_mol'],
        })
    
    print(f"Wrote {len(output_records)} PDB files to {out_dir}")
    
    # Write scores.json for API
    scores_json = out_dir / 'scores.json'
    scores_json.write_text(json.dumps(output_records, indent=2))
    print(f"Wrote scores to {scores_json}")


if __name__ == '__main__':
    main()

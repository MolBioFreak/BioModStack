#!/usr/bin/env python3
"""
parse_unidock_scores.py - Extract binding affinities from Uni-Dock output PDBQT files

Uni-Dock writes affinity scores in REMARK lines of output PDBQT files.
This script parses all output files and generates a CSV summary.

Usage:
    python parse_unidock_scores.py poses_dir/ > scores.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path


def parse_pdbqt_affinity(pdbqt_path: Path) -> list:
    """
    Parse affinity scores from Uni-Dock PDBQT output file.
    
    Uni-Dock writes multiple poses in a single file with REMARK lines like:
    REMARK VINA RESULT:    -7.3      0.000      0.000
    
    Returns:
        List of dicts with pose info
    """
    poses = []
    current_pose = {'file': pdbqt_path.name, 'ligand': pdbqt_path.stem}
    pose_num = 0
    
    with open(pdbqt_path, 'r') as f:
        for line in f:
            # Parse affinity from REMARK line
            if 'VINA RESULT' in line or 'INTER + INTRA' in line:
                match = re.search(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', line)
                if match:
                    affinity = float(match.group(1))
                    current_pose['affinity_kcal_mol'] = affinity
            
            # New model (pose) marker
            elif line.startswith('MODEL'):
                pose_num += 1
                current_pose = {
                    'file': pdbqt_path.name,
                    'ligand': pdbqt_path.stem,
                    'pose': pose_num,
                }
            
            # End of model
            elif line.startswith('ENDMDL'):
                if 'affinity_kcal_mol' in current_pose:
                    poses.append(current_pose.copy())
    
    # Handle single-model files
    if not poses and 'affinity_kcal_mol' in current_pose:
        current_pose['pose'] = 1
        poses.append(current_pose)
    
    return poses


def main():
    parser = argparse.ArgumentParser(description='Parse Uni-Dock output scores')
    parser.add_argument('poses_dir', help='Directory containing output PDBQT files')
    parser.add_argument('--output', '-o', help='Output CSV file (default: stdout)')
    
    args = parser.parse_args()
    
    poses_dir = Path(args.poses_dir)
    if not poses_dir.exists():
        print(f"ERROR: Directory not found: {poses_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Parse all PDBQT files
    all_poses = []
    for pdbqt_file in sorted(poses_dir.glob('*.pdbqt')):
        poses = parse_pdbqt_affinity(pdbqt_file)
        all_poses.extend(poses)
    
    if not all_poses:
        print("WARNING: No poses found", file=sys.stderr)
    
    # Sort by affinity (best = most negative first)
    all_poses.sort(key=lambda x: x.get('affinity_kcal_mol', 0))
    
    # Add rank
    for i, pose in enumerate(all_poses):
        pose['rank'] = i + 1
    
    # Write CSV
    out_file = open(args.output, 'w') if args.output else sys.stdout
    
    fieldnames = ['rank', 'ligand', 'pose', 'affinity_kcal_mol', 'file']
    writer = csv.DictWriter(out_file, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(all_poses)
    
    if args.output:
        out_file.close()
        print(f"Wrote {len(all_poses)} poses to {args.output}", file=sys.stderr)
    else:
        print(f"# Parsed {len(all_poses)} poses", file=sys.stderr)


if __name__ == '__main__':
    main()

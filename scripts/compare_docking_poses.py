#!/usr/bin/env python3
"""
compare_docking_poses.py - Compare docking poses between DiffDock and Uni-Dock

Features:
- Computes RMSD between poses from different engines
- Identifies agreements (poses with RMSD below threshold)
- Generates consensus scoring combining both methods
- Outputs comparison.json for API consumption

Usage:
    python compare_docking_poses.py \
        --diffdock_dir ./diffdock/results \
        --unidock_dir ./unidock/filtered \
        --rmsd_threshold 2.0 \
        --output comparison.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    HAS_RDKIT = True
except ImportError:
    HAS_RDKIT = False

try:
    from Bio.PDB import PDBParser
    HAS_BIOPYTHON = True
except ImportError:
    HAS_BIOPYTHON = False


def parse_coordinates_from_sdf(sdf_path: Path) -> Optional[np.ndarray]:
    """Extract heavy atom coordinates from SDF file using RDKit."""
    if not HAS_RDKIT:
        return None
    
    try:
        mol = Chem.MolFromMolFile(str(sdf_path), removeHs=True)
        if mol is None:
            return None
        conf = mol.GetConformer()
        coords = np.array([conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())])
        return coords
    except Exception as e:
        print(f"Warning: Failed to parse SDF {sdf_path}: {e}", file=sys.stderr)
        return None


def parse_coordinates_from_pdb(pdb_path: Path) -> Optional[np.ndarray]:
    """Extract heavy atom coordinates from PDB file using BioPython."""
    if not HAS_BIOPYTHON:
        # Fallback to simple parsing
        coords = []
        try:
            with open(pdb_path, 'r') as f:
                for line in f:
                    if line.startswith(('ATOM', 'HETATM')):
                        # Skip hydrogens
                        atom_name = line[12:16].strip()
                        if atom_name.startswith('H'):
                            continue
                        x = float(line[30:38])
                        y = float(line[38:46])
                        z = float(line[46:54])
                        coords.append([x, y, z])
            return np.array(coords) if coords else None
        except Exception as e:
            print(f"Warning: Failed to parse PDB {pdb_path}: {e}", file=sys.stderr)
            return None
    
    try:
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure('pose', str(pdb_path))
        coords = []
        for atom in structure.get_atoms():
            if atom.element != 'H':  # Skip hydrogens
                coords.append(atom.coord)
        return np.array(coords) if coords else None
    except Exception as e:
        print(f"Warning: Failed to parse PDB {pdb_path}: {e}", file=sys.stderr)
        return None


def compute_rmsd(coords1: np.ndarray, coords2: np.ndarray) -> float:
    """
    Compute RMSD between two coordinate sets after optimal superposition.
    Uses Kabsch algorithm for alignment.
    """
    if coords1.shape != coords2.shape:
        # Different number of atoms - can't directly compare
        return float('inf')
    
    if len(coords1) == 0:
        return float('inf')
    
    # Center both structures
    c1 = coords1 - coords1.mean(axis=0)
    c2 = coords2 - coords2.mean(axis=0)
    
    # Kabsch algorithm for optimal rotation
    H = c1.T @ c2
    U, S, Vt = np.linalg.svd(H)
    
    # Correct for reflection
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    Vt[-1, :] *= d
    
    # Optimal rotation
    R = Vt.T @ U.T
    
    # Apply rotation and compute RMSD
    c1_aligned = c1 @ R
    rmsd = np.sqrt(((c1_aligned - c2) ** 2).sum(axis=1).mean())
    
    return rmsd


def load_diffdock_poses(results_dir: Path) -> List[Dict]:
    """Load DiffDock poses with confidence scores."""
    poses = []
    
    for sdf_file in results_dir.rglob("*.sdf"):
        # Parse confidence from filename
        confidence = None
        match = re.search(r'confidence(-?\d+\.?\d*)', sdf_file.name)
        if match:
            confidence = float(match.group(1))
        
        # Parse rank
        rank = None
        rank_match = re.search(r'rank(\d+)', sdf_file.name)
        if rank_match:
            rank = int(rank_match.group(1))
        
        coords = parse_coordinates_from_sdf(sdf_file)
        
        poses.append({
            "engine": "diffdock",
            "file": str(sdf_file),
            "filename": sdf_file.name,
            "confidence": confidence,
            "rank": rank,
            "coords": coords,
        })
    
    # Sort by confidence (higher = better for DiffDock, but negative values so sort ascending)
    poses.sort(key=lambda x: x.get('rank') if x.get('rank') is not None else 999)
    return poses


def load_unidock_poses(filtered_dir: Path) -> List[Dict]:
    """Load Uni-Dock poses with affinity scores."""
    poses = []
    
    # Try to load scores.json
    scores_file = filtered_dir / "scores.json"
    score_map = {}
    if scores_file.exists():
        try:
            scores_data = json.loads(scores_file.read_text())
            for entry in scores_data:
                score_map[entry['pdb_file']] = entry
        except Exception as e:
            print(f"Warning: Failed to load scores.json: {e}", file=sys.stderr)
    
    for pdb_file in filtered_dir.glob("*.pdb"):
        entry = score_map.get(pdb_file.name, {})
        coords = parse_coordinates_from_pdb(pdb_file)
        
        poses.append({
            "engine": "unidock",
            "file": str(pdb_file),
            "filename": pdb_file.name,
            "affinity": entry.get('affinity_kcal_mol'),
            "rank": entry.get('rank'),
            "coords": coords,
        })
    
    # Sort by affinity (more negative = better)
    poses.sort(key=lambda x: x.get('affinity', 0) if x.get('affinity') is not None else 0)
    return poses


def compare_poses(diffdock_poses: List[Dict], unidock_poses: List[Dict],
                  rmsd_threshold: float) -> Dict[str, Any]:
    """
    Compare poses between engines and find best matches.
    """
    comparisons = []
    consensus = []
    
    for dd_pose in diffdock_poses:
        best_match = None
        best_rmsd = float('inf')
        
        if dd_pose['coords'] is None:
            comparisons.append({
                "diffdock_file": dd_pose['filename'],
                "diffdock_confidence": dd_pose['confidence'],
                "diffdock_rank": dd_pose['rank'],
                "unidock_file": None,
                "unidock_affinity": None,
                "rmsd": None,
                "agreement": False,
                "error": "Could not parse DiffDock coordinates"
            })
            continue
        
        for ud_pose in unidock_poses:
            if ud_pose['coords'] is None:
                continue
            
            # Compute RMSD
            rmsd = compute_rmsd(dd_pose['coords'], ud_pose['coords'])
            
            if rmsd < best_rmsd:
                best_rmsd = rmsd
                best_match = ud_pose
        
        comparison = {
            "diffdock_file": dd_pose['filename'],
            "diffdock_confidence": dd_pose['confidence'],
            "diffdock_rank": dd_pose['rank'],
            "unidock_file": best_match['filename'] if best_match else None,
            "unidock_affinity": best_match.get('affinity') if best_match else None,
            "unidock_rank": best_match.get('rank') if best_match else None,
            "rmsd": round(best_rmsd, 3) if best_rmsd != float('inf') else None,
            "agreement": best_rmsd <= rmsd_threshold if best_rmsd != float('inf') else False,
        }
        comparisons.append(comparison)
        
        # Add to consensus if agreement
        if comparison['agreement'] and best_match:
            # Compute consensus score (normalize and combine)
            # DiffDock confidence: typically -10 to +5, map to 0-1
            norm_conf = (dd_pose['confidence'] + 10) / 15 if dd_pose['confidence'] else 0.5
            # Uni-Dock affinity: typically -15 to 0, more negative = better
            norm_aff = (-best_match.get('affinity', -7)) / 15 if best_match.get('affinity') else 0.5
            consensus_score = (norm_conf + norm_aff) / 2
            
            consensus.append({
                **comparison,
                "consensus_score": round(consensus_score, 3)
            })
    
    # Sort consensus by combined score
    consensus.sort(key=lambda x: x.get('consensus_score', 0), reverse=True)
    
    total_dd = len(diffdock_poses)
    total_ud = len(unidock_poses)
    agreements = len(consensus)
    
    return {
        "comparisons": comparisons,
        "consensus_poses": consensus,
        "summary": {
            "total_diffdock_poses": total_dd,
            "total_unidock_poses": total_ud,
            "total_agreements": agreements,
            "agreement_rate": round(agreements / max(total_dd, 1), 3),
            "rmsd_threshold": rmsd_threshold,
        }
    }


def main():
    parser = argparse.ArgumentParser(description='Compare docking poses between engines')
    parser.add_argument('--diffdock_dir', required=True, 
                        help='Directory containing DiffDock SDF results')
    parser.add_argument('--unidock_dir', required=True,
                        help='Directory containing Uni-Dock filtered PDB files')
    parser.add_argument('--rmsd_threshold', type=float, default=2.0,
                        help='RMSD threshold for agreement (default: 2.0 Å)')
    parser.add_argument('--output', required=True,
                        help='Output JSON file for comparison results')
    parser.add_argument('--consensus_dir', default=None,
                        help='Directory to copy consensus poses to (optional)')
    
    args = parser.parse_args()
    
    diffdock_dir = Path(args.diffdock_dir)
    unidock_dir = Path(args.unidock_dir)
    
    if not diffdock_dir.exists():
        print(f"Warning: DiffDock directory not found: {diffdock_dir}", file=sys.stderr)
        diffdock_poses = []
    else:
        diffdock_poses = load_diffdock_poses(diffdock_dir)
        print(f"Loaded {len(diffdock_poses)} DiffDock poses")
    
    if not unidock_dir.exists():
        print(f"Warning: Uni-Dock directory not found: {unidock_dir}", file=sys.stderr)
        unidock_poses = []
    else:
        unidock_poses = load_unidock_poses(unidock_dir)
        print(f"Loaded {len(unidock_poses)} Uni-Dock poses")
    
    # Compare poses
    comparison = compare_poses(diffdock_poses, unidock_poses, args.rmsd_threshold)
    
    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(comparison, indent=2))
    
    print(f"\nComparison complete:")
    print(f"  Agreements: {comparison['summary']['total_agreements']} "
          f"({comparison['summary']['agreement_rate']:.1%})")
    print(f"  Output: {output_path}")
    
    # Copy consensus poses if requested
    if args.consensus_dir and comparison['consensus_poses']:
        import shutil
        consensus_dir = Path(args.consensus_dir)
        consensus_dir.mkdir(parents=True, exist_ok=True)
        
        for pose in comparison['consensus_poses']:
            # Copy both the DiffDock and Uni-Dock files
            for key in ['diffdock_file', 'unidock_file']:
                if key in pose and pose[key]:
                    src = diffdock_dir / pose[key] if 'diffdock' in key else unidock_dir / pose[key]
                    if src.exists():
                        shutil.copy(src, consensus_dir / src.name)
        
        print(f"  Consensus poses copied to: {consensus_dir}")


if __name__ == '__main__':
    main()

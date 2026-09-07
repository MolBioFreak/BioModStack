"""
Structure metrics calculation.

Calculates secondary structure, radius of gyration, and other
structural metrics using biotite.
"""

import numpy as np
from pathlib import Path
from typing import Dict, Optional, Tuple

# Try to import biotite
try:
    import biotite.structure as struc
    HAS_BIOTITE = True
except ImportError:
    HAS_BIOTITE = False


def calculate_radius_of_gyration(coords: np.ndarray) -> float:
    """
    Calculate radius of gyration from coordinates.
    
    Args:
        coords: Nx3 numpy array of atomic coordinates
        
    Returns:
        Radius of gyration in Angstroms
    """
    if coords is None or len(coords) == 0:
        return 0.0
    
    centroid = coords.mean(axis=0)
    distances_sq = np.sum((coords - centroid) ** 2, axis=1)
    return float(np.sqrt(np.mean(distances_sq)))


def calculate_secondary_structure(structure: 'struc.AtomArray', core_protein_scientific_contract=None) -> Dict[str, Optional[int]]:
    """
    Calculate secondary structure content using DSSP-like algorithm.
    
    Uses biotite's annotate_sse function which implements a simplified
    DSSP algorithm based on hydrogen bonding patterns.
    
    Args:
        structure: Biotite AtomArray
        
    Returns:
        Dict with 'helices', 'strands', 'total_ss' counts
    """
    unavailable: Dict[str, Optional[int]] = dict.fromkeys(('helices', 'strands', 'total_ss'), None if core_protein_scientific_contract == 1 else 0)
    if not HAS_BIOTITE:
        return unavailable
    
    if structure is None:
        return unavailable
    
    try:
        # Get SSE (Secondary Structure Elements) annotation
        # 'a' = alpha helix, 'b' = beta strand, 'c' = coil
        sse = struc.annotate_sse(structure)
        
        # Count contiguous secondary structure elements
        helix_count = 0
        strand_count = 0
        in_helix = False
        in_strand = False
        
        for ss in sse:
            if ss == 'a':  # Alpha helix
                if not in_helix:
                    helix_count += 1
                    in_helix = True
                in_strand = False
            elif ss == 'b':  # Beta strand
                if not in_strand:
                    strand_count += 1
                    in_strand = True
                in_helix = False
            else:  # Coil or other
                in_helix = False
                in_strand = False
        
        return {
            'helices': helix_count,
            'strands': strand_count,
            'total_ss': helix_count + strand_count
        }
    except Exception as e:
        print(f"Warning: SS calculation failed: {e}")
        return unavailable


def calculate_backbone_metrics(structure: 'struc.AtomArray', core_protein_scientific_contract=None) -> Dict[str, float]:
    """
    Calculate backbone-related metrics for a structure.
    
    Args:
        structure: Biotite AtomArray
        
    Returns:
        Dict with backbone metrics
    """
    if structure is None:
        return {}
    
    metrics = {}
    
    # Get backbone atoms
    try:
        from biotite.structure import filter_peptide_backbone
        backbone_mask = filter_peptide_backbone(structure)
    except ImportError:
        from biotite.structure import filter_backbone
        backbone_mask = filter_backbone(structure)
    
    backbone = structure[backbone_mask]
    
    # Calculate RoG
    if len(backbone) > 0:
        metrics['rog'] = calculate_radius_of_gyration(backbone.coord)
    
    # Calculate SS
    ss_metrics = calculate_secondary_structure(structure, core_protein_scientific_contract)
    metrics.update(ss_metrics)
    
    return metrics


def extract_confidence_metrics(metadata: dict, core_protein_scientific_contract=None) -> Dict[str, Optional[float]]:
    """
    Extract confidence metrics from prediction metadata.
    
    Handles different naming conventions from AF2, Boltz, RF3.
    
    Args:
        metadata: Dict from JSON metadata file
        
    Returns:
        Normalized dict with 'plddt', 'ptm', 'pae' keys
    """
    if core_protein_scientific_contract == 1:
        aliases = {'plddt': ('plddt', 'mean_plddt', 'pLDDT'), 'ptm': ('ptm', 'pTM'), 'pae': ('pae', 'mean_pae', 'PAE'), 'rmsd': ('rmsd', 'rmsd_overall'), 'rmsd_binder': ('rmsd_binder', 'binder_rmsd')}
        result = {key: next((metadata[k] for k in names if k in metadata), None) for key, names in aliases.items()}
        result['plddt_units'] = metadata.get('plddt_units')
        return result
    return {
        'plddt': metadata.get('plddt') or metadata.get('mean_plddt') or metadata.get('pLDDT'),
        'ptm': metadata.get('ptm') or metadata.get('pTM'),
        'pae': metadata.get('pae') or metadata.get('mean_pae') or metadata.get('PAE'),
        'rmsd': metadata.get('rmsd') or metadata.get('rmsd_overall'),
        'rmsd_binder': metadata.get('rmsd_binder') or metadata.get('binder_rmsd'),
    }

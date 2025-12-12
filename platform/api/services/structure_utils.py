"""
Structure Utilities - Biotite-based structure analysis helpers.

This module provides robust structure parsing and analysis using Biotite,
supporting both PDB and CIF formats. These utilities can be used alongside
or as replacements for manual parsing in result_ingester.py.

Usage:
    from services.structure_utils import load_structure, get_residue_plddt
    
    structure = load_structure("path/to/file.pdb")  # or .cif
    plddt_values = get_residue_plddt("path/to/file.cif")
"""

from pathlib import Path
from typing import Optional, Tuple, List, Union
import numpy as np

# Biotite imports
import biotite.structure as struc
import biotite.structure.io as strucio
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile


def load_structure(path: Union[str, Path]) -> struc.AtomArray:
    """
    Load a structure from PDB or CIF file, including B-factors.
    
    Args:
        path: Path to structure file (.pdb or .cif)
        
    Returns:
        AtomArray representing the structure (first model if multi-model)
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If format not supported
    """
    import biotite.structure.io.pdbx as pdbx
    
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Structure file not found: {path}")
    
    suffix = path.suffix.lower()
    
    if suffix == ".pdb":
        # Use PDB reader with extra fields to capture B-factors
        pdb_file = PDBFile.read(str(path))
        structure = pdb_file.get_structure(extra_fields=["b_factor", "occupancy"])
    elif suffix in (".cif", ".mmcif"):
        # Use CIF reader - correct API: read file then use get_structure function
        cif_file = pdbx.CIFFile.read(str(path))
        structure = pdbx.get_structure(cif_file, extra_fields=["b_factor"])
    else:
        # Fallback to auto-detection
        structure = strucio.load_structure(str(path))
    
    # If we got a stack (multi-model), take the first model
    if isinstance(structure, struc.AtomArrayStack):
        structure = structure[0]
    
    return structure


def get_residue_plddt(path: Union[str, Path]) -> Tuple[Optional[float], Optional[List[float]]]:
    """
    Extract per-residue pLDDT from structure B-factors (CA atoms).
    
    Works with both PDB and CIF files. This is a drop-in replacement for
    the manual extract_plddt_from_pdb() in result_ingester.py.
    
    Args:
        path: Path to structure file
        
    Returns:
        Tuple of (average_plddt, per_residue_plddt_list)
        Returns (None, None) on error
    """
    try:
        structure = load_structure(path)
        
        # Check if B-factors are available (stored in 'b_factor' annotation)
        if 'b_factor' not in structure.get_annotation_categories():
            print(f"[structure_utils] No B-factors in {path}")
            return None, None
        
        # Get CA atoms for per-residue values
        ca_mask = structure.atom_name == "CA"
        ca_atoms = structure[ca_mask]
        
        if len(ca_atoms) == 0:
            return None, None
        
        # B-factors accessed via annotation array
        b_factors = ca_atoms.get_annotation("b_factor")
        per_residue = [round(float(b), 2) for b in b_factors]
        avg_plddt = float(np.mean(b_factors))
        
        return avg_plddt, per_residue
        
    except Exception as e:
        print(f"[structure_utils] Error extracting pLDDT from {path}: {e}")
        return None, None


def get_chain_ids(path: Union[str, Path]) -> List[str]:
    """Get unique chain IDs from a structure."""
    try:
        structure = load_structure(path)
        return list(np.unique(structure.chain_id))
    except Exception:
        return []


def get_residue_count(path: Union[str, Path]) -> int:
    """Get total residue count from a structure."""
    try:
        structure = load_structure(path)
        return struc.get_residue_count(structure)
    except Exception:
        return 0


def get_secondary_structure(path: Union[str, Path]) -> dict:
    """
    Annotate secondary structure using P-SEA algorithm.
    
    Returns:
        Dict with counts: {'helix': n, 'sheet': n, 'coil': n}
    """
    try:
        structure = load_structure(path)
        
        # Only works on protein (amino acid residues)
        protein = structure[struc.filter_amino_acids(structure)]
        if len(protein) == 0:
            return {'helix': 0, 'sheet': 0, 'coil': 0}
        
        # Annotate SSE (returns array of 'a'=helix, 'b'=sheet, 'c'=coil per residue)
        sse = struc.annotate_sse(protein)
        
        return {
            'helix': int(np.sum(sse == 'a')),
            'sheet': int(np.sum(sse == 'b')),
            'coil': int(np.sum(sse == 'c'))
        }
    except Exception as e:
        print(f"[structure_utils] Error computing SSE for {path}: {e}")
        return {'helix': 0, 'sheet': 0, 'coil': 0}


def compute_rmsd(
    ref_path: Union[str, Path], 
    model_path: Union[str, Path],
    backbone_only: bool = True
) -> Optional[float]:
    """
    Compute RMSD between two structures after superimposition.
    
    Args:
        ref_path: Reference structure path
        model_path: Model structure path  
        backbone_only: If True, use only backbone atoms (N, CA, C)
        
    Returns:
        RMSD in Angstroms, or None on error
    """
    try:
        ref = load_structure(ref_path)
        model = load_structure(model_path)
        
        if backbone_only:
            backbone_atoms = ["N", "CA", "C"]
            ref = ref[np.isin(ref.atom_name, backbone_atoms)]
            model = model[np.isin(model.atom_name, backbone_atoms)]
        
        # Superimpose and compute RMSD
        fitted, _ = struc.superimpose(ref, model)
        rmsd = struc.rmsd(ref, fitted)
        
        return float(rmsd)
    except Exception as e:
        print(f"[structure_utils] Error computing RMSD: {e}")
        return None


def compute_gyration_radius(path: Union[str, Path]) -> Optional[float]:
    """Compute radius of gyration for a structure."""
    try:
        structure = load_structure(path)
        return float(struc.gyration_radius(structure))
    except Exception:
        return None


def convert_cif_to_pdb(cif_path: Union[str, Path], output_path: Union[str, Path]) -> bool:
    """
    Convert CIF file to PDB format.
    
    Useful for RF3 outputs that need PDB for frontend visualization.
    
    Args:
        cif_path: Input CIF file
        output_path: Output PDB file path
        
    Returns:
        True on success, False on error
    """
    try:
        structure = load_structure(cif_path)
        
        pdb_file = PDBFile()
        pdb_file.set_structure(structure)
        pdb_file.write(str(output_path))
        
        return True
    except Exception as e:
        print(f"[structure_utils] Error converting CIF to PDB: {e}")
        return False

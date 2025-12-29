"""
Structure file converters and loaders.

Handles CIF/PDB conversion and coordinate extraction using biotite.
"""

import gzip
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

# Try to import biotite
try:
    import biotite.structure as struc
    import biotite.structure.io.pdbx as pdbx
    import biotite.structure.io.pdb as pdb_io
    HAS_BIOTITE = True
except ImportError:
    HAS_BIOTITE = False


def load_structure(filepath: Path) -> Optional['struc.AtomArray']:
    """
    Load a structure from PDB or CIF file.
    
    Args:
        filepath: Path to structure file (.pdb, .cif, .cif.gz)
        
    Returns:
        AtomArray or None if loading fails
    """
    if not HAS_BIOTITE:
        raise ImportError("biotite is required for structure loading")
    
    filepath = Path(filepath)
    name = filepath.name.lower()
    
    try:
        if name.endswith('.cif.gz'):
            with gzip.open(filepath, 'rt') as f:
                cif_file = pdbx.CIFFile.read(f)
            return pdbx.get_structure(cif_file, model=1)
        elif name.endswith('.cif'):
            cif_file = pdbx.CIFFile.read(filepath)
            return pdbx.get_structure(cif_file, model=1)
        elif name.endswith('.pdb'):
            pdb_file = pdb_io.PDBFile.read(filepath)
            return pdb_file.get_structure(model=1)
        else:
            raise ValueError(f"Unknown file format: {filepath}")
    except Exception as e:
        print(f"Error loading structure {filepath}: {e}")
        return None


def load_structure_coords(filepath: Path, backbone_only: bool = True) -> Optional[np.ndarray]:
    """
    Load coordinates from a structure file.
    
    Args:
        filepath: Path to structure file
        backbone_only: If True, only return backbone atom coordinates
        
    Returns:
        Numpy array of coordinates or None
    """
    structure = load_structure(filepath)
    if structure is None:
        return None
    
    if backbone_only:
        try:
            from biotite.structure import filter_peptide_backbone
            mask = filter_peptide_backbone(structure)
        except ImportError:
            from biotite.structure import filter_backbone
            mask = filter_backbone(structure)
        structure = structure[mask]
    
    return structure.coord


def cif_to_pdb(cif_path: Path, pdb_path: Path) -> bool:
    """
    Convert CIF file to PDB format.
    
    Args:
        cif_path: Path to input CIF file (.cif or .cif.gz)
        pdb_path: Path to output PDB file
        
    Returns:
        True if successful, False otherwise
    """
    if not HAS_BIOTITE:
        raise ImportError("biotite is required for CIF to PDB conversion")
    
    try:
        structure = load_structure(cif_path)
        if structure is None:
            return False
        
        pdb_file = pdb_io.PDBFile()
        pdb_file.set_structure(structure)
        pdb_file.write(pdb_path)
        return True
    except Exception as e:
        print(f"Error converting {cif_path} to PDB: {e}")
        return False


def get_structure_info(filepath: Path) -> dict:
    """
    Get basic info about a structure file.
    
    Returns:
        Dict with 'n_atoms', 'n_residues', 'chains'
    """
    structure = load_structure(filepath)
    if structure is None:
        return {}
    
    return {
        'n_atoms': len(structure),
        'n_residues': len(set(zip(structure.chain_id, structure.res_id))),
        'chains': list(set(structure.chain_id)),
    }

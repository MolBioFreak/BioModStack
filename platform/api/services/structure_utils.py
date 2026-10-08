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

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
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
            logger.info(f"[structure_utils] No B-factors in {path}")
            return None, None
        
        # Get CA atoms for per-residue values
        ca_mask = structure.atom_name == "CA"
        ca_atoms = structure[ca_mask]
        
        if len(ca_atoms) == 0:
            return None, None
        
        # B-factors accessed via annotation array
        b_factors = ca_atoms.get_annotation("b_factor")
        
        # Auto-scale 0-1 to 0-100 (CIF files often use normalized 0-1 pLDDT)
        if len(b_factors) > 0 and np.max(b_factors) <= 1.0:
            b_factors = b_factors * 100.0
        
        per_residue = [round(float(b), 2) for b in b_factors]
        avg_plddt = float(np.mean(b_factors))
        
        return avg_plddt, per_residue
        
    except Exception as e:
        logger.error(f"[structure_utils] Error extracting pLDDT from {path}: {e}")
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
        logger.error(f"[structure_utils] Error computing SSE for {path}: {e}")
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
        logger.error(f"[structure_utils] Error computing RMSD: {e}")
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
        logger.error(f"[structure_utils] Error converting CIF to PDB: {e}")
        return False


def get_per_chain_metrics(path: Union[str, Path]) -> dict:
    """
    Extract per-chain metrics for proteins AND nucleic acids.
    
    Returns:
        {
            "A": {"type": "protein", "plddt": [...], "length": 500, "avg_plddt": 85.2, "residue_numbers": [1, 2, ...]},
            "B": {"type": "dna", "plddt": [...], "length": 50, "avg_plddt": 72.1, "residue_numbers": [1, 2, ...]},
            ...
        }
    """
    try:
        structure = load_structure(path)
        result = {}
        
        chain_ids = np.unique(structure.chain_id)
        
        for chain_id in chain_ids:
            chain = structure[structure.chain_id == chain_id]
            if len(chain) == 0:
                continue
                
            # Detect chain type
            # Check for amino acids (protein)
            is_protein = struc.filter_amino_acids(chain)
            has_protein = np.any(is_protein)
            
            # Check for nucleotides (DNA/RNA)
            is_nucleic = struc.filter_nucleotides(chain)
            has_nucleic = np.any(is_nucleic)
            
            # Get representative atoms based on type (for pLDDT extraction)
            atoms = None
            chain_type = "unknown"
            
            if has_protein:
                chain_type = "protein"
                # For protein, use CA atoms for pLDDT profile
                atoms = chain[chain.atom_name == "CA"]
                # Fallback if no CA (e.g. coarse grained?) - unlikely for AlphaFold/Boltz
                if len(atoms) == 0:
                    atoms = chain[is_protein] # Use all atoms if no CA found, to just get annot
            
            elif has_nucleic:
                # Distinguish DNA vs RNA based on residue names
                # DNA: DA, DT, DG, DC. RNA: A, U, G, C (typically) or RA, RU...
                # Check for Thymine (DNA specific) vs Uracil (RNA specific)
                res_names = np.unique(chain.res_name)
                is_dna = np.any([r in ["DT", "DA", "DC", "DG", "THY"] for r in res_names])
                is_rna = np.any([r in ["U", "URA"] for r in res_names])
                
                # If ambiguous, check sugar (C2' atom exists in RNA, not DNA? No, O2' exists in RNA)
                # But residue name check is usually sufficient for PDBs
                if is_dna and not is_rna:
                    chain_type = "dna"
                elif is_rna:
                    chain_type = "rna"
                else:
                    # Generic nucleic
                    chain_type = "dna" # Default to DNA if unsure (e.g. all G/C)
                
                # For nucleic acids, P (Phosphorous) is often used as "backbone" representative similar to CA
                # Or C1', C4', etc. Let's use P if available, else C1'
                atoms = chain[chain.atom_name == "P"]
                if len(atoms) == 0:
                    atoms = chain[chain.atom_name == "C1'"]
            
            else:
                # Ligand or Ion
                chain_type = "ligand"
                atoms = chain 
            
            # Extract pLDDT from B-factors if available
            plddt_list = []
            res_nums = []
            
            # For ligands, we might just want a single average, but the schema allows list
            # For polymers (protein/dna), we want per-residue list
            
            if atoms is not None and len(atoms) > 0 and 'b_factor' in atoms.get_annotation_categories():
                b_factors = atoms.get_annotation("b_factor")
                
                # Scale if 0-1
                if len(b_factors) > 0 and np.max(b_factors) <= 1.0:
                    b_factors = b_factors * 100.0
                
                plddt_list = [round(float(b), 2) for b in b_factors]
                res_nums = list(range(1, len(plddt_list) + 1)) # Simple 1-based index for now
                
                # Try to get actual residue numbers if available
                if 'res_id' in atoms.get_annotation_categories():
                    res_ids = atoms.res_id
                    # If strictly increasing, use them. If there are insertion codes (not handled by Biotite simple array),
                    # simple indexing might be safer. But let's try to use res_id.
                    if len(res_ids) == len(plddt_list):
                        res_nums = [int(r) for r in res_ids]

            avg_plddt = float(np.mean(plddt_list)) if plddt_list else None
            
            result[chain_id] = {
                "type": chain_type,
                "length": len(plddt_list),
                "avg_plddt": avg_plddt,
                "plddt": plddt_list,
                "residue_numbers": res_nums
            }
            
        return result
        
    except Exception as e:
        logger.error(f"[structure_utils] Error extracting chain metrics from {path}: {e}")
        return {}


def calculate_epitope_contacts(
    pdb_path: Union[str, Path],
    epitope_residues: List[str],
    antibody_chain: str = "A",
    target_chain: str = "B",
    distance_threshold: float = 8.0
) -> Tuple[int, Optional[float]]:
    """
    Calculate antibody-epitope contact metrics.
    
    Counts how many antibody residues are within distance_threshold of epitope
    residues, and returns the minimum distance to the epitope.
    
    Args:
        pdb_path: Path to structure file (PDB or CIF)
        epitope_residues: List of epitope residue specs (e.g., ["A111", "A112", ...])
        antibody_chain: Chain ID for antibody (default "A")
        target_chain: Chain ID for target protein (default "B")
        distance_threshold: Distance cutoff in Angstroms (default 8.0)
        
    Returns:
        Tuple of (contact_count, min_distance)
        - contact_count: Number of antibody CA atoms within threshold of any epitope CA
        - min_distance: Minimum CA-CA distance to epitope (Angstroms)
        Returns (0, None) on error
    """
    try:
        structure = load_structure(pdb_path)
        
        # Parse epitope residue numbers
        epitope_resnums = set()
        for res_spec in epitope_residues:
            # Format: "A111" or "B52" - extract chain and number
            if len(res_spec) < 2:
                continue
            chain_id = res_spec[0]
            try:
                resnum = int(res_spec[1:])
                epitope_resnums.add(resnum)
            except ValueError:
                continue
        
        if not epitope_resnums:
            logger.warning(f"[structure_utils] No valid epitope residues parsed from {epitope_residues}")
            return 0, None
        
        # Get CA atoms for antibody and target chains
        ab_ca = structure[
            (structure.chain_id == antibody_chain) & 
            (structure.atom_name == "CA")
        ]
        target_ca = structure[
            (structure.chain_id == target_chain) & 
            (structure.atom_name == "CA")
        ]
        
        if len(ab_ca) == 0 or len(target_ca) == 0:
            logger.warning(f"[structure_utils] Missing chains: Ab({antibody_chain})={len(ab_ca)}, Target({target_chain})={len(target_ca)}")
            return 0, None
        
        # Filter target to only epitope residues
        epitope_mask = np.isin(target_ca.res_id, list(epitope_resnums))
        epitope_ca = target_ca[epitope_mask]
        
        if len(epitope_ca) == 0:
            logger.warning(f"[structure_utils] No epitope residues found in chain {target_chain}")
            return 0, None
        
        # Calculate distances between all antibody CA and epitope CA
        ab_coords = ab_ca.coord
        epitope_coords = epitope_ca.coord
        
        # Compute pairwise distances
        min_distances = []
        for ab_coord in ab_coords:
            distances = np.sqrt(np.sum((epitope_coords - ab_coord) ** 2, axis=1))
            min_dist = np.min(distances)
            min_distances.append(min_dist)
        
        min_distances = np.array(min_distances)
        
        # Count contacts (antibody residues within threshold)
        contact_count = int(np.sum(min_distances < distance_threshold))
        
        # Overall minimum distance
        overall_min = float(np.min(min_distances)) if len(min_distances) > 0 else None
        
        return contact_count, overall_min
        
    except Exception as e:
        logger.error(f"[structure_utils] Error calculating epitope contacts: {e}")
        return 0, None

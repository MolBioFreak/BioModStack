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
        Dict with percentages (0-100): {'helix': pct, 'sheet': pct, 'coil': pct}
    """
    try:
        structure = load_structure(path)
        
        # Only works on protein (amino acid residues)
        protein = structure[struc.filter_amino_acids(structure)]
        if len(protein) == 0:
            return {'helix': 0.0, 'sheet': 0.0, 'coil': 0.0}
        
        # Annotate SSE (returns array of 'a'=helix, 'b'=sheet, 'c'=coil per residue)
        sse = struc.annotate_sse(protein)
        
        # Count each type
        total = len(sse)
        if total == 0:
            return {'helix': 0.0, 'sheet': 0.0, 'coil': 0.0}
        
        helix_count = int(np.sum(sse == 'a'))
        sheet_count = int(np.sum(sse == 'b'))
        coil_count = int(np.sum(sse == 'c'))
        
        # Convert to percentages
        return {
            'helix': round(helix_count / total * 100, 1),
            'sheet': round(sheet_count / total * 100, 1),
            'coil': round(coil_count / total * 100, 1)
        }
    except Exception as e:
        logger.error(f"[structure_utils] Error computing SSE for {path}: {e}")
        return {'helix': 0.0, 'sheet': 0.0, 'coil': 0.0}


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
    antibody_chain: str = "H",  # RFantibody outputs H/L chains
    target_chain: str = "B",    # Target is typically renamed to B
    distance_threshold: float = 8.0
) -> Tuple[int, Optional[float]]:
    """
    Calculate antibody-epitope contact metrics.
    
    Counts how many antibody residues are within distance_threshold of epitope
    residues, and returns the minimum distance to the epitope.
    
    Args:
        pdb_path: Path to structure file (PDB or CIF)
        epitope_residues: List of epitope residue specs (e.g., ["A111", "A112", ...])
                         The chain letter is stripped - only residue numbers are used.
        antibody_chain: Chain ID for antibody (default "H", also checks "L")
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
        
        # Parse epitope residue numbers (strip chain prefix, only use numbers)
        epitope_resnums = set()
        for res_spec in epitope_residues:
            if not res_spec:
                continue
            # Strip leading non-digit characters (chain ID like 'A', 'B', etc.)
            res_spec = res_spec.strip()
            num_str = ''.join(c for c in res_spec if c.isdigit() or c == '-')
            if num_str:
                try:
                    resnum = int(num_str)
                    epitope_resnums.add(resnum)
                except ValueError:
                    continue
        
        if not epitope_resnums:
            logger.warning(f"[structure_utils] No valid epitope residues parsed from {epitope_residues}")
            return 0, None
        
        # Get all chain IDs in structure
        all_chains = [str(chain_id) for chain_id in np.unique(structure.chain_id)]
        logger.info(f"[structure_utils] Structure chains: {all_chains}, epitope resnums: {sorted(epitope_resnums)[:5]}...")

        # Resolve target chain before applying chain-A antibody fallback.
        provisional_target_chain = target_chain if target_chain in all_chains else None
        if provisional_target_chain is None:
            for res_spec in epitope_residues:
                res_spec = (res_spec or "").strip()
                if res_spec and res_spec[0].isalpha() and res_spec[0] in all_chains:
                    provisional_target_chain = res_spec[0]
                    break

        # Auto-detect antibody chains (prefer H/L; only fall back to A when H/L are absent
        # and A is not already known to be the antigen chain).
        ab_chain_ids = [potential_ab for potential_ab in ['H', 'L'] if potential_ab in all_chains]
        if not ab_chain_ids:
            if antibody_chain in all_chains and antibody_chain != provisional_target_chain:
                ab_chain_ids.append(antibody_chain)
            elif 'A' in all_chains and provisional_target_chain != 'A':
                ab_chain_ids.append('A')

        # Auto-detect target chain (first non-antibody chain, prefer provisional/B/T)
        if target_chain not in all_chains:
            non_ab_chains = [c for c in all_chains if c not in ab_chain_ids]
            if provisional_target_chain in non_ab_chains:
                target_chain = provisional_target_chain
            elif 'B' in non_ab_chains:
                target_chain = 'B'
            elif 'T' in non_ab_chains:
                target_chain = 'T'
            elif non_ab_chains:
                target_chain = non_ab_chains[0]
            if target_chain:
                logger.info(f"[structure_utils] Auto-detected target chain: {target_chain}")
        
        # Get CA atoms for antibody chains (combine H and L)
        ab_mask = np.isin(structure.chain_id, ab_chain_ids) & (structure.atom_name == "CA")
        ab_ca = structure[ab_mask]
        
        # Get CA atoms for target chain
        target_ca = structure[
            (structure.chain_id == target_chain) & 
            (structure.atom_name == "CA")
        ]
        
        if len(ab_ca) == 0:
            logger.warning(f"[structure_utils] No antibody CA atoms found in chains {ab_chain_ids}")
            return 0, None
        if len(target_ca) == 0:
            logger.warning(f"[structure_utils] No target CA atoms found in chain {target_chain}")
            return 0, None
        
        # Filter target to only epitope residues
        epitope_mask = np.isin(target_ca.res_id, list(epitope_resnums))
        epitope_ca = target_ca[epitope_mask]
        
        if len(epitope_ca) == 0:
            logger.warning(f"[structure_utils] No epitope residues found in chain {target_chain}. "
                          f"Target has res_ids: {sorted(set(target_ca.res_id))[:10]}...")
            return 0, None
        
        logger.info(f"[structure_utils] Found {len(epitope_ca)} epitope atoms, {len(ab_ca)} antibody atoms")
        
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
        
        logger.info(f"[structure_utils] Epitope contacts: {contact_count}, min_distance: {overall_min:.2f}Å" if overall_min else "")
        
        return contact_count, overall_min
        
    except Exception as e:
        logger.error(f"[structure_utils] Error calculating epitope contacts: {e}")
        return 0, None


def compute_contact_map(
    path: Union[str, Path],
    distance_cutoff: float = 8.0,
    max_size: int = 500
) -> Tuple[Optional[List[List[float]]], Optional[List[int]], Optional[List[str]]]:
    """
    Compute Cα-Cα distance matrix for contact map visualization.
    
    Args:
        path: Path to structure file (PDB or CIF)
        distance_cutoff: Distance cutoff for binary contact (Å), not used for distance matrix
        max_size: Maximum matrix dimension (will downsample if larger)
        
    Returns:
        Tuple of (distance_matrix, residue_numbers, chain_ids)
        - distance_matrix: 2D list of distances in Angstroms
        - residue_numbers: List of residue numbers for each row/col
        - chain_ids: List of chain IDs for each residue
        Returns (None, None, None) on error
    """
    try:
        structure = load_structure(path)
        
        # Get CA atoms for protein chains
        protein = structure[struc.filter_amino_acids(structure)]
        if len(protein) == 0:
            logger.warning(f"[structure_utils] No amino acids in {path}")
            return None, None, None
        
        ca_atoms = protein[protein.atom_name == "CA"]
        
        if len(ca_atoms) == 0:
            logger.warning(f"[structure_utils] No CA atoms in {path}")
            return None, None, None
        
        coords = ca_atoms.coord
        n_residues = len(ca_atoms)
        
        # Get residue info
        res_ids = ca_atoms.res_id.tolist() if hasattr(ca_atoms, 'res_id') else list(range(1, n_residues + 1))
        chain_ids = ca_atoms.chain_id.tolist() if hasattr(ca_atoms, 'chain_id') else ['A'] * n_residues
        
        # Compute pairwise distance matrix
        # Use broadcasting for efficiency
        diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
        distances = np.sqrt(np.sum(diff ** 2, axis=-1))
        
        # Downsample if too large
        if n_residues > max_size:
            step = n_residues // max_size
            indices = list(range(0, n_residues, step))[:max_size]
            distances = distances[np.ix_(indices, indices)]
            res_ids = [res_ids[i] for i in indices]
            chain_ids = [chain_ids[i] for i in indices]
        
        # Convert to list for JSON serialization
        distance_matrix = [[round(float(d), 2) for d in row] for row in distances]
        
        return distance_matrix, res_ids, chain_ids
        
    except Exception as e:
        logger.error(f"[structure_utils] Error computing contact map from {path}: {e}")
        return None, None, None

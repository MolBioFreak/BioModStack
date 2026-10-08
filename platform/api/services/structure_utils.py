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
from typing import Optional, Tuple, List, Union, Dict, Any
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


def _parse_residue_specs(epitope_residues: List[str]) -> List[Tuple[Optional[str], int]]:
    specs: List[Tuple[Optional[str], int]] = []
    for res_spec in epitope_residues:
        raw = str(res_spec or "").strip()
        if not raw:
            continue
        chain_id = raw[0] if raw[0].isalpha() else None
        num_str = "".join(char for char in raw if char.isdigit() or char == "-")
        if not num_str:
            continue
        try:
            specs.append((chain_id, int(num_str)))
        except ValueError:
            continue
    return specs


def _unique_residue_ids(ca_atoms) -> List[int]:
    seen: List[int] = []
    for res_id in ca_atoms.res_id.tolist():
        value = int(res_id)
        if not seen or seen[-1] != value:
            seen.append(value)
    return seen


def _format_residue_label(atom) -> str:
    chain_id = str(getattr(atom, "chain_id", "") or "")
    res_id = int(getattr(atom, "res_id", 0))
    res_name = str(getattr(atom, "res_name", "") or "").strip()
    if res_name:
        return f"{chain_id}{res_id}:{res_name}"
    return f"{chain_id}{res_id}"


def _format_atom_label(atom) -> str:
    residue_label = _format_residue_label(atom)
    atom_name = str(getattr(atom, "atom_name", "") or "").strip()
    return f"{residue_label}:{atom_name}" if atom_name else residue_label


def _nearest_pair_details(query_atoms, target_atoms) -> Dict[str, Any]:
    if len(query_atoms) == 0 or len(target_atoms) == 0:
        return {
            "distance": None,
            "query_residue": None,
            "target_residue": None,
            "query_atom": None,
            "target_atom": None,
        }

    pairwise_distances = np.linalg.norm(
        query_atoms.coord[:, None, :] - target_atoms.coord[None, :, :],
        axis=2,
    )
    query_idx, target_idx = np.unravel_index(np.argmin(pairwise_distances), pairwise_distances.shape)
    query_atom = query_atoms[query_idx]
    target_atom = target_atoms[target_idx]
    return {
        "distance": float(pairwise_distances[query_idx, target_idx]),
        "query_residue": _format_residue_label(query_atom),
        "target_residue": _format_residue_label(target_atom),
        "query_atom": _format_atom_label(query_atom),
        "target_atom": _format_atom_label(target_atom),
    }


def _calculate_centroid_distance(query_ca, target_ca) -> Optional[float]:
    if len(query_ca) == 0 or len(target_ca) == 0:
        return None
    return float(np.linalg.norm(np.mean(query_ca.coord, axis=0) - np.mean(target_ca.coord, axis=0)))


def _parse_chain_hints(raw: Union[str, List[str], Tuple[str, ...], None]) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = [token.strip() for token in str(raw).replace(";", ",").replace("|", ",").split(",") if token.strip()]
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _infer_antibody_chains(all_chains: List[str], antibody_chain_hint: Union[str, List[str], Tuple[str, ...], None]) -> List[str]:
    antibody_chains: List[str] = []
    for chain_id in ["H", "L"]:
        if chain_id in all_chains and chain_id not in antibody_chains:
            antibody_chains.append(chain_id)

    if not antibody_chains:
        for chain_id in _parse_chain_hints(antibody_chain_hint) + ["A"]:
            if chain_id in all_chains and chain_id not in antibody_chains:
                antibody_chains.append(chain_id)

    return antibody_chains


def _infer_target_chain(
    all_chains: List[str],
    antibody_chain_ids: List[str],
    target_chain_hint: Union[str, List[str], Tuple[str, ...], None],
    epitope_residues: List[str],
) -> Optional[str]:
    for hinted_chain in _parse_chain_hints(target_chain_hint):
        if hinted_chain in all_chains and hinted_chain not in antibody_chain_ids:
            return hinted_chain

    for chain_id, _resnum in _parse_residue_specs(epitope_residues):
        if chain_id and chain_id in all_chains and chain_id not in antibody_chain_ids:
            return chain_id

    non_antibody_chains = [chain_id for chain_id in all_chains if chain_id not in antibody_chain_ids]
    for preferred_chain in ["B", "T"]:
        if preferred_chain in non_antibody_chains:
            return preferred_chain
    return non_antibody_chains[0] if non_antibody_chains else None


def _map_epitope_residue_numbers(
    epitope_residues: List[str],
    design_target_ca,
    target_chain_id: str,
    reference_target_pdb: Optional[Union[str, Path]],
    reference_target_chain: Optional[str],
) -> Tuple[set[int], str]:
    direct_numbers = {
        resnum
        for chain_id, resnum in _parse_residue_specs(epitope_residues)
        if chain_id in (None, target_chain_id)
    }
    if direct_numbers and np.isin(design_target_ca.res_id, list(direct_numbers)).any():
        return direct_numbers, "direct"

    if not reference_target_pdb:
        return direct_numbers, "missing_reference"

    reference_target_path = Path(reference_target_pdb)
    if not reference_target_path.exists():
        return direct_numbers, "missing_reference"

    reference_structure = load_structure(reference_target_path)
    reference_chains = [str(chain_id) for chain_id in np.unique(reference_structure.chain_id)]
    reference_specs = _parse_residue_specs(epitope_residues)
    reference_chain = reference_target_chain if reference_target_chain in reference_chains else None
    if reference_chain is None:
        for chain_id, _resnum in reference_specs:
            if chain_id and chain_id in reference_chains:
                reference_chain = chain_id
                break
    if reference_chain is None and len(reference_chains) == 1:
        reference_chain = reference_chains[0]
    if reference_chain is None:
        return direct_numbers, "reference_chain_unresolved"

    reference_target_ca = reference_structure[
        (reference_structure.chain_id == reference_chain) & (reference_structure.atom_name == "CA")
    ]
    if len(reference_target_ca) == 0:
        return direct_numbers, "reference_target_missing"

    reference_order = _unique_residue_ids(reference_target_ca)
    design_order = _unique_residue_ids(design_target_ca)
    if not reference_order or not design_order:
        return direct_numbers, "reference_or_design_empty"

    ordinal_map = {res_id: idx for idx, res_id in enumerate(reference_order)}
    mapped_numbers: set[int] = set()
    for chain_id, resnum in reference_specs:
        if chain_id not in (None, reference_chain):
            continue
        idx = ordinal_map.get(resnum)
        if idx is None or idx >= len(design_order):
            continue
        mapped_numbers.add(design_order[idx])

    if mapped_numbers:
        return mapped_numbers, "reference_order"
    return direct_numbers, "reference_mapping_failed"


def compute_contact_geometry_metrics(
    pdb_path: Union[str, Path],
    epitope_residues: List[str],
    antibody_chain: Union[str, List[str], Tuple[str, ...], None] = "H",
    target_chain: Union[str, List[str], Tuple[str, ...], None] = "B",
    epitope_contact_distance_threshold: float = 8.0,
    target_contact_distance_threshold: float = 12.0,
    reference_target_pdb: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Compute the full antibody-target geometry bundle used by RF screening.

    Returns the same headline fields the viewer already understands:
    epitope/target contact counts, min distances, centroid distances, nearest
    residue/atom labels, detected chain assignments, and residue counts.
    """
    structure = load_structure(pdb_path)
    all_chains = [str(chain_id) for chain_id in np.unique(structure.chain_id)]

    antibody_chain_ids = _infer_antibody_chains(all_chains, antibody_chain)
    target_chain_id = _infer_target_chain(all_chains, antibody_chain_ids, target_chain, epitope_residues)
    if target_chain_id in antibody_chain_ids:
        antibody_chain_ids = [chain_id for chain_id in antibody_chain_ids if chain_id != target_chain_id]

    if not antibody_chain_ids:
        raise ValueError(f"No antibody chains found in structure. Chains: {all_chains}")
    if not target_chain_id:
        raise ValueError(f"No target chain found in structure. Chains: {all_chains}")

    antibody_ca = structure[np.isin(structure.chain_id, antibody_chain_ids) & (structure.atom_name == "CA")]
    target_ca = structure[(structure.chain_id == target_chain_id) & (structure.atom_name == "CA")]
    antibody_atoms = structure[np.isin(structure.chain_id, antibody_chain_ids)]
    target_atoms = structure[structure.chain_id == target_chain_id]

    if len(antibody_ca) == 0:
        raise ValueError(f"No antibody CA atoms found in chains {antibody_chain_ids}")
    if len(target_ca) == 0:
        raise ValueError(f"No target CA atoms found in chain {target_chain_id}")

    reference_target_chain = _parse_chain_hints(target_chain)
    epitope_residue_numbers, epitope_mapping_mode = _map_epitope_residue_numbers(
        epitope_residues,
        design_target_ca=target_ca,
        target_chain_id=target_chain_id,
        reference_target_pdb=reference_target_pdb,
        reference_target_chain=reference_target_chain[0] if reference_target_chain else None,
    )

    epitope_residue_count = 0
    epitope_ca = target_ca[:0]
    epitope_atoms = target_atoms[:0]
    if epitope_residue_numbers:
        epitope_ca = target_ca[np.isin(target_ca.res_id, list(epitope_residue_numbers))]
        epitope_atoms = target_atoms[np.isin(target_atoms.res_id, list(epitope_residue_numbers))]
        epitope_residue_count = int(len(epitope_ca))

    query_coords = antibody_ca.coord
    target_pairwise = np.linalg.norm(
        query_coords[:, None, :] - target_ca.coord[None, :, :],
        axis=2,
    )
    min_target_distances = np.min(target_pairwise, axis=1)
    target_ca_nearest = _nearest_pair_details(antibody_ca, target_ca)
    target_atom_nearest = _nearest_pair_details(antibody_atoms, target_atoms)

    epitope_contact_count = 0
    epitope_min_distance: Optional[float] = None
    epitope_ca_nearest = {
        "distance": None,
        "query_residue": None,
        "target_residue": None,
        "query_atom": None,
        "target_atom": None,
    }
    epitope_atom_nearest = {
        "distance": None,
        "query_residue": None,
        "target_residue": None,
        "query_atom": None,
        "target_atom": None,
    }
    if len(epitope_ca) > 0:
        epitope_pairwise = np.linalg.norm(
            query_coords[:, None, :] - epitope_ca.coord[None, :, :],
            axis=2,
        )
        min_epitope_distances = np.min(epitope_pairwise, axis=1)
        epitope_contact_count = int(np.sum(min_epitope_distances < epitope_contact_distance_threshold))
        epitope_min_distance = float(np.min(min_epitope_distances))
        epitope_ca_nearest = _nearest_pair_details(antibody_ca, epitope_ca)
        epitope_atom_nearest = _nearest_pair_details(antibody_atoms, epitope_atoms) if len(epitope_atoms) > 0 else epitope_atom_nearest

    return {
        "detected_antibody_chains": ",".join(antibody_chain_ids),
        "detected_target_chain": target_chain_id,
        "antibody_residue_count": int(len(antibody_ca)),
        "target_residue_count": int(len(target_ca)),
        "epitope_residue_count": epitope_residue_count,
        "epitope_mapping_mode": epitope_mapping_mode,
        "epitope_contact_count": epitope_contact_count,
        "epitope_min_distance": epitope_min_distance,
        "epitope_min_atom_distance": epitope_atom_nearest["distance"],
        "epitope_nearest_antibody_residue": epitope_ca_nearest["query_residue"],
        "epitope_nearest_target_residue": epitope_ca_nearest["target_residue"],
        "epitope_nearest_antibody_atom": epitope_atom_nearest["query_atom"],
        "epitope_nearest_target_atom": epitope_atom_nearest["target_atom"],
        "epitope_centroid_distance": _calculate_centroid_distance(antibody_ca, epitope_ca),
        "target_contact_count": int(np.sum(min_target_distances < target_contact_distance_threshold)),
        "target_min_distance": float(np.min(min_target_distances)),
        "target_min_atom_distance": target_atom_nearest["distance"],
        "target_nearest_antibody_residue": target_ca_nearest["query_residue"],
        "target_nearest_target_residue": target_ca_nearest["target_residue"],
        "target_nearest_antibody_atom": target_atom_nearest["query_atom"],
        "target_nearest_target_atom": target_atom_nearest["target_atom"],
        "target_centroid_distance": _calculate_centroid_distance(antibody_ca, target_ca),
    }


def calculate_epitope_contacts(
    pdb_path: Union[str, Path],
    epitope_residues: List[str],
    antibody_chain: Union[str, List[str], Tuple[str, ...], None] = "H",
    target_chain: Union[str, List[str], Tuple[str, ...], None] = "B",
    distance_threshold: float = 8.0
) -> Tuple[int, Optional[float]]:
    """
    Backward-compatible light wrapper around the richer geometry computation.
    """
    try:
        metrics = compute_contact_geometry_metrics(
            pdb_path=pdb_path,
            epitope_residues=epitope_residues,
            antibody_chain=antibody_chain,
            target_chain=target_chain,
            epitope_contact_distance_threshold=distance_threshold,
        )
        return int(metrics.get("epitope_contact_count") or 0), metrics.get("epitope_min_distance")
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

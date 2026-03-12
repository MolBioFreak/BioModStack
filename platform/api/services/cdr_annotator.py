"""
CDR Annotator Service - Extract CDR sequences and lengths using ANARCII.

Runs ANARCII (deep learning antibody numbering) from the antibody_tools.sif container
to annotate antibody designs with CDR region information.
"""

import subprocess
import tempfile
import json
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class CDRAnnotation:
    """CDR annotation result for a single antibody sequence."""
    antibody_type: str  # vhh, fab, scfv
    binder_length: int
    
    # CDR sequences (IMGT numbered)
    cdr_h1: Optional[str] = None
    cdr_h2: Optional[str] = None
    cdr_h3: Optional[str] = None
    cdr_l1: Optional[str] = None
    cdr_l2: Optional[str] = None
    cdr_l3: Optional[str] = None
    
    # CDR lengths
    cdr_h1_length: Optional[int] = None
    cdr_h2_length: Optional[int] = None
    cdr_h3_length: Optional[int] = None
    cdr_l1_length: Optional[int] = None
    cdr_l2_length: Optional[int] = None
    cdr_l3_length: Optional[int] = None
    
    # IMGT position ranges (classic)
    cdr_h1_range: Optional[tuple] = None
    cdr_h2_range: Optional[tuple] = None
    cdr_h3_range: Optional[tuple] = None
    cdr_l1_range: Optional[tuple] = None
    cdr_l2_range: Optional[tuple] = None
    cdr_l3_range: Optional[tuple] = None

    # Raw Sequence 0-indexed ranges (for UI selection match)
    cdr_h1_seq_range: Optional[tuple] = None
    cdr_h2_seq_range: Optional[tuple] = None
    cdr_h3_seq_range: Optional[tuple] = None
    cdr_l1_seq_range: Optional[tuple] = None
    cdr_l2_seq_range: Optional[tuple] = None
    cdr_l3_seq_range: Optional[tuple] = None
    
    # Framework contact hotspots (Zavrtanik et al. 2018)
    # These FR positions mediate antigen contacts in nanobodies
    fr2_contacts: Optional[str] = None   # IMGT 37, 42, 44, 45, 47
    de_loop: Optional[str] = None        # IMGT 72-75
    fr3_contacts: Optional[str] = None   # IMGT 82-87
    fr4_contacts: Optional[str] = None   # IMGT 101-103
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "antibody_type": self.antibody_type,
            "binder_length": self.binder_length,
            "cdr_h1": self.cdr_h1,
            "cdr_h2": self.cdr_h2,
            "cdr_h3": self.cdr_h3,
            "cdr_l1": self.cdr_l1,
            "cdr_l2": self.cdr_l2,
            "cdr_l3": self.cdr_l3,
            "cdr_h1_length": self.cdr_h1_length,
            "cdr_h2_length": self.cdr_h2_length,
            "cdr_h3_length": self.cdr_h3_length,
            "cdr_l1_length": self.cdr_l1_length,
            "cdr_l2_length": self.cdr_l2_length,
            "cdr_l3_length": self.cdr_l3_length,
            "cdr_h1_seq_range": self.cdr_h1_seq_range,
            "cdr_h2_seq_range": self.cdr_h2_seq_range,
            "cdr_h3_seq_range": self.cdr_h3_seq_range,
            "cdr_l1_seq_range": self.cdr_l1_seq_range,
            "cdr_l2_seq_range": self.cdr_l2_seq_range,
            "cdr_l3_seq_range": self.cdr_l3_seq_range,
            "fr2_contacts": self.fr2_contacts,
            "de_loop": self.de_loop,
            "fr3_contacts": self.fr3_contacts,
            "fr4_contacts": self.fr4_contacts,
        }


# Container path resolution - uses BMS_DATA/BMS_CONTAINER_DIR for correct NVMe paths
from paths import get_container_path
import logging
logger = logging.getLogger(__name__)


def extract_sequence_from_pdb(pdb_path: str, chain_id: Optional[str] = None) -> Dict[str, str]:
    """
    Extract amino acid sequences from a PDB or CIF file.
    
    Returns dict of {chain_id: sequence}
    """
    import numpy as np
    import biotite.structure as struc
    from biotite.sequence import ProteinSequence

    from services.structure_utils import load_structure

    structure = load_structure(pdb_path)
    protein = structure[struc.filter_amino_acids(structure)]

    sequences: Dict[str, str] = {}
    if len(protein) == 0:
        return sequences

    for current_chain_id in np.unique(protein.chain_id):
        current_chain_id = str(current_chain_id)
        if chain_id and current_chain_id != chain_id:
            continue

        chain_atoms = protein[protein.chain_id == current_chain_id]
        _, residue_names = struc.get_residues(chain_atoms)

        residues = []
        for res_name in residue_names:
            try:
                aa = ProteinSequence.convert_letter_3to1(str(res_name))
            except Exception:
                continue
            if aa and aa != "?":
                residues.append(aa)

        if residues:
            sequences[current_chain_id] = "".join(residues)

    return sequences


def identify_binder_chains(sequences: Dict[str, str], pdb_path: str) -> Dict[str, str]:
    """
    Identify which chains are potential antibody/TCR variable domains.
    
    For VHH/nanobody: Returns only H chain
    For Fab/scFv: Returns both H and L chains
    For TCR: Returns alpha (as L) and/or beta (as H) chains
    
    Uses sequence signatures to detect chains:
    - VHH/VH: QVQLV, EVQLV, etc.
    - VL/VK: DIVMT, DIQMT, etc.
    - TCR Beta: NAGVTQ, GAVVSQ, DGVTQ, etc.
    - TCR Alpha: AQTVT, AQSVE, AQQVT, etc.
    
    Returns dict of {chain_type: chain_id} where chain_type is 'H' or 'L'
    (H for heavy/beta, L for light/alpha)
    """
    # Common VH/VHH framework 1 signatures.
    # Include common camelid VHH starts such as VQLQE... used by many nanobodies.
    vh_signatures = ["MQLQE", "MQVQL", "MEVQL", "VQLVE", "VQLQE", "LQLQE", "QVQLV", "EVQLV", "QVKLV", "QVQLQ", "EVQLQ", "QVQLK", "EVQLK", "QVTLK", "VQLEQ"]
    vl_signatures = ["DIVMT", "DIQMT", "EIVLT", "DIVLT", "EIVMT", "QSVLT", "QSVVS"]
    # TCR variable region signatures
    tcr_beta_signatures = ["NAGVT", "GAVVS", "DGVTQ", "LGVTQ", "LGHDT", "GVTQS"]
    tcr_alpha_signatures = ["AQTVT", "AQSVE", "AQQVT", "AQEVT", "AQKVT", "GQEVE"]
    
    found_chains = {}  # {chain_type: chain_id}
    
    # Detect by sequence signature or explicit H/L chain IDs.
    for chain_id, seq in sequences.items():
        seq_upper = seq.upper()[:10]  # Check first 10 residues
        chain_id_upper = chain_id.upper()

        # Check for VH/VHH signature
        for sig in vh_signatures:
            if seq_upper.startswith(sig):
                print(f"[CDR Annotator] Chain {chain_id} identified as VH/VHH (signature: {sig})")
                found_chains['H'] = chain_id
                break
        
        # Check for VL signature
        for sig in vl_signatures:
            if seq_upper.startswith(sig):
                print(f"[CDR Annotator] Chain {chain_id} identified as VL (signature: {sig})")
                found_chains['L'] = chain_id
                break
        
        # Check for TCR Beta signature (maps to H)
        for sig in tcr_beta_signatures:
            if seq_upper.startswith(sig):
                print(f"[CDR Annotator] Chain {chain_id} identified as TCR Beta (signature: {sig})")
                found_chains['H'] = chain_id
                break
        
        # Check for TCR Alpha signature (maps to L)
        for sig in tcr_alpha_signatures:
            if seq_upper.startswith(sig):
                print(f"[CDR Annotator] Chain {chain_id} identified as TCR Alpha (signature: {sig})")
                found_chains['L'] = chain_id
                break

        # Respect explicit chain IDs when they are present, but do not invent
        # additional antibody chains from unrelated target chains.
        if 'H' not in found_chains and chain_id_upper == 'H' and 80 <= len(seq) <= 150:
            print(f"[CDR Annotator] Chain {chain_id} identified as H by chain ID")
            found_chains['H'] = chain_id
        if 'L' not in found_chains and chain_id_upper in {'L', 'K'} and 80 <= len(seq) <= 150:
            print(f"[CDR Annotator] Chain {chain_id} identified as L by chain ID")
            found_chains['L'] = chain_id

    # If found at least one chain by signature, return
    if found_chains:
        return found_chains

    # Conservative fallback: only accept an unlabeled single variable-domain-sized
    # chain. Do not guess a second light chain from a target chain.
    candidates = [chain_id for chain_id, seq in sequences.items() if 80 <= len(seq) <= 150]
    if len(candidates) == 1:
        print(f"[CDR Annotator] Single variable-domain candidate {candidates[0]} selected as H")
        return {'H': candidates[0]}

    if candidates:
        print(f"[CDR Annotator] Ambiguous binder chains in {pdb_path}; refusing to guess: {candidates}")
    return {}


# Keep legacy function for backward compatibility
def identify_binder_chain(sequences: Dict[str, str], pdb_path: str) -> Optional[str]:
    """Legacy wrapper - returns first identified chain."""
    chains = identify_binder_chains(sequences, pdb_path)
    return chains.get('H') or chains.get('L') or None


def run_anarcii(sequence: str, scheme: str = "imgt") -> Optional[Dict]:
    """
    Run ANARCII numbering on a sequence using the antibody_tools container.
    
    Returns dict with chain type and CDR regions extracted from IMGT numbering.
    CDR definitions (IMGT): H1=27-38, H2=56-65, H3=105-117, L1=27-38, L2=56-65, L3=105-117
    """
    container_path = get_container_path("antibody_tools.sif")
    
    if not container_path.exists():
        logger.error(f"[CDR Annotator] Container not found: {container_path}")
        return None
    
    try:
        # Run ANARCII inside container with correct API
        cmd = [
            "apptainer", "exec", str(container_path),
            "python3", "-c", f'''
import json
from anarcii import Anarcii

seq = "{sequence}"
numberer = Anarcii(seq_type='unknown')  # Auto-detect antibody vs TCR
result = numberer.number([seq])

output = {{}}
for seq_name, data in result.items():
    chain_type = data.get("chain_type", "")
    if not chain_type:
        continue
    
    numbering = data.get("numbering") or []
    if not numbering:
        output[chain_type] = {{
            "cdr1": "",
            "cdr2": "",
            "cdr3": "",
            "cdr1_range": None,
            "cdr2_range": None,
            "cdr3_range": None,
            "cdr1_seq_range": None,
            "cdr2_seq_range": None,
            "cdr3_seq_range": None,
            "scheme": data.get("scheme", "imgt"),
        }}
        continue
    
    # Extract CDRs based on IMGT position ranges
    # Track both residues and position ranges
    cdr1_residues = []
    cdr2_residues = []
    cdr3_residues = []
    cdr1_positions = []
    cdr2_positions = []
    cdr3_positions = []
    
    # Track raw sequence indices
    cdr1_seq_indices = []
    cdr2_seq_indices = []
    cdr3_seq_indices = []
    
    seq_idx = -1
    
    for (pos, insertion), aa in numbering:
        if aa != "-":
            seq_idx += 1
        else:
            continue
            
        if 27 <= pos <= 38:
            cdr1_residues.append(aa)
            cdr1_positions.append(pos)
            cdr1_seq_indices.append(seq_idx)
        elif 56 <= pos <= 65:
            cdr2_residues.append(aa)
            cdr2_positions.append(pos)
            cdr2_seq_indices.append(seq_idx)
        elif 105 <= pos <= 117:
            cdr3_residues.append(aa)
            cdr3_positions.append(pos)
            cdr3_seq_indices.append(seq_idx)
    
    output[chain_type] = {{
        "cdr1": "".join(cdr1_residues),
        "cdr2": "".join(cdr2_residues),
        "cdr3": "".join(cdr3_residues),
        "cdr1_range": [min(cdr1_positions), max(cdr1_positions)] if cdr1_positions else None,
        "cdr2_range": [min(cdr2_positions), max(cdr2_positions)] if cdr2_positions else None,
        "cdr3_range": [min(cdr3_positions), max(cdr3_positions)] if cdr3_positions else None,
        "cdr1_seq_range": [min(cdr1_seq_indices), max(cdr1_seq_indices)] if cdr1_seq_indices else None,
        "cdr2_seq_range": [min(cdr2_seq_indices), max(cdr2_seq_indices)] if cdr2_seq_indices else None,
        "cdr3_seq_range": [min(cdr3_seq_indices), max(cdr3_seq_indices)] if cdr3_seq_indices else None,
        "scheme": data.get("scheme", "imgt"),
    }}

print(json.dumps(output))
'''
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                print(f"[CDR Annotator] Invalid JSON: {result.stdout[:200]}")
                return None
        else:
            print(f"[CDR Annotator] ANARCII error: {result.stderr[:500]}")
            return None
            
    except subprocess.TimeoutExpired:
        print("[CDR Annotator] ANARCII timeout")
        return None
    except Exception as e:
        print(f"[CDR Annotator] Error: {e}")
        return None


def annotate_pdb(
    pdb_path: str,
    preferred_chains: Optional[Dict[str, str]] = None,
) -> Optional[CDRAnnotation]:
    """
    Annotate CDR regions for an antibody PDB file.
    
    Handles both VHH (single chain) and Fab/scFv (H+L chains).
    """
    pdb_path = Path(pdb_path)
    if not pdb_path.exists():
        print(f"[CDR Annotator] PDB not found: {pdb_path}")
        return None
    
    # Extract sequences from all chains
    try:
        sequences = extract_sequence_from_pdb(str(pdb_path))
    except Exception as e:
        print(f"[CDR Annotator] Failed to parse PDB: {e}")
        return None
    
    if not sequences:
        print(f"[CDR Annotator] No sequences extracted from {pdb_path}")
        return None
    
    # Identify antibody chains (H and/or L).
    # Prefer explicit chain hints when available (e.g. SAbDab metadata),
    # then fall back to sequence-signature auto-detection.
    binder_chains: Dict[str, str] = {}
    if preferred_chains:
        for chain_type in ("H", "L"):
            chain_id = preferred_chains.get(chain_type)
            if not chain_id:
                continue
            chain_id = str(chain_id).strip()
            if chain_id in sequences:
                binder_chains[chain_type] = chain_id
        if binder_chains:
            print(f"[CDR Annotator] Using preferred chains: {binder_chains}")
        else:
            print("[CDR Annotator] Preferred chains not found in parsed PDB, falling back to auto-detection")

    if not binder_chains:
        binder_chains = identify_binder_chains(sequences, str(pdb_path))
    if not binder_chains:
        print(f"[CDR Annotator] Could not identify binder chain")
        return None
    
    # Calculate total binder length
    binder_length = sum(len(sequences[chain_id]) for chain_id in binder_chains.values())
    
    # Determine antibody type
    antibody_type = "vhh" if len(binder_chains) == 1 and 'H' in binder_chains else "fab" if 'L' in binder_chains else "vhh"
    
    annotation = CDRAnnotation(
        antibody_type=antibody_type,
        binder_length=binder_length
    )
    
    # Run ANARCII on each antibody chain
    for chain_type, chain_id in binder_chains.items():
        chain_seq = sequences[chain_id]
        print(f"[CDR Annotator] Running ANARCII on {chain_type} chain (chain {chain_id}, {len(chain_seq)} AA)")
        
        anarcii_result = run_anarcii(chain_seq)
        
        if not anarcii_result or "error" in anarcii_result:
            print(f"[CDR Annotator] ANARCII failed for {chain_type} chain")
            continue
        
        # Extract CDRs for this chain type
        # ANARCII returns chain type: H/K/L for antibodies, A/B/G/D for TCRs
        # Map TCR chains: B/G -> H fields (heavy-like), A/D -> L fields (light-like)
        if "H" in anarcii_result or "B" in anarcii_result or "G" in anarcii_result:
            h_data = anarcii_result.get("H") or anarcii_result.get("B") or anarcii_result.get("G", {})
            annotation.cdr_h1 = h_data.get("cdr1", "")
            annotation.cdr_h2 = h_data.get("cdr2", "")
            annotation.cdr_h3 = h_data.get("cdr3", "")
            annotation.cdr_h1_length = len(annotation.cdr_h1) if annotation.cdr_h1 else None
            annotation.cdr_h2_length = len(annotation.cdr_h2) if annotation.cdr_h2 else None
            annotation.cdr_h3_length = len(annotation.cdr_h3) if annotation.cdr_h3 else None
            # Extract IMGT position ranges for 3D viewer highlighting
            if h_data.get("cdr1_range"):
                annotation.cdr_h1_range = tuple(h_data["cdr1_range"])
            if h_data.get("cdr2_range"):
                annotation.cdr_h2_range = tuple(h_data["cdr2_range"])
            if h_data.get("cdr3_range"):
                annotation.cdr_h3_range = tuple(h_data["cdr3_range"])
            
            # Extract raw sequential ranges for seamless frontend mapping
            if h_data.get("cdr1_seq_range"):
                annotation.cdr_h1_seq_range = tuple(h_data["cdr1_seq_range"])
            if h_data.get("cdr2_seq_range"):
                annotation.cdr_h2_seq_range = tuple(h_data["cdr2_seq_range"])
            if h_data.get("cdr3_seq_range"):
                annotation.cdr_h3_seq_range = tuple(h_data["cdr3_seq_range"])
                
            # Mark as TCR if detected
            if "B" in anarcii_result or "G" in anarcii_result:
                annotation.antibody_type = "tcr"
        
        if "L" in anarcii_result or "K" in anarcii_result or "A" in anarcii_result or "D" in anarcii_result:
            l_data = anarcii_result.get("L") or anarcii_result.get("K") or anarcii_result.get("A") or anarcii_result.get("D", {})
            annotation.cdr_l1 = l_data.get("cdr1", "")
            annotation.cdr_l2 = l_data.get("cdr2", "")
            annotation.cdr_l3 = l_data.get("cdr3", "")
            annotation.cdr_l1_length = len(annotation.cdr_l1) if annotation.cdr_l1 else None
            annotation.cdr_l2_length = len(annotation.cdr_l2) if annotation.cdr_l2 else None
            annotation.cdr_l3_length = len(annotation.cdr_l3) if annotation.cdr_l3 else None
            # Extract IMGT position ranges for 3D viewer highlighting
            if l_data.get("cdr1_range"):
                annotation.cdr_l1_range = tuple(l_data["cdr1_range"])
            if l_data.get("cdr2_range"):
                annotation.cdr_l2_range = tuple(l_data["cdr2_range"])
            if l_data.get("cdr3_range"):
                annotation.cdr_l3_range = tuple(l_data["cdr3_range"])
                
            # Extract raw sequential ranges for seamless frontend mapping
            if l_data.get("cdr1_seq_range"):
                annotation.cdr_l1_seq_range = tuple(l_data["cdr1_seq_range"])
            if l_data.get("cdr2_seq_range"):
                annotation.cdr_l2_seq_range = tuple(l_data["cdr2_seq_range"])
            if l_data.get("cdr3_seq_range"):
                annotation.cdr_l3_seq_range = tuple(l_data["cdr3_seq_range"])
                
            if "A" in anarcii_result or "D" in anarcii_result:
                annotation.antibody_type = "tcr"
            elif annotation.antibody_type != "tcr":
                annotation.antibody_type = "fab"  # Has light chain
    
    return annotation


def _fallback_annotate_individually(pdb_paths: list[str]) -> Dict[str, CDRAnnotation]:
    """Slow but reliable fallback when batched ANARCII fails."""
    annotations: Dict[str, CDRAnnotation] = {}
    for pdb_path in pdb_paths:
        try:
            annot = annotate_pdb(pdb_path)
        except Exception as exc:
            print(f"[CDR Annotator] Individual fallback failed for {pdb_path}: {exc}")
            annot = None
        if annot:
            annotations[pdb_path] = annot
    return annotations


def batch_annotate_pdbs(pdb_paths: list, batch_size: int = 500) -> Dict[str, CDRAnnotation]:
    """
    Batch annotate multiple PDB files for CDR regions.
    
    This is ~30x faster than calling annotate_pdb one-by-one because it:
    1. Extracts all sequences first (CPU-bound, fast)
    2. Runs ANARCII once with all sequences batched (CPU inference with 24 cores)
    3. Maps results back to PDB paths
    
    Returns dict of {pdb_path: CDRAnnotation}
    """
    container_path = get_container_path("antibody_tools.sif")
    
    if not container_path.exists():
        logger.error(f"[CDR Annotator] Container not found: {container_path}")
        return {}
    
    # Step 1: Extract all binder sequences from PDBs
    # For Fab/scFv, we need to track both H and L chains separately
    path_to_chains = {}  # {pdb_path: {'H': seq, 'L': seq, 'length': total_len}}
    all_sequences = []   # Flat list of all sequences for batch ANARCII
    seq_to_info = []     # [(pdb_path, chain_type), ...] - maps seq index to origin
    
    print(f"[CDR Annotator] Extracting sequences from {len(pdb_paths)} PDBs...")
    for pdb_path in pdb_paths:
        try:
            sequences = extract_sequence_from_pdb(pdb_path)
            if sequences:
                binder_chains = identify_binder_chains(sequences, pdb_path)
                if binder_chains:
                    path_to_chains[pdb_path] = {
                        'H': None, 'L': None, 
                        'length': sum(len(sequences[cid]) for cid in binder_chains.values())
                    }
                    for chain_type, chain_id in binder_chains.items():
                        seq = sequences[chain_id]
                        path_to_chains[pdb_path][chain_type] = seq
                        all_sequences.append(seq)
                        seq_to_info.append((pdb_path, chain_type))
        except Exception as e:
            print(f"[CDR Annotator] Error extracting {pdb_path}: {e}")
    
    if not all_sequences:
        print("[CDR Annotator] No sequences extracted")
        return {}
    
    print(f"[CDR Annotator] Extracted {len(all_sequences)} chains from {len(path_to_chains)} PDBs, running ANARCII with 24 cores...")
    
    # Step 2: Run ANARCII in batch using temp file for sequences
    # Write sequences to temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(all_sequences, f)
        seq_file = f.name
    
    try:
        cmd = [
            "apptainer", "exec", 
            "--bind", f"{seq_file}:/tmp/sequences.json",
            str(container_path),
            "python3", "-c", '''
import json
import sys

# Read sequences from temp file
with open("/tmp/sequences.json") as f:
    sequences = json.load(f)

from anarcii import Anarcii

# Use 24 CPU cores and batch size 500
numberer = Anarcii(seq_type='unknown', cpu=True, batch_size=500, ncpu=24)  # Auto-detect antibody vs TCR
results = numberer.number(sequences) or []

# FR contact hotspot IMGT positions (Zavrtanik et al. 2018)
FR2_POSITIONS = {37, 42, 44, 45, 47}  # VHH tetrad + contacts
DE_LOOP_RANGE = (72, 75)              # DE loop
FR3_RANGE = (82, 87)                  # FR3 contacts
FR4_RANGE = (101, 103)                # C-terminal contacts

if isinstance(results, dict):
    ordered_results = list(results.values())
elif isinstance(results, list):
    ordered_results = results
else:
    ordered_results = []

output = []
for data in ordered_results:
    if data is None:
        output.append({
            "chain_type": "",
            "cdr1": "",
            "cdr2": "",
            "cdr3": "",
            "cdr1_seq_range": None,
            "cdr2_seq_range": None,
            "cdr3_seq_range": None,
            "fr2_contacts": "",
            "de_loop": "",
            "fr3_contacts": "",
            "fr4_contacts": "",
        })
        continue
    chain_type = data.get("chain_type", "")
    numbering = data.get("numbering") or []
    if not numbering:
        output.append({
            "chain_type": chain_type,
            "cdr1": "",
            "cdr2": "",
            "cdr3": "",
            "cdr1_seq_range": None,
            "cdr2_seq_range": None,
            "cdr3_seq_range": None,
            "fr2_contacts": "",
            "de_loop": "",
            "fr3_contacts": "",
            "fr4_contacts": "",
        })
        continue
    
    cdr1_residues = []
    cdr2_residues = []
    cdr3_residues = []
    
    cdr1_seq_indices = []
    cdr2_seq_indices = []
    cdr3_seq_indices = []
    
    # FR contact hotspots
    fr2_residues = []
    de_loop_residues = []
    fr3_residues = []
    fr4_residues = []
    
    seq_idx = -1
    
    for (pos, insertion), aa in numbering:
        if aa != "-":
            seq_idx += 1
        else:
            continue
            
        # CDRs (IMGT)
        if 27 <= pos <= 38:
            cdr1_residues.append(aa)
            cdr1_seq_indices.append(seq_idx)
        elif 56 <= pos <= 65:
            cdr2_residues.append(aa)
            cdr2_seq_indices.append(seq_idx)
        elif 105 <= pos <= 117:
            cdr3_residues.append(aa)
            cdr3_seq_indices.append(seq_idx)
        
        # FR contact hotspots
        if pos in FR2_POSITIONS:
            fr2_residues.append(aa)
        if DE_LOOP_RANGE[0] <= pos <= DE_LOOP_RANGE[1]:
            de_loop_residues.append(aa)
        if FR3_RANGE[0] <= pos <= FR3_RANGE[1]:
            fr3_residues.append(aa)
        if FR4_RANGE[0] <= pos <= FR4_RANGE[1]:
            fr4_residues.append(aa)
    
    output.append({
        "chain_type": chain_type,
        "cdr1": "".join(cdr1_residues),
        "cdr2": "".join(cdr2_residues),
        "cdr3": "".join(cdr3_residues),
        "cdr1_seq_range": [min(cdr1_seq_indices), max(cdr1_seq_indices)] if cdr1_seq_indices else None,
        "cdr2_seq_range": [min(cdr2_seq_indices), max(cdr2_seq_indices)] if cdr2_seq_indices else None,
        "cdr3_seq_range": [min(cdr3_seq_indices), max(cdr3_seq_indices)] if cdr3_seq_indices else None,
        "fr2_contacts": "".join(fr2_residues),
        "de_loop": "".join(de_loop_residues),
        "fr3_contacts": "".join(fr3_residues),
        "fr4_contacts": "".join(fr4_residues),
    })

print(json.dumps(output))
'''
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout for large batches
        )
        
        if result.returncode != 0:
            print(f"[CDR Annotator] Batch ANARCII error: {result.stderr[:500]}")
            return _fallback_annotate_individually(pdb_paths)
        
        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            print("[CDR Annotator] Empty output from ANARCII")
            return _fallback_annotate_individually(pdb_paths)
        
        try:
            anarcii_results = json.loads(stdout)
        except json.JSONDecodeError as e:
            print(f"[CDR Annotator] JSON parse error: {e}")
            print(f"[CDR Annotator] Raw output (first 500 chars): {stdout[:500]}")
            return _fallback_annotate_individually(pdb_paths)
        
    except subprocess.TimeoutExpired:
        print("[CDR Annotator] Batch ANARCII timeout")
        return _fallback_annotate_individually(pdb_paths)
    except Exception as e:
        print(f"[CDR Annotator] Batch error: {e}")
        return _fallback_annotate_individually(pdb_paths)
    finally:
        Path(seq_file).unlink(missing_ok=True)

    if not isinstance(anarcii_results, list):
        print(f"[CDR Annotator] Unexpected ANARCII batch payload type: {type(anarcii_results).__name__}")
        return _fallback_annotate_individually(pdb_paths)
    
    print(f"[CDR Annotator] ANARCII returned {len(anarcii_results)} results")
    
    # Step 3: Map results back to paths
    # First, create annotation objects for each PDB
    annotations = {}
    for pdb_path in path_to_chains:
        chain_info = path_to_chains[pdb_path]
        has_light = chain_info.get('L') is not None
        annotations[pdb_path] = CDRAnnotation(
            antibody_type="fab" if has_light else "vhh",
            binder_length=chain_info.get('length', 0)
        )
    
    # Then, populate CDR data from ANARCII results
    for i, (pdb_path, expected_chain_type) in enumerate(seq_to_info):
        if i >= len(anarcii_results):
            break
        
        result = anarcii_results[i]
        chain_type = result.get("chain_type", "")
        annotation = annotations[pdb_path]
        
        # Map chain types: H/B/G -> H fields, L/K/A/D -> L fields
        if chain_type in ("H", "B", "G"):  # Heavy chain or TCR beta/gamma
            annotation.cdr_h1 = result.get("cdr1", "")
            annotation.cdr_h2 = result.get("cdr2", "")
            annotation.cdr_h3 = result.get("cdr3", "")
            annotation.cdr_h1_length = len(annotation.cdr_h1) if annotation.cdr_h1 else None
            annotation.cdr_h2_length = len(annotation.cdr_h2) if annotation.cdr_h2 else None
            annotation.cdr_h3_length = len(annotation.cdr_h3) if annotation.cdr_h3 else None
            # FR contact hotspots (VHH/heavy chain only - Zavrtanik et al. 2018)
            annotation.fr2_contacts = result.get("fr2_contacts", "")
            annotation.de_loop = result.get("de_loop", "")
            annotation.fr3_contacts = result.get("fr3_contacts", "")
            annotation.fr4_contacts = result.get("fr4_contacts", "")
            # Extract raw sequential ranges for seamless frontend mapping
            if result.get("cdr1_seq_range"):
                annotation.cdr_h1_seq_range = tuple(result["cdr1_seq_range"])
            if result.get("cdr2_seq_range"):
                annotation.cdr_h2_seq_range = tuple(result["cdr2_seq_range"])
            if result.get("cdr3_seq_range"):
                annotation.cdr_h3_seq_range = tuple(result["cdr3_seq_range"])
                
            # Mark as TCR if detected
            if chain_type in ("B", "G"):
                annotation.antibody_type = "tcr"
        elif chain_type in ("L", "K", "A", "D"):  # Light chain or TCR alpha/delta
            annotation.cdr_l1 = result.get("cdr1", "")
            annotation.cdr_l2 = result.get("cdr2", "")
            annotation.cdr_l3 = result.get("cdr3", "")
            annotation.cdr_l1_length = len(annotation.cdr_l1) if annotation.cdr_l1 else None
            annotation.cdr_l2_length = len(annotation.cdr_l2) if annotation.cdr_l2 else None
            annotation.cdr_l3_length = len(annotation.cdr_l3) if annotation.cdr_l3 else None
            
            # Extract raw sequential ranges for seamless frontend mapping
            if result.get("cdr1_seq_range"):
                annotation.cdr_l1_seq_range = tuple(result["cdr1_seq_range"])
            if result.get("cdr2_seq_range"):
                annotation.cdr_l2_seq_range = tuple(result["cdr2_seq_range"])
            if result.get("cdr3_seq_range"):
                annotation.cdr_l3_seq_range = tuple(result["cdr3_seq_range"])
                
            if chain_type in ("A", "D"):
                annotation.antibody_type = "tcr"
            elif annotation.antibody_type != "tcr":
                annotation.antibody_type = "fab"  # Has light chain
    
    return annotations

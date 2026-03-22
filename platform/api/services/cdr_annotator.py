"""
CDR Annotator Service - Extract CDR sequences and lengths using ANARCII.

Runs ANARCII (deep learning antibody numbering) from the antibody_tools.sif container
to annotate antibody designs with CDR region information.
"""

import subprocess
import tempfile
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

from services.anarcii_runtime import (
    ANARCIIRuntime,
    build_apptainer_exec_command,
    get_default_anarcii_batch_size,
    get_default_anarcii_cpu_threads,
    resolve_anarcii_runtime,
)

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
    
    # IMGT position ranges (base IMGT positions; insertion-bearing residues remain
    # part of the extracted loop sequence even when the displayed endpoints are
    # the classic anchor positions)
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


IMGTLowerUpper = Tuple[Tuple[int, str], Tuple[int, str]]
IMGT_CDR_ANCHORS: Dict[str, IMGTLowerUpper] = {
    "cdr1": ((26, ""), (39, "")),
    "cdr2": ((55, ""), (66, "")),
    "cdr3": ((104, ""), (118, "")),
}
FR2_POSITIONS = {37, 42, 44, 45, 47}
DE_LOOP_RANGE = (72, 75)
FR3_RANGE = (82, 87)
FR4_RANGE = (101, 103)
VARIABLE_DOMAIN_MIN_LENGTH = 70
VARIABLE_DOMAIN_MAX_LENGTH = 260


def _cpu_only_runtime(container_path: Path) -> ANARCIIRuntime:
    return ANARCIIRuntime(mode="cpu", gpu_id=None, reason="cpu fallback", container_path=container_path)


def _normalize_insertion_code(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).strip()
    if normalized in {"", "-"}:
        return ""
    return normalized


def _position_key(pos: Any, insertion: Any) -> Tuple[int, str]:
    return int(pos), _normalize_insertion_code(insertion)


def _position_between(
    current: Tuple[int, str],
    lower: Tuple[int, str],
    upper: Tuple[int, str],
) -> bool:
    return lower < current < upper


def _collect_numbered_residues(numbering: List[Any]) -> List[Dict[str, Any]]:
    residues: List[Dict[str, Any]] = []
    seq_idx = -1
    for entry in numbering:
        try:
            (pos, insertion), aa = entry
        except Exception:
            continue
        if aa == "-":
            continue
        seq_idx += 1
        residues.append(
            {
                "pos": int(pos),
                "insertion": _normalize_insertion_code(insertion),
                "aa": aa,
                "seq_idx": seq_idx,
                "key": _position_key(pos, insertion),
            }
        )
    return residues


def _build_loop_payload(numbering: List[Any]) -> Dict[str, Any]:
    residues = _collect_numbered_residues(numbering)
    output: Dict[str, Any] = {}

    for loop_name, (lower, upper) in IMGT_CDR_ANCHORS.items():
        lower_key = _position_key(*lower)
        upper_key = _position_key(*upper)
        loop_residues = [entry for entry in residues if _position_between(entry["key"], lower_key, upper_key)]
        positions = [entry["pos"] for entry in loop_residues]
        seq_indices = [entry["seq_idx"] for entry in loop_residues]
        loop_index = loop_name[-1]
        output[f"cdr{loop_index}"] = "".join(entry["aa"] for entry in loop_residues)
        output[f"cdr{loop_index}_range"] = [min(positions), max(positions)] if positions else None
        output[f"cdr{loop_index}_seq_range"] = [min(seq_indices), max(seq_indices)] if seq_indices else None

    output["fr2_contacts"] = "".join(entry["aa"] for entry in residues if entry["pos"] in FR2_POSITIONS)
    output["de_loop"] = "".join(entry["aa"] for entry in residues if DE_LOOP_RANGE[0] <= entry["pos"] <= DE_LOOP_RANGE[1])
    output["fr3_contacts"] = "".join(entry["aa"] for entry in residues if FR3_RANGE[0] <= entry["pos"] <= FR3_RANGE[1])
    output["fr4_contacts"] = "".join(entry["aa"] for entry in residues if FR4_RANGE[0] <= entry["pos"] <= FR4_RANGE[1])
    return output


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
        if 'H' not in found_chains and chain_id_upper == 'H' and VARIABLE_DOMAIN_MIN_LENGTH <= len(seq) <= VARIABLE_DOMAIN_MAX_LENGTH:
            print(f"[CDR Annotator] Chain {chain_id} identified as H by chain ID")
            found_chains['H'] = chain_id
        if 'L' not in found_chains and chain_id_upper in {'L', 'K'} and VARIABLE_DOMAIN_MIN_LENGTH <= len(seq) <= VARIABLE_DOMAIN_MAX_LENGTH:
            print(f"[CDR Annotator] Chain {chain_id} identified as L by chain ID")
            found_chains['L'] = chain_id

    # If found at least one chain by signature, return
    if found_chains:
        return found_chains

    # Conservative fallback: only accept an unlabeled single variable-domain-sized
    # chain. Do not guess a second light chain from a target chain.
    candidates = [chain_id for chain_id, seq in sequences.items() if VARIABLE_DOMAIN_MIN_LENGTH <= len(seq) <= VARIABLE_DOMAIN_MAX_LENGTH]
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
    
    Returns dict with chain type and CDR regions extracted from ANARCII IMGT
    numbering using numbering-aware loop boundaries.
    """
    container_path = get_container_path("antibody_tools.sif")
    
    if not container_path.exists():
        logger.error(f"[CDR Annotator] Container not found: {container_path}")
        return None

    runtime = resolve_anarcii_runtime(container_path=container_path)
    logger.info(
        "[CDR Annotator] Single ANARCII runtime=%s gpu=%s (%s)",
        runtime.mode,
        runtime.gpu_id,
        runtime.reason,
    )

    def _build_cmd(selected_runtime: ANARCIIRuntime) -> list[str]:
        cpu_mode = selected_runtime.mode != "gpu"
        inner_cmd = [
            "python3",
            "-c",
            f'''
import json
from anarcii import Anarcii

seq = {json.dumps(sequence)}
numberer = Anarcii(seq_type='unknown', mode='accuracy', cpu={str(cpu_mode)}, ncpu=1, batch_size=32)
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
    
    def normalize_insertion(value):
        if value is None:
            return ""
        value = str(value).strip()
        return "" if value in ("", "-") else value

    def position_key(pos, insertion):
        return (int(pos), normalize_insertion(insertion))

    def between(current, lower, upper):
        return lower < current < upper

    anchors = {{
        "cdr1": ((26, ""), (39, "")),
        "cdr2": ((55, ""), (66, "")),
        "cdr3": ((104, ""), (118, "")),
    }}

    residues = []
    seq_idx = -1
    for (pos, insertion), aa in numbering:
        if aa == "-":
            continue
        seq_idx += 1
        residues.append({{
            "pos": int(pos),
            "aa": aa,
            "seq_idx": seq_idx,
            "key": position_key(pos, insertion),
        }})

    payload = {{}}
    for loop_name, (lower, upper) in anchors.items():
        lower_key = position_key(*lower)
        upper_key = position_key(*upper)
        loop_residues = [entry for entry in residues if between(entry["key"], lower_key, upper_key)]
        positions = [entry["pos"] for entry in loop_residues]
        seq_indices = [entry["seq_idx"] for entry in loop_residues]
        idx = loop_name[-1]
        payload[f"cdr{{idx}}"] = "".join(entry["aa"] for entry in loop_residues)
        payload[f"cdr{{idx}}_range"] = [min(positions), max(positions)] if positions else None
        payload[f"cdr{{idx}}_seq_range"] = [min(seq_indices), max(seq_indices)] if seq_indices else None

    payload["scheme"] = data.get("scheme", "imgt")
    output[chain_type] = payload

print(json.dumps(output))
''',
        ]
        return build_apptainer_exec_command(selected_runtime, inner_cmd)

    try:
        result = subprocess.run(
            _build_cmd(runtime),
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0 and runtime.mode == "gpu":
            logger.warning(
                "[CDR Annotator] GPU ANARCII failed on gpu=%s, retrying on CPU: %s",
                runtime.gpu_id,
                (result.stderr or "").strip()[:300],
            )
            runtime = _cpu_only_runtime(container_path)
            result = subprocess.run(
                _build_cmd(runtime),
                capture_output=True,
                text=True,
                timeout=120,
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
    
    This is much faster than calling annotate_pdb one-by-one because it:
    1. Extracts all sequences first (CPU-bound, fast)
    2. Runs ANARCII once with all sequences batched
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
    
    requested_batch_size = max(1, int(batch_size or get_default_anarcii_batch_size()))
    cpu_threads = get_default_anarcii_cpu_threads()
    runtime = resolve_anarcii_runtime(container_path=container_path)
    print(
        f"[CDR Annotator] Extracted {len(all_sequences)} chains from {len(path_to_chains)} PDBs, "
        f"running batched ANARCII via {runtime.mode}"
        + (f" on GPU {runtime.gpu_id}" if runtime.gpu_id is not None else "")
        + f" ({runtime.reason})..."
    )
    
    # Step 2: Run ANARCII in batch using temp file for sequences
    # Write sequences to temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(all_sequences, f)
        seq_file = f.name
    
    def _build_batch_cmd(selected_runtime: ANARCIIRuntime) -> list[str]:
        cpu_mode = selected_runtime.mode != "gpu"
        inner_script = """
import json
import sys

# Read sequences from temp file
with open("/tmp/sequences.json") as f:
    sequences = json.load(f)

from anarcii import Anarcii

# Use a large batch on CPU or GPU, with explicit fallback mode selection.
numberer = Anarcii(
    seq_type='unknown',
    mode='accuracy',
    batch_size=__BATCH_SIZE__,
    cpu=__CPU_MODE__,
    ncpu=__NCPU__,
)  # Auto-detect antibody vs TCR
results = numberer.number(sequences) or []

# FR contact hotspot IMGT positions (Zavrtanik et al. 2018)
FR2_POSITIONS = {37, 42, 44, 45, 47}  # VHH tetrad + contacts
DE_LOOP_RANGE = (72, 75)              # DE loop
FR3_RANGE = (82, 87)                  # FR3 contacts
FR4_RANGE = (101, 103)                # C-terminal contacts
ANCHORS = {
    "cdr1": ((26, ""), (39, "")),
    "cdr2": ((55, ""), (66, "")),
    "cdr3": ((104, ""), (118, "")),
}

def normalize_insertion(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value in ("", "-") else value

def position_key(pos, insertion):
    return (int(pos), normalize_insertion(insertion))

def between(current, lower, upper):
    return lower < current < upper

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

    residues = []
    seq_idx = -1

    for (pos, insertion), aa in numbering:
        if aa == "-":
            continue
        seq_idx += 1
        residues.append({
            "pos": int(pos),
            "aa": aa,
            "seq_idx": seq_idx,
            "key": position_key(pos, insertion),
        })

    payload = {}
    for loop_name, (lower, upper) in ANCHORS.items():
        lower_key = position_key(*lower)
        upper_key = position_key(*upper)
        loop_residues = [entry for entry in residues if between(entry["key"], lower_key, upper_key)]
        positions = [entry["pos"] for entry in loop_residues]
        seq_indices = [entry["seq_idx"] for entry in loop_residues]
        idx = loop_name[-1]
        payload[f"cdr{idx}"] = "".join(entry["aa"] for entry in loop_residues)
        payload[f"cdr{idx}_range"] = [min(positions), max(positions)] if positions else None
        payload[f"cdr{idx}_seq_range"] = [min(seq_indices), max(seq_indices)] if seq_indices else None

    output.append({
        "chain_type": chain_type,
        "cdr1": payload["cdr1"],
        "cdr2": payload["cdr2"],
        "cdr3": payload["cdr3"],
        "cdr1_range": payload["cdr1_range"],
        "cdr2_range": payload["cdr2_range"],
        "cdr3_range": payload["cdr3_range"],
        "cdr1_seq_range": payload["cdr1_seq_range"],
        "cdr2_seq_range": payload["cdr2_seq_range"],
        "cdr3_seq_range": payload["cdr3_seq_range"],
        "fr2_contacts": "".join(entry["aa"] for entry in residues if entry["pos"] in FR2_POSITIONS),
        "de_loop": "".join(entry["aa"] for entry in residues if DE_LOOP_RANGE[0] <= entry["pos"] <= DE_LOOP_RANGE[1]),
        "fr3_contacts": "".join(entry["aa"] for entry in residues if FR3_RANGE[0] <= entry["pos"] <= FR3_RANGE[1]),
        "fr4_contacts": "".join(entry["aa"] for entry in residues if FR4_RANGE[0] <= entry["pos"] <= FR4_RANGE[1]),
    })

print(json.dumps(output))
""".replace("__BATCH_SIZE__", str(requested_batch_size)).replace(
            "__CPU_MODE__", "True" if cpu_mode else "False"
        ).replace("__NCPU__", str(cpu_threads if cpu_mode else 1))
        inner_cmd = [
            "python3",
            "-c",
            inner_script,
        ]
        cmd = ["apptainer", "exec"]
        if selected_runtime.mode == "gpu" and selected_runtime.gpu_id is not None:
            cmd.extend(
                [
                    "--nv",
                    "--env",
                    "CUDA_DEVICE_ORDER=PCI_BUS_ID",
                    "--env",
                    f"CUDA_VISIBLE_DEVICES={selected_runtime.gpu_id}",
                ]
            )
        cmd.extend(["--bind", f"{seq_file}:/tmp/sequences.json", str(container_path)])
        cmd.extend(inner_cmd)
        return cmd

    try:
        result = subprocess.run(
            _build_batch_cmd(runtime),
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout for large batches
        )

        if result.returncode != 0 and runtime.mode == "gpu":
            logger.warning(
                "[CDR Annotator] GPU batch ANARCII failed on gpu=%s, retrying on CPU: %s",
                runtime.gpu_id,
                (result.stderr or "").strip()[:300],
            )
            runtime = _cpu_only_runtime(container_path)
            result = subprocess.run(
                _build_batch_cmd(runtime),
                capture_output=True,
                text=True,
                timeout=600,
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
            if result.get("cdr1_range"):
                annotation.cdr_h1_range = tuple(result["cdr1_range"])
            if result.get("cdr2_range"):
                annotation.cdr_h2_range = tuple(result["cdr2_range"])
            if result.get("cdr3_range"):
                annotation.cdr_h3_range = tuple(result["cdr3_range"])
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
            if result.get("cdr1_range"):
                annotation.cdr_l1_range = tuple(result["cdr1_range"])
            if result.get("cdr2_range"):
                annotation.cdr_l2_range = tuple(result["cdr2_range"])
            if result.get("cdr3_range"):
                annotation.cdr_l3_range = tuple(result["cdr3_range"])
            
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

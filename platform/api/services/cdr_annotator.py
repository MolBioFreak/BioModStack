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
    
    # IMGT position ranges
    cdr_h1_range: Optional[tuple] = None
    cdr_h2_range: Optional[tuple] = None
    cdr_h3_range: Optional[tuple] = None
    
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
        }


# Project root for container paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def extract_sequence_from_pdb(pdb_path: str, chain_id: Optional[str] = None) -> Dict[str, str]:
    """
    Extract amino acid sequences from PDB file.
    
    Returns dict of {chain_id: sequence}
    """
    from Bio.PDB import PDBParser
    from Bio.SeqUtils import seq1
    
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("protein", pdb_path)
    
    sequences = {}
    for model in structure:
        for chain in model:
            if chain_id and chain.id != chain_id:
                continue
            
            residues = []
            for res in chain.get_residues():
                if res.id[0] == " ":  # Standard residue, not heteroatom
                    try:
                        aa = seq1(res.resname)
                        residues.append(aa)
                    except:
                        pass
            
            if residues:
                sequences[chain.id] = "".join(residues)
    
    return sequences


def identify_binder_chains(sequences: Dict[str, str], pdb_path: str) -> Dict[str, str]:
    """
    Identify which chains are antibody chains (H and/or L).
    
    For VHH/nanobody: Returns only H chain
    For Fab/scFv: Returns both H and L chains
    
    Uses sequence signatures to detect antibody chains:
    - VHH/VH typically starts with QVQLV, EVQLV, QVKLV (framework 1)
    - VL/VK typically starts with DIVMT, DIQMT, EIVLT
    
    Returns dict of {chain_type: chain_id} where chain_type is 'H' or 'L'
    """
    # Common VH/VHH framework 1 signatures
    vh_signatures = ["QVQLV", "EVQLV", "QVKLV", "QVQLQ", "EVQLQ", "QVTLK", "QVQLK"]
    vl_signatures = ["DIVMT", "DIQMT", "EIVLT", "DIVLT", "EIVMT", "QSVLT"]
    
    found_chains = {}  # {chain_type: chain_id}
    
    # Detect by sequence signature (most reliable)
    for chain_id, seq in sequences.items():
        seq_upper = seq.upper()[:10]  # Check first 10 residues
        
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
    
    # If found at least one chain by signature, return
    if found_chains:
        return found_chains
    
    # Fallback: check for chain in typical antibody length range
    for chain_id, seq in sequences.items():
        if 100 <= len(seq) <= 150:  # VHH/Fab typical range
            print(f"[CDR Annotator] Chain {chain_id} selected by length ({len(seq)} AA)")
            return {'H': chain_id}  # Assume heavy if unsure
    
    # Last resort: if only two chains, guess smaller is antibody
    if len(sequences) == 2:
        smallest = min(sequences.keys(), key=lambda k: len(sequences[k]))
        return {'H': smallest}
    
    # Final fallback: any chain
    if sequences:
        first_chain = list(sequences.keys())[0]
        return {'H': first_chain}
    
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
    container_path = PROJECT_ROOT / "apptainer" / "antibody_tools.sif"
    
    if not container_path.exists():
        print(f"[CDR Annotator] Container not found: {container_path}")
        return None
    
    try:
        # Run ANARCII inside container with correct API
        cmd = [
            "apptainer", "exec", str(container_path),
            "python3", "-c", f'''
import json
from anarcii import Anarcii

seq = "{sequence}"
numberer = Anarcii()
result = numberer.number([seq])

output = {{}}
for seq_name, data in result.items():
    chain_type = data.get("chain_type", "")
    if not chain_type:
        continue
    
    numbering = data.get("numbering", [])
    
    # Extract CDRs based on IMGT position ranges
    cdr1_residues = []
    cdr2_residues = []
    cdr3_residues = []
    
    for (pos, insertion), aa in numbering:
        if aa == "-":
            continue
        if 27 <= pos <= 38:
            cdr1_residues.append(aa)
        elif 56 <= pos <= 65:
            cdr2_residues.append(aa)
        elif 105 <= pos <= 117:
            cdr3_residues.append(aa)
    
    output[chain_type] = {{
        "cdr1": "".join(cdr1_residues),
        "cdr2": "".join(cdr2_residues),
        "cdr3": "".join(cdr3_residues),
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


def annotate_pdb(pdb_path: str) -> Optional[CDRAnnotation]:
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
    
    # Identify antibody chains (H and/or L)
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
        # ANARCII returns chain type based on sequence, but we already know from structure
        # The result key will be 'H' or 'L' based on what ANARCII detects
        if "H" in anarcii_result:
            h_data = anarcii_result["H"]
            annotation.cdr_h1 = h_data.get("cdr1", "")
            annotation.cdr_h2 = h_data.get("cdr2", "")
            annotation.cdr_h3 = h_data.get("cdr3", "")
            annotation.cdr_h1_length = len(annotation.cdr_h1) if annotation.cdr_h1 else None
            annotation.cdr_h2_length = len(annotation.cdr_h2) if annotation.cdr_h2 else None
            annotation.cdr_h3_length = len(annotation.cdr_h3) if annotation.cdr_h3 else None
        
        if "L" in anarcii_result:
            l_data = anarcii_result["L"]
            annotation.cdr_l1 = l_data.get("cdr1", "")
            annotation.cdr_l2 = l_data.get("cdr2", "")
            annotation.cdr_l3 = l_data.get("cdr3", "")
            annotation.cdr_l1_length = len(annotation.cdr_l1) if annotation.cdr_l1 else None
            annotation.cdr_l2_length = len(annotation.cdr_l2) if annotation.cdr_l2 else None
            annotation.cdr_l3_length = len(annotation.cdr_l3) if annotation.cdr_l3 else None
            annotation.antibody_type = "fab"  # Has light chain
    
    return annotation


def batch_annotate_pdbs(pdb_paths: list, batch_size: int = 500) -> Dict[str, CDRAnnotation]:
    """
    Batch annotate multiple PDB files for CDR regions.
    
    This is ~30x faster than calling annotate_pdb one-by-one because it:
    1. Extracts all sequences first (CPU-bound, fast)
    2. Runs ANARCII once with all sequences batched (CPU inference with 24 cores)
    3. Maps results back to PDB paths
    
    Returns dict of {pdb_path: CDRAnnotation}
    """
    container_path = PROJECT_ROOT / "apptainer" / "antibody_tools.sif"
    
    if not container_path.exists():
        print(f"[CDR Annotator] Container not found: {container_path}")
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
numberer = Anarcii(cpu=True, batch_size=500, ncpu=24)
results = numberer.number(sequences)

output = []
for seq_name, data in results.items():
    chain_type = data.get("chain_type", "")
    numbering = data.get("numbering", [])
    
    cdr1_residues = []
    cdr2_residues = []
    cdr3_residues = []
    
    for (pos, insertion), aa in numbering:
        if aa == "-":
            continue
        if 27 <= pos <= 38:
            cdr1_residues.append(aa)
        elif 56 <= pos <= 65:
            cdr2_residues.append(aa)
        elif 105 <= pos <= 117:
            cdr3_residues.append(aa)
    
    output.append({
        "chain_type": chain_type,
        "cdr1": "".join(cdr1_residues),
        "cdr2": "".join(cdr2_residues),
        "cdr3": "".join(cdr3_residues),
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
            return {}
        
        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            print("[CDR Annotator] Empty output from ANARCII")
            return {}
        
        try:
            anarcii_results = json.loads(stdout)
        except json.JSONDecodeError as e:
            print(f"[CDR Annotator] JSON parse error: {e}")
            print(f"[CDR Annotator] Raw output (first 500 chars): {stdout[:500]}")
            return {}
        
    except subprocess.TimeoutExpired:
        print("[CDR Annotator] Batch ANARCII timeout")
        return {}
    except Exception as e:
        print(f"[CDR Annotator] Batch error: {e}")
        return {}
    finally:
        Path(seq_file).unlink(missing_ok=True)
    
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
        
        if chain_type == "H":
            annotation.cdr_h1 = result.get("cdr1", "")
            annotation.cdr_h2 = result.get("cdr2", "")
            annotation.cdr_h3 = result.get("cdr3", "")
            annotation.cdr_h1_length = len(annotation.cdr_h1) if annotation.cdr_h1 else None
            annotation.cdr_h2_length = len(annotation.cdr_h2) if annotation.cdr_h2 else None
            annotation.cdr_h3_length = len(annotation.cdr_h3) if annotation.cdr_h3 else None
        elif chain_type == "L":
            annotation.cdr_l1 = result.get("cdr1", "")
            annotation.cdr_l2 = result.get("cdr2", "")
            annotation.cdr_l3 = result.get("cdr3", "")
            annotation.cdr_l1_length = len(annotation.cdr_l1) if annotation.cdr_l1 else None
            annotation.cdr_l2_length = len(annotation.cdr_l2) if annotation.cdr_l2 else None
            annotation.cdr_l3_length = len(annotation.cdr_l3) if annotation.cdr_l3 else None
            annotation.antibody_type = "fab"  # Has light chain
    
    return annotations

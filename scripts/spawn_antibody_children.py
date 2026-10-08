#!/usr/bin/env python3
"""
Spawn child validation jobs for antibody designs.
Called by parent Nextflow job after FAMPNN completes.

Each child job enters the GPU orchestrator queue and gets
bin-packed across available GPUs for parallel processing.
"""
import argparse
import requests
import json
import sys
from pathlib import Path


def extract_sequence_from_pdb(pdb_path: Path) -> str:
    """Extract amino acid sequence from PDB file."""
    aa_codes = {
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    }
    
    sequence = []
    seen_residues = set()
    
    with open(pdb_path) as f:
        for line in f:
            if line.startswith('ATOM') and line[12:16].strip() == 'CA':
                res_name = line[17:20].strip()
                res_num = line[22:26].strip()
                chain = line[21]
                key = f"{chain}_{res_num}"
                
                if key not in seen_residues and res_name in aa_codes:
                    seen_residues.add(key)
                    sequence.append(aa_codes[res_name])
    
    return ''.join(sequence) if sequence else "AAAA"


def spawn_children(
    parent_job_id: str,
    pdb_dir: str,
    batch_name: str,
    msa_path: str = None,
    api_url: str = "http://localhost:8000"
):
    """
    Create child jobs for each PDB design.
    
    Args:
        parent_job_id: ID of parent antibody job
        pdb_dir: Directory containing FAMPNN output PDBs
        batch_name: Name for the batch (appears in dashboard)
        msa_path: Path to shared MSA file (optional)
        api_url: Backend API URL
    """
    pdbs = sorted(Path(pdb_dir).glob("*.pdb"))
    
    if not pdbs:
        print(f"[SPAWN] No PDB files found in {pdb_dir}", file=sys.stderr)
        return
    
    print(f"[SPAWN] Creating {len(pdbs)} child jobs for parent {parent_job_id}")
    
    created = 0
    failed = 0
    
    for i, pdb in enumerate(pdbs):
        try:
            sequence = extract_sequence_from_pdb(pdb)
            seq_length = len(sequence)
            
            job_data = {
                "name": f"{batch_name}_design_{i+1:03d}",
                "model_id": "antibody_child",
                "mode": "validation",
                "params": {
                    "pdb_path": str(pdb.absolute()),
                    "sequence": sequence,
                    "msa_path": msa_path or "",
                    "design_index": i + 1,
                },
                "batch_id": parent_job_id,
                "batch_name": batch_name,
                "parent_job_id": parent_job_id,
                "sequence_length": seq_length,
            }
            
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN] Created child job {job_id} for {pdb.name} (seq len: {seq_length})")
                created += 1
            else:
                print(f"[SPAWN] Failed to create job for {pdb.name}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN] Error processing {pdb.name}: {e}", file=sys.stderr)
            failed += 1
    
    print(f"[SPAWN] Complete: {created} created, {failed} failed")
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spawn antibody child validation jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--pdb_dir", required=True, help="Directory with FAMPNN PDB outputs")
    parser.add_argument("--batch_name", required=True, help="Batch name for dashboard")
    parser.add_argument("--msa_path", default="", help="Path to shared MSA file")
    parser.add_argument("--api_url", default="http://localhost:8000", help="API URL")
    
    args = parser.parse_args()
    
    spawn_children(
        parent_job_id=args.parent_job_id,
        pdb_dir=args.pdb_dir,
        batch_name=args.batch_name,
        msa_path=args.msa_path,
        api_url=args.api_url
    )

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

import re
from collections import defaultdict

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

def group_pdbs_by_backbone(pdb_files):
    """
    Group PDBs by their parent backbone ID.
    Expected format: {backbone_id}_seq_{seq_id}.pdb
    Example: antibody_0_seq_1.pdb -> key: antibody_0
    """
    groups = defaultdict(list)
    for pdb in pdb_files:
        # Regex to find backbone ID (everything before _seq_)
        # Matches: job_name_0_seq_1.pdb -> job_name_0
        match = re.match(r"(.+)_seq_\d+\.pdb", pdb.name)
        if match:
            backbone_id = match.group(1)
            groups[backbone_id].append(pdb)
        else:
            # Fallback for non-matching names, try splitting by _seq_
            if "_seq_" in pdb.name:
                backbone_id = pdb.name.split("_seq_")[0]
                groups[backbone_id].append(pdb)
            else:
                # Last resort: entire stem is the group (single item group)
                groups[pdb.stem].append(pdb)
    return groups


def spawn_children(
    parent_job_id: str,
    pdb_dir: str,
    batch_name: str,
    msa_path: str = None,
    api_url: str = "http://localhost:8000"
):
    """
    Create filtered child jobs for each PDB design.
    Groups designs by backbone to reduce job queue size.
    """
    pdbs = sorted(Path(pdb_dir).glob("*.pdb"))
    
    if not pdbs:
        print(f"[SPAWN] No PDB files found in {pdb_dir}", file=sys.stderr)
        return
    
    # Group by backbone
    groups = group_pdbs_by_backbone(pdbs)
    print(f"[SPAWN] Found {len(pdbs)} designs from {len(groups)} unique backbones")
    
    created = 0
    failed = 0
    
    for i, (backbone_id, group_pdbs) in enumerate(groups.items()):
        try:
            # Prepare payload for batch job
            # We pass a LIST of PDB paths to the child workflow
            pdb_paths = [str(p.absolute()) for p in group_pdbs]
            
            # Metadata: Calculate total sequence length (for roughly estimating effort)
            # Just take the first one as representative
            example_pdb = group_pdbs[0]
            sequence = extract_sequence_from_pdb(example_pdb)
            seq_length = len(sequence)
            
            job_data = {
                "name": f"{batch_name}_{backbone_id}",
                "model_id": "antibody_child",
                "mode": "validation_batch", # Just label, workflow uses defaults
                "params": {
                    "pdb_paths": ",".join(pdb_paths), # Pass as comma-separated string
                    "msa_path": msa_path or "",
                    "backbone_id": backbone_id,
                    "design_count": len(group_pdbs),
                    "rfd_mode": "antibody_child"
                },
                "batch_id": parent_job_id,
                "batch_name": batch_name,
                "parent_job_id": parent_job_id,
                "sequence_length": seq_length * len(group_pdbs), # Total effort
            }
            
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN] Created child job {job_id} for backbone {backbone_id} ({len(group_pdbs)} seqs)")
                created += 1
            else:
                print(f"[SPAWN] Failed to create job for {backbone_id}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN] Error processing backbone {backbone_id}: {e}", file=sys.stderr)
            failed += 1
    
    print(f"[SPAWN] Complete: {created} batches created, {failed} failed")
    
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

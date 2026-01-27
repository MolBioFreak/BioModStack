#!/usr/bin/env python3
"""
Spawn child validation jobs for antibody designs.
Called by parent Nextflow job after FAMPNN completes.

Each child job enters the GPU orchestrator queue and gets
bin-packed across available GPUs for parallel processing.

Batching Strategy:
- Uses seqs_per_boltz_job ratio to determine how many sequences per Boltz job
- Higher ratio = fewer Boltz jobs, fewer model loads
- Lower ratio = more parallelism, more model loads

Supports RESUME: If children already exist and completed, reuses them
instead of spawning new ones.
"""
import argparse
import requests
import json
import os
import sys
from pathlib import Path
from math import ceil
DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


import re
from collections import defaultdict


def check_existing_children(parent_job_id: str, stage: str, api_url: str, batch_name: str = None):
    """
    Check if completed children already exist for this parent job and stage.
    
    Args:
        parent_job_id: Parent job ID
        stage: Child stage filter (e.g., 'boltz2')
        api_url: API base URL
        batch_name: Optional batch name to search by (for resume scenarios)
    
    Returns:
        tuple: (bool all_done, list completed_children, dict child_status)
    """
    try:
        params = {"stage": stage}
        if batch_name:
            params["batch_name"] = batch_name
        
        resp = requests.get(
            f"{api_url}/api/jobs/{parent_job_id}/children/status",
            params=params,
            timeout=10
        )
        
        if not resp.ok:
            return False, [], {}
        
        data = resp.json()
        all_done = data.get("all_done", False)
        child_output_dirs = data.get("child_output_dirs", [])
        
        # Build list of completed children with their output directories
        # Note: child_output_dirs only contains directories for COMPLETED children
        completed_children = []
        for i, output_dir in enumerate(child_output_dirs):
            completed_children.append({
                "job_id": f"completed_{i}",  # We don't need actual IDs for resume
                "output_dir": output_dir,
                "index": i
            })
        
        return all_done, completed_children, data
        
    except Exception as e:
        print(f"[SPAWN] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


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
    params_json: str = None,
    seqs_per_boltz_job: int = 10,
    api_url: str = DEFAULT_API_URL
):
    """
    Create child validation jobs grouped by seqs_per_boltz_job ratio.
    
    Instead of one job per backbone, we batch sequences to reduce
    Boltz model load/unload cycles.
    
    Args:
        seqs_per_boltz_job: Number of sequences per Boltz child job.
            1 = no batching (one job per sequence)
            10 = 10 sequences per job
            500 = heavy batching
    """
    pdbs = sorted(Path(pdb_dir).glob("*.pdb"))
    
    if not pdbs:
        print(f"[SPAWN] No PDB files found in {pdb_dir}", file=sys.stderr)
        return
    
    # Parse quality settings passed from parent workflow
    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
            print(f"[SPAWN] Forwarding {len(extra_params)} params from parent workflow")
        except json.JSONDecodeError as e:
            print(f"[SPAWN] Warning: Failed to parse params_json: {e}", file=sys.stderr)
    
    # Calculate number of jobs based on ratio
    total_seqs = len(pdbs)
    num_jobs = max(1, ceil(total_seqs / seqs_per_boltz_job))
    chunk_size = ceil(total_seqs / num_jobs)
    
    # =========================================================================
    # RESUME CHECK: See if children already exist and completed
    # If so, skip spawning and return early
    # Pass batch_name to find children from original run if this is a resume
    # =========================================================================
    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, "boltz2", api_url, batch_name=batch_name
    )
    
    existing_count = len(existing_children)
    if existing_count > 0:
        print(f"[SPAWN] Found {existing_count} existing Boltz children for parent {parent_job_id}")
        
        if all_done:
            print(f"[SPAWN] RESUME: All {existing_count} Boltz children already completed! Skipping spawn.")
            return
        else:
            completed = child_status.get("completed", 0)
            running = child_status.get("running", 0)
            pending = child_status.get("pending", 0)
            failed = child_status.get("failed", 0)
            
            print(f"[SPAWN] Existing: {completed} completed, {running} running, {pending} pending, {failed} failed")
            
            if running > 0 or pending > 0:
                print(f"[SPAWN] RESUME: {running + pending} children still in progress. Not spawning duplicates.")
                return
    
    # =========================================================================
    # No existing children or all failed - proceed with fresh spawn
    # =========================================================================
    print(f"[SPAWN] {total_seqs} sequences → {num_jobs} Boltz jobs ({seqs_per_boltz_job} seqs/job ratio)")
    
    created = 0
    failed = 0
    
    for job_idx in range(num_jobs):
        try:
            # Get this job's chunk of PDBs
            start_idx = job_idx * chunk_size
            end_idx = min((job_idx + 1) * chunk_size, total_seqs)
            chunk_pdbs = pdbs[start_idx:end_idx]
            
            if not chunk_pdbs:
                continue
            
            # Prepare payload for batch job
            pdb_paths = [str(p.absolute()) for p in chunk_pdbs]
            
            # Calculate sequence length for VRAM estimation
            # NOTE: Use SINGLE sequence length, NOT total. Boltz processes one at a time.
            # VRAM is dictated by the largest single sequence, not the sum.
            example_pdb = chunk_pdbs[0]
            sequence = extract_sequence_from_pdb(example_pdb)
            seq_length = len(sequence)
            
            job_data = {
                "name": f"{batch_name}_boltz_batch_{job_idx}",
                "model_id": "antibody_child",
                "mode": "validation_batch",
                "params": {
                    "pdb_paths": ",".join(pdb_paths),
                    "msa_path": msa_path or "",
                    "batch_index": job_idx,
                    "design_count": len(chunk_pdbs),
                    "rfd_mode": "antibody_child",
                    # Merge all quality settings from parent
                    **extra_params
                },
                "batch_id": parent_job_id,
                "batch_name": batch_name,
                "parent_job_id": parent_job_id,
                "child_stage": "boltz2",
                "sequence_length": seq_length,  # Single sequence, not multiplied!
            }

            
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN] Created Boltz batch {job_idx} ({len(chunk_pdbs)} seqs): {job_id}")
                created += 1
            else:
                print(f"[SPAWN] Failed to create batch {job_idx}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN] Error creating batch {job_idx}: {e}", file=sys.stderr)
            failed += 1
    
    print(f"[SPAWN] Complete: {created} Boltz batches created, {failed} failed")
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spawn antibody child validation jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--pdb_dir", required=True, help="Directory with FAMPNN PDB outputs")
    parser.add_argument("--batch_name", required=True, help="Batch name for dashboard")
    parser.add_argument("--msa_path", default="", help="Path to shared MSA file")
    parser.add_argument("--params_json", default="", help="JSON string with quality settings from parent")
    parser.add_argument("--seqs_per_boltz_job", type=int, default=10, help="Sequences per Boltz job (1=no batch, higher=more batch)")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    
    args = parser.parse_args()
    
    spawn_children(
        parent_job_id=args.parent_job_id,
        pdb_dir=args.pdb_dir,
        batch_name=args.batch_name,
        msa_path=args.msa_path,
        params_json=args.params_json if args.params_json else None,
        seqs_per_boltz_job=args.seqs_per_boltz_job,
        api_url=args.api_url
    )

#!/usr/bin/env python3
"""
Spawn FAMPNN child jobs for parallel sequence design.

Called by parent workflow to split FAMPNN across multiple GPUs.
Each child job runs FAMPNN for a subset of backbone PDBs and is managed by
the GPU orchestrator for optimal resource allocation.
"""
import argparse
import json
import sys
import requests
from pathlib import Path


def spawn_fampnn_jobs(
    parent_job_id: str,
    pdb_dir: str,
    pdbs_per_job: int,
    seqs_per_design: int,
    batch_name: str,
    params_json: str = None,
    api_url: str = "http://localhost:8000"
):
    """
    Spawn multiple FAMPNN child jobs.
    
    Args:
        parent_job_id: Parent job's ID
        pdb_dir: Directory containing backbone PDBs
        pdbs_per_job: How many PDBs per child job
        seqs_per_design: Sequences to generate per PDB
        batch_name: Human-readable batch name
        params_json: Additional parameters as JSON string
        api_url: API base URL
    """
    # Find all PDBs
    pdb_path = Path(pdb_dir)
    pdbs = sorted(pdb_path.glob("*.pdb"))
    
    if not pdbs:
        print(f"[SPAWN-FAMPNN] No PDBs found in {pdb_dir}", file=sys.stderr)
        return {"status": "error", "message": "No PDBs found"}
    
    # Check for already-completed FAMPNN jobs for this parent
    # This prevents re-spawning on resume
    already_done_pdbs = set()
    try:
        resp = requests.get(
            f"{api_url}/api/jobs",
            params={"parent_job_id": parent_job_id},
            timeout=10
        )
        if resp.ok:
            payload = resp.json()
            existing_jobs = payload.get("jobs", payload) if isinstance(payload, dict) else payload
            for job in existing_jobs:
                # Check if this is a completed FAMPNN child job
                if (job.get("status") == "completed" and 
                    "fampnn" in job.get("name", "").lower() and
                    job.get("params", {}).get("pdb_paths")):
                    # Get PDB basenames from this completed job
                    pdb_paths_str = job["params"].get("pdb_paths", "")
                    for pdb_path_str in pdb_paths_str.split(","):
                        if pdb_path_str:
                            already_done_pdbs.add(Path(pdb_path_str).name)
            if already_done_pdbs:
                print(f"[SPAWN-FAMPNN] Found {len(already_done_pdbs)} PDBs already processed")
    except Exception as e:
        print(f"[SPAWN-FAMPNN] Warning: Could not check existing jobs: {e}", file=sys.stderr)
    
    # Filter out already-processed PDBs
    if already_done_pdbs:
        original_count = len(pdbs)
        pdbs = [p for p in pdbs if p.name not in already_done_pdbs]
        skipped = original_count - len(pdbs)
        if skipped > 0:
            print(f"[SPAWN-FAMPNN] Skipping {skipped} already-processed PDBs")
    
    if not pdbs:
        print(f"[SPAWN-FAMPNN] All PDBs already processed, nothing to spawn")
        return {"status": "complete", "spawned_jobs": 0, "failed_spawns": 0, 
                "total_pdbs": 0, "pdbs_per_job": pdbs_per_job, "child_jobs": [],
                "skipped_pdbs": len(already_done_pdbs)}
    
    total_pdbs = len(pdbs)
    num_jobs = (total_pdbs + pdbs_per_job - 1) // pdbs_per_job
    
    print(f"[SPAWN-FAMPNN] Spawning {num_jobs} FAMPNN jobs for {total_pdbs} PDBs")
    print(f"[SPAWN-FAMPNN] PDBs per job: {pdbs_per_job}, Seqs per design: {seqs_per_design}")
    
    # Parse additional params
    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
        except json.JSONDecodeError:
            print(f"[SPAWN-FAMPNN] Warning: Failed to parse params_json", file=sys.stderr)
    
    created = []
    failed = 0
    
    for i in range(num_jobs):
        start_idx = i * pdbs_per_job
        end_idx = min(start_idx + pdbs_per_job, total_pdbs)
        job_pdbs = pdbs[start_idx:end_idx]
        
        # Convert to absolute paths
        pdb_paths = [str(p.absolute()) for p in job_pdbs]
        
        # VRAM estimate based on CDR loops being designed (NOT entire chain)
        # VHH: 3 CDR loops ~40 AA total, Full H+L: 6 CDR loops ~80 AA total
        is_vhh = extra_params.get('vhh_mode', False) or extra_params.get('antibody_format') == 'VHH'
        estimated_seq_len = 40 if is_vhh else 80
        
        job_data = {
            "name": f"{batch_name}_fampnn_{i}",
            "model_id": "fampnn_child",
            "mode": "sequence_design",
            "params": {
                "rfd_mode": "fampnn_child",  # Critical: routes to fampnn_child block in main.nf
                "pdb_paths": ",".join(pdb_paths),
                "seqs_per_design": seqs_per_design,
                "pdb_count": len(job_pdbs),
                "job_index": i,
                "total_jobs": num_jobs,
                **extra_params
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": batch_name,
            "child_stage": "fampnn",
            "sequence_length": estimated_seq_len,
        }
        
        try:
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN-FAMPNN] Created child job {job_id} ({len(job_pdbs)} PDBs)")
                created.append({
                    "job_id": job_id,
                    "pdb_count": len(job_pdbs),
                    "index": i
                })
            else:
                print(f"[SPAWN-FAMPNN] Failed to create job {i}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN-FAMPNN] Error creating job {i}: {e}", file=sys.stderr)
            failed += 1
    
    result = {
        "status": "complete" if failed == 0 else "partial",
        "spawned_jobs": len(created),
        "failed_spawns": failed,
        "total_pdbs": total_pdbs,
        "pdbs_per_job": pdbs_per_job,
        "child_jobs": created
    }
    
    print(f"[SPAWN-FAMPNN] Complete: {len(created)} jobs created, {failed} failed")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Spawn FAMPNN child jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--pdb_dir", required=True, help="Directory with backbone PDBs")
    parser.add_argument("--pdbs_per_job", type=int, default=5, help="PDBs per child job")
    parser.add_argument("--seqs_per_design", type=int, default=20, help="Sequences per design")
    parser.add_argument("--batch_name", required=True, help="Batch name for display")
    parser.add_argument("--params_json", default=None, help="Additional params as JSON")
    parser.add_argument("--api_url", default="http://localhost:8000", help="API URL")
    parser.add_argument("--output", default="spawn_fampnn_result.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = spawn_fampnn_jobs(
        parent_job_id=args.parent_job_id,
        pdb_dir=args.pdb_dir,
        pdbs_per_job=args.pdbs_per_job,
        seqs_per_design=args.seqs_per_design,
        batch_name=args.batch_name,
        params_json=args.params_json,
        api_url=args.api_url
    )
    
    # Write result
    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)
    
    if result.get("failed_spawns", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

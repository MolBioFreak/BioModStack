#!/usr/bin/env python3
"""
Spawn RFantibody child jobs for parallel backbone generation.

Called by parent workflow to split backbone generation across multiple GPUs.
Each child job runs RFantibody for a subset of designs and is managed by
the GPU orchestrator for optimal resource allocation.
"""
import argparse
import json
import sys
import requests
from pathlib import Path


def spawn_rfantibody_jobs(
    parent_job_id: str,
    total_designs: int,
    designs_per_job: int,
    target_pdb_path: str,
    epitope_residues: str,
    framework_type: str,
    batch_name: str,
    params_json: str = None,
    api_url: str = "http://localhost:8000"
):
    """
    Spawn multiple RFantibody child jobs.
    
    Args:
        parent_job_id: Parent job's ID
        total_designs: Total number of backbones to generate
        designs_per_job: How many designs per child job
        target_pdb_path: Path to target antigen PDB
        epitope_residues: Epitope residue specification
        framework_type: Framework type (standard-fv, nanobody)
        batch_name: Human-readable batch name
        params_json: Additional parameters as JSON string
        api_url: API base URL
    """
    # Calculate number of jobs needed
    num_jobs = (total_designs + designs_per_job - 1) // designs_per_job
    
    print(f"[SPAWN-RFA] Spawning {num_jobs} RFantibody jobs for {total_designs} total designs")
    print(f"[SPAWN-RFA] Designs per job: {designs_per_job}")
    
    # Parse additional params
    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
        except json.JSONDecodeError:
            print(f"[SPAWN-RFA] Warning: Failed to parse params_json", file=sys.stderr)
    
    created = []
    failed = 0
    designs_assigned = 0
    
    for i in range(num_jobs):
        # Calculate designs for this job
        remaining = total_designs - designs_assigned
        job_designs = min(designs_per_job, remaining)
        designs_assigned += job_designs
        
        job_data = {
            "name": f"{batch_name}_rfa_{i}",
            "model_id": "rfantibody_child",
            "mode": "antibody_backbone",
            "params": {
                "rfantibody_num_designs": job_designs,
                "target_pdb": target_pdb_path,
                "epitope_residues": epitope_residues,
                "framework_type": framework_type,
                "job_index": i,
                "total_jobs": num_jobs,
                **extra_params
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": batch_name,
            "child_stage": "rfantibody",
            "sequence_length": 250,  # Approximate for VRAM estimation
        }
        
        try:
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN-RFA] Created child job {job_id} ({job_designs} designs)")
                created.append({
                    "job_id": job_id,
                    "designs": job_designs,
                    "index": i
                })
            else:
                print(f"[SPAWN-RFA] Failed to create job {i}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN-RFA] Error creating job {i}: {e}", file=sys.stderr)
            failed += 1
    
    result = {
        "status": "complete" if failed == 0 else "partial",
        "spawned_jobs": len(created),
        "failed_spawns": failed,
        "total_designs": total_designs,
        "designs_per_job": designs_per_job,
        "child_jobs": created
    }
    
    print(f"[SPAWN-RFA] Complete: {len(created)} jobs created, {failed} failed")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Spawn RFantibody child jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--total_designs", type=int, required=True, help="Total designs to generate")
    parser.add_argument("--designs_per_job", type=int, default=5, help="Designs per child job")
    parser.add_argument("--target_pdb", required=True, help="Path to target PDB")
    parser.add_argument("--epitope_residues", default="", help="Epitope residue specification")
    parser.add_argument("--framework_type", default="standard-fv", help="Framework type")
    parser.add_argument("--batch_name", required=True, help="Batch name for display")
    parser.add_argument("--params_json", default=None, help="Additional params as JSON")
    parser.add_argument("--api_url", default="http://localhost:8000", help="API URL")
    parser.add_argument("--output", default="spawn_result.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = spawn_rfantibody_jobs(
        parent_job_id=args.parent_job_id,
        total_designs=args.total_designs,
        designs_per_job=args.designs_per_job,
        target_pdb_path=args.target_pdb,
        epitope_residues=args.epitope_residues,
        framework_type=args.framework_type,
        batch_name=args.batch_name,
        params_json=args.params_json,
        api_url=args.api_url
    )
    
    # Write result
    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)
    
    if result["failed_spawns"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

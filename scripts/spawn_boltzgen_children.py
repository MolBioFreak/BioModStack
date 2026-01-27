#!/usr/bin/env python3
"""
Spawn BoltzGen child jobs for parallel binder design campaigns.

Called by parent workflow to split large design campaigns across multiple GPUs.
Each child job runs BoltzGen for a subset of designs and is managed by
the GPU orchestrator for optimal resource allocation.

Supports RESUME: If children already exist and completed, reuses them
instead of spawning new ones.
"""
import argparse
import json
import os
import sys
import requests
from pathlib import Path
from math import ceil


def check_existing_children(parent_job_id: str, stage: str, api_url: str, batch_name: str = None):
    """
    Check if completed children already exist for this parent job and stage.
    
    Args:
        parent_job_id: Parent job ID
        stage: Child stage filter (e.g., 'boltzgen')
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
        completed_children = []
        for i, output_dir in enumerate(child_output_dirs):
            completed_children.append({
                "job_id": f"completed_{i}",
                "output_dir": output_dir,
                "index": i
            })
        
        data["completed_children"] = completed_children
        return all_done, completed_children, data
        
    except Exception as e:
        print(f"[SPAWN-BOLTZGEN] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def spawn_boltzgen_jobs(
    parent_job_id: str,
    total_designs: int,
    designs_per_job: int,
    yaml_config_path: str,
    target_pdb_path: str,
    mode: str,
    batch_name: str,
    params_json: str = None,
    api_url: str = DEFAULT_API_URL
):
    """
    Spawn multiple BoltzGen child jobs.
    
    Args:
        parent_job_id: Parent job's ID
        total_designs: Total number of designs to generate
        designs_per_job: How many designs per child job (parallelization factor)
        yaml_config_path: Path to pre-generated BoltzGen YAML config
        target_pdb_path: Path to target PDB file
        mode: BoltzGen mode (nanobody_binder, peptide_binder, protein_binder)
        batch_name: Human-readable batch name
        params_json: Additional parameters as JSON string
        api_url: API base URL
    """
    # Calculate number of jobs needed
    num_jobs = ceil(total_designs / designs_per_job)
    
    # =========================================================================
    # RESUME CHECK: See if children already exist and completed
    # =========================================================================
    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, "boltzgen", api_url, batch_name=batch_name
    )
    
    existing_count = len(existing_children)
    if existing_count > 0:
        print(f"[SPAWN-BOLTZGEN] Found {existing_count} existing children for parent {parent_job_id}")
        
        if all_done:
            print(f"[SPAWN-BOLTZGEN] RESUME: All {existing_count} children already completed! Skipping spawn.")
            return {
                "status": "resumed",
                "spawned_jobs": 0,
                "reused_jobs": existing_count,
                "failed_spawns": 0,
                "total_designs": total_designs,
                "designs_per_job": designs_per_job,
                "child_jobs": existing_children,
                "resumed": True
            }
        else:
            completed = child_status.get("completed", 0)
            running = child_status.get("running", 0)
            pending = child_status.get("pending", 0)
            
            print(f"[SPAWN-BOLTZGEN] Existing: {completed} completed, {running} running, {pending} pending")
            
            if running > 0 or pending > 0:
                print(f"[SPAWN-BOLTZGEN] RESUME: Children still in progress. Not spawning duplicates.")
                return {
                    "status": "in_progress",
                    "spawned_jobs": 0,
                    "reused_jobs": existing_count,
                    "failed_spawns": 0,
                    "total_designs": total_designs,
                    "designs_per_job": designs_per_job,
                    "child_jobs": existing_children,
                    "resumed": True
                }
    
    # =========================================================================
    # FRESH SPAWN: No existing children or all failed
    # =========================================================================
    print(f"[SPAWN-BOLTZGEN] Spawning {num_jobs} BoltzGen jobs for {total_designs} total designs")
    print(f"[SPAWN-BOLTZGEN] Designs per job: {designs_per_job}")
    
    # Parse additional params
    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
        except json.JSONDecodeError:
            print(f"[SPAWN-BOLTZGEN] Warning: Failed to parse params_json", file=sys.stderr)
    
    created = []
    failed = 0
    designs_assigned = 0
    
    for i in range(num_jobs):
        # Calculate designs for this job
        remaining = total_designs - designs_assigned
        job_designs = min(designs_per_job, remaining)
        designs_assigned += job_designs
        
        job_data = {
            "name": f"{batch_name}_boltzgen_{i}",
            "model_id": "boltzgen_child",
            "mode": mode,
            "params": {
                "boltzgen_num_designs": job_designs,
                "boltzgen_yaml_config": yaml_config_path,
                "target_pdb": target_pdb_path,
                "job_index": i,
                "total_jobs": num_jobs,
                **extra_params
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": batch_name,
            "child_stage": "boltzgen",
            # VRAM estimation: BoltzGen uses ~5-8 GB per job
            "sequence_length": 150,  # Approximate scaffold length for VRAM calculation
        }
        
        try:
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            
            if resp.ok:
                job_id = resp.json().get("id", "unknown")
                print(f"[SPAWN-BOLTZGEN] Created child job {job_id} ({job_designs} designs)")
                created.append({
                    "job_id": job_id,
                    "designs": job_designs,
                    "index": i
                })
            else:
                print(f"[SPAWN-BOLTZGEN] Failed to create job {i}: {resp.status_code} {resp.text}", file=sys.stderr)
                failed += 1
                
        except Exception as e:
            print(f"[SPAWN-BOLTZGEN] Error creating job {i}: {e}", file=sys.stderr)
            failed += 1
    
    result = {
        "status": "complete" if failed == 0 else "partial",
        "spawned_jobs": len(created),
        "failed_spawns": failed,
        "total_designs": total_designs,
        "designs_per_job": designs_per_job,
        "num_jobs": num_jobs,
        "child_jobs": created
    }
    
    print(f"[SPAWN-BOLTZGEN] Complete: {len(created)} jobs created, {failed} failed")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Spawn BoltzGen child jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--total_designs", type=int, required=True, help="Total designs to generate")
    parser.add_argument("--designs_per_job", type=int, default=100, help="Designs per child job (parallelization)")
    parser.add_argument("--yaml_config", required=True, help="Path to BoltzGen YAML config")
    parser.add_argument("--target_pdb", required=True, help="Path to target PDB")
    parser.add_argument("--mode", default="nanobody_binder", help="BoltzGen mode")
    parser.add_argument("--batch_name", required=True, help="Batch name for display")
    parser.add_argument("--params_json", default=None, help="Additional params as JSON")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--output", default="spawn_boltzgen_result.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = spawn_boltzgen_jobs(
        parent_job_id=args.parent_job_id,
        total_designs=args.total_designs,
        designs_per_job=args.designs_per_job,
        yaml_config_path=args.yaml_config,
        target_pdb_path=args.target_pdb,
        mode=args.mode,
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

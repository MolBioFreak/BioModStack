#!/usr/bin/env python3
"""
Spawn RFantibody child jobs for parallel backbone generation.

Called by parent workflow to split backbone generation across multiple GPUs.
Each child job runs RFantibody for a subset of designs and is managed by
the GPU orchestrator for optimal resource allocation.

Supports RESUME: If children already exist and completed, reuses them
instead of spawning new ones.
"""
import argparse
import json
import sys
import requests
from pathlib import Path


def check_existing_children(parent_job_id: str, stage: str, api_url: str, batch_name: str = None):
    """
    Check if completed children already exist for this parent job and stage.
    
    Args:
        parent_job_id: Parent job ID
        stage: Child stage filter (e.g., 'rfantibody')
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
        completed_count = data.get("completed", 0)
        total_count = data.get("total", 0)
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
        
        # Add the raw API data for downstream use
        data["completed_children"] = completed_children
        
        return all_done, completed_children, data
        
    except Exception as e:
        print(f"[SPAWN-RFA] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


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
    
    # =========================================================================
    # RESUME CHECK: See if children already exist and completed
    # If so, skip spawning and return their info
    # Pass batch_name to find children from original run if this is a resume
    # =========================================================================
    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, "rfantibody", api_url, batch_name=batch_name
    )
    
    existing_count = len(existing_children)
    if existing_count > 0:
        print(f"[SPAWN-RFA] Found {existing_count} existing children for parent {parent_job_id}")
        
        if all_done:
            print(f"[SPAWN-RFA] RESUME: All {existing_count} children already completed! Skipping spawn.")
            # Return info about existing children instead of spawning new ones
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
            # Some children exist but not all completed - check if we need more
            completed = child_status.get("completed", 0)
            running = child_status.get("running", 0)
            pending = child_status.get("pending", 0)
            failed = child_status.get("failed", 0)
            
            print(f"[SPAWN-RFA] Existing children: {completed} completed, {running} running, {pending} pending, {failed} failed")
            
            # If any are still running or pending, don't spawn duplicates
            if running > 0 or pending > 0:
                print(f"[SPAWN-RFA] RESUME: {running + pending} children still in progress. Not spawning duplicates.")
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
    # No existing children or all failed - proceed with fresh spawn
    # =========================================================================
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

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
import os
import sys
import requests
from pathlib import Path


def _normalize_pinned_gpus(raw_value):
    if raw_value in (None, "", []):
        return None
    if isinstance(raw_value, list):
        values = raw_value
    else:
        text = str(raw_value).strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        values = [part.strip() for part in text.split(",") if part.strip()]
    normalized = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return normalized or None


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
        children = data.get("children", [])
        child_output_dirs = data.get("child_output_dirs", [])
        child_output_dirs_all = data.get("child_output_dirs_all", [])

        # Deduplicate by child job ID and keep deterministic order.
        deduped_children = {}
        for child in children:
            child_id = child.get("job_id")
            if child_id:
                deduped_children[child_id] = child

        completed_children = []
        for child_id, child in deduped_children.items():
            if child.get("status") != "completed":
                continue
            output_dir = child.get("output_dir")
            if not output_dir:
                continue
            completed_children.append(
                {
                    "job_id": child_id,
                    "output_dir": output_dir,
                    "aggregated_by_parent": bool(child.get("aggregated_by_parent", False)),
                }
            )

        # Fallback for older API payloads that do not expose `children`.
        if not completed_children:
            fallback_dirs = child_output_dirs_all or child_output_dirs
            for i, output_dir in enumerate(fallback_dirs):
                completed_children.append(
                    {
                        "job_id": f"completed_{i}",
                        "output_dir": output_dir,
                        "aggregated_by_parent": False,
                    }
                )

        # Add normalized children info for downstream resume logic.
        data["completed_children"] = completed_children
        data["deduped_child_ids"] = [c.get("job_id") for c in completed_children]

        return all_done, completed_children, data
        
    except Exception as e:
        print(f"[SPAWN-RFA] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def spawn_rfantibody_jobs(
    parent_job_id: str,
    total_designs: int,
    designs_per_job: int,
    target_pdb_path: str,
    epitope_residues: str,
    framework_type: str,
    framework_pdb: str | None,
    batch_name: str,
    params_json: str = None,
    api_url: str = DEFAULT_API_URL
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
    
    existing_count = child_status.get("total", len(existing_children))
    if existing_count > 0:
        print(f"[SPAWN-RFA] Found {existing_count} existing children for parent {parent_job_id}")
        completed = child_status.get("completed", len(existing_children))
        running = child_status.get("running", 0)
        pending = child_status.get("pending", 0)
        failed = child_status.get("failed", 0)
        cancelled = child_status.get("cancelled", 0)

        print(
            f"[SPAWN-RFA] Existing children status: "
            f"{completed} completed, {running} running, {pending} pending, "
            f"{failed} failed, {cancelled} cancelled"
        )
        
        if all_done:
            if completed > 0:
                print(f"[SPAWN-RFA] RESUME: Reusing {completed} completed child job(s); skipping spawn.")
                return {
                    "status": "resumed",
                    "spawned_jobs": 0,
                    "reused_jobs": completed,
                    "failed_spawns": 0,
                    "total_designs": total_designs,
                    "designs_per_job": designs_per_job,
                    "child_jobs": existing_children,
                    "resumed": True
                }

            print("[SPAWN-RFA] Existing children are all failed/cancelled; starting a fresh spawn.")
        else:
            # If any are still running/pending, do not spawn duplicates.
            if running > 0 or pending > 0:
                print(f"[SPAWN-RFA] RESUME: {running + pending} children still in progress. Not spawning duplicates.")
                return {
                    "status": "in_progress",
                    "spawned_jobs": 0,
                    "reused_jobs": completed,
                    "failed_spawns": 0,
                    "total_designs": total_designs,
                    "designs_per_job": designs_per_job,
                    "child_jobs": existing_children,
                    "resumed": True
                }

            if completed > 0:
                print("[SPAWN-RFA] RESUME: Completed children already exist; skipping duplicate spawn.")
                return {
                    "status": "partial_complete",
                    "spawned_jobs": 0,
                    "reused_jobs": completed,
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

    pinned_gpus = _normalize_pinned_gpus(extra_params.get("pinned_gpus"))
    if pinned_gpus is not None:
        extra_params["pinned_gpus"] = pinned_gpus
    raw_pinned_gpu = extra_params.get("pinned_gpu")
    pinned_gpu = None
    if raw_pinned_gpu not in (None, ""):
        try:
            pinned_gpu = int(raw_pinned_gpu)
        except (TypeError, ValueError):
            pinned_gpu = None
    if pinned_gpu is None and pinned_gpus and len(pinned_gpus) == 1:
        pinned_gpu = pinned_gpus[0]
    
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
        if pinned_gpu is not None:
            job_data["pinned_gpu"] = pinned_gpu
        if framework_pdb:
            job_data["params"]["framework_pdb"] = framework_pdb
        
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
    parser.add_argument("--framework_pdb", default=None, help="Optional custom framework PDB path")
    parser.add_argument("--batch_name", required=True, help="Batch name for display")
    parser.add_argument("--params_json", default=None, help="Additional params as JSON")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--output", default="spawn_result.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = spawn_rfantibody_jobs(
        parent_job_id=args.parent_job_id,
        total_designs=args.total_designs,
        designs_per_job=args.designs_per_job,
        target_pdb_path=args.target_pdb,
        epitope_residues=args.epitope_residues,
        framework_type=args.framework_type,
        framework_pdb=args.framework_pdb,
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

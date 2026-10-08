#!/usr/bin/env python3
"""
Spawn FAMPNN child jobs for parallel sequence design.

Called by parent workflow to split FAMPNN across multiple GPUs.
Each child job runs FAMPNN for a subset of backbone PDBs and is managed by
the GPU orchestrator for optimal resource allocation.
"""
import argparse
import json
import os
import sys
import requests
from pathlib import Path

from child_job_utils import (
    apply_child_resume_params,
    child_status_kind,
    fetch_children_status,
    find_existing_child,
    preferred_child_gpu,
)


DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def _child_display_name(display_prefix: str, stage_label: str, index: int, total: int) -> str:
    prefix = (display_prefix or "").strip() or "Antibody"
    if total > 1:
        return f"{prefix} · {stage_label} {index + 1}/{total}"
    return f"{prefix} · {stage_label}"


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
    try:
        data = fetch_children_status(parent_job_id, stage, api_url=api_url, batch_name=batch_name)
        children = data.get("children", [])
        return data.get("all_done", False), children, data
    except Exception as e:
        print(f"[SPAWN-FAMPNN] Warning: Could not check existing child status: {e}", file=sys.stderr)
        return False, [], {}


def spawn_fampnn_jobs(
    parent_job_id: str,
    pdb_dir: str,
    pdbs_per_job: int,
    seqs_per_design: int,
    batch_name: str,
    display_prefix: str = "",
    params_json: str = None,
    api_url: str = DEFAULT_API_URL
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

    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, "fampnn", api_url, batch_name=batch_name
    )
    existing_count = child_status.get("total", len(existing_children))
    if existing_count > 0:
        print(
            f"[SPAWN-FAMPNN] Existing children status: "
            f"{child_status.get('completed', 0)} completed, "
            f"{child_status.get('running', 0)} running, "
            f"{child_status.get('pending', 0)} pending, "
            f"{child_status.get('failed', 0)} failed, "
            f"{child_status.get('cancelled', 0)} cancelled"
        )

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
    reused = 0
    resumed = 0
    
    for i in range(num_jobs):
        start_idx = i * pdbs_per_job
        end_idx = min(start_idx + pdbs_per_job, total_pdbs)
        job_pdbs = pdbs[start_idx:end_idx]
        child_name = _child_display_name(display_prefix, "FA-MPNN", i, num_jobs)
        existing_child = find_existing_child(
            child_status,
            child_name=child_name,
            job_index=i,
        )
        existing_kind = child_status_kind(existing_child)

        if existing_kind == "completed":
            reused += 1
            created.append({
                "job_id": existing_child.get("job_id"),
                "pdb_count": len(job_pdbs),
                "index": i,
                "reused": True,
            })
            print(f"[SPAWN-FAMPNN] RESUME: Reusing completed child {child_name}")
            continue

        if existing_kind == "active":
            reused += 1
            created.append({
                "job_id": existing_child.get("job_id"),
                "pdb_count": len(job_pdbs),
                "index": i,
                "reused": True,
            })
            print(f"[SPAWN-FAMPNN] RESUME: Child still active, leaving in place: {child_name}")
            continue
        
        # Convert to absolute paths
        pdb_paths = [str(p.absolute()) for p in job_pdbs]
        
        # VRAM estimate based on CDR loops being designed (NOT entire chain)
        # VHH: 3 CDR loops ~40 AA total, Full H+L: 6 CDR loops ~80 AA total
        is_vhh = extra_params.get('vhh_mode', False) or extra_params.get('antibody_format') == 'VHH'
        estimated_seq_len = 40 if is_vhh else 80
        
        job_data = {
            "name": child_name,
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
        effective_pinned_gpu = preferred_child_gpu(existing_child, pinned_gpu)
        if effective_pinned_gpu is not None:
            job_data["pinned_gpu"] = effective_pinned_gpu
        if existing_kind == "failed":
            job_data["params"] = apply_child_resume_params(job_data["params"], existing_child)
            resumed += 1
            print(f"[SPAWN-FAMPNN] RESUME: Relaunching failed child with Nextflow resume: {child_name}")
        
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
        "spawned_jobs": len([child for child in created if not child.get("reused")]),
        "reused_jobs": reused,
        "resumed_jobs": resumed,
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
    parser.add_argument("--display_prefix", default="", help="Human-readable prefix for child job names")
    parser.add_argument("--params_json", default=None, help="Additional params as JSON")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--output", default="spawn_fampnn_result.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = spawn_fampnn_jobs(
        parent_job_id=args.parent_job_id,
        pdb_dir=args.pdb_dir,
        pdbs_per_job=args.pdbs_per_job,
        seqs_per_design=args.seqs_per_design,
        batch_name=args.batch_name,
        display_prefix=args.display_prefix,
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

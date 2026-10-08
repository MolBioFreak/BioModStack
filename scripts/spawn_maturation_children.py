#!/usr/bin/env python3
"""
Spawn PPIFlow maturation child jobs for parallel processing.
"""
import argparse
import json
import os
import sys
from pathlib import Path
import requests
DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")



def check_existing_children(parent_job_id, stage, api_url, batch_name=None):
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
        print(f"[SPAWN-MAT] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


def spawn_jobs(parent_job_id, pdb_dir, designs_per_job, batch_name, stage, params_json, api_url):
    pdb_dir = Path(pdb_dir)
    pdb_files = sorted(pdb_dir.glob("*.pdb"))
    if not pdb_files:
        print("[SPAWN-MAT] No PDB files found to spawn.", file=sys.stderr)
        return {"status": "no_inputs", "spawned_jobs": 0, "child_jobs": []}

    total_designs = len(pdb_files)
    num_jobs = (total_designs + designs_per_job - 1) // designs_per_job

    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, stage, api_url, batch_name=batch_name
    )
    existing_count = len(existing_children)
    if existing_count > 0:
        if all_done:
            print(f"[SPAWN-MAT] RESUME: All {existing_count} children already completed.")
            return {
                "status": "resumed",
                "spawned_jobs": 0,
                "reused_jobs": existing_count,
                "child_jobs": existing_children,
                "resumed": True
            }

        running = child_status.get("running", 0)
        pending = child_status.get("pending", 0)
        if running > 0 or pending > 0:
            print(f"[SPAWN-MAT] RESUME: {running + pending} children still in progress.")
            return {
                "status": "in_progress",
                "spawned_jobs": 0,
                "reused_jobs": existing_count,
                "child_jobs": existing_children,
                "resumed": True
            }

    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
        except json.JSONDecodeError:
            print("[SPAWN-MAT] Warning: Failed to parse params_json", file=sys.stderr)

    created = []
    failed = 0
    designs_assigned = 0

    for i in range(num_jobs):
        remaining = total_designs - designs_assigned
        job_designs = min(designs_per_job, remaining)
        batch_slice = pdb_files[designs_assigned:designs_assigned + job_designs]
        designs_assigned += job_designs

        pdb_paths = ",".join(str(p.resolve()) for p in batch_slice)

        job_data = {
            "name": f"{batch_name}_{stage}_{i}",
            "model_id": "template_antibody_denovo",
            "mode": "maturation_child",
            "params": {
                "pdb_paths": pdb_paths,
                "job_index": i,
                "total_jobs": num_jobs,
                "maturation_stage_name": stage,
                **extra_params
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": batch_name,
            "child_stage": stage,
            "sequence_length": 300,
        }

        try:
            resp = requests.post(
                f"{api_url}/api/jobs",
                json=job_data,
                timeout=10
            )
            if resp.ok:
                job = resp.json()
                created.append(job)
                print(f"[SPAWN-MAT] Spawned child {job.get('job_id')}")
            else:
                failed += 1
                print(f"[SPAWN-MAT] Failed spawn: {resp.text}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"[SPAWN-MAT] Error spawning job: {e}", file=sys.stderr)

    return {
        "status": "spawned",
        "spawned_jobs": len(created),
        "failed_spawns": failed,
        "total_designs": total_designs,
        "designs_per_job": designs_per_job,
        "child_jobs": created,
    }


def main():
    parser = argparse.ArgumentParser(description="Spawn PPIFlow maturation child jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--pdb_dir", required=True, help="Directory of input PDBs")
    parser.add_argument("--designs_per_job", type=int, default=4,
                        help="PDBs per child job")
    parser.add_argument("--batch_name", required=True, help="Batch name")
    parser.add_argument("--stage", default="maturation", help="Stage name to record on child jobs")
    parser.add_argument("--params_json", default="", help="Additional params as JSON")
    parser.add_argument("--api_url", default=DEFAULT_API_URL,
                        help="API base URL")
    parser.add_argument("--output", default="spawn_maturation_result.json",
                        help="Output JSON file")
    args = parser.parse_args()

    result = spawn_jobs(
        parent_job_id=args.parent_job_id,
        pdb_dir=args.pdb_dir,
        designs_per_job=args.designs_per_job,
        batch_name=args.batch_name,
        stage=args.stage,
        params_json=args.params_json,
        api_url=args.api_url
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

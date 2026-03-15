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

from child_job_utils import (
    apply_child_resume_params,
    child_status_kind,
    fetch_children_status,
    find_existing_child,
    preferred_child_gpu,
)


def _child_display_name(display_prefix, stage_label, index, total):
    prefix = (display_prefix or "").strip() or "Antibody"
    if total > 1:
        return f"{prefix} · {stage_label} {index + 1}/{total}"
    return f"{prefix} · {stage_label}"


def _stage_label(stage):
    stage_key = (stage or "").strip().lower()
    if stage_key in {"maturation", "validated_maturation", "maturation_post_validation"}:
        return "PPIFlow"
    return stage_key.replace("_", " ").title() or "Child"


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



def check_existing_children(parent_job_id, stage, api_url, batch_name=None):
    try:
        data = fetch_children_status(parent_job_id, stage, api_url=api_url, batch_name=batch_name)
        all_done = data.get("all_done", False)
        completed_children = data.get("children", [])
        data["completed_children"] = completed_children
        return all_done, completed_children, data
    except Exception as e:
        print(f"[SPAWN-MAT] Warning: Failed to check existing children: {e}", file=sys.stderr)
        return False, [], {}


def spawn_jobs(parent_job_id, pdb_dir, designs_per_job, batch_name, display_prefix, stage, params_json, api_url):
    pdb_dir = Path(pdb_dir)
    pdb_files = sorted(pdb_dir.glob("*.pdb"))
    if not pdb_files:
        print("[SPAWN-MAT] No PDB files found to spawn.", file=sys.stderr)
        return {"status": "no_inputs", "spawned_jobs": 0, "child_jobs": []}

    total_designs = len(pdb_files)
    num_jobs = (total_designs + designs_per_job - 1) // designs_per_job
    stage_label = _stage_label(stage)

    all_done, existing_children, child_status = check_existing_children(
        parent_job_id, stage, api_url, batch_name=batch_name
    )
    existing_count = child_status.get("total", len(existing_children))
    if existing_count > 0:
        print(
            f"[SPAWN-MAT] Existing children status: "
            f"{child_status.get('completed', 0)} completed, "
            f"{child_status.get('running', 0)} running, "
            f"{child_status.get('pending', 0)} pending, "
            f"{child_status.get('failed', 0)} failed, "
            f"{child_status.get('cancelled', 0)} cancelled"
        )

    extra_params = {}
    if params_json:
        try:
            extra_params = json.loads(params_json)
        except json.JSONDecodeError:
            print("[SPAWN-MAT] Warning: Failed to parse params_json", file=sys.stderr)

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
    designs_assigned = 0

    for i in range(num_jobs):
        remaining = total_designs - designs_assigned
        job_designs = min(designs_per_job, remaining)
        batch_slice = pdb_files[designs_assigned:designs_assigned + job_designs]
        designs_assigned += job_designs

        pdb_paths = ",".join(str(p.resolve()) for p in batch_slice)
        child_name = _child_display_name(display_prefix, stage_label, i, num_jobs)
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
                "index": i,
                "reused": True,
            })
            print(f"[SPAWN-MAT] RESUME: Reusing completed child {child_name}")
            continue

        if existing_kind == "active":
            reused += 1
            created.append({
                "job_id": existing_child.get("job_id"),
                "index": i,
                "reused": True,
            })
            print(f"[SPAWN-MAT] RESUME: Child still active, leaving in place: {child_name}")
            continue

        job_data = {
            "name": child_name,
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
        effective_pinned_gpu = preferred_child_gpu(existing_child, pinned_gpu)
        if effective_pinned_gpu is not None:
            job_data["pinned_gpu"] = effective_pinned_gpu
        if existing_kind == "failed":
            job_data["params"] = apply_child_resume_params(job_data["params"], existing_child)
            resumed += 1
            print(f"[SPAWN-MAT] RESUME: Relaunching failed child with Nextflow resume: {child_name}")

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
        "spawned_jobs": len([child for child in created if not child.get("reused")]),
        "reused_jobs": reused,
        "resumed_jobs": resumed,
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
    parser.add_argument("--display_prefix", default="", help="Human-readable prefix for child job names")
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
        display_prefix=args.display_prefix,
        stage=args.stage,
        params_json=args.params_json,
        api_url=args.api_url
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

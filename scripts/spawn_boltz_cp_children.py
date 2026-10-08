#!/usr/bin/env python3
"""
Spawn Fold-CP child jobs from a logical shard-plan manifest.

Called by the parent boltz_cp_experimental workflow when a logical shard plan
expands into multiple bundles. Each child keeps the logical plan context but
executes a single bundle under the existing parent/child job infrastructure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests

from child_job_utils import (
    apply_child_resume_params,
    child_status_kind,
    fetch_children_status,
    find_existing_child,
    preferred_child_gpu,
)

DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
CHILD_STAGE = "boltz_cp_bundle"


def check_existing_children(parent_job_id: str, api_url: str, batch_name: str | None = None):
    try:
        data = fetch_children_status(parent_job_id, CHILD_STAGE, api_url=api_url, batch_name=batch_name)
        all_done = data.get("all_done", False)
        completed_children = data.get("children", [])
        data["completed_children"] = completed_children
        return all_done, completed_children, data
    except Exception as exc:  # pragma: no cover - defensive logging path
        print(f"[SPAWN-BOLTZ-CP] Warning: failed to inspect existing children: {exc}", file=sys.stderr)
        return False, [], {}


def _normalize_gpu_ids(raw: Any) -> list[int]:
    values: list[int] = []
    if isinstance(raw, (list, tuple)):
        source = raw
    else:
        source = str(raw or "").split(",")
    for item in source:
        text = str(item).strip()
        if not text:
            continue
        try:
            gpu_id = int(text)
        except ValueError:
            continue
        if gpu_id not in values:
            values.append(gpu_id)
    return values


def spawn_boltz_cp_children(
    parent_job_id: str,
    manifest_path: str,
    batch_name: str,
    api_url: str = DEFAULT_API_URL,
) -> Dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    bundles = manifest.get("bundles") or []
    input_metadata = manifest.get("input_metadata") or {}
    shard_plan = manifest.get("shard_plan") or {}
    available_gpus = _normalize_gpu_ids(
        manifest.get("physical_gpu_ids") or input_metadata.get("physical_gpu_ids") or input_metadata.get("gpu_ids")
    )
    sequence_length = int(input_metadata.get("sequence_length") or 0)

    all_done, existing_children, child_status = check_existing_children(parent_job_id, api_url, batch_name=batch_name)
    existing_count = child_status.get("total", len(existing_children))
    if existing_count > 0:
        print(
            f"[SPAWN-BOLTZ-CP] Found {existing_count} existing children for parent {parent_job_id}: "
            f"{child_status.get('completed', 0)} completed, {child_status.get('running', 0)} running, "
            f"{child_status.get('pending', 0)} pending, {child_status.get('failed', 0)} failed, "
            f"{child_status.get('cancelled', 0)} cancelled"
        )
    if all_done and existing_count == len(bundles) and bundles:
        print("[SPAWN-BOLTZ-CP] All expected children already exist and are complete; reusing them.")

    created = []
    failed = 0
    reused = 0
    resumed = 0

    for bundle_index, bundle in enumerate(bundles):
        child_name = f"{batch_name}_{bundle.get('bundle_id', f'bundle_{bundle_index:02d}') }"
        existing_child = find_existing_child(
            child_status,
            child_name=child_name,
            batch_index=bundle_index,
            job_index=bundle_index,
        )
        existing_kind = child_status_kind(existing_child)
        if existing_kind in {"completed", "active"}:
            reused += 1
            created.append(
                {
                    "job_id": existing_child.get("job_id"),
                    "bundle_id": bundle.get("bundle_id"),
                    "bundle_index": bundle_index,
                    "reused": True,
                }
            )
            print(f"[SPAWN-BOLTZ-CP] RESUME: reusing {existing_kind} child {child_name}")
            continue

        assigned_gpu = None
        if available_gpus:
            assigned_gpu = available_gpus[bundle_index % len(available_gpus)]
        assigned_gpu = preferred_child_gpu(existing_child, assigned_gpu)

        child_params: Dict[str, Any] = {
            "bcp_role": "child",
            "bcp_input_path": input_metadata.get("input_path"),
            "bcp_input_format": input_metadata.get("input_format", "config_files"),
            "bcp_output_format": input_metadata.get("output_format", "mmcif"),
            "bcp_write_full_pae": input_metadata.get("write_full_pae", False),
            "bcp_repo_path": input_metadata.get("repo_path"),
            "bcp_container_path": input_metadata.get("container_path"),
            "bcp_shard_plan_id": shard_plan.get("name") or manifest.get("plan_id") or input_metadata.get("shard_plan_id") or "2x2",
            "bcp_parent_job_id": parent_job_id,
            "bcp_plan_manifest_path": str(Path(manifest_path).resolve()),
            "bcp_bundle_id": bundle.get("bundle_id"),
            "bcp_bundle_index": bundle_index,
            "bcp_bundle_row_index": bundle.get("row_index"),
            "bcp_bundle_col_index": bundle.get("col_index"),
            "bcp_bundle_row_range": bundle.get("row_range"),
            "bcp_bundle_col_range": bundle.get("col_range"),
            "batch_index": bundle_index,
            "job_index": bundle_index,
            "bcp_size_cp": 1,
        }
        for passthrough_key in (
            "bcp_seed",
            "bcp_recycling_steps",
            "bcp_sampling_steps",
            "bcp_diffusion_samples",
            "boltz_use_msa",
            "msa_provider",
            "msa_preset",
            "msa_local_db",
            "msa_cache_dir",
            "msa_threads",
            "msa_use_gpu",
            "colabfold_api_host",
            "colabfold_api_min_interval",
            "colabfold_api_poll_interval",
            "msa_min_depth_warning",
            "msa_min_depth_fail",
            "msa_force_refresh",
            "msa_cache_only",
            "msa_use_expand",
            "msa_use_env",
            "msa_num_iterations",
            "msa_min_seq_id",
            "msa_min_coverage",
            "msa_taxon_list",
            "code_root",
            "lock_gpus",
        ):
            if passthrough_key in input_metadata and input_metadata[passthrough_key] not in (None, ""):
                child_params[passthrough_key] = input_metadata[passthrough_key]

        if assigned_gpu is not None:
            child_params["bcp_gpu_ids"] = str(assigned_gpu)
            child_params["pinned_gpus"] = [assigned_gpu]

        job_data: Dict[str, Any] = {
            "name": child_name,
            "model_id": "boltz_cp_experimental",
            "mode": "design",
            "params": child_params,
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": batch_name,
            "child_stage": CHILD_STAGE,
            "sequence_length": sequence_length or None,
        }
        if assigned_gpu is not None:
            job_data["pinned_gpu"] = assigned_gpu
        if existing_kind == "failed":
            job_data["params"] = apply_child_resume_params(job_data["params"], existing_child)
            resumed += 1
            print(f"[SPAWN-BOLTZ-CP] RESUME: relaunching failed child with Nextflow resume: {child_name}")

        try:
            response = requests.post(f"{api_url}/api/jobs", json=job_data, timeout=10)
            if response.ok:
                job_id = response.json().get("id", "unknown")
                print(
                    f"[SPAWN-BOLTZ-CP] Created child job {job_id} for bundle {bundle.get('bundle_id')}"
                    + (f" on GPU {assigned_gpu}" if assigned_gpu is not None else "")
                )
                created.append(
                    {
                        "job_id": job_id,
                        "bundle_id": bundle.get("bundle_id"),
                        "bundle_index": bundle_index,
                        "pinned_gpu": assigned_gpu,
                    }
                )
            else:
                print(
                    f"[SPAWN-BOLTZ-CP] Failed to create child {child_name}: {response.status_code} {response.text}",
                    file=sys.stderr,
                )
                failed += 1
        except Exception as exc:  # pragma: no cover - network/API failure path
            print(f"[SPAWN-BOLTZ-CP] Error creating child {child_name}: {exc}", file=sys.stderr)
            failed += 1

    result = {
        "status": "complete" if failed == 0 else "partial",
        "spawned_jobs": len([child for child in created if not child.get("reused")]),
        "reused_jobs": reused,
        "resumed_jobs": resumed,
        "failed_spawns": failed,
        "bundle_count": len(bundles),
        "plan_id": manifest.get("plan_id"),
        "shard_plan_id": shard_plan.get("name") or input_metadata.get("shard_plan_id"),
        "child_jobs": created,
    }
    print(
        f"[SPAWN-BOLTZ-CP] Complete: {len(created)} planned children, {failed} failed, "
        f"{reused} reused, {resumed} resumed"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Spawn Fold-CP child jobs from a plan manifest")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--manifest", required=True, help="Path to boltz_cp_plan_manifest.json")
    parser.add_argument("--batch_name", required=True, help="Batch name for display")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--output", default="spawn_boltz_cp_result.json", help="Output JSON path")
    args = parser.parse_args()

    result = spawn_boltz_cp_children(
        parent_job_id=args.parent_job_id,
        manifest_path=args.manifest,
        batch_name=args.batch_name,
        api_url=args.api_url,
    )
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result["failed_spawns"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

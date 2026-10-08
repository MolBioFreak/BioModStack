#!/usr/bin/env python3
"""
Spawn BindCraft child jobs for parallel trajectory generation.

Each child job runs BindCraft with a different random seed, all targeting
the same protein. Results are aggregated by the parent job.
"""

import argparse
import json
import os
import requests
import sys
from pathlib import Path
from typing import Dict, Any
import math

DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def spawn_child_job(
    api_url: str,
    parent_job_id: str,
    child_index: int,
    target_pdb: str,
    trajectories_for_child: int,
    params: Dict[str, Any],
    batch_name: str
) -> Dict[str, Any]:
    """Spawn a single BindCraft child job."""
    
    child_params = {
        "workflow": "bindcraft_child",
        "parent_job_id": parent_job_id,
        "child_index": child_index,
        "batch_name": batch_name,
        # Target configuration
        "bindcraft_target_pdb": target_pdb,
        "bindcraft_hotspot_residues": params.get("hotspot_residues", ""),
        "bindcraft_binder_lengths": params.get("binder_lengths", "80-120"),
        "bindcraft_chains": params.get("chains", "A"),
        # Scale down designs per child
        "bindcraft_num_final_designs": max(10, trajectories_for_child // 4),
        # Design settings
        "bindcraft_design_algorithm": params.get("design_algorithm", "4stage"),
        "bindcraft_use_multimer_design": params.get("use_multimer_design", True),
        "bindcraft_num_recycles_design": params.get("num_recycles_design", 3),
        "bindcraft_num_recycles_validation": params.get("num_recycles_validation", 3),
        # MPNN settings
        "bindcraft_mpnn_weights": params.get("mpnn_weights", "soluble"),
        "bindcraft_num_mpnn_sequences": params.get("num_mpnn_sequences", 8),
        # Filters
        "bindcraft_min_iptm": params.get("min_iptm", 0.6),
        "bindcraft_max_hotspot_rmsd": params.get("max_hotspot_rmsd", 3.0),
        # Storage optimization (inherit from parent)
        "bindcraft_zip_animations": params.get("zip_animations", True),
        "bindcraft_zip_plots": params.get("zip_plots", True),
        "bindcraft_remove_unrelaxed_trajectory": params.get("remove_unrelaxed_trajectory", True),
        "bindcraft_remove_unrelaxed_complex": params.get("remove_unrelaxed_complex", True),
        "bindcraft_remove_binder_monomer": params.get("remove_binder_monomer", True),
        "bindcraft_save_trajectory_pickle": params.get("save_trajectory_pickle", False),
        # Child-specific settings
        "bindcraft_use_swa": False,  # Children run directly, no further spawning
        "bindcraft_random_seed": child_index * 1000,  # Unique seed per child
    }
    
    # Submit job via API
    try:
        response = requests.post(
            f"{api_url}/jobs",
            json={
                "name": f"{batch_name}_child_{child_index}",
                "workflow": "bindcraft_child",
                "params": child_params,
                "parent_job_id": parent_job_id,
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error spawning child {child_index}: {e}")
        return {"error": str(e), "child_index": child_index}


def main():
    parser = argparse.ArgumentParser(description="Spawn BindCraft child jobs")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--total_trajectories", type=int, required=True, 
                        help="Total trajectories to generate")
    parser.add_argument("--trajectories_per_job", type=int, required=True,
                        help="Trajectories per child job")
    parser.add_argument("--target_pdb", required=True, help="Target PDB file path")
    parser.add_argument("--batch_name", required=True, help="Batch identifier")
    parser.add_argument("--params_json", required=True, help="JSON string of parameters")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--output", required=True, help="Output JSON file")
    
    args = parser.parse_args()
    
    # Parse parameters
    try:
        params = json.loads(args.params_json)
    except json.JSONDecodeError as e:
        print(f"Error parsing params JSON: {e}")
        sys.exit(1)
    
    # Calculate number of child jobs
    num_children = math.ceil(args.total_trajectories / args.trajectories_per_job)
    
    print(f"=== Spawning BindCraft Child Jobs ===")
    print(f"Parent Job ID: {args.parent_job_id}")
    print(f"Total Trajectories: {args.total_trajectories}")
    print(f"Trajectories per Job: {args.trajectories_per_job}")
    print(f"Number of Children: {num_children}")
    print(f"Batch Name: {args.batch_name}")
    
    # Spawn children
    results = {
        "parent_job_id": args.parent_job_id,
        "batch_name": args.batch_name,
        "total_trajectories": args.total_trajectories,
        "trajectories_per_job": args.trajectories_per_job,
        "num_children": num_children,
        "children": [],
        "errors": []
    }
    
    for i in range(num_children):
        # Last child may have fewer trajectories
        remaining = args.total_trajectories - (i * args.trajectories_per_job)
        trajectories_for_child = min(args.trajectories_per_job, remaining)
        
        print(f"Spawning child {i+1}/{num_children} ({trajectories_for_child} trajectories)...")
        
        result = spawn_child_job(
            api_url=args.api_url,
            parent_job_id=args.parent_job_id,
            child_index=i,
            target_pdb=args.target_pdb,
            trajectories_for_child=trajectories_for_child,
            params=params,
            batch_name=args.batch_name
        )
        
        if "error" in result:
            results["errors"].append(result)
        else:
            results["children"].append({
                "child_index": i,
                "job_id": result.get("id"),
                "trajectories": trajectories_for_child
            })
    
    # Write output
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n=== Spawn Complete ===")
    print(f"Successfully spawned: {len(results['children'])}/{num_children}")
    print(f"Errors: {len(results['errors'])}")
    print(f"Output written to: {args.output}")
    
    if results["errors"]:
        print("\nErrors encountered:")
        for err in results["errors"]:
            print(f"  Child {err.get('child_index', '?')}: {err.get('error', 'Unknown error')}")


if __name__ == "__main__":
    main()

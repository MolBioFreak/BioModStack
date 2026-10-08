#!/usr/bin/env python3
"""
Wait for child jobs to complete.

Called by parent Nextflow job after spawning children.
Polls the API until all children finish, then outputs their result directories.
"""
import argparse
import json
import os
import sys
import time
import requests
DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

from pathlib import Path


def wait_for_children(
    parent_job_id: str,
    stage: str,
    poll_interval: int = 10,
    timeout: int = 0,  # 0 = no timeout (disabled by default)
    api_url: str = DEFAULT_API_URL,
    batch_name: str = None  # For resume: find children by batch_name
):
    """
    Block until all children for this parent+stage complete.
    
    Args:
        parent_job_id: Parent job's ID
        stage: Stage filter (rfantibody, fampnn, boltz2)
        poll_interval: Seconds between polls
        timeout: Maximum wait time in seconds (0 = no timeout)
        api_url: API base URL
        
    Returns:
        Dictionary with child output directories
    """
    start_time = time.time()
    endpoint = f"{api_url}/api/jobs/{parent_job_id}/children/status"
    params = {"stage": stage} if stage else {}
    if batch_name:
        params["batch_name"] = batch_name
    
    print(f"[WAIT] Waiting for children of {parent_job_id} (stage={stage})...")
    
    while True:
        elapsed = time.time() - start_time
        
        # Only check timeout if explicitly set (timeout > 0)
        if timeout > 0 and elapsed > timeout:
            print(f"[WAIT] TIMEOUT after {timeout}s", file=sys.stderr)
            return {
                "status": "timeout",
                "child_output_dirs": [],
                "elapsed_seconds": elapsed
            }
        
        try:
            resp = requests.get(endpoint, params=params, timeout=30)
            
            if not resp.ok:
                print(f"[WAIT] API error: {resp.status_code}", file=sys.stderr)
                time.sleep(poll_interval)
                continue
            
            data = resp.json()
            
            total = data.get("total", 0)
            completed = data.get("completed", 0)
            failed = data.get("failed", 0)
            running = data.get("running", 0)
            pending = data.get("pending", 0)
            all_done = data.get("all_done", False)
            
            print(f"[WAIT] Progress: {completed}/{total} done, {running} running, {pending} pending, {failed} failed")
            
            if all_done:
                success_rate = data.get("success_rate", 0)
                output_dirs = data.get("child_output_dirs", [])
                
                print(f"[WAIT] All children complete! Success rate: {success_rate}%")
                print(f"[WAIT] Output directories: {len(output_dirs)}")
                
                # Mark children as aggregated to prevent double-collection
                mark_url = f"{api_url}/api/jobs/{parent_job_id}/children/mark-aggregated"
                try:
                    requests.post(mark_url, params=params, timeout=10)
                except Exception as e:
                    print(f"[WAIT] Warning: Failed to mark children aggregated: {e}")
                
                return {
                    "status": "complete",
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "success_rate": success_rate,
                    "child_output_dirs": output_dirs,
                    "child_ids": data.get("child_ids", []),
                    "elapsed_seconds": elapsed
                }
            
        except requests.exceptions.RequestException as e:
            print(f"[WAIT] Network error: {e}", file=sys.stderr)
        
        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Wait for child jobs to complete")
    parser.add_argument("--parent_job_id", required=True, help="Parent job ID")
    parser.add_argument("--stage", default=None, help="Stage filter (rfantibody, fampnn, boltz2)")
    parser.add_argument("--poll_interval", type=int, default=10, help="Seconds between polls")
    parser.add_argument("--timeout", type=int, default=0, help="Max wait time in seconds (0 = no timeout)")
    parser.add_argument("--api_url", default=DEFAULT_API_URL, help="API URL")
    parser.add_argument("--batch_name", default=None, help="Batch name for resume (find children by batch_name)")
    parser.add_argument("--output", default="child_outputs.json", help="Output JSON file")
    
    args = parser.parse_args()
    
    result = wait_for_children(
        parent_job_id=args.parent_job_id,
        stage=args.stage,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        api_url=args.api_url,
        batch_name=args.batch_name
    )
    
    # Write result to file for Nextflow to consume
    output_path = Path(args.output)
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"[WAIT] Result written to {output_path}")
    
    # Exit non-zero if all children failed
    if result.get("status") == "timeout":
        sys.exit(2)
    elif result.get("completed", 0) == 0 and result.get("failed", 0) > 0:
        print("[WAIT] All children failed!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

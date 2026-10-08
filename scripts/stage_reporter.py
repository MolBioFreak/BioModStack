#!/usr/bin/env python3
"""
Report workflow stage completion to the backend API.
Usage: python stage_reporter.py <job_id> <stage_name> <status> [output_files...]
"""

import sys
import json
import requests
import os
import argparse

# Default API URL - construct from environment or localhost
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

def main():
    parser = argparse.ArgumentParser(description="Report workflow stage status")
    parser.add_argument("job_id", help="UUID of the job")
    parser.add_argument("stage", help="Name of the stage (e.g., rfantibody)")
    parser.add_argument("status", choices=["start", "complete"], help="Status to report")
    parser.add_argument("outputs", nargs="*", help="List of output file paths")
    
    args = parser.parse_args()
    
    # Clean output paths to be relative if possible, or absolute
    # If they are in the work directory, we might want to keep them absolute
    cleaned_outputs = [os.path.abspath(p) for p in args.outputs]
    
    try:
        if args.status == "start":
            url = f"{API_BASE_URL}/api/jobs/{args.job_id}/stage-start"
            response = requests.post(url, params={"stage": args.stage}, timeout=10)
        else:
            url = f"{API_BASE_URL}/api/jobs/{args.job_id}/stage-complete"
            payload = {
                "outputs": cleaned_outputs
            }
            # FastAPI expects query param for stage, JSON body for outputs
            response = requests.post(
                url, 
                params={"stage": args.stage}, 
                json=cleaned_outputs,  # The endpoint expects List[str] as body? No, body param name
                timeout=10
            )
            
            # Re-checking api definition:
            # async def report_stage_complete(job_id, stage, outputs: List[str]...)
            # "outputs" is in the body.
            
            response = requests.post(
                url, 
                params={"stage": args.stage}, 
                json=cleaned_outputs,
                timeout=10
            )
            
        if response.status_code >= 400:
            print(f"Error reporting stage: {response.text}", file=sys.stderr)
            # Don't fail the workflow just because reporting failed
            sys.exit(0)
            
        print(f"Successfully reported stage {args.stage} {args.status}")
        
    except Exception as e:
        print(f"Failed to report stage: {e}", file=sys.stderr)
        # Don't fail the workflow
        sys.exit(0)

if __name__ == "__main__":
    main()

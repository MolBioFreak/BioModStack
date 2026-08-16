#!/usr/bin/env python3
"""
Trigger ANARCII CDR annotation for a job via API.
"""
import argparse
import os
import sys
import requests


def main():
    parser = argparse.ArgumentParser(description="Trigger ANARCII CDR annotation for a job")
    parser.add_argument("--job_id", required=True, help="Job ID to annotate")
    parser.add_argument("--include_children", default="true",
                        help="Include child jobs (true/false)")
    parser.add_argument("--api_url", default="",
                        help="API base URL (optional, overrides API_BASE_URL env)")
    args = parser.parse_args()

    api_base = args.api_url or os.environ.get("API_BASE_URL", "http://localhost:8000")
    include_children = args.include_children.lower() in ("true", "1", "yes")

    url = f"{api_base}/api/jobs/{args.job_id}/annotate-cdrs"
    try:
        resp = requests.post(url, params={"include_children": include_children}, timeout=10)
        if resp.status_code >= 400:
            print(f"[ANARCII] Failed: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(0)
        print(f"[ANARCII] Triggered annotation for {args.job_id} (include_children={include_children})")
    except Exception as exc:
        print(f"[ANARCII] Error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()

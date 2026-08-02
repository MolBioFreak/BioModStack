#!/usr/bin/env python3
"""
Report workflow stage completion to the backend API.
Usage: python stage_reporter.py <job_id> <stage_name> <status> [output_files...]
"""

import argparse
import os
import sys
from pathlib import Path

import requests

# Allow importing platform/api/paths.py when run from workflow sandboxes.
CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "platform" / "api"))

try:
    from paths import to_allowed_relative  # type: ignore
except Exception:
    to_allowed_relative = None  # type: ignore


# Default API URL - construct from environment or localhost
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
STAGE_REPORT_TOKEN = os.environ.get("BMS_STAGE_REPORT_TOKEN", "").strip()


def normalize_output_path(path: str) -> str:
    """Prefer API-allowed relative paths; fall back to absolute path."""
    resolved = Path(path).expanduser().resolve()
    if to_allowed_relative is not None:
        try:
            return to_allowed_relative(resolved)
        except Exception:
            pass
    return str(resolved)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report workflow stage status")
    parser.add_argument("job_id", help="UUID of the job")
    parser.add_argument("stage", help="Name of the stage (e.g., rfantibody)")
    parser.add_argument(
        "status",
        choices=["start", "complete", "failed", "not_requested"],
        help="Status to report",
    )
    parser.add_argument("outputs", nargs="*", help="List of output file paths")

    args = parser.parse_args()
    cleaned_outputs = [normalize_output_path(p) for p in args.outputs]
    if not STAGE_REPORT_TOKEN:
        print("Failed to report stage: missing launch-scoped stage credential", file=sys.stderr)
        sys.exit(1)
    headers = {"Authorization": f"Bearer {STAGE_REPORT_TOKEN}"}

    try:
        if args.status == "start":
            url = f"{API_BASE_URL}/api/jobs/{args.job_id}/stage-start"
            response = requests.post(url, params={"stage": args.stage}, headers=headers, timeout=10)
        elif args.status == "complete":
            url = f"{API_BASE_URL}/api/jobs/{args.job_id}/stage-complete"
            # FastAPI endpoint expects List[str] body directly.
            response = requests.post(
                url,
                params={"stage": args.stage},
                json=cleaned_outputs,
                headers=headers,
                timeout=10,
            )
        else:
            url = f"{API_BASE_URL}/api/jobs/{args.job_id}/stage-terminal"
            response = requests.post(
                url,
                params={"stage": args.stage, "status": args.status},
                json=cleaned_outputs,
                headers=headers,
                timeout=10,
            )

        if response.status_code >= 400:
            print(f"Error reporting stage: {response.text}", file=sys.stderr)
            sys.exit(1)

        print(f"Successfully reported stage {args.stage} {args.status}")
    except Exception as e:
        print(f"Failed to report stage: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

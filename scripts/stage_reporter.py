#!/usr/bin/env python3
"""
Report workflow stage completion to the backend API.
Usage: python stage_reporter.py <job_id> <stage_name> <status> [output_files...]
"""

import argparse
import os
import sys
from pathlib import Path


# Allow importing platform/api/paths.py when run from workflow sandboxes.
CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "platform" / "api"))

try:
    from paths import to_allowed_relative  # type: ignore
except Exception:
    to_allowed_relative = None  # type: ignore


API_BASE_URL = os.environ.get("API_BASE_URL", "").strip()
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


def normalize_job_root_relative_output(path: str) -> str:
    """Preserve an explicitly job-root-relative POSIX output path fail closed."""
    raw = str(path)
    parts = raw.split("/")
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("unsafe job-root-relative output")
    return "/".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report workflow stage status")
    parser.add_argument(
        "--job-root-relative",
        action="store_true",
        help="preserve already validated job-root-relative output paths",
    )
    parser.add_argument("job_id", help="UUID of the job")
    parser.add_argument("stage", help="Name of the stage (e.g., rfantibody)")
    parser.add_argument(
        "status",
        choices=["start", "complete", "failed", "not_requested"],
        help="Status to report",
    )
    parser.add_argument("outputs", nargs="*", help="List of output file paths")

    args = parser.parse_args()
    normalizer = (
        normalize_job_root_relative_output
        if args.job_root_relative
        else normalize_output_path
    )
    if os.environ.get("BMS_REMOTE_EXECUTION") == "1":
        import fcntl
        import json
        import tempfile
        if args.job_id != os.environ.get("BMS_REMOTE_JOB_ID"):
            raise RuntimeError("remote stage Job identity mismatch")
        root = Path(os.environ["BMS_REMOTE_OUTPUT_ROOT"]).resolve(strict=True)
        outputs = []
        for value in args.outputs:
            path = root / normalize_job_root_relative_output(value) if args.job_root_relative else Path(value)
            outputs.append(path.resolve(strict=True).relative_to(root).as_posix())
        journal = root / "_remote" / "stage-terminal.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            states = json.loads(journal.read_text()) if journal.exists() else {}
            if args.status != "start":
                record = {"status": args.status, "outputs": outputs}
                if args.stage in states and states[args.stage] != record:
                    raise RuntimeError("conflicting remote terminal stage report")
                states[args.stage] = record
                fd, name = tempfile.mkstemp(dir=journal.parent)
                with os.fdopen(fd, "w") as handle:
                    json.dump(states, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, journal)
                descriptor = os.open(journal.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return
    cleaned_outputs = [normalizer(p) for p in args.outputs]
    if not API_BASE_URL:
        print("Failed to report stage: missing environment-owned API_BASE_URL", file=sys.stderr)
        sys.exit(1)
    if not STAGE_REPORT_TOKEN:
        print("Failed to report stage: missing launch-scoped stage credential", file=sys.stderr)
        sys.exit(1)
    import requests

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

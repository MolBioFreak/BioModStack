#!/usr/bin/env python3
"""Produce native CDR review annotations without host database prerequisites.

Nextflow invokes this for an explicit candidate artifact set. The canonical CDR
annotator retains sequence extraction, IMGT numbering and native batch behavior.
The legacy API trigger remains available only outside a component attempt.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def annotate_candidates(candidate_dirs, output, *, job_id, batch_size=500,
                        preferred_chains_by_path=None):
    """Annotate the exact ordered candidate set and retain source-bound results."""
    paths = []
    for raw in candidate_dirs:
        directory = Path(raw).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError(f"Annotation candidate directory is not a directory: {directory}")
        for path in sorted(directory.glob("*.pdb")):
            value = str(path.resolve(strict=True))
            if value not in paths:
                paths.append(value)
    if not paths:
        raise ValueError("Required CDR annotation has no candidate structures")

    # This scientific service does not import database or the interactive API.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
    from services.cdr_annotator import batch_annotate_pdbs

    annotations = batch_annotate_pdbs(
        paths, batch_size=batch_size,
        preferred_chains_by_path=preferred_chains_by_path or {},
    )
    missing = [path for path in paths if path not in annotations]
    report = {
        "schema": "bms.antibody.cdr_annotations.v1",
        "job_id": job_id,
        "status": "incomplete" if missing else "complete",
        "missing": missing,
        "annotations": [
            {"source_path": path,
             "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
             "annotation": annotations[path].to_dict() if path in annotations else None}
            for path in paths
        ],
    }
    Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if missing:
        raise RuntimeError(f"Required CDR annotation missing for {len(missing)} candidates; see {output}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Produce native CDR annotations for review")
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--candidate_dir", action="append", default=[])
    parser.add_argument("--output", default="cdr_annotations.json")
    parser.add_argument("--batch_size", type=int, default=500)
    parser.add_argument("--preferred_chains_json", default="{}",
                        help="Native source-path to detected H/L chain mapping")
    parser.add_argument("--include_children", default="true")
    parser.add_argument("--api_url", default="")
    args = parser.parse_args()
    if args.candidate_dir:
        annotate_candidates(args.candidate_dir, args.output, job_id=args.job_id,
                            batch_size=args.batch_size,
                            preferred_chains_by_path=json.loads(args.preferred_chains_json))
        return
    if os.environ.get("BMS_COMPONENT_CONTEXT"):
        raise ValueError("Worker annotation requires explicit candidate artifacts, not host job rows")

    # Compatibility for explicit host-side callers; never used for worker science.
    import requests
    api_base = args.api_url or os.environ.get("API_BASE_URL", "http://localhost:8000")
    include_children = args.include_children.lower() in ("true", "1", "yes")
    response = requests.post(f"{api_base}/api/jobs/{args.job_id}/annotate-cdrs",
                             params={"include_children": include_children}, timeout=10)
    response.raise_for_status()
    print(f"[ANARCII] Triggered annotation for {args.job_id} (include_children={include_children})")


if __name__ == "__main__":
    main()

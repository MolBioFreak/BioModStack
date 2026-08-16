#!/usr/bin/env python3
"""Build a digest-bound alignment-session manifest from explicitly named artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_fasta_sha256(path: Path) -> str:
    sequence = "".join(
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(">")
    )
    if not sequence:
        raise ValueError(f"reference FASTA has no sequence: {path}")
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def parse_required(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("required must be true or false")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--expected-source-reference-sha256", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input-mode", choices=("fastq", "bam", "pod5"), required=True)
    parser.add_argument("--out", type=Path, default=Path("qc_manifest.json"))
    parser.add_argument("--mode", choices=("primary", "dimer_candidates"), default="dimer_candidates")
    parser.add_argument(
        "--artifact",
        action="append",
        nargs=3,
        metavar=("KIND", "PATH", "REQUIRED"),
        default=[],
    )
    args = parser.parse_args()

    declarations = args.artifact or [
        ("reference", "dimer_reference.fasta", "true"),
        ("reference_index", "dimer_reference.fasta.fai", "false"),
        ("dimer_alignment_bam", "dimer_candidates.aligned.bam", "false"),
        ("dimer_alignment_bai", "dimer_candidates.aligned.bam.bai", "false"),
        ("dimer_analysis_summary", "dimer_analysis_summary.tsv", "false"),
    ]
    artifacts: list[dict[str, object]] = []
    for kind, raw_path, raw_required in declarations:
        required = parse_required(raw_required)
        path = Path(raw_path)
        if path.is_file() and not path.is_symlink():
            artifacts.append(
                {
                    "kind": kind,
                    "path": raw_path,
                    "required": required,
                    "state": "present",
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
        else:
            artifacts.append(
                {
                    "kind": kind,
                    "path": None,
                    "required": required,
                    "state": "missing_required" if required else "missing_optional",
                    "sha256": None,
                    "size_bytes": None,
                }
            )

    reference_path = next((Path(raw_path) for kind, raw_path, _required in declarations if kind == "reference"), None)
    if reference_path is None or not reference_path.is_file():
        raise ValueError("alignment-session manifest requires a reference artifact")
    expected_source_sha256 = args.expected_source_reference_sha256.strip().lower()
    if len(expected_source_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_source_sha256):
        raise ValueError("authorized source reference SHA-256 is invalid")
    if args.workflow_id not in {"ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"}:
        raise ValueError("canonical workflow_id is invalid")
    normalized_reference = "".join(
        line.strip().upper()
        for line in reference_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(">")
    )
    midpoint = len(normalized_reference) // 2
    if (
        len(normalized_reference) % 2 != 0
        or normalized_reference[:midpoint] != normalized_reference[midpoint:]
        or hashlib.sha256(normalized_reference[:midpoint].encode("ascii")).hexdigest() != expected_source_sha256
    ):
        raise ValueError("dimer reference is not derived from the authorized source reference")
    payload = {
        "artifact_schema_version": 2,
        "schema": "sequence_qc.manifest.v1",
        "workflow_id": args.workflow_id,
        "job_id": args.job_id,
        "input_mode": args.input_mode,
        "analysis_status": "completed",
        "alignment_session": {
            "mode": args.mode,
            "reference_sequence_sha256": _normalized_fasta_sha256(reference_path),
            "source_reference_sequence_sha256": expected_source_sha256,
            "binding": "authorized source reference binds an exact tandem dimer reference plus BAM and index digests",
        },
        "artifacts": artifacts,
    }
    args.out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

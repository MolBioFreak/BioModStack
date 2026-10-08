#!/usr/bin/env python3
"""Build a typed sequence-QC manifest for FASTQ plasmid-QC artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA_VERSION = 1
REFERENCE_COPY_FALLBACK_NOTE = "reference-copy fallback consensus is not verified"


@dataclass(frozen=True)
class ArtifactSpec:
    kind: str
    path: Path | None
    required: bool = False
    state: str | None = None
    missing_reason: str | None = None
    unavailable_reason: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build qc_manifest.json for FASTQ plasmid QC")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--job-id", required=False, default="unknown")
    parser.add_argument("--sample-name", required=False, default=None)
    parser.add_argument("--reference-fasta", required=True, type=Path)
    parser.add_argument("--summary", required=False, type=Path)
    parser.add_argument("--alignment-stats", required=False, type=Path)
    parser.add_argument("--coverage", required=False, type=Path)
    parser.add_argument("--per-base-support", required=False, type=Path)
    parser.add_argument("--consensus", required=False, type=Path)
    parser.add_argument("--consensus-index", required=False, type=Path)
    parser.add_argument("--consensus-status", required=False, default="not_run")
    parser.add_argument("--alignment-bam", required=False, type=Path)
    parser.add_argument("--alignment-bai", required=False, type=Path)
    parser.add_argument("--reference-index", required=False, type=Path)
    parser.add_argument("--igv-track-config", required=False, type=Path)
    parser.add_argument("--igv-report", required=False, type=Path)
    parser.add_argument("--igv-report-log", required=False, type=Path)
    parser.add_argument("--log", required=False, type=Path)
    return parser.parse_args()


def read_first_fasta_record(path: Path) -> tuple[str, str]:
    header = ""
    chunks: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if chunks:
                    break
                header = line[1:].split()[0]
                continue
            chunks.append(line.upper())
    return (header or "reference", "".join(chunks))


def _relative_path(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.name


def _artifact_state(spec: ArtifactSpec) -> str:
    if spec.state:
        return spec.state
    if spec.path is not None and Path(spec.path).exists():
        return "present"
    if spec.path is not None:
        return "missing_after_workflow" if spec.required else "missing_after_workflow"
    return "not_requested"


def _artifact_payload(spec: ArtifactSpec, manifest_dir: Path) -> dict[str, Any] | None:
    if spec.path is None and spec.state is None:
        return None

    state = _artifact_state(spec)
    payload: dict[str, Any] = {
        "kind": spec.kind,
        "path": _relative_path(spec.path, manifest_dir),
        "required": bool(spec.required),
        "state": state,
    }
    if spec.missing_reason:
        payload["missing_reason"] = spec.missing_reason
    elif spec.path is not None and state == "missing_after_workflow":
        payload["missing_reason"] = f"artifact path not found after workflow: {Path(spec.path).name}"
    if spec.unavailable_reason:
        payload["unavailable_reason"] = spec.unavailable_reason
    return payload


def _consensus_method(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "ok":
        return "samtools_consensus"
    if normalized == "pileup_majority_fallback":
        return "mpileup_majority_fallback"
    if normalized == "reference_copy_fallback":
        return "reference_copy_fallback"
    return normalized or "not_run"


def _interpretation_for_status(status: str) -> dict[str, Any]:
    normalized = status.strip().lower()
    if normalized == "reference_copy_fallback":
        return {
            "verified_construct_status": "fail",
            "notes": [REFERENCE_COPY_FALLBACK_NOTE],
        }
    return {
        "verified_construct_status": "review_required",
        "notes": ["sequence-QC evidence generated; construct verification requires review"],
    }


def build_manifest(
    *,
    out: Path,
    job_id: str,
    sample_name: str | None,
    reference_fasta: Path,
    consensus_fasta: Path | None,
    consensus_status: str,
    artifacts: Iterable[ArtifactSpec],
) -> dict[str, Any]:
    manifest_dir = out.parent.resolve()
    manifest_dir.mkdir(parents=True, exist_ok=True)
    ref_name, ref_seq = read_first_fasta_record(reference_fasta)
    consensus_name = None
    consensus_length = None
    if consensus_fasta is not None and consensus_fasta.exists():
        consensus_name, consensus_seq = read_first_fasta_record(consensus_fasta)
        consensus_length = len(consensus_seq)

    method = _consensus_method(consensus_status)
    is_reference_copy_fallback = method == "reference_copy_fallback"
    payload: dict[str, Any] = {
        "artifact_schema_version": MANIFEST_SCHEMA_VERSION,
        "job_id": str(job_id or "unknown"),
        "sample_name": sample_name or str(job_id or "unknown"),
        "reference": {
            "name": ref_name,
            "path": _relative_path(reference_fasta, manifest_dir),
            "length": len(ref_seq),
        },
        "consensus": {
            "name": consensus_name,
            "path": _relative_path(consensus_fasta, manifest_dir),
            "status": consensus_status,
            "method": method,
            "fallback": is_reference_copy_fallback,
            "length": consensus_length,
        },
        "artifacts": [],
        "interpretation": _interpretation_for_status(consensus_status),
    }

    artifact_list = list(artifacts)
    if not any(spec.kind == "modified_bases" for spec in artifact_list):
        artifact_list.append(
            ArtifactSpec(
                "modified_bases",
                None,
                False,
                state="not_applicable_to_input_mode",
                unavailable_reason="FASTQ-only input does not retain MM/ML modified-base tags; use POD5/BAM modkit workflow for modified-base evidence",
            )
        )

    for spec in artifact_list:
        artifact = _artifact_payload(spec, manifest_dir)
        if artifact is not None:
            payload["artifacts"].append(artifact)

    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def artifact_specs_from_args(args: argparse.Namespace) -> list[ArtifactSpec]:
    specs = [
        ArtifactSpec("reference", args.reference_fasta, True),
        ArtifactSpec(
            "modified_bases",
            None,
            False,
            state="not_applicable_to_input_mode",
            unavailable_reason="FASTQ-only input does not retain MM/ML modified-base tags; use POD5/BAM modkit workflow for modified-base evidence",
        ),
    ]
    maybe_specs = [
        ("reference_index", args.reference_index, False),
        ("summary", args.summary, True),
        ("alignment_stats", args.alignment_stats, False),
        ("coverage", args.coverage, False),
        ("per_base_support", args.per_base_support, True),
        ("consensus", args.consensus, False),
        ("consensus_index", args.consensus_index, False),
        ("alignment_bam", args.alignment_bam, False),
        ("alignment_bai", args.alignment_bai, False),
        ("igv_track_config", args.igv_track_config, False),
        ("igv_report", args.igv_report, False),
        ("log", args.igv_report_log, False),
        ("log", args.log, False),
    ]
    for kind, path, required in maybe_specs:
        if path is not None:
            specs.append(ArtifactSpec(kind, path, required))
    return specs


def main() -> int:
    args = parse_args()
    build_manifest(
        out=args.out,
        job_id=args.job_id,
        sample_name=args.sample_name,
        reference_fasta=args.reference_fasta,
        consensus_fasta=args.consensus,
        consensus_status=args.consensus_status,
        artifacts=artifact_specs_from_args(args),
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

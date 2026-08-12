#!/usr/bin/env python3
"""Build a typed sequence-QC manifest for FASTQ plasmid-QC artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA_VERSION = 2
CONSENSUS_METHOD = "samtools_1.24_bayesian_consensus"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input-mode", required=True)
    parser.add_argument("--sample-name", required=False, default=None)
    parser.add_argument("--reference-fasta", required=True, type=Path)
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Expected reference SHA-256 from upstream provenance; never inferred from the observed file",
    )
    parser.add_argument("--summary", required=False, type=Path)
    parser.add_argument("--read-lengths", required=False, type=Path)
    parser.add_argument("--alignment-stats", required=False, type=Path)
    parser.add_argument("--coverage", required=False, type=Path)
    parser.add_argument("--per-base-support", required=False, type=Path)
    parser.add_argument("--consensus", required=False, type=Path)
    parser.add_argument("--consensus-index", required=False, type=Path)
    parser.add_argument("--consensus-log", required=False, type=Path)
    parser.add_argument("--consensus-status", required=False, default="not_run")
    parser.add_argument("--workflow-status", required=False, default=None)
    parser.add_argument("--verification-status", required=False, default=None)
    parser.add_argument("--verification-reason-code", action="append", default=[])
    parser.add_argument("--alignment-bam", required=False, type=Path)
    parser.add_argument("--alignment-bai", required=False, type=Path)
    parser.add_argument("--reference-index", required=False, type=Path)
    parser.add_argument("--igv-coverage-depth", required=False, type=Path)
    parser.add_argument("--igv-position-gradient", required=False, type=Path)
    parser.add_argument("--igv-gc-content", required=False, type=Path)
    parser.add_argument("--igv-gc-zscore", required=False, type=Path)
    parser.add_argument("--igv-split-read-density", required=False, type=Path)
    parser.add_argument("--igv-softclip-density", required=False, type=Path)
    parser.add_argument("--igv-junction-hotspots", required=False, type=Path)
    parser.add_argument("--igv-report-sites-bed", required=False, type=Path)
    parser.add_argument("--igv-report-sites-tsv", required=False, type=Path)
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
    if state == "present" and spec.path is not None:
        artifact_path = Path(spec.path)
        if artifact_path.is_file():
            payload["sha256"] = _sha256_file(artifact_path)
            payload["size_bytes"] = artifact_path.stat().st_size
    return payload


def _consensus_method(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"ok", "samtools_consensus", CONSENSUS_METHOD}:
        return CONSENSUS_METHOD
    if "fallback" in normalized:
        raise ValueError("consensus fallback status labels are forbidden")
    raise ValueError(
        f"unsupported consensus method/status {status!r}; "
        f"required method is {CONSENSUS_METHOD}"
    )


def _interpretation_for_status(status: str) -> dict[str, Any]:
    return {
        "verified_construct_status": "review_required",
        "notes": ["sequence-QC evidence generated; construct verification requires review"],
    }


def _status_contract(
    *,
    observed_sequence_sha256: str | None,
    workflow_status: str | None,
    verification_status: str | None,
    verification_reason_codes: Iterable[str],
) -> tuple[str, str, list[str]]:
    normalized_workflow = (workflow_status or "").strip().lower()
    if not normalized_workflow:
        normalized_workflow = (
            "completed" if observed_sequence_sha256 else "completed_with_unavailable_observation"
        )

    normalized_verification = (verification_status or "review_required").strip().lower()
    if normalized_verification not in {"pass", "fail", "review_required"}:
        raise ValueError(f"unsupported verification status: {verification_status!r}")
    if normalized_verification == "pass" and not observed_sequence_sha256:
        raise ValueError("verification status cannot be pass without an observed consensus")
    if normalized_verification == "pass":
        raise ValueError("verification status pass is unavailable in Phase 1 without independent verification evidence")

    reasons = [str(reason).strip() for reason in verification_reason_codes if str(reason).strip()]
    if not reasons:
        reasons = [
            "phase1_manual_review_required"
            if observed_sequence_sha256
            else "observed_consensus_unavailable"
        ]
    return normalized_workflow, normalized_verification, reasons


def build_manifest(
    *,
    out: Path,
    job_id: str,
    sample_name: str | None,
    reference_fasta: Path,
    consensus_fasta: Path | None,
    consensus_status: str,
    artifacts: Iterable[ArtifactSpec],
    expected_sha256: str,
    workflow_id: str = "ont_fastq_qc",
    input_mode: str = "fastq",
    workflow_status: str | None = None,
    verification_status: str | None = None,
    verification_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id or normalized_job_id.lower() == "unknown":
        raise ValueError("sequence-QC manifest requires an exact non-placeholder job identity")
    normalized_workflow_id = str(workflow_id or "").strip()
    normalized_input_mode = str(input_mode or "").strip()
    if not normalized_workflow_id or not normalized_input_mode:
        raise ValueError("sequence-QC manifest requires workflow_id and input_mode")
    manifest_dir = out.parent.resolve()
    manifest_dir.mkdir(parents=True, exist_ok=True)
    ref_name, ref_seq = read_first_fasta_record(reference_fasta)
    observed_reference_sha256 = hashlib.sha256(ref_seq.encode("ascii")).hexdigest()
    normalized_provided_expected_sha256 = str(expected_sha256).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized_provided_expected_sha256) is None:
        raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256 digest")
    if normalized_provided_expected_sha256 != observed_reference_sha256:
        raise ValueError("expected_sha256 does not match the normalized expected reference sequence")
    expected_sequence_sha256 = normalized_provided_expected_sha256
    reference_source_sha256 = hashlib.sha256(reference_fasta.read_bytes()).hexdigest()
    consensus_name = None
    consensus_length = None
    observed_sequence_sha256: str | None = None
    if consensus_fasta is not None and consensus_fasta.exists():
        consensus_name, consensus_seq = read_first_fasta_record(consensus_fasta)
        consensus_length = len(consensus_seq)
        observed_sequence_sha256 = hashlib.sha256(consensus_seq.encode("ascii")).hexdigest()

    method = _consensus_method(consensus_status)
    normalized_workflow_status, normalized_verification_status, normalized_reason_codes = _status_contract(
        observed_sequence_sha256=observed_sequence_sha256,
        workflow_status=workflow_status,
        verification_status=verification_status,
        verification_reason_codes=verification_reason_codes,
    )
    payload: dict[str, Any] = {
        "schema": "sequence_qc.manifest.v1",
        "artifact_schema_version": MANIFEST_SCHEMA_VERSION,
        "workflow_id": normalized_workflow_id,
        "job_id": normalized_job_id,
        "input_mode": normalized_input_mode,
        "analysis_status": normalized_workflow_status,
        "sample_name": sample_name or normalized_job_id,
        "workflow_status": normalized_workflow_status,
        "verification_status": normalized_verification_status,
        "verification_reason_codes": normalized_reason_codes,
        "sequence_digests": {
            "algorithm": "sha256",
            "normalization": "uppercase FASTA sequence with headers and whitespace removed",
            "expected_reference_sha256": expected_sequence_sha256,
            "observed_consensus_sha256": observed_sequence_sha256,
        },
        "reference": {
            "name": ref_name,
            "path": _relative_path(reference_fasta, manifest_dir),
            "length": len(ref_seq),
            "role": "expected_reference",
            "expected_sha256": expected_sequence_sha256,
            "source_file_sha256": reference_source_sha256,
            "provided_expected_sha256": normalized_provided_expected_sha256,
        },
        "alignment_session": {
            "mode": "primary",
            "reference_sequence_sha256": expected_sequence_sha256,
            "binding": "server-generated manifest binds BAM, index, and normalized reference digests",
        },
        "consensus": {
            "name": consensus_name,
            "path": _relative_path(consensus_fasta, manifest_dir),
            "status": consensus_status,
            "method": method,
            "fallback": False,
            "length": consensus_length,
            "role": "observed_consensus",
            "observed_sha256": observed_sequence_sha256,
            "provenance": {
                "source": "aligned_reads" if observed_sequence_sha256 else "unavailable",
                "method": method,
            },
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

    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
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
        ("read_lengths", args.read_lengths, True),
        ("alignment_stats", args.alignment_stats, False),
        ("coverage", args.coverage, False),
        ("per_base_support", args.per_base_support, True),
        ("consensus", args.consensus, True),
        ("consensus_index", args.consensus_index, True),
        ("consensus_log", args.consensus_log, False),
        ("alignment_bam", args.alignment_bam, False),
        ("alignment_bai", args.alignment_bai, False),
        ("igv_coverage_depth", args.igv_coverage_depth, False),
        ("igv_position_gradient", args.igv_position_gradient, False),
        ("igv_gc_content", args.igv_gc_content, False),
        ("igv_gc_zscore", args.igv_gc_zscore, False),
        ("igv_split_read_density", args.igv_split_read_density, False),
        ("igv_softclip_density", args.igv_softclip_density, False),
        ("igv_junction_hotspots", args.igv_junction_hotspots, False),
        ("igv_report_sites_bed", args.igv_report_sites_bed, False),
        ("igv_report_sites_tsv", args.igv_report_sites_tsv, False),
        ("igv_track_config", args.igv_track_config, False),
        ("igv_report", args.igv_report, True),
        ("log", args.igv_report_log, False),
        ("log", args.log, False),
    ]
    for kind, path, required in maybe_specs:
        if path is not None:
            specs.append(
                ArtifactSpec(
                    kind,
                    path,
                    required,
                )
            )
    return specs


def main() -> int:
    args = parse_args()
    build_manifest(
        out=args.out,
        job_id=args.job_id,
        workflow_id=args.workflow_id,
        input_mode=args.input_mode,
        sample_name=args.sample_name,
        reference_fasta=args.reference_fasta,
        consensus_fasta=args.consensus,
        consensus_status=args.consensus_status,
        artifacts=artifact_specs_from_args(args),
        expected_sha256=args.expected_sha256,
        workflow_status=args.workflow_status,
        verification_status=args.verification_status,
        verification_reason_codes=args.verification_reason_code,
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

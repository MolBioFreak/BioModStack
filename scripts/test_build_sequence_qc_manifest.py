from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_sequence_qc_manifest.py"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import pytest

from services.sequence_qc_manifest import load_sequence_qc_manifest  # noqa: E402


def _load_module():
    spec = importlib.util.spec_from_file_location("build_sequence_qc_manifest_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_sequence_qc_manifest_declares_typed_artifacts_and_sequence_provenance(tmp_path: Path) -> None:
    module = _load_module()
    reference = tmp_path / "reference_qc.fasta"
    reference.write_text(">plasmid example\nACGT\n", encoding="utf-8")
    consensus = tmp_path / "fastq_consensus.fasta"
    consensus.write_text(">plasmid\nACGT\n", encoding="utf-8")
    for name in [
        "fastq_qc_summary.tsv",
        "fastq_alignment_stats.tsv",
        "fastq_coverage.tsv",
        "per_base_support.tsv",
        "fastq_consensus.fasta.fai",
        "fastq_qc.log",
    ]:
        (tmp_path / name).write_text("placeholder\n", encoding="utf-8")

    manifest = module.build_manifest(
        out=tmp_path / "qc_manifest.json",
        job_id="job-42",
        sample_name="sample-A",
        reference_fasta=reference,
        consensus_fasta=consensus,
        consensus_status="samtools_consensus",
        artifacts=[
            module.ArtifactSpec("summary", tmp_path / "fastq_qc_summary.tsv", True),
            module.ArtifactSpec("alignment_stats", tmp_path / "fastq_alignment_stats.tsv", True),
            module.ArtifactSpec("coverage", tmp_path / "fastq_coverage.tsv", True),
            module.ArtifactSpec("per_base_support", tmp_path / "per_base_support.tsv", True),
            module.ArtifactSpec("consensus", consensus, True),
            module.ArtifactSpec("consensus_index", tmp_path / "fastq_consensus.fasta.fai", True),
            module.ArtifactSpec("igv_report", tmp_path / "missing_igv_report.html", False),
            module.ArtifactSpec("log", tmp_path / "fastq_qc.log", True),
        ],
    )

    assert manifest["artifact_schema_version"] == 2
    assert manifest["job_id"] == "job-42"
    assert manifest["reference"]["name"] == "plasmid"
    assert manifest["reference"]["path"] == "reference_qc.fasta"
    assert manifest["reference"]["length"] == 4
    assert manifest["reference"]["expected_sha256"] == manifest["sequence_digests"]["expected_reference_sha256"]
    assert manifest["reference"]["source_file_sha256"]
    assert manifest["consensus"]["status"] == "samtools_consensus"
    assert manifest["consensus"]["fallback"] is False
    assert manifest["consensus"]["observed_sha256"] == manifest["sequence_digests"]["observed_consensus_sha256"]
    assert manifest["sequence_digests"]["expected_reference_sha256"] == manifest["sequence_digests"]["observed_consensus_sha256"]
    assert manifest["interpretation"]["verified_construct_status"] == "review_required"
    assert {artifact["kind"] for artifact in manifest["artifacts"]} >= {"summary", "per_base_support", "igv_report", "modified_bases"}
    modified_bases = next(artifact for artifact in manifest["artifacts"] if artifact["kind"] == "modified_bases")
    assert modified_bases["state"] == "not_applicable_to_input_mode"
    assert modified_bases["path"] is None
    assert "FASTQ-only" in modified_bases["unavailable_reason"]

    assert manifest["workflow_status"] == "completed"
    assert manifest["verification_status"] == "review_required"
    assert manifest["verification_reason_codes"] == ["phase1_manual_review_required"]

    normalized = load_sequence_qc_manifest(tmp_path / "qc_manifest.json")
    missing_report = next(artifact for artifact in normalized["artifacts"] if artifact["kind"] == "igv_report")
    assert missing_report["required"] is False
    assert missing_report["path"] is None
    assert missing_report["state"] == "missing_after_workflow"
    assert missing_report["declared_path"] == "missing_igv_report.html"


def test_build_sequence_qc_manifest_rejects_reference_copy_fallback(tmp_path: Path) -> None:
    module = _load_module()
    reference = tmp_path / "reference.fasta"
    consensus = tmp_path / "consensus.fasta"
    reference.write_text(">expected\nACGT\n", encoding="utf-8")
    consensus.write_text(">observed\nACGT\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reference_copy_fallback is forbidden"):
        module.build_manifest(
            out=tmp_path / "qc_manifest.json",
            job_id="job-fallback",
            sample_name="sample-fallback",
            reference_fasta=reference,
            consensus_fasta=consensus,
            consensus_status="reference_copy_fallback",
            artifacts=[],
        )


def test_manifest_rejects_mismatched_expected_digest_and_phase1_pass(tmp_path: Path) -> None:
    module = _load_module()
    reference = tmp_path / "reference.fasta"
    consensus = tmp_path / "consensus.fasta"
    reference.write_text(">expected\nACGT\n", encoding="utf-8")
    consensus.write_text(">observed\nACGT\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected_sha256 does not match"):
        module.build_manifest(
            out=tmp_path / "digest-mismatch.json",
            job_id="job-digest-mismatch",
            sample_name="sample-digest-mismatch",
            reference_fasta=reference,
            consensus_fasta=consensus,
            consensus_status="samtools_consensus",
            artifacts=[],
            expected_sha256="0" * 64,
        )

    with pytest.raises(ValueError, match="pass is unavailable in Phase 1"):
        module.build_manifest(
            out=tmp_path / "invalid-phase1-pass.json",
            job_id="job-invalid-pass",
            sample_name="sample-invalid-pass",
            reference_fasta=reference,
            consensus_fasta=consensus,
            consensus_status="samtools_consensus",
            artifacts=[],
            verification_status="pass",
        )


def test_missing_consensus_is_typed_incomplete_and_cannot_pass(tmp_path: Path) -> None:
    module = _load_module()
    reference = tmp_path / "reference.fasta"
    reference.write_text(">expected\nACGT\n", encoding="utf-8")
    missing_consensus = tmp_path / "fastq_consensus.fasta"

    manifest = module.build_manifest(
        out=tmp_path / "qc_manifest.json",
        job_id="job-no-consensus",
        sample_name="sample-no-consensus",
        reference_fasta=reference,
        consensus_fasta=missing_consensus,
        consensus_status="unavailable",
        artifacts=[
            module.ArtifactSpec(
                "consensus",
                missing_consensus,
                False,
                missing_reason="Unable to derive observed consensus from aligned reads",
            )
        ],
    )

    assert manifest["workflow_status"] == "completed_with_unavailable_observation"
    assert manifest["verification_status"] == "review_required"
    assert manifest["verification_reason_codes"] == ["observed_consensus_unavailable"]
    assert manifest["sequence_digests"]["observed_consensus_sha256"] is None
    assert manifest["consensus"]["path"] == "fastq_consensus.fasta"
    assert manifest["consensus"]["observed_sha256"] is None
    consensus_artifact = next(item for item in manifest["artifacts"] if item["kind"] == "consensus")
    assert consensus_artifact["state"] == "missing_after_workflow"
    assert "Unable to derive observed consensus" in consensus_artifact["missing_reason"]

    normalized = load_sequence_qc_manifest(tmp_path / "qc_manifest.json")
    normalized_consensus = next(item for item in normalized["artifacts"] if item["kind"] == "consensus")
    assert normalized_consensus["path"] is None
    assert normalized_consensus["declared_path"] == "fastq_consensus.fasta"

    with pytest.raises(ValueError, match="cannot be pass without an observed consensus"):
        module.build_manifest(
            out=tmp_path / "invalid-pass.json",
            job_id="job-invalid-pass",
            sample_name="sample-invalid-pass",
            reference_fasta=reference,
            consensus_fasta=missing_consensus,
            consensus_status="unavailable",
            artifacts=[],
            verification_status="pass",
        )


def test_build_sequence_qc_manifest_cli_writes_json(tmp_path: Path) -> None:
    reference = tmp_path / "reference_qc.fasta"
    reference.write_text(">plasmid\nAC\n", encoding="utf-8")
    consensus = tmp_path / "fastq_consensus.fasta"
    consensus.write_text(">plasmid\nAC\n", encoding="utf-8")
    summary = tmp_path / "fastq_qc_summary.tsv"
    summary.write_text("metric\tvalue\n", encoding="utf-8")
    support = tmp_path / "per_base_support.tsv"
    support.write_text("chrom\tposition_1based\n", encoding="utf-8")
    bam = tmp_path / "aligned.bam"
    bai = tmp_path / "aligned.bam.bai"
    bam.write_bytes(b"BAM\n")
    bai.write_bytes(b"BAI\n")
    output = tmp_path / "qc_manifest.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--out",
            str(output),
            "--job-id",
            "job-cli",
            "--sample-name",
            "sample-cli",
            "--reference-fasta",
            str(reference),
            "--summary",
            str(summary),
            "--per-base-support",
            str(support),
            "--consensus",
            str(consensus),
            "--consensus-status",
            "ok",
            "--alignment-bam",
            str(bam),
            "--alignment-bai",
            str(bai),
            "--igv-report",
            str(tmp_path / "not-created.html"),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stderr == ""
    assert "qc_manifest.json" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["job_id"] == "job-cli"
    assert payload["sample_name"] == "sample-cli"
    assert any(artifact["kind"] == "per_base_support" for artifact in payload["artifacts"])
    assert any(artifact["kind"] == "alignment_bam" and artifact["path"] == "aligned.bam" for artifact in payload["artifacts"])
    assert any(artifact["kind"] == "alignment_bai" and artifact["path"] == "aligned.bam.bai" for artifact in payload["artifacts"])
    assert any(artifact["kind"] == "igv_report" and artifact["required"] is False and artifact["state"] == "missing_after_workflow" for artifact in payload["artifacts"])
    assert any(artifact["kind"] == "modified_bases" and artifact["state"] == "not_applicable_to_input_mode" for artifact in payload["artifacts"])

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.sequence_qc_manifest import (  # noqa: E402
    SequenceQcManifestError,
    find_manifest_for_job,
    load_sequence_qc_manifest,
)
from routers import sequence_qc  # noqa: E402


def _write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_load_sequence_qc_manifest_normalizes_existing_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "job-1" / "fastq_qc"
    (run_dir / "fastq_qc_summary.tsv").parent.mkdir(parents=True)
    (run_dir / "fastq_qc_summary.tsv").write_text("metric\tvalue\n", encoding="utf-8")
    (run_dir / "per_base_support.tsv").write_text("chrom\tposition_1based\n", encoding="utf-8")

    manifest_path = run_dir / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-1",
            "sample_name": "example plasmid",
            "reference": {"name": "plasmid", "path": "reference.fasta", "length": 12},
            "consensus": {
                "path": "fastq_consensus.fasta",
                "status": "reference_copy_fallback",
                "method": "reference_copy_fallback",
                "fallback": True,
                "length": 12,
            },
            "artifacts": [
                {"kind": "summary", "path": "fastq_qc_summary.tsv", "required": True},
                {"kind": "per_base_support", "path": "per_base_support.tsv", "required": True},
                {"kind": "igv_report", "path": "missing_optional.html", "required": False},
            ],
            "interpretation": {
                "verified_construct_status": "fail",
                "notes": ["reference-copy fallback consensus is not verified"],
            },
        },
    )

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["artifact_schema_version"] == 1
    assert manifest["job_id"] == "job-1"
    assert "manifest_path" not in manifest
    assert "manifest_dir" not in manifest
    assert manifest["reference"]["declared_path"] == "reference.fasta"
    assert manifest["reference"]["path"] is None
    assert manifest["reference"]["exists"] is False
    assert manifest["consensus"]["fallback"] is True
    assert manifest["interpretation"]["verified_construct_status"] == "fail"
    assert manifest["artifacts"][0]["exists"] is True
    assert manifest["artifacts"][0]["state"] == "present"
    assert manifest["artifacts"][0]["size_bytes"] == len("metric\tvalue\n")
    assert manifest["artifacts"][1]["schema"] == "sequence_qc.per_base_support.v1"
    assert manifest["artifacts"][1]["state"] == "present"
    assert manifest["artifacts"][2]["exists"] is False
    assert manifest["artifacts"][2]["required"] is False
    assert manifest["artifacts"][2]["state"] == "missing_optional"
    assert manifest["artifacts"][2]["path"] is None
    assert manifest["artifacts"][2]["declared_path"] == "missing_optional.html"


def test_load_sequence_qc_manifest_allows_unavailable_artifacts_without_fake_paths(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-fastq-only",
            "artifacts": [
                {
                    "kind": "modified_bases",
                    "required": False,
                    "state": "not_applicable_to_input_mode",
                    "unavailable_reason": "FASTQ-only input does not retain MM/ML modified-base tags",
                },
                {
                    "kind": "igv_report",
                    "path": "report.html",
                    "required": False,
                    "state": "missing_after_workflow",
                    "missing_reason": "IGV report process completed without report.html",
                },
            ],
        },
    )

    manifest = load_sequence_qc_manifest(manifest_path)

    modified_bases = manifest["artifacts"][0]
    assert modified_bases["path"] is None
    assert modified_bases["declared_path"] is None
    assert modified_bases["exists"] is False
    assert modified_bases["state"] == "not_applicable_to_input_mode"
    assert "FASTQ-only" in modified_bases["unavailable_reason"]

    missing_report = manifest["artifacts"][1]
    assert missing_report["path"] is None
    assert missing_report["declared_path"] == "report.html"
    assert missing_report["state"] == "missing_after_workflow"
    assert "report.html" in missing_report["missing_reason"]


def test_load_sequence_qc_manifest_rejects_path_escape(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-escape",
            "artifacts": [
                {"kind": "summary", "path": "../outside.tsv", "required": True},
            ],
        },
    )

    with pytest.raises(SequenceQcManifestError, match="escapes"):
        load_sequence_qc_manifest(manifest_path)


def test_load_sequence_qc_manifest_rejects_absolute_artifact_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-absolute",
            "artifacts": [
                {"kind": "summary", "path": str(tmp_path / "summary.tsv"), "required": False},
            ],
        },
    )

    with pytest.raises(SequenceQcManifestError, match="relative"):
        load_sequence_qc_manifest(manifest_path)


def test_load_sequence_qc_manifest_rejects_top_level_reference_path_escape(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-reference-escape",
            "reference": {"name": "plasmid", "path": "../outside.fasta", "length": 4},
            "artifacts": [],
        },
    )

    with pytest.raises(SequenceQcManifestError, match="reference path escapes"):
        load_sequence_qc_manifest(manifest_path)


def test_load_sequence_qc_manifest_rejects_top_level_consensus_symlink_escape(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "job" / "fastq_qc"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    (outside_dir / "consensus.fasta").write_text(">x\nAC\n", encoding="utf-8")
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "linked").symlink_to(outside_dir, target_is_directory=True)
    manifest_path = manifest_dir / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-consensus-escape",
            "consensus": {"path": "linked/consensus.fasta", "status": "ok"},
            "artifacts": [],
        },
    )

    with pytest.raises(SequenceQcManifestError, match="consensus path escapes"):
        load_sequence_qc_manifest(manifest_path)


def test_reference_copy_fallback_forces_failed_verified_status(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-fallback",
            "consensus": {"method": "reference_copy_fallback", "status": "ok"},
            "interpretation": {"verified_construct_status": "pass", "notes": []},
            "artifacts": [],
        },
    )

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["consensus"]["fallback"] is True
    assert manifest["interpretation"]["verified_construct_status"] == "fail"
    assert "reference-copy fallback consensus is not verified" in manifest["interpretation"]["notes"]


def test_load_sequence_qc_manifest_rejects_required_missing_artifact(tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-missing",
            "artifacts": [
                {"kind": "summary", "path": "missing.tsv", "required": True},
            ],
        },
    )

    with pytest.raises(SequenceQcManifestError, match="required artifact missing"):
        load_sequence_qc_manifest(manifest_path)


def test_find_manifest_for_job_prefers_fastq_qc_subdir(tmp_path: Path) -> None:
    results_dir = tmp_path / "bms_results"
    run_dir = results_dir / "job-1" / "fastq_qc"
    manifest_path = run_dir / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-1",
            "artifacts": [],
        },
    )

    assert find_manifest_for_job("job-1", results_dir=results_dir) == manifest_path


def test_find_manifest_for_job_rejects_symlink_escape(tmp_path: Path) -> None:
    results_dir = tmp_path / "bms_results"
    outside_dir = tmp_path / "outside"
    outside_manifest = outside_dir / "qc_manifest.json"
    _write_manifest(
        outside_manifest,
        {
            "artifact_schema_version": 1,
            "job_id": "job-1",
            "artifacts": [],
        },
    )

    job_dir = results_dir / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "fastq_qc").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(SequenceQcManifestError, match="escapes"):
        find_manifest_for_job("job-1", results_dir=results_dir)


def test_find_manifest_for_job_rejects_unsafe_job_id(tmp_path: Path) -> None:
    with pytest.raises(SequenceQcManifestError, match="unsafe job_id"):
        find_manifest_for_job("../escape", results_dir=tmp_path)


@pytest.mark.asyncio
async def test_sequence_qc_manifest_by_path_route_loads_allowed_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest_path = tmp_path / "qc_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "artifact_schema_version": 1,
            "job_id": "job-route",
            "artifacts": [],
        },
    )
    monkeypatch.setattr(sequence_qc, "resolve_allowed_path", lambda raw_path: manifest_path)

    response = await sequence_qc.get_sequence_qc_manifest_by_path("bms_results/job-route/fastq_qc/qc_manifest.json")

    assert response["job_id"] == "job-route"
    assert "manifest_path" not in response
    assert "manifest_dir" not in response


@pytest.mark.asyncio
async def test_sequence_qc_manifest_by_path_route_maps_escape_to_403(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_escape(raw_path: str) -> Path:
        raise ValueError("Path escapes allowed root")

    monkeypatch.setattr(sequence_qc, "resolve_allowed_path", reject_escape)

    with pytest.raises(HTTPException) as exc_info:
        await sequence_qc.get_sequence_qc_manifest_by_path("bms_results/linked/qc_manifest.json")

    assert exc_info.value.status_code == 403
    assert "escapes" in str(exc_info.value.detail)

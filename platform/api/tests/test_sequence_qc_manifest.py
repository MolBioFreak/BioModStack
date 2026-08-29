from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.sequence_qc_manifest import (  # noqa: E402
    SequenceQcManifestError,
    find_canonical_fastq_manifest,
    find_manifest_for_job,
    find_manifest_in_result_root,
    load_sequence_qc_manifest,
)
from routers import sequence_qc  # noqa: E402


def test_legacy_governed_manifest_route_uses_closed_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions
    from services.ngs_alignment_sessions import AlignmentSessionError

    job_id = "00000000-0000-4000-8000-000000000001"
    job = SimpleNamespace(
        id=job_id,
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    )
    app = FastAPI()
    app.add_exception_handler(
        ngs_alignment_sessions.OntNgsRouteError,
        ngs_alignment_sessions.ont_ngs_route_error_handler,
    )
    app.include_router(sequence_qc.router, prefix="/api")
    app.dependency_overrides[ngs_alignment_sessions.require_alignment_job] = lambda: job
    @asynccontextmanager
    async def failed_package(_job):
        raise AlignmentSessionError("secret path /tmp/result")
        yield  # pragma: no cover

    monkeypatch.setattr(sequence_qc, "_validated_pinned_result_root", failed_package)

    response = TestClient(app).get(f"/api/jobs/{job_id}/manifest")
    assert response.status_code == 409
    assert response.json() == {
        "schema": "bms.ngs.error.v1",
        "code": "NGS_PACKAGE_INTEGRITY_CONFLICT",
        "message": "The governed result package failed integrity validation.",
        "job_id": job_id,
        "resource": "manifest",
        "retryable": False,
    }


def _write_manifest(path: Path, payload: dict) -> None:
    if path.parent.name != "verification":
        payload = {
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "analysis_status": "completed",
            **payload,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_legacy_governed_manifest_route_reads_from_retained_root_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions

    job_id = "00000000-0000-4000-8000-000000000004"
    root = tmp_path / "result"

    def manifest_payload(sample_name: str) -> dict:
        return {
            "artifact_schema_version": 1,
            "job_id": job_id,
            "sample_name": sample_name,
            "reference": {"name": "plasmid", "path": "reference.fasta", "length": 4},
            "consensus": {"path": "consensus.fasta", "status": "ok", "method": "samtools_1.24_bayesian_consensus", "fallback": False, "length": 4},
            "artifacts": [],
            "interpretation": {"verified_construct_status": "review", "notes": []},
        }

    _write_manifest(root / "fastq_qc/qc_manifest.json", manifest_payload("original"))
    job = SimpleNamespace(
        id=job_id,
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    )

    @asynccontextmanager
    async def replace_after_validation(_job):
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            held = tmp_path / "held-result"
            root.rename(held)
            _write_manifest(root / "fastq_qc/qc_manifest.json", manifest_payload("replacement"))
            yield Path(f"/proc/self/fd/{descriptor}")
        finally:
            os.close(descriptor)

    monkeypatch.setattr(sequence_qc, "_validated_pinned_result_root", replace_after_validation)
    app = FastAPI()
    app.add_exception_handler(
        ngs_alignment_sessions.OntNgsRouteError,
        ngs_alignment_sessions.ont_ngs_route_error_handler,
    )
    app.include_router(sequence_qc.router, prefix="/api")
    app.dependency_overrides[ngs_alignment_sessions.require_alignment_job] = lambda: job

    response = TestClient(app).get(f"/api/jobs/{job_id}/manifest")

    assert response.status_code == 200, response.text
    assert response.json()["sample_name"] == "original"


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
                "status": "ok",
                "method": "samtools_1.24_bayesian_consensus",
                "fallback": False,
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
    assert manifest["consensus"]["fallback"] is False
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


def test_consensus_fallback_status_labels_are_rejected(tmp_path: Path) -> None:
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

    with pytest.raises(SequenceQcManifestError, match="fallback status labels are forbidden"):
        load_sequence_qc_manifest(manifest_path)


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

    with pytest.raises(SequenceQcManifestError, match="symlink|escapes"):
        find_manifest_for_job("job-1", results_dir=results_dir)


def test_find_manifest_for_job_rejects_symlinked_parent_inside_result_root(tmp_path: Path) -> None:
    results_dir = tmp_path / "bms_results"
    real_dir = results_dir / "real-job" / "fastq_qc"
    real_dir.mkdir(parents=True)
    _write_manifest(
        real_dir / "qc_manifest.json",
        {"artifact_schema_version": 1, "job_id": "job-1", "artifacts": []},
    )
    job_dir = results_dir / "job-1"
    job_dir.mkdir()
    (job_dir / "fastq_qc").symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(SequenceQcManifestError, match="symlink"):
        find_manifest_for_job("job-1", results_dir=results_dir)


def test_find_manifest_in_result_root_rejects_symlinked_parent_inside_root(tmp_path: Path) -> None:
    result_root = tmp_path / "job"
    real_dir = result_root / "real-fastq-qc"
    real_dir.mkdir(parents=True)
    _write_manifest(
        real_dir / "qc_manifest.json",
        {"artifact_schema_version": 1, "job_id": "job-1", "artifacts": []},
    )
    (result_root / "fastq_qc").symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(SequenceQcManifestError, match="symlink"):
        find_manifest_in_result_root(result_root)


def test_canonical_fastq_manifest_ignores_verification_manifest(tmp_path: Path) -> None:
    result_root = tmp_path / "job"
    _write_manifest(
        result_root / "verification" / "qc_manifest.json",
        {"schema": "biomodstack.construct_verification.v2", "artifacts": []},
    )
    canonical = result_root / "fastq_qc" / "qc_manifest.json"
    _write_manifest(
        canonical,
        {"artifact_schema_version": 1, "job_id": "job-1", "artifacts": []},
    )

    assert find_canonical_fastq_manifest(result_root) == canonical


@pytest.mark.asyncio
async def test_ngs_manifest_receipt_resolver_rejects_symlinked_fastq_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from services import molbio_ngs_member_receipts as receipts

    result_root = tmp_path / "job"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "qc_manifest.json").write_text("{}", encoding="utf-8")
    result_root.mkdir()
    (result_root / "fastq_qc").symlink_to(outside, target_is_directory=True)
    job = SimpleNamespace(id="job-receipt", model_id="nanopore", params={"ont_workflow_id": "ont_fastq_qc"})

    class Session:
        async def get(self, _model, _job_id):
            return job

    monkeypatch.setattr(receipts, "resolve_persisted_job_result_root", lambda _job: result_root)

    with pytest.raises(SequenceQcManifestError, match="symlink"):
        await receipts.resolve_ngs_result_manifest_receipt(Session(), job_id=job.id)


@pytest.mark.asyncio
async def test_job_manifest_route_uses_canonical_fastq_manifest_for_ont_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = type(
        "JobStub",
        (),
        {
            "id": "job-route",
            "params": {"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        },
    )()
    canonical = tmp_path / "fastq_qc" / "qc_manifest.json"
    manifest_bytes = b'{"schema":"sequence_qc.manifest.v1"}'
    @asynccontextmanager
    async def pinned_root(_job):
        yield tmp_path

    monkeypatch.setattr(sequence_qc, "_validated_pinned_result_root", pinned_root)
    monkeypatch.setattr(sequence_qc, "find_canonical_fastq_manifest", lambda _root, **_kwargs: canonical)
    monkeypatch.setattr(
        sequence_qc,
        "find_manifest_in_result_root",
        lambda _root, **_kwargs: (_ for _ in ()).throw(AssertionError("verification manifest finder was used")),
    )
    monkeypatch.setattr(
        sequence_qc,
        "read_manifest_json_nofollow",
        lambda _path, **_kwargs: ({"schema": "sequence_qc.manifest.v1"}, manifest_bytes, "a" * 64, len(manifest_bytes)),
    )
    monkeypatch.setattr(
        sequence_qc,
        "load_sequence_qc_manifest",
        lambda *_args, **kwargs: {"raw_bytes": kwargs["raw_bytes"], "schema": "sequence_qc.manifest.v1"},
    )

    result = await sequence_qc.get_sequence_qc_manifest_for_job("job-route", job)

    assert result["raw_bytes"] == manifest_bytes


def test_find_manifest_for_job_rejects_unsafe_job_id(tmp_path: Path) -> None:
    with pytest.raises(SequenceQcManifestError, match="unsafe job_id"):
        find_manifest_for_job("../escape", results_dir=tmp_path)


def test_sequence_qc_manifest_has_no_caller_selected_path_route() -> None:
    assert not hasattr(sequence_qc, "get_sequence_qc_manifest_by_path")
    assert not hasattr(sequence_qc, "resolve_allowed_path")
    assert all("{path" not in str(getattr(route, "path", "")) for route in sequence_qc.router.routes)


def test_molbio_evidence_consumer_uses_canonical_fastq_manifest_when_both_exist(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from services.molbio_ngs_evidence import _read_receipt_bound_result_manifest

    result_root = tmp_path / "result"
    canonical = result_root / "fastq_qc" / "qc_manifest.json"
    verification = result_root / "verification" / "qc_manifest.json"
    canonical.parent.mkdir(parents=True)
    verification.parent.mkdir(parents=True)
    canonical_bytes = b'{"authority":"canonical-fastq"}'
    canonical.write_bytes(canonical_bytes)
    verification.write_bytes(b'{"authority":"construct-verification"}')
    job = SimpleNamespace(params={"ont_workflow_id": "ont_fastq_qc"})

    manifest_path, raw = _read_receipt_bound_result_manifest(job, result_root)

    assert manifest_path == canonical.resolve()
    assert raw == canonical_bytes


def test_molbio_workup_consumer_uses_canonical_fastq_manifest_when_both_exist(
    tmp_path: Path,
) -> None:
    from routers.molbio_ops import _load_job_sequence_qc_manifest

    result_root = tmp_path / "result"
    canonical = result_root / "fastq_qc" / "qc_manifest.json"
    verification = result_root / "verification" / "qc_manifest.json"
    _write_manifest(
        canonical,
        {
            "artifact_schema_version": 1,
            "job_id": "job-receipt",
            "artifacts": [],
        },
    )
    verification.parent.mkdir(parents=True, exist_ok=True)
    verification.write_text('{"authority":"wrong-manifest"}', encoding="utf-8")
    job = SimpleNamespace(
        id="job-receipt",
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    )

    manifest = _load_job_sequence_qc_manifest(job, result_root)

    assert manifest["job_id"] == "job-receipt"
    assert manifest["workflow_id"] == "ont_fastq_qc"


def test_molbio_evidence_consumer_rejects_symlinked_canonical_parent(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from services.molbio_ngs_evidence import _read_receipt_bound_result_manifest

    result_root = tmp_path / "result"
    outside = tmp_path / "outside"
    result_root.mkdir()
    outside.mkdir()
    (outside / "qc_manifest.json").write_text('{"authority":"outside"}', encoding="utf-8")
    (result_root / "fastq_qc").symlink_to(outside, target_is_directory=True)
    job = SimpleNamespace(params={"ont_workflow_id": "ont_fastq_qc"})

    with pytest.raises(SequenceQcManifestError, match="symlink"):
        _read_receipt_bound_result_manifest(job, result_root)

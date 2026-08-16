from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast
from pathlib import Path

import pytest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _comparison_fixture(tmp_path: Path) -> tuple[SimpleNamespace, dict, SimpleNamespace, Path]:
    root = tmp_path / "job" / "comparison_panel"
    root.mkdir(parents=True)
    source = root / "comparison_panel_source.fastq"
    source.write_text("@same\nACGT\n+\n!!!!\n@same\nTGCA\n+\n####\n", encoding="utf-8")
    normalized = root / "comparison_panel_normalized.fastq"
    normalized.write_text(
        "@bms_occurrence_000000000001\nACGT\n+\n!!!!\n"
        "@bms_occurrence_000000000002\nTGCA\n+\n####\n",
        encoding="utf-8",
    )
    occurrence_map = root / "comparison_panel_occurrence_map.json"
    occurrence_map.write_text(
        json.dumps({
            "schema": "bms.ngs.comparison-panel-occurrence-map.v1",
            "source_fastq_sha256": _sha256(source),
            "normalized_fastq_sha256": _sha256(normalized),
            "input_read_count": 2,
            "occurrences": [
                {"occurrence_id": "bms_occurrence_000000000001", "read_id": "same", "ordinal": 1},
                {"occurrence_id": "bms_occurrence_000000000002", "read_id": "same", "ordinal": 2},
            ],
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    expected = root / "comparison_panel_expected_reference.fasta"
    expected.write_text(">expected_plasmid\nAAAA\n", encoding="utf-8")
    bam = root / "comparison_panel.bam"
    bai = root / "comparison_panel.bam.bai"
    bam.write_bytes(b"bam-evidence")
    bai.write_bytes(b"bai-evidence")
    panel_digest = "d" * 64
    summary_path = root / "comparison_panel_summary.json"
    summary = {
        "schema": "bms.ngs.comparison-attribution-summary.v1",
        "status": "review_required",
        "reference": {
            "id": "expected_plasmid", "role": "intended", "path": expected.name,
            "source_file_sha256": _sha256(expected), "sha256": _sha256(expected), "size_bytes": expected.stat().st_size,
        },
        "panel": {
            "schema": "bms.ngs.comparison-panel.v1", "snapshot_sha256": "e" * 64,
            "panel_id": "panel-1", "panel_version": 1, "panel_manifest_sha256": panel_digest,
            "entries": [{"id": "host-1", "role": "host", "label": "Host", "fasta_sha256": "f" * 64}],
        },
        "source_fastq": {"path": source.name, "sha256": _sha256(source), "size_bytes": source.stat().st_size},
        "source_fastq_sha256": _sha256(source),
        "normalized_fastq": {"path": normalized.name, "sha256": _sha256(normalized), "size_bytes": normalized.stat().st_size},
        "occurrence_map": {"path": occurrence_map.name, "sha256": _sha256(occurrence_map), "size_bytes": occurrence_map.stat().st_size},
        "occurrence_map_sha256": _sha256(occurrence_map),
        "input_read_count": 2,
        "classified_read_count": 2,
        "category_closure": ["expected_plasmid_unique", "panel_reference_unique", "ambiguous_multimapping", "unclassified"],
        "categories": {"expected_plasmid_unique": 1, "panel_reference_unique": 1, "ambiguous_multimapping": 0, "unclassified": 0},
        "role_counts": {"intended": 1, "host": 1, "plasmid_decoy": 0, "ambiguous": 0, "unclassified": 0},
        "reference_counts": {"expected_plasmid": 1, "host-1": 1},
        "reads": [
            {"read_id": "same", "ordinal": 1, "occurrence_id": "bms_occurrence_000000000001", "accepted_references": ["expected_plasmid"], "category": "expected_plasmid_unique", "role": "intended"},
            {"read_id": "same", "ordinal": 2, "occurrence_id": "bms_occurrence_000000000002", "accepted_references": ["host-1"], "category": "panel_reference_unique", "role": "host"},
        ],
        "artifacts": [
            {"kind": "comparison_panel_alignment_bam", "path": bam.name, "sha256": _sha256(bam), "size_bytes": bam.stat().st_size},
            {"kind": "comparison_panel_alignment_bai", "path": bai.name, "sha256": _sha256(bai), "size_bytes": bai.stat().st_size},
            {"kind": "comparison_panel_occurrence_map", "path": occurrence_map.name, "sha256": _sha256(occurrence_map), "size_bytes": occurrence_map.stat().st_size},
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    job = SimpleNamespace(
        id="job-1", status="completed", child_output_dir=str(root.parent), params={
            "molbio_revision_binding": {
                "sequence_id": "seq-1", "revision_id": "rev-1", "revision_sha256": "a" * 64,
                "reference_snapshot_sha256": _sha256(expected), "receipt_id": "receipt-1",
            },
            "comparison_panel_binding": {
                "panel_id": "panel-1", "panel_version": 1, "panel_snapshot_sha256": panel_digest,
                "receipt_id": "panel-receipt",
            },
        },
    )
    manifest = {
        "schema": "biomodstack.construct_verification.v2", "artifact_schema_version": 2, "verdict": "PASS",
        "artifacts": [{"kind": "reference", "sha256": _sha256(expected)}],
    }
    current = SimpleNamespace(id="rev-1", content_sha256="a" * 64)
    return job, manifest, current, summary_path


def test_revision_bound_workup_projects_manifest_verdict_without_promoting_job() -> None:
    from services.molbio_ngs_workup import project_ngs_workup

    job = SimpleNamespace(id="job-1", status="completed", params={
        "molbio_revision_binding": {"sequence_id": "seq-1", "revision_id": "rev-1", "revision_sha256": "a" * 64,
                                    "reference_snapshot_sha256": "c" * 64, "receipt_id": "receipt-1"},
    })
    manifest = {"schema": "biomodstack.construct_verification.v2", "artifact_schema_version": 2, "verdict": "PASS",
                "artifacts": [{"kind": "reference", "sha256": "c" * 64}]}
    current = SimpleNamespace(id="rev-2", content_sha256="b" * 64)
    result = project_ngs_workup(job, manifest, current)

    assert result["scientific_status"] == "PASS"
    assert result["revision_relation"] == "historical"
    assert result["job_status"] == "completed"
    assert "mutation" not in result


def test_missing_or_wrong_receipt_manifest_is_a_typed_review() -> None:
    from services.molbio_ngs_workup import project_ngs_workup

    job = SimpleNamespace(id="job-1", status="completed", params={"molbio_revision_binding": {
        "sequence_id": "seq-1", "revision_id": "rev-1", "revision_sha256": "a" * 64,
        "reference_snapshot_sha256": "c" * 64, "receipt_id": "receipt-1",
    }})
    current = SimpleNamespace(id="rev-1", content_sha256="a" * 64)
    result = project_ngs_workup(job, None, current)
    assert result["scientific_status"] == "REVIEW"
    assert result["projection_state"] == "REVIEW"
    assert result["manifest_available"] is False


def test_comparison_projection_requires_valid_digest_bound_artifacts() -> None:
    from services.molbio_ngs_workup import project_ngs_workup

    job = SimpleNamespace(id="job-1", status="completed", params={"molbio_revision_binding": {
        "sequence_id": "seq-1", "revision_id": "rev-1", "revision_sha256": "a" * 64,
        "reference_snapshot_sha256": "c" * 64, "receipt_id": "receipt-1",
    }, "comparison_panel_binding": {"panel_id": "panel-1", "panel_version": 1, "panel_snapshot_sha256": "d" * 64, "receipt_id": "panel-receipt"}})
    manifest = {"schema": "biomodstack.construct_verification.v2", "artifact_schema_version": 2, "verdict": "PASS", "artifacts": [
        {"kind": "reference", "sha256": "c" * 64}, {"kind": "comparison_panel_summary", "integrity_valid": False},
    ]}
    result = project_ngs_workup(job, manifest, SimpleNamespace(id="rev-1", content_sha256="a" * 64))
    assert result["scientific_status"] == "REVIEW"
    assert result["comparison_panel"] is None


def test_comparison_projection_rejects_source_artifact_tampering_and_accepts_duplicate_rows(tmp_path: Path) -> None:
    from services.molbio_ngs_workup import project_ngs_workup

    job, manifest, current, summary_path = _comparison_fixture(tmp_path)
    root = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result = project_ngs_workup(job, manifest, current, summary, comparison_panel_root=root, comparison_summary_path=summary_path)
    assert result["scientific_status"] == "PASS"
    assert result["comparison_panel"]["input_read_count"] == 2
    assert result["comparison_panel"]["role_counts"]["host"] == 1

    source = root / "comparison_panel_source.fastq"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    tampered = project_ngs_workup(job, manifest, current, summary, comparison_panel_root=root, comparison_summary_path=summary_path)
    assert tampered["scientific_status"] == "REVIEW"
    assert tampered["comparison_panel"] is None


def test_comparison_projection_rejects_row_tampering_and_declared_summary_digest_tampering(tmp_path: Path) -> None:
    from services.molbio_ngs_workup import project_ngs_workup

    job, manifest, current, summary_path = _comparison_fixture(tmp_path)
    root = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["reads"][1]["ordinal"] = 1
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = project_ngs_workup(job, manifest, current, summary, comparison_panel_root=root, comparison_summary_path=summary_path)
    assert result["scientific_status"] == "REVIEW"
    assert result["comparison_panel"] is None

    job, manifest, current, summary_path = _comparison_fixture(tmp_path / "digest")
    root = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    job.params["comparison_panel_summary_sha256"] = _sha256(summary_path)
    valid = project_ngs_workup(job, manifest, current, summary, comparison_panel_root=root, comparison_summary_path=summary_path)
    assert valid["comparison_panel"] is not None
    summary_path.write_text(summary_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    invalid = project_ngs_workup(job, manifest, current, summary, comparison_panel_root=root, comparison_summary_path=summary_path)
    assert invalid["scientific_status"] == "REVIEW"
    assert invalid["comparison_panel"] is None


@pytest.mark.asyncio
async def test_server_receipt_materializes_revision_snapshot_and_is_one_time(tmp_path, monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import Base
    from services import molbio_ngs_receipts

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(molbio_ngs_receipts, "get_inputs_dir", lambda: tmp_path / "inputs")
    revision = SimpleNamespace(id="rev-1", content_sha256=molbio_ngs_receipts.sha256_text("ACGT"), snapshot={
        "sequence": "ACGT", "sequence_type": "dna",
    })
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        receipt = await molbio_ngs_receipts.issue_molbio_ngs_receipt(session, sequence_id="seq-1", revision=revision)
        await session.commit()
        consumed = await molbio_ngs_receipts.consume_molbio_ngs_receipt(session, receipt_id=receipt.id)
        assert consumed.reference_snapshot_path.endswith("expected_reference.fasta")
        consumed.consumed_at = __import__("datetime").datetime.utcnow()
        await session.commit()
        with pytest.raises(ValueError, match="already used"):
            await molbio_ngs_receipts.consume_molbio_ngs_receipt(session, receipt_id=receipt.id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_approved_panel_is_frozen_from_immutable_revisions_and_receipted(tmp_path, monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import Base
    from services import ngs_comparison_panels

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(ngs_comparison_panels, "get_inputs_dir", lambda: tmp_path / "inputs")
    revisions = [
        SimpleNamespace(id="host-rev", revision_number=3, content_sha256=ngs_comparison_panels.sha256_text("ACGT"), snapshot={"sequence": "ACGT", "sequence_type": "dna"}),
        SimpleNamespace(id="decoy-rev", revision_number=4, content_sha256=ngs_comparison_panels.sha256_text("TGCA"), snapshot={"sequence": "TGCA", "sequence_type": "dna"}),
    ]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        panel = await ngs_comparison_panels.seed_approved_panel(
            session, label="ignored caller label", entries=[
                {"sequence_id": "host-seq", "revision": revisions[0], "role": "host", "sequence_name": "Host genome"},
                {"sequence_id": "decoy-seq", "revision": revisions[1], "role": "plasmid_decoy", "sequence_name": "Decoy plasmid"},
            ], actor="admin",
        )
        await session.commit()
        assert panel.status == "APPROVED"
        assert panel.label.startswith("Approved comparison panel")
        snapshot = tmp_path / "inputs" / "approved_ngs_comparison_panels" / panel.id / "v1" / "manifest.json"
        payload = __import__("json").loads(snapshot.read_text())
        assert {entry["role"] for entry in payload["entries"]} == {"host", "plasmid_decoy"}
        assert all("fasta_path" not in entry for entry in payload["entries"])
        receipt = await ngs_comparison_panels.issue_comparison_panel_receipt(session, panel_id=panel.id, expected_receipt_id="expected-1")
        assert receipt.panel_snapshot_sha256 == panel.snapshot_sha256
        expected = tmp_path / "inputs" / "expected.fasta"
        expected.write_text(">expected\nAAAA\n", encoding="utf-8")
        staged = ngs_comparison_panels.materialize_comparison_launch(
            expected_fasta=str(expected), expected_sha256=__import__("hashlib").sha256(expected.read_bytes()).hexdigest(), panel_receipt=receipt,
        )
        assert (Path(staged["input_root"]) / "expected_reference.fasta").read_text() == ">expected\nAAAA\n"
        assert Path(staged["comparison_panel_snapshot"]).is_file()
    await engine.dispose()


def test_panel_launch_is_limited_to_generic_qc_and_requires_both_receipts() -> None:
    from routers.ont_runs import _validate_comparison_panel_launch

    with pytest.raises(ValueError, match="only available"):
        _validate_comparison_panel_launch("wf_clone_validation", "expected", "panel")
    with pytest.raises(ValueError, match="both"):
        _validate_comparison_panel_launch("ont_fastq_qc", "", "panel")
    _validate_comparison_panel_launch("ont_fastq_qc", "expected", "")
    _validate_comparison_panel_launch("ont_fastq_qc", "expected", "panel")


def test_generic_submit_rejects_raw_panel_and_untrusted_molbio_sequence_id() -> None:
    from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit

    request = OntNgsSubmitRequest(params={"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fasta", "comparison_panel_snapshot": "/tmp/panel.json"})
    with pytest.raises(ValueError, match="comparison-panel"):
        _job_create_for_ont_submit("ont_fastq_qc", request)


@pytest.mark.asyncio
async def test_actual_submit_binds_only_server_consumed_receipt(monkeypatch) -> None:
    from fastapi import Response
    from routers import ont_runs

    receipt = SimpleNamespace(id="receipt-1", sequence_id="seq-1", revision_id="rev-1", revision_sha256="a" * 64,
                              reference_snapshot_path="/server/immutable.fasta", reference_snapshot_sha256="b" * 64,
                              consumed_at=None, consumed_job_id=None)
    seen = {}

    async def consume(_session, *, receipt_id):
        seen["receipt_id"] = receipt_id
        return receipt

    def build(_workflow, request):
        seen["submitted"] = request.params
        return SimpleNamespace(params={})

    async def create(_job, *_args):
        return SimpleNamespace(id="job-1")

    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    monkeypatch.setattr(ont_runs, "_job_create_for_ont_submit", build)
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", create)
    session = SimpleNamespace(commit=lambda: _async_none(), flush=lambda: _async_none())
    result = await ont_runs.ont_submit_ngs_workflow(
        "ont_fastq_qc", ont_runs.OntNgsSubmitRequest(params={"molbio_ngs_receipt_id": "receipt-1", "molbio_sequence_id": ""}),
        SimpleNamespace(), SimpleNamespace(), Response(), session,
    )
    assert result.id == "job-1"
    assert seen["receipt_id"] == "receipt-1"
    assert seen["submitted"]["reference_fasta"] == "/server/immutable.fasta"
    assert receipt.consumed_job_id == "job-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params, message",
    [
        ({"fastq_path": "/tmp/reads.fastq"}, "molbio_ngs_receipt_id"),
        (
            {
                "fastq_path": "/tmp/reads.fastq",
                "reference_fasta": "/tmp/mutable.fasta",
                "molbio_ngs_receipt_id": "receipt-1",
            },
            "reference_fasta is server-controlled",
        ),
    ],
)
async def test_public_reference_workflow_rejects_mutable_or_missing_authority(params, message) -> None:
    from fastapi import HTTPException, Response
    from routers import ont_runs

    with pytest.raises(HTTPException) as raised:
        await ont_runs.ont_submit_ngs_workflow(
            "ont_fastq_qc",
            ont_runs.OntNgsSubmitRequest(params=params),
            cast(Any, SimpleNamespace()),
            cast(Any, SimpleNamespace()),
            Response(),
            cast(Any, SimpleNamespace()),
        )
    assert raised.value.status_code == 422
    assert message in str(raised.value.detail)


async def _async_none() -> None:
    return None

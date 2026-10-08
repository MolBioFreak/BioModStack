from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest


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


async def _async_none() -> None:
    return None

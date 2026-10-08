"""Offline receipt contract tests; model execution is not exercised here."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from services.remote_stage_receipts import (
    RECEIPT_DIRECTORY, canonical_bytes, validate_remote_stage_receipts,
    write_remote_stage_receipt,
)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    attempt = str(uuid.uuid4())
    monkeypatch.setenv("BMS_REMOTE_EXECUTION", "1")
    monkeypatch.setenv("BMS_REMOTE_ATTEMPT_ID", attempt)
    monkeypatch.setenv("BMS_REMOTE_JOB_ID", "job-one")
    monkeypatch.setenv("BMS_REMOTE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("BMS_STAGE_REPORT_TOKEN", raising=False)
    return tmp_path, attempt


def manifest(root, attempt):
    return SimpleNamespace(job_id="job-one", attempt_id=attempt, exit_code=0, artifacts=[
        SimpleNamespace(relative_path=p.relative_to(root).as_posix(),
                        size_bytes=p.stat().st_size,
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest(), link_target=None)
        for p in root.rglob("*") if p.is_file()
    ])


def write(stage="protenix", status="complete", outputs=None):
    return write_remote_stage_receipt(job_id="job-one", stage=stage, status=status,
        outputs=outputs or [], job_root_relative=True)


def test_remote_receipts_canonical_immutable_and_manifest_bound(remote):
    root, attempt = remote
    (root / "structure.cif").write_text("fixture-only")
    write(status="start")
    receipt = write(outputs=["structure.cif"])
    assert receipt.read_bytes() == canonical_bytes(json.loads(receipt.read_bytes()))
    assert write(outputs=["structure.cif"]) == receipt
    with pytest.raises(ValueError, match="immutable"):
        write(status="failed")
    result = validate_remote_stage_receipts(output_root=root, job_id="job-one",
                                            attempt_id=attempt, manifest=manifest(root, attempt))
    assert {r["status"] for r in result} == {"start", "complete"}
    bound = manifest(root, attempt)
    (root / "structure.cif").write_text("tampered")
    with pytest.raises(ValueError, match="size|digest"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=bound)


@pytest.mark.parametrize("output", ["../outside", "/tmp/absolute", "a//b", "a/./b", "a\\b", ".bms-stage-receipts/a.json"])
def test_outputs_fail_closed(remote, output):
    with pytest.raises(ValueError):
        write(outputs=[output])


def test_receipt_job_attempt_closed_schema_and_missing_integrity(remote):
    root, attempt = remote
    receipt = write(status="not_requested")
    for key, value in [("job_id", "other"), ("attempt_id", str(uuid.uuid4())), ("unknown", True)]:
        original = receipt.read_bytes()
        payload = json.loads(original)
        payload[key] = value
        receipt.chmod(0o600)
        receipt.write_bytes(canonical_bytes(payload))
        with pytest.raises(ValueError):
            validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=manifest(root, attempt))
        receipt.write_bytes(original)
    bound = manifest(root, attempt)
    bound.artifacts.clear()
    with pytest.raises(ValueError, match="set"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=bound)


def test_symlink_and_unfinished_stage_rejected(remote, tmp_path):
    root, attempt = remote
    (root / "alias").symlink_to(root.parent)
    with pytest.raises(ValueError):
        write(outputs=["alias/outside"])
    (root / "alias").unlink()
    write(status="start")
    with pytest.raises(ValueError, match="no terminal"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=manifest(root, attempt))


def test_remote_reporter_does_not_call_http_or_require_credentials(remote, monkeypatch):
    reporter_path = Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("remote_test_reporter", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    def forbidden(*args, **kwargs):
        pytest.fail("remote stage reporter attempted HTTP")
    monkeypatch.setattr(reporter.requests, "post", forbidden)
    monkeypatch.setattr("sys.argv", [str(reporter_path), "job-one", "frustrampnn", "not_requested"])
    reporter.main()
    assert (remote[0] / RECEIPT_DIRECTORY / "frustrampnn.terminal.json").exists()


def test_worker_manifest_includes_stage_receipts(remote):
    root, attempt = remote
    write(status="not_requested")
    worker_path = Path(__file__).resolve().parents[1] / "tools" / "bms_remote_worker.py"
    spec = importlib.util.spec_from_file_location("receipt_test_worker", worker_path)
    assert spec is not None and spec.loader is not None
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    attempt_dir = root / "attempt-metadata"
    attempt_dir.mkdir()
    (attempt_dir / "execution-envelope.json").write_text("{}")
    result = worker.build_result_manifest(attempt_dir, dict(output_directory=str(root),
        attempt_id=attempt, job_id="job-one", source_revision="a" * 40, source_tree="b" * 40), 0)
    record = next(a for a in result["artifacts"] if a["relative_path"].startswith(RECEIPT_DIRECTORY + "/"))
    assert record["sha256"] == hashlib.sha256((root / record["relative_path"]).read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_apply_uses_exact_session_and_preserves_scientific_ingestion(remote, monkeypatch):
    from unittest.mock import AsyncMock
    import sqlalchemy
    from services.remote_stage_receipts import apply_remote_stage_receipts
    from services.result_ingester import _ingest_explicit_frustrampnn_results

    root, attempt = remote
    write(stage="frustrampnn", status="not_requested")
    job = SimpleNamespace(id="job-one", remote_attempt_id=attempt,
        execution_target_id="target", remote_state="returning", status="awaiting_input",
        nextflow_run_id=f"remote:{attempt}", output_dir=str(root), child_output_dir=None,
        model_id="protenix", completed_stages=[], stage_outputs={}, provenance={}, current_stage=None)
    # Explicitly mocked attachment/flush; this is metadata unit coverage, not DB proof.
    session = SimpleNamespace(sync_session=object(), flush=AsyncMock())
    monkeypatch.setattr(sqlalchemy, "inspect", lambda obj: SimpleNamespace(session=session.sync_session))
    args = dict(session=session, job=job, attempt_id=attempt, output_root=root, manifest=manifest(root, attempt))
    assert await apply_remote_stage_receipts(**args) == 1
    assert job.completed_stages == []
    assert job.stage_outputs == {"frustrampnn": []}
    assert await _ingest_explicit_frustrampnn_results(job, root, session, commit=False) is None
    assert job.status == "awaiting_input"
    assert "stage_report_token_sha256" not in job.provenance
    job.remote_state = "results_available"
    with pytest.raises(ValueError, match="authority"):
        await apply_remote_stage_receipts(**args)
    job.remote_state = "returning"
    job.remote_attempt_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="authority"):
        await apply_remote_stage_receipts(**args)


def test_local_reporter_http_contract_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("BMS_REMOTE_EXECUTION", raising=False)
    monkeypatch.setenv("API_BASE_URL", "http://local.invalid")
    monkeypatch.setenv("BMS_STAGE_REPORT_TOKEN", "unit-fixture-token")
    reporter_path = Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("local_test_reporter", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(reporter.requests, "post", post)
    monkeypatch.setattr("sys.argv", [str(reporter_path), "--job-root-relative", "local-job", "protenix", "complete", "final/model.cif"])
    reporter.main()
    assert calls == [("http://local.invalid/api/jobs/local-job/stage-complete", {
        "params": {"stage": "protenix"}, "json": ["final/model.cif"],
        "headers": {"Authorization": "Bearer unit-fixture-token"}, "timeout": 10})]

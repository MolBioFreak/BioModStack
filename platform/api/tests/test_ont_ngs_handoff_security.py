from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "platform" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import routers.ont_runs as ont_runs


@pytest.fixture(autouse=True)
def _unit_contract_paths_are_prevalidated(monkeypatch):
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))

    async def valid_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-safe"
        return SimpleNamespace(
            reference_snapshot_path="/trusted/reference.fasta",
            consumed_at=None,
            consumed_job_id=None,
        )

    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", valid_receipt)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": []})


class _Session:
    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _handoff_payload(params: dict | None = None) -> dict:
    return {"molbio_ngs_receipt_id": "receipt-safe", "params": params or {}}


def test_handoff_accepts_only_explicit_safe_tuning_fields(monkeypatch):
    trusted = {"pod5_dir": "/trusted/pod5", "output_dir": "/trusted/out", "reference_fasta": "/trusted/ref.fa", "source_instrument_run_id": "run-trusted"}
    async def build_handoff(run_id, payload):
        return {"params": trusted}
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", build_handoff)
    captured = {}
    created = SimpleNamespace(id="job-safe")
    async def fake_create(job, background_tasks, session, response, request):
        captured["job"] = job
        return created
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create)
    result = asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-trusted", _handoff_payload({"igv_report_max_sites": 7}), BackgroundTasks(), _request(), Response(), session=_Session()))
    params = captured["job"].params
    assert result is created
    assert params["pod5_dir"] == "/trusted/pod5"
    assert params["output_dir"] == "/trusted/out"
    assert params["reference_fasta"] == "/trusted/ref.fa"
    assert params["source_instrument_run_id"] == "run-trusted"
    assert params["igv_report_max_sites"] == 7


@pytest.mark.parametrize(
    "params",
    [
        {"threads": 7},
        {"caller_selected_path": "/caller/chosen/output"},
        {"bam_path": "/caller/chosen/reads.bam"},
        {"igv_report_max_sites": "/caller/chosen/path"},
    ],
)
def test_handoff_rejects_unknown_or_path_valued_params_before_handoff_or_job_creation(monkeypatch, params):
    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe params must not reach handoff")),
    )
    monkeypatch.setattr(
        ont_runs,
        "_create_pipeline_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe params must not create a job")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ont_runs.ont_submit_plasmid_qc_from_run(
                "run-trusted", _handoff_payload(params), BackgroundTasks(), _request(), Response(), session=_Session()
            )
        )

    assert exc_info.value.status_code == 422
    assert "/caller/chosen" not in str(exc_info.value.detail)


def test_handoff_validation_error_is_http_422(monkeypatch):
    async def reject(run_id, payload):
        raise ValueError("invalid instrument handoff")
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", reject)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-invalid", _handoff_payload(), BackgroundTasks(), _request(), Response(), session=_Session()))
    assert exc_info.value.status_code == 422
    assert "invalid instrument handoff" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "reserved_key",
    sorted(ont_runs.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ont_runs.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS),
)
def test_public_handoff_rejects_every_reserved_runtime_and_provenance_key(monkeypatch, reserved_key):
    trusted = {
        "pod5_dir": "/trusted/pod5",
        "output_dir": "/trusted/out",
        "reference_fasta": "/trusted/ref.fa",
        "source_instrument_run_id": "run-trusted",
    }
    async def build_handoff(_run_id, _payload):
        return {"params": trusted}
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", build_handoff)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ont_runs.ont_submit_plasmid_qc_from_run(
                "run-trusted",
                _handoff_payload({reserved_key: "/caller/value"}),
                BackgroundTasks(),
                _request(),
                Response(),
                session=_Session(),
            )
        )

    assert exc_info.value.status_code == 422
    assert "unsupported fields" in str(exc_info.value.detail)
    assert "/caller/value" not in str(exc_info.value.detail)

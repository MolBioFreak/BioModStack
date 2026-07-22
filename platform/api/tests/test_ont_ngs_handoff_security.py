from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": []})


def test_handoff_extras_cannot_add_or_override_controlled_fields(monkeypatch):
    trusted = {"pod5_dir": "/trusted/pod5", "output_dir": "/trusted/out", "reference_fasta": "/trusted/ref.fa", "source_instrument_run_id": "run-trusted"}
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", lambda run_id, payload: {"params": trusted})
    captured = {}
    async def fake_create(job, background_tasks, session, response, request):
        captured["job"] = job
        return job
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create)
    result = asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-trusted", {"params": {"pod5_dir": "/bad/pod5", "reference_fasta": "/bad/ref.fa", "source_instrument_run_id": "run-bad", "bam_path": "/bad/alternate.bam", "threads": 7}}, BackgroundTasks(), _request(), Response(), session=None))
    params = captured["job"].params
    assert result is captured["job"]
    assert params["pod5_dir"] == "/trusted/pod5"
    assert params["output_dir"] == "/trusted/out"
    assert params["reference_fasta"] == "/trusted/ref.fa"
    assert params["source_instrument_run_id"] == "run-trusted"
    assert "bam_path" not in params
    assert params["threads"] == 7


def test_handoff_validation_error_is_http_422(monkeypatch):
    def reject(run_id, payload):
        raise ValueError("invalid instrument handoff")
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", reject)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-invalid", {}, BackgroundTasks(), _request(), Response(), session=None))
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
    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda _run_id, _payload: {"params": trusted},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ont_runs.ont_submit_plasmid_qc_from_run(
                "run-trusted",
                {"params": {reserved_key: "/caller/value"}},
                BackgroundTasks(),
                _request(),
                Response(),
                session=None,
            )
        )

    assert exc_info.value.status_code == 422
    assert reserved_key in str(exc_info.value.detail)

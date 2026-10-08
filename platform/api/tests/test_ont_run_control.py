from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import ont_runs  # noqa: E402
from services import ont_run_control  # noqa: E402
from services.ont_run_control import (  # noqa: E402
    build_start_preflight,
    reset_ont_run_store,
    start_instrument_run,
    stop_instrument_run,
    refresh_instrument_run_status,
)


def test_start_preflight_blocks_missing_flowcell() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": False,
            "flow_cell": {"present": False},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        output_directories={"reads": "/data/minknow"},
    )

    assert payload["can_start"] is False
    assert "flowcell_absent" in payload["blockers"]
    assert payload["position"] == "X1"
    assert payload["fake_or_demo_devices"] is False


def test_start_preflight_blocks_running_position_and_missing_output_directory() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": True,
            "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=False,
        output_directories={},
    )

    assert payload["can_start"] is False
    assert payload["blockers"] == ["position_already_running", "output_directory_missing"]


def test_start_preflight_allows_ready_position_with_requested_kit_and_output_dir() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": False,
            "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        basecalling_options={"simplex_models": ["dna_r10.4.1_e8.2_400bps_sup"]},
        output_directories={"reads": "/data/minknow"},
    )

    assert payload["can_start"] is True
    assert payload["blockers"] == []
    assert payload["protocol_id"] is None
    assert payload["basecalling_options"]["simplex_models"] == ["dna_r10.4.1_e8.2_400bps_sup"]


def test_protocol_options_endpoint_uses_host_agent_payload(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    client = TestClient(app)

    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda position, kit=None, basecalling_enabled=True: {
            "position": position,
            "can_start": True,
            "blockers": [],
            "protocol_id": "sequencing/sequencing_MIN114_DNA_e8_2_400K",
            "basecalling_options": {"simplex_models": ["sup"]},
            "output_directories": {"reads": "/data/minknow"},
            "fake_or_demo_devices": False,
        },
    )

    response = client.get("/api/ont/positions/X1/protocol-options?kit=SQK-LSK114")

    assert response.status_code == 200
    payload = response.json()
    assert payload["position"] == "X1"
    assert payload["can_start"] is True
    assert payload["fake_or_demo_devices"] is False


def test_start_instrument_run_requires_explicit_confirmation() -> None:
    try:
        start_instrument_run("X1", {"kit": "SQK-LSK114", "confirm_start": False})
    except ValueError as exc:
        assert "confirm_start" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("start should require explicit confirmation")


def test_start_instrument_run_records_bms_and_minknow_ids(monkeypatch) -> None:
    reset_ont_run_store()
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda method, path, payload=None, *, query=None: {
            "minknow_run_id": "MNK-RUN-001",
            "status": "running",
            "position": "X1",
            "output_directories": {"reads": "/data/minknow/run1"},
        },
    )

    run = start_instrument_run(
        "X1",
        {
            "sample_id": "plasmid_A12",
            "experiment_group": "bms_plasmid_verification",
            "kit": "SQK-LSK114",
            "confirm_start": True,
        },
    )

    assert run["id"].startswith("ont-run-")
    assert run["minknow_run_id"] == "MNK-RUN-001"
    assert run["position"] == "X1"
    assert run["status"] == "running"
    assert run["sample_id"] == "plasmid_A12"
    assert run["fake_or_demo_devices"] is False


def test_stop_instrument_run_requires_confirmation_and_uses_recorded_minknow_id(monkeypatch) -> None:
    reset_ont_run_store()
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        if path.endswith("/start"):
            return {"minknow_run_id": "MNK-RUN-002", "status": "running", "position": "X1"}
        return {"status": "stopped"}

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)
    run = start_instrument_run("X1", {"kit": "SQK-LSK114", "confirm_start": True})

    try:
        stop_instrument_run(run["id"], {"confirm_stop": False})
    except ValueError as exc:
        assert "confirm_stop" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("stop should require explicit confirmation")

    stopped = stop_instrument_run(run["id"], {"confirm_stop": True})

    assert stopped["status"] == "stopped"
    assert calls[-1] == ("POST", "/ont/runs/MNK-RUN-002/stop", {"confirm_stop": True})


def test_refresh_instrument_run_status_updates_real_output_readiness(monkeypatch, tmp_path: Path) -> None:
    reset_ont_run_store()
    fastq = tmp_path / "reads.fastq.gz"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n")

    def fake_request(method, path, payload=None, *, query=None):
        if path.endswith("/start"):
            return {"minknow_run_id": "MNK-RUN-003", "status": "running", "position": "X1"}
        return {
            "status": "completed",
            "output_files": {"fastq": [str(fastq)], "pod5": [], "bam": []},
        }

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)
    run = start_instrument_run("X1", {"kit": "SQK-LSK114", "confirm_start": True})

    refreshed = refresh_instrument_run_status(run["id"])

    assert refreshed["status"] == "completed"
    assert refreshed["handoff_ready"] is True
    assert refreshed["output_files"]["fastq"] == [str(fastq)]


def test_refresh_instrument_run_status_does_not_mark_missing_outputs_ready(monkeypatch) -> None:
    reset_ont_run_store()

    def fake_request(method, path, payload=None, *, query=None):
        if path.endswith("/start"):
            return {"minknow_run_id": "MNK-RUN-004", "status": "running", "position": "X1"}
        return {
            "status": "completed",
            "output_files": {"fastq": ["/missing/run.fastq.gz"], "pod5": [], "bam": []},
        }

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)
    run = start_instrument_run("X1", {"kit": "SQK-LSK114", "confirm_start": True})

    refreshed = refresh_instrument_run_status(run["id"])

    assert refreshed["status"] == "completed"
    assert refreshed["handoff_ready"] is False
    assert refreshed["output_files"]["fastq"] == []

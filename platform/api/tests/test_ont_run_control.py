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
    build_plasmid_qc_handoff,
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



def test_start_preflight_exposes_flowcell_truth_and_output_directories_for_config_cell() -> None:
    payload = build_start_preflight(
        position={
            "position": "MD-105428",
            "running": True,
            "flow_cell": {
                "present": False,
                "is_ctc": False,
                "channel_count": 0,
                "sample_rate": 10000,
            },
            "output_directories": {"reads": "/var/lib/minknow/data/"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        output_directories={"reads": "/var/lib/minknow/data/"},
    )

    assert payload["can_start"] is False
    assert payload["flow_cell"] == {
        "present": False,
        "is_ctc": False,
        "channel_count": 0,
        "sample_rate": 10000,
    }
    assert payload["output_directories"] == {"reads": "/var/lib/minknow/data/"}
    assert "flowcell_absent" in payload["blockers"]
    assert "position_already_running" in payload["blockers"]
    assert "output_directory_missing" not in payload["blockers"]


def test_refresh_position_state_proxies_host_agent_without_claiming_restart(monkeypatch) -> None:
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda method, path, payload=None, *, query=None: {
            "action": "refresh",
            "detail": "Reopened the MinKNOW position connection and reread device/flow-cell state; this does not power-cycle the instrument.",
            "position": {"position": "MD-105428"},
            "fake_or_demo_devices": False,
        },
    )

    payload = ont_run_control.refresh_position_state("MD-105428")

    assert payload["action"] == "refresh"
    assert "does not power-cycle" in payload["detail"]
    assert payload["position"]["position"] == "MD-105428"


def test_restart_position_requires_confirmation_but_remains_host_agent_contract(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        return {
            "detail": "BMS does not yet perform a MinKNOW/Mk1D instrument restart",
            "position": "MD-105428",
            "fake_or_demo_devices": False,
        }

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)

    try:
        ont_run_control.restart_position("MD-105428", {"confirm_restart": False})
    except ValueError as exc:
        assert "confirm_restart" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restart should require explicit confirmation")

    payload = ont_run_control.restart_position("MD-105428", {"confirm_restart": True})

    assert "does not yet perform" in payload["detail"]
    assert calls == [("POST", "/ont/positions/MD-105428/restart", {"confirm_restart": True})]

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


def test_build_plasmid_qc_handoff_requires_ready_outputs_and_reference(monkeypatch, tmp_path: Path) -> None:
    reset_ont_run_store()
    fastq = tmp_path / "reads.fastq.gz"
    ref = tmp_path / "reference.fasta"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n")
    ref.write_text(">plasmid\nACGT\n")

    def fake_request(method, path, payload=None, *, query=None):
        if path.endswith("/start"):
            return {"minknow_run_id": "MNK-RUN-005", "status": "running", "position": "X1"}
        return {"status": "completed", "output_files": {"fastq": [str(fastq)], "pod5": [], "bam": []}}

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)
    run = start_instrument_run("X1", {"kit": "SQK-LSK114", "confirm_start": True})
    refresh_instrument_run_status(run["id"])

    handoff = build_plasmid_qc_handoff(run["id"], {"reference_fasta": str(ref)})

    assert handoff["model_id"] == "nanopore"
    assert handoff["mode"] == "plasmid_qc"
    assert handoff["params"]["ont_workflow_id"] == "ont_plasmid_qc"
    assert handoff["params"]["fastq_path"] == str(fastq)
    assert handoff["params"]["reference_fasta"] == str(ref)
    assert handoff["params"]["source_instrument_run_id"] == run["id"]


def test_build_plasmid_qc_handoff_blocks_missing_reference() -> None:
    reset_ont_run_store()
    try:
        build_plasmid_qc_handoff("missing", {"reference_fasta": "/missing/reference.fasta"})
    except KeyError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("unknown run should not hand off")



def test_begin_position_hardware_check_requires_confirmation_and_proxies_host_agent(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        return {
            "action": "begin_hardware_check",
            "position": "MD-105428",
            "hardware_check_run_id": "HC-001",
            "fake_or_demo_devices": False,
        }

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)

    try:
        ont_run_control.begin_position_hardware_check("MD-105428", {"confirm_hardware_check": False})
    except ValueError as exc:
        assert "confirm_hardware_check" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("hardware check should require explicit confirmation")

    payload = ont_run_control.begin_position_hardware_check("MD-105428", {"confirm_hardware_check": True})

    assert payload["action"] == "begin_hardware_check"
    assert calls == [("POST", "/ont/positions/MD-105428/hardware-check", {"confirm_hardware_check": True})]



def test_hardware_check_endpoint_maps_host_agent_error_without_500(monkeypatch) -> None:
    from services.host_agent_client import HostAgentRequestError  # noqa: PLC0415

    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    client = TestClient(app)

    monkeypatch.setattr(
        ont_run_control,
        "begin_position_hardware_check",
        lambda position, payload: (_ for _ in ()).throw(
            HostAgentRequestError(
                status_code=409,
                detail={"detail": "MinKNOW refused hardware check: no flow cell/test cell is currently reported as present on this position."},
                url="http://127.0.0.1:8798/ont/positions/MD-105428/hardware-check",
            )
        ),
    )

    response = client.post("/api/ont/positions/MD-105428/hardware-check", json={"confirm_hardware_check": True})

    assert response.status_code == 409
    assert "no flow cell/test cell" in response.text

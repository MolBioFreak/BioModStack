from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import ont_devices  # noqa: E402
from services.ont_device_control import (  # noqa: E402
    DEVICE_CONTROL_STATUS_NOT_CONFIGURED,
    ONT_DEVICE_CONTROL_CAPABILITIES,
    build_analysis_handoff,
    get_device_control_status,
)
from services.ont_ngs_contract import DEVICE_CONTROL_OWNER  # noqa: E402


def test_ont_device_control_contract_supports_mk1b_mk1d_without_fake_devices() -> None:
    status = get_device_control_status()

    assert status["owner"] == DEVICE_CONTROL_OWNER
    assert status["implementation_status"] == DEVICE_CONTROL_STATUS_NOT_CONFIGURED
    assert status["live_devices"] == []
    assert {device["id"] for device in status["supported_device_types"]} == {"mk1b", "mk1d"}
    assert status["fake_or_demo_devices"] is False


def test_ont_device_control_capabilities_are_hardware_side_not_nextflow_side() -> None:
    assert ONT_DEVICE_CONTROL_CAPABILITIES["owner"] == "bms_service_api"
    assert ONT_DEVICE_CONTROL_CAPABILITIES["not_owned_by"] == "nextflow_analysis"
    assert ONT_DEVICE_CONTROL_CAPABILITIES["controls"] == [
        "discover_devices",
        "inspect_flowcell",
        "configure_run",
        "start_run",
        "stop_run",
        "monitor_run",
        "handoff_outputs_to_analysis",
    ]


def test_analysis_handoff_requires_existing_run_outputs_not_live_device_handle(tmp_path: Path) -> None:
    run_dir = tmp_path / "ont_run_001"
    run_dir.mkdir()

    handoff = build_analysis_handoff(
        workflow_id="ont_methylation_analysis",
        run_output_dir=run_dir,
        primary_input_kind="pod5",
    )

    assert handoff["workflow_id"] == "ont_methylation_analysis"
    assert handoff["analysis_owner"] == "nextflow_analysis"
    assert handoff["device_control_owner"] == "bms_service_api"
    assert handoff["requires_live_device"] is False
    assert handoff["primary_input_kind"] == "pod5"
    assert handoff["run_output_dir"] == str(run_dir)


def test_ont_device_router_exposes_truthful_not_configured_status() -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = TestClient(app)

    response = client.get("/api/ont/devices/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["implementation_status"] == "not_configured"
    assert payload["live_devices"] == []
    assert payload["fake_or_demo_devices"] is False
    assert payload["owner"] == "bms_service_api"

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
from services.ont_run_control import build_start_preflight  # noqa: E402


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

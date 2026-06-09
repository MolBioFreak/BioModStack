from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"

for path in (API_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import host_agent_client, ont_device_control  # noqa: E402


def test_host_agent_client_exposes_ont_status_and_positions(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, payload=None, *, query=None):
        calls.append((method, path))
        return {"path": path, "fake_or_demo_devices": False}

    monkeypatch.setattr(host_agent_client, "request_host_agent", fake_request)

    assert host_agent_client.get_ont_status()["path"] == "/ont/status"
    assert host_agent_client.get_ont_positions()["path"] == "/ont/positions"
    assert host_agent_client.get_ont_position("X1")["path"] == "/ont/positions/X1"
    assert calls == [("GET", "/ont/status"), ("GET", "/ont/positions"), ("GET", "/ont/positions/X1")]


def test_device_status_delegates_to_host_agent_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(
        ont_device_control,
        "get_ont_status",
        lambda: {
            "implementation_status": "configured",
            "minknow": {"host": "localhost", "manager_port": 9502},
            "live_devices": [{"position": "X1", "device_type": "mk1d"}],
            "fake_or_demo_devices": False,
        },
    )
    monkeypatch.setattr(
        ont_device_control,
        "discover_minknow_devices",
        lambda: (_ for _ in ()).throw(AssertionError("API container direct MinKNOW fallback should not run when host-agent is configured")),
    )

    payload = ont_device_control.get_device_control_status()

    assert payload["owner"] == "bms_service_api"
    assert payload["analysis_owner"] == "nextflow_analysis"
    assert payload["implementation_status"] == "configured"
    assert payload["live_devices"] == [{"position": "X1", "device_type": "mk1d"}]
    assert payload["fake_or_demo_devices"] is False


def test_device_status_reports_host_agent_unavailable_without_fake_devices(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(
        ont_device_control,
        "get_ont_status",
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    payload = ont_device_control.get_device_control_status()

    assert payload["implementation_status"] == "host_agent_unavailable"
    assert payload["live_devices"] == []
    assert payload["fake_or_demo_devices"] is False
    assert "connection refused" in payload["message"]


def test_host_agent_ont_routes_return_discovery_payload(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_status",
        lambda: {
            "implementation_status": "configured",
            "live_devices": [{"position": "X1"}, {"position": "X2"}],
            "fake_or_demo_devices": False,
        },
    )

    assert bms_host_agent.ont_route_payload("/ont/status") == {
        "implementation_status": "configured",
        "live_devices": [{"position": "X1"}, {"position": "X2"}],
        "fake_or_demo_devices": False,
    }
    assert bms_host_agent.ont_route_payload("/ont/positions") == {
        "positions": [{"position": "X1"}, {"position": "X2"}],
        "implementation_status": "configured",
        "fake_or_demo_devices": False,
    }
    assert bms_host_agent.ont_route_payload("/ont/positions/X2") == {
        "position": {"position": "X2"},
        "implementation_status": "configured",
        "fake_or_demo_devices": False,
    }
    assert bms_host_agent.ont_route_payload("/ont/positions/missing") is None


def test_host_agent_protocol_options_route_returns_truthful_preflight(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_status",
        lambda: {
            "implementation_status": "configured",
            "live_devices": [
                {
                    "position": "X1",
                    "running": False,
                    "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
                }
            ],
            "fake_or_demo_devices": False,
        },
    )
    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_protocol_options",
        lambda position, kit=None, basecalling_enabled=True, status=None: {
            "position": position,
            "kit": kit,
            "can_start": True,
            "blockers": [],
            "basecalling_enabled": basecalling_enabled,
            "fake_or_demo_devices": False,
        },
    )

    payload = bms_host_agent.ont_route_payload("/ont/positions/X1/protocol-options?kit=SQK-LSK114")

    assert payload == {
        "position": "X1",
        "kit": "SQK-LSK114",
        "can_start": True,
        "blockers": [],
        "basecalling_enabled": True,
        "fake_or_demo_devices": False,
    }

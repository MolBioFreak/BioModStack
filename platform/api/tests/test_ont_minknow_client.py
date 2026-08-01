from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_minknow_client import (  # noqa: E402
    MinknowConnectionConfig,
    discover_minknow_devices,
    infer_ont_device_type,
    normalize_position,
)


class FakeDevice:
    def __init__(self, flow_cell_info):
        self._flow_cell_info = flow_cell_info

    def get_flow_cell_info(self):
        return self._flow_cell_info


class FakePosition:
    def __init__(self, *, name="X1", state="STATE_RUNNING", running=False, flow_cell_info=None, description=None):
        self.name = name
        self.state = state
        self.running = running
        self.description = description or SimpleNamespace(rpc_ports=SimpleNamespace(secure=9503))
        self._flow_cell_info = flow_cell_info or SimpleNamespace(
            has_flow_cell=True,
            flow_cell_id="FAK12345",
            user_specified_flow_cell_id="",
            product_code="MIN-106D",
            user_specified_product_code="Mk1D",
            sample_rate=5000,
        )

    def connect(self):
        return SimpleNamespace(device=FakeDevice(self._flow_cell_info))


class FakeManager:
    def __init__(self, positions):
        self._positions = positions

    def flow_cell_positions(self):
        return self._positions


def test_infer_ont_device_type_only_classifies_explicit_mk_tokens() -> None:
    assert infer_ont_device_type(position_name="X1", product_code="Mk1D") == "mk1d"
    assert infer_ont_device_type(position_name="X1", product_code="MinION Mk1B") == "mk1b"
    assert infer_ont_device_type(position_name="X1", product_code="MIN-106D") is None


def test_normalize_position_emits_only_browser_safe_mk1d_discovery_fields() -> None:
    normalized = normalize_position(FakePosition())

    assert normalized == {
        "position": "X1",
        "device_type": "mk1d",
        "state": "STATE_RUNNING",
        "running": False,
        "available_for_run": True,
        "flow_cell": {"present": True},
    }
    rendered = str(normalized)
    for secret in ("FAK12345", "MIN-106D", "Mk1D", "9503", "rpc_ports", "connection_error"):
        assert secret not in rendered


def test_discover_minknow_devices_returns_configured_status_from_manager_positions() -> None:
    config = MinknowConnectionConfig(host="127.0.0.1", port=9502)

    payload = discover_minknow_devices(
        config=config,
        manager_factory=lambda _config: FakeManager([FakePosition(name="X1")]),
    )

    assert payload["implementation_status"] == "configured"
    assert "minknow" not in payload
    assert payload["fake_or_demo_devices"] is False
    assert len(payload["live_devices"]) == 1
    assert payload["live_devices"][0]["position"] == "X1"
    assert payload["live_devices"][0]["available_for_run"] is True


def test_discover_minknow_devices_reports_unreachable_without_fake_devices() -> None:
    def broken_factory(_config):
        raise RuntimeError("connection refused")

    payload = discover_minknow_devices(
        config=MinknowConnectionConfig(host="localhost", port=9502),
        manager_factory=broken_factory,
    )

    assert payload["implementation_status"] == "unreachable"
    assert payload["live_devices"] == []
    assert payload["fake_or_demo_devices"] is False
    assert payload["message"] == "MinKNOW discovery is unavailable."
    assert "localhost" not in str(payload)
    assert "connection refused" not in str(payload)

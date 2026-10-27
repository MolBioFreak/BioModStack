"""Offline native API-shape and lost-reply acquisition regressions."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from lib import ont_minknow_host as host
from lib.ont_start_receipts import read_start_receipt

REQUEST_ID = "ont-run-" + "a" * 32


@pytest.fixture
def fake_instrument(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []
    connection = NS(
        device=NS(get_flow_cell_info=lambda: NS(has_flow_cell=True, product_code="FLO-MIN114")),
        protocol=NS(get_current_protocol_run=lambda: None, list_protocols=lambda: NS(protocols=[])),
    )
    monkeypatch.setattr(host, "_connect_position", lambda *a, **kw: (NS(), connection))
    def start(*a, **kw):
        calls.append(kw)
        return "minknow-run-one"
    tools = NS(start_protocol=start, OutputArgs=lambda **kw: kw)
    monkeypatch.setattr(host, "_protocols_module", lambda: tools)
    payload = {"confirm_start": True, "start_request_id": REQUEST_ID,
               "protocol_id": "protocol-one", "run_hours": 2, "basecalling_enabled": False}
    return calls, tools, payload


def test_native_find_protocol_returns_string_not_message(monkeypatch, fake_instrument):
    _calls, tools, _payload = fake_instrument
    tools.find_protocol = lambda *a, **kw: "protocol-one"
    status = {"live_devices": [{"position": "X1", "device_type": "mk1d", "running": False,
                               "flow_cell": {"present": True}, "output_directories": {"reads": "/data"}}]}
    result = host.protocol_options("X1", kit="SQK-LSK114", basecalling_enabled=False, status=status)
    assert result["protocol_id"] == "protocol-one"
    assert result["can_start"] is True


def test_success_receipt_survives_lost_reply_and_prevents_second_physical_start(fake_instrument):
    calls, _tools, payload = fake_instrument
    code, response = host.start_protocol("X1", payload)
    assert code == 201
    assert read_start_receipt("X1", REQUEST_ID) == response
    assert host.start_protocol("X1", dict(payload)) == (code, response)
    assert len(calls) == 1
    assert host.start_protocol("X1", {**payload, "run_hours": 3})[0] == 409
    assert read_start_receipt("another-position", REQUEST_ID).get("minknow_run_id") is None


def test_minknow_transport_failure_is_unknown_and_never_retried(fake_instrument):
    calls, tools, payload = fake_instrument
    def lost_reply(*a, **kw):
        calls.append(kw)
        raise TimeoutError("RPC reply lost after sending start")
    tools.start_protocol = lost_reply
    code, response = host.start_protocol("X1", payload)
    assert code == 502 and response["start_outcome"] == "unknown"
    assert host.start_protocol("X1", payload) == (code, response)
    assert read_start_receipt("X1", REQUEST_ID)["start_outcome"] == "unknown"
    assert len(calls) == 1


def test_missing_request_identity_never_calls_minknow(fake_instrument):
    calls, _tools, payload = fake_instrument
    del payload["start_request_id"]
    assert host.start_protocol("X1", payload)[0] == 400
    assert calls == []


def test_host_receipt_route_is_read_only_and_does_not_discover_devices(monkeypatch, fake_instrument):
    import bms_host_agent
    _calls, _tools, payload = fake_instrument
    _, response = host.start_protocol("X1", payload)
    monkeypatch.setattr(bms_host_agent, "discover_ont_status", lambda: pytest.fail("receipt read must not use instrument"))
    assert bms_host_agent.ont_route_payload(f"/ont/positions/X1/start-requests/{REQUEST_ID}") == response

"""Closed consumer contracts for the robot's compact, read-only projections."""
import copy
import json
from pathlib import Path

import pytest
from test_bioxp_operator_controls import catalog, make_client

FIXTURES = Path(__file__).parent / "fixtures"


def history():
    payload = json.loads((FIXTURES / "bioxp_retained_durable_history_v1.json").read_text())
    for row in payload["receipts"]:
        row.pop("__projection_source", None)
    return payload


# Exact four-field producer in Serial206OemInitializationProvider's passive
# X projection. It does not claim a controller query, position, or readiness.
PASSIVE_X = {
    "ok": False,
    "available": False,
    "authority": "passive_projection",
    "failure": "explicit_terminal_readback_required",
}


@pytest.mark.parametrize("suffix,route,path", [
    ("catalog", "operator_control_catalog", ("dashboard",)),
    ("dashboard", "operator_dashboard", ()),
    ("v2/catalog", "operator_control_catalog_v2", ("dashboard", "telemetry")),
    ("v2/dashboard", "operator_dashboard_v2", ("telemetry",)),
])
def test_all_four_cockpit_routes_relay_passive_x_without_making_queries(
    monkeypatch, suffix, route, path,
):
    client, runtime = make_client(monkeypatch, mutations=False)
    raw = runtime.connection.client.responses[route]
    telemetry = raw
    for key in path:
        if key == "telemetry":
            telemetry[key] = copy.deepcopy(catalog()["dashboard"])
        telemetry = telemetry[key]
    if "x_axis" not in telemetry:
        telemetry["x_axis"] = copy.deepcopy(catalog()["dashboard"]["x_axis"])
    provider = telemetry["x_axis"]["provider"]
    provider["live_status"] = dict(PASSIVE_X)
    provider["profile"]["verified"] = False
    provider["switch_masks"] = {
        "observed": None, "policy": "observed_only_oem_source_omits_x_writes",
    }
    with client:
        response = client.get("/api/bioxp/operator-controls/" + suffix)
    assert response.status_code == 200, response.text
    relayed = response.json()
    for key in path:
        relayed = relayed[key]
    assert relayed["x_axis"]["provider"]["live_status"] == PASSIVE_X
    assert relayed["x_axis"]["provider"]["profile"]["verified"] is False
    assert relayed["x_axis"]["physical_position_verified"] is False
    assert [call[0] for call in runtime.connection.client.calls] == (
        [route, "operator_control_catalog_v2"] if suffix == "catalog" else [route]
    )
    assert not runtime.connection.oem_action_calls
    assert not runtime.connection.safety_interrupt_calls


def compact_history():
    raw = history()
    for row in raw["receipts"]:
        if row.get("source") == "legacy_operator_plane":
            row["transport_exchanges"] = []
    return raw


def test_legacy_history_route_relays_empty_exchange_marker(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    raw = compact_history()
    runtime.connection.client.responses["operator_action_history"] = copy.deepcopy(raw)
    with client:
        response = client.get("/api/bioxp/operator-controls/history")
    assert response.status_code == 200, response.text
    relayed = response.json()
    assert relayed["schema_version"] == raw["schema_version"]
    assert [r["command_id"] for r in relayed["receipts"]] == [r["command_id"] for r in raw["receipts"]]
    # Existing direct variants add their declared optional defaults on HTTP
    # serialization; these two legacy projections must remain exactly intact.
    assert relayed["receipts"][23:] == raw["receipts"][23:]
    assert [call[0] for call in runtime.connection.client.calls] == ["operator_action_history"]
    assert not runtime.connection.oem_action_calls

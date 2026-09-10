"""Closed consumer contracts for the robot's compact, read-only projections."""
import copy

import pytest
from pydantic import TypeAdapter, ValidationError

from services.bioxp.operator_models import (
    OperatorActionHistory,
    OperatorDashboardXLiveStatus,
    OperatorLegacyDurableCommandReceipt,
    OperatorLiveActionReceipt,
)
from test_bioxp_durable_history_compat import history
from test_bioxp_operator_controls import catalog, make_client


# Exact four-field producer in Serial206OemInitializationProvider's passive
# X projection. It does not claim a controller query, position, or readiness.
PASSIVE_X = {
    "ok": False,
    "available": False,
    "authority": "passive_projection",
    "failure": "explicit_terminal_readback_required",
}


def test_passive_x_round_trips_without_terminal_authority():
    parsed = OperatorDashboardXLiveStatus.model_validate(PASSIVE_X)
    assert parsed.model_dump(mode="json", exclude_unset=True) == PASSIVE_X
    assert parsed.readbacks == {}


@pytest.mark.parametrize("field,bad", [
    ("ok", True), ("ok", 0), ("ok", "false"),
    ("available", True), ("available", 0), ("available", "false"),
    ("authority", "serial206_x_terminal_register_readback"),
    ("failure", "invented_failure"),
    ("position_steps", 123), ("profile_verified", True),
    ("readbacks", {}), ("unknown_authority", False),
])
def test_passive_x_rejects_coercion_unknown_fields_and_invented_authority(field, bad):
    with pytest.raises(ValidationError):
        OperatorDashboardXLiveStatus.model_validate({**PASSIVE_X, field: bad})


@pytest.mark.parametrize("field", list(PASSIVE_X))
def test_passive_x_requires_its_complete_marker(field):
    raw = dict(PASSIVE_X)
    del raw[field]
    with pytest.raises(ValidationError):
        OperatorDashboardXLiveStatus.model_validate(raw)


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
    assert [call[0] for call in runtime.connection.client.calls] == [route]
    assert not runtime.connection.oem_action_calls
    assert not runtime.connection.safety_interrupt_calls


def compact_history():
    raw = history()
    for row in raw["receipts"]:
        if row.get("source") == "legacy_operator_plane":
            row["transport_exchanges"] = []
    return raw


def test_compact_legacy_history_preserves_every_record_and_terminal_evidence():
    raw = compact_history()
    parsed = OperatorActionHistory.model_validate(raw)
    assert parsed.model_dump(mode="json", by_alias=True, exclude_unset=True) == raw
    assert len(parsed.receipts) == len(raw["receipts"])


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


def test_old_legacy_receipt_does_not_gain_a_fabricated_exchange_marker():
    row = history()["receipts"][23]
    assert "transport_exchanges" not in row
    parsed = OperatorLegacyDurableCommandReceipt.model_validate(row)
    assert "transport_exchanges" not in parsed.model_dump(mode="json")


@pytest.mark.parametrize("bad", [None, {}, "[]", False, (), [None], [{"acknowledged": True}]])
def test_legacy_compact_exchange_marker_accepts_only_an_empty_array(bad):
    row = history()["receipts"][23]
    row["transport_exchanges"] = bad
    with pytest.raises(ValidationError):
        OperatorLegacyDurableCommandReceipt.model_validate(row)


@pytest.mark.parametrize("index", [23, 24])
def test_compact_legacy_receipt_still_cannot_be_a_live_mutation_receipt(index):
    row = compact_history()["receipts"][index]
    with pytest.raises(ValidationError):
        TypeAdapter(OperatorLiveActionReceipt).validate_python(row)

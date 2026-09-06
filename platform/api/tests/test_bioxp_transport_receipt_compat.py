"""F06 robot transport evidence must survive the strict BMS read relay."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorActionReceiptV2, OperatorDashboardV2, OperatorActionReceipt
from test_bioxp_operator_controls import receipt as legacy_receipt
from test_serial206_bioxp_v2_models import compact_payload, dashboard_payload
from test_bioxp_operator_controls import make_client


def exchange_payload():
    # Source contract: robot novo_router._publish_attempt + ExchangeObserver.append.
    return {
        "exchange_id": "transaction-a:1", "transaction_id": "transaction-a",
        "attempt_ordinal": 1, "response_attempt_attribution": "single_write",
        "owner_generation": 1, "matcher": "tmcl:4:6", "registration_timestamp": 1.0,
        "tx_timestamp": 1.1, "tx_write_completed_at": 1.2, "timeout_ms": 1000,
        "tx_raw": [1, 4, 6], "command_family": "tmcl", "tx_id": 4, "tx_dlc": 8,
        "expected_board": 4, "expected_command": 6, "write_attempted": True,
        "write_returned": True, "wait_signaled": True, "response_present": True,
        "observed_status": 100, "observed_rx_raw": [1, 100, 6],
        "observed_rx_id": 4, "observed_rx_dlc": 8, "receive_timestamp": 1.3,
        "receive_sequence": 2, "outcome": "response", "router_outcome": "response",
        "exception": None, "finalized_at": 1.4, "physical_effect_verified": False,
        "command_id": "cmd-1", "trace_id": "trace-a", "transport_call_ordinal": 1,
        "durable_ownership_claimed": False,
    }


def evidence():
    return {"transport_exchanges": [exchange_payload()], "transport_retention_errors": [
        {"stage": "sink", "class": "OSError", "message": "retention unavailable"}
    ]}


@pytest.mark.parametrize("populated", [False, True])
def test_v1_transport_evidence_survives_receipt_parsing(populated):
    payload = legacy_receipt()
    payload.update(evidence() if populated else {
        "transport_exchanges": [], "transport_retention_errors": []})
    result = OperatorActionReceipt.model_validate(payload).model_dump(mode="json")
    for key in ("transport_exchanges", "transport_retention_errors"):
        assert result[key] == payload[key]


def test_old_v1_receipts_keep_absent_transport_evidence_absent():
    result = OperatorActionReceipt.model_validate(legacy_receipt()).model_dump(mode="json")
    assert "transport_exchanges" not in result
    assert "transport_retention_errors" not in result


@pytest.mark.parametrize("key", ["transport_exchanges", "transport_retention_errors"])
def test_v1_rejects_explicit_null_transport_evidence(key):
    payload = legacy_receipt()
    payload[key] = None
    with pytest.raises(ValidationError):
        OperatorActionReceipt.model_validate(payload)


@pytest.mark.parametrize("populated", [False, True])
def test_live_shaped_dashboard_retains_transport_fields(populated):
    payload = dashboard_payload()
    extension = evidence() if populated else {"transport_exchanges": [], "transport_retention_errors": []}
    payload["latest_receipts"] = [compact_payload(**extension)]
    parsed = OperatorDashboardV2.model_validate(payload).model_dump(mode="json")
    assert parsed["latest_receipts"][0] == payload["latest_receipts"][0]
    assert parsed["latest_receipts"][0]["physical_effect_verified"] is False


def test_legacy_receipt_omitting_transport_extension_still_parses():
    parsed = OperatorActionReceiptV2.model_validate(compact_payload())
    assert parsed.transport_exchanges == []
    assert parsed.transport_retention_errors == []


@pytest.mark.parametrize("field,value", [
    ("transport_exchanges", {}), ("transport_retention_errors", ["error"]),
])
def test_transport_container_shape_stays_strict(field, value):
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(**{field: value}))


@pytest.mark.parametrize("field,value", [
    ("write_returned", "true"), ("physical_effect_verified", True),
    ("durable_ownership_claimed", True), ("unexpected", 1),
    ("tx_raw", [256]), ("finalized_at", float("nan")),
])
def test_transport_evidence_rejects_malformed_or_promoted_truth(field, value):
    extension = evidence()
    extension["transport_exchanges"][0][field] = value
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(**extension))


def test_transport_timeout_preserves_unknown_receive_fields():
    extension = evidence()
    exchange = extension["transport_exchanges"][0]
    exchange.update(wait_signaled=False, response_present=False, observed_status=None,
                    observed_rx_raw=None, observed_rx_id=None, observed_rx_dlc=None,
                    receive_timestamp=None, receive_sequence=None, outcome="timeout",
                    router_outcome="timeout")
    parsed = OperatorActionReceiptV2.model_validate(compact_payload(**extension)).model_dump(mode="json")
    assert parsed["transport_exchanges"] == extension["transport_exchanges"]


def test_unrelated_receipt_fields_stay_forbidden():
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(**evidence(), unsupported=True))


def test_real_dashboard_relay_preserves_evidence_without_dispatch(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = dashboard_payload()
    # The live robot omits this optional projection; exercise the existing adapter too.
    del payload["command_queue"]
    payload["latest_receipts"] = [compact_payload(**evidence())]
    original = deepcopy(payload)
    runtime.connection.client.responses["operator_dashboard_v2"] = payload
    response = client.get("/api/bioxp/operator-controls/v2/dashboard")
    assert response.status_code == 200, response.text
    assert response.json()["latest_receipts"] == original["latest_receipts"]
    assert payload == original
    assert [call[0] for call in runtime.connection.client.calls] == ["operator_dashboard_v2"]

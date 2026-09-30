"""Read-only canonical lookup/history compatibility; all transport is inert."""
import copy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routers.bioxp.operator_controls import _validate_live_action_receipt
from services.bioxp.operator_models import OperatorCanonicalCommandReceiptV1
from test_bioxp_operator_controls import history_page, make_client, v2_receipt


FLAGS = (
    "source_call_completed", "source_return_ok", "controller_stop_acknowledged",
    "controller_terminal_state_verified",
)


def canonical_receipt(status="completed"):
    # Unit fixture for CommandReceiptReader's public lookup envelope, not a
    # captured receipt or controller/physical acceptance claim.
    return {
        "schema_version": "bioxp.operator_command_receipt.v1",
        "robot_identity": "serial206", "command_id": "canonical-lookup-1",
        "method_id": None, "method_sequence": None, "stream_sequence": 7,
        "action_id": "oem.deck.move_to_location", "status": status,
        "ownership_generation": 1,
        "requested_inputs": {"target": "LOC_PARK", "camera_offset": False},
        "effective_inputs": {"target": "LOC_PARK", "camera_offset": False},
        "accepted_at": 1.25, "queued_at": 1.25, "dispatched_at": None,
        "finished_at": None if status == "queued" else 2.5,
        "source_noop": True, "source_noop_reason": "already_at_park",
        "remote_acknowledged": False, "controller_acknowledged": False,
        "physical_effect_verified": False,
        "terminal_evidence": {"delivery_attempted": False, "source_noop": True},
        "sequence": 7, "state_version": 2, "expected_board_epoch_by_board": {},
        "terminal_receipt_id": None, "completion_class": None,
        "transition_sequence": 3, "response": None, "stage_receipts": [],
    }


@pytest.mark.parametrize("status", ["queued", "completed", "failed", "ambiguous", "cleared"])
def test_canonical_lookup_preserves_numeric_times_and_unverified_truth(monkeypatch, status):
    client, runtime = make_client(monkeypatch, mutations=False)
    payload = canonical_receipt(status)
    original = copy.deepcopy(payload)
    runtime.connection.client.responses["operator_action_receipt"] = payload
    response = client.get("/api/bioxp/operator-controls/receipts/canonical-lookup-1")
    assert response.status_code == 200, response.text
    assert response.json() == original
    assert payload == original
    assert runtime.connection.active_request_calls[-1]["require_fresh"] is False
    assert len(runtime.connection.client.calls) == 1
    assert runtime.connection.safety_interrupt_calls == []
    assert runtime.connection.oem_action_calls == []


@pytest.mark.parametrize("field,value", [
    ("physical_effect_verified", "false"), ("controller_acknowledged", 1),
    ("finished_at", "2.5"), ("status", "not_a_status"),
    ("schema_version", "bioxp.operator_action_receipt.v1"),
    ("unpublished_extra", "not_in_contract"),
])
def test_canonical_lookup_keeps_its_actual_typed_contract(monkeypatch, field, value):
    client, runtime = make_client(monkeypatch, mutations=False)
    payload = canonical_receipt()
    payload[field] = value
    runtime.connection.client.responses["operator_action_receipt"] = payload
    response = client.get("/api/bioxp/operator-controls/receipts/canonical-lookup-1")
    assert response.status_code == 502
    assert len(runtime.connection.client.calls) == 1


def test_canonical_lookup_does_not_accept_another_command_identity(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses["operator_action_receipt"] = canonical_receipt()
    response = client.get("/api/bioxp/operator-controls/receipts/other-command")
    assert response.status_code == 502
    assert response.json()["detail"] == "BioXP robot returned a mismatched command receipt"


def test_canonical_lookup_variant_does_not_widen_live_invocation():
    payload = canonical_receipt()
    assert OperatorCanonicalCommandReceiptV1.model_validate(payload).physical_effect_verified is False
    with pytest.raises(HTTPException) as error:
        _validate_live_action_receipt(payload)
    assert error.value.status_code == 502
    with pytest.raises(ValidationError):
        OperatorCanonicalCommandReceiptV1.model_validate({**payload, "controller_acknowledged": "false"})


@pytest.mark.parametrize("supplied", [None, False, True])
def test_history_normalizes_only_missing_interrupt_flags_without_promoting_refusal(monkeypatch, supplied):
    client, runtime = make_client(monkeypatch, mutations=False)
    item = v2_receipt(action_id="oem.x.stop", command_id="refused-stop-1")
    item.update(status="rejected", terminal=True, finished_at=2.0)
    item["interrupt_evidence"] = {
        "details": {"rejection": "interrupt_request_schema_required"},
        "physical_effect_verified": False, "persistence_state": "committed",
    }
    if supplied is not None:
        item["interrupt_evidence"]["source_call_completed"] = supplied
    item["history"] = history_page()["items"][0]["history"]
    item["history"].update(recorded_status="rejected", machine_assessment="fail")
    payload = history_page(items=[item])
    original = copy.deepcopy(payload)
    runtime.connection.client.responses["operator_action_history"] = payload
    response = client.get("/api/bioxp/operator-controls/history?limit=100")
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["status"] == "rejected"
    assert row["physical_effect_verified"] is False
    assert row["interrupt_evidence"]["source_call_completed"] is supplied
    assert all(row["interrupt_evidence"][flag] is None for flag in FLAGS[1:])
    assert row["interrupt_evidence"]["physical_effect_verified"] is False
    assert payload == original
    assert len(runtime.connection.client.calls) == 1
    assert not runtime.connection.safety_interrupt_calls

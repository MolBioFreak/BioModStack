"""Read-only canonical lookup/history compatibility; all transport is inert."""
import copy

import pytest

from test_bioxp_operator_controls import make_client


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

import math

import pytest
from pydantic import ValidationError

from services.bioxp import operator_models
from services.bioxp.operator_models import (
    OperatorActionReceiptV2,
    OperatorActionRequestV2,
    OperatorControlCatalogV2,
    OperatorDashboardV2,
    OperatorInterruptRequestV1,
    OperatorMethodRequestV1,
    OperatorMethodV1,
)
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

OperatorInterruptReceiptV1 = getattr(operator_models, "OperatorInterruptReceiptV1", None)


def compact_payload(**overrides):
    payload = {
        "schema_version": "bioxp.operator_action_receipt.v2",
        "command_id": "cmd-1",
        "action_id": "oem.y.move_steps",
        "status": "queued",
        "terminal": False,
        "sequence": 1,
        "method_id": None,
        "ownership_generation": 1,
        "expected_board_epoch_by_board": {"4": 2},
        "state_version": 1,
        "status_path": "/operator/v2/actions/receipts/cmd-1",
        "accepted_at": 1.0,
        "queued_at": 1.0,
        "dispatched_at": None,
        "finished_at": None,
        "terminal_receipt_id": None,
        "completion_class": None,
        "physical_effect_verified": False,
        "error": None,
    }
    payload.update(overrides)
    return payload


def y_axis_payload():
    return {
        "axis": "y",
        "board_id": 4,
        "motor_id": 0,
        "ownership_generation": 1,
        "prior_board_epoch": 1,
        "active_board_epoch": 2,
        "prepared_board_epoch": 2,
        "lifecycle_state": "referenced_ready",
        "reference_state": "referenced",
        "position_steps": 0,
        "position_reply_valid": True,
        "position_status_code": 100,
        "speed_steps_s": 0,
        "speed_reply_valid": True,
        "speed_status_code": 100,
        "left_switch_raw": 1,
        "left_switch_reply_valid": True,
        "left_switch_status_code": 100,
        "home_effective": True,
        "profile_fingerprint": "a" * 64,
        "profile_readback_valid": True,
        "profile_mismatches": [],
        "active_command": None,
        "interrupt_epoch": 0,
        "latest_compact_receipt": None,
        "last_discrepancy_steps": None,
        "state_version": 2,
        "updated_at": 1.0,
        "physical_position_verified": False,
    }


def dashboard_payload(*, board_state="active"):
    return {
        "schema_version": "bioxp.operator_dashboard.v2",
        "generated_at": 1.0,
        "ownership_generation": 1,
        "board4": {
            "state": board_state,
            "prior_board_epoch": 1,
            "active_board_epoch": 2,
            "transition_phase": "committed" if board_state == "active" else "unknown",
            "transition_evidence": {},
            "member_motors": {"y": 0, "z": 1, "gripper": 2},
            "state_version": 2,
            "updated_at": 1.0,
        },
        "y_axis": y_axis_payload(),
        "active_commands": [],
        "command_queue": {
            "schema_version": "bioxp.oem_command_queue.v1",
            "generated_at": 1.0,
            "items": [],
        },
        "latest_receipts": [],
    }


def test_v2_receipt_derives_terminality_and_rejects_mismatch():
    receipt = OperatorActionReceiptV2.model_validate(compact_payload())
    assert receipt.terminal is False
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(status="completed", terminal=False))


def test_v2_dashboard_requires_strict_y_axis_and_catalog_embeds_it():
    dashboard = OperatorDashboardV2.model_validate(dashboard_payload())
    assert dashboard.y_axis.board_id == 4
    catalog = OperatorControlCatalogV2.model_validate({
        "schema_version": "bioxp.operator_control_catalog.v2",
        "dashboard": dashboard.model_dump(),
        "actions": [{"action_id": "oem.y.move_steps", "request_schema_version": "bioxp.operator_action_request.v2", "response_schema_version": "bioxp.operator_action_receipt.v2", "interrupt": False, "enabled": True, "disabled_reason": None}],
    })
    assert catalog.actions[0].action_id == "oem.y.move_steps"


def test_v2_catalog_accepts_exact_y_stop_pair_and_unknown_board_authority():
    payload = dashboard_payload(board_state="unknown")
    payload["board4"]["prior_board_epoch"] = None
    payload["board4"]["active_board_epoch"] = None
    payload["y_axis"]["prior_board_epoch"] = None
    payload["y_axis"]["active_board_epoch"] = None
    payload["y_axis"]["prepared_board_epoch"] = None
    catalog = OperatorControlCatalogV2.model_validate({
        "schema_version": "bioxp.operator_control_catalog.v2",
        "dashboard": payload,
        "actions": [{
            "action_id": "oem.y.stop",
            "request_schema_version": "bioxp.operator_interrupt_request.v1",
            "response_schema_version": "bioxp.operator_interrupt_receipt.v1",
            "interrupt": True,
            "enabled": True,
            "disabled_reason": None,
        }],
    })
    assert catalog.dashboard.board4.state == "unknown"
    assert catalog.actions[0].interrupt is True


@pytest.mark.parametrize(
    ("interrupt", "request_schema", "response_schema"),
    [
        (True, "bioxp.operator_action_request.v2", "bioxp.operator_interrupt_receipt.v1"),
        (True, "bioxp.operator_interrupt_request.v1", "bioxp.operator_action_receipt.v2"),
        (False, "bioxp.operator_interrupt_request.v1", "bioxp.operator_action_receipt.v2"),
    ],
)
def test_v2_catalog_rejects_mismatched_interrupt_schema_pairs(interrupt, request_schema, response_schema):
    with pytest.raises(ValidationError):
        OperatorControlCatalogV2.model_validate({
            "schema_version": "bioxp.operator_control_catalog.v2",
            "dashboard": dashboard_payload(),
            "actions": [{
                "action_id": "oem.y.stop" if interrupt else "oem.y.move_steps",
                "request_schema_version": request_schema,
                "response_schema_version": response_schema,
                "interrupt": interrupt,
                "enabled": True,
                "disabled_reason": None,
            }],
        })


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["y_axis"].__setitem__("ownership_generation", 2),
    lambda payload: payload["y_axis"].__setitem__("active_board_epoch", 3),
    lambda payload: payload["y_axis"].__setitem__("prior_board_epoch", 0),
])
def test_v2_dashboard_rejects_cross_field_authority_mismatch(mutate):
    payload = dashboard_payload()
    mutate(payload)
    with pytest.raises(ValidationError):
        OperatorDashboardV2.model_validate(payload)


@pytest.mark.parametrize("model,payload,field", [
    (OperatorActionRequestV2, {
        "expected_connection_generation": 1,
        "schema_version": "bioxp.operator_action_request.v2",
        "idempotency_key": "move-1",
        "expected_ownership_generation": 1,
        "expected_board_epoch_by_board": {"04": 2},
        "inputs": {"steps": 1},
    }, "expected_board_epoch_by_board"),
    (OperatorInterruptRequestV1, {
        "expected_connection_generation": 1,
        "schema_version": "bioxp.operator_interrupt_request.v1",
        "reason": "stop",
        "observed_ownership_generation": 1,
        "observed_board_epoch_by_board": {"+4": 2},
    }, "observed_board_epoch_by_board"),
    (OperatorMethodRequestV1, {
        "expected_connection_generation": 1,
        "schema_version": "bioxp.operator_method_request.v1",
        "idempotency_key": "method-1",
        "method_action_id": "oem.xy.home",
        "expected_ownership_generation": 1,
        "expected_board_epoch_by_board": {" 4": 2},
        "inputs": {},
    }, "expected_board_epoch_by_board"),
])
def test_v2_requests_reject_noncanonical_board_epoch_keys(model, payload, field):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    assert field in str(caught.value)


@pytest.mark.parametrize("steps", [-102_937, 102_937])
def test_y_move_steps_rejects_values_outside_exact_robot_envelope(steps):
    with pytest.raises(ValidationError):
        OperatorActionRequestV2.model_validate({
            "expected_connection_generation": 1,
            "schema_version": "bioxp.operator_action_request.v2",
            "idempotency_key": "move-steps",
            "expected_ownership_generation": 1,
            "expected_board_epoch_by_board": {"4": 2},
            "inputs": {"steps": steps},
        })


@pytest.mark.parametrize("status", [
    "pause_requested", "paused", "cancel_requested", "stopping", "aborting",
])
def test_method_receipt_accepts_robot_transient_statuses(status):
    parsed = OperatorMethodV1.model_validate({
        "schema_version": "bioxp.operator_method.v1",
        "method_id": "method-1",
        "action_id": "oem.xy.home",
        "status": status,
        "state_version": 2,
        "child_receipts": [],
        "accepted_at": 1.0,
        "finished_at": None,
    })
    assert parsed.status == status


def interrupt_receipt_payload(**overrides):
    payload = {
        "schema_version": "bioxp.operator_interrupt_receipt.v1",
        "robot_identity": "serial206",
        "ownership_generation": 7,
        "observed_ownership_generation": 7,
        "observed_board_epoch_by_board": {"4": 5},
        "interrupt_attempt_id": "interrupt-attempt-12345678",
        "interrupt_id": "interrupt-1",
        "action_id": "oem.y.stop",
        "scope": "y",
        "cutoff": 4,
        "active_command_id": "cmd-1",
        "active_command_ids": ["cmd-1"],
        "global_safety_epoch": 1,
        "x_safety_epoch": 1,
        "y_safety_epoch": 2,
        "z_safety_epoch": 1,
        "oem_abort_latched": False,
        "controller_stop_attempted": True,
        "controller_stop_acknowledged": True,
        "controller_response": {"ok": True},
        "error": None,
        "physical_effect_verified": False,
        "persistence_state": "committed",
        "recovery_hold": False,
        "transition_sequence": 9,
        "terminal_transition_sequences": [10],
    }
    payload.update(overrides)
    return payload


def test_interrupt_receipt_closes_identity_controller_persistence_and_recovery_semantics():
    assert OperatorInterruptReceiptV1 is not None
    committed = OperatorInterruptReceiptV1.model_validate(interrupt_receipt_payload())
    assert committed.action_id == "oem.y.stop"
    fallback_payload = interrupt_receipt_payload(
        interrupt_id="attempt-2",
        interrupt_attempt_id="attempt-2",
        active_command_id=None,
        active_command_ids=[],
        controller_stop_acknowledged=False,
        controller_response=None,
        error="controller_interrupt_exception:TimeoutError",
        persistence_state="fsync_fallback",
        recovery_hold=True,
        persistence_fallback={
            "kind": "serial206_interrupt_jsonl",
            "reason": "sqlite_lock_timeout_after_y_stop_delivery",
            "recorded_at": 2.0,
        },
        cutoff=None,
        global_safety_epoch=None,
        x_safety_epoch=None,
        y_safety_epoch=None,
        z_safety_epoch=None,
        transition_sequence=None,
        terminal_transition_sequences=[],
    )
    assert OperatorInterruptReceiptV1.model_validate(fallback_payload).recovery_hold is True
    for invalid in [
        interrupt_receipt_payload(action_id="oem.x.stop"),
        interrupt_receipt_payload(controller_stop_attempted=False),
        interrupt_receipt_payload(physical_effect_verified=True),
        interrupt_receipt_payload(persistence_state="committed", recovery_hold=True),
        interrupt_receipt_payload(controller_stop_acknowledged=False, error=None),
    ]:
        with pytest.raises(ValidationError):
            OperatorInterruptReceiptV1.model_validate(invalid)


def test_v2_receipt_rejects_nonfinite_time_and_extra_fields():
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(accepted_at=math.nan))
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(extra="nope"))


def test_robot_client_has_only_generation_bound_v2_operator_seams():
    assert DEFAULT_ROBOT_ROUTES["operator_control_catalog_v2"] == ("GET", "/operator/v2/control-catalog", 5.0)
    assert DEFAULT_ROBOT_ROUTES["operator_dashboard_v2"] == ("GET", "/operator/v2/dashboard", 5.0)
    assert DEFAULT_ROBOT_ROUTES["invoke_operator_action_v2"][1] == "/operator/v2/actions/{action_id}"
    assert DEFAULT_ROBOT_ROUTES["operator_action_receipt_v2"][1] == "/operator/v2/actions/receipts/{command_id}"
    assert DEFAULT_ROBOT_ROUTES["interrupt_operator_action_v1"] == ("POST", "/operator/v2/actions/{action_id}", 10.0)
    assert DEFAULT_ROBOT_ROUTES["submit_operator_method_v1"] == ("POST", "/operator/v2/methods", 5.0)

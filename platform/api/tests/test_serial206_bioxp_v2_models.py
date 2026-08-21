import math

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import (
    OperatorActionReceiptV2,
    OperatorDashboardV2,
    OperatorControlCatalogV2,
)
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES


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
        "status_path": "/operator/actions/receipts/cmd-1",
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


def test_v2_receipt_derives_terminality_and_rejects_mismatch():
    receipt = OperatorActionReceiptV2.model_validate(compact_payload())
    assert receipt.terminal is False
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(status="completed", terminal=False))


def test_v2_dashboard_requires_strict_y_axis_and_catalog_embeds_it():
    dashboard = OperatorDashboardV2.model_validate({
        "schema_version": "bioxp.operator_dashboard.v2",
        "generated_at": 1.0,
        "ownership_generation": 1,
        "board4": {"state": "active", "prior_board_epoch": 1, "active_board_epoch": 2, "transition_phase": "committed", "transition_evidence": {}, "member_motors": {"y": 0}, "state_version": 2, "updated_at": 1.0},
        "y_axis": y_axis_payload(),
        "active_commands": [],
        "command_queue": {"schema_version": "bioxp.oem_command_queue.v1", "generated_at": 1.0, "items": []},
        "latest_receipts": [],
    })
    assert dashboard.y_axis.board_id == 4
    catalog = OperatorControlCatalogV2.model_validate({
        "schema_version": "bioxp.operator_control_catalog.v2",
        "dashboard": dashboard.model_dump(),
        "actions": [{"action_id": "oem.y.move_steps", "request_schema_version": "bioxp.operator_action_request.v2", "response_schema_version": "bioxp.operator_action_receipt.v2", "interrupt": False, "enabled": True, "disabled_reason": None}],
    })
    assert catalog.actions[0].action_id == "oem.y.move_steps"


def test_v2_receipt_rejects_nonfinite_time_and_extra_fields():
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(accepted_at=math.nan))
    with pytest.raises(ValidationError):
        OperatorActionReceiptV2.model_validate(compact_payload(extra="nope"))


def test_robot_client_has_only_generation_bound_v2_operator_seams():
    assert DEFAULT_ROBOT_ROUTES["operator_control_catalog_v2"] == ("GET", "/operator/v2/control-catalog", 10.0)
    assert DEFAULT_ROBOT_ROUTES["operator_dashboard_v2"] == ("GET", "/operator/v2/dashboard", 10.0)
    assert DEFAULT_ROBOT_ROUTES["invoke_operator_action_v2"][1] == "/operator/v2/actions/{action_id}"
    assert DEFAULT_ROBOT_ROUTES["operator_action_receipt_v2"][1] == "/operator/v2/actions/receipts/{command_id}"

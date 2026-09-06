import math

import pytest
from pydantic import TypeAdapter, ValidationError

from services.bioxp import operator_models
from services.bioxp.operator_models import (
    OperatorActionReceiptV2,
    OperatorActionRequestV2,
    OperatorControlCatalogV2,
    OperatorDashboard,
    OperatorDashboardV2,
    OperatorInterruptRequestV1,
    OperatorMethodRequestV1,
    OperatorMethodV1,
)
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

OperatorInterruptReceiptV1 = getattr(operator_models, "OperatorInterruptReceiptV1", None)
OperatorDeckMoveInputsV1 = getattr(operator_models, "OperatorDeckMoveInputsV1", None)


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


@pytest.mark.parametrize("status", ["stopped", "aborted", "cancelled"])
def test_v2_receipt_accepts_truthful_additional_terminal_lifecycle_statuses(status):
    receipt = OperatorActionReceiptV2.model_validate(
        compact_payload(status=status, terminal=True, finished_at=2.0)
    )
    assert receipt.status == status
    assert receipt.terminal is True


def test_v2_dashboard_requires_strict_y_axis_and_catalog_embeds_it():
    dashboard = OperatorDashboardV2.model_validate(dashboard_payload())
    assert dashboard.y_axis.board_id == 4
    catalog = OperatorControlCatalogV2.model_validate({
        "schema_version": "bioxp.operator_control_catalog.v2",
        "dashboard": dashboard.model_dump(),
        "actions": [{"action_id": "oem.y.move_steps", "request_schema_version": "bioxp.operator_action_request.v2", "response_schema_version": "bioxp.operator_action_receipt.v2", "interrupt": False, "enabled": True, "disabled_reason": None}],
    })
    assert catalog.actions[0].action_id == "oem.y.move_steps"


_SERIAL206_DESTINATIONS = (
    ("LOC_MS", "LOC_MS", 0, "ordinary", ["Magnetic Station"]),
    ("LOC_OC", "LOC_OC", 1, "ordinary", ["OC chiller", "Output Chiller", "Output Tray"]),
    ("TECANRACK2", "TIP TRAY 2", 8, "ordinary", []),
    ("TECANRACK4", "TIP TRAY_4", 10, "ordinary", []),
    ("LOC_P_MS", "LOC_MS_PLATE", 25, "ordinary", []),
    ("LOC_P_OC", "LOC_OC_PLATE", 21, "ordinary", []),
    ("LOC_OC_COVER", "LOC_OC_COVER", 17, "ordinary", []),
    ("LOC_TC", "LOC_TC", 2, "ordinary", ["Thermal Cycler"]),
    ("LOC_TC_BARCODE", "LOC_TC_BARCODE", 2, "barcode", []),
    ("LOC_RC", "LOC_RC", 3, "ordinary", ["Reagent Chiller"]),
    ("LOC_RC_BARCODE", "LOC_RC_BARCODE", 3, "barcode", []),
    ("TECANRACK1", "TIP TRAY 1", 7, "ordinary", []),
    ("TECANRACK3", "TIP TRAY_3", 9, "ordinary", []),
    ("LOC_P_TC", "LOC_TC_PLATE", 23, "ordinary", []),
    ("LOC_BSC", "LOC_TC_COVER", 5, "ordinary", []),
    ("LOC_RC_COVER", "LOC_RC_COVER", 19, "ordinary", []),
    ("LOC_STRIP1", "STRIP 1", 11, "ordinary", []),
    ("LOC_STRIP2", "STRIP 2", 12, "ordinary", []),
    ("LOC_STRIP3", "STRIP 3", 13, "ordinary", []),
    ("LOC_STRIP4", "STRIP 4", 14, "ordinary", []),
    ("LOC_OC_COVER_STORAGE", "LOC_OC_COVER_S", 18, "ordinary", []),
    ("LOC_RC_COVER_STORAGE", "LOC_RC_COVER_S", 20, "ordinary", []),
    ("LOC_TROUGH", "LOC_TROUGH1", 16, "ordinary", []),
    ("LOC_BSCS", "LOC_BSCS", 4, "ordinary", []),
    ("WASTE_BIN", "Waste Bin", 6, "ordinary", ["Waste Bin"]),
    ("LOC_PARK", "Park", 28, "park", ["Park"]),
)


def _serial206_destination_options(*, enabled: bool = True, reason: str | None = None) -> list[dict[str, object]]:
    return [{
        "target": target,
        "label": label,
        "aliases": aliases,
        "location_id": location_id,
        "branch_kind": branch,
        "camera_offset_option": branch == "ordinary",
        "source_anchors": [f"ClassControlInterface:{branch}"],
        "enabled": enabled,
        "disabled_reason": reason,
    } for target, label, location_id, branch, aliases in _SERIAL206_DESTINATIONS]


def _serial206_catalog_payload(*, enabled: bool = True) -> dict[str, object]:
    payload = dashboard_payload()
    payload["deck"] = {"current_location": "LOC_TEST_02" if enabled else None, "current_well": 2 if enabled else None, "position_table_revision": "a" * 64 if enabled else None, "destination_catalog_revision": "b" * 64 if enabled else None, "semantic_state_revision": 17 if enabled else 0, "ambiguity_state": "none" if enabled else "recovery_required"}
    return {"schema_version": "bioxp.operator_control_catalog.v2", "dashboard": payload, "actions": [{"action_id": "oem.deck.move_to_location", "request_schema_version": "bioxp.operator_action_request.v2", "response_schema_version": "bioxp.operator_action_receipt.v2", "interrupt": False, "enabled": enabled, "disabled_reason": None if enabled else "canonical_deck_authority_unavailable:RuntimeError", "destination_catalog_revision": "b" * 64 if enabled else None, "position_table_revision": "a" * 64 if enabled else None, "required_boards": [4, 5], "expected_board_epoch_by_board": {"4": 2, "5": 8} if enabled else {}, "required_references": ["x", "y", "z", "g"], "destination_options": _serial206_destination_options() if enabled else []}]}


def test_deck_catalog_and_dashboard_admit_exact_serial206_contract_without_coordinates():
    catalog = OperatorControlCatalogV2.model_validate(_serial206_catalog_payload())
    action = catalog.actions[0]
    assert len(action.destination_options or []) == 26
    assert len({row.location_id for row in action.destination_options or []}) == 24
    assert [(row.target, row.location_id) for row in action.destination_options or [] if row.location_id in {2, 3}] == [
        ("LOC_TC", 2), ("LOC_TC_BARCODE", 2), ("LOC_RC", 3), ("LOC_RC_BARCODE", 3),
    ]
    assert action.required_boards == [4, 5]
    assert "x" not in action.model_dump()["destination_options"][0]
    with pytest.raises(ValidationError):
        OperatorDeckMoveInputsV1.model_validate({"target": "LOC_OC", "camera_offset": False, "x": 1})


def test_deck_catalog_rejects_counterfeit_exact_denominator_rows_aliases_and_internal_targets():
    mutations = [
        lambda rows: rows[0].__setitem__("label", "Invented"),
        lambda rows: rows[0].__setitem__("location_id", 1),
        lambda rows: rows[1].__setitem__("aliases", ["Thermal Cycler"]),
        lambda rows: rows[0].__setitem__("target", "LOC_P_OC_PRESS"),
        lambda rows: rows[7].__setitem__("branch_kind", "barcode"),
        lambda rows: rows[8].__setitem__("camera_offset_option", True),
    ]
    for mutate in mutations:
        payload = _serial206_catalog_payload()
        mutate(payload["actions"][0]["destination_options"])
        with pytest.raises(ValidationError):
            OperatorControlCatalogV2.model_validate(payload)


def test_deck_catalog_permits_only_the_two_source_authorized_duplicate_id_pairs():
    payload = _serial206_catalog_payload()
    payload["actions"][0]["destination_options"][0]["location_id"] = 1
    with pytest.raises(ValidationError):
        OperatorControlCatalogV2.model_validate(payload)

    catalog = OperatorControlCatalogV2.model_validate(_serial206_catalog_payload())
    assert len({row.location_id for row in catalog.actions[0].destination_options or []}) == 24


def test_disabled_serial206_deck_authority_preserves_null_empty_and_reason():
    catalog = OperatorControlCatalogV2.model_validate(_serial206_catalog_payload(enabled=False))
    assert catalog.actions[0].disabled_reason == "canonical_deck_authority_unavailable:RuntimeError"
    assert catalog.actions[0].destination_options == []


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["actions"][0].__setitem__("destination_catalog_revision", "c" * 64),
    lambda payload: payload["actions"][0].__setitem__("position_table_revision", "d" * 64),
    lambda payload: payload["actions"][0].__setitem__("destination_options", payload["actions"][0]["destination_options"][:1]),
    lambda payload: payload["actions"][0]["destination_options"][1].__setitem__("target", "LOC_TEST_01"),
    lambda payload: payload["actions"][0]["destination_options"][2].__setitem__("location_id", 1),
    lambda payload: payload["dashboard"]["deck"].__setitem__("ambiguity_state", "invented"),
    lambda payload: payload["actions"][0].__setitem__("required_board_ids", [4, 5]),
    lambda payload: payload["actions"][0].__setitem__("destinations", []),
])
def test_deck_catalog_rejects_incoherent_or_retired_authority_shapes(mutate):
    payload = _serial206_catalog_payload(); mutate(payload)
    with pytest.raises(ValidationError): OperatorControlCatalogV2.model_validate(payload)


def test_deck_catalog_rejects_non_sha_revision_and_empty_source_anchor():
    payload = _serial206_catalog_payload(); payload["actions"][0]["position_table_revision"] = "pt-206-9"
    with pytest.raises(ValidationError): OperatorControlCatalogV2.model_validate(payload)
    payload = _serial206_catalog_payload(); payload["actions"][0]["destination_options"][0]["source_anchors"] = []
    with pytest.raises(ValidationError): OperatorControlCatalogV2.model_validate(payload)


def test_deck_move_inputs_are_exact_target_and_camera_offset_only():
    parsed = OperatorDeckMoveInputsV1.model_validate({"target": "LOC_OC", "camera_offset": False})
    assert parsed.model_dump() == {"target": "LOC_OC", "camera_offset": False}
    with pytest.raises(ValidationError):
        OperatorDeckMoveInputsV1.model_validate({"target": "", "camera_offset": False})


@pytest.mark.parametrize("forbidden", ["x", "y", "z", "path", "speed", "acceleration", "current", "offset", "branch"])
def test_deck_move_inputs_reject_every_client_owned_physical_or_branch_field(forbidden):
    with pytest.raises(ValidationError):
        OperatorDeckMoveInputsV1.model_validate({
            "target": "LOC_OC", "camera_offset": False, forbidden: 1,
        })


def test_agent_resolves_finite_catalog_alias_to_the_same_canonical_action_schema():
    catalog = OperatorControlCatalogV2.model_validate(_serial206_catalog_payload())
    action = catalog.actions[0]
    matches = [
        option for option in action.destination_options or []
        if "OC chiller".casefold() in {alias.casefold() for alias in option.aliases}
    ]
    assert len(matches) == 1
    agent_inputs = OperatorDeckMoveInputsV1.model_validate({
        "target": matches[0].target,
        "camera_offset": False,
    })
    assert agent_inputs.model_dump() == {"target": "LOC_OC", "camera_offset": False}
    with pytest.raises(ValidationError):
        OperatorDeckMoveInputsV1.model_validate({
            "target": matches[0].target, "camera_offset": False, "x": 26213, "y": 42413,
        })


def test_deck_detail_receipt_keeps_controller_semantic_and_physical_truth_separate():
    payload = {
        **compact_payload(
            action_id="oem.deck.move_to_location",
            command_id="deck-command-1",
            status="completed",
            terminal=True,
            finished_at=2.0,
            terminal_receipt_id="deck-command-1",
            completion_class="completed",
        ),
        "canonical_inputs": {"target": "LOC_OC", "camera_offset": False},
        "requested_values": {"target": "LOC_OC", "camera_offset": False},
        "effective_values": {"target": "LOC_OC", "camera_offset": False},
        "observed_values": {},
        "raw_return_layers": {},
        "controller_evidence": {},
        "transport_artifacts": [],
        "child_receipts": [],
        "transitions": [],
        "deck_movement": {
            "target": "LOC_OC",
            "target_label": "OC chiller",
            "source_branch": "ordinary.scriptmoveTo",
            "resolved_location_id": 1,
            "destination_catalog_revision": "b" * 64,
            "position_table_revision": "a" * 64,
            "authority_snapshot_digest": "c" * 64,
            "complete_authority_digest": "d" * 64,
            "plan_digest": "e" * 64,
            "source_anchors": ["ClassControlInterface.moveTo:3691-3716"],
            "delivery_attempted": True,
            "controller_command_acknowledged": True,
            "controller_completion_verified": True,
            "hardware_postcondition_verified": True,
            "semantic_state_committed": True,
            "physical_observation_verified": False,
            "transition_revision": 3,
            "ambiguity_state": "ambiguous",
            "stages": [{
                "order": 0,
                "operation": "ForceToHighHome",
                "source_anchor": "ClassControlInterface.btnLOC1_Click:1932-1959",
                "resources": ["axis:z"],
                "arguments": {},
                "dependencies": [],
                "terminal_state": "completed",
                "terminal_evidence": {"controller_acknowledged": True},
            }],
        },
    }
    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.deck_movement.controller_completion_verified is True
    assert detail.deck_movement.semantic_state_committed is True
    assert detail.deck_movement.physical_observation_verified is False
    malformed = {**payload, "deck_movement": {**payload["deck_movement"], "x": 12}}
    with pytest.raises(ValidationError):
        operator_models.OperatorActionReceiptDetailV2.model_validate(malformed)


def test_queued_deck_detail_keeps_unestablished_planning_and_effect_evidence_unknown():
    payload = {
        **compact_payload(action_id="oem.deck.move_to_location", command_id="deck-queued-1"),
        "canonical_inputs": {"target": "LOC_OC", "camera_offset": False},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": None, "source_branch": None,
            "controller_completion_verified": None, "semantic_state_committed": None,
            "physical_observation_verified": None,
        },
    }
    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.deck_movement.target_label is None
    assert detail.deck_movement.controller_completion_verified is None


@pytest.mark.parametrize("status,terminal,controller,semantic", [
    ("queued", False, True, False),
    ("dispatched", False, False, True),
    ("ambiguous", True, True, True),
    ("failed", True, True, True),
    ("rejected", True, True, False),
])
def test_deck_detail_rejects_impossible_lifecycle_completion_claims(status, terminal, controller, semantic):
    payload = {
        **compact_payload(
            action_id="oem.deck.move_to_location", command_id="deck-bad-1",
            status=status, terminal=terminal, finished_at=2.0 if terminal else None,
        ),
        "canonical_inputs": {"target": "LOC_OC", "camera_offset": False},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": "OC chiller", "source_branch": "ordinary.scriptmoveTo",
            "controller_completion_verified": controller, "semantic_state_committed": semantic,
            "physical_observation_verified": False,
        },
    }
    with pytest.raises(ValidationError):
        operator_models.OperatorActionReceiptDetailV2.model_validate(payload)


def test_deck_controller_completion_without_semantic_commit_requires_ambiguous_recovery_outcome():
    base = {
        **compact_payload(
            action_id="oem.deck.move_to_location",
            command_id="deck-recovery-1",
            terminal=True,
            finished_at=2.0,
        ),
        "canonical_inputs": {"target": "LOC_OC", "camera_offset": False},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": "OC chiller", "source_branch": "ordinary.scriptmoveTo",
            "controller_completion_verified": True, "semantic_state_committed": False,
            "physical_observation_verified": False,
        },
    }

    with pytest.raises(ValidationError):
        operator_models.OperatorActionReceiptDetailV2.model_validate({
            **base,
            "status": "failed",
            "completion_class": "failed",
        })

    recovered = operator_models.OperatorActionReceiptDetailV2.model_validate({
        **base,
        "status": "ambiguous",
        "completion_class": "recovery_required",
    })
    assert recovered.status == "ambiguous"
    assert recovered.completion_class == "recovery_required"


@pytest.mark.parametrize("action_id", [
    "oem.deck.move_to_location", "oem.deck._mov_execution", "oem.deck._finite_operation",
])
@pytest.mark.parametrize("status", ["queued", "dispatched", "failed", "rejected", "ambiguous", "stopped", "aborted", "cancelled"])
def test_deck_preplan_outcomes_preserve_receipts_without_inventing_plan_evidence(action_id, status):
    terminal = status not in {"queued", "dispatched"}
    payload = {
        **compact_payload(action_id=action_id, status=status, terminal=terminal,
                          finished_at=2.0 if terminal else None),
        "canonical_inputs": {}, "requested_values": {}, "effective_values": {},
        "observed_values": {}, "raw_return_layers": {}, "controller_evidence": {},
        "transport_artifacts": [], "child_receipts": [], "transitions": [],
    }
    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.deck_movement is None
    assert detail.status == status
    with pytest.raises(ValidationError):
        operator_models.OperatorActionReceiptDetailV2.model_validate({
            **payload, "physical_effect_verified": True,
        })


def test_deck_postcommit_terminalization_ambiguity_preserves_semantic_truth():
    detail = operator_models.OperatorActionReceiptDetailV2.model_validate({
        **compact_payload(action_id="oem.deck.move_to_location", status="ambiguous",
                          terminal=True, finished_at=2.0, completion_class="recovery_required"),
        "canonical_inputs": {"target": "LOC_OC", "camera_offset": False},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": "LOC_OC", "source_branch": "ordinary",
            "controller_completion_verified": True, "semantic_state_committed": True,
            "physical_observation_verified": False, "ambiguity_state": "recovery_required",
        },
    })
    assert detail.deck_movement.semantic_state_committed is True
    assert detail.physical_effect_verified is False


def test_non_deck_legacy_detail_remains_compatible_without_deck_evidence():
    payload = {
        **compact_payload(), "canonical_inputs": {"steps": 1}, "requested_values": {},
        "effective_values": {}, "observed_values": {}, "raw_return_layers": {},
        "controller_evidence": {}, "transport_artifacts": [], "child_receipts": [], "transitions": [],
    }
    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.deck_movement is None


@pytest.mark.parametrize(
    ("action_id", "canonical_inputs"),
    [
        (
            "oem.deck._mov_execution",
            {
                "script_line": "MOV,LOC_OC",
                "plate_name": "Plate",
                "location_id": 1,
                "well": 0,
                "material": "plate",
                "continuation": False,
            },
        ),
        (
            "oem.deck._finite_operation",
            {"operation": "catch_plate", "operation_inputs": {}},
        ),
    ],
)
def test_terminal_internal_deck_plan_actions_accept_producer_deck_movement(action_id, canonical_inputs):
    payload = {
        **compact_payload(
            action_id=action_id,
            command_id=f"internal-{action_id}",
            status="completed",
            terminal=True,
            finished_at=2.0,
        ),
        "canonical_inputs": canonical_inputs,
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": "OC chiller",
            "source_branch": "ordinary.scriptmoveTo",
            "controller_completion_verified": False,
            "semantic_state_committed": False,
            "physical_observation_verified": False,
        },
    }

    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.action_id == action_id
    assert detail.deck_movement is not None


@pytest.mark.parametrize("status", ["queued", "dispatched", "issued_pending", "interrupting"])
@pytest.mark.parametrize("action_id", ["oem.deck._mov_execution", "oem.deck._finite_operation"])
def test_nonterminal_internal_deck_plan_actions_may_omit_deck_movement(action_id, status):
    payload = {
        **compact_payload(action_id=action_id, command_id=f"internal-{action_id}", status=status),
        "canonical_inputs": {},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
    }

    detail = operator_models.OperatorActionReceiptDetailV2.model_validate(payload)
    assert detail.deck_movement is None


@pytest.mark.parametrize(
    "status",
    ["completed"],
)
@pytest.mark.parametrize("action_id", ["oem.deck.move_to_location", "oem.deck._mov_execution", "oem.deck._finite_operation"])
def test_completed_deck_plan_actions_require_deck_movement(action_id, status):
    payload = {
        **compact_payload(
            action_id=action_id,
            command_id=f"internal-{action_id}",
            status=status,
            terminal=True,
            finished_at=2.0,
        ),
        "canonical_inputs": {},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
    }

    with pytest.raises(ValidationError, match="completed deck movement requires typed deck evidence"):
        operator_models.OperatorActionReceiptDetailV2.model_validate(payload)


@pytest.mark.parametrize(
    "action_id",
    ["oem.deck._mov_execution_extra", "oem.deck._finite_operation_extra", "oem.y.move_steps"],
)
def test_deck_movement_rejects_every_nonproducer_action_id(action_id):
    payload = {
        **compact_payload(action_id=action_id, command_id=f"nonproducer-{action_id}"),
        "canonical_inputs": {},
        "requested_values": {}, "effective_values": {}, "observed_values": {},
        "raw_return_layers": {}, "controller_evidence": {}, "transport_artifacts": [],
        "child_receipts": [], "transitions": [],
        "deck_movement": {
            "target": "LOC_OC", "target_label": None, "source_branch": None,
            "controller_completion_verified": False,
            "semantic_state_committed": False,
            "physical_observation_verified": False,
        },
    }
    with pytest.raises(ValidationError):
        operator_models.OperatorActionReceiptDetailV2.model_validate(payload)


@pytest.mark.parametrize("terminal_state", ["stopped", "aborted"])
def test_deck_stage_accepts_robot_terminal_interrupt_states(terminal_state):
    stage = {
        "order": 0,
        "operation": "MovePlate",
        "source_anchor": "operator_command_plane:deck_plan",
        "resources": ["axis:x", "axis:y"],
        "arguments": {},
        "dependencies": [],
        "terminal_state": terminal_state,
        "terminal_evidence": {"interrupt": terminal_state},
    }
    parsed = operator_models.OperatorDeckStageV1.model_validate(stage)
    assert parsed.terminal_state == terminal_state


@pytest.mark.parametrize("terminal_state", ["cancelled", "dispatched"])
def test_deck_stage_rejects_unpublished_terminal_state(terminal_state):
    with pytest.raises(ValidationError):
        operator_models.OperatorDeckStageV1.model_validate({
            "order": 0,
            "operation": "MovePlate",
            "source_anchor": "operator_command_plane:deck_plan",
            "resources": [],
            "arguments": {},
            "dependencies": [],
            "terminal_state": terminal_state,
            "terminal_evidence": None,
        })


def test_v2_dashboard_accepts_robot_unbound_y_runtime_projection():
    payload = dashboard_payload(board_state="unknown")
    payload["y_axis"].update({
        "lifecycle_state": "unbound",
        "reference_state": "unreferenced",
        "prior_board_epoch": None,
        "active_board_epoch": None,
        "prepared_board_epoch": None,
        "profile_fingerprint": None,
        "profile_readback_valid": False,
    })
    dashboard = OperatorDashboardV2.model_validate(payload)
    assert dashboard.y_axis.lifecycle_state == "unbound"


def test_v1_dashboard_z_axis_is_closed_and_requires_observed_only_switch_metadata():
    adapter = TypeAdapter(OperatorDashboard.model_fields["z_axis"].annotation)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "status": None,
            "provider": {"bound": False},
            "snapshot_freshness": {},
            "last_failure": None,
            "authority": "unbound",
            "unexpected": True,
        })
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "status": None,
            "provider": {"bound": True},
            "snapshot_freshness": {},
            "last_failure": None,
            "authority": "Serial206OemInitializationProvider",
        })


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


@pytest.mark.parametrize("steps", [-(2 ** 31) - 1, 2 ** 31])
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
        "source_call_completed": True,
        "source_return_ok": True,
        "controller_stop_acknowledged": True,
        "controller_response": {"ok": True},
        "controller_response_evidence": {
            "evidence_id": "attempt:controller_response:digest",
            "evidence_kind": "controller_response",
            "content_sha256": "a" * 64,
            "payload_bytes": 11,
        },
        "error": None,
        "physical_effect_verified": False,
        "persistence_state": "committed",
        "recovery_hold": False,
        "transition_sequence": 9,
        "terminal_transition_sequences": [10],
    }
    payload.update(overrides)
    return payload


def test_action_request_keeps_wire_inputs_unambiguous_until_route_validation():
    parsed = OperatorActionRequestV2.model_validate({
        "expected_connection_generation": 2,
        "schema_version": "bioxp.operator_action_request.v2",
        "idempotency_key": "typed-action-inputs",
        "expected_ownership_generation": 7,
        "expected_board_epoch_by_board": {},
        "inputs": {"steps": -123},
    })
    assert type(parsed.inputs) is dict
    assert parsed.inputs == {"steps": -123}


def test_interrupt_evidence_pointer_rejects_extra_or_malformed_fields():
    assert OperatorInterruptReceiptV1 is not None
    malformed = interrupt_receipt_payload()
    malformed["controller_response_evidence"] = {
        **malformed["controller_response_evidence"],
        "content_sha256": "not-a-digest",
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        OperatorInterruptReceiptV1.model_validate(malformed)


def test_interrupt_receipt_rejects_success_without_completed_source_call():
    assert OperatorInterruptReceiptV1 is not None
    with pytest.raises(ValidationError):
        OperatorInterruptReceiptV1.model_validate(
            interrupt_receipt_payload(
                source_call_completed=False,
                source_return_ok=True,
                error="source_call_not_completed",
            )
        )


def test_interrupt_receipt_closes_identity_controller_persistence_and_recovery_semantics():
    assert OperatorInterruptReceiptV1 is not None
    committed = OperatorInterruptReceiptV1.model_validate(interrupt_receipt_payload())
    assert committed.action_id == "oem.y.stop"
    assert committed.controller_response_evidence.payload_bytes == 11
    replay = OperatorInterruptReceiptV1.model_validate(interrupt_receipt_payload(idempotent_replay=True))
    assert replay.idempotent_replay is True
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
    with pytest.raises(ValidationError):
        OperatorInterruptReceiptV1.model_validate(fallback_payload)
    for invalid in [
        interrupt_receipt_payload(action_id="oem.x.stop"),
        interrupt_receipt_payload(controller_stop_attempted=False),
        interrupt_receipt_payload(physical_effect_verified=True),
        interrupt_receipt_payload(persistence_state="committed", recovery_hold=True),
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

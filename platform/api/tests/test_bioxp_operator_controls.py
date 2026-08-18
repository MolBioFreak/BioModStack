from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from routers import bioxp
from routers.bioxp.operator_controls import _translate_robot_error
from services.bioxp.errors import ConnectionStateError, RobotResponseError
from services.bioxp.operator_models import (
    OperatorActionHistory,
    OperatorActionReceipt,
    OperatorDashboard,
    OperatorDashboardXFailure,
    OperatorDashboardXReference,
)
from services.bioxp.operator_semantic_quarantine import OPERATOR_SEMANTIC_QUARANTINE_BY_PATH
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

REGISTRY = "1" * 64
LOCK = "2" * 64


def pipette_channel(channel: int) -> dict:
    return {
        "ok": True,
        "transport": "novo_usb_can",
        "channel": channel,
        "bitrate": 0,
        "pipette_id": channel,
        "transport_details": {
            "source": "OEM Novo.Devices.CanInterfaceBoard over one shared NovoRouter",
            "vid": "0x03eb",
            "pid": "0x2423",
            "alt": 1,
            "shared_bioxp_usb_runtime": True,
        },
        "available": True,
        "initialized": False,
        "software_initialized": False,
        "tip_loaded": False,
        "software_tip_loaded": False,
        "pressure_profile": "1R",
        "top_speed": 1000.0,
        "last_command": None,
        "last_transaction": None,
        "pipette_message_state": {},
        "oem_initialization_counter": 0,
        "oem_diagnosis": None,
        "oem_error_queue": [],
        "oem_process_error_code": None,
        "hardware_tip_status": None,
        "hardware_pressure": None,
        "hardware_truth_level": "cached_transport_state",
        "ack_required": True,
        "delivery_verified": False,
        "controller_acknowledged": None,
        "completion_verified": False,
        "hardware_precondition_verified": False,
        "hardware_postcondition_verified": False,
        "state_reconciled": False,
        "state_reconciliation_source": None,
        "physical_effect_verified": False,
        "response_timeout_s": 60.0,
        "liquid_level_ul": 0.0,
        "front_air_level_ul": 0.0,
        "rear_air_level_ul": 0.0,
    }


def pipette_group() -> dict:
    return {
        "ok": True,
        "transport": "novo_usb_can",
        "channels": [pipette_channel(channel) for channel in range(4)],
        "channel_count": 4,
        "group_status_spacing_ms": 30,
        "live_query_performed": False,
        "last_group_transaction": None,
        "liquid_mutation_enabled": False,
        "tip_type": 201,
        "tip_location": -1,
        "allow_to_stop": True,
        "fluid_detection_timestamps": {str(channel): None for channel in range(4)},
        "last_error": None,
        "physical_effect_verified": False,
    }


def pipette_readback(*, include_data: bool = False) -> dict:
    return {
        "ok": True,
        "semantic_ok": True,
        "available": True,
        "channel_count": 4,
        "channels_constructed_unconditionally": [0, 1, 2, 3],
        "channels": [
            {
                "channel": channel,
                "semantic_ok": True,
                "firmware": {"ok": True, "value": "1.0"},
                "status": {"ok": True, "error_code": 0},
                "tip": {"ok": True, "hardware_truth_level": "hardware_query", "tip_loaded": False},
                "pressure": None,
                "data": {"?40": {"ok": True, "value": 40}} if include_data else None,
            }
            for channel in range(4)
        ],
        "include_data": include_data,
        "live_query_performed": True,
        "truth_source": "live_hardware_queries",
        "delivery_verified": False,
        "controller_acknowledged": False,
        "completion_verified": False,
        "hardware_postcondition_verified": False,
        "physical_effect_verified": False,
        "oem_source_anchor": "ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData",
        "receipt_id": "a" * 32,
        "receipt_truth": {
            "delivery_verified": False,
            "controller_acknowledged": False,
            "completion_verified": False,
            "hardware_precondition_verified": False,
            "hardware_postcondition_verified": False,
            "physical_effect_verified": False,
            "physical_effect_claim_suppressed": True,
        },
    }


def test_x_dashboard_accepts_generation_drift_failure_context() -> None:
    failure = OperatorDashboardXFailure.model_validate(
        {
            "failure": "x_board_lifecycle_generation_changed",
            "recorded_generation": 1,
            "current_generation": 1,
            "recorded_board_lifecycle_generation": 1,
            "current_board_lifecycle_generation": None,
        }
    )

    assert failure.failure == "x_board_lifecycle_generation_changed"


def catalog():
    dashboard = {
        "schema_version": "bioxp.operator_dashboard.v1",
        "ownership_generation": 7,
        "connection": {"live": True, "ownership": {"transport": "owned", "usb": "service", "router": "running", "CAN_READY": True}},
        "motion": {"enabled": False, "reason": "Motion is inactive."},
        "operation": {"state": "stopped", "reason": "ready"},
        "enclosure": {"door_closed": True, "latch_closed": True},
        "axes": [{"axis": "x", "reference": "referenced", "position_steps": 123, "speed_steps_s": 0, "run_current": 31, "standby_current": 8, "left_switch_raw_active": False, "right_switch_raw_active": True, "left_switch_active": False, "right_switch_active": True, "motor_temperature_c": None, "motor_temperature_available": False}],
        "x_axis": {
            "status": {"axis": "x", "reference": "referenced", "position_steps": 123, "speed_steps_s": 0, "run_current": 31, "standby_current": None, "left_switch_state": 0, "right_switch_state": 1, "left_switch_raw_active": None, "right_switch_raw_active": None, "left_switch_active": None, "right_switch_active": None, "left_switch_disabled": False, "right_switch_disabled": True, "coordinate_contract": "serial206_x_source_0_90263_effective_min_60_relative_margin_20", "min_steps": 0, "max_steps": 90263, "motor_temperature_c": None, "motor_temperature_available": False, "telemetry_authority": "motor_x_terminal_status", "physical_position_verified": False},
            "provider": {
                "authority": "Serial206OemInitializationProvider",
                "axis": "x",
                "board": 5,
                "motor": 0,
                "source_min_steps": 0,
                "source_max_steps": 90263,
                "effective_absolute_min_steps": 60,
                "relative_limit_margin_steps": 20,
                "current_generation": 7,
                "current_board_lifecycle_generation": 3,
                "board_generation_fresh": True,
                "lifecycle": {
                    "schema_version": "bioxp.serial206_x_lifecycle.v2",
                    "state": "referenced_ready",
                    "generation": 7,
                    "board_lifecycle_generation": 3,
                    "reference_state": "referenced",
                    "prepared_receipt": None,
                    "active_receipt": None,
                    "pending_ticket": None,
                    "awaiting_observation_receipt_id": None,
                    "terminal_state": None,
                    "last_failure": None,
                    "receipt_storage": "robot_sqlite",
                    "receipt_detail_on_request": True,
                    "recent_receipt_count": 1,
                    "latest_receipt": {"command_id": "x-status-1", "intent": "status", "status": "completed"},
                },
                "live_status": {
                    "ok": True,
                    "axis": "x",
                    "board": 5,
                    "motor": 0,
                    "position_steps": 123,
                    "speed_steps_s": 0,
                    "max_speed": 1700,
                    "max_acceleration": 350,
                    "max_current": 31,
                    "left_switch_state": 0,
                    "right_switch_state": 1,
                    "right_switch_disabled": True,
                    "left_switch_disabled": False,
                    "stall_guard": 16,
                    "profile_verified": True,
                    "expected_profile": {4: 1700, 5: 350, 6: 31, 205: 16},
                    "switch_mask_verified": True,
                    "switch_mask_tuple": {12: 1, 13: 0},
                    "expected_switch_masks": {12: 1, 13: 0},
                    "readbacks": {
                        param: {"board": 5, "param": param, "motor": 0, "ack": None, "value": value}
                        for param, value in {1: 123, 3: 0, 4: 1700, 5: 350, 6: 31, 9: 0, 10: 1, 12: 1, 13: 0, 205: 16}.items()
                    },
                    "authority": "serial206_x_terminal_register_readback",
                    "failure": None,
                },
                "switch_masks": {"expected": {12: 1, 13: 0}, "verified": True},
                "profile": {"expected": {"4": 1700, "5": 350, "6": 31, "205": 16}, "verified": True},
                "reference": {
                    "ok": True,
                    "axes": ["x"],
                    "rows": {"x": {"axis": "x", "state": "referenced", "origin_position_steps": 0, "source": "home", "note": None, "updated_at": "2026-08-12T00:00:00Z", "last_motion_kind": "home"}},
                    "persisted": True,
                    "verified": True,
                    "durable_clean": True,
                    "authority_untrusted": False,
                },
                "bound": True,
                "physical_position_verified": False,
            },
            "snapshot_freshness": {"state": "fresh"},
            "last_failure": None,
            "latest_receipt": {"command_id": "x-status-1", "intent": "status", "status": "completed"},
            "authority": "Serial206OemInitializationProvider",
            "physical_position_verified": False,
        },
        "z_axis": {
            "status": None,
            "provider": {"bound": True, "state": "prepared_unreferenced"},
            "snapshot_freshness": {"state": "fresh"},
            "last_failure": None,
            "authority": "Serial206OemInitializationProvider",
            "board": 4,
            "motor": 1,
        },
        "temperatures": [{"sensor": "tc_temp_c", "label": "Thermal cycler block", "unit": "°C", "temperature_c": 37.0, "available": True}],
        "pipettes": pipette_group(),
        "snapshot": {"snapshot_id": "snap-1", "freshness": {"state": "fresh", "age_s": 1.0, "fresh_for_s": 30.0}, "collection_triggered": False},
    }
    return {
        "schema_name": "bioxp.operator_control_catalog",
        "schema_version": "bioxp.operator_control_catalog.v1",
        "machine_serial": "206",
        "ownership_generation": 7,
        "registry_sha256": REGISTRY,
        "evidence_lock_sha256": LOCK,
        "source_authority_verified": True,
        "dashboard": dashboard,
        "actions": [{
            "action_id": "motion.home_xy",
            "label": "OEM HomeXY",
            "subsystem": "gantry",
            "category": "homing",
            "kind": "meta",
            "safety_class": "motion",
            "description": "Robot-owned source-shaped X/Y homing composite.",
            "source_anchor": "MachineControlLibrary.HomeXY",
            "informational_method": "POST",
            "informational_path": "/operator/actions/motion.home_xy",
            "provider_available": True,
            "provider_unavailable_reason": None,
            "available": False,
            "unavailable_reason": "Motion is inactive. Activate motion before moving this motor.",
            "enabled": False,
            "disabled_reason": "Motion is inactive. Activate motion before moving this motor.",
            "dependencies": [{"key": "motion_enabled", "label": "Motion enabled", "met": False, "reason": "Motion is inactive. Activate motion before moving this motor."}],
            "requires_confirmation": True,
            "timeout_seconds": 300,
            "inputs": [{
                "name": "timeout_s", "wire_name": "timeout_s", "label": "Timeout S",
                "value_type": "number", "location": "body", "required": True,
                "description": "Bounded timeout.", "unit": "s", "enum_values": [],
                "minimum": None, "maximum": 60.0,
                "exclusive_minimum": 0.1, "exclusive_maximum": None,
                "default": 12.0,
            }],
            "stages": ["home_x", "home_y", "verify_xy"],
        }],
    }


def receipt(*, action_id="motion.home_xy", key="invoke-12345678", command_id="cmd-1"):
    return {
        "schema_version": "bioxp.operator_action_receipt.v1",
        "command_id": command_id,
        "action_id": action_id,
        "kind": "meta",
        "safety_class": "motion",
        "status": "acknowledged",
        "idempotency_key": key,
        "ownership_generation": 7,
        "started_at": "2026-07-30T18:00:00Z",
        "finished_at": "2026-07-30T18:00:01Z",
        "duration_ms": 1000,
        "remote_acknowledged": True,
        "controller_acknowledged": True,
        "controller_terminal_state_verified": True,
        "physical_effect_verified": False,
        "machine_assessment": "unverified",
        "operator_assessment": None,
        "operator_note": None,
        "inputs": {},
        "response": {"controller_acknowledged": True},
        "authority_receipt_id": "authority-command-1",
        "authority_receipt_status": "completed",
        "observation_receipt_id": None,
        "observes_command_id": None,
        "error": None,
        "stage_receipts": [],
    }


def test_completed_x_receipt_accepts_robot_http_response_envelope():
    completed = receipt(action_id="oem.x.manual_panel_home", command_id="operator-x-home-1")
    completed.update({
        "status": "completed",
        "response": {
            "http_status": 200,
            "body": {
                "ok": True,
                "axis": "x",
                "intent": "manual_panel_home",
                "state": "awaiting_operator_observation",
                "authority_receipt": {
                    "command_id": "operator-x-home-1",
                    "status": "completed",
                },
            },
        },
        "authority_receipt_id": "operator-x-home-1",
        "authority_receipt_status": "completed",
    })

    parsed = OperatorActionReceipt.model_validate(completed)

    assert parsed.command_id == "operator-x-home-1"
    assert parsed.status == "completed"


def test_legacy_completed_x_receipt_derives_self_authority_from_bound_fingerprint():
    completed = receipt(action_id="oem.x.manual_panel_home", command_id="operator-x-home-legacy")
    completed.update({
        "status": "completed",
        "response": {
            "http_status": 200,
            "body": {
                "ok": True,
                "axis": "x",
                "intent": "manual_panel_home",
                "state": "awaiting_operator_observation",
            },
        },
        "authority_receipt_id": None,
        "authority_receipt_status": None,
        "authority_fingerprint": "a" * 64,
    })

    parsed = OperatorActionReceipt.model_validate(completed)

    assert parsed.authority_receipt_id == "operator-x-home-legacy"
    assert parsed.authority_receipt_status == "completed"


def test_legacy_x_home_history_summary_derives_bound_self_authority():
    completed = receipt(action_id="oem.x.manual_panel_home", command_id="operator-x-home-summary")
    completed.update({
        "status": "completed",
        "response": {
            "http_status": 200,
            "body": {
                "ok": True,
                "result": {"ok": {}, "physical_effect_verified": {}},
                "state": "awaiting_operator_observation",
            },
        },
        "authority_receipt_id": None,
        "authority_receipt_status": None,
        "authority_fingerprint": "b" * 64,
    })

    parsed = OperatorActionReceipt.model_validate(completed)

    assert parsed.authority_receipt_id == "operator-x-home-summary"
    assert parsed.authority_receipt_status == "completed"


def test_bounded_x_home_history_summary_derives_completed_self_authority():
    completed = receipt(action_id="oem.x.manual_panel_home", command_id="operator-x-home-bounded-summary")
    completed.update({
        "status": "completed",
        "response": {
            "http_status": 200,
            "body": {
                "ok": True,
                "result": {"ok": {}, "physical_effect_verified": {}},
                "state": "awaiting_operator_observation",
            },
        },
        "authority_receipt_id": "operator-x-home-bounded-summary",
        "authority_receipt_status": {"omitted": "item_limit"},
        "authority_fingerprint": "b" * 64,
    })

    parsed = OperatorActionReceipt.model_validate(completed)

    assert parsed.authority_receipt_id == "operator-x-home-bounded-summary"
    assert parsed.authority_receipt_status == "completed"


def test_bounded_completed_x_observation_summary_keeps_exact_receipt_binding():
    observed = receipt(action_id="oem.x.observe", command_id="operator-x-observe-summary")
    observed.update({
        "status": "completed",
        "controller_acknowledged": False,
        "controller_terminal_state_verified": False,
        "authority_receipt_id": None,
        "authority_receipt_status": None,
        "authority_fingerprint": "c" * 64,
        "observation_receipt_id": "operator-x-observe-summary",
        "observes_command_id": "operator-x-home-summary",
        "inputs": {
            "command_id": "operator-x-home-summary",
            "verdict": "pass",
            "physical_motion_observed": True,
            "expected_direction_observed": True,
            "home_endpoint_observed": True,
            "stopped_observed": True,
            "note": "Christian confirmed the X Home was kosher.",
        },
        "response": {
            "http_status": 200,
            "body": {
                "ok": True,
                "state": "referenced_ready",
                "observation": {
                    "command_id": "operator-x-observe-summary",
                    "status": "completed",
                    "reference_persistence": {"ok": True, "state": "referenced"},
                },
            },
        },
    })

    parsed = OperatorActionReceipt.model_validate(observed)

    assert parsed.observation_receipt_id == "operator-x-observe-summary"
    assert parsed.observes_command_id == "operator-x-home-summary"


def test_history_accepts_explicit_legacy_authority_status_omission_marker():
    legacy = receipt()
    legacy["authority_receipt_status"] = {"omitted": "item_limit"}

    parsed = OperatorActionHistory.model_validate({
        "schema_version": "bioxp.operator_action_history.v1",
        "receipts": [legacy],
    })

    assert parsed.receipts[0].model_dump(exclude_none=True)["authority_receipt_status"] == {"omitted": "item_limit"}


def test_reference_success_derives_trusted_authority_when_field_is_absent():
    parsed = OperatorDashboardXReference.model_validate(
        {
            "ok": True,
            "persisted": True,
            "verified": True,
            "durable_clean": True,
            "axes": ["x"],
            "rows": {
                "x": {
                    "axis": "x",
                    "state": "unknown",
                    "origin_position_steps": None,
                    "source": None,
                    "note": None,
                    "updated_at": None,
                    "last_motion_kind": None,
                }
            },
        }
    )

    assert parsed.model_dump()["authority_untrusted"] is False


class FakeRobotClient:
    def __init__(self):
        self.calls = []
        self.responses = {
            "operator_control_catalog": catalog(),
            "operator_dashboard": catalog()["dashboard"],
            "pipette_readback": pipette_readback(),
            "pipette_application_status": {
                "ok": False,
                "mode": "plan_only",
                "execution_admitted": False,
                "physical_effect_verified": False,
                "operations": ["load_tip", "move_to_waste", "detect_fluid", "plunger_up", "plunger_down"],
                "dependencies": {
                    name: {
                        "bound": name != "gantry",
                        "authority": f"test.{name}" if name != "gantry" else None,
                        "generation": 7,
                        "state": {"ready": name != "gantry"},
                        "blockers": [] if name != "gantry" else ["gantry_reference_unavailable"],
                    }
                    for name in ("deck", "gantry", "z", "pressure", "pipette", "machine_state")
                },
                "required_dependencies": ["deck", "gantry", "machine_state", "pipette", "pressure", "z"],
                "missing_dependencies": ["gantry"],
                "dependency_blockers": ["gantry:unbound"],
                "dependencies_satisfied": False,
                "blocker": "physical_pipette_execution_not_authorized",
            },
            "pipette_application_plan": {
                "ok": False,
                "operation": "detect_fluid",
                "mode": "plan_only",
                "execution_admitted": False,
                "motion_commanded": False,
                "liquid_mutation_commanded": False,
                "controller_acknowledged": False,
                "completion_verified": False,
                "physical_effect_verified": False,
                "state_reconciled": False,
                "requested_inputs": {"fluid_class": "RC"},
                "effective_inputs": None,
                "steps": [{"action": "resolve_fluid_target", "mutates": False, "owner": "deck"}],
                "dependencies": {
                    "deck": {"bound": True, "authority": "test.deck", "generation": 7, "state": {"ready": True}, "blockers": []},
                    "gantry": {"bound": False, "authority": None, "generation": 7, "state": {"ready": False}, "blockers": ["gantry_reference_unavailable"]},
                },
                "required_dependencies": ["deck", "gantry"],
                "missing_dependencies": ["gantry"],
                "dependency_blockers": ["gantry:unbound"],
                "dependencies_satisfied": False,
                "required_completion_evidence": ["controller_fluid_completion"],
                "constants": {"supported_offset_classes": ["TC", "MS", "OC", "RC", "STRIP"]},
                "oem_source_anchor": "ControlLib fluid detection",
                "blocker": "application_dependencies_unbound",
                "receipt_id": "0123456789abcdef0123456789abcdef",
                "receipt_truth": {
                    "delivery_verified": False,
                    "controller_acknowledged": False,
                    "completion_verified": False,
                    "hardware_precondition_verified": False,
                    "hardware_postcondition_verified": False,
                    "physical_effect_verified": False,
                    "physical_effect_claim_suppressed": True,
                },
            },
            "operator_action_admission": {"action_id": "motion.home_xy", "ownership_generation": 7, "enabled": False, "disabled_reason": "Motion is inactive. Activate motion before moving this motor.", "dependencies": [{"key": "motion_enabled", "label": "Motion enabled", "met": False, "reason": "Motion is inactive. Activate motion before moving this motor."}]},
            "invoke_operator_action": receipt(),
            "operator_action_history": {
                "schema_version": "bioxp.operator_action_history.v1",
                "receipts": [receipt()],
            },
            "operator_action_receipt": receipt(),
            "assess_operator_action": {
                **receipt(),
                "operator_assessment": "pass",
                "operator_note": "Observed X/Y references.",
                "operator_assessment_idempotency_key": "assess-12345678",
                "operator_assessed_at": 1785434400.0,
            },
        }

    async def request(self, route_name, **kwargs):
        self.calls.append((route_name, kwargs))
        return self.responses[route_name]


@dataclass
class FakeSnapshot:
    generation: int = 77


class FakeConnection:
    def __init__(self):
        self.client = FakeRobotClient()
        self.value = FakeSnapshot()
        self.safety_interrupt_calls = []

    def snapshot(self):
        return self.value

    async def request_active(self, route_name, *, expected_generation, require_fresh=True, **kwargs):
        if expected_generation != self.value.generation:
            raise ConnectionStateError(
                f"BioXP connection generation changed: expected {expected_generation}, current {self.value.generation}"
            )
        assert require_fresh is True
        return await self.client.request(route_name, **kwargs)

    async def request_active_query(self, route_name, *, expected_generation, require_fresh=True, **kwargs):
        return await self.request_active(
            route_name,
            expected_generation=expected_generation,
            require_fresh=require_fresh,
            **kwargs,
        )

    async def request_active_safety_interrupt(self, route_name, *, expected_generation, **kwargs):
        if expected_generation != self.value.generation:
            raise ConnectionStateError(
                f"BioXP connection generation changed: expected {expected_generation}, current {self.value.generation}"
            )
        self.safety_interrupt_calls.append((route_name, kwargs))
        return await self.client.request(route_name, **kwargs)


def make_client(monkeypatch, *, mutations=True):
    if mutations:
        monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    else:
        monkeypatch.delenv("BMS_BIOXP_MUTATIONS_ENABLED", raising=False)
    runtime = SimpleNamespace(connection=FakeConnection())
    app = FastAPI()
    app.state.bioxp_runtime = runtime
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app), runtime


def test_robot_409_translation_preserves_structured_detail():
    detail = {"error": "action_unavailable", "reason": {"key": "z_switch_masks_clear", "met": False}}
    translated = _translate_robot_error(RobotResponseError(409, detail))
    assert translated.status_code == 409
    assert translated.detail == {
        "error": "bioxp_robot_response_error",
        "robot_status": 409,
        "robot_detail": detail,
    }


def test_robot_client_uses_fixed_operator_routes_only():
    assert DEFAULT_ROBOT_ROUTES["operator_control_catalog"][:2] == ("GET", "/operator/control-catalog")
    assert DEFAULT_ROBOT_ROUTES["operator_dashboard"][:2] == ("GET", "/operator/dashboard")
    assert DEFAULT_ROBOT_ROUTES["pipette_readback"][:2] == ("POST", "/liquid/readback")
    assert DEFAULT_ROBOT_ROUTES["pipette_application_status"][:2] == ("GET", "/liquid/application/status")
    assert DEFAULT_ROBOT_ROUTES["pipette_application_plan"][:2] == ("POST", "/liquid/application/plan")
    assert DEFAULT_ROBOT_ROUTES["operator_action_admission"][:2] == ("POST", "/operator/actions/{action_id}/admission")
    assert DEFAULT_ROBOT_ROUTES["invoke_operator_action"][:2] == ("POST", "/operator/actions/{action_id}")
    assert DEFAULT_ROBOT_ROUTES["operator_action_history"][:2] == ("GET", "/operator/actions/history")
    assert DEFAULT_ROBOT_ROUTES["operator_action_receipt"][:2] == ("GET", "/operator/actions/receipts/{command_id}")
    assert DEFAULT_ROBOT_ROUTES["assess_operator_action"][:2] == ("POST", "/operator/actions/receipts/{command_id}/assessment")


def test_typed_pipette_application_proxy_is_plan_only(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)

    status = client.get("/api/bioxp/operator-controls/pipettes/application/status")
    plan = client.post(
        "/api/bioxp/operator-controls/pipettes/application/plan",
        json={"operation": "detect_fluid", "fluid_class": "RC"},
    )

    assert status.status_code == 200
    assert status.json()["execution_admitted"] is False
    assert plan.status_code == 200
    assert plan.json()["motion_commanded"] is False
    assert plan.json()["controller_acknowledged"] is False
    assert [call[0] for call in runtime.connection.client.calls[-2:]] == [
        "pipette_application_status",
        "pipette_application_plan",
    ]


def test_typed_pipette_active_readback_proxy_forwards_fixed_request(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/readback",
        json={"include_data": False},
    )

    assert response.status_code == 200
    assert response.json()["channels_constructed_unconditionally"] == [0, 1, 2, 3]
    assert response.json()["live_query_performed"] is True
    assert runtime.connection.client.calls == [
        ("pipette_readback", {"json_data": {"include_data": False}}),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["channels"].pop(),
        lambda payload: payload["channels"].__setitem__(3, copy.deepcopy(payload["channels"][2])),
        lambda payload: payload["channels"][0].__setitem__("invented", True),
        lambda payload: payload.__setitem__("controller_acknowledged", True),
        lambda payload: payload["receipt_truth"].__setitem__("physical_effect_verified", True),
        lambda payload: payload.__setitem__("truth_source", "cached_transport_state"),
    ],
)
def test_pipette_active_readback_rejects_malformed_or_inflated_evidence(monkeypatch, mutate):
    client, runtime = make_client(monkeypatch, mutations=False)
    payload = pipette_readback()
    mutate(payload)
    runtime.connection.client.responses["pipette_readback"] = payload

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/readback",
        json={"include_data": False},
    )

    assert response.status_code == 502


def test_pipette_active_readback_request_rejects_unknown_fields(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/readback",
        json={"include_data": False, "operation": "aspirate"},
    )

    assert response.status_code == 422
    assert runtime.connection.client.calls == []


def test_pipette_dashboard_accepts_exact_closed_four_channel_projection():
    parsed = OperatorDashboard.model_validate(catalog()["dashboard"])

    assert [channel.channel for channel in parsed.pipettes.channels] == [0, 1, 2, 3]
    assert parsed.pipettes.live_query_performed is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda group: group["channels"].pop(),
        lambda group: group["channels"].append(pipette_channel(3)),
        lambda group: group["channels"].__setitem__(3, pipette_channel(2)),
        lambda group: group["channels"][1].__setitem__("pipette_id", 2),
        lambda group: group["channels"][0].__setitem__("position_steps", 123),
        lambda group: group.__setitem__("physical_effect_verified", True),
        lambda group: group["channels"][0].__setitem__("physical_effect_verified", True),
        lambda group: group["channels"][0].__setitem__("hardware_pressure", "not-evidence"),
        lambda group: group.__setitem__("application", {
            "ok": True,
            "mode": "plan_only",
            "execution_admitted": False,
            "physical_effect_verified": False,
            "operations": ["load_tip"] * 5,
            "blocker": "physical_pipette_execution_not_authorized",
        }),
        lambda group: group.__setitem__("unexpected", "invented"),
    ],
)
def test_pipette_dashboard_rejects_malformed_or_phase_inflated_projection(mutate):
    dashboard = copy.deepcopy(catalog()["dashboard"])
    mutate(dashboard["pipettes"])

    with pytest.raises(ValidationError):
        OperatorDashboard.model_validate(dashboard)


def test_pipette_dashboard_rejects_reordered_distinct_channels():
    dashboard = copy.deepcopy(catalog()["dashboard"])
    dashboard["pipettes"]["channels"] = [
        pipette_channel(3),
        pipette_channel(2),
        pipette_channel(1),
        pipette_channel(0),
    ]

    with pytest.raises(ValidationError, match="ordered"):
        OperatorDashboard.model_validate(dashboard)


def _real_hardware_tip_evidence() -> dict:
    return {
        "ok": True,
        "hardware_truth_level": "hardware_query",
        "reply_received": True,
        "semantic_ok": True,
        "tip_loaded": False,
        "pressure": None,
        "error": None,
        "delivery_verified": True,
        "controller_acknowledged": False,
        "completion_verified": False,
        "board_id": 0x50B,
        "payload": [0x20, 0x60, ord("0")],
        "dlc": 3,
        "command_name": "query_tip_status",
        "ack_required": True,
        "tx_ok": True,
        "immediate_ack_received": False,
        "semantic_query_response_verified": True,
        "completion_deferred": False,
        "completion_owner_token": None,
        "ack": {"ok": True, "received": True, "dlc": 3, "data": [0x20, 0x60, ord("0")], "outcome": "completion"},
        "provenance": {"channel": 0, "outcome": "completion", "frames": []},
        "pipette_message_state": {},
        "ascii_command": "?31",
        "length": 3,
        "observed_at": 1785434400.0,
        "reader_generation": 1,
        "oem_source_anchor": "ClassPipette.QueryTipStatus: ?31",
    }


def test_pipette_dashboard_hardware_evidence_closed_model_accepts_exact_producer_envelope():
    from services.bioxp.operator_models import OperatorDashboardPipetteHardwareEvidence

    parsed = OperatorDashboardPipetteHardwareEvidence.model_validate(_real_hardware_tip_evidence())
    assert parsed.ok is True
    assert parsed.reader_generation == 1
    assert parsed.tip_loaded is False


def test_pipette_dashboard_hardware_evidence_closed_model_rejects_unknown_keys():
    from services.bioxp.operator_models import OperatorDashboardPipetteHardwareEvidence

    evidence = _real_hardware_tip_evidence()
    evidence["invented"] = True
    with pytest.raises(ValidationError):
        OperatorDashboardPipetteHardwareEvidence.model_validate(evidence)


def test_pipette_receipt_source_identity_requires_exact_producer_source_role_keys():
    from services.bioxp.operator_models import PipetteReceiptSourceIdentity

    base = {
        "repository_root": "/opt/bioxp",
        "source_sha256": {
            "pipette_models": "1" * 64,
            "pipette_transport": "2" * 64,
            "pipette_receipts": "3" * 64,
            "can_driver": "4" * 64,
            "novo_router": "5" * 64,
            "novo_usb_can": "6" * 64,
            "pipette_service": "7" * 64,
            "pipette_spec": "8" * 64,
        },
        "registry_sha256": REGISTRY,
        "evidence_authority": {
            "evidence_lock_path": "/opt/bioxp/oem/evidence_lock.json",
            "evidence_lock_sha256": LOCK,
            "evidence_lock_schema": "bioxp.oem_evidence_lock.v4",
            "acquisition_id": "acq-1",
            "evidence_lock_identity_verified": True,
        },
        "authority_verified": True,
    }
    parsed = PipetteReceiptSourceIdentity.model_validate(base)
    assert parsed.authority_verified is True

    extra = copy.deepcopy(base)
    extra["source_sha256"]["invented"] = "9" * 64
    with pytest.raises(ValidationError):
        PipetteReceiptSourceIdentity.model_validate(extra)

    missing = copy.deepcopy(base)
    missing["source_sha256"].pop("can_driver")
    with pytest.raises(ValidationError):
        PipetteReceiptSourceIdentity.model_validate(missing)

    bad_authority = copy.deepcopy(base)
    bad_authority["evidence_authority"]["invented"] = True
    with pytest.raises(ValidationError):
        PipetteReceiptSourceIdentity.model_validate(bad_authority)

    unverified = copy.deepcopy(base)
    unverified["authority_verified"] = False
    with pytest.raises(ValidationError):
        PipetteReceiptSourceIdentity.model_validate(unverified)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "move_to_waste", "home_z_after": True},
        {"operation": "move_to_waste", "fluid_class": "RC"},
        {"operation": "detect_fluid"},
        {"operation": "detect_fluid", "fluid_class": "RC", "tip_location": 0},
        {"operation": "load_tip", "tip_tray": "tray", "tip_well": "A1", "tip_type": 201},
        {"operation": "plunger_up", "fluid_class": "RC"},
    ],
)
def test_pipette_plan_request_rejects_irrelevant_or_missing_operation_fields(monkeypatch, payload):
    client, runtime = make_client(monkeypatch, mutations=False)

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/application/plan",
        json=payload,
    )

    assert response.status_code == 422
    assert runtime.connection.client.calls == []


@pytest.mark.parametrize(
    ("payload", "forwarded"),
    [
        ({"operation": "move_to_waste"}, {"operation": "move_to_waste"}),
        (
            {"operation": "detect_fluid", "fluid_class": "RC"},
            {"operation": "detect_fluid", "fluid_class": "RC"},
        ),
        ({"operation": "plunger_down"}, {"operation": "plunger_down"}),
    ],
)
def test_pipette_plan_forwards_only_selected_operation_fields(monkeypatch, payload, forwarded):
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses["pipette_application_plan"]["operation"] = payload["operation"]
    runtime.connection.client.responses["pipette_application_plan"]["requested_inputs"] = (
        {"direction": payload["operation"].removeprefix("plunger_")}
        if payload["operation"].startswith("plunger_")
        else {key: value for key, value in forwarded.items() if key != "operation"}
    )

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/application/plan",
        json=payload,
    )

    assert response.status_code == 200
    assert runtime.connection.client.calls == [
        ("pipette_application_plan", {"json_data": forwarded}),
    ]


def test_strict_x_dashboard_rejects_unknown_nested_authority_keys():
    nested_paths = [
        "provider.lifecycle",
        "provider.live_status",
        "provider.switch_masks",
        "provider.profile",
        "provider.reference",
        "snapshot_freshness",
        "latest_receipt",
    ]
    from services.bioxp.operator_models import OperatorDashboard
    from pydantic import ValidationError
    for label in nested_paths:
        candidate = catalog()["dashboard"]
        current = candidate["x_axis"]
        if label.startswith("provider."):
            current = current["provider"]
            field = label.split(".", 1)[1]
            current = current[field]
        else:
            current = current[label]
        current["unexpected"] = True
        with pytest.raises(ValidationError):
            OperatorDashboard.model_validate(candidate)


@pytest.mark.parametrize(
    ("container_path", "field", "value"),
    [
        (("provider", "lifecycle"), "prepared_receipt", {
            "ok": True,
            "observed_generation": 7,
            "board_lifecycle_generation": 11,
            "board_preparation_verified": True,
            "initialize_without_motion_verified": True,
            "physical_motion": False,
            "motor_output_state": "unknown",
            "motor_torque_verified": False,
            "receipt": None,
            "axis": "x",
            "source_anchor": "locked OEM source",
            "source_exact": True,
            "literal_switch_mask_writes": [],
            "unexpected_authority": "must fail closed",
        }),
        (("provider", "lifecycle"), "active_receipt", {
            "command_id": "x-command-1",
            "intent": "move_absolute",
            "idempotency_key": "x-command-1",
            "generation": 7,
            "inputs": {"position_steps": 6000},
            "status": "executing",
            "result": None,
            "unexpected_authority": "must fail closed",
        }),
        (("provider", "lifecycle"), "pending_ticket", {
            "ok": True,
            "axis": "x",
            "source_mode": "provider.x.move_absolute",
            "requested_position_steps": 6000,
            "target_position_steps": 6000,
            "before": {"board": 5, "param": 1, "motor": 0, "ack": {"status": 100, "value": 1000}, "value": 1000},
            "before_position_steps": 1000,
            "preflight": {"profile": {"board": 5}, "profile_receipt": None, "switch_masks": {}, "expected_switch_masks": {"12": 1, "13": 0}},
            "command_issued": True,
            "source_noop": False,
            "physical_motion_commanded": True,
            "controller_command_acknowledged": True,
            "event_window": {"after_sequence": 10},
            "move": {"ok": True, "ack": {"status": 100, "value": 0}, "board": 5, "motor": 0, "position": 6000},
            "pending_motion": True,
            "physical_motion": True,
            "reference_before": {"ok": True, "durable_clean": True, "authority_untrusted": False, "rows": {"x": {"state": "referenced"}}},
            "unexpected_authority": "must fail closed",
        }),
        (("provider", "live_status"), "readbacks", {1: {
            "board": 5,
            "param": 1,
            "motor": 0,
            "ack": {"status": 100, "value": 1000},
            "value": 1000,
            "unexpected_authority": "must fail closed",
        }}),
    ],
)
def test_operator_contract_rejects_unknown_fields_in_each_nested_x_authority_payload(container_path, field, value):
    from services.bioxp.operator_models import OperatorDashboardXAxis
    from pydantic import ValidationError

    payload = catalog()["dashboard"]["x_axis"]
    cursor = payload
    for key in container_path:
        cursor = cursor[key]
    cursor[field] = value

    with pytest.raises(ValidationError):
        OperatorDashboardXAxis.model_validate(payload)


def _exact_tmcl_provenance():
    return {
        "transaction_id": None,
        "owner_generation": None,
        "ok": True,
        "outcome": "completion",
        "matcher": "tmcl:5:6",
        "registration_timestamp": 100.0,
        "tx_timestamp": 100.1,
        "tx_write_completed_at": 100.2,
        "timeout_ms": 1260,
        "tx_raw": [126, 0, 0, 0, 5, 8, 6, 1, 0, 0, 0, 0, 20, 126],
        "command_family": "tmcl",
        "tx_id": 5,
        "tx_dlc": 8,
        "expected_board": 5,
        "expected_command": 6,
        "receive_timestamp": 100.3,
        "frames": [],
        "skipped_frames": [],
        "skipped_count": 0,
        "skipped_frames_truncated": False,
        "ack_received": False,
        "completion_received": True,
        "multipart_received": False,
        "observed_status": 100,
        "observed_rx_id": 5,
        "observed_rx_dlc": 8,
        "observed_rx_raw": [126, 0, 5, 8, 5, 100, 6, 0, 0, 0, 123, 0, 239, 126],
    }


def _exact_x_register_readback(*, param=1, value=123):
    return {
        "board": 5,
        "param": param,
        "motor": 0,
        "ack": {
            "status": 100,
            "status_str": "OK",
            "board": 5,
            "cmd": 6,
            "value": value,
            "raw": [5, 100, 6, 0, 0, 0, value, 0],
            "provenance": _exact_tmcl_provenance(),
        },
        "value": value,
    }


def _exact_x_reference_success():
    return {
        "ok": True,
        "axes": ["x"],
        "rows": {"x": {
            "axis": "x",
            "state": "referenced",
            "origin_position_steps": 0,
            "source": "serial206.x.operator_observation",
            "note": None,
            "updated_at": "2026-08-12T00:00:00+00:00",
            "last_motion_kind": "home",
        }},
        "persisted": True,
        "verified": True,
        "durable_clean": True,
        "authority_untrusted": False,
    }


def _exact_x_event_window():
    return {
        "after_sequence": 10,
        "cleared": 0,
        "router_cleared": {"valid_async": 0, "unknown_async": 0},
        "dispatch_cursors": {"5:0": 100.2},
        "dispatch_cursor": 100.2,
    }


def _exact_x_raw_profile():
    return {
        "label": "X",
        "board": 5,
        "motor": 0,
        "speed": 1700,
        "acc": 350,
        "run_current": 31,
        "standby_current": 10,
        "stall_guard": 16,
        "warm_enable": True,
        "axis_min_steps": 0,
        "axis_max_steps": 90263,
    }


def _exact_x_profile_receipt():
    return {
        "ok": True,
        "source": "initializeMotorsWithoutMotion",
        "axis": "x",
        "board": 5,
        "motor": 0,
        "board_lifecycle_generation": 3,
        "profile_fingerprint": {
            "board": 5,
            "motor": 0,
            "speed": 1700,
            "acceleration": 350,
            "current": 31,
            "stall_threshold": 16,
        },
        "readbacks": {
            str(param): _exact_x_register_readback(param=param, value=value)
            for param, value in {4: 1700, 5: 350, 6: 31, 205: 16}.items()
        },
    }


def _exact_x_preflight():
    return {
        "profile": _exact_x_raw_profile(),
        "profile_receipt": _exact_x_profile_receipt(),
        "switch_masks": {
            "12": _exact_x_register_readback(param=12, value=1),
            "13": _exact_x_register_readback(param=13, value=0),
        },
        "expected_switch_masks": {"12": 1, "13": 0},
    }


def _exact_x_position_readback(*, position=123):
    return {
        "board": 5,
        "motor": 0,
        "ack": _exact_x_register_readback(param=1, value=position)["ack"],
        "position": position,
        "ok": True,
    }


def _exact_x_move_receipt(*, position=6000):
    return {
        "ok": True,
        "ack": {
            "status": 100,
            "status_str": "OK",
            "board": 5,
            "cmd": 4,
            "value": position,
            "raw": [5, 100, 4, 0, 0, 23, 112, 0],
            "provenance": _exact_tmcl_provenance(),
        },
        "board": 5,
        "motor": 0,
        "position": position,
        "source_noop": False,
        "event_window": _exact_x_event_window(),
    }


def _exact_x_parameter_write(*, value: int, param: int = 5):
    readback = _exact_x_register_readback(param=param, value=value)
    return {
        "board": 5,
        "param": param,
        "motor": 0,
        "set_value": value,
        "ack": readback["ack"],
        "readback": readback,
        "ok": True,
    }


def _exact_x_pending_ticket():
    return {
        "ok": True,
        "axis": "x",
        "source_mode": "provider.x.move_absolute",
        "requested_position_steps": 6000,
        "target_position_steps": 6000,
        "before": _exact_x_position_readback(),
        "before_position_steps": 123,
        "preflight": _exact_x_preflight(),
        "command_issued": True,
        "source_noop": False,
        "physical_motion_commanded": True,
        "controller_command_acknowledged": True,
        "event_window": _exact_x_event_window(),
        "move": _exact_x_move_receipt(),
        "pending_motion": True,
        "physical_motion": True,
        "reference_before": _exact_x_reference_success(),
        "acceleration_set": None,
        "acceleration_restore": None,
        "acceleration_restore_verified": None,
        "failure": None,
    }


def _exact_x_lifecycle(**updates):
    value = {
        "schema_version": "bioxp.serial206_x_lifecycle.v2",
        "state": "referenced_ready",
        "generation": 7,
        "board_lifecycle_generation": 3,
        "reference_state": "referenced",
        "prepared_receipt": None,
        "active_receipt": None,
        "pending_ticket": None,
        "awaiting_observation_receipt_id": None,
        "terminal_state": None,
        "last_failure": None,
        "receipt_storage": "robot_sqlite",
        "receipt_detail_on_request": True,
        "recent_receipt_count": 0,
        "latest_receipt": None,
    }
    value.update(updates)
    return value


def test_x_dashboard_accepts_exact_robot_lifecycle_reference_and_tmcl_projection_shapes_after_json_transport():
    from services.bioxp.operator_models import OperatorDashboardXAxis

    payload = catalog()["dashboard"]["x_axis"]
    payload["provider"]["lifecycle"].update({
        "schema_version": "bioxp.serial206_x_lifecycle.v2",
        "generation": 7,
        "board_lifecycle_generation": 3,
        "prepared_receipt": None,
        "active_receipt": None,
        "pending_ticket": None,
        "awaiting_observation_receipt_id": None,
        "terminal_state": None,
        "receipt_storage": "robot_sqlite",
        "receipt_detail_on_request": True,
        "recent_receipt_count": 1,
    })
    payload["provider"]["live_status"].update({
        "ok": True,
        "axis": "x",
        "board": 5,
        "motor": 0,
        "expected_profile": {"4": 1700, "5": 350, "6": 31, "205": 16},
        "switch_mask_tuple": {"12": 1, "13": 0},
        "expected_switch_masks": {"12": 1, "13": 0},
        "readbacks": {
            str(param): _exact_x_register_readback(param=param, value=value)
            for param, value in {1: 123, 3: 0, 4: 1700, 5: 350, 6: 31, 9: 0, 10: 1, 12: 1, 13: 0, 205: 16}.items()
        },
    })
    payload["provider"]["switch_masks"] = {"expected": {"12": 1, "13": 0}, "verified": True}
    payload["provider"]["reference"] = {
        "ok": True,
        "persisted": True,
        "verified": True,
        "durable_clean": True,
        "authority_untrusted": False,
        "axes": ["x"],
        "rows": {"x": {
            "axis": "x",
            "state": "referenced",
            "origin_position_steps": 0,
            "source": "serial206.x.operator_observation",
            "note": None,
            "updated_at": "2026-08-12T00:00:00+00:00",
            "last_motion_kind": "home",
        }},
    }

    validated = OperatorDashboardXAxis.model_validate_json(OperatorDashboardXAxis.model_validate(payload).model_dump_json())
    assert validated.provider.lifecycle is not None
    assert validated.provider.reference is not None
    assert validated.provider.live_status is not None
    assert validated.provider.live_status.readbacks[1].ack is not None

    assert validated.provider.lifecycle.schema_version == "bioxp.serial206_x_lifecycle.v2"
    assert validated.provider.reference.axes == ["x"]
    assert validated.provider.reference.rows["x"].state == "referenced"
    assert validated.provider.live_status.readbacks[1].ack.cmd == 6


@pytest.mark.parametrize("last_failure", [
    "interrupted_x_transaction_outcome_ambiguous",
    "operator_rejected_x_home",
    "xy_restart_or_reentry_during_executing",
    "homexy_intent_exception:RuntimeError:boom",
    {
        "failure": "x_board_lifecycle_generation_changed",
        "recorded_generation": 7,
        "current_generation": 7,
        "recorded_board_lifecycle_generation": 3,
        "current_board_lifecycle_generation": 4,
    },
    {
        "ok": False,
        "error": "durable_reference_path_required",
        "axis": "x",
        "state": "unknown",
        "origin_position_steps": None,
        "source": None,
        "note": None,
        "updated_at": None,
        "last_motion_kind": None,
        "persisted": False,
        "verified": False,
        "durable_clean": False,
    },
])
def test_x_dashboard_accepts_exact_robot_string_and_structured_failure_variants(last_failure):
    from services.bioxp.operator_models import OperatorDashboardXAxis

    payload = catalog()["dashboard"]["x_axis"]
    payload["provider"]["lifecycle"]["last_failure"] = last_failure
    assert OperatorDashboardXAxis.model_validate(payload).provider.lifecycle.last_failure is not None


def test_x_preparation_stage_rejects_cross_stage_authority_evidence():
    from pydantic import ValidationError
    from services.bioxp.operator_models import OperatorDashboardXPreparationStage

    authority = {
        "machine_serial": 206,
        "acquisition_id": "serial206-acquisition",
        "evidence_lock_sha256": "a" * 64,
        "mutation_authorized": True,
        "component_source": "serial-206 ClassControlInterface motor construction and m_AxisIODesignater",
    }
    stage = {
        "stage_id": "authority",
        "status": "passed",
        "source_anchor": "immutable serial-206 OEM evidence lock",
        "controller_evidence": authority,
        "physical_motion": False,
    }
    assert isinstance(OperatorDashboardXPreparationStage.model_validate(stage).controller_evidence, object)

    stage["stage_id"] = "rail_24v_readback"
    projected = OperatorDashboardXPreparationStage.model_validate(stage)
    from services.bioxp.operator_models import OperatorDashboardXJsonSafeEvidence
    assert isinstance(projected.controller_evidence, OperatorDashboardXJsonSafeEvidence)


def _exact_x_preparation_evidence():
    return {
        "schema_version": "bioxp.oem_prepare_without_motion.v2",
        "ok": True,
        "state": "completed",
        "machine_serial": 206,
        "controller_evidence": {
            "machine_serial": 206,
            "acquisition_id": "serial206-acquisition",
            "evidence_lock_sha256": "a" * 64,
            "mutation_authorized": True,
            "component_source": "serial-206 ClassControlInterface motor construction and m_AxisIODesignater",
        },
        "stage_ledger": [],
        "stage_receipts": [],
        "board_lifecycle_generation": 3,
        "physical_motion": False,
        "physical_motion_commanded": False,
        "homing_performed": False,
        "motor_output_state": "unknown",
        "motor_torque_verified": False,
        "global_24v_switch_claimed": False,
    }


@pytest.mark.parametrize(
    ("field_path", "valid_value"),
    [
        (("prepared_receipt", "receipt"), _exact_x_preparation_evidence()),
        (("active_receipt", "inputs"), {"command_id": "x-command", "idempotency_key": "x-command", "expected_generation": 7, "position_steps": 6000, "wait_for_stop": False, "wait_timeout_s": 20.0}),
        (("pending_ticket", "before"), _exact_x_position_readback()),
        (("pending_ticket", "preflight"), _exact_x_preflight()),
        (("pending_ticket", "event_window"), _exact_x_event_window()),
        (("pending_ticket", "move"), _exact_x_move_receipt()),
        (("pending_ticket", "reference_before"), _exact_x_reference_success()),
        (("pending_ticket", "acceleration_set"), {"board": 5, "param": 5, "motor": 0, "set_value": 350, "ack": {"status": 100, "status_str": "OK", "board": 5, "cmd": 5, "value": 350, "raw": [5, 100, 5, 0, 0, 1, 94, 0], "provenance": _exact_tmcl_provenance()}, "readback": _exact_x_register_readback(param=5, value=350), "ok": True}),
    ],
)
def test_x_dashboard_rejects_unknown_keys_at_each_authority_evidence_leaf(field_path, valid_value):
    from copy import deepcopy
    from pydantic import ValidationError
    from services.bioxp.operator_models import OperatorDashboardXLifecycle

    lifecycle = _exact_x_lifecycle(state="executing")
    if field_path[0] == "prepared_receipt":
        lifecycle["state"] = "prepared_unreferenced"
        lifecycle["prepared_receipt"] = {"ok": True, "observed_generation": 7, "board_lifecycle_generation": 3, "board_preparation_verified": True, "initialize_without_motion_verified": True, "physical_motion": False, "motor_output_state": "unknown", "motor_torque_verified": False, "receipt": valid_value, "axis": "x", "source_anchor": "ClassControlInterface.initializeMotorsWithoutMotion:3187-3195", "source_exact": True, "literal_switch_mask_writes": []}
    elif field_path[0] == "active_receipt":
        lifecycle["active_receipt"] = {"command_id": "x-command", "intent": "move_absolute", "idempotency_key": "x-command", "generation": 7, "inputs": valid_value, "status": "executing", "result": None}
    else:
        ticket = _exact_x_pending_ticket()
        ticket[field_path[1]] = valid_value
        if field_path[1] == "acceleration_set":
            ticket["acceleration_restore"] = {**valid_value, "set_value": 350}
            ticket["acceleration_restore_verified"] = True
        lifecycle["pending_ticket"] = ticket
        lifecycle["active_receipt"] = {"command_id": "x-command", "intent": "move_absolute", "status": "executing", "result": ticket}

    valid = OperatorDashboardXLifecycle.model_validate(lifecycle)
    target = deepcopy(valid.model_dump(mode="python"))
    cursor = target[field_path[0]][field_path[1]]
    cursor["unexpected_authority"] = True
    with pytest.raises(ValidationError):
        OperatorDashboardXLifecycle.model_validate(target)


def test_x_authority_rejects_invented_states_axes_registers_and_event_addresses():
    from pydantic import ValidationError
    from services.bioxp.operator_models import (
        OperatorDashboardXEventWindow,
        OperatorDashboardXLifecycle,
        OperatorDashboardXLiveStatus,
        OperatorDashboardXActiveReceipt,
        OperatorDashboardXParameterWrite,
        OperatorDashboardXPreflight,
        OperatorDashboardXPreflightProfile,
        OperatorDashboardXProfile,
        OperatorDashboardXProfileFingerprint,
        OperatorDashboardXProfileReceipt,
        OperatorDashboardXProvider,
        OperatorDashboardXReference,
        OperatorDashboardXRegisterReadback,
        OperatorDashboardXSwitchMasks,
    )

    cases = [
        (OperatorDashboardXLifecycle, {"schema_version": "bioxp.serial206_x_lifecycle.v2", "state": "invented_state", "reference_state": "unknown"}),
        (OperatorDashboardXLifecycle, {"schema_version": "bioxp.serial206_x_lifecycle.v2", "state": "unprepared", "reference_state": "invented_reference"}),
        (OperatorDashboardXReference, {"ok": True, "axes": ["y"], "rows": {"y": {"axis": "y", "state": "referenced"}}}),
        (OperatorDashboardXReference, {"ok": True, "axes": ["x"], "rows": {"x": {"axis": "x", "state": "invented_reference"}}}),
        (OperatorDashboardXLiveStatus, {"expected_profile": {"999": 1}}),
        (OperatorDashboardXLiveStatus, {"switch_mask_tuple": {"999": 1}}),
        (OperatorDashboardXLiveStatus, {"expected_switch_masks": {"999": 1}}),
        (OperatorDashboardXLiveStatus, {"readbacks": {"999": _exact_x_register_readback(param=999, value=1)}}),
        (OperatorDashboardXPreflight, {"profile": {"board": 5, "motor": 0, "speed": 1700, "acc": 350, "run_current": 31, "stall_guard": 16, "axis_min_steps": 0, "axis_max_steps": 90263, "disable_right": True, "disable_left": False}, "switch_masks": {"999": _exact_x_register_readback(param=999, value=1)}, "expected_switch_masks": {"12": 1, "13": 0}}),
        (OperatorDashboardXProfileReceipt, {"ok": True, "source": "initializeMotorsWithoutMotion", "axis": "x", "board": 5, "motor": 0, "profile_fingerprint": {"board": 5, "motor": 0}, "readbacks": {"999": _exact_x_register_readback(param=999, value=1)}}),
        (OperatorDashboardXSwitchMasks, {"expected": {"999": 1}, "verified": False}),
        (OperatorDashboardXProfile, {"expected": {"999": 1}, "verified": False}),
        (OperatorDashboardXEventWindow, {"after_sequence": 1, "router_cleared": {"invented": 0}}),
        (OperatorDashboardXEventWindow, {"after_sequence": 1, "dispatch_cursors": {"999:9": 1.0}}),
        (OperatorDashboardXEventWindow, {"after_sequence": -1}),
        (OperatorDashboardXRegisterReadback, {"board": 4, "param": 1, "motor": 0, "value": 0}),
        (OperatorDashboardXRegisterReadback, {"board": 5, "param": 1, "motor": 1, "value": 0}),
        (OperatorDashboardXProfileFingerprint, {"board": 4, "motor": 0}),
        (OperatorDashboardXPreflightProfile, {"board": 5, "motor": 0, "speed": 1700, "acc": 350, "run_current": 31, "stall_guard": 16, "axis_min_steps": 0, "axis_max_steps": 99999, "disable_right": True, "disable_left": False}),
        (OperatorDashboardXProfileReceipt, {"ok": True, "source": "invented", "axis": "x", "board": 5, "motor": 0, "profile_fingerprint": {"board": 5, "motor": 0}, "readbacks": {}}),
        (OperatorDashboardXProvider, {"authority": "Serial206OemInitializationProvider", "axis": "x", "board": 4, "motor": 0, "bound": True, "physical_position_verified": False}),
        (OperatorDashboardXActiveReceipt, {"command_id": "x-command", "intent": "move_absolute", "status": "invented", "inputs": {}}),
        (OperatorDashboardXParameterWrite, {"board": 5, "param": 4, "motor": 0, "set_value": 1, "ok": True}),
        (OperatorDashboardXParameterWrite, {"board": 5, "param": 5, "motor": 0, "set_value": 350, "readback": _exact_x_register_readback(param=4, value=350), "ok": True}),
    ]
    for model, payload in cases:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_x_lifecycle_last_failure_rejects_invented_and_partial_families():
    from pydantic import ValidationError
    from services.bioxp.operator_models import OperatorDashboardXLifecycleLastFailure

    valid = [
        "restart_or_reentry_during_executing",
        {
            "failure": "x_generation_changed",
            "recorded_generation": 6,
            "current_generation": 7,
            "recorded_board_lifecycle_generation": 2,
            "current_board_lifecycle_generation": 3,
        },
        {
            "ok": False,
            "observed_generation": 7,
            "physical_motion": False,
            "blocker": "ownership_generation_changed_before_preparation",
            "axis": "x",
            "source_anchor": "ClassControlInterface.initializeMotorsWithoutMotion:3187-3195",
            "source_exact": True,
            "literal_switch_mask_writes": [],
        },
        {
            "ok": False,
            "error": "reference store not bound",
            "axis": "x",
            "state": "unknown",
            "origin_position_steps": None,
            "source": None,
            "note": None,
            "updated_at": None,
            "last_motion_kind": None,
            "persisted": False,
            "verified": False,
            "durable_clean": False,
        },
        {"ok": False, "failure": "x_result_not_mapping"},
        {
            "ok": False,
            "failure": "x_generation_changed_during_command",
            "command_issued": True,
            "recorded_generation": 6,
            "current_generation": 7,
            "primitive_result": _exact_x_pending_ticket(),
        },
        {
            "ok": False,
            "failure": "x_position_before_unavailable",
            "physical_motion_commanded": False,
            "before": _exact_x_position_readback(),
        },
        {
            "ok": False,
            "failure": "xy_interrupted_or_generation_changed",
            "primitive_result": {"omitted": "item_limit"},
        },
        {
            "ok": False,
            "failure": "homexy_result_not_mapping",
            "live_preflight": _exact_x_preflight(),
        },
        {
            "reason": "board5_lifecycle_change",
            "transition": "activated",
            "command64_value": 1,
            "previous_state": "referenced_ready",
            "ack": None,
            "invalidated_at": 100.0,
            "reference_invalidation": None,
        },
    ]
    for value in valid:
        OperatorDashboardXLifecycleLastFailure.model_validate(value)

    from services.bioxp.operator_models import (
        OperatorDashboardXYReferenceAuthorityEffect,
        OperatorDashboardXYSingleReferenceMutationSuccess,
    )
    y_only_reference = {
        "axis": "y", "state": "referenced", "origin_position_steps": 4321,
        "source": "serial206.move_xy", "note": None,
        "updated_at": "2026-08-13T00:00:00+00:00", "last_motion_kind": "move_xy",
        "ok": True, "persisted": True, "verified": True, "durable_clean": True,
    }
    y_effect = OperatorDashboardXYReferenceAuthorityEffect.model_validate(y_only_reference)
    assert isinstance(y_effect.root, OperatorDashboardXYSingleReferenceMutationSuccess)
    assert y_effect.root.axis == "y"

    invalid = [
        "invented_x_failure",
        {"failure": "x_generation_changed", "current_generation": 7},
        {"ok": False, "failure": "x_result_not_mapping", "invented": True},
        {"ok": False, "failure": "enableXY_result_not_mapping"},
        {"reason": "board5_lifecycle_change", "transition": "activated"},
        {"ok": True},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            OperatorDashboardXLifecycleLastFailure.model_validate(value)


def test_x_safety_interrupt_receipt_binds_intent_inputs_result_and_status():
    from pydantic import ValidationError
    from services.bioxp.operator_models import OperatorDashboardXSafetyInterruptReceipt

    stop_result = {
        "ok": False,
        "axis": "x",
        "intent": "stop",
        "stop": {"board": 5, "motor": 0, "ack": None, "first_delivery": None, "second_delivery": None, "oem_double_stop": True, "ok": False},
        "wait": {"stopped": False, "elapsed_ms": 10, "polls": 1, "last_speed": None, "seen_nonzero": False, "target_position": None, "target_reached": False, "last_position": None, "ambiguous_no_motion": False, "last_ack": None},
        "controller_command_acknowledged": False,
        "controller_terminal_state_verified": False,
        "physical_motion": False,
        "physical_effect_verified": False,
        "failure": "x_stop_not_verified",
        "interrupt_epoch": 4,
        "interrupted_command_ids": ["moving-x"],
    }
    base = {
        "command_id": "stop-x",
        "receipt_id": "stop-x:1",
        "intent": "stop",
        "idempotency_key": "stop-key",
        "idempotency_replay_enabled": False,
        "generation": 7,
        "inputs": {"command_id": "stop-x", "idempotency_key": "stop-key", "expected_generation": 7, "timeout_s": 3.0},
        "status": "failed",
        "started_at": 1.0,
        "finished_at": 2.0,
        "interrupt_epoch": 4,
        "interrupted_command_ids": ["moving-x"],
        "result": stop_result,
    }
    OperatorDashboardXSafetyInterruptReceipt.model_validate(base)

    exception = dict(base)
    exception["result"] = {"ok": False, "error": "RuntimeError: failed", "interrupt_epoch": 4, "interrupted_command_ids": ["moving-x"]}
    OperatorDashboardXSafetyInterruptReceipt.model_validate(exception)

    cross_intent = dict(base)
    cross_intent["intent"] = "abort"
    with pytest.raises(ValidationError):
        OperatorDashboardXSafetyInterruptReceipt.model_validate(cross_intent)

    mismatched_status = dict(base)
    mismatched_status["status"] = "completed"
    with pytest.raises(ValidationError):
        OperatorDashboardXSafetyInterruptReceipt.model_validate(mismatched_status)


def test_x_preparation_and_reference_success_reject_invented_authority_claims():
    from pydantic import ValidationError
    from services.bioxp.operator_models import (
        OperatorDashboardXPreparationEvidence,
        OperatorDashboardXReferenceSuccess,
    )

    preparation = _exact_x_preparation_evidence()
    for field, value in (("state", "invented"), ("motor_output_state", "invented")):
        malformed = copy.deepcopy(preparation)
        malformed[field] = value
        with pytest.raises(ValidationError):
            OperatorDashboardXPreparationEvidence.model_validate(malformed)

    reference = _exact_x_reference_success()
    for field in ("persisted", "verified", "durable_clean"):
        malformed = copy.deepcopy(reference)
        malformed[field] = False
        with pytest.raises(ValidationError):
            OperatorDashboardXReferenceSuccess.model_validate(malformed)


def test_x_failure_branch_validators_reject_sparse_cross_branch_authority():
    from pydantic import ValidationError
    from services.bioxp.operator_models import (
        OperatorDashboardXBoardLifecycleInvalidation,
        OperatorDashboardXFailure,
        OperatorDashboardXIssuedMoveFailure,
        OperatorDashboardXMoveXYFailure,
        OperatorDashboardXRelativeMoveFailure,
        OperatorDashboardXSwitchReconciliationFailure,
    )

    invalid = [
        (OperatorDashboardXFailure, {}),
        (OperatorDashboardXFailure, {"current_generation": 8}),
        (OperatorDashboardXBoardLifecycleInvalidation, {
            "reason": "board5_lifecycle_change", "transition": "activated", "command64_value": 0,
            "previous_state": "cold", "ack": None, "invalidated_at": 1,
            "reference_invalidation": None,
        }),
        (OperatorDashboardXMoveXYFailure, {
            "ok": False, "source_operation": "ClassControlInterface.moveXY",
            "source_anchor": "ClassControlInterface.cs:4285-4367",
            "requested": {"x": 1, "y": 2}, "board_present": {"x": True, "y": True},
            "ignored_compatibility_inputs": {}, "oem_wait_timeout_ms": 5000,
        }),
        (OperatorDashboardXMoveXYFailure, {
            "ok": False, "source_operation": "ClassControlInterface.moveXY",
            "source_anchor": "ClassControlInterface.cs:4285-4367",
            "requested": {"x": 1, "y": 2}, "board_present": {"x": True, "y": True},
            "ignored_compatibility_inputs": {}, "oem_wait_timeout_ms": 5000,
            "branch": "source_noop", "failure": "parallel_wait_not_verified",
            "launch_order": ["x", "y"], "pair_wait": {},
        }),
    ]
    for model, payload in invalid:
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    base = _exact_x_pending_ticket()
    relative_restore = {
        "ok": False, "axis": "x", "intent": "move_steps",
        "source_mode": "ClassControlInterface.moveSteps", "source_noop": False,
        "requested_steps": 100, "target_position_steps": 6100,
        "before": base["before"], "before_position_steps": 6000,
        "preflight": base["preflight"], "event_window": base["event_window"],
        "move": base["move"], "command_issued": True, "physical_motion_commanded": True,
        "controller_command_acknowledged": True, "failure": "x_acceleration_restore_failed",
        "after": base["before"], "after_position_steps": 6100,
        "terminal_speed": {"board": 5, "motor": 0, "ack": None, "speed": 0, "ok": True},
        "target_event_128_observed": True, "controller_terminal_state_verified": True,
        "physical_effect_verified": False, "reference_before": base["reference_before"],
        "physical_motion": True, "wait": {"omitted": "item_limit"}, "wait_verified": True, "events": [],
        "controller_error_events": [], "target_events": [], "target_event_128_verified": True,
        "target_position_verified": True,
        "acceleration_set": base["acceleration_set"] or _exact_x_parameter_write(value=350),
        "acceleration_restore": _exact_x_parameter_write(value=350),
        "acceleration_restore_verified": False,
    }
    OperatorDashboardXRelativeMoveFailure.model_validate(relative_restore)


def test_catalog_is_robot_owned_and_strict(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.get("/api/bioxp/operator-controls/catalog")
    assert response.status_code == 200
    assert response.json()["actions"][0]["action_id"] == "motion.home_xy"
    assert response.json()["actions"][0]["inputs"][0]["exclusive_minimum"] == 0.1
    assert runtime.connection.client.calls == [("operator_control_catalog", {})]


def test_unavailable_source_authority_is_explicit_and_strictly_accepted(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["operator_control_catalog"].update({
        "registry_sha256": "unavailable",
        "evidence_lock_sha256": "unavailable",
        "source_authority_verified": False,
    })
    response = client.get("/api/bioxp/operator-controls/catalog")
    assert response.status_code == 200, response.text
    assert response.json()["registry_sha256"] == "unavailable"
    assert response.json()["source_authority_verified"] is False


def test_dashboard_and_input_admission_are_robot_owned(monkeypatch):
    client, runtime = make_client(monkeypatch)
    dashboard = client.get("/api/bioxp/operator-controls/dashboard")
    admission = client.post("/api/bioxp/operator-controls/actions/motion.home_xy/admission", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "inputs": {},
    })
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["motion"]["enabled"] is False
    assert dashboard.json()["axes"][0]["left_switch_raw_active"] is False
    assert dashboard.json()["axes"][0]["right_switch_raw_active"] is True
    assert dashboard.json()["axes"][0]["motor_temperature_available"] is False
    assert dashboard.json()["x_axis"]["provider"]["live_status"]["max_speed"] == 1700
    assert dashboard.json()["x_axis"]["provider"]["board_generation_fresh"] is True
    assert dashboard.json()["x_axis"]["physical_position_verified"] is False
    assert dashboard.json()["temperatures"][0]["label"] == "Thermal cycler block"
    assert dashboard.json()["temperatures"][0]["unit"] == "°C"
    assert admission.status_code == 200, admission.text
    assert admission.json()["disabled_reason"] == "Motion is inactive. Activate motion before moving this motor."
    assert runtime.connection.client.calls == [
        ("operator_dashboard", {}),
        ("operator_control_catalog", {}),
        ("operator_action_admission", {"path_params": {"action_id": "motion.home_xy"}, "json_data": {"expected_generation": 7, "inputs": {}}}),
    ]


def test_one_invocation_maps_to_one_action_id_not_a_browser_path(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.post("/api/bioxp/operator-controls/actions/motion.home_xy", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "invoke-12345678",
        "inputs": {},
    })
    assert response.status_code == 200
    assert response.json()["physical_effect_verified"] is False
    assert runtime.connection.client.calls == [
        (
            "invoke_operator_action",
        {
            "path_params": {"action_id": "motion.home_xy"},
            "json_data": {
                "expected_generation": 7,
                "idempotency_key": "invoke-12345678",
                "inputs": {},
            },
        },
    )]


def test_z_stop_invocation_skips_catalog_preflight_but_keeps_generation_contract(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action"] = receipt(
        action_id="oem.z.stop",
        key="z-stop-12345678",
        command_id="z-stop-command-1",
    )

    response = client.post("/api/bioxp/operator-controls/actions/oem.z.stop", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "z-stop-12345678",
        "inputs": {},
    })

    assert response.status_code == 200, response.text
    assert response.json()["action_id"] == "oem.z.stop"
    assert runtime.connection.safety_interrupt_calls == [(
        "invoke_operator_action",
        {
            "path_params": {"action_id": "oem.z.stop"},
            "json_data": {
                "expected_generation": 7,
                "idempotency_key": "z-stop-12345678",
                "inputs": {},
            },
        },
    )]
    assert runtime.connection.client.calls == [(
        "invoke_operator_action",
        {
            "path_params": {"action_id": "oem.z.stop"},
            "json_data": {
                "expected_generation": 7,
                "idempotency_key": "z-stop-12345678",
                "inputs": {},
            },
        },
    )]

    runtime.connection.client.calls.clear()
    stale = client.post("/api/bioxp/operator-controls/actions/oem.z.stop", json={
        "expected_connection_generation": 999,
        "expected_ownership_generation": 7,
        "idempotency_key": "z-stop-stale-12345678",
        "inputs": {},
    })
    assert stale.status_code == 409
    assert "connection generation changed" in stale.json()["detail"].lower()
    assert runtime.connection.client.calls == []


def test_z_abort_invocation_uses_independent_interrupt_lane(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action"] = receipt(
        action_id="oem.z.abort",
        key="z-abort-12345678",
        command_id="z-abort-command-1",
    )

    response = client.post("/api/bioxp/operator-controls/actions/oem.z.abort", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "z-abort-12345678",
        "inputs": {},
    })

    assert response.status_code == 200, response.text
    assert response.json()["action_id"] == "oem.z.abort"
    assert runtime.connection.safety_interrupt_calls[0][1]["path_params"] == {"action_id": "oem.z.abort"}


def test_x_stop_and_abort_use_independent_interrupt_lane(monkeypatch):
    for action_id in ("oem.x.stop", "oem.abort_all"):
        client, runtime = make_client(monkeypatch)
        key = f"{action_id.rsplit('.', 1)[-1]}-x-12345678"
        runtime.connection.client.responses["invoke_operator_action"] = receipt(
            action_id=action_id,
            key=key,
            command_id=f"{action_id}-command-1",
        )
        response = client.post(f"/api/bioxp/operator-controls/actions/{action_id}", json={
            "expected_connection_generation": 77,
            "expected_ownership_generation": 7,
            "idempotency_key": key,
            "inputs": {},
        })
        assert response.status_code == 200, response.text
        assert response.json()["action_id"] == action_id
        assert runtime.connection.safety_interrupt_calls == [(
            "invoke_operator_action",
            {
                "path_params": {"action_id": action_id},
                "json_data": {
                    "expected_generation": 7,
                    "idempotency_key": key,
                    "inputs": {},
                },
            },
        )]
        assert runtime.connection.client.calls[-1][1]["path_params"] == {"action_id": action_id}


def test_receipt_identity_mismatch_fails_closed(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action"] = receipt(action_id="motion.home_z")
    response = client.post("/api/bioxp/operator-controls/actions/motion.home_xy", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "invoke-12345678",
        "inputs": {},
    })
    assert response.status_code == 502


def test_mutation_gate_blocks_action_and_assessment(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    admission = client.post("/api/bioxp/operator-controls/actions/motion.home_xy/admission", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "inputs": {},
    })
    invoke = client.post("/api/bioxp/operator-controls/actions/motion.home_xy", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "invoke-12345678",
        "inputs": {},
    })
    assess = client.post("/api/bioxp/operator-controls/receipts/cmd-1/assessment", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "assess-12345678",
        "verdict": "pass",
        "note": "Observed X/Y references.",
    })
    assert admission.status_code == 503
    assert invoke.status_code == 503
    assert assess.status_code == 503
    assert runtime.connection.client.calls == []


def test_operator_requests_require_both_generation_domains(monkeypatch):
    client, runtime = make_client(monkeypatch)
    missing_ownership = client.post("/api/bioxp/operator-controls/actions/motion.home_xy/admission", json={
        "expected_connection_generation": 77,
        "inputs": {},
    })
    missing_connection = client.post("/api/bioxp/operator-controls/actions/motion.home_xy/admission", json={
        "expected_ownership_generation": 7,
        "inputs": {},
    })
    assert missing_ownership.status_code == 422
    assert missing_connection.status_code == 422
    assert runtime.connection.client.calls == []


def test_history_and_operator_assessment_are_robot_authoritative(monkeypatch):
    client, runtime = make_client(monkeypatch)
    history = client.get("/api/bioxp/operator-controls/history")
    assessed = client.post("/api/bioxp/operator-controls/receipts/cmd-1/assessment", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": "assess-12345678",
        "verdict": "pass",
        "note": "Observed X/Y references.",
    })
    assert history.status_code == 200
    assert assessed.status_code == 200
    assert assessed.json()["operator_assessment"] == "pass"
    assert assessed.json()["operator_assessment_idempotency_key"] == "assess-12345678"
    assert assessed.json()["operator_assessed_at"] == 1785434400.0
    assert runtime.connection.client.calls == [
        ("operator_action_history", {}),
        ("assess_operator_action", {
            "path_params": {"command_id": "cmd-1"},
            "json_data": {
                "expected_generation": 7,
                "idempotency_key": "assess-12345678",
                "verdict": "pass",
                "note": "Observed X/Y references.",
            },
        }),
    ]


def test_history_accepts_robot_authority_fingerprint(monkeypatch):
    client, runtime = make_client(monkeypatch)
    fingerprint = "a" * 64
    runtime.connection.client.responses["operator_action_history"] = {
        "schema_version": "bioxp.operator_action_history.v1",
        "receipts": [{**receipt(), "authority_fingerprint": fingerprint}],
    }

    response = client.get("/api/bioxp/operator-controls/history")

    assert response.status_code == 200
    assert response.json()["receipts"][0]["authority_fingerprint"] == fingerprint


def test_history_accepts_robot_startup_reconciliation_receipts(monkeypatch):
    client, runtime = make_client(monkeypatch)
    reconciled = {
        **receipt(command_id="crash-queued"),
        "status": "reconciliation_required",
        "remote_acknowledged": False,
        "controller_acknowledged": False,
        "controller_terminal_state_verified": False,
        "automatic_retry": False,
        "physical_outcome": "ambiguous",
    }
    runtime.connection.client.responses["operator_action_history"] = {
        "schema_version": "bioxp.operator_action_history.v1",
        "receipts": [reconciled],
    }

    response = client.get("/api/bioxp/operator-controls/history")

    assert response.status_code == 200
    assert response.json()["receipts"][0]["status"] == "reconciliation_required"


_FIXED_QUARANTINE_CASES = (
    ("route.motion_power_diag", "/motion/power/diag"),
    ("route.runtime_emergency_stop", "/oem/runtime/emergency_stop"),
)


def test_semantically_unproven_operator_paths_are_visible_but_never_mutation_relayed(monkeypatch):
    client, runtime = make_client(monkeypatch)
    catalog_payload = runtime.connection.client.responses["operator_control_catalog"]
    template = catalog_payload["actions"][0]
    for action_id, path in _FIXED_QUARANTINE_CASES:
        catalog_payload["actions"].append({
            **template,
            "action_id": action_id,
            "label": action_id,
            "kind": "primitive",
            "informational_method": "POST",
            "informational_path": path,
            "provider_available": True,
            "provider_unavailable_reason": None,
            "available": True,
            "unavailable_reason": None,
            "enabled": True,
            "disabled_reason": None,
            "dependencies": [],
            "inputs": [],
            "stages": [],
        })

    catalog_response = client.get("/api/bioxp/operator-controls/catalog")
    assert catalog_response.status_code == 200, catalog_response.text
    by_id = {row["action_id"]: row for row in catalog_response.json()["actions"]}
    for action_id, path in _FIXED_QUARANTINE_CASES:
        reason = OPERATOR_SEMANTIC_QUARANTINE_BY_PATH[path]
        row = by_id[action_id]
        assert row["provider_available"] is False
        assert row["available"] is False
        assert row["enabled"] is False
        assert row["provider_unavailable_reason"] == reason
        assert row["disabled_reason"] == reason

    runtime.connection.client.calls.clear()
    for action_id, path in _FIXED_QUARANTINE_CASES:
        reason = OPERATOR_SEMANTIC_QUARANTINE_BY_PATH[path]
        admission = client.post(f"/api/bioxp/operator-controls/actions/{action_id}/admission", json={
            "expected_connection_generation": 77,
            "expected_ownership_generation": 7,
            "inputs": {},
        })
        assert admission.status_code == 200, admission.text
        assert admission.json()["enabled"] is False
        assert admission.json()["disabled_reason"] == reason

        invocation = client.post(f"/api/bioxp/operator-controls/actions/{action_id}", json={
            "expected_connection_generation": 77,
            "expected_ownership_generation": 7,
            "idempotency_key": f"quarantine-{action_id[-12:]}",
            "inputs": {},
        })
        assert invocation.status_code == 409
        assert invocation.json()["detail"] == reason

    assert runtime.connection.client.calls == [
        ("operator_control_catalog", {})
        for _ in range(len(_FIXED_QUARANTINE_CASES))
    ]


def test_quarantine_validates_both_generation_domains_before_responding(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = runtime.connection.client.responses["operator_control_catalog"]
    template = payload["actions"][0]
    action_id, path = _FIXED_QUARANTINE_CASES[0]
    payload["actions"].append({
        **template,
        "action_id": action_id,
        "informational_method": "POST",
        "informational_path": path,
        "inputs": [],
    })

    stale_connection = client.post(f"/api/bioxp/operator-controls/actions/{action_id}/admission", json={
        "expected_connection_generation": 999,
        "expected_ownership_generation": 7,
        "inputs": {},
    })
    assert stale_connection.status_code == 409
    assert "connection generation changed" in stale_connection.json()["detail"].lower()
    assert runtime.connection.client.calls == []

    stale_ownership = client.post(f"/api/bioxp/operator-controls/actions/{action_id}/admission", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 999,
        "inputs": {},
    })
    assert stale_ownership.status_code == 409
    assert "ownership generation changed" in stale_ownership.json()["detail"].lower()
    assert runtime.connection.client.calls == [("operator_control_catalog", {})]


def test_catalog_removes_non_oem_session_field_and_keeps_latest_startup_status(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = runtime.connection.client.responses["operator_control_catalog"]
    template = payload["actions"][0]
    session_input = {
        **template["inputs"][0],
        "name": "session_id",
        "wire_name": "session_id",
        "label": "Session Id",
        "value_type": "string",
        "location": "path",
        "required": True,
        "default": None,
    }
    payload["actions"].extend([
        {
            **template,
            "action_id": "route.startup_status_by_session",
            "kind": "primitive",
            "informational_method": "GET",
            "informational_path": "/oem/startup/status/{session_id}",
            "inputs": [session_input],
        },
        {
            **template,
            "action_id": "route.startup_status_latest",
            "kind": "primitive",
            "informational_method": "GET",
            "informational_path": "/oem/startup/status/latest",
            "inputs": [],
        },
        {
            **template,
            "action_id": "route.startup_door_event",
            "kind": "primitive",
            "informational_method": "POST",
            "informational_path": "/oem/startup/door_event",
            "inputs": [{**session_input, "location": "body", "required": False}],
        },
    ])

    response = client.get("/api/bioxp/operator-controls/catalog")
    assert response.status_code == 200, response.text
    actions = response.json()["actions"]
    paths = {row["informational_path"] for row in actions}
    assert "/oem/startup/status/latest" in paths
    assert "/oem/startup/status/{session_id}" not in paths
    assert all(input_row["name"] != "session_id" for row in actions for input_row in row["inputs"])

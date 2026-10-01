from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routers import bioxp
from routers.bioxp.operator_controls import _translate_robot_error
from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTimeoutError
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
        "hardware_truth_level": "hardware_query",
        "delivery_verified": False,
        "controller_acknowledged": False,
        "completion_verified": False,
        "hardware_postcondition_verified": False,
        "physical_effect_verified": False,
        "oem_source_anchor": "ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData",
        "receipt_id": "a" * 32,
        "receipt_truth": {
            "semantic_query_response_verified": False,
            "delivery_verified": False,
            "controller_acknowledged": False,
            "completion_verified": False,
            "hardware_precondition_verified": False,
            "hardware_postcondition_verified": False,
            "physical_effect_verified": False,
            "physical_effect_claim_suppressed": True,
        },
    }


def v2_receipt(*, action_id: str = "oem.y.move_steps", command_id: str = "cmd-1") -> dict:
    return {
        "schema_version": "bioxp.operator_action_receipt.v2",
        "command_id": command_id,
        "action_id": action_id,
        "status": "queued",
        "terminal": False,
        "sequence": 1,
        "method_id": None,
        "ownership_generation": 1,
        "expected_board_epoch_by_board": {"4": 2},
        "state_version": 1,
        "status_path": f"/operator/v2/actions/receipts/{command_id}",
        "accepted_at": 1.0,
        "queued_at": 1.0,
        "dispatched_at": None,
        "finished_at": None,
        "terminal_receipt_id": None,
        "completion_class": None,
        "physical_effect_verified": False,
        "error": None,
    }


def v2_receipt_detail() -> dict:
    return {
        **v2_receipt(),
        "canonical_inputs": {"steps": 20},
        "requested_values": {},
        "effective_values": {},
        "observed_values": {},
        "raw_return_layers": {},
        "controller_evidence": {},
        "transport_artifacts": [],
        "child_receipts": [],
        "transitions": [],
    }


def history_page(*, limit=100, items=None):
    row = {**v2_receipt(), 'history': {
        'source': 'direct', 'source_schema': 'bioxp.operator_action_receipt.v1',
        'recorded_status': 'queued', 'remote_acknowledged': False,
        'controller_acknowledged': False, 'controller_terminal_state_verified': None,
        'machine_assessment': 'unverified', 'operator_assessment': None, 'operator_note': None,
    }}
    return {'schema_version': 'bioxp.operator_action_history.v2',
            'items': [row] if items is None else items, 'next_cursor': None, 'limit': limit}


def v2_dashboard() -> dict:
    return {
        "schema_version": "bioxp.operator_dashboard.v2",
        "generated_at": 1.0,
        "ownership_generation": 1,
        "board4": {
            "state": "active",
            "prior_board_epoch": 1,
            "active_board_epoch": 2,
            "transition_phase": "committed",
            "transition_evidence": {},
            "member_motors": {"y": 0, "z": 1, "gripper": 2},
            "state_version": 2,
            "updated_at": 1.0,
        },
        "y_axis": {
            "axis": "y",
            "board_id": 4,
            "motor_id": 0,
            "ownership_generation": 1,
            "prior_board_epoch": 1,
            "active_board_epoch": 2,
            "prepared_board_epoch": 2,
            "lifecycle_state": "referenced_ready",
            "reference_state": "referenced",
            "position_steps": 1000,
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
        },
        "active_commands": [],
        "command_queue": {
            "schema_version": "bioxp.oem_command_queue.v1",
            "generated_at": 1.0,
            "items": [],
        },
        "latest_receipts": [],
    }


def v2_method() -> dict:
    return {
        "schema_version": "bioxp.operator_method.v1",
        "method_id": "method-1",
        "action_id": "oem.xy.home",
        "status": "queued",
        "state_version": 1,
        "child_receipts": [],
        "accepted_at": 1.0,
        "finished_at": None,
    }


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
            "provider": {
                "bound": True,
                "state": "prepared_unreferenced",
                "switch_mask_policy": "observed_only_oem_source_omits_z_writes",
                "switch_mask_tuple": None,
            },
            "snapshot_freshness": {"state": "fresh"},
            "last_failure": None,
            "authority": "Serial206OemInitializationProvider",
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


def y_interrupt_receipt() -> dict:
    return {
        "schema_version": "bioxp.operator_interrupt_receipt.v1",
        "robot_identity": "bioxp3200-serial206",
        "ownership_generation": 7,
        "interrupt_attempt_id": "interrupt-attempt-12345678",
        "interrupt_id": "interrupt-12345678",
        "action_id": "oem.y.stop",
        "scope": "y",
        "cutoff": 12,
        "active_command_id": "command-y-12345678",
        "active_command_ids": ["command-y-12345678"],
        "global_safety_epoch": 9,
        "x_safety_epoch": 4,
        "y_safety_epoch": 7,
        "z_safety_epoch": 3,
        "oem_abort_latched": False,
        "controller_stop_attempted": True,
        "source_call_completed": True,
        "source_return_ok": True,
        "controller_stop_acknowledged": True,
        "controller_stop_delivered": None,
        "controller_response": {"status": 100},
        "controller_response_evidence": None,
        "first_stop_ack": None,
        "second_stop_ack": None,
        "terminal_speed_evidence": None,
        "authority_snapshot": None,
        "error": None,
        "physical_effect_verified": False,
        "persistence_state": "committed",
        "recovery_hold": False,
        "transition_sequence": 12,
        "terminal_transition_sequences": [12],
        "idempotent_replay": False,
        "observed_ownership_generation": 7,
        "observed_board_epoch_by_board": {},
    }


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
                    "semantic_query_response_verified": False,
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
            "interrupt_operator_action_v1": y_interrupt_receipt(),
            "operator_control_catalog_v2": {
                "schema_version": "bioxp.operator_control_catalog.v2",
                "dashboard": v2_dashboard(),
                "actions": [{
                    "action_id": "oem.y.move_steps",
                    "request_schema_version": "bioxp.operator_action_request.v2",
                    "response_schema_version": "bioxp.operator_action_receipt.v2",
                    "interrupt": False,
                    "enabled": True,
                    "disabled_reason": None,
                }],
            },
            "operator_dashboard_v2": v2_dashboard(),
            "invoke_operator_action_v2": v2_receipt(),
            "operator_action_receipt_v2": v2_receipt(),
            "operator_action_receipt_v2_detail": v2_receipt_detail(),
            "submit_operator_method_v1": v2_method(),
            "operator_method_status_v1": v2_method(),
            "operator_command_status_v2": v2_receipt(),
            "operator_command_status_v2_detail": v2_receipt_detail(),
            "operator_action_history": history_page(),
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
        if route_name == "pipette_readback":
            result = self.responses[route_name]
            return {**result, "semantic_query_response_verified": result["receipt_truth"]["semantic_query_response_verified"]}
        return self.responses[route_name]


@dataclass
class FakeSnapshot:
    generation: int = 77


class FakeConnection:
    def __init__(self):
        self.client = FakeRobotClient()
        self.value = FakeSnapshot()
        self.safety_interrupt_calls = []
        self.active_request_calls = []
        self.oem_action_calls = []

    @property
    def generation(self):
        return self.value.generation

    def snapshot(self):
        return self.value

    @asynccontextmanager
    async def active_request_lease(self, *, expected_generation, require_fresh=True):
        if expected_generation != self.value.generation:
            raise ConnectionStateError("Expected connection generation does not match")
        self.active_request_calls.append({"require_fresh": require_fresh})
        yield self.client

    async def request_active(self, route_name, *, expected_generation, require_fresh=True, **kwargs):
        if expected_generation != self.value.generation:
            raise ConnectionStateError(
                f"BioXP connection generation changed: expected {expected_generation}, current {self.value.generation}"
            )
        self.active_request_calls.append({
            "route_name": route_name,
            "require_fresh": require_fresh,
            "path_params": kwargs.get("path_params"),
        })
        return await self.client.request(route_name, **kwargs)

    async def request_active_query(self, route_name, *, expected_generation, require_fresh=True, **kwargs):
        return await self.request_active(
            route_name,
            expected_generation=expected_generation,
            require_fresh=require_fresh,
            **kwargs,
        )

    async def request_active_oem_action(self, route_name, *, expected_generation, **kwargs):
        if expected_generation != self.value.generation:
            raise ConnectionStateError(
                f"BioXP connection generation changed: expected {expected_generation}, current {self.value.generation}"
            )
        self.oem_action_calls.append({
            "route_name": route_name,
            "path_params": kwargs.get("path_params"),
        })
        return await self.client.request(route_name, **kwargs)

    async def request_active_v2_query(self, route_name, *, expected_generation, **kwargs):
        if kwargs.get("params", {}).get("detail") is True:
            route_name = f"{route_name}_detail"
        return await self.request_active(
            route_name,
            expected_generation=expected_generation,
            require_fresh=False,
            **kwargs,
        )

    async def request_active_v2_enqueue(self, route_name, *, expected_generation, **kwargs):
        return await self.request_active(
            route_name,
            expected_generation=expected_generation,
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


def test_dashboard_relays_successive_move_queue(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = catalog()["dashboard"]
    payload["successive_move_queue"] = {
        "x": {
            "active_command_id": "operator_x_1787188152000_abc",
            "depth": 1,
            "head_action_id": "oem.x.move_steps",
            "state": "queued",
        }
    }
    runtime.connection.client.responses["operator_dashboard"] = payload

    response = client.get("/api/bioxp/operator-controls/dashboard")

    assert response.status_code == 200
    queue = response.json()["successive_move_queue"]
    assert queue["x"]["depth"] == 1
    assert queue["x"]["head_action_id"] == "oem.x.move_steps"
    assert queue["x"]["state"] == "queued"


def test_invoke_relays_queued_successive_move_receipt(monkeypatch):
    client, runtime = make_client(monkeypatch)
    queued = receipt(action_id="oem.x.move_steps", command_id="operator-x-relay-queued")
    queued.update({
        "status": "queued",
        "queued_at": 1787188152.83,
        "dispatched_at": None,
        "controller_acknowledged": False,
        "controller_terminal_state_verified": False,
        "authority_receipt_id": None,
        "authority_receipt_status": None,
        "authority_fingerprint": None,
        "observation_receipt_id": None,
        "observes_command_id": None,
    })
    runtime.connection.client.responses["invoke_operator_action"] = queued

    response = client.post(
        "/api/bioxp/operator-controls/actions/oem.x.move_steps",
        json={
            "expected_connection_generation": 77,
            "expected_ownership_generation": 7,
            "idempotency_key": "invoke-12345678",
            "inputs": {"steps": 100},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["queued_at"] == 1787188152.83
    assert runtime.connection.active_request_calls == []
    assert runtime.connection.oem_action_calls == [{
        "route_name": "invoke_operator_action",
        "path_params": {"action_id": "oem.x.move_steps"},
    }]


def test_z_invocation_relies_on_robot_admission_without_bms_freshness_gate(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action"] = receipt(
        action_id="oem.z.move_steps",
        key="invoke-z-12345678",
        command_id="operator-z-direct",
    )

    response = client.post(
        "/api/bioxp/operator-controls/actions/oem.z.move_steps",
        json={
            "expected_connection_generation": 77,
            "expected_ownership_generation": 7,
            "idempotency_key": "invoke-z-12345678",
            "inputs": {"steps": 100},
        },
    )

    assert response.status_code == 200
    assert runtime.connection.active_request_calls == []
    assert runtime.connection.oem_action_calls == [{
        "route_name": "invoke_operator_action",
        "path_params": {"action_id": "oem.z.move_steps"},
    }]


def test_robot_409_translation_preserves_structured_detail():
    detail = {"error": "action_unavailable", "reason": {"key": "z_switch_masks_clear", "met": False}}
    translated = _translate_robot_error(RobotResponseError(409, detail))
    assert translated.status_code == 409
    assert translated.detail == detail


def test_dispatched_timeout_translation_is_explicitly_ambiguous_and_do_not_retry():
    translated = _translate_robot_error(RobotTimeoutError("late robot response possible", dispatched=True))
    assert translated.status_code == 504
    assert translated.detail == {
        "error": "bioxp_robot_timeout",
        "message": "late robot response possible",
        "robot_response_received": False,
        "dispatch_state": "outcome_ambiguous",
        "status_recovery": "query_current_v2_dashboard_and_receipt_before_any_retry",
    }


def test_legacy_catalog_projection_preserves_y_and_xy_action_identities(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    payload = runtime.connection.client.responses["operator_control_catalog"]
    payload["actions"].extend([
        {**copy.deepcopy(payload["actions"][0]), "action_id": "oem.y.move_steps"},
        {**copy.deepcopy(payload["actions"][0]), "action_id": "oem.xy.home"},
    ])
    response = client.get("/api/bioxp/operator-controls/catalog")
    assert response.status_code == 200
    assert [row["action_id"] for row in response.json()["actions"]] == [
        "motion.home_xy",
        "oem.y.move_steps",
        "oem.xy.home",
    ]


def test_addressed_y_interrupt_relays_robot_receipt_unchanged(monkeypatch):
    client, runtime = make_client(monkeypatch)
    body = {
        "expected_connection_generation": 77,
        "schema_version": "bioxp.operator_interrupt_request.v1",
        "idempotency_key": "stop-y-12345678",
        "reason": "operator requested Y STOP",
        "observed_ownership_generation": 7,
        "observed_board_epoch_by_board": {},
    }

    # Robot _compact_v2_receipt uses the common v2 receipt for interrupts.
    # Keep the raw interrupt detail instead of accepting an obsolete envelope.
    compact = {
        **v2_receipt(action_id="oem.y.stop"),
        "interrupt_evidence": {
            "source_call_completed": True, "source_return_ok": True,
            "controller_stop_acknowledged": True,
            "controller_terminal_state_verified": None,
            "physical_effect_verified": False, "persistence_state": "committed",
            "details": y_interrupt_receipt(),
        },
        "transport_exchanges": [], "transport_retention_errors": [],
    }
    runtime.connection.client.responses["interrupt_operator_action_v1"] = compact
    response = client.post("/api/bioxp/operator-controls/v2/interrupts/oem.y.stop", json=body)

    assert response.status_code == 200, response.text
    assert response.json() == compact
    route_name, kwargs = runtime.connection.safety_interrupt_calls[-1]
    assert route_name == "interrupt_operator_action_v1"
    assert kwargs == {
        "path_params": {"action_id": "oem.y.stop"},
        "json_data": {
            "schema_version": "bioxp.operator_interrupt_request.v1",
            "idempotency_key": "stop-y-12345678",
            "reason": "operator requested Y STOP",
            "observed_ownership_generation": 7,
            "observed_board_epoch_by_board": {},
        },
    }


def test_every_strict_v2_route_relays_and_validates_the_exact_robot_contract(monkeypatch):
    client, runtime = make_client(monkeypatch)
    action_request = {
        "expected_connection_generation": 77,
        "schema_version": "bioxp.operator_action_request.v2",
        "idempotency_key": "move-12345678",
        "expected_ownership_generation": 1,
        "expected_board_epoch_by_board": {},
        "inputs": {"steps": 20},
    }
    method_request = {
        "expected_connection_generation": 77,
        "schema_version": "bioxp.operator_method_request.v1",
        "idempotency_key": "method-12345678",
        "method_action_id": "oem.xy.home",
        "expected_ownership_generation": 1,
        "expected_board_epoch_by_board": {},
        "inputs": {},
    }

    responses = [
        client.get("/api/bioxp/operator-controls/v2/catalog"),
        client.get("/api/bioxp/operator-controls/v2/dashboard"),
        client.post("/api/bioxp/operator-controls/v2/actions/oem.y.move_steps", json=action_request),
        client.get("/api/bioxp/operator-controls/history?limit=100"),
        client.get("/api/bioxp/operator-controls/v2/receipts/cmd-1"),
        client.get("/api/bioxp/operator-controls/v2/receipts/cmd-1?detail=true"),
        client.post("/api/bioxp/operator-controls/v2/methods", json=method_request),
        client.get("/api/bioxp/operator-controls/v2/methods/method-1"),
        client.get("/api/bioxp/operator-controls/v2/commands/cmd-1"),
        client.get("/api/bioxp/operator-controls/v2/commands/cmd-1?detail=true"),
    ]

    assert [response.status_code for response in responses] == [200, 200, 202, 200, 200, 200, 202, 200, 200, 200]
    assert responses[0].json()["dashboard"]["y_axis"]["active_board_epoch"] == 2

    assert responses[2].json()["command_id"] == "cmd-1"
    assert responses[5].json()["canonical_inputs"] == {"steps": 20}
    assert responses[6].json()["method_id"] == "method-1"
    assert [call[0] for call in runtime.connection.client.calls] == [
        "operator_control_catalog_v2",
        "operator_dashboard_v2",
        "invoke_operator_action_v2",
        "operator_action_history",
        "operator_action_receipt_v2",
        "operator_action_receipt_v2_detail",
        "submit_operator_method_v1",
        "operator_method_status_v1",
        "operator_command_status_v2",
        "operator_command_status_v2_detail",
    ]
    assert "expected_connection_generation" not in runtime.connection.client.calls[2][1]["json_data"]
    assert "expected_connection_generation" not in runtime.connection.client.calls[6][1]["json_data"]


@pytest.mark.parametrize("action_id", ["meta.activate_motion", "meta.recover_motion_non_homing"])
def test_oem_lifecycle_actions_relay_through_the_closed_v2_contract(monkeypatch, action_id):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action_v2"] = v2_receipt(
        action_id=action_id,
        command_id=f"{action_id.replace('.', '-')}-1",
    )
    response = client.post(
        f"/api/bioxp/operator-controls/v2/actions/{action_id}",
        json={
            "expected_connection_generation": 77,
            "schema_version": "bioxp.operator_action_request.v2",
            "idempotency_key": f"{action_id.replace('.', '-')}-12345678",
            "expected_ownership_generation": 1,
            "expected_board_epoch_by_board": {},
            "inputs": {},
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["action_id"] == action_id
    _, kwargs = runtime.connection.client.calls[-1]
    assert kwargs["path_params"] == {"action_id": action_id}
    assert kwargs["json_data"]["inputs"] == {}


def test_deck_move_relays_only_semantic_inputs_and_exact_board_fences(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action_v2"] = v2_receipt(
        action_id="oem.deck.move_to_location",
        command_id="deck-command-1",
    )
    response = client.post(
        "/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_location",
        json={
            "expected_connection_generation": 77,
            "schema_version": "bioxp.operator_action_request.v2",
            "idempotency_key": "deck-move-12345678",
            "expected_ownership_generation": 1,
            "expected_board_epoch_by_board": {"4": 2, "5": 8},
            "inputs": {"target": "LOC_OC", "camera_offset": False},
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["command_id"] == "deck-command-1"
    _, kwargs = runtime.connection.client.calls[-1]
    assert kwargs["json_data"] == {
        "schema_version": "bioxp.operator_action_request.v2",
        "idempotency_key": "deck-move-12345678",
        "expected_ownership_generation": 1,
        "expected_board_epoch_by_board": {"4": 2, "5": 8},
        "inputs": {"target": "LOC_OC", "camera_offset": False},
    }


@pytest.mark.parametrize("epochs", [{}, {"4": 2}, {"5": 8}, {"4": 2, "5": 8, "6": 1}])
def test_deck_move_forwards_available_epoch_observations_without_host_gate(monkeypatch, epochs):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["invoke_operator_action_v2"] = v2_receipt(
        action_id="oem.deck.move_to_location", command_id="deck-command-observation")
    response = client.post(
        "/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_location",
        json={
            "expected_connection_generation": 77,
            "schema_version": "bioxp.operator_action_request.v2",
            "idempotency_key": "deck-move-12345678",
            "expected_ownership_generation": 1,
            "expected_board_epoch_by_board": epochs,
            "inputs": {"target": "LOC_OC", "camera_offset": False},
        },
    )
    assert response.status_code == 202, response.text
    assert len(runtime.connection.client.calls) == 1
    assert runtime.connection.client.calls[0][1]["json_data"]["expected_board_epoch_by_board"] == epochs


@pytest.mark.parametrize("status", [409, 422, 503])
def test_deck_move_preserves_robot_rejection_evidence_and_never_retries(monkeypatch, status):
    client, runtime = make_client(monkeypatch)
    evidence = {"error": "deck_move_rejected", "command_id": "deck-command-9", "evidence": {"reason": "stale_authority"}}

    async def reject(*args, **kwargs):
        raise RobotResponseError(status, evidence)

    runtime.connection.request_active_v2_enqueue = reject
    response = client.post(
        "/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_location",
        json={
            "expected_connection_generation": 77,
            "schema_version": "bioxp.operator_action_request.v2",
            "idempotency_key": "deck-move-12345678",
            "expected_ownership_generation": 1,
            "expected_board_epoch_by_board": {"4": 2, "5": 8},
            "inputs": {"target": "LOC_OC", "camera_offset": False},
        },
    )
    assert response.status_code == status
    assert response.json()["detail"] == evidence


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
        "/api/bioxp/operator-controls/pipettes/application/plan?expected_connection_generation=77",
        headers={"Idempotency-Key": "pipette-test-12345678"},
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
        "/api/bioxp/operator-controls/pipettes/readback?expected_connection_generation=77",
        headers={"Idempotency-Key": "pipette-test-12345678"},
        json={"include_data": False},
    )

    assert response.status_code == 200
    assert response.json()["channels_constructed_unconditionally"] == [0, 1, 2, 3]
    assert response.json()["live_query_performed"] is True
    assert runtime.connection.client.calls == [
        ("pipette_readback", {"json_data": {"include_data": False, "idempotency_key": "pipette-test-12345678"}}),
    ]


def test_pipette_active_readback_request_rejects_unknown_fields(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)

    response = client.post(
        "/api/bioxp/operator-controls/pipettes/readback?expected_connection_generation=77",
        headers={"Idempotency-Key": "pipette-test-12345678"},
        json={"include_data": False, "operation": "aspirate"},
    )

    assert response.status_code == 422
    assert runtime.connection.client.calls == []


def _real_hardware_tip_evidence() -> dict:
    return {
        "ok": True,
        "hardware_truth_level": "hardware_query",
        "reply_received": True,
        "semantic_ok": True,
        "tip_loaded": False,
        "source_tip_loaded": False,
        "source_return": 2,
        "source_return_completed": True,
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
        "/api/bioxp/operator-controls/pipettes/application/plan?expected_connection_generation=77",
        headers={"Idempotency-Key": "pipette-test-12345678"},
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
        "/api/bioxp/operator-controls/pipettes/application/plan?expected_connection_generation=77",
        headers={"Idempotency-Key": "pipette-test-12345678"},
        json=payload,
    )

    assert response.status_code == 200
    assert runtime.connection.client.calls == [
        ("pipette_application_plan", {"json_data": {**forwarded, "idempotency_key": "pipette-test-12345678"}}),
    ]


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


def test_catalog_is_robot_owned_and_strict(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.get("/api/bioxp/operator-controls/catalog")
    assert response.status_code == 200
    assert response.json()["actions"][0]["action_id"] == "motion.home_xy"
    assert response.json()["actions"][0]["inputs"][0]["exclusive_minimum"] == 0.1
    assert runtime.connection.client.calls == [
        ("operator_control_catalog", {"params": None}),
        ("operator_control_catalog_v2", {"params": {"schema_version": "bioxp.operator_control_catalog.v2"}}),
    ]
    assert response.json()["canonical"]["schema_version"] == "bioxp.operator_control_catalog.v2"


def test_catalog_accepts_typed_z_provider_last_observation(monkeypatch):
    client, runtime = make_client(monkeypatch)
    observation = {
        "command_id": "operator-observation-1",
        "observes_command_id": "operator-home-1",
        "verdict": "pass",
        "physical_motion_observed": True,
        "expected_direction_observed": True,
        "home_endpoint_observed": True,
        "stopped_observed": True,
        "source_already_home_short_circuit": False,
        "physical_effect_verified": True,
        "reference_eligible": True,
        "authority_current": True,
        "observed_at": 1785873899.8462605,
        "note": "Operator-confirmed OEM home observation.",
        "reference_persistence": {
            "ok": True,
            "axis": "z",
            "state": "referenced",
            "origin_position_steps": 0,
            "source": "serial206.z.operator_observation",
            "note": None,
            "updated_at": "2026-08-04T20:04:59.850580+00:00",
            "last_motion_kind": "home",
            "persisted": True,
            "verified": True,
            "durable_clean": True,
        },
    }
    runtime.connection.client.responses["operator_control_catalog"]["dashboard"]["z_axis"]["provider"]["last_observation"] = observation

    response = client.get("/api/bioxp/operator-controls/catalog")

    assert response.status_code == 200, response.text
    assert response.json()["dashboard"]["z_axis"]["provider"]["last_observation"] == observation


def test_shared_xz_catalog_and_dashboard_query_robot_without_bms_freshness_gate(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)

    catalog_response = client.get("/api/bioxp/operator-controls/catalog")
    dashboard_response = client.get("/api/bioxp/operator-controls/dashboard")

    assert catalog_response.status_code == 200, catalog_response.text
    assert dashboard_response.status_code == 200, dashboard_response.text
    assert [
        (call["route_name"], call["require_fresh"])
        for call in runtime.connection.active_request_calls
    ] == [
        ("operator_control_catalog", False),
        ("operator_control_catalog_v2", False),
        ("operator_dashboard", False),
    ]


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


@pytest.mark.parametrize("action_id", ["oem.x.move_steps", "oem.z.move_steps"])
def test_exact_xz_admission_and_invocation_rely_on_robot_without_host_policy_gates(monkeypatch, action_id):
    client, runtime = make_client(monkeypatch, mutations=False)
    axis = action_id.split(".")[1]
    key = f"{axis}-direct-12345678"
    runtime.connection.client.responses["operator_action_admission"] = {
        "action_id": action_id,
        "ownership_generation": 7,
        "enabled": True,
        "disabled_reason": None,
        "dependencies": [],
    }
    runtime.connection.client.responses["invoke_operator_action"] = receipt(
        action_id=action_id,
        key=key,
        command_id=f"{axis}-direct-command-1",
    )

    admission = client.post(f"/api/bioxp/operator-controls/actions/{action_id}/admission", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "inputs": {"steps": 100},
    })
    invocation = client.post(f"/api/bioxp/operator-controls/actions/{action_id}", json={
        "expected_connection_generation": 77,
        "expected_ownership_generation": 7,
        "idempotency_key": key,
        "inputs": {"steps": 100},
    })

    assert admission.status_code == 200, admission.text
    assert admission.json()["enabled"] is True
    assert invocation.status_code == 200, invocation.text
    assert invocation.json()["action_id"] == action_id
    admission_call = next(
        call for call in runtime.connection.active_request_calls
        if call["route_name"] == "operator_action_admission"
    )
    assert admission_call["require_fresh"] is False
    assert runtime.connection.oem_action_calls == [{
        "route_name": "invoke_operator_action",
        "path_params": {"action_id": action_id},
    }]


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
        ("operator_action_history", {"params": {"limit": 100}}),
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


def test_history_exposes_only_one_format_and_retired_route_is_absent(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.get('/api/bioxp/operator-controls/history')
    assert response.status_code == 200
    assert response.json()['schema_version'] == 'bioxp.operator_action_history.v2'
    assert set(response.json()) == {'schema_version', 'items', 'next_cursor', 'limit'}
    assert client.get('/api/bioxp/operator-controls/v2/history').status_code == 404
    assert 'operator_action_history_v2' not in DEFAULT_ROBOT_ROUTES
    paths = client.get('/openapi.json').json()['paths']
    assert '/api/bioxp/operator-controls/v2/history' not in paths
    assert '/api/bioxp/operator-controls/history' in paths


@pytest.mark.parametrize('limit', [8, 25, 50, 100, 200])
def test_history_passes_limit_and_cursor_without_format_negotiation(monkeypatch, limit):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['operator_action_history'] = history_page(limit=limit, items=[])
    response = client.get('/api/bioxp/operator-controls/history', params={'limit': limit, 'cursor': 'opaque-cursor'})
    assert response.status_code == 200, response.text
    assert runtime.connection.client.calls[-1] == ('operator_action_history', {'params': {'limit': limit, 'cursor': 'opaque-cursor'}})


def test_history_defaults_limit_to_100_when_omitted(monkeypatch):
    client, runtime = make_client(monkeypatch)
    assert client.get('/api/bioxp/operator-controls/history').status_code == 200
    assert runtime.connection.client.calls[-1][1]['params'] == {'limit': 100}


def test_receipt_detail_keeps_incomplete_native_evidence_without_promoting_it(monkeypatch):
    client, runtime = make_client(monkeypatch)
    native = {'command_id': 'cmd-1', 'authority_fingerprint': 'a' * 64,
              'outcome': 'in_progress', 'response': {'body': {'retained_layers': [1, None, 'raw']}},
              'stage_receipts': [{'raw': 'retained proof'}]}
    runtime.connection.client.responses['operator_action_receipt_v2_detail'] = {**v2_receipt_detail(), 'source_receipt': native}
    response = client.get('/api/bioxp/operator-controls/v2/receipts/cmd-1?detail=true')
    assert response.status_code == 200, response.text
    assert response.json()['source_receipt'] == native
    assert response.json()['physical_effect_verified'] is False


_FIXED_QUARANTINE_CASES = (
    ("route.motion_power_diag", "/motion/power/diag"),
    ("route.runtime_emergency_stop", "/oem/runtime/emergency_stop"),
)


def refusal_stop_receipt() -> dict:
    """Exact robot receipt for a stop press refused before dispatch.

    Captured from the live robot (2026-09-21, oem.x.stop,
    interrupt_request_schema_required): the four nullable interrupt-evidence
    flags are omitted entirely rather than recorded as nulls, which the BMS
    relay must tolerate without inventing delivery facts.
    """
    return {
        "schema_version": "bioxp.operator_action_receipt.v2",
        "command_id": "141380cb8e0c48aa8990dab4b94d0ae1",
        "action_id": "oem.x.stop",
        "status": "rejected",
        "terminal": True,
        "sequence": 13101,
        "method_id": None,
        "ownership_generation": 1,
        "expected_board_epoch_by_board": {},
        "state_version": 1,
        "status_path": "/operator/v2/actions/receipts/141380cb8e0c48aa8990dab4b94d0ae1",
        "accepted_at": 1789989274.050407,
        "queued_at": 1789989274.050407,
        "dispatched_at": None,
        "finished_at": 1789989274.050427,
        "terminal_receipt_id": None,
        "completion_class": None,
        "physical_effect_verified": False,
        "error": {
            "code": "action_rejected",
            "message": "Operator action was rejected.",
            "retryable": False,
        },
        "transport_exchanges": [],
        "transport_retention_errors": [],
        "interrupt_evidence": {
            "details": {
                "rejection": "interrupt_request_schema_required",
                "required_schema": "bioxp.operator_interrupt_request.v1",
                "surface": "x",
            },
            "persistence_state": "committed",
            "physical_effect_verified": False,
        },
        "z_move": None,
        "xy_failure": None,
    }


def test_v2_dashboard_route_relays_refusal_receipt(monkeypatch) -> None:
    client, runtime = make_client(monkeypatch)
    payload = v2_dashboard()
    payload["latest_receipts"] = [refusal_stop_receipt()]
    runtime.connection.client.responses["operator_dashboard_v2"] = payload

    response = client.get("/api/bioxp/operator-controls/v2/dashboard")

    assert response.status_code == 200
    evidence = response.json()["latest_receipts"][0]["interrupt_evidence"]
    assert evidence == refusal_stop_receipt()["interrupt_evidence"]
    assert evidence["details"]["rejection"] == "interrupt_request_schema_required"


def test_v2_catalog_route_relays_refusal_receipt(monkeypatch) -> None:
    client, runtime = make_client(monkeypatch)
    dashboard = v2_dashboard()
    dashboard["latest_receipts"] = [refusal_stop_receipt()]
    payload = {
        "schema_version": "bioxp.operator_control_catalog.v2",
        "dashboard": dashboard,
        "actions": [{
            "action_id": "oem.x.stop",
            "request_schema_version": "bioxp.operator_interrupt_request.v1",
            "response_schema_version": "bioxp.operator_action_receipt.v2",
            "interrupt": True,
            "enabled": True,
            "disabled_reason": None,
        }],
    }
    runtime.connection.client.responses["operator_control_catalog_v2"] = payload

    response = client.get("/api/bioxp/operator-controls/v2/catalog")

    assert response.status_code == 200
    evidence = response.json()["dashboard"]["latest_receipts"][0]["interrupt_evidence"]
    assert evidence == refusal_stop_receipt()["interrupt_evidence"]


def test_v2_receipt_route_relays_refusal_receipt(monkeypatch) -> None:
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["operator_action_receipt_v2"] = refusal_stop_receipt()

    response = client.get(
        "/api/bioxp/operator-controls/v2/receipts/141380cb8e0c48aa8990dab4b94d0ae1"
    )

    assert response.status_code == 200
    evidence = response.json()["interrupt_evidence"]
    assert evidence == refusal_stop_receipt()["interrupt_evidence"]

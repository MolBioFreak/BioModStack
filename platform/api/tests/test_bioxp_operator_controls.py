from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from services.bioxp.errors import ConnectionStateError
from services.bioxp.operator_semantic_quarantine import OPERATOR_SEMANTIC_QUARANTINE_BY_PATH
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

REGISTRY = "1" * 64
LOCK = "2" * 64


def catalog():
    dashboard = {
        "schema_version": "bioxp.operator_dashboard.v1",
        "ownership_generation": 7,
        "connection": {"live": True, "ownership": {"transport": "owned", "usb": "service", "router": "running", "CAN_READY": True}},
        "motion": {"enabled": False, "reason": "Motion is inactive."},
        "operation": {"state": "stopped", "reason": "ready"},
        "enclosure": {"door_closed": True, "latch_closed": True},
        "axes": [{"axis": "x", "reference": "referenced", "position_steps": 123, "speed_steps_s": 0, "run_current": 31, "standby_current": 8, "left_switch_active": False, "right_switch_active": True, "motor_temperature_c": None, "motor_temperature_available": False}],
        "temperatures": [{"sensor": "tc_temp_c", "label": "Thermal cycler block", "unit": "°C", "temperature_c": 37.0, "available": True}],
        "pipettes": {"ok": True, "channels": [{"channel": 0, "available": True}]},
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
        "physical_effect_verified": False,
        "machine_assessment": "unverified",
        "operator_assessment": None,
        "operator_note": None,
        "inputs": {},
        "response": {"controller_acknowledged": True},
        "error": None,
        "stage_receipts": [],
    }


class FakeRobotClient:
    def __init__(self):
        self.calls = []
        self.responses = {
            "operator_control_catalog": catalog(),
            "operator_dashboard": catalog()["dashboard"],
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

    def snapshot(self):
        return self.value

    async def request_active(self, route_name, *, expected_generation, require_fresh=True, **kwargs):
        if expected_generation != self.value.generation:
            raise ConnectionStateError(
                f"BioXP connection generation changed: expected {expected_generation}, current {self.value.generation}"
            )
        assert require_fresh is True
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


def test_robot_client_uses_fixed_operator_routes_only():
    assert DEFAULT_ROBOT_ROUTES["operator_control_catalog"][:2] == ("GET", "/operator/control-catalog")
    assert DEFAULT_ROBOT_ROUTES["operator_dashboard"][:2] == ("GET", "/operator/dashboard")
    assert DEFAULT_ROBOT_ROUTES["operator_action_admission"][:2] == ("POST", "/operator/actions/{action_id}/admission")
    assert DEFAULT_ROBOT_ROUTES["invoke_operator_action"][:2] == ("POST", "/operator/actions/{action_id}")
    assert DEFAULT_ROBOT_ROUTES["operator_action_history"][:2] == ("GET", "/operator/actions/history")
    assert DEFAULT_ROBOT_ROUTES["operator_action_receipt"][:2] == ("GET", "/operator/actions/receipts/{command_id}")
    assert DEFAULT_ROBOT_ROUTES["assess_operator_action"][:2] == ("POST", "/operator/actions/receipts/{command_id}/assessment")


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
    assert dashboard.json()["axes"][0]["motor_temperature_available"] is False
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
        ("operator_control_catalog", {}),
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


_FIXED_QUARANTINE_CASES = (
    ("route.motion_power_enable", "/motion/power/enable"),
    ("route.motion_power_diag", "/motion/power/diag"),
    ("route.runtime_emergency_stop", "/oem/runtime/emergency_stop"),
    ("alternate.valid.id.for.same.path", "/motion/power/enable"),
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
        for _ in range(len(_FIXED_QUARANTINE_CASES) * 2)
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

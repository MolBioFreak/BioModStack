from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routers import bioxp


REGISTRY = "1" * 64
EVIDENCE_LOCK = "2" * 64


def canonical_run(*, run_state="planned"):
    terminal = "cancelled_by_operator" if run_state == "cancelled" else None
    return {
        "schema_version": "bioxp.oem_full_lifecycle.v1",
        "run_id": "run-12345678",
        "command": "initialize_oem_movement_lifecycle",
        "idempotency_key": "plan-12345678",
        "request": {
            "command": "initialize_oem_movement_lifecycle",
            "operator_ack": "INITIALIZE",
            "expected_generation": 7,
            "bms_connection_generation": 77,
            "expected_machine_serial": 206,
            "expected_registry_sha256": REGISTRY,
            "expected_evidence_lock_sha256": EVIDENCE_LOCK,
            "idempotency_key": "plan-12345678",
            "mode": "dry_run",
            "inputs": {
                "ownership_generation": 7,
                "can_ready": True,
                "board_test_mode": False,
                "pipette_exists": None,
                "initialize_system_producer": "initializeEnvironment",
                "update_check_suppresses_initialize_system": False,
                "system_in_motion_at_entry": False,
                "enclosure_door_closed": True,
                "latch_closed": True,
                "saved_status": 1,
                "ship_mode": "",
                "start_mode": "WebMode",
                "tip_present": False,
                "self_test_due": True,
                "check_camera": True,
                "camera_installed": True,
                "is_development_machine": False,
                "deck_inspection": True,
            },
        },
        "run_state": run_state,
        "terminal_state": "cancelled" if run_state == "cancelled" else None,
        "planned_terminal_state": "oem_movement_ready_job_admission",
        "current_stage": None,
        "expected_next_stage": None if run_state == "cancelled" else "construct_control_lib",
        "blocked_reason": None,
        "source_authority_verified": False,
        "configuration_verified": False,
        "evidence_lock_verified": True,
        "source_registry_identity_verified": True,
        "machine_configuration_verified": True,
        "transport_owner_verified": False,
        "controller_acknowledged": False,
        "postcondition_verified": False,
        "physical_motion_commanded": False,
        "physical_effect_verified": False,
        "safety_deviation": [],
        "registry_sha256": REGISTRY,
        "evidence_lock_path": "/authority/evidence-lock.json",
        "evidence_lock_sha256": EVIDENCE_LOCK,
        "evidence_lock_schema": "bioxp.oem_evidence_lock.v4",
        "evidence_lock_identity_verified": True,
        "acquisition_id": "serial-206-acquisition",
        "machine_serial": 206,
        "ownership_generation": 7,
        "transport_frames": [],
        "sequence": 2 if run_state == "cancelled" else 1,
        "stages": [{
            "stage_id": "construct_control_lib",
            "source_anchor": "BioXPMainWindow.initializeEnvironment",
            "branch": None,
            "movement_ledger_stage": None,
            "status": "pending",
            "would_command_hardware": False,
            "would_command_physical_motion": False,
            "physical_motion_commanded": False,
            "controller_acknowledged": False,
            "postcondition_verified": False,
            "physical_effect_verified": False,
            "started_at": None,
            "completed_at": None,
            "blocked_reason": None,
        }],
        "created_at": 1785024000.0,
        "updated_at": 1785024000.0,
    }


class FakeRobotClient:
    def __init__(self):
        self.calls = []
        self.on_request = None
        self.responses = {
            "oem_full_lifecycle_contract": {
                "schema_version": "bioxp.oem_full_lifecycle_contract.v1",
                "command": "initialize_oem_movement_lifecycle",
                "machine_serial": 206,
                "registry_sha256": REGISTRY,
                "evidence_lock_path": "/robot/private/OEM_EVIDENCE_LOCK.json",
                "evidence_lock_sha256": EVIDENCE_LOCK,
                "evidence_lock_schema": "bioxp.oem_evidence_lock.v4",
                "evidence_lock_identity_verified": True,
                "acquisition_id": "serial-206-acquisition",
                "evidence_lock_verified": True,
                "source_registry_identity_verified": True,
                "machine_configuration_verified": True,
                "source_authority_verified": False,
                "ownership_generation": 7,
                "initialize_system_producers": [{
                    "producer": "initializeEnvironment",
                    "source_anchor": "BioXPMainWindow.initializeEnvironment:989-997",
                }],
                "plan_available": True,
                "plan_blockers": [],
                "live_creation_enabled": False,
                "physical_commissioning_complete": False,
                "providers": {
                    "initial_check": {
                        "source_contract": True,
                        "implemented": True,
                        "live_bound": True,
                        "commissioned": False,
                    },
                },
                "safety_boundary": {
                    "caller_supplied_motion_parameters": False,
                    "dry_run_commands_hardware": False,
                    "queue_acceptance_is_execution": False,
                    "physical_effect_verified": False,
                },
            },
            "plan_oem_full_lifecycle": copy.deepcopy(canonical_run()),
            "get_oem_full_lifecycle_run": copy.deepcopy(canonical_run()),
            "get_oem_full_lifecycle_ledger": copy.deepcopy(canonical_run()),
            "cancel_oem_full_lifecycle_run": copy.deepcopy(canonical_run(run_state="cancelled")),
        }

    async def request(self, route_name, **kwargs):
        self.calls.append((route_name, kwargs))
        if self.on_request is not None:
            self.on_request(route_name)
        return self.responses[route_name]


@dataclass
class FakeSnapshot:
    active: bool = True
    generation: int = 77
    observation_fresh: bool | None = True


class FakeConnection:
    def __init__(self):
        self.active_client = FakeRobotClient()
        self.value = FakeSnapshot()

    def snapshot(self):
        return self.value

    async def request_active(
        self,
        route_name,
        *,
        expected_generation,
        require_fresh=True,
        **kwargs,
    ):
        from services.bioxp.errors import ConnectionStateError

        if not self.value.active or self.active_client is None:
            raise ConnectionStateError("BioXP saved profile is not actively connected")
        if self.value.generation != expected_generation:
            raise ConnectionStateError("Expected connection generation does not match the active generation")
        if require_fresh and self.value.observation_fresh is not True:
            raise ConnectionStateError("A fresh process-local BioXP status observation is required")
        return await self.active_client.request(route_name, **kwargs)


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


def request_payload(**extra):
    value = {
        "expected_generation": 77,
        "expected_machine_serial": 206,
        "expected_registry_sha256": REGISTRY,
        "expected_evidence_lock_sha256": EVIDENCE_LOCK,
        "idempotency_key": "plan-12345678",
    }
    value.update(extra)
    return value


def cancel_payload(**extra):
    value = {
        "expected_generation": 77,
        "expected_machine_serial": 206,
        "expected_registry_sha256": REGISTRY,
        "expected_evidence_lock_sha256": EVIDENCE_LOCK,
    }
    value.update(extra)
    return value


def test_contract_and_reads_use_only_fixed_robot_routes(monkeypatch):
    client, runtime = make_client(monkeypatch)
    contract = client.get("/api/bioxp/oem-full-lifecycle/contract")
    assert contract.status_code == 200
    assert "evidence_lock_path" not in contract.json()
    assert client.get("/api/bioxp/oem-full-lifecycle/runs/run-12345678").status_code == 200
    assert client.get("/api/bioxp/oem-full-lifecycle/runs/run-12345678/ledger").status_code == 200
    assert runtime.connection.active_client.calls == [
        ("oem_full_lifecycle_contract", {}),
        ("get_oem_full_lifecycle_run", {"path_params": {"run_id": "run-12345678"}}),
        ("get_oem_full_lifecycle_ledger", {"path_params": {"run_id": "run-12345678"}}),
    ]


def test_plan_is_fixed_dry_run_with_no_caller_motion_fields(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 200
    assert runtime.connection.active_client.calls == [
        ("oem_full_lifecycle_contract", {}),
        (
            "plan_oem_full_lifecycle",
            {
                "json_data": {
                    "command": "initialize_oem_movement_lifecycle",
                    "operator_ack": "INITIALIZE",
                    "expected_generation": 7,
                    "bms_connection_generation": 77,
                    "expected_machine_serial": 206,
                    "expected_registry_sha256": REGISTRY,
                    "expected_evidence_lock_sha256": EVIDENCE_LOCK,
                    "idempotency_key": "plan-12345678",
                    "mode": "dry_run",
                }
            },
        )
    ]
    for field, value in (("mode", "live"), ("axis", "z"), ("stage", "M01"), ("raw_frame", "00ff")):
        rejected = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload(**{field: value}))
        assert rejected.status_code == 422, (field, rejected.text)


def test_plan_requires_kill_switch_generation_and_fresh_process_observation(monkeypatch):
    disabled, disabled_runtime = make_client(monkeypatch, mutations=False)
    assert disabled.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload()).status_code == 503
    assert disabled_runtime.connection.active_client.calls == []

    client, runtime = make_client(monkeypatch)
    assert client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload(expected_generation=76)).status_code == 409
    runtime.connection.value.observation_fresh = False
    assert client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload()).status_code == 409
    assert runtime.connection.active_client.calls == []


@pytest.mark.parametrize("generation", [True, "77", 77.0])
def test_plan_and_cancel_reject_coerced_connection_generations(monkeypatch, generation):
    client, _ = make_client(monkeypatch)
    assert client.post(
        "/api/bioxp/oem-full-lifecycle/runs",
        json=request_payload(expected_generation=generation),
    ).status_code == 422
    assert client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(expected_generation=generation),
    ).status_code == 422


def test_plan_refuses_robot_contract_blockers_before_mutation(monkeypatch):
    client, runtime = make_client(monkeypatch)
    contract = runtime.connection.active_client.responses["oem_full_lifecycle_contract"]
    contract["plan_available"] = False
    contract["plan_blockers"] = ["robot-owned can_ready must be an exact boolean"]

    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())

    assert response.status_code == 409
    assert "can_ready" in response.json()["detail"]
    assert runtime.connection.active_client.calls == [("oem_full_lifecycle_contract", {})]


def test_plan_refuses_changed_or_unverified_evidence_authority_before_mutation(monkeypatch):
    client, runtime = make_client(monkeypatch)
    contract = runtime.connection.active_client.responses["oem_full_lifecycle_contract"]
    contract["evidence_lock_sha256"] = "3" * 64
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 409
    assert runtime.connection.active_client.calls == [("oem_full_lifecycle_contract", {})]

    runtime.connection.active_client.calls.clear()
    contract["evidence_lock_sha256"] = EVIDENCE_LOCK
    contract["machine_configuration_verified"] = False
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert runtime.connection.active_client.calls == [("oem_full_lifecycle_contract", {})]


def test_cancel_is_generation_freshness_gated_and_response_checked(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    )
    assert response.status_code == 200
    assert runtime.connection.active_client.calls == [
        ("get_oem_full_lifecycle_run", {"path_params": {"run_id": "run-12345678"}}),
        (
            "cancel_oem_full_lifecycle_run",
            {
                "path_params": {"run_id": "run-12345678"},
                "json_data": {
                    "expected_generation": 7,
                    "bms_connection_generation": 77,
                    "expected_machine_serial": 206,
                    "expected_registry_sha256": REGISTRY,
                    "expected_evidence_lock_sha256": EVIDENCE_LOCK,
                },
            },
        ),
    ]

    runtime.connection.value.observation_fresh = False
    assert client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    ).status_code == 409


def test_plan_rejects_unsafe_robot_response(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.active_client.responses["plan_oem_full_lifecycle"]["physical_effect_verified"] = True
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert "unsafe" in response.json()["detail"].lower()


@pytest.mark.parametrize("field", [
    "source_authority_verified",
    "configuration_verified",
    "transport_owner_verified",
    "controller_acknowledged",
])
def test_plan_rejects_contradictory_dry_run_authority_claims(monkeypatch, field):
    client, runtime = make_client(monkeypatch)
    runtime.connection.active_client.responses["plan_oem_full_lifecycle"][field] = True
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502


def test_plan_rejects_unverified_evidence_lock_identity(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.active_client.responses["plan_oem_full_lifecycle"]["evidence_lock_identity_verified"] = False
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502


@pytest.mark.parametrize("mutate", [
    lambda run: run.update({"controller_acknowledged": True}),
    lambda run: run.update({"source_authority_verified": True}),
    lambda run: run.update({"configuration_verified": True}),
    lambda run: run.update({"transport_owner_verified": True}),
    lambda run: run.update({"evidence_lock_identity_verified": False}),
    lambda run: run["stages"][0].update({"physical_motion_commanded": True}),
    lambda run: run["stages"][0].update({"controller_acknowledged": True}),
])
def test_cancel_rejects_contradictory_authority_or_no_effect_claims(monkeypatch, mutate):
    client, runtime = make_client(monkeypatch)
    mutate(runtime.connection.active_client.responses["cancel_oem_full_lifecycle_run"])
    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    )
    assert response.status_code == 502


def test_cancel_does_not_depend_on_current_plan_availability(monkeypatch):
    client, runtime = make_client(monkeypatch)
    contract = runtime.connection.active_client.responses["oem_full_lifecycle_contract"]
    contract["plan_available"] = False
    contract["plan_blockers"] = ["current robot predicate unavailable"]
    contract["machine_configuration_verified"] = False

    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    )
    assert response.status_code == 200
    assert response.json()["run_state"] == "cancelled"


def test_plan_rejects_mismatched_canonical_request_echo(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.active_client.responses["plan_oem_full_lifecycle"]["request"][
        "expected_registry_sha256"
    ] = "9" * 64
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert "request echo" in response.json()["detail"].lower()


@pytest.mark.parametrize("mutate", [
    lambda run: run.update({"stages": []}),
    lambda run: run["stages"][0].update({"state": run["stages"][0].pop("status")}),
    lambda run: run.update({"unexpected_authority": True}),
    lambda run: run.update({"physical_motion_commanded": "false"}),
])
def test_plan_rejects_noncanonical_run_and_stage_shapes(monkeypatch, mutate):
    client, runtime = make_client(monkeypatch)
    mutate(runtime.connection.active_client.responses["plan_oem_full_lifecycle"])
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert "malformed" in response.json()["detail"].lower()


def test_plan_rereads_generation_and_freshness_immediately_before_mutation(monkeypatch):
    client, runtime = make_client(monkeypatch)

    def stale_after_contract(route_name):
        if route_name == "oem_full_lifecycle_contract":
            runtime.connection.value.generation = 78
            runtime.connection.value.observation_fresh = False

    runtime.connection.active_client.on_request = stale_after_contract
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 409
    assert runtime.connection.active_client.calls == [("oem_full_lifecycle_contract", {})]


def test_plan_revalidates_returned_authority_identity(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response_payload = runtime.connection.active_client.responses["plan_oem_full_lifecycle"]
    response_payload["machine_serial"] = 999
    response_payload["registry_sha256"] = "3" * 64
    response_payload["evidence_lock_sha256"] = "4" * 64
    response_payload["machine_configuration_verified"] = False
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502


def test_cancel_rereads_generation_and_freshness_immediately_before_mutation(monkeypatch):
    client, runtime = make_client(monkeypatch)

    def stale_after_admitted_run(route_name):
        if route_name == "get_oem_full_lifecycle_run":
            runtime.connection.value.generation = 78
            runtime.connection.value.observation_fresh = False

    runtime.connection.active_client.on_request = stale_after_admitted_run
    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    )
    assert response.status_code == 409
    assert runtime.connection.active_client.calls == [
        ("get_oem_full_lifecycle_run", {"path_params": {"run_id": "run-12345678"}})
    ]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("request", "idempotency_key"), "mutated-idempotency"),
        (("request", "inputs", "saved_status"), 2),
        (("request", "inputs", "ownership_generation"), 8),
        (("ownership_generation",), 8),
        (("planned_terminal_state",), "mutated-terminal"),
        (("stages", 0, "status"), "completed"),
    ],
)
def test_cancel_rejects_any_nonterminal_canonical_echo_mutation(monkeypatch, path, value):
    client, runtime = make_client(monkeypatch)
    target = runtime.connection.active_client.responses["cancel_oem_full_lifecycle_run"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json=cancel_payload(),
    )
    assert response.status_code == 502


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("request", "inputs", "ownership_generation"), 8),
        (("terminal_state",), "cancelled"),
        (("stages", 0, "status"), "completed"),
    ],
)
def test_plan_rejects_cross_field_or_state_contradictions(monkeypatch, path, value):
    client, runtime = make_client(monkeypatch)
    target = runtime.connection.active_client.responses["plan_oem_full_lifecycle"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502


@pytest.mark.parametrize(
    "mutate",
    [
        lambda contract: contract.__setitem__("mutation_token", "must-not-reach-browser"),
        lambda contract: contract["providers"]["initial_check"].__setitem__(
            "raw_mutation_header", "must-not-reach-browser"
        ),
        lambda contract: contract["safety_boundary"].__setitem__(
            "arbitrary_robot_path", "/oem/runtime/commands/enqueue"
        ),
    ],
)
def test_contract_projection_rejects_credential_or_route_injection(monkeypatch, mutate):
    client, runtime = make_client(monkeypatch)
    mutate(runtime.connection.active_client.responses["oem_full_lifecycle_contract"])
    response = client.get("/api/bioxp/oem-full-lifecycle/contract")
    assert response.status_code == 502
    assert "must-not-reach-browser" not in response.text
    assert "/oem/runtime/commands/enqueue" not in response.text

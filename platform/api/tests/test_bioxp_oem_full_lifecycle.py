from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp


REGISTRY = "1" * 64
EVIDENCE_LOCK = "2" * 64


class FakeRobotClient:
    def __init__(self):
        self.calls = []
        self.responses = {
            "oem_full_lifecycle_contract": {
                "machine_serial": 206,
                "registry_sha256": REGISTRY,
                "evidence_lock_sha256": EVIDENCE_LOCK,
                "source_authority_verified": True,
                "plan_available": True,
                "plan_blockers": [],
                "live_creation_enabled": False,
                "physical_commissioning_complete": False,
            },
            "plan_oem_full_lifecycle": {
                "run_id": "run-12345678",
                "mode": "dry_run",
                "run_state": "planned",
                "physical_command_sent": False,
                "physical_effect_verified": False,
                "stages": [],
            },
            "get_oem_full_lifecycle_run": {"run_id": "run-12345678", "run_state": "planned"},
            "get_oem_full_lifecycle_ledger": {"run_id": "run-12345678", "stages": []},
            "cancel_oem_full_lifecycle_run": {
                "run_id": "run-12345678",
                "run_state": "cancelled",
                "physical_command_sent": False,
                "physical_effect_verified": False,
            },
        }

    async def request(self, route_name, **kwargs):
        self.calls.append((route_name, kwargs))
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


def test_contract_and_reads_use_only_fixed_robot_routes(monkeypatch):
    client, runtime = make_client(monkeypatch)
    assert client.get("/api/bioxp/oem-full-lifecycle/contract").status_code == 200
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
                    "expected_machine_serial": 206,
                    "expected_registry_sha256": REGISTRY,
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
    contract["source_authority_verified"] = False
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert runtime.connection.active_client.calls == [("oem_full_lifecycle_contract", {})]


def test_cancel_is_generation_freshness_gated_and_response_checked(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json={"expected_generation": 77},
    )
    assert response.status_code == 200
    assert runtime.connection.active_client.calls == [
        ("cancel_oem_full_lifecycle_run", {"path_params": {"run_id": "run-12345678"}}),
    ]

    runtime.connection.value.observation_fresh = False
    assert client.post(
        "/api/bioxp/oem-full-lifecycle/runs/run-12345678/cancel",
        json={"expected_generation": 77},
    ).status_code == 409


def test_plan_rejects_unsafe_robot_response(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.active_client.responses["plan_oem_full_lifecycle"]["physical_effect_verified"] = True
    response = client.post("/api/bioxp/oem-full-lifecycle/runs", json=request_payload())
    assert response.status_code == 502
    assert "unsafe" in response.json()["detail"].lower()

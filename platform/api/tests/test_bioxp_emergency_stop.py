from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from routers.bioxp.dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from services.bioxp.models import BioXpSnapshot
from services.bioxp.operator_semantic_quarantine import EMERGENCY_STOP_QUARANTINE_REASON


class FakeCommandCoordinator:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.registry: dict = {}

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("quarantined emergency stop must not reach the coordinator")


class FakeConnectionManager:
    def __init__(self, snapshot: BioXpSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> BioXpSnapshot:
        return self._snapshot


class FakeRuntime:
    def __init__(self, snapshot: BioXpSnapshot) -> None:
        self.connection = FakeConnectionManager(snapshot)
        self.commands = FakeCommandCoordinator()
        self.artifact_store = None
        self.startup_warnings: list[str] = []
        self.legacy_jobs = SimpleNamespace(model_dump=lambda: {})


def active_snapshot(*, generation: int = 4) -> BioXpSnapshot:
    return BioXpSnapshot(
        configured=True,
        active=True,
        display_name="lab",
        masked_target="https://robot.tailnet.ts.net",
        generation=generation,
        freshness_budget_seconds=30.0,
    )


def mounted_app(runtime: FakeRuntime) -> FastAPI:
    app = FastAPI()
    app.include_router(bioxp.router, prefix="/api/bioxp")
    app.dependency_overrides[get_bioxp_runtime] = lambda: runtime
    app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
    return app


def test_emergency_stop_is_semantically_quarantined_without_coordinator_dispatch(monkeypatch):
    runtime = FakeRuntime(active_snapshot())
    app = mounted_app(runtime)
    monkeypatch.setattr("routers.bioxp.connection.mutations_enabled", lambda: True)
    with TestClient(app) as client:
        response = client.post(
            "/api/bioxp/emergency-stop",
            json={"expected_generation": 4, "idempotency_key": "test-stop-0001"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == EMERGENCY_STOP_QUARANTINE_REASON
    assert runtime.commands.calls == []


def test_connection_status_advertises_no_physical_emergency_delivery(monkeypatch):
    runtime = FakeRuntime(active_snapshot())
    app = mounted_app(runtime)
    monkeypatch.setattr("routers.bioxp.connection.mutations_enabled", lambda: True)
    with TestClient(app) as client:
        response = client.get("/api/bioxp/status")

    assert response.status_code == 200
    body = response.json()
    assert body["available_controls"] == []
    assert body["emergency_stop"] == {
        "delivery_available": False,
        "physical_effect_verifiable": False,
        "reason": EMERGENCY_STOP_QUARANTINE_REASON,
    }

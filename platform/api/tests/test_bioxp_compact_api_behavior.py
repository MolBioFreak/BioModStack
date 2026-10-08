from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from services.bioxp.runtime import create_bioxp_runtime

def _client(runtime) -> TestClient:
    app = FastAPI()
    app.state.bioxp_runtime = runtime
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app)


def test_startup_is_disconnected_and_unverified_commands_are_not_advertised(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BMS_BIOXP_ALLOWED_HOSTS", "robot")
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    runtime = create_bioxp_runtime(data_root=tmp_path)
    with _client(runtime) as client:
        saved = client.put(
            "/api/bioxp/profile",
            json={"schema_version": 1, "display_name": "Lab robot", "api_url": "http://robot:8123"},
        )
        status = client.get("/api/bioxp/status")
    asyncio.run(runtime.close())

    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["valid"] is True
    assert saved.json()["target_url"] != "http://robot:8123"
    assert status.status_code == 200
    assert status.json()["connection"]["active"] is False
    assert status.json()["available_commands"] == []

    restarted = create_bioxp_runtime(data_root=tmp_path)
    with _client(restarted) as client:
        after_restart = client.get("/api/bioxp/status").json()
    asyncio.run(restarted.close())
    assert after_restart["connection"]["configured"] is True
    assert after_restart["connection"]["active"] is False
    assert after_restart["connection"]["generation"] != status.json()["connection"]["generation"]


def test_offline_compile_is_open_but_local_submission_requires_mutation_authorization(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("BMS_BIOXP_MUTATIONS_ENABLED", raising=False)
    runtime = create_bioxp_runtime(data_root=tmp_path)
    protocol = {
        "schema_version": 1,
        "name": "offline",
        "steps": [{"action": "initialize_motors"}],
    }
    with _client(runtime) as client:
        compiled = client.post("/api/bioxp/protocols/compile", json=protocol)
        blocked = client.post(
            "/api/bioxp/protocols/submit",
            json={"protocol": protocol, "idempotency_key": "offline-submit-1"},
        )
        monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
        submitted = client.post(
            "/api/bioxp/protocols/submit",
            json={"protocol": protocol, "idempotency_key": "offline-submit-1"},
        )
        job_id = submitted.json()["job"]["job_id"]
        detail = client.get(f"/api/bioxp/jobs/{job_id}")
    asyncio.run(runtime.close())

    assert compiled.status_code == 200
    assert compiled.json()["executable"] is False
    assert compiled.json()["robot_compatible"] is None
    assert blocked.status_code == 503
    assert submitted.status_code == 202
    assert submitted.json()["delivery_attempted"] is False
    assert submitted.json()["job"]["state"] == "submission_blocked"
    assert detail.json()["events"][-1]["to_state"] == "submission_blocked"


def test_robot_facing_operations_fail_closed_then_default_registry_still_denies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime = create_bioxp_runtime(data_root=tmp_path)
    command = {
        "command": "initialize_motors",
        "expected_generation": 1,
        "idempotency_key": "command-test-1",
    }
    emergency = {"expected_generation": 1, "idempotency_key": "emergency-test-1"}
    with _client(runtime) as client:
        disabled_command = client.post("/api/bioxp/commands", json=command)
        disabled_stop = client.post("/api/bioxp/emergency-stop", json=emergency)

        monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
        authorized_command = client.post(
            "/api/bioxp/commands",
            json=command,
        )
        authorized_stop = client.post(
            "/api/bioxp/emergency-stop",
            json=emergency,
        )
    asyncio.run(runtime.close())

    assert disabled_command.status_code == 503
    assert disabled_stop.status_code == 503
    assert authorized_command.status_code == 409
    assert "Disabled until" in authorized_command.json()["detail"]
    assert authorized_stop.status_code == 409
    assert "active target" in authorized_stop.json()["detail"]


def test_malformed_persisted_profile_is_sanitized_without_auto_connect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    runtime = create_bioxp_runtime(data_root=tmp_path)
    profile_path = tmp_path / "bioxp" / "profile.json"
    profile_path.write_text('{"api_url":"http://robot:8123","secret":"do-not-leak"}', encoding="utf-8")
    with _client(runtime) as client:
        profile = client.get("/api/bioxp/profile")
        status = client.get("/api/bioxp/status")
        connect = client.post("/api/bioxp/connection/connect")
    asyncio.run(runtime.close())

    assert profile.status_code == 200
    assert profile.json()["valid"] is False
    assert profile.json()["display_name"] is None
    assert profile.json()["target_url"] is None
    assert "malformed" in profile.json()["detail"].lower()
    assert "do-not-leak" not in profile.text
    assert "do-not-leak" not in status.text
    assert "do-not-leak" not in connect.text
    assert status.json()["connection"]["active"] is False
    assert connect.status_code == 409
    assert "malformed" in connect.json()["detail"].lower()

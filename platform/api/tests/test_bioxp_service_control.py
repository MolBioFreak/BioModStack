"""Real HTTP/service/profile owners; only the SSH process boundary is inert."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from routers.bioxp import router
from routers.bioxp import connection as routes
from services.bioxp import service_control as control
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.models import BioXpProfile
from services.bioxp.profile_store import BioXpProfileStore
from services.bioxp.target_policy import BioXpTargetPolicy

OBSERVATION = b"SubState=running\nMainPID=417\nActiveState=active\nInvocationID=0123456789abcdef0123456789abcdef\n"
EXPECTED = {
    "restarted": True, "unit": "bioxp-api.service", "active_state": "active",
    "sub_state": "running", "invocation_id": "0123456789abcdef0123456789abcdef", "pid": 417,
}


class InertProcess:
    def __init__(self):
        self.stdout = OBSERVATION
        self.exit_code = 0
        self.returncode = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.kills = 0
        self.waits = 0

    async def communicate(self):
        self.started.set()
        await self.release.wait()
        self.returncode = self.exit_code
        return self.stdout, None

    def kill(self):
        self.kills += 1
        self.returncode = -9
        self.release.set()

    async def wait(self):
        self.waits += 1
        return self.returncode


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    monkeypatch.setenv("BMS_BIOXP_CONNECTION_ENABLED", "0")
    monkeypatch.setenv("BMS_BIOXP_SERVICE_SSH_TARGET", "operator@robot-fixture")
    monkeypatch.setenv("BMS_BIOXP_SERVICE_API_URL", "http://ROBOT-FIXTURE.:8123/")
    store = BioXpProfileStore(tmp_path / "profile.json")
    store.save(BioXpProfile(display_name="Offline fixture", api_url="http://robot-fixture:8123"))
    connection = BioXpConnectionService(store, BioXpTargetPolicy(allowed_hosts=["robot-fixture"]))
    # These existing owners may not be used by metadata or service recovery.
    for name in ("snapshot", "connect", "probe_status_only", "request_active", "request_active_query"):
        monkeypatch.setattr(connection, name, Mock(side_effect=AssertionError(f"native owner queried: {name}")))
    monkeypatch.setattr(connection.target_policy, "validate_for_connection", Mock(side_effect=AssertionError("DNS queried")))
    owner = control.BioXpServiceControl()
    monkeypatch.setattr(routes, "service_control", owner)
    process = InertProcess()
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(control.asyncio, "create_subprocess_exec", spawn)
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=connection)
    app.include_router(router, prefix="/api/bioxp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, connection=connection, store=store, owner=owner, process=process, calls=calls)
    assert not owner._restart_lock.locked()


@pytest.mark.asyncio
async def test_local_availability_no_native_or_ssh(harness):
    response = await harness.client.get("/api/bioxp/service")
    assert response.status_code == 200
    assert response.json() == {"available": True, "detail": None, "unit": "bioxp-api.service", "restart_in_progress": False}
    assert harness.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_exact_service_only_restart_when_native_unavailable(harness, connected):
    if connected:
        harness.connection._client = Mock(side_effect=AssertionError("unresponsive native client touched"))
    response = await harness.client.post("/api/bioxp/service/restart", json={})
    assert response.status_code == 200
    assert response.json() == EXPECTED
    assert harness.calls == [((
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectionAttempts=1", "--", "operator@robot-fixture",
        "sudo -n systemctl restart bioxp-api.service && systemctl show bioxp-api.service --property=ActiveState --property=SubState --property=InvocationID --property=MainPID",
    ), {"stdin": asyncio.subprocess.DEVNULL, "stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.DEVNULL})]
    assert harness.process.kills == 0
    assert (await harness.client.get("/api/bioxp/service")).json()["restart_in_progress"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,value", [
    ("BMS_BIOXP_SERVICE_SSH_TARGET", ""),
    ("BMS_BIOXP_SERVICE_API_URL", ""),
    ("BMS_BIOXP_SERVICE_API_URL", "http://other:8123"),
    ("BMS_BIOXP_SERVICE_API_URL", "https://robot-fixture:8123"),
    ("BMS_BIOXP_SERVICE_API_URL", "http://robot-fixture:8124"),
    ("BMS_BIOXP_SERVICE_API_URL", "http://secret:credential@robot-fixture:8123"),
    ("BMS_BIOXP_SERVICE_API_URL", "http://robot-fixture:8123/path"),
])
async def test_disabled_or_mismatched_config_is_local_only(harness, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    available = await harness.client.get("/api/bioxp/service")
    assert available.status_code == 200
    assert available.json()["available"] is False
    response = await harness.client.post("/api/bioxp/service/restart", json={})
    assert response.status_code == 503
    assert "credential" not in response.text
    assert harness.calls == []
    # New capability configuration never becomes a profile/ordinary-action gate.
    assert (await harness.client.get("/api/bioxp/profile")).json()["valid"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["-oProxyCommand=evil", "robot;reboot", "u@robot\nevil", "u@robot$(evil)", "u@robot host", "ssh://robot", "robot:22", "u@@robot", " robot", "robot "])
async def test_ssh_injection_configuration_rejected(harness, monkeypatch, target):
    monkeypatch.setenv("BMS_BIOXP_SERVICE_SSH_TARGET", target)
    assert (await harness.client.get("/api/bioxp/service")).json()["available"] is False
    assert (await harness.client.post("/api/bioxp/service/restart", json={})).status_code == 503
    assert harness.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ['{"target":"other"}', '{"unit":"other"}', '{"command":"reboot"}', '{"force":true}', '[]', 'null', 'true', '""', ''])
async def test_strict_empty_object_required(harness, body):
    response = await harness.client.post("/api/bioxp/service/restart", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert harness.calls == []


@pytest.mark.asyncio
async def test_existing_mutation_guard(harness, monkeypatch):
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    assert (await harness.client.get("/api/bioxp/service")).json()["available"] is True
    response = await harness.client.post("/api/bioxp/service/restart", json={})
    assert response.status_code == 503
    assert "mutations are disabled" in response.json()["detail"]
    assert harness.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["missing", "malformed"])
async def test_profile_failures_do_not_contact_robot(harness, profile):
    if profile == "missing":
        harness.store.forget()
    else:
        harness.store.path.write_text("not json")
    assert (await harness.client.get("/api/bioxp/service")).json()["available"] is False
    assert (await harness.client.post("/api/bioxp/service/restart", json={})).status_code == 503
    assert harness.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ssh", "spawn", "timeout", "properties", "duplicate", "pid", "utf8"])
async def test_failures_are_uncertain_sanitized_and_never_retried(harness, monkeypatch, failure):
    if failure == "ssh":
        harness.process.exit_code = 255
    elif failure == "spawn":
        async def fail(*args, **kwargs):
            harness.calls.append((args, kwargs))
            raise OSError("sensitive configuration credential")
        monkeypatch.setattr(control.asyncio, "create_subprocess_exec", fail)
    elif failure == "timeout":
        harness.process.release.clear()
        monkeypatch.setattr(control, "RESTART_TIMEOUT_SECONDS", 0.02)
    elif failure == "properties":
        harness.process.stdout = b"ActiveState=active\n"
    elif failure == "duplicate":
        harness.process.stdout = OBSERVATION + b"MainPID=1\n"
    elif failure == "pid":
        harness.process.stdout = OBSERVATION.replace(b"417", b"not-a-pid")
    else:
        harness.process.stdout = b"\xff"
    response = await harness.client.post("/api/bioxp/service/restart", json={})
    assert response.status_code == 502
    assert "uncertain" in response.json()["detail"]
    assert "No automatic retry" in response.json()["detail"]
    assert "credential" not in response.text
    assert len(harness.calls) == 1
    if failure == "timeout":
        assert harness.process.kills == harness.process.waits == 1
    assert (await harness.client.get("/api/bioxp/service")).json()["restart_in_progress"] is False


@pytest.mark.asyncio
async def test_exact_inactive_observation_is_not_api_readiness_gate(harness):
    harness.process.stdout = b"MainPID=0\nInvocationID=\nActiveState=failed\nSubState=failed\n"
    response = await harness.client.post("/api/bioxp/service/restart", json={})
    assert response.status_code == 200
    assert response.json() == {**EXPECTED, "pid": 0, "invocation_id": "", "active_state": "failed", "sub_state": "failed"}


@pytest.mark.asyncio
async def test_concurrent_duplicate_returns_conflict_without_queue(harness):
    harness.process.release.clear()
    first = asyncio.create_task(harness.client.post("/api/bioxp/service/restart", json={}))
    await asyncio.wait_for(harness.process.started.wait(), 1)
    try:
        assert (await harness.client.get("/api/bioxp/service")).json()["restart_in_progress"] is True
        duplicate = await harness.client.post("/api/bioxp/service/restart", json={})
        assert duplicate.status_code == 409
        assert len(harness.calls) == 1
    finally:
        harness.process.release.set()
        assert (await first).status_code == 200
    assert len(harness.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_http_request_reaps_child_and_releases_lock(harness):
    harness.process.release.clear()
    request = asyncio.create_task(harness.client.post("/api/bioxp/service/restart", json={}))
    await asyncio.wait_for(harness.process.started.wait(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert harness.process.kills == harness.process.waits == 1
    assert len(harness.calls) == 1
    assert (await harness.client.get("/api/bioxp/service")).json()["restart_in_progress"] is False


@pytest.mark.asyncio
async def test_cancellation_during_spawn_and_cleanup_keeps_child_owned(harness, monkeypatch):
    launching = asyncio.Event()
    release_spawn = asyncio.Event()
    reaping = asyncio.Event()
    release_reap = asyncio.Event()

    async def spawn(*args, **kwargs):
        harness.calls.append((args, kwargs))
        launching.set()
        await release_spawn.wait()
        return harness.process

    async def wait():
        harness.process.waits += 1
        reaping.set()
        await release_reap.wait()
        return -9

    monkeypatch.setattr(control.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(harness.process, "wait", wait)
    request = asyncio.create_task(harness.client.post("/api/bioxp/service/restart", json={}))
    await asyncio.wait_for(launching.wait(), 1)
    request.cancel()
    await asyncio.sleep(0)
    release_spawn.set()
    await asyncio.wait_for(reaping.wait(), 1)
    request.cancel()
    await asyncio.sleep(0)
    assert harness.owner._restart_lock.locked()
    release_reap.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert harness.process.kills == harness.process.waits == 1
    assert len(harness.calls) == 1

"""Reboot, thermal readings relay and saved deck contents for the Live deck tab."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from routers.bioxp import connection as routes
from routers.bioxp import operator_controls, router
from services.bioxp import service_control as control
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.models import BioXpProfile
from services.bioxp.profile_store import BioXpProfileStore
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES
from services.bioxp.target_policy import BioXpTargetPolicy


class Process:
    def __init__(self, code=0):
        self.returncode = None
        self.code = code

    async def communicate(self):
        self.returncode = self.code
        return b"", None

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    monkeypatch.setenv("BMS_BIOXP_SERVICE_SSH_TARGET", "operator@robot-fixture")
    monkeypatch.setenv("BMS_BIOXP_SERVICE_API_URL", "http://robot-fixture:8123")
    monkeypatch.setattr(operator_controls, "get_data_root", lambda: tmp_path)
    store = BioXpProfileStore(tmp_path / "profile.json")
    store.save(BioXpProfile(display_name="Offline fixture", api_url="http://robot-fixture:8123"))
    connection = BioXpConnectionService(store, BioXpTargetPolicy(allowed_hosts=["robot-fixture"]))
    owner = control.BioXpServiceControl()
    monkeypatch.setattr(routes, "service_control", owner)
    state = SimpleNamespace(process=Process(), calls=[])

    async def spawn(*args, **kwargs):
        state.calls.append(args)
        return state.process

    monkeypatch.setattr(control.asyncio, "create_subprocess_exec", spawn)
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=connection)
    app.include_router(router, prefix="/api/bioxp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, connection=connection, state=state, owner=owner, tmp=tmp_path)
    assert not owner._restart_lock.locked()


@pytest.mark.asyncio
async def test_reboot_runs_an_operating_system_reboot_once(harness):
    response = await harness.client.post("/api/bioxp/service/reboot", json={})
    assert response.status_code == 200
    assert response.json() == {"reboot_requested": True}
    assert len(harness.state.calls) == 1
    command = harness.state.calls[0][-1]
    assert harness.state.calls[0][-2] == "operator@robot-fixture"
    assert command.endswith("systemctl reboot") and "bioxp-api" not in command


@pytest.mark.asyncio
async def test_reboot_failure_is_uncertain_and_not_retried(harness):
    harness.state.process = Process(code=255)
    response = await harness.client.post("/api/bioxp/service/reboot", json={})
    assert response.status_code == 502
    assert "No automatic retry" in response.json()["detail"]
    assert len(harness.state.calls) == 1


@pytest.mark.asyncio
async def test_reboot_needs_configuration_and_mutation_access(harness, monkeypatch):
    monkeypatch.setenv("BMS_BIOXP_SERVICE_SSH_TARGET", "")
    assert (await harness.client.post("/api/bioxp/service/reboot", json={})).status_code == 503
    monkeypatch.setenv("BMS_BIOXP_SERVICE_SSH_TARGET", "operator@robot-fixture")
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    assert (await harness.client.post("/api/bioxp/service/reboot", json={})).status_code == 503
    assert harness.state.calls == []


@pytest.mark.asyncio
async def test_thermal_readings_relay_the_robot_reply_unchanged(harness, monkeypatch):
    assert DEFAULT_ROBOT_ROUTES["thermal_fan_services"][:2] == ("GET", "/thermal/fan-services")
    reply = {"services": [{"board": 7, "axis": 0, "current_c": 21.4, "pedestal_c": 28.0, "fan_speed": 0}]}
    query = AsyncMock(return_value=reply)
    monkeypatch.setattr(harness.connection, "request_active_query", query)
    response = await harness.client.get("/api/bioxp/operator-controls/thermal/readings")
    assert response.status_code == 200 and response.json() == reply
    assert query.await_args.args == ("thermal_fan_services",)
    assert query.await_args.kwargs["require_fresh"] is False


@pytest.mark.asyncio
async def test_deck_contents_persist_and_clear_per_station_or_whole_deck(harness):
    url = "/api/bioxp/operator-controls/deck/contents"
    assert (await harness.client.get(url)).json()["wells"] == {}
    await harness.client.put(url, json={"station": "LOC_RC", "wells": {"A1": 50, "A2": 12.5}})
    saved = await harness.client.put(url, json={"station": "LOC_OC", "wells": {"A1": 0}})
    assert saved.json()["wells"] == {"LOC_RC": {"A1": 50.0, "A2": 12.5}, "LOC_OC": {"A1": 0.0}}
    # A new store on the same file (an API restart) reads the same record.
    assert (harness.tmp / "bioxp" / "deck_contents.json").exists()
    assert (await harness.client.get(url)).json()["wells"]["LOC_RC"] == {"A1": 50.0, "A2": 12.5}
    cleared = await harness.client.put(url, json={"station": "LOC_RC", "wells": {"A2": None}})
    assert cleared.json()["wells"]["LOC_RC"] == {"A1": 50.0}
    assert (await harness.client.delete(url, params={"station": "LOC_RC"})).json()["wells"] == {"LOC_OC": {"A1": 0.0}}
    assert (await harness.client.delete(url)).json()["wells"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"station": "../etc", "wells": {"A1": 1}}, {"station": "LOC_RC", "wells": {"Z9": 1}},
    {"station": "LOC_RC", "wells": {"A1": -1}}, {"station": "LOC_RC", "wells": {}},
])
async def test_deck_contents_reject_bad_input_without_writing(harness, body):
    response = await harness.client.put("/api/bioxp/operator-controls/deck/contents", json=body)
    assert response.status_code == 422
    assert not (harness.tmp / "bioxp" / "deck_contents.json").exists()

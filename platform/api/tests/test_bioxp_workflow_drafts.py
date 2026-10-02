"""Standalone draft CRUD through real HTTP and durable UserTemplate SQLite."""
from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Job, UserTemplate, get_session
from routers.user_templates import router
from services.bioxp.robot_client import BioXpRobotClient

URL = "/api/user-templates"
EMPTY = {"schema": "bms.bioxp-workflow-draft.v1", "steps": [], "editor_state": {}}


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'drafts.db'}"
    robot_calls = []
    statements = []

    def forbid_robot(*args, **kwargs):
        robot_calls.append((args, kwargs))
        raise AssertionError("Draft CRUD must not construct a robot client")

    async def forbid_network(*args, **kwargs):
        raise AssertionError("Draft CRUD must not make outbound HTTP calls")

    monkeypatch.setattr(BioXpRobotClient, "__init__", forbid_robot)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)

    @asynccontextmanager
    async def open_store():
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: UserTemplate.__table__.create(conn, checkfirst=True))
            await connection.run_sync(lambda conn: Job.__table__.create(conn, checkfirst=True))
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda conn, cursor, statement, parameters, context, many: statements.append(statement))
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def session_dependency():
            async with sessions() as session:
                yield session

        app = FastAPI()
        app.include_router(router, prefix=URL)
        app.dependency_overrides[get_session] = session_dependency
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client, sessions
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Job)) == 0
        finally:
            await engine.dispose()

    yield open_store
    assert robot_calls == []
    # No Project/Domain lookup, job submission, compiler or other persistence owner.
    writes = [sql.lower().strip() for sql in statements
              if sql.lower().strip().startswith(("insert", "update", "delete"))]
    assert all("user_templates" in sql for sql in writes)


async def create(client, name="draft", params=None, **kwargs):
    return await client.post(URL, json={"name": name, "mode": "bioxp_workflow",
        "model_id": None, "base_template_id": None,
        "params": EMPTY if params is None else params, **kwargs})


@pytest.mark.asyncio
async def test_empty_incomplete_edit_reorder_and_reopen_sqlite(store):
    async with store() as (client, sessions):
        response = await create(client)
        assert response.status_code == 201, response.text
        saved = response.json()
        identifier = saved["id"]
        assert saved["params"] == EMPTY
        assert saved["mode"] == "bioxp_workflow"
        assert saved["model_id"] is saved["base_template_id"] is None
    # A fresh app, engine and sessions must recover the empty named workflow.
    async with store() as (client, sessions):
        assert (await client.get(f"{URL}/{identifier}")).json()["params"] == EMPTY
        incomplete = {**EMPTY, "steps": [
            {"step_id": "pipette-α", "intent": {"volume": "", "enabled": False, "count": 0,
                "source": None, "unknown_science": {"nested": [None, False, 0, "", {}, []]}}},
            {"step_id": "untouched", "intent": {}}],
            "editor_state": {"selected": None, "zoom": 0, "extra": {"future": True}}}
        response = await client.put(f"{URL}/{identifier}", json={"params": incomplete})
        assert response.status_code == 200, response.text
        assert response.json()["params"] == incomplete
        edited = copy.deepcopy(incomplete)
        edited["steps"].reverse()
        edited["steps"][1]["intent"]["volume"] = "0.0000000000001"
        assert (await client.put(f"{URL}/{identifier}", json={"params": edited})).json()["params"] == edited
        response = await client.put(f"{URL}/{identifier}", json={"name": "edited standalone"})
        assert response.status_code == 200
        assert response.json()["params"] == edited
    async with store() as (client, sessions):
        saved = (await client.get(f"{URL}/{identifier}")).json()
        assert saved["name"] == "edited standalone"
        assert saved["params"] == edited
        assert [row["step_id"] for row in saved["params"]["steps"]] == ["untouched", "pipette-α"]
        async with sessions() as session:
            raw = await session.scalar(text("SELECT params FROM user_templates WHERE id = :id"), {"id": identifier})
            assert raw == json.dumps(edited)
            assert json.loads(raw) == edited
            assert "omitted" not in json.loads(raw)["steps"][1]["intent"]
        assert (await client.delete(f"{URL}/{identifier}")).status_code == 204
        assert (await client.get(f"{URL}/{identifier}")).status_code == 404


BAD_PARAMS = [
    {}, {**EMPTY, "schema": "wrong"}, {**EMPTY, "extra": 1},
    {"schema": EMPTY["schema"], "steps": []},
    {**EMPTY, "steps": {}}, {**EMPTY, "editor_state": []},
    {**EMPTY, "steps": [None]},
    *[{**EMPTY, "steps": [row]} for row in [
        {}, {"step_id": "x"}, {"step_id": "", "intent": {}},
        {"step_id": 1, "intent": {}}, {"step_id": "x", "intent": []},
        {"step_id": "x", "intent": {}, "extra": None}]],
    {**EMPTY, "steps": [{"step_id": "same", "intent": {}}, {"step_id": "same", "intent": {}}]},
    *[{**EMPTY, "editor_state": {"nested": [value]}} for value in [float("nan"), float("inf"), -float("inf")]],
    {**EMPTY, "steps": [{"step_id": "x", "intent": {"value": float("inf")}}]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", BAD_PARAMS)
async def test_malformed_create_and_update_are_atomic(store, params):
    async with store() as (client, sessions):
        response = await client.post(URL, content=json.dumps({"name": "bad", "mode": "bioxp_workflow", "params": params}),
                                     headers={"content-type": "application/json"})
        assert response.status_code == 422, response.text
        assert (await client.get(URL)).json() == []
        identifier = (await create(client)).json()["id"]
        response = await client.put(f"{URL}/{identifier}", content=json.dumps({"name": "must not persist", "params": params}),
                                    headers={"content-type": "application/json"})
        assert response.status_code == 422, response.text
        saved = (await client.get(f"{URL}/{identifier}")).json()
        assert saved["name"] == "draft"
        assert saved["params"] == EMPTY


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["model_id", "base_template_id"])
async def test_standalone_identity(store, field):
    async with store() as (client, sessions):
        assert (await create(client, **{field: "not-standalone"})).status_code == 422
        assert (await client.get(URL)).json() == []


@pytest.mark.asyncio
async def test_effective_existing_mode_and_null_update(store):
    async with store() as (client, sessions):
        identifier = (await create(client)).json()["id"]
        assert (await client.put(f"{URL}/{identifier}", json={"params": None})).status_code == 422
        # Existing malformed rows are validated on a name-only PUT too.
        async with sessions() as session:
            row = await session.get(UserTemplate, identifier)
            row.params = {}
            await session.commit()
        assert (await client.put(f"{URL}/{identifier}", json={"name": "rename"})).status_code == 422
        assert (await client.get(f"{URL}/{identifier}")).json()["name"] == "draft"
        assert (await client.put(f"{URL}/{identifier}", json={"params": EMPTY})).status_code == 200


@pytest.mark.asyncio
async def test_exact_filters_search_model_pagination_and_non_bioxp_regression(store):
    async with store() as (client, sessions):
        draft = (await create(client, name="needle draft")).json()
        others = []
        arbitrary = {"steps": "not a BioXP envelope", "value": False, "zero": 0, "null": None}
        for index, mode in enumerate([None, "campaign", "BIOXP_WORKFLOW", "bioxp_workflow-extra", ""]):
            response = await client.post(URL, json={"name": f"other {index}", "description": "needle description",
                "mode": mode, "model_id": "native", "params": arbitrary})
            assert response.status_code == 201, response.text
            others.append(response.json())
        assert len((await client.get(URL)).json()) == 6
        assert [row["id"] for row in (await client.get(URL, params={"mode": "bioxp_workflow"})).json()] == [draft["id"]]
        excluded = (await client.get(URL, params={"exclude_mode": "bioxp_workflow"})).json()
        assert {row["id"] for row in excluded} == {row["id"] for row in others}
        assert (await client.get(URL, params={"mode": "bioxp_workflow", "exclude_mode": "bioxp_workflow"})).json() == []
        assert len((await client.get(URL, params={"mode": ""})).json()) == 1
        assert len((await client.get(URL, params={"search": "needle", "model_id": "native", "exclude_mode": "bioxp_workflow"})).json()) == 5
        assert (await client.get(URL, params={"search": "missing", "mode": "bioxp_workflow"})).json() == []
        page = (await client.get(URL, params={"exclude_mode": "bioxp_workflow", "offset": 1, "limit": 2})).json()
        assert [row["id"] for row in page] == [row["id"] for row in excluded[1:3]]
        identifier = others[0]["id"]
        assert (await client.put(f"{URL}/{identifier}", json={"params": {}})).json()["params"] == {}
        assert (await client.put(f"{URL}/{identifier}", json={"params": None})).json()["params"] == {}
        assert (await client.get(f"{URL}/{others[1]['id']}")).json()["params"] == arbitrary
        assert (await create(client, name="needle draft")).status_code == 400

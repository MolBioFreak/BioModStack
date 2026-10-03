"""Public HTTP lifecycle with real file SQLite and existing relay contracts.

Pure compiler replaced at its function seam only; this suite does NOT establish
native compilation or physical qualification. Parent integrated suites own that.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import UserTemplate, get_session
from migrations.add_user_template_revisions import migrate
from routers.bioxp import router
from routers.bioxp import methods
from routers.user_templates import router as template_router
from services.bioxp.errors import RobotTimeoutError, RobotResponseError, ConnectionStateError
from test_bioxp_protocol_relay import bundle, control_receipt

BASE = "/api/bioxp/methods"
RAW = {"schema": "bms.bioxp-method.v1", "name": "Incomplete α", "steps": [],
       "unknown": {"null": None, "blank": "", "decimal": "0.0001000", "false": False, "zero": 0},
       "editor_state": {"collapsed": ["unknown"]}}
KEY = "original-key"
JOB = "protocol-live-" + sha256(KEY.encode()).hexdigest()


def compile_seam(request):
    return {"document": {"protocol_id": "method", "stages": []}, "digest": "seam-only",
            "resolved": {"requested": deepcopy(request)}, "dependencies": deepcopy(request.get("dependencies", {})),
            "issues": [{"code": "unknown_fill", "category": "advisory", "path": "/deck_plan"}],
            "water_substitutions": [{"path": "/steps/0/speed", "revision": "pinned-Water", "value": 50}],
            "provenance": [], "simulation": {"status": "unknown"}}


class NativeTransport:
    def __init__(self):
        self.calls = []
        self.payload = None
        self.error = None
        self.entered = asyncio.Event()
        self.release = None

    async def request(self, route, **kwargs):
        self.calls.append((route, deepcopy(kwargs)))
        if route == "protocol_execute":
            body = kwargs["json_data"]
            payload = bundle()
            payload["job_id"] = JOB
            payload["command"].update(command_id=JOB, idempotency_key=body["idempotency_key"].strip())
            state = payload["execution"]["runtime_state"]
            state["job_id"] = JOB
            state["workflow"]["command_id"] = JOB
            payload["protocol"]["document"] = deepcopy(body["document"])
            self.payload = payload  # accepted even when reply gets lost
            self.entered.set()
            if self.release is not None:
                await self.release.wait()
            if self.error:
                raise self.error
            return deepcopy(payload)
        if self.error:
            raise self.error
        if route == "protocol_jobs":
            return {"rows": [deepcopy(self.payload)] if self.payload else []}
        if route in ("protocol_job", "protocol_review"):
            return deepcopy(self.payload)
        if route == "protocol_control":
            receipt = control_receipt()
            receipt.update(job_id=JOB, command_id=JOB)
            return receipt
        raise AssertionError(route)


class Connection:
    generation = 77
    def __init__(self, transport):
        self.transport = transport

    @asynccontextmanager
    async def active_request_lease(self, *, expected_generation, require_fresh):
        if expected_generation != 77:
            raise ConnectionStateError("Connection generation changed")
        assert require_fresh is False
        yield self.transport

    async def request_active_v2_query(self, route, *, expected_generation, **kwargs):
        assert expected_generation == 77
        return await self.transport.request(route, **kwargs)


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    path = tmp_path / "methods.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda connection: UserTemplate.__table__.create(connection))
    migrate(path)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def session_dependency():
        async with sessions() as session:
            yield session
    transport = NativeTransport()
    app = FastAPI()
    app.include_router(router, prefix="/api/bioxp")
    app.include_router(template_router, prefix="/api/user-templates")
    app.dependency_overrides[get_session] = session_dependency
    app.state.bioxp_runtime = SimpleNamespace(connection=Connection(transport))
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    monkeypatch.setattr(methods, "compile_method", compile_seam)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, transport, path, sessions
    await engine.dispose()


async def create(client, raw=None, **kwargs):
    response = await client.post(BASE + "/library", json={"method": deepcopy(RAW if raw is None else raw), **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def run_body(**kwargs):
    return {"idempotency_key": KEY, "expected_generation": 77, "acknowledge_live": True,
            "bindings": {"n": "0.00100"}, "dependencies": {"water": {"revision": 3, "speed": "50.0"}}, **kwargs}


@pytest.mark.asyncio
async def test_raw_revision_lifecycle_and_stale_writer(store):
    client, transport, path, _ = store
    saved = await create(client)
    url = BASE + "/library/" + saved["id"]
    assert saved["revision"] == 1 and saved["method"] == RAW
    changed = deepcopy(RAW)
    changed["unknown"].pop("null")
    changed["unknown"]["false"] = 0
    response = await client.put(url, json={"method": changed, "expected_base_revision": 1, "description": None})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    stale = await client.put(url, json={"method": RAW, "expected_base_revision": 1})
    assert stale.status_code == 409
    assert (await client.get(url)).json()["method"] == changed
    assert (await client.get(url + "/revisions/1")).json()["method"] == RAW
    assert [r["revision"] for r in (await client.get(url + "/revisions?limit=1&offset=1")).json()] == [1]
    difference = (await client.get(url + "/diff?from_revision=1&to_revision=2")).json()
    assert {c["path"] for c in difference["changes"]} == {"/unknown/null", "/unknown/false"}
    # Legacy generic endpoint cannot bypass expected revision checks.
    bypass = await client.put("/api/user-templates/" + saved["id"], json={"params": RAW})
    assert bypass.status_code == 409
    with sqlite3.connect(path) as db:
        raw = json.loads(db.execute("SELECT params FROM user_templates").fetchone()[0])
        assert raw == changed
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE user_template_revisions SET revision=999")
    assert not transport.calls


@pytest.mark.asyncio
async def test_concurrent_put_exactly_one_winner(store):
    client, _, _, _ = store
    saved = await create(client)
    url = BASE + "/library/" + saved["id"]
    results = await asyncio.gather(*[
        client.put(url, json={"method": {**RAW, "writer": writer}, "expected_base_revision": 1})
        for writer in ("one", "two")])
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    assert (await client.get(url)).json() == winner
    assert len((await client.get(url + "/revisions")).json()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["library", "liquid-classes", "presets"])
async def test_collection_import_export_duplicate_is_one_storage_owner(store, collection):
    client, transport, path, _ = store
    raw = {**RAW, "schema": methods.SCHEMAS[collection]}
    url = BASE + "/" + collection
    imported = await client.post(url + "/import", json={"method": raw})
    assert imported.status_code == 201, imported.text
    saved = imported.json()
    exported = (await client.get(url + "/" + saved["id"] + "/export?revision=1")).json()
    assert exported["method"] == raw
    reimported = await client.post(url + "/import", json={**exported, "name": "portable imported copy"})
    assert reimported.status_code == 201 and reimported.json()["method"] == raw
    assert reimported.json()["id"] != exported["id"]
    assert (await client.post(url + "/import", json={"method": raw})).status_code == 400
    duplicate = await client.post(url + "/" + saved["id"] + "/duplicate", json={"name": "explicit new name", "revision": 1})
    assert duplicate.status_code == 201 and duplicate.json()["id"] != saved["id"]
    assert duplicate.json()["method"] == raw
    assert len((await client.get(url + "?limit=1&offset=1")).json()) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT DISTINCT mode, model_id, base_template_id FROM user_templates").fetchall() == [("bioxp_workflow", None, None)]
    assert not transport.calls


@pytest.mark.asyncio
async def test_legacy_revision_zero_preserved_during_explicit_migration(store):
    client, transport, _, _ = store
    original = {"schema": "bms.bioxp-workflow-draft.v1", "steps": [
        {"step_id": "raw", "intent": {"unknown": None, "volume": ""}, "required_capability": None}], "editor_state": {}}
    response = await client.post("/api/user-templates", json={"name": "legacy", "mode": "bioxp_workflow", "params": original})
    url = BASE + "/library/" + response.json()["id"]
    assert (await client.get(url)).json()["revision"] == 0
    assert (await client.put(url, json={"method": {**RAW, "legacy_original": original}, "expected_base_revision": 0})).status_code == 200
    assert (await client.get(url + "/revisions/0")).json()["method"] == original
    assert not transport.calls


@pytest.mark.asyncio
async def test_saved_snapshot_run_observe_control_review_report_recovery(store):
    client, transport, _, _ = store
    saved = await create(client)
    url = BASE + "/library/" + saved["id"]
    response = await client.post(url + "/runs", json=run_body(revision=1))
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["job_id"] == JOB
    snapshot = job["method_snapshot"]
    assert snapshot["method"] == RAW
    assert snapshot["compilation"]["water_substitutions"]
    assert snapshot["saved"]["revision"] == 1
    assert transport.calls[0][0] == "protocol_execute"
    assert transport.calls[0][1]["json_data"]["live_execution_ack"] is True
    # Subsequent head/class edits cannot alter the native embedded run.
    assert (await client.put(url, json={"method": {**RAW, "steps": [{"future": "changed"}]}, "expected_base_revision": 1})).status_code == 200
    runs = (await client.get(BASE + "/runs?limit=1")).json()
    assert runs["rows"][0]["job_id"] == JOB
    original = (await client.get(BASE + "/runs/" + JOB)).json()
    assert original["protocol"]["document"]["metadata"]["bms_method_run"] == snapshot
    control = {"expected_connection_generation": 77, "expected_ownership_generation": 7,
               "command_id": JOB, "idempotency_key": "control-original-key"}
    result = await client.post(BASE + "/runs/" + JOB + "/control", json={**control, "action": "continue", "gate": "ordinary_pause", "gate_id": "pause-1"})
    assert result.status_code == 200 and result.json()["reached"] is False
    result = await client.post(BASE + "/runs/" + JOB + "/review", json={**control, "stage_id": "s", "note": "checked"})
    assert result.status_code == 200, result.text
    report = (await client.get(BASE + "/runs/" + JOB + "/report")).json()
    assert report["method_snapshot"] == snapshot
    recovered = await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json={"occurrence": {"step_id": "aspirate", "loop_path": [2]}, "initial_state": {"tips": "unknown"}})
    assert recovered.status_code == 200
    assert recovered.json()["method"] == RAW
    assert recovered.json()["recovery"]["submitted"] is False
    assert recovered.json()["initial_state"] == {"tips": "unknown"}
    assert len([r for r, _ in transport.calls if r == "protocol_execute"]) == 1
    assert (await client.get(BASE + "/runs/" + JOB)).json() == original
    clone = (await client.post(BASE + "/runs/" + JOB + "/clone")).json()
    assert clone["method"] == RAW and clone["submitted"] is False


@pytest.mark.asyncio
async def test_quick_snapshot_mutation_and_timeout_retains_original_identity(store):
    client, transport, _, _ = store
    transport.error = RobotTimeoutError("lost after acceptance", dispatched=True)
    response = await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    assert response.status_code == 504, response.text
    detail = response.json()["detail"]
    assert detail["job_id"] == JOB and detail["idempotency_key"] == KEY
    assert detail["expected_generation"] == 77 and detail["delivery"] == "uncertain"
    assert detail["method_snapshot"]["method"] == RAW
    transport.error = None
    assert (await client.get(BASE + "/runs/" + JOB)).json()["job_id"] == JOB
    assert [r for r, _ in transport.calls].count("protocol_execute") == 1


@pytest.mark.asyncio
async def test_submit_freezes_before_async_network_and_late_save(store):
    client, transport, _, _ = store
    saved = await create(client)
    transport.release = asyncio.Event()
    task = asyncio.create_task(client.post(BASE + "/library/" + saved["id"] + "/runs", json=run_body(revision=1)))
    await transport.entered.wait()
    assert (await client.put(BASE + "/library/" + saved["id"], json={"method": {**RAW, "late": True}, "expected_base_revision": 1})).status_code == 200
    transport.release.set()
    response = await task
    assert response.json()["method_snapshot"]["method"] == RAW


@pytest.mark.asyncio
async def test_compile_disconnected_no_gate_from_advisory_and_null_document_no_submit(store, monkeypatch):
    client, transport, _, _ = store
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    saved = await create(client)
    response = await client.post(BASE + "/check", json={"method": RAW})
    assert response.status_code == 200 and response.json()["document"] is not None
    assert not transport.calls
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    monkeypatch.setattr(methods, "compile_method", lambda request: {"document": None, "issues": [{"path": "/steps/0", "code": "missing_volume"}]})
    response = await client.post(BASE + "/library/" + saved["id"] + "/runs", json=run_body(revision=1))
    assert response.status_code == 422 and response.json()["detail"]["delivery"] == "not_submitted"
    assert not transport.calls


@pytest.mark.asyncio
async def test_missing_snapshot_recovery_is_explicit_no_guess_and_retired_routes_absent(store):
    client, transport, _, _ = store
    await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    transport.payload["protocol"]["document"].pop("metadata")
    response = await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "lossless_reconstruction_unavailable"
    assert (await client.post("/api/bioxp/operator-controls/v2/methods", json={})).status_code == 404
    assert (await client.get("/api/bioxp/operator-controls/v2/methods/old")).status_code == 404
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert BASE + "/library/{template_id}/runs" in paths
    assert "/api/bioxp/operator-controls/v2/actions/{action_id}" in paths


@pytest.mark.asyncio
async def test_reopen_exact_snapshot_with_new_engine(store):
    client, _, path, _ = store
    saved = await create(client)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    from services.user_template_revisions import exact_revision
    async with sessions() as session:
        template = await session.get(UserTemplate, saved["id"])
        assert await exact_revision(session, template, 1) == saved
    await engine.dispose()
    migrate(path)  # explicit migration is repeatable; never edits raw content
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute("SELECT snapshot FROM user_template_revisions").fetchone()[0])["method"] == RAW


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatched", [False, True])
async def test_timeout_delivery_classification_does_not_use_http_status(store, dispatched):
    client, transport, _, _ = store
    transport.error = RobotTimeoutError("timeout", dispatched=dispatched)
    response = await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    assert response.status_code == 504
    assert response.json()["detail"]["delivery"] == ("uncertain" if dispatched else "not_submitted")


@pytest.mark.asyncio
async def test_key_body_conflict_retains_native_reason_and_original_identity(store):
    client, transport, _, _ = store
    reason = {"code": "idempotency_conflict", "message": "same key, changed body"}
    transport.error = RobotResponseError(409, reason)
    response = await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["native_reason"] == reason and detail["job_id"] == JOB
    assert detail["delivery"] == "uncertain"  # conflict doesn't certify no previous motion
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_partial_occurrence_report_keeps_children_and_native_results(store):
    client, transport, _, _ = store
    await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    payload = transport.payload
    snapshot = payload["protocol"]["document"]["metadata"]["bms_method_run"]
    snapshot["compilation"]["provenance"] = [
        {"occurrence_id": "transfer-loop2", "step_id": "transfer", "loop_path": [2],
         "native_action_ids": ["pickup", "aspirate", "dispense"]}]
    state = payload["execution"]["runtime_state"]
    state["stage_states"] = {"stage": {"stage_id": "stage", "title": None, "status": "failed",
        "review_required": False, "current_action_id": "aspirate", "completed_actions": ["pickup"],
        "pause_marker_action_id": None}}
    state["action_results"] = [{"action_id": "aspirate", "ok": False, "result": {"partial": True, "applied": None}}]
    report = (await client.get(BASE + "/runs/" + JOB + "/report")).json()
    occurrence = report["occurrences"][0]
    assert occurrence["status"] == "failed"
    assert [c["status"] for c in occurrence["children"]] == ["completed", "failed", "unknown"]
    assert occurrence["children"][1]["results"] == state["action_results"]
    assert report["duration"]["status"] == "unknown"
    recovered = (await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json={"occurrence": {"occurrence_id": "transfer-loop2", "action_id": "aspirate"}})).json()
    assert recovered["method"] == RAW and recovered["recovery"]["automatic_setup"] == []


@pytest.mark.asyncio
async def test_discovery_uses_model_function_seams_and_openapi(store, monkeypatch):
    # This is facade forwarding coverage, not scientific schema qualification.
    import sys
    client, transport, _, _ = store
    monkeypatch.setitem(sys.modules, "bioxp_method_model", SimpleNamespace(
        method_schema=lambda: {"type": "object", "test_seam": True},
        method_catalog=lambda: {"actions": [], "test_seam": True},
        method_examples=lambda: [{"method": RAW, "test_seam": True}],
        migrate_legacy=lambda draft: {"original": draft, "test_seam": True}))
    source = '{"precision": 50.00, "blank": null}'
    monkeypatch.setitem(sys.modules, "bioxp_method_liquids", SimpleNamespace(
        starter_entries=lambda: [{"schema": "bms.bioxp-liquid-class.v1", "test_seam": True}],
        source_catalog_text=lambda: source))
    assert (await client.get(BASE + "/catalog")).json()["test_seam"] is True
    schema = (await client.get(BASE + "/schema")).json()
    assert schema["method"]["test_seam"] is True
    assert "expected_base_revision" in schema["requests"]["DraftUpdate"]["required"]
    assert (await client.get(BASE + "/examples")).json()[0]["method"] == RAW
    assert "MethodRunResponse" in schema["results"]
    assert (await client.get(BASE + "/liquid-classes/starters")).json()[0]["test_seam"] is True
    assert (await client.get(BASE + "/liquid-classes/source")).text == source
    assert (await client.post(BASE + "/migrate", json={"method": RAW})).json()["original"] == RAW
    assert not transport.calls


@pytest.mark.asyncio
async def test_generation_lease_refusal_proves_this_attempt_not_submitted(store):
    client, transport, _, _ = store
    response = await client.post(BASE + "/quick-runs", json=run_body(method=RAW, expected_generation=76))
    assert response.status_code == 409
    assert response.json()["detail"]["delivery"] == "not_submitted"
    assert response.json()["detail"]["job_id"] == JOB
    assert not transport.calls


@pytest.mark.asyncio
async def test_warm_read_failure_does_not_block_control_or_retry_submission(store):
    client, transport, _, _ = store
    await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    transport.error = RobotResponseError(404, "not found in this observation")
    assert (await client.get(BASE + "/runs/" + JOB)).status_code == 404
    transport.error = None
    response = await client.post(BASE + "/runs/" + JOB + "/control", json={
        "expected_connection_generation": 77, "expected_ownership_generation": 7,
        "command_id": JOB, "idempotency_key": "control-original-key", "action": "abort"})
    assert response.status_code == 200
    assert [name for name, _ in transport.calls].count("protocol_execute") == 1


@pytest.mark.asyncio
async def test_run_listing_window_search_does_not_claim_complete_history(store):
    client, transport, _, _ = store
    await client.post(BASE + "/quick-runs", json=run_body(method=RAW))
    page = (await client.get(BASE + "/runs?limit=1&search=" + JOB)).json()
    assert len(page["rows"]) == 1 and page["window"]["complete_history"] is False
    assert (await client.get(BASE + "/runs?offset=1&limit=1")).json()["rows"] == []
    assert (await client.get(BASE + "/runs?search=not-a-job")).json()["rows"] == []


@pytest.mark.asyncio
async def test_late_save_response_returns_its_accepted_revision_not_newer_head(store, monkeypatch):
    from routers import user_templates
    client, _, _, sessions = store
    saved = await create(client)
    original_update = user_templates.update_user_template
    async def update_then_other_writer(template_id, data, session):
        accepted = await original_update(template_id, data, session)
        async with sessions() as other:
            await original_update(template_id, user_templates.UserTemplateUpdate(
                params={**RAW, "writer": "later"}, expected_base_revision=2), other)
        return accepted
    monkeypatch.setattr(user_templates, "update_user_template", update_then_other_writer)
    url = BASE + "/library/" + saved["id"]
    response = await client.put(url, json={"method": {**RAW, "writer": "first"}, "expected_base_revision": 1})
    assert response.status_code == 200
    assert response.json()["revision"] == 2 and response.json()["method"]["writer"] == "first"
    current = (await client.get(url)).json()
    assert current["revision"] == 3 and current["method"]["writer"] == "later"

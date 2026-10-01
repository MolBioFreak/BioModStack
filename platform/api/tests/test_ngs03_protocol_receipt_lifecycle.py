"""Actual ONT HTTP owners with an inert host provider and isolated SQLite ledger."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import database
from database import Base, OntInstrumentRun, OntInstrumentRunPreflight, OntProtocolOptionReceipt
from migrations.add_ont_instrument_run_ledger import migrate as migrate_ledger
from migrations.add_ont_protocol_preflight import migrate as migrate_preflight
from migrations.add_ont_terminal_artifact_manifests import migrate as migrate_manifests
from migrations.sqlite_sha256 import register_sqlite_sha256
from routers import ont_runs
from services import ont_run_control as owner


@pytest_asyncio.fixture
async def lane(monkeypatch, tmp_path):
    path = tmp_path / "ngs03.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool,
                                 json_serializer=database._canonical_sqlite_json)

    @event.listens_for(engine.sync_engine, "connect")
    def connect(connection, _record):
        register_sqlite_sha256(connection)
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    migrate_ledger(str(path))
    migrate_preflight(str(path))
    migrate_manifests(str(path))
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(owner, "async_session", factory)
    state = SimpleNamespace(
        now=datetime(2026, 1, 1), calls=[], writes=[], begins=0, factory=factory,
        payload={"position": "X1", "device_type": "mk1d", "can_start": True,
                 "blockers": [], "protocol_id": "PRIVATE-PROTOCOL", "kit": "PRIVATE-KIT",
                 "basecalling_enabled": True, "basecalling_options": {"simplex_models": ["sup"]},
                 "output_directories": {"reads": "/private/output"},
                 "flow_cell": {"present": True, "flow_cell_id": "FC-001", "product_code": "FLO-MIN114"}},
    )
    monkeypatch.setattr(owner, "_utc_now", lambda: state.now)

    def host(method, route, *args, **kwargs):
        # Leave get_position_protocol_options itself intact, including its fallback.
        assert method == "GET"
        assert route.endswith("/protocol-options")
        state.calls.append((method, route))
        return copy.deepcopy(state.payload)

    monkeypatch.setattr(owner, "request_host_agent", host)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def count(_connection, _cursor, statement, _parameters, _context, _many):
        if statement == "BEGIN IMMEDIATE":
            state.begins += 1
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            state.writes.append(statement)

    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        state.client = client
        yield state
    await engine.dispose()


async def catalog(lane, position="X1"):
    response = await lane.client.get(f"/api/ont/positions/{position}/protocol-options")
    assert response.status_code == 200, response.text
    assert "PRIVATE" not in response.text and "/private" not in response.text
    return response.json()


def intent_payload(option):
    return {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"],
            "sample_id": "sample-1", "experiment_group": "group-1"}


async def rows(lane, model):
    async with lane.factory() as session:
        return (await session.scalar(select(func.count()).select_from(model)))


@pytest.mark.asyncio
async def test_passive_reads_keep_original_expiry_and_zero_warm_writes(lane, record_property):
    options = []
    for _ in range(6):
        options.append((await catalog(lane))["options"][0])
        lane.now += timedelta(seconds=10)
    inserts = sum(s.startswith("INSERT INTO ont_protocol_option_receipts") for s in lane.writes)
    record_property("passive_reads", 6)
    record_property("provider_reads", len(lane.calls))
    record_property("receipt_inserts", inserts)
    record_property("durable_rows", await rows(lane, OntProtocolOptionReceipt))
    assert len(lane.calls) == 6
    assert inserts == 1
    assert len(lane.writes) == 1
    assert all(option == options[0] for option in options)
    async with lane.factory() as session:
        receipt = await session.get(OntProtocolOptionReceipt, options[0]["option_receipt_id"])
        assert receipt.created_at == datetime(2026, 1, 1)
        assert receipt.expires_at == datetime(2026, 1, 1, 0, 10)
        assert receipt.consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["position", "flowcell", "flowcell_metadata", "protocol", "kit", "basecalling", "model", "output"])
async def test_context_change_never_reuses_incompatible_receipt(lane, change):
    first = (await catalog(lane))["options"][0]
    position = "X1"
    if change == "position":
        position = lane.payload["position"] = "X2"
    elif change == "flowcell":
        lane.payload["flow_cell"]["flow_cell_id"] = "FC-002"
    elif change == "flowcell_metadata":
        lane.payload["flow_cell"]["sample_rate"] = 5000
    elif change == "protocol":
        lane.payload["protocol_id"] = "PRIVATE-OTHER"
    elif change == "kit":
        lane.payload["kit"] = "PRIVATE-OTHER-KIT"
    elif change == "basecalling":
        lane.payload["basecalling_enabled"] = False
    elif change == "model":
        lane.payload["basecalling_options"] = {"simplex_models": ["hac"]}
    else:
        lane.payload["output_directories"] = {"reads": "/private/other"}
    second = (await catalog(lane, position))["options"][0]
    assert second["option_receipt_id"] != first["option_receipt_id"]
    assert (await catalog(lane, position))["options"][0] == second
    assert await rows(lane, OntProtocolOptionReceipt) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unsupported", "unreachable", "absent", "running", "malformed"])
async def test_each_passive_read_checks_current_device_before_reuse(lane, change):
    first = (await catalog(lane))["options"][0]
    original = copy.deepcopy(lane.payload)
    if change == "unsupported":
        lane.payload["device_type"] = "promethion"
    elif change == "unreachable":
        lane.payload.update(can_start=False, blockers=["host_unreachable"])
    elif change == "absent":
        lane.payload["flow_cell"]["present"] = False
    elif change == "running":
        lane.payload.update(can_start=False, blockers=["position_already_running"])
    else:
        lane.payload["basecalling_enabled"] = "true"
    blocked = await catalog(lane)
    assert blocked["can_start"] is False and blocked["options"] == []
    assert len(lane.writes) == 1
    lane.payload = original
    assert (await catalog(lane))["options"][0] == first
    assert len(lane.calls) == 3 and len(lane.writes) == 1


@pytest.mark.asyncio
async def test_expiry_boundary_rejects_old_intent_and_mints_without_renewal(lane):
    first = (await catalog(lane))["options"][0]
    lane.now += timedelta(minutes=10)
    rejected = await lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(first))
    assert rejected.status_code == 422
    second = (await catalog(lane))["options"][0]
    assert second["option_receipt_id"] != first["option_receipt_id"]
    async with lane.factory() as session:
        old = await session.get(OntProtocolOptionReceipt, first["option_receipt_id"])
        assert old.expires_at == datetime(2026, 1, 1, 0, 10)
        assert old.consumed_at is None
    assert await rows(lane, OntProtocolOptionReceipt) == 2


@pytest.mark.asyncio
async def test_consumption_binding_replay_and_disabled_genuine_start(lane):
    option = (await catalog(lane))["options"][0]
    for position, payload in [("X2", intent_payload(option)), ("X1", {**intent_payload(option), "option_id": "wrong"})]:
        assert (await lane.client.post(f"/api/ont/positions/{position}/run-intents", json=payload)).status_code == 422
    response = await lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(option))
    assert response.status_code == 200, response.text
    intent = response.json()
    assert intent["state"] == "armed" and intent["selected_option_id"] == option["option_id"]
    assert intent["sample_id"] == "sample-1" and intent["experiment_group"] == "group-1"
    async with lane.factory() as session:
        receipt = await session.get(OntProtocolOptionReceipt, option["option_receipt_id"])
        preflight = (await session.execute(select(OntInstrumentRunPreflight))).scalar_one()
        assert receipt.consumed_at == lane.now
        assert preflight.run_id == intent["id"] and preflight.option_receipt_id == receipt.id
        for field in ("source_digest", "capability_digest", "source_snapshot", "flow_cell_identity_sha256", "expires_at"):
            assert getattr(preflight, field) == getattr(receipt, field)
    assert (await lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(option))).status_code == 422
    new = (await catalog(lane))["options"][0]
    assert new["option_receipt_id"] != option["option_receipt_id"]
    started = await lane.client.post(f"/api/ont/runs/{intent['id']}/start", json={"confirm_start": True, "intent_generation": 1})
    assert started.status_code == 501  # Existing start authority stays disabled.
    assert len(lane.calls) == 4  # two catalog checks plus two fresh start checks
    assert await rows(lane, OntInstrumentRun) == 1


@pytest.mark.asyncio
async def test_concurrent_passive_reads_and_intent_race_are_single_owner(lane):
    catalogs = await asyncio.gather(*(catalog(lane) for _ in range(8)))
    option = catalogs[0]["options"][0]
    assert all(c["options"][0] == option for c in catalogs)
    assert await rows(lane, OntProtocolOptionReceipt) == 1
    responses = await asyncio.gather(*(lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(option)) for _ in range(8)))
    assert sorted(r.status_code for r in responses) == [200] + [422] * 7
    assert await rows(lane, OntInstrumentRun) == 1
    assert await rows(lane, OntInstrumentRunPreflight) == 1
    successor = (await catalog(lane))["options"][0]
    assert successor["option_receipt_id"] != option["option_receipt_id"]


@pytest.mark.asyncio
async def test_catalog_racing_consumption_never_replays_consumed_handle(lane):
    option = (await catalog(lane))["options"][0]
    intent, read = await asyncio.gather(
        lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(option)), catalog(lane))
    assert intent.status_code == 200
    # The read may serialize before consumption; it must never permit a second intent.
    candidate = read["options"][0]
    attempted = await lane.client.post("/api/ont/positions/X1/run-intents", json=intent_payload(candidate))
    assert attempted.status_code == (422 if candidate == option else 200)
    assert await rows(lane, OntInstrumentRun) == (1 if candidate == option else 2)


@pytest.mark.asyncio
async def test_expiry_is_checked_after_real_writer_lock_wait(lane):
    option = (await catalog(lane))["options"][0]
    async with lane.factory() as blocker:
        await blocker.execute(text("BEGIN IMMEDIATE"))
        target_begins = lane.begins + 2
        intent = asyncio.create_task(lane.client.post(
            "/api/ont/positions/X1/run-intents", json=intent_payload(option)))
        read = asyncio.create_task(catalog(lane))
        try:
            async with asyncio.timeout(2):
                while lane.begins < target_begins:
                    await asyncio.sleep(0.001)
            assert not intent.done() and not read.done()
            lane.now += timedelta(minutes=10)
        finally:
            await blocker.rollback()
        rejected, current = await asyncio.gather(intent, read)
    assert rejected.status_code == 422
    assert current["options"][0]["option_receipt_id"] != option["option_receipt_id"]
    assert await rows(lane, OntInstrumentRun) == 0
    assert await rows(lane, OntProtocolOptionReceipt) == 2


@pytest.mark.asyncio
async def test_legacy_device_provider_fallback_never_reuses_without_protocol(lane, monkeypatch):
    await catalog(lane)
    lane.payload = {}
    fallback_reads = []

    def position(_position):
        fallback_reads.append("position")
        return {"position": {"position": "X1", "device_type": "mk1d",
                             "flow_cell": {"present": True, "flow_cell_id": "FC-001"}}}

    def status():
        fallback_reads.append("status")
        return {"minknow": {"output_directories": {"reads": "/private/output"}}}

    monkeypatch.setattr(owner, "get_ont_position", position)
    monkeypatch.setattr(owner, "get_ont_status", status)
    result = await catalog(lane)
    assert result["can_start"] is False and result["options"] == []
    assert "protocol_unavailable" in result["blockers"]
    assert fallback_reads == ["position", "status"]
    assert len(lane.writes) == 1

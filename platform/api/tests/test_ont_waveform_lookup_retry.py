"""A single-read waveform lookup retries on request and needs space only for what it writes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, OntRawSignalLookup, OntRawSignalRepresentation
from services import ont_raw_signal

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning")]


@pytest_asyncio.fixture
async def sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lookups.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(OntRawSignalRepresentation(
            id="rep-1", run_id="run-1", observed_generation=1, role="source", source_kind="external_native",
            format="blow5", source_fidelity="external_native", state="ready", reason_code="validated",
            artifact_manifest={"schema": "test"}, manifest_sha256="a" * 64, parent_representation_ids=[],
            parent_manifest_sha256s=[], compression={}, runtime_identity={}, validation_receipts={}, read_count=1,
        ))
        await session.commit()
    monkeypatch.setattr(ont_raw_signal, "_validated_blow5_paths", lambda _representation, _read_id=None: (None, None))
    try:
        yield factory
    finally:
        await engine.dispose()


async def request(factory, read_id="read-1"):
    async with factory() as session:
        return await ont_raw_signal.request_waveform_lookup(
            session, run_id="run-1", observed_generation=1, representation_id="rep-1", read_id=read_id)


async def set_state(factory, lookup_id, **values):
    async with factory() as session:
        await session.execute(update(OntRawSignalLookup).where(OntRawSignalLookup.id == lookup_id).values(**values))
        await session.commit()


async def row_count(factory):
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(OntRawSignalLookup))).scalar_one()


@pytest.mark.asyncio
async def test_a_failed_lookup_runs_again_on_the_same_row_when_asked(sessions):
    first = await request(sessions)
    assert first["state"] == "requested"
    await set_state(sessions, first["lookup_id"], state="failed", reason_code="waveform_RuntimeError",
                    completed_at=datetime.utcnow())
    retried = await request(sessions)
    assert retried["lookup_id"] == first["lookup_id"]
    assert (retried["state"], retried["reason_code"]) == ("requested", "retry_requested")
    async with sessions() as session:
        row = await session.get(OntRawSignalLookup, first["lookup_id"])
        assert row.completed_at is None and row.claim_token is None and row.lease_expires_at is None
    assert await row_count(sessions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["requested", "running", "ready"])
async def test_a_lookup_that_is_waiting_running_or_done_is_returned_as_it_is(sessions, state):
    first = await request(sessions)
    values = {"state": state, "reason_code": state}
    if state == "running":
        values.update(claim_token="claim-1", lease_expires_at=datetime.utcnow() + timedelta(minutes=2))
    if state == "ready":
        values.update(sample_count=3, samples=[1.0, 2.0, 3.0])
    await set_state(sessions, first["lookup_id"], **values)
    again = await request(sessions)
    assert (again["lookup_id"], again["state"], again["reason_code"]) == (first["lookup_id"], state, state)
    if state == "running":
        async with sessions() as session:
            assert (await session.get(OntRawSignalLookup, first["lookup_id"])).claim_token == "claim-1"
    if state == "ready":
        assert again["samples"] == [1.0, 2.0, 3.0]
    assert await row_count(sessions) == 1


@pytest.mark.asyncio
async def test_requests_made_together_share_one_lookup(sessions):
    results = await asyncio.gather(*(request(sessions, "read-2") for _ in range(4)))
    assert len({item["lookup_id"] for item in results}) == 1
    assert await row_count(sessions) == 1


def test_space_needed_is_the_result_plus_a_copy_only_when_the_pair_is_copied():
    retained = OntRawSignalRepresentation(id="r", artifact_manifest={"retained_indexed_pair": True})
    copied = OntRawSignalRepresentation(id="c", artifact_manifest={})
    blow5, index = {"bytes": 30 * 1024 ** 3}, {"bytes": 5 * 1024 ** 2}
    result = ont_raw_signal._WAVEFORM_LOOKUP_OUTPUT_BYTES
    assert result < 64 * 1024 ** 2
    # Read in place: a 30 GB run needs no more room than the result file.
    assert ont_raw_signal._waveform_lookup_required_bytes(retained, blow5, index) == result
    assert ont_raw_signal._waveform_lookup_required_bytes(copied, blow5, index) == result + 30 * 1024 ** 3 + 5 * 1024 ** 2

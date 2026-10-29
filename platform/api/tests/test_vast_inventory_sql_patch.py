"""Inventory publication patches the small inventory object in SQL.

Historical workers keep tens of MB of attempt evidence in provider_metadata, so
the periodic inventory write must not load and re-serialize it. Private SQLite;
no provider calls.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import ExecutionTarget
from services.remote_execution import targets
from services.remote_execution.contracts import ExecutionTargetInventoryResponse

HISTORY = {'artifact_inventory': {'artifacts': [{'path': 'a'}] * 50}, 'preload': {'phase': 'complete'},
           'attachment': {'phase': 'ready'}, 'critical_runtime_manifest': {'x': 1}}


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/inventory.db')
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: ExecutionTarget.__table__.create(conn))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all([
            ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1', state='ready', active=True,
                            provider_metadata={**HISTORY, 'inventory': {'status': 'complete', 'present': True, 'running': True,
                                                                         'checked_at': '2026-01-01T00:00:00', 'ssh_endpoints': [{'h': 1}]}}),
            ExecutionTarget(id='vast:2', provider='vast', provider_instance_id='2', state='inactive', active=False,
                            provider_metadata={**HISTORY}),    # no inventory object at all
            ExecutionTarget(id='vast:3', provider='vast', provider_instance_id='3', state='ready', active=True,
                            provider_metadata={**HISTORY, 'inventory': {'status': 'unknown', 'present': True}}),   # already unknown
        ])
        await session.commit()
    yield maker
    await engine.dispose()


async def rows(maker):
    async with maker() as session:
        return {row.id: row for row in (await session.scalars(select(ExecutionTarget))).all()}


@pytest.mark.asyncio
async def test_invalidate_changes_only_status_and_keeps_every_other_key(factory):
    async with factory() as session:
        await targets.invalidate_vast_inventory(session)
    result = await rows(factory)
    first = result['vast:1'].provider_metadata
    assert first['inventory'] == {'status': 'unknown', 'present': True, 'running': True,
                                  'checked_at': '2026-01-01T00:00:00', 'ssh_endpoints': [{'h': 1}]}
    assert {key: first[key] for key in HISTORY} == HISTORY
    second = result['vast:2'].provider_metadata
    assert second['inventory'] == {'status': 'unknown'}
    assert {key: second[key] for key in HISTORY} == HISTORY
    third = result['vast:3'].provider_metadata
    assert third['inventory'] == {'status': 'unknown', 'present': True}
    assert result['vast:1'].active is True and result['vast:1'].state == 'ready'          # only knowledge is invalidated


@pytest.mark.asyncio
async def test_invalidate_is_idempotent_and_does_not_rewrite_rows_already_unknown(factory):
    async with factory() as session:
        await targets.invalidate_vast_inventory(session)
    before = {key: value.updated_at for key, value in (await rows(factory)).items()}
    async with factory() as session:
        await targets.invalidate_vast_inventory(session)
    after = {key: value.updated_at for key, value in (await rows(factory)).items()}
    assert before == after


@pytest.mark.asyncio
async def test_absent_workers_are_marked_with_the_same_values_and_history_survives(factory, monkeypatch):
    async def complete_empty():
        return ExecutionTargetInventoryResponse(provider='vast', available=True, credential_configured=True,
                                                message='fixture', instances=[])
    monkeypatch.setattr(targets, 'list_owned_instances', complete_empty)
    async with factory() as session:
        await targets.refresh_vast_targets(session)
    result = await rows(factory)
    for identifier in ('vast:1', 'vast:2', 'vast:3'):
        row = result[identifier]
        inventory = row.provider_metadata['inventory']
        assert (inventory['status'], inventory['present'], inventory['running']) == ('complete', False, False)
        datetime.fromisoformat(inventory['checked_at'])
        assert row.active is False and row.state == 'inactive'
        assert row.last_error == 'Absent from complete owned Vast inventory'
        assert {key: row.provider_metadata[key] for key in HISTORY} == HISTORY
        assert json.dumps(row.provider_metadata)   # still one valid JSON document

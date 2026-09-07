"""Mounted fleet telemetry on SQLite upgraded by the production runner."""
from datetime import datetime
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import ExecutionTarget, get_session
from migrations import runner
from routers.execution_targets import router
from services.remote_execution import telemetry
from tests.test_multiworker_migration import legacy_database
from tests.test_remote_telemetry import entry, fixture, target


@pytest.mark.asyncio
async def test_migrated_two_worker_selection_and_safe_legacy_endpoint(tmp_path, monkeypatch):
    path = tmp_path / 'fleet.db'
    legacy_database(path)
    runner.run_all(str(path))
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = telemetry.RemoteTelemetry()
    monkeypatch.setattr(telemetry, 'remote_telemetry', store)
    async def forbidden_remote(*args, **kwargs):
        pytest.fail('Telemetry reads must never poll SSH/provider')
    monkeypatch.setattr(telemetry, 'run_remote', forbidden_remote)
    async with factory() as session:
        first = await session.get(ExecutionTarget, 'vast:1')
        first.host = 'one.example.test'
        first.port = 22
        first.username = 'root'
        first.provider_metadata = {'inventory': {'status': 'complete', 'present': True,
            'running': True, 'checked_at': datetime.utcnow().isoformat()}}
        second = target()
        session.add(second)
        await session.commit()
        for worker, utilization in [(first, 11), (second, 88)]:
            state = entry()
            raw = fixture()
            raw['gpus'][0]['utilization'] = utilization
            sample = {**telemetry.derive(raw, None), 'gpus': [{'id': worker.id + ':gpu:0',
                'execution_target_id': worker.id, 'utilization': utilization}],
                'observed_at': datetime.utcnow().isoformat(), 'available': True}
            store.sequence += 1
            state['history'].append((store.sequence, time.monotonic(), sample))
            store.entries[telemetry.identity(worker)] = state

    app = FastAPI()
    app.include_router(router, prefix='/api/execution-targets')
    async def dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
            async def read(**params):
                response = await client.get('/api/execution-targets/active/telemetry', params=params)
                assert response.status_code == 200
                return response.json()
            ambiguous = await read()
            assert ambiguous['target'] is None and not ambiguous['available']
            assert ambiguous['history'] == [] and ambiguous['gpus'] == []
            assert 'Select' in ambiguous['error']
            for worker_id, utilization in [('vast:1', 11), ('vast:8', 88)]:
                value = await read(execution_target_id=worker_id)
                assert value['target']['id'] == worker_id
                assert value['available']
                assert value['gpus'][0]['utilization'] == utilization
                assert [sample['gpus'][0]['execution_target_id'] for sample in value['history']] == [worker_id]
                delta = await read(execution_target_id=worker_id, since=value['cursor'])
                assert delta['target']['id'] == worker_id and delta['history'] == []
            assert (await read(execution_target_id='missing'))['target'] is None
            async with factory() as session:
                second = await session.get(ExecutionTarget, 'vast:8')
                second.active = False
                await session.commit()
            assert (await read(execution_target_id='vast:8'))['target'] is None
            assert (await read())['target']['id'] == 'vast:1'
            async with factory() as session:
                first = await session.get(ExecutionTarget, 'vast:1')
                first.state = 'error'
                await session.commit()
            assert (await read())['target'] is None
            assert (await read(execution_target_id='vast:1'))['target'] is None
    finally:
        await engine.dispose()

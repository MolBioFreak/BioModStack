import asyncio
from collections import deque
from datetime import datetime
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import ExecutionTarget
from services.remote_execution import telemetry as mod, targets, telemetry_probe


def target():
    return ExecutionTarget(id='vast:8', provider='vast', provider_instance_id='8', active=True, state='ready',
        host='example.test', port=22, username='root', remote_root='/opt/biomodstack',
        provider_metadata={'inventory': {'status': 'complete', 'present': True, 'running': True,
                                         'checked_at': datetime.utcnow().isoformat()}},
        capabilities={}, pricing={}, created_at=datetime.utcnow(), updated_at=datetime.utcnow())


def fixture(t=100):
    return {'worker_time': t, 'gpus': [dict(index=i, uuid=f'GPU-{i}', name='Eight GPU fixture',
        utilization=30, memory_total_mb=24000, memory_used_mb=1000, temperature=None, power_draw_w=90) for i in range(8)],
        'cpu': {'scope': 'cgroup', 'allocated_cores': 8, 'usage_seconds': t * 4},
        'ram': {'limit_bytes': 2**35, 'used_bytes': 2**30}, 'disk': {'free_bytes': 2**39, 'total_bytes': 2**40},
        'network': {'eth0': {'rx_bytes': t*1024, 'tx_bytes': t*2048}}}


def entry():
    return {'history': deque(maxlen=361), 'failures': 0, 'due': 0}


@pytest.mark.asyncio
async def test_eight_gpu_payload_incremental_fresh_admission_and_expiry(monkeypatch):
    store = mod.RemoteTelemetry(); worker = target(); state = entry()
    store.entries[mod.identity(worker)] = state
    async def remote(*args, **kwargs):
        assert kwargs['timeout'] == 6
        assert args[1] == ['python3', '-', '/opt/biomodstack']
        return SimpleNamespace(stdout=json.dumps(fixture()))
    monkeypatch.setattr(mod, 'run_remote', remote)
    monkeypatch.setattr(mod, 'remote_telemetry', store)
    await store.collect(worker, state)
    first = store.read(worker)
    assert first['available'] and len(first['gpus']) == 8
    assert first['payload_bytes'] < 4096
    assert first['collection_duration_ms'] < 100
    assert store.read(worker, first['cursor'])['history'] == []
    assert (await targets.remote_target_telemetry(worker))['available']
    assert len(store.read(worker, 'different:1')['history']) == 1
    seq, stamp, sample = state['history'][-1]
    state['history'][-1] = (seq, stamp-21, sample)
    assert not (await targets.remote_target_telemetry(worker))['available']
    state['history'][-1] = (seq, stamp-3601, sample)
    assert not store.read(worker)['history']
    print('eight_gpu_measurement', first['payload_bytes'], first['collection_duration_ms'])


@pytest.mark.asyncio
async def test_missing_vram_never_authorizes_admission(monkeypatch):
    store = mod.RemoteTelemetry(); worker = target(); state = entry()
    store.entries[mod.identity(worker)] = state
    async def remote(*args, **kwargs):
        raw = fixture(); raw['gpus'][0]['memory_used_mb'] = None
        return SimpleNamespace(stdout=json.dumps(raw))
    monkeypatch.setattr(mod, 'run_remote', remote)
    monkeypatch.setattr(mod, 'remote_telemetry', store)
    await store.collect(worker, state)
    assert not (await targets.remote_target_telemetry(worker))['available']
    worker.host = 'new.example.test'
    assert not store.read(worker)['available']


def test_deltas_first_sample_reset_and_unknown():
    assert mod.derive(fixture(), None)['cpu']['utilization'] is None
    assert mod.derive(fixture(), None)['network'][0]['rx_bytes_per_second'] is None
    current = mod.derive(fixture(110), fixture(100))
    assert current['cpu']['utilization'] == 50
    assert current['network'][0]['rx_bytes_per_second'] == 1024
    reset = fixture(110); reset['network']['eth0']['rx_bytes'] = 0
    assert mod.derive(reset, fixture())['network'][0]['rx_bytes_per_second'] is None
    assert mod.derive(fixture(1), fixture())['cpu']['utilization'] is None


@pytest.mark.asyncio
async def test_background_singleflight_slow_failure_and_many_viewers(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/telemetry.db')
    async with engine.begin() as connection:
        await connection.run_sync(ExecutionTarget.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(target()); await session.commit()
    store = mod.RemoteTelemetry(); stop = asyncio.Event(); started = asyncio.Event(); release = asyncio.Event()
    calls = 0
    async def remote(*args, **kwargs):
        nonlocal calls
        calls += 1; started.set(); await release.wait()
        raise TimeoutError('fixture slow SSH')
    monkeypatch.setattr(mod, 'run_remote', remote)
    monkeypatch.setattr(mod, 'remote_telemetry', store)
    task = asyncio.create_task(store.run(factory, stop))
    try:
        await asyncio.wait_for(started.wait(), 2)
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from database import get_session
        from routers.execution_targets import router
        app = FastAPI()
        app.include_router(router, prefix='/api/execution-targets')
        async def session_dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = session_dependency
        async def viewer():
            async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
                response = await client.get('/api/execution-targets/active/telemetry')
                assert response.status_code == 200
                return response.json()
        values = await asyncio.wait_for(asyncio.gather(*[viewer() for _ in range(8)]), 1)
        assert all(not value['available'] for value in values)
        await asyncio.sleep(1.1)  # another owner tick while SSH remains in flight
        assert calls == 1
        release.set()
        await asyncio.sleep(0.01)
        state = next(iter(store.entries.values()))
        assert state['failures'] == 1
        assert state['due'] - time.monotonic() > 15
        async with factory() as session:
            value = await asyncio.wait_for(targets.active_remote_telemetry(session), 0.5)
        assert value['error'] == 'Remote collection failed or timed out'
        assert calls == 1
    finally:
        stop.set(); release.set(); await task; await engine.dispose()
    assert not store.entries


def test_probe_one_gpu_command_missing_optional_values(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='0, GPU-0, fixture, 24000, 500, N/A, N/A, N/A\n')
    monkeypatch.setattr(telemetry_probe.subprocess, 'run', run)
    result = telemetry_probe.snapshot('/definitely/not/present')
    assert len(calls) == 1 and calls[0][0] == 'nvidia-smi'
    assert result['gpus'][0]['utilization'] is None
    assert result['disk'] == {}
    assert len(json.dumps(result)) < 65536


def test_vm_probe_does_not_measure_only_ssh_session(monkeypatch):
    original = telemetry_probe.read
    def read(path):
        assert str(path) != '/proc/self/cgroup'
        return original(path)
    monkeypatch.setattr(telemetry_probe, 'read', read)
    monkeypatch.setattr(telemetry_probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=''))
    result = telemetry_probe.snapshot('/tmp')
    assert result['cpu']['scope'] in ('host', 'cgroup')
    assert result['cpu']['allocated_cores'] > 0

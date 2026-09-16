"""Isolated one-connection pool regressions; local helper, never SSH/provider."""
import asyncio
import json
import threading
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import cache, preloading as p
from services.remote_execution.contracts import ProvisionRequest, ProvisionSelection
from test_remote_independent_provisioning import assets
from test_remote_cache_integration import local_transport
from test_remote_preloading import settle


@pytest_asyncio.fixture
async def small_pool(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.sqlite'}",
                                 pool_size=1, max_overflow=0, pool_timeout=0.5)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # Exercise default expiration as well as rollback expiration.
    factory = async_sessionmaker(engine, expire_on_commit=True)
    monkeypatch.setattr(p, 'current_source_identity', lambda: ('a'*40, 'b'*40))
    async with factory() as session:
        session.add(ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1',
            active=True, state='ready', host='host', port=22, username='root',
            remote_root=str(tmp_path / 'worker'), host_key_sha256='c'*64,
            provider_metadata={'inventory': {'status':'complete', 'present':True, 'running':True,
                'checked_at':datetime.utcnow().isoformat()}}))
        await session.commit()
    yield engine, factory
    await engine.dispose()


async def unrelated_get(factory):
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await asyncio.wait_for(client.get('/execution-targets'), 2)
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('admission', [False, True])
async def test_hashing_releases_request_connection_and_endpoint_cas(small_pool, assets, monkeypatch, admission):
    engine, factory = small_pool
    controller = p.PreloadController(factory)
    selection = ProvisionSelection(kind='model', model_id='protenix')
    async with factory() as session:
        preview = await controller.preview(session, 'vast:1', selection)
    entered, release = threading.Event(), threading.Event()
    original = cache.independent_preview
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(cache, 'independent_preview', blocked)
    async def request():
        async with factory() as session:
            # Keep an ORM identity alive: rollback must not leak expired attributes.
            row = await session.get(ExecutionTarget, 'vast:1')
            assert row.host == 'host'
            if admission:
                return await controller.start(session, 'vast:1', ProvisionRequest(
                    **selection.model_dump(), preview_sha256=preview.preview_sha256))
            return await controller.preview(session, 'vast:1', selection)
    task = asyncio.create_task(request())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert engine.pool.checkedout() == 0
        await unrelated_get(factory)
        async with factory() as session:
            (await session.get(ExecutionTarget, 'vast:1')).host = 'changed'
            await session.commit()
        release.set()
        if admission:
            with pytest.raises(p.ExecutionTargetError, match='changed'):
                await task
            assert not controller.tasks
        else:
            assert (await task).preview_sha256 == preview.preview_sha256
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'endpoint', 'operation', 'lease', 'source'])
async def test_final_readback_has_no_connection_and_completion_fails_closed(
        small_pool, assets, local_transport, monkeypatch, change):
    engine, factory = small_pool
    controller = p.PreloadController(factory)
    entered, release = asyncio.Event(), asyncio.Event()
    original = cache.run_remote
    probes = 0
    async def blocked(connection, command, **kwargs):
        nonlocal probes
        payload = kwargs.get('input_bytes', b'')
        if '--root' in command and payload and json.loads(payload).get('action') == 'probe':
            probes += 1
            if probes == 2:  # Initial cache probe, then actual final readback.
                entered.set()
                await release.wait()
        return await original(connection, command, **kwargs)
    monkeypatch.setattr(cache, 'run_remote', blocked)
    selection = ProvisionSelection(kind='model', model_id='protenix')
    async with factory() as session:
        preview = await controller.preview(session, 'vast:1', selection)
        await controller.start(session, 'vast:1', ProvisionRequest(
            **selection.model_dump(), preview_sha256=preview.preview_sha256))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert engine.pool.checkedout() == 0
        await unrelated_get(factory)
        async with factory() as session:
            target = await session.get(ExecutionTarget, 'vast:1')
            if change == 'endpoint':
                target.host = 'changed'
            elif change == 'lease':
                target.leased_job_id = 'other'
            elif change == 'operation':
                target.provider_metadata = {**target.provider_metadata, 'preload': {
                    **target.provider_metadata['preload'], 'operation_id': 'replacement'}}
            await session.commit()
        if change == 'source':
            monkeypatch.setattr(p, 'current_source_identity', lambda: ('d'*40, 'e'*40))
        release.set()
        await settle(controller)
        async with factory() as session:
            metadata = (await session.get(ExecutionTarget, 'vast:1')).provider_metadata
            if change == 'none':
                assert metadata['preload']['phase'] == 'source_download_ready'
                assert metadata['artifact_inventory']['artifacts']
            else:
                assert 'artifact_inventory' not in metadata
                if change == 'operation':
                    assert metadata['preload']['operation_id'] == 'replacement'
                    assert metadata['preload']['phase'] in p.PRELOAD_ACTIVE_PHASES
                else:
                    assert metadata['preload']['phase'] == 'failed'
    finally:
        release.set()
        await controller.close()


@pytest.mark.asyncio
async def test_preview_hashing_bound_includes_cancelled_threads(small_pool, assets, monkeypatch):
    engine, factory = small_pool
    controller = p.PreloadController(factory)
    selection = ProvisionSelection(kind='model', model_id='protenix')
    entered, release = threading.Event(), threading.Event()
    count_lock = threading.Lock()
    count = 0
    original = cache.independent_preview
    def blocked(*args):
        nonlocal count
        with count_lock:
            count += 1
            if count == 2:
                entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(cache, 'independent_preview', blocked)
    async def preview():
        async with factory() as session:
            return await controller.preview(session, 'vast:1', selection)
    tasks = [asyncio.create_task(preview()) for _ in range(4)]
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        tasks[0].cancel()
        # Drain a real unrelated request, including queued DB readers.
        await unrelated_get(factory)
        assert engine.pool.checkedout() == 0
        with count_lock:
            assert count == 2
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert all(not isinstance(result, BaseException) for result in results[1:])
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await controller.close()

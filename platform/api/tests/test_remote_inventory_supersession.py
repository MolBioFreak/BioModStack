"""Real controller/SQLite failure publication; no worker or network activity."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, _canonical_sqlite_json
from services.remote_execution import managed_inventory as mi, preloading


@pytest_asyncio.fixture(params=['default', 'canonical'])
async def inventory_store(request, tmp_path):
    kwargs = {'json_serializer': _canonical_sqlite_json} if request.param == 'canonical' else {}
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.sqlite'}", **kwargs)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        now = datetime.utcnow()
        target = ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1',
            active=True, state='ready', host='worker.invalid', port=22, username='root',
            remote_root='/worker', host_key_sha256='a' * 64, activated_at=now)
        observation = mi.ManagedInventory(observed_at=datetime.now(timezone.utc),
            boot_id=uuid.uuid4(), releases=[]).model_dump(mode='json')
        target.provider_metadata = {
            'inventory': {'status': 'complete', 'present': True, 'running': True,
                          'checked_at': now.isoformat()},
            'managed_inventory': {'manifests': [], 'endpoint_sha256': mi.endpoint_digest(target),
                                  'observation': observation, 'refresh_failed': False},
        }
        session.add(target)
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'observation', 'operation', 'attachment', 'endpoint',
    'unchanged', 'expired_provider', 'new_lease', 'unrelated_probing',
])
async def test_controller_preserves_newer_authority_on_failed_readback(inventory_store, monkeypatch, change):
    factory = inventory_store
    controller = preloading.PreloadController(factory)
    replacement = None
    calls = 0

    async def interrupted_readback(connection, manifests, check_fence):
        nonlocal replacement, calls
        calls += 1
        assert connection.target_id == 'vast:1' and manifests == []
        await check_fence()  # Exercise the controller's actual initial fence.
        async with factory() as competing:
            row = await competing.get(ExecutionTarget, 'vast:1')
            metadata = deepcopy(row.provider_metadata)
            if change == 'observation':
                previous = datetime.fromisoformat(metadata['managed_inventory']['observation']['observed_at'])
                metadata['managed_inventory']['observation']['observed_at'] = (
                    previous + timedelta(seconds=1)).isoformat()
            elif change == 'operation':
                metadata['preload'] = {'operation_id': 'new-operation', 'phase': 'checking'}
            elif change == 'attachment':
                row.activated_at += timedelta(seconds=1)
            elif change == 'endpoint':
                row.host = 'replacement.invalid'
            elif change == 'expired_provider':
                metadata['inventory']['checked_at'] = '2000-01-01T00:00:00'
            elif change == 'new_lease':
                row.leased_job_id = 'newly-leased-work'
            elif change == 'unrelated_probing':
                competing.add(ExecutionTarget(id='vast:2', provider='vast',
                    provider_instance_id='2', active=False, state='probing'))
            row.provider_metadata = metadata
            replacement = deepcopy(metadata['managed_inventory'])
            await competing.commit()
        raise OSError('injected readback failure after concurrent change')

    monkeypatch.setattr(mi, 'observe_releases', interrupted_readback)
    superseded = change in {'observation', 'operation', 'attachment', 'endpoint'}
    expected = ('Managed inventory readback superseded; current observation preserved'
                if superseded else
                'Managed inventory readback failed; prior observation is stale, explicitly retry')
    try:
        async with factory() as session:
            with pytest.raises(preloading.ExecutionTargetError) as caught:
                await controller.refresh_inventory(session, 'vast:1')
            assert str(caught.value) == expected
        assert calls == 1
        async with factory() as session:
            row = await session.get(ExecutionTarget, 'vast:1')
            actual = row.provider_metadata['managed_inventory']
            assert actual == (replacement if superseded else dict(replacement, refresh_failed=True))
            if change == 'unrelated_probing':
                assert (await session.get(ExecutionTarget, 'vast:2')).state == 'probing'
    finally:
        await controller.close()

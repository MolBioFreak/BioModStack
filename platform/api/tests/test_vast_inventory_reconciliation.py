"""Offline presence reconciliation: provider fixtures and isolated SQLite only."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import ExecutionTarget, Job
from services.remote_execution import targets, vast
from services.remote_execution.contracts import ExecutionTargetInventoryResponse, ExecutionTargetActivateRequest


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inventory.db")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: ExecutionTarget.__table__.create(conn))
        await connection.run_sync(lambda conn: Job.__table__.create(conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for identifier, state in [('49684651', 'ready'), ('49674511', 'discovered'), ('49604414', 'unavailable')]:
            session.add(ExecutionTarget(id=f'vast:{identifier}', provider='vast', provider_instance_id=identifier,
                state=state, active=state == 'ready', host='203.0.113.10', port=22, username='root',
                leased_job_id='attempt-owner' if state == 'ready' else None,
                capabilities={'readiness': {'ok': True}}, last_seen_at=datetime(2026, 9, 2)))
        for identifier in ['cancelled-1', 'cancelled-2']:
            session.add(Job(id=identifier, name=identifier, model_id='boltz2', mode='monomer', params={},
                status='cancelled', queue_status='cancelled', execution_target_id='vast:49684651',
                remote_attempt_id=identifier, provenance={'evidence': identifier}))
        await session.commit()
        yield session, factory
    await engine.dispose()


def inventory(monkeypatch, ids=(), *, available=True, host='203.0.113.10', state='running'):
    value = ExecutionTargetInventoryResponse(provider='vast', available=available, credential_configured=True,
        message='fixture', instances=[vast._normalize({'id': i, 'actual_status': state, 'ssh_host': host, 'ssh_port': 22}) for i in ids])
    async def read():
        return value
    monkeypatch.setattr(targets, 'list_owned_instances', read)


@pytest.mark.asyncio
async def test_complete_empty_hides_all_history_without_touching_jobs_or_lease(store, monkeypatch):
    session, _ = store
    before = (await session.execute(select(Job.__table__))).all()
    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    assert await targets.list_targets(session) == []
    rows = (await session.scalars(select(ExecutionTarget))).all()
    assert len(rows) == 3
    assert all(not row.active and row.provider_metadata['inventory']['present'] is False for row in rows)
    assert rows[0].leased_job_id == 'attempt-owner'
    assert (await session.execute(select(Job.__table__))).all() == before


@pytest.mark.asyncio
async def test_present_subset_preserves_attachment_but_changed_endpoint_does_not_redirect_lease(store, monkeypatch):
    session, _ = store
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    assert [t.id for t in await targets.list_targets(session)] == ['vast:49684651']
    assert (await targets.get_ready_target(session, 'vast:49684651')).active
    inventory(monkeypatch, ['49684651'], host='203.0.113.99')
    await targets.refresh_vast_targets(session)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, 'vast:49684651')
    row = await session.get(ExecutionTarget, 'vast:49684651')
    assert row.host == '203.0.113.10'
    assert row.leased_job_id == 'attempt-owner'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['unavailable', 'error'])
async def test_failed_inventory_preserves_presence_but_denies_current_reads_and_ssh(store, monkeypatch, failure):
    session, _ = store
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    row = await session.get(ExecutionTarget, 'vast:49684651')
    seen = row.last_seen_at
    if failure == 'error':
        async def read():
            raise vast.VastInventoryError('offline error')
        monkeypatch.setattr(targets, 'list_owned_instances', read)
    else:
        inventory(monkeypatch, available=False)
    try:
        await targets.refresh_vast_targets(session)
    except targets.ExecutionTargetError:
        pass
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, row.id)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.list_targets(session)
    assert row.provider_metadata['inventory']['present'] is True
    assert row.last_seen_at == seen
    async def forbidden(*args, **kwargs):
        pytest.fail('stale endpoint SSH')
    monkeypatch.setattr(targets, 'run_remote', forbidden)
    assert not (await targets.active_remote_telemetry(session))['available']
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49684651'))


@pytest.mark.asyncio
async def test_legacy_and_expired_positive_inventory_fail_closed(store, monkeypatch):
    session, _ = store
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, 'vast:49684651')
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    row = await session.get(ExecutionTarget, 'vast:49684651')
    row.provider_metadata = {'inventory': {**row.provider_metadata['inventory'], 'checked_at': (datetime.utcnow() - timedelta(seconds=121)).isoformat()}}
    await session.commit()
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, row.id)
    from services.gpu_orchestrator import _claim_remote_job
    assert await _claim_remote_job(session, SimpleNamespace(id='new', execution_target_id=row.id), gpu_id=0, vram_estimate_mb=1) is None


@pytest.mark.asyncio
async def test_owned_lifecycle_invalidates_startup_and_refreshes_every_sixty_seconds(store, monkeypatch):
    import asyncio
    session, factory = store
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    async def unavailable():
        raise vast.VastInventoryError('offline')
    monkeypatch.setattr(targets, 'list_owned_instances', unavailable)
    stop = asyncio.Event()
    delays = []
    async def wait(seconds):
        delays.append(seconds)
        async with factory() as check:
            with pytest.raises(targets.ExecutionTargetError):
                await targets.get_ready_target(check, 'vast:49684651')
        inventory(monkeypatch)
        if len(delays) == 2:
            stop.set()
    await targets.run_vast_inventory_refresh(factory, stop, wait=wait)
    assert delays == [60, 60]
    async with factory() as check:
        assert await targets.list_targets(check) == []


@pytest.mark.asyncio
async def test_new_present_resource_is_discovered_not_failed(store, monkeypatch):
    session, _ = store
    inventory(monkeypatch, ['new'])
    await targets.refresh_vast_targets(session)
    assert (await targets.list_targets(session))[0].state == 'discovered'


@pytest.mark.asyncio
async def test_cached_session_cannot_place_after_another_session_invalidates(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    cached = await targets.get_ready_target(session, 'vast:49684651')
    cached.leased_job_id = None
    await session.commit()
    async with factory() as other:
        await targets.invalidate_vast_inventory(other)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, cached.id)


@pytest.mark.asyncio
async def test_current_gets_are_read_only_and_map_unknown_to_503(store, monkeypatch):
    from fastapi import HTTPException
    from routers.execution_targets import execution_targets
    session, _ = store
    before = (await session.execute(select(ExecutionTarget.__table__))).all()
    async def forbidden():
        pytest.fail('GET attempted provider refresh')
    monkeypatch.setattr(targets, 'list_owned_instances', forbidden)
    with pytest.raises(HTTPException) as error:
        await execution_targets(session)
    assert error.value.status_code == 503
    assert (await session.execute(select(ExecutionTarget.__table__))).all() == before
    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    monkeypatch.setattr(targets, 'list_owned_instances', forbidden)
    assert await execution_targets(session) == []


@pytest.mark.asyncio
async def test_scheduler_claim_rechecks_inventory_in_atomic_update(store, monkeypatch):
    from services.gpu_orchestrator import _claim_remote_job
    session, _ = store
    inventory(monkeypatch, ['49684651'])
    await targets.refresh_vast_targets(session)
    row = await targets.get_ready_target(session, 'vast:49684651')
    row.leased_job_id = None
    await session.commit()
    original = targets.get_ready_target
    async def invalidate_after_check(db, identifier):
        target = await original(db, identifier)
        await targets.invalidate_vast_inventory(db)
        return target
    monkeypatch.setattr(targets, 'get_ready_target', invalidate_after_check)
    job = Job(id='new', name='new', model_id='boltz2', mode='monomer', execution_target_id=row.id, params={}, status='queued', queue_status='queued')
    session.add(job)
    await session.commit()
    assert await _claim_remote_job(session, job, gpu_id=0, vram_estimate_mb=1) is None


@pytest.mark.asyncio
async def test_submission_rejects_legacy_ready_before_source_or_transport(store, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from routers import jobs
    session, _ = store
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *args: None)
    before = (await session.execute(select(Job.__table__))).all()
    request = jobs.JobCreate(name='new placement', model_id='boltz2', mode='monomer', params={}, execution_target_id='vast:49684651')
    with pytest.raises(HTTPException) as error:
        await jobs._create_job(request, BackgroundTasks(), session)
    assert error.value.status_code == 422
    assert 'active ready' in error.value.detail
    assert (await session.execute(select(Job.__table__))).all() == before


@pytest.mark.asyncio
async def test_discover_serializes_complete_fetch_through_reconciliation(store, monkeypatch):
    import asyncio
    _, factory = store
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    async def read():
        calls.append(len(calls))
        if len(calls) == 1:
            entered.set()
            await release.wait()
            ids = ['49684651']
        else:
            ids = []
        return ExecutionTargetInventoryResponse(provider='vast', available=True, credential_configured=True,
            message='fixture', instances=[vast._normalize({'id': i, 'actual_status': 'running'}) for i in ids])
    monkeypatch.setattr(targets, 'list_owned_instances', read)
    async def refresh():
        async with factory() as session:
            await targets.refresh_vast_targets(session)
    first = asyncio.create_task(refresh())
    await entered.wait()
    second = asyncio.create_task(refresh())
    await asyncio.sleep(0.02)
    overlap = len(calls)
    release.set()
    await asyncio.gather(first, second)
    assert overlap == 1
    async with factory() as session:
        assert await targets.list_targets(session) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['stopped', 'stopped_final', 'absent', 'unknown', 'present'])
async def test_activation_cannot_publish_ready_after_provider_stops_during_probe(store, monkeypatch, tmp_path, outcome):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    launcher = tmp_path / 'nextflow'
    launcher.write_text('fixture')
    from services import nextflow
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    async def capture(*args): return ('fixture host key', 'a' * 64)
    async def noop(*args, **kwargs): pass
    calls = []
    async def probe(*args):
        async def stopped():
            return ExecutionTargetInventoryResponse(provider='vast', available=True, credential_configured=True,
                message='stopped', instances=[vast._normalize({'id': '49674511', 'actual_status': 'stopped', 'ssh_host': '203.0.113.10', 'ssh_port': 22})])
        if outcome in {'present', 'stopped_final'}:
            return {'ok': True}
        monkeypatch.setattr(targets, 'list_owned_instances', stopped)
        async with factory() as other:
            if outcome == 'unknown':
                await targets.invalidate_vast_inventory(other)
            else:
                if outcome == 'absent':
                    inventory(monkeypatch)
                await targets.refresh_vast_targets(other)
        return {'ok': True}
    async def run(connection, command, **kwargs):
        calls.append('ssh')
        if outcome == 'stopped_final' and command[0] == 'sha256sum':
            inventory(monkeypatch, ['49674511'], state='stopped')
            async with factory() as other:
                await targets.refresh_vast_targets(other)
        if command[0] == 'env': return SimpleNamespace(stdout='nextflow version 25.10.1\n')
        if command[0] == 'apptainer': return SimpleNamespace(stdout='BMS_CUDA_OK\n')
        return SimpleNamespace(stdout='fixturehash worker\nfixturehash nextflow\n')
    monkeypatch.setattr(targets, 'capture_host_key', capture)
    monkeypatch.setattr(targets, 'persist_host_key', noop)
    monkeypatch.setattr(targets, 'probe_readiness', probe)
    monkeypatch.setattr(targets, 'rsync_to_remote', noop)
    monkeypatch.setattr(targets, 'run_remote', run)
    monkeypatch.setattr(targets, '_sha256_file', lambda *args: 'fixturehash')
    if outcome == 'present':
        result = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
        assert result.active and result.state == 'ready'
        assert len(calls) == 7  # One verified promotion replaces two chmod calls.
        return
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    await session.rollback()
    async with factory() as check:
        assert not (await check.get(ExecutionTarget, 'vast:49674511')).active
    assert len(calls) == (5 if outcome == 'stopped_final' else 2)


@pytest.mark.asyncio
async def test_existing_attempt_unknown_inventory_does_not_probe_or_rewrite_history(store, monkeypatch):
    from services.remote_execution import executor
    session, _ = store
    job = await session.get(Job, 'cancelled-1')
    before = (await session.execute(select(Job.__table__))).all()
    calls = []
    async def remote(*args, **kwargs):
        calls.append('ssh')
        raise executor.RemoteExecutionError('offline probe')
    monkeypatch.setattr(executor, 'run_remote', remote)
    with pytest.raises(executor.RemoteExecutionError):
        await executor.remote_status(session, job)
    assert calls == []
    assert (await session.execute(select(Job.__table__))).all() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh_at', ['probe', 'final'])
@pytest.mark.parametrize('username', ['root', 'worker-user'])
async def test_inventory_healthy_refresh_preserves_attachment(store, monkeypatch, tmp_path, refresh_at, username):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    launcher = tmp_path / 'nextflow'
    launcher.write_text('fixture')
    from services import nextflow
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    async def refresh():
        async with factory() as other:
            await targets.refresh_vast_targets(other)
    async def capture(*args): return ('fixture host key', 'a' * 64)
    async def noop(*args, **kwargs): pass
    async def probe(*args):
        if refresh_at == 'probe':
            await refresh()
        return {'ok': True}
    async def run(connection, command, **kwargs):
        if refresh_at == 'final' and command[0] == 'sha256sum':
            await refresh()
        if command[0] == 'env': return SimpleNamespace(stdout='nextflow version 25.10.1\n')
        if command[0] == 'apptainer': return SimpleNamespace(stdout='BMS_CUDA_OK\n')
        return SimpleNamespace(stdout='fixturehash worker\nfixturehash nextflow\n')
    monkeypatch.setattr(targets, 'capture_host_key', capture)
    monkeypatch.setattr(targets, 'persist_host_key', noop)
    monkeypatch.setattr(targets, 'probe_readiness', probe)
    monkeypatch.setattr(targets, 'rsync_to_remote', noop)
    monkeypatch.setattr(targets, 'run_remote', run)
    monkeypatch.setattr(targets, '_sha256_file', lambda *args: 'fixturehash')
    result = await targets.activate_target(session, ExecutionTargetActivateRequest(
        provider_instance_id='49674511', username=username))
    assert result.active and result.state == 'ready'
    assert result.username == username


@pytest.mark.asyncio
async def test_inventory_newer_endpoint_before_attachment_read_is_preserved(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    refresh = targets.refresh_vast_targets
    async def superseded_inventory(db):
        old = await refresh(db)
        inventory(monkeypatch, ['49674511'], host='203.0.113.99')
        async with factory() as other:
            await refresh(other)
        db.expire_all()
        return old
    calls = []
    async def forbidden(*args):
        calls.append('ssh')
        raise AssertionError('superseded endpoint reached transport')
    monkeypatch.setattr(targets, 'refresh_vast_targets', superseded_inventory)
    monkeypatch.setattr(targets, 'capture_host_key', forbidden)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    await session.rollback()
    async with factory() as other:
        current = await other.get(ExecutionTarget, 'vast:49674511')
        assert current.host == '203.0.113.99'
        assert not current.active
    assert calls == []


@pytest.mark.parametrize('payload', [
    {'success': True, 'instances': [{'id': True}]},
    {'success': True, 'instances': [{'id': {'malformed': 1}}]},
    {'success': False, 'instances': []},
    {'success': True, 'instances': [], 'next_token': 'more'},
    {'success': True, 'instances': [], 'total_instances': 1},
    {'success': True, 'instances': [], 'instances_found': 1},
    {'success': True, 'instances': [None]},
    {'success': True, 'instances': [{}]},
    {'success': True, 'instances': [{'id': 1}, {'id': 1}]},
])
def test_adapter_rejects_incomplete_or_malformed_inventory(monkeypatch, payload):
    class Response:
        headers = SimpleNamespace(get_content_type=lambda: 'application/json')
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(payload).encode()
    monkeypatch.setenv('VAST_API_KEY', 'offline-fixture')
    monkeypatch.delenv('BMS_VAST_API_BASE_URL', raising=False)
    monkeypatch.setattr(vast.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=lambda *args, **kwargs: Response()))
    with pytest.raises(vast.VastInventoryError):
        vast._fetch_owned_instances()


@pytest.mark.asyncio
async def test_inventory_refresh_atomic_endpoint_preserves_racing_lease(store, monkeypatch):
    from sqlalchemy import create_engine, event, update
    from sqlalchemy.orm import Session
    session, factory = store
    ids = ['49684651', '49674511', '49604414']
    inventory(monkeypatch, ids)
    await targets.refresh_vast_targets(session)
    row = await session.get(ExecutionTarget, 'vast:49684651')
    row.leased_job_id = None
    await session.commit()
    engine = session.bind
    competitor = create_engine(str(engine.url).replace('+aiosqlite', ''))
    claimed = []
    def claim_before_write(conn, cursor, statement, parameters, context, many):
        if not claimed and statement.lstrip().upper().startswith('UPDATE'):
            with Session(competitor) as other:
                other.execute(update(ExecutionTarget).where(ExecutionTarget.id == row.id).values(leased_job_id='racing-owner'))
                other.commit()
            claimed.append(True)
    event.listen(engine.sync_engine, 'before_cursor_execute', claim_before_write)
    inventory(monkeypatch, ids, host='203.0.113.99')
    try:
        await targets.refresh_vast_targets(session)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', claim_before_write)
        competitor.dispose()
    async with factory() as check:
        current = await check.get(ExecutionTarget, row.id)
        assert claimed == [True]
        assert current.leased_job_id == 'racing-owner'
        assert (current.host, current.port, current.username) == ('203.0.113.10', 22, 'root')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['lease', 'stopped'])
async def test_inventory_attachment_initial_mutation_is_fenced(store, monkeypatch, change):
    from sqlalchemy import update
    session, factory = store
    identifier = 'vast:49674511'
    inventory(monkeypatch, ['49674511'])
    original = session.scalar
    transitions = []
    async def competing_transition(*args, **kwargs):
        result = await original(*args, **kwargs)
        if not transitions:
            async with factory() as other:
                if change == 'lease':
                    await other.execute(update(ExecutionTarget).where(ExecutionTarget.id == identifier).values(
                        leased_job_id='racing-owner', state='ready', active=True))
                    await other.commit()
                else:
                    inventory(monkeypatch, ['49674511'], state='stopped')
                    await targets.refresh_vast_targets(other)
                current = await other.get(ExecutionTarget, identifier)
                transitions.append((current.state, current.active, current.host, current.username, current.remote_root))
        return result
    monkeypatch.setattr(session, 'scalar', competing_transition)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append('ssh')
        raise targets.RemoteTransportError('unexpected SSH')
    monkeypatch.setattr(targets, 'capture_host_key', forbidden)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(
            provider_instance_id='49674511', username='different', remote_root='/different'))
    await session.rollback()
    async with factory() as check:
        current = await check.get(ExecutionTarget, identifier)
        assert (current.state, current.active, current.host, current.username, current.remote_root) == transitions[0]
        if change == 'lease':
            assert current.leased_job_id == 'racing-owner'
    assert calls == []

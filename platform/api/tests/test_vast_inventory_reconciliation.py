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
async def test_complete_empty_inventory_releases_history_only_after_a_confirmed_omission(
        store, monkeypatch):
    session, _ = store
    before = (await session.execute(select(Job.__table__))).all()
    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    rows = (await session.scalars(select(ExecutionTarget))).all()
    assert len(rows) == 3
    # One omission is a provider read artefact: presence is denied to admission
    # but nothing is released.
    assert all(row.provider_metadata['inventory']['present'] is False for row in rows)
    assert [row.provider_metadata['inventory']['absent_observations'] for row in rows] == [1, 1, 1]
    ready = await session.get(ExecutionTarget, 'vast:49684651')
    assert ready.active and ready.state == 'ready' and ready.last_error is None
    assert not targets.target_eligible(ready)
    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    rows = (await session.scalars(select(ExecutionTarget))).all()
    assert all(not row.active and row.state == 'inactive'
               and row.last_error == targets.ABSENT_INVENTORY_MESSAGE for row in rows)
    assert [row.provider_metadata['inventory']['absent_observations'] for row in rows] == [2, 2, 2]
    assert rows[0].leased_job_id == 'attempt-owner'
    # The leased attempt is still the operator's retained ownership.
    assert [row.id for row in await targets.list_targets(session)] == ['vast:49684651']
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
        # The leased attempt keeps the target visible for its explicit return.
        assert [row.id for row in await targets.list_targets(session)] == ['vast:49684651']


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['stopped', 'absent', 'unknown'])
async def test_provider_reading_during_setup_does_not_discard_a_completed_install(
        store, monkeypatch, managed_runtime, outcome):
    """A provider status flip is not evidence that the worker is gone.

    The attempt publishes readiness from its own authenticated attachment; the
    provider reading stays an admission gate, so the worker is never advertised
    for new work until the provider agrees again.
    """
    session, factory = store
    identifier = '49674511'
    inventory(monkeypatch, [identifier])

    async def flip(*args):
        async with factory() as other:
            if outcome == 'unknown':
                await targets.invalidate_vast_inventory(other)
                return
            inventory(monkeypatch, [identifier] if outcome == 'stopped' else [], state='stopped')
            await targets.refresh_vast_targets(other)

    attach_stubs(monkeypatch, on_command=flip)
    result = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id=identifier))
    assert result.active and result.state == 'ready'
    row = await targets.get_target(session, targets.target_id('vast', identifier))
    assert row.provider_metadata['setup']['phase'] == 'ready'
    assert row.provider_metadata['setup']['message'] != 'Vast inventory or endpoint changed during attachment'
    assert row.host_key_sha256 == 'a' * 64
    assert not targets.target_eligible(row)
    inventory(monkeypatch, [identifier])
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert targets.target_eligible(row)


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
        if command[:3] == ['sh', '-s', '--']:
            assert command[3] == connection.remote_root
            assert b'BMS_ATTACHED' in kwargs['input_bytes']
            return SimpleNamespace(stdout='BMS_ATTACHED\nBMS_TELEMETRY\n')
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
    original = session.execute
    transitions = []
    async def competing_transition(*args, **kwargs):
        statement = args[0]
        if (not transitions and getattr(statement, 'is_update', False)
                and statement.compile().params.get('state') == 'probing'):
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
        return await original(*args, **kwargs)
    monkeypatch.setattr(session, 'execute', competing_transition)
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


# --- provider lifecycle: bounded absence, two-way ready, typed fence reasons ---

@pytest.fixture
def managed_runtime(monkeypatch):
    """Local doubles for the managed-runtime owner: real state, no SSH or provider."""
    from services.remote_execution import cache, critical_runtime, managed_inventory

    class Receipt:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def model_dump(self, mode='json'):
            return {key: (value.model_dump(mode) if isinstance(value, Receipt) else value)
                    for key, value in self.__dict__.items()}

    hooks = SimpleNamespace(before_publication=None)
    release = Receipt(critical=Receipt(observed={'backend': 'apptainer', 'cuda': {'ok': True}}), state='verified')

    monkeypatch.setattr(critical_runtime, 'project_runtime',
                        lambda root, staging: ({'selection': 'fixture', 'entries': []}, []))
    monkeypatch.setattr(critical_runtime, 'runtime_binding',
                        lambda root, manifest, backend: {'environment': {'NXF_OFFLINE': 'true'}, 'paths': {}})
    monkeypatch.setattr(managed_inventory, 'saved_manifests', lambda target: [])

    async def helper_call(connection, request, fence):
        await fence()
        return {'boot_id': 'fixture-boot'}

    async def cache_artifacts(**kwargs):
        await kwargs['check_fence']()

    async def activate_release(connection, manifest, fence, progress, boot):
        await fence()
        return release

    async def observe_releases(connection, manifests, fence):
        await fence()
        if hooks.before_publication is not None:
            await hooks.before_publication()
        return Receipt(boot_id='fixture-boot', critical_runtime_ready=True)

    monkeypatch.setattr(managed_inventory, 'helper_call', helper_call)
    monkeypatch.setattr(cache, '_cache_artifacts', cache_artifacts)
    monkeypatch.setattr(managed_inventory, 'activate_release', activate_release)
    monkeypatch.setattr(managed_inventory, 'observe_releases', observe_releases)
    return hooks


def attach_stubs(monkeypatch, *, on_command=None, after=1, on_attach=None):
    """Stub one attachment's transport; `on_command` runs once, after the commit."""
    seen = []

    async def capture(host, port):
        return 'fixture host key', 'a' * 64

    async def persist(*args):
        return None

    async def probe(connection):
        return {'gpus': ['fixture gpu']}

    async def run(connection, command, **kwargs):
        if command[:3] == ['sh', '-s', '--']:
            assert command[3] == connection.remote_root
            assert b'BMS_ATTACHED' in kwargs['input_bytes']
            if on_attach is not None:
                await on_attach(command)
            return SimpleNamespace(stdout='BMS_ATTACHED\nBMS_TELEMETRY\n')
        seen.append(command)
        if on_command is not None and len(seen) == after:
            await on_command(command)
        return SimpleNamespace(stdout='fixture output\n')

    monkeypatch.setattr(targets, 'capture_host_key', capture)
    monkeypatch.setattr(targets, 'persist_host_key', persist)
    monkeypatch.setattr(targets, 'probe_readiness', probe)
    monkeypatch.setattr(targets, 'run_remote', run)


async def attach_ready(session, monkeypatch, identifier='51264075'):
    """Drive one real publication to `ready` against the stubbed transport."""
    inventory(monkeypatch, [identifier])
    attach_stubs(monkeypatch)
    result = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id=identifier))
    assert result.active and result.state == 'ready'
    return result


@pytest.mark.asyncio
async def test_omitted_inventory_releases_only_after_a_confirmed_omission(store, monkeypatch, managed_runtime):
    session, _ = store
    await attach_ready(session, monkeypatch)
    row = await session.get(ExecutionTarget, 'vast:51264075')
    attachment, pinned = dict(row.provider_metadata['attachment']), row.host_key_sha256
    assert targets.target_eligible(row)

    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert row.active and row.state == 'ready'
    assert row.provider_metadata['attachment'] == attachment
    assert row.host_key_sha256 == pinned
    assert row.provider_metadata['inventory']['absent_observations'] == 1
    assert not targets.target_eligible(row)

    inventory(monkeypatch, ['51264075'])
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert targets.target_eligible(row)
    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert row.active and row.state == 'ready'
    assert row.provider_metadata['inventory']['absent_observations'] == 1

    inventory(monkeypatch)
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert not row.active and row.state == 'inactive'
    assert row.last_error == targets.ABSENT_INVENTORY_MESSAGE
    # The ready projection, the pinned key and the last known endpoints survive.
    assert row.provider_metadata['attachment'] == attachment
    assert row.host_key_sha256 == pinned
    assert row.provider_metadata['inventory']['ssh_endpoints']
    scheduling = targets._target_response(row).capabilities['scheduling']
    assert scheduling['inventory_reason'] == targets.ABSENT_INVENTORY_MESSAGE
    assert scheduling['new_work_ready'] is False


@pytest.mark.asyncio
async def test_running_to_not_running_to_running_restores_the_ready_projection(store, monkeypatch, managed_runtime):
    session, _ = store
    await attach_ready(session, monkeypatch)
    row = await session.get(ExecutionTarget, 'vast:51264075')

    inventory(monkeypatch, ['51264075'], state='stopped')
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert row.state == 'unavailable' and row.active
    assert row.last_error == 'Provider state is stopped'
    assert not targets.target_eligible(row)

    inventory(monkeypatch, ['51264075'])
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert row.state == 'ready' and row.active and row.last_error is None
    assert targets.target_eligible(row)


@pytest.mark.asyncio
async def test_released_target_is_never_reactivated_by_recovery_or_a_late_failure(store, monkeypatch, managed_runtime):
    session, _ = store
    await attach_ready(session, monkeypatch)
    row = await session.get(ExecutionTarget, 'vast:51264075')
    started_at = row.provider_metadata['setup']['started_at']
    for _ in range(targets.ABSENT_INVENTORY_CONFIRMATIONS):
        inventory(monkeypatch)
        await targets.refresh_vast_targets(session)
    await session.refresh(row)
    assert not row.active and row.state == 'inactive'

    inventory(monkeypatch, ['51264075'])
    await targets.refresh_vast_targets(session)
    await session.refresh(row)
    # A provider reading is not an activation: the operator keeps that decision.
    assert not row.active and row.state == 'inactive'
    matched = (await session.scalars(select(ExecutionTarget.id).where(
        ExecutionTarget.id == row.id, targets.attachment_clause(datetime.utcnow())))).all()
    assert matched == []
    await targets.fail_setup(session, row.id, started_at, 'late failure for the released attempt')
    await session.refresh(row)
    assert not row.active and row.state == 'inactive'
    assert row.provider_metadata['setup']['phase'] == 'ready'


@pytest.mark.asyncio
async def test_single_omitted_inventory_read_does_not_fence_an_in_flight_attachment(
        store, monkeypatch, managed_runtime):
    session, factory = store
    identifier = '51264075'

    async def omission(*args):
        async with factory() as other:
            row = await targets.get_target(other, targets.target_id('vast', identifier))
            metadata = dict(row.provider_metadata)
            metadata['inventory'] = {**metadata['inventory'], 'present': False, 'running': False}
            row.provider_metadata = metadata
            await other.commit()

    inventory(monkeypatch, [identifier])
    attach_stubs(monkeypatch, on_command=omission)
    result = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id=identifier))
    assert result.active and result.state == 'ready'
    row = await targets.get_target(session, targets.target_id('vast', identifier))
    assert row.provider_metadata['setup']['phase'] == 'ready'
    assert row.host_key_sha256 == 'a' * 64
    assert row.provider_metadata['attachment']['telemetry'] is True
    assert not targets.target_eligible(row)


@pytest.mark.asyncio
async def test_stranded_probing_target_is_releasable_after_the_install_budget(store, monkeypatch):
    session, _ = store
    inventory(monkeypatch, ['51264075'])
    admitted = await targets.begin_activation(session, ExecutionTargetActivateRequest(provider_instance_id='51264075'))
    assert admitted.state == 'probing'
    with pytest.raises(targets.ExecutionTargetError) as refused:
        await targets.deactivate_target(session, admitted.id)
    assert str(refused.value) == targets.DETACH_ATTACHMENT_BLOCKED
    with pytest.raises(targets.ExecutionTargetError, match='changed before attachment'):
        await targets.begin_activation(session, ExecutionTargetActivateRequest(provider_instance_id='51264075'))
    row = await session.get(ExecutionTarget, admitted.id)
    row.provider_metadata = {**row.provider_metadata, 'setup': {
        **row.provider_metadata['setup'],
        'started_at': (datetime.utcnow() - timedelta(
            seconds=targets.ATTACHMENT_ABANDONED_SECONDS + 1)).isoformat()}}
    await session.commit()
    released = await targets.deactivate_target(session, admitted.id)
    assert released.state == 'inactive' and not released.active
    assert released.setup is not None
    assert released.setup.phase == 'failed'
    assert released.setup.message == targets.STALE_ATTACHMENT_RELEASED
    assert released.last_error == targets.STALE_ATTACHMENT_RELEASED
    inventory(monkeypatch, ['51264075'])
    assert (await targets.begin_activation(session, ExecutionTargetActivateRequest(
        provider_instance_id='51264075'))).state == 'probing'


@pytest.mark.asyncio
async def test_attach_task_registry_failure_does_not_strand_the_target(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['51264075'])
    controller = targets.AttachmentController(factory)

    class BrokenLoop:
        def create_task(self, *args, **kwargs):
            raise RuntimeError('task registry unavailable')

    monkeypatch.setattr(targets, 'asyncio', BrokenLoop())
    with pytest.raises(RuntimeError, match='task registry unavailable'):
        await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id='51264075'))
    row = await session.get(ExecutionTarget, 'vast:51264075')
    assert row.state == 'unavailable' and not row.active
    assert row.provider_metadata['setup']['phase'] == 'failed'
    assert row.provider_metadata['setup']['message'] == targets.ATTACHMENT_TASK_NOT_STARTED
    assert (await targets.deactivate_target(session, row.id)).state == 'inactive'


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['superseded', 'endpoint', 'lease', 'released', 'absent',
                                     'not_running', 'stale_commit', 'publication'])
async def test_attachment_fence_publishes_the_blocking_clause(store, monkeypatch, managed_runtime, blocked):
    session, factory = store
    identifier = '51264075'
    reason, disposition = {
        'superseded': (targets.ATTACHMENT_SUPERSEDED, 'replacement'),
        'endpoint': (targets.ATTACHMENT_ENDPOINT_CHANGED, 'recorded'),
        'lease': (targets.ATTACHMENT_LEASED, 'lease'),
        'released': (targets.ATTACHMENT_RELEASED, 'recorded'),
        'absent': (targets.ATTACHMENT_WORKER_ABSENT, 'recorded'),
        'not_running': (targets.ATTACHMENT_NOT_RUNNING, 'recorded'),
        'stale_commit': (targets.ATTACHMENT_INVENTORY_STALE, 'recorded'),
        'publication': (targets.ATTACHMENT_LEASED, 'lease'),
    }[blocked]

    async def mutate(*args):
        async with factory() as other:
            row = await targets.get_target(other, targets.target_id('vast', identifier))
            if blocked == 'superseded':
                row.provider_metadata = {**row.provider_metadata, 'setup': {
                    **row.provider_metadata['setup'], 'started_at': 'replacement'}}
            elif blocked == 'endpoint':
                row.host = '203.0.113.99'
            elif blocked in {'lease', 'publication'}:
                row.leased_job_id = 'racing-owner'
            elif blocked == 'stale_commit':
                metadata = dict(row.provider_metadata)
                metadata['inventory'] = {**metadata['inventory'],
                                         'checked_at': (datetime.utcnow() - timedelta(seconds=121)).isoformat()}
                row.provider_metadata = metadata
            else:
                metadata = dict(row.provider_metadata)
                metadata['inventory'] = {**metadata['inventory'],
                                         'present': blocked != 'absent',
                                         'running': blocked != 'not_running'}
                row.provider_metadata = metadata
                row.active = False
            await other.commit()

    inventory(monkeypatch, [identifier])
    if blocked == 'publication':
        managed_runtime.before_publication = mutate
        attach_stubs(monkeypatch)
    elif blocked == 'stale_commit':
        # The last pre-commit fence: the provider reading goes stale while the
        # authenticated attachment root is being proved.
        attach_stubs(monkeypatch, on_attach=mutate)
    else:
        # `superseded` must be observed by the fence, not by the setup writer.
        attach_stubs(monkeypatch, on_command=mutate, after=2 if blocked == 'superseded' else 1)
    with pytest.raises(targets.ExecutionTargetError) as refused:
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id=identifier))
    assert str(refused.value) == reason
    row = await targets.get_target(session, targets.target_id('vast', identifier))
    if disposition == 'recorded':
        assert row.provider_metadata['setup']['message'] == reason
        assert row.last_error == reason
    elif disposition == 'replacement':
        # The replacement attempt owns the row; the abandoned attempt must not
        # rewrite its setup record.
        assert row.provider_metadata['setup']['started_at'] == 'replacement'
    else:
        assert row.leased_job_id == 'racing-owner'

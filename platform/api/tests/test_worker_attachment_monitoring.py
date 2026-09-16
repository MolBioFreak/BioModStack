"""Authenticated attachment is monitoring authority, never science readiness."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from services.remote_execution import targets, telemetry, transport, preloading
from services.remote_execution.contracts import ExecutionTargetActivateRequest, ProvisionRequest
from test_vast_inventory_reconciliation import store, inventory
from test_remote_telemetry import fixture

BLOCKER = 'Mount namespaces unavailable; use a compatible VM'


def connection_double(monkeypatch, *, runtime=None, authenticate=None):
    async def capture(*args):
        return 'fixture pinned key', 'a' * 64
    async def persist(*args):
        pass
    async def run(connection, argv, **kwargs):
        if argv[0] == 'sh':
            if authenticate:
                await authenticate()
            return SimpleNamespace(stdout='BMS_ATTACHED\nBMS_TELEMETRY\n')
        if runtime:
            await runtime()
        raise transport.RemoteTransportError(BLOCKER)
    monkeypatch.setattr(targets, 'capture_host_key', capture)
    monkeypatch.setattr(targets, 'persist_host_key', persist)
    monkeypatch.setattr(targets, 'run_remote', run)


async def attach_failed(session):
    with pytest.raises(targets.ExecutionTargetError, match=BLOCKER):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    return await targets.get_target(session, 'vast:49674511')


@pytest.mark.asyncio
async def test_verified_attachment_runtime_failure_background_reads_and_detach(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    connection_double(monkeypatch)
    row = await attach_failed(session)
    assert row.active and row.state == 'unavailable' and row.activated_at
    assert row.host_key_sha256 == 'a' * 64
    assert row.last_error == row.provider_metadata['setup']['message'] == BLOCKER
    assert targets.telemetry_eligible(row) and not targets.target_eligible(row)
    assert not targets._target_response(row).capabilities['scheduling']['new_work_ready']
    with pytest.raises(targets.ExecutionTargetError):
        await targets.get_ready_target(session, row.id)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.submission_target_fields(session, row.id)
    with pytest.raises(targets.ExecutionTargetError):
        await preloading.PreloadController(factory).start(session, row.id,
            ProvisionRequest(kind='image', model_id='protenix', preview_sha256='b' * 64))

    collector = telemetry.RemoteTelemetry()
    monkeypatch.setattr(telemetry, 'remote_telemetry', collector)
    sampled = asyncio.Event()
    calls = []
    async def read(connection, argv, **kwargs):
        calls.append((connection, argv))
        sampled.set()
        return SimpleNamespace(stdout=json.dumps(fixture()))
    monkeypatch.setattr(telemetry, 'run_remote', read)
    stop = asyncio.Event()
    task = asyncio.create_task(collector.run(factory, stop))
    try:
        await asyncio.wait_for(sampled.wait(), 2)
        await asyncio.sleep(0)
        before = deepcopy(row.provider_metadata)
        for _ in range(3):
            value = await targets.active_remote_telemetry(session, execution_target_id=row.id)
            assert value['available'] and value['target']['state'] == 'unavailable'
            assert len(value['gpus']) == 8
        assert len(calls) == 1  # GETs never initiate SSH
        assert row.provider_metadata == before
        assert not (await targets.remote_target_telemetry(row))['available']
        detached = await targets.deactivate_target(session, row.id)
        assert not detached.active and detached.state == 'inactive'
        assert not (await targets.active_remote_telemetry(session, execution_target_id=row.id))['available']
    finally:
        stop.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['host', 'port', 'username', 'remote_root', 'fingerprint', 'stale', 'absent', 'lease', 'attempt'])
async def test_attachment_commit_is_fenced_after_authenticated_io(store, monkeypatch, change):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    async def change_owner():
        async with factory() as other:
            row = await targets.get_target(other, 'vast:49674511')
            metadata = deepcopy(row.provider_metadata)
            if change in {'host', 'username', 'remote_root'}:
                setattr(row, change, {'host': '203.0.113.99', 'username': 'other', 'remote_root': '/other'}[change])
            elif change == 'port': row.port = 23
            elif change == 'fingerprint': row.host_key_sha256 = 'b' * 64
            elif change == 'lease': row.leased_job_id = 'new-owner'
            elif change == 'stale': metadata['inventory']['checked_at'] = (datetime.utcnow() - timedelta(seconds=121)).isoformat()
            elif change == 'absent': metadata['inventory']['present'] = False
            else: metadata['setup']['started_at'] = 'successor'
            row.provider_metadata = metadata
            await other.commit()
    connection_double(monkeypatch, authenticate=change_owner)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert not row.active and row.activated_at is None
    assert 'attachment' not in row.provider_metadata
    assert not targets.telemetry_eligible(row)
    if change == 'lease': assert row.leased_job_id == 'new-owner'
    if change == 'attempt': assert row.provider_metadata['setup']['started_at'] == 'successor'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['authenticate', 'runtime', 'retry'])
async def test_failed_authenticated_channel_never_retains_attachment(store, monkeypatch, stage):
    session, _ = store
    inventory(monkeypatch, ['49674511'])
    if stage == 'retry':
        connection_double(monkeypatch)
        assert (await attach_failed(session)).active
    async def denied():
        raise transport.RemoteConnectionError('Remote SSH connection or authentication failed')
    connection_double(monkeypatch, **{'runtime' if stage == 'runtime' else 'authenticate': denied})
    with pytest.raises(targets.ExecutionTargetError, match='authentication failed'):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert not row.active and row.state == 'unavailable'
    assert not targets.telemetry_eligible(row)


@pytest.mark.asyncio
@pytest.mark.parametrize('restart', [False, True])
async def test_same_generation_cancellation_and_restart_preserve_commit(store, monkeypatch, restart):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    entered = asyncio.Event()
    async def blocked():
        entered.set()
        await asyncio.Event().wait()
    connection_double(monkeypatch, runtime=blocked)
    controller = targets.AttachmentController(factory)
    task = None
    if restart:
        result = await targets.begin_activation(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
        task = asyncio.create_task(targets.finish_activation(session, result.id))
    else:
        await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    await asyncio.wait_for(entered.wait(), 2)
    if restart:
        assert task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await controller.recover()
    else:
        await controller.close()
    row = await targets.get_target(session, 'vast:49674511')
    assert row.active and row.state == 'unavailable'
    assert row.provider_metadata['setup']['phase'] == 'failed'
    assert targets.telemetry_eligible(row) and not targets.target_eligible(row)
    await targets.deactivate_target(session, row.id)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stale', 'key', 'endpoint'])
async def test_recovery_drops_changed_attachment_generation(store, monkeypatch, change):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    connection_double(monkeypatch)
    row = await attach_failed(session)
    row.state = 'probing'
    metadata = deepcopy(row.provider_metadata)
    metadata['setup']['phase'] = 'installing'
    if change == 'stale': metadata['inventory']['checked_at'] = (datetime.utcnow() - timedelta(seconds=121)).isoformat()
    elif change == 'key': row.host_key_sha256 = 'b' * 64
    else: row.host = '203.0.113.99'
    row.provider_metadata = metadata
    await session.commit()
    await targets.AttachmentController(factory).recover()
    row = await targets.get_target(session, row.id)
    assert not row.active and not targets.telemetry_eligible(row)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stale', 'future', 'absent', 'stopped', 'host', 'port', 'username', 'remote_root', 'key', 'attempt', 'time', 'tools', 'detached'])
async def test_attached_cached_monitoring_read_rejects_stale_or_changed_identity(monkeypatch, change):
    from test_remote_telemetry import target, entry
    row = target()
    row.state = 'unavailable'
    generation = datetime.utcnow().isoformat()
    row.provider_metadata.update(setup={'started_at': generation, 'phase': 'failed', 'message': BLOCKER}, attachment={
        'started_at': generation, 'fingerprint': row.host_key_sha256, 'telemetry': True,
        **{key: getattr(row, key) for key in ('host', 'port', 'username', 'remote_root')}})
    collector = telemetry.RemoteTelemetry()
    state = entry()
    collector.entries[telemetry.identity(row)] = state
    async def read(*args, **kwargs): return SimpleNamespace(stdout=json.dumps(fixture()))
    monkeypatch.setattr(telemetry, 'run_remote', read)
    await collector.collect(row, state)
    assert collector.read(row)['available']
    if change in {'stale', 'future'}:
        row.provider_metadata['inventory']['checked_at'] = (datetime.utcnow() + timedelta(seconds=121 if change == 'future' else -121)).isoformat()
    elif change == 'absent': row.provider_metadata['inventory']['present'] = False
    elif change == 'stopped': row.provider_metadata['inventory']['running'] = False
    elif change in {'host', 'port', 'username', 'remote_root'}:
        setattr(row, change, 23 if change == 'port' else 'changed')
    elif change == 'key': row.host_key_sha256 = 'b' * 64
    elif change == 'attempt': row.provider_metadata['setup']['started_at'] = 'new-generation'
    elif change == 'time': row.activated_at += timedelta(seconds=1)
    elif change == 'tools': row.provider_metadata['attachment']['telemetry'] = False
    else: row.active = False
    assert not collector.read(row)['available']


@pytest.mark.asyncio
@pytest.mark.parametrize('previously_attached', [False, True])
async def test_attachment_requires_a_usable_requested_root(store, monkeypatch, previously_attached):
    session, _ = store
    inventory(monkeypatch, ['49674511'])
    previous_time = None
    if previously_attached:
        connection_double(monkeypatch)
        previous = await attach_failed(session)
        previous_time = previous.activated_at
    async def occupied(): raise transport.RemoteTransportError('untrusted remote permission detail')
    connection_double(monkeypatch, authenticate=occupied)
    with pytest.raises(targets.ExecutionTargetError, match='Authenticated attachment root check failed'):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert not row.active and not targets.telemetry_eligible(row)
    if previously_attached:
        assert row.activated_at == previous_time  # history, not current attachment authority
    else:
        assert row.host_key_sha256 is None and row.activated_at is None


@pytest.mark.asyncio
async def test_changed_pinned_key_on_retry_refuses_before_authentication(store, monkeypatch):
    session, _ = store
    inventory(monkeypatch, ['49674511'])
    connection_double(monkeypatch)
    await attach_failed(session)
    async def changed_key(*args): return 'changed fixture key', 'b' * 64
    async def forbidden(*args, **kwargs): pytest.fail('Changed host key must never reach SSH')
    monkeypatch.setattr(targets, 'capture_host_key', changed_key)
    monkeypatch.setattr(targets, 'run_remote', forbidden)
    with pytest.raises(targets.ExecutionTargetError, match='SSH host key changed'):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert not row.active and not targets.telemetry_eligible(row)
    assert row.host_key_sha256 == 'a' * 64


@pytest.mark.asyncio
async def test_ssh_exit_255_is_connection_failure_not_runtime_blocker(monkeypatch):
    async def run(*args, **kwargs):
        return transport.CommandResult(255, '', 'Permission denied (publickey).')
    monkeypatch.setattr(transport, '_run', run)
    monkeypatch.setattr(transport, '_ssh_base', lambda connection: ['ssh', 'fixture'])
    with pytest.raises(transport.RemoteConnectionError, match='authentication failed'):
        await transport.run_remote(transport.RemoteConnection('vast:1', 'fixture', 22, 'root', '/worker'), ['true'])

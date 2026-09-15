"""Long bundle preparation must not reuse aged ORM admission observations.

Real launch/readiness/resource/publication owners and independent file-SQLite
engines; only provider, telemetry, bundle bytes and worker transport are doubles.
"""
import asyncio
from datetime import datetime, timedelta
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, Job
from services.remote_execution import executor as ex, targets, telemetry
from services.remote_execution.contracts import (
    DiscoveredExecutionTarget, ExecutionTargetInventoryResponse, RemoteAttemptStatus,
)
from test_remote_lifecycle_gaps import lifecycle_invocation


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'fresh', 'stale', 'unknown', 'absent', 'stopped', 'inactive', 'preload',
    'capacity', 'endpoint', 'fingerprint', 'attachment', 'runtime',
    'lease_epoch', 'lease_owner', 'cancelled',
])
async def test_bundle_preparation_refreshes_admission_without_retargeting(tmp_path, monkeypatch, change):
    class Clock(datetime):
        now_value = datetime.utcnow()

        @classmethod
        def utcnow(cls):
            return cls.now_value

    monkeypatch.setattr(targets, 'datetime', Clock)
    url = f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}"
    engine, writer_engine = create_async_engine(url), create_async_engine(url)
    # Distinct pools guarantee the inventory writer has a different physical
    # connection, even after the launch owner returns its connection to its pool.
    assert engine.pool is not writer_engine.pool
    store = async_sessionmaker(engine, expire_on_commit=False)
    writer = async_sessionmaker(writer_engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(ex, 'async_session', store)
    monkeypatch.setattr(ex, 'get_data_root', lambda: tmp_path)
    epoch = Clock.utcnow()
    async with store() as s:
        s.add(ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1',
            active=True, state='ready', host='worker.test', port=22, username='root',
            remote_root='/worker', host_key_sha256='fingerprint', activated_at=epoch,
            leased_job_id='job', lease_acquired_at=epoch,
            provider_metadata={'inventory': {'status': 'complete', 'present': True,
                'running': True, 'checked_at': epoch.isoformat()}}))
        s.add(Job(id='job', name='job', model_id='boltz2', mode='predict', params={},
            status='queued', queue_status='preparing', remote_state='preparing',
            execution_target_id='vast:1', execution_source_revision='a' * 40,
            execution_source_tree='b' * 40,
            provenance={'remote_execution_assignment': {'claimed_at': epoch.isoformat() + 'Z'}}))
        await s.commit()

    async def inventory():
        return ExecutionTargetInventoryResponse(provider='vast', available=True,
            credential_configured=True, message='Complete owned inventory', instances=[
                DiscoveredExecutionTarget(provider='vast', provider_instance_id='1',
                    provider_state='running', host='worker.test', port=22, username='root',
                    gpu_count=1, gpu_name='Test GPU', gpu_vram_mb=40960)])

    monkeypatch.setattr(targets, 'list_owned_instances', inventory)
    entered, release = threading.Event(), threading.Event()
    old_snapshots, admissions, staged, commands = [], [], [], []
    bundle = SimpleNamespace(attempt_id='attempt', envelope_sha256='hash',
        remote_attempt_dir='/worker/attempts/attempt', envelope=SimpleNamespace(
            source_revision='a' * 40, source_tree='b' * 40,
            environment={'BMS_TARGET_RESOURCES': json.dumps({
                'required': {'cpus': 1, 'memory_bytes': 1, 'scratch_bytes': 1}, 'gpu_ids': [0]})}))

    def prepare(**kwargs):
        old_snapshots.append(kwargs['target'].provider_metadata['inventory']['checked_at'])
        entered.set()
        if not release.wait(10):
            raise TimeoutError('Inventory interleaving did not complete')
        return bundle

    def sample(target, **_):
        admissions.append(target.provider_metadata['inventory']['checked_at'])
        return {'available': True, 'observed_at': Clock.utcnow().isoformat(),
            'cpu': {'allocated_cores': 2}, 'ram': {'limit_bytes': 100, 'used_bytes': 0},
            'disk': {'path': '/worker', 'free_bytes': 0 if change == 'capacity' else 100},
            'gpus': [{'index': 0, 'uuid': 'test-gpu', 'memory_total_mb': 40960, 'memory_used_mb': 0}]}

    async def noop(*_, **__):
        pass

    async def stage(*_):
        staged.append(True)

    async def remote(_connection, argv, **_):
        commands.append(argv[0])
        status = RemoteAttemptStatus(job_id='job', attempt_id='attempt', state='running',
            boot_id='test-boot', quiescent=False, started_at=Clock.utcnow())
        return SimpleNamespace(stdout=status.model_dump_json(), stderr='', returncode=0)

    monkeypatch.setattr(ex, '_verify_remote_runner', noop)
    monkeypatch.setattr(ex, 'prepare_remote_bundle', prepare)
    monkeypatch.setattr(telemetry.remote_telemetry, 'read', sample)
    from services.remote_execution import bundle as bundle_module
    monkeypatch.setattr(bundle_module, 'bind_resource_admission',
        lambda value, admission, *, resource_monitor=None: value)
    monkeypatch.setattr(ex, '_archive_envelope', lambda *_: None)
    monkeypatch.setattr(ex, '_cleanup_local_bundle', lambda *_: None)
    monkeypatch.setattr(ex, '_stage_bundle', stage)
    monkeypatch.setattr(ex, '_worker_argv', lambda _, action, *__: [action])
    monkeypatch.setattr(ex, 'run_remote', remote)
    monkeypatch.setattr(ex, '_remote_receipt', lambda b, t, **kw: {
        'attempt_id': b.attempt_id, 'state': kw['state'],
        'lease_acquired_at': t.lease_acquired_at.isoformat()})

    async def launch():
        async with store() as s:
            return await ex.launch_remote_job(s, await s.get(Job, 'job'),
                command=['true'], native_invocation=lifecycle_invocation(['true']))

    task = asyncio.create_task(launch())
    identity_changes = {'endpoint', 'fingerprint', 'attachment', 'runtime', 'lease_epoch', 'lease_owner'}
    try:
        assert await asyncio.to_thread(entered.wait, 5), 'Launch did not enter bundle preparation'
        # No sleep or raised TTL: the unchanged production freshness window
        # expires while its normal publisher commits a newer observation.
        Clock.now_value += timedelta(seconds=targets.INVENTORY_MAX_AGE_SECONDS + 1)
        async with writer() as s:
            if change != 'stale':
                await targets.refresh_vast_targets(s)
            target = await s.get(ExecutionTarget, 'vast:1')
            if change in {'unknown', 'absent', 'stopped'}:
                fields = {'unknown': {'status': 'unknown'}, 'absent': {'present': False},
                          'stopped': {'running': False}}[change]
                target.provider_metadata = {**target.provider_metadata,
                    'inventory': {**target.provider_metadata['inventory'], **fields}}
            elif change == 'inactive':
                target.active, target.state = False, 'inactive'
            elif change == 'preload':
                target.provider_metadata = {**target.provider_metadata,
                    'preload': {'phase': 'transferring', 'operation_id': 'new-preload'}}
            elif change == 'endpoint':
                target.host = 'other-worker.test'
            elif change == 'fingerprint':
                target.host_key_sha256 = 'new-fingerprint'
            elif change == 'attachment':
                target.activated_at = Clock.utcnow()
            elif change == 'runtime':
                target.capabilities = {**target.capabilities,
                    'critical_runtime_binding': {'release_sha256': 'replacement'}}
            elif change == 'lease_epoch':
                target.lease_acquired_at = Clock.utcnow()
            elif change == 'lease_owner':
                target.leased_job_id = 'successor'
            elif change == 'cancelled':
                job = await s.get(Job, 'job')
                job.status, job.queue_status = 'cancelled', 'cancelled'
            await s.commit()
            assert targets.target_eligible(target) == (change in {'fresh', 'capacity', 'cancelled'} | identity_changes)
        release.set()
        if change == 'fresh':
            assert await asyncio.wait_for(task, 5) == 'remote:attempt'
            assert staged == [True] and commands == ['prepare', 'run']
            assert admissions == [Clock.utcnow().isoformat()] * 2
            assert old_snapshots == [epoch.isoformat()]
            async with writer() as s:
                assert (await s.get(Job, 'job')).status == 'running'
        else:
            with pytest.raises(ex.RemoteExecutionError):
                await asyncio.wait_for(task, 5)
            assert not staged and not commands
            async with writer() as s:
                job = await s.get(Job, 'job')
                target = await s.get(ExecutionTarget, 'vast:1')
                if change in identity_changes:
                    assert job.status == 'queued' and job.remote_attempt_id is None
                    assert target.leased_job_id == ('successor' if change == 'lease_owner' else 'job')
                elif change == 'cancelled':
                    assert job.status == 'cancelled' and job.remote_attempt_id is None
                else:
                    assert (job.status, job.remote_state) == ('failed', 'launch_failed')
                    assert target.leased_job_id is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
        await writer_engine.dispose()

"""Regression probes for lease, poll isolation, and live preparation authority."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store, preparing, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [False, True])
async def test_lost_lease_must_fence_observation_and_terminal(store, monkeypatch, terminal):
    async with store() as s:
        (await s.get(Job, 'job')).remote_state = 'old_state'
        await s.commit()
    async def remote_status(*_):
        async with store() as other:
            (await other.get(ExecutionTarget, 'target')).leased_job_id = 'successor-job'
            await other.commit()
        return receipt('failed' if terminal else 'running')
    monkeypatch.setattr(ex, 'remote_status', remote_status)
    async with store() as s:
        assert not await ex.reconcile_remote_job(s, await s.get(Job, 'job'))
    async with store() as s:
        job = await s.get(Job, 'job')
        assert (job.status, job.remote_state) == ('running', 'old_state')
        assert (await s.get(ExecutionTarget, 'target')).leased_job_id == 'successor-job'


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_finalizer_lost_lease_after_ingester_commit(store, fails):
    from services.result_state_integrity import finalize_successful_job
    async with store() as s:
        (await s.get(Job, 'job')).model_id = 'custom_file_workflow'
        await s.commit()
    async def ingest(_id, _root, s, **_):
        await s.commit()
        async with store() as other:
            (await other.get(ExecutionTarget, 'target')).leased_job_id = 'successor-job'
            await other.commit()
        if fails:
            raise ValueError('ingestion failed after commit')
        return 0
    async with store() as s:
        result = await finalize_successful_job(await s.get(Job, 'job'), '/unused', s, ingest_fn=ingest)
    assert not result.completed
    async with store() as s:
        assert (await s.get(Job, 'job')).status == 'running'
        assert (await s.get(ExecutionTarget, 'target')).leased_job_id == 'successor-job'


@pytest.mark.asyncio
@pytest.mark.parametrize('first_errors', [False, True])
async def test_production_poller_reconciles_two_remote_jobs_and_local(store, monkeypatch, first_errors):
    from services import gpu_orchestrator as go
    async with store() as s:
        s.add(Job(id='job2', name='job2', model_id='boltz2', mode='predict', params={}, status='running', queue_status='running', execution_target_id='target2', nextflow_run_id='remote:attempt2', remote_attempt_id='attempt2', remote_state='running'))
        s.add(ExecutionTarget(id='target2', provider='vast', provider_instance_id='2', leased_job_id='job2', lease_acquired_at=datetime.utcnow()))
        s.add(Job(id='local', name='local', model_id='custom_file_workflow', mode='predict', params={}, status='running', queue_status='running'))
        await s.commit()
    seen, local_seen = [], []
    async def remote_status(s, job):
        job_id, attempt = job.id, job.remote_attempt_id
        seen.append(job_id)
        if first_errors and job_id == 'job':
            await s.rollback()
            raise ValueError('first job lost transaction')
        return receipt('running', job_id=job_id, attempt_id=attempt)
    def history(ids):
        local_seen.extend(ids)
        return {}
    monkeypatch.setattr(ex, 'remote_status', remote_status)
    monkeypatch.setattr(go, '_read_nextflow_history_statuses', history)
    poller = go.GPUOrchestrator.__new__(go.GPUOrchestrator)
    poller.db_session_factory = store
    await poller.check_job_completions()
    assert set(seen) == {'job', 'job2'}
    assert local_seen == ['local']


def test_controller_guard_is_cross_process_and_released_on_crash(tmp_path, monkeypatch):
    import multiprocessing
    monkeypatch.setattr(ex, 'get_data_root', lambda: tmp_path)
    ctx = multiprocessing.get_context('fork')
    parent, child = ctx.Pipe()
    def hold():
        with ex._controller_attempt_guard('job') as owned:
            child.send(owned)
            child.recv()
    process = ctx.Process(target=hold)
    process.start()
    try:
        assert parent.poll(5) and parent.recv() is True
        with ex._controller_attempt_guard('job') as owned:
            assert owned is False
        with ex._controller_attempt_guard('other-job') as owned:
            assert owned is True
        process.kill()
        process.join(5)
        assert not process.is_alive()
        with ex._controller_attempt_guard('job') as owned:
            assert owned is True
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        parent.close()
        child.close()


@pytest.mark.asyncio
async def test_live_staging_producer_is_not_expired(store, monkeypatch):
    await preparing(store)
    entered, release = asyncio.Event(), asyncio.Event()
    async def ready(s, *_):
        return await s.get(ExecutionTarget, 'target')
    async def noop(*_, **__):
        pass
    async def staging(*_):
        entered.set()
        await release.wait()
    async def unavailable(*_, **__):
        raise ex.RemoteTransportError('transfer has not finished')
    monkeypatch.setattr(ex, 'get_ready_target', ready)
    monkeypatch.setattr(ex.RemoteConnection, 'from_target', lambda *_: None)
    monkeypatch.setattr(ex, '_verify_remote_runner', noop)
    monkeypatch.setattr(ex, '_stage_bundle', staging)
    monkeypatch.setattr(ex, '_stage_secret_environment', noop)
    monkeypatch.setattr(ex, '_archive_envelope', lambda *_: None)
    monkeypatch.setattr(ex, '_cleanup_local_bundle', lambda *_: None)
    monkeypatch.setattr(ex, '_worker_argv', lambda *_: [])
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (None, '/attempt'))
    monkeypatch.setattr(ex, '_remote_receipt', lambda b, t, **kw: {'state': kw['state']})
    bundle = SimpleNamespace(attempt_id='attempt', envelope_sha256='hash', remote_attempt_dir='/attempt', envelope=SimpleNamespace(source_revision='rev', source_tree='tree'))
    monkeypatch.setattr(ex, 'prepare_remote_bundle', lambda **_: bundle)
    monkeypatch.setattr(ex, 'run_remote', unavailable)
    async def launch():
        async with store() as s:
            with pytest.raises(ex.RemoteExecutionError):
                await ex.launch_remote_job(s, await s.get(Job, 'job'), command=['true'])
    producer = asyncio.create_task(launch())
    await asyncio.wait_for(entered.wait(), 5)
    try:
        async with store() as s:
            j = await s.get(Job, 'job')
            j.provenance = dict(j.provenance, remote_execution_assignment={'claimed_at': (datetime.utcnow() - timedelta(hours=1)).isoformat() + 'Z'})
            await s.commit()
        async with store() as s:
            await ex.reconcile_remote_job(s, await s.get(Job, 'job'))
        async with store() as s:
            j = await s.get(Job, 'job')
            assert (j.status, j.queue_status, j.remote_state) == ('queued', 'preparing', 'staging')
            assert (await s.get(ExecutionTarget, 'target')).leased_job_id == 'job'
    finally:
        release.set()
        await producer

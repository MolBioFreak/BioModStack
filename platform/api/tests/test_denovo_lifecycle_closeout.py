"""Independent SQLite sessions at cancellation/projection/claim boundaries."""
import asyncio
from collections import deque
from datetime import datetime
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update

from database import Design, ExecutionTarget, Job
from services import job_control, stage_review
from services.gpu_orchestrator import GPUOrchestrator
from services.remote_execution import executor, targets, telemetry
from services.remote_execution.claims import outstanding_claim_clause, target_idle_clause
from test_local_gate_review_rows import store, _job, _isolate
from test_remote_gate_review_rows import _retrieved


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh', [False, True])
async def test_continue_cannot_replace_durable_cancel_intent(store, tmp_path, monkeypatch, fresh):
    async with store() as seed:
        job = _job(tmp_path, ['one'])
        job.nextflow_run_id = 'owned-unit'
        job.provenance = {}
        seed.add(job)
        await seed.commit()
    async with store() as continuation, store() as cancellation:
        stale = await continuation.get(Job, 'gate-job')
        async def stop(_):
            if fresh:
                await continuation.refresh(stale)
            # Exact route UPDATE contract, not a mock of Continue or cancellation.
            predicates = (job_control.checkpoint_continuation_predicates(stale)
                          if hasattr(job_control, 'checkpoint_continuation_predicates') else (
                              Job.id == stale.id, Job.status == 'awaiting_input', Job.awaiting_input.is_(True),
                              Job.awaiting_payload == stale.awaiting_payload, Job.provenance == stale.provenance))
            changed = await continuation.execute(update(Job).where(*predicates).values(
                status='queued', queue_status='queued', awaiting_input=False, nextflow_run_id=None))
            await continuation.commit()
            assert changed.rowcount == 0
            return True
        monkeypatch.setattr(job_control, 'cancel_nextflow_job', stop)
        await job_control.cancel_job_lineage('gate-job', cancellation)
    async with store() as verify:
        current = await verify.get(Job, 'gate-job')
        assert current.status == current.queue_status == 'cancelled'
        assert current.awaiting_payload == {}
        assert not (await verify.scalars(select(Job.id).where(Job.queue_status == 'queued'))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_owner', ['cancel', 'checkpoint'])
async def test_remote_projection_does_not_overwrite_committed_owner(store, tmp_path, monkeypatch, changed_owner):
    _isolate(monkeypatch)
    async with store() as seed:
        seed.add(_job(tmp_path, ['one']))
        await seed.commit()
    expected = {} if changed_owner == 'cancel' else {'component_checkpoint': {'checkpoint_id': 'new'}}
    async def fetch(session, job, checkpoint):
        # Before the publisher's first DML: a real independent writer commits.
        async with store() as writer:
            await writer.execute(update(Job).where(Job.id == job.id).values(
                status='cancelled' if changed_owner == 'cancel' else 'awaiting_input',
                awaiting_input=changed_owner != 'cancel', awaiting_payload=expected))
            await writer.commit()
        return _retrieved(tmp_path, checkpoint)
    monkeypatch.setattr(executor, 'retrieve_remote_checkpoint_review', fetch)
    async with store() as publisher:
        job = await publisher.get(Job, 'gate-job')
        assert await executor.publish_remote_review_rows(publisher, job) == 0
    async with store() as verify:
        assert (await verify.get(Job, 'gate-job')).awaiting_payload == expected
        assert not (await verify.scalars(select(Design))).all()


@pytest.mark.asyncio
async def test_stage_owner_rejects_stale_local_snapshot(store, tmp_path, monkeypatch):
    _isolate(monkeypatch)
    async with store() as seed:
        job = _job(tmp_path, ['one'])
        job.awaiting_payload = dict(job.awaiting_payload, candidate_dir=str(tmp_path / 'out/.bms-review/abc/candidate'))
        seed.add(job)
        await seed.commit()
    async with store() as publisher:
        stale = await publisher.get(Job, 'gate-job')
        async with store() as canceller:
            await job_control.cancel_job_lineage('gate-job', canceller)
        assert await stage_review.ensure_stage_review_rows(publisher, stale) == 0
    async with store() as verify:
        job = await verify.get(Job, 'gate-job')
        assert job.status == 'cancelled' and job.awaiting_payload == {}
        assert not (await verify.scalars(select(Design))).all()


def orchestrator(store):
    async def forbidden(**kwargs):
        pytest.fail('review recovery must not launch science')
    owner = GPUOrchestrator(store, lambda: [], forbidden)
    owner._next_binder_recovery_at = float('inf')
    return owner


@pytest.mark.asyncio
async def test_cold_scheduler_recovers_rows_after_projection_flush_failure(store, tmp_path, monkeypatch):
    _isolate(monkeypatch)
    async with store() as seed:
        job = _job(tmp_path, ['one', 'two'])
        # Reproduce a historical candidate_dir committed before missing rows.
        job.awaiting_payload = dict(job.awaiting_payload, candidate_dir=str(tmp_path / 'out/.bms-review/abc/candidate'))
        seed.add(job)
        await seed.commit()
    from sqlalchemy.orm import Session
    def fail_projection(session, *_):
        if any(isinstance(row, Design) for row in session.new):
            raise RuntimeError('injected projection flush failure')
    event.listen(Session, 'before_flush', fail_projection)
    try:
        await orchestrator(store).check_job_completions()
    finally:
        event.remove(Session, 'before_flush', fail_projection)
    async with store() as verify:
        assert (await verify.get(Job, 'gate-job')).status == 'awaiting_input'
        assert not (await verify.scalars(select(Design))).all()
    await orchestrator(store).check_job_completions()
    async with store() as verify:
        ids = set((await verify.scalars(select(Design.id))).all())
        assert len(ids) == 2
    await orchestrator(store).check_job_completions()
    async with store() as verify:
        assert set((await verify.scalars(select(Design.id))).all()) == ids


@pytest.mark.asyncio
async def test_delete_handoff_keeps_existing_claim_discoverable(store, tmp_path, monkeypatch):
    async with store() as seed:
        seed.add(ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1', active=True, state='ready'))
        job = _job(tmp_path, ['one'], execution_target_id='vast:1', remote_attempt_id='attempt')
        job.nextflow_run_id = 'remote:attempt'
        job.provenance = {'remote_execution_assignment': dict(policy='vram_packing', lease_id='lease',
                          execution_target_id='vast:1', root_job_id=job.id)}
        seed.add(job)
        await seed.commit()
    async def unreachable(_):
        return False
    monkeypatch.setattr(job_control, 'cancel_nextflow_job', unreachable)
    async with store() as deletion:
        job, _ = await job_control.cancel_job_lineage('gate-job', deletion)
        if hasattr(job_control, 'retain_permanent_delete_cleanup'):
            assert await job_control.retain_permanent_delete_cleanup(deletion, job)
        else:
            await deletion.delete(job)  # old permanent route's physical deletion
        await deletion.commit()
    async with store() as cold:
        assert (await cold.scalars(select(Job.id).where(outstanding_claim_clause()))).all() == ['gate-job']
        assert not (await cold.scalars(select(ExecutionTarget.id).where(target_idle_clause()))).all()
        job = await cold.get(Job, 'gate-job')
        assert job.params['permanent_delete_cleanup'] is True
        assert job.params['cancellation_receipt']['remote_stop_verified'] is False
    # Cold existing orchestrator must dispatch this exact existing owner.
    dispatched = []
    async def observe(session, job):
        dispatched.append(job.id)
    original_reconcile = executor.reconcile_remote_job
    monkeypatch.setattr(executor, 'reconcile_remote_job', observe)
    scheduler = orchestrator(store)
    await scheduler.check_job_completions()
    await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())
    assert dispatched == ['gate-job']
    monkeypatch.setattr(executor, 'reconcile_remote_job', original_reconcile)
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    async def stopped(session, job, **kwargs):
        return executor.RemoteAttemptStatus(job_id=job.id, attempt_id='attempt',
            state='cancelled', boot_id='boot', quiescent=True)
    monkeypatch.setattr(executor, 'remote_status', stopped)
    # Join the existing reconciliation task through its retained orchestrator.
    scheduler = orchestrator(store)
    await scheduler.check_job_completions()
    await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())
    async with store() as cold:
        job = await cold.get(Job, 'gate-job')
        assert job.params['permanent_delete_cleanup'] is True
        assert job.remote_state == 'cancelled'
        assert not (await cold.scalars(select(Job.id).where(outstanding_claim_clause()))).all()
        assert (await cold.scalars(select(ExecutionTarget.id).where(target_idle_clause()))).all() == ['vast:1']


@pytest.mark.asyncio
async def test_deleted_return_owner_survives_busy_controller_guard(store, tmp_path, monkeypatch):
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    async with store() as seed:
        job = _job(tmp_path, ['one'], execution_target_id='vast:1', remote_attempt_id='attempt')
        job.status, job.queue_status, job.remote_state = 'cancelled', 'cancelled', 'returning'
        job.awaiting_input = False
        job.params = {'cancellation_receipt': {'remote_stop_verified': False}}
        seed.add(job)
        await seed.commit()
    with executor._controller_attempt_guard('gate-job') as owned:
        assert owned
        async with store() as deletion:
            job = await deletion.get(Job, 'gate-job')
            assert not await executor.cancel_local_result_transfer(job, timeout=0.01)
            assert await job_control.retain_permanent_delete_cleanup(deletion, job)
            await deletion.commit()
        scheduler = orchestrator(store)
        await scheduler.check_job_completions()
        await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())
        async with store() as verify:
            assert (await verify.get(Job, 'gate-job')).remote_state == 'returning'
    scheduler = orchestrator(store)
    await scheduler.check_job_completions()
    await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())
    async with store() as verify:
        job = await verify.get(Job, 'gate-job')
        assert job.remote_state == 'cancelled' and job.params['permanent_delete_cleanup']


@pytest.mark.asyncio
@pytest.mark.parametrize('candidate_marker', [False, True])
async def test_remote_claim_free_gate_retries_on_cold_scan(store, tmp_path, monkeypatch, candidate_marker):
    _isolate(monkeypatch)
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(executor, '_review_row_attempts', {})
    async with store() as seed:
        job = _job(tmp_path, ['one'], execution_target_id='vast:1', remote_attempt_id='attempt')
        job.nextflow_run_id = 'remote:attempt'
        job.remote_state = 'awaiting_input'
        cp = dict(job.awaiting_payload['component_checkpoint'], attempt_id='attempt', target_id='vast:1', lease_id='lease')
        job.awaiting_payload = {'component_checkpoint': cp}
        if candidate_marker:
            job.awaiting_payload['candidate_dir'] = str(tmp_path / 'not-fetched')
        seed.add(job)
        await seed.commit()
    async def observed(session, job, **kwargs):
        return executor.RemoteAttemptStatus(job_id=job.id, attempt_id='attempt', state='awaiting_input',
            boot_id='boot', quiescent=True, checkpoints=[cp])
    monkeypatch.setattr(executor, 'remote_status', observed)
    attempts = []
    async def fetch(session, job, checkpoint):
        attempts.append(job.id)
        if len(attempts) == 1:
            raise executor.RemoteExecutionError('transient transport failure')
        return _retrieved(tmp_path, checkpoint)
    monkeypatch.setattr(executor, 'retrieve_remote_checkpoint_review', fetch)
    for _ in range(2):
        scheduler = orchestrator(store)
        executor._review_row_attempts.clear()  # cold controller restart
        await scheduler.check_job_completions()
        await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())
    assert attempts == ['gate-job', 'gate-job']
    async with store() as verify:
        assert len((await verify.scalars(select(Design))).all()) == 1
        assert (await verify.get(Job, 'gate-job')).status == 'awaiting_input'


@pytest.mark.asyncio
async def test_real_stale_telemetry_admission_uses_control_error(store, tmp_path, monkeypatch):
    service = telemetry.RemoteTelemetry()
    monkeypatch.setattr(telemetry, 'remote_telemetry', service)
    monkeypatch.setattr(executor, '_connection_for_attempt', lambda *_: (object(), '/fixture/attempt'))
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    async with store() as session:
        target = ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1', active=True, state='ready',
            host='fixture', port=22, username='fixture', remote_root='/fixture', host_key_sha256='SHA256:fixture',
            activated_at=datetime.utcnow(), capabilities={'gpu_count': 1}, provider_metadata={'inventory': {'status': 'complete', 'present': True,
            'running': True, 'checked_at': datetime.utcnow().isoformat()}})
        job = _job(tmp_path, ['one'], execution_target_id=target.id, remote_attempt_id='attempt')
        cp = dict(attempt_id='attempt', target_id=target.id, lease_id='lease', checkpoint_id='review', checkpoint_sha256='a'*64)
        job.awaiting_payload = {'component_checkpoint': cp}
        job.provenance = dict(remote_execution_receipt=dict(boot_id='boot', source_revision=None, source_tree=None,
            execution_envelope_sha256=None, component_context_identity=dict(root_job_id=job.id, attempt_id='attempt',
            target_id=target.id, lease_id='lease')), remote_execution_assignment=dict(resources=dict(gpu_ids=[0])))
        session.add_all([target, job])
        await session.commit()
        sample = dict(available=True, gpus=[dict(index=0, uuid='GPU-zero', memory_total_mb=1000, memory_used_mb=0)])
        service.entries[telemetry.identity(target)] = dict(history=deque([(1, time.monotonic()-30, sample)]))
        assert service.read(target)['error'] == 'Remote telemetry is stale'
        with pytest.raises(executor.RemoteExecutionError, match='stale'):
            await executor.request_remote_checkpoint_resume(session, job, cp, {'continue': True})
    async with store() as verify:
        job = await verify.get(Job, 'gate-job')
        assert job.status == 'awaiting_input' and job.awaiting_payload == {'component_checkpoint': cp}
        assert 'remote_checkpoint_operation' not in job.provenance
        assert not (await verify.scalars(select(Job.id).where(outstanding_claim_clause()))).all()

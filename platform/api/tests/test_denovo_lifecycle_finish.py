"""Private cancellation custody: existing reconciler completes deletion."""
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select, func

from database import ExecutionTarget, Job
from services import job_control
from services.remote_execution import executor
from services.remote_execution.claims import outstanding_claim_clause, target_idle_clause
from test_local_gate_review_rows import store, _job
from test_denovo_lifecycle_closeout import orchestrator


async def sweep(store):
    scheduler = orchestrator(store)
    await scheduler.check_job_completions()
    await asyncio.gather(*scheduler._remote_reconciliation_tasks.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('sharing', ['none', 'equal', 'nested', 'ancestor', 'project', 'scientific'])
async def test_cancel_delete_reconcile_finishes_custody(store, tmp_path, monkeypatch, sharing):
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    output = tmp_path / 'out'
    output.mkdir()
    (output / 'result').write_text('owned')
    retained = output / 'retained'
    retained.mkdir()
    (retained / 'bytes').write_text('keep')
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(retained if sharing == 'project' else tmp_path / 'projects'))
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(retained if sharing == 'scientific' else tmp_path / 'scientific'))
    async with store() as seed:
        seed.add(ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1', active=True, state='ready'))
        job = _job(tmp_path, ['one'], execution_target_id='vast:1', remote_attempt_id='attempt')
        job.output_dir = str(output)
        job.nextflow_run_id = 'remote:attempt'
        job.provenance = {'remote_execution_assignment': dict(policy='vram_packing', lease_id='lease',
                          execution_target_id='vast:1', root_job_id=job.id)}
        seed.add(job)
        if sharing in {'equal', 'nested', 'ancestor'}:
            other = _job(tmp_path, ['other'])
            other.id, other.status, other.queue_status = 'other', 'completed', 'completed'
            other.awaiting_input = False
            other.output_dir = str({'equal': output, 'nested': retained, 'ancestor': tmp_path}[sharing])
            seed.add(other)
        await seed.commit()
    async def unreachable(_):
        return False
    monkeypatch.setattr(job_control, 'cancel_nextflow_job', unreachable)
    async with store() as deletion:
        job, _ = await job_control.cancel_job_lineage('gate-job', deletion)
        assert await job_control.retain_permanent_delete_cleanup(deletion, job)
        await deletion.commit()
    async def unavailable(*args, **kwargs):
        raise executor.RemoteExecutionError('private unavailable fixture')
    monkeypatch.setattr(executor, 'remote_status', unavailable)
    monkeypatch.setattr(executor, 'cancel_remote_job', unavailable)
    await sweep(store)
    async with store() as verify:
        assert await verify.get(Job, 'gate-job') is not None
        assert (await verify.scalars(select(Job.id).where(outstanding_claim_clause()))).all() == ['gate-job']
        # Exact public visibility contract: never hide from claim/recovery readers.
        assert not (await verify.scalars(select(Job.id).where(Job.id == 'gate-job',
            func.coalesce(Job.params['permanent_delete_cleanup'].as_boolean(), False).is_(False)))).all()
    assert output.exists()
    async def stopped(session, job, **kwargs):
        return executor.RemoteAttemptStatus(job_id=job.id, attempt_id='attempt',
            state='cancelled', boot_id='boot', quiescent=True)
    monkeypatch.setattr(executor, 'remote_status', stopped)
    await sweep(store)
    async with store() as verify:
        assert await verify.get(Job, 'gate-job') is None
        assert not (await verify.scalars(select(Job.id).where(outstanding_claim_clause()))).all()
        assert (await verify.scalars(select(ExecutionTarget.id).where(target_idle_clause()))).all() == ['vast:1']
    assert output.exists() is (sharing != 'none')
    if sharing != 'none':
        assert (retained / 'bytes').read_text() == 'keep'
    await sweep(store)
    async with store() as verify:
        assert await verify.get(Job, 'gate-job') is None


@pytest.mark.asyncio
async def test_cleanup_failure_retries_completed_hidden_owner(store, tmp_path, monkeypatch):
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    output = tmp_path / 'output'
    output.mkdir()
    async with store() as seed:
        job = _job(tmp_path, ['one'], execution_target_id='vast:1', remote_attempt_id='attempt')
        job.status = job.queue_status = job.remote_state = 'cancelled'
        job.output_dir = str(output)
        job.provenance = {}
        job.params = {'permanent_delete_cleanup': True, 'cancellation_receipt': {'state': 'completed'}}
        seed.add(job)
        await seed.commit()
    import shutil
    original = shutil.rmtree
    def fail(*args, **kwargs):
        raise OSError('private cleanup interruption')
    monkeypatch.setattr(shutil, 'rmtree', fail)
    await sweep(store)
    async with store() as verify:
        assert await verify.get(Job, 'gate-job') is not None
    monkeypatch.setattr(shutil, 'rmtree', original)
    with executor._controller_attempt_guard('gate-job') as owned:
        assert owned
        await sweep(store)
        assert output.exists()
    await sweep(store)
    async with store() as verify:
        assert await verify.get(Job, 'gate-job') is None
    assert not output.exists()
    await sweep(store)

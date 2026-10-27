"""Scratch scheduler-to-owned-runner tests with a real MAFFT container."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job, JobArtifact
from routers import jobs
from schemas import JobCreate
from services import gpu_orchestrator, nextflow
from tests.test_core_protein_scientific_admission import admission


@pytest.mark.asyncio
@pytest.mark.parametrize(('molecule', 'model'), [('protein', 'family_msa_protein'), ('dna', 'family_msa_nucleic')])
async def test_scheduler_adapter_to_native_owned_runner(admission, monkeypatch, molecule, model):
    image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
    if not image:
        pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to the isolated native image')
    monkeypatch.setenv('BMS_FAMILY_MSA_SIF', image)
    payload = {'roster': {'molecule': molecule, 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACGTACGT'},
        {'row_id': 'r2', 'header': 'two', 'sequence': 'ACGTACGA'}]}, 'settings': {'threads': 2}}
    created = await jobs._create_job(JobCreate(name='native scratch', model_id=model,
        mode='family_msa', params=payload), BackgroundTasks(), admission)
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    import database
    monkeypatch.setattr(database, 'async_session', factory)
    monkeypatch.setattr(gpu_orchestrator, 'read_scheduler_config', lambda: {'global': {'enabled': True}})
    monkeypatch.setattr(nextflow, 'assert_workflow_launch_allowed', lambda *a: None)
    monkeypatch.setattr(nextflow, 'configured_lane', lambda **k: None)
    monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: False)
    monkeypatch.setattr(nextflow, 'workflow_adapter_enabled', lambda: True)
    captured = []
    def adapter(**kwargs):
        captured.append(kwargs)
        return {'accepted': True, 'run_id': 'scratch-owned-unit'}
    monkeypatch.setattr(nextflow, 'launch_via_workflow_adapter', adapter)
    scheduler = gpu_orchestrator.GPUOrchestrator(factory, lambda: [], nextflow.launch_nextflow_job)
    await scheduler._process_cycle()
    await admission.refresh(await admission.get(Job, created.id))
    job = await admission.get(Job, created.id)
    assert job.status == 'running' and job.queue_status == 'running' and job.assigned_gpu is None
    assert len(captured) == 1 and job.nextflow_run_id == 'scratch-owned-unit'
    # Exercise the real runner branch. Only the systemd adapter boundary is simulated;
    # pytest does not start or stop host units.
    monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: True)
    monkeypatch.setattr(nextflow, 'configured_lane', lambda **k: 'scratch')
    monkeypatch.setattr(nextflow, 'assert_unit_lane', lambda *a: SimpleNamespace(job_id=created.id))
    monkeypatch.setenv(nextflow.TRANSIENT_WORKFLOW_UNIT_NAME_ENV, 'scratch-owned-unit')
    monkeypatch.setenv(nextflow.TRANSIENT_WORKFLOW_OWNER_NONCE_ENV, 'scratch-nonce')
    await nextflow.launch_nextflow_job(**captured[0], allow_running_job=True)
    await admission.refresh(job)
    assert job.status == 'completed' and job.queue_status == 'completed', job.error_message
    artifacts = (await admission.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
    roles = {a.provenance['role']: a for a in artifacts}
    assert {'input', 'alignment', 'engine_log', 'receipt'} <= roles.keys()
    assert '>r1' in Path(roles['alignment'].storage_path).read_text()
    assert Path(roles['engine_log'].storage_path).stat().st_size > 0


@pytest.mark.asyncio
async def test_owned_runner_cancellation_reaps_process(admission, monkeypatch, tmp_path):
    from services.family_msa.execution import run_owned_job
    executable = tmp_path / 'mafft'
    executable.write_text('#!/bin/sh\nprintf "%s" "$$" > "$PID_FILE"\nexec sleep 120\n')
    executable.chmod(0o755)
    pid_file = tmp_path / 'pid'
    monkeypatch.setenv('PID_FILE', str(pid_file))
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
    payload = {'roster': {'molecule': 'dna', 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACGT'}]}, 'settings': {'threads': 1}}
    job = Job(id='cancel-scratch', name='cancel', model_id='family_msa_nucleic', mode='family_msa',
              params=payload, status='running', queue_status='running', nextflow_run_id='scratch-unit')
    admission.add(job)
    await admission.commit()
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    task = asyncio.create_task(run_owned_job(job.id, payload, str(tmp_path / 'output'), factory))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.05)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

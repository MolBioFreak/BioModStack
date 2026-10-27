"""Scratch scheduler-to-owned-runner tests with a real MAFFT container."""
import asyncio
import hashlib
import json
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
@pytest.mark.parametrize(('molecule', 'model', 'settings', 'members'), [
    ('protein', 'family_msa_protein', {'engine': 'mafft', 'threads': 2}, 1),
    ('dna', 'family_msa_nucleic', {'engine': 'mafft', 'threads': 2}, 1),
    ('protein', 'family_msa_protein', {'engine': 'famsa'}, 1),
    *[(mol, 'family_msa_protein' if mol == 'protein' else 'family_msa_nucleic',
       {'engine': 'muscle', 'mode': mode, 'ensemble': ensemble, 'replicates': 2 if ensemble != 'none' else None}, count)
      for mol in ('protein', 'dna', 'rna') for mode, ensemble, count in
      [('align', 'none', 1), ('super5', 'none', 1), ('align', 'stratified', 8), ('align', 'diversified', 2)]],
    ('dna', 'family_msa_nucleic', {'engine': 'muscle', 'permutation': 'all'}, 4),
    ('dna', 'family_msa_nucleic', {'engine': 'twilight', 'method': 'tcs', 'guide_source': 'supplied',
       'guide_tree': '((a:0.1,b:0.1):0.1,c:0.1);', 'leaf_mapping': {'a': 'r1', 'b': 'r2', 'c': 'r3'}}, 1),
    *[(mol, 'family_msa_nucleic', {'engine': 'twilight', 'method': method, 'guide_source': 'mafft'}, 1)
      for mol in ('dna', 'rna') for method in ('pairwise', 'tcs')],
])
async def test_scheduler_adapter_to_native_owned_runner(admission, monkeypatch, molecule, model, settings, members, managed=False):
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
    if not image:
        pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to the isolated native image')
    if managed:
        import paths
        from publish_runtime_images import publish_references
        root = Path(admission.bind.url.database).parent / 'family-image-store'
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
        monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
        monkeypatch.delenv('BMS_RUNTIME_IMAGE_MSA_ENGINES_SIF', raising=False)
        monkeypatch.setattr(paths, 'get_container_dir', lambda: root.parent / 'containers')
        digest = hashlib.sha256(Path(image).read_bytes()).hexdigest()
        publish_references(root, 'production', {'BMS_RUNTIME_IMAGE_MSA_ENGINES_SIF':
            {'source': image, 'sha256': digest}})
    else:
        monkeypatch.setenv('BMS_FAMILY_MSA_SIF', image)
    seq = 'ACDEFGHIKLMNPQRSTVWY' if molecule == 'protein' else ('ACGU' if molecule == 'rna' else 'ACGT') * 5
    payload = {'roster': {'molecule': molecule, 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': seq},
        {'row_id': 'r2', 'header': 'two', 'sequence': seq},
        {'row_id': 'r3', 'header': 'three', 'sequence': seq[:-1] + 'A'}]}, 'settings': settings}
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
    if settings.get('permutation') == 'all':
        assert job.status == 'failed' and 'muscle exited' in job.error_message
        artifacts = (await admission.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
        logs = [Path(a.storage_path).read_text() for a in artifacts if a.provenance['role'] == 'engine_log']
        assert any('-perm all not supported' in text for text in logs)
        return
    assert job.status == 'completed' and job.queue_status == 'completed', job.error_message
    artifacts = (await admission.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
    roles = {a.provenance['role']: a for a in artifacts}
    assert {'input', 'alignment', 'engine_log', 'receipt'} <= roles.keys()
    receipt = json.loads(Path(roles['receipt'].storage_path).read_text())
    assert receipt['requested'] == payload
    assert receipt['effective_settings']['engine'] == settings['engine']
    runtime = receipt['runtime']
    assert runtime['sha256'] == hashlib.sha256(Path(image).read_bytes()).hexdigest()
    if managed:
        assert runtime['selection'] == 'managed'
        assert runtime['release_sha256'] == runtime['sha256']
        assert Path(runtime['path']).is_relative_to(root)
    alignments = [a for a in artifacts if a.provenance['role'] == 'alignment']
    assert len(alignments) == members
    assert all(Path(a.storage_path).stat().st_size > 0 for a in alignments)
    if settings['engine'] == 'mafft':
        assert '>r1' in Path(alignments[0].storage_path).read_text()
    else:
        assert 'row_map' in roles and 'native_receipt' in roles
        assert '>bmsrow' in Path(alignments[0].storage_path).read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(('molecule', 'model', 'settings'), [
    ('dna', 'family_msa_nucleic', {'engine': 'mafft', 'threads': 2}),
    ('protein', 'family_msa_protein', {'engine': 'famsa'}),
])
async def test_clean_root_registered_image_scheduler(admission, monkeypatch, molecule, model, settings):
    await test_scheduler_adapter_to_native_owned_runner(
        admission, monkeypatch, molecule, model, settings, 1, managed=True)


@pytest.mark.asyncio
async def test_owned_runner_cancellation_reaps_process(admission, monkeypatch, tmp_path):
    from services.family_msa.execution import run_owned_job
    monkeypatch.delenv('INVOCATION_ID', raising=False)
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


@pytest.mark.asyncio
async def test_cancel_api_stops_owned_runner(admission, monkeypatch, tmp_path):
    from services.family_msa.execution import run_owned_job
    from services import job_control
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    executable = tmp_path / 'famsa'
    executable.write_text('#!/bin/sh\nprintf "%s" "$$" > "$PID_FILE"\nexec sleep 120\n')
    executable.chmod(0o755)
    pid_file = tmp_path / 'pid'
    monkeypatch.setenv('PID_FILE', str(pid_file))
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
    payload = {'roster': {'molecule': 'protein', 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACDEFG'}]}, 'settings': {'engine': 'famsa'}}
    job = Job(id='cancel-api-scratch', name='cancel', model_id='family_msa_protein', mode='family_msa',
              params=payload, status='running', queue_status='running', nextflow_run_id='scratch-unit')
    admission.add(job)
    await admission.commit()
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    task = asyncio.create_task(run_owned_job(job.id, payload, str(tmp_path / 'output'), factory))
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.05)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    async def stop_owner(run_id):
        assert run_id == 'scratch-unit'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return True
    monkeypatch.setattr(job_control, 'cancel_nextflow_job', stop_owner)
    result = await jobs.cancel_job(job.id, admission)
    await admission.refresh(job)
    assert result['job_id'] == job.id and job.status == 'cancelled'
    assert job.params['cancellation_receipt']['remote_stop_verified'] is True
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['canceling', 'receipt', 'successor', 'owner'])
async def test_terminal_publication_fenced_by_live_authority(admission, monkeypatch, tmp_path, mutation):
    from services.family_msa.execution import run_owned_job
    executable = tmp_path / 'mafft'
    executable.write_text('#!/bin/sh\ntouch "$READY"\nwhile [ ! -f "$RELEASE" ]; do sleep 0.05; done\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('READY', str(tmp_path / 'ready'))
    monkeypatch.setenv('RELEASE', str(tmp_path / 'release'))
    monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
    monkeypatch.delenv('BMS_TRANSIENT_WORKFLOW_UNIT_NAME', raising=False)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    payload = {'roster': {'molecule': 'dna', 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACGT'}]}, 'settings': {'threads': 1}}
    if mutation == 'owner':
        monkeypatch.setenv('BMS_TRANSIENT_WORKFLOW_UNIT_NAME', 'unit-a')
        monkeypatch.setenv('BMS_TRANSIENT_WORKFLOW_OWNER_NONCE', 'nonce-a')
        monkeypatch.setenv('INVOCATION_ID', 'invocation-a')
        payload['execution_attempts'] = [{'unit': 'unit-a', 'owner_nonce': 'nonce-a',
            'invocation_id': 'invocation-a', 'state': 'started'}]
    job = Job(id='race-' + mutation, name='race', model_id='family_msa_nucleic', mode='family_msa',
              params=payload, status='running', queue_status='running', nextflow_run_id=None)
    admission.add(job)
    await admission.commit()
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    task = asyncio.create_task(run_owned_job(job.id, payload, str(tmp_path / 'output'), factory))
    for _ in range(200):
        if (tmp_path / 'ready').exists():
            break
        await asyncio.sleep(0.05)
    assert (tmp_path / 'ready').exists()
    if mutation == 'canceling':
        job.queue_status = 'canceling'
    elif mutation == 'receipt':
        job.params = {**payload, 'cancellation_receipt': {'schema': 'bms.workflow-cancellation.v1', 'state': 'requested'}}
    elif mutation == 'owner':
        job.params = {**payload, 'execution_attempts': [*payload['execution_attempts'],
            {'unit': 'unit-b', 'owner_nonce': 'nonce-b', 'invocation_id': 'invocation-b', 'state': 'started'}]}
    else:
        job.retry_count = (job.retry_count or 0) + 1
    await admission.commit()
    (tmp_path / 'release').touch()
    await task
    await admission.refresh(job)
    assert job.status == 'running'
    assert not (await admission.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()

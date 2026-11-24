"""Exercise the child launcher, not a direct call to the parent reconciler."""
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from component_runtime import NativeInvocation
from database import Job, JobArtifact, MdAttemptSegment, MdReplicaRun, MdRun
from schemas import JobCreate
from services import analysis_autorun, job_requests, job_submission, nextflow
from test_conformational_mapping_nextflow_failure import _configure_local_nextflow_launch
from test_molecular_dynamics_orchestration import _AcceptingRegistry
from test_md_results_trim import _seed, _tree, store


@pytest.mark.asyncio
@pytest.mark.parametrize('replace', [None, 'nextflow_run_id', 'remote_attempt_id'])
async def test_analysis_launcher_callback_origin(store, tmp_path, monkeypatch, replace):
    _, sessions = store
    root, spec = _tree(tmp_path, monkeypatch)
    rid, sid = await _seed(sessions, root, spec)
    parent_id, child_id = 'md-job-1', 'analysis-child-0'
    barrier = root / 'md_completion_barrier.json'
    barrier.unlink()
    (root / 'analysis/manifest.json').unlink()
    manifest = root / 'replicas/replica_0/manifest.json'
    work_item = root / 'work-item.json'
    work_item.write_text(json.dumps({'schema': 'bms.md.analysis-work-item.v1', 'replica_index': 0,
        'job_id': parent_id, 'manifest': str(manifest),
        'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    for module in (job_requests, job_submission):
        monkeypatch.setattr(module, 'get_registry', _AcceptingRegistry)
    monkeypatch.setattr(job_submission, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(job_submission, 'require_molecular_dynamics_feature', lambda *_: None)
    monkeypatch.setattr(job_submission, '_raise_if_workflow_launches_disabled', lambda *_: None)
    async with sessions() as session:
        parent = await session.get(Job, parent_id)
        parent.nextflow_run_id = 'parent-unit'
        await session.delete(await session.get(Job, child_id))
        await session.commit()
        response = await job_submission.submit_job(JobCreate(name='analysis', model_id='molecular_dynamics',
            mode='analyze', parent_job_id=parent_id, child_stage='md_analysis', params={
                'md_analysis_work_item': str(work_item),
                'md_analysis_sif_sha256': job_submission._MD_ANALYSIS_SIF_SHA256}), BackgroundTasks(), session)
        child_id = str(response.id)
        unit = f'biomodstack-development-job-{child_id}-attempt-1.service'
        child = await session.get(Job, child_id)
        child.output_dir = str(root / 'analysis')
        child.status = child.queue_status = 'running'
        child.nextflow_run_id = unit
        await session.commit()

    _configure_local_nextflow_launch(monkeypatch, sessions, tmp_path)
    monkeypatch.setenv('BMS_TRANSIENT_WORKFLOW_UNIT_NAME', unit)
    monkeypatch.setenv('BMS_TRANSIENT_WORKFLOW_OWNER_NONCE', 'fixture')
    monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: True)
    monkeypatch.setattr(nextflow, 'preflight_nextflow_java', lambda env: (True, 'fixture'))
    monkeypatch.setattr(nextflow, 'compile_job_nextflow_invocation', lambda *a, **kw:
        NativeInvocation.capture(model_id='molecular_dynamics', mode='analyze', command=('inert',),
            requested={}, effective={}, native_parameters={}, entrypoint='fixture.nf'))
    monkeypatch.setattr(nextflow, 'needs_component_runtime', lambda invocation: False)
    monkeypatch.setattr(nextflow, '_pin_local_invocation_images', AsyncMock(return_value={}))
    process = AsyncMock()
    process.wait.return_value = 0
    monkeypatch.setattr(nextflow.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda *_: None)

    # Commit replacement before callback entry, without changing status or files.
    original = nextflow.reconcile_md_analysis_parent_if_current
    entered = []
    async def delayed(parent_id, session, **kwargs):
        async with sessions() as peer:
            entered.append((await peer.get(Job, child_id)).status)
            if replace:
                setattr(await peer.get(Job, parent_id), replace, 'replacement')
                await peer.commit()
        return await original(parent_id, session, **kwargs)
    monkeypatch.setattr(nextflow, 'reconcile_md_analysis_parent_if_current', delayed)
    await nextflow.launch_nextflow_job(child_id, 'molecular_dynamics', 'analyze', {}, str(root / 'analysis'))
    process.wait.assert_awaited_once()
    assert entered == ['completed']
    async with sessions() as observer:
        parent = await observer.get(Job, parent_id)
        if replace:
            assert getattr(parent, replace) == 'replacement'
        expected = 'running' if replace else 'completed'
        assert parent.status == parent.queue_status == expected
        assert (await observer.get(MdRun, parent_id)).phase == ('finalizing' if replace else 'completed')
        assert (await observer.get(MdReplicaRun, rid)).state == expected
        assert (await observer.get(MdAttemptSegment, sid)).state == expected
        assert bool(await observer.scalar(select(JobArtifact.id).where(JobArtifact.owner_job_id == 'replica-child-0'))) == (not replace)
        assert barrier.exists() == (not replace)

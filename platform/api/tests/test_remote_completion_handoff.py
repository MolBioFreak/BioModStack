from test_remote_lifecycle_gaps import store
from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from services import result_ingester, analysis_autorun
import hashlib
import json
import pytest
from services import result_state_integrity as integrity

@pytest.mark.asyncio
@pytest.mark.parametrize('successor_lease', [False, True])
@pytest.mark.parametrize('return_policy', ['manual', 'automatic'])
async def test_real_completion_schedules_viewer_analysis(store, monkeypatch, tmp_path, successor_lease, return_policy):
    from test_remote_result_generation import package
    async with store() as s:
        job = await s.get(Job, 'job')
        job.model_id = 'custom_file_workflow'
        job.output_dir = str(tmp_path / "output")
        job.params = {"remote_result_policy": return_policy}
        manifest, incoming, terminal = package(job)
        job.provenance = {'remote_execution_receipt': {
            **{key: value for key, value in ex._pull_identity(job).items() if key != "schema"},
            "remote_attempt_dir": "/fixture/attempt",
            'expected_result_contract_sha256': hashlib.sha256(json.dumps(ex.resolve_job_result_contract(job), sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
        await s.commit()
    async def status(*_):
        return terminal
    async def collect(session, job, status):
        assert ex._verify_result_package(incoming, job, status) == manifest
        return manifest, incoming
    async def ingest(*_, **__):
        return 0
    original_finalizer = integrity.finalize_successful_job
    async def finalize(*args, **kwargs):
        result = await original_finalizer(*args, **kwargs)
        if successor_lease:
            async with store() as other:
                (await other.get(ExecutionTarget, 'target')).leased_job_id = 'successor-job'
                await other.commit()
        return result
    monkeypatch.setattr(integrity, 'finalize_successful_job', finalize)
    calls = []
    monkeypatch.setattr(ex, 'remote_status', status)
    monkeypatch.setattr(ex, 'collect_remote_results', collect)
    monkeypatch.setattr(result_ingester, 'ingest_job_results', ingest)
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda job_id: calls.append(job_id))
    from fastapi import BackgroundTasks
    async def proof(*_, **__):
        pass
    monkeypatch.setattr(ex, '_prove_pull_endpoint', proof)
    background = BackgroundTasks()
    async with store() as s:
        changed = await ex.reconcile_remote_job(s, await s.get(Job, 'job'), background_tasks=background)
        assert changed and not calls
        job = await s.get(Job, 'job')
        if return_policy == 'automatic':
            assert (job.status, job.remote_state) == ('running', 'returning')
        else:
            assert (job.status, job.remote_state) == ('awaiting_input', 'results_available')
            assert not background.tasks
            assert await ex.request_remote_result_pull(s, job, background)
        assert len(background.tasks) == 1
    await background()
    async with store() as s:
        job = await s.get(Job, 'job')
        lease = (await s.get(ExecutionTarget, 'target')).leased_job_id
        print('REAL_FINALIZER_HANDOFF', {'status':job.status, 'remote_state':job.remote_state, 'lease':lease, 'changed':changed, 'schedule_calls':calls})
        assert job.status == 'completed' and job.remote_state == 'ingested' and lease == ('successor-job' if successor_lease else None)
    assert calls == ['job'], 'successful real finalizer must retain the remote completion scheduling hook'
    assert changed is True
    async with store() as s:
        assert not await ex.reconcile_remote_job(s, await s.get(Job, 'job'))
    assert calls == ['job']

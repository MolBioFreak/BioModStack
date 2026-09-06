from test_remote_lifecycle_gaps import store, receipt
from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from services import result_ingester, analysis_autorun
from types import SimpleNamespace
import hashlib
import json
import pytest
from services import result_state_integrity as integrity

@pytest.mark.asyncio
@pytest.mark.parametrize('successor_lease', [False, True])
async def test_real_completion_schedules_viewer_analysis(store, monkeypatch, tmp_path, successor_lease):
    async with store() as s:
        job = await s.get(Job, 'job')
        job.model_id = 'custom_file_workflow'
        job.provenance = {'remote_execution_receipt': {'expected_result_contract_sha256': hashlib.sha256(json.dumps(ex.resolve_job_result_contract(job), sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
        await s.commit()
    async def status(*_):
        return receipt().model_copy(update={'result_manifest_sha256': 'a'*64})
    async def collect(*_):
        return SimpleNamespace(artifacts=[]), tmp_path
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
    monkeypatch.setattr(ex, '_publish_result_generation', lambda *_: (tmp_path, None))
    monkeypatch.setattr(result_ingester, 'ingest_job_results', ingest)
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda job_id: calls.append(job_id))
    async with store() as s:
        changed = await ex.reconcile_remote_job(s, await s.get(Job, 'job'))
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

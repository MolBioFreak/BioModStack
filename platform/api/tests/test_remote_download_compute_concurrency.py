"""Real target/HTTP/resource owners with scratch SQLite and inert leaves.

No download, provider call, GPU inference or live database access.
"""
from copy import deepcopy
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from database import ExecutionTarget, Job, get_session
from services.remote_execution import targets, telemetry
from services.remote_execution.contracts import PreloadProgress
from test_multiworker_scheduling import workers

# Preserve the actual owner before the shared fixture replaces the transport leaf.
read_telemetry = targets.remote_target_telemetry
PHASES = ('checking', 'transferring', 'verifying', 'cancelling', 'recovery_blocked')


async def preparing(store, phase):
    now = datetime.utcnow()
    progress = PreloadProgress(operation_id='independent-download', source_revision='a' * 40,
        source_tree='b' * 40, request_sha256='c' * 64, phase=phase,
        message='Inert download fixture', started_at=now, updated_at=now,
        recovery_required=phase == 'recovery_blocked')
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:2')
        target.remote_root = '/worker'
        target.provider_metadata = {**target.provider_metadata,
            'preload': progress.model_dump(mode='json')}
        await session.commit()
    return progress.model_dump(mode='json')


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', PHASES)
@pytest.mark.parametrize('shortage', [None, 'cpu', 'ram', 'disk', 'vram'])
async def test_preload_keeps_telemetry_and_actual_resource_limits(workers, monkeypatch, phase, shortage):
    await preparing(workers, phase)
    sample: dict[str, Any] = dict(available=True, observed_at='inert',
        cpu={'allocated_cores': 8}, ram={'limit_bytes': 1000, 'used_bytes': 200},
        disk={'path': '/worker', 'free_bytes': 500},
        gpus=[{'index': 0, 'uuid': 'GPU-0', 'memory_total_mb': 24000, 'memory_used_mb': 8000}])
    if shortage == 'cpu':
        sample['cpu']['allocated_cores'] = 1
    elif shortage == 'ram':
        sample['ram']['used_bytes'] = 950
    elif shortage == 'disk':
        sample['disk']['free_bytes'] = 0
    elif shortage == 'vram':
        sample['gpus'][0]['memory_total_mb'] = None
    monkeypatch.setattr(telemetry.remote_telemetry, 'read', lambda *a, **kw: deepcopy(sample))
    monkeypatch.setattr(targets, 'remote_target_telemetry', read_telemetry)
    async with workers() as session:
        target = await targets.get_ready_target(session, 'vast:2')
        observed = await targets.remote_target_telemetry(target)
        assert observed['available'] is (shortage != 'vram')
        status = await targets.target_status(session, target.id)
        assert status.capabilities['scheduling']['new_work_ready']
        assert status.preload.phase == phase
        request = dict(required_cpus=4, required_memory_bytes=100,
            required_scratch_bytes=200, gpu_ids=[0])
        if shortage:
            with pytest.raises(targets.ExecutionTargetError):
                await targets.admit_target_resources(target, **request)
        else:
            result = await targets.admit_target_resources(target, **request)
            assert result['required'] == {'cpus': 4, 'memory_bytes': 100, 'scratch_bytes': 200}
            assert result['devices'] == [{'gpu_index': 0, 'gpu_uuid': 'GPU-0'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', PHASES)
async def test_http_launch_preview_remains_available_during_preload(workers, monkeypatch, phase):
    from routers import jobs
    original_preload = await preparing(workers, phase)
    calls = []

    def compile_preview(request, expansions):
        # Keep compilation inert: this case qualifies the real route/target gate,
        # not the scientific plan compiler or model readiness.
        calls.append(request.model_dump())
        assert expansions == []
        return dict(schema='bms.job.execution-preview.v1', approval_digest='d' * 64,
            admissible=False, request=request.model_dump(), plan={}, input_identities=[],
            generated_inputs=[], deferred_preparation=[], blockers=[{'code': 'inert_fixture'}])

    monkeypatch.setattr(jobs, '_execution_plan_preview', compile_preview)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')

    async def session():
        async with workers() as current:
            yield current

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[jobs.get_experiment_session] = session
    payload = dict(name='preview only', model_id='boltz2', mode='predict',
        params={'sequence': 'ACDEFG'}, execution_target_id='vast:2')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/jobs/execution-plan/preview', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['blockers'] == [{'code': 'inert_fixture'}]
    assert len(calls) == 1 and calls[0]['execution_target_id'] == 'vast:2'
    async with workers() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 2
        target = await session.get(ExecutionTarget, 'vast:2')
        assert target.provider_metadata['preload'] == original_preload
        assert target.leased_job_id is None

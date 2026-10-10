"""Offline concurrent provisioning ownership tests on real scratch SQLite."""
import asyncio

import pytest
from sqlalchemy import update

from database import Job, ExecutionTarget
from services.remote_execution import preloading as p, cache
from services.remote_execution.claims import job_has_claim
from test_preload_queued_workflow_pack import lane, start, settle, approved, TARGET, JOB
from test_remote_preloading import store
from services.remote_execution.contracts import PreloadRequest


async def own(lane, state, legacy):
    async with lane.factory() as s:
        job = await s.get(Job, JOB)
        job.status = 'completed' if state == 'terminal' else 'running'
        job.queue_status = 'completed' if state == 'terminal' else 'preparing' if state == 'staging' else 'running'
        job.remote_state = 'succeeded' if state == 'terminal' else state
        job.remote_attempt_id = 'retained-attempt'
        if legacy:
            (await s.get(ExecutionTarget, TARGET)).leased_job_id = JOB
        else:
            job.provenance = dict(job.provenance, remote_execution_assignment={
                'policy': 'vram_packing', 'execution_target_id': TARGET,
                'root_job_id': JOB, 'lease_id': 'retained-claim', 'released_at': None})
        await s.commit()
        return p.recipe_snapshot(job).__dict__


async def unchanged(lane, before, legacy):
    async with lane.factory() as s:
        target, job = await s.get(ExecutionTarget, TARGET), await s.get(Job, JOB)
        assert p.recipe_snapshot(job).__dict__ == before
        assert job_has_claim(target, job)
        assert target.leased_job_id == (JOB if legacy else None)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['running', 'staging', 'terminal'])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('finish', ['complete', 'cancel_retry', 'recovery'])
async def test_download_progress_finish_cancel_retry_preserve_job_claim(lane, monkeypatch, state, legacy, finish):
    before = await own(lane, state, legacy)
    request = await approved(lane)
    original = cache.provision_cache
    progressed = asyncio.Event()

    async def transfer(**kw):
        await kw['progress']({'phase': 'transferring', 'message': 'Downloading independent assets'})
        progressed.set()
        result = await original(**kw)
        await kw['progress']({'phase': 'verifying', 'message': 'Verifying independent assets'})
        return result

    monkeypatch.setattr(cache, 'provision_cache', transfer)
    lane.transfer_release.clear()
    response = await start(lane, request)
    operation = response.preload.operation_id
    await asyncio.wait_for(progressed.wait(), 5)
    await asyncio.wait_for(lane.transfer_entered.wait(), 5)
    await unchanged(lane, before, legacy)
    async with lane.factory() as s:
        assert (await s.get(ExecutionTarget, TARGET)).provider_metadata['preload']['phase'] == 'transferring'
        with pytest.raises(p.ExecutionTargetError):
            await lane.controller.start(s, TARGET, request)
        with pytest.raises(p.ExecutionTargetError, match='idle attached worker'):
            await lane.controller.refresh_inventory(s, TARGET)
    if finish != 'complete':
        lane.quiescent = finish == 'cancel_retry'
        async with lane.factory() as s:
            result = await lane.controller.cancel(s, TARGET, operation)
        assert result.preload.phase == ('cancelled' if lane.quiescent else 'recovery_blocked')
        await unchanged(lane, before, legacy)
        if finish == 'recovery':
            async with lane.factory() as s:
                with pytest.raises(p.ExecutionTargetError, match='confirmed remote quiescence'):
                    await lane.controller.start(s, TARGET, request, retry_operation_id=operation)
            lane.quiescent = True
            async with lane.factory() as s:
                assert (await lane.controller.cancel(s, TARGET, operation)).preload.phase == 'cancelled'
        lane.transfer_release.set()
        response = await start(lane, request, retry_operation_id=operation)
        assert response.preload.operation_id != operation
    lane.transfer_release.set()
    assert (await settle(lane))['phase'] == 'source_download_ready'
    await unchanged(lane, before, legacy)


@pytest.mark.asyncio
async def test_claim_arriving_at_completion_sql_does_not_veto_publication(lane, monkeypatch):
    original = lane.controller._publish
    before = None

    async def publish(session, target_id, progress, *args, **kwargs):
        nonlocal before
        if progress.phase == 'source_download_ready':
            before = await own(lane, 'running', False)
        return await original(session, target_id, progress, *args, **kwargs)

    monkeypatch.setattr(lane.controller, '_publish', publish)
    await start(lane)
    assert (await settle(lane))['phase'] == 'source_download_ready'
    await unchanged(lane, before, False)


@pytest.mark.asyncio
async def test_saved_recipe_preload_allows_active_claim_and_scalar_progress(store):
    async with store() as s:
        job = await s.get(Job, 'recipe')
        job.execution_target_id = 'vast:1'
        job.status = job.queue_status = job.remote_state = 'running'
        job.provenance = {'remote_execution_assignment': {'policy': 'vram_packing',
            'execution_target_id': 'vast:1', 'root_job_id': job.id, 'lease_id': 'saved-claim'}}
        await s.commit()
        before = p.recipe_snapshot(job).__dict__

    async def prewarm(**kw):
        async with store() as s:
            await s.execute(update(Job).where(Job.id == 'recipe').values(stage_progress='50/100'))
            await s.commit()
        await kw['progress']({'phase': 'transferring', 'message': 'Downloading'})
        await kw['check_fence']()
        return {'source_revision': 'a'*40, 'source_tree': 'b'*40, 'artifacts': []}

    controller = p.PreloadController(store, prewarm=prewarm)
    try:
        async with store() as s:
            await controller.start(s, 'vast:1', PreloadRequest(job_id='recipe'))
        await asyncio.gather(*list(controller.tasks.values()))
        async with store() as s:
            job, target = await s.get(Job, 'recipe'), await s.get(ExecutionTarget, 'vast:1')
            assert target.provider_metadata['preload']['phase'] == 'source_download_ready'
            assert job_has_claim(target, job)
            after = p.recipe_snapshot(job).__dict__
            assert after['stage_progress'] == '50/100'
            # SQLAlchemy's onupdate timestamp belongs to the simulated Job writer.
            for key in ('stage_progress', 'updated_at'):
                before.pop(key, None)
                after.pop(key, None)
            assert after == before
    finally:
        await controller.close()

"""Dismiss stale fleet cards without deleting historical workers or claims."""
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from database import Job
from services.remote_execution import targets
from test_remote_preloading import store
from test_remote_compact_status import app_for


async def absent(store, **overrides):
    async with store() as session:
        row = await targets.get_target(session, 'vast:1')
        row.active, row.state = False, 'inactive'
        row.provider_metadata = {**row.provider_metadata, 'inventory': {
            'status': 'complete', 'present': False, 'running': False,
            'checked_at': datetime.utcnow().isoformat(), **overrides}}
        # Reproduce the actual stale worker: an old, still-owned attempt.
        session.add(Job(id='retained-job', name='Retained run', model_id='protenix',
                        status='running', mode='predict', params={}, execution_target_id='vast:1',
                        remote_attempt_id='retained-attempt', remote_state='running',
                        provenance={'remote_execution_assignment': {
                            'policy': 'vram_packing', 'lease_id': 'retained-lease',
                            'execution_target_id': 'vast:1', 'root_job_id': 'retained-job'}}))
        await session.commit()


@pytest.mark.asyncio
async def test_dismiss_preserves_claims_history_and_reappears_if_provider_returns(store):
    await absent(store)
    async with AsyncClient(transport=ASGITransport(app=app_for(store)), base_url='http://test') as client:
        listing = (await client.get('/execution-targets')).json()
        assert listing[0]['can_remove_from_list'] is True
        assert listing[0]['active_job_ids'] == ['retained-job']
        for _ in range(2):
            reply = await client.post('/execution-targets/vast:1/dismiss')
            assert reply.status_code == 200, reply.text
            assert reply.json()['active_job_ids'] == ['retained-job']
            assert (await client.get('/execution-targets')).json() == []
        assert (await client.get('/execution-targets/vast:1')).status_code == 200
    async with store() as session:
        row = await targets.get_target(session, 'vast:1')
        assert row.provider_metadata['dismissed_from_list'] is True
        job = await session.get(Job, 'retained-job')
        assert (job.status, job.remote_attempt_id, job.execution_target_id) == ('running', 'retained-attempt', 'vast:1')
        row.provider_metadata = {**row.provider_metadata, 'inventory': {
            'status': 'complete', 'present': True, 'running': True,
            'checked_at': datetime.utcnow().isoformat()}}
        await session.commit()
    async with store() as session:
        listing = await targets.list_targets(session)
        assert [x.id for x in listing] == ['vast:1']
        assert listing[0].can_remove_from_list is False


@pytest.mark.asyncio
@pytest.mark.parametrize('inventory', [
    {'present': True}, {'status': 'unknown'},
    {'checked_at': (datetime.utcnow() - timedelta(hours=1)).isoformat()},
    {'checked_at': (datetime.utcnow() + timedelta(hours=1)).isoformat()},
])
async def test_dismiss_rechecks_current_inventory_at_write(store, inventory):
    await absent(store, **inventory)
    async with AsyncClient(transport=ASGITransport(app=app_for(store)), base_url='http://test') as client:
        response = await client.post('/execution-targets/vast:1/dismiss')
        assert response.status_code == 409
    async with store() as session:
        assert 'dismissed_from_list' not in (await targets.get_target(session, 'vast:1')).provider_metadata


@pytest.mark.asyncio
async def test_active_worker_cannot_be_dismissed(store):
    await absent(store)
    async with store() as session:
        row = await targets.get_target(session, 'vast:1')
        row.active, row.state = True, 'probing'
        await session.commit()
    async with store() as session:
        with pytest.raises(targets.ExecutionTargetError):
            await targets.dismiss_target(session, 'vast:1')

"""Cross-lane cancellation uses the existing controller owner, even before results.

Real guard + independent file-backed database Sessions + mounted DELETE. Only
worker SSH is doubled; no provider, science or remote instance is contacted.
"""
import asyncio
from types import SimpleNamespace

import pytest

from database import ExecutionTarget, Job
from services.remote_execution import executor as ex, transport
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import success
from test_remote_rectify_return import mounted


@pytest.mark.asyncio
async def test_nonterminal_controller_owner_is_not_quiescent(store):
    async with store() as session:
        job = await session.get(Job, 'job')
        assert not (job.provenance or {}).get('remote_execution_receipt', {}).get('result_manifest_sha256')
        with ex._controller_attempt_guard('job') as owned:
            assert owned
            assert not await ex.cancel_local_result_transfer(job, timeout=0)
            assert await ex.cancel_local_result_transfer(job, timeout=0, guard_owned=True)
        assert await ex.cancel_local_result_transfer(job, timeout=0)


@pytest.mark.asyncio
async def test_delete_retains_lease_until_nonterminal_controller_work_joins(store, monkeypatch):
    status = success().model_copy(update={'state': 'cancelled', 'exit_code': 1})
    async def remote(*_, **__):
        return SimpleNamespace(stdout=status.model_dump_json())
    monkeypatch.setattr(ex, 'run_remote', remote)
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (
        transport.RemoteConnection('target', 'localhost', 22, 'user', '/fixture'), '/fixture/attempt'))
    monkeypatch.setattr(ex, '_worker_argv', lambda _, op, *args: [op])
    cancel = None
    async with mounted(store) as client:
        try:
            with ex._controller_attempt_guard('job') as owned:
                assert owned
                cancel = asyncio.create_task(client.delete('/api/jobs/job'))
                for _ in range(200):
                    async with store() as independent:
                        job = await independent.get(Job, 'job')
                        observed = job.queue_status
                    if observed in {'cancelling', 'cancelled'}:
                        break
                    await asyncio.sleep(.01)
                # The persisted cancellation intent interrupts preparation/upload;
                # worker stop alone must not release this still-owned controller.
                await asyncio.sleep(.1)
                async with store() as independent:
                    job = await independent.get(Job, 'job')
                    assert job.queue_status == 'cancelling'
                    assert (await independent.get(ExecutionTarget, 'target')).leased_job_id == 'job'
                assert not cancel.done()
            response = await asyncio.wait_for(cancel, 3)
            assert response.status_code == 200, response.text
            async with store() as independent:
                assert (await independent.get(Job, 'job')).status == 'cancelled'
                assert (await independent.get(ExecutionTarget, 'target')).leased_job_id is None
        finally:
            if cancel is not None:
                if not cancel.done():
                    cancel.cancel()
                await asyncio.gather(cancel, return_exceptions=True)

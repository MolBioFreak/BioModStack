"""Continue Workflow must not be refused because the controller's own status check holds the job lock for a moment.

The orchestrator's periodic reconcile takes the per-job controller lock while it reads the worker's status. A resume
that landed in that window was refused at once with "Checkpoint controller operation is already active" and the gate
stayed open until the user clicked again. The resume waits a bounded time for the lock instead; a lock that stays held
still ends in the same refusal.
"""
import asyncio

import pytest

from database import Job
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)


@pytest.fixture
def owned_resume(monkeypatch):
    calls = []

    async def fake(session, job, checkpoint, decision):
        calls.append(str(job.id))
        return {"job_id": str(job.id), "state": "pending"}

    monkeypatch.setattr(ex, "_request_remote_checkpoint_resume_owned", fake)
    return calls


@pytest.mark.asyncio
async def test_resume_proceeds_once_a_short_lived_lock_is_released(store, owned_resume):
    async with store() as session:
        job = await session.get(Job, "job")
        with ex._controller_attempt_guard("job") as owned:
            assert owned
            task = asyncio.create_task(ex.request_remote_checkpoint_resume(session, job, {}, {}))
            await asyncio.sleep(0.4)
            assert not task.done(), "the resume must be waiting for the lock, not refused"
        result = await asyncio.wait_for(task, timeout=5)
    assert result["state"] == "pending" and owned_resume == ["job"]


@pytest.mark.asyncio
async def test_resume_still_refuses_when_the_lock_stays_held(store, owned_resume, monkeypatch):
    monkeypatch.setattr(ex, "CHECKPOINT_RESUME_LOCK_WAIT_SECONDS", 0.6)
    async with store() as session:
        job = await session.get(Job, "job")
        with ex._controller_attempt_guard("job") as owned:
            assert owned
            with pytest.raises(ex.RemoteExecutionError, match="already active"):
                await ex.request_remote_checkpoint_resume(session, job, {}, {})
    assert owned_resume == []


@pytest.mark.asyncio
async def test_an_uncontended_resume_does_not_wait(store, owned_resume):
    async with store() as session:
        job = await session.get(Job, "job")
        started = asyncio.get_running_loop().time()
        await ex.request_remote_checkpoint_resume(session, job, {}, {})
        assert asyncio.get_running_loop().time() - started < 0.2
    assert owned_resume == ["job"]

"""A Continue that waited for the controller lock must act on the job as it is now, not as the request loaded it.

The orchestrator's reconcile pass holds the per-job controller lock while it publishes the gate's review rows, and that
publication rewrites awaiting_payload. The resume waits for the lock (CHECKPOINT_RESUME_LOCK_WAIT_SECONDS); it must then
reload the job, otherwise its compare-and-swap runs against the snapshot from before the wait and fails with
"Checkpoint owner changed during resource reacquisition".
"""
import asyncio

import pytest

from database import Job
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)


@pytest.mark.asyncio
async def test_resume_acts_on_the_job_as_it_is_after_the_wait(store, monkeypatch):
    seen = {}

    async def fake(session, job, checkpoint, decision):
        seen["payload"] = dict(job.awaiting_payload or {})
        return {"job_id": str(job.id), "state": "pending"}

    monkeypatch.setattr(ex, "_request_remote_checkpoint_resume_owned", fake)
    async with store() as request_session:
        stale = await request_session.get(Job, "job")          # what the HTTP request loaded before waiting
        assert "candidate_dir" not in (stale.awaiting_payload or {})
        with ex._controller_attempt_guard("job") as owned:      # the reconcile pass holds the lock ...
            assert owned
            task = asyncio.create_task(ex.request_remote_checkpoint_resume(request_session, stale, {}, {}))
            await asyncio.sleep(0.4)
            async with store() as reconciler_session:           # ... and publishes the review rows meanwhile
                current = await reconciler_session.get(Job, "job")
                current.awaiting_payload = {"candidate_dir": "/published/review"}
                await reconciler_session.commit()
        await asyncio.wait_for(task, timeout=5)
    assert seen["payload"] == {"candidate_dir": "/published/review"}

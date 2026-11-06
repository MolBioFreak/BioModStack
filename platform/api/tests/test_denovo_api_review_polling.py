"""Complete frozen review rows poll without repeated byte/worker acquisition."""
import pytest
from sqlalchemy import select

from database import Design, Job
from services.remote_execution import executor
from test_local_gate_review_rows import store
from test_denovo_portable_checkpoint import retained_job, no_transport


@pytest.mark.asyncio
async def test_complete_review_poll_skips_pickup_but_explicit_source_still_verifies(store, tmp_path, monkeypatch):
    job, retained = retained_job(tmp_path)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        session.add(job)
        await session.commit()
        assert await executor.publish_remote_review_rows(session, job) == 1
    original = executor.retrieve_remote_checkpoint_review
    async def unexpected(*args, **kwargs):
        pytest.fail('complete-row polling repeated artifact acquisition')
    monkeypatch.setattr(executor, 'retrieve_remote_checkpoint_review', unexpected)
    async with store() as session:
        source = await session.get(Job, job.id)
        assert await executor.publish_remote_review_rows(session, source) == 1
        checkpoint = source.awaiting_payload['component_checkpoint']
        candidate = next(item for item in checkpoint['artifacts'] if item['schema'] == 'bms.stage-review.candidate.v1')
        (retained / candidate['relative_path']).write_bytes(b'changed')
        assert await executor.publish_remote_review_rows(session, source) == 1
        with pytest.raises((ValueError, RuntimeError)):
            await original(session, source, checkpoint)


@pytest.mark.asyncio
async def test_missing_projection_is_repaired_and_stale_cancelled_owner_never_pickups(store, tmp_path, monkeypatch):
    job, _ = retained_job(tmp_path)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        session.add(job)
        await session.commit()
        assert await executor.publish_remote_review_rows(session, job) == 1
        row = (await session.scalars(select(Design))).one()
        await session.delete(row)
        await session.commit()
    original = executor.retrieve_remote_checkpoint_review
    calls = []
    async def pickup(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(executor, 'retrieve_remote_checkpoint_review', pickup)
    async with store() as stale:
        source = await stale.get(Job, job.id)
        assert await executor.publish_remote_review_rows(stale, source) == 1
        assert calls == [True]
        async with store() as writer:
            cancelled = await writer.get(Job, job.id)
            cancelled.status = 'cancelled'
            cancelled.awaiting_input = False
            cancelled.queue_status = 'cancelling'
            await writer.commit()
        assert await executor.publish_remote_review_rows(stale, source) == 0
        assert calls == [True]

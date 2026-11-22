"""Attempt and child authority at the MD terminal writer (file SQLite)."""
import pytest
from sqlalchemy import select

from database import Job, JobArtifact, MdAttemptSegment, MdReplicaRun, MdRun
from services.md import completion, lifecycle
from services.md.results import MDResultError
from services.nextflow import reconcile_md_analysis_parent_if_current
from services.remote_execution import executor
from test_md_results_trim import _seed, _tree, store


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', ['nextflow_run_id', 'remote_attempt_id'])
@pytest.mark.parametrize('boundary', ['_publish_barrier', '_prepare_completion'])
async def test_replaced_attempt_cannot_publish(store, tmp_path, monkeypatch, identity, boundary):
    _, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    rid, sid = await _seed(maker, root, spec)
    (root / 'analysis/manifest.json').unlink()
    (root / 'md_completion_barrier.json').unlink()
    async with maker() as writer:
        setattr(await writer.get(Job, 'md-job-1'), identity, 'original')
        await writer.commit()

    joined = executor._joined_thread

    async def replace_after_read(fn, *args, **kwargs):
        result = await joined(fn, *args, **kwargs)
        if fn.__name__ == boundary:
            async with maker() as peer:
                setattr(await peer.get(Job, 'md-job-1'), identity, 'replacement')
                await peer.commit()
        return result

    monkeypatch.setattr(lifecycle if boundary == '_publish_barrier' else executor,
                        '_joined_thread', replace_after_read)
    async with maker() as session:
        with pytest.raises(MDResultError) as exc:
            await reconcile_md_analysis_parent_if_current('md-job-1', session)
        assert exc.value.code == 'MD_COMPLETION_CONFLICT'
        await session.rollback()
    async with maker() as observer:
        parent = await observer.get(Job, 'md-job-1')
        assert getattr(parent, identity) == 'replacement'
        assert parent.status == parent.queue_status == 'running'
        assert (await observer.get(MdRun, parent.id)).phase == 'finalizing'
        assert (await observer.get(MdReplicaRun, rid)).state == 'running'
        assert (await observer.get(MdAttemptSegment, sid)).state == 'running'
        assert await observer.scalar(select(JobArtifact.id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('owner,state', [('analysis', 'cancelled'), ('analysis', 'running'),
    ('analysis', 'failed'), ('segment', 'failed'), ('segment', 'cancelled'), ('segment', 'orphaned')])
async def test_unsuccessful_owner_blocks_completion(store, tmp_path, monkeypatch, owner, state):
    _, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    rid, sid = await _seed(maker, root, spec)
    async with maker() as session:
        if owner == 'analysis':
            child = await session.get(Job, 'analysis-child-0')
            child.status = child.queue_status = state
        else:
            (await session.get(MdAttemptSegment, sid)).state = state
        await session.commit()
        with pytest.raises(MDResultError) as exc:
            await completion.validate_and_finalize_md_job(await session.get(Job, 'md-job-1'), session)
        assert exc.value.code == 'MD_COMPLETION_CONFLICT'
        await session.rollback()
    async with maker() as observer:
        assert (await observer.get(Job, 'md-job-1')).status == 'running'
        assert (await observer.get(MdReplicaRun, rid)).state == 'running'
        assert (await observer.get(MdAttemptSegment, sid)).state == (state if owner == 'segment' else 'running')
        assert (await observer.get(Job, 'analysis-child-0')).status == (state if owner == 'analysis' else 'completed')
        assert await observer.scalar(select(JobArtifact.id)) is None

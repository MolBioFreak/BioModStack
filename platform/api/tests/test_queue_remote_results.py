"""Real SQLite queue projection; no runtime, provider, or network access."""
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job
from routers import queue


@pytest.mark.asyncio
async def test_remote_results_projection_preserves_capacity_and_filters(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(queue, '_get_queue_enrichment', lambda jobs: {})
    try:
        async with factory() as session:
            base = dict(name='remote', model_id='protenix', mode='structure_prediction',
                        status='awaiting_input', queue_status='completed', execution_target_id='vast:1',
                        awaiting_input=True, awaiting_stage='remote_results', remote_state='results_available',
                        params={}, created_at=datetime(2026, 9, 1))
            cases = {
                'ready': {},
                'retry': dict(remote_state='result_pull_failed', error_message='Checksum mismatch'),
                'returning': dict(status='running', queue_status='running', remote_state='returning'),
                'local-gate': dict(execution_target_id=None),
                'other-gate': dict(awaiting_stage='post_rfantibody'),
                'terminal': dict(status='completed'),
                'unknown': dict(remote_state='unknown'),
                'empty-target': dict(execution_target_id=''),
                'queued': dict(status='pending', queue_status='queued', execution_target_id=None,
                               awaiting_input=False, awaiting_stage=None, remote_state=None, vram_estimate_mb=1000),
            }
            for job_id, changes in cases.items():
                session.add(Job(id=job_id, **(base | changes)))
            await session.commit()
            rows = {row.id: row.model_dump(mode='json') for row in await queue.list_queue(session=session)}
            assert set(rows) == {'ready', 'retry', 'returning', 'queued'}
            assert rows['ready']['status'] == 'awaiting_input'
            assert rows['ready']['queue_status'] == 'completed'
            assert rows['ready']['awaiting_input'] is True
            assert rows['ready']['awaiting_stage'] == 'remote_results'
            assert rows['ready']['assigned_gpu'] is None
            assert rows['ready']['live_vram_mb'] is None
            assert rows['retry']['error_message'] == 'Checksum mismatch'
            assert rows['returning']['status'] == rows['returning']['queue_status'] == 'running'
            assert {r.id for r in await queue.list_queue(status='completed', session=session)} == {'ready', 'retry'}
            assert {r.id for r in await queue.list_queue(status='running', session=session)} == {'returning'}
            stats = await queue.get_queue_stats(session=session)
            assert stats.model_dump() == dict(queued=1, running=0, paused=0, preparing=0, cancelling=0, total=1)
            # Reloading the session retains durable state; projection never rewrites result waits into capacity.
        async with factory() as session:
            assert {r.id for r in await queue.list_queue(session=session)} == {'ready', 'retry', 'returning', 'queued'}
    finally:
        await engine.dispose()

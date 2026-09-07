"""Attempt-wide task completeness must precede first ingestion/mutation."""
import json
import pytest
from sqlalchemy import event, select
from database import Design
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication
from test_core_protein_candidates import setup, job


@pytest.mark.asyncio
async def test_missing_task_attempt_binding_rejects_valid_native_output(tmp_path):
    from test_boltz_scientific_persistence import job as fixture_job
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = fixture_job(tmp_path)
            session.add(current)
            await session.commit()
            binding = tmp_path / 'scientific/boltz/sample/boltz_task_binding.json'
            binding.unlink(missing_ok=True)
            with pytest.raises(RuntimeError, match='boltz_task_binding|task.*binding'):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_absent_launch_inventory_cannot_publish_surviving_task(tmp_path):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            session.add_all([current, Design(id='review', job_id='job', name='keep', source_stage='review', pdb_path='review.pdb')])
            await session.commit()
            current.name = 'pending'
            flushed = []
            event.listen(session.sync_session, 'before_flush', lambda *args: flushed.append(True))
            with pytest.raises(RuntimeError, match='launch.*authority'):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert not flushed
            await session.commit()
            assert [(r.id, r.name) for r in (await session.execute(select(Design))).scalars()] == [('review', 'keep')]
    finally:
        await engine.dispose()

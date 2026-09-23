"""BC2 terminal binding to the existing Job finalizer; synthetic tables, not GPU acceptance."""
import csv

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services.bindcraft2_publication import read_published_native_results
from services.result_state_integrity import finalize_successful_job


def _campaign(root):
    root.mkdir()
    (root / '.campaign_state.json').write_text('{"trajectories":1}')
    path = root / '1_Trajectories/!_Trajectories.csv'
    path.parent.mkdir(parents=True)
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['design', 'trajectory'])
        writer.writeheader()
        writer.writerow({'design': 'native_attempt', 'trajectory': '1'})
    return path


@pytest.mark.asyncio
async def test_native_zero_yield_finalizes_and_reopens_without_designs(tmp_path):
    root = tmp_path / 'native'
    _campaign(root)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc2', name='BC2 native', model_id='bindcraft2', mode='campaign',
                      status='running', queue_status='running', awaiting_input=False,
                      params={}, output_dir=str(root))
            session.add(job)
            await session.commit()
            first = await finalize_successful_job(job, str(root), session)
            assert first.completed is True
            assert first.design_count == 0
            assert job.provenance['result_integrity']['result_kind'] == 'bindcraft2_native_publication'
            assert job.provenance['result_integrity']['idempotent_prior_results'] is False
            assert not (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
        async with factory() as session:
            job = await session.get(Job, 'bc2')
            publication, receipt = await read_published_native_results(job, session)
            assert publication.arms[0].accounting['retained_sequences'] == 0
            assert receipt['selection']['eligible'] is False
            job.status = job.queue_status = 'running'
            job.completed_at = None
            await session.commit()
            replay = await finalize_successful_job(job, str(root), session)
            assert replay.completed is True
            assert job.provenance['result_integrity']['idempotent_prior_results'] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tampered_native_bytes_fail_finalization_without_synthetic_design(tmp_path):
    root = tmp_path / 'native'
    table = _campaign(root)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc2', name='BC2 native', model_id='bindcraft2', mode='campaign',
                      status='running', queue_status='running', awaiting_input=False,
                      params={}, output_dir=str(root))
            session.add(job)
            await session.commit()
            assert (await finalize_successful_job(job, str(root), session)).completed
        table.write_text(table.read_text().replace('native_attempt', 'altered_attempt'))
        async with factory() as session:
            job = await session.get(Job, 'bc2')
            job.status = job.queue_status = 'running'
            job.completed_at = None
            await session.commit()
            failed = await finalize_successful_job(job, str(root), session)
            assert failed.completed is False
            assert failed.integrity_state == 'ingestion_failed'
            assert 'replay changed' in job.error_message
            assert not (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
    finally:
        await engine.dispose()

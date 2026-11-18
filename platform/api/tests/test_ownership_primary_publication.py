"""The public ingester and the finalizer publish the same primary rows."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job
from services import result_ingester
from services.result_state_integrity import finalize_successful_job

SKIP = {'id', 'created_at'}


async def _primary_rows(tmp_path: Path, name: str, root: Path, publish):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='primary-owner', name='primary-owner', model_id='rfantibody', mode='design',
                      params={}, status='running', queue_status='running', awaiting_input=False,
                      output_dir=str(root), stage_outputs={'frustrampnn': ['manifest']})
            session.add(job)
            await session.commit()
            await publish(job, session)
        async with factory() as session:
            rows = (await session.scalars(select(Design).where(
                Design.job_id == 'primary-owner', Design.source_stage.is_(None),
            ).order_by(Design.name))).all()
            job = await session.get(Job, 'primary-owner')
            return ([{c.name: getattr(row, c.name) for c in Design.__table__.columns if c.name not in SKIP}
                     for row in rows], (job.provenance or {}).get('antibody_pipeline_result'))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingester_and_finalizer_publish_same_primary_rows(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    (root / 'results' / 'best_designs').mkdir(parents=True)
    (root / 'results' / 'all_designs.csv').write_text(
        'description,pr_plddt\ncandidate_a,87\ncandidate_b,71\n')
    for name in ('candidate_a', 'candidate_b'):
        (root / 'results' / 'best_designs' / f'{name}.pdb').write_text(
            'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C\nEND\n')

    async def no_attachment(*_args, **_kwargs):
        return 0
    # The selected optional stage sends the finalizer down its own primary path.
    monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', no_attachment)

    async def ingest(job, session):
        assert await result_ingester.ingest_job_results(job.id, str(root), session) == 2

    async def finalize(job, session):
        result = await finalize_successful_job(job, str(root), session)
        assert result.completed and result.design_count == 2

    ingested = await _primary_rows(tmp_path, 'ingester.db', root, ingest)
    finalized = await _primary_rows(tmp_path, 'finalizer.db', root, finalize)
    assert len(ingested[0]) == 2
    assert ingested == finalized

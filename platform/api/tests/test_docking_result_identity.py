"""Native docking readback keeps engine/complex identity and real result counts."""
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job
from routers import jobs as jobs_router




@pytest.mark.asyncio
async def test_job_detail_does_not_count_inputs_or_ancillary_structure_files(tmp_path, monkeypatch):
    output = tmp_path / 'published'
    output.mkdir()
    (output / 'input.pdb').write_text('input, not a Design')
    (output / 'intermediate.cif').write_text('intermediate, not a Design')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "counts.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        session.add(Job(id='parent', name='parent', model_id='test', mode='structure_prediction', status='completed', params={}, output_dir=str(output), created_at=datetime(2026, 9, 1)))
        await session.commit()
        assert (await jobs_router.get_job('parent', session=session)).design_count == 0
        session.add(Job(id='child', name='child', parent_job_id='parent', model_id='test', mode='structure_prediction', status='completed', params={}, created_at=datetime(2026, 9, 1)))
        session.add(Design(id='real-candidate', job_id='child', name='retained candidate', pdb_path=str(output / 'input.pdb')))
        await session.commit()
        assert (await jobs_router.get_job('parent', session=session)).design_count == 1
    await engine.dispose()

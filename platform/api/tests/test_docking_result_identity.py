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
async def test_docking_listing_exact_paths_formats_and_ambiguous_legacy_name(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'dock'
    name = 'rank1_confidence-1.92.sdf'
    expected = {}
    for complex_name in ('complex-a', 'complex b'):
        path = output / 'run/diffdock/results' / complex_name / name
        path.parent.mkdir(parents=True)
        payload = f'{complex_name}\nBMS test fixture\n\n  1  0  0  0  0  0            999 V2000\n    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n'
        path.write_text(payload)
        expected[path.relative_to(output).as_posix()] = payload.encode()
    pdb = output / 'run/unidock/filtered/pose.pdb'
    pdb.parent.mkdir(parents=True)
    pdb.write_bytes(b'HETATM    1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00           C\nEND\n')
    expected[pdb.relative_to(output).as_posix()] = pdb.read_bytes()
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "docking.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        session.add(Job(id='dock', name='docking identity fixture', model_id='docking', mode='dual_docking', status='completed', params={}, output_dir=str(output), created_at=datetime(2026, 9, 1)))
        await session.commit()
    async def get_session():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = get_session
    app.include_router(jobs_router.router, prefix='/api/jobs')
    try:
        with TestClient(app) as client:
            listing = client.get('/api/jobs/dock/docking-results')
            assert listing.status_code == 200, listing.text
            rows = listing.json()['sdfs']
            assert len(rows) == 3
            assert {row['artifact_path'] for row in rows} == set(expected)
            for row in rows:
                response = client.get('/api/jobs/dock/docking-results/' + row['artifact_path'])
                assert response.status_code == 200, response.text
                assert response.content == expected[row['artifact_path']]
                assert row['format'] == ('sdf' if row['engine'] == 'diffdock' else 'pdb')
                assert response.headers['content-type'].startswith('chemical/x-mdl-sdfile' if row['format'] == 'sdf' else 'chemical/x-pdb')
            assert client.get('/api/jobs/dock/docking-results/' + name).status_code == 409
            assert client.get('/api/jobs/dock/docking-results/pose.pdb').content == pdb.read_bytes()
            assert client.get('/api/jobs/dock/docking-results/rank*.sdf').status_code == 404
            assert client.get('/api/jobs/dock/docking-results/inputs/private.pdb').status_code == 400
            assert client.get('/api/jobs/missing/docking-results/' + name).status_code == 404
            # Intermediate and leaf symlinks cannot redirect the addressed pose.
            foreign = tmp_path / 'foreign'
            foreign.mkdir()
            (foreign / name).write_bytes(b'other job')
            linked = output / 'run/diffdock/results/link'
            linked.symlink_to(foreign, target_is_directory=True)
            assert client.get('/api/jobs/dock/docking-results/run/diffdock/results/link/' + name).status_code == 404
            leaf = output / 'run/diffdock/results/complex-a/linked.sdf'
            leaf.symlink_to(foreign / name)
            assert client.get('/api/jobs/dock/docking-results/' + leaf.relative_to(output).as_posix()).status_code == 404
    finally:
        await engine.dispose()


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

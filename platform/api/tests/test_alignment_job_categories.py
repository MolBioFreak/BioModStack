"""Family alignment category filtering is a list-only projection."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job
from routers import jobs


@pytest.mark.asyncio
async def test_alignment_and_ngs_filters_apply_independently_before_paging(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'categories.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Place the two family jobs behind the first page to prove server-side
        # filtering/counting does not depend on the browser's recent-100 set.
        rows = [Job(id=f'ordinary-{i:03}', name='ordinary', status='completed',
                    model_id='boltz2', mode='prediction', params={},
                    created_at=datetime(2026, 1, 2) + timedelta(minutes=i))
                for i in range(105)]
        rows.extend([
            Job(id='family-p', name='family protein', status='completed',
                model_id=' FAMILY_MSA_PROTEIN ', mode='family_msa', params={},
                created_at=datetime(2026, 1, 1)),
            Job(id='family-n', name='family nucleotide', status='completed',
                model_id='family_msa_nucleic', mode='family_msa', params={},
                created_at=datetime(2026, 1, 1, 0, 1)),
            Job(id='ngs', name='ngs', status='completed', model_id='ont_fastq_qc',
                mode='read', params={}, created_at=datetime(2026, 1, 1, 0, 2)),
            Job(id='folding-msa', name='folding msa', status='completed', model_id='msa_batch',
                mode='msa_generation', params={}, created_at=datetime(2026, 1, 1, 0, 3)),
        ])
        session.add_all(rows)
        await session.commit()

    async def session_override():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs.get_session] = session_override
    app.include_router(jobs.router, prefix='/api/jobs')
    client = TestClient(app)
    for exclude_ngs in (False, True):
        for exclude_alignments in (False, True):
            params = {'summary': True, 'limit': 500, 'exclude_ngs': exclude_ngs,
                      'exclude_alignments': exclude_alignments}
            response = client.get('/api/jobs', params=params)
            assert response.status_code == 200, response.text
            body = response.json()
            ids = {row['id'] for row in body['jobs']}
            assert body['total'] == len(ids) == 109 - int(exclude_ngs) - 2 * int(exclude_alignments)
            assert ('ngs' in ids) is not exclude_ngs
            assert ('family-p' in ids) is not exclude_alignments
            assert ('family-n' in ids) is not exclude_alignments
            assert 'folding-msa' in ids
            if not exclude_alignments:
                # Native search can retrieve older family jobs, and count remains
                # consistent with the full filtered set before limit/offset.
                page = client.get('/api/jobs', params={**params, 'limit': 1, 'offset': 107})
                assert page.status_code == 200, page.text
                assert page.json()['total'] == body['total']

    await engine.dispose()

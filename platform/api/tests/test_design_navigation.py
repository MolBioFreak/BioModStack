"""Generated TEST records exercise real bounded Design HTTP consumers."""
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, get_session
from routers import designs
from services.global_experiments.result_surfaces import _reopen_route
from experiment_services import ValidationFailure


@pytest.mark.asyncio
async def test_model_scope_summary_pagination_and_exact_lineage(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'navigation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Job(id='parent', name='TEST parent', status='completed', mode='structure_prediction', model_id='boltz2', params={}),
            Job(id='child', name='TEST child', status='completed', mode='structure_prediction', model_id='protenix', parent_job_id='parent', params={}),
            Job(id='foreign', name='TEST foreign', status='completed', mode='structure_prediction', model_id='protenix', params={}),
        ])
        session.add_all([Design(id=f'a-{i:03}', job_id='parent', name=f'a-{i:03}', pdb_path=f'{i}.pdb', provenance={'model_id': 'boltz2'}) for i in range(501)])
        session.add_all([Design(id=f'z-{i}', job_id='child', name=f'z-{i}', pdb_path=f'z-{i}.pdb', provenance={'model_id': 'protenix'}, plddt_overall=70 + i) for i in range(3)])
        session.add(Design(id='foreign-design', job_id='foreign', name='foreign', pdb_path='foreign.pdb', provenance={'model_id': 'protenix'}))
        await session.commit()
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        base = {'job_id': 'parent', 'sort_by': 'name', 'sort_desc': False, 'limit': 2, 'include_summary': True}
        first = await client.get('/api/designs', params=base)
        assert first.status_code == 200, first.text
        assert first.json()['total'] == 504
        assert first.json()['model_counts'] == {'boltz2': 501, 'protenix': 3}
        assert [d['id'] for d in first.json()['designs']] == ['a-000', 'a-001']
        scoped = await client.get('/api/designs', params={**base, 'model_id': 'protenix', 'offset': 2})
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()['total'] == 3
        assert scoped.json()['summary']['avg_plddt'] == 71
        assert [d['id'] for d in scoped.json()['designs']] == ['z-2']
        filtered = await client.post('/api/designs/query', json={**base, 'model_id': 'protenix', 'q': 'z-1'})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()['total'] == 1
        assert filtered.json()['model_counts'] == first.json()['model_counts']
        assert filtered.json()['designs'][0]['id'] == 'z-1'
        for design_id, status in [('z-2', 200), ('foreign-design', 404), ('missing', 404)]:
            response = await client.get(f'/api/designs/{design_id}', params={'job_id': 'parent'})
            assert response.status_code == status, response.text
            if status == 200:
                assert response.json()['id'] == design_id
        unavailable = await client.get('/api/designs', params={**base, 'model_id': 'missing'})
        assert unavailable.json()['total'] == 0
        assert unavailable.json()['model_counts'] == first.json()['model_counts']
        assert (await client.get('/api/designs', params={**base, 'limit': 501})).status_code == 422
    await engine.dispose()


def test_historical_design_reopen_uses_receipt_identity_without_mutating_acknowledgement():
    payload = {"entity_kind": "design", "entity_id": "second", "reopen_uri": "/designs/job", "metadata": {"design_id": "second"}}
    assert _reopen_route(payload)["query"] == {"design_id": "second"}
    assert payload["reopen_uri"] == "/designs/job"
    with pytest.raises(ValidationFailure, match="different candidate"):
        _reopen_route({**payload, "reopen_uri": "/designs/job?design_id=first"})
    with pytest.raises(ValidationFailure, match="identity"):
        _reopen_route({**payload, "metadata": {"design_id": "foreign"}})

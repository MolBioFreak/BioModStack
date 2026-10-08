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


@pytest.mark.asyncio
async def test_persisted_producer_identity_drives_every_design_read(tmp_path, monkeypatch):
    from collections import Counter
    from sqlalchemy import select
    from services import plr_workflow_results

    def no_artifact_surface(*args, **kwargs):
        raise AssertionError('model navigation must remain metadata-only')
    monkeypatch.setattr(plr_workflow_results, 'build_protein_local_redesign_result_surface', no_artifact_surface)
    monkeypatch.setattr(plr_workflow_results, '_sha256', no_artifact_surface)
    # Explicit producer metadata overrides upstream/workflow identity. Without it,
    # disagreements are unknown; stage tags and filenames alone are not producers.
    cases = [
        ('sequence', {'model_call_family': 'fampnn', 'sequence_design_model': 'fampnn'}, 'fampnn'),
        ('family', {'model_call_family': ' FAMPNN '}, 'fampnn'),
        ('sequence-only', {'sequence_design_model': 'fampnn'}, 'fampnn'),
        ('generic', {'model_id': 'boltz2'}, 'boltz2'),
        ('explicit', {'producer_model_id': 'protenix', 'model_id': 'boltz2'}, 'protenix'),
        ('unknown', {'producer_model_id': 'future_model'}, 'future_model'),
        ('missing', None, None),
        ('blank', {'model_id': ' ', 'model_call_family': None}, None),
        ('conflict', {'model_call_family': 'boltz2', 'sequence_design_model': 'fampnn'}, None),
        ('stored-conflict', {'model_id': 'boltz2', 'model_call_family': 'fampnn'}, None),
        ('agree', {'model_id': 'fampnn', 'model_call_family': ' FAMPNN ', 'sequence_design_model': 'fampnn'}, 'fampnn'),
    ]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'producers.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Job(id='parent', name='TEST nanobody', model_id='nanobody', mode='refinement', params={}),
            Job(id='child', name='TEST child', model_id='nanobody', mode='refinement', parent_job_id='parent', params={}),
            Job(id='foreign', name='TEST foreign', model_id='nanobody', mode='refinement', params={}),
        ])
        for identity, provenance, _ in cases:
            session.add(Design(id=identity, name=identity, job_id='parent', provenance=provenance,
                               stage_family='fampnn', stage_mode='sequence_design',
                               pdb_path='native-sequence.pdb', fampnn_psce=0.02))
        for owner in ('child', 'foreign'):
            session.add(Design(id=owner, name=owner, job_id=owner, pdb_path='native-sequence.pdb',
                               provenance={'producer_model_id': 'fampnn'}, stage_family='fampnn',
                               stage_mode='sequence_design', fampnn_psce=0.02))
        await session.commit()
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    expected = {identity: producer for identity, _, producer in cases}
    expected['child'] = 'fampnn'
    counts = dict(Counter(producer for producer in expected.values() if producer))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        base = {'job_id': 'parent', 'sort_by': 'name', 'sort_desc': False, 'include_summary': True}
        unfiltered = await client.get('/api/designs', params={**base, 'limit': 2})
        assert unfiltered.status_code == 200, unfiltered.text
        assert unfiltered.json()['total'] == len(expected)
        assert unfiltered.json()['model_counts'] == counts
        for producer, count in counts.items():
            observed = []
            for offset in range(0, count + 1, 2):
                page = await client.get('/api/designs', params={**base, 'model_id': producer.upper(), 'limit': 2, 'offset': offset})
                assert page.status_code == 200, page.text
                body = page.json()
                assert body['total'] == body['summary']['total'] == count
                assert body['model_counts'] == counts
                for row in body['designs']:
                    observed.append(row['id'])
                    assert row['provenance']['producer_model_id'] == producer
                    assert row['pdb_path'] == 'native-sequence.pdb'
                    assert row['plddt_overall'] is None
            assert sorted(observed) == sorted(identity for identity, model in expected.items() if model == producer)
        for identity, producer in expected.items():
            detail = await client.get(f'/api/designs/{identity}', params={'job_id': 'parent'})
            assert detail.status_code == 200, detail.text
            assert (detail.json()['provenance'] or {}).get('producer_model_id') == producer
        exact = await client.post('/api/designs/query', json={**base, 'model_id': 'fampnn', 'design_ids': ['sequence', 'foreign']})
        assert [row['id'] for row in exact.json()['designs']] == ['sequence']
        assert exact.json()['model_counts'] == counts
        own = await client.get('/api/designs', params={**base, 'include_children': False, 'model_id': 'fampnn'})
        assert own.json()['total'] == counts['fampnn'] - 1
        assert own.json()['model_counts'] == {**counts, 'fampnn': counts['fampnn'] - 1}
        child = await client.get('/api/designs', params={**base, 'job_id': 'child'})
        assert child.json()['model_counts'] == {'fampnn': 1}
        by_job = await client.get('/api/designs/by-job/parent', params={'limit': 500})
        assert {row['id']: (row['provenance'] or {}).get('producer_model_id') for row in by_job.json()['designs']} == expected
        assert (await client.get('/api/designs/foreign', params={'job_id': 'parent'})).status_code == 404
        empty = await client.get('/api/designs', params={**base, 'model_id': 'unrecorded'})
        assert empty.json()['total'] == 0
        assert empty.json()['model_counts'] == counts
    async with factory() as session:
        stored = dict((await session.execute(select(Design.id, Design.provenance))).all())
        assert all(stored[identity] == provenance for identity, provenance, _ in cases)
    await engine.dispose()


def test_historical_design_reopen_uses_receipt_identity_without_mutating_acknowledgement():
    payload = {"entity_kind": "design", "entity_id": "second", "reopen_uri": "/designs/job", "metadata": {"design_id": "second"}}
    assert _reopen_route(payload)["query"] == {"design_id": "second"}
    assert payload["reopen_uri"] == "/designs/job"
    with pytest.raises(ValidationFailure, match="different candidate"):
        _reopen_route({**payload, "reopen_uri": "/designs/job?design_id=first"})
    with pytest.raises(ValidationFailure, match="identity"):
        _reopen_route({**payload, "metadata": {"design_id": "foreign"}})

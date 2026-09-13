"""Disposable actual-router measurements; full-load oracle preserves wire semantics."""
import json
import os
import statistics
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import InvalidRequestError

from database import Design, Job, get_session
from routers import analytics
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_scalar_routes_match_full_hydration(tmp_path, monkeypatch, native):
    count = 8 if native else 128
    if native:
        publication(tmp_path, count=count)
    factory, engine = await setup(tmp_path)
    narrow = analytics._scalar_design_query
    statements = []
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', record)
    try:
        async with factory() as session:
            owner = job(tmp_path) if native else Job(id='job', name='fixture', model_id='boltz2', mode='predict', params={}, provenance={})
            session.add(owner)
            await session.commit()
            if native:
                await ingest_job_results('job', str(tmp_path), session)
            else:
                session.add_all([Design(id=f'd{i:04}', job_id='job', name=f'd{i:04}', pdb_path='fixture.pdb',
                    plddt_overall=float(i % 100), pae_overall=float(i % 17), ptm=(i % 10)/10,
                    is_favorite=i % 2 == 0) for i in range(count)])
                await session.commit()
            # Unrelated retained matrix/detail payload, not scientific authority.
            for row in (await session.scalars(select(Design))).all():
                row.pae_matrix = [[0.25] * 100 for _ in range(100)]
                row.stability_data = {'values': [0.5] * 1000}
                row.residue_plddt = [75.0] * 100
                row.chain_metrics = {'A': {'values': [0.5] * 100}}
            await session.commit()
        app = FastAPI()
        app.include_router(analytics.router, prefix='/analytics')
        async def sessions():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        routes = [('GET', '/analytics/job/job', None), ('GET', '/analytics/job/job/designs', None),
                  ('POST', '/analytics/batch', ['job', 'missing', 'job'])]
        measurements = {}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            for method, path, body in routes:
                expected = None
                results = {}
                for label, query in [('full', lambda: select(Design)), ('narrow', narrow)]:
                    monkeypatch.setattr(analytics, '_scalar_design_query', query)
                    samples = []
                    queries = []
                    for _ in range(3):
                        statements.clear()
                        started = time.perf_counter()
                        response = await client.request(method, path, json=body)
                        samples.append((time.perf_counter() - started) * 1000)
                        assert response.status_code == 200, response.text
                        queries.append(len(statements))
                        if expected is None:
                            expected = response.json()
                        assert response.json() == expected
                    results[label] = {'milliseconds': samples, 'median_ms': statistics.median(samples),
                                      'queries': queries, 'payload_bytes': len(response.content)}
                assert results['full']['queries'] == results['narrow']['queries']
                measurements[path] = results
        # No deferred I/O can creep back into the scalar read path.
        async with factory() as session:
            row = (await session.scalars(narrow())).first()
            for field in ('pae_matrix', 'stability_data', 'residue_plddt', 'chain_metrics'):
                assert field in inspect(row).unloaded
                with pytest.raises(InvalidRequestError):
                    getattr(row, field)
        if native:
            cohort = expected['scientific_cohorts'][0]
            assert len(cohort['design_ids']) == count
            assert cohort['pairs']['complex_plddt_vs_ptm']['pair_count'] == count
        target = os.environ.get('BMS_ANALYTICS_PERFORMANCE_DIR')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / f'analytics-measurements-{native}.json').write_text(json.dumps(
                {'native': native, 'design_count': count, 'matrix_shape': [100, 100], 'routes': measurements}, indent=2))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scalar_scope_and_not_found(tmp_path):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add_all([Job(id=k, name=k, model_id='boltz2', mode='predict', params={}, provenance={},
                                parent_job_id='parent' if k == 'child' else None) for k in ['parent', 'child', 'empty']])
            session.add(Design(id='child-design', job_id='child', name='child', pdb_path='fixture.pdb'))
            await session.commit()
        async with factory() as session:
            included = await analytics.get_job_analytics('parent', True, session)
            excluded = await analytics.get_job_analytics('parent', False, session)
            assert included.design_count == 1
            assert excluded.design_count == 0 and excluded.metrics is None
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as error:
                await analytics.get_job_analytics('missing', True, session)
            assert error.value.status_code == 404
    finally:
        await engine.dispose()

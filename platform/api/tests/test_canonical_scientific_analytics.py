"""Native fixture publication -> disposable SQLite -> canonical analytics reads."""
import copy
import pytest
from sqlalchemy import select
from database import Design
from services.result_ingester import ingest_job_results
from services import scientific_analytics as scientific
from test_boltz_scientific_persistence import publication, job, setup


@pytest.mark.asyncio
async def test_native_canonical_scalars_ignore_legacy_columns(tmp_path, monkeypatch):
    publication(tmp_path, count=1)
    factory, engine = await setup(tmp_path)
    async with factory() as session:
        owner = job(tmp_path)
        session.add(owner)
        await session.commit()
        await ingest_job_results('job', str(tmp_path), session)
    async with factory() as session:
        row = (await session.execute(select(Design))).scalar_one()
        row.ptm = 999
        row.plddt_overall = 999
        result = await scientific.persisted_projection(row, session)
        assert set(result['metrics']) == {'ptm', 'complex_plddt'}
        assert result['metrics']['ptm'] != 999
        assert result['metric_descriptors']['complex_plddt'].unit == 'fraction'
        assert result['metric_sources']['ptm']['candidate_id'] != row.id
        assert result['source_job_id'] == row.job_id
        assert result['metric_descriptors']['ptm'].producer_version
        assert result['metric_descriptors']['ptm'].derivation_version
    await engine.dispose()


@pytest.mark.asyncio
async def test_changed_canonical_block_is_unavailable_without_legacy_fallback(tmp_path, monkeypatch):
    publication(tmp_path, count=1)
    factory, engine = await setup(tmp_path)
    async with factory() as session:
        owner = job(tmp_path)
        session.add(owner)
        await session.commit()
        await ingest_job_results('job', str(tmp_path), session)
        row = (await session.execute(select(Design))).scalar_one()
        data = copy.deepcopy(row.confidence_metrics)
        data['core_protein_scientific']['metrics'][0]['value'] = True
        row.confidence_metrics = data
        await session.commit()
    async with factory() as session:
        row = (await session.execute(select(Design))).scalar_one()
        result = await scientific.persisted_projection(row, session)
        assert result['metrics'] == {}
        assert result['metric_states']['ptm'].state == 'invalid'
        assert result['metric_states']['ptm'].reason_code == 'invalid_canonical_publication'
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("zero", [False, True])
async def test_actual_api_transports_canonical_scalar_sources(tmp_path, zero):
    import os, json
    from pathlib import Path
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from database import get_session
    from routers.analytics import router as analytics_router
    from routers.designs import router as designs_router
    publication(tmp_path, count=2, native_scalars=[{"ptm":0,"complex_plddt":0},{"ptm":0.6,"complex_plddt":0.6}] if zero else None)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            app = FastAPI()
            app.include_router(analytics_router, prefix='/analytics')
            app.include_router(designs_router, prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                raw = await client.get('/analytics/job/job/designs')
                assert raw.status_code == 200, raw.text
                assert all(set(p['metrics']) == {'ptm','complex_plddt'} for p in raw.json())
                if zero:
                    assert any(p['metrics'].get('ptm') == 0 for p in raw.json())
                assert all(p['metric_sources']['ptm']['artifact_sha256'] for p in raw.json())
                chart = await client.get('/designs/by-job/job/plotly-metrics')
                assert chart.status_code == 200, chart.text
                wire = chart.json()
                assert len(wire['points']) == 2
                assert wire['scientific_cohorts'][0]['pairs']['complex_plddt_vs_ptm']['pair_count'] == 2
                target = os.environ.get('BMS_ANALYTICS_ZERO_WIRE' if zero else 'BMS_ANALYTICS_WIRE')
                if target:
                    Path(target).write_text(json.dumps(wire, allow_nan=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_esmfold2_actual_api_retains_asymmetric_missingness(tmp_path, monkeypatch):
    import os, json
    from pathlib import Path
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from database import get_session
    from routers import analytics, designs
    from services.result_ingester import ingest_esmfold2_results
    from tests.test_core_protein_candidates import artifacts, setup, job
    from tests.test_esmfold2_scalars import DIALECT
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path/'artifacts'))
    root=artifacts(tmp_path, ('a','b'))
    for name,plddt,ptm in [('a',None,0.7),('b',0.005,None)]:
        path=root/f'{name}.metrics.json'; payload=json.loads(path.read_text())
        payload.update(scalar_dialect=DIALECT,plddt_mean=plddt,ptm=ptm,iptm=0)
        path.write_text(json.dumps(payload))
    factory,engine=await setup(tmp_path)
    try:
        async with factory() as session:
            current=job(tmp_path);session.add(current);await session.commit()
            await ingest_esmfold2_results('job',tmp_path,session,current)
        app=FastAPI();app.include_router(analytics.router,prefix='/api/analytics');app.include_router(designs.router,prefix='/api/designs')
        async def sessions():
            async with factory() as session:yield session
        app.dependency_overrides[get_session]=sessions
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            response=await client.get('/api/designs/by-job/job/plotly-metrics')
            assert response.status_code==200,response.text
            body=response.json(); points=body['points']; cohorts=body['scientific_cohorts']
            assert len(points)==2 and len(cohorts)==1
            assert set(points[0]['metric_states'])=={'plddt','ptm','iptm'}
            summary=cohorts[0]
            assert summary['metrics']['plddt']['observed_count']==1
            assert summary['metrics']['plddt']['unavailable_count']==1
            assert summary['pairs']['plddt_vs_ptm']['pair_count']==0
            assert summary['pairs']['iptm_vs_plddt']['pair_count']==1
            assert summary['pairs']['iptm_vs_ptm']['pair_count']==1
            for point in points:
                assert point['metrics']['iptm']==0
                assert point['metric_descriptors']['ptm']['unit']=='dimensionless'
                assert point['metric_descriptors']['plddt']['scope']=='model_token_mean'
            if os.environ.get('BMS_ESM_ANALYTICS_WIRE'):
                Path(os.environ['BMS_ESM_ANALYTICS_WIRE']).write_text(json.dumps(body,allow_nan=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_parent_keeps_legacy_values_out_of_native_cohorts(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from database import get_session, Job
    from routers.designs import router
    publication(tmp_path, count=1)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            owner=job(tmp_path)
            owner.parent_job_id='parent'
            session.add_all([Job(id='parent',name='parent',model_id='boltz',mode='predict',status='completed',params={},provenance={}),owner,
                Job(id='legacy',name='legacy',model_id='boltz',mode='predict',status='completed',parent_job_id='parent',params={},provenance={})])
            await session.commit()
            await ingest_job_results('job',str(tmp_path),session)
            session.add(Design(id='old',job_id='legacy',name='old',pdb_path='legacy.pdb',plddt_overall=42,ptm=0.9,confidence_metrics={'seed':17}))
            await session.commit()
        async with factory() as session:
            app=FastAPI();app.include_router(router,prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session]=dependency
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
                response=await client.get('/designs/by-job/parent/plotly-metrics')
                assert response.status_code==200,response.text
                data=response.json()
                assert len(data['points'])==2
                assert len(data['scientific_cohorts'])==1
                assert 'old' not in data['scientific_cohorts'][0]['design_ids']
                legacy=next(p for p in data['points'] if p['id']=='old')
                assert legacy['contract_revision'] is None
                assert legacy['metrics']['plddt_overall']==42
                assert legacy['metrics']['ptm']==0.9
                row=await session.get(Design,'old')
                assert row.confidence_metrics=={'seed':17}
    finally:
        await engine.dispose()

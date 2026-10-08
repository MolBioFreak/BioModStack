"""Native publication custody -> scratch SQLite -> existing Charts transport."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import event

from database import Design, get_session
from services import scientific_analytics as analytics
import test_protenix_native_confidence as native


async def fixture(tmp_path, monkeypatch, summary, custody='local'):
    source, full, _ = native.mixed_bytes()
    raw = summary if isinstance(summary, bytes) else json.dumps(summary).encode()
    monkeypatch.setattr(native, 'mixed_bytes', lambda: (source, full, raw))
    job, design, root = native.make_publication(tmp_path, custody=custody)
    engine, factory = await native.store(tmp_path, job, design)
    return engine, factory, root


@pytest.mark.asyncio
@pytest.mark.parametrize('custody', ['local', 'retained_remote'])
async def test_charts_native_scalars_and_sources_without_spatial_reads(tmp_path, monkeypatch, custody):
    summary = dict(plddt=.5, ptm=0, iptm=.8, gpde=1.25, ranking_score=-99.2,
                   chain_plddt=[.99], has_clash=True)
    engine, factory, root = await fixture(tmp_path, monkeypatch, summary, custody)
    # Summary analytics does not require a PAE matrix or atom correspondence.
    next(root.rglob('*_full_data_sample_0.json')).unlink()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    writes = []
    def statement(conn, cursor, sql, params, context, many):
        if sql.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
            writes.append(sql)
    event.listen(engine.sync_engine, 'before_cursor_execute', statement)
    try:
        async with factory() as session:
            row = await session.get(Design, 'candidate')
            original = deepcopy(row.confidence_metrics)
            row.plddt_overall = 999
            row.ptm = 999
            result = await analytics.persisted_projection(row, session)
            assert result['metrics'] == {k: summary[k] for k in ('plddt', 'ptm', 'iptm', 'gpde', 'ranking_score')}
            assert result['metric_descriptors']['plddt'].unit == 'percent'
            assert result['metric_descriptors']['plddt'].scope == 'model_atom_mean'
            assert result['metric_descriptors']['gpde'].unit == 'angstrom'
            assert result['metric_descriptors']['gpde'].direction == 'lower'
            source = result['metric_sources']['plddt']
            assert source['candidate_id'] == row.provenance['native_producer']['producer_output_key']
            assert source['document_id'] == source['candidate_id']
            assert source['artifact_sha256'] == hashlib.sha256(Path(row.json_path).read_bytes()).hexdigest()
            # Drop only deliberately dirtied legacy columns before actual HTTP reads.
            await session.rollback()
            from routers.designs import router
            from routers.analytics import router as analytics_router
            app = FastAPI()
            app.include_router(router, prefix='/api/designs')
            app.include_router(analytics_router, prefix='/api/analytics')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/api/designs/by-job/job/plotly-metrics')
                assert response.status_code == 200, response.text
                body = response.json()
                point = body['points'][0]
                assert point['metrics'] == result['metrics']
                assert point['metric_sources'] == result['metric_sources']
                assert point['publication_state'] is None
                cohort = body['scientific_cohorts'][0]
                assert cohort['metrics']['plddt']['statistics']['avg'] == .5
                assert cohort['pairs']['plddt_vs_ptm']['pair_count'] == 1
                response = await client.get('/api/analytics/job/job/designs')
                assert response.status_code == 200, response.text
                assert response.json()[0]['metrics'] == result['metrics']
            assert (await session.get(Design, 'candidate')).confidence_metrics == original
            assert not session.dirty
        assert not writes
        assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()} == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('summary,expected', [
    ({'plddt': 0, 'ptm': None}, {'plddt': ('ok', None), 'ptm': ('unavailable', 'native_null'), 'iptm': ('unavailable', 'not_reported')}),
    ({'plddt': True, 'ptm': '0.9', 'iptm': []}, {k: ('invalid', 'not_finite_real') for k in ('plddt', 'ptm', 'iptm')}),
    ({'plddt': 101, 'ptm': -1, 'iptm': 2, 'gpde': -1}, {k: ('invalid', 'out_of_range') for k in ('plddt', 'ptm', 'iptm', 'gpde')}),
    ({'full_plddt': 95, 'complex_plddt': .9, 'confidence_score': .8, 'pae': 3}, {k: ('unavailable', 'not_reported') for k in ('plddt', 'ranking_score', 'gpde')}),
])
async def test_native_missing_null_zero_invalid_and_no_aliases(tmp_path, monkeypatch, summary, expected):
    engine, factory, _ = await fixture(tmp_path, monkeypatch, summary)
    try:
        async with factory() as session:
            result = await analytics.persisted_projection(await session.get(Design, 'candidate'), session)
            for key, state in expected.items():
                assert (result['metric_states'][key].state, result['metric_states'][key].reason_code) == state
            if summary.get('plddt') == 0:
                assert result['metrics']['plddt'] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['bytes', 'missing', 'foreign_path', 'foreign_candidate', 'nonfinite', 'duplicate'])
async def test_no_legacy_fallback_for_invalid_native_evidence(tmp_path, monkeypatch, fault):
    summary = {'plddt': 90, 'ptm': .9}
    if fault == 'nonfinite':
        summary['ptm'] = float('nan')
    elif fault == 'duplicate':
        summary = b'{"ptm":0,"ptm":1}'
    engine, factory, root = await fixture(tmp_path, monkeypatch, summary)
    try:
        async with factory() as session:
            row = await session.get(Design, 'candidate')
            row.ptm = .99
            path = Path(row.json_path)
            if fault == 'bytes': path.write_text('{"plddt": 99}')
            elif fault == 'missing': path.unlink()
            elif fault == 'foreign_path': row.json_path = str(root/'foreign.json')
            elif fault == 'foreign_candidate':
                data = deepcopy(row.provenance)
                data['native_producer']['producer_output_key'] = 'foreign_sample_0.cif'
                row.provenance = data
            result = await analytics.persisted_projection(row, session)
            assert result['metrics'] == {}
            assert result['publication_state'].reason_code == 'invalid_canonical_publication'
            assert all(s.state == 'invalid' for s in result['metric_states'].values())
            assert all(s is None for s in result['metric_sources'].values())
    finally:
        await engine.dispose()

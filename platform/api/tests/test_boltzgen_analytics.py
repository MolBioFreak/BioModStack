"""Real software publication -> SQLite reload -> mounted-consumer wire."""
import json
import os
from pathlib import Path
import numpy as np
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import get_session
from routers import analytics, designs
from test_boltzgen_native_scalars import observed_source, publish, setup, make_job, publication


def publish_csv(tmp_path, identity):
    from types import SimpleNamespace
    from lib.filtering.evidence import csv_metadata
    from filter_boltzgen import run_strict_filter
    source = tmp_path / 'native.csv'
    source.write_text('id,design_ptm,affinity_probability_binary1,filter_rmsd\na,0,0,0\n')
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    csv_metadata(source, inputs, {'a'}, producer_identity=identity, filter_from_inverse_folded=True)
    pdb = inputs / 'a.pdb'
    pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    out = tmp_path / 'collected/boltzgen_filtered'; out.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(pdb)], jsons=[str(inputs / 'confidence_a.json')],
        out_dir=str(out), filter_biased='false', metrics_override='design_ptm=none affinity_probability=none filter_rmsd=none',
        additional_filters=None, size_buckets=None, boltzgen_min_plddt=None, boltzgen_min_conf_score=None,
        boltzgen_max_rmsd=None, budget=1, alpha=0))
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['zero', 'csv_zero', 'missing', 'invalid', 'source_swapped', 'unknown_producer'])
async def test_boltzgen_published_analytics_wire(tmp_path, monkeypatch, case):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    identity, _ = observed_source(tmp_path)
    values = {'affinity_probability_binary1': np.array([0.])}
    if case != 'missing':
        values['design_ptm'] = np.array([float('nan') if case == 'invalid' else 0.])
    out = publish_csv(tmp_path, identity) if case == 'csv_zero' else publish(
        tmp_path, identity=None if case == 'unknown_producer' else identity, values=values)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            owner = make_job(tmp_path); db.add(owner); await db.commit()
            await publication.ingest(owner, tmp_path, db)
        if case == 'source_swapped':
            (out / 'native_a.npz').write_bytes(b'foreign native bytes')
        app = FastAPI()
        app.include_router(analytics.router, prefix='/api/analytics')
        app.include_router(designs.router, prefix='/api/designs')
        async def sessions():
            async with factory() as db:
                yield db
        app.dependency_overrides[get_session] = sessions
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/designs/by-job/job/plotly-metrics')
            assert response.status_code == 200, response.text
            wire = response.json()
            point, = wire['points']
            assert set(point['metric_states']) == {'design_ptm', 'affinity_probability', 'filter_rmsd'}
            state = {'zero':'ok', 'csv_zero':'ok', 'missing':'unavailable', 'invalid':'invalid', 'source_swapped':'invalid', 'unknown_producer':'unavailable'}[case]
            assert point['metric_states']['design_ptm']['state'] == state
            assert point['metric_states']['design_ptm']['value'] == (0 if state == 'ok' else None)
            assert point['metric_descriptors']['design_ptm']['unit'] == 'fraction'
            assert point['metric_descriptors']['filter_rmsd']['unit'] == 'angstrom'
            assert point['metric_descriptors']['filter_rmsd']['direction'] == 'lower'
            if case == 'source_swapped':
                assert point['metrics'] == {}
                assert all(v is None for v in point['metric_sources'].values())
            elif case != 'unknown_producer':
                assert point['metrics']['affinity_probability'] == 0
                assert point['metric_sources']['design_ptm']['candidate_id'] == point['id']
            cohort, = wire['scientific_cohorts']
            pair = cohort['pairs']['affinity_probability_vs_design_ptm']
            assert pair['pair_count'] == (1 if case in {'zero', 'csv_zero'} else 0)
            if case == 'csv_zero':
                assert point['metrics']['filter_rmsd'] == 0
                assert point['metric_descriptors']['filter_rmsd']['scope'] == 'native_refolded_complex_backbone'
                assert all(pair['pair_count'] == 1 for pair in cohort['pairs'].values())
            raw = await client.get('/api/analytics/job/job/designs')
            assert raw.status_code == 200, raw.text
            assert raw.json()[0]['metric_states'] == point['metric_states']
            directory = os.environ.get('BMS_BOLTZGEN_ANALYTICS_WIRES')
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                (Path(directory) / f'{case}.json').write_text(json.dumps(wire, allow_nan=False))
    finally:
        await engine.dispose()

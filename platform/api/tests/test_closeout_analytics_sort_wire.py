"""Native CSV -> filter -> SQLite -> actual chart wire for metric selection."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import get_session
from routers.designs import router
from test_boltzgen_native_scalars import observed_source, setup, make_job, publication
from lib.filtering.evidence import csv_metadata
from filter_boltzgen import run_strict_filter


@pytest.mark.asyncio
async def test_native_rmsd_sort_wire(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    identity, _ = observed_source(tmp_path)
    source = tmp_path / 'native.csv'
    source.write_text('id,design_ptm,affinity_probability_binary1,filter_rmsd\nmissing,0,0,\nhigh,0,0,5\nzero,0,0,0\n')
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    names = ['missing', 'high', 'zero']
    csv_metadata(source, inputs, set(names), producer_identity=identity, filter_from_inverse_folded=True)
    for name in names:
        (inputs / f'{name}.pdb').write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    out = tmp_path / 'collected/boltzgen_filtered'; out.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(inputs/f'{n}.pdb') for n in names],
        jsons=[str(inputs/f'confidence_{n}.json') for n in names], out_dir=str(out), filter_biased='false',
        metrics_override='design_ptm=none affinity_probability=none filter_rmsd=none',
        additional_filters=None,size_buckets=None,boltzgen_min_plddt=None,boltzgen_min_conf_score=None,
        boltzgen_max_rmsd=None,budget=3,alpha=0))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            owner = make_job(tmp_path); db.add(owner); await db.commit()
            assert await publication.ingest(owner, tmp_path, db) == 3
        app = FastAPI(); app.include_router(router,prefix='/designs')
        async def sessions():
            async with factory() as db:
                yield db
        app.dependency_overrides[get_session] = sessions
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
            response = await client.get('/designs/by-job/job/plotly-metrics')
            assert response.status_code == 200, response.text
            wire = response.json()
            assert len(wire['scientific_cohorts']) == 1
            assert sorted(p['metrics']['filter_rmsd'] for p in wire['points'] if 'filter_rmsd' in p['metrics']) == [0,5]
            if os.environ.get('BMS_SORT_ANALYTICS_WIRE'):
                Path(os.environ['BMS_SORT_ANALYTICS_WIRE']).write_text(json.dumps(wire))
    finally:
        await engine.dispose()

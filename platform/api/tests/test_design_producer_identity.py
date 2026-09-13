"""New native publication → Parquet/SQLite → fresh-process HTTP acceptance.

Native files are generated fixtures, not scientific inference runs.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers import designs
from services.result_ingester import ingest_job_results
from test_generic_sequence_results import native_fixture


async def reopen_http(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        expected = dict((await session.execute(select(Design.id, Design.producer_model_id).where(
            Design.job_id != 'foreign'))).all())
        foreign = (await session.execute(select(Design.id).where(Design.job_id == 'foreign'))).scalar_one()
    counts = {'fampnn': 2, 'proteinmpnn': 2, 'future_native': 1}
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        base = {'job_id': 'parent', 'include_summary': True, 'sort_by': 'name', 'sort_desc': False}
        # Offset past all rows exercises real HTTP count/group/summary SQL with
        # Parquet resolution forbidden, not merely a hand-written SELECT.
        with patch('services.scientific_artifacts.resolve_json_value', side_effect=AssertionError('counts opened Parquet')):
            response = await client.get('/api/designs', params={**base, 'offset': 100, 'limit': 1})
            assert response.status_code == 200, response.text
            assert response.json()['model_counts'] == counts
            assert response.json()['total'] == response.json()['summary']['total'] == 6
        for model, count in counts.items():
            observed = []
            for offset in range(count):
                response = await client.get('/api/designs', params={**base, 'model_id': model.upper(), 'offset': offset, 'limit': 1})
                assert response.status_code == 200, response.text
                body = response.json()
                assert body['total'] == body['summary']['total'] == count
                assert body['model_counts'] == counts
                row = body['designs'][0]
                assert row['provenance']['producer_model_id'] == model
                observed.append(row['id'])
            assert set(observed) == {key for key, value in expected.items() if value == model}
        for identity, model in expected.items():
            response = await client.get(f'/api/designs/{identity}', params={'job_id': 'parent'})
            assert response.status_code == 200, response.text
            assert (response.json()['provenance'] or {}).get('producer_model_id') == model
        chosen = next(key for key, value in expected.items() if value == 'proteinmpnn')
        exact = await client.post('/api/designs/query', json={**base, 'design_ids': [chosen, foreign], 'model_id': 'proteinmpnn'})
        assert exact.status_code == 200, exact.text
        assert [row['id'] for row in exact.json()['designs']] == [chosen]
        assert exact.json()['model_counts'] == counts
        assert (await client.get(f'/api/designs/{foreign}', params={'job_id': 'parent'})).status_code == 404
        own = await client.get('/api/designs', params={**base, 'include_children': False})
        assert own.json()['total'] == 4
        assert own.json()['model_counts'] == {'fampnn': 2, 'future_native': 1}
        by_job = await client.get('/api/designs/by-job/parent')
        assert {row['id'] for row in by_job.json()['designs']} == set(expected)
        for stage in ('protein_modification_experimental', 'validation', 'unrecorded'):
            empty = await client.get('/api/designs', params={**base, 'model_id': stage})
            assert empty.json()['total'] == 0
            assert empty.json()['model_counts'] == counts
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_batch_publication_survives_fresh_process_http(tmp_path):
    path = tmp_path / 'producers.db'
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for identity, model, parent, selected in [('parent', 'fampnn', None, [0, 1]),
                ('child', 'proteinmpnn', 'parent', [0, 1]), ('foreign', 'fampnn', None, [0])]:
            root = tmp_path / identity; root.mkdir()
            output, _ = native_fixture(root, model, selected, note='native fixture ' * 30000)
            session.add(Job(id=identity, name=identity, model_id=model, mode='design',
                            parent_job_id=parent, status='completed', params={}, output_dir=str(output)))
            await session.commit()
            assert await ingest_job_results(identity, output, session) == len(selected)
        # Unattributed workflow/reference rows stay honest despite tempting tags,
        # paths and upstream provenance; an explicitly declared unknown ID survives.
        session.add_all([
            Design(id='reference', job_id='parent', name='reference_seq_0', pdb_path='/pdb_files/reference_seq_0.pdb',
                   stage_family='validation', provenance={'model_id': 'protein_modification_experimental', 'sequence_design_model': 'fampnn', 'producer_model_id': 'validation'}),
            Design(id='future', job_id='parent', name='future', pdb_path='future.pdb', producer_model_id='future_native'),
        ])
        await session.commit()
    await engine.dispose()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT producer_model_id, provenance FROM designs WHERE id NOT IN ('reference','future')").fetchall()
        assert len(rows) == 5
        assert all(json.loads(raw)['schema'] == 'bms.scientific-artifact-row-reference.v1' for _, raw in rows)
        assert connection.execute("SELECT count(*) FROM scientific_artifact_receipts WHERE owner_kind='design_field'").fetchone()[0] >= 5
    await reopen_http(path)
    # A brand-new interpreter has no Session identity map or artifact resolver cache.
    code = "import asyncio,sys; sys.path.insert(0, 'tests'); from test_design_producer_identity import reopen_http; asyncio.run(reopen_http(sys.argv[1]))"
    result = subprocess.run([sys.executable, '-c', code, str(path)], cwd=Path(__file__).resolve().parents[1],
                            env=os.environ.copy(), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_native_fampnn_sidecar_binds_only_its_produced_structure(tmp_path):
    import importlib.util
    from services.result_ingester import ingest_loose_files, _fampnn_producer_identity
    script = Path(__file__).resolve().parents[3] / 'scripts/analyse_fampnn.py'
    spec = importlib.util.spec_from_file_location('native_psce_producer', script)
    owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)
    output = tmp_path / 'output'; structures = output / 'pdb_files'; structures.mkdir(parents=True)
    pdb = structures / 'candidate.pdb'
    pdb.write_text('ATOM      1  CA  SER A   1       0.000   0.000   0.000  1.00  0.00           C\n'
                   'ATOM      2  CB  SER A   1       1.000   0.000   0.000  1.00  2.00           C\n'
                   'ATOM      3  OG  SER A   1       2.000   0.000   0.000  1.00  8.00           O\nEND\n')
    owner.average_per_residue_bfactor(structures, 'all_chains', True, structures)
    payload = json.loads(pdb.with_suffix('.json').read_text())
    assert _fampnn_producer_identity(payload, pdb) == 'fampnn'
    downstream = tmp_path / 'predicted.pdb'; downstream.write_bytes(pdb.read_bytes() + b'REMARK predicted\n')
    assert _fampnn_producer_identity(payload, downstream) is None
    path = tmp_path / 'sidecar.db'
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(id='workflow', name='TEST workflow', model_id='template_antibody_denovo',
                  mode='antibody_refinement_pipeline', params={}, output_dir=str(output))
        session.add(job); await session.commit()
        assert await ingest_loose_files(job.id, output, session, current_job=job) == 1
    async with factory() as session:
        row = (await session.execute(select(Design))).scalar_one()
        assert row.producer_model_id == 'fampnn'
        assert row.provenance['model_id'] == 'template_antibody_denovo'
    await engine.dispose()


def test_registered_structural_upgrade_does_not_infer_data(tmp_path):
    from test_database_bootstrap import run
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    path = tmp_path / 'core.db'
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE designs DROP COLUMN producer_model_id')
        connection.execute('DELETE FROM schema_migrations WHERE version=47')
        connection.execute("INSERT INTO designs(id,job_id,name,pdb_path,provenance) VALUES('unattributed','job','name','source.pdb','{\"model_id\":\"fampnn\"}')")
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT producer_model_id FROM designs').fetchone() == (None,)
        assert connection.execute('SELECT name FROM schema_migrations WHERE version=47').fetchone() == ('add_design_producer_identity',)
    assert run(tmp_path).returncode == 0

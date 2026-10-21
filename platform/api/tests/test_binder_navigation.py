"""Real scratch publication -> mounted router -> compact JSON. No native runs."""
import csv
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs as jobs_router
from services import bindcraft2_result_readback as readback
from services.result_ingester import ingest_job_results
from test_bindcraft2_publication import campaign, table
from test_bindcraft2_nested_publication import materialize


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'navigation.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[jobs_router.get_session] = session_override
    app.include_router(jobs_router.router, prefix='/api/jobs')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield factory, client, engine
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('sweep,zero', [(False, False), (True, False), (False, True), (True, True)])
async def test_navigation_real_publication(store, tmp_path, monkeypatch, sweep, zero):
    factory, client, _ = store
    root, native, params = materialize(tmp_path, monkeypatch, sweep=sweep)
    arms = ('arm_alpha', 'arm_beta') if sweep else (None,)
    for arm in arms:
        folder = native / arm if arm else native
        campaign(folder, zero=zero)
        trajectory = folder / '1_Trajectories/!_Trajectories.csv'
        rows = list(csv.DictReader(trajectory.open()))
        rows.append({**rows[0], 'design': 't_other', 'trajectory': '007.0', 'bms_attempt_sha256': ''})
        table(trajectory, rows)
        if not zero:
            refolded = folder / '2_Refolded/!_Refolded.csv'
            rows = list(csv.DictReader(refolded.open()))
            if arm == 'arm_beta':
                rows[0]['Binder_Sequence'] = 'DDD'
            rows.extend({**rows[0], 'design': f't_candidate{i}'} for i in range(3, 108))
            table(refolded, rows)
            # Explicit native producer stamp associates a rejected draw. Filename
            # deliberately does not match its design. Ranked docs remain distinct.
            for state in ('stateA', 'stateB'):
                (folder / f'2_Refolded/unrelated_filename_{state}.cif').write_text(
                    'data_model\n_bindcraft.design t_candidate1\n'
                    f'_bindcraft.bms_target_state {state}\n'
                    '_bindcraft.binder_chains B\n_bindcraft.target_chains A\n_atom_site.id 1\n')
    if sweep:
        table(native / 'sweep.csv', [{'arm': arm, 'rank': str(i)} for i, arm in enumerate(arms)])
    async with factory() as session:
        session.add(Job(id='bc', name='fixture', model_id='bindcraft2', mode='campaign',
                        status='completed', params=params, output_dir=str(root)))
        session.add(Job(id='wrong', name='wrong', model_id='proteinmpnn', mode='design', params={}))
        session.add(Job(id='unpublished', name='unpublished', model_id='bindcraft2', mode='campaign', params={}))
        await session.commit()
        await ingest_job_results('bc', str(root), session)
    calls = []
    real_read = readback.read_published_native_results

    async def counted(*args, **kwargs):
        calls.append(args[0].id)
        return await real_read(*args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('compact navigation must not compute analytics/decorate artifacts')

    monkeypatch.setattr(readback, 'read_published_native_results', counted)
    for name in ('_trace', 'trajectory_analytics', 'native_workbench_page'):
        monkeypatch.setattr(readback, name, forbidden)
    for arm in (None, *arms) if sweep else arms:
        before = len(calls)
        response = await client.get('/api/jobs/bc/bindcraft2-navigation', params={} if arm is None else {'arm': arm})
        assert response.status_code == 200, response.text
        assert len(calls) == before + 1
        data = response.json()
        assert set(data) == {'job_id', 'arm', 'trajectories', 'draws', 'total'}
        assert data['arm'] == (arm or arms[0])
        assert data['trajectories'] == [{'design': 't', 'trajectory': '1'},
                                         {'design': 't_other', 'trajectory': '007.0'}]
        assert data['total'] == len(data['draws']) == (0 if zero else 107)
        if not zero:
            first = data['draws'][0]
            assert set(first) == {'design', 'arm', 'trajectory_design', 'sequence', 'structures'}
            assert first['sequence'] == ('DDD' if arm == 'arm_beta' else 'AAA')
            assert first['trajectory_design'] == 't'
            assert len(first['structures']) == 2
            assert {item['target_state'] for item in first['structures']} == {'stateA', 'stateB'}
            assert len({item['artifact_id'] for item in first['structures']}) == 2
            assert set(first['structures'][0]) == {'artifact_id', 'target_state', 'binder_chains', 'target_chains'}
            assert first['structures'][0]['binder_chains'] == 'B'
            assert data['draws'][-1]['structures'] == []
    for job, expected in [('absent', 404), ('wrong', 400), ('unpublished', 409)]:
        assert (await client.get(f'/api/jobs/{job}/bindcraft2-navigation')).status_code == expected
    assert (await client.get('/api/jobs/bc/bindcraft2-navigation?arm=unknown')).status_code == 409
    folder = native / arms[0] if arms[0] else native
    changed = folder / '1_Trajectories/!_Trajectories.csv'
    changed.write_text(changed.read_text() + '\n')
    before = len(calls)
    assert (await client.get('/api/jobs/bc/bindcraft2-navigation')).status_code == 409
    assert len(calls) == before + 1


@pytest.mark.asyncio
async def test_jobs_projection_family_and_fresh_reads(store):
    factory, client, engine = store
    component = {'sequence': 'AAA', 'role': 'binder', 'count': 'wrong', 'copies': None, 'extra': 'drop'}
    params = {'lineage_root_job_id': 'root', 'selection_source_type': None,
              'sequence_name': ['wrong'], 'complex_components': [component, None, 4], 'big_settings': 'drop'}
    step = {'source_ref': {'job_id': 'root', 'native_record_id': 't_candidate1', 'extra': 'drop'},
            'source_job_id': 'root', 'retry_of_job_id': 'previous', 'stage': None,
            'input_components': [component], 'receipt': 'drop'}
    async with factory() as session:
        for identity in ('root', 'child_b', 'child_a', 'unrelated'):
            session.add(Job(id=identity, name=identity, model_id='bindcraft2', mode='campaign',
                            status='completed', created_at=datetime(2025, 1, 1),
                            parent_job_id='root' if identity.startswith('child') else None,
                            params=params if identity.startswith('child') else {},
                            provenance={'binder_round_step': step, 'huge_receipt': 'drop'}))
        await session.commit()
    statements = []
    event.listen(engine.sync_engine, 'before_cursor_execute',
                 lambda conn, cursor, statement, parameters, context, many: statements.append(statement))
    query = {'projection': 'binder_navigation', 'scientific_family_job_id': 'child_a',
             'include_children': 'true', 'limit': 1}
    ids = []
    for offset in range(3):
        response = await client.get('/api/jobs', params={**query, 'offset': offset})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['total'] == 3
        ids.append(data['jobs'][0]['id'])
    assert ids == ['child_a', 'child_b', 'root']
    response = await client.get('/api/jobs/child_a?projection=binder_navigation')
    compact = response.json()
    assert response.status_code == 200, response.text
    assert set(compact) == {'id', 'name', 'model_id', 'status', 'created_at', 'mode', 'stage_family',
                            'parent_job_id', 'lineage_root_job_id', 'selection_source_job_id',
                            'source_stage_job_id', 'params', 'provenance'}
    assert compact['created_at'] == '2025-01-01T00:00:00Z'
    expected_component = {key: value for key, value in component.items() if key != 'extra'}
    assert compact['params'] == {key: ([expected_component, None, 4] if key == 'complex_components' else value)
                                  for key, value in params.items() if key != 'big_settings'}
    assert compact['provenance']['binder_round_step'] == {
        **{key: value for key, value in step.items() if key not in ('receipt', 'source_ref', 'input_components')},
        'source_ref': {'job_id': 'root', 'native_record_id': 't_candidate1'},
        'input_components': [expected_component]}
    assert 'drop' not in str(compact)
    assert all('designs' not in sql.lower() for sql in statements)
    assert all(', jobs.provenance,' not in sql for sql in statements)
    assert any('JSON_EXTRACT(jobs.provenance' in sql for sql in statements)
    async with factory() as session:
        child = await session.get(Job, 'child_a')
        child.status = 'failed'
        session.add(Job(id='late', name='late', model_id='bindcraft2', mode='campaign', params={'selection_source_job_id': 'child_a'}))
        await session.commit()
    fresh = (await client.get('/api/jobs', params={**query, 'limit': 100})).json()
    assert fresh['total'] == 4
    assert next(job for job in fresh['jobs'] if job['id'] == 'child_a')['status'] == 'failed'
    filtered = (await client.get('/api/jobs', params={**query, 'status': 'failed', 'q': 'child_a',
                                                    'model_id': 'bindcraft2'})).json()
    assert filtered['total'] == 1
    assert filtered['jobs'][0]['id'] == 'child_a'
    scoped = (await client.get('/api/jobs', params={**query, 'include_children': 'false', 'limit': 100})).json()
    assert {job['id'] for job in scoped['jobs']} == {'root', 'late'}
    assert (await client.get('/api/jobs/missing?projection=binder_navigation')).status_code == 404
    for query_string in ('', '?summary=true'):
        ordinary = await client.get('/api/jobs' + query_string)
        assert ordinary.status_code == 200, ordinary.text
        assert 'design_count' in ordinary.json()['jobs'][0]
    ordinary = await client.get('/api/jobs/root')
    assert ordinary.status_code == 200, ordinary.text
    assert 'huge_receipt' in ordinary.json()['provenance']


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, False, 42, 'bad', [], {'binder_round_step': None},
                                   {'binder_round_step': {'source_ref': 7, 'input_components': False}}])
async def test_wrong_type_and_null_evidence_is_not_coerced(store, value):
    factory, client, _ = store
    async with factory() as session:
        session.add(Job(id='legacy', name='legacy', model_id='bindcraft2', mode='campaign', params=value, provenance=value))
        await session.commit()
    response = await client.get('/api/jobs/legacy?projection=binder_navigation')
    assert response.status_code == 200, response.text
    assert response.json()['provenance'] == value
    assert response.json()['params'] == ({} if isinstance(value, dict) else value)

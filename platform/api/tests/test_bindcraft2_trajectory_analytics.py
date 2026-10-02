"""Read-only analytical projections; fixtures do not claim native GPU execution."""
import copy
import csv
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs as jobs_router
from services.bindcraft2_native_results import NativeResultError, native_loss_trace
from services.bindcraft2_publication import PublicationError, publish_native_results
from services.bindcraft2_result_readback import read_bindcraft2_result_page, read_bindcraft2_trajectory
from tests.test_bindcraft2_publication import campaign, table


TRACE = b'phase,round,human_EGFR.iptm,human_EGFR.iptm_loss,hash,bindcraft_version\nscreen,1,,0.4,123,2\nscreen,2,0.25,0.3,123,2\nanneal,1,0.5,0.2,123,2\nanneal,2,nan,inf,123,2\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('zero', [True, False])
@pytest.mark.parametrize('trace_kind', ['present', 'missing', 'unsupported'])
async def test_verified_analytics_and_http_read_only(tmp_path, zero, trace_kind):
    root = tmp_path / 'campaign'
    campaign(root, zero=zero)
    table_path = root / '1_Trajectories/!_Trajectories.csv'
    rows = list(csv.DictReader(table_path.open()))
    rows[0].update(Timing='worker=0;start=123;design=12.5;compiled=1', terminated='anneal')
    table(table_path, rows)
    trace_path = root / '1_Trajectories/t/t_losses.csv'
    if trace_kind != 'missing':
        trace_path.parent.mkdir()
        trace_path.write_bytes(TRACE if trace_kind == 'present' else b'unknown\n123\n')
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc2', name='fixture', status='completed', model_id='bindcraft2',
                      mode='campaign', params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            assert await publish_native_results(job, root, session, commit=True) == (0 if zero else 1)
            provenance = copy.deepcopy(job.provenance)
            writes = []
            def record(_conn, _cursor, statement, *_args):
                if statement.lstrip().split()[0].upper() in ('INSERT', 'UPDATE', 'DELETE'):
                    writes.append(statement)
            event.listen(engine.sync_engine, 'before_cursor_execute', record)
            page = await read_bindcraft2_result_page(job, session, limit=1)
            assert page['analytics']['trajectory_count'] == 1
            assert page['analytics']['termination_counts'] == {'anneal': 1}
            assert page['analytics']['timing_seconds'] == {'samples': 1, 'median': 12.5, 'total': 12.5}
            assert page['analytics']['complete'] is (trace_kind == 'present')
            row = page['rows'][0]
            assert row['values']['Timing'] == rows[0]['Timing']
            assert row['analytics']['compiled'] is True
            assert row['outcome'] is None
            detail = await read_bindcraft2_trajectory(job, session, design='t', offset=1, limit=2)
            assert detail['available'] is (trace_kind == 'present')
            assert detail['warnings']
            if trace_kind == 'present':
                assert detail['total'] == 4
                assert detail['rows'] == [
                    {'phase': 'screen', 'round': 2.0, 'human_EGFR.iptm': 0.25, 'human_EGFR.iptm_loss': 0.3},
                    {'phase': 'anneal', 'round': 1.0, 'human_EGFR.iptm': 0.5, 'human_EGFR.iptm_loss': 0.2}]
                assert row['analytics']['phase_metrics']['screen']['human_EGFR.iptm'] == {
                    'first': None, 'last': 0.25, 'min': 0.25, 'max': 0.25, 'samples': 1}
                assert row['analytics']['phase_metrics']['anneal']['human_EGFR.iptm']['last'] is None
            else:
                assert detail['total'] == 0 and detail['rows'] == []
                assert row['analytics']['phase_metrics'] == {}
            empty = await read_bindcraft2_result_page(job, session, offset=1)
            assert empty['rows'] == [] and empty['analytics'] == page['analytics']
            assert (await read_bindcraft2_trajectory(job, session, design='t', offset=999))['rows'] == []
            for bounds in [{'limit': 0}, {'limit': 1001}, {'offset': -1}]:
                with pytest.raises(NativeResultError, match='bounds'):
                    await read_bindcraft2_trajectory(job, session, design='t', **bounds)
            for design in ['../t', 'T', 't_candidate2', 't_seq0']:
                with pytest.raises(NativeResultError, match='unknown native trajectory'):
                    await read_bindcraft2_trajectory(job, session, design=design)
            for stage in ['retained', 'draw', 'attempt', 'document']:
                raw = await read_bindcraft2_result_page(job, session, stage=stage)
                assert 'analytics' not in raw
            async def override():
                yield session
            app = FastAPI()
            app.dependency_overrides[jobs_router.get_session] = override
            app.include_router(jobs_router.router, prefix='/api/jobs')
            with TestClient(app) as client:
                url = '/api/jobs/bc2/bindcraft2-results/trajectory'
                response = client.get(url, params={'design': 't', 'offset': 1, 'limit': 2})
                assert response.status_code == 200, response.text
                assert response.json() == detail
                for query in [{'design': 't', 'limit': 1001}, {'design': 't', 'offset': -1}, {}]:
                    assert client.get(url, params=query).status_code == 422
                assert client.get(url, params={'design': '../t'}).status_code == 409
                assert client.get(url.replace('bc2/', 'absent/'), params={'design': 't'}).status_code == 404
            assert job.provenance == provenance
            assert not session.dirty and not writes
            json.dumps(page, allow_nan=False)
            if trace_kind == 'present':
                trace_path.write_bytes(TRACE + b'anneal,3,0.9,0.1,123,2\n')
                with pytest.raises(PublicationError, match='bytes'):
                    await read_bindcraft2_trajectory(job, session, design='t')
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_exact_arm_identity(tmp_path):
    root = tmp_path / 'sweep'
    for arm, value in [('alpha', '0.1'), ('alpha_extra', '0.9')]:
        campaign(root / arm, zero=True)
        trace = root / arm / '1_Trajectories/t/t_losses.csv'
        trace.parent.mkdir()
        trace.write_text(f'phase,round,target.iptm\nanneal,1,{value}\n')
    table(root / 'sweep.csv', [{'arm': 'alpha'}, {'arm': 'alpha_extra'}])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = Job(id='sweep', name='sweep', model_id='bindcraft2', mode='campaign', status='completed', output_dir=str(root), params={})
            session.add(job)
            await session.flush()
            await publish_native_results(job, root, session, commit=True)
            first = await read_bindcraft2_trajectory(job, session, design='t')
            second = await read_bindcraft2_trajectory(job, session, design='t', arm='alpha_extra')
            assert first['arm'] == 'alpha' and first['rows'][0]['target.iptm'] == 0.1
            assert second['arm'] == 'alpha_extra' and second['rows'][0]['target.iptm'] == 0.9
            with pytest.raises(NativeResultError, match='unknown native arm'):
                await read_bindcraft2_trajectory(job, session, design='t', arm='alph')
    finally:
        await engine.dispose()


def test_nonfinite_and_unsupported_trace():
    rows, warnings = native_loss_trace(TRACE)
    assert rows[-1]['human_EGFR.iptm'] is None
    assert rows[-1]['human_EGFR.iptm_loss'] is None
    assert warnings
    assert all('hash' not in row and 'bindcraft_version' not in row for row in rows)
    json.dumps(rows, allow_nan=False)
    for data in [b'', b'phase,round,round\na,1,2', b'phase,round,x\na,1,2,3', b'\xff']:
        rows, warnings = native_loss_trace(data)
        assert not rows and warnings

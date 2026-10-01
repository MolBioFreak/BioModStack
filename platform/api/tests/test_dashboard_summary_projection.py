"""Actual mounted Jobs owner regression and reproducible inert cost fixture."""
from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job
from routers import jobs as owner


@pytest_asyncio.fixture
async def summary_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'summary.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    async def session_override():
        async with factory() as session:
            yield session
    app.dependency_overrides[owner.get_session] = session_override
    app.include_router(owner.router, prefix='/api/jobs')
    with TestClient(app) as client:
        yield engine, factory, client
    await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_summary_actual_router_cost(summary_store, monkeypatch):
    engine, factory, client = summary_store
    async with factory() as session:
        session.add_all([Job(id=f'cost-{i:04d}', name=f'job {i}', model_id='external_import',
            mode='structure_import', status='completed', params={'retained': 'x' * 2048},
            provenance={'retained': 'y' * 2048}, completed_stages=['import'],
            created_at=datetime(2026, 1, 1) + timedelta(seconds=i)) for i in range(1000)])
        await session.flush()
        session.add_all([Design(id=f'design-{i}', job_id=f'cost-{i % 1000:04d}',
                               name=f'design {i}', pdb_path=f'design-{i}.pdb') for i in range(20000)])
        await session.commit()
    sql = []
    elapsed_sql = []
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def before(conn, cursor, statement, params, context, many):
        context.summary_started = time.perf_counter()
        if statement.lstrip().upper().startswith('SELECT'):
            sql.append((statement, params))
    @event.listens_for(engine.sync_engine, 'after_cursor_execute')
    def after(conn, cursor, statement, params, context, many):
        elapsed_sql.append((time.perf_counter() - context.summary_started) * 1000)
    construction_counts = {}
    for class_name in ('JobResponse', 'JobSummaryResponse'):
        dto = getattr(owner, class_name, None)
        if dto is None:
            continue
        original_init = dto.__init__
        def counted_init(self, *args, _init=original_init, _name=class_name, **kwargs):
            construction_counts[_name] = construction_counts.get(_name, 0) + 1
            _init(self, *args, **kwargs)
        monkeypatch.setattr(dto, '__init__', counted_init)
    summary_computations = [0]
    original_summary = owner.JobResponse.result_summary.fget
    def counted_summary(self):
        summary_computations[0] += 1
        return original_summary(self)
    monkeypatch.setattr(owner.JobResponse, 'result_summary', property(counted_summary))
    params = {'summary': True, 'limit': 100}
    first = client.get('/api/jobs', params=params)
    first_construction_counts = dict(construction_counts)
    first_summary_computations = summary_computations[0]
    initial_sql = list(sql)
    assert first.status_code == 200 and first.json()['total'] == 1000
    assert len(first.json()['jobs']) == 100
    assert all(row['design_count'] == 20 for row in first.json()['jobs'])
    etag = first.headers['etag']
    plans = []
    async with engine.connect() as conn:
        for statement, bindings in initial_sql:
            rows = await conn.exec_driver_sql('EXPLAIN QUERY PLAN ' + statement, bindings)
            plans.append([list(row) for row in rows])
    timings = {}
    for conditional in (False, True):
        wall, cpu, sql_times = [], [], []
        for i in range(23):
            elapsed_sql.clear()
            start_wall, start_cpu = time.perf_counter(), time.process_time()
            response = client.get('/api/jobs', params=params,
                headers={'If-None-Match': etag} if conditional else {})
            end_cpu, end_wall = time.process_time(), time.perf_counter()
            assert response.status_code == (304 if conditional else 200)
            if conditional:
                assert response.content == b''
            if i >= 3:
                wall.append((end_wall - start_wall) * 1000)
                cpu.append((end_cpu - start_cpu) * 1000)
                sql_times.append(sum(elapsed_sql))
        timings['304' if conditional else '200'] = {
            'samples': len(wall), 'median_wall_ms': statistics.median(wall),
            'median_process_cpu_ms': statistics.median(cpu),
            'median_sql_execute_ms': statistics.median(sql_times),
            'wall_samples_ms': wall, 'cpu_samples_ms': cpu}
    metrics = {'fixture': {'jobs': 1000, 'designs': 20000, 'page': 100},
        'body_bytes': len(first.content), 'row_keys': sorted(first.json()['jobs'][0]),
        'first_request_dto_constructions': first_construction_counts,
        'first_request_result_summary_computations': first_summary_computations,
        'page_select_count': len(initial_sql),
        'timings': timings, 'page_selects': [{'sql': s, 'bindings': list(p)} for s, p in initial_sql],
        'query_plans': plans}
    destination = os.environ.get('DASH_SUMMARY_METRICS')
    if destination:
        Path(destination).write_text(json.dumps(metrics, indent=2))
    print(json.dumps({k: v for k, v in metrics.items() if k not in {'page_selects', 'query_plans'}}, indent=2))


@pytest.mark.asyncio
async def test_dashboard_summary_visible_mutations_and_detail(summary_store, monkeypatch):
    engine, factory, client = summary_store
    async with factory() as session:
        session.add_all([Job(id='parent', name='parent', status='completed', model_id='external_import',
            mode='structure_import', params={'false': False, 'zero': 0, 'null': None},
            provenance={'retained': 'science'}, stage_outputs={'import': ['a.pdb']},
            awaiting_payload={'zero': 0}, decision_history=[{'decision': 'keep'}],
            created_at=datetime(2026, 1, 2)), Job(id='child', name='child', status='completed',
            model_id='external_import', mode='structure_import', params={}, parent_job_id='parent',
            created_at=datetime(2026, 1, 1))])
        await session.flush()
        session.add(Design(id='child-design', job_id='child', name='child design', pdb_path='child.pdb'))
        await session.commit()
    params = {'summary': True, 'limit': 1}
    full_response_class = owner.JobResponse
    def forbid_full_construction(*args, **kwargs):
        raise AssertionError('summary must not construct the full scientific DTO')
    monkeypatch.setattr(owner, 'JobResponse', forbid_full_construction)
    response = client.get('/api/jobs', params=params)
    assert response.status_code == 200
    assert response.json()['jobs'][0]['design_count'] == 1
    assert 'params' not in response.json()['jobs'][0]
    assert 'result_summary' not in response.json()['jobs'][0]
    assert response.json()['jobs'][0]['awaiting_input'] is False
    async def assert_changed(mutate):
        nonlocal response
        async with factory() as session:
            job = await session.get(Job, 'parent')
            await mutate(session, job)
            await session.commit()
        new = client.get('/api/jobs', params=params, headers={'If-None-Match': response.headers['etag']})
        assert new.status_code == 200 and new.headers['etag'] != response.headers['etag']
        assert client.get('/api/jobs', params=params, headers={'If-None-Match': new.headers['etag']}).status_code == 304
        response = new
    for field, value in [('name', 'renamed'), ('status', 'running'), ('current_stage', 'import'),
                         ('completed_stages', ['import']), ('awaiting_input', True), ('error_message', 'visible')]:
        async def mutate(session, job, field=field, value=value):
            setattr(job, field, value)
        await assert_changed(mutate)
        assert response.json()['jobs'][0][field] == value
    async def add_design(session, job):
        session.add(Design(id='direct', job_id='parent', name='direct', pdb_path='direct.pdb'))
        session.add(Design(id='direct2', job_id='parent', name='direct2', pdb_path='direct2.pdb'))
    await assert_changed(add_design)
    assert response.json()['jobs'][0]['design_count'] == 2
    async def remove_direct(session, job):
        job.status = 'completed'
        await session.execute(delete(Design).where(Design.job_id == 'parent'))
        session.add(Design(id='child-design2', job_id='child', name='late child design', pdb_path='child2.pdb'))
    await assert_changed(remove_direct)
    assert response.json()['jobs'][0]['design_count'] == 2
    # No parent timestamp/status change: independent child publication alone
    # changes the exact displayed count and therefore its validator.
    async def publish_child(session, job):
        session.add(Design(id='child-design3', job_id='child', name='child 3', pdb_path='child3.pdb'))
    await assert_changed(publish_child)
    assert response.json()['jobs'][0]['design_count'] == 3
    # The scalar execution policy and observed stage evidence stay visible.
    async def change_policy(session, job):
        job.params = {**job.params, 'remote_result_policy': 'automatic'}
    await assert_changed(change_policy)
    async def change_stage_evidence(session, job):
        job.provenance = {**job.provenance, 'stage_terminal_states': {'later_native': {'status': 'failed'}}}
    await assert_changed(change_stage_evidence)
    for query in ({'offset': 1}, {'status': 'failed'}, {'q': 'absent'}, {'include_children': True}):
        new = client.get('/api/jobs', params={**params, **query}, headers={'If-None-Match': response.headers['etag']})
        assert new.status_code == 200
    monkeypatch.setattr(owner, 'JobResponse', full_response_class)
    full = client.get('/api/jobs/parent').json()
    legacy = client.get('/api/jobs', params={'summary': False}).json()['jobs'][0]
    for row in (full, legacy):
        assert row['params'] == {'false': False, 'zero': 0, 'null': None}
        assert row['provenance'] == {'retained': 'science', 'stage_terminal_states': {'later_native': {'status': 'failed'}}}
        assert row['stage_outputs'] == {'import': ['a.pdb']}
        assert row['awaiting_payload'] == {'zero': 0}
        assert row['decision_history'] == [{'decision': 'keep'}]
    async def remove_parent(session, job):
        await session.delete(job)
    await assert_changed(remove_parent)
    assert response.json() == {'jobs': [], 'total': 0}

"""Read-only projection with real SQLite joins, including pre-publication jobs."""
import copy

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import AnalysisRun, Base, Design, Job, get_session
from routers.designs import router


@pytest_asyncio.fixture
async def evidence(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "evidence.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        def job(id, stage=None, source='source-a', backbone='source-a', status='completed', target='open', root='root'):
            step = dict(schema_version=1, root_job_id=root, source_design_id=source,
                        source_job_id='root' if source.startswith('source') else 'designer',
                        backbone_design_id=backbone, stage=stage, target_state=target,
                        binder_chains=['original-binder'], target_chains=['original-target'])
            return Job(id=id, name='same name', model_id='protenix' if stage == 'prediction' else 'fampnn', mode='predict',
                       status=status, params={}, provenance={'binder_round_step': step} if stage else {})
        session.add_all([job('root'), job('designer', 'sequence_design'),
                        job('pred-1', 'prediction', 'sequence-1'),
                        job('pred-2', 'prediction', 'sequence-1', status='failed', target='closed'),
                        job('pred-3', 'prediction', 'sequence-2', status='queued'),
                        job('direct', 'prediction', 'source-b', 'source-b'),
                        job('foreign', 'prediction', root='other-root'),
                        job('unjoined')])
        for id, owner in [('source-a', 'root'), ('source-b', 'root'), ('sequence-1', 'designer'), ('sequence-2', 'designer'),
                          ('sample-1', 'pred-1'), ('sample-2', 'pred-1'), ('unjoined-sample', 'unjoined')]:
            session.add(Design(id=id, job_id=owner, name='same name', pdb_path='/unread/identical.pdb',
                               provenance={'candidate_key': id}, pae_matrix=[] if id == 'sample-1' else None))
        payload = {'pae_cutoff': 10, 'dist_cutoff': 15, 'pair_scores': [
            {'chain_1': 'X', 'chain_2': 'Y', 'ipsae': 0}, {'chain_1': 'Y', 'chain_2': 'X', 'ipsae': None}]}
        session.add(AnalysisRun(id='analysis', subject_kind='design', subject_id='sample-1', analysis_type='ipsae_interface',
                                status='completed', params_json={'pae_cutoff': 10}, params_hash='hash', input_signature='signature',
                                code_version='v1', cache_key='cache', result_inline_json=payload))
        await session.commit()
        statements = []
        event.listen(engine.sync_engine, 'before_cursor_execute', lambda conn, cursor, stmt, params, context, many: statements.append(stmt))
        app = FastAPI()
        app.include_router(router, prefix='/api/designs')
        async def dependency():
            yield session
        app.dependency_overrides[get_session] = dependency
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, session, statements, payload
    await engine.dispose()


@pytest.mark.asyncio
async def test_exact_sequence_sample_state_joins_and_no_writes(evidence):
    client, session, statements, payload = evidence
    before = copy.deepcopy((await session.get(Job, 'pred-2')).provenance)
    response = await client.get('/api/designs/by-job/root/binder-evidence?limit=1')
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data['schema_version'], data['offset'], data['limit'], data['total']) == (1, 0, 1, 2)
    row = data['records'][0]
    assert row['source_design_id'] == 'source-a'
    assert [seq['design_id'] for seq in row['sequences']] == ['sequence-1', 'sequence-2']
    first = row['sequences'][0]['predictions']
    assert [(p['job_id'], p['design_id'], p['target_state']) for p in first] == [
        ('pred-1', 'sample-1', 'open'), ('pred-1', 'sample-2', 'open'), ('pred-2', None, 'closed')]
    assert first[0]['ipsae'][0]['result'] == payload
    assert first[0]['pae_url'] == '/api/designs/sample-1/pae'
    assert first[1]['ipsae'] == [] and first[1]['pae_url'] == '/api/designs/sample-2/pae'
    assert first[2]['status'] == 'failed' and first[2]['design_url'] is None
    assert row['sequences'][1]['predictions'][0]['status'] == 'queued'
    assert (await session.get(Job, 'pred-2')).provenance == before
    assert all(stmt.lstrip().upper().startswith('SELECT') for stmt in statements)
    assert not session.dirty and not session.new


@pytest.mark.asyncio
async def test_sequence_bearing_source_has_no_fictitious_designer_and_pagination(evidence):
    client, *_ = evidence
    response = await client.get('/api/designs/by-job/root/binder-evidence?offset=1&limit=1')
    sequence = response.json()['records'][0]['sequences'][0]
    assert sequence['job_id'] == 'root' and sequence['design_id'] == 'source-b'
    assert sequence['predictions'][0]['job_id'] == 'direct'
    empty = await client.get('/api/designs/by-job/root/binder-evidence?offset=2&limit=1')
    assert empty.json()['records'] == [] and empty.json()['total'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['offset=-1', 'limit=0', 'limit=1001'])
async def test_bounded_pagination(evidence, query):
    client, *_ = evidence
    assert (await client.get(f'/api/designs/by-job/root/binder-evidence?{query}')).status_code == 422


@pytest.mark.asyncio
async def test_missing_job(evidence):
    client, *_ = evidence
    assert (await client.get('/api/designs/by-job/missing/binder-evidence')).status_code == 404

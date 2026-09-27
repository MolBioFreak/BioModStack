"""Scratch SQLite/ASGI Jobs review with real BC2 CPU/settings and plan compilers.

Source tables/state are explicit fixtures, not native inference evidence. Only
checkout identity/tool discovery are substituted; no worker/provider is started.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from component_runtime import SourceIdentity
from database import Base, ExecutionTarget, Job, get_session
from routers import jobs
from services import bindcraft2_launch as launch, nextflow
from services.remote_execution import bundle, executor
from test_bindcraft2_lifecycle import setup


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest_asyncio.fixture
async def remote_action(tmp_path, monkeypatch):
    if not launch.IMAGE.is_file():
        pytest.skip('installed pinned BC2 settings compiler image unavailable')
    setup(tmp_path, monkeypatch, launch._native_compile)
    import paths
    for owner in (paths, jobs):
        monkeypatch.setattr(owner, 'get_results_dir', lambda: tmp_path)
        monkeypatch.setattr(owner, 'get_data_root', lambda: tmp_path)
        monkeypatch.setattr(owner, 'get_inputs_dir', lambda: tmp_path)
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: (identity.revision, identity.tree))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    calls = []
    materialize = launch.materialize_native_action

    def counted(*args, **kwargs):
        calls.append(args)
        return materialize(*args, **kwargs)

    monkeypatch.setattr(launch, 'materialize_native_action', counted)

    async def forbidden(*args, **kwargs):
        pytest.fail('Job preparation/submit must not start a remote worker')

    monkeypatch.setattr(executor, 'launch_remote_job', forbidden)
    monkeypatch.setattr(executor, 'run_remote', forbidden)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "remote-actions.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Job(id='source', name='fixture source', model_id='bindcraft2', mode='campaign',
                        status='completed', params={}, output_dir=str(tmp_path / 'parent'),
                        lineage_root_job_id='scientific-root', execution_target_id='vast:old-offline'))
        session.add(ExecutionTarget(id='vast:fixture', provider='vast', provider_instance_id='fixture',
            active=True, state='ready', capabilities={'gpu_count': 1},
            provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
                'status': 'complete', 'present': True, 'running': True}}))
        await session.commit()

    async def session_dependency():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[jobs.get_experiment_session] = session_dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
            yield client, sessions, tmp_path, calls
    finally:
        await engine.dispose()


def action_request(mode='rank'):
    return {'name': 'native action', 'model_id': 'bindcraft2', 'mode': mode,
            'execution_target_id': 'vast:fixture',
            'params': {'bc2_source_job_id': 'source', 'bc2_action_options': {}}}


async def prepare(client, payload=None):
    response = await client.post('/api/jobs', json=payload or action_request(),
                                headers={'X-BMS-Skip-Launch-Context': '1'})
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'remote_prepared_job_review_required'
    request = detail['job_request']
    assert request['execution_plan_approval'] is None
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['rank', 'resume'])
async def test_remote_prepares_once_reviews_and_submits_exact_snapshot(remote_action, mode):
    client, sessions, root, calls = remote_action
    original = tree_bytes(root / 'parent')
    request = await prepare(client, action_request(mode))
    job_id, output = jobs._bc2_prepared_action_output(request['params'], mode)
    assert request['parent_job_id'] is None
    assert request['params']['lineage_root_job_id'] == 'scientific-root'
    snapshot = tree_bytes(output)
    assert snapshot['bindcraft2/campaign/.campaign_state.json'] == original['bindcraft2/campaign/.campaign_state.json']
    async with sessions() as session:
        assert list(await session.scalars(select(Job.id))) == ['source']
    assert len(calls) == 1

    # Unapproved prepared POST cannot queue, and does not materialize again.
    denied = await client.post('/api/jobs', json=request)
    assert denied.status_code == 409, denied.text
    assert 'explicit execution-plan preview approval' in denied.json()['detail']
    preview = await client.post('/api/jobs/execution-plan/preview', json=request)
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    assert reviewed['admissible'], reviewed['blockers']
    assert reviewed['plan']['native_parameters_json']['out_dir'] == str(output)
    assert reviewed['input_identities']
    assert len(calls) == 1
    assert tree_bytes(output) == snapshot

    # No access to original source files is needed after preparation. The source
    # Job remains the lineage authority even after its returned bytes move offline.
    (root / 'parent').rename(root / 'offline-parent')
    request['execution_plan_approval'] = reviewed['approval_digest']
    response = await client.post('/api/jobs', json=request)
    assert response.status_code == 201, response.text
    assert response.json()['id'] == job_id
    async with sessions() as session:
        child = await session.get(Job, job_id)
        assert child.status == 'queued'
        assert child.output_dir == str(output)
        assert child.parent_job_id is None
        assert child.lineage_root_job_id == 'scientific-root'
        assert child.selection_source_job_id == 'source'
        assert child.execution_target_id == 'vast:fixture'
        assert child.execution_source_revision == 'a' * 40
        assert child.execution_source_tree == 'b' * 40
        assert child.remote_attempt_id is None
        assert (child.vram_estimate_mb > 0) is (mode == 'resume')
        assert child.provenance['execution_plan_approval']['approval_digest'] == reviewed['approval_digest']
        invocation = nextflow.compile_job_nextflow_invocation(child, child.params, child.output_dir)
        assert invocation.native_parameters['bc2_compilation'] == request['params']['bc2_compilation']
        assert invocation.native_parameters['bc2_campaign_dir'] == str(output / 'bindcraft2')
        assert len(list(await session.scalars(select(Job.id)))) == 2
    assert len(calls) == 1
    assert tree_bytes(output) == snapshot
    assert tree_bytes(root / 'offline-parent') == original
    replay = await client.post('/api/jobs', json=request)
    assert replay.status_code == 409, replay.text
    assert 'already belongs to a Job' in replay.json()['detail']
    assert tree_bytes(output) == snapshot
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_prepared_paths_settings_and_approval_cannot_be_repurposed(remote_action):
    client, sessions, root, calls = remote_action
    request = await prepare(client)
    _, output = jobs._bc2_prepared_action_output(request['params'], 'rank')
    snapshot, parent = tree_bytes(output), tree_bytes(root / 'parent')
    for edits in (
        {'bc2_compilation': str(root / 'parent/bindcraft2/compilation.json')},
        {'bc2_campaign_dir': str(root / 'parent/bindcraft2')},
        {'bc2_action_options': {'top': 1}},
        {'bc2_source_job_id': 'missing-source'},
        {'bc2_effective_sha256': '0' * 64},
        {'resume_source_dir': str(root / 'parent')},
    ):
        changed = deepcopy(request)
        changed['params'].update(edits)
        response = await client.post('/api/jobs', json=changed)
        assert response.status_code in {404, 409, 422}, response.text
    stale = deepcopy(request)
    stale['execution_plan_approval'] = '0' * 64
    response = await client.post('/api/jobs', json=stale)
    assert response.status_code == 409, response.text
    assert 'approval is stale' in response.json()['detail']
    async with sessions() as session:
        assert list(await session.scalars(select(Job.id))) == ['source']
    assert tree_bytes(output) == snapshot
    assert tree_bytes(root / 'parent') == parent
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_native_source_and_target_checks_precede_materialization(remote_action):
    client, sessions, root, calls = remote_action
    for payload in (
        {**action_request(), 'execution_target_id': 'vast:missing'},
        {**action_request(), 'params': {'bc2_source_job_id': 'missing', 'bc2_action_options': {}}},
        {**action_request(), 'params': {'bc2_source_job_id': 'source', 'bc2_action_options': {'not_native': True}}},
    ):
        response = await client.post('/api/jobs', json=payload)
        assert response.status_code in {404, 422}, response.text
    assert calls == []
    async with sessions() as session:
        assert list(await session.scalars(select(Job.id))) == ['source']

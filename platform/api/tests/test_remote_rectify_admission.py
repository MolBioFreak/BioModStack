"""Mounted canonical admission with real compiler; no provider/worker traffic."""
from copy import deepcopy
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, Job, get_session
from routers import jobs
from tests.test_msa_bundle_integration import offline_bundle


def test_preparation_rebind_keeps_approved_scientific_identity(offline_bundle):
    from pathlib import Path
    from schemas import JobCreate
    from services import nextflow
    from services.model_msa_handoff import prepare_launch_msa
    _, job, _, _ = offline_bundle
    payload = JobCreate(name='prepared-identity', model_id=job.model_id, mode=job.mode, params=deepcopy(job.params))
    approved = jobs._execution_plan_preview(payload)
    assert approved['admissible']
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    invocation.materialize_inputs(Path(job.output_dir))
    supplied = prepare_launch_msa(job.model_id, {**job.params, **invocation.native_parameters},
                                  Path(job.output_dir) / 'prepared-msa')
    prepared = nextflow._bind_protenix_msa_transport(invocation, supplied)
    assert prepared.requested_json == invocation.requested_json
    assert prepared.effective_json == invocation.effective_json
    assert prepared.execution_plan.complete
    assert prepared.execution_plan.plan_sha256 != invocation.execution_plan.plan_sha256
    assert jobs._execution_plan_preview(payload)['approval_digest'] == approved['approval_digest']


@pytest_asyncio.fixture
async def admission(tmp_path, monkeypatch):
    from component_runtime import SourceIdentity
    from services.remote_execution import bundle
    # Frozen source identity is infrastructure, never a replacement compiler.
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: identity)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: (identity.revision, identity.tree))
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "admission.sqlite"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *_: None)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')
    from routers import molecular_dynamics
    app.include_router(molecular_dynamics.router)
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with factory() as session:
        for name in ('one', 'two'):
            session.add(ExecutionTarget(id='vast:' + name, provider='vast', provider_instance_id=name,
                active=True, state='ready', capabilities={'gpu_count': 1},
                provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
                    'status': 'complete', 'present': True, 'running': True}}))
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        yield client, factory
    await engine.dispose()


def request(model='boltz2', msa=False):
    return dict(name='admission', model_id=model, mode='predict', execution_target_id='vast:one',
        params={'sequence': 'ACDEFGHIKLMNPQRSTVWY', 'run_frustrampnn': False,
                ('protenix_use_msa' if model == 'protenix' else 'boltz_use_msa'): msa})


async def empty(factory):
    async with factory() as session:
        assert list((await session.scalars(select(Job))).all()) == []


@pytest.mark.asyncio
async def test_missing_stale_and_valid_direct_approval(admission):
    client, factory = admission
    payload = request()
    response = await client.post('/jobs', json=payload)
    assert response.status_code == 409, response.text
    await empty(factory)
    response = await client.post('/jobs/execution-plan/preview', json=payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['admissible'], preview['blockers']
    assert preview['plan']['requested_json'] == payload['params']
    await empty(factory)
    payload['execution_plan_approval'] = preview['approval_digest']
    for edited in ({**payload, 'execution_target_id': 'vast:two'},
                   {**payload, 'params': {**payload['params'], 'sequence': 'AAAAAAAA'}}):
        rejected = await client.post('/jobs', json=edited)
        assert rejected.status_code == 409, rejected.text
        await empty(factory)
    accepted = await client.post('/jobs', json=payload)
    assert accepted.status_code == 201, accepted.text
    async with factory() as session:
        row = (await session.scalars(select(Job))).one()
        assert row.status == 'queued'
        assert row.provenance['core_protein_requested_params'] == payload['params']
        assert row.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
        parent_id = row.id
    forged_child = await client.post('/jobs', json={**request(), 'parent_job_id': parent_id})
    assert forged_child.status_code == 409, forged_child.text


@pytest.mark.asyncio
async def test_declared_input_byte_change_invalidates_approval(admission):
    from paths import get_inputs_dir
    source = get_inputs_dir() / 'preview-input.a3m'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('>query\nACDEFGHIKLMNPQRSTVWY\n')
    client, factory = admission
    payload = request('protenix', True)
    payload['params']['msa_path'] = str(source)
    first = await client.post('/jobs/execution-plan/preview', json=payload)
    assert first.status_code == 200, first.text
    preview = first.json()
    assert preview['input_identities']
    source.write_text('>query\nACDEFGHIKLMNPQRSTVWY\n>hit\nACDEFGHIKLMNPQRSTVWY\n')
    response = await client.post('/jobs', json={**payload, 'execution_plan_approval': preview['approval_digest']})
    assert response.status_code == 409, response.text
    await empty(factory)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['boltz2', 'protenix'])
async def test_msa_preview_is_declaration_not_preparation(admission, monkeypatch, model):
    from services import model_msa_handoff
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only preview invoked MSA preparation')
    # Native compiler is deliberately not mocked.
    for name in dir(model_msa_handoff):
        if name.startswith('prepare_') and callable(getattr(model_msa_handoff, name)):
            monkeypatch.setattr(model_msa_handoff, name, forbidden)
    client, factory = admission
    response = await client.post('/jobs/execution-plan/preview', json=request(model, True))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['admissible'], preview['blockers']
    assert model + ':msa' in preview['deferred_preparation']
    assert preview['plan']['complete'] is False  # never relabel executable authority
    await empty(factory)


@pytest.mark.asyncio
async def test_extra_blocker_on_msa_service_remains_visible(admission, monkeypatch):
    from dataclasses import replace
    import model_registry
    from component_runtime import UnresolvedField
    original = model_registry.selected_execution_metadata
    def with_unsupported_metadata(*args, **kwargs):
        metadata = original(*args, **kwargs)
        return replace(metadata, blockers=metadata.blockers + (
            UnresolvedField('protenix:msa', 'effective_settings', 'future unsupported setting', 'Must remain blocked'),))
    monkeypatch.setattr(model_registry, 'selected_execution_metadata', with_unsupported_metadata)
    client, factory = admission
    response = await client.post('/jobs/execution-plan/preview', json=request('protenix', True))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert not preview['admissible']
    assert preview['blockers'][0]['reason'] == 'Must remain blocked'
    await empty(factory)


@pytest.mark.asyncio
async def test_unsupported_plan_and_forged_parent_do_not_enqueue(admission):
    client, factory = admission
    payload = request()
    payload['params'].pop('run_frustrampnn')
    preview = (await client.post('/jobs/execution-plan/preview', json=payload)).json()
    assert preview['admissible'] is False
    assert any(row['field'] == 'effective_settings' for row in preview['blockers'])
    response = await client.post('/jobs', json={**payload, 'execution_plan_approval': preview['approval_digest']})
    assert response.status_code == 422, response.text
    response = await client.post('/jobs', json={**request(), 'parent_job_id': 'forged'})
    assert response.status_code == 409, response.text
    await empty(factory)


@pytest.mark.asyncio
async def test_md_native_preview_binds_shared_plan_without_second_approval(admission, monkeypatch):
    from routers import molecular_dynamics as md
    from tests.test_md_typed_launch import _intent_payload
    from tests.test_md_job_v2_contract import _catalog
    from services.md import launch_contract
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    catalog = _catalog()
    monkeypatch.setattr(md, 'get_chemistry_catalog', lambda: catalog)
    monkeypatch.setattr(launch_contract, 'get_chemistry_catalog', lambda: catalog)
    view = catalog.view()
    profile = view.get_profile('gmx_amber99sb_ildn_tip3p_smoke_v1')
    client, factory = admission
    intent = {**_intent_payload(), 'name': 'typed-md-admission', 'execution_target_id': 'vast:one',
        'chemistry_profile_id': profile['id'], 'chemistry_profile_sha256': profile['profile_sha256'],
        'catalog_digest': view.catalog_digest}
    response = await client.post('/api/molecular-dynamics/launch-preview', json={
        'schema_version': 'bms.md.launch-preview-request.v1', 'intent': intent})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['execution_plan']['complete']
    await empty(factory)
    response = await client.post('/api/molecular-dynamics/launch', json={
        'schema_version': 'bms.md.launch-request.v1', 'intent': intent,
        'preview_digest': preview['preview_digest']})
    assert response.status_code == 201, response.text
    async with factory() as session:
        row = (await session.scalars(select(Job))).one()
        assert row.provenance['execution_plan_approval']['approval_digest'] == preview['preview_digest']
        from database import MdRun
        run = await session.get(MdRun, row.id)
        run.phase = 'failed'  # infrastructure failure before any replica exists
        row.status = 'failed'
        job_id, version = row.id, run.state_version
        await session.commit()
    replay = await client.post(f'/api/molecular-dynamics/runs/{job_id}/reorchestrate', json={
        'idempotency_key': 'admission-md-reorchestrate', 'expected_state_version': version})
    assert replay.status_code == 201, replay.text
    async with factory() as session:
        successor = await session.get(Job, replay.json()['new_job_id'])
        assert successor.parent_job_id is None
        assert successor.execution_target_id == intent['execution_target_id']
        assert successor.provenance['execution_plan_approval']['approval_digest'] == preview['preview_digest']


@pytest.mark.asyncio
async def test_changed_source_rejects_prior_preview(admission, monkeypatch):
    from component_runtime import SourceIdentity
    from services.remote_execution import bundle
    client, factory = admission
    payload = request()
    preview = (await client.post('/jobs/execution-plan/preview', json=payload)).json()
    payload['execution_plan_approval'] = preview['approval_digest']
    changed = SourceIdentity('c' * 40, 'd' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: changed)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: (changed.revision, changed.tree))
    response = await client.post('/jobs', json=payload)
    assert response.status_code == 409, response.text
    await empty(factory)

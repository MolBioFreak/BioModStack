"""Native Project authority fixtures: real SQLite/HTTP, no scientific execution."""
import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from experiment_models import ExperimentWorkflowPreparation, ExperimentLaunchContext
from experiment_services import validate_preparation_authority, ValidationFailure
from routers import project_manager as pm
from services.global_experiments.launch_contexts import validate_bound_job_request
from test_project_workflow_setups import setup_store, _project_payload


@pytest.fixture(autouse=True)
def policy_cache():
    from biomodstack_local_resources import applied_local_policy
    applied_local_policy.cache_clear()
    yield
    applied_local_policy.cache_clear()


@pytest_asyncio.fixture
async def native_core_store(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def native_http(setup_store, native_core_store, tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from molbio_ngs_models import MolBioNGSBase
    ngs_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ngs.db'}")
    async with ngs_engine.begin() as connection:
        await connection.run_sync(MolBioNGSBase.metadata.create_all)
    ngs_store = async_sessionmaker(ngs_engine, expire_on_commit=False)
    core_store = native_core_store
    app = FastAPI()
    from routers import projects
    app.include_router(projects.router)
    app.include_router(pm.router)
    from routers import ngs_molbio_n5
    app.include_router(ngs_molbio_n5.router)
    from services import ngs_molbio_n5 as resource_owner
    async def irrelevant_connector(*args, **kwargs):
        raise AssertionError('Native Protein must never consult the NGS connector or hierarchy')
    def irrelevant_source_audit(*args, **kwargs):
        raise AssertionError('Native Protein must never consult a frozen NGS source audit')
    monkeypatch.setattr(pm, 'exact_local_launch_authority', irrelevant_connector)
    monkeypatch.setattr(ngs_molbio_n5, 'require_domain_hierarchy', irrelevant_connector)
    monkeypatch.setattr(resource_owner, 'runtime_implementation_record', irrelevant_source_audit)

    @app.middleware('http')
    async def operator(request, call_next):
        request.state.authenticated_principal = {'id': 'operator', 'roles': ['operator']}
        return await call_next(request)

    async def store():
        async with setup_store() as session:
            yield session

    async def ngs_dependency():
        async with ngs_store() as session:
            yield session

    async def core_dependency():
        async with core_store() as session:
            yield session

    # All dependencies are scratch-only; native authority needs no NGS replica.
    app.dependency_overrides[pm.get_experiment_session] = store
    app.dependency_overrides[pm.get_core_session] = core_dependency
    app.dependency_overrides[pm.get_molbio_ngs_session] = ngs_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client
    finally:
        await ngs_engine.dispose()


async def prepared_setup(client, model):
    response = await client.post('/api/projects', json=_project_payload('Native authority'))
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    capability = ('protein.structure_prediction.esmfold2' if model == 'esmfold2'
                  else 'protein.native.bindcraft2.campaign')
    response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
        'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
        'global_experiment_id': None, 'experiment': {'name': 'Native', 'objective': 'Authority fixture'},
        'domain_kind': 'protein_in_silico', 'capability_id': capability,
    }, headers={'Idempotency-Key': 'create'})
    assert response.status_code == 201, response.text
    setup = response.json()
    path = f"/api/projects/{project_id}/workflow-setups/{setup['setup_context_id']}"
    if model == 'esmfold2':
        draft = {'sequence': 'MQIFVK'}
    else:
        draft = {'native_job_request': {
            'name': 'BC2 authority fixture', 'model_id': 'bindcraft2', 'mode': 'campaign',
            'params': {'bindcraft2_settings': {'modality': 'VHH', 'number_of_final_designs': 25,
                'max_trajectories': 100, 'kept_sequences': 1,
                'paratope_conformations': ['extended', 'folded_back'],
                'targets': [{'name': 'fixture', 'target_path': 'fixture.cif', 'chains': 'A',
                             'hotspots': 'A438,A440,A465,A469', 'objective': 'target'}]},
                'bc2_preview_digest': 'a' * 64},
            'execution_target_id': 'vast:53410018',
            'execution_policy': {'remote_result_policy': 'automatic'},
            'binder_round': {'schema_version': 2, 'enabled': False, 'sequence_design': [
                {'model_id': 'fampnn', 'params': {'seqs_per_design': 3, 'fampnn_temperature': 0.3}},
                {'model_id': 'caliby_binder', 'params': {'caliby_num_seqs_per_pdb': 1, 'caliby_temperature': 0.2}}],
                'prediction': [{'model_id': 'protenix', 'params': {'protenix_model_weights': 'protenix-v2'}}]},
        }}
    response = await client.put(path + '/draft', json={'expected_generation': 0, 'draft': draft},
                                headers={'Idempotency-Key': 'save'})
    assert response.status_code == 200, response.text
    assert (await client.get(path)).json()['draft'] == response.json()['draft']
    response = await client.post(path + '/prepare-launch', json={'expected_generation': 1},
                                 headers={'Idempotency-Key': 'prepare'})
    assert response.status_code == 200, response.text
    prepared = response.json()
    domain_path = (f"/api/projects/{project_id}/experiments/{setup['global_experiment_id']}"
                   f"/domains/{setup['domain_experiment_id']}")
    return project_id, setup, prepared, domain_path


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['esmfold2', 'bindcraft2'])
@pytest.mark.parametrize('handoff_first', [False, True])
async def test_native_setup_handoff_launch_and_bound_job(native_http, setup_store, model, handoff_first):
    client = native_http
    project_id, setup, prepared, domain_path = await prepared_setup(client, model)
    preparation_id = prepared['preparation_id']
    response = await client.get(domain_path + f'/preparations/{preparation_id}')
    assert response.status_code == 200, response.text
    expected = response.json()
    async with setup_store() as session:
        issued = await session.get(ExperimentLaunchContext, prepared['launch_context_id'])
        return_uri = issued.return_uri
    if handoff_first:
        response = await client.post(domain_path + f'/preparations/{preparation_id}/launch-contexts',
            json={'return_uri': return_uri}, headers={'Idempotency-Key': 'handoff'})
        assert response.status_code == 201, response.text
    response = await client.post(domain_path + '/run-groups', json={'preparation_launches': [
        {'preparation_id': preparation_id, 'launch_context_id': prepared['launch_context_id']}]},
        headers={'Idempotency-Key': 'run'})
    # Exercise the actual native HTTP launch and resource owner, without a
    # private-service fallback or an NGS source-audit escape/skip.
    assert response.status_code == 201, response.text
    group = response.json()
    readback = await client.get(domain_path + '/run-groups/' + group['run_group_id'])
    assert readback.status_code == 200 and readback.json() == group, readback.text
    listed = await client.get(domain_path + '/run-groups')
    assert listed.status_code == 200, listed.text
    assert [row['run_group_id'] for row in listed.json()['items']] == [group['run_group_id']]
    attempt_id = group['runs'][0]['attempts'][0]['attempt_id']
    for suffix in ['', '/logs', '/validations']:
        detail = await client.get(domain_path + '/attempts/' + attempt_id + suffix)
        assert detail.status_code == 200, detail.text
        assert detail.json()['attempt_id'] == attempt_id
    absent = await client.get(domain_path + '/attempts/' + attempt_id + '/validations/absent')
    assert absent.status_code == 404, absent.text
    audit = await client.get(domain_path + '/audit')
    assert audit.status_code == 200, audit.text
    async with setup_store() as session:
        preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
        await validate_preparation_authority(session, preparation)
        assert pm._preparation_document(preparation) == expected
        context = await session.get(ExperimentLaunchContext, prepared['launch_context_id'])
        assert context.state == 'reserved' and context.run_attempt_id
        scheduler = json.loads(preparation.scheduler_payload_json)
        from schemas import JobCreate
        from services.global_experiments.launch_contexts import validate_prepared_child_job_request
        pinned = (await pm._launch_context_document(session, context))['pinned_scheduler']
        if model == 'bindcraft2':
            assert await validate_prepared_child_job_request(session, context, JobCreate.model_validate(pinned))
            assert pinned['execution_target_id'] == 'vast:53410018'
            assert pinned['binder_round']['enabled'] is False
            lineup = pinned['binder_round']['sequence_design']
            assert [(row['model_id'], row['params'].get('seqs_per_design', row['params'].get('caliby_num_seqs_per_pdb'))) for row in lineup] == [('fampnn', 3), ('caliby_binder', 1)]
            assert [lineup[0]['params']['fampnn_temperature'], lineup[1]['params']['caliby_temperature']] == [0.3, 0.2]
            assert scheduler['params']['bindcraft2_settings']['number_of_final_designs'] == 25
            assert scheduler['params']['bindcraft2_settings']['max_trajectories'] == 100
        from services.ngs_molbio_n5 import resource_admission_handoff_for_attempt
        from component_runtime import SourceIdentity
        from paths import get_code_root
        handoff = await resource_admission_handoff_for_attempt(session,
            run_attempt_id=context.run_attempt_id,
            canonical_job_id=group['runs'][0]['attempts'][0]['canonical_job_id'])
        source = SourceIdentity.from_checkout(get_code_root())
        assert (handoff['source_revision'], handoff['source_tree']) == (source.revision, source.tree)
        params = await validate_bound_job_request(session, context, job_name=scheduler['name'],
            model_id=scheduler['model_id'], mode=scheduler['mode'], params=scheduler['params'], pinned_gpu=None)
        assert all(params[key] == value for key, value in scheduler['params'].items())


def test_connector_resource_source_keeps_existing_authority(monkeypatch):
    from services import ngs_molbio_n5 as owner
    def unavailable():
        raise owner.NgsMolBioRuntimeAuthorityError('existing frozen connector source is unavailable')
    monkeypatch.setattr(owner, 'runtime_implementation_record', unavailable)
    with pytest.raises(owner.ResourceAdmissionDenied) as error:
        owner._runtime_source_authority([])
    assert error.value.code == 'resource_source_revision_unavailable'
    # Committed native source metadata comes from the same existing owner used
    # by execution-plan compilation, not from invented fixture identifiers.
    from component_runtime import SourceIdentity
    from paths import get_code_root
    source = SourceIdentity.from_checkout(get_code_root())
    assert owner._runtime_source_authority([], source_identity=source) == (source.revision, source.tree)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['esmfold2', 'bindcraft2'])
async def test_native_plan_draft_publish_prepare_routes(native_http, setup_store, model):
    client = native_http
    project_id, setup, prepared, domain_path = await prepared_setup(client, model)
    from experiment_models import ExperimentAggregateHead, ExperimentWorkflowDraft
    from sqlalchemy import select
    async with setup_store() as session:
        plan = await session.get(ExperimentAggregateHead, prepared['diagnostics']['workflow_id'])
        domain = await session.get(ExperimentAggregateHead, setup['domain_experiment_id'])
        domain_revision_id = domain.current_revision_id
        generation = plan.head_generation
        draft = await session.scalar(select(ExperimentWorkflowDraft).where(
            ExperimentWorkflowDraft.workflow_id == plan.aggregate_id))
        draft_generation, payload = draft.generation, json.loads(draft.canonical_payload)
    if model == 'esmfold2':
        response = await client.post(domain_path + '/plans', json={
            'name': 'New native Plan', 'capability_id': 'protein.structure_prediction.esmfold2',
            'expected_domain_revision_id': domain_revision_id}, headers={'Idempotency-Key': 'new-plan'})
        assert response.status_code == 201, response.text
    payload['scheduler']['name'] = 'Renamed native Plan'
    plan_path = domain_path + '/plans/' + prepared['diagnostics']['workflow_id']
    response = await client.put(plan_path + '/draft', json={
        'expected_draft_generation': draft_generation, 'payload': payload})
    assert response.status_code == 200, response.text
    response = await client.post(plan_path + '/revisions', json={
        'expected_draft_generation': response.json()['generation'], 'expected_head_generation': generation,
        'change_summary': 'Same scientific settings'})
    assert response.status_code == 201, response.text
    revision_id = response.json()['revision_id']
    response = await client.post(plan_path + '/revisions/' + revision_id + '/preparations',
        json={'input_dataset_revision_ids': []}, headers={'Idempotency-Key': 'plan-prepare'})
    assert response.status_code == 201, response.text
    assert response.json()['normalized_request']['launch_authority']['schema'] == 'bms.protein-setup-launch-authority.v1'
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    scheduler = payload['scheduler']
    expected_params = normalize_job_request(JobCreate(name=scheduler['name'], model_id=scheduler['model_id'],
        mode=scheduler['mode'], params=scheduler['params'])).params
    assert response.json()['effective_settings'] == expected_params


@pytest.mark.asyncio
async def test_native_pending_run_cancel_uses_global_owner(native_http, setup_store):
    project_id, setup, prepared, domain_path = await prepared_setup(native_http, 'bindcraft2')
    from experiment_services import create_run_group
    async with setup_store() as session:
        group = await create_run_group(session, project_id, [prepared['preparation_id']],
            idempotency_key='pending-run', source_domain_id=setup['domain_experiment_id'],
            launch_context_ids={prepared['preparation_id']: prepared['launch_context_id']})
        group_id, generation = group.resource_id, group.generation
        from services.ngs_molbio_n5 import reserve_run_group
        await reserve_run_group(session, group_id=group_id,
            domain_id=setup['domain_experiment_id'], actor='operator')
        await session.commit()
    response = await native_http.post(domain_path + '/run-groups/' + group_id + '/cancel',
        json={'expected_run_group_generation': generation, 'reason': 'Fixture cancellation'},
        headers={'Idempotency-Key': 'cancel'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'applied'


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['project_revision_sha256', 'capability_contract_sha256', 'schema'])
async def test_native_authority_revalidates_pinned_proof_despite_domain_cache(native_http, setup_store, field):
    project_id, setup, prepared, _ = await prepared_setup(native_http, 'bindcraft2')
    async with setup_store() as session:
        preparation = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        from experiment_services import load_workflow_plan_authority
        authority, _ = await load_workflow_plan_authority(session, prepared['diagnostics']['workflow_id'])
        normalized = json.loads(preparation.normalized_request_json)
        original = normalized['launch_authority']
        proof = await pm._current_preparation_launch_authority(session, session,
            project_id=project_id, global_experiment_id=setup['global_experiment_id'],
            domain_id=setup['domain_experiment_id'], plan_authority=authority, preparation=preparation)
        assert proof == original
        normalized['launch_authority'] = {**original, field: 'tampered'}
        session.expunge(preparation)  # tamper only the detached test view, never the immutable DB row
        preparation.normalized_request_json = json.dumps(normalized)
        # A prefilled Domain-only cache must not bypass the pinned owner.
        with pytest.raises(ValidationFailure):
            await pm._current_preparation_launch_authority(session, session,
                project_id=project_id, global_experiment_id=setup['global_experiment_id'],
                domain_id=setup['domain_experiment_id'], plan_authority=authority,
                preparation=preparation, proof_cache={authority.expected_domain_revision_id: original})


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,schema', [
    ('ngs_molbio', 'bms.domain-experiment.v2'),
    ('ngs_molbio', 'bms.domain-experiment.v4'),
    ('protein_in_silico', 'bms.domain-experiment.v4'),
])
async def test_connector_domains_keep_existing_ngs_authority(setup_store, kind, schema, monkeypatch):
    # Dispatch-only negative fixture; never stands in for successful admission.
    from types import SimpleNamespace
    from experiment_models import ExperimentRevision
    async with setup_store() as session:
        async def revision(_model, _id):
            assert _model is ExperimentRevision
            return SimpleNamespace(canonical_payload=json.dumps({'schema': schema, 'domain_kind': kind}))
        monkeypatch.setattr(session, 'get', revision)
        calls = []
        async def existing_connector(*args, **kwargs):
            calls.append(kwargs)
            raise ValidationFailure('existing connector refusal')
        monkeypatch.setattr(pm, 'exact_local_launch_authority', existing_connector)
        authority = SimpleNamespace(expected_domain_revision_id='domain-revision', capability_contract_sha256='capability')
        with pytest.raises(ValidationFailure, match='existing connector refusal'):
            await pm._plan_launch_authority(session, session, project_id='project',
                global_experiment_id='experiment', domain_id='domain', plan_authority=authority)
        assert calls == [{'project_id': 'project', 'global_experiment_id': 'experiment',
                          'domain_id': 'domain', 'expected_domain_revision_id': 'domain-revision'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_native_terminal_state_is_not_gated_on_missing_accounting(
        native_http, native_core_store, setup_store, status):
    # Inert canonical Job fixtures, real durable reservation/binding/projection.
    from datetime import datetime, timezone
    from database import Job
    from sqlalchemy import select
    from experiment_models import ExperimentRunAttempt, ExperimentResourceAdmission
    from experiment_services import reconcile_run_group
    from services.global_experiments.launch_contexts import claim_launch_context, consume_launch_context
    project_id, setup, prepared, domain_path = await prepared_setup(native_http, 'bindcraft2')
    response = await native_http.post(domain_path + '/run-groups', json={
        'preparation_launches': [{'preparation_id': prepared['preparation_id'],
                                 'launch_context_id': prepared['launch_context_id']}]},
        headers={'Idempotency-Key': 'terminal-run'})
    assert response.status_code == 201, response.text
    group_doc = response.json()
    attempt_doc = group_doc['runs'][0]['attempts'][0]
    async with setup_store() as session, native_core_store() as core:
        preparation = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        scheduler = json.loads(preparation.scheduler_payload_json)
        job = Job(id=attempt_doc['canonical_job_id'], name=scheduler['name'],
            model_id=scheduler['model_id'], mode=scheduler['mode'], params=scheduler['params'],
            status=status, provenance={}, completed_at=datetime.now(timezone.utc),
            error_message='Inert canonical terminal fixture')
        core.add(job)
        await core.flush()
        context, token = await claim_launch_context(session, prepared['launch_context_id'])
        context, binding = await consume_launch_context(session,
            launch_context_id=context.launch_context_id, claim_token=token,
            canonical_job_id=job.id, canonical_batch_id=None)
        await pm._project_bound_job(session, core, context, job, binding)
        group = await reconcile_run_group(session, core, project_id, group_doc['run_group_id'])
        attempt = await session.get(ExperimentRunAttempt, attempt_doc['attempt_id'])
        assert group.state == attempt.state == status
        receipt = json.loads(attempt.terminal_receipt_json)
        assert receipt['terminal_state'] == receipt['status'] == status
        assert receipt['resource_usage_receipt_id'] is None
        assert receipt['resource_usage_receipt_sha256'] is None
        assert receipt['resource_usage_evidence']['core_status'] == status
        assert receipt['resource_usage_evidence']['state'] == 'producer_resource_evidence_pending'
        admission = await session.scalar(select(ExperimentResourceAdmission).where(
            ExperimentResourceAdmission.run_attempt_id == attempt.resource_id))
        assert admission.state == 'released'
        await session.commit()
        await core.commit()
        generation = group.generation
        again = await reconcile_run_group(session, core, project_id, group.resource_id)
        assert again.state == status and again.generation == generation

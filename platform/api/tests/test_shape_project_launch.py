"""Shape setup through canonical Project insertion, using two scratch stores.

No resource/source authority is replaced. In an unbound checkout the insertion
cases report the real reservation blocker and remain runnable after binding.
These are software transport fixtures, not scientific or worker execution.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import ExecutionTarget, Job, ShapeDesignRequest
from experiment_models import ExperimentLaunchContext, ExperimentRunAttempt, ExperimentWorkflowPreparation
from experiment_services import create_run_group, validate_preparation_authority
from routers import jobs, shape_blueprint
from schemas import JobCreate
from services import shape_requests, shape_resources
from services.global_experiments import workflow_setups
from services.global_experiments.launch_contexts import LaunchContextError, validate_bound_job_request
from services.protein_project_capabilities import SHAPE_SETUP_CAPABILITY_ID, protein_capability_inventory
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_project_workflow_setups import setup_store, _project  # noqa: F401
from test_shape_submission import CUBE_OBJ


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    import paths
    from biomodstack_local_resources import applied_local_policy
    from services import nextflow
    applied_local_policy.cache_clear()
    monkeypatch.setenv('BMS_HOME', str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv('BMS_SHAPE_BLUEPRINT_ENABLED', 'true')
    for module in (paths, nextflow, shape_blueprint):
        monkeypatch.setattr(module, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    yield
    applied_local_policy.cache_clear()


async def submitted(core, root, *, remote=False):
    geometry = await shape_resources.admit_obj_geometry(core, data_root=root,
        payload=CUBE_OBJ, filename='cube.obj', angstrom_per_unit=10.0)
    return shape_requests.SubmittedShapeRequest(
        client_request_id='aa56b5a3-7c52-4254-ae25-246274533c47', name='Shape Project',
        geometry_id=geometry.geometry_id, expected_geometry_sha256=geometry.geometry_sha256,
        expected_geometry_manifest_sha256=geometry.manifest['manifest_sha256'],
        expected_point_pool_sha256=geometry.point_pool_sha256, target_length=120,
        num_backbones=1, sequences_per_backbone=0, sequence_policy='skip', seed=0,
        execution_target_id='vast:shape-project-fixture' if remote else None)


async def prepare(core, factory, request):
    draft = {'modification_mode': 'shape_blueprint', 'shape_seed': 0,
             'shape_sequences_per_backbone': 0, 'expanded': False, 'nullable': None,
             'shape_sequence_settings_by_engine': {'proteinmpnn': {'omit_AAs': ''}},
             'shape_submitted_request': request.model_dump(mode='json')}
    async with factory() as db:
        project = await _project(db)
        project_id = project.id
        setup = await workflow_setups.create_workflow_setup(db, project_id=project_id,
            relationship_kind='primary', global_experiment_id=None, experiment_name='Shape',
            experiment_objective='Inspect Shape transport', domain_kind='protein_in_silico',
            capability_id=SHAPE_SETUP_CAPABILITY_ID, idempotency_key='create')
        assert setup['validation_state'] == 'incomplete'
        # Editor state alone must reopen, but is not a runnable preparation.
        from experiment_services import ValidationFailure
        with pytest.raises(ValidationFailure, match='open and ready'):
            await workflow_setups.prepare_workflow_setup_launch(db, core_session=core,
                project_id=project_id, setup_context_id=setup['setup_context_id'],
                expected_generation=0, idempotency_key='incomplete')
        saved = await workflow_setups.save_workflow_setup_draft(db, project_id=project_id,
            setup_context_id=setup['setup_context_id'], draft=draft,
            expected_generation=0, idempotency_key='save')
        assert saved['draft'] == draft and saved['validation_state'] == 'ready'
        await db.commit()
    async with factory() as db:
        reopened = await workflow_setups.get_workflow_setup(db, project_id=project_id,
            setup_context_id=setup['setup_context_id'])
        assert reopened['draft'] == draft
        response = await workflow_setups.prepare_workflow_setup_launch(db, core_session=core,
            project_id=project_id, setup_context_id=setup['setup_context_id'],
            expected_generation=1, idempotency_key='prepare')
        preparation = await db.get(ExperimentWorkflowPreparation, response['preparation_id'])
        await validate_preparation_authority(db, preparation)
        scheduler = json.loads(preparation.scheduler_payload_json)
        staged = await shape_requests.materialize_shape_request(core, data_root=Path(core.bind.url.database).parent,
                                                               submitted=request)
        expected = jobs.normalize_job_request(shape_requests.shape_job_request(staged, request))
        assert scheduler['params'] == {**expected.params,
            'workflow_adapter': 'bms.core-job.protein_modification_experimental.adapter.v1'}
        assert scheduler['name'] == request.name
        assert not {'editor_state', 'shape_submitted_request', 'expanded', 'nullable',
                    'shape_sequence_settings_by_engine'} & scheduler['params'].keys()
        group = await create_run_group(db, project_id, [preparation.resource_id], idempotency_key='run',
            launch_context_ids={preparation.resource_id: response['launch_context_id']})
        context = await db.get(ExperimentLaunchContext, response['launch_context_id'])
        attempt = await db.get(ExperimentRunAttempt, context.run_attempt_id)
        assert attempt.scheduler_job_id
        await core.commit()
        await db.commit()
        return {**response, 'group_id': group.resource_id, 'scheduler_job_id': attempt.scheduler_job_id,
                'request_id': staged.request_id, 'scheduler': scheduler}


async def reserve(db, prepared):
    from services.ngs_molbio_n5 import reserve_run_group, ResourceAdmissionDenied
    try:
        await reserve_run_group(db, group_id=prepared['group_id'],
            domain_id=prepared['domain_experiment_id'], actor='shape-project-test')
    except ResourceAdmissionDenied as exc:
        if exc.code == 'resource_source_revision_unavailable':
            pytest.skip(f'Real reservation blocked: {exc.code}: {exc}; canonical insertion NOT verified')
        raise
    await db.commit()


def contextual(request, context_id):
    # Validation, not model_copy: this must expose a missing typed dependency.
    return shape_requests.SubmittedShapeRequest.model_validate({
        **request.model_dump(mode='json'), 'launch_context_id': context_id})


@pytest.mark.asyncio
async def test_shape_setup_save_reopen_prepare_and_request_binding(admission, setup_store, tmp_path):
    assert SHAPE_SETUP_CAPABILITY_ID in {
        row['capability_id'] for row in protein_capability_inventory(project_ready_only=True)['capabilities']}
    request = await submitted(admission, tmp_path)
    prepared = await prepare(admission, setup_store, request)
    assert not list(await admission.scalars(select(Job)))
    async with setup_store() as db:
        context = await db.get(ExperimentLaunchContext, prepared['launch_context_id'])
        scheduler = prepared['scheduler']
        params = await validate_bound_job_request(db, context, job_name=request.name,
            model_id=scheduler['model_id'], mode=scheduler['mode'], params=scheduler['params'],
            pinned_gpu=None, attach_resource_authority=False)
        assert params == scheduler['params']
        # Shape owns materialization; the Project seam must accept its actual
        # JobCreate projection rather than a hand-built pinned scheduler payload.
        from services.global_experiments.launch_contexts import validate_prepared_child_job_request
        staged = await shape_requests.materialize_shape_request(admission, data_root=tmp_path, submitted=request)
        projected = shape_requests.shape_job_request(staged, request)
        projected.launch_context_id = context.launch_context_id
        assert await validate_prepared_child_job_request(db, context, projected)
        assert await validate_bound_job_request(db, context, job_name=projected.name,
            model_id=projected.model_id, mode=projected.mode, params=projected.params,
            pinned_gpu=None, attach_resource_authority=False) == scheduler['params']
        for field, value in [('shape_seed', 123), ('shape_request_sha256', '0' * 64),
                             ('workflow_adapter', 'bms.core-job.esmfold2.adapter.v1')]:
            altered = projected.model_copy(deep=True)
            altered.params[field] = value
            with pytest.raises(LaunchContextError):
                await validate_prepared_child_job_request(db, context, altered)
        with pytest.raises(LaunchContextError, match='does not match'):
            await validate_bound_job_request(db, context, job_name=request.name,
                model_id=scheduler['model_id'], mode=scheduler['mode'],
                params={**scheduler['params'], 'shape_seed': 123}, pinned_gpu=None,
                attach_resource_authority=False)
        replay = await workflow_setups.prepare_workflow_setup_launch(db, core_session=admission,
            project_id=prepared['project_id'], setup_context_id=prepared['setup_context_id'],
            expected_generation=1, idempotency_key='prepare')
        assert replay['launch_context_id'] == context.launch_context_id


@pytest.mark.asyncio
async def test_shape_context_is_carried_by_typed_job_projection(admission, setup_store, tmp_path):
    request = await submitted(admission, tmp_path)
    prepared = await prepare(admission, setup_store, request)
    bound = contextual(request, prepared['launch_context_id'])
    staged = await shape_requests.materialize_shape_request(admission, data_root=tmp_path, submitted=bound)
    job_request = shape_requests.shape_job_request(staged, bound)
    assert job_request.launch_context_id == prepared['launch_context_id']
    assert 'launch_context_id' not in (await admission.get(ShapeDesignRequest, staged.request_id)).request_spec


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', [False, True])
async def test_shape_reserved_identity_actual_insertion_and_crash_replay(
        admission, setup_store, tmp_path, monkeypatch, crash):
    request = await submitted(admission, tmp_path)
    prepared = await prepare(admission, setup_store, request)
    async with setup_store() as db:
        await reserve(db, prepared)
        bound = contextual(request, prepared['launch_context_id'])
        token = jobs.current_launch_context_id.set(bound.launch_context_id)
        try:
            if crash:
                canonical = jobs.create_job
                async def commit_then_crash(*args, **kwargs):
                    await canonical(*args, **kwargs)
                    raise RuntimeError('injected crash after canonical commit before Shape link')
                with monkeypatch.context() as fault:
                    fault.setattr(jobs, 'create_job', commit_then_crash)
                    with pytest.raises(RuntimeError, match='injected crash'):
                        await shape_blueprint.submit_shape_request(bound, BackgroundTasks(), admission, db)
                await admission.rollback()
                assert (await admission.get(ShapeDesignRequest, prepared['request_id'])).job_id is None
                assert await admission.get(Job, prepared['scheduler_job_id']) is not None
            result = await shape_blueprint.submit_shape_request(bound, BackgroundTasks(), admission, db)
            assert result['job_id'] == prepared['scheduler_job_id']
            assert result['reused'] is crash
            assert result['launch_context_id'] == bound.launch_context_id
            assert result['launch_context_binding'] and result['return_uri'] == prepared['return_uri']
            admission.expire_all()
            row = await admission.get(ShapeDesignRequest, prepared['request_id'])
            assert row.job_id == result['job_id']
            replay = await shape_blueprint.submit_shape_request(bound, BackgroundTasks(), admission, db)
            assert replay['reused'] is True and replay['job_id'] == result['job_id']
            assert len(list(await admission.scalars(select(Job)))) == 1
            reopened = await workflow_setups.get_workflow_setup(db, project_id=prepared['project_id'],
                setup_context_id=prepared['setup_context_id'])
            assert reopened['state'] == 'submitted'
        finally:
            jobs.current_launch_context_id.reset(token)


@pytest.mark.asyncio
async def test_shape_project_prepared_remote_review_and_actual_approval(admission, setup_store, tmp_path):
    # A persisted fixture destination, not a running worker or mocked resource admission.
    admission.add(ExecutionTarget(id='vast:shape-project-fixture', provider='vast',
        provider_instance_id='shape-project-fixture', active=True, state='ready', capabilities={'gpu_count': 1},
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}}))
    await admission.commit()
    request = await submitted(admission, tmp_path, remote=True)
    prepared = await prepare(admission, setup_store, request)
    bound = contextual(request, prepared['launch_context_id'])
    async with setup_store() as db:
        token = jobs.current_launch_context_id.set(bound.launch_context_id)
        try:
            with pytest.raises(HTTPException) as review:
                await shape_blueprint.submit_shape_request(bound, BackgroundTasks(), admission, db)
            assert review.value.status_code == 409
            assert review.value.detail['code'] == 'remote_prepared_job_review_required'
            retained = JobCreate.model_validate(review.value.detail['job_request'])
            assert retained.launch_context_id == bound.launch_context_id
            assert not list(await admission.scalars(select(Job)))
            stage = Path(retained.params['shape_request_path']).parent
            before = {p.name: p.read_bytes() for p in stage.iterdir()}
            await reserve(db, prepared)
            preview = await jobs.preview_job_execution_plan(retained, session=admission, experiment_session=db)
            assert preview['admissible'], preview['blockers']
            approved = bound.model_copy(update={'execution_plan_approval': preview['approval_digest']})
            result = await shape_blueprint.submit_shape_request(approved, BackgroundTasks(), admission, db)
            assert result['job_id'] == prepared['scheduler_job_id']
            job = await admission.get(Job, result['job_id'])
            assert job.execution_target_id == request.execution_target_id
            assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
            assert job.provenance['launch_context_id'] == bound.launch_context_id
            assert (await admission.get(ShapeDesignRequest, prepared['request_id'])).job_id == job.id
            assert before == {p.name: p.read_bytes() for p in stage.iterdir()}
            replay = await shape_blueprint.submit_shape_request(approved, BackgroundTasks(), admission, db)
            assert replay['reused'] and replay['job_id'] == result['job_id']
            assert len(list(await admission.scalars(select(Job)))) == 1
        finally:
            jobs.current_launch_context_id.reset(token)


@pytest.mark.asyncio
async def test_shape_project_http_preparation_uses_both_scratch_stores(admission, setup_store, tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from routers import project_manager, projects
    from test_project_workflow_setups import _project_payload

    request = await submitted(admission, tmp_path)
    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(project_manager.router)

    @app.middleware('http')
    async def authenticated_operator(http_request, call_next):
        http_request.state.authenticated_principal = {'id': 'operator', 'roles': ['operator']}
        return await call_next(http_request)

    async def core_dependency():
        yield admission

    async def experiment_dependency():
        async with setup_store() as db:
            yield db

    app.dependency_overrides[project_manager.get_core_session] = core_dependency
    app.dependency_overrides[project_manager.get_experiment_session] = experiment_dependency
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        # Use the real Project creator, which persists its authenticated owner.
        created = await client.post('/api/projects', json=_project_payload('Shape HTTP'))
        assert created.status_code == 201, created.text
        project_id = created.json()['id']
        setup_response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
            'schema': 'bms.project-workflow-setup.create.v1',
            'relationship_kind': 'primary', 'global_experiment_id': None,
            'experiment': {'name': 'Shape HTTP', 'objective': 'Verify preparation transport'},
            'domain_kind': 'protein_in_silico', 'capability_id': SHAPE_SETUP_CAPABILITY_ID,
        }, headers={'Idempotency-Key': 'http-create'})
        assert setup_response.status_code == 201, setup_response.text
        setup = setup_response.json()
        setup_path = f"/api/projects/{project_id}/workflow-setups/{setup['setup_context_id']}"
        saved = await client.put(f'{setup_path}/draft', json={
            'expected_generation': 0,
            'draft': {'modification_mode': 'shape_blueprint',
                      'shape_submitted_request': request.model_dump(mode='json')},
        }, headers={'Idempotency-Key': 'http-save'})
        assert saved.status_code == 200, saved.text
        response = await client.post(f'{setup_path}/prepare-launch', json={'expected_generation': 1},
                                     headers={'Idempotency-Key': 'http-prepare'})
        assert response.status_code == 200, response.text
        prepared = response.json()
        replay = await client.post(f'{setup_path}/prepare-launch', json={'expected_generation': 1},
                                   headers={'Idempotency-Key': 'http-prepare'})
        assert replay.status_code == 200, replay.text
        assert replay.json() == prepared

    async with setup_store() as db:
        preparation = await db.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        await validate_preparation_authority(db, preparation)
        scheduler = json.loads(preparation.scheduler_payload_json)
    admission.expire_all()
    row = await admission.get(ShapeDesignRequest, scheduler['params']['shape_request_id'])
    assert row is not None and row.job_id is None
    assert Path(scheduler['params']['shape_request_path']).is_file()
    assert not list(await admission.scalars(select(Job)))

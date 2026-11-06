"""Actual mounted native envelopes through private HTTP stores; NOT INFERENCE.

Set BMS_DENOVO_AUTHORING_ENVELOPES to the mounted author's capture directory.
Only the external execution boundary is inert; Project and Job owners are real.
"""
import copy
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from database import Job, ExecutionTarget
from experiment_models import ExperimentLaunchContext, ExperimentWorkflowPreparation
from routers import jobs, projects
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_denovo_project_http import app_for
from test_project_workflow_setups import setup_store, _project_payload  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa'])
@pytest.mark.parametrize('mode', ['generate', 'evaluate'])
async def test_mounted_native_project_http_lifecycle(admission, setup_store, tmp_path, monkeypatch, record_property, model, mode):
    capture_root = os.environ.get('BMS_DENOVO_AUTHORING_ENVELOPES')
    if not capture_root:
        pytest.skip('Requires actual mounted authoring capture directory; no synthesized replacement')
    draft = json.loads((Path(capture_root) / f'native-envelope-{model}-{mode}.json').read_text())
    original_request = copy.deepcopy(draft['native_job_request'])
    assert original_request['execution_target_id'] is None
    assert original_request['sequence_design'] is None
    assert original_request['model_id'] == model and original_request['mode'] == mode
    # A distinct inactive editor draft must persist but never enter the scheduler.
    draft['editor_state']['native_task_drafts']['inactive-project-case'] = {
        'enabled': False, 'seed': 0, 'nullable': None, 'rows': [], 'text': '',
        'execution_target_id': 'vast:inactive-not-selected'}
    import paths
    from services import nextflow
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: tmp_path)
    if model == 'rfd4_proteina' and mode == 'evaluate':
        # The actual mounted capture names a source slot but does not select
        # bytes. Complete that selection through the existing typed binding;
        # retain the captured native scientific request without alteration.
        import gzip
        import hashlib
        from routers import files
        from tests.test_binder_source_materialization import cif
        roots = {'inputs': tmp_path / 'inputs', 'bms_results': tmp_path / 'results'}
        for root in roots.values():
            root.mkdir(parents=True, exist_ok=True)
        for module in (paths, jobs, files):
            monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
        source = roots['inputs'] / 'project-source.cif.gz'
        source.write_bytes(gzip.compress(cif(models=(3, 7)).encode()))
        binding = {'path': 'inputs/project-source.cif.gz', 'model_number': 7,
                   'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        draft['native_job_request']['params']['native_sources'] = {'exact-retained': binding}
        original_request = copy.deepcopy(draft['native_job_request'])
        record_property('source_binding', 'captured exact-retained slot bound to scratch CIF MODEL 7; not inference')
    # Ambient remote inventory cannot replace the envelope's explicit Local.
    admission.add(ExecutionTarget(id='vast:inactive-not-selected', provider='vast',
        provider_instance_id='inactive-not-selected', active=True, state='ready',
        capabilities={'gpu_count': 1}, provider_metadata={}))
    await admission.commit()

    def application():
        # Existing factory helper expects an async context-manager session factory.
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def core_factory():
            yield admission
        app = app_for(setup_store, core_factory)
        app.include_router(projects.router)
        app.include_router(jobs.router, prefix='/api/jobs')
        @app.middleware('http')
        async def launch_transport(request, call_next):
            token = jobs.current_launch_context_id.set(request.headers.get('X-BMS-Launch-Context'))
            try:
                return await call_next(request)
            finally:
                jobs.current_launch_context_id.reset(token)
        return app

    async with AsyncClient(transport=ASGITransport(application()), base_url='http://private') as client:
        response = await client.post('/api/projects', json=_project_payload('Mounted native HTTP'))
        assert response.status_code == 201, response.text
        project_id = response.json()['id']
        response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
            'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
            'experiment': {'name': 'Native HTTP', 'objective': 'Preserve actual mounted transport'},
            'domain_kind': 'protein_in_silico', 'capability_id': f'protein.native.{model}.{mode}'},
            headers={'Idempotency-Key': 'create'})
        assert response.status_code == 201, response.text
        setup_id = response.json()['setup_context_id']
        url = f'/api/projects/{project_id}/workflow-setups/{setup_id}'
        for generation in (0, 1):
            response = await client.put(url + '/draft', json={'expected_generation': generation, 'draft': draft},
                headers={'Idempotency-Key': f'save-{generation}'})
            assert response.status_code == 200, response.text
            assert response.json()['generation'] == generation + 1
            assert response.json()['draft'] == {**draft['editor_state'], 'native_job_request': original_request}
        assert not list(await admission.scalars(select(Job)))
    record_property('save_twice', 'real HTTP committed generations 1 and 2')
    await setup_store.kw['bind'].dispose()
    # Dispose connections and recreate application; every HTTP call opens a new session.
    async with AsyncClient(transport=ASGITransport(application()), base_url='http://cold') as client:
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()['draft'] == {**draft['editor_state'], 'native_job_request': original_request}
        record_property('cold_reopen', 'exact native request and inactive draft read back after connection disposal')
        response = await client.post(url + '/prepare-launch', json={'expected_generation': 2},
            headers={'Idempotency-Key': 'prepare'})
        assert response.status_code == 200, response.text
        prepared = response.json()
        async with setup_store() as session:
            preparation = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
            scheduler = json.loads(preparation.scheduler_payload_json)
            from experiment_services import create_run_group
            await create_run_group(session, project_id, [preparation.resource_id],
                idempotency_key='run', launch_context_ids={preparation.resource_id: prepared['launch_context_id']})
            from experiment_services import load_workflow_plan_authority
            authority = await load_workflow_plan_authority(session, prepared['diagnostics']['workflow_id'])
            assert authority is not None
            assert authority[1]['capability']['result_adapter_ids'] == [f'bms.native-binder.{model}.adapter.v1']
            submission = copy.deepcopy(authority[1]['capability']['normalized_job_request'])
            submission['params']['workflow_adapter'] = authority[1]['capability']['workflow_adapter_id']
            await session.commit()
        assert scheduler['execution_target_id'] is None
        assert scheduler['execution_policy'] == original_request['execution_policy']
        assert not {'native_task_drafts', 'editor_state', 'inactive-project-case'} & scheduler['params'].keys()
        assert not list(await admission.scalars(select(Job)))
        record_property('prepare', 'real HTTP frozen native scheduler; explicit Local; no Job before insertion')
        # Submit the existing immutable Project projection, not a handwritten wire.
        submitted = await client.post('/api/jobs', json={**submission,
            'launch_context_id': prepared['launch_context_id']},
            headers={'X-BMS-Launch-Context': prepared['launch_context_id']})
        assert submitted.status_code == 201, submitted.text
        job_id = submitted.json()['id']
        assert submitted.json()['launch_context_binding']
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'submitted'
    admission.expire_all()
    row = await admission.get(Job, job_id)
    assert row.execution_target_id is None
    assert row.model_id == model and row.mode == mode
    assert len(list(await admission.scalars(select(Job)))) == 1
    async with setup_store() as session:
        context = await session.get(ExperimentLaunchContext, prepared['launch_context_id'])
        assert context.canonical_job_id == job_id
        from services.global_experiments.launch_contexts import validate_bound_job
        await validate_bound_job(session, context, row)
    if model == 'rfd4_proteina' and mode == 'evaluate':
        retained = row.params['native_sources']['exact-retained']
        assert retained['model_number'] == 7
        assert retained['expected_sha256'] == binding['expected_sha256']
        assert row.params['rfd4_input_closure'] == submission['params']['rfd4_input_closure']
        record_property('canonical_bind', 'real insertion and cold authority verification preserve CIF MODEL 7 custody')

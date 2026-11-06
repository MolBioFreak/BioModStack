"""Ordinary La evaluation route with private stores; no inference/GPU."""
import json
import os
from pathlib import Path
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import get_session
from experiment_database import get_experiment_session
from routers import jobs, files, models
from test_project_workflow_setups import setup_store
from test_project_generation_bound_jobs import isolated_core
from test_project_setup_job_preparation import isolate_applied_local_policy


def payload():
    return {'name': 'La evaluation', 'model_id': 'laproteina', 'mode': 'evaluate',
        'params': {'laproteina_evaluation_request': {'samples_slot': 'samples', 'config_slot': 'config',
            'job_id': 0, 'ncpus': 1, 'metrics': {'compute_designability': False, 'motif_rmsd_modes': []}},
            'native_sources': {'samples': {'kind': 'file', 'path': 'inputs/native'},
                               'config': {'kind': 'file', 'path': 'inputs/original.yaml'}}}}


@pytest.mark.asyncio
@pytest.mark.parametrize('native_exit', [0, 7])
async def test_http_normalize_prepare_portable(isolated_core, setup_store, isolate_applied_local_policy, tmp_path, monkeypatch, native_exit):
    import paths
    roots = {'inputs': tmp_path/'owned/inputs', 'bms_results': tmp_path/'owned/results'}
    for module in (paths, jobs, files):
        monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
    samples = roots['inputs']/'native'; samples.mkdir()
    (samples/'motif_info.csv').write_text('name,ancestry\noriginal,retained\n')
    (roots['inputs']/'original.yaml').write_text('generation:\n  metric:\n    compute_codesignability: true\n')
    app = FastAPI(); app.include_router(jobs.router, prefix='/jobs'); app.include_router(models.router, prefix='/models')
    async def core():
        async with isolated_core() as session: yield session
    async def project():
        async with setup_store() as session: yield session
    app.dependency_overrides[get_session] = core
    app.dependency_overrides[get_experiment_session] = project
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        discovery = await client.get('/models/laproteina/generation-settings?mode=evaluate')
        assert discovery.status_code == 200, discovery.text
        assert 'source_directory' not in discovery.json()['parameter_schema']['properties']
        authored = json.loads(Path(os.environ['BMS_LA_CAPTURE']).read_text()) if os.environ.get('BMS_LA_CAPTURE') else payload()
        response = await client.post('/jobs/execution-plan/preview', json=authored)
        assert response.status_code == 200, response.text
        document = response.json()
        retained = document['request']
        closure = Path(retained['params']['laproteina_evaluation_input_closure'])
        request = json.loads((closure/'request.json').read_text())
        assert (closure/request['source_directory']/'motif_info.csv').read_bytes() == (samples/'motif_info.csv').read_bytes()
        assert request['metrics'] == payload()['params']['laproteina_evaluation_request']['metrics']
        from scripts.lib.portable_inputs import discover_native_input_references
        refs = discover_native_input_references('laproteina', 'evaluate', retained['params'], (), output_dir=tmp_path, allowed_roots=roots.values())
        assert any(r['source_path'].endswith('motif_info.csv') for r in refs)
        (samples/'motif_info.csv').unlink(); samples.rmdir(); (roots['inputs']/'original.yaml').unlink()
        replay = await client.post('/jobs/execution-plan/preview', json=retained)
        assert replay.status_code == 200, replay.text
        assert replay.json()['request']['params'] == retained['params']
        assert document['admissible'], document['blockers']
        created = await client.post('/jobs', json=retained)
        assert created.status_code == 201, created.text
        job_id = created.json()['id']
        assert (await client.get('/jobs/'+job_id)).status_code == 200
        (tmp_path/'la-preview.json').write_text(json.dumps(document, indent=2))
    from test_denovo_project_http import app_for, envelope
    from test_project_workflow_setups import _project
    from experiment_services import add_audit_event
    from experiment_models import ExperimentWorkflowPreparation
    async with setup_store() as session:
        project_id = (await _project(session)).id
        add_audit_event(session, workspace_id=project_id, resource_id=project_id,
            event_type='workspace_owner_bound', generation=1, payload={'principal_id': 'operator'})
        await session.commit()
    retained['execution_policy'] = {'remote_result_policy': 'manual'}
    draft = envelope(retained)
    async with AsyncClient(transport=ASGITransport(app_for(setup_store, isolated_core)), base_url='http://fixture') as client:
        url = f'/api/projects/{project_id}/workflow-setups'
        made = await client.post(url, json={'schema': 'bms.project-workflow-setup.create.v1',
            'relationship_kind': 'primary', 'experiment': {'name': 'La', 'objective': 'Retained evaluation'},
            'domain_kind': 'protein_in_silico', 'capability_id': 'protein.native.laproteina.evaluate'}, headers={'Idempotency-Key': 'create'})
        assert made.status_code == 201, made.text
        url += '/' + made.json()['setup_context_id']
        saved = await client.put(url+'/draft', json={'expected_generation': 0, 'draft': draft}, headers={'Idempotency-Key': 'save'})
        assert saved.status_code == 200, saved.text
        template = await client.post('/api/user-templates', json={'name': 'La', 'model_id': 'laproteina', 'mode': 'evaluate', 'params': draft})
        assert template.status_code in (200, 201), template.text
        assert (await client.get('/api/user-templates/'+template.json()['id'])).json()['params'] == draft
        prepared = await client.post(url+'/prepare-launch', json={'expected_generation': 1}, headers={'Idempotency-Key': 'prepare'})
        assert prepared.status_code == 200, prepared.text
    async with setup_store() as session:
        row = await session.get(ExperimentWorkflowPreparation, prepared.json()['preparation_id'])
        scheduler = json.loads(row.scheduler_payload_json)
        assert scheduler['params']['laproteina_evaluation_request'] == retained['params']['laproteina_evaluation_request']
        assert scheduler['params']['native_sources'] == retained['params']['native_sources']
        from routers.jobs import normalize_job_request
        from schemas import JobCreate
        assert normalize_job_request(JobCreate(**{key: value for key, value in scheduler.items() if key != 'resources'})).params == scheduler['params']

    from database import Job
    from laproteina_inert_nextflow import execute_inert_evaluation
    from services.rfd4_proteina_results import publish_native_generation, read_published_generation_page
    async with isolated_core() as session:
        job = await session.get(Job, job_id)
        native = execute_inert_evaluation(closure, Path(job.output_dir), tmp_path, native_exit)
        await publish_native_generation(session, job, job.output_dir)
        await session.commit()
    async with isolated_core() as session:
        job = await session.get(Job, job_id)
        page = await read_published_generation_page(job, session)
        assert page['total'] == 0
        assert page['native_execution_status'] == ('failed' if native_exit else 'completed')
        assert page['publication']['contract'] == 'laproteina_evaluation_v1'
        manifest = json.loads((native/'manifest.json').read_text())
        assert manifest['scientific_metrics_available'] is False
        assert manifest['receipt']['native_execution'] is False
        assert (native/'evaluation_inert/original_config.yaml').read_bytes() == (closure/request['config_path']).read_bytes()
        await publish_native_generation(session, job, job.output_dir)
        await session.commit()




@pytest.mark.parametrize('bad', [
    {'source_directory': '/controller/forbidden'}, {'ncpus': False},
    {'metrics': {'compute_designability': 'false'}},
    {'metrics': {'codesignability_folding_models': ['chai1']}},
])
def test_public_request_typed_and_no_controller_paths(bad):
    from services.laproteina_evaluation import normalize_job_request
    request = payload()['params']['laproteina_evaluation_request']
    with pytest.raises(ValueError):
        normalize_job_request('evaluate', {**request, **bad})


def test_sparse_public_controls_inherit_and_preserve_explicit_values():
    from services.laproteina_evaluation import normalize_job_request
    from scripts.lib.laproteina_contract import EVALUATION_CONTROLS
    request = payload()['params']['laproteina_evaluation_request']
    request['metrics'] = {key: False if spec['type'] == 'boolean' else [] for key, spec in EVALUATION_CONTROLS.items()}
    assert normalize_job_request('evaluate', request) == request
    del request['metrics']
    assert 'metrics' not in normalize_job_request('evaluate', request)

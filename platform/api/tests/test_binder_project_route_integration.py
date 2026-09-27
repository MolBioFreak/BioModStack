"""Route-to-owner software fixtures; no model execution or release admission."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, BackgroundTasks, HTTPException
from sqlalchemy import select, func

from database import Job, Design, get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentLaunchContext, ExperimentWorkflowPreparation
from routers import jobs
from schemas import JobCreate
from test_project_manager_adapters import stores
from test_project_workflow_setups import setup_store
from test_project_normalized_child_requests import destination, requests
from test_generation_publication_integration import pp_output
from test_boltzgen_generation_launch import target
from test_project_workflow_setups import _project
from services.global_experiments import workflow_setups

MODES = [('boltzgen', 'protein_binder'), ('boltzgen', 'nanobody_binder'),
         ('boltzgen', 'peptide_binder'), ('ppiflow', 'protein_binder'),
         ('ppiflow', 'antibody_binder'), ('ppiflow', 'nanobody_binder')]


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', MODES)
@pytest.mark.parametrize('zero', [False, True])
async def test_generation_endpoint_native_page_and_zero_yield(stores, model, mode, zero):
    root, _, core = stores
    output = root / 'results' / 'generation'
    output.mkdir(parents=True)
    if model == 'ppiflow':
        from services.ppiflow_generation import publish_generation_results as publish
        from services.ppiflow_generation import read_published_generation_results as read
        pp_output(output, 0 if zero else 2)
        for path in (output / 'ppiflow_generation').rglob('*.json'):
            value = json.loads(path.read_text())
            value['mode'] = mode
            path.write_text(json.dumps(value))
        samples = output / 'ppiflow_generation' / 'samples.jsonl'
        if samples.exists():
            values = [dict(json.loads(line), mode=mode) for line in samples.read_text().splitlines()]
            samples.write_text('\n'.join(json.dumps(row) for row in values) + '\n')
    else:
        from services.boltzgen_candidate_publication import ingest as publish
        from services.boltzgen_candidate_publication import read_published_generation_results as read
        from test_boltzgen_candidate_accounting import published
        if zero:
            from filter_boltzgen import run_strict_filter
            target = output / 'collected/boltzgen_filtered'
            target.mkdir(parents=True)
            run_strict_filter(SimpleNamespace(pdbs=[], jsons=[], out_dir=str(target), filter_biased='false',
                metrics_override=None, additional_filters=None, size_buckets=None, boltzgen_min_plddt=None,
                boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=1, alpha=0))
        else:
            published(output)
    async with core() as db:
        job = Job(id='generation', name='generation', model_id=model, mode=mode,
                  output_dir=str(output), params={}, provenance={'core_protein_scientific_contract': 1}, status='completed')
        db.add(job)
        await db.flush()
        await publish(job, output, db, commit=False)
        await db.commit()
        expected = await read(job, db, offset=0, limit=1)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def dependency():
        async with core() as db:
            yield db
    app.dependency_overrides[get_session] = dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get('/api/jobs/generation/generation-results?offset=0&limit=1')
        assert result.status_code == 200, result.text
        assert result.json() == expected
        assert set(result.json()) >= {'receipt', 'records', 'publication', 'artifacts', 'total', 'offset', 'limit'}
        if zero:
            assert result.json()['total'] == 0 and result.json()['records'] == []
        for query in ['limit=0', 'limit=1001', 'offset=-1']:
            assert (await client.get('/api/jobs/generation/generation-results?' + query)).status_code == 422
        assert (await client.get('/api/jobs/missing/generation-results')).status_code == 404
        assert (await client.get('/api/jobs/generation/generation-results?offset=100&limit=1000')).json()['records'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', MODES)
async def test_native_project_editor_save_reopen_and_prepare(setup_store, target, model, mode):
    from services.protein_project_capabilities import protein_capability_inventory
    capability_id = f'protein.native.{model}.{mode}'
    assert capability_id in {row['capability_id'] for row in protein_capability_inventory(project_ready_only=True)['capabilities']}
    if model == 'boltzgen':
        params = {'target_pdb': str(target), 'alpha': 0, 'min_plddt': None, 'filter_biased': False}
    else:
        params = {'target_pdb': str(target), 'self_condition': False, 'samples_per_target': 2}
        if mode == 'protein_binder':
            params.update(binder_chain='B', dataset_seed=0, translation_corrupt=False)
        else:
            params.update(framework_pdb=str(target), antigen_chain='A', heavy_chain='H', specified_hotspots='A1')
            if mode == 'antibody_binder':
                params['light_chain'] = 'L'
    request = JobCreate(name='Native editor', model_id=model, mode=mode, params=params)
    draft = {'model_id': model, 'mode': mode, 'source_document': {'artifact_id': 'editor-only'},
             'collapsed': False, 'count': 0, 'nullable': None,
             'native_job_request': request.model_dump(mode='json')}
    async with setup_store() as db:
        project = await _project(db)
        setup = await workflow_setups.create_workflow_setup(db, project_id=project.id, relationship_kind='primary',
            global_experiment_id=None, experiment_name='Binder', experiment_objective='Design',
            domain_kind='protein_in_silico', capability_id=capability_id, idempotency_key='new')
        saved = await workflow_setups.save_workflow_setup_draft(db, project_id=project.id,
            setup_context_id=setup['setup_context_id'], draft=draft, expected_generation=0, idempotency_key='save')
        assert saved['draft'] == draft
        await db.commit()
    async with setup_store() as db:
        reopened = await workflow_setups.get_workflow_setup(db, project_id=project.id, setup_context_id=setup['setup_context_id'])
        assert reopened['draft'] == draft
        prepared = await workflow_setups.prepare_workflow_setup_launch(db, project_id=project.id,
            setup_context_id=setup['setup_context_id'], expected_generation=1, idempotency_key='prepare')
        row = await db.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        scheduler = json.loads(row.scheduler_payload_json)
        expected = jobs.normalize_job_request(request).params
        assert scheduler['params'] == {**expected, 'workflow_adapter': f'bms.core-job.{model}.adapter.v1'}
        assert scheduler['name'] == request.name
        assert not {'source_document', 'collapsed', 'native_job_request', 'editor_state'} & scheduler['params'].keys()
        from experiment_services import validate_preparation_authority
        await validate_preparation_authority(db, row)
        from routers.project_manager import _launch_context_document
        context = await db.get(ExperimentLaunchContext, prepared['launch_context_id'])
        pinned = (await _launch_context_document(db, context))['pinned_scheduler']
        bound = JobCreate.model_validate(pinned)
        assert bound.name == request.name and bound.params == scheduler['params']
        assert bound.launch_context_id == context.launch_context_id
        from services.global_experiments.launch_contexts import validate_prepared_child_job_request
        assert await validate_prepared_child_job_request(db, context, bound)


@pytest.mark.asyncio
async def test_selected_caller_retains_distinct_project_requests_for_review(setup_store):
    async with setup_store() as db:
        dest = await destination(db)
        await db.commit()
        children = requests()
        for item in children:
            item.execution_target_id = 'vast:fixture'
        # Real native normalization, Plan persistence, run group and context owner.
        # No successful resource authority is mocked; review does not reserve it.
        with pytest.raises(HTTPException) as raised:
            await jobs.submit_selected_child_jobs(children, BackgroundTasks(), None, db,
                destination_launch_context_id=dest['launch_context_id'], idempotency_key='retained',
                response_context={'operation': 'predict'})
        assert raised.value.status_code == 409
        retained = raised.value.detail['job_requests']
        assert len({item['launch_context_id'] for item in retained}) == 2
        assert all(item['parent_job_id'] == 'external-source' for item in retained)
        assert all(item['launch_context_id'] != dest['launch_context_id'] for item in retained)
        assert (await db.get(ExperimentLaunchContext, dest['launch_context_id'])).state == 'issued'
    async with setup_store() as db:
        with pytest.raises(HTTPException) as replay:
            await jobs.submit_selected_child_jobs(children, BackgroundTasks(), None, db,
                destination_launch_context_id=dest['launch_context_id'], idempotency_key='retained',
                response_context={'operation': 'predict'})
        assert replay.value.detail['job_requests'] == retained
        assert await db.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 3

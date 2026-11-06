"""Project native drafts traverse the real scratch Project/normalizer owners."""
import json
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from experiment_database import get_experiment_session
from experiment_models import ExperimentWorkflowPreparation
from routers.project_manager import router
from services.global_experiments import workflow_setups
from test_project_workflow_setups import setup_store, _project


@pytest.mark.asyncio
@pytest.mark.parametrize('generator', ['rfd3', 'laproteina', 'disco'])
async def test_native_generator_project_save_reopen_prepare(setup_store, generator):
    params = {'generator': generator, 'num_designs': 2, 'seed': 0}
    if generator == 'rfd3':
        params.update(min_length=100, max_length=100, dump_trajectories=False)
    else:
        params.update(backend=generator, design_task='unconditional', target_lengths='100')
    request = {'model_id': 'protein_modification_experimental', 'mode': 'de_novo_design',
               'name': 'Native Project fixture', 'params': params, 'execution_target_id': None}
    async with setup_store() as session:
        project = await _project(session); project_id = project.id
        setup = await workflow_setups.create_workflow_setup(session, project_id=project_id,
            relationship_kind='primary', global_experiment_id=None, experiment_name='Native',
            experiment_objective='Native request preservation', domain_kind='protein_in_silico',
            capability_id=f'protein.de_novo.{generator}', idempotency_key='create')
        setup_id = setup['setup_context_id']
        saved = await workflow_setups.save_workflow_setup_draft(session, project_id=project_id,
            setup_context_id=setup_id, expected_generation=0, idempotency_key='save',
            draft={'native_job_request': request, 'editor_state': {'generator': generator, 'seed': 0, 'enabled': False}})
        await session.commit()
    app = FastAPI(); app.include_router(router)
    async def session_override():
        async with setup_store() as session: yield session
    app.dependency_overrides[get_experiment_session] = session_override
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        reopened = await client.get(f'/api/projects/{project_id}/workflow-setups/{setup_id}')
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()['draft'] == saved['draft']
        assert reopened.json()['draft']['native_job_request'] == request
    async with setup_store() as session:
        prepared = await workflow_setups.prepare_workflow_setup_launch(session, project_id=project_id,
            setup_context_id=setup_id, expected_generation=1, idempotency_key='prepare')
        record = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        scheduler = json.loads(record.scheduler_payload_json)
        assert scheduler['model_id'] == request['model_id'] and scheduler['mode'] == request['mode']
        assert scheduler['params']['generator'] == generator
        assert scheduler['params']['seed'] == 0
        assert scheduler.get('execution_target_id') is None
        assert scheduler['params']['workflow_adapter'] == 'bms.core-job.protein_modification_experimental.adapter.v1'

import json

import httpx
import pytest
from fastapi import FastAPI

import experiment_migrations
from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_models import ExperimentAggregateHead, ExperimentRevision
from routers.project_manager import router, WorkflowSetupCreateRequest
from services.global_experiments import workflow_setups
from test_project_workflow_setups import _project


@pytest.mark.asyncio
@pytest.mark.parametrize('model,domain', [('family_msa_protein', 'protein_in_silico'), ('family_msa_nucleic', 'ngs_molbio')])
async def test_discover_create_save_and_reopen(tmp_path, model, domain):
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/family-msa-project-capabilities')
    assert response.status_code == 200
    item = next(c for c in response.json()['capabilities'] if c['capability_id'] == model)
    assert item['domain_kind'] == domain
    WorkflowSetupCreateRequest.model_validate({'schema': 'bms.project-workflow-setup.create.v1',
        'relationship_kind': 'primary', 'experiment': {'name': 'Family', 'objective': 'Compare'},
        'domain_kind': domain, 'capability_id': model})
    path = tmp_path / 'project.db'
    experiment_migrations.run_all(path)
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    try:
        async with create_experiment_session_factory(engine)() as session:
            project = await _project(session)
            project_id = project.id
            setup = await workflow_setups.create_workflow_setup(session, project_id=project_id,
                relationship_kind='primary', global_experiment_id=None, experiment_name='Family',
                experiment_objective='Compare', domain_kind=domain, capability_id=model, idempotency_key='create')
            assert setup['setup_destination'] == item['safe_setup_destination']
            await workflow_setups.save_workflow_setup_draft(session, project_id=project_id,
                setup_context_id=setup['setup_context_id'], draft={'editor_state': {'model_id': model, 'empty': ''}},
                expected_generation=0, idempotency_key='save')
            head = await session.get(ExperimentAggregateHead, setup['domain_experiment_id'])
            revision = await session.get(ExperimentRevision, head.current_revision_id)
            payload = json.loads(revision.canonical_payload)
            assert payload['domain_kind'] == domain
            assert payload['domain_payload']['schema'] == ('bms.ngs-molbio-experiment.v1' if domain == 'ngs_molbio' else 'bms.protein-in-silico-experiment.v1')
            await session.commit()
    finally:
        await engine.dispose()
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    try:
        async with create_experiment_session_factory(engine)() as session:
            reopened = await workflow_setups.get_workflow_setup(session, project_id=project_id, setup_context_id=setup['setup_context_id'])
            assert reopened['draft'] == {'model_id': model, 'empty': ''}
            assert reopened['setup_destination'] == item['safe_setup_destination']
            assert reopened['generation'] == 1
    finally:
        await engine.dispose()

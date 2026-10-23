"""Native authoring acceptance through real routers and migrated private stores.

Optional BMS_DENOVO_WIRE_CAPTURE replays a parent's exported authoring envelope.
No execution routes, providers, serializer doubles or scientific defaults.
"""
import copy
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import experiment_migrations
from database import get_session, UserTemplate
from experiment_database import get_experiment_session, create_experiment_engine, create_experiment_session_factory
from experiment_models import ExperimentWorkflowPreparation, ExperimentWorkflowSetupContext, ExperimentRunGroup
from routers.project_manager import router, get_core_session
from routers.user_templates import router as templates_router
from test_project_workflow_setups import _project


def request_for(case, tmp_path):
    if case in ('rfd3', 'laproteina', 'disco'):
        params = {'generator': case, 'num_designs': 2, 'seed': 0}
        if case == 'rfd3':
            params.update(min_length=100, max_length=100, dump_trajectories=False)
        else:
            params.update(backend=case, design_task='unconditional', target_lengths='100')
        model, mode, capability = 'protein_modification_experimental', 'de_novo_design', f'protein.de_novo.{case}'
    elif case == 'shape':
        model, mode = 'protein_modification_experimental', 'shape_blueprint'
        capability, params = f'protein.native.{model}.{mode}', {}
    elif case in ('native_local', 'validated_local'):
        source = tmp_path / 'source.pdb'
        source.write_text(
            'ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\n'
            'ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00 10.00           C\nEND\n')
        if case == 'native_local':
            model, mode, capability = 'protein_local_redesign', 'local_redesign', 'protein.de_novo.local_redesign'
            params = {'input_structure': str(source), 'redesign_mode': 'partial_diffusion',
                'design_chains': ['A'], 'redesign_ranges': 'A2', 'sequence_policy': 'preserve', 'seed': 0}
        else:
            model, mode = 'protein_modification_experimental', 'region_redesign'
            capability = f'protein.native.{model}.{mode}'
            params = {'input_pdb': str(source), 'design_chains': 'A', 'context_chains': None,
                'region_mode': 'manual_ranges', 'redesign_ranges': 'A2', 'sequence_redesign_ranges': 'A2',
                'seq_method': 'fampnn', 'structure_validators': ['esmfold2'], 'num_designs': 1,
                'seqs_per_design': 1, 'interactive_gating': False}
    else:
        model, mode = case.split(':')
        capability = f'protein.native.{model}.{mode}'
        if model == 'rfd4_proteina':
            native = {'input': {'kind': 'unconditional', 'name': 'sample',
                'chains': [{'chain_id': 'A', 'min_length': 100, 'max_length': 100}]},
                'settings': {'seed': 0, 'metrics_tags': []}}
            if mode == 'evaluate':
                native = {'input': {'kind': 'annotated', 'slots': ['exact-retained']},
                    'settings': {'verbose': False, 'metrics_tags': []}}
            params = {'rfd4_request': native}
        else:
            params = {'complexa_request': {'task': 'protein_binder', 'task_name': 'http-fixture',
                'operation': mode, 'seed': 0, 'reward': None,
                'evaluation': {'compute_binder_metrics': False}}}
    return capability, {'model_id': model, 'mode': mode, 'name': 'HTTP native', 'params': params,
        'execution_target_id': None, 'execution_policy': {'remote_result_policy': 'manual'},
        'pinned_gpu': 0}


CASES = ['rfd3', 'laproteina', 'disco', 'native_local', 'validated_local', 'shape'] + [f'{model}:{mode}'
    for model in ('rfd4_proteina', 'complexa') for mode in ('generate', 'evaluate')]


def app_for(project_sessions, core_sessions):
    app = FastAPI()
    @app.middleware('http')
    async def principal(request, call_next):
        request.state.authenticated_principal = {'subject': 'operator', 'roles': ['operator']}
        return await call_next(request)
    app.include_router(router)
    app.include_router(templates_router, prefix='/api/user-templates')
    async def projects():
        async with project_sessions() as session:
            yield session
    async def core():
        async with core_sessions() as session:
            yield session
    app.dependency_overrides[get_experiment_session] = projects
    app.dependency_overrides[get_core_session] = core
    app.dependency_overrides[get_session] = core
    return app


def envelope(request):
    return {'native_job_request': request, 'editor_state': {
        'model_id': request['model_id'], 'mode': request['mode'],
        'execution_target_id': None, 'execution_policy': request['execution_policy'], 'pinned_gpu': 0,
        'native_mode_drafts': {'inactive': {'seed': 0, 'enabled': False, 'optional': None, 'rows': [], 'sequence': ''}},
        'optional_sequence_stage': None}}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES)
async def test_native_http_save_second_save_cold_prepare(tmp_path, case, monkeypatch):
    import paths
    from services import rfd3_local_redesign
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(rfd3_local_redesign, 'get_data_root', lambda: tmp_path)
    capability, request = request_for(case, tmp_path)
    draft = envelope(request)
    capture = os.environ.get('BMS_DENOVO_WIRE_CAPTURE')
    if capture:
        captures = json.loads(Path(capture).read_text())
        draft = captures.get(case, draft)
        request = draft['native_job_request']
    project_url = f'sqlite+aiosqlite:///{tmp_path / "project.db"}'
    core_url = f'sqlite+aiosqlite:///{tmp_path / "core.db"}'
    experiment_migrations.run_all(tmp_path / 'project.db')
    project_engine = create_experiment_engine(project_url)
    core_engine = create_async_engine(core_url)
    async with core_engine.begin() as connection:
        from database import ShapeCadSource, ShapeDesignGeometry, ShapeDesignRequest
        for table in (UserTemplate, ShapeCadSource, ShapeDesignGeometry, ShapeDesignRequest):
            await connection.run_sync(table.__table__.create)
    sessions = create_experiment_session_factory(project_engine)
    core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
    if case == 'shape':
        from test_shape_project_launch import submitted
        async with core_sessions() as core:
            shape_request = await submitted(core, tmp_path)
            await core.commit()
        draft.pop('native_job_request')
        draft['shape_submitted_request'] = shape_request.model_dump(mode='json')
    request_key = 'shape_submitted_request' if case == 'shape' else 'native_job_request'
    saved_native = copy.deepcopy(draft[request_key])
    try:
        async with sessions() as session:
            project_id = (await _project(session)).id
            from experiment_services import add_audit_event
            add_audit_event(session, workspace_id=project_id, resource_id=project_id,
                event_type='workspace_owner_bound', generation=1, payload={'principal_id': 'operator'})
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app_for(sessions, core_sessions)), base_url='http://test') as client:
            root = f'/api/projects/{project_id}/workflow-setups'
            create_body = {'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
                'experiment': {'name': 'Native HTTP', 'objective': 'Exact persistence'},
                'domain_kind': 'protein_in_silico', 'capability_id': capability}
            created = await client.post(root, json=create_body, headers={'Idempotency-Key': 'create'})
            assert created.status_code == 201, created.text
            replay = await client.post(root, json=create_body, headers={'Idempotency-Key': 'create'})
            assert replay.json() == created.json()
            url = root + '/' + created.json()['setup_context_id']
            async with sessions() as session:
                initial = await session.get(ExperimentWorkflowSetupContext, created.json()['setup_context_id'])
                pinned = (initial.capability_contract_json, initial.capability_contract_sha256)
            for generation in (0, 1):
                if generation:
                    draft = copy.deepcopy(draft)
                    draft['editor_state'].setdefault('native_mode_drafts', {})['second_save'] = {'empty': '', 'disabled': False}
                body = {'expected_generation': generation, 'draft': draft}
                headers = {'Idempotency-Key': f'save-{generation}'}
                saved = await client.put(url + '/draft', json=body, headers=headers)
                assert saved.status_code == 200, saved.text
                replay = await client.put(url + '/draft', json=body, headers=headers)
                assert replay.json() == saved.json()
                fetched = await client.get(url)
                assert fetched.status_code == 200, fetched.text
                assert fetched.json()['draft'][request_key] == saved_native
                assert fetched.json()['draft'] == {**draft['editor_state'], request_key: saved_native}
                conflict = await client.put(url + '/draft', json={**body, 'draft': {}}, headers=headers)
                assert conflict.status_code == 409, conflict.text
            stale = await client.put(url + '/draft', json={'expected_generation': 0, 'draft': draft}, headers={'Idempotency-Key': 'stale'})
            assert stale.status_code == 409, stale.text
            # Templates store the exact same editor/native envelope, not a repaired projection.
            template = await client.post('/api/user-templates', json={'name': 'Native saved',
                'model_id': request['model_id'], 'mode': request['mode'], 'params': draft})
            assert template.status_code in (200, 201), template.text
            assert template.json()['params'] == draft
            template_id = template.json()['id']
            changed = copy.deepcopy(draft)
            changed['editor_state']['template_second_save'] = [False, 0, None, '', []]
            updated = await client.put('/api/user-templates/' + template_id, json={'params': changed})
            assert updated.status_code == 200, updated.text
            assert updated.json()['params'] == changed
        await project_engine.dispose()
        await core_engine.dispose()
        project_engine = create_experiment_engine(project_url)
        core_engine = create_async_engine(core_url)
        sessions = create_experiment_session_factory(project_engine)
        core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
        async with AsyncClient(transport=ASGITransport(app_for(sessions, core_sessions)), base_url='http://cold') as client:
            cold = await client.get(url)
            assert cold.status_code == 200, cold.text
            assert cold.json()['generation'] == 2
            assert cold.json()['draft'] == fetched.json()['draft']
            template = await client.get('/api/user-templates/' + template_id)
            assert template.status_code == 200, template.text
            assert template.json()['params'] == changed
            listing = await client.get('/api/user-templates', params={'model_id': request['model_id']})
            assert listing.status_code == 200, listing.text
            assert next(row for row in listing.json() if row['id'] == template_id)['params'] == changed
            prepared = await client.post(url + '/prepare-launch', json={'expected_generation': 2}, headers={'Idempotency-Key': 'prepare'})
            assert prepared.status_code == 200, prepared.text
            replay = await client.post(url + '/prepare-launch', json={'expected_generation': 2}, headers={'Idempotency-Key': 'prepare'})
            assert replay.json() == prepared.json()
        async with sessions() as session:
            row = await session.get(ExperimentWorkflowSetupContext, created.json()['setup_context_id'])
            assert json.loads(row.draft_json) == draft
            assert (row.capability_contract_json, row.capability_contract_sha256) == pinned
            preparation = await session.get(ExperimentWorkflowPreparation, prepared.json()['preparation_id'])
            scheduler = json.loads(preparation.scheduler_payload_json)
            assert scheduler['model_id'] == request['model_id']
            assert scheduler['mode'] == request['mode']
            assert scheduler['execution_target_id'] is None
            if case != 'shape':
                assert scheduler['execution_policy'] == request['execution_policy']
                if 'pinned_gpu' in request:
                    assert scheduler['resources']['pinned_gpu'] == request['pinned_gpu']
            else:
                from database import ShapeDesignRequest
                async with core_sessions() as core:
                    records = (await core.scalars(select(ShapeDesignRequest))).all()
                    assert len(records) == 1
                assert scheduler['params']['shape_seed'] == 0
                assert scheduler['params']['shape_sequences_per_backbone'] == 0
            assert not {'editor_state', 'native_mode_drafts', 'optional_sequence_stage'} & scheduler['params'].keys()
            for key, value in request['params'].items():
                if not isinstance(value, dict):
                    assert scheduler['params'][key] == value
                else:
                    for field, declared in value.items():
                        assert scheduler['params'][key][field] == declared
            assert await session.scalar(select(func.count()).select_from(ExperimentRunGroup)) == 0
        await project_engine.dispose()
        project_engine = create_experiment_engine(project_url)
        sessions = create_experiment_session_factory(project_engine)
        async with sessions() as session:
            reopened_preparation = await session.get(ExperimentWorkflowPreparation, prepared.json()['preparation_id'])
            assert json.loads(reopened_preparation.scheduler_payload_json) == scheduler
        async with AsyncClient(transport=ASGITransport(app_for(sessions, core_sessions)), base_url='http://prepared-cold') as client:
            final_read = await client.get(url)
            assert final_read.status_code == 200, final_read.text
            assert final_read.json()['draft'] == cold.json()['draft']
    finally:
        await project_engine.dispose()
        await core_engine.dispose()

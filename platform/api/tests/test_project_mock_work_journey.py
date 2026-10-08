"""User-requested smoke journey: real HTTP handlers and stores, synthetic work only.

The only substitutions are isolated database dependencies and an explicitly named
local test principal. No production handler, authority, receipt, or response is mocked.
No job submission endpoint, runtime worker, hardware, or network service is started.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import perf_counter

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base as CoreBase, Job, get_session
from experiment_database import create_experiment_engine, create_experiment_session_factory, get_experiment_session
from experiment_migrations import run_all
from experiment_models import ExperimentRunGroup, ExperimentWorkflowRun, ExperimentWorkflowPreparation
from molbio_ngs_database import create_molbio_ngs_engine, create_molbio_ngs_session_factory, get_molbio_ngs_session
from molbio_ngs_migrations import run_all as migrate_domain
from routers import projects, project_manager

OUT = Path('/home/dalab/audits/project-management-debloat/mock-work-journey')


@pytest.mark.asyncio
async def test_real_project_with_mock_work_can_be_created_edited_and_reopened(tmp_path):
    OUT.mkdir(parents=True, exist_ok=True)
    experiment_db, core_db, domain_db = [tmp_path / name for name in ('experiments.db', 'core.db', 'domain.db')]
    run_all(experiment_db)
    migrate_domain(domain_db)
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{experiment_db}')
    factory = create_experiment_session_factory(engine)
    core_engine = create_async_engine(f'sqlite+aiosqlite:///{core_db}')
    async with core_engine.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    domain_engine = create_molbio_ngs_engine(f'sqlite+aiosqlite:///{domain_db}')
    domain_factory = create_molbio_ngs_session_factory(domain_engine)

    app = FastAPI()

    @app.middleware('http')
    async def named_test_identity(request, call_next):
        request.state.authenticated_principal = {'id': request.headers.get('x-test-actor', 'hermes-smoke-operator'), 'roles': ['operator']}
        return await call_next(request)

    app.include_router(projects.router)
    app.include_router(project_manager.router)

    def sessions(session_factory):
        async def dependency():
            async with session_factory() as session:
                yield session
        return dependency

    app.dependency_overrides[get_experiment_session] = sessions(factory)
    app.dependency_overrides[get_session] = sessions(core_factory)
    app.dependency_overrides[get_molbio_ngs_session] = sessions(domain_factory)
    trace = []
    ids = {}
    counts = {}

    async def send(client, name, method, path, body=None, *, expected=(200,), key=None, actor=None):
        headers = {}
        if key:
            headers['Idempotency-Key'] = key
        if actor:
            headers['x-test-actor'] = actor
        started = perf_counter()
        response = await client.request(method, path, json=body, headers=headers)
        try:
            value = response.json()
        except ValueError:
            value = response.text
        trace.append({'step': name, 'method': method, 'path': path, 'request': body, 'http_status': response.status_code, 'seconds': round(perf_counter() - started, 4), 'response': value})
        (OUT / 'http-trace.json').write_text(json.dumps(trace, indent=2) + '\n')
        assert response.status_code in expected, f'{name}: {response.status_code}: {response.text}'
        return value

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://isolated-test') as client:
            project = await send(client, 'Create disposable project', 'POST', '/api/projects', {'schema': 'bms.project.v2', 'project_scope': 'global', 'name': 'Hermes smoke test — mock work only', 'description': 'Disposable user-requested functional test. No scientific execution.', 'research_objective': 'Verify ordinary project organization and workflow setup.', 'tags': ['smoke-test', 'synthetic']}, expected=(201,))
            pid = ids['project_id'] = project['id']
            root = f'/api/projects/{pid}'
            project_note = await send(client, 'Add project note', 'POST', root + '/records', {'record_kind': 'note', 'body': 'Mock work: prepare a tiny example sequence and record a review note. This is not a scientific result.', 'author': 'hermes-smoke-operator'}, expected=(201,))
            ids['project_note_id'] = project_note['id']
            setup_body = {'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary', 'global_experiment_id': None, 'experiment': {'name': 'Tiny sequence — mock folding work', 'objective': 'Exercise setup and persistence only; do not execute a model.'}, 'domain_kind': 'protein_in_silico', 'capability_id': 'protein.structure_prediction.esmfold2'}
            setup = await send(client, 'Add real workflow setup with mock intent', 'POST', root + '/workflow-setups', setup_body, expected=(201,), key='smoke-create-primary')
            sid = ids['setup_context_id'] = setup['setup_context_id']
            gid = ids['global_experiment_id'] = setup['global_experiment_id']
            did = ids['domain_experiment_id'] = setup['domain_experiment_id']
            setup_path = root + f'/workflow-setups/{sid}'
            global_path = root + f'/experiments/{gid}'
            domain_path = global_path + f'/domains/{did}'
            replay = await send(client, 'Replay setup creation without duplication', 'POST', root + '/workflow-setups', setup_body, expected=(201,), key='smoke-create-primary')
            assert replay == setup
            domain = await send(client, 'Open generated Domain', 'GET', domain_path)
            assert domain['payload']['schema'] == 'bms.domain-experiment.v4'
            assert domain['payload']['domain_payload']['targets'] == []
            assert domain['payload']['domain_payload']['acceptance_criteria'] == []
            original = domain['payload']['domain_payload']
            domain = await send(client, 'Rename and tag generated Domain', 'PATCH', domain_path, {'expected_head_generation': domain['head_generation'], 'name': 'Reviewed mock folding work', 'tags': ['mock', 'reviewed']})
            assert domain['payload']['domain_payload'] == original
            assert domain['name'] == 'Reviewed mock folding work'
            await send(client, 'Reject stale edit', 'PATCH', domain_path, {'expected_head_generation': 0, 'name': 'Must not replace reviewed work'}, expected=(409,))
            observation = await send(client, 'Add Domain observation', 'POST', domain_path + '/records', {'record_kind': 'observation', 'body': 'Mock review complete: sequence is test input only; no folding was executed.', 'author': 'hermes-smoke-operator'}, expected=(201,))
            ids['domain_observation_id'] = observation['id']
            summary = await send(client, 'Read Project summary with added work', 'GET', root + f'/summary?focus_id={gid}&selected_node_key=domain_experiment:{did}')
            assert any(task['setup_context_id'] == sid for task in summary['tasks'])
            saved = await send(client, 'Save tiny sequence into workflow draft', 'PUT', setup_path + '/draft', {'expected_generation': setup['generation'], 'draft': {'sequence': 'MQIFVK'}}, key='smoke-save-draft')
            assert saved['draft']['sequence'] == 'MQIFVK'
            assert saved['validation_state'] == 'ready'
            prepared = await send(client, 'Prepare handoff without launching a job', 'POST', setup_path + '/prepare-launch', {'expected_generation': saved['generation']}, key='smoke-prepare')
            ids['preparation_id'] = prepared['preparation_id']
            ids['launch_context_id'] = prepared['launch_context_id']
            context = await send(client, 'Resolve actual issued handoff', 'GET', '/api/launch-contexts/' + prepared['launch_context_id'])
            assert context['project_id'] == pid and context['domain_experiment_id'] == did
            assert context['preparation_id'] == prepared['preparation_id']
            prepared_replay = await send(client, 'Replay preparation without duplication', 'POST', setup_path + '/prepare-launch', {'expected_generation': saved['generation']}, key='smoke-prepare')
            assert prepared_replay == prepared
            await send(client, 'Reject edits from another principal', 'PATCH', root, {'expected_head_generation': project['head_generation'], 'name': 'Unauthorized replacement'}, expected=(404,), actor='different-test-operator')
            domain = await send(client, 'Refresh exact Domain generation', 'GET', domain_path)
            archived = await send(client, 'Archive mock Domain', 'POST', domain_path + '/archive', {'expected_head_generation': domain['head_generation']})
            assert archived['lifecycle_state'] == 'archived'
            restored = await send(client, 'Restore mock Domain', 'POST', domain_path + '/restore', {'expected_head_generation': archived['head_generation']})
            assert restored['lifecycle_state'] == domain['lifecycle_state']
            assert restored['payload']['domain_payload'] == original

        async with httpx.AsyncClient(transport=transport, base_url='http://isolated-test') as fresh_client:
            reopened = await send(fresh_client, 'Reopen Project through a fresh client/session', 'GET', root)
            assert reopened['name'] == project['name']
            reopened_draft = await send(fresh_client, 'Reopen saved workflow', 'GET', setup_path)
            assert reopened_draft['draft']['sequence'] == 'MQIFVK'
            notes = await send(fresh_client, 'Read back Project note', 'GET', root + '/records')
            assert any(note['id'] == project_note['id'] for note in notes['items'])
            observations = await send(fresh_client, 'Read back Domain observation', 'GET', domain_path + '/records')
            assert any(note['id'] == observation['id'] for note in observations['items'])
            final_summary = await send(fresh_client, 'Reopen populated Project summary', 'GET', root + f'/summary?focus_id={gid}&selected_node_key=domain_experiment:{did}')
            assert final_summary['selection']['title'] == 'Reviewed mock folding work'
            assert final_summary['selection']['summary']['tags'] == ['mock', 'reviewed']
            for collection in (final_summary['tree']['nodes'], final_summary['map']['nodes']):
                assert next(node for node in collection if node['node_key'] == f'domain_experiment:{did}')['label'] == 'Reviewed mock folding work'

        async with factory() as session:
            for name, model in [('run_groups', ExperimentRunGroup), ('runs', ExperimentWorkflowRun), ('preparations', ExperimentWorkflowPreparation)]:
                counts[name] = await session.scalar(select(func.count()).select_from(model))
        async with core_factory() as session:
            counts['jobs'] = await session.scalar(select(func.count()).select_from(Job))
        assert counts == {'run_groups': 0, 'runs': 0, 'preparations': 1, 'jobs': 0}
        (OUT / 'result.json').write_text(json.dumps({'status': 'passed', 'project': ids, 'steps': len(trace), 'counts': counts, 'boundary': 'Real routes/services/migrations with isolated SQLite and named synthetic principal. No browser or live execution acceptance.'}, indent=2) + '\n')
    finally:
        (OUT / 'project-ids.json').write_text(json.dumps(ids, indent=2) + '\n')
        for original_db, destination in ((experiment_db, 'experiments.db'), (core_db, 'core.db'), (domain_db, 'domain.db')):
            with sqlite3.connect(original_db) as source, sqlite3.connect(OUT / destination) as backup:
                source.backup(backup)
        await engine.dispose()
        await core_engine.dispose()
        await domain_engine.dispose()

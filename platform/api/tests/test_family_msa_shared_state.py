import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_database import get_experiment_session
from experiment_models import ExperimentBase
from experiment_services import create_project, create_global_experiment, create_domain_experiment, add_audit_event
from routers.family_msa_results import router
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
async def test_shared_state_owner_conflicts_and_reopen(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('ngs_molbio'))
        add_audit_event(session, workspace_id=project.id, resource_id=project.id,
                        event_type='workspace_owner_bound', generation=project.head_generation,
                        payload={'principal_id': 'local-application-operator'})
        project_id, domain_id = project.id, domain.id
        await session.commit()
    async def dependency():
        async with sessions() as session:
            yield session
    monkeypatch.setenv('BMS_CM_TRUSTED_PROXY_SECRET', 'test-secret')
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_experiment_session] = dependency
    base = f'/api/family-msa/projects/{project_id}/shared-state'
    state = {'marks': [
        {'id': 'a', 'label': 'column', 'coordinate': {'scope': 'artifact_columns', 'artifact_id': 'art', 'start': 1, 'end': 2}},
        {'id': 'b', 'label': 'source', 'coordinate': {'scope': 'source_positions', 'source_member_id': 'm', 'occurrence_id': 'o', 'start': 3, 'end': 4}}],
        'views': [{'id': 'v', 'name': 'view', 'artifact_id': 'art', 'hidden_row_ids': ['row']}],
        'comparisons': [{'id': 'c', 'name': 'compare', 'left_artifact_id': 'art', 'right_artifact_id': 'art2', 'method': 'source', 'report': {'rows': []}}]}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            payload = {'name': 'Shared', 'domain_experiment_id': domain_id, 'state': state}
            assert (await client.post(base, json=payload)).status_code == 401
            client.headers['X-BMS-CM-Proxy-Secret'] = 'test-secret'
            created = await client.post(base, json=payload)
            assert created.status_code == 201, created.text
            original = created.json(); url = base + '/' + original['id']
            client.headers.pop('X-BMS-CM-Proxy-Secret')
            assert (await client.get(url)).status_code == 401
            client.headers['X-BMS-CM-Proxy-Secret'] = 'test-secret'
            assert (await client.get(f'/api/family-msa/projects/not-the-project/shared-state/{original["id"]}')).status_code == 404
            changed = dict(state, views=[])
            saved = await client.put(url, json={'expected_head_generation': original['head_generation'], 'state': changed})
            assert saved.status_code == 200, saved.text
            stale = await client.put(url, json={'expected_head_generation': original['head_generation'], 'state': state})
            assert stale.status_code == 409, stale.text
            reopened = await client.get(url)
            assert reopened.json()['state']['views'] == []
            old = await client.get(url, params={'revision_id': original['revision_id']})
            assert old.json()['state'] == original['state']
            assert reopened.json()['state']['marks'][1]['coordinate']['scope'] == 'source_positions'
            assert reopened.json()['state']['comparisons'][0]['report'] == {'rows': []}
            bad = dict(state, marks=[{'id': 'x', 'label': '', 'coordinate': {'scope': 'source_positions', 'artifact_id': 'art', 'start': 0, 'end': 1}}])
            assert (await client.put(url, json={'expected_head_generation': saved.json()['head_generation'], 'state': bad})).status_code == 422
    finally:
        await engine.dispose()

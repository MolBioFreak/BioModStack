"""One-shot ASGI bridge for mounted tests; private migrated stores, no listener/compute."""
import asyncio
import json
import os
from pathlib import Path
import sys

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import experiment_migrations
from database import UserTemplate
from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_services import add_audit_event
from test_denovo_project_http import app_for
from test_project_workflow_setups import _project


async def main(request):
    home = Path(os.environ['BMS_OPERATIONS_HTTP_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    experiment_migrations.run_all(home / 'projects.db')
    project_engine = create_experiment_engine(f'sqlite+aiosqlite:///{home / "projects.db"}')
    core_engine = create_async_engine(f'sqlite+aiosqlite:///{home / "core.db"}')
    async with core_engine.begin() as connection:
        await connection.run_sync(UserTemplate.__table__.create, checkfirst=True)
    sessions = create_experiment_session_factory(project_engine)
    core = async_sessionmaker(core_engine, expire_on_commit=False)
    async with AsyncClient(transport=ASGITransport(app_for(sessions, core)), base_url='http://isolated') as client:
        if request['url'] == '/test/setup':
            async with sessions() as session:
                project_id = (await _project(session)).id
                add_audit_event(session, workspace_id=project_id, resource_id=project_id,
                    event_type='workspace_owner_bound', generation=1, payload={'principal_id': 'operator'})
                await session.commit()
            response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
                'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
                'experiment': {'name': 'Mounted operations', 'objective': 'Exact draft receiving'},
                'domain_kind': 'protein_in_silico', 'capability_id': 'protein.native.rfd4_proteina.generate'},
                headers={'Idempotency-Key': 'create'})
            navigation = response.json()
            if response.status_code == 201:
                from test_denovo_project_http import envelope, request_for
                _, native = request_for('rfd4_proteina:generate', home)
                initialized = await client.put(
                    f'/api/projects/{project_id}/workflow-setups/{navigation["setup_context_id"]}/draft',
                    json={'expected_generation': 0, 'draft': envelope(native)},
                    headers={'Idempotency-Key': 'initial-native'})
                assert initialized.status_code == 200, initialized.text
            result = {'status': response.status_code, 'data': {**navigation, 'project_id': project_id}}
        else:
            response = await client.request(request.get('method', 'GET'), request['url'], json=request.get('data'), headers=request.get('headers', {}))
            result = {'status': response.status_code, 'data': response.json()}
    await project_engine.dispose()
    await core_engine.dispose()
    return result


if __name__ == '__main__':
    print(json.dumps(asyncio.run(main(json.load(sys.stdin)))))

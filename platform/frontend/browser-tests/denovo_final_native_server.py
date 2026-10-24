"""Bounded scratch-native de-novo browser server. NOT INFERENCE.

No main/lifespan/worker. Only native router/storage owners and synthetic auth.
Run via denovo_final_qualification.py in a loopback-only user namespace.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['BMS_FINAL_BROWSER_ROOT']).resolve()
assert ROOT.is_relative_to(Path('/home/dalab/.hermes/profiles/fresh/cache/scratch'))
ROOT.mkdir(parents=True, exist_ok=True)
for key, leaf in {
    'HOME':'home', 'BMS_DATA':'data', 'BMS_INPUTS':'inputs', 'BMS_RESULTS_DIR':'results',
    'BMS_WORK':'work', 'BMS_CACHE':'cache', 'BMS_ANALYSIS_CACHE':'analysis',
    'XDG_CACHE_HOME':'xdg', 'TMPDIR':'tmp', 'BMS_WEIGHTS':'weights',
    'BMS_CONTAINER_DIR':'containers', 'BMS_MSA_CACHE':'msa', 'NXF_HOME':'nxf',
}.items():
    os.environ[key] = str(ROOT / leaf)
    (ROOT / leaf).mkdir(parents=True, exist_ok=True)
os.environ['BMS_HOME'] = str(REPO)
for key, leaf in {'BMS_DB_PATH':'core.db','BMS_EXPERIMENT_DB_PATH':'project.db',
                  'BMS_MOLBIO_NGS_DB_PATH':'molbio.db'}.items():
    os.environ[key] = str(ROOT / leaf)
for key in ['DATABASE_URL', 'BMS_DATABASE_URL', 'BMS_EXPERIMENT_DATABASE_URL']:
    os.environ.pop(key, None)
sys.path[:0] = [str(REPO / 'platform/api/tests'), str(REPO / 'platform/api'), str(REPO)]

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from database import Base, engine, async_session
from routers import models, templates, user_templates, projects, project_manager, jobs, files, binder_continuation, execution_targets, molecular_dynamics, designs, experiment_workspaces, inputs, sequence_native
from test_denovo_project_http import app_for
# Storage adapter: expose ONLY the two inventoried retained inert artifact roots.
# This avoids treating a missing harness binding as a product download failure.
import paths
native_roots = paths.get_allowed_roots
retained = Path('/home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-complete-parent/phase2/api-receiving/basetemp')
def read_roots():
    return {**native_roots(),
        'retained_generation': retained / 'test_complete_receiving_real_e1/output',
        'retained_redesign': retained / 'test_partial_zero_unexpected_a2/results'}
paths.get_allowed_roots = read_roots
files.get_allowed_roots = read_roots
import experiment_migrations
from experiment_database import create_experiment_engine, create_experiment_session_factory

experiment_migrations.run_all(ROOT / 'project.db')
project_engine = create_experiment_engine('sqlite+aiosqlite:///' + str(ROOT / 'project.db'))
project_sessions = create_experiment_session_factory(project_engine)
app = app_for(project_sessions, async_session)
for router, prefix in [(models.router,'/api/models'), (templates.router,'/api/templates'),
        (projects.router,''), (designs.router,'/api/designs'), (experiment_workspaces.router,''),
        (inputs.router,'/api/inputs'), (sequence_native.router,'/api'),
        (jobs.router,'/api/jobs'), (files.router,'/api/files'),
        (binder_continuation.router,'/api/binder-continuation'),
        (execution_targets.router,'/api/execution-targets'), (molecular_dynamics.router,'')]:
    app.include_router(router, prefix=prefix)

@app.middleware('http')
async def bounded(request: Request, call_next):
    # Explicitly forbid execution; these routes are not exercised as fake successes.
    path = request.url.path
    if request.method not in {'GET','HEAD'}:
        allowed = ((request.method == 'POST' and (path == '/api/projects' or path.endswith('/workflow-setups')))
                   or (request.method == 'PUT' and '/workflow-setups/' in path and path.endswith('/draft'))
                   or path.startswith('/api/user-templates') or path == '/api/files/materialize-structure')
        if not allowed:
            return JSONResponse({'detail':'Inert browser qualification: execution is unperformed'},405)
    body = (await request.body()).decode('utf-8', errors='replace')
    try:
        response = await call_next(request)
    except Exception as exc:
        with (ROOT / 'http-errors.jsonl').open('a') as stream:
            stream.write(json.dumps({'path':path,'error':repr(exc)})+'\n')
        raise
    if path.startswith('/api/'):
        with (ROOT / 'http.jsonl').open('a') as stream:
            stream.write(json.dumps({'method':request.method,'path':str(request.url),
                'headers':dict(request.headers),'body':body,'status':response.status_code})+'\n')
    return response

@app.get('/acceptance-health')
def health():
    return {'root':str(ROOT),'source':str(REPO),'core':str(engine.url),
            'project':str(project_engine.url),'inert':True,'namespace':os.readlink('/proc/self/ns/net')}

BUILD = Path(os.environ['BMS_FINAL_BROWSER_BUILD']).resolve()
app.mount('/bms/assets', StaticFiles(directory=BUILD / 'assets'), name='assets')
@app.get('/{route:path}')
def shell(route: str):
    if route.startswith('api/'):
        return JSONResponse({'detail':'Native owner not mounted in bounded harness'},404)
    leaf = BUILD / route
    return FileResponse(leaf if route and leaf.is_file() and leaf.is_relative_to(BUILD) else BUILD / 'index.html')

async def seed():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if (ROOT / 'seed.json').exists():
        return
    # Use the existing real Project HTTP fixture, not a handwritten Project row.
    from httpx import AsyncClient, ASGITransport
    from test_project_workflow_setups import _project_payload
    async with AsyncClient(transport=ASGITransport(app), base_url='http://scratch') as client:
        response = await client.post('/api/projects', json=_project_payload('INERT final browser'))
        assert response.status_code == 201, response.text
        pid = response.json()['id']
        response = await client.post(f'/api/projects/{pid}/workflow-setups', json={
            'schema':'bms.project-workflow-setup.create.v1','relationship_kind':'primary',
            'experiment':{'name':'Browser retained authoring','objective':'No scientific execution'},
            'domain_kind':'protein_in_silico','capability_id':'protein.native.rfd4_proteina.generate'},
            headers={'Idempotency-Key':'final-browser-create'})
        assert response.status_code == 201, response.text
        setup = response.json()
        (ROOT / 'seed.json').write_text(json.dumps({'project_id':pid,'setup':setup},indent=2))
    (ROOT / 'root-readback.json').write_text(json.dumps(health(),indent=2))

if __name__ == '__main__':
    asyncio.run(seed())
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18871)

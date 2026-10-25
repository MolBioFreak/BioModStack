"""Scratch SQLite HTTP owner for mounted ordinary parent acceptance; no Jobs route."""
import asyncio
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['BMS_PARENT_LIFECYCLE_ROOT']).resolve()
assert ROOT.is_relative_to(Path('/home/dalab/.hermes/profiles/fresh/cache/scratch'))
ROOT.mkdir(parents=True, exist_ok=True)
for key, leaf in {'HOME':'home','BMS_DATA':'data','BMS_INPUTS':'inputs','BMS_RESULTS_DIR':'results','BMS_WORK':'work','BMS_CACHE':'cache','BMS_ANALYSIS_CACHE':'analysis','TMPDIR':'tmp','BMS_WEIGHTS':'weights','BMS_CONTAINER_DIR':'containers','BMS_MSA_CACHE':'msa','NXF_HOME':'nxf'}.items():
    os.environ[key] = str(ROOT / leaf)
    (ROOT / leaf).mkdir(exist_ok=True)
os.environ['BMS_HOME'] = str(REPO)
os.environ['BMS_DB_PATH'] = str(ROOT / 'core.db')
os.environ['BMS_EXPERIMENT_DB_PATH'] = str(ROOT / 'project.db')
for key in ['DATABASE_URL','BMS_DATABASE_URL','BMS_EXPERIMENT_DATABASE_URL']:
    os.environ.pop(key, None)
sys.path[:0] = [str(REPO / 'platform/api/tests'), str(REPO / 'platform/api'), str(REPO)]
from fastapi import Request
from fastapi.responses import JSONResponse
from database import Base, engine, async_session
from experiment_database import create_experiment_engine, create_experiment_session_factory
import experiment_migrations
from routers import models, templates, projects, files
from test_denovo_project_http import app_for
experiment_migrations.run_all(ROOT / 'project.db')
project_engine = create_experiment_engine('sqlite+aiosqlite:///' + str(ROOT / 'project.db'))
app = app_for(create_experiment_session_factory(project_engine), async_session)
for router, prefix in [(models.router,'/api/models'),(templates.router,'/api/templates'),(projects.router,''),(files.router,'/api/files')]:
    app.include_router(router, prefix=prefix)

@app.middleware('http')
async def bounded(request: Request, call_next):
    path = request.url.path
    allowed = (request.method in {'GET','HEAD'} or path.startswith('/api/user-templates') or
        (request.method == 'POST' and (path == '/api/projects' or path == '/api/files/materialize-structure' or path.endswith('/workflow-setups') or path.endswith('/prepare-launch'))) or
        (request.method == 'PUT' and path.endswith('/draft')))
    if (ROOT / 'browser-readonly').exists() and request.method not in {'GET', 'HEAD'}:
        allowed = False
    body = (await request.body()).decode()
    response = await call_next(request) if allowed else JSONResponse({'detail':'Execution excluded from qualification'},405)
    with (ROOT / 'http.jsonl').open('a') as out:
        out.write(json.dumps({'method':request.method,'path':str(request.url),'body':body,'status':response.status_code})+'\n')
    return response

@app.get('/acceptance-health')
def health():
    return {'root':str(ROOT),'source':str(REPO),'core':str(engine.url),'project':str(project_engine.url),'execution_routes':False}

async def seed():
    import hashlib
    source = ROOT / 'inputs' / 'two-model.pdb'
    source.write_text(''.join(f'MODEL     {m:4d}\nATOM      1  CA  ALA A   1       {m}.000   2.000   3.000  1.00 20.00           C  \nENDMDL\n' for m in (1,2)) + 'END\n')
    state = ROOT / 'inputs' / 'inert-state.pt'
    state.write_bytes(b'Inert opaque state: persistence only, never deserialize')
    (ROOT / 'source.json').write_text(json.dumps({'source':{'path':'inputs/two-model.pdb','model_number':2,'expected_sha256':hashlib.sha256(source.read_bytes()).hexdigest()},'state':{'path':str(state),'sha256':hashlib.sha256(state.read_bytes()).hexdigest(),'seed':0}}))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from httpx import AsyncClient, ASGITransport
    from test_project_workflow_setups import _project_payload
    async with AsyncClient(transport=ASGITransport(app), base_url='http://scratch') as client:
        r = await client.post('/api/projects', json=_project_payload('Mounted parent no execution'))
        assert r.status_code == 201, r.text
        pid = r.json()['id']
        r = await client.post(f'/api/projects/{pid}/workflow-setups', json={'schema':'bms.project-workflow-setup.create.v1','relationship_kind':'primary','experiment':{'name':'Mounted parent','objective':'Persist actual parent envelope'},'domain_kind':'protein_in_silico','capability_id':'protein.native.rfd4_proteina.generate'}, headers={'Idempotency-Key':'parent-create'})
        assert r.status_code == 201, r.text
        (ROOT / 'seed.json').write_text(json.dumps({'project_id':pid,'setup':r.json()},indent=2))

if os.environ.get('BMS_PARENT_BROWSER_BUILD'):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    build = Path(os.environ['BMS_PARENT_BROWSER_BUILD']).resolve()
    app.mount('/bms/assets', StaticFiles(directory=build / 'assets'), name='assets')
    @app.get('/{route:path}')
    def shell(route: str):
        if route.startswith('api/'):
            return JSONResponse({'detail':'Owner not mounted in bounded qualification'},404)
        return FileResponse(build / 'index.html')

if __name__ == '__main__':
    asyncio.run(seed())
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('BMS_PARENT_PORT','18879')))

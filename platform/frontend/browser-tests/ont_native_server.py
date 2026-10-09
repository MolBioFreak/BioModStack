"""Scratch native ONT read acceptance; never imports the application lifespan.

Run inside a loopback-only user/net namespace. Required env: ONT_ACCEPTANCE_ROOT,
ONT_ACCEPTANCE_CLONE_OUTPUT (real prior wf-clone output), ONT_ACCEPTANCE_TOKEN.
Start via the locked API Python, then uvicorn serves native routers on 127.0.0.1.
No scientific calls or instrument mutations are allowed by this harness.
"""
from pathlib import Path
import asyncio
import hashlib
import json
import os
import shutil
import sys

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['ONT_ACCEPTANCE_ROOT']).resolve()
SOURCE = Path(os.environ['ONT_ACCEPTANCE_CLONE_OUTPUT']).resolve()
TOKEN = os.environ['ONT_ACCEPTANCE_TOKEN']
assert ROOT != SOURCE and not ROOT.is_relative_to(REPO)
ROOT.mkdir(parents=True, exist_ok=True)
for key, value in {
    'HOME': ROOT / 'home', 'XDG_CONFIG_HOME': ROOT / 'config',
    'BMS_HOME': REPO, 'BMS_DATA': ROOT / 'state', 'BMS_INPUTS': ROOT / 'inputs',
    'BMS_RESULTS_DIR': ROOT / 'state/bms_results', 'BMS_WORK': ROOT / 'work',
    'BMS_ANALYSIS_CACHE': ROOT / 'analysis_cache', 'BMS_DB_PATH': ROOT / 'jobs.db',
    'BMS_EXPERIMENT_DB_PATH': ROOT / 'experiments.db',
    'BMS_MOLBIO_NGS_DB_PATH': ROOT / 'molbio.db',
    'BMS_MOLBIO_NGS_REFERENCE_ROOT': ROOT / 'references',
}.items():
    os.environ[key] = str(value)
    if key != 'BMS_HOME': (value.parent if key.endswith('_PATH') else value).mkdir(parents=True, exist_ok=True)
for key in ['DATABASE_URL', 'BMS_DATABASE_URL', 'BMS_EXPERIMENT_DATABASE_URL', 'BMS_MOLBIO_NGS_DATABASE_URL']:
    os.environ.pop(key, None)
os.environ['BMS_RUNTIME_MODE'] = 'dev'
sys.path[:0] = [str(REPO / 'platform/api'), str(REPO)]

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from database import Base, Job, async_session, engine
from routers import jobs, files, models, ngs_alignment_sessions, sequence_qc, ont_runs, ont_signal_workbench

app = FastAPI()
app.add_exception_handler(ngs_alignment_sessions.OntNgsRouteError, ngs_alignment_sessions.ont_ngs_route_error_handler)
@app.middleware('http')
async def isolation(request: Request, call_next):
    if request.headers.get('x-ont-acceptance') != TOKEN and request.cookies.get('ont-acceptance') != TOKEN:
        return JSONResponse({'detail': 'Explicit synthetic acceptance auth required'}, 401)
    if request.method not in {'GET', 'HEAD'}:
        return JSONResponse({'detail': 'Read acceptance does not launch science'}, 405)
    return await call_next(request)

for router, prefix in [(models.router, '/api/models'), (jobs.router, '/api/jobs'), (files.router, '/api/files'),
        (ngs_alignment_sessions.router, '/api'), (sequence_qc.router, '/api/sequence-qc'),
        (ont_runs.router, '/api/ont'), (ont_runs.barcode_router, '/api/jobs'),
        (ont_signal_workbench.router, '/api/ont/signal-workbench')]:
    app.include_router(router, prefix=prefix)

@app.get('/acceptance-health')
def health():
    return {'scratch_root': str(ROOT), 'source': str(SOURCE), 'science_started': False}

async def seed():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    jid = '3a786c08-88d9-4343-9d58-1db49bb03f9b'
    destination = ROOT / 'state/bms_results' / jid / 'assembly/wf_clone_out'
    if not destination.exists(): shutil.copytree(SOURCE, destination, symlinks=False)
    paths = sorted(path for path in destination.rglob('*') if path.is_file())
    inventory = [{'path': str(p.relative_to(destination)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size} for p in paths]
    (ROOT / 'imported-artifacts.json').write_text(json.dumps({'source': str(SOURCE), 'job_id': jid, 'artifacts': inventory}, indent=2))
    async with async_session() as session:
        if await session.get(Job, jid) is None:
            session.add(Job(id=jid, name='Retained native Flye clone control', model_id='nanopore', mode='clone_validation',
                status='completed', queue_status='completed', params={'ont_workflow_id': 'wf_clone_validation', 'fastq_path': 'retained-native-input.fastq', 'wf_clone_assembly_tool': 'flye'},
                output_dir=str(destination.parents[1]), completed_stages=['wf_clone_validation'],
                stage_outputs={'wf_clone_validation': [str(p) for p in paths]},
                provenance={'acceptance_fixture': 'Imported real prior output; no science executed by this harness'}))
            await session.commit()
        job = await session.get(Job, jid)
        job.provenance = {**job.provenance, 'alignment_access_scheme': 'opaque_job_capability_v1', 'alignment_access_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()}
        await session.commit()
    # Optional retained native job roots. Only metadata is synthetic; all result
    # bytes are copied unchanged and inventoried. No scientific response replay.
    cases_file = os.environ.get('ONT_ACCEPTANCE_RETAINED_JOBS')
    if cases_file:
        cases = json.loads(Path(cases_file).read_text())
        for case in cases:
            source = Path(case['source_output']).resolve(strict=True)
            job_id = case['job_id']
            import uuid
            assert str(uuid.UUID(job_id)) == job_id
            target = ROOT / 'state/bms_results' / job_id
            if not target.exists(): shutil.copytree(source, target, symlinks=False)
            outputs = sorted(p for p in target.rglob('*') if p.is_file())
            inventory = [{'path': str(p.relative_to(target)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size} for p in outputs]
            (ROOT / f'imported-{job_id}.json').write_text(json.dumps({'source': str(source), 'job_id': job_id, 'artifacts': inventory}, indent=2))
            stages = {}
            for path in outputs:
                stages.setdefault(path.relative_to(target).parts[0], []).append(str(path))
            async with async_session() as session:
                if await session.get(Job, job_id) is None:
                    session.add(Job(id=job_id, name=case['name'], model_id='nanopore', mode=case['mode'],
                        status='completed', queue_status='completed', params=case['params'], output_dir=str(target),
                        completed_stages=list(stages), stage_outputs=stages,
                        provenance={'acceptance_fixture': 'Imported real prior native output; no science executed',
                            'alignment_access_scheme': 'opaque_job_capability_v1',
                            'alignment_access_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()}))
                    await session.commit()

if __name__ == '__main__':
    asyncio.run(seed())
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('ONT_ACCEPTANCE_API_PORT', '18761')))

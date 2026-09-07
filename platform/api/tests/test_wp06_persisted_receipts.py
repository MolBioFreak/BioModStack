"""Actual compiler + non-model argv probe + SQLite + ASGI projection."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

@pytest.fixture(autouse=True)
def isolated_scientific_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))

from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from database import Design, Job, get_session
from schemas import JobCreate
from routers import jobs
from services import core_protein_scientific_contract as contract
from services.nextflow import build_job_nextflow_command
from services.result_ingester import ingest_esmfold2_results
from tests.test_core_protein_scientific_admission import admission
from tests.test_core_protein_candidates import artifacts
from tests.test_esmfold2_scalars import DIALECT

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_openmm_receipts_persist_through_real_finalizer(admission, tmp_path):
    from services.result_state_integrity import finalize_successful_job
    current = Job(id='openmm',name='openmm',model_id='antibody_denovo',mode='validation',
        status='running',queue_status='running',awaiting_input=False,output_dir=str(tmp_path),
        params={'openmm_enabled':True},provenance={'core_protein_scientific_contract':1,
            'core_protein_requested_params':{'openmm_cdr_only':False}})
    root = tmp_path/'run/openmm/relaxation/fixture'
    root.mkdir(parents=True)
    values = {'cdr_only':False,'force_field':'amber14sb','max_iterations':100,
        'energy_tolerance':50.0,'restraint_mode':'framework','antibody_chain':'H','fix_structure':True}
    argv = ['--input','input.pdb','--output','relaxed/out.pdb','--output_json','relaxed/out.json']
    for key,value in values.items():
        if type(value) is bool:
            if value: argv.append('--'+key)
        else: argv.extend(['--'+key,str(value)])
    settings = {k:{'requested':False if k=='cdr_only' else None, 'effective':v,
        'origin':'request' if k=='cdr_only' else 'compute_tier' if k in ('max_iterations','energy_tolerance') else 'workflow_default',
        'scope':'antibody' if k=='cdr_only' else 'model'} for k,v in values.items()}
    payload = {'schema_version':1,'core_protein_scientific_contract':1,'model':'openmm','argv':argv,
        'settings':settings,'sources':[{'scope':'structure','sha256':'a'*64}]}
    source = os.environ.get('BMS_WP06_OPENMM_RECEIPT')
    if source:
        payload = json.loads(Path(source).read_bytes())
    (root/'effective_settings.json').write_bytes(Path(source).read_bytes() if source else json.dumps(payload).encode())
    (root/'fixture.pdb').write_text('NON_MODEL_FIXTURE_ONLY\n')
    admission.add_all([current, Design(id='openmm-row',job_id=current.id,name='fixture',pdb_path=str(root/'fixture.pdb'))])
    await admission.commit()
    async def ingest(*args,**kwargs): return 1
    result = await finalize_successful_job(current,str(tmp_path),admission,ingest_fn=ingest)
    assert result.completed
    await admission.refresh(current)
    assert current.provenance.get('core_protein_execution_settings'), 'finalizer lost OpenMM execution receipt'
    row = await admission.get(Design,'openmm-row')
    assert row.confidence_metrics['core_protein_execution_settings'] == current.provenance['core_protein_execution_settings']
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def db(): yield admission
    app.dependency_overrides[get_session] = db
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response = await client.get('/api/jobs/openmm/execution-settings')
        assert response.status_code == 200
        assert response.json()['status'] == 'ok'
        if os.environ.get('BMS_WP06_OPENMM_WIRE'):
            Path(os.environ['BMS_WP06_OPENMM_WIRE']).write_text(response.text)


@pytest.mark.asyncio
async def test_receipt_from_executed_argv_to_persisted_api(admission, monkeypatch, tmp_path):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('esmfold2','predict')}))
    msa = tmp_path / 'query.a3m'
    msa.write_text('>q\nACDE\n')
    raw = {'sequence':'ACDE', 'esmf_seed':0, 'esmf_msa_remove_insertions':False, 'esmf_msa_path':str(msa)}
    created = await jobs._create_job(JobCreate(name='receipt',model_id='esmfold2',mode='predict',params=raw), BackgroundTasks(), admission)
    current = await admission.get(Job, created.id)
    output = Path(current.output_dir)
    command = build_job_nextflow_command(current, current.params, str(output))
    request = {}
    for i, flag in enumerate(command[:-1]):
        if flag.startswith('--esmf_') or flag == '--core_protein_scientific_contract':
            value = command[i+1]
            try: value = json.loads(value)
            except ValueError: pass
            # The Nextflow CLI retains JSON strings as strings.
            if flag.endswith('_json'): value = command[i+1]
            request[flag[2:]] = value
    spec = importlib.util.spec_from_file_location('receipt_runner', ROOT / 'scripts/run_esmfold2_inference.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    request.update(output_dir=str(output / 'esmfold2_results'))
    argv, receipt = runner.compile_workflow_request(request, {str(msa):str(msa)})
    root = artifacts(output, ('a',))
    receipt_path = root / 'effective_settings.json'
    receipt_path.write_text(json.dumps(receipt, allow_nan=False))
    completed = subprocess.run([sys.executable, str(ROOT / 'tests/fixtures/wp06_capture.py'), *argv],
        env={**os.environ, 'BMS_ESMFOLD2_EFFECTIVE_SETTINGS':str(receipt_path)}, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads((root/'capture.json').read_text())['argv'] == receipt['argv']
    # Explicitly synthetic publication, distinct from instrumented argv evidence.
    artifacts(output, ('a',))
    p = root/'a.metrics.json'
    p.write_text(json.dumps({**json.loads(p.read_text()), 'scalar_dialect':DIALECT, 'plddt_mean':0.8}))
    await ingest_esmfold2_results(current.id, output, admission, current)
    admission.expire_all()
    current = await admission.get(Job, created.id)
    assert current.provenance.get('core_protein_execution_settings'), 'missing persisted execution receipt'
    row = (await admission.execute(select(Design).where(Design.job_id==current.id))).scalar_one()
    assert row.confidence_metrics.get('core_protein_execution_settings'), 'missing Design receipt binding'
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def db(): yield admission
    app.dependency_overrides[get_session] = db
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response = await client.get(f'/api/jobs/{current.id}/execution-settings')
        assert response.status_code == 200, response.text
        wire = response.json()
        assert wire['status'] == 'ok'
        entry = wire['receipts'][0]
        assert entry['artifact_sha256'] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        settings = {s['key']:s for s in entry['settings']}
        assert settings['seed']['requested'] == settings['seed']['effective'] == 0
        assert settings['msa_remove_insertions']['requested'] is False
        assert settings['msa_remove_insertions']['effective'] is False
        assert settings['msa_remove_insertions']['origin'] == 'request'
        assert entry['sources'][0]['sha256'] == hashlib.sha256(msa.read_bytes()).hexdigest()
        assert str(tmp_path) not in response.text and 'argv' not in response.text
        wire_path = os.environ.get('BMS_WP06_WIRE')
        if wire_path: Path(wire_path).write_text(json.dumps(wire))
        from services.esmfold2_scientific_consumer import verified_esmfold2_design
        assert (await verified_esmfold2_design(row, admission))['block']['candidate_id'] == 'a'
        block = row.confidence_metrics
        row.confidence_metrics = {**block, 'core_protein_execution_settings': []}
        await admission.commit()
        await admission.refresh(row)
        with pytest.raises((RuntimeError, ValueError), match='execution receipt replay changed'):
            await verified_esmfold2_design(row, admission)
        row.confidence_metrics = block
        await admission.commit()
        receipt_path.write_text('[]')
        damaged = await client.get(f'/api/jobs/{current.id}/execution-settings')
        assert damaged.json()['status'] == 'unavailable'

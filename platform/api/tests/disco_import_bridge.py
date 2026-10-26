"""CPU-only mounted-test bridge through real managed reads and native preparation."""
import json
import os
from pathlib import Path
import subprocess
import sys


def run(payload):
    root = Path(payload['root'])
    os.environ['BMS_DATA'] = str(root)
    os.environ['BMS_DB_PATH'] = str(root / 'scratch.sqlite')
    import paths
    from routers import files
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    roots = {'inputs': root / 'inputs'}
    paths.get_allowed_roots = lambda: roots
    files.get_allowed_roots = lambda: roots
    app = FastAPI()
    app.include_router(files.router, prefix='/files')
    app.dependency_overrides[files.get_governed_ngs_result_roots] = lambda: ()
    if payload['operation'] == 'import':
        with TestClient(app) as client:
            response = client.get('/files/disco-jobs/' + payload['path'])
        return {'status': response.status_code, 'data': response.json()}
    from schemas import JobCreate
    from routers.jobs import normalize_job_request
    from services.nextflow import compile_nextflow_invocation
    from component_runtime import SourceIdentity
    from scripts.lib.portable_inputs import discover_native_input_references
    request = normalize_job_request(JobCreate(**payload['request']))
    invocation = compile_nextflow_invocation(request.model_id, request.mode, request.params,
        str(root / 'out'), job_id='import-fixture', source_identity=SourceIdentity('a' * 40, 'b' * 40))
    params = invocation.native_parameters
    refs = discover_native_input_references(request.model_id, request.mode, params, [],
        output_dir=root / 'out', allowed_roots=[root])
    target = root / 'prepared'
    target.mkdir(exist_ok=True)
    command = [sys.executable, str(paths.REPO_ROOT / 'scripts/prep_protein_cad_request.py'),
        '--backend', 'disco', '--task', 'custom_json', '--num-designs', '1', '--target-lengths', '4',
        '--disco-input-json-path', params.get('pcad_disco_input_json_path', ''),
        '--disco-jobs-json', json.dumps(params['pcad_disco_jobs_json']),
        '--disco-jobs-source-path', params['pcad_disco_jobs_source_path'],
        '--output', str(target / 'request.json'), '--input-dir', str(target / 'inputs')]
    subprocess.run(command, check=True, capture_output=True, text=True)
    native = json.loads((target / 'inputs/disco_input.json').read_text())
    original = target / 'inputs/original_disco_jobs' / Path(params['pcad_disco_jobs_source_path']).name
    return {'params': params, 'native': native, 'refs': refs, 'original': original.read_text()}


if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin))))

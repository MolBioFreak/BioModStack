"""Real RFD3 process/producer transport; only the scientific executable is inert."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from test_rfd3_bounded_corrections import ROOT, cif_source, generation, load_script
from test_denovo_parent_source_workflows import process_source


def test_retired_catalog_is_not_a_launch_surface():
    import yaml
    from model_registry import ModelDefinition
    model = ModelDefinition.model_validate(yaml.safe_load((ROOT / 'platform/api/config/models/protein_local_redesign.yaml').read_text()))
    assert model.public_launch is False
    assert model.enabled is False


@pytest.mark.parametrize('conditioning,task,expected', [
    ({'length': '4'}, None, True), ({'length': '3-5'}, None, True),
    ({'length': '2'}, None, False), ({'length': None}, None, True),
    ({}, 'native_recall', True), ({'partial_t': 0}, 'partial_diffusion', True),
])
def test_effective_preparation_and_manifest_accounting(tmp_path, conditioning, task, expected):
    import scripts.rfd3_generation.contract as contract
    prepare = load_script('closeout_prepare', 'scripts/rfd3_generation/prepare_native_input.py', contract)
    writer = load_script('closeout_writer', 'scripts/rfd3_generation/build_result_manifest.py', contract)
    request = generation(1)
    request['generation'].update(min_length=100, max_length=200)
    request['conditioning'] = conditioning
    if not task:
        from services.rfd3_generation import normalize_generation_params
        _, request, _ = normalize_generation_params(
            dict(min_length=100, max_length=200, num_designs=1, conditioning=conditioning), job_name='accounting')
    if task:
        source = tmp_path / 'source.cif'
        source.write_text(cif_source())
        request.update(task=task, source={'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    native, receipt = prepare.prepare(request)
    if task:
        assert 'length' not in native['generation_0']
    else:
        assert native['generation_0']['length'] == conditioning['length']
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    cif, metadata = tmp_path / 'one.cif.gz', tmp_path / 'one.json'
    with gzip.open(cif, 'wt') as handle:
        handle.write(cif_source())
    metadata.write_text('{}')
    kwargs = dict(request_path=path, structure_paths=[cif], metadata_paths=[metadata], storage_prefix='run/rfd3')
    manifest = writer.build_result_manifest(**kwargs, accepted_candidate_ids=None)
    assert manifest['candidates'][0]['metrics']['residue_count'] == 4
    assert manifest['candidates'][0]['accepted'] is expected
    assert manifest['request_sha256'] == receipt['request_sha256']
    assert writer.build_result_manifest(**kwargs, accepted_candidate_ids=set())['aggregate']['accepted'] == 0
    assert writer.build_result_manifest(**kwargs, accepted_candidate_ids={'one'})['aggregate']['accepted'] == 1


@pytest.mark.parametrize('mapped,yield_kind,exit_code,remote', [
    (False, 'paired', 0, True), (True, 'paired', 0, True),
    (False, 'empty', 0, True), (False, 'unpaired', 0, True),
    (True, 'paired', 7, True), (False, 'empty', 9, True),
    (False, 'empty', 0, False), (False, 'unpaired', 0, False),
    (True, 'paired', 7, False),
])
def test_conditioned_remote_workflow_and_native_terminal_yields(tmp_path, mapped, yield_kind, exit_code, remote):
    jar = Path(os.environ['BMS_TEST_NEXTFLOW_JAR'])
    source = tmp_path / 'controller-only.cif'
    payload = cif_source().encode()
    source.write_bytes(payload)
    worker = tmp_path / 'worker-renamed.cif'
    worker.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    request = generation(2)
    request['source'] = {'path': str(source), 'sha256': digest}
    if mapped:
        request['source_mapping'] = 'bms.rfd3.source-map.v1'
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request))
    sealed = request_path.read_bytes()
    bindings = tmp_path / 'bindings.json'
    bindings.write_text(json.dumps({'schema': 'bms.portable-input-bindings.v1', 'roots': [str(tmp_path)],
        'bindings': [{'reference': {'source_path': str(source), 'sha256': digest, 'size_bytes': len(payload)}, 'path': str(worker)}]}))
    if remote:
        source.unlink()  # unavailable BEFORE the workflow evaluates its input channels
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    wrapper = binaries / 'python3'
    wrapper.write_text(f'''#!{sys.executable}
import os, sys
args = sys.argv[1:]
if args and args[0].startswith('/scripts/'):
    args[0] = {str(ROOT)!r} + args[0]
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
''')
    wrapper.chmod(0o755)
    executable = binaries / 'rfd3'
    executable.write_text(f'''#!{sys.executable}
# INERT scientific leaf. Never represented as inference evidence.
import gzip, json, sys
from pathlib import Path
args = dict(x.split('=', 1) for x in sys.argv[1:])
native = json.loads(Path(args['inputs']).read_text())['generation_0']
assert Path(native['input']).read_bytes()
assert not Path(native['input']).is_absolute()
assert Path(native['input']).parts[0] == 'rfd3_sources'
out = Path(args['out_dir'])
if {yield_kind!r} != 'empty':
    with gzip.open(out / 'one.cif.gz', 'wb') as handle:
        handle.write({payload!r})
if {yield_kind!r} == 'paired':
    (out / 'one.json').write_text('{{}}')
sys.exit({exit_code})
''')
    executable.chmod(0o755)
    workflow = (ROOT / 'workflows/protein_design.nf').read_text()
    helper = re.search(r'^def rfd3GenerationSources\(.*?^\}', workflow, re.M | re.S)
    # The old product has no helper: still execute its real entry rather than
    # failing solely on the absent function.
    functions = helper.group() if helper else ''
    entry = workflow.split('            if (generalRfd3Generation) {', 1)[1].split('            } else {', 1)[0]
    (tmp_path / 'prep.nf').write_text(process_source('workflows/protein_design.nf', 'PrepareGeneralRFD3Input'))
    (tmp_path / 'build.nf').write_text(process_source('workflows/protein_design.nf', 'BuildGeneralRFD3ResultManifest'))
    (tmp_path / 'run.nf').write_text(process_source('modules/rfd3.nf', 'RunRFD3'))
    (tmp_path / 'main.nf').write_text('''nextflow.enable.dsl=2
import groovy.json.JsonSlurper
include { PrepareGeneralRFD3Input } from './prep'
include { BuildGeneralRFD3ResultManifest } from './build'
include { RunRFD3 } from './run'
''' + functions + '\nworkflow {\n' + entry + '\n}\n')
    params = {'code_root': str(ROOT), 'out_dir': str(tmp_path / 'returned'),
        'rfd3_generation_request_path': str(request_path), 'rfd3_request_path': None,
        'rfd3_extra_config': '', 'rfd3_generation_num_designs': 2,
        'rfd3_generation_min_length': 1, 'rfd3_generation_max_length': 2,
        'rfd3_generation_seed': 0, 'rfd3_generation_dump_trajectories': False}
    (tmp_path / 'params.json').write_text(json.dumps(params))
    env = dict(os.environ, BMS_PORTABLE_INPUT_BINDINGS=str(bindings), NXF_HOME=str(tmp_path / 'nxf-home'),
        PATH=str(binaries) + os.pathsep + os.environ['PATH'], NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
        PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'platform/api')]))
    result = subprocess.run(['java', '-jar', str(jar), 'run', 'main.nf', '-params-file', 'params.json',
        '-w', str(tmp_path / 'work'), '-ansi-log', 'false'], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=150)
    (tmp_path / 'nextflow-stdout.log').write_text(result.stdout)
    assert (result.returncode == 0) == (exit_code == 0), result.stdout
    assert request_path.read_bytes() == sealed
    assert source.exists() is (not remote)
    manifests = list((tmp_path / 'work').glob('*/*/rfd3_generation_result_manifest.json'))
    assert manifests, result.stdout
    if exit_code:
        observed = [(p, json.loads(p.read_text())) for p in manifests]
        assert len(observed) == 1
        manifest_path, manifest = observed[0]
        assert int((manifest_path.parent / '.exitcode').read_text()) == exit_code
    else:
        manifest_path = tmp_path / 'returned/collected/rfd3_generation/rfd3_generation_result_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        from services.rfd3_generation import validate_result_manifest
        validate_result_manifest(manifest, request=request, output_root=tmp_path / 'returned', job_id=request['job_id'])
    assert manifest['execution_status'] == ('failed' if exit_code else 'completed')
    assert manifest['aggregate']['generated'] == (1 if yield_kind == 'paired' else 0)
    assert len(manifest['population']['unpaired_artifacts']) == (1 if yield_kind == 'unpaired' else 0)
    assert manifest['population']['failed'] is None
    assert manifest['request_sha256'] == hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

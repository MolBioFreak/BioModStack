"""Actual Nextflow source/collection shells; only RFD3 computation is inert.

MD uses a declared terminal replica fixture, not a dynamics result. All native
input preparation, source copies, collection and metadata conversion are real.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from test_rfd3_bounded_corrections import cif_source, generation

ROOT = Path(__file__).resolve().parents[1]


def process_source(path, name):
    text = (ROOT / path).read_text()
    match = re.search(r'^process ' + re.escape(name) + r' \{.*?^\}', text, re.M | re.S)
    assert match, name
    return match.group()


def run_nextflow(tmp_path, pipeline, params, *, binaries=None):
    launcher = os.environ['BMS_TEST_NEXTFLOW_LAUNCHER']
    (tmp_path / 'main.nf').write_text(pipeline)
    (tmp_path / 'params.json').write_text(json.dumps(params))
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               NXF_HOME=str(tmp_path / 'nxf-home'), BMS_HOME=str(ROOT),
               PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'platform/api')]))
    if binaries:
        env['PATH'] = str(binaries) + os.pathsep + env['PATH']
    result = subprocess.run([launcher, 'run', str(tmp_path / 'main.nf'),
        '-params-file', str(tmp_path / 'params.json'), '-w', str(tmp_path / 'work'),
        '-ansi-log', 'false'], cwd=tmp_path, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
    (tmp_path / 'nextflow-stdout.log').write_text(result.stdout)
    assert result.returncode == 0, result.stdout
    return list((tmp_path / 'work').glob('*/*/.command.sh'))


@pytest.mark.parametrize('kind,mapped', [('generation', False), ('generation', True), ('local_redesign', True)])
def test_actual_rfd3_prepare_to_run_tuple(tmp_path, kind, mapped):
    source = tmp_path / 'original.cif'
    source.write_text(cif_source())
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    binding = {'path': str(source), 'sha256': source_digest}
    if kind == 'generation':
        request = generation()
        request['source'] = binding
        name = 'PrepareGeneralRFD3Input'
        filename = 'workflows/protein_design.nf'
        invoke = f'{name}(Channel.value(file(params.request)))'
    else:
        request = {'schema': 'bms.rfd3.local-redesign.request.v1', 'input': binding,
            'rfd3': {'input': str(source), 'contig': 'A2'}, 'sequence_policy': 'skip',
            'redesign_mode': 'native_recall', 'execution': {'num_designs': 2, 'seed': 0}}
        name = 'PrepareProteinLocalNativeRFD3Input'
        filename = 'workflows/protein_local_redesign.nf'
        invoke = f'{name}(Channel.value(file(params.source)), Channel.value(file(params.request)))'
    if mapped:
        request['source_mapping'] = 'bms.rfd3.source-map.v1'
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request))
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    python = binaries / 'python3'
    python.write_text(f'''#!{sys.executable}
import os, sys
args = sys.argv[1:]
if args and args[0].startswith('/scripts/'):
    args[0] = {str(ROOT)!r} + args[0]
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
''')
    python.chmod(0o755)
    native = binaries / 'rfd3'
    native.write_text(f'''#!{sys.executable}
# Explicit inert scientific leaf: no checkpoint, sampling or GPU execution.
import gzip, hashlib, json, sys
from pathlib import Path
args = dict(value.split('=', 1) for value in sys.argv[1:])
inputs = json.loads(Path(args['inputs']).read_text())
item = next(iter(inputs.values()))
Path({str(source)!r}).unlink(missing_ok=True)
selected = Path(item['input'])
assert not selected.is_absolute(), item
assert selected.parts[0] == 'rfd3_sources', item
payload = selected.read_bytes()
out = Path(args['out_dir']); out.mkdir(exist_ok=True)
with gzip.open(out / 'fixture_0.cif.gz', 'wb') as f:
    f.write(b'data_explicitly_inert_transport_fixture\\n#\\n')
(out / 'fixture_0.json').write_text(json.dumps({{'native_execution': False,
    'source_sha256': hashlib.sha256(payload).hexdigest(), 'source_path': str(selected)}}))
''')
    native.chmod(0o755)
    quote = '''def shellQuote(value) {
    return "'" + value.toString().replace("'", "'\\\"'\\\"'") + "'"
}
'''
    (tmp_path / 'prep.nf').write_text(quote + process_source(filename, name))
    (tmp_path / 'rfd3.nf').write_text(process_source('modules/rfd3.nf', 'RunRFD3'))
    params = {'code_root': str(ROOT), 'out_dir': str(tmp_path / 'returned'),
        'request': str(request_path), 'source': str(source), 'rfd3_extra_config': '',
        'rfd3_request_path': str(request_path) if kind == 'local_redesign' else None,
        'rfd3_generation_request_path': str(request_path) if kind == 'generation' else None,
        'rfd3_generation_num_designs': 2, 'plr_num_designs': 2,
        'rfd3_generation_min_length': 1, 'rfd3_generation_max_length': 2,
        'rfd3_generation_seed': 0, 'rfd3_generation_dump_trajectories': False}
    pipeline = f'''nextflow.enable.dsl=2
include {{ {name} }} from './prep'
include {{ RunRFD3 }} from './rfd3'
workflow {{
    {invoke}
    RunRFD3({name}.out.input_json)
}}
'''
    commands = run_nextflow(tmp_path, pipeline, params, binaries=binaries)
    assert len(commands) == 2
    assert not source.exists(), 'scientific fixture must hide the controller source'
    output = tmp_path / 'returned'
    record = json.loads((output / 'run/rfd3/fixture_0.json').read_text())
    assert record['native_execution'] is False
    retained = output / 'collected' / ('rfd3_generation' if kind == 'generation' else 'protein_local_redesign')
    receipt_path = retained / ('rfd3_generation_preparation_receipt.json' if kind == 'generation' else 'rfd3_preparation_receipt.json')
    receipt = json.loads(receipt_path.read_text())
    assert record['source_sha256'] == receipt['runtime_input']['sha256']
    staged = retained / record['source_path']
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == record['source_sha256']
    if mapped:
        assert receipt['source_mapping']['original_sha256'] == source_digest
    else:
        assert record['source_sha256'] == source_digest


@pytest.mark.parametrize('with_provenance', [False, True])
def test_actual_md_collector_keeps_original_and_selected_source(tmp_path, with_provenance):
    source = tmp_path / "operator's original.pdb"
    original = b'MODEL        1\nREMARK original first model\nENDMDL\nMODEL        2\nREMARK selected model\nENDMDL\nEND\n'
    selected = b'REMARK selected model\nEND\n'
    source.write_bytes(original)
    derivative = tmp_path / 'selected-model-2.pdb'
    derivative.write_bytes(selected)
    digest = lambda data: hashlib.sha256(data).hexdigest()
    provenance = {'model_number': 2, 'original_format': 'pdb',
        'source_ref': {'kind': 'candidate', 'id': 'producer', 'candidate_ref': {
            'kind': 'native_document', 'job_id': 'producer', 'artifact_id': 'alternate', 'model': 2}},
        'snapshot': {'structure': str(derivative), 'structure_sha256': digest(selected), 'structure_bytes': len(selected)},
        'original_snapshot': {'path': str(source), 'sha256': digest(original), 'bytes': len(original)}}
    child = tmp_path / 'child'
    child.mkdir()
    (child / 'source.pdb').write_bytes(selected)
    (child / 'manifest.json').write_text(json.dumps({'schema': 'bms.md.run.v1',
        'job_id': 'md-parent', 'replica_index': 0, 'replica_seed': 42,
        'engine': {'name': 'gromacs'}, 'status': 'completed', 'config': {},
        'artifacts': {'declared_terminal_fixture': {'path': 'source.pdb'}}}))
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({'child_ids': ['child'], 'child_output_dirs': [str(child)],
                                  'total': 1, 'completed': 1}))
    spawn = tmp_path / 'spawn.json'
    spawn.write_text(json.dumps({'schema': 'bms.md.replica-spawn.v1',
        'parent_job_id': 'md-parent', 'replica_count': 1, 'engine': 'gromacs',
        'children': [{'id': 'child', 'replica_index': 0, 'replica_seed': 42}]}))
    (tmp_path / 'collect.nf').write_text(process_source(
        'workflows/experimental/molecular_dynamics/orchestrator.nf', 'MD_COLLECT_REPLICAS'))
    params = {'code_root': str(ROOT), 'api_python': sys.executable,
        'job_id': 'md-parent', 'out_dir': str(tmp_path / 'returned'),
        'md_source_provenance': provenance if with_provenance else None,
        'status': str(status), 'spawn': str(spawn)}
    pipeline = '''nextflow.enable.dsl=2
include { MD_COLLECT_REPLICAS } from './collect'
workflow {
    MD_COLLECT_REPLICAS(Channel.value(file(params.status)), Channel.value(file(params.spawn)))
}
'''
    commands = run_nextflow(tmp_path, pipeline, params)
    assert len(commands) == 1
    returned = tmp_path / 'returned'
    assert (returned / 'manifest.json').is_file()
    source.unlink()
    derivative.unlink()
    shutil.rmtree(child)
    if with_provenance:
        retained = json.loads((returned / 'retained_source.json').read_text())
        assert retained['source_ref'] == provenance['source_ref']
        assert (returned / retained['snapshot']['structure']).read_bytes() == selected
        assert (returned / retained['original_snapshot']['path']).read_bytes() == original
        assert retained['original_snapshot']['sha256'] == digest(original)
    else:
        assert not (returned / 'retained_source.json').exists()

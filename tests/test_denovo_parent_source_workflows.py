"""Actual Nextflow source/collection shells.

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

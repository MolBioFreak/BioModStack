"""Real CAD Nextflow shells with an explicitly inert native executable leaf."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('backend', ['laproteina', 'disco'])
@pytest.mark.parametrize('zero_yield', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_cad_native_tree_and_failed_task_return(tmp_path, backend, zero_yield, failed):
    launcher = os.environ.get('BMS_TEST_NEXTFLOW_LAUNCHER')
    if not launcher:
        pytest.skip('Set BMS_TEST_NEXTFLOW_LAUNCHER to cached offline launcher')
    fixture = tmp_path / 'fixture.py'
    fixture.write_text('''import json
from pathlib import Path
r=json.loads(Path('protein_cad_request.json').read_text())
Path('raw/pdbs').mkdir(parents=True)
Path('raw/metadata').mkdir(parents=True)
Path('raw/pdbs/sample.pdb').write_text('INERT NATIVE FIXTURE\\n')
Path('raw/metadata/generator_sample.json').write_text('{}')
Path('design_manifest.json').write_text(json.dumps([{'structure_path':'raw/pdbs/sample.pdb'}]))
if r['backend']=='laproteina':
    Path('invocation_fixture').mkdir()
    Path('invocation_fixture/invocation.json').write_text(json.dumps(r))
    Path('laproteina_publication.json').write_text('{}')
    Path('laproteina.log').write_text('inert')
else:
    Path('disco_output').mkdir()
    Path('native').mkdir()
    Path('native/sequence.txt').write_text('>cogen_seq 0\\nAC')
    Path('disco_publication.json').write_text(json.dumps(r))
    Path('disco.log').write_text('inert')
''')
    if zero_yield:
        text = fixture.read_text()
        text = '\n'.join(line for line in text.splitlines() if not line.startswith((
            "Path('raw/pdbs/sample.pdb')", "Path('raw/metadata/generator_sample.json')")))
        text = text.replace("[{'structure_path':'raw/pdbs/sample.pdb'}]", '[]')
        fixture.write_text(text)
    if failed:
        with fixture.open('a') as stream:
            stream.write('\nraise SystemExit(23)\n')
    # Only substitute science; declarations, prep and failed-task collection are real.
    module = (ROOT / 'modules/protein_cad_experimental.nf').read_text()
    for name in ['laproteina', 'disco']:
        module = module.replace('${params.code_root}/scripts/run_' + name + '_inference.py', str(fixture))
    (tmp_path / 'cad.nf').write_text(module)
    workflow = (ROOT / 'workflows/protein_cad_experimental.nf').read_text().replace("'../modules/protein_cad_experimental.nf'", "'./cad.nf'")
    (tmp_path / 'main.nf').write_text(workflow)
    params = {'out_dir': str(tmp_path / "returned's outputs"), 'weights_root': str(tmp_path / 'weights'),
              'code_root': str(ROOT), 'pcad_backend': backend, 'pcad_task': 'unconditional',
              'pcad_target_lengths': '10', 'pcad_num_designs': 1,
              'pcad_laproteina_settings_json': json.dumps({'seed': 0, 'self_cond': False}),
              'pcad_disco_settings_json': json.dumps({'seeds': 0, 'effort': 'fast'}),
              'pcad_disco_jobs_json': json.dumps([{'name': "operator's input", 'sequences': [
                  {'proteinChain': {'sequence': '----', 'count': 1}}]}])}
    (tmp_path / 'params.json').write_text(json.dumps(params))
    env = os.environ.copy()
    env['NXF_OFFLINE'] = 'true'
    result = subprocess.run([launcher, 'run', str(tmp_path / 'main.nf'), '-params-file',
                             str(tmp_path / 'params.json'), '-w', str(tmp_path / 'work')],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180)
    assert (result.returncode != 0) is failed, result.stdout + result.stderr
    assert 'FinalizeProteinCadOutputs' not in result.stdout
    returned = tmp_path / "returned's outputs"
    canonical = returned / 'collected/protein_cad_native'
    if zero_yield:
        assert json.loads((canonical / 'design_manifest.json').read_text()) == []
        assert not (returned / 'pdb_files/sample.pdb').exists()
    else:
        assert (canonical / 'raw/pdbs/sample.pdb').read_text() == 'INERT NATIVE FIXTURE\n'
    assert not (returned / 'pdb_files').exists()
    if not failed:
        assert (returned / 'metadata/design_manifest.json').is_file()
    request = json.loads((returned / 'inputs/protein_cad/protein_cad_request.json').read_text())
    assert request['laproteina']['seed'] == 0 and request['laproteina']['self_cond'] is False
    assert request['disco']['seeds'] == 0 and request['disco']['effort'] == 'fast'
    if backend == 'disco':
        assert (canonical / 'native/sequence.txt').is_file()
        assert (canonical / 'disco_publication.json').is_file()
    else:
        assert (canonical / 'invocation_fixture/invocation.json').is_file()
        assert (canonical / 'laproteina_publication.json').is_file()

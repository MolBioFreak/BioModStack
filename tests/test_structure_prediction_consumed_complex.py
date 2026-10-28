"""Real entrypoint/channel/task/compiler, inert scientific executable only."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('source,contract', [('file', True), ('inline', True), ('sequence', True), ('file', False), ('sequence', False), ('metadata', True)])
def test_consumed_structure_input(tmp_path, source, contract):
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR', str(Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')))
    if not jar.is_file():
        pytest.skip('offline Nextflow distribution not installed')
    components = [{'type': 'protein', 'id': chain, 'sequence': seq}
                  for chain, seq in zip('ABCDE', ['ACDE', 'FGHI', 'KLMN', 'PQRS', 'TVWY'])]
    params = {'pred_method': 'esmfold2', 'run_frustrampnn': False, 'job_id': 'consumed-fixture',
              'sequence_name': 'five-chain', 'out_dir': str(tmp_path / 'out'),
              'code_root': str(ROOT), 'esmf_model_variant': 'full', 'esmf_seed': 42,
              'esmf_num_diffusion_samples': 1}
    retained = os.environ.get('BMS_TEST_ESMFOLD2_REQUEST')
    if retained and source in {'file', 'metadata'}:
        # Exercise the exact saved scientific request through its real compiler.
        sys.path.insert(0, str(ROOT / 'platform/api'))
        from services.nextflow import build_nextflow_command
        request = json.loads(Path(retained).read_text())['params']
        components = request['complex_components']
        command = build_nextflow_command('esmfold2', 'predict',
            dict(request, core_protein_scientific_contract=1, gpu_id=0),
            output_dir=str(tmp_path / 'compiled'), job_id='consumed-fixture', requested_params=request)
        for i, flag in enumerate(command[:-1]):
            if flag.startswith('--esmf_'):
                params[flag[2:]] = command[i + 1]
        # Nextflow parses CLI booleans/numbers, unlike params-file strings.
        for key, value in list(params.items()):
            if isinstance(value, str):
                try:
                    scalar = json.loads(value)
                    if not isinstance(scalar, (dict, list)):
                        params[key] = scalar
                except (ValueError, TypeError):
                    pass
    component_file = tmp_path / 'components.json'
    if retained and source in {'file', 'metadata'}:
        component_file = Path(params['esmf_complex_components_file'])
        components = json.loads(component_file.read_text())
    else:
        component_file.write_text(json.dumps(components))
    original = component_file.read_bytes()
    if source in {'file', 'metadata'}:
        params['esmf_complex_components_file'] = str(component_file)
    elif source == 'inline':
        params['esmf_complex_components'] = components
    else:
        params['sequence_input'] = 'ACDE'
    if contract:
        params['core_protein_scientific_contract'] = 1
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shim = bindir / 'python3'
    # Legacy process uses /scripts; contract process uses the real scripts root.
    shim.write_text(f'''#!/bin/sh
case "$1" in
/scripts/run_esmfold2_inference.py|{ROOT}/scripts/run_esmfold2_inference.py)
    shift; exec {shlex.quote(sys.executable)} {ROOT}/tests/fixtures/wp06_capture.py "$@";;
/scripts/bms_gpu_run_telemetry.py)
    shift; exec {shlex.quote(sys.executable)} {ROOT}/scripts/bms_gpu_run_telemetry.py "$@";;
{ROOT}/scripts/stage_reporter.py) exit 0;;
esac
exec {shlex.quote(sys.executable)} "$@"
''')
    shim.chmod(0o755)
    params['api_python'] = str(shim)
    config = tmp_path / 'fixture.config'
    config.write_text(f'''process.executor='local'
process.cpus=1
process.memory='256 MB'
process.ext.scripts_root='{ROOT}/scripts'
docker.enabled=false
singularity.enabled=false
''')
    pf = tmp_path / 'params.json'
    pf.write_text(json.dumps(params))
    env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'], NXF_OFFLINE='true',
               NXF_HOME=str(tmp_path / 'nxf'), NXF_PLUGINS_DEFAULT='',
               NXF_DISABLE_CHECK_LATEST='true', JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',
               BMS_NVIDIA_SMI='/bin/false', PYTHONDONTWRITEBYTECODE='1')
    entrypoint = ROOT / 'workflows/structure_prediction.nf'
    if source == 'metadata':
        entrypoint = tmp_path / 'metadata.nf'
        entrypoint.write_text(f'''nextflow.enable.dsl=2
include {{ structure_prediction_wf }} from '{ROOT}/modules/structure_prediction.nf'
workflow {{
    structure_prediction_wf(Channel.of(tuple('', 'five-chain')))
    structure_prediction_wf.out.canonical_structures.view {{ meta, predicted ->
        'PRODUCER_CAPTURE=' + groovy.json.JsonOutput.toJson(meta)
    }}
}}
''')
    result = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run',
                             str(entrypoint),
                             '-params-file', str(pf), '-work-dir', str(tmp_path / 'work')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    captures = list((tmp_path / 'work').glob('*/*/esmfold2_results/capture.json'))
    assert len(captures) == 1
    captured = json.loads(captures[0].read_text())
    parsed = captured['parsed']
    assert parsed['model_variant'] == 'full'
    assert parsed['seed'] == 42
    assert parsed['num_diffusion_samples'] == 1
    if source != 'sequence':
        assert parsed['sequence'] == ''
        component_rows = components.get('components', components.get('sequences', [])) if isinstance(components, dict) else components
        assert [(row['id'], row['sequence']) for row in captured['associations']] == [
            (row['id'], row['sequence']) for row in component_rows]
    else:
        assert parsed['sequence'] == 'ACDE'
    assert component_file.read_bytes() == original
    assert (captures[0].parent / 'fixture.cif').is_file()
    if contract:
        assert list((tmp_path / 'out/final/esmfold2').rglob('fixture.cif'))
    if source == 'metadata':
        line = next(line for line in result.stdout.splitlines() if line.startswith('PRODUCER_CAPTURE='))
        meta = json.loads(line.split('=', 1)[1])
        assert meta['producer_components'] == components
        assert 'producer_sequence' not in meta
        assert meta['original_submission_identity'] == {'id': 'five-chain', 'name': 'five-chain'}
        assert meta['producer_artifact_key'] == meta['producer_sample'] == 'five-chain'
        assert meta['producer_output_key'] == 'five-chain/fixture.cif'
        import hashlib
        assert meta['producer_artifact_sha256'] == hashlib.sha256((captures[0].parent / 'fixture.cif').read_bytes()).hexdigest()
    else:
        assert list((tmp_path / 'out/frustrampnn').glob('*terminal_manifest.json'))

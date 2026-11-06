"""Exercise the real direct workflow in offline Nextflow preview; no inference."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('source,predictor,accepted', [
    ('file', 'esmfold2', True),
    ('inline', 'esmfold2', True),
    ('sequence', 'esmfold2', True),
    ('missing', 'esmfold2', False),
    ('file', 'protenix', False),
])
def test_direct_entrypoint_native_input(tmp_path, source, predictor, accepted):
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR', str(Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')))
    if not jar.is_file():
        pytest.skip('offline Nextflow distribution not installed')
    components = [{'type': 'protein', 'id': chain, 'sequence': 'ACDE'} for chain in 'ABCDE']
    component_file = tmp_path / 'components.json'
    original = json.dumps(components).encode()
    component_file.write_bytes(original)
    params = {'pred_method': predictor, 'run_frustrampnn': False, 'job_id': 'entrypoint-fixture',
              'out_dir': str(tmp_path / 'out'), 'esmf_model_variant': 'full',
              'esmf_seed': 42, 'esmf_num_diffusion_samples': 1}
    if source == 'file':
        params['esmf_complex_components_file'] = str(component_file)
    elif source == 'inline':
        params['esmf_complex_components'] = components
    elif source == 'sequence':
        params['sequence_input'] = 'ACDE'
    params_file = tmp_path / 'params.json'
    params_file.write_text(json.dumps(params))
    config = tmp_path / 'empty.config'
    config.write_text('process.executor="local"\n')
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               NXF_HOME=str(tmp_path / 'nxf'), NXF_PLUGINS_DEFAULT='',
               JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2')
    run = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run',
                          str(ROOT / 'workflows/structure_prediction.nf'), '-preview',
                          '-params-file', str(params_file), '-work-dir', str(tmp_path / 'work')],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    output = run.stdout + run.stderr
    assert (run.returncode == 0) is accepted, output
    if accepted:
        assert 'Structure Prediction Workflow' in output
    else:
        assert '--sequence_input is required' in output
    assert component_file.read_bytes() == original

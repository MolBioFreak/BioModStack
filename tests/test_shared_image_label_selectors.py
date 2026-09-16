"""Exercise real deferred Nextflow container selection without image execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('selection', ['environment', 'params', 'fallback'])
def test_deferred_scientific_labels(tmp_path, selection):
    configured = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    jars = [Path(configured)] if configured else sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    if not jars or not shutil.which('java'):
        pytest.skip('installed Nextflow and Java required; no dependency downloads')
    (tmp_path / 'main.nf').write_text('''nextflow.enable.dsl=2
process ProbeProtenix {
    label 'Protenix'
    output: path('protenix.txt')
    script:
    """printf '%s' '${task.container}' > protenix.txt"""
}
process ProbeConfornets {
    label 'ConforNetsCanonical'
    output: path('confornets.txt')
    script:
    """printf '%s' '${task.container}' > confornets.txt"""
}
process ProbeExperimentalConfornets {
    label 'ConforNets'
    output: path('experimental.txt')
    script:
    """printf '%s' '${task.container}' > experimental.txt"""
}
workflow { ProbeProtenix(); ProbeConfornets(); ProbeExperimentalConfornets() }
''')
    (tmp_path / 'nextflow.config').write_text((REPO / 'nextflow.config').read_text() + '''
apptainer.enabled = false
singularity.enabled = false
docker.enabled = false
process.executor = 'local'
process.cpus = 1
process.memory = '256 MB'
''')
    env = {k: v for k, v in os.environ.items() if k not in
           ('BMS_PROTENIX_CONTAINER_PATH', 'BMS_CM_CONFORNETS_CONTAINER_PATH')}
    env.update(NXF_OFFLINE='true', NXF_HOME=str(tmp_path / 'nxf'),
               BMS_DATA=str(tmp_path / 'data'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
               BMS_HOME=str(REPO), BMS_CONTAINER_DIR=str(tmp_path / 'containers'))
    params = {'container_dir': str(tmp_path / 'containers')}
    if selection == 'fallback':
        expected = [str(tmp_path / 'containers' / name) for name in
                    ('protenix.sif', 'confornets-canonical.sif')]
    else:
        expected = [str(tmp_path / 'shared/objects/sha256' / digest / 'runtime.sif')
                    for digest in ('a' * 64, 'b' * 64)]
        env.update(BMS_PROTENIX_CONTAINER_PATH=expected[0],
                   BMS_CM_CONFORNETS_CONTAINER_PATH=expected[1])
        if selection == 'params':
            expected = [value.replace('/shared/', '/explicit/') for value in expected]
            params.update(protenix_container_path=expected[0], cm_confornets_container_path=expected[1])
    # Experimental Confornets is a distinct scientific build: CM's environment
    # selector must never choose it. Its explicit system parameter remains separate.
    experimental = str(tmp_path / 'containers/confornets.sif')
    if selection == 'params':
        experimental = str(tmp_path / 'experimental/objects/sha256' / ('c' * 64) / 'runtime.sif')
        params['cn_container_path'] = experimental
    expected.append(experimental)
    (tmp_path / 'params.json').write_text(json.dumps(params))
    run = subprocess.run(['java', '-jar', str(jars[-1]), 'run', 'main.nf', '-offline',
                          '-params-file', 'params.json'], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    for filename, value in zip(('protenix.txt', 'confornets.txt', 'experimental.txt'), expected):
        results = list((tmp_path / 'work').rglob(filename))
        assert len(results) == 1
        assert results[0].read_text() == value
    assert not list((tmp_path / 'work').rglob('*.sif'))

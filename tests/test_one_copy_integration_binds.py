"""Real Groovy/Nextflow bind serialization; no scientific container execution."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


def _jar():
    configured = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    jars = [Path(configured)] if configured else sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    if not jars or not jars[-1].is_file() or not shutil.which('java'):
        pytest.skip('existing Nextflow JAR and Java required; no test downloads')
    return jars[-1]


@pytest.mark.parametrize('backend', ['apptainer', 'udocker', None])
@pytest.mark.parametrize('guest', ['/native/weights', None])
def test_weight_bind_serializes_readonly_native_and_original_names(tmp_path, backend, guest):
    from tools.bms_container import parse_exec
    config = (REPO / 'nextflow.config').read_text()
    method = config[config.index('def weightBind('):config.index('def gpuPrefix(')]
    source = str(tmp_path / 'weights')
    call = 'weightBind(' + json.dumps(source) + ', ' + json.dumps(guest) + ')'
    script = method + '\nprintln(groovy.json.JsonOutput.toJson(' + call + '))'
    env = dict(os.environ)
    env.pop('BMS_SHARED_WEIGHTS_ROOT', None)
    if backend:
        env['BMS_CONTAINER_BACKEND'] = backend
    else:
        env.pop('BMS_CONTAINER_BACKEND', None)
    result = subprocess.run(['java', '-cp', str(_jar()), 'groovy.ui.GroovyMain', '-e', script],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    bind = json.loads(result.stdout.strip())
    invocation = parse_exec(['--bind', bind, '/fixture.sif', 'true'])
    expected = [(source, guest or source, 'ro')]
    if guest:
        expected.append((source, source, 'ro'))
    assert invocation.binds == expected


def test_actual_nextflow_config_keeps_weight_modes_and_environment_values_separate(tmp_path):
    weights = tmp_path / 'weights'
    weights.mkdir()
    env = dict(os.environ, BMS_HOME=str(REPO), BMS_WEIGHTS=str(weights),
               BMS_DATA=str(tmp_path / 'data'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
               NXF_HOME=str(tmp_path / 'nextflow'), NXF_OFFLINE='true', NXF_ANSI_LOG='false')
    env.pop('BMS_SHARED_WEIGHTS_ROOT', None)
    env.pop('BMS_CONTAINER_BACKEND', None)
    result = subprocess.run(['java', '-jar', str(_jar()), '-log', str(tmp_path / 'nextflow.log'),
                             '-C', str(REPO / 'nextflow.config'), 'config', '-flat'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'{weights}/protenix:/protenix_weights:ro,{weights}/protenix:{weights}/protenix:ro' in result.stdout
    assert f'BMS_HOST_WEIGHTS={weights} ' in result.stdout
    assert f'BMS_WEIGHTS={weights} ' in result.stdout
    assert f'BMS_HOST_WEIGHTS={weights}:' not in result.stdout
    assert f'BMS_WEIGHTS={weights}:' not in result.stdout
    config = (REPO / 'nextflow.config').read_text()
    # More-specific protection follows ordinary writable code/data/cache mounts.
    for body in re.findall(r'"""(.*?)"""', config, re.S):
        if 'weightBind(' not in body:
            continue
        lines = body.splitlines()
        weight_indices = [i for i, line in enumerate(lines) if 'weightBind(' in line]
        writable_indices = [i for i, line in enumerate(lines)
                            if '--bind ${params.code_root}' in line or '--bind ${params.data_root' in line]
        assert not writable_indices or min(weight_indices) > max(writable_indices)

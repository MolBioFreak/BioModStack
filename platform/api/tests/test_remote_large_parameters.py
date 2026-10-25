"""Exercise native large settings transport without scientific substitution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from services.remote_execution.bundle import _file_backed_nextflow_parameters


def test_large_parameter_preserves_exact_string_and_other_arguments():
    value = json.dumps({'complex_components': [{'msa': 'a' * 5500000}]})
    original = ['nextflow', 'run', 'workflow.nf', '--requested_json', value,
                '--seed', '42', '--empty', '', '-profile', 'protenix']
    command, parameters = _file_backed_nextflow_parameters(original)
    assert command == ['nextflow', 'run', 'workflow.nf', '--seed', '42', '--empty', '', '-profile', 'protenix']
    assert parameters == {'requested_json': value}
    assert json.loads(json.dumps(parameters))['requested_json'] == value
    assert original[4] == value


@pytest.mark.skipif(not os.getenv('BMS_RELOCATION_TEST_NEXTFLOW_JAR'), reason='installed native parser opt-in')
def test_installed_nextflow_params_file_retains_multimegabyte_string(tmp_path):
    value = json.dumps({'complex_components': [{'msa': 'a' * 5500000}]})
    params = tmp_path / 'large.json'
    params.write_text(json.dumps({'requested_json': value}))
    workflow = tmp_path / 'probe.nf'
    workflow.write_text("workflow {\n println params.requested_json.class.name\n println java.security.MessageDigest.getInstance('SHA-256').digest(params.requested_json.getBytes('UTF-8')).encodeHex().toString()\n}\n")
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_HOME=str(tmp_path/'nxf-home'))
    result = subprocess.run(['java', '-jar', os.environ['BMS_RELOCATION_TEST_NEXTFLOW_JAR'],
                             'run', str(workflow), '-params-file', str(params)],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'java.lang.String' in result.stdout
    assert hashlib.sha256(value.encode()).hexdigest() in result.stdout

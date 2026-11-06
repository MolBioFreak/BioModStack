"""Qualify the retained optional process without enabling it or running AF2."""
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('params,expected', [({}, 'true'), ({'af2_backprop_use_multimer': False}, 'false'), ({'af2_backprop_use_multimer': True}, 'true')])
def test_actual_optional_process_preserves_explicit_multimer_false(tmp_path, params, expected):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Select the existing offline Nextflow/Groovy jar')
    source = (ROOT / 'modules/af2_backprop.nf').read_text()
    body = source.split('process AF2_BACKPROP {', 1)[1].split('    script:\n', 1)[1].rsplit('\n}', 1)[0]
    params = {'code_root': '/source', **params}
    script = tmp_path / 'render.groovy'
    script.write_text('def params = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(params)) + ')\n'
                      "def meta=[id:'fixture']; def complex_pdb='fixture.pdb'\n"
                      'def rendered = { ->\n' + body + '\n}.call()\nprint rendered\n')
    output = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)], capture_output=True, text=True, check=True).stdout
    command = output.split('python3 ', 1)[1].split('2>&1', 1)[0]
    argv = shlex.split(command.replace('\\\n', ' '))
    assert argv[argv.index('--use_multimer') + 1] == expected
    # This change does not alter workflow activation or default execution.
    assert 'if (params.run_af2_backprop == true)' in (ROOT / 'workflows/antibody_denovo.nf').read_text()

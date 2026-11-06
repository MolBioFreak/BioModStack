"""Real pinned Nextflow shutdown callbacks, isolated from scientific execution.

Reuse each owner's terminal reporting block verbatim; only the scientific graph
is replaced by one local process. The reporter interpreter records argv instead
of contacting an API. A callback-entry witness makes failure-path coverage real.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('owner', ['wf_clone_validation', 'ont_construct_screening'])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_named_workflow_completion_metadata(tmp_path, owner, exit_code):
    configured = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not configured:
        pytest.skip('set BMS_TEST_NEXTFLOW_JAR to the pinned Nextflow 25.10.0 one-jar')
    jar = Path(configured)
    assert jar.is_file()
    java = shutil.which('java')
    assert java
    env = {**os.environ, 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true',
           'NXF_HOME': str(tmp_path / 'nxf-home')}
    version = subprocess.run([java, '-jar', str(jar), '-version'], env=env,
                             capture_output=True, text=True, timeout=30)
    assert version.returncode == 0, version.stdout + version.stderr
    assert 'version 25.10.0' in version.stdout + version.stderr

    source = (ROOT / f'workflows/ngs/{owner}.nf').read_text()
    # Capture both registration and any immediately preceding metadata binding,
    # retaining the actual owner closure and its lexical named-workflow scope.
    start = source.index('    workflow.onComplete {')
    # Include immediately preceding local captures verbatim, never synthesize a fix.
    while start:
        previous = source.rfind('\n', 0, start - 1) + 1
        if not source[previous:start].startswith('    def completion'):
            break
        start = previous
    terminal = source[start:]
    terminal = terminal[:terminal.index('\n\n}')]
    witness = tmp_path / 'callback-entered'
    terminal = terminal.replace('workflow.onComplete {',
        'workflow.onComplete {\n        new File(' + json.dumps(str(witness)) + ').text = "entered"', 1)

    argv_file = tmp_path / 'reporter-argv.jsonl'
    reporter = tmp_path / 'managed-python'
    reporter.write_text('#!' + sys.executable + '\n'
                        'import json, sys\n'
                        'with open(' + repr(str(argv_file)) + ', "a") as out:\n'
                        '    out.write(json.dumps(sys.argv[1:]) + "\\n")\n')
    reporter.chmod(0o755)
    status = tmp_path / 'native-success'
    script = tmp_path / 'probe.nf'
    script.write_text(
        "include { reportStage } from '" + str(ROOT / 'modules/ngs/stage_reporting') + "'\n"
        'params.job_id = "isolated-completion-regression"\n'
        'params.code_root = ' + json.dumps(str(ROOT)) + '\n'
        'params.out_dir = ' + json.dumps(str(tmp_path / 'out')) + '\n'
        'params.pod5_python = ' + json.dumps(str(reporter)) + '\n'
        'params.comparison_panel_snapshot = true\n'
        'process Inert {\n  script:\n  """\n  exit ' + str(exit_code) + '\n  """\n}\n'
        'workflow NamedOwner {\n    def runFastqQc = true\n    def runAssembly = true\n'
        '    Inert()\n' + terminal + '\n}\n'
        'workflow { NamedOwner() }\n'
        'def observedWorkflow = workflow\n'
        'workflow.onComplete { new File(' + json.dumps(str(status)) +
        ').text = observedWorkflow.success.toString() }\n')
    log_file = tmp_path / 'nextflow.log'
    result = subprocess.run([java, '-jar', str(jar), '-log', str(log_file),
                             'run', str(script), '-offline', '-ansi-log', 'false'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    diagnostic = result.stdout + '\n' + result.stderr + '\n' + log_file.read_text()
    assert witness.read_text() == 'entered', diagnostic
    assert 'NullPointerException' not in diagnostic, diagnostic
    assert 'Cannot get property' not in diagnostic, diagnostic
    assert 'Failed to invoke `workflow.onComplete`' not in diagnostic, diagnostic
    assert status.read_text() == ('true' if exit_code == 0 else 'false'), diagnostic
    if exit_code:
        assert result.returncode != 0, diagnostic
        assert 'Process `NamedOwner:Inert` terminated with an error exit status (7)' in diagnostic
        assert not argv_file.exists(), 'Failed workflows must not report successful stages'
        # Compare with the same native process without the reporting callback:
        # the repair must not mask or replace Nextflow's original failure code.
        control = tmp_path / 'control'
        control.mkdir()
        control_script = control / 'probe.nf'
        control_script.write_text(script.read_text().replace(terminal, ''))
        native = subprocess.run([java, '-jar', str(jar), 'run', str(control_script),
                                 '-offline', '-ansi-log', 'false'],
                                cwd=control, env=env, capture_output=True, text=True, timeout=90)
        assert 'error exit status (7)' in native.stdout + native.stderr
        assert result.returncode == native.returncode, native.stdout + native.stderr
    else:
        assert result.returncode == 0, diagnostic
        reports = [json.loads(line) for line in argv_file.read_text().splitlines()]
        expected_stages = ['wf_clone_validation', 'construct_verification', 'fastq_qc', 'dimer_qc']
        if owner == 'ont_construct_screening':
            expected_stages.append('comparison_panel')
        assert [row[2] for row in reports] == expected_stages
        for row in reports:
            assert row[:2] == [str(ROOT / 'scripts/stage_reporter.py'),
                               'isolated-completion-regression']
            assert row[3] == 'complete'
            assert all(path.startswith(str(tmp_path / 'out') + '/') for path in row[4:])
            assert row[4:]

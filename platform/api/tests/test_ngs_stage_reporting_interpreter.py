"""Run the actual Nextflow reporting function with a managed interpreter selector."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('selection', ['parameter', 'environment'])
def test_stage_reporter_uses_managed_python_not_clean_host(tmp_path, selection):
    configured = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not configured:
        pytest.skip('set BMS_TEST_NEXTFLOW_JAR for native Nextflow regression')
    jar = Path(configured)
    assert jar.is_file()
    java = shutil.which('java')
    assert java
    marker = tmp_path / 'reporter-argv.txt'
    managed = tmp_path / 'managed-python'
    managed.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > '+str(marker)+'\n')
    managed.chmod(0o755)
    shadow = tmp_path / 'shadow'
    shadow.mkdir()
    (shadow / 'python3').write_text('#!/bin/sh\nexit 42\n')
    (shadow / 'python3').chmod(0o755)
    selector = "params.pod5_python = '"+str(managed)+"'\n" if selection == 'parameter' else ''
    script = tmp_path / 'probe.nf'
    script.write_text(
        "include { reportStage } from '"+str(ROOT/'modules/ngs/stage_reporting')+"'\n"
        "params.job_id = 'installation-check'\n"
        "params.code_root = '"+str(ROOT)+"'\n"+selector+
        "workflow { reportStage(params, 'dorado_basecall', ['output.bam']) }\n")
    env = {**os.environ, 'NXF_OFFLINE':'true', 'NXF_HOME':str(tmp_path/'nxf-home'),
           'PATH':str(shadow)+os.pathsep+os.environ['PATH']}
    if selection == 'environment':
        env['BMS_API_PYTHON'] = str(managed)
    else:
        env['BMS_API_PYTHON'] = str(shadow/'python3')
    result = subprocess.run([java, '-jar', str(jar), 'run', str(script)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    assert marker.read_text().splitlines() == [str(ROOT/'scripts/stage_reporter.py'),
                                              'installation-check', 'dorado_basecall', 'complete', 'output.bam']

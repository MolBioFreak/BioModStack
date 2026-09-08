"""Exercise the antibody process selector with real offline Nextflow parsing.

No inference: only the script/output are substituted with a transport probe.
The label change in nextflow.config is integration-owned and deliberately not
reimplemented here; this verifies the module's own deferred selector.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_antibody_process_transports_configured_image_without_original(tmp_path):
    jars = sorted((Path.home() / '.nextflow/framework').glob('*/*-one.jar'))
    if not jars or not shutil.which('java'):
        pytest.skip('installed Nextflow and Java required; no downloads')
    source = (REPO / 'modules/antibody_batch.nf').read_text()
    selector = source.split('process BatchProtenixValidation {', 1)[1].split('    publishDir', 1)[0]
    harness = 'process BatchProtenixValidation {' + selector + '''
    output:
    path 'transport.txt'
    script:
    """
    printf '%s' "\\$APPTAINER_CONTAINER" > transport.txt
    """
}
workflow { BatchProtenixValidation() }
'''
    (tmp_path / 'main.nf').write_text(harness)
    selected = tmp_path / 'store/objects/sha256/test/runtime.sif'
    selected.parent.mkdir(parents=True)
    selected.write_bytes(b'offline synthetic image; never mounted')
    binary = tmp_path / 'bin'
    binary.mkdir()
    executable = binary / 'apptainer'
    executable.write_text(f'''#!{sys.executable}
import os, subprocess, sys
args = sys.argv[1:]
if '--version' in args or 'version' in args:
    print('apptainer version 1.4.0')
    raise SystemExit(0)
i = next(i for i, a in enumerate(args) if a.endswith('.sif'))
raise SystemExit(subprocess.run(args[i+1:], env=os.environ | {{'APPTAINER_CONTAINER': args[i]}}).returncode)
''')
    executable.chmod(0o755)
    (tmp_path / 'nextflow.config').write_text('''
apptainer.enabled = true
apptainer.autoMounts = true
process.executor = 'local'
process.cpus = 1
process.memory = '256 MB'
''')
    (tmp_path / 'params.json').write_text(json.dumps({'container_dir': str(tmp_path / 'absent'),
                                                   'protenix_container_path': str(selected)}))
    result = subprocess.run(['java', '-jar', str(jars[-1]), 'run', 'main.nf', '-offline',
                             '-params-file', 'params.json'], cwd=tmp_path,
                            env=os.environ | {'NXF_OFFLINE': 'true', 'NXF_HOME': str(tmp_path / 'nxf'),
                                              'PATH': str(binary) + os.pathsep + os.environ['PATH']},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = list((tmp_path / 'work').rglob('transport.txt'))
    assert len(outputs) == 1
    assert outputs[0].read_text() == str(selected)
    assert not (tmp_path / 'absent/protenix.sif').exists()

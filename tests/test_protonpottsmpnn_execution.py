"""Real Nextflow staging/CPU/publication harness, not a scientific inference test.

BMS_NEXTFLOW_JAR permits the Java launcher when the installed Docker wrapper
cannot write the isolated worktree. Apptainer is an explicit command-boundary
fixture; native inference is qualified separately by the runtime owner.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_nextflow_real_cpu_staging_and_publication(tmp_path):
    jar = os.environ.get('BMS_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Set BMS_NEXTFLOW_JAR for the real Java Nextflow harness')
    binary = tmp_path/'bin'
    binary.mkdir()
    apptainer = binary/'apptainer'
    apptainer.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
args=sys.argv[1:]
assert '--nv' not in args
assert 'CUDA_VISIBLE_DEVICES=' in args
request=pathlib.Path(args[args.index('--request')+1])
source=pathlib.Path(args[args.index('--input')+1])
assert request.name=='prepared_request.json'
assert source.is_file() and source.read_text()=='retained structure bytes'
assert args[args.index('--n-jobs')+1]=='2'
out=pathlib.Path(args[args.index('--out')+1]);out.mkdir()
(out/'manifest.json').write_text(json.dumps({'harness':'command-boundary-only','request':json.loads(request.read_text()),'source_bytes':source.read_text()}))
''')
    apptainer.chmod(0o755)
    source = tmp_path/'selected source.pdb'
    source.write_text('retained structure bytes')
    request = tmp_path/'request.json'
    request.write_text(json.dumps({'harness':'not scientific output'}))
    config = tmp_path/'harness.config'
    config.write_text('process.executor="local"\nprocess.cpus=2\nprocess.memory="512 MB"\nprocess.container=null\n')
    out = tmp_path/'published'
    run = subprocess.run(['java','-jar',jar,'-C',str(config),'run',str(ROOT/'workflows/protonpottsmpnn_design.nf'),
        '--protonpottsmpnn_design_request',str(request),'--protonpottsmpnn_design_input',str(source),
        '--out_dir',str(out),'--code_root',str(ROOT),'--container_dir',str(tmp_path/'containers'),
        '-work-dir',str(tmp_path/'work')],cwd=tmp_path,env={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH'],
        'NXF_HOME':str(tmp_path/'nxf-home'),'NXF_OFFLINE':'true'},capture_output=True,text=True,timeout=120)
    assert run.returncode == 0, run.stdout+'\n'+run.stderr
    assert json.loads((out/'protonpottsmpnn_design/manifest.json').read_text())['source_bytes']=='retained structure bytes'
    commands=list((tmp_path/'work').glob('*/*/.command.sh'))
    assert len(commands)==1
    assert '--n-jobs 2' in commands[0].read_text()
    assert '--nv' not in commands[0].read_text()

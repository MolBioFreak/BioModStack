"""Real Nextflow wiring plus optional shipped-checkpoint execution.

The command fixture checks GPU binding, not GPU inference. Set
BMS_PROTON_RUNTIME_IMAGE, INPUT and REQUEST to exercise the actual native image.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launch(tmp_path, source, request, image, *, device='cpu', env=None):
    jar = os.environ.get('BMS_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Set BMS_NEXTFLOW_JAR for the real Java Nextflow harness')
    config = tmp_path/'harness.config'
    config.write_text('process.executor="local"\nprocess.cpus=2\nprocess.memory="512 MB"\nprocess.container=null\n')
    out = tmp_path/'published'
    run = subprocess.run(['java','-jar',jar,'-C',str(config),'run',str(ROOT/'workflows/protonpottsmpnn_design.nf'),
        '--protonpottsmpnn_design_request',str(request),'--protonpottsmpnn_design_input',str(source),
        '--protonpottsmpnn_device',device, '--protonpottsmpnn_container_path',str(image), '--gpu_id','3',
        '--out_dir',str(out),'--code_root',str(ROOT),'--container_dir',str(tmp_path/'containers'),
        '-work-dir',str(tmp_path/'work')],cwd=tmp_path,
        env={**os.environ,'NXF_HOME':str(tmp_path/'nxf-home'),'NXF_OFFLINE':'true',
             'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1', **(env or {})},
        capture_output=True,text=True,timeout=240)
    assert run.returncode == 0, run.stdout+'\n'+run.stderr
    commands=list((tmp_path/'work').glob('*/*/.command.sh'))
    assert len(commands)==1
    return out/'protonpottsmpnn_design', commands[0].read_text()


@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_nextflow_real_staging_placement_and_publication(tmp_path, device):
    binary = tmp_path/'bin'
    binary.mkdir()
    apptainer = binary/'apptainer'
    apptainer.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
cuda=os.environ['BMS_TEST_NATIVE_DEVICE']=='cuda'
assert ('--nv' in args)==cuda
assert ('CUDA_VISIBLE_DEVICES=3' if cuda else 'CUDA_VISIBLE_DEVICES=') in args
assert args[args.index('--device')+1]==('cuda:0' if cuda else 'cpu')
assert os.environ['BMS_SELECTED_IMAGE_PROTONPOTTSMPNN_SIF'] in args
request=pathlib.Path(args[args.index('--request')+1])
source=pathlib.Path(args[args.index('--input')+1])
assert request.name=='prepared_request.json'
assert source.is_file() and source.read_text()=='retained structure bytes'
assert args[args.index('--n-jobs')+1]=='2'
out=pathlib.Path(args[args.index('--out')+1])/'protonpottsmpnn_design';out.mkdir()
(out/'manifest.json').write_text(json.dumps({'harness':'command-boundary-only','request':json.loads(request.read_text()),'source_bytes':source.read_text()}))
''')
    apptainer.chmod(0o755)
    source = tmp_path/'selected source.pdb'
    source.write_text('retained structure bytes')
    request = tmp_path/'request.json'
    request.write_text(json.dumps({'harness':'not scientific output'}))
    selected = tmp_path/'selected immutable image.sif'
    folder, command = launch(tmp_path, source, request, tmp_path/'unused.sif', device=device,
        env={'PATH':str(binary)+os.pathsep+os.environ['PATH'], 'BMS_TEST_NATIVE_DEVICE':device,
             'BMS_SELECTED_IMAGE_PROTONPOTTSMPNN_SIF':str(selected)})
    assert json.loads((folder/'manifest.json').read_text())['source_bytes']=='retained structure bytes'
    assert '--n-jobs 2' in command
    assert ('--nv' in command)==(device=='cuda')


def test_nextflow_actual_checkpoint_outputs_are_readable_by_bms(tmp_path, monkeypatch):
    image = os.environ.get('BMS_PROTON_RUNTIME_IMAGE')
    input_path = os.environ.get('BMS_PROTON_RUNTIME_INPUT')
    request_path = os.environ.get('BMS_PROTON_RUNTIME_REQUEST')
    if not all((image, input_path, request_path)):
        pytest.skip('Set BMS_PROTON_RUNTIME_IMAGE, INPUT and REQUEST for actual native inference')
    import sys
    sys.path.insert(0, str(ROOT/'platform/api'))
    from scripts.lib.protonpottsmpnn_contract import normalize_request
    from services.protonpottsmpnn_design import read_design_result
    document = json.loads(Path(request_path).read_text())
    document['options'].pop('write_states_fasta', None)
    document = normalize_request(document)
    source = tmp_path/'actual retained complex.pdb'
    source.write_bytes(Path(input_path).read_bytes())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == document['source']['sha256']
    request = tmp_path/'request.json'
    request.write_text(json.dumps(document))
    monkeypatch.delenv('BMS_SELECTED_IMAGE_PROTONPOTTSMPNN_SIF', raising=False)
    folder, command = launch(tmp_path, source, request, image)
    result = read_design_result(folder)
    assert result['request'] == document
    assert len(result['designs']) == 2
    assert result['runtime']['device']=='cpu'
    assert result['runtime']['checkpoint']['sha256']=='a39872250c0b8eb4c0b4cb6472a6edad0d6f6134a50c28b611386e83c592fe7b'
    assert all(len(row['native']['canonical_sequence'])==114 for row in result['designs'])
    assert all(row['native']['energy_trajectory'] for row in result['designs'])
    assert (folder/'designs.fasta').is_file()
    assert (folder/'designs_states.fasta').is_file()
    assert not (folder/'protonpottsmpnn_design').exists()
    assert '--nv' not in command

"""Prepare real RFD3 source closures; no sampler or model execution."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from test_rfd3_bounded_corrections import ROOT, cif_source, generation


@pytest.mark.parametrize('mapped', [False, True])
@pytest.mark.parametrize('kind', ['generation', 'local_redesign'])
def test_prepared_source_survives_real_task_relocation(tmp_path, mapped, kind):
    source = tmp_path / 'original.cif'
    source.write_text(cif_source())
    original = source.read_bytes()
    source_binding = {'path': str(source), 'sha256': hashlib.sha256(original).hexdigest()}
    work = tmp_path / 'prepare'
    work.mkdir()
    native = work / 'native.json'
    receipt = work / 'receipt.json'
    if kind == 'generation':
        request = generation()
        request['source'] = source_binding
        extra = ['--expected-min-length', '1', '--expected-max-length', '2',
                 '--expected-num-designs', '2', '--expected-seed', '0', '--expected-dump-trajectories', 'false']
    else:
        request = {'schema': 'bms.rfd3.local-redesign.request.v1', 'input': source_binding,
                   'rfd3': {'input': str(source), 'contig': 'A2'}, 'sequence_policy': 'skip',
                   'redesign_mode': 'native_recall'}
        extra = ['--input-structure', str(source)]
    if mapped:
        request['source_mapping'] = 'bms.rfd3.source-map.v1'
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request))
    subprocess.run([sys.executable, str(ROOT / f'scripts/rfd3_{kind}/prepare_native_input.py'),
        '--request', str(request_path), '--output-native', str(native), '--output-receipt', str(receipt),
        '--source-dir', str(work / 'rfd3_sources'), *extra], cwd=work, check=True)
    returned = tmp_path / 'next-task'
    returned.mkdir()
    # Exactly the tuple to be staged by the shared workflow owner, not the
    # original input or an absolute controller path.
    shutil.copy2(native, returned / 'native.json')
    shutil.copytree(work / 'rfd3_sources', returned / 'rfd3_sources')
    observed = json.loads(receipt.read_text())
    source.unlink()
    shutil.rmtree(work)
    payload = next(iter(json.loads((returned / 'native.json').read_text()).values()))
    staged = returned / payload['input']
    assert staged.is_file() and not Path(payload['input']).is_absolute()
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == observed['runtime_input']['sha256']
    if mapped:
        assert observed['source_mapping']['original_sha256'] == hashlib.sha256(original).hexdigest()
        assert {a['source']['component'] for a in observed['source_mapping']['atoms']} == {'ALA', 'GLY', 'ZN', 'DA'}
    else:
        assert staged.read_bytes() == original

"""Dorado task uses scheduler binding and native allocation, not copied VRAM floors."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('selection,accepted', [('cuda:0', True), ('cuda:1', True), ('cuda:all', True), ('cuda:2', False), ('cuda:1,1', False)])
def test_device_binding_survives_without_vram_probe(tmp_path, selection, accepted):
    text = (ROOT / 'modules/ngs/dorado_basecall.nf').read_text()
    block = text[text.index('    visible_gpus='):text.index('    pod5_root=')].replace('\\$', '$')
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + block], cwd=tmp_path,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES='GPU-bound-a,GPU-bound-b', device=selection),
        capture_output=True, text=True)
    assert (result.returncode == 0) == accepted
    assert 'nvidia-smi' not in text
    assert 'min_gpu_total' not in text and 'min_gpu_free' not in text
    lock = json.loads((ROOT / 'config/ngs/dorado_v2.1.2.lock.json').read_text())
    assert 'min_gpu_total_mib' not in lock['policy']
    assert 'min_gpu_free_mib' not in lock['policy']
    assert lock['policy']['default_batch_size'] == {'simplex': 64, 'duplex': 32}

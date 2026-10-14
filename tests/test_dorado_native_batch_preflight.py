"""Batch policy checks without duplicating chemistry or asset qualification."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('batch_preflight', ROOT / 'scripts/dorado_p4_preflight.py')
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def build(root, value, mode='simplex'):
    return preflight.build_preflight(
        lock_path=ROOT / 'config/ngs/dorado_v2.1.2.lock.json', pod5_root=root,
        molecule='dna', quality='fast', mode=mode, model_root=root, runtime_sif=root/'image.sif',
        batch_size=value, pairs=root/'pairs.txt' if mode == 'duplex' else None,
        verify_assets=False,
    )


@pytest.mark.parametrize('value', [-1, 0.5, 1.5, True, '0'])
def test_preflight_rejects_negative_and_noninteger_before_input_access(tmp_path, value):
    with pytest.raises(ValueError, match='integer'):
        build(tmp_path, value)


@pytest.mark.parametrize('mode,default', [('simplex', 64), ('duplex', 32)])
def test_preflight_preserves_auto_large_and_defaults(tmp_path, monkeypatch, mode, default):
    # This isolates the batch policy; real POD5/native controls run separately.
    monkeypatch.setattr(preflight, '_pod5_files', lambda *_: [])
    monkeypatch.setattr(preflight, '_read_pod5_inventory', lambda *_: ({}, set()))
    monkeypatch.setattr(preflight, '_validate_chemistry', lambda *_: None)
    monkeypatch.setattr(preflight, '_validate_pairs', lambda *_: {'pair_count': 1})
    (tmp_path/'pairs.txt').write_text('fixture')
    for value, expected in [(None, default), (0, 0), (512, 512), (1000000, 1000000)]:
        payload = build(tmp_path, value, mode)
        path = tmp_path/'preflight.json'
        preflight._atomic_json(path, payload)
        assert json.loads(path.read_text())['execution_policy']['batch_size'] == expected

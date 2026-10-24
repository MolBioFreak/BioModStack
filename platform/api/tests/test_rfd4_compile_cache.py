"""Offline placement tests; inert GPU metadata only, no compilation/inference."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from bms_rfd4 import cache, run as runner


@pytest.fixture
def placement(tmp_path, monkeypatch):
    for key in (*cache.CACHE_KEYS, 'BMS_RFD4_COMPILE_CACHE_ROOT', 'BMS_RFD4_RUNTIME_IMAGE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('BMS_RFD4_RUNTIME_IMAGE', '/objects/sha256/' + 'a' * 64 + '/runtime.sif')
    monkeypatch.setenv('BMS_RFD4_COMPILE_CACHE_ROOT', str(tmp_path / 'worker-cache'))
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '2')
    def inert(command, **kwargs):
        assert command[-1] == '--id=2'
        return subprocess.CompletedProcess(command, 0, '8.9, 580.1\n', '')
    monkeypatch.setattr(cache.subprocess, 'run', inert)
    return {'input': {'kind': 'unconditional', 'name': 'x', 'chains': [
        {'chain_id': 'A', 'min_length': 12, 'max_length': 12}]},
        'settings': {'precision': 'bf16-mixed', 'compile_lengths': [128]}}


def test_persistent_native_paths_and_private_tuning_across_attempts(placement, tmp_path):
    before = dict(os.environ)
    reports = []
    for attempt in ('one', 'two'):
        with cache.compile_environment(placement, tmp_path / attempt / 'output') as report:
            reports.append(report)
            assert all(Path(os.environ[key]).is_dir() for key in (*cache.SHARED, *cache.PRIVATE))
            assert all(not Path(value).is_relative_to(tmp_path / attempt / 'output')
                       for value in report['effective'].values())
        assert dict(os.environ) == before
    for key in cache.SHARED:
        assert reports[0]['effective'][key] == reports[1]['effective'][key]
        assert not reports[0]['private_reasons'].get(key)
    for key in cache.PRIVATE:
        assert reports[0]['effective'][key] != reports[1]['effective'][key]
    assert 'CUDA_CACHE_PATH' not in reports[0]['effective']  # driver owns its native cache
    assert 'BIOIR_CACHE' not in reports[0]['effective']  # not a JIT cache


@pytest.mark.parametrize('field,value', [('precision', '32-true'), ('compile_lengths', [256]),
                                        ('nn', {'torch_compile_mode': 'reduce-overhead'}),
                                        ('bioir', {'dtype': 'bfloat16'})])
def test_configuration_namespace_isolation(placement, monkeypatch, field, value):
    original = cache.runtime_namespace(placement, os.environ)[0]
    changed = {**placement, 'settings': {**placement['settings'], field: value}}
    assert cache.runtime_namespace(changed, os.environ)[0] != original
    monkeypatch.setenv('BMS_RFD4_RUNTIME_IMAGE', '/objects/' + 'b' * 64 + '/runtime.sif')
    assert cache.runtime_namespace(placement, os.environ)[0] != original


def test_unknown_architecture_and_unwritable_root_fall_back_not_gate(placement, tmp_path, monkeypatch):
    blocked = tmp_path / 'file-not-directory'
    blocked.write_text('inert')
    monkeypatch.setenv('BMS_RFD4_COMPILE_CACHE_ROOT', str(blocked))
    with cache.compile_environment(placement, tmp_path / 'attempt/output') as report:
        assert all(Path(report['effective'][key]).is_relative_to(tmp_path / 'attempt') for key in cache.SHARED)
    monkeypatch.setenv('BMS_RFD4_COMPILE_CACHE_ROOT', '')
    with cache.compile_environment(placement, tmp_path / 'unbound/output') as report:
        assert all(Path(report['effective'][key]).is_relative_to(tmp_path / 'unbound') for key in cache.SHARED)
    monkeypatch.setenv('BMS_RFD4_COMPILE_CACHE_ROOT', str(blocked))
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('offline GPU metadata unavailable')
    monkeypatch.setattr(cache.subprocess, 'run', unavailable)
    with cache.compile_environment(placement, tmp_path / 'other/output') as report:
        assert report['identity']['gpu_architecture_driver'] is None
        assert all('unavailable' in report['private_reasons'][key] for key in cache.SHARED)


def test_effective_overrides_win_and_restore_on_failure(placement, monkeypatch, tmp_path):
    for key in cache.CACHE_KEYS:
        monkeypatch.setenv(key, '' if key == 'TRITON_CACHE_DIR' else '/explicit/' + key)
    before = dict(os.environ)
    with pytest.raises(RuntimeError, match='inert'):
        with cache.compile_environment(placement, tmp_path / 'output') as report:
            assert report['inherited'] == {key: before[key] for key in cache.CACHE_KEYS}
            assert all(os.environ[key] == before[key] for key in cache.CACHE_KEYS)
            raise RuntimeError('inert')
    assert dict(os.environ) == before


def test_runtime_version_architecture_and_flags_isolate_namespace(placement, monkeypatch):
    original = cache.runtime_namespace(placement, os.environ)[0]
    monkeypatch.setenv('TRITON_DEBUG', '1')
    assert cache.runtime_namespace(placement, os.environ)[0] != original
    monkeypatch.delenv('TRITON_DEBUG')
    with monkeypatch.context() as changed:
        changed.setattr(cache.subprocess, 'run', lambda command, **kwargs:
                       subprocess.CompletedProcess(command, 0, '9.0, 580.1\n', ''))
        assert cache.runtime_namespace(placement, os.environ)[0] != original
    monkeypatch.setattr(cache.importlib.metadata, 'version', lambda name: 'different-build')
    assert cache.runtime_namespace(placement, os.environ)[0] != original


def test_immutable_digest_relocates_but_unknown_image_stays_private(placement, monkeypatch, tmp_path):
    original = cache.runtime_namespace(placement, os.environ)[0]
    monkeypatch.setenv('BMS_RFD4_RUNTIME_IMAGE', '/remote/cache/objects/' + 'a' * 64 + '/runtime.sif')
    assert cache.runtime_namespace(placement, os.environ)[0] == original
    monkeypatch.setenv('BMS_RFD4_RUNTIME_IMAGE', '/unqualified/mutable-alias.sif')
    with cache.compile_environment(placement, tmp_path / 'attempt/output') as report:
        assert report['identity']['image_sha256'] is None
        assert all(key in report['private_reasons'] for key in cache.SHARED)


def test_actual_runner_enters_cache_before_native_import(placement, monkeypatch, tmp_path):
    def inert(request, bindings, output, **kwargs):
        assert Path(os.environ['TORCHINDUCTOR_CACHE_DIR']).is_dir()
        assert Path(os.environ['CUEQ_TRITON_CACHE_DIR']).is_relative_to(tmp_path)
        assert kwargs['bundle'] == 'retained-pair'
        return 'inert native seam only'
    monkeypatch.setattr(runner, '_run', inert)
    assert runner.run(placement, {}, tmp_path / 'output', bundle='retained-pair') == 'inert native seam only'
    assert 'TORCHINDUCTOR_CACHE_DIR' not in os.environ

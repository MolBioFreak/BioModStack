"""Shared userspace command boundary, independent of live science/SSH."""
import base64
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bms_userspace_driver_tests', ROOT / 'platform/api/tools/bms_container.py')
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)


def test_execution_flags_preserve_scientific_arguments(tmp_path):
    value = driver.parse_exec(['--nv', '--writable-tmpfs', '--cleanenv', '--no-home',
        '--bind', f'{tmp_path}:/bms/input:ro', '--env', 'CUDA_VISIBLE_DEVICES=0,1,OMP_NUM_THREADS=2',
        '--pwd', '/bms/input', '/image.sif', 'python', '-c', 'print(17)'])
    assert value.command == ['python', '-c', 'print(17)']
    assert value.gpu and value.writable and value.cleanenv and value.no_home
    assert value.environment == {'CUDA_VISIBLE_DEVICES': '0,1', 'OMP_NUM_THREADS': '2'}
    assert value.binds == [(str(tmp_path), '/bms/input', 'ro')]
    assert value.cwd == '/bms/input'


@pytest.mark.parametrize('option', ['--containall', '--pid', '--net', '--network', '--writable', '--fake'])
def test_removed_or_unknown_contracts_are_not_silently_ignored(option):
    with pytest.raises(ValueError, match='unsupported userspace'):
        driver.parse_exec([option, '/image.sif', 'true'])


@pytest.mark.parametrize('binding', ['/host:relative:ro', '/host:/data:bad', '/host:/../data:rw', ':/data'])
def test_unsafe_bind_rejected(binding):
    with pytest.raises(ValueError):
        driver.parse_exec(['--bind', binding, '/image.sif', 'true'])


@pytest.mark.parametrize('shell', [['-ue', '/work/.command.run', 'nxf_trace'],
                                 ['-euo', 'pipefail', '/work/.command.run', 'nxf_trace']])
def test_real_nextflow_trace_wrapper_remains_host_bash(monkeypatch, shell):
    payload = base64.b64encode(json.dumps({'image': '/image.sif', 'options': '--nv'}).encode()).decode()
    class Executed(Exception):
        pass
    def execute(path, args):
        assert path == '/bin/bash'
        assert args == ['/bin/bash', *shell]
        raise Executed
    monkeypatch.setattr(driver.os, 'execv', execute)
    with pytest.raises(Executed):
        driver.main(['task-shell', payload, *shell])


def test_snapshot_detects_content_and_membership_mutation(tmp_path):
    (tmp_path / 'input').write_text('original')
    initial = driver.snapshot(tmp_path)
    (tmp_path / 'input').write_text('modified')
    assert driver.snapshot(tmp_path) != initial
    current = driver.snapshot(tmp_path)
    (tmp_path / 'extra').write_text('new')
    assert driver.snapshot(tmp_path) != current


def test_image_downloads_not_disguised_as_execution(tmp_path):
    with pytest.raises(ValueError, match='published local image'):
        driver.canonical_image('docker://python:latest', tmp_path)

"""Shared userspace command boundary, independent of live science/SSH."""
import importlib.util
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


@pytest.mark.parametrize('option', ['--containall', '--pid', '--net', '--network', '--writable', '--no-mount', '--oci', '--userns', '--disable-cache', '--fake'])
def test_removed_or_unknown_contracts_are_not_silently_ignored(option):
    with pytest.raises(ValueError, match='unsupported userspace'):
        driver.parse_exec([option, '/image.sif', 'true'])


@pytest.mark.parametrize('binding', ['/host:relative:ro', '/host:/data:bad', '/host:/../data:rw', ':/data'])
def test_unsafe_bind_rejected(binding):
    with pytest.raises(ValueError):
        driver.parse_exec(['--bind', binding, '/image.sif', 'true'])


def test_removed_shell_entrypoint_cannot_run_on_host():
    with pytest.raises(ValueError, match='expected exec'):
        driver.main(['task-shell', 'obsolete', '-ue', '.command.sh'])


def test_nextflow_bridge_restores_environment_and_does_not_shadow_apptainer(tmp_path, monkeypatch):
    path = ROOT/'platform/api/tools/bms_nextflow_singularity.py'
    spec = importlib.util.spec_from_file_location('nextflow_bridge', path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    private = tmp_path/'release/nextflow/container-bin'
    monkeypatch.setattr(bridge, '__file__', str(private/'singularity'))
    monkeypatch.setenv('PATH', str(private) + ':/usr/bin:/bin')
    monkeypatch.setenv('SINGULARITYENV_BMS_UDOCKER', '/trusted/udocker')
    monkeypatch.setenv('SINGULARITYENV_PATH', str(private) + ':/usr/bin:/bin')
    monkeypatch.setenv('SINGULARITYENV_LD_LIBRARY_PATH', '/host/libraries')
    monkeypatch.setenv('SINGULARITYENV_EXPLICIT', 'container-override')
    monkeypatch.setenv('SINGULARITYENV_BMS_NEXTFLOW_EXPLICIT_ENV', '["SINGULARITYENV_EXPLICIT"]')
    class Executed(Exception): pass
    def execute(path, argv, env):
        assert path == str(tmp_path/'release/bin/bms-container')
        assert argv == ['bms-container', 'exec', '--no-home', '/image.sif', '/usr/bin/env', 'python3', '.command.sh']
        assert env['PATH'] == '/usr/bin:/bin'
        assert env['BMS_UDOCKER'] == '/trusted/udocker'
        assert env['LD_LIBRARY_PATH'] == '/host/libraries'
        assert 'SINGULARITYENV_LD_LIBRARY_PATH' not in env
        assert 'SINGULARITYENV_PATH' not in env
        assert env['SINGULARITYENV_EXPLICIT'] == 'container-override'
        raise Executed
    monkeypatch.setattr(bridge.os, 'execve', execute)
    with pytest.raises(Executed):
        bridge.main(['exec', '--no-home', '/image.sif', '/usr/bin/env', 'python3', '.command.sh'])
    for command in (['pull', 'image'], ['version'], ['sif', 'list'], ['run', 'image']):
        with pytest.raises(ValueError, match='no image acquisition'):
            bridge.main(command)



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

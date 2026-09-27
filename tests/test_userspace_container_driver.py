"""Shared userspace command boundary, independent of live science/SSH."""
import importlib.util
import os
import stat
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bms_userspace_driver_tests', ROOT / 'platform/api/tools/bms_container.py')
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)


def test_preparation_and_execution_share_the_sif_extractor():
    assert driver.extract_sif is driver.views.extract_sif
    assert driver.sif_partition_offset is driver.views.sif_partition_offset


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


def test_input_projection_preserves_bytes_modes_and_symlinks(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    (source / 'nested').mkdir(parents=True)
    item = source / 'nested' / 'payload'
    item.write_bytes(b'input bytes')
    item.chmod(0o755)
    (source / 'alias').symlink_to('nested/payload')
    source.chmod(0o555)
    (source / 'nested').chmod(0o555)
    before = driver.snapshot(source)
    cloned = []
    def clone(out, operation, fd):
        assert operation == driver.views.FICLONE
        cloned.append(os.fstat(fd).st_ino)
        os.write(out, os.pread(fd, os.fstat(fd).st_size, 0))
    monkeypatch.setattr(driver.fcntl, 'ioctl', clone)
    images = []
    try:
        driver.copy_input(source, target, '/input', images)
        assert cloned == [item.stat().st_ino]
        assert not images
        assert driver.snapshot(source) == before
        assert (target / 'nested/payload').read_bytes() == b'input bytes'
        assert stat.S_IMODE((target / 'nested/payload').stat().st_mode) == 0o755
        assert os.readlink(target / 'alias') == 'nested/payload'
        assert stat.S_IMODE(target.stat().st_mode) == 0o555
        (target / 'nested/payload').write_bytes(b'private edit')
        assert item.read_bytes() == b'input bytes'
    finally:
        for node in [source, source / 'nested', target, target / 'nested']:
            if node.exists():
                node.chmod(0o755)


@pytest.mark.parametrize('mutation', ['replace_file', 'replace_directory', 'write_restore'])
def test_input_projection_rejects_inflight_mutation(tmp_path, monkeypatch, mutation):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    item = source / 'payload'
    item.write_bytes(b'input bytes')
    def mutate(out, operation, fd):
        os.write(out, os.pread(fd, os.fstat(fd).st_size, 0))
        if mutation == 'replace_directory':
            source.rename(tmp_path / 'old-source')
            source.symlink_to(tmp_path / 'old-source', target_is_directory=True)
        elif mutation == 'replace_file':
            item.rename(source / 'old-payload')
            item.symlink_to('old-payload')
        else:
            info, content = item.stat(), item.read_bytes()
            item.write_bytes(b'X' * len(content))
            item.write_bytes(content)
            os.utime(item, ns=(info.st_atime_ns, info.st_mtime_ns))
    monkeypatch.setattr(driver.fcntl, 'ioctl', mutate)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        driver.copy_input(source, target, '/input', [])


def test_input_projection_has_no_byte_copy_fallback(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    source.write_bytes(b'unchanged')
    def fail(*args):
        raise OSError('CoW unsupported')
    monkeypatch.setattr(driver.fcntl, 'ioctl', fail)
    with pytest.raises(OSError, match='CoW unsupported'):
        driver.copy_input(source, tmp_path / 'output', '/input', [])
    assert source.read_bytes() == b'unchanged'


def test_input_snapshot_no_per_file_ancestor_reopening(tmp_path, monkeypatch):
    (tmp_path / 'nested').mkdir()
    for index in range(30):
        (tmp_path / 'nested' / str(index)).write_text('small file')
    original, calls = os.open, []
    def counted(path, flags, *args, **kwargs):
        if flags & os.O_DIRECTORY:
            calls.append(path)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', counted)
    assert len(driver.snapshot(tmp_path)) == 32
    # Two ancestry checks plus the actual directories, not per-file traversal.
    assert len(calls) <= 2 * len(tmp_path.parts) + 2

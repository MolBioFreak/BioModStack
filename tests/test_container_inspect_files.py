"""Real CLI/process/lease qualification without containers, CoW or hardware."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'platform/api/tools/bms_container.py'
spec = importlib.util.spec_from_file_location('inspect_driver_tests', DRIVER)
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)

# Executable doubles exercise actual pipes, inherited descriptors and signals.
DOUBLE = r'''
import json, os, pathlib, signal, sys, time
args = sys.argv[1:]
mode = os.environ.get('INSPECT_TEST_MODE', '')
source = next(a for a in args if a.startswith('/proc/self/fd/'))
assert pathlib.Path(source).read_bytes() == b'published-image'
with open(os.environ['INSPECT_TEST_LOG'], 'a') as out:
    out.write(json.dumps([pathlib.Path(sys.argv[0]).name, *args]) + '\n')
if pathlib.Path(sys.argv[0]).name == 'apptainer':
    assert args[:2] == ['sif', 'list']
    if mode == 'metadata-limit':
        sys.stdout.write('X' * 1100000)
    elif mode == 'partition-missing':
        print('no system partition')
    elif mode == 'partition-malformed':
        print('FS (Squashfs/*System/amd64)')
    else:
        print('1 | 1 | 1 | 4096 - 9999 | FS (Squashfs/*System/amd64)')
    sys.exit(0)
assert args[:5] == ['-no-progress', '-no-wildcards', '-o', '4096', '-cat']
path = args[-1]
if mode in ('sleep', 'cancel'):
    pid = os.fork()
    if pid == 0:
        os.setsid()
        time.sleep(90)
        sys.exit(0)
    pathlib.Path(os.environ['INSPECT_TEST_MARKER']).write_text(json.dumps([os.getpid(), pid]))
    time.sleep(90)
if path == 'missing' or mode == 'partial-error' and path == 'checkpoint':
    sys.stdout.write('partial bytes')
    sys.exit(7)
if mode in ('replace', 'parent-replace', 'write-restore') and path == 'checkpoint':
    image = pathlib.Path(os.environ['INSPECT_TEST_IMAGE'])
    if mode == 'parent-replace':
        old = image.parent.with_name('old-generation')
        image.parent.rename(old)
        image.parent.mkdir()
        image.write_bytes(b'published-image')
        image.chmod(0o400)
        image.parent.chmod(0o500)
    elif mode == 'replace':
        image.parent.chmod(0o700)
        image.unlink()
        image.write_bytes(b'published-image')
        image.chmod(0o400)
        image.parent.chmod(0o500)
    else:
        info = image.stat()
        image.chmod(0o600)
        image.write_bytes(b'published-image')
        image.chmod(0o400)
        os.utime(image, ns=(info.st_atime_ns, info.st_mtime_ns))
if path == 'checkpoint':
    for _ in range(128):
        sys.stdout.buffer.write(b'Z' * 65536)
else:
    sys.stdout.buffer.write(b'#!/bin/sh\nexit 0\n')
'''


@pytest.fixture
def inspection(tmp_path):
    from scripts.lib.shared_runtime_images import publish_image
    source = tmp_path / 'source.sif'
    source.write_bytes(b'published-image')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / 'store'
    image = publish_image(source, store, digest)
    tools = tmp_path / 'tools'
    tools.mkdir()
    for name in ('apptainer', 'unsquashfs'):
        executable = tools / name
        executable.write_text(f'#!{sys.executable}\n' + DOUBLE)
        executable.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith('BMS_')}
    env.update(BMS_HOME=str(ROOT), BMS_RUNTIME_IMAGE_STORE=str(store),
               PATH=str(tools) + os.pathsep + env.get('PATH', ''),
               INSPECT_TEST_IMAGE=str(image), INSPECT_TEST_LOG=str(tmp_path / 'calls'),
               INSPECT_TEST_MARKER=str(tmp_path / 'marker'))
    yield image, store, env
    # Frozen published fixtures must be removable by pytest's ordinary owner.
    for path in store.rglob('*'):
        if path.is_dir():
            path.chmod(0o700)


def invoke(inspection, paths=('/executable', '/checkpoint'), *, mode='', limit=None, timeout=None, inherited=False):
    image, store, env = inspection
    env = dict(env, INSPECT_TEST_MODE=mode)
    command = [sys.executable, str(DRIVER)]
    if limit is not None or timeout is not None:
        setup = f"import sys; sys.path.insert(0, {str(DRIVER.parent)!r}); import bms_container as d; "
        if limit is not None:
            setup += f'd.INSPECT_MAX_BYTES = {limit}; '
        if timeout is not None:
            setup += f'd.INSPECT_TIMEOUT = {timeout}; '
        command = [sys.executable, '-c', setup + 'sys.exit(d.main())']
    with image.open('rb') as handle:
        name = f'/proc/self/fd/{handle.fileno()}' if inherited else str(image)
        result = subprocess.run([*command, 'inspect-files', name, *paths], env=env,
                                pass_fds=(handle.fileno(),) if inherited else (),
                                capture_output=True, text=True, timeout=25)
    assert driver.lifecycle.load_state(store)['leases'] == {}
    return result


@pytest.mark.parametrize('inherited', [False, True])
def test_real_stream_exact_roster_no_execution_tree(inspection, inherited):
    result = invoke(inspection, inherited=inherited)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (hashlib.sha256(b'#!/bin/sh\nexit 0\n').hexdigest() + '  /executable\n'
                             + hashlib.sha256(b'Z' * (128 * 65536)).hexdigest() + '  /checkpoint\n')
    image, store, env = inspection
    calls = [json.loads(line) for line in Path(env['INSPECT_TEST_LOG']).read_text().splitlines()]
    assert [c[0] for c in calls] == ['apptainer', 'unsquashfs', 'unsquashfs']
    assert not (store / 'derived').exists()
    assert not any(p.name in {'rootfs', 'udocker'} for p in store.rglob('*'))


@pytest.mark.parametrize('paths', [(), ('relative',), ('/',), ('//a',), ('/a/',), ('/a//b',),
    ('/a/./b',), ('/a/../b',), ('/a*',), ('/a?',), ('/a[1]',), ('/a\\b',), ('/a\nb',),
    ('/a\x7fb',), ('/-option',), ('/a/-option',), ('/a', '/a'), tuple('/a' + str(i) for i in range(33)),
    ('/' + 'a' * 4096,)])
def test_invalid_roster_never_launches(inspection, paths):
    result = invoke(inspection, paths)
    assert result.returncode != 0 and result.stdout == ''
    assert not Path(inspection[2]['INSPECT_TEST_LOG']).exists()


@pytest.mark.parametrize('mode', ['partial-error', 'partition-missing', 'partition-malformed',
                                  'metadata-limit', 'replace', 'parent-replace', 'write-restore'])
def test_failures_publish_nothing_and_release_lease(inspection, mode):
    result = invoke(inspection, mode=mode, inherited=True)
    assert result.returncode != 0 and result.stdout == ''


def test_missing_file_cannot_publish_first_digest(inspection):
    result = invoke(inspection, ('/executable', '/missing'))
    assert result.returncode != 0 and result.stdout == ''


def test_stream_limit_releases_lease_without_partial_stdout(inspection):
    result = invoke(inspection, limit=65536)
    assert result.returncode != 0 and result.stdout == ''
    assert 'byte limit' in result.stderr


def test_timeout_reaps_children_before_lease_release(inspection):
    result = invoke(inspection, mode='sleep', timeout=1)
    assert result.returncode != 0 and result.stdout == ''
    assert 'timed out' in result.stderr
    pids = json.loads(Path(inspection[2]['INSPECT_TEST_MARKER']).read_text())
    assert all(not Path(f'/proc/{pid}').exists() for pid in pids)


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_main_cancellation_reaps_adopted_children_and_releases_lease(inspection, signum):
    image, store, env = inspection
    env = dict(env, INSPECT_TEST_MODE='cancel')
    process = subprocess.Popen([sys.executable, str(DRIVER), 'inspect-files', str(image), '/executable'],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        marker = Path(env['INSPECT_TEST_MARKER'])
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert marker.exists()
        pids = json.loads(marker.read_text())
        assert driver.lifecycle.load_state(store)['leases']
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 128 + signum, stderr
        assert stdout == ''
        assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
        assert driver.lifecycle.load_state(store)['leases'] == {}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('paths', [('/file', '/empty'), ('/file', '/missing'), ('/nested',)])
def test_real_unsquashfs_stream_and_missing_errors(inspection, tmp_path, paths):
    import shutil
    from scripts.lib.shared_runtime_images import publish_image
    unsquashfs, mksquashfs = shutil.which('unsquashfs'), shutil.which('mksquashfs')
    if not unsquashfs or not mksquashfs:
        pytest.skip('local squashfs-tools unavailable')
    _, store, env = inspection
    root = tmp_path / 'squash-root'
    root.mkdir()
    (root / 'file').write_bytes(b'actual Squashfs asset')
    (root / 'empty').touch()
    (root / 'nested').mkdir()
    squash = tmp_path / 'squashfs'
    subprocess.run([mksquashfs, str(root), str(squash), '-noappend', '-processors', '1', '-no-progress'],
                   check=True, capture_output=True, timeout=20)
    sif = tmp_path / 'embedded.sif'
    sif.write_bytes(b'X' * 4096 + squash.read_bytes())
    image = publish_image(sif, store, hashlib.sha256(sif.read_bytes()).hexdigest())
    tools = tmp_path / 'tools'
    (tools / 'unsquashfs').unlink()
    (tools / 'unsquashfs').symlink_to(unsquashfs)
    (tools / 'apptainer').write_text(f'#!{sys.executable}\n'
        "print('1 | 1 | 1 | 4096 - 9999 | FS (Squashfs/*System/amd64)')\n")
    result = invoke((image, store, dict(env, INSPECT_TEST_IMAGE=str(image))), paths, inherited=True)
    if paths == ('/file', '/empty'):
        assert result.returncode == 0, result.stderr
        assert result.stdout == (hashlib.sha256(b'actual Squashfs asset').hexdigest() + '  /file\n'
                                 + hashlib.sha256(b'').hexdigest() + '  /empty\n')
    else:
        assert result.returncode != 0 and result.stdout == ''


def test_replacement_after_acquisition_is_rejected(inspection, monkeypatch, capsys):
    image, store, env = inspection
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    original = driver.lifecycle.acquire_lease
    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        image.parent.chmod(0o700)
        image.unlink()
        image.write_bytes(b'published-image')
        image.chmod(0o400)
        image.parent.chmod(0o500)
        return result
    monkeypatch.setattr(driver.lifecycle, 'acquire_lease', replace)
    with pytest.raises(ValueError, match='generation changed'):
        driver.inspect_files(str(image), ['/executable'], {})
    assert capsys.readouterr().out == ''
    assert driver.lifecycle.load_state(store)['leases'] == {}

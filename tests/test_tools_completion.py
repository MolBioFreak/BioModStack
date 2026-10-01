"""Actual worker helper qualification; inert counters are not live acceptance."""
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from contextlib import contextmanager

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = 'd6b67fd3271e0f9b2a05d4d8ba7697ceb127325f'


def load(name, baseline=False):
    path = ROOT / 'platform/api/tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name + ('_baseline' if baseline else '_qualification'), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    source = subprocess.check_output(['git', 'show', f'{BASE}:platform/api/tools/{name}.py'], cwd=ROOT) if baseline else path.read_bytes()
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


def item(data):
    return dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def seed(cache, data):
    row = item(data)
    source = Path(cache.root) / 'incoming' / row['sha256']
    source.write_bytes(data)
    cache.ingest(row, source)
    return row, Path(cache.root) / 'objects/sha256' / row['sha256'][:2] / row['sha256']


def counter(record_property, key, value):
    record_property(key, json.dumps(value, sort_keys=True))


@pytest.mark.parametrize('baseline', [True, False], ids=['baseline', 'candidate'])
def test_d08_actual_install_source_bytes(tmp_path, monkeypatch, record_property, baseline):
    cache, managed = load('bms_artifact_cache', baseline), load('bms_managed_runtime', baseline)
    storage = cache.Cache(tmp_path / 'worker/cache/artifacts/v1')
    data = b'a' * 14336
    row, obj = seed(storage, data)
    root = tmp_path / 'worker/managed-assets/v1'
    manifest = dict(selection=dict(kind='model', model_id='fixture'), source_revision='a'*40,
                    source_tree='b'*40, artifacts=[dict(name='weights/model.bin', mode=0o444, **row)])
    inode = obj.stat().st_ino
    read, reads = os.read, []
    def counted(fd, n):
        chunk = read(fd, n)
        if os.fstat(fd).st_ino == inode:
            reads.append(len(chunk))
        return chunk
    with monkeypatch.context() as m:
        m.setattr(os, 'read', counted)
        result = managed.install(root, manifest, managed.boot_id(), cache)
    assert result['release']['state'] == 'verified'
    assert sum(reads) == len(data) * (2 if baseline else 1)
    assert managed.observe(root, manifest, cache)['state'] == 'verified'
    assert (managed.release_path(root, manifest) / 'weights/model.bin').read_bytes() == data
    counter(record_property, 'managed_source_body_bytes', sum(reads))


@pytest.mark.parametrize('failure', ['digest', 'size', 'enospc', 'cancel', 'file_sync', 'rename'])
def test_d08_failure_preserves_active(tmp_path, monkeypatch, failure):
    cache, managed = load('bms_artifact_cache'), load('bms_managed_runtime')
    storage = cache.Cache(tmp_path / 'worker/cache/artifacts/v1')
    root = tmp_path / 'worker/managed-assets/v1'
    row, _ = seed(storage, b'old')
    manifest = dict(selection=dict(kind='model', model_id='fixture'), source_revision='a'*40,
                    source_tree='b'*40, artifacts=[dict(name='weights/model.bin', mode=0o444, **row)])
    managed.install(root, manifest, managed.boot_id(), cache)
    active = root / 'active/model-fixture.json'
    prior = active.read_bytes()
    row, obj = seed(storage, b'new bytes')
    new = manifest | dict(artifacts=[dict(name='weights/model.bin', mode=0o444, **row)])
    if failure in {'digest', 'size'}:
        obj.chmod(0o600)
        obj.write_bytes(b'bad bytes' if failure == 'digest' else b'bad')
        obj.chmod(0o444)
    else:
        original = {'enospc': os.write, 'cancel': cache.Cache.emit,
                    'file_sync': os.fsync, 'rename': os.replace}[failure]
        if failure == 'enospc':
            def fault(fd, data):
                raise OSError(errno.ENOSPC, 'injected copy write')
            monkeypatch.setattr(os, 'write', fault)
        elif failure == 'cancel':
            def fault(self, *args, **kwargs):
                raise KeyboardInterrupt('injected cancellation')
            monkeypatch.setattr(cache.Cache, 'emit', fault)
        elif failure == 'file_sync':
            def fault(fd):
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError('injected file sync')
                return original(fd)
            monkeypatch.setattr(os, 'fsync', fault)
        else:
            def fault(*args, **kwargs):
                raise OSError('injected rename')
            monkeypatch.setattr(os, 'replace', fault)
    with pytest.raises((ValueError, OSError, KeyboardInterrupt)):
        managed.install(root, new, managed.boot_id(), cache)
    assert active.read_bytes() == prior
    assert not (managed.release_path(root, new) / 'manifest.json').exists()
    assert not list(root.rglob('.partial-*'))


@pytest.mark.parametrize('baseline', [True, False], ids=['baseline', 'candidate'])
def test_d09_four_hardlinks_cold_warm(tmp_path, monkeypatch, record_property, baseline):
    cache = load('bms_artifact_cache', baseline)
    storage = cache.Cache(tmp_path / 'cache')
    row, obj = seed(storage, b'a' * 14336)
    rows = [dict(name=f'model/{n}.bin', mode=0o444, **row) for n in range(4)]
    sync, read, listdir = os.fsync, os.read, os.listdir
    events, body, scans = [], [], []
    def counted_sync(fd):
        events.append(('dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file', os.readlink(f'/proc/self/fd/{fd}')))
        return sync(fd)
    def counted_read(fd, size):
        data = read(fd, size)
        body.append((os.fstat(fd).st_ino, len(data)))
        return data
    def counted_scan(fd):
        scans.append(fd)
        return listdir(fd)
    with monkeypatch.context() as m:
        m.setattr(os, 'fsync', counted_sync)
        result = storage.weights(rows, install=True)
    assert [kind for kind, _ in events].count('dir') == (7 if baseline else 3)
    assert [kind for kind, _ in events].count('file') == 1
    if not baseline:
        assert [kind for kind, _ in events] == ['dir', 'file', 'dir', 'dir']
        assert events[0][1].endswith('/model')
        assert events[1][1].endswith('/.bms-weights.json')
        assert events[-1][1].endswith('/weights')
    root = Path(result['root'])
    assert all((root / f'model/{n}.bin').stat().st_ino == obj.stat().st_ino for n in range(4))
    cold = [kind for kind, _ in events]
    events.clear()
    with monkeypatch.context() as m:
        m.setattr(os, 'fsync', counted_sync)
        m.setattr(os, 'read', counted_read)
        m.setattr(os, 'listdir', counted_scan)
        assert storage.weights(rows, install=True) == result
    assert not events and not scans
    assert sum(n for inode, n in body if inode == obj.stat().st_ino) == 0
    assert sum(n for _, n in body) == (root / '.bms-weights.json').stat().st_size
    counter(record_property, 'weight_cold_sync_order', cold)
    counter(record_property, 'weight_warm', dict(fsyncs=len(events), scans=len(scans), body_bytes=0,
                                               marker_bytes=sum(n for _, n in body)))


@pytest.mark.parametrize('boundary', ['mkdir_stage', 'mkdir_child', 'link1', 'link2', 'link3', 'link4',
                                      'sync_child', 'marker_open', 'marker_sync', 'sync_root', 'rename', 'sync_parent'])
def test_d09_publication_fault_retry(tmp_path, monkeypatch, boundary):
    cache = load('bms_artifact_cache')
    storage = cache.Cache(tmp_path / 'cache')
    row, obj = seed(storage, b'committed bytes')
    other = storage.weights([dict(name='other.bin', mode=0o444, **row)], install=True)
    rows = [dict(name=f'model/{n}.bin', mode=0o444, **row) for n in range(4)]
    digest, _, _ = cache.weight_layout(rows)
    root = Path(storage.root) / 'weights' / digest
    count = 0
    fired = False
    def fail():
        nonlocal fired
        fired = True
        raise OSError('injected ' + boundary)
    with monkeypatch.context() as m:
        mkdir, link, sync, open_, rename = os.mkdir, os.link, os.fsync, os.open, os.rename
        def fault_mkdir(name, *args, **kw):
            if boundary == 'mkdir_stage' and str(name).startswith('.partial-' + digest):
                fail()
            if boundary == 'mkdir_child' and name == 'model':
                fail()
            return mkdir(name, *args, **kw)
        def fault_link(*args, **kw):
            nonlocal count
            count += 1
            if boundary == f'link{count}':
                fail()
            return link(*args, **kw)
        def fault_sync(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            if boundary == 'sync_child' and path.endswith('/model'):
                fail()
            if boundary == 'marker_sync' and path.endswith('/.bms-weights.json'):
                fail()
            if boundary == 'sync_root' and '.partial-' + digest in path and not path.endswith('/model') and stat.S_ISDIR(os.fstat(fd).st_mode):
                fail()
            if boundary == 'sync_parent' and path == str(root.parent):
                fail()
            return sync(fd)
        def fault_open(name, flags, *args, **kw):
            if boundary == 'marker_open' and name == '.bms-weights.json' and flags & os.O_CREAT:
                fail()
            return open_(name, flags, *args, **kw)
        def fault_rename(*args, **kw):
            if boundary == 'rename':
                fail()
            return rename(*args, **kw)
        for name, fn in [('mkdir', fault_mkdir), ('link', fault_link), ('fsync', fault_sync), ('open', fault_open), ('rename', fault_rename)]:
            m.setattr(os, name, fn)
        with pytest.raises(OSError):
            storage.weights(rows, install=True)
    assert fired
    # A failure AFTER atomic rename leaves a complete visible publication, never a partial one.
    assert root.exists() == (boundary == 'sync_parent')
    assert not list(root.parent.glob('.partial-' + digest + '-*'))
    assert obj.read_bytes() == b'committed bytes'
    assert storage.weights(rows, install=True)['state'] == 'ready'
    assert storage.weights([dict(name='other.bin', mode=0o444, **row)], install=True) == other


def test_d09_executable_copy_fsync_retained(tmp_path, monkeypatch):
    cache = load('bms_artifact_cache')
    storage = cache.Cache(tmp_path / 'cache')
    row, obj = seed(storage, b'executable bytes')
    sync, files = os.fsync, []
    def counted(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            files.append(os.readlink(f'/proc/self/fd/{fd}'))
        return sync(fd)
    monkeypatch.setattr(os, 'fsync', counted)
    result = storage.weights([dict(name='model/exe', mode=0o555, **row)], install=True)
    assert len(files) == 2  # copy and marker
    assert (Path(result['root']) / 'model/exe').stat().st_ino != obj.stat().st_ino
    assert stat.S_IMODE(obj.stat().st_mode) == 0o444


def image_fixture(tmp_path):
    cache = load('bms_artifact_cache')
    storage = cache.Cache(tmp_path / 'worker/cache/artifacts/v1')
    data = b'legacy canonical image'
    row = item(data) | dict(kind='runtime_image')
    path = storage.image_path(row)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    path.chmod(0o400)
    path.parent.chmod(0o500)
    return cache, storage, row, path


def test_d10_shared_identity_zero_body_no_receipt(tmp_path, monkeypatch, record_property):
    cache, storage, row, path = image_fixture(tmp_path)
    images = cache.runtime_images()
    monkeypatch.setattr(cache, 'runtime_images', lambda: images)
    original, calls = images.image_identity, []
    def delegated(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(images, 'image_identity', delegated)
    def forbidden(*args):
        raise AssertionError('warm identity read body')
    monkeypatch.setattr(os, 'read', forbidden)
    result = storage.runtime_identity(row)
    assert calls == [(path, row['sha256'])]
    assert result['size'] == row['size_bytes']
    assert set(result) == {'sha256', 'size', 'device', 'inode', 'mtime_ns', 'ctime_ns'}
    assert list(path.parent.iterdir()) == [path]
    assert storage.probe_runtime(row)['state'] == 'cache_hit'
    storage.pin_runtime(row, 'test-owner')
    assert storage.prepare_runtime_image(row, 'apptainer', '00000000-0000-4000-8000-000000000001')['state'] == 'ready'
    counter(record_property, 'image_body_bytes', 0)


@pytest.mark.parametrize('damage', ['size', 'mode', 'nlink', 'directory_mode', 'replacement', 'directory_swap'])
def test_d10_canonical_negatives(tmp_path, monkeypatch, damage):
    cache, storage, row, path = image_fixture(tmp_path)
    images = cache.runtime_images()
    monkeypatch.setattr(cache, 'runtime_images', lambda: images)
    if damage == 'size':
        row = row | dict(size_bytes=row['size_bytes'] + 1)
    elif damage == 'mode':
        path.chmod(0o600)
    elif damage == 'nlink':
        os.link(path, tmp_path / 'alias')
    elif damage == 'directory_mode':
        path.parent.chmod(0o700)
    else:
        check = images._check_file
        count = 0
        def race(*args):
            nonlocal count
            count += 1
            if count == 2:
                if damage == 'replacement':
                    path.parent.chmod(0o700)
                    replacement = path.parent / 'replacement'
                    replacement.write_bytes(b'legacy canonical image')
                    replacement.chmod(0o400)
                    os.replace(replacement, path)
                    path.parent.chmod(0o500)
                else:
                    path.parent.rename(path.parent.with_name('old'))
                    path.parent.mkdir(mode=0o500)
            return check(*args)
        monkeypatch.setattr(images, '_check_file', race)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        storage.probe_runtime(row)


@pytest.fixture
def projection(tmp_path, monkeypatch):
    driver = load('bms_container')
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    (source / 'nested/a').write_bytes(b'input')
    os.link(source / 'nested/a', source / 'nested/b')
    (source / 'alias').symlink_to('nested/a')
    calls = []
    def inert_clone(out, operation, fd):
        assert operation == driver.views.FICLONE
        calls.append((os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        # Deliberately metadata-only ioctl double; this does NOT prove isolation.
        os.ftruncate(out, os.fstat(fd).st_size)
    monkeypatch.setattr(driver.fcntl, 'ioctl', inert_clone)
    return driver, source, calls


@pytest.mark.parametrize('baseline', [True, False], ids=['baseline', 'candidate'])
def test_d11_actual_execute_walks(projection, tmp_path, monkeypatch, record_property, baseline):
    driver, source, clones = projection
    if baseline:
        driver = load('bms_container', True)
    store, work = tmp_path / 'store', tmp_path / 'work'
    rootfs = tmp_path / 'private/rootfs'
    rootfs.mkdir(parents=True)
    launcher = tmp_path / 'udocker'
    launcher.touch()
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setenv('BMS_CONTAINER_WORK_ROOT', str(work))
    monkeypatch.setenv('BMS_UDOCKER', str(launcher))
    monkeypatch.delenv('BMS_SHARED_WEIGHTS_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver, 'canonical_image', lambda *args: (tmp_path / 'image', 'a'*64))
    @contextmanager
    def private(*args):
        yield dict(rootfs=rootfs, image_fd=0)
    monkeypatch.setattr(driver.views, 'private_image_view', private)
    monkeypatch.setattr(driver, 'host_system_bindings', lambda _: [])
    monkeypatch.setattr(driver, 'run_owned', lambda *args: 0)
    walk, source_walks, private_walks = driver.views._walk_tree, [], []
    def counted(path):
        (source_walks if Path(path) == source else private_walks).append(str(path))
        yield from walk(path)
    monkeypatch.setattr(driver.views, '_walk_tree', counted)
    def forbidden(*args):
        raise AssertionError('projection body copy fallback')
    monkeypatch.setattr(os, 'read', forbidden)
    monkeypatch.setattr(os, 'pread', forbidden)
    assert driver.execute(driver.Invocation('/image', ['true'], binds=[(str(source), '/input', 'ro')]), {}) == 0
    assert len(source_walks) == (4 if baseline else 3)
    assert len(private_walks) == 2
    assert len(clones) == (2 if baseline else 1)
    counter(record_property, 'cow_counters', dict(source_walks=len(source_walks), private_snapshots=len(private_walks),
                                                ficlone_calls=len(clones), physical_files=len(set(clones)), body_bytes=0))


def test_d11_returned_snapshot_complete(projection, tmp_path):
    driver, source, calls = projection
    before = driver.snapshot(source)
    returned = driver.copy_input(source, tmp_path / 'projected', '/input', [])
    assert returned == before == driver.snapshot(source)
    assert returned['./alias'][-1] == 'nested/a'
    assert len(returned['.']) == 8  # full shared inode stamp plus link target
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['write_restore', 'replace', 'before_open', 'symlink', 'directory', 'membership', 'mode'])
def test_d11_midwalk_rejects(projection, tmp_path, monkeypatch, mutation):
    driver, source, calls = projection
    clone = driver.fcntl.ioctl
    leaf = source / 'nested/a'
    def mutate(out, op, fd):
        clone(out, op, fd)
        if mutation == 'write_restore':
            info = leaf.stat()
            leaf.write_bytes(b'other')
            leaf.write_bytes(b'input')
            os.utime(leaf, ns=(info.st_atime_ns, info.st_mtime_ns))
        elif mutation == 'replace':
            replacement = source / 'nested/new'
            replacement.write_bytes(b'input')
            os.replace(replacement, leaf)
        elif mutation == 'symlink':
            (source / 'alias').unlink()
            (source / 'alias').symlink_to('nested/b')
        elif mutation == 'directory':
            source.rename(tmp_path / 'old')
            source.mkdir()
        elif mutation == 'membership':
            (source / 'added').touch()
        else:
            leaf.chmod(0o400)
    if mutation == 'before_open':
        member = driver.views._member
        @contextmanager
        def swap(parent, name):
            if name == 'a':
                replacement = source / 'nested/new'
                replacement.write_bytes(b'input')
                os.replace(replacement, leaf)
            with member(parent, name) as value:
                yield value
        monkeypatch.setattr(driver.views, '_member', swap)
    else:
        monkeypatch.setattr(driver.fcntl, 'ioctl', mutate)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        before = driver.copy_input(source, tmp_path / 'projected', '/input', [])
        if driver.snapshot(source) != before:
            raise ValueError('post projection mismatch')


@pytest.mark.parametrize('phase', ['post_projection', 'source_exit', 'private_exit'])
def test_d11_execute_retained_rechecks(projection, tmp_path, monkeypatch, phase):
    driver, source, _ = projection
    rootfs = tmp_path / 'private/rootfs'
    rootfs.mkdir(parents=True)
    launcher = tmp_path / 'udocker'
    launcher.touch()
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'store'))
    monkeypatch.setenv('BMS_CONTAINER_WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setenv('BMS_UDOCKER', str(launcher))
    monkeypatch.delenv('BMS_SHARED_WEIGHTS_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver, 'canonical_image', lambda *args: (tmp_path / 'image', 'a'*64))
    @contextmanager
    def private(*args):
        yield dict(rootfs=rootfs, image_fd=0)
    monkeypatch.setattr(driver.views, 'private_image_view', private)
    monkeypatch.setattr(driver, 'host_system_bindings', lambda _: [])
    copy = driver.copy_input
    projected = []
    def copy_and_mutate(*args, **kw):
        before = copy(*args, **kw)
        projected.append(args[1])
        if phase == 'post_projection':
            (source / 'nested/a').chmod(0o400)
        return before
    monkeypatch.setattr(driver, 'copy_input', copy_and_mutate)
    def run(*args):
        if phase == 'source_exit':
            (source / 'nested/a').write_bytes(b'input')
        elif phase == 'private_exit':
            (projected[0] / 'nested/a').write_bytes(b'changed private')
        return 0
    monkeypatch.setattr(driver, 'run_owned', run)
    with pytest.raises(ValueError, match='declared .*input changed'):
        driver.execute(driver.Invocation('/image', ['true'], binds=[(str(source), '/input', 'ro')]), {})


def test_d11_real_filesystem_cow(tmp_path):
    driver = load('bms_container')
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b'actual cow bytes')
    try:
        before = driver.copy_input(source, target, '/input', [])
    except OSError as exc:
        if exc.errno in {errno.EOPNOTSUPP, errno.ENOTTY, errno.EXDEV, errno.EINVAL}:
            pytest.skip(f'real filesystem FICLONE unavailable: {exc}')
        raise
    assert before == driver.snapshot(source)
    assert target.read_bytes() == source.read_bytes()
    target.write_bytes(b'private mutation')
    assert source.read_bytes() == b'actual cow bytes'

"""Real cache publication and container exit guard; no inference/worker changes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import hashlib
import io
import os
from pathlib import Path
import tarfile

import pytest

from tools import bms_artifact_cache as cache, bms_container as driver


def ingest(storage, data):
    item = dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    path = Path(storage.root) / 'incoming' / item['sha256']
    path.write_bytes(data)
    storage.ingest(item, path)
    return item


@pytest.mark.parametrize('fallback', [False, True], ids=['filesystem', 'copy-fallback'])
@pytest.mark.parametrize('mutation', [None, 'legacy-links', 'content', 'write-restore', 'mode', 'replace', 'private'])
def test_concurrent_attempts_inside_actual_exit_guard(tmp_path, monkeypatch, fallback, mutation, record_property):
    storage = cache.Cache(tmp_path / 'cache')
    support = tmp_path / 'support-python'
    support.mkdir()
    payloads = {'venv.lock': b'', 'library.py': b'nonempty library\n'}
    items = {name: ingest(storage, data) for name, data in payloads.items()}
    # Reproduce installed runtime ownership, including the empty global CAS alias.
    for name, item in items.items():
        with storage.locked(item), storage.objects(item) as objects, cache.directory(support) as out:
            fd = os.open(item['sha256'], os.O_RDONLY, dir_fd=objects)
            try:
                storage._publish_immutable(fd, objects, out, name, item, 0o444)
            finally:
                os.close(fd)
    before = driver.snapshot(support)
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode='w') as archive:
        for name, data in payloads.items():
            member = tarfile.TarInfo('lib/' + name)
            member.size, member.mode = len(data), 0o644
            archive.addfile(member, io.BytesIO(data))
    archive_item = ingest(storage, packed.getvalue())
    clone = driver.fcntl.ioctl
    clone_results = []
    def allocate(out, op, source):
        if fallback:
            clone_results.append('forced-EOPNOTSUPP')
            raise OSError(errno.EOPNOTSUPP, 'fixture fallback')
        try:
            result = clone(out, op, source)
        except OSError as exc:
            clone_results.append('errno-' + str(exc.errno))
            raise
        clone_results.append('reflink')
        return result
    monkeypatch.setattr(driver.fcntl, 'ioctl', allocate)
    if mutation == 'legacy-links':
        def legacy(fd, parent, name, item, mode):
            with storage.objects(item) as objects:
                storage._publish_immutable(fd, objects, parent, name, item, mode)
        monkeypatch.setattr(storage, '_publish_private', legacy)

    rootfs = tmp_path / 'private/rootfs'
    rootfs.mkdir(parents=True)
    launcher = tmp_path / 'udocker'
    launcher.touch()
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'store'))
    monkeypatch.setenv('BMS_CONTAINER_WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setenv('BMS_UDOCKER', str(launcher))
    monkeypatch.delenv('BMS_SHARED_WEIGHTS_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver, 'canonical_image', lambda *a: (tmp_path / 'image', 'a' * 64))
    @contextmanager
    def private(*args):
        yield dict(rootfs=rootfs, image_fd=0)
    monkeypatch.setattr(driver.views, 'private_image_view', private)
    monkeypatch.setattr(driver, 'host_system_bindings', lambda _: [])

    def run(*args):
        # execute() has already captured the real source/private snapshots.
        def attempt(index):
            root = tmp_path / 'attempts' / str(index)
            storage.extract_source(archive_item, root / 'source', immutable=True)
            for name, item in items.items():
                storage.materialize(item, root / 'inputs' / name, root, 0o444)
            return root
        with ThreadPoolExecutor(max_workers=2) as pool:
            roots = list(pool.map(attempt, range(2)))
        if mutation != 'legacy-links':
            assert driver.snapshot(support) == before
            for name, data in payloads.items():
                paths = [r / kind / name for r in roots for kind in ('source/lib', 'inputs')]
                assert len({p.stat().st_ino for p in paths} | {(support / name).stat().st_ino}) == 5
                assert all(p.read_bytes() == data and p.stat().st_mode & 0o777 == 0o444 for p in paths)
                for path in paths:
                    path.unlink()
            assert driver.snapshot(support) == before  # Retirement is also independent.
        leaf = support / 'library.py'
        if mutation in ('content', 'write-restore'):
            info = leaf.stat()
            leaf.chmod(0o644)
            leaf.write_bytes(b'changed bytes')
            if mutation == 'write-restore':
                leaf.write_bytes(payloads['library.py'])
                leaf.chmod(0o444)
                os.utime(leaf, ns=(info.st_atime_ns, info.st_mtime_ns))
        elif mutation == 'mode':
            leaf.chmod(0o400)
        elif mutation == 'replace':
            replacement = support / 'replacement'
            replacement.write_bytes(payloads['library.py'])
            replacement.chmod(0o444)
            os.replace(replacement, leaf)
        elif mutation == 'private':
            projected = next((rootfs.parent / 'inputs').rglob('library.py'))
            projected.chmod(0o644)
            projected.write_bytes(b'private changed')
        return 0
    monkeypatch.setattr(driver, 'run_owned', run)
    invocation = driver.Invocation('/image', ['true'], binds=[(str(support), '/support-python', 'ro')])
    if mutation is None:
        assert driver.execute(invocation, {}) == 0
        assert driver.snapshot(support) == before
    else:
        with pytest.raises(ValueError, match='declared scientific input changed during execution'):
            driver.execute(invocation, {})
    record_property('allocation_results', ','.join(sorted(set(clone_results))))


@pytest.mark.parametrize('fault', ['corrupt', 'ENOSPC', 'cancel'])
def test_private_publication_failure_preserves_destination(tmp_path, monkeypatch, fault):
    storage = cache.Cache(tmp_path / 'cache')
    item = ingest(storage, b'original')
    target = tmp_path / 'attempt/input'
    target.parent.mkdir()
    target.write_bytes(b'old destination')
    donor = Path(storage.root) / 'objects/sha256' / item['sha256'][:2] / item['sha256']
    if fault == 'corrupt':
        donor.chmod(0o644)
        donor.write_bytes(b'corrupt!')
        donor.chmod(0o444)
        error = ValueError
    elif fault == 'ENOSPC':
        def fail(*args):
            raise OSError(errno.ENOSPC, 'fixture full')
        monkeypatch.setattr(driver.fcntl, 'ioctl', fail)
        error = OSError
    else:
        def cancel(item, state, **kwargs):
            if state == 'publishing':
                raise KeyboardInterrupt('fixture cancellation')
        monkeypatch.setattr(storage, 'emit', cancel)
        error = KeyboardInterrupt
    before = driver.snapshot(donor)
    with pytest.raises(error):
        storage.materialize(item, target, target.parent, 0o444)
    assert target.read_bytes() == b'old destination'
    assert driver.snapshot(donor) == before
    assert not list(target.parent.glob('.partial-*'))

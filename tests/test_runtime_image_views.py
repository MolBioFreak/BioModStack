"""Synthetic local filesystem tests; never launch containers or access services."""
import errno
import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import runtime_image_views as views
from lib import runtime_image_lifecycle as lifecycle
from lib import shared_runtime_images as shared


@pytest.fixture
def store(tmp_path):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'synthetic SIF fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    root, workspace = tmp_path / 'store', tmp_path / 'work'
    image = shared.publish_image(source, root, digest)
    yield root, digest, workspace, image
    for directory, _, _ in os.walk(tmp_path):
        os.chmod(directory, 0o700)


def extract(fd, destination):
    assert os.pread(fd, 100, 0) == b'synthetic SIF fixture'
    destination.mkdir(mode=0o755)
    (destination / 'bin').mkdir(mode=0o755)
    program = destination / 'bin' / 'program'
    program.write_bytes(b'original bytes' * 8192)
    program.chmod(0o755)
    os.link(program, destination / 'bin' / 'alias')
    (destination / 'link').symlink_to('bin/program')
    (destination / 'sticky').mkdir(mode=0o1777)
    (destination / 'sticky').chmod(0o1777)


def empty(fd, destination):
    destination.mkdir(mode=0o755)


def derive(store):
    root, digest, workspace, image = store
    identity = shared.verify_image(image, digest)
    with lifecycle.transaction(root), shared._file(image) as (fd, _, _):
        return views._derive(root, digest, fd, identity, extract)


@pytest.fixture
def clone_double(monkeypatch):
    """Byte-sized filesystem double, NOT live CoW/performance qualification."""
    def clone(destination, operation, source):
        assert operation == views.FICLONE
        os.write(destination, os.pread(source, os.fstat(source).st_size, 0))
    monkeypatch.setattr(views.fcntl, 'ioctl', clone)


def test_warm_launch_hashes_each_generation_once(store, monkeypatch, clone_double):
    root, digest, workspace, image = store
    path, _ = derive(store)
    program = path / 'rootfs/bin/program'
    watched = {image.stat().st_ino, program.stat().st_ino}
    calls = []
    original_hash = shared._hash
    def measured(fd):
        info = os.fstat(fd)
        if info.st_ino in watched:
            calls.append(info.st_ino)
        return original_hash(fd)
    monkeypatch.setattr(shared, '_hash', measured)
    monkeypatch.setattr(views, '_hash', measured)
    for _ in range(2):
        calls.clear()
        with views.private_image_view(root, digest, workspace,
                                      lambda *_: pytest.fail('warm launch extracted')):
            assert calls.count(image.stat().st_ino) == 1
            assert calls.count(program.stat().st_ino) == 1  # includes its hardlink
        assert calls.count(image.stat().st_ino) == 1
        assert calls.count(program.stat().st_ino) == 1
    assert not lifecycle.load_state(root)['leases']


def test_cold_derivation_hashes_rootfs_twice_and_reuses_frozen_receipt(store, monkeypatch, clone_double):
    root, digest, workspace, _ = store
    counts = []
    original = views._hash
    def counted(fd):
        counts.append(os.fstat(fd).st_size)
        return original(fd)
    monkeypatch.setattr(views, '_hash', counted)
    with views.private_image_view(root, digest, workspace, extract) as view:
        assert (view['rootfs'] / 'bin/program').read_bytes() == b'original bytes' * 8192
        assert counts.count(len(b'original bytes') * 8192) == 2
    assert counts.count(len(b'original bytes') * 8192) == 2
    assert not list(workspace.iterdir())


def test_freeze_mutation_rejected_before_publication(store, monkeypatch):
    root, digest, _, _ = store
    original = views._inventory
    def mutate_after_freeze(path, **kwargs):
        entries = original(path, **kwargs)
        if kwargs.get('freeze'):
            program = path / 'bin/program'
            program.chmod(0o600)
            program.write_bytes(b'changed' * 8192)
            program.chmod(0o555)
        return entries
    monkeypatch.setattr(views, '_inventory', mutate_after_freeze)
    with pytest.raises(shared.SharedRuntimeImageError, match='integrity'):
        derive(store)
    assert not views.derived_path(root, digest).exists()


def test_published_derivation_rechecks_members_without_rehash(store, monkeypatch):
    root, digest, _, _ = store
    rename = views.os.rename
    def tamper_after_publication(source, destination, **kwargs):
        rename(source, destination, **kwargs)
        if source.startswith('.derive-') and destination.startswith('.rootfs-'):
            program = views.derived_path(root, digest) / 'rootfs/bin/program'
            program.chmod(0o600)
            program.write_bytes(b'changed')
            program.chmod(0o555)
    monkeypatch.setattr(views.os, 'rename', tamper_after_publication)
    with pytest.raises(shared.SharedRuntimeImageError, match='integrity'):
        derive(store)


@pytest.mark.parametrize('phase', ['cloning', 'execution'])
@pytest.mark.parametrize('mutation', ['source_restore', 'tree_restore', 'replace',
                                    'symlink', 'metadata', 'membership', 'hardlink'])
def test_generation_rechecks_reject_mutation(store, monkeypatch, clone_double, phase, mutation):
    root, digest, workspace, image = store
    path, _ = derive(store)
    program = path / 'rootfs/bin/program'
    def mutate():
        if mutation in {'source_restore', 'tree_restore', 'metadata'}:
            selected = image if mutation == 'source_restore' else (
                path / 'manifest.json' if mutation == 'metadata' else program)
            info, content = selected.stat(), selected.read_bytes()
            selected.chmod(0o600)
            selected.write_bytes(b'X' * len(content))
            selected.write_bytes(content)
            os.utime(selected, ns=(info.st_atime_ns, info.st_mtime_ns))
            selected.chmod(info.st_mode & 0o7777)
        elif mutation == 'hardlink':
            os.link(program, workspace / 'unexpected-link')
        else:
            folder = program.parent
            folder.chmod(0o700)
            if mutation == 'replace':
                payload = program.read_bytes()
                program.unlink()
                program.write_bytes(payload)
                program.chmod(0o555)
            elif mutation == 'symlink':
                program.unlink()
                program.symlink_to('/not-an-admitted-input')
            else:
                (folder / 'new-member').write_text('not admitted')
            folder.chmod(0o555)
    real_clone = views._clone
    def clone(*args):
        real_clone(*args)
        if phase == 'cloning':
            mutate()
    monkeypatch.setattr(views, '_clone', clone)
    with pytest.raises(shared.SharedRuntimeImageError):
        with views.private_image_view(root, digest, workspace, extract):
            assert phase == 'execution', 'changed generation reached engine'
            mutate()
    assert not lifecycle.load_state(root)['leases']
    assert not any(p.name.startswith('.image-view-') for p in workspace.iterdir())


def test_inventory_and_snapshot_open_ancestry_once(store, monkeypatch):
    path, manifest = derive(store)
    rootfs = path / 'rootfs'
    original = views._directory
    calls = []
    def counted(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(views, '_directory', counted)
    views.snapshot_tree(rootfs)
    assert calls == [rootfs.parent]
    calls.clear()
    assert views._inventory(rootfs) == manifest['frozen']
    assert calls == [rootfs.parent]


def test_real_cow_preserves_modes_hardlinks_and_source(store):
    root, digest, workspace, image = store
    before = shared.verify_image(image, digest)
    calls = []
    def once(fd, destination):
        calls.append(1)
        extract(fd, destination)
    try:
        with views.private_image_view(root, digest, workspace, once) as view:
            program = view['rootfs'] / 'bin/program'
            assert program.stat().st_mode & 0o7777 == 0o755
            assert (view['rootfs'] / 'sticky').stat().st_mode & 0o7777 == 0o1777
            assert os.readlink(view['rootfs'] / 'link') == 'bin/program'
            assert program.stat().st_ino == (view['rootfs'] / 'bin/alias').stat().st_ino
            assert program.stat().st_ino != (views.derived_path(root, digest) / 'rootfs/bin/program').stat().st_ino
            program.write_bytes(b'private mutation')
            assert (views.derived_path(root, digest) / 'rootfs/bin/program').read_bytes().startswith(b'original bytes')
            assert os.fstat(view['image_fd']).st_ino == before['inode']
        with views.private_image_view(root, digest, workspace, once) as view:
            assert (view['rootfs'] / 'bin/program').read_bytes().startswith(b'original bytes')
        assert calls == [1]
    except views.CoWUnavailable as exc:
        assert not list(workspace.iterdir())
        assert not lifecycle.load_state(root)['leases']
        pytest.skip('Actual local filesystem unavailable: ' + str(exc))
    assert shared.verify_image(image, digest) == before
    assert not list(workspace.iterdir())
    assert not lifecycle.load_state(root)['leases']


def test_no_full_copy_fallback(store, monkeypatch):
    root, digest, workspace, image = store
    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, 'fixture unsupported filesystem')
    monkeypatch.setattr(views.fcntl, 'ioctl', unsupported)
    with pytest.raises(views.CoWUnavailable):
        with views.private_image_view(root, digest, workspace, extract):
            pytest.fail('must not execute without real CoW')
    assert not list(workspace.iterdir())
    assert not lifecycle.load_state(root)['leases']
    shared.verify_image(image, digest)


def test_reuse_does_not_reextract_and_retains_source_lease(store):
    root, digest, workspace, image = store
    calls = []
    def once(fd, destination):
        calls.append(1)
        empty(fd, destination)
    before = shared.verify_image(image, digest)
    for _ in range(2):
        with views.private_image_view(root, digest, workspace, once) as view:
            assert view['image'] == image
            assert view['identity'] == before
            plan = lifecycle.plan_retirement(root, digest)
            assert plan['derivation']
            assert any('private-image-view:' in reason for reason in plan['reasons'])
            with pytest.raises(lifecycle.Error, match='retained'):
                lifecycle.apply_retirement(root, plan, maintenance_authorization='test only')
        with pytest.raises(OSError):
            os.fstat(view['image_fd'])
    assert calls == [1]
    assert shared.verify_image(image, digest) == before


@pytest.mark.parametrize('during_extract', [True, False])
def test_source_mutation_fails_closed(store, during_extract):
    root, digest, workspace, image = store
    def mutate():
        image.chmod(0o600)
        image.write_bytes(b'changed source bytes')
        image.chmod(0o400)
    def callback(fd, destination):
        empty(fd, destination)
        if during_extract:
            mutate()
    with pytest.raises(shared.SharedRuntimeImageError):
        with views.private_image_view(root, digest, workspace, callback):
            mutate()
    assert not lifecycle.load_state(root)['leases']
    assert not workspace.exists() or not list(workspace.iterdir())
    if during_extract:
        assert not views.derived_path(root, digest).exists()


def test_concurrent_views_publish_once_with_independent_leases(store):
    root, digest, workspace, _ = store
    barrier, calls = Barrier(2), []
    def callback(fd, destination):
        calls.append(1)
        empty(fd, destination)
    def execute(_):
        with views.private_image_view(root, digest, workspace, callback) as view:
            barrier.wait(timeout=10)
            assert len(lifecycle.load_state(root)['leases']) == 2
            result = view['rootfs']
            barrier.wait(timeout=10)
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(execute, range(2)))
    assert len(set(paths)) == 2
    assert calls == [1]
    assert not lifecycle.load_state(root)['leases']
    assert not list(workspace.iterdir())


def test_corrupt_derivation_is_not_replaced(store):
    root, digest, workspace, _ = store
    path, _ = derive(store)
    program = path / 'rootfs/bin/program'
    program.chmod(0o600)
    program.write_bytes(b'corrupt')
    program.chmod(0o555)
    with pytest.raises(shared.SharedRuntimeImageError, match='integrity'):
        with views.private_image_view(root, digest, workspace, lambda *_: pytest.fail('must not heal')):
            pytest.fail('corrupt rootfs admitted')
    assert program.read_bytes() == b'corrupt'


@pytest.mark.parametrize('where', ['extract', 'execute'])
def test_cancellation_cleans_private_state(store, where):
    root, digest, workspace, _ = store
    def callback(fd, destination):
        empty(fd, destination)
        if where == 'extract':
            raise KeyboardInterrupt('cancel extraction')
    with pytest.raises(KeyboardInterrupt):
        with views.private_image_view(root, digest, workspace, callback):
            raise KeyboardInterrupt('cancel execution')
    assert not lifecycle.load_state(root)['leases']
    assert not workspace.exists() or not list(workspace.iterdir())
    assert not any(p.name.startswith('.derive-') for p in (root / 'objects/sha256').iterdir())


def test_execution_rechecks_derived_integrity(store):
    root, digest, workspace, _ = store
    with pytest.raises(shared.SharedRuntimeImageError, match='integrity'):
        with views.private_image_view(root, digest, workspace, empty):
            tree = views.derived_path(root, digest) / 'rootfs'
            tree.chmod(0o700)
            (tree / 'injected').write_text('changed by external owner')
            tree.chmod(0o555)
    assert not lifecycle.load_state(root)['leases']
    assert not list(workspace.iterdir())


def test_mode000_extraction_metadata_and_private_cleanup(store):
    root, digest, workspace, _ = store
    def callback(fd, destination):
        empty(fd, destination)
        (destination / 'locked').mkdir()
        (destination / 'locked').chmod(0)
    with views.private_image_view(root, digest, workspace, callback) as view:
        assert (view['rootfs'] / 'locked').stat().st_mode & 0o777 == 0
        view['rootfs'].chmod(0)
    assert not list(workspace.iterdir())


def test_derivation_symlink_rejected(store, tmp_path):
    root, digest, workspace, _ = store
    outside = tmp_path / 'outside'
    outside.mkdir()
    views.derived_path(root, digest).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        with views.private_image_view(root, digest, workspace, empty):
            pytest.fail('symlink admitted')
    assert not list(outside.iterdir())


def test_retirement_moves_derived_with_source_and_fences_stale_plan(store):
    root, digest, workspace, image = store
    original = lifecycle.plan_retirement(root, digest)
    derive(store)
    with pytest.raises(lifecycle.Error, match='stale'):
        lifecycle.apply_retirement(root, original, maintenance_authorization='test')
    plan = lifecycle.plan_retirement(root, digest)
    retired = lifecycle.apply_retirement(root, plan, maintenance_authorization='test')
    assert not image.exists()
    assert not views.derived_path(root, digest).exists()
    assert retired.with_name(retired.name + '.rootfs').is_dir()
    lifecycle.recover_image_derivations(root)
    with pytest.raises(FileNotFoundError):
        with views.private_image_view(root, digest, workspace, empty):
            pytest.fail('retired image admitted')


def test_interrupted_retirement_recovery(store, monkeypatch):
    root, digest, _, image = store
    derive(store)
    plan = lifecycle.plan_retirement(root, digest)
    rename = os.rename
    def interrupt(source, destination, **kwargs):
        if source == '.rootfs-' + digest:
            raise KeyboardInterrupt('crash between quarantine renames')
        return rename(source, destination, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle.os, 'rename', interrupt)
        with pytest.raises(KeyboardInterrupt):
            lifecycle.apply_retirement(root, plan, maintenance_authorization='test')
    assert not image.exists()
    assert views.derived_path(root, digest).exists()
    lifecycle.recover_image_derivations(root)
    assert not views.derived_path(root, digest).exists()
    lifecycle.recover_image_derivations(root)


def test_interrupted_stage_recovery_no_symlink_traversal(store, tmp_path):
    root, digest, _, _ = store
    stage = views.derived_path(root, digest).parent / ('.derive-' + digest + '-' + 'a' * 32)
    stage.mkdir()
    outside = tmp_path / 'preserved'
    outside.write_text('preserve')
    (stage / 'rootfs').mkdir()
    (stage / 'rootfs/link').symlink_to(outside)
    lifecycle.recover_image_derivations(root)
    assert not stage.exists()
    assert outside.read_text() == 'preserve'

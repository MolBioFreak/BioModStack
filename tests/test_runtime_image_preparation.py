"""Preparation-only fixtures; no image programs, engines or network access."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import runtime_image_views as views
from lib import runtime_image_lifecycle as lifecycle
from lib import shared_runtime_images as shared


@pytest.fixture
def image(tmp_path, monkeypatch):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'preparation fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    root = tmp_path / 'store'
    path = shared.publish_image(source, root, digest)
    def forbidden(*args, **kwargs):
        pytest.fail('preparation must not create private views or spawn native programs')
    monkeypatch.setattr(views, '_clone', forbidden)
    monkeypatch.setattr(views, 'private_image_view', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield root, digest, path
    for folder, _, _ in os.walk(tmp_path):
        os.chmod(folder, 0o700)


def extract(fd, destination):
    assert os.pread(fd, 100, 0) == b'preparation fixture'
    destination.mkdir()
    (destination / 'data').write_bytes(b'fixture rootfs')


def test_cold_once_warm_reuses_and_hashes_once(image, monkeypatch):
    root, digest, path = image
    calls = []
    def counted(fd, destination):
        calls.append(destination)
        assert any(r['owner'] == 'test-preload' for r in lifecycle.load_state(root)['leases'].values())
        extract(fd, destination)
    first = views.prepare_image(root, digest, counted, owner='test-preload')
    hashes = []
    original = shared._hash
    def measured(fd):
        hashes.append(os.fstat(fd).st_ino)
        return original(fd)
    monkeypatch.setattr(shared, '_hash', measured)
    monkeypatch.setattr(views, '_hash', measured)
    second = views.prepare_image(root, digest, counted)
    assert first == second
    assert len(calls) == 1
    assert hashes.count(path.stat().st_ino) == 1
    assert hashes.count((first['rootfs'] / 'data').stat().st_ino) == 1
    assert not lifecycle.load_state(root)['leases']
    assert not list(root.rglob('.image-view-*'))


@pytest.mark.parametrize('damage', ['image', 'rootfs', 'manifest', 'symlink'])
def test_corruption_is_not_repaired_or_reextracted(image, damage):
    root, digest, path = image
    result = views.prepare_image(root, digest, extract)
    target = path if damage == 'image' else result['rootfs'] / 'data'
    if damage == 'manifest':
        target = result['rootfs'].parent / 'manifest.json'
    if damage == 'symlink':
        target.parent.chmod(0o700)
        target.unlink()
        target.symlink_to(path)
    else:
        target.chmod(0o600)
        target.write_bytes(b'corrupt')
        target.chmod(0o400)
    with pytest.raises((shared.SharedRuntimeImageError, OSError)):
        views.prepare_image(root, digest, lambda *_: pytest.fail('must not reextract corruption'))
    assert not lifecycle.load_state(root)['leases']


def test_size_mismatch_before_extraction(image):
    root, digest, _ = image
    with pytest.raises(shared.SharedRuntimeImageError, match='size_mismatch'):
        views.prepare_image(root, digest, lambda *_: pytest.fail('unexpected extraction'), expected_size=0)
    assert not views.derived_path(root, digest).exists()
    assert not lifecycle.load_state(root)['leases']


def test_cold_preparation_reads_source_once(image, monkeypatch):
    root, digest, path = image
    calls = []
    original = shared._hash
    def measured(fd):
        if os.fstat(fd).st_ino == path.stat().st_ino:
            calls.append(1)
        return original(fd)
    monkeypatch.setattr(shared, '_hash', measured)
    views.prepare_image(root, digest, extract)
    assert calls == [1]


@pytest.mark.parametrize('mutation', ['write_restore', 'replace'])
def test_source_changed_during_extraction_rejected(image, mutation):
    root, digest, path = image
    def tamper(fd, destination):
        extract(fd, destination)
        before, payload = path.stat(), path.read_bytes()
        if mutation == 'replace':
            path.parent.chmod(0o700)
            path.unlink()
            path.write_bytes(payload)
            path.parent.chmod(0o500)
        else:
            path.chmod(0o600)
            path.write_bytes(b'X' * len(payload))
            path.write_bytes(payload)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        path.chmod(0o400)
    with pytest.raises(shared.SharedRuntimeImageError):
        views.prepare_image(root, digest, tamper)
    assert not views.derived_path(root, digest).exists()
    assert not lifecycle.load_state(root)['leases']


def test_extraction_failure_releases_owned_lease_and_stage(image):
    root, digest, _ = image
    def failed(fd, destination):
        extract(fd, destination)
        raise RuntimeError('extractor failed')
    with pytest.raises(RuntimeError, match='extractor failed'):
        views.prepare_image(root, digest, failed)
    assert not lifecycle.load_state(root)['leases']
    assert not views.derived_path(root, digest).exists()
    assert not list((root / 'objects/sha256').glob('.derive-*'))

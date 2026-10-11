"""Tiny opaque fixtures: allocation ownership, not scientific qualification."""
import hashlib
import os
from pathlib import Path

import pytest

from test_member_redirect_transport import source, cleanup_readonly_objects, members, materialize
from lib import shared_runtime_images as images
from lib import runtime_image_lifecycle as lifecycle


def test_native_layouts_allocate_no_additional_bodies(source, tmp_path, monkeypatch):
    artifact, handler = source
    def no_copy(*args):
        pytest.fail('acquisition/layout allocated a second payload')
    monkeypatch.setattr(images, '_copy', no_copy)
    receipt = materialize(members(artifact), tmp_path)
    root = Path(receipt['path'])
    leaves = [root / name for name in receipt['members']]
    assert all(p.is_symlink() for p in leaves)
    identities = {(p.stat().st_dev, p.stat().st_ino) for p in leaves}
    assert len(identities) == 1
    assert all(p.stat().st_nlink == 1 for p in leaves)
    assert materialize(members(artifact), tmp_path) == receipt
    assert len(handler.requests) == 1
    objects = list(tmp_path.rglob('runtime.sif'))
    assert len(objects) == 1
    assert leaves[0].stat().st_blocks == objects[0].stat().st_blocks


@pytest.mark.parametrize('interrupt', [False, True])
def test_owned_adoption_keeps_inode_and_recovers(tmp_path, monkeypatch, interrupt):
    source = tmp_path / 'incoming.sif'
    source.write_bytes(b'inert SIF standin')
    original = source.stat()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / 'store'
    def no_copy(*args):
        pytest.fail('owned same-device adoption copied payload')
    monkeypatch.setattr(images, '_copy', no_copy)
    rename = os.rename
    def fault(src, dst, **kwargs):
        if str(src).startswith('.adopt-'):
            raise OSError('publication interruption')
        return rename(src, dst, **kwargs)
    if interrupt:
        monkeypatch.setattr(images.os, 'rename', fault)
        with pytest.raises(OSError, match='interruption'):
            lifecycle.publish_leased_image(source, store, digest, owner='fixture', adopt_owned=True)
        assert not source.exists()
        monkeypatch.setattr(images.os, 'rename', rename)
    result, token, identities = lifecycle.publish_leased_image(
        source, store, digest, owner='fixture', adopt_owned=True)
    assert result.stat().st_ino == original.st_ino
    assert result.stat().st_blocks == original.st_blocks
    assert result.stat().st_nlink == 1
    assert result.stat().st_mode & 0o777 == 0o400
    assert not source.exists()
    assert images.verify_image(result, digest) == identities[digest]
    assert lifecycle.publish_leased_image(source, store, digest, owner='fixture', adopt_owned=True) == (result, token, identities)
    assert not list((store / 'objects/sha256').glob('.adopt-*'))


def test_acquisition_retry_finishes_adoption_without_redownload(source, tmp_path, monkeypatch):
    from lib.pinned_acquisition import acquire
    artifact, handler = source
    rename = os.rename
    def fault(src, dst, **kwargs):
        if str(src).startswith('.adopt-'):
            raise OSError('publication interruption')
        return rename(src, dst, **kwargs)
    monkeypatch.setattr(images.os, 'rename', fault)
    with pytest.raises(OSError, match='interruption'):
        acquire(artifact, tmp_path, test_only=True)
    monkeypatch.setattr(images.os, 'rename', rename)
    receipt = acquire(artifact, tmp_path, test_only=True)
    assert Path(receipt['path']).is_file()
    assert len(handler.requests) == 1
    assert acquire(artifact, tmp_path, test_only=True) == receipt
    assert len(handler.requests) == 1


def test_concurrent_owned_publishers_share_one_lease(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    source = tmp_path / 'incoming.sif'
    source.write_bytes(b'concurrent inert payload')
    inode = source.stat().st_ino
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    def no_copy(*args):
        pytest.fail('concurrent adoption copied payload')
    monkeypatch.setattr(images, '_copy', no_copy)
    def publish(_):
        return lifecycle.publish_leased_image(source, tmp_path / 'store', digest,
                                              owner='shared-job', adopt_owned=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(publish, range(3)))
    assert results == [results[0]] * 3
    assert results[0][0].stat().st_ino == inode
    assert results[0][0].stat().st_nlink == 1


def test_default_publication_preserves_legacy_source(tmp_path):
    source = tmp_path / 'legacy.sif'
    source.write_bytes(b'legacy opaque payload')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = images.publish_image(source, tmp_path / 'store', digest)
    assert source.read_bytes() == result.read_bytes()
    assert source.stat().st_ino != result.stat().st_ino
    assert result.stat().st_nlink == 1


def test_hardlinked_donor_keeps_compatibility_copy(tmp_path):
    source = tmp_path / 'incoming.sif'
    source.write_bytes(b'opaque payload')
    os.link(source, tmp_path / 'legacy-name')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = images.publish_image(source, tmp_path / 'store', digest, adopt_owned=True)
    assert source.exists()
    assert source.stat().st_mode & 0o777 != 0o400
    assert result.stat().st_nlink == 1
    assert result.stat().st_ino != source.stat().st_ino

"""Canonical reader tests use real filesystem bytes, never scientific inference."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path

import pytest

from services.frustrampnn import runtime
from lib.shared_runtime_images import publish_image
from test_remote_bundle_runtime_gaps import package


@pytest.fixture
def selected(tmp_path, monkeypatch):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'fixture image, not scientific inference')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / 'shared'
    canonical = publish_image(source, store, digest)
    legacy = tmp_path / 'containers/frustrampnn.sif'
    legacy.parent.mkdir()
    legacy.symlink_to(canonical)
    identity = replace(runtime.FRUSTRAMPNN_RUNTIME_IDENTITY,
                       configured_sif_path=str(canonical), sif_sha256=digest)
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(canonical))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setattr(runtime, 'get_container_path', lambda name: legacy.parent / name)
    monkeypatch.setattr(runtime, 'get_container_dir', lambda: legacy.parent)
    return canonical, legacy, identity


def test_canonical_reader_keeps_store_and_descriptor_identity(selected):
    canonical, legacy, identity = selected
    path = runtime.validate_configured_container_path(legacy, identity=identity)
    assert path == str(canonical)
    with runtime.open_verified_container(path, identity.sif_sha256) as pinned:
        assert os.fstat(pinned.fd).st_ino == canonical.stat().st_ino
        assert pinned.sha256 == identity.sif_sha256
        assert pinned.proc_path == Path(f'/proc/self/fd/{pinned.fd}')
    assert pinned.closed
    with pytest.raises(runtime.RuntimeValidationError, match='without following symlinks'):
        runtime.open_verified_container(legacy, identity.sif_sha256)


def test_original_and_compatibility_alias_can_be_absent(selected, tmp_path):
    canonical, legacy, identity = selected
    legacy.unlink()
    (tmp_path / 'source.sif').unlink()
    assert not legacy.exists()
    selected_path = runtime.validate_configured_container_path(legacy, identity=identity)
    assert selected_path == str(canonical)
    with runtime.open_verified_container(selected_path, identity.sif_sha256) as pinned:
        assert os.fstat(pinned.fd).st_ino == canonical.stat().st_ino


@pytest.mark.parametrize('mutation', ['writable', 'hardlink', 'symlink', 'parent_symlink', 'digest'])
def test_canonical_reader_rejects_invalid_objects(selected, tmp_path, mutation):
    canonical, legacy, identity = selected
    if mutation == 'writable':
        canonical.chmod(0o600)
    elif mutation == 'hardlink':
        os.link(canonical, tmp_path / 'hardlink')
    elif mutation == 'symlink':
        canonical.parent.chmod(0o700)
        canonical.unlink()
        canonical.symlink_to(tmp_path / 'source.sif')
        canonical.parent.chmod(0o500)
    elif mutation == 'parent_symlink':
        parent = canonical.parent
        moved = parent.with_name('moved')
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    else:
        canonical.chmod(0o600)
        canonical.write_bytes(b'changed')
        canonical.chmod(0o400)
    with pytest.raises(runtime.RuntimeValidationError):
        runtime.open_verified_container(canonical, identity.sif_sha256)


def test_canonical_reader_rejects_same_bytes_generation_swap(selected, monkeypatch):
    canonical, _, identity = selected
    original = runtime.verify_image
    def swap(path, digest):
        verified = original(path, digest)
        path.parent.chmod(0o700)
        other = path.with_name('replacement')
        other.write_bytes(path.read_bytes())
        other.chmod(0o400)
        other.replace(path)
        path.parent.chmod(0o500)
        return verified
    monkeypatch.setattr(runtime, 'verify_image', swap)
    with pytest.raises(runtime.RuntimeValidationError, match='generation changed'):
        runtime.open_verified_container(canonical, identity.sif_sha256)


def test_selector_cannot_choose_unregistered_digest_or_arbitrary_path(selected, monkeypatch):
    canonical, legacy, identity = selected
    for path in (legacy, canonical.with_name('other.sif'),
                 canonical.parent.parent / ('0' * 64) / 'runtime.sif'):
        monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(path))
        changed = replace(identity, configured_sif_path=str(path))
        # Legacy remains the original exact-path mode, not a canonical selection.
        if path == legacy:
            with pytest.raises(runtime.RuntimeValidationError):
                runtime.open_verified_container(path, identity.sif_sha256)
        else:
            with pytest.raises(runtime.RuntimeValidationError, match='registered digest'):
                runtime.validate_configured_container_path(path, identity=changed)


def test_retained_v1_result_reopens_without_rewriting_receipt(tmp_path, monkeypatch):
    from test_frustrampnn_manifests import _bundle
    from services.frustrampnn import manifests
    from services.frustrampnn.contracts import canonical_json_bytes
    root = tmp_path / 'result'
    root.mkdir()
    _bundle(root)
    manifest = manifests.build_result_manifest(root)
    (root / manifests.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    old = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
    current = replace(old, configured_sif_path=str(tmp_path / 'store/objects/sha256' / old.sif_sha256 / 'runtime.sif'))
    monkeypatch.setattr(runtime, 'FRUSTRAMPNN_RUNTIME_IDENTITY', current)
    # Receipt reopen is filesystem independent: only scientific identities and
    # its own original content hashes are checked, never old image availability.
    assert manifests.load_result_manifest(root) == manifest
    manifests.validate_result_manifest(root, manifest)
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


def test_canonical_source_used_for_registered_stage(selected, package, monkeypatch):
    from services.remote_execution import bundle
    canonical, _, identity = selected
    monkeypatch.setattr(runtime, 'FRUSTRAMPNN_RUNTIME_IDENTITY', identity)
    entries = bundle._runtime_assets('protenix', 'predict', {'run_frustrampnn': True})
    assert (canonical, 'containers/frustrampnn.sif') in entries

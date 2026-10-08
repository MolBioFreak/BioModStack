"""Read-only scientific selectors use existing approved releases, not aliases."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from routers import conformational_mapping as cm
from lib.runtime_image_lifecycle import commit_release, transaction
from lib.shared_runtime_images import publish_image
from prepare_runtime_image_attestation import (
    create_verified_image_reference, resolve_verified_image_reference,
    RuntimeImageAttestationError,
)

KEYS = ['BMS_PROTENIX_CONTAINER_PATH', 'BMS_CM_CONFORNETS_CONTAINER_PATH']


@pytest.fixture
def managed(tmp_path, monkeypatch):
    for key in KEYS + ['BMS_RUNTIME_IMAGE_STORE', 'BMS_RUNTIME_IMAGE_LANE']:
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / '.image-store'
    source = tmp_path / 'source.sif'
    source.write_bytes(b'test approved image')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    image = publish_image(source, root, digest)
    with transaction(root):
        commit_release(root, 'production', {key: {'sha256': digest, 'path': str(image)} for key in KEYS})
    source.unlink()
    monkeypatch.setattr(cm, 'get_container_dir', lambda: tmp_path)
    monkeypatch.setattr(cm, 'get_weights_root', lambda: tmp_path / 'weights')
    checkpoint = tmp_path / 'weights/protenix/checkpoint/protenix-v2.pt'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'checkpoint')
    monkeypatch.setattr(cm._frustrampnn_runtime, 'cm_analysis_runtime_registry_v1', lambda _: {})
    yield root, image, digest
    # Restore permissions only on this test's temporary object directories so
    # pytest can retire its fixtures (never follow deliberately hostile links).
    for directory in (root / 'objects/sha256').iterdir():
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o700)


@pytest.mark.parametrize('explicit', [False, True])
def test_registry_uses_approved_cas_without_originals(managed, monkeypatch, explicit):
    root, image, digest = managed
    if explicit:
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
        for key in KEYS:
            monkeypatch.setenv(key, str(image))
    before = (root / 'references/state.json').read_bytes()
    for backend in ['protenix_v2_ensemble', 'confornets']:
        assert cm._runtime_registry(backend)['container_digest'] == 'sha256:' + digest
    assert (root / 'references/state.json').read_bytes() == before
    assert not (root.parent / 'protenix.sif').exists()
    assert not (root.parent / 'confornets-canonical.sif').exists()


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'writable', 'symlink', 'parent_symlink', 'hardlink'])
def test_actual_cas_fails_closed(managed, fault):
    root, image, _ = managed
    image.parent.chmod(0o700)
    if fault == 'missing':
        image.unlink()
    elif fault == 'corrupt':
        image.chmod(0o600)
        image.write_bytes(b'corrupt')
        image.chmod(0o400)
    elif fault == 'writable':
        image.chmod(0o600)
    elif fault == 'symlink':
        other = root.parent / 'other'
        image.rename(other)
        image.symlink_to(other)
    elif fault == 'parent_symlink':
        parent = image.parent
        other = parent.with_name('moved-object')
        parent.rename(other)
        parent.symlink_to(other, target_is_directory=True)
    else:
        os.link(image, root.parent / 'hardlink')
    image.parent.chmod(0o500)
    with pytest.raises(HTTPException) as exc:
        cm._runtime_registry('protenix_v2_ensemble')
    assert exc.value.status_code == 503


def test_legacy_published_projection_is_read_only_authority(managed, monkeypatch):
    root, image, digest = managed
    (root / 'references/state.json').unlink()  # synthetic pre-upgrade store
    monkeypatch.setenv(KEYS[0], str(image))
    projection = (root / 'references/production.env').read_bytes()
    assert cm._runtime_registry('protenix_v2_ensemble')['container_digest'] == 'sha256:' + digest
    assert not (root / 'references/state.json').exists()
    assert (root / 'references/production.env').read_bytes() == projection


def test_unregistered_path_digest_is_not_approval(managed, monkeypatch):
    root, _, _ = managed
    source = root.parent / 'arbitrary.sif'
    source.write_bytes(b'unapproved image')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    other = publish_image(source, root, digest)
    monkeypatch.setenv(KEYS[0], str(other))
    with pytest.raises(HTTPException):
        cm._runtime_registry('protenix_v2_ensemble')


def test_retained_digest_not_current_lane_substitution(managed, monkeypatch, tmp_path):
    root, image, digest = managed
    source = tmp_path / 'successor.sif'
    source.write_bytes(b'successor')
    successor_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    successor = publish_image(source, root, successor_digest)
    with transaction(root):
        commit_release(root, 'production', {KEYS[0]: {'sha256': successor_digest, 'path': str(successor)}})
    monkeypatch.setenv(KEYS[0], str(image))
    assert cm._runtime_registry('protenix_v2_ensemble')['container_digest'] == 'sha256:' + digest


def test_existing_receipt_rejects_same_byte_replacement(managed, tmp_path):
    root, image, digest = managed
    reference, receipt = tmp_path / 'reference.json', tmp_path / 'receipt.json'
    create_verified_image_reference(image=image, expected_sha256=digest, store_root=root,
                                    reference=reference, receipt=receipt)
    historical = receipt.read_bytes()
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(image.read_bytes())
    replacement.chmod(0o400)
    image.parent.chmod(0o700)
    replacement.replace(image)
    image.parent.chmod(0o500)
    with pytest.raises(RuntimeImageAttestationError, match='identity changed'):
        resolve_verified_image_reference(reference=reference, receipt=receipt,
                                         expected_sha256=digest, store_root=root)
    assert receipt.read_bytes() == historical


def test_clean_install_is_unqualified_without_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, 'get_container_dir', lambda: tmp_path)
    monkeypatch.delenv('BMS_RUNTIME_IMAGE_STORE', raising=False)
    monkeypatch.delenv(KEYS[1], raising=False)
    with pytest.raises(HTTPException) as exc:
        cm._server_confornets_identity()
    assert exc.value.status_code == 503
    assert not list(tmp_path.iterdir())

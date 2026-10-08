"""Approved fixture bytes only: remote selectors do not confer lane availability."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.remote_execution import bundle, cache, images
from lib.runtime_image_lifecycle import commit_release, transaction
from lib.shared_runtime_images import publish_image


@pytest.fixture
def approved(tmp_path, monkeypatch):
    containers = tmp_path / 'containers'
    containers.mkdir()
    root = tmp_path / 'shared-store'  # configured CAS may be outside container root
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
    for _, selector in images.IMAGE_SELECTORS.values():
        monkeypatch.delenv(selector, raising=False)
    source = tmp_path / 'fixture.sif'
    source.write_bytes(b'approved fixture image')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    image = publish_image(source, root, digest)
    source.unlink()
    keys = ['BMS_PROTENIX_CONTAINER_PATH', 'BMS_CM_CONFORNETS_CONTAINER_PATH']
    with transaction(root):
        commit_release(root, 'production', {key: dict(path=str(image), sha256=digest) for key in keys})
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: containers)
    import paths
    monkeypatch.setattr(paths, 'get_container_dir', lambda: containers)
    weights = tmp_path / 'weights'
    (weights / 'protenix').mkdir(parents=True)
    (weights / 'protenix/model.pt').write_bytes(b'fixture weights')
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    runtime = tmp_path / 'runtime'
    (runtime / 'current').mkdir(parents=True)
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(runtime))
    monkeypatch.setattr(bundle, 'get_data_root', lambda: tmp_path)
    return containers, root, image, digest


@pytest.mark.parametrize('name', ['protenix.sif', 'confornets-canonical.sif'])
@pytest.mark.parametrize('selection', ['lane', 'environment', 'typed'])
def test_inventory_and_transport_share_approved_selection(approved, monkeypatch, name, selection):
    containers, root, image, digest = approved
    flag, key = images.IMAGE_SELECTORS[name]
    argv = ['nextflow', 'run', 'main.nf']
    if selection == 'environment':
        monkeypatch.setenv(key, str(image))
    if selection == 'typed':
        argv += ['--' + flag, str(image)]
    # Canonical CM is not a supported remote lane: this probes the typed boundary
    # only, not submission or scientific execution of that workflow.
    model = 'protenix' if name == 'protenix.sif' else 'selector_probe'
    if name != 'protenix.sif' and selection != 'typed':
        argv += ['--' + flag, str(images.resolve_image(name, containers))]
    compiled, effective = bundle.compile_remote_dependencies(model, 'predict', argv)
    assert compiled[compiled.index('--' + flag) + 1] == str(image)
    assets = bundle._runtime_assets(model, 'predict', effective)
    assert [(p, n) for p, n in assets if n.endswith('.sif')] == [(image, 'containers/' + name)]
    assert not (containers / name).exists()
    assert image.stat().st_nlink == 1


@pytest.mark.parametrize('fault', ['missing', 'unregistered', 'corrupt', 'writable', 'symlink', 'parent_symlink', 'hardlink'])
def test_invalid_explicit_selection_never_falls_back(approved, monkeypatch, fault):
    containers, root, image, digest = approved
    (containers / 'protenix.sif').write_bytes(b'fallback must never be read')
    selected = image
    if fault == 'missing':
        selected = root / 'missing.sif'
    elif fault == 'unregistered':
        source = root / 'unapproved.sif'
        source.write_bytes(b'arbitrary bytes')
        selected = publish_image(source, root, hashlib.sha256(source.read_bytes()).hexdigest())
    elif fault == 'corrupt':
        image.chmod(0o600)
        image.write_bytes(b'corrupt')
        image.chmod(0o400)
    elif fault == 'writable':
        image.chmod(0o600)
    elif fault == 'hardlink':
        os.link(image, root / 'second-link')
    else:
        image.parent.chmod(0o700)
        if fault == 'symlink':
            other = root / 'moved'
            image.rename(other)
            image.symlink_to(other)
        else:
            parent = image.parent
            other = parent.with_name('moved')
            parent.rename(other)
            parent.symlink_to(other, target_is_directory=True)
        image.parent.chmod(0o500)
    monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(selected))
    with pytest.raises(bundle.RemoteBundleError):
        bundle.compile_remote_dependencies('protenix', 'predict', ['nextflow', 'run', 'main.nf'])
    with pytest.raises((OSError, RuntimeError, ValueError)):
        cache.independent_plan(SimpleNamespace(kind='image', model_id='protenix'))


def test_typed_retained_selection_precedes_current_lane_and_environment(approved, monkeypatch):
    containers, root, retained, digest = approved
    source = root / 'successor.sif'
    source.write_bytes(b'successor fixture')
    successor_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    successor = publish_image(source, root, successor_digest)
    with transaction(root):
        commit_release(root, 'production', {'BMS_PROTENIX_CONTAINER_PATH': dict(path=str(successor), sha256=successor_digest)})
    monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(successor))
    before = (root / 'references/state.json').read_bytes()
    compiled, params = bundle.compile_remote_dependencies('protenix', 'predict',
        ['nextflow', 'run', 'main.nf', '--protenix_container_path', str(retained)])
    assert params['protenix_container_path'] == str(retained)
    assert (retained, 'containers/protenix.sif') in bundle._runtime_assets('protenix', 'predict', params)
    assert (root / 'references/state.json').read_bytes() == before


@pytest.mark.parametrize('kind', ['image', 'model'])
def test_standalone_provisioning_preserves_semantic_name_without_original(approved, kind):
    containers, root, image, digest = approved
    entries = cache.independent_plan(SimpleNamespace(kind=kind, model_id='protenix'))
    image_entries = [entry for entry in entries if entry.role == 'image']
    assert len(image_entries) == 1
    entry = image_entries[0]
    assert (entry.source, entry.remote_destination, entry.sha256) == (image, 'containers/protenix.sif', digest)
    assert len(entries) == (1 if kind == 'image' else 2)


def test_public_availability_and_cm_callback_gate_are_unchanged(approved, monkeypatch, tmp_path):
    from model_registry import get_registry
    from services import nextflow
    for model in ('frustrampnn', 'conformational_mapping', 'confornets_experimental'):
        with pytest.raises(ValueError, match='closure is not available'):
            cache.independent_plan(SimpleNamespace(kind='image', model_id=model))
    assert get_registry().get_model('frustrampnn') is None
    monkeypatch.setenv('BMS_WORK', str(tmp_path / 'work'))
    command = nextflow.build_nextflow_command('conformational_mapping', 'map',
        dict(cm_request_path=str(tmp_path / 'request.json'), gpu_id=0, run_frustrampnn=True),
        str(tmp_path / 'out'), job_id='fixture-cm')
    with pytest.raises(bundle.RemoteBundleError, match='Remote workflow closure is not implemented'):
        bundle.compile_remote_dependencies('conformational_mapping', 'map', command)
    containers, _, _, _ = approved
    assert images.resolve_image('confornets.sif', containers) == containers / 'confornets.sif'


def test_invalid_lane_cannot_reinstate_original(approved, monkeypatch):
    containers, root, image, digest = approved
    (containers / 'protenix.sif').write_bytes(b'not a fallback')
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'unknown')
    with pytest.raises(bundle.RemoteBundleError):
        bundle.compile_remote_dependencies('protenix', 'predict', ['nextflow', 'run', 'main.nf'])


def test_recording_rejects_changed_cas_bytes_instead_of_approving_new_hash(approved, monkeypatch):
    containers, root, image, digest = approved
    compiled, effective = bundle.compile_remote_dependencies('protenix', 'predict', ['nextflow', 'run', 'main.nf'])
    real_hash = bundle._sha256_file
    def mutate(path):
        path.chmod(0o600)
        path.write_bytes(b'changed after selector approval')
        path.chmod(0o400)
        return real_hash(path)
    monkeypatch.setattr(bundle, '_sha256_file', mutate)
    with pytest.raises((RuntimeError, ValueError)):
        bundle._record_file(image, 'runtime/containers/protenix.sif', 'runtime')


def test_frustra_central_digest_not_arbitrary_release_membership(approved, monkeypatch):
    from dataclasses import replace
    from services.frustrampnn import runtime
    containers, root, image, digest = approved
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(image))
    monkeypatch.setattr(runtime, 'get_container_dir', lambda: containers)
    monkeypatch.setattr(runtime, 'get_container_path', lambda name: containers / name)
    monkeypatch.setattr(runtime, 'FRUSTRAMPNN_RUNTIME_IDENTITY', replace(
        runtime.FRUSTRAMPNN_RUNTIME_IDENTITY, configured_sif_path=str(image), sif_sha256=digest))
    compiled, effective = bundle.compile_remote_dependencies('protenix', 'predict',
        ['nextflow', 'run', 'main.nf', '--run_frustrampnn', 'true'])
    assets = bundle._runtime_assets('protenix', 'predict', effective)
    assert (image, 'containers/frustrampnn.sif') in assets
    assert effective['frustrampnn_container_path'] == str(image)
    assert not (containers / 'frustrampnn.sif').exists()
    source = root / 'wrong-science.sif'
    source.write_bytes(b'wrong scientific image')
    wrong_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    wrong = publish_image(source, root, wrong_digest)
    with transaction(root):
        commit_release(root, 'production', {'BMS_FRUSTRAMPNN_SIF': dict(path=str(wrong), sha256=wrong_digest)})
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(wrong))
    with pytest.raises(bundle.RemoteBundleError):
        bundle.compile_remote_dependencies('protenix', 'predict',
            ['nextflow', 'run', 'main.nf', '--run_frustrampnn', 'true'])

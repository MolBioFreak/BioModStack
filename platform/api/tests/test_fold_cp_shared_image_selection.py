"""Real filesystem/production compiler coverage; fixture bytes are not inference."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import nextflow
from services.remote_execution import bundle, cache, images
from publish_runtime_images import publish_references
from lib.shared_runtime_images import publish_image

KEY = 'BMS_FOLD_CP_CONTAINER_PATH'
MODEL = 'boltz_cp_experimental'


@pytest.fixture
def installation(tmp_path, monkeypatch):
    import paths
    containers, weights, store = (tmp_path / name for name in ('containers', 'weights', 'store'))
    containers.mkdir()
    (weights / 'boltz').mkdir(parents=True)
    (weights / 'boltz' / 'fixture-weight').write_bytes(b'fixture, not scientific weights')
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'development')
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.setenv('BMS_CONTAINER_DIR', str(containers))
    monkeypatch.setenv('BMS_WEIGHTS', str(weights))
    monkeypatch.setenv('BMS_WORK', str(tmp_path / 'work'))
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
        monkeypatch.setattr(owner, 'get_data_root', lambda: tmp_path)
        monkeypatch.setattr(owner, 'get_inputs_dir', lambda: tmp_path / 'inputs')
        monkeypatch.setattr(owner, 'get_results_dir', lambda: tmp_path / 'results')
    source = containers / 'fold-cp-pinned.sif'
    source.write_bytes(b'offline Fold-CP image fixture, not executable')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (containers / 'fold-cp.sif').symlink_to(source.name)
    return containers, store, source, digest


def compile_request():
    from schemas import JobCreate
    return nextflow.compile_workflow_provision_request(JobCreate(
        name='image fixture', model_id=MODEL, mode='design', params={
            'sequence': 'MKTIIALSYIFCLVFADYKDDDDA',
            'sequence_name': 'image_selection', 'pinned_gpus': [0, 1, 2, 3],
            'boltz_use_msa': False, 'run_frustrampnn': False,
        }))


def publish(installation):
    containers, store, source, digest = installation
    publish_references(store, 'development', {KEY: {'source': str(source), 'sha256': digest}})
    return store / 'objects' / 'sha256' / digest / 'runtime.sif'


def test_cold_compiler_preserves_legacy_path_but_asset_reader_refuses_symlink(installation):
    containers, store, source, digest = installation
    invocation = compile_request()
    assert invocation.native_parameters['bcp_container_path'] == str(containers / 'fold-cp.sif')
    with pytest.raises(bundle.RemoteBundleError, match='no-follow'):
        cache.workflow_plan(None, compiled_plan=invocation.execution_plan)
    assert not store.exists()
    (containers / 'fold-cp.sif').unlink()
    source.rename(containers / 'fold-cp.sif')
    entries, _ = cache.workflow_plan(None, compiled_plan=compile_request().execution_plan)
    assert [e.source for e in entries if e.role == 'image'] == [containers / 'fold-cp.sif']


@pytest.mark.parametrize('selection', ['lane', 'environment'])
def test_real_compiler_preflight_and_bundle_share_one_registered_object(installation, monkeypatch, selection):
    containers, store, source, digest = installation
    image = publish(installation)
    if selection == 'environment':
        monkeypatch.setenv(KEY, str(image))
    # Neither a conventional symlink nor its source is needed after publication.
    (containers / 'fold-cp.sif').unlink()
    source.unlink()
    before = image.stat()
    for _ in range(2):
        invocation = compile_request()
        assert invocation.native_parameters['bcp_container_path'] == str(image)
        entries, plan = cache.workflow_plan(None, compiled_plan=invocation.execution_plan)
        selected = [e for e in entries if e.role == 'image']
        assert [(e.source, e.remote_destination, e.sha256) for e in selected] == [
            (image, 'containers/fold-cp.sif', digest)]
        native = []
        argv = nextflow.build_nextflow_command(MODEL, 'design', {
            'sequence': 'MKTIIALSYIFCLVFADYKDDDDA', 'pinned_gpus': [0, 1, 2, 3],
            'boltz_use_msa': False, 'run_frustrampnn': False,
        }, str(containers.parent / 'out'), job_id='fixture-bundle', native_invocations=native)
        invocation = native[0]
        assert argv[argv.index('--bcp_container_path') + 1] == str(image)
        command, params = bundle.compile_remote_dependencies(MODEL, 'design', argv,
                                                             native_invocation=invocation)
        assert command[command.index('--bcp_container_path') + 1] == str(image)
        assert [(p, n) for p, n in bundle._runtime_assets(MODEL, 'design', params,
                native_invocation=invocation) if n.endswith('.sif')] == [(image, 'containers/fold-cp.sif')]
        # Existing local execution lease/environment carries exactly this object.
        refs = images.bind_local_image_references({'fold-cp.sif': image}, containers, owner='fixture-fold-cp')
        assert refs['environment'][KEY] == str(image)
        assert refs['environment']['BMS_SELECTED_IMAGE_FOLD_CP_SIF'] == str(image)
    assert image.stat().st_ino == before.st_ino
    assert image.stat().st_nlink == 1
    assert list(store.glob('objects/sha256/*/runtime.sif')) == [image]


@pytest.mark.parametrize('fault', ['unregistered', 'symlink', 'bad_digest', 'missing'])
@pytest.mark.parametrize('selection', ['environment', 'explicit'])
def test_invalid_selection_never_falls_back(installation, monkeypatch, fault, selection):
    containers, store, source, digest = installation
    image = publish(installation)
    if fault == 'unregistered':
        source.write_bytes(b'unapproved different build')
        image = publish_image(source, store, hashlib.sha256(source.read_bytes()).hexdigest())
    elif fault == 'symlink':
        image = containers / 'fold-cp.sif'
    elif fault == 'missing':
        image = store / 'objects' / 'sha256' / ('a' * 64) / 'runtime.sif'
    else:
        image.chmod(0o600)
        image.write_bytes(b'wrong digest')
        image.chmod(0o400)
    params = {'bcp_container_path': str(image)} if selection == 'explicit' else {}
    if selection == 'environment':
        monkeypatch.setenv(KEY, str(image))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        nextflow.build_nextflow_command(MODEL, 'design', {
            'sequence': 'MKTIIALSYIFCLVFADYKDDDDA', 'pinned_gpus': [0],
            'container_dir': str(containers), **params,
        }, str(containers.parent / 'out'), job_id='fixture-invalid')


def test_registration_is_required_even_for_valid_canonical_environment(installation, monkeypatch):
    containers, store, source, digest = installation
    image = publish_image(source, store, digest)
    monkeypatch.setenv(KEY, str(image))
    with pytest.raises(ValueError, match='not a retained managed reference'):
        compile_request()


def test_public_workflow_preparation_uses_real_compiler(installation):
    from schemas import JobCreate
    image = publish(installation)
    request = JobCreate(name='image fixture', model_id=MODEL, mode='design', params={
        'sequence': 'MKTIIALSYIFCLVFADYKDDDDA', 'pinned_gpus': [0, 1, 2, 3],
        'boltz_use_msa': False, 'run_frustrampnn': False,
    })
    entries, plan = cache.workflow_plan(SimpleNamespace(workflow_request=request))
    assert json.loads(plan.native_parameters_json)['bcp_container_path'] == str(image)
    assert [e.source for e in entries if e.role == 'image'] == [image]

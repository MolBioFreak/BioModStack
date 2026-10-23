"""Actual transport owners, tiny inert bodies; no scientific execution."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from services.remote_execution import bundle, hf_assets
from scripts.lib import portable_inputs as portable
from scripts.lib import runtime_image_views as views
from test_remote_bundle_runtime_gaps import package


@pytest.fixture
def protenix_package(tmp_path, monkeypatch, request):
    # Isolate this owner-level Protenix transport test from unrelated registry
    # declarations. Load the real descriptor; no model execution is substituted.
    import model_registry
    definitions = tmp_path / 'model-definitions'
    definitions.mkdir()
    source = Path(model_registry.__file__).parent / 'config/models/protenix.yaml'
    shutil.copy2(source, definitions / source.name)
    registry = model_registry.ModelRegistry(definitions)
    monkeypatch.setattr(model_registry, 'get_registry', lambda: registry)
    return request.getfixturevalue('package')


def test_actual_bundle_managed_image_has_no_body_inventory(protenix_package, monkeypatch, record_property):
    from lib.shared_runtime_images import publish_image
    from lib.runtime_image_lifecycle import transaction, commit_release
    roots, _, job, target, command = protenix_package
    image = roots['containers'] / 'protenix.sif'
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    store = roots['containers'] / '.image-store'
    selected = publish_image(image, store, digest)
    with transaction(store):
        commit_release(store, 'production', {'BMS_PROTENIX_CONTAINER_PATH':
                                            dict(path=str(selected), sha256=digest)})
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
    original = bundle._sha256_file
    image_reads = []
    def hashed(path):
        if path == selected:
            image_reads.append(str(path))
        return original(path)
    monkeypatch.setattr(bundle, '_sha256_file', hashed)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                           native_invocation=job.native_invocation)
    assert len(prepared.runtime_images) == 1
    assert prepared.runtime_images[0].sha256 == digest
    assert prepared.runtime_images[0].source == selected
    assert image_reads == []
    record_property('managed_bundle_image_hash_reads', len(image_reads))


@pytest.mark.parametrize('fallback', [False, True])
def test_archive_private_allocation_unchanged(tmp_path, monkeypatch, record_property, fallback):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'main.nf').write_bytes(b'// real archived source fixture\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    counts = {'clone_attempts': 0, 'clone_successes': 0, 'fallback_bytes': 0}
    ioctl, pread = views.fcntl.ioctl, views.os.pread
    def clone(*args):
        counts['clone_attempts'] += 1
        if fallback:
            raise OSError(errno.EOPNOTSUPP, 'forced unsupported filesystem')
        result = ioctl(*args)
        counts['clone_successes'] += 1
        return result
    def read(fd, size, offset):
        data = pread(fd, size, offset)
        counts['fallback_bytes'] += len(data)
        return data
    monkeypatch.setattr(views.fcntl, 'ioctl', clone)
    monkeypatch.setattr(views.os, 'pread', read)
    digests = []
    for name in ('cold', 'warm'):
        digests.append(bundle._staged_source_archive(repo, tmp_path / 'data', revision,
                                                    tmp_path / name, extract=False))
    source = tmp_path / 'data/remote-execution/source-archives' / (revision + '.tar.gz')
    cold, warm = [tmp_path / name / '.bms-source.tar.gz' for name in ('cold', 'warm')]
    assert digests[0] == digests[1] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert source.read_bytes() == cold.read_bytes() == warm.read_bytes()
    assert len({p.stat().st_ino for p in (source, cold, warm)}) == 3
    assert {p.stat().st_mode & 0o777 for p in (source, cold, warm)} == {0o444}
    assert counts['clone_attempts'] == 2
    assert counts['fallback_bytes'] == (2 - counts['clone_successes']) * source.stat().st_size
    if fallback:
        assert counts['clone_successes'] == 0
    record_property('allocation', json.dumps(counts, sort_keys=True))
    cold.chmod(0o644)
    cold.write_bytes(b'private mutation')
    assert source.read_bytes() == warm.read_bytes()


def test_managed_image_declaration_skips_publication_inventory(tmp_path, monkeypatch):
    from lib.shared_runtime_images import publish_image
    from lib.runtime_image_lifecycle import transaction, commit_release
    containers = tmp_path / 'containers'
    containers.mkdir()
    image = containers / 'fixture.sif'
    image.write_bytes(b'inert immutable image, not model output')
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    store = containers / '.image-store'
    selected = publish_image(image, store, digest)
    with transaction(store):
        commit_release(store, 'production', {'BMS_RUNTIME_IMAGE_FIXTURE_SIF':
                                            dict(path=str(selected), sha256=digest)})
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
    monkeypatch.delenv('BMS_RUNTIME_IMAGE_FIXTURE_SIF', raising=False)
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: containers)
    def forbidden(*args, **kwargs):
        pytest.fail('managed image declaration must not scan publication or image bytes')
    monkeypatch.setattr(hf_assets, 'published_asset_rows', forbidden)
    monkeypatch.setattr(bundle, '_sha256_file', forbidden)
    rows = bundle._runtime_records(selected, 'runtime/containers/fixture.sif', publication={})
    assert [(r.relative_path, r.sha256, r.size_bytes) for r in rows] == [
        ('runtime/containers/fixture.sif', digest, selected.stat().st_size)]


@pytest.mark.parametrize('original_present', [False, True])
def test_md_original_and_derivative_portable_after_controller_hidden(tmp_path, monkeypatch, original_present):
    root = tmp_path / 'controller'
    root.mkdir()
    derivative = root / 'selected.pdb'
    derivative.write_bytes(b'MODEL        2\nENDMDL\n')
    original = root / 'original.pdb'
    original.write_bytes(b'MODEL        1\nENDMDL\nMODEL        2\nENDMDL\n')
    document = root / 'job.json'
    document.write_text(json.dumps({'schema': 'bms.md.job.v3', 'input': {'structure': str(derivative)}}))
    params = {'md_job_config': str(document)}
    if original_present:
        params['md_source_provenance'] = {'source_path': '/absent/observational/original.pdb', 'original_snapshot': {
            'path': str(original), 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}}
    refs, assets = _input_inventory(tmp_path, monkeypatch, root, params, 'molecular_dynamics', 'simulate')
    assert {r['source_path'] for r in refs} == {str(document), str(derivative), *([str(original)] if original_present else [])}
    _place_and_hide(tmp_path, monkeypatch, root, refs, assets)
    assert portable.resolve_input_path(derivative).read_bytes() == b'MODEL        2\nENDMDL\n'
    if original_present:
        assert portable.resolve_input_path(original).read_bytes() != portable.resolve_input_path(derivative).read_bytes()
    relocated = portable.resolve_input_path(document)
    bound = portable.bind_native_document(json.loads(relocated.read_text()), 'md-job', owner=relocated)
    assert Path(bound['input']['structure']).read_bytes() == portable.resolve_input_path(derivative).read_bytes()


def _input_inventory(tmp_path, monkeypatch, root, params, model, mode):
    from types import SimpleNamespace
    for name in ('get_data_root', 'get_inputs_dir', 'get_results_dir'):
        monkeypatch.setattr(bundle, name, lambda: root)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: tmp_path / 'weights')
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: tmp_path / 'containers')
    refs = []
    assets = bundle._input_assets(params, native_invocation=SimpleNamespace(
        model_id=model, mode=mode, generated_inputs=()), repo_root=tmp_path / 'repo',
        runtime_paths=set(), output_dir=root, references=refs)
    return refs, assets


def _place_and_hide(tmp_path, monkeypatch, controller, refs, assets=None):
    worker = tmp_path / 'worker'
    transfers, records = [], []
    if assets is None:
        assets = [(Path(ref['source_path']), str(i) + '/' + Path(ref['source_path']).name)
                  for i, ref in enumerate(refs)]
    for source, logical in assets:
        relative = 'inputs/' + logical
        target = worker / 'bundle' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        transfers.append(bundle.TransferPlan(source, str(target)))
        records.extend(bundle._records_for_source(source, relative, 'input'))
    transfer, record = bundle._write_portable_bindings(staging_root=tmp_path,
        remote_attempt=str(worker), references=refs, input_transfers=transfers,
        input_records=records, remote_runtime=str(worker / 'runtime'), remote_results=str(worker / 'results'))
    Path(transfer.remote_destination).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transfer.source, transfer.remote_destination)
    assert hashlib.sha256(Path(transfer.remote_destination).read_bytes()).hexdigest() == record.sha256
    monkeypatch.setenv(portable.ENV, transfer.remote_destination)
    shutil.rmtree(controller)


@pytest.mark.parametrize('selection_model', [None, 'different-generator', 'tiny'])
def test_pack_and_downstream_layout_use_declared_owner(tmp_path, monkeypatch, selection_model):
    from types import SimpleNamespace
    import model_registry
    import paths
    from services.remote_execution import cache
    from test_one_copy_remote_runtime_core import pinned
    layout, native, obj = pinned(tmp_path, monkeypatch)
    weights = layout.parents[2]
    (weights / 'tiny').symlink_to(layout)
    declared = model_registry.model_acquisition_plan('tiny')
    calls = []
    def acquisition(owner):
        calls.append(owner)
        return declared if owner == 'tiny' else {'artifacts': []}
    monkeypatch.setattr(model_registry, 'model_acquisition_plan', acquisition)
    monkeypatch.setattr(model_registry, 'get_registry', lambda: SimpleNamespace(
        list_models=lambda: [SimpleNamespace(id='different-generator'), SimpleNamespace(id='tiny')]))
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    selection = (SimpleNamespace(kind='workflow_pack', workflow_id='de_novo_design')
                 if selection_model is None else SimpleNamespace(kind='model', model_id=selection_model))
    refs = [SimpleNamespace(kind='weights', relative_path='tiny')]
    entries = cache.independent_plan(selection, dependencies=refs, publication={})
    assert [(e.source, e.remote_destination, e.sha256) for e in entries] == [
        (obj, 'weights/tiny/native/checkpoint.bin', obj.parent.name)]
    assert calls.count('tiny') == 1  # discovery/records share operation-local metadata
    if selection_model == 'tiny':
        assert calls == ['tiny']  # don't widen an already matching declaration
    # The exact declared layout is still enforced, never arbitrary symlink data.
    native.parent.chmod(0o700)
    native.unlink()
    alien = tmp_path / 'alien'
    alien.write_bytes(obj.read_bytes())
    alien.chmod(0o400)
    native.symlink_to(alien)
    native.parent.chmod(0o500)
    from scripts.lib.pinned_acquisition import AcquisitionError
    with pytest.raises(AcquisitionError, match='selected acquisition object'):
        cache.independent_plan(selection, dependencies=refs, publication={})


def test_prepared_rfd4_state_closure_is_opaque_and_portable(tmp_path, monkeypatch):
    root = tmp_path / 'controller'
    closure = root / 'closure'
    closure.mkdir(parents=True)
    payloads = {'state.pt': b'opaque state\x00', 'original.cif': b'data_original\n',
                'request.json': b'{"annotation":"/unavailable/observational/path"}'}
    for name, body in payloads.items():
        (closure / name).write_bytes(body)
    params = {'rfd4_input_closure': str(closure), 'rfd4_request': '/absent/original-request',
              'native_sources': '/absent/provenance'}
    assert portable.prepared_generation_source_fields('rfd4_proteina', 'evaluate', params) == {'rfd4_request', 'native_sources'}
    refs = portable.discover_native_input_references('rfd4_proteina', 'evaluate', params, (),
                output_dir=root, allowed_roots=(root,))
    assert {Path(r['source_path']).name for r in refs} == set(payloads)
    _place_and_hide(tmp_path, monkeypatch, root, refs)
    for name, body in payloads.items():
        assert portable.resolve_input_path(closure / name).read_bytes() == body

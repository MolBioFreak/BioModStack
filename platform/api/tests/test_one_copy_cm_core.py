"""Current compiler/prep/retry receiving fixtures; no scientific execution."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts.lib import portable_inputs as portable
from services import nextflow
from services.conformational_mapping.request_builder import materialize_trusted_internal_request
from tests.test_remote_bundle_path_gaps import roots
from tests.test_conformational_mapping_routing import _request_params
from tests.test_one_copy_cm_bindings import load_script


def installed(roots, form):
    payload = bytes(range(256)) * 256
    digest = hashlib.sha256(payload).hexdigest()
    if form == 'pinned':
        checkpoint = roots['weights'] / 'objects/sha256' / digest / 'runtime.sif'
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(payload)
        checkpoint.chmod(0o400)
        layout = roots['weights'] / 'layouts/openfold3' / ('a' * 64)
        layout.mkdir(parents=True)
        (layout / 'of3-p2-155k.pt').symlink_to(os.path.relpath(checkpoint, layout))
        (roots['weights'] / 'openfold3').symlink_to(layout)
    else:
        checkpoint = roots['weights'] / 'openfold3/of3-p2-155k.pt'
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(payload)
        checkpoint.chmod(0o444)
        if form == 'leaf':
            donor = checkpoint.with_name('payload.pt')
            checkpoint.rename(donor)
            checkpoint.symlink_to(donor.name)
            checkpoint = donor
        elif form == 'hardlink':
            os.link(checkpoint, checkpoint.with_name('other-name.pt'))
    return checkpoint, digest


def request_fixture(roots, tmp_path, monkeypatch, form, retained):
    checkpoint, digest = installed(roots, form)
    root = roots['results'] / 'request'
    root.mkdir()
    logical = 'registered/historical-source-id.pt'
    if retained:
        (root / 'registered').mkdir()
        (root / logical).write_bytes(checkpoint.read_bytes())
    controls = _request_params('confornets')
    controls['confornets']['checkpoint'] = dict(path=logical, sha256=digest)
    materialized = materialize_trusted_internal_request(controls, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture')
    (root / 'cm_runtime_registry_v1.json').write_text(json.dumps(controls['confornets']['backend_identity']))
    snapshots = json.loads((Path(__file__).parent / 'fixtures/conformational_mapping/schemas/positive/all_schemas.json').read_text())
    (root / 'cm_complex_snapshots_v1.json').write_text(json.dumps([snapshots['cm_complex_snapshot_v1']]))
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: tmp_path / 'work')
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setenv('BMS_RESULTS_ROOT', str(roots['results']))
    return materialized, checkpoint


@pytest.mark.parametrize('form', ['regular', 'leaf', 'hardlink', 'pinned'])
@pytest.mark.parametrize('retained', [False, True])
def test_current_compile_preview_prepare_and_retry(roots, tmp_path, monkeypatch, form, retained):
    from routers import conformational_mapping as router
    materialized, checkpoint = request_fixture(roots, tmp_path, monkeypatch, form, retained)
    monkeypatch.setattr(router, 'get_weights_root', lambda: roots['weights'])
    original = materialized.request_path.read_bytes()
    before = checkpoint.stat()
    params = dict(materialized.launch_params, run_frustrampnn=True)
    preview = nextflow.compile_workflow_provision_request(dict(name='tiny CM preview', model_id='conformational_mapping', mode='map', params=params))
    assert 'gpu_id' not in preview.native_parameters
    assert not Path(preview.native_parameters['out_dir']).exists()
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map',
        dict(params, gpu_id=0), str(roots['results'] / 'job'), job_id='fixture')
    invocation.materialize_inputs(roots['results'] / 'job')
    envelope_path = Path(invocation.native_parameters['cm_portable_input_bindings'])
    envelope = json.loads(envelope_path.read_bytes())
    assert envelope['bindings'][0]['path'] == str(checkpoint)
    assert envelope['bindings'][0]['reference']['reference_path'].endswith('registered/historical-source-id.pt')
    names = [row.component_key for row in invocation.execution_plan.metadata.static_components]
    assert 'FinalizeConforNetsOutputs' not in names
    dependencies = [row for row in invocation.execution_plan.dependencies if row.kind == 'weights']
    assert any(row.selector == 'cn_checkpoint_path' and row.relative_path == 'openfold3/of3-p2-155k.pt' for row in dependencies)
    assert not any(row.relative_path == 'openfold3' for row in dependencies)
    inventory = nextflow.cm_runtime_references(params)
    refs = portable.discover_native_input_references('conformational_mapping', 'map', params, (),
        output_dir=roots['results'] / 'job', allowed_roots=[roots['results']], runtime_references=inventory)
    assert len([r for r in refs if r['role'] == 'runtime']) == 1
    assert not any(r['role'] == 'runtime-snapshot' for r in refs)
    prep = load_script('prep_canonical_confornets_request', monkeypatch)
    monkeypatch.setattr(prep, '_instrumented_confornets_runtime_available', lambda: True)
    monkeypatch.setenv(portable.ENV, str(envelope_path))
    prep.prepare(materialized.request_path, materialized.coordinate_plan_path, tmp_path / 'assets', tmp_path / 'native.json')
    assert json.loads((tmp_path / 'native.json').read_bytes())['params']['checkpoint_path'] == str(checkpoint)
    authority = router._build_retry_authority(materialized.request_path.parent)
    request = json.loads(original)
    plan = json.loads(materialized.coordinate_plan_path.read_bytes())
    source = materialized.request_path.parent
    for index in range(2):
        target = roots['results'] / f'retry-{index}'
        router._copy_clean_retry_authority(source_root=source, attempt_root=target,
            request_payload=request, coordinate_plan=plan, persisted_authority=authority)
        assert not (target / 'registered/historical-source-id.pt').exists()
        assert json.loads((target / 'cm_request_v1.json').read_bytes()) == request
        assert nextflow.cm_runtime_references({'cm_request_path': str(target / 'cm_request_v1.json')}) == inventory
        source = target
    assert checkpoint.stat().st_ino == before.st_ino
    assert checkpoint.stat().st_blocks == before.st_blocks
    assert checkpoint.stat().st_mode == before.st_mode
    assert checkpoint.stat().st_mtime_ns == before.st_mtime_ns
    assert materialized.request_path.read_bytes() == original
    assert not list((roots['results'] / 'job').rglob('*.pt'))
    assert not list((tmp_path / 'assets').rglob('*.pt'))
    print(json.dumps(dict(form=form, retained=retained, checkpoint_bytes=before.st_size,
        checkpoint_blocks=before.st_blocks, generated_checkpoint_bytes=0, retry_checkpoint_bytes=0)))


@pytest.mark.parametrize('defect', ['hash', 'escape'])
def test_compiler_rejects_selected_checkpoint_corruption(roots, tmp_path, monkeypatch, defect):
    materialized, checkpoint = request_fixture(roots, tmp_path, monkeypatch, 'regular', False)
    if defect == 'hash':
        checkpoint.write_bytes(b'incorrect')
    else:
        outside = tmp_path / 'outside.pt'
        checkpoint.rename(outside)
        checkpoint.symlink_to(outside)
    with pytest.raises(ValueError, match='digest|escape'):
        nextflow.cm_runtime_references(materialized.launch_params)


def test_retained_digest_uses_old_object_not_current_native_name(roots, tmp_path, monkeypatch):
    materialized, checkpoint = request_fixture(roots, tmp_path, monkeypatch, 'pinned', True)
    root_alias = roots['weights'] / 'openfold3'
    root_alias.unlink()
    root_alias.mkdir()
    (root_alias / 'of3-p2-155k.pt').write_bytes(b'new default')
    assert next(iter(nextflow.cm_runtime_references(materialized.launch_params))) == str(checkpoint)


def test_retained_private_version_remains_supported_without_default_substitution(roots, tmp_path, monkeypatch):
    from routers import conformational_mapping as router
    materialized, checkpoint = request_fixture(roots, tmp_path, monkeypatch, 'regular', True)
    monkeypatch.setattr(router, 'get_weights_root', lambda: roots['weights'])
    original_snapshot = materialized.request_path.parent / 'registered/historical-source-id.pt'
    authority = router._build_retry_authority(materialized.request_path.parent)
    checkpoint.write_bytes(b'different current default')
    inventory = nextflow.cm_runtime_references(materialized.launch_params)
    assert next(iter(inventory)) == str(original_snapshot)
    target = roots['results'] / 'legacy-retry'
    router._copy_clean_retry_authority(source_root=materialized.request_path.parent, attempt_root=target,
        request_payload=json.loads(materialized.request_path.read_bytes()),
        coordinate_plan=json.loads(materialized.coordinate_plan_path.read_bytes()), persisted_authority=authority)
    retained = target / 'registered/historical-source-id.pt'
    assert retained.read_bytes() == original_snapshot.read_bytes()
    assert retained.stat().st_ino != original_snapshot.stat().st_ino  # documented private legacy exception


def test_retry_keeps_sealed_checkpoint_authority_digest_check(roots, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from routers import conformational_mapping as router
    materialized, _ = request_fixture(roots, tmp_path, monkeypatch, 'pinned', True)
    monkeypatch.setattr(router, 'get_weights_root', lambda: roots['weights'])
    authority = router._build_retry_authority(materialized.request_path.parent)
    authority['files']['registered/historical-source-id.pt']['sha256'] = 'f' * 64
    authority['authority_sha256'] = router.canonical_sha256({k: v for k, v in authority.items() if k != 'authority_sha256'})
    with pytest.raises(HTTPException, match='checkpoint authority differs'):
        router._copy_clean_retry_authority(source_root=materialized.request_path.parent,
            attempt_root=roots['results'] / 'invalid-retry',
            request_payload=json.loads(materialized.request_path.read_bytes()),
            coordinate_plan=json.loads(materialized.coordinate_plan_path.read_bytes()), persisted_authority=authority)
    assert not (roots['results'] / 'invalid-retry').exists()


def test_protenix_pinned_layout_alias_producer_and_receipt(tmp_path, monkeypatch):
    from tests.test_one_copy_protenix_execution import fixture, prepare
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    store = tmp_path / 'weights'
    selected = store / 'objects/sha256' / digest / 'runtime.sif'
    selected.parent.mkdir(parents=True)
    checkpoint.rename(selected)
    selected.chmod(0o400)
    layout = store / 'layouts/protenix' / ('b' * 64)
    layout.parent.mkdir(parents=True)
    weights.rename(layout)
    checkpoint = layout / 'checkpoint/protenix-v2.pt'
    checkpoint.symlink_to(os.path.relpath(selected, checkpoint.parent))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'scripts'))
    before = selected.stat()
    runtime, receipt, result = prepare(tmp_path, layout, wrapper, registry)
    assert result['checkpoint']['observed_source']['sha256'] == digest
    assert result['checkpoint']['source_path'] == 'checkpoint/protenix-v2.pt'
    assert not list(runtime.rglob('*.pt'))
    assert selected.stat().st_ino == before.st_ino and selected.stat().st_blocks == before.st_blocks
    assert selected.stat().st_nlink == 1 and selected.stat().st_mode == before.st_mode
    receiver = load_script('attest_protenix_runtime', monkeypatch)
    assert receiver._validate_execution_receipt(receipt, checkpoint,
        runtime / 'bms-wrapper/run_protenix_inference.py')['receipt'] == result
    selected.write_bytes(b'wrong selected bytes')
    with pytest.raises(ValueError, match='digest|mismatch'):
        prepare(tmp_path / 'wrong-hash', layout, wrapper, registry)
    outside = tmp_path / 'outside.pt'
    outside.write_bytes(b'tiny selected checkpoint\x00\x01')
    checkpoint.unlink()
    checkpoint.symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        prepare(tmp_path / 'escape', layout, wrapper, registry)


def test_current_cm_protenix_compiler_pins_layout_and_selected_member(roots, tmp_path, monkeypatch):
    layout = roots['weights'] / 'layouts/protenix' / ('c' * 64)
    layout.mkdir(parents=True)
    member = 'checkpoint/retained-v2.pt'
    payload = b'selected Protenix weight fixture'
    digest = hashlib.sha256(payload).hexdigest()
    selected = roots['weights'] / 'objects/sha256' / digest / 'runtime.sif'
    selected.parent.mkdir(parents=True)
    selected.write_bytes(payload)
    selected.chmod(0o400)
    leaf = layout / member
    leaf.parent.mkdir()
    leaf.symlink_to(os.path.relpath(selected, leaf.parent))
    (roots['weights'] / 'protenix').symlink_to(layout)
    monkeypatch.setattr(nextflow, 'get_weights_root', lambda: roots['weights'])
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: tmp_path / 'work')
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.delenv('BMS_PROTENIX_WEIGHTS', raising=False)
    request_root = roots['results'] / 'protenix-request'
    controls = _request_params('protenix_v2_ensemble')
    controls['feature_policy']['rna_msa_enabled'] = False  # supported hosted protein-MSA request
    materialized = materialize_trusted_internal_request(controls,
        output_dir=request_root, request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture')
    registry = request_root / 'cm_runtime_registry_v1.json'
    registry.write_text(json.dumps(dict(checkpoint_relative_path=member, checkpoint_sha256=digest)))
    params = dict(materialized.launch_params, run_frustrampnn=True)
    preview = nextflow.compile_workflow_provision_request(dict(name='Protenix tiny',
        model_id='conformational_mapping', mode='map', params=params))
    assert preview.native_parameters['protenix_weights'] == str(layout)
    assert not Path(preview.native_parameters['out_dir']).exists()
    weights = [row for row in preview.execution_plan.dependencies if row.kind == 'weights']
    assert any(row.selector_subpath == member for row in weights)
    assert not any(row.selector_subpath == 'checkpoint/protenix-v2.pt' for row in weights)
    producer = load_script('prepare_protenix_execution_snapshot', monkeypatch)
    wrapper = Path(__file__).resolve().parents[3] / 'scripts/run_protenix_inference.py'
    runtime = tmp_path / 'protenix-preflight'
    receipt = tmp_path / 'protenix-receipt.json'
    produced = producer.prepare_execution_snapshot(registry_path=registry,
        weights_root=Path(preview.native_parameters['protenix_weights']), wrapper=wrapper,
        runtime_root=runtime, receipt_path=receipt)
    receiver = load_script('attest_protenix_runtime', monkeypatch)
    assert receiver._validate_execution_receipt(receipt, leaf,
        runtime / 'bms-wrapper/run_protenix_inference.py')['receipt'] == produced
    assert not list(runtime.rglob('*.pt'))


def test_cm_retained_bundle_reopens_with_real_shared_frustra_validator(tmp_path, monkeypatch):
    import shutil
    from tests.test_frustrampnn_manifests import _v2_bundle
    from services.frustrampnn.manifests import load_result_manifest_bytes_and_document, validate_result_manifest
    postprocess = load_script('postprocess_conformational_mapping_frustrampnn_v2', monkeypatch)
    donor = tmp_path / 'completed-bundle'
    manifest, _ = _v2_bundle(donor, monkeypatch)  # explicit CPU model-output fixture, real publisher
    target = tmp_path / 'cm-retained-bundle'
    target.mkdir()
    bodies = {}
    for source in donor.rglob('*'):
        destination = target / source.relative_to(donor)
        if source.is_dir():
            destination.mkdir(exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            postprocess._retain_result_file(source, destination)
            bodies[destination.name] = (source.stat().st_ino, source.stat().st_size)
            assert source.stat().st_ino == destination.stat().st_ino
    _, _, received = load_result_manifest_bytes_and_document(target)
    assert received == manifest
    payloads = validate_result_manifest(target, received)
    assert payloads
    shutil.rmtree(donor)
    assert validate_result_manifest(target, received) == payloads
    print(json.dumps(dict(shared_frustra_members=len(bodies),
        shared_frustra_bytes=sum(size for _, size in bodies.values()), additional_payload_bodies=0)))


def test_cm_protenix_selected_metadata_keeps_native_auxiliary_members():
    from model_registry import native_checkpoint_dependencies
    settings = {'cm_runtime_registry': {'checkpoint_relative_path': 'checkpoint/retained-v2.pt'},
                'protenix_use_template': True}
    for process in ('PrepareProtenixExecution', 'CanonicalProtenixEnsemble'):
        deps, blockers = native_checkpoint_dependencies(process, settings)
        members = {row.selector_subpath for row in deps}
        assert not blockers
        assert {'checkpoint/retained-v2.pt', 'common/components.cif',
                'common/components.cif.rdkit_mol.pkl', 'common/obsolete_to_successor.json',
                'common/release_date_cache.json', 'mmcif'} <= members
        assert 'checkpoint/protenix-v2.pt' not in members

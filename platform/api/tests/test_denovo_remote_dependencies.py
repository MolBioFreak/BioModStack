"""Offline managed-asset closure tests; no downloads, workers or science."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from component_runtime import SelectedExecutionPlan, SourceIdentity, canonical_bytes
from model_registry import (
    RuntimeDependencyRef, model_image_dependencies, model_runtime_dependencies,
    native_checkpoint_dependencies, selected_execution_metadata,
)
from services.remote_execution import bundle, cache
from services.remote_execution.contracts import ProvisionSelection
from test_remote_cache_integration import local_transport

MODEL = 'protein_modification_experimental'
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def assets(tmp_path, monkeypatch):
    import paths
    roots = {name: tmp_path / name for name in ('containers', 'weights', 'data')}
    for root in roots.values():
        root.mkdir()
    for module in (paths, bundle):
        monkeypatch.setattr(module, 'get_container_dir', lambda: roots['containers'])
        monkeypatch.setattr(module, 'get_weights_root', lambda: roots['weights'])
        monkeypatch.setattr(module, 'get_data_root', lambda: roots['data'])
    monkeypatch.setattr(bundle, 'get_code_root', lambda: ROOT)
    return roots


def leaf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'offline asset fixture')
    return path


def plan(params, *, entrypoint='protein_cad_experimental', model=MODEL, mode='de_novo_design'):
    entrypoint = 'workflows/' + entrypoint + '.nf'
    metadata = selected_execution_metadata(model, mode, params, entrypoint)
    return SelectedExecutionPlan(SourceIdentity('a' * 40, 'b' * 40),
        Path(entrypoint).stem, model, mode, entrypoint, b'{}', b'{}', canonical_bytes(params), metadata)


def runtime(params, **kwargs):
    selected = plan(params, **kwargs)
    return bundle._runtime_assets(selected.model_id, selected.mode, params,
        selected_plan=selected, only_kinds=frozenset({'image', 'weights', 'runtime_data'}))


@pytest.mark.asyncio
async def test_existing_catalog_exposes_family_without_scientific_request():
    from routers.execution_targets import provision_catalog
    catalog = [item.model_dump() for item in await provision_catalog()]
    assert {'kind': 'model', 'model_id': MODEL} in catalog
    assert {'kind': 'image', 'model_id': MODEL} in catalog
    assert {r.relative_path for r in model_image_dependencies(MODEL)} == {'foundry.sif'}


def test_family_union_exact_deduplicated_native_members_and_no_unrelated_assets(assets):
    refs = model_runtime_dependencies(MODEL)
    images = {r.relative_path for r in refs if r.kind == 'image'}
    assert images == {'foundry.sif', 'shape_rfd3.sif', 'disco.sif', 'laproteina.sif',
                      'pyrosetta_tools.sif', 'fampnn.sif', 'dl_binder_design.sif',
                      'boltz2.sif', 'esmfold2.sif', 'protenix.sif'}
    protenix, blockers = native_checkpoint_dependencies('RunShapeProtenixValidator', {})
    assert not blockers
    assert {r.relative_path for r in refs if r.kind == 'weights'} == {
        'foundry/checkpoints/rfd3_latest.ckpt', 'disco', 'laproteina', 'esmfold2',
        'boltz/boltz2_conf.ckpt', 'boltz/boltz2_aff.ckpt', 'boltz/mols',
        *(d.relative_path for d in protenix),
    }
    assert len(refs) == len(set(refs))
    # No RF3/foundry tree, MPNN external weights, binder engines, AF2, MSA
    # databases, template mmcif tree, custom runtime paths or optional CUTLASS.
    assert not any(r.relative_path in {'foundry', 'protenix', 'fampnn', 'proteinmpnn',
        'alphafold', 'caliby', 'rfantibody', 'ppiflow', 'boltzgen', 'cutlass'} for r in refs)
    for ref in refs:
        root = assets['containers' if ref.kind == 'image' else 'weights']
        path = root / ref.relative_path
        leaf(path if ref.kind == 'image' or '.' in path.name else path / 'fixture.bin')
    unrelated = leaf(assets['weights'] / 'foundry/checkpoints/rf3_unselected.ckpt')
    leaf(assets['weights'] / 'protenix/checkpoint/unselected.pt')
    entries = cache.independent_plan(ProvisionSelection(kind='model', model_id=MODEL))
    destinations = [entry.remote_destination for entry in entries]
    assert len(destinations) == len(set(destinations)) == len(refs)
    assert unrelated not in {entry.source for entry in entries}
    assert 'weights/foundry/checkpoints/rfd3_latest.ckpt' in destinations
    assert not any('unselected' in item for item in destinations)


@pytest.mark.parametrize('value', ['', '.', '..', '../model.pt', 'a/../b', 'a/./b',
    '/absolute', 'a//b', 'a/', 'a\\b', 'https://example.invalid/a', 'a\n'])
def test_nested_managed_ref_rejects_unsafe_spellings(value):
    with pytest.raises(ValidationError):
        RuntimeDependencyRef(kind='weights', relative_path=value)


@pytest.mark.parametrize('designer', ['fampnn', 'mpnn'])
@pytest.mark.parametrize('validator', ['boltz2', 'esmfold2', 'protenix_v2'])
def test_validated_redesign_keeps_selected_designer_and_validator(designer, validator):
    params = {'plr_seq_method': designer, 'plr_structure_validators': [validator]}
    selected = plan(params, mode='region_redesign', entrypoint='protein_local_redesign')
    images = {d.relative_path for d in selected.dependencies if d.kind == 'image'}
    assert images == {'foundry.sif', 'pyrosetta_tools.sif',
        'fampnn.sif' if designer == 'fampnn' else 'dl_binder_design.sif',
        {'boltz2': 'boltz2.sif', 'esmfold2': 'esmfold2.sif', 'protenix_v2': 'protenix.sif'}[validator]}
    family = {(d.kind, d.relative_path) for d in model_runtime_dependencies(MODEL)}
    assert {(d.kind, d.relative_path) for d in selected.dependencies
            if d.kind in {'image', 'weights'}} <= family


@pytest.mark.parametrize('designer', ['proteinmpnn', 'fampnn'])
@pytest.mark.parametrize('validators', [[], ['boltz2'], ['protenix_v2'], ['boltz2', 'protenix_v2']])
def test_shape_selected_sequence_and_peers_are_subset_of_family(designer, validators):
    params = {'shape_request': {'sequence_policy': 'auto', 'sequences_per_backbone': 1,
              'sequence_engine': designer, 'validator_suite': validators}}
    selected = plan(params, mode='shape_blueprint', entrypoint='shape_blueprint_design')
    images = {d.relative_path for d in selected.dependencies if d.kind == 'image'}
    assert images == {'shape_rfd3.sif', 'esmfold2.sif',
        'fampnn.sif' if designer == 'fampnn' else 'dl_binder_design.sif',
        *({'boltz2': 'boltz2.sif', 'protenix_v2': 'protenix.sif'}[v] for v in validators)}
    family = {(d.kind, d.relative_path) for d in model_runtime_dependencies(MODEL)}
    assert {(d.kind, d.relative_path) for d in selected.dependencies
            if d.kind in {'image', 'weights'}} <= family


def test_shape_skip_excludes_all_downstream_engines():
    selected = plan({'shape_request': {'sequence_policy': 'skip'}},
                    mode='shape_blueprint', entrypoint='shape_blueprint_design')
    assert {d.relative_path for d in selected.dependencies if d.kind == 'image'} == {'shape_rfd3.sif'}
    assert {d.relative_path for d in selected.dependencies if d.kind == 'weights'} == {
        'foundry/checkpoints/rfd3_latest.ckpt'}


@pytest.mark.parametrize('backend,override', [
    ('laproteina', None), ('laproteina', 'laproteina_data_path'),
    ('disco', 'disco_cutlass_path'),
])
def test_real_compiler_preserves_selected_cad_runtime_bindings(assets, tmp_path, backend, override):
    from services.nextflow import compile_nextflow_invocation
    leaf(assets['containers'] / (backend + '.sif'))
    leaf(assets['weights'] / backend / 'fixture.bin')
    params = {'generator': backend, 'backend': backend, 'design_task': 'unconditional',
              'target_lengths': '100', 'num_designs': 1}
    custom = assets['data'] / 'custom'
    if override:
        leaf(custom / 'fixture.bin')
        params[override] = str(custom)
    invocation = compile_nextflow_invocation(MODEL, 'de_novo_design', params,
                                           str(tmp_path / 'output'), job_id='offline-fixture')
    native = invocation.native_parameters
    assert native['pcad_backend'] == backend
    if override:
        assert native['pcad_' + override] == str(custom)
    selected = bundle._runtime_assets(MODEL, 'de_novo_design', native,
        native_invocation=invocation, only_kinds=frozenset({'image', 'weights', 'runtime_data'}))
    assert {path for path, _ in selected} == {
        assets['containers'] / (backend + '.sif'), assets['weights'] / backend,
        *([custom] if override else [])}


def test_independent_nested_member_keeps_containment_owner(assets, tmp_path, monkeypatch):
    import model_registry
    outside = leaf(tmp_path / 'unmanaged/member.ckpt')
    (assets['weights'] / 'escape').symlink_to(outside.parent, target_is_directory=True)
    monkeypatch.setattr(model_registry, 'model_runtime_dependencies', lambda _: (
        RuntimeDependencyRef(kind='weights', relative_path='escape/member.ckpt'),))
    with pytest.raises(ValueError, match='contained regular asset'):
        cache.independent_plan(ProvisionSelection(kind='model', model_id=MODEL))


@pytest.mark.parametrize('model,expected', [
    ('protenix', {('image', 'protenix.sif'), ('weights', 'protenix')}),
    ('af2', {('image', 'af2.sif'), ('image', 'pyrosetta_tools.sif'), ('weights', 'alphafold')}),
    ('proteinmpnn', {('image', 'dl_binder_design.sif'), ('image', 'pyrosetta_tools.sif')}),
])
def test_unrelated_independent_model_closures_unchanged(model, expected):
    assert {(d.kind, d.relative_path) for d in model_runtime_dependencies(model)} == expected


def test_nested_managed_ref_retains_exact_member():
    value = 'foundry/checkpoints/rfd3_latest.ckpt'
    assert RuntimeDependencyRef(kind='weights', relative_path=value).relative_path == value


@pytest.mark.parametrize('backend', ['disco', 'laproteina'])
def test_selected_cad_does_not_expand_to_family(assets, backend):
    metadata = plan({'pcad_backend': backend}).metadata
    assert {d.relative_path for d in metadata.dependencies if d.kind == 'image'} == {backend + '.sif'}
    assert not any(d.relative_path and ('foundry' in d.relative_path or 'protenix' in d.relative_path)
                   for d in metadata.dependencies)


@pytest.mark.parametrize('prefix', ['pcad_', ''])
def test_selected_disco_cutlass_only_when_explicit(assets, prefix):
    leaf(assets['containers'] / 'disco.sif')
    leaf(assets['weights'] / 'disco/DISCO.pt')
    cutlass = assets['data'] / 'operator-cutlass'
    leaf(cutlass / 'include/cutlass.h')
    params = {'pcad_backend': 'disco'}
    assert cutlass not in {path for path, _ in runtime(params)}
    params[prefix + 'disco_cutlass_path'] = str(cutlass)
    selected = runtime(params)
    assert cutlass in {path for path, _ in selected}
    assert len(selected) == 3
    assert any(d.selector == prefix + 'disco_cutlass_path' for d in plan(params).dependencies)


@pytest.mark.parametrize('checkpoint,data', [
    (None, None), (None, 'custom-data'), ('custom-checkpoints', None),
    ('custom-checkpoints', 'custom-data'), ('same', 'same'), (None, 'default'),
    ('default', None),
])
@pytest.mark.parametrize('prefix', ['pcad_', ''])
def test_laproteina_default_override_and_single_source_dedup(assets, checkpoint, data, prefix):
    leaf(assets['containers'] / 'laproteina.sif')
    default = assets['weights'] / 'laproteina'
    leaf(default / 'fixture.ckpt')
    params = {'pcad_backend': 'laproteina', 'weights_root': str(assets['weights'])}
    expected = set()
    for key, value in (('checkpoint_dir', checkpoint), ('data_path', data)):
        path = default if value in (None, 'default') else assets['data'] / value
        leaf(path / 'fixture.bin')
        expected.add(path)
        if value is not None:
            params[prefix + 'laproteina_' + key] = str(path)
    selected = runtime(params)
    actual = [path for path, _ in selected if path.suffix != '.sif']
    assert set(actual) == expected
    assert len(actual) == len(expected)
    assert assets['data'] / 'laproteina' not in actual


def test_cad_compiled_selector_precedence(assets):
    leaf(assets['containers'] / 'laproteina.sif')
    preferred = assets['weights'] / 'preferred'
    leaf(preferred / 'fixture.bin')
    params = {'pcad_backend': 'laproteina',
        'pcad_laproteina_checkpoint_dir': str(preferred),
        'pcad_laproteina_data_path': str(preferred),
        'laproteina_checkpoint_dir': '/not-selected/checkpoints',
        'laproteina_data_path': '/not-selected/data'}
    assert {path for path, _ in runtime(params)} == {assets['containers'] / 'laproteina.sif', preferred}


def test_laproteina_override_retains_existing_containment_owner(assets, tmp_path):
    leaf(assets['containers'] / 'laproteina.sif')
    leaf(assets['weights'] / 'laproteina/default.ckpt')
    outside = tmp_path / 'unmanaged'
    leaf(outside / 'fixture.bin')
    with pytest.raises(bundle.RemoteBundleError, match='escapes managed storage'):
        runtime({'pcad_backend': 'laproteina', 'pcad_laproteina_data_path': str(outside)})


@pytest.mark.parametrize('model,mode,entrypoint,params,images', [
    (MODEL, 'de_novo_design', 'protein_design',
     {'run_rfd_only': True, 'run_frustrampnn': False}, {'foundry.sif', 'pyrosetta_tools.sif'}),
    ('protein_local_redesign', 'local_redesign', 'protein_local_redesign',
     {'rfd3_request_path': '/not-materialized/request.json'}, {'foundry.sif', 'pyrosetta_tools.sif'}),
])
def test_selected_rfd3_generation_and_native_redesign_remain_narrow(model, mode, entrypoint, params, images):
    selected = plan(params, model=model, mode=mode, entrypoint=entrypoint)
    assert {d.relative_path for d in selected.dependencies if d.kind == 'image'} == images
    assert {d.relative_path for d in selected.dependencies if d.kind == 'weights'} == {
        'foundry/checkpoints/rfd3_latest.ckpt'}


@pytest.mark.asyncio
async def test_family_cold_install_and_warm_reuse(assets, tmp_path, local_transport):
    """Real cache/install helpers; local transport and inert bytes, not science."""
    import hashlib
    import uuid
    from types import SimpleNamespace
    from services.remote_execution import managed_inventory

    selection = ProvisionSelection(kind='model', model_id=MODEL)
    for ref in model_runtime_dependencies(MODEL):
        root = assets['containers' if ref.kind == 'image' else 'weights']
        path = root / ref.relative_path
        path = path if ref.kind == 'image' or '.' in path.name else path / 'fixture.bin'
        leaf(path).write_bytes(('inert family asset ' + ref.kind + '/' + ref.relative_path).encode())
    entries = cache.independent_plan(selection)
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    manifest = managed_inventory.manifest_for(selection, entries, ('a' * 40, 'b' * 40))
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    _, uploads = local_transport
    cold_uploads = 0
    for attempt in range(2):
        await cache.provision_cache(connection=connection, entries=entries,
            operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=cache._noop)
        release = await managed_inventory.activate_release(connection, manifest,
            cache._noop, cache._noop, boot)
        assert release.state == 'verified'
        observed = await managed_inventory.observe_releases(connection, [manifest], cache._noop)
        assert observed.releases[0].release_sha256 == release.release_sha256
        if attempt == 0:
            cold_uploads = len(uploads)
            assert cold_uploads > 0
        else:
            assert len(uploads) == cold_uploads
        for entry in entries:
            if entry.role == 'image':
                obj = Path(connection.remote_root) / 'cache/runtime-images/objects/sha256' / entry.sha256 / 'runtime.sif'
            else:
                obj = Path(connection.remote_root) / 'cache/artifacts/v1/objects/sha256' / entry.sha256[:2] / entry.sha256
            assert hashlib.sha256(obj.read_bytes()).hexdigest() == entry.sha256
    assert not (Path(connection.remote_root) / 'attempts').exists()


@pytest.mark.parametrize('shape', [False, True])
@pytest.mark.parametrize('custom', [False, True])
def test_boltz_selected_bundle_and_family_manifest_native_closure(assets, shape, custom):
    from services.remote_execution import managed_inventory

    for ref in model_runtime_dependencies(MODEL):
        root = assets['containers' if ref.kind == 'image' else 'weights']
        path = root / ref.relative_path
        leaf(path if ref.kind == 'image' or '.' in path.name else path / 'fixture.bin')
    root = assets['data'] / 'custom-boltz' if custom else assets['weights'] / 'boltz'
    members = ('boltz2_conf.ckpt', 'boltz2_aff.ckpt', 'mols/fixture.bin')
    extras = ('boltz/boltz2_conf.ckpt', 'boltz/mols/old.pkl', 'mols.tar',
              'home/.cache/torch/jit.bin')
    for member in (*members, *extras):
        leaf(root / member)
        leaf(assets['weights'] / 'boltz' / member)
    params: dict = ({'shape_request': {'sequence_policy': 'auto', 'sequences_per_backbone': 1,
                                     'sequence_engine': 'fampnn',
                               'validator_suite': ['boltz2']}} if shape else
              {'plr_seq_method': 'fampnn', 'plr_structure_validators': ['boltz2']})
    if custom:
        params['boltz_models'] = str(root)
    kwargs = ({'mode': 'shape_blueprint', 'entrypoint': 'shape_blueprint_design'} if shape else
              {'mode': 'region_redesign', 'entrypoint': 'protein_local_redesign'})
    selected = plan(params, **kwargs)
    deps = [d for d in selected.dependencies if d.logical_id.startswith('weights:boltz')]
    assert {d.selector_subpath for d in deps} == {'boltz2_conf.ckpt', 'boltz2_aff.ckpt', 'mols'}
    records = [record for source, name in runtime(params, **kwargs)
               if name.startswith('weights/boltz/')
               for record in bundle._records_for_source(source, name, 'runtime')]
    expected = {'weights/boltz/' + member for member in members}
    assert {record.relative_path for record in records} == expected
    selection = ProvisionSelection(kind='model', model_id=MODEL)
    manifest = managed_inventory.manifest_for(selection, cache.independent_plan(selection),
                                              ('a' * 40, 'b' * 40))
    assert {row['name'] for row in manifest['artifacts']
            if row['name'].startswith('weights/boltz/')} == expected
    assert all((root / member).exists() for member in extras)
    assert {source for source, name in runtime(params, **kwargs)
            if name.startswith('weights/boltz/')} == {
                root / 'boltz2_conf.ckpt', root / 'boltz2_aff.ckpt', root / 'mols'}
    (root / 'boltz2_aff.ckpt').unlink()
    with pytest.raises(bundle.RemoteBundleError, match='Required runtime asset is unavailable'):
        runtime(params, **kwargs)


def test_boltz_native_cli_override_preserves_historical_closure():
    dependencies, blockers = native_checkpoint_dependencies('RunBoltz',
        {'boltz_extra_config': '--model boltz1', 'boltz_models': '/custom/cache'})
    assert not blockers
    assert [(d.relative_path, d.selector, d.selector_subpath) for d in dependencies] == [
        ('boltz', 'boltz_models', None)]
    # Shape does not forward this free-form setting to the native CLI.
    dependencies, blockers = native_checkpoint_dependencies('RunShapeBoltzValidator',
        {'boltz_extra_config': '--model boltz1'})
    assert not blockers
    assert {d.selector_subpath for d in dependencies} == {'boltz2_conf.ckpt', 'boltz2_aff.ckpt', 'mols'}

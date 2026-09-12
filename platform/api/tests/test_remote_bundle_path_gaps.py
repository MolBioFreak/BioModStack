"""Input admission uses managed inventories and path components."""
from pathlib import Path
import pytest
from services.remote_execution import bundle
from component_runtime import NativeInvocation, GeneratedInput


@pytest.fixture
def roots(tmp_path, monkeypatch):
    roots = {key: tmp_path / key for key in ('data', 'inputs', 'results', 'weights', 'containers', 'repo')}
    for root in roots.values():
        root.mkdir()
    import paths
    for getter, key in [('get_data_root', 'data'), ('get_inputs_dir', 'inputs'), ('get_results_dir', 'results'), ('get_weights_root', 'weights'), ('get_container_dir', 'containers')]:
        monkeypatch.setattr(bundle, getter, lambda key=key: roots[key], raising=False)
        monkeypatch.setattr(paths, getter, lambda key=key: roots[key])
    return roots


def inputs(roots, raw, runtime=()):
    params = {'input': str(raw)}
    invocation = projection(params)
    return bundle._input_assets(params, native_invocation=invocation,
                                repo_root=roots['repo'], runtime_paths=set(runtime),
                                output_dir=roots['results']/'job')


def projection(params, generated_inputs=()):
    # Lower-layer admission fixture, not a substitute scientific compiler.
    return NativeInvocation.capture(model_id='fixture', mode='fixture', command=['nextflow'],
                                    requested=params, effective=params, native_parameters=params,
                                    entrypoint="fixture.nf", generated_inputs=generated_inputs)


def test_destination_roles_filtered_before_admission(roots):
    params = {key: str(roots['data'] / 'missing' / key) for key in (
        'work_dir', 'out_dir', 'out', 'data_root', 'code_root', 'weights_root',
        'container_dir', 'msa_cache_dir', 'cm_api_runtime_dir', 'runtime_image_store')}
    assert bundle._input_assets(params, native_invocation=projection(params),
                                repo_root=roots['repo'], runtime_paths=set(),
                                output_dir=roots['results']/'job') == []


def test_generated_input_identity_verified_at_record_boundary(roots):
    generated = GeneratedInput('native/input.json', b'{"native":true}')
    invocation = projection({}, (generated,))
    output = roots['results']/'job'
    invocation.materialize_inputs(output)
    assets = bundle._input_assets({}, native_invocation=invocation,
                                  repo_root=roots['repo'], runtime_paths=set(), output_dir=output)
    assert len(assets) == 1
    path, relative = assets[0]
    records = bundle._input_records(path, 'inputs/' + relative,
                                    native_invocation=invocation, output_dir=output)
    assert records[0].sha256 == generated.reference['sha256']
    path.write_bytes(b'changed')
    with pytest.raises(bundle.RemoteBundleError, match='identity changed'):
        bundle._input_records(path, 'inputs/' + relative,
                              native_invocation=invocation, output_dir=output)


def test_generated_children_of_selected_directory_transfer_once(roots):
    output = roots['results'] / 'job'
    generated = (GeneratedInput('batch/one.json', b'{"n":1}'),
                 GeneratedInput('batch/two.json', b'{"n":2}'))
    params = {'complex_batch_dir': str(output / 'batch')}
    invocation = projection(params, generated)
    invocation.materialize_inputs(output)
    assets = bundle._input_assets(params, native_invocation=invocation,
                                  repo_root=roots['repo'], runtime_paths=set(), output_dir=output)
    assert len(assets) == 1 and assets[0][0] == output / 'batch'
    path, relative = assets[0]
    records = bundle._input_records(path, 'inputs/' + relative,
                                   native_invocation=invocation, output_dir=output)
    assert len(records) == len(generated)
    assert {record.sha256 for record in records} == {item.reference['sha256'] for item in generated}
    (output / 'batch' / 'two.json').write_bytes(b'corrupt')
    with pytest.raises(bundle.RemoteBundleError, match='identity changed'):
        bundle._input_records(path, 'inputs/' + relative,
                              native_invocation=invocation, output_dir=output)
    (output / 'batch' / 'two.json').unlink()
    with pytest.raises(bundle.RemoteBundleError, match='disappeared'):
        bundle._input_records(path, 'inputs/' + relative,
                              native_invocation=invocation, output_dir=output)


@pytest.mark.parametrize('relative', ['../escape.json', '/absolute.json', 'nested/../escape.json', 'nested//input.json'])
def test_generated_input_requires_contained_path(relative):
    with pytest.raises(ValueError, match='contained path'):
        GeneratedInput(relative, b'{}')


@pytest.mark.parametrize('symlink_kind', ['directory', 'file'])
def test_generated_materialization_never_follows_symlinks(roots, symlink_kind):
    output = roots['results'] / 'job'
    outside = roots['data'] / 'outside'
    output.mkdir()
    outside.mkdir()
    protected = outside / 'protected.json'
    protected.write_bytes(b'original')
    if symlink_kind == 'directory':
        (output / 'linked').symlink_to(outside, target_is_directory=True)
        generated = GeneratedInput('linked/protected.json', b'changed')
    else:
        (output / 'protected.json').symlink_to(protected)
        generated = GeneratedInput('protected.json', b'changed')
    with pytest.raises((OSError, ValueError)):
        generated.materialize(output)
    assert protected.read_bytes() == b'original'


def test_missing_generated_input_is_not_materialized_by_bundle(roots):
    invocation = projection({}, (GeneratedInput('input.json', b'{}'),))
    with pytest.raises(bundle.RemoteBundleError, match='unavailable'):
        bundle._input_assets({}, native_invocation=invocation, repo_root=roots['repo'],
                             runtime_paths=set(), output_dir=roots['results']/'job')


def test_independent_managed_input_root(roots):
    path = roots['inputs'] / 'seq.fa'
    path.write_text('>a\nAAAA\n')
    assert inputs(roots, path)[0][0] == path


def test_runtime_roots_excluded_before_admission(roots):
    path = roots['weights']/'protenix'
    path.mkdir()
    assert inputs(roots, roots['weights'], [path]) == []


def test_missing_declared_input_rejected(roots):
    with pytest.raises(bundle.RemoteBundleError, match='unavailable|missing'):
        inputs(roots, roots['data']/'missing.fa')


def test_ancestor_symlink_rejected(roots):
    real = roots['data']/'real'
    real.mkdir()
    (real/'seq.fa').write_text('data')
    alias = roots['data']/'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(bundle.RemoteBundleError, match='symlink'):
        inputs(roots, alias/'seq.fa')


def test_rewrite_uses_components_not_substrings():
    mapping = {'/local/data': '/remote/data'}
    assert bundle._rewrite('/local/data/file', mapping) == '/remote/data/file'
    assert bundle._rewrite('/local/database/file', mapping) == '/local/database/file'
    assert bundle._rewrite('literal /local/data/file', mapping) == 'literal /local/data/file'
    assert bundle._rewrite('/local/data/../escape', mapping) == '/local/data/../escape'


@pytest.fixture
def portable(monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    path = root / 'scripts/lib/portable_inputs.py'
    spec = importlib.util.spec_from_file_location('portable_input_fixture', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv(module.ENV, raising=False)
    return module


@pytest.mark.parametrize("also_selected", [False, True])
def test_generated_discovery_reuses_call_local_identity(tmp_path, monkeypatch, portable, also_selected):
    generated = GeneratedInput("input.json", b'{"native":true}')
    generated.materialize(tmp_path)
    path = tmp_path / generated.relative_path
    calls = []
    original = portable._identity
    def identify(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(portable, "_identity", identify)
    params = {"input_path": str(path)} if also_selected else {}
    references = portable.discover_native_input_references("fixture", "fixture", params,
        (generated,), output_dir=tmp_path, allowed_roots=[tmp_path])
    assert calls == [path]
    assert all(row["sha256"] == generated.reference["sha256"] for row in references)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match="differs from compiler"):
        portable.discover_native_input_references("fixture", "fixture", {}, (generated,),
            output_dir=tmp_path, allowed_roots=[tmp_path])


def test_portable_nested_boltz_identity_and_different_roots(tmp_path, monkeypatch, portable):
    import json
    host, worker = tmp_path / 'host', tmp_path / 'worker'
    host.mkdir(); worker.mkdir()
    alignment = host / 'chain.a3m'
    alignment.write_bytes(b'>query\nAAAA\n')
    native = host / 'complex.json'
    document = {'components': [{'msa_path': str(alignment), 'sequence': 'AAAA'}],
                'description': str(alignment)}
    original = json.dumps(document, indent=4).encode()
    native.write_bytes(original)
    references = portable.discover_native_input_references('boltz2', 'complex',
        {'complex_json_path': str(native)}, (), output_dir=host, allowed_roots=[host])
    assert len(references) == 2
    bindings = []
    for reference in references:
        target = worker / Path(reference['source_path']).name
        target.write_bytes(Path(reference['source_path']).read_bytes())
        bindings.append({'reference': reference, 'path': str(target)})
    config = worker / 'bindings.json'
    config.write_text(json.dumps({'schema': portable.SCHEMA, 'roots': [str(worker)],
                                 'bindings': bindings}))
    monkeypatch.setenv(portable.ENV, str(config))
    derived = portable.bind_native_document(document, 'boltz-complex', owner=worker/'complex.json')
    assert derived['components'][0]['msa_path'] == str(worker/'chain.a3m')
    assert derived['description'] == str(alignment)
    assert document['components'][0]['msa_path'] == str(alignment)
    assert native.read_bytes() == original
    assert (worker/'complex.json').read_bytes() == original
    (worker/'chain.a3m').write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest/size'):
        portable.resolve_input_path(alignment)


def test_portable_discovery_rejects_nested_escape_and_symlink(tmp_path, portable):
    import json
    approved = tmp_path/'approved'; approved.mkdir()
    outside = tmp_path/'outside.a3m'; outside.write_text('outside')
    native = approved/'complex.json'
    native.write_text(json.dumps({'components': [{'msa_path': str(outside)}]}))
    with pytest.raises(ValueError, match='outside approved roots'):
        portable.discover_native_input_references('boltz2', 'complex',
            {'complex_json_path': str(native)}, (), output_dir=approved, allowed_roots=[approved])
    link = approved/'alias.a3m'; link.symlink_to(outside)
    native.write_text(json.dumps({'components': [{'msa_path': str(link)}]}))
    with pytest.raises(ValueError, match='symlink'):
        portable.discover_native_input_references('boltz2', 'complex',
            {'complex_json_path': str(native)}, (), output_dir=approved, allowed_roots=[approved])


@pytest.mark.parametrize('fmt,document,expected', [
    ('md-job', {'input': {'structure': '/native/input.pdb'}}, [('/native/input.pdb', 'structure')]),
    ('disco-json', [{'sequences': [{'ligand': {'ligand': 'FILE_/native/input.sdf'}}]}], [('/native/input.sdf', 'ligand')]),
    ('boltz-yaml', {'sequences': [{'protein': {'msa': 'empty'}}, {'protein': {'msa': '/native/query.a3m'}}], 'templates': [{'cif': '/native/template.cif'}]}, [('/native/query.a3m', 'msa'), ('/native/template.cif', 'template')]),
    ('protein-cad', {'laproteina': {'motif_pdb': '/native/motif.pdb', 'checkpoint_dir': '/runtime/checkpoints'}}, [('/native/motif.pdb', 'input'), ('/runtime/checkpoints', 'runtime')]),
])
def test_portable_native_field_roles(portable, fmt, document, expected):
    assert [(value, role) for _, value, role in portable.native_reference_fields(document, fmt)] == expected


def test_portable_yaml_discovery_uses_native_parser(tmp_path, portable):
    import yaml
    alignment = tmp_path/'supplied.a3m'; alignment.write_text('>q\nAAAA\n')
    native = tmp_path/'query.yaml'
    native.write_text('version: 1\nsequences:\n- protein:\n    id: A\n    sequence: AAAA\n    msa: supplied.a3m\n')
    original = native.read_bytes()
    refs = portable.discover_native_input_references('boltz_cp_experimental', 'predict',
        {'bcp_input_path': str(native)}, (), output_dir=tmp_path, allowed_roots=[tmp_path],
        yaml_loader=yaml.safe_load)
    assert {item['source_path'] for item in refs} == {str(native), str(alignment)}
    assert native.read_bytes() == original


def _place_closure(roots, tmp_path, monkeypatch, invocation, *, runtime_references=None):
    """Exercise the real bundle closure/record/binding producers, no runner stub."""
    import shutil
    refs = []
    assets = bundle._input_assets(invocation.native_parameters, native_invocation=invocation,
        repo_root=roots['repo'], runtime_paths=set(), output_dir=roots['results']/'job',
        references=refs, runtime_references=runtime_references)
    remote = tmp_path/'worker-attempt'
    transfers, records = [], []
    for source, relative in assets:
        prefix = 'inputs/' + relative
        records.extend(bundle._input_records(source, prefix, native_invocation=invocation,
                                             output_dir=roots['results']/'job'))
        transfers.append(bundle.TransferPlan(source, str(remote/'bundle'/prefix)))
    transfer, record = bundle._write_portable_bindings(staging_root=roots['data'],
        remote_attempt=str(remote), references=refs, input_transfers=transfers,
        input_records=records, remote_runtime=str(remote/'materialized/runtime'),
        remote_results=str(remote/'results'))
    for item in [*transfers, transfer]:
        destination = Path(item.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if item.source.is_dir():
            shutil.copytree(item.source, destination)
        else:
            shutil.copy2(item.source, destination)
    assert bundle._sha256_file(Path(transfer.remote_destination)) == record.sha256
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', transfer.remote_destination)
    return remote, refs, transfer


def test_real_bundle_nested_md_v1_native_copy_and_identity(roots, tmp_path, monkeypatch, portable):
    import copy
    import json
    import shutil
    import tarfile
    from dataclasses import replace
    from types import SimpleNamespace
    from component_runtime import SourceIdentity
    from scripts.bms_md.contract import normalize_job_config, prepare_verified_worker_inputs
    from services.md.results import _replica_protocol_matches
    source = roots['inputs']/'original.pdb'
    source.write_bytes(b'ATOM input fixture, no engine execution\n')
    output = roots['results']/'job'
    output.mkdir()
    config = normalize_job_config({'schema': 'bms.md.job.v1', 'job_id': 'job',
        'input': {'structure': str(source), 'structure_sha256': bundle._sha256_file(source),
                  'structure_bytes': source.stat().st_size}})
    original = json.dumps(config, indent=2).encode()
    generated = GeneratedInput('protocol.json', original)
    params = {'md_job_config': str(output/'protocol.json')}
    command = ['nextflow', 'run', str(roots['repo']/'main.nf'),
               '--md_job_config', params['md_job_config'], '--out_dir', str(output)]
    invocation = replace(NativeInvocation.capture(model_id='molecular_dynamics', mode='simulate',
        command=command, requested=params, effective=params, native_parameters=params,
        entrypoint='main.nf', generated_inputs=(generated,)),
        source_identity=SourceIdentity('a'*40, 'b'*40))
    from test_remote_cache_integration import cache_only_plan_fixture
    from test_remote_bundle_runtime_gaps import bundle_assignment_fixture
    invocation = cache_only_plan_fixture(invocation)
    invocation.materialize_inputs(output)
    monkeypatch.setattr(bundle, 'get_code_root', lambda: roots['repo'])
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: ('a'*40, 'b'*40))
    monkeypatch.setattr(bundle, '_git', lambda *_: 'b'*40)
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *args, **kwargs: [])
    monkeypatch.setattr(bundle, 'resolve_job_result_contract', lambda *_: {})
    (roots['repo']/'main.nf').write_text('workflow {}\n')
    def archive(argv, **kwargs):
        assert argv[:2] == ['git', 'archive']
        with tarfile.open(fileobj=kwargs['stdout'], mode='w') as handle:
            handle.add(roots['repo']/'main.nf', arcname='main.nf')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bundle.subprocess, 'run', archive)
    job = SimpleNamespace(id='job', model_id='molecular_dynamics', mode='simulate',
        output_dir=str(output), child_output_dir=None, lineage_root_job_id=None,
        parent_job_id=None, execution_source_revision='a'*40, execution_source_tree='b'*40,
        provenance=bundle_assignment_fixture(()), assigned_gpu=None)
    target = SimpleNamespace(id='target', remote_root=str(tmp_path/'remote'))
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                            native_invocation=invocation)
    for transfer in prepared.input_transfers:
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, destination)
        else:
            shutil.copy2(transfer.source, destination)
    env = prepared.envelope.environment
    assert env['BMS_PORTABLE_INPUT_BINDINGS'] == env['APPTAINERENV_BMS_PORTABLE_INPUT_BINDINGS']
    metadata = Path(env['BMS_PORTABLE_INPUT_BINDINGS'])
    assert any(r.relative_path == 'inputs/.bms/portable-input-bindings.json'
               and r.sha256 == bundle._sha256_file(metadata) for r in prepared.envelope.files)
    monkeypatch.setenv(portable.ENV, str(metadata))
    relocated = portable.resolve_input_path(output/'protocol.json')
    # Output/work roots are intentionally absent when the first input is read.
    assert not Path(prepared.envelope.output_directory).exists()
    assert relocated.read_bytes() == original == (output/'protocol.json').read_bytes()
    observed = prepare_verified_worker_inputs(relocated, Path(prepared.remote_attempt_dir)/'work')
    assert Path(observed['input']['structure']).read_bytes() == source.read_bytes()
    assert observed['input']['structure'] != config['input']['structure']
    assert _replica_protocol_matches(config, observed)
    changed = copy.deepcopy(observed)
    changed['random_seed'] += 1
    assert not _replica_protocol_matches(config, changed)
    changed = copy.deepcopy(observed)
    changed['input']['structure_sha256'] = '0'*64
    assert not _replica_protocol_matches(config, changed)
    assert (output/'protocol.json').read_bytes() == original


def test_cm_real_compiler_preserves_request_siblings_and_trusted_owner(roots, tmp_path, monkeypatch, portable):
    import json
    from services import nextflow
    import prep_canonical_confornets_request as prep
    request_root = roots['results']/'native-request'
    request_root.mkdir()
    checkpoint = request_root/'registered/model.pt'
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'selected checkpoint fixture')
    request = request_root/'cm_request_v1.json'
    request.write_text(json.dumps({'request_sha256': 'a'*64, 'confornets': {
        'checkpoint': {'path': 'registered/model.pt', 'sha256': bundle._sha256_file(checkpoint)},
        'references': []}}))
    plan = request_root/'cm_coordinate_plan_v1.json'; plan.write_text('{}')
    registry = request_root/'cm_runtime_registry_v1.json'; registry.write_text('{}')
    originals = {p: p.read_bytes() for p in (request, checkpoint, plan, registry)}
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: roots['data']/'work')
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    invocations = []
    nextflow.build_nextflow_command('conformational_mapping', 'map',
        {'cm_request_path': str(request), 'gpu_id': 0, 'run_frustrampnn': True},
        str(roots['results']/'job'), job_id='job', materialize_inputs=False,
        native_invocations=invocations)
    assert len(invocations) == 1
    runtime = {str(checkpoint): {'sha256': bundle._sha256_file(checkpoint),
        'size_bytes': checkpoint.stat().st_size, 'format': 'pt', 'path': '/unused-runtime/model.pt'}}
    remote, refs, transfer = _place_closure(roots, tmp_path, monkeypatch, invocations[0],
                                            runtime_references=runtime)
    mapped = portable.resolve_input_path(request)
    assert mapped.parent == portable.resolve_input_path(plan).parent
    assert (mapped.parent/registry.name).read_bytes() == registry.read_bytes()
    trusted = portable.trusted_results_root('/not-the-host-root')
    assert mapped.is_relative_to(trusted) and not mapped.is_relative_to(remote/'results')
    bound = prep._resolve_authenticated(mapped.parent, 'registered/model.pt',
                                        bundle._sha256_file(checkpoint), 'checkpoint')
    assert bound.read_bytes() == checkpoint.read_bytes()
    assert any(r['role'] == 'runtime-config' and r['source_path'] == str(registry) for r in refs)
    assert all(path.read_bytes() == content for path, content in originals.items())
    # No weakening of native unaliased request-owned files.
    import os
    os.link(bound, bound.parent/'alias.pt')
    with pytest.raises(prep.CanonicalPrepError, match='unaliased'):
        prep._resolve_authenticated(mapped.parent, 'registered/model.pt',
                                    bundle._sha256_file(checkpoint), 'checkpoint')


def test_runtime_directory_discovery_reuses_selected_identity(tmp_path, monkeypatch, portable):
    import json
    request = tmp_path/'cad.json'
    request.write_text(json.dumps({'backend': 'laproteina', 'laproteina': {
        'checkpoint_dir': '/selected/checkpoints'}, 'disco': {'checkpoint_path': '/unselected/model.pt'}}))
    def no_scan(*args, **kwargs):
        raise AssertionError('Runtime discovery must not enumerate checkpoint trees')
    monkeypatch.setattr(Path, 'rglob', no_scan)
    refs = portable.discover_native_input_references('protein_cad_experimental', 'design',
        {'protein_cad_request': str(request)}, (), output_dir=tmp_path, allowed_roots=[tmp_path],
        runtime_references={'/selected/checkpoints': {'sha256': 'a'*64, 'size_bytes': 42,
            'format': 'runtime-directory', 'path': '/worker/runtime/checkpoints'}})
    assert [r['source_path'] for r in refs if r['role'] == 'runtime'] == ['/selected/checkpoints']
    with pytest.raises(ValueError, match='not a selected dependency'):
        portable.discover_native_input_references('protein_cad_experimental', 'design',
            {'protein_cad_request': str(request)}, (), output_dir=tmp_path, allowed_roots=[tmp_path])


@pytest.mark.parametrize('model,mode,entrypoint,settings', [
    ('molecular_dynamics', 'simulate', 'workflows/experimental/molecular_dynamics/orchestrator.nf',
     {'md_config': {'engine': 'gromacs', 'replicas': 3}}),
    ('conformational_mapping', 'map', 'workflows/conformational_mapping.nf',
     {'cm_request': {'backend': 'external_import', 'targets': [], 'ordered_seeds': [1]}}),
])
def test_metadata_only_runtime_projection_preserves_plan(roots, monkeypatch, model, mode, entrypoint, settings):
    from dataclasses import replace
    from component_runtime import SourceIdentity
    from services.nextflow import build_selected_execution_plan
    plan = build_selected_execution_plan(model_id=model, mode=mode, entrypoint=entrypoint,
        requested=settings, effective=settings, native_parameters={},
        source_identity=SourceIdentity('a'*40, 'b'*40))
    original = plan.to_dict()
    runtime_roots = {'image': roots['containers'], 'weights': roots['weights'],
                     'database': roots['data'], 'reference_database': roots['data'],
                     'runtime_data': roots['data']}
    # Image approval has separate tests; these leaves exercise the actual shared
    # metadata selection and provisioning projection without constructing argv.
    monkeypatch.setattr(bundle, 'resolve_image', lambda relative, root, params: root/relative)
    params = {}
    for dependency in plan.dependencies:
        if dependency.kind in runtime_roots:
            relative = dependency.relative_path or 'selected/' + dependency.logical_id.replace(':', '-')
            path = runtime_roots[dependency.kind]/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'isolated dependency binding fixture')
            if dependency.relative_path is None:
                assert dependency.selector
                params[dependency.selector] = str(path)
    assets = bundle._runtime_assets(model, mode, params, selected_plan=plan)
    assert assets
    assert plan.to_dict() == original

    for invalid in (None, object(), replace(plan, model_id='other'), replace(plan, mode='other')):
        with pytest.raises(bundle.RemoteBundleError, match='matching selected execution plan'):
            bundle._runtime_assets(model, mode, {}, selected_plan=invalid)
    incomplete = replace(plan, metadata=replace(plan.metadata, closure_reviewed=False))
    with pytest.raises(bundle.RemoteBundleError, match='closure is incomplete'):
        bundle._runtime_assets(model, mode, {}, selected_plan=incomplete)


def test_prepared_cp_template_keeps_original_native_owner(roots, tmp_path, monkeypatch, portable):
    import json
    import yaml
    source = roots['inputs']/'configs'; source.mkdir()
    template = source/'template.cif'; template.write_bytes(b'template fixture')
    native = source/'input.yaml'
    payload = {'sequences': [{'protein': {'id': 'A', 'sequence': 'AAAA', 'msa': 'empty'}}],
               'templates': [{'cif': 'template.cif'}]}
    native.write_text(yaml.safe_dump(payload))
    prepared = roots['results']/'prepared'; prepared.mkdir()
    packaged = prepared/'input.yaml'; packaged.write_bytes(native.read_bytes())
    manifest = prepared/'msa-inputs.json'
    manifest.write_text(json.dumps({'schema': 'bms.boltz-cp-msa-inputs.v1', 'configs': [{
        'path': 'input.yaml', 'sha256': bundle._sha256_file(packaged),
        'source_sha256': bundle._sha256_file(native), 'chains': []}]}))
    params = {'bcp_input_path': str(prepared), 'boltz_prepared_msa_sha256': bundle._sha256_file(manifest)}
    invocation = NativeInvocation.capture(model_id='boltz_cp_experimental', mode='predict',
        command=['nextflow'], requested={'bcp_input_path': str(source)}, effective=params,
        native_parameters=params, entrypoint='boltz_cp_experimental.nf')
    original = packaged.read_bytes()
    remote, refs, _ = _place_closure(roots, tmp_path, monkeypatch, invocation)
    owner = portable.resolve_input_path(packaged)
    derived = portable.bind_native_document(yaml.safe_load(owner.read_bytes()), 'boltz-yaml', owner=owner)
    assert Path(derived['templates'][0]['cif']).read_bytes() == template.read_bytes()
    assert owner.read_bytes() == original == packaged.read_bytes() == native.read_bytes()

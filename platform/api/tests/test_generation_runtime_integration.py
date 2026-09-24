"""Pure selected metadata and local bridge-closure fixtures; no native execution."""
import json
import shutil
from pathlib import Path

import pytest

from component_runtime import NativeInvocation
from model_registry import selected_execution_metadata
from native_components import PROCESS_CONTRACTS
from services.ppiflow_generation import materialize_ppiflow_generation_request, selected_assets
from services.remote_execution import bundle
from scripts.lib.portable_inputs import resolve_input_path
from test_remote_bundle_path_gaps import roots

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
def test_ppiflow_initial_metadata_exact_mode_assets(mode):
    metadata = selected_execution_metadata('ppiflow', mode,
        {'ppiflow_generation_request': '/not-materialized/inputs/ppiflow-generation'},
        'workflows/ppiflow_generation.nf')
    assert [c.component_key for c in metadata.static_components] == ['RunPPIFlowGeneration']
    assert not metadata.dynamic_templates
    assert metadata.closure_reviewed and metadata.descriptors_reviewed
    assets = selected_assets(mode)
    assert {(d.kind, d.relative_path) for d in metadata.dependencies if d.kind in {'image', 'weights'}} == {
        ('image', assets['image']), ('weights', assets['weights'][0])}
    checkpoint = next(d for d in metadata.dependencies if d.kind == 'weights')
    assert checkpoint.selector == 'ppiflow_weights_dir'
    assert checkpoint.selector_subpath == Path(assets['weights'][0]).name
    assert json.loads(metadata.static_components[0].resources_json)['gpu']['count'] == 1
    assert 'read_ppiflow_generation_result' in metadata.retrieval_authority
    # Never advertise maturation scores/partial-flow results for initial generation.
    assert b'ppiflow_maturation' not in metadata.result_contract_json
    assert b'ppiflow_generation' in metadata.result_contract_json


@pytest.mark.parametrize('mode,protocol', [('protein_binder', 'protein-anything'),
    ('peptide_binder', 'peptide-anything'), ('nanobody_binder', 'nanobody-anything')])
@pytest.mark.parametrize('prepared', [False, True])
def test_boltzgen_graph_uses_exact_existing_processes(mode, protocol, prepared):
    from scripts.lib.boltzgen_native import selected_checkpoint_members
    params = {'boltzgen_protocol': protocol, 'boltzgen_checkpoint_mode': 'diverse',
              'boltzgen_skip_inverse_folding': True}
    if prepared:
        params['boltzgen_yaml_config'] = '/not-materialized/prepared'
    metadata = selected_execution_metadata('boltzgen', mode, params, 'workflows/boltzgen_generation.nf')
    expected = ([] if prepared else ['PrepBoltzGenInput']) + ['RunBoltzGen', 'FilterBoltzGen']
    assert [c.component_key for c in metadata.static_components] == expected
    assert not metadata.dynamic_templates
    assert metadata.static_components[-1].condition == 'RunBoltzGen.out.pdbs is nonempty'
    members = selected_checkpoint_members(protocol, 'diverse', True)
    assert {d.relative_path for d in metadata.dependencies if d.kind == 'weights'} == {
        *('boltzgen/' + member for member in members), 'boltzgen/mols.zip'}
    assert {d.relative_path for d in metadata.dependencies if d.kind == 'image'} == {
        'boltzgen.sif', 'pyrosetta_tools.sif'}


@pytest.mark.parametrize('process,module', [('RunPPIFlowGeneration', 'ppiflow'),
    ('PrepBoltzGenInput', 'boltzgen'), ('RunBoltzGen', 'boltzgen'), ('FilterBoltzGen', 'boltzgen')])
def test_descriptors_match_actual_process_declarations(process, module):
    text = (ROOT / 'modules' / (module + '.nf')).read_text()
    body = text.split('process ' + process + ' {', 1)[1].split('\nprocess ', 1)[0]
    labels, inputs, outputs, helpers, directives = PROCESS_CONTRACTS[f'modules/{module}.nf:{process}']
    for declaration in (*inputs, *outputs):
        assert declaration in body
    for label in labels:
        assert "label '" + label + "'" in body


def _invocation(model, params):
    return NativeInvocation.capture(model_id=model, mode='protein_binder', command=['nextflow'],
        requested=params, effective=params, native_parameters=params,
        entrypoint=f'workflows/{model}_generation.nf')


def _tree(roots, model):
    output = roots['results'] / 'job'
    output.mkdir()
    target = roots['inputs'] / 'target.pdb'
    target.write_text('REMARK transport-only fixture\nEND\n')
    if model == 'ppiflow':
        original = {'target_pdb': str(target), 'target_chain': 'R', 'binder_chain': 'B',
                    'specified_hotspots': 'R2'}
        transport = materialize_ppiflow_generation_request('protein_binder', original, output / 'request')
        params = {**original, **transport}
        root = Path(transport['ppiflow_generation_request'])
    else:
        from scripts.lib.boltzgen_inputs import materialize_generation_input
        original = {'boltzgen_target_pdb_path': str(target), 'boltzgen_generation_mode': 'protein_binder',
                    'boltzgen_protocol': 'protein-anything', 'boltzgen_binder_sequence': '8-12'}
        transport = materialize_generation_input(original, output / 'request', allowed_input_roots=[roots['inputs']])
        params = {**original, **transport}
        root = Path(transport['boltzgen_yaml_config'])
    target.unlink()  # Reopen the retained native snapshot, not historical source paths.
    return output, root, params


@pytest.mark.parametrize('model', ['ppiflow', 'boltzgen'])
def test_prepared_generation_directory_relocates_once_with_exact_bindings(roots, model, monkeypatch, tmp_path):
    output, root, params = _tree(roots, model)
    invocation = _invocation(model, params)
    references = []
    assets = bundle._input_assets(params, native_invocation=invocation, repo_root=roots['repo'],
        runtime_paths=set(), output_dir=output, references=references)
    assert len(assets) == 1 and assets[0][0] == root
    members = {p for p in root.rglob('*') if p.is_file()}
    assert {Path(r['source_path']) for r in references} == members
    relative = assets[0][1]
    records = bundle._input_records(root, 'inputs/' + relative, native_invocation=invocation, output_dir=output)
    assert len(records) == len(members)
    attempt = tmp_path / 'worker'
    remote = attempt / 'bundle/inputs' / relative
    shutil.copytree(root, remote)
    transfers = [bundle.TransferPlan(root, str(remote))]
    staging = tmp_path / 'staging'
    staging.mkdir()
    binding, _ = bundle._write_portable_bindings(staging_root=staging, remote_attempt=str(attempt),
        references=references, input_transfers=transfers, input_records=records,
        remote_runtime=str(attempt / 'runtime'), remote_results=str(attempt / 'results'))
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', str(binding.source))
    assert resolve_input_path(root) == remote
    for source in members:
        relocated = remote / source.relative_to(root)
        assert resolve_input_path(source) == relocated
        assert relocated.read_bytes() == source.read_bytes()
    shutil.rmtree(root)
    assert resolve_input_path(root) == remote
    # Existing byte identity check remains authoritative after relocation.
    source = next(iter(members))
    (remote / source.relative_to(root)).write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        resolve_input_path(source)


@pytest.mark.parametrize('model', ['ppiflow', 'boltzgen'])
def test_prepared_generation_retains_symlink_and_root_authority(roots, model):
    output, root, params = _tree(roots, model)
    (root / 'escape.pdb').symlink_to(roots['inputs'] / 'missing')
    with pytest.raises(bundle.RemoteBundleError, match='symlink'):
        bundle._input_assets(params, native_invocation=_invocation(model, params), repo_root=roots['repo'],
            runtime_paths=set(), output_dir=output)


@pytest.mark.parametrize('model,key', [('ppiflow', 'ppiflow_generation_request'),
    ('boltzgen', 'boltzgen_yaml_config')])
def test_prepared_generation_outside_managed_roots_is_not_transferable(roots, tmp_path, model, key):
    outside = tmp_path / 'unowned'
    outside.mkdir()
    (outside / 'request.json').write_text('{}')
    params = {key: str(outside)}
    with pytest.raises(bundle.RemoteBundleError, match='outside BMS-managed storage'):
        bundle._input_assets(params, native_invocation=_invocation(model, params), repo_root=roots['repo'],
            runtime_paths=set(), output_dir=roots['results'] / 'job')


def test_unprepared_generation_does_not_exempt_original_source_admission(roots):
    params = {'target_pdb': '/offline/original.pdb'}
    with pytest.raises(bundle.RemoteBundleError, match='Declared input is unavailable'):
        bundle._input_assets(params, native_invocation=_invocation('ppiflow', params), repo_root=roots['repo'],
            runtime_paths=set(), output_dir=roots['results'] / 'job')


@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
@pytest.mark.parametrize('custom', [False, True])
def test_compiler_plan_projects_one_checkpoint_not_the_installed_tree(roots, mode, custom):
    from component_runtime import SourceIdentity
    from services.nextflow import build_selected_execution_plan
    selected_root = roots['weights'] / ('custom' if custom else 'ppiflow')
    selected_root.mkdir()
    for name in ('binder.ckpt', 'antibody.ckpt', 'nanobody.ckpt', 'unselected.ckpt'):
        (selected_root / name).write_bytes(b'byte-only runtime fixture: ' + name.encode())
    params = {'ppiflow_generation_request': '/unmaterialized/request'}
    if custom:
        params['ppiflow_weights_dir'] = str(selected_root)
    plan = build_selected_execution_plan(model_id='ppiflow', mode=mode,
        entrypoint='workflows/ppiflow_generation.nf', requested=params, effective=params,
        native_parameters=params, source_identity=SourceIdentity('a' * 40, 'b' * 40))
    assert plan.dependency_closure_complete
    member = selected_assets(mode)['weights'][0]
    before = plan.to_dict()
    assets = bundle._runtime_assets('ppiflow', mode, params, selected_plan=plan,
        only_kinds=frozenset({'weights'}))
    assert assets == [(selected_root / Path(member).name, 'weights/' + member)]
    assert plan.to_dict() == before


def test_ppiflow_csv_transport_closure_keeps_snapshots_not_original_references(roots):
    source = roots['inputs'] / 'features.pkl'
    source.write_bytes(b'opaque trusted feature snapshot; never unpickled')
    csv = roots['inputs'] / 'native.csv'
    csv.write_text('pdb_name,processed_path\nfixture,features.pkl\n')
    output = roots['results'] / 'job'
    original = {'input_csv': str(csv)}
    params = {**original, **materialize_ppiflow_generation_request('protein_binder', original, output / 'request')}
    source.unlink()
    csv.unlink()
    references = []
    assets = bundle._input_assets(params, native_invocation=_invocation('ppiflow', params),
        repo_root=roots['repo'], runtime_paths=set(), output_dir=output, references=references)
    assert len(assets) == 1
    root = assets[0][0]
    assert {Path(ref['source_path']).relative_to(root).as_posix() for ref in references} == {
        'request.json', 'inputs/original.csv', 'inputs/native_input.csv', 'inputs/processed_0.pkl'}


@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
def test_raw_ppiflow_sources_discovered_before_request_materialization(roots, mode):
    from scripts.lib.portable_inputs import discover_native_input_references
    target = roots['inputs'] / 'target.pdb'
    framework = roots['inputs'] / 'framework.pdb'
    feature = roots['inputs'] / 'feature.pkl'
    csv = roots['inputs'] / 'source.csv'
    for source in (target, framework, feature):
        source.write_bytes(b'input-closure fixture')
    csv.write_text('pdb_name,processed_path\nsource,feature.pkl\n')
    params = {'target_pdb': str(target), 'framework_pdb': str(framework), 'input_csv': str(csv)}
    refs = discover_native_input_references('ppiflow', mode, params, (),
        output_dir=roots['results'] / 'job', allowed_roots=[roots['inputs']])
    assert {Path(ref['source_path']) for ref in refs} == {target, framework, feature, csv}
    feature_ref = next(ref for ref in refs if Path(ref['source_path']) == feature)
    assert feature_ref['owner'] == str(csv)
    assert feature_ref['lineage']
    assert not (roots['results'] / 'job').exists()


def test_historical_ppiflow_refinement_assets_unchanged():
    metadata = selected_execution_metadata('binder_refinement', 'refine',
        {'maturation_flow_enabled': True, 'maturation_repack_enabled': False,
         'maturation_anchors_enabled': False, 'maturation_redesign_enabled': False},
        'workflows/binder_refinement.nf')
    assert 'RunPartialFlow' in {c.component_key for c in metadata.static_components}
    assert {d.relative_path for d in metadata.dependencies if d.kind == 'weights'} == {'ppiflow'}

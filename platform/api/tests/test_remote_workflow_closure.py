"""Native compiler handoff tests; no provider or scientific execution.

The six synthetic entrypoints below test the lower-layer callback gate only.
They are deliberately not represented as successful producer/router coverage.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from component_runtime import NativeInvocation, SourceIdentity
from services import nextflow
from services.remote_execution import bundle as bundle_module
from services.remote_execution import executor
from services.remote_execution.bundle import compile_remote_dependencies, RemoteBundleError


def _component_context(tmp_path, identity, *, params=None, model='antibody_denovo',
                       mode='antibody_denovo_pipeline', entrypoint='workflows/antibody_denovo.nf'):
    from component_runtime import canonical_bytes
    settings = params or {'seq_design_fampnn': True, 'seq_design_antifold': False,
        'seq_design_proteinmpnn': False, 'run_structure_validation': False,
        'skip_rfantibody': True}
    plan = nextflow.build_selected_execution_plan(model_id=model, mode=mode,
        entrypoint=entrypoint, requested=canonical_bytes(settings), effective=canonical_bytes(settings),
        native_parameters=canonical_bytes(settings), source_identity=identity)
    return {'source_identity': {'revision': identity.revision, 'tree': identity.tree},
        'execution_plan': plan.to_dict(), 'plan_sha256': plan.plan_sha256,
        'artifact_root': str(tmp_path), 'resources': {'gpu_id': 2, 'gpu_ids': [2], 'cpus': 4},
        'native_runtime': {'anarcii_execution_mode': 'cpu'},
        'parent': {'id': 'parent', 'model_id': model, 'mode': mode, 'params': settings,
                   'provenance': {}, 'output_dir': str(tmp_path)}}


def _fampnn_component():
    from component_runtime import ComponentRequest
    return ComponentRequest.capture(parent_job_id='parent', stage='fampnn', child_key='0',
        payload={'name': 'native FAMPNN child', 'model_id': 'fampnn_child', 'mode': 'sequence_design',
                 'parent_job_id': 'parent', 'child_stage': 'fampnn',
                 'params': {'job_index': 0, 'total_jobs': 1, 'fampnn_seed': 17}})


def test_component_compiler_reuses_native_and_trusted_parent(tmp_path, compiler_environment, monkeypatch):
    from services.core_protein_scientific_contract import admitted_payload, workflow_params
    from services.fampnn_policy_admission import compile_declaration
    context = _component_context(tmp_path, compiler_environment)
    # Existing supported parent declaration, not a child-supplied policy marker.
    declaration = dict(schema_version=1, owner='protein_design', version=1,
        declaration='declared_protein_inputs', input_domain=['A:1:'], sequence_design=['A:1:'],
        summary=['A:1:'], fixed=[], summary_override=None, mutation_override=None,
        allow_summary_override=True, require_full_coverage=False)
    context['parent']['provenance']['fampnn_analysis_declaration'] = declaration
    request = _fampnn_component()
    monkeypatch.setattr(SourceIdentity, 'from_checkout',
        classmethod(lambda *args: pytest.fail('worker compiler probed .git')))
    child = nextflow.compile_component_nextflow_invocation(request, context)
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    normalized = normalize_job_request(JobCreate.model_validate(request.payload))
    params, provenance = admitted_payload(normalized.params, {}, 1)
    provenance['fampnn_analysis_declaration'] = compile_declaration('fampnn_child', 'sequence_design',
        params, parent=SimpleNamespace(**context['parent']))
    params = workflow_params(SimpleNamespace(model_id='fampnn_child', provenance=provenance), params)
    params['gpu_id'] = 2
    output = tmp_path / 'components' / request.component_id.replace(':', '-')
    ordinary = nextflow.compile_nextflow_invocation('fampnn_child', 'sequence_design', params,
        str(output), request.component_id, requested_params=request.payload['params'],
        source_identity=compiler_environment,
        execution_context=nextflow.NativeCompilerExecutionContext(2, (2,), 'cpu'))
    assert child.command == ordinary.command
    assert child.requested_json == ordinary.requested_json
    assert child.effective_json == ordinary.effective_json
    assert child.source_identity == compiler_environment
    assert child.native_parameters == ordinary.native_parameters
    snapshot = nextflow.component_native_parent_snapshot(child, request, context)
    assert snapshot['id'] == request.component_id
    assert snapshot['provenance']['fampnn_analysis_declaration'] == provenance['fampnn_analysis_declaration']
    assert snapshot['provenance']['core_protein_scientific_contract'] == 1
    assert snapshot['provenance']['core_protein_requested_params'] == request.payload['params']
    assert 'core_protein_scientific_contract' not in snapshot['params']
    assert 'fampnn_analysis_declaration' not in snapshot['params']
    assert request.payload['params'] == {'job_index': 0, 'total_jobs': 1, 'fampnn_seed': 17}
    assert not output.exists()
    context['execution_plan']['metadata']['dependencies'] = [row for row in
        context['execution_plan']['metadata']['dependencies'] if row['kind'] != 'image']
    with pytest.raises(ValueError, match='dependency not provisioned'):
        nextflow.compile_component_nextflow_invocation(request, context)


@pytest.mark.parametrize('stage,model,mode,settings,child_params,entrypoint', [
    ('rfantibody', 'rfantibody_child', 'antibody_backbone',
     {'parallel_mode': 'full_orchestrator'},
     {'target_pdb': '/logical/target.pdb', 'epitope_residues': '', 'rfantibody_num_designs': 2, 'framework_type': 'standard-fv'},
     'workflows/rfantibody_backbone.nf'),
    ('backbone_refine', 'template_antibody_denovo', 'maturation_child',
     {'run_ppiflow_backbone_refine': True, 'skip_rfantibody': True},
     {'pdb_paths': '/logical/backbone.pdb', 'ppiflow_mode': 'backbone_refine', 'maturation_stage_name': 'backbone_refine'},
     'workflows/maturation_child.nf'),
    ('maturation', 'template_antibody_denovo', 'maturation_child',
     {'run_ppiflow_maturation': True, 'skip_rfantibody': True},
     {'pdb_paths': '/logical/designed.pdb', 'ppiflow_mode': 'maturation', 'maturation_stage_name': 'maturation'},
     'workflows/maturation_child.nf'),
    ('maturation_post_validation', 'template_antibody_denovo', 'maturation_child',
     {'run_post_validation_maturation': True, 'skip_rfantibody': True, 'run_structure_validation': True},
     {'pdb_paths': '/logical/validated.pdb', 'ppiflow_mode': 'maturation', 'maturation_stage_name': 'maturation_post_validation'},
     'workflows/maturation_child.nf'),
])
def test_selected_native_aliases_keep_inputs_and_public_boundary(stage, model, mode, settings,
        child_params, entrypoint, tmp_path, compiler_environment, monkeypatch):
    from component_runtime import ComponentRequest
    from model_registry import get_registry
    settings = {'seq_design_fampnn': False, 'seq_design_antifold': False,
        'seq_design_proteinmpnn': False, 'run_structure_validation': False, **settings}
    context = _component_context(tmp_path, compiler_environment, params=settings)
    context['parent']['provenance'] = {'ancestor_only': 'must not inherit'}
    payload: dict = dict(name='native child', model_id=model, mode=mode, parent_job_id='parent',
        child_stage=stage, params={**child_params, 'job_index': 0, 'total_jobs': 1})
    request = ComponentRequest.capture(parent_job_id='parent', stage=stage, child_key='0', payload=payload)
    monkeypatch.setattr('services.anarcii_runtime.resolve_anarcii_runtime',
        lambda **kwargs: pytest.fail('native child probed ANARCII/GPU compatibility'))
    monkeypatch.setattr('services.gpu_config.read_scheduler_config',
        lambda: pytest.fail('native child read host scheduler reservation'))
    monkeypatch.setattr('services.msa_server.read_server_settings',
        lambda: pytest.fail('native child read host MSA device policy'))
    invocation = nextflow.compile_component_nextflow_invocation(request, context)
    assert invocation.entrypoint == entrypoint
    assert json.loads(invocation.requested_json) == payload['params']
    for key, value in child_params.items():
        if value == '':
            assert json.loads(invocation.effective_json)[key] == value
        else:
            assert invocation.native_parameters[key] == value
    assert invocation.native_parameters['gpu_id'] == 2
    snapshot = nextflow.component_native_parent_snapshot(invocation, request, context)
    assert 'ancestor_only' not in snapshot['provenance']
    assert 'core_protein_scientific_contract' not in snapshot['provenance']
    registry = get_registry()
    assert registry.validate_job_params(model, mode, payload['params'])
    assert registry.validate_job_params(model, mode, {}, native_entrypoint=entrypoint)
    assert registry.validate_job_params(model, mode, {**payload['params'], 'rfantibody_num_designs': 0}, native_entrypoint=entrypoint)
    assert registry.validate_job_params(model, mode, payload['params'], native_entrypoint='workflows/protein_design.nf')
    # Internal authority never enables a disabled backing model.
    definition = registry.get_internal_model_definition('antibody_denovo')
    monkeypatch.setattr(definition, 'enabled', False)
    assert registry.validate_job_params(model, mode, payload['params'], native_entrypoint=entrypoint)
    monkeypatch.setattr(definition, 'enabled', True)
    context['native_runtime'] = {'anarcii_execution_mode': 'gpu', 'anarcii_gpu_id': 99}
    with pytest.raises(ValueError, match='ANARCII GPU is outside'):
        nextflow.compile_component_nextflow_invocation(request, context)


def test_component_compiler_does_not_forge_fampnn_parent(tmp_path, compiler_environment):
    context = _component_context(tmp_path, compiler_environment)
    with pytest.raises(ValueError, match='trusted parent declaration'):
        nextflow.compile_component_nextflow_invocation(_fampnn_component(), context)
    context['execution_plan']['metadata']['dynamic_templates'] = []
    with pytest.raises(ValueError, match='not bound'):
        nextflow.compile_component_nextflow_invocation(_fampnn_component(), context)


def test_component_expansions_bind_native_model_mode_stage(tmp_path, compiler_environment):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('molecular_dynamics', 'simulate',
        {'md_config': {'engine': 'gromacs', 'replicas': 3}},
        'workflows/experimental/molecular_dynamics/orchestrator.nf')
    bindings = [json.loads(row.expansion_json) for row in metadata.dynamic_templates]
    assert {(r['child_model'], r['child_mode'], r['child_stage']) for r in bindings} == {
        ('molecular_dynamics', 'replica', 'md_replica'),
        ('molecular_dynamics', 'analyze', 'md_analysis')}
    context = _component_context(tmp_path, compiler_environment)
    request = _fampnn_component()
    context['child_output_dir'] = str(tmp_path / 'other')
    with pytest.raises(ValueError, match='stable component identity'):
        nextflow.compile_component_nextflow_invocation(request, context)


@pytest.fixture
def compiler_environment(tmp_path, monkeypatch):
    # Isolate host configuration; retain the real compiler and native adapters.
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    return identity


def _job(output, params, identity, *, model_id='protenix', mode='predict'):
    return SimpleNamespace(
        id='native-job', model_id=model_id, mode=mode, params=params,
        output_dir=str(output), provenance={}, parent_job_id=None,
        stage_family=None, stage_mode=None, selected_input_artifact_class=None,
        execution_source_revision=identity.revision, execution_source_tree=identity.tree,
    )


@pytest.mark.parametrize('workflow', [
    'conformational_mapping', 'protein_design', 'boltz_cp_experimental',
    'antibody_denovo', 'protein_local_redesign', 'ppiflow_generator_design',
])
def test_commands_without_native_plan_rejected_without_disabling_science(workflow):
    """A command alone is not a plan; callback filenames are not admission gates."""
    command = ['nextflow', 'run', f'workflows/{workflow}.nf', '--run_frustrampnn', 'true']
    invocation = NativeInvocation.capture(
        model_id='protenix', mode='predict', command=command,
        requested={'run_frustrampnn': True}, effective={'run_frustrampnn': True},
        native_parameters={'run_frustrampnn': True}, entrypoint=f'workflows/{workflow}.nf',
    )
    original = list(command)
    with pytest.raises(RemoteBundleError, match='complete selected native execution plan; no local fallback'):
        compile_remote_dependencies('protenix', 'predict', command, native_invocation=invocation)
    assert command == original
    assert invocation.native_parameters['run_frustrampnn'] is True


@pytest.mark.parametrize('mode', ['predict', 'complex'])
def test_self_contained_prediction_keeps_required_stage(mode, tmp_path, compiler_environment, monkeypatch):
    from services.frustrampnn.settings import FrustraMPNNRequestedSettings
    requested = {
        'sequence': 'ACDEFG', 'run_frustrampnn': True, 'gpu_id': 0,
        'protenix_use_msa': False,
        'frustrampnn_settings': FrustraMPNNRequestedSettings().model_dump(mode='json'),
    }
    job = _job(tmp_path / 'output', requested, compiler_environment, mode=mode)
    invocation = nextflow.compile_job_nextflow_invocation(job, requested, job.output_dir)
    # Placement selection is isolated, not native scientific compilation.
    monkeypatch.setattr(bundle_module, 'resolve_image', lambda name, root, params: root / name)
    assert invocation.execution_plan.complete, invocation.execution_plan.metadata.blockers
    compiled, params = compile_remote_dependencies(
        'protenix', mode, list(invocation.command), native_invocation=invocation)
    assert params['run_frustrampnn'] is True
    assert params['frustrampnn_physical_gpu_id'] == 0
    assert compiled[compiled.index('--run_frustrampnn') + 1] == 'true'
    assert not (tmp_path / 'output').exists()


def test_real_compiler_preserves_typed_snapshots_and_supported_nested_values(tmp_path, compiler_environment):
    settings = {
        'settings_value_origin': 'operator_request',
        'protein_selection': {'chains': ['A'], 'regions': []},
        'batching_enabled': False, 'structures_per_job': 1,
    }
    params = {'sequence': 'ACDEFG', 'protenix_seeds': [0, 7],
              'protenix_use_msa': False, 'protenix_oom_retry_attempts': 0,
              'frustrampnn_settings': settings}
    expected_request = json.loads(json.dumps(params))
    invocation = nextflow.compile_nextflow_invocation('protenix', 'predict', params, str(tmp_path / 'out'))
    native = invocation.native_parameters
    assert native['protenix_seeds'] == [0, 7]
    assert native['protenix_use_msa'] is False
    assert type(native['protenix_oom_retry_attempts']) is int
    assert native['protenix_oom_retry_attempts'] == 0
    assert native['frustrampnn_settings']['protein_selection'] == settings['protein_selection']
    assert native['frustrampnn_settings']['batching_enabled'] is False
    assert native['frustrampnn_settings_value_origin'] == 'operator_request'
    assert 'settings_value_origin' not in native['frustrampnn_settings']
    params['protenix_seeds'].append(99)
    settings['protein_selection']['chains'].append('B')
    native['frustrampnn_settings']['protein_selection']['chains'].clear()
    assert json.loads(invocation.requested_json) == expected_request
    assert json.loads(invocation.effective_json)['protenix_seeds'] == [0, 7]
    assert invocation.native_parameters['frustrampnn_settings']['protein_selection']['chains'] == ['A']
    with pytest.raises(FrozenInstanceError):
        setattr(invocation, 'command', ('changed',))


def test_selected_plan_preserves_identity_and_detached_settings(tmp_path, compiler_environment, monkeypatch):
    from dataclasses import replace
    from component_runtime import canonical_bytes
    from model_registry import selected_execution_metadata

    params = {'sequence': 'ACDEFG', 'protenix_use_msa': False,
              'protenix_seeds': [0, 7], 'protenix_oom_retry_attempts': 0,
              'frustrampnn_settings': {'batching_enabled': False, 'structures_per_job': 1,
                  'settings_value_origin': 'operator_request',
                  'protein_selection': {'chains': ['A'], 'regions': []}}}
    # Native compilation must not materialize or call the MSA preparation owner.
    monkeypatch.setattr(NativeInvocation, 'materialize_inputs',
                        lambda *args: pytest.fail('compile materialized inputs'))
    monkeypatch.setattr(nextflow, 'compile_controller_protenix_input',
                        lambda *args: pytest.fail('compile prepared MSA'))
    first = nextflow.compile_nextflow_invocation('protenix', 'predict', params, str(tmp_path / 'out'))
    second = nextflow.compile_nextflow_invocation('protenix', 'predict',
        dict(reversed(list(params.items()))), str(tmp_path / 'out'))
    plan = first.execution_plan
    assert plan is not None and second.execution_plan is not None
    assert first.entrypoint is not None
    assert plan.plan_sha256 == second.execution_plan.plan_sha256
    assert plan.source_identity == compiler_environment
    assert plan.requested_json == first.requested_json
    assert plan.effective_json == first.effective_json
    assert json.loads(plan.requested_json)['protenix_seeds'] == [0, 7]
    assert json.loads(plan.effective_json)['protenix_use_msa'] is False
    assert json.loads(plan.effective_json)['protenix_oom_retry_attempts'] == 0
    assert plan.requested_sha256 == hashlib.sha256(first.requested_json).hexdigest()
    projection = selected_execution_metadata(first.model_id, first.mode,
        json.loads(first.effective_json), first.entrypoint)
    assert projection == plan.metadata  # Independent/workflow consumers use this same producer.
    wire = plan.to_dict()
    wire['requested_json']['protenix_seeds'].append(999)
    wire['metadata']['static_components'].clear()
    assert json.loads(plan.requested_json)['protenix_seeds'] == [0, 7]
    assert plan.metadata.static_components
    assert plan.dependency_closure_complete
    assert not plan.metadata.blockers_for('provision')
    with pytest.raises(FrozenInstanceError):
        setattr(plan.dependencies[0], 'kind', 'changed')
    changed = nextflow.compile_nextflow_invocation('protenix', 'predict',
        {**params, 'protenix_seeds': [1, 7]}, str(tmp_path / 'out'))
    assert changed.execution_plan is not None
    assert changed.execution_plan.plan_sha256 != plan.plan_sha256
    origin = {**params, 'operator_note': False}
    rebound = replace(first, requested_json=canonical_bytes(origin))
    assert rebound.execution_plan is not None
    assert rebound.execution_plan.requested_json == canonical_bytes(origin)
    assert rebound.execution_plan.plan_sha256 != plan.plan_sha256
    assert rebound.execution_plan.metadata is plan.metadata
    source = replace(first, source_identity=SourceIdentity('c' * 40, 'd' * 40))
    assert source.execution_plan is not None
    assert source.execution_plan.plan_sha256 != plan.plan_sha256
    assert not (tmp_path / 'out').exists()


def test_selected_plan_transport_rebind_is_not_scientific_reselection(tmp_path, compiler_environment, monkeypatch):
    from dataclasses import replace
    from component_runtime import canonical_bytes
    params = {'sequence': 'ACDEFG', 'protenix_use_msa': False}
    invocation = nextflow.compile_nextflow_invocation('protenix', 'predict', params, str(tmp_path / 'out'))
    plan = invocation.execution_plan
    assert plan is not None
    monkeypatch.setattr('model_registry.selected_execution_metadata',
                        lambda *args: pytest.fail('transport reselected science'))
    bindings = {'protenix_prepared_msa_dir': '/attempt/prepared',
                'protenix_prepared_msa_sha256': 'e' * 64}
    bound = replace(invocation, native_parameters_json=canonical_bytes(
        {**invocation.native_parameters, **bindings}))
    assert bound.execution_plan is not None
    assert bound.execution_plan.plan_sha256 == plan.plan_sha256
    assert bound.execution_plan.metadata is plan.metadata
    assert json.loads(bound.execution_plan.launch_bindings_json) == bindings
    assert bound.execution_plan.native_parameters_json == plan.native_parameters_json
    with pytest.raises(ValueError, match='science changed'):
        replace(invocation, effective_json=canonical_bytes({**params, 'protenix_seeds': [123]}))
    with pytest.raises(ValueError, match='science changed'):
        replace(invocation, native_parameters_json=canonical_bytes(
            {**invocation.native_parameters, 'protenix_use_msa': True}))


def test_real_compiler_generated_json_exact_bytes_compile_only(tmp_path, compiler_environment):
    components = [{'type': 'protein', 'id': 'A', 'sequence': 'ACDEFG'}]
    output = tmp_path / 'out'
    invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', {
        'complex_components': components, 'protenix_use_msa': False,
    }, str(output))
    expected = json.dumps({'components': components}, indent=2).encode('utf-8')
    assert [(item.relative_path, item.payload) for item in invocation.generated_inputs] == [
        ('complex_definition.json', expected)]
    assert not output.exists()
    item = invocation.generated_inputs[0]
    assert item.reference == {'relative_path': 'complex_definition.json', 'size_bytes': len(expected),
                              'sha256': hashlib.sha256(expected).hexdigest(), 'role': 'input'}
    invocation.materialize_inputs(output)
    assert (output / item.relative_path).read_bytes() == expected


def test_real_compiler_generated_yaml_exact_bytes_compile_only(tmp_path, compiler_environment):
    output = tmp_path / 'out'
    invocation = nextflow.compile_nextflow_invocation('boltz_cp_experimental', 'design', {
        'sequence': 'ACDEFG', 'boltz_use_msa': False, 'gpu_id': 0,
    }, str(output))
    expected = b'version: 1\nsequences:\n- protein:\n    id:\n    - A\n    sequence: ACDEFG\n    msa: empty\n'
    assert [(item.relative_path, item.payload) for item in invocation.generated_inputs] == [
        ('boltz_cp_input.yaml', expected)]
    assert not output.exists()
    invocation.materialize_inputs(output)
    assert (output / 'boltz_cp_input.yaml').read_bytes() == expected


def test_native_csv_writer_exact_bytes_lower_layer(tmp_path):
    """Writer fixture only: batch normalization/producer routing is not proven here."""
    captured = []
    nextflow._write_sequence_batch_name_map(
        output_dir=tmp_path / 'out', entries=[{
            'batch_index': '0', 'name': 'variant_0', 'label': 'a,b',
            'original_name': 'sample', 'sequence': 'ACD',
        }], write_input=lambda path, payload: captured.append((path, payload)),
    )
    assert captured == [(tmp_path / 'out' / 'sequence_batch_manifest.csv',
        b'batch_index,runtime_name,label,original_name,sequence_length,sequence,complex_json\r\n'
        b'0,variant_0,"a,b",sample,3,ACD,\r\n')]
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('drift', ['revision_pin', 'tree_pin', 'during_compilation'])
def test_job_compiler_rejects_source_pin_drift(drift, tmp_path, compiler_environment, monkeypatch):
    job = _job(tmp_path / 'out', {'sequence': 'ACDEFG', 'protenix_use_msa': False}, compiler_environment)
    if drift == 'revision_pin':
        job.execution_source_revision = 'c' * 40
    elif drift == 'tree_pin':
        job.execution_source_tree = 'd' * 40
    else:
        identities = iter([compiler_environment, SourceIdentity('c' * 40, 'd' * 40)])
        monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: next(identities)))
    with pytest.raises(ValueError, match='[Ss]ource identity changed'):
        nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert not Path(job.output_dir).exists()


def test_job_compiler_binds_requested_origin_and_trusted_msa(tmp_path, compiler_environment):
    # Real native producer and explicit supplied bytes, not provider evidence.
    from services.msa_preparation import export_protenix_inputs
    from scripts.prepare_protenix_msa import load_native_protenix_input
    from biomodstack_msa_policy import apply_msa_policy
    params = {'sequence': 'ACDEFG', 'protenix_use_msa': True}
    job = _job(tmp_path / 'out', params, compiler_environment)
    requested = dict(params)
    job.provenance['core_protein_requested_params'] = requested
    initial = nextflow.compile_job_nextflow_invocation(job, params, job.output_dir)
    payload = load_native_protenix_input(initial.native_parameters)
    alignment = tmp_path / 'supplied.a3m'
    alignment.write_text('>query\nACDEFG\n>explicit_fixture\nACDEFG\n')
    for task in payload:
        for wrapper in task['sequences']:
            if 'proteinChain' in wrapper:
                wrapper['proteinChain']['unpairedMsaPath'] = str(alignment)
    system = {'msa_cache_dir', 'msa_local_db', 'protenix_container_path',
              'protenix_model_dir', 'protenix_download_cache_dir'}
    settings = apply_msa_policy('protenix', {key: value for key, value in
        initial.native_parameters.items() if key.startswith(('msa_', 'protenix_', 'colabfold_'))
        and key not in system})
    prepared = Path(job.output_dir) / 'prepared'
    export_protenix_inputs(payload, prepared, settings, {'fixture': 'explicit_supplied_a3m'})
    params.update(protenix_prepared_msa_dir=str(prepared),
                  protenix_prepared_msa_sha256=hashlib.sha256((prepared / 'msa-inputs.json').read_bytes()).hexdigest())
    before = {str(p.relative_to(Path(job.output_dir))): p.read_bytes()
              for p in Path(job.output_dir).rglob('*') if p.is_file()}
    invocation = nextflow.compile_job_nextflow_invocation(job, params, job.output_dir)
    assert invocation.source_identity == compiler_environment
    assert json.loads(invocation.requested_json) == requested
    assert invocation.execution_plan is not None
    assert invocation.execution_plan.requested_json == invocation.requested_json
    assert invocation.execution_plan.effective_json == invocation.effective_json
    assert json.loads(invocation.execution_plan.launch_bindings_json) == {
        key: params[key] for key in ('protenix_prepared_msa_dir', 'protenix_prepared_msa_sha256')}
    for key in ('protenix_prepared_msa_dir', 'protenix_prepared_msa_sha256'):
        assert invocation.native_parameters[key] == params[key]
        assert invocation.command[invocation.command.index('--' + key) + 1] == params[key]
    assert {str(p.relative_to(Path(job.output_dir))): p.read_bytes()
            for p in Path(job.output_dir).rglob('*') if p.is_file()} == before
    assert any(item.logical_id == 'protenix:msa' and item.state == 'prepared'
               for item in invocation.execution_plan.metadata.external_services)


def test_job_compiler_rejects_unqualified_unrequested_msa_transport(tmp_path, compiler_environment):
    params = {'sequence': 'ACDEFG', 'protenix_use_msa': False,
              'protenix_prepared_msa_dir': str(tmp_path / 'outside-output'),
              'protenix_prepared_msa_sha256': 'c' * 64}
    job = _job(tmp_path / 'out', params, compiler_environment)
    with pytest.raises(ValueError, match='outside compiled job output'):
        nextflow.compile_job_nextflow_invocation(job, params, job.output_dir)
    assert params['protenix_use_msa'] is False
    assert not Path(job.output_dir).exists()


def test_job_compiler_snapshots_persisted_request_before_launch_adjustments(tmp_path, compiler_environment):
    requested = {'sequence': 'ACDEFG', 'protenix_use_msa': False, 'cpus_per_gpu': 8}
    job = _job(tmp_path / 'out', requested, compiler_environment)
    invocation = nextflow.compile_job_nextflow_invocation(
        job, {**requested, 'cpus_per_gpu': 4}, job.output_dir)
    assert json.loads(invocation.requested_json) == requested
    assert json.loads(invocation.effective_json)['cpus_per_gpu'] == 4
    assert invocation.native_parameters['cpus_per_gpu'] == 4
    assert job.params == requested
    assert not Path(job.output_dir).exists()


@pytest.mark.asyncio
async def test_launch_helper_materializes_real_compiler_once_and_preserves_identity(tmp_path, compiler_environment, monkeypatch):
    output = tmp_path / 'out'
    params = {'complex_components': [{'type': 'protein', 'id': 'A', 'sequence': 'ACDEFG'}],
              'protenix_use_msa': False}
    job = _job(output, params, compiler_environment, mode='complex')
    materialized = []
    original = NativeInvocation.materialize_inputs

    def materialize(invocation, root):
        materialized.append(invocation)
        return original(invocation, root)

    monkeypatch.setattr(NativeInvocation, 'materialize_inputs', materialize)
    # Real non-Boltz native adapter path; no fake compiler or launch authority.
    invocation = await nextflow._compile_launch_nextflow_invocation(AsyncMock(), job, params, str(output))
    assert materialized == [invocation]
    assert invocation.source_identity == compiler_environment
    assert (output / 'complex_definition.json').read_bytes() == invocation.generated_inputs[0].payload
    compiled_parameters, collected = {}, []
    command = nextflow.build_job_nextflow_command(job, params, str(output),
        compiled_parameters=compiled_parameters, materialize_inputs=False, native_invocations=collected)
    assert command == list(invocation.command)
    assert compiled_parameters == invocation.native_parameters
    assert collected == [invocation]
    assert materialized == [invocation]


@pytest.mark.asyncio
async def test_executor_forwards_real_invocation_to_bundle(tmp_path, compiler_environment, monkeypatch):
    job = _job(tmp_path / 'out', {'sequence': 'ACDEFG', 'protenix_use_msa': False}, compiler_environment)
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    job.execution_target_id = 'vast:123'
    job.status, job.queue_status, job.remote_state = 'queued', 'preparing', 'preparing'
    job.remote_attempt_id = job.nextflow_run_id = None
    target = SimpleNamespace(id='vast:123', host='203.0.113.10', port=22,
                             username='root', remote_root='/opt/biomodstack')
    monkeypatch.setattr(executor, 'get_ready_target', AsyncMock(return_value=target))
    monkeypatch.setattr(executor, '_verify_launch_runner', AsyncMock())
    monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path)
    captured = {}

    class HandoffReached(BaseException):
        pass

    def intercept_bundle(**kwargs):
        captured.update(kwargs)
        raise HandoffReached()

    monkeypatch.setattr(executor, 'prepare_remote_bundle', intercept_bundle)
    with pytest.raises(HandoffReached):
        await executor._launch_remote_job_owned(AsyncMock(), job,
            command=list(invocation.command), native_invocation=invocation)
    assert captured['native_invocation'] is invocation
    assert captured['command'] == list(invocation.command)
    assert captured['job'] is job


@pytest.mark.parametrize('mismatch', ['command', 'model', 'mode'])
def test_remote_projection_rejects_mismatched_real_invocation(mismatch, tmp_path, compiler_environment):
    invocation = nextflow.compile_nextflow_invocation('protenix', 'predict', {
        'sequence': 'ACDEFG', 'protenix_use_msa': False,
    }, str(tmp_path / 'out'))
    command, model, mode = list(invocation.command), 'protenix', 'predict'
    if mismatch == 'command':
        command.extend(['--protenix_n_sample', '999'])
    elif mismatch == 'model':
        model = 'boltz2'
    else:
        mode = 'complex'
    with pytest.raises(RemoteBundleError, match='does not match'):
        compile_remote_dependencies(model, mode, command, native_invocation=invocation)


def test_conformational_command_without_plan_retains_selected_science(tmp_path, compiler_environment, monkeypatch):
    import paths
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp_path)
    (tmp_path / 'request.json').write_text(json.dumps({'backend': 'protenix_v2_ensemble'}))
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map', {
        'cm_request_path': str(tmp_path / 'request.json'), 'gpu_id': 0,
        'run_frustrampnn': True,
    }, str(tmp_path / 'out'), job_id='native-job')
    assert invocation.entrypoint == 'workflows/conformational_mapping.nf'
    with pytest.raises(RemoteBundleError, match='complete selected native execution plan; no local fallback'):
        compile_remote_dependencies('conformational_mapping', 'map', list(invocation.command),
                                    native_invocation=invocation)
    assert invocation.native_parameters['run_frustrampnn'] is True
    assert not (tmp_path / 'out').exists()


def test_real_batch_compiler_captures_ordered_json_and_csv(tmp_path, compiler_environment):
    output = tmp_path / 'batch-output'
    params = {'sequence': 'ACD', 'protenix_use_msa': False, 'sequence_batch_prefix': 'batch',
              'sequence_batch_entries': [{'name': 'one', 'sequence': 'a c d'},
                                         {'name': 'two', 'sequence': 'GG'}]}
    invocation = nextflow.compile_nextflow_invocation('protenix', 'predict', params, str(output))
    entries = [dict(name='batch_001_one', sequence='ACD', label='one', original_name='one', batch_index='1'),
               dict(name='batch_002_two', sequence='GG', label='two', original_name='two', batch_index='2')]
    expected = {
        'sequence_batch_manifest.json': json.dumps(entries, indent=2).encode('utf-8'),
        'sequence_batch_manifest.csv': (
            b'batch_index,runtime_name,label,original_name,sequence_length,sequence,complex_json\r\n'
            b'1,batch_001_one,one,one,3,ACD,\r\n2,batch_002_two,two,two,2,GG,\r\n'),
    }
    assert {item.relative_path: item.payload for item in invocation.generated_inputs} == expected
    assert invocation.native_parameters['sequence_batch_json_path'] == str(output / 'sequence_batch_manifest.json')
    assert json.loads(invocation.requested_json) == params
    assert not output.exists()
    invocation.materialize_inputs(output)
    assert {name: (output / name).read_bytes() for name in expected} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['predict', 'complex'])
async def test_real_boltz_authority_transport_is_sealed_without_science_mutation(
        mode, tmp_path, compiler_environment, monkeypatch):
    from services import boltz_launch_authority as native
    from services.frustrampnn.settings import FrustraMPNNRequestedSettings
    params = {'sequence': 'ACDEFG', 'sequence_name': 'native-input', 'boltz_use_msa': False}
    if mode == 'predict':
        # The native prediction workflow selects this stage by default. Supply
        # its real typed settings rather than silently disabling it in a test.
        params['frustrampnn_settings'] = FrustraMPNNRequestedSettings().model_dump(mode='json')
    if mode == 'complex':
        params['complex_components'] = [{'type': 'protein', 'id': 'A', 'sequence': 'ACDEFG'}]
    output = tmp_path / 'boltz-output'
    job = _job(output, params, compiler_environment, model_id='boltz2', mode=mode)
    job.retry_count = 0
    job.provenance['core_protein_scientific_contract'] = 1
    session = AsyncMock()
    invocation = await nextflow._compile_launch_nextflow_invocation(session, job, params, str(output))
    authority = job.provenance[native.KEY]
    assert authority['job_id'] == job.id and authority['attempt'] == 0
    assert authority['tasks']
    transport_path = Path(invocation.native_parameters['boltz_launch_authority_path'])
    payload = transport_path.read_bytes()
    assert json.loads(payload) == authority
    assert invocation.native_parameters['boltz_launch_authority_sha256'] == hashlib.sha256(payload).hexdigest()
    assert invocation.native_parameters['protein_science_contract_revision'] == 1
    assert json.loads(invocation.requested_json) == params
    assert invocation.native_parameters['boltz_use_msa'] is False
    from services.core_protein_scientific_contract import workflow_params
    assert invocation.execution_plan is not None
    assert invocation.execution_plan.requested_json == invocation.requested_json
    assert json.loads(invocation.execution_plan.launch_bindings_json)['boltz_launch_authority_sha256'] == hashlib.sha256(payload).hexdigest()
    before_transport = nextflow.compile_job_nextflow_invocation(job, params, str(output))
    assert before_transport.execution_plan is not None
    assert invocation.execution_plan.plan_sha256 == before_transport.execution_plan.plan_sha256
    scientific = nextflow.compile_nextflow_invocation('boltz2', mode,
        workflow_params(job, params), str(output), job_id=job.id,
        requested_params=job.provenance.get('core_protein_requested_params'))
    assert invocation.command[:len(scientific.command)] == scientific.command
    assert {key: value for key, value in invocation.native_parameters.items()
            if key not in {*native.TRANSPORT, 'protein_science_contract_revision'}} == scientific.native_parameters
    for key in native.TRANSPORT:
        assert invocation.command[invocation.command.index('--' + key) + 1] == invocation.native_parameters[key]
    monkeypatch.setattr(bundle_module, 'resolve_image', lambda name, root, params: root / name)
    assert invocation.execution_plan.complete, invocation.execution_plan.metadata.blockers
    _, placement = compile_remote_dependencies('boltz2', mode, list(invocation.command),
                                               native_invocation=invocation)
    assert placement['boltz_use_msa'] is False
    assert placement['boltz_launch_authority_path'] == str(transport_path)
    assert placement['boltz_launch_authority_sha256'] == hashlib.sha256(payload).hexdigest()
    session.commit.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed, message', [
    ({'sequence': 'CCCCCC'}, 'Boltz launch input differs'),
    ({'boltz_use_msa': True}, 'Boltz compiled settings differ'),
])
async def test_launch_handoff_retains_real_boltz_native_rejections(
        changed, message, tmp_path, compiler_environment):
    params = {'sequence': 'ACDEFG', 'sequence_name': 'native-input', 'boltz_use_msa': False}
    job = _job(tmp_path / 'boltz-output', params, compiler_environment, model_id='boltz2')
    job.retry_count = 0
    job.provenance['core_protein_scientific_contract'] = 1
    session = AsyncMock()
    with pytest.raises(ValueError, match=message):
        await nextflow._compile_launch_nextflow_invocation(session, job, {**params, **changed}, job.output_dir)
    session.commit.assert_not_awaited()
    assert 'boltz_launch_authority' not in job.provenance


def test_launch_callers_structurally_share_helper_and_forward_command():
    """Supplement to executed helper/executor tests, not standalone proof."""
    tree = ast.parse(inspect.getsource(nextflow.launch_nextflow_job))
    helper_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == '_compile_launch_nextflow_invocation']
    assert len(helper_calls) == 2
    retry_loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)
                   and any(call in list(ast.walk(node)) for call in helper_calls)]
    assert retry_loops
    remote_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'launch_remote_job']
    assert len(remote_calls) == 1
    keywords = {kw.arg: ast.unparse(kw.value) for kw in remote_calls[0].keywords}
    assert keywords['native_invocation'] == 'remote_invocation'
    assert keywords['command'] == 'remote_command'
    assignments = {ast.unparse(node.targets[0]): ast.unparse(node.value)
                   for node in ast.walk(tree) if isinstance(node, ast.Assign) and len(node.targets) == 1}
    assert assignments['remote_command'] == 'list(remote_invocation.command)'
    cmd_assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'cmd' for t in node.targets)]
    initial = next(node for node in cmd_assignments if ast.unparse(node.value) == 'list(invocation.command)')
    wrapped = next(node for node in cmd_assignments if isinstance(node.value, ast.Call)
                   and ast.unparse(node.value.func) == '_component_launch_command')
    assert isinstance(wrapped.value, ast.Call)
    assert [ast.unparse(arg) for arg in wrapped.value.args] == ['invocation', 'job', 'cmd', 'env']
    assert {kw.arg: ast.unparse(kw.value) for kw in wrapped.value.keywords} == {
        'attempt': 'attempt', 'output_dir': 'output_dir'}
    launches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and ast.unparse(node.func) in {'build_systemd_run_command', 'asyncio.create_subprocess_exec'}]
    assert len(launches) == 2
    for launch in launches:
        assert initial.lineno < wrapped.lineno < launch.lineno
        assert (any(ast.unparse(arg) == '*cmd' for arg in launch.args) or
                any(kw.arg == 'command' and ast.unparse(kw.value) == 'cmd' for kw in launch.keywords))


@pytest.mark.parametrize('model,mode,entrypoint,params,expected', [
    ('protein_cad_experimental', 'design', 'protein_cad_experimental', {'pcad_backend': 'disco'}, {'PrepProteinCadRequest', 'RunDISCO', 'FinalizeProteinCadOutputs'}),
    ('protein_cad_experimental', 'design', 'protein_cad_experimental', {'pcad_backend': 'laproteina'}, {'PrepProteinCadRequest', 'RunLaProteina', 'FinalizeProteinCadOutputs'}),
    ('confornets_experimental', 'design', 'confornets_experimental', {'cn_skip_msa': True}, {'PrepConforNetsRequest', 'RunConforNets', 'FinalizeConforNetsOutputs'}),
    ('boltz_cp_experimental', 'design', 'boltz_cp_experimental', {'run_frustrampnn': False}, {'RunBoltzCPExperimental', 'FinalizeBoltzCPExperimental'}),
    ('oligo_design', 'oligo_design', 'oligo_design', {'oligo_validate_boltz': False}, {'RFDPolyDesign', 'NAMPNNDesign', 'PyRosettaRebuild'}),
    ('antibody_child', 'validation_batch', 'antibody_child', {'structure_validator': 'boltz2'}, {'BatchBoltzValidation', 'AlignBoltzValidation'}),
    ('protein_local_redesign', 'local_redesign', 'protein_local_redesign', {'rfd3_request_path': '/logical/request.json'}, {'PrepareProteinLocalNativeRFD3Input', 'RunRFD3', 'BuildProteinLocalRFD3ResultManifest'}),
    ('nanopore', 'fastq_qc', 'ngs/ont_fastq_qc', {'fastq_path': '/logical/reads.fastq', 'reference_fasta': '/logical/ref.fasta'}, {'FastqAlign', 'FastqDimerAnalysis', 'BuildDimerCanonicalOutputs', 'FastqPlasmidQC', 'ConstructVerify'}),
])
def test_selected_native_family_graphs_are_typed_not_placeholders(model, mode, entrypoint, params, expected):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata(model, mode, params, f'workflows/{entrypoint}.nf')
    nodes = (*metadata.static_components, *metadata.dynamic_templates)
    assert expected <= {row.component_key for row in nodes}
    assert 'native_workflow' not in {row.component_key for row in nodes}
    dependencies = {row.logical_id for row in metadata.dependencies}
    roles = {row.role_id for row in metadata.artifact_roles}
    keys = {row.component_key for row in nodes}
    for row in nodes:
        assert row.resources_json and row.lifecycle_json
        assert row.input_role_ids and row.output_role_ids
        assert set(row.dependency_ids) <= dependencies
        assert set(row.input_role_ids + row.output_role_ids) <= roles
        assert set(row.depends_on) <= keys
    assert metadata.descriptors_reviewed


@pytest.mark.parametrize('engine,process,image,cpus', [
    ('gromacs', 'MD_GROMACS_REPLICA', 'gromacs-md-2025.3.sif', 8),
    ('openmm', 'MD_OPENMM_REPLICA', 'openmm-md-8.5.2.sif', 4),
])
def test_selected_md_replicas_require_analysis_and_preserve_engine(engine, process, image, cpus):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('molecular_dynamics', 'simulate',
        {'md_config': {'engine': engine, 'replicas': 3}},
        'workflows/experimental/molecular_dynamics/orchestrator.nf')
    dynamic = {row.component_key: row for row in metadata.dynamic_templates}
    assert {process, 'MD_ANALYZE_REPLICA'} <= dynamic.keys()
    assert dynamic[process].expansion_json is not None
    assert dynamic[process].resources_json is not None
    assert dynamic['MD_ANALYZE_REPLICA'].lifecycle_json is not None
    assert json.loads(dynamic[process].expansion_json)['count'] == 3
    assert json.loads(dynamic[process].resources_json)['cpus']['value'] == cpus
    assert dynamic['MD_ANALYZE_REPLICA'].requiredness == 'required'
    assert json.loads(dynamic['MD_ANALYZE_REPLICA'].lifecycle_json)['max_retries'] == 2
    images = {row.relative_path for row in metadata.dependencies if row.kind == 'image'}
    assert {image, 'md-preparation-v1.sif', 'md-analysis-1.0.0.sif'} <= images
    assert not any(row.field == 'computational_closure' for row in metadata.blockers)
    assert any(row.kind == 'support_tool' and row.relative_path == 'scripts/lib/component_adapter.py'
               for row in metadata.dependencies)
    assert not any(row.field == 'computational_closure' for row in metadata.blockers_for('provision'))


def test_selected_cm_requires_native_request_snapshot_not_path_guessing():
    from model_registry import selected_execution_metadata
    missing = selected_execution_metadata('conformational_mapping', 'map',
        {'cm_request_path': '/must/not/be/read'}, 'workflows/conformational_mapping.nf')
    assert not missing.dependency_closure_complete
    assert any('cm_request snapshot' in row.reason for row in missing.blockers)
    imported = selected_execution_metadata('conformational_mapping', 'map',
        {'cm_request': {'backend': 'external_import', 'targets': [], 'ordered_seeds': [1]}},
        'workflows/conformational_mapping.nf')
    assert any(row.component_key == 'CanonicalConformationalImport' for row in imported.static_components)
    assert not any(row.relative_path == 'protenix.sif' for row in imported.dependencies)
    assert any(row.component_key == 'CanonicalConformationalAnalysisPlaneV2' for row in imported.static_components)
    assert imported.dynamic_templates


def test_complete_metadata_cannot_be_a_blank_reviewed_component():
    from dataclasses import replace
    from component_runtime import NativeComponent, canonical_bytes
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('protein_cad_experimental', 'design',
        {'pcad_backend': 'disco'}, 'workflows/protein_cad_experimental.nf')
    invalid = replace(metadata, static_components=(NativeComponent('blank', 'native', canonical_bytes({})),),
                      dynamic_templates=(), blockers=(), closure_reviewed=True, descriptors_reviewed=True)
    assert not invalid.complete


@pytest.mark.parametrize('model,mode', [
    ('boltzgen', 'design'), ('caliby_experimental', 'design'),
    ('protein_hunter_experimental', 'design'),
    ('protein_modification_experimental', 'iterative_binder_design'),
])
def test_compiler_rejected_routes_are_not_descriptor_acceptance(model, mode, tmp_path, compiler_environment):
    with pytest.raises(ValueError):
        nextflow.compile_nextflow_invocation(model, mode, {}, str(tmp_path / 'out'))
    assert not (tmp_path / 'out').exists()


def test_selected_descriptor_roster_matches_actual_compiler_dispatch():
    """Routing/descriptors only: not request admission or native execution."""
    from model_registry import selected_execution_metadata
    routes = [(model, mode, path) for (model, mode), path in
              nextflow.MODEL_MODE_WORKFLOW_ENTRYPOINTS.items()]
    routes += [(profile, 'descriptor-roster', path) for profile, path in
               nextflow.WORKFLOW_ENTRYPOINTS.items()]
    routes.append(('protein_modification_experimental', 'de_novo_design',
                   nextflow.DEFAULT_WORKFLOW_ENTRYPOINT))
    for model, mode, path in routes:
        params = {'md_config': {'engine': 'gromacs', 'replicas': 2},
                  'cm_request': {'backend': 'external_import'},
                  'pred_method': 'rf3' if model == 'rf3' else 'protenix',
                  'protenix_use_msa': False, 'run_frustrampnn': False,
                  'shape_request': {'sequence_policy': 'skip'},
                  'bam_path': '/logical/reads.bam', 'reference_fasta': '/logical/reference.fasta'}
        metadata = selected_execution_metadata(model, mode, params, path)
        assert metadata.descriptors_reviewed, (model, mode, path)
        if path == 'workflows/protein_sequence_design.nf' and mode == 'descriptor-roster':
            # A compiler-profile alias is not a public model/mode selection.
            assert not metadata.complete
            assert any(row.component_or_dependency_id == 'protein_sequence_design'
                       and row.field == 'dependency_closure' for row in metadata.blockers)
        elif model == 'rf3':
            # Compiler routing does not establish a native RF3 sequence branch.
            assert not metadata.dependency_closure_complete
            assert any(row.field == 'dependency_closure' for row in metadata.blockers)
        else:
            assert metadata.static_components or metadata.dynamic_templates, (model, mode, path)
        if metadata.availability in {'disabled', 'unknown', 'unavailable'}:
            assert metadata.blockers_for('provision')
    internal = selected_execution_metadata('fampnn_child', 'sequence_design', {},
                                           'workflows/fampnn_child.nf')
    # Existing YAML declares this child public; absent-YAML aliases are internal.
    from model_registry import get_registry
    declared = get_registry().get_internal_model_definition('fampnn_child')
    assert internal.availability == ('public' if declared and declared.public_launch else 'internal')
    assert internal.dependency_closure_complete
    assert not internal.blockers_for('provision')


@pytest.mark.parametrize('predictor,expected', [
    ('boltz', {'BoltzFromSequenceTask'}), ('protenix', {'ProtenixPredict'}),
    ('boltz_protenix', {'BoltzFromSequenceTask', 'ProtenixPredict'}),
    ('esmfold2', {'ESMFold2Predict'}),
])
def test_protein_design_sequence_branch_reuses_native_prediction(predictor, expected):
    from model_registry import selected_execution_metadata
    params = {'sequence_input': 'ACDEFG', 'pred_method': predictor,
              'boltz_use_msa': False, 'protenix_use_msa': False,
              'run_frustrampnn': False}
    before = dict(params)
    metadata = selected_execution_metadata('rfd3', 'design', params,
                                           'workflows/protein_design.nf')
    assert metadata.dependency_closure_complete
    keys = {row.component_key for row in metadata.static_components}
    assert expected <= keys
    assert not keys & {'RunRFD3', 'RunRF3', 'PrepMPNN', 'RunFAMPNN', 'RunAF2'}
    assert {'BindProteinDesignTerminalMetadata', 'PublishResults'} <= keys
    assert params == before


def test_rf3_sequence_is_unsupported_but_pdb_checkpoint_member_is_known():
    from model_registry import selected_execution_metadata, native_checkpoint_dependencies
    metadata = selected_execution_metadata('rf3', 'predict',
        {'sequence_input': 'ACDEFG', 'pred_method': 'rf3', 'run_frustrampnn': False},
        'workflows/protein_design.nf')
    assert not metadata.dependency_closure_complete
    assert not any(row.component_key == 'RunRF3' for row in metadata.static_components)
    deps, blockers = native_checkpoint_dependencies('RunRF3', {})
    assert deps[0].relative_path == 'foundry/checkpoints/rf3_foundry_01_24_latest_remapped.ckpt'
    assert not blockers
    root = Path(__file__).resolve().parents[3]
    assert '/' + deps[0].relative_path in (root / 'scripts/run_rf3.py').read_text()
    overridden, blockers = native_checkpoint_dependencies('RunRF3', {'rf3_extra_config': 'ckpt_path=other'})
    assert overridden[0].relative_path is None and blockers


@pytest.mark.parametrize('process', ['FilterRF3', 'FilterRFD3'])
def test_foundry_filters_do_not_select_inference_checkpoints(process):
    from native_components import _NativeAnnotations
    components, dynamic, dependencies, roles, services, blockers = [], [], {}, [], [], []
    a = _NativeAnnotations({}, 'workflows/protein_design.nf', components,
        dynamic, dependencies, roles, services, lambda *args, **kwargs: blockers.append(args))
    a.stage(process)
    assert 'image:foundry.sif' in dependencies
    assert not any(row.kind == 'weights' for row in dependencies.values())
    assert not blockers


@pytest.mark.parametrize('process', ['RunRFD3', 'RunBoltzGen', 'RunDiffDock', 'RunShapeRFD3'])
def test_native_checkpoint_requirements_never_select_whole_cache(process):
    from model_registry import native_checkpoint_dependencies
    deps, blockers = native_checkpoint_dependencies(process, {'cache_root': '/host/private/cache'})
    assert deps
    assert bool(blockers) == (process in {'RunBoltzGen', 'RunDiffDock'})
    assert all(row.selector != 'cache_root' for row in deps)
    assert all('provision' in row.blocks for row in blockers)
    if process == 'RunShapeRFD3':
        root = Path(__file__).resolve().parents[3]
        assert '/' + deps[0].relative_path in (root / 'scripts/shape_blueprint/run_shape_rfd3.py').read_text()
    else:
        assert all(row.relative_path is not None for row in deps)
    assert not any(row.relative_path.endswith('.npy') for row in deps)


@pytest.mark.parametrize('mode', ['diverse', 'adherence'])
def test_boltzgen_checkpoint_selectors_are_native_not_acquisition_approval(mode):
    from model_registry import native_checkpoint_dependencies
    deps, blockers = native_checkpoint_dependencies('RunBoltzGen', {'boltzgen_checkpoint_mode': mode})
    assert deps[0].semantic_release == f'huggingface:boltzgen/boltzgen-1:boltzgen1_{mode}.ckpt'
    assert deps[0].relative_path == f'boltzgen/boltzgen1_{mode}.ckpt'
    assert blockers  # Native auto protocol still needs the generated YAML producer.


@pytest.mark.parametrize('protocol', ['protein-anything', 'protein-small_molecule', 'peptide-anything', 'nanobody-anything', 'antibody-anything'])
@pytest.mark.parametrize('mode', ['both', 'diverse', 'adherence'])
@pytest.mark.parametrize('skip_inverse', [False, True])
def test_boltzgen_exact_selected_pipeline_members(protocol, mode, skip_inverse):
    from model_registry import native_checkpoint_dependencies
    deps, blockers = native_checkpoint_dependencies('RunBoltzGen', {
        'boltzgen_protocol': protocol, 'boltzgen_checkpoint_mode': mode,
        'boltzgen_skip_inverse_folding': skip_inverse})
    expected = {'boltzgen/boltz2_conf_final.ckpt', 'boltzgen/mols.zip'}
    expected.update(f'boltzgen/boltzgen1_{v}.ckpt' for v in
                    (('diverse', 'adherence') if mode == 'both' else (mode,)))
    if not skip_inverse:
        expected.add('boltzgen/boltzgen1_ifold.ckpt')
    if protocol == 'protein-small_molecule':
        expected.add('boltzgen/boltz2_aff.ckpt')
    assert {d.relative_path for d in deps} == expected
    assert not blockers


def test_installed_checkpoint_consumer_bindings_and_generated_cache_policy():
    from model_registry import native_checkpoint_dependencies
    root = Path(__file__).resolve().parents[3]
    config = (root / 'nextflow.config').read_text()
    assert '${params.weights_root}/foundry/checkpoints:/foundry/checkpoints:ro' in config
    assert '--env FOUNDRY_CHECKPOINT_DIRS=/foundry/checkpoints' in config
    assert "task.process.tokenize(':').last() in ['RunRF3', 'RunRFD3']" in config
    assert '${params.weights_root}/boltzgen:/weights/boltzgen:ro' in config
    module = (root / 'modules/boltzgen.nf').read_text()
    for flag, member in [('inverse_fold_checkpoint', 'boltzgen1_ifold.ckpt'),
                         ('folding_checkpoint', 'boltz2_conf_final.ckpt'),
                         ('affinity_checkpoint', 'boltz2_aff.ckpt'), ('moldir', 'mols.zip')]:
        assert f'--{flag} /weights/boltzgen/{member}' in module
    assert '--design_checkpoints ${checkpointPaths}' in module
    assert '--checkpoint_mode ${checkpointMode}' not in module
    assert '${params.weights_root}/diffdock/workdir:/cache/workdir:ro' in config
    assert '${params.weights_root}/diffdock/torch/hub/checkpoints:/cache/torch/hub/checkpoints:ro' in config
    assert '--env TORCH_HOME=/cache/torch' in config and '--pwd /cache' in config
    deps, blockers = native_checkpoint_dependencies('RunDiffDock', {})
    assert len(deps) == 6
    assert sum(d.relative_path.endswith('model_parameters.yml') for d in deps) == 2
    assert any(d.relative_path.endswith('-contact-regression.pt') for d in deps)
    assert all(not d.relative_path.endswith('.npy') for d in deps)
    assert blockers[0].component_or_dependency_id == 'weights:diffdock:workdir/v1.1/score_model/model_parameters.yml'
    deps, blockers = native_checkpoint_dependencies('RunRFD3', {})
    assert deps[0].relative_path == 'foundry/checkpoints/rfd3_latest.ckpt'
    assert '2025_12_01_remapped.ckpt' in deps[0].semantic_release
    assert not blockers


@pytest.mark.parametrize('esm_name,extra', [(None, 0), ('precomputed', 0),
    ('esm2_t33_650M_UR50D', 0), ('esm2_t30_150M_UR50D', 2), ('esm1v_t33_650M_UR90S_1', 1)])
def test_diffdock_present_metadata_resolves_and_pins_auxiliary_assets(tmp_path, esm_name, extra):
    import hashlib
    import yaml
    from model_registry import native_checkpoint_dependencies
    for family in ['score_model', 'confidence_model']:
        path = tmp_path / 'diffdock/workdir/v1.1' / family / 'model_parameters.yml'
        path.parent.mkdir(parents=True)
        # Synthetic loader-field fixture only: no checkpoint or scientific execution.
        path.write_text(yaml.safe_dump({'all_atoms': False, 'esm_embeddings_model': esm_name,
                                      'use_original_model_cache': True}))
    deps, blockers = native_checkpoint_dependencies('RunDiffDock', {'weights_root': str(tmp_path)})
    assert not blockers
    assert len(deps) == 6 + extra
    for dep in deps:
        if dep.relative_path.endswith('model_parameters.yml'):
            assert dep.semantic_release == 'sha256:' + hashlib.sha256((tmp_path / dep.relative_path).read_bytes()).hexdigest()
    selected = next(d for d in deps if d.relative_path.endswith('score_model/model_parameters.yml'))
    path = tmp_path / selected.relative_path
    path.write_text(path.read_text() + '\n# changed immutable metadata\n')
    changed, _ = native_checkpoint_dependencies('RunDiffDock', {'weights_root': str(tmp_path)})
    assert next(d for d in changed if d.logical_id == selected.logical_id).semantic_release != selected.semantic_release


@pytest.mark.parametrize('bad', ['missing', 'symlink', 'not_mapping', 'local_esm'])
def test_diffdock_metadata_missing_or_unbound_is_concrete(tmp_path, bad):
    from model_registry import native_checkpoint_dependencies
    root = tmp_path / 'diffdock/workdir/v1.1'
    for family in ['score_model', 'confidence_model']:
        path = root / family / 'model_parameters.yml'
        path.parent.mkdir(parents=True)
        path.write_text('all_atoms: false\n')
    path = root / 'score_model/model_parameters.yml'
    if bad == 'missing':
        path.unlink()
    elif bad == 'symlink':
        path.unlink()
        path.symlink_to(root / 'confidence_model/model_parameters.yml')
    elif bad == 'not_mapping':
        path.write_text('[]\n')
    else:
        path.write_text('esm_embeddings_model: /private/other.pt\n')
    _, blockers = native_checkpoint_dependencies('RunDiffDock', {'weights_root': str(tmp_path)})
    assert len(blockers) == 1
    assert blockers[0].component_or_dependency_id.endswith('score_model/model_parameters.yml')
    assert ('Missing native metadata asset' in blockers[0].reason) == (bad == 'missing')


def test_native_stage_projects_predecessor_roles_once():
    from component_runtime import NativeArtifactRole
    from native_components import _NativeAnnotations

    class CountedRoles(list):
        scans = 0

        def __iter__(self):
            self.scans += 1
            return super().__iter__()

    roles = CountedRoles([
        NativeArtifactRole('parent:first', 'parent', 'output', 'native', 'test'),
        NativeArtifactRole('parent:input', 'parent', 'input', 'native', 'test'),
        NativeArtifactRole('other:output', 'other', 'output', 'native', 'test'),
        NativeArtifactRole('parent:second', 'parent', 'output', 'native', 'test'),
    ])
    components, dynamic, dependencies, services, blockers = [], [], {}, [], []
    annotations = _NativeAnnotations({}, 'workflows/protein_design.nf', components,
        dynamic, dependencies, roles, services, lambda *a, **kw: blockers.append(a))
    annotations.stage('AlignAF2', after=('parent',))
    assert roles.scans == 1
    emitted = roles[4:]
    assert [role.source_role_ids for role in emitted if role.direction == 'input'] == [
        ('parent:first', 'parent:second'), ('parent:first', 'parent:second')]
    assert all(role.source_role_ids == () for role in emitted if role.direction == 'output')
    assert not blockers


def test_native_process_annotations_retain_current_output_requiredness():
    from native_components import PROCESS_CONTRACTS
    root = Path(__file__).resolve().parents[3]
    for authority, (_, inputs, outputs, _, _) in PROCESS_CONTRACTS.items():
        source, process = authority.rsplit(':', 1)
        text = (root / source).read_text()
        assert f'process {process} ' in text, authority
        # Whitespace is immaterial; optional flags and native emit names are not.
        compact = ' '.join(text.split())
        for declaration in (*inputs, *outputs):
            assert ' '.join(declaration.split()) in compact, (authority, declaration)

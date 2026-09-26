"""Shared compiler connections; native computation is not executed here."""
import json
from copy import deepcopy

import pytest

from model_registry import ModelRegistry
from services.nextflow import compile_nextflow_invocation


@pytest.mark.parametrize('model,mode,entrypoint,profile', [
    ('binder_refinement', 'refine', 'workflows/binder_refinement.nf', 'maturation_child'),
    ('caliby_binder', 'design', 'workflows/caliby_binder.nf', 'protein_sequence_design'),
])
def test_selected_defaults_reach_native_route_without_unselected_assets(tmp_path, model, mode, entrypoint, profile):
    registry = ModelRegistry()
    definition = registry.get_internal_model_definition(model)
    params = {field.name: deepcopy(field.default) for field in definition.params if field.default is not None}
    params.update(pdb_paths=str(tmp_path / 'selected.pdb'), binder_chains='A', target_chains='B')
    assert registry.get_model(model) is not None
    assert registry.validate_job_params(model, mode, params) == []
    assert registry.validate_job_params(model, mode, params, native_entrypoint=entrypoint) == []
    invocation = compile_nextflow_invocation(model, mode, params, str(tmp_path / 'output'))
    assert invocation.entrypoint == entrypoint
    assert f'{profile},workstation_ryzen7960x' in invocation.command
    for name in ('rfd_models', 'af2_models', 'boltz_models', 'alphafold_params', 'msa_local_db'):
        assert f'--{name}' not in invocation.command
    for field in definition.params:
        if field.default is not None and field.default != '':
            assert invocation.native_parameters[field.name] == field.default


@pytest.mark.parametrize('loops,manual', [
    ({}, []),
    ({'H3': ['A:10', 'A:11']}, [{'chain_id': 'A', 'start': 10, 'end': 11, 'name': 'H3'}]),
])
def test_selected_nested_masks_are_json_not_dropped_or_comma_joined(tmp_path, loops, manual):
    params = {'pdb_paths': str(tmp_path / 'selected.pdb'), 'binder_chains': 'A', 'target_chains': 'B',
              'cdr_positions_by_loop': loops, 'manual_cdr_definitions': manual,
              'maturation_flow_enabled': True, 'maturation_repack_enabled': False,
              'maturation_anchors_enabled': False, 'maturation_redesign_enabled': False}
    invocation = compile_nextflow_invocation('binder_refinement', 'refine', params,
                                             str(tmp_path / 'output'))
    for key in ('cdr_positions_by_loop', 'manual_cdr_definitions'):
        index = invocation.command.index('--' + key)
        assert json.loads(invocation.command[index + 1]) == params[key]
        assert invocation.native_parameters[key] == params[key]
    assert invocation.native_parameters['maturation_flow_enabled'] is True
    assert invocation.native_parameters['maturation_redesign_enabled'] is False


def test_selected_remote_descriptors_match_final_refinement_processes():
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('binder_refinement', 'refine',
        {'maturation_redesign_enabled': True}, 'workflows/binder_refinement.nf')
    stages = {row.component_key for row in metadata.static_components}
    assert stages == {'PrepareBinderRefinementRegions', 'PrepareBinderRedesign',
                      'RunMaturationFAMPNN', 'PublishBinderRefinement'}


def test_omitted_selected_stage_flags_do_not_inherit_active_legacy_defaults(tmp_path):
    params = {'pdb_paths': str(tmp_path / 'selected.pdb'), 'binder_chains': 'A', 'target_chains': 'B'}
    invocation = compile_nextflow_invocation('binder_refinement', 'refine', params, str(tmp_path / 'output'))
    for key in ('maturation_repack_enabled', 'maturation_anchors_enabled',
                'maturation_flow_enabled', 'maturation_redesign_enabled'):
        assert invocation.native_parameters[key] is False
        assert invocation.command[invocation.command.index('--' + key) + 1] == 'false'
    assert 'maturation_redesign_enabled' not in params


@pytest.mark.parametrize('mode', ['resume', 'rank', 'filter', 'campaign_output', 'archive', 'unarchive', 'score'])
def test_bc2_lifecycle_compiles_its_owned_snapshot_without_campaign_preview(tmp_path, monkeypatch, mode):
    from services import bindcraft2_launch as launch
    from test_bindcraft2_lifecycle import compiler, setup
    setup(tmp_path, monkeypatch)
    options = {'structure_relative_path': 'coordinate.pdb'} if mode == 'score' else {}
    output = tmp_path / 'child'
    materialized = launch.materialize_native_action(tmp_path / 'parent', output,
        operation=mode, options=options, source_job_id='parent', compiler=compiler)
    params = {**materialized, 'bc2_source_job_id': 'parent', 'bc2_action_options': options}
    invocation = compile_nextflow_invocation('bindcraft2', mode, params, str(output))
    assert invocation.entrypoint == 'workflows/bindcraft2.nf'
    assert 'bindcraft2,workstation_ryzen7960x' in invocation.command
    assert invocation.native_parameters['bc2_compilation'] == materialized['bc2_compilation']
    assert '--bc2_preview_digest' not in invocation.command
    assert '--bc2_action_options' not in invocation.command
    assert launch.read_campaign_receipt(output)['native_action'] == {
        'operation': mode, 'options': options, 'source_job_id': 'parent'}


@pytest.mark.parametrize('filter_params,filtered', [({}, False),
    ({'caliby_max_potts_energy': 0}, True),
    ({'caliby_min_sc_plddt': 0}, True),
    ({'caliby_max_sc_rmsd': 0}, True),
    ({'enable_caliby_filter': False, 'caliby_max_sc_rmsd': 0}, False)])
def test_parent_caliby_descriptor_tracks_native_filter_predicate(tmp_path, filter_params, filtered):
    params = dict(seq_design_caliby=True, seq_design_fampnn=False, seq_design_antifold=False,
        seq_design_proteinmpnn=False, run_structure_validation=False,
        run_immunogenicity_scoring=False, target_pdb=str(tmp_path / 'target.pdb'), **filter_params)
    invocation = compile_nextflow_invocation('template_antibody_denovo', 'default', params,
                                             str(tmp_path / 'output'))
    assert invocation.execution_plan is not None
    metadata = invocation.execution_plan.metadata
    components = {row.component_key: row for row in metadata.static_components}
    assert 'RunCaliby' in components
    assert ('FilterCaliby' in components) is filtered
    if filtered:
        assert components['FilterCaliby'].depends_on == ('RunCaliby',)
    assert not any('caliby_experimental' in row.needed_authority for row in metadata.blockers)
    assets = [row for row in metadata.dependencies if 'caliby' in (row.relative_path or '')]
    assert {row.relative_path for row in assets} >= {
        'caliby.sif', 'caliby/model_params/caliby/soluble_caliby_v1.ckpt'}
    assert len({row.logical_id for row in assets}) == len(assets)


@pytest.mark.parametrize('resume', [{'interactive_gate_continue': True}, {'resume_job_id': 'parent'}])
@pytest.mark.parametrize('source_key', ['selected_input_dir', 'rfantibody_input_pdbs', 'fampnn_collected_pdbs'])
def test_parent_caliby_precollected_does_not_resample_or_refilter(resume, source_key):
    from model_registry import selected_execution_metadata
    params = dict(seq_design_caliby=True, seq_design_fampnn=False, seq_design_antifold=False,
        seq_design_proteinmpnn=False, run_structure_validation=False, run_immunogenicity_scoring=False,
        selected_input_artifact_class='sequence_designed_complex', selected_input_stage_family='caliby',
        caliby_max_potts_energy=0, caliby_run_self_consistency_eval=True, **resume,
        **{source_key: '/fixture/selected'})
    metadata = selected_execution_metadata('template_antibody_denovo', 'default', params,
                                            'workflows/antibody_denovo.nf')
    assert not any('Caliby' in row.component_key for row in metadata.static_components)
    assert not any('caliby' in (row.relative_path or '') for row in metadata.dependencies)


@pytest.mark.parametrize('self_consistency', [False, True])
@pytest.mark.parametrize('checkpoint', ['caliby', 'soluble_caliby', 'soluble_caliby_v1'])
def test_parent_and_selected_caliby_share_exact_checkpoint_dependencies(checkpoint, self_consistency):
    from model_registry import native_checkpoint_dependencies
    params = {'caliby_model_name': checkpoint, 'caliby_run_self_consistency_eval': self_consistency}
    parent, blockers = native_checkpoint_dependencies('RunCaliby', params)
    selected, selected_blockers = native_checkpoint_dependencies('RunCalibyBinder', params)
    assert parent == selected
    assert not blockers and not selected_blockers
    assert {row.relative_path for row in parent} == {
        f'caliby/model_params/caliby/{checkpoint}.ckpt',
        *(['caliby/model_params/af2'] if self_consistency else [])}



def test_embedded_caliby_settings_reuse_canonical_fields_without_changing_parent_defaults():
    import yaml
    registry = ModelRegistry()
    parent = registry.get_internal_model_definition('antibody_denovo')
    native = registry.get_internal_model_definition('caliby_binder')
    assert parent is not None and native is not None
    raw = yaml.safe_load((registry.config_dir / 'antibody_denovo.yaml').read_text())
    original = {field['name']: field for field in raw['params']}
    composed = {field.name: field for field in parent.params}
    for field in native.params:
        if field.name.startswith('caliby_') and field.name not in {
                'caliby_design_positions', 'caliby_num_seqs_per_pdb'}:
            assert field.name in composed
            if field.name not in original:
                assert composed[field.name] == field
                assert composed[field.name] is not field
    for name, field in original.items():
        assert composed[name].default == field.get('default')
    assert 'caliby_num_seqs_per_pdb' not in composed
    assert 'caliby_design_positions' not in composed



@pytest.mark.parametrize('process', ['RunCaliby', 'RunCalibyBinder', 'FilterCaliby'])
def test_caliby_descriptor_io_matches_leaf(process):
    from pathlib import Path
    from native_components import PROCESS_CONTRACTS
    module = Path(__file__).resolve().parents[3] / 'modules/caliby.nf'
    body = module.read_text().split('process ' + process + ' {', 1)[1]
    inputs = body.split('input:', 1)[1].split('output:', 1)[0]
    outputs = body.split('output:', 1)[1].split('script:', 1)[0]
    contract = PROCESS_CONTRACTS['modules/caliby.nf:' + process]
    assert tuple(line.strip() for line in inputs.splitlines() if line.strip()) == contract[1]
    assert tuple(line.strip() for line in outputs.splitlines() if line.strip()) == contract[2]

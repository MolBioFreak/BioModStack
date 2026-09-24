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

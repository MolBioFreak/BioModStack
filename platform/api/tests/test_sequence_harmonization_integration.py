"""Cross-owner request transport; no native model inference or live services."""
import json

import pytest

from routers.jobs import normalize_job_request
from schemas import JobCreate
from services.nextflow import compile_nextflow_invocation
from test_generic_sequence_launch_closeout import ROWS, request


@pytest.mark.parametrize('model,mode', ROWS)
def test_selected_source_id_list_survives_normalization_clone_and_compilation(model, mode, tmp_path):
    submitted = request(model, mode, tmp_path)
    selected_ids = ['first-source', 'second-source']
    submitted.params.update(iteration_source_design_ids=selected_ids,
                            source_selection_count=len(selected_ids),
                            iteration_source_job_id='source-job',
                            lineage_root_job_id='root-job')
    normalized = normalize_job_request(submitted)
    replay = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert replay.model_dump() == normalized.model_dump()
    invocation = compile_nextflow_invocation(model, mode, replay.params, str(tmp_path / 'output'))
    settings = json.loads(next(item.payload for item in invocation.generated_inputs
                               if item.relative_path == '.sequence-design-settings.json'))
    assert settings['iteration_source_design_ids'] == selected_ids
    assert invocation.native_parameters['iteration_source_design_ids'] == selected_ids
    assert '--iteration_source_design_ids' not in invocation.command
    assert invocation.native_parameters['lineage_root_job_id'] == 'root-job'
    assert submitted.params['iteration_source_design_ids'] == selected_ids


@pytest.mark.parametrize('mode', ['ensemble_design', 'sidechain_pack'])
def test_standalone_caliby_real_normalizer_and_preview_preserve_native_request(mode, tmp_path):
    source = tmp_path / 'source with spaces.cif'
    source.write_text('data_transport_fixture\n')
    state = {'state_id': 'native-state', 'path': str(source)}
    params = ({'ensembles': [{'ensemble_id': 'conditioned-group', 'states': [state]}],
               'omit_aas': [], 'verbose': False, 'gaussian_n_conformers': 0}
              if mode == 'ensemble_design' else {'structures': [state]})
    params.update(num_workers=0, scn_step_scale=0.0)
    typed = JobCreate(name='Caliby transport fixture', model_id='caliby_experimental', mode=mode, params=params)
    normalized = normalize_job_request(typed)
    replay = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert replay.model_dump() == normalized.model_dump()
    invocation = compile_nextflow_invocation(replay.model_id, mode, replay.params,
                                             str(tmp_path / 'not-created'), _preview_only=True)
    assert invocation.entrypoint == 'workflows/caliby_native.nf'
    for key, value in normalized.params.items():
        assert invocation.native_parameters[key] == value
    assert 'caliby_request_dir' not in invocation.native_parameters
    assert not (tmp_path / 'not-created').exists()
    assert not {'af2_models', 'rfd_models', 'boltz_models', 'msa_local_db', 'msa_cache_dir'} & invocation.native_parameters.keys()


@pytest.mark.parametrize('model,expected', [
    ('caliby_experimental', {('image', 'caliby.sif'),
        ('weights', 'caliby/model_params/caliby/soluble_caliby_v1.ckpt'),
        ('weights', 'caliby/model_params/caliby/caliby_packer_010.ckpt')}),
    ('ligandmpnn', {('image', 'foundry.sif')}),
])
def test_independent_native_model_assets_need_no_dummy_scientific_input(model, expected):
    from model_registry import model_runtime_dependencies
    actual = {(item.kind, item.relative_path) for item in model_runtime_dependencies(model)}
    assert actual == expected


@pytest.mark.parametrize('mode,key,value', [
    ('ensemble_design', 'model_name', 'caliby'),
    ('sidechain_pack', 'packer_model_name', 'caliby_packer_030'),
])
def test_caliby_configured_assets_select_only_requested_checkpoint(mode, key, value):
    from services.caliby_native import selected_assets
    assert selected_assets(mode, {key: value})['checkpoint'] == f'caliby/{value}.ckpt'
    with pytest.raises(ValueError):
        selected_assets(mode, {key: 'not-a-native-checkpoint'})


def test_historical_caliby_design_is_not_reinterpreted(tmp_path):
    from fastapi import HTTPException
    request = JobCreate(name='historical', model_id='caliby_experimental', mode='design',
                        params={'task': 'sidechain_pack'})
    with pytest.raises(HTTPException) as error:
        normalize_job_request(request)
    assert error.value.status_code == 410
    with pytest.raises(ValueError, match='remains retired'):
        compile_nextflow_invocation(request.model_id, request.mode, request.params, str(tmp_path / 'output'))

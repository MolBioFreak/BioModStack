"""Current owner seams; private specimens, no scientific inference."""
from copy import deepcopy

from scripts.lib.protein_cad_settings import bind_new_cad_settings, replay_cad_settings, settings_projection


def test_cad_projection_presence_precedence_and_replay_boundary():
    params = {'backend': 'disco', 'design_task': 'dna_conditioned', 'disco_effort': 'fast',
              'disco_seeds': '0,19', 'disco_use_deepspeed_evo_attention': False,
              'laproteina_seed': 0, 'laproteina_self_cond': False, 'laproteina_bb_ca_gt_clamp_val': None,
              'laproteina_settings': {'num_steps': 17}, 'disco_settings': {'N_step': 0, 'output_format': None}}
    before = deepcopy(params)
    projection = settings_projection(params)
    assert projection['pcad_laproteina_settings_json'] == {'seed': 0, 'self_cond': False, 'bb_ca_gt_clamp_val': None, 'num_steps': 17}
    assert projection['pcad_disco_settings_json']['seeds'] == [0, 19]
    assert projection['pcad_disco_settings_json']['use_deepspeed_evo_attention'] is False
    assert projection['pcad_disco_settings_json']['N_step'] == 0
    assert projection['pcad_disco_settings_json']['output_format'] is None
    fresh = bind_new_cad_settings('protein_cad_experimental', 'generate', params)
    assert replay_cad_settings('protein_cad_experimental', 'generate', fresh, fresh) == fresh
    old = {k: v for k, v in params.items() if k != 'disco_settings'}
    replay = replay_cad_settings('protein_cad_experimental', 'generate', old, old)
    assert replay['pcad_disco_settings_json']['effort'] == 'max'
    assert replay_cad_settings('protein_cad_experimental', 'generate', old, old, overrides={'disco_effort': 'fast'})['pcad_disco_settings_json']['effort'] == 'fast'
    assert replay['pcad_disco_settings_json']['seeds'] == [0, 19]
    assert params == before
    for task in ['custom_json', 'unconditional']:
        source = {**old, 'design_task': task}
        assert replay_cad_settings('protein_cad_experimental', 'generate', source, source)['pcad_disco_settings_json']['effort'] == 'fast'
    empty = bind_new_cad_settings('protein_cad_experimental', 'generate', {'backend': 'disco', 'design_task': 'dna_conditioned'})
    assert empty['pcad_disco_settings_json'] == {}
    assert replay_cad_settings('protein_cad_experimental', 'generate', empty, empty) == empty

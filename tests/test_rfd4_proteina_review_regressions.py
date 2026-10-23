"""Pure transport regressions from the three retained native review findings."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.rfd4_proteina_contract import normalize_request, native_overlay, selected_dependencies
from bms_rfd4.run import oracle_environment


def request():
    return {'input': {'kind': 'unconditional', 'name': 'sample',
                      'chains': [{'chain_id': 'A', 'min_length': 100, 'max_length': 100}]}}


@pytest.mark.parametrize('backbone,latent', [(0, 0.5), (0.5, 0), (0, 0), (None, None)])
def test_zero_modality_start_roundtrips(backbone, latent):
    r = request()
    r['settings'] = {'generation': {'args': {'partial_t_backbone': backbone, 'partial_t_latent': latent}}}
    assert native_overlay(normalize_request(r)) == r['settings']


@pytest.mark.parametrize('key,value', [('partial_t_backbone', 1), ('partial_t_latent', 1),
                                      ('partial_trajectory_fraction_steps', 0)])
def test_native_upper_and_fraction_bounds_remain(key, value):
    r = request()
    r['settings'] = {'generation': {'args': {key: value}}}
    with pytest.raises(ValueError):
        normalize_request(r)


@pytest.mark.parametrize('profile,family', [('of3', None), ('of3', 'of3'), ('af3', 'of3'), ('af3', None)])
def test_actual_active_oracle_profile_trusted_bindings(profile, family):
    r = request()
    r['providers'] = {'refolding_oracles': {'profile': profile}}
    r['settings'] = {'metrics_tags': ['ppi_refolding']}
    if family is not None:
        r['oracle_parameter_family'] = family
    selected = family or profile
    binding = {f'AF3_{key}': f'/selected/{selected}/{key}' for key in ['IMAGE', 'MODEL_DIR', 'JAX_CACHE_DIR']}
    assert selected_dependencies(r)['active_oracle_profiles'] == [profile]
    assert selected_dependencies(r)['oracle_parameter_family'] == selected
    assert oracle_environment(r, {selected: binding}) == binding
    r['settings']['metrics_tags'] = []
    assert oracle_environment(r, {selected: binding}) == {}


def test_guidance_terms_only_preserve_group_metrics_inheritance():
    r = request()
    r['groups'] = {'guidance': 'contract_ca'}
    r['guidance'] = {'terms': {'contract_ca.sq_norm': {'bb_ca': {'kind': 'constant', 'w': -0.2}}}}
    overlay = native_overlay(r)
    assert 'metrics' not in overlay['guidance']
    assert overlay['guidance']['terms']['contract_ca.sq_norm']['bb_ca'] == {
        '_target_': 'rfproteina.guidance.schedules.constant', '_partial_': True, 'w': -0.2}
    r['guidance']['metrics'] = []
    assert native_overlay(r)['guidance']['metrics'] == {}

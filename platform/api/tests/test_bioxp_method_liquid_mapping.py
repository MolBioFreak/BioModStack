"""Class air-gap speed -> native Air phases; unmappable aliases keep explicit errors."""
from copy import deepcopy
import pytest
from bioxp_method_planning import liquid_resolution

CONTEXT = {'generation': 'original ADP', 'tip_profile_id': 'tecan-liha-T200', 'filter_type': 'unfiltered',
           'mode': 'single-dispense', 'recipe_context': 'ctx', 'target_volume_ul': 20}


def entry(eid, settings, kind='authored'):
    return {'id': eid, 'revision': 1, 'context': deepcopy(CONTEXT), 'settings': settings, 'provenance_kind': kind}


def run(recipe, requested=None, cls=None, water=None):
    inputs = {'recipe': recipe, 'liquid': {'context': deepcopy(CONTEXT), 'requested': requested or {},
                                           'liquid_class': cls, 'water': water}}
    return liquid_resolution(inputs, {}), recipe


def test_explicit_air_gap_speed_emits_to_both_existing_air_phases():
    res, r = run({'leading_air': {'volume_ul': 20}, 'trailing_air': {'volume_ul': 1.25}},
                 requested={'air_gap_aspiration_speed_ul_s': 40})
    assert r['leading_air']['speed_ul_s'] == 40 and r['trailing_air']['speed_ul_s'] == 40
    f = res['fields']['air_gap_aspiration_speed_ul_s']
    assert f['requested'] == {'present': True, 'value': 40}
    assert f['emitted']['native_paths'] == ['/recipe/leading_air/speed_ul_s', '/recipe/trailing_air/speed_ul_s']
    assert f['applied'] == {'status': 'unknown'}


def test_absent_or_null_air_phase_is_never_fabricated():
    res, r = run({'leading_air': {'volume_ul': 20}, 'trailing_air': None},
                 requested={'air_gap_aspiration_speed_ul_s': 40})
    assert r['trailing_air'] is None
    assert res['fields']['air_gap_aspiration_speed_ul_s']['emitted']['native_paths'] == ['/recipe/leading_air/speed_ul_s']
    with pytest.raises(ValueError, match='no air phase is fabricated'):
        run({'trailing_air': None}, requested={'air_gap_aspiration_speed_ul_s': 40})


def test_water_fills_only_unspecified_air_speed_and_never_overwrites_authored():
    water = entry('water', {'air_gap_aspiration_speed_ul_s': 30}, 'manufacturer')
    res, r = run({'leading_air': {'volume_ul': 20}, 'trailing_air': {'volume_ul': 1}}, water=water)
    assert r['leading_air']['speed_ul_s'] == 30 and r['trailing_air']['speed_ul_s'] == 30
    assert [s['field'] for s in res['water_substitutions']] == ['air_gap_aspiration_speed_ul_s']
    # Authored, agreeing phase speeds are the request; Water never replaces them.
    res, r = run({'leading_air': {'volume_ul': 20, 'speed_ul_s': 50}, 'trailing_air': {'volume_ul': 1, 'speed_ul_s': 50}}, water=water)
    assert r['leading_air']['speed_ul_s'] == 50 and not res['water_substitutions']
    assert res['fields']['air_gap_aspiration_speed_ul_s']['requested']['present'] is True
    # Differing authored phase speeds are retained per phase, not unified or overwritten.
    res, r = run({'leading_air': {'volume_ul': 20, 'speed_ul_s': 50}, 'trailing_air': {'volume_ul': 1, 'speed_ul_s': 10}}, water=water)
    assert (r['leading_air']['speed_ul_s'], r['trailing_air']['speed_ul_s']) == (50, 10)
    assert res['fields']['air_gap_aspiration_speed_ul_s']['emitted']['status'] == 'not_emitted'


def test_explicit_air_speed_conflicting_with_authored_phase_errors():
    with pytest.raises(ValueError, match='conflicts with explicitly authored recipe leading_air/speed_ul_s'):
        run({'leading_air': {'volume_ul': 20, 'speed_ul_s': 50}}, requested={'air_gap_aspiration_speed_ul_s': 40})
    res, r = run({'leading_air': {'volume_ul': 20, 'speed_ul_s': '40.0'}}, requested={'air_gap_aspiration_speed_ul_s': 40})
    assert r['leading_air']['speed_ul_s'] == 40


@pytest.mark.parametrize('field,value,reason', [
    ('retract_distance_mm', 5, 'no Z mm-to-steps conversion'),
    ('calibration_function', {'kind': 'affine', 'scale': 1, 'offset_ul': 0}, 'does not call correction_at_target'),
    ('correction_points', [{'target_ul': 20, 'commanded_ul': 22.5}], 'does not call correction_at_target')])
def test_unmappable_explicit_fields_keep_documented_error(field, value, reason):
    recipe = {'leading_air': {'volume_ul': 20, 'speed_ul_s': 50}}
    with pytest.raises(ValueError, match='no native recipe mapping.*' + reason):
        run(recipe, requested={field: value})


@pytest.mark.parametrize('source', ['requested', 'liquid_class', 'water'])
def test_air_speed_compiles_through_real_schema_and_native_document(source):
    from test_bioxp_method_finish import recipe, compile_nodes, node, actions
    from bioxp_method_planning import liquid_selection_schema
    assert 'air_gap_aspiration_speed_ul_s' in liquid_selection_schema()['properties']['requested']['properties']
    raw = recipe()
    del raw['leading_air']['speed_ul_s']
    del raw['trailing_air']['speed_ul_s']
    selection = {'context': deepcopy(CONTEXT), 'requested': {}}
    if source == 'requested':
        selection['requested']['air_gap_aspiration_speed_ul_s'] = '40.00'
    else:
        selection[source] = entry(source, {'air_gap_aspiration_speed_ul_s': '40.00'},
                                  'manufacturer' if source == 'water' else 'authored')
    inputs = {'recipe': raw, 'liquid': selection}
    original = deepcopy(inputs)
    result = compile_nodes(node('liquid_recipe', inputs))
    native = actions(result)[0]['params']['recipe']
    assert native['leading_air']['speed_ul_s'] == native['trailing_air']['speed_ul_s'] == 40
    field = result['resolved']['liquids'][0]['fields']['air_gap_aspiration_speed_ul_s']
    assert field['emitted']['status'] == 'emitted' and field['applied']['status'] == 'unknown'
    assert inputs == original


def test_inherited_unmappable_field_is_advisory_not_emitted():
    res, r = run({'leading_air': {'volume_ul': 20, 'speed_ul_s': 50}},
                 cls=entry('cls', {'retract_distance_mm': 5}))
    assert 'retract_distance_mm' not in str({k: v for k, v in r.items() if k != 'liquid_settings'})
    assert res['fields']['retract_distance_mm']['emitted'] == {'status': 'not_emitted'}
    assert any(i['code'] == 'liquid_field_not_mapped' for i in res['issues'])

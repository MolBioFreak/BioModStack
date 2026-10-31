"""Transfer liquid detection: first draw per source well, mm -> robot steps, legacy transfers unchanged."""
import pytest

from test_bioxp_methods_api import store  # noqa: F401

from bioxp_method_planning import positioned_transfer_recipes


def _inputs(**extra):
    recipe = {'mode': 'single', 'channels': [0, 1], 'before_leading_air': [], 'before_liquid': [],
              'after_liquid': [], 'before_dispense': [], 'after_dispense': [], 'final_empty_before': [],
              'final_empty_speed_ul_s': None, 'multi': None}
    return {'source': {'station': 'reagent', 'location_id': 3, 'wells': ['A1', 'A1', 'B1']},
            'destination': {'station': 'output', 'location_id': 4, 'wells': ['A1', 'A2', 'A3']},
            'channels': [0, 1], 'volume_ul': '5', 'recipe': recipe,
            'source_position_flag': 1, 'destination_position_flag': 1,
            'source_lift_height_steps': None, 'destination_lift_height_steps': None, **extra}


def _lowers(recipe):
    return [m['positioning']['operation'] for m in recipe['before_liquid']]


def test_first_draw_detects_once_per_source_well_across_steps():
    seen = set()
    first = positioned_transfer_recipes(_inputs(liquid_detection={}), seen)
    assert [('liquid_detection' in r) for r in first] == [True, False, True]
    assert _lowers(first[0]) == [] and _lowers(first[1]) == ['lower']
    d = first[0]['liquid_detection']
    assert d['location_id'] == 3 and d['expected'] is True and d['mode'] == 'by_level'
    assert d['submerge_steps'] == 2016 and d['level_tolerance_steps'] == 1008
    # A later step drawing from the same wells relies on the earlier detection.
    later = positioned_transfer_recipes(_inputs(liquid_detection={}), seen)
    assert not any('liquid_detection' in r for r in later)


def test_every_draw_and_options_are_passed_through():
    rows = positioned_transfer_recipes(_inputs(liquid_detection={'when': 'every_draw', 'mode': 'together',
        'expected': False, 'submerge_mm': '2.5', 'follow_mm': 1}))
    assert all('liquid_detection' in r for r in rows)
    d = rows[0]['liquid_detection']
    assert (d['mode'], d['expected'], d['submerge_steps'], d['follow_steps']) == ('together', False, 5039, 2016)


def test_saved_transfers_without_setting_keep_calibrated_lowering():
    rows = positioned_transfer_recipes(_inputs())
    assert not any('liquid_detection' in r for r in rows) and all(_lowers(r) == ['lower'] for r in rows)


def test_bad_settings_are_rejected():
    with pytest.raises(ValueError):
        positioned_transfer_recipes(_inputs(liquid_detection={'submerge_mm': 50}))
    with pytest.raises(ValueError):
        positioned_transfer_recipes(_inputs(liquid_detection={'when': 'sometimes'}))


def test_plan_listed_wells_set_expected_channels_per_pair():
    rows = positioned_transfer_recipes(_inputs(liquid_detection={}, source_liquid_channels=[[0], [0, 1], [1]]))
    assert rows[0]['liquid_detection']['expected_channels'] == [0]
    assert rows[2]['liquid_detection']['expected_channels'] == [1]
    off = positioned_transfer_recipes(_inputs(liquid_detection={'expected': False}, source_liquid_channels=[[0], [0], [1]]))
    assert 'expected_channels' not in off[0]['liquid_detection']


def test_volume_check_and_on_contact_pass_through():
    geometry = {'height_to_volume': [[0, 0], [2.27, 20]], 'calibrated_low_gap_mm': 0.5}
    rows = positioned_transfer_recipes(_inputs(liquid_detection={'mode': 'on_contact', 'mismatch_response': 'fail'},
        source_expected_ul=[{'0': 20.0, '1': 20.0}, None, {'0': 5.0}], source_vessel_geometry=geometry))
    d = rows[0]['liquid_detection']
    assert d['mode'] == 'on_contact'
    assert d['volume_check']['expected_ul'] == {'0': 20.0, '1': 20.0} and d['volume_check']['response'] == 'fail'
    assert d['volume_check']['gap_mm'] == 0.5
    assert 'volume_check' not in rows[2]['liquid_detection'] or rows[2]['liquid_detection']['volume_check']['expected_ul'] == {'0': 5.0}
    with pytest.raises(ValueError):
        positioned_transfer_recipes(_inputs(liquid_detection={'mismatch_response': 'maybe'}))


def test_plate_library_is_served():
    import json
    from pathlib import Path
    lib = json.loads((Path(__file__).resolve().parents[1] / 'schemas/bioxp_plate_library.json').read_text())
    fast = next(p for p in lib['profiles'] if p['id'] == 'thermo-4483485-enduraplate-fast')
    assert fast['vessel_geometry']['well_top_diameter_mm'] == 5.49 and fast['native_addressing']



@pytest.mark.asyncio
async def test_measured_plate_profile_replaces_builtin(store):
    client = store[0]
    BASE = '/api/bioxp/methods'
    profile = {'id': 'thermo-4483485-enduraplate-fast', 'name': 'measured', 'native_addressing': {'row_increment': 2, 'column_increment': 0, 'reference_channel': 0},
               'vessel_geometry': {'height_to_volume': [[0, 0], [3.1, 20]], 'calibrated_low_gap_mm': 0.4}}
    r = await client.post(BASE + '/plate-profiles', json={'method': {'schema': 'bms.bioxp-plate-profile.v1', 'name': 'Fast measured', 'profile': profile}})
    assert r.status_code == 201, r.text
    plates = (await client.get(BASE + '/plates')).json()['profiles']
    fast = next(p for p in plates if p['id'] == 'thermo-4483485-enduraplate-fast')
    assert fast['vessel_geometry']['calibrated_low_gap_mm'] == 0.4 and fast['saved_record_id']

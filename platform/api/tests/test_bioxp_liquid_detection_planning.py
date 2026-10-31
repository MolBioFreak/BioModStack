"""Transfer liquid detection: first draw per source well, mm -> robot steps, legacy transfers unchanged."""
import pytest

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

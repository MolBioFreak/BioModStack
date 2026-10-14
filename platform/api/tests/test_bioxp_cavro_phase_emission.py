"""Actual BMS compilation; native differential consumes exported documents separately."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from bioxp_method_planning import liquid_resolution
from test_bioxp_method_finish import recipe, compile_nodes, node, actions
from test_bioxp_method_liquid_mapping import CONTEXT


@pytest.mark.parametrize('phase', ['leading_air', 'trailing_air'])
@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('field,requested', [('plld_threshold_adc', {'plld_threshold_adc': 20}),
    ('slope_n1', {'slope_n1': 2, 'slope_n2': 3})])
def test_optional_phase_emission(phase, present, field, requested):
    raw = recipe()
    if not present:
        raw[phase] = None
    inputs = {'recipe': raw, 'liquid': {'context': deepcopy(CONTEXT), 'requested': requested,
        'setting_phases': {key: phase for key in requested}}}
    original = deepcopy(inputs)
    result = compile_nodes(node('liquid_recipe', inputs))
    emitted = actions(result)[0]['params']['recipe']
    record = result['resolved']['liquids'][0]['fields'][field]
    assert record['requested']['present'] is True
    assert record['emitted']['status'] == ('emitted' if present else 'not_emitted')
    assert record['applied'] == {'status': 'unknown'}
    assert (emitted[phase] is not None) == present
    assert field in emitted['liquid_settings']['bms_resolution']['fields']
    if not present:
        assert 'native_action_id' not in record['emitted']
        assert any(i['code'] == 'liquid_field_not_emitted' for i in result['issues'])
    assert inputs == original
    export = os.environ.get('BIOXP_CAVRO_PHASE_EXPORT')
    if export:
        path = Path(export)
        path.mkdir(parents=True, exist_ok=True)
        (path / f'{phase}-{present}-{field}.json').write_text(json.dumps({'inputs': original,
            'result': result, 'expected_setting_phase': phase, 'expected_settings_emitted': present}, indent=2))


@pytest.mark.parametrize('phase', ['leading_air', 'trailing_air'])
def test_omitted_air_phase_retains_setting_without_inserting_stroke(phase):
    inputs = {'recipe': {}, 'liquid': {'context': deepcopy(CONTEXT),
        'requested': {'plld_threshold_adc': 20}, 'setting_phases': {'plld_threshold_adc': phase}}}
    result = liquid_resolution(inputs, {})
    assert phase not in inputs['recipe']
    assert inputs['recipe']['phase_settings'][phase]['plld_threshold_adc'] == 20
    assert result['fields']['plld_threshold_adc']['emitted']['status'] == 'not_emitted'

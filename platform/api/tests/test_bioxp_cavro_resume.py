"""Regression receiving for typed nested native models and legacy cloning."""
from copy import deepcopy
import pytest
from bioxp_method_compiler import compile_method
from bioxp_workflow_authoring import _clone_behavior
from test_bioxp_cavro_finish import export
from test_bioxp_methods_api import store, BASE

PROFILE_REQUEST = {'method': {'schema': 'bms.bioxp-method.v1', 'name': 'UI profile', 'steps': [{'type': 'action', 'step_id': 'typed', 'action': 'profile_pickup', 'inputs': {'operation': 'profile_pickup', 'profile': {'profile_id': 'test-T10', 'nominal_volume_ul': '10', 'usable_capacity_ul': '10.5', 'manufacturer': 'test only', 'sku': 'explicit fixture', 'filtered': False}, 'calibration': {'calibration_id': 'test-rack-depth', 'rack_id': 'test-rack', 'positioning': {'operation': 'move', 'location_id': '2', 'well': 'A1', 'position_flag': '1'}, 'pickup': {'operation': 'surface_move', 'intent': 'immersion', 'reference': {'kind': 'authored', 'reference_id': 'fixture', 'position_steps': '40000'}, 'displacement': {'unit': 'steps', 'offset_steps': '100', 'speed_native': '300'}}, 'retract': {'operation': 'surface_move', 'intent': 'immersion', 'reference': {'kind': 'authored', 'reference_id': 'fixture', 'position_steps': '40000'}, 'displacement': {'unit': 'steps', 'offset_steps': '-100', 'speed_native': '300'}}, 'channel_wells': {'0': 'A1', '1': 'B1'}, 'provenance': 'synthetic explicit authored coordinates'}}}]}}

@pytest.mark.asyncio
async def test_typed_profile_pickup_nested_numeric_text(store):
    client, transport, _, _ = store
    request = deepcopy(PROFILE_REQUEST)
    response = await client.post(BASE + '/compile', json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['document'], result['issues']
    params = result['document']['stages'][0]['actions'][0]['params']
    assert params['profile']['nominal_volume_ul'] == 10
    assert params['profile']['usable_capacity_ul'] == 10.5
    assert params['calibration']['pickup']['reference']['position_steps'] == 40000
    assert params['calibration']['retract']['displacement']['offset_steps'] == -100
    assert request == PROFILE_REQUEST
    assert not transport.calls
    export('typed-profile-pickup', {'request': request, 'result': result})


def test_clone_comparison_preserves_nondefault_error_policy():
    legacy = {'stages': [{'actions': [{'action_id': 'old', 'params': {}}]}]}
    explicit = deepcopy(legacy)
    explicit['stages'][0]['actions'][0]['on_error'] = 'stop'
    assert _clone_behavior(legacy) == _clone_behavior(explicit)
    explicit['stages'][0]['actions'][0]['on_error'] = 'pause_for_operator'
    assert _clone_behavior(legacy) != _clone_behavior(explicit)
    assert 'on_error' not in legacy['stages'][0]['actions'][0]

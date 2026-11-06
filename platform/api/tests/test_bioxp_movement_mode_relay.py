"""Fast lane / Retarget reach the robot through the real BMS v2 relay unchanged."""
import copy

import pytest

from test_bioxp_live_deck_contract import body, run

SEQUENCE = 'oem.xy.move_sequence'
RETARGET = 'oem.xy.retarget'
TARGETS = [{'target': 'LOC_RC', 'camera_offset': True}, {'location_id': 1, 'well': 'H12'}, {'x': 3300, 'y': 4400}]


def test_fast_lane_and_retarget_forward_exact_inputs(tmp_path):
    async def scenario(http, service, clients, generation):
        calls = []
        reply = {'command_id': 'native-1', 'status': 'queued'}
        async def request(route, **kwargs):
            calls.append(copy.deepcopy(kwargs))
            return reply
        clients[0].request = request
        for action, inputs in [(SEQUENCE, {'targets': TARGETS}), (RETARGET, {'target': TARGETS[1]})]:
            sent = body(generation, inputs)
            response = await http.post(f'/api/bioxp/operator-controls/v2/actions/{action}', json=sent)
            assert response.status_code == 202, response.text
            assert response.json() == reply
            assert calls[-1]['json_data'] == {k: v for k, v in sent.items() if k != 'expected_connection_generation'}
            assert calls[-1]['path_params'] == {'action_id': action}
    run(tmp_path, scenario)


@pytest.mark.parametrize('action,inputs', [
    (SEQUENCE, {'targets': []}),
    (SEQUENCE, {'targets': [{'location_id': 1, 'well': 'A1'}] * 65}),
    (SEQUENCE, {'targets': [{'location_id': 1, 'well': 'A1', 'position_flag': 1}]}),
    (SEQUENCE, {'targets': [{'target': 'LOC_RC'}]}),
    (RETARGET, {'targets': TARGETS}),
    (RETARGET, {'target': {'x': 1.5, 'y': 2}}),
])
def test_malformed_lane_inputs_never_reach_transport(tmp_path, action, inputs):
    async def scenario(http, service, clients, generation):
        response = await http.post(f'/api/bioxp/operator-controls/v2/actions/{action}', json=body(generation, inputs))
        assert response.status_code == 422, response.text
        assert clients[0].request_calls == []
    run(tmp_path, scenario)

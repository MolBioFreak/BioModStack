"""Actual facade producers, exact occurrence lowering and scratch persistence."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from bioxp_method_compiler import compile_method
from test_bioxp_methods_api import store, BASE, create


def measurement_request():
    return {'method': {'schema': 'bms.bioxp-method.v1', 'steps': [
        {'type': 'action', 'step_id': 'measure', 'action': 'checkpoint', 'inputs': {
            'message': 'Enter external measurement', 'measurement_form': {'fields': [
                {'field_id': 'volume', 'label': 'External volume', 'unit': 'uL', 'module': 'pipette', 'target': '20.000', 'commanded': '22.500', 'repetitions': 1}]}}},
        {'type': 'action', 'step_id': 'analyze', 'action': 'calibration', 'inputs': {
            'operation': 'analyze', 'analysis': {'kind': 'pipette', 'model': 'descriptive', 'context': {'fixture': 'software-only'},
                'samples': [{'point_id': 'p', 'target_ul': '20.000', 'commanded_ul': '22.500',
                    'observation': {'kind': 'volume', 'measured_ul': {'method_result': {'step_path': '/method/steps/0'}, 'output': 'measurements', 'path': ['volume', 0]}}}]}}}]}}


def export(name, value):
    if directory := os.environ.get('BIOXP_CAVRO_FINISH_EXPORT'):
        path = Path(directory) / (name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n')


def test_exact_measurement_reference():
    request = measurement_request()
    result = compile_method(request)
    assert result['document'], result['issues']
    actions = result['document']['stages'][0]['actions']
    assert actions[0]['params']['measurement_form']['fields'][0]['target'] == '20.000'
    ref = actions[1]['params']['analysis']['samples'][0]['observation']['measured_ul']
    assert ref == {k: actions[0][k] for k in ('stage_id', 'action_id', 'source_occurrence_id')} | {'output': 'measurements', 'path': ['volume', 0]}
    assert request == measurement_request()
    export('measurement-compile', {'request': request, 'result': result})


@pytest.mark.asyncio
async def test_facade_exports_and_incomplete_cold_save(store):
    client, transport, _, _ = store
    for name in ('catalog', 'schema', 'examples'):
        response = await client.get(BASE + '/' + name)
        assert response.status_code == 200, response.text
        export(name, response.json())
    request = measurement_request()
    response = await client.post(BASE + '/compile', json=request)
    assert response.status_code == 200 and response.json()['document'], response.text
    export('facade-measurement-compile', {'request': request, 'result': response.json()})
    from bioxp_method_calibration import calibration_examples
    for entry in calibration_examples():
        raw = entry['method']
        assert all('default' not in p for p in raw['parameters'])
        saved = await create(client, raw)
        cold = await client.get(BASE + '/library/' + saved['id'] + '/revisions/1')
        assert cold.status_code == 200 and cold.json()['method'] == raw
        assert compile_method({'method': raw})['document'] is None
        export(entry['id'] + '-cold-open', cold.json())
    assert not transport.calls

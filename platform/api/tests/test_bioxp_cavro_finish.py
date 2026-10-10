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


@pytest.mark.parametrize('entry_index', range(5))
def test_bound_calibration_companions(entry_index):
    from bioxp_method_calibration import calibration_examples, calibration_bound_fixture
    entry = calibration_examples()[entry_index]
    bound = calibration_bound_fixture(entry)
    result = compile_method(bound)
    assert result['document'], result['issues']
    assert entry['bindings'] == {}
    export(entry['id'] + '-bound', {'request': bound, 'result': result})


@pytest.mark.parametrize('scope', ['loop', 'procedure', 'groups'])
def test_repeated_and_duplicate_local_ids_resolve_exactly(scope):
    request = measurement_request()
    children = request['method']['steps']
    if scope == 'loop':
        request['method']['steps'] = [{'type': 'repeat', 'step_id': 'loop', 'count': 2, 'steps': children}]
        path = '/method/steps/0/steps/0'
    elif scope == 'procedure':
        request['method']['procedures'] = [{'id': 'p', 'parameters': [], 'steps': children}]
        request['method']['steps'] = [{'type': 'call', 'step_id': name, 'procedure_id': 'p', 'arguments': {}} for name in ('first', 'second')]
        path = '/method/procedures/0/steps/0'
    else:
        request['method']['steps'] = [{'type': 'group', 'step_id': name, 'steps': deepcopy(children)} for name in ('first', 'second')]
        for index, group in enumerate(request['method']['steps']):
            group['steps'][1]['inputs']['analysis']['samples'][0]['observation']['measured_ul']['method_result']['step_path'] = f'/method/steps/{index}/steps/0'
        path = None
    if path:
        children[1]['inputs']['analysis']['samples'][0]['observation']['measured_ul']['method_result']['step_path'] = path
    result = compile_method(request)
    assert result['document'], result['issues']
    actions = [a for s in result['document']['stages'] for a in s['actions']]
    assert len(actions) == 4
    for i in (0, 2):
        reference = actions[i + 1]['params']['analysis']['samples'][0]['observation']['measured_ul']
        assert all(reference[k] == actions[i][k] for k in ('stage_id', 'action_id', 'source_occurrence_id'))
    assert actions[0]['source_occurrence_id'] != actions[2]['source_occurrence_id']
    export('references-' + scope, {'request': request, 'result': result})


def test_forward_reference_not_latest_and_context_not_interpreted():
    request = measurement_request()
    analysis = request['method']['steps'][1]['inputs']['analysis']
    analysis['context'] = {'method_result': {'step_path': 'opaque'}, 'output': 'documentary'}
    result = compile_method(request)
    assert result['document'], result['issues']
    assert result['document']['stages'][0]['actions'][1]['params']['analysis']['context'] == analysis['context']
    analysis['samples'][0]['observation']['measured_ul']['method_result']['step_path'] = '/method/steps/1'
    assert compile_method(request)['document'] is None


@pytest.mark.parametrize('selected', [0, 1])
def test_explicit_cross_call_occurrence_and_full_native_reference(selected):
    request = measurement_request()
    prompt, analysis = request['method']['steps']
    request['method']['procedures'] = [{'id': 'p', 'parameters': [], 'steps': [prompt]}]
    request['method']['steps'] = [
        {'type': 'call', 'step_id': 'first', 'procedure_id': 'p', 'arguments': {}},
        {'type': 'call', 'step_id': 'second', 'procedure_id': 'p', 'arguments': {}}, analysis]
    operand = analysis['inputs']['analysis']['samples'][0]['observation']
    operand['measured_ul']['method_result'] = {'step_path': '/method/procedures/0/steps/0'}
    assert compile_method(request)['document'] is None  # no guessing across calls
    operand['measured_ul']['method_result']['occurrence_index'] = selected
    result = compile_method(request)
    assert result['document'], result['issues']
    actions = [a for stage in result['document']['stages'] for a in stage['actions']]
    lowered = actions[-1]['params']['analysis']['samples'][0]['observation']['measured_ul']
    assert lowered['source_occurrence_id'] == actions[selected]['source_occurrence_id']
    operand['measured_ul'] = deepcopy(lowered)
    explicit = compile_method(request)
    assert explicit['document'], explicit['issues']
    assert explicit['document']['stages'][-1]['actions'][-1]['params']['analysis']['samples'][0]['observation']['measured_ul'] == lowered
    export('reference-explicit-' + str(selected), {'request': request, 'result': explicit})


def correction_request():
    from test_bioxp_cavro_transfer import request
    from test_bioxp_method_liquid_mapping import CONTEXT
    req = request()
    inputs = req['method']['steps'][0]['inputs']
    inputs['recipe'].pop('commanded_aspiration_ul')
    pin = {'calibration_id': 'software-fixture', 'revision': 'r7', 'context': {'tip': 'T200', 'fixture': 'not measured'},
        'function': {'kind': 'points', 'points': [{'target_ul': '20.000', 'commanded_ul': '22.750'}]}}
    cls = {'schema': 'bms.bioxp-liquid-class.v1', 'id': 'selected', 'revision': 7, 'context': deepcopy(CONTEXT),
        'settings': {'selected_correction': pin}, 'authored_settings': {'selected_correction': pin}, 'provenance_kind': 'authored'}
    inputs['liquid'] = {'context': deepcopy(CONTEXT), 'liquid_class': 'selected', 'requested': {}}
    req['dependencies']['liquid_classes'] = [cls]
    return req


def test_selected_class_correction_preserves_pin_and_no_bms_arithmetic():
    from jsonschema import Draft202012Validator
    from bioxp_method_model import method_catalog
    request = correction_request()
    before = deepcopy(request)
    inputs = request['method']['steps'][0]['inputs']
    schema = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == 'transfer')
    Draft202012Validator(schema).validate(inputs)
    result = compile_method(request)
    assert result['document'], result['issues']
    recipe = result['document']['stages'][0]['actions'][0]['params']['recipe']
    assert 'commanded_aspiration_ul' not in recipe
    assert recipe['selected_correction'] == request['dependencies']['liquid_classes'][0]['settings']['selected_correction']
    assert result['resolved']['liquids'][0]['fields']['selected_correction']['emitted']['status'] == 'emitted'
    assert result['simulation']['accounting']['commanded_displacement']['has_unknown']
    assert request == before
    export('selected-correction', {'request': request, 'result': result})
    inputs['recipe']['selected_correction'] = None
    assert compile_method(request)['document'] is None  # native still requires an explicit displacement source
    inputs['recipe']['commanded_aspiration_ul'] = '21.125'
    disabled = compile_method(request)
    assert disabled['document'], disabled['issues']
    assert disabled['document']['stages'][0]['actions'][0]['params']['recipe']['selected_correction'] is None
    inputs['recipe']['selected_correction'] = before['dependencies']['liquid_classes'][0]['settings']['selected_correction']
    assert compile_method(request)['document'] is None


@pytest.mark.asyncio
async def test_selected_correction_real_library_snapshot_recovery(store):
    from test_bioxp_methods_api import KEY, JOB
    client, transport, _, _ = store
    request = correction_request()
    cls = request['dependencies']['liquid_classes'][0]
    saved_class = await client.post(BASE + '/liquid-classes', json={'method': cls, 'name': 'Explicit correction fixture'})
    assert saved_class.status_code == 201, saved_class.text
    assert (await client.get(BASE + '/liquid-classes/' + saved_class.json()['id'] + '/revisions/1')).json()['method'] == cls
    raw = deepcopy(request['method'])
    raw['editor_state'] = {'run_inputs': {'dependencies': deepcopy(request['dependencies']), 'bindings': deepcopy(request.get('bindings', {})), 'initial_state': None}}
    saved = await create(client, raw)
    url = BASE + '/library/' + saved['id']
    cold = (await client.get(url + '/revisions/1')).json()
    assert cold['method'] == raw
    run = await client.post(url + '/runs', json={'revision': 1, 'idempotency_key': KEY, 'expected_generation': 77,
        'acknowledge_live': True, **cold['method']['editor_state']['run_inputs']})
    assert run.status_code == 202, run.text
    recovery = await client.post(BASE + '/runs/' + JOB + '/recovery-draft', json={})
    assert recovery.status_code == 200, recovery.text
    assert recovery.json()['dependencies'] == request['dependencies']
    sent = next(kwargs['json_data']['document'] for route, kwargs in transport.calls if route == 'protocol_execute')
    assert sent['metadata']['bms_method_run']['dependencies'] == request['dependencies']
    export('correction-store', {'class': saved_class.json(), 'cold': cold, 'run': run.json(), 'recovery': recovery.json()})


def application_cases():
    surface = {'operation': 'surface_move', 'intent': 'authored',
        'reference': {'kind': 'authored', 'reference_id': 'software-only', 'position_steps': 40000},
        'displacement': {'unit': 'steps', 'offset_steps': 100, 'speed_native': 300}}
    search = {'operation': 'plld', 'channels': [0], 'timeout_ms': 100, 'start_mode': 1,
        'detection_id': 'source', 'start_steps': 40000, 'search_target_steps': 60000, 'search_speed_native': 300, 'z_motor_current': 17}
    return {
        'search-surface-tracking': [search, surface, {'operation': 'tracked_aspirate', 'channels': [0], 'timeout_ms': 100,
            'volume_ul': '22.5', 'speed_ul_s': '50', 'target_liquid_ul': '20', 'trajectory': deepcopy(surface), 'z_motor_current': 17, 'z_timeout_ms': 100}],
        'pressure-capture': [{'operation': 'pressure_capture', 'capture': {'channels': [0], 'duration_ms': 20, 'max_frames': 2}}],
        'device-service': [{'operation': 'device_command', 'channels': [0], 'timeout_ms': 100, 'command': {'command': 'report', 'selector': 57}},
            {'operation': 'device_program', 'channels': [0], 'timeout_ms': 100, 'program': {'mode': 'execute', 'commands': [{'command': 'delay', 'milliseconds': 20}]}}],
        'profile-pickup': [{'operation': 'profile_pickup', 'profile': {'profile_id': 'software-only', 'nominal_volume_ul': 10,
            'manufacturer': 'fixture', 'sku': 'fixture', 'filtered': False},
            'calibration': {'calibration_id': 'software-only', 'rack_id': 'rack', 'positioning': {'operation': 'move', 'location_id': 4, 'well': 'A1', 'position_flag': 0},
                'pickup': deepcopy(surface), 'retract': None, 'channel_wells': {'0': 'A1', '1': 'C1', '2': 'E1', '3': 'G1'}, 'provenance': 'inert fixture, not physical calibration'}}]}


@pytest.mark.parametrize('name', sorted(application_cases()))
def test_native_new_families_from_actual_discovery(name):
    from bioxp_method_model import method_catalog
    from jsonschema import Draft202012Validator
    inputs = {'application': {'implementation': 'original-adp.application.v1', 'operations': application_cases()[name]}}
    schema = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == 'cavro_application')
    Draft202012Validator(schema).validate(inputs)
    request = {'method': {'schema': 'bms.bioxp-method.v1', 'steps': [{'step_id': 'a', 'type': 'action', 'action': 'cavro_application', 'inputs': inputs}]}}
    result = compile_method(request)
    assert result['document'], result['issues']
    export('application-' + name, {'request': request, 'result': result})


def test_refresh_original_87_documents():
    import gzip
    original = json.loads(gzip.decompress((Path(__file__).parent / 'fixtures/bioxp_methods/final-model-documents.json.gz').read_bytes()))
    assert len(original) == 87
    documents = []
    for old in original:
        metadata = old['metadata']['bms_method']
        request = {key: metadata[key] for key in ('method', 'bindings', 'dependencies', 'initial_state')}
        result = compile_method(request)
        assert result['document'], result['issues']
        documents.append(result['document'])
    assert len(documents) == len(original)
    export('documents', documents)

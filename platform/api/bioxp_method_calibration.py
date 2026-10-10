"""Native calibration contracts and exact authored-occurrence addressing.

No fit, correction arithmetic, hardware access or independent execution owner.
"""
from copy import deepcopy
import json
from pathlib import Path

CALIBRATION = json.loads((Path(__file__).parent / 'schemas/bioxp_calibration_native.json').read_text())
AUTHOR_REFERENCE = {
    'type': 'object', 'additionalProperties': False, 'required': ['method_result', 'output'],
    'properties': {
        'method_result': {'type': 'object', 'additionalProperties': False,
            'required': ['step_path'], 'properties': {
                'step_path': {'type': 'string', 'description': 'Exact authored JSON pointer, e.g. /method/steps/0. Same call/loop occurrence by default.'},
                'occurrence_index': {'type': 'integer', 'minimum': 0,
                    'description': 'Explicit zero-based expanded occurrence at this path; omit for same call/loop scope.'},
                'generated_index': {'type': 'integer', 'minimum': 0}}},
        'output': {'enum': ['measurements', 'result']},
        'path': {'type': 'array', 'items': {'anyOf': [{'type': 'string'}, {'type': 'integer'}]}}}}


def calibration_examples():
    """Incomplete ordinary starters; all numeric/scientific inputs are bindings."""
    from bioxp_method_examples import ref
    entries = []
    for key, title, steps in (
        ('pipette_volume_calibration', 'Pipette volume measurement', [('prepare', 'checkpoint'), ('dispense', 'liquid_recipe'), ('measure', 'checkpoint'), ('analyze', 'calibration')]),
        ('pipette_height_calibration', 'Pipette height calibration', [('prepare', 'checkpoint'), ('measure', 'checkpoint'), ('calculate', 'calibration'), ('apply', 'calibration'), ('decision', 'calibration')]),
        *[(module + '_calibration', module.upper() + ' temperature measurement', [('setpoint', 'thermal_setpoint' if module == 'tc' else 'chiller_setpoint'), ('settle', 'wait'), ('measure', 'checkpoint'), ('analyze', 'calibration')]) for module in ('rc', 'oc', 'tc')],
    ):
        parameters = [{'id': sid + '_inputs', 'type': 'object', 'label': title + ': ' + sid} for sid, _ in steps]
        method = {'schema': 'bms.bioxp-method.v1', 'name': title,
            'description': 'Unbound calibration procedure. Author the command, settling, measurement form and analysis model. No measured values, tolerances or corrections supplied.',
            'parameters': parameters, 'procedures': [], 'tip_policy': 'manual',
            'steps': [{'step_id': sid, 'type': 'action', 'action': action, 'inputs': ref(sid + '_inputs')} for sid, action in steps]}
        entries.append({'id': key, 'method': method, 'bindings': {}, 'status': 'intentionally_unbound',
            'coverage': [{'step_id': sid, 'stage_owner': 'operator' if action == 'checkpoint' else 'robot', 'physical_qualification': 'not demonstrated'} for sid, action in steps]})
    return entries


def calibration_bound_fixture(entry):
    """Separately selected software companion, never a starter's defaults."""
    fixture = deepcopy(entry)
    fixture['status'] = 'bound_software_fixture_not_wet_lab_recipe'
    fixture['qualification'] = 'Synthetic authored inputs for compile-only qualification; not measured observations or device execution.'
    key = entry['id']
    paths = {s['step_id']: f'/method/steps/{index}' for index, s in enumerate(entry['method']['steps'])}
    def reference(step, output, path):
        return {'method_result': {'step_path': paths[step]}, 'output': output, 'path': path}
    if key == 'pipette_height_calibration':
        bindings = {
            'prepare_inputs': {'message': 'Software-only height fixture; operator positions and measures independently'},
            'measure_inputs': {'message': 'Enter externally observed Z', 'measurement_form': {'fields': [
                {'field_id': 'z', 'label': 'Observed Z', 'unit': 'steps', 'module': 'pipette', 'repetitions': 1}]}},
            'calculate_inputs': {'operation': 'height_calculate', 'scope_id': 'software-height', 'plate': 'TC',
                'measured_z': reference('measure', 'measurements', ['z', 0])},
            'apply_inputs': {'operation': 'height_apply', 'calculation': reference('calculate', 'result', [])},
            'decision_inputs': {'operation': 'height_decision', 'scope_id': 'software-height', 'decision': 'restore'}}
    elif key == 'pipette_volume_calibration':
        recipe = {'mode': 'single', 'channels': [0], 'timeout_ms': 1000,
            'target_liquid_ul': '20', 'commanded_aspiration_ul': '22.5', 'aspiration_speed_ul_s': '50',
            'aspiration_delay_ms': 0, 'leading_air': None, 'trailing_air': None,
            'before_leading_air': [], 'before_liquid': [], 'after_liquid': [], 'before_dispense': [], 'after_dispense': [],
            'dispense_segments': [{'volume_ul': '22.5', 'speed_ul_s': '50'}],
            'final_empty_speed_ul_s': None, 'final_empty_before': [], 'multi': None}
        bindings = {'prepare_inputs': {'message': 'Software-only fixture; author actual positioning and consumables before use'},
            'dispense_inputs': {'recipe': recipe},
            'measure_inputs': {'message': 'Enter external delivered volume', 'measurement_form': {'fields': [
                {'field_id': 'volume', 'label': 'External volume', 'unit': 'uL', 'module': 'pipette', 'target': '20', 'commanded': '22.5', 'repetitions': 1}]}},
            'analyze_inputs': {'operation': 'analyze', 'analysis': {'kind': 'pipette', 'model': 'descriptive', 'context': {'fixture': 'software-only'},
                'samples': [{'point_id': 'fixture', 'target_ul': '20', 'commanded_ul': '22.5',
                    'observation': {'kind': 'volume', 'measured_ul': reference('measure', 'measurements', ['volume', 0])}}]}}}
    else:
        module = key.split('_')[0]
        bindings = {'setpoint_inputs': {'bank': 'nest' if module == 'tc' else module, 'target_temp_c': '25'},
            'settle_inputs': {'seconds': '1'},
            'measure_inputs': {'message': 'Enter independent temperature probe observation', 'measurement_form': {'fields': [
                {'field_id': 'temperature', 'label': 'External probe', 'unit': 'degC', 'module': module, 'target': '25', 'commanded': '25', 'repetitions': 1}]}},
            'analyze_inputs': {'operation': 'analyze', 'analysis': {'kind': 'thermal', 'samples': [{
                'point_id': 'fixture', 'module': module, 'target_c': '25', 'commanded_c': '25',
                'measured_c': reference('measure', 'measurements', ['temperature', 0]), 'context': {'fixture': 'software-only'}}]}}}
    fixture['bindings'] = bindings
    return fixture


def inline_schema(schema, root=None):
    """Embed a closed exported schema without changing its local $ref scope."""
    root = schema if root is None else root
    if isinstance(schema, list):
        return [inline_schema(item, root) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if '$ref' in schema:
        return inline_schema(root['$defs'][schema['$ref'].rsplit('/', 1)[-1]], root)
    return {key: inline_schema(value, root) for key, value in schema.items() if key != '$defs'}


def action_schema():
    schema = deepcopy(CALIBRATION['action_params'])
    schema['$defs']['ResultReference'] = {'anyOf': [deepcopy(CALIBRATION['result_reference']), deepcopy(AUTHOR_REFERENCE)]}
    return schema


def lower_references(params, occurrence, occurrences, stages):
    """Resolve typed operands only, after every native stage/action ID exists."""
    from bioxp_method_native import validate
    actions = [a for stage in stages for a in stage['actions']]
    order = {o['occurrence_id']: i for i, o in enumerate(occurrences)}

    def operand(value):
        if not isinstance(value, dict) or 'method_result' not in value:
            return value  # complete native references remain valid
        validate(value, AUTHOR_REFERENCE)
        address = value['method_result']
        candidates = [o for o in occurrences if o['path'] == address['step_path']]
        if 'occurrence_index' in address:
            index = address['occurrence_index']
            candidates = candidates[index:index + 1]
        else:
            candidates = [o for o in candidates if o['call_path'] == occurrence['call_path'] and o['loop_path'] == occurrence['loop_path']]
        if len(candidates) != 1:
            raise ValueError('Calibration reference must identify one exact authored occurrence; use occurrence_index for another call/loop')
        selected = candidates[0]
        if order[selected['occurrence_id']] >= order[occurrence['occurrence_id']]:
            raise ValueError('Calibration reference must address an earlier occurrence')
        emitted = [a for a in actions if a['source_occurrence_id'] == selected['occurrence_id']]
        if 'generated_index' in address:
            emitted = [a for a in emitted if a['metadata']['generated_index'] == address['generated_index']]
        if len(emitted) != 1:
            raise ValueError('Calibration reference requires an explicit generated_index for a compound step')
        action = emitted[0]
        return {key: action[key] for key in ('stage_id', 'action_id', 'source_occurrence_id')} | {
            'output': value['output'], **({'path': deepcopy(value['path'])} if 'path' in value else {})}

    result = deepcopy(params)
    if result.get('operation') == 'height_calculate':
        result['measured_z'] = operand(result.get('measured_z'))
    elif result.get('operation') == 'height_apply':
        result['calculation'] = operand(result.get('calculation'))
    elif result.get('operation') == 'analyze':
        analysis = result.get('analysis', {})
        for name in ('samples', 'verification_samples'):
            for sample in analysis.get(name, []):
                for key in ('target_ul', 'commanded_ul', 'target_c', 'commanded_c', 'controller_reported_c', 'measured_c'):
                    if key in sample:
                        sample[key] = operand(sample[key])
                observation = sample.get('observation', {})
                for key in ('measured_ul', 'mass_mg', 'density_mg_per_ul'):
                    if key in observation:
                        observation[key] = operand(observation[key])
    return validate(result, CALIBRATION['action_params'])

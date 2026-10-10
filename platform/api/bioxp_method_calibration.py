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

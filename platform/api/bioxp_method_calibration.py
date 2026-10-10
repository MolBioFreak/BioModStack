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
        # Keep structural measurement/result connections in the ordinary starter,
        # not only its bound software companion. Scientific operands stay required.
        by_id = {step['step_id']: step for step in method['steps']}
        def earlier(step, output, path):
            index = next(i for i, node in enumerate(method['steps']) if node['step_id'] == step)
            return {'method_result': {'step_path': f'/method/steps/{index}'}, 'output': output, 'path': path}
        def parameter(identifier, kind, label):
            method['parameters'].append({'id': identifier, 'type': kind, 'label': label})
            return ref(identifier)
        if key == 'pipette_height_calibration':
            method['parameters'] = []
            scope = parameter('scope_id', 'string', 'Authored shared height-calibration scope')
            plate = parameter('plate', 'string', 'Native plate: TC / MS / OC / RC / STRIP')
            decision = parameter('decision', 'string', 'Explicit accept or restore decision')
            by_id['prepare']['inputs'] = {'message': 'Position and measure using the authored height-calibration procedure'}
            by_id['measure']['inputs'] = {'message': 'Enter externally observed Z', 'measurement_form': {'fields': [
                {'field_id': 'z', 'label': 'Observed Z', 'unit': 'steps', 'module': 'pipette'}]}}
            by_id['calculate']['inputs'] = {'operation': 'height_calculate', 'scope_id': scope, 'plate': plate,
                'measured_z': earlier('measure', 'measurements', ['z', 0])}
            by_id['apply']['inputs'] = {'operation': 'height_apply', 'calculation': earlier('calculate', 'result', [])}
            by_id['decision']['inputs'] = {'operation': 'height_decision', 'scope_id': deepcopy(scope), 'decision': decision}
        else:
            method['parameters'] = [p for p in parameters if p['id'] in {'dispense_inputs', 'setpoint_inputs', 'settle_inputs'}]
            volume = key == 'pipette_volume_calibration'
            module, field_id, unit = ('pipette', 'volume', 'uL') if volume else (key.split('_')[0], 'temperature', 'degC')
            target = parameter('target', 'number', 'Authored target (' + unit + ')')
            commanded = parameter('commanded', 'number', 'Authored command (' + unit + ')')
            context = parameter('measurement_context', 'object', 'Authored instrument / tip / liquid / probe / location context')
            by_id['measure']['inputs'] = {'message': 'Enter the independent external observation; extend the authored graph for further points',
                'measurement_form': {'fields': [{'field_id': field_id, 'label': 'External ' + field_id, 'unit': unit,
                    'module': module, 'target': deepcopy(target), 'commanded': deepcopy(commanded), 'context': deepcopy(context)}]}}
            if volume:
                by_id['prepare']['inputs'] = {'message': 'Prepare the explicitly authored consumables, positioning and measurement procedure'}
                analysis = {'kind': 'pipette', 'model': parameter('fit_model', 'string', 'Explicit descriptive / exact_point_offset / affine_inverse_ols model'),
                    'context': deepcopy(context), 'samples': [{'point_id': 'point', 'target_ul': target, 'commanded_ul': commanded,
                        'observation': {'kind': 'volume', 'measured_ul': earlier('measure', 'measurements', [field_id, 0])}}]}
            else:
                analysis = {'kind': 'thermal', 'samples': [{'point_id': 'point', 'module': module, 'target_c': target, 'commanded_c': commanded,
                    'context': deepcopy(context), 'measured_c': earlier('measure', 'measurements', [field_id, 0])}]}
            by_id['analyze']['inputs'] = {'operation': 'analyze', 'analysis': analysis}
        entries.append({'id': key, 'method': method, 'bindings': {}, 'status': 'intentionally_unbound',
            'coverage': [{'step_id': sid, 'stage_owner': 'operator' if action == 'checkpoint' else 'robot', 'physical_qualification': 'not demonstrated'} for sid, action in steps]})
    return entries


def calibration_bound_fixture(entry):
    """Separately selected software companion, never a starter's defaults."""
    fixture = deepcopy(entry)
    fixture['status'] = 'bound_software_fixture_not_wet_lab_recipe'
    fixture['qualification'] = 'Synthetic authored inputs for compile-only qualification; not measured observations or device execution.'
    key = entry['id']
    if key == 'pipette_height_calibration':
        bindings = {'scope_id': 'software-height', 'plate': 'TC', 'decision': 'restore'}
    else:
        bindings = {'target': '20' if key == 'pipette_volume_calibration' else '25',
            'commanded': '22.5' if key == 'pipette_volume_calibration' else '25',
            'measurement_context': {'fixture': 'software-only'}}
        if key == 'pipette_volume_calibration':
            bindings.update(fit_model='descriptive', dispense_inputs={'recipe': {
                'mode': 'single', 'channels': [0], 'timeout_ms': 1000,
                'target_liquid_ul': '20', 'commanded_aspiration_ul': '22.5', 'aspiration_speed_ul_s': '50',
                'aspiration_delay_ms': 0, 'leading_air': None, 'trailing_air': None,
                'before_leading_air': [], 'before_liquid': [], 'after_liquid': [], 'before_dispense': [], 'after_dispense': [],
                'dispense_segments': [{'volume_ul': '22.5', 'speed_ul_s': '50'}],
                'final_empty_speed_ul_s': None, 'final_empty_before': [], 'multi': None}})
        else:
            module = key.split('_')[0]
            bindings.update(setpoint_inputs={'bank': 'nest' if module == 'tc' else module, 'target_temp_c': '25'},
                settle_inputs={'seconds': '1'})
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
        value = validate(value, AUTHOR_REFERENCE)
        address = value['method_result']
        candidates = [o for o in occurrences if o['path'] == address['step_path'] and 'generated_by' not in o]
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

"""Disconnected native mechanism contracts pinned to the exported robot source.

No device import or duplicate recipe expander. Recipe lowering stays native.
"""
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

EXPORT = json.loads((Path(__file__).parent / 'schemas/bioxp_method_native.json').read_text())
# Domains are exported documentary parameters; no wire encoder is copied.
SETTINGS = {'type': 'object', 'additionalProperties': False, 'properties': {
    **{name: {'type': 'integer', 'minimum': spec['minimum'], 'maximum': spec['maximum']}
       for name, spec in EXPORT['capabilities']['pressure_parameters'].items()},
    'slope': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 2, 'maxItems': 2},
    'pressure_streaming': {'type': 'boolean'},
}}
for _schema in (EXPORT['application'], EXPORT['recipe']):
    _schema['$defs']['Settings']['properties']['values'] = deepcopy(SETTINGS)
    for _definition in _schema['$defs'].values():
        if 'channels' in _definition.get('properties', {}):
            _definition['properties']['channels']['uniqueItems'] = True
EXPORT['recipe']['properties']['channels'] = deepcopy(EXPORT['application']['$defs']['Stroke']['properties']['channels'])
EXPORT['recipe']['properties']['phase_settings']['additionalProperties'] = deepcopy(SETTINGS)

N = {'type': 'number'}
NN = {'type': 'number', 'minimum': 0}
I = {'type': 'integer'}
B = {'type': 'boolean'}
S = {'type': 'string'}

def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required, 'additionalProperties': False}

def enum(*values):
    return {'enum': list(values)}

HOLD = obj({'bank': enum('nest', 'lid', 'pedestal'), 'target_temp_c': N,
    'duration_s': NN, 'start': enum('dispatch', 'attainment'), 'tolerance_c': NN, 'timeout_s': NN},
    ['bank', 'target_temp_c', 'duration_s', 'start'])
HOLD['allOf'] = [{'if': {'properties': {'start': {'const': 'attainment'}}},
                  'then': {'required': ['tolerance_c', 'timeout_s']}}]
CONTRACTS = {
    'tip_eject': obj({}),
    'wait': obj({'seconds': NN}),
    'timer_start': obj({'timer_id': {'type': 'string', 'minLength': 1}, 'seconds': NN}),
    'timer_wait': obj({'timer_id': {'type': 'string', 'minLength': 1}}),
    'thermal_setpoint': obj({'bank': enum('nest', 'lid', 'pedestal'), 'target_temp_c': N}),
    'thermal_hold': HOLD,
    'thermal_profile': obj({'segments': {'type': 'array', 'minItems': 1, 'items': HOLD}, 'repeat': {'type': 'integer', 'minimum': 0}}),
    'chiller_setpoint': obj({'bank': enum('rc', 'oc'), 'target_temp_c': N}),
    'snapshot': obj({}),
    'inspect': obj({}),
    'camera_illumination': obj({'channel': enum(1, 2, 3), 'on': B}),
    'barcode_read': obj({'mode': enum('stationary')}),
    'plate_catch': obj({'plate': I, 'run_in_parallel': B}, ['plate']),
    'plate_release': obj({'destination': I, 'press_plate': B, 'run_in_parallel': B}, ['destination']),
    'plate_press': obj({'plate': I, 'run_in_parallel': B}, ['plate']),
    'cut_seal': obj({'count': {'type': 'integer', 'not': {'const': 0}}, 'cut_z_offset_steps': I}),
    'plate_move': obj({'plate_id': S, 'target_location': S, 'move_mode': S}, ['plate_id', 'target_location']),
    'move_cover': obj({'cover_id': S, 'target_location': S, 'move_mode': S}, ['cover_id', 'target_location']),
    'plate_prepare': obj({'plate_ids': {'type': 'array', 'items': enum('PL_POOL', 'PL_OUTPUT', 'PL_REAGENT')}}),
    'thermal_door': obj({'door_command': enum('DO', 'DC')}),
}
ALIASES = {'incubate': 'thermal_hold', 'illumination': 'camera_illumination', 'barcode': 'barcode_read',
           'gripper_catch': 'plate_catch', 'gripper_release': 'plate_release', 'gripper_press': 'plate_press'}


def coerce(value, schema, root=None):
    """Convert numeric draft strings only where the exported field is numeric."""
    root = root or schema
    if '$ref' in schema:
        return coerce(value, root['$defs'][schema['$ref'].split('/')[-1]], root)
    for option in schema.get('anyOf', schema.get('oneOf', [])):
        try:
            result = coerce(value, option, root)
            Draft202012Validator({**option, '$defs': root.get('$defs', {})}).validate(result)
            return result
        except (ValueError, TypeError, ValidationError):
            pass
    if isinstance(value, dict):
        extra = schema.get('additionalProperties', {})
        return {k: coerce(v, schema.get('properties', {}).get(k, extra if isinstance(extra, dict) else {}), root) for k, v in value.items()}
    if isinstance(value, list):
        return [coerce(v, schema.get('items', {}), root) for v in value]
    if isinstance(value, str) and schema.get('type') in ('number', 'integer'):
        from decimal import Decimal
        n = Decimal(value)
        if not n.is_finite():
            raise ValueError('Nonfinite native number')
        if schema['type'] == 'integer':
            if n != n.to_integral_value():
                raise ValueError('Expected exact integer')
            return int(n)
        return float(n)
    return value


def validate(value: Any, schema: dict) -> Any:
    result = coerce(value, schema)
    try:
        Draft202012Validator(schema).validate(result)
    except ValidationError as exc:
        raise ValueError('/' + '/'.join(map(str, exc.path)) + ': ' + exc.message) from exc
    return result


def native_action(operation, inputs):
    if operation in {'fluid_search', 'pipette_settings', 'pressure_stream'}:
        if operation == 'pressure_stream':
            selected = EXPORT['application']['$defs']['Settings']['properties']
            schema = obj({'channels': selected['channels'], 'timeout_ms': selected['timeout_ms'], 'enabled': B})
            selected_inputs = validate(inputs, schema)
            instruction = {'operation': 'settings', 'channels': selected_inputs['channels'],
                'timeout_ms': selected_inputs['timeout_ms'], 'values': {'pressure_streaming': selected_inputs['enabled']}}
        else:
            instruction = {'operation': 'plld' if operation == 'fluid_search' else 'settings', **inputs}
        return native_action('cavro_application', {'application': {'implementation': 'original-adp.application.v1', 'operations': [instruction]}})
    kind = ALIASES.get(operation, operation)
    if kind in CONTRACTS:
        return kind, validate(inputs, CONTRACTS[kind])
    if operation in ('cavro_application', 'liquid_recipe'):
        field = 'application' if operation == 'cavro_application' else 'recipe'
        if set(inputs) != {field}:
            raise ValueError(f'{operation} requires exactly {field}')
        value = validate(inputs[field], EXPORT[field])
        def validate_instructions(part):
            if isinstance(part, list):
                for child in part:
                    validate_instructions(child)
            elif isinstance(part, dict):
                if part.get('operation') == 'plld':
                    if (part.get('after_detection_steps') is None) != (part.get('after_detection_speed_native') is None):
                        raise ValueError('after_detection_steps and after_detection_speed_native must be specified together')
                if part.get('operation') == 'position':
                    from bioxp_workflow_authoring import preview, WorkflowPreviewRequest
                    positioning = part['positioning']
                    if positioning.get('operation') not in {'move', 'lower', 'lift'}:
                        raise ValueError('Cavro position requires a native move/lower/lift instruction')
                    result = preview(WorkflowPreviewRequest.model_validate({'protocol_id': 'position-check', 'draft': {
                        'schema': 'bms.bioxp-workflow-draft.v1', 'editor_state': {},
                        'steps': [{'step_id': 'position', 'intent': positioning}]}}))
                    if result.document is None:
                        raise ValueError('; '.join(i.message for i in result.issues))
                for key, child in part.items():
                    if key != 'liquid_settings':
                        validate_instructions(child)
        validate_instructions(value)
        if field == 'recipe':
            multi = value['multi']
            if (value['mode'] == 'multi') != (multi is not None):
                raise ValueError('multi parameters required exactly for multi mode')
            if multi is not None:
                if value['dispense_segments'] or multi['sample_count'] != len(multi['aliquots']):
                    raise ValueError('Complete multi aliquot list required; no single dispense segments')
                if multi['reaspiration'] is not None and multi['dispense_to_reaspiration_delay_ms'] is None:
                    raise ValueError('Reaspiration requires explicit delay')
        return 'pipette_manual_physical', {'operation': 'cavro_application' if field == 'application' else 'cavro_liquid_recipe', field: value}
    return None

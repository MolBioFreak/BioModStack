"""Raw-preserving method contracts and disconnected discovery. No device imports."""
from copy import deepcopy
import json
from bioxp_workflow_authoring import discovery

SCHEMA = 'bms.bioxp-method.v1'
EXPRESSION_OPS = ('literal', 'param', 'arg', 'loop_index', 'loop_item', 'add', 'sub', 'mul', 'div', 'eq', 'ne', 'lt', 'le', 'gt', 'ge', 'and', 'or', 'not')


def validate_method(raw):
    """Persistence validation only: incomplete/unknown scientific fields survive."""
    if not isinstance(raw, dict) or raw.get('schema') != SCHEMA:
        raise ValueError('expected bms.bioxp-method.v1 object')
    if not isinstance(raw.get('steps'), list):
        raise ValueError('steps must be an array')
    for key in ('parameters', 'procedures'):
        if key in raw and not isinstance(raw[key], list):
            raise ValueError(f'{key} must be an array')
    json.dumps(raw, allow_nan=False)


def migrate_legacy(draft):
    if draft.get('schema') == SCHEMA:
        validate_method(draft)
        return deepcopy(draft)
    if draft.get('schema') not in ('bms.bioxp-workflow-draft.v1', 'bms.bioxp-workflow-draft.v2'):
        raise ValueError('unsupported legacy discriminator')
    result = deepcopy(draft)
    result.update(schema=SCHEMA, parameters=[], procedures=[], legacy_original=deepcopy(draft))
    result['steps'] = []
    for row in draft['steps']:
        node = {**deepcopy(row), 'type': 'action', 'action': 'native_intent', 'inputs': deepcopy(row.get('intent', {}))}
        result['steps'].append(node)
    return result


def method_schema():
    expression = {'type': 'object', 'required': ['version', 'op'], 'properties': {
        'version': {'const': 1}, 'op': {'enum': list(EXPRESSION_OPS)}, 'id': {'type': 'string'},
        'value': {}, 'unit': {'type': 'string'}, 'args': {'type': 'array', 'items': {'$ref': '#/$defs/value'}}}}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': SCHEMA,
        'type': 'object', 'required': ['schema', 'steps'], 'properties': {
            'schema': {'const': SCHEMA}, 'name': {'type': 'string'}, 'description': {'type': 'string'},
            'parameters': {'type': 'array', 'items': {'$ref': '#/$defs/parameter'}},
            'procedures': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'steps'], 'properties': {
                'id': {'type': 'string'}, 'parameters': {'type': 'array', 'items': {'$ref': '#/$defs/parameter'}},
                'steps': {'type': 'array', 'items': {'$ref': '#/$defs/node'}}}}},
            'steps': {'type': 'array', 'items': {'$ref': '#/$defs/node'}}, 'deck_plan': {'type': 'object'},
            'tip_policy': {}, 'editor_state': {}},
        '$defs': {'expression': expression, 'value': {'description': 'Raw JSON or {expr: versioned typed AST}; no executable text'},
            'parameter': {'type': 'object', 'required': ['id', 'type'], 'properties': {
                'id': {'type': 'string'}, 'label': {'type': 'string'},
                'type': {'enum': ['number', 'integer', 'boolean', 'string', 'array', 'object']},
                'unit': {'type': 'string'}, 'default': {}, 'minimum': {}, 'maximum': {}, 'choices': {'type': 'array'}}},
            'node': {'type': 'object', 'required': ['step_id', 'type'], 'properties': {
                'step_id': {'type': 'string'}, 'type': {'enum': ['action', 'group', 'repeat', 'if', 'call']},
                'enabled': {'type': 'boolean'}, 'on_error': {'enum': ['stop', 'pause_for_operator']},
                'label': {'type': 'string'}, 'action': {'type': 'string'}, 'inputs': {'type': 'object'},
                'steps': {'type': 'array', 'items': {'$ref': '#/$defs/node'}},
                'then': {'type': 'array', 'items': {'$ref': '#/$defs/node'}},
                'else': {'type': 'array', 'items': {'$ref': '#/$defs/node'}},
                'count': {}, 'items': {}, 'condition': {}, 'procedure_id': {'type': 'string'}, 'arguments': {'type': 'object'}}}}}


def method_catalog():
    native = discovery()
    schema = native['native_request']
    actions = []
    for name, ref in schema['properties']['steps']['items']['discriminator']['mapping'].items():
        actions.append({'action': name, 'inputs': deepcopy(schema['$defs'][ref.rsplit('/', 1)[-1]]),
            'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
            'native_owner': 'manual_pipetting', 'source_revision': native['native_source_commit']})
    actions.append({'action': 'transfer', 'inputs': native['transfer'], 'status': {'authorable': True, 'emitted': True},
        'effects': 'Ordered move/lower/aspirate/lift/move/lower/dispense/lift per head-reference pair; non-atomic'})
    pending = {
        'park': ['location_id'], 'eject_tip': ['channels'], 'distribute': ['source', 'destinations', 'recipe'],
        'consolidate': ['sources', 'destination', 'recipe'], 'plate_move': ['source', 'destination'],
        'move_cover': ['source', 'destination'], 'plate_prepare': ['location_id'], 'gripper_catch': ['location_id'],
        'gripper_release': ['location_id'], 'gripper_press': ['location_id'], 'thermal_door': ['operation'],
        'thermal_setpoint': ['temperature'], 'thermal_hold': ['temperature', 'duration', 'dwell_start', 'tolerance', 'timeout'],
        'thermal_profile': ['segments', 'repeats', 'dwell_start', 'tolerance', 'timeout'],
        'chiller_setpoint': ['device', 'temperature'], 'wait': ['duration'], 'incubate': ['duration', 'temperature'],
        'checkpoint': ['message'], 'note': ['message'], 'inspect': ['location_id'], 'snapshot': ['camera'],
        'illumination': ['camera', 'enabled'], 'status_light': ['color'], 'barcode': ['reader', 'mode'],
        'fluid_search': ['channels', 'search', 'plld'], 'cut_seal': ['location_id'], 'pierce_seal': ['location_id', 'channels'],
        'seal_separate': ['location_id'], 'plunger': ['channels', 'position', 'speed'], 'pipette_settings': ['channels', 'settings'],
        'pressure_stream': ['channels', 'interval'], 'classifier': ['algorithm', 'conditions'],
    }
    for name, fields in pending.items():
        status = {'authorable': True, 'emitted': name in ('checkpoint', 'note'), 'registered': True if name in ('checkpoint', 'note') else None, 'connected_tested': False, 'physically_qualified': False}
        actions.append({'action': name, 'inputs': {'type': 'object', 'properties': {f: {'type': 'string'} if f == 'message' else {} for f in fields}, 'required': fields},
            'status': status,
            'integration': 'Existing executor host-only review/note' if name in ('checkpoint', 'note') else 'Exact native binding required; not replaced by a note or silent omission'})
    return {'schema': SCHEMA, 'actions': actions, 'native_definitions': schema['$defs'],
        'geometry': native['alignment'], 'locations': native['native_locations'],
        'limits': {'depth': 64, 'occurrences': 10000, 'native_actions': 100000},
        'precision': 'Decimal 28 significant digits, ROUND_HALF_EVEN; normalized base-unit decimal strings; native emitter converts at boundary',
        'qualification': 'Software representation is not physical qualification'}


def method_examples():
    from bioxp_method_examples import examples
    return deepcopy(examples())

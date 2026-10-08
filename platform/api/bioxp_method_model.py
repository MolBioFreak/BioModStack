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
        'value': {}, 'type': {'enum': ['number'], 'description': 'Explicit numeric literal kind; raw spelling persists until compilation'}, 'unit': {'type': 'string'}, 'args': {'type': 'array', 'items': {'$ref': '#/$defs/value'}}}}
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
    actions.append({'action': 'transfer', 'inputs': native['transfer'], 'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
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
        'pressure_stream': ['channels', 'interval'],
    }
    for name, fields in pending.items():
        status = {'authorable': True, 'emitted': name in ('checkpoint', 'note'), 'registered': True if name in ('checkpoint', 'note') else None, 'connected_tested': False, 'physically_qualified': False}
        actions.append({'action': name, 'inputs': {'type': 'object', 'properties': {f: {'type': 'string'} if f == 'message' else {} for f in fields}, 'required': fields},
            'status': status,
            'integration': 'Existing executor host-only review/note' if name in ('checkpoint', 'note') else 'Exact native binding required; not replaced by a note or silent omission'})
    from bioxp_method_native import CONTRACTS, ALIASES, EXPORT, obj, B
    implemented = set(CONTRACTS) | set(ALIASES) | {'cavro_application', 'liquid_recipe', 'fluid_search', 'pipette_settings', 'pressure_stream'}
    actions = [a for a in actions if a['action'] not in implemented]
    for name in sorted(set(CONTRACTS) | set(ALIASES)):
        actions.append({'action': name, 'inputs': deepcopy(CONTRACTS[ALIASES.get(name, name)]),
            'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
            'native_owner': ALIASES.get(name, name), 'source_revision': EXPORT['source_commit']})
    for name, definition in (('fluid_search', 'Plld'), ('pipette_settings', 'Settings'), ('pressure_stream', 'Settings')):
        inputs = deepcopy(EXPORT['application']['$defs'][definition])
        inputs['properties'].pop('operation')
        inputs['required'].remove('operation')
        if name == 'pressure_stream':
            inputs['properties'].pop('values')
            inputs['required'].remove('values')
            inputs['properties']['enabled'] = B
            inputs['required'].append('enabled')
        actions.append({'action': name, 'inputs': inputs,
            'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
            'native_owner': 'cavro_application', 'source_revision': EXPORT['source_commit']})
    from bioxp_method_planning import liquid_selection_schema
    for name, field in (('cavro_application', 'application'), ('liquid_recipe', 'recipe')):
        schema_input = deepcopy(EXPORT[field])
        definitions = schema_input.pop('$defs', {})
        if field == 'recipe':
            from bioxp_method_planning import RECIPE_FIELDS
            inherited_top = {path[0] for path in RECIPE_FIELDS.values() if len(path) == 1}
            schema_input['required'] = [key for key in schema_input['required'] if key not in inherited_top]
            definitions['Air']['required'] = []  # volume and speed may resolve through pinned class/Water
            inherited_multi = {path[1] for path in RECIPE_FIELDS.values() if len(path) == 2 and path[0] == 'multi'}
            definitions['Multi']['required'] = [key for key in definitions['Multi']['required'] if key not in inherited_multi]
            schema_input['description'] = 'Editable recipe: omitted applicable mapped fields may resolve through pinned class/Water. Final native Recipe is validated after resolution.'
        actions.append({'action': name, 'inputs': {'type': 'object', 'required': [field],
            'properties': {field: schema_input, **({'liquid': liquid_selection_schema()} if field == 'recipe' else {}), 'channel_transfers': {'type': 'array', 'items': {'type': 'object'}}}, '$defs': definitions},
            'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
            'native_owner': 'pipette_manual_physical', 'source_revision': EXPORT['source_commit'],
            'registration_evidence': 'Finite Cavro glue is qualified by the native runtime lane, not this pure compiler'})
    actions = [a for a in actions if a['action'] not in {'distribute', 'consolidate', 'eject_tip', 'tip_eject'}]
    transfer_schema = deepcopy(native['transfer'])
    transfer_defs = transfer_schema.pop('$defs', {})
    transfer_defs['TransferEndpoint']['properties']['labware_id'] = {'type': 'string', 'description': 'Stable logical vessel identity; not a native positioning field'}
    transfer_schema.get('properties', {}).pop('operation', None)
    transfer_schema['required'] = [k for k in transfer_schema.get('required', []) if k != 'operation']
    class_transfer = deepcopy(transfer_schema)
    class_transfer['required'] = [k for k in class_transfer['required'] if k not in {'aspirate_speed', 'dispense_speed'}] + ['liquid', 'recipe']
    class_transfer['properties'].pop('dispense_speed', None)
    recipe_editor = deepcopy(next(a['inputs'] for a in actions if a['action'] == 'liquid_recipe'))
    class_transfer['properties'].update(liquid=liquid_selection_schema(), recipe=recipe_editor['properties']['recipe'])
    class_transfer['properties']['recipe']['required'] = [k for k in class_transfer['properties']['recipe']['required']
        if k not in {'mode', 'channels', 'before_leading_air', 'before_liquid', 'after_liquid', 'before_dispense', 'after_dispense'}]
    for entry in actions:
        if entry['action'] == 'transfer':
            entry['inputs'] = {'oneOf': [deepcopy(transfer_schema), class_transfer],
                '$defs': {**transfer_defs, **recipe_editor['$defs']}}
    for name in ('distribute', 'consolidate'):
        alternatives = [{'type': 'object', 'required': ['mode', 'transfers'], 'additionalProperties': False,
            'properties': {'mode': {'const': 'repeated_single'}, 'transfers': {'type': 'array', 'minItems': 1, 'items': {'oneOf': [transfer_schema, class_transfer]}}}}]
        recipe_schema = deepcopy(EXPORT['recipe'])
        recipe_defs = recipe_schema.pop('$defs', {})
        if name == 'distribute':
            alternatives.append({'type': 'object', 'required': ['mode', 'recipe'], 'properties': {
                'mode': {'const': 'multi'}, 'recipe': recipe_schema}})
        actions.append({'action': name, 'inputs': {'oneOf': alternatives, '$defs': {**transfer_defs, **recipe_defs, **recipe_editor['$defs']}},
            'status': {'authorable': True, 'emitted': True, 'registered': True, 'connected_tested': False, 'physically_qualified': False},
            'effects': 'Explicit repeated-single transfers are not true multi aliquots; true multi uses complete native recipe'})
    actions.append({'action': 'tip_eject', 'inputs': {'type': 'object', 'required': ['channels'],
        'properties': {'channels': {'const': [0, 1, 2, 3]}}, 'additionalProperties': False},
        'status': {'authorable': True, 'emitted': True, 'registered': True},
        'effects': 'Existing all-channel source ejection; selected-channel diagnostic remains separate'})
    pickup = deepcopy(schema['$defs']['ManualLoadTip'])
    pickup['properties'].pop('operation', None)
    pickup['required'] = [k for k in pickup.get('required', []) if k != 'operation']
    pickup['properties']['channels'] = {'const': [0, 1, 2, 3]}
    actions.append({'action': 'tip_pickup', 'inputs': pickup,
        'status': {'authorable': True, 'emitted': True, 'registered': True}, 'effects': 'Source group-of-four pickup'})
    for entry in actions:
        if entry['action'] in {'plate_move', 'move_cover', 'plate_catch', 'plate_release', 'plate_press', 'gripper_catch', 'gripper_release', 'gripper_press'}:
            entry['inputs']['properties']['labware_id'] = {'type': 'string', 'description': 'Stable logical identity; retained as metadata, not a controller setting'}
        if entry['action'] in {'cavro_application', 'liquid_recipe', 'fluid_search', 'pipette_settings', 'pressure_stream'}:
            entry['status']['registered'] = True  # pinned native finite-owner registration
        if entry['action'] == 'plunger':
            entry['inputs'] = deepcopy(schema['$defs']['DiagnosticPlunger'])
            entry['status'].update(emitted=True, registered=True)
            entry['integration'] = 'Existing diagnostic_pipette physical owner; native all-pipette plunger operation'
        if entry['action'] in {'park', 'led', 'status_light', 'seal_separate'}:
            entry['source_revision'] = EXPORT['source_commit']
            entry['source_contract'] = 'Committed native binding contract; source revision is pinned above.'
            entry['native_binding'] = deepcopy(EXPORT['method_contract']['bindings'][ALIASES.get(entry['action'], entry['action'])])
            entry['status']['registered'] = True
        if entry['action'] == 'seal_separate':
            entry['effects'] = 'Source SS lower-interpreter no-op; not a physical seal actuator or operator completion claim'
        if entry['action'] == 'park':
            entry['effects'] = 'Source Park; not a well move. Omitted rehome retains native false default'
        if entry['action'] in {'led', 'status_light'}:
            entry['effects'] = 'Source status RGB; separate from camera illumination'
        if entry['action'] == 'inspect':
            entry['effects'] = 'Source cover inspection can relocate covers; not photo-only'
        if entry['action'] == 'thermal_door':
            entry['effects'] = 'Source DO opens thermal door then initializes pipettes; DC closes door'
    from bioxp_method_custody import custody_suggestions
    from bioxp_method_planning import liquid_field_matrix
    return {'schema': SCHEMA, 'actions': actions, 'native_definitions': schema['$defs'],
        'authoring': {'custody': custody_suggestions(), 'liquid_fields': liquid_field_matrix()},
        'geometry': native['alignment'], 'locations': native['native_locations'],
        'limits': {'depth': 64, 'occurrences': 10000, 'native_actions': 100000},
        'precision': 'Exact literals and integer boundaries; arithmetic Decimal 28 significant digits, ROUND_HALF_EVEN; normalized base-unit decimal strings; native emitter converts at boundary',
        'qualification': 'Software representation is not physical qualification'}


def method_examples():
    from bioxp_method_examples import examples, bound_examples, magnetic_variant
    entries = deepcopy(examples())
    fixtures = {e['id']: e for e in bound_examples()}
    for entry in entries:
        entry['bound_fixture'] = fixtures[entry['id']]
        if entry['id'] in {'purification', 'dna_purification', 'rna_purification', 'cfps_and_purification'}:
            entry['authoring_variants'] = [magnetic_variant(entry)]
    return entries

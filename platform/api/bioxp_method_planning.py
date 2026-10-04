"""Logical occurrence preparation before physical child lowering."""
from copy import deepcopy
from decimal import Decimal
from bioxp_method_liquids import resolve_liquid_settings, record_liquid_application
from bioxp_method_simulation import channel_wells, plan_tip_policy

# Paths are application requests, not guessed Cavro opcodes.
RECIPE_FIELDS = {
    'target_volume_ul': ('target_liquid_ul',),
    'commanded_corrected_aspiration_ul': ('commanded_aspiration_ul',),
    'aspirate_speed_ul_s': ('aspiration_speed_ul_s',),
    'aspiration_delay_ms': ('aspiration_delay_ms',),
    'dispense_segments': ('dispense_segments',),
    'blowout_leading_air_gap_ul': ('leading_air', 'volume_ul'),
    'carry_trailing_air_gap_ul': ('trailing_air', 'volume_ul'),
    'number_of_dispenses': ('multi', 'sample_count'),
    'dispense_volume_ul': ('multi', 'sample_volume_ul'),
    'conditioning_volume_ul': ('multi', 'conditioning_volume_ul'),
    'number_back_to_source': ('multi', 'conditioning_back_to_source_count'),
    'excess_volume_ul': ('multi', 'excess_volume_ul'),
    'reaspiration_volume_ul': ('multi', 'reaspiration', 'volume_ul'),
    'dispense_to_reaspiration_delay_ms': ('multi', 'dispense_to_reaspiration_delay_ms'),
}
# One class value, the speed of aspirating air gaps, lands on each existing
# native Air phase (Recipe.leading_air/trailing_air.speed_ul_s). No phase is
# created for it; an explicitly null/absent phase receives nothing.
AIR_GAP_SPEED_FIELD = 'air_gap_aspiration_speed_ul_s'
AIR_GAP_SPEED_PATHS = (('leading_air', 'speed_ul_s'), ('trailing_air', 'speed_ul_s'))
# Retained explicit representation errors:
# retract_distance_mm: native Z motion is in steps only (z_move target_steps,
#   lift height_steps relative to zLow); the native owner defines no Z mm->steps
#   conversion, so no conversion is invented here.
# calibration_function/correction_points: native Recipe has no calibration
#   field and does not call cavro_liquid.correction_at_target; the corrected
#   displacement is authored directly as commanded_corrected_aspiration_ul.
UNMAPPED_REASONS = {
    'retract_distance_mm': 'native Z motion is authored in steps (after_liquid lift height_steps / z_move target_steps); '
                           'the native owner defines no Z mm-to-steps conversion',
    'calibration_function': 'native Recipe has no calibration field and does not call correction_at_target; '
                            'author commanded_corrected_aspiration_ul explicitly',
    'correction_points': 'native Recipe has no correction field and does not call correction_at_target; '
                         'author commanded_corrected_aspiration_ul explicitly',
}


def liquid_selection_schema():
    """Typed executable selection fields; persistence still preserves raw JSON."""
    from bioxp_method_native import SETTINGS
    number = {'anyOf': [{'type': 'number'}, {'type': 'string'}, {'type': 'null'}]}
    settings = {key: deepcopy(number) for key in (*RECIPE_FIELDS, AIR_GAP_SPEED_FIELD) if key != 'dispense_segments'}
    settings.update({key: {'anyOf': [deepcopy(value), {'type': 'null'}]} for key, value in SETTINGS['properties'].items()})
    settings['dispense_segments'] = {'type': 'array', 'items': {'type': 'object', 'properties': {
        'volume_ul': deepcopy(number), 'speed_ul_s': deepcopy(number),
        'source_heading': {'type': 'string'}, 'evidence': {'type': 'string'}}, 'required': ['volume_ul', 'speed_ul_s']}}
    context = {'type': 'object', 'properties': {
        'generation': {'type': 'string'}, 'tip_profile_id': {'type': 'string'}, 'filter_type': {'type': 'string'},
        'mode': {'enum': ['single-dispense', 'multi-dispense']}, 'recipe_context': {'type': 'string'},
        'target_volume_ul': deepcopy(number), 'aliquot_volume_ul': deepcopy(number), 'sample_count': {'type': 'integer'},
        'applicable_fields': {'type': 'array', 'items': {'type': 'string'}}}}
    entry = {'type': 'object', 'properties': {'id': {'type': 'string'}, 'revision': {'type': 'integer'},
        'context': deepcopy(context), 'settings': {'type': 'object', 'properties': deepcopy(settings)},
        'provenance_kind': {'type': 'string'}}, 'required': ['id', 'revision', 'context', 'settings']}
    return {'type': 'object', 'additionalProperties': False, 'required': ['context'], 'properties': {
        'requested': {'type': 'object', 'properties': settings}, 'context': context,
        'water': {'anyOf': [{'type': 'string'}, deepcopy(entry), {'type': 'null'}]},
        'liquid_class': {'anyOf': [{'type': 'string'}, deepcopy(entry), {'type': 'null'}]},
        'setting_phases': {'type': 'object', 'properties': {key: {'enum': ['leading_air', 'aspirate', 'trailing_air', 'dispense']}
            for key in SETTINGS['properties']}, 'additionalProperties': False}}}


def same_setting(left, right):
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_setting(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_setting(a, b) for a, b in zip(left, right))
    if left == right:
        return True
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and a == b
    except ArithmeticError:
        return False


def liquid_resolution(inputs, dependencies):
    selection = inputs.pop('liquid', None)
    if selection is None:
        return None
    if not isinstance(selection, dict) or set(selection) - {'requested', 'context', 'water', 'liquid_class', 'setting_phases'}:
        raise ValueError('Unknown liquid selection fields; no scientific setting is silently discarded')
    def entry(name):
        value = selection.get(name)
        if isinstance(value, str):
            entries = dependencies.get('liquid_classes', [])
            matches = [x for x in entries if x.get('id') == value]
            if len(matches) != 1:
                raise ValueError(f'Liquid dependency {value!r} must identify one pinned entry')
            return matches[0]
        if value is not None and not isinstance(value, dict):
            raise ValueError(f'{name}: expected an embedded entry or pinned ID')
        return value
    recipe = inputs.get('recipe')
    if not isinstance(recipe, dict):
        raise ValueError('Liquid-class application requires an explicit native recipe')
    requested = deepcopy(selection.get('requested', {}))
    if not isinstance(requested, dict) or not isinstance(selection.get('context'), dict):
        raise ValueError('Liquid requested settings and context must be objects')
    recipe_requested = {}
    for field, path in RECIPE_FIELDS.items():
        value = recipe
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            recipe_requested[field] = deepcopy(value)
            if field not in requested:
                requested[field] = deepcopy(value)
    # Authored air-phase speeds count as the request only when every authored
    # phase agrees; differing phase speeds stay phase-specific authored values.
    # A phase missing its speed is not filled from the other phase's speed.
    phases = [recipe[p] for p, _ in AIR_GAP_SPEED_PATHS if isinstance(recipe.get(p), dict)]
    authored_air = [phase['speed_ul_s'] for phase in phases if 'speed_ul_s' in phase]
    if authored_air and len(authored_air) == len(phases) and all(same_setting(authored_air[0], v) for v in authored_air):
        recipe_requested[AIR_GAP_SPEED_FIELD] = deepcopy(authored_air[0])
        if AIR_GAP_SPEED_FIELD not in requested:
            requested[AIR_GAP_SPEED_FIELD] = deepcopy(authored_air[0])
    resolution = resolve_liquid_settings(requested,
        liquid_class=entry('liquid_class'), water=entry('water'), context=selection['context'])
    resolution['selection_requested'] = deepcopy(selection.get('requested', {}))
    resolution['recipe_requested_fields'] = recipe_requested
    emitted = {}
    for field, value in resolution['resolved'].items():
        path = RECIPE_FIELDS.get(field)
        phase = selection.get('setting_phases', {}).get(field)
        if phase is not None:
            from bioxp_method_native import SETTINGS
            if phase not in {'leading_air', 'aspirate', 'trailing_air', 'dispense'} or field not in SETTINGS['properties']:
                raise ValueError(f'{field}: unsupported explicit setting phase {phase!r}')
            path = ('phase_settings', phase, field)
        if path is None and field == AIR_GAP_SPEED_FIELD:
            continue  # applied after every phase volume is placed, below
        if path is None:
            resolution['issues'].append({'code': 'liquid_field_not_mapped', 'category': 'advisory',
                'path': '/' + field, 'message': 'No native recipe field mapping; retained, not reported applied.'})
            if resolution['fields'][field]['requested']['present']:
                reason = UNMAPPED_REASONS.get(field)
                raise ValueError(f'{field}: explicit liquid setting has no native recipe mapping; no partial recipe emitted'
                                 + (f' ({reason})' if reason else ''))
            continue
        applied_value = deepcopy(value)
        if field == 'dispense_segments':
            applied_value = []
            for segment in value:
                if segment.get('evidence') == 'source_dash_no_first_segment_reported' and segment.get('volume_ul') is None and segment.get('speed_ul_s') is None:
                    continue  # source explicitly reports no first segment; retained in resolved ledger
                if segment.get('volume_ul') is None or segment.get('speed_ul_s') is None:
                    raise ValueError('Incomplete dispense segment; no fabricated volume/speed')
                applied_value.append({key: segment[key] for key in ('volume_ul', 'speed_ul_s')})
        target = recipe
        for key in path[:-1]:
            if key not in target:
                target[key] = {}
            if not isinstance(target[key], dict):
                raise ValueError(f'{field}: explicit null/incompatible recipe phase is not overwritten by Water')
            target = target[key]
        key = path[-1]
        if key in target and not same_setting(target[key], applied_value):
            # Do not overwrite a separately authored recipe value.
            raise ValueError(f'{field}: liquid setting conflicts with explicitly authored recipe {"/".join(path)}')
        target[key] = deepcopy(applied_value)
        emitted[field] = {'status': 'emitted', 'value': deepcopy(applied_value), 'native_path': '/recipe/' + '/'.join(path)}
    if AIR_GAP_SPEED_FIELD in resolution['resolved'] and AIR_GAP_SPEED_FIELD not in selection.get('setting_phases', {}):
        value = resolution['resolved'][AIR_GAP_SPEED_FIELD]
        explicit = resolution['fields'][AIR_GAP_SPEED_FIELD]['requested']['present']
        paths, retained = [], []
        for phase, key in AIR_GAP_SPEED_PATHS:
            target = recipe.get(phase)
            if not isinstance(target, dict):
                continue  # absent/explicit-null air phase: no air stroke, nothing fabricated
            if key in target and not same_setting(target[key], value):
                if explicit:
                    raise ValueError(f'{AIR_GAP_SPEED_FIELD}: liquid setting conflicts with explicitly authored recipe {phase}/{key}')
                retained.append('/recipe/' + phase + '/' + key)  # authored phase speed is never overwritten
                continue
            target[key] = deepcopy(value)
            paths.append('/recipe/' + phase + '/' + key)
        if paths:
            emitted[AIR_GAP_SPEED_FIELD] = {'status': 'emitted', 'value': deepcopy(value), 'native_paths': paths,
                                            'native_path': paths[0], 'authored_phase_values_retained': retained}
        elif explicit:
            raise ValueError(f'{AIR_GAP_SPEED_FIELD}: recipe has no leading_air/trailing_air phase to receive the explicit speed; no air phase is fabricated')
        else:
            resolution['issues'].append({'code': 'liquid_field_not_emitted', 'category': 'advisory', 'path': '/' + AIR_GAP_SPEED_FIELD,
                'message': 'No receiving native air phase (or authored phase speeds retained); not reported applied.'})
    resolution = record_liquid_application(resolution, emitted=emitted)
    recipe.setdefault('liquid_settings', {})['bms_resolution'] = deepcopy(resolution)
    return resolution


def prepare_class_transfer(inputs):
    """Bind authored transfer quantities; no inferred recipe science or defaults."""
    recipe = inputs.get('recipe')
    if not isinstance(recipe, dict):
        raise ValueError('Class-selected Transfer requires explicit recipe phase options')
    for key, value in {'mode': 'single', 'channels': inputs['channels'],
                       'target_liquid_ul': inputs['volume_ul']}.items():
        if key in recipe and not same_setting(recipe[key], value):
            raise ValueError(f'Transfer conflicts with recipe {key}')
        recipe[key] = deepcopy(value)
    if 'aspirate_speed' in inputs:
        if 'aspiration_speed_ul_s' in recipe and not same_setting(recipe['aspiration_speed_ul_s'], inputs['aspirate_speed']):
            raise ValueError('Transfer aspiration speed conflicts with recipe')
        recipe['aspiration_speed_ul_s'] = inputs['aspirate_speed']
    if 'dispense_speed' in inputs:
        raise ValueError('Class Transfer uses ordered recipe segment speeds; remove legacy dispense_speed explicitly')


def positioned_transfer_recipes(inputs):
    """Position around a resolved recipe; the native owner alone expands strokes."""
    from bioxp_workflow_authoring import native_intent
    allowed = {'source', 'destination', 'channels', 'volume_ul', 'aspirate_speed', 'recipe',
               'source_position_flag', 'destination_position_flag',
               'source_lift_height_steps', 'destination_lift_height_steps'}
    if set(inputs) - allowed:
        raise ValueError(f'Unsupported class Transfer fields: {sorted(set(inputs) - allowed)}')
    source, destination = inputs['source'], inputs['destination']
    for endpoint in (source, destination):
        if set(endpoint) - {'station', 'location_id', 'wells'}:
            raise ValueError('Unsupported Transfer endpoint fields')
    if not source['wells'] or len(source['wells']) != len(destination['wells']):
        raise ValueError('Transfer requires equal nonempty reference-well pairs')
    recipes = []
    for sw, dw in zip(source['wells'], destination['wells']):
        recipe = deepcopy(inputs['recipe'])
        def position(operation, side, well):
            endpoint = inputs[side]
            params = {'operation': operation, 'location_id': endpoint['location_id']}
            if operation == 'move':
                params.update(well=well, position_flag=inputs[side + '_position_flag'])
            elif operation == 'lift':
                params['height_steps'] = inputs[side + '_lift_height_steps']
            return {'operation': 'position', 'positioning': native_intent(params)}
        phases = {
            'before_leading_air': [position('move', 'source', sw)],
            'before_liquid': [position('lower', 'source', sw)],
            'after_liquid': [position('lift', 'source', sw)],
            'before_dispense': [position('move', 'destination', dw), position('lower', 'destination', dw)],
            'after_dispense': [position('lift', 'destination', dw)],
        }
        for key, moves in phases.items():
            # Additional authored search/contact motions retain their order.
            recipe[key] = moves + recipe.get(key, [])
        recipes.append(recipe)
    return recipes


class RepresentationErrors(ValueError):
    """Every per-step representation error, each addressed to its occurrence."""
    def __init__(self, issues):
        super().__init__(issues[0]['message'])
        self.issues = issues


def prepare_occurrences(occurrences, method, dependencies):
    """Attach logical accounting without changing native addressing or admission."""
    result, notices, resolutions, issues = [], [], [], []
    if any(not isinstance(o.get('inputs'), dict) for o in occurrences):
        raise ValueError('Action inputs must be an object')
    deck = method.get('deck_plan', {})
    profiles = {p['id']: p for p in dependencies.get('labware_profiles', [])}
    labware = deepcopy(deck.get('labware', []))
    expanded = []
    count = sum(len(o['inputs'].get('transfers', [])) if o['action'] in ('distribute', 'consolidate') and o['inputs'].get('mode') == 'repeated_single' else 1 for o in occurrences)
    if count > 10000:
        raise ValueError('Compound expansion exceeds 10000 logical transfers')
    for original in occurrences:
        if original['action'] in ('distribute', 'consolidate'):
            inputs = original['inputs']
            if inputs.get('mode') == 'repeated_single':
                if set(inputs) != {'mode', 'transfers'} or not isinstance(inputs['transfers'], list) or not inputs['transfers']:
                    raise ValueError('Repeated-single compound requires a nonempty explicit transfers list')
                if len(expanded) + len(inputs['transfers']) > 10000:
                    raise ValueError('Compound expansion exceeds 10000 logical transfers')
                for index, transfer in enumerate(inputs['transfers']):
                    expanded.append({**deepcopy({k: v for k, v in original.items() if k != 'inputs'}), 'action': 'transfer', 'inputs': deepcopy(transfer),
                        'occurrence_id': original['occurrence_id'] + f'/transfer/{index}',
                        'compound_parent': original['occurrence_id'], 'compound_index': index})
            elif original['action'] == 'distribute' and inputs.get('mode') == 'multi':
                if inputs.get('recipe', {}).get('mode') != 'multi':
                    raise ValueError('True multi-distribute requires a complete multi recipe')
                expanded.append({**deepcopy(original), 'action': 'liquid_recipe',
                    'inputs': {k: deepcopy(v) for k, v in inputs.items() if k != 'mode'}})
            else:
                raise ValueError('Choose explicit repeated_single transfers; true multi distribute uses mode multi and a native recipe')
        else:
            expanded.append(original)
    failures = []
    for original in expanded:
        row = deepcopy(original)
        inputs = row['inputs']
        if row['action'] == 'native_intent' and isinstance(inputs, dict):
            row['action'] = inputs.pop('operation', None)
        if row['action'] == 'load_tip':
            row['action'] = 'tip_pickup'
            inputs.setdefault('channels', [0, 1, 2, 3])  # native pickup is always four-channel
        if not isinstance(inputs, dict):
            raise ValueError('Action inputs must be an object')
        try:
            if row['action'] == 'transfer' and 'liquid' in inputs:
                prepare_class_transfer(inputs)
            resolution = liquid_resolution(inputs, dependencies)
        except ValueError as exc:
            failures.append({'code': 'representation_error', 'category': 'representation', 'message': str(exc),
                'path': row.get('path', ''), 'step_id': row.get('step_id'), 'occurrence_id': row['occurrence_id']})
            continue
        if resolution:
            resolutions.append({'occurrence_id': row['occurrence_id'], **resolution})
            notices.extend({'occurrence_id': row['occurrence_id'], 'step_id': row['step_id'], **n} for n in resolution['water_substitutions'])
            issues.extend({'occurrence_id': row['occurrence_id'], **i} for i in resolution['issues'])
        if row['action'] == 'transfer' and 'channel_transfers' not in inputs:
            try:
                endpoints = []
                for key in ('source', 'destination'):
                    endpoint = inputs[key]
                    from bioxp_workflow_authoring import _NATIVE
                    station = _NATIVE['locations'].get(str(int(endpoint['location_id'])))
                    candidates = [x for x in labware if (x.get('id') == endpoint['labware_id'] if 'labware_id' in endpoint else x.get('station') == station)]
                    if len(candidates) != 1:
                        raise ValueError(f'{key}: stable labware identity is unknown or ambiguous')
                    vessel = candidates[0]
                    geometry = profiles.get(vessel.get('profile_id'), {}).get('native_addressing')
                    if not geometry:
                        raise ValueError(f'{key}: pinned signed channel addressing is unknown')
                    endpoints.append([[{'labware_id': vessel['id'], **c} for c in channel_wells(well, inputs['channels'],
                        row_increment=geometry['row_increment'], column_increment=geometry['column_increment'],
                        reference_channel=geometry['reference_channel'])] for well in endpoint['wells']])
                if len(endpoints[0]) != len(endpoints[1]):
                    raise ValueError('Source/destination head-reference pair count differs')
                inputs['channel_transfers'] = [
                    {'channel': src['channel'], 'source': {'labware_id': src['labware_id'], 'well': src['well']},
                     'destination': {'labware_id': dst['labware_id'], 'well': dst['well']},
                     'volume_ul': inputs['volume_ul'], 'pair_index': pair}
                    for pair, (sources, destinations) in enumerate(zip(*endpoints)) for src, dst in zip(sources, destinations)]
            except (KeyError, ValueError, TypeError) as exc:
                issues.append({'code': 'liquid_geometry_unknown', 'category': 'advisory',
                    'occurrence_id': row['occurrence_id'], 'message': str(exc)})
        if row['action'] in {'aspirate', 'dispense', 'mix'} and 'channel_transfers' not in inputs:
            inputs['channel_transfers'] = [{'channel': channel, 'volume_ul': None,
                'commanded_displacement_ul': inputs.get('volume_ul'), 'air_ul': None}
                for channel in inputs.get('channels', [])]
        if row['action'] == 'transfer':
            recipe = inputs.get('recipe')
            for effect in inputs.get('channel_transfers', []):
                if recipe is None:
                    effect.setdefault('commanded_displacement_ul', inputs.get('volume_ul'))
                    effect.setdefault('air_ul', None)
                else:
                    effect.setdefault('commanded_displacement_ul', recipe.get('commanded_aspiration_ul'))
                    airs = [recipe.get(key) for key in ('leading_air', 'trailing_air')]
                    try:
                        effect.setdefault('air_ul', format(sum((Decimal(str(a['volume_ul'])) if a is not None else Decimal(0) for a in airs)), 'f'))
                    except (KeyError, ArithmeticError):
                        effect.setdefault('air_ul', None)
                    effect['dispense_segments'] = deepcopy(recipe.get('dispense_segments'))
                    effect['final_empty_tip'] = recipe.get('final_empty_speed_ul_s') is not None
        result.append(row)
        if row['action'] == 'plate_move' and 'labware_id' in inputs:
            for vessel in labware:
                if vessel.get('id') == inputs['labware_id']:
                    vessel['station'] = inputs.get('target_location')
    if failures:
        raise RepresentationErrors(failures)
    policy = method.get('tip_policy', 'manual')
    if policy is None:
        policy = 'manual'
    if not isinstance(policy, (dict, str)):
        raise ValueError('Tip policy must be a mode or policy object')
    mode = policy if isinstance(policy, str) else policy.get('mode', 'manual')
    if mode not in {'manual', 'per_transfer', 'per_source', 'per_step'}:
        raise ValueError(f'Unknown executable tip policy {mode!r}')
    planned = plan_tip_policy(result, policy)
    by_id = {r['occurrence_id']: r for r in result}
    for row in planned['occurrences']:
        if 'generated_by' in row:
            parent = by_id[row['generated_by']['occurrence_id']]
            for key in ('path', 'call_path', 'loop_path', 'group_path', 'on_error'):
                row[key] = deepcopy(parent[key])
    return planned['occurrences'], resolutions, notices, issues + planned['issues']


def emission_inputs(occurrence):
    inputs = deepcopy(occurrence['inputs'])
    inputs.pop('channel_transfers', None)
    if occurrence['action'] in {'plate_move', 'move_cover', 'plate_catch', 'plate_release', 'plate_press', 'gripper_catch', 'gripper_release', 'gripper_press'}:
        inputs.pop('labware_id', None)  # stable logical identity is retained in occurrence metadata, not a device setting
    if occurrence['action'] == 'transfer':
        for name in ('source', 'destination'):
            if isinstance(inputs.get(name), dict):
                inputs[name].pop('labware_id', None)
    return inputs


def initial_state(method, explicit):
    state = deepcopy(explicit or {})
    for vessel in method.get('deck_plan', {}).get('labware', []):
        state.setdefault('labware', {}).setdefault(vessel['id'], deepcopy(vessel))
    planned_vessels = {}
    for assignment in method.get('deck_plan', {}).get('assignments', []):
        key = f"{assignment['labware_id']}:{assignment['well']}"
        if key not in planned_vessels:
            planned_vessels[key] = {'volume_ul': assignment.get('volume_ul'),
                'materials': [assignment['material_id']] if 'material_id' in assignment else None,
                'assignments': [deepcopy(assignment)]}
        else:
            vessel = planned_vessels[key]
            vessel['assignments'].append(deepcopy(assignment))
            if isinstance(vessel['materials'], list) and 'material_id' in assignment:
                if assignment['material_id'] not in vessel['materials']:
                    vessel['materials'].append(assignment['material_id'])
            else:
                vessel['materials'] = None
            try:
                left, right = Decimal(str(vessel['volume_ul'])), Decimal(str(assignment.get('volume_ul')))
                vessel['volume_ul'] = format(left + right, 'f') if left.is_finite() and right.is_finite() else None
            except ArithmeticError:
                vessel['volume_ul'] = None
    for key, value in planned_vessels.items():
        state.setdefault('vessels', {}).setdefault(key, value)
    return state

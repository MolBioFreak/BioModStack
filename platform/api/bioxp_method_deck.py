"""Typed starting-deck contract for BioXP methods.

One authored starting deck per method: labware per station, well contents,
tip racks and where each movable plate/cover starts. Check and compile report
problems as advisory issues; nothing here refuses compilation or changes native
addressing. Raw persistence keeps every authored value unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

ROWS = 'ABCDEFGH'
WELL = re.compile(r'([A-Ha-h])(1[0-2]|[1-9])')

# Pipetting stations that carry addressable wells (OEM ClassMachineStatus trays).
STATION_GEOMETRY = {
    'LOC_MS': (8, 12), 'LOC_OC': (8, 12), 'LOC_TC': (8, 12), 'LOC_RC': (8, 12),
    'LOC_STRIP1': (8, 1), 'LOC_STRIP2': (8, 1), 'LOC_STRIP3': (8, 1), 'LOC_STRIP4': (8, 1),
    'LOC_TROUGH': (8, 12),
}
# OEM tip trays 1-4 are TECANRACK1-4; tray 5 is the tip hotel (construct_new_machine_source_model).
TIP_TRAYS = {1: 'TECANRACK1', 2: 'TECANRACK2', 3: 'TECANRACK3', 4: 'TECANRACK4', 5: 'LOC_TIP_HOTEL'}
TIP_TYPES = ('T50', 'T200', 'T201')
# ClassMachineStatus movable objects and their constructor locations.
MOVABLE_OBJECTS = {
    'POOL_PLATE': 'LOC_P_TC', 'OUTPUT_PLATE': 'LOC_P_OC', 'REAGENT_PLATE': 'LOC_RC',
    'STRIP_ONE': 'LOC_STRIP1', 'STRIP_TWO': 'LOC_STRIP2', 'STRIP_THREE': 'LOC_STRIP3',
    'STRIP_FOUR': 'LOC_STRIP4', 'REAGENT_COVER': 'LOC_RC_COVER_STORAGE',
    'OUTPUT_COVER': 'LOC_OC_COVER_STORAGE', 'BIO_SECURITY_COVER': 'LOC_BSC', 'TROUGH': 'LOC_TROUGH',
}
# MP/MC script tokens (ClassGlobals.lookupSrcDstPlate) to movable object names.
TOKEN_OBJECTS = {
    'PL_POOL': 'POOL_PLATE', 'PL_OUTPUT': 'OUTPUT_PLATE', 'PL_REAGENT': 'REAGENT_PLATE',
    'CV_OUTPUT': 'OUTPUT_COVER', 'CV_REAGENT': 'REAGENT_COVER', 'CV_BIOSECURITY': 'BIO_SECURITY_COVER',
    'STRIP1': 'STRIP_ONE', 'STRIP2': 'STRIP_TWO', 'STRIP3': 'STRIP_THREE', 'STRIP4': 'STRIP_FOUR',
    'TROUGH': 'TROUGH',
}
COVERS = {'OUTPUT_COVER', 'REAGENT_COVER', 'BIO_SECURITY_COVER'}
# ControlLib.inspectCover:3745-3752 unconditional terminal assignments (4->18, 5->20).
INSPECTED_COVER_LOCATIONS = {'OUTPUT_COVER': 'LOC_OC_COVER_STORAGE', 'REAGENT_COVER': 'LOC_RC_COVER_STORAGE'}
# lookupDesLocation aliases; covers and plates resolve differently (translate_oem_plate_move).
TARGET_ALIASES = {
    'LOC_BSCS': ('LOC_BSCS', 'LOC_BSCS'), 'LOC_TC': ('LOC_BSC', 'LOC_P_TC'),
    'LOC_RC': ('LOC_RC_COVER', 'UNKNOWN'), 'LOC_RCS': ('LOC_RC_COVER_STORAGE', 'LOC_RC_COVER_STORAGE'),
    'LOC_OC': ('LOC_OC_COVER', 'LOC_P_OC'), 'LOC_OCS': ('LOC_OC_COVER_STORAGE', 'LOC_OC_COVER_STORAGE'),
    'LOC_MS': ('LOC_P_MS', 'LOC_P_MS'),
}
MATERIAL_KINDS = ('sample', 'reagent', 'product', 'waste')
TOP_KEYS = ('labware', 'materials', 'assignments', 'tip_racks', 'movable_objects')


def _locations():
    from bioxp_workflow_authoring import _NATIVE
    return set(_NATIVE['locations'].values()) | {'LOC_GANTRY'}


def well_index(well):
    match = WELL.fullmatch(well) if isinstance(well, str) else None
    if not match:
        return None
    return (ROWS.index(match[1].upper()), int(match[2]) - 1)


def well_name(row, column):
    return f'{ROWS[row]}{column + 1}'


def deck_schema():
    text = {'type': 'string'}
    number = {'anyOf': [{'type': 'number'}, {'type': 'string'}, {'type': 'null'}]}
    well = {'type': 'string', 'pattern': '^[A-Ha-h](?:[1-9]|1[0-2])$'}
    return {
        'type': 'object', 'additionalProperties': True,
        'description': ('Starting deck. Check/compile report inconsistencies as advisory issues; '
                        'they never refuse compilation or change native targets. Planned, not observed.'),
        'properties': {
            'labware': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'station'], 'properties': {
                'id': text, 'station': {'enum': sorted(STATION_GEOMETRY)}, 'name': text, 'profile_id': text,
                'native_plate_id': {'description': 'Optional MP/MC token or movable object this labware is'}}}},
            'materials': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'name'], 'properties': {
                'id': text, 'name': text, 'kind': {'enum': list(MATERIAL_KINDS)}, 'description': text,
                'concentration': number, 'concentration_unit': text}}},
            'assignments': {'type': 'array', 'items': {'type': 'object',
                'required': ['id', 'labware_id', 'well', 'material_id', 'volume_ul'], 'properties': {
                    'id': text, 'labware_id': text, 'well': well, 'material_id': text, 'volume_ul': number}}},
            'tip_racks': {'type': 'array', 'description': ('OEM tip trays: 1-4 TECANRACK1-4, 5 tip hotel. 96 tips; '
                    'four-channel pickup at A/B head references takes four tips (rows +0/+2/+4/+6).'),
                'items': {'type': 'object', 'required': ['tray', 'tips'], 'properties': {
                    'tray': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'tip_type': {'enum': [*TIP_TYPES, None]},
                    'tips': {'anyOf': [{'enum': ['full', 'empty']},
                        {'type': 'object', 'required': ['available'], 'properties': {
                            'available': {'type': 'array', 'items': well}}, 'additionalProperties': False}]}}}},
            'movable_objects': {'type': 'object', 'description': 'Where each plate/cover starts (ClassMachineStatus names).',
                'propertyNames': {'enum': sorted(MOVABLE_OBJECTS)},
                'additionalProperties': {'type': 'string'}},
        }}


def _issue(code, path, message):
    return {'code': code, 'category': 'advisory', 'path': path, 'message': message}


def _wells_for(station):
    return STATION_GEOMETRY.get(station)


def check_deck(method):
    """Report-only starting-deck findings. Never raises for authored content."""
    raw = method.get('deck_plan') if isinstance(method, dict) else None
    issues = []
    if raw is None:
        return issues
    base = '/method/deck_plan'
    if not isinstance(raw, dict):
        return [_issue('deck_not_object', base, 'Starting deck must be an object')]
    for key in TOP_KEYS:
        if key in raw and not isinstance(raw[key], dict if key == 'movable_objects' else list):
            issues.append(_issue('deck_section_type', f'{base}/{key}', f'{key} has the wrong JSON type'))
    labware, materials = {}, {}
    for index, row in enumerate(raw.get('labware') if isinstance(raw.get('labware'), list) else []):
        path = f'{base}/labware/{index}'
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            issues.append(_issue('deck_labware_id', path, 'Labware needs a nonempty id'))
            continue
        if row['id'] in labware:
            issues.append(_issue('deck_duplicate_id', path, f'Duplicate labware id {row["id"]!r}'))
        labware[row['id']] = row
        if row.get('station') not in STATION_GEOMETRY:
            issues.append(_issue('deck_station_unknown', path + '/station',
                                 f'Station {row.get("station")!r} has no addressable wells'))
    stations = {}
    for lid, row in labware.items():
        stations.setdefault(row.get('station'), []).append(lid)
    for station, ids in stations.items():
        if station in STATION_GEOMETRY and len(ids) > 1:
            issues.append(_issue('deck_station_shared', base + '/labware',
                                 f'Station {station} holds several labware: {", ".join(ids)}'))
    for index, row in enumerate(raw.get('materials') if isinstance(raw.get('materials'), list) else []):
        path = f'{base}/materials/{index}'
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            issues.append(_issue('deck_material_id', path, 'Material needs a nonempty id'))
            continue
        if row['id'] in materials:
            issues.append(_issue('deck_duplicate_id', path, f'Duplicate material id {row["id"]!r}'))
        materials[row['id']] = row
        if 'kind' in row and row['kind'] not in MATERIAL_KINDS:
            issues.append(_issue('deck_material_kind', path + '/kind', f'Unknown material kind {row["kind"]!r}'))
    seen_assignment = set()
    for index, row in enumerate(raw.get('assignments') if isinstance(raw.get('assignments'), list) else []):
        path = f'{base}/assignments/{index}'
        if not isinstance(row, dict):
            issues.append(_issue('deck_assignment_type', path, 'Assignment must be an object'))
            continue
        if row.get('id') in seen_assignment:
            issues.append(_issue('deck_duplicate_id', path, f'Duplicate assignment id {row.get("id")!r}'))
        seen_assignment.add(row.get('id'))
        vessel = labware.get(row.get('labware_id'))
        if vessel is None:
            issues.append(_issue('deck_assignment_labware', path + '/labware_id',
                                 f'No labware {row.get("labware_id")!r} on the deck'))
        if row.get('material_id') not in materials:
            issues.append(_issue('deck_assignment_material', path + '/material_id',
                                 f'No material {row.get("material_id")!r} declared'))
        position = well_index(row.get('well'))
        geometry = _wells_for(vessel.get('station')) if vessel else None
        if position is None or (geometry and (position[0] >= geometry[0] or position[1] >= geometry[1])):
            issues.append(_issue('deck_assignment_well', path + '/well',
                                 f'Well {row.get("well")!r} is not on {vessel.get("station") if vessel else "that labware"}'))
        volume = row.get('volume_ul')
        if volume is not None:
            try:
                amount = Decimal(str(volume))
                if not amount.is_finite() or amount < 0 or isinstance(volume, bool):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                issues.append(_issue('deck_assignment_volume', path + '/volume_ul',
                                     f'Volume {volume!r} is not a nonnegative number'))
    trays = set()
    for index, row in enumerate(raw.get('tip_racks') if isinstance(raw.get('tip_racks'), list) else []):
        path = f'{base}/tip_racks/{index}'
        tray = row.get('tray') if isinstance(row, dict) else None
        if type(tray) is not int or tray not in TIP_TRAYS:
            issues.append(_issue('deck_tip_tray', path + '/tray', 'Tip tray must be 1-5'))
            continue
        if tray in trays:
            issues.append(_issue('deck_duplicate_id', path, f'Tip tray {tray} declared twice'))
        trays.add(tray)
        if row.get('tip_type') not in (*TIP_TYPES, None):
            issues.append(_issue('deck_tip_type', path + '/tip_type', f'Unknown tip type {row.get("tip_type")!r}'))
        tips = row.get('tips')
        if tips not in ('full', 'empty'):
            available = tips.get('available') if isinstance(tips, dict) else None
            if not isinstance(available, list) or any(well_index(w) is None for w in available):
                issues.append(_issue('deck_tip_occupancy', path + '/tips',
                                     "Tips must be 'full', 'empty' or {available: [A1..H12]}"))
    objects = raw.get('movable_objects')
    if isinstance(objects, dict):
        locations, occupied = _locations(), {}
        for name, location in objects.items():
            path = f'{base}/movable_objects/{name}'
            if name not in MOVABLE_OBJECTS:
                issues.append(_issue('deck_object_unknown', path, f'Unknown movable object {name!r}'))
            if location not in locations:
                issues.append(_issue('deck_object_location', path, f'Unknown deck location {location!r}'))
            elif location != 'UNKNOWN' and location in occupied:
                issues.append(_issue('deck_object_collision', path,
                                     f'{name} and {occupied[location]} both start at {location}'))
            occupied.setdefault(location, name)
    return issues


def tip_occupancy(rack):
    tips = rack.get('tips')
    if tips == 'full':
        return [True] * 96
    if tips == 'empty':
        return [False] * 96
    occupancy = [False] * 96
    for well in tips.get('available', []) if isinstance(tips, dict) else []:
        position = well_index(well)
        if position is not None:
            occupancy[position[0] * 12 + position[1]] = True
    return occupancy


def pickup_indices(well):
    """OEM four-channel pickup at a head reference: rows +0/+2/+4/+6 (tip_removed offsets 0,24,48,72)."""
    position = well_index(well)
    if position is None or position[0] > 1:
        return None
    start = position[0] * 12 + position[1]
    return [start + offset for offset in (0, 24, 48, 72)]


def seed_state(method, state):
    """Merge starting tip racks and object locations into the planning seed."""
    raw = method.get('deck_plan') if isinstance(method, dict) else None
    if not isinstance(raw, dict):
        return state
    for rack in raw.get('tip_racks') if isinstance(raw.get('tip_racks'), list) else []:
        if isinstance(rack, dict) and rack.get('tray') in TIP_TRAYS:
            state.setdefault('tip_racks', {}).setdefault(str(rack['tray']), {
                'station': TIP_TRAYS[rack['tray']], 'tip_type': rack.get('tip_type'),
                'occupancy': tip_occupancy(rack)})
    objects = raw.get('movable_objects')
    if isinstance(objects, dict):
        for name, location in objects.items():
            if isinstance(name, str) and isinstance(location, str):
                state.setdefault('object_locations', {}).setdefault(name, location)
    return state


def summarize_racks(racks):
    return {tray: {'station': rack.get('station'), 'tip_type': rack.get('tip_type'),
                   'tips_remaining': sum(1 for tip in rack.get('occupancy', []) if tip),
                   'four_channel_pickups_remaining': sum(
                       1 for row in range(2) for column in range(12)
                       if all(rack['occupancy'][i] for i in pickup_indices(well_name(row, column))))}
            for tray, rack in sorted(racks.items())}


def resolve_target(name, token):
    alias = TARGET_ALIASES.get(token)
    if alias is not None:
        return alias[0] if name in COVERS else alias[1]
    return token


def resolve_material_endpoint(endpoint, method, dependencies, channels, path):
    """Expand {material_id[, labware_id]} into station/location_id/wells.

    Only fills fields the author omitted; explicit fields are never replaced.
    Unresolvable references raise ValueError (addressed representation error),
    because there is no native target to emit.
    """
    if not isinstance(endpoint, dict) or 'material_id' not in endpoint:
        return endpoint
    deck = method.get('deck_plan') if isinstance(method.get('deck_plan'), dict) else {}
    material = endpoint['material_id']
    rows = [a for a in deck.get('assignments', []) if isinstance(a, dict) and a.get('material_id') == material
            and ('labware_id' not in endpoint or a.get('labware_id') == endpoint['labware_id'])]
    vessels = sorted({str(a.get('labware_id')) for a in rows})
    if len(vessels) != 1:
        raise ValueError(f'{path}: material {material!r} is on {len(vessels)} labware; name labware_id')
    vessel = next((l for l in deck.get('labware', []) if isinstance(l, dict) and l.get('id') == vessels[0]), None)
    if vessel is None:
        raise ValueError(f'{path}: labware {vessels[0]!r} is not on the deck')
    from bioxp_workflow_authoring import _NATIVE
    by_name = {name: int(key) for key, name in _NATIVE['locations'].items()}
    station = vessel.get('station')
    if station not in by_name:
        raise ValueError(f'{path}: station {station!r} has no native location')
    resolved = {**deepcopy(endpoint), 'labware_id': vessels[0]}
    resolved.setdefault('station', station)
    resolved.setdefault('location_id', by_name[station])
    if 'wells' not in resolved:
        held = {a.get('well', '').upper() for a in rows}
        profile = next((p for p in dependencies.get('labware_profiles', [])
                        if isinstance(p, dict) and p.get('id') == vessel.get('profile_id')), None)
        geometry = (profile or {}).get('native_addressing')
        if not geometry:
            raise ValueError(f'{path}: name wells explicitly; labware profile has no channel addressing')
        from bioxp_method_simulation import channel_wells
        references, covered = [], set()
        for row in range(8):
            for column in range(12):
                candidate = well_name(row, column)
                try:
                    reached = [c['well'] for c in channel_wells(candidate, channels,
                        row_increment=geometry['row_increment'], column_increment=geometry['column_increment'],
                        reference_channel=geometry['reference_channel'])]
                except ValueError:
                    continue
                if set(reached) <= held and not set(reached) & covered:
                    references.append(candidate)
                    covered |= set(reached)
        if not references:
            raise ValueError(f'{path}: material {material!r} wells do not fit the selected channels')
        resolved['wells'] = references
    resolved.pop('material_id')
    return resolved


def resolve_occurrence_materials(occurrence, method, dependencies):
    """Transfer endpoints may name a material instead of station/wells."""
    if occurrence.get('action') not in ('transfer', 'aspirate', 'dispense', 'mix'):
        return []
    inputs = occurrence.get('inputs')
    resolved = []
    if not isinstance(inputs, dict):
        return resolved
    channels = inputs.get('channels') if isinstance(inputs.get('channels'), list) else [0, 1, 2, 3]
    for key in ('source', 'destination'):
        endpoint = inputs.get(key)
        if isinstance(endpoint, dict) and 'material_id' in endpoint:
            material = endpoint['material_id']
            inputs[key] = resolve_material_endpoint(endpoint, method, dependencies, channels,
                                                    f"{occurrence.get('path', '')}/inputs/{key}")
            resolved.append({'endpoint': key, 'material_id': material,
                             'labware_id': inputs[key]['labware_id'], 'wells': deepcopy(inputs[key]['wells'])})
    return resolved

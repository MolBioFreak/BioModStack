from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from bioxp_method_compiler import compile_method
from bioxp_method_deck import check_deck, pickup_indices, tip_occupancy
from bioxp_method_model import method_catalog, method_schema
from routers.bioxp import methods

PROFILE = {'id': 'four-head', 'revision': 1,
           'native_addressing': {'row_increment': 2, 'column_increment': 0, 'reference_channel': 0}}


def deck(**extra):
    value = {
        'labware': [{'id': 'src', 'station': 'LOC_RC', 'name': 'Reagents', 'profile_id': 'four-head'},
                    {'id': 'dst', 'station': 'LOC_TC', 'name': 'Reaction', 'profile_id': 'four-head'}],
        'materials': [{'id': 'mm', 'name': 'Master mix', 'kind': 'reagent'}],
        'assignments': [{'id': f'a{w}', 'labware_id': 'src', 'well': w, 'material_id': 'mm', 'volume_ul': '100'}
                        for w in ('A1', 'C1', 'E1', 'G1')],
    }
    value.update(extra)
    return value


def method(*steps, **plan):
    return {'schema': 'bms.bioxp-method.v1', 'name': 'Deck', 'parameters': [], 'procedures': [],
            'steps': list(steps), 'deck_plan': deck(**plan)}


def transfer(source, step_id='t'):
    return {'step_id': step_id, 'type': 'action', 'action': 'transfer', 'inputs': {
        'source': source, 'destination': {'station': 'LOC_TC', 'location_id': 2, 'wells': ['A1'], 'labware_id': 'dst'},
        'channels': [0, 1, 2, 3], 'volume_ul': '10', 'aspirate_speed': '50', 'dispense_speed': '50',
        'source_position_flag': 0, 'destination_position_flag': 0,
        'source_lift_height_steps': None, 'destination_lift_height_steps': None}}


def actions(result):
    assert result['document'], result['issues']
    return [a for s in result['document']['stages'] for a in s['actions']]


def test_schema_publishes_typed_deck_and_validates_example():
    schema = method_schema()
    plan = schema['properties']['deck_plan']
    assert set(plan['properties']) == {'labware', 'materials', 'assignments', 'tip_racks', 'movable_objects', 'slots'}
    assert plan['properties']['tip_racks']['items']['properties']['tray'] == {'enum': [1, 2, 3, 4]}
    full = method(tip_racks=[{'tray': 1, 'tip_type': 'T50', 'tips': 'full'}],
                  movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})
    assert not list(Draft202012Validator(schema).iter_errors(full))


def test_check_reports_without_refusing():
    bad = method(transfer({'station': 'LOC_RC', 'location_id': 3, 'wells': ['A1']}))
    bad['deck_plan']['assignments'].append({'id': 'x', 'labware_id': 'nowhere', 'well': 'Z9',
                                            'material_id': 'ghost', 'volume_ul': '-1'})
    bad['deck_plan']['movable_objects'] = {'OUTPUT_COVER': 'LOC_OC_COVER', 'REAGENT_COVER': 'LOC_OC_COVER'}
    result = compile_method({'method': bad, 'dependencies': {'labware_profiles': [PROFILE]}})
    codes = {i['code'] for i in result['issues']}
    assert {'deck_assignment_labware', 'deck_assignment_material', 'deck_assignment_well',
            'deck_assignment_volume', 'deck_object_collision'} <= codes
    assert all(i['category'] == 'advisory' for i in result['issues'] if i['code'].startswith('deck_'))
    assert result['document'] is not None  # report primacy: no new refusal


def test_material_endpoint_resolves_to_native_wells_and_tracks_volume():
    m = method(transfer({'material_id': 'mm'}))
    result = compile_method({'method': m, 'dependencies': {'labware_profiles': [PROFILE]}})
    emitted = actions(result)
    moves = [a for a in emitted if a['kind'] == 'pipette_position' and a['params'].get('operation') == 'move']
    assert moves[0]['params']['location_id'] == 3 and moves[0]['params']['well'] == 'A1'
    vessels = result['simulation']['state']['vessels']
    assert vessels['src:A1']['volume_ul'] == '90'
    assert vessels['src:G1']['volume_ul'] == '90'
    occurrence = result['resolved']['occurrences'][0]
    assert occurrence['material_endpoints'][0]['wells'] == ['A1']
    assert m['steps'][0]['inputs']['source'] == {'material_id': 'mm'}  # raw method unchanged


def test_material_endpoint_explicit_fields_win_and_ambiguity_is_addressed():
    explicit = method(transfer({'material_id': 'mm', 'wells': ['A1'], 'station': 'LOC_RC', 'location_id': 3}))
    assert actions(compile_method({'method': explicit, 'dependencies': {'labware_profiles': [PROFILE]}}))
    two = method(transfer({'material_id': 'mm'}))
    two['deck_plan']['assignments'].append({'id': 'b', 'labware_id': 'dst', 'well': 'B1', 'material_id': 'mm', 'volume_ul': 1})
    result = compile_method({'method': two, 'dependencies': {'labware_profiles': [PROFILE]}})
    assert result['document'] is None
    assert any('name labware_id' in i['message'] and i['path'].startswith('/method/steps/0') for i in result['issues'])


def test_tip_racks_count_96_and_consume_four_per_pickup():
    assert sum(tip_occupancy({'tips': 'full'})) == 96
    assert pickup_indices('B3') == [14, 38, 62, 86]
    pickup = {'step_id': 'p', 'type': 'action', 'action': 'tip_pickup',
              'inputs': {'tray': 1, 'well': 'A1', 'overpress': False, 'lift_z': False}}
    again = {**deepcopy(pickup), 'step_id': 'p2'}
    m = method(pickup, again, tip_racks=[{'tray': 1, 'tip_type': 'T50', 'tips': {'available': ['A1', 'C1', 'E1', 'G1', 'A2']}}])
    result = compile_method({'method': m})
    summary = result['simulation']['state']['tip_rack_summary']['1']
    assert summary['tips_remaining'] == 1
    assert any(i['code'] == 'planned_tip_unavailable' for i in result['simulation']['issues'])
    full = method(pickup, tip_racks=[{'tray': 1, 'tips': 'full'}])
    assert compile_method({'method': full})['simulation']['state']['tip_rack_summary']['1'] == {
        'station': 'TECANRACK1', 'tip_type': None, 'tips_remaining': 92, 'four_channel_pickups_remaining': 23}


def test_cover_move_tracks_planned_location_with_oem_aliases():
    move = {'step_id': 'c', 'type': 'action', 'action': 'move_cover',
            'inputs': {'cover_id': 'CV_OUTPUT', 'target_location': 'LOC_OCS'}}
    m = method(move, movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})
    state = compile_method({'method': m})['simulation']['state']
    assert state['object_locations']['OUTPUT_COVER'] == 'LOC_OC_COVER_STORAGE'


def test_legacy_three_section_deck_still_clean():
    assert check_deck(method()) == []
    assert check_deck({'schema': 'bms.bioxp-method.v1', 'steps': []}) == []


def test_catalog_transfer_endpoint_accepts_material_reference():
    entry = next(a for a in method_catalog()['actions'] if a['action'] == 'transfer')
    endpoint = entry['inputs']['$defs']['TransferEndpoint']
    assert 'material_id' in endpoint['properties'] and 'anyOf' in endpoint


def test_check_route_reports_deck_issues():
    app = FastAPI()
    app.include_router(methods.router, prefix='/api/bioxp')
    m = method(transfer({'material_id': 'mm'}))
    m['deck_plan']['labware'][0]['station'] = 'TECANRACK1'
    body = TestClient(app).post('/api/bioxp/methods/check', json={'method': m}).json()
    assert any(i['code'] == 'deck_station_unknown' for i in body['issues'])


def test_deck_record_relay_forwards_record_only_body(monkeypatch):
    import asyncio
    from routers.bioxp import operator_controls
    from services.bioxp.operator_requests import OperatorDeckRecordRequestV1
    calls = []

    class Connection:
        generation = 3
        async def request_active_query(self, route, **kwargs):
            calls.append((route, kwargs))
            return {'motion_commanded': False}

    runtime = type('R', (), {'connection': Connection()})()
    request = OperatorDeckRecordRequestV1(expected_connection_generation=3, record_id='rec-cover-0001',
        reason='cover on chiller', movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})
    result = asyncio.run(operator_controls.operator_deck_record(request, runtime))
    assert result == {'motion_commanded': False}
    route, kwargs = calls[0]
    assert route == 'operator_deck_record' and kwargs['expected_generation'] == 3
    assert kwargs['json_data'] == {'schema_version': 'bioxp.operator_deck_record_request.v1',
        'record_id': 'rec-cover-0001', 'reason': 'cover on chiller',
        'movable_objects': {'OUTPUT_COVER': 'LOC_OC_COVER'}}


def test_cover_inspection_resets_covers_and_uninspected_move_is_advisory():
    inspect = {'step_id': 'i', 'type': 'action', 'action': 'inspect', 'inputs': {}}
    move = {'step_id': 'c', 'type': 'action', 'action': 'move_cover',
            'inputs': {'cover_id': 'CV_OUTPUT', 'target_location': 'LOC_OC'}}
    bare = compile_method({'method': method(move, movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})})
    codes = [i['code'] for i in bare['simulation'].get('issues', []) + bare.get('issues', [])]
    assert 'cover_move_before_inspection' in codes
    assert bare['document'] is not None  # report only
    inspected = compile_method({'method': method(inspect, move, movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})})
    codes = [i['code'] for i in inspected['simulation'].get('issues', []) + inspected.get('issues', [])]
    assert 'cover_move_before_inspection' not in codes
    assert inspected['simulation']['state']['object_locations']['OUTPUT_COVER'] == 'LOC_OC_COVER'
    assert inspected['simulation']['state']['object_locations']['REAGENT_COVER'] == 'LOC_RC_COVER_STORAGE'


def test_run_submission_carries_deck_plan_as_record_only_manifest(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from routers.bioxp import protocols
    seen = {}

    async def submit(submission, response, runtime):
        seen['manifest'] = submission.deck_manifest
        from hashlib import sha256
        return SimpleNamespace(job_id='protocol-live-' + sha256(b'deck-run-0001').hexdigest(),
                               model_dump=lambda **k: {})

    monkeypatch.setattr(protocols, 'submit_bioxp_protocol', submit)
    data = SimpleNamespace(bindings={}, dependencies={}, model_fields_set=set(), expected_generation=1,
                           idempotency_key='deck-run-0001', acknowledge_live=True)
    m = method({'step_id': 'n', 'type': 'action', 'action': 'note', 'inputs': {'message': 'x'}})
    asyncio.run(methods._submit(m, data, None, None))
    assert seen['manifest'] == m['deck_plan']


def test_job_rows_accept_robot_row_kind():
    from services.bioxp.protocol_models import ProtocolJobSummary
    row = ProtocolJobSummary(job_id='j', status='completed', dry_run=True, protocol_id='p', source_type='native',
                             created_at='a', updated_at='b', pending_review=None, row_kind='historical_bundle')
    assert row.row_kind == 'historical_bundle'


def test_deck_record_relay_forwards_gantry():
    from services.bioxp.operator_requests import OperatorDeckRecordRequestV1
    body = OperatorDeckRecordRequestV1(expected_connection_generation=1, gantry='empty')
    assert body.model_dump(exclude={'expected_connection_generation'}, exclude_none=True) == {'gantry': 'empty'}


def test_slots_default_and_cover_to_reconfigured_storage_is_advisory():
    move = {'step_id': 'c', 'type': 'action', 'action': 'move_cover',
            'inputs': {'cover_id': 'CV_OUTPUT', 'target_location': 'LOC_OCS'}}
    schema = method_schema()
    fitted = method(move, movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'},
                    slots=[{'slot': 'LOC_OC_COVER_STORAGE', 'holder': 'fluorometer', 'label': '4X DIYnaFLUOR'}])
    assert not list(Draft202012Validator(schema).iter_errors(fitted))
    result = compile_method({'method': fitted})
    codes = [i['code'] for i in result['simulation']['issues']]
    assert 'cover_slot_holder_mismatch' in codes
    assert result['document'] is not None  # advisory, never a refusal
    default = compile_method({'method': method(move, movable_objects={'OUTPUT_COVER': 'LOC_OC_COVER'})})
    assert 'cover_slot_holder_mismatch' not in [i['code'] for i in default['simulation']['issues']]
    explicit = method(move, slots=[{'slot': 'LOC_OC_COVER_STORAGE', 'holder': 'cover_storage'}])
    assert 'cover_slot_holder_mismatch' not in [i['code'] for i in compile_method({'method': explicit})['simulation']['issues']]


def test_slot_findings_and_starting_cover_in_reconfigured_slot():
    m = method(slots=[{'slot': 'LOC_RC_COVER_STORAGE', 'holder': 'tool_dock'}, {'slot': 'LOC_MS', 'holder': 'trough'},
                      {'slot': 'LOC_TROUGH', 'holder': 'spaceship'}],
               movable_objects={'REAGENT_COVER': 'LOC_RC_COVER_STORAGE'})
    codes = [i['code'] for i in check_deck(m)]
    assert {'deck_slot_unknown', 'deck_slot_holder', 'cover_slot_holder_mismatch'} <= set(codes)
    assert all(i['category'] == 'advisory' for i in check_deck(m))


def test_tray_five_retained_records_are_still_accepted():
    m = method(tip_racks=[{'tray': 5, 'tips': 'full'}])
    assert check_deck(m) == []


def test_draft_run_accepts_unsaved_method_and_freezes_exact_copy(monkeypatch):
    import asyncio
    from hashlib import sha256
    from types import SimpleNamespace
    from routers.bioxp import protocols
    seen = {}

    async def submit(submission, response, runtime):
        seen['document'] = submission.document
        return SimpleNamespace(job_id='protocol-live-' + sha256(b'unsaved-0001').hexdigest(),
                               model_dump=lambda **k: {})

    monkeypatch.setattr(protocols, 'submit_bioxp_protocol', submit)
    draft = method({'step_id': 'n', 'type': 'action', 'action': 'note', 'inputs': {'message': 'unsaved'}})
    original = deepcopy(draft)
    body = methods.QuickRun(method=draft, based_on={'id': 'tmpl-1', 'revision': 3, 'edited': True},
                            idempotency_key='unsaved-0001', expected_generation=1, acknowledge_live=True)
    reply = asyncio.run(methods.quick_run(body, None, None))
    draft['steps'].clear()  # later edits never reach the retained run copy
    snapshot = seen['document']['metadata']['bms_method_run']
    assert snapshot['method'] == original and snapshot['saved'] is None
    assert snapshot['based_on'] == {'id': 'tmpl-1', 'revision': 3, 'edited': True}
    assert reply['method_snapshot']['method'] == original

"""Backend-only receiving, inert HTTP transport and offline native publications."""
import ast
import copy
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.bioxp.calibration_models import CalibrationSettingsPatch
from services.bioxp.operator_requests import (
    OperatorDeckMoveInputsV1, OperatorXYMoveSequenceInputsV2, OperatorXYRetargetInputsV2,
)
from bioxp_calibration_route_bridge import relay_calibration

PUBLICATION = json.loads((Path(__file__).parent / 'fixtures/bioxp_bulk_holder_publication.json').read_text())
HOLDER = PUBLICATION['holder']


def cases():
    valid = [({'bulk_reagent_holder': HOLDER}, True),
             ({'positions': [], 'bulk_reagent_holder': HOLDER}, True),
             ({'positions': [{'name': 'LOC_TROUGH', 'x': 0, 'y': -1}]}, True),
             ({'positions': [{'name': 'CAMERA_OFFSET', 'zLow': -2147483648, 'inc_factor': 2147483647}], 'bulk_reagent_holder': HOLDER}, True)]
    invalid = [{}, {'positions': []}, {'positions': None}, {'bulk_reagent_holder': None},
               {'positions': [{'name': 'LOC_TROUGH', 'x': 1}], 'bulk_reagent_holder': None},
               {'positions': [{'name': 'LOC_TROUGH'}]},
               {'positions': [{'name': 'LOC_TROUGH', 'x': None}]},
               {'positions': [{'name': 'LOC_TROUGH', 'x': True}]},
               {'positions': [{'name': 'LOC_TROUGH', 'x': 2147483648}]},
               {'positions': [{'name': 'LOC_TROUGH', 'x': 0}, {'name': 'LOC_TROUGH', 'y': 0}]}]
    for field in HOLDER:
        holder = copy.deepcopy(HOLDER)
        del holder[field]
        invalid.append({'bulk_reagent_holder': holder})
    for field in ('z', 'zLow', 'zDelta', 'inc_factor'):
        holder = copy.deepcopy(HOLDER)
        holder['pcr_row_1'][field] = 1
        invalid.append({'bulk_reagent_holder': holder})
    for value in (True, '2132', 1.5, 0, -1, 2147483648, None):
        invalid.append({'bulk_reagent_holder': {**HOLDER, 'pcr_y_pitch': value}})
    for value in (True, '0', 1.5, None, -2147483649, 2147483648):
        holder = copy.deepcopy(HOLDER)
        holder['micro_row_1']['x'] = value
        invalid.append({'bulk_reagent_holder': holder})
    for row in ('pcr_row_1', 'pcr_row_2'):
        holder = copy.deepcopy(HOLDER)
        holder[row]['y'] = 2147483647 - 7 * HOLDER['pcr_y_pitch']
        valid.append(({'bulk_reagent_holder': copy.deepcopy(holder)}, True))
        holder[row]['y'] += 1
        invalid.append({'bulk_reagent_holder': holder})
    return valid + [(body, False) for body in invalid]


@pytest.mark.parametrize('body,accepted', cases())
def test_holder_acceptance_and_exact_route_payload(body, accepted):
    result = relay_calibration('patch', {**body, 'expected_connection_generation': 77})
    assert result['status'] == (200 if accepted else 422)
    assert result['robot_requests'] == ([{'method': 'PATCH', 'path': '/motion/oem/calibration_settings', 'body': body}] if accepted else [])
    if accepted:
        assert CalibrationSettingsPatch.model_validate(body).model_dump(mode='json', exclude_unset=True) == body


def test_actual_native_schema_and_get_fields(monkeypatch):
    import bioxp_calibration_route_bridge as bridge
    assert CalibrationSettingsPatch.model_json_schema() == PUBLICATION['schema']
    monkeypatch.setitem(bridge.FIXTURE, 'before', PUBLICATION['before'])
    monkeypatch.setitem(bridge.FIXTURE, 'after', PUBLICATION['after'])
    before = relay_calibration()
    saved = relay_calibration('patch', {'expected_connection_generation': 77, 'bulk_reagent_holder': HOLDER})
    after = relay_calibration(saved=True)
    assert before['data'] == PUBLICATION['before']
    assert saved['data'] == after['data'] == PUBLICATION['after']
    assert after['data']['active_bulk_reagent_holder'] == HOLDER
    assert after['data']['saved_bulk_reagent_holder'] == HOLDER
    assert after['data']['motion_commanded'] is False
    export = os.environ.get('BMS_BULK_HOLDER_ROUTE_EXPORT')
    if export:
        Path(export).write_text(json.dumps({'before': before, 'patch': saved, 'after': after}, indent=2))


@pytest.mark.parametrize('row', PUBLICATION['destinations'], ids=lambda row: row['target'])
def test_native_twenty_named_tokens_unchanged(row):
    body = {'target': row['target'], 'camera_offset': False}
    assert OperatorDeckMoveInputsV1.model_validate(body).model_dump(mode='json') == body
    assert OperatorXYRetargetInputsV2.model_validate({'target': body}).model_dump(mode='json') == {'target': body}
    assert OperatorXYMoveSequenceInputsV2.model_validate({'targets': [body]}).model_dump(mode='json') == {'targets': [body]}


def test_native_source_acceptance_equivalence():
    """Execute exact source model definitions only: no runtime/robot imports."""
    source = os.environ.get('BIOXP_NATIVE_CALIBRATION_SOURCE')
    if not source:
        pytest.skip('set BIOXP_NATIVE_CALIBRATION_SOURCE for exact native model equivalence')
    tree = ast.parse(Path(source).read_text())
    names = {'OemInt32', 'PositionName', '_position_patch_schema', 'PositionCalibrationPatch',
             'HolderXY', 'BulkReagentHolder', 'CalibrationSettingsPatch'}
    nodes = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module in ('typing', 'pydantic')
             or getattr(n, 'name', None) in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    scope = {'__name__': 'native_calibration_contract'}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), source, 'exec'), scope)
    native = scope['CalibrationSettingsPatch']
    assert native.model_json_schema() == CalibrationSettingsPatch.model_json_schema()
    for body, expected in cases():
        results = []
        for model in (native, CalibrationSettingsPatch):
            try:
                result = model.model_validate(body).model_dump(mode='json', exclude_unset=True)
            except ValidationError:
                results.append(False)
            else:
                assert result == body
                results.append(True)
        assert results == [expected, expected], body


def test_all_native_named_tokens_through_real_action_routes(monkeypatch):
    import asyncio
    from ipaddress import ip_address
    import httpx
    from services.bioxp.robot_client import BioXpRobotClient
    from services.bioxp.target_policy import ValidatedBioXpTarget
    from test_bioxp_operator_controls import make_client

    calls = []
    # This is an inert transport acknowledgement, not a native execution receipt.
    async def transport(request):
        calls.append({'method': request.method, 'path': request.url.path,
                      'body': json.loads(request.content)})
        return httpx.Response(202, json={'inert_receiving_only': True})

    target = ValidatedBioXpTarget(api_url='http://offline-fixture:8123', scheme='http',
        hostname='offline-fixture', port=8123, resolved_addresses=(ip_address('192.0.2.1'),))
    robot = BioXpRobotClient(target, transport=httpx.MockTransport(transport))
    client, runtime = make_client(monkeypatch)
    runtime.connection.client = robot
    try:
        with client:
            for row in PUBLICATION['destinations']:
                named = {'target': row['target'], 'camera_offset': False}
                for action, inputs in (
                    ('oem.deck.move_to_location', named),
                    ('oem.xy.move_sequence', {'targets': [named]}),
                    ('oem.xy.retarget', {'target': named}),
                ):
                    body = {'schema_version': 'bioxp.operator_action_request.v2',
                        'expected_connection_generation': 77, 'idempotency_key': action + '-' + row['target'],
                        'expected_ownership_generation': 7, 'expected_board_epoch_by_board': {'4': 2, '5': 3},
                        'inputs': inputs}
                    response = client.post('/api/bioxp/operator-controls/v2/actions/' + action, json=body)
                    assert response.status_code == 202, response.text
                    assert calls[-1] == {'method': 'POST', 'path': '/operator/v2/actions/' + action,
                        'body': {key: value for key, value in body.items() if key != 'expected_connection_generation'}}
            assert len(calls) == 60
    finally:
        asyncio.run(robot.close())
    export = os.environ.get('BMS_BULK_HOLDER_ROUTE_EXPORT')
    if export:
        Path(export).with_name('action-route-exports.json').write_text(json.dumps(calls, indent=2))

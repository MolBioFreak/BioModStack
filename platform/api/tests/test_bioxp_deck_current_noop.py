"""Exact current native receipts at the model and actual transport/API boundary.

BMS_NOOP_CAPTURE_ROOT selects immutable captures, outside the source tree.
No robot/service calls: only the existing guarded transport-leaf fixture.
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from routers.bioxp.operator_controls import router
from services.bioxp.operator_models import OperatorActionReceiptDetailV2, OperatorActionReceiptV2
from test_bioxp_camera_boundary import Boundary


def capture(name):
    root = os.environ.get('BMS_NOOP_CAPTURE_ROOT')
    if name == 'noop':
        fixture = Path(__file__).parent / 'fixtures/bioxp_deck_current_noop.json'
        if root:
            assert fixture.read_bytes() == (Path(root) / 'noop.json').read_bytes()
        return json.loads(fixture.read_text())
    if not root:
        pytest.skip('requires supplemental native Pool/failed capture bundle')
    return json.loads((Path(root) / (name + '.json')).read_text())


def test_exact_native_noop_model():
    raw = capture('noop')
    try:
        model = OperatorActionReceiptDetailV2.model_validate(raw)
    except ValidationError as exc:
        print('EXACT_NATIVE_VALIDATION_ERROR=' + exc.json(include_input=False))
        raise
    assert model.status == 'completed'
    assert model.source_receipt == raw['source_receipt']
    assert model.deck_movement.stages[-1].terminal_evidence == raw['deck_movement']['stages'][-1]['terminal_evidence']
    assert model.deck_movement.semantic_state_committed is True
    assert model.physical_effect_verified is False


@pytest.mark.parametrize('name,expected', [('noop', 200), ('pool', 200), ('old_failed', 502)])
def test_exact_native_receipt_router(tmp_path, name, expected):
    raw = capture(name)
    compact = {k: v for k, v in raw.items() if k in OperatorActionReceiptV2.model_fields}

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        await b.connect()
        calls = []
        async def transport(request):
            assert request.method == 'GET'
            calls.append(str(request.url))
            return httpx.Response(200, json=raw if request.url.params.get('detail') == 'true' else compact)
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as client:
            for detail in (False, True):
                response = await client.get('/operator-controls/v2/receipts/' + raw['command_id'], params={'detail': str(detail).lower()})
                assert response.status_code == (expected if detail else 200), response.text
                if response.status_code == 200:
                    result = response.json()
                    assert result['status'] == raw['status']
                    assert result['completion_class'] == raw['completion_class']
                    if detail:
                        assert result['source_receipt'] == raw['source_receipt']
                        assert result['deck_movement'] == raw['deck_movement']
                print('EXACT_ROUTER=' + json.dumps({'name': name, 'detail': detail, 'status_code': response.status_code, 'body': response.json()}))
        await b.connection.disconnect()
        assert len(calls) == 2
    asyncio.run(scenario())


# Mutations of the actual payload, not invented positive receipts. These must
# fail in both the model and the connected detailed GET response.
@pytest.mark.parametrize('path,value', [
    (('terminal',), False),
    (('status',), 'ambiguous'),
    (('status',), 'unknown'),
    (('completion_class',), 'unknown'),
    (('physical_effect_verified',), True),
    (('error',), {'code': 'action_failed', 'message': 'failed', 'retryable': False}),
    (('unexpected',), True),
    (('source_receipt',), None),
    (('deck_movement',), None),
    (('deck_movement', 'source_branch'), 'unknown'),
    (('deck_movement', 'source_branch'), 'park'),
    (('deck_movement', 'resolved_location_id'), None),
    (('deck_movement', 'target'), 'UNKNOWN'),
    (('deck_movement', 'semantic_state_committed'), False),
    (('deck_movement', 'transition_revision'), 0),
    (('deck_movement', 'ambiguity_state'), 'recovery_required'),
    (('deck_movement', 'delivery_attempted'), True),
    (('deck_movement', 'delivery_attempted'), None),
    (('deck_movement', 'delivery_attempted'), 0),
    (('deck_movement', 'controller_command_acknowledged'), True),
    (('deck_movement', 'controller_completion_verified'), True),
    (('deck_movement', 'hardware_postcondition_verified'), True),
    (('deck_movement', 'physical_observation_verified'), True),
    (('deck_movement', 'stages'), []),
    (('deck_movement', 'stages', 3, 'terminal_state'), 'failed'),
    (('source_receipt', 'command_id'), 'other'),
    (('source_receipt', 'action_id'), 'oem.x.move_steps'),
    (('source_receipt', 'effective_inputs'), {}),
    (('source_receipt', 'status'), 'failed'),
    (('source_receipt', 'completion_class'), 'controller_completed'),
    (('source_receipt', 'source_noop'), False),
    (('source_receipt', 'source_noop'), 1),
    (('source_receipt', 'source_noop_reason'), None),
    (('source_receipt', 'controller_acknowledged'), True),
    (('source_receipt', 'remote_acknowledged'), True),
    (('source_receipt', 'physical_effect_verified'), True),
    (('source_receipt', 'recovery_required'), True),
])
def test_actual_noop_rejects_contradictory_or_missing_proof(tmp_path, path, value):
    raw = capture('noop')
    cursor = raw
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        OperatorActionReceiptDetailV2.model_validate(raw)

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        await b.connect()
        async def transport(request):
            assert request.method == 'GET'
            return httpx.Response(200, json=raw)
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as client:
            response = await client.get('/operator-controls/v2/receipts/' + raw['command_id'], params={'detail': 'true'})
            assert response.status_code == 502, response.text
        await b.connection.disconnect()
    asyncio.run(scenario())

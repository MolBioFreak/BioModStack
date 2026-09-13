"""Real robot-produced manual receipts through the actual BMS proxy consumer.
Fixtures were emitted by robot testdata/tmcl/test_gripper_door_offline.py:
real moveToAbs -> gripper -> source route -> operator dispatch -> SQLite.
Only the connection's network delivery is replaced here.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from routers.bioxp.operator_controls import invoke_operator_action, operator_action_receipt
from services.bioxp.operator_models import OperatorActionInvokeRequest


@pytest.mark.parametrize('status', [200, 409])
def test_manual_producer_receipts_survive_bms_route_consumer(status):
    payload=json.loads((Path(__file__).parent/'fixtures/gripper_door'/f'gripper-{status}.json').read_text())
    calls=[]
    class Connection:
        def snapshot(self): return SimpleNamespace(generation=23)
        async def request_active(self, method, **kwargs):
            calls.append((method,kwargs))
            return payload
        async def request_active_query(self, method, **kwargs):
            calls.append((method,kwargs))
            return payload
    runtime=SimpleNamespace(connection=Connection())
    request=OperatorActionInvokeRequest(expected_connection_generation=23,
        expected_ownership_generation=7,idempotency_key=payload['idempotency_key'],inputs={})
    invoked=asyncio.run(invoke_operator_action(payload['action_id'],request,runtime))
    reopened=asyncio.run(operator_action_receipt(payload['command_id'],runtime))
    for model in (invoked,reopened):
        result=model.model_dump(mode='json')
        assert result['response']==payload['response']
        assert result['response']['http_status']==status
        assert result['physical_effect_verified'] is False
    assert calls[0][1]['json_data']['expected_generation']==7
    assert calls[0][1]['expected_generation']==23
    assert calls[0][1]['require_fresh'] is True
    assert calls[1][1]['path_params']=={'command_id':payload['command_id']}

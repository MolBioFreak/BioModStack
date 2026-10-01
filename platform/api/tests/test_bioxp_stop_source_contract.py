"""Actual offline robot/SQLite outputs through BMS; hardware was doubled."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.bioxp import operator_controls as routes

CASES = json.loads((Path(__file__).parents[2] / 'frontend/tests/fixtures/bioxp_stop_source_producer.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: f"armed-{c['armed']}-first-{c['first_ack']}")
@pytest.mark.parametrize('detail', (False, True))
@pytest.mark.parametrize('endpoint', ('operator_action_receipt_v2', 'operator_command_status_v2'))
def test_actual_stop_outputs_through_bms_http_get(case, detail, endpoint):
    calls = []
    async def query(operation, **kwargs):
        calls.append((operation, kwargs))
        return copy.deepcopy(case['captures']['detail' if kwargs['params']['detail'] else 'compact']['projected'])
    runtime = SimpleNamespace(connection=SimpleNamespace(
        generation=37, request_active_v2_query=query))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_bioxp_runtime] = lambda: runtime
    command_id = case['mutation']['command_id']
    with TestClient(app) as client:
        response = client.get(str(app.url_path_for(endpoint, command_id=command_id)),
                              params={'detail': str(detail).lower()})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'completed' and data['physical_effect_verified'] is False
    assert data['interrupt_evidence']['first_stop_acknowledged'] is case['first_ack']
    assert data['interrupt_evidence']['second_stop_acknowledged'] is True
    assert data['interrupt_evidence']['controller_terminal_state_verified'] is False
    assert calls == [(endpoint, {'expected_generation': 37,
                                'path_params': {'command_id': command_id},
                                'params': {'detail': detail}})]

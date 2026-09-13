"""Actual offline robot/SQLite outputs through BMS; hardware was doubled."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from routers.bioxp import operator_controls as routes
from services.bioxp.operator_models import (
    OperatorActionReceiptV2,
    OperatorActionReceiptDetailV2,
    OperatorHistoryReceipt,
    OperatorInterruptEvidenceV2,
    OperatorLiveActionReceipt,
)

CASES = json.loads((Path(__file__).parents[2] / 'frontend/tests/fixtures/bioxp_stop_source_producer.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: f"armed-{c['armed']}-first-{c['first_ack']}")
def test_actual_stop_outputs_preserve_source_done_without_physical_claim(case):
    for name in ('mutation', 'history'):
        model = OperatorHistoryReceipt if name == 'history' else OperatorActionReceiptV2
        parsed = model.model_validate(case[name])
        assert parsed.status == 'completed'
        assert parsed.physical_effect_verified is False
        assert parsed.error is None
    for detail in ('compact', 'detail'):
        raw = TypeAdapter(OperatorLiveActionReceipt).validate_python(case['captures'][detail]['fresh'])
        assert raw.status == 'completed'
        assert raw.model_dump()['interrupt_evidence']['source_return_ok'] is True
        model = OperatorActionReceiptDetailV2 if detail == 'detail' else OperatorActionReceiptV2
        parsed = model.model_validate(case['captures'][detail]['projected'])
        evidence = parsed.interrupt_evidence
        assert evidence is not None
        assert evidence.first_stop_acknowledged is case['first_ack']
        assert evidence.second_stop_acknowledged is True
        assert evidence.controller_stop_acknowledged is True
        assert evidence.controller_terminal_state_verified is False
        assert evidence.physical_effect_verified is False


@pytest.mark.parametrize('case', CASES, ids=lambda c: f"armed-{c['armed']}-first-{c['first_ack']}")
@pytest.mark.parametrize('detail', (False, True))
@pytest.mark.parametrize('endpoint', ('operator_action_receipt_v2', 'operator_command_status_v2'))
def test_actual_stop_outputs_through_bms_http_get(case, detail, endpoint):
    calls = []
    async def query(operation, **kwargs):
        calls.append((operation, kwargs))
        return copy.deepcopy(case['captures']['detail' if kwargs['params']['detail'] else 'compact']['projected'])
    runtime = SimpleNamespace(connection=SimpleNamespace(
        snapshot=lambda: SimpleNamespace(generation=37), request_active_v2_query=query))
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


def test_new_stop_ack_fields_remain_strict_and_do_not_enable_physical_claims():
    evidence = copy.deepcopy(CASES[0]['mutation']['interrupt_evidence'])
    for key, value in [('first_stop_acknowledged', 'true'), ('second_stop_acknowledged', 1),
                       ('physical_effect_verified', True)]:
        with pytest.raises(ValidationError):
            OperatorInterruptEvidenceV2.model_validate({**evidence, key: value})

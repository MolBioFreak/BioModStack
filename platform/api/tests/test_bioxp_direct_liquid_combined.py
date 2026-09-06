"""OFFLINE SYNTHETIC F33/F37 contracts, never release authority or hardware proof."""
import hashlib
import json
from pathlib import Path
import sys
from contextlib import asynccontextmanager
from ipaddress import ip_address
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from routers import bioxp
from services.bioxp import operator_models as models
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_bioxp_direct_liquid_idempotency import LeasedConnection

CONTRACTS = Path(__file__).parent / 'fixtures/f33_f37_combined'
MANIFEST = json.loads((CONTRACTS / 'manifest.json').read_text())
assert hashlib.sha256((CONTRACTS / 'manifest.json').read_bytes()).hexdigest() == '5cf6125b12cdd341bd83fcff4c0b9d2b00086c152b8eb9ae4f6e1680dfec575a'
CASES = [(group, item) for group in ('wire', 'strict_projections') for item in MANIFEST[group]]
assert len(MANIFEST['wire']) == 16 and len(MANIFEST['strict_projections']) == 7


def client_for(raw, item):
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(item['http_status'], content=raw, headers=item['headers'])
    robot = BioXpRobotClient(ValidatedBioXpTarget(
        api_url='http://robot:8123', scheme='http', hostname='robot', port=8123,
        resolved_addresses=(ip_address('100.64.0.10'),)), transport=httpx.MockTransport(transport))
    class Connection(LeasedConnection):
        def snapshot(self):
            raise AssertionError('no fresh generation substitution')
        @asynccontextmanager
        async def active_query_lease(self, *, expected_generation, require_fresh):
            assert expected_generation == 77
            assert isinstance(require_fresh, bool)
            yield self.client
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=Connection(robot))
    app.include_router(bioxp.router, prefix='/api/bioxp')
    return TestClient(app), requests


@pytest.mark.parametrize('group,item', CASES, ids=[i['file'] for _, i in CASES])
def test_direct_liquid_export_acceptance(group, item):
    raw = (CONTRACTS / item['file']).read_bytes()
    assert len(raw) == item['bytes']
    assert hashlib.sha256(raw).hexdigest() == item['sha256']
    payload = json.loads(raw)
    lookup = item['file'].endswith('-get.json')
    readback = item['file'].startswith(('readback', 'pending', 'unknown'))
    model = (models.PipetteDirectRequestLookupResponse if lookup else
             (models.PipetteReadbackPostEnvelope if readback else models.PipetteApplicationPlanPostEnvelope) if group == 'wire' else
             (models.PipetteReadbackResponse if readback else models.PipetteApplicationPlanResponse))
    errors = []
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        errors = [{'loc': list(e['loc']), 'type': e['type'], 'msg': e['msg']} for e in exc.errors()]
    result = {'file': item['file'], 'group': group, 'sha256': item['sha256'], 'model': model.__name__, 'errors': errors}
    if group == 'wire':
        client, requests = client_for(raw, item)
        kind = payload['request_kind'] if lookup else ('readback' if readback else 'application_plan')
        key = payload['idempotency_key'] if lookup else 'crossstack:original-post'
        with client:
            if lookup:
                response = client.get('/api/bioxp/operator-controls/pipettes/requests', params={'request_kind': kind, 'expected_connection_generation': 77}, headers={'Idempotency-Key': key})
            else:
                original_get = json.loads((CONTRACTS / item['file'].replace('-post.json', '-get.json')).read_bytes())
                body = dict(original_get['record']['requested_inputs'])
                # Robot normalization adds home_z_after for every operation;
                # BMS ingress supports it only for load_tip. Response is untouched.
                if not readback and body['operation'] != 'load_tip':
                    assert body.pop('home_z_after') is True
                suffix = 'readback' if readback else 'application/plan'
                response = client.post('/api/bioxp/operator-controls/pipettes/' + suffix, params={'expected_connection_generation': 77}, json=body, headers={'Idempotency-Key': key})
        result.update(http_status=response.status_code, response=response.json())
        if response.status_code == 200:
            if lookup:
                assert model.model_validate(response.json()) == model.model_validate(payload)
            else:
                strict = models.PipetteReadbackResponse if readback else models.PipetteApplicationPlanResponse
                expected = json.loads((CONTRACTS / (item['name'].removesuffix('-post') + '-strict-result.json')).read_bytes())
                assert strict.model_validate(response.json()) == strict.model_validate(expected)
                if readback:
                    assert payload['semantic_query_response_verified'] == payload['receipt_truth']['semantic_query_response_verified']
                    assert 'semantic_query_response_verified' not in response.json()
                    assert response.json()['hardware_truth_level'] == 'hardware_query'
        assert len(requests) == 1
        if not lookup:
            assert json.loads(requests[0].content) == body
            assert requests[0].method == 'POST'
        assert requests[0].headers['idempotency-key'] == key
        if lookup:
            assert requests[0].content == b''
            assert dict(requests[0].url.params) == {'request_kind': kind}
            assert requests[0].method == 'GET'
        print('CROSSSTACK_MATRIX ' + json.dumps(result, sort_keys=True))
        assert response.status_code == 200, result
    else:
        source = json.loads((CONTRACTS / (item['name'] + '-get.json')).read_bytes())
        assert payload == source['record']['result']
        source_item = next(i for i in MANIFEST['wire'] if i['file'] == item['name'] + '-get.json')
        client, requests = client_for((CONTRACTS / source_item['file']).read_bytes(), source_item)
        with client:
            response = client.get('/api/bioxp/operator-controls/pipettes/requests', params={'request_kind': source['request_kind'], 'expected_connection_generation': 77}, headers={'Idempotency-Key': source['idempotency_key']})
        result.update(http_status=response.status_code, response=response.json())
        if response.status_code == 200:
            assert model.model_validate(payload).model_dump(mode='json') == model.model_validate(response.json()['record']['result']).model_dump(mode='json')
        assert response.status_code == 200, result
        print('CROSSSTACK_MATRIX ' + json.dumps(result, sort_keys=True))
    assert not errors, result


@pytest.mark.parametrize('name,change', [
    ('pending', 'missing_outcome'),
    ('readback', 'missing_hardware'),
    ('readback-data', 'missing_hardware'),
    ('readback', 'missing_semantic'),
    ('readback-data', 'contradictory_semantic'),
    ('readback', 'numeric_semantic'),
])
def test_direct_liquid_fresh_explicit_counterexample(name, change):
    """Mutated negative envelopes, never relabeled exact positive exports."""
    suffix = 'get' if change in ('missing_outcome', 'missing_hardware') else 'post'
    item = next(i for i in MANIFEST['wire'] if i['file'] == name + '-' + suffix + '.json')
    payload = json.loads((CONTRACTS / item['file']).read_bytes())
    if change == 'missing_outcome':
        del payload['record']['outcome']
    elif change == 'missing_hardware':
        del payload['record']['result']['hardware_truth_level']
    elif change == 'missing_semantic':
        del payload['semantic_query_response_verified']
    elif change == 'contradictory_semantic':
        payload['semantic_query_response_verified'] = not payload['receipt_truth']['semantic_query_response_verified']
    else:
        payload['semantic_query_response_verified'] = 1
    raw = json.dumps(payload).encode()
    client, requests = client_for(raw, {'http_status': 200, 'headers': {'content-type': 'application/json'}})
    with client:
        if suffix == 'get':
            response = client.get('/api/bioxp/operator-controls/pipettes/requests', params={'request_kind': 'readback', 'expected_connection_generation': 77}, headers={'Idempotency-Key': payload['idempotency_key']})
        else:
            response = client.post('/api/bioxp/operator-controls/pipettes/readback', params={'expected_connection_generation': 77}, json={'include_data': name == 'readback-data'}, headers={'Idempotency-Key': 'offline:negative-envelope'})
    assert len(requests) == 1
    assert response.status_code == 502, response.text

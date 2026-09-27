"""Offline RGB route -> generation lease -> real robot client tests."""
import json

import httpx
import pytest

from services.bioxp.robot_client import BioXpRobotClient
from test_bioxp_camera import make_client
from test_bioxp_camera_client import target


def rgb_result(ok=True):
    return dict(ok=ok, rgb=[255, 0, 0], tmcl=[65535, 0, 0],
                acks={'r': {'status': 100, 'status_str': 'SUCCESS', 'raw': [1, 2]},
                      'g': None, 'b': {'status': 2, 'value': 0}}, sent=4, elapsed_ms=250)


def setup(monkeypatch, *, payload=None, status=200, mutations=True, change=False):
    client, connection = make_client(monkeypatch, mutations=mutations)
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path, json.loads(request.content)))
        if change:
            connection.generation += 1
        return httpx.Response(status, json=rgb_result() if payload is None else payload)
    connection.client = BioXpRobotClient(target(), transport=httpx.MockTransport(handle))
    return client, connection, calls


def post(client, **changes):
    return client.post('/api/bioxp/camera/rgb', json=dict(dict(expected_connection_generation=77, r=255, g=0, b=0), **changes))


@pytest.mark.parametrize('ok', [True, False])
def test_rgb_preserves_ack_evidence_and_false_ok_with_no_activation_or_reconnect(monkeypatch, ok):
    payload = rgb_result(ok)
    client, connection, calls = setup(monkeypatch, payload=payload)
    response = post(client)
    assert response.status_code == 200
    assert response.json() == dict(payload, connection_generation=77)
    assert connection.lease_entries == [(77, False)]
    assert calls == [('POST', '/led/rgb', dict(r=255, g=0, b=0, reconnect_first=False, activate_first=False))]


@pytest.mark.parametrize('changes', [dict(r=True), dict(g=-1), dict(b=256), dict(r='255'), dict(expected_connection_generation=True), dict(expected_generation=77), dict(reconnect_first=True), dict(activate_first=True)])
def test_rgb_strict_request_and_no_activation_knobs(monkeypatch, changes):
    client, _, calls = setup(monkeypatch)
    assert post(client, **changes).status_code == 422
    assert calls == []


@pytest.mark.parametrize('changes', [dict(ok=1), dict(rgb=[0, 0, 0]), dict(tmcl=[1, 2]), dict(acks={'r': None}), dict(sent=True), dict(elapsed_ms='2')])
def test_rgb_rejects_malformed_response(monkeypatch, changes):
    client, _, _ = setup(monkeypatch, payload=dict(rgb_result(), **changes))
    assert post(client).status_code == 502


@pytest.mark.parametrize('status', [409, 423, 503])
def test_rgb_preserves_robot_refusal(monkeypatch, status):
    client, _, calls = setup(monkeypatch, status=status, payload={'detail': 'OEM refused'})
    response = post(client)
    assert response.status_code == status
    assert 'OEM refused' in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('case,code', [('permission', 503), ('stale', 409), ('disconnected', 409), ('changed', 409)])
def test_rgb_existing_admission_and_generation_fencing(monkeypatch, case, code):
    client, connection, calls = setup(monkeypatch, mutations=case != 'permission', change=case == 'changed')
    if case == 'disconnected':
        connection.active = False
    response = post(client, expected_connection_generation=76 if case == 'stale' else 77)
    assert response.status_code == code
    assert len(calls) == (1 if case == 'changed' else 0)

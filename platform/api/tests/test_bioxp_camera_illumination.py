from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from services.bioxp.errors import RobotResponseError, RobotTransportError
from services.bioxp.robot_client import BioXpRobotClient
from test_bioxp_camera import make_client
from test_bioxp_camera_client import target


def state():
    return dict(schema_version="bioxp.camera_illumination.v1", provider_generation=1,
                channels=[dict(channel=i, on=None) for i in (1, 2, 3)],
                state_source="last_successful_command", physical_effect_verified=False)


def command(channel=1, on=True):
    result = state()
    result["channels"][channel - 1]["on"] = on
    return dict(result, ok=True, channel=channel, on=on, delivery_attempted=True)


def wire(connection, payload=None, error=None, change_generation=False):
    async def read():
        connection.client.calls.append("illumination_state")
        return state() if payload is None else payload

    async def write(*, channel, on):
        connection.client.calls.append((channel, on))
        if change_generation:
            connection.generation += 1
        if error:
            raise error
        return command(channel, on) if payload is None else payload

    connection.client.camera_illumination_state = read
    connection.client.camera_illumination = write


def test_passive_cached_state_without_mutation_permission(monkeypatch):
    client, connection = make_client(monkeypatch, mutations=False)
    wire(connection)
    response = client.get('/api/bioxp/camera/illumination/state?expected_generation=77')
    assert response.status_code == 200
    assert response.json() == dict(state(), connection_generation=77)
    assert connection.lease_entries == [(77, False)]
    assert connection.client.calls == ['illumination_state']


@pytest.mark.parametrize('channel', [1, 2, 3])
@pytest.mark.parametrize('on', [True, False])
def test_command_exact_payload_and_nonfresh_lease(monkeypatch, channel, on):
    client, connection = make_client(monkeypatch)
    wire(connection)
    response = client.post('/api/bioxp/camera/illumination', json=dict(expected_generation=77, channel=channel, on=on))
    assert response.status_code == 200
    assert response.json() == dict(command(channel, on), connection_generation=77)
    assert connection.client.calls == [(channel, on)]
    assert connection.lease_entries == [(77, False)]


@pytest.mark.parametrize('patch', [dict(channel=True), dict(channel=0), dict(channel=4), dict(channel='1'), dict(on=1), dict(on='true'), dict(expected_generation=True), dict(extra='x')])
def test_strict_request(monkeypatch, patch):
    client, connection = make_client(monkeypatch)
    wire(connection)
    response = client.post('/api/bioxp/camera/illumination', json=dict(dict(expected_generation=77, channel=1, on=True), **patch))
    assert response.status_code == 422
    assert connection.client.calls == []


@pytest.mark.parametrize('case,code', [('stale', 409), ('disconnected', 409), ('permission', 503), ('changed', 409), ('refusal', 423), ('transport', 502)])
def test_existing_refusals_and_generation_fence(monkeypatch, case, code):
    client, connection = make_client(monkeypatch, mutations=case != 'permission')
    error = RobotResponseError(status_code=423, detail={'reason': 'OEM refused'}) if case == 'refusal' else RobotTransportError('offline') if case == 'transport' else None
    wire(connection, error=error, change_generation=case == 'changed')
    if case == 'disconnected':
        connection.active = False
    response = client.post('/api/bioxp/camera/illumination', json=dict(expected_generation=76 if case == 'stale' else 77, channel=1, on=True))
    assert response.status_code == code
    if case == 'refusal':
        assert response.json()['detail'] == {'reason': 'OEM refused'}
    if case in ('stale', 'disconnected', 'permission'):
        assert connection.client.calls == []


BAD_STATES = [dict(schema_version='wrong'), dict(provider_generation=True), dict(physical_effect_verified=True),
              dict(physical_effect_verified=0), dict(state_source='sensor'), dict(extra=1),
              dict(channels=[dict(channel=i, on=None) for i in (1, 1, 3)]),
              dict(channels=[dict(channel=i, on=0) for i in (1, 2, 3)]),
              dict(channels=[dict(channel=i, on=None) for i in (True, 2, 3)])]


@pytest.mark.parametrize('patch', BAD_STATES)
def test_route_and_robot_client_reject_malformed_state(monkeypatch, patch):
    payload = dict(state(), **copy.deepcopy(patch))
    client, connection = make_client(monkeypatch)
    wire(connection, payload)
    assert client.get('/api/bioxp/camera/illumination/state?expected_generation=77').status_code == 502
    robot = BioXpRobotClient(target(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(RobotTransportError):
        asyncio.run(robot.camera_illumination_state())


@pytest.mark.parametrize('patch', [dict(ok=1), dict(delivery_attempted=False), dict(channel=2), dict(on=False)])
def test_command_response_validation(monkeypatch, patch):
    client, connection = make_client(monkeypatch)
    wire(connection, dict(command(), **patch))
    assert client.post('/api/bioxp/camera/illumination', json=dict(expected_generation=77, channel=1, on=True)).status_code == 502


def test_no_query_tuning(monkeypatch):
    client, connection = make_client(monkeypatch)
    wire(connection)
    assert client.get('/api/bioxp/camera/illumination/state?expected_generation=77&on=true').status_code == 422
    assert client.post('/api/bioxp/camera/illumination?channel=2', json=dict(expected_generation=77, channel=1, on=True)).status_code == 422
    assert connection.client.calls == []


def test_real_generation_lease_and_client_allow_lights_after_status_freshness_expires(tmp_path):
    from datetime import timedelta
    from test_bioxp_camera_boundary import Boundary
    from test_bioxp_camera_rgb import rgb_result

    async def scenario():
        boundary = Boundary(tmp_path)
        generation = await boundary.connect()
        boundary.wall += timedelta(hours=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            boundary.payload = state()
            response = await browser.get('/camera/illumination/state', params={'expected_generation': generation})
            assert response.status_code == 200, response.text
            boundary.payload = command()
            response = await browser.post('/camera/illumination', json=dict(expected_generation=generation, channel=1, on=True))
            assert response.status_code == 200, response.text
            boundary.payload = rgb_result(False)
            response = await browser.post('/camera/rgb', json=dict(expected_connection_generation=generation, r=255, g=0, b=0))
            assert response.status_code == 200, response.text
            assert response.json()['ok'] is False
        assert boundary.paths == ['/status', '/camera/illumination/state', '/camera/illumination', '/led/rgb']
        assert boundary.connection._generation_leases[generation].lease_count == 0
        await boundary.connection.disconnect()
    asyncio.run(scenario())


def test_real_robot_client_fixed_routes_and_bodies():
    seen = []
    def handle(request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json=state() if request.method == 'GET' else command(**json.loads(request.content)))
    robot = BioXpRobotClient(target(), transport=httpx.MockTransport(handle))
    async def run():
        assert await robot.camera_illumination_state() == state()
        for channel in (1, 2, 3):
            for on in (True, False):
                assert await robot.camera_illumination(channel=channel, on=on) == command(channel, on)
        await robot.close()
    asyncio.run(run())
    assert seen == [('GET', '/camera/illumination/state', None)] + [('POST', '/camera/illumination', dict(channel=c, on=o)) for c in (1, 2, 3) for o in (True, False)]

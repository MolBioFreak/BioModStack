"""RP13 actual HTTP/router/lease/client receiving; transport is strictly inert.

No new auth double: these routers inherit the deployment's existing ingress
access, and have no application credential dependency. Generation is connection
identity, not authentication. Both direct and aggregate mounts are exercised.
"""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from routers import bioxp
from routers.bioxp.operator_controls import router as direct_router
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.models import BioXpProfile
from services.bioxp.profile_store import BioXpProfileStore
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import BioXpTargetPolicy

READERS = [('settings', '/motion/oem/machine_config', 'machine-config.json'),
           ('position-table', '/motion/oem/position_table', 'position-table.json')]
BASE = '/api/bioxp/operator-controls/readers/'


@pytest_asyncio.fixture(params=['direct', 'facade'])
async def boundary(request, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_BIOXP_MUTATIONS_ENABLED', '0')
    b = SimpleNamespace(calls=[], body=None, status=200, malformed=False,
                        block=False, started=asyncio.Event(), release=asyncio.Event(), clients=[])

    async def receive(req):
        assert req.method == 'GET', 'passive readers must never cause a mutation'
        if req.url.path == '/status':
            return httpx.Response(200, json={'available': False})
        assert req.url.path in {row[1] for row in READERS}
        assert not req.content and not req.url.query
        b.calls.append(req)
        b.started.set()
        if b.block:
            await b.release.wait()
        return httpx.Response(b.status, content=b'not-json' if b.malformed else json.dumps(b.body).encode())

    async def resolve(_):
        return ('100.64.0.10',)

    def factory(target):
        client = BioXpRobotClient(target, transport=httpx.MockTransport(receive))
        b.clients.append(client)
        return client

    b.connection = BioXpConnectionService(
        BioXpProfileStore(tmp_path / 'profile.json'),
        BioXpTargetPolicy(allowed_hosts={'robot'}, allowed_cidrs={'100.64.0.0/10'}, resolver=resolve),
        client_factory=factory, initial_generation=0)
    await b.connection.save_profile(BioXpProfile(api_url='http://robot:8123'))
    b.generation = (await b.connection.connect()).generation
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=b.connection)
    app.include_router(direct_router if request.param == 'direct' else bioxp.router, prefix='/api/bioxp')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bms.test') as b.http:
        yield b
    b.release.set()
    await b.connection.close()
    assert all(client._client.is_closed for client in b.clients)


async def get(b, name, generation=None):
    return await b.http.get(BASE + name, params={'expected_connection_generation': b.generation if generation is None else generation})


@pytest.mark.asyncio
@pytest.mark.parametrize('name,native,filename', READERS)
@pytest.mark.parametrize('body', [None, {}, [], {'ok': False, 'rows': [{'id': 'raw', 'x': '-000.50', 'y': None}], 'unknown': [False, 0, ''], 'source': {'units': None}}, 'native scalar'])
async def test_unchanged_json(boundary, name, native, filename, body):
    b = boundary
    b.body = body
    response = await get(b, name)
    assert response.status_code == 200, response.text
    assert response.json() == body
    assert [req.url.path for req in b.calls] == [native]


@pytest.mark.asyncio
@pytest.mark.parametrize('name,native,filename', READERS)
async def test_generation_validation_and_mutation_authority(boundary, name, native, filename):
    b = boundary
    for params in ({}, {'expected_connection_generation': 0}, {'expected_connection_generation': 'invalid'}):
        assert (await b.http.get(BASE + name, params=params)).status_code == 422
    assert (await get(b, name, b.generation + 1)).status_code == 409
    assert not b.calls
    # No mutation dependency was moved or bypassed by adding the read routes.
    response = await b.http.post('/api/bioxp/operator-controls/actions/oem.deck.move_to_location', json={})
    assert response.status_code == 503
    assert (await b.http.post(BASE + name)).status_code == 405
    assert not b.calls
    await b.connection.disconnect()
    assert (await get(b, name)).status_code == 409
    assert not b.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('name,native,filename', READERS)
async def test_native_http_and_invalid_json_errors(boundary, name, native, filename):
    b = boundary
    b.body = {'detail': {'error': 'native-unavailable', 'ok': False, 'evidence': None}}
    for status in (404, 409, 503):
        b.status = status
        response = await get(b, name)
        assert response.status_code == status
        assert response.json() == b.body
    b.status, b.malformed = 200, True
    response = await get(b, name)
    assert response.status_code == 502
    assert response.json()['detail']['error'] == 'bioxp_robot_transport_error'
    assert len(b.calls) == 4  # no fallback collection, action or retry


@pytest.mark.asyncio
@pytest.mark.parametrize('name,native,filename', READERS)
async def test_original_client_lease_and_replacement(boundary, name, native, filename):
    b = boundary
    b.block, b.body = True, {'source': 'original', 'value': None}
    task = asyncio.create_task(get(b, name))
    await asyncio.wait_for(b.started.wait(), 1)
    old_generation = b.generation
    b.generation = (await b.connection.connect()).generation
    assert (await get(b, name, old_generation)).status_code == 409
    assert not b.clients[0]._client.is_closed
    b.release.set()
    response = await asyncio.wait_for(task, 1)
    assert response.status_code == 200 and response.json() == b.body
    await b.connection._wait_for_drains()
    assert b.clients[0]._client.is_closed
    b.body = {'source': 'replacement'}
    assert (await get(b, name)).json() == b.body
    assert len(b.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('name,native,filename', READERS)
async def test_captured_full_native_payload(boundary, name, native, filename):
    root = os.environ.get('BIOXP_REPAIR_READER_EXPORTS')
    if not root:
        pytest.skip('optional actual retained full-reader exports')
    b = boundary
    b.body = json.loads((Path(root) / filename).read_text())
    response = await get(b, name)
    assert response.status_code == 200 and response.json() == b.body
    assert [r.url.path for r in b.calls] == [native]
    if name == 'position-table':
        assert len(response.json()['rows']) == b.body['position_table_count']

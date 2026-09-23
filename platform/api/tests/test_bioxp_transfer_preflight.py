"""Offline mounted read-only relay validation; captured robot catalog, no motion."""
import hashlib
import json
from pathlib import Path

import pytest
from test_bioxp_operator_controls import make_client
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

PATH = '/api/bioxp/protocols/transfer-preflight'
CATALOG = Path(__file__).parents[2] / 'frontend/tests/fixtures/bioxp_deck_canonical_queue.json'


def reference():
    # Captured read-only robot reference/status; historical timestamps are not expiry gates.
    return json.loads((Path(__file__).parent / 'fixtures/bioxp_transfer_reference_status.json').read_text())


def setup(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['reference_status'] = reference()
    runtime.connection.client.responses['operator_control_catalog_v2'] = json.loads(CATALOG.read_text())
    return client, runtime


def test_readonly_routes_and_real_catalog_validation(monkeypatch):
    client, runtime = setup(monkeypatch)
    result = client.get(PATH, params={'expected_connection_generation': 77})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['preflight']['reference_snapshot'] == reference()
    digest = hashlib.sha256(json.dumps(reference(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert body['preflight']['artifact_refs'][-1] == f'robot:reference-snapshot:sha256:{digest}'
    assert [call[0] for call in runtime.connection.client.calls] == ['reference_status', 'operator_control_catalog_v2']
    assert DEFAULT_ROBOT_ROUTES['reference_status'] == ('GET', '/motion/reference/status', 5.0)
    assert 'physical_console_verified' not in body


def test_real_http_client_registration_fresh_reads_and_no_cache(tmp_path):
    import asyncio
    import httpx
    from test_bioxp_camera_boundary import Boundary
    from routers.bioxp.protocols import router

    async def scenario():
        boundary = Boundary(tmp_path)
        boundary.app.include_router(router)
        generation = await boundary.connect()
        paths = []
        async def transport(request):
            assert request.method == 'GET'
            paths.append(request.url.path)
            payload = {'/motion/reference/status': reference(),
                       '/operator/v2/control-catalog': json.loads(CATALOG.read_text())}[request.url.path]
            return httpx.Response(200, json=payload)
        boundary.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            for _ in range(2):
                result = await browser.get('/protocols/transfer-preflight', params={'expected_connection_generation': generation})
                assert result.status_code == 200, result.text
            assert paths == ['/motion/reference/status', '/operator/v2/control-catalog'] * 2
            boundary.connection._last_reachable = False
            boundary.connection._observed_at = None
            result = await browser.get('/protocols/transfer-preflight', params={'expected_connection_generation': generation})
            assert result.status_code == 409, result.text
            assert len(paths) == 4
        await boundary.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['absent', 'unknown', 'false', 'string', 'axis', 'revision', 'mismatch'])
def test_refuses_missing_or_mismatched_authority(monkeypatch, change):
    client, runtime = setup(monkeypatch)
    responses = runtime.connection.client.responses
    ref = responses['reference_status']
    catalog = responses['operator_control_catalog_v2']
    if change == 'absent': ref['rows'].pop('g')
    if change == 'unknown': ref['rows']['x']['state'] = 'unknown'
    if change == 'false': ref['verified'] = False
    if change == 'string': ref['durable_clean'] = 'true'
    if change == 'axis': ref['rows']['y']['axis'] = 'x'
    if change == 'revision': catalog['dashboard']['deck']['position_table_revision'] = None
    if change == 'mismatch': catalog['dashboard']['deck']['position_table_revision'] = 'a' * 64
    result = client.get(PATH, params={'expected_connection_generation': 77})
    assert result.status_code == 502, result.text
    assert 'Transfer preflight unavailable' in result.text
    assert all(DEFAULT_ROBOT_ROUTES[call[0]][0] == 'GET' for call in runtime.connection.client.calls)


@pytest.mark.parametrize('after', ['before', 'reference_status', 'operator_control_catalog_v2'])
def test_connection_drift(monkeypatch, after):
    client, runtime = setup(monkeypatch)
    original = runtime.connection.client.request
    async def request(route_name, **kwargs):
        result = await original(route_name, **kwargs)
        if route_name == after: runtime.connection.value.generation = 78
        return result
    monkeypatch.setattr(runtime.connection.client, 'request', request)
    if after == 'before': runtime.connection.value.generation = 78
    result = client.get(PATH, params={'expected_connection_generation': 77})
    assert result.status_code == 409, result.text

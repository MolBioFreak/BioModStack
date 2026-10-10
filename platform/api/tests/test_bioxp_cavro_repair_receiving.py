"""Actual repaired native exports through the production HTTP robot client."""
from copy import deepcopy
from ipaddress import ip_address
import json
import httpx
import pytest
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_methods_api import store, Connection
from test_bioxp_cavro_receiving import captured
from test_bioxp_cavro_finish import export


def connect_transport(client, receive):
    native = BioXpRobotClient(ValidatedBioXpTarget(api_url='http://robot:8123', scheme='http',
        hostname='robot', port=8123, resolved_addresses=(ip_address('100.64.0.10'),)),
        transport=httpx.MockTransport(receive))
    client._transport.app.state.bioxp_runtime.connection = Connection(native)
    return native


@pytest.mark.asyncio
async def test_actual_review_rejection_and_deliberate_fresh_host_response(store):
    rejected = captured('review-first-fail-same-key-rejected.json')
    fresh = captured('review-deliberate-fresh-attempt.json')
    client, _, _, _ = store
    calls = []
    responses = []
    def receive(request):
        calls.append(request)
        assert request.method == 'POST'
        assert request.url.path == '/protocol/jobs/' + rejected['request']['command_id'] + '/review'
        body = json.loads(request.content)
        if body['idempotency_key'] == rejected['request']['idempotency_key']:
            assert body == rejected['request']
            return httpx.Response(rejected['retry_http'], json=rejected['retry'])
        assert body == fresh['request']
        return httpx.Response(fresh['http'], json=fresh['response'])
    native = connect_transport(client, receive)
    try:
        for case, status in [(rejected, 409), (fresh, 200), (fresh, 200)]:
            response = await client.post('/api/bioxp/protocols/jobs/' + case['request']['command_id'] + '/review',
                json={**deepcopy(case['request']), 'expected_connection_generation': 77})
            assert response.status_code == status, response.text
            expected = rejected['retry'] if status == 409 else fresh['response']
            assert response.json() == expected
            responses.append({'status': status, 'body': response.json()})
        assert len(calls) == 3  # exactly authored HOST attempts; no automatic retry/submit
        assert {k: v for k, v in fresh['request'].items() if k != 'idempotency_key'} == {
            k: v for k, v in rejected['request'].items() if k != 'idempotency_key'}
        assert rejected['still_held']['execution']['runtime_state']['measurement_reviews'] == []
        assert fresh['response']['execution']['runtime_state']['measurement_reviews'][0]['measurements'] == fresh['request']['measurements']
        export('actual-review-rejection-fresh-relay', {'rejected': rejected, 'fresh': fresh, 'responses': responses})
    finally:
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('name,terminal,chunks', [
    ('finite-final-pressure.json', 'interrupted', 2),
    ('finite-final-pressure-retention-error.json', 'failed', 1)])
async def test_actual_interrupted_pressure_cold_reader_relay(store, name, terminal, chunks):
    source = captured(name)
    capture = source['fresh']['capture']
    client, _, _, _ = store
    calls = []
    def receive(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/pipettes/pressure-captures/' + capture['stream']['stream_session_id']
        return httpx.Response(200, json=capture)
    native = connect_transport(client, receive)
    try:
        response = await client.get('/api/bioxp/pipettes/pressure-captures/' + capture['stream']['stream_session_id'])
        assert response.status_code == 200, response.text
        assert response.json() == capture
        assert capture['stream']['terminal_state'] == terminal
        assert len(capture['chunks']) == chunks
        assert capture['chunks'][0]['scientific_result']['samples'][0]['raw_pressure'] == 8
        assert capture['chunks'][0]['scientific_result']['scientific_result']['interrupted'] is True
        assert len(calls) == 1
        export('actual-relay-' + name.removesuffix('.json'), {'job': source['job'], 'capture': response.json()})
    finally:
        await native.close()

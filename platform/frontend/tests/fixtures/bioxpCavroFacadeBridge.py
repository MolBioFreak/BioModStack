"""Frontend-owned JSONL bridge to real scratch Methods HTTP owners.

Run only under the original API conftest offline guard in a route-free netns.
No compiler stub, live stores, network or physical transport. Native read/review
exports, when supplied, replace only the robot HTTP leaf and remain labelled.
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile

API = Path(os.environ['BIOXP_METHOD_API_DIR']).resolve()
sys.path[:0] = [str(API / 'tests'), str(API)]
import conftest  # original repository safety policy, before any API imports
import pytest
from test_bioxp_methods_api import store
import httpx
from ipaddress import ip_address
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget


def native_leaf(transport):
    directory = Path(os.environ['BIOXP_CAVRO_TASK']) / 'native-science-finish/exports'
    load = lambda name: json.loads((directory / (name + '.json')).read_text())
    analysis = load('analysis-to-recipe')['analysis']
    pressure = load('pressure-public-receiving')['capture']
    rejected, fresh = load('review-first-fail-same-key-rejected'), load('review-deliberate-fresh-attempt')
    retained = Path(os.environ['BIOXP_CAVRO_TASK']) / 'native-receipts-finish/committed/exports'
    captures = [json.loads((retained / name).read_text())['capture'] for name in [
        'pressure-public-receiving.json', 'review-pressure-readback-None.json', 'failed-capture-ValueError.json'] ]
    state = {'review_attempt': 0, 'job': rejected['still_held']}
    geometry = []
    if directory := os.environ.get('BIOXP_GEOMETRY_PRODUCER'):
        for pattern in ('finish-*.json', 'review-*.json'):
            for path in Path(directory).glob(pattern):
                capture = json.loads(path.read_text())
                job = capture.get('job', capture.get('result'))
                if isinstance(job, dict) and 'job_id' in job:
                    geometry.append(job)
    def receive(request):
        path = request.url.path
        for job in geometry:
            if path == '/protocol/jobs/' + job['job_id']:
                assert request.method == 'GET'
                return httpx.Response(200, json=job)
        if path == '/protocol/calibration/analyze':
            assert json.loads(request.content) == analysis['requested'], 'Only exact native producer input may use this retained result'
            return httpx.Response(200, json=analysis)
        if path == '/pipettes/pressure-captures/' + pressure['stream']['stream_session_id']:
            assert request.method == 'GET'
            return httpx.Response(200, json=pressure)
        for capture in captures:
            if path == '/pipettes/pressure-captures/' + capture['stream']['stream_session_id']:
                assert request.method == 'GET'
                return httpx.Response(200, json=capture)
        if path == '/protocol/jobs/' + state['job']['job_id']:
            assert request.method == 'GET'
            return httpx.Response(200, json=state['job'])
        if path == '/protocol/jobs/' + state['job']['job_id'] + '/review':
            payload = json.loads(request.content)
            expected = fresh['request'] if state['review_attempt'] >= 2 else rejected['request']
            for key in ['command_id', 'expected_ownership_generation', 'stage_id', 'action_id', 'source_occurrence_id', 'reviewer']:
                assert payload[key] == expected[key], (key, payload)
            assert payload['measurements'] == {k: [str(v) for v in values] for k, values in expected['measurements'].items()}
            state['review_attempt'] += 1
            if state['review_attempt'] == 1:
                return httpx.Response(rejected['failed_http'], text=rejected['failed_body'])
            if state['review_attempt'] == 2:
                return httpx.Response(rejected['retry_http'], json=rejected['retry'])
            state['job'] = fresh['response']
            return httpx.Response(fresh['http'], json=fresh['response'])
        raise AssertionError('No live/unconfigured native route: ' + request.method + ' ' + path)
    native = BioXpRobotClient(ValidatedBioXpTarget(api_url='http://robot:8123', scheme='http', hostname='robot', port=8123, resolved_addresses=(ip_address('100.64.0.10'),)), transport=httpx.MockTransport(receive))
    async def request(route, **kwargs):
        transport.calls.append((route, kwargs))
        return await native.request(route, **kwargs)
    transport.request = request


async def main():
    scratch = Path(tempfile.mkdtemp(prefix='cavro-ui-', dir=os.environ['TMPDIR']))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(scratch / 'artifacts'))
    generator = store.__wrapped__(scratch, monkeypatch)
    client, transport, _, _ = await anext(generator)
    native_leaf(transport)
    if os.environ.get('BIOXP_CAVRO_REVIEW_LEASE_REFUSAL') == '1':
        from services.bioxp.errors import ConnectionStateError
        connection = client._transport.app.state.bioxp_runtime.connection
        lease = connection.active_request_lease
        refused = False
        @contextlib.asynccontextmanager
        async def refuse_once(**kwargs):
            nonlocal refused
            if not refused:
                refused = True
                raise ConnectionStateError('Injected pre-dispatch lease refusal')
            async with lease(**kwargs) as active:
                yield active
        monkeypatch.setattr(connection, 'active_request_lease', refuse_once)
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                response = await client.request(request['method'], request['url'], params=request.get('params'), json=request.get('body'))
                result = {'status': response.status_code, 'data': response.json(), 'native_calls': transport.calls}
            except Exception as error:
                result = {'status': 599, 'error': repr(error)}
            print(json.dumps({'bridge_id': request['bridge_id'], **result}), flush=True)
    finally:
        with contextlib.suppress(StopAsyncIteration):
            await anext(generator)
        monkeypatch.undo()


asyncio.run(main())

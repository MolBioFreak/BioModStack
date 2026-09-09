"""Actual HTTP decoding -> generation leases -> camera routes, no live sockets.

Producer JSON is exported from the real isolated worker with synthetic JPEGs.
Only HTTP transport, monotonic time and target DNS are synthetic here.
"""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from routers.bioxp.camera import router
from routers.bioxp.dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.models import BioXpProfile
from services.bioxp.profile_store import BioXpProfileStore
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import BioXpTargetPolicy

ROWS = json.loads((Path(__file__).parent / 'fixtures/bioxp_camera_producer.json').read_text())['rows']


class Boundary:
    def __init__(self, tmp_path):
        self.mono = 100.0
        self.wall = datetime(2026, 9, 7, tzinfo=timezone.utc)
        self.delay = 0.0
        self.payload = copy.deepcopy(ROWS[1]['status'])
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.clients = []
        self.paths = []
        self.status = {'status': 'ok', 'available': True, 'cache_state': 'fresh',
                       'freshness': {'state': 'fresh', 'age_s': 0.0, 'fresh_for_s': 15.0},
                       'snapshot_id': 'producer-snapshot-1', 'ownership_epoch': 3,
                       'runtime_ready': True, 'hardware_connected': True}
        async def resolver(_):
            return ('100.64.0.10',)
        policy = BioXpTargetPolicy(allowed_hosts={'robot'}, allowed_cidrs={'100.64.0.0/10'}, resolver=resolver)
        def factory(target):
            client = BioXpRobotClient(target, transport=httpx.MockTransport(self.transport), monotonic_clock=lambda: self.mono)
            self.clients.append(client)
            return client
        self.connection = BioXpConnectionService(BioXpProfileStore(tmp_path/'profile.json'), policy,
                                                client_factory=factory, clock=lambda: self.wall, initial_generation=0)
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=self.connection)
        self.app.dependency_overrides[require_bioxp_mutation_access] = lambda: None

    async def transport(self, request):
        self.paths.append(request.url.path)
        if request.url.path == '/status':
            return httpx.Response(200, json=self.status)
        payload = copy.deepcopy(self.payload)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        self.mono += self.delay
        self.wall -= timedelta(seconds=self.delay)  # NTP reversal cannot subtract transit
        return httpx.Response(200, json=payload)

    async def connect(self):
        await self.connection.save_profile(BioXpProfile(api_url='http://robot:8123'))
        return (await self.connection.connect()).generation

    async def get(self, generation):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://bms') as browser:
            return await browser.get('/camera/status', params={'expected_generation': generation})


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['label'])
def test_real_producer_fixture_crosses_http_strict_client_and_route(tmp_path, row):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        b.payload = row['status']
        response = await b.get(generation)
        assert response.status_code == 200, response.text
        body = response.json()
        for key, value in row['status'].items():
            if key == 'frame_captured_at' and value:
                assert datetime.fromisoformat(body[key].replace('Z', '+00:00')) == datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                assert body[key] == value
        assert body['connection_generation'] == generation
        assert b.paths == ['/status', '/camera/status']
        b.payload = copy.deepcopy(row['stream'])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as browser:
            stream = await browser.get('/camera/stream/state', params={'expected_generation':generation})
        assert stream.status_code == 200, stream.text
        for key in ('schema_version', 'state', 'active', 'stream_id', 'camera_ownership_epoch', 'frames_emitted', 'dropped_frames'):
            assert stream.json()[key] == row['stream'][key]
        assert not {'device', 'session', 'provenance', 'mjpeg_url'}.intersection(stream.json())
        assert b.connection._generation_leases[generation].lease_count == 0
        await b.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('label', ['first', 'advancing'])
def test_delayed_advancing_status_cannot_award_transit_freshness(tmp_path, label):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        await b.get(generation)
        b.payload = copy.deepcopy(next(row['status'] for row in ROWS if row['label'] == label))
        # A response completed inside the real 5s HTTP deadline can still
        # cross the producer's budget when its observation was already aged.
        b.payload['frame_age_seconds'] = b.payload['freshness_budget_seconds'] - 1.0
        b.delay = 2.0
        response = await b.get(generation)
        body = response.json()
        assert body['frame_sequence'] == b.payload['frame_sequence']
        assert body['provider_generation'] == b.payload['provider_generation']
        assert body['frame_age_seconds'] >= b.payload['frame_age_seconds'] + b.delay
        assert body['state'] == 'stale'
        await b.connection.disconnect()
    asyncio.run(scenario())


def test_repeated_identity_cannot_reset_age_but_new_frame_can(tmp_path):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        await b.get(generation)
        b.mono += 40
        b.wall -= timedelta(days=1)
        b.payload['frame_age_seconds'] = 0.0
        assert (await b.get(generation)).json()['state'] == 'stale'
        b.payload = copy.deepcopy(next(row['status'] for row in ROWS if row['label'] == 'advancing'))
        assert (await b.get(generation)).json()['state'] == 'live'
        await b.connection.disconnect()
    asyncio.run(scenario())


def test_late_status_after_disconnect_reconnect_is_rejected_and_lease_drains(tmp_path):
    async def scenario():
        b = Boundary(tmp_path)
        old = await b.connect()
        b.release = asyncio.Event()
        pending = asyncio.create_task(b.get(old))
        await b.entered.wait()
        lease = b.connection._generation_leases[old]
        assert lease.lease_count == 1
        disconnecting = asyncio.create_task(b.connection.disconnect())
        while b.connection.snapshot().generation == old:
            await asyncio.sleep(0)
        new = (await b.connection.connect()).generation
        assert new != old
        b.release.set()
        assert (await pending).status_code == 409
        await disconnecting
        assert b.clients[0]._client.is_closed
        assert lease.lease_count == 0
        assert (await b.get(new)).json()['connection_generation'] == new
        await b.connection.disconnect()
    asyncio.run(scenario())


def test_late_older_frame_response_does_not_erase_newer_identity_age_anchor(tmp_path):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        old_entered, old_release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def transport(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                old_entered.set()
                await old_release.wait()
                payload = copy.deepcopy(ROWS[1]['status'])
            else:
                payload = copy.deepcopy(ROWS[2]['status'])
            payload['frame_age_seconds'] = 0.0  # upstream wall reversal/reset witness
            return httpx.Response(200, json=payload)
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        old = asyncio.create_task(b.get(generation))
        await old_entered.wait()
        newer = await b.get(generation)
        assert newer.json()['state'] == 'live'
        b.mono += 1.0
        old_release.set()
        assert (await old).status_code == 200
        b.mono += 40.0
        repeated = await b.get(generation)
        assert repeated.json()['frame_sequence'] == newer.json()['frame_sequence']
        assert repeated.json()['state'] == 'stale'
        await b.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('route,producer_key', [('stream/start', 'stream'), ('stream/stop', 'stream'), ('snapshot', 'image')])
def test_late_camera_command_completion_cannot_bind_to_reconnected_client(tmp_path, route, producer_key):
    async def scenario():
        b = Boundary(tmp_path)
        old = await b.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def transport(request):
            calls.append(request.url.path)
            entered.set()
            await release.wait()
            if producer_key == 'image':
                import hashlib
                content = (Path(__file__).parent/'fixtures/bioxp_camera_producer.jpg').read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                return httpx.Response(200, content=content, headers={'Content-Type':'image/jpeg', 'ETag':f'"{digest}"', 'X-Content-SHA256':digest})
            return httpx.Response(200, json=ROWS[2 if route.endswith('start') else -1]['stream'])
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as browser:
            pending = asyncio.create_task(browser.post('/camera/'+route, json={'expected_generation':old}))
            await entered.wait()
            lease = b.connection._generation_leases[old]
            disconnecting = asyncio.create_task(b.connection.disconnect())
            while b.connection.snapshot().generation == old:
                await asyncio.sleep(0)
            new = (await b.connection.connect()).generation
            release.set()
            assert (await pending).status_code == 409
            await disconnecting
            assert new != old
            assert lease.lease_count == 0
            assert calls == ['/camera/'+route]  # no resubmission of remote effects
        await b.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('extra', ['device', 'camera_control', 'gain'])
def test_actual_http_status_stays_closed(tmp_path, extra):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        b.payload[extra] = 'unsupported'
        assert (await b.get(generation)).status_code == 502
        assert b.connection._generation_leases[generation].lease_count == 0
        await b.connection.disconnect()
    asyncio.run(scenario())

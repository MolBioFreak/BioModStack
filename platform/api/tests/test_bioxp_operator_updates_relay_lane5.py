"""Offline real HTTP receiving/connection/client tests; no robot transport."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import httpx
from fastapi import FastAPI
from routers.bioxp.operator_controls import router
from routers.bioxp.dependencies import get_bioxp_runtime
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.models import BioXpProfile
from services.bioxp.profile_store import BioXpProfileStore
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import BioXpTargetPolicy

PATH = '/api/bioxp/operator-controls/updates'


def envelope(sequence=10, **changes):
    # Explicit synthetic frozen producer-shaped envelope, not hardware evidence.
    return dict(schema_version='bioxp.operator_updates.v1', source_instance_id='native-A',
                ownership_generation=3, next_after_sequence=sequence, pose_sequence=2,
                changed_command_ids=[], active_command_ids=['pending-A'], has_more=False,
                reset=False, pose=None, **changes)


class UpdatesRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.inflight = 0
        self.peak = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.delay = 0
        self.body = envelope()
        self.error = None
        self.clients = []

        async def transport(request):
            if request.url.path == '/status':
                return httpx.Response(200, json={'available': False})
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.url.path, '/operator/updates')
            self.calls.append(request)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.started.set()
            try:
                if self.block:
                    await self.release.wait()
                if self.delay:
                    await asyncio.sleep(self.delay)
                if self.error:
                    return httpx.Response(self.error, json={'detail': {'error': 'native-refusal', 'ok': False}})
                return httpx.Response(200, json=copy.deepcopy(self.body))
            finally:
                self.inflight -= 1

        async def resolve(_):
            return ('100.64.0.10',)

        def factory(target):
            client = BioXpRobotClient(target, transport=httpx.MockTransport(transport))
            self.clients.append(client)
            return client

        self.service = BioXpConnectionService(
            BioXpProfileStore(Path(self.tmp.name) / 'profile.json'),
            BioXpTargetPolicy(allowed_hosts={'robot'}, allowed_cidrs={'100.64.0.0/10'}, resolver=resolve),
            client_factory=factory, initial_generation=0)
        await self.service.save_profile(BioXpProfile(api_url='http://robot:8123'))
        self.generation = (await self.service.connect()).generation
        app = FastAPI()
        app.include_router(router, prefix='/api/bioxp')
        app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=self.service)
        self.app = app
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bms.test')

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.service.close()
        self.assertEqual(self.inflight, 0)
        self.assertFalse(self.service._update_waiters)
        self.tmp.cleanup()

    async def get(self, **params):
        return await self.http.get(PATH, params={'expected_connection_generation': self.generation, **params})

    async def test_exact_wire_and_query_validation(self):
        self.body['pose'] = {'ownership_generation': 3, 'axes': [
            {'axis': 'x', 'position_steps': 0, 'observed_at': 1.5},
            {'axis': 'z', 'position_steps': -22, 'observed_at': 1.0}]}
        response = await self.get(wait_s=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.body)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(dict(self.calls[-1].url.params), {'wait_s': '0.0'})
        for params in ({'wait_s': 26}, {'after_sequence': -1}, {'after_pose_sequence': -1}, {'wait_s': 'nan'}):
            self.assertEqual((await self.get(**params)).status_code, 422)
        self.assertEqual(len(self.calls), 1)

    async def test_many_subscribers_share_one_request_and_cancel_independently(self):
        self.block = True
        requests = [asyncio.create_task(self.get(after_sequence=10, after_pose_sequence=2)) for _ in range(12)]
        await self.started.wait()
        await asyncio.sleep(.05)
        self.assertEqual(len(self.calls), 1)
        requests[0].cancel()
        await asyncio.gather(requests[0], return_exceptions=True)
        self.assertEqual(self.inflight, 1)
        self.release.set()
        results = await asyncio.gather(*requests[1:])
        self.assertTrue(all(r.json() == self.body for r in results))
        self.assertEqual(self.peak, 1)
        self.assertEqual(len(self.calls), 1)

    async def test_last_subscriber_cancel_stops_upstream_then_resume(self):
        self.block = True
        request = asyncio.create_task(self.get(after_sequence=10))
        await self.started.wait()
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        self.assertEqual(self.inflight, 0)
        self.assertIsNone(self.service._update_task)
        self.block = False
        self.assertEqual((await self.get(after_sequence=10, wait_s=0)).status_code, 200)
        self.assertEqual(self.peak, 1)

    async def test_replacement_cancels_old_client_and_fences_waiter(self):
        self.block = True
        request = asyncio.create_task(self.get(after_sequence=10))
        await self.started.wait()
        self.generation = (await self.service.connect()).generation
        response = await asyncio.wait_for(request, 1)
        self.assertEqual(response.status_code, 409)
        await self.service._wait_for_drains()
        self.assertTrue(self.clients[0]._client.is_closed)
        self.assertEqual(self.inflight, 0)
        self.block = False
        self.assertEqual((await self.get(wait_s=0)).status_code, 200)
        self.assertEqual((await self.get(expected_connection_generation=self.generation - 1)).status_code, 409)

    async def test_disconnect_does_not_wait_for_native_long_wait(self):
        self.block = True
        task = asyncio.create_task(self.get(after_sequence=10))
        await self.started.wait()
        await asyncio.wait_for(self.service.disconnect(), 1)
        self.assertEqual((await task).status_code, 409)
        self.assertTrue(self.clients[0]._client.is_closed)

    async def test_bounded_pages_and_terminal_recovery_ids_not_filtered(self):
        for seq, more, ids in [(200, True, ['failed', 'stopped', 'ambiguous']), (400, True, ['terminal-recovery']), (401, False, [])]:
            self.body.update(next_after_sequence=seq, has_more=more, changed_command_ids=ids)
            previous = 0 if seq == 200 else 200 if seq == 400 else 400
            response = await self.get(after_sequence=previous, after_pose_sequence=2, wait_s=0)
            self.assertEqual(response.json(), self.body)
            self.assertEqual(self.calls[-1].url.params['after_sequence'], str(previous))
        self.assertEqual(len(self.calls), 3)

    async def test_source_and_ownership_replacement_preserved(self):
        self.assertEqual((await self.get(wait_s=0)).json(), self.body)
        for changes in ({'source_instance_id': 'native-B', 'next_after_sequence': 0, 'reset': True},
                        {'ownership_generation': 4, 'pose_sequence': 3, 'pose': None}):
            self.body.update(changes)
            self.assertEqual((await self.get(after_sequence=10, wait_s=0)).json(), self.body)
        self.assertEqual(self.service._update_source, ('native-B', 4))

    async def test_different_cursor_shortens_wait_without_parallel_upstream(self):
        self.block = True
        old = asyncio.create_task(self.get(after_sequence=10))
        await self.started.wait()
        other = asyncio.create_task(self.get(after_sequence=1, wait_s=0))
        await asyncio.sleep(.05)
        self.block = False
        self.release.set()
        responses = await asyncio.wait_for(asyncio.gather(old, other), 1)
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(self.peak, 1)
        self.assertEqual([r.url.params['after_sequence'] for r in self.calls], ['10', '10', '1'])
        self.assertEqual(self.calls[1].url.params['wait_s'], '0.0')

    async def test_native_errors_are_not_success_and_not_retried(self):
        for status in (409, 503):
            self.error = status
            response = await self.get(wait_s=0)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {'detail': {'error': 'native-refusal', 'ok': False}})
        self.assertEqual(len(self.calls), 2)

    async def test_actual_wait_exceeds_old_query_timeout(self):
        self.delay = 25.05
        response = await self.get(after_sequence=10, wait_s=25)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0].extensions['timeout']['read'], 30)
        self.assertEqual(len(self.calls), 1)

    async def test_actual_outer_timeout_cleans_transport_without_retry(self):
        self.block = True
        response = await self.get(after_sequence=10, wait_s=0)
        self.assertEqual(response.status_code, 504)
        self.assertFalse(response.json()['detail']['robot_response_received'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.inflight, 0)

    async def test_asgi_disconnect_releases_unused_feed(self):
        self.block = True
        receive_queue = asyncio.Queue()
        await receive_queue.put({'type': 'http.request', 'body': b'', 'more_body': False})
        sent = []
        async def send(message):
            sent.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'GET', 'scheme': 'http', 'path': PATH, 'raw_path': PATH.encode(),
                 'query_string': f'expected_connection_generation={self.generation}&after_sequence=10'.encode(),
                 'headers': [], 'client': ('test', 1), 'server': ('bms.test', 80), 'root_path': ''}
        task = asyncio.create_task(self.app(scope, receive_queue.get, send))
        await self.started.wait()
        await receive_queue.put({'type': 'http.disconnect'})
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.inflight, 0)
        self.assertFalse(self.service._update_waiters)
        self.assertFalse(sent)

    async def test_observation_does_not_hold_mutation_or_interrupt_locks(self):
        self.block = True
        task = asyncio.create_task(self.get(after_sequence=10))
        await self.started.wait()
        self.assertFalse(self.service._v2_enqueue_lock.locked())
        self.assertFalse(self.service._interrupt_lock.locked())
        self.assertFalse(self.service._v1_workflow_lock.locked())
        async with self.service.active_request_lease(expected_generation=self.generation):
            self.assertEqual(self.inflight, 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_exported_native_envelopes(self):
        path = os.environ.get('BIOXP_NATIVE_UPDATE_EXPORT')
        if not path:
            self.skipTest('Set BIOXP_NATIVE_UPDATE_EXPORT to actual native producer JSON export')
        exports = json.loads(Path(path).read_text())
        for body in exports:
            self.body = body
            response = await self.get(wait_s=0)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), body)


if __name__ == '__main__':
    unittest.main()

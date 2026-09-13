"""Offline real manager/client/routes: status failure must not gate receipt GETs."""
import asyncio
import copy
from datetime import timedelta

import httpx
import pytest

from routers.bioxp.operator_controls import router
from services.bioxp.errors import ConnectionStateError
from test_bioxp_camera_boundary import Boundary
from test_bioxp_operator_controls import FakeRobotClient, v2_receipt, v2_receipt_detail


@pytest.mark.parametrize('fault', ['timeout', 'expired'])
def test_passive_v2_reconciliation_survives_status_failure_without_motion(tmp_path, fault):
    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        responses = copy.deepcopy(FakeRobotClient().responses)
        fail_status = False
        calls = []

        async def transport(request):
            calls.append((request.method, request.url.path))
            assert request.method == 'GET', 'Reconciliation must never retry a physical action'
            if request.url.path == '/status':
                if fail_status:
                    raise httpx.ReadTimeout('transient status failure', request=request)
                return httpx.Response(200, json=b.status)
            if request.url.path.startswith('/camera/'):
                return await b.transport(request)
            route = next(name for name, (_, path, _) in b.clients[0].routes.items()
                         if path.replace('{command_id}', receipt['command_id']).replace('{method_id}', 'method-1') == request.url.path)
            payload = responses[route]
            if request.url.params.get('detail') == 'true':
                payload = {**v2_receipt_detail(), **payload}
            return httpx.Response(200, json=payload)

        generation = await b.connect()
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        paths = ['/operator-controls/v2/catalog', '/operator-controls/v2/dashboard',
                 '/operator-controls/v2/receipts/xy-current', '/operator-controls/v2/commands/xy-current',
                 '/operator-controls/v2/methods/method-1',
                 '/operator-controls/v2/receipts/xy-current?detail=true',
                 '/operator-controls/v2/commands/xy-current?detail=true']
        receipt = v2_receipt(action_id='oem.xy.move_absolute', command_id='xy-current')
        responses['operator_action_receipt_v2'] = receipt
        responses['operator_command_status_v2'] = receipt
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as browser:
            # Warm the actual schema-parameterized route path before the fault.
            for path in paths:
                response = await browser.get(path)
                assert response.status_code == 200, response.text
            if fault == 'timeout':
                fail_status = True
                await b.connection._active_status_probe()
                assert b.connection.snapshot().reachable is False
            else:
                b.wall += timedelta(seconds=1801)
                assert b.connection.snapshot().observation_fresh is False
            for sequence, outcome in enumerate(['completed', 'failed', 'completed'], start=1):
                # Separate already-issued command identities, never rewrite a
                # prior terminal success into a failure or issue another action.
                receipt.update(command_id=f'xy-{sequence}', sequence=sequence,
                               status=outcome, terminal=True, finished_at=2.0,
                               completion_class='completion_timeout' if outcome == 'failed' else 'source_return')
                for path in paths:
                    response = await browser.get(path.replace('xy-current', receipt['command_id']))
                    assert response.status_code == 200, response.text
                    if '/receipts/' in path or '/commands/' in path:
                        assert response.json()['status'] == outcome
                        assert response.json()['terminal'] is True
                        assert response.json()['completion_class'] == receipt['completion_class']
                # Receipt success is not status/admission authority.
                before = len(calls)
                with pytest.raises(ConnectionStateError, match='fresh reachable'):
                    await b.connection.request_active_v2_enqueue('invoke_operator_action_v2',
                        expected_generation=generation, path_params={'action_id': 'oem.xy.move_absolute'}, json_data={})
                with pytest.raises(ConnectionStateError, match='fresh reachable'):
                    await b.connection.request_active_v2_query('invoke_operator_action_v2', expected_generation=generation)
                assert len(calls) == before
            # Independently healthy passive camera observations already survive this fault.
            camera = await browser.get('/camera/status', params={'expected_generation': generation})
            assert camera.status_code == 200, camera.text
            assert camera.json()['connection_generation'] == generation
            fail_status = False
            await b.connection._active_status_probe()
            assert b.connection.snapshot().reachable is True
            assert b.connection.snapshot().observation_fresh is True
            assert b.connection.generation == generation
            assert len(b.clients) == 1
            with pytest.raises(ConnectionStateError, match='generation'):
                await b.connection.request_active_v2_query('operator_dashboard_v2', expected_generation=generation + 1)
        await b.connection.disconnect()
        with pytest.raises(ConnectionStateError, match='not actively connected'):
            await b.connection.request_active_v2_query('operator_dashboard_v2', expected_generation=generation)
        assert all(method == 'GET' for method, _ in calls)
    asyncio.run(scenario())

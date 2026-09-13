"""Replay actual isolated robot failure receipts through the BMS boundary."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from routers.bioxp.operator_controls import router
from test_bioxp_camera_boundary import Boundary


def test_native_xy_failure_survives_strict_receipt_boundary(tmp_path):
    export = os.environ.get('XY_FAILURE_EXPORT')
    if not export:
        pytest.skip('requires isolated native XY receipt export')
    assert export is not None
    receipts = json.loads(Path(export).read_text())
    command_id = receipts['compact']['command_id']
    evidence = receipts['compact']['xy_failure']

    async def scenario():
        boundary = Boundary(tmp_path)
        boundary.app.include_router(router)
        generation = await boundary.connect()
        paths = []

        async def transport(request):
            paths.append((request.method, request.url.path))
            assert request.method == 'GET', 'Receipt reads must never retry motion'
            if request.url.path == '/status':
                raise httpx.ReadTimeout('status unavailable during native failure', request=request)
            assert request.url.path.endswith(command_id)
            name = ('legacy' if '/v2/' not in request.url.path else
                    'detail' if request.url.params.get('detail') == 'true' else 'compact')
            return httpx.Response(200, json=receipts[name])

        boundary.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        await boundary.connection._active_status_probe()
        assert boundary.connection.snapshot().reachable is False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            for detail in (False, True):
                response = await browser.get('/operator-controls/v2/receipts/' + command_id, params={'detail': str(detail).lower()})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['status'] == 'failed'
                assert result['terminal'] is True
                assert result['physical_effect_verified'] is False
                assert result['xy_failure'] == evidence
                assert result['xy_failure']['controller_failure']['ack']['status'] == 100
                assert result['xy_failure']['controller_failure']['timeout_position']['position'] == 86000
            legacy = await browser.get('/operator-controls/receipts/' + command_id)
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()['status'] == 'failed'
            assert legacy.json()['xy_failure'] == evidence
        assert boundary.connection.generation == generation
        assert boundary.connection.snapshot().reachable is False
        await boundary.connection.disconnect()
        assert all(method == 'GET' for method, _ in paths)

    asyncio.run(scenario())

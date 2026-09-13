"""Replay actual isolated robot failure receipts through the BMS boundary."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from routers.bioxp.operator_controls import router
from test_bioxp_camera_boundary import Boundary


@pytest.mark.parametrize('source', ['native', 'actual_y5'])
def test_native_xy_failure_survives_strict_receipt_boundary(tmp_path, source):
    export = os.environ.get('XY_FAILURE_EXPORT')
    if source == 'actual_y5':
        fixtures = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures'
        receipts = {name: json.loads((fixtures / f'bioxp_xy_y5_{name}.json').read_text())
                    for name in ('compact', 'detail', 'legacy')}
    else:
        if not export:
            pytest.skip('requires isolated native XY receipt export')
        assert export is not None
        receipts = json.loads(Path(export).read_text())
    command_id = receipts['compact']['command_id']
    evidence = receipts['compact']['xy_failure']
    classification = evidence['terminal_classification']
    assert classification['classification'] == 'failed_with_coherent_stopped_coordinates'
    assert classification['authority_unchanged'] is True
    for axis in ('x', 'y'):
        assert classification['readbacks'][axis]['speed']['speed'] == 0
        assert classification['readbacks'][axis]['position']['position_reply_valid'] is True

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
                assert result['status'] == receipts['compact']['status']
                assert result['completion_class'] == receipts['compact']['completion_class']
                assert result['terminal'] is True
                assert result['command_id'] == command_id
                assert result['physical_effect_verified'] is False
                assert result['xy_failure'] == evidence
                assert result['xy_failure']['controller_failure']['ack']['status'] == 100
                assert result['xy_failure']['controller_failure']['timeout_position']['position'] == (5 if source == 'actual_y5' else 86000)
                assert result['error'] == ({**receipts['compact']['error'], 'detail': None} if receipts['compact']['error'] is not None else None)
                if source == 'actual_y5':
                    assert result['error']['code'] == 'route_http_conflict'
                    assert result['xy_failure']['requested']['y'] == 0
                    assert result['xy_failure']['terminal_classification']['authority_scope'] == 'classified_terminal_epoch_not_current_admission'
            legacy = await browser.get('/operator-controls/receipts/' + command_id)
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()['status'] == receipts['compact']['status']
            assert legacy.json()['xy_failure'] == evidence
        assert boundary.connection.generation == generation
        assert boundary.connection.snapshot().reachable is False
        await boundary.connection.disconnect()
        assert all(method == 'GET' for method, _ in paths)

    asyncio.run(scenario())

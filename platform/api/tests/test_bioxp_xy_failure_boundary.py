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
                assert result['status'] == 'failed'
                assert result['terminal'] is True
                assert result['command_id'] == command_id
                assert result['physical_effect_verified'] is False
                assert result['xy_failure'] == evidence
                assert result['xy_failure']['controller_failure']['ack']['status'] == 100
                assert result['xy_failure']['controller_failure']['timeout_position']['position'] == (5 if source == 'actual_y5' else 86000)
                assert result['error'] == {**receipts['compact']['error'], 'detail': None}
                if source == 'actual_y5':
                    assert result['error']['code'] == 'route_http_conflict'
                    assert result['xy_failure']['requested']['y'] == 0
                    assert result['xy_failure']['terminal_classification']['authority_scope'] == 'classified_terminal_epoch_not_current_admission'
            legacy = await browser.get('/operator-controls/receipts/' + command_id)
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()['status'] == 'failed'
            assert legacy.json()['xy_failure'] == evidence
        assert boundary.connection.generation == generation
        assert boundary.connection.snapshot().reachable is False
        await boundary.connection.disconnect()
        assert all(method == 'GET' for method, _ in paths)

    asyncio.run(scenario())


def test_actual_y5_history_preserves_reporting_evidence(monkeypatch):
    from test_bioxp_operator_controls import make_client
    fixtures = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures'
    compact = json.loads((fixtures / 'bioxp_xy_y5_compact.json').read_text())
    history = json.loads((fixtures / 'bioxp_xy_y5_history.json').read_text())
    original = next(row for row in history['items'] if row['command_id'] == compact['command_id'])
    assert original['xy_failure'] is None  # summary intentionally omits detailed evidence
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['operator_action_history'] = history
    response = client.get('/api/bioxp/operator-controls/history', params={'limit': history['limit']})
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()['items'] if row['command_id'] == compact['command_id'])
    assert row['xy_failure'] is None
    assert row['history'] == original['history']
    assert row['status'] == 'failed'
    assert row['error'] == {**compact['error'], 'detail': None}
    assert [call[0] for call in runtime.connection.client.calls] == ['operator_action_history']


def test_native_post_failure_metadata_through_strict_routes(tmp_path):
    export = os.environ.get('XY_FAILURE_EXPORT')
    if not export:
        pytest.skip('requires isolated native XY metadata export')
    assert export is not None
    raw = json.loads(Path(export).read_text())
    assert {'catalog', 'dashboard'} <= raw.keys()

    def preserves(actual, original):
        # Strict response models may add declared optional defaults.
        if isinstance(original, dict):
            for key, value in original.items():
                preserves(actual[key], value)
        elif isinstance(original, list):
            assert len(actual) == len(original)
            for left, right in zip(actual, original):
                preserves(left, right)
        else:
            assert actual == original

    async def scenario():
        boundary = Boundary(tmp_path)
        boundary.app.include_router(router)
        await boundary.connect()  # declared connection/status transport fixture
        paths = []

        async def transport(request):
            paths.append((request.method, request.url.path))
            assert request.method == 'GET'
            name = {'/operator/v2/dashboard': 'dashboard',
                    '/operator/v2/control-catalog': 'catalog'}[request.url.path]
            return httpx.Response(200, json=raw[name])

        boundary.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        result = {name: raw[name] for name in ('compact', 'detail', 'legacy')}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            for name in ('dashboard', 'catalog'):
                response = await browser.get('/operator-controls/v2/' + name)
                assert response.status_code == 200, response.text
                result[name] = response.json()
                dashboard = result[name]['dashboard'] if name == 'catalog' else result[name]
                original = raw[name]['dashboard'] if name == 'catalog' else raw[name]
                for key in ('ownership_generation', 'generated_at', 'board4', 'y_axis', 'active_commands', 'command_queue', 'latest_receipts'):
                    preserves(dashboard[key], original[key])
            preserves(result['catalog']['actions'], raw['catalog']['actions'])
            xy = next(row for row in result['catalog']['actions'] if row['action_id'] == 'oem.xy.move_absolute')
            assert xy['enabled'] is True
            assert xy['disabled_reason'] is None
            assert result['compact']['status'] == 'failed'
        assert len(paths) == 2
        await boundary.connection.disconnect()
        if output := os.environ.get('XY_BMS_METADATA_EXPORT'):
            Path(output).write_text(json.dumps(result, indent=2) + '\n')

    asyncio.run(scenario())

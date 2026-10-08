"""Reopen a real native post-delivery failure through the BMS HTTP boundary."""
import asyncio
import json
import os
from pathlib import Path
import httpx
import pytest
from routers.bioxp.operator_controls import router
from test_bioxp_camera_boundary import Boundary


def test_native_deck_recovery_stays_readable_without_resubmission(tmp_path):
    path = os.environ.get('BMS_NATIVE_RECOVERY_EXPORT')
    if not path:
        pytest.skip('requires isolated native post-delivery failure export')
    evidence = json.loads(Path(path).read_text())
    compact, detail = evidence['compact'], evidence['detail']
    cid = compact['command_id']
    async def scenario():
        boundary = Boundary(tmp_path)
        boundary.app.include_router(router)
        await boundary.connect()
        calls = []
        async def transport(request):
            assert request.method == 'GET', 'reconciliation must not resubmit motion'
            calls.append(request.url.path)
            if request.url.path == '/status':
                raise httpx.ReadTimeout('isolated status failure', request=request)
            assert request.url.path.endswith(cid)
            return httpx.Response(200, json=detail if request.url.params.get('detail') == 'true' else compact)
        boundary.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        await boundary.connection._active_status_probe()
        assert boundary.connection.snapshot().reachable is False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as client:
            for _ in range(3):
                for verbose in (False, True):
                    response = await client.get('/operator-controls/v2/receipts/' + cid, params={'detail': str(verbose).lower()})
                    assert response.status_code == 200, response.text
                    row = response.json()
                    assert row['status'] == 'ambiguous' and row['terminal'] is True
                    assert row['completion_class'] == 'recovery_required'
                    assert row['physical_effect_verified'] is False
                    if verbose:
                        deck = row['deck_movement']
                        assert deck['controller_completion_verified'] is True
                        assert deck['semantic_state_committed'] is False
                        assert deck['ambiguity_state'] == 'recovery_required'
                        assert row['source_receipt']['terminal_evidence'] == detail['source_receipt']['terminal_evidence']
                        if output := os.environ.get('BMS_RECOVERY_DETAIL_EXPORT'):
                            Path(output).write_text(json.dumps(row, indent=2))
        await boundary.connection.disconnect()
        assert len([path for path in calls if path.endswith(cid)]) == 6
    asyncio.run(scenario())

"""Producer telemetry through actual HTTP decode, query leases and strict routes."""
import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from routers.bioxp.operator_controls import router
from services.bioxp.operator_models import OperatorDashboardV2
from test_bioxp_camera_boundary import Boundary

FIXTURES = Path(__file__).parent/'fixtures'


@pytest.mark.parametrize('label,state', [('fresh','fresh'), ('stale','stale'), ('future','missing')])
def test_producer_telemetry_http_route_preserves_observation_identity_and_skew(tmp_path, label, state):
    async def scenario():
        payload = json.loads((FIXTURES/f'bioxp_telemetry_producer_{label}.json').read_text())['payload']
        b = Boundary(tmp_path)
        b.app.include_router(router)
        generation = await b.connect()
        b.payload = copy.deepcopy(payload)
        b.delay = 2.0
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://bms') as browser:
            response = await browser.get('/operator-controls/v2/dashboard')
            assert response.status_code == 200, response.text
            parsed = OperatorDashboardV2.model_validate(response.json())
            assert parsed.telemetry is not None
            source = payload['telemetry']['snapshot']
            assert parsed.telemetry.snapshot == source
            assert parsed.telemetry.snapshot['freshness']['state'] == state
            assert parsed.telemetry.snapshot['freshness']['fresh_for_s'] == 15
            assert parsed.telemetry.snapshot['collection_triggered'] is False
            if label == 'future':
                assert parsed.telemetry.snapshot['clock_skew_detected'] is True
            assert [a.position_steps for a in parsed.telemetry.axes] == [123, 456]
            repeated = await browser.get('/operator-controls/v2/dashboard')
            assert repeated.json()['telemetry']['snapshot'] == source
            assert '/operator/v2/dashboard' in b.paths
            assert b.connection._generation_leases[generation].lease_count == 0
        await b.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('age,fresh', [(14.999, True), (15.0, True), (15.001, False)])
def test_http_hardware_evidence_budget_is_independent_of_1800s_liveness(tmp_path, age, fresh):
    async def scenario():
        b = Boundary(tmp_path)
        producer = json.loads((FIXTURES/'bioxp_hardware_producer.json').read_text())['rows'][0]['payload']
        # Actual HardwareStateOwner projection; software reachability envelope is
        # synthetic and supplies no hardware readiness or actuation authority.
        b.status = copy.deepcopy(producer)
        b.status['runtime_ready'] = True
        generation = await b.connect()
        original = b.connection.snapshot().hardware_observed_at
        b.wall += timedelta(seconds=age)
        snapshot = b.connection.snapshot()
        assert snapshot.observation_fresh is True
        assert snapshot.freshness_budget_seconds == 1800
        assert snapshot.hardware_observation_fresh is fresh
        await b.connection.probe_status_only()
        snapshot = b.connection.snapshot()
        assert snapshot.hardware_observed_at == original
        assert snapshot.hardware_observation_fresh is fresh
        assert snapshot.hardware_ready is None  # no fabricated readiness evidence
        assert b.connection._generation_leases[generation].lease_count == 0
        await b.connection.disconnect()
        assert b.connection.snapshot().hardware_observed_at is None
    asyncio.run(scenario())


def test_http_failed_probe_preserves_source_anchor_without_authority(tmp_path):
    async def scenario():
        b = Boundary(tmp_path)
        await b.connect()
        observed = b.connection.snapshot().hardware_observed_at
        async def failed(request):
            raise httpx.ReadTimeout('synthetic failed probe', request=request)
        b.clients[0]._client._transport._transport = httpx.MockTransport(failed)
        await b.connection.probe_status_only()
        snapshot = b.connection.snapshot()
        assert snapshot.hardware_observed_at == observed
        assert snapshot.hardware_observation_fresh is False
        assert snapshot.hardware_ready is None
        assert snapshot.reachable is False
        await b.connection.disconnect()
    asyncio.run(scenario())

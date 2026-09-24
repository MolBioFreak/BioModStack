"""Passive reads preserve source sample identity and expiry."""
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from services.bioxp.robot_client import BioXpRobotClient
from tests.test_bioxp_connection import _load, _service
from tests.test_bioxp_manual_readiness_schedule import payload


def test_passive_polling_cannot_renew_same_sample_or_extend_expiry(tmp_path):
    _, Profile, _, _ = _load()
    now = [datetime(2026, 9, 24, tzinfo=timezone.utc)]
    row = {**payload(0), "hardware_ready": True, "snapshot_id": "unchanged"}
    requests = []
    async def handle(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=row)
    async def scenario():
        service = _service(tmp_path, [], clock=lambda: now[0], active_probe_interval_seconds=None)
        service.client_factory = lambda target: BioXpRobotClient(target, transport=httpx.MockTransport(handle))
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        captured = service.snapshot().hardware_observed_at
        assert service.snapshot().hardware_ready is True
        try:
            for seconds in (10, 10, 11):
                now[0] += timedelta(seconds=seconds)
                await service._active_status_probe()
                assert service.snapshot().hardware_observed_at == captured
            snapshot = service.snapshot()
            assert snapshot.runtime_ready is True
            assert snapshot.hardware_ready is None
            assert snapshot.hardware_observation_stale is True
            assert snapshot.hardware_evidence_error is not None
            assert "expired" in snapshot.hardware_evidence_error
            assert requests == [("GET", "/status")] * 4
        finally:
            await service.disconnect()
    asyncio.run(scenario())

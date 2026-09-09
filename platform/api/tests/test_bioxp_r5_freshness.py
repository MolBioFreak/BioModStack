"""R5: source evidence ages independently of connection response liveness."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from test_bioxp_connection import _service, _load


def test_hardware_evidence_expires_without_another_response(tmp_path):
    _, Profile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    service = _service(tmp_path, [], clock=lambda: now)
    asyncio.run(service.save_profile(Profile(api_url="http://robot:8123")))
    asyncio.run(service.connect())
    original = service.snapshot().hardware_observed_at
    now += timedelta(seconds=31)
    snapshot = service.snapshot()
    assert snapshot.observation_fresh is True  # intentional 1800s liveness policy
    assert snapshot.hardware_observed_at == original
    assert snapshot.hardware_observation_fresh is False
    assert snapshot.hardware_ready is None
    assert snapshot.hardware_evidence_error


def test_same_snapshot_response_cannot_renew_hardware_evidence(tmp_path):
    _, Profile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    clients = []
    service = _service(tmp_path, clients, clock=lambda: now)
    asyncio.run(service.save_profile(Profile(api_url="http://robot:8123")))
    async def scenario():
        await service.connect()
        clients[0].probe_result.update(snapshot_id="snapshot-1", ownership_epoch=3)
        await service.probe_status_only()
        original = service.snapshot().hardware_observed_at
        nonlocal now
        now += timedelta(seconds=31)
        await service.probe_status_only()  # unchanged snapshot, even bogus reset age
        snapshot = service.snapshot()
        assert snapshot.observed_at == now
        assert snapshot.hardware_observed_at == original
        assert snapshot.hardware_observation_fresh is False
        clients[0].probe_result["snapshot_id"] = "snapshot-2"
        await service.probe_status_only()
        assert service.snapshot().hardware_observation_fresh is True
        await service.disconnect()
        assert service.snapshot().hardware_observed_at is None
    asyncio.run(scenario())


def test_failed_status_keeps_observation_identity_but_withholds_freshness(tmp_path):
    _, Profile, _, _ = _load()
    clients = []
    service = _service(tmp_path, clients)
    asyncio.run(service.save_profile(Profile(api_url="http://robot:8123")))
    async def scenario():
        await service.connect()
        observed = service.snapshot().hardware_observed_at
        clients[0].probe_error = RuntimeError("offline witness")
        await service.probe_status_only()
        snapshot = service.snapshot()
        assert snapshot.hardware_observed_at == observed
        assert snapshot.hardware_observation_fresh is False
        assert snapshot.hardware_ready is None
        assert snapshot.hardware_evidence_error == "offline witness"
        assert snapshot.reachable is False
    asyncio.run(scenario())


@pytest.mark.parametrize("elapsed", [31, -1])
def test_slow_response_and_local_clock_reversal_do_not_create_fresh_evidence(tmp_path, elapsed):
    _, Profile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    clients = []
    service = _service(tmp_path, clients, clock=lambda: now)
    asyncio.run(service.save_profile(Profile(api_url="http://robot:8123")))
    async def scenario():
        await service.connect()
        async def delayed_status():
            nonlocal now
            now += timedelta(seconds=elapsed)
            return clients[0].probe_result
        clients[0].probe_status_only = delayed_status
        await service.probe_status_only()
        assert service.snapshot().hardware_observation_fresh is False
        assert service.snapshot().hardware_ready is None
    asyncio.run(scenario())

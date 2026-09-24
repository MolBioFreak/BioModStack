"""The surviving periodic status monitor never owns hardware collection."""
import asyncio
import httpx
import pytest
from services.bioxp.robot_client import BioXpRobotClient
from tests.test_bioxp_connection import _load, _service


def payload(age=0, state="fresh"):
    return {"runtime_ready": True, "available": state == "fresh", "cache_state": state,
            "freshness": {"state": state, "age_s": age, "fresh_for_s": 30},
            "capabilities": ["collect_hardware_snapshot"]}


@pytest.mark.parametrize("state,age", [("missing", None), ("stale", 60), ("fresh", 15)])
def test_periodic_and_passive_display_reads_never_post_collect(tmp_path, monkeypatch, state, age):
    _, Profile, _, _ = _load()
    requests = []
    row = payload(age, state)
    async def handle(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        return httpx.Response(200, json=row)
    async def scenario():
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target, transport=httpx.MockTransport(handle))
        await service.save_profile(Profile(api_url="http://robot:8123"))
        connected = await service.connect()
        service._stop_active_probe_locked()
        await asyncio.sleep(0)
        ticks = []
        async def sleep(delay):
            ticks.append(delay)
            if len(ticks) > 4:
                raise asyncio.CancelledError
        try:
            with monkeypatch.context() as m:
                m.setattr(asyncio, "sleep", sleep)
                with pytest.raises(asyncio.CancelledError):
                    await service._active_probe_loop()
            for _ in range(4):
                for route in ("operator_control_catalog_v2", "operator_dashboard_v2"):
                    assert await service.request_active_v2_query(route, expected_generation=connected.generation) == row
            assert ticks == [10] * 5
            assert requests.count(("GET", "/status")) == 5
            assert {path for _, path in requests} == {"/status", "/operator/v2/control-catalog", "/operator/v2/dashboard"}
            assert service.snapshot().runtime_ready is True
            if state != "fresh":
                assert service.snapshot().hardware_ready is None
                assert service.snapshot().hardware_observation_fresh is not True
            assert service.snapshot().automatic_snapshot_refresh is None
            assert not hasattr(service, "_snapshot_refresh_task")
        finally:
            await service.disconnect()
        assert service._active_probe_task is None
    asyncio.run(scenario())

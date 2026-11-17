"""One automatic hardware read per new connection; the periodic monitor never collects again."""
import asyncio
import json
import httpx
import pytest
from services.bioxp.robot_client import BioXpRobotClient
from tests.test_bioxp_connection import _load, _service


def payload(age=0, state="fresh"):
    return {"runtime_ready": True, "available": state == "fresh", "cache_state": state,
            "freshness": {"state": state, "age_s": age, "fresh_for_s": 30},
            "capabilities": ["collect_hardware_snapshot"]}


@pytest.mark.parametrize("state,age", [("missing", None), ("stale", 60), ("fresh", 15)])
def test_only_the_connect_time_read_posts_collect(tmp_path, monkeypatch, state, age):
    _, Profile, _, _ = _load()
    requests, ticks = [], []
    row = payload(age, state)
    collect = ("POST", "/hardware/snapshot/collect")
    async def handle(request):
        requests.append((request.method, request.url.path))
        if requests[-1] == collect:
            assert json.loads(request.content) == {"automatic": True}
            assert requests.count(collect) == 1 and ticks == []
        else:
            assert request.method == "GET"
        return httpx.Response(200, json=row)
    async def scenario():
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target, transport=httpx.MockTransport(handle))
        await service.save_profile(Profile(api_url="http://robot:8123"))
        connected = await service.connect()
        service._stop_active_probe_locked()
        await asyncio.sleep(0)
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
            # connect, the read-back after the connect-time collect, then four periodic probes
            assert requests.count(("GET", "/status")) == 6
            assert requests.count(collect) == 1
            assert {path for _, path in requests} == {"/status", "/hardware/snapshot/collect", "/operator/v2/control-catalog", "/operator/v2/dashboard"}
            assert service.snapshot().runtime_ready is True
            if state != "fresh":
                assert service.snapshot().hardware_ready is None
                assert service.snapshot().hardware_observation_fresh is not True
            assert "automatic_snapshot_refresh" not in service.snapshot().model_fields
            assert not hasattr(service, "_snapshot_refresh_task")
        finally:
            await service.disconnect()
        assert service._active_probe_task is None
    asyncio.run(scenario())

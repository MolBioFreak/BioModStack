"""Explicit collection and controller submission survive passive-only observation."""
import asyncio
import httpx
import pytest
from services.bioxp.errors import RobotResponseError
from services.bioxp.robot_client import BioXpRobotClient
from tests.test_bioxp_connection import _load, _service
from tests.test_bioxp_manual_readiness_schedule import payload


@pytest.mark.parametrize("state,age", [("missing", None), ("stale", 60)])
def test_explicit_collection_does_not_block_commands_or_independent_stop(tmp_path, state, age):
    _, Profile, _, _ = _load()
    requests = []
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handle(request):
            requests.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, json=payload(age, state))
            if request.url.path == "/hardware/snapshot/collect":
                entered.set()
                await release.wait()
                return httpx.Response(200, json={"ok": True, "published": True, "snapshot": {"snapshot_id": "explicit"}})
            if "denied" in request.url.path:
                return httpx.Response(409, json={"detail": "controller denied"})
            return httpx.Response(200, json={"accepted": True})
        service = _service(tmp_path, [], active_probe_interval_seconds=None)
        service.client_factory = lambda target: BioXpRobotClient(target, transport=httpx.MockTransport(handle))
        await service.save_profile(Profile(api_url="http://robot:8123"))
        connected = await service.connect()
        pending = asyncio.create_task(service.request_active("collect_hardware_snapshot",
            expected_generation=connected.generation, json_data={}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert service.snapshot().hardware_ready is None
            async def submit(action):
                return await service.request_active_v2_enqueue("invoke_operator_action_v2",
                    expected_generation=connected.generation, path_params={"action_id": action},
                    json_data={"inputs": {}, "idempotency_key": action, "expected_generation": 7})
            assert await asyncio.wait_for(submit("oem.y.move_steps"), 1) == {"accepted": True}
            stopped = await asyncio.wait_for(service.request_active_safety_interrupt(
                "interrupt_operator_action_v1", expected_generation=connected.generation,
                path_params={"action_id": "oem.y.stop"}, json_data={"inputs": {}}), 1)
            assert stopped == {"accepted": True}
            with pytest.raises(RobotResponseError):
                await submit("denied")
            assert not pending.done()
            release.set()
            assert (await pending)["published"] is True
            await service._active_status_probe()
            assert service.snapshot().hardware_ready is None  # no fabricated readback
            assert requests.count(("POST", "/hardware/snapshot/collect")) == 1
        finally:
            release.set()
            await pending
            await service.disconnect()
    asyncio.run(scenario())

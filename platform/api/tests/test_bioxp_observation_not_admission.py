"""Offline robot-boundary regressions: observations are not host interlocks."""
import asyncio
from datetime import timedelta

import pytest

from test_bioxp_connection import _load, _service
from services.bioxp.errors import ConnectionStateError, RobotResponseError


@pytest.mark.parametrize("observation", ["missing", "aged", "unreachable"])
def test_real_connection_dispatch_preserves_robot_denial_and_target_fence(tmp_path, observation):
    _, Profile, _, _ = _load()
    clients = []
    service = _service(tmp_path, clients)

    async def scenario():
        await service.save_profile(Profile(api_url="http://robot:8123"))
        generation = (await service.connect()).generation
        if observation == "missing":
            service._observed_at = None
        elif observation == "aged":
            assert service._observed_at is not None
            service._observed_at -= timedelta(days=1)
        else:
            service._last_reachable = False
        before = (clients[0].probes, clients[0].status_only_probes)
        request = {"schema_version": "bioxp.operator_action_request.v2",
                   "expected_ownership_generation": 1, "expected_board_epoch_by_board": {},
                   "idempotency_key": "offline-observation-request",
                   "inputs": {"target": "LOC_OC", "camera_offset": False}}
        calls = []

        async def robot(route, **kwargs):
            calls.append((route, kwargs))
            raise RobotResponseError(409, {"error": "controller_denied", "reason": "door open"})

        clients[0].request = robot
        try:
            with pytest.raises(RobotResponseError) as denied:
                await service.request_active_v2_enqueue("invoke_operator_action_v2",
                    expected_generation=generation, json_data=request,
                    path_params={"action_id": "oem.deck.move_to_location"})
            assert denied.value.status_code == 409
            assert len(calls) == 1
            assert calls[0][1]["json_data"] == request
            with pytest.raises(ConnectionStateError):
                await service.request_active_v2_enqueue("invoke_operator_action_v2",
                    expected_generation=generation + 1, json_data=request)
            assert len(calls) == 1
            assert (clients[0].probes, clients[0].status_only_probes) == before
        finally:
            await service.close()
    asyncio.run(scenario())


def test_first_terminal_receipt_invalidates_only_once_without_extra_robot_reads(tmp_path):
    _, Profile, _, _ = _load()
    clients = []
    service = _service(tmp_path, clients)

    async def scenario():
        await service.save_profile(Profile(api_url="http://robot:8123"))
        generation = (await service.connect()).generation
        terminal = False
        calls = []

        async def robot(route, **kwargs):
            calls.append(route)
            if route == "operator_action_receipt_v2":
                return {"command_id": "offline-terminal", "terminal": terminal}
            return {"observed": len(calls)}

        clients[0].request = robot
        async def catalog():
            return await service.request_active_v2_query("operator_control_catalog_v2", expected_generation=generation)
        async def receipt():
            return await service.request_active_v2_query("operator_action_receipt_v2", expected_generation=generation,
                path_params={"command_id": "offline-terminal"})
        try:
            first = await catalog()
            await receipt()
            assert await catalog() == first
            terminal = True
            await receipt()
            second = await catalog()
            assert second != first
            await receipt()
            assert await catalog() == second
            assert calls == ["operator_control_catalog_v2", "operator_action_receipt_v2",
                             "operator_action_receipt_v2", "operator_control_catalog_v2",
                             "operator_action_receipt_v2"]
        finally:
            await service.close()
    asyncio.run(scenario())

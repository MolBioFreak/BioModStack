from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import cast

from services.bioxp.command_coordinator import CommandCoordinator
from services.bioxp.command_models import parse_command_request
from services.bioxp.command_policy import CommandAdmissionContext, evaluate_command
from services.bioxp.command_registry import CommandName, DEFAULT_COMMAND_REGISTRY
from services.bioxp.models import BioXpSnapshot


def _request(command: str, **extra):
    return parse_command_request({
        "command": command,
        "expected_generation": 41,
        "idempotency_key": f"compact-{command}-41",
        **extra,
    })


def test_compact_oem_commands_do_not_require_bms_readiness_or_maintenance_policy() -> None:
    requests = {
        "recover_motion_non_homing": _request("recover_motion_non_homing"),
        "run_axis_diagnostic": _request("run_axis_diagnostic", axis="x", operation="home"),
        "stop_axis_diagnostic": _request("stop_axis_diagnostic", axis="x"),
    }

    for name, request in requests.items():
        definition = DEFAULT_COMMAND_REGISTRY[cast(CommandName, name)]
        assert definition.requires_fresh_observation is False
        assert definition.requires_runtime_ready is False
        assert definition.requires_hardware_ready is False
        assert definition.maintenance_policy == "independent"
        decision = evaluate_command(
            request,
            definition,
            CommandAdmissionContext(
                mutations_enabled=True,
                active=True,
                generation=41,
                observation_fresh=None,
                runtime_ready=None,
                hardware_ready=None,
                capabilities=frozenset({name}),
                startup_lifecycle=None,
                maintenance_state=None,
            ),
        )
        assert decision.allowed, decision.reasons


def test_compact_oem_requests_need_no_operator_ack_or_reason_and_robot_payload_is_fixed() -> None:
    requests = [
        _request("recover_motion_non_homing"),
        _request("run_axis_diagnostic", axis="x", operation="move-positive"),
        _request("stop_axis_diagnostic", axis="x"),
    ]
    assert all("operator_ack" not in request.model_fields_set for request in requests)
    assert all("reason" not in request.model_fields_set for request in requests)

    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def request(self, route_key, *, json_data):
            self.calls.append((route_key, json_data))
            if route_key == "collect_hardware_snapshot":
                return {"ok": True, "published": False}
            return {"ok": True, "acknowledged": True}

    class Connection:
        active_client = Client()

        @asynccontextmanager
        async def workflow_lease(self, expected_generation):
            yield self.active_client

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=41,
                reachable=True,
                runtime_ready=None,
                hardware_ready=None,
                hardware_observation_fresh=None,
                capabilities=(
                    "recover_motion_non_homing",
                    "run_axis_diagnostic",
                    "stop_axis_diagnostic",
                ),
                observed_at=None,
                freshness_budget_seconds=30.0,
                observation_fresh=None,
                maintenance_state=None,
            )

        def observe_command_response(self, value):
            return None

    async def scenario() -> list[tuple[str, object]]:
        connection = Connection()
        coordinator = CommandCoordinator(connection, DEFAULT_COMMAND_REGISTRY)
        for request in requests:
            await coordinator.execute(request, mutations_enabled=True)
        return connection.active_client.calls

    calls = asyncio.run(scenario())
    assert calls[0] == (
        "recover_motion_non_homing",
        {
            "run_homing": False,
            "operator_ack": "RECOVER_MOTION",
            "operator_reason": "BMS operator requested controller initialization",
        },
    )
    assert calls[1] == (
        "run_axis_diagnostic",
        {
            "axis": "x",
            "operation": "move-positive",
            "operator_ack": "RUN_AXIS_DIAGNOSTIC",
            "reason": "BMS operator requested x move-positive",
        },
    )
    assert calls[-1] == (
        "stop_axis_diagnostic",
        {
            "axis": "x",
            "operator_ack": "STOP_AXIS",
            "reason": "BMS operator requested x stop",
        },
    )

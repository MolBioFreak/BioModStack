from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import cast

import pytest

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


def _context(
    *capabilities: str,
    ownership: dict | None,
    maintenance_state: dict | None,
    fresh: bool | None = True,
) -> CommandAdmissionContext:
    return CommandAdmissionContext(
        mutations_enabled=True,
        active=True,
        generation=41,
        observation_fresh=fresh,
        runtime_ready=None,
        hardware_ready=None,
        capabilities=frozenset(capabilities),
        startup_lifecycle=None,
        maintenance_state=maintenance_state,
        ownership=ownership,
    )


def test_compact_oem_commands_follow_robot_ownership_and_motion_latch() -> None:
    activate = _request("activate_usb_for_service")
    recovery = _request("recover_motion_non_homing")
    move = _request("run_axis_diagnostic", axis="x", operation="home")
    stop = _request("stop_axis_diagnostic", axis="x")

    unbound = {"transport": "unbound", "usb": "unbound", "router": "unbound"}
    owned = {"transport": "owned", "usb": "service", "router": "running"}
    blocked = {
        "motion_blocked": True,
        "recovery_required": True,
        "block_reason": "Service startup requires explicit non-homing motion recovery.",
    }
    unblocked = {"motion_blocked": False, "recovery_required": False}

    assert evaluate_command(
        activate,
        DEFAULT_COMMAND_REGISTRY["activate_usb_for_service"],
        _context(ownership=unbound, maintenance_state=blocked),
    ).allowed
    assert not evaluate_command(
        recovery,
        DEFAULT_COMMAND_REGISTRY["recover_motion_non_homing"],
        _context("recover_motion_non_homing", ownership=unbound, maintenance_state=blocked),
    ).allowed
    assert evaluate_command(
        recovery,
        DEFAULT_COMMAND_REGISTRY["recover_motion_non_homing"],
        _context("recover_motion_non_homing", ownership=owned, maintenance_state=blocked),
    ).allowed
    move_while_blocked = evaluate_command(
        move,
        DEFAULT_COMMAND_REGISTRY["run_axis_diagnostic"],
        _context("run_axis_diagnostic", ownership=owned, maintenance_state=blocked),
    )
    assert not move_while_blocked.allowed
    assert "Service startup requires explicit non-homing motion recovery" in "; ".join(move_while_blocked.reasons)
    move_while_unbound = evaluate_command(
        move,
        DEFAULT_COMMAND_REGISTRY["run_axis_diagnostic"],
        _context("run_axis_diagnostic", ownership=unbound, maintenance_state=unblocked),
    )
    assert not move_while_unbound.allowed
    assert "not service-owned and running" in "; ".join(move_while_unbound.reasons)
    assert evaluate_command(
        move,
        DEFAULT_COMMAND_REGISTRY["run_axis_diagnostic"],
        _context("run_axis_diagnostic", ownership=owned, maintenance_state=unblocked),
    ).allowed
    assert not evaluate_command(
        move,
        DEFAULT_COMMAND_REGISTRY["run_axis_diagnostic"],
        _context("run_axis_diagnostic", ownership=owned, maintenance_state=unblocked, fresh=False),
    ).allowed
    assert evaluate_command(
        stop,
        DEFAULT_COMMAND_REGISTRY["stop_axis_diagnostic"],
        _context("stop_axis_diagnostic", ownership=None, maintenance_state=None, fresh=None),
    ).allowed

    assert DEFAULT_COMMAND_REGISTRY["activate_usb_for_service"].route_key == "activate_usb_for_service"
    assert DEFAULT_COMMAND_REGISTRY["run_axis_diagnostic"].maintenance_policy == "motion_unblocked"
    assert DEFAULT_COMMAND_REGISTRY["recover_motion_non_homing"].maintenance_policy == "recovery_required"


@pytest.mark.parametrize(
    "ownership",
    [
        {"transport": "unbound", "router": "unbound"},
        {"transport": "unbound", "usb": "service", "router": "unbound"},
        {"transport": "released", "usb": "owned-by-other", "router": "stopped"},
        {"transport": "unbound", "usb": None, "router": "stopped"},
    ],
)
def test_transport_claim_rejects_noncanonical_ownership(ownership) -> None:
    decision = evaluate_command(
        _request("activate_usb_for_service"),
        DEFAULT_COMMAND_REGISTRY["activate_usb_for_service"],
        _context(
            ownership=ownership,
            maintenance_state={"motion_blocked": True, "recovery_required": True},
        ),
    )

    assert not decision.allowed
    assert "explicitly fully unbound" in "; ".join(decision.reasons)


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
            if route_key == "recover_motion_non_homing":
                return {
                    "ok": True,
                    "acknowledged": True,
                    "ownership": {"transport": "owned", "usb": "service", "router": "running"},
                    "maintenance_state": {"motion_blocked": False, "recovery_required": False},
                }
            return {"ok": True, "acknowledged": True}

    class Connection:
        active_client = Client()

        def __init__(self) -> None:
            self.ownership = {"transport": "owned", "usb": "service", "router": "running"}
            self.maintenance_state = {
                "motion_blocked": True,
                "recovery_required": True,
                "block_reason": "Non-homing recovery required",
            }

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
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                maintenance_state=self.maintenance_state,
                ownership=self.ownership,
            )

        def observe_command_response(self, value):
            if isinstance(value, dict):
                if isinstance(value.get("maintenance_state"), dict):
                    self.maintenance_state = value["maintenance_state"]
                if isinstance(value.get("ownership"), dict):
                    self.ownership = value["ownership"]

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

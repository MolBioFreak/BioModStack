from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest


RETIRED_COMMANDS = (
    "activate_usb_for_service",
    "initialize_oem_environment",
    "run_oem_motor_stage",
    "record_oem_motor_stage_observation",
)
ROBOT_CONTRACT_UNAVAILABLE = (
    "robot-contract-unavailable: unsupported by the exact robot runtime contract"
)


def _payloads() -> tuple[dict[str, object], ...]:
    return (
        {
            "command": "activate_usb_for_service",
            "expected_generation": 17,
            "idempotency_key": "retired-activate",
        },
        {
            "command": "initialize_oem_environment",
            "expected_generation": 17,
            "idempotency_key": "retired-initialize",
            "mode": "live",
            "operator_ack": "INITIALIZE",
        },
        {
            "command": "run_oem_motor_stage",
            "expected_generation": 17,
            "idempotency_key": "retired-stage",
            "stage": "z-home",
            "mode": "live",
            "operator_ack": "HOME",
        },
        {
            "command": "record_oem_motor_stage_observation",
            "expected_generation": 17,
            "idempotency_key": "retired-observation",
            "stage": "z-home",
            "observed_pass": True,
            "operator_ack": "OBSERVE",
            "operator_note": "Legacy observation retained only for parsing compatibility.",
        },
    )


def test_unsupported_robot_command_families_are_disabled_with_no_route() -> None:
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    for command in RETIRED_COMMANDS:
        definition = DEFAULT_COMMAND_REGISTRY[command]
        assert definition.enabled is False
        assert definition.route_key is None
        assert definition.required_capability is None
        assert definition.disabled_reason == ROBOT_CONTRACT_UNAVAILABLE

    for command in (
        "collect_hardware_snapshot",
        "collect_axis_diagnostics",
        "run_axis_diagnostic",
        "stop_axis_diagnostic",
        "recover_motion_non_homing",
    ):
        definition = DEFAULT_COMMAND_REGISTRY[command]
        assert definition.enabled is True
        assert definition.route_key == command


def test_default_robot_client_has_no_alias_for_retired_command_families() -> None:
    from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

    for command in RETIRED_COMMANDS:
        assert command not in DEFAULT_ROBOT_ROUTES
    for command in (
        "collect_hardware_snapshot",
        "collect_axis_diagnostics",
        "run_axis_diagnostic",
        "stop_axis_diagnostic",
        "recover_motion_non_homing",
    ):
        assert command in DEFAULT_ROBOT_ROUTES


def test_status_never_advertises_retired_commands_even_if_robot_claims_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers.bioxp.connection import get_status
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    snapshot = BioXpSnapshot(
        configured=True,
        active=True,
        generation=17,
        reachable=True,
        runtime_ready=True,
        hardware_ready=True,
        hardware_observed_at=datetime.now(timezone.utc),
        hardware_observation_fresh=True,
        capabilities=tuple(RETIRED_COMMANDS) + (
            "collect_hardware_snapshot",
            "collect_axis_diagnostics",
            "run_axis_diagnostic",
            "stop_axis_diagnostic",
            "recover_motion_non_homing",
        ),
        observed_at=datetime.now(timezone.utc),
        freshness_budget_seconds=30.0,
        observation_fresh=True,
        maintenance_state={"motion_blocked": False, "recovery_required": False},
    )
    runtime = SimpleNamespace(
        connection=SimpleNamespace(snapshot=lambda: snapshot),
        commands=SimpleNamespace(registry=DEFAULT_COMMAND_REGISTRY),
        startup_warnings=(),
        legacy_jobs=SimpleNamespace(model_dump=lambda: {}),
    )

    status = asyncio.run(get_status(cast(Any, runtime)))

    for command in RETIRED_COMMANDS:
        assert command not in status["available_commands"]
        assert status["unavailable_commands"][command] == ROBOT_CONTRACT_UNAVAILABLE
    for command in (
        "collect_hardware_snapshot",
        "collect_axis_diagnostics",
        "run_axis_diagnostic",
        "stop_axis_diagnostic",
    ):
        assert command in status["available_commands"]


def test_retired_legacy_payloads_are_rejected_before_transport() -> None:
    from services.bioxp.command_coordinator import CommandCoordinator, CommandDeniedError
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    class ExplodingClient:
        async def request(self, *_: object, **__: object) -> object:
            raise AssertionError("retired command must never reach robot transport")

    class Connection:
        active_client = ExplodingClient()

        def snapshot(self) -> BioXpSnapshot:
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=17,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                capabilities=tuple(RETIRED_COMMANDS),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                startup_lifecycle={
                    "stages": {
                        "constructor_pipette_stage": {"state": "not_run"},
                        "initialization_without_motion": {"state": "blocked"},
                        "initial_check": {"state": "passed"},
                    }
                },
                maintenance_state={"motion_blocked": False, "recovery_required": False},
            )

    coordinator = CommandCoordinator(Connection(), DEFAULT_COMMAND_REGISTRY)

    async def scenario() -> None:
        for payload in _payloads():
            request = parse_command_request(payload)
            with pytest.raises(CommandDeniedError, match="robot-contract-unavailable") as exc_info:
                await coordinator.execute(request, mutations_enabled=True)
            assert exc_info.value.reasons == (ROBOT_CONTRACT_UNAVAILABLE,)

    asyncio.run(scenario())

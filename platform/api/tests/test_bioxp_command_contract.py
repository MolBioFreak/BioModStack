from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


def _load():
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    return parse_command_request, DEFAULT_COMMAND_REGISTRY


def test_command_request_is_discriminated_and_rejects_unknown_names_or_parameters() -> None:
    parse, _ = _load()
    valid = parse(
        {
            "command": "initialize_motors",
            "expected_generation": 3,
            "idempotency_key": "init-3",
        }
    )
    assert valid.command == "initialize_motors"

    staged = parse(
        {
            "command": "run_oem_motor_stage",
            "expected_generation": 3,
            "idempotency_key": "m01-z-home-3",
            "stage": "z-home",
            "mode": "live",
            "operator_ack": "HOME",
        }
    )
    assert staged.stage == "z-home"
    assert staged.operator_ack == "HOME"

    observed = parse(
        {
            "command": "record_oem_motor_stage_observation",
            "expected_generation": 3,
            "idempotency_key": "m01-observed-3",
            "stage": "z-home",
            "observed_pass": True,
            "operator_ack": "OBSERVE",
            "operator_note": "Z reached the physical reference without collision.",
        }
    )
    assert observed.stage == "z-home"
    assert observed.observed_pass is True

    observation_base = {
        "command": "record_oem_motor_stage_observation",
        "expected_generation": 3,
        "idempotency_key": "m01-observed-invalid",
        "stage": "z-home",
        "observed_pass": True,
        "operator_ack": "OBSERVE",
        "operator_note": "Observed physical completion.",
    }
    for invalid_pass in ("true", "false", "yes", "no", 0, 1):
        with pytest.raises(ValidationError):
            parse({**observation_base, "observed_pass": invalid_pass})
    for invalid_note in (None, "", "   "):
        with pytest.raises(ValidationError):
            parse({**observation_base, "operator_note": invalid_note})
    missing_note = dict(observation_base)
    missing_note.pop("operator_note")
    with pytest.raises(ValidationError):
        parse(missing_note)

    with pytest.raises(ValidationError):
        parse(
            {
                "command": "initialize_motors",
                "expected_generation": 3,
                "idempotency_key": "unknown-stage-3",
                "stage": "raw-axis-jog",
                "mode": "live",
                "operator_ack": "HOME",
            }
        )

    with pytest.raises(ValidationError):
        parse({"command": "arbitrary_path", "expected_generation": 3, "idempotency_key": "x"})
    with pytest.raises(ValidationError):
        parse(
            {
                "command": "initialize_motors",
                "expected_generation": 3,
                "idempotency_key": "x",
                "path": "/motion/axis/relative",
            }
        )


def test_queue_receipts_are_not_misclassified_as_completed_handler_acknowledgements() -> None:
    from services.bioxp.command_coordinator import _strict_acknowledgement

    assert _strict_acknowledgement({"ok": True, "queued": True}) is True
    assert _strict_acknowledgement({"ok": True, "queued": False}) is False
    assert _strict_acknowledgement({"ok": True}) is True
    assert _strict_acknowledgement({"ok": False, "queued": True}) is False
    assert _strict_acknowledgement({"queued": True}) is False
    assert _strict_acknowledgement({"ok": "false", "queued": True}) is False
    assert _strict_acknowledgement({"ok": 0, "queued": True}) is False


def test_default_registry_exposes_only_current_compact_commissioning_mappings() -> None:
    _, registry = _load()

    assert set(registry) == {
        "activate_usb_for_service",
        "collect_hardware_snapshot",
        "initialize_oem_environment",
        "initialize_motors",
        "run_oem_motor_stage",
        "record_oem_motor_stage_observation",
        "start_job",
        "pause_job",
        "resume_job",
        "stop_job",
        "recover_runtime",
    }
    enabled = {
        "activate_usb_for_service": "activate_usb_for_service",
        "collect_hardware_snapshot": "collect_hardware_snapshot",
        "initialize_oem_environment": "initialize_oem_environment",
        "run_oem_motor_stage": "run_oem_motor_stage",
        "record_oem_motor_stage_observation": "record_oem_motor_stage_observation",
    }
    for name, route_key in enabled.items():
        assert registry[name].enabled is True
        assert registry[name].route_key == route_key
        expected_capability = (
            None if name == "activate_usb_for_service"
            else "run_oem_motor_stage" if name == "record_oem_motor_stage_observation"
            else name
        )
        assert registry[name].required_capability == expected_capability

    assert registry["initialize_oem_environment"].requires_hardware_ready is True
    assert registry["run_oem_motor_stage"].requires_hardware_ready is True
    assert registry["record_oem_motor_stage_observation"].requires_hardware_ready is False
    assert registry["record_oem_motor_stage_observation"].required_capability == "run_oem_motor_stage"
    assert registry["initialize_oem_environment"].required_lifecycle_states == (
        ("constructor_pipette_stage", "not_run"),
        ("initialization_without_motion", "blocked"),
        ("initial_check", "blocked"),
    )
    assert registry["activate_usb_for_service"].required_capability is None
    assert registry["activate_usb_for_service"].requires_runtime_inactive is True

    for name in set(registry) - set(enabled):
        assert registry[name].enabled is False
        assert registry[name].route_key is None
        assert "online contract" in registry[name].disabled_reason.lower()


def test_current_commissioning_command_payloads_are_typed_and_oem_startup_requires_ack() -> None:
    parse, _ = _load()

    for name in (
        "activate_usb_for_service",
        "collect_hardware_snapshot",
    ):
        request = parse({"command": name, "expected_generation": 3, "idempotency_key": f"{name}-3"})
        assert request.command == name

    live = parse({
        "command": "initialize_oem_environment",
        "expected_generation": 3,
        "idempotency_key": "oem-startup-3",
        "mode": "live",
        "operator_ack": "INITIALIZE",
    })
    assert live.command == "initialize_oem_environment"

    with pytest.raises(ValidationError):
        parse({
            "command": "initialize_oem_environment",
            "expected_generation": 3,
            "idempotency_key": "oem-startup-bad",
            "mode": "live",
            "operator_ack": "YES",
        })


def test_oem_motor_stage_requires_advertised_capability_and_translates_to_queued_robot_envelope() -> None:
    from services.bioxp.command_coordinator import CommandCoordinator, CommandDeniedError
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    class Client:
        def __init__(self, response=None) -> None:
            self.calls = []
            self.response = response or {"ok": True, "queued": True}

        async def request(self, route_key, *, json_data):
            self.calls.append((route_key, json_data))
            return self.response

    class Connection:
        def __init__(self, capabilities, response=None):
            self.active_client = Client(response)
            self.capabilities = capabilities
            self.observed = None

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=7,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                hardware_observed_at=datetime.now(timezone.utc),
                hardware_observation_fresh=True,
                capabilities=self.capabilities,
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                startup_lifecycle={"stages": {"initial_check": {"state": "passed"}}},
            )

        def observe_command_response(self, value):
            self.observed = value

    request = parse_command_request({
        "command": "run_oem_motor_stage",
        "expected_generation": 7,
        "idempotency_key": "m01-cross-contract",
        "stage": "z-home",
        "mode": "live",
        "operator_ack": "HOME",
    })

    async def scenario():
        missing = Connection(("collect_hardware_snapshot",))
        with pytest.raises(CommandDeniedError, match="Required capability is unavailable"):
            await CommandCoordinator(missing, DEFAULT_COMMAND_REGISTRY).execute(request, mutations_enabled=True)
        assert missing.active_client.calls == []

        admitted = Connection(("collect_hardware_snapshot", "run_oem_motor_stage"))
        record = await CommandCoordinator(admitted, DEFAULT_COMMAND_REGISTRY).execute(request, mutations_enabled=True)
        assert record.status == "queued"
        assert record.remote_acknowledged is True
        assert record.physical_effect_verified is False
        assert admitted.active_client.calls == [(
            "run_oem_motor_stage",
            {
                "name": "startupHomingStepwise",
                "mode": "live",
                "operator_ack": "HOME",
                "params": {"homing_step": "z-home"},
            },
        )]

        for malformed_receipt in (
            {"queued": True},
            {"ok": "false", "queued": True},
            {"ok": 0, "queued": True},
        ):
            malformed = Connection(
                ("collect_hardware_snapshot", "run_oem_motor_stage"),
                malformed_receipt,
            )
            rejected = await CommandCoordinator(
                malformed,
                DEFAULT_COMMAND_REGISTRY,
            ).execute(request, mutations_enabled=True)
            assert rejected.status == "delivery_failed"
            assert rejected.remote_acknowledged is False
            assert rejected.physical_effect_verified is False

    asyncio.run(scenario())


def test_oem_motor_stage_observation_is_typed_non_motion_and_uses_the_same_robot_queue() -> None:
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    class Client:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, route_key, *, json_data):
            self.calls.append((route_key, json_data))
            return {"ok": True, "queued": True, "command_id": "robot-observation-queue-id"}

    class Connection:
        def __init__(self) -> None:
            self.active_client = Client()

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=9,
                reachable=True,
                runtime_ready=True,
                hardware_ready=None,
                hardware_observation_fresh=False,
                hardware_observation_stale=True,
                capabilities=("run_oem_motor_stage",),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                startup_lifecycle={"stages": {"initial_check": {"state": "passed"}}},
            )

        def observe_command_response(self, value):
            pass

    request = parse_command_request({
        "command": "record_oem_motor_stage_observation",
        "expected_generation": 9,
        "idempotency_key": "m01-observe-pass",
        "stage": "z-home",
        "observed_pass": True,
        "operator_ack": "OBSERVE",
        "operator_note": "Observed Z reference complete.",
    })

    async def scenario():
        connection = Connection()
        record = await CommandCoordinator(connection, DEFAULT_COMMAND_REGISTRY).execute(request, mutations_enabled=True)
        assert record.status == "queued"
        assert record.remote_acknowledged is True
        assert record.physical_effect_verified is False
        assert connection.active_client.calls == [(
            "record_oem_motor_stage_observation",
            {
                "name": "startupHomingStepwise",
                "mode": "live",
                "operator_ack": "OBSERVE",
                "params": {
                    "homing_step": "z-home",
                    "record_stage_observation": True,
                    "observed_pass": True,
                    "operator_note": "Observed Z reference complete.",
                },
            },
        )]

    asyncio.run(scenario())

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
        "collect_axis_diagnostics",
        "run_axis_diagnostic",
        "stop_axis_diagnostic",
        "recover_motion_non_homing",
        "start_job",
        "pause_job",
        "resume_job",
        "stop_job",
        "recover_runtime",
    }
    enabled = {
        "collect_hardware_snapshot": "collect_hardware_snapshot",
        "collect_axis_diagnostics": "collect_axis_diagnostics",
        "run_axis_diagnostic": "run_axis_diagnostic",
        "stop_axis_diagnostic": "stop_axis_diagnostic",
        "recover_motion_non_homing": "recover_motion_non_homing",
    }
    for name, route_key in enabled.items():
        assert registry[name].enabled is True
        assert registry[name].route_key == route_key
        assert registry[name].required_capability == name

    activation = registry["activate_usb_for_service"]
    assert activation.enabled is True
    assert activation.route_key == "activate_usb_for_service"
    assert activation.required_capability is None
    assert activation.ownership_policy == "unbound"

    retired = {
        "initialize_oem_environment",
        "run_oem_motor_stage",
        "record_oem_motor_stage_observation",
    }
    for name in retired:
        assert registry[name].enabled is False
        assert registry[name].route_key is None
        assert registry[name].disabled_reason == (
            "robot-contract-unavailable: unsupported by the exact robot runtime contract"
        )

    for name in set(registry) - set(enabled) - retired - {"activate_usb_for_service"}:
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


def test_legacy_oem_motor_stage_translation_remains_isolated_from_the_default_registry() -> None:
    from dataclasses import replace

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
                maintenance_state={"motion_blocked": False, "recovery_required": False},
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
    legacy_registry = dict(DEFAULT_COMMAND_REGISTRY)
    legacy_registry["run_oem_motor_stage"] = replace(
        legacy_registry["run_oem_motor_stage"],
        enabled=True,
        route_key="run_oem_motor_stage",
        required_capability="run_oem_motor_stage",
        disabled_reason="",
    )

    async def scenario():
        missing = Connection(("collect_hardware_snapshot",))
        with pytest.raises(CommandDeniedError, match="Required capability is unavailable"):
            await CommandCoordinator(missing, legacy_registry).execute(request, mutations_enabled=True)
        assert missing.active_client.calls == []

        admitted = Connection(("collect_hardware_snapshot", "run_oem_motor_stage"))
        record = await CommandCoordinator(admitted, legacy_registry).execute(request, mutations_enabled=True)
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
                legacy_registry,
            ).execute(request, mutations_enabled=True)
            assert rejected.status == "delivery_failed"
            assert rejected.remote_acknowledged is False
            assert rejected.physical_effect_verified is False

    asyncio.run(scenario())


def test_legacy_oem_stage_observation_translation_remains_isolated_from_the_default_registry() -> None:
    from dataclasses import replace

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
    legacy_registry = dict(DEFAULT_COMMAND_REGISTRY)
    legacy_registry["record_oem_motor_stage_observation"] = replace(
        legacy_registry["record_oem_motor_stage_observation"],
        enabled=True,
        route_key="record_oem_motor_stage_observation",
        disabled_reason="",
    )

    async def scenario():
        connection = Connection()
        record = await CommandCoordinator(connection, legacy_registry).execute(request, mutations_enabled=True)
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


def test_axis_diagnostic_commands_are_finite_typed_and_m02_is_retired() -> None:
    parse, registry = _load()

    collect = parse({
        "command": "collect_axis_diagnostics",
        "expected_generation": 11,
        "idempotency_key": "axis-status-11",
    })
    assert collect.command == "collect_axis_diagnostics"

    run = parse({
        "command": "run_axis_diagnostic",
        "expected_generation": 11,
        "idempotency_key": "axis-x-positive-11",
        "axis": "x",
        "operation": "move-positive",
    })
    assert run.axis == "x" and run.operation == "move-positive"

    stop = parse({
        "command": "stop_axis_diagnostic",
        "expected_generation": 11,
        "idempotency_key": "axis-x-stop-11",
        "axis": "x",
    })
    assert stop.axis == "x"

    invalid = run.model_dump(mode="json")
    invalid["operation"] = "open"
    with pytest.raises(ValidationError):
        parse(invalid)
    with pytest.raises(ValidationError):
        parse({**run.model_dump(mode="json"), "steps": 999999})
    with pytest.raises(ValidationError):
        parse({
            "command": "run_oem_motor_stage",
            "expected_generation": 11,
            "idempotency_key": "retired-m02",
            "stage": "gripper-current-31",
            "mode": "live",
            "operator_ack": "HOME",
        })

    assert registry["collect_axis_diagnostics"].requires_hardware_ready is False
    assert registry["run_axis_diagnostic"].requires_hardware_ready is False
    assert registry["stop_axis_diagnostic"].requires_hardware_ready is False
    assert registry["stop_axis_diagnostic"].requires_fresh_observation is False


def test_axis_diagnostic_execution_holds_generation_lease_and_forwards_only_typed_payload() -> None:
    from contextlib import asynccontextmanager

    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    events: list[object] = []

    class Client:
        async def request(self, route_key, *, json_data):
            events.append(("request", route_key, json_data))
            if route_key == "collect_hardware_snapshot":
                return {"ok": True, "published": True, "snapshot_id": "post-axis-13"}
            return {
                "ok": True,
                "axis": "x",
                "operation": "move-positive",
                "terminal_status": {"rows": {"x": {"status": {"speed": {"speed": 0}}}}},
            }

    class Connection:
        active_client = Client()

        @asynccontextmanager
        async def workflow_lease(self, expected_generation):
            events.append(("lease-enter", expected_generation))
            try:
                yield self.active_client
            finally:
                events.append(("lease-exit", expected_generation))

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=13,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                hardware_observed_at=datetime.now(timezone.utc),
                hardware_observation_fresh=True,
                capabilities=("run_axis_diagnostic",),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                maintenance_state={"motion_blocked": False, "recovery_required": False},
                ownership={"transport": "owned", "usb": "service", "router": "running"},
            )

        def observe_command_response(self, value):
            events.append(("observe", value["axis"], value["operation"]))

    request = parse_command_request({
        "command": "run_axis_diagnostic",
        "expected_generation": 13,
        "idempotency_key": "axis-x-positive-13",
        "axis": "x",
        "operation": "move-positive",
    })

    record = asyncio.run(CommandCoordinator(Connection(), DEFAULT_COMMAND_REGISTRY).execute(request, mutations_enabled=True))

    assert record.status == "acknowledged"
    assert record.remote_acknowledged is True
    assert record.physical_effect_verified is False
    assert record.handler_response is not None
    assert "inline_hardware_evidence" not in record.handler_response
    assert events == [
        ("lease-enter", 13),
        ("request", "run_axis_diagnostic", {
            "axis": "x",
            "operation": "move-positive",
            "operator_ack": "RUN_AXIS_DIAGNOSTIC",
            "reason": "BMS operator requested x move-positive",
        }),
        ("observe", "x", "move-positive"),
        ("request", "collect_hardware_snapshot", None),
        ("lease-exit", 13),
    ]


def test_acknowledged_command_http_refresh_is_status_only() -> None:
    from types import SimpleNamespace

    from routers.bioxp.commands import execute_command

    class Result:
        remote_acknowledged = True
        status = "acknowledged"

        def model_dump(self, *, mode):
            return {"command": "run_axis_diagnostic", "status": self.status}

    class Commands:
        async def execute(self, request, *, mutations_enabled):
            return Result()

    class Connection:
        status_only_called = False

        async def probe(self):
            raise AssertionError("post-command HTTP publication must not auto-collect another snapshot")

        async def probe_status_only(self):
            self.status_only_called = True

    connection = Connection()
    runtime = SimpleNamespace(commands=Commands(), connection=connection)
    payload = {
        "command": "run_axis_diagnostic",
        "expected_generation": 13,
        "idempotency_key": "axis-status-only-13",
        "axis": "x",
        "operation": "home",
    }

    result = asyncio.run(execute_command(payload, runtime))

    assert result["status"] == "acknowledged"
    assert connection.status_only_called is True


def test_axis_stop_preempts_inflight_diagnostic_without_waiting_for_workflow_lease() -> None:
    from contextlib import asynccontextmanager

    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    async def scenario() -> None:
        run_started = asyncio.Event()
        release_run = asyncio.Event()
        stop_sent = asyncio.Event()
        events: list[object] = []

        class Client:
            async def request(self, route_key, *, json_data):
                events.append(("request", route_key))
                if route_key == "run_axis_diagnostic":
                    run_started.set()
                    await release_run.wait()
                    return {"ok": True, "axis": "x", "operation": "move-positive"}
                assert route_key == "stop_axis_diagnostic"
                stop_sent.set()
                return {"ok": True, "axis": "x", "acknowledged": True}

        class Connection:
            active_client = Client()

            @asynccontextmanager
            async def workflow_lease(self, expected_generation):
                events.append(("lease-enter", expected_generation))
                try:
                    yield self.active_client
                finally:
                    events.append(("lease-exit", expected_generation))

            def snapshot(self):
                return BioXpSnapshot(
                    configured=True,
                    active=True,
                    generation=17,
                    reachable=True,
                    runtime_ready=True,
                    hardware_ready=True,
                    hardware_observed_at=datetime.now(timezone.utc),
                    hardware_observation_fresh=True,
                    capabilities=("run_axis_diagnostic", "stop_axis_diagnostic"),
                    observed_at=datetime.now(timezone.utc),
                    freshness_budget_seconds=30.0,
                    observation_fresh=True,
                    maintenance_state={"motion_blocked": False, "recovery_required": False},
                    ownership={"transport": "owned", "usb": "service", "router": "running"},
                )

        coordinator = CommandCoordinator(Connection(), DEFAULT_COMMAND_REGISTRY)
        run_request = parse_command_request({
            "command": "run_axis_diagnostic",
            "expected_generation": 17,
            "idempotency_key": "axis-x-run-preemption-17",
            "axis": "x",
            "operation": "move-positive",
        })
        stop_request = parse_command_request({
            "command": "stop_axis_diagnostic",
            "expected_generation": 17,
            "idempotency_key": "axis-x-stop-preemption-17",
            "axis": "x",
        })

        run_task = asyncio.create_task(coordinator.execute(run_request, mutations_enabled=True))
        await run_started.wait()
        stop_task = asyncio.create_task(coordinator.execute(stop_request, mutations_enabled=True))
        await asyncio.wait_for(stop_sent.wait(), timeout=0.5)
        stop_record = await asyncio.wait_for(stop_task, timeout=0.5)
        assert stop_record.status == "acknowledged"
        assert run_task.done() is False
        assert events[:3] == [
            ("lease-enter", 17),
            ("request", "run_axis_diagnostic"),
            ("request", "stop_axis_diagnostic"),
        ]
        release_run.set()
        await run_task
        assert events[-1] == ("lease-exit", 17)

    asyncio.run(scenario())


def test_axis_stop_http_acknowledgement_never_waits_for_normal_probe_lane() -> None:
    from types import SimpleNamespace

    from routers.bioxp.commands import execute_command

    class Result:
        remote_acknowledged = True
        status = "acknowledged"
        command = "stop_axis_diagnostic"

        def model_dump(self, *, mode):
            return {"command": self.command, "status": self.status}

    class Commands:
        async def execute(self, request, *, mutations_enabled):
            assert request.command == "stop_axis_diagnostic"
            return Result()

    class Connection:
        probe_called = False

        async def probe(self):
            self.probe_called = True
            await asyncio.Event().wait()

    connection = Connection()
    runtime = SimpleNamespace(commands=Commands(), connection=connection)
    payload = {
        "command": "stop_axis_diagnostic",
        "expected_generation": 13,
        "idempotency_key": "axis-stop-http-13",
        "axis": "x",
    }

    result = asyncio.run(asyncio.wait_for(execute_command(payload, runtime), timeout=0.1))

    assert result["command"] == "stop_axis_diagnostic"
    assert connection.probe_called is False


def test_non_homing_recovery_is_typed_and_maps_to_exact_robot_payload() -> None:
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    valid_payload = {
        "command": "recover_motion_non_homing",
        "expected_generation": 19,
        "idempotency_key": "recover-motion-19",
    }
    request = parse_command_request(valid_payload)
    for invalid in (
        {**valid_payload, "operator_ack": "RECOVER_MOTION"},
        {**valid_payload, "reason": "unnecessary BMS prompt"},
    ):
        with pytest.raises(ValidationError):
            parse_command_request(invalid)

    class Client:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, route_key, *, json_data):
            self.calls.append((route_key, json_data))
            return {
                "ok": True,
                "maintenance_state": {
                    "motion_blocked": False,
                    "recovery_required": False,
                    "block_reason": None,
                },
            }

    class Connection:
        active_client = Client()

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=19,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                hardware_observation_fresh=True,
                capabilities=("recover_motion_non_homing",),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                maintenance_state={
                    "motion_blocked": True,
                    "recovery_required": True,
                    "block_reason": "USB owner changed",
                },
                ownership={"transport": "owned", "usb": "service", "router": "running"},
                )

        def observe_command_response(self, value):
            self.observed = value

    connection = Connection()
    record = asyncio.run(
        CommandCoordinator(connection, DEFAULT_COMMAND_REGISTRY).execute(request, mutations_enabled=True)
    )

    assert record.status == "acknowledged"
    assert connection.active_client.calls == [(
        "recover_motion_non_homing",
        {
            "run_homing": False,
            "operator_ack": "RECOVER_MOTION",
            "operator_reason": "BMS operator requested controller initialization",
        },
    )]
    assert DEFAULT_COMMAND_REGISTRY["recover_motion_non_homing"].required_capability == "recover_motion_non_homing"

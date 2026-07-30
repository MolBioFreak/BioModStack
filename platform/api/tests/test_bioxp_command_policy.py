from __future__ import annotations

from dataclasses import replace


def _load():
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_policy import CommandAdmissionContext, evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    return parse_command_request, CommandAdmissionContext, evaluate_command, DEFAULT_COMMAND_REGISTRY


def _allow_context(Context, **changes):
    values = {
        "mutations_enabled": True,
        "active": True,
        "generation": 7,
        "observation_fresh": True,
        "runtime_ready": True,
        "hardware_ready": True,
        "capabilities": frozenset({"initialize_motors"}),
        "maintenance_state": {"motion_blocked": False, "recovery_required": False},
    }
    values.update(changes)
    return Context(**values)


def test_policy_table_allows_verified_definition_and_denies_each_missing_gate() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({"command": "initialize_motors", "expected_generation": 7, "idempotency_key": "init-7"})
    verified = replace(
        registry["initialize_motors"],
        enabled=True,
        route_key="initialize_motors",
        disabled_reason="",
    )

    assert evaluate(request, verified, _allow_context(Context)).allowed is True

    cases = [
        ({"mutations_enabled": False}, "disabled"),
        ({"active": False}, "active target"),
        ({"generation": 8}, "generation"),
        ({"observation_fresh": False}, "fresh"),
        ({"runtime_ready": None}, "runtime readiness"),
        ({"hardware_ready": None}, "hardware readiness"),
        ({"capabilities": frozenset()}, "capability"),
    ]
    for changes, expected in cases:
        decision = evaluate(request, verified, _allow_context(Context, **changes))
        assert decision.allowed is False
        assert expected in " ".join(decision.reasons).lower()


def test_disabled_default_definition_fails_before_transport_admission() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({"command": "initialize_motors", "expected_generation": 7, "idempotency_key": "init-7"})

    decision = evaluate(request, registry[request.command], _allow_context(Context))

    assert decision.allowed is False
    assert any("online contract" in reason.lower() for reason in decision.reasons)


def test_retired_usb_activation_is_denied_for_every_runtime_state() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({
        "command": "activate_usb_for_service",
        "expected_generation": 7,
        "idempotency_key": "activate-usb-7",
    })
    definition = registry["activate_usb_for_service"]

    inactive = _allow_context(
        Context,
        runtime_ready=None,
        hardware_ready=None,
        capabilities=frozenset(),
    )
    for context in (
        inactive,
        replace(inactive, observation_fresh=False),
        replace(inactive, runtime_ready=True),
    ):
        decision = evaluate(request, definition, context)
        assert decision.allowed is False
        assert decision.reasons[0] == (
            "robot-contract-unavailable: unsupported by the exact robot runtime contract"
        )


def test_hardware_snapshot_requires_service_runtime_ownership() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({
        "command": "collect_hardware_snapshot",
        "expected_generation": 7,
        "idempotency_key": "snapshot-7",
    })
    definition = registry["collect_hardware_snapshot"]
    unbound = _allow_context(
        Context,
        runtime_ready=None,
        hardware_ready=None,
        capabilities=frozenset({"collect_hardware_snapshot"}),
    )

    denied = evaluate(request, definition, unbound)
    assert denied.allowed is False
    assert "runtime readiness must be known and ready" in " ".join(denied.reasons).lower()

    owned = replace(unbound, runtime_ready=True)
    assert evaluate(request, definition, owned).allowed is True


def test_finite_motion_relay_does_not_apply_bms_maintenance_state_policy() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({
        "command": "run_axis_diagnostic",
        "expected_generation": 7,
        "idempotency_key": "axis-motion-maintenance-7",
        "axis": "x",
        "operation": "move-positive",
    })
    base = _allow_context(Context, capabilities=frozenset({"run_axis_diagnostic"}))

    for state in (
        None,
        {},
        {"motion_blocked": None},
        {"motion_blocked": False},
        {"motion_blocked": False, "recovery_required": True},
        {"motion_blocked": False, "recovery_required": "true"},
        {"motion_blocked": "false", "recovery_required": False},
        {"motion_blocked": True, "recovery_required": False, "block_reason": "USB owner changed"},
        {"motion_blocked": True, "recovery_required": True, "block_reason": "USB owner changed"},
    ):
        decision = evaluate(request, registry[request.command], replace(base, maintenance_state=state))
        assert decision.allowed is True

    assert evaluate(request, registry[request.command], base).allowed is True


def test_non_homing_recovery_does_not_require_preexisting_runtime_or_hardware_readiness() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({
        "command": "recover_motion_non_homing",
        "expected_generation": 7,
        "idempotency_key": "recover-motion-unready-7",
    })
    definition = registry[request.command]
    base = _allow_context(
        Context,
        capabilities=frozenset({"recover_motion_non_homing"}),
        maintenance_state={
            "motion_blocked": True,
            "recovery_required": True,
            "block_reason": "Service startup requires recovery",
        },
    )

    assert definition.requires_runtime_ready is False
    assert definition.requires_hardware_ready is False
    for runtime_ready in (False, None):
        for hardware_ready in (False, None):
            assert evaluate(
                request,
                definition,
                replace(base, runtime_ready=runtime_ready, hardware_ready=hardware_ready),
            ).allowed is True


def test_non_homing_initialization_requires_active_exact_capability_not_bms_derived_state() -> None:
    parse, Context, evaluate, registry = _load()
    request = parse({
        "command": "recover_motion_non_homing",
        "expected_generation": 7,
        "idempotency_key": "recover-motion-7",
    })
    definition = registry[request.command]
    admitted = _allow_context(
        Context,
        capabilities=frozenset({"recover_motion_non_homing"}),
        maintenance_state={
            "motion_blocked": True,
            "recovery_required": True,
            "block_reason": "USB owner changed",
        },
    )
    assert evaluate(request, definition, admitted).allowed is True

    for changes in (
        {"maintenance_state": None},
        {"maintenance_state": {"motion_blocked": False, "recovery_required": True}},
        {"maintenance_state": {"motion_blocked": True, "recovery_required": False}},
        {"observation_fresh": False},
    ):
        assert evaluate(request, definition, replace(admitted, **changes)).allowed is True

    assert evaluate(
        request,
        definition,
        replace(admitted, capabilities=frozenset()),
    ).allowed is False

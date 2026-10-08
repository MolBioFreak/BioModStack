from __future__ import annotations

from dataclasses import replace


def _load():
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_policy import CommandAdmissionContext, evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    return parse_command_request, CommandAdmissionContext, evaluate_command, DEFAULT_COMMAND_REGISTRY


def _allow_context(Context, **changes):
    values = {
        "token_authorized": True,
        "mutations_enabled": True,
        "active": True,
        "generation": 7,
        "observation_fresh": True,
        "runtime_ready": True,
        "hardware_ready": True,
        "capabilities": frozenset({"initialize_motors"}),
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
        ({"token_authorized": False}, "operator credential"),
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

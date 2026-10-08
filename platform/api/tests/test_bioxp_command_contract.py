from __future__ import annotations

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


def test_default_registry_exposes_only_current_compact_commissioning_mappings() -> None:
    _, registry = _load()

    assert set(registry) == {
        "activate_usb_for_service",
        "collect_hardware_snapshot",
        "initialize_oem_environment",
        "initialize_motors",
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
    }
    for name, route_key in enabled.items():
        assert registry[name].enabled is True
        assert registry[name].route_key == route_key
        if name != "activate_usb_for_service":
            assert registry[name].required_capability == name

    assert registry["initialize_oem_environment"].requires_hardware_ready is True
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

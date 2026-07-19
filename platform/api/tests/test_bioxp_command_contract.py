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


def test_default_registry_has_no_unverified_robot_mapping() -> None:
    _, registry = _load()

    assert set(registry) == {
        "initialize_motors",
        "start_job",
        "pause_job",
        "resume_job",
        "stop_job",
        "recover_runtime",
    }
    assert all(not definition.enabled for definition in registry.values())
    assert all(definition.route_key is None for definition in registry.values())
    assert all("online contract" in definition.disabled_reason.lower() for definition in registry.values())

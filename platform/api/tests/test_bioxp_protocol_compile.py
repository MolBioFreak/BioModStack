from __future__ import annotations


def _load():
    from services.bioxp.protocols import BioXpProtocol, compile_protocol

    return BioXpProtocol, compile_protocol


def test_offline_compile_is_deterministic_and_honest_about_robot_compatibility() -> None:
    Model, compile_protocol = _load()
    protocol = Model.model_validate(
        {
            "schema_version": 1,
            "name": "offline demo",
            "steps": [
                {"action": "initialize_motors"},
                {"action": "start_job", "job_id": "job-1"},
            ],
        }
    )

    first = compile_protocol(protocol)
    second = compile_protocol(protocol)

    assert first.compiled_hash == second.compiled_hash
    assert first.validation_status == "validated_offline"
    assert first.robot_compatible is None
    assert first.executable is False
    assert "online" in " ".join(first.blockers).lower()


def test_protocol_model_rejects_paths_unknown_actions_and_extra_parameters() -> None:
    from pydantic import ValidationError

    Model, _ = _load()
    invalid = [
        {"schema_version": 1, "name": "x", "steps": [{"action": "arbitrary"}]},
        {"schema_version": 1, "name": "x", "steps": [{"action": "initialize_motors", "path": "/motion/x"}]},
        {"schema_version": 1, "name": "x", "steps": [{"action": "start_job"}]},
    ]
    for payload in invalid:
        try:
            Model.model_validate(payload)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid protocol accepted: {payload}")

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


def _startup(*, constructor="not_run", no_motion="blocked", initial="blocked"):
    return {
        "state": "not_run",
        "stages": {
            "constructor_pipette_stage": {"state": constructor},
            "initialization_without_motion": {"state": no_motion},
            "initial_check": {"state": initial, "repeatable": True},
        },
    }


def _request(generation: int, idempotency_key: str):
    from services.bioxp.command_models import parse_command_request

    return parse_command_request({
        "command": "initialize_oem_environment",
        "expected_generation": generation,
        "idempotency_key": idempotency_key,
        "mode": "live",
        "operator_ack": "INITIALIZE",
    })


def _context(startup, *, hardware_ready: bool | None = True):
    from services.bioxp.command_policy import CommandAdmissionContext

    return CommandAdmissionContext(
        mutations_enabled=True,
        active=True,
        generation=7,
        observation_fresh=True,
        runtime_ready=True,
        hardware_ready=hardware_ready,
        capabilities=frozenset({"initialize_oem_environment"}),
        startup_lifecycle=startup,
    )


def test_aggregate_oem_startup_is_admitted_only_for_a_fresh_ownership_epoch():
    from services.bioxp.command_policy import evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    request = _request(7, "oem-startup-1")
    definition = DEFAULT_COMMAND_REGISTRY[request.command]

    assert evaluate_command(request, definition, _context(_startup(), hardware_ready=None)).allowed
    for startup in (
        _startup(constructor="passed", no_motion="not_run"),
        _startup(constructor="passed", no_motion="passed", initial="passed"),
        _startup(constructor="failed"),
        _startup(constructor="running"),
    ):
        decision = evaluate_command(request, definition, _context(startup))
        assert decision.allowed is False
        assert any("fresh ownership epoch" in reason.lower() for reason in decision.reasons)


def test_command_record_preserves_aggregate_handler_failure_and_lifecycle_response():
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    response = {
        "ok": False,
        "failed_stage": "constructor_pipette_stage",
        "lifecycle": {"startup": _startup(constructor="failed")},
        "initialize_system_started": False,
    }

    class Client:
        async def request(self, *_args, **_kwargs):
            return response

    class Connection:
        active_client = Client()

        def __init__(self):
            self.observed = None

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=4,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                capabilities=("initialize_oem_environment",),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                startup_lifecycle=_startup(),
            )

        def set_command_active(self, _active):
            pass

        def observe_command_response(self, value):
            self.observed = value

    async def scenario():
        connection = Connection()
        coordinator = CommandCoordinator(connection, DEFAULT_COMMAND_REGISTRY)
        record = await coordinator.execute(_request(4, "preserve-failure"), mutations_enabled=True)
        assert record.status == "delivery_failed"
        assert record.remote_acknowledged is False
        assert record.handler_response == response
        assert connection.observed == response

    asyncio.run(scenario())


def test_aggregate_admission_rejects_missing_null_unknown_and_wrong_stage_states():
    from services.bioxp.command_policy import evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    request = _request(7, "malformed-lifecycle")
    definition = DEFAULT_COMMAND_REGISTRY[request.command]

    malformed = (
        None,
        {},
        {"state": "not_run", "stages": {}},
        {"state": "not_run", "stages": {"constructor_pipette_stage": {"state": None}}},
        _startup(no_motion="unexpected"),
    )
    for startup in malformed:
        decision = evaluate_command(request, definition, _context(startup))
        assert decision.allowed is False
        assert any("lifecycle" in reason.lower() or "fresh ownership epoch" in reason.lower() for reason in decision.reasons)


def test_http_error_updates_cached_lifecycle_before_aggregate_failure_record_is_returned():
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.errors import RobotResponseError
    from services.bioxp.models import BioXpSnapshot

    body = {
        "detail": {
            "ok": False,
            "failed_stage": "constructor_pipette_stage",
            "lifecycle": {"startup": _startup(constructor="failed")},
            "initialize_system_started": False,
        }
    }

    class Client:
        async def request(self, *_args, **_kwargs):
            raise RobotResponseError(409, body)

    class Connection:
        active_client = Client()

        def __init__(self):
            self.observed = None

        def snapshot(self):
            return BioXpSnapshot(
                configured=True,
                active=True,
                generation=4,
                reachable=True,
                runtime_ready=True,
                hardware_ready=True,
                capabilities=("initialize_oem_environment",),
                observed_at=datetime.now(timezone.utc),
                freshness_budget_seconds=30.0,
                observation_fresh=True,
                startup_lifecycle=_startup(),
            )

        def set_command_active(self, _active):
            pass

        def observe_command_response(self, value):
            self.observed = value

    async def scenario():
        connection = Connection()
        coordinator = CommandCoordinator(connection, DEFAULT_COMMAND_REGISTRY)
        record = await coordinator.execute(_request(4, "http-lifecycle-failure"), mutations_enabled=True)
        assert record.status == "delivery_failed"
        assert isinstance(record.handler_response, dict)
        assert record.handler_response["http_status"] == 409
        assert record.handler_response["detail"] == body
        assert connection.observed == body

    asyncio.run(scenario())

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


def test_lifecycle_admission_orders_one_shot_stages_and_keeps_initial_check_repeatable():
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_policy import CommandAdmissionContext, evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    def context(startup):
        return CommandAdmissionContext(
            mutations_enabled=True,
            active=True,
            generation=7,
            observation_fresh=True,
            runtime_ready=True,
            hardware_ready=True,
            capabilities=frozenset({
                "construct_pipettes",
                "initialize_without_motion",
                "run_initial_check",
            }),
            startup_lifecycle=startup,
        )

    construct = parse_command_request({
        "command": "construct_pipettes",
        "expected_generation": 7,
        "idempotency_key": "constructor-1",
    })
    no_motion = parse_command_request({
        "command": "initialize_without_motion",
        "expected_generation": 7,
        "idempotency_key": "no-motion-1",
    })
    initial = parse_command_request({
        "command": "run_initial_check",
        "expected_generation": 7,
        "idempotency_key": "initial-1",
        "mode": "live",
        "operator_ack": "INITIALIZE",
    })

    first = _startup()
    assert evaluate_command(construct, DEFAULT_COMMAND_REGISTRY[construct.command], context(first)).allowed
    assert not evaluate_command(no_motion, DEFAULT_COMMAND_REGISTRY[no_motion.command], context(first)).allowed

    after_constructor = _startup(constructor="passed", no_motion="not_run")
    assert not evaluate_command(construct, DEFAULT_COMMAND_REGISTRY[construct.command], context(after_constructor)).allowed
    assert evaluate_command(no_motion, DEFAULT_COMMAND_REGISTRY[no_motion.command], context(after_constructor)).allowed

    complete = _startup(constructor="passed", no_motion="passed", initial="passed")
    assert evaluate_command(initial, DEFAULT_COMMAND_REGISTRY[initial.command], context(complete)).allowed


def test_command_record_preserves_handler_failure_and_lifecycle_response():
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    response = {
        "ok": False,
        "error": "status_query_failed",
        "lifecycle": {"startup": _startup(constructor="failed", no_motion="blocked")},
        "trace": [{"step": "query_status", "ok": False}],
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
                capabilities=("construct_pipettes",),
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
        request = parse_command_request({
            "command": "construct_pipettes",
            "expected_generation": 4,
            "idempotency_key": "preserve-failure",
        })
        record = await coordinator.execute(request, mutations_enabled=True)
        assert record.status == "delivery_failed"
        assert record.remote_acknowledged is False
        assert record.handler_response == response
        assert connection.observed == response

    asyncio.run(scenario())


def test_lifecycle_admission_rejects_missing_null_and_unknown_stage_states():
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_policy import CommandAdmissionContext, evaluate_command
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY

    request = parse_command_request({
        "command": "construct_pipettes",
        "expected_generation": 7,
        "idempotency_key": "malformed-lifecycle",
    })
    definition = DEFAULT_COMMAND_REGISTRY[request.command]

    for stage in ({}, {"state": None}, {"state": "unexpected"}, "not-a-stage"):
        context = CommandAdmissionContext(
            mutations_enabled=True,
            active=True,
            generation=7,
            observation_fresh=True,
            runtime_ready=True,
            hardware_ready=True,
            capabilities=frozenset({"construct_pipettes"}),
            startup_lifecycle={
                "state": "not_run",
                "stages": {"constructor_pipette_stage": stage},
            },
        )
        decision = evaluate_command(request, definition, context)
        assert decision.allowed is False
        assert any("lifecycle" in reason.lower() for reason in decision.reasons)


def test_http_error_updates_cached_lifecycle_before_failure_record_is_returned():
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.errors import RobotResponseError
    from services.bioxp.models import BioXpSnapshot

    body = {
        "startup": _startup(constructor="failed", no_motion="blocked"),
        "error": "constructor_status_failed",
    }

    class Client:
        async def request(self, *_args, **_kwargs):
            raise RobotResponseError(500, body)

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
                capabilities=("construct_pipettes",),
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
        request = parse_command_request({
            "command": "construct_pipettes",
            "expected_generation": 4,
            "idempotency_key": "http-lifecycle-failure",
        })
        record = await coordinator.execute(request, mutations_enabled=True)
        assert record.status == "delivery_failed"
        assert isinstance(record.handler_response, dict)
        assert record.handler_response["http_status"] == 500
        assert record.handler_response["detail"] == body
        assert connection.observed == body

    asyncio.run(scenario())

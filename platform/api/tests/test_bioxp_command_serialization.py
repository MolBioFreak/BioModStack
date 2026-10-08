from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest


def _load():
    from services.bioxp.command_coordinator import CommandBusyError, CommandCoordinator
    from services.bioxp.command_models import parse_command_request
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    return CommandBusyError, CommandCoordinator, parse_command_request, DEFAULT_COMMAND_REGISTRY, BioXpSnapshot


class BlockingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def request(self, route_name: str, *, json_data=None, **_: object):
        self.calls.append((route_name, dict(json_data or {})))
        self.started.set()
        await self.release.wait()
        return {"acknowledged": True}


class FakeConnection:
    def __init__(self, snapshot, client) -> None:
        self._snapshot = snapshot
        self.active_client = client

    def snapshot(self):
        return self._snapshot


def _ready_snapshot(Model):
    return Model(
        configured=True,
        active=True,
        generation=4,
        reachable=True,
        runtime_ready=True,
        hardware_ready=True,
        capabilities=("initialize_motors",),
        freshness_budget_seconds=30,
        observation_fresh=True,
    )


def test_two_normal_commands_cannot_overlap_and_busy_is_409_semantic() -> None:
    Busy, Coordinator, parse, registry, Snapshot = _load()

    async def scenario():
        client = BlockingClient()
        connection = FakeConnection(_ready_snapshot(Snapshot), client)
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(connection, definitions)
        request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "one"})
        first = asyncio.create_task(coordinator.execute(request, token_authorized=True, mutations_enabled=True))
        await client.started.wait()
        with pytest.raises(Busy) as exc_info:
            await coordinator.execute(
                request.model_copy(update={"idempotency_key": "two"}),
                token_authorized=True,
                mutations_enabled=True,
            )
        assert exc_info.value.status_code == 409
        client.release.set()
        result = await first
        assert result.status == "acknowledged"
        assert len(client.calls) == 1

    asyncio.run(scenario())


def test_concurrent_same_normal_idempotency_key_joins_one_delivery() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    async def scenario() -> None:
        client = BlockingClient()
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), client), definitions)
        request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "join"})
        first = asyncio.create_task(coordinator.execute(request, token_authorized=True, mutations_enabled=True))
        await client.started.wait()
        second = asyncio.create_task(coordinator.execute(request, token_authorized=True, mutations_enabled=True))
        await asyncio.sleep(0)
        assert second.done() is False
        client.release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result == second_result
        assert len(client.calls) == 1

    asyncio.run(scenario())


def test_normal_and_emergency_operations_cannot_share_an_inflight_key() -> None:
    from services.bioxp.command_coordinator import IdempotencyConflictError

    _, Coordinator, parse, registry, Snapshot = _load()

    async def scenario() -> None:
        client = BlockingClient()
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), client), definitions)
        request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "cross"})
        normal = asyncio.create_task(coordinator.execute(request, token_authorized=True, mutations_enabled=True))
        await client.started.wait()
        with pytest.raises(IdempotencyConflictError):
            await coordinator.emergency_stop(
                expected_generation=4,
                idempotency_key="cross",
                token_authorized=True,
                mutations_enabled=True,
            )
        client.release.set()
        await normal
        assert [call[0] for call in client.calls] == ["initialize_motors"]

    asyncio.run(scenario())


def test_idempotency_returns_structured_prior_result_without_redelivery() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    class ImmediateClient:
        def __init__(self):
            self.calls = 0

        async def request(self, route_name: str, *, json_data=None, **_: object):
            self.calls += 1
            return {"acknowledged": True, "route": route_name}

    async def scenario():
        client = ImmediateClient()
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), client), definitions)
        request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "same"})
        first = await coordinator.execute(request, token_authorized=True, mutations_enabled=True)
        second = await coordinator.execute(request, token_authorized=True, mutations_enabled=True)
        assert first == second
        assert client.calls == 1
        assert coordinator.get(first.command_id) == first

    asyncio.run(scenario())


def test_idempotent_replay_still_requires_current_authorization() -> None:
    from services.bioxp.command_coordinator import CommandDeniedError

    _, Coordinator, parse, registry, Snapshot = _load()

    class ImmediateClient:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, *_: object, **__: object):
            self.calls += 1
            return {"acknowledged": True}

    async def scenario() -> None:
        client = ImmediateClient()
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), client), definitions)
        request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "auth-replay"})
        await coordinator.execute(request, token_authorized=True, mutations_enabled=True)
        with pytest.raises(CommandDeniedError):
            await coordinator.execute(request, token_authorized=False, mutations_enabled=True)
        assert client.calls == 1

    asyncio.run(scenario())


def test_denied_precondition_never_reaches_transport() -> None:
    from services.bioxp.command_coordinator import CommandDeniedError

    _, Coordinator, parse, registry, Snapshot = _load()

    class ExplodingClient:
        async def request(self, *_: object, **__: object):
            raise AssertionError("transport must not be reached")

    snapshot = _ready_snapshot(Snapshot).model_copy(update={"hardware_ready": None})
    definitions = dict(registry)
    definitions["initialize_motors"] = replace(
        definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
    )
    coordinator = Coordinator(FakeConnection(snapshot, ExplodingClient()), definitions)
    request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "denied"})

    with pytest.raises(CommandDeniedError):
        asyncio.run(coordinator.execute(request, token_authorized=True, mutations_enabled=True))


def test_remote_acknowledgement_requires_literal_true() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    class StringClient:
        async def request(self, *_: object, **__: object):
            return {"acknowledged": "false", "ok": "error"}

    definitions = dict(registry)
    definitions["initialize_motors"] = replace(
        definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
    )
    coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), StringClient()), definitions)
    request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "strict-bool"})

    result = asyncio.run(coordinator.execute(request, token_authorized=True, mutations_enabled=True))
    assert result.remote_acknowledged is False
    assert result.status == "delivered_unacknowledged"

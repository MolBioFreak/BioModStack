from __future__ import annotations

import asyncio
import json
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
        first = asyncio.create_task(coordinator.execute(request, mutations_enabled=True))
        await client.started.wait()
        with pytest.raises(Busy) as exc_info:
            await coordinator.execute(
                request.model_copy(update={"idempotency_key": "two"}),
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
        first = asyncio.create_task(coordinator.execute(request, mutations_enabled=True))
        await client.started.wait()
        second = asyncio.create_task(coordinator.execute(request, mutations_enabled=True))
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
        normal = asyncio.create_task(coordinator.execute(request, mutations_enabled=True))
        await client.started.wait()
        with pytest.raises(IdempotencyConflictError):
            await coordinator.emergency_stop(
                expected_generation=4,
                idempotency_key="cross",
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
        first = await coordinator.execute(request, mutations_enabled=True)
        second = await coordinator.execute(request, mutations_enabled=True)
        assert first == second
        assert client.calls == 1
        assert coordinator.get(first.command_id) == first

    asyncio.run(scenario())


def test_legacy_activation_replay_semantics_require_an_explicit_non_default_mapping() -> None:
    from services.bioxp.command_coordinator import CommandDeniedError

    _, Coordinator, parse, registry, Snapshot = _load()

    class ActivatingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.connection: Any = None

        async def request(self, route_name: str, **__: object):
            self.calls.append(route_name)
            if route_name == "activate_usb_for_service":
                self.connection._snapshot = self.connection._snapshot.model_copy(update={
                    "runtime_ready": True,
                    "ownership": {"transport": "owned", "usb": "service", "router": "running"},
                })
                return {"acknowledged": True, "ok": True}
            raise AssertionError(f"unexpected hidden request: {route_name}")

    async def scenario() -> None:
        client = ActivatingClient()
        snapshot = _ready_snapshot(Snapshot).model_copy(
            update={
                "runtime_ready": False,
                "hardware_ready": None,
                "capabilities": (),
                "ownership": {"transport": "unbound", "usb": "unbound", "router": "unbound"},
            }
        )
        connection = FakeConnection(snapshot, client)
        client.connection = connection
        definitions = dict(registry)
        definitions["activate_usb_for_service"] = replace(
            definitions["activate_usb_for_service"],
            enabled=True,
            route_key="activate_usb_for_service",
            disabled_reason="",
        )
        coordinator = Coordinator(connection, definitions)
        request = parse({
            "command": "activate_usb_for_service",
            "expected_generation": 4,
            "idempotency_key": "activate-replay",
        })

        first = await coordinator.execute(request, mutations_enabled=True)
        second = await coordinator.execute(request, mutations_enabled=True)

        assert second == first
        assert client.calls == ["activate_usb_for_service"]

        for invalid_ownership in (
            {"transport": "owned", "usb": "foreign", "router": "running"},
            {"transport": "owned", "usb": "service", "router": "stopped"},
            {"transport": "quarantined", "usb": "quarantined", "router": "stopped"},
            {"transport": "unbound", "usb": None, "router": "unbound"},
            None,
        ):
            connection._snapshot = connection._snapshot.model_copy(update={"ownership": invalid_ownership})
            with pytest.raises(CommandDeniedError, match="canonical|ownership"):
                await coordinator.execute(request, mutations_enabled=True)
            assert client.calls == ["activate_usb_for_service"]

        connection._snapshot = connection._snapshot.model_copy(update={
            "ownership": {"transport": "owned", "usb": "service", "router": "running"},
        })

        connection._snapshot = connection._snapshot.model_copy(update={"generation": 5})
        with pytest.raises(CommandDeniedError, match="generation"):
            await coordinator.execute(request, mutations_enabled=True)
        assert client.calls == ["activate_usb_for_service"]

    asyncio.run(scenario())


def test_idempotent_replay_does_not_redeliver() -> None:
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
        await coordinator.execute(request, mutations_enabled=True)
        await coordinator.execute(request, mutations_enabled=True)
        assert client.calls == 1

    asyncio.run(scenario())


def test_motion_replay_is_denied_after_ownership_loss_without_redelivery() -> None:
    from services.bioxp.command_coordinator import CommandDeniedError

    _, Coordinator, parse, registry, Snapshot = _load()

    class ImmediateClient:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, *_: object, **__: object):
            self.calls += 1
            return {"acknowledged": True, "ok": True}

    async def scenario() -> None:
        client = ImmediateClient()
        snapshot = _ready_snapshot(Snapshot).model_copy(update={
            "capabilities": ("run_axis_diagnostic",),
            "ownership": {"transport": "owned", "usb": "service", "router": "running"},
            "maintenance_state": {"motion_blocked": False, "recovery_required": False},
        })
        connection = FakeConnection(snapshot, client)
        coordinator = Coordinator(connection, registry)
        request = parse({
            "command": "run_axis_diagnostic",
            "expected_generation": 4,
            "idempotency_key": "motion-ownership-replay",
            "axis": "x",
            "operation": "home",
        })

        first = await coordinator.execute(request, mutations_enabled=True)
        assert first.status == "acknowledged"
        connection._snapshot = connection._snapshot.model_copy(update={
            "ownership": {"transport": "unbound", "usb": "unbound", "router": "unbound"},
        })
        with pytest.raises(CommandDeniedError, match="not service-owned and running"):
            await coordinator.execute(request, mutations_enabled=True)
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
        asyncio.run(coordinator.execute(request, mutations_enabled=True))


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

    result = asyncio.run(coordinator.execute(request, mutations_enabled=True))
    assert result.remote_acknowledged is False
    assert result.status == "delivered_unacknowledged"


def test_history_eviction_also_evicts_idempotency_replay_state() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, *_: object, **__: object):
            self.calls += 1
            return {"acknowledged": True}

    async def scenario() -> None:
        client = Client()
        definitions = dict(registry)
        definitions["initialize_motors"] = replace(
            definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
        )
        coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), client), definitions, history_limit=2)
        requests = [parse({
            "command": "initialize_motors",
            "expected_generation": 4,
            "idempotency_key": f"bounded-{index}",
        }) for index in range(3)]
        records = [await coordinator.execute(request, mutations_enabled=True) for request in requests]

        assert coordinator.get(records[0].command_id) is None
        replayed = await coordinator.execute(requests[0], mutations_enabled=True)
        assert replayed.command_id != records[0].command_id
        assert client.calls == 4
        assert len(coordinator._idempotent) <= 2

    asyncio.run(scenario())


def test_upstream_receipt_is_bounded_before_command_record_persistence() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    class HugeClient:
        async def request(self, *_: object, **__: object):
            return {"ok": False, "detail": {"detail": "X" * 1_000_000}}

    definitions = dict(registry)
    definitions["initialize_motors"] = replace(
        definitions["initialize_motors"], enabled=True, route_key="initialize_motors", disabled_reason=""
    )
    coordinator = Coordinator(FakeConnection(_ready_snapshot(Snapshot), HugeClient()), definitions)
    request = parse({"command": "initialize_motors", "expected_generation": 4, "idempotency_key": "huge"})

    result = asyncio.run(coordinator.execute(request, mutations_enabled=True))
    assert len(result.model_dump_json()) < 30_000
    assert len(result.detail) <= 4_096
    assert len(str(result.handler_response)) < 20_000


def test_wide_receipts_and_interrupt_exceptions_have_hard_record_size_limits() -> None:
    _, Coordinator, parse, registry, Snapshot = _load()

    class WideClient:
        async def request(self, route_name: str, **__: object):
            if route_name in {"stop_axis_diagnostic", "emergency_stop"}:
                raise RuntimeError("E" * 1_000_000)
            payload = {f"wide-key-{index}-" + ("K" * 1_000): index for index in range(200)}
            payload["acknowledged"] = True
            return payload

    async def scenario() -> None:
        definitions = dict(registry)
        for name in ("initialize_motors", "stop_axis_diagnostic"):
            definitions[name] = replace(definitions[name], enabled=True, route_key=name, disabled_reason="")
        snapshot = _ready_snapshot(Snapshot).model_copy(update={
            "capabilities": (*_ready_snapshot(Snapshot).capabilities, "stop_axis_diagnostic"),
        })
        coordinator = Coordinator(FakeConnection(snapshot, WideClient()), definitions)

        normal = await coordinator.execute(parse({
            "command": "initialize_motors", "expected_generation": 4, "idempotency_key": "wide",
        }), mutations_enabled=True)
        assert len(normal.model_dump_json()) < 20_000
        assert len(json.dumps(normal.handler_response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 8_192

        stopped = await coordinator.execute(parse({
            "command": "stop_axis_diagnostic", "axis": "x",
            "expected_generation": 4, "idempotency_key": "stop-huge",
        }), mutations_enabled=True)
        assert len(stopped.detail) <= 4_096
        assert len(stopped.model_dump_json()) < 20_000

        emergency = await coordinator.emergency_stop(
            expected_generation=4, idempotency_key="emergency-huge", mutations_enabled=True,
        )
        assert len(emergency.detail) <= 4_096
        assert len(emergency.model_dump_json()) < 20_000

    asyncio.run(scenario())

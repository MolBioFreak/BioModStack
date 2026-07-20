from __future__ import annotations

import asyncio


def _load():
    from services.bioxp.command_coordinator import CommandCoordinator
    from services.bioxp.command_registry import DEFAULT_COMMAND_REGISTRY
    from services.bioxp.models import BioXpSnapshot

    return CommandCoordinator, DEFAULT_COMMAND_REGISTRY, BioXpSnapshot


class EmergencyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, route_name: str, *, json_data=None, **_: object):
        self.calls.append(route_name)
        return {"acknowledged": True, "physical_effect_verified": False}


class Connection:
    def __init__(self, snapshot, client) -> None:
        self._snapshot = snapshot
        self.active_client = client

    def snapshot(self):
        return self._snapshot


def test_emergency_stop_attempts_with_stale_unknown_readiness_and_is_honest() -> None:
    Coordinator, registry, Snapshot = _load()
    snapshot = Snapshot(
        configured=True,
        active=True,
        generation=9,
        reachable=None,
        runtime_ready=None,
        hardware_ready=None,
        freshness_budget_seconds=30,
        observation_fresh=False,
        observation_stale=True,
    )
    client = EmergencyClient()
    coordinator = Coordinator(Connection(snapshot, client), registry)

    result = asyncio.run(
        coordinator.emergency_stop(
            expected_generation=9,
            idempotency_key="estop-9",
            token_authorized=True,
            mutations_enabled=True,
        )
    )

    assert client.calls == ["emergency_stop"]
    assert result.delivery_attempted is True
    assert result.remote_acknowledged is True
    assert result.physical_effect_verified is False
    assert "not verified" in result.detail.lower()


def test_emergency_stop_generation_or_auth_failure_never_reaches_transport() -> None:
    from services.bioxp.command_coordinator import CommandDeniedError

    Coordinator, registry, Snapshot = _load()
    snapshot = Snapshot(
        configured=True,
        active=True,
        generation=9,
        freshness_budget_seconds=30,
    )
    client = EmergencyClient()
    coordinator = Coordinator(Connection(snapshot, client), registry)

    for generation, authorized in [(8, True), (9, False)]:
        try:
            asyncio.run(
                coordinator.emergency_stop(
                    expected_generation=generation,
                    idempotency_key=f"estop-{generation}-{authorized}",
                    token_authorized=authorized,
                    mutations_enabled=True,
                )
            )
        except CommandDeniedError:
            pass
        else:
            raise AssertionError("emergency stop should have been denied")
    assert client.calls == []


def test_concurrent_same_emergency_idempotency_key_delivers_once() -> None:
    Coordinator, registry, Snapshot = _load()

    class BlockingEmergencyClient(EmergencyClient):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, route_name: str, *, json_data=None, **_: object):
            self.calls.append(route_name)
            self.started.set()
            await self.release.wait()
            return {"acknowledged": "false", "ok": True}

    async def scenario() -> None:
        snapshot = Snapshot(configured=True, active=True, generation=9, freshness_budget_seconds=30)
        client = BlockingEmergencyClient()
        coordinator = Coordinator(Connection(snapshot, client), registry)
        first = asyncio.create_task(
            coordinator.emergency_stop(
                expected_generation=9,
                idempotency_key="same-emergency-key",
                token_authorized=True,
                mutations_enabled=True,
            )
        )
        await client.started.wait()
        second = asyncio.create_task(
            coordinator.emergency_stop(
                expected_generation=9,
                idempotency_key="same-emergency-key",
                token_authorized=True,
                mutations_enabled=True,
            )
        )
        await asyncio.sleep(0)
        client.release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result == second_result
        assert first_result.remote_acknowledged is True
        assert client.calls == ["emergency_stop"]

    asyncio.run(scenario())

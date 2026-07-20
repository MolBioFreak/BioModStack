from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


def _load():
    from services.bioxp.connection import BioXpConnectionService
    from services.bioxp.models import BioXpProfile
    from services.bioxp.profile_store import BioXpProfileStore
    from services.bioxp.target_policy import BioXpTargetPolicy

    return BioXpConnectionService, BioXpProfile, BioXpProfileStore, BioXpTargetPolicy


class FakeRobotClient:
    def __init__(self, target: Any, *, probe_result: dict[str, Any] | None = None, probe_error: Exception | None = None) -> None:
        self.target = target
        self.probe_result = probe_result or {
            "status": "ok",
            "available": True,
            "cache_state": "fresh",
            "freshness": {"state": "fresh", "age_s": 0.0, "fresh_for_s": 30.0},
            "runtime_ready": True,
            "hardware_connected": True,
            "capabilities": ["oem_xml_compile"],
        }
        self.probe_error = probe_error
        self.closed = False
        self.probes = 0

    async def probe(self) -> dict[str, Any]:
        self.probes += 1
        if self.probe_error:
            raise self.probe_error
        return self.probe_result

    async def close(self) -> None:
        self.closed = True


def _service(
    tmp_path: Path,
    clients: list[FakeRobotClient],
    *,
    clock=None,
    probe_result: dict[str, Any] | None = None,
    **service_options,
):
    BioXpConnectionService, _, BioXpProfileStore, BioXpTargetPolicy = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10",)

    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"100.64.0.0/10"},
        resolver=resolver,
    )
    store = BioXpProfileStore(tmp_path / "bioxp" / "profile.json")

    def factory(target):
        client = FakeRobotClient(target, probe_result=probe_result)
        clients.append(client)
        return client

    return BioXpConnectionService(
        store,
        policy,
        client_factory=factory,
        clock=clock,
        initial_generation=0,
        **service_options,
    )


def test_saved_profile_does_not_activate_on_startup_or_restart(tmp_path: Path) -> None:
    BioXpConnectionService, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)
    asyncio.run(service.save_profile(BioXpProfile(display_name="BioXP3200", api_url="http://robot:8123")))

    first = service.snapshot()
    restarted = BioXpConnectionService(
        service.profile_store,
        service.target_policy,
        client_factory=lambda target: FakeRobotClient(target),
        initial_generation=0,
    )
    second = restarted.snapshot()

    assert first.configured is True and first.active is False
    assert second.configured is True and second.active is False
    assert first.reachable is None and second.reachable is None
    assert clients == []


def test_connect_uses_only_saved_profile_and_failed_probe_is_unknown_not_ready(tmp_path: Path) -> None:
    BioXpConnectionService, BioXpProfile, BioXpProfileStore, BioXpTargetPolicy = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10",)

    policy = BioXpTargetPolicy(allowed_hosts={"robot"}, allowed_cidrs={"100.64.0.0/10"}, resolver=resolver)
    store = BioXpProfileStore(tmp_path / "profile.json")
    clients: list[FakeRobotClient] = []

    def factory(target):
        client = FakeRobotClient(target, probe_error=RuntimeError("offline"))
        clients.append(client)
        return client

    service = BioXpConnectionService(store, policy, client_factory=factory, initial_generation=0)
    with pytest.raises(Exception, match="saved profile"):
        asyncio.run(service.connect())

    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    snapshot = asyncio.run(service.connect())

    assert snapshot.active is True
    assert snapshot.reachable is False
    assert snapshot.runtime_ready is None
    assert snapshot.hardware_ready is None
    assert snapshot.last_error == "offline"
    assert snapshot.generation == 1


def test_disconnect_closes_client_increments_generation_and_clears_observation(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    connected = asyncio.run(service.connect())
    disconnected = asyncio.run(service.disconnect())

    assert connected.reachable is True
    assert clients[0].closed is True
    assert disconnected.generation == connected.generation + 1
    assert disconnected.active is False
    assert disconnected.reachable is None
    assert disconnected.runtime_ready is None
    assert disconnected.hardware_ready is None
    assert disconnected.observed_at is None
    assert disconnected.capabilities == ()


def test_stale_observation_is_explicit(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients, clock=lambda: now)
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    asyncio.run(service.connect())

    service.clock = lambda: now + timedelta(seconds=31)
    snapshot = service.snapshot()

    assert snapshot.observation_fresh is False
    assert snapshot.observation_stale is True
    assert snapshot.reachable is None
    assert snapshot.last_observed_reachable is True


def test_robot_stale_cache_cannot_be_relabelled_as_fresh_readiness(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    clients: list[FakeRobotClient] = []
    service = _service(
        tmp_path,
        clients,
        clock=lambda: now,
        probe_result={
            "status": "ok",
            "available": False,
            "cache_state": "stale",
            "freshness": {"state": "stale", "age_s": 600.0, "fresh_for_s": 30.0},
            "runtime_available": True,
            "hardware_connected": True,
            "capabilities": [
                "collect_hardware_snapshot",
                "construct_pipettes",
                "initialize_without_motion",
                "run_initial_check",
            ],
        },
    )
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    snapshot = asyncio.run(service.connect())

    assert snapshot.observation_fresh is False
    assert snapshot.observation_stale is True
    assert snapshot.reachable is None
    assert snapshot.last_observed_reachable is True
    assert snapshot.runtime_ready is None
    assert snapshot.hardware_ready is None
    assert snapshot.capabilities == ()
    assert snapshot.last_error is not None
    assert "stale" in snapshot.last_error.lower()


def test_explicit_active_connection_monitor_renews_observation_and_stops_on_disconnect(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []

    async def scenario() -> None:
        service = _service(
            tmp_path,
            clients,
            freshness_budget_seconds=0.05,
            active_probe_interval_seconds=0.01,
        )
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        await service.connect()
        await asyncio.sleep(0.08)

        assert service.snapshot().observation_fresh is True
        assert clients[0].probes >= 3

        await service.disconnect()
        stopped_at = clients[0].probes
        await asyncio.sleep(0.04)
        assert clients[0].probes == stopped_at

    asyncio.run(scenario())


def test_concurrent_connect_and_disconnect_leave_one_coherent_generation(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))

    async def race():
        return await asyncio.gather(service.connect(), service.disconnect())

    asyncio.run(race())
    snapshot = service.snapshot()

    assert snapshot.generation == 2
    assert snapshot.active is False
    assert all(client.closed for client in clients)


def test_masked_target_never_returns_short_host_and_preserves_ipv6_brackets() -> None:
    from services.bioxp.connection import mask_target_url

    assert mask_target_url("http://lab:8123") == "http://***:8123"
    assert mask_target_url("https://[fd00::1]:8123") == "https://[fd***1]:8123"


def test_dns_failure_deactivates_and_records_operator_visible_error(tmp_path: Path) -> None:
    BioXpConnectionService, BioXpProfile, BioXpProfileStore, BioXpTargetPolicy = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        raise OSError("resolver unavailable")

    service = BioXpConnectionService(
        BioXpProfileStore(tmp_path / "profile.json"),
        BioXpTargetPolicy(
            allowed_hosts={"robot"},
            allowed_cidrs={"100.64.0.0/10"},
            resolver=resolver,
        ),
        initial_generation=0,
    )
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))

    with pytest.raises(Exception, match="DNS resolution failed"):
        asyncio.run(service.connect())

    snapshot = service.snapshot()
    assert snapshot.active is False
    assert snapshot.reachable is None
    assert snapshot.runtime_ready is None
    assert snapshot.hardware_ready is None
    assert snapshot.last_error == "BioXP target DNS resolution failed"

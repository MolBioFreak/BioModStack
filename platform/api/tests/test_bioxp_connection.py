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
        self.status_only_probes = 0
        self.request_started: asyncio.Event | None = None
        self.request_release: asyncio.Event | None = None
        self.blocking_route_name: str | None = None
        self.blocking_action_id: str | None = None
        self.status_probe_started: asyncio.Event | None = None
        self.status_probe_release: asyncio.Event | None = None

    async def probe(self) -> dict[str, Any]:
        self.probes += 1
        if self.probe_error:
            raise self.probe_error
        return self.probe_result

    async def probe_status_only(self) -> dict[str, Any]:
        self.status_only_probes += 1
        if self.status_probe_started is not None:
            self.status_probe_started.set()
        if self.status_probe_release is not None:
            await self.status_probe_release.wait()
        if self.probe_error:
            raise self.probe_error
        return self.probe_result

    async def close(self) -> None:
        self.closed = True

    async def request(self, route_name: str, **kwargs: Any) -> dict[str, Any]:
        if self.request_started is not None:
            self.request_started.set()
        if self.request_release is not None and (
            self.blocking_route_name is None
            or (
                route_name == self.blocking_route_name
                and (
                    self.blocking_action_id is None
                    or (kwargs.get("path_params") or {}).get("action_id") == self.blocking_action_id
                )
            )
        ):
            await self.request_release.wait()
        return {"route_name": route_name, "kwargs": kwargs}


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


def test_runtime_start_restores_saved_managed_connection(monkeypatch) -> None:
    from services.bioxp.runtime import BioXpRuntime

    monkeypatch.setenv("BMS_BIOXP_CONNECTION_ENABLED", "1")

    class Connection:
        connected = 0

        def load_profile(self):
            return object()

        async def connect(self):
            self.connected += 1

    connection = Connection()
    runtime = BioXpRuntime(connection=connection, commands=None, jobs=None)  # type: ignore[arg-type]

    asyncio.run(runtime.start())

    assert connection.connected == 1
    assert runtime.startup_warnings == []


def test_runtime_start_keeps_failed_restore_truthful_without_crashing_api(monkeypatch) -> None:
    from services.bioxp.runtime import BioXpRuntime

    monkeypatch.setenv("BMS_BIOXP_CONNECTION_ENABLED", "1")

    class Connection:
        def load_profile(self):
            return object()

        async def connect(self):
            raise RuntimeError("robot offline")

    runtime = BioXpRuntime(connection=Connection(), commands=None, jobs=None)  # type: ignore[arg-type]

    asyncio.run(runtime.start())

    assert runtime.startup_warnings == ["Saved BioXP profile was not restored: robot offline"]


def test_runtime_start_does_not_contact_saved_robot_when_connection_access_is_disabled(monkeypatch) -> None:
    from services.bioxp.runtime import BioXpRuntime

    monkeypatch.setenv("BMS_BIOXP_CONNECTION_ENABLED", "0")

    class Connection:
        connected = 0

        def load_profile(self):
            return object()

        async def connect(self):
            self.connected += 1

    connection = Connection()
    runtime = BioXpRuntime(connection=connection, commands=None, jobs=None)  # type: ignore[arg-type]

    asyncio.run(runtime.start())

    assert connection.connected == 0
    assert runtime._restore_task is None


def test_runtime_start_does_not_wait_for_saved_robot_probe(monkeypatch) -> None:
    from services.bioxp.runtime import BioXpRuntime

    monkeypatch.setenv("BMS_BIOXP_CONNECTION_ENABLED", "1")

    class Connection:
        entered = asyncio.Event()
        release = asyncio.Event()

        def load_profile(self):
            return object()

        async def connect(self):
            self.entered.set()
            await self.release.wait()

    async def scenario() -> None:
        connection = Connection()
        runtime = BioXpRuntime(connection=connection, commands=None, jobs=None)  # type: ignore[arg-type]
        start_task = asyncio.create_task(runtime.start())
        await asyncio.wait_for(connection.entered.wait(), timeout=0.1)
        await asyncio.sleep(0)
        assert start_task.done() is True
        connection.release.set()
        await start_task
        await asyncio.sleep(0)

    asyncio.run(scenario())


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
    from services.bioxp.connection import ConnectionStateError

    with pytest.raises(ConnectionStateError, match="offline"):
        asyncio.run(service.connect())
    snapshot = service.snapshot()

    assert snapshot.active is False
    assert snapshot.reachable is None
    assert snapshot.runtime_ready is None
    assert snapshot.hardware_ready is None
    assert snapshot.last_error == "offline"
    assert snapshot.generation == 1
    assert clients[0].closed is True


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
    assert disconnected.maintenance_state is None


def test_maintenance_state_projects_from_status_and_nested_command_or_error_responses(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(
        tmp_path,
        clients,
        probe_result={
            "status": "ok",
            "available": True,
            "cache_state": "fresh",
            "freshness": {"state": "fresh", "age_s": 0.0, "fresh_for_s": 30.0},
            "runtime_ready": True,
            "hardware_connected": True,
            "capabilities": ["recover_motion_non_homing"],
            "maintenance_state": {
                "motion_blocked": True,
                "recovery_required": True,
                "block_reason": "USB owner changed",
            },
        },
    )
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    connected = asyncio.run(service.connect())
    assert connected.maintenance_state == {
        "motion_blocked": True,
        "recovery_required": True,
        "block_reason": "USB owner changed",
    }

    service.observe_command_response({
        "detail": {
            "error": "post_maintenance_motion_recovery_required",
            "maintenance_state": {
                "motion_blocked": False,
                "recovery_required": False,
                "block_reason": None,
            },
        },
    })
    assert service.snapshot().maintenance_state == {
        "motion_blocked": False,
        "recovery_required": False,
        "block_reason": None,
    }

    asyncio.run(service.disconnect())
    assert service.snapshot().maintenance_state is None


def test_query_request_does_not_block_critical_command_dispatch(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)

    async def exercise() -> None:
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        connected = await service.connect()
        robot = clients[0]
        robot.request_started = asyncio.Event()
        robot.request_release = asyncio.Event()
        robot.blocking_route_name = "operator_control_catalog"

        query_task = asyncio.create_task(service.request_active_query(
            "operator_control_catalog",
            expected_generation=connected.generation,
        ))
        await robot.request_started.wait()

        command = await asyncio.wait_for(service.request_active(
            "invoke_operator_action",
            expected_generation=connected.generation,
            path_params={"action_id": "oem.z.manual_home"},
            json_data={"expected_generation": 7, "idempotency_key": "home-action", "inputs": {}},
        ), timeout=0.1)
        assert command["route_name"] == "invoke_operator_action"
        assert query_task.done() is False

        robot.request_release.set()
        query = await query_task
        assert query["route_name"] == "operator_control_catalog"

    asyncio.run(exercise())


def test_generation_bound_request_lease_serializes_disconnect(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)

    async def exercise() -> None:
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        connected = await service.connect()
        robot = clients[0]
        robot.request_started = asyncio.Event()
        robot.request_release = asyncio.Event()

        request_task = asyncio.create_task(service.request_active(
            "plan_oem_full_lifecycle",
            expected_generation=connected.generation,
            json_data={"expected_generation": connected.generation},
        ))
        await robot.request_started.wait()
        disconnect_task = asyncio.create_task(service.disconnect())
        await asyncio.sleep(0)
        assert disconnect_task.done() is False
        assert service.snapshot().generation == connected.generation

        robot.request_release.set()
        response = await request_task
        assert response["route_name"] == "plan_oem_full_lifecycle"
        disconnected = await disconnect_task
        assert disconnected.generation == connected.generation + 1
        assert disconnected.active is False

    asyncio.run(exercise())


def test_safety_interrupt_bypasses_active_request_lease_without_allowing_disconnect(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)

    async def exercise() -> None:
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        connected = await service.connect()
        robot = clients[0]
        robot.request_started = asyncio.Event()
        robot.request_release = asyncio.Event()
        robot.blocking_route_name = "invoke_operator_action"
        robot.blocking_action_id = "oem.z.diagnostic_home_axis"

        movement_task = asyncio.create_task(service.request_active(
            "invoke_operator_action",
            expected_generation=connected.generation,
            path_params={"action_id": "oem.z.diagnostic_home_axis"},
            json_data={"expected_generation": 7, "idempotency_key": "home-action", "inputs": {}},
        ))
        await robot.request_started.wait()

        stop = await asyncio.wait_for(service.request_active_safety_interrupt(
            "invoke_operator_action",
            expected_generation=connected.generation,
            path_params={"action_id": "oem.z.stop"},
            json_data={"expected_generation": 7, "idempotency_key": "stop-action", "inputs": {}},
        ), timeout=0.1)
        assert stop["kwargs"]["path_params"] == {"action_id": "oem.z.stop"}
        assert movement_task.done() is False

        disconnect_task = asyncio.create_task(service.disconnect())
        await asyncio.sleep(0)
        assert disconnect_task.done() is False

        robot.request_release.set()
        await movement_task
        disconnected = await disconnect_task
        assert disconnected.generation == connected.generation + 1

    asyncio.run(exercise())


def test_multi_request_lease_serializes_disconnect_until_canonical_readback(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []
    service = _service(tmp_path, clients)

    async def exercise() -> None:
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        connected = await service.connect()

        async with service.active_request_lease(
            expected_generation=connected.generation,
        ) as robot:
            assert (await robot.request("oem_full_lifecycle_contract"))["route_name"] == "oem_full_lifecycle_contract"
            disconnect_task = asyncio.create_task(service.disconnect())
            await asyncio.sleep(0)
            assert disconnect_task.done() is False
            assert (await robot.request("plan_oem_full_lifecycle"))["route_name"] == "plan_oem_full_lifecycle"
            assert (await robot.request("get_oem_full_lifecycle_run"))["route_name"] == "get_oem_full_lifecycle_run"
            assert disconnect_task.done() is False

        disconnected = await disconnect_task
        assert disconnected.generation == connected.generation + 1
        assert disconnected.active is False

    asyncio.run(exercise())


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


def test_stale_hardware_cache_does_not_relabel_live_runtime_probe_as_stale(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    clients: list[FakeRobotClient] = []
    service = _service(
        tmp_path,
        clients,
        clock=lambda: now,
        probe_result={
            "status": "degraded",
            "available": True,
            "cache_state": "stale",
            "freshness": {"state": "stale", "age_s": 600.0, "fresh_for_s": 30.0},
            "runtime_available": True,
            "hardware_connected": True,
            "capabilities": [
                "collect_hardware_snapshot",
                "initialize_oem_environment",
            ],
        },
    )
    asyncio.run(service.save_profile(BioXpProfile(api_url="http://robot:8123")))
    snapshot = asyncio.run(service.connect())

    assert snapshot.observation_fresh is True
    assert snapshot.observation_stale is False
    assert snapshot.reachable is True
    assert snapshot.runtime_ready is True
    assert snapshot.hardware_ready is None
    assert snapshot.hardware_observation_fresh is False
    assert snapshot.hardware_observation_stale is True
    assert snapshot.hardware_observed_at == now - timedelta(seconds=600)
    assert snapshot.capabilities == (
        "collect_hardware_snapshot",
        "initialize_oem_environment",
    )
    assert snapshot.last_error is None
    assert snapshot.hardware_evidence_error is not None
    assert "stale" in snapshot.hardware_evidence_error.lower()


def test_connection_and_active_monitor_are_status_only_and_stop_on_disconnect(tmp_path: Path) -> None:
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
        assert clients[0].status_only_probes >= 3
        assert clients[0].probes == 0

        await service.disconnect()
        stopped_at = clients[0].status_only_probes
        await asyncio.sleep(0.04)
        assert clients[0].status_only_probes == stopped_at

    asyncio.run(scenario())


def test_active_monitor_probe_does_not_block_critical_command_dispatch(tmp_path: Path) -> None:
    _, BioXpProfile, _, _ = _load()
    clients: list[FakeRobotClient] = []

    async def scenario() -> None:
        service = _service(tmp_path, clients, active_probe_interval_seconds=0.01)
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        connected = await service.connect()
        robot = clients[0]
        robot.status_probe_started = asyncio.Event()
        robot.status_probe_release = asyncio.Event()
        await robot.status_probe_started.wait()

        command = await asyncio.wait_for(service.request_active(
            "invoke_operator_action",
            expected_generation=connected.generation,
            path_params={"action_id": "oem.z.manual_home"},
            json_data={"expected_generation": 7, "idempotency_key": "home-action", "inputs": {}},
        ), timeout=0.1)
        assert command["route_name"] == "invoke_operator_action"

        robot.status_probe_release.set()
        await service.disconnect()

    asyncio.run(scenario())


def test_status_only_connection_fails_closed_without_status_only_client_method(tmp_path: Path) -> None:
    BioXpConnectionService, BioXpProfile, BioXpProfileStore, BioXpTargetPolicy = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10",)

    class RichProbeOnlyClient:
        rich_probes = 0

        async def probe(self):
            self.rich_probes += 1
            raise AssertionError("rich probe must not be called by connection-only admission")

        async def close(self) -> None:
            return None

    client = RichProbeOnlyClient()
    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"100.64.0.0/10"},
        resolver=resolver,
    )
    store = BioXpProfileStore(tmp_path / "profile.json")
    service = BioXpConnectionService(
        store,
        policy,
        client_factory=lambda _: client,  # type: ignore[arg-type,return-value]
        initial_generation=0,
    )
    store.save(BioXpProfile(api_url="http://robot:8123", display_name="BioXP 3200"))

    with pytest.raises(Exception, match="probe_status_only"):
        asyncio.run(service.connect())

    assert client.rich_probes == 0


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

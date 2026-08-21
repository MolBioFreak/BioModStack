from __future__ import annotations

import asyncio
import copy
import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Protocol
from urllib.parse import urlsplit

from .errors import ConnectionStateError, ProfileStoreError, RobotTimeoutError, TargetPolicyError
from .models import (
    DEFAULT_BIOXP_FRESHNESS_BUDGET_SECONDS,
    BioXpProfile,
    BioXpSnapshot,
)
from .profile_store import BioXpProfileStore
from .robot_client import BioXpRobotClient, CameraImage, RobotBytesResponse
from .target_policy import BioXpTargetPolicy, ValidatedBioXpTarget


class RobotClientProtocol(Protocol):
    async def probe(self) -> dict[str, Any]: ...

    async def probe_status_only(self) -> dict[str, Any]: ...

    async def request(
        self,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any]: ...

    async def request_bytes(
        self,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> RobotBytesResponse: ...

    async def camera_status(self) -> dict[str, Any]: ...

    async def camera_latest(self) -> CameraImage: ...

    async def camera_snapshot(self) -> CameraImage: ...

    async def camera_stream_start(self) -> dict[str, Any]: ...

    async def camera_stream_state(self) -> dict[str, Any]: ...

    async def camera_stream_stop(self) -> dict[str, Any]: ...

    def camera_mjpeg_stream(self) -> Any: ...

    async def close(self) -> None: ...


ClientFactory = Callable[[ValidatedBioXpTarget], RobotClientProtocol]
Clock = Callable[[], datetime]


@dataclass(slots=True)
class _GenerationLease:
    generation: int
    client: RobotClientProtocol
    state: str = "OPEN"
    lease_count: int = 0
    zero_lease_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.zero_lease_event.set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mask_target_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if len(host) <= 4:
        masked = "*" * len(host)
    else:
        masked = f"{host[:2]}***{host[-1:]}"
    if ":" in host:
        masked = f"[{masked}]"
    return f"{parsed.scheme}://{masked}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{masked}"


class BioXpConnectionService:
    """The sole owner of active target, generation, client, and observations."""

    def __init__(
        self,
        profile_store: BioXpProfileStore,
        target_policy: BioXpTargetPolicy,
        *,
        client_factory: ClientFactory | None = None,
        freshness_budget_seconds: float | None = DEFAULT_BIOXP_FRESHNESS_BUDGET_SECONDS,
        active_probe_interval_seconds: float | None = None,
        clock: Clock | None = None,
        initial_generation: int | None = None,
        v2_enqueue_timeout_seconds: float = 5.0,
        interrupt_timeout_seconds: float = 10.0,
    ) -> None:
        self.profile_store = profile_store
        self.target_policy = target_policy
        self.client_factory = client_factory or (lambda target: BioXpRobotClient(target))
        self.freshness_budget_seconds = freshness_budget_seconds
        if active_probe_interval_seconds is not None and active_probe_interval_seconds <= 0:
            raise ValueError("active probe interval must be positive")
        if v2_enqueue_timeout_seconds <= 0 or interrupt_timeout_seconds <= 0:
            raise ValueError("BioXP request timeouts must be positive")
        self.active_probe_interval_seconds = active_probe_interval_seconds
        self.v2_enqueue_timeout_seconds = v2_enqueue_timeout_seconds
        self.interrupt_timeout_seconds = interrupt_timeout_seconds
        self.clock = clock or _utcnow
        self._transition_lock = asyncio.Lock()
        self._v1_workflow_lock = asyncio.Lock()
        self._v2_enqueue_lock = asyncio.Lock()
        self._v2_query_lock = asyncio.Lock()
        self._interrupt_lock = asyncio.Lock()
        self._client: RobotClientProtocol | None = None
        self._active_target: ValidatedBioXpTarget | None = None
        # Opaque per-process epoch: delayed requests from a previous process
        # cannot match the first connection generation after restart.
        self._generation = initial_generation if initial_generation is not None else max(1, secrets.randbits(40) << 12)
        self._observed_at: datetime | None = None
        self._last_reachable: bool | None = None
        self._last_runtime_ready: bool | None = None
        self._last_hardware_ready: bool | None = None
        self._hardware_observed_at: datetime | None = None
        self._hardware_observation_fresh: bool | None = None
        self._hardware_evidence_error: str | None = None
        self._automatic_snapshot_refresh: dict[str, Any] | None = None
        self._capabilities: tuple[str, ...] = ()
        self._startup_lifecycle: dict[str, Any] | None = None
        self._maintenance_state: dict[str, Any] | None = None
        self._ownership: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._active_probe_task: asyncio.Task[None] | None = None
        self._snapshot_refresh_task: asyncio.Task[None] | None = None
        self._generation_leases: dict[int, _GenerationLease] = {}
        self._drain_tasks: set[asyncio.Task[None]] = set()
        self._remote_request_tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._profile_revision = 0

    async def save_profile(self, profile: BioXpProfile) -> BioXpSnapshot:
        canonical = self.target_policy.validate(profile.api_url)
        async with self._transition_lock:
            existing = self.profile_store.load()
            freshness_budget_seconds = (
                profile.freshness_budget_seconds
                if "freshness_budget_seconds" in profile.model_fields_set or existing is None
                else existing.freshness_budget_seconds
            )
            normalized = profile.model_copy(update={
                "api_url": canonical.api_url,
                "freshness_budget_seconds": freshness_budget_seconds,
            })
            await self._deactivate_locked(increment=bool(self._client or self._active_target))
            self.profile_store.save(normalized)
            self._profile_revision += 1
            self.freshness_budget_seconds = normalized.freshness_budget_seconds
        return self.snapshot()

    async def forget_profile(self) -> BioXpSnapshot:
        async with self._transition_lock:
            await self._deactivate_locked(increment=True)
            self.profile_store.forget()
            self._profile_revision += 1
            self.freshness_budget_seconds = DEFAULT_BIOXP_FRESHNESS_BUDGET_SECONDS
        return self.snapshot()

    async def set_freshness_budget_seconds(self, value: float | None) -> BioXpSnapshot:
        """Persist the BMS age-expiry policy without reconnecting or touching hardware."""
        async with self._transition_lock:
            profile = self.profile_store.load()
            if profile is None:
                raise ConnectionStateError("Save a BioXP profile before changing freshness policy")
            updated = profile.model_copy(update={"freshness_budget_seconds": value})
            self.profile_store.save(updated)
            self._profile_revision += 1
            self.freshness_budget_seconds = value
        return self.snapshot()

    async def connect(self) -> BioXpSnapshot:
        async with self._transition_lock:
            profile = self.profile_store.load()
            profile_revision = self._profile_revision
            starting_client = self._client
            starting_generation = self._generation
        if profile is None:
            raise ConnectionStateError("Save a BioXP saved profile before connecting")
        try:
            target = await self.target_policy.validate_for_connection(profile.api_url)
        except TargetPolicyError as exc:
            async with self._transition_lock:
                if self._connect_context_matches_locked(
                    profile,
                    profile_revision,
                    starting_client,
                    starting_generation,
                ):
                    await self._deactivate_locked(increment=bool(self._client or self._active_target))
                    self._last_error = str(exc)
            raise
        candidate = self.client_factory(target)
        try:
            payload = await candidate.probe_status_only()
        except Exception as exc:
            await candidate.close()
            async with self._transition_lock:
                if not self._connect_context_matches_locked(
                    profile,
                    profile_revision,
                    starting_client,
                    starting_generation,
                ):
                    raise ConnectionStateError("BioXP profile or connection changed during connect") from exc
                await self._deactivate_locked(increment=True)
                self._last_error = str(exc) or exc.__class__.__name__
            raise ConnectionStateError(str(exc) or "BioXP robot probe failed") from exc
        async with self._transition_lock:
            if not self._connect_context_matches_locked(
                profile,
                profile_revision,
                starting_client,
                starting_generation,
            ):
                stale_candidate = True
            else:
                self._stop_active_probe_locked()
                self._stop_snapshot_refresh_locked()
                self._mark_current_draining_locked(increment=False)
                self._generation += 1
                self.freshness_budget_seconds = profile.freshness_budget_seconds
                self._active_target = target
                self._client = candidate
                self._generation_leases[self._generation] = _GenerationLease(self._generation, candidate)
                self._apply_probe_payload(payload)
                self._start_active_probe_locked()
                self._start_snapshot_refresh_locked()
                stale_candidate = False
        if stale_candidate:
            await candidate.close()
            raise ConnectionStateError("BioXP profile or connection changed during connect")
        return self.snapshot()

    def _connect_context_matches_locked(
        self,
        profile: BioXpProfile,
        profile_revision: int,
        starting_client: RobotClientProtocol | None,
        starting_generation: int,
    ) -> bool:
        return (
            self._profile_revision == profile_revision
            and self._client is starting_client
            and self._generation == starting_generation
            and self.profile_store.load() == profile
        )

    async def probe(self) -> BioXpSnapshot:
        return await self._probe_and_rebind(status_only=False)

    async def probe_status_only(self) -> BioXpSnapshot:
        return await self._probe_and_rebind(status_only=True)

    async def _probe_and_rebind(self, *, status_only: bool) -> BioXpSnapshot:
        async with self._transition_lock:
            client = self._client
            target = self._active_target
            generation = self._generation
            if client is None or target is None:
                raise ConnectionStateError("BioXP saved profile is not actively connected")
        try:
            validated = await self.target_policy.validate_for_connection(target.api_url)
        except TargetPolicyError as exc:
            async with self._transition_lock:
                if client is self._client and generation == self._generation:
                    await self._deactivate_locked(increment=True)
                    self._last_error = str(exc)
            raise

        if validated != target:
            candidate = self.client_factory(validated)
            try:
                payload = (
                    await candidate.probe_status_only()
                    if status_only
                    else await candidate.probe()
                )
            except Exception as exc:
                await candidate.close()
                async with self._transition_lock:
                    if client is self._client and generation == self._generation:
                        await self._deactivate_locked(increment=True)
                        self._record_probe_failure(exc)
                return self.snapshot()
            async with self._transition_lock:
                if client is not self._client or generation != self._generation:
                    close_candidate = True
                else:
                    self._stop_active_probe_locked()
                    self._stop_snapshot_refresh_locked()
                    self._mark_current_draining_locked(increment=False)
                    self._generation += 1
                    self._client = candidate
                    self._active_target = validated
                    self._generation_leases[self._generation] = _GenerationLease(self._generation, candidate)
                    self._apply_probe_payload(payload)
                    self._start_active_probe_locked()
                    self._start_snapshot_refresh_locked()
                    close_candidate = False
            if close_candidate:
                await candidate.close()
            return self.snapshot()

        async with self._transition_lock:
            if client is not self._client or generation != self._generation:
                raise ConnectionStateError("BioXP connection generation changed during probe")
            lease = self._acquire_lease_locked(generation, require_fresh=False)
        try:
            payload = await client.probe_status_only() if status_only else await client.probe()
        except Exception as exc:
            async with self._transition_lock:
                if client is self._client and generation == self._generation:
                    self._record_probe_failure(exc)
            return self.snapshot()
        finally:
            await self._release_lease(lease)
        async with self._transition_lock:
            if client is self._client and generation == self._generation:
                self._apply_probe_payload(payload)
        return self.snapshot()

    async def request_active(
        self,
        route_name: str,
        *,
        expected_generation: int,
        require_fresh: bool = True,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch against one generation while excluding disconnect/rebind."""
        async with self.active_request_lease(
            expected_generation=expected_generation,
            require_fresh=require_fresh,
        ) as client:
            async with self._v1_workflow_lock:
                return await self._request_client(
                    client,
                    route_name,
                    json_data=json_data,
                    params=params,
                    path_params=path_params,
                )

    async def request_active_v2_enqueue(
        self,
        route_name: str,
        *,
        expected_generation: int,
        json_data: dict[str, Any],
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._request_with_retained_timeout(
            lane_lock=self._v2_enqueue_lock,
            timeout_seconds=self.v2_enqueue_timeout_seconds,
            timeout_label="BioXP v2 enqueue lane",
            route_name=route_name,
            expected_generation=expected_generation,
            require_fresh=True,
            json_data=json_data,
            path_params=path_params,
        )

    async def request_active_bytes(
        self,
        route_name: str,
        *,
        expected_generation: int,
        require_fresh: bool = True,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> RobotBytesResponse:
        async with self.active_request_lease(
            expected_generation=expected_generation,
            require_fresh=require_fresh,
        ) as client:
            return await client.request_bytes(
                route_name,
                json_data=json_data,
                params=params,
                path_params=path_params,
                max_bytes=max_bytes,
            )

    async def request_active_query(
        self,
        route_name: str,
        *,
        expected_generation: int,
        require_fresh: bool = True,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one query without holding the command/lifecycle lock remotely."""
        async with self.active_query_lease(
            expected_generation=expected_generation,
            require_fresh=require_fresh,
        ) as client:
            kwargs: dict[str, Any] = {}
            if json_data is not None:
                kwargs["json_data"] = json_data
            if params is not None:
                kwargs["params"] = params
            if path_params is not None:
                kwargs["path_params"] = path_params
            return await client.request(route_name, **kwargs)

    async def request_active_v2_query(
        self,
        route_name: str,
        *,
        expected_generation: int,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with self.active_query_lease(
            expected_generation=expected_generation,
            require_fresh=True,
        ) as client:
            try:
                async with asyncio.timeout(15.0):
                    async with self._v2_query_lock:
                        return await self._request_client(
                            client,
                            route_name,
                            params=params,
                            path_params=path_params,
                            timeout_override=12.0,
                        )
            except TimeoutError as exc:
                raise RobotTimeoutError(
                    "BioXP v2 query lane timed out before a robot response was received",
                    dispatched=False,
                ) from exc

    @asynccontextmanager
    async def active_query_lease(
        self,
        *,
        expected_generation: int,
        require_fresh: bool = True,
    ) -> AsyncIterator[RobotClientProtocol]:
        async with self._transition_lock:
            lease = self._acquire_lease_locked(expected_generation, require_fresh=require_fresh)
        try:
            yield lease.client
        finally:
            await self._release_lease(lease)

    async def request_active_safety_interrupt(
        self,
        route_name: str,
        *,
        expected_generation: int,
        json_data: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch an exact interrupt without waiting behind normal request owners."""
        action_id = path_params.get("action_id") if path_params else None
        safety_actions = {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort", "oem.y.stop"}
        if route_name not in {"invoke_operator_action", "interrupt_operator_action_v1"} or action_id not in safety_actions:
            raise ValueError("BioXP safety-interrupt transport is reserved for exact axis stop and aggregate abort actions")
        return await self._request_with_retained_timeout(
            lane_lock=self._interrupt_lock,
            timeout_seconds=self.interrupt_timeout_seconds,
            timeout_label="BioXP interrupt lane",
            route_name=route_name,
            expected_generation=expected_generation,
            require_fresh=False,
            json_data=json_data,
            path_params=path_params,
        )

    async def _request_with_retained_timeout(
        self,
        *,
        lane_lock: asyncio.Lock,
        timeout_seconds: float,
        timeout_label: str,
        route_name: str,
        expected_generation: int,
        require_fresh: bool,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with self._transition_lock:
            lease = self._acquire_lease_locked(expected_generation, require_fresh=require_fresh)
        dispatched = asyncio.Event()

        async def owned_request() -> dict[str, Any]:
            try:
                async with lane_lock:
                    dispatched.set()
                    return await self._request_client(
                        lease.client,
                        route_name,
                        json_data=json_data,
                        params=params,
                        path_params=path_params,
                    )
            finally:
                await self._release_lease(lease)

        task = asyncio.create_task(owned_request(), name=f"bioxp-retained-{route_name}")
        self._remote_request_tasks.add(task)

        def consume_late_result(done: asyncio.Task[dict[str, Any]]) -> None:
            self._remote_request_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(consume_late_result)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except TimeoutError as exc:
            was_dispatched = dispatched.is_set()
            if not was_dispatched:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            guidance = (
                "delivery and outcome are ambiguous; do not retry until current v2 dashboard and receipt status are queried"
                if was_dispatched
                else "timed out before dispatch; no robot request was started"
            )
            raise RobotTimeoutError(
                f"{timeout_label} timed out: {guidance}",
                dispatched=was_dispatched,
            ) from exc

    @staticmethod
    async def _request_client(
        client: RobotClientProtocol,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if json_data is not None:
            kwargs["json_data"] = json_data
        if params is not None:
            kwargs["params"] = params
        if path_params is not None:
            kwargs["path_params"] = path_params
        if timeout_override is not None:
            kwargs["timeout_override"] = timeout_override
        return await client.request(route_name, **kwargs)

    @asynccontextmanager
    async def active_request_lease(
        self,
        *,
        expected_generation: int,
        require_fresh: bool = True,
    ) -> AsyncIterator[RobotClientProtocol]:
        """Hold one active connection generation across a remote transaction."""
        async with self._transition_lock:
            lease = self._acquire_lease_locked(expected_generation, require_fresh=require_fresh)
        try:
            yield lease.client
        finally:
            await self._release_lease(lease)

    def _acquire_lease_locked(self, expected_generation: int, *, require_fresh: bool) -> _GenerationLease:
        if self._client is None or self._active_target is None:
            raise ConnectionStateError("BioXP saved profile is not actively connected")
        if self._generation != expected_generation:
            raise ConnectionStateError("Expected connection generation does not match the active generation")
        if require_fresh:
            snapshot = self.snapshot()
            if snapshot.observation_fresh is not True or snapshot.reachable is not True:
                raise ConnectionStateError("A fresh reachable process-local BioXP status observation is required")
        lease = self._generation_leases.get(self._generation)
        if lease is None or lease.client is not self._client or lease.state != "OPEN":
            lease = _GenerationLease(self._generation, self._client)
            self._generation_leases[self._generation] = lease
        lease.lease_count += 1
        lease.zero_lease_event.clear()
        return lease

    async def _release_lease(self, lease: _GenerationLease) -> None:
        async with self._transition_lock:
            if lease.lease_count > 0:
                lease.lease_count -= 1
            if lease.lease_count == 0:
                lease.zero_lease_event.set()

    def _schedule_drain_locked(self, lease: _GenerationLease) -> None:
        task = asyncio.create_task(self._close_drained_lease(lease), name=f"bioxp-close-generation-{lease.generation}")
        self._drain_tasks.add(task)
        task.add_done_callback(self._drain_tasks.discard)

    async def _close_drained_lease(self, lease: _GenerationLease) -> None:
        await lease.zero_lease_event.wait()
        await lease.client.close()
        async with self._transition_lock:
            lease.state = "CLOSED"
            if self._generation_leases.get(lease.generation) is lease:
                self._generation_leases.pop(lease.generation, None)

    async def _wait_for_drains(self) -> None:
        tasks = tuple(self._drain_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    def _apply_probe_payload(self, payload: dict[str, Any]) -> None:
        automatic_snapshot_refresh = payload.get("automatic_snapshot_refresh")
        if isinstance(automatic_snapshot_refresh, dict):
            self._automatic_snapshot_refresh = copy.deepcopy(automatic_snapshot_refresh)
        startup = payload.get("startup")
        self._startup_lifecycle = copy.deepcopy(startup) if isinstance(startup, dict) else None
        found_maintenance, maintenance_state = _find_maintenance_state(payload)
        self._maintenance_state = maintenance_state if found_maintenance else None
        ownership = payload.get("ownership")
        self._ownership = copy.deepcopy(ownership) if isinstance(ownership, dict) else None
        raw_capabilities = payload.get("capabilities")
        self._capabilities = (
            tuple(sorted({str(value) for value in raw_capabilities}))
            if isinstance(raw_capabilities, (list, tuple, set))
            else ()
        )
        now = self.clock()
        self._last_reachable = True
        self._observed_at = now
        self._last_runtime_ready = _optional_bool(payload, "runtime_ready", "runtime_available")
        hardware_observed_at, freshness_error = _robot_evidence_time(
            payload,
            now=now,
            local_freshness_budget_seconds=self.freshness_budget_seconds,
        )
        self._hardware_observed_at = hardware_observed_at
        self._hardware_observation_fresh = freshness_error is None
        self._hardware_evidence_error = freshness_error
        if freshness_error is not None:
            self._last_hardware_ready = None
            self._last_error = None
            return
        self._last_hardware_ready = _optional_bool(payload, "hardware_ready", "hardware_connected")
        self._hardware_evidence_error = None
        self._last_error = None

    def _record_probe_failure(self, exc: Exception) -> None:
        self._last_reachable = False
        self._last_runtime_ready = None
        self._last_hardware_ready = None
        self._capabilities = ()
        self._ownership = None
        self._last_error = str(exc) or exc.__class__.__name__
        self._observed_at = self.clock()

    async def _active_status_probe(self) -> None:
        async with self._transition_lock:
            client = self._client
            generation = self._generation
            if client is None or self._active_target is None:
                raise ConnectionStateError("BioXP saved profile is not actively connected")
            lease = self._acquire_lease_locked(generation, require_fresh=False)
        try:
            payload = await client.probe_status_only()
        except Exception as exc:
            async with self._transition_lock:
                if client is self._client and generation == self._generation:
                    self._record_probe_failure(exc)
            return
        finally:
            await self._release_lease(lease)
        async with self._transition_lock:
            if client is self._client and generation == self._generation:
                self._apply_probe_payload(payload)

    @asynccontextmanager
    async def workflow_lease(self, expected_generation: int):
        """Hold connection authority stable across one admitted robot workflow."""
        async with self.active_request_lease(expected_generation=expected_generation, require_fresh=False) as client:
            async with self._v1_workflow_lock:
                yield client

    async def disconnect(self) -> BioXpSnapshot:
        async with self._transition_lock:
            await self._deactivate_locked(increment=True)
        await self._wait_for_drains()
        return self.snapshot()

    async def close(self) -> None:
        async with self._transition_lock:
            await self._deactivate_locked(increment=False)
        await self._wait_for_drains()

    def _mark_current_draining_locked(self, *, increment: bool) -> None:
        client = self._client
        if client is not None:
            lease = self._generation_leases.get(self._generation)
            if lease is None or lease.client is not client:
                lease = _GenerationLease(self._generation, client)
                self._generation_leases[self._generation] = lease
            lease.state = "DRAINING"
            self._schedule_drain_locked(lease)
        self._client = None
        self._active_target = None
        if increment:
            self._generation += 1
        self._clear_observation()

    async def _deactivate_locked(self, *, increment: bool) -> None:
        self._stop_active_probe_locked()
        self._stop_snapshot_refresh_locked()
        self._mark_current_draining_locked(increment=increment)

    def _start_active_probe_locked(self) -> None:
        if self.active_probe_interval_seconds is None or self._client is None:
            return
        self._active_probe_task = asyncio.create_task(
            self._active_probe_loop(),
            name="bioxp-active-connection-probe",
        )

    def _stop_active_probe_locked(self) -> None:
        task = self._active_probe_task
        self._active_probe_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()

    async def _active_probe_loop(self) -> None:
        assert self.active_probe_interval_seconds is not None
        try:
            while True:
                await asyncio.sleep(self.active_probe_interval_seconds)
                await self._active_status_probe()
        except asyncio.CancelledError:
            raise
        except (ConnectionStateError, TargetPolicyError):
            return

    def _start_snapshot_refresh_locked(self) -> None:
        if self.active_probe_interval_seconds is None or self._client is None:
            return
        self._snapshot_refresh_task = asyncio.create_task(
            self._snapshot_refresh_loop(),
            name="bioxp-canonical-snapshot-refresh",
        )

    def _stop_snapshot_refresh_locked(self) -> None:
        task = self._snapshot_refresh_task
        self._snapshot_refresh_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()

    async def _snapshot_refresh_loop(self) -> None:
        assert self.active_probe_interval_seconds is not None
        try:
            while True:
                await asyncio.sleep(self.active_probe_interval_seconds)
                await self._snapshot_refresh_once()
        except asyncio.CancelledError:
            raise
        except (ConnectionStateError, TargetPolicyError):
            return

    async def _snapshot_refresh_once(self) -> None:
        async with self._transition_lock:
            client = self._client
            generation = self._generation
            if client is None or self._active_target is None:
                raise ConnectionStateError("BioXP saved profile is not actively connected")
            lease = self._acquire_lease_locked(generation, require_fresh=False)
        try:
            # Full probe: robot-owned canonical snapshot is auto-collected by the
            # client when its cached evidence is missing or stale, keeping the
            # operator admission gate (door, axes, gripper) continuously fresh.
            payload = await client.probe()
        except Exception as exc:
            async with self._transition_lock:
                if client is self._client and generation == self._generation:
                    self._record_probe_failure(exc)
            return
        finally:
            await self._release_lease(lease)
        async with self._transition_lock:
            if client is self._client and generation == self._generation:
                self._apply_probe_payload(payload)

    def _clear_observation(self) -> None:
        self._observed_at = None
        self._last_reachable = None
        self._last_runtime_ready = None
        self._last_hardware_ready = None
        self._hardware_observed_at = None
        self._hardware_observation_fresh = None
        self._hardware_evidence_error = None
        self._automatic_snapshot_refresh = None
        self._capabilities = ()
        self._startup_lifecycle = None
        self._maintenance_state = None
        self._ownership = None
        self._last_error = None

    def snapshot(self) -> BioXpSnapshot:
        profile_error: str | None = None
        try:
            profile = self.profile_store.load()
        except ProfileStoreError as exc:
            profile = None
            profile_error = str(exc)
        now = self.clock()
        fresh: bool | None = None
        stale = False
        if self._observed_at is not None:
            age = max(0.0, (now - self._observed_at).total_seconds())
            fresh = self.freshness_budget_seconds is None or age <= self.freshness_budget_seconds
            stale = not fresh
        expose_observation = fresh is True
        return BioXpSnapshot(
            configured=profile is not None or self.profile_store.exists(),
            display_name=profile.display_name if profile else None,
            masked_target=mask_target_url(profile.api_url) if profile else None,
            active=self._active_target is not None,
            generation=self._generation,
            reachable=self._last_reachable if expose_observation else None,
            runtime_ready=self._last_runtime_ready if expose_observation else None,
            hardware_ready=(
                self._last_hardware_ready
                if expose_observation and self._hardware_observation_fresh is True
                else None
            ),
            hardware_observed_at=self._hardware_observed_at,
            hardware_observation_fresh=self._hardware_observation_fresh,
            hardware_observation_stale=self._hardware_observation_fresh is False,
            hardware_evidence_error=self._hardware_evidence_error,
            automatic_snapshot_refresh=copy.deepcopy(self._automatic_snapshot_refresh),
            capabilities=self._capabilities,
            observed_at=self._observed_at,
            freshness_budget_seconds=self.freshness_budget_seconds,
            observation_fresh=fresh,
            observation_stale=stale,
            last_observed_reachable=self._last_reachable,
            last_observed_runtime_ready=self._last_runtime_ready,
            last_observed_hardware_ready=self._last_hardware_ready,
            last_error=profile_error or self._last_error,
            startup_lifecycle=copy.deepcopy(self._startup_lifecycle),
            maintenance_state=copy.deepcopy(self._maintenance_state),
            ownership=copy.deepcopy(self._ownership),
        )

    @property
    def active_client(self) -> RobotClientProtocol | None:
        return self._client

    @property
    def generation(self) -> int:
        return self._generation

    def load_profile(self) -> BioXpProfile | None:
        return self.profile_store.load()

def _find_maintenance_state(payload: Mapping[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    """Find the first maintenance_state in bounded robot response envelopes."""
    pending: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while pending and visited < 100:
        value, depth = pending.pop(0)
        visited += 1
        if isinstance(value, Mapping):
            if "maintenance_state" in value:
                state = value["maintenance_state"]
                return True, copy.deepcopy(dict(state)) if isinstance(state, Mapping) else None
            if depth < 8:
                pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, (list, tuple)) and depth < 8:
            pending.extend((child, depth + 1) for child in value)
    return False, None


def _find_named_mapping(
    payload: Mapping[str, Any],
    key: str,
) -> tuple[bool, dict[str, Any] | None]:
    pending: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while pending and visited < 100:
        value, depth = pending.pop(0)
        visited += 1
        if isinstance(value, Mapping):
            if key in value:
                found = value[key]
                return True, copy.deepcopy(dict(found)) if isinstance(found, Mapping) else None
            if depth < 8:
                pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, (list, tuple)) and depth < 8:
            pending.extend((child, depth + 1) for child in value)
    return False, None


def _optional_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    return None


def _robot_evidence_time(
    payload: dict[str, Any],
    *,
    now: datetime,
    local_freshness_budget_seconds: float | None,
) -> tuple[datetime | None, str | None]:
    """Preserve robot-owned hardware cache age separately from runtime liveness."""

    freshness = payload.get("freshness")
    freshness = freshness if isinstance(freshness, dict) else {}
    age_s = _non_negative_number(freshness.get("age_s"))
    fresh_for_s = _positive_number(freshness.get("fresh_for_s"))
    available = payload.get("available") is True
    cache_state = payload.get("cache_state")
    freshness_state = freshness.get("state")
    upstream_fresh = (
        available
        and cache_state == "fresh"
        and freshness_state == "fresh"
        and age_s is not None
        and fresh_for_s is not None
        and age_s <= fresh_for_s
    )
    if upstream_fresh:
        assert age_s is not None
        return now - timedelta(seconds=age_s), None

    observed_at = now - timedelta(seconds=age_s) if age_s is not None else None
    detail = (
        "BioXP status evidence is stale or unavailable "
        f"(available={available}, cache_state={cache_state!r}, freshness_state={freshness_state!r})"
    )
    return observed_at, detail


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) and numeric >= 0 else None


def _positive_number(value: object) -> float | None:
    numeric = _non_negative_number(value)
    return numeric if numeric is not None and numeric > 0 else None

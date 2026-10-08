from __future__ import annotations

import asyncio
import copy
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Protocol
from urllib.parse import urlsplit

from .errors import ConnectionStateError, ProfileStoreError, TargetPolicyError
from .models import BioXpProfile, BioXpSnapshot
from .profile_store import BioXpProfileStore
from .robot_client import BioXpRobotClient
from .target_policy import BioXpTargetPolicy, ValidatedBioXpTarget


class RobotClientProtocol(Protocol):
    async def probe(self) -> dict[str, Any]: ...

    async def request(
        self,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


ClientFactory = Callable[[ValidatedBioXpTarget], RobotClientProtocol]
Clock = Callable[[], datetime]


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
        freshness_budget_seconds: float = 30.0,
        active_probe_interval_seconds: float | None = None,
        clock: Clock | None = None,
        initial_generation: int | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.target_policy = target_policy
        self.client_factory = client_factory or (lambda target: BioXpRobotClient(target))
        self.freshness_budget_seconds = freshness_budget_seconds
        if active_probe_interval_seconds is not None and active_probe_interval_seconds <= 0:
            raise ValueError("active probe interval must be positive")
        self.active_probe_interval_seconds = active_probe_interval_seconds
        self.clock = clock or _utcnow
        self._transition_lock = asyncio.Lock()
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
        self._capabilities: tuple[str, ...] = ()
        self._startup_lifecycle: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._command_active = False
        self._active_probe_task: asyncio.Task[None] | None = None

    async def save_profile(self, profile: BioXpProfile) -> BioXpSnapshot:
        canonical = self.target_policy.validate(profile.api_url)
        normalized = profile.model_copy(update={"api_url": canonical.api_url})
        async with self._transition_lock:
            await self._deactivate_locked(increment=bool(self._client or self._active_target))
            self.profile_store.save(normalized)
        return self.snapshot()

    async def forget_profile(self) -> BioXpSnapshot:
        async with self._transition_lock:
            await self._deactivate_locked(increment=True)
            self.profile_store.forget()
        return self.snapshot()

    async def connect(self) -> BioXpSnapshot:
        async with self._transition_lock:
            profile = self.profile_store.load()
            if profile is None:
                raise ConnectionStateError("Save a BioXP saved profile before connecting")
            try:
                target = await self.target_policy.validate_for_connection(profile.api_url)
            except TargetPolicyError as exc:
                await self._deactivate_locked(increment=bool(self._client or self._active_target))
                self._last_error = str(exc)
                raise
            await self._stop_active_probe_locked()
            if self._client is not None:
                await self._client.close()
            self._generation += 1
            self._clear_observation()
            self._active_target = target
            self._client = self.client_factory(target)
            await self._probe_locked()
            self._start_active_probe_locked()
        return self.snapshot()

    async def probe(self) -> BioXpSnapshot:
        async with self._transition_lock:
            if self._client is None or self._active_target is None:
                raise ConnectionStateError("BioXP saved profile is not actively connected")
            # Rebind only after every current DNS answer still passes policy.
            try:
                validated = await self.target_policy.validate_for_connection(self._active_target.api_url)
            except TargetPolicyError as exc:
                await self._deactivate_locked(increment=True)
                self._last_error = str(exc)
                raise
            if validated != self._active_target:
                await self._client.close()
                self._client = self.client_factory(validated)
                self._active_target = validated
                self._generation += 1
                self._clear_observation()
            await self._probe_locked()
        return self.snapshot()

    async def _probe_locked(self) -> None:
        assert self._client is not None
        try:
            payload = await self._client.probe()
            startup = payload.get("startup")
            self._startup_lifecycle = copy.deepcopy(startup) if isinstance(startup, dict) else None
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
        except Exception as exc:
            self._last_reachable = False
            self._last_runtime_ready = None
            self._last_hardware_ready = None
            self._capabilities = ()
            self._last_error = str(exc) or exc.__class__.__name__
            self._observed_at = self.clock()

    async def disconnect(self) -> BioXpSnapshot:
        async with self._transition_lock:
            await self._deactivate_locked(increment=True)
        return self.snapshot()

    async def close(self) -> None:
        async with self._transition_lock:
            await self._deactivate_locked(increment=False)

    async def _deactivate_locked(self, *, increment: bool) -> None:
        await self._stop_active_probe_locked()
        if self._client is not None:
            await self._client.close()
        self._client = None
        self._active_target = None
        if increment:
            self._generation += 1
        self._clear_observation()

    def _start_active_probe_locked(self) -> None:
        if self.active_probe_interval_seconds is None or self._client is None:
            return
        self._active_probe_task = asyncio.create_task(
            self._active_probe_loop(),
            name="bioxp-active-connection-probe",
        )

    async def _stop_active_probe_locked(self) -> None:
        task = self._active_probe_task
        self._active_probe_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            same_loop = task.get_loop() is asyncio.get_running_loop()
        except RuntimeError:
            same_loop = False
        if same_loop:
            with suppress(asyncio.CancelledError):
                await task

    async def _active_probe_loop(self) -> None:
        assert self.active_probe_interval_seconds is not None
        try:
            while True:
                await asyncio.sleep(self.active_probe_interval_seconds)
                await self.probe()
        except asyncio.CancelledError:
            raise
        except (ConnectionStateError, TargetPolicyError):
            return

    def _clear_observation(self) -> None:
        self._observed_at = None
        self._last_reachable = None
        self._last_runtime_ready = None
        self._last_hardware_ready = None
        self._hardware_observed_at = None
        self._hardware_observation_fresh = None
        self._hardware_evidence_error = None
        self._capabilities = ()
        self._startup_lifecycle = None
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
            fresh = age <= self.freshness_budget_seconds
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
            capabilities=self._capabilities,
            observed_at=self._observed_at,
            freshness_budget_seconds=self.freshness_budget_seconds,
            observation_fresh=fresh,
            observation_stale=stale,
            last_observed_reachable=self._last_reachable,
            last_observed_runtime_ready=self._last_runtime_ready,
            last_observed_hardware_ready=self._last_hardware_ready,
            last_error=profile_error or self._last_error,
            command_active=self._command_active,
            startup_lifecycle=copy.deepcopy(self._startup_lifecycle),
        )

    @property
    def active_client(self) -> RobotClientProtocol | None:
        return self._client

    @property
    def generation(self) -> int:
        return self._generation

    def load_profile(self) -> BioXpProfile | None:
        return self.profile_store.load()

    def set_command_active(self, active: bool) -> None:
        self._command_active = active

    def observe_command_response(self, response: object) -> None:
        if not isinstance(response, dict):
            return
        lifecycle = response.get("lifecycle")
        startup = lifecycle.get("startup") if isinstance(lifecycle, dict) else response.get("startup")
        if isinstance(startup, dict):
            self._startup_lifecycle = copy.deepcopy(startup)


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
    local_freshness_budget_seconds: float,
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

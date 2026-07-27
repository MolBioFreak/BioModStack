from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .errors import RobotResponseError, RobotTransportError
from .target_policy import ValidatedBioXpTarget


DEFAULT_ROBOT_ROUTES: Mapping[str, tuple[str, str, float]] = {
    "status": ("GET", "/status", 5.0),
    "activate_usb_for_service": ("POST", "/oem/runtime/activate_service", 90.0),
    "collect_hardware_snapshot": ("POST", "/hardware/snapshot/collect", 210.0),
    "initialize_oem_environment": ("POST", "/oem/startup/initialize_environment", 470.0),
    "run_oem_motor_stage": ("POST", "/oem/runtime/commands/enqueue", 30.0),
    "record_oem_motor_stage_observation": ("POST", "/oem/runtime/commands/enqueue", 15.0),
    "oem_full_lifecycle_contract": ("GET", "/oem/runtime/movement-runs/contract", 10.0),
    "plan_oem_full_lifecycle": ("POST", "/oem/runtime/movement-runs", 30.0),
    "get_oem_full_lifecycle_run": ("GET", "/oem/runtime/movement-runs/{run_id}", 10.0),
    "get_oem_full_lifecycle_ledger": ("GET", "/oem/runtime/movement-runs/{run_id}/ledger", 10.0),
    "cancel_oem_full_lifecycle_run": ("POST", "/oem/runtime/movement-runs/{run_id}/cancel", 15.0),
    "collect_axis_diagnostics": ("GET", "/motion/diagnostics/status", 45.0),
    "run_axis_diagnostic": ("POST", "/motion/diagnostics/execute", 180.0),
    "stop_axis_diagnostic": ("POST", "/motion/diagnostics/stop", 25.0),
    "emergency_stop": ("POST", "/oem/runtime/emergency_stop", 5.0),
}

_ROUTE_PARAMETER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_OEM_LIFECYCLE_MUTATION_ROUTES = frozenset({"plan_oem_full_lifecycle", "cancel_oem_full_lifecycle_run"})
_AUTOMATIC_SNAPSHOT_TIMEOUT_SECONDS = 15.0


def _read_oem_lifecycle_token(path: Path | None) -> str:
    if path is None:
        raise RobotTransportError("BioXP OEM lifecycle mutation token file is not configured")
    try:
        mode = path.stat().st_mode
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RobotTransportError("BioXP OEM lifecycle mutation token file is unavailable") from exc
    if mode & 0o077:
        raise RobotTransportError("BioXP OEM lifecycle mutation token file permissions are too broad")
    if len(token) < 32:
        raise RobotTransportError("BioXP OEM lifecycle mutation token is invalid")
    return token


def _render_route_path(template: str, path_params: dict[str, str] | None) -> str:
    required = set(_ROUTE_PARAMETER_RE.findall(template))
    supplied = set((path_params or {}).keys())
    if supplied != required:
        raise RobotTransportError(
            f"BioXP route parameters do not match template: required={sorted(required)} supplied={sorted(supplied)}"
        )
    path = template
    for key in sorted(required):
        value = (path_params or {})[key]
        if type(value) is not str or not value:
            raise RobotTransportError(f"BioXP route parameter {key!r} must be a non-empty string")
        path = path.replace("{" + key + "}", quote(value, safe=""))
    return path


class PinnedAddressTransport(httpx.AsyncBaseTransport):
    """Connect to one policy-validated address while preserving HTTP/TLS identity."""

    def __init__(
        self,
        target: ValidatedBioXpTarget,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not target.resolved_addresses:
            raise ValueError("BioXP transport requires a policy-validated address")
        self._target = target
        self._address = str(target.resolved_addresses[0])
        self._transport = transport or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self._target.hostname:
            raise httpx.UnsupportedProtocol("BioXP transport target changed after validation")
        extensions = dict(request.extensions)
        if self._target.scheme == "https":
            extensions["sni_hostname"] = self._target.hostname
        pinned_request = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=self._address),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class BioXpRobotClient:
    """One generation-bound transport; callers use registry keys, never paths."""

    def __init__(
        self,
        target: ValidatedBioXpTarget,
        *,
        routes: Mapping[str, tuple[str, str, float]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        oem_lifecycle_token_file: Path | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        snapshot_retry_backoff_seconds: float = 30.0,
    ) -> None:
        if snapshot_retry_backoff_seconds <= 0:
            raise ValueError("automatic snapshot retry backoff must be positive")
        self.target = target
        self.routes = dict(routes or DEFAULT_ROBOT_ROUTES)
        configured_token_file = os.environ.get("BMS_BIOXP_OEM_RUNTIME_TOKEN_FILE", "").strip()
        self._oem_lifecycle_token_file = oem_lifecycle_token_file or (
            Path(configured_token_file) if configured_token_file else None
        )
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._snapshot_retry_backoff_seconds = snapshot_retry_backoff_seconds
        self._snapshot_retry_after = 0.0
        pinned_transport = PinnedAddressTransport(target, transport=transport)
        self._client = httpx.AsyncClient(
            base_url=target.api_url,
            transport=pinned_transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0),
        )

    async def probe(self) -> dict[str, Any]:
        payload = await self.probe_status_only()
        now = self._monotonic_clock()
        if _hardware_evidence_needs_refresh(payload) and now < self._snapshot_retry_after:
            payload = dict(payload)
            payload["automatic_snapshot_refresh"] = {
                "attempted": False,
                "published": False,
                "retry_deferred": True,
                "retry_after_s": max(0.0, self._snapshot_retry_after - now),
            }
            return payload
        if _hardware_evidence_needs_refresh(payload):
            try:
                collected = await self.request(
                    "collect_hardware_snapshot",
                    timeout_override=_AUTOMATIC_SNAPSHOT_TIMEOUT_SECONDS,
                )
                _require_published_snapshot(collected)
                payload = await self.probe_status_only()
                self._snapshot_retry_after = 0.0
            except (RobotResponseError, RobotTransportError) as exc:
                # Runtime reachability remains truthful when only the query-only
                # evidence refresh fails. The stale payload is still useful and
                # must not be relabeled as a disconnected robot.
                payload = dict(payload)
                payload["automatic_snapshot_refresh"] = {
                    "attempted": True,
                    "published": False,
                    "error": str(exc) or exc.__class__.__name__,
                }
                self._snapshot_retry_after = (
                    self._monotonic_clock() + self._snapshot_retry_backoff_seconds
                )
        return payload

    async def probe_status_only(self) -> dict[str, Any]:
        payload = await self.request("status", retry_read_once=True)
        if not isinstance(payload, dict):
            raise RobotTransportError("BioXP status response was not an object")
        return payload

    async def request(
        self,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
        retry_read_once: bool = False,
        timeout_override: float | None = None,
    ) -> Any:
        try:
            method, path_template, timeout = self.routes[route_name]
        except KeyError as exc:
            raise RobotTransportError(f"Unknown BioXP robot route key: {route_name}") from exc
        path = _render_route_path(path_template, path_params)
        headers = None
        if route_name in _OEM_LIFECYCLE_MUTATION_ROUTES:
            headers = {"X-BioXP-OEM-Token": _read_oem_lifecycle_token(self._oem_lifecycle_token_file)}
        attempts = 2 if retry_read_once and method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json_data,
                    params=params,
                    headers=headers,
                    timeout=timeout if timeout_override is None else timeout_override,
                )
                if 300 <= response.status_code < 400:
                    raise RobotTransportError("BioXP target redirects are forbidden")
                if response.is_error:
                    try:
                        detail: object = response.json()
                    except ValueError:
                        detail = response.text[:1000]
                    raise RobotResponseError(response.status_code, detail)
                return response.json()
            except RobotResponseError:
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                if attempt + 1 < attempts:
                    continue
                raise RobotTransportError("BioXP robot transport is unreachable or timed out") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise RobotTransportError("BioXP robot returned an invalid transport response") from exc
        raise RobotTransportError("BioXP robot transport failed")

    async def close(self) -> None:
        await self._client.aclose()


def _hardware_evidence_needs_refresh(payload: Mapping[str, Any]) -> bool:
    runtime_ready = payload.get("runtime_ready")
    if not isinstance(runtime_ready, bool):
        runtime_ready = payload.get("runtime_available")
    if runtime_ready is not True:
        return False
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, (list, tuple, set)) or "collect_hardware_snapshot" not in capabilities:
        return False
    freshness = payload.get("freshness")
    freshness = freshness if isinstance(freshness, Mapping) else {}
    available = payload.get("available") is True
    cache_fresh = payload.get("cache_state") == "fresh" and freshness.get("state") == "fresh"
    age_s = freshness.get("age_s")
    fresh_for_s = freshness.get("fresh_for_s")
    if not available or not cache_fresh:
        return True
    if isinstance(age_s, bool) or not isinstance(age_s, (int, float)):
        return True
    if isinstance(fresh_for_s, bool) or not isinstance(fresh_for_s, (int, float)) or fresh_for_s <= 0:
        return True
    return float(age_s) >= float(fresh_for_s) / 2.0


def _require_published_snapshot(response: object) -> str:
    payload = response if isinstance(response, Mapping) else {}
    snapshot = payload.get("snapshot")
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None
    if (
        payload.get("ok") is True
        and payload.get("published") is True
        and isinstance(snapshot_id, str)
        and snapshot_id
    ):
        return snapshot_id
    detail = payload.get("error") or payload.get("detail") or "automatic hardware snapshot was not published"
    raise RobotTransportError(str(detail))

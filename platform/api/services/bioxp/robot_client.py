from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import RobotResponseError, RobotTransportError
from .target_policy import ValidatedBioXpTarget


DEFAULT_ROBOT_ROUTES: Mapping[str, tuple[str, str, float]] = {
    "status": ("GET", "/status", 5.0),
    "activate_usb_for_service": ("POST", "/reconnect", 30.0),
    "collect_hardware_snapshot": ("POST", "/hardware/snapshot/collect", 210.0),
    "oem_full_lifecycle_contract": ("GET", "/oem/runtime/movement-runs/contract", 10.0),
    "plan_oem_full_lifecycle": ("POST", "/oem/runtime/movement-runs", 30.0),
    "get_oem_full_lifecycle_run": ("GET", "/oem/runtime/movement-runs/{run_id}", 10.0),
    "get_oem_full_lifecycle_ledger": ("GET", "/oem/runtime/movement-runs/{run_id}/ledger", 10.0),
    "cancel_oem_full_lifecycle_run": ("POST", "/oem/runtime/movement-runs/{run_id}/cancel", 15.0),
    "collect_axis_diagnostics": ("GET", "/motion/diagnostics/status", 45.0),
    "run_axis_diagnostic": ("POST", "/motion/diagnostics/execute", 180.0),
    "stop_axis_diagnostic": ("POST", "/motion/diagnostics/stop", 25.0),
    "recover_motion_non_homing": ("POST", "/motion/arm/strict_startup", 90.0),
    "emergency_stop": ("POST", "/oem/runtime/emergency_stop", 5.0),
    "camera_status": ("GET", "/camera/status", 5.0),
    "camera_latest": ("GET", "/camera/frame/latest", 5.0),
    "camera_snapshot": ("POST", "/camera/snapshot", 15.0),
    "operator_control_catalog": ("GET", "/operator/control-catalog", 10.0),
    "operator_control_catalog_v2": ("GET", "/operator/v2/control-catalog", 10.0),
    "operator_dashboard": ("GET", "/operator/dashboard", 10.0),
    "operator_dashboard_v2": ("GET", "/operator/v2/dashboard", 10.0),
    "pipette_readback": ("POST", "/liquid/readback", 120.0),
    "pipette_application_status": ("GET", "/liquid/application/status", 10.0),
    "pipette_application_plan": ("POST", "/liquid/application/plan", 10.0),
    "operator_action_admission": ("POST", "/operator/actions/{action_id}/admission", 10.0),
    "invoke_operator_action": ("POST", "/operator/actions/{action_id}", 900.0),
    "invoke_operator_action_v2": ("POST", "/operator/v2/actions/{action_id}", 30.0),
    "operator_action_history": ("GET", "/operator/actions/history", 10.0),
    "operator_action_history_v2": ("GET", "/operator/v2/actions/history", 10.0),
    "operator_action_receipt": ("GET", "/operator/actions/receipts/{command_id}", 10.0),
    "operator_action_receipt_v2": ("GET", "/operator/v2/actions/receipts/{command_id}", 10.0),
    "assess_operator_action": ("POST", "/operator/actions/receipts/{command_id}/assessment", 15.0),
    "operator_report_summary": ("GET", "/operator/reports/summary", 10.0),
    "operator_report_commands": ("GET", "/operator/reports/commands", 10.0),
    "operator_report_command_detail": ("GET", "/operator/reports/commands/{command_id}", 10.0),
    "operator_report_command_transitions": ("GET", "/operator/reports/commands/{command_id}/transitions", 10.0),
    "operator_report_pipette": ("GET", "/operator/reports/pipette", 10.0),
    "operator_report_pipette_detail": ("GET", "/operator/reports/pipette/{pipette_operation_id}", 10.0),
    "operator_report_pipette_channels": ("GET", "/operator/reports/pipette/{pipette_operation_id}/channels", 10.0),
    "operator_report_pipette_exchanges": ("GET", "/operator/reports/pipette/{pipette_operation_id}/exchanges", 10.0),
    "operator_report_events": ("GET", "/operator/reports/events", 10.0),
    "operator_report_event_detail": ("GET", "/operator/reports/events/{event_id}", 10.0),
    "operator_report_pressure_streams": ("GET", "/operator/reports/pressure-streams", 10.0),
    "operator_report_pressure_detail": ("GET", "/operator/reports/pressure-streams/{stream_session_id}", 10.0),
    "operator_report_pressure_samples": ("GET", "/operator/reports/pressure-streams/{stream_session_id}/samples", 10.0),
    "operator_report_audit_health": ("GET", "/operator/audit-health", 10.0),
    "operator_report_export_create": ("POST", "/operator/reports/exports", 30.0),
    "operator_report_export_detail": ("GET", "/operator/reports/exports/{export_id}", 10.0),
    "operator_report_export_download": ("GET", "/operator/reports/exports/{export_id}/download", 30.0),
}

_ROUTE_PARAMETER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")

_AUTOMATIC_SNAPSHOT_TIMEOUT_SECONDS = 15.0
MAX_CAMERA_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CAMERA_STATUS_BYTES = 64 * 1024
MAX_CAMERA_ERROR_BYTES = 1000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_ISO8601_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)$"
)


class _CameraStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^bioxp\.camera_status\.v1$")
    available: StrictBool
    frame_sequence: StrictInt | None = Field(default=None, ge=0)
    frame_captured_at: AwareDatetime | None = None
    frame_age_seconds: StrictFloat | None = Field(default=None, ge=0, allow_inf_nan=False)
    freshness_budget_seconds: StrictFloat = Field(gt=0, le=60, allow_inf_nan=False)
    provider_generation: StrictInt = Field(ge=0)
    dropped_frames: StrictInt = Field(ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    detail: str | None = Field(default=None, max_length=1000)

    @field_validator("frame_captured_at", mode="before")
    @classmethod
    def require_timestamp_wire_string(cls, value: object) -> object:
        if value is not None and (
            type(value) is not str or _UTC_ISO8601_RE.fullmatch(value) is None
        ):
            raise ValueError("frame_captured_at must be an ISO-8601 string")
        return value

    @model_validator(mode="after")
    def validate_frame_availability(self) -> "_CameraStatusResponse":
        frame_values = (
            self.frame_sequence,
            self.frame_captured_at,
            self.frame_age_seconds,
            self.content_sha256,
        )
        if self.available and any(value is None for value in frame_values):
            raise ValueError("available camera status requires complete frame metadata")
        if not self.available and any(value is not None for value in frame_values):
            raise ValueError("unavailable camera status cannot claim frame metadata")
        return self


@dataclass(frozen=True, slots=True)
class CameraImage:
    content: bytes
    content_type: str
    etag: str
    sha256: str


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
        monotonic_clock: Callable[[], float] | None = None,
        snapshot_retry_backoff_seconds: float = 30.0,
    ) -> None:
        if snapshot_retry_backoff_seconds <= 0:
            raise ValueError("automatic snapshot retry backoff must be positive")
        self.target = target
        self.routes = dict(routes or DEFAULT_ROBOT_ROUTES)
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
                snapshot_id = _require_published_snapshot(collected)
                payload = await self.probe_status_only()
                payload = dict(payload)
                payload["automatic_snapshot_refresh"] = {
                    "attempted": True,
                    "published": True,
                    "snapshot_id": snapshot_id,
                }
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

    async def camera_status(self) -> dict[str, Any]:
        try:
            method, path_template, timeout_seconds = self.routes["camera_status"]
        except KeyError as exc:
            raise RobotTransportError("Unknown BioXP robot route key: camera_status") from exc
        path = _render_route_path(path_template, None)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._client.stream(
                    method,
                    path,
                    timeout=_bounded_timeout(timeout_seconds),
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise RobotTransportError("BioXP target redirects are forbidden")
                    if response.is_error:
                        error_bytes = await _read_limited_body(
                            response,
                            limit=MAX_CAMERA_ERROR_BYTES,
                            overflow_message="BioXP camera error response exceeded the size limit",
                        )
                        raise RobotResponseError(
                            response.status_code,
                            error_bytes.decode("utf-8", errors="replace"),
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "application/json":
                        raise RobotTransportError("BioXP robot returned a malformed camera status")
                    declared_length = _required_bounded_content_length(
                        response,
                        limit=MAX_CAMERA_STATUS_BYTES,
                        label="camera status",
                    )
                    body = await _read_limited_body(
                        response,
                        limit=MAX_CAMERA_STATUS_BYTES,
                        overflow_message="BioXP camera status exceeded the size limit",
                    )
                    if len(body) != declared_length:
                        raise RobotTransportError(
                            "BioXP camera status content length did not match received bytes"
                        )
                    payload = json.loads(body)
        except RobotResponseError:
            raise
        except RobotTransportError:
            raise
        except TimeoutError as exc:
            raise RobotTransportError("BioXP camera transport is unreachable or timed out") from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as exc:
            raise RobotTransportError("BioXP camera transport is unreachable or timed out") from exc
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise RobotTransportError("BioXP robot returned a malformed camera status") from exc
        try:
            status = _CameraStatusResponse.model_validate(payload)
        except ValidationError as exc:
            raise RobotTransportError("BioXP robot returned a malformed camera status") from exc
        return status.model_dump(mode="json")

    async def camera_latest(self) -> CameraImage:
        return await self._camera_image("camera_latest")

    async def camera_snapshot(self) -> CameraImage:
        return await self._camera_image("camera_snapshot")

    async def _camera_image(self, route_name: str) -> CameraImage:
        try:
            method, path_template, timeout_seconds = self.routes[route_name]
        except KeyError as exc:
            raise RobotTransportError(f"Unknown BioXP robot route key: {route_name}") from exc
        path = _render_route_path(path_template, None)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._client.stream(
                    method,
                    path,
                    timeout=_bounded_timeout(timeout_seconds),
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise RobotTransportError("BioXP target redirects are forbidden")
                    if response.is_error:
                        error_bytes = await _read_limited_body(
                            response,
                            limit=MAX_CAMERA_ERROR_BYTES,
                            overflow_message="BioXP camera error response exceeded the size limit",
                        )
                        raise RobotResponseError(
                            response.status_code,
                            error_bytes.decode("utf-8", errors="replace"),
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "image/jpeg":
                        raise RobotTransportError("BioXP camera response was not a JPEG image")
                    parsed_length = _required_bounded_content_length(
                        response,
                        limit=MAX_CAMERA_IMAGE_BYTES,
                        label="camera response",
                    )
                    content = await _read_limited_body(
                        response,
                        limit=MAX_CAMERA_IMAGE_BYTES,
                        overflow_message="BioXP camera image exceeded the size limit",
                    )
                    received = len(content)
                    if not content:
                        raise RobotTransportError("BioXP camera returned an empty image")
                    if received != parsed_length:
                        raise RobotTransportError(
                            "BioXP camera response content length did not match received bytes"
                        )
                    if not (content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9")):
                        raise RobotTransportError("BioXP camera response had invalid JPEG framing")
                    digest = sha256(content).hexdigest()
                    upstream_hash = response.headers.get("x-content-sha256", "")
                    canonical_etag = f'\"{digest}\"'
                    if not _SHA256_RE.fullmatch(upstream_hash) or upstream_hash != digest:
                        raise RobotTransportError("BioXP camera image content hash was missing or invalid")
                    if response.headers.get("etag", "") != canonical_etag:
                        raise RobotTransportError("BioXP camera image ETag did not match its content hash")
                    return CameraImage(
                        content=content,
                        content_type=content_type,
                        etag=canonical_etag,
                        sha256=digest,
                    )
        except RobotResponseError:
            raise
        except RobotTransportError:
            raise
        except TimeoutError as exc:
            raise RobotTransportError("BioXP camera transport is unreachable or timed out") from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as exc:
            raise RobotTransportError("BioXP camera transport is unreachable or timed out") from exc
        except httpx.HTTPError as exc:
            raise RobotTransportError("BioXP camera returned an invalid transport response") from exc

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
        attempts = 2 if retry_read_once and method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json_data,
                    params=params,
                    timeout=_bounded_timeout(timeout if timeout_override is None else timeout_override),
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


def _bounded_timeout(read_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=min(3.0, read_seconds),
        read=read_seconds,
        write=min(5.0, read_seconds),
        pool=min(3.0, read_seconds),
    )


def _required_bounded_content_length(
    response: httpx.Response,
    *,
    limit: int,
    label: str,
) -> int:
    declared = response.headers.get("content-length")
    if declared is None:
        raise RobotTransportError(f"BioXP {label} had no content length")
    if not declared.isascii() or not declared.isdigit():
        raise RobotTransportError(f"BioXP {label} had an invalid content length")
    try:
        parsed = int(declared)
    except ValueError as exc:
        raise RobotTransportError(f"BioXP {label} had an invalid content length") from exc
    if parsed < 1 or parsed > limit:
        raise RobotTransportError(f"BioXP {label} exceeded the size limit")
    return parsed


async def _read_limited_body(
    response: httpx.Response,
    *,
    limit: int,
    overflow_message: str,
) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > limit:
            raise RobotTransportError(overflow_message)
        body.extend(chunk)
    return bytes(body)


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

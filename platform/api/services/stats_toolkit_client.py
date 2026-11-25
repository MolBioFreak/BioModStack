"""Typed HTTP client for the standalone BioModStack Stats Toolkit service."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

DEFAULT_STATS_TOOLKIT_URL = "http://127.0.0.1:18181"
DEFAULT_STATS_TOOLKIT_TIMEOUT_SECONDS = 15.0
DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS = 2.0
DEFAULT_BMS_WORKSPACE_NAME = "BioModStack analyses"
DEFAULT_BMS_WORKSPACE_DESCRIPTION = "BioModStack centralized statistical analyses"
STATS_TOOLKIT_UNAVAILABLE_REASON = "stats_toolkit_unavailable"

_NON_TERMINAL_RUN_STATUSES = frozenset({"accepted", "validating", "prepared", "queued", "running"})
_WORKSPACE_CACHE_LOCK = threading.Lock()
_WORKSPACE_ID_CACHE: dict[tuple[str, str], str] = {}


class StatsToolkitError(RuntimeError):
    """Base error for Stats Toolkit service interactions."""


class StatsToolkitUnavailableError(StatsToolkitError):
    """Raised when the Stats Toolkit service is unreachable, times out, or returns 5xx."""


class StatsToolkitValidationError(StatsToolkitError):
    """Raised when the Stats Toolkit rejects inputs with HTTP 422."""


class StatsToolkitNotFoundError(StatsToolkitError):
    """Raised when a requested tool or run does not exist (HTTP 404)."""


def clear_workspace_cache() -> None:
    """Clear the process-local workspace ID cache (primarily for tests)."""
    with _WORKSPACE_CACHE_LOCK:
        _WORKSPACE_ID_CACHE.clear()


def stats_toolkit_required() -> bool:
    """Return True when BMS_STATS_TOOLKIT_REQUIRED forbids local fallback on unavailable service."""
    raw = (os.environ.get("BMS_STATS_TOOLKIT_REQUIRED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_base_url(base_url: str | None = None) -> str:
    raw = (base_url or os.environ.get("BMS_STATS_TOOLKIT_URL") or DEFAULT_STATS_TOOLKIT_URL).strip()
    return raw.rstrip("/")


def _resolve_timeout(timeout: float | None = None, *, default: float = DEFAULT_STATS_TOOLKIT_TIMEOUT_SECONDS) -> float:
    if timeout is not None:
        return float(timeout)
    env_val = os.environ.get("BMS_STATS_TOOLKIT_TIMEOUT_SECONDS")
    if env_val:
        try:
            parsed = float(env_val)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return default


def _extract_method_version(envelope: dict[str, Any], result: dict[str, Any]) -> str | None:
    mv = result.get("method_version")
    if isinstance(mv, str) and mv.strip():
        return mv.strip()
    provenance = envelope.get("provenance")
    if isinstance(provenance, dict):
        pmv = provenance.get("method_version")
        if isinstance(pmv, str) and pmv.strip():
            return pmv.strip()
    return None


class StatsToolkitClient:
    """Synchronous httpx client for calling the standalone Stats Toolkit API."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = _resolve_base_url(base_url)
        self.timeout = _resolve_timeout(timeout)
        self._transport = transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self.base_url.lower() in {"", "off", "disabled", "none"}:
            raise StatsToolkitUnavailableError("Stats Toolkit client disabled by configuration")
        effective_timeout = self.timeout if timeout is None else float(timeout)
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=effective_timeout, transport=self._transport) as client:
                response = client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=headers,
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit unavailable at {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit HTTP transport error at {self.base_url}: {exc}"
            ) from exc
        except Exception as exc:
            if type(exc).__name__ == "SocketBlockedError":
                raise StatsToolkitUnavailableError(
                    f"Stats Toolkit unreachable (network sockets blocked): {exc}"
                ) from exc
            raise

        if response.status_code == 404:
            raise StatsToolkitNotFoundError(self._extract_detail(response, f"Not found: {path}"))
        if response.status_code == 422:
            raise StatsToolkitValidationError(self._extract_detail(response, f"Validation failed for {path}"))
        if response.status_code >= 500:
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit returned HTTP {response.status_code}: {self._extract_detail(response, 'server error')}"
            )
        if response.status_code >= 400:
            raise StatsToolkitError(
                f"Stats Toolkit returned HTTP {response.status_code}: {self._extract_detail(response, 'request error')}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise StatsToolkitUnavailableError("Stats Toolkit returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise StatsToolkitUnavailableError("Stats Toolkit returned unexpected JSON payload")
        return data

    @staticmethod
    def _extract_detail(response: httpx.Response, fallback: str) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("error")
                if isinstance(detail, dict):
                    return str(detail.get("message") or detail)
                if detail is not None:
                    return str(detail)
        except ValueError:
            pass
        return response.text.strip() or fallback

    def health_ready(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready", timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS)

    def list_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/capabilities", timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS)

    def list_tools(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/tools", timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS)

    def list_workspaces(self, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/api/v1/workspaces",
            params={"offset": int(offset), "limit": int(limit)},
            timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS,
        )
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, list):
            raise StatsToolkitUnavailableError("Stats Toolkit workspaces response missing list")
        return [item for item in workspaces if isinstance(item, dict)]

    def create_workspace(self, name: str, description: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/workspaces",
            json_body={"name": name, "description": description},
        )

    def resolve_workspace_id(self, *, name: str = DEFAULT_BMS_WORKSPACE_NAME) -> str:
        """Resolve the shared BMS Toolkit workspace ID once per process (D1)."""
        override = (os.environ.get("BMS_STATS_TOOLKIT_WORKSPACE_ID") or "").strip()
        if override:
            return override

        cache_key = (self.base_url, name)
        with _WORKSPACE_CACHE_LOCK:
            cached = _WORKSPACE_ID_CACHE.get(cache_key)
            if cached:
                return cached

            # Paginate workspaces to locate an active match by exact name.
            matched_id: str | None = None
            archived_fallback: str | None = None
            offset = 0
            page_size = 100
            for _ in range(50):
                page = self.list_workspaces(offset=offset, limit=page_size)
                for ws in page:
                    if ws.get("name") != name:
                        continue
                    ws_id = str(ws.get("workspace_id") or ws.get("id") or "").strip()
                    if not ws_id:
                        continue
                    status = str(ws.get("status") or "active").strip().lower()
                    if status != "archived":
                        matched_id = ws_id
                        break
                    if archived_fallback is None:
                        archived_fallback = ws_id
                if matched_id is not None or len(page) < page_size:
                    break
                offset += page_size

            if matched_id is None:
                created = self.create_workspace(name, DEFAULT_BMS_WORKSPACE_DESCRIPTION)
                ws_id = str(created.get("workspace_id") or created.get("id") or "").strip()
                if not ws_id:
                    raise StatsToolkitUnavailableError("Stats Toolkit create_workspace response missing workspace_id")
                matched_id = ws_id

            _WORKSPACE_ID_CACHE[cache_key] = matched_id
            return matched_id

    def execute_tool(self, tool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = self._request("POST", f"/api/v1/tools/{tool_id}/execute", json_body=payload)
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise StatsToolkitUnavailableError(f"Stats Toolkit response for {tool_id} missing result object")
        return result

    def create_run(
        self,
        workspace_id: str,
        tool_id: str,
        inputs: dict[str, Any],
        *,
        schema_version: str = "1.0",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "schema_version": schema_version,
            "workspace_id": workspace_id,
            "tool_id": tool_id,
            "inputs": inputs,
        }
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request("POST", "/api/v1/runs", json_body=body, headers=headers)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/runs/{run_id}",
            timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS,
        )

    def run_tool(
        self,
        workspace_id: str | None,
        tool_id: str,
        inputs: dict[str, Any],
        idempotency_key: str | None = None,
        *,
        poll_timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a durable Toolkit run, verify completion, and return (run_ref, outputs)."""
        resolved_ws = str(workspace_id).strip() if workspace_id else self.resolve_workspace_id()
        envelope = self.create_run(
            resolved_ws,
            tool_id,
            inputs,
            idempotency_key=idempotency_key,
        )
        run_id = str(envelope.get("run_id") or "").strip()
        if not run_id:
            raise StatsToolkitUnavailableError(f"Stats Toolkit run for {tool_id} missing run_id")

        status = str(envelope.get("status") or "").strip().lower()
        if status in _NON_TERMINAL_RUN_STATUSES:
            budget = float(self.timeout if poll_timeout is None else poll_timeout)
            deadline = time.monotonic() + max(0.05, budget)
            delay = max(0.01, float(poll_interval))
            polls = 0
            while status in _NON_TERMINAL_RUN_STATUSES and time.monotonic() < deadline and polls < 60:
                time.sleep(min(delay, max(0.01, deadline - time.monotonic())))
                envelope = self.get_run(run_id)
                status = str(envelope.get("status") or "").strip().lower()
                delay = min(delay * 1.5, 0.5)
                polls += 1
            if status in _NON_TERMINAL_RUN_STATUSES:
                raise StatsToolkitUnavailableError(
                    f"Stats Toolkit run {run_id} did not reach terminal state (status={status})"
                )

        if status == "rejected":
            err = envelope.get("error")
            detail = err.get("message") if isinstance(err, dict) else str(err or "rejected")
            raise StatsToolkitValidationError(f"Stats Toolkit rejected run {run_id}: {detail}")
        if status != "completed":
            err = envelope.get("error")
            detail = err.get("message") if isinstance(err, dict) else str(err or status)
            if status == "recovery_required":
                raise StatsToolkitUnavailableError(f"Stats Toolkit run {run_id} requires recovery: {detail}")
            raise StatsToolkitError(f"Stats Toolkit run {run_id} ended with status={status}: {detail}")

        result = envelope.get("result")
        if not isinstance(result, dict):
            raise StatsToolkitUnavailableError(f"Stats Toolkit run {run_id} missing result object")

        tool_obj = envelope.get("tool") if isinstance(envelope.get("tool"), dict) else {}
        run_ref: dict[str, Any] = {
            "run_id": run_id,
            "workspace_id": str(envelope.get("workspace_id") or resolved_ws),
            "tool_id": str(envelope.get("tool_id") or tool_obj.get("id") or tool_id),
            "tool_version": str(envelope.get("tool_version") or tool_obj.get("version") or "1.0.0"),
            "method_version": _extract_method_version(envelope, result),
        }
        return run_ref, result

    def read_run_outputs(self, run_id: str) -> dict[str, Any]:
        """Read persisted outputs for a completed Toolkit run via GET /api/v1/runs/{run_id}."""
        envelope = self.get_run(run_id)
        status = str(envelope.get("status") or "").strip().lower()
        if status != "completed":
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit run {run_id} is not completed (status={status})"
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise StatsToolkitUnavailableError(f"Stats Toolkit run {run_id} missing result object")
        return result


def get_stats_toolkit_client(
    base_url: str | None = None,
    timeout: float | None = None,
) -> StatsToolkitClient:
    return StatsToolkitClient(base_url=base_url, timeout=timeout)


def resolve_stats_toolkit_workspace_id(
    client: StatsToolkitClient | None = None,
    *,
    name: str = DEFAULT_BMS_WORKSPACE_NAME,
) -> str:
    active_client = client if client is not None else get_stats_toolkit_client()
    return active_client.resolve_workspace_id(name=name)


__all__ = [
    "DEFAULT_BMS_WORKSPACE_DESCRIPTION",
    "DEFAULT_BMS_WORKSPACE_NAME",
    "DEFAULT_STATS_TOOLKIT_TIMEOUT_SECONDS",
    "DEFAULT_STATS_TOOLKIT_URL",
    "STATS_TOOLKIT_UNAVAILABLE_REASON",
    "StatsToolkitClient",
    "StatsToolkitError",
    "StatsToolkitNotFoundError",
    "StatsToolkitUnavailableError",
    "StatsToolkitValidationError",
    "clear_workspace_cache",
    "get_stats_toolkit_client",
    "resolve_stats_toolkit_workspace_id",
    "stats_toolkit_required",
]

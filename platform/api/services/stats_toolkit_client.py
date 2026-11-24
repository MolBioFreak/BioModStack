"""Typed HTTP client for the standalone BioModStack Stats Toolkit service."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_STATS_TOOLKIT_URL = "http://127.0.0.1:18181"
DEFAULT_STATS_TOOLKIT_TIMEOUT_SECONDS = 15.0
DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS = 2.0


class StatsToolkitError(RuntimeError):
    """Base error for Stats Toolkit service interactions."""


class StatsToolkitUnavailableError(StatsToolkitError):
    """Raised when the optional Stats Toolkit service is unreachable, times out, or returns 5xx."""


class StatsToolkitValidationError(StatsToolkitError):
    """Raised when the Stats Toolkit rejects inputs with HTTP 422."""


class StatsToolkitNotFoundError(StatsToolkitError):
    """Raised when a requested tool or run does not exist (HTTP 404)."""


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
        timeout: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = self.timeout if timeout is None else float(timeout)
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=effective_timeout, transport=self._transport) as client:
                response = client.request(method, url, json=json_body)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit unavailable at {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise StatsToolkitUnavailableError(
                f"Stats Toolkit HTTP transport error at {self.base_url}: {exc}"
            ) from exc

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
    ) -> dict[str, Any]:
        body = {
            "schema_version": schema_version,
            "workspace_id": workspace_id,
            "tool_id": tool_id,
            "inputs": inputs,
        }
        return self._request("POST", "/api/v1/runs", json_body=body)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/runs/{run_id}",
            timeout=DEFAULT_STATS_TOOLKIT_READ_TIMEOUT_SECONDS,
        )


def get_stats_toolkit_client(
    base_url: str | None = None,
    timeout: float | None = None,
) -> StatsToolkitClient:
    return StatsToolkitClient(base_url=base_url, timeout=timeout)


__all__ = [
    "DEFAULT_STATS_TOOLKIT_TIMEOUT_SECONDS",
    "DEFAULT_STATS_TOOLKIT_URL",
    "StatsToolkitClient",
    "StatsToolkitError",
    "StatsToolkitNotFoundError",
    "StatsToolkitUnavailableError",
    "StatsToolkitValidationError",
    "get_stats_toolkit_client",
]

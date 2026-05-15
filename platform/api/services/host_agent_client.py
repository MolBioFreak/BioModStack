from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_HOST_AGENT_TIMEOUT_SECONDS = 2.0


class HostAgentRequestError(RuntimeError):
    def __init__(self, *, status_code: int, detail: Any, url: str) -> None:
        self.status_code = int(status_code)
        self.detail = detail
        self.url = url
        super().__init__(f"Host Agent request to {url} failed with status {status_code}: {detail!r}")


def host_agent_base_url() -> str | None:
    raw_value = os.getenv("BMS_HOST_AGENT_URL")
    if raw_value is None:
        return None
    normalized = raw_value.strip().rstrip("/")
    return normalized or None


def host_agent_enabled() -> bool:
    return host_agent_base_url() is not None


def host_agent_timeout_seconds() -> float:
    raw_value = os.getenv("BMS_HOST_AGENT_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_HOST_AGENT_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_HOST_AGENT_TIMEOUT_SECONDS
    return max(0.1, min(value, 30.0))


def _decode_json_response(raw_body: str, *, url: str) -> Any:
    try:
        return json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Host Agent returned invalid JSON for {url}: {raw_body!r}") from exc


def request_host_agent(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    query: dict[str, Any] | None = None,
) -> Any:
    base_url = host_agent_base_url()
    if not base_url:
        raise RuntimeError("BMS_HOST_AGENT_URL is not configured")

    url = f"{base_url}{path}"
    if query:
        encoded = urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
        if encoded:
            url = f"{url}?{encoded}"

    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=host_agent_timeout_seconds()) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        detail: Any = raw_body
        if raw_body:
            try:
                detail = _decode_json_response(raw_body, url=url)
            except RuntimeError:
                detail = raw_body
        raise HostAgentRequestError(status_code=exc.code, detail=detail, url=url) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Host Agent request to {url} failed: {exc}") from exc

    return _decode_json_response(raw_body, url=url)


def _request_object(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = request_host_agent(method, path, payload, query=query)
    if not isinstance(parsed, dict):
        base_url = host_agent_base_url() or "<unconfigured>"
        raise RuntimeError(f"Host Agent returned non-object JSON for {base_url}{path}: {parsed!r}")
    return parsed


def _service_path(service_id: str, suffix: str = "") -> str:
    quoted_service_id = urllib.parse.quote(str(service_id), safe="")
    return f"/api/host-agent/services/{quoted_service_id}{suffix}"


def get_host_agent_service(service_id: str, *, tail: int | None = None) -> dict[str, Any]:
    return _request_object("GET", _service_path(service_id), query={"tail": tail})


def run_host_agent_service_action(
    service_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quoted_action = urllib.parse.quote(str(action), safe="")
    return _request_object("POST", _service_path(service_id, f"/{quoted_action}"), payload)

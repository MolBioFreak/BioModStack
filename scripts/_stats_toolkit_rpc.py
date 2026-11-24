"""Zero-dependency stdlib RPC helper for standalone scripts calling BioModStack Stats Toolkit."""

from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request

DEFAULT_STATS_TOOLKIT_URL = "http://127.0.0.1:18181"


class StatsToolkitRpcError(RuntimeError):
    """Raised when a Stats Toolkit RPC request fails or is rejected."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def execute_stats_tool(
    tool_id: str,
    payload: dict[str, Any],
    *,
    base_url: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute an allowlisted Stats Toolkit tool via HTTP and return its result payload."""
    root = (base_url or os.environ.get("BMS_STATS_TOOLKIT_URL") or DEFAULT_STATS_TOOLKIT_URL).strip().rstrip("/")
    url = f"{root}/api/v1/tools/{tool_id}/execute"
    encoded = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise StatsToolkitRpcError(
            f"Stats Toolkit HTTP {exc.code} for {tool_id}: {detail}",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StatsToolkitRpcError(f"Stats Toolkit unreachable at {root}: {exc}") from exc

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StatsToolkitRpcError(f"Stats Toolkit returned invalid JSON for {tool_id}") from exc

    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        raise StatsToolkitRpcError(f"Stats Toolkit response for {tool_id} missing result object")
    return envelope["result"]


__all__ = ["DEFAULT_STATS_TOOLKIT_URL", "StatsToolkitRpcError", "execute_stats_tool"]

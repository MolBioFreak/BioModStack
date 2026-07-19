from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:18181"
DEFAULT_ENTRY_URL = "http://127.0.0.1:18180/stats/"


def _read_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def probe_stats_addon() -> dict[str, Any]:
    base_url = os.getenv("BMS_STATS_TOOLKIT_URL", DEFAULT_BASE_URL).rstrip("/")
    entry_url = os.getenv("BMS_STATS_TOOLKIT_ENTRY_URL", DEFAULT_ENTRY_URL)
    timeout = float(os.getenv("BMS_STATS_TOOLKIT_TIMEOUT_SECONDS", "1.5"))
    result: dict[str, Any] = {
        "id": "bms-stats-toolkit",
        "display_name": "BioModStack Stats Toolkit",
        "available": False,
        "ready": False,
        "version": None,
        "api_version": None,
        "capability_count": 0,
        "entry_url": entry_url,
        "detail": "standalone service unavailable",
    }
    try:
        ready = _read_json(f"{base_url}/health/ready", timeout)
        capabilities = _read_json(f"{base_url}/api/v1/capabilities", timeout)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        result["detail"] = f"standalone probe failed: {exc.__class__.__name__}"
        return result

    capability_items = capabilities.get("capabilities")
    result.update(
        available=True,
        ready=ready.get("status") == "ready",
        version=ready.get("version"),
        api_version=capabilities.get("api_version"),
        capability_count=len(capability_items) if isinstance(capability_items, list) else 0,
        detail="standalone service ready" if ready.get("status") == "ready" else "standalone service not ready",
    )
    return result

"""Read-only Vast.ai instance inventory adapter."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .contracts import DiscoveredExecutionTarget, ExecutionTargetInventoryResponse

DEFAULT_API_BASE = "https://console.vast.ai/api/v0"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class VastInventoryError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "Vast redirect refused", headers, fp)


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value)) if integer else float(value)
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _instances(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise VastInventoryError("Vast returned an invalid instance inventory")
    for key in ("instances", "offers", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise VastInventoryError("Vast returned no instance inventory array")


def _normalize(item: dict[str, Any]) -> DiscoveredExecutionTarget:
    raw_id = item.get("id") or item.get("instance_id") or item.get("contract_id")
    if raw_id is None:
        raise VastInventoryError("Vast instance is missing its provider identity")
    provider_state = str(
        item.get("actual_status") or item.get("status") or item.get("state") or "unknown"
    ).strip().lower()
    raw_gpu_ram = _number(item.get("gpu_ram") or item.get("gpu_ram_mb"), integer=True)
    gpu_ram = int(raw_gpu_ram) if raw_gpu_ram is not None else None
    if gpu_ram is not None and gpu_ram < 1024:
        gpu_ram *= 1024
    raw_port = _number(item.get("ssh_port") or item.get("direct_port_start"), integer=True)
    port = int(raw_port) if raw_port is not None else None
    raw_gpu_count = _number(item.get("num_gpus") or item.get("gpu_count"), integer=True)
    gpu_count = int(raw_gpu_count) if raw_gpu_count is not None else 0
    return DiscoveredExecutionTarget(
        provider="vast",
        provider_instance_id=str(raw_id),
        name=(str(item.get("label") or item.get("name") or "").strip() or None),
        provider_state=provider_state,
        host=(str(item.get("ssh_host") or item.get("public_ipaddr") or "").strip() or None),
        port=port,
        username=(str(item.get("ssh_user") or item.get("username") or "root").strip() or "root"),
        gpu_name=(str(item.get("gpu_name") or item.get("gpu_model") or "").strip() or None),
        gpu_count=gpu_count,
        gpu_vram_mb=gpu_ram,
        hourly_rate_usd=_number(item.get("dph_total") or item.get("dph_base") or item.get("hourly_rate")),
        started_at=_datetime(item.get("start_date") or item.get("started_at") or item.get("start_time")),
        verified=(bool(item.get("verification")) if item.get("verification") is not None else None),
        # The provider payload is not part of the public or persisted target
        # contract. Keep only normalized, explicitly typed fields above.
        raw={},
    )


def _fetch_owned_instances() -> ExecutionTargetInventoryResponse:
    api_key = os.getenv("VAST_API_KEY", "").strip()
    if not api_key:
        return ExecutionTargetInventoryResponse(
            provider="vast",
            available=False,
            credential_configured=False,
            message="Vast credential is not configured in the API runtime",
            instances=[],
        )
    base = os.getenv("BMS_VAST_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")
    parsed_base = urllib.parse.urlsplit(base)
    if parsed_base.scheme != "https" or parsed_base.hostname != "console.vast.ai":
        raise VastInventoryError("Vast API base URL is not the approved HTTPS origin")
    request = urllib.request.Request(
        f"{base}/instances/?owner=me",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "BioModStack-remote-execution/1",
        },
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=15) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise VastInventoryError("Vast instance inventory returned an invalid content type")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VastInventoryError("Vast instance inventory is unavailable") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise VastInventoryError("Vast instance inventory exceeded the response limit")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VastInventoryError("Vast returned invalid JSON") from exc
    normalized: list[DiscoveredExecutionTarget] = []
    for item in _instances(payload):
        try:
            normalized.append(_normalize(item))
        except VastInventoryError:
            continue
    return ExecutionTargetInventoryResponse(
        provider="vast",
        available=True,
        credential_configured=True,
        message="Owned Vast instances refreshed",
        instances=normalized,
    )


async def list_owned_instances() -> ExecutionTargetInventoryResponse:
    return await asyncio.to_thread(_fetch_owned_instances)


async def get_owned_instance(provider_instance_id: str) -> DiscoveredExecutionTarget:
    inventory = await list_owned_instances()
    if not inventory.available:
        raise VastInventoryError(inventory.message)
    for instance in inventory.instances:
        if instance.provider_instance_id == str(provider_instance_id):
            return instance
    raise VastInventoryError("The selected Vast instance is absent from the owned-instance inventory")

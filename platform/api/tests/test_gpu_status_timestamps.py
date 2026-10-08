from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from routers import gpu


@pytest.mark.asyncio
async def test_native_gpu_status_endpoints_emit_timezone_aware_utc_timestamps(monkeypatch) -> None:
    cpu = SimpleNamespace(utilization=12.5)
    ram = SimpleNamespace(utilization=34.5)

    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: False)
    monkeypatch.setattr(gpu, "get_cpu_stats", lambda: cpu)
    monkeypatch.setattr(gpu, "get_ram_stats", lambda: ram)
    monkeypatch.setattr(gpu, "get_gpu_stats_with_error", lambda: ([], None))
    monkeypatch.setattr(gpu, "SystemStatusResponse", lambda **payload: payload)

    payloads = [
        await gpu.get_system_status(),
        await gpu.get_gpus_only(),
        await gpu.get_cpu_only(),
        await gpu.get_ram_only(),
    ]

    for payload in payloads:
        timestamp = payload["timestamp"]
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == timedelta(0)
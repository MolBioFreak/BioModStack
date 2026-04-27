from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import gpu


def test_status_power_limits_use_discovered_hardware_limits(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu,
        "HARDWARE_LIMITS",
        {2: {"min": 100, "default": 370, "max": 380, "eco": 277, "name": "RTX 3090"}},
    )

    assert gpu._status_power_limits_for_gpu(2, 250.0) == {
        "min": 100,
        "default": 370,
        "max": 380,
        "eco": 277,
        "name": "RTX 3090",
    }


def test_status_power_limits_do_not_fabricate_writable_range_for_unknown_gpu(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {})

    assert gpu._status_power_limits_for_gpu(7, 215.4) == {
        "min": 215,
        "default": 215,
        "max": 215,
        "eco": 215,
        "name": "GPU 7",
    }


def test_status_power_limits_collapse_to_zero_when_live_limit_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {})

    assert gpu._status_power_limits_for_gpu(3, 0.0) == {
        "min": 0,
        "default": 0,
        "max": 0,
        "eco": 0,
        "name": "GPU 3",
    }

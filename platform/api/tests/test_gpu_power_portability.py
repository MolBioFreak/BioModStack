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


def test_valid_gpu_indices_for_mutation_uses_local_metadata_first(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {3: {}, 1: {}})
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: True)
    monkeypatch.setattr(
        gpu,
        "_gpu_proxy_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("proxy should not be called")),
    )

    assert gpu._valid_gpu_indices_for_mutation() == [1, 3]


def test_valid_gpu_indices_for_mutation_falls_back_to_proxied_status(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {})
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: True)
    monkeypatch.setattr(
        gpu,
        "_gpu_proxy_request",
        lambda method, path: {
            "gpus": [
                {"index": 2, "name": "RTX 3090"},
                {"index": "0", "name": "RTX 5090"},
                {"index": "bad"},
                {"index": 2, "name": "duplicate"},
            ]
        },
    )

    assert gpu._valid_gpu_indices_for_mutation() == [0, 2]


def test_validate_gpu_index_for_mutation_reports_proxied_valid_indices(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {})
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: True)
    monkeypatch.setattr(gpu, "_gpu_proxy_request", lambda method, path: {"gpus": [{"index": 0}, {"index": 1}]})

    gpu._validate_gpu_index_for_mutation(1)

    try:
        gpu._validate_gpu_index_for_mutation(4)
    except Exception as exc:
        assert exc.status_code == 400
        assert exc.detail == "Invalid GPU index: 4. Valid: 0,1."
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected HTTPException")

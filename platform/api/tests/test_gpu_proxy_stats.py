from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import routers.gpu as gpu


ADAPTER_GPU_PAYLOAD = {
    "gpus": [
        {
            "index": 2,
            "name": "NVIDIA GeForce RTX 3090",
            "utilization": 0,
            "memory_utilization": 0,
            "memory_used_mb": 466,
            "memory_total_mb": 24576,
            "reserved_memory_mb": 0,
            "power_draw_w": 15.2,
            "power_limit_w": 203.0,
            "min_power_watts": 100,
            "default_power_watts": 370,
            "max_power_watts": 380,
            "temperature": 45,
            "fan_speed": 0,
            "clock_graphics_mhz": 210,
            "clock_memory_mhz": 405,
            "clock_max_graphics_mhz": 1695,
            "clock_max_memory_mhz": 9751,
            "processes": [],
        }
    ],
    "gpu_error": None,
}


def test_gpu_process_labels_follow_live_validator_process_identity() -> None:
    assert gpu._infer_gpu_process_label(
        "python3",
        ["python3", "/opt/esmfold2/run_esmfold2.py", "--input", "candidate.pdb"],
    ) == "ESMFold2"
    assert gpu._infer_gpu_process_label(
        "python3",
        ["python3", "/app/run_protenix_inference.py", "--model_name", "protenix-v2"],
    ) == "Protenix V2"


def test_generic_gpu_process_keeps_its_live_identity() -> None:
    assert gpu._infer_gpu_process_label("python3", []) == "python3"


def test_get_gpu_stats_with_error_uses_workflow_adapter_in_core_runtime_mode(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: True)
    monkeypatch.setattr(gpu, "_gpu_status_cache", [])
    monkeypatch.setattr(gpu, "_gpu_status_error", None)
    monkeypatch.setattr(gpu, "_gpu_status_cache_time", 0.0)

    def adapter_request(method: str, path: str, payload=None):
        calls.append((method, path))
        return ADAPTER_GPU_PAYLOAD

    monkeypatch.setattr(gpu, "request_via_workflow_adapter", adapter_request)

    gpus, error = gpu.get_gpu_stats_with_error(force_refresh=True)

    assert error is None
    assert len(gpus) == 1
    assert gpus[0].index == 2
    assert gpus[0].name == "NVIDIA GeForce RTX 3090"
    assert calls == [("GET", "/api/gpu/gpus")]

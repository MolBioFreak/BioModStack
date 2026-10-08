from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TELEMETRY_SCRIPT = REPO_ROOT / "scripts" / "bms_gpu_run_telemetry.py"


def _load_telemetry_module():
    spec = importlib.util.spec_from_file_location("bms_gpu_run_telemetry", TELEMETRY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gpu_telemetry_summary_uses_measured_process_memory_not_estimates() -> None:
    telemetry = _load_telemetry_module()
    start = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 30, 12, 0, 3, tzinfo=timezone.utc)
    samples = [
        telemetry.TelemetrySample(
            timestamp="2026-05-30T12:00:00.000000+00:00",
            gpus=[{"index": 0, "memory_used_mb": 1000, "memory_total_mb": 32607, "utilization_gpu_pct": 0}],
            compute_apps=[],
            tracked_pids=[9001],
            tracked_compute_apps=[],
        ),
        telemetry.TelemetrySample(
            timestamp="2026-05-30T12:00:01.000000+00:00",
            gpus=[{"index": 0, "memory_used_mb": 7100, "memory_total_mb": 32607, "utilization_gpu_pct": 92}],
            compute_apps=[{"gpu_index": 0, "pid": 9001, "process_name": "python3", "used_gpu_memory_mb": 6120}],
            tracked_pids=[9001, 9002],
            tracked_compute_apps=[{"gpu_index": 0, "pid": 9001, "process_name": "python3", "used_gpu_memory_mb": 6120}],
        ),
        telemetry.TelemetrySample(
            timestamp="2026-05-30T12:00:02.000000+00:00",
            gpus=[{"index": 0, "memory_used_mb": 7600, "memory_total_mb": 32607, "utilization_gpu_pct": 87}],
            compute_apps=[{"gpu_index": 0, "pid": 9001, "process_name": "python3", "used_gpu_memory_mb": 6726}],
            tracked_pids=[9001, 9002],
            tracked_compute_apps=[{"gpu_index": 0, "pid": 9001, "process_name": "python3", "used_gpu_memory_mb": 6726}],
        ),
    ]

    summary = telemetry.build_summary(
        label="RunESMFold2Experimental",
        command=["python3", "/scripts/run_esmfold2_inference.py"],
        start_time=start,
        end_time=end,
        exit_code=0,
        samples=samples,
        interval_seconds=1.0,
    )

    assert summary["schema"] == "bms.gpu_run_telemetry.v1"
    assert summary["label"] == "RunESMFold2Experimental"
    assert summary["status"] == "completed"
    assert summary["measurement_mode"] == "measured"
    assert summary["attribution_status"] == "process_attributed"
    assert summary["peak_process_gpu_memory_mb"] == 6726
    assert summary["peak_process_gpu_memory_by_gpu_mb"] == {"0": 6726}
    assert summary["peak_device_memory_used_mb"] == 7600
    assert summary["peak_device_memory_used_by_gpu_mb"] == {"0": 7600}
    assert summary["sample_count"] == 3
    assert summary["source"] == "nvidia-smi"
    assert "vram_estimate_mb" not in summary


def test_gpu_telemetry_summary_marks_unavailable_instead_of_guessing() -> None:
    telemetry = _load_telemetry_module()
    start = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    summary = telemetry.build_summary(
        label="RunESMFold2Experimental",
        command=["python3", "/scripts/run_esmfold2_inference.py"],
        start_time=start,
        end_time=start,
        exit_code=0,
        samples=[],
        interval_seconds=1.0,
        telemetry_error="nvidia-smi unavailable",
    )

    assert summary["measurement_mode"] == "unavailable"
    assert summary["attribution_status"] == "unavailable"
    assert summary["peak_process_gpu_memory_mb"] is None
    assert summary["peak_device_memory_used_mb"] is None
    assert summary["telemetry_error"] == "nvidia-smi unavailable"
    assert "vram_estimate_mb" not in summary

#!/usr/bin/env python3
"""
BioModStack-owned GPU telemetry wrapper for model/runtime commands.

This intentionally does not import model frameworks (torch/JAX/etc.) and does not
compute estimates. It samples the GPU runtime via nvidia-smi while a command runs
and writes measured values or explicit unavailable/no-attribution markers.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = "bms.gpu_run_telemetry.v1"
SOURCE = "nvidia-smi"
DEFAULT_INTERVAL_SECONDS = 1.0


class TelemetrySample:
    def __init__(
        self,
        *,
        timestamp: str,
        gpus: list[dict[str, Any]],
        compute_apps: list[dict[str, Any]],
        tracked_pids: list[int],
        tracked_compute_apps: list[dict[str, Any]],
    ) -> None:
        self.timestamp = timestamp
        self.gpus = gpus
        self.compute_apps = compute_apps
        self.tracked_pids = tracked_pids
        self.tracked_compute_apps = tracked_compute_apps

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "gpus": self.gpus,
            "compute_apps": self.compute_apps,
            "tracked_pids": self.tracked_pids,
            "tracked_compute_apps": self.tracked_compute_apps,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "[N/A]", "NOT SUPPORTED", "[NOT SUPPORTED]"}:
        return None
    # nvidia-smi nounits should already remove units, but tolerate suffixes.
    text = text.replace("MiB", "").replace("MB", "").replace("%", "").strip()
    try:
        return int(float(text))
    except ValueError:
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "[N/A]", "NOT SUPPORTED", "[NOT SUPPORTED]"}:
        return None
    text = text.replace("W", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _clean_cell(value: str) -> str:
    return value.strip().strip('"')


def _run_nvidia_smi(nvidia_smi: str, query: str) -> str:
    result = subprocess.run(
        [nvidia_smi, query, "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"nvidia-smi exited {result.returncode}").strip())
    return result.stdout


def query_gpu_snapshot(nvidia_smi: str = "nvidia-smi") -> list[dict[str, Any]]:
    fields = [
        "index",
        "uuid",
        "name",
        "memory.used",
        "memory.total",
        "utilization.gpu",
        "power.draw",
        "power.limit",
    ]
    output = _run_nvidia_smi(nvidia_smi, f"--query-gpu={','.join(fields)}")
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        cells = [_clean_cell(cell) for cell in line.split(",")]
        while len(cells) < len(fields):
            cells.append("")
        index = _coerce_int(cells[0])
        gpus.append(
            {
                "index": index,
                "uuid": cells[1] or None,
                "name": cells[2] or None,
                "memory_used_mb": _coerce_int(cells[3]),
                "memory_total_mb": _coerce_int(cells[4]),
                "utilization_gpu_pct": _coerce_int(cells[5]),
                "power_draw_w": _coerce_float(cells[6]),
                "power_limit_w": _coerce_float(cells[7]),
            }
        )
    return gpus


def query_compute_apps(nvidia_smi: str = "nvidia-smi", gpu_uuid_to_index: Optional[dict[str, int]] = None) -> list[dict[str, Any]]:
    fields = ["gpu_uuid", "pid", "process_name", "used_gpu_memory"]
    output = _run_nvidia_smi(nvidia_smi, f"--query-compute-apps={','.join(fields)}")
    apps: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        cells = [_clean_cell(cell) for cell in line.split(",")]
        while len(cells) < len(fields):
            cells.append("")
        gpu_uuid = cells[0] or None
        apps.append(
            {
                "gpu_uuid": gpu_uuid,
                "gpu_index": gpu_uuid_to_index.get(gpu_uuid) if gpu_uuid_to_index and gpu_uuid else None,
                "pid": _coerce_int(cells[1]),
                "process_name": cells[2] or None,
                "used_gpu_memory_mb": _coerce_int(cells[3]),
            }
        )
    return apps


def collect_descendant_pids(root_pid: int) -> list[int]:
    """Collect root_pid and descendants using /proc, avoiding ps/pgrep dependencies."""
    parent_by_pid: dict[int, int] = {}
    for proc_entry in Path("/proc").iterdir():
        if not proc_entry.name.isdigit():
            continue
        stat_path = proc_entry / "stat"
        try:
            stat = stat_path.read_text(errors="replace")
        except OSError:
            continue
        # /proc/<pid>/stat has comm in parentheses and ppid as field 4.
        try:
            after_comm = stat.rsplit(")", 1)[1].strip().split()
            ppid = int(after_comm[1])
            pid = int(proc_entry.name)
        except Exception:
            continue
        parent_by_pid[pid] = ppid

    descendants = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent_by_pid.items():
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return sorted(descendants)


def take_sample(root_pid: int, nvidia_smi: str = "nvidia-smi") -> TelemetrySample:
    gpus = query_gpu_snapshot(nvidia_smi)
    uuid_to_index = {str(gpu.get("uuid")): gpu.get("index") for gpu in gpus if gpu.get("uuid") and gpu.get("index") is not None}
    compute_apps = query_compute_apps(nvidia_smi, uuid_to_index)
    tracked_pids = collect_descendant_pids(root_pid)
    tracked_set = set(tracked_pids)
    tracked_compute_apps = [app for app in compute_apps if app.get("pid") in tracked_set]
    return TelemetrySample(
        timestamp=isoformat(utc_now()),
        gpus=gpus,
        compute_apps=compute_apps,
        tracked_pids=tracked_pids,
        tracked_compute_apps=tracked_compute_apps,
    )


def _sample_to_dict(sample: Any) -> dict[str, Any]:
    if isinstance(sample, TelemetrySample):
        return sample.to_dict()
    return dict(sample)


def _max_non_null(values: Iterable[Optional[int]]) -> Optional[int]:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _sum_non_null(values: Iterable[Optional[int]]) -> Optional[int]:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _update_peak_by_gpu(target: dict[str, int], gpu_index: Any, value: Optional[int]) -> None:
    if gpu_index is None or value is None:
        return
    key = str(gpu_index)
    target[key] = max(target.get(key, 0), value)


def build_summary(
    *,
    label: str,
    command: list[str],
    start_time: datetime,
    end_time: datetime,
    exit_code: int,
    samples: list[Any],
    interval_seconds: float,
    telemetry_error: Optional[str] = None,
) -> dict[str, Any]:
    sample_dicts = [_sample_to_dict(sample) for sample in samples]
    duration_seconds = max(0.0, (end_time - start_time).total_seconds())

    peak_device_by_gpu: dict[str, int] = {}
    peak_process_by_gpu: dict[str, int] = {}
    per_sample_device_peaks: list[Optional[int]] = []
    per_sample_process_totals: list[Optional[int]] = []
    matched_process_sample_count = 0

    for sample in sample_dicts:
        gpu_values = []
        for gpu in sample.get("gpus") or []:
            value = _coerce_int(gpu.get("memory_used_mb"))
            gpu_values.append(value)
            _update_peak_by_gpu(peak_device_by_gpu, gpu.get("index"), value)
        per_sample_device_peaks.append(_max_non_null(gpu_values))

        by_gpu_this_sample: dict[str, list[Optional[int]]] = {}
        for app in sample.get("tracked_compute_apps") or []:
            gpu_index = app.get("gpu_index")
            key = str(gpu_index) if gpu_index is not None else "unknown"
            by_gpu_this_sample.setdefault(key, []).append(_coerce_int(app.get("used_gpu_memory_mb")))
        if by_gpu_this_sample:
            matched_process_sample_count += 1
        sample_process_values = []
        for key, values in by_gpu_this_sample.items():
            value = _sum_non_null(values)
            sample_process_values.append(value)
            if key != "unknown":
                _update_peak_by_gpu(peak_process_by_gpu, key, value)
        per_sample_process_totals.append(_sum_non_null(sample_process_values))

    peak_process = _max_non_null(per_sample_process_totals)
    peak_device = _max_non_null(per_sample_device_peaks)

    if telemetry_error and not sample_dicts:
        measurement_mode = "unavailable"
        attribution_status = "unavailable"
    elif not sample_dicts:
        measurement_mode = "unavailable"
        attribution_status = "unavailable"
    elif peak_process is not None:
        measurement_mode = "measured"
        attribution_status = "process_attributed"
    else:
        measurement_mode = "measured"
        attribution_status = "device_only_no_process_match"

    return {
        "schema": SCHEMA,
        "label": label,
        "command": command,
        "start_time": isoformat(start_time),
        "end_time": isoformat(end_time),
        "duration_seconds": duration_seconds,
        "exit_code": int(exit_code),
        "status": "completed" if int(exit_code) == 0 else "failed",
        "source": SOURCE,
        "measurement_mode": measurement_mode,
        "attribution_status": attribution_status,
        "telemetry_error": telemetry_error,
        "sample_interval_seconds": interval_seconds,
        "sample_count": len(sample_dicts),
        "matched_process_sample_count": matched_process_sample_count,
        "peak_process_gpu_memory_mb": peak_process,
        "peak_process_gpu_memory_by_gpu_mb": peak_process_by_gpu,
        "peak_device_memory_used_mb": peak_device,
        "peak_device_memory_used_by_gpu_mb": peak_device_by_gpu,
        "samples": sample_dicts,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_monitored_command(
    *,
    label: str,
    command: list[str],
    output_json: Path,
    interval_seconds: float,
    nvidia_smi: str,
) -> int:
    samples: list[TelemetrySample] = []
    telemetry_error: Optional[str] = None
    stop_event = threading.Event()
    process = subprocess.Popen(command)
    start_time = utc_now()

    def monitor() -> None:
        nonlocal telemetry_error
        while not stop_event.is_set():
            try:
                samples.append(take_sample(process.pid, nvidia_smi=nvidia_smi))
            except Exception as exc:  # keep the wrapped model alive even if telemetry is unavailable
                telemetry_error = str(exc)
                if not samples:
                    # If nvidia-smi is completely unavailable, stop hammering it.
                    stop_event.wait(interval_seconds)
                    break
            stop_event.wait(interval_seconds)

    monitor_thread = threading.Thread(target=monitor, name="bms-gpu-telemetry", daemon=True)
    monitor_thread.start()
    try:
        exit_code = process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        exit_code = process.wait()
    finally:
        stop_event.set()
        monitor_thread.join(timeout=max(1.0, interval_seconds * 2))
    end_time = utc_now()

    summary = build_summary(
        label=label,
        command=command,
        start_time=start_time,
        end_time=end_time,
        exit_code=exit_code,
        samples=samples,
        interval_seconds=interval_seconds,
        telemetry_error=telemetry_error,
    )
    _write_json(output_json, summary)
    print(
        "[BMS telemetry] "
        f"label={label} status={summary['status']} measurement={summary['measurement_mode']} "
        f"attribution={summary['attribution_status']} peak_process_gpu_mb={summary['peak_process_gpu_memory_mb']} "
        f"peak_device_gpu_mb={summary['peak_device_memory_used_mb']} output={output_json}",
        flush=True,
    )
    return int(exit_code)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a command while recording BMS-owned GPU telemetry")
    parser.add_argument("--label", required=True, help="Human/process label for the telemetry artifact")
    parser.add_argument("--output-json", required=True, type=Path, help="Telemetry summary JSON path")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("BMS_GPU_TELEMETRY_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Sampling interval in seconds",
    )
    parser.add_argument("--nvidia-smi", default=os.getenv("BMS_NVIDIA_SMI", "nvidia-smi"))
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("command is required after --")
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be > 0")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return run_monitored_command(
        label=args.label,
        command=args.command,
        output_json=args.output_json,
        interval_seconds=args.interval_seconds,
        nvidia_smi=args.nvidia_smi,
    )


if __name__ == "__main__":
    raise SystemExit(main())

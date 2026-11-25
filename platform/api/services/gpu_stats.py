"""GPU statistics collection service shared by the API router and background worker."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from paths import get_code_root
from runtime_policy import core_runtime_mode_enabled
from services.gpu_metadata import HARDWARE_LIMITS
from services.workflow_adapter import request_via_workflow_adapter

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_code_root()
GPU_RESERVATIONS_PATH = PROJECT_ROOT / ".gpu_reservations.json"


class GPUProcess(BaseModel):
    """Process running on a GPU."""
    pid: int
    name: str
    memory_mb: int


class GPUStatusEnhanced(BaseModel):
    """Enhanced GPU status with all metrics."""
    index: int
    name: str
    # Utilization
    utilization: int  # GPU compute %
    memory_utilization: int  # Memory controller %
    # Memory
    memory_used_mb: int
    memory_total_mb: int
    reserved_memory_mb: int = 0  # Virtual usage from scheduler reservations
    # Power
    power_draw_w: float
    power_limit_w: float
    min_power_watts: int
    default_power_watts: int
    max_power_watts: int
    # Temperature & Cooling
    temperature: int
    fan_speed: int  # percentage
    # Clocks
    clock_graphics_mhz: int
    clock_memory_mhz: int
    clock_max_graphics_mhz: int
    clock_max_memory_mhz: int
    # Processes
    processes: List[GPUProcess]


_gpu_status_cache: List[GPUStatusEnhanced] = []
_gpu_status_error: Optional[str] = None
_gpu_status_cache_time: float = 0.0
_GPU_STATUS_CACHE_TTL_SECONDS = 1.25


def invalidate_gpu_stats_cache() -> None:
    """Clear cached GPU telemetry so the next sample reflects the live host."""
    global _gpu_status_cache, _gpu_status_error, _gpu_status_cache_time
    _gpu_status_cache = []
    _gpu_status_error = None
    _gpu_status_cache_time = 0.0


def _gpu_proxy_enabled() -> bool:
    return core_runtime_mode_enabled()


def _infer_gpu_process_label(proc_name: str, cmdline: Optional[List[str]]) -> str:
    """Infer a model label only from the live process identity."""
    base_name = proc_name or ""
    cmdline_list = cmdline or []
    cmdline_str = " ".join(cmdline_list)
    haystack = f"{base_name} {cmdline_str}".lower()

    patterns = [
        ("dorado_basecall_server", "Dorado Basecall Server"),
        ("dorado-basecall-server", "Dorado Basecall Server"),
        ("dorado_basecall", "Dorado Basecall Server"),
        ("basecall_manager", "Basecall Manager"),
        ("run_esmfold2", "ESMFold2"),
        ("esmfold2", "ESMFold2"),
        ("run_protenix_inference", "Protenix V2"),
        ("protenix", "Protenix V2"),
        ("seq_design.py", "FAMPNN"),
        ("fampnn", "FAMPNN"),
        ("ppiflow", "PPIFlow"),
        ("rfdiffusion_inference.py", "RFantibody"),
        ("rfantibody", "RFantibody"),
        ("boltzgen", "BoltzGen"),
        ("boltz", "Boltz-2"),
        ("alphafold", "AlphaFold2"),
        ("af2", "AlphaFold2"),
        ("frustrampnn", "FrustraMPNN"),
        ("frustra", "FrustraMPNN"),
        ("ligandmpnn", "LigandMPNN"),
        ("proteinmpnn", "ProteinMPNN"),
        ("mpnn", "ProteinMPNN"),
        ("rf3", "RoseTTAFold3"),
        ("antiberty", "AntiBERTy"),
        ("anarcii", "ANARCII"),
        ("immunebuilder", "ImmuneBuilder"),
    ]
    for key, label in patterns:
        if key in haystack:
            return label

    if base_name.lower() in ["python", "python3", "python3.10", "python3.11"]:
        if len(cmdline_list) > 1:
            script_idx = 2 if cmdline_list[1] == "-m" and len(cmdline_list) > 2 else 1
            script = os.path.basename(cmdline_list[script_idx])
            if script:
                return script
    pretty_name = re.sub(r"[_-]+", " ", base_name).strip()
    return pretty_name or base_name


def _status_power_limits_for_gpu(gpu_index: int, live_power_limit_w: float) -> Dict[str, Any]:
    """Return display power bounds for telemetry without inventing a writable range."""
    hw = HARDWARE_LIMITS.get(gpu_index)
    if hw:
        return hw

    try:
        live_limit = int(round(float(live_power_limit_w)))
    except (TypeError, ValueError):
        live_limit = 0
    live_limit = max(0, live_limit)
    return {
        "min": live_limit,
        "default": live_limit,
        "max": live_limit,
        "eco": live_limit,
    }


def _collect_gpu_stats() -> tuple[List[GPUStatusEnhanced], Optional[str]]:
    """Get enhanced GPU statistics using pynvml, with nvidia-smi fallback."""
    from services.nvidia_inventory import nvidia_gpu_present

    if nvidia_gpu_present() is False:
        return [], None

    def _load_active_gpu_reservations() -> tuple[Dict[int, int], Dict[int, List[tuple[Optional[str], Optional[str]]]]]:
        reservations: Dict[int, int] = {}
        gpu_job_info: Dict[int, List[tuple[Optional[str], Optional[str]]]] = {}
        try:
            if GPU_RESERVATIONS_PATH.exists():
                with open(GPU_RESERVATIONS_PATH, "r") as f:
                    data = json.load(f)
                    now = time.time() * 1000  # ms

                    for gpu_idx, res_list in data.items():
                        active_vram = 0
                        job_infos: List[tuple[Optional[str], Optional[str]]] = []
                        for res in res_list:
                            if (now - res.get("timestamp", 0)) < 60000:
                                active_vram += res.get("vram", 0)
                                job_infos.append((res.get("job_name"), res.get("model_type")))
                        reservations[int(gpu_idx)] = active_vram
                        if job_infos:
                            gpu_job_info[int(gpu_idx)] = job_infos
        except Exception as e:
            print(f"Error reading reservations: {e}")
        return reservations, gpu_job_info

    def _collect_gpu_stats_via_nvidia_smi() -> tuple[List[GPUStatusEnhanced], Optional[str]]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,temperature.gpu,fan.speed,clocks.current.graphics,clocks.current.memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            return [], str(exc).strip() or exc.__class__.__name__

        if result.returncode != 0:
            raw = (result.stderr or result.stdout or "").strip()
            return [], raw or f"nvidia-smi exited with {result.returncode}"

        reservations, _gpu_job_info = _load_active_gpu_reservations()
        gpus: List[GPUStatusEnhanced] = []

        def _parse_int(raw: str, default: int = 0) -> int:
            try:
                return int(round(float(str(raw).strip())))
            except (TypeError, ValueError):
                return default

        def _parse_float(raw: str, default: float = 0.0) -> float:
            try:
                return float(str(raw).strip())
            except (TypeError, ValueError):
                return default

        for line in result.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 12:
                continue
            try:
                gpu_idx = int(parts[0])
            except (TypeError, ValueError):
                continue

            power_limit_w = round(_parse_float(parts[7], 0.0), 1)
            hw_limits = _status_power_limits_for_gpu(gpu_idx, power_limit_w)
            clock_graphics = _parse_int(parts[10], 0)
            clock_memory = _parse_int(parts[11], 0)
            gpus.append(
                GPUStatusEnhanced(
                    index=gpu_idx,
                    name=parts[1] or f"GPU {gpu_idx}",
                    utilization=_parse_int(parts[2], 0),
                    memory_utilization=_parse_int(parts[3], 0),
                    memory_used_mb=_parse_int(parts[4], 0),
                    memory_total_mb=_parse_int(parts[5], 0),
                    reserved_memory_mb=reservations.get(gpu_idx, 0),
                    power_draw_w=round(_parse_float(parts[6], 0.0), 1),
                    power_limit_w=power_limit_w,
                    min_power_watts=hw_limits["min"],
                    default_power_watts=hw_limits["default"],
                    max_power_watts=hw_limits["max"],
                    temperature=_parse_int(parts[8], 0),
                    fan_speed=_parse_int(parts[9], 0),
                    clock_graphics_mhz=clock_graphics,
                    clock_memory_mhz=clock_memory,
                    clock_max_graphics_mhz=clock_graphics,
                    clock_max_memory_mhz=clock_memory,
                    processes=[],
                )
            )

        if not gpus:
            return [], "nvidia-smi returned no GPU records"
        return gpus, None

    try:
        import pynvml

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []

        reservations, _gpu_job_info = _load_active_gpu_reservations()

        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)

            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")

            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)

            try:
                power_draw = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                power_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
            except pynvml.NVMLError:
                power_draw = 0.0
                power_limit = 0.0

            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

            try:
                fan_speed = pynvml.nvmlDeviceGetFanSpeed(handle)
            except pynvml.NVMLError:
                fan_speed = 0

            try:
                clock_graphics = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                clock_memory = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                clock_max_graphics = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                clock_max_memory = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
            except pynvml.NVMLError:
                clock_graphics = clock_memory = clock_max_graphics = clock_max_memory = 0

            processes = []
            try:
                procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                for proc in procs:
                    try:
                        import psutil

                        p = psutil.Process(proc.pid)
                        proc_name = p.name()
                        cmdline = p.cmdline()
                        proc_name = _infer_gpu_process_label(proc_name, cmdline)
                    except Exception:
                        proc_name = f"PID {proc.pid}"

                    processes.append(
                        GPUProcess(
                            pid=proc.pid,
                            name=proc_name,
                            memory_mb=proc.usedGpuMemory // (1024 * 1024) if proc.usedGpuMemory else 0,
                        )
                    )
            except pynvml.NVMLError:
                pass

            hw_limits = _status_power_limits_for_gpu(i, power_limit)

            gpus.append(
                GPUStatusEnhanced(
                    index=i,
                    name=name,
                    utilization=utilization.gpu,
                    memory_utilization=utilization.memory,
                    memory_used_mb=memory.used // (1024 * 1024),
                    memory_total_mb=memory.total // (1024 * 1024),
                    reserved_memory_mb=reservations.get(i, 0),
                    power_draw_w=round(power_draw, 1),
                    power_limit_w=round(power_limit, 1),
                    min_power_watts=hw_limits["min"],
                    default_power_watts=hw_limits["default"],
                    max_power_watts=hw_limits["max"],
                    temperature=temperature,
                    fan_speed=fan_speed,
                    clock_graphics_mhz=clock_graphics,
                    clock_memory_mhz=clock_memory,
                    clock_max_graphics_mhz=clock_max_graphics,
                    clock_max_memory_mhz=clock_max_memory,
                    processes=processes,
                )
            )

        pynvml.nvmlShutdown()
        return gpus, None

    except Exception as e:
        message = str(e).strip() or e.__class__.__name__
        fallback_gpus, fallback_error = _collect_gpu_stats_via_nvidia_smi()
        if fallback_gpus:
            logger.warning("pynvml failed (%s); using nvidia-smi fallback", message)
            return fallback_gpus, None
        return [], fallback_error or message


def _coerce_gpu_proxy_stats_payload(payload: Any) -> tuple[List[GPUStatusEnhanced], Optional[str]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Workflow adapter returned invalid GPU stats payload: {payload!r}")

    raw_gpus = payload.get("gpus", [])
    if not isinstance(raw_gpus, list):
        raise RuntimeError(f"Workflow adapter returned invalid GPU list: {raw_gpus!r}")

    gpus: List[GPUStatusEnhanced] = []
    for raw_gpu in raw_gpus:
        gpus.append(GPUStatusEnhanced.model_validate(raw_gpu))

    raw_error = payload.get("gpu_error")
    error = None if raw_error in (None, "") else str(raw_error)
    return gpus, error


def get_gpu_stats_with_error(force_refresh: bool = False) -> tuple[List[GPUStatusEnhanced], Optional[str]]:
    global _gpu_status_cache, _gpu_status_error, _gpu_status_cache_time

    now = time.monotonic()
    if not force_refresh and (now - _gpu_status_cache_time) < _GPU_STATUS_CACHE_TTL_SECONDS:
        return list(_gpu_status_cache), _gpu_status_error

    error: Optional[str] = None
    if _gpu_proxy_enabled():
        try:
            gpus, error = _coerce_gpu_proxy_stats_payload(request_via_workflow_adapter("GET", "/api/gpu/gpus"))
        except Exception as exc:
            proxy_error = str(exc).strip() or exc.__class__.__name__
            logger.warning("GPU proxy stats error: %s", proxy_error)
            gpus, local_error = _collect_gpu_stats()
            error = proxy_error if not local_error else f"{proxy_error}; local fallback failed: {local_error}"
    else:
        gpus, error = _collect_gpu_stats()

    if error and error != _gpu_status_error:
        logger.warning("GPU stats error: %s", error)
    _gpu_status_cache = list(gpus)
    _gpu_status_error = error
    _gpu_status_cache_time = now
    return list(gpus), error


def get_gpu_stats() -> List[GPUStatusEnhanced]:
    return get_gpu_stats_with_error()[0]

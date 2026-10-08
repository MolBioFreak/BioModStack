"""
System monitoring API router - GPU, CPU, RAM statistics.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel
from collections import deque
from pathlib import Path
import base64
import copy
import os
import subprocess
import json
import asyncio
import logging
import time
import re
import shutil
import threading
from urllib import request as urlrequest, error as urlerror
from urllib.parse import quote as urlquote
import psutil

from paths import get_code_root
from services.gpu_config import (
    read_scheduler_config,
    write_scheduler_config,
    GPU_CONFIG_PATH,
    DEFAULT_SCHEDULER_CONFIG,
)
from services.gpu_metadata import HARDWARE_LIMITS
from services.job_control import force_launch_job as force_launch_job_service

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = get_code_root()
GPU_RESERVATIONS_PATH = PROJECT_ROOT / ".gpu_reservations.json"
GPU_POWER_STATE_PATH = PROJECT_ROOT / ".gpu_power_state.json"
GPU_FAN_STATE_PATH = PROJECT_ROOT / ".gpu_fan_state.json"

# Power control state
# - current_limits: currently active per-GPU watt limits
# - saved_limits: profile used when "enabled" mode is toggled on
# - enabled: whether saved profile is considered active
_current_limits = {gpu_idx: int(limits["default"]) for gpu_idx, limits in HARDWARE_LIMITS.items()}
_saved_limits = {gpu_idx: int(limits["eco"]) for gpu_idx, limits in HARDWARE_LIMITS.items()}
_power_enabled = False  # True = using saved limits, False = using stock

# Fan control state
# profiles: desired per-GPU mode/speed by nvidia-smi index (persisted)
# mapping_overrides: optional explicit fan target mapping by nvidia-smi index
_fan_profiles: Dict[int, Dict[str, Any]] = {}
_fan_mapping_overrides: Dict[int, List[int]] = {}

FAN_BACKEND_NVIDIA_SETTINGS = "nvidia-settings"
FAN_BACKEND_COOLERCONTROL = "coolercontrol"

# Historical data for sparkline graphs (max 60 samples = ~2 min at 2s polling)
_cpu_history: deque = deque(maxlen=60)
_ram_history: deque = deque(maxlen=60)
_last_per_core_utilization: List[float] = []
_cpu_percent_primed = False
_cpu_percent_lock = threading.Lock()


# --- Enhanced GPU Schema ---
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


class CPUStatus(BaseModel):
    """CPU status information."""
    name: str
    cores_physical: int
    cores_logical: int
    utilization: float  # Overall %
    per_core_utilization: List[float]
    frequency_current_mhz: float
    frequency_max_mhz: float
    temperature: Optional[float] = None  # Celsius, if available
    power_watts: Optional[float] = None  # Package power via RAPL


# RAPL power tracking (for computing instantaneous power from energy delta)
_rapl_package_sources: Optional[List[Dict[str, Any]]] = None
_rapl_sample_state: Dict[str, Dict[str, float]] = {}
_rapl_state_lock = threading.Lock()

try:
    # Prime psutil's non-blocking CPU delta sampler once at process start so
    # the first telemetry request does not need to block on an interval sleep.
    _last_per_core_utilization = psutil.cpu_percent(interval=None, percpu=True)
    _cpu_percent_primed = True
except Exception:
    _last_per_core_utilization = []
    _cpu_percent_primed = False


class RAMStatus(BaseModel):
    """RAM status information."""
    total_gb: float
    used_gb: float
    available_gb: float
    utilization: float  # percentage
    swap_total_gb: float
    swap_used_gb: float
    swap_percent: float


class SystemStatusResponse(BaseModel):
    """Complete system status response."""
    gpus: List[GPUStatusEnhanced]
    gpu_error: Optional[str] = None
    cpu: CPUStatus
    ram: RAMStatus
    timestamp: datetime
    # Historical data for sparkline graphs (last 60 samples)
    cpu_history: List[float] = []  # Overall CPU % over time
    ram_history: List[float] = []  # RAM % over time


_gpu_status_cache: List[GPUStatusEnhanced] = []
_gpu_status_error: Optional[str] = None
_gpu_status_cache_time: float = 0.0
_GPU_STATUS_CACHE_TTL_SECONDS = 1.25

_power_control_cache: Optional[Dict[str, Any]] = None
_power_control_cache_time: float = 0.0
_POWER_CONTROL_CACHE_TTL_SECONDS = 2.0

_fan_control_cache: Optional[Dict[str, Any]] = None
_fan_control_cache_time: float = 0.0
_FAN_CONTROL_CACHE_TTL_SECONDS = 5.0

_coolercontrol_cookie_cache: Optional[str] = None
_coolercontrol_cookie_cache_time: float = 0.0
_COOLERCONTROL_COOKIE_CACHE_TTL_SECONDS = 20.0

_coolercontrol_devices_cache: List[Dict[str, Any]] = []
_coolercontrol_devices_error: str = ""
_coolercontrol_devices_cache_time: float = 0.0
_COOLERCONTROL_DEVICES_CACHE_TTL_SECONDS = 15.0

_coolercontrol_modes_cache: List[str] = []
_coolercontrol_modes_error: str = ""
_coolercontrol_modes_cache_time: float = 0.0
_COOLERCONTROL_MODES_CACHE_TTL_SECONDS = 30.0


def _clamp_power_limit(gpu_index: int, watts: int) -> int:
    hw = HARDWARE_LIMITS[gpu_index]
    return max(int(hw["min"]), min(int(hw["max"]), int(watts)))


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _derive_power_enabled_from_current() -> bool:
    for gpu_idx, hw in HARDWARE_LIMITS.items():
        current = _current_limits.get(gpu_idx, int(hw["default"]))
        if int(current) != int(hw["default"]):
            return True
    return False


def _load_power_state() -> None:
    global _current_limits, _saved_limits, _power_enabled

    if not GPU_POWER_STATE_PATH.exists():
        return

    try:
        raw = json.loads(GPU_POWER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[GPU] Failed reading power state file %s: %s", GPU_POWER_STATE_PATH, exc)
        return

    loaded_current = raw.get("current_limits", {}) if isinstance(raw, dict) else {}
    loaded_saved = raw.get("saved_limits", {}) if isinstance(raw, dict) else {}
    loaded_enabled = raw.get("enabled", False) if isinstance(raw, dict) else False

    for gpu_idx, hw in HARDWARE_LIMITS.items():
        default_limit = int(hw["default"])

        current_raw = loaded_current.get(str(gpu_idx), loaded_current.get(gpu_idx, default_limit))
        saved_raw = loaded_saved.get(str(gpu_idx), loaded_saved.get(gpu_idx, int(hw["eco"])))

        _current_limits[gpu_idx] = _clamp_power_limit(gpu_idx, _safe_int(current_raw, default_limit))
        _saved_limits[gpu_idx] = _clamp_power_limit(gpu_idx, _safe_int(saved_raw, int(hw["eco"])))

    _power_enabled = bool(loaded_enabled)


def _save_power_state() -> None:
    payload = {
        "current_limits": {str(k): int(v) for k, v in _current_limits.items()},
        "saved_limits": {str(k): int(v) for k, v in _saved_limits.items()},
        "enabled": bool(_power_enabled),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }

    try:
        GPU_POWER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = GPU_POWER_STATE_PATH.with_name(f".{GPU_POWER_STATE_PATH.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, GPU_POWER_STATE_PATH)
    except Exception as exc:
        logger.warning("[GPU] Failed writing power state file %s: %s", GPU_POWER_STATE_PATH, exc)


def _read_live_power_limits() -> Dict[int, int]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,power.limit", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return {}

    if result.returncode != 0:
        return {}

    live: Dict[int, int] = {}
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
            watts = int(round(float(parts[1])))
        except (TypeError, ValueError):
            continue
        if idx in HARDWARE_LIMITS:
            live[idx] = _clamp_power_limit(idx, watts)
    return live


def _refresh_power_state_from_hardware() -> None:
    global _power_enabled

    live_limits = _read_live_power_limits()
    if not live_limits:
        return

    for gpu_idx, watts in live_limits.items():
        _current_limits[gpu_idx] = watts
    _power_enabled = _derive_power_enabled_from_current()


def _invalidate_power_control_cache() -> None:
    global _power_control_cache, _power_control_cache_time
    _power_control_cache = None
    _power_control_cache_time = 0.0


def _power_control_payload() -> Dict[str, Any]:
    summary = _power_summary()
    return {
        "limits": _current_limits,
        "saved_limits": _saved_limits,
        "enabled": _power_enabled,
        "eco_mode": summary["any_below_default"],
        "power_percentage": summary["power_percentage"],
        "total_current_watts": summary["total_current_watts"],
        "total_max_watts": summary["total_max_watts"],
        "total_default_watts": summary["total_default_watts"],
        "per_gpu": summary["per_gpu"],
        "hardware_limits": HARDWARE_LIMITS,
    }


def _get_power_control_payload(force_refresh: bool = False) -> Dict[str, Any]:
    global _power_control_cache, _power_control_cache_time

    now = time.monotonic()
    if (
        not force_refresh
        and _power_control_cache is not None
        and (now - _power_control_cache_time) < _POWER_CONTROL_CACHE_TTL_SECONDS
    ):
        return copy.deepcopy(_power_control_cache)

    _refresh_power_state_from_hardware()
    payload = _power_control_payload()
    _power_control_cache = copy.deepcopy(payload)
    _power_control_cache_time = now
    return payload


def _power_summary() -> Dict[str, Any]:
    total_min = sum(int(limits["min"]) for limits in HARDWARE_LIMITS.values())
    total_max = sum(int(limits["max"]) for limits in HARDWARE_LIMITS.values())
    total_current = sum(
        int(_current_limits.get(idx, limits["default"]))
        for idx, limits in HARDWARE_LIMITS.items()
    )
    total_default = sum(int(limits["default"]) for limits in HARDWARE_LIMITS.values())

    power_range = max(1, total_max - total_min)
    power_percentage = round(((total_current - total_min) / power_range) * 100)
    any_below_default = any(
        int(_current_limits.get(idx, limits["default"])) < int(limits["default"])
        for idx, limits in HARDWARE_LIMITS.items()
    )

    per_gpu = {}
    for idx, limits in HARDWARE_LIMITS.items():
        min_w = int(limits["min"])
        max_w = int(limits["max"])
        current_w = int(_current_limits.get(idx, limits["default"]))
        range_w = max(1, max_w - min_w)
        per_gpu[str(idx)] = {
            "current_watts": current_w,
            "saved_watts": int(_saved_limits.get(idx, limits["eco"])),
            "min_watts": min_w,
            "default_watts": int(limits["default"]),
            "max_watts": max_w,
            "eco_watts": int(limits["eco"]),
            "percentage": round(((current_w - min_w) / range_w) * 100),
            "name": limits.get("name", f"GPU {idx}"),
        }

    return {
        "any_below_default": any_below_default,
        "power_percentage": power_percentage,
        "total_current_watts": total_current,
        "total_max_watts": total_max,
        "total_default_watts": total_default,
        "per_gpu": per_gpu,
    }


def _clamp_fan_percent(value: int, min_percent: int = 30, max_percent: int = 100) -> int:
    return max(int(min_percent), min(int(max_percent), int(value)))


def _fan_control_backend() -> str:
    raw = str(os.getenv("BMS_FAN_CONTROL_BACKEND", FAN_BACKEND_NVIDIA_SETTINGS)).strip().lower()
    if raw in {"nvidia", "nvidia-settings", "nvidia_settings"}:
        return FAN_BACKEND_NVIDIA_SETTINGS
    if raw in {"coolercontrol", "cctv"}:
        return FAN_BACKEND_COOLERCONTROL
    return FAN_BACKEND_NVIDIA_SETTINGS


def _fan_control_display() -> str:
    return os.getenv("BMS_NVIDIA_SETTINGS_DISPLAY", os.getenv("DISPLAY", ":0")) or ":0"


def _fan_control_xauthority() -> str:
    candidates = [
        os.getenv("BMS_NVIDIA_SETTINGS_XAUTHORITY"),
        os.getenv("XAUTHORITY"),
        "/run/user/1000/gdm/Xauthority",
        str(Path.home() / ".Xauthority"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
    return "/run/user/1000/gdm/Xauthority"


def _run_nvidia_settings_query(args: List[str], timeout: int = 10) -> tuple[bool, str]:
    display = _fan_control_display()
    xauth = _fan_control_xauthority()
    base_env = os.environ.copy()
    base_env["DISPLAY"] = display
    if xauth:
        base_env["XAUTHORITY"] = xauth

    attempts: List[tuple[List[str], Optional[Dict[str, str]]]] = [
        (["nvidia-settings", "-c", display, *args], base_env),
        (
            [
                "sudo",
                "-n",
                "env",
                f"DISPLAY={display}",
                f"XAUTHORITY={xauth}",
                "nvidia-settings",
                "-c",
                display,
                *args,
            ],
            None,
        ),
    ]

    last_output = ""
    for cmd, env in attempts:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout)),
                env=env,
            )
        except Exception as exc:
            last_output = str(exc)
            continue
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return True, output.strip()
        last_output = output.strip()
    return False, last_output


def _run_nvidia_settings_assign(args: List[str], timeout: int = 10) -> tuple[bool, str]:
    """
    Run writable nvidia-settings command.

    Tries direct call first, then sudo -n with DISPLAY/XAUTHORITY passthrough.
    """
    display = _fan_control_display()
    xauth = _fan_control_xauthority()
    base_env = os.environ.copy()
    base_env["DISPLAY"] = display
    if xauth:
        base_env["XAUTHORITY"] = xauth
    attempts = [
        ["nvidia-settings", "-c", display, *args],
        [
            "sudo",
            "-n",
            "env",
            f"DISPLAY={display}",
            f"XAUTHORITY={xauth}",
            "nvidia-settings",
            "-c",
            display,
            *args,
        ],
    ]
    last_output = ""
    for idx, cmd in enumerate(attempts):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout)),
                env=base_env if idx == 0 else None,
            )
        except Exception as exc:
            last_output = str(exc)
            continue
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return True, output.strip()
        last_output = output.strip()
    return False, last_output


def _resolve_cctv_binary_candidates() -> List[str]:
    env_override = os.getenv("BMS_COOLERCONTROL_CLI")
    candidates: List[Optional[str]] = [
        env_override,
        shutil.which("cctv"),
        str(Path.home() / ".cargo/bin/cctv"),
        "/usr/local/bin/cctv",
        "/usr/bin/cctv",
    ]
    out: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            resolved = str(path)
            if resolved not in seen:
                out.append(resolved)
                seen.add(resolved)
    return out


def _cctv_config_path() -> Path:
    explicit = os.getenv("BMS_COOLERCONTROL_CCTV_CONFIG")
    if explicit:
        return Path(explicit)
    return PROJECT_ROOT / ".cctv.generated.json"


def _ensure_cctv_config() -> Path:
    cfg_path = _cctv_config_path()
    daemon_address = str(os.getenv("BMS_COOLERCONTROL_DAEMON_ADDRESS", "127.0.0.1")).strip() or "127.0.0.1"
    username = str(os.getenv("BMS_COOLERCONTROL_USERNAME", "CCAdmin")).strip() or "CCAdmin"
    try:
        daemon_port = int(str(os.getenv("BMS_COOLERCONTROL_DAEMON_PORT", "11987")).strip())
    except (TypeError, ValueError):
        daemon_port = 11987
    daemon_port = max(1, min(65535, daemon_port))

    payload = {
        "daemon_address": daemon_address,
        "port": daemon_port,
        "time_range_s": 60,
        "username": username,
        "skip_splash": True,
        "tasks": [],
    }

    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        existing: Optional[Dict[str, Any]] = None
        if cfg_path.exists():
            try:
                existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                existing = None
        if existing != payload:
            cfg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        # Best-effort only; cctv call below will return a descriptive error if this fails.
        pass

    return cfg_path


def _coolercontrol_daemon_address() -> str:
    return str(os.getenv("BMS_COOLERCONTROL_DAEMON_ADDRESS", "127.0.0.1")).strip() or "127.0.0.1"


def _coolercontrol_daemon_port() -> int:
    try:
        daemon_port = int(str(os.getenv("BMS_COOLERCONTROL_DAEMON_PORT", "11987")).strip())
    except (TypeError, ValueError):
        daemon_port = 11987
    return max(1, min(65535, daemon_port))


def _coolercontrol_daemon_base_url() -> str:
    return f"http://{_coolercontrol_daemon_address()}:{_coolercontrol_daemon_port()}"


def _coolercontrol_credentials() -> tuple[str, str]:
    username = str(os.getenv("BMS_COOLERCONTROL_USERNAME", "CCAdmin")).strip() or "CCAdmin"
    password = str(os.getenv("BMS_COOLERCONTROL_PASSWORD", "coolAdmin")).strip() or "coolAdmin"
    return username, password


def _parse_json_text(raw: str) -> Optional[Any]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _coolercontrol_request(
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
) -> tuple[bool, int, Optional[Any], str, Dict[str, str]]:
    url = f"{_coolercontrol_daemon_base_url()}{path}"
    payload: Optional[bytes] = None
    request_headers: Dict[str, str] = {}
    if headers:
        request_headers.update(headers)
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(url=url, data=payload, method=str(method).upper())
    for k, v in request_headers.items():
        req.add_header(str(k), str(v))
    try:
        with urlrequest.urlopen(req, timeout=max(1, int(timeout))) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = _parse_json_text(raw)
            out_headers = {k: v for k, v in resp.headers.items()}
            return 200 <= status < 300, status, parsed, raw, out_headers
    except urlerror.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = str(exc)
        parsed = _parse_json_text(raw)
        headers_obj = getattr(exc, "headers", None)
        out_headers = {k: v for k, v in headers_obj.items()} if headers_obj is not None else {}
        return False, int(getattr(exc, "code", 0) or 0), parsed, raw, out_headers
    except Exception as exc:
        return False, 0, None, str(exc), {}


def _clear_coolercontrol_cookie_cache() -> None:
    global _coolercontrol_cookie_cache, _coolercontrol_cookie_cache_time
    _coolercontrol_cookie_cache = None
    _coolercontrol_cookie_cache_time = 0.0


def _coolercontrol_login_cookie(force_refresh: bool = False) -> tuple[Optional[str], str]:
    global _coolercontrol_cookie_cache, _coolercontrol_cookie_cache_time

    now = time.monotonic()
    if (
        not force_refresh
        and _coolercontrol_cookie_cache
        and (now - _coolercontrol_cookie_cache_time) < _COOLERCONTROL_COOKIE_CACHE_TTL_SECONDS
    ):
        return _coolercontrol_cookie_cache, ""

    username, password = _coolercontrol_credentials()
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    ok, status, _payload, raw, headers = _coolercontrol_request(
        method="POST",
        path="/login",
        headers={"Authorization": f"Basic {auth}"},
        timeout=3,
    )
    if not ok:
        _clear_coolercontrol_cookie_cache()
        return None, f"CoolerControl login failed ({status}): {raw.strip() or 'unknown error'}"
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    match = re.search(r"\bcc=[^;,\s]+", set_cookie)
    if not match:
        _clear_coolercontrol_cookie_cache()
        return None, "CoolerControl login did not return a session cookie"
    _coolercontrol_cookie_cache = match.group(0)
    _coolercontrol_cookie_cache_time = now
    return _coolercontrol_cookie_cache, ""


def _normalize_pci_bus_id(raw_bus_id: Optional[str]) -> Optional[str]:
    value = str(raw_bus_id or "").strip()
    if not value:
        return None
    match = re.match(r"^([0-9A-Fa-f]{1,8}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2})\.([0-7])$", value)
    if not match:
        return value.upper()
    return (
        f"{int(match.group(1), 16):08X}:"
        f"{int(match.group(2), 16):02X}:"
        f"{int(match.group(3), 16):02X}."
        f"{match.group(4)}"
    )


def _coolercontrol_device_fan_channels(device: Dict[str, Any]) -> List[str]:
    channels_obj = device.get("info", {}).get("channels", {}) if isinstance(device, dict) else {}
    if not isinstance(channels_obj, dict):
        return []
    fan_channels: List[str] = []
    for channel_name, channel_meta in channels_obj.items():
        if not isinstance(channel_meta, dict):
            continue
        speed_options = channel_meta.get("speed_options")
        if not isinstance(speed_options, dict):
            continue
        if bool(speed_options.get("fixed_enabled", False)):
            fan_channels.append(str(channel_name))
    return sorted(set(fan_channels))


def _coolercontrol_device_pci_location(device: Dict[str, Any]) -> Optional[str]:
    locations = (
        device.get("info", {})
        .get("driver_info", {})
        .get("locations", [])
        if isinstance(device, dict)
        else []
    )
    if not isinstance(locations, list):
        return None
    for raw_location in locations:
        location = _normalize_pci_bus_id(str(raw_location))
        if location and re.match(r"^[0-9A-F]{8}:[0-9A-F]{2}:[0-9A-F]{2}\.[0-7]$", location):
            return location
    return None


def _coolercontrol_devices(force_refresh: bool = False) -> tuple[List[Dict[str, Any]], str]:
    global _coolercontrol_devices_cache, _coolercontrol_devices_error, _coolercontrol_devices_cache_time

    now = time.monotonic()
    if (
        not force_refresh
        and (now - _coolercontrol_devices_cache_time) < _COOLERCONTROL_DEVICES_CACHE_TTL_SECONDS
    ):
        return copy.deepcopy(_coolercontrol_devices_cache), _coolercontrol_devices_error

    ok, status, payload, raw, _headers = _coolercontrol_request("GET", "/devices", timeout=3)
    if not ok and status in {401, 403}:
        cookie, cookie_error = _coolercontrol_login_cookie(force_refresh=True)
        if cookie:
            ok, status, payload, raw, _headers = _coolercontrol_request(
                "GET",
                "/devices",
                headers={"Cookie": cookie},
                timeout=3,
            )
        elif cookie_error:
            raw = cookie_error
            status = 401
    if not ok:
        _coolercontrol_devices_cache = []
        _coolercontrol_devices_error = f"CoolerControl /devices failed ({status}): {raw.strip() or 'unknown error'}"
        _coolercontrol_devices_cache_time = now
        return [], _coolercontrol_devices_error
    devices: Any = None
    if isinstance(payload, dict):
        devices = payload.get("devices")
    elif isinstance(payload, list):
        devices = payload
    if not isinstance(devices, list):
        _coolercontrol_devices_cache = []
        _coolercontrol_devices_error = "CoolerControl /devices payload missing devices list"
        _coolercontrol_devices_cache_time = now
        return [], _coolercontrol_devices_error
    _coolercontrol_devices_cache = [d for d in devices if isinstance(d, dict)]
    _coolercontrol_devices_error = ""
    _coolercontrol_devices_cache_time = now
    return copy.deepcopy(_coolercontrol_devices_cache), ""


def _coolercontrol_settings_map(device_uid: str, cookie: str) -> tuple[Dict[str, Dict[str, Any]], str]:
    uid = urlquote(str(device_uid), safe="")
    ok, status, payload, raw, _headers = _coolercontrol_request(
        "GET",
        f"/devices/{uid}/settings",
        headers={"Cookie": cookie},
        timeout=3,
    )
    if not ok:
        if status in {401, 403}:
            _clear_coolercontrol_cookie_cache()
        return {}, f"settings read failed ({status}): {raw.strip() or 'unknown error'}"
    if not isinstance(payload, dict):
        return {}, "settings payload malformed"
    settings = payload.get("settings", [])
    if not isinstance(settings, list):
        return {}, "settings payload missing list"
    out: Dict[str, Dict[str, Any]] = {}
    for setting in settings:
        if not isinstance(setting, dict):
            continue
        channel_name = str(setting.get("channel_name", "")).strip()
        if not channel_name:
            continue
        out[channel_name] = setting
    return out, ""


def _coolercontrol_channel_status_map(device_uid: str, cookie: Optional[str] = None) -> tuple[Dict[str, Dict[str, Any]], str]:
    uid = urlquote(str(device_uid), safe="")
    headers = {"Cookie": cookie} if cookie else None
    ok, status, payload, raw, _headers = _coolercontrol_request("GET", f"/status/{uid}", headers=headers, timeout=3)
    if not ok and status in {401, 403} and not cookie:
        refreshed_cookie, cookie_error = _coolercontrol_login_cookie(force_refresh=True)
        if refreshed_cookie:
            ok, status, payload, raw, _headers = _coolercontrol_request(
                "GET",
                f"/status/{uid}",
                headers={"Cookie": refreshed_cookie},
                timeout=3,
            )
        elif cookie_error:
            raw = cookie_error
            status = 401
    if not ok:
        return {}, f"status read failed ({status}): {raw.strip() or 'unknown error'}"
    if not isinstance(payload, dict):
        return {}, "status payload malformed"
    history = payload.get("status_history")
    if not isinstance(history, list) or not history:
        return {}, "status history unavailable"
    latest = history[0] if isinstance(history[0], dict) else {}
    channels = latest.get("channels", []) if isinstance(latest, dict) else []
    if not isinstance(channels, list):
        return {}, "status channels unavailable"
    out: Dict[str, Dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        name = str(channel.get("name", "")).strip()
        if not name:
            continue
        out[name] = channel
    return out, ""


def _coolercontrol_apply_manual(device_uid: str, channel_name: str, target_percent: int, cookie: str) -> tuple[bool, str]:
    uid = urlquote(str(device_uid), safe="")
    channel = urlquote(str(channel_name), safe="")
    ok, status, _payload, raw, _headers = _coolercontrol_request(
        "PUT",
        f"/devices/{uid}/settings/{channel}/manual",
        json_body={"speed_fixed": int(max(0, min(100, int(target_percent))))},
        headers={"Cookie": cookie},
        timeout=8,
    )
    if ok:
        return True, ""
    if status in {401, 403}:
        _clear_coolercontrol_cookie_cache()
    return False, f"{channel_name}: manual apply failed ({status}): {raw.strip() or 'unknown error'}"


def _coolercontrol_apply_reset(device_uid: str, channel_name: str, cookie: str) -> tuple[bool, str]:
    uid = urlquote(str(device_uid), safe="")
    channel = urlquote(str(channel_name), safe="")
    ok, status, _payload, raw, _headers = _coolercontrol_request(
        "PUT",
        f"/devices/{uid}/settings/{channel}/reset",
        headers={"Cookie": cookie},
        timeout=8,
    )
    if ok:
        return True, ""
    if status in {401, 403}:
        _clear_coolercontrol_cookie_cache()
    return False, f"{channel_name}: reset failed ({status}): {raw.strip() or 'unknown error'}"


def _run_cctv(args: List[str], timeout: int = 10) -> tuple[bool, str]:
    candidates = _resolve_cctv_binary_candidates()
    if not candidates:
        return False, "cctv binary not found (set BMS_COOLERCONTROL_CLI or install cctv)"
    cctv_cfg_path = _ensure_cctv_config()
    cctv_password = str(os.getenv("BMS_COOLERCONTROL_PASSWORD", "coolAdmin")).strip() or "coolAdmin"
    errors: List[str] = []
    for cctv_bin in candidates:
        cmd = [cctv_bin, *args]
        cmd_env = os.environ.copy()
        cmd_env.setdefault("CCTV_CONFIG_FILEPATH", str(cctv_cfg_path))
        cmd_env.setdefault("CCTV_DAEMON_PASSWORD", cctv_password)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout)),
                env=cmd_env,
            )
        except Exception as exc:
            errors.append(f"{cctv_bin}: {exc}")
            continue
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 0:
            return True, output
        errors.append(f"{cctv_bin}: {output or f'exit {result.returncode}'}")
    return False, " | ".join(errors)


def _coolercontrol_extract_mode_names(payload: Any) -> List[str]:
    raw_modes: Any = None
    if isinstance(payload, list):
        raw_modes = payload
    elif isinstance(payload, dict):
        for key in ("modes", "items", "data"):
            if isinstance(payload.get(key), list):
                raw_modes = payload.get(key)
                break
        if raw_modes is None:
            raw_modes = [payload]

    if not isinstance(raw_modes, list):
        return []

    names: List[str] = []
    for item in raw_modes:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = (
                item.get("name")
                or item.get("mode_name")
                or item.get("title")
                or item.get("uid")
                or ""
            )
            value = str(value).strip()
        else:
            value = ""
        if value:
            names.append(value)

    deduped: List[str] = []
    seen: set[str] = set()
    for mode in names:
        key = mode.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mode)
    return deduped


def _coolercontrol_list_modes(force_refresh: bool = False) -> tuple[List[str], str]:
    global _coolercontrol_modes_cache, _coolercontrol_modes_error, _coolercontrol_modes_cache_time

    now = time.monotonic()
    if (
        not force_refresh
        and (now - _coolercontrol_modes_cache_time) < _COOLERCONTROL_MODES_CACHE_TTL_SECONDS
    ):
        return list(_coolercontrol_modes_cache), _coolercontrol_modes_error

    ok, status, payload, raw, _headers = _coolercontrol_request("GET", "/modes", timeout=3)
    if not ok and status in {401, 403}:
        cookie, cookie_error = _coolercontrol_login_cookie(force_refresh=True)
        if cookie:
            ok, status, payload, raw, _headers = _coolercontrol_request(
                "GET",
                "/modes",
                headers={"Cookie": cookie},
                timeout=3,
            )
        elif cookie_error:
            raw = cookie_error
            status = 401

    if not ok:
        _coolercontrol_modes_cache = []
        _coolercontrol_modes_error = f"CoolerControl /modes failed ({status}): {raw.strip() or 'unknown error'}"
        _coolercontrol_modes_cache_time = now
        return [], _coolercontrol_modes_error

    deduped = _coolercontrol_extract_mode_names(payload)
    if not deduped and payload not in (None, "", []):
        _coolercontrol_modes_cache = []
        _coolercontrol_modes_error = "CoolerControl /modes payload missing mode names"
        _coolercontrol_modes_cache_time = now
        return [], _coolercontrol_modes_error

    _coolercontrol_modes_cache = list(deduped)
    _coolercontrol_modes_error = ""
    _coolercontrol_modes_cache_time = now
    return deduped, ""


def _query_smi_fan_percents() -> Dict[int, int]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,fan.speed", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    out: Dict[int, int] = {}
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
            pct = int(round(float(parts[1])))
        except (TypeError, ValueError):
            continue
        out[idx] = max(0, min(100, pct))
    return out


def _parse_first_int(pattern: str, text: str) -> Optional[int]:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _load_fan_state() -> None:
    global _fan_profiles, _fan_mapping_overrides

    if not GPU_FAN_STATE_PATH.exists():
        _fan_profiles = {}
        _fan_mapping_overrides = {}
        return

    try:
        raw = json.loads(GPU_FAN_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[GPU] Failed reading fan state file {GPU_FAN_STATE_PATH}: {exc}")
        _fan_profiles = {}
        _fan_mapping_overrides = {}
        return

    profiles_raw = raw.get("profiles", {}) if isinstance(raw, dict) else {}
    mapping_raw = raw.get("mapping_overrides", {}) if isinstance(raw, dict) else {}

    profiles: Dict[int, Dict[str, Any]] = {}
    if isinstance(profiles_raw, dict):
        for k, v in profiles_raw.items():
            try:
                gpu_idx = int(k)
            except (TypeError, ValueError):
                continue
            if gpu_idx not in HARDWARE_LIMITS or not isinstance(v, dict):
                continue
            mode = str(v.get("mode", "auto")).strip().lower()
            if mode not in {"auto", "manual"}:
                mode = "auto"
            target = _clamp_fan_percent(_safe_int(v.get("target_percent"), 35))
            profiles[gpu_idx] = {
                "mode": mode,
                "target_percent": target,
            }

    mapping_overrides: Dict[int, List[int]] = {}
    if isinstance(mapping_raw, dict):
        for k, v in mapping_raw.items():
            try:
                gpu_idx = int(k)
            except (TypeError, ValueError):
                continue
            if gpu_idx not in HARDWARE_LIMITS or not isinstance(v, list):
                continue
            fan_targets: List[int] = []
            for fan in v:
                try:
                    fan_targets.append(int(fan))
                except (TypeError, ValueError):
                    continue
            mapping_overrides[gpu_idx] = sorted(set(fan_targets))

    _fan_profiles = profiles
    _fan_mapping_overrides = mapping_overrides


def _save_fan_state() -> None:
    payload = {
        "profiles": {str(k): v for k, v in _fan_profiles.items()},
        "mapping_overrides": {str(k): v for k, v in _fan_mapping_overrides.items()},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        GPU_FAN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = GPU_FAN_STATE_PATH.with_name(f".{GPU_FAN_STATE_PATH.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, GPU_FAN_STATE_PATH)
    except Exception as exc:
        print(f"[GPU] Failed writing fan state file {GPU_FAN_STATE_PATH}: {exc}")


def _query_smi_gpu_map() -> Dict[int, Dict[str, Any]]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        if idx not in HARDWARE_LIMITS:
            continue
        out[idx] = {
            "uuid": parts[1],
            "name": ", ".join(parts[2:-1]).strip(),
            "pci_bus_id": _normalize_pci_bus_id(parts[-1]),
        }
    return out


def _extract_target_ids(output: str, target_type: str) -> List[int]:
    pattern = rf"\[{re.escape(target_type)}:(\d+)\]"
    return sorted({int(m.group(1)) for m in re.finditer(pattern, output)})


def _query_gpu_fan_targets(settings_gpu_target: int) -> List[int]:
    """Return fan target IDs attached to a specific nvidia-settings GPU target."""
    ok, output = _run_nvidia_settings_query(["-q", f"[gpu:{settings_gpu_target}]/fans"])
    if not ok:
        return []
    return _extract_target_ids(output, "fan")


def _query_all_fan_targets() -> List[int]:
    ok, output = _run_nvidia_settings_query(["-q", "fans"])
    if not ok:
        return []
    return _extract_target_ids(output, "fan")


def _query_settings_gpu_uuid(settings_gpu_target: int) -> Optional[str]:
    ok, output = _run_nvidia_settings_query(["-q", f"[gpu:{settings_gpu_target}]/GPUUUID"])
    if not ok:
        return None
    match = re.search(r"\bGPU-[0-9a-fA-F-]{36}\b", output)
    if not match:
        return None
    return match.group(0)


def _query_settings_targets() -> tuple[Dict[int, Dict[str, Any]], str]:
    ok, output = _run_nvidia_settings_query(["-q", "gpus"])
    if not ok:
        return {}, output

    gpu_targets: Dict[int, Dict[str, Any]] = {}
    settings_targets = _extract_target_ids(output, "gpu")
    for settings_target in settings_targets:
        gpu_targets[settings_target] = {
            "settings_gpu_target": settings_target,
            "name": f"settings-gpu:{settings_target}",
            "uuid": _query_settings_gpu_uuid(settings_target),
            "fan_targets": _query_gpu_fan_targets(settings_target),
        }

    # Fallback fan assignment if per-GPU fan query doesn't report targets.
    all_fans = _query_all_fan_targets()
    if all_fans:
        all_empty = all(len(meta.get("fan_targets", [])) == 0 for meta in gpu_targets.values())
        if all_empty:
            remaining_fans = sorted(set(all_fans))
            # Most common case: fan target index equals settings GPU index.
            for settings_target in settings_targets:
                if settings_target in remaining_fans:
                    gpu_targets[settings_target]["fan_targets"] = [settings_target]
                    remaining_fans.remove(settings_target)
            # Fallback distribution by stable ordering when IDs don't line up.
            unresolved_targets = [t for t in settings_targets if not gpu_targets[t]["fan_targets"]]
            if unresolved_targets and remaining_fans:
                if len(remaining_fans) % len(unresolved_targets) == 0:
                    per_gpu = len(remaining_fans) // len(unresolved_targets)
                    for idx, settings_target in enumerate(unresolved_targets):
                        start = idx * per_gpu
                        gpu_targets[settings_target]["fan_targets"] = remaining_fans[start:start + per_gpu]
                else:
                    for idx, settings_target in enumerate(unresolved_targets):
                        if idx < len(remaining_fans):
                            gpu_targets[settings_target]["fan_targets"] = [remaining_fans[idx]]

    return gpu_targets, ""


def _query_gpu_fan_mode(settings_gpu_target: int) -> Optional[str]:
    ok, output = _run_nvidia_settings_query(["-q", f"[gpu:{settings_gpu_target}]/GPUFanControlState"])
    if not ok:
        return None
    value = _parse_first_int(r"GPUFanControlState'.*:\s*(-?\d+)\.", output)
    if value is None:
        return None
    return "manual" if value == 1 else "auto"


def _query_fan_target_stats(fan_target: int) -> Dict[str, Any]:
    ok, output = _run_nvidia_settings_query(
        [
            "-q", f"[fan:{fan_target}]/GPUTargetFanSpeed",
            "-q", f"[fan:{fan_target}]/GPUCurrentFanSpeed",
            "-q", f"[fan:{fan_target}]/GPUCurrentFanSpeedRPM",
        ]
    )
    if not ok:
        return {
            "fan_target": fan_target,
            "target_percent": None,
            "current_percent": None,
            "current_rpm": None,
            "min_percent": 30,
            "max_percent": 100,
            "error": output,
        }

    target_percent = _parse_first_int(r"GPUTargetFanSpeed'.*:\s*(-?\d+)\.", output)
    current_percent = _parse_first_int(r"GPUCurrentFanSpeed'.*:\s*(-?\d+)\.", output)
    current_rpm = _parse_first_int(r"GPUCurrentFanSpeedRPM'.*:\s*(-?\d+)\.", output)
    min_bound = _parse_first_int(r"range\s+(-?\d+)\s*-\s*-?\d+", output) or 30
    max_bound = _parse_first_int(r"range\s+-?\d+\s*-\s*(-?\d+)", output) or 100

    return {
        "fan_target": fan_target,
        "target_percent": target_percent,
        "current_percent": current_percent,
        "current_rpm": current_rpm,
        "min_percent": min_bound,
        "max_percent": max_bound,
        "error": None,
    }


def _resolve_fan_mapping(
    smi_map: Dict[int, Dict[str, Any]],
    settings_gpu_targets: Dict[int, Dict[str, Any]],
) -> tuple[Dict[int, Dict[str, Any]], str]:
    """
    Resolve nvidia-smi GPU index -> nvidia-settings target + fan target list.

    Mapping source priority:
    1) persisted explicit mapping override
    2) UUID GPU mapping + direct per-GPU fan target discovery
    3) index-order fallback (only when UUID mapping is incomplete)
    """
    resolved: Dict[int, Dict[str, Any]] = {}
    mapping_sources: set[str] = set()

    # Build UUID map from nvidia-settings target to nvidia-smi GPU index.
    uuid_to_smi: Dict[str, int] = {}
    for gpu_idx, meta in smi_map.items():
        uuid = str(meta.get("uuid", "")).strip()
        if uuid:
            uuid_to_smi[uuid.lower()] = gpu_idx

    settings_target_to_smi: Dict[int, int] = {}
    for settings_target, meta in settings_gpu_targets.items():
        uuid = str(meta.get("uuid", "")).strip().lower()
        if uuid and uuid in uuid_to_smi:
            smi_idx = uuid_to_smi[uuid]
            settings_target_to_smi[settings_target] = smi_idx
            resolved[smi_idx] = {
                "settings_gpu_target": settings_target,
                "fan_targets": sorted(set(int(f) for f in meta.get("fan_targets", []) if isinstance(f, int))),
                "mapping_source": "uuid_direct",
            }
            mapping_sources.add("uuid_direct")

    # Fast-path fallback: if target indices overlap smi indices, use direct index mapping.
    for settings_target, meta in settings_gpu_targets.items():
        if settings_target not in smi_map:
            continue
        if settings_target in resolved:
            continue
        resolved[settings_target] = {
            "settings_gpu_target": settings_target,
            "fan_targets": sorted(set(int(f) for f in meta.get("fan_targets", []) if isinstance(f, int))),
            "mapping_source": "target_index_direct",
        }
        settings_target_to_smi[settings_target] = settings_target
        mapping_sources.add("target_index_direct")

    # If UUID matching misses some GPUs, fall back to stable index ordering for remaining entries.
    mapped_smi = set(resolved.keys())
    mapped_settings = {int(v["settings_gpu_target"]) for v in resolved.values() if v.get("settings_gpu_target") is not None}
    smi_remaining = sorted([idx for idx in smi_map.keys() if idx not in mapped_smi])
    settings_remaining = sorted([idx for idx in settings_gpu_targets.keys() if idx not in mapped_settings])
    if smi_remaining and settings_remaining and len(smi_remaining) == len(settings_remaining):
        for smi_idx, settings_target in zip(smi_remaining, settings_remaining):
            if smi_idx in resolved:
                continue
            meta = settings_gpu_targets.get(settings_target, {})
            resolved[smi_idx] = {
                "settings_gpu_target": settings_target,
                "fan_targets": sorted(set(int(f) for f in meta.get("fan_targets", []) if isinstance(f, int))),
                "mapping_source": "index_fallback",
            }
        mapping_sources.add("index_fallback")

    mapping_source = "none"
    if mapping_sources:
        mapping_source = next(iter(mapping_sources)) if len(mapping_sources) == 1 else "mixed"

    # Apply explicit override fan targets (if present) on top.
    for gpu_idx, fan_list in _fan_mapping_overrides.items():
        target = resolved.get(gpu_idx, {})
        if not target:
            # Try to attach matching settings target from uuid map.
            possible_target = next(
                (
                    t for t, s in settings_target_to_smi.items()
                    if s == gpu_idx
                ),
                None,
            )
            target = {
                "settings_gpu_target": possible_target,
                "fan_targets": [],
                "mapping_source": "override_only",
            }
        target["fan_targets"] = sorted(set(int(f) for f in fan_list))
        target["mapping_source"] = "override"
        resolved[gpu_idx] = target

    return resolved, mapping_source


def _fan_control_snapshot_nvidia_settings() -> Dict[str, Any]:
    smi_map = _query_smi_gpu_map()
    if not smi_map:
        return {
            "supported": False,
            "backend": FAN_BACKEND_NVIDIA_SETTINGS,
            "display": _fan_control_display(),
            "xauthority": _fan_control_xauthority(),
            "message": "nvidia-smi unavailable; cannot build fan control map",
            "mapping_source": "none",
            "gpus": {},
        }

    settings_gpu_targets, settings_error = _query_settings_targets()
    if not settings_gpu_targets:
        return {
            "supported": False,
            "backend": FAN_BACKEND_NVIDIA_SETTINGS,
            "display": _fan_control_display(),
            "xauthority": _fan_control_xauthority(),
            "message": f"nvidia-settings unavailable: {settings_error or 'no GPU targets reported'}",
            "mapping_source": "none",
            "gpus": {},
        }

    resolved_map, mapping_source = _resolve_fan_mapping(smi_map, settings_gpu_targets)
    gpus: Dict[str, Any] = {}
    for gpu_idx in sorted(smi_map.keys()):
        smi_meta = smi_map[gpu_idx]
        resolved = resolved_map.get(gpu_idx, {})
        settings_target = resolved.get("settings_gpu_target")
        mapped_fans = list(resolved.get("fan_targets", []))
        mode = _query_gpu_fan_mode(settings_target) if settings_target is not None else None

        fan_stats = [_query_fan_target_stats(fan_target) for fan_target in mapped_fans]
        valid_target = [s["target_percent"] for s in fan_stats if isinstance(s.get("target_percent"), int)]
        valid_current = [s["current_percent"] for s in fan_stats if isinstance(s.get("current_percent"), int)]
        valid_rpm = [s["current_rpm"] for s in fan_stats if isinstance(s.get("current_rpm"), int)]
        valid_min = [s["min_percent"] for s in fan_stats if isinstance(s.get("min_percent"), int)]
        valid_max = [s["max_percent"] for s in fan_stats if isinstance(s.get("max_percent"), int)]

        profile = _fan_profiles.get(gpu_idx, {"mode": "auto", "target_percent": 35})
        warning: Optional[str] = None
        if settings_target is None:
            warning = "No nvidia-settings GPU target mapping for this GPU"
        elif not mapped_fans:
            warning = "No fan targets mapped; configure override mapping"

        gpus[str(gpu_idx)] = {
            "gpu_index": gpu_idx,
            "gpu_name": smi_meta.get("name", f"GPU {gpu_idx}"),
            "uuid": smi_meta.get("uuid"),
            "settings_gpu_target": settings_target,
            "fan_targets": mapped_fans,
            "fan_count": len(mapped_fans),
            "mode": mode or "unknown",
            "target_percent": round(sum(valid_target) / len(valid_target)) if valid_target else None,
            "current_percent": round(sum(valid_current) / len(valid_current)) if valid_current else None,
            "current_rpm": round(sum(valid_rpm) / len(valid_rpm)) if valid_rpm else None,
            "min_percent": min(valid_min) if valid_min else 30,
            "max_percent": max(valid_max) if valid_max else 100,
            "profile_mode": profile.get("mode", "auto"),
            "profile_target_percent": _clamp_fan_percent(_safe_int(profile.get("target_percent"), 35)),
            "writable": settings_target is not None and len(mapped_fans) > 0,
            "mapping_source": resolved.get("mapping_source") or mapping_source,
            "warning": warning,
            "fan_details": fan_stats,
        }

    return {
        "supported": True,
        "backend": FAN_BACKEND_NVIDIA_SETTINGS,
        "display": _fan_control_display(),
        "xauthority": _fan_control_xauthority(),
        "message": "",
        "mapping_source": mapping_source,
        "gpus": gpus,
    }


def _coolercontrol_gpu_device_map(
    smi_map: Dict[int, Dict[str, Any]],
    devices: List[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    by_bus: Dict[str, Dict[str, Any]] = {}
    for device in devices:
        fan_channels = _coolercontrol_device_fan_channels(device)
        if not fan_channels:
            continue
        pci_location = _coolercontrol_device_pci_location(device)
        if not pci_location:
            continue
        by_bus[pci_location] = {
            "device_uid": str(device.get("uid", "")).strip(),
            "device_name": str(device.get("name", "")).strip(),
            "fan_channels": fan_channels,
            "raw_device": device,
        }

    mapped: Dict[int, Dict[str, Any]] = {}
    for gpu_idx, gpu_meta in smi_map.items():
        bus_id = _normalize_pci_bus_id(gpu_meta.get("pci_bus_id"))
        if not bus_id:
            continue
        device_meta = by_bus.get(bus_id)
        if not device_meta:
            continue
        if not device_meta.get("device_uid"):
            continue
        mapped[gpu_idx] = {
            **device_meta,
            "mapping_source": "pci_bus",
        }
    return mapped


def _fan_control_snapshot_coolercontrol() -> Dict[str, Any]:
    smi_map = _query_smi_gpu_map()
    if not smi_map:
        return {
            "supported": False,
            "backend": FAN_BACKEND_COOLERCONTROL,
            "display": None,
            "xauthority": None,
            "message": "nvidia-smi unavailable; cannot enumerate GPUs for CoolerControl backend",
            "mapping_source": "none",
            "gpus": {},
        }

    devices, devices_error = _coolercontrol_devices()
    device_map = _coolercontrol_gpu_device_map(smi_map, devices) if devices else {}

    cookie, cookie_error = _coolercontrol_login_cookie()
    available_modes, mode_error = _coolercontrol_list_modes()

    smi_fan_percents = _query_smi_fan_percents()
    settings_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
    settings_error_cache: Dict[str, str] = {}
    status_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
    status_error_cache: Dict[str, str] = {}
    gpus: Dict[str, Any] = {}
    for gpu_idx in sorted(smi_map.keys()):
        smi_meta = smi_map[gpu_idx]
        mapped_device = device_map.get(gpu_idx, {})
        device_uid = mapped_device.get("device_uid")
        fan_channels = list(mapped_device.get("fan_channels", []))

        warning_parts: List[str] = []
        settings_map: Dict[str, Dict[str, Any]] = {}
        status_map: Dict[str, Dict[str, Any]] = {}
        mode = "auto"
        target_percent: Optional[int] = None
        current_percent: Optional[int] = smi_fan_percents.get(gpu_idx)
        current_rpm: Optional[int] = None
        min_percent = 0
        max_percent = 100
        writable = bool(device_uid and fan_channels and cookie)

        if not device_uid:
            warning_parts.append("No CoolerControl device mapping for this GPU")
        elif not fan_channels:
            warning_parts.append("No CoolerControl fan channels detected for this GPU")

        if device_uid:
            if cookie:
                if device_uid not in settings_cache:
                    settings_cache[device_uid], settings_error_cache[device_uid] = _coolercontrol_settings_map(device_uid, cookie)
                settings_map = settings_cache.get(device_uid, {})
                if settings_error_cache.get(device_uid):
                    warning_parts.append(f"settings: {settings_error_cache[device_uid]}")
            else:
                warning_parts.append(cookie_error or "CoolerControl login failed")

            if device_uid not in status_cache:
                status_cache[device_uid], status_error_cache[device_uid] = _coolercontrol_channel_status_map(device_uid, cookie)
            status_map = status_cache.get(device_uid, {})
            if status_error_cache.get(device_uid):
                warning_parts.append(f"status: {status_error_cache[device_uid]}")

        configured_targets: List[int] = []
        live_duty_values: List[int] = []
        live_rpm_values: List[int] = []
        min_candidates: List[int] = []
        max_candidates: List[int] = []
        for channel_name in fan_channels:
            settings = settings_map.get(channel_name, {})
            speed_fixed = settings.get("speed_fixed")
            if isinstance(speed_fixed, (int, float)):
                configured_targets.append(_clamp_fan_percent(int(speed_fixed), min_percent=0, max_percent=100))

            channel_status = status_map.get(channel_name, {})
            duty_value = channel_status.get("duty")
            rpm_value = channel_status.get("rpm")
            if isinstance(duty_value, (int, float)):
                live_duty_values.append(_clamp_fan_percent(int(round(float(duty_value))), min_percent=0, max_percent=100))
            if isinstance(rpm_value, (int, float)):
                live_rpm_values.append(max(0, int(round(float(rpm_value)))))

            # Keep min/max constraints from the device metadata when available.
            channel_meta = (
                mapped_device.get("raw_device", {})
                .get("info", {})
                .get("channels", {})
                .get(channel_name, {})
                if isinstance(mapped_device, dict)
                else {}
            )
            speed_options = channel_meta.get("speed_options") if isinstance(channel_meta, dict) else None
            if isinstance(speed_options, dict):
                min_candidates.append(max(0, _safe_int(speed_options.get("min_duty"), 0)))
                max_candidates.append(max(0, _safe_int(speed_options.get("max_duty"), 100)))

        if configured_targets:
            mode = "manual"
            target_percent = round(sum(configured_targets) / len(configured_targets))
        if live_duty_values:
            current_percent = round(sum(live_duty_values) / len(live_duty_values))
        if live_rpm_values:
            current_rpm = round(sum(live_rpm_values) / len(live_rpm_values))
        if min_candidates:
            min_percent = min(min_candidates)
        if max_candidates:
            max_percent = max(max_candidates)

        # For CoolerControl, report profile fields from the live effective state so UI doesn't
        # show stale legacy hints from earlier backend behavior.
        profile_mode = mode
        if mode == "manual" and target_percent is not None:
            profile_target = _clamp_fan_percent(_safe_int(target_percent, 35), min_percent=0, max_percent=100)
        elif current_percent is not None:
            profile_target = _clamp_fan_percent(_safe_int(current_percent, 35), min_percent=0, max_percent=100)
        else:
            profile_target = _clamp_fan_percent(35, min_percent=0, max_percent=100)
        warning = " | ".join(warning_parts) if warning_parts else None

        gpus[str(gpu_idx)] = {
            "gpu_index": gpu_idx,
            "gpu_name": smi_meta.get("name", f"GPU {gpu_idx}"),
            "uuid": smi_meta.get("uuid"),
            "settings_gpu_target": None,
            "fan_targets": fan_channels,
            "fan_count": len(fan_channels),
            "mode": mode,
            "target_percent": target_percent if mode == "manual" else None,
            "current_percent": current_percent,
            "current_rpm": current_rpm,
            "min_percent": min_percent,
            "max_percent": max_percent,
            "profile_mode": profile_mode,
            "profile_target_percent": profile_target,
            "writable": writable,
            "mapping_source": mapped_device.get("mapping_source", "none"),
            "warning": warning,
            "fan_details": [],
            "coolercontrol_device_uid": device_uid,
            "coolercontrol_channels": fan_channels,
        }

    any_writable = any(bool(v.get("writable")) for v in gpus.values())
    messages: List[str] = []
    if devices_error:
        messages.append(devices_error)
    if not any_writable:
        if cookie_error:
            messages.append(cookie_error)
        messages.append("No writable CoolerControl GPU fan channels found")
    elif cookie_error:
        messages.append(cookie_error)
    if mode_error:
        messages.append(f"modes: {mode_error}")

    return {
        "supported": any_writable,
        "backend": FAN_BACKEND_COOLERCONTROL,
        "display": None,
        "xauthority": None,
        "message": " | ".join(messages),
        "mapping_source": "coolercontrol_device",
        "available_modes": available_modes,
        "gpus": gpus,
    }


def _fan_control_snapshot() -> Dict[str, Any]:
    backend = _fan_control_backend()
    if backend == FAN_BACKEND_COOLERCONTROL:
        return _fan_control_snapshot_coolercontrol()
    return _fan_control_snapshot_nvidia_settings()


def _invalidate_fan_control_cache() -> None:
    global _fan_control_cache, _fan_control_cache_time
    _fan_control_cache = None
    _fan_control_cache_time = 0.0


def _get_fan_control_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    global _fan_control_cache, _fan_control_cache_time

    now = time.monotonic()
    if (
        not force_refresh
        and _fan_control_cache is not None
        and (now - _fan_control_cache_time) < _FAN_CONTROL_CACHE_TTL_SECONDS
    ):
        return copy.deepcopy(_fan_control_cache)

    snapshot = _fan_control_snapshot()
    _fan_control_cache = copy.deepcopy(snapshot)
    _fan_control_cache_time = now
    return snapshot


def _apply_coolercontrol_gpu_setting(
    device_uid: str,
    channels: List[str],
    desired_mode: str,
    desired_target: int,
) -> tuple[bool, str]:
    if not device_uid:
        return False, "No CoolerControl device UID available for this GPU"
    if not channels:
        return False, "No CoolerControl fan channels available for this GPU"

    cookie, cookie_error = _coolercontrol_login_cookie()
    if not cookie:
        return False, cookie_error or "CoolerControl login failed"

    errors: List[str] = []
    for channel_name in channels:
        if desired_mode == "manual":
            ok, out = _coolercontrol_apply_manual(device_uid, channel_name, desired_target, cookie)
        else:
            ok, out = _coolercontrol_apply_reset(device_uid, channel_name, cookie)
        if not ok:
            errors.append(out or f"{channel_name}: unknown failure")

    if errors:
        return False, " | ".join(errors)

    if desired_mode == "manual":
        return True, f"Applied {desired_target}% to {len(channels)} channel(s)"
    return True, f"Reset {len(channels)} channel(s) to automatic/default control"


def _apply_gpu_fan_mode(settings_gpu_target: int, mode: str) -> tuple[bool, str]:
    value = 1 if mode == "manual" else 0
    return _run_nvidia_settings_assign(["-a", f"[gpu:{settings_gpu_target}]/GPUFanControlState={value}"])


def _apply_fan_target_percent(fan_target: int, percent: int) -> tuple[bool, str]:
    pct = _clamp_fan_percent(percent)
    return _run_nvidia_settings_assign(["-a", f"[fan:{fan_target}]/GPUTargetFanSpeed={pct}"])


# Load persisted state on module import.
_load_power_state()
_load_fan_state()


def _collect_gpu_stats() -> tuple[List[GPUStatusEnhanced], Optional[str]]:
    """Get enhanced GPU statistics using pynvml, with nvidia-smi fallback."""
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

            hw_limits = HARDWARE_LIMITS.get(gpu_idx, {"min": 100, "default": 300, "max": 400})
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
                    power_limit_w=round(_parse_float(parts[7], 0.0), 1),
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

        # Load active reservations and extract job info for process naming
        reservations, gpu_job_info = _load_active_gpu_reservations()

        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)

            # Name
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')

            # Utilization
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)

            # Memory
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)

            # Power
            try:
                power_draw = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # mW to W
                power_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
            except pynvml.NVMLError:
                power_draw = 0.0
                power_limit = 0.0

            # Temperature
            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

            # Fan speed
            try:
                fan_speed = pynvml.nvmlDeviceGetFanSpeed(handle)
            except pynvml.NVMLError:
                fan_speed = 0  # Some GPUs don't report fan speed

            # Clocks
            try:
                clock_graphics = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                clock_memory = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                clock_max_graphics = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                clock_max_memory = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
            except pynvml.NVMLError:
                clock_graphics = clock_memory = clock_max_graphics = clock_max_memory = 0

            def _infer_process_label(proc_name: str, cmdline: Optional[List[str]]) -> str:
                """Infer a human-friendly label from process name/cmdline."""
                base_name = proc_name or ""
                cmdline_list = cmdline or []
                cmdline_str = " ".join(cmdline_list)
                haystack = f"{base_name} {cmdline_str}".lower()

                # Order matters: more specific first to avoid false matches
                patterns = [
                    ("dorado_basecall_server", "Dorado Basecall Server"),
                    ("dorado-basecall-server", "Dorado Basecall Server"),
                    ("dorado_basecall", "Dorado Basecall Server"),
                    ("basecall_manager", "Basecall Manager"),
                    ("fampnn", "FAMPNN"),
                    ("seq_design.py", "FAMPNN"),
                    ("thermompnn", "ThermoMPNN"),
                    ("ppiflow", "PPIFlow"),
                    ("rfdiffusion_inference.py", "RFantibody"),
                    ("rfantibody", "RFantibody"),
                    ("boltzgen", "BoltzGen"),
                    ("boltz", "Boltz-2"),
                    ("af2_backprop", "AF2 Backprop"),
                    ("alphafold", "AlphaFold2"),
                    ("af2", "AlphaFold2"),
                    ("diffdock", "DiffDock"),
                    ("unidock", "Uni-Dock"),
                    # MPNN variants - specific first, generic last
                    ("fampnn", "FAMPNN"),
                    ("frustrampnn", "FrustraMPNN"),
                    ("frustra", "FrustraMPNN"),
                    ("ligandmpnn", "LigandMPNN"),
                    ("thermompnn", "ThermoMPNN"),
                    ("proteinmpnn", "ProteinMPNN"),
                    ("mpnn", "ProteinMPNN"),  # Generic fallback
                    ("rfd3", "RFdiffusion3"),
                    ("rfdiffusion", "RFdiffusion"),
                    ("rf3", "RoseTTAFold3"),
                    ("openmm", "OpenMM"),
                    ("antiberty", "AntiBERTy"),
                    ("anarcii", "ANARCII"),
                    ("immunebuilder", "ImmuneBuilder"),
                ]
                for key, label in patterns:
                    if key in haystack:
                        return label

                # If it's a generic python process, try to show the script name
                if base_name.lower() in ["python", "python3", "python3.10", "python3.11"]:
                    if cmdline_list and len(cmdline_list) > 1:
                        script_idx = 1
                        if cmdline_list[1] == "-m" and len(cmdline_list) > 2:
                            script_idx = 2
                        script = os.path.basename(cmdline_list[script_idx])
                        if script:
                            return script
                pretty_name = re.sub(r"[_-]+", " ", base_name).strip()
                return pretty_name or base_name

            # Processes
            processes = []
            try:
                procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                for proc in procs:
                    try:
                        import psutil
                        p = psutil.Process(proc.pid)
                        proc_name = p.name()
                        cmdline = p.cmdline()
                        proc_name = _infer_process_label(proc_name, cmdline)
                    except:
                        proc_name = f"PID {proc.pid}"

                    processes.append(GPUProcess(
                        pid=proc.pid,
                        name=proc_name,
                        memory_mb=proc.usedGpuMemory // (1024 * 1024) if proc.usedGpuMemory else 0
                    ))
            except pynvml.NVMLError:
                pass

            # Post-process: Rename "python" / "python3" with better labels
            # Use model_type from active reservations if available
            if i in reservations and reservations[i] > 0:
                # Get model type(s) for this GPU
                model_types = []
                if i in gpu_job_info:
                    for job_name, model_type in gpu_job_info[i]:
                        model_types.append(model_type)

                # Create display name
                if model_types:
                    # Map model types to display names - MPNN variants explicitly listed
                    MODEL_DISPLAY = {
                        'boltz': 'Boltz-2', 'boltz_batch': 'Boltz-2 Batch',
                        'rf3': 'RoseTTAFold3', 'af2': 'AlphaFold2',
                        'rfdiffusion': 'RFdiffusion', 'rfantibody': 'RFantibody',
                        # MPNN variants
                        'fampnn': 'FAMPNN', 'frustrampnn': 'FrustraMPNN',
                        'ligandmpnn': 'LigandMPNN', 'thermompnn': 'ThermoMPNN',
                        'mpnn': 'ProteinMPNN', 'proteinmpnn': 'ProteinMPNN',
                        # Other
                        'diffdock': 'DiffDock', 'unidock': 'Uni-Dock',
                        'boltzgen': 'BoltzGen', 'antibody_child': 'Antibody Validation',
                    }
                    display_names = [MODEL_DISPLAY.get(m, m) for m in model_types]
                    process_label = ", ".join(display_names[:2])  # Max 2 labels
                    if len(display_names) > 2:
                        process_label += f" +{len(display_names) - 2}"
                else:
                    process_label = "Job (Allocated)"
                
                for p in processes:
                    if p.name in ["python", "python3"]:
                        p.name = process_label
            
            # Get hardware limits for this GPU (fallback to defaults if not defined)
            hw_limits = HARDWARE_LIMITS.get(i, {"min": 100, "default": 300, "max": 400})
            
            gpus.append(GPUStatusEnhanced(
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
                processes=processes
            ))
        
        pynvml.nvmlShutdown()
        return gpus, None
        
    except Exception as e:
        message = str(e).strip() or e.__class__.__name__
        fallback_gpus, fallback_error = _collect_gpu_stats_via_nvidia_smi()
        if fallback_gpus:
            logger.warning("pynvml failed (%s); using nvidia-smi fallback", message)
            return fallback_gpus, None
        return [], fallback_error or message


def get_gpu_stats_with_error(force_refresh: bool = False) -> tuple[List[GPUStatusEnhanced], Optional[str]]:
    global _gpu_status_cache, _gpu_status_error, _gpu_status_cache_time

    now = time.monotonic()
    if not force_refresh and (now - _gpu_status_cache_time) < _GPU_STATUS_CACHE_TTL_SECONDS:
        return list(_gpu_status_cache), _gpu_status_error

    gpus, error = _collect_gpu_stats()
    if error and error != _gpu_status_error:
        logger.warning("GPU stats error: %s", error)
    _gpu_status_cache = list(gpus)
    _gpu_status_error = error
    _gpu_status_cache_time = now
    return list(gpus), error


def get_gpu_stats() -> List[GPUStatusEnhanced]:
    return get_gpu_stats_with_error()[0]


def _discover_rapl_package_sources() -> List[Dict[str, Any]]:
    """Find readable package-level RAPL energy counters."""
    powercap_root = Path("/sys/class/powercap")
    sources: List[Dict[str, Any]] = []

    if powercap_root.exists():
        for domain_path in sorted(powercap_root.glob("*-rapl:*")):
            name_path = domain_path / "name"
            energy_path = domain_path / "energy_uj"
            if not name_path.exists() or not energy_path.exists():
                continue

            try:
                domain_name = name_path.read_text().strip().lower()
            except OSError:
                continue

            if not domain_name.startswith("package-"):
                continue

            max_energy_uj = float(2**32)
            max_range_path = domain_path / "max_energy_range_uj"
            try:
                if max_range_path.exists():
                    max_energy_uj = float(max_range_path.read_text().strip())
            except (OSError, ValueError):
                pass

            sources.append({
                "energy_path": energy_path,
                "max_energy_uj": max_energy_uj,
            })

    fallback_path = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    if fallback_path.exists() and not any(source["energy_path"] == fallback_path for source in sources):
        max_energy_uj = float(2**32)
        max_range_path = fallback_path.parent / "max_energy_range_uj"
        try:
            if max_range_path.exists():
                max_energy_uj = float(max_range_path.read_text().strip())
        except (OSError, ValueError):
            pass

        sources.append({
            "energy_path": fallback_path,
            "max_energy_uj": max_energy_uj,
        })

    return sources


def _get_rapl_package_sources() -> List[Dict[str, Any]]:
    global _rapl_package_sources

    if _rapl_package_sources is None:
        _rapl_package_sources = _discover_rapl_package_sources()

    return _rapl_package_sources


def _sample_cpu_package_power_watts() -> Optional[float]:
    """Sample package power directly from RAPL energy counters."""
    sources = _get_rapl_package_sources()
    if not sources:
        return None

    total_power_watts = 0.0
    valid_samples = 0

    with _rapl_state_lock:
        for source in sources:
            energy_path = source["energy_path"]
            max_energy_uj = float(source["max_energy_uj"])

            try:
                current_energy = float(energy_path.read_text().strip())
            except (PermissionError, FileNotFoundError, ValueError, OSError):
                continue

            current_time = time.monotonic()
            cache_key = str(energy_path)
            previous = _rapl_sample_state.get(cache_key)

            if not previous:
                # Prime state without blocking the request. The first watt sample
                # can be null; the next request will have a real delta.
                _rapl_sample_state[cache_key] = {
                    "energy_uj": current_energy,
                    "time_s": current_time,
                }
                continue

            _rapl_sample_state[cache_key] = {
                "energy_uj": current_energy,
                "time_s": current_time,
            }

            time_delta_s = current_time - previous["time_s"]
            if time_delta_s <= 0.01:
                continue

            energy_delta_uj = current_energy - previous["energy_uj"]
            if energy_delta_uj < 0:
                energy_delta_uj += max_energy_uj

            power_watts = energy_delta_uj / (time_delta_s * 1_000_000)
            if power_watts >= 0:
                total_power_watts += power_watts
                valid_samples += 1

    if valid_samples == 0:
        return None

    return round(total_power_watts, 1)


def get_cpu_stats() -> CPUStatus:
    """Get CPU statistics using psutil."""
    # Get CPU name from /proc/cpuinfo on Linux
    cpu_name = "Unknown CPU"
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    cpu_name = line.split(":")[1].strip()
                    break
    except:
        pass

    freq = psutil.cpu_freq()

    # Try to get CPU temperature
    cpu_temp = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Try common sensor names
            for name in ['coretemp', 'k10temp', 'cpu_thermal', 'acpitz']:
                if name in temps and temps[name]:
                    cpu_temp = temps[name][0].current
                    break
    except:
        pass

    global _last_per_core_utilization, _cpu_percent_primed
    with _cpu_percent_lock:
        per_core_utilization = psutil.cpu_percent(interval=None, percpu=True)
        if not _cpu_percent_primed:
            _cpu_percent_primed = True

        if per_core_utilization:
            _last_per_core_utilization = list(per_core_utilization)
        elif _last_per_core_utilization:
            per_core_utilization = list(_last_per_core_utilization)

    overall_utilization = round(
        sum(per_core_utilization) / max(1, len(per_core_utilization)),
        1,
    )
    frequency_current_mhz = freq.current if freq else 0.0
    frequency_max_mhz = freq.max if freq else 0.0

    # Get CPU package power via RAPL only. If powercap is unreadable, return null
    # so the UI does not fabricate a wattage value.
    cpu_power = _sample_cpu_package_power_watts()
    
    return CPUStatus(
        name=cpu_name,
        cores_physical=psutil.cpu_count(logical=False) or 0,
        cores_logical=psutil.cpu_count(logical=True) or 0,
        utilization=overall_utilization,
        per_core_utilization=per_core_utilization,
        frequency_current_mhz=frequency_current_mhz,
        frequency_max_mhz=frequency_max_mhz,
        temperature=cpu_temp,
        power_watts=cpu_power
    )


def get_ram_stats() -> RAMStatus:
    """Get RAM statistics using psutil."""
    import psutil
    
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    
    return RAMStatus(
        total_gb=round(mem.total / (1024**3), 1),
        used_gb=round(mem.used / (1024**3), 1),
        available_gb=round(mem.available / (1024**3), 1),
        utilization=mem.percent,
        swap_total_gb=round(swap.total / (1024**3), 1),
        swap_used_gb=round(swap.used / (1024**3), 1),
        swap_percent=swap.percent
    )


@router.get("/status")
async def get_system_status():
    """Get complete system status including GPUs, CPU, and RAM."""
    # Run blocking hardware checks in thread pool to avoid blocking event loop
    cpu = await asyncio.to_thread(get_cpu_stats)
    ram = await asyncio.to_thread(get_ram_stats)
    gpus, gpu_error = await asyncio.to_thread(get_gpu_stats_with_error)
    
    # Append to history for sparkline graphs
    _cpu_history.append(cpu.utilization)
    _ram_history.append(ram.utilization)
    
    return SystemStatusResponse(
        gpus=gpus,
        gpu_error=gpu_error,
        cpu=cpu,
        ram=ram,
        timestamp=datetime.utcnow(),
        cpu_history=list(_cpu_history),
        ram_history=list(_ram_history)
    )


@router.get("/gpus")
async def get_gpus_only():
    """Get GPU status only (for lighter polling)."""
    gpus, gpu_error = await asyncio.to_thread(get_gpu_stats_with_error)
    return {"gpus": gpus, "gpu_error": gpu_error, "timestamp": datetime.utcnow()}


@router.get("/cpu")
async def get_cpu_only():
    """Get CPU status only."""
    cpu = await asyncio.to_thread(get_cpu_stats)
    return {"cpu": cpu, "timestamp": datetime.utcnow()}


@router.get("/ram")
async def get_ram_only():
    """Get RAM status only."""
    ram = await asyncio.to_thread(get_ram_stats)
    return {"ram": ram, "timestamp": datetime.utcnow()}


# --- Power Profile Endpoints ---

class PowerProfileResponse(BaseModel):
    eco_mode: bool
    message: str


def set_gpu_power_limit(gpu_index: int, watts: int) -> bool:
    """Set power limit for a specific GPU using nvidia-smi."""
    try:
        cmds = [
            ["nvidia-smi", "-i", str(gpu_index), "-pl", str(watts)],
            ["sudo", "-n", "nvidia-smi", "-i", str(gpu_index), "-pl", str(watts)],
        ]
        for cmd in cmds:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        return False
    except Exception as e:
        logger.warning("Failed to set power limit for GPU %d: %s", gpu_index, e)
        return False


@router.get("/power-control")
async def get_power_control():
    """Get current power control state."""
    return _get_power_control_payload()


class PowerControlRequest(BaseModel):
    preset: Optional[str] = None  # "eco" or "stock"
    gpu_index: Optional[int] = None
    limit_watts: Optional[int] = None
    toggle: Optional[bool] = None  # Toggle between saved limits and stock


@router.post("/power-control")
async def set_power_control(request: PowerControlRequest):
    """Set power limits via preset, manual control, or toggle."""
    global _current_limits, _saved_limits, _power_enabled

    _invalidate_power_control_cache()
    _refresh_power_state_from_hardware()
    errors: List[str] = []
    applied_count = 0
    
    if request.toggle:
        # Toggle between saved limits and stock
        target_enabled = not _power_enabled
        for gpu_idx, limits in HARDWARE_LIMITS.items():
            target = _saved_limits[gpu_idx] if target_enabled else int(limits["default"])
            if set_gpu_power_limit(gpu_idx, target):
                _current_limits[gpu_idx] = target
                applied_count += 1
            else:
                errors.append(f"GPU {gpu_idx}")
        _power_enabled = target_enabled if not errors else _derive_power_enabled_from_current()
        message = f"Power limits {'enabled' if target_enabled else 'disabled (stock)'}"
        
    elif request.preset:
        # Apply preset to all GPUs
        for gpu_idx, limits in HARDWARE_LIMITS.items():
            if request.preset == "eco":
                target = int(limits["eco"])
            elif request.preset == "stock":
                target = int(limits["default"])
            else:
                raise HTTPException(status_code=400, detail=f"Unknown preset: {request.preset}")
            
            if set_gpu_power_limit(gpu_idx, target):
                _current_limits[gpu_idx] = target
                applied_count += 1
                if request.preset == "eco":
                    _saved_limits[gpu_idx] = target
            else:
                errors.append(f"GPU {gpu_idx}")
        if request.preset == "eco":
            _power_enabled = len(errors) == 0 or _derive_power_enabled_from_current()
        elif request.preset == "stock":
            _power_enabled = False if not errors else _derive_power_enabled_from_current()

        message = f"Applied '{request.preset}' preset"
        
    elif request.gpu_index is not None and request.limit_watts is not None:
        # Manual single-GPU control - also saves to _saved_limits
        if request.gpu_index not in HARDWARE_LIMITS:
            raise HTTPException(status_code=400, detail=f"Unknown GPU index: {request.gpu_index}")
        
        clamped = _clamp_power_limit(request.gpu_index, int(request.limit_watts))
        
        if set_gpu_power_limit(request.gpu_index, clamped):
            _current_limits[request.gpu_index] = clamped
            _saved_limits[request.gpu_index] = clamped  # Save for toggle memory
            _power_enabled = _derive_power_enabled_from_current()
            applied_count += 1
            message = f"GPU {request.gpu_index} set to {clamped}W"
        else:
            errors.append(f"GPU {request.gpu_index}")
            message = f"Failed to set GPU {request.gpu_index}"
    else:
        raise HTTPException(status_code=400, detail="Must provide 'toggle', 'preset', or both 'gpu_index' and 'limit_watts'")

    _refresh_power_state_from_hardware()
    if applied_count > 0:
        _save_power_state()
    payload = _get_power_control_payload(force_refresh=True)

    if errors:
        message += f" (Failed: {', '.join(errors)})"
    
    return {
        "success": len(errors) == 0 and applied_count > 0,
        "message": message,
        **payload,
    }


class FanControlRequest(BaseModel):
    gpu_index: int
    mode: Optional[str] = None  # auto | manual
    target_percent: Optional[int] = None


class FanMappingOverrideRequest(BaseModel):
    mapping: Dict[str, List[int]]


@router.get("/fan-control")
async def get_fan_control():
    """Get per-GPU fan control status and mapping."""
    return _get_fan_control_snapshot()


@router.put("/fan-control/mapping")
async def update_fan_control_mapping(request: FanMappingOverrideRequest):
    """Persist explicit nvidia-smi GPU index -> fan target list overrides."""
    global _fan_mapping_overrides
    if _fan_control_backend() == FAN_BACKEND_COOLERCONTROL:
        raise HTTPException(
            status_code=400,
            detail="Fan target mapping overrides are only supported with BMS_FAN_CONTROL_BACKEND=nvidia-settings",
        )

    normalized: Dict[int, List[int]] = {}
    for gpu_key, fan_list in request.mapping.items():
        try:
            gpu_idx = int(gpu_key)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Invalid GPU key: {gpu_key}")
        if gpu_idx not in HARDWARE_LIMITS:
            raise HTTPException(status_code=400, detail=f"Unknown GPU index: {gpu_idx}")
        if not isinstance(fan_list, list):
            raise HTTPException(status_code=400, detail=f"Fan list for GPU {gpu_idx} must be an array")
        parsed_fans: List[int] = []
        for fan_target in fan_list:
            try:
                parsed_fans.append(int(fan_target))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Invalid fan target '{fan_target}' for GPU {gpu_idx}")
        normalized[gpu_idx] = sorted(set(parsed_fans))

    _fan_mapping_overrides = normalized
    _save_fan_state()
    _invalidate_fan_control_cache()
    snapshot = _get_fan_control_snapshot(force_refresh=True)
    return {
        "success": True,
        "message": "Updated fan target mapping overrides",
        "fan_control": snapshot,
    }


@router.post("/fan-control")
async def set_fan_control(request: FanControlRequest):
    """Apply fan mode/speed for a single nvidia-smi GPU index using the configured backend."""
    gpu_idx = int(request.gpu_index)
    if gpu_idx not in HARDWARE_LIMITS:
        valid = ",".join(str(idx) for idx in sorted(HARDWARE_LIMITS.keys()))
        raise HTTPException(status_code=400, detail=f"Unknown GPU index {gpu_idx}. Valid: {valid}")

    snapshot = _get_fan_control_snapshot(force_refresh=True)
    gpu_state = snapshot.get("gpus", {}).get(str(gpu_idx), {})
    if not snapshot.get("supported"):
        return {
            "success": False,
            "message": snapshot.get("message", "Fan control unsupported"),
            "fan_control": snapshot,
        }
    if not gpu_state:
        return {
            "success": False,
            "message": f"GPU {gpu_idx} not present in fan control snapshot",
            "fan_control": snapshot,
        }

    desired_mode = str(request.mode or gpu_state.get("profile_mode") or gpu_state.get("mode") or "auto").strip().lower()
    if desired_mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="mode must be 'auto' or 'manual'")

    current_or_profile_target = _safe_int(
        request.target_percent
        if request.target_percent is not None
        else (
            gpu_state.get("profile_target_percent")
            if gpu_state.get("profile_target_percent") is not None
            else gpu_state.get("target_percent")
        ),
        35,
    )
    min_percent = _safe_int(gpu_state.get("min_percent"), 30)
    max_percent = _safe_int(gpu_state.get("max_percent"), 100)
    desired_target = _clamp_fan_percent(current_or_profile_target, min_percent=min_percent, max_percent=max_percent)

    backend = str(snapshot.get("backend") or _fan_control_backend())
    if backend == FAN_BACKEND_COOLERCONTROL:
        device_uid = str(gpu_state.get("coolercontrol_device_uid") or "").strip()
        channels = [str(ch) for ch in (gpu_state.get("coolercontrol_channels") or []) if str(ch).strip()]
        mode_ok, mode_out = _apply_coolercontrol_gpu_setting(
            device_uid=device_uid,
            channels=channels,
            desired_mode=desired_mode,
            desired_target=desired_target,
        )
        if mode_ok:
            _fan_profiles[gpu_idx] = {
                "mode": desired_mode,
                "target_percent": desired_target,
            }
            _save_fan_state()
        _invalidate_fan_control_cache()
        refreshed = _get_fan_control_snapshot(force_refresh=True)
        if mode_ok:
            if desired_mode == "manual":
                message = (
                    f"GPU {gpu_idx} ({device_uid}) set to manual {desired_target}% "
                    f"across {len(channels)} channel(s)"
                )
            else:
                message = (
                    f"GPU {gpu_idx} ({device_uid}) restored to automatic/default control "
                    f"across {len(channels)} channel(s)"
                )
        else:
            message = f"Failed to update CoolerControl fan setting for GPU {gpu_idx}: {mode_out or 'unknown error'}"
        return {
            "success": mode_ok,
            "message": message,
            "fan_control": refreshed,
        }

    settings_target = gpu_state.get("settings_gpu_target")
    fan_targets = [int(f) for f in gpu_state.get("fan_targets", [])]
    if settings_target is None:
        return {
            "success": False,
            "message": f"GPU {gpu_idx} has no nvidia-settings target mapping",
            "fan_control": snapshot,
        }
    if desired_mode == "manual" and not fan_targets:
        return {
            "success": False,
            "message": f"GPU {gpu_idx} has no mapped fan targets for manual control",
            "fan_control": snapshot,
        }

    errors: List[str] = []
    mode_ok, mode_out = _apply_gpu_fan_mode(int(settings_target), desired_mode)
    if not mode_ok:
        errors.append(f"mode assign failed: {mode_out or 'unknown error'}")

    if mode_ok and desired_mode == "manual":
        for fan_target in fan_targets:
            fan_ok, fan_out = _apply_fan_target_percent(fan_target, desired_target)
            if not fan_ok:
                errors.append(f"fan:{fan_target} assign failed: {fan_out or 'unknown error'}")

    success = len(errors) == 0
    if success:
        _fan_profiles[gpu_idx] = {
            "mode": desired_mode,
            "target_percent": desired_target,
        }
        _save_fan_state()

    _invalidate_fan_control_cache()
    refreshed = _get_fan_control_snapshot(force_refresh=True)
    if success:
        if desired_mode == "auto":
            message = f"GPU {gpu_idx} fan control set to auto"
        else:
            message = f"GPU {gpu_idx} fan target set to {desired_target}%"
    else:
        message = f"Failed to update GPU {gpu_idx} fan control: {' | '.join(errors)}"

    return {
        "success": success,
        "message": message,
        "fan_control": refreshed,
    }


class SchedulerGlobalConfig(BaseModel):
    """Global scheduler settings."""
    busy_threshold: float = DEFAULT_SCHEDULER_CONFIG["global"]["busy_threshold"]  # 0.0-1.0
    cooldown_ms: int = DEFAULT_SCHEDULER_CONFIG["global"]["cooldown_ms"]
    cpu_threads_per_job: int = DEFAULT_SCHEDULER_CONFIG["global"]["cpu_threads_per_job"]
    enabled: bool = DEFAULT_SCHEDULER_CONFIG["global"]["enabled"]
    target_vram_fill: float = DEFAULT_SCHEDULER_CONFIG["global"]["target_vram_fill"]
    capacity_weight: float = DEFAULT_SCHEDULER_CONFIG["global"]["capacity_weight"]
    emptiness_weight: float = DEFAULT_SCHEDULER_CONFIG["global"]["emptiness_weight"]
    max_launches_per_cycle: int = DEFAULT_SCHEDULER_CONFIG["global"]["max_launches_per_cycle"]
    msa_concurrency_limit: int = DEFAULT_SCHEDULER_CONFIG["global"]["msa_concurrency_limit"]
    msa_preferred_gpu_ids: List[int] = DEFAULT_SCHEDULER_CONFIG["global"]["msa_preferred_gpu_ids"]
    msa_avoid_heavy_gpus: bool = DEFAULT_SCHEDULER_CONFIG["global"]["msa_avoid_heavy_gpus"]


class SchedulerGPUOverride(BaseModel):
    """Per-GPU override settings."""
    force_available: bool = False         # Permanent override (debug mode)
    quick_enable: bool = False            # One-shot: accept 1 job, then auto-clear
    threshold: Optional[float] = None     # null = use global
    disabled: bool = False                # GPU excluded from orchestrator scheduling
    priority_tier: Optional[int] = None   # Manual priority tier (higher = preferred)
    vram_safety_margin_mb: int = 500      # VRAM buffer to leave free
    max_concurrent_jobs: Optional[int] = None  # Max jobs on this GPU (null = unlimited)


class SchedulerConfigResponse(BaseModel):
    """Full scheduler config response."""
    global_config: SchedulerGlobalConfig
    overrides: Dict[str, SchedulerGPUOverride]
    config_path: str


@router.get("/scheduler-config")
async def get_scheduler_config():
    """Get current GPU scheduler configuration."""
    config = read_scheduler_config()
    return {
        "global": config.get("global", {}),
        "overrides": config.get("overrides", {}),
        "workflow_pins": config.get("workflow_pins", {}),
        "gpu_locks": config.get("gpu_locks", {}),
        "config_path": str(GPU_CONFIG_PATH)
    }


@router.put("/scheduler-config")
async def update_scheduler_config(global_config: SchedulerGlobalConfig):
    """Update global scheduler settings."""
    config = read_scheduler_config()
    config["global"] = {
        "busy_threshold": max(0.0, min(1.0, global_config.busy_threshold)),
        "cooldown_ms": max(0, min(60000, global_config.cooldown_ms)),
        "cpu_threads_per_job": max(1, min(24, global_config.cpu_threads_per_job)),
        "enabled": global_config.enabled,
        "target_vram_fill": max(0.5, min(0.95, global_config.target_vram_fill)),
        "capacity_weight": max(0.0, min(10.0, global_config.capacity_weight)),
        "emptiness_weight": max(0.0, min(10.0, global_config.emptiness_weight)),
        "max_launches_per_cycle": max(1, min(20, global_config.max_launches_per_cycle)),
        "msa_concurrency_limit": max(1, min(4, global_config.msa_concurrency_limit)),
        "msa_preferred_gpu_ids": sorted({int(g) for g in global_config.msa_preferred_gpu_ids if isinstance(g, int) and g >= 0}),
        "msa_avoid_heavy_gpus": bool(global_config.msa_avoid_heavy_gpus),
    }
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "message": f"Updated: capacity_weight={config['global']['capacity_weight']}, emptiness_weight={config['global']['emptiness_weight']}",
        "global": config["global"],
        "overrides": config["overrides"]
    }


@router.put("/scheduler-config/gpu/{gpu_id}")
async def set_gpu_override(gpu_id: str, override: SchedulerGPUOverride):
    """Set per-GPU override (force_available, quick_enable, or custom threshold)."""
    config = read_scheduler_config()
    
    config["overrides"][gpu_id] = {
        "force_available": override.force_available,
        "quick_enable": override.quick_enable,
        "threshold": override.threshold,
        "disabled": override.disabled,
        "priority_tier": override.priority_tier,
        "vram_safety_margin_mb": override.vram_safety_margin_mb,
        "max_concurrent_jobs": override.max_concurrent_jobs,
    }
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "message": f"GPU {gpu_id}: force_available={override.force_available}, disabled={override.disabled}",
        "overrides": config["overrides"]
    }


@router.post("/scheduler-config/gpu/{gpu_id}/toggle-disable")
async def toggle_gpu_disabled(gpu_id: str):
    """Simple toggle to enable/disable a GPU from inference scheduling."""
    config = read_scheduler_config()
    
    # Get current state
    overrides = config.get("overrides", {})
    gpu_override = overrides.get(gpu_id, {})
    current_disabled = gpu_override.get("disabled", False)
    
    # Toggle
    new_disabled = not current_disabled
    
    # Update override
    if gpu_id not in config["overrides"]:
        config["overrides"][gpu_id] = {}
    config["overrides"][gpu_id]["disabled"] = new_disabled
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "gpu_id": gpu_id,
        "disabled": new_disabled,
        "message": f"GPU {gpu_id} {'disabled' if new_disabled else 'enabled'} for inference"
    }


@router.delete("/scheduler-config/gpu/{gpu_id}")
async def clear_gpu_override(gpu_id: str):
    """Clear per-GPU override, reverting to global settings."""
    config = read_scheduler_config()
    
    if gpu_id in config["overrides"]:
        del config["overrides"][gpu_id]
        write_scheduler_config(config)
        return {"success": True, "message": f"Cleared override for GPU {gpu_id}"}
    
    return {"success": True, "message": f"No override existed for GPU {gpu_id}"}


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW PINNING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/workflow-pins")
async def get_workflow_pins():
    """
    Get all active workflow-level GPU pins.
    
    Workflow pins route ALL jobs of a specific model_type to a specific GPU.
    """
    config = read_scheduler_config()
    return {
        "workflow_pins": config.get("workflow_pins", {}),
        "available_workflows": [
            "boltz", "fampnn", "rfantibody", "rfdiffusion", "rfd3", "rf3",
            "af2", "mpnn", "boltzgen", "diffdock", "unidock", "msa_batch",
            "antibody_child", "antibody_denovo"
        ]
    }


@router.post("/workflow-pins/{workflow_type}/gpu/{gpu_id}")
async def pin_workflow_to_gpu(workflow_type: str, gpu_id: int):
    """
    Pin all jobs of a specific workflow type to a GPU.
    
    Args:
        workflow_type: Model type (e.g., 'boltz', 'fampnn', 'rfantibody')
        gpu_id: GPU index
    
    Example: POST /gpu/workflow-pins/boltz/gpu/2
             → All Boltz jobs will run on GPU 2
    """
    if gpu_id not in HARDWARE_LIMITS:
        valid = ",".join(str(idx) for idx in sorted(HARDWARE_LIMITS.keys()))
        raise HTTPException(status_code=400, detail=f"Invalid GPU index: {gpu_id}. Valid: {valid}.")
    
    config = read_scheduler_config()
    
    if "workflow_pins" not in config:
        config["workflow_pins"] = {}
    
    config["workflow_pins"][workflow_type] = gpu_id
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "message": f"All '{workflow_type}' jobs will now run on GPU {gpu_id}",
        "workflow_pins": config["workflow_pins"]
    }


@router.delete("/workflow-pins/{workflow_type}")
async def unpin_workflow(workflow_type: str):
    """Remove workflow-level GPU pin for a model type."""
    config = read_scheduler_config()
    
    workflow_pins = config.get("workflow_pins", {})
    
    if workflow_type in workflow_pins:
        del workflow_pins[workflow_type]
        config["workflow_pins"] = workflow_pins
        
        if not write_scheduler_config(config):
            raise HTTPException(status_code=500, detail="Failed to save config")
        
        return {
            "success": True,
            "message": f"Removed pin for '{workflow_type}' - jobs will use normal orchestrator logic",
            "workflow_pins": config["workflow_pins"]
        }
    
    return {
        "success": True,
        "message": f"No pin existed for '{workflow_type}'",
        "workflow_pins": workflow_pins
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GPU LOCK ENDPOINTS (Exclusive batch access)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/gpu-locks")
async def get_gpu_locks():
    """
    Get all active GPU locks.
    
    GPU locks reserve a GPU exclusively for a batch of child jobs.
    Other workflows are blocked from using a locked GPU.
    """
    config = read_scheduler_config()
    return {
        "gpu_locks": config.get("gpu_locks", {}),
        "message": "GPU locks reserve a GPU exclusively for a batch. Other jobs are blocked."
    }


@router.post("/gpu-locks/{batch_id}/gpu/{gpu_id}")
async def lock_gpu_for_batch(batch_id: str, gpu_id: int):
    """
    Lock a GPU exclusively for a batch of jobs.
    
    When locked:
    - All jobs in this batch will run on the specified GPU
    - Other workflows/batches are BLOCKED from this GPU
    
    Args:
        batch_id: Unique batch identifier (e.g., parent job ID)
        gpu_id: GPU index to lock
    """
    if gpu_id not in HARDWARE_LIMITS:
        valid = ",".join(str(idx) for idx in sorted(HARDWARE_LIMITS.keys()))
        raise HTTPException(status_code=400, detail=f"Invalid GPU index: {gpu_id}. Valid: {valid}.")
    
    config = read_scheduler_config()
    
    if "gpu_locks" not in config:
        config["gpu_locks"] = {}
    
    # Check if this GPU is already locked by another batch
    existing_locks = config["gpu_locks"]
    for existing_batch, locked_gpu in existing_locks.items():
        if locked_gpu == gpu_id and existing_batch != batch_id:
            raise HTTPException(
                status_code=409,
                detail=f"GPU {gpu_id} is already locked by batch '{existing_batch}'"
            )
    
    config["gpu_locks"][batch_id] = gpu_id
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "message": f"GPU {gpu_id} is now LOCKED for batch '{batch_id}'. Other workflows blocked.",
        "batch_id": batch_id,
        "gpu_id": gpu_id,
        "gpu_locks": config["gpu_locks"]
    }


@router.delete("/gpu-locks/{batch_id}")
async def unlock_gpu_for_batch(batch_id: str):
    """
    Release a GPU lock for a batch.
    
    Call this when all jobs in a batch have completed to allow other
    workflows to use the GPU again.
    """
    config = read_scheduler_config()
    
    gpu_locks = config.get("gpu_locks", {})
    
    if batch_id in gpu_locks:
        released_gpu = gpu_locks[batch_id]
        del gpu_locks[batch_id]
        config["gpu_locks"] = gpu_locks
        
        if not write_scheduler_config(config):
            raise HTTPException(status_code=500, detail="Failed to save config")
        
        return {
            "success": True,
            "message": f"GPU {released_gpu} is now UNLOCKED (was reserved by batch '{batch_id}')",
            "released_gpu": released_gpu,
            "gpu_locks": config["gpu_locks"]
        }
    
    return {
        "success": True,
        "message": f"No lock existed for batch '{batch_id}'",
        "gpu_locks": gpu_locks
    }


@router.get("/batch-status/{batch_id}")
async def get_batch_status(batch_id: str):
    """
    Get the GPU assignment status for a batch.
    
    Returns whether the batch has a GPU lock and which GPU it's using.
    """
    config = read_scheduler_config()
    gpu_locks = config.get("gpu_locks", {})
    
    if batch_id in gpu_locks:
        return {
            "batch_id": batch_id,
            "locked": True,
            "gpu_id": gpu_locks[batch_id],
            "mode": "exclusive"
        }
    
    return {
        "batch_id": batch_id,
        "locked": False,
        "gpu_id": None,
        "mode": "round_robin"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG ORCHESTRATOR OVERRIDES
# These endpoints allow bypassing normal orchestrator scheduling for debugging
# ═══════════════════════════════════════════════════════════════════════════════

class ForceRunRequest(BaseModel):
    """Request to force-run a queued job."""
    gpu_id: Optional[int] = None  # None = any available


@router.post("/force-run/{job_id}")
async def force_run_job(job_id: str, request: ForceRunRequest):
    """
    [DEBUG] Force a queued job to run immediately, bypassing orchestrator.
    
    Skips VRAM checks and concurrency limits. Use with caution.
    """
    import logging
    logger = logging.getLogger("api.gpu")

    # Determine GPU
    gpu_id = request.gpu_id
    if gpu_id is None:
        # Pick least-loaded GPU
        try:
            gpu_stats = get_gpu_stats()
            enabled_gpus = [g for g in gpu_stats if g.index != 1]  # Skip GPU 1 (display)
            if enabled_gpus:
                gpu_id = min(enabled_gpus, key=lambda g: g.memory_used_mb).index
            else:
                gpu_id = 0
        except Exception:
            gpu_id = 0

    job = await force_launch_job_service(
        job_id=job_id,
        gpu_id=gpu_id,
        allowed_queue_statuses=["queued"],
    )

    logger.warning(f"[FORCE RUN] User forced {job.name} to GPU {gpu_id}")

    return {
        "success": True,
        "message": f"Force-launched {job.name} on GPU {gpu_id}",
        "job_id": job_id,
        "gpu_id": gpu_id
    }


class ConcurrencyLimitRequest(BaseModel):
    """Request to set concurrency limit for a model type."""
    model_type: str  # e.g., "fampnn", "rfantibody", "boltz"
    limit: Optional[Union[int, str]] = None  # None = unlimited, "auto" = VRAM-derived


@router.get("/concurrency-limits")
async def get_concurrency_limits():
    """
    [DEBUG] Get current concurrency limits for all model types.
    """
    config = read_scheduler_config()
    return {
        "concurrency_limits": config.get("concurrency_limits", {}),
        "description": "Model type -> max concurrent running jobs (cap; 'auto' uses VRAM-derived limit)"
    }


@router.put("/concurrency-limits")
async def set_concurrency_limit(request: ConcurrencyLimitRequest):
    """
    [DEBUG] Set concurrency limit for a specific model type.
    
    Limits how many jobs of this type can run concurrently (cap).
    Set to null for unlimited, or "auto" for VRAM-derived limit.
    """
    import logging
    logger = logging.getLogger("api.gpu")
    
    config = read_scheduler_config()
    
    if "concurrency_limits" not in config:
        config["concurrency_limits"] = {}
    
    old_limit = config["concurrency_limits"].get(request.model_type)
    
    if request.limit is None:
        # Remove the limit (unlimited)
        config["concurrency_limits"].pop(request.model_type, None)
        logger.info(f"[CONCURRENCY] Removed limit for {request.model_type}")
    elif isinstance(request.limit, str):
        if request.limit.lower() != "auto":
            raise HTTPException(status_code=400, detail="limit must be an integer, 'auto', or null")
        config["concurrency_limits"][request.model_type] = "auto"
        logger.info(f"[CONCURRENCY] Set {request.model_type} limit to auto")
    else:
        if request.limit < 1:
            raise HTTPException(status_code=400, detail="limit must be >= 1, 'auto', or null")
        config["concurrency_limits"][request.model_type] = int(request.limit)
        logger.info(f"[CONCURRENCY] Set {request.model_type} limit to {request.limit}")
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    return {
        "success": True,
        "model_type": request.model_type,
        "old_limit": old_limit,
        "new_limit": request.limit,
        "concurrency_limits": config["concurrency_limits"]
    }


@router.delete("/concurrency-limits/{model_type}")
async def delete_concurrency_limit(model_type: str):
    """
    [DEBUG] Remove concurrency limit for a model type (revert to auto).
    """
    import logging
    logger = logging.getLogger("api.gpu")
    
    config = read_scheduler_config()
    
    if "concurrency_limits" not in config:
        return {"success": True, "message": "No limits configured"}
    
    old_limit = config["concurrency_limits"].pop(model_type, None)
    
    if old_limit is None:
        return {"success": True, "message": f"No limit was set for {model_type}"}
    
    if not write_scheduler_config(config):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    logger.info(f"[CONCURRENCY] Removed limit for {model_type} (was {old_limit})")
    
    return {
        "success": True,
        "model_type": model_type,
        "removed_limit": old_limit,
        "concurrency_limits": config["concurrency_limits"]
    }

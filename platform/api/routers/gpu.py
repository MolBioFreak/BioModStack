"""
System monitoring API router - GPU, CPU, RAM statistics.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from collections import deque
from pathlib import Path
import subprocess
import json
import asyncio
import time

router = APIRouter()

# Hardware power limits per GPU: (min, default, max, eco_preset)
# Values from nvidia-smi -q -d POWER
HARDWARE_LIMITS = {
    0: {"min": 400, "default": 575, "max": 600, "eco": 500, "name": "RTX 5090"},
    1: {"min": 150, "default": 180, "max": 200, "eco": 165, "name": "RTX 5060 Ti"},
    2: {"min": 100, "default": 370, "max": 380, "eco": 300, "name": "RTX 3090"},
    3: {"min": 100, "default": 390, "max": 480, "eco": 300, "name": "RTX 3090"},
}

# --- GPU Scheduler Config Endpoints ---
# Config file path (in project root, read by Nextflow)
GPU_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / ".gpu_config.json"
GPU_RESERVATIONS_PATH = Path(__file__).parent.parent.parent.parent / ".gpu_reservations.json"
HARDWARE_LIMITS = {
    0: {"min": 400, "default": 575, "max": 600, "eco": 500, "name": "RTX 5090"},
    1: {"min": 150, "default": 180, "max": 200, "eco": 165, "name": "RTX 5060 Ti"},
    2: {"min": 100, "default": 370, "max": 380, "eco": 300, "name": "RTX 3090"},
    3: {"min": 100, "default": 390, "max": 480, "eco": 300, "name": "RTX 3090"},
}

# Power control state (in-memory, resets on restart)
_current_limits = {gpu_idx: limits["default"] for gpu_idx, limits in HARDWARE_LIMITS.items()}
_saved_limits = {gpu_idx: limits["eco"] for gpu_idx, limits in HARDWARE_LIMITS.items()}  # User's saved profile
_power_enabled = False  # True = using saved limits, False = using stock

# Historical data for sparkline graphs (max 60 samples = ~2 min at 2s polling)
_cpu_history: deque = deque(maxlen=60)
_ram_history: deque = deque(maxlen=60)


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
_last_rapl_energy_uj: Optional[float] = None
_last_rapl_time: Optional[float] = None


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
    cpu: CPUStatus
    ram: RAMStatus
    timestamp: datetime
    # Historical data for sparkline graphs (last 60 samples)
    cpu_history: List[float] = []  # Overall CPU % over time
    ram_history: List[float] = []  # RAM % over time


def get_gpu_stats() -> List[GPUStatusEnhanced]:
    """Get enhanced GPU statistics using pynvml."""
    try:
        import pynvml
        pynvml.nvmlInit()
        
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []

        # Load active reservations and extract job info for process naming
        reservations = {}  # gpu_idx -> total_vram
        gpu_job_info = {}  # gpu_idx -> list of (job_name, model_type) for active reservations
        try:
            if GPU_RESERVATIONS_PATH.exists():
                with open(GPU_RESERVATIONS_PATH, "r") as f:
                    data = json.load(f)
                    now = time.time() * 1000  # ms
                    
                    for gpu_idx, res_list in data.items():
                        active_vram = 0
                        job_infos = []
                        for res in res_list:
                            # Only count if within last 60s (or custom duration)
                            # The scheduler cleans this up, but we filter here for UI accuracy
                            if (now - res.get("timestamp", 0)) < 60000:
                                active_vram += res.get("vram", 0)
                                job_name = res.get("job_name")
                                model_type = res.get("model_type")
                                if model_type:
                                    job_infos.append((job_name, model_type))
                        reservations[int(gpu_idx)] = active_vram
                        if job_infos:
                            gpu_job_info[int(gpu_idx)] = job_infos
        except Exception as e:
            print(f"Error reading reservations: {e}")
        
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
            
            # Processes
            processes = []
            try:
                procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                for proc in procs:
                    try:
                        import psutil
                        p = psutil.Process(proc.pid)
                        proc_name = p.name()
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
                    # Map model types to display names
                    MODEL_DISPLAY = {
                        'boltz': 'Boltz-2', 'boltz_batch': 'Boltz-2 Batch',
                        'rf3': 'RoseTTAFold3', 'af2': 'AlphaFold2',
                        'rfdiffusion': 'RFdiffusion', 'rfantibody': 'RFantibody',
                        'fampnn': 'FAMPNN', 'mpnn': 'ProteinMPNN', 'proteinmpnn': 'ProteinMPNN',
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
        return gpus
        
    except Exception as e:
        print(f"GPU stats error: {e}")
        return []


def get_cpu_stats() -> CPUStatus:
    """Get CPU statistics using psutil."""
    import psutil
    global _last_rapl_energy_uj, _last_rapl_time
    
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
    
    # Get CPU package power via RAPL (works for Intel and AMD)
    cpu_power = None
    try:
        rapl_energy_path = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
        if rapl_energy_path.exists():
            current_energy = float(rapl_energy_path.read_text().strip())
            current_time = time.time()
            
            if _last_rapl_energy_uj is not None and _last_rapl_time is not None:
                # Calculate power from energy delta
                energy_delta_uj = current_energy - _last_rapl_energy_uj
                time_delta_s = current_time - _last_rapl_time
                
                # Handle counter rollover (32-bit counter rolls over at ~4294967296)
                if energy_delta_uj < 0:
                    energy_delta_uj += 2**32
                
                if time_delta_s > 0.01:  # Avoid division by zero
                    cpu_power = round(energy_delta_uj / (time_delta_s * 1_000_000), 1)  # uJ to W
            
            _last_rapl_energy_uj = current_energy
            _last_rapl_time = current_time
    except (PermissionError, FileNotFoundError, ValueError):
        # RAPL requires read permission - may need to configure group access
        pass
    
    return CPUStatus(
        name=cpu_name,
        cores_physical=psutil.cpu_count(logical=False) or 0,
        cores_logical=psutil.cpu_count(logical=True) or 0,
        utilization=psutil.cpu_percent(interval=0.1),
        per_core_utilization=psutil.cpu_percent(interval=0.1, percpu=True),
        frequency_current_mhz=freq.current if freq else 0,
        frequency_max_mhz=freq.max if freq else 0,
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
    gpus = await asyncio.to_thread(get_gpu_stats)
    
    # Append to history for sparkline graphs
    _cpu_history.append(cpu.utilization)
    _ram_history.append(ram.utilization)
    
    return SystemStatusResponse(
        gpus=gpus,
        cpu=cpu,
        ram=ram,
        timestamp=datetime.utcnow(),
        cpu_history=list(_cpu_history),
        ram_history=list(_ram_history)
    )


@router.get("/gpus")
async def get_gpus_only():
    """Get GPU status only (for lighter polling)."""
    gpus = await asyncio.to_thread(get_gpu_stats)
    return {"gpus": gpus, "timestamp": datetime.utcnow()}


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
        # Using sudo as requested/required for power limit modification
        result = subprocess.run(
            ["sudo", "nvidia-smi", "-i", str(gpu_index), "-pl", str(watts)],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Failed to set power limit for GPU {gpu_index}: {e}")
        return False


@router.get("/power-control")
async def get_power_control():
    """Get current power control state."""
    # Determine if eco mode is active (any GPU below default)
    any_below_default = any(
        _current_limits.get(idx, limits["default"]) < limits["default"]
        for idx, limits in HARDWARE_LIMITS.items()
    )
    
    # Calculate power percentage (0% = all at min, 100% = all at max)
    total_min = sum(limits["min"] for limits in HARDWARE_LIMITS.values())
    total_max = sum(limits["max"] for limits in HARDWARE_LIMITS.values())
    total_current = sum(_current_limits.get(idx, limits["default"]) for idx, limits in HARDWARE_LIMITS.items())
    
    power_range = total_max - total_min
    power_percentage = round(((total_current - total_min) / power_range) * 100) if power_range > 0 else 100
    
    return {
        "limits": _current_limits,
        "saved_limits": _saved_limits,
        "enabled": _power_enabled,
        "eco_mode": any_below_default,
        "power_percentage": power_percentage,
        "total_current_watts": total_current,
        "total_max_watts": total_max,
        "hardware_limits": HARDWARE_LIMITS
    }


class PowerControlRequest(BaseModel):
    preset: Optional[str] = None  # "eco" or "stock"
    gpu_index: Optional[int] = None
    limit_watts: Optional[int] = None
    toggle: Optional[bool] = None  # Toggle between saved limits and stock


@router.post("/power-control")
async def set_power_control(request: PowerControlRequest):
    """Set power limits via preset, manual control, or toggle."""
    global _current_limits, _saved_limits, _power_enabled
    
    errors = []
    
    if request.toggle:
        # Toggle between saved limits and stock
        _power_enabled = not _power_enabled
        
        for gpu_idx, limits in HARDWARE_LIMITS.items():
            target = _saved_limits[gpu_idx] if _power_enabled else limits["default"]
            if set_gpu_power_limit(gpu_idx, target):
                _current_limits[gpu_idx] = target
            else:
                errors.append(f"GPU {gpu_idx}")
        
        message = f"Power limits {'enabled' if _power_enabled else 'disabled (stock)'}"
        
    elif request.preset:
        # Apply preset to all GPUs
        for gpu_idx, limits in HARDWARE_LIMITS.items():
            if request.preset == "eco":
                target = limits["eco"]
            elif request.preset == "stock":
                target = limits["default"]
                _power_enabled = False  # Disable when going to stock
            else:
                raise HTTPException(status_code=400, detail=f"Unknown preset: {request.preset}")
            
            if set_gpu_power_limit(gpu_idx, target):
                _current_limits[gpu_idx] = target
                if request.preset == "eco":
                    _saved_limits[gpu_idx] = target
                    _power_enabled = True
            else:
                errors.append(f"GPU {gpu_idx}")
        
        message = f"Applied '{request.preset}' preset"
        
    elif request.gpu_index is not None and request.limit_watts is not None:
        # Manual single-GPU control - also saves to _saved_limits
        if request.gpu_index not in HARDWARE_LIMITS:
            raise HTTPException(status_code=400, detail=f"Unknown GPU index: {request.gpu_index}")
        
        limits = HARDWARE_LIMITS[request.gpu_index]
        clamped = max(limits["min"], min(request.limit_watts, limits["max"]))
        
        if set_gpu_power_limit(request.gpu_index, clamped):
            _current_limits[request.gpu_index] = clamped
            _saved_limits[request.gpu_index] = clamped  # Save for toggle memory
            _power_enabled = True  # Mark as enabled since user set custom limit
            message = f"GPU {request.gpu_index} set to {clamped}W"
        else:
            errors.append(f"GPU {request.gpu_index}")
            message = f"Failed to set GPU {request.gpu_index}"
    else:
        raise HTTPException(status_code=400, detail="Must provide 'toggle', 'preset', or both 'gpu_index' and 'limit_watts'")
    
    # Determine eco mode state
    any_below_default = any(
        _current_limits.get(idx, limits["default"]) < limits["default"]
        for idx, limits in HARDWARE_LIMITS.items()
    )
    
    # Calculate power percentage
    total_min = sum(limits["min"] for limits in HARDWARE_LIMITS.values())
    total_max = sum(limits["max"] for limits in HARDWARE_LIMITS.values())
    total_current = sum(_current_limits.get(idx, limits["default"]) for idx, limits in HARDWARE_LIMITS.items())
    power_range = total_max - total_min
    power_percentage = round(((total_current - total_min) / power_range) * 100) if power_range > 0 else 100
    
    if errors:
        message += f" (Failed: {', '.join(errors)})"
    
    return {
        "success": len(errors) == 0,
        "message": message,
        "limits": _current_limits,
        "saved_limits": _saved_limits,
        "enabled": _power_enabled,
        "eco_mode": any_below_default,
        "power_percentage": power_percentage
    }


# --- GPU Scheduler Config Endpoints ---
# Config file path (in project root, read by Nextflow)
GPU_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / ".gpu_config.json"

# Default scheduler config
DEFAULT_SCHEDULER_CONFIG = {
    "global": {
        "busy_threshold": 0.5,       # 50% = GPU is busy
        "cooldown_ms": 10000,        # 10 seconds after assignment
        "enabled": True,             # Master switch for capacity lock
        "target_vram_fill": 0.75,    # Target VRAM fill before preferring another GPU
        "capacity_weight": 3.0,      # Weight for GPU capacity in scoring
        "emptiness_weight": 5.0,     # Weight for GPU emptiness in scoring
        "msa_concurrency_limit": 1,  # Max parallel MSA batch jobs
    },
    "overrides": {}  # Per-GPU: {"0": {"force_available": false, "threshold": null}}
}


class SchedulerGlobalConfig(BaseModel):
    """Global scheduler settings."""
    busy_threshold: float = 0.5           # 0.0-1.0
    cooldown_ms: int = 10000
    enabled: bool = True
    target_vram_fill: float = 0.75        # Target fill % before preferring another GPU
    capacity_weight: float = 3.0          # Larger = prefer bigger GPUs more
    emptiness_weight: float = 5.0         # Larger = prefer emptier GPUs more
    msa_concurrency_limit: int = 1        # Max parallel MSA jobs


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


def read_scheduler_config() -> Dict[str, Any]:
    """Read scheduler config from file, or return defaults."""
    if GPU_CONFIG_PATH.exists():
        try:
            with open(GPU_CONFIG_PATH, "r") as f:
                config = json.load(f)
                # Merge with defaults to ensure all keys exist
                return {
                    "global": {**DEFAULT_SCHEDULER_CONFIG["global"], **config.get("global", {})},
                    "overrides": config.get("overrides", {})
                }
        except Exception as e:
            print(f"Error reading GPU config: {e}")
    return DEFAULT_SCHEDULER_CONFIG.copy()


def write_scheduler_config(config: Dict[str, Any]) -> bool:
    """Write scheduler config to file."""
    try:
        with open(GPU_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error writing GPU config: {e}")
        return False


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
        "enabled": global_config.enabled,
        "target_vram_fill": max(0.5, min(0.95, global_config.target_vram_fill)),
        "capacity_weight": max(0.0, min(10.0, global_config.capacity_weight)),
        "emptiness_weight": max(0.0, min(10.0, global_config.emptiness_weight)),
        "msa_concurrency_limit": max(1, min(4, global_config.msa_concurrency_limit)),
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
        gpu_id: GPU index (0-3)
    
    Example: POST /gpu/workflow-pins/boltz/gpu/2
             → All Boltz jobs will run on GPU 2
    """
    if gpu_id < 0 or gpu_id > 3:
        raise HTTPException(status_code=400, detail=f"Invalid GPU index: {gpu_id}. Must be 0-3.")
    
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
        gpu_id: GPU index to lock (0-3)
    """
    if gpu_id < 0 or gpu_id > 3:
        raise HTTPException(status_code=400, detail=f"Invalid GPU index: {gpu_id}. Must be 0-3.")
    
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
    from database import async_session, Job
    from sqlalchemy import select
    from datetime import datetime
    from services.nextflow import launch_nextflow_job
    
    logger = logging.getLogger("api.gpu")
    
    async with async_session() as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        if job.queue_status != "queued":
            raise HTTPException(
                status_code=400, 
                detail=f"Job must be queued to force-run (current: {job.queue_status})"
            )
        
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
        
        # Update job status
        job.queue_status = "running"
        job.assigned_gpu = gpu_id
        job.started_at = datetime.utcnow()
        await session.commit()
        
        # Build params with gpu_id
        import json
        params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
        params["gpu_id"] = gpu_id
        
        logger.warning(f"[FORCE RUN] User forced {job.name} to GPU {gpu_id}")
        
        # Launch the job
        import asyncio
        asyncio.create_task(launch_nextflow_job(
            job_id=job.id,
            model_id=job.model_id,
            mode=params.get("mode", "default"),
            params=params,
            output_dir=job.output_dir
        ))
        
        return {
            "success": True,
            "message": f"Force-launched {job.name} on GPU {gpu_id}",
            "job_id": job_id,
            "gpu_id": gpu_id
        }


class ConcurrencyLimitRequest(BaseModel):
    """Request to set concurrency limit for a model type."""
    model_type: str  # e.g., "fampnn", "rfantibody", "boltz"
    limit: Optional[int] = None  # None = auto (no limit)


@router.get("/concurrency-limits")
async def get_concurrency_limits():
    """
    [DEBUG] Get current concurrency limits for all model types.
    """
    config = read_scheduler_config()
    return {
        "concurrency_limits": config.get("concurrency_limits", {}),
        "description": "Model type -> max concurrent jobs (null = auto/unlimited)"
    }


@router.put("/concurrency-limits")
async def set_concurrency_limit(request: ConcurrencyLimitRequest):
    """
    [DEBUG] Set concurrency limit for a specific model type.
    
    Limits how many jobs of this type can run concurrently.
    Set to null to remove the limit (auto mode).
    """
    import logging
    logger = logging.getLogger("api.gpu")
    
    config = read_scheduler_config()
    
    if "concurrency_limits" not in config:
        config["concurrency_limits"] = {}
    
    old_limit = config["concurrency_limits"].get(request.model_type)
    
    if request.limit is None:
        # Remove the limit
        config["concurrency_limits"].pop(request.model_type, None)
        logger.info(f"[CONCURRENCY] Removed limit for {request.model_type}")
    else:
        config["concurrency_limits"][request.model_type] = request.limit
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

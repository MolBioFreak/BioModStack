"""
System monitoring API router - GPU, CPU, RAM statistics.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
import subprocess

router = APIRouter()

# Hardware power limits per GPU: (min, default, max, eco_preset)
# Values from nvidia-smi -q -d POWER
HARDWARE_LIMITS = {
    0: {"min": 400, "default": 575, "max": 600, "eco": 500, "name": "RTX 5090"},
    1: {"min": 150, "default": 180, "max": 200, "eco": 165, "name": "RTX 5060 Ti"},
    2: {"min": 100, "default": 370, "max": 380, "eco": 300, "name": "RTX 3090"},
    3: {"min": 100, "default": 390, "max": 480, "eco": 300, "name": "RTX 3090"},
}

# Track current power control state (in-memory, resets on restart)
_current_limits = {gpu_idx: limits["default"] for gpu_idx, limits in HARDWARE_LIMITS.items()}


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


class RAMStatus(BaseModel):
    """RAM status information."""
    total_gb: float
    used_gb: float
    available_gb: float
    utilization: float  # percentage


class SystemStatusResponse(BaseModel):
    """Complete system status response."""
    gpus: List[GPUStatusEnhanced]
    cpu: CPUStatus
    ram: RAMStatus
    timestamp: datetime


def get_gpu_stats() -> List[GPUStatusEnhanced]:
    """Get enhanced GPU statistics using pynvml."""
    try:
        import pynvml
        pynvml.nvmlInit()
        
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []
        
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
            
            gpus.append(GPUStatusEnhanced(
                index=i,
                name=name,
                utilization=utilization.gpu,
                memory_utilization=utilization.memory,
                memory_used_mb=memory.used // (1024 * 1024),
                memory_total_mb=memory.total // (1024 * 1024),
                power_draw_w=round(power_draw, 1),
                power_limit_w=round(power_limit, 1),
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
    
    return CPUStatus(
        name=cpu_name,
        cores_physical=psutil.cpu_count(logical=False) or 0,
        cores_logical=psutil.cpu_count(logical=True) or 0,
        utilization=psutil.cpu_percent(interval=0.1),
        per_core_utilization=psutil.cpu_percent(interval=0.1, percpu=True),
        frequency_current_mhz=freq.current if freq else 0,
        frequency_max_mhz=freq.max if freq else 0
    )


def get_ram_stats() -> RAMStatus:
    """Get RAM statistics using psutil."""
    import psutil
    
    mem = psutil.virtual_memory()
    
    return RAMStatus(
        total_gb=round(mem.total / (1024**3), 1),
        used_gb=round(mem.used / (1024**3), 1),
        available_gb=round(mem.available / (1024**3), 1),
        utilization=mem.percent
    )


@router.get("/status")
async def get_system_status():
    """Get complete system status including GPUs, CPU, and RAM."""
    return SystemStatusResponse(
        gpus=get_gpu_stats(),
        cpu=get_cpu_stats(),
        ram=get_ram_stats(),
        timestamp=datetime.utcnow()
    )


@router.get("/gpus")
async def get_gpus_only():
    """Get GPU status only (for lighter polling)."""
    return {"gpus": get_gpu_stats(), "timestamp": datetime.utcnow()}


@router.get("/cpu")
async def get_cpu_only():
    """Get CPU status only."""
    return {"cpu": get_cpu_stats(), "timestamp": datetime.utcnow()}


@router.get("/ram")
async def get_ram_only():
    """Get RAM status only."""
    return {"ram": get_ram_stats(), "timestamp": datetime.utcnow()}


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


@router.get("/power-profile")
async def get_power_profile() -> PowerProfileResponse:
    """Get current power profile state."""
    return PowerProfileResponse(
        eco_mode=_eco_mode_enabled,
        message="Eco mode active" if _eco_mode_enabled else "Default power limits"
    )


@router.post("/power-profile")
async def set_power_profile(enable_eco: bool) -> PowerProfileResponse:
    """Toggle eco mode power limits."""
    global _eco_mode_enabled

    errors = []
    # GPU index -> (eco_limit_watts, default_limit_watts)
    for gpu_index, (eco_watts, default_watts) in POWER_PROFILES.items():
        target_watts = eco_watts if enable_eco else default_watts
        if not set_gpu_power_limit(gpu_index, target_watts):
            errors.append(f"GPU {gpu_index}")

    message_suffix = ""
    if errors:
        # Log failure but DO NOT crash the UI - keep button in sync with intended state
        print(f"ERROR: Failed to set power limits for: {', '.join(errors)}")
        message_suffix = f" (Failed on: {', '.join(errors)})"
    
    # Always update state to keep UI responsive
    _eco_mode_enabled = enable_eco

    return PowerProfileResponse(
        eco_mode=_eco_mode_enabled,
        message=f"Eco mode {'enabled' if enable_eco else 'disabled'} - limits applied{message_suffix}"
    )


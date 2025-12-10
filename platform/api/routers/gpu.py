"""
System monitoring API router - GPU, CPU, RAM statistics.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
import subprocess

router = APIRouter()

# Power profile configuration
# GPU index -> (eco_limit_watts, default_limit_watts)
POWER_PROFILES = {
    0: (500, 575),   # RTX 5090: 500W eco, 575W default
    2: (300, 370),   # RTX 3090: 300W eco, 370W default
    3: (300, 390),   # RTX 3090: 300W eco, 390W default
    # GPU 1 (5060 Ti) intentionally omitted - no limit changes
}

# Track current eco mode state (in-memory, resets on restart)
_eco_mode_enabled = False


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
    for gpu_index, (eco_watts, default_watts) in POWER_PROFILES.items():
        target_watts = eco_watts if enable_eco else default_watts
        if not set_gpu_power_limit(gpu_index, target_watts):
            errors.append(f"GPU {gpu_index}")

    # Determine message based on success/failure
    if errors:
        # In container environments without privileges, we likely can't set limits.
        # We'll enable "Soft Eco Mode" (UI state only) to satisfy user preference for the display.
        print(f"WARNING: Failed to set hardware power limits for {', '.join(errors)}. Running in simulation/soft mode.")
        message = f"Soft Eco Mode {'enabled' if enable_eco else 'disabled'} (Hardware limits restricted)"
    else:
        message = f"Eco mode {'enabled' if enable_eco else 'disabled'} - limits applied"

    # Always update the state so the UI toggles
    _eco_mode_enabled = enable_eco

    return PowerProfileResponse(
        eco_mode=_eco_mode_enabled,
        message=message
    )


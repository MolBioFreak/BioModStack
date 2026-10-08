"""
GPU monitoring API router.
"""

from fastapi import APIRouter
from datetime import datetime
from typing import List

from schemas import GPUStatus, GPUStatusResponse

router = APIRouter()


def get_gpu_stats() -> List[GPUStatus]:
    """Get current GPU statistics using pynvml."""
    try:
        import pynvml
        pynvml.nvmlInit()
        
        device_count = pynvml.nvmlDeviceGetCount()
        gpus = []
        
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')
            
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            
            gpus.append(GPUStatus(
                index=i,
                name=name,
                utilization=utilization.gpu,
                memory_used_mb=memory.used // (1024 * 1024),
                memory_total_mb=memory.total // (1024 * 1024),
                temperature=temperature,
                current_task=None  # TODO: Track which job is using this GPU
            ))
        
        pynvml.nvmlShutdown()
        return gpus
        
    except Exception as e:
        # Return empty list if NVML fails (e.g., no NVIDIA driver)
        return []


@router.get("/status", response_model=GPUStatusResponse)
async def get_gpu_status():
    """Get current status of all GPUs."""
    gpus = get_gpu_stats()
    return GPUStatusResponse(
        gpus=gpus,
        timestamp=datetime.utcnow()
    )

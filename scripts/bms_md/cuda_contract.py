from __future__ import annotations

import os
from typing import Any, Mapping

from .contract import normalize_job_config


CUDA_CONTRACT_ERROR = "MD_CUDA_CONTRACT_VIOLATION"


class CudaContractError(RuntimeError):
    code = CUDA_CONTRACT_ERROR


def assert_single_cuda_device(
    job_config: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the engine-neutral one-replica/one-CUDA-device contract.

    The scheduler identity may be a local index, PCI/UUID identity, or a cloud
    provider allocation token. Inside the container the sole visible device is
    always addressed as logical device zero.
    """

    config = normalize_job_config(job_config)
    execution = config["execution"]
    if execution["gpu_offload"] not in {"full", "full_forces"}:
        raise CudaContractError(
            f"{CUDA_CONTRACT_ERROR}: dynamics replicas require execution.gpu_offload=full "
            "or full_forces"
        )
    if execution["gpu_id"] != "0":
        raise CudaContractError(
            f"{CUDA_CONTRACT_ERROR}: the sole container-visible CUDA device must be logical index 0"
        )

    env = os.environ if environ is None else environ
    raw_visible = str(env.get("CUDA_VISIBLE_DEVICES") or "").strip()
    visible = [value.strip() for value in raw_visible.split(",") if value.strip()]
    if len(visible) != 1:
        raise CudaContractError(
            f"{CUDA_CONTRACT_ERROR}: exactly one CUDA device must be visible to each replica; "
            f"observed {len(visible)}"
        )

    scheduler_device = str(execution.get("scheduler_gpu_id") or visible[0])
    return {
        "schema": "bms.md.cuda-allocation.v1",
        "request": {"vendor": "nvidia", "api": "cuda", "count": 1},
        "scheduler_device_id": scheduler_device,
        "visible_device_token": visible[0],
        "container_device_index": "0",
    }

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from paths import get_container_path
from services.gpu_config import read_scheduler_config

logger = logging.getLogger(__name__)

DEFAULT_ANARCII_MODE = "auto"
DEFAULT_ANARCII_BATCH_SIZE = 500
DEFAULT_ANARCII_CPU_THREADS = 24


@dataclass(frozen=True)
class ANARCIIRuntime:
    mode: str
    gpu_id: Optional[int]
    reason: str
    container_path: Path


@dataclass(frozen=True)
class HostGPU:
    index: int
    name: str
    compute_capability: str
    sm_token: str


def _normalize_mode(value: object) -> str:
    normalized = str(value or DEFAULT_ANARCII_MODE).strip().lower()
    if normalized in {"gpu", "cpu"}:
        return normalized
    return DEFAULT_ANARCII_MODE


def _coerce_int(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_gpu_id_iterable(raw_value: object) -> set[int]:
    if raw_value in (None, "", []):
        return set()
    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = [part.strip() for part in str(raw_value).split(",") if part.strip()]

    normalized: set[int] = set()
    for value in values:
        parsed = _coerce_int(value)
        if parsed is not None:
            normalized.add(parsed)
    return normalized


def get_default_anarcii_mode() -> str:
    return _normalize_mode(os.getenv("BMS_ANARCII_EXECUTION_MODE"))


def get_default_anarcii_gpu_id() -> Optional[int]:
    return _coerce_int(os.getenv("BMS_ANARCII_GPU_ID"))


def get_default_anarcii_batch_size() -> int:
    value = _coerce_int(os.getenv("BMS_ANARCII_BATCH_SIZE"))
    if value is None:
        return DEFAULT_ANARCII_BATCH_SIZE
    return max(1, value)


def get_default_anarcii_cpu_threads() -> int:
    value = _coerce_int(os.getenv("BMS_ANARCII_CPU_THREADS"))
    if value is None:
        return DEFAULT_ANARCII_CPU_THREADS
    return max(1, value)


def _compute_capability_to_sm_token(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    major, _, minor = normalized.partition(".")
    if not major.isdigit():
        return ""
    minor_digits = "".join(ch for ch in minor if ch.isdigit()) or "0"
    return f"sm_{major}{minor_digits}"


def _scheduler_disabled_gpu_ids() -> set[int]:
    config = read_scheduler_config() or {}
    overrides = config.get("overrides", {}) if isinstance(config, dict) else {}
    disabled: set[int] = set()
    if not isinstance(overrides, dict):
        return disabled
    for raw_gpu_id, override in overrides.items():
        if not isinstance(override, dict) or not override.get("disabled", False):
            continue
        parsed = _coerce_int(raw_gpu_id)
        if parsed is not None:
            disabled.add(parsed)
    return disabled


@lru_cache(maxsize=1)
def get_host_gpus() -> tuple[HostGPU, ...]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,compute_cap",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        logger.warning(f"[ANARCII] Failed to query host GPUs: {exc}")
        return ()

    if result.returncode != 0:
        logger.warning(f"[ANARCII] nvidia-smi failed: {(result.stderr or '').strip()[:300]}")
        return ()

    gpus: list[HostGPU] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        gpu_index = _coerce_int(parts[0])
        if gpu_index is None:
            continue
        compute_capability = parts[3]
        sm_token = _compute_capability_to_sm_token(compute_capability)
        if not sm_token:
            continue
        gpus.append(
            HostGPU(
                index=gpu_index,
                name=parts[1],
                compute_capability=compute_capability,
                sm_token=sm_token,
            )
        )
    return tuple(gpus)


@lru_cache(maxsize=8)
def get_container_supported_sm_tokens(container_path_str: str) -> tuple[str, ...]:
    container_path = Path(container_path_str)
    if not container_path.exists():
        return ()

    try:
        result = subprocess.run(
            [
                "apptainer",
                "exec",
                "--nv",
                str(container_path),
                "python3",
                "-c",
                "import json, torch; print(json.dumps(torch.cuda.get_arch_list()))",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        logger.warning(f"[ANARCII] Failed to inspect container CUDA arch list: {exc}")
        return ()

    if result.returncode != 0:
        logger.warning(
            f"[ANARCII] Could not read supported CUDA arches from {container_path.name}: "
            f"{(result.stderr or '').strip()[:300]}"
        )
        return ()

    try:
        payload = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError as exc:
        logger.warning(f"[ANARCII] Invalid CUDA arch list from {container_path.name}: {exc}")
        return ()

    if not isinstance(payload, list):
        return ()

    supported = sorted({str(token).strip() for token in payload if str(token).strip().startswith("sm_")})
    return tuple(supported)


def resolve_anarcii_runtime(
    requested_mode: object = None,
    preferred_gpu: object = None,
    excluded_gpu_ids: object = None,
    container_path: Optional[Path] = None,
) -> ANARCIIRuntime:
    mode = _normalize_mode(requested_mode or get_default_anarcii_mode())
    preferred_gpu_id = _coerce_int(preferred_gpu if preferred_gpu is not None else get_default_anarcii_gpu_id())
    container = container_path or get_container_path("antibody_tools.sif")

    if mode == "cpu":
        return ANARCIIRuntime(mode="cpu", gpu_id=None, reason="requested cpu mode", container_path=container)

    if not container.exists():
        return ANARCIIRuntime(mode="cpu", gpu_id=None, reason="container not found", container_path=container)

    supported_sms = set(get_container_supported_sm_tokens(str(container)))
    if not supported_sms:
        return ANARCIIRuntime(mode="cpu", gpu_id=None, reason="container cuda arch list unavailable", container_path=container)

    disabled_gpu_ids = _scheduler_disabled_gpu_ids()
    excluded = disabled_gpu_ids | _normalize_gpu_id_iterable(excluded_gpu_ids)
    host_gpus = list(get_host_gpus())
    compatible_gpus = [gpu for gpu in host_gpus if gpu.sm_token in supported_sms and gpu.index not in excluded]

    if preferred_gpu_id is not None:
        preferred = next((gpu for gpu in compatible_gpus if gpu.index == preferred_gpu_id), None)
        if preferred is not None:
            return ANARCIIRuntime(
                mode="gpu",
                gpu_id=preferred.index,
                reason=f"preferred gpu {preferred.index} ({preferred.name}, {preferred.sm_token}) is compatible",
                container_path=container,
            )

    if compatible_gpus:
        selected = compatible_gpus[0]
        reason = f"selected compatible gpu {selected.index} ({selected.name}, {selected.sm_token})"
        if preferred_gpu_id is not None:
            reason = (
                f"preferred gpu {preferred_gpu_id} unavailable or incompatible; "
                f"{reason}"
            )
        return ANARCIIRuntime(mode="gpu", gpu_id=selected.index, reason=reason, container_path=container)

    requested_reason = "no compatible enabled GPU found"
    if preferred_gpu_id is not None:
        requested_reason = f"preferred gpu {preferred_gpu_id} unavailable or incompatible; {requested_reason}"
    return ANARCIIRuntime(mode="cpu", gpu_id=None, reason=requested_reason, container_path=container)


def build_apptainer_exec_command(runtime: ANARCIIRuntime, inner_cmd: Iterable[str]) -> list[str]:
    cmd = ["apptainer", "exec"]
    if runtime.mode == "gpu" and runtime.gpu_id is not None:
        cmd.extend(
            [
                "--nv",
                "--env",
                "CUDA_DEVICE_ORDER=PCI_BUS_ID",
                "--env",
                f"CUDA_VISIBLE_DEVICES={runtime.gpu_id}",
            ]
        )
    cmd.append(str(runtime.container_path))
    cmd.extend(str(item) for item in inner_cmd)
    return cmd

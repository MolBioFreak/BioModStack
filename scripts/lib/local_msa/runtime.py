from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from local_msa.providers.local_mmseqs import _load_legacy_run_local_msa_module  # noqa: E402
from local_msa_runtime import (  # noqa: E402
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    DEFAULT_MSA_SERVER_STATUS_URL,
    filter_matching_servers,
    is_isolated_task_runtime,
    is_matching_gpuserver_process,
    normalize_gpuserver_db_load_mode,
    normalize_gpuserver_startup_wait,
    normalize_gpuserver_wait_timeout,
    query_host_gpuserver_status,
)

_inspect_mmseqs_runtime_impl: Callable[..., Dict[str, Any]] | None = None


def parse_gpu_csv(csv_value: Optional[str]) -> Optional[List[int]]:
    """Parse comma-separated GPU IDs into an ordered unique list."""
    if not csv_value:
        return None
    gpu_ids: List[int] = []
    seen: set[int] = set()
    for token in csv_value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            gpu_id = int(token)
        except ValueError as exc:
            raise ValueError(f"Invalid GPU id in list: {token}") from exc
        if gpu_id in seen:
            continue
        seen.add(gpu_id)
        gpu_ids.append(gpu_id)
    return gpu_ids if gpu_ids else None


def _load_legacy_inspect_mmseqs_runtime() -> Callable[..., Dict[str, Any]]:
    global _inspect_mmseqs_runtime_impl
    if _inspect_mmseqs_runtime_impl is not None:
        return _inspect_mmseqs_runtime_impl
    module = _load_legacy_run_local_msa_module()
    impl = getattr(module, "inspect_mmseqs_runtime", None)
    if impl is None:
        raise RuntimeError("Legacy local MSA implementation is missing inspect_mmseqs_runtime")
    _inspect_mmseqs_runtime_impl = impl
    return _inspect_mmseqs_runtime_impl


def inspect_mmseqs_runtime(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return _load_legacy_inspect_mmseqs_runtime()(*args, **kwargs)


__all__ = [
    "DEFAULT_GPUSERVER_DB_LOAD_MODE",
    "DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS",
    "DEFAULT_GPUSERVER_WAIT_TIMEOUT",
    "DEFAULT_MSA_SERVER_STATUS_URL",
    "filter_matching_servers",
    "inspect_mmseqs_runtime",
    "is_isolated_task_runtime",
    "is_matching_gpuserver_process",
    "normalize_gpuserver_db_load_mode",
    "normalize_gpuserver_startup_wait",
    "normalize_gpuserver_wait_timeout",
    "parse_gpu_csv",
    "query_host_gpuserver_status",
]

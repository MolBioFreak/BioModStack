from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

DEFAULT_GPUSERVER_DB_LOAD_MODE = 2
GPUSERVER_DB_LOAD_MODE_CHOICES = range(4)
DEFAULT_GPUSERVER_WAIT_TIMEOUT = 120
DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS = 5.0
DEFAULT_MSA_SERVER_STATUS_URL = (
    os.getenv("BMS_MSA_SERVER_STATUS_URL")
    or "http://127.0.0.1:18000/api/msa/server/status"
)

DB_ALIAS_BY_NAME = {
    "uniref30_2302_db": "uniref",
    "colabfold_envdb_202108_db": "envdb",
}


def normalize_gpuserver_db_load_mode(value: Optional[int]) -> int:
    normalized = DEFAULT_GPUSERVER_DB_LOAD_MODE if value is None else int(value)
    if normalized not in GPUSERVER_DB_LOAD_MODE_CHOICES:
        raise ValueError("gpu_server_db_load_mode must be in MMseqs db-load-mode range 0..3")
    return normalized


def normalize_gpuserver_wait_timeout(value: Optional[int]) -> int:
    normalized = DEFAULT_GPUSERVER_WAIT_TIMEOUT if value is None else int(value)
    if normalized < -1:
        raise ValueError("gpu_server_wait_timeout must be -1 (infinite), 0, or a positive integer")
    return normalized


def normalize_gpuserver_startup_wait(value: Optional[float]) -> float:
    normalized = (
        DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS if value is None else float(value)
    )
    if normalized < 0:
        raise ValueError("gpu_server_startup_wait must be non-negative")
    return normalized


def resolve_protenix_local_gpu_server_mode(value: Optional[str]) -> Dict[str, str]:
    requested = str(value or "persistent").strip().lower() or "persistent"
    effective = "off" if requested in {"auto", "persistent"} else requested
    return {
        "requested_gpu_server_mode": requested,
        "effective_gpu_server_mode": effective,
    }


def is_matching_gpuserver_process(
    pid: int,
    target_db: Path,
    *,
    pid_is_alive: Callable[[int], bool],
    read_proc_cmdline: Callable[[int], str],
) -> bool:
    if not pid_is_alive(pid):
        return False
    cmdline = read_proc_cmdline(pid)
    if not cmdline:
        return False
    target_db_text = str(target_db)
    return "gpuserver" in cmdline and target_db_text in cmdline


def is_isolated_task_runtime(env: Optional[Mapping[str, str]] = None) -> bool:
    current_env = dict(env or os.environ)
    override = str(current_env.get("BMS_MSA_TASK_RUNTIME", "")).strip().lower()
    if override in {"host", "local"}:
        return False
    if override in {"task", "isolated", "container"}:
        return True
    return any(
        str(current_env.get(key, "")).strip()
        for key in (
            "APPTAINER_CONTAINER",
            "APPTAINER_NAME",
            "SINGULARITY_CONTAINER",
            "SINGULARITY_NAME",
        )
    )


def _coerce_server_gpu_id(server: Dict[str, Any]) -> Optional[int]:
    for candidate in (server.get("gpu_id"), server.get("cuda_visible_devices")):
        if candidate in (None, ""):
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def db_alias_for_target(target_db: Path) -> Optional[str]:
    return DB_ALIAS_BY_NAME.get(target_db.name)


def filter_matching_servers(
    servers: Any,
    *,
    target_db: Path,
    gpu_id: Optional[int],
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
) -> list[Dict[str, Any]]:
    if not isinstance(servers, list):
        return []
    expected_alias = db_alias_for_target(target_db)
    expected_target_db = str(target_db)
    expected_max_seqs = int(max(1, max_seqs))
    expected_prefilter_mode = int(prefilter_mode)
    expected_db_load_mode = normalize_gpuserver_db_load_mode(db_load_mode)
    expected_gpu_id = None if gpu_id is None else int(gpu_id)

    matching: list[Dict[str, Any]] = []
    for raw_server in servers:
        if not isinstance(raw_server, dict):
            continue
        if not raw_server.get("running"):
            continue
        server_target_db = str(raw_server.get("target_db") or "")
        server_alias = raw_server.get("db_alias") or raw_server.get("alias")
        server_alias_text = str(server_alias or "")
        if expected_alias and server_alias_text not in {expected_alias, target_db.name, ""}:
            continue
        if server_target_db and server_target_db != expected_target_db:
            if Path(server_target_db).name != target_db.name:
                continue
        try:
            server_max_seqs = int(raw_server.get("max_seqs"))
            server_prefilter_mode = int(raw_server.get("prefilter_mode"))
            server_db_load_mode = int(raw_server.get("db_load_mode"))
        except (TypeError, ValueError):
            continue
        if server_max_seqs != expected_max_seqs:
            continue
        if server_prefilter_mode != expected_prefilter_mode:
            continue
        if server_db_load_mode != expected_db_load_mode:
            continue
        server_gpu_id = _coerce_server_gpu_id(raw_server)
        if expected_gpu_id is not None and server_gpu_id != expected_gpu_id:
            continue
        matching.append(raw_server)
    return matching


def query_host_gpuserver_status(
    *,
    target_db: Path,
    gpu_id: Optional[int],
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
    include_envdb: Optional[bool] = None,
    status_url: str = DEFAULT_MSA_SERVER_STATUS_URL,
    timeout_seconds: float = 2.0,
) -> Dict[str, Any]:
    requested_contract = {
        "gpu_id": None if gpu_id is None else int(gpu_id),
        "max_seqs": int(max(1, max_seqs)),
        "prefilter_mode": int(prefilter_mode),
        "db_load_mode": normalize_gpuserver_db_load_mode(db_load_mode),
        "target_db": str(target_db),
        "db_alias": db_alias_for_target(target_db),
    }
    params: Dict[str, Any] = {
        "max_seqs": requested_contract["max_seqs"],
        "prefilter_mode": requested_contract["prefilter_mode"],
        "db_load_mode": requested_contract["db_load_mode"],
    }
    if requested_contract["gpu_id"] is not None:
        params["gpu_id"] = requested_contract["gpu_id"]
    if include_envdb is not None:
        params["include_envdb"] = str(bool(include_envdb)).lower()
    url = f"{status_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=max(0.1, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        return {
            "checked": False,
            "ready": False,
            "error": str(exc),
            "status_url": status_url,
            "requested_contract": requested_contract,
            "matching_servers": [],
            "matching_server": None,
        }

    matching_servers = filter_matching_servers(
        payload.get("matching_servers") or payload.get("servers"),
        target_db=target_db,
        gpu_id=gpu_id,
        max_seqs=max_seqs,
        prefilter_mode=prefilter_mode,
        db_load_mode=db_load_mode,
    )
    return {
        "checked": True,
        "ready": bool(matching_servers),
        "status_url": status_url,
        "requested_contract": requested_contract,
        "matching_servers": matching_servers,
        "matching_server": matching_servers[0] if matching_servers else None,
        "payload": payload,
    }

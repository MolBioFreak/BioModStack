"""
Persistent MMseqs2 gpuserver management for local MSA acceleration.

This mirrors the keying/layout used by scripts/run_local_msa.py so servers
started here are reused by workflow MSA jobs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time

from paths import get_colabfold_db, get_msa_cache_dir
from services.gpu_config import read_scheduler_config


DB_ALIASES: Dict[str, str] = {
    "uniref": "uniref30_2302_db",
    "envdb": "colabfold_envdb_202108_db",
}


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _is_matching_gpuserver_process(pid: int, target_db: Path) -> bool:
    if not _pid_is_alive(pid):
        return False
    cmdline = _read_proc_cmdline(pid)
    if not cmdline:
        return True
    return "gpuserver" in cmdline and str(target_db) in cmdline


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _tail_text_file(path: Path, max_chars: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text if len(text) <= max_chars else text[-max_chars:]


def _gpuserver_runtime_root() -> Path:
    override = os.getenv("BMS_MMSEQS_GPUSERVER_DIR")
    if override:
        root = Path(override)
    else:
        root = get_msa_cache_dir() / ".gpuserver"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_mmseqs_gpu_binary(db_root: Path) -> Path:
    candidates = [
        db_root / "mmseqs-gpu-blackwell" / "bin" / "mmseqs",
        db_root / "mmseqs-gpu" / "bin" / "mmseqs",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No MMseqs GPU binary found under {db_root} "
        "(expected mmseqs-gpu-blackwell/bin/mmseqs or mmseqs-gpu/bin/mmseqs)"
    )


def _list_gpu_indices() -> List[int]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    gpu_ids: List[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            gpu_ids.append(int(line))
        except ValueError:
            continue
    return sorted(set(gpu_ids))


def resolve_msa_gpu_id(requested_gpu_id: Optional[int] = None) -> int:
    """
    Resolve target GPU for MSA server startup.

    Priority:
    1) Explicit request
    2) Scheduler global.msa_preferred_gpu_ids
    3) GPU 1 (5060 Ti on this workstation, if available)
    4) First non-disabled GPU
    """
    available = _list_gpu_indices()
    if not available:
        raise RuntimeError("No NVIDIA GPUs detected via nvidia-smi")

    scheduler_cfg = read_scheduler_config() or {}
    global_cfg = scheduler_cfg.get("global", {}) if isinstance(scheduler_cfg, dict) else {}
    overrides = scheduler_cfg.get("overrides", {}) if isinstance(scheduler_cfg, dict) else {}

    disabled: set[int] = set()
    if isinstance(overrides, dict):
        for gpu_key, override in overrides.items():
            if not isinstance(override, dict) or not override.get("disabled", False):
                continue
            try:
                disabled.add(int(gpu_key))
            except (TypeError, ValueError):
                continue

    if requested_gpu_id is not None:
        if requested_gpu_id not in available:
            raise RuntimeError(f"Requested GPU {requested_gpu_id} is not available")
        if requested_gpu_id in disabled:
            raise RuntimeError(f"Requested GPU {requested_gpu_id} is disabled in scheduler config")
        return requested_gpu_id

    preferred: List[int] = []
    raw_preferred = global_cfg.get("msa_preferred_gpu_ids")
    if isinstance(raw_preferred, list):
        for gpu_id in raw_preferred:
            try:
                preferred.append(int(gpu_id))
            except (TypeError, ValueError):
                continue
    for gpu_id in preferred:
        if gpu_id in available and gpu_id not in disabled:
            return gpu_id

    if 1 in available and 1 not in disabled:
        return 1

    for gpu_id in available:
        if gpu_id not in disabled:
            return gpu_id

    raise RuntimeError("All detected GPUs are disabled in scheduler config")


def _gpuserver_key(
    mmseqs_bin: Path,
    target_db: Path,
    cuda_visible_devices: str,
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
) -> str:
    key_material = "|".join(
        [
            str(mmseqs_bin.resolve()),
            str(target_db.resolve()),
            str(cuda_visible_devices),
            str(int(max(1, max_seqs))),
            str(int(prefilter_mode)),
            str(int(db_load_mode)),
        ]
    )
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]


def _db_alias_from_path(target_db: str) -> str:
    p = Path(target_db)
    for alias, db_name in DB_ALIASES.items():
        if p.name == db_name:
            return alias
    return "custom"


def _read_server_jsons() -> List[Path]:
    root = _gpuserver_runtime_root()
    return sorted(root.glob("*/server.json"))


def list_servers() -> List[Dict[str, Any]]:
    servers: List[Dict[str, Any]] = []
    for meta_path in _read_server_jsons():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = int(meta.get("pid", -1))
        target_db = Path(str(meta.get("target_db", "")))
        running = _is_matching_gpuserver_process(pid, target_db)
        servers.append(
            {
                **meta,
                "db_alias": _db_alias_from_path(str(target_db)),
                "running": running,
                "meta_path": str(meta_path),
            }
        )
    return servers


def ensure_server_for_db(
    db_alias: str,
    gpu_id: int,
    max_seqs: int = 300,
    prefilter_mode: int = 1,
    db_load_mode: int = 0,
    startup_wait_seconds: float = 1.0,
) -> Dict[str, Any]:
    if db_alias not in DB_ALIASES:
        raise ValueError(f"Unknown db alias '{db_alias}'. Expected one of: {sorted(DB_ALIASES)}")

    db_root = get_colabfold_db()
    mmseqs_bin = _resolve_mmseqs_gpu_binary(db_root)
    target_db = db_root / DB_ALIASES[db_alias]
    if not target_db.exists():
        raise FileNotFoundError(f"Target DB not found: {target_db}")
    if not Path(str(target_db) + ".dbtype").exists():
        raise FileNotFoundError(f"Target DB type file missing: {target_db}.dbtype")

    env = os.environ.copy()
    # Keep CUDA ordinal mapping aligned with nvidia-smi GPU indices.
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    runtime_root = _gpuserver_runtime_root()
    key = _gpuserver_key(
        mmseqs_bin=mmseqs_bin,
        target_db=target_db,
        cuda_visible_devices=env["CUDA_VISIBLE_DEVICES"],
        max_seqs=max_seqs,
        prefilter_mode=prefilter_mode,
        db_load_mode=db_load_mode,
    )
    server_dir = runtime_root / key
    server_dir.mkdir(parents=True, exist_ok=True)

    lock_path = server_dir / "server.lock"
    meta_path = server_dir / "server.json"
    log_path = server_dir / "gpuserver.log"

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                existing = None
            if existing:
                pid = int(existing.get("pid", -1))
                if _is_matching_gpuserver_process(pid, target_db):
                    return {
                        **existing,
                        "db_alias": db_alias,
                        "running": True,
                        "reused": True,
                        "key": key,
                        "meta_path": str(meta_path),
                    }

        cmd = [
            str(mmseqs_bin),
            "gpuserver",
            str(target_db),
            "--max-seqs", str(max(1, int(max_seqs))),
            "--prefilter-mode", str(int(prefilter_mode)),
            "--db-load-mode", str(int(db_load_mode)),
        ]

        with open(log_path, "a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

        time.sleep(max(0.0, float(startup_wait_seconds)))
        early_exit = proc.poll()
        if early_exit is not None:
            tail = _tail_text_file(log_path)
            raise RuntimeError(
                f"MMseqs2 gpuserver exited early for {db_alias} (code={early_exit}). "
                f"Log: {log_path}\n{tail}"
            )

        metadata = {
            "pid": proc.pid,
            "key": key,
            "target_db": str(target_db),
            "cuda_visible_devices": env["CUDA_VISIBLE_DEVICES"],
            "max_seqs": int(max(1, max_seqs)),
            "prefilter_mode": int(prefilter_mode),
            "db_load_mode": int(db_load_mode),
            "log_path": str(log_path),
            "started_at": datetime.utcnow().isoformat() + "Z",
            "db_alias": db_alias,
        }
        _atomic_write_json(meta_path, metadata)
        return {
            **metadata,
            "running": True,
            "reused": False,
            "meta_path": str(meta_path),
        }


def stop_servers(gpu_id: Optional[int] = None) -> Dict[str, Any]:
    servers = list_servers()
    stopped = 0
    examined = 0
    results: List[Dict[str, Any]] = []

    for server in servers:
        examined += 1
        server_gpu = str(server.get("cuda_visible_devices", ""))
        if gpu_id is not None and server_gpu != str(gpu_id):
            continue

        pid = int(server.get("pid", -1))
        was_running = bool(server.get("running", False))
        action = "not_running"
        if was_running:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    if not _pid_is_alive(pid):
                        break
                    time.sleep(0.1)
                if _pid_is_alive(pid):
                    os.kill(pid, signal.SIGKILL)
                    action = "killed"
                else:
                    action = "terminated"
                stopped += 1
            except Exception:
                action = "error"

        results.append(
            {
                "pid": pid,
                "db_alias": server.get("db_alias"),
                "gpu": server_gpu,
                "was_running": was_running,
                "action": action,
                "meta_path": server.get("meta_path"),
            }
        )

    return {
        "examined": examined,
        "stopped": stopped,
        "servers": results,
    }


def server_status(
    gpu_id: Optional[int] = None,
    include_envdb: bool = True,
    max_seqs: int = 300,
    prefilter_mode: int = 1,
    db_load_mode: int = 0,
) -> Dict[str, Any]:
    effective_gpu_id: Optional[int] = None
    selection_error: Optional[str] = None
    try:
        effective_gpu_id = resolve_msa_gpu_id(gpu_id)
    except Exception as exc:
        selection_error = str(exc)

    servers = list_servers()
    selected_servers = servers
    if effective_gpu_id is not None:
        selected_servers = [
            s for s in servers if str(s.get("cuda_visible_devices", "")) == str(effective_gpu_id)
        ]

    running_aliases = {s.get("db_alias") for s in selected_servers if s.get("running")}
    expected_aliases = {"uniref", "envdb"} if include_envdb else {"uniref"}

    return {
        "running": any(s.get("running") for s in selected_servers),
        "all_running": expected_aliases.issubset(running_aliases),
        "effective_gpu_id": effective_gpu_id,
        "gpu_selection_error": selection_error,
        "expected_aliases": sorted(expected_aliases),
        "max_seqs": int(max(1, max_seqs)),
        "prefilter_mode": int(prefilter_mode),
        "db_load_mode": int(db_load_mode),
        "servers": selected_servers,
    }

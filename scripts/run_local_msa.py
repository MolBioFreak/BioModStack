#!/usr/bin/env python3
"""
Run local MMseqs2 MSA search using ColabFold databases.

Implements the FULL ColabFold canonical workflow:
1. Iterative profile search against UniRef30 (3 iterations)
2. Profile extraction for environmental search
3. Alignment expansion to recover cluster members
4. Environmental database search (ColabFold EnvDB)
5. Quality filtering
6. MSA merging (UniRef + Environmental)

Quality Presets:
- maximum: Full ColabFold workflow (default) - ~15-30s
- balanced: Environmental search without expansion - ~8-15s
- fast: UniRef30 only, minimal processing - ~3-5s

Usage:
    # Default: Maximum quality preset
    python run_local_msa.py \\
        --sequence "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH" \\
        --name hemoglobin \\
        --out_dir ./output
        
    # Fast preset for screening
    python run_local_msa.py --preset fast ...
    
    # Force GPU (specific device)
    python run_local_msa.py --use-gpu --gpu-id 2 ...
"""
import argparse
import contextlib
import os
import subprocess
import tempfile
import hashlib
import gzip
import sys
import fcntl
import time
import shutil
import re
import json
import signal
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

_default_data_root = Path(os.path.expanduser(os.getenv("BMS_DATA") or "~/.biomodstack"))
DEFAULT_DB_PATH = os.getenv("BMS_COLABFOLD_DB") or str(_default_data_root / "colabfold_db")
DEFAULT_CACHE_DIR = os.getenv("BMS_MSA_CACHE") or str(_default_data_root / "msa_cache")
DEFAULT_COLABFOLD_API_HOST = os.getenv("BMS_COLABFOLD_API_HOST") or "https://api.colabfold.com"
DEFAULT_COLABFOLD_API_USER_AGENT = os.getenv("BMS_COLABFOLD_API_USER_AGENT") or "biomodstack-msa/1.0"

# ═══════════════════════════════════════════════════════════════════════════════
# MSA QUALITY PRESETS
# ═══════════════════════════════════════════════════════════════════════════════
MSA_PRESETS = {
    "maximum": {
        "num_iterations": 3,
        "use_env": True,
        "use_expand": True,   # Re-enabled: _aln files verified valid (8.7GB)
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,       # ColabFold uses 0.1 for initial search
        "max_seqs": 10000,
        "qsc": -20.0,        # ColabFold default - score per aligned residue
        "max_seq_id": 0.95,
        "description": "Full ColabFold workflow - highest MSA depth and diversity (~15-30s)"
    },
    "balanced": {
        "num_iterations": 2,
        "use_env": True,
        "use_expand": False,
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,
        "max_seqs": 300,
        "qsc": -20.0,        # ColabFold default
        "max_seq_id": 0.95,
        "description": "Environmental search without expansion (~8-15s)"
    },
    "fast": {
        "num_iterations": 1,
        "use_env": False,
        "use_expand": False,
        "use_filter": False,
        "sensitivity": 7.0,
        "evalue": 0.001,
        "max_seqs": 300,
        "qsc": -20.0,
        "max_seq_id": 1.0,
        "description": "UniRef30 only - quick screening (~3-5s)"
    }
}


def compute_sequence_hash(sequence: str) -> str:
    """Compute SHA256 hash of sequence for cache key."""
    return hashlib.sha256(sequence.encode()).hexdigest()


def get_cache_path(cache_dir: str, seq_hash: str, cache_profile: str = "maximum") -> Path:
    """Get path to profile-scoped cached file (legacy compatibility)."""
    subdir = seq_hash[:2]
    cache_path = Path(cache_dir) / subdir / f"{seq_hash}_{cache_profile}.a3m.gz"
    return cache_path


def get_single_cache_path(cache_dir: str, seq_hash: str) -> Path:
    """Get canonical single-cache path (one MSA per sequence hash)."""
    subdir = seq_hash[:2]
    return Path(cache_dir) / subdir / f"{seq_hash}.a3m.gz"


def get_legacy_cache_path(cache_dir: str, seq_hash: str) -> Path:
    """Backward-compat alias for canonical single-cache path."""
    return get_single_cache_path(cache_dir, seq_hash)


def _is_cache_fresh(cache_path: Path, max_age_days: int) -> bool:
    """Return True when cache file is within age threshold."""
    if max_age_days <= 0:
        return True
    mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
    age = datetime.now() - mtime
    return age <= timedelta(days=max_age_days)


def _count_cached_depth(cache_path: Path) -> Optional[int]:
    """Estimate MSA depth for a cached A3M.gz file."""
    try:
        depth = 0
        with gzip.open(cache_path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith(">"):
                    depth += 1
        return depth
    except Exception:
        return None


def _cleanup_profile_caches(cache_dir: str, seq_hash: str) -> int:
    """Delete profile-scoped cache artifacts for a sequence hash."""
    subdir = Path(cache_dir) / seq_hash[:2]
    if not subdir.exists():
        return 0
    removed = 0
    for profile_path in subdir.glob(f"{seq_hash}_*.a3m.gz"):
        try:
            profile_path.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def check_cache(
    cache_dir: str,
    seq_hash: str,
    max_age_days: int,
    preset: str = "maximum",
    cache_profile: Optional[str] = None,
) -> tuple[Path, str] | None:
    """
    Check if valid cached MSA exists.

    Returns:
        (cache_path, cached_profile) when a compatible cache exists, otherwise None.

    Single-cache policy:
    - canonical sequence cache (<hash>.a3m.gz) is preferred
    - legacy profile caches (<hash>_<profile>.a3m.gz) are migration-only fallbacks
      and are promoted into canonical cache on use
    """
    _ = preset
    _ = cache_profile

    canonical_path = get_single_cache_path(cache_dir, seq_hash)
    if canonical_path.exists():
        if _is_cache_fresh(canonical_path, max_age_days):
            removed = _cleanup_profile_caches(cache_dir, seq_hash)
            if removed:
                print(
                    f"Cache cleanup: removed {removed} legacy profile cache file(s) for {seq_hash[:16]}...",
                    flush=True,
                )
            return canonical_path, "single"
        print(
            "Canonical cache expired; will refresh "
            f"({canonical_path.name})",
            flush=True,
        )

    # Migration fallback: legacy profile caches for this sequence hash.
    subdir = Path(cache_dir) / seq_hash[:2]
    if not subdir.exists():
        return None

    legacy_candidates: List[Tuple[int, float, Path, str]] = []
    for legacy_path in subdir.glob(f"{seq_hash}_*.a3m.gz"):
        if not _is_cache_fresh(legacy_path, max_age_days):
            continue
        legacy_depth = _count_cached_depth(legacy_path)
        if legacy_depth is None:
            continue
        legacy_candidates.append(
            (
                int(legacy_depth),
                float(legacy_path.stat().st_mtime),
                legacy_path,
                legacy_path.name.replace(f"{seq_hash}_", "").replace(".a3m.gz", ""),
            )
        )

    if not legacy_candidates:
        return None

    # Pick the deepest candidate, tie-breaker by recency.
    legacy_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_depth, _best_mtime, best_path, best_profile = legacy_candidates[0]
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_path, canonical_path)
    removed = _cleanup_profile_caches(cache_dir, seq_hash)
    print(
        "CACHE MIGRATION: promoted legacy profile cache to canonical single cache "
        f"(profile={best_profile}, depth={best_depth}, removed={removed})",
        flush=True,
    )
    return canonical_path, "single"


def _normalize_taxon_filter(taxon_list: Optional[str]) -> Optional[str]:
    if not taxon_list:
        return None
    tokens = [tok.strip() for tok in taxon_list.split(",") if tok.strip()]
    return ",".join(tokens) if tokens else None


def build_cache_profile(
    preset: str,
    config: Dict[str, Any],
    min_seq_id: Optional[float],
    min_coverage: Optional[float],
    taxon_list: Optional[str],
) -> str:
    """
    Build cache profile key.

    Default preset config keeps legacy key names (maximum|balanced|fast) so existing
    caches still hit. Any effective override gets an isolated profile suffix to avoid
    cross-contaminating default preset caches.
    """
    default_cfg = MSA_PRESETS.get(preset, {})
    tracked_keys = [
        "num_iterations",
        "use_env",
        "use_expand",
        "use_filter",
        "sensitivity",
        "evalue",
        "max_seqs",
        "qsc",
        "max_seq_id",
    ]
    changed_from_default = any(
        key in config and key in default_cfg and config.get(key) != default_cfg.get(key)
        for key in tracked_keys
    )

    normalized_taxon = _normalize_taxon_filter(taxon_list)
    has_extra_filters = (
        min_seq_id is not None
        or min_coverage is not None
        or normalized_taxon is not None
    )

    if not changed_from_default and not has_extra_filters:
        return preset

    signature_payload = {
        "preset": preset,
        "num_iterations": int(config.get("num_iterations", 0)),
        "use_env": bool(config.get("use_env", False)),
        "use_expand": bool(config.get("use_expand", False)),
        "use_filter": bool(config.get("use_filter", False)),
        "sensitivity": float(config.get("sensitivity", 0.0)),
        "evalue": float(config.get("evalue", 0.0)),
        "max_seqs": int(config.get("max_seqs", 0)),
        "qsc": float(config.get("qsc", 0.0)),
        "max_seq_id": float(config.get("max_seq_id", 1.0)),
        "min_seq_id": float(min_seq_id) if min_seq_id is not None else None,
        "min_coverage": float(min_coverage) if min_coverage is not None else None,
        "taxon_list": normalized_taxon,
    }
    digest = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"{preset}_{digest}"


def save_to_cache(cache_dir: str, seq_hash: str, a3m_content: str, cache_profile: str = "maximum") -> Path:
    """Save MSA to cache with gzip compression."""
    _ = cache_profile
    cache_path = get_single_cache_path(cache_dir, seq_hash)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Protect canonical cache from accidental quality regressions.
    new_depth = a3m_content.count('\n>') + (1 if a3m_content.startswith('>') else 0)
    if cache_path.exists():
        old_depth = None
        try:
            with gzip.open(cache_path, 'rt', encoding='utf-8', errors='ignore') as old_fh:
                old_content = old_fh.read()
            old_depth = old_content.count('\n>') + (1 if old_content.startswith('>') else 0)
        except Exception as exc:
            print(f"WARNING: Could not inspect existing cache {cache_path}: {exc}", flush=True)

        if old_depth is not None and old_depth > new_depth:
            removed_profile_files = _cleanup_profile_caches(cache_dir, seq_hash)
            print(
                "Cache preserve: kept existing canonical cache "
                f"(old_depth={old_depth} > new_depth={new_depth}); "
                f"discarded refresh result; removed_profile_files={removed_profile_files}",
                flush=True,
            )
            return cache_path

    with gzip.open(cache_path, 'wt', encoding='utf-8') as f:
        f.write(a3m_content)

    file_size = cache_path.stat().st_size
    removed_profile_files = _cleanup_profile_caches(cache_dir, seq_hash)
    print(
        f"Saved to canonical cache: {cache_path} "
        f"({file_size} bytes compressed, depth={new_depth}, removed_profile_files={removed_profile_files})",
        flush=True,
    )
    return cache_path


def load_from_cache(cache_path: Path, out_path: str) -> str:
    """Decompress cached MSA to output location."""
    with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
        content = f.read()
    
    with open(out_path, 'w') as f:
        f.write(content)
    
    return content


def get_msa_lock_path(cache_dir: str, seq_hash: str) -> Path:
    """Get path to lock file for MSA generation serialization."""
    lock_dir = Path(cache_dir) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{seq_hash}.lock"


def acquire_msa_lock(lock_path: Path, timeout: int = 600) -> int:
    """
    Acquire exclusive lock for MSA generation.
    
    Returns file descriptor if lock acquired, blocks if another process has it.
    Timeout in seconds (default 10 minutes for long MSA searches).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    
    start_time = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (IOError, OSError):
            if time.time() - start_time > timeout:
                os.close(fd)
                raise TimeoutError(f"Timeout waiting for MSA lock: {lock_path}")
            print(f"Waiting for MSA lock (another job is generating MSA for this sequence)...", flush=True)
            time.sleep(5)


def release_msa_lock(fd: int):
    """Release MSA lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


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
        except ValueError:
            raise ValueError(f"Invalid GPU id in list: {token}")
        if gpu_id in seen:
            continue
        seen.add(gpu_id)
        gpu_ids.append(gpu_id)
    return gpu_ids if gpu_ids else None


def load_scheduler_gpu_policy(config_path: Optional[Path] = None) -> Dict[str, Optional[List[int]]]:
    """
    Load GPU policy from scheduler config (.gpu_config.json), if available.

    Returns:
        {
            "preferred": Optional[List[int]],  # global.msa_preferred_gpu_ids
            "disabled": Optional[List[int]],   # overrides.*.disabled == true
        }
    """
    if config_path is None:
        # Repo root fallback: scripts/run_local_msa.py -> ../.gpu_config.json
        config_path = Path(__file__).resolve().parent.parent / ".gpu_config.json"

    if not config_path.exists():
        return {"preferred": None, "disabled": None}

    data = None
    last_error = None
    for attempt in range(5):
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            break
        except json.JSONDecodeError as exc:
            last_error = exc
            # Handle transient partial reads while scheduler config is being updated.
            time.sleep(0.05 * (attempt + 1))
        except Exception as exc:
            last_error = exc
            break

    if data is None:
        if last_error is not None:
            print(
                f"WARNING: Unable to read scheduler GPU policy from {config_path}: {last_error}",
                flush=True,
            )
        return {"preferred": None, "disabled": None}

    preferred = None
    raw_preferred = data.get("global", {}).get("msa_preferred_gpu_ids")
    if isinstance(raw_preferred, list):
        parsed: List[int] = []
        seen: set[int] = set()
        for value in raw_preferred:
            try:
                gpu_id = int(value)
            except (TypeError, ValueError):
                continue
            if gpu_id in seen:
                continue
            seen.add(gpu_id)
            parsed.append(gpu_id)
        if parsed:
            preferred = parsed

    disabled: List[int] = []
    disabled_seen: set[int] = set()
    overrides = data.get("overrides", {})
    if isinstance(overrides, dict):
        for gpu_key, override in overrides.items():
            if not isinstance(override, dict):
                continue
            if not override.get("disabled", False):
                continue
            try:
                gpu_id = int(gpu_key)
            except (TypeError, ValueError):
                continue
            if gpu_id in disabled_seen:
                continue
            disabled_seen.add(gpu_id)
            disabled.append(gpu_id)

    if not preferred:
        # No explicit MSA preferred list: derive strongest-first order from
        # scheduler tier overrides and detected GPU VRAM capacity.
        capacity_weight = 10.0
        global_cfg = data.get("global", {})
        if isinstance(global_cfg, dict):
            try:
                capacity_weight = float(global_cfg.get("capacity_weight", 10.0))
            except (TypeError, ValueError):
                capacity_weight = 10.0

        try:
            probe = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            probe = None

        ranked: List[Tuple[float, int, int]] = []
        if probe and probe.returncode == 0:
            for line in probe.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    gpu_id = int(parts[0])
                    mem_total = int(parts[1])
                except (TypeError, ValueError):
                    continue
                if gpu_id in disabled_seen:
                    continue

                override = overrides.get(str(gpu_id), {}) if isinstance(overrides, dict) else {}
                priority_tier = None
                if isinstance(override, dict):
                    priority_tier = override.get("priority_tier")

                if priority_tier is not None:
                    try:
                        base_score = float(priority_tier) * 10.0
                    except (TypeError, ValueError):
                        base_score = (mem_total / 10000.0) * capacity_weight
                else:
                    base_score = (mem_total / 10000.0) * capacity_weight
                ranked.append((base_score, mem_total, gpu_id))

        if ranked:
            ranked.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
            preferred = [gpu_id for _score, _mem_total, gpu_id in ranked]

    return {
        "preferred": preferred,
        "disabled": disabled if disabled else None,
    }


def read_persisted_msa_pinned_gpu_id(cache_dir: Optional[str]) -> Optional[int]:
    """
    Read the global MSA GPU pin from shared gpuserver settings.

    This is the default GPU chosen in the UI's MSA Server Settings menu.
    Explicit CLI GPU overrides still take precedence over this persisted value.
    """
    try:
        settings_path = _gpuserver_runtime_root(cache_dir) / "settings.json"
        if not settings_path.exists():
            return None
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        raw_value = data.get("pinned_gpu_id")
        if raw_value in (None, ""):
            return None
        return int(raw_value)
    except Exception:
        return None


def _preferred_gpu_has_running_gpuserver(
    gpu_id: int,
    cache_dir: Optional[str],
    target_db: Optional[Path] = None,
) -> bool:
    """
    Best-effort check for an alive persistent MMseqs gpuserver on a given GPU.

    This lets us treat high VRAM usage from preloaded gpuserver state as expected
    for MSA workloads instead of incorrectly flagging the GPU as "busy".
    If target_db is provided, only a gpuserver for that exact DB counts.
    """
    try:
        runtime_root = _gpuserver_runtime_root(cache_dir)
        for meta_path in runtime_root.glob("*/server.json"):
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(meta.get("cuda_visible_devices", "")) != str(gpu_id):
                continue

            pid = int(meta.get("pid", -1))
            target_db_raw = str(meta.get("target_db", "")).strip()
            if not target_db_raw:
                continue
            meta_target_db = Path(target_db_raw)
            if target_db is not None:
                try:
                    if meta_target_db.resolve() != Path(target_db).resolve():
                        continue
                except Exception:
                    if str(meta_target_db) != str(target_db):
                        continue
            if _is_matching_gpuserver_process(pid, meta_target_db):
                return True
    except Exception:
        pass

    # Fallback for externally managed servers: detect live gpuserver compute PIDs
    # directly from nvidia-smi + /proc even when runtime metadata is absent/stale.
    try:
        gpu_uuid_map_proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if gpu_uuid_map_proc.returncode != 0:
            return False

        gpu_uuid_to_index = {}
        for line in gpu_uuid_map_proc.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                gpu_uuid_to_index[parts[1]] = int(parts[0])
            except Exception:
                continue

        compute_proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if compute_proc.returncode != 0:
            return False

        target_db_text = None
        if target_db is not None:
            try:
                target_db_text = str(Path(target_db).resolve())
            except Exception:
                target_db_text = str(target_db)

        for line in compute_proc.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            gpu_uuid = parts[0]
            try:
                pid = int(parts[1])
            except Exception:
                continue
            if gpu_uuid_to_index.get(gpu_uuid) != int(gpu_id):
                continue

            cmdline_path = f"/proc/{pid}/cmdline"
            if not os.path.exists(cmdline_path):
                continue
            try:
                with open(cmdline_path, "r", encoding="utf-8", errors="ignore") as fh:
                    cmdline = fh.read().replace("\x00", " ").strip()
            except Exception:
                continue
            if "mmseqs" not in cmdline or "gpuserver" not in cmdline:
                continue
            if target_db_text and target_db_text not in cmdline:
                continue
            return True
    except Exception:
        return False
    return False


def check_gpu_availability(
    threshold: int = 80,
    preferred_gpus: Optional[List[int]] = None,
    excluded_gpus: Optional[List[int]] = None,
    allow_gpuserver_memory_override: bool = False,
    cache_dir: Optional[str] = None,
    preferred_gpuserver_target_db: Optional[Path] = None,
) -> int | None:
    """
    Check for available GPU for MMseqs2.
    
    Args:
        threshold: Max utilization/memory percentage to consider GPU available
        preferred_gpus: Optional allowlist of GPU IDs
        excluded_gpus: Optional denylist of GPU IDs
        allow_gpuserver_memory_override: If true, preferred GPUs with active
            persistent MMseqs gpuserver are eligible even when VRAM usage exceeds
            threshold (utilization threshold still applies).
        cache_dir: Cache directory for gpuserver metadata lookup
        preferred_gpuserver_target_db: Optional DB path to require when applying
            gpuserver-based memory override.
    
    Returns GPU ID if one is available, None otherwise.
    
    Note: MMseqs2-GPU Blackwell binary supports SM 7.5-12.0 (Turing through Blackwell).
    """
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total,compute_cap,name',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        
        threshold = max(0, min(100, int(threshold)))
        preferred_order: List[int] = []
        preferred_seen: set[int] = set()
        for gpu_id in preferred_gpus or []:
            try:
                normalized_id = int(gpu_id)
            except (TypeError, ValueError):
                continue
            if normalized_id in preferred_seen:
                continue
            preferred_seen.add(normalized_id)
            preferred_order.append(normalized_id)

        preferred = set(preferred_order)
        excluded = set(excluded_gpus or [])

        gpus = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 6:
                gpu_id = int(parts[0])
                utilization = int(parts[1])
                mem_used = int(parts[2])
                mem_total = int(parts[3])
                compute_cap = float(parts[4])
                gpu_name = parts[5]
                mem_percent = (mem_used / mem_total * 100) if mem_total > 0 else 100
                
                gpus.append({
                    'id': gpu_id,
                    'utilization': utilization,
                    'memory_percent': mem_percent,
                    'memory_total_mb': mem_total,
                    'compute_cap': compute_cap,
                    'name': gpu_name
                })
        
        # Apply allow/deny filters
        if preferred:
            gpus = [g for g in gpus if g['id'] in preferred]
        if excluded:
            gpus = [g for g in gpus if g['id'] not in excluded]

        if preferred_order:
            preferred_rank = {gpu_id: idx for idx, gpu_id in enumerate(preferred_order)}
            gpus.sort(key=lambda g: (preferred_rank.get(g['id'], len(preferred_rank)), g['utilization'], g['memory_percent']))
        else:
            # Mirror scheduler strategy: prefer stronger GPUs first, then least busy.
            gpus.sort(key=lambda g: (-g['memory_total_mb'], g['utilization'], g['memory_percent'], g['id']))
        
        for gpu in gpus:
            memory_ok = gpu['memory_percent'] < threshold
            gpuserver_override = False

            if (
                allow_gpuserver_memory_override
                and preferred
                and gpu['id'] in preferred
                and gpu['utilization'] < threshold
            ):
                gpuserver_override = _preferred_gpu_has_running_gpuserver(
                    gpu_id=gpu['id'],
                    cache_dir=cache_dir,
                    target_db=preferred_gpuserver_target_db,
                )
                if gpuserver_override and not memory_ok:
                    print(
                        f"Preferred GPU {gpu['id']} has active MSA gpuserver; "
                        f"ignoring VRAM threshold (util={gpu['utilization']}%, mem={gpu['memory_percent']:.1f}%).",
                        flush=True,
                    )

            if gpu['utilization'] < threshold and (memory_ok or gpuserver_override):
                print(
                    f"Selected GPU {gpu['id']} ({gpu['name']}, SM {gpu['compute_cap']}) "
                    f"for MMseqs2 [util={gpu['utilization']}%, mem={gpu['memory_percent']:.1f}%]",
                    flush=True
                )
                return gpu['id']
        
        if (
            gpus
            and preferred
            and allow_gpuserver_memory_override
        ):
            # Explicit preferred GPU intent: if only VRAM threshold blocks selection
            # in opportunistic mode, attempt GPU anyway and let downstream OOM
            # handling decide CPU fallback.
            for gpu in gpus:
                if gpu['id'] in preferred and gpu['utilization'] < threshold and gpu['memory_percent'] < 99.5:
                    print(
                        f"Preferred GPU {gpu['id']} selected despite VRAM pressure "
                        f"(util={gpu['utilization']}%, mem={gpu['memory_percent']:.1f}%).",
                        flush=True,
                    )
                    return gpu['id']

        if not gpus:
            print("No GPUs matched selection policy for MMseqs2", flush=True)
        else:
            details = ", ".join(
                f"gpu{g['id']}:util={g['utilization']}%,mem={g['memory_percent']:.1f}%"
                for g in gpus
            )
            print(
                f"All candidate GPUs are busy (utilization/memory > {threshold}%): {details}",
                flush=True,
            )
        
        return None
    except Exception as e:
        print(f"GPU detection failed: {e}", flush=True)
        return None


def run_mmseqs(mmseqs_bin: str, params: list, env: dict, capture_output: bool = True):
    """
    Run MMseqs2 command with robust output handling.
    
    Uses Popen with threaded output streaming to prevent pipe buffer deadlock
    while preserving full logging capability. Allows unlimited runtime for
    long MSA searches.
    
    Args:
        mmseqs_bin: Path to mmseqs binary
        params: Command parameters
        env: Environment dict
        capture_output: If True, capture and return stdout/stderr
        
    Returns:
        subprocess.CompletedProcess with returncode and captured output
        
    Raises:
        RuntimeError: If mmseqs command fails (non-zero exit)
        
    Note:
        This implementation uses threading to read stdout/stderr concurrently,
        preventing the deadlock that can occur with subprocess.run(capture_output=True)
        when a child process produces more output than the OS pipe buffer can hold.
        See: https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate
    """
    import threading
    from io import StringIO
    
    cmd = [str(mmseqs_bin)] + [str(p) for p in params]
    module = params[0] if params else "unknown"
    
    # Suppress verbose parameter list in logs
    env_copy = env.copy()
    env_copy["MMSEQS_CALL_DEPTH"] = "1"
    
    if not capture_output:
        # Simple case - no capture needed
        result = subprocess.run(cmd, env=env_copy)
        if result.returncode != 0:
            raise RuntimeError(f"MMseqs2 {module} failed with exit code {result.returncode}")
        return result
    
    # Use Popen with threaded output reading to prevent pipe buffer deadlock
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    
    def stream_output(pipe, buffer, echo_to=None):
        """Read from pipe and optionally echo to another stream."""
        try:
            for line in iter(pipe.readline, ''):
                buffer.write(line)
                if echo_to:
                    echo_to.write(line)
                    echo_to.flush()
            pipe.close()
        except Exception:
            pass
    
    proc = subprocess.Popen(
        cmd,
        env=env_copy,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1  # Line buffered
    )
    
    # Start threads to read output concurrently (prevents pipe buffer deadlock)
    stdout_thread = threading.Thread(
        target=stream_output, 
        args=(proc.stdout, stdout_buffer, None)  # Don't echo stdout (too verbose)
    )
    stderr_thread = threading.Thread(
        target=stream_output,
        args=(proc.stderr, stderr_buffer, sys.stderr)  # Echo errors
    )
    
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()
    
    # Wait for process to complete (no timeout - allow long searches)
    proc.wait()
    
    # Wait for output threads to finish reading
    stdout_thread.join(timeout=5.0)
    stderr_thread.join(timeout=5.0)
    
    stdout_content = stdout_buffer.getvalue()
    stderr_content = stderr_buffer.getvalue()
    
    if proc.returncode != 0:
        error_msg = stderr_content.strip() if stderr_content else f"Exit code {proc.returncode}"
        raise RuntimeError(f"MMseqs2 {module} failed: {error_msg}")
    
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout_content, stderr_content)


def sanitize_a3m_for_boltz(a3m_content: str) -> Tuple[str, int]:
    """
    Strip unexpected characters from A3M sequence lines.

    Boltz's parser can fail on digits/control symbols embedded in sequence rows.
    Keep only letters and gap '-' in sequence lines; preserve headers verbatim.
    """
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-")
    removed = 0
    cleaned_lines: List[str] = []

    for line in a3m_content.splitlines():
        if line.startswith(">") or line == "":
            cleaned_lines.append(line)
            continue
        cleaned = "".join(ch for ch in line if ch in allowed)
        removed += len(line) - len(cleaned)
        cleaned_lines.append(cleaned)

    sanitized = "\n".join(cleaned_lines)
    if a3m_content.endswith("\n"):
        sanitized += "\n"
    return sanitized, removed


def _tail_text_file(path: Path, max_chars: int = 2000) -> str:
    """Return a safe tail of a text file for debugging."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _pid_is_alive(pid: int) -> bool:
    """Return True when a process with PID exists and is signalable."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_proc_cmdline(pid: int) -> str:
    """Best-effort process cmdline read from /proc."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _is_matching_gpuserver_process(pid: int, target_db: Path) -> bool:
    """
    Check that PID still points to an MMseqs gpuserver for target DB.

    This guards against stale PID files after PID reuse.
    """
    if not _pid_is_alive(pid):
        return False
    cmdline = _read_proc_cmdline(pid)
    if not cmdline:
        # If /proc cmdline is not available but PID exists, assume alive.
        return True
    target_db_text = str(target_db)
    return "gpuserver" in cmdline and target_db_text in cmdline


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically write JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _http_post_form_json(
    url: str,
    form_data: Dict[str, Any],
    timeout_seconds: float = 20.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> Dict[str, Any]:
    """POST form data and parse JSON response."""
    payload = urllib.parse.urlencode(form_data).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} from ColabFold API POST {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ColabFold API POST {url} failed: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON from ColabFold API POST {url}: {raw[:200]!r}") from exc


def _http_get_json(
    url: str,
    timeout_seconds: float = 20.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> Dict[str, Any]:
    """GET JSON from URL."""
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} from ColabFold API GET {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ColabFold API GET {url} failed: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON from ColabFold API GET {url}: {raw[:200]!r}") from exc


def _http_download_file(
    url: str,
    dest_path: Path,
    timeout_seconds: float = 60.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> None:
    """Download URL to local file."""
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as fh:
                fh.write(response.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} while downloading {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def _normalize_colabfold_host(host_url: str) -> str:
    """Normalize ColabFold API host URL."""
    normalized = (host_url or DEFAULT_COLABFOLD_API_HOST).strip()
    if not normalized:
        normalized = DEFAULT_COLABFOLD_API_HOST
    return normalized.rstrip("/")


def _resolve_colabfold_api_mode(use_env: bool, use_filter: bool) -> str:
    """
    ColabFold API mode mapping:
      use_env + use_filter      -> env
      !use_env + use_filter     -> all
      use_env + !use_filter     -> env-nofilter
      !use_env + !use_filter    -> nofilter
    """
    if use_filter:
        return "env" if use_env else "all"
    return "env-nofilter" if use_env else "nofilter"


def _wait_for_colabfold_submit_slot(cache_dir: str, min_interval_seconds: float) -> None:
    """
    Global pacing gate for remote submissions.

    This serializes submissions and enforces a minimum inter-submit delay so
    single-job users still behave politely against shared ColabFold servers.
    """
    interval = max(0.0, float(min_interval_seconds))
    if interval <= 0:
        return

    lock_dir = Path(cache_dir or DEFAULT_CACHE_DIR) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "colabfold_api.rate.lock"
    state_path = lock_dir / "colabfold_api.rate.json"

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        last_submit = 0.0
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                last_submit = float(state.get("last_submit_unix", 0.0) or 0.0)
            except Exception:
                last_submit = 0.0

        now = time.time()
        wait_seconds = max(0.0, (last_submit + interval) - now)
        if wait_seconds > 0:
            print(
                f"ColabFold API pacing: waiting {wait_seconds:.1f}s before submit...",
                flush=True,
            )
            time.sleep(wait_seconds)

        _atomic_write_json(
            state_path,
            {
                "last_submit_unix": float(time.time()),
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
        )


def _read_a3m_entries(a3m_text: str) -> List[Tuple[str, str]]:
    """Parse A3M text into (header, sequence) entries."""
    entries: List[Tuple[str, str]] = []
    current_header: Optional[str] = None
    seq_lines: List[str] = []

    for raw_line in a3m_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_header is not None:
                entries.append((current_header, "".join(seq_lines)))
            current_header = line
            seq_lines = []
            continue
        if current_header is None:
            # Ignore malformed leading sequence lines.
            continue
        seq_lines.append(line)

    if current_header is not None:
        entries.append((current_header, "".join(seq_lines)))

    return entries


def _entries_to_a3m(entries: List[Tuple[str, str]]) -> str:
    """Serialize parsed A3M entries back to text."""
    lines: List[str] = []
    for header, seq in entries:
        lines.append(header)
        lines.append(seq)
    return "\n".join(lines) + ("\n" if lines else "")


def _merge_colabfold_a3m_contents(primary_text: str, extra_texts: List[str]) -> str:
    """Merge ColabFold A3M blocks while keeping query first and deduplicating by sequence."""
    merged_entries: List[Tuple[str, str]] = []
    seen_sequences: set[str] = set()

    primary_entries = _read_a3m_entries(primary_text)
    if not primary_entries:
        return ""

    # Always keep first entry from primary (query).
    query_header, query_seq = primary_entries[0]
    merged_entries.append((query_header, query_seq))
    seen_sequences.add(query_seq)

    for _, seq in primary_entries[1:]:
        if not seq or seq in seen_sequences:
            continue
        seen_sequences.add(seq)
        merged_entries.append((f">hit_{len(merged_entries)}", seq))

    for text in extra_texts:
        for idx, (header, seq) in enumerate(_read_a3m_entries(text)):
            if idx == 0:
                # Skip query entry from additional files.
                continue
            if not seq or seq in seen_sequences:
                continue
            seen_sequences.add(seq)
            merged_entries.append((header, seq))

    return _entries_to_a3m(merged_entries)


def _postfilter_a3m_by_taxonomy(a3m_content: str, taxon_list: Optional[str]) -> str:
    """Best-effort taxonomy post-filter for A3M text (keeps query sequence)."""
    if not taxon_list:
        return a3m_content

    target_taxids = {tok.strip() for tok in str(taxon_list).split(",") if tok.strip()}
    if not target_taxids:
        return a3m_content

    domain_map = {
        "2": "Bacteria",
        "2157": "Archaea",
        "2759": "Eukaryota",
        "10239": "Viruses",
    }
    filter_domains = {domain_map.get(tid) for tid in target_taxids if tid in domain_map}
    if not filter_domains:
        return a3m_content

    filtered_entries: List[str] = []
    current_entry: List[str] = []

    for line in a3m_content.split("\n"):
        if line.startswith(">"):
            if current_entry:
                header = current_entry[0]
                if header == ">query" or "query" in header.lower()[:20]:
                    filtered_entries.append("\n".join(current_entry))
                else:
                    tax_match = re.search(r"Tax=([^T]+?)(?:TaxID=|$)", header)
                    if tax_match:
                        tax_name = tax_match.group(1).strip().lower()
                        is_bacteria = any(
                            kw in tax_name
                            for kw in (
                                "bacteri",
                                "escherichia",
                                "salmonella",
                                "streptococcus",
                                "staphylococcus",
                                "pseudomonas",
                                "clostridium",
                                "bacillus",
                            )
                        )
                        if "Bacteria" in filter_domains and is_bacteria:
                            filtered_entries.append("\n".join(current_entry))
                        elif "Bacteria" not in filter_domains:
                            filtered_entries.append("\n".join(current_entry))
                    else:
                        filtered_entries.append("\n".join(current_entry))
            current_entry = [line]
        else:
            current_entry.append(line)

    if current_entry:
        filtered_entries.append("\n".join(current_entry))

    return "\n".join(filtered_entries)


def _gpuserver_runtime_root(cache_dir: Optional[str]) -> Path:
    """
    Directory for persistent gpuserver metadata/logs.

    Can be overridden with BMS_MMSEQS_GPUSERVER_DIR.
    """
    env_override = os.getenv("BMS_MMSEQS_GPUSERVER_DIR")
    if env_override:
        root = Path(env_override)
    else:
        base_cache = Path(cache_dir or DEFAULT_CACHE_DIR)
        root = base_cache / ".gpuserver"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _terminate_gpuserver_pid(pid: int, timeout_seconds: float = 3.0) -> None:
    """Best-effort termination of a gpuserver process started in its own session."""
    if pid <= 0 or not _pid_is_alive(pid):
        return
    try:
        # gpuserver is started with start_new_session=True, so kill process group first.
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return

    deadline = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            return
        time.sleep(0.1)

    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def reclaim_conflicting_gpuserver_instances(
    target_db: Path,
    preferred_gpus: Optional[List[int]],
    cache_dir: Optional[str],
) -> int:
    """
    Free VRAM on preferred GPUs by stopping persistent gpuserver instances that
    are for a different DB than target_db.

    This is primarily for low-VRAM dedicated MSA GPUs where keeping both UniRef
    and EnvDB servers resident can force all searches onto CPU.
    """
    preferred = {int(g) for g in (preferred_gpus or [])}
    if not preferred:
        return 0

    runtime_root = _gpuserver_runtime_root(cache_dir)
    target_resolved = target_db.resolve()
    reclaimed = 0

    for meta_path in runtime_root.glob("*/server.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        try:
            gpu_id = int(str(meta.get("cuda_visible_devices", "")).strip())
        except Exception:
            continue
        if gpu_id not in preferred:
            continue

        target_raw = str(meta.get("target_db", "")).strip()
        if not target_raw:
            continue
        meta_target = Path(target_raw)
        try:
            same_target = meta_target.resolve() == target_resolved
        except Exception:
            same_target = str(meta_target) == str(target_db)
        if same_target:
            continue

        pid = int(meta.get("pid", -1))
        if _is_matching_gpuserver_process(pid, meta_target):
            print(
                f"Stopping conflicting gpuserver pid={pid} gpu={gpu_id} db={meta_target.name} "
                f"to prioritize {target_db.name}.",
                flush=True,
            )
            _terminate_gpuserver_pid(pid)
            reclaimed += 1

        # Clear stale metadata for stopped/missing processes.
        try:
            meta_path.unlink(missing_ok=True)
        except Exception:
            pass

    return reclaimed


def _gpuserver_key(
    mmseqs_bin: str,
    target_db: Path,
    cuda_visible_devices: str,
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
) -> str:
    """Stable key for persistent gpuserver instances."""
    key_material = "|".join(
        [
            str(Path(mmseqs_bin).resolve()),
            str(Path(target_db).resolve()),
            str(cuda_visible_devices),
            str(int(max(1, max_seqs))),
            str(int(prefilter_mode)),
            str(int(db_load_mode)),
        ]
    )
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]


def ensure_persistent_mmseqs_gpuserver(
    mmseqs_bin: str,
    target_db: Path,
    env: Dict[str, str],
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
    cache_dir: Optional[str],
    startup_wait_seconds: float = 1.0,
) -> Dict[str, Any]:
    """
    Ensure an MMseqs2 gpuserver stays alive across jobs for this DB/GPU key.

    Returns metadata including PID and whether an existing server was reused.
    """
    cuda_devices = env.get("CUDA_VISIBLE_DEVICES", "")
    runtime_root = _gpuserver_runtime_root(cache_dir)
    key = _gpuserver_key(
        mmseqs_bin=mmseqs_bin,
        target_db=target_db,
        cuda_visible_devices=cuda_devices,
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

        existing = None
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                existing = None

        if existing:
            pid = int(existing.get("pid", -1))
            if _is_matching_gpuserver_process(pid, target_db):
                existing["reused"] = True
                existing["key"] = key
                existing["log_path"] = str(log_path)
                return existing

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
                f"Persistent gpuserver exited early (code {early_exit}) for {target_db}. "
                f"Log: {log_path}\n{tail}"
            )

        metadata = {
            "pid": proc.pid,
            "key": key,
            "reused": False,
            "target_db": str(target_db),
            "cuda_visible_devices": cuda_devices,
            "max_seqs": int(max(1, max_seqs)),
            "prefilter_mode": int(prefilter_mode),
            "db_load_mode": int(db_load_mode),
            "log_path": str(log_path),
            "started_at": datetime.utcnow().isoformat() + "Z",
        }
        _atomic_write_json(meta_path, metadata)
        return metadata


def touch_gpuserver_query_activity(
    cache_dir: Optional[str],
    job_name: str,
    preset: str,
    use_gpu: bool,
    gpu_id: Optional[int],
) -> None:
    """Record the latest MSA query timestamp for optional idle auto-stop logic."""
    try:
        runtime_root = _gpuserver_runtime_root(cache_dir)
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "job_name": job_name,
            "preset": preset,
            "use_gpu": bool(use_gpu),
            "gpu_id": gpu_id,
        }
        _atomic_write_json(runtime_root / "last_query_activity.json", payload)
    except Exception as exc:
        # Metadata write failures should never block MSA generation.
        print(f"WARNING: Could not record MSA query activity: {exc}", flush=True)


@contextlib.contextmanager
def run_mmseqs_gpuserver(
    mmseqs_bin: str,
    target_db: Path,
    env: Dict[str, str],
    max_seqs: int,
    prefilter_mode: int,
    db_load_mode: int,
    log_path: Path,
    startup_wait_seconds: float = 1.0,
):
    """
    Run an MMseqs2 GPU server process for a single target DB.

    The caller executes one or more `mmseqs search --gpu-server 1` commands while this
    context is active. On exit, the server is terminated safely.
    """
    cmd = [
        str(mmseqs_bin),
        "gpuserver",
        str(target_db),
        "--max-seqs", str(max(1, int(max_seqs))),
        "--prefilter-mode", str(int(prefilter_mode)),
        "--db-load-mode", str(int(db_load_mode)),
    ]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        time.sleep(max(0.0, float(startup_wait_seconds)))
        early_exit = proc.poll()
        if early_exit is not None:
            log_handle.flush()
            tail = _tail_text_file(log_path)
            raise RuntimeError(
                f"MMseqs2 gpuserver exited early (code {early_exit}) for {target_db}. "
                f"Log: {log_path}\n{tail}"
            )
        yield
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        finally:
            log_handle.close()


def _run_colabfold_api_search(
    sequence: str,
    work_dir: Path,
    host_url: str,
    cache_dir: str,
    use_env: bool,
    use_filter: bool,
    min_submit_interval_seconds: float,
    poll_interval_seconds: float,
    submit_timeout_seconds: float = 20.0,
    status_timeout_seconds: float = 20.0,
    download_timeout_seconds: float = 90.0,
    max_submit_attempts: int = 12,
) -> Dict[str, Any]:
    """
    Query ColabFold public API for a single sequence MSA.

    Returns:
      {
        "ticket_id": str,
        "api_mode": str,
        "status": str,
        "a3m_content": str,
      }
    """
    host = _normalize_colabfold_host(host_url)
    api_mode = _resolve_colabfold_api_mode(use_env=use_env, use_filter=use_filter)
    query = f">101\n{sequence}\n"

    ticket_id: Optional[str] = None
    submit_status = "UNKNOWN"
    for attempt in range(max(1, int(max_submit_attempts))):
        _wait_for_colabfold_submit_slot(
            cache_dir=cache_dir,
            min_interval_seconds=min_submit_interval_seconds,
        )
        submit_payload = _http_post_form_json(
            url=f"{host}/ticket/msa",
            form_data={"q": query, "mode": api_mode},
            timeout_seconds=submit_timeout_seconds,
        )
        submit_status = str(submit_payload.get("status", "")).strip().upper()
        candidate_id = submit_payload.get("id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            ticket_id = candidate_id.strip()

        if submit_status in {"RATELIMIT", "UNKNOWN"}:
            backoff = min(60.0, 5.0 + (attempt * 2.0))
            print(
                f"ColabFold API submit status={submit_status}; retrying in {backoff:.1f}s...",
                flush=True,
            )
            time.sleep(backoff)
            continue
        if submit_status == "MAINTENANCE":
            raise RuntimeError("ColabFold API is in MAINTENANCE mode")
        if submit_status in {"ERROR", "FAILED"}:
            raise RuntimeError(f"ColabFold API submit failed: {submit_payload}")
        if ticket_id:
            break

    if not ticket_id:
        raise RuntimeError(f"ColabFold API submit did not return a ticket id (last status={submit_status})")

    print(f"ColabFold API ticket: {ticket_id} (mode={api_mode})", flush=True)

    final_status = submit_status if submit_status else "UNKNOWN"
    while True:
        status_payload = _http_get_json(
            url=f"{host}/ticket/{ticket_id}",
            timeout_seconds=status_timeout_seconds,
        )
        final_status = str(status_payload.get("status", "")).strip().upper()

        if final_status == "COMPLETE":
            break
        if final_status in {"PENDING", "RUNNING", "UNKNOWN"}:
            sleep_for = max(1.0, float(poll_interval_seconds))
            print(
                f"ColabFold API ticket {ticket_id} status={final_status}; polling again in {sleep_for:.1f}s...",
                flush=True,
            )
            time.sleep(sleep_for)
            continue
        if final_status == "RATELIMIT":
            sleep_for = max(5.0, float(poll_interval_seconds) * 2.0)
            print(
                f"ColabFold API ticket {ticket_id} status=RATELIMIT; waiting {sleep_for:.1f}s...",
                flush=True,
            )
            time.sleep(sleep_for)
            continue
        if final_status == "MAINTENANCE":
            raise RuntimeError("ColabFold API entered MAINTENANCE mode during polling")
        raise RuntimeError(f"ColabFold API ticket {ticket_id} failed with status={final_status}")

    archive_path = work_dir / f"{ticket_id}.tar.gz"
    _http_download_file(
        url=f"{host}/result/download/{ticket_id}",
        dest_path=archive_path,
        timeout_seconds=download_timeout_seconds,
    )

    with tarfile.open(archive_path, mode="r:gz") as tar:
        tar.extractall(path=work_dir)

    a3m_paths = sorted(work_dir.rglob("*.a3m"))
    if not a3m_paths:
        raise RuntimeError(f"ColabFold API ticket {ticket_id} produced no .a3m files")

    primary_candidates = [p for p in a3m_paths if "uniref" in p.name.lower()]
    primary_path = primary_candidates[0] if primary_candidates else a3m_paths[0]
    primary_text = primary_path.read_text(encoding="utf-8", errors="ignore")

    extra_texts: List[str] = []
    if use_env:
        env_candidates = [
            p for p in a3m_paths
            if p != primary_path and any(
                token in p.name.lower() for token in ("bfd", "mgnify", "metaeuk", "env")
            )
        ]
        if not env_candidates:
            env_candidates = [p for p in a3m_paths if p != primary_path]
        for env_path in env_candidates:
            extra_texts.append(env_path.read_text(encoding="utf-8", errors="ignore"))

    merged_content = _merge_colabfold_a3m_contents(primary_text, extra_texts)
    if not merged_content.strip():
        merged_content = primary_text

    return {
        "ticket_id": ticket_id,
        "api_mode": api_mode,
        "status": final_status,
        "a3m_content": merged_content,
    }


def run_colabfold_api_msa_workflow(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = DEFAULT_DB_PATH,
    cache_dir: str = None,
    max_age_days: int = 0,
    force_refresh: bool = False,
    cache_only: bool = False,
    num_threads: int = 32,
    use_gpu: bool = None,
    gpu_id: int = None,
    cpu_only: bool = False,
    gpu_mode: str = "auto",
    gpu_threshold: int = 80,
    preferred_gpus: Optional[List[int]] = None,
    excluded_gpus: Optional[List[int]] = None,
    gpu_server_mode: str = "persistent",
    gpu_server_wait_timeout: int = 120,
    gpu_server_db_load_mode: int = 0,
    gpu_server_startup_wait: float = 1.0,
    reference_sequence: str = None,
    preset: str = "balanced",
    num_iterations: int = None,
    use_env: bool = None,
    use_expand: bool = None,
    use_filter: bool = None,
    evalue: float = None,
    sensitivity: float = None,
    max_seqs: int = None,
    min_seq_id: float = None,
    min_coverage: float = None,
    taxon_list: str = None,
    min_depth_warning: int = 100,
    min_depth_fail: int = 0,
    fast_env_fallback_min_depth: int = 25,
    colabfold_api_host: str = DEFAULT_COLABFOLD_API_HOST,
    colabfold_api_min_interval: float = 6.0,
    colabfold_api_poll_interval: float = 6.0,
):
    """
    Generate MSA via remote ColabFold API (single-query mode).

    This intentionally keeps the same cache + report behavior as local mode so
    downstream workflow logic remains unchanged.
    """
    _ = db_path
    _ = num_threads
    _ = use_gpu
    _ = gpu_id
    _ = cpu_only
    _ = gpu_mode
    _ = gpu_threshold
    _ = preferred_gpus
    _ = excluded_gpus
    _ = gpu_server_mode
    _ = gpu_server_wait_timeout
    _ = gpu_server_db_load_mode
    _ = gpu_server_startup_wait
    _ = fast_env_fallback_min_depth

    if force_refresh and cache_only:
        raise ValueError("Invalid flags: --force_refresh cannot be combined with --cache-only")

    if preset not in MSA_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Options: {list(MSA_PRESETS.keys())}")

    config = MSA_PRESETS[preset].copy()
    print(f"MSA Preset: {preset} - {config['description']}", flush=True)
    print(f"MSA Provider: colabfold_api ({_normalize_colabfold_host(colabfold_api_host)})", flush=True)

    if num_iterations is not None:
        config["num_iterations"] = num_iterations
    if use_env is not None:
        config["use_env"] = bool(use_env)
    if use_expand is not None:
        config["use_expand"] = bool(use_expand)
    if use_filter is not None:
        config["use_filter"] = bool(use_filter)
    if evalue is not None:
        config["evalue"] = evalue
    if sensitivity is not None:
        config["sensitivity"] = sensitivity
    if max_seqs is not None:
        config["max_seqs"] = max(1, int(max_seqs))

    if config.get("use_expand"):
        print(
            "WARNING: ColabFold API mode ignores local alignment expansion controls.",
            flush=True,
        )

    cache_profile = build_cache_profile(
        preset=preset,
        config=config,
        min_seq_id=min_seq_id,
        min_coverage=min_coverage,
        taxon_list=taxon_list,
    )

    cache_key_seq = reference_sequence or sequence
    seq_hash = compute_sequence_hash(cache_key_seq)
    cache_dir = cache_dir or DEFAULT_CACHE_DIR

    lock_fd = None
    lock_path = get_msa_lock_path(cache_dir, seq_hash)
    print(f"Acquiring MSA lock for {seq_hash[:16]}...", flush=True)
    lock_fd = acquire_msa_lock(lock_path)
    print("MSA lock acquired", flush=True)

    try:
        cache_found = False
        if not force_refresh:
            cached = check_cache(
                cache_dir=cache_dir,
                seq_hash=seq_hash,
                max_age_days=max_age_days,
                preset=preset,
                cache_profile=cache_profile,
            )
            if cached:
                cache_found = True
                cached_path, cached_profile = cached
                print(f"CACHE HIT: {seq_hash[:16]}... ({cached_profile})", flush=True)
                final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
                os.makedirs(out_dir, exist_ok=True)
                load_from_cache(cached_path, final_a3m)

                with open(final_a3m, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                content, removed_invalid_chars = sanitize_a3m_for_boltz(content)
                if removed_invalid_chars > 0:
                    with open(final_a3m, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(
                        f"Sanitized cached A3M: removed {removed_invalid_chars} invalid character(s).",
                        flush=True,
                    )
                msa_depth = content.count("\n>") + (1 if content.startswith(">") else 0)

                report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "msa_depth": msa_depth,
                            "query_length": len(sequence),
                            "preset": preset,
                            "cache_profile": cache_profile,
                            "cached_profile": cached_profile,
                            "cached_preset": cached_profile.split("_", 1)[0],
                            "provider": "colabfold_api",
                            "api_host": _normalize_colabfold_host(colabfold_api_host),
                            "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                            "selected_gpu_id": None,
                            "used_gpu_mmseqs": False,
                            "from_cache": True,
                        },
                        f,
                        indent=2,
                    )
                print(f"MSA quality report: {report_path}", flush=True)
                print(f"MSA generated: {final_a3m}", flush=True)
                return

        if cache_only and not cache_found:
            raise RuntimeError(
                "CACHE ONLY MODE: No cached MSA found for sequence hash "
                f"{seq_hash[:16]}...; disable --cache-only or run once without it."
            )

        print(f"CACHE MISS: {seq_hash[:16]}... ({cache_profile}; running ColabFold API)", flush=True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            query_result = _run_colabfold_api_search(
                sequence=sequence,
                work_dir=Path(tmp_dir),
                host_url=colabfold_api_host,
                cache_dir=cache_dir,
                use_env=bool(config["use_env"]),
                use_filter=bool(config["use_filter"]),
                min_submit_interval_seconds=colabfold_api_min_interval,
                poll_interval_seconds=colabfold_api_poll_interval,
            )

            a3m_content = str(query_result["a3m_content"])
            a3m_content, removed_invalid_chars = sanitize_a3m_for_boltz(a3m_content)
            if removed_invalid_chars > 0:
                print(
                    f"Sanitized A3M: removed {removed_invalid_chars} invalid character(s).",
                    flush=True,
                )

            if taxon_list:
                before_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
                a3m_content = _postfilter_a3m_by_taxonomy(a3m_content, taxon_list)
                after_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
                if before_depth != after_depth:
                    print(
                        f"Taxonomy filter: {before_depth} -> {after_depth} sequences",
                        flush=True,
                    )

            msa_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
            print(f"Final MSA depth: {msa_depth} sequences", flush=True)

            if min_depth_fail > 0 and msa_depth < min_depth_fail:
                error_msg = (
                    f"MSA FAILED: Only {msa_depth} sequences found (minimum: {min_depth_fail}). "
                    "Consider: 1) Different preset, 2) Relaxing filters, 3) Checking sequence."
                )
                print(f"ERROR: {error_msg}", flush=True)
                raise RuntimeError(error_msg)

            if msa_depth < min_depth_warning:
                print(
                    f"WARNING: MSA has only {msa_depth} sequences (recommended >{min_depth_warning}). "
                    f"Structure prediction confidence may be low.",
                    flush=True,
                )

            quality_report = {
                "msa_depth": msa_depth,
                "query_length": len(sequence),
                "preset": preset,
                "cache_profile": cache_profile,
                "provider": "colabfold_api",
                "api_host": _normalize_colabfold_host(colabfold_api_host),
                "api_ticket_id": query_result.get("ticket_id"),
                "api_mode": query_result.get("api_mode"),
                "api_status": query_result.get("status"),
                "num_iterations": config["num_iterations"],
                "use_env_requested": bool(config["use_env"]),
                "use_env_effective": bool(config["use_env"]),
                "auto_env_fallback_triggered": False,
                "fast_env_fallback_min_depth": 0,
                "uniref_only_depth": None,
                "use_expand": config["use_expand"],
                "use_filter": config["use_filter"],
                "evalue": config["evalue"],
                "sensitivity": config["sensitivity"],
                "taxon_filter": taxon_list,
                "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                "selected_gpu_id": None,
                "used_gpu_mmseqs": False,
                "from_cache": False,
            }

            final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
            os.makedirs(out_dir, exist_ok=True)
            with open(final_a3m, "w", encoding="utf-8") as f:
                f.write(a3m_content)

            report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(quality_report, f, indent=2)
            print(f"MSA quality report: {report_path}", flush=True)
            print(f"MSA generated: {final_a3m}", flush=True)

            if cache_dir:
                save_to_cache(cache_dir, seq_hash, a3m_content, cache_profile)

    finally:
        if lock_fd is not None:
            release_msa_lock(lock_fd)
            print("MSA lock released", flush=True)


def run_colabfold_msa_workflow(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = DEFAULT_DB_PATH,
    cache_dir: str = None,
    max_age_days: int = 0,
    force_refresh: bool = False,
    cache_only: bool = False,
    num_threads: int = 32,
    use_gpu: bool = None,
    gpu_id: int = None,
    cpu_only: bool = False,
    gpu_mode: str = "auto",
    gpu_threshold: int = 80,
    preferred_gpus: Optional[List[int]] = None,
    excluded_gpus: Optional[List[int]] = None,
    gpu_server_mode: str = "persistent",
    gpu_server_wait_timeout: int = 120,
    gpu_server_db_load_mode: int = 0,
    gpu_server_startup_wait: float = 1.0,
    reference_sequence: str = None,
    # Preset and override parameters
    preset: str = "balanced",
    num_iterations: int = None,
    use_env: bool = None,
    use_expand: bool = None,
    use_filter: bool = None,
    # Legacy parameters (for backward compatibility)
    evalue: float = None,
    sensitivity: float = None,
    max_seqs: int = None,
    min_seq_id: float = None,
    min_coverage: float = None,  
    taxon_list: str = None,
    min_depth_warning: int = 100,
    min_depth_fail: int = 0,
    fast_env_fallback_min_depth: int = 25,
):
    """
    Generate MSA using FULL ColabFold-compatible workflow.
    
    Workflow (Maximum preset):
    1. Create query database
    2. Iterative profile search against UniRef30 (n iterations)
    3. Extract refined profile from final iteration
    4. Expand alignments to recover cluster members
    5. Search Environmental DB with profile
    6. Filter results by quality metrics
    7. Convert both to A3M format
    8. Merge UniRef + Environmental MSAs
    
    Args:
        sequence: Amino acid sequence
        job_name: Name for output files
        out_dir: Output directory
        db_path: Path to ColabFold database directory
        cache_dir: Cache directory for storing results
        max_age_days: Cache expiry in days (0 = never expire)
        force_refresh: Bypass cache
        cache_only: Use only cached MSA; fail on cache miss
        num_threads: CPU threads for MMseqs2
        use_gpu: Force GPU mode (None = auto-detect)
        gpu_id: Specific GPU to use
        cpu_only: Force CPU mode
        gpu_mode: GPU policy (auto, opportunistic, required, cpu)
        gpu_threshold: Max utilization/memory threshold for opportunistic GPU selection
        preferred_gpus: Preferred GPU IDs for MSA search
        excluded_gpus: Excluded GPU IDs for MSA search
        gpu_server_mode: GPU server policy (auto, required, persistent, off)
        gpu_server_wait_timeout: Seconds search waits for gpuserver readiness
        gpu_server_db_load_mode: MMseqs DB load mode used by gpuserver/search
        gpu_server_startup_wait: Seconds to wait after starting gpuserver
        reference_sequence: For mutagenesis - use this sequence for cache key
        preset: Quality preset (maximum, balanced, fast)
        num_iterations: Override number of profile iterations
        use_env: Override environmental DB usage
        use_expand: Override alignment expansion
        use_filter: Override quality filtering
        evalue: Override e-value threshold
        sensitivity: Override sensitivity
        max_seqs: Override maximum candidate sequences retained by MMseqs2
        min_seq_id: Minimum sequence identity filter
        min_coverage: Minimum query coverage filter
        taxon_list: Taxonomy filter (comma-separated NCBI IDs)
        min_depth_warning: Warn if MSA depth below this
        min_depth_fail: Fail if MSA depth below this
        fast_env_fallback_min_depth: For preset=fast when use_env is disabled,
            automatically run EnvDB search if UniRef-only depth is below this.
            Set to 0 to disable this fallback.
    """
    def _should_retry_direct_gpu_on_cpu(err: Exception) -> bool:
        msg = str(err).lower()
        gpu_tokens = (
            "out of memory",
            "memoryallocation",
            "working set",
            "cuda",
            "gpu",
        )
        return any(token in msg for token in gpu_tokens)

    if force_refresh and cache_only:
        raise ValueError("Invalid flags: --force_refresh cannot be combined with --cache-only")

    # Load preset configuration
    if preset not in MSA_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Options: {list(MSA_PRESETS.keys())}")
    
    config = MSA_PRESETS[preset].copy()
    print(f"MSA Preset: {preset} - {config['description']}", flush=True)
    
    # Apply overrides
    if num_iterations is not None:
        config["num_iterations"] = num_iterations
    if use_env is not None:
        config["use_env"] = bool(use_env)
    if use_expand is not None:
        config["use_expand"] = bool(use_expand)
    if use_filter is not None:
        config["use_filter"] = bool(use_filter)
    if evalue is not None:
        config["evalue"] = evalue
    if sensitivity is not None:
        config["sensitivity"] = sensitivity
    if max_seqs is not None:
        config["max_seqs"] = max(1, int(max_seqs))
    
    # Resolve DB paths before cache lookup so cache profile can reflect effective config.
    db_path = Path(db_path)
    envdb = db_path / "colabfold_envdb_202108_db"
    env_available = envdb.exists() and Path(str(envdb) + ".dbtype").exists()
    if config["use_env"] and not env_available:
        print("WARNING: Environmental DB not found, falling back to UniRef30 only", flush=True)
        config["use_env"] = False

    cache_profile = build_cache_profile(
        preset=preset,
        config=config,
        min_seq_id=min_seq_id,
        min_coverage=min_coverage,
        taxon_list=taxon_list,
    )

    # For cache: use reference sequence if provided (mutagenesis mode)
    cache_key_seq = reference_sequence or sequence
    seq_hash = compute_sequence_hash(cache_key_seq)
    
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    lock_fd = None
    
    # Acquire MSA lock for this sequence to prevent duplicate work
    lock_path = get_msa_lock_path(cache_dir, seq_hash)
    print(f"Acquiring MSA lock for {seq_hash[:16]}...", flush=True)
    lock_fd = acquire_msa_lock(lock_path)
    print("MSA lock acquired", flush=True)
    
    try:
        # Check cache (after lock to avoid race conditions)
        cache_found = False
        if not force_refresh:
            cached = check_cache(
                cache_dir=cache_dir,
                seq_hash=seq_hash,
                max_age_days=max_age_days,
                preset=preset,
                cache_profile=cache_profile,
            )
            if cached:
                cache_found = True
                cached_path, cached_profile = cached
                if cached_profile == "single":
                    print(f"CACHE HIT: {seq_hash[:16]}... (single cache)", flush=True)
                elif cached_profile == cache_profile:
                    print(f"CACHE HIT: {seq_hash[:16]}... ({cached_profile} profile)", flush=True)
                else:
                    print(
                        f"CACHE HIT: {seq_hash[:16]}... "
                        f"(requested {cache_profile}, reused {cached_profile})",
                        flush=True,
                    )
                final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
                os.makedirs(out_dir, exist_ok=True)
                load_from_cache(cached_path, final_a3m)
                
                # Generate quality report from cached MSA (sanitize to keep Boltz-safe)
                with open(final_a3m, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                content, removed_invalid_chars = sanitize_a3m_for_boltz(content)
                if removed_invalid_chars > 0:
                    with open(final_a3m, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(
                        f"Sanitized cached A3M: removed {removed_invalid_chars} invalid character(s).",
                        flush=True,
                    )
                msa_depth = content.count('\n>') + (1 if content.startswith('>') else 0)
                
                report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
                with open(report_path, 'w') as f:
                    cached_preset = cached_profile.split("_", 1)[0]
                    json.dump({
                        "msa_depth": msa_depth,
                        "query_length": len(sequence),
                        "preset": preset,
                        "cache_profile": cache_profile,
                        "cached_profile": cached_profile,
                        "cached_preset": cached_preset,
                        "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                        "selected_gpu_id": None,
                        "used_gpu_mmseqs": None,
                        "from_cache": True,
                    }, f, indent=2)
                
                release_msa_lock(lock_fd)
                print("MSA lock released", flush=True)
                return
        if cache_only and not cache_found:
            raise RuntimeError(
                "CACHE ONLY MODE: No cached MSA found for sequence hash "
                f"{seq_hash[:16]}...; disable --cache-only or run once without it."
            )
        
        print(f"CACHE MISS: {seq_hash[:16]}... ({cache_profile}; running ColabFold workflow)", flush=True)
        
        # Database paths
        mmseqs_cpu = db_path / "mmseqs" / "bin" / "mmseqs"
        mmseqs_gpu = db_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
        uniref_db = db_path / "uniref30_2302_db"
        
        # Determine which binary to use
        selected_gpu_id = None
        use_gpu_flag = False
        normalized_gpu_mode = (gpu_mode or "auto").strip().lower()
        if normalized_gpu_mode not in {"auto", "opportunistic", "required", "cpu"}:
            raise ValueError(
                f"Invalid gpu_mode='{gpu_mode}'. Choose from: auto, opportunistic, required, cpu"
            )
        normalized_gpu_server_mode = (gpu_server_mode or "persistent").strip().lower()
        if normalized_gpu_server_mode not in {"auto", "required", "persistent", "off"}:
            raise ValueError(
                f"Invalid gpu_server_mode='{gpu_server_mode}'. Choose from: auto, required, persistent, off"
            )
        effective_gpu_server_wait_timeout = int(gpu_server_wait_timeout)
        if (
            normalized_gpu_mode in {"auto", "opportunistic"}
            and effective_gpu_server_wait_timeout > 30
        ):
            effective_gpu_server_wait_timeout = 30
            print(
                "Capping gpuserver wait timeout to 30s for opportunistic/auto mode.",
                flush=True,
            )
        if gpu_server_wait_timeout < -1:
            raise ValueError("gpu_server_wait_timeout must be -1 (infinite), 0, or a positive integer")
        if cpu_only:
            normalized_gpu_mode = "cpu"
        elif use_gpu is True and normalized_gpu_mode in {"auto", "opportunistic"}:
            # Backward-compatibility: explicit --use-gpu means "required"
            normalized_gpu_mode = "required"

        scheduler_policy = load_scheduler_gpu_policy()
        persisted_pinned_gpu_id = None
        if gpu_id is None and preferred_gpus is None and not cpu_only:
            persisted_pinned_gpu_id = read_persisted_msa_pinned_gpu_id(cache_dir)
        effective_preferred_gpus = preferred_gpus
        effective_excluded_gpus = excluded_gpus

        if persisted_pinned_gpu_id is not None:
            effective_preferred_gpus = [persisted_pinned_gpu_id]
            print(
                f"MSA GPU pin from persisted server settings: {persisted_pinned_gpu_id}",
                flush=True,
            )
        elif effective_preferred_gpus is None:
            effective_preferred_gpus = scheduler_policy.get("preferred")
        if effective_excluded_gpus is None:
            effective_excluded_gpus = scheduler_policy.get("disabled")

        if effective_preferred_gpus:
            print(f"MSA GPU preferred list: {effective_preferred_gpus}", flush=True)
        if effective_excluded_gpus:
            print(f"MSA GPU excluded list: {effective_excluded_gpus}", flush=True)

        # On low-VRAM preferred GPUs, stale/conflicting persistent gpuserver
        # instances (e.g. EnvDB while we need UniRef) can force CPU fallback.
        # Reclaim those before GPU selection for opportunistic/auto runs.
        if (
            normalized_gpu_mode in {"auto", "opportunistic"}
            and normalized_gpu_server_mode == "persistent"
            and effective_preferred_gpus
        ):
            reclaimed = reclaim_conflicting_gpuserver_instances(
                target_db=uniref_db,
                preferred_gpus=effective_preferred_gpus,
                cache_dir=cache_dir,
            )
            if reclaimed > 0:
                print(
                    f"Reclaimed {reclaimed} conflicting gpuserver instance(s) before UniRef GPU selection.",
                    flush=True,
                )

        if normalized_gpu_mode == "cpu":
            mmseqs_bin = mmseqs_cpu
            print("Using CPU mmseqs (forced)", flush=True)
        elif mmseqs_gpu.exists():
            if gpu_id is not None:
                selected_gpu_id = gpu_id
            elif persisted_pinned_gpu_id is not None:
                selected_gpu_id = persisted_pinned_gpu_id
                print(
                    f"Using pinned MSA GPU {selected_gpu_id} from persisted server settings",
                    flush=True,
                )
            else:
                selected_gpu_id = check_gpu_availability(
                    threshold=gpu_threshold,
                    preferred_gpus=effective_preferred_gpus,
                    excluded_gpus=effective_excluded_gpus,
                    allow_gpuserver_memory_override=(normalized_gpu_server_mode != "off"),
                    cache_dir=cache_dir,
                    preferred_gpuserver_target_db=uniref_db,
                )
            if selected_gpu_id is not None and effective_excluded_gpus and selected_gpu_id in set(effective_excluded_gpus):
                raise RuntimeError(f"Selected gpu_id {selected_gpu_id} is excluded by policy")
            
            if selected_gpu_id is not None:
                mmseqs_bin = mmseqs_gpu
                use_gpu_flag = True
                print(f"Using GPU mmseqs on device {selected_gpu_id}", flush=True)
            else:
                if normalized_gpu_mode == "required":
                    raise RuntimeError("GPU mode is 'required' but no eligible GPU is available")
                mmseqs_bin = mmseqs_cpu
                print("GPU unavailable or busy, falling back to CPU mmseqs", flush=True)
        else:
            if normalized_gpu_mode == "required":
                raise RuntimeError(f"GPU mode is 'required' but GPU binary not found at: {mmseqs_gpu}")
            mmseqs_bin = mmseqs_cpu
            print("GPU binary unavailable, using CPU mmseqs", flush=True)

        if not use_gpu_flag:
            normalized_gpu_server_mode = "off"
        elif normalized_gpu_server_mode != "off":
            print(
                f"GPU server mode: {normalized_gpu_server_mode} "
                f"(wait_timeout={effective_gpu_server_wait_timeout}s, db_load_mode={gpu_server_db_load_mode})",
                flush=True,
            )
        
        if not Path(str(mmseqs_bin)).exists():
            mmseqs_bin = "mmseqs"  # Try system mmseqs
        
        # Environment setup
        env = os.environ.copy()
        # Keep CUDA ordinal mapping aligned with nvidia-smi GPU indices.
        env['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
        if selected_gpu_id is not None:
            env['CUDA_VISIBLE_DEVICES'] = str(selected_gpu_id)
        
        os.makedirs(out_dir, exist_ok=True)
        touch_gpuserver_query_activity(
            cache_dir=cache_dir,
            job_name=job_name,
            preset=preset,
            use_gpu=use_gpu_flag,
            gpu_id=selected_gpu_id,
        )
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            # ═══════════════════════════════════════════════════════════════════
            # STEP 1: Create query database
            # ═══════════════════════════════════════════════════════════════════
            query_fasta = os.path.join(tmp_dir, "query.fasta")
            with open(query_fasta, 'w') as f:
                f.write(f">query\n{sequence}\n")
            
            query_db = os.path.join(tmp_dir, "qdb")
            run_mmseqs(mmseqs_bin, [
                "createdb", query_fasta, query_db,
                "--shuffle", "0", "--dbtype", "1"
            ], env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 2: Iterative profile search against UniRef30
            # ═══════════════════════════════════════════════════════════════════
            print(f"Searching UniRef30 ({config['num_iterations']} iterations)...", flush=True)
            
            result_db = os.path.join(tmp_dir, "res")
            base_search_params = [
                "search", query_db, str(uniref_db), result_db,
                os.path.join(tmp_dir, "tmp"),
                "--num-iterations", str(config["num_iterations"]),
                "-a",  # Report alignments
                "-e", str(config["evalue"]),
                "--max-seqs", str(config["max_seqs"]),
            ]
            
            if use_gpu_flag:
                used_gpu_server = False
                direct_gpu_allowed = True
                if normalized_gpu_server_mode != "off":
                    try:
                        if normalized_gpu_server_mode == "persistent":
                            server_meta = ensure_persistent_mmseqs_gpuserver(
                                mmseqs_bin=mmseqs_bin,
                                target_db=uniref_db,
                                env=env,
                                max_seqs=config["max_seqs"],
                                prefilter_mode=1,
                                db_load_mode=gpu_server_db_load_mode,
                                cache_dir=cache_dir,
                                startup_wait_seconds=gpu_server_startup_wait,
                            )
                            action = "Reusing" if server_meta.get("reused") else "Started"
                            print(
                                f"{action} persistent gpuserver for {uniref_db.name} "
                                f"(pid={server_meta.get('pid')}, gpu={env.get('CUDA_VISIBLE_DEVICES', 'auto')})",
                                flush=True,
                            )
                            run_mmseqs(mmseqs_bin, base_search_params + [
                                "--db-load-mode", str(gpu_server_db_load_mode),
                                "--gpu", "1",
                                "--gpu-server", "1",
                                "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                                "--prefilter-mode", "1",
                                "--threads", str(num_threads),
                            ], env)
                        else:
                            gpuserver_log = Path(tmp_dir) / "gpuserver_uniref.log"
                            print(f"Starting gpuserver for {uniref_db.name}...", flush=True)
                            with run_mmseqs_gpuserver(
                                mmseqs_bin=mmseqs_bin,
                                target_db=uniref_db,
                                env=env,
                                max_seqs=config["max_seqs"],
                                prefilter_mode=1,
                                db_load_mode=gpu_server_db_load_mode,
                                log_path=gpuserver_log,
                                startup_wait_seconds=gpu_server_startup_wait,
                            ):
                                run_mmseqs(mmseqs_bin, base_search_params + [
                                    "--db-load-mode", str(gpu_server_db_load_mode),
                                    "--gpu", "1",
                                    "--gpu-server", "1",
                                    "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                                    "--prefilter-mode", "1",
                                    "--threads", str(num_threads),
                                ], env)
                            print(f"gpuserver completed for {uniref_db.name}", flush=True)
                        used_gpu_server = True
                    except Exception as e:
                        if normalized_gpu_server_mode == "required":
                            raise
                        if normalized_gpu_mode in {"auto", "opportunistic"}:
                            direct_gpu_allowed = False
                            use_gpu_flag = False
                            print(
                                f"WARNING: gpuserver unavailable for {uniref_db.name} ({e}). "
                                "Falling back to CPU search to avoid direct-GPU OOM.",
                                flush=True,
                            )
                        else:
                            print(
                                f"WARNING: gpuserver unavailable for {uniref_db.name} ({e}). "
                                "Falling back to direct GPU search.",
                                flush=True,
                            )
                if not used_gpu_server:
                    if direct_gpu_allowed:
                        try:
                            run_mmseqs(mmseqs_bin, base_search_params + [
                                "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                                "--gpu", "1",
                                "--prefilter-mode", "1",
                                "--threads", str(num_threads),
                            ], env)
                        except RuntimeError as e:
                            if not _should_retry_direct_gpu_on_cpu(e):
                                raise
                            print(
                                f"WARNING: Direct GPU UniRef30 search failed ({e}). Retrying on CPU.",
                                flush=True,
                            )
                            use_gpu_flag = False
                            run_mmseqs(mmseqs_bin, base_search_params + [
                                "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                                "-s", str(config["sensitivity"]),
                                "--threads", str(num_threads),
                            ], env)
                    else:
                        run_mmseqs(mmseqs_bin, base_search_params + [
                            "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                            "-s", str(config["sensitivity"]),
                            "--threads", str(num_threads),
                        ], env)
            else:
                run_mmseqs(mmseqs_bin, base_search_params + [
                    "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                    "-s", str(config["sensitivity"]),
                    "--threads", str(num_threads),
                ], env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 3: Extract/derive refined profile for environmental search
            # ═══════════════════════════════════════════════════════════════════
            profile_db = os.path.join(tmp_dir, "prof_res")
            has_profile = False
            
            # ColabFold uses profile_1 (first iteration profile), but check all iterations
            for iter_num in [1, 2, 3, config["num_iterations"]]:
                profile_source = os.path.join(tmp_dir, f"tmp/latest/profile_{iter_num}")
                if os.path.exists(profile_source + ".dbtype"):
                    print(f"Found profile at iteration {iter_num}", flush=True)
                    run_mmseqs(mmseqs_bin, ["mvdb", profile_source, profile_db], env)
                    # Link headers
                    run_mmseqs(mmseqs_bin, ["lndb", query_db + "_h", profile_db + "_h"], env)
                    has_profile = True
                    break
            
            if not has_profile:
                # GPU paths do not always emit tmp/latest/profile_*. Build profile DB directly.
                try:
                    run_mmseqs(mmseqs_bin, [
                        "result2profile", query_db, str(uniref_db), result_db, profile_db,
                        "--threads", str(num_threads),
                        "--db-load-mode", "2",
                    ], env)
                    has_profile = True
                    print("Derived profile DB from UniRef search results (result2profile)", flush=True)
                except RuntimeError as e:
                    print(
                        f"WARNING: Could not derive profile DB ({e}); using query DB for env search",
                        flush=True,
                    )
                    profile_db = query_db
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 4: Alignment expansion (Maximum preset)
            # ═══════════════════════════════════════════════════════════════════
            can_expand = False
            if config["use_expand"]:
                # Check if alignment database is valid for expansion
                aln_db = Path(str(uniref_db) + "_aln")
                aln_index = Path(str(uniref_db) + "_aln.index")
                if aln_db.exists() and aln_index.exists():
                    # Verify the aln file is larger than its index (sanity check)
                    aln_size = aln_db.stat().st_size
                    index_size = aln_index.stat().st_size
                    if aln_size > index_size:
                        can_expand = True
                    else:
                        print(f"WARNING: Alignment database appears incomplete ({aln_size} bytes < {index_size} bytes index), skipping expansion", flush=True)
                else:
                    print("WARNING: Alignment database not found, skipping expansion", flush=True)
            
            if can_expand:
                print("Expanding alignments to recover cluster members...", flush=True)
                expanded_db = os.path.join(tmp_dir, "res_exp")
                try:
                    run_mmseqs(mmseqs_bin, [
                        "expandaln", query_db, str(uniref_db) + "_seq",
                        result_db, str(uniref_db) + "_aln", expanded_db,
                        "--expansion-mode", "0",
                        "-e", "inf",
                        "--expand-filter-clusters", "1" if config["use_filter"] else "0",
                        "--max-seq-id", str(config["max_seq_id"]),
                        "--threads", str(num_threads),
                    ], env)
                    
                    # Realign expanded hits
                    realigned_db = os.path.join(tmp_dir, "res_exp_realign")
                    run_mmseqs(mmseqs_bin, [
                        "align", profile_db if has_profile else query_db, 
                        str(uniref_db) + "_seq",
                        expanded_db, realigned_db,
                        "-e", "10",
                        "--max-accept", "100000",
                        "--alt-ali", "10",
                        "-a",
                        "--threads", str(num_threads),
                    ], env)
                    result_db = realigned_db
                except RuntimeError as e:
                    print(f"WARNING: Alignment expansion failed ({e}), continuing without expansion", flush=True)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 5: Quality filtering (Maximum/Balanced presets)
            # ═══════════════════════════════════════════════════════════════════
            if config["use_filter"]:
                print("Filtering MSA by quality metrics...", flush=True)
                filtered_db = os.path.join(tmp_dir, "res_filtered")
                run_mmseqs(mmseqs_bin, [
                    "filterresult", query_db, str(uniref_db) + "_seq",
                    result_db, filtered_db,
                    "--qid", "0",
                    "--qsc", str(config["qsc"]),
                    "--diff", "0",
                    "--max-seq-id", str(config["max_seq_id"]),
                    "--filter-min-enable", "100",
                    "--threads", str(num_threads),
                ], env)
                result_db = filtered_db
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 6: Generate UniRef30 MSA
            # ═══════════════════════════════════════════════════════════════════
            uniref_a3m_db = os.path.join(tmp_dir, "uniref.a3m")
            filter_params = [
                "--filter-msa", "1" if config["use_filter"] else "0",
                "--filter-min-enable", "1000",
                "--diff", "3000",
                "--qid", "0.0,0.2,0.4,0.6,0.8,1.0",
                "--qsc", "0",
                "--max-seq-id", "0.95",
            ]
            run_mmseqs(mmseqs_bin, [
                "result2msa", query_db, str(uniref_db) + "_seq",
                result_db, uniref_a3m_db,
                "--msa-format-mode", "5",
                "--threads", str(num_threads),
                "--db-load-mode", "2",  # Preload sequence DB into RAM
            ] + filter_params, env)

            # FAST fallback: UniRef30-only can be too shallow for some proteins.
            # When depth is very low, auto-enable EnvDB search for coverage rescue.
            effective_use_env = bool(config["use_env"])
            auto_env_fallback_triggered = False
            uniref_only_depth = None
            if (
                preset == "fast"
                and not effective_use_env
                and env_available
                and fast_env_fallback_min_depth > 0
            ):
                try:
                    preview_dir = os.path.join(tmp_dir, "uniref_preview")
                    os.makedirs(preview_dir, exist_ok=True)
                    run_mmseqs(mmseqs_bin, [
                        "unpackdb", uniref_a3m_db, preview_dir,
                        "--unpack-name-mode", "0",
                        "--unpack-suffix", ".a3m",
                    ], env)
                    preview_a3m = os.path.join(preview_dir, "0.a3m")
                    if os.path.exists(preview_a3m):
                        with open(preview_a3m, "rb") as f:
                            preview_bytes = f.read()
                        preview_bytes = bytes(
                            b for b in preview_bytes if b >= 0x20 or b in (0x0A, 0x09)
                        )
                        preview_content = preview_bytes.decode("utf-8", errors="ignore")
                        uniref_only_depth = preview_content.count("\n>") + (
                            1 if preview_content.startswith(">") else 0
                        )
                except Exception as e:
                    print(
                        f"WARNING: Unable to estimate UniRef-only MSA depth for fast fallback ({e})",
                        flush=True,
                    )

                if (
                    uniref_only_depth is not None
                    and uniref_only_depth < fast_env_fallback_min_depth
                ):
                    auto_env_fallback_triggered = True
                    effective_use_env = True
                    print(
                        f"FAST fallback: UniRef30 depth {uniref_only_depth} < "
                        f"{fast_env_fallback_min_depth}; enabling EnvDB search.",
                        flush=True,
                    )
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 7: Environmental database search (Maximum/Balanced presets)
            # ═══════════════════════════════════════════════════════════════════
            env_a3m_db = None
            if effective_use_env and env_available:
                print(f"Searching environmental database ({envdb.name})...", flush=True)

                if (
                    use_gpu_flag
                    and normalized_gpu_server_mode == "persistent"
                    and effective_preferred_gpus
                ):
                    reclaimed = reclaim_conflicting_gpuserver_instances(
                        target_db=envdb,
                        preferred_gpus=effective_preferred_gpus,
                        cache_dir=cache_dir,
                    )
                    if reclaimed > 0:
                        print(
                            f"Reclaimed {reclaimed} conflicting gpuserver instance(s) before EnvDB GPU search.",
                            flush=True,
                        )
                
                env_result_db = os.path.join(tmp_dir, "res_env")
                env_base_search_params = [
                    "search", profile_db if has_profile else query_db, 
                    str(envdb), env_result_db,
                    os.path.join(tmp_dir, "tmp_env"),
                    "--num-iterations", str(config["num_iterations"]),
                    "-a", "-e", str(config["evalue"]),
                    "--max-seqs", str(config["max_seqs"]),
                ]
                
                if use_gpu_flag:
                    used_gpu_server = False
                    direct_gpu_allowed = True
                    if normalized_gpu_server_mode != "off":
                        try:
                            if normalized_gpu_server_mode == "persistent":
                                server_meta = ensure_persistent_mmseqs_gpuserver(
                                    mmseqs_bin=mmseqs_bin,
                                    target_db=envdb,
                                    env=env,
                                    max_seqs=config["max_seqs"],
                                    prefilter_mode=1,
                                    db_load_mode=gpu_server_db_load_mode,
                                    cache_dir=cache_dir,
                                    startup_wait_seconds=gpu_server_startup_wait,
                                )
                                action = "Reusing" if server_meta.get("reused") else "Started"
                                print(
                                    f"{action} persistent gpuserver for {envdb.name} "
                                    f"(pid={server_meta.get('pid')}, gpu={env.get('CUDA_VISIBLE_DEVICES', 'auto')})",
                                    flush=True,
                                )
                                run_mmseqs(mmseqs_bin, env_base_search_params + [
                                    "--db-load-mode", str(gpu_server_db_load_mode),
                                    "--gpu", "1",
                                    "--gpu-server", "1",
                                    "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                                    "--prefilter-mode", "1",
                                    "--threads", str(num_threads),
                                ], env)
                            else:
                                gpuserver_log = Path(tmp_dir) / "gpuserver_envdb.log"
                                print(f"Starting gpuserver for {envdb.name}...", flush=True)
                                with run_mmseqs_gpuserver(
                                    mmseqs_bin=mmseqs_bin,
                                    target_db=envdb,
                                    env=env,
                                    max_seqs=config["max_seqs"],
                                    prefilter_mode=1,
                                    db_load_mode=gpu_server_db_load_mode,
                                    log_path=gpuserver_log,
                                    startup_wait_seconds=gpu_server_startup_wait,
                                ):
                                    run_mmseqs(mmseqs_bin, env_base_search_params + [
                                        "--db-load-mode", str(gpu_server_db_load_mode),
                                        "--gpu", "1",
                                        "--gpu-server", "1",
                                        "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                                        "--prefilter-mode", "1",
                                        "--threads", str(num_threads),
                                    ], env)
                                print(f"gpuserver completed for {envdb.name}", flush=True)
                            used_gpu_server = True
                        except Exception as e:
                            if normalized_gpu_server_mode == "required":
                                raise
                            if normalized_gpu_mode in {"auto", "opportunistic"}:
                                direct_gpu_allowed = False
                                use_gpu_flag = False
                                print(
                                    f"WARNING: gpuserver unavailable for {envdb.name} ({e}). "
                                    "Falling back to CPU search to avoid direct-GPU OOM.",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"WARNING: gpuserver unavailable for {envdb.name} ({e}). "
                                    "Falling back to direct GPU search.",
                                    flush=True,
                                )
                    if not used_gpu_server:
                        if direct_gpu_allowed:
                            try:
                                run_mmseqs(mmseqs_bin, env_base_search_params + [
                                    "--db-load-mode", "2",  # mmap databases into RAM
                                    "--gpu", "1",
                                    "--prefilter-mode", "1",
                                    "--threads", str(num_threads),
                                ], env)
                            except RuntimeError as e:
                                if not _should_retry_direct_gpu_on_cpu(e):
                                    raise
                                print(
                                    f"WARNING: Direct GPU EnvDB search failed ({e}). Retrying on CPU.",
                                    flush=True,
                                )
                                use_gpu_flag = False
                                run_mmseqs(mmseqs_bin, env_base_search_params + [
                                    "--db-load-mode", "2",  # mmap databases into RAM
                                    "-s", str(config["sensitivity"]),
                                    "--threads", str(num_threads),
                                ], env)
                        else:
                            run_mmseqs(mmseqs_bin, env_base_search_params + [
                                "--db-load-mode", "2",  # mmap databases into RAM
                                "-s", str(config["sensitivity"]),
                                "--threads", str(num_threads),
                            ], env)
                else:
                    run_mmseqs(mmseqs_bin, env_base_search_params + [
                        "--db-load-mode", "2",  # mmap databases into RAM
                        "-s", str(config["sensitivity"]),
                        "--threads", str(num_threads),
                    ], env)
                
                # Expand environmental hits if enabled
                if config["use_expand"]:
                    env_expanded = os.path.join(tmp_dir, "res_env_exp")
                    run_mmseqs(mmseqs_bin, [
                        "expandaln", profile_db if has_profile else query_db,
                        str(envdb) + "_seq",
                        env_result_db, str(envdb) + "_aln", env_expanded,
                        "-e", "inf",
                        "--expansion-mode", "0",
                        "--threads", str(num_threads),
                    ], env)
                    
                    # Realign expanded environmental hits
                    env_tmp_dir = os.path.join(tmp_dir, "tmp_env")
                    env_profile = os.path.join(env_tmp_dir, f"latest/profile_{config['num_iterations']}")
                    if os.path.exists(env_profile + ".dbtype"):
                        align_profile = env_profile
                    else:
                        align_profile = profile_db if has_profile else query_db
                    
                    env_realigned = os.path.join(tmp_dir, "res_env_realign")
                    run_mmseqs(mmseqs_bin, [
                        "align", align_profile, str(envdb) + "_seq",
                        env_expanded, env_realigned,
                        "-e", "10",
                        "--max-accept", "100000",
                        "--alt-ali", "10",
                        "-a",
                        "--threads", str(num_threads),
                    ], env)
                    env_result_db = env_realigned
                
                # Filter environmental results
                if config["use_filter"]:
                    env_filtered = os.path.join(tmp_dir, "res_env_filtered")
                    run_mmseqs(mmseqs_bin, [
                        "filterresult", query_db, str(envdb) + "_seq",
                        env_result_db, env_filtered,
                        "--qid", "0",
                        "--qsc", str(config["qsc"]),
                        "--diff", "0",
                        "--max-seq-id", str(config["max_seq_id"]),
                        "--filter-min-enable", "100",
                        "--threads", str(num_threads),
                    ], env)
                    env_result_db = env_filtered
                
                # Generate Environmental MSA
                env_a3m_db = os.path.join(tmp_dir, "env.a3m")
                run_mmseqs(mmseqs_bin, [
                    "result2msa", query_db, str(envdb) + "_seq",
                    env_result_db, env_a3m_db,
                    "--msa-format-mode", "5",
                    "--threads", str(num_threads),
                    "--db-load-mode", "2",  # Preload sequence DB into RAM
                ] + filter_params, env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 8: Merge MSAs
            # ═══════════════════════════════════════════════════════════════════
            if env_a3m_db and os.path.exists(env_a3m_db + ".dbtype"):
                print("Merging UniRef30 and Environmental MSAs...", flush=True)
                final_a3m_db = os.path.join(tmp_dir, "final.a3m")
                run_mmseqs(mmseqs_bin, [
                    "mergedbs", query_db, final_a3m_db,
                    uniref_a3m_db, env_a3m_db
                ], env)
            else:
                final_a3m_db = uniref_a3m_db
            
            # Unpack to final A3M file
            run_mmseqs(mmseqs_bin, [
                "unpackdb", final_a3m_db, tmp_dir,
                "--unpack-name-mode", "0",
                "--unpack-suffix", ".a3m"
            ], env)
            
            # Read unpacked A3M (uses query ID as filename)
            unpacked_a3m = os.path.join(tmp_dir, "0.a3m")
            if os.path.exists(unpacked_a3m):
                with open(unpacked_a3m, 'rb') as f:
                    a3m_bytes = f.read()
                # Remove ALL control characters except newline (\x0a) and tab (\x09)
                a3m_bytes = bytes(b for b in a3m_bytes if b >= 0x20 or b in (0x0a, 0x09))
                a3m_content = a3m_bytes.decode('utf-8', errors='ignore')
            else:
                # Fallback: try to read directly
                result = subprocess.run([
                    str(mmseqs_bin), "result2flat",
                    query_db, query_db, final_a3m_db, "/dev/stdout"
                ], env=env, capture_output=True)
                # Remove ALL control characters except newline and tab
                a3m_bytes = bytes(b for b in result.stdout if b >= 0x20 or b in (0x0a, 0x09))
                a3m_content = a3m_bytes.decode('utf-8', errors='ignore')

            # Ensure final A3M is compatible with downstream parsers (Boltz).
            a3m_content, removed_invalid_chars = sanitize_a3m_for_boltz(a3m_content)
            if removed_invalid_chars > 0:
                print(
                    f"Sanitized A3M: removed {removed_invalid_chars} invalid character(s).",
                    flush=True,
                )
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 9: Post-processing (taxonomy filter, quality check)
            # ═══════════════════════════════════════════════════════════════════
            
            # Apply taxonomy post-filter if specified
            if taxon_list:
                target_taxids = set(taxon_list.split(','))
                domain_map = {
                    '2': 'Bacteria',
                    '2157': 'Archaea',
                    '2759': 'Eukaryota',
                    '10239': 'Viruses',
                }
                filter_domains = {domain_map.get(tid) for tid in target_taxids if tid in domain_map}
                
                if filter_domains:
                    print(f"Post-filtering MSA by taxonomy: {filter_domains}", flush=True)
                    filtered_entries = []
                    current_entry = []
                    
                    for line in a3m_content.split('\n'):
                        if line.startswith('>'):
                            if current_entry:
                                header = current_entry[0]
                                if header == '>query' or 'query' in header.lower()[:20]:
                                    filtered_entries.append('\n'.join(current_entry))
                                else:
                                    tax_match = re.search(r'Tax=([^T]+?)(?:TaxID=|$)', header)
                                    if tax_match:
                                        tax_name = tax_match.group(1).strip().lower()
                                        is_bacteria = any(kw in tax_name for kw in [
                                            'bacteri', 'escherichia', 'salmonella', 'streptococcus',
                                            'staphylococcus', 'pseudomonas', 'clostridium', 'bacillus'
                                        ])
                                        if 'Bacteria' in filter_domains and is_bacteria:
                                            filtered_entries.append('\n'.join(current_entry))
                                        elif 'Bacteria' not in filter_domains:
                                            filtered_entries.append('\n'.join(current_entry))
                                    else:
                                        filtered_entries.append('\n'.join(current_entry))
                            current_entry = [line]
                        else:
                            current_entry.append(line)
                    
                    if current_entry:
                        filtered_entries.append('\n'.join(current_entry))
                    
                    original_count = a3m_content.count('\n>') + 1
                    a3m_content = '\n'.join(filtered_entries)
                    print(f"Taxonomy filter: {original_count} -> {len(filtered_entries)} sequences", flush=True)
            
            # Count MSA depth
            msa_depth = a3m_content.count('\n>') + (1 if a3m_content.startswith('>') else 0)
            print(f"Final MSA depth: {msa_depth} sequences", flush=True)
            
            # Quality report
            quality_report = {
                "msa_depth": msa_depth,
                "query_length": len(sequence),
                "preset": preset,
                "cache_profile": cache_profile,
                "num_iterations": config["num_iterations"],
                "use_env_requested": bool(config["use_env"]),
                "use_env_effective": bool(effective_use_env),
                "auto_env_fallback_triggered": bool(auto_env_fallback_triggered),
                "fast_env_fallback_min_depth": int(max(0, fast_env_fallback_min_depth)),
                "uniref_only_depth": uniref_only_depth,
                "use_expand": config["use_expand"],
                "use_filter": config["use_filter"],
                "evalue": config["evalue"],
                "sensitivity": config["sensitivity"],
                "taxon_filter": taxon_list,
                "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                "selected_gpu_id": selected_gpu_id,
                "used_gpu_mmseqs": use_gpu_flag,
                "from_cache": False,
            }
            
            # Check depth thresholds
            if min_depth_fail > 0 and msa_depth < min_depth_fail:
                error_msg = (
                    f"MSA FAILED: Only {msa_depth} sequences found (minimum: {min_depth_fail}). "
                    f"Consider: 1) Different preset, 2) Relaxing filters, 3) Checking sequence."
                )
                print(f"ERROR: {error_msg}", flush=True)
                raise RuntimeError(error_msg)
            
            if msa_depth < min_depth_warning:
                print(
                    f"WARNING: MSA has only {msa_depth} sequences (recommended >{min_depth_warning}). "
                    f"Structure prediction confidence may be low.",
                    flush=True
                )
            
            # Write output
            final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
            with open(final_a3m, 'w') as f:
                f.write(a3m_content)
            
            report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
            with open(report_path, 'w') as f:
                json.dump(quality_report, f, indent=2)
            print(f"MSA quality report: {report_path}", flush=True)
            
            print(f"MSA generated: {final_a3m}", flush=True)
            
            # Save to cache
            if cache_dir:
                save_to_cache(cache_dir, seq_hash, a3m_content, cache_profile)
    
    finally:
        if lock_fd is not None:
            release_msa_lock(lock_fd)
            print("MSA lock released", flush=True)


# Backward compatibility alias
run_local_mmseqs2 = run_colabfold_msa_workflow


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate MSA using full ColabFold workflow (GPU/CPU hybrid)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quality Presets:
  maximum   Full ColabFold workflow with environmental DB (~15-30s)
  balanced  Environmental search without expansion (~8-15s) [DEFAULT]
  fast      UniRef30 only, minimal processing (~3-5s)
"""
    )
    parser.add_argument("--sequence", required=True, help="Amino acid sequence")
    parser.add_argument("--name", required=True, help="Job name for output files")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--db_path", 
                        default=DEFAULT_DB_PATH,
                        help="Path to ColabFold database directory")
    parser.add_argument("--cache_dir",
                        default=DEFAULT_CACHE_DIR,
                        help="Cache directory")
    parser.add_argument("--max_age_days", type=int, default=0, 
                        help="Cache expiry in days (0 = never expire)")
    parser.add_argument("--force_refresh", action="store_true", 
                        help="Bypass cache")
    parser.add_argument("--cache-only", action="store_true",
                        help="Use only existing cache; fail if cache is missing")
    parser.add_argument("--threads", type=int, default=32, 
                        help="CPU threads for MMseqs2")
    parser.add_argument("--use-gpu", action="store_true",
                        help="Force GPU mode")
    parser.add_argument("--gpu-id", type=int, default=None,
                        help="Specific GPU device ID")
    parser.add_argument("--cpu-only", action="store_true",
                        help="Force CPU mode (no GPU)")
    parser.add_argument("--gpu-mode", type=str, default="auto",
                        choices=["auto", "opportunistic", "required", "cpu"],
                        help="GPU policy: auto|opportunistic|required|cpu")
    parser.add_argument("--gpu-threshold", type=int, default=80,
                        help="Max util/memory %% for opportunistic GPU selection (default: 80)")
    parser.add_argument("--preferred-gpus", type=str, default=None,
                        help="Comma-separated preferred GPU IDs for MSA (e.g., 1,2)")
    parser.add_argument("--excluded-gpus", type=str, default=None,
                        help="Comma-separated GPU IDs to avoid for MSA (e.g., 0)")
    parser.add_argument("--gpu-server-mode", type=str, default="persistent",
                        choices=["auto", "required", "persistent", "off"],
                        help="MMseqs gpuserver policy: persistent|auto|required|off")
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=120,
                        help="Seconds to wait for gpuserver handshake (0=no wait, -1=infinite)")
    parser.add_argument("--gpu-server-db-load-mode", type=int, default=0, choices=[0, 1, 2, 3],
                        help="MMseqs db-load-mode for gpuserver-backed searches (default: 0)")
    parser.add_argument("--gpu-server-startup-wait", type=float, default=1.0,
                        help="Seconds to wait after starting gpuserver before first search")
    parser.add_argument("--msa-provider", type=str, default="local",
                        choices=["local", "colabfold_api"],
                        help="MSA backend provider: local MMseqs2 or remote ColabFold API")
    parser.add_argument("--colabfold-api-host", type=str, default=DEFAULT_COLABFOLD_API_HOST,
                        help="ColabFold API host URL (default: https://api.colabfold.com)")
    parser.add_argument("--colabfold-api-min-interval", type=float, default=6.0,
                        help="Minimum seconds between remote ColabFold API submits")
    parser.add_argument("--colabfold-api-poll-interval", type=float, default=6.0,
                        help="Polling interval seconds for remote ColabFold API ticket status")
    parser.add_argument("--reference-sequence", type=str, default=None,
                        help="Reference sequence for cache key (mutagenesis mode)")
    
    # Quality Presets
    parser.add_argument("--preset", type=str, default="balanced",
                        choices=["maximum", "balanced", "fast"],
                        help="MSA quality preset (default: balanced)")
    
    # Override parameters
    parser.add_argument("--num-iterations", type=int, default=None,
                        help="Override: number of profile iterations")
    parser.add_argument("--use-env", type=int, default=None, choices=[0, 1],
                        help="Override: use environmental database")
    parser.add_argument("--use-expand", type=int, default=None, choices=[0, 1],
                        help="Override: use alignment expansion")
    parser.add_argument("--use-filter", type=int, default=None, choices=[0, 1],
                        help="Override: use quality filtering")
    
    # Legacy parameters (backward compat)
    parser.add_argument("--evalue", type=float, default=None,
                        help="Override: E-value threshold")
    parser.add_argument("--sensitivity", type=float, default=None,
                        help="Override: MMseqs2 sensitivity (1-8)")
    parser.add_argument("--max-seqs", type=int, default=None,
                        help="Override: maximum candidate sequences to retain")
    parser.add_argument("--min-seq-id", type=float, default=None,
                        help="Minimum sequence identity (0-1.0)")
    parser.add_argument("--min-coverage", type=float, default=None,
                        help="Minimum query coverage (0-1.0)")
    parser.add_argument("--taxon-list", type=str, default=None,
                        help="NCBI taxonomy IDs to filter (comma-separated)")
    parser.add_argument("--min-depth-warning", type=int, default=100,
                        help="Warn if MSA has fewer sequences (default: 100)")
    parser.add_argument("--min-depth-fail", type=int, default=0,
                        help="Fail if MSA has fewer sequences (0 = no fail)")
    parser.add_argument("--fast-env-fallback-min-depth", type=int, default=25,
                        help="For preset=fast with use_env disabled, auto-run EnvDB when UniRef depth is below this (0 disables fallback)")
    
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    common_kwargs = dict(
        sequence=args.sequence,
        job_name=args.name,
        out_dir=args.out_dir,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
        max_age_days=args.max_age_days,
        force_refresh=args.force_refresh,
        cache_only=args.cache_only,
        num_threads=args.threads,
        use_gpu=args.use_gpu if args.use_gpu else None,
        gpu_id=args.gpu_id,
        cpu_only=args.cpu_only,
        gpu_mode=args.gpu_mode,
        gpu_threshold=args.gpu_threshold,
        preferred_gpus=parse_gpu_csv(args.preferred_gpus),
        excluded_gpus=parse_gpu_csv(args.excluded_gpus),
        gpu_server_mode=args.gpu_server_mode,
        gpu_server_wait_timeout=args.gpu_server_wait_timeout,
        gpu_server_db_load_mode=args.gpu_server_db_load_mode,
        gpu_server_startup_wait=args.gpu_server_startup_wait,
        reference_sequence=args.reference_sequence,
        preset=args.preset,
        num_iterations=args.num_iterations,
        use_env=bool(args.use_env) if args.use_env is not None else None,
        use_expand=bool(args.use_expand) if args.use_expand is not None else None,
        use_filter=bool(args.use_filter) if args.use_filter is not None else None,
        evalue=args.evalue,
        sensitivity=args.sensitivity,
        max_seqs=args.max_seqs,
        min_seq_id=args.min_seq_id,
        min_coverage=args.min_coverage,
        taxon_list=args.taxon_list,
        min_depth_warning=args.min_depth_warning,
        min_depth_fail=args.min_depth_fail,
        fast_env_fallback_min_depth=args.fast_env_fallback_min_depth,
    )

    if args.msa_provider == "colabfold_api":
        run_colabfold_api_msa_workflow(
            **common_kwargs,
            colabfold_api_host=args.colabfold_api_host,
            colabfold_api_min_interval=args.colabfold_api_min_interval,
            colabfold_api_poll_interval=args.colabfold_api_poll_interval,
        )
    else:
        run_colabfold_msa_workflow(**common_kwargs)

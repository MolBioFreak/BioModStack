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
from typing import TYPE_CHECKING, Optional, Dict, Any, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa.db_integrity import validate_alignment_index_keyspace
from local_msa.mmseqs_stage_report import command_report, effective_gpu_stages
from local_msa.providers.colabfold_api import register_legacy_run_colabfold_api_msa_workflow
from local_msa.providers.local_mmseqs import register_legacy_run_colabfold_msa_workflow
from local_msa.sharding import (
    DEFAULT_TARGET_SHARD_MIN_SIZE_GB,
    DEFAULT_TARGET_SHARDS,
    build_target_shard_plan_from_gb,
    run_native_target_split_search,
)

if TYPE_CHECKING:
    from lib.local_msa.cli.run_single import dispatch_single_request as _dispatch_single_request_ast_check

from local_msa_runtime import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    GPUSERVER_DB_LOAD_MODE_CHOICES,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    DEFAULT_MSA_SERVER_STATUS_URL,
    is_isolated_task_runtime,
    is_matching_gpuserver_process,
    normalize_gpuserver_db_load_mode,
    normalize_gpuserver_startup_wait,
    normalize_gpuserver_wait_timeout,
    query_host_gpuserver_status,
)

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


def _mmseqs_prefix_status(prefix: str | Path) -> Dict[str, Any]:
    prefix = Path(prefix)
    dbtype = Path(str(prefix) + ".dbtype")
    index = Path(str(prefix) + ".index")
    return {
        "path": str(prefix),
        "exists": prefix.exists(),
        "dbtype_exists": dbtype.exists(),
        "index_exists": index.exists(),
        "ready": prefix.exists() and dbtype.exists(),
    }


def build_runtime_db_integrity_preflight(
    db_path: str | Path,
    *,
    use_env: bool,
    use_expand: bool,
) -> Dict[str, Any]:
    """Lightweight runtime DB preflight for local ColabFold/MMseqs runs.

    The full forensic validator scans entire multi-GB index files. This runtime
    gate stays cheap enough for every MSA request: it checks required prefixes,
    dbtype/index presence, and only samples alignment keyspace when expansion is
    actually requested.
    """
    root = Path(db_path)
    issues: List[str] = []
    families: Dict[str, Dict[str, Any]] = {}
    required_families = ["uniref30_2302_db"]
    if use_env:
        required_families.append("colabfold_envdb_202108_db")

    labels = {
        "uniref30_2302_db": "UniRef",
        "colabfold_envdb_202108_db": "EnvDB",
    }

    for family in required_families:
        label = labels.get(family, family)
        target = root / family
        sequence = root / f"{family}_seq"
        alignment = root / f"{family}_aln"
        target_status = _mmseqs_prefix_status(target)
        sequence_status = _mmseqs_prefix_status(sequence)
        alignment_status = _mmseqs_prefix_status(alignment)
        family_issues: List[str] = []

        if not target_status["ready"]:
            family_issues.append(f"{label} target DB prefix is missing")
        if not target_status["index_exists"]:
            family_issues.append(f"{label} target DB index is missing")
        if not sequence_status["ready"]:
            family_issues.append(f"{label} sequence DB prefix is missing")
        if not sequence_status["index_exists"]:
            family_issues.append(f"{label} sequence DB index is missing")

        alignment_keyspace_compatible: Optional[bool] = None
        alignment_keyspace_reason: Optional[str] = None
        if use_expand:
            if not alignment_status["ready"]:
                family_issues.append(f"{label} alignment DB prefix is missing")
            if not alignment_status["index_exists"]:
                family_issues.append(f"{label} alignment DB index is missing")
            if alignment_status["ready"] and alignment_status["index_exists"]:
                validation = validate_alignment_index_keyspace(target, alignment)
                alignment_keyspace_compatible = bool(validation.compatible)
                alignment_keyspace_reason = validation.reason
                if not validation.compatible:
                    family_issues.append(f"{label} alignment DB keyspace validation failed: {validation.reason}")

        issues.extend(family_issues)
        families[family] = {
            "label": label,
            "target": target_status,
            "sequence": sequence_status,
            "alignment": alignment_status,
            "target_db_ready": bool(target_status["ready"]),
            "sequence_db_ready": bool(sequence_status["ready"]),
            "alignment_db_ready": bool(alignment_status["ready"]),
            "alignment_keyspace_compatible": alignment_keyspace_compatible,
            "alignment_keyspace_reason": alignment_keyspace_reason,
            "issues": family_issues,
        }

    return {
        "checked": True,
        "db_root": str(root),
        "required_families": required_families,
        "use_env_required": bool(use_env),
        "use_expand_required": bool(use_expand),
        "compatible": not issues,
        "issues": issues,
        "families": families,
    }


def write_runtime_db_integrity_preflight(
    out_dir: str | Path,
    job_name: str,
    report: Dict[str, Any],
) -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    report_path = Path(out_dir) / f"{job_name}_local_db_integrity.json"
    report["report_path"] = str(report_path)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    return str(report_path)


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


def resolve_mmseqs_binaries(db_path: str | Path) -> tuple[Path, Path | None]:
    """Resolve CPU and GPU MMseqs binaries from the configured DB root."""
    db_root = Path(db_path)
    mmseqs_cpu = db_root / "mmseqs" / "bin" / "mmseqs"
    gpu_candidates = [
        db_root / "mmseqs-gpu-blackwell" / "bin" / "mmseqs",
        db_root / "mmseqs-gpu" / "bin" / "mmseqs",
    ]
    mmseqs_gpu = next((candidate for candidate in gpu_candidates if candidate.exists()), None)
    return mmseqs_cpu, mmseqs_gpu


GPU_TARGET_DB_SUFFIXES = ("_gpu", "_padded", "_paddedseq")


def _mmseqs_db_prefix_ready(prefix: str | Path) -> bool:
    prefix = Path(prefix)
    return prefix.exists() and Path(str(prefix) + ".dbtype").exists()


def resolve_mmseqs_gpu_target_db(target_db: str | Path) -> Optional[Path]:
    """Return the padded MMseqs GPU-search target DB for a logical target DB.

    MMseqs GPU search does not accept an ordinary createdb/search DB as the
    target; the target must be prepared with `mmseqs makepaddedseqdb`. Empty
    `<target>.GPU_READY` marker files are therefore not sufficient evidence.
    """
    target = Path(target_db)
    for suffix in GPU_TARGET_DB_SUFFIXES:
        candidate = Path(str(target) + suffix)
        if _mmseqs_db_prefix_ready(candidate):
            return candidate
    return None


def describe_mmseqs_gpu_target_db(target_db: str | Path) -> Dict[str, Any]:
    target = Path(target_db)
    resolved = resolve_mmseqs_gpu_target_db(target)
    candidates = [str(Path(str(target) + suffix)) for suffix in GPU_TARGET_DB_SUFFIXES]
    return {
        "logical_target_db": str(target),
        "gpu_target_db": str(resolved) if resolved else None,
        "ready": resolved is not None,
        "candidate_prefixes": candidates,
        "gpu_ready_marker": str(Path(str(target) + ".GPU_READY")),
        "gpu_ready_marker_exists": Path(str(target) + ".GPU_READY").exists(),
        "required_command": f"mmseqs makepaddedseqdb {target} {target}_gpu",
    }


def _replace_search_target_db(params: List[str], target_db: Path) -> List[str]:
    updated = [str(part) for part in params]
    if len(updated) < 3 or updated[0] != "search":
        raise ValueError("Expected MMseqs search params with target DB at argv[2]")
    updated[2] = str(target_db)
    return updated


def _read_mmseqs_index_rows(index_path: str | Path) -> List[Tuple[int, int, int, List[str]]]:
    rows: List[Tuple[int, int, int, List[str]]] = []
    for line_number, line in enumerate(Path(index_path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise RuntimeError(f"Invalid MMseqs index row {line_number} in {index_path}: {line!r}")
        try:
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3:]))
        except ValueError as exc:
            raise RuntimeError(f"Invalid MMseqs index integers at row {line_number} in {index_path}: {line!r}") from exc
    return rows


def _collect_alignment_result_target_keys(record: bytes) -> set[int]:
    keys: set[int] = set()
    body = record[:-1] if record.endswith(b"\x00") else record
    for raw_line in body.split(b"\n"):
        if not raw_line:
            continue
        target_token = raw_line.split(b"\t", 1)[0]
        if not target_token:
            continue
        try:
            keys.add(int(target_token))
        except ValueError as exc:
            raise RuntimeError(
                f"Cannot parse MMseqs alignment-result target key from line prefix {raw_line[:80]!r}"
            ) from exc
    return keys


def _remap_alignment_result_record(record: bytes, key_map: Dict[int, int]) -> Tuple[bytes, int]:
    trailing_nul = b"\x00" if record.endswith(b"\x00") else b""
    body = record[:-1] if trailing_nul else record
    remapped_hits = 0
    remapped_lines: List[bytes] = []
    for raw_line in body.split(b"\n"):
        if not raw_line:
            remapped_lines.append(raw_line)
            continue
        parts = raw_line.split(b"\t", 1)
        if len(parts) != 2:
            raise RuntimeError(f"Invalid MMseqs alignment-result line without tab separator: {raw_line[:80]!r}")
        try:
            gpu_key = int(parts[0])
        except ValueError as exc:
            raise RuntimeError(
                f"Cannot parse MMseqs alignment-result target key from line prefix {raw_line[:80]!r}"
            ) from exc
        logical_key = key_map.get(gpu_key)
        if logical_key is None:
            raise RuntimeError(f"GPU target lookup is missing target key {gpu_key}")
        remapped_lines.append(str(logical_key).encode("ascii") + b"\t" + parts[1])
        remapped_hits += 1
    return b"\n".join(remapped_lines) + trailing_nul, remapped_hits


def _load_gpu_lookup_filenumber_map(lookup_path: str | Path, required_keys: set[int]) -> Dict[int, int]:
    mapping: Dict[int, int] = {}
    lookup_path = Path(lookup_path)
    with lookup_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if len(mapping) == len(required_keys):
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            try:
                gpu_key = int(parts[0])
            except ValueError:
                continue
            if gpu_key not in required_keys:
                continue
            try:
                mapping[gpu_key] = int(parts[2])
            except ValueError as exc:
                raise RuntimeError(
                    f"Invalid logical fileNumber for GPU lookup key {gpu_key} at {lookup_path}:{line_number}: {parts[2]!r}"
                ) from exc
    missing = sorted(required_keys - set(mapping))
    if missing:
        preview = ", ".join(str(key) for key in missing[:10])
        plural = "s" if len(missing) != 1 else ""
        raise RuntimeError(
            f"GPU target lookup {lookup_path} is missing {len(missing)} target key{plural}: {preview}"
        )
    return mapping


def remap_mmseqs_result_target_keys_from_gpu_lookup(
    *,
    result_db: str | Path,
    gpu_target_db: str | Path,
    output_db: str | Path,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Rewrite MMseqs alignment-result target IDs from padded-GPU to logical keyspace.

    `mmseqs makepaddedseqdb` creates a GPU target DB whose numeric target IDs
    are remapped. Search results against that padded target therefore cannot be
    consumed directly by ColabFold's logical `<target>_seq` / `<target>_aln`
    stages. The padded target `.lookup` stores the original logical target key
    in its third `fileNumber` column; rewrite the first column of each alignment
    result row through that mapping before result2profile/expandaln/result2msa.
    """
    result_db = Path(result_db)
    gpu_target_db = Path(gpu_target_db)
    output_db = Path(output_db)
    if result_db.resolve() == output_db.resolve():
        raise RuntimeError("Refusing to remap MMseqs result DB in place")

    index_path = Path(str(result_db) + ".index")
    dbtype_path = Path(str(result_db) + ".dbtype")
    lookup_path = Path(str(gpu_target_db) + ".lookup")
    if not result_db.exists():
        raise RuntimeError(f"MMseqs result DB is missing: {result_db}")
    if not index_path.exists():
        raise RuntimeError(f"MMseqs result DB index is missing: {index_path}")
    if not dbtype_path.exists():
        raise RuntimeError(f"MMseqs result DB dbtype is missing: {dbtype_path}")
    if not lookup_path.exists():
        raise RuntimeError(f"GPU target lookup is missing: {lookup_path}")

    rows = _read_mmseqs_index_rows(index_path)
    required_keys: set[int] = set()
    source_bytes = result_db.read_bytes()
    records: List[Tuple[int, bytes, List[str]]] = []
    for query_key, offset, length, extra in rows:
        record = source_bytes[offset: offset + length]
        if len(record) != length:
            raise RuntimeError(
                f"MMseqs result DB record for query {query_key} is truncated: expected {length} bytes, got {len(record)}"
            )
        required_keys.update(_collect_alignment_result_target_keys(record))
        records.append((query_key, record, extra))

    key_map = _load_gpu_lookup_filenumber_map(lookup_path, required_keys) if required_keys else {}
    output_db.parent.mkdir(parents=True, exist_ok=True)
    output_index_rows: List[str] = []
    output_payload = bytearray()
    remapped_hits = 0
    for query_key, record, extra in records:
        remapped_record, record_remapped_hits = _remap_alignment_result_record(record, key_map)
        offset = len(output_payload)
        output_payload.extend(remapped_record)
        remapped_hits += record_remapped_hits
        extra_columns = "" if not extra else "\t" + "\t".join(extra)
        output_index_rows.append(f"{query_key}\t{offset}\t{len(remapped_record)}{extra_columns}\n")

    output_db.write_bytes(bytes(output_payload))
    Path(str(output_db) + ".index").write_text("".join(output_index_rows), encoding="utf-8")
    shutil.copyfile(dbtype_path, Path(str(output_db) + ".dbtype"))
    return {
        "stage": stage,
        "result_db": str(result_db),
        "gpu_target_db": str(gpu_target_db),
        "lookup_path": str(lookup_path),
        "output_db": str(output_db),
        "result_records": len(records),
        "target_hits": remapped_hits,
        "unique_target_keys": len(required_keys),
        "remapped_hits": remapped_hits,
    }


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


def inspect_mmseqs_runtime(
    *,
    db_path: str | Path,
    cache_dir: Optional[str],
    use_gpu: bool | None = None,
    gpu_id: int | None = None,
    cpu_only: bool = False,
    gpu_mode: str = "auto",
    gpu_threshold: int = 80,
    preferred_gpus: Optional[List[int]] = None,
    excluded_gpus: Optional[List[int]] = None,
    gpu_server_mode: str = "persistent",
    gpu_server_wait_timeout: int = DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    gpu_server_db_load_mode: int = DEFAULT_GPUSERVER_DB_LOAD_MODE,
    gpu_server_startup_wait: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Inspect whether this local MSA request will run on GPU or CPU."""
    mmseqs_cpu, mmseqs_gpu = resolve_mmseqs_binaries(db_path)
    db_root = Path(db_path)
    uniref_db = db_root / "uniref30_2302_db"

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

    effective_gpu_server_wait_timeout = normalize_gpuserver_wait_timeout(gpu_server_wait_timeout)
    normalized_gpu_server_db_load_mode = normalize_gpuserver_db_load_mode(gpu_server_db_load_mode)
    normalized_gpu_server_startup_wait = normalize_gpuserver_startup_wait(gpu_server_startup_wait)
    gpu_server_db_load_mode = normalized_gpu_server_db_load_mode
    gpu_server_startup_wait = normalized_gpu_server_startup_wait
    isolated_task_context = is_isolated_task_runtime()
    if isolated_task_context and normalized_gpu_server_mode == "persistent" and verbose:
        print(
            "Detected isolated task runtime; will reuse host gpuserver when available and avoid task-local persistent metadata.",
            flush=True,
        )
    if cpu_only:
        normalized_gpu_mode = "cpu"
    elif use_gpu is True and normalized_gpu_mode in {"auto", "opportunistic"}:
        normalized_gpu_mode = "required"

    scheduler_policy = load_scheduler_gpu_policy()
    persisted_pinned_gpu_id = None
    if gpu_id is None and preferred_gpus is None and not cpu_only:
        persisted_pinned_gpu_id = read_persisted_msa_pinned_gpu_id(cache_dir)

    effective_preferred_gpus = list(preferred_gpus) if preferred_gpus else None
    effective_excluded_gpus = list(excluded_gpus) if excluded_gpus else None
    ignored_pinned_gpu_id = None

    if persisted_pinned_gpu_id is not None:
        if effective_excluded_gpus and persisted_pinned_gpu_id in set(effective_excluded_gpus):
            ignored_pinned_gpu_id = persisted_pinned_gpu_id
            if verbose:
                print(
                    f"Ignoring persisted MSA GPU pin {persisted_pinned_gpu_id} because it is excluded by policy.",
                    flush=True,
                )
            persisted_pinned_gpu_id = None
        else:
            effective_preferred_gpus = [persisted_pinned_gpu_id]
            if verbose:
                print(
                    f"MSA GPU pin from persisted server settings: {persisted_pinned_gpu_id}",
                    flush=True,
                )
    elif effective_preferred_gpus is None:
        effective_preferred_gpus = scheduler_policy.get("preferred")

    if effective_excluded_gpus is None:
        effective_excluded_gpus = scheduler_policy.get("disabled")

    if effective_preferred_gpus and verbose:
        print(f"MSA GPU preferred list: {effective_preferred_gpus}", flush=True)
    if effective_excluded_gpus and verbose:
        print(f"MSA GPU excluded list: {effective_excluded_gpus}", flush=True)

    reclaimed_gpuserver_instances = 0
    if (
        normalized_gpu_mode in {"auto", "opportunistic"}
        and normalized_gpu_server_mode == "persistent"
        and effective_preferred_gpus
    ):
        reclaimed_gpuserver_instances = reclaim_conflicting_gpuserver_instances(
            target_db=uniref_db,
            preferred_gpus=effective_preferred_gpus,
            cache_dir=cache_dir,
        )
        if reclaimed_gpuserver_instances > 0 and verbose:
            print(
                f"Reclaimed {reclaimed_gpuserver_instances} conflicting gpuserver instance(s) before UniRef GPU selection.",
                flush=True,
            )

    runtime: Dict[str, Any] = {
        "status": "unknown",
        "normalized_gpu_mode": normalized_gpu_mode,
        "normalized_gpu_server_mode": normalized_gpu_server_mode,
        "effective_gpu_server_wait_timeout": effective_gpu_server_wait_timeout,
        "gpu_server_db_load_mode": normalized_gpu_server_db_load_mode,
        "gpu_server_startup_wait": normalized_gpu_server_startup_wait,
        "isolated_task_context": isolated_task_context,
        "persisted_pinned_gpu_id": persisted_pinned_gpu_id,
        "ignored_pinned_gpu_id": ignored_pinned_gpu_id,
        "effective_preferred_gpus": list(effective_preferred_gpus) if effective_preferred_gpus else [],
        "effective_excluded_gpus": list(effective_excluded_gpus) if effective_excluded_gpus else [],
        "reclaimed_gpuserver_instances": reclaimed_gpuserver_instances,
        "cpu_binary_path": str(mmseqs_cpu),
        "gpu_binary_path": str(mmseqs_gpu) if mmseqs_gpu else None,
        "gpu_binary_exists": bool(mmseqs_gpu),
        "mmseqs_bin": str(mmseqs_cpu),
        "selected_gpu_id": None,
        "use_gpu_mmseqs": False,
        "failure_reason": None,
        "failure_message": None,
        "summary_message": None,
    }

    if normalized_gpu_mode == "cpu":
        runtime["status"] = "cpu_forced"
        runtime["summary_message"] = "Using CPU mmseqs (forced)"
        return runtime

    if not mmseqs_gpu:
        runtime["status"] = "gpu_binary_missing"
        runtime["failure_reason"] = "gpu_binary_missing"
        runtime["failure_message"] = (
            "GPU MMseqs unavailable: no GPU binary found under "
            f"{db_root} (expected mmseqs-gpu-blackwell/bin/mmseqs or mmseqs-gpu/bin/mmseqs)"
        )
        return runtime

    selected_gpu_id = None
    if gpu_id is not None:
        selected_gpu_id = gpu_id
    elif persisted_pinned_gpu_id is not None:
        selected_gpu_id = persisted_pinned_gpu_id
        if verbose:
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
        runtime["status"] = "gpu_excluded"
        runtime["failure_reason"] = "gpu_excluded"
        runtime["failure_message"] = f"Selected gpu_id {selected_gpu_id} is excluded by policy"
        runtime["selected_gpu_id"] = selected_gpu_id
        return runtime

    if selected_gpu_id is not None:
        runtime["status"] = "gpu_ready"
        runtime["selected_gpu_id"] = selected_gpu_id
        runtime["use_gpu_mmseqs"] = True
        runtime["mmseqs_bin"] = str(mmseqs_gpu)
        runtime["summary_message"] = f"Using GPU mmseqs on device {selected_gpu_id}"
        return runtime

    runtime["status"] = "gpu_unavailable"
    runtime["failure_reason"] = "gpu_unavailable"
    runtime["failure_message"] = (
        "GPU MMseqs unavailable: no eligible GPU is currently available under the active policy"
    )
    return runtime


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
    return is_matching_gpuserver_process(
        pid,
        target_db,
        pid_is_alive=_pid_is_alive,
        read_proc_cmdline=_read_proc_cmdline,
    )


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

    def _should_keep_entry(entry_lines: List[str]) -> bool:
        if not entry_lines:
            return False
        header = entry_lines[0]
        if header == ">query" or "query" in header.lower()[:20]:
            return True
        tax_match = re.search(r"Tax=([^T]+?)(?:TaxID=|$)", header)
        if not tax_match:
            return True
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
        if "Bacteria" in filter_domains:
            return is_bacteria
        return True

    filtered_entries: List[str] = []
    current_entry: List[str] = []

    for line in a3m_content.split("\n"):
        if line.startswith(">"):
            if current_entry and _should_keep_entry(current_entry):
                filtered_entries.append("\n".join(current_entry))
            current_entry = [line]
        else:
            current_entry.append(line)

    if current_entry and _should_keep_entry(current_entry):
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
    startup_wait_seconds: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
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
    startup_wait_seconds: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
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


def _extract_tar_archive_safely(archive_path: Path, work_dir: Path) -> None:
    """Extract a tar.gz archive without allowing path traversal or special-file escapes."""
    root = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, mode="r:gz") as tar:
        safe_members = []
        for member in tar.getmembers():
            if not (member.isdir() or member.isreg()):
                raise RuntimeError(f"Refusing to extract special entry from ColabFold archive: {member.name}")
            if member.islnk() or member.issym():
                raise RuntimeError(f"Refusing to extract link entry from ColabFold archive: {member.name}")
            member_path = (work_dir / member.name).resolve()
            if member_path != root and not member_path.is_relative_to(root):
                raise RuntimeError(f"Refusing to extract path outside work dir from ColabFold archive: {member.name}")
            safe_members.append(member)
        tar.extractall(path=work_dir, members=safe_members)



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

    _extract_tar_archive_safely(archive_path=archive_path, work_dir=work_dir)

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
    gpu_server_wait_timeout: int = DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    gpu_server_db_load_mode: int = DEFAULT_GPUSERVER_DB_LOAD_MODE,
    gpu_server_startup_wait: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
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
    gpu_server_wait_timeout: int = DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    gpu_server_db_load_mode: int = DEFAULT_GPUSERVER_DB_LOAD_MODE,
    gpu_server_startup_wait: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    target_shard_mode: str = "auto",
    target_shards: int = DEFAULT_TARGET_SHARDS,
    target_shard_min_size_gb: float = DEFAULT_TARGET_SHARD_MIN_SIZE_GB,
    disallow_cpu_fallback: bool = False,
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
    allow_degraded_quality: bool = False,
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
        target_shard_mode: EnvDB target-sharding policy (auto, required, off)
        target_shards: Target DB shard count for high-quality EnvDB searches
        target_shard_min_size_gb: Minimum target DB size before auto sharding
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
        allow_degraded_quality: Permit high-quality local MSA runs to continue
            after core ColabFold stages fail. Defaults to False so maximum and
            balanced do not silently feed degraded A3Ms downstream.
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
    filterresult_qsc = 0.8 if config["use_filter"] else config["qsc"]
    filterresult_max_seq_id = 1.0 if config["use_filter"] else config["max_seq_id"]
    degraded_reasons: List[str] = []
    db_integrity_report: Dict[str, Any] = {
        "checked": False,
        "reason": "not_checked",
        "compatible": None,
        "issues": [],
        "families": {},
    }
    db_integrity_report_path: Optional[str] = None
    
    # Resolve DB paths before cache lookup so cache profile can reflect effective config.
    db_path = Path(db_path)
    uniref_db = db_path / "uniref30_2302_db"
    envdb = db_path / "colabfold_envdb_202108_db"
    env_available = envdb.exists() and Path(str(envdb) + ".dbtype").exists()
    if config["use_env"] and not env_available:
        message = "Environmental DB prefix is missing"
        if preset in {"maximum", "balanced"} and not allow_degraded_quality:
            raise RuntimeError(
                f"{message}; refusing degraded high-quality local MSA. "
                "Install/repair the local ColabFold EnvDB bundle or pass "
                "--allow-degraded-quality to continue intentionally with UniRef30 only."
            )
        degraded_reasons.append("envdb_missing")
        print(f"WARNING: {message}; falling back to UniRef30 only", flush=True)
        config["use_env"] = False

    db_integrity_report = build_runtime_db_integrity_preflight(
        db_path,
        use_env=bool(config["use_env"]),
        use_expand=bool(config["use_expand"]),
    )
    db_integrity_report_path = write_runtime_db_integrity_preflight(out_dir, job_name, db_integrity_report)
    if db_integrity_report["issues"]:
        first_issue = str(db_integrity_report["issues"][0])
        uniref_core_invalid = any(
            str(issue).startswith(("UniRef target", "UniRef sequence"))
            for issue in db_integrity_report["issues"]
        )
        env_core_invalid = any(
            str(issue).startswith(("EnvDB target", "EnvDB sequence"))
            for issue in db_integrity_report["issues"]
        )
        alignment_invalid = any("alignment" in str(issue) for issue in db_integrity_report["issues"])
        if preset in {"maximum", "balanced"} and not allow_degraded_quality:
            raise RuntimeError(
                f"{first_issue}; refusing degraded high-quality local MSA. "
                "Repair/rebuild the local ColabFold DB bundle or pass "
                "--allow-degraded-quality to continue intentionally. "
                f"Integrity report: {db_integrity_report_path}"
            )
        if uniref_core_invalid:
            raise RuntimeError(
                f"{first_issue}; local UniRef30 DB is required and cannot be degraded away. "
                f"Integrity report: {db_integrity_report_path}"
            )
        if env_core_invalid:
            degraded_reasons.append("envdb_integrity_invalid")
            config["use_env"] = False
            print(
                f"WARNING: EnvDB integrity preflight failed ({first_issue}); falling back to UniRef30 only. "
                f"Integrity report: {db_integrity_report_path}",
                flush=True,
            )
        if alignment_invalid and config["use_expand"]:
            degraded_reasons.append("alignment_integrity_invalid")
            config["use_expand"] = False
            print(
                f"WARNING: Alignment DB integrity preflight failed ({first_issue}); skipping expansion. "
                f"Integrity report: {db_integrity_report_path}",
                flush=True,
            )

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
                        "db_integrity": db_integrity_report,
                        "db_integrity_report_path": db_integrity_report_path,
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
        mmseqs_cpu, _mmseqs_gpu = resolve_mmseqs_binaries(db_path)

        runtime = inspect_mmseqs_runtime(
            db_path=db_path,
            cache_dir=cache_dir,
            use_gpu=use_gpu,
            gpu_id=gpu_id,
            cpu_only=cpu_only,
            gpu_mode=gpu_mode,
            gpu_threshold=gpu_threshold,
            preferred_gpus=preferred_gpus,
            excluded_gpus=excluded_gpus,
            gpu_server_mode=gpu_server_mode,
            gpu_server_wait_timeout=gpu_server_wait_timeout,
            gpu_server_db_load_mode=gpu_server_db_load_mode,
            gpu_server_startup_wait=gpu_server_startup_wait,
            verbose=True,
        )
        normalized_gpu_mode = runtime["normalized_gpu_mode"]
        normalized_gpu_server_mode = runtime["normalized_gpu_server_mode"]
        effective_gpu_server_wait_timeout = runtime["effective_gpu_server_wait_timeout"]
        effective_preferred_gpus = list(runtime.get("effective_preferred_gpus") or [])
        selected_gpu_id = runtime["selected_gpu_id"]
        use_gpu_flag = bool(runtime["use_gpu_mmseqs"])
        gpu_mmseqs_requested = bool(use_gpu_flag)
        gpu_target_db_status: Dict[str, Dict[str, Any]] = {
            "uniref30_2302_db": describe_mmseqs_gpu_target_db(uniref_db),
        }
        if config["use_env"]:
            gpu_target_db_status["colabfold_envdb_202108_db"] = describe_mmseqs_gpu_target_db(envdb)
        gpu_target_fallback_reason: Optional[str] = None
        gpu_uniref_search_db = uniref_db
        gpu_envdb_search_db = envdb

        if use_gpu_flag:
            missing_gpu_targets = [
                f"{name} (expected one of: {', '.join(status['candidate_prefixes'])})"
                for name, status in gpu_target_db_status.items()
                if not status.get("ready")
            ]
            if missing_gpu_targets:
                gpu_target_fallback_reason = "gpu_db_not_ready: " + "; ".join(missing_gpu_targets)
                message = (
                    "GPU MMseqs target DB not prepared for padded GPU search: "
                    + "; ".join(missing_gpu_targets)
                    + ". Build each target with `mmseqs makepaddedseqdb <target_db> <target_db>_gpu`; "
                    "empty .GPU_READY markers are not sufficient."
                )
                if normalized_gpu_mode == "required" or normalized_gpu_server_mode == "required" or disallow_cpu_fallback:
                    raise RuntimeError(message)
                print(f"WARNING: {message} Falling back to CPU MMseqs.", flush=True)
                use_gpu_flag = False
                selected_gpu_id = None
                normalized_gpu_server_mode = "off"
                mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
            else:
                gpu_uniref_search_db = Path(gpu_target_db_status["uniref30_2302_db"]["gpu_target_db"])
                if config["use_env"]:
                    gpu_envdb_search_db = Path(gpu_target_db_status["colabfold_envdb_202108_db"]["gpu_target_db"])

        if runtime["status"] == "cpu_forced":
            mmseqs_bin = Path(runtime["mmseqs_bin"])
            print(runtime["summary_message"], flush=True)
        elif use_gpu_flag:
            mmseqs_bin = Path(runtime["mmseqs_bin"])
            print(runtime["summary_message"], flush=True)
        else:
            failure_message = str(runtime.get("failure_message") or "GPU MMseqs unavailable")
            if normalized_gpu_mode == "required":
                raise RuntimeError(failure_message)
            if disallow_cpu_fallback:
                raise RuntimeError(f"{failure_message}. CPU fallback disabled for this run.")
            mmseqs_bin = mmseqs_cpu
            print(f"{failure_message}. Falling back to CPU mmseqs.", flush=True)

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
        gpuserver_status_url = os.getenv("BMS_MSA_SERVER_STATUS_URL") or DEFAULT_MSA_SERVER_STATUS_URL
        gpuserver_sources: Dict[str, str] = {}
        gpuserver_host_status: Dict[str, Dict[str, Any]] = {}
        isolated_task_context = bool(runtime.get("isolated_task_context"))
        mmseqs_stage_reports: List[Dict[str, Any]] = []
        gpu_target_result_remaps: List[Dict[str, Any]] = []
        envdb_acceleration_fallback_reason: Optional[str] = gpu_target_fallback_reason
        envdb_acceleration_backend_hint: Optional[str] = None

        def run_mmseqs_stage(stage: str, mmseqs_bin_arg, params, env_arg):
            report = command_report(stage, mmseqs_bin_arg, params)
            started = time.monotonic()
            try:
                result = run_mmseqs(mmseqs_bin_arg, params, env_arg)
                report.returncode = int(getattr(result, "returncode", 0) or 0)
                return result
            except Exception as exc:
                report.returncode = 1
                report.fallback_reason = str(exc)
                raise
            finally:
                report.elapsed_seconds = time.monotonic() - started
                mmseqs_stage_reports.append(report.to_json())

        def _run_search_with_gpu_server(
            *,
            stage: str,
            target_db: Path,
            base_search_params: List[str],
            prefilter_mode: int,
            tmp_dir: str,
        ) -> None:
            host_status: Optional[Dict[str, Any]] = None
            if isolated_task_context and normalized_gpu_server_mode != "off":
                host_status = query_host_gpuserver_status(
                    target_db=target_db,
                    gpu_id=selected_gpu_id,
                    max_seqs=config["max_seqs"],
                    prefilter_mode=prefilter_mode,
                    db_load_mode=gpu_server_db_load_mode,
                    include_envdb=(Path(target_db) in {Path(envdb), Path(gpu_envdb_search_db)}),
                    status_url=gpuserver_status_url,
                )
                gpuserver_host_status[target_db.name] = host_status
                if host_status.get("ready"):
                    print(
                        f"Reusing host gpuserver for {target_db.name} via {gpuserver_status_url}.",
                        flush=True,
                    )
                    run_mmseqs_stage(stage, mmseqs_bin, base_search_params + [
                        "--db-load-mode", str(gpu_server_db_load_mode),
                        "--gpu", "1",
                        "--gpu-server", "1",
                        "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                        "--prefilter-mode", str(prefilter_mode),
                        "--threads", str(num_threads),
                    ], env)
                    gpuserver_sources[target_db.name] = "host"
                    return
                if host_status.get("checked"):
                    print(
                        f"Host gpuserver not ready for {target_db.name}; starting task-local server for this search.",
                        flush=True,
                    )
                else:
                    print(
                        f"Host gpuserver status check failed for {target_db.name} ({host_status.get('error')}); starting task-local server for this search.",
                        flush=True,
                    )

            if normalized_gpu_server_mode == "persistent" and not isolated_task_context:
                server_meta = ensure_persistent_mmseqs_gpuserver(
                    mmseqs_bin=mmseqs_bin,
                    target_db=target_db,
                    env=env,
                    max_seqs=config["max_seqs"],
                    prefilter_mode=prefilter_mode,
                    db_load_mode=gpu_server_db_load_mode,
                    cache_dir=cache_dir,
                    startup_wait_seconds=gpu_server_startup_wait,
                )
                action = "Reusing" if server_meta.get("reused") else "Started"
                print(
                    f"{action} persistent gpuserver for {target_db.name} "
                    f"(pid={server_meta.get('pid')}, gpu={env.get('CUDA_VISIBLE_DEVICES', 'auto')})",
                    flush=True,
                )
                run_mmseqs_stage(stage, mmseqs_bin, base_search_params + [
                    "--db-load-mode", str(gpu_server_db_load_mode),
                    "--gpu", "1",
                    "--gpu-server", "1",
                    "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                    "--prefilter-mode", str(prefilter_mode),
                    "--threads", str(num_threads),
                ], env)
                gpuserver_sources[target_db.name] = "persistent"
                return

            gpuserver_log = Path(tmp_dir) / f"gpuserver_{target_db.stem}.log"
            server_label = "task-local transient" if isolated_task_context else "transient"
            print(f"Starting {server_label} gpuserver for {target_db.name}...", flush=True)
            with run_mmseqs_gpuserver(
                mmseqs_bin=mmseqs_bin,
                target_db=target_db,
                env=env,
                max_seqs=config["max_seqs"],
                prefilter_mode=prefilter_mode,
                db_load_mode=gpu_server_db_load_mode,
                log_path=gpuserver_log,
                startup_wait_seconds=gpu_server_startup_wait,
            ):
                run_mmseqs_stage(stage, mmseqs_bin, base_search_params + [
                    "--db-load-mode", str(gpu_server_db_load_mode),
                    "--gpu", "1",
                    "--gpu-server", "1",
                    "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
                    "--prefilter-mode", str(prefilter_mode),
                    "--threads", str(num_threads),
                ], env)
            print(f"gpuserver completed for {target_db.name}", flush=True)
            gpuserver_sources[target_db.name] = "transient"

        with tempfile.TemporaryDirectory() as tmp_dir:
            # ═══════════════════════════════════════════════════════════════════
            # STEP 1: Create query database
            # ═══════════════════════════════════════════════════════════════════
            query_fasta = os.path.join(tmp_dir, "query.fasta")
            with open(query_fasta, 'w') as f:
                f.write(f">query\n{sequence}\n")
            
            query_db = os.path.join(tmp_dir, "qdb")
            run_mmseqs_stage("createdb", mmseqs_bin, [
                "createdb", query_fasta, query_db,
                "--shuffle", "0", "--dbtype", "1"
            ], env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 2: Iterative profile search against UniRef30
            # ═══════════════════════════════════════════════════════════════════
            print(f"Searching UniRef30 ({config['num_iterations']} iterations)...", flush=True)
            
            result_db = os.path.join(tmp_dir, "res")
            uniref_base_search_params_cpu = [
                "search", query_db, str(uniref_db), result_db,
                os.path.join(tmp_dir, "tmp"),
                "--num-iterations", str(config["num_iterations"]),
                "-a",  # Report alignments
                "-e", str(config["evalue"]),
                "--max-seqs", str(config["max_seqs"]),
                "-s", str(config["sensitivity"]),
            ]
            base_search_params = (
                _replace_search_target_db(uniref_base_search_params_cpu, gpu_uniref_search_db)
                if use_gpu_flag
                else uniref_base_search_params_cpu
            )
            uniref_search_used_gpu_padded_target = False
            if use_gpu_flag and Path(base_search_params[2]) != uniref_db:
                print(f"Using padded GPU UniRef target DB: {base_search_params[2]}", flush=True)
            
            if use_gpu_flag:
                used_gpu_server = False
                direct_gpu_allowed = True
                if normalized_gpu_server_mode != "off":
                    try:
                        _run_search_with_gpu_server(
                            stage="uniref_search",
                            target_db=gpu_uniref_search_db,
                            base_search_params=base_search_params,
                            prefilter_mode=1,
                            tmp_dir=tmp_dir,
                        )
                        used_gpu_server = True
                        uniref_search_used_gpu_padded_target = Path(base_search_params[2]) != uniref_db
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
                            run_mmseqs_stage("uniref_search", mmseqs_bin, base_search_params + [
                                "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                                "--gpu", "1",
                                "--prefilter-mode", "1",
                                "--threads", str(num_threads),
                            ], env)
                            uniref_search_used_gpu_padded_target = Path(base_search_params[2]) != uniref_db
                        except RuntimeError as e:
                            if not _should_retry_direct_gpu_on_cpu(e):
                                raise
                            print(
                                f"WARNING: Direct GPU UniRef30 search failed ({e}). Retrying on CPU.",
                                flush=True,
                            )
                            use_gpu_flag = False
                            uniref_search_used_gpu_padded_target = False
                            run_mmseqs_stage("uniref_search", mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin, uniref_base_search_params_cpu + [
                                "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                                "--threads", str(num_threads),
                            ], env)
                    else:
                        uniref_search_used_gpu_padded_target = False
                        run_mmseqs_stage("uniref_search", mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin, uniref_base_search_params_cpu + [
                            "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                            "--threads", str(num_threads),
                        ], env)
            else:
                uniref_search_used_gpu_padded_target = False
                run_mmseqs_stage("uniref_search", mmseqs_bin, base_search_params + [
                    "--db-load-mode", "2",  # mmap databases into RAM for faster I/O
                    "--threads", str(num_threads),
                ], env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 3: Extract/derive refined profile for environmental search
            # ═══════════════════════════════════════════════════════════════════
            profile_db = os.path.join(tmp_dir, "prof_res")
            has_profile = False
            profile_builder_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin

            if uniref_search_used_gpu_padded_target:
                # GPU search uses a makepaddedseqdb target. Its result DB target
                # IDs are in the padded GPU keyspace, not the logical ColabFold
                # target keyspace used by *_seq/*_aln. Rewrite result rows via
                # the padded target .lookup before any downstream logical-stage
                # result2profile/expandaln/filter/result2msa command sees them.
                try:
                    remap_report = remap_mmseqs_result_target_keys_from_gpu_lookup(
                        result_db=result_db,
                        gpu_target_db=gpu_uniref_search_db,
                        output_db=os.path.join(tmp_dir, "res_logical"),
                        stage="uniref_search",
                    )
                    gpu_target_result_remaps.append(remap_report)
                    result_db = str(remap_report["output_db"])
                    print(
                        "Remapped UniRef GPU search result target IDs back to logical UniRef keyspace.",
                        flush=True,
                    )
                except RuntimeError as e:
                    raise RuntimeError(
                        f"Could not remap UniRef padded-GPU search results to logical target keyspace ({e}); "
                        "refusing to continue with a corrupted local MSA."
                    ) from e

                # The profile MMseqs writes under tmp/latest is also in the
                # padded target's coordinate space. Rebuild the profile from the
                # remapped GPU hits against the logical target DB before any
                # high-quality UniRef/EnvDB stages consume it.
                try:
                    print(
                        "Rebuilding UniRef profile from GPU search results against logical UniRef target DB...",
                        flush=True,
                    )
                    run_mmseqs_stage("uniref_result2profile", profile_builder_bin, [
                        "result2profile", query_db, str(uniref_db), result_db, profile_db,
                        "--threads", str(num_threads),
                        "--db-load-mode", "2",
                    ], env)
                    has_profile = True
                except RuntimeError as e:
                    message = f"Could not rebuild UniRef profile from padded GPU search results ({e})"
                    if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                        raise RuntimeError(
                            f"{message}; refusing degraded high-quality local MSA. "
                            "Pass --allow-degraded-quality to continue intentionally."
                        ) from e
                    degraded_reasons.append("uniref_gpu_profile_rebuild_failed")
                    print(f"WARNING: {message}; using query DB for env search", flush=True)
                    profile_db = query_db
            else:
                # ColabFold uses profile_1 (first iteration profile), but check all iterations
                for iter_num in [1, 2, 3, config["num_iterations"]]:
                    profile_source = os.path.join(tmp_dir, f"tmp/latest/profile_{iter_num}")
                    if os.path.exists(profile_source + ".dbtype"):
                        print(f"Found profile at iteration {iter_num}", flush=True)
                        run_mmseqs_stage("profile_mvdb", mmseqs_bin, ["mvdb", profile_source, profile_db], env)
                        # Link headers
                        run_mmseqs_stage("profile_lndb", mmseqs_bin, ["lndb", query_db + "_h", profile_db + "_h"], env)
                        has_profile = True
                        break
            
            if not has_profile:
                # GPU paths do not always emit tmp/latest/profile_*. Build profile DB directly.
                try:
                    run_mmseqs_stage("result2profile", profile_builder_bin, [
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
                # Check if alignment database is valid for expansion. A plain
                # file-size check is not enough: a bad rebuild can leave an
                # `_aln.index` in a different numeric keyspace from the target
                # DB, which makes expandaln emit `Missing alignments...`.
                aln_db = Path(str(uniref_db) + "_aln")
                if aln_db.exists():
                    validation = validate_alignment_index_keyspace(uniref_db, aln_db)
                    if validation.compatible:
                        can_expand = True
                    else:
                        message = f"UniRef alignment DB keyspace validation failed: {validation.reason}"
                        if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                            raise RuntimeError(
                                f"{message}; refusing degraded high-quality local MSA. "
                                "Repair/rebuild the local ColabFold UniRef alignment DB or pass "
                                "--allow-degraded-quality to continue intentionally."
                            )
                        degraded_reasons.append("uniref_aln_keyspace_invalid")
                        print(f"WARNING: {message}; skipping expansion", flush=True)
                else:
                    message = "UniRef alignment DB prefix is missing"
                    if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                        raise RuntimeError(
                            f"{message}; refusing degraded high-quality local MSA. "
                            "Repair/rebuild the local ColabFold UniRef alignment DB or pass "
                            "--allow-degraded-quality to continue intentionally."
                        )
                    degraded_reasons.append("uniref_aln_missing")
                    print(f"WARNING: {message}; skipping expansion", flush=True)
            
            if can_expand:
                print("Expanding alignments to recover cluster members...", flush=True)
                expanded_db = os.path.join(tmp_dir, "res_exp")
                try:
                    run_mmseqs_stage("uniref_expandaln", mmseqs_bin, [
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
                    run_mmseqs_stage("uniref_align", mmseqs_bin, [
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
                    message = f"Alignment expansion failed ({e})"
                    if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                        raise RuntimeError(
                            f"{message}; refusing degraded high-quality local MSA. "
                            "Pass --allow-degraded-quality to continue intentionally."
                        ) from e
                    degraded_reasons.append("uniref_expandaln_failed")
                    print(f"WARNING: {message}, continuing without expansion", flush=True)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 5: Quality filtering (Maximum/Balanced presets)
            # ═══════════════════════════════════════════════════════════════════
            if config["use_filter"]:
                print("Filtering MSA by quality metrics...", flush=True)
                filtered_db = os.path.join(tmp_dir, "res_filtered")
                run_mmseqs_stage("uniref_filterresult", mmseqs_bin, [
                    "filterresult", query_db, str(uniref_db) + "_seq",
                    result_db, filtered_db,
                    "--qid", "0",
                    "--qsc", str(filterresult_qsc),
                    "--diff", "0",
                    "--max-seq-id", str(filterresult_max_seq_id),
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
            run_mmseqs_stage("uniref_result2msa", mmseqs_bin, [
                "result2msa", query_db, str(uniref_db) + "_seq",
                result_db, uniref_a3m_db,
                "--msa-format-mode", "6",
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
                    run_mmseqs_stage("uniref_preview_unpack", mmseqs_bin, [
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
            target_shard_plan = None
            target_shard_materialization = None
            target_sharded_env_search = False
            if effective_use_env and env_available:
                print(f"Searching environmental database ({envdb.name})...", flush=True)

                if (
                    use_gpu_flag
                    and normalized_gpu_server_mode == "persistent"
                    and effective_preferred_gpus
                ):
                    reclaimed = reclaim_conflicting_gpuserver_instances(
                        target_db=gpu_envdb_search_db,
                        preferred_gpus=effective_preferred_gpus,
                        cache_dir=cache_dir,
                    )
                    if reclaimed > 0:
                        print(
                            f"Reclaimed {reclaimed} conflicting gpuserver instance(s) before EnvDB GPU search.",
                            flush=True,
                        )
                
                env_result_db = os.path.join(tmp_dir, "res_env")
                env_base_search_params_cpu = [
                    "search", profile_db if has_profile else query_db,
                    str(envdb), env_result_db,
                    os.path.join(tmp_dir, "tmp_env"),
                    "--num-iterations", str(config["num_iterations"]),
                    "-a", "-e", str(config["evalue"]),
                    "--max-seqs", str(config["max_seqs"]),
                    "-s", str(config["sensitivity"]),
                ]
                env_base_search_params = (
                    _replace_search_target_db(env_base_search_params_cpu, gpu_envdb_search_db)
                    if use_gpu_flag
                    else env_base_search_params_cpu
                )
                env_search_used_gpu_padded_target = False
                if use_gpu_flag and Path(env_base_search_params[2]) != envdb:
                    print(f"Using padded GPU EnvDB target DB: {env_base_search_params[2]}", flush=True)

                target_shard_plan = build_target_shard_plan_from_gb(
                    mode=target_shard_mode,
                    preset=preset,
                    use_env=bool(effective_use_env),
                    env_available=bool(env_available),
                    target_db=envdb,
                    total_threads=num_threads,
                    requested_shards=target_shards,
                    target_shard_min_size_gb=target_shard_min_size_gb,
                    shard_cache_dir=Path(cache_dir) / ".target_shards",
                )
                if target_shard_plan.enabled:
                    print(
                        f"MMseqs native target splitting enabled for EnvDB: split={target_shard_plan.shard_count}, "
                        f"threads={target_shard_plan.total_threads} (global budget).",
                        flush=True,
                    )
                    try:
                        # Use MMseqs' native search splitting so iterative/profile
                        # barriers stay inside the MMseqs search workflow. The old
                        # BioModStack splitdb + per-shard search + mergedbs path ran
                        # independent shard-local iterations and was not
                        # high-quality-equivalent.
                        def _run_envdb_native_split(native_bin, extra_params: List[str], base_params: Optional[List[str]] = None) -> None:
                            run_native_target_split_search(
                                mmseqs_bin=native_bin,
                                base_search_params=base_params or env_base_search_params,
                                split_count=target_shard_plan.shard_count,
                                total_threads=target_shard_plan.total_threads,
                                env=env,
                                run_mmseqs=lambda bin_arg, args, env_arg: run_mmseqs_stage(
                                    "envdb_search", bin_arg, args, env_arg
                                ),
                                extra_search_params=extra_params,
                                split_mode=0,
                            )

                        if use_gpu_flag:
                            try:
                                envdb_acceleration_backend_hint = "gpu_native_split"
                                _run_envdb_native_split(
                                    mmseqs_bin,
                                    [
                                        "--db-load-mode", str(gpu_server_db_load_mode),
                                        "--gpu", "1",
                                        "--prefilter-mode", "1",
                                    ],
                                )
                                target_sharded_env_search = True
                                env_search_used_gpu_padded_target = Path(env_base_search_params[2]) != envdb
                            except Exception as gpu_exc:
                                if normalized_gpu_mode == "required" or normalized_gpu_server_mode == "required" or disallow_cpu_fallback:
                                    raise RuntimeError(
                                        f"GPU EnvDB native target split required but failed: {gpu_exc}"
                                    ) from gpu_exc
                                envdb_acceleration_fallback_reason = str(gpu_exc)
                                envdb_acceleration_backend_hint = "cpu_native_split"
                                print(
                                    f"WARNING: GPU EnvDB native target split failed ({gpu_exc}); "
                                    "falling back to CPU native target split.",
                                    flush=True,
                                )
                                shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
                                _run_envdb_native_split(
                                    shard_mmseqs_bin,
                                    ["--db-load-mode", "2"],
                                    base_params=env_base_search_params_cpu,
                                )
                                target_sharded_env_search = True
                                env_search_used_gpu_padded_target = False
                        else:
                            envdb_acceleration_backend_hint = "cpu_native_split"
                            shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
                            _run_envdb_native_split(
                                shard_mmseqs_bin,
                                ["--db-load-mode", "2"],
                            )
                            target_sharded_env_search = True
                            env_search_used_gpu_padded_target = False
                    except Exception as e:
                        if not target_shard_plan.fallback_allowed:
                            raise
                        envdb_acceleration_fallback_reason = str(e)
                        print(
                            f"WARNING: EnvDB native target split search failed ({e}); falling back to unsharded EnvDB search.",
                            flush=True,
                        )
                elif target_shard_mode != "off":
                    print(f"Target DB sharding not used: {target_shard_plan.reason}", flush=True)
                
                if not target_sharded_env_search and use_gpu_flag:
                    used_gpu_server = False
                    direct_gpu_allowed = True
                    if normalized_gpu_server_mode != "off":
                        try:
                            _run_search_with_gpu_server(
                                stage="envdb_search",
                                target_db=gpu_envdb_search_db,
                                base_search_params=env_base_search_params,
                                prefilter_mode=1,
                                tmp_dir=tmp_dir,
                            )
                            used_gpu_server = True
                            env_search_used_gpu_padded_target = Path(env_base_search_params[2]) != envdb
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
                                run_mmseqs_stage("envdb_search", mmseqs_bin, env_base_search_params + [
                                    "--db-load-mode", "2",  # mmap databases into RAM
                                    "--gpu", "1",
                                    "--prefilter-mode", "1",
                                    "--threads", str(num_threads),
                                ], env)
                                env_search_used_gpu_padded_target = Path(env_base_search_params[2]) != envdb
                            except RuntimeError as e:
                                if not _should_retry_direct_gpu_on_cpu(e):
                                    raise
                                print(
                                    f"WARNING: Direct GPU EnvDB search failed ({e}). Retrying on CPU.",
                                    flush=True,
                                )
                                use_gpu_flag = False
                                env_search_used_gpu_padded_target = False
                                shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
                                run_mmseqs_stage("envdb_search", shard_mmseqs_bin, env_base_search_params_cpu + [
                                    "--db-load-mode", "2",  # mmap databases into RAM
                                    "--threads", str(num_threads),
                                ], env)
                        else:
                            env_search_used_gpu_padded_target = False
                            shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
                            run_mmseqs_stage("envdb_search", shard_mmseqs_bin, env_base_search_params_cpu + [
                                "--db-load-mode", "2",  # mmap databases into RAM
                                "--threads", str(num_threads),
                            ], env)
                elif not target_sharded_env_search:
                    env_search_used_gpu_padded_target = False
                    run_mmseqs_stage("envdb_search", mmseqs_bin, env_base_search_params + [
                        "--db-load-mode", "2",  # mmap databases into RAM
                        "--threads", str(num_threads),
                    ], env)
                
                if env_search_used_gpu_padded_target:
                    try:
                        remap_report = remap_mmseqs_result_target_keys_from_gpu_lookup(
                            result_db=env_result_db,
                            gpu_target_db=gpu_envdb_search_db,
                            output_db=os.path.join(tmp_dir, "res_env_logical"),
                            stage="envdb_search",
                        )
                        gpu_target_result_remaps.append(remap_report)
                        env_result_db = str(remap_report["output_db"])
                        print(
                            "Remapped EnvDB GPU search result target IDs back to logical EnvDB keyspace.",
                            flush=True,
                        )
                    except RuntimeError as e:
                        raise RuntimeError(
                            f"Could not remap EnvDB padded-GPU search results to logical target keyspace ({e}); "
                            "refusing to continue with a corrupted local MSA."
                        ) from e

                # Expand environmental hits if enabled
                if config["use_expand"]:
                    env_aln_db = Path(str(envdb) + "_aln")
                    env_validation = validate_alignment_index_keyspace(envdb, env_aln_db)
                    if not env_validation.compatible:
                        message = f"EnvDB alignment DB keyspace validation failed: {env_validation.reason}"
                        if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                            raise RuntimeError(
                                f"{message}; refusing degraded high-quality local MSA. "
                                "Repair/rebuild the local ColabFold EnvDB alignment DB or pass "
                                "--allow-degraded-quality to continue intentionally."
                            )
                        degraded_reasons.append("envdb_aln_keyspace_invalid")
                        print(f"WARNING: {message}; skipping EnvDB expansion", flush=True)
                    else:
                        env_expanded = os.path.join(tmp_dir, "res_env_exp")
                        run_mmseqs_stage("envdb_expandaln", mmseqs_bin, [
                            "expandaln", profile_db if has_profile else query_db,
                            str(envdb) + "_seq",
                            env_result_db, str(envdb) + "_aln", env_expanded,
                            "-e", "inf",
                            "--expansion-mode", "0",
                            "--threads", str(num_threads),
                        ], env)

                        # Realign expanded environmental hits
                        env_tmp_dir = os.path.join(tmp_dir, "tmp_env")
                        env_profile = os.path.join(env_tmp_dir, "latest/profile_1")
                        if env_search_used_gpu_padded_target:
                            env_profile = os.path.join(tmp_dir, "prof_env_res")
                            try:
                                print(
                                    "Rebuilding EnvDB profile from GPU search results against logical EnvDB target DB...",
                                    flush=True,
                                )
                                run_mmseqs_stage("envdb_result2profile", profile_builder_bin, [
                                    "result2profile",
                                    env_base_search_params_cpu[1],
                                    str(envdb),
                                    env_result_db,
                                    env_profile,
                                    "--threads", str(num_threads),
                                    "--db-load-mode", "2",
                                ], env)
                                align_profile = env_profile
                            except RuntimeError as e:
                                message = f"Could not rebuild EnvDB profile from padded GPU search results ({e})"
                                if preset in {"maximum", "balanced"} and not allow_degraded_quality:
                                    raise RuntimeError(
                                        f"{message}; refusing degraded high-quality local MSA. "
                                        "Pass --allow-degraded-quality to continue intentionally."
                                    ) from e
                                degraded_reasons.append("envdb_gpu_profile_rebuild_failed")
                                print(f"WARNING: {message}; using UniRef/query profile for EnvDB realign", flush=True)
                                align_profile = profile_db if has_profile else query_db
                        elif os.path.exists(env_profile + ".dbtype"):
                            align_profile = env_profile
                        else:
                            align_profile = profile_db if has_profile else query_db

                        env_realigned = os.path.join(tmp_dir, "res_env_realign")
                        run_mmseqs_stage("envdb_align", mmseqs_bin, [
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
                    run_mmseqs_stage("envdb_filterresult", mmseqs_bin, [
                        "filterresult", query_db, str(envdb) + "_seq",
                        env_result_db, env_filtered,
                        "--qid", "0",
                        "--qsc", str(filterresult_qsc),
                        "--diff", "0",
                        "--max-seq-id", str(filterresult_max_seq_id),
                        "--filter-min-enable", "100",
                        "--threads", str(num_threads),
                    ], env)
                    env_result_db = env_filtered
                
                # Generate Environmental MSA
                env_a3m_db = os.path.join(tmp_dir, "env.a3m")
                run_mmseqs_stage("envdb_result2msa", mmseqs_bin, [
                    "result2msa", query_db, str(envdb) + "_seq",
                    env_result_db, env_a3m_db,
                    "--msa-format-mode", "6",
                    "--threads", str(num_threads),
                    "--db-load-mode", "2",  # Preload sequence DB into RAM
                ] + filter_params, env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 8: Merge MSAs
            # ═══════════════════════════════════════════════════════════════════
            if env_a3m_db and os.path.exists(env_a3m_db + ".dbtype"):
                print("Merging UniRef30 and Environmental MSAs...", flush=True)
                final_a3m_db = os.path.join(tmp_dir, "final.a3m")
                run_mmseqs_stage("merge_msa", mmseqs_bin, [
                    "mergedbs", query_db, final_a3m_db,
                    uniref_a3m_db, env_a3m_db
                ], env)
            else:
                final_a3m_db = uniref_a3m_db
            
            # Unpack to final A3M file
            run_mmseqs_stage("unpack_final", mmseqs_bin, [
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

            effective_gpu_stage_names = effective_gpu_stages(mmseqs_stage_reports)
            envdb_search_stage_reports = [
                report for report in mmseqs_stage_reports
                if report.get("stage") == "envdb_search" and report.get("module") == "search"
            ]
            last_envdb_search_report = envdb_search_stage_reports[-1] if envdb_search_stage_reports else None
            envdb_effective_gpu = bool(
                last_envdb_search_report
                and (
                    last_envdb_search_report.get("uses_gpu_flag")
                    or last_envdb_search_report.get("uses_gpu_server")
                )
            )
            if last_envdb_search_report:
                if last_envdb_search_report.get("uses_gpu_server"):
                    envdb_backend = "gpu_server_native_split" if last_envdb_search_report.get("split_count") else "gpu_server"
                elif last_envdb_search_report.get("uses_gpu_flag"):
                    envdb_backend = "gpu_native_split" if last_envdb_search_report.get("split_count") else "gpu_unsharded"
                elif last_envdb_search_report.get("split_count"):
                    envdb_backend = "cpu_native_split"
                else:
                    envdb_backend = "cpu_unsharded"
            else:
                envdb_backend = envdb_acceleration_backend_hint or "not_run"
            if envdb_acceleration_backend_hint and not envdb_effective_gpu:
                envdb_backend = envdb_acceleration_backend_hint
            envdb_acceleration = {
                "backend": envdb_backend,
                "requested_gpu": bool(gpu_mmseqs_requested),
                "effective_gpu": envdb_effective_gpu,
                "target_split": bool(target_sharded_env_search),
                "uses_gpu_server": bool(last_envdb_search_report and last_envdb_search_report.get("uses_gpu_server")),
                "split_count": last_envdb_search_report.get("split_count") if last_envdb_search_report else None,
                "split_mode": last_envdb_search_report.get("split_mode") if last_envdb_search_report else None,
                "threads": last_envdb_search_report.get("threads") if last_envdb_search_report else None,
                "fallback_from_gpu": bool(envdb_acceleration_fallback_reason),
                "fallback_reason": envdb_acceleration_fallback_reason,
            }
            
            mmseqs_stage_report_path = os.path.join(out_dir, f"{job_name}_mmseqs_stage_report.json")

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
                "allow_degraded_quality": bool(allow_degraded_quality),
                "degraded_quality": bool(degraded_reasons),
                "degraded_reasons": list(degraded_reasons),
                "uniref_only_depth": uniref_only_depth,
                "use_expand": config["use_expand"],
                "use_filter": config["use_filter"],
                "evalue": config["evalue"],
                "sensitivity": config["sensitivity"],
                "taxon_filter": taxon_list,
                "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                "selected_gpu_id": selected_gpu_id,
                "gpu_mmseqs_requested": gpu_mmseqs_requested,
                "used_gpu_mmseqs": bool(effective_gpu_stage_names),
                "effective_gpu_stages": effective_gpu_stage_names,
                "mmseqs_gpu_target_dbs": gpu_target_db_status,
                "mmseqs_gpu_target_result_remaps": gpu_target_result_remaps,
                "envdb_acceleration": envdb_acceleration,
                "mmseqs_stage_reports": mmseqs_stage_reports,
                "mmseqs_stage_report_path": mmseqs_stage_report_path,
                "gpuserver_mode_requested": gpu_server_mode,
                "gpuserver_mode_effective": normalized_gpu_server_mode,
                "gpuserver_db_load_mode": gpu_server_db_load_mode,
                "gpuserver_wait_timeout": effective_gpu_server_wait_timeout,
                "gpuserver_startup_wait": gpu_server_startup_wait,
                "isolated_task_context": isolated_task_context,
                "gpuserver_sources": gpuserver_sources,
                "gpuserver_host_status": {
                    name: {
                        "checked": bool(status.get("checked")),
                        "ready": bool(status.get("ready")),
                        "error": status.get("error"),
                    }
                    for name, status in gpuserver_host_status.items()
                },
                "db_integrity": db_integrity_report,
                "db_integrity_report_path": db_integrity_report_path,
                "target_sharding": {
                    "mode_requested": target_shard_mode,
                    "enabled": bool(target_shard_plan.enabled) if target_shard_plan else False,
                    "mode_effective": target_shard_plan.mode if target_shard_plan else "off",
                    "reason": target_shard_plan.reason if target_shard_plan else "EnvDB search not executed",
                    "shard_count": target_shard_plan.shard_count if target_shard_plan else 0,
                    "threads_per_worker": target_shard_plan.threads_per_worker if target_shard_plan else num_threads,
                    "total_threads": target_shard_plan.total_threads if target_shard_plan else num_threads,
                    "fallback_allowed": bool(target_shard_plan.fallback_allowed) if target_shard_plan else False,
                    "implementation": "mmseqs_native_search_split" if target_sharded_env_search else "unsharded",
                    "native_split_mode": 0 if target_sharded_env_search else None,
                    "manifest_path": str(target_shard_materialization.manifest_path) if target_shard_materialization else None,
                    "reused_manifest": bool(target_shard_materialization.reused) if target_shard_materialization else None,
                    "search_was_sharded": bool(target_sharded_env_search),
                },
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
            
            mmseqs_stage_report_payload = {
                "job_name": job_name,
                "preset": preset,
                "gpu_mmseqs_requested": gpu_mmseqs_requested,
                "used_gpu_mmseqs": bool(effective_gpu_stage_names),
                "effective_gpu_stages": effective_gpu_stage_names,
                "mmseqs_gpu_target_dbs": gpu_target_db_status,
                "mmseqs_gpu_target_result_remaps": gpu_target_result_remaps,
                "envdb_acceleration": envdb_acceleration,
                "stages": mmseqs_stage_reports,
            }
            with open(mmseqs_stage_report_path, 'w') as f:
                json.dump(mmseqs_stage_report_payload, f, indent=2)
            print(f"MMseqs stage report: {mmseqs_stage_report_path}", flush=True)

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


def build_arg_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=DEFAULT_GPUSERVER_WAIT_TIMEOUT,
                        help="Seconds to wait for gpuserver handshake (0=no wait, -1=infinite)")
    parser.add_argument(
        "--gpu-server-db-load-mode",
        type=int,
        default=DEFAULT_GPUSERVER_DB_LOAD_MODE,
        choices=GPUSERVER_DB_LOAD_MODE_CHOICES,
        help=f"MMseqs db-load-mode for gpuserver-backed searches (default: {DEFAULT_GPUSERVER_DB_LOAD_MODE})",
    )
    parser.add_argument("--gpu-server-startup-wait", type=float, default=DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
                        help="Seconds to wait after starting gpuserver before first search")
    parser.add_argument("--disallow-cpu-fallback", action="store_true",
                        help="Fail instead of falling back to CPU MMseqs when GPU MMseqs is unavailable")
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
    parser.add_argument("--allow-degraded-quality", action="store_true",
                        help="Allow high-quality local MSA runs to continue after core ColabFold stages fail (default: fail fast)")
    parser.add_argument("--target-shard-mode", dest="target_shard_mode", type=str, default="auto",
                        choices=["auto", "required", "off"],
                        help="EnvDB target DB sharding policy for high-quality local MSA runs (default: auto)")
    parser.add_argument("--target-shards", dest="target_shards", type=int, default=DEFAULT_TARGET_SHARDS,
                        help=f"Number of target DB shards for EnvDB sharded search (default: {DEFAULT_TARGET_SHARDS})")
    parser.add_argument("--target-shard-min-size-gb", dest="target_shard_min_size_gb", type=float, default=DEFAULT_TARGET_SHARD_MIN_SIZE_GB,
                        help=f"Minimum target DB size before auto sharding, in GiB (default: {DEFAULT_TARGET_SHARD_MIN_SIZE_GB})")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    from local_msa.cli.run_single import build_single_request_from_namespace, dispatch_single_request

    request = build_single_request_from_namespace(args)
    dispatch_single_request(
        request,
        local_executor=run_colabfold_msa_workflow,
        colabfold_api_executor=run_colabfold_api_msa_workflow,
        colabfold_api_options={
            "colabfold_api_host": args.colabfold_api_host,
            "colabfold_api_min_interval": args.colabfold_api_min_interval,
            "colabfold_api_poll_interval": args.colabfold_api_poll_interval,
        },
    )
    return 0


run_colabfold_api_msa_workflow = register_legacy_run_colabfold_api_msa_workflow(run_colabfold_api_msa_workflow)
run_colabfold_msa_workflow = register_legacy_run_colabfold_msa_workflow(run_colabfold_msa_workflow)


# Backward compatibility alias
run_local_mmseqs2 = run_colabfold_msa_workflow


if __name__ == "__main__":
    raise SystemExit(main())

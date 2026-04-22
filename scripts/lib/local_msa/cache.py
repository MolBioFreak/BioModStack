from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import DEFAULT_CACHE_DIR, MSA_PRESETS

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

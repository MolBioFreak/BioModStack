from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


MMseqsRunner = Callable[[Any, list[Any], dict[str, str]], Any]

VALID_TARGET_SHARD_MODES = {"off", "auto", "required"}
QUALITY_ENVDB_PRESETS = {"balanced", "maximum"}
DEFAULT_TARGET_SHARDS = 4
DEFAULT_TARGET_SHARD_MIN_SIZE_GB = 1.0
DEFAULT_MIN_THREADS_PER_WORKER = 1


@dataclass(frozen=True)
class TargetShardPlan:
    enabled: bool
    mode: str
    preset: str
    reason: str
    shard_count: int
    total_threads: int
    threads_per_worker: int
    fallback_allowed: bool
    target_db: str
    shard_cache_dir: str


@dataclass(frozen=True)
class TargetShardMaterialization:
    target_db: Path
    shard_count: int
    shard_dir: Path
    manifest_path: Path
    shards: tuple[Path, ...]
    reused: bool


def _normalize_mode(mode: str | None) -> str:
    normalized = str(mode or "auto").strip().lower()
    if normalized not in VALID_TARGET_SHARD_MODES:
        raise ValueError(
            f"Invalid target shard mode {mode!r}; expected one of {sorted(VALID_TARGET_SHARD_MODES)}"
        )
    return normalized


def _target_db_ready(target_db: Path) -> bool:
    return target_db.exists() and Path(str(target_db) + ".dbtype").exists()


def _target_signature(target_db: Path) -> dict[str, Any]:
    dbtype = Path(str(target_db) + ".dbtype")

    def _stat_payload(path: Path) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    return {
        "path": str(target_db.resolve()),
        "db": _stat_payload(target_db),
        "dbtype": _stat_payload(dbtype),
    }


def _target_size_bytes(target_db: Path) -> int:
    try:
        return int(target_db.stat().st_size)
    except FileNotFoundError:
        return 0


def _min_size_bytes_from_gb(value: float | int | None) -> int:
    if value is None:
        value = DEFAULT_TARGET_SHARD_MIN_SIZE_GB
    value = max(0.0, float(value))
    return int(value * 1024 * 1024 * 1024)


def build_target_shard_plan(
    *,
    mode: str | None,
    preset: str,
    use_env: bool,
    env_available: bool,
    target_db: str | os.PathLike[str] | Path,
    total_threads: int,
    requested_shards: int | None = DEFAULT_TARGET_SHARDS,
    min_threads_per_worker: int = DEFAULT_MIN_THREADS_PER_WORKER,
    min_target_size_bytes: int | None = None,
    shard_cache_dir: str | os.PathLike[str] | Path | None = None,
) -> TargetShardPlan:
    """Build the effective target-DB sharding plan for a local MSA request.

    The policy intentionally scopes adaptive sharding to high-quality EnvDB-backed
    local runs. Fast remains the screening path, even when its shallow-depth EnvDB
    rescue later runs an environmental search.
    """
    normalized_mode = _normalize_mode(mode)
    normalized_preset = str(preset or "fast").strip().lower()
    target_path = Path(target_db)
    total_threads = max(1, int(total_threads or 1))
    requested = max(1, int(requested_shards or DEFAULT_TARGET_SHARDS))
    min_threads_per_worker = max(1, int(min_threads_per_worker or DEFAULT_MIN_THREADS_PER_WORKER))
    min_target_size_bytes = int(min_target_size_bytes if min_target_size_bytes is not None else _min_size_bytes_from_gb(None))
    resolved_cache_dir = Path(shard_cache_dir) if shard_cache_dir else target_path.parent / ".target_shards"

    def _disabled(reason: str) -> TargetShardPlan:
        if normalized_mode == "required":
            raise ValueError(reason)
        return TargetShardPlan(
            enabled=False,
            mode=normalized_mode,
            preset=normalized_preset,
            reason=reason,
            shard_count=0,
            total_threads=total_threads,
            threads_per_worker=total_threads,
            fallback_allowed=True,
            target_db=str(target_path),
            shard_cache_dir=str(resolved_cache_dir),
        )

    if normalized_mode == "off":
        return TargetShardPlan(
            enabled=False,
            mode=normalized_mode,
            preset=normalized_preset,
            reason="target DB sharding disabled by explicit off mode",
            shard_count=0,
            total_threads=total_threads,
            threads_per_worker=total_threads,
            fallback_allowed=False,
            target_db=str(target_path),
            shard_cache_dir=str(resolved_cache_dir),
        )

    if normalized_preset not in QUALITY_ENVDB_PRESETS:
        return _disabled(f"target DB sharding is scoped to EnvDB-backed quality presets, not {normalized_preset!r}/fast screening")
    if not bool(use_env):
        return _disabled("target DB sharding requires effective EnvDB usage")
    if not bool(env_available) or not _target_db_ready(target_path):
        return _disabled("target DB sharding requires an available EnvDB target database")
    if requested <= 1:
        return _disabled("target DB sharding requires requested_shards greater than 1")
    if requested > total_threads:
        return _disabled(
            f"requested_shards={requested} exceeds available total_threads={total_threads}"
        )

    threads_per_worker = total_threads // requested
    if threads_per_worker < min_threads_per_worker:
        return _disabled(
            f"requested_shards={requested} with total_threads={total_threads} leaves only "
            f"{threads_per_worker} thread(s) per worker; need at least {min_threads_per_worker}"
        )

    target_size = _target_size_bytes(target_path)
    if min_target_size_bytes > 0 and target_size < min_target_size_bytes:
        return _disabled(
            f"target DB is below sharding size threshold ({target_size} < {min_target_size_bytes} bytes)"
        )

    return TargetShardPlan(
        enabled=True,
        mode=normalized_mode,
        preset=normalized_preset,
        reason=(
            f"{normalized_preset} EnvDB target search will use MMseqs native target splitting "
            f"with split={requested} within total thread budget {total_threads}"
        ),
        shard_count=requested,
        total_threads=total_threads,
        threads_per_worker=threads_per_worker,
        fallback_allowed=(normalized_mode == "auto"),
        target_db=str(target_path),
        shard_cache_dir=str(resolved_cache_dir),
    )


def build_target_shard_plan_from_gb(
    *,
    target_shard_min_size_gb: float | int | None,
    **kwargs: Any,
) -> TargetShardPlan:
    return build_target_shard_plan(
        min_target_size_bytes=_min_size_bytes_from_gb(target_shard_min_size_gb),
        **kwargs,
    )


def _manifest_payload(
    *,
    target_db: Path,
    shard_count: int,
    shards: Sequence[Path],
) -> dict[str, Any]:
    return {
        "version": 1,
        "target": _target_signature(target_db),
        "shard_count": int(shard_count),
        "created_at": time.time(),
        "shards": [str(path.resolve()) for path in shards],
    }


def _manifest_valid(manifest_path: Path, *, target_db: Path, shard_count: int) -> tuple[bool, tuple[Path, ...]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False, ()
    if not isinstance(payload, dict):
        return False, ()
    if int(payload.get("shard_count") or 0) != int(shard_count):
        return False, ()
    if payload.get("target") != _target_signature(target_db):
        return False, ()
    raw_shards = payload.get("shards")
    if not isinstance(raw_shards, list) or len(raw_shards) != shard_count:
        return False, ()
    shards: list[Path] = []
    for item in raw_shards:
        shard = Path(str(item))
        if not shard.exists() or not Path(str(shard) + ".dbtype").exists():
            return False, ()
        shards.append(shard)
    return True, tuple(shards)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def ensure_target_shards(
    *,
    mmseqs_bin: str | os.PathLike[str] | Path,
    target_db: str | os.PathLike[str] | Path,
    shard_count: int,
    shard_cache_dir: str | os.PathLike[str] | Path,
    env: dict[str, str],
    run_mmseqs: MMseqsRunner,
) -> TargetShardMaterialization:
    """Materialize/reuse an MMseqs splitdb target shard set.

    The manifest is tied to the target DB path + stat signature so stale shards
    are rebuilt when the underlying EnvDB changes.
    """
    target_path = Path(target_db)
    if not _target_db_ready(target_path):
        raise FileNotFoundError(f"Target DB is not ready for sharding: {target_path}")
    shard_count = max(1, int(shard_count))
    if shard_count <= 1:
        raise ValueError("shard_count must be greater than 1")

    root = Path(shard_cache_dir)
    shard_dir = root / f"{target_path.name}.shards-{shard_count}"
    manifest_path = shard_dir / "manifest.json"
    lock_path = shard_dir / ".split.lock"
    shard_prefix = shard_dir / "target"
    shard_dir.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        valid, shards = _manifest_valid(manifest_path, target_db=target_path, shard_count=shard_count)
        if valid:
            return TargetShardMaterialization(
                target_db=target_path,
                shard_count=shard_count,
                shard_dir=shard_dir,
                manifest_path=manifest_path,
                shards=shards,
                reused=True,
            )

        # Remove stale shard DB prefixes from prior split attempts while keeping
        # the lock file. MMseqs splitdb creates prefix_N_COUNT plus sidecars.
        for stale in shard_dir.glob("target_*"):
            try:
                if stale.is_dir():
                    continue
                stale.unlink()
            except FileNotFoundError:
                pass

        run_mmseqs(
            mmseqs_bin,
            ["splitdb", str(target_path), str(shard_prefix), "--split", str(shard_count)],
            env,
        )

        shards = tuple(shard_prefix.parent / f"{shard_prefix.name}_{idx}_{shard_count}" for idx in range(shard_count))
        missing = [str(shard) for shard in shards if not shard.exists() or not Path(str(shard) + ".dbtype").exists()]
        if missing:
            raise RuntimeError(f"MMseqs splitdb did not produce expected target shard DBs: {missing}")
        _write_json_atomic(
            manifest_path,
            _manifest_payload(target_db=target_path, shard_count=shard_count, shards=shards),
        )
        return TargetShardMaterialization(
            target_db=target_path,
            shard_count=shard_count,
            shard_dir=shard_dir,
            manifest_path=manifest_path,
            shards=shards,
            reused=False,
        )


def _strip_option_with_value(args: Iterable[Any], option: str) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for item in args:
        token = str(item)
        if skip_next:
            skip_next = False
            continue
        if token == option:
            skip_next = True
            continue
        if token.startswith(option + "="):
            continue
        cleaned.append(token)
    return cleaned


def _strip_split_thread_overrides(args: Iterable[Any]) -> list[str]:
    cleaned = [str(item) for item in args]
    for option in ("--threads", "--split", "--split-mode"):
        cleaned = _strip_option_with_value(cleaned, option)
    return cleaned


def _strip_search_execution_overrides(args: Iterable[Any]) -> list[str]:
    cleaned = _strip_split_thread_overrides(args)
    for option in (
        "--gpu",
        "--gpu-server",
        "--gpu-server-wait-timeout",
        "--prefilter-mode",
        "--db-load-mode",
    ):
        cleaned = _strip_option_with_value(cleaned, option)
    return cleaned


def _prepare_search_params(
    base_search_params: Sequence[Any],
    *,
    shard_target_db: Path,
    shard_result_db: Path,
    shard_tmp_dir: Path,
    extra_search_params: Sequence[Any] | None,
    threads_per_worker: int,
) -> list[str]:
    if len(base_search_params) < 5 or str(base_search_params[0]) != "search":
        raise ValueError("base_search_params must be an MMseqs search command beginning with 'search'")
    params = [str(item) for item in base_search_params]
    params[2] = str(shard_target_db)
    params[3] = str(shard_result_db)
    params[4] = str(shard_tmp_dir)
    params = _strip_search_execution_overrides(params)
    extras = _strip_split_thread_overrides(extra_search_params or ())
    return params + extras + ["--threads", str(max(1, int(threads_per_worker)))]


def run_native_target_split_search(
    *,
    mmseqs_bin: str | os.PathLike[str] | Path,
    base_search_params: Sequence[Any],
    split_count: int,
    total_threads: int,
    env: dict[str, str],
    run_mmseqs: MMseqsRunner,
    extra_search_params: Sequence[Any] | None = None,
    split_mode: int = 0,
) -> None:
    """Run one MMseqs search using its native target-DB splitting support.

    This is the correctness-preserving high-quality path: MMseqs owns the split
    search and any iterative/profile barriers inside the `search` macro. It avoids
    the old BioModStack anti-pattern of launching independent `search` commands
    per target shard and merging their result DBs after each shard has already run
    its own local iterations.
    """
    if len(base_search_params) < 5 or str(base_search_params[0]) != "search":
        raise ValueError("base_search_params must be an MMseqs search command beginning with 'search'")
    split_count = max(1, int(split_count or 1))
    if split_count <= 1:
        raise ValueError("split_count must be greater than 1 for native target splitting")
    total_threads = max(1, int(total_threads or 1))
    params = _strip_search_execution_overrides(base_search_params)
    extras = _strip_split_thread_overrides(extra_search_params or ())
    run_mmseqs(
        mmseqs_bin,
        params
        + extras
        + [
            "--split",
            str(split_count),
            "--split-mode",
            str(int(split_mode)),
            "--threads",
            str(total_threads),
        ],
        env,
    )


def run_sharded_target_search(
    *,
    mmseqs_bin: str | os.PathLike[str] | Path,
    query_db: str | os.PathLike[str] | Path,
    target_db: str | os.PathLike[str] | Path,
    result_db: str | os.PathLike[str] | Path,
    tmp_dir: str | os.PathLike[str] | Path,
    base_search_params: Sequence[Any],
    shards: Sequence[str | os.PathLike[str] | Path],
    threads_per_worker: int,
    env: dict[str, str],
    run_mmseqs: MMseqsRunner,
    extra_search_params: Sequence[Any] | None = None,
    max_parallel_workers: int = 1,
) -> None:
    """Run a target DB search against each shard, then merge result DBs.

    `base_search_params` is the canonical unsharded `mmseqs search` argv. Shard
    execution replaces only target/result/tmp positions and appends the per-worker
    thread budget. The final `mergedbs` output is placed at `result_db`, preserving
    downstream ColabFold/MMseqs steps that consume the original target DB.
    """
    _ = target_db  # kept for call-site readability and future validation
    shard_paths = tuple(Path(shard) for shard in shards)
    if len(shard_paths) <= 1:
        raise ValueError("run_sharded_target_search requires at least two shards")
    tmp_root = Path(tmp_dir)
    tmp_root.mkdir(parents=True, exist_ok=True)
    result_paths = tuple(tmp_root / f"shard_{idx}_result" for idx in range(len(shard_paths)))

    def _run_one(idx: int) -> None:
        shard_tmp = tmp_root / f"shard_{idx}_tmp"
        shard_tmp.mkdir(parents=True, exist_ok=True)
        params = _prepare_search_params(
            base_search_params,
            shard_target_db=shard_paths[idx],
            shard_result_db=result_paths[idx],
            shard_tmp_dir=shard_tmp,
            extra_search_params=extra_search_params,
            threads_per_worker=threads_per_worker,
        )
        run_mmseqs(mmseqs_bin, params, env)

    workers = max(1, min(int(max_parallel_workers or 1), len(shard_paths)))
    if workers == 1:
        for idx in range(len(shard_paths)):
            _run_one(idx)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_one, idx) for idx in range(len(shard_paths))]
            for future in futures:
                future.result()

    missing = [str(path) for path in result_paths if not path.exists() or not Path(str(path) + ".dbtype").exists()]
    if missing:
        raise RuntimeError(f"Shard search did not produce expected result DBs: {missing}")

    run_mmseqs(
        mmseqs_bin,
        ["mergedbs", str(query_db), str(result_db), *[str(path) for path in result_paths]],
        env,
    )

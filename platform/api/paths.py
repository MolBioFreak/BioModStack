from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


def _resolve_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def get_code_root() -> Path:
    env = os.getenv("BMS_HOME")
    if env:
        return _resolve_path(env)
    # api/paths.py -> api -> platform -> repo root
    return Path(__file__).resolve().parents[2]


def get_data_root() -> Path:
    env = os.getenv("BMS_DATA")
    if env:
        return _resolve_path(env)
    return get_code_root()


def get_inputs_dir() -> Path:
    env = os.getenv("BMS_INPUTS")
    if env:
        return _resolve_path(env)
    return get_code_root() / "platform" / "api" / "inputs"


def get_results_dir() -> Path:
    return get_data_root() / "bms_results"


def get_work_dir() -> Path:
    return get_data_root() / "work"


def _get_default_data_root() -> Path:
    """Return user-space default data root for portability."""
    return Path.home() / ".biomodstack"


def get_weights_root() -> Path:
    env = os.getenv("BMS_WEIGHTS")
    if env:
        return _resolve_path(env)
    return _get_default_data_root() / "weights"


def get_colabfold_db() -> Path:
    env = os.getenv("BMS_COLABFOLD_DB")
    if env:
        return _resolve_path(env)
    return _get_default_data_root() / "colabfold_db"


def get_msa_cache_dir() -> Path:
    env = os.getenv("BMS_MSA_CACHE")
    if env:
        return _resolve_path(env)
    return _get_default_data_root() / "msa_cache"


def get_sabdab_cache_dir() -> Path:
    env = os.getenv("BMS_SABDAB_CACHE")
    if env:
        return _resolve_path(env)
    return _get_default_data_root() / "sabdab_cache"


def _sqlite_path_from_url(db_url: str) -> Path | None:
    if not db_url.startswith("sqlite"):
        return None
    if ":///" in db_url:
        path = db_url.split(":///")[-1]
        return _resolve_path(path)
    parsed = urlparse(db_url)
    if parsed.path:
        return _resolve_path(parsed.path)
    return None


def get_db_path() -> Path:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        parsed = _sqlite_path_from_url(db_url)
        if parsed:
            return parsed
    env = os.getenv("BMS_DB_PATH")
    if env:
        return _resolve_path(env)
    if os.getenv("BMS_DATA"):
        return get_data_root() / "biomodstack.db"
    return Path(__file__).resolve().parent / "biomodstack.db"


def get_db_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{get_db_path()}"


def get_allowed_roots() -> dict[str, Path]:
    code_root = get_code_root()
    return {
        "bms_results": get_results_dir(),
        "benchmarkdata": code_root / "benchmarkdata",
        "lib": code_root / "lib",
        "rcsb": code_root / "rcsb",
        "inputs": get_inputs_dir(),
    }


def resolve_allowed_path(rel_path: str) -> Path:
    rel_path = rel_path.strip().lstrip("/")
    if not rel_path:
        raise ValueError("Empty path")
    parts = Path(rel_path).parts
    root_key = parts[0]
    roots = get_allowed_roots()
    root = roots.get(root_key)
    if not root:
        raise ValueError(f"Root not allowed: {root_key}")
    candidate = (root / Path(*parts[1:])).resolve()
    root_resolved = root.resolve()
    if not str(candidate).startswith(str(root_resolved)):
        raise ValueError("Path escapes allowed root")
    return candidate


def to_allowed_relative(path: Path) -> str:
    resolved = path.resolve()
    for key, root in get_allowed_roots().items():
        root_resolved = root.resolve()
        try:
            rel = resolved.relative_to(root_resolved)
            return str(Path(key) / rel)
        except ValueError:
            continue
    raise ValueError("Path not under allowed roots")

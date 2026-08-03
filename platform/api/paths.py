from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biomodstack_runtime_profile import resolve_runtime_paths


def _resolve_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def get_code_root() -> Path:
    env = os.getenv("BMS_HOME")
    if env:
        return _resolve_path(env)
    # api/paths.py -> api -> platform -> repo root
    return Path(__file__).resolve().parents[2]


def _default_data_root() -> Path:
    return Path.home() / ".biomodstack"


def _runtime_paths() -> dict[str, object]:
    return resolve_runtime_paths(project_root=get_code_root())


def _candidate_data_roots() -> list[Path]:
    return [
        Path("/mnt/BioModStack"),
        _default_data_root(),
    ]


def _looks_like_data_root(path: Path) -> bool:
    if not path.exists():
        return False
    markers = (
        "biomodstack.db",
        "bms_results",
        "work",
        "analysis_cache",
    )
    return any((path / marker).exists() for marker in markers)


def get_data_root() -> Path:
    return Path(str(_runtime_paths()["data_root"]))


def resolve_runtime_data_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.exists():
        return candidate

    current_data_root = get_data_root().resolve()
    alias_roots: list[Path] = [current_data_root]

    container_state_path = str(_runtime_paths().get("container_state_path") or "").strip()
    if container_state_path:
        alias_roots.append(Path(container_state_path).expanduser().resolve())

    alias_roots.extend(root.expanduser().resolve() for root in _candidate_data_roots())

    seen: set[str] = set()
    ordered_alias_roots: list[Path] = []
    for root in alias_roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        ordered_alias_roots.append(root)

    for alias_root in ordered_alias_roots:
        if alias_root == current_data_root:
            continue
        try:
            relative_path = candidate.relative_to(alias_root)
        except ValueError:
            continue
        remapped = (current_data_root / relative_path).resolve()
        if remapped.exists():
            return remapped

    return candidate


def get_inputs_dir() -> Path:
    return Path(str(_runtime_paths()["inputs_dir"]))


def get_results_dir() -> Path:
    return get_data_root() / "bms_results"


def get_analysis_cache_dir() -> Path:
    return get_data_root() / "analysis_cache"


def get_work_dir() -> Path:
    return get_data_root() / "work"


def get_mobile_ui_updates_dir() -> Path:
    env = os.getenv("BMS_MOBILE_UI_UPDATES_DIR")
    if env:
        return _resolve_path(env)
    return get_data_root() / "mobile-ui-updates"


def get_mobile_apk_updates_dir() -> Path:
    """Return the configurable root containing immutable native APK channels."""
    env = os.getenv("BMS_MOBILE_APK_UPDATES_DIR")
    if env:
        return _resolve_path(env)
    return get_data_root() / "mobile-apk-updates"


def get_container_dir() -> Path:
    return Path(str(_runtime_paths()["container_dir"]))


def get_container_path(container_name: str) -> Path:
    return get_container_dir() / container_name


def get_rfd_models_dir() -> Path:
    env = os.getenv("BMS_RFD_MODELS")
    if env:
        return _resolve_path(env)

    weights_root = get_weights_root()
    default_dir = weights_root / "rfd"
    if default_dir.exists():
        return default_dir

    # RFantibody bundles an RFdiffusion checkpoint with different naming.
    rfantibody_dir = weights_root / "rfantibody" / "rfantibody_repo" / "weights"
    if (rfantibody_dir / "RFdiffusion_Ab.pt").exists():
        return rfantibody_dir

    return default_dir


def get_weights_root() -> Path:
    return Path(str(_runtime_paths()["weights_root"]))


def get_colabfold_db() -> Path:
    return Path(str(_runtime_paths()["colabfold_db"]))


def get_msa_cache_dir() -> Path:
    return Path(str(_runtime_paths()["msa_cache_dir"]))


def get_sabdab_cache_dir() -> Path:
    return Path(str(_runtime_paths()["sabdab_cache_dir"]))


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
    return Path(str(_runtime_paths()["db_path"]))


def get_db_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{get_db_path()}"


def get_experiment_db_path() -> Path:
    """Return the dedicated global experiment-control SQLite path."""
    configured = os.getenv("BMS_EXPERIMENT_DB_PATH")
    if configured:
        return _resolve_path(configured)
    return get_data_root() / "experiments.db"


def get_experiment_db_url() -> str:
    """Return the future-portable global experiment-control database URL."""
    return os.getenv("BMS_EXPERIMENT_DATABASE_URL") or f"sqlite+aiosqlite:///{get_experiment_db_path()}"


def get_allowed_roots() -> dict[str, Path]:
    code_root = get_code_root()
    roots = {
        "bms_results": get_results_dir(),
        "analysis_cache": get_analysis_cache_dir(),
        "work": get_work_dir(),
        "benchmarkdata": code_root / "benchmarkdata",
        "lib": code_root / "lib",
        "rcsb": code_root / "rcsb",
        "inputs": get_inputs_dir(),
    }
    # Host filesystem roots for Nanopore/NGS data browsing
    home = Path.home()
    downloads = home / "Downloads"
    if downloads.exists():
        roots["downloads"] = downloads
    data_root = get_data_root()
    if data_root.exists() and data_root != code_root:
        roots["data"] = data_root
    return roots


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
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes allowed root") from exc
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

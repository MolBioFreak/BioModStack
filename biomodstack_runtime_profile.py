from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

INSTALL_PROFILE_FILENAME = "install_profile.json"
CORE_RUNTIME_ENV_FILENAME = "core-runtime.env"
COMPAT_ENV_FILENAME = "env.sh"

DEFAULT_CONTAINER_STATE_PATH = "/var/lib/biomodstack"
DEFAULT_API_HOST_PORT = 8000
DEFAULT_DEV_WEB_HOST_PORT = 5173
DEFAULT_WEB_HOST_PORT = 18080
DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:18080",
    "http://localhost",
    "https://localhost",
    "http://localhost:5173",
    "http://localhost:18080",
    "https://localhost:5173",
    "https://127.0.0.1",
]
DEFAULT_WORKFLOW_ADAPTER_URL = "http://127.0.0.1:8001"
DEFAULT_COMPOSE_PROJECT_NAME = "biomodstack-core-runtime"

_PATH_FIELDS = (
    "data_root",
    "inputs_dir",
    "db_path",
    "container_dir",
    "weights_root",
    "colabfold_db",
    "msa_cache_dir",
    "sabdab_cache_dir",
)
_CONFIG_FIELDS = (
    "container_state_path",
    "inputs_container_path",
    "db_container_path",
    "workflow_adapter_url",
    "compose_project_name",
)
_INT_FIELDS = ("api_host_port", "dev_web_host_port", "web_host_port")
_FEATURE_DEFAULTS = {
    "bioxp": True,
    "stats_tools": True,
    "assay_db": True,
}
_FEATURE_ENV_NAMES = {
    "bioxp": "BMS_FEATURE_BIOXP",
    "stats_tools": "BMS_FEATURE_STATS_TOOLS",
    "assay_db": "BMS_FEATURE_ASSAY_DB",
}


def _resolve_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def _normalize_optional_path(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return str(_resolve_path(text))


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _normalize_optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Cannot parse boolean value: {value!r}")


def _normalize_feature_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def _normalize_optional_features(value: object) -> dict[str, bool] | None:
    if value is None or value == "":
        return None
    if not isinstance(value, Mapping):
        raise ValueError("features must be a mapping of feature names to booleans")

    normalized: dict[str, bool] = {}
    for raw_key, raw_value in value.items():
        key = _normalize_feature_key(raw_key)
        if key not in _FEATURE_DEFAULTS:
            continue
        parsed = _normalize_optional_bool(raw_value)
        if parsed is not None:
            normalized[key] = parsed
    return normalized or None


def _resolve_features(profile: Mapping[str, object]) -> dict[str, bool]:
    resolved = dict(_FEATURE_DEFAULTS)
    profile_features = profile.get("features")
    if isinstance(profile_features, Mapping):
        for raw_key, raw_value in profile_features.items():
            key = _normalize_feature_key(raw_key)
            if key not in resolved:
                continue
            parsed = _normalize_optional_bool(raw_value)
            if parsed is not None:
                resolved[key] = parsed

    for key, env_name in _FEATURE_ENV_NAMES.items():
        if os.getenv(env_name) is None:
            continue
        parsed = _normalize_optional_bool(os.getenv(env_name))
        if parsed is not None:
            resolved[key] = parsed
    return resolved


def _normalize_cors_origins(value: object) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        raise ValueError("cors_origins must be a comma-separated string or list of strings")
    normalized = [item for item in items if item]
    return normalized or None


def get_biomodstack_config_dir() -> Path:
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return _resolve_path(xdg_config_home) / "biomodstack"
    return Path.home().resolve() / ".config" / "biomodstack"


def get_install_profile_path() -> Path:
    return get_biomodstack_config_dir() / INSTALL_PROFILE_FILENAME


def get_core_runtime_env_path() -> Path:
    return get_biomodstack_config_dir() / CORE_RUNTIME_ENV_FILENAME


def get_compat_env_path() -> Path:
    return Path.home().resolve() / ".biomodstack" / COMPAT_ENV_FILENAME


def get_project_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    env = os.getenv("BMS_HOME")
    if env:
        return _resolve_path(env)
    return Path(__file__).resolve().parent


def _default_data_root() -> Path:
    return Path.home().resolve() / ".biomodstack"


def _candidate_data_roots() -> list[Path]:
    return [Path("/mnt/BioModStack"), _default_data_root()]


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


def _heuristic_data_root(project_root: Path) -> Path:
    for candidate in _candidate_data_roots():
        resolved = candidate.expanduser().resolve()
        if _looks_like_data_root(resolved):
            return resolved
    return project_root


def _sqlite_path_from_url(db_url: str) -> Path | None:
    if not db_url.startswith("sqlite"):
        return None
    if ":///" in db_url:
        return _resolve_path(db_url.split(":///")[-1])
    parsed = urlparse(db_url)
    if parsed.path:
        return _resolve_path(parsed.path)
    return None


def normalize_install_profile(raw: Mapping[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    normalized: dict[str, object] = {}

    for key in _PATH_FIELDS:
        value = _normalize_optional_path(raw.get(key))
        if value is not None:
            normalized[key] = value

    for key in _CONFIG_FIELDS:
        value = _normalize_optional_string(raw.get(key))
        if value is not None:
            normalized[key] = value

    for key in _INT_FIELDS:
        value = _normalize_optional_int(raw.get(key))
        if value is not None:
            normalized[key] = value

    cors_origins = _normalize_cors_origins(raw.get("cors_origins"))
    if cors_origins is not None:
        normalized["cors_origins"] = cors_origins

    core_runtime_mode = _normalize_optional_bool(raw.get("core_runtime_mode"))
    if core_runtime_mode is not None:
        normalized["core_runtime_mode"] = core_runtime_mode

    features = _normalize_optional_features(raw.get("features"))
    if features is not None:
        normalized["features"] = features

    return normalized


def load_install_profile() -> dict[str, object]:
    path = get_install_profile_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, Mapping):
        return {}
    return normalize_install_profile(data)


def _coerce_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return int(value)


def _coerce_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _profile_data_base(profile: Mapping[str, object]) -> Path:
    data_root = profile.get("data_root")
    if isinstance(data_root, str) and data_root.strip():
        return Path(data_root).expanduser().resolve()
    return _default_data_root()


def resolve_runtime_paths(
    project_root: Path | None = None,
    profile: Mapping[str, object] | None = None,
) -> dict[str, object]:
    root = get_project_root(project_root)
    normalized_profile = normalize_install_profile(profile if profile is not None else load_install_profile())

    data_root = _resolve_path(os.environ["BMS_DATA"]) if os.getenv("BMS_DATA") else None
    if data_root is None:
        profile_data_root = normalized_profile.get("data_root")
        if isinstance(profile_data_root, str) and profile_data_root.strip():
            data_root = Path(profile_data_root).expanduser().resolve()
        else:
            data_root = _heuristic_data_root(root)

    if os.getenv("BMS_INPUTS"):
        inputs_dir = _resolve_path(os.environ["BMS_INPUTS"])
    else:
        profile_inputs_dir = normalized_profile.get("inputs_dir")
        if isinstance(profile_inputs_dir, str) and profile_inputs_dir.strip():
            inputs_dir = Path(profile_inputs_dir).expanduser().resolve()
        elif "data_root" in normalized_profile:
            inputs_dir = data_root / "inputs"
        else:
            inputs_dir = root / "platform" / "api" / "inputs"

    db_url = os.getenv("DATABASE_URL")
    db_path = _sqlite_path_from_url(db_url) if db_url else None
    if db_path is None:
        if os.getenv("BMS_DB_PATH"):
            db_path = _resolve_path(os.environ["BMS_DB_PATH"])
        else:
            profile_db_path = normalized_profile.get("db_path")
            if isinstance(profile_db_path, str) and profile_db_path.strip():
                db_path = Path(profile_db_path).expanduser().resolve()
            elif data_root != root:
                db_path = data_root / "biomodstack.db"
            else:
                repo_root_db = root / "biomodstack.db"
                legacy_api_db = root / "platform" / "api" / "biomodstack.db"
                if repo_root_db.exists():
                    db_path = repo_root_db
                elif legacy_api_db.exists():
                    db_path = legacy_api_db
                else:
                    db_path = repo_root_db

    if os.getenv("BMS_CONTAINER_DIR"):
        container_dir = _resolve_path(os.environ["BMS_CONTAINER_DIR"])
    else:
        profile_container_dir = normalized_profile.get("container_dir")
        if isinstance(profile_container_dir, str) and profile_container_dir.strip():
            container_dir = Path(profile_container_dir).expanduser().resolve()
        else:
            container_dir = data_root / "apptainer"

    def resolve_data_like(env_name: str, profile_key: str, leaf: str) -> Path:
        if os.getenv(env_name):
            return _resolve_path(os.environ[env_name])
        profile_value = normalized_profile.get(profile_key)
        if isinstance(profile_value, str) and profile_value.strip():
            return Path(profile_value).expanduser().resolve()
        return data_root / leaf

    weights_root = resolve_data_like("BMS_WEIGHTS", "weights_root", "weights")
    colabfold_db = resolve_data_like("BMS_COLABFOLD_DB", "colabfold_db", "colabfold_db")
    msa_cache_dir = resolve_data_like("BMS_MSA_CACHE", "msa_cache_dir", "msa_cache")
    sabdab_cache_dir = resolve_data_like("BMS_SABDAB_CACHE", "sabdab_cache_dir", "sabdab_cache")

    container_state_path = os.getenv("BMS_CONTAINER_STATE_PATH") or str(
        normalized_profile.get("container_state_path") or DEFAULT_CONTAINER_STATE_PATH
    )
    inputs_container_path = os.getenv("BMS_INPUTS_CONTAINER_PATH") or str(
        normalized_profile.get("inputs_container_path") or f"{container_state_path.rstrip('/')}/inputs"
    )
    db_container_path = os.getenv("BMS_DB_CONTAINER_PATH") or str(
        normalized_profile.get("db_container_path") or f"{container_state_path.rstrip('/')}/biomodstack.db"
    )

    cors_origins = os.getenv("CORS_ORIGINS")
    if cors_origins:
        resolved_cors_origins = [item.strip() for item in cors_origins.split(",") if item.strip()]
    else:
        profile_cors_origins = normalized_profile.get("cors_origins")
        if isinstance(profile_cors_origins, list) and profile_cors_origins:
            resolved_cors_origins = [str(item).strip() for item in profile_cors_origins if str(item).strip()]
        else:
            resolved_cors_origins = list(DEFAULT_CORS_ORIGINS)

    workflow_adapter_url = os.getenv("BMS_WORKFLOW_ADAPTER_URL") or str(
        normalized_profile.get("workflow_adapter_url") or DEFAULT_WORKFLOW_ADAPTER_URL
    )
    compose_project_name = os.getenv("COMPOSE_PROJECT_NAME") or str(
        normalized_profile.get("compose_project_name") or DEFAULT_COMPOSE_PROJECT_NAME
    )
    features = _resolve_features(normalized_profile)

    return {
        "project_root": str(root),
        "data_root": str(data_root),
        "inputs_dir": str(inputs_dir),
        "results_dir": str(data_root / "bms_results"),
        "analysis_cache_dir": str(data_root / "analysis_cache"),
        "work_dir": str(data_root / "work"),
        "db_path": str(db_path),
        "container_dir": str(container_dir),
        "weights_root": str(weights_root),
        "colabfold_db": str(colabfold_db),
        "msa_cache_dir": str(msa_cache_dir),
        "sabdab_cache_dir": str(sabdab_cache_dir),
        "container_state_path": container_state_path,
        "inputs_container_path": inputs_container_path,
        "db_container_path": db_container_path,
        "api_host_port": _coerce_env_int("BMS_API_HOST_PORT", int(normalized_profile.get("api_host_port") or DEFAULT_API_HOST_PORT)),
        "dev_web_host_port": _coerce_env_int(
            "BMS_DEV_WEB_HOST_PORT",
            int(normalized_profile.get("dev_web_host_port") or DEFAULT_DEV_WEB_HOST_PORT),
        ),
        "web_host_port": _coerce_env_int("BMS_WEB_HOST_PORT", int(normalized_profile.get("web_host_port") or DEFAULT_WEB_HOST_PORT)),
        "cors_origins": resolved_cors_origins,
        "workflow_adapter_url": workflow_adapter_url,
        "compose_project_name": compose_project_name,
        "features": features,
        "core_runtime_mode": _coerce_env_bool(
            "BMS_CORE_RUNTIME_MODE",
            bool(normalized_profile.get("core_runtime_mode", True)),
        ),
    }


def _compat_env_lines(resolved: Mapping[str, object]) -> list[str]:
    cors_origins = ",".join(str(item) for item in resolved["cors_origins"])
    core_runtime_mode = "1" if bool(resolved["core_runtime_mode"]) else "0"
    return [
        "#!/bin/bash",
        "# Generated by BioModStack install-profile management. Explicit environment variables still win.",
        f'export BMS_DATA="${{BMS_DATA:-{resolved["data_root"]}}}"',
        f'export BMS_INPUTS="${{BMS_INPUTS:-{resolved["inputs_dir"]}}}"',
        f'export BMS_DB_PATH="${{BMS_DB_PATH:-{resolved["db_path"]}}}"',
        f'export BMS_CONTAINER_DIR="${{BMS_CONTAINER_DIR:-{resolved["container_dir"]}}}"',
        f'export BMS_WEIGHTS="${{BMS_WEIGHTS:-{resolved["weights_root"]}}}"',
        f'export BMS_COLABFOLD_DB="${{BMS_COLABFOLD_DB:-{resolved["colabfold_db"]}}}"',
        f'export BMS_MSA_CACHE="${{BMS_MSA_CACHE:-{resolved["msa_cache_dir"]}}}"',
        f'export BMS_SABDAB_CACHE="${{BMS_SABDAB_CACHE:-{resolved["sabdab_cache_dir"]}}}"',
        f'export BMS_WORK="${{BMS_WORK:-{resolved["work_dir"]}}}"',
        f'export BMS_STATE_DIR="${{BMS_STATE_DIR:-{resolved["data_root"]}}}"',
        f'export BMS_CONTAINER_STATE_PATH="${{BMS_CONTAINER_STATE_PATH:-{resolved["container_state_path"]}}}"',
        f'export BMS_INPUTS_CONTAINER_PATH="${{BMS_INPUTS_CONTAINER_PATH:-{resolved["inputs_container_path"]}}}"',
        f'export BMS_DB_CONTAINER_PATH="${{BMS_DB_CONTAINER_PATH:-{resolved["db_container_path"]}}}"',
        f'export BMS_API_HOST_PORT="${{BMS_API_HOST_PORT:-{resolved["api_host_port"]}}}"',
        f'export BMS_DEV_WEB_HOST_PORT="${{BMS_DEV_WEB_HOST_PORT:-{resolved["dev_web_host_port"]}}}"',
        f'export BMS_WEB_HOST_PORT="${{BMS_WEB_HOST_PORT:-{resolved["web_host_port"]}}}"',
        f'export CORS_ORIGINS="${{CORS_ORIGINS:-{cors_origins}}}"',
        f'export BMS_CORE_RUNTIME_MODE="${{BMS_CORE_RUNTIME_MODE:-{core_runtime_mode}}}"',
        f'export BMS_FEATURE_BIOXP="${{BMS_FEATURE_BIOXP:-{1 if resolved["features"]["bioxp"] else 0}}}"',
        f'export BMS_FEATURE_STATS_TOOLS="${{BMS_FEATURE_STATS_TOOLS:-{1 if resolved["features"]["stats_tools"] else 0}}}"',
        f'export BMS_FEATURE_ASSAY_DB="${{BMS_FEATURE_ASSAY_DB:-{1 if resolved["features"]["assay_db"] else 0}}}"',
        f'export BMS_WORKFLOW_ADAPTER_URL="${{BMS_WORKFLOW_ADAPTER_URL:-{resolved["workflow_adapter_url"]}}}"',
        f'export COMPOSE_PROJECT_NAME="${{COMPOSE_PROJECT_NAME:-{resolved["compose_project_name"]}}}"',
        "",
    ]


def _core_runtime_env_lines(resolved: Mapping[str, object]) -> list[str]:
    cors_origins = ",".join(str(item) for item in resolved["cors_origins"])
    core_runtime_mode = "1" if bool(resolved["core_runtime_mode"]) else "0"
    return [
        "# Generated by BioModStack install-profile management.",
        f'BMS_STATE_DIR={resolved["data_root"]}',
        f'BMS_DATA={resolved["data_root"]}',
        f'BMS_INPUTS={resolved["inputs_dir"]}',
        f'BMS_DB_PATH={resolved["db_path"]}',
        f'BMS_CONTAINER_DIR={resolved["container_dir"]}',
        f'BMS_WEIGHTS={resolved["weights_root"]}',
        f'BMS_COLABFOLD_DB={resolved["colabfold_db"]}',
        f'BMS_MSA_CACHE={resolved["msa_cache_dir"]}',
        f'BMS_SABDAB_CACHE={resolved["sabdab_cache_dir"]}',
        f'BMS_WORK={resolved["work_dir"]}',
        f'BMS_CONTAINER_STATE_PATH={resolved["container_state_path"]}',
        f'BMS_INPUTS_CONTAINER_PATH={resolved["inputs_container_path"]}',
        f'BMS_DB_CONTAINER_PATH={resolved["db_container_path"]}',
        f'BMS_API_HOST_PORT={resolved["api_host_port"]}',
        f'BMS_DEV_WEB_HOST_PORT={resolved["dev_web_host_port"]}',
        f'BMS_WEB_HOST_PORT={resolved["web_host_port"]}',
        f'CORS_ORIGINS={cors_origins}',
        f'BMS_CORE_RUNTIME_MODE={core_runtime_mode}',
        f'BMS_FEATURE_BIOXP={1 if resolved["features"]["bioxp"] else 0}',
        f'BMS_FEATURE_STATS_TOOLS={1 if resolved["features"]["stats_tools"] else 0}',
        f'BMS_FEATURE_ASSAY_DB={1 if resolved["features"]["assay_db"] else 0}',
        f'BMS_WORKFLOW_ADAPTER_URL={resolved["workflow_adapter_url"]}',
        f'COMPOSE_PROJECT_NAME={resolved["compose_project_name"]}',
        "",
    ]


def export_install_profile(profile: Mapping[str, object] | None = None, project_root: Path | None = None) -> dict[str, str]:
    normalized_profile = normalize_install_profile(profile if profile is not None else load_install_profile())
    resolved = resolve_runtime_paths(project_root=project_root, profile=normalized_profile)

    compat_env_path = get_compat_env_path()
    compat_env_path.parent.mkdir(parents=True, exist_ok=True)
    compat_env_path.write_text("\n".join(_compat_env_lines(resolved)), encoding="utf-8")

    core_runtime_env_path = get_core_runtime_env_path()
    core_runtime_env_path.parent.mkdir(parents=True, exist_ok=True)
    core_runtime_env_path.write_text("\n".join(_core_runtime_env_lines(resolved)), encoding="utf-8")

    return {
        "compat_env_path": str(compat_env_path),
        "core_runtime_env_path": str(core_runtime_env_path),
    }


def save_install_profile(raw: Mapping[str, object], project_root: Path | None = None) -> dict[str, object]:
    normalized = normalize_install_profile(raw)
    profile_path = get_install_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    export_install_profile(normalized, project_root=project_root)
    return normalized


def install_profile_snapshot(
    profile: Mapping[str, object] | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    profile_path = get_install_profile_path()
    normalized_profile = normalize_install_profile(profile if profile is not None else load_install_profile())
    return {
        "profile_path": str(profile_path),
        "profile_exists": profile_path.exists(),
        "compat_env_path": str(get_compat_env_path()),
        "core_runtime_env_path": str(get_core_runtime_env_path()),
        "profile": normalized_profile,
        "resolved": resolve_runtime_paths(project_root=project_root, profile=normalized_profile),
    }


def resolve_install_features(profile: Mapping[str, object] | None = None) -> dict[str, bool]:
    normalized_profile = normalize_install_profile(profile if profile is not None else load_install_profile())
    return _resolve_features(normalized_profile)


def install_feature_enabled(feature_name: str, profile: Mapping[str, object] | None = None) -> bool:
    key = _normalize_feature_key(feature_name)
    if key not in _FEATURE_DEFAULTS:
        raise ValueError(f"unknown BioModStack install feature: {feature_name}")
    return resolve_install_features(profile=profile)[key]

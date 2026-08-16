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
BMS_PORT_REGISTRY: dict[str, int] = {
    "production_api": 18000,
    "development_workflow_adapter": 18001,
    "production_workflow_adapter": 18101,
    "development_api": 18002,
    "production_web": 18080,
    "production_tailnet_proxy": 18081,
    "development_web": 18082,
    "stats_web": 18180,
    "stats_api": 18181,
    "cpu_collector": 18797,
    "mk1d_host_agent": 18798,
}
DEFAULT_API_HOST_PORT = BMS_PORT_REGISTRY["production_api"]
DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_PORT = BMS_PORT_REGISTRY["development_workflow_adapter"]
DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT = BMS_PORT_REGISTRY["production_workflow_adapter"]
# Compatibility constant for unqualified callers. Lane-aware code must use
# one of the explicit Development or Production constants above.
DEFAULT_WORKFLOW_ADAPTER_PORT = DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT
DEFAULT_DEV_API_HOST_PORT = BMS_PORT_REGISTRY["development_api"]
DEFAULT_WEB_HOST_PORT = BMS_PORT_REGISTRY["production_web"]
DEFAULT_TAILNET_PROXY_PORT = BMS_PORT_REGISTRY["production_tailnet_proxy"]
DEFAULT_DEV_WEB_HOST_PORT = BMS_PORT_REGISTRY["development_web"]
DEFAULT_STATS_WEB_PORT = BMS_PORT_REGISTRY["stats_web"]
DEFAULT_STATS_API_PORT = BMS_PORT_REGISTRY["stats_api"]
DEFAULT_CPU_POWER_PORT = BMS_PORT_REGISTRY["cpu_collector"]
DEFAULT_HOST_AGENT_PORT = BMS_PORT_REGISTRY["mk1d_host_agent"]
DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1",
    f"http://127.0.0.1:{DEFAULT_DEV_WEB_HOST_PORT}",
    "http://127.0.0.1:18080",
    "http://localhost",
    "https://localhost",
    f"http://localhost:{DEFAULT_DEV_WEB_HOST_PORT}",
    "http://localhost:18080",
    f"https://localhost:{DEFAULT_DEV_WEB_HOST_PORT}",
    "https://127.0.0.1",
]
DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_URL = f"http://127.0.0.1:{DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_PORT}"
DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_URL = f"http://127.0.0.1:{DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT}"
# The unqualified URL is retained for old callers and now denotes the stable
# Production control plane. New code must select a lane explicitly.
DEFAULT_WORKFLOW_ADAPTER_URL = DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_URL
DEFAULT_COMPOSE_PROJECT_NAME = "biomodstack-core-runtime"

# Migrate only listener defaults that this repository historically owned. Do
# not rewrite arbitrary ports (notably native ONT/MinKNOW listeners).
_LEGACY_PORT_MIGRATIONS = {
    "api_host_port": {8000: DEFAULT_API_HOST_PORT},
    "dev_api_host_port": {8002: DEFAULT_DEV_API_HOST_PORT},
    "dev_web_host_port": {5173: DEFAULT_DEV_WEB_HOST_PORT},
}
_LEGACY_CONFIG_MIGRATIONS = {
    "workflow_adapter_url": {"http://127.0.0.1:8001": DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_URL},
}
_LEGACY_CORS_ORIGIN_MIGRATIONS = {
    "http://127.0.0.1:5173": "http://127.0.0.1:18082",
    "http://localhost:5173": "http://localhost:18082",
    "https://localhost:5173": "https://localhost:18082",
}

_PATH_FIELDS = (
    "data_root",
    "results_dir",
    "inputs_dir",
    "db_path",
    "container_dir",
    "dev_data_root",
    "dev_results_dir",
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
    "development_workflow_adapter_url",
    "production_workflow_adapter_url",
    "compose_project_name",
    "api_image",
    "web_image",
    "host_agent_image",
    "cpu_power_image",
)
_IMAGE_ENV_FIELDS = {
    "api_image": "BMS_API_IMAGE",
    "web_image": "BMS_WEB_IMAGE",
    "host_agent_image": "BMS_HOST_AGENT_IMAGE",
    "cpu_power_image": "BMS_CPU_POWER_IMAGE",
}
_INT_FIELDS = ("api_host_port", "dev_api_host_port", "dev_web_host_port", "web_host_port")
# Host-side operational endpoints; application surfaces may never claim them.
RESERVED_AUXILIARY_PORTS: dict[int, str] = {
    DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_PORT: "Development workflow adapter",
    DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT: "Production workflow adapter",
    DEFAULT_TAILNET_PROXY_PORT: "Production Tailnet proxy",
    DEFAULT_STATS_WEB_PORT: "Stats web",
    DEFAULT_STATS_API_PORT: "Stats API",
    DEFAULT_CPU_POWER_PORT: "CPU telemetry",
    DEFAULT_HOST_AGENT_PORT: "Mk1D host agent",
}
_FEATURE_DEFAULTS = {
    "bioxp": True,
    "molecular_dynamics": False,
}
_FEATURE_ENV_NAMES = {
    "bioxp": "BMS_FEATURE_BIOXP",
    "molecular_dynamics": "BMS_FEATURE_MOLECULAR_DYNAMICS",
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


def _default_dev_data_root() -> Path:
    # Production mounts and local development must not share SQLite, queues,
    # caches, or result directories.
    return Path.home().resolve() / ".biomodstack-dev"


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
            value = _LEGACY_CONFIG_MIGRATIONS.get(key, {}).get(value, value)
            normalized[key] = value

    for key in _INT_FIELDS:
        value = _normalize_optional_int(raw.get(key))
        if value is not None:
            value = _LEGACY_PORT_MIGRATIONS.get(key, {}).get(value, value)
            normalized[key] = value

    cors_origins = _normalize_cors_origins(raw.get("cors_origins"))
    if cors_origins is not None:
        normalized["cors_origins"] = [
            _LEGACY_CORS_ORIGIN_MIGRATIONS.get(origin, origin) for origin in cors_origins
        ]

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
    port = int(value)
    profile_key = {
        "BMS_DEV_API_HOST_PORT": "dev_api_host_port",
        "BMS_DEV_WEB_HOST_PORT": "dev_web_host_port",
        "BMS_WEB_HOST_PORT": "web_host_port",
    }.get(name)
    if profile_key is not None:
        port = _LEGACY_PORT_MIGRATIONS.get(profile_key, {}).get(port, port)
    return port


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

    results_dir = resolve_data_like("BMS_RESULTS_DIR", "results_dir", "bms_results")
    weights_root = resolve_data_like("BMS_WEIGHTS", "weights_root", "weights")
    colabfold_db = resolve_data_like("BMS_COLABFOLD_DB", "colabfold_db", "colabfold_db")
    msa_cache_dir = resolve_data_like("BMS_MSA_CACHE", "msa_cache_dir", "msa_cache")
    sabdab_cache_dir = resolve_data_like("BMS_SABDAB_CACHE", "sabdab_cache_dir", "sabdab_cache")
    profile_dev_data_root = normalized_profile.get("dev_data_root")
    dev_data_root = (
        Path(profile_dev_data_root).expanduser().resolve()
        if isinstance(profile_dev_data_root, str) and profile_dev_data_root.strip()
        else _default_dev_data_root()
    )
    profile_dev_results_dir = normalized_profile.get("dev_results_dir")
    dev_results_dir = (
        Path(profile_dev_results_dir).expanduser().resolve()
        if isinstance(profile_dev_results_dir, str) and profile_dev_results_dir.strip()
        else Path(os.getenv("BMS_DEV_RESULTS_DIR", "")).expanduser().resolve()
        if os.getenv("BMS_DEV_RESULTS_DIR")
        else dev_data_root / "bms_results"
    )

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

    development_workflow_adapter_url = os.getenv("BMS_DEVELOPMENT_WORKFLOW_ADAPTER_URL") or str(
        normalized_profile.get("development_workflow_adapter_url") or DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_URL
    )
    production_workflow_adapter_url = os.getenv("BMS_PRODUCTION_WORKFLOW_ADAPTER_URL") or str(
        normalized_profile.get("production_workflow_adapter_url") or DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_URL
    )
    workflow_adapter_url = os.getenv("BMS_WORKFLOW_ADAPTER_URL") or str(
        normalized_profile.get("workflow_adapter_url") or production_workflow_adapter_url
    )
    compose_project_name = os.getenv("COMPOSE_PROJECT_NAME") or str(
        normalized_profile.get("compose_project_name") or DEFAULT_COMPOSE_PROJECT_NAME
    )
    features = _resolve_features(normalized_profile)
    image_selectors = {
        field: os.getenv(env_name) or normalized_profile.get(field)
        for field, env_name in _IMAGE_ENV_FIELDS.items()
    }

    return {
        "project_root": str(root),
        "data_root": str(data_root),
        "inputs_dir": str(inputs_dir),
        "results_dir": str(results_dir),
        "analysis_cache_dir": str(data_root / "analysis_cache"),
        "work_dir": str(data_root / "work"),
        "db_path": str(db_path),
        "container_dir": str(container_dir),
        "weights_root": str(weights_root),
        "colabfold_db": str(colabfold_db),
        "msa_cache_dir": str(msa_cache_dir),
        "sabdab_cache_dir": str(sabdab_cache_dir),
        "dev_data_root": str(dev_data_root),
        "dev_results_dir": str(dev_results_dir),
        "dev_inputs_dir": str(dev_data_root / "inputs"),
        "dev_db_path": str(dev_data_root / "biomodstack.db"),
        "dev_work_dir": str(dev_data_root / "work"),
        "dev_weights_root": str(dev_data_root / "weights"),
        "dev_colabfold_db": str(dev_data_root / "colabfold_db"),
        "dev_msa_cache_dir": str(dev_data_root / "msa_cache"),
        "dev_sabdab_cache_dir": str(dev_data_root / "sabdab_cache"),
        "container_state_path": container_state_path,
        "inputs_container_path": inputs_container_path,
        "db_container_path": db_container_path,
        # The production API image is deliberately pinned to the registry port. Do not
        # advertise a configurable host port that Docker cannot honor.
        "api_host_port": DEFAULT_API_HOST_PORT,
        "dev_api_host_port": _coerce_env_int(
            "BMS_DEV_API_HOST_PORT",
            int(normalized_profile.get("dev_api_host_port") or DEFAULT_DEV_API_HOST_PORT),
        ),
        "dev_web_host_port": _coerce_env_int(
            "BMS_DEV_WEB_HOST_PORT",
            int(normalized_profile.get("dev_web_host_port") or DEFAULT_DEV_WEB_HOST_PORT),
        ),
        "web_host_port": _coerce_env_int("BMS_WEB_HOST_PORT", int(normalized_profile.get("web_host_port") or DEFAULT_WEB_HOST_PORT)),
        "cors_origins": resolved_cors_origins,
        "workflow_adapter_url": workflow_adapter_url,
        "development_workflow_adapter_url": development_workflow_adapter_url,
        "production_workflow_adapter_url": production_workflow_adapter_url,
        "compose_project_name": compose_project_name,
        "features": features,
        **image_selectors,
        "core_runtime_mode": _coerce_env_bool(
            "BMS_CORE_RUNTIME_MODE",
            bool(normalized_profile.get("core_runtime_mode", True)),
        ),
    }


def validate_runtime_port_contract(resolved: Mapping[str, object]) -> None:
    """Reject ambiguous application channels before a profile or unit is written."""
    port_fields = {
        "api_host_port": resolved.get("api_host_port", DEFAULT_API_HOST_PORT),
        "dev_api_host_port": resolved.get("dev_api_host_port", DEFAULT_DEV_API_HOST_PORT),
        "dev_web_host_port": resolved.get("dev_web_host_port", DEFAULT_DEV_WEB_HOST_PORT),
        "web_host_port": resolved.get("web_host_port", DEFAULT_WEB_HOST_PORT),
    }
    normalized: dict[str, int] = {}
    for field, value in port_fields.items():
        if field == "api_host_port" and value is not None and int(str(value)) != DEFAULT_API_HOST_PORT:
            raise ValueError(
                f"api_host_port is fixed at {DEFAULT_API_HOST_PORT}: the stable container image binds that port"
            )
        try:
            port = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an integer TCP port") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"{field} must be between 1 and 65535")
        if not 18000 <= port <= 18999:
            raise ValueError(
                f"{field} must use the governed BioModStack port neighborhood 18000-18999"
            )
        if port in RESERVED_AUXILIARY_PORTS:
            raise ValueError(
                f"{field} uses reserved BioModStack auxiliary port {port} ({RESERVED_AUXILIARY_PORTS[port]})"
            )
        normalized[field] = port
    duplicates = sorted({port for port in normalized.values() if list(normalized.values()).count(port) > 1})
    if duplicates:
        raise ValueError(f"BioModStack runtime ports must be distinct; duplicate port(s): {', '.join(map(str, duplicates))}")


def _compat_env_lines(resolved: Mapping[str, object]) -> list[str]:
    cors_origins = ",".join(str(item) for item in resolved["cors_origins"])
    core_runtime_mode = "1" if bool(resolved["core_runtime_mode"]) else "0"
    return [
        "#!/bin/bash",
        "# Generated by BioModStack install-profile management. Explicit environment variables still win.",
        f'export BMS_DATA="${{BMS_DATA:-{resolved["data_root"]}}}"',
        f'export BMS_INPUTS="${{BMS_INPUTS:-{resolved["inputs_dir"]}}}"',
        f'export BMS_DB_PATH="${{BMS_DB_PATH:-{resolved["db_path"]}}}"',
        f'export BMS_RESULTS_DIR="${{BMS_RESULTS_DIR:-{resolved["results_dir"]}}}"',
        f'export BMS_DEV_RESULTS_DIR="${{BMS_DEV_RESULTS_DIR:-{resolved["dev_results_dir"]}}}"',
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
        f'export BMS_DEV_API_HOST_PORT="${{BMS_DEV_API_HOST_PORT:-{resolved["dev_api_host_port"]}}}"',
        f'export BMS_DEV_WEB_HOST_PORT="${{BMS_DEV_WEB_HOST_PORT:-{resolved["dev_web_host_port"]}}}"',
        f'export BMS_WEB_HOST_PORT="${{BMS_WEB_HOST_PORT:-{resolved["web_host_port"]}}}"',
        f'export CORS_ORIGINS="${{CORS_ORIGINS:-{cors_origins}}}"',
        f'export BMS_CORE_RUNTIME_MODE="${{BMS_CORE_RUNTIME_MODE:-{core_runtime_mode}}}"',
        'export BMS_WORKFLOW_ADAPTER_LANE="${BMS_WORKFLOW_ADAPTER_LANE:-production}"',
        f'export BMS_FEATURE_BIOXP="${{BMS_FEATURE_BIOXP:-{1 if resolved["features"]["bioxp"] else 0}}}"',
        f'export BMS_FEATURE_MOLECULAR_DYNAMICS="${{BMS_FEATURE_MOLECULAR_DYNAMICS:-{1 if resolved["features"]["molecular_dynamics"] else 0}}}"',
        f'export BMS_WORKFLOW_ADAPTER_URL="${{BMS_WORKFLOW_ADAPTER_URL:-{resolved["workflow_adapter_url"]}}}"',
        f'export BMS_DEVELOPMENT_WORKFLOW_ADAPTER_URL="${{BMS_DEVELOPMENT_WORKFLOW_ADAPTER_URL:-{resolved["development_workflow_adapter_url"]}}}"',
        f'export BMS_PRODUCTION_WORKFLOW_ADAPTER_URL="${{BMS_PRODUCTION_WORKFLOW_ADAPTER_URL:-{resolved["production_workflow_adapter_url"]}}}"',
        f'export COMPOSE_PROJECT_NAME="${{COMPOSE_PROJECT_NAME:-{resolved["compose_project_name"]}}}"',
        "",
    ]


def _core_runtime_env_lines(resolved: Mapping[str, object]) -> list[str]:
    cors_origins = ",".join(str(item) for item in resolved["cors_origins"])
    core_runtime_mode = "1" if bool(resolved["core_runtime_mode"]) else "0"
    image_lines = [
        f"{env_name}={resolved[field]}"
        for field, env_name in _IMAGE_ENV_FIELDS.items()
        if resolved.get(field)
    ]
    return [
        "# Generated by BioModStack install-profile management.",
        *image_lines,
        f'BMS_STATE_DIR={resolved["data_root"]}',
        f'BMS_DATA={resolved["data_root"]}',
        f'BMS_INPUTS={resolved["inputs_dir"]}',
        f'BMS_DB_PATH={resolved["db_path"]}',
        f'BMS_RESULTS_DIR={resolved["results_dir"]}',
        f'BMS_DEV_RESULTS_DIR={resolved["dev_results_dir"]}',
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
        f'BMS_DEV_API_HOST_PORT={resolved["dev_api_host_port"]}',
        f'BMS_DEV_WEB_HOST_PORT={resolved["dev_web_host_port"]}',
        f'BMS_WEB_HOST_PORT={resolved["web_host_port"]}',
        f'CORS_ORIGINS={cors_origins}',
        f'BMS_CORE_RUNTIME_MODE={core_runtime_mode}',
        'BMS_WORKFLOW_ADAPTER_LANE=production',
        f'BMS_FEATURE_BIOXP={1 if resolved["features"]["bioxp"] else 0}',
        f'BMS_FEATURE_MOLECULAR_DYNAMICS={1 if resolved["features"]["molecular_dynamics"] else 0}',
        f'BMS_WORKFLOW_ADAPTER_URL={resolved["workflow_adapter_url"]}',
        f'BMS_DEVELOPMENT_WORKFLOW_ADAPTER_URL={resolved["development_workflow_adapter_url"]}',
        f'BMS_PRODUCTION_WORKFLOW_ADAPTER_URL={resolved["production_workflow_adapter_url"]}',
        f'COMPOSE_PROJECT_NAME={resolved["compose_project_name"]}',
        "",
    ]


def export_install_profile(profile: Mapping[str, object] | None = None, project_root: Path | None = None) -> dict[str, str]:
    normalized_profile = normalize_install_profile(profile if profile is not None else load_install_profile())
    resolved = resolve_runtime_paths(project_root=project_root, profile=normalized_profile)
    validate_runtime_port_contract(resolved)

    compat_env_path = get_compat_env_path()
    compat_env_path.parent.mkdir(parents=True, exist_ok=True)
    compat_env_path.write_text("\n".join(_compat_env_lines(resolved)), encoding="utf-8")

    core_runtime_env_path = get_core_runtime_env_path()
    core_runtime_env_path.parent.mkdir(parents=True, exist_ok=True)
    generated_lines = _core_runtime_env_lines(resolved)
    generated_keys = {
        line.split("=", 1)[0]
        for line in generated_lines
        if line and not line.startswith("#") and "=" in line
    }
    preserved_lines: list[str] = []
    if core_runtime_env_path.exists():
        for raw_line in core_runtime_env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0]
            if key not in generated_keys and key.replace("_", "").isalnum():
                preserved_lines.append(line)
    merged_lines = [generated_lines[0], *preserved_lines, *generated_lines[1:]]
    core_runtime_env_path.write_text("\n".join(merged_lines), encoding="utf-8")
    core_runtime_env_path.chmod(0o600)

    return {
        "compat_env_path": str(compat_env_path),
        "core_runtime_env_path": str(core_runtime_env_path),
    }


def save_install_profile(raw: Mapping[str, object], project_root: Path | None = None) -> dict[str, object]:
    requested_api_port = _normalize_optional_int(raw.get("api_host_port"))
    if requested_api_port is not None and requested_api_port != DEFAULT_API_HOST_PORT:
        raise ValueError(
            f"api_host_port is fixed at {DEFAULT_API_HOST_PORT}: the stable container image binds that port"
        )
    normalized = normalize_install_profile(raw)
    resolved = resolve_runtime_paths(project_root=project_root, profile=normalized)
    validate_runtime_port_contract(resolved)
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

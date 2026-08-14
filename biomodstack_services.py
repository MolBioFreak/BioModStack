from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from textwrap import dedent

from biomodstack_runtime_profile import (
    DEFAULT_API_HOST_PORT,
    DEFAULT_CPU_POWER_PORT,
    DEFAULT_DEV_API_HOST_PORT,
    DEFAULT_DEV_WEB_HOST_PORT,
    DEFAULT_HOST_AGENT_PORT,
    DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_PORT,
    DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT,
    DEFAULT_WEB_HOST_PORT,
    get_biomodstack_config_dir as runtime_profile_config_dir,
    install_profile_snapshot as runtime_profile_snapshot,
    load_install_profile,
    save_install_profile,
    validate_runtime_port_contract,
)

API_SERVICE = "biomodstack-api.service"
FRONTEND_SERVICE = "biomodstack-frontend.service"
TELEMETRY_SERVICE = "biomodstack-telemetry.service"
DEVELOPMENT_LANE = "development"
PRODUCTION_LANE = "production"
DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE = "biomodstack-development-workflow-adapter.service"
PRODUCTION_WORKFLOW_ADAPTER_SERVICE = "biomodstack-production-workflow-adapter.service"
DEV_WORKFLOW_ADAPTER_SERVICE = DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE
PROD_WORKFLOW_ADAPTER_SERVICE = PRODUCTION_WORKFLOW_ADAPTER_SERVICE
# Compatibility alias for code that refers to the stable/core adapter without
# selecting a lane. New service rendering always uses the explicit name.
WORKFLOW_ADAPTER_SERVICE = PRODUCTION_WORKFLOW_ADAPTER_SERVICE
WORKFLOW_PARENT_SLICE = "biomodstack.slice"
WORKFLOW_ROOT_SLICE = "biomodstack-workflows.slice"
DEVELOPMENT_WORKFLOW_SLICE = "biomodstack-workflows-development.slice"
PRODUCTION_WORKFLOW_SLICE = "biomodstack-workflows-production.slice"
TAILNET_GLOBAL_SERVICE = "biomodstack-tailnet-global.service"
CORE_RUNTIME_SERVICE = "biomodstack-core-runtime.service"
TARGET_UNIT = "biomodstack.target"
DEV_TARGET_UNIT = "biomodstack-dev.target"

# Bounds are intentionally conservative for a large workstation.  Operators
# can override each value with the documented BMS_<COMPONENT>_<FIELD>
# environment variable without editing generated units.
SYSTEMD_RESOURCE_LIMITS: dict[str, tuple[str, dict[str, str]]] = {
    API_SERVICE: (
        "API",
        {"MemoryHigh": "12G", "MemoryMax": "16G", "TasksMax": "2048", "LimitNOFILE": "131072"},
    ),
    FRONTEND_SERVICE: (
        "FRONTEND",
        {"MemoryHigh": "2G", "MemoryMax": "4G", "TasksMax": "1024", "LimitNOFILE": "65536"},
    ),
    TELEMETRY_SERVICE: (
        "TELEMETRY",
        {"MemoryHigh": "256M", "MemoryMax": "512M", "TasksMax": "64", "LimitNOFILE": "4096"},
    ),
    DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE: (
        "WORKFLOW_ADAPTER",
        {"MemoryHigh": "48G", "MemoryMax": "64G", "TasksMax": "8192", "LimitNOFILE": "262144"},
    ),
    PRODUCTION_WORKFLOW_ADAPTER_SERVICE: (
        "WORKFLOW_ADAPTER",
        {"MemoryHigh": "48G", "MemoryMax": "64G", "TasksMax": "8192", "LimitNOFILE": "262144"},
    ),
    CORE_RUNTIME_SERVICE: (
        "CORE_RUNTIME",
        {"MemoryHigh": "4G", "MemoryMax": "8G", "TasksMax": "1024", "LimitNOFILE": "131072"},
    ),
}
_SYSTEMD_MEMORY_LIMIT_RE = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?[KMGTPE]$")
_SYSTEMD_COUNT_LIMIT_RE = re.compile(r"^[1-9][0-9]*$")

DEV_RUNTIME_MODE = "dev"
CONTAINER_RUNTIME_MODE = "container"
DEFAULT_RUNTIME_MODE = CONTAINER_RUNTIME_MODE
VALID_RUNTIME_MODES = {DEV_RUNTIME_MODE, CONTAINER_RUNTIME_MODE}

API_PORT = DEFAULT_API_HOST_PORT
DEV_API_PORT = DEFAULT_DEV_API_HOST_PORT
FRONTEND_PORT = DEFAULT_DEV_WEB_HOST_PORT
STABLE_FRONTEND_PORT = DEFAULT_WEB_HOST_PORT
# Unqualified compatibility callers refer to the stable Production listener.
WORKFLOW_ADAPTER_PORT = DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT
DEVELOPMENT_WORKFLOW_ADAPTER_PORT = DEFAULT_DEVELOPMENT_WORKFLOW_ADAPTER_PORT
PRODUCTION_WORKFLOW_ADAPTER_PORT = DEFAULT_PRODUCTION_WORKFLOW_ADAPTER_PORT
CPU_POWER_PORT = DEFAULT_CPU_POWER_PORT
HOST_AGENT_PORT = DEFAULT_HOST_AGENT_PORT
API_HEALTH_URL = f"http://127.0.0.1:{API_PORT}/api/health"
FRONTEND_URL = f"http://127.0.0.1:{STABLE_FRONTEND_PORT}/bms/"
DEVELOPMENT_WORKFLOW_ADAPTER_HEALTH_URL = f"http://127.0.0.1:{DEVELOPMENT_WORKFLOW_ADAPTER_PORT}/api/workflow-adapter/health"
PRODUCTION_WORKFLOW_ADAPTER_HEALTH_URL = f"http://127.0.0.1:{PRODUCTION_WORKFLOW_ADAPTER_PORT}/api/workflow-adapter/health"
WORKFLOW_ADAPTER_HEALTH_URL = PRODUCTION_WORKFLOW_ADAPTER_HEALTH_URL
DEFAULT_HTTP_WAIT_TIMEOUT_SECONDS = 30.0
CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS = 180.0

BROWSER_LAUNCH_SURFACE = "browser"
ELECTRON_LAUNCH_SURFACE = "electron"
NONE_LAUNCH_SURFACE = "none"
SUPPORTED_LAUNCH_SURFACES = (
    BROWSER_LAUNCH_SURFACE,
    ELECTRON_LAUNCH_SURFACE,
    NONE_LAUNCH_SURFACE,
)
DEFAULT_LAUNCH_PREFERENCES = {
    "default_surface": BROWSER_LAUNCH_SURFACE,
    "auto_open_hosted_web_on_start": True,
}

_STATE_HOME = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser().resolve()
LOG_DIR = _STATE_HOME / "biomodstack" / "logs"
API_LOG = LOG_DIR / "api.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"
TELEMETRY_LOG = LOG_DIR / "telemetry.log"
DEVELOPMENT_WORKFLOW_ADAPTER_LOG = LOG_DIR / "development-workflow-adapter.log"
PRODUCTION_WORKFLOW_ADAPTER_LOG = LOG_DIR / "production-workflow-adapter.log"
# Compatibility consumers use the production/core adapter identity by default.
WORKFLOW_ADAPTER_LOG = PRODUCTION_WORKFLOW_ADAPTER_LOG
CORE_RUNTIME_LOG = LOG_DIR / "core-runtime.log"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
_LIFECYCLE_LOCK_FILENAME = "lifecycle.lock"
_lifecycle_lock_state = threading.local()


def _normalize_execution_lane(lane: str) -> str:
    normalized = str(lane or "").strip().lower()
    if normalized not in {DEVELOPMENT_LANE, PRODUCTION_LANE}:
        raise ServiceManagerError(
            f"Unknown workflow execution lane {lane!r}; expected development or production"
        )
    return normalized


def workflow_adapter_service_for_lane(lane: str) -> str:
    return (
        DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE
        if _normalize_execution_lane(lane) == DEVELOPMENT_LANE
        else PRODUCTION_WORKFLOW_ADAPTER_SERVICE
    )


def workflow_adapter_port_for_lane(lane: str) -> int:
    return (
        DEVELOPMENT_WORKFLOW_ADAPTER_PORT
        if _normalize_execution_lane(lane) == DEVELOPMENT_LANE
        else PRODUCTION_WORKFLOW_ADAPTER_PORT
    )


def workflow_adapter_url_for_lane(lane: str) -> str:
    return f"http://127.0.0.1:{workflow_adapter_port_for_lane(lane)}"


def workflow_adapter_health_url_for_lane(lane: str) -> str:
    return f"{workflow_adapter_url_for_lane(lane)}/api/workflow-adapter/health"


def workflow_adapter_log_for_lane(lane: str) -> Path:
    return (
        DEVELOPMENT_WORKFLOW_ADAPTER_LOG
        if _normalize_execution_lane(lane) == DEVELOPMENT_LANE
        else PRODUCTION_WORKFLOW_ADAPTER_LOG
    )


@contextmanager
def lifecycle_mutation_lock(project_root: Path | None = None):
    """Serialize mutating lifecycle operations across launcher processes."""
    depth = getattr(_lifecycle_lock_state, "depth", 0)
    if depth:
        _lifecycle_lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lifecycle_lock_state.depth -= 1
        return

    import fcntl

    lock_path = get_biomodstack_config_dir() / _LIFECYCLE_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        _lifecycle_lock_state.depth = 1
        try:
            yield
        finally:
            _lifecycle_lock_state.depth = 0
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def serialized_lifecycle_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with lifecycle_mutation_lock(kwargs.get("project_root")):
            return function(*args, **kwargs)
    return wrapped


def runtime_log_max_bytes() -> int:
    raw = os.getenv("BMS_RUNTIME_LOG_MAX_BYTES")
    if raw is None:
        return DEFAULT_LOG_MAX_BYTES
    try:
        return max(1024 * 1024, int(raw))
    except ValueError:
        return DEFAULT_LOG_MAX_BYTES


def runtime_log_backup_count() -> int:
    raw = os.getenv("BMS_RUNTIME_LOG_BACKUP_COUNT")
    if raw is None:
        return DEFAULT_LOG_BACKUP_COUNT
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_LOG_BACKUP_COUNT


def rotate_log_file(path: Path, *, max_bytes: int | None = None, backup_count: int | None = None) -> bool:
    max_size = max_bytes if max_bytes is not None else runtime_log_max_bytes()
    backups = backup_count if backup_count is not None else runtime_log_backup_count()
    try:
        if not path.exists() or path.stat().st_size <= max_size:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        oldest = path.with_name(f"{path.name}.{backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(backups - 1, 0, -1):
            current = path.with_name(f"{path.name}.{index}")
            if current.exists():
                current.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        path.touch()
        return True
    except OSError:
        return False


def rotate_runtime_logs() -> None:
    for path in (
        API_LOG,
        FRONTEND_LOG,
        WORKFLOW_ADAPTER_LOG,
        DEVELOPMENT_WORKFLOW_ADAPTER_LOG,
        PRODUCTION_WORKFLOW_ADAPTER_LOG,
        CORE_RUNTIME_LOG,
        TELEMETRY_LOG,
    ):
        rotate_log_file(path)


def get_biomodstack_config_dir() -> Path:
    return runtime_profile_config_dir()


def get_launch_preferences_path() -> Path:
    return get_biomodstack_config_dir() / "launch_preferences.json"


def normalize_launch_preferences(raw: Mapping[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    surface = str(raw.get("default_surface") or BROWSER_LAUNCH_SURFACE).strip().lower()
    if surface not in SUPPORTED_LAUNCH_SURFACES:
        surface = BROWSER_LAUNCH_SURFACE
    return {
        "default_surface": surface,
        "auto_open_hosted_web_on_start": bool(raw.get("auto_open_hosted_web_on_start", True)),
    }


def load_launch_preferences() -> dict[str, object]:
    path = get_launch_preferences_path()
    if not path.exists():
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    if not isinstance(data, Mapping):
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    return normalize_launch_preferences(data)


def install_profile_snapshot(profile: Mapping[str, object] | None = None, project_root: Path | None = None) -> dict[str, object]:
    return runtime_profile_snapshot(profile=profile, project_root=project_root)


def electron_shell_available(project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    shell_dir = root / "platform" / "desktop-electron"
    if not (shell_dir / "package.json").exists():
        return False
    electron_dist = shell_dir / "node_modules" / "electron" / "dist"
    if sys.platform == "darwin":
        return (electron_dist / "Electron.app" / "Contents" / "MacOS" / "Electron").exists()
    binary_name = "electron.exe" if sys.platform == "win32" else "electron"
    return (electron_dist / binary_name).exists()


def build_launch_ui_command(
    project_root: Path | None = None,
    runtime_mode: str | None = CONTAINER_RUNTIME_MODE,
    surface: str | None = ELECTRON_LAUNCH_SURFACE,
    python_executable: str | None = None,
) -> list[str]:
    root = (project_root or get_project_root()).resolve()
    command = [python_executable or sys.executable, str(root / "scripts" / "launch_biomodstack_ui.py")]

    if runtime_mode:
        command.extend(["--runtime", resolve_runtime_mode(runtime_mode)])
    if surface:
        normalized_surface = str(surface).strip().lower()
        if normalized_surface not in SUPPORTED_LAUNCH_SURFACES:
            raise ServiceManagerError(
                f"Unsupported BioModStack launch surface '{surface}'. Expected one of: {', '.join(SUPPORTED_LAUNCH_SURFACES)}"
            )
        command.extend(["--surface", normalized_surface])

    return command


class ServiceManagerError(RuntimeError):
    """Raised when BioModStack service management fails."""


def render_systemd_resource_boundaries(service_name: str) -> str:
    try:
        component, defaults = SYSTEMD_RESOURCE_LIMITS[service_name]
    except KeyError as exc:
        raise ServiceManagerError(f"No resource-limit policy exists for {service_name}") from exc

    rendered: list[str] = []
    for directive, default in defaults.items():
        env_suffix = re.sub(r"(?<!^)(?=[A-Z])", "_", directive).upper()
        value = os.getenv(f"BMS_{component}_{env_suffix}", default).strip()
        matcher = _SYSTEMD_MEMORY_LIMIT_RE if directive.startswith("Memory") else _SYSTEMD_COUNT_LIMIT_RE
        if not matcher.fullmatch(value):
            raise ServiceManagerError(
                f"Invalid {directive} limit for {service_name}: {value!r}"
            )
        rendered.append(f"{directive}={value}")
    return "\n".join(rendered)


def get_project_root() -> Path:
    env = os.getenv("BMS_HOME")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    return Path(__file__).resolve().parent


def get_user_systemd_dir(home: Path | None = None) -> Path:
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser().resolve() / "systemd" / "user"
    base_home = Path(home).expanduser().resolve() if home else Path.home().resolve()
    return base_home / ".config" / "systemd" / "user"


def resolve_runtime_mode(runtime_mode: str | None = None) -> str:
    mode = (runtime_mode or os.getenv("BMS_RUNTIME_MODE") or DEFAULT_RUNTIME_MODE).strip().lower()
    if mode not in VALID_RUNTIME_MODES:
        raise ServiceManagerError(
            f"Unknown BioModStack runtime mode '{mode}'. Expected one of: {', '.join(sorted(VALID_RUNTIME_MODES))}"
        )
    return mode


def runtime_service_names(runtime_mode: str | None = None) -> tuple[str, ...]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return (PRODUCTION_WORKFLOW_ADAPTER_SERVICE, CORE_RUNTIME_SERVICE)
    return (DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE, API_SERVICE, FRONTEND_SERVICE)


def runtime_target_unit(runtime_mode: str | None = None) -> str:
    mode = resolve_runtime_mode(runtime_mode)
    return TARGET_UNIT if mode == CONTAINER_RUNTIME_MODE else DEV_TARGET_UNIT


def all_runtime_service_names() -> tuple[str, ...]:
    return (
        API_SERVICE,
        FRONTEND_SERVICE,
        TELEMETRY_SERVICE,
        DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE,
        PRODUCTION_WORKFLOW_ADAPTER_SERVICE,
        CORE_RUNTIME_SERVICE,
    )


def incompatible_runtime_service_names(runtime_mode: str | None = None) -> tuple[str, ...]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return ()
    return ()


def _resolved_profile_int(project_root: Path | None, key: str, default: int) -> int:
    try:
        snapshot = install_profile_snapshot(project_root=(project_root or get_project_root()).resolve())
    except Exception:
        snapshot = {}
    if isinstance(snapshot, Mapping):
        resolved = snapshot.get("resolved", {})
        if isinstance(resolved, Mapping):
            try:
                return int(resolved.get(key) or default)
            except (TypeError, ValueError):
                return default
    return default


def runtime_frontend_port(runtime_mode: str | None = None, project_root: Path | None = None) -> int:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == DEV_RUNTIME_MODE:
        return _resolved_profile_int(project_root, "dev_web_host_port", FRONTEND_PORT)
    return _resolved_profile_int(project_root, "web_host_port", STABLE_FRONTEND_PORT)


def runtime_frontend_origin(runtime_mode: str | None = None, project_root: Path | None = None) -> str:
    return f"http://127.0.0.1:{runtime_frontend_port(runtime_mode, project_root)}"


def runtime_router_basename(runtime_mode: str | None = None) -> str:
    mode = resolve_runtime_mode(runtime_mode)
    return "/bms/" if mode == CONTAINER_RUNTIME_MODE else "/"


def runtime_frontend_url(runtime_mode: str | None = None, project_root: Path | None = None) -> str:
    origin = runtime_frontend_origin(runtime_mode, project_root)
    basename = runtime_router_basename(runtime_mode)
    if basename == "/":
        return f"{origin}/"
    return f"{origin}{basename}"


def runtime_api_port(runtime_mode: str | None = None, project_root: Path | None = None) -> int:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == DEV_RUNTIME_MODE:
        return _resolved_profile_int(project_root, "dev_api_host_port", DEV_API_PORT)
    # docker/api.Dockerfile binds the stable image to the registry port; keeping this
    # immutable prevents a profile/UI URL from drifting away from the listener.
    return API_PORT


def runtime_api_url(runtime_mode: str | None = None, project_root: Path | None = None) -> str:
    return f"http://127.0.0.1:{runtime_api_port(runtime_mode, project_root)}"


def runtime_api_health_url(runtime_mode: str | None = None, project_root: Path | None = None) -> str:
    return f"{runtime_api_url(runtime_mode, project_root)}/api/health"


def active_runtime_mode(project_root: Path | None = None) -> str | None:
    root = (project_root or get_project_root()).resolve()
    active_modes = [
        mode
        for mode in (DEV_RUNTIME_MODE, CONTAINER_RUNTIME_MODE)
        if all(service["active"] for service in runtime_service_descriptors(root, mode))
    ]
    if len(active_modes) == 1:
        return active_modes[0]
    return None


def operator_runtime_mode(project_root: Path | None = None, runtime_mode: str | None = None) -> str:
    if runtime_mode:
        return resolve_runtime_mode(runtime_mode)
    detected_mode = active_runtime_mode(project_root)
    if detected_mode:
        return detected_mode
    return resolve_runtime_mode(None)


def operator_frontend_url(project_root: Path | None = None, runtime_mode: str | None = None) -> str:
    return runtime_frontend_url(operator_runtime_mode(project_root=project_root, runtime_mode=runtime_mode), project_root=project_root)


def _coerce_host_port(value: int | str | None, *, label: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ServiceManagerError(f"{label} must be an integer TCP port") from exc
    if not 1 <= port <= 65535:
        raise ServiceManagerError(f"{label} must be between 1 and 65535")
    return port


def _runtime_port_settings_from_resolved(resolved: Mapping[str, object], project_root: Path | None = None) -> dict[str, object]:
    dev_api_port = _coerce_host_port(resolved.get("dev_api_host_port"), label="dev_api_host_port") or DEV_API_PORT
    dev_port = _coerce_host_port(resolved.get("dev_web_host_port"), label="dev_web_host_port") or FRONTEND_PORT
    prod_port = _coerce_host_port(resolved.get("web_host_port"), label="prod_web_host_port") or STABLE_FRONTEND_PORT
    return {
        "dev_api_host_port": dev_api_port,
        "prod_api_host_port": runtime_api_port(CONTAINER_RUNTIME_MODE, project_root=project_root),
        "dev_web_host_port": dev_port,
        "prod_web_host_port": prod_port,
        "dev_api_url": runtime_api_url(DEV_RUNTIME_MODE, project_root=project_root),
        "prod_api_url": runtime_api_url(CONTAINER_RUNTIME_MODE, project_root=project_root),
        "dev_url": runtime_frontend_url(DEV_RUNTIME_MODE, project_root=project_root),
        "prod_url": runtime_frontend_url(CONTAINER_RUNTIME_MODE, project_root=project_root),
    }


def _assert_runtime_port_contract(resolved: Mapping[str, object]) -> None:
    try:
        validate_runtime_port_contract(resolved)
    except ValueError as exc:
        raise ServiceManagerError(str(exc)) from exc


def runtime_port_settings(project_root: Path | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    snapshot = install_profile_snapshot(project_root=root)
    resolved = snapshot.get("resolved", {}) if isinstance(snapshot, Mapping) else {}
    if not isinstance(resolved, Mapping):
        resolved = {}
    _assert_runtime_port_contract(resolved)
    return _runtime_port_settings_from_resolved(resolved, project_root=root)


def save_runtime_port_settings(
    dev_api_host_port: int | str | None = None,
    dev_web_host_port: int | str | None = None,
    prod_web_host_port: int | str | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    dev_api_port = _coerce_host_port(dev_api_host_port, label="dev_api_host_port")
    dev_port = _coerce_host_port(dev_web_host_port, label="dev_web_host_port")
    prod_port = _coerce_host_port(prod_web_host_port, label="prod_web_host_port")
    payload = dict(load_install_profile())
    if dev_api_port is not None:
        payload["dev_api_host_port"] = dev_api_port
    if dev_port is not None:
        payload["dev_web_host_port"] = dev_port
    if prod_port is not None:
        payload["web_host_port"] = prod_port
    root = (project_root or get_project_root()).resolve()
    saved_profile = save_install_profile(payload, project_root=root)
    snapshot = install_profile_snapshot(profile=saved_profile, project_root=root)
    resolved = snapshot.get("resolved", {}) if isinstance(snapshot, Mapping) else {}
    if not isinstance(resolved, Mapping):
        resolved = {}
    return _runtime_port_settings_from_resolved(resolved, project_root=root)


def runtime_log_descriptors(runtime_mode: str | None = None) -> list[dict[str, str]]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return [
            {"id": "api", "label": "API backend log", "path": "docker:biomodstack-api", "fallback_path": str(API_LOG)},
            {"id": "frontend", "label": "Frontend/web log", "path": "docker:biomodstack-web", "fallback_path": str(FRONTEND_LOG)},
            {
                "id": "workflow-adapter",
                "label": "Production workflow adapter log",
                "path": str(PRODUCTION_WORKFLOW_ADAPTER_LOG),
            },
            {"id": "core-runtime", "label": "Container runtime log", "path": str(CORE_RUNTIME_LOG)},
        ]
    return [
        {
            "id": "workflow-adapter",
            "label": "Development workflow adapter log",
            "path": str(DEVELOPMENT_WORKFLOW_ADAPTER_LOG),
        },
        {"id": "api", "label": "API backend log", "path": str(API_LOG)},
        {"id": "frontend", "label": "Frontend/web log", "path": str(FRONTEND_LOG)},
    ]


def runtime_service_descriptors(project_root: Path | None = None, runtime_mode: str | None = None) -> list[dict[str, object]]:
    root = (project_root or get_project_root()).resolve()
    return [
        {"name": service_name, "active": service_is_active(service_name, root)}
        for service_name in runtime_service_names(runtime_mode)
    ]


def _runtime_listener_specs(
    project_root: Path | None = None,
    runtime_mode: str | None = None,
) -> tuple[dict[str, object], ...]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    if mode == DEV_RUNTIME_MODE:
        return (
            {
                "id": "workflow-adapter",
                "port": DEVELOPMENT_WORKFLOW_ADAPTER_PORT,
                "owner_kind": "development-workflow-adapter",
            },
            {"id": "api", "port": runtime_api_port(mode, project_root=root), "owner_kind": "dev-api"},
            {"id": "frontend", "port": runtime_frontend_port(mode, project_root=root), "owner_kind": "dev-frontend"},
        )
    return (
        {
            "id": "workflow-adapter",
            "port": PRODUCTION_WORKFLOW_ADAPTER_PORT,
            "owner_kind": "production-workflow-adapter",
        },
        {"id": "api", "port": runtime_api_port(mode, project_root=root), "owner_kind": "api"},
        {"id": "frontend", "port": runtime_frontend_port(mode, project_root=root), "owner_kind": "frontend"},
        {"id": "cpu-power", "port": CPU_POWER_PORT, "owner_kind": "cpu-power"},
        {"id": "host-agent", "port": HOST_AGENT_PORT, "owner_kind": "host-agent"},
    )


def _listener_matches_expected_owner(
    pid: int,
    owner_kind: str,
    project_root: Path,
) -> tuple[bool, str, list[int]]:
    if owner_kind in {"api", "frontend", "cpu-power", "host-agent"}:
        if pid_is_biomodstack_runtime_container(pid, owner_kind, project_root):
            return True, f"managed-container-{owner_kind}", []
        return False, "foreign", []
    if owner_kind in {"development-workflow-adapter", "production-workflow-adapter"}:
        lane = (
            DEVELOPMENT_LANE
            if owner_kind == "development-workflow-adapter"
            else PRODUCTION_LANE
        )
        matcher = lambda cmdline, cwd, root: is_biomodstack_workflow_adapter_process(  # noqa: E731
            cmdline, cwd, root, lane=lane
        )
        chain = matching_process_chain(pid, matcher, project_root)
        return bool(chain), f"managed-{lane}-workflow-adapter" if chain else "foreign", chain
    if owner_kind == "dev-api":
        chain = matching_process_chain(pid, is_biomodstack_api_process, project_root)
        return bool(chain), "managed-dev-api" if chain else "foreign", chain
    if owner_kind == "dev-frontend":
        chain = matching_process_chain(pid, is_biomodstack_frontend_process, project_root)
        return bool(chain), "managed-dev-frontend" if chain else "foreign", chain
    raise ValueError(f"Unknown listener owner kind: {owner_kind}")


def _managed_compose_service_for_owner_kind(owner_kind: str) -> str | None:
    return {
        "api": "bms-api",
        "frontend": "bms-web",
        "cpu-power": "bms-cpu-power",
        "host-agent": "bms-host-agent",
    }.get(owner_kind)


def runtime_listener_ownership(
    component: str,
    port: int,
    owner_kind: str,
    project_root: Path | None = None,
) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    pids = listener_pids(port)
    listeners: list[dict[str, object]] = []
    ok = True
    for pid in pids:
        matches, owner, matched_chain = _listener_matches_expected_owner(pid, owner_kind, root)
        ok = ok and matches
        try:
            cmdline = read_pid_cmdline(pid)
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            cmdline = ""
        listeners.append(
            {
                "pid": pid,
                "owner": owner,
                "matched_chain": matched_chain,
                "command": cmdline or "(cmdline unavailable)",
            }
        )
    if not pids:
        managed_service = _managed_compose_service_for_owner_kind(owner_kind)
        if managed_service and docker_compose_service_is_running(managed_service, root):
            return {
                "component": component,
                "port": port,
                "checked": True,
                "ok": True,
                "status": "ok",
                "listeners": [
                    {
                        "pid": None,
                        "owner": f"managed-container-{owner_kind}",
                        "matched_chain": [],
                        "command": f"docker compose service {managed_service} (listener PID hidden)",
                    }
                ],
            }
        return {
            "component": component,
            "port": port,
            "checked": True,
            "ok": None,
            "status": "no-listener",
            "listeners": [],
        }
    return {
        "component": component,
        "port": port,
        "checked": True,
        "ok": ok,
        "status": "ok" if ok else "wrong-owner",
        "listeners": listeners,
    }


def runtime_listener_preflight(
    project_root: Path | None = None,
    runtime_mode: str | None = None,
) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    components = {
        str(spec["id"]): runtime_listener_ownership(
            str(spec["id"]), int(spec["port"]), str(spec["owner_kind"]), root
        )
        for spec in _runtime_listener_specs(root, mode)
    }
    conflicts = [component for component, result in components.items() if result.get("ok") is False]
    return {
        "runtime_mode": mode,
        "checked": True,
        "ok": not conflicts,
        "status": "ok" if not conflicts else "blocked",
        "conflicts": conflicts,
        "components": components,
    }


def production_core_listener_preflight(
    project_root: Path | None = None,
) -> dict[str, object]:
    """Validate only Production-owned container listeners.

    The workflow adapter is the independent Tailnet selector control plane and
    may be owned by canonical Development while Production is built or
    restarted. It must therefore never participate in Production core-runtime
    ownership or rollback.
    """
    result = runtime_listener_preflight(project_root, CONTAINER_RUNTIME_MODE)
    raw_components = result.get("components", {})
    components = (
        {
            component: entry
            for component, entry in raw_components.items()
            if component != "workflow-adapter"
        }
        if isinstance(raw_components, Mapping)
        else {}
    )
    conflicts = [
        component
        for component, entry in components.items()
        if isinstance(entry, Mapping) and entry.get("ok") is False
    ]
    return {
        **result,
        "ok": not conflicts,
        "status": "ok" if not conflicts else "blocked",
        "conflicts": conflicts,
        "components": components,
    }


def assert_production_core_listener_preflight(
    project_root: Path | None = None,
) -> dict[str, object]:
    result = production_core_listener_preflight(project_root)
    if not result["ok"]:
        raw_components = result.get("components", {})
        components = raw_components if isinstance(raw_components, Mapping) else {}
        details = "; ".join(
            f"{component} port {entry['port']}: {entry['listeners']}"
            for component, entry in components.items()
            if isinstance(entry, Mapping) and entry.get("ok") is False
        )
        raise ServiceManagerError(
            f"Production core launch blocked by listener ownership conflict: {details}"
        )
    return result



def assert_runtime_listener_preflight(
    project_root: Path | None = None,
    runtime_mode: str | None = None,
) -> dict[str, object]:
    result = runtime_listener_preflight(project_root=project_root, runtime_mode=runtime_mode)
    if not result["ok"]:
        details = "; ".join(
            f"{component} port {entry['port']}: {entry['listeners']}"
            for component, entry in result["components"].items()
            if entry.get("ok") is False
        )
        raise ServiceManagerError(f"Runtime launch blocked by listener ownership conflict: {details}")
    return result


def runtime_api_listener_ownership(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    api_port = runtime_api_port(mode, project_root=root)
    owner_kind = "api" if mode == CONTAINER_RUNTIME_MODE else "dev-api"
    result = runtime_listener_ownership("api", api_port, owner_kind, root)
    # Preserve the legacy owner label for callers that distinguish a stale dev
    # API from an arbitrary foreign process on the stable port.
    if mode == CONTAINER_RUNTIME_MODE:
        for listener in result["listeners"]:
            if listener["owner"] == "foreign":
                chain = matching_process_chain(int(listener["pid"]), is_biomodstack_api_process, root)
                if chain:
                    listener["owner"] = "legacy-dev-api"
                    listener["matched_chain"] = chain
    return result


def runtime_descriptor(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    services = runtime_service_descriptors(root, mode)
    telemetry_active = service_is_active(TELEMETRY_SERVICE, project_root=root)
    install_profile = install_profile_snapshot(project_root=root)
    if not isinstance(install_profile, Mapping):
        install_profile = {}
    resolved_paths = install_profile.get("resolved", {})
    if not isinstance(resolved_paths, Mapping):
        resolved_paths = {}
    listener_preflight = runtime_listener_preflight(root, mode)
    raw_listener_components = listener_preflight.get("components", {})
    listener_components: dict[str, object] = (
        dict(raw_listener_components) if isinstance(raw_listener_components, Mapping) else {}
    )
    api_ownership = runtime_api_listener_ownership(root, mode)
    listener_components["api"] = api_ownership
    listener_conflicts = [
        component
        for component, ownership in listener_components.items()
        if isinstance(ownership, Mapping) and ownership.get("ok") is False
    ]
    listener_preflight = {
        **listener_preflight,
        "ok": not listener_conflicts,
        "status": "ok" if not listener_conflicts else "blocked",
        "conflicts": listener_conflicts,
        "components": listener_components,
    }

    def component_readiness(
        component: str,
        *,
        required: bool,
        http_ready: bool | None = None,
    ) -> dict[str, object]:
        ownership = listener_components.get(component)
        ownership_record = ownership if isinstance(ownership, Mapping) else {}
        owner_ready = ownership_record.get("ok") is True
        ready = owner_ready and (http_ready is not False)
        ownership_status = str(ownership_record.get("status", "unknown"))
        if ready:
            state = "ready"
            diagnostic = None
        elif ownership_status == "wrong-owner":
            state = "wrong-owner"
            diagnostic = {"code": "wrong-owner", "summary": f"Port owner for {component} is not managed by this runtime."}
        elif ownership_status == "no-listener":
            state = "inactive"
            diagnostic = {"code": "no-listener", "summary": f"No listener is present for {component}."}
        elif http_ready is False:
            state = "unreachable"
            diagnostic = {"code": "http-unreachable", "summary": f"The {component} health endpoint did not return HTTP 200."}
        else:
            state = "unknown"
            diagnostic = {"code": "unknown", "summary": f"Readiness for {component} could not be verified."}
        log_refs = {
            "api": "docker:biomodstack-api" if mode == CONTAINER_RUNTIME_MODE else str(API_LOG),
            "frontend": "docker:biomodstack-web" if mode == CONTAINER_RUNTIME_MODE else str(FRONTEND_LOG),
            "workflow-adapter": str(
                DEVELOPMENT_WORKFLOW_ADAPTER_LOG
                if mode == DEV_RUNTIME_MODE
                else PRODUCTION_WORKFLOW_ADAPTER_LOG
            ),
            "cpu-power": str(CORE_RUNTIME_LOG),
            "host-agent": str(CORE_RUNTIME_LOG),
            "analytical-db": str(CORE_RUNTIME_LOG),
        }
        return {
            "id": component,
            "required": required,
            "ready": ready,
            "active": owner_ready,
            "http_ready": http_ready,
            "owner_verified": owner_ready,
            "owner_ready": owner_ready,
            "state": state,
            "diagnostic": diagnostic,
            "log_ref": log_refs.get(component),
            "ownership_status": ownership_status,
            "port": ownership_record.get("port"),
            "listeners": list(ownership_record.get("listeners", []))
            if isinstance(ownership_record.get("listeners", []), list)
            else [],
        }

    api_http_ready = url_is_ready(runtime_api_health_url(mode, project_root=root))
    frontend_http_ready = url_is_ready(frontend_url)
    readiness: dict[str, dict[str, object]] = {
        "api": component_readiness("api", required=True, http_ready=api_http_ready),
        "frontend": component_readiness("frontend", required=True, http_ready=frontend_http_ready),
    }
    if mode == CONTAINER_RUNTIME_MODE:
        adapter_http_ready = url_is_ready(
            workflow_adapter_health_url_for_lane(PRODUCTION_LANE)
        )
        readiness = {
            "workflow-adapter": component_readiness(
                "workflow-adapter",
                required=True,
                http_ready=adapter_http_ready,
            ),
            **readiness,
            "cpu-power": component_readiness("cpu-power", required=False),
            "host-agent": component_readiness("host-agent", required=False),
            "analytical-db": component_readiness("analytical-db", required=False),
        }
    health = {
        "api_ready": readiness["api"]["ready"],
        "frontend_ready": readiness["frontend"]["ready"],
    }
    if mode == DEV_RUNTIME_MODE:
        adapter_http_ready = url_is_ready(
            workflow_adapter_health_url_for_lane(DEVELOPMENT_LANE)
        )
        readiness = {
            "workflow-adapter": component_readiness(
                "workflow-adapter",
                required=True,
                http_ready=adapter_http_ready,
            ),
            **readiness,
        }
        health = {
            "adapter_ready": readiness["workflow-adapter"]["ready"],
            **health,
        }
    if mode == CONTAINER_RUNTIME_MODE:
        health = {
            "adapter_ready": readiness["workflow-adapter"]["ready"],
            **health,
        }
    runtime_ready = telemetry_active and all(
        bool(component["ready"])
        for component in readiness.values()
        if component["required"]
    )
    return {
        "runtime_mode": mode,
        "runtime_active": telemetry_active and all(bool(service["active"]) for service in services),
        "telemetry_active": telemetry_active,
        "runtime_ready": runtime_ready,
        "runtime_manager": "systemd-user",
        "api_url": runtime_api_url(mode, project_root=root),
        "frontend_origin": runtime_frontend_origin(mode, project_root=root),
        "frontend_url": frontend_url,
        "browser_url": frontend_url,
        "router_basename": runtime_router_basename(mode),
        "supported_launch_surfaces": list(SUPPORTED_LAUNCH_SURFACES),
        "launch_preferences": load_launch_preferences(),
        "health": health,
        "components": readiness,
        "component_readiness": readiness,
        "runtime_ownership": dict(listener_components),
        "listener_preflight": listener_preflight,
        "services": services,
        "logs": runtime_log_descriptors(mode),
        "install_profile": dict(install_profile),
        "paths": dict(resolved_paths),
        "electron_shell_available": bool(electron_shell_available(project_root=root)),
        "capabilities": {
            "open_in_browser": True,
            "restart_all": True,
            "start_api": True,
            "stop_api": True,
            "restart_api": True,
            "stop_all": True,
            "runtime_api": True,
            "install_profile_api": True,
        },
    }


def run_core_runtime_script(
    *args: str,
    check: bool = True,
    capture_output: bool = True,
    project_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    root = (project_root or get_project_root()).resolve()
    env = os.environ.copy()
    env.setdefault("BMS_HOME", str(root))
    env.setdefault("BMS_RUNTIME_MODE", CONTAINER_RUNTIME_MODE)
    script = root / "scripts" / "run_biomodstack_core_runtime.sh"
    return subprocess.run(
        [str(script), *args],
        check=check,
        capture_output=capture_output,
        text=True,
        env=env,
    )


def url_is_ready(url: str, timeout_seconds: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def git_build_identity(project_root: Path) -> dict[str, str]:
    """Return immutable build metadata for the exact Git checkout."""
    root = project_root.resolve()

    def git_value(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
        return completed.stdout.strip()

    revision = git_value("rev-parse", "HEAD").lower()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return {"revision": "unknown", "build_id": "development", "build_time": "unknown"}

    branch = git_value("symbolic-ref", "--quiet", "--short", "HEAD") or "detached"
    branch = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "detached"
    raw_build_time = git_value("show", "-s", "--format=%cI", "HEAD")
    try:
        parsed_build_time = datetime.fromisoformat(raw_build_time.replace("Z", "+00:00"))
        if parsed_build_time.tzinfo is None:
            raise ValueError("Git commit time must include a UTC offset")
        build_time = (
            parsed_build_time.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except ValueError:
        build_time = "unknown"
    return {
        "revision": revision,
        "build_id": f"{branch}-{revision[:12]}",
        "build_time": build_time,
    }


def render_workflow_parent_slice() -> str:
    return dedent(
        """\
        [Unit]
        Description=BioModStack managed service parent slice

        [Slice]
        CPUAccounting=true
        CPUWeight=100
        MemoryAccounting=true
        TasksAccounting=true
        """
    )


def render_workflow_root_slice() -> str:
    return dedent(
        """\
        [Unit]
        Description=BioModStack aggregate workflow resource envelope

        [Slice]
        CPUAccounting=yes
        CPUQuota=2400%
        MemoryAccounting=yes
        MemoryMax=96G
        TasksAccounting=yes
        """
    )


def render_workflow_slice(lane: str) -> str:
    normalized_lane = _normalize_execution_lane(lane)
    return dedent(
        f"""\
        [Unit]
        Description=BioModStack {normalized_lane} workflow jobs

        [Slice]
        CPUAccounting=yes
        MemoryAccounting=yes
        TasksAccounting=yes
        """
    )


def render_user_units(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    snapshot = install_profile_snapshot(project_root=root)
    resolved = snapshot.get("resolved", {}) if isinstance(snapshot, Mapping) else {}
    if not isinstance(resolved, Mapping):
        resolved = {}
    _assert_runtime_port_contract(resolved)
    build_identity = git_build_identity(root)
    build_revision = build_identity["revision"]
    build_id = build_identity["build_id"]
    build_time = build_identity["build_time"]
    log_rotator = root / "scripts" / "rotate_biomodstack_logs.py"
    shared_data_root = Path(str(resolved.get("data_root", Path("/mnt/BioModStack")))).expanduser().resolve()
    expected_telemetry_db = (shared_data_root / "telemetry" / "telemetry.sqlite3").resolve()
    resolved_jobs_db = Path(str(resolved.get("db_path", shared_data_root / "biomodstack.db"))).expanduser().resolve()
    configured_telemetry_db = os.getenv("BMS_TELEMETRY_DB_PATH")
    telemetry_db_path = Path(configured_telemetry_db).expanduser().resolve() if configured_telemetry_db else expected_telemetry_db
    if telemetry_db_path == resolved_jobs_db:
        raise ServiceManagerError("Telemetry database must be separate from the resolved jobs database")
    if mode == CONTAINER_RUNTIME_MODE and telemetry_db_path != expected_telemetry_db:
        raise ServiceManagerError("Production telemetry database must remain under the resolved data root")
    telemetry_db = str(telemetry_db_path)
    telemetry_limits = render_systemd_resource_boundaries(TELEMETRY_SERVICE).replace("\n", "\n        ")
    telemetry_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack host telemetry collector
        After=local-fs.target

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_TELEMETRY_DB_PATH={telemetry_db}
        Environment=PYTHONUNBUFFERED=1
        WorkingDirectory={root / 'platform' / 'api'}
        ExecStartPre=/usr/bin/env python3 {log_rotator}
        ExecStartPre=/usr/bin/mkdir -p {Path(telemetry_db).parent}
        ExecStart={root / 'platform' / 'api' / '.venv' / 'bin' / 'python'} -m tools.telemetry_collector
        Restart=on-failure
        RestartSec=5
        TimeoutStopSec=15
        KillMode=control-group
        {telemetry_limits}
        StandardOutput=append:{TELEMETRY_LOG}
        StandardError=append:{TELEMETRY_LOG}

        [Install]
        WantedBy=default.target
        """
    )
    if mode == CONTAINER_RUNTIME_MODE:
        adapter_runner = root / "scripts" / "run_biomodstack_workflow_adapter.sh"
        core_runner = root / "scripts" / "run_biomodstack_core_runtime.sh"
        production_state_root = str(resolved.get("data_root", Path("/mnt/BioModStack")))
        production_inputs_dir = str(resolved.get("inputs_dir", Path(production_state_root) / "inputs"))
        production_db_path = str(resolved.get("db_path", Path(production_state_root) / "biomodstack.db"))
        production_work_dir = str(resolved.get("work_dir", Path(production_state_root) / "work"))
        production_results_root = str(
            resolved.get("results_dir", Path(production_state_root) / "bms_results")
        )
        production_container_dir = str(
            resolved.get("container_dir", Path(production_state_root) / "apptainer")
        )
        adapter_limits = render_systemd_resource_boundaries(PRODUCTION_WORKFLOW_ADAPTER_SERVICE).replace(
            "\n", "\n            "
        )
        core_limits = render_systemd_resource_boundaries(CORE_RUNTIME_SERVICE).replace(
            "\n", "\n            "
        )
        workflow_adapter_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack Production workflow adapter
            PartOf={TARGET_UNIT}
            After=network-online.target
            Wants=network-online.target
            StartLimitIntervalSec=300
            StartLimitBurst=3

            [Service]
            Type=simple
            Environment=BMS_HOME={root}
            Environment=BMS_RUNTIME_MODE={CONTAINER_RUNTIME_MODE}
            Environment=BMS_WORKFLOW_ADAPTER_LANE={PRODUCTION_LANE}
            Environment=BMS_REQUIRE_TRANSIENT_WORKFLOW_UNITS=1
            Environment=BMS_STATE_DIR={production_state_root}
            Environment=BMS_DATA={production_state_root}
            Environment=BMS_INPUTS={production_inputs_dir}
            Environment=BMS_DB_PATH={production_db_path}
            Environment=BMS_WORK={production_work_dir}
            Environment=BMS_RESULTS_DIR={production_results_root}
            Environment=BMS_RESULTS_ROOT={production_results_root}
            Environment=BMS_CONTAINER_DIR={production_container_dir}
            Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1
            Environment=BMS_WORKFLOW_ADAPTER_PORT={PRODUCTION_WORKFLOW_ADAPTER_PORT}
            Environment=BMS_BUILD_SHA={build_revision}
            Environment=BMS_BUILD_ID={build_id}
            Environment=BMS_BUILD_TIME={build_time}
            ExecStartPre=/usr/bin/env python3 {log_rotator}
            ExecStart={adapter_runner}
            Restart=on-failure
            RestartSec=10
            TimeoutStopSec=20
            KillMode=control-group
            {adapter_limits}
            StandardOutput=append:{PRODUCTION_WORKFLOW_ADAPTER_LOG}
            StandardError=append:{PRODUCTION_WORKFLOW_ADAPTER_LOG}

            [Install]
            WantedBy={TARGET_UNIT}
            """
        )
        workflow_root_slice = render_workflow_root_slice()
        production_workflow_slice = render_workflow_slice(PRODUCTION_LANE)
        core_runtime_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack core runtime container stack
            PartOf={TARGET_UNIT}
            After=network-online.target docker.service {WORKFLOW_ADAPTER_SERVICE}
            Wants=network-online.target docker.service {WORKFLOW_ADAPTER_SERVICE}
            StartLimitIntervalSec=300
            StartLimitBurst=3

            [Service]
            Type=simple
            Environment=BMS_HOME={root}
            Environment=BMS_RUNTIME_MODE={CONTAINER_RUNTIME_MODE}
            Environment=BMS_WORKFLOW_ADAPTER_LANE={PRODUCTION_LANE}
            Environment=BMS_REQUIRE_TRANSIENT_WORKFLOW_UNITS=1
            Environment=BMS_WORKFLOW_ADAPTER_URL={workflow_adapter_url_for_lane(PRODUCTION_LANE)}
            Environment=BMS_STATE_DIR={production_state_root}
            Environment=BMS_DB_PATH={production_db_path}
            Environment=BMS_TELEMETRY_DB_PATH=/var/lib/biomodstack/telemetry/telemetry.sqlite3
            Environment=BMS_WORK={production_work_dir}
            Environment=BMS_RESULTS_DIR={production_results_root}
            Environment=BMS_RESULTS_ROOT={production_results_root}
            Environment=BMS_CONTAINER_DIR={production_container_dir}
            ExecStartPre={core_runner} preflight
            ExecStart={core_runner} supervise
            ExecStop={core_runner} down
            Restart=no
            TimeoutStartSec=180
            TimeoutStopSec=60
            KillMode=control-group
            {core_limits}
            StandardOutput=append:{CORE_RUNTIME_LOG}
            StandardError=append:{CORE_RUNTIME_LOG}

            [Install]
            WantedBy={TARGET_UNIT}
            """
        )

        target_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack workstation runtime target
            Wants={TELEMETRY_SERVICE} {PRODUCTION_WORKFLOW_ADAPTER_SERVICE} {WORKFLOW_ROOT_SLICE} {PRODUCTION_WORKFLOW_SLICE} {CORE_RUNTIME_SERVICE}

            [Install]
            WantedBy=default.target
            """
        )

        return {
            PRODUCTION_WORKFLOW_ADAPTER_SERVICE: workflow_adapter_unit,
            TELEMETRY_SERVICE: telemetry_unit,
            WORKFLOW_PARENT_SLICE: render_workflow_parent_slice(),
            WORKFLOW_ROOT_SLICE: workflow_root_slice,
            PRODUCTION_WORKFLOW_SLICE: production_workflow_slice,
            CORE_RUNTIME_SERVICE: core_runtime_unit,
            TARGET_UNIT: target_unit,
        }

    api_runner = root / "scripts" / "run_biomodstack_api.sh"
    frontend_runner = root / "scripts" / "run_biomodstack_frontend.sh"
    tailnet_global_installer = root / "scripts" / "install_tailnet_global_routes.py"
    dev_api_host_port = runtime_api_port(DEV_RUNTIME_MODE, project_root=root)
    dev_web_host_port = runtime_frontend_port(DEV_RUNTIME_MODE, project_root=root)
    dev_data_root = str(resolved.get("dev_data_root", Path.home() / ".biomodstack-dev"))
    dev_inputs_dir = str(resolved.get("dev_inputs_dir", Path(dev_data_root) / "inputs"))
    dev_db_path = str(resolved.get("dev_db_path", Path(dev_data_root) / "biomodstack.db"))
    dev_work_dir = str(resolved.get("dev_work_dir", Path(dev_data_root) / "work"))
    dev_results_root = str(resolved.get("dev_results_dir", Path(dev_data_root) / "bms_results"))
    # Model weights are immutable shared runtime assets, not lane-owned job
    # state.  Native Development keeps its DB/work/results isolated while
    # reusing the profile's canonical weights root (as container mode does).
    dev_weights_root = str(resolved.get("weights_root", Path("/mnt/BioModStack") / "weights"))
    # ColabFold's reference database is an immutable shared model asset.  Keep
    # mutable MSA cache state lane-local, but do not require a duplicate DB.
    dev_colabfold_db = str(resolved.get("colabfold_db", Path("/mnt/BioModStack") / "colabfold_db"))
    dev_msa_cache_dir = str(resolved.get("dev_msa_cache_dir", Path(dev_data_root) / "msa_cache"))
    dev_sabdab_cache_dir = str(resolved.get("dev_sabdab_cache_dir", Path(dev_data_root) / "sabdab_cache"))
    dev_container_dir = str(
        resolved.get("dev_container_dir", shared_data_root / "apptainer")
    )
    dev_confornets_container = str(
        os.environ.get("BMS_DEV_CM_CONFORNETS_CONTAINER_PATH")
        or resolved.get(
            "dev_cm_confornets_container_path",
            shared_data_root / "dev" / "apptainer" / "confornets-canonical.sif",
        )
    )
    dev_ngs_runtime_sif = str(
        os.environ.get("BMS_DEV_NGS_RUNTIME_SIF")
        or resolved.get(
            "dev_ngs_runtime_sif",
            shared_data_root / "dev" / "apptainer" / "dorado-v1.3.1-samtools-v1.24.sif",
        )
    )
    ont_container_runtime = os.environ.get("BMS_ONT_CONTAINER_RUNTIME", "docker").strip() or "docker"
    ont_runtime_image = os.environ.get("BMS_ONT_SLOW5TOOLS_IMAGE", "").strip()
    ont_runtime_digest = os.environ.get("BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST", "").strip()
    ont_staging_root = os.environ.get(
        "BMS_ONT_RAW_SIGNAL_STAGING_ROOT",
        str(shared_data_root / "ont-raw-signal-staging"),
    ).strip()
    ont_acquisition_pressure = os.environ.get("BMS_ONT_RAW_SIGNAL_ACQUISITION_PRESSURE", "unknown").strip() or "unknown"
    ont_conversion_qualified = os.environ.get("BMS_ONT_BLOW5_CONVERSION_QUALIFIED", "0").strip() or "0"
    dev_adapter_limits = render_systemd_resource_boundaries(DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE).replace(
        "\n", "\n            "
    )
    api_limits = render_systemd_resource_boundaries(API_SERVICE).replace("\n", "\n        ")
    frontend_limits = render_systemd_resource_boundaries(FRONTEND_SERVICE).replace("\n", "\n        ")

    tailnet_global_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack global Tailnet launch-surface policy
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=oneshot
        Environment=BMS_HOME={root}
        ExecStart=/usr/bin/env python3 {tailnet_global_installer}
        # Tailnet is installed only after an explicit Development or Production
        # selection has proved that environment live. It is never a prerequisite
        # for starting either runtime.
        # The installer may legitimately wait up to 90 seconds for a restarted
        # workflow adapter to prove its policy identity.  Keep systemd's bound
        # above that inner convergence bound so it cannot terminate a valid
        # installation mid-transaction.
        TimeoutStartSec=120
        """
    )

    development_workflow_adapter_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack Development workflow adapter
        PartOf={DEV_TARGET_UNIT}
        After=network-online.target
        Wants=network-online.target
        StartLimitIntervalSec=300
        StartLimitBurst=3

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_WORKFLOW_ADAPTER_LANE={DEVELOPMENT_LANE}
        Environment=BMS_REQUIRE_TRANSIENT_WORKFLOW_UNITS=1
        Environment=BMS_STATE_DIR={dev_data_root}
        Environment=BMS_DATA={dev_data_root}
        Environment=BMS_INPUTS={dev_inputs_dir}
        Environment=BMS_DB_PATH={dev_db_path}
        Environment=BMS_WORK={dev_work_dir}
        Environment=BMS_RESULTS_DIR={dev_results_root}
        Environment=BMS_RESULTS_ROOT={dev_results_root}
        Environment=BMS_CONTAINER_DIR={dev_container_dir}
        Environment=BMS_CM_CONFORNETS_CONTAINER_PATH={dev_confornets_container}
        Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1
        Environment=BMS_WORKFLOW_ADAPTER_PORT={DEVELOPMENT_WORKFLOW_ADAPTER_PORT}
        Environment=BMS_BUILD_SHA={build_revision}
        Environment=BMS_BUILD_ID={build_id}
        Environment=BMS_BUILD_TIME={build_time}
        ExecStartPre=/usr/bin/mkdir -p {dev_data_root} {dev_inputs_dir} {dev_work_dir} {dev_results_root} {dev_container_dir}
        ExecStartPre=/usr/bin/env python3 {log_rotator}
        ExecStart={root / 'scripts' / 'run_biomodstack_workflow_adapter.sh'}
        Restart=on-failure
        RestartSec=10
        TimeoutStopSec=20
        KillMode=control-group
        {dev_adapter_limits}
        StandardOutput=append:{DEVELOPMENT_WORKFLOW_ADAPTER_LOG}
        StandardError=append:{DEVELOPMENT_WORKFLOW_ADAPTER_LOG}

        [Install]
        WantedBy={DEV_TARGET_UNIT}
        """
    )
    workflow_root_slice = render_workflow_root_slice()
    development_workflow_slice = render_workflow_slice(DEVELOPMENT_LANE)

    api_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack API service
        PartOf={DEV_TARGET_UNIT}
        After=network-online.target {DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE}
        Wants=network-online.target {DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE}
        StartLimitIntervalSec=300
        StartLimitBurst=3

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_WORKFLOW_ADAPTER_LANE={DEVELOPMENT_LANE}
        Environment=BMS_REQUIRE_TRANSIENT_WORKFLOW_UNITS=1
        Environment=BMS_WORKFLOW_ADAPTER_URL={workflow_adapter_url_for_lane(DEVELOPMENT_LANE)}
        Environment=BMS_FRONTEND_HEALTH_URL=http://127.0.0.1:{dev_web_host_port}/
        Environment=BMS_API_MODE=dev
        Environment=BMS_API_RELOAD=0
        Environment=BMS_API_BIND_PORT={dev_api_host_port}
        Environment=BMS_DATA={dev_data_root}
        Environment=BMS_STATE_DIR={dev_data_root}
        Environment=BMS_INPUTS={dev_inputs_dir}
        Environment=BMS_DB_PATH={dev_db_path}
        Environment=BMS_TELEMETRY_DB_PATH={telemetry_db}
        Environment=BMS_WORK={dev_work_dir}
        Environment=BMS_RESULTS_DIR={dev_results_root}
        Environment=BMS_RESULTS_ROOT={dev_results_root}
        Environment=BMS_CONTAINER_DIR={dev_container_dir}
        Environment=BMS_CM_CONFORNETS_CONTAINER_PATH={dev_confornets_container}
        Environment=BMS_NGS_RUNTIME_SIF={dev_ngs_runtime_sif}
        Environment=BMS_WEIGHTS={dev_weights_root}
        Environment=BMS_COLABFOLD_DB={dev_colabfold_db}
        Environment=BMS_MSA_CACHE={dev_msa_cache_dir}
        Environment=BMS_SABDAB_CACHE={dev_sabdab_cache_dir}
        Environment=BMS_CPU_POWER_STRICT=0
        Environment=BMS_ONT_CONTAINER_RUNTIME={ont_container_runtime}
        Environment=BMS_ONT_SLOW5TOOLS_IMAGE={ont_runtime_image}
        Environment=BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST={ont_runtime_digest}
        Environment=BMS_ONT_RAW_SIGNAL_STAGING_ROOT={ont_staging_root}
        Environment=BMS_ONT_RAW_SIGNAL_ACQUISITION_PRESSURE={ont_acquisition_pressure}
        Environment=BMS_ONT_BLOW5_CONVERSION_QUALIFIED={ont_conversion_qualified}
        Environment=BMS_BUILD_SHA={build_revision}
        Environment=BMS_BUILD_ID={build_id}
        Environment=BMS_BUILD_TIME={build_time}
        Environment=PYTHONUNBUFFERED=1
        ExecStartPre=/usr/bin/mkdir -p {dev_data_root} {dev_inputs_dir} {dev_work_dir} {dev_results_root} {dev_weights_root} {dev_msa_cache_dir} {dev_sabdab_cache_dir} {dev_container_dir} {ont_staging_root}
        ExecStartPre=/usr/bin/env python3 {log_rotator}
        ExecStart={api_runner}
        Restart=on-failure
        RestartSec=10
        TimeoutStopSec=20
        KillMode=process
        {api_limits}
        StandardOutput=append:{API_LOG}
        StandardError=append:{API_LOG}

        [Install]
        WantedBy={DEV_TARGET_UNIT}
        """
    )

    frontend_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack frontend dev service
        PartOf={DEV_TARGET_UNIT}
        After=network-online.target
        Wants=network-online.target
        StartLimitIntervalSec=300
        StartLimitBurst=3

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_FRONTEND_MODE=dev
        Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:{dev_api_host_port}
        Environment=BMS_DEV_WEB_HOST_PORT={dev_web_host_port}
        Environment=VITE_BMS_BUILD_SHA={build_revision}
        Environment=VITE_BMS_BUILD_ID={build_id}
        Environment=VITE_BMS_BUILD_TIME={build_time}
        ExecStartPre=/usr/bin/env python3 {log_rotator}
        ExecStart={frontend_runner}
        Restart=on-failure
        RestartSec=10
        TimeoutStopSec=20
        KillMode=control-group
        {frontend_limits}
        StandardOutput=append:{FRONTEND_LOG}
        StandardError=append:{FRONTEND_LOG}

        [Install]
        WantedBy={DEV_TARGET_UNIT}
        """
    )

    target_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack development UI target
        Wants={TELEMETRY_SERVICE} {DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE} {WORKFLOW_ROOT_SLICE} {DEVELOPMENT_WORKFLOW_SLICE} {API_SERVICE} {FRONTEND_SERVICE}

        [Install]
        WantedBy=default.target
        """
    )

    return {
        DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE: development_workflow_adapter_unit,
        TELEMETRY_SERVICE: telemetry_unit,
        WORKFLOW_PARENT_SLICE: render_workflow_parent_slice(),
        WORKFLOW_ROOT_SLICE: workflow_root_slice,
        DEVELOPMENT_WORKFLOW_SLICE: development_workflow_slice,
        API_SERVICE: api_unit,
        FRONTEND_SERVICE: frontend_unit,
        TAILNET_GLOBAL_SERVICE: tailnet_global_unit,
        DEV_TARGET_UNIT: target_unit,
    }


def install_user_units(
    project_root: Path | None = None,
    systemd_dir: Path | None = None,
    runtime_mode: str | None = None,
) -> list[Path]:
    root = (project_root or get_project_root()).resolve()
    target_dir = (systemd_dir or get_user_systemd_dir()).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    written_paths: list[Path] = []
    for unit_name, content in render_user_units(root, runtime_mode=runtime_mode).items():
        path = target_dir / unit_name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        written_paths.append(path)
    return written_paths


def run_systemctl(
    *args: str,
    check: bool = True,
    capture_output: bool = True,
    project_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("BMS_HOME", str((project_root or get_project_root()).resolve()))
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        capture_output=capture_output,
        text=True,
        env=env,
    )


def daemon_reload(project_root: Path | None = None) -> None:
    run_systemctl("daemon-reload", project_root=project_root)


def ensure_user_units(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_runtime_logs()
    API_LOG.touch(exist_ok=True)
    FRONTEND_LOG.touch(exist_ok=True)
    WORKFLOW_ADAPTER_LOG.touch(exist_ok=True)
    DEVELOPMENT_WORKFLOW_ADAPTER_LOG.touch(exist_ok=True)
    PRODUCTION_WORKFLOW_ADAPTER_LOG.touch(exist_ok=True)
    CORE_RUNTIME_LOG.touch(exist_ok=True)
    install_user_units(project_root=project_root, runtime_mode=runtime_mode)
    daemon_reload(project_root=project_root)


def ensure_target_enabled(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    run_systemctl("enable", runtime_target_unit(runtime_mode), project_root=project_root)


def service_is_active(service_name: str, project_root: Path | None = None) -> bool:
    try:
        result = run_systemctl("is-active", service_name, check=False, project_root=project_root)
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "active"


def read_pid_cmdline(pid: int) -> str:
    return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def read_pid_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def read_pid_cgroup(pid: int) -> str:
    return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")


def is_biomodstack_api_process(cmdline: str, cwd: str | None, project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    api_dir = str(root / "platform" / "api")
    cmd = cmdline.strip()
    if "uvicorn" not in cmd or "main:app" not in cmd:
        return False
    dev_port = runtime_api_port(DEV_RUNTIME_MODE, project_root=root)
    prod_port = runtime_api_port(CONTAINER_RUNTIME_MODE, project_root=root)
    port_markers = (
        f"--port {dev_port}",
        f"--port={dev_port}",
        f"--port {prod_port}",
        f"--port={prod_port}",
    )
    if not any(marker in cmd for marker in port_markers):
        return False
    if api_dir in cmd:
        return True
    return cwd == api_dir


def is_biomodstack_frontend_process(cmdline: str, cwd: str | None, project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    frontend_dir = str(root / "platform" / "frontend")
    cmd = cmdline.strip()
    frontend_port = runtime_frontend_port(DEV_RUNTIME_MODE, project_root=root)
    if f"--port {frontend_port}" not in cmd and f"--port={frontend_port}" not in cmd:
        return False
    if any(token in cmd for token in ("vite", "npm run dev", "vite.js")) and frontend_dir in cmd:
        return True
    return cwd == frontend_dir and any(token in cmd for token in ("vite", "npm run dev", "node "))


def is_biomodstack_workflow_adapter_process(
    cmdline: str,
    cwd: str | None,
    project_root: Path | None = None,
    *,
    lane: str | None = None,
) -> bool:
    root = (project_root or get_project_root()).resolve()
    api_dir = str(root / "platform" / "api")
    cmd = cmdline.strip()
    expected_port = (
        workflow_adapter_port_for_lane(lane)
        if lane is not None
        else WORKFLOW_ADAPTER_PORT
    )
    port_markers = (f"--port {expected_port}", f"--port={expected_port}")
    if "workflow_adapter_app:app" not in cmd or not any(marker in cmd for marker in port_markers):
        return False
    return api_dir in cmd or cwd == api_dir


def _docker_container_id_for_pid(pid: int) -> str | None:
    try:
        cgroup = read_pid_cgroup(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    for pattern in (
        r"/docker/([0-9a-f]{12,64})(?:/|$)",
        r"docker-([0-9a-f]{12,64})\.scope",
    ):
        match = re.search(pattern, cgroup)
        if match:
            return match.group(1)
    return None


def _docker_container_labels(container_id: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id, "--format", "{{json .Config.Labels}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return {}
    if result.returncode != 0:
        return {}
    payload = result.stdout.strip()
    if not payload or payload == "null":
        return {}
    try:
        labels = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def current_process_is_core_runtime_api() -> bool:
    return _truthy_env(os.getenv("BMS_CORE_RUNTIME_MODE")) or os.getenv("BMS_RUNTIME_MODE") == CONTAINER_RUNTIME_MODE


def pid_is_current_core_runtime_api(pid: int) -> bool:
    return pid == os.getpid() and current_process_is_core_runtime_api()


def pid_is_biomodstack_runtime_container(pid: int, kind: str, project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    expected_service = {
        "api": "bms-api",
        "frontend": "bms-web",
        "cpu-power": "bms-cpu-power",
        "host-agent": "bms-host-agent",
    }.get(kind)
    if expected_service is None:
        raise ValueError(f"Unknown listener kind: {kind}")

    if kind == "api" and pid_is_current_core_runtime_api(pid):
        # Inside the host-networked API container, PID/cgroup namespace visibility
        # may not expose the Docker container id. The process environment is the
        # authoritative ownership marker for the currently serving API.
        return True

    container_id = _docker_container_id_for_pid(pid)
    if not container_id:
        return False
    labels = _docker_container_labels(container_id)
    config_files = {
        entry.strip()
        for entry in labels.get("com.docker.compose.project.config_files", "").split(",")
        if entry.strip()
    }
    return (
        labels.get("com.docker.compose.service") == expected_service
        and labels.get("com.docker.compose.project.working_dir") == str(root)
        and str(root / "compose.core-runtime.yml") in config_files
    )


def docker_service_publishes_port(service: str, port: int, project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.ID}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    for container_id in result.stdout.splitlines():
        labels = _docker_container_labels(container_id.strip())
        if (
            labels.get("com.docker.compose.service") == service
            and labels.get("com.docker.compose.project.working_dir") == str(root)
        ):
            return True
    return False


def docker_compose_service_is_running(service: str, project_root: Path | None = None) -> bool:
    """Verify a running Compose service when an unprivileged listener scan sees no PID."""
    root = (project_root or get_project_root()).resolve()
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    expected_config = str(root / "compose.core-runtime.yml")
    for container_id in result.stdout.splitlines():
        labels = _docker_container_labels(container_id.strip())
        config_files = {
            item.strip()
            for item in labels.get("com.docker.compose.project.config_files", "").split(",")
            if item.strip()
        }
        if (
            labels.get("com.docker.compose.service") == service
            and labels.get("com.docker.compose.project.working_dir") == str(root)
            and expected_config in config_files
        ):
            return True
    return False


def _parse_pid_tokens(text: str) -> list[int]:
    seen: set[int] = set()
    parsed: list[int] = []
    for token in re.findall(r"\b\d+\b", text):
        pid = int(token)
        if pid in seen:
            continue
        seen.add(pid)
        parsed.append(pid)
    return parsed


def listener_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode in (0, 1):
            return _parse_pid_tokens(result.stdout)
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["fuser", "-n", "tcp", str(port)],
            check=False,
            capture_output=True,
            text=True,
        )
        combined_output = f"{result.stdout}\n{result.stderr}".replace(f"{port}/tcp:", " ")
        pids = _parse_pid_tokens(combined_output)
        if pids or result.returncode in (0, 1):
            return pids
    except FileNotFoundError:
        pass

    pids = _listener_pids_from_proc(port)
    if pids:
        return pids

    return []


def _listener_pids_from_proc(port: int) -> list[int]:
    listen_inodes: set[str] = set()
    for proc_net in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = proc_net.read_text(encoding="utf-8").splitlines()[1:]
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_address, state, inode = parts[1], parts[3], parts[9]
            try:
                local_port = int(local_address.rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and state == "0A":
                listen_inodes.add(inode)
    if not listen_inodes:
        return []

    pids: list[int] = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            if target.startswith("socket:[") and target[8:-1] in listen_inodes:
                pids.append(int(pid_dir.name))
                break
    return sorted(set(pids))


def read_pid_ppid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, OSError):
        return None
    return None


def matching_process_chain(
    pid: int,
    matcher,
    project_root: Path | None = None,
) -> list[int]:
    root = (project_root or get_project_root()).resolve()
    matches: list[int] = []
    seen: set[int] = set()
    current: int | None = pid
    while current and current > 1 and current not in seen:
        seen.add(current)
        try:
            cmdline = read_pid_cmdline(current)
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            cmdline = ""
        cwd = read_pid_cwd(current)
        if matcher(cmdline, cwd, root):
            matches.append(current)
        current = read_pid_ppid(current)
    return matches


def _terminate_pid(pid: int, grace_seconds: float = 8.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def cleanup_legacy_listener(
    kind: str,
    project_root: Path | None = None,
    runtime_mode: str | None = None,
) -> None:
    root = (project_root or get_project_root()).resolve()
    if kind == "api":
        mode = resolve_runtime_mode(runtime_mode or DEV_RUNTIME_MODE)
        port = runtime_api_port(mode, project_root=root)
        matcher = is_biomodstack_api_process
    elif kind == "frontend":
        port = runtime_frontend_port(DEV_RUNTIME_MODE, project_root=root)
        matcher = is_biomodstack_frontend_process
    else:
        raise ValueError(f"Unknown listener kind: {kind}")

    kill_targets: list[int] = []
    seen_targets: set[int] = set()
    for pid in listener_pids(port):
        if pid_is_biomodstack_runtime_container(pid, kind, root):
            # A host-networked core-runtime container can show up as a plain
            # uvicorn/node listener on the host port. It is BioModStack-owned,
            # not a foreign process, and legacy cleanup must not surface a
            # misleading "non-BioModStack process" error for it.
            continue
        chain = matching_process_chain(pid, matcher, root)
        if not chain:
            try:
                cmdline = read_pid_cmdline(pid)
            except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
                cmdline = ""
            raise ServiceManagerError(
                f"Port {port} is occupied by a non-BioModStack process (pid {pid}): {cmdline or '(cmdline unavailable)'}"
            )
        for target_pid in reversed(chain):
            if target_pid in seen_targets:
                continue
            seen_targets.add(target_pid)
            kill_targets.append(target_pid)

    for pid in kill_targets:
        _terminate_pid(pid)


def wait_for_http(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.5)
            continue
    raise ServiceManagerError(f"Timed out waiting for {url}: {last_error}")


def should_cleanup_legacy_listeners_before_start(
    runtime_mode: str | None = None,
    project_root: Path | None = None,
) -> bool:
    mode = resolve_runtime_mode(runtime_mode)
    if all(service_is_active(name, project_root=project_root) for name in runtime_service_names(mode)):
        return False
    if mode != CONTAINER_RUNTIME_MODE:
        return True

    root = (project_root or get_project_root()).resolve()
    runtime_listener_found = False
    for kind, port in (("api", API_PORT),):
        pids = listener_pids(port)
        if not pids:
            continue
        runtime_listener_found = True
        if not all(pid_is_biomodstack_runtime_container(pid, kind, root) for pid in pids):
            return True
    return not runtime_listener_found


def runtime_http_wait_timeout_seconds(runtime_mode: str | None = None) -> float:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS
    return DEFAULT_HTTP_WAIT_TIMEOUT_SECONDS


@serialized_lifecycle_operation
def start_all(
    project_root: Path | None = None,
    runtime_mode: str | None = None,
    *,
    skip_api_wait: bool = False,
    skip_workflow_adapter_wait: bool = False,
) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    snapshot = install_profile_snapshot(project_root=root)
    resolved = snapshot.get("resolved", {}) if isinstance(snapshot, Mapping) else {}
    if not isinstance(resolved, Mapping):
        resolved = {}
    _assert_runtime_port_contract(resolved)
    assert_runtime_listener_preflight(root, mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    wait_timeout_seconds = runtime_http_wait_timeout_seconds(mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == DEV_RUNTIME_MODE:
        services_to_start: list[str] = []
        if not service_is_active(TELEMETRY_SERVICE, project_root=root):
            services_to_start.append(TELEMETRY_SERVICE)
        if not service_is_active(API_SERVICE, project_root=root) and not url_is_ready(runtime_api_health_url(mode, project_root=root)):
            services_to_start.append(API_SERVICE)
        if not service_is_active(FRONTEND_SERVICE, project_root=root):
            services_to_start.append(FRONTEND_SERVICE)
        if services_to_start:
            run_systemctl("start", *services_to_start, DEV_TARGET_UNIT, project_root=root)
        if not skip_api_wait:
            wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
        wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)
        return

    ensure_target_enabled(root, runtime_mode=mode)
    runtime_services_active = service_is_active(TELEMETRY_SERVICE, project_root=root) and all(
        service_is_active(name, project_root=root) for name in runtime_service_names(mode)
    )
    if runtime_services_active and not (
        url_is_ready(runtime_api_health_url(mode, project_root=root)) and url_is_ready(frontend_url)
    ):
        raise ServiceManagerError(
            "Stable runtime units are active but HTTP readiness is down. Automatic lifecycle restart is disabled; "
            "inspect the runtime supervisor blocked-state/incident diagnostics or issue an explicit restart."
        )
    run_systemctl("start", TELEMETRY_SERVICE, *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    if not skip_workflow_adapter_wait:
        wait_for_http(
            workflow_adapter_health_url_for_lane(
                PRODUCTION_LANE if mode == CONTAINER_RUNTIME_MODE else DEVELOPMENT_LANE
            ),
            timeout_seconds=wait_timeout_seconds,
        )
    if not skip_api_wait:
        wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
    wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)


@serialized_lifecycle_operation
def start_runtime_target(
    target: str | None = None,
    project_root: Path | None = None,
    *,
    skip_api_wait: bool = False,
    skip_workflow_adapter_wait: bool = False,
) -> None:
    normalized = str(target or "prod").strip().lower()
    wait_kwargs = {
        "skip_api_wait": skip_api_wait,
        "skip_workflow_adapter_wait": skip_workflow_adapter_wait,
    }
    if normalized in {"prod", "production", "stable", CONTAINER_RUNTIME_MODE}:
        start_all(project_root=project_root, runtime_mode=CONTAINER_RUNTIME_MODE, **wait_kwargs)
        return
    if normalized == DEV_RUNTIME_MODE:
        start_all(project_root=project_root, runtime_mode=DEV_RUNTIME_MODE, **wait_kwargs)
        return
    if normalized == "both":
        start_all(project_root=project_root, runtime_mode=CONTAINER_RUNTIME_MODE, **wait_kwargs)
        start_all(project_root=project_root, runtime_mode=DEV_RUNTIME_MODE, **wait_kwargs)
        return
    raise ServiceManagerError("Unknown BioModStack runtime target '{target}'. Expected dev, prod, or both".format(target=target))


@serialized_lifecycle_operation
def stop_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == CONTAINER_RUNTIME_MODE:
        run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
        run_systemctl(
            "stop",
            PRODUCTION_WORKFLOW_ADAPTER_SERVICE,
            CORE_RUNTIME_SERVICE,
            check=False,
            project_root=root,
        )
        return

    run_systemctl("stop", DEV_TARGET_UNIT, FRONTEND_SERVICE, check=False, project_root=root)
    cleanup_legacy_listener("frontend", root)
    if service_is_active(API_SERVICE, project_root=root):
        run_systemctl("stop", API_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("api", root, runtime_mode=mode)


@serialized_lifecycle_operation
def restart_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    wait_timeout_seconds = runtime_http_wait_timeout_seconds(mode)
    assert_runtime_listener_preflight(root, mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == DEV_RUNTIME_MODE:
        local_api_active = service_is_active(API_SERVICE, project_root=root)
        run_systemctl("restart", TELEMETRY_SERVICE, project_root=root)
        run_systemctl("stop", DEV_TARGET_UNIT, FRONTEND_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("frontend", root)
        services_to_start: list[str] = []
        if local_api_active or not url_is_ready(runtime_api_health_url(mode, project_root=root)):
            run_systemctl("stop", API_SERVICE, check=False, project_root=root)
            cleanup_legacy_listener("api", root, runtime_mode=mode)
            services_to_start.append(API_SERVICE)
        services_to_start.append(FRONTEND_SERVICE)
        run_systemctl("start", *services_to_start, DEV_TARGET_UNIT, project_root=root)
        wait_for_http(
            workflow_adapter_health_url_for_lane(DEVELOPMENT_LANE),
            timeout_seconds=wait_timeout_seconds,
        )
        wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
        wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)
        return

    ensure_target_enabled(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
    run_systemctl(
        "stop",
        PRODUCTION_WORKFLOW_ADAPTER_SERVICE,
        CORE_RUNTIME_SERVICE,
        check=False,
        project_root=root,
    )
    run_systemctl("start", TELEMETRY_SERVICE, *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(
        workflow_adapter_health_url_for_lane(PRODUCTION_LANE),
        timeout_seconds=wait_timeout_seconds,
    )
    wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
    wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)


@serialized_lifecycle_operation
def start_api(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    wait_timeout_seconds = runtime_http_wait_timeout_seconds(mode)
    assert_runtime_listener_preflight(root, mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == CONTAINER_RUNTIME_MODE:
        ensure_target_enabled(root, runtime_mode=mode)
        api_pids = listener_pids(runtime_api_port(mode, project_root=root))
        if api_pids and all(pid_is_biomodstack_runtime_container(pid, "api", root) for pid in api_pids):
            wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
            return
        run_core_runtime_script("up", "--no-deps", "bms-api", project_root=root)
        wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)
        return

    services_to_start: list[str] = []
    if not service_is_active(DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE, project_root=root):
        services_to_start.append(DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE)
    if not service_is_active(API_SERVICE, project_root=root):
        services_to_start.append(API_SERVICE)
    if services_to_start:
        run_systemctl("start", *services_to_start, project_root=root)
    wait_for_http(
        workflow_adapter_health_url_for_lane(DEVELOPMENT_LANE),
        timeout_seconds=wait_timeout_seconds,
    )
    wait_for_http(runtime_api_health_url(mode, project_root=root), timeout_seconds=wait_timeout_seconds)


@serialized_lifecycle_operation
def stop_api(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == CONTAINER_RUNTIME_MODE:
        run_core_runtime_script("stop", "bms-api", project_root=root)
        return

    run_systemctl("stop", API_SERVICE, check=False, project_root=root)
    cleanup_legacy_listener("api", root, runtime_mode=mode)


@serialized_lifecycle_operation
def restart_api(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        run_core_runtime_script("restart", "bms-api", project_root=root)
    elif service_is_active(API_SERVICE, project_root=root):
        run_systemctl("stop", API_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("api", root, runtime_mode=mode)
        run_systemctl("start", API_SERVICE, project_root=root)
    elif service_is_active(CORE_RUNTIME_SERVICE, project_root=root):
        run_core_runtime_script("restart", "bms-api", project_root=root)
    else:
        cleanup_legacy_listener("api", root, runtime_mode=mode)
        run_systemctl("start", API_SERVICE, project_root=root)
    wait_for_http(runtime_api_health_url(mode, project_root=root))


def status_lines(project_root: Path | None = None, runtime_mode: str | None = None) -> list[str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    descriptor = runtime_descriptor(project_root=root, runtime_mode=mode)
    api_health_url = runtime_api_health_url(mode, project_root=root)
    if mode == CONTAINER_RUNTIME_MODE:
        return [
            f"Runtime: {'active' if descriptor['runtime_active'] else 'inactive'} ({CORE_RUNTIME_SERVICE})",
            f"Workflow adapter: {'ready' if descriptor['health']['adapter_ready'] else 'not ready'} ({PRODUCTION_WORKFLOW_ADAPTER_HEALTH_URL})",
            f"API: {'ready' if descriptor['health']['api_ready'] else 'not ready'} ({api_health_url})",
            f"Frontend: {'ready' if descriptor['health']['frontend_ready'] else 'not ready'} ({descriptor['frontend_url']})",
            f"Workflow adapter log: {PRODUCTION_WORKFLOW_ADAPTER_LOG}",
            f"Runtime log: {CORE_RUNTIME_LOG}",
        ]

    services_by_name = {item["name"]: item["active"] for item in descriptor["services"]}
    frontend_unit_state = "unit active" if services_by_name.get(FRONTEND_SERVICE, False) else "unit inactive"
    return [
        f"API: {'ready' if descriptor['health']['api_ready'] else 'not ready'} ({api_health_url})",
        f"Frontend: {'ready' if descriptor['health']['frontend_ready'] else 'not ready'} ({FRONTEND_SERVICE} {frontend_unit_state}; {descriptor['frontend_url']})",
        f"API log: {API_LOG}",
        f"Frontend log: {FRONTEND_LOG}",
    ]

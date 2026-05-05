from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent

from biomodstack_runtime_profile import (
    DEFAULT_DEV_WEB_HOST_PORT,
    DEFAULT_WEB_HOST_PORT,
    get_biomodstack_config_dir as runtime_profile_config_dir,
    install_profile_snapshot as runtime_profile_snapshot,
    load_install_profile,
    save_install_profile,
)

API_SERVICE = "biomodstack-api.service"
FRONTEND_SERVICE = "biomodstack-frontend.service"
WORKFLOW_ADAPTER_SERVICE = "biomodstack-workflow-adapter.service"
CORE_RUNTIME_SERVICE = "biomodstack-core-runtime.service"
TARGET_UNIT = "biomodstack.target"
DEV_TARGET_UNIT = "biomodstack-dev.target"

DEV_RUNTIME_MODE = "dev"
CONTAINER_RUNTIME_MODE = "container"
DEFAULT_RUNTIME_MODE = CONTAINER_RUNTIME_MODE
VALID_RUNTIME_MODES = {DEV_RUNTIME_MODE, CONTAINER_RUNTIME_MODE}

API_PORT = 8000
FRONTEND_PORT = DEFAULT_DEV_WEB_HOST_PORT
STABLE_FRONTEND_PORT = DEFAULT_WEB_HOST_PORT
WORKFLOW_ADAPTER_PORT = 8001
API_HEALTH_URL = f"http://127.0.0.1:{API_PORT}/api/health"
FRONTEND_URL = f"http://127.0.0.1:{STABLE_FRONTEND_PORT}/bms/"
WORKFLOW_ADAPTER_HEALTH_URL = f"http://127.0.0.1:{WORKFLOW_ADAPTER_PORT}/api/workflow-adapter/health"
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
WORKFLOW_ADAPTER_LOG = LOG_DIR / "workflow-adapter.log"
CORE_RUNTIME_LOG = LOG_DIR / "core-runtime.log"


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
        return (WORKFLOW_ADAPTER_SERVICE, CORE_RUNTIME_SERVICE)
    return (FRONTEND_SERVICE,)


def runtime_target_unit(runtime_mode: str | None = None) -> str:
    mode = resolve_runtime_mode(runtime_mode)
    return TARGET_UNIT if mode == CONTAINER_RUNTIME_MODE else DEV_TARGET_UNIT


def all_runtime_service_names() -> tuple[str, ...]:
    return (API_SERVICE, FRONTEND_SERVICE, WORKFLOW_ADAPTER_SERVICE, CORE_RUNTIME_SERVICE)


def incompatible_runtime_service_names(runtime_mode: str | None = None) -> tuple[str, ...]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return (API_SERVICE,)
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
    dev_port = _coerce_host_port(resolved.get("dev_web_host_port"), label="dev_web_host_port") or FRONTEND_PORT
    prod_port = _coerce_host_port(resolved.get("web_host_port"), label="prod_web_host_port") or STABLE_FRONTEND_PORT
    return {
        "dev_web_host_port": dev_port,
        "prod_web_host_port": prod_port,
        "dev_url": runtime_frontend_url(DEV_RUNTIME_MODE, project_root=project_root),
        "prod_url": runtime_frontend_url(CONTAINER_RUNTIME_MODE, project_root=project_root),
    }


def runtime_port_settings(project_root: Path | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    snapshot = install_profile_snapshot(project_root=root)
    resolved = snapshot.get("resolved", {}) if isinstance(snapshot, Mapping) else {}
    if not isinstance(resolved, Mapping):
        resolved = {}
    return _runtime_port_settings_from_resolved(resolved, project_root=root)


def save_runtime_port_settings(
    dev_web_host_port: int | str | None = None,
    prod_web_host_port: int | str | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    dev_port = _coerce_host_port(dev_web_host_port, label="dev_web_host_port")
    prod_port = _coerce_host_port(prod_web_host_port, label="prod_web_host_port")
    payload = dict(load_install_profile())
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
            {"id": "workflow-adapter", "label": "Workflow adapter log", "path": str(WORKFLOW_ADAPTER_LOG)},
            {"id": "core-runtime", "label": "Container runtime log", "path": str(CORE_RUNTIME_LOG)},
        ]
    return [
        {"id": "api", "label": "API backend log", "path": str(API_LOG)},
        {"id": "frontend", "label": "Frontend/web log", "path": str(FRONTEND_LOG)},
    ]


def runtime_service_descriptors(project_root: Path | None = None, runtime_mode: str | None = None) -> list[dict[str, object]]:
    root = (project_root or get_project_root()).resolve()
    return [
        {"name": service_name, "active": service_is_active(service_name, root)}
        for service_name in runtime_service_names(runtime_mode)
    ]


def runtime_descriptor(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    services = runtime_service_descriptors(root, mode)
    install_profile = install_profile_snapshot(project_root=root)
    if not isinstance(install_profile, Mapping):
        install_profile = {}
    resolved_paths = install_profile.get("resolved", {})
    if not isinstance(resolved_paths, Mapping):
        resolved_paths = {}
    health = {
        "api_ready": url_is_ready(API_HEALTH_URL),
        "frontend_ready": url_is_ready(frontend_url),
    }
    if mode == CONTAINER_RUNTIME_MODE:
        health = {
            "adapter_ready": url_is_ready(WORKFLOW_ADAPTER_HEALTH_URL),
            **health,
        }
    return {
        "runtime_mode": mode,
        "runtime_active": all(service["active"] for service in services),
        "runtime_manager": "systemd-user",
        "api_url": f"http://127.0.0.1:{API_PORT}",
        "frontend_origin": runtime_frontend_origin(mode, project_root=root),
        "frontend_url": frontend_url,
        "browser_url": frontend_url,
        "router_basename": runtime_router_basename(mode),
        "supported_launch_surfaces": list(SUPPORTED_LAUNCH_SURFACES),
        "launch_preferences": load_launch_preferences(),
        "health": health,
        "services": services,
        "logs": runtime_log_descriptors(mode),
        "install_profile": dict(install_profile),
        "paths": dict(resolved_paths),
        "electron_shell_available": bool(electron_shell_available(project_root=root)),
        "capabilities": {
            "open_in_browser": True,
            "restart_all": True,
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
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def render_user_units(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        adapter_runner = root / "scripts" / "run_biomodstack_workflow_adapter.sh"
        core_runner = root / "scripts" / "run_biomodstack_core_runtime.sh"
        workflow_adapter_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack host workflow adapter
            PartOf={TARGET_UNIT}
            After=network-online.target
            Wants=network-online.target

            [Service]
            Type=simple
            Environment=BMS_HOME={root}
            Environment=BMS_RUNTIME_MODE={CONTAINER_RUNTIME_MODE}
            Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1
            ExecStart={adapter_runner}
            Restart=on-failure
            RestartSec=2
            TimeoutStopSec=20
            KillMode=control-group
            StandardOutput=append:{WORKFLOW_ADAPTER_LOG}
            StandardError=append:{WORKFLOW_ADAPTER_LOG}

            [Install]
            WantedBy={TARGET_UNIT}
            """
        )
        core_runtime_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack core runtime container stack
            PartOf={TARGET_UNIT}
            After=network-online.target docker.service {WORKFLOW_ADAPTER_SERVICE}
            Wants=network-online.target docker.service {WORKFLOW_ADAPTER_SERVICE}

            [Service]
            Type=oneshot
            RemainAfterExit=yes
            Environment=BMS_HOME={root}
            Environment=BMS_RUNTIME_MODE={CONTAINER_RUNTIME_MODE}
            ExecStart={core_runner}
            ExecStop={core_runner} down
            Restart=on-failure
            RestartSec=5
            TimeoutStopSec=60
            KillMode=control-group
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
            Wants={WORKFLOW_ADAPTER_SERVICE} {CORE_RUNTIME_SERVICE}

            [Install]
            WantedBy=default.target
            """
        )

        return {
            WORKFLOW_ADAPTER_SERVICE: workflow_adapter_unit,
            CORE_RUNTIME_SERVICE: core_runtime_unit,
            TARGET_UNIT: target_unit,
        }

    api_runner = root / "scripts" / "run_biomodstack_api.sh"
    frontend_runner = root / "scripts" / "run_biomodstack_frontend.sh"
    dev_web_host_port = runtime_frontend_port(DEV_RUNTIME_MODE, project_root=root)

    api_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack API service
        PartOf={DEV_TARGET_UNIT}
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_API_MODE=dev
        Environment=BMS_CPU_POWER_STRICT=0
        Environment=PYTHONUNBUFFERED=1
        ExecStart={api_runner}
        Restart=on-failure
        RestartSec=2
        TimeoutStopSec=20
        KillMode=control-group
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

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_FRONTEND_MODE=dev
        Environment=BMS_DEV_WEB_HOST_PORT={dev_web_host_port}
        ExecStart={frontend_runner}
        Restart=on-failure
        RestartSec=2
        TimeoutStopSec=20
        KillMode=control-group
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
        Wants={FRONTEND_SERVICE}

        [Install]
        WantedBy=default.target
        """
    )

    return {
        API_SERVICE: api_unit,
        FRONTEND_SERVICE: frontend_unit,
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
    API_LOG.touch(exist_ok=True)
    FRONTEND_LOG.touch(exist_ok=True)
    WORKFLOW_ADAPTER_LOG.touch(exist_ok=True)
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
    if "uvicorn" not in cmd or "main:app" not in cmd or f"--port {API_PORT}" not in cmd:
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


def pid_is_biomodstack_runtime_container(pid: int, kind: str, project_root: Path | None = None) -> bool:
    root = (project_root or get_project_root()).resolve()
    expected_service = {
        "api": "bms-api",
        "frontend": "bms-web",
    }.get(kind)
    if expected_service is None:
        raise ValueError(f"Unknown listener kind: {kind}")

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

    return []


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


def cleanup_legacy_listener(kind: str, project_root: Path | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    if kind == "api":
        port = API_PORT
        matcher = is_biomodstack_api_process
    elif kind == "frontend":
        port = runtime_frontend_port(DEV_RUNTIME_MODE, project_root=root)
        matcher = is_biomodstack_frontend_process
    else:
        raise ValueError(f"Unknown listener kind: {kind}")

    kill_targets: list[int] = []
    seen_targets: set[int] = set()
    for pid in listener_pids(port):
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
                if 200 <= response.status < 500:
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


def start_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    wait_timeout_seconds = runtime_http_wait_timeout_seconds(mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == DEV_RUNTIME_MODE:
        services_to_start: list[str] = []
        if not service_is_active(API_SERVICE, project_root=root) and not url_is_ready(API_HEALTH_URL):
            cleanup_legacy_listener("api", root)
            services_to_start.append(API_SERVICE)
        if not service_is_active(FRONTEND_SERVICE, project_root=root):
            cleanup_legacy_listener("frontend", root)
        services_to_start.append(FRONTEND_SERVICE)
        run_systemctl("start", *services_to_start, DEV_TARGET_UNIT, project_root=root)
        wait_for_http(API_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
        wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)
        return

    ensure_target_enabled(root, runtime_mode=mode)
    incompatible_services = incompatible_runtime_service_names(mode)
    if incompatible_services:
        run_systemctl("stop", *incompatible_services, check=False, project_root=root)
    if should_cleanup_legacy_listeners_before_start(mode, project_root=root):
        cleanup_legacy_listener("api", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(WORKFLOW_ADAPTER_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
    wait_for_http(API_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
    wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)


def start_runtime_target(target: str | None = None, project_root: Path | None = None) -> None:
    normalized = str(target or "prod").strip().lower()
    if normalized in {"prod", "production", "stable", CONTAINER_RUNTIME_MODE}:
        start_all(project_root=project_root, runtime_mode=CONTAINER_RUNTIME_MODE)
        return
    if normalized == DEV_RUNTIME_MODE:
        start_all(project_root=project_root, runtime_mode=DEV_RUNTIME_MODE)
        return
    if normalized == "both":
        start_all(project_root=project_root, runtime_mode=CONTAINER_RUNTIME_MODE)
        start_all(project_root=project_root, runtime_mode=DEV_RUNTIME_MODE)
        return
    raise ServiceManagerError("Unknown BioModStack runtime target '{target}'. Expected dev, prod, or both".format(target=target))


def stop_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, DEV_TARGET_UNIT, check=False, project_root=root)
    run_systemctl("stop", *all_runtime_service_names(), check=False, project_root=root)
    cleanup_legacy_listener("api", root)
    cleanup_legacy_listener("frontend", root)


def restart_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode, project_root=root)
    wait_timeout_seconds = runtime_http_wait_timeout_seconds(mode)
    ensure_user_units(root, runtime_mode=mode)

    if mode == DEV_RUNTIME_MODE:
        local_api_active = service_is_active(API_SERVICE, project_root=root)
        run_systemctl("stop", DEV_TARGET_UNIT, FRONTEND_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("frontend", root)
        services_to_start: list[str] = []
        if local_api_active or not url_is_ready(API_HEALTH_URL):
            run_systemctl("stop", API_SERVICE, check=False, project_root=root)
            cleanup_legacy_listener("api", root)
            services_to_start.append(API_SERVICE)
        services_to_start.append(FRONTEND_SERVICE)
        run_systemctl("start", *services_to_start, DEV_TARGET_UNIT, project_root=root)
        wait_for_http(API_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
        wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)
        return

    ensure_target_enabled(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
    run_systemctl("stop", API_SERVICE, WORKFLOW_ADAPTER_SERVICE, CORE_RUNTIME_SERVICE, check=False, project_root=root)
    cleanup_legacy_listener("api", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(WORKFLOW_ADAPTER_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
    wait_for_http(API_HEALTH_URL, timeout_seconds=wait_timeout_seconds)
    wait_for_http(frontend_url, timeout_seconds=wait_timeout_seconds)


def restart_api(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        run_core_runtime_script("restart", "bms-api", project_root=root)
    elif service_is_active(API_SERVICE, project_root=root):
        run_systemctl("stop", API_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("api", root)
        run_systemctl("start", API_SERVICE, project_root=root)
    elif service_is_active(CORE_RUNTIME_SERVICE, project_root=root):
        run_core_runtime_script("restart", "bms-api", project_root=root)
    else:
        cleanup_legacy_listener("api", root)
        run_systemctl("start", API_SERVICE, project_root=root)
    wait_for_http(API_HEALTH_URL)


def status_lines(project_root: Path | None = None, runtime_mode: str | None = None) -> list[str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    descriptor = runtime_descriptor(project_root=root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return [
            f"Runtime: {'active' if descriptor['runtime_active'] else 'inactive'} ({CORE_RUNTIME_SERVICE})",
            f"Workflow adapter: {'ready' if descriptor['health']['adapter_ready'] else 'not ready'} ({WORKFLOW_ADAPTER_HEALTH_URL})",
            f"API: {'ready' if descriptor['health']['api_ready'] else 'not ready'} ({API_HEALTH_URL})",
            f"Frontend: {'ready' if descriptor['health']['frontend_ready'] else 'not ready'} ({descriptor['frontend_url']})",
            f"Workflow adapter log: {WORKFLOW_ADAPTER_LOG}",
            f"Runtime log: {CORE_RUNTIME_LOG}",
        ]

    services_by_name = {item["name"]: item["active"] for item in descriptor["services"]}
    return [
        f"API: {'ready' if descriptor['health']['api_ready'] else 'not ready'} ({API_HEALTH_URL})",
        f"Frontend: {'active' if services_by_name.get(FRONTEND_SERVICE, False) else 'inactive'} ({FRONTEND_SERVICE}; {descriptor['frontend_url']})",
        f"API log: {API_LOG}",
        f"Frontend log: {FRONTEND_LOG}",
    ]

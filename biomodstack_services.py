from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from textwrap import dedent

API_SERVICE = "biomodstack-api.service"
FRONTEND_SERVICE = "biomodstack-frontend.service"
CORE_RUNTIME_SERVICE = "biomodstack-core-runtime.service"
TARGET_UNIT = "biomodstack.target"

DEV_RUNTIME_MODE = "dev"
CONTAINER_RUNTIME_MODE = "container"
VALID_RUNTIME_MODES = {DEV_RUNTIME_MODE, CONTAINER_RUNTIME_MODE}

API_PORT = 8000
FRONTEND_PORT = 5173
API_HEALTH_URL = f"http://127.0.0.1:{API_PORT}/api/health"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}/bms/"

_STATE_HOME = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser().resolve()
LOG_DIR = _STATE_HOME / "biomodstack" / "logs"
API_LOG = LOG_DIR / "api.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"
CORE_RUNTIME_LOG = LOG_DIR / "core-runtime.log"


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
    mode = (runtime_mode or os.getenv("BMS_RUNTIME_MODE") or DEV_RUNTIME_MODE).strip().lower()
    if mode not in VALID_RUNTIME_MODES:
        raise ServiceManagerError(
            f"Unknown BioModStack runtime mode '{mode}'. Expected one of: {', '.join(sorted(VALID_RUNTIME_MODES))}"
        )
    return mode


def runtime_service_names(runtime_mode: str | None = None) -> tuple[str, ...]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return (CORE_RUNTIME_SERVICE,)
    return (API_SERVICE, FRONTEND_SERVICE)


def all_runtime_service_names() -> tuple[str, ...]:
    return (API_SERVICE, FRONTEND_SERVICE, CORE_RUNTIME_SERVICE)


def incompatible_runtime_service_names(runtime_mode: str | None = None) -> tuple[str, ...]:
    active = set(runtime_service_names(runtime_mode))
    return tuple(name for name in all_runtime_service_names() if name not in active)


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
        core_runner = root / "scripts" / "run_biomodstack_core_runtime.sh"
        core_runtime_unit = dedent(
            f"""\
            [Unit]
            Description=BioModStack core runtime container stack
            PartOf={TARGET_UNIT}
            After=network-online.target docker.service
            Wants=network-online.target docker.service

            [Service]
            Type=simple
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
            Wants={CORE_RUNTIME_SERVICE}

            [Install]
            WantedBy=default.target
            """
        )

        return {
            CORE_RUNTIME_SERVICE: core_runtime_unit,
            TARGET_UNIT: target_unit,
        }

    api_runner = root / "scripts" / "run_biomodstack_api.sh"
    frontend_runner = root / "scripts" / "run_biomodstack_frontend.sh"

    api_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack API service
        PartOf={TARGET_UNIT}
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
        WantedBy={TARGET_UNIT}
        """
    )

    frontend_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack frontend dev service
        PartOf={TARGET_UNIT}
        After={API_SERVICE}
        Wants={API_SERVICE}

        [Service]
        Type=simple
        Environment=BMS_HOME={root}
        Environment=BMS_RUNTIME_MODE={DEV_RUNTIME_MODE}
        Environment=BMS_FRONTEND_MODE=dev
        ExecStart={frontend_runner}
        Restart=on-failure
        RestartSec=2
        TimeoutStopSec=20
        KillMode=control-group
        StandardOutput=append:{FRONTEND_LOG}
        StandardError=append:{FRONTEND_LOG}

        [Install]
        WantedBy={TARGET_UNIT}
        """
    )

    target_unit = dedent(
        f"""\
        [Unit]
        Description=BioModStack workstation runtime target
        Wants={API_SERVICE} {FRONTEND_SERVICE}

        [Install]
        WantedBy=default.target
        """
    )

    return {
        API_SERVICE: api_unit,
        FRONTEND_SERVICE: frontend_unit,
        TARGET_UNIT: target_unit,
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
    CORE_RUNTIME_LOG.touch(exist_ok=True)
    install_user_units(project_root=project_root, runtime_mode=runtime_mode)
    daemon_reload(project_root=project_root)


def service_is_active(service_name: str, project_root: Path | None = None) -> bool:
    result = run_systemctl("is-active", service_name, check=False, project_root=project_root)
    return result.returncode == 0 and result.stdout.strip() == "active"


def read_pid_cmdline(pid: int) -> str:
    return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def read_pid_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


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
    if f"--port {FRONTEND_PORT}" not in cmd:
        return False
    if any(token in cmd for token in ("vite", "npm run dev", "vite.js")) and frontend_dir in cmd:
        return True
    return cwd == frontend_dir and any(token in cmd for token in ("vite", "npm run dev", "node "))


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

    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        check=False,
        capture_output=True,
        text=True,
    )
    return _parse_pid_tokens(result.stdout)


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
        port = FRONTEND_PORT
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
    if mode != CONTAINER_RUNTIME_MODE:
        return True
    return not service_is_active(CORE_RUNTIME_SERVICE, project_root=project_root)


def start_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    incompatible_services = incompatible_runtime_service_names(mode)
    run_systemctl("stop", *incompatible_services, check=False, project_root=root)
    if should_cleanup_legacy_listeners_before_start(mode, project_root=root):
        cleanup_legacy_listener("api", root)
        cleanup_legacy_listener("frontend", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(API_HEALTH_URL)
    wait_for_http(FRONTEND_URL)


def stop_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
    run_systemctl("stop", *all_runtime_service_names(), check=False, project_root=root)
    cleanup_legacy_listener("api", root)
    cleanup_legacy_listener("frontend", root)


def restart_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
    run_systemctl("stop", *all_runtime_service_names(), check=False, project_root=root)
    cleanup_legacy_listener("api", root)
    cleanup_legacy_listener("frontend", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(API_HEALTH_URL)
    wait_for_http(FRONTEND_URL)


def restart_api(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        run_core_runtime_script("restart", "bms-api", project_root=root)
    else:
        run_systemctl("stop", API_SERVICE, check=False, project_root=root)
        cleanup_legacy_listener("api", root)
        run_systemctl("start", API_SERVICE, project_root=root)
    wait_for_http(API_HEALTH_URL)


def status_lines(project_root: Path | None = None, runtime_mode: str | None = None) -> list[str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        runtime_active = service_is_active(CORE_RUNTIME_SERVICE, root)
        api_ready = url_is_ready(API_HEALTH_URL)
        frontend_ready = url_is_ready(FRONTEND_URL)
        return [
            f"Runtime: {'active' if runtime_active else 'inactive'} ({CORE_RUNTIME_SERVICE})",
            f"API: {'ready' if api_ready else 'not ready'} ({API_HEALTH_URL})",
            f"Frontend: {'ready' if frontend_ready else 'not ready'} ({FRONTEND_URL})",
            f"Runtime log: {CORE_RUNTIME_LOG}",
        ]

    api_active = service_is_active(API_SERVICE, root)
    frontend_active = service_is_active(FRONTEND_SERVICE, root)
    return [
        f"API: {'active' if api_active else 'inactive'} ({API_SERVICE})",
        f"Frontend: {'active' if frontend_active else 'inactive'} ({FRONTEND_SERVICE})",
        f"API log: {API_LOG}",
        f"Frontend log: {FRONTEND_LOG}",
    ]

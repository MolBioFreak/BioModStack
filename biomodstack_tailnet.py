from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Iterator, Mapping
import urllib.error
import urllib.request

from biomodstack_services import (
    CONTAINER_RUNTIME_MODE,
    DEV_RUNTIME_MODE,
    FRONTEND_SERVICE,
    ServiceManagerError,
    WORKFLOW_ADAPTER_SERVICE,
    daemon_reload,
    listener_pids,
    render_user_units,
    run_systemctl,
    service_is_active,
    runtime_api_health_url,
    runtime_api_port,
    runtime_frontend_port,
    runtime_frontend_url,
    wait_for_http,
)

STATE_ROOT = Path.home() / ".local" / "state" / "biomodstack"
SELECTION_STATE_PATH = STATE_ROOT / "tailnet-environment.json"
SELECTION_LOCK_PATH = STATE_ROOT / "tailnet-environment.lock"

SUPPORTED_ENVIRONMENTS = ("development", "production")
CONTROL_PATH = "/api/tailnet-environment"
CONTROL_TARGET = "http://127.0.0.1:8001"
LEGACY_CONTROL_TARGET = "http://127.0.0.1:8001/api/workflow-adapter/tailnet-environment"
PRODUCTION_TAILNET_PROXY_PORT = 18081
PRODUCTION_TAILNET_PROXY_CONTAINER = "biomodstack-tailnet-production-proxy"
PRODUCTION_TAILNET_PROXY_IMAGE = "nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
PRODUCTION_TAILNET_PROXY_IMAGE_ID = "sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea"
PRODUCTION_TAILNET_PROXY_CONFIG = Path("docker/tailnet-production-proxy.conf")
PRODUCTION_TAILNET_PROXY_SHA_LABEL = "com.biomodstack.tailnet-proxy-config-sha"
_TAILSCALE_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+\-]{0,253}$")
_GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class TailnetEnvironmentError(ServiceManagerError):
    """Raised when an environment switch cannot be completed safely."""


def _host_user_systemd_dir() -> Path:
    # systemd --user always reads the login manager's real home configuration;
    # never let a Cordova/Gradle XDG_CONFIG_HOME override redirect ownership files.
    return Path.home() / ".config" / "systemd" / "user"


@dataclass(frozen=True)
class EnvironmentSpec:
    environment: str
    runtime_mode: str
    runtime_target: str
    frontend_url: str
    api_health_url: str
    serve_target: str
    frontend_port: int
    api_port: int


@dataclass(frozen=True)
class ServeSnapshot:
    origin: str
    root_proxy: str
    handlers: dict[str, dict[str, Any]]
    raw: dict[str, Any]


def environment_spec(environment: str | None, *, project_root: Path | None = None) -> EnvironmentSpec:
    normalized = str(environment or "").strip().lower()
    if normalized not in SUPPORTED_ENVIRONMENTS:
        raise TailnetEnvironmentError(
            "Tailnet environment must be selected explicitly: development or production"
        )
    root = (project_root or Path(__file__).resolve().parent).resolve()
    runtime_mode = DEV_RUNTIME_MODE if normalized == "development" else CONTAINER_RUNTIME_MODE
    frontend_url = runtime_frontend_url(runtime_mode, project_root=root)
    frontend_port = runtime_frontend_port(runtime_mode, project_root=root)
    # The operator development frontend shares the single managed API and
    # production database. Tailnet-facing Vite must never use isolated 18002.
    api_port = runtime_api_port(CONTAINER_RUNTIME_MODE, project_root=root)
    api_health_url = runtime_api_health_url(CONTAINER_RUNTIME_MODE, project_root=root)
    # Production nginx owns both /bms/ and /api and redirects / to /bms/. Development
    # Vite owns / and its canonical /api proxy. Mapping the origin, not a source path,
    # keeps absolute /api requests on the selected environment.
    serve_port = frontend_port if runtime_mode == DEV_RUNTIME_MODE else PRODUCTION_TAILNET_PROXY_PORT
    serve_target = f"http://127.0.0.1:{serve_port}"
    return EnvironmentSpec(
        environment=normalized,
        runtime_mode=runtime_mode,
        runtime_target="dev" if runtime_mode == DEV_RUNTIME_MODE else "prod",
        frontend_url=frontend_url,
        api_health_url=api_health_url,
        serve_target=serve_target,
        frontend_port=frontend_port,
        api_port=api_port,
    )


def _contains_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, Mapping):
        return any(_contains_true(nested) for nested in value.values())
    if isinstance(value, list):
        return any(_contains_true(item) for item in value)
    return False


def _contains_enabled_funnel(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).casefold()
            if normalized_key == "funnel" and nested is True:
                return True
            if normalized_key == "allowfunnel" and _contains_true(nested):
                return True
            if _contains_enabled_funnel(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_enabled_funnel(item) for item in value)
    return False


def serve_snapshot(status: Mapping[str, object]) -> ServeSnapshot:
    raw = dict(status)
    if _contains_enabled_funnel(raw):
        raise TailnetEnvironmentError("Tailscale Funnel is enabled; refusing to modify public exposure")
    web = raw.get("Web")
    if not isinstance(web, Mapping) or not web:
        raise TailnetEnvironmentError("Tailscale Serve has no HTTPS web host to update")

    candidates: list[tuple[str, Mapping[str, object]]] = []
    for host, config in web.items():
        if not isinstance(config, Mapping):
            continue
        handlers = config.get("Handlers")
        if isinstance(handlers, Mapping) and "/" in handlers:
            candidates.append((str(host), handlers))
    if len(candidates) != 1:
        raise TailnetEnvironmentError("Tailscale Serve must expose exactly one root handler")

    host, raw_handlers = candidates[0]
    root_handler = raw_handlers.get("/")
    if not isinstance(root_handler, Mapping):
        raise TailnetEnvironmentError("Tailscale Serve root handler is invalid")
    root_proxy = root_handler.get("Proxy")
    if not isinstance(root_proxy, str) or not root_proxy.startswith("http://127.0.0.1:"):
        raise TailnetEnvironmentError("Tailscale Serve root must proxy a canonical loopback HTTP target")

    normalized_handlers: dict[str, dict[str, Any]] = {}
    for path, handler in raw_handlers.items():
        if isinstance(handler, Mapping):
            normalized_handlers[str(path)] = dict(handler)
    hostname = host.rsplit(":", 1)[0] if host.endswith(":443") else host
    return ServeSnapshot(
        origin=f"https://{hostname}",
        root_proxy=root_proxy.rstrip("/"),
        handlers=normalized_handlers,
        raw=raw,
    )


def _run(command: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise TailnetEnvironmentError(f"{' '.join(command)} failed: {detail}")
    return result


def _read_serve_snapshot() -> ServeSnapshot:
    result = _run(["tailscale", "serve", "status", "--json"])
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TailnetEnvironmentError("Tailscale Serve returned invalid JSON") from exc
    if not isinstance(status, Mapping):
        raise TailnetEnvironmentError("Tailscale Serve status must be a JSON object")
    return serve_snapshot(status)


def _set_serve_root(target: str) -> None:
    if not target.startswith("http://127.0.0.1:"):
        raise TailnetEnvironmentError("refusing non-loopback Tailnet proxy target")
    _run(["tailscale", "serve", "--bg", "--yes", target])


def _set_serve_path(path: str, target: str) -> None:
    if path != CONTROL_PATH or target not in (CONTROL_TARGET, LEGACY_CONTROL_TARGET):
        raise TailnetEnvironmentError("refusing an unexpected Tailnet control-path mapping")
    _run(["tailscale", "serve", "--bg", "--yes", f"--set-path={path}", target])


def _clear_serve_path(path: str) -> None:
    if path != CONTROL_PATH:
        raise TailnetEnvironmentError("refusing to clear an unexpected Tailnet path")
    _run(["tailscale", "serve", "--bg", "--yes", f"--set-path={path}", "off"])


def _ensure_control_route(snapshot: ServeSnapshot) -> bool:
    existing = snapshot.handlers.get(CONTROL_PATH)
    rollback_target: str | None = None
    if existing is not None:
        existing_target = str(existing.get("Proxy", "")).rstrip("/")
        if existing_target == CONTROL_TARGET:
            return False
        if existing_target != LEGACY_CONTROL_TARGET:
            raise TailnetEnvironmentError(
                f"Tailnet control path is already owned by an unexpected target: {existing}"
            )
        rollback_target = LEGACY_CONTROL_TARGET
    _set_serve_path(CONTROL_PATH, CONTROL_TARGET)
    try:
        installed = _read_serve_snapshot()
        handler = installed.handlers.get(CONTROL_PATH)
        if not isinstance(handler, Mapping) or str(handler.get("Proxy", "")).rstrip("/") != CONTROL_TARGET:
            raise TailnetEnvironmentError("Tailnet control-path installation did not verify")
    except Exception as exc:
        if rollback_target is None:
            _clear_serve_path(CONTROL_PATH)
        else:
            _set_serve_path(CONTROL_PATH, rollback_target)
        restored = _read_serve_snapshot()
        if restored.raw != snapshot.raw:
            raise TailnetEnvironmentError(
                f"control-path installation failed and rollback did not restore Serve: {exc}"
            ) from exc
        raise
    return True


def _url_probe(url: str, *, expect_json: bool = False, timeout: float = 20.0) -> dict[str, object]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json" if expect_json else "text/html,*/*"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read(1024 * 1024)
            final_url = response.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TailnetEnvironmentError(f"health probe failed for {url}: {exc}") from exc
    if status != 200:
        raise TailnetEnvironmentError(f"health probe returned HTTP {status} for {url}")
    payload: object = None
    if expect_json:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TailnetEnvironmentError(f"health probe returned invalid JSON for {url}") from exc
        if not isinstance(payload, Mapping):
            raise TailnetEnvironmentError(f"health probe returned a non-object for {url}")
        if payload.get("status") != "healthy":
            raise TailnetEnvironmentError(f"selected API is not healthy at {url}")
    return {
        "url": url,
        "final_url": final_url,
        "status": status,
        "payload": payload,
    }


def _api_build_identity(probe: Mapping[str, object], *, source: str) -> dict[str, str]:
    payload = probe.get("payload")
    build = payload.get("build") if isinstance(payload, Mapping) else None
    if not isinstance(build, Mapping):
        raise TailnetEnvironmentError(f"{source} API health has no build provenance")
    identity = {
        "revision": str(build.get("revision", "")).strip(),
        "build_id": str(build.get("build_id", "")).strip(),
        "build_time": str(build.get("build_time", "")).strip(),
    }
    if not _GIT_REVISION_PATTERN.fullmatch(identity["revision"]) or not all(identity.values()):
        raise TailnetEnvironmentError(f"{source} API health has incomplete build provenance")
    return identity


def _git_revision(root: Path) -> str:
    return _run(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout.strip()


def _pid_report(port: int) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for pid in sorted(listener_pids(port)):
        proc = Path("/proc") / str(pid)
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            cmdline = ""
        try:
            cwd = os.readlink(proc / "cwd")
        except OSError:
            cwd = None
        try:
            cgroup = (proc / "cgroup").read_text(encoding="utf-8")
        except OSError:
            cgroup = ""
        reports.append(
            {
                "pid": pid,
                "cwd": cwd,
                "cmdline": cmdline,
                "cgroup": cgroup,
                "build_revision": (
                    _pid_environment_value(pid, "VITE_BMS_BUILD_SHA")
                    or _pid_environment_value(pid, "BMS_BUILD_SHA")
                ),
            }
        )
    return reports


def _pid_environment_value(pid: int, key: str) -> str | None:
    try:
        entries = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return None
    prefix = key.encode("ascii") + b"="
    for entry in entries:
        if entry.startswith(prefix):
            return entry[len(prefix):].decode("utf-8", errors="replace")
    return None


def _process_cgroup(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "cgroup").read_text(encoding="utf-8")
    except OSError:
        return ""


def _dev_frontend_matches_root(spec: EnvironmentSpec, root: Path) -> bool:
    expected = str((root / "platform" / "frontend").resolve())
    revision = _git_revision(root)
    reports = _pid_report(spec.frontend_port)
    for report in reports:
        pid = report.get("pid")
        if (
            report.get("cwd") == expected
            and isinstance(pid, int)
            and _pid_environment_value(pid, "VITE_BMS_BUILD_SHA") == revision
        ):
            return True
    return False


def _atomic_write(path: Path, content: str | bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if isinstance(content, bytes):
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        metadata_fd = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(metadata_fd)
        finally:
            os.close(metadata_fd)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _install_operator_development_frontend(root: Path) -> None:
    units = render_user_units(project_root=root, runtime_mode=DEV_RUNTIME_MODE)
    frontend = units[FRONTEND_SERVICE]
    dev_api_target = f"http://127.0.0.1:{runtime_api_port(DEV_RUNTIME_MODE, project_root=root)}"
    managed_api_target = f"http://127.0.0.1:{runtime_api_port(CONTAINER_RUNTIME_MODE, project_root=root)}"
    expected_line = f"Environment=BMS_DEV_API_PROXY_TARGET={dev_api_target}"
    if expected_line not in frontend:
        raise TailnetEnvironmentError("could not render the canonical development frontend proxy contract")
    revision = _git_revision(root)
    build_time = _run(["git", "-C", str(root), "show", "-s", "--format=%cI", "HEAD"]).stdout.strip()
    home_line = f"Environment=BMS_HOME={root}"
    if home_line not in frontend:
        raise TailnetEnvironmentError("development frontend unit has no canonical source identity")
    frontend = frontend.replace(
        home_line,
        "\n".join(
            (
                home_line,
                f"Environment=VITE_BMS_BUILD_SHA={revision}",
                f"Environment=VITE_BMS_BUILD_ID=tailnet-development-{revision[:12]}",
                f"Environment=VITE_BMS_BUILD_TIME={build_time}",
            )
        ),
        1,
    )
    frontend = frontend.replace(
        expected_line,
        f"Environment=BMS_DEV_API_PROXY_TARGET={managed_api_target}",
        1,
    )
    _atomic_write(_host_user_systemd_dir() / FRONTEND_SERVICE, frontend)
    _atomic_write(
        _host_user_systemd_dir() / f"{FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf",
        "[Service]\n"
        f"Environment=BMS_HOME={root}\n"
        f"Environment=BMS_DEV_API_PROXY_TARGET={managed_api_target}\n"
        f"Environment=VITE_BMS_BUILD_SHA={revision}\n"
        f"Environment=VITE_BMS_BUILD_ID=tailnet-development-{revision[:12]}\n"
        f"Environment=VITE_BMS_BUILD_TIME={build_time}\n"
        "ExecStartPre=\n"
        "ExecStartPre=/usr/bin/sh -c 'test \"$BMS_DEV_API_PROXY_TARGET\" = \"http://127.0.0.1:8000\"'\n"
        f"ExecStartPre=/usr/bin/env python3 {root}/scripts/rotate_biomodstack_logs.py\n"
        "ExecStart=\n"
        f"ExecStart={root}/scripts/run_biomodstack_frontend.sh\n",
    )
    daemon_reload(project_root=root)


def _adapter_matches_root(root: Path) -> bool:
    expected = str((root / "platform" / "api").resolve())
    revision = _git_revision(root)
    reports = _pid_report(8001)
    for report in reports:
        pid = report.get("pid")
        if (
            report.get("cwd") == expected
            and isinstance(pid, int)
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_SOURCE_REVISION") == revision
        ):
            return True
    return False


def _tailnet_owner_login() -> str:
    result = _run(["tailscale", "status", "--json"])
    try:
        status = json.loads(result.stdout)
        user_id = str(status["Self"]["UserID"])
        login = str(status["User"][user_id]["LoginName"]).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TailnetEnvironmentError("could not resolve the Tailnet owner identity") from exc
    if not _TAILSCALE_LOGIN_PATTERN.fullmatch(login):
        raise TailnetEnvironmentError("Tailnet owner identity is malformed")
    return login


def _install_adapter_control_policy(root: Path) -> str:
    login = _tailnet_owner_login()
    revision = _git_revision(root)
    dropin = _host_user_systemd_dir() / f"{WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    _atomic_write(
        dropin,
        "[Service]\n"
        f"Environment=BMS_HOME={root}\n"
        f"Environment=BMS_TAILNET_CONTROL_SOURCE_REVISION={revision}\n"
        "Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1\n"
        "Environment=BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS=127.0.0.1,::1\n"
        f"Environment=BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS={login}\n"
        "ExecStartPre=\n"
        f"ExecStartPre=/usr/bin/env python3 {root}/scripts/rotate_biomodstack_logs.py\n"
        "ExecStart=\n"
        f"ExecStart={root}/scripts/run_biomodstack_workflow_adapter.sh\n",
    )
    return login


def _listener_bind_addresses(port: int) -> set[str]:
    result = _run(["ss", "-H", "-ltn", "sport", "=", f":{port}"])
    addresses: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        endpoint = fields[3]
        if endpoint.startswith("[") and "]:" in endpoint:
            addresses.add(endpoint[1:endpoint.rfind("]:")])
        elif ":" in endpoint:
            addresses.add(endpoint.rsplit(":", 1)[0])
    return addresses


def _adapter_control_policy_matches(login: str) -> bool:
    if _listener_bind_addresses(8001) not in ({"127.0.0.1"}, {"::1"}, {"127.0.0.1", "::1"}):
        return False
    for pid in listener_pids(8001):
        if (
            _pid_environment_value(pid, "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS") == login
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS") == "127.0.0.1,::1"
            and _pid_environment_value(pid, "BMS_WORKFLOW_ADAPTER_BIND_HOST") == "127.0.0.1"
        ):
            return True
    return False


def _wait_for_adapter_policy(root: Path, login: str, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _adapter_matches_root(root) and _adapter_control_policy_matches(login):
            return True
        time.sleep(0.25)
    return False


def _wait_for_development_frontend(spec: EnvironmentSpec, root: Path, timeout_seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _dev_frontend_matches_root(spec, root):
            return True
        time.sleep(0.25)
    return False


def _start_selected_environment(spec: EnvironmentSpec, root: Path) -> None:
    # Both views use the existing managed API/DB. Selection must not rebuild or
    # restart it, nor start the unselected environment. Require the shared API
    # (and immutable production web, when selected) to be healthy first.
    _url_probe(spec.api_health_url, expect_json=True)
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        _url_probe(spec.frontend_url)
        _validated_production_tailnet_proxy(root)
        _url_probe(f"http://127.0.0.1:{PRODUCTION_TAILNET_PROXY_PORT}/")
    allowed_login = _install_adapter_control_policy(root)
    if not _adapter_matches_root(root) or not _adapter_control_policy_matches(allowed_login):
        adapter_unit = _host_user_systemd_dir() / WORKFLOW_ADAPTER_SERVICE
        if not adapter_unit.is_file():
            raise TailnetEnvironmentError("managed workflow-adapter systemd unit is not installed")
        daemon_reload(project_root=root)
        run_systemctl("restart", WORKFLOW_ADAPTER_SERVICE, project_root=root)
        if not _wait_for_adapter_policy(root, allowed_login):
            raise TailnetEnvironmentError("workflow adapter did not restart from canonical authenticated source")
    if spec.runtime_mode == DEV_RUNTIME_MODE:
        _install_operator_development_frontend(root)
        if not _dev_frontend_matches_root(spec, root):
            run_systemctl("reset-failed", FRONTEND_SERVICE, project_root=root)
            run_systemctl("restart", FRONTEND_SERVICE, project_root=root)
    wait_for_http(spec.frontend_url)
    wait_for_http(spec.api_health_url)
    _url_probe(spec.frontend_url)
    _url_probe(spec.api_health_url, expect_json=True)
    if spec.runtime_mode == DEV_RUNTIME_MODE and not _wait_for_development_frontend(spec, root):
        raise TailnetEnvironmentError(
            f"development frontend listener is not owned by {root / 'platform' / 'frontend'}"
        )


def _docker_runtime_report(required_names: set[str]) -> dict[str, object] | None:
    try:
        raw = _run(["docker", "inspect", *sorted(required_names)]).stdout
        inspected = json.loads(raw)
    except (TailnetEnvironmentError, FileNotFoundError, json.JSONDecodeError):
        return None
    containers: list[dict[str, object]] = []
    for item in inspected:
        if not isinstance(item, Mapping):
            continue
        config = item.get("Config", {})
        state = item.get("State", {})
        labels = config.get("Labels", {}) if isinstance(config, Mapping) else {}
        pid = state.get("Pid") if isinstance(state, Mapping) else None
        cgroup = _process_cgroup(pid) if isinstance(pid, int) and pid > 0 else ""
        command = " ".join(
            [str(item.get("Path", "")), *(str(arg) for arg in item.get("Args", []) or [])]
        ).strip()
        containers.append(
            {
                "name": str(item.get("Name", "")).lstrip("/"),
                "container_id": str(item.get("Id", "")),
                "image_id": str(item.get("Image", "")),
                "revision": str(labels.get("org.opencontainers.image.revision", "")),
                "compose_working_dir": str(labels.get("com.docker.compose.project.working_dir", "")),
                "pid": pid,
                "cgroup": cgroup,
                "cmdline": command,
                "cwd": str(config.get("WorkingDir", "") or "/") if isinstance(config, Mapping) else "/",
            }
        )
    return {"containers": containers}


def _container_listener_pids(container_name: str, port: int) -> list[int]:
    script = r'''port_hex=$(printf '%04X' "$1")
inodes=$(awk -v suffix=":$port_hex" '$4 == "0A" && substr($2, length($2)-4) == suffix {print $10}' /proc/net/tcp /proc/net/tcp6 2>/dev/null)
for proc in /proc/[0-9]*; do
  for fd in "$proc"/fd/*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    for inode in $inodes; do
      if [ "$target" = "socket:[$inode]" ]; then basename "$proc"; break 2; fi
    done
  done
done'''
    result = _run([
        "docker", "exec", container_name, "/bin/sh", "-ec", script, "--", str(port)
    ])
    return sorted({int(line) for line in result.stdout.splitlines() if line.isdigit()})


def _validated_production_tailnet_proxy(root: Path) -> dict[str, object]:
    config = (root / PRODUCTION_TAILNET_PROXY_CONFIG).resolve()
    if not config.is_file():
        raise TailnetEnvironmentError(f"production Tailnet proxy config is missing: {config}")
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    try:
        raw = _run(["docker", "inspect", PRODUCTION_TAILNET_PROXY_CONTAINER]).stdout
        item = json.loads(raw)[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError, TailnetEnvironmentError) as exc:
        raise TailnetEnvironmentError("production Tailnet host-normalization proxy is not deployed") from exc
    labels = item.get("Config", {}).get("Labels", {})
    mounts = item.get("Mounts", [])
    expected_mount = any(
        isinstance(mount, Mapping)
        and Path(str(mount.get("Source", ""))).resolve() == config
        and mount.get("Destination") == "/etc/nginx/conf.d/default.conf"
        and mount.get("RW") is False
        for mount in mounts
    )
    if labels.get(PRODUCTION_TAILNET_PROXY_SHA_LABEL) != config_sha or not expected_mount:
        raise TailnetEnvironmentError("production Tailnet proxy does not match the reviewed config")
    host_config = item.get("HostConfig", {})
    if (
        labels.get("com.biomodstack.tailnet-proxy-owner") != "compose.core-runtime"
        or labels.get("com.docker.compose.project") != "biomodstack-tailnet-control"
        or labels.get("com.docker.compose.service") != "tailnet-production-proxy"
        or host_config.get("RestartPolicy", {}).get("Name") != "unless-stopped"
        or host_config.get("ReadonlyRootfs") is not True
    ):
        raise TailnetEnvironmentError("production Tailnet proxy has no durable canonical Compose owner")
    if host_config.get("NetworkMode") != "host" or item.get("State", {}).get("Running") is not True:
        raise TailnetEnvironmentError("production Tailnet proxy is not running on the host network")
    image_id = str(item.get("Image", ""))
    config_identity = item.get("Config", {})
    if (
        image_id != PRODUCTION_TAILNET_PROXY_IMAGE_ID
        or str(config_identity.get("Image", "")) != PRODUCTION_TAILNET_PROXY_IMAGE
        or item.get("Path") != "/docker-entrypoint.sh"
        or item.get("Args") != ["nginx", "-g", "daemon off;"]
        or config_identity.get("Entrypoint") != ["/docker-entrypoint.sh"]
        or config_identity.get("Cmd") != ["nginx", "-g", "daemon off;"]
    ):
        raise TailnetEnvironmentError("production Tailnet proxy executable identity is not pinned")
    pid = item.get("State", {}).get("Pid")
    cgroup = _process_cgroup(pid) if isinstance(pid, int) and pid > 0 else ""
    container_id = str(item.get("Id", ""))
    command = " ".join(
        [str(item.get("Path", "")), *(str(arg) for arg in item.get("Args", []) or [])]
    ).strip()
    if not isinstance(pid, int) or pid <= 0 or container_id[:12] not in cgroup or not command:
        raise TailnetEnvironmentError("production Tailnet proxy process provenance is incomplete")
    listener_pids_in_container = _container_listener_pids(
        PRODUCTION_TAILNET_PROXY_CONTAINER, PRODUCTION_TAILNET_PROXY_PORT
    )
    if not listener_pids_in_container:
        raise TailnetEnvironmentError("production Tailnet proxy has no container-owned listener")
    return {
        "container_id": container_id,
        "image": str(item.get("Config", {}).get("Image", "")),
        "image_id": image_id,
        "config_path": str(config),
        "config_sha256": config_sha,
        "listener_port": PRODUCTION_TAILNET_PROXY_PORT,
        "pid": pid,
        "listener_pids": listener_pids_in_container,
        "cgroup": cgroup,
        "cmdline": command,
        "cwd": str(item.get("Config", {}).get("WorkingDir", "") or "/"),
    }


def _validated_container_runtime(root: Path, *, require_web: bool) -> dict[str, object]:
    required_names = {"biomodstack-api"}
    if require_web:
        required_names.add("biomodstack-web")
    report = _docker_runtime_report(required_names)
    if report is None:
        raise TailnetEnvironmentError("managed container runtime provenance is unavailable")
    raw_containers = report.get("containers")
    if not isinstance(raw_containers, list):
        raise TailnetEnvironmentError("managed container runtime provenance is unavailable")
    containers = {
        str(item.get("name")): item
        for item in raw_containers
        if isinstance(item, Mapping)
    }
    if not required_names.issubset(containers):
        raise TailnetEnvironmentError(
            f"managed container runtime is missing {sorted(required_names - containers.keys())}"
        )

    selected = [containers[name] for name in sorted(required_names)]
    revisions = {str(item.get("revision", "")) for item in selected}
    working_dirs = {str(item.get("compose_working_dir", "")) for item in selected}
    if len(revisions) != 1 or not _GIT_REVISION_PATTERN.fullmatch(next(iter(revisions), "")):
        raise TailnetEnvironmentError("managed container revisions are missing or inconsistent")
    if len(working_dirs) != 1:
        raise TailnetEnvironmentError("managed containers do not share one canonical Compose owner")
    if any(not str(item.get("image_id", "")).startswith("sha256:") for item in selected):
        raise TailnetEnvironmentError("managed container image identity is missing")
    if any(
        not isinstance(item.get("pid"), int)
        or int(item["pid"]) <= 0
        or str(item.get("container_id", ""))[:12] not in str(item.get("cgroup", ""))
        or not str(item.get("cmdline", ""))
        or not str(item.get("cwd", ""))
        for item in selected
    ):
        raise TailnetEnvironmentError("managed container process provenance is incomplete")

    revision = next(iter(revisions))
    compose_root = Path(next(iter(working_dirs))).resolve()
    if not compose_root.is_dir() or _git_revision(compose_root) != revision:
        raise TailnetEnvironmentError(
            "managed container Compose owner does not exactly match its image revision"
        )
    _run(["git", "-C", str(root), "merge-base", "--is-ancestor", revision, "HEAD"])
    report["validated_revision"] = revision
    report["validated_compose_root"] = str(compose_root)
    return report


def _verify_selected_environment(
    spec: EnvironmentSpec,
    root: Path,
    snapshot: ServeSnapshot,
) -> dict[str, object]:
    if snapshot.root_proxy != spec.serve_target.rstrip("/"):
        raise TailnetEnvironmentError(
            f"Tailscale Serve root is {snapshot.root_proxy}, expected {spec.serve_target}"
        )
    local_frontend = _url_probe(spec.frontend_url)
    local_api = _url_probe(spec.api_health_url, expect_json=True)
    public_frontend = _url_probe(snapshot.origin + "/")
    public_api = _url_probe(snapshot.origin + "/api/health", expect_json=True)
    local_api_build = _api_build_identity(local_api, source="local")
    public_api_build = _api_build_identity(public_api, source="Tailnet")
    if local_api_build != public_api_build:
        raise TailnetEnvironmentError(
            "local and Tailnet API build provenance disagree: "
            f"local={local_api_build}, Tailnet={public_api_build}"
        )
    managed_api_runtime = _validated_container_runtime(root, require_web=False)
    if managed_api_runtime.get("validated_revision") != local_api_build["revision"]:
        raise TailnetEnvironmentError(
            "API build provenance does not match the managed container revision"
        )
    report: dict[str, object] = {
        "selected_environment": spec.environment,
        "runtime_mode": spec.runtime_mode,
        "runtime_target": spec.runtime_target,
        "project_root": str(root),
        "project_revision": _git_revision(root),
        "frontend_target": spec.frontend_url,
        "api_health_target": spec.api_health_url,
        "serve_root_proxy": snapshot.root_proxy,
        "tailnet_origin": snapshot.origin,
        "serve_handlers": snapshot.handlers,
        "frontend_listeners": _pid_report(spec.frontend_port),
        "api_listeners": _pid_report(spec.api_port),
        "health": {
            "local_frontend": local_frontend,
            "local_api": local_api,
            "tailnet_frontend": public_frontend,
            "tailnet_api": public_api,
        },
    }
    report["managed_api_runtime"] = managed_api_runtime
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        report["container_runtime"] = _validated_container_runtime(root, require_web=True)
        report["tailnet_production_proxy"] = _validated_production_tailnet_proxy(root)
        report["tailnet_production_proxy_listeners"] = _pid_report(PRODUCTION_TAILNET_PROXY_PORT)
    return report


def _write_selection_state(report: Mapping[str, object]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(dict(report), indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tailnet-environment-", dir=STATE_ROOT)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        metadata_fd = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(metadata_fd)
        finally:
            os.close(metadata_fd)
        os.replace(temporary, SELECTION_STATE_PATH)
        directory_fd = os.open(STATE_ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _selection_lock() -> Iterator[None]:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with SELECTION_LOCK_PATH.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class ServiceOwnershipSnapshot:
    files: Mapping[Path, tuple[bytes, int] | None]
    active: Mapping[str, bool]


def _service_ownership_snapshot(root: Path, spec: EnvironmentSpec) -> ServiceOwnershipSnapshot:
    systemd_dir = _host_user_systemd_dir()
    paths = [
        SELECTION_STATE_PATH,
        systemd_dir / f"{WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf",
    ]
    services = [WORKFLOW_ADAPTER_SERVICE]
    if spec.runtime_mode == DEV_RUNTIME_MODE:
        paths.extend((
            systemd_dir / FRONTEND_SERVICE,
            systemd_dir / f"{FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf",
        ))
        services.append(FRONTEND_SERVICE)
    return ServiceOwnershipSnapshot(
        files={
            path: (path.read_bytes(), path.stat().st_mode & 0o7777) if path.is_file() else None
            for path in paths
        },
        active={service: service_is_active(service, project_root=root) for service in services},
    )


def _restore_service_ownership(snapshot: ServiceOwnershipSnapshot, root: Path) -> None:
    for path, content in snapshot.files.items():
        if content is None:
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed and path.parent.is_dir():
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        else:
            file_bytes, mode = content
            _atomic_write(path, file_bytes, mode=mode)
    daemon_reload(project_root=root)
    for service, was_active in snapshot.active.items():
        if was_active:
            run_systemctl("reset-failed", service, project_root=root)
            run_systemctl("restart", service, project_root=root)
        else:
            run_systemctl("stop", service, project_root=root)


def select_tailnet_environment(
    environment: str | None,
    *,
    project_root: Path | None = None,
) -> dict[str, object]:
    # Validate before acquiring the lifecycle lock or invoking any service/Serve action.
    root = (project_root or Path(__file__).resolve().parent).resolve()
    spec = environment_spec(environment, project_root=root)

    with _selection_lock():
        prior = _read_serve_snapshot()
        ownership = _service_ownership_snapshot(root, spec)
        prior_non_root = {path: handler for path, handler in prior.handlers.items() if path != "/"}
        control_route_added = False
        try:
            _start_selected_environment(spec, root)
            control_route_added = _ensure_control_route(prior)
            expected_non_root = dict(prior_non_root)
            expected_non_root[CONTROL_PATH] = {"Proxy": CONTROL_TARGET}
            _set_serve_root(spec.serve_target)
            selected = _read_serve_snapshot()
            selected_non_root = {path: handler for path, handler in selected.handlers.items() if path != "/"}
            if selected_non_root != expected_non_root:
                raise TailnetEnvironmentError("Tailscale Serve changed unrelated path handlers")
            report = _verify_selected_environment(spec, root, selected)
            report["previous_serve_root_proxy"] = prior.root_proxy
            _write_selection_state(report)
        except Exception as exc:
            rollback_detail = ""
            try:
                _set_serve_root(prior.root_proxy)
                if control_route_added:
                    _clear_serve_path(CONTROL_PATH)
                restored = _read_serve_snapshot()
                if restored.raw != prior.raw:
                    raise TailnetEnvironmentError("rollback Serve configuration verification failed")
                _restore_service_ownership(ownership, root)
                wait_for_http(f"{prior.root_proxy.rstrip('/')}/", timeout_seconds=90.0)
            except Exception as rollback_exc:
                rollback_detail = f"; rollback also failed: {rollback_exc}"
            raise TailnetEnvironmentError(
                f"Tailnet environment switch failed and was rolled back to {prior.root_proxy}: {exc}{rollback_detail}"
            ) from exc
        return report


def current_tailnet_environment(*, project_root: Path | None = None) -> dict[str, object]:
    root = (project_root or Path(__file__).resolve().parent).resolve()
    snapshot = _read_serve_snapshot()
    development_target = environment_spec("development", project_root=root).serve_target.rstrip("/")
    production_target = environment_spec("production", project_root=root).serve_target.rstrip("/")
    if snapshot.root_proxy == development_target:
        environment = "development"
    elif snapshot.root_proxy == production_target:
        environment = "production"
    else:
        environment = "unmanaged"
    return {
        "selected_environment": environment,
        "serve_root_proxy": snapshot.root_proxy,
        "tailnet_origin": snapshot.origin,
        "serve_handlers": snapshot.handlers,
    }

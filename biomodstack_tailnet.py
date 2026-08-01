from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Iterator, Mapping, cast
import urllib.error
import urllib.parse
import urllib.request

from biomodstack_services import (
    API_SERVICE,
    CONTAINER_RUNTIME_MODE,
    DEV_RUNTIME_MODE,
    FRONTEND_SERVICE,
    ServiceManagerError,
    WORKFLOW_ADAPTER_SERVICE,
    daemon_reload,
    git_build_identity,
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
CANONICAL_DEVELOPMENT_ROOT = Path("/home/dalab/biomodstack/dev-test-canonical")
CANONICAL_PRODUCTION_ROOT = Path("/home/dalab/biomodstack/prod-main-canonical")
CONTROL_PATH = "/api/tailnet-environment"
CONTROL_TARGET = "http://127.0.0.1:8001"
LEGACY_CONTROL_TARGET = "http://127.0.0.1:8001/api/workflow-adapter/tailnet-environment"
STATS_TOOLKIT_TARGET = "http://127.0.0.1:18180"
GLOBAL_SERVE_HANDLERS: Mapping[str, str] = {
    CONTROL_PATH: CONTROL_TARGET,
    "/api/mobile-apk": "http://127.0.0.1:8000/api/mobile-apk",
    "/api/mobile-ui": "http://127.0.0.1:8000/api/mobile-ui",
    "/stats/embed": f"{STATS_TOOLKIT_TARGET}/stats",
    "/stats/assets": f"{STATS_TOOLKIT_TARGET}/stats/assets",
    "/stats/embed/health/live": f"{STATS_TOOLKIT_TARGET}/health/live",
    "/stats/embed/health/ready": f"{STATS_TOOLKIT_TARGET}/health/ready",
    "/stats/embed/api/v1/capabilities": f"{STATS_TOOLKIT_TARGET}/api/v1/capabilities",
    "/stats/embed/api/v1/tools": f"{STATS_TOOLKIT_TARGET}/api/v1/tools",
}
LEGACY_GLOBAL_SERVE_HANDLERS: Mapping[str, frozenset[str]] = {
    # The original embed mapping mounted the Stats server root. The governed
    # application now lives at /stats; allow only this exact prior target to be
    # migrated transactionally while continuing to reject foreign owners.
    "/stats/embed": frozenset({STATS_TOOLKIT_TARGET}),
}
DEPRECATED_SERVE_HANDLERS: Mapping[str, str] = {
    "/am": "http://127.0.0.1:5174/am",
    "/vlm": "http://127.0.0.1:8010",
}
PRODUCTION_TAILNET_PROXY_PORT = 18081
PRODUCTION_TAILNET_PROXY_CONTAINER = "biomodstack-tailnet-production-proxy"
PRODUCTION_TAILNET_PROXY_IMAGE = "nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
PRODUCTION_TAILNET_PROXY_IMAGE_ID = "sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea"
HOST_PROC_HELPER_IMAGE = "python:3.10-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015"
MANAGED_API_IMAGE_ID = "sha256:74bf34e32e2f5d0f72d3f6d117c1b4877c169e7a62c0da06ea05b75d5e0cd12c"
MANAGED_WEB_IMAGE_ID = "sha256:7e79b645349216a2457cd2f64af53beb26d9041c7911ed8438d6708239017c3e"
_IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
PRODUCTION_TAILNET_PROXY_CONFIG = Path("docker/tailnet-production-proxy.conf")
PRODUCTION_TAILNET_PROXY_SHA_LABEL = "com.biomodstack.tailnet-proxy-config-sha"
_TAILSCALE_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+\-]{0,253}$")
_GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_BUILD_TIME_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,9})?Z$"
)


class TailnetEnvironmentError(ServiceManagerError):
    """Raised when an environment switch cannot be completed safely."""


def _managed_image_id(environment_name: str, sealed_default: str) -> str:
    """Resolve a deploy-time image receipt without weakening its exact-ID contract."""
    value = os.environ.get(environment_name, sealed_default)
    if not _IMAGE_ID_PATTERN.fullmatch(value):
        raise TailnetEnvironmentError(f"{environment_name} is not an exact Docker image ID")
    return value


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
    # Each canonical lane owns its own API and database: test/Development uses
    # the isolated 18002 API while main/Production uses the container API.
    api_port = runtime_api_port(runtime_mode, project_root=root)
    api_health_url = runtime_api_health_url(runtime_mode, project_root=root)
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


def _canonical_loopback_http_target(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"127.0.0.1:{port}"
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"http://127.0.0.1:{port}"


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
    root_proxy = _canonical_loopback_http_target(root_handler.get("Proxy"))
    if root_proxy is None:
        raise TailnetEnvironmentError("Tailscale Serve root must proxy a canonical loopback HTTP target")

    normalized_handlers: dict[str, dict[str, Any]] = {}
    for path, handler in raw_handlers.items():
        if isinstance(handler, Mapping):
            normalized_handlers[str(path)] = dict(handler)
    # Receipts are consumed by JavaScript with an exact canonical target
    # contract. Never retain an otherwise tolerated root slash in the emitted
    # handler after canonicalizing root_proxy.
    normalized_handlers["/"]["Proxy"] = root_proxy
    hostname = host.rsplit(":", 1)[0] if host.endswith(":443") else host
    return ServeSnapshot(
        origin=f"https://{hostname}",
        root_proxy=root_proxy,
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
    if _canonical_loopback_http_target(target) != target:
        raise TailnetEnvironmentError("refusing non-loopback Tailnet proxy target")
    _run(["tailscale", "serve", "--bg", "--yes", target])


def _set_serve_path(path: str, target: str) -> None:
    allowed_targets = dict(GLOBAL_SERVE_HANDLERS)
    allowed_targets[CONTROL_PATH] = CONTROL_TARGET
    if path == CONTROL_PATH and target == LEGACY_CONTROL_TARGET:
        pass
    elif target in LEGACY_GLOBAL_SERVE_HANDLERS.get(path, frozenset()):
        pass
    elif allowed_targets.get(path) != target:
        raise TailnetEnvironmentError("refusing an unexpected global Tailnet path mapping")
    parsed = urllib.parse.urlsplit(target)
    target_origin = f"{parsed.scheme}://{parsed.netloc}"
    if (
        _canonical_loopback_http_target(target_origin) != target_origin
        or (parsed.path and not parsed.path.startswith("/"))
        or parsed.query
        or parsed.fragment
    ):
        raise TailnetEnvironmentError("refusing a non-loopback global Tailnet target")
    _run(["tailscale", "serve", "--bg", "--yes", f"--set-path={path}", target])


def _clear_serve_path(path: str) -> None:
    if path not in GLOBAL_SERVE_HANDLERS and path not in DEPRECATED_SERVE_HANDLERS:
        raise TailnetEnvironmentError("refusing to clear an unexpected Tailnet path")
    _run(["tailscale", "serve", "--bg", "--yes", f"--set-path={path}", "off"])


def _restore_control_route(snapshot: ServeSnapshot) -> None:
    prior_control = snapshot.handlers.get(CONTROL_PATH)
    if prior_control is None:
        _clear_serve_path(CONTROL_PATH)
        return
    prior_target = str(prior_control.get("Proxy", "")).rstrip("/")
    if prior_target not in (CONTROL_TARGET, LEGACY_CONTROL_TARGET):
        raise TailnetEnvironmentError("prior Tailnet control target is not restorable")
    _set_serve_path(CONTROL_PATH, prior_target)


def _ensure_control_route(snapshot: ServeSnapshot) -> bool:
    existing = snapshot.handlers.get(CONTROL_PATH)
    if existing is not None:
        existing_target = str(existing.get("Proxy", "")).rstrip("/")
        if existing_target == CONTROL_TARGET:
            return False
        if existing_target != LEGACY_CONTROL_TARGET:
            raise TailnetEnvironmentError(
                f"Tailnet control path is already owned by an unexpected target: {existing}"
            )

    try:
        # Treat the CLI call as an ambiguous mutation: it can apply the route and
        # then disconnect before returning success.
        _set_serve_path(CONTROL_PATH, CONTROL_TARGET)
        installed = _read_serve_snapshot()
        handler = installed.handlers.get(CONTROL_PATH)
        if not isinstance(handler, Mapping) or str(handler.get("Proxy", "")).rstrip("/") != CONTROL_TARGET:
            raise TailnetEnvironmentError("Tailnet control-path installation did not verify")
    except Exception as exc:
        _restore_control_route(snapshot)
        restored = _read_serve_snapshot()
        if restored.raw != snapshot.raw:
            raise TailnetEnvironmentError(
                f"control-path installation failed and rollback did not restore Serve: {exc}"
            ) from exc
        raise
    return True


def _control_route_needs_mutation(snapshot: ServeSnapshot) -> bool:
    """Preflight the control route without mutating it."""
    existing = snapshot.handlers.get(CONTROL_PATH)
    if existing is None:
        return True
    existing_target = str(existing.get("Proxy", "")).rstrip("/")
    if existing_target == CONTROL_TARGET:
        return False
    if existing_target != LEGACY_CONTROL_TARGET:
        raise TailnetEnvironmentError(
            f"Tailnet control path is already owned by an unexpected target: {existing}"
        )
    return True


def ensure_global_tailnet_routes() -> ServeSnapshot:
    """Install global routes and remove exact dead legacy mappings without changing `/`.

    The operation rejects conflicting managed-route owners, preserves arbitrary
    unrelated handlers byte-for-byte, and rolls back to the exact prior Serve
    document if any setter, removal, or verification step fails.
    """
    prior = _read_serve_snapshot()
    expected_handlers = dict(prior.handlers)
    mutations: list[tuple[str, str | None]] = []
    for path, target in GLOBAL_SERVE_HANDLERS.items():
        existing = prior.handlers.get(path)
        if existing is None:
            mutations.append((path, target))
        else:
            existing_target = str(existing.get("Proxy", "")).rstrip("/")
            allowed_existing = {target}
            if path == CONTROL_PATH:
                allowed_existing.add(LEGACY_CONTROL_TARGET)
            allowed_existing.update(LEGACY_GLOBAL_SERVE_HANDLERS.get(path, frozenset()))
            if existing_target not in allowed_existing:
                raise TailnetEnvironmentError(
                    f"global Tailnet path {path} is already owned by an unexpected target: {existing}"
                )
            if existing_target != target:
                mutations.append((path, target))
        expected_handlers[path] = {"Proxy": target}

    for path, dead_target in DEPRECATED_SERVE_HANDLERS.items():
        existing = prior.handlers.get(path)
        if existing is None:
            continue
        existing_target = str(existing.get("Proxy", "")).rstrip("/")
        if existing_target == dead_target:
            mutations.append((path, None))
            expected_handlers.pop(path, None)

    attempted: list[tuple[str, str | None]] = []
    try:
        for path, target in mutations:
            attempted.append((path, target))
            if target is None:
                _clear_serve_path(path)
            else:
                _set_serve_path(path, target)
        installed = _read_serve_snapshot()
        if installed.root_proxy != prior.root_proxy:
            raise TailnetEnvironmentError("global Tailnet policy changed Serve root")
        if installed.handlers != expected_handlers:
            raise TailnetEnvironmentError("global Tailnet policy installation did not verify")
        return installed
    except Exception as exc:
        rollback_errors: list[str] = []
        for path, _target in reversed(attempted):
            try:
                previous = prior.handlers.get(path)
                if previous is None:
                    _clear_serve_path(path)
                else:
                    previous_target = str(previous.get("Proxy", "")).rstrip("/")
                    if path in DEPRECATED_SERVE_HANDLERS:
                        _run([
                            "tailscale", "serve", "--bg", "--yes",
                            f"--set-path={path}", previous_target,
                        ])
                    else:
                        _set_serve_path(path, previous_target)
            except Exception as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        try:
            restored = _read_serve_snapshot()
            if restored.raw != prior.raw:
                raise TailnetEnvironmentError("Serve bytes differ from the prior snapshot")
        except Exception as rollback_exc:
            rollback_errors.append(f"verification: {rollback_exc}")
        detail = f"; rollback also failed: {' | '.join(rollback_errors)}" if rollback_errors else ""
        raise TailnetEnvironmentError(
            f"global Tailnet policy installation failed: {exc}{detail}"
        ) from exc


def _url_probe(
    url: str,
    *,
    expect_json: bool = False,
    expected_final_url: str | None = None,
    timeout: float = 20.0,
) -> dict[str, object]:
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
            requested = urllib.parse.urlsplit(url)
            final = urllib.parse.urlsplit(final_url)
            expected = expected_final_url or url
            if (
                final.scheme != requested.scheme
                or final.netloc != requested.netloc
                or final.username is not None
                or final.password is not None
                or final_url != expected
            ):
                raise TailnetEnvironmentError(
                    f"health probe did not terminate at its canonical endpoint: "
                    f"{url} -> {final_url}; expected {expected}"
                )
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
    if not isinstance(payload, Mapping):
        raise TailnetEnvironmentError(f"{source} API health has no build provenance")
    build = payload.get("build")
    if not isinstance(build, Mapping):
        raise TailnetEnvironmentError(f"{source} API health has no build provenance")
    raw_identity = {
        "revision": str(build.get("revision", "")),
        "build_id": str(build.get("build_id", "")),
        "build_time": str(build.get("build_time", "")),
    }
    identity = {key: value.strip() for key, value in raw_identity.items()}
    if not _GIT_REVISION_PATTERN.fullmatch(identity["revision"]) or not all(identity.values()):
        raise TailnetEnvironmentError(f"{source} API health has incomplete build provenance")
    if (
        raw_identity != identity
        or len(identity["build_id"].encode("utf-16-le")) // 2 > 256
    ):
        raise TailnetEnvironmentError(f"{source} API health has noncanonical build provenance")
    build_time_match = _BUILD_TIME_PATTERN.fullmatch(identity["build_time"])
    if build_time_match is None:
        raise TailnetEnvironmentError(f"{source} API health has invalid build time")
    try:
        parsed_build_time = datetime.strptime(build_time_match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise TailnetEnvironmentError(f"{source} API health has invalid build time") from exc
    if parsed_build_time.year < 2000:
        raise TailnetEnvironmentError(f"{source} API health has invalid build time")
    liveness = payload.get("liveness")
    readiness = payload.get("readiness")
    if (
        payload.get("status") != "healthy"
        or not isinstance(liveness, Mapping)
        or liveness.get("alive") is not True
        or not isinstance(readiness, Mapping)
        or readiness.get("ready") is not True
    ):
        raise TailnetEnvironmentError(f"{source} API health is not live and ready")
    return identity


def _wait_for_healthy_api(
    url: str,
    *,
    timeout_seconds: float = 90.0,
) -> dict[str, object]:
    """Wait for HTTP and semantic API readiness after service startup."""
    deadline = time.monotonic() + timeout_seconds
    last_error: TailnetEnvironmentError | None = None
    while time.monotonic() < deadline:
        try:
            probe = _url_probe(url, expect_json=True)
            _api_build_identity(probe, source="local")
            return probe
        except TailnetEnvironmentError as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        raise last_error
    raise TailnetEnvironmentError(f"selected API did not become healthy at {url}")


def _git_revision(root: Path) -> str:
    resolved_root = root.resolve()
    top_level = Path(
        _run(["git", "-C", str(resolved_root), "rev-parse", "--show-toplevel"])
        .stdout.strip()
    ).resolve()
    if top_level != resolved_root:
        raise TailnetEnvironmentError("Tailnet selector source is not the canonical Git root")
    revision = _run(["git", "-C", str(resolved_root), "rev-parse", "HEAD"]).stdout.strip()
    if not _GIT_REVISION_PATTERN.fullmatch(revision):
        raise TailnetEnvironmentError("Tailnet selector source revision is invalid")
    dirty = _run([
        "git", "-C", str(resolved_root), "status", "--porcelain=v1", "--untracked-files=all"
    ]).stdout.strip()
    if dirty:
        raise TailnetEnvironmentError("Tailnet selector source is not clean; uncommitted changes exist")
    return revision


def _validate_canonical_environment_root(root: Path, environment: str) -> str:
    normalized = str(environment).strip().lower()
    if normalized not in SUPPORTED_ENVIRONMENTS:
        raise TailnetEnvironmentError("Tailnet environment must be development or production")
    branch = "test" if normalized == "development" else "main"
    resolved = root.resolve()
    revision = _git_revision(resolved)
    actual_branch = _run(
        ["git", "-C", str(resolved), "symbolic-ref", "--quiet", "--short", "HEAD"]
    ).stdout.strip()
    if actual_branch != branch:
        raise TailnetEnvironmentError(
            f"canonical {normalized} source must be branch {branch}, found {actual_branch or 'detached'}"
        )
    remote_revision = _run(
        ["git", "-C", str(resolved), "rev-parse", f"origin/{branch}"]
    ).stdout.strip()
    if revision != remote_revision:
        raise TailnetEnvironmentError(
            f"canonical {normalized} source does not exactly match origin/{branch}"
        )
    return revision


def _canonical_environment_root(environment: str) -> Path:
    normalized = str(environment).strip().lower()
    if normalized == "development":
        return CANONICAL_DEVELOPMENT_ROOT.resolve()
    if normalized == "production":
        return CANONICAL_PRODUCTION_ROOT.resolve()
    raise TailnetEnvironmentError("Tailnet environment must be development or production")


def _pid_report_for_pids(pids: list[int]) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for pid in sorted(set(pids)):
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
                "argv": _process_argv(pid),
                "executable": (
                    str(executable) if (executable := _process_executable(pid)) is not None else None
                ),
                "cgroup": cgroup,
                "build_revision": (
                    _pid_environment_value(pid, "VITE_BMS_BUILD_SHA")
                    or _pid_environment_value(pid, "BMS_BUILD_SHA")
                ),
            }
        )
    return reports


def _pid_report(port: int) -> list[dict[str, object]]:
    return _pid_report_for_pids(listener_pids(port))


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


def _process_argv(pid: int) -> list[str]:
    try:
        return [
            item.decode("utf-8", errors="strict")
            for item in (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            if item
        ]
    except (OSError, UnicodeDecodeError):
        return []


def _process_executable(pid: int) -> Path | None:
    try:
        return (Path("/proc") / str(pid) / "exe").resolve(strict=True)
    except OSError:
        return None


def _process_in_exact_container_cgroup(cgroup: object, container_id: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        return False
    expected = {
        f"/docker/{container_id}",
        f"/system.slice/docker-{container_id}.scope",
    }
    lines = [line for line in str(cgroup).splitlines() if line]
    matches = [re.fullmatch(r"[0-9]+:[^:]*:(/.*)", line) for line in lines]
    if not lines or any(match is None for match in matches):
        return False
    paths = [match.group(1) for match in matches if match is not None]
    return any(path in expected for path in paths) and all(
        path == "/" or path in expected for path in paths
    )


def _process_in_exact_systemd_unit(cgroup: object, service: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", service):
        return False
    pattern = re.compile(
        rf"^/user\.slice/user-(\d+)\.slice/user@\1\.service/app\.slice/{re.escape(service)}$"
    )
    lines = [line for line in str(cgroup).splitlines() if line]
    matches = [re.fullmatch(r"[0-9]+:[^:]*:(/.*)", line) for line in lines]
    if not lines or any(match is None for match in matches):
        return False
    paths = [match.group(1) for match in matches if match is not None]
    return any(pattern.fullmatch(path) for path in paths) and all(
        path == "/" or pattern.fullmatch(path) for path in paths
    )


def _trusted_node_executables() -> set[Path]:
    trusted: set[Path] = set()
    for candidate in (Path("/usr/bin/node"), Path("/usr/bin/nodejs")):
        try:
            trusted.add(candidate.resolve(strict=True))
        except OSError:
            continue
    return trusted


def _dev_frontend_matches_root(
    spec: EnvironmentSpec,
    root: Path,
    *,
    reports: list[dict[str, object]] | None = None,
) -> bool:
    expected = str((root / "platform" / "frontend").resolve())
    revision = _git_revision(root)
    if _listener_bind_addresses(spec.frontend_port) != {"127.0.0.1"}:
        return False
    if reports is None:
        reports = _exclusive_listener_reports(spec.frontend_port)
    if not reports:
        return False
    for report in reports:
        pid = report.get("pid")
        raw_argv = report.get("argv")
        argv = raw_argv if isinstance(raw_argv, list) and all(isinstance(item, str) for item in raw_argv) else []
        raw_executable = report.get("executable")
        executable = Path(raw_executable).resolve() if isinstance(raw_executable, str) else None
        expected_vite = (Path(expected) / "node_modules" / "vite" / "bin" / "vite.js").resolve()
        if (
            report.get("cwd") != expected
            or not isinstance(pid, int)
            or _pid_environment_value(pid, "VITE_BMS_BUILD_SHA") != revision
            or report.get("build_revision") != revision
            or executable is None
            or executable not in _trusted_node_executables()
            or len(argv) != 6
            or Path(argv[1]).resolve() != expected_vite
            or argv[2:] != ["--host", "127.0.0.1", "--port", str(spec.frontend_port)]
            or not _process_in_exact_systemd_unit(report.get("cgroup", ""), FRONTEND_SERVICE)
        ):
            return False
    return True


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


def _install_operator_development_frontend(root: Path, mutation_ledger: set[str] | None = None) -> None:
    units = render_user_units(project_root=root, runtime_mode=DEV_RUNTIME_MODE)
    frontend = units[FRONTEND_SERVICE]
    dev_api_target = f"http://127.0.0.1:{runtime_api_port(DEV_RUNTIME_MODE, project_root=root)}"
    expected_line = f"Environment=BMS_DEV_API_PROXY_TARGET={dev_api_target}"
    if expected_line not in frontend:
        raise TailnetEnvironmentError("could not render the canonical development frontend proxy contract")
    revision = _git_revision(root)
    build_identity = git_build_identity(root)
    build_time = build_identity["build_time"]
    build_id = build_identity["build_id"]
    home_line = f"Environment=BMS_HOME={root}"
    required_identity_lines = (
        home_line,
        f"Environment=VITE_BMS_BUILD_SHA={revision}",
        f"Environment=VITE_BMS_BUILD_ID={build_id}",
        f"Environment=VITE_BMS_BUILD_TIME={build_time}",
    )
    if any(line not in frontend for line in required_identity_lines):
        raise TailnetEnvironmentError("development frontend unit has no exact canonical source identity")
    if mutation_ledger is not None:
        mutation_ledger.add("frontend_files")
    _atomic_write(_host_user_systemd_dir() / FRONTEND_SERVICE, frontend)
    _atomic_write(
        _host_user_systemd_dir() / f"{FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf",
        "[Service]\n"
        f"Environment=BMS_HOME={root}\n"
        f"Environment=BMS_DEV_API_PROXY_TARGET={dev_api_target}\n"
        f"Environment=VITE_BMS_BUILD_SHA={revision}\n"
        f"Environment=VITE_BMS_BUILD_ID={build_id}\n"
        f"Environment=VITE_BMS_BUILD_TIME={build_time}\n"
        f"WorkingDirectory={root}/platform/frontend\n"
        "ExecStartPre=\n"
        f"ExecStartPre=/usr/bin/sh -c 'test \"$BMS_DEV_API_PROXY_TARGET\" = \"{dev_api_target}\"'\n"
        f"ExecStartPre=/usr/bin/env python3 {root}/scripts/rotate_biomodstack_logs.py\n"
        "ExecStart=\n"
        f"ExecStart=/usr/bin/node {root}/platform/frontend/node_modules/vite/bin/vite.js "
        "--host 127.0.0.1 --port 5173\n",
    )
    daemon_reload(project_root=root)


def _adapter_matches_root(root: Path) -> bool:
    expected = str((root / "platform" / "api").resolve())
    revision = _git_revision(root)
    reports = _exclusive_listener_reports(8001)
    if not reports:
        return False
    for report in reports:
        pid = report.get("pid")
        if not (
            report.get("cwd") == expected
            and isinstance(pid, int)
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_SOURCE_REVISION") == revision
        ):
            return False
    return True


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


def _install_adapter_control_policy(
    root: Path,
    runtime_revision: str,
    mutation_ledger: set[str] | None = None,
) -> str:
    login = _tailnet_owner_login()
    revision = _git_revision(root)
    if not _GIT_REVISION_PATTERN.fullmatch(runtime_revision):
        raise TailnetEnvironmentError("managed API runtime revision is malformed")
    api_image_id = _managed_image_id("BMS_MANAGED_API_IMAGE_ID", MANAGED_API_IMAGE_ID)
    web_image_id = _managed_image_id("BMS_MANAGED_WEB_IMAGE_ID", MANAGED_WEB_IMAGE_ID)
    dropin = _host_user_systemd_dir() / f"{WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    if mutation_ledger is not None:
        mutation_ledger.add("adapter_files")
    _atomic_write(
        dropin,
        "[Service]\n"
        f"Environment=BMS_HOME={root}\n"
        f"Environment=BMS_TAILNET_CONTROL_SOURCE_REVISION={revision}\n"
        f"Environment=BMS_BUILD_SHA={runtime_revision}\n"
        f"Environment=BMS_MANAGED_API_IMAGE_ID={api_image_id}\n"
        f"Environment=BMS_MANAGED_WEB_IMAGE_ID={web_image_id}\n"
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


def _adapter_identity_policy_matches(
    root: Path,
    login: str,
    *,
    runtime_revision: str,
    reports: list[dict[str, object]] | None = None,
) -> bool:
    addresses = _listener_bind_addresses(8001)
    if addresses not in ({"127.0.0.1"}, {"::1"}, {"127.0.0.1", "::1"}):
        return False
    expected_cwd = str((root / "platform" / "api").resolve())
    expected_revision = _git_revision(root)
    expected_python = (root / "platform" / "api" / ".venv" / "bin" / "python").resolve()
    expected_uvicorn = (root / "platform" / "api" / ".venv" / "bin" / "uvicorn").resolve()
    expected_args = [
        "workflow_adapter_app:app",
        "--port", "8001",
        "--host", "127.0.0.1",
        "--no-proxy-headers",
        "--no-access-log",
    ]
    if reports is None:
        reports = _exclusive_listener_reports(8001)
    if not reports:
        return False
    for report in reports:
        pid = report.get("pid")
        raw_argv = report.get("argv")
        argv = raw_argv if isinstance(raw_argv, list) and all(isinstance(item, str) for item in raw_argv) else []
        raw_executable = report.get("executable")
        executable = Path(raw_executable).resolve() if isinstance(raw_executable, str) else None
        if not (
            isinstance(pid, int)
            and report.get("cwd") == expected_cwd
            and executable == expected_python
            and len(argv) == 9
            and Path(argv[0]).resolve() == expected_python
            and Path(argv[1]).resolve() == expected_uvicorn
            and argv[2:] == expected_args
            and report.get("build_revision") == runtime_revision
            and _process_in_exact_systemd_unit(report.get("cgroup", ""), WORKFLOW_ADAPTER_SERVICE)
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_SOURCE_REVISION") == expected_revision
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS") == login
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS") == "127.0.0.1,::1"
            and _pid_environment_value(pid, "BMS_WORKFLOW_ADAPTER_BIND_HOST") == "127.0.0.1"
        ):
            return False
    return True


def _adapter_control_policy_matches(login: str) -> bool:
    if _listener_bind_addresses(8001) not in ({"127.0.0.1"}, {"::1"}, {"127.0.0.1", "::1"}):
        return False
    reports = _exclusive_listener_reports(8001)
    pids: list[int] = []
    for report in reports:
        pid = report.get("pid")
        if isinstance(pid, int):
            pids.append(pid)
    pids.sort()
    if not pids:
        return False
    for pid in pids:
        if not (
            _pid_environment_value(pid, "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS") == login
            and _pid_environment_value(pid, "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS") == "127.0.0.1,::1"
            and _pid_environment_value(pid, "BMS_WORKFLOW_ADAPTER_BIND_HOST") == "127.0.0.1"
        ):
            return False
    return True


def _wait_for_adapter_policy(
    root: Path,
    login: str,
    runtime_revision: str,
    timeout_seconds: float = 30.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _adapter_identity_policy_matches(root, login, runtime_revision=runtime_revision):
            return True
        time.sleep(0.25)
    return False


def ensure_tailnet_control_policy(root: Path = CANONICAL_DEVELOPMENT_ROOT) -> dict[str, object]:
    """Synchronize the Development-owned selector policy outside selector requests.

    This is called by the global Tailnet oneshot before Development's launch
    surface is accepted. An already-correct adapter is left untouched; a stale
    active adapter is restarted from the newly installed canonical policy.
    """
    canonical_root = root.resolve()
    _validate_canonical_environment_root(canonical_root, "development")
    revision = _git_revision(canonical_root)
    login = _install_adapter_control_policy(canonical_root, revision)
    daemon_reload(project_root=canonical_root)
    restarted = False
    if service_is_active(WORKFLOW_ADAPTER_SERVICE, project_root=canonical_root):
        if not _adapter_identity_policy_matches(
            canonical_root, login, runtime_revision=revision
        ):
            run_systemctl(
                "restart", WORKFLOW_ADAPTER_SERVICE, project_root=canonical_root
            )
            restarted = True
            if not _wait_for_adapter_policy(canonical_root, login, revision, timeout_seconds=90.0):
                raise TailnetEnvironmentError(
                    "workflow adapter did not restart from canonical authenticated source"
                )
    return {
        "source_root": str(canonical_root),
        "source_revision": revision,
        "allowed_login": login,
        "adapter_restarted": restarted,
    }


def _wait_for_development_frontend(spec: EnvironmentSpec, root: Path, timeout_seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _dev_frontend_matches_root(spec, root):
            return True
        time.sleep(0.25)
    return False


def _start_selected_environment(spec: EnvironmentSpec, root: Path) -> set[str]:
    # Both views use the existing managed API/DB. Selection must not rebuild or
    # restart it, nor start the unselected environment. Require the shared API
    # (and immutable production web, when selected) to be healthy first.
    _wait_for_healthy_api(spec.api_health_url)
    # The authenticated Tailnet selector is an independently owned control lane.
    # Always keep it on canonical Development source, regardless of which runtime
    # is being selected or whether Development's API revision temporarily lags
    # its live source. A selector request must never restart its own process.
    control_root = CANONICAL_DEVELOPMENT_ROOT.resolve()
    control_revision = _git_revision(control_root)
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        _url_probe(spec.frontend_url)
        _validated_production_tailnet_proxy(root)
        _url_probe(
            f"http://127.0.0.1:{PRODUCTION_TAILNET_PROXY_PORT}/",
            expected_final_url=f"http://127.0.0.1:{PRODUCTION_TAILNET_PROXY_PORT}/bms/",
        )
    mutations: set[str] = set()
    try:
        # Everything below this boundary can mutate service files or process state.
        allowed_login = _install_adapter_control_policy(
            control_root, control_revision, mutations
        )
        if not _adapter_identity_policy_matches(
            control_root, allowed_login, runtime_revision=control_revision
        ):
            adapter_unit = _host_user_systemd_dir() / WORKFLOW_ADAPTER_SERVICE
            if not adapter_unit.is_file():
                raise TailnetEnvironmentError("managed workflow-adapter systemd unit is not installed")
            mutations.add("adapter_service")
            daemon_reload(project_root=control_root)
            run_systemctl(
                "restart", WORKFLOW_ADAPTER_SERVICE, project_root=control_root
            )
            if not _wait_for_adapter_policy(
                control_root, allowed_login, control_revision
            ):
                raise TailnetEnvironmentError("workflow adapter did not restart from canonical authenticated source")
        if spec.runtime_mode == DEV_RUNTIME_MODE:
            _install_operator_development_frontend(root, mutations)
            if not _dev_frontend_matches_root(spec, root):
                mutations.add("frontend_service")
                run_systemctl("reset-failed", FRONTEND_SERVICE, project_root=root)
                run_systemctl("restart", FRONTEND_SERVICE, project_root=root)
        wait_for_http(spec.frontend_url)
        wait_for_http(spec.api_health_url)
        _url_probe(spec.frontend_url)
        _wait_for_healthy_api(spec.api_health_url)
        if spec.runtime_mode == DEV_RUNTIME_MODE and not _wait_for_development_frontend(spec, root):
            raise TailnetEnvironmentError(
                f"development frontend listener is not owned by {root / 'platform' / 'frontend'}"
            )
    except Exception as exc:
        setattr(exc, "service_ownership_mutations", frozenset(mutations))
        raise
    return mutations


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
        host_config = item.get("HostConfig", {})
        raw_mounts = item.get("Mounts", [])
        mounts = sorted(
            (
                {
                    "type": str(mount.get("Type", "")),
                    "source": str(mount.get("Source", "")),
                    "destination": str(mount.get("Destination", "")),
                    "mode": str(mount.get("Mode", "")),
                    "rw": mount.get("RW"),
                    "propagation": str(mount.get("Propagation", "")),
                }
                for mount in raw_mounts
                if isinstance(mount, Mapping)
            ),
            key=lambda mount: (mount["destination"], mount["source"]),
        ) if isinstance(raw_mounts, list) else []
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
                "readonly_rootfs": bool(host_config.get("ReadonlyRootfs", False))
                if isinstance(host_config, Mapping) else False,
                "mounts": mounts,
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
        "docker", "exec", "--privileged", "--user", "0:0",
        container_name, "/bin/sh", "-ec", script, "--", str(port),
    ])
    return sorted({int(line) for line in result.stdout.splitlines() if line.isdigit()})


def _container_listener_inodes(container_name: str, port: int) -> list[int]:
    script = r'''port_hex=$(printf '%04X' "$1")
inodes=$(awk -v suffix=":$port_hex" '$4 == "0A" && substr($2, length($2)-4) == suffix {print $10}' /proc/net/tcp /proc/net/tcp6 2>/dev/null)
for proc in /proc/[0-9]*; do
  for fd in "$proc"/fd/*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    for inode in $inodes; do
      if [ "$target" = "socket:[$inode]" ]; then printf '%s\n' "$inode"; fi
    done
  done
done'''
    result = _run([
        "docker", "exec", "--privileged", "--user", "0:0",
        container_name, "/bin/sh", "-ec", script, "--", str(port),
    ])
    return sorted({int(line) for line in result.stdout.splitlines() if line.isdigit()})


def _host_listener_inodes(port: int) -> list[int]:
    port_hex = f"{port:04X}"
    inodes: set[int] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            if fields[1].rsplit(":", 1)[-1].upper() != port_hex:
                continue
            try:
                inodes.add(int(fields[9]))
            except ValueError:
                continue
    return sorted(inodes)


def _host_listener_inode_owners(inodes: list[int]) -> dict[int, list[int]]:
    if not inodes:
        return {}
    helper = r'''import os, sys
wanted = {int(value) for value in sys.argv[1:]}
for proc_entry in os.scandir('/host-proc'):
    if not proc_entry.name.isdigit() or not proc_entry.is_dir(follow_symlinks=False):
        continue
    fd_root = os.path.join(proc_entry.path, 'fd')
    try:
        fd_entries = os.scandir(fd_root)
    except OSError:
        continue
    with fd_entries:
        for fd_entry in fd_entries:
            if not fd_entry.name.isdigit() or not fd_entry.is_symlink():
                continue
            try:
                target = os.readlink(fd_entry.path)
            except OSError:
                continue
            if not target.startswith('socket:[') or not target.endswith(']'):
                continue
            raw_inode = target[8:-1]
            if raw_inode.isdigit() and int(raw_inode) in wanted:
                print(f'{proc_entry.name} {raw_inode}')
'''
    result = _run([
        "docker", "run", "--rm", "--pull=never", "--network=none", "--read-only",
        "--privileged", "--user", "0:0",
        "--mount", "type=bind,src=/proc,dst=/host-proc,readonly",
        "--entrypoint", "/usr/local/bin/python3.10",
        HOST_PROC_HELPER_IMAGE,
        "-c", helper, *(str(inode) for inode in inodes),
    ])
    owners: dict[int, set[int]] = {inode: set() for inode in inodes}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            raise TailnetEnvironmentError("host listener ownership helper returned invalid output")
        pid, inode = (int(field) for field in fields)
        if inode not in owners:
            raise TailnetEnvironmentError("host listener ownership helper returned an unexpected inode")
        owners[inode].add(pid)
    return {inode: sorted(pids) for inode, pids in owners.items()}


def _exclusive_listener_reports(port: int) -> list[dict[str, object]]:
    reports = _pid_report(port)
    report_pid_set: set[int] = set()
    for report in reports:
        pid = report.get("pid")
        if isinstance(pid, int) and pid > 0:
            report_pid_set.add(pid)
    report_pids = sorted(report_pid_set)
    inodes = _host_listener_inodes(port)
    owners = _host_listener_inode_owners(inodes)
    owner_pids = sorted({pid for pids in owners.values() for pid in pids})
    if (
        not reports
        or not inodes
        or any(not owners.get(inode) for inode in inodes)
        or report_pids != owner_pids
    ):
        return []
    return reports


def _host_listener_closure(port: int) -> dict[str, object]:
    # Bind reports, socket inodes and complete owners to one stable observation.
    # A replacement that preserves a PID but changes the socket inode must not be
    # combined with the prior process identity.
    for _attempt in range(1):
        before_inodes = _host_listener_inodes(port)
        before_owners = _host_listener_inode_owners(before_inodes)
        before_addresses = sorted(_listener_bind_addresses(port))
        owner_pids = sorted({pid for pids in before_owners.values() for pid in pids})
        before_reports = _pid_report_for_pids(owner_pids)
        after_inodes = _host_listener_inodes(port)
        after_owners = _host_listener_inode_owners(after_inodes)
        after_addresses = sorted(_listener_bind_addresses(port))
        after_owner_pids = sorted({pid for pids in after_owners.values() for pid in pids})
        after_reports = _pid_report_for_pids(after_owner_pids)
        report_pids: list[int] = []
        for report in after_reports:
            report_pid = report.get("pid")
            if isinstance(report_pid, int):
                report_pids.append(report_pid)
        report_pids.sort()
        if (
            before_inodes
            and before_inodes == after_inodes
            and before_owners == after_owners
            and before_addresses == after_addresses
            and owner_pids == after_owner_pids == report_pids
            and before_reports == after_reports
            and all(after_owners.get(inode) for inode in after_inodes)
        ):
            return {
                "port": port,
                "bind_addresses": after_addresses,
                "listener_inodes": after_inodes,
                "listener_inode_owners": after_owners,
                "listener_pids": report_pids,
                "listener_reports": after_reports,
            }
    raise TailnetEnvironmentError(f"listener ownership closure is unstable for port {port}")


def _validated_workflow_adapter_listener(
    root: Path, runtime_revision: str
) -> dict[str, object]:
    closure = _host_listener_closure(8001)
    reports = closure.get("listener_reports")
    if not isinstance(reports, list):
        raise TailnetEnvironmentError("listener ownership reports are unavailable")
    login = _tailnet_owner_login()
    if not _adapter_identity_policy_matches(
        root, login, runtime_revision=runtime_revision, reports=reports
    ):
        raise TailnetEnvironmentError("workflow adapter listener lost exact authenticated service ownership")
    confirmed = _host_listener_closure(8001)
    confirmed_reports = confirmed.get("listener_reports")
    if (
        confirmed != closure
        or not isinstance(confirmed_reports, list)
        or not _adapter_identity_policy_matches(
            root,
            login,
            runtime_revision=runtime_revision,
            reports=confirmed_reports,
        )
    ):
        raise TailnetEnvironmentError("workflow adapter listener changed during validation")
    closure = confirmed
    reports = confirmed_reports
    api_root = root / "platform" / "api"
    canonical_reports: list[dict[str, object]] = []
    for report in reports:
        if not isinstance(report, Mapping):
            raise TailnetEnvironmentError("workflow adapter listener report is malformed")
        argv = report.get("argv")
        if not isinstance(argv, list):
            raise TailnetEnvironmentError("workflow adapter listener argv is unavailable")
        canonical = dict(report)
        canonical["executable"] = str(api_root / ".venv" / "bin" / "python")
        canonical["cmdline"] = " ".join(str(item) for item in argv)
        canonical_reports.append(canonical)
    closure["listener_reports"] = canonical_reports
    closure.update({
        "systemd_service": WORKFLOW_ADAPTER_SERVICE,
        "source_root": str(root),
        "source_revision": _git_revision(root),
    })
    return closure


def _validated_development_frontend_listener(
    spec: EnvironmentSpec,
    root: Path,
) -> dict[str, object]:
    closure = _host_listener_closure(spec.frontend_port)
    reports = closure.get("listener_reports")
    if not isinstance(reports, list):
        raise TailnetEnvironmentError("listener ownership reports are unavailable")
    if not _dev_frontend_matches_root(spec, root, reports=reports):
        raise TailnetEnvironmentError("development frontend lost exact service ownership before receipt")
    confirmed = _host_listener_closure(spec.frontend_port)
    confirmed_reports = confirmed.get("listener_reports")
    if (
        confirmed != closure
        or not isinstance(confirmed_reports, list)
        or not _dev_frontend_matches_root(spec, root, reports=confirmed_reports)
    ):
        raise TailnetEnvironmentError("development frontend listener changed during validation")
    closure = confirmed
    closure.update({
        "systemd_service": FRONTEND_SERVICE,
        "source_root": str((root / "platform" / "frontend").resolve()),
        "source_revision": _git_revision(root),
    })
    return closure


def _validated_development_api_listener(
    spec: EnvironmentSpec,
    root: Path,
    runtime_revision: str,
) -> dict[str, object]:
    closure = _host_listener_closure(spec.api_port)
    reports = closure.get("listener_reports")
    if not isinstance(reports, list) or not reports:
        raise TailnetEnvironmentError("development API listener ownership reports are unavailable")
    expected_cwd = str((root / "platform" / "api").resolve())
    expected_python = (root / "platform" / "api" / ".venv" / "bin" / "python").resolve()
    expected_state = str((Path.home() / ".biomodstack-dev").resolve())
    expected_db = str((Path(expected_state) / "biomodstack.db").resolve())
    for report in reports:
        pid = report.get("pid") if isinstance(report, Mapping) else None
        executable = report.get("executable") if isinstance(report, Mapping) else None
        if not (
            isinstance(pid, int)
            and report.get("cwd") == expected_cwd
            and isinstance(executable, str)
            and Path(executable).resolve() == expected_python
            and report.get("build_revision") == runtime_revision
            and _process_in_exact_systemd_unit(report.get("cgroup", ""), API_SERVICE)
            and _pid_environment_value(pid, "BMS_HOME") == str(root)
            and _pid_environment_value(pid, "BMS_STATE_DIR") == expected_state
            and _pid_environment_value(pid, "BMS_DB_PATH") == expected_db
        ):
            raise TailnetEnvironmentError("development API listener lost exact isolated service ownership")
    confirmed = _host_listener_closure(spec.api_port)
    if confirmed != closure:
        raise TailnetEnvironmentError("development API listener changed during validation")
    closure.update({
        "systemd_service": API_SERVICE,
        "source_root": expected_cwd,
        "source_revision": runtime_revision,
        "state_root": expected_state,
        "database_path": expected_db,
    })
    return closure


def _container_listener_pid_map(
    container_id: str, container_pids: list[int]
) -> list[dict[str, int]]:
    """Map each listener namespace PID to its exact host PID."""
    wanted = set(container_pids)
    mapped: dict[int, int] = {}
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            host_pid = int(proc.name)
            if not _process_in_exact_container_cgroup(_process_cgroup(host_pid), container_id):
                continue
            status_text = (proc / "status").read_text(encoding="utf-8", errors="replace")
            namespace_line = next(
                line for line in status_text.splitlines() if line.startswith("NSpid:")
            )
            namespace_pids = [int(value) for value in namespace_line.split()[1:]]
            if namespace_pids and namespace_pids[-1] in wanted:
                mapped[namespace_pids[-1]] = host_pid
        except (OSError, StopIteration, ValueError):
            continue
    if set(mapped) != wanted:
        return []
    return [
        {"container_pid": container_pid, "host_pid": mapped[container_pid]}
        for container_pid in sorted(mapped)
    ]


def _host_owner_pid_map(
    container_id: str, host_pids: list[int]
) -> list[dict[str, int]]:
    """Map every authoritative host socket owner into the container PID namespace."""
    mapped: list[dict[str, int]] = []
    for host_pid in sorted(host_pids):
        if not _process_in_exact_container_cgroup(_process_cgroup(host_pid), container_id):
            return []
        try:
            status_text = (Path("/proc") / str(host_pid) / "status").read_text(
                encoding="utf-8", errors="replace"
            )
            namespace_line = next(
                line for line in status_text.splitlines() if line.startswith("NSpid:")
            )
            namespace_pids = [int(value) for value in namespace_line.split()[1:]]
        except (OSError, StopIteration, ValueError):
            return []
        if (
            len(namespace_pids) < 2
            or namespace_pids[0] != host_pid
            or namespace_pids[-1] <= 0
        ):
            return []
        mapped.append({"container_pid": namespace_pids[-1], "host_pid": host_pid})
    mapped.sort(key=lambda item: item["container_pid"])
    if (
        len({item["container_pid"] for item in mapped}) != len(mapped)
        or [item["host_pid"] for item in mapped] != sorted(host_pids)
    ):
        return []
    return mapped


def _container_process_identities(
    container_name: str,
    container_pids: list[int],
    *,
    expected_uid: int,
) -> dict[int, dict[str, object]]:
    if not container_pids:
        return {}
    script = (
        'for p do '
        'printf "%s|%s|" "$p" "$(readlink "/proc/$p/exe" | base64 -w0)"; '
        'printf "%s|" "$(readlink "/proc/$p/cwd" | base64 -w0)"; '
        'printf "%s|" "$(base64 -w0 "/proc/$p/cmdline")"; '
        'printf "%s|" "$(grep "^PPid:" "/proc/$p/status" | base64 -w0)"; '
        'printf "%s\\n" "$(grep "^Uid:" "/proc/$p/status" | base64 -w0)"; '
        'done'
    )
    result = _run([
        "docker", "exec", "-u", str(expected_uid), container_name,
        "sh", "-c", script, "--", *(str(pid) for pid in container_pids),
    ])
    lines = result.stdout.strip().splitlines()
    if len(lines) != len(container_pids):
        raise TailnetEnvironmentError(
            f"container process identity batch is incomplete: {container_name}"
        )
    identities: dict[int, dict[str, object]] = {}
    for line in lines:
        parts = line.split("|")
        if len(parts) != 6 or any(not part for part in parts):
            raise TailnetEnvironmentError(
                f"container process identity batch is unreadable: {container_name}"
            )
        try:
            container_pid = int(parts[0])
            executable = base64.b64decode(parts[1], validate=True).decode().strip()
            cwd = base64.b64decode(parts[2], validate=True).decode().strip()
            raw_cmdline = base64.b64decode(parts[3], validate=True)
            parent_line = base64.b64decode(parts[4], validate=True).decode().strip()
            uid_line = base64.b64decode(parts[5], validate=True).decode().strip()
            argv = [item.decode() for item in raw_cmdline.split(b"\0") if item]
            parent_pid = int(parent_line.split()[1])
            uids = [int(value) for value in uid_line.split()[1:]]
        except (UnicodeDecodeError, ValueError, IndexError, binascii.Error) as exc:
            raise TailnetEnvironmentError(
                f"container process identity batch is malformed: {container_name}"
            ) from exc
        if (
            container_pid not in container_pids
            or container_pid in identities
            or not executable.startswith("/")
            or not cwd.startswith("/")
            or not argv
            or len(uids) != 4
            or any(uid != expected_uid for uid in uids)
            or parent_pid < 0
        ):
            raise TailnetEnvironmentError(
                f"container process identity batch is invalid: {container_name}:{container_pid}"
            )
        identities[container_pid] = {
            "container_pid": container_pid,
            "parent_container_pid": parent_pid,
            "executable": executable,
            "argv": argv,
            "cwd": cwd,
            "uid": expected_uid,
        }
    if set(identities) != set(container_pids):
        raise TailnetEnvironmentError(
            f"container process identity batch changed during capture: {container_name}"
        )
    return identities


def _container_process_reports(
    container_name: str,
    container_id: str,
    host_pids: list[int],
) -> list[dict[str, object]]:
    pid_map = _host_owner_pid_map(container_id, host_pids)
    if len(pid_map) != len(host_pids):
        return []
    uid_groups: dict[int, list[int]] = {}
    for mapping in pid_map:
        container_pid = mapping["container_pid"]
        expected_uid = (
            1000 if container_name == "biomodstack-api"
            else (0 if container_pid == 1 else 101)
        )
        uid_groups.setdefault(expected_uid, []).append(container_pid)
    identities: dict[int, dict[str, object]] = {}
    for expected_uid, container_pids in uid_groups.items():
        identities.update(_container_process_identities(
            container_name, container_pids, expected_uid=expected_uid
        ))
    reports = [{
        "pid": mapping["host_pid"],
        "cgroup": _process_cgroup(mapping["host_pid"]),
        **identities[mapping["container_pid"]],
    } for mapping in pid_map]
    reports.sort(key=lambda report: cast(int, report["pid"]))
    return reports


def _valid_container_process_roles(
    container_name: str,
    reports: list[dict[str, object]],
) -> bool:
    if not reports:
        return False
    for report in reports:
        container_pid = report.get("container_pid")
        if container_name == "biomodstack-api":
            expected = (
                container_pid == 1
                and report.get("parent_container_pid") == 0
                and report.get("executable") == "/usr/local/bin/python3.10"
                and report.get("argv") == [
                    "/app/platform/api/.venv/bin/python",
                    "/app/platform/api/.venv/bin/uvicorn",
                    "main:app", "--host", "127.0.0.1", "--port", "8000",
                ]
                and report.get("cwd") == "/app/platform/api"
                and report.get("uid") == 1000
            )
        elif container_pid == 1:
            expected = (
                report.get("parent_container_pid") == 0
                and report.get("executable") == "/usr/sbin/nginx"
                and report.get("argv") == ["nginx: master process nginx -g daemon off;"]
                and report.get("cwd") == "/"
                and report.get("uid") == 0
            )
        else:
            expected = (
                report.get("parent_container_pid") == 1
                and report.get("executable") == "/usr/sbin/nginx"
                and report.get("argv") == ["nginx: worker process"]
                and report.get("cwd") == "/"
                and report.get("uid") == 101
            )
        if not expected:
            return False
    return True


def _container_listener_host_pids(container_id: str, container_pids: list[int]) -> list[int]:
    return sorted(
        item["host_pid"] for item in _container_listener_pid_map(container_id, container_pids)
    )


def _container_host_pids(container_name: str) -> list[int]:
    result = _run(["docker", "top", container_name, "-eo", "pid"])
    pids: set[int] = set()
    for line in result.stdout.splitlines()[1:]:
        value = line.strip()
        if not value.isdigit() or int(value) <= 0:
            raise TailnetEnvironmentError("production Tailnet proxy process inventory is invalid")
        pids.add(int(value))
    if not pids:
        raise TailnetEnvironmentError("production Tailnet proxy process inventory is empty")
    return sorted(pids)


def _validated_runtime_container_listener(
    runtime_report: Mapping[str, object],
    *,
    container_name: str,
    port: int,
) -> dict[str, object]:
    raw_containers = runtime_report.get("containers")
    if not isinstance(raw_containers, list):
        raise TailnetEnvironmentError("managed container runtime listener provenance is unavailable")
    item = next(
        (
            candidate
            for candidate in raw_containers
            if isinstance(candidate, Mapping) and candidate.get("name") == container_name
        ),
        None,
    )
    if not isinstance(item, Mapping):
        raise TailnetEnvironmentError(f"managed listener container is missing: {container_name}")
    container_id = str(item.get("container_id", ""))
    if not container_id:
        raise TailnetEnvironmentError(f"managed listener container identity is missing: {container_name}")
    if _listener_bind_addresses(port) != {"127.0.0.1"}:
        raise TailnetEnvironmentError(
            f"managed listener {container_name}:{port} is not bound exactly to IPv4 loopback"
        )

    reported_container_listener_pids = _container_listener_pids(container_name, port)
    container_listener_inodes = _container_listener_inodes(container_name, port)
    host_listener_inodes = _host_listener_inodes(port)
    if (
        not reported_container_listener_pids
        or not container_listener_inodes
        or host_listener_inodes != container_listener_inodes
    ):
        raise TailnetEnvironmentError(
            f"managed listener {container_name}:{port} is not exclusively owned by its container"
        )
    inode_owners = _host_listener_inode_owners(host_listener_inodes)
    all_owner_pids = sorted({pid for pids in inode_owners.values() for pid in pids})
    listener_pid_map = _host_owner_pid_map(container_id, all_owner_pids)
    container_listener_pids = [item["container_pid"] for item in listener_pid_map]
    host_listener_pids = [item["host_pid"] for item in listener_pid_map]
    expected_host_pids = item.get("host_pids")
    expected_process_reports = item.get("process_reports")
    container_host_pids = _container_host_pids(container_name)
    runtime_init_pid = item.get("pid")
    reports = [
        report for report in expected_process_reports
        if isinstance(report, Mapping) and report.get("pid") in all_owner_pids
    ] if isinstance(expected_process_reports, list) else []
    report_pid_map_matches = all(
        next(
            (
                report.get("container_pid") == mapping["container_pid"]
                for report in reports
                if report.get("pid") == mapping["host_pid"]
            ),
            False,
        )
        for mapping in listener_pid_map
    )
    second_reported_pids = _container_listener_pids(container_name, port)
    second_container_inodes = _container_listener_inodes(container_name, port)
    second_host_inodes = _host_listener_inodes(port)
    second_inode_owners = _host_listener_inode_owners(second_host_inodes)
    second_owner_pids = sorted({pid for pids in second_inode_owners.values() for pid in pids})
    second_listener_pid_map = _host_owner_pid_map(container_id, second_owner_pids)
    second_container_host_pids = _container_host_pids(container_name)
    second_process_reports = _container_process_reports(
        container_name, container_id, second_container_host_pids
    )
    stable_capture = (
        reported_container_listener_pids == second_reported_pids
        and container_listener_inodes == second_container_inodes
        and host_listener_inodes == second_host_inodes
        and inode_owners == second_inode_owners
        and all_owner_pids == second_owner_pids
        and listener_pid_map == second_listener_pid_map
        and container_host_pids == second_container_host_pids
        and expected_process_reports == second_process_reports
        and _listener_bind_addresses(port) == {"127.0.0.1"}
    )
    if (
        any(not inode_owners.get(inode) for inode in host_listener_inodes)
        or not listener_pid_map
        or reported_container_listener_pids != container_listener_pids
        or host_listener_pids != sorted(set(host_listener_pids))
        or host_listener_pids != all_owner_pids
        or not isinstance(expected_host_pids, list)
        or container_host_pids != expected_host_pids
        or not stable_capture
        or not isinstance(expected_process_reports, list)
        or not _valid_container_process_roles(container_name, expected_process_reports)
        or [report.get("pid") for report in reports] != all_owner_pids
        or not report_pid_map_matches
        or all_owner_pids != container_host_pids
        or [
            mapping["host_pid"]
            for mapping in listener_pid_map
            if mapping["container_pid"] == 1
        ] != [runtime_init_pid]
        or (
            container_name == "biomodstack-api"
            and (
                container_listener_pids != [1]
                or container_host_pids != [runtime_init_pid]
                or host_listener_pids != [runtime_init_pid]
            )
        )
        or len(host_listener_pids) != len(container_listener_pids)
        or not isinstance(runtime_init_pid, int)
        or runtime_init_pid not in container_host_pids
        or any(
            not _process_in_exact_container_cgroup(_process_cgroup(owner), container_id)
            for owner in all_owner_pids
        )
    ):
        raise TailnetEnvironmentError(
            f"managed listener {container_name}:{port} has an owner outside its validated container"
        )
    return {
        "container_name": container_name,
        "container_id": container_id,
        "port": port,
        "bind_addresses": ["127.0.0.1"],
        "container_listener_pids": container_listener_pids,
        "listener_pid_map": listener_pid_map,
        "host_listener_pids": host_listener_pids,
        "listener_inodes": host_listener_inodes,
        "listener_inode_owners": inode_owners,
        "container_host_pids": container_host_pids,
        "runtime_image_id": item.get("image_id", ""),
        "runtime_cmdline": item.get("cmdline", ""),
        "runtime_cwd": item.get("cwd", ""),
        "listener_reports": reports,
    }


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
    expected_mount = (
        len(mounts) == 1
        and isinstance(mounts[0], Mapping)
        and mounts[0].get("Type") == "bind"
        and Path(str(mounts[0].get("Source", ""))).resolve() == config
        and mounts[0].get("Destination") == "/etc/nginx/templates/default.conf.template"
        and mounts[0].get("RW") is False
    )
    if labels.get(PRODUCTION_TAILNET_PROXY_SHA_LABEL) != config_sha or not expected_mount:
        raise TailnetEnvironmentError("production Tailnet proxy does not match the reviewed config")
    host_config = item.get("HostConfig", {})
    expected_bind = f"{config}:/etc/nginx/templates/default.conf.template:ro"
    if (
        host_config.get("Binds") != [expected_bind]
        or host_config.get("Mounts") not in (None, [])
        or host_config.get("VolumesFrom") not in (None, [])
        or host_config.get("Tmpfs") != {"/var/cache/nginx": "", "/var/run": ""}
    ):
        raise TailnetEnvironmentError("production Tailnet proxy has unexpected runtime mounts")
    if (
        labels.get("com.biomodstack.tailnet-proxy-owner") != "compose.core-runtime"
        or labels.get("com.docker.compose.project") != "biomodstack-tailnet-control"
        or labels.get("com.docker.compose.service") != "tailnet-production-proxy"
        or host_config.get("RestartPolicy", {}).get("Name") != "unless-stopped"
        or host_config.get("ReadonlyRootfs") is not True
    ):
        raise TailnetEnvironmentError("production Tailnet proxy has no durable canonical Compose owner")
    if (
        host_config.get("Memory") != 256 * 1024 * 1024
        or host_config.get("PidsLimit") != 128
        or host_config.get("Ulimits") != [{"Name": "nofile", "Hard": 4096, "Soft": 4096}]
        or host_config.get("LogConfig") != {
            "Type": "json-file",
            "Config": {"max-file": "5", "max-size": "10m"},
        }
    ):
        raise TailnetEnvironmentError("production Tailnet proxy resource boundaries are not pinned")
    if (
        host_config.get("NetworkMode") != "host"
        or host_config.get("PidMode") not in (None, "")
        or item.get("State", {}).get("Running") is not True
    ):
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
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not _process_in_exact_container_cgroup(cgroup, container_id)
        or not command
    ):
        raise TailnetEnvironmentError("production Tailnet proxy process provenance is incomplete")
    reported_listener_pids_in_container = _container_listener_pids(
        PRODUCTION_TAILNET_PROXY_CONTAINER, PRODUCTION_TAILNET_PROXY_PORT
    )
    if not reported_listener_pids_in_container:
        raise TailnetEnvironmentError("production Tailnet proxy has no container-owned listener")
    container_listener_inodes = _container_listener_inodes(
        PRODUCTION_TAILNET_PROXY_CONTAINER, PRODUCTION_TAILNET_PROXY_PORT
    )
    host_listener_inodes = _host_listener_inodes(PRODUCTION_TAILNET_PROXY_PORT)
    if not container_listener_inodes or host_listener_inodes != container_listener_inodes:
        raise TailnetEnvironmentError(
            "production Tailnet proxy port has a listener outside the validated container"
        )
    inode_owners = _host_listener_inode_owners(host_listener_inodes)
    all_owner_pids = sorted({pid for pids in inode_owners.values() for pid in pids})
    listener_pid_map = _host_owner_pid_map(container_id, all_owner_pids)
    listener_pids_in_container = [item["container_pid"] for item in listener_pid_map]
    host_listener_pids = [item["host_pid"] for item in listener_pid_map]
    container_host_pids = _container_host_pids(PRODUCTION_TAILNET_PROXY_CONTAINER)
    process_reports = _container_process_reports(
        PRODUCTION_TAILNET_PROXY_CONTAINER, container_id, container_host_pids
    )
    listener_reports = [
        report for report in process_reports if report.get("pid") in all_owner_pids
    ]
    report_pid_map_matches = all(
        next(
            (
                report.get("container_pid") == mapping["container_pid"]
                for report in listener_reports
                if report.get("pid") == mapping["host_pid"]
            ),
            False,
        )
        for mapping in listener_pid_map
    )
    second_reported_pids = _container_listener_pids(
        PRODUCTION_TAILNET_PROXY_CONTAINER, PRODUCTION_TAILNET_PROXY_PORT
    )
    second_container_inodes = _container_listener_inodes(
        PRODUCTION_TAILNET_PROXY_CONTAINER, PRODUCTION_TAILNET_PROXY_PORT
    )
    second_host_inodes = _host_listener_inodes(PRODUCTION_TAILNET_PROXY_PORT)
    second_inode_owners = _host_listener_inode_owners(second_host_inodes)
    second_owner_pids = sorted({owner for owners in second_inode_owners.values() for owner in owners})
    second_pid_map = _host_owner_pid_map(container_id, second_owner_pids)
    second_container_host_pids = _container_host_pids(PRODUCTION_TAILNET_PROXY_CONTAINER)
    second_process_reports = _container_process_reports(
        PRODUCTION_TAILNET_PROXY_CONTAINER, container_id, second_container_host_pids
    )
    stable_capture = (
        reported_listener_pids_in_container == second_reported_pids
        and container_listener_inodes == second_container_inodes
        and host_listener_inodes == second_host_inodes
        and inode_owners == second_inode_owners
        and all_owner_pids == second_owner_pids
        and listener_pid_map == second_pid_map
        and container_host_pids == second_container_host_pids
        and process_reports == second_process_reports
        and _listener_bind_addresses(PRODUCTION_TAILNET_PROXY_PORT) == {"127.0.0.1"}
    )
    if (
        any(not inode_owners.get(inode) for inode in host_listener_inodes)
        or not listener_pid_map
        or reported_listener_pids_in_container != listener_pids_in_container
        or host_listener_pids != sorted(set(host_listener_pids))
        or host_listener_pids != all_owner_pids
        or not stable_capture
        or not _valid_container_process_roles(
            PRODUCTION_TAILNET_PROXY_CONTAINER, process_reports
        )
        or [report.get("pid") for report in listener_reports] != all_owner_pids
        or not report_pid_map_matches
        or all_owner_pids != container_host_pids
        or len(host_listener_pids) != len(listener_pids_in_container)
        or [
            mapping["host_pid"]
            for mapping in listener_pid_map
            if mapping["container_pid"] == 1
        ] != [pid]
        or pid not in container_host_pids
        or any(
            not _process_in_exact_container_cgroup(_process_cgroup(owner), container_id)
            for owner in container_host_pids
        )
    ):
        raise TailnetEnvironmentError(
            "production Tailnet proxy socket has an owner outside the validated container"
        )
    return {
        "container_id": container_id,
        "image": str(item.get("Config", {}).get("Image", "")),
        "image_id": image_id,
        "config_path": str(config),
        "config_sha256": config_sha,
        "listener_port": PRODUCTION_TAILNET_PROXY_PORT,
        "pid": pid,
        "listener_pids": host_listener_pids,
        "container_listener_pids": listener_pids_in_container,
        "listener_pid_map": listener_pid_map,
        "listener_inodes": host_listener_inodes,
        "listener_inode_owners": inode_owners,
        "container_host_pids": container_host_pids,
        "listener_reports": listener_reports,
        "cgroup": cgroup,
        "cmdline": command,
        "cwd": str(item.get("Config", {}).get("WorkingDir", "") or "/"),
    }


def _accepted_release_image_ids(root: Path, revision: str) -> dict[str, str] | None:
    """Read immutable image IDs from the accepted Production release receipt."""
    if root.resolve() != CANONICAL_PRODUCTION_ROOT.resolve():
        return None
    receipt_path = Path.home() / ".local" / "state" / "biomodstack" / "releases" / "known-good.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        build = receipt["build"]
        images = receipt["images"]
        accepted_revision = str(build["BMS_BUILD_SHA"])
        api_image = str(images["bms-api"])
        web_image = str(images["bms-web"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TailnetEnvironmentError("accepted Production image receipt is unavailable") from exc
    if accepted_revision != revision:
        raise TailnetEnvironmentError("accepted Production receipt revision does not match the runtime")
    for image_id in (api_image, web_image):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise TailnetEnvironmentError("accepted Production image receipt is malformed")
    return {"biomodstack-api": api_image, "biomodstack-web": web_image}


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
    expected_process_identity = {
        "biomodstack-api": (
            "/bin/sh -ec /app/platform/api/.venv/bin/python run_migrations.py && exec "
            "/app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000",
            "/app/platform/api",
        ),
        "biomodstack-web": ("/docker-entrypoint.sh nginx -g daemon off;", "/"),
    }
    expected_image_ids = _accepted_release_image_ids(root, next(iter(revisions))) or {
        "biomodstack-api": _managed_image_id("BMS_MANAGED_API_IMAGE_ID", MANAGED_API_IMAGE_ID),
        "biomodstack-web": _managed_image_id("BMS_MANAGED_WEB_IMAGE_ID", MANAGED_WEB_IMAGE_ID),
    }
    expected_mounts = {
        "biomodstack-api": [{
            "type": "bind",
            "source": "/mnt/BioModStack",
            "destination": "/var/lib/biomodstack",
            "mode": "rw",
            "rw": True,
            "propagation": "rprivate",
        }],
        "biomodstack-web": [],
    }
    if any(
        str(item.get("image_id", "")) != expected_image_ids[str(item.get("name", ""))]
        or (
            str(item.get("cmdline", "")),
            str(item.get("cwd", "")),
        ) != expected_process_identity[str(item.get("name", ""))]
        or item.get("mounts") != expected_mounts[str(item.get("name", ""))]
        or item.get("readonly_rootfs") is not False
        for item in selected
    ):
        raise TailnetEnvironmentError("managed container image/process identity is not pinned")
    if any(
        not isinstance(item.get("pid"), int)
        or int(item["pid"]) <= 0
        or not _process_in_exact_container_cgroup(
            item.get("cgroup", ""),
            str(item.get("container_id", "")),
        )
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
    validated_containers: list[dict[str, object]] = []
    for item in selected:
        name = str(item.get("name", ""))
        container_id = str(item.get("container_id", ""))
        init_pid = item.get("pid")
        host_pids = _container_host_pids(name)
        process_reports = _container_process_reports(name, container_id, host_pids)
        if (
            not isinstance(init_pid, int)
            or init_pid not in host_pids
            or (name == "biomodstack-api" and host_pids != [init_pid])
            or any(
                not _process_in_exact_container_cgroup(_process_cgroup(pid), container_id)
                for pid in host_pids
            )
            or not _valid_container_process_roles(name, process_reports)
            or _container_host_pids(name) != host_pids
            or _container_process_reports(name, container_id, host_pids) != process_reports
        ):
            raise TailnetEnvironmentError(
                f"managed container process inventory is inconsistent: {name}"
            )
        validated_containers.append({
            "name": name,
            "container_id": container_id,
            "revision": revision,
            "compose_working_dir": str(compose_root),
            "pid": init_pid,
            "cgroup": item.get("cgroup", ""),
            "image_id": item.get("image_id", ""),
            "cmdline": item.get("cmdline", ""),
            "cwd": item.get("cwd", ""),
            "host_pids": host_pids,
            "process_reports": process_reports,
            "readonly_rootfs": item.get("readonly_rootfs"),
            "mounts": item.get("mounts"),
        })
    return {
        "containers": validated_containers,
        "validated_revision": revision,
        "validated_compose_root": str(compose_root),
    }


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
    public_frontend = _url_probe(
        snapshot.origin + "/",
        expected_final_url=(
            snapshot.origin + "/bms/"
            if spec.runtime_mode == CONTAINER_RUNTIME_MODE
            else snapshot.origin + "/"
        ),
    )
    public_api = _url_probe(snapshot.origin + "/api/health", expect_json=True)
    local_api_build = _api_build_identity(local_api, source="local")
    public_api_build = _api_build_identity(public_api, source="Tailnet")
    if local_api_build != public_api_build:
        raise TailnetEnvironmentError(
            "local and Tailnet API build provenance disagree: "
            f"local={local_api_build}, Tailnet={public_api_build}"
        )
    managed_api_runtime: dict[str, object] | None = None
    managed_api_listener: dict[str, object] | None = None
    development_api_listener: dict[str, object] | None = None
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        managed_api_runtime = _validated_container_runtime(root, require_web=False)
        if managed_api_runtime.get("validated_revision") != local_api_build["revision"]:
            raise TailnetEnvironmentError(
                "API build provenance does not match the managed container revision"
            )
        managed_api_listener = _validated_runtime_container_listener(
            managed_api_runtime,
            container_name="biomodstack-api",
            port=spec.api_port,
        )
    else:
        development_api_listener = _validated_development_api_listener(
            spec, root, local_api_build["revision"]
        )
    production_runtime: dict[str, object] | None = None
    managed_frontend_listener: dict[str, object] | None = None
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        production_runtime = _validated_container_runtime(root, require_web=True)
        managed_frontend_listener = _validated_runtime_container_listener(
            production_runtime,
            container_name="biomodstack-web",
            port=spec.frontend_port,
        )
    adapter_root = CANONICAL_DEVELOPMENT_ROOT.resolve()
    adapter_revision = _git_revision(adapter_root)
    workflow_adapter_listener = _validated_workflow_adapter_listener(
        adapter_root, adapter_revision
    )
    development_frontend_listener: dict[str, object] | None = None
    if spec.runtime_mode == DEV_RUNTIME_MODE:
        development_frontend_listener = _validated_development_frontend_listener(spec, root)
    report: dict[str, object] = {
        "selected_environment": spec.environment,
        "runtime_mode": spec.runtime_mode,
        "runtime_target": spec.runtime_target,
        "project_root": str(root),
        "project_revision": local_api_build["revision"],
        "selector_revision": adapter_revision,
        "frontend_target": (
            spec.frontend_url.rstrip("/")
            if spec.environment == "development"
            else spec.frontend_url
        ),
        "api_health_target": spec.api_health_url,
        "serve_root_proxy": snapshot.root_proxy,
        "tailnet_origin": snapshot.origin,
        "serve_handlers": snapshot.handlers,
        "frontend_listeners": (
            managed_frontend_listener["listener_reports"]
            if managed_frontend_listener is not None
            else _pid_report(spec.frontend_port)
        ),
        "api_listeners": (
            managed_api_listener["listener_reports"]
            if managed_api_listener is not None
            else cast(dict[str, object], development_api_listener)["listener_reports"]
        ),
        "workflow_adapter_listener": workflow_adapter_listener,
        "health": {
            "local_frontend": local_frontend,
            "local_api": local_api,
            "tailnet_frontend": public_frontend,
            "tailnet_api": public_api,
        },
    }
    if managed_api_runtime is not None:
        report["managed_api_runtime"] = managed_api_runtime
    if managed_api_listener is not None:
        report["managed_api_listener"] = managed_api_listener
    if development_api_listener is not None:
        report["development_api_listener"] = development_api_listener
    if development_frontend_listener is not None:
        report["development_frontend_listener"] = development_frontend_listener
    if spec.runtime_mode == CONTAINER_RUNTIME_MODE:
        report["container_runtime"] = production_runtime
        report["managed_frontend_listener"] = managed_frontend_listener
        proxy = _validated_production_tailnet_proxy(root)
        report["tailnet_production_proxy"] = proxy
        report["tailnet_production_proxy_listeners"] = proxy["listener_reports"]
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


def _restore_service_ownership(
    snapshot: ServiceOwnershipSnapshot,
    root: Path,
    mutations: set[str] | frozenset[str] | None = None,
) -> None:
    systemd_dir = _host_user_systemd_dir()
    adapter_dropin = systemd_dir / f"{WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    frontend_unit = systemd_dir / FRONTEND_SERVICE
    frontend_dropin = systemd_dir / f"{FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    if mutations is None:
        selected_files = dict(snapshot.files)
        selected_services = dict(snapshot.active)
    else:
        requested = set(mutations)
        selected_files = {
            path: content
            for path, content in snapshot.files.items()
            if (
                (path == SELECTION_STATE_PATH and "selection_state" in requested)
                or (path == adapter_dropin and "adapter_files" in requested)
                or (path in {frontend_unit, frontend_dropin} and "frontend_files" in requested)
            )
        }
        selected_services = {
            service: was_active
            for service, was_active in snapshot.active.items()
            if (
                (service == WORKFLOW_ADAPTER_SERVICE and "adapter_service" in requested)
                or (service == FRONTEND_SERVICE and "frontend_service" in requested)
            )
        }
    errors: list[str] = []
    for path, content in selected_files.items():
        try:
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
        except Exception as exc:
            errors.append(f"file {path}: {exc}")
    if any(path != SELECTION_STATE_PATH for path in selected_files):
        try:
            daemon_reload(project_root=root)
        except Exception as exc:
            errors.append(f"daemon reload: {exc}")
    for service, was_active in selected_services.items():
        actions = ("reset-failed", "restart") if was_active else ("stop",)
        for action in actions:
            try:
                run_systemctl(action, service, project_root=root)
            except Exception as exc:
                errors.append(f"service {service} {action}: {exc}")
    for path, content in selected_files.items():
        try:
            if content is None:
                if path.exists():
                    raise TailnetEnvironmentError("unexpected file remains")
            else:
                file_bytes, mode = content
                if not path.is_file() or path.read_bytes() != file_bytes:
                    raise TailnetEnvironmentError("file bytes differ")
                if path.stat().st_mode & 0o7777 != mode:
                    raise TailnetEnvironmentError("file mode differs")
        except Exception as exc:
            errors.append(f"verify file {path}: {exc}")
    for service, was_active in selected_services.items():
        try:
            if service_is_active(service, project_root=root) is not was_active:
                raise TailnetEnvironmentError("active state differs")
        except Exception as exc:
            errors.append(f"verify service {service}: {exc}")
    if errors:
        raise TailnetEnvironmentError(
            "service ownership rollback incomplete: " + " | ".join(errors)
        )


def select_tailnet_environment(
    environment: str | None,
    *,
    project_root: Path | None = None,
) -> dict[str, object]:
    # Validate before acquiring the lifecycle lock or invoking any service/Serve action.
    normalized = str(environment or "").strip().lower()
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else _canonical_environment_root(normalized)
    )
    spec = environment_spec(normalized, project_root=root)
    if project_root is None:
        _validate_canonical_environment_root(root, normalized)

    with _selection_lock():
        prior = _read_serve_snapshot()
        ownership = _service_ownership_snapshot(root, spec)
        prior_non_root = {path: handler for path, handler in prior.handlers.items() if path != "/"}
        service_ownership_mutations: set[str] = set()
        control_route_attempted = False
        serve_root_attempted = False
        try:
            started_mutations = _start_selected_environment(spec, root)
            if started_mutations:
                service_ownership_mutations.update(started_mutations)
            control_route_will_mutate = _control_route_needs_mutation(prior)
            if control_route_will_mutate:
                # Register before the ambiguous setter: it may apply the route
                # and disconnect, and its own best-effort rollback may fail.
                control_route_attempted = True
            changed_control_route = _ensure_control_route(prior)
            if changed_control_route is not control_route_will_mutate:
                raise TailnetEnvironmentError(
                    "control-route mutation accounting disagreed with preflight"
                )
            expected_non_root = dict(prior_non_root)
            expected_non_root[CONTROL_PATH] = {"Proxy": CONTROL_TARGET}
            serve_root_attempted = True
            _set_serve_root(spec.serve_target)
            selected = _read_serve_snapshot()
            selected_non_root = {path: handler for path, handler in selected.handlers.items() if path != "/"}
            if selected_non_root != expected_non_root:
                raise TailnetEnvironmentError("Tailscale Serve changed unrelated path handlers")
            report = _verify_selected_environment(spec, root, selected)
            report["previous_serve_root_proxy"] = prior.root_proxy
            service_ownership_mutations.add("selection_state")
            _write_selection_state(report)
        except Exception as exc:
            service_ownership_mutations.update(
                getattr(exc, "service_ownership_mutations", frozenset())
            )
            rollback_errors: list[str] = []
            if control_route_attempted:
                try:
                    _restore_control_route(prior)
                except Exception as rollback_exc:
                    rollback_errors.append(f"control route: {rollback_exc}")
            if serve_root_attempted:
                try:
                    _set_serve_root(prior.root_proxy)
                except Exception as rollback_exc:
                    rollback_errors.append(f"Serve root: {rollback_exc}")
            if control_route_attempted or serve_root_attempted:
                try:
                    restored = _read_serve_snapshot()
                    if restored.raw != prior.raw:
                        raise TailnetEnvironmentError("rollback Serve configuration verification failed")
                except Exception as rollback_exc:
                    rollback_errors.append(f"Serve verification: {rollback_exc}")
            if service_ownership_mutations:
                try:
                    _restore_service_ownership(ownership, root, service_ownership_mutations)
                except Exception as rollback_exc:
                    rollback_errors.append(f"service ownership: {rollback_exc}")
            if serve_root_attempted:
                try:
                    wait_for_http(f"{prior.root_proxy.rstrip('/')}/", timeout_seconds=90.0)
                except Exception as rollback_exc:
                    rollback_errors.append(f"prior root health: {rollback_exc}")
            rollback_detail = (
                f"; rollback also failed: {' | '.join(rollback_errors)}"
                if rollback_errors else ""
            )
            rollback_state = (
                f"was rolled back to {prior.root_proxy}"
                if control_route_attempted or serve_root_attempted or service_ownership_mutations
                else "failed before any mutation"
            )
            raise TailnetEnvironmentError(
                f"Tailnet environment switch {rollback_state}: {exc}{rollback_detail}"
            ) from exc
        return report


def current_tailnet_environment(*, project_root: Path | None = None) -> dict[str, object]:
    if project_root is not None:
        development_root = production_root = Path(project_root).resolve()
    else:
        development_root = CANONICAL_DEVELOPMENT_ROOT.resolve()
        production_root = CANONICAL_PRODUCTION_ROOT.resolve()
    snapshot = _read_serve_snapshot()
    development_target = environment_spec(
        "development", project_root=development_root
    ).serve_target.rstrip("/")
    production_target = environment_spec(
        "production", project_root=production_root
    ).serve_target.rstrip("/")
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

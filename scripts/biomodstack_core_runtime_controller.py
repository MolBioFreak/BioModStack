#!/usr/bin/env python3
"""Bounded supervisor for the stable BioModStack Compose runtime.

This process is the only automatic recovery owner for the stable runtime.
Docker Compose services deliberately use ``restart: no`` and systemd does not
restart this controller after it enters a blocked state. Operator actions can
inspect the persisted state/incident bundle before explicitly starting again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "compose.core-runtime.yml"
GENERATED_OWNERSHIP_NORMALIZER = PROJECT_ROOT / "scripts" / "normalize_generated_ownership.py"
ALL_SERVICES = (
    "bms-api",
    "bms-host-agent",
    "bms-cpu-power",
    "bms-web",
)
DEFAULT_SERVICES = ALL_SERVICES
OPTIONAL_PROFILE_SERVICES: dict[str, tuple[str, ...]] = {}
SERVICE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "bms-web": ("bms-api",),
}
MAX_RECOVERIES = int(os.getenv("BMS_RUNTIME_MAX_RECOVERIES", "2"))
RECOVERY_WINDOW_SECONDS = int(os.getenv("BMS_RUNTIME_RECOVERY_WINDOW_SECONDS", "300"))
POLL_SECONDS = float(os.getenv("BMS_RUNTIME_SUPERVISOR_POLL_SECONDS", "10"))
STARTUP_GRACE_SECONDS = float(os.getenv("BMS_RUNTIME_STARTUP_GRACE_SECONDS", "90"))
MINKNOW_LOCAL_AUTH_TOKEN_PATH = Path("/tmp/minknow-auth-token.json")

NON_TRANSIENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("container-name-conflict", r"container name .* already in use|conflict\. the container name"),
    ("port-conflict", r"port is already allocated|address already in use|bind.*failed"),
    ("mount-invalid", r"invalid mount config|bind source path does not exist|mount.*not found"),
    ("permission-denied", r"permission denied|access denied"),
    ("missing-configuration", r"required variable|is not set|no such file or directory"),
    ("credential-failure", r"authentication failed|password authentication failed|unauthorized"),
    ("database-integrity", r"database disk image is malformed|invalid checkpoint record|could not locate a valid checkpoint"),
)
SECRET_PATTERN = re.compile(
    r"(?i)(password|token|secret|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
_STOP_REQUESTED = False


class RuntimeBlockedError(RuntimeError):
    def __init__(self, reason: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.context = dict(context or {})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir() -> Path:
    override = os.getenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    xdg_state = os.getenv("XDG_STATE_HOME")
    base = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    return (base / "biomodstack").resolve()


def state_path() -> Path:
    return state_dir() / "core-runtime-supervisor.json"


def redact(value: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_state() -> dict[str, Any]:
    try:
        payload = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def publish_state(status: str, **updates: Any) -> dict[str, Any]:
    payload = read_state()
    payload.update({"schema_version": 1, "status": status, "updated_at": utc_now(), **updates})
    atomic_write_json(state_path(), payload)
    return payload


def compose_command(*args: str) -> list[str]:
    command = ["docker", "compose"]
    env_file = str(os.getenv("BMS_CORE_RUNTIME_ENV_FILE") or "").strip()
    if env_file:
        command.extend(["--env-file", env_file])
    return [*command, "-f", str(COMPOSE_FILE), *args]


def run_command(args: Sequence[str], *, check: bool = False, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(args),
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeBlockedError("command-unavailable", f"Cannot run {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        combined = redact(f"{result.stdout}\n{result.stderr}".strip())
        reason = classify_failure(combined)
        raise RuntimeBlockedError(reason, f"Command failed ({result.returncode}): {' '.join(args)}", context={"output": combined})
    return result


def classify_failure(text: str) -> str:
    normalized = text.lower()
    for reason, pattern in NON_TRANSIENT_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return reason
    return "transient-runtime-failure"


def managed_services() -> tuple[str, ...]:
    """Return the exact service set activated for this Compose invocation."""
    active_profiles = {
        profile
        for profile in re.split(r"[\s,]+", os.getenv("COMPOSE_PROFILES", "").strip())
        if profile
    }
    active_optional = {
        service
        for profile, services in OPTIONAL_PROFILE_SERVICES.items()
        if profile in active_profiles
        for service in services
    }
    return tuple(
        service
        for service in ALL_SERVICES
        if service in DEFAULT_SERVICES or service in active_optional
    )


def parse_compose_ps(text: str) -> dict[str, dict[str, Any]]:
    text = text.strip()
    if not text:
        return {}
    rows: list[Any]
    try:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    services: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        service = str(raw.get("Service") or raw.get("service") or "").strip()
        if service:
            services[service] = {str(key): value for key, value in raw.items()}
    return services


def recovery_attempts(state: Mapping[str, Any], service: str, *, now: float | None = None) -> list[float]:
    current = time.time() if now is None else now
    raw_recovery = state.get("recovery", {})
    if not isinstance(raw_recovery, Mapping):
        return []
    raw_attempts = raw_recovery.get(service, [])
    if not isinstance(raw_attempts, list):
        return []
    attempts: list[float] = []
    for value in raw_attempts:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if current - timestamp <= RECOVERY_WINDOW_SECONDS:
            attempts.append(timestamp)
    return attempts


def reserve_recovery(service: str, *, now: float | None = None) -> dict[str, Any]:
    current = time.time() if now is None else now
    state = read_state()
    attempts = recovery_attempts(state, service, now=current)
    if len(attempts) >= MAX_RECOVERIES:
        raise RuntimeBlockedError(
            "recovery-budget-exhausted",
            f"Recovery budget exhausted for {service}: {len(attempts)}/{MAX_RECOVERIES} in {RECOVERY_WINDOW_SECONDS}s",
            context={"service": service, "attempts": attempts},
        )
    attempts.append(current)
    raw_recovery = state.get("recovery", {})
    recovery = dict(raw_recovery) if isinstance(raw_recovery, Mapping) else {}
    recovery[service] = attempts
    return publish_state("recovering", recovery=recovery, component=service, recovery_attempt=len(attempts))


def compose_ps() -> dict[str, dict[str, Any]]:
    result = run_command(compose_command("ps", "--all", "--format", "json"), check=False)
    return parse_compose_ps(result.stdout)


def service_failure(service: str, row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return "missing"
    state = str(row.get("State") or row.get("state") or "").lower()
    health = str(row.get("Health") or row.get("health") or "").lower()
    if state != "running":
        return state or "unknown-state"
    if health and health != "healthy":
        return health
    return None


def dependencies_ready(service: str, rows: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(service_failure(dependency, rows.get(dependency)) is None for dependency in SERVICE_DEPENDENCIES.get(service, ()))


def expected_container_names() -> dict[str, str]:
    return {
        "bms-api": "biomodstack-api",
        "bms-web": "biomodstack-web",
        "bms-host-agent": "biomodstack-host-agent",
        "bms-cpu-power": "biomodstack-cpu-power",
    }


def validate_fixed_container_names() -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    project_name = os.getenv("COMPOSE_PROJECT_NAME", "biomodstack-core-runtime")
    for service, container_name in expected_container_names().items():
        result = run_command(
            [
                "docker",
                "inspect",
                container_name,
                "--format",
                "{{ index .Config.Labels \"com.docker.compose.project\" }}|{{ index .Config.Labels \"com.docker.compose.service\" }}",
            ],
            check=False,
        )
        if result.returncode != 0:
            continue
        owner = result.stdout.strip()
        if owner != f"{project_name}|{service}":
            conflicts.append({"container": container_name, "expected": f"{project_name}|{service}", "actual": owner or "unlabelled"})
    return conflicts


def validate_storage() -> dict[str, str]:
    raw_state_dir = os.getenv("BMS_STATE_DIR", "").strip()
    if not raw_state_dir:
        raise RuntimeBlockedError("missing-state-root", "BMS_STATE_DIR must be explicitly configured; no fallback state root is permitted")
    stable_state = Path(raw_state_dir).expanduser().resolve()
    if not stable_state.is_dir():
        raise RuntimeBlockedError("missing-state-root", f"Configured BMS_STATE_DIR does not exist or is not a directory: {stable_state}")
    if not os.access(stable_state, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeBlockedError("state-root-permission", f"Configured BMS_STATE_DIR is not readable/writable/searchable: {stable_state}")
    return {"state_dir": str(stable_state)}


def run_preflight() -> dict[str, Any]:
    storage = validate_storage()
    docker = run_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=30
    )
    run_command(compose_command("config", "--quiet"), check=True, timeout=60)
    conflicts = validate_fixed_container_names()
    if conflicts:
        raise RuntimeBlockedError(
            "container-name-conflict",
            "Fixed container names are owned by another runtime",
            context={"conflicts": conflicts},
        )

    sys.path.insert(0, str(PROJECT_ROOT))
    from biomodstack_services import production_core_listener_preflight

    listener = production_core_listener_preflight(PROJECT_ROOT)
    if not listener.get("ok"):
        raise RuntimeBlockedError(
            "listener-ownership-conflict",
            "One or more stable runtime ports have the wrong owner",
            context={"listener_preflight": listener},
        )
    result = {
        "checked_at": utc_now(),
        "docker_server": docker.stdout.strip(),
        "storage": storage,
        "listener_preflight": listener,
        "container_name_conflicts": [],
    }
    publish_state("preflight-ok", preflight=result, blocked_reason=None, component=None)
    return result


def recent_logs(service: str) -> str:
    result = run_command(compose_command("logs", "--no-color", "--tail", "120", service), check=False, timeout=30)
    return redact(f"{result.stdout}\n{result.stderr}".strip())[-20000:]


def write_incident(reason: str, message: str, *, context: Mapping[str, Any] | None = None) -> Path:
    incident_dir = state_dir() / "incidents"
    incident_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = incident_dir / f"core-runtime-{stamp}-{reason}.json"
    try:
        snapshot = compose_ps()
    except (RuntimeBlockedError, subprocess.SubprocessError, OSError) as error:
        snapshot = {"diagnostic": {"message": redact(str(error))}}
    payload = {
        "schema_version": 1,
        "created_at": utc_now(),
        "reason": reason,
        "message": redact(message),
        "context": context or {},
        "compose_ps": snapshot,
    }
    atomic_write_json(path, payload)
    publish_state("blocked", blocked_reason=reason, message=redact(message), incident=str(path))
    return path


def recover(service: str, failure: str) -> None:
    logs = recent_logs(service)
    classified = classify_failure(logs)
    if classified != "transient-runtime-failure":
        raise RuntimeBlockedError(classified, f"Non-transient failure detected for {service}: {failure}", context={"service": service, "logs": logs})
    reserve_recovery(service)
    if failure in {"missing", "created", "exited", "dead", "removing", "unknown-state"}:
        command = compose_command("up", "-d", "--no-deps", service)
    else:
        command = compose_command("restart", service)
    result = run_command(command, check=False, timeout=120)
    if result.returncode != 0:
        combined = redact(f"{result.stdout}\n{result.stderr}".strip())
        raise RuntimeBlockedError(classify_failure(combined), f"Bounded recovery command failed for {service}", context={"service": service, "output": combined})
    publish_state("monitoring", component=service, last_recovery={"service": service, "failure": failure, "at": utc_now()})


def minknow_local_auth_token_marker() -> tuple[int, int] | None:
    """Return the mounted MinKNOW token file identity without reading its secret."""
    try:
        token_stat = MINKNOW_LOCAL_AUTH_TOKEN_PATH.stat()
    except OSError:
        return None
    return token_stat.st_dev, token_stat.st_ino


def minknow_local_auth_enabled() -> bool:
    return str(os.getenv("MINKNOW_API_USE_LOCAL_TOKEN", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def recover_minknow_token_rotation(
    previous_marker: tuple[int, int] | None,
    current_marker: tuple[int, int] | None,
) -> bool:
    """Record token replacement without competing for the singleton host agent.

    The root-owned Mk1D reconnect service is the sole authority allowed to
    recreate ``bms-host-agent``.  The core supervisor only reports that a
    bounded, explicit Reconnect Mk1D operation is required.
    """
    if previous_marker is None or current_marker is None or previous_marker == current_marker:
        return False
    publish_state(
        "monitoring",
        component="bms-host-agent",
        last_recovery={
            "service": "bms-host-agent",
            "failure": "minknow-local-auth-token-rotated",
            "action": "manual-mk1d-reconnect-required",
            "at": utc_now(),
        },
    )
    return False


def _handle_signal(_signum: int, _frame: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def supervise() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        preflight = run_preflight()
        local_token_auth_enabled = minknow_local_auth_enabled()
        # Capture identity before Compose starts so an atomic token rotation in the
        # startup window cannot leave the host-agent with a stale bind mount.
        token_marker = minknow_local_auth_token_marker() if local_token_auth_enabled else None
        result = run_command(compose_command("up", "-d"), check=False, timeout=300)
        if result.returncode != 0:
            combined = redact(f"{result.stdout}\n{result.stderr}".strip())
            raise RuntimeBlockedError(classify_failure(combined), "Initial Compose launch failed", context={"output": combined})
        startup_token_recovered = False
        if local_token_auth_enabled:
            current_token_marker = minknow_local_auth_token_marker()
            startup_token_recovered = recover_minknow_token_rotation(token_marker, current_token_marker)
            if current_token_marker is not None:
                token_marker = current_token_marker
        services = managed_services()
        publish_state(
            "starting",
            pid=os.getpid(),
            services=list(services),
            preflight=preflight,
            started_at=utc_now(),
            startup_token_recovered=startup_token_recovered,
        )
        grace_deadline = time.monotonic() + STARTUP_GRACE_SECONDS
        while not _STOP_REQUESTED:
            token_recovered = False
            if minknow_local_auth_enabled():
                current_token_marker = minknow_local_auth_token_marker()
                token_recovered = recover_minknow_token_rotation(token_marker, current_token_marker)
                if current_token_marker is not None:
                    token_marker = current_token_marker
            rows = compose_ps()
            failures = [(service, service_failure(service, rows.get(service))) for service in services]
            failures = [
                (service, failure)
                for service, failure in failures
                if failure and not (token_recovered and service == "bms-host-agent")
            ]
            if failures and time.monotonic() >= grace_deadline:
                deferred: list[dict[str, object]] = []
                recovered = False
                for service, failure in failures:
                    if not dependencies_ready(service, rows):
                        deferred.append(
                            {
                                "service": service,
                                "failure": failure,
                                "waiting_for": list(SERVICE_DEPENDENCIES.get(service, ())),
                            }
                        )
                        continue
                    recover(service, str(failure))
                    recovered = True
                publish_state("recovering" if recovered else "dependency-blocked", deferred_recoveries=deferred)
                grace_deadline = time.monotonic() + STARTUP_GRACE_SECONDS
            else:
                publish_state(
                    "monitoring" if not failures else "starting",
                    pid=os.getpid(),
                    services=list(services),
                    observed=rows,
                    pending_failures=[{"service": service, "failure": failure} for service, failure in failures],
                )
            time.sleep(POLL_SECONDS)
        publish_state("stopping", pid=os.getpid())
        return 0
    except RuntimeBlockedError as exc:
        incident = write_incident(exc.reason, str(exc), context=exc.context)
        print(f"BioModStack core runtime blocked: {exc}. Incident: {incident}", file=sys.stderr)
        return 78
    except Exception as exc:  # defensive: unexpected supervisor bugs must fail closed
        incident = write_incident("supervisor-internal-error", f"{type(exc).__name__}: {exc}")
        print(f"BioModStack core runtime supervisor failed closed. Incident: {incident}", file=sys.stderr)
        return 70


def normalize_generated_ownership(*, check_only: bool) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(GENERATED_OWNERSHIP_NORMALIZER)]
    if check_only:
        command.append("--check")
    result = run_command(command, check=False, timeout=300)
    if result.returncode != 0:
        reason = "generated-ownership-drift" if check_only else "generated-ownership-repair-failed"
        raise RuntimeBlockedError(
            reason,
            "Generated build/test ownership is not normalized",
            context={"output": redact(f"{result.stdout}\n{result.stderr}".strip())},
        )
    return result


def perform_once(action: str, services: Sequence[str]) -> int:
    try:
        if action in {"up", "rebuild"}:
            run_preflight()
        args: list[str]
        if action == "up":
            args = ["up", "-d"]
            if services:
                args.extend(services)
        elif action == "rebuild":
            args = ["up", "-d", "--build"]
            if services:
                args.extend(services)
        elif action == "stop":
            args = ["stop", *services]
        elif action == "down":
            args = ["down"]
        elif action == "status":
            payload = {"supervisor": read_state(), "compose": compose_ps()}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        elif action == "preflight":
            print(json.dumps(run_preflight(), indent=2, sort_keys=True))
            return 0
        elif action in {"ownership-check", "ownership-repair"}:
            result = normalize_generated_ownership(check_only=action == "ownership-check")
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return 0
        else:
            raise ValueError(action)
        result = run_command(compose_command(*args), check=False, timeout=600)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            combined = redact(f"{result.stdout}\n{result.stderr}".strip())
            raise RuntimeBlockedError(classify_failure(combined), f"Compose {action} failed", context={"output": combined})
        return 0
    except RuntimeBlockedError as exc:
        incident = write_incident(exc.reason, str(exc), context=exc.context)
        print(f"BioModStack core runtime blocked: {exc}. Incident: {incident}", file=sys.stderr)
        return 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "supervise",
            "preflight",
            "status",
            "up",
            "rebuild",
            "stop",
            "down",
            "ownership-check",
            "ownership-repair",
        ),
    )
    parser.add_argument("services", nargs="*")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "supervise":
        if args.services:
            raise SystemExit("supervise does not accept service names")
        return supervise()
    return perform_once(args.action, args.services)


if __name__ == "__main__":
    raise SystemExit(main())

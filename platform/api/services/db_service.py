from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from services.assay_analytical_store import analytical_store_settings, analytical_store_status
from services.host_agent_client import (
    get_host_agent_service as _get_host_agent_service,
    host_agent_enabled as _host_agent_enabled,
    run_host_agent_service_action as _run_host_agent_service_action,
)

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

DB_SERVICE_ID = "bms-db-service"
DB_SERVICE_COMPONENT = "db-service"
DB_SERVICE_DISPLAY_NAME = "BMS DB service"
DB_SERVICE_SHORT_NAME = "BMS DB"
OFFLINE_MESSAGE = "db_service_offline — use BMS DB service → Start"
DEFAULT_DB_SERVICE_NAMES = ("bms-db",)
DEFAULT_DB_CONTAINER_NAMES = ("biomodstack-db",)
LEGACY_DB_SERVICE_NAMES = ("bms-analytical-postgres",)
LEGACY_DB_CONTAINER_NAMES = ("biomodstack-analytical-postgres",)
SUPPORTED_ACTIONS = {"status", "health", "logs", "start", "restart", "stop"}
COMMANDS = [
    "bms db-service status",
    "bms db-service start",
    "bms db-service restart",
    "bms db-service logs --tail 120",
]


def _split_csv_env(*names: str, defaults: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for name in names:
        raw = os.getenv(name)
        if not raw:
            continue
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    return values or list(defaults)


def _configured_service_names() -> list[str]:
    return _split_csv_env("BMS_DB_COMPOSE_SERVICE", "BMS_DB_COMPOSE_SERVICES", defaults=DEFAULT_DB_SERVICE_NAMES)


def _configured_container_names() -> list[str]:
    return _split_csv_env("BMS_DB_CONTAINER_NAME", "BMS_DB_CONTAINER_NAMES", defaults=DEFAULT_DB_CONTAINER_NAMES)


def _legacy_service_names() -> list[str]:
    return _split_csv_env("BMS_DB_LEGACY_COMPOSE_SERVICE", "BMS_DB_LEGACY_COMPOSE_SERVICES", defaults=LEGACY_DB_SERVICE_NAMES)


def _legacy_container_names() -> list[str]:
    return _split_csv_env("BMS_DB_LEGACY_CONTAINER_NAME", "BMS_DB_LEGACY_CONTAINER_NAMES", defaults=LEGACY_DB_CONTAINER_NAMES)


def _primary_service_name() -> str:
    service_names = _configured_service_names()
    return service_names[0] if service_names else DB_SERVICE_ID


def _primary_container_name() -> str:
    container_names = _configured_container_names()
    return container_names[0] if container_names else "biomodstack-db"


def _bounded_tail(tail: int | str | None) -> int:
    try:
        value = int(tail if tail is not None else 120)
    except (TypeError, ValueError):
        value = 120
    return max(1, min(value, 500))


def _compose_file() -> Path:
    return Path(os.getenv("BMS_DB_COMPOSE_FILE") or os.getenv("BMS_STATS_TOOLS_COMPOSE_FILE") or REPO_ROOT / "compose.core-runtime.yml")


def _compose_project() -> str:
    return os.getenv("BMS_DOCKER_COMPOSE_PROJECT") or os.getenv("COMPOSE_PROJECT_NAME") or "biomodstack-core-runtime"


def _compose_env_file() -> Path | None:
    candidates = [
        os.getenv("BMS_DOCKER_COMPOSE_ENV_FILE"),
        os.getenv("BMS_CORE_RUNTIME_ENV_FILE"),
        str(Path.home() / ".config" / "biomodstack" / "core-runtime.env"),
        str(REPO_ROOT / ".env.core-runtime.local"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _docker_compose_base() -> list[str]:
    configured = os.getenv("BMS_DOCKER_COMPOSE", "").strip()
    if configured:
        return configured.split()
    docker_bin = shutil.which("docker")
    if docker_bin:
        probe = subprocess.run([docker_bin, "compose", "version"], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return [docker_bin, "compose"]
    docker_compose_bin = shutil.which("docker-compose")
    if docker_compose_bin:
        return [docker_compose_bin]
    return ["docker", "compose"]


def _compose_global_args() -> list[str]:
    args = ["-p", _compose_project()]
    env_file = _compose_env_file()
    if env_file is not None:
        args.extend(["--env-file", str(env_file)])
    args.extend(["-f", str(_compose_file())])
    return args


def _run_compose(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    command = [*_docker_compose_base(), *_compose_global_args(), *args]
    env = os.environ.copy()
    env.setdefault("COMPOSE_PROJECT_NAME", _compose_project())
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _run_docker(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    command = [shutil.which("docker") or "docker", *args]
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _docker_available() -> tuple[bool, str | None]:
    if not shutil.which("docker"):
        return False, "docker CLI not available"
    return True, None


def _compose_available() -> tuple[bool, str | None]:
    docker_ok, docker_note = _docker_available()
    if not docker_ok:
        return False, docker_note
    compose_file = _compose_file()
    if not compose_file.exists():
        return False, f"compose file not found: {compose_file}"
    base = _docker_compose_base()
    if base == ["docker", "compose"]:
        return False, "docker compose plugin/docker-compose binary not available"
    return True, None


def _container_from_name(container_name: str) -> dict[str, Any] | None:
    result = _run_docker(["inspect", container_name], timeout=30)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError):
        payload = {}
    labels = ((payload.get("Config") or {}).get("Labels") or {}) if isinstance(payload, dict) else {}
    implementation_service_name = labels.get("com.docker.compose.service") or labels.get("org.biomodstack.compose_service")
    return {
        "service_name": _primary_service_name(),
        "implementation_service_name": implementation_service_name,
        "container_name": container_name,
        "source": "configured-name",
    }


def _find_container_by_label_or_name() -> dict[str, Any] | None:
    ok, _note = _docker_available()
    if not ok:
        return None

    for label_filter in (f"org.biomodstack.service_id={DB_SERVICE_ID}", f"org.biomodstack.component={DB_SERVICE_COMPONENT}"):
        result = _run_docker(["ps", "-a", "--filter", f"label={label_filter}", "--format", "{{json .}}"], timeout=30)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("Names") or row.get("Name")
            if name:
                return {"service_name": _primary_service_name(), "container_name": str(name).split(",")[0], "source": "docker-label"}

    for name in _configured_container_names():
        found = _container_from_name(name)
        if found is not None:
            return found
    for name in _legacy_container_names():
        found = _container_from_name(name)
        if found is not None:
            return found
    return None


def _tail_text(text: str, max_chars: int = 6000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def _redact_text(text: str) -> str:
    redacted = re.sub(r"(postgresql(?:\+[A-Za-z0-9_]+)?://[^:\s/@]+:)([^@\s]+)(@)", r"\1***\3", text)
    redacted = re.sub(r"(\b[A-Z0-9_]*PASSWORD[A-Z0-9_]*=)([^\s\n]+)", r"\1[REDACTED]", redacted)
    return redacted


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _inspect_container(container_name: str) -> dict[str, Any]:
    ok, note = _docker_available()
    if not ok:
        return {"state": "unknown", "health": "offline", "runtime_available": False, "runtime_note": note}

    result = _run_docker(["inspect", container_name], timeout=30)
    if result.returncode != 0:
        detail = _tail_text((result.stderr or result.stdout or "docker inspect failed").strip())
        if "No such object" in detail or "No such container" in detail:
            return {"state": "missing", "health": "offline", "runtime_available": False, "runtime_note": OFFLINE_MESSAGE}
        return {"state": "unknown", "health": "degraded", "runtime_available": False, "runtime_note": detail}

    try:
        payload = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        return {"state": "unknown", "health": "degraded", "runtime_available": False, "runtime_note": f"docker inspect parse failed: {exc}"}

    container_state = payload.get("State") or {}
    status = str(container_state.get("Status") or "unknown")
    running = bool(container_state.get("Running"))
    health_status = str((container_state.get("Health") or {}).get("Status") or ("healthy" if running else "offline"))
    state = "running" if running else "stopped" if status in {"created", "exited", "dead", "removing"} else status
    health = health_status if running else "offline"
    runtime_available = running and health in {"healthy", "running"}
    runtime_note = None if runtime_available else (f"BMS DB service container running but health={health}" if running else OFFLINE_MESSAGE)
    return {"state": state, "health": health, "runtime_available": runtime_available, "runtime_note": runtime_note}


def _container_logs(container_name: str, tail: int) -> str:
    bounded = _bounded_tail(tail)
    result = _run_docker(["logs", "--tail", str(bounded), container_name], timeout=30)
    return _tail_text((result.stdout or result.stderr or "").strip())


def _logical_database_statuses() -> list[dict[str, Any]]:
    logical: list[dict[str, Any]] = [
        {
            "name": os.getenv("BMS_CORE_DB_NAME", "bms_core_runtime"),
            "role": "core-runtime",
            "storage_mode": "sqlite-legacy",
            "status": "legacy-fallback-active",
            "reachable": True,
            "note": "Core runtime is still using SQLite during migration",
        }
    ]
    try:
        status = analytical_store_status()
        settings = analytical_store_settings()
        logical.append(
            {
                "name": str(status.get("database_name") or settings.database),
                "role": "assay-analytics",
                "storage_mode": "postgres" if str(status.get("database_kind") or "").startswith("postgres") else str(status.get("database_kind") or "unknown"),
                "status": str(status.get("status") or "configured"),
                "reachable": bool(status.get("available", True)),
                "note": status.get("message") or status.get("url_preview"),
            }
        )
    except (SQLAlchemyError, ValueError, OSError, RuntimeError) as exc:
        logical.append(
            {
                "name": os.getenv("BMS_ANALYTICAL_DB_NAME", "bms_analytical_data"),
                "role": "assay-analytics",
                "storage_mode": "postgres",
                "status": "degraded",
                "reachable": False,
                "note": str(exc),
            }
        )
    return logical


def _missing_descriptor(tail: int, *, runtime_note: str | None = None, control_mode: str = "docker-direct-transitional") -> dict[str, Any]:
    return {
        "component": DB_SERVICE_COMPONENT,
        "service_id": DB_SERVICE_ID,
        "display_name": DB_SERVICE_DISPLAY_NAME,
        "state": "missing" if control_mode != "unavailable" else "unknown",
        "health": "offline",
        "runtime_available": False,
        "optional_at_boot": True,
        "control_mode": control_mode,
        "service_name": _primary_service_name(),
        "container_name": _primary_container_name(),
        "host_agent_available": False,
        "runtime_note": runtime_note or OFFLINE_MESSAGE,
        "offline_message": OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logical_databases": _logical_database_statuses(),
        "logs": "",
        "logs_tail": _bounded_tail(tail),
    }


def _host_agent_unavailable_descriptor(tail: int, exc: Exception, *, last_action: str | None = None) -> dict[str, Any]:
    descriptor = _missing_descriptor(
        tail,
        runtime_note=f"Host Agent unavailable: {exc}",
        control_mode="host-agent",
    )
    descriptor["state"] = "unknown"
    descriptor["health"] = "degraded"
    descriptor["host_agent_available"] = False
    if last_action is not None:
        descriptor["last_action"] = last_action
    return _redact_value(descriptor)


def _normalize_host_agent_payload(
    payload: dict[str, Any],
    tail: int,
    *,
    last_action: str | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "component": DB_SERVICE_COMPONENT,
        "service_id": DB_SERVICE_ID,
        "display_name": DB_SERVICE_DISPLAY_NAME,
        "state": "unknown",
        "health": "unknown",
        "runtime_available": False,
        "optional_at_boot": True,
        "control_mode": "host-agent",
        "service_name": _primary_service_name(),
        "container_name": _primary_container_name(),
        "host_agent_available": True,
        "runtime_note": None,
        "offline_message": OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logical_databases": _logical_database_statuses(),
        "logs": "",
        "logs_tail": tail,
    }
    normalized.update(payload)
    payload_service_name = payload.get("service_name")
    if payload_service_name and payload_service_name != _primary_service_name():
        normalized["implementation_service_name"] = payload_service_name
    normalized["service_name"] = _primary_service_name()
    normalized["container_name"] = normalized.get("container_name") or _primary_container_name()
    normalized["component"] = str(normalized.get("component") or DB_SERVICE_COMPONENT)
    normalized["service_id"] = str(normalized.get("service_id") or DB_SERVICE_ID)
    normalized["display_name"] = str(normalized.get("display_name") or DB_SERVICE_DISPLAY_NAME)
    normalized["optional_at_boot"] = bool(normalized.get("optional_at_boot", True))
    normalized["control_mode"] = "host-agent"
    normalized["host_agent_available"] = True
    normalized["offline_message"] = normalized.get("offline_message") or OFFLINE_MESSAGE
    normalized["commands"] = normalized.get("commands") or COMMANDS
    normalized["logical_databases"] = normalized.get("logical_databases") or _logical_database_statuses()
    normalized["logs_tail"] = tail
    if last_action is not None:
        normalized["last_action"] = last_action
    return _redact_value(normalized)


def describe_db_service(tail: int = 120) -> dict[str, Any]:
    bounded_tail = _bounded_tail(tail)
    if _host_agent_enabled():
        try:
            return _normalize_host_agent_payload(_get_host_agent_service(DB_SERVICE_ID, tail=bounded_tail), bounded_tail)
        except (RuntimeError, OSError, ValueError) as exc:
            return _host_agent_unavailable_descriptor(bounded_tail, exc)

    ok, note = _docker_available()
    if not ok:
        return _redact_value(_missing_descriptor(bounded_tail, runtime_note=note, control_mode="unavailable"))

    container = _find_container_by_label_or_name()
    if container is None:
        return _redact_value(_missing_descriptor(bounded_tail))

    container_name = str(container.get("container_name") or _primary_container_name())
    inspected = _inspect_container(container_name)
    payload = {
        "component": DB_SERVICE_COMPONENT,
        "service_id": DB_SERVICE_ID,
        "display_name": DB_SERVICE_DISPLAY_NAME,
        "state": inspected.get("state", "unknown"),
        "health": inspected.get("health", "unknown"),
        "runtime_available": bool(inspected.get("runtime_available")),
        "optional_at_boot": True,
        "control_mode": "docker-direct-transitional",
        "service_name": _primary_service_name(),
        "implementation_service_name": container.get("implementation_service_name") or container.get("service_name"),
        "container_name": container_name,
        "host_agent_available": False,
        "runtime_note": inspected.get("runtime_note"),
        "offline_message": OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logical_databases": _logical_database_statuses(),
        "logs": inspected.get("logs", ""),
        "logs_tail": bounded_tail,
    }
    return _redact_value(payload)


def run_db_service_action(action: str, tail: int = 120, *, advanced: bool = False) -> dict[str, Any]:
    normalized = str(action or "").strip().lower()
    if normalized not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported db-service action: {action}")
    bounded_tail = _bounded_tail(tail)

    if normalized in {"status", "health"}:
        payload = describe_db_service(tail=bounded_tail)
        payload["last_action"] = normalized
        return payload

    if normalized == "stop" and not advanced:
        raise ValueError("unsupported db-service action: stop requires --i-know-this-disables-db-backed-features")

    if _host_agent_enabled():
        try:
            return _normalize_host_agent_payload(
                _run_host_agent_service_action(DB_SERVICE_ID, normalized, {"tail": bounded_tail, "advanced": advanced}),
                bounded_tail,
                last_action=normalized,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return _host_agent_unavailable_descriptor(bounded_tail, exc, last_action=normalized)

    ok, note = _docker_available()
    if not ok:
        descriptor = describe_db_service(tail=bounded_tail)
        descriptor["last_action"] = normalized
        descriptor["runtime_note"] = note
        return descriptor

    container = _find_container_by_label_or_name()

    if normalized == "logs":
        if container is None:
            descriptor = describe_db_service(tail=bounded_tail)
            descriptor["last_action"] = "logs"
            return descriptor
        container_name = str(container.get("container_name") or _primary_container_name())
        result = _run_docker(["logs", "--tail", str(bounded_tail), container_name], timeout=30)
        descriptor = describe_db_service(tail=bounded_tail)
        descriptor["logs"] = _redact_text(_tail_text((result.stdout or result.stderr or "").strip()))
        descriptor["last_action"] = "logs"
        descriptor["action_returncode"] = result.returncode
        if result.returncode != 0:
            descriptor["health"] = "degraded"
            descriptor["runtime_note"] = _redact_text(_tail_text((result.stderr or result.stdout or "docker logs failed").strip()))
        return _redact_value(descriptor)

    if normalized == "stop" and not advanced:
        raise ValueError("unsupported db-service action: stop requires --i-know-this-disables-db-backed-features")

    if container is not None:
        container_name = str(container.get("container_name") or _primary_container_name())
        docker_action = "stop" if normalized == "stop" else "restart" if normalized == "restart" else "start"
        result = _run_docker([docker_action, container_name], timeout=90)
    else:
        compose_ok, compose_note = _compose_available()
        if not compose_ok:
            descriptor = describe_db_service(tail=bounded_tail)
            descriptor["last_action"] = normalized
            descriptor["runtime_note"] = compose_note
            return descriptor
        service_name = _primary_service_name()
        result = _run_compose(["up", "-d", "--no-build", service_name], timeout=180)

    descriptor = describe_db_service(tail=bounded_tail)
    descriptor["last_action"] = normalized
    descriptor["action_returncode"] = result.returncode
    descriptor["action_output"] = _redact_text(_tail_text((result.stdout or result.stderr or "").strip()))
    if result.returncode != 0:
        descriptor["health"] = "degraded"
        descriptor["runtime_note"] = _redact_text(_tail_text((result.stderr or result.stdout or f"db-service {normalized} failed").strip()))
    return _redact_value(descriptor)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    tail = 120
    advanced = "--i-know-this-disables-db-backed-features" in sys.argv
    if "--tail" in sys.argv:
        idx = sys.argv.index("--tail")
        if idx + 1 < len(sys.argv):
            tail = int(sys.argv[idx + 1])
    print(json.dumps(run_db_service_action(action, tail=tail, advanced=advanced), indent=2, sort_keys=True))

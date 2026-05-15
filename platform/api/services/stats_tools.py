from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from services.host_agent_client import (
    get_host_agent_service as _get_host_agent_service,
    host_agent_enabled as _host_agent_enabled,
    run_host_agent_service_action as _run_host_agent_service_action,
)

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

STATS_TOOLS_COMPONENT = "stats-tools"
STATS_TOOLS_SERVICE_ID = "bms-stats-tools"
STATS_TOOLS_SERVICE_NAME = os.getenv("BMS_STATS_TOOLS_SERVICE", "bms-stats-tools")
STATS_TOOLS_CONTAINER_NAME = os.getenv("BMS_STATS_TOOLS_CONTAINER", "biomodstack-stats-tools")
STATS_TOOLS_OFFLINE_MESSAGE = "stats_tools_offline — use Stats Toolkit → Debug → Start stats-tools"
SUPPORTED_ACTIONS = {"status", "start", "stop", "restart", "health", "logs"}
COMMANDS = [
    "bms stats-tools status",
    "bms stats-tools start",
    "bms stats-tools stop",
    "bms stats-tools restart",
    "bms stats-tools logs --tail 120",
]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def stats_tools_externalized() -> bool:
    """Whether assay/statistics packages are expected in the optional stats-tools component."""
    return _truthy(os.getenv("BMS_STATS_TOOLS_EXTERNALIZED"))


def _compose_file() -> Path:
    return Path(os.getenv("BMS_STATS_TOOLS_COMPOSE_FILE") or REPO_ROOT / "compose.core-runtime.yml")


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
    env.setdefault("COMPOSE_PROFILES", "stats-tools")
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


def _container_exists() -> bool:
    result = _run_docker(["inspect", STATS_TOOLS_CONTAINER_NAME], timeout=30)
    return result.returncode == 0


def _tail_text(text: str, max_chars: int = 6000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


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


def _inspect_state() -> dict[str, Any]:
    ok, note = _docker_available()
    if not ok:
        return {
            "state": "unavailable",
            "health": "offline",
            "runtime_available": False,
            "runtime_note": note,
            "logs": "",
        }
    result = _run_docker(["inspect", STATS_TOOLS_CONTAINER_NAME], timeout=30)
    if result.returncode != 0:
        detail = _tail_text((result.stderr or result.stdout or "docker inspect failed").strip())
        if "No such object" in detail or "No such container" in detail:
            return {
                "state": "stopped",
                "health": "offline",
                "runtime_available": False,
                "runtime_note": STATS_TOOLS_OFFLINE_MESSAGE,
                "logs": "",
            }
        return {
            "state": "unknown",
            "health": "degraded",
            "runtime_available": False,
            "runtime_note": detail,
            "logs": "",
        }
    try:
        payload = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        return {
            "state": "unknown",
            "health": "degraded",
            "runtime_available": False,
            "runtime_note": f"docker inspect parse failed: {exc}",
            "logs": "",
        }
    container_state = payload.get("State") or {}
    status = str(container_state.get("Status") or "unknown")
    running = bool(container_state.get("Running"))
    health_status = str((container_state.get("Health") or {}).get("Status") or ("running" if running else "offline"))
    state = "running" if running else "stopped" if status in {"created", "exited", "dead", "removing"} else status
    health = health_status if running else "offline"
    runtime_available = running and health == "healthy"
    runtime_note = None
    if not runtime_available:
        runtime_note = f"stats-tools container running but health={health}" if running else STATS_TOOLS_OFFLINE_MESSAGE
    return {
        "state": state,
        "health": health,
        "runtime_available": runtime_available,
        "runtime_note": runtime_note,
        "logs": "",
    }


def _host_agent_unavailable_descriptor(tail: int, exc: Exception, *, last_action: str | None = None) -> dict[str, Any]:
    descriptor = {
        "component": STATS_TOOLS_COMPONENT,
        "service_id": STATS_TOOLS_SERVICE_ID,
        "service_name": STATS_TOOLS_SERVICE_NAME,
        "container_name": STATS_TOOLS_CONTAINER_NAME,
        "externalized": True,
        "optional_at_boot": True,
        "control_mode": "host-agent",
        "state": "unknown",
        "health": "degraded",
        "runtime_available": False,
        "runtime_note": f"Host Agent unavailable: {exc}",
        "offline_message": STATS_TOOLS_OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logs": "",
        "logs_tail": tail,
        "host_agent_available": False,
    }
    if last_action is not None:
        descriptor["last_action"] = last_action
    return descriptor


def _normalize_host_agent_payload(payload: dict[str, Any], tail: int, *, last_action: str | None = None) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "component": STATS_TOOLS_COMPONENT,
        "service_id": STATS_TOOLS_SERVICE_ID,
        "service_name": STATS_TOOLS_SERVICE_NAME,
        "container_name": STATS_TOOLS_CONTAINER_NAME,
        "externalized": True,
        "optional_at_boot": True,
        "control_mode": "host-agent",
        "state": "unknown",
        "health": "unknown",
        "runtime_available": False,
        "runtime_note": None,
        "offline_message": STATS_TOOLS_OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logs": "",
        "logs_tail": tail,
        "host_agent_available": True,
    }
    normalized.update(payload)
    normalized["component"] = str(normalized.get("component") or STATS_TOOLS_COMPONENT)
    normalized["service_id"] = str(normalized.get("service_id") or STATS_TOOLS_SERVICE_ID)
    normalized["externalized"] = True
    normalized["optional_at_boot"] = bool(normalized.get("optional_at_boot", True))
    normalized["control_mode"] = "host-agent"
    normalized["host_agent_available"] = True
    normalized["offline_message"] = normalized.get("offline_message") or STATS_TOOLS_OFFLINE_MESSAGE
    normalized["commands"] = normalized.get("commands") or COMMANDS
    normalized["logs_tail"] = tail
    if last_action is not None:
        normalized["last_action"] = last_action
    return normalized


def describe_stats_tools(tail: int = 120) -> dict[str, Any]:
    embedded = not stats_tools_externalized()
    if not embedded and _host_agent_enabled():
        try:
            return _normalize_host_agent_payload(_get_host_agent_service(STATS_TOOLS_SERVICE_ID, tail=tail), tail)
        except (RuntimeError, OSError, ValueError) as exc:
            return _host_agent_unavailable_descriptor(tail, exc)

    inspected = _inspect_state() if stats_tools_externalized() else {
        "state": "embedded",
        "health": "healthy",
        "runtime_available": True,
        "runtime_note": "stats tools are currently embedded in the core API image; lifecycle controls target the optional split container",
        "logs": "",
    }
    return {
        "component": STATS_TOOLS_COMPONENT,
        "service_name": STATS_TOOLS_SERVICE_NAME,
        "container_name": STATS_TOOLS_CONTAINER_NAME,
        "externalized": not embedded,
        "optional_at_boot": True,
        "control_mode": "docker-compose-profile" if stats_tools_externalized() else "embedded-core-runtime",
        "host_agent_available": False,
        "state": inspected["state"],
        "health": inspected["health"],
        "runtime_available": bool(inspected["runtime_available"]),
        "runtime_note": inspected.get("runtime_note"),
        "offline_message": STATS_TOOLS_OFFLINE_MESSAGE,
        "commands": COMMANDS,
        "logs": inspected.get("logs", ""),
        "logs_tail": tail,
    }


def stats_tools_available() -> bool:
    if not stats_tools_externalized():
        return True
    return bool(describe_stats_tools()["runtime_available"])


def run_stats_tools_action(action: str, tail: int = 120) -> dict[str, Any]:
    normalized = str(action or "").strip().lower()
    if normalized not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported stats-tools action: {action}")

    if normalized in {"status", "health"}:
        return describe_stats_tools(tail=tail)

    if stats_tools_externalized() and _host_agent_enabled():
        try:
            return _normalize_host_agent_payload(
                _run_host_agent_service_action(STATS_TOOLS_SERVICE_ID, normalized, {"tail": tail}),
                tail,
                last_action=normalized,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return _host_agent_unavailable_descriptor(tail, exc, last_action=normalized)

    if normalized == "logs":
        ok, note = _docker_available()
        descriptor = describe_stats_tools(tail=tail)
        if not ok:
            descriptor["runtime_note"] = note
            return descriptor
        result = _run_docker(["logs", "--tail", str(tail), STATS_TOOLS_CONTAINER_NAME], timeout=30)
        descriptor["logs"] = _tail_text((result.stdout or result.stderr or "").strip())
        descriptor["last_action"] = "logs"
        if result.returncode != 0:
            descriptor["health"] = "degraded"
            descriptor["runtime_note"] = _tail_text((result.stderr or result.stdout or "docker logs failed").strip())
        return descriptor

    ok, note = _docker_available()
    if not ok:
        descriptor = describe_stats_tools(tail=tail)
        descriptor["last_action"] = normalized
        descriptor["runtime_note"] = note
        return descriptor

    if normalized == "stop":
        before = describe_stats_tools(tail=tail)
        if before["state"] == "stopped":
            before["last_action"] = normalized
            before["action_returncode"] = 0
            before["action_output"] = "stats-tools container already stopped"
            return before
        result = _run_docker(["stop", STATS_TOOLS_CONTAINER_NAME], timeout=90)
    elif _container_exists():
        if normalized == "restart":
            before = describe_stats_tools(tail=tail)
            docker_action = "start" if before["state"] == "stopped" else "restart"
        else:
            docker_action = "start"
        result = _run_docker([docker_action, STATS_TOOLS_CONTAINER_NAME], timeout=90)
    else:
        ok, note = _compose_available()
        if not ok:
            descriptor = describe_stats_tools(tail=tail)
            descriptor["last_action"] = normalized
            descriptor["runtime_note"] = note
            return descriptor
        compose_args = ["--profile", "stats-tools", "up", "-d", "--no-build", STATS_TOOLS_SERVICE_NAME]
        result = _run_compose(compose_args, timeout=180)

    descriptor = describe_stats_tools(tail=tail)
    descriptor["last_action"] = normalized
    descriptor["action_returncode"] = result.returncode
    descriptor["action_output"] = _tail_text((result.stdout or result.stderr or "").strip())
    if result.returncode != 0:
        descriptor["health"] = "degraded"
        descriptor["runtime_note"] = _tail_text((result.stderr or result.stdout or f"stats-tools {normalized} failed").strip())
    return descriptor


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    tail = 120
    if "--tail" in sys.argv:
        idx = sys.argv.index("--tail")
        if idx + 1 < len(sys.argv):
            tail = int(sys.argv[idx + 1])
    import json

    print(json.dumps(run_stats_tools_action(action, tail=tail), indent=2, sort_keys=True))

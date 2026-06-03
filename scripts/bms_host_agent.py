#!/usr/bin/env python3
"""Host-local BioModStack control daemon.

This is intentionally narrow: it exposes only localhost HTTP endpoints used by the
socket-free BMS API container to inspect/control optional host-owned services.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "biomodstack-core-runtime"
DEFAULT_COMPOSE_FILE = REPO_ROOT / "compose.core-runtime.yml"
SUPPORTED_ACTIONS = {"status", "health", "logs", "start", "restart", "stop"}

SERVICES: dict[str, dict[str, Any]] = {
    "bms-db-service": {
        "service_id": "bms-db-service",
        "component": "db-service",
        "display_name": "BMS DB service",
        "service_name": "bms-db",
        "container_names": ["biomodstack-db", "biomodstack-analytical-postgres"],
        "legacy_service_names": ["bms-analytical-postgres"],
        "optional_at_boot": True,
        "offline_message": "db_service_offline — use BMS DB service → Start",
        "commands": [
            "bms db-service status",
            "bms db-service start",
            "bms db-service restart",
            "bms db-service logs --tail 120",
        ],
        "profile": None,
        "stop_requires_advanced": True,
    },
    "bms-stats-tools": {
        "service_id": "bms-stats-tools",
        "component": "stats-tools",
        "service_name": "bms-stats-tools",
        "container_names": ["biomodstack-stats-tools"],
        "externalized": True,
        "optional_at_boot": True,
        "offline_message": "stats_tools_offline — use Stats Toolkit → Debug → Start stats-tools",
        "commands": [
            "bms stats-tools status",
            "bms stats-tools start",
            "bms stats-tools stop",
            "bms stats-tools restart",
            "bms stats-tools logs --tail 120",
        ],
        "profile": "stats-tools",
        "stop_requires_advanced": False,
    },
}


def bounded_tail(raw: Any) -> int:
    try:
        value = int(raw if raw is not None else 120)
    except (TypeError, ValueError):
        value = 120
    return max(1, min(value, 500))


def tail_text(text: str, max_chars: int = 6000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def redact_text(text: str) -> str:
    redacted = re.sub(r"(postgresql(?:\+[A-Za-z0-9_]+)?://[^:\s/@]+:)([^@\s]+)(@)", r"\1***\3", text)
    redacted = re.sub(r"(\b[A-Z0-9_]*PASSWORD[A-Z0-9_]*=)([^\s\n]+)", r"\1[REDACTED]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def docker_bin() -> str:
    return shutil.which("docker") or "docker"


def compose_base() -> list[str]:
    configured = os.getenv("BMS_DOCKER_COMPOSE", "").strip()
    if configured:
        return configured.split()
    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run([docker, "compose", "version"], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return [docker, "compose"]
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [docker_compose]
    return ["docker", "compose"]


def compose_file() -> Path:
    return Path(os.getenv("BMS_HOST_AGENT_COMPOSE_FILE") or os.getenv("BMS_DB_COMPOSE_FILE") or os.getenv("BMS_STATS_TOOLS_COMPOSE_FILE") or DEFAULT_COMPOSE_FILE)


def compose_project() -> str:
    return os.getenv("BMS_DOCKER_COMPOSE_PROJECT") or os.getenv("COMPOSE_PROJECT_NAME") or DEFAULT_PROJECT


def run_command(command: list[str], *, timeout: int = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    merged_env.setdefault("COMPOSE_PROJECT_NAME", compose_project())
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_docker(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run_command([docker_bin(), *args], timeout=timeout)


def run_compose(args: list[str], *, service: dict[str, Any], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    command = [*compose_base(), "-p", compose_project(), "-f", str(compose_file()), *args]
    env: dict[str, str] = {}
    if service.get("profile"):
        env["COMPOSE_PROFILES"] = str(service["profile"])
    return run_command(command, timeout=timeout, env=env)


def parse_inspect(container_name: str) -> dict[str, Any] | None:
    result = run_docker(["inspect", container_name], timeout=30)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    return payload[0]


def find_container(service: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    service_id = service["service_id"]
    for label_filter in (f"org.biomodstack.service_id={service_id}", f"org.biomodstack.component={service['component']}"):
        result = run_docker(["ps", "-a", "--filter", f"label={label_filter}", "--format", "{{json .}}"], timeout=30)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            names = str(row.get("Names") or row.get("Name") or "").split(",")
            if not names:
                continue
            inspected = parse_inspect(names[0])
            if inspected is not None:
                return names[0], inspected
    for name in service.get("container_names", []):
        inspected = parse_inspect(str(name))
        if inspected is not None:
            return str(name), inspected
    return None, None


def inspect_state(service: dict[str, Any], inspected: dict[str, Any] | None, container_name: str | None) -> dict[str, Any]:
    if inspected is None or container_name is None:
        return {
            "container_name": (service.get("container_names") or [None])[-1],
            "state": "missing",
            "health": "offline",
            "runtime_available": False,
            "runtime_note": service["offline_message"],
        }
    state = inspected.get("State") or {}
    status = str(state.get("Status") or "unknown")
    running = bool(state.get("Running"))
    health_status = str((state.get("Health") or {}).get("Status") or ("running" if running else "offline"))
    normalized_state = "running" if running else "stopped" if status in {"created", "exited", "dead", "removing"} else status
    health = health_status if running else "offline"
    runtime_available = running and health in {"healthy", "running"}
    runtime_note = None if runtime_available else (f"{service['service_id']} container running but health={health}" if running else service["offline_message"])
    labels = ((inspected.get("Config") or {}).get("Labels") or {}) if isinstance(inspected, dict) else {}
    implementation_service_name = labels.get("com.docker.compose.service") or service.get("service_name")
    return {
        "container_name": container_name,
        "state": normalized_state,
        "health": health,
        "runtime_available": runtime_available,
        "runtime_note": runtime_note,
        "service_name": service.get("service_name"),
        "implementation_service_name": implementation_service_name,
    }


def container_logs(container_name: str | None, tail: int) -> tuple[str, int, str | None]:
    if not container_name:
        return "", 0, None
    result = run_docker(["logs", "--tail", str(tail), container_name], timeout=30)
    text = tail_text((result.stdout or result.stderr or "").strip())
    note = None if result.returncode == 0 else tail_text((result.stderr or result.stdout or "docker logs failed").strip())
    return redact_text(text), result.returncode, redact_text(note) if note else None


def descriptor(service_id: str, *, tail: int = 120, include_logs: bool = False) -> dict[str, Any]:
    if service_id not in SERVICES:
        raise KeyError(service_id)
    service = SERVICES[service_id]
    container_name, inspected = find_container(service)
    state = inspect_state(service, inspected, container_name)
    resolved_container = state.get("container_name") or container_name
    logs = ""
    log_returncode = 0
    log_note = None
    if include_logs:
        logs, log_returncode, log_note = container_logs(str(resolved_container) if resolved_container else None, tail)
    payload = {
        "component": service["component"],
        "service_id": service["service_id"],
        "service_name": service.get("service_name"),
        "implementation_service_name": state.get("implementation_service_name"),
        "container_name": resolved_container,
        "state": state["state"],
        "health": state["health"],
        "runtime_available": bool(state["runtime_available"]),
        "runtime_note": state.get("runtime_note"),
        "optional_at_boot": bool(service.get("optional_at_boot", True)),
        "control_mode": "host-agent",
        "host_agent_available": True,
        "offline_message": service.get("offline_message"),
        "commands": service.get("commands", []),
        "logs": logs,
        "logs_tail": tail,
    }
    if "display_name" in service:
        payload["display_name"] = service["display_name"]
    if "externalized" in service:
        payload["externalized"] = service["externalized"]
    if include_logs:
        payload["action_returncode"] = log_returncode
        if log_note:
            payload["health"] = "degraded"
            payload["runtime_note"] = log_note
    return redact_value(payload)


def run_action(service_id: str, action: str, *, tail: int = 120, advanced: bool = False) -> dict[str, Any]:
    if service_id not in SERVICES:
        raise KeyError(service_id)
    normalized = str(action or "").strip().lower()
    if normalized not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    service = SERVICES[service_id]

    if normalized in {"status", "health"}:
        payload = descriptor(service_id, tail=tail)
        payload["last_action"] = normalized
        return payload

    if normalized == "logs":
        payload = descriptor(service_id, tail=tail, include_logs=True)
        payload["last_action"] = "logs"
        return payload

    if normalized == "stop" and service.get("stop_requires_advanced") and not advanced:
        raise PermissionError(f"{service_id} stop requires advanced=true")

    container_name, inspected = find_container(service)
    result: subprocess.CompletedProcess[str]
    if inspected is not None and container_name is not None:
        if normalized == "restart":
            # docker restart starts stopped containers poorly on some engines; use start if not running.
            state = inspected.get("State") or {}
            docker_action = "restart" if bool(state.get("Running")) else "start"
        else:
            docker_action = normalized
        result = run_docker([docker_action, container_name], timeout=90)
    else:
        if normalized == "stop":
            payload = descriptor(service_id, tail=tail)
            payload.update({"last_action": normalized, "action_returncode": 0, "action_output": "container already missing/stopped"})
            return payload
        compose_args = []
        if service.get("profile"):
            compose_args.extend(["--profile", str(service["profile"])])
        compose_args.extend(["up", "-d", "--no-build", str(service["service_name"])])
        result = run_compose(compose_args, service=service, timeout=180)

    payload = descriptor(service_id, tail=tail, include_logs=(normalized == "logs"))
    payload["last_action"] = normalized
    payload["action_returncode"] = result.returncode
    payload["action_output"] = redact_text(tail_text((result.stdout or result.stderr or "").strip()))
    if result.returncode != 0:
        payload["health"] = "degraded"
        payload["runtime_available"] = False
        payload["runtime_note"] = redact_text(tail_text((result.stderr or result.stdout or f"{service_id} {normalized} failed").strip()))
    return redact_value(payload)


class HostAgentHandler(BaseHTTPRequestHandler):
    server_version = "BMSHostAgent/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _send_json(self, status: int, payload: Any) -> None:
        raw = json.dumps(redact_value(payload), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("request JSON must be an object")
        return parsed

    def _service_route(self) -> tuple[str | None, str | None]:
        path = urlparse(self.path).path.rstrip("/")
        prefix = "/api/host-agent/services/"
        if not path.startswith(prefix):
            return None, None
        rest = path[len(prefix):]
        parts = [unquote(part) for part in rest.split("/") if part]
        if not parts:
            return None, None
        return parts[0], parts[1] if len(parts) > 1 else None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") in {"/health", "/api/host-agent/health"}:
            self._send_json(200, {"status": "healthy", "service": "bms-host-agent"})
            return
        service_id, suffix = self._service_route()
        if service_id is None or suffix is not None:
            self._send_json(404, {"detail": "not found"})
            return
        if service_id not in SERVICES:
            self._send_json(404, {"detail": f"unknown service_id: {service_id}"})
            return
        query = parse_qs(parsed.query)
        tail = bounded_tail((query.get("tail") or [120])[0])
        self._send_json(200, descriptor(service_id, tail=tail))

    def do_POST(self) -> None:  # noqa: N802
        service_id, action = self._service_route()
        if service_id is None or action is None:
            self._send_json(404, {"detail": "not found"})
            return
        if service_id not in SERVICES:
            self._send_json(404, {"detail": f"unknown service_id: {service_id}"})
            return
        try:
            payload = self._read_json()
            tail = bounded_tail(payload.get("tail", 120))
            advanced = bool(payload.get("advanced", False))
            self._send_json(200, run_action(service_id, action, tail=tail, advanced=advanced))
        except PermissionError as exc:
            self._send_json(403, {"detail": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"detail": str(exc)})
        except subprocess.TimeoutExpired as exc:
            self._send_json(504, {"detail": f"command timed out: {exc}"})
        except Exception as exc:  # pragma: no cover - operational guard
            self._send_json(500, {"detail": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="BMS host-local control agent")
    parser.add_argument("--host", default=os.getenv("BMS_HOST_AGENT_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BMS_HOST_AGENT_PORT", "8798")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), HostAgentHandler)
    print(f"bms-host-agent listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

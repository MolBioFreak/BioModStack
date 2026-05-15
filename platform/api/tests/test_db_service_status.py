from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import db_service


def _running_container() -> dict[str, object]:
    return {
        "service_name": "bms-analytical-postgres",
        "container_name": "biomodstack-analytical-postgres",
        "source": "configured-name",
    }


def _running_inspection() -> dict[str, object]:
    return {
        "state": "running",
        "health": "healthy",
        "runtime_available": True,
        "runtime_note": None,
    }


def test_db_service_status_reports_product_name_and_transitional_container(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", "postgresql+asyncpg://bms_assay:[REDACTED]@127.0.0.1:55432/bms_analytical_data")
    monkeypatch.setattr(db_service, "_docker_available", lambda: (True, None), raising=False)
    monkeypatch.setattr(db_service, "_find_container_by_label_or_name", _running_container, raising=False)
    monkeypatch.setattr(db_service, "_inspect_container", lambda container_name: _running_inspection(), raising=False)
    monkeypatch.setattr(db_service, "_container_logs", lambda container_name, tail: "database system is ready", raising=False)
    monkeypatch.setattr(
        db_service,
        "_logical_database_statuses",
        lambda: [
            {
                "name": "bms_core_runtime",
                "role": "core-runtime",
                "storage_mode": "sqlite-legacy",
                "status": "legacy-fallback-active",
                "reachable": True,
                "note": "Core runtime is still using SQLite during migration",
            },
            {
                "name": "bms_analytical_data",
                "role": "assay-analytics",
                "storage_mode": "postgres",
                "status": "ok",
                "reachable": True,
                "note": "postgresql+asyncpg://bms_assay:[REDACTED]@127.0.0.1:55432/bms_analytical_data",
            },
        ],
        raising=False,
    )

    payload = db_service.describe_db_service(tail=120)

    assert payload["component"] == "db-service"
    assert payload["service_id"] == "bms-db-service"
    assert payload["display_name"] == "BMS DB service"
    assert payload["service_name"] == "bms-analytical-postgres"
    assert payload["container_name"] == "biomodstack-analytical-postgres"
    assert payload["optional_at_boot"] is True
    assert payload["host_agent_available"] is False
    assert payload["control_mode"] == "docker-direct-transitional"
    assert payload["runtime_available"] is True
    assert payload["logical_databases"][0]["name"] == "bms_core_runtime"
    assert payload["logical_databases"][1]["name"] == "bms_analytical_data"
    assert "bms db-service status" in payload["commands"]
    assert "bms db-service start" in payload["commands"]
    assert "bms db-service restart" in payload["commands"]
    assert "bms db-service logs --tail 120" in payload["commands"]
    assert "[REDACTED]" not in str(payload)


def test_db_service_status_prefers_configured_host_agent_without_docker(monkeypatch) -> None:
    docker_touched: list[str] = []

    def fail_docker_available():
        docker_touched.append("docker")
        raise AssertionError("BMS API must not touch Docker directly when Host Agent is configured")

    def fake_host_agent_status(service_id: str, tail: int = 120):
        assert service_id == "bms-db-service"
        assert tail == 80
        return {
            "service_id": "bms-db-service",
            "component": "db-service",
            "display_name": "BMS DB service",
            "state": "running",
            "health": "healthy",
            "runtime_available": True,
            "service_name": "bms-db",
            "container_name": "biomodstack-db",
            "logs_tail": tail,
        }

    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(db_service, "_host_agent_enabled", lambda: True, raising=False)
    monkeypatch.setattr(db_service, "_get_host_agent_service", fake_host_agent_status, raising=False)
    monkeypatch.setattr(db_service, "_docker_available", fail_docker_available, raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    payload = db_service.describe_db_service(tail=80)

    assert docker_touched == []
    assert payload["control_mode"] == "host-agent"
    assert payload["host_agent_available"] is True
    assert payload["service_id"] == "bms-db-service"
    assert payload["display_name"] == "BMS DB service"
    assert payload["container_name"] == "biomodstack-db"


def test_db_service_action_prefers_configured_host_agent_without_docker(monkeypatch) -> None:
    host_agent_actions: list[tuple[str, str, dict[str, object] | None]] = []

    def fail_docker_available():
        raise AssertionError("BMS API must not touch Docker directly when Host Agent is configured")

    def fake_host_agent_action(service_id: str, action: str, payload: dict[str, object] | None = None):
        host_agent_actions.append((service_id, action, payload))
        return {
            "service_id": service_id,
            "component": "db-service",
            "display_name": "BMS DB service",
            "state": "running",
            "health": "healthy",
            "runtime_available": True,
            "last_action": action,
            "action_returncode": 0,
            "logs_tail": payload["tail"] if payload else None,
        }

    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(db_service, "_host_agent_enabled", lambda: True, raising=False)
    monkeypatch.setattr(db_service, "_run_host_agent_service_action", fake_host_agent_action, raising=False)
    monkeypatch.setattr(db_service, "_docker_available", fail_docker_available, raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    payload = db_service.run_db_service_action("restart", tail=42)

    assert host_agent_actions == [("bms-db-service", "restart", {"tail": 42, "advanced": False})]
    assert payload["control_mode"] == "host-agent"
    assert payload["host_agent_available"] is True
    assert payload["last_action"] == "restart"


def test_db_service_host_agent_unavailable_degrades_without_docker(monkeypatch) -> None:
    def fail_docker_available():
        raise AssertionError("BMS API must not fall back to Docker when Host Agent is configured but offline")

    def fail_host_agent_status(service_id: str, tail: int = 120):
        raise RuntimeError("Host Agent request to http://127.0.0.1:8798/api/host-agent/services/bms-db-service failed: refused")

    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(db_service, "_host_agent_enabled", lambda: True, raising=False)
    monkeypatch.setattr(db_service, "_get_host_agent_service", fail_host_agent_status, raising=False)
    monkeypatch.setattr(db_service, "_docker_available", fail_docker_available, raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    payload = db_service.describe_db_service(tail=30)

    assert payload["control_mode"] == "host-agent"
    assert payload["host_agent_available"] is False
    assert payload["runtime_available"] is False
    assert payload["health"] == "degraded"
    assert "Host Agent" in payload["runtime_note"]


def test_db_service_status_degrades_when_docker_missing(monkeypatch) -> None:
    monkeypatch.setattr(db_service, "_docker_available", lambda: (False, "docker CLI not available"), raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    payload = db_service.describe_db_service()

    assert payload["component"] == "db-service"
    assert payload["service_id"] == "bms-db-service"
    assert payload["display_name"] == "BMS DB service"
    assert payload["state"] == "unknown"
    assert payload["health"] == "offline"
    assert payload["runtime_available"] is False
    assert payload["control_mode"] == "unavailable"
    assert payload["runtime_note"] == "docker CLI not available"
    assert payload["offline_message"] == "db_service_offline — use BMS DB service → Start"


def test_db_service_status_redacts_database_urls(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", "postgresql+asyncpg://bms_assay:[REDACTED]@127.0.0.1:55432/bms_analytical_data")
    monkeypatch.setenv("BMS_DB_PASSWORD", "also-secret")
    monkeypatch.setattr(db_service, "_docker_available", lambda: (False, "docker CLI not available"), raising=False)

    payload = db_service.describe_db_service()

    rendered = str(payload)
    assert "[REDACTED]" not in rendered
    assert "also-secret" not in rendered
    assert "postgresql+asyncpg://bms_assay:***@127.0.0.1:55432/bms_analytical_data" in rendered


def test_db_service_start_uses_existing_container_without_compose_build(monkeypatch) -> None:
    docker_calls: list[list[str]] = []
    compose_calls: list[list[str]] = []

    monkeypatch.setattr(db_service, "_docker_available", lambda: (True, None), raising=False)
    monkeypatch.setattr(db_service, "_find_container_by_label_or_name", _running_container, raising=False)
    monkeypatch.setattr(db_service, "_inspect_container", lambda container_name: _running_inspection(), raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    def fake_run_docker(args: list[str], timeout: int = 60):
        docker_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="biomodstack-analytical-postgres\n", stderr="")

    monkeypatch.setattr(db_service, "_run_docker", fake_run_docker, raising=False)
    monkeypatch.setattr(db_service, "_run_compose", lambda args, timeout=60: compose_calls.append(args), raising=False)

    payload = db_service.run_db_service_action("start")

    assert payload["last_action"] == "start"
    assert payload["action_returncode"] == 0
    assert docker_calls == [["start", "biomodstack-analytical-postgres"]]
    assert compose_calls == []


def test_db_service_missing_container_compose_fallback_uses_no_build(monkeypatch) -> None:
    compose_calls: list[list[str]] = []

    monkeypatch.setattr(db_service, "_docker_available", lambda: (True, None), raising=False)
    monkeypatch.setattr(db_service, "_find_container_by_label_or_name", lambda: None, raising=False)
    monkeypatch.setattr(db_service, "_compose_available", lambda: (True, None), raising=False)
    monkeypatch.setattr(db_service, "_configured_service_names", lambda: ["bms-analytical-postgres"], raising=False)
    monkeypatch.setattr(db_service, "_inspect_container", lambda container_name: _running_inspection(), raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    def fake_run_compose(args: list[str], timeout: int = 60):
        compose_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="started\n", stderr="")

    monkeypatch.setattr(db_service, "_run_compose", fake_run_compose, raising=False)

    payload = db_service.run_db_service_action("start")

    assert payload["last_action"] == "start"
    assert payload["action_returncode"] == 0
    assert compose_calls == [["up", "-d", "--no-build", "bms-analytical-postgres"]]


def test_db_service_rejects_unsupported_action() -> None:
    try:
        db_service.run_db_service_action("purge")
    except ValueError as exc:
        assert "unsupported db-service action" in str(exc)
    else:  # pragma: no cover - guards the contract explicitly
        raise AssertionError("unsupported db-service action should raise ValueError")


def test_db_service_logs_tail_is_bounded_and_redacted(monkeypatch) -> None:
    docker_calls: list[list[str]] = []
    monkeypatch.setattr(db_service, "_docker_available", lambda: (True, None), raising=False)
    monkeypatch.setattr(db_service, "_find_container_by_label_or_name", _running_container, raising=False)
    monkeypatch.setattr(db_service, "_inspect_container", lambda container_name: _running_inspection(), raising=False)
    monkeypatch.setattr(db_service, "_logical_database_statuses", lambda: [], raising=False)

    def fake_run_docker(args: list[str], timeout: int = 60):
        docker_calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "BMS_ANALYTICAL_DATABASE_URL=postgresql+asyncpg://bms_assay:[REDACTED]@127.0.0.1:55432/bms_analytical_data\n"
                "POSTGRES_PASSWORD=[REDACTED]\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(db_service, "_run_docker", fake_run_docker, raising=False)

    payload = db_service.run_db_service_action("logs", tail=5000)

    assert docker_calls == [["logs", "--tail", "500", "biomodstack-analytical-postgres"]]
    assert payload["logs_tail"] == 500
    assert "bms_assay:[REDACTED]" not in payload["logs"]
    assert "POSTGRES_PASSWORD=[REDACTED]" in payload["logs"]
    assert "postgresql+asyncpg://bms_assay:***@127.0.0.1:55432/bms_analytical_data" in payload["logs"]

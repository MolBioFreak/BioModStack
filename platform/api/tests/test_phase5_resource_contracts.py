from __future__ import annotations

import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_services as services


def _profile_snapshot() -> dict[str, object]:
    return {
        "profile_path": "/tmp/biomodstack-test-profile.json",
        "resolved": {
            "data_root": "/tmp/biomodstack-data",
            "db_path": "/tmp/biomodstack-data/biomodstack.db",
        },
    }


def test_every_systemd_service_has_explicit_resource_boundaries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(services, "install_profile_snapshot", lambda **_: _profile_snapshot())

    rendered = {
        **services.render_user_units(project_root=tmp_path, runtime_mode=services.DEV_RUNTIME_MODE),
        **services.render_user_units(project_root=tmp_path, runtime_mode=services.CONTAINER_RUNTIME_MODE),
    }

    for service_name in services.all_runtime_service_names():
        unit = rendered[service_name]
        assert "MemoryHigh=" in unit, service_name
        assert "MemoryMax=" in unit, service_name
        assert "TasksMax=" in unit, service_name
        assert "LimitNOFILE=" in unit, service_name


def test_systemd_resource_boundaries_accept_documented_environment_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(services, "install_profile_snapshot", lambda **_: _profile_snapshot())
    monkeypatch.setenv("BMS_API_MEMORY_MAX", "24G")
    monkeypatch.setenv("BMS_API_TASKS_MAX", "3072")

    api_unit = services.render_user_units(
        project_root=tmp_path,
        runtime_mode=services.DEV_RUNTIME_MODE,
    )[services.API_SERVICE]

    assert "MemoryMax=24G" in api_unit
    assert "TasksMax=3072" in api_unit


def test_every_compose_service_has_memory_pid_and_nofile_boundaries() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))

    for service_name, service in compose["services"].items():
        assert "mem_limit" in service, service_name
        assert "pids_limit" in service, service_name
        assert service.get("ulimits", {}).get("nofile", {}).get("soft"), service_name
        assert service.get("ulimits", {}).get("nofile", {}).get("hard"), service_name

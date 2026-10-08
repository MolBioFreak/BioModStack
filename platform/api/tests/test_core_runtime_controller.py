from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROLLER_PATH = REPO_ROOT / "scripts" / "biomodstack_core_runtime_controller.py"


def load_controller() -> ModuleType:
    spec = importlib.util.spec_from_file_location("biomodstack_core_runtime_controller", CONTROLLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_failure_separates_non_transient_and_transient_failures() -> None:
    controller = load_controller()

    assert controller.classify_failure("Conflict. The container name /biomodstack-api is already in use") == "container-name-conflict"
    assert controller.classify_failure("listen tcp 0.0.0.0:8000: bind: address already in use") == "port-conflict"
    assert controller.classify_failure("password authentication failed for user bms_assay") == "credential-failure"
    assert controller.classify_failure("upstream reset the connection") == "transient-runtime-failure"


def test_parse_compose_ps_supports_json_array_and_json_lines() -> None:
    controller = load_controller()
    api = {"Service": "bms-api", "State": "running", "Health": "healthy"}
    web = {"Service": "bms-web", "State": "running", "Health": ""}

    assert controller.parse_compose_ps(json.dumps([api, web])) == {"bms-api": api, "bms-web": web}
    assert controller.parse_compose_ps(f"{json.dumps(api)}\n{json.dumps(web)}") == {"bms-api": api, "bms-web": web}


def test_compose_command_uses_the_profile_runtime_env_file(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    env_file = tmp_path / "core-runtime.env"
    env_file.write_text("BMS_STATE_DIR=/tmp/biomodstack-state\n", encoding="utf-8")
    monkeypatch.setenv("BMS_CORE_RUNTIME_ENV_FILE", str(env_file))

    assert controller.compose_command("config", "--quiet") == [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(controller.COMPOSE_FILE),
        "config",
        "--quiet",
    ]


def test_reserve_recovery_persists_and_enforces_budget(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    monkeypatch.setenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(controller, "MAX_RECOVERIES", 2)
    monkeypatch.setattr(controller, "RECOVERY_WINDOW_SECONDS", 300)

    first = controller.reserve_recovery("bms-api", now=1000.0)
    second = controller.reserve_recovery("bms-api", now=1001.0)

    assert first["recovery_attempt"] == 1
    assert second["recovery_attempt"] == 2
    with pytest.raises(controller.RuntimeBlockedError, match="Recovery budget exhausted") as raised:
        controller.reserve_recovery("bms-api", now=1002.0)
    assert raised.value.reason == "recovery-budget-exhausted"


def test_validate_storage_requires_explicit_existing_state_root(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    monkeypatch.delenv("BMS_STATE_DIR", raising=False)

    with pytest.raises(controller.RuntimeBlockedError, match="explicitly configured") as missing:
        controller.validate_storage()
    assert missing.value.reason == "missing-state-root"

    state_root = tmp_path / "stable-state"
    monkeypatch.setenv("BMS_STATE_DIR", str(state_root))
    with pytest.raises(controller.RuntimeBlockedError, match="does not exist"):
        controller.validate_storage()

    state_root.mkdir()
    assert controller.validate_storage() == {"state_dir": str(state_root.resolve())}


def test_preflight_blocks_fixed_name_owned_by_other_project(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    state_root = tmp_path / "stable-state"
    state_root.mkdir()
    monkeypatch.setenv("BMS_STATE_DIR", str(state_root))
    monkeypatch.setenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR", str(tmp_path / "supervisor"))

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "info"]:
            return SimpleNamespace(returncode=0, stdout="27.0.0\n", stderr="")
        if args[:3] == ["docker", "compose", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "inspect"]:
            if args[2] == "biomodstack-api":
                return SimpleNamespace(returncode=0, stdout="other-project|bms-api\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        raise AssertionError(args)

    monkeypatch.setattr(controller.subprocess, "run", fake_run)

    with pytest.raises(controller.RuntimeBlockedError, match="owned by another runtime") as raised:
        controller.run_preflight()
    assert raised.value.reason == "container-name-conflict"


def test_redact_removes_credential_values() -> None:
    controller = load_controller()
    rendered = controller.redact("password=super-secret token:abc123 authorization = Bearer-secret")

    assert "super-secret" not in rendered
    assert "abc123" not in rendered
    assert "Bearer-secret" not in rendered
    assert rendered.count("[REDACTED]") == 3


def test_controller_models_every_compose_service_and_strict_dependency() -> None:
    controller = load_controller()

    assert controller.ALL_SERVICES == (
        "bms-api",
        "bms-host-agent",
        "bms-cpu-power",
        "bms-web",
    )
    assert controller.SERVICE_DEPENDENCIES == {
        "bms-web": ("bms-api",),
    }
    assert set(controller.expected_container_names()) == set(controller.ALL_SERVICES)


def test_managed_services_ignores_unowned_compose_profiles(monkeypatch) -> None:
    controller = load_controller()
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)

    assert controller.managed_services() == controller.DEFAULT_SERVICES

    monkeypatch.setenv("COMPOSE_PROFILES", "gpu,external-addon")
    assert controller.managed_services() == controller.ALL_SERVICES


def test_dependency_readiness_requires_running_and_healthy_dependencies() -> None:
    controller = load_controller()
    assert controller.dependencies_ready(
        "bms-web",
        {"bms-api": {"State": "running", "Health": "healthy"}},
    )
    assert not controller.dependencies_ready(
        "bms-web",
        {"bms-api": {"State": "running", "Health": "starting"}},
    )
    assert not controller.dependencies_ready(
        "bms-web",
        {"bms-api": {"State": "exited", "Health": ""}},
    )
    assert not controller.dependencies_ready("bms-web", {})


def test_service_failure_rejects_non_running_or_non_healthy_rows() -> None:
    controller = load_controller()

    assert controller.service_failure("bms-api", None) == "missing"
    assert controller.service_failure("bms-api", {"State": "created", "Health": ""}) == "created"
    assert controller.service_failure("bms-api", {"State": "running", "Health": "starting"}) == "starting"
    assert controller.service_failure("bms-api", {"State": "running", "Health": "healthy"}) is None
    assert controller.service_failure("bms-web", {"State": "running", "Health": ""}) is None


def test_controller_source_has_no_unbounded_or_topology_mutating_recovery() -> None:
    source = CONTROLLER_PATH.read_text(encoding="utf-8")

    assert "--remove-orphans" not in source
    assert "while not _STOP_REQUESTED" in source
    assert "MAX_RECOVERIES" in source
    assert 'compose_command("restart", service)' in source
    assert 'DATABASE_SERVICE = "bms-db"' in source

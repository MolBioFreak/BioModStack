from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_services as services


def test_launch_preferences_default_to_browser_and_auto_open(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    prefs = services.load_launch_preferences()

    assert prefs == {
        "default_surface": services.BROWSER_LAUNCH_SURFACE,
        "auto_open_hosted_web_on_start": True,
    }


def test_launch_preferences_normalize_invalid_surface_to_browser(tmp_path: Path, monkeypatch) -> None:
    config_home = tmp_path / "config"
    prefs_path = config_home / "biomodstack" / "launch_preferences.json"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(
        '{"default_surface": "sideways", "auto_open_hosted_web_on_start": false}',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    prefs = services.load_launch_preferences()

    assert prefs == {
        "default_surface": services.BROWSER_LAUNCH_SURFACE,
        "auto_open_hosted_web_on_start": False,
    }


def test_build_launch_ui_command_defaults_to_container_electron_surface(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"

    command = services.build_launch_ui_command(project_root=project_root)

    assert command == [
        sys.executable,
        str(project_root / "scripts" / "launch_biomodstack_ui.py"),
        "--runtime",
        services.CONTAINER_RUNTIME_MODE,
        "--surface",
        services.ELECTRON_LAUNCH_SURFACE,
    ]


def test_build_launch_ui_command_rejects_unknown_surface(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"

    with pytest.raises(services.ServiceManagerError, match="Unsupported BioModStack launch surface"):
        services.build_launch_ui_command(project_root=project_root, surface="sideways")


def test_resolve_runtime_mode_defaults_to_container_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("BMS_RUNTIME_MODE", raising=False)

    assert services.resolve_runtime_mode() == services.CONTAINER_RUNTIME_MODE


def test_runtime_descriptor_for_dev_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: name == services.FRONTEND_SERVICE)
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="dev")

    assert descriptor["runtime_mode"] == "dev"
    assert descriptor["runtime_active"] is True
    assert descriptor["runtime_ready"] is True
    assert descriptor["frontend_url"] == "http://127.0.0.1:5173/"
    assert descriptor["browser_url"] == "http://127.0.0.1:5173/"
    assert descriptor["router_basename"] == "/"
    assert descriptor["supported_launch_surfaces"] == ["browser", "electron", "none"]
    assert descriptor["services"] == [
        {"name": services.FRONTEND_SERVICE, "active": True},
    ]


def test_runtime_descriptor_for_container_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name in {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE},
    )
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )
    monkeypatch.setattr(
        services,
        "install_profile_snapshot",
        lambda profile=None, project_root=None: {
            "profile_path": "/home/christian/.config/biomodstack/install_profile.json",
            "compat_env_path": "/home/christian/.biomodstack/env.sh",
            "core_runtime_env_path": "/home/christian/.config/biomodstack/core-runtime.env",
            "profile": {"data_root": "/srv/biomodstack"},
            "resolved": {
                "data_root": "/srv/biomodstack",
                "db_path": "/srv/biomodstack/biomodstack.db",
            },
        },
        raising=False,
    )
    monkeypatch.setattr(services, "electron_shell_available", lambda project_root=None: True, raising=False)

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["runtime_mode"] == "container"
    assert descriptor["runtime_active"] is True
    assert descriptor["runtime_ready"] is True
    assert descriptor["frontend_url"] == "http://127.0.0.1:18080/bms/"
    assert descriptor["browser_url"] == "http://127.0.0.1:18080/bms/"
    assert descriptor["router_basename"] == "/bms/"
    assert descriptor["services"] == [
        {"name": services.WORKFLOW_ADAPTER_SERVICE, "active": True},
        {"name": services.CORE_RUNTIME_SERVICE, "active": True},
    ]
    assert descriptor["health"] == {
        "adapter_ready": True,
        "api_ready": True,
        "frontend_ready": True,
    }
    assert descriptor["logs"] == [
        {
            "id": "api",
            "label": "API backend log",
            "path": "docker:biomodstack-api",
            "fallback_path": str(services.API_LOG),
        },
        {
            "id": "frontend",
            "label": "Frontend/web log",
            "path": "docker:biomodstack-web",
            "fallback_path": str(services.FRONTEND_LOG),
        },
        {
            "id": "workflow-adapter",
            "label": "Workflow adapter log",
            "path": str(services.WORKFLOW_ADAPTER_LOG),
        },
        {
            "id": "core-runtime",
            "label": "Container runtime log",
            "path": str(services.CORE_RUNTIME_LOG),
        }
    ]
    assert descriptor["install_profile"]["profile"]["data_root"] == "/srv/biomodstack"
    assert descriptor["paths"]["data_root"] == "/srv/biomodstack"
    assert descriptor["paths"]["db_path"] == "/srv/biomodstack/biomodstack.db"
    assert descriptor["electron_shell_available"] is True


def test_runtime_descriptor_container_rejects_legacy_api_listener(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name in {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE},
    )
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "runtime_api_listener_ownership",
        lambda project_root=None, runtime_mode=None: {
            "port": services.API_PORT,
            "checked": True,
            "ok": False,
            "status": "wrong-owner",
            "listeners": [{"pid": 9100, "owner": "legacy-dev-api", "matched_chain": [9101, 9102]}],
        },
    )

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["health"]["api_ready"] is False
    assert descriptor["runtime_ready"] is False
    assert descriptor["runtime_active"] is False
    assert descriptor["runtime_ownership"]["api"]["status"] == "wrong-owner"
    assert descriptor["runtime_ownership"]["api"]["listeners"][0]["owner"] == "legacy-dev-api"


def test_container_frontend_url_honors_install_profile_web_host_port(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "install_profile_snapshot",
        lambda profile=None, project_root=None: {"resolved": {"web_host_port": 19090}},
        raising=False,
    )

    assert services.runtime_frontend_url("container", project_root=project_root) == "http://127.0.0.1:19090/bms/"
    assert services.runtime_frontend_url("dev", project_root=project_root) == "http://127.0.0.1:5173/"


def test_runtime_frontend_urls_honor_configured_dev_and_prod_ports(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "install_profile_snapshot",
        lambda profile=None, project_root=None: {"resolved": {"dev_web_host_port": 5179, "web_host_port": 19090}},
        raising=False,
    )

    assert services.runtime_frontend_url("dev", project_root=project_root) == "http://127.0.0.1:5179/"
    assert services.runtime_frontend_url("container", project_root=project_root) == "http://127.0.0.1:19090/bms/"


def test_runtime_port_settings_preserve_profile_and_save_dev_and_prod_ports(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(
        services,
        "load_install_profile",
        lambda: {"data_root": "/srv/biomodstack", "web_host_port": 18080},
        raising=False,
    )
    monkeypatch.setattr(
        services,
        "save_install_profile",
        lambda payload, project_root=None: saved.append(dict(payload)) or dict(payload),
        raising=False,
    )
    monkeypatch.setattr(
        services,
        "install_profile_snapshot",
        lambda profile=None, project_root=None: {"resolved": {"dev_web_host_port": 5180, "web_host_port": 19090}},
        raising=False,
    )

    settings = services.save_runtime_port_settings(dev_web_host_port=5180, prod_web_host_port=19090, project_root=project_root)

    assert saved == [{"data_root": "/srv/biomodstack", "web_host_port": 19090, "dev_web_host_port": 5180}]
    assert settings == {
        "dev_web_host_port": 5180,
        "prod_web_host_port": 19090,
        "dev_url": "http://127.0.0.1:5180/",
        "prod_url": "http://127.0.0.1:19090/bms/",
    }


def test_render_user_units_exports_configured_dev_frontend_port(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "biomodstack"
    monkeypatch.setattr(
        services,
        "install_profile_snapshot",
        lambda profile=None, project_root=None: {"resolved": {"dev_web_host_port": 5179, "web_host_port": 18080}},
        raising=False,
    )

    units = services.render_user_units(project_root, runtime_mode="dev")

    assert "Environment=BMS_DEV_WEB_HOST_PORT=5179" in units[services.FRONTEND_SERVICE]


def test_start_runtime_target_supports_dev_prod_and_both_without_collapsing_channels(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, bool, bool]] = []
    monkeypatch.setattr(
        services,
        "start_all",
        lambda project_root=None, runtime_mode=None, skip_api_wait=False, skip_workflow_adapter_wait=False: calls.append(
            (runtime_mode or "missing", skip_api_wait, skip_workflow_adapter_wait)
        ),
    )

    services.start_runtime_target("dev", project_root=project_root)
    services.start_runtime_target("prod", project_root=project_root)
    services.start_runtime_target("both", project_root=project_root, skip_api_wait=True, skip_workflow_adapter_wait=True)

    assert calls == [
        ("dev", False, False),
        ("container", False, False),
        ("container", True, True),
        ("dev", True, True),
    ]


def test_runtime_descriptor_requires_all_expected_runtime_services_for_runtime_active(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name == services.WORKFLOW_ADAPTER_SERVICE,
    )
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: False)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )
    monkeypatch.setattr(services, "install_profile_snapshot", lambda profile=None, project_root=None: {}, raising=False)
    monkeypatch.setattr(services, "electron_shell_available", lambda project_root=None: False, raising=False)

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["services"] == [
        {"name": services.WORKFLOW_ADAPTER_SERVICE, "active": True},
        {"name": services.CORE_RUNTIME_SERVICE, "active": False},
    ]
    assert descriptor["runtime_active"] is False


def test_runtime_descriptor_requires_http_readiness_for_runtime_active(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name in {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE},
    )
    monkeypatch.setattr(
        services,
        "url_is_ready",
        lambda url, timeout_seconds=2.0: url != services.runtime_frontend_url("container", project_root=project_root),
    )
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )
    monkeypatch.setattr(services, "install_profile_snapshot", lambda profile=None, project_root=None: {}, raising=False)
    monkeypatch.setattr(services, "electron_shell_available", lambda project_root=None: False, raising=False)

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["services"] == [
        {"name": services.WORKFLOW_ADAPTER_SERVICE, "active": True},
        {"name": services.CORE_RUNTIME_SERVICE, "active": True},
    ]
    assert descriptor["health"] == {"adapter_ready": True, "api_ready": True, "frontend_ready": False}
    assert descriptor["runtime_ready"] is False
    assert descriptor["runtime_active"] is False


def test_service_is_active_degrades_to_false_when_systemctl_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"

    def raise_missing_systemctl(*args, **kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(services, "run_systemctl", raise_missing_systemctl)

    assert services.service_is_active(services.CORE_RUNTIME_SERVICE, project_root=project_root) is False



def test_operator_runtime_mode_prefers_fully_active_dev_runtime(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name in {services.API_SERVICE, services.FRONTEND_SERVICE},
    )

    assert services.operator_runtime_mode(project_root=project_root) == services.DEV_RUNTIME_MODE
    assert services.operator_frontend_url(project_root=project_root) == "http://127.0.0.1:5173/"



def test_operator_runtime_mode_falls_back_to_default_when_no_runtime_is_fully_active(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: False)
    monkeypatch.delenv("BMS_RUNTIME_MODE", raising=False)

    assert services.active_runtime_mode(project_root=project_root) is None
    assert services.operator_runtime_mode(project_root=project_root) == services.CONTAINER_RUNTIME_MODE
    assert services.operator_frontend_url(project_root=project_root) == "http://127.0.0.1:18080/bms/"



def test_render_user_units_include_repo_owned_execstart_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "biomodstack"
    units = services.render_user_units(project_root, runtime_mode="dev")

    assert set(units) == {services.API_SERVICE, services.FRONTEND_SERVICE, services.DEV_TARGET_UNIT}

    api_unit = units[services.API_SERVICE]
    assert f"Environment=BMS_HOME={project_root}" in api_unit
    assert "Environment=BMS_RUNTIME_MODE=dev" in api_unit
    assert "Environment=BMS_API_MODE=dev" in api_unit
    assert "Environment=BMS_CPU_POWER_STRICT=0" in api_unit
    assert f"ExecStartPre=/usr/bin/env python3 {project_root / 'scripts' / 'rotate_biomodstack_logs.py'}" in api_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_api.sh'}" in api_unit
    assert f"StandardOutput=append:{services.API_LOG}" in api_unit
    assert f"PartOf={services.DEV_TARGET_UNIT}" in api_unit

    frontend_unit = units[services.FRONTEND_SERVICE]
    assert "Environment=BMS_RUNTIME_MODE=dev" in frontend_unit
    assert "Environment=BMS_FRONTEND_MODE=dev" in frontend_unit
    assert f"ExecStartPre=/usr/bin/env python3 {project_root / 'scripts' / 'rotate_biomodstack_logs.py'}" in frontend_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_frontend.sh'}" in frontend_unit
    assert f"StandardOutput=append:{services.FRONTEND_LOG}" in frontend_unit
    assert f"PartOf={services.DEV_TARGET_UNIT}" in frontend_unit
    assert f"Wants={services.API_SERVICE}" not in frontend_unit

    target_unit = units[services.DEV_TARGET_UNIT]
    assert f"Wants={services.FRONTEND_SERVICE}" in target_unit
    assert services.API_SERVICE not in target_unit
    assert "WantedBy=default.target" in target_unit


def test_render_user_units_support_container_runtime_mode(tmp_path: Path) -> None:
    project_root = tmp_path / "biomodstack"
    units = services.render_user_units(project_root, runtime_mode="container")

    assert set(units) == {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT}

    adapter_unit = units[services.WORKFLOW_ADAPTER_SERVICE]
    assert f"Environment=BMS_HOME={project_root}" in adapter_unit
    assert "Environment=BMS_RUNTIME_MODE=container" in adapter_unit
    assert "Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1" in adapter_unit
    assert f"ExecStartPre=/usr/bin/env python3 {project_root / 'scripts' / 'rotate_biomodstack_logs.py'}" in adapter_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_workflow_adapter.sh'}" in adapter_unit
    assert f"StandardOutput=append:{services.WORKFLOW_ADAPTER_LOG}" in adapter_unit
    assert f"PartOf={services.TARGET_UNIT}" in adapter_unit

    runtime_unit = units[services.CORE_RUNTIME_SERVICE]
    assert f"Environment=BMS_HOME={project_root}" in runtime_unit
    assert "Environment=BMS_RUNTIME_MODE=container" in runtime_unit
    assert "Type=oneshot" in runtime_unit
    assert "RemainAfterExit=yes" in runtime_unit
    assert f"ExecStartPre=/usr/bin/env python3 {project_root / 'scripts' / 'rotate_biomodstack_logs.py'}" in runtime_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_core_runtime.sh'}" in runtime_unit
    assert f"ExecStop={project_root / 'scripts' / 'run_biomodstack_core_runtime.sh'} down" in runtime_unit
    assert f"StandardOutput=append:{services.CORE_RUNTIME_LOG}" in runtime_unit
    assert f"PartOf={services.TARGET_UNIT}" in runtime_unit

    target_unit = units[services.TARGET_UNIT]
    assert f"Wants={services.WORKFLOW_ADAPTER_SERVICE} {services.CORE_RUNTIME_SERVICE}" in target_unit
    assert services.API_SERVICE not in target_unit
    assert services.FRONTEND_SERVICE not in target_unit


def test_install_user_units_writes_expected_files(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    user_dir = tmp_path / "user-systemd"

    written = services.install_user_units(project_root=project_root, systemd_dir=user_dir)

    assert {path.name for path in written} == {
        services.WORKFLOW_ADAPTER_SERVICE,
        services.CORE_RUNTIME_SERVICE,
        services.TARGET_UNIT,
    }
    for path in written:
        assert path.exists()
        assert path.read_text(encoding="utf-8") == services.render_user_units(project_root)[path.name]


def test_rotate_log_file_bounds_append_only_service_logs(tmp_path: Path) -> None:
    log_path = tmp_path / "service.log"
    log_path.write_text("a" * 32, encoding="utf-8")
    (tmp_path / "service.log.1").write_text("old", encoding="utf-8")

    assert services.rotate_log_file(log_path, max_bytes=10, backup_count=2) is True

    assert log_path.read_text(encoding="utf-8") == ""
    assert (tmp_path / "service.log.1").read_text(encoding="utf-8") == "a" * 32
    assert (tmp_path / "service.log.2").read_text(encoding="utf-8") == "old"


def test_biomodstack_api_process_detection_uses_cmdline_or_cwd(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    api_dir = project_root / "platform" / "api"

    assert services.is_biomodstack_api_process(
        cmdline=f"/usr/bin/python3 /home/dalab/.local/bin/uvicorn main:app --port {services.API_PORT} --host 127.0.0.1",
        cwd=str(api_dir),
        project_root=project_root,
    )
    assert services.is_biomodstack_api_process(
        cmdline=f"uv run uvicorn main:app --port {services.API_PORT} --host 127.0.0.1 --reload-dir {api_dir}",
        cwd=None,
        project_root=project_root,
    )
    assert not services.is_biomodstack_api_process(
        cmdline="uvicorn other:app --port 8000 --host 127.0.0.1",
        cwd=str(api_dir),
        project_root=project_root,
    )


def test_biomodstack_frontend_process_detection_uses_cmdline_or_cwd(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    frontend_dir = project_root / "platform" / "frontend"

    assert services.is_biomodstack_frontend_process(
        cmdline=f"node {frontend_dir}/node_modules/vite/bin/vite.js --host 127.0.0.1 --port {services.FRONTEND_PORT}",
        cwd=None,
        project_root=project_root,
    )
    assert services.is_biomodstack_frontend_process(
        cmdline=f"npm run dev -- --host 127.0.0.1 --port {services.FRONTEND_PORT}",
        cwd=str(frontend_dir),
        project_root=project_root,
    )
    assert not services.is_biomodstack_frontend_process(
        cmdline="python -m http.server 5173",
        cwd=str(frontend_dir),
        project_root=project_root,
    )


def test_listener_pids_prefers_lsof_listeners_over_fuser_matches(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["lsof", "-ti"]:
            return SimpleNamespace(stdout="", stderr="", returncode=1)
        if args == ["fuser", "-n", "tcp", str(services.API_PORT)]:
            return SimpleNamespace(stdout=f"{services.API_PORT}/tcp: 9100\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    assert services.listener_pids(services.API_PORT) == []
    assert calls[0] == ["lsof", "-ti", f"tcp:{services.API_PORT}", "-sTCP:LISTEN"]



def test_listener_pids_falls_back_to_fuser_when_lsof_missing(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["lsof", "-ti"]:
            raise FileNotFoundError("lsof")
        if args == ["fuser", "-n", "tcp", str(services.API_PORT)]:
            return SimpleNamespace(stdout=f"{services.API_PORT}/tcp: 9100 9101\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    assert services.listener_pids(services.API_PORT) == [9100, 9101]
    assert calls == [
        ["lsof", "-ti", f"tcp:{services.API_PORT}", "-sTCP:LISTEN"],
        ["fuser", "-n", "tcp", str(services.API_PORT)],
    ]


def test_runtime_api_listener_ownership_classifies_legacy_dev_api_as_wrong_owner(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "listener_pids", lambda port: [9100])
    monkeypatch.setattr(services, "pid_is_biomodstack_runtime_container", lambda pid, kind, project_root=None: False)
    monkeypatch.setattr(services, "matching_process_chain", lambda pid, matcher, project_root=None: [9101, 9102])

    ownership = services.runtime_api_listener_ownership(project_root=project_root, runtime_mode="container")

    assert ownership["ok"] is False
    assert ownership["status"] == "wrong-owner"
    assert ownership["listeners"] == [{"pid": 9100, "owner": "legacy-dev-api", "matched_chain": [9101, 9102]}]


def test_runtime_api_listener_ownership_accepts_current_core_runtime_api(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setattr(services, "listener_pids", lambda port: [services.os.getpid()])

    ownership = services.runtime_api_listener_ownership(project_root=project_root, runtime_mode="container")

    assert ownership["ok"] is True
    assert ownership["status"] == "ok"
    assert ownership["listeners"][0]["owner"] == "managed-container-api"


def test_cleanup_legacy_listener_kills_matching_ancestor_chain(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    api_dir = project_root / "platform" / "api"

    monkeypatch.setattr(services, "listener_pids", lambda port: [9100])
    monkeypatch.setattr(services, "pid_is_biomodstack_runtime_container", lambda pid, kind, project_root=None: False)

    cmdlines = {
        9100: "python -c 'from multiprocessing.spawn import spawn_main'",
        9101: f"/usr/bin/python3 /home/dalab/.local/bin/uvicorn main:app --port {services.API_PORT} --host 127.0.0.1 --reload-dir {api_dir}",
        9102: f"uv run uvicorn main:app --port {services.API_PORT} --host 127.0.0.1 --reload-dir {api_dir}",
    }
    cwd = {9100: None, 9101: None, 9102: None}
    ppids = {9100: 9101, 9101: 9102, 9102: 1}
    killed: list[int] = []

    monkeypatch.setattr(services, "read_pid_cmdline", lambda pid: cmdlines[pid])
    monkeypatch.setattr(services, "read_pid_cwd", lambda pid: cwd[pid])
    monkeypatch.setattr(services, "read_pid_ppid", lambda pid: ppids.get(pid))
    monkeypatch.setattr(services, "_terminate_pid", lambda pid, grace_seconds=8.0: killed.append(pid))

    services.cleanup_legacy_listener("api", project_root=project_root)

    assert killed == [9102, 9101]


def test_cleanup_legacy_listener_skips_core_runtime_container_listener(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    killed: list[int] = []

    monkeypatch.setattr(services, "listener_pids", lambda port: [4175129])
    monkeypatch.setattr(
        services,
        "pid_is_biomodstack_runtime_container",
        lambda pid, kind, project_root=None: pid == 4175129 and kind == "api",
    )
    monkeypatch.setattr(
        services,
        "matching_process_chain",
        lambda pid, matcher, project_root=None: (_ for _ in ()).throw(AssertionError("container listener should not be chain-matched")),
    )
    monkeypatch.setattr(services, "_terminate_pid", lambda pid, grace_seconds=8.0: killed.append(pid))

    services.cleanup_legacy_listener("api", project_root=project_root)

    assert killed == []


def test_pid_is_biomodstack_runtime_container_matches_compose_labels(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    container_id = "306c092ef0e95b29e872926d620d79b0aed489c1544ed70b4e1b0f0d3d815869"
    labels_json = (
        '{'
        '"com.docker.compose.project.working_dir": "' + str(project_root) + '",'
        '"com.docker.compose.project.config_files": "' + str(project_root / "compose.core-runtime.yml") + '",'
        '"com.docker.compose.service": "bms-api"'
        '}'
    )

    monkeypatch.setattr(
        services,
        "read_pid_cgroup",
        lambda pid: f"1:net_cls:/\n0::/system.slice/docker-{container_id}.scope\n",
    )

    def fake_run(args, **kwargs):
        assert args == ["docker", "inspect", container_id, "--format", "{{json .Config.Labels}}"]
        return SimpleNamespace(stdout=labels_json, stderr="", returncode=0)

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    assert services.pid_is_biomodstack_runtime_container(4175129, "api", project_root=project_root)
    assert not services.pid_is_biomodstack_runtime_container(4175129, "frontend", project_root=project_root)


def test_start_all_container_mode_skips_legacy_cleanup_when_runtime_container_listener_already_present(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)),
    )
    monkeypatch.setattr(services, "service_is_active", lambda service_name, project_root=None: False)
    monkeypatch.setattr(
        services,
        "listener_pids",
        lambda port: [4175129] if port == services.API_PORT else [],
    )
    monkeypatch.setattr(
        services,
        "pid_is_biomodstack_runtime_container",
        lambda pid, kind, project_root=None: pid == 4175129 and kind == "api",
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: (_ for _ in ()).throw(AssertionError(f"cleanup should be skipped for {kind}")),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("enable", services.TARGET_UNIT)),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("systemctl", ("start", services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", (services.WORKFLOW_ADAPTER_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.FRONTEND_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_start_all_dev_mode_keeps_container_runtime_and_starts_only_dev_frontend_when_api_is_ready(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(services, "cleanup_legacy_listener", lambda kind, project_root=None: calls.append(("cleanup", kind)))
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: url == services.API_HEALTH_URL)
    monkeypatch.setattr(services, "service_is_active", lambda service_name, project_root=None: False)
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(services, "wait_for_http", lambda url, timeout_seconds=30.0: calls.append(("wait", url)))

    services.start_all(project_root=project_root, runtime_mode="dev")

    assert calls == [
        ("ensure", "dev"),
        ("cleanup", "frontend"),
        ("systemctl", ("start", services.FRONTEND_SERVICE, services.DEV_TARGET_UNIT)),
        ("wait", services.API_HEALTH_URL),
        ("wait", "http://127.0.0.1:5173/"),
    ]


def test_start_all_dev_mode_skips_legacy_cleanup_when_runtime_already_active(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda service_name, project_root=None: service_name in {services.API_SERVICE, services.FRONTEND_SERVICE},
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: (_ for _ in ()).throw(AssertionError(f"cleanup should be skipped for {kind}")),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(services, "wait_for_http", lambda url, timeout_seconds=30.0: calls.append(("wait", url)))

    services.start_all(project_root=project_root, runtime_mode="dev")

    assert calls == [
        ("ensure", "dev"),
        ("systemctl", ("start", services.FRONTEND_SERVICE, services.DEV_TARGET_UNIT)),
        ("wait", services.API_HEALTH_URL),
        ("wait", services.runtime_frontend_url("dev")),
    ]


def test_status_lines_keep_existing_container_human_output(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: None)
    monkeypatch.setattr(
        services,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "runtime_mode": "container",
            "runtime_active": False,
            "runtime_ready": False,
            "api_url": "http://127.0.0.1:8000",
            "frontend_url": "http://127.0.0.1:18080/bms/",
            "health": {"adapter_ready": True, "api_ready": True, "frontend_ready": False},
            "logs": [
                {"id": "workflow-adapter", "label": "Workflow adapter log", "path": str(services.WORKFLOW_ADAPTER_LOG)},
                {"id": "runtime", "label": "Core runtime log", "path": str(services.CORE_RUNTIME_LOG)},
            ],
        },
    )

    lines = services.status_lines(project_root=project_root, runtime_mode="container")

    assert lines == [
        f"Runtime: inactive ({services.CORE_RUNTIME_SERVICE})",
        f"Workflow adapter: ready ({services.WORKFLOW_ADAPTER_HEALTH_URL})",
        f"API: ready ({services.API_HEALTH_URL})",
        "Frontend: not ready (http://127.0.0.1:18080/bms/)",
        f"Workflow adapter log: {services.WORKFLOW_ADAPTER_LOG}",
        f"Runtime log: {services.CORE_RUNTIME_LOG}",
    ]


def test_status_lines_do_not_mutate_runtime_state(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: (_ for _ in ()).throw(AssertionError("ensure_user_units should not run during status")),
    )
    monkeypatch.setattr(
        services,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "runtime_mode": "dev",
            "services": [
                {"name": services.API_SERVICE, "active": True},
                {"name": services.FRONTEND_SERVICE, "active": False},
            ],
            "health": {"api_ready": True, "frontend_ready": False},
            "frontend_url": "http://127.0.0.1:5173/",
            "logs": [
                {"id": "api", "label": "API log", "path": str(services.API_LOG)},
                {"id": "frontend", "label": "Frontend log", "path": str(services.FRONTEND_LOG)},
            ],
        },
    )

    lines = services.status_lines(project_root=project_root, runtime_mode="dev")

    assert lines == [
        f"API: ready ({services.API_HEALTH_URL})",
        f"Frontend: not ready ({services.FRONTEND_SERVICE} unit inactive; http://127.0.0.1:5173/)",
        f"API log: {services.API_LOG}",
        f"Frontend log: {services.FRONTEND_LOG}",
    ]


def test_start_all_container_mode_skips_legacy_cleanup_when_runtime_already_active(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)),
    )
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda service_name, project_root=None: service_name in {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE},
    )
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: (_ for _ in ()).throw(AssertionError(f"cleanup should be skipped for {kind}")),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("enable", services.TARGET_UNIT)),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("systemctl", ("start", services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", (services.WORKFLOW_ADAPTER_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.FRONTEND_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_start_all_container_mode_restarts_api_web_when_units_active_but_http_down(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(services, "ensure_target_enabled", lambda root, runtime_mode=None: calls.append(("enable", runtime_mode)))
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda service_name, project_root=None: service_name in {services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE},
    )
    monkeypatch.setattr(services, "should_cleanup_legacy_listeners_before_start", lambda runtime_mode=None, project_root=None: False)
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: False)
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "run_core_runtime_script",
        lambda *args, **kwargs: calls.append(("core-script", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("enable", "container"),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("core-script", ("up", "bms-api", "bms-web")),
        ("wait", (services.WORKFLOW_ADAPTER_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.FRONTEND_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_start_all_container_mode_cleans_legacy_listeners_before_first_start(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)),
    )
    monkeypatch.setattr(services, "service_is_active", lambda service_name, project_root=None: False)
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: calls.append(("cleanup", kind)),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("enable", services.TARGET_UNIT)),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("cleanup", "api"),
        ("systemctl", ("start", services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", (services.WORKFLOW_ADAPTER_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.FRONTEND_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_restart_all_container_mode_enables_target_before_restart(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: calls.append(("cleanup", kind)),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.restart_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("enable", services.TARGET_UNIT)),
        ("systemctl", ("stop", services.TARGET_UNIT)),
        (
            "systemctl",
            (
                "stop",
                services.API_SERVICE,
                services.WORKFLOW_ADAPTER_SERVICE,
                services.CORE_RUNTIME_SERVICE,
            ),
        ),
        ("cleanup", "api"),
        ("systemctl", ("start", services.WORKFLOW_ADAPTER_SERVICE, services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", (services.WORKFLOW_ADAPTER_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
        ("wait", (services.FRONTEND_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_core_runtime_script_start_does_not_rebuild_images() -> None:
    script = (services.get_project_root() / "scripts" / "run_biomodstack_core_runtime.sh").read_text(encoding="utf-8")

    assert "up -d --remove-orphans" in script
    assert "up --build --remove-orphans" not in script
    assert "up -d --build --remove-orphans" in script
    assert "cleanup_legacy_api_listener_if_needed" in script
    assert "cleanup_legacy_listener('api', root)" in script
    assert "stop)" in script
    assert "rebuild|build)" in script


def test_api_runtime_image_contains_ssh_client_for_bioxp_lifecycle_actions() -> None:
    dockerfile = (services.get_project_root() / "docker" / "api.Dockerfile").read_text(encoding="utf-8")

    assert "openssh-client" in dockerfile


def test_core_runtime_compose_bounds_docker_json_logs() -> None:
    compose = (services.get_project_root() / "compose.core-runtime.yml").read_text(encoding="utf-8")

    assert "x-bms-json-logging: &bms-json-logging" in compose
    assert "max-size: ${BMS_DOCKER_LOG_MAX_SIZE:-10m}" in compose
    assert "max-file: \"${BMS_DOCKER_LOG_MAX_FILE:-5}\"" in compose
    assert compose.count("logging: *bms-json-logging") >= 5


def test_stop_all_container_mode_is_prod_scoped(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        services,
        "ensure_user_units",
        lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)),
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: (_ for _ in ()).throw(AssertionError(f"container stop should not clean dev {kind}")),
    )

    services.stop_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("stop", services.TARGET_UNIT)),
        (
            "systemctl",
            (
                "stop",
                services.WORKFLOW_ADAPTER_SERVICE,
                services.CORE_RUNTIME_SERVICE,
            ),
        ),
    ]


def test_stop_all_dev_mode_stops_dev_frontend_without_touching_container_api(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(services, "cleanup_legacy_listener", lambda kind, project_root=None: calls.append(("cleanup", kind)))
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: False)

    services.stop_all(project_root=project_root, runtime_mode="dev")

    assert calls == [
        ("ensure", "dev"),
        ("systemctl", ("stop", services.DEV_TARGET_UNIT, services.FRONTEND_SERVICE)),
        ("cleanup", "frontend"),
    ]


def test_start_api_container_mode_stops_local_dev_api_then_starts_container_service(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(services, "ensure_target_enabled", lambda project_root=None, runtime_mode=None: calls.append(("enable", runtime_mode)))
    monkeypatch.setattr(
        services,
        "listener_pids",
        lambda port: [9012] if port == services.API_PORT else [],
    )
    monkeypatch.setattr(
        services,
        "pid_is_biomodstack_runtime_container",
        lambda pid, kind, project_root=None: False,
    )
    monkeypatch.setattr(
        services,
        "service_is_active",
        lambda name, project_root=None: name == services.API_SERVICE,
    )
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: calls.append(("cleanup", kind)),
    )
    monkeypatch.setattr(
        services,
        "run_core_runtime_script",
        lambda *args, **kwargs: calls.append(("compose", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "wait_for_http",
        lambda url, timeout_seconds=30.0: calls.append(("wait", (url, timeout_seconds))),
    )

    services.start_api(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("enable", "container"),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("cleanup", "api"),
        ("compose", ("up", "--no-deps", "bms-api")),
        ("wait", (services.API_HEALTH_URL, services.CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS)),
    ]


def test_stop_api_container_mode_stops_only_container_api_service(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(
        services,
        "run_core_runtime_script",
        lambda *args, **kwargs: calls.append(("compose", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    services.stop_api(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("compose", ("stop", "bms-api")),
    ]


def test_start_api_dev_mode_rejects_container_owned_port(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: None)
    monkeypatch.setattr(services, "listener_pids", lambda port: [9012] if port == services.API_PORT else [])
    monkeypatch.setattr(
        services,
        "pid_is_biomodstack_runtime_container",
        lambda pid, kind, project_root=None: True,
    )
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: False)

    with pytest.raises(services.ServiceManagerError, match="core runtime container API owns port 8000"):
        services.start_api(project_root=project_root, runtime_mode="dev")


def test_stop_api_dev_mode_stops_systemd_service_and_cleans_listener(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "cleanup_legacy_listener",
        lambda kind, project_root=None: calls.append(("cleanup", kind)),
    )

    services.stop_api(project_root=project_root, runtime_mode="dev")

    assert calls == [
        ("ensure", "dev"),
        ("systemctl", ("stop", services.API_SERVICE)),
        ("cleanup", "api"),
    ]

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


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


def test_runtime_descriptor_for_dev_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: name == services.API_SERVICE)
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
    assert descriptor["frontend_url"] == "http://127.0.0.1:5173/"
    assert descriptor["browser_url"] == "http://127.0.0.1:5173/"
    assert descriptor["router_basename"] == "/"
    assert descriptor["supported_launch_surfaces"] == ["browser", "electron", "none"]
    assert descriptor["services"] == [
        {"name": services.API_SERVICE, "active": True},
        {"name": services.FRONTEND_SERVICE, "active": False},
    ]


def test_runtime_descriptor_for_container_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: name == services.CORE_RUNTIME_SERVICE)
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["runtime_mode"] == "container"
    assert descriptor["frontend_url"] == "http://127.0.0.1:5173/bms/"
    assert descriptor["browser_url"] == "http://127.0.0.1:5173/bms/"
    assert descriptor["router_basename"] == "/bms/"
    assert descriptor["logs"] == [
        {
            "id": "runtime",
            "label": "Core runtime log",
            "path": str(services.CORE_RUNTIME_LOG),
        }
    ]


def test_render_user_units_include_repo_owned_execstart_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "biomodstack"
    units = services.render_user_units(project_root, runtime_mode="dev")

    assert set(units) == {services.API_SERVICE, services.FRONTEND_SERVICE, services.TARGET_UNIT}

    api_unit = units[services.API_SERVICE]
    assert f"Environment=BMS_HOME={project_root}" in api_unit
    assert "Environment=BMS_RUNTIME_MODE=dev" in api_unit
    assert "Environment=BMS_API_MODE=dev" in api_unit
    assert "Environment=BMS_CPU_POWER_STRICT=0" in api_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_api.sh'}" in api_unit
    assert f"StandardOutput=append:{services.API_LOG}" in api_unit
    assert f"PartOf={services.TARGET_UNIT}" in api_unit

    frontend_unit = units[services.FRONTEND_SERVICE]
    assert "Environment=BMS_RUNTIME_MODE=dev" in frontend_unit
    assert "Environment=BMS_FRONTEND_MODE=dev" in frontend_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_frontend.sh'}" in frontend_unit
    assert f"StandardOutput=append:{services.FRONTEND_LOG}" in frontend_unit
    assert f"Wants={services.API_SERVICE}" in frontend_unit

    target_unit = units[services.TARGET_UNIT]
    assert f"Wants={services.API_SERVICE} {services.FRONTEND_SERVICE}" in target_unit
    assert "WantedBy=default.target" in target_unit


def test_render_user_units_support_container_runtime_mode(tmp_path: Path) -> None:
    project_root = tmp_path / "biomodstack"
    units = services.render_user_units(project_root, runtime_mode="container")

    assert set(units) == {services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT}

    runtime_unit = units[services.CORE_RUNTIME_SERVICE]
    assert f"Environment=BMS_HOME={project_root}" in runtime_unit
    assert "Environment=BMS_RUNTIME_MODE=container" in runtime_unit
    assert f"ExecStart={project_root / 'scripts' / 'run_biomodstack_core_runtime.sh'}" in runtime_unit
    assert f"ExecStop={project_root / 'scripts' / 'run_biomodstack_core_runtime.sh'} down" in runtime_unit
    assert f"StandardOutput=append:{services.CORE_RUNTIME_LOG}" in runtime_unit
    assert f"PartOf={services.TARGET_UNIT}" in runtime_unit

    target_unit = units[services.TARGET_UNIT]
    assert f"Wants={services.CORE_RUNTIME_SERVICE}" in target_unit
    assert services.API_SERVICE not in target_unit
    assert services.FRONTEND_SERVICE not in target_unit


def test_install_user_units_writes_expected_files(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    user_dir = tmp_path / "user-systemd"

    written = services.install_user_units(project_root=project_root, systemd_dir=user_dir)

    assert {path.name for path in written} == {
        services.API_SERVICE,
        services.FRONTEND_SERVICE,
        services.TARGET_UNIT,
    }
    for path in written:
        assert path.exists()
        assert path.read_text(encoding="utf-8") == services.render_user_units(project_root)[path.name]


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


def test_listener_pids_parses_fuser_output(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        assert args == ["fuser", "-n", "tcp", str(services.API_PORT)]
        return SimpleNamespace(stdout=f"{services.API_PORT}/tcp: 9100 9101\n", stderr="", returncode=0)

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    assert services.listener_pids(services.API_PORT) == [9100, 9101]


def test_cleanup_legacy_listener_kills_matching_ancestor_chain(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    api_dir = project_root / "platform" / "api"

    monkeypatch.setattr(services, "listener_pids", lambda port: [9100])

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


def test_start_all_dev_mode_waits_for_dev_frontend_url(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(services, "cleanup_legacy_listener", lambda kind, project_root=None: calls.append(("cleanup", kind)))
    monkeypatch.setattr(
        services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        services,
        "should_cleanup_legacy_listeners_before_start",
        lambda runtime_mode=None, project_root=None: True,
    )
    monkeypatch.setattr(services, "wait_for_http", lambda url, timeout_seconds=30.0: calls.append(("wait", url)))

    services.start_all(project_root=project_root, runtime_mode="dev")

    assert calls[-1] == ("wait", "http://127.0.0.1:5173/")


def test_status_lines_keep_existing_container_human_output(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: None)
    monkeypatch.setattr(
        services,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "runtime_mode": "container",
            "runtime_active": True,
            "api_url": "http://127.0.0.1:8000",
            "frontend_url": "http://127.0.0.1:5173/bms/",
            "health": {"api_ready": True, "frontend_ready": False},
            "logs": [{"id": "runtime", "label": "Core runtime log", "path": str(services.CORE_RUNTIME_LOG)}],
        },
    )

    lines = services.status_lines(project_root=project_root, runtime_mode="container")

    assert lines == [
        f"Runtime: active ({services.CORE_RUNTIME_SERVICE})",
        f"API: ready ({services.API_HEALTH_URL})",
        "Frontend: not ready (http://127.0.0.1:5173/bms/)",
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
        f"API: active ({services.API_SERVICE})",
        f"Frontend: inactive ({services.FRONTEND_SERVICE})",
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
    monkeypatch.setattr(services, "service_is_active", lambda service_name, project_root=None: service_name == services.CORE_RUNTIME_SERVICE)
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

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("stop", services.API_SERVICE, services.FRONTEND_SERVICE)),
        ("systemctl", ("start", services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", services.API_HEALTH_URL),
        ("wait", services.FRONTEND_URL),
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
    monkeypatch.setattr(services, "wait_for_http", lambda url, timeout_seconds=30.0: calls.append(("wait", url)))

    services.start_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("stop", services.API_SERVICE, services.FRONTEND_SERVICE)),
        ("cleanup", "api"),
        ("cleanup", "frontend"),
        ("systemctl", ("start", services.CORE_RUNTIME_SERVICE, services.TARGET_UNIT)),
        ("wait", services.API_HEALTH_URL),
        ("wait", services.FRONTEND_URL),
    ]


def test_stop_all_container_mode_stops_both_runtime_flavors(monkeypatch, tmp_path: Path) -> None:
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

    services.stop_all(project_root=project_root, runtime_mode="container")

    assert calls == [
        ("ensure", "container"),
        ("systemctl", ("stop", services.TARGET_UNIT)),
        ("systemctl", ("stop", services.API_SERVICE, services.FRONTEND_SERVICE, services.CORE_RUNTIME_SERVICE)),
        ("cleanup", "api"),
        ("cleanup", "frontend"),
    ]

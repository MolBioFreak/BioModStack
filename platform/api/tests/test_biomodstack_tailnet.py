from __future__ import annotations
import json
import os
from pathlib import Path

import pytest

import biomodstack_tailnet as tailnet

REAL_SERVICE_OWNERSHIP_SNAPSHOT = tailnet._service_ownership_snapshot
REAL_RESTORE_SERVICE_OWNERSHIP = tailnet._restore_service_ownership
REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER = tailnet._validated_runtime_container_listener
REAL_VALIDATED_DEVELOPMENT_FRONTEND_LISTENER = tailnet._validated_development_frontend_listener


def _api_process_report(host_pid: int, cgroup: str) -> dict[str, object]:
    return {
        "pid": host_pid, "cgroup": cgroup, "container_pid": 1,
        "parent_container_pid": 0, "executable": "/usr/local/bin/python3.10",
        "argv": [
            "/app/platform/api/.venv/bin/python", "/app/platform/api/.venv/bin/uvicorn",
            "main:app", "--host", "127.0.0.1", "--port", "8000",
        ],
        "cwd": "/app/platform/api", "uid": 1000,
    }


def _nginx_process_report(
    host_pid: int, container_pid: int, cgroup: str,
) -> dict[str, object]:
    master = container_pid == 1
    return {
        "pid": host_pid, "cgroup": cgroup, "container_pid": container_pid,
        "parent_container_pid": 0 if master else 1,
        "executable": "/usr/sbin/nginx",
        "argv": [
            "nginx: master process nginx -g daemon off;" if master else "nginx: worker process"
        ],
        "cwd": "/", "uid": 0 if master else 101,
    }


@pytest.fixture(autouse=True)
def _isolate_live_service_ownership(monkeypatch) -> None:
    empty = tailnet.ServiceOwnershipSnapshot(files={}, active={})
    monkeypatch.setattr(tailnet, "_service_ownership_snapshot", lambda root, spec: empty)
    monkeypatch.setattr(tailnet, "_restore_service_ownership", lambda snapshot, root, mutations=None: None)
    monkeypatch.setattr(
        tailnet,
        "_validated_runtime_container_listener",
        lambda runtime_report, *, container_name, port: {
            "container_name": container_name,
            "port": port,
            "listener_reports": tailnet._pid_report(port),
        },
    )
    monkeypatch.setattr(tailnet, "wait_for_http", lambda url, timeout_seconds=30.0: None)
    monkeypatch.setattr(
        tailnet,
        "_validated_workflow_adapter_listener",
        lambda root, runtime_revision: {"listener_reports": []},
    )
    monkeypatch.setattr(
        tailnet,
        "_validated_development_frontend_listener",
        lambda spec, root: {"listener_reports": tailnet._pid_report(spec.frontend_port)},
    )


def test_host_systemd_path_ignores_xdg_config_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cordova-build-config"))
    assert tailnet._host_user_systemd_dir() == Path.home() / ".config" / "systemd" / "user"


def test_git_revision_rejects_dirty_or_nested_selector_source(tmp_path: Path) -> None:
    tailnet._run(["git", "-C", str(tmp_path), "init", "--quiet"])
    tracked = tmp_path / "selector.py"
    tracked.write_text("sealed = True\n", encoding="utf-8")
    tailnet._run(["git", "-C", str(tmp_path), "add", "selector.py"])
    tailnet._run([
        "git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
        "user.email=test@example.invalid", "commit", "--quiet", "-m", "seal"
    ])
    assert len(tailnet._git_revision(tmp_path)) == 40
    (tmp_path / "nested").mkdir()
    with pytest.raises(tailnet.TailnetEnvironmentError, match="canonical Git root"):
        tailnet._git_revision(tmp_path / "nested")
    tracked.write_text("sealed = False\n", encoding="utf-8")
    with pytest.raises(tailnet.TailnetEnvironmentError, match="uncommitted changes"):
        tailnet._git_revision(tmp_path)


def test_adapter_policy_pins_and_verifies_loopback_binding(monkeypatch, tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(tailnet, "_tailnet_owner_login", lambda: "owner@example.com")
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "a" * 40)
    tailnet._install_adapter_control_policy(tmp_path, "b" * 40)
    dropin = systemd_dir / f"{tailnet.WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    assert "Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1" in dropin.read_text()
    assert f"Environment=BMS_BUILD_SHA={'b' * 40}" in dropin.read_text()

    monkeypatch.setattr(tailnet, "_pid_report", lambda port: [{"pid": 123}])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [77])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {77: [123]})
    values = {
        "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS": "owner@example.com",
        "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS": "127.0.0.1,::1",
        "BMS_WORKFLOW_ADAPTER_BIND_HOST": "127.0.0.1",
    }
    monkeypatch.setattr(tailnet, "_pid_environment_value", lambda pid, key: values.get(key))
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    assert tailnet._adapter_control_policy_matches("owner@example.com") is True
    values["BMS_WORKFLOW_ADAPTER_BIND_HOST"] = "0.0.0.0"
    assert tailnet._adapter_control_policy_matches("owner@example.com") is False
    values["BMS_WORKFLOW_ADAPTER_BIND_HOST"] = "127.0.0.1"
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"0.0.0.0"})
    assert tailnet._adapter_control_policy_matches("owner@example.com") is False


def test_adapter_identity_and_policy_must_match_one_listener(monkeypatch, tmp_path: Path) -> None:
    revision = "a" * 40
    expected_cwd = str((tmp_path / "platform" / "api").resolve())
    expected_python = (tmp_path / "platform" / "api" / ".venv" / "bin" / "python").resolve()
    expected_uvicorn = (tmp_path / "platform" / "api" / ".venv" / "bin" / "uvicorn").resolve()
    expected_argv = [
        str(expected_python), str(expected_uvicorn), "workflow_adapter_app:app",
        "--port", "8001", "--host", "127.0.0.1", "--no-proxy-headers", "--no-access-log",
    ]
    valid_cgroup = f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{tailnet.WORKFLOW_ADAPTER_SERVICE}"
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [77])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {77: [101, 202]})
    monkeypatch.setattr(tailnet, "_process_executable", lambda pid: expected_python)
    monkeypatch.setattr(tailnet, "_process_argv", lambda pid: expected_argv)
    monkeypatch.setattr(
        tailnet,
        "_pid_report",
        lambda port: [
            {"pid": 101, "cwd": expected_cwd, "cgroup": valid_cgroup, "argv": expected_argv, "executable": str(expected_python), "build_revision": revision},
            {"pid": 202, "cwd": "/wrong/source", "cgroup": valid_cgroup, "argv": expected_argv, "executable": str(expected_python), "build_revision": revision},
        ],
    )
    environment = {
        101: {"BMS_TAILNET_CONTROL_SOURCE_REVISION": revision},
        202: {
            "BMS_TAILNET_CONTROL_SOURCE_REVISION": revision,
            "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS": "owner@example.com",
            "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS": "127.0.0.1,::1",
            "BMS_WORKFLOW_ADAPTER_BIND_HOST": "127.0.0.1",
        },
    }
    monkeypatch.setattr(
        tailnet,
        "_pid_environment_value",
        lambda pid, key: environment.get(pid, {}).get(key),
    )
    assert tailnet._adapter_identity_policy_matches(tmp_path, "owner@example.com", runtime_revision=revision) is False
    environment[101].update(environment[202])
    assert tailnet._adapter_identity_policy_matches(tmp_path, "owner@example.com", runtime_revision=revision) is False
    monkeypatch.setattr(
        tailnet,
        "_pid_report",
        lambda port: [
            {"pid": 101, "cwd": expected_cwd, "cgroup": valid_cgroup, "argv": expected_argv, "executable": str(expected_python), "build_revision": revision},
            {"pid": 202, "cwd": expected_cwd, "cgroup": valid_cgroup, "argv": expected_argv, "executable": str(expected_python), "build_revision": revision},
        ],
    )
    assert tailnet._adapter_identity_policy_matches(tmp_path, "owner@example.com", runtime_revision=revision) is True
    missing_revision = [
        {**report, "build_revision": None}
        for report in tailnet._pid_report(8001)
    ]
    assert tailnet._adapter_identity_policy_matches(
        tmp_path, "owner@example.com", runtime_revision=revision, reports=missing_revision,
    ) is False
    monkeypatch.setattr(
        tailnet,
        "_pid_report",
        lambda port: [
            {"pid": 101, "cwd": expected_cwd, "cgroup": valid_cgroup, "argv": ["python", "-m", "http.server", "8001"], "executable": "/usr/bin/python3"},
            {"pid": 202, "cwd": expected_cwd, "cgroup": valid_cgroup, "argv": expected_argv, "executable": str(expected_python), "build_revision": revision},
        ],
    )
    assert tailnet._adapter_identity_policy_matches(tmp_path, "owner@example.com", runtime_revision=revision) is False


def test_production_ownership_snapshot_excludes_frontend_and_restores_exact_mode(monkeypatch, tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    frontend = systemd_dir / tailnet.FRONTEND_SERVICE
    adapter = systemd_dir / f"{tailnet.WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    frontend.parent.mkdir(parents=True)
    adapter.parent.mkdir(parents=True)
    frontend.write_bytes(b"frontend-original\n")
    adapter.write_bytes(b"adapter-original\n")
    frontend.chmod(0o644)
    adapter.chmod(0o640)
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(tailnet, "service_is_active", lambda service, **kwargs: service == tailnet.WORKFLOW_ADAPTER_SERVICE)
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: None)
    monkeypatch.setattr(tailnet, "run_systemctl", lambda *args, **kwargs: None)

    production = tailnet.environment_spec("production", project_root=tmp_path)
    snapshot = REAL_SERVICE_OWNERSHIP_SNAPSHOT(tmp_path, production)
    assert frontend not in snapshot.files
    assert set(snapshot.active) == {tailnet.WORKFLOW_ADAPTER_SERVICE}

    adapter.write_bytes(b"changed\n")
    adapter.chmod(0o600)
    REAL_RESTORE_SERVICE_OWNERSHIP(snapshot, tmp_path)
    assert adapter.read_bytes() == b"adapter-original\n"
    assert adapter.stat().st_mode & 0o7777 == 0o640
    assert frontend.read_bytes() == b"frontend-original\n"
    assert frontend.stat().st_mode & 0o7777 == 0o644


def test_selection_receipt_is_restored_with_exact_bytes_and_mode(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "tailnet-environment.json"
    state_path.write_bytes(b"prior-receipt\n")
    state_path.chmod(0o640)
    monkeypatch.setattr(tailnet, "SELECTION_STATE_PATH", state_path)
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: tmp_path / "systemd")
    monkeypatch.setattr(tailnet, "service_is_active", lambda *args, **kwargs: False)
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: None)
    monkeypatch.setattr(tailnet, "run_systemctl", lambda *args, **kwargs: None)
    production = tailnet.environment_spec("production", project_root=tmp_path)
    snapshot = REAL_SERVICE_OWNERSHIP_SNAPSHOT(tmp_path, production)
    state_path.write_bytes(b"failed-new-receipt\n")
    state_path.chmod(0o600)
    REAL_RESTORE_SERVICE_OWNERSHIP(snapshot, tmp_path)
    assert state_path.read_bytes() == b"prior-receipt\n"
    assert state_path.stat().st_mode & 0o7777 == 0o640


def test_service_rollback_attempts_every_file_and_service_after_one_file_failure(monkeypatch, tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    adapter = tmp_path / "adapter.conf"
    receipt.write_bytes(b"mutated\n")
    adapter.write_bytes(b"mutated\n")
    snapshot = tailnet.ServiceOwnershipSnapshot(
        files={receipt: (b"prior-receipt\n", 0o600), adapter: (b"prior-adapter\n", 0o640)},
        active={tailnet.WORKFLOW_ADAPTER_SERVICE: True, tailnet.FRONTEND_SERVICE: False},
    )
    real_atomic_write = tailnet._atomic_write
    events: list[object] = []

    def restore_file(path: Path, content: bytes, *, mode: int) -> None:
        if path == receipt:
            raise OSError("receipt restore failed")
        real_atomic_write(path, content, mode=mode)

    monkeypatch.setattr(tailnet, "_atomic_write", restore_file)
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: events.append("reload"))
    monkeypatch.setattr(
        tailnet, "run_systemctl",
        lambda action, service, **kwargs: events.append((action, service)),
    )
    monkeypatch.setattr(
        tailnet, "service_is_active",
        lambda service, **kwargs: service == tailnet.WORKFLOW_ADAPTER_SERVICE,
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="receipt restore failed"):
        REAL_RESTORE_SERVICE_OWNERSHIP(snapshot, tmp_path)

    assert adapter.read_bytes() == b"prior-adapter\n"
    assert adapter.stat().st_mode & 0o7777 == 0o640
    assert events == [
        "reload",
        ("reset-failed", tailnet.WORKFLOW_ADAPTER_SERVICE),
        ("restart", tailnet.WORKFLOW_ADAPTER_SERVICE),
        ("stop", tailnet.FRONTEND_SERVICE),
    ]


def test_api_build_identity_requires_mobile_compatible_canonical_time() -> None:
    probe = {
        "payload": {"status": "healthy", "liveness": {"alive": True},
                    "readiness": {"ready": True}, "build": {
            "revision": "a" * 40,
            "build_id": "development",
            "build_time": "2026-07-27T01:02:03.123456789Z",
        }}
    }
    assert tailnet._api_build_identity(probe, source="local")["build_time"].endswith("Z")
    for rejected in (
        "unknown", " 2026-07-27T01:02:03Z ", "1999-12-31T23:59:59Z",
        "2026-02-30T01:02:03Z", "2026-07-27T01:02:03+00:00",
    ):
        probe["payload"]["build"]["build_time"] = rejected
        with pytest.raises(
            tailnet.TailnetEnvironmentError,
            match="invalid build time|noncanonical build provenance",
        ):
            tailnet._api_build_identity(probe, source="local")

    probe["payload"]["build"]["build_time"] = "2026-07-27T01:02:03Z"
    probe["payload"]["build"]["build_id"] = "x" * 257
    with pytest.raises(tailnet.TailnetEnvironmentError, match="noncanonical build provenance"):
        tailnet._api_build_identity(probe, source="local")
    probe["payload"]["build"]["build_id"] = "💥" * 129
    with pytest.raises(tailnet.TailnetEnvironmentError, match="noncanonical build provenance"):
        tailnet._api_build_identity(probe, source="local")


def test_environment_spec_accepts_only_explicit_development_or_production(tmp_path: Path) -> None:
    development = tailnet.environment_spec("development", project_root=tmp_path)
    production = tailnet.environment_spec("production", project_root=tmp_path)

    assert development.environment == "development"
    assert development.runtime_mode == "dev"
    assert development.serve_target == "http://127.0.0.1:5173"
    assert development.api_health_url == "http://127.0.0.1:8000/api/health"
    assert development.api_port == 8000
    assert production.environment == "production"
    assert production.runtime_mode == "container"
    assert production.serve_target == "http://127.0.0.1:18081"

    for rejected in (None, "", "dev", "prod", "both", "stable", "staging"):
        with pytest.raises(tailnet.TailnetEnvironmentError, match="development or production"):
            tailnet.environment_spec(rejected, project_root=tmp_path)


def test_serve_snapshot_preserves_other_paths_and_rejects_funnel() -> None:
    status = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "node.example.ts.net:443": {
                "Handlers": {
                    "/": {"Proxy": "http://127.0.0.1:5173"},
                    "/am": {"Proxy": "http://127.0.0.1:5174/am"},
                }
            }
        },
    }
    snapshot = tailnet.serve_snapshot(status)
    assert snapshot.origin == "https://node.example.ts.net"
    assert snapshot.root_proxy == "http://127.0.0.1:5173"
    assert snapshot.handlers["/am"]["Proxy"] == "http://127.0.0.1:5174/am"

    root_handler = status["Web"]["node.example.ts.net:443"]["Handlers"]["/"]
    for hostile in (
        "http://127.0.0.1:80@attacker.example",
        "http://user@127.0.0.1:5173",
        "http://127.0.0.1:5173/path",
        "http://127.0.0.1:5173?query=1",
        "http://127.0.0.1:5173#fragment",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "https://127.0.0.1:5173",
    ):
        root_handler["Proxy"] = hostile
        with pytest.raises(tailnet.TailnetEnvironmentError, match="canonical loopback"):
            tailnet.serve_snapshot(status)
    root_handler["Proxy"] = "http://127.0.0.1:5173"

    status["TCP"]["443"]["Funnel"] = True
    with pytest.raises(tailnet.TailnetEnvironmentError, match="Funnel"):
        tailnet.serve_snapshot(status)

    status["TCP"]["443"].pop("Funnel")
    status["AllowFunnel"] = {"node.example.ts.net:443": True}
    with pytest.raises(tailnet.TailnetEnvironmentError, match="Funnel"):
        tailnet.serve_snapshot(status)


def test_set_serve_root_requires_exact_loopback_authority(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(tailnet, "_run", lambda command: commands.append(command))
    tailnet._set_serve_root("http://127.0.0.1:5173")
    assert commands == [["tailscale", "serve", "--bg", "--yes", "http://127.0.0.1:5173"]]

    for hostile in (
        "http://127.0.0.1:80@attacker.example",
        "http://user@127.0.0.1:5173",
        "http://127.0.0.1:5173/",
        "http://127.0.0.1:5173/path",
    ):
        with pytest.raises(tailnet.TailnetEnvironmentError, match="non-loopback"):
            tailnet._set_serve_root(hostile)
    assert len(commands) == 1


def test_control_route_rejects_preexisting_conflict(monkeypatch) -> None:
    assert tailnet.CONTROL_TARGET == "http://127.0.0.1:8001"
    snapshot = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={
            "/": {"Proxy": "http://127.0.0.1:5173"},
            tailnet.CONTROL_PATH: {"Proxy": "http://127.0.0.1:9999"},
        },
        raw={},
    )
    monkeypatch.setattr(
        tailnet,
        "_set_serve_path",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not overwrite")),
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="unexpected target"):
        tailnet._ensure_control_route(snapshot)


def test_control_route_migrates_sealed_legacy_path_target(monkeypatch) -> None:
    snapshot = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={tailnet.CONTROL_PATH: {"Proxy": tailnet.LEGACY_CONTROL_TARGET}},
        raw={},
    )
    targets: list[str] = []
    monkeypatch.setattr(tailnet, "_set_serve_path", lambda path, target: targets.append(target))
    monkeypatch.setattr(
        tailnet,
        "_read_serve_snapshot",
        lambda: tailnet.ServeSnapshot(
            origin=snapshot.origin,
            root_proxy=snapshot.root_proxy,
            handlers={tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
            raw={},
        ),
    )
    assert tailnet._ensure_control_route(snapshot) is True
    assert targets == [tailnet.CONTROL_TARGET]


def test_control_route_installation_verifies_exact_target(monkeypatch) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={},
    )
    installed = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy=prior.root_proxy,
        handlers={**prior.handlers, tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
        raw={},
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tailnet, "_set_serve_path", lambda path, target: calls.append((path, target)))
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: installed)

    assert tailnet._ensure_control_route(prior) is True
    assert calls == [(tailnet.CONTROL_PATH, tailnet.CONTROL_TARGET)]


def test_control_route_setter_disconnect_after_apply_restores_prior_snapshot(monkeypatch) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"sealed": "prior"},
    )
    clear_calls: list[str] = []

    def apply_then_disconnect(path: str, target: str) -> None:
        assert path == tailnet.CONTROL_PATH
        assert target == tailnet.CONTROL_TARGET
        raise tailnet.TailnetEnvironmentError("simulated CLI disconnect after apply")

    monkeypatch.setattr(tailnet, "_set_serve_path", apply_then_disconnect)
    monkeypatch.setattr(tailnet, "_clear_serve_path", clear_calls.append)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: prior)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="disconnect after apply"):
        tailnet._ensure_control_route(prior)
    assert clear_calls == [tailnet.CONTROL_PATH]


def test_control_route_failed_install_rejects_raw_rollback_drift(monkeypatch) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"Web": {"original": True}, "TCP": {"443": {"HTTPS": True}}},
    )
    invalid_install = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy=prior.root_proxy,
        handlers=prior.handlers,
        raw={"Web": {"invalid": True}},
    )
    restored_with_drift = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy=prior.root_proxy,
        handlers=prior.handlers,
        raw={"Web": {"original": True}, "TCP": {"443": {"HTTPS": False}}},
    )
    snapshots = iter((invalid_install, restored_with_drift))
    monkeypatch.setattr(tailnet, "_set_serve_path", lambda path, target: None)
    monkeypatch.setattr(tailnet, "_clear_serve_path", lambda path: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rollback did not restore Serve"):
        tailnet._ensure_control_route(prior)


def test_operator_development_frontend_is_forced_to_managed_api(monkeypatch, tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(
        tailnet,
        "render_user_units",
        lambda **kwargs: {
            tailnet.FRONTEND_SERVICE: (
                f"[Service]\nEnvironment=BMS_HOME={tmp_path}\n"
                "Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002\n"
            )
        },
    )
    monkeypatch.setattr(
        tailnet,
        "runtime_api_port",
        lambda mode, project_root=None: 18002 if mode == tailnet.DEV_RUNTIME_MODE else 8000,
    )
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: None)
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "a" * 40)
    monkeypatch.setattr(
        tailnet,
        "_run",
        lambda args: type("Result", (), {"stdout": "2026-07-26T20:00:00-05:00\n"})(),
    )

    tailnet._install_operator_development_frontend(tmp_path)

    unit = (systemd_dir / tailnet.FRONTEND_SERVICE).read_text(encoding="utf-8")
    dropin = (
        systemd_dir / f"{tailnet.FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    ).read_text(encoding="utf-8")
    assert "BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000" in unit
    assert "BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000" in dropin
    assert "ExecStart=/usr/bin/node " in dropin
    assert "/platform/frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173" in dropin
    assert "18002" not in unit
    assert "18002" not in dropin
    assert f"VITE_BMS_BUILD_SHA={'a' * 40}" in unit
    assert f"VITE_BMS_BUILD_SHA={'a' * 40}" in dropin


def test_development_frontend_requires_every_exclusive_loopback_vite_owner(monkeypatch, tmp_path: Path) -> None:
    spec = tailnet.environment_spec("development", project_root=tmp_path)
    revision = "a" * 40
    expected = str((tmp_path / "platform" / "frontend").resolve())
    reports = [{
        "pid": 101,
        "cwd": expected,
        "cmdline": f"node {expected}/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173",
        "cgroup": f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{tailnet.FRONTEND_SERVICE}\n",
        "build_revision": revision,
        "argv": [
            "/usr/bin/node",
            f"{expected}/node_modules/vite/bin/vite.js",
            "--host", "127.0.0.1", "--port", "5173",
        ],
        "executable": "/usr/bin/node",
    }]
    addresses = {"127.0.0.1"}
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: addresses)
    monkeypatch.setattr(tailnet, "_pid_report", lambda port: list(reports))
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [77])
    monkeypatch.setattr(
        tailnet,
        "_host_listener_inode_owners",
        lambda inodes: {77: [int(report["pid"]) for report in reports]},
    )
    monkeypatch.setattr(
        tailnet,
        "_pid_environment_value",
        lambda pid, key: revision if pid == 101 and key == "VITE_BMS_BUILD_SHA" else None,
    )
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is True

    reports[0]["executable"] = "/tmp/attacker/node"
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is False
    reports[0]["executable"] = "/usr/bin/node"

    original_argv = reports[0]["argv"]
    reports[0]["argv"] = ["python", "-m", "http.server", "5173"]
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is False
    reports[0]["argv"] = original_argv
    reports[0]["cgroup"] = f"0::/user.slice/{tailnet.FRONTEND_SERVICE}-rogue.service\n"
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is False
    reports[0]["cgroup"] = f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{tailnet.FRONTEND_SERVICE}\n"

    addresses = {"0.0.0.0"}
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is False
    addresses = {"127.0.0.1"}

    reports.append({
        "pid": 202,
        "cwd": "/tmp/rogue",
        "cmdline": "python -m http.server 5173",
        "cgroup": "0::/user.slice/rogue.service\n",
        "build_revision": None,
    })
    assert tailnet._dev_frontend_matches_root(spec, tmp_path) is False


def test_final_development_receipt_revalidates_frontend_ownership(monkeypatch, tmp_path: Path) -> None:
    spec = tailnet.environment_spec("development", project_root=tmp_path)
    monkeypatch.setattr(tailnet, "_host_listener_closure", lambda port: {"listener_reports": []})
    monkeypatch.setattr(
        tailnet,
        "_dev_frontend_matches_root",
        lambda selected, root, reports=None: False,
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="lost exact service ownership"):
        REAL_VALIDATED_DEVELOPMENT_FRONTEND_LISTENER(spec, tmp_path)


def test_container_cgroup_identity_requires_the_complete_container_id() -> None:
    container_id = "a" * 64
    assert tailnet._process_in_exact_container_cgroup(
        f"0::/system.slice/docker-{container_id}.scope\n", container_id
    ) is True
    assert tailnet._process_in_exact_container_cgroup(
        f"1:net_cls:/\n0::/system.slice/docker-{container_id}.scope\n", container_id
    ) is True
    assert tailnet._process_in_exact_container_cgroup(
        f"0::/system.slice/docker-{container_id}.scope\n0::/rogue.service\n", container_id
    ) is False
    assert tailnet._process_in_exact_container_cgroup(
        f"0::/system.slice/docker-{container_id[:12]}{'b' * 52}.scope\n", container_id
    ) is False
    assert tailnet._process_in_exact_container_cgroup(
        f"0::/system.slice/docker-{container_id}.scope/rogue.service\n", container_id
    ) is False


def test_trusted_node_executables_ignore_ambient_path(monkeypatch, tmp_path: Path) -> None:
    attacker = tmp_path / "node"
    attacker.write_bytes(Path("/usr/bin/node").read_bytes())
    attacker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    assert attacker.resolve() not in tailnet._trusted_node_executables()


def test_adapter_root_match_requires_exact_source_revision(monkeypatch, tmp_path: Path) -> None:
    expected_cwd = str((tmp_path / "platform" / "api").resolve())
    monkeypatch.setattr(tailnet, "_pid_report", lambda port: [{"pid": 42, "cwd": expected_cwd}])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [77])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {77: [42]})
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "a" * 40)
    monkeypatch.setattr(tailnet, "_pid_environment_value", lambda pid, key: "b" * 40)

    assert tailnet._adapter_matches_root(tmp_path) is False

    monkeypatch.setattr(tailnet, "_pid_environment_value", lambda pid, key: "a" * 40)
    assert tailnet._adapter_matches_root(tmp_path) is True


def test_selected_environment_rejects_local_tailnet_api_build_mismatch(monkeypatch, tmp_path: Path) -> None:
    spec = tailnet.environment_spec("development", project_root=tmp_path)
    snapshot = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy=spec.serve_target,
        handlers={"/": {"Proxy": spec.serve_target}},
        raw={"Web": {}},
    )
    local_build = {"revision": "a" * 40, "build_id": "local", "build_time": "2026-07-27T01:00:00.123456Z"}
    public_build = {"revision": "b" * 40, "build_id": "tailnet", "build_time": "2026-07-27T01:00:01Z"}

    def probe(url: str, *, expect_json: bool = False, expected_final_url: str | None = None):
        if not expect_json:
            return {"url": url, "status": 200, "payload": None}
        build = local_build if url == spec.api_health_url else public_build
        return {"url": url, "status": 200, "payload": {
            "status": "healthy", "liveness": {"alive": True},
            "readiness": {"ready": True}, "build": build,
        }}

    monkeypatch.setattr(tailnet, "_url_probe", probe)
    monkeypatch.setattr(tailnet, "_pid_report", lambda port: [])
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "c" * 40)
    monkeypatch.setattr(
        tailnet,
        "_validated_container_runtime",
        lambda root, require_web: {"validated_revision": "a" * 40},
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="API build provenance"):
        tailnet._verify_selected_environment(spec, tmp_path, snapshot)


def test_selected_environment_rejects_api_health_revision_outside_managed_runtime(monkeypatch, tmp_path: Path) -> None:
    spec = tailnet.environment_spec("development", project_root=tmp_path)
    snapshot = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy=spec.serve_target,
        handlers={"/": {"Proxy": spec.serve_target}},
        raw={"Web": {}},
    )
    build = {"revision": "a" * 40, "build_id": "same", "build_time": "2026-07-27T01:00:00.123456789Z"}
    monkeypatch.setattr(
        tailnet,
        "_url_probe",
        lambda url, expect_json=False, **kwargs: {
            "url": url,
            "status": 200,
            "payload": {
                "status": "healthy", "liveness": {"alive": True},
                "readiness": {"ready": True}, "build": build,
            } if expect_json else None,
        },
    )
    monkeypatch.setattr(tailnet, "_pid_report", lambda port: [])
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "c" * 40)
    monkeypatch.setattr(
        tailnet,
        "_validated_container_runtime",
        lambda root, require_web: {"validated_revision": "b" * 40},
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="managed container revision"):
        tailnet._verify_selected_environment(spec, tmp_path, snapshot)

    monkeypatch.setattr(
        tailnet,
        "_validated_container_runtime",
        lambda root, require_web: {"validated_revision": "a" * 40},
    )
    report = tailnet._verify_selected_environment(spec, tmp_path, snapshot)
    assert report["frontend_target"] == "http://127.0.0.1:5173"


def test_start_selected_environment_probes_only_selected_runtime_and_never_starts_shared_services(
    monkeypatch,
    tmp_path: Path,
) -> None:
    probes: list[tuple[str, bool]] = []
    waits: list[str] = []
    health_payload = {
        "status": "healthy",
        "liveness": {"alive": True},
        "readiness": {"ready": True},
        "build": {
            "revision": "a" * 40,
            "build_id": "test-build",
            "build_time": "2026-07-27T01:00:00Z",
        }
    }
    monkeypatch.setattr(
        tailnet,
        "_url_probe",
        lambda url, expect_json=False, **kwargs: probes.append((url, expect_json)) or {
            "status": 200,
            "payload": health_payload if expect_json else None,
        },
    )
    monkeypatch.setattr(tailnet, "wait_for_http", waits.append)
    monkeypatch.setattr(tailnet, "_validated_production_tailnet_proxy", lambda root: {"validated": True})
    monkeypatch.setattr(
        tailnet,
        "_install_adapter_control_policy",
        lambda root, runtime_revision, ledger=None: "owner@example.com",
    )
    monkeypatch.setattr(
        tailnet,
        "_adapter_identity_policy_matches",
        lambda root, login, **kwargs: True,
    )
    monkeypatch.setattr(tailnet, "_install_operator_development_frontend", lambda root, ledger=None: None)
    monkeypatch.setattr(tailnet, "_dev_frontend_matches_root", lambda spec, root: True)

    development = tailnet.environment_spec("development", project_root=tmp_path)
    tailnet._start_selected_environment(development, tmp_path)
    assert probes == [
        (development.api_health_url, True),
        (development.frontend_url, False),
        (development.api_health_url, True),
    ]
    assert waits == [development.frontend_url, development.api_health_url]

    probes.clear()
    waits.clear()
    production = tailnet.environment_spec("production", project_root=tmp_path)
    tailnet._start_selected_environment(production, tmp_path)
    assert probes == [
        (production.api_health_url, True),
        (production.frontend_url, False),
        ("http://127.0.0.1:18081/", False),
        (production.frontend_url, False),
        (production.api_health_url, True),
    ]
    assert waits == [production.frontend_url, production.api_health_url]


def test_production_tailnet_proxy_requires_exact_read_only_config(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / tailnet.PRODUCTION_TAILNET_PROXY_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("server { listen 127.0.0.1:18081; }\n", encoding="utf-8")
    config_sha = tailnet.hashlib.sha256(config.read_bytes()).hexdigest()
    inspected = [{
        "Id": "b" * 64,
        "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID,
        "State": {"Running": True, "Pid": 123},
        "Path": "/docker-entrypoint.sh",
        "Args": ["nginx", "-g", "daemon off;"],
        "HostConfig": {
            "NetworkMode": "host",
            "RestartPolicy": {"Name": "unless-stopped"},
            "ReadonlyRootfs": True,
            "Memory": 256 * 1024 * 1024, "PidsLimit": 128,
            "Ulimits": [{"Name": "nofile", "Hard": 4096, "Soft": 4096}],
            "LogConfig": {"Type": "json-file", "Config": {"max-file": "5", "max-size": "10m"}},
            "Binds": [f"{config.resolve()}:/etc/nginx/conf.d/default.conf:ro"],
            "Tmpfs": {"/var/cache/nginx": "", "/var/run": ""},
        },
        "Config": {
            "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE,
            "Entrypoint": ["/docker-entrypoint.sh"],
            "Cmd": ["nginx", "-g", "daemon off;"],
            "Labels": {
                tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL: config_sha,
                "com.biomodstack.tailnet-proxy-owner": "compose.core-runtime",
                "com.docker.compose.project": "biomodstack-tailnet-control",
                "com.docker.compose.service": "tailnet-production-proxy",
            },
        },
        "Mounts": [{
            "Type": "bind",
            "Source": str(config.resolve()),
            "Destination": "/etc/nginx/conf.d/default.conf",
            "RW": False,
        }],
    }]
    monkeypatch.setattr(
        tailnet,
        "_run",
        lambda command, **kwargs: tailnet.subprocess.CompletedProcess(command, 0, tailnet.json.dumps(inspected), ""),
    )
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/system.slice/docker-" + ("b" * 64) + ".scope\n")
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1, 27])
    monkeypatch.setattr(tailnet, "_host_owner_pid_map", lambda container_id, pids: [
        {"container_pid": 1, "host_pid": 123}, {"container_pid": 27, "host_pid": 124},
    ])
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda container_name: [123, 124])
    monkeypatch.setattr(
        tailnet, "_container_process_reports",
        lambda name, container_id, host_pids: [
            _nginx_process_report(123, 1, tailnet._process_cgroup(123)),
            _nginx_process_report(124, 27, tailnet._process_cgroup(124)),
        ],
    )
    monkeypatch.setattr(tailnet, "_container_listener_inodes", lambda name, port: [810, 827])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [810, 827])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {810: [123], 827: [124]})
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})

    report = tailnet._validated_production_tailnet_proxy(tmp_path)
    assert report["config_sha256"] == config_sha
    assert report["image_id"] == tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID
    assert report["listener_pids"] == [123, 124]
    assert report["container_listener_pids"] == [1, 27]

    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1])
    with pytest.raises(tailnet.TailnetEnvironmentError, match="socket has an owner outside"):
        tailnet._validated_production_tailnet_proxy(tmp_path)
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1, 27])

    monkeypatch.setattr(
        tailnet, "_container_process_reports",
        lambda name, container_id, host_pids: [
            _nginx_process_report(123, 27, tailnet._process_cgroup(123)),
            _nginx_process_report(124, 1, tailnet._process_cgroup(124)),
        ],
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="socket has an owner outside"):
        tailnet._validated_production_tailnet_proxy(tmp_path)
    monkeypatch.setattr(
        tailnet, "_container_process_reports",
        lambda name, container_id, host_pids: [
            _nginx_process_report(123, 1, tailnet._process_cgroup(123)),
            _nginx_process_report(124, 27, tailnet._process_cgroup(124)),
        ],
    )

    inspected[0]["HostConfig"]["Memory"] = 0
    with pytest.raises(tailnet.TailnetEnvironmentError, match="resource boundaries"):
        tailnet._validated_production_tailnet_proxy(tmp_path)
    inspected[0]["HostConfig"]["Memory"] = 256 * 1024 * 1024

    monkeypatch.setattr(
        tailnet,
        "_host_listener_inode_owners",
        lambda inodes: {810: [124, 999], 827: [124]},
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="socket has an owner outside"):
        tailnet._validated_production_tailnet_proxy(tmp_path)
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {810: [124], 827: [124]})

    inspected[0]["Mounts"].append({
        "Type": "bind", "Source": "/tmp/hostile-entrypoint.sh",
        "Destination": "/docker-entrypoint.sh", "RW": False,
    })
    with pytest.raises(tailnet.TailnetEnvironmentError, match="reviewed config"):
        tailnet._validated_production_tailnet_proxy(tmp_path)
    inspected[0]["Mounts"].pop()

    inspected[0]["Config"]["Labels"][tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL] = "0" * 64
    with pytest.raises(tailnet.TailnetEnvironmentError, match="reviewed config"):
        tailnet._validated_production_tailnet_proxy(tmp_path)

    inspected[0]["Config"]["Labels"][tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL] = config_sha
    inspected[0]["Image"] = "sha256:attacker"
    inspected[0]["Config"]["Image"] = "attacker/proxy:latest"
    with pytest.raises(tailnet.TailnetEnvironmentError, match="executable identity"):
        tailnet._validated_production_tailnet_proxy(tmp_path)

    inspected[0]["Image"] = tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID
    inspected[0]["Config"]["Image"] = tailnet.PRODUCTION_TAILNET_PROXY_IMAGE
    inspected[0]["HostConfig"]["PidMode"] = "host"
    with pytest.raises(tailnet.TailnetEnvironmentError, match="host network"):
        tailnet._validated_production_tailnet_proxy(tmp_path)


def test_production_tailnet_proxy_requires_container_owned_listener(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / tailnet.PRODUCTION_TAILNET_PROXY_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("server { listen 127.0.0.1:18081; }\n", encoding="utf-8")
    config_sha = tailnet.hashlib.sha256(config.read_bytes()).hexdigest()
    inspected = [{
        "Id": "b" * 64, "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID,
        "State": {"Running": True, "Pid": 123}, "Path": "/docker-entrypoint.sh",
        "Args": ["nginx", "-g", "daemon off;"],
        "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"}, "ReadonlyRootfs": True,
            "Memory": 256 * 1024 * 1024, "PidsLimit": 128,
            "Ulimits": [{"Name": "nofile", "Hard": 4096, "Soft": 4096}],
            "LogConfig": {"Type": "json-file", "Config": {"max-file": "5", "max-size": "10m"}},
            "Binds": [f"{config.resolve()}:/etc/nginx/conf.d/default.conf:ro"], "Tmpfs": {"/var/cache/nginx": "", "/var/run": ""}},
        "Config": {"Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE, "Entrypoint": ["/docker-entrypoint.sh"],
            "Cmd": ["nginx", "-g", "daemon off;"], "Labels": {
                tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL: config_sha,
                "com.biomodstack.tailnet-proxy-owner": "compose.core-runtime",
                "com.docker.compose.project": "biomodstack-tailnet-control",
                "com.docker.compose.service": "tailnet-production-proxy"}},
        "Mounts": [{"Type": "bind", "Source": str(config.resolve()), "Destination": "/etc/nginx/conf.d/default.conf", "RW": False}],
    }]
    monkeypatch.setattr(tailnet, "_run", lambda command, **kwargs: tailnet.subprocess.CompletedProcess(command, 0, tailnet.json.dumps(inspected), ""))
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/system.slice/docker-" + ("b" * 64) + ".scope\n")
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [])
    with pytest.raises(tailnet.TailnetEnvironmentError, match="listener"):
        tailnet._validated_production_tailnet_proxy(tmp_path)


def test_production_tailnet_proxy_rejects_unrelated_host_listener(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / tailnet.PRODUCTION_TAILNET_PROXY_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("server { listen 127.0.0.1:18081; }\n", encoding="utf-8")
    config_sha = tailnet.hashlib.sha256(config.read_bytes()).hexdigest()
    inspected = [{
        "Id": "b" * 64, "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID,
        "State": {"Running": True, "Pid": 123}, "Path": "/docker-entrypoint.sh",
        "Args": ["nginx", "-g", "daemon off;"],
        "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"}, "ReadonlyRootfs": True,
            "Memory": 256 * 1024 * 1024, "PidsLimit": 128,
            "Ulimits": [{"Name": "nofile", "Hard": 4096, "Soft": 4096}],
            "LogConfig": {"Type": "json-file", "Config": {"max-file": "5", "max-size": "10m"}},
            "Binds": [f"{config.resolve()}:/etc/nginx/conf.d/default.conf:ro"], "Tmpfs": {"/var/cache/nginx": "", "/var/run": ""}},
        "Config": {"Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE, "Entrypoint": ["/docker-entrypoint.sh"],
            "Cmd": ["nginx", "-g", "daemon off;"], "Labels": {
                tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL: config_sha,
                "com.biomodstack.tailnet-proxy-owner": "compose.core-runtime",
                "com.docker.compose.project": "biomodstack-tailnet-control",
                "com.docker.compose.service": "tailnet-production-proxy"}},
        "Mounts": [{"Type": "bind", "Source": str(config.resolve()), "Destination": "/etc/nginx/conf.d/default.conf", "RW": False}],
    }]
    monkeypatch.setattr(tailnet, "_run", lambda command, **kwargs: tailnet.subprocess.CompletedProcess(command, 0, tailnet.json.dumps(inspected), ""))
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1])
    monkeypatch.setattr(tailnet, "_host_owner_pid_map", lambda container_id, pids: [
        {"container_pid": 1, "host_pid": 124},
    ])
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda container_name: [124])
    monkeypatch.setattr(tailnet, "_container_listener_inodes", lambda name, port: [810])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [810, 999])
    monkeypatch.setattr(
        tailnet,
        "_process_cgroup",
        lambda pid: "0::/system.slice/docker-" + ("b" * 64) + ".scope\n" if pid == 123 else "0::/system.slice/unrelated.service\n",
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="outside the validated container"):
        tailnet._validated_production_tailnet_proxy(tmp_path)


def test_development_runtime_inspects_only_shared_api_container(monkeypatch, tmp_path: Path) -> None:
    revision = "a" * 40
    commands: list[list[str]] = []
    inspected = [{
        "Name": "/biomodstack-api",
        "Id": "a" * 64,
        "Image": tailnet.MANAGED_API_IMAGE_ID,
        "Path": "/bin/sh",
        "Args": [
            "-ec",
            "/app/platform/api/.venv/bin/python run_migrations.py && exec "
            "/app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000",
        ],
        "State": {"Pid": 123},
        "HostConfig": {"ReadonlyRootfs": False},
        "Mounts": [{
            "Type": "bind", "Source": "/mnt/BioModStack",
            "Destination": "/var/lib/biomodstack", "Mode": "rw",
            "RW": True, "Propagation": "rprivate",
        }],
        "Config": {
            "WorkingDir": "/app/platform/api",
            "Labels": {
                "org.opencontainers.image.revision": revision,
                "com.docker.compose.project.working_dir": str(tmp_path),
            },
        },
    }]

    def run(command, **kwargs):
        commands.append(command)
        return tailnet.subprocess.CompletedProcess(command, 0, tailnet.json.dumps(inspected), "")

    monkeypatch.setattr(tailnet, "_run", run)
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/system.slice/docker-" + ("a" * 64) + ".scope\n")
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda name: [123])
    monkeypatch.setattr(
        tailnet, "_container_process_reports",
        lambda name, container_id, host_pids: [_api_process_report(123, tailnet._process_cgroup(123))],
    )
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    report = tailnet._validated_container_runtime(tmp_path, require_web=False)
    assert report["validated_revision"] == revision
    assert commands[0] == ["docker", "inspect", "biomodstack-api"]
    assert "biomodstack-web" not in commands[0]


def test_container_runtime_rejects_revision_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tailnet, "_docker_runtime_report", lambda required_names: {
        "containers": [
            {"name": "biomodstack-api", "image_id": "sha256:api", "revision": "a" * 40, "compose_working_dir": str(tmp_path)},
            {"name": "biomodstack-web", "image_id": "sha256:web", "revision": "b" * 40, "compose_working_dir": str(tmp_path)},
        ]
    })
    with pytest.raises(tailnet.TailnetEnvironmentError, match="revisions"):
        tailnet._validated_container_runtime(tmp_path, require_web=True)


def test_container_runtime_accepts_exact_source_owned_image_lineage(monkeypatch, tmp_path: Path) -> None:
    revision = "a" * 40
    runtime_report = {
        "containers": [
            {"name": "biomodstack-api", "container_id": "a" * 64, "image_id": tailnet.MANAGED_API_IMAGE_ID, "revision": revision, "compose_working_dir": str(tmp_path), "pid": 1, "cgroup": "0::/system.slice/docker-" + ("a" * 64) + ".scope\n", "cmdline": "/bin/sh -ec /app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000", "cwd": "/app/platform/api", "readonly_rootfs": False, "mounts": [{"type": "bind", "source": "/mnt/BioModStack", "destination": "/var/lib/biomodstack", "mode": "rw", "rw": True, "propagation": "rprivate"}]},
            {"name": "biomodstack-web", "container_id": "b" * 64, "image_id": tailnet.MANAGED_WEB_IMAGE_ID, "revision": revision, "compose_working_dir": str(tmp_path), "pid": 2, "cgroup": "0::/system.slice/docker-" + ("b" * 64) + ".scope\n", "cmdline": "/docker-entrypoint.sh nginx -g daemon off;", "cwd": "/", "readonly_rootfs": False, "mounts": []},
        ]
    }
    monkeypatch.setattr(tailnet, "_docker_runtime_report", lambda required_names: runtime_report)
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    monkeypatch.setattr(
        tailnet,
        "_container_host_pids",
        lambda name: [1] if name == "biomodstack-api" else [2],
    )
    monkeypatch.setattr(
        tailnet,
        "_process_cgroup",
        lambda pid: "0::/system.slice/docker-" + (("a" if pid == 1 else "b") * 64) + ".scope\n",
    )
    monkeypatch.setattr(
        tailnet,
        "_container_process_reports",
        lambda name, container_id, host_pids: (
            [_api_process_report(1, tailnet._process_cgroup(1))]
            if name == "biomodstack-api"
            else [_nginx_process_report(2, 1, tailnet._process_cgroup(2))]
        ),
    )
    monkeypatch.setattr(tailnet, "_run", lambda args: type("Result", (), {"stdout": ""})())
    report = tailnet._validated_container_runtime(tmp_path, require_web=True)
    assert report["validated_revision"] == revision
    assert report["validated_compose_root"] == str(tmp_path.resolve())

    runtime_report["containers"][0]["mounts"].append({
        "type": "bind", "source": "/tmp/rogue.py",
        "destination": "/app/platform/api/main.py", "mode": "ro",
        "rw": False, "propagation": "rprivate",
    })
    with pytest.raises(tailnet.TailnetEnvironmentError, match="image/process identity"):
        tailnet._validated_container_runtime(tmp_path, require_web=True)


def test_host_owner_helper_uses_kernel_proc_names_not_human_ls_output(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return tailnet.subprocess.CompletedProcess(command, 0, "123 77\n", "")

    monkeypatch.setattr(tailnet, "_run", fake_run)
    assert tailnet._host_listener_inode_owners([77]) == {77: [123]}
    assert captured[captured.index("--user") + 1] == "0:0"
    helper = captured[captured.index("-c") + 1]
    assert "os.scandir('/host-proc')" in helper
    assert "os.readlink(fd_entry.path)" in helper
    assert "ls -l" not in helper


def test_container_listener_helpers_use_privileged_root_proc_visibility(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        return tailnet.subprocess.CompletedProcess(command, 0, "1\n27\n", "")

    monkeypatch.setattr(tailnet, "_run", fake_run)
    assert tailnet._container_listener_pids("biomodstack-web", 18080) == [1, 27]
    assert tailnet._container_listener_inodes("biomodstack-web", 18080) == [1, 27]
    for command in captured:
        assert command[:5] == [
            "docker", "exec", "--privileged", "--user", "0:0",
        ]


def test_canonical_source_override_supersedes_old_dropins_without_deleting_them(
    monkeypatch,
    tmp_path: Path,
) -> None:
    systemd_dir = tmp_path / "systemd"
    dropin_dir = systemd_dir / f"{tailnet.FRONTEND_SERVICE}.d"
    dropin_dir.mkdir(parents=True)
    conflicting = dropin_dir / "60-third-source.conf"
    conflicting.write_text("[Service]\nEnvironment=BMS_HOME=/tmp/third-source\n", encoding="utf-8")
    resource = dropin_dir / "80-resource-policy.conf"
    resource.write_text("[Service]\nMemoryMax=4G\n", encoding="utf-8")
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)

    monkeypatch.setattr(tailnet, "render_user_units", lambda **kwargs: {
        tailnet.FRONTEND_SERVICE: (
            f"[Service]\nEnvironment=BMS_HOME={tmp_path / 'canonical'}\n"
            "Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002\n"
        )
    })
    monkeypatch.setattr(tailnet, "runtime_api_port", lambda mode, project_root=None: 18002 if mode == tailnet.DEV_RUNTIME_MODE else 8000)
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "b" * 40)
    monkeypatch.setattr(tailnet, "_run", lambda args: type("Result", (), {"stdout": "2026-07-26T20:00:00-05:00\n"})())
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: None)

    tailnet._install_operator_development_frontend(tmp_path / "canonical")

    assert conflicting.exists()
    assert resource.exists()
    override = dropin_dir / "99-tailnet-canonical-source.conf"
    assert str(tmp_path / "canonical") in override.read_text(encoding="utf-8")
    assert "ExecStart=" in override.read_text(encoding="utf-8")


def test_selection_starts_only_selected_target_then_changes_root(monkeypatch, tmp_path: Path) -> None:
    events: list[object] = []
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}, "/am": {"Proxy": "http://127.0.0.1:5174/am"}},
        raw={},
    )
    selected = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy="http://127.0.0.1:18081",
        handlers={**prior.handlers, tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
        raw={},
    )
    snapshots = iter((prior, selected))

    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: events.append(("start", spec.environment, root)))
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)
    monkeypatch.setattr(tailnet, "_set_serve_root", lambda target: events.append(("serve", target)))
    monkeypatch.setattr(
        tailnet,
        "_verify_selected_environment",
        lambda spec, root, snapshot: {
            "selected_environment": spec.environment,
            "serve_root_proxy": snapshot.root_proxy,
            "project_root": str(root),
        },
    )
    monkeypatch.setattr(tailnet, "_write_selection_state", lambda report: events.append(("state", report["selected_environment"])))

    report = tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert report["selected_environment"] == "production"
    assert events == [
        ("start", "production", tmp_path.resolve()),
        ("serve", "http://127.0.0.1:18081"),
        ("state", "production"),
    ]


def test_selection_rolls_back_previous_root_after_post_switch_failure(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={},
    )
    selected = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy="http://127.0.0.1:18081",
        handlers={**prior.handlers, tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
        raw={},
    )
    restored = prior
    snapshots = iter((prior, selected, restored))
    serve_calls: list[str] = []

    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)
    monkeypatch.setattr(tailnet, "_set_serve_root", serve_calls.append)
    monkeypatch.setattr(
        tailnet,
        "_verify_selected_environment",
        lambda *args, **kwargs: (_ for _ in ()).throw(tailnet.TailnetEnvironmentError("public health failed")),
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rolled back"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert serve_calls == ["http://127.0.0.1:18081", "http://127.0.0.1:5173"]


def test_root_command_failure_removes_new_control_route_and_restores_exact_serve(
    monkeypatch,
    tmp_path: Path,
) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={},
    )
    snapshots = iter((prior, prior))
    root_calls: list[str] = []
    clear_calls: list[str] = []

    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)

    def set_root(target: str) -> None:
        root_calls.append(target)
        if len(root_calls) == 1:
            raise tailnet.TailnetEnvironmentError("tailscale root command failed")

    monkeypatch.setattr(tailnet, "_set_serve_root", set_root)
    monkeypatch.setattr(tailnet, "_clear_serve_path", clear_calls.append)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rolled back"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert root_calls == ["http://127.0.0.1:18081", "http://127.0.0.1:5173"]
    assert clear_calls == [tailnet.CONTROL_PATH]


def test_selection_retries_control_rollback_when_install_attempt_raises(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"sealed": "prior"},
    )
    snapshots = iter((prior, prior))
    clear_calls: list[str] = []
    root_calls: list[str] = []
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(
        tailnet,
        "_ensure_control_route",
        lambda snapshot: (_ for _ in ()).throw(
            tailnet.TailnetEnvironmentError("disconnect after route apply")
        ),
    )
    monkeypatch.setattr(tailnet, "_clear_serve_path", clear_calls.append)
    monkeypatch.setattr(tailnet, "_set_serve_root", root_calls.append)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="disconnect after route apply"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert clear_calls == [tailnet.CONTROL_PATH]
    assert root_calls == []


def test_post_migration_failure_restores_sealed_legacy_control_target(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={
            "/": {"Proxy": "http://127.0.0.1:5173"},
            tailnet.CONTROL_PATH: {"Proxy": tailnet.LEGACY_CONTROL_TARGET},
        },
        raw={"sealed": "legacy"},
    )
    snapshots = iter((prior, prior))
    root_calls: list[str] = []
    path_calls: list[tuple[str, str]] = []
    clear_calls: list[str] = []
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)

    def set_root(target: str) -> None:
        root_calls.append(target)
        if len(root_calls) == 1:
            raise tailnet.TailnetEnvironmentError("post-migration root failure")

    monkeypatch.setattr(tailnet, "_set_serve_root", set_root)
    monkeypatch.setattr(tailnet, "_set_serve_path", lambda path, target: path_calls.append((path, target)))
    monkeypatch.setattr(tailnet, "_clear_serve_path", clear_calls.append)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rolled back"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert path_calls == [(tailnet.CONTROL_PATH, tailnet.LEGACY_CONTROL_TARGET)]
    assert clear_calls == []


def test_state_write_failure_rolls_back_selected_root(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={},
    )
    selected = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy="http://127.0.0.1:18081",
        handlers={**prior.handlers, tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
        raw={},
    )
    snapshots = iter((prior, selected, prior))
    root_calls: list[str] = []
    ownership = tailnet.ServiceOwnershipSnapshot(files={}, active={})
    restored: list[tuple[tailnet.ServiceOwnershipSnapshot, Path, set[str]]] = []
    monkeypatch.setattr(tailnet, "_service_ownership_snapshot", lambda root, spec: ownership)
    monkeypatch.setattr(
        tailnet,
        "_restore_service_ownership",
        lambda snapshot, root, mutations: restored.append((snapshot, root, set(mutations))),
    )
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)
    monkeypatch.setattr(tailnet, "_set_serve_root", root_calls.append)
    monkeypatch.setattr(tailnet, "_clear_serve_path", lambda path: None)
    monkeypatch.setattr(tailnet, "_verify_selected_environment", lambda *args: {"selected_environment": "production"})
    monkeypatch.setattr(
        tailnet,
        "_write_selection_state",
        lambda report: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rolled back"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert root_calls == ["http://127.0.0.1:18081", "http://127.0.0.1:5173"]
    assert restored == [(ownership, tmp_path.resolve(), {"selection_state"})]


def test_rollback_rejects_raw_serve_drift_even_when_handlers_match(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"Web": {"node.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5173"}}}}, "TCP": {"443": {"HTTPS": True}}},
    )
    selected = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy="http://127.0.0.1:18081",
        handlers={**prior.handlers, tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET}},
        raw={"Web": {}},
    )
    restored_with_drift = tailnet.ServeSnapshot(
        origin=prior.origin,
        root_proxy=prior.root_proxy,
        handlers=prior.handlers,
        raw={**prior.raw, "TCP": {"443": {"HTTPS": False}}},
    )
    snapshots = iter((prior, selected, restored_with_drift))
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: True)
    monkeypatch.setattr(tailnet, "_set_serve_root", lambda target: None)
    monkeypatch.setattr(tailnet, "_clear_serve_path", lambda path: None)
    monkeypatch.setattr(
        tailnet,
        "_verify_selected_environment",
        lambda *args: (_ for _ in ()).throw(tailnet.TailnetEnvironmentError("forced failure")),
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rollback also failed"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)


def test_invalid_environment_has_no_start_or_serve_side_effect(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda *args: (_ for _ in ()).throw(AssertionError("start")))
    monkeypatch.setattr(tailnet, "_set_serve_root", lambda *args: (_ for _ in ()).throw(AssertionError("serve")))

    with pytest.raises(tailnet.TailnetEnvironmentError):
        tailnet.select_tailnet_environment("dev", project_root=tmp_path)


def test_read_only_health_preflight_failure_has_no_compensating_mutations(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"sealed": "prior"},
    )
    ownership = tailnet.ServiceOwnershipSnapshot(files={}, active={})
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: prior)
    monkeypatch.setattr(tailnet, "_service_ownership_snapshot", lambda root, spec: ownership)
    monkeypatch.setattr(
        tailnet,
        "_url_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            tailnet.TailnetEnvironmentError("initial health preflight failed")
        ),
    )
    for name in (
        "_restore_control_route",
        "_set_serve_root",
        "_restore_service_ownership",
        "wait_for_http",
    ):
        monkeypatch.setattr(
            tailnet,
            name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"unexpected compensation: {_name}")
            ),
        )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="failed before any mutation"):
        tailnet.select_tailnet_environment("development", project_root=tmp_path)


def test_owner_identity_read_failure_before_first_write_has_no_compensation(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={"/": {"Proxy": "http://127.0.0.1:5173"}},
        raw={"sealed": "prior"},
    )
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: prior)
    monkeypatch.setattr(tailnet, "_url_probe", lambda *args, **kwargs: {"status": 200})
    monkeypatch.setattr(
        tailnet,
        "_tailnet_owner_login",
        lambda: (_ for _ in ()).throw(tailnet.TailnetEnvironmentError("owner read failed")),
    )
    monkeypatch.setattr(
        tailnet,
        "_restore_service_ownership",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected service compensation")),
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="failed before any mutation"):
        tailnet.select_tailnet_environment("development", project_root=tmp_path)


def test_scoped_adapter_rollback_does_not_restart_frontend(monkeypatch, tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    adapter_dropin = systemd_dir / f"{tailnet.WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    frontend_unit = systemd_dir / tailnet.FRONTEND_SERVICE
    frontend_dropin = systemd_dir / f"{tailnet.FRONTEND_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    for path in (adapter_dropin, frontend_unit, frontend_dropin):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mutated\n")
    snapshot = tailnet.ServiceOwnershipSnapshot(
        files={
            adapter_dropin: (b"adapter-prior\n", 0o600),
            frontend_unit: (b"frontend-prior\n", 0o600),
            frontend_dropin: (b"frontend-dropin-prior\n", 0o600),
        },
        active={tailnet.WORKFLOW_ADAPTER_SERVICE: True, tailnet.FRONTEND_SERVICE: True},
    )
    actions: list[tuple[str, str]] = []
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(tailnet, "daemon_reload", lambda **kwargs: None)
    monkeypatch.setattr(
        tailnet,
        "run_systemctl",
        lambda action, service, **kwargs: actions.append((action, service)),
    )
    monkeypatch.setattr(tailnet, "service_is_active", lambda service, **kwargs: True)

    REAL_RESTORE_SERVICE_OWNERSHIP(
        snapshot,
        tmp_path,
        {"adapter_files", "adapter_service"},
    )

    assert adapter_dropin.read_bytes() == b"adapter-prior\n"
    assert frontend_unit.read_bytes() == b"mutated\n"
    assert frontend_dropin.read_bytes() == b"mutated\n"
    assert actions == [
        ("reset-failed", tailnet.WORKFLOW_ADAPTER_SERVICE),
        ("restart", tailnet.WORKFLOW_ADAPTER_SERVICE),
    ]


def test_exact_control_route_noop_is_not_compensated_after_root_failure(monkeypatch, tmp_path: Path) -> None:
    prior = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={
            "/": {"Proxy": "http://127.0.0.1:5173"},
            tailnet.CONTROL_PATH: {"Proxy": tailnet.CONTROL_TARGET},
        },
        raw={"sealed": "prior"},
    )
    snapshots = iter((prior, prior))
    root_calls: list[str] = []
    control_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: set())
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_set_serve_path", lambda path, target: control_calls.append((path, target)))

    def set_root(target: str) -> None:
        root_calls.append(target)
        if len(root_calls) == 1:
            raise tailnet.TailnetEnvironmentError("root setter failed")

    monkeypatch.setattr(tailnet, "_set_serve_root", set_root)
    with pytest.raises(tailnet.TailnetEnvironmentError, match="root setter failed"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert control_calls == []
    assert root_calls == ["http://127.0.0.1:18081", prior.root_proxy]


def test_serve_snapshot_emits_canonical_root_handler_for_mobile_consumer() -> None:
    snapshot = tailnet.serve_snapshot({
        "Web": {
            "node.example.ts.net:443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:5173/"}}
            }
        }
    })
    assert snapshot.root_proxy == "http://127.0.0.1:5173"
    assert snapshot.handlers["/"]["Proxy"] == "http://127.0.0.1:5173"


def test_url_probe_rejects_redirect_to_different_authority(monkeypatch) -> None:
    class Response:
        status = 200

        def __init__(self, final_url: str) -> None:
            self.final_url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit: int) -> bytes:
            return b"ok"

        def geturl(self) -> str:
            return self.final_url

    class Opener:
        def __init__(self, final_url: str) -> None:
            self.final_url = final_url

        def open(self, request, timeout: float):
            return Response(self.final_url)

    monkeypatch.setattr(
        tailnet.urllib.request,
        "build_opener",
        lambda *args: Opener("https://attacker.example/api/health"),
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="canonical endpoint"):
        tailnet._url_probe("http://127.0.0.1:8000/api/health")

    monkeypatch.setattr(
        tailnet.urllib.request,
        "build_opener",
        lambda *args: Opener("https://node.example.ts.net/bms/"),
    )
    with pytest.raises(tailnet.TailnetEnvironmentError, match="canonical endpoint"):
        tailnet._url_probe("https://node.example.ts.net/")
    report = tailnet._url_probe(
        "https://node.example.ts.net/",
        expected_final_url="https://node.example.ts.net/bms/",
    )
    assert report["final_url"] == "https://node.example.ts.net/bms/"


def test_managed_runtime_listener_requires_complete_container_owned_socket(monkeypatch) -> None:
    container_id = "a" * 64
    cgroup = f"0::/system.slice/docker-{container_id}.scope\n"
    runtime_report = {
        "containers": [{
            "name": "biomodstack-web", "container_id": container_id, "pid": 101,
            "host_pids": [101, 102],
            "process_reports": [
                _nginx_process_report(101, 1, cgroup),
                _nginx_process_report(102, 27, cgroup),
            ],
        }]
    }
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1, 27])
    monkeypatch.setattr(tailnet, "_container_listener_inodes", lambda name, port: [44])
    monkeypatch.setattr(tailnet, "_host_owner_pid_map", lambda container_id, pids: [
        {"container_pid": 1, "host_pid": 101},
        {"container_pid": 27, "host_pid": 102},
    ])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [44])
    owners = {44: [101, 102]}
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: owners)
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda name: [101, 102])
    monkeypatch.setattr(
        tailnet, "_container_process_reports",
        lambda name, container_id, host_pids: [
            _nginx_process_report(101, 1, cgroup),
            _nginx_process_report(102, 27, cgroup),
        ],
    )
    monkeypatch.setattr(
        tailnet,
        "_process_cgroup",
        lambda pid: cgroup,
    )
    monkeypatch.setattr(
        tailnet,
        "_pid_report_for_pids",
        lambda pids: [{"pid": pid, "cgroup": "api-id-12345"} for pid in pids],
    )

    report = REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER(
        runtime_report,
        container_name="biomodstack-web",
        port=18080,
    )
    assert [item["pid"] for item in report["listener_reports"]] == [101, 102]

    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1])
    with pytest.raises(tailnet.TailnetEnvironmentError, match="owner outside"):
        REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER(
            runtime_report,
            container_name="biomodstack-web",
            port=18080,
        )
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1, 27])

    owners[44] = [101, 999]
    with pytest.raises(tailnet.TailnetEnvironmentError, match="owner outside"):
        REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER(
            runtime_report,
            container_name="biomodstack-web",
            port=18080,
        )


def test_managed_runtime_listener_rejects_mixed_epoch_capture(monkeypatch) -> None:
    container_id = "a" * 64
    cgroup = f"0::/system.slice/docker-{container_id}.scope\n"
    reports = [
        _nginx_process_report(101, 1, cgroup),
        _nginx_process_report(102, 27, cgroup),
    ]
    runtime_report = {"containers": [{
        "name": "biomodstack-web", "container_id": container_id, "pid": 101,
        "host_pids": [101, 102], "process_reports": reports,
    }]}
    listener_epochs = iter(([1], [1, 27]))
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: list(next(listener_epochs)))
    monkeypatch.setattr(tailnet, "_container_listener_inodes", lambda name, port: [44])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [44])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {44: [101, 102]})
    monkeypatch.setattr(tailnet, "_host_owner_pid_map", lambda container_id, pids: [
        {"container_pid": 1, "host_pid": 101},
        {"container_pid": 27, "host_pid": 102},
    ])
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda name: [101, 102])
    monkeypatch.setattr(tailnet, "_container_process_reports", lambda name, container_id, pids: reports)
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: cgroup)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="owner outside"):
        REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER(
            runtime_report, container_name="biomodstack-web", port=18080,
        )


def test_host_listener_closure_rejects_same_pid_socket_substitution(monkeypatch) -> None:
    captures = iter(([77], [88]))
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: list(next(captures)))
    monkeypatch.setattr(
        tailnet,
        "_host_listener_inode_owners",
        lambda inodes: {inodes[0]: [101]},
    )
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(
        tailnet,
        "_pid_report_for_pids",
        lambda pids: [{"pid": 101, "executable": "/usr/bin/node"}],
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="unstable"):
        tailnet._host_listener_closure(5173)


def test_host_listener_closure_emits_exact_stable_listener_pids(monkeypatch) -> None:
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [51731])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {51731: [201]})
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(
        tailnet,
        "_pid_report_for_pids",
        lambda pids: [{"pid": 201, "executable": "/usr/bin/node"}],
    )

    closure = tailnet._host_listener_closure(5173)

    assert closure["listener_pids"] == [201]
    assert closure["listener_pids"] == [
        report["pid"] for report in closure["listener_reports"]
    ]
    assert closure["listener_pids"] == sorted({
        pid
        for owners in closure["listener_inode_owners"].values()
        for pid in owners
    })


def test_managed_api_rejects_coherent_extra_container_process(monkeypatch) -> None:
    container_id = "a" * 64
    cgroup = f"0::/system.slice/docker-{container_id}.scope\n"
    runtime = {"containers": [{
        "name": "biomodstack-api", "container_id": container_id, "pid": 101,
        "host_pids": [101, 999],
        "process_reports": [
            {"pid": 101, "cgroup": cgroup}, {"pid": 999, "cgroup": cgroup},
        ],
    }]}
    monkeypatch.setattr(tailnet, "_listener_bind_addresses", lambda port: {"127.0.0.1"})
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1])
    monkeypatch.setattr(tailnet, "_container_listener_inodes", lambda name, port: [77])
    monkeypatch.setattr(tailnet, "_host_owner_pid_map", lambda cid, pids: [
        {"container_pid": 1, "host_pid": 101},
    ])
    monkeypatch.setattr(tailnet, "_host_listener_inodes", lambda port: [77])
    monkeypatch.setattr(tailnet, "_host_listener_inode_owners", lambda inodes: {77: [101, 999]})
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda name: [101, 999])
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: cgroup)

    with pytest.raises(tailnet.TailnetEnvironmentError, match="owner outside"):
        REAL_VALIDATED_RUNTIME_CONTAINER_LISTENER(
            runtime, container_name="biomodstack-api", port=8000,
        )

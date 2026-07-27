from __future__ import annotations

from pathlib import Path

import pytest

import biomodstack_tailnet as tailnet

REAL_SERVICE_OWNERSHIP_SNAPSHOT = tailnet._service_ownership_snapshot
REAL_RESTORE_SERVICE_OWNERSHIP = tailnet._restore_service_ownership


@pytest.fixture(autouse=True)
def _isolate_live_service_ownership(monkeypatch) -> None:
    empty = tailnet.ServiceOwnershipSnapshot(files={}, active={})
    monkeypatch.setattr(tailnet, "_service_ownership_snapshot", lambda root, spec: empty)
    monkeypatch.setattr(tailnet, "_restore_service_ownership", lambda snapshot, root: None)
    monkeypatch.setattr(tailnet, "wait_for_http", lambda url, timeout_seconds=30.0: None)


def test_host_systemd_path_ignores_xdg_config_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cordova-build-config"))
    assert tailnet._host_user_systemd_dir() == Path.home() / ".config" / "systemd" / "user"


def test_adapter_policy_pins_and_verifies_loopback_binding(monkeypatch, tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(tailnet, "_host_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(tailnet, "_tailnet_owner_login", lambda: "owner@example.com")
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "a" * 40)
    tailnet._install_adapter_control_policy(tmp_path)
    dropin = systemd_dir / f"{tailnet.WORKFLOW_ADAPTER_SERVICE}.d" / "99-tailnet-canonical-source.conf"
    assert "Environment=BMS_WORKFLOW_ADAPTER_BIND_HOST=127.0.0.1" in dropin.read_text()

    monkeypatch.setattr(tailnet, "listener_pids", lambda port: [123])
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

    status["TCP"]["443"]["Funnel"] = True
    with pytest.raises(tailnet.TailnetEnvironmentError, match="Funnel"):
        tailnet.serve_snapshot(status)

    status["TCP"]["443"].pop("Funnel")
    status["AllowFunnel"] = {"node.example.ts.net:443": True}
    with pytest.raises(tailnet.TailnetEnvironmentError, match="Funnel"):
        tailnet.serve_snapshot(status)


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
    assert "BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000" in unit
    assert "18002" not in unit
    assert f"VITE_BMS_BUILD_SHA={'a' * 40}" in unit


def test_adapter_root_match_requires_exact_source_revision(monkeypatch, tmp_path: Path) -> None:
    expected_cwd = str((tmp_path / "platform" / "api").resolve())
    monkeypatch.setattr(tailnet, "_pid_report", lambda port: [{"pid": 42, "cwd": expected_cwd}])
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
    local_build = {"revision": "a" * 40, "build_id": "local", "build_time": "now"}
    public_build = {"revision": "b" * 40, "build_id": "tailnet", "build_time": "then"}

    def probe(url: str, *, expect_json: bool = False):
        if not expect_json:
            return {"url": url, "status": 200, "payload": None}
        build = local_build if url == spec.api_health_url else public_build
        return {"url": url, "status": 200, "payload": {"status": "healthy", "build": build}}

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
    build = {"revision": "a" * 40, "build_id": "same", "build_time": "now"}
    monkeypatch.setattr(
        tailnet,
        "_url_probe",
        lambda url, expect_json=False: {
            "url": url,
            "status": 200,
            "payload": {"status": "healthy", "build": build} if expect_json else None,
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


def test_start_selected_environment_probes_only_selected_runtime_and_never_starts_shared_services(
    monkeypatch,
    tmp_path: Path,
) -> None:
    probes: list[tuple[str, bool]] = []
    waits: list[str] = []
    monkeypatch.setattr(
        tailnet,
        "_url_probe",
        lambda url, expect_json=False: probes.append((url, expect_json)) or {"status": 200},
    )
    monkeypatch.setattr(tailnet, "wait_for_http", waits.append)
    monkeypatch.setattr(tailnet, "_validated_production_tailnet_proxy", lambda root: {"validated": True})
    monkeypatch.setattr(tailnet, "_install_adapter_control_policy", lambda root: "owner@example.com")
    monkeypatch.setattr(tailnet, "_adapter_matches_root", lambda root: True)
    monkeypatch.setattr(tailnet, "_adapter_control_policy_matches", lambda login: True)
    monkeypatch.setattr(tailnet, "_install_operator_development_frontend", lambda root: None)
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
        "Id": "proxy-id",
        "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID,
        "State": {"Running": True, "Pid": 123},
        "Path": "/docker-entrypoint.sh",
        "Args": ["nginx", "-g", "daemon off;"],
        "HostConfig": {
            "NetworkMode": "host",
            "RestartPolicy": {"Name": "unless-stopped"},
            "ReadonlyRootfs": True,
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
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/docker/proxy-id\n")
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [1, 27])

    report = tailnet._validated_production_tailnet_proxy(tmp_path)
    assert report["config_sha256"] == config_sha
    assert report["image_id"] == tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID
    assert report["listener_pids"] == [1, 27]

    inspected[0]["Config"]["Labels"][tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL] = "0" * 64
    with pytest.raises(tailnet.TailnetEnvironmentError, match="reviewed config"):
        tailnet._validated_production_tailnet_proxy(tmp_path)

    inspected[0]["Config"]["Labels"][tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL] = config_sha
    inspected[0]["Image"] = "sha256:attacker"
    inspected[0]["Config"]["Image"] = "attacker/proxy:latest"
    with pytest.raises(tailnet.TailnetEnvironmentError, match="executable identity"):
        tailnet._validated_production_tailnet_proxy(tmp_path)


def test_production_tailnet_proxy_requires_container_owned_listener(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / tailnet.PRODUCTION_TAILNET_PROXY_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("server { listen 127.0.0.1:18081; }\n", encoding="utf-8")
    config_sha = tailnet.hashlib.sha256(config.read_bytes()).hexdigest()
    inspected = [{
        "Id": "proxy-id", "Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE_ID,
        "State": {"Running": True, "Pid": 123}, "Path": "/docker-entrypoint.sh",
        "Args": ["nginx", "-g", "daemon off;"],
        "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"}, "ReadonlyRootfs": True},
        "Config": {"Image": tailnet.PRODUCTION_TAILNET_PROXY_IMAGE, "Entrypoint": ["/docker-entrypoint.sh"],
            "Cmd": ["nginx", "-g", "daemon off;"], "Labels": {
                tailnet.PRODUCTION_TAILNET_PROXY_SHA_LABEL: config_sha,
                "com.biomodstack.tailnet-proxy-owner": "compose.core-runtime",
                "com.docker.compose.project": "biomodstack-tailnet-control",
                "com.docker.compose.service": "tailnet-production-proxy"}},
        "Mounts": [{"Source": str(config.resolve()), "Destination": "/etc/nginx/conf.d/default.conf", "RW": False}],
    }]
    monkeypatch.setattr(tailnet, "_run", lambda command, **kwargs: tailnet.subprocess.CompletedProcess(command, 0, tailnet.json.dumps(inspected), ""))
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/docker/proxy-id\n")
    monkeypatch.setattr(tailnet, "_container_listener_pids", lambda name, port: [])
    with pytest.raises(tailnet.TailnetEnvironmentError, match="listener"):
        tailnet._validated_production_tailnet_proxy(tmp_path)


def test_development_runtime_inspects_only_shared_api_container(monkeypatch, tmp_path: Path) -> None:
    revision = "a" * 40
    commands: list[list[str]] = []
    inspected = [{
        "Name": "/biomodstack-api",
        "Id": "api-id",
        "Image": "sha256:api",
        "Path": "uvicorn",
        "Args": ["main:app"],
        "State": {"Pid": 123},
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
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: "0::/docker/api-id\n")
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
    monkeypatch.setattr(tailnet, "_docker_runtime_report", lambda required_names: {
        "containers": [
            {"name": "biomodstack-api", "container_id": "api-id", "image_id": "sha256:api", "revision": revision, "compose_working_dir": str(tmp_path), "pid": 1, "cgroup": "api-id", "cmdline": "api", "cwd": "/app"},
            {"name": "biomodstack-web", "container_id": "web-id", "image_id": "sha256:web", "revision": revision, "compose_working_dir": str(tmp_path), "pid": 2, "cgroup": "web-id", "cmdline": "nginx", "cwd": "/"},
        ]
    })
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    monkeypatch.setattr(tailnet, "_run", lambda args: type("Result", (), {"stdout": ""})())
    report = tailnet._validated_container_runtime(tmp_path, require_web=True)
    assert report["validated_revision"] == revision
    assert report["validated_compose_root"] == str(tmp_path.resolve())


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
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: False)
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
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: False)
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
        handlers=prior.handlers,
        raw={},
    )
    snapshots = iter((prior, selected, prior))
    root_calls: list[str] = []
    ownership = tailnet.ServiceOwnershipSnapshot(files={}, active={})
    restored: list[tuple[tailnet.ServiceOwnershipSnapshot, Path]] = []
    monkeypatch.setattr(tailnet, "_service_ownership_snapshot", lambda root, spec: ownership)
    monkeypatch.setattr(
        tailnet,
        "_restore_service_ownership",
        lambda snapshot, root: restored.append((snapshot, root)),
    )
    monkeypatch.setattr(tailnet, "_start_selected_environment", lambda spec, root: None)
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: False)
    monkeypatch.setattr(tailnet, "_set_serve_root", root_calls.append)
    monkeypatch.setattr(tailnet, "_verify_selected_environment", lambda *args: {"selected_environment": "production"})
    monkeypatch.setattr(
        tailnet,
        "_write_selection_state",
        lambda report: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="rolled back"):
        tailnet.select_tailnet_environment("production", project_root=tmp_path)

    assert root_calls == ["http://127.0.0.1:18081", "http://127.0.0.1:5173"]
    assert restored == [(ownership, tmp_path.resolve())]


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
        handlers=prior.handlers,
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
    monkeypatch.setattr(tailnet, "_ensure_control_route", lambda snapshot: False)
    monkeypatch.setattr(tailnet, "_set_serve_root", lambda target: None)
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

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import biomodstack_release as release


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "release@example.invalid"], cwd=path, check=True)
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_release_revision_is_bound_to_clean_current_head(tmp_path: Path) -> None:
    head = _init_git_repo(tmp_path)
    assert release._validated_source_revision(tmp_path, head) == head

    (tmp_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(release.ReleaseValidationError, match="must be clean"):
        release._validated_source_revision(tmp_path, head)

    subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=tmp_path, check=True)
    (tmp_path / "untracked.txt").write_text("not committed\n", encoding="utf-8")
    with pytest.raises(release.ReleaseValidationError, match="must be clean"):
        release._validated_source_revision(tmp_path, head)


def test_release_rejects_revision_that_is_not_current_head(tmp_path: Path) -> None:
    old_head = _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "second"], cwd=tmp_path, check=True)

    with pytest.raises(release.ReleaseValidationError, match="current HEAD"):
        release._validated_source_revision(tmp_path, old_head)

    with pytest.raises(release.ReleaseValidationError, match="resolve to a Git commit"):
        release._validated_source_revision(tmp_path, "not-a-revision")


def test_release_materializes_exact_blobs_without_hooks_or_private_attributes(
    tmp_path: Path, monkeypatch
) -> None:
    head = _init_git_repo(tmp_path)
    (tmp_path / "compose.core-runtime.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "tracked-link").symlink_to("tracked.txt")
    subprocess.run(
        ["git", "add", "compose.core-runtime.yml", "tracked-link"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "commit", "-qm", "compose"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    hook = tmp_path / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\nprintf 'hook-mutated\\n' > tracked.txt\n", encoding="utf-8")
    hook.chmod(0o755)
    info_attributes = tmp_path / ".git/info/attributes"
    info_attributes.write_text("tracked.txt export-ignore\n", encoding="utf-8")
    subprocess.run(["git", "config", "core.symlinks", "false"], cwd=tmp_path, check=True)
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)
    observed: dict[str, object] = {}

    def fake_build(
        materialized_root: Path,
        identity: release.BuildIdentity,
        *,
        image_refs,
    ) -> None:
        observed["root"] = materialized_root
        observed["tracked"] = (materialized_root / "tracked.txt").read_text(encoding="utf-8")
        observed["has_git_metadata"] = (materialized_root / ".git").exists()
        observed["link_target"] = (materialized_root / "tracked-link").readlink()
        observed["image_refs"] = image_refs

    monkeypatch.setattr(backend, "_build_materialized_images", fake_build)
    identity = release.BuildIdentity(head, "materialized", "2026-07-19T00:00:00Z")
    backend.build_images(identity)

    assert observed["tracked"] == "committed\n"
    assert observed["has_git_metadata"] is False
    assert observed["link_target"] == Path("tracked.txt")
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    materialized_root = observed["root"]
    assert isinstance(materialized_root, Path)
    assert not materialized_root.exists()


def test_release_backend_loads_configured_image_refs_and_build_uses_same_refs(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / "core-runtime.env"
    env_file.write_text(
        "# production refs\n"
        "BMS_API_IMAGE=registry.example/bms-api:release-42\n"
        "BMS_HOST_AGENT_IMAGE=registry.example/bms-host:release-42\n"
        "BMS_CPU_POWER_IMAGE=registry.example/bms-power:release-42\n"
        "BMS_WEB_IMAGE=registry.example/bms-web:release-42\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BMS_CORE_RUNTIME_ENV_FILE", str(env_file))
    for variable in release.IMAGE_REFS.values():
        monkeypatch.delenv(variable, raising=False)

    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)
    assert backend.image_refs == {
        "bms-api": "registry.example/bms-api:release-42",
        "bms-host-agent": "registry.example/bms-host:release-42",
        "bms-cpu-power": "registry.example/bms-power:release-42",
        "bms-web": "registry.example/bms-web:release-42",
    }

    observed: dict[str, str] = {}

    def fake_run(command, *, cwd, env, check):
        observed.update({key: env[key] for key in release.IMAGE_REFS.values()})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    identity = release.BuildIdentity(
        revision="0123456789abcdef0123456789abcdef01234567",
        build_id="release-42",
        build_time="2026-07-22T23:00:00Z",
    )
    backend._build_materialized_images(tmp_path, identity, image_refs=backend.image_refs)

    assert observed == {
        release.IMAGE_REFS[service]: image_ref
        for service, image_ref in backend.image_refs.items()
    }


def test_release_plan_is_explicit_and_validation_precedes_commit() -> None:
    plan = release.release_plan()
    assert plan == (
        "snapshot-known-good",
        "build-images-explicitly",
        "verify-generated-ownership",
        "verify-image-provenance",
        "stop-installed-owner-before-unit-replacement",
        "install-candidate-units",
        "start-candidate-runtime",
        "validate-all-runtime-surfaces-and-ownership",
        "commit-known-good",
    )


class _FailingValidationBackend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def snapshot_known_good(self):
        self.events.append("snapshot")
        return {
            "images": {
                service: ("sha256:old" if service == "bms-api" else f"sha256:old-{service}")
                for service in release.BUILD_SERVICES
            },
            "units": {},
        }

    def build_images(self, identity):
        self.events.append(f"build:{identity.revision}")

    def verify_generated_ownership(self):
        self.events.append("verify-ownership")

    def verify_image_provenance(self, identity):
        self.events.append("verify-images")

    def stop_installed_owner(self):
        self.events.append("stop-installed-owner")

    def install_units(self, identity):
        self.events.append("install-units")

    def start_candidate(self):
        self.events.append("start-candidate")

    def stop_candidate(self):
        self.events.append("stop-candidate")

    def validate_candidate_release(self, identity):
        self.events.append("validate-new")
        raise release.ReleaseValidationError("new runtime failed readiness")

    def revalidate_known_good(self, snapshot):
        self.events.append("validate-rollback")

    def restore_known_good(self, snapshot):
        self.events.append(f"restore:{snapshot['images']['bms-api']}")

    def restart_known_good(self, snapshot):
        self.events.append("restart-old")

    def commit_known_good(self, snapshot, identity):
        self.events.append("commit")


def test_failed_validation_restores_and_revalidates_known_good_runtime() -> None:
    backend = _FailingValidationBackend()
    identity = release.BuildIdentity(
        revision="0123456789abcdef0123456789abcdef01234567",
        build_id="release-17",
        build_time="2026-07-18T04:00:00Z",
    )
    with pytest.raises(release.ReleaseValidationError, match="new runtime failed readiness"):
        release.execute_release(backend, identity)
    assert backend.events == [
        "snapshot",
        f"build:{identity.revision}",
        "verify-ownership",
        "verify-images",
        "stop-installed-owner",
        "install-units",
        "start-candidate",
        "validate-new",
        "stop-candidate",
        "restore:sha256:old",
        "restart-old",
        "validate-rollback",
    ]
    assert "commit" not in backend.events


class _FirstInstallBuildFailureBackend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def snapshot_known_good(self):
        self.events.append("snapshot")
        return {
            "images": {"bms-api": None, "bms-web": "sha256:old-web"},
            "units": {"biomodstack.target": None},
        }

    def build_images(self, identity):
        self.events.append(f"build:{identity.revision}")
        raise RuntimeError("docker build failed")

    def verify_image_provenance(self, identity):
        raise AssertionError("not reached")

    def stop_installed_owner(self):
        raise AssertionError("not reached")

    def install_units(self, identity):
        raise AssertionError("not reached")

    def start_candidate(self):
        raise AssertionError("first install has no runtime to restart")

    def stop_candidate(self):
        self.events.append("stop-candidate")

    def validate_runtime(self, identity=None):
        raise AssertionError("first install has no runtime to validate")

    def restore_known_good(self, snapshot):
        self.events.append("restore-units")

    def restart_known_good(self, snapshot):
        raise AssertionError("first install has no runtime to restart")

    def commit_known_good(self, snapshot, identity):
        raise AssertionError("not reached")


def test_first_install_build_failure_preserves_original_error_without_fake_restart() -> None:
    backend = _FirstInstallBuildFailureBackend()
    identity = release.BuildIdentity(
        revision="0123456789abcdef0123456789abcdef01234567",
        build_id="first-install",
        build_time="2026-07-18T04:00:00Z",
    )
    with pytest.raises(RuntimeError, match="docker build failed") as captured:
        release.execute_release(backend, identity)
    assert type(captured.value) is RuntimeError
    assert backend.events == [
        "snapshot",
        f"build:{identity.revision}",
        "restore-units",
    ]


def test_release_identity_requires_full_sha() -> None:
    with pytest.raises(ValueError, match="full 40-character"):
        release.BuildIdentity(revision="short", build_id="x", build_time="now")


def test_release_backend_rejects_systemd_unsafe_release_root(tmp_path: Path) -> None:
    unsafe_root = tmp_path / "unsafe root"
    unsafe_root.mkdir()
    with pytest.raises(release.ReleaseValidationError, match="systemd-safe"):
        release.ProductionReleaseBackend(repo_root=unsafe_root, allow_first_install=True)


def _managed_container_inspect(
    repo_root: Path, service: str, *, project: str = "biomodstack-core-runtime"
) -> dict[str, object]:
    image_digit = str(release.BUILD_SERVICES.index(service) + 1)
    return {
        "Id": f"container-{service}",
        "Image": f"sha256:{image_digit * 64}",
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.service": service,
                "com.docker.compose.project": project,
                "com.docker.compose.project.working_dir": str(repo_root),
                "com.docker.compose.project.config_files": str(
                    repo_root / "compose.core-runtime.yml"
                ),
            }
        },
    }


def test_snapshot_prefers_running_managed_container_image_id_over_drifted_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed_owner = (tmp_path / "installed-owner").resolve()
    installed_owner.mkdir()
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
    )
    monkeypatch.setattr(
        release.services, "runtime_listener_ownership", lambda *args, **kwargs: {"ok": True}
    )
    backend.image_refs["bms-web"] = "registry.invalid/web:mutable"

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            service = {
                container_name: service
                for service, container_name in release.CONTAINER_NAMES.items()
            }[command[-1]]
            payload = _managed_container_inspect(installed_owner, service)
            return subprocess.CompletedProcess(command, 0, json.dumps([payload]), "")
        if command[:4] == ["systemctl", "--user", "is-active", command[-1]]:
            return subprocess.CompletedProcess(command, 3, "inactive\n", "")
        if command[:4] == ["systemctl", "--user", "is-enabled", command[-1]]:
            return subprocess.CompletedProcess(command, 1, "disabled\n", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:selector-drift\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(
        backend,
        "_snapshot_known_good_validation",
        lambda units: {
            "unit_roots": {release.services.CORE_RUNTIME_SERVICE: str(installed_owner)},
            "runtime_roots": [str(installed_owner)],
            "effective_units": {},
            "surfaces": None,
        },
    )
    snapshot = backend.snapshot_known_good()

    assert snapshot["images"]["bms-web"] == "sha256:" + "4" * 64
    assert snapshot["containers"]["bms-web"]["id"] == "container-bms-web"
    assert snapshot["containers"]["bms-web"]["service"] == "bms-web"
    assert snapshot["image_refs"]["bms-web"] == "registry.invalid/web:mutable"


def test_unit_snapshot_and_restore_include_frontend_dropins_and_active_truth_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    dropin_dir = systemd_dir / f"{release.services.FRONTEND_SERVICE}.d"
    dropin_dir.mkdir(parents=True)
    frontend = systemd_dir / release.services.FRONTEND_SERVICE
    frontend.write_bytes(b"old frontend unit\n")
    override = dropin_dir / "20-owner.conf"
    override.write_bytes(b"[Service]\nEnvironment=OWNER=old\n")
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    commands: list[list[str]] = []
    active_units = {release.services.FRONTEND_SERVICE}

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["systemctl", "--user", "stop"]:
            active_units.clear()
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:4] == ["systemctl", "--user", "is-active", command[-1]]:
            active = command[-1] in active_units
            return subprocess.CompletedProcess(
                command, 0 if active else 3, "active\n" if active else "inactive\n", ""
            )
        if command[:4] == ["systemctl", "--user", "is-enabled", command[-1]]:
            enabled = command[-1] == release.services.FRONTEND_SERVICE
            return subprocess.CompletedProcess(
                command, 0 if enabled else 1, "enabled\n" if enabled else "disabled\n", ""
            )
        if command[:3] == ["docker", "compose", "-f"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(
        backend,
        "_snapshot_known_good_validation",
        lambda units: {
            "unit_roots": {},
            "runtime_roots": [],
            "effective_units": {},
            "surfaces": None,
        },
    )
    snapshot = backend.snapshot_known_good()
    frontend_record = snapshot["units"][release.services.FRONTEND_SERVICE]
    assert frontend_record["active"] is True
    assert set(frontend_record["drop_ins"]) == {"20-owner.conf"}

    frontend.write_bytes(b"candidate frontend\n")
    override.write_bytes(b"candidate override\n")
    (dropin_dir / "99-candidate.conf").write_bytes(b"candidate extra\n")
    backend.restore_known_good(snapshot)
    backend.restart_known_good(snapshot)

    assert frontend.read_bytes() == b"old frontend unit\n"
    assert override.read_bytes() == b"[Service]\nEnvironment=OWNER=old\n"
    assert not (dropin_dir / "99-candidate.conf").exists()
    assert [
        "systemctl",
        "--user",
        "start",
        "--job-mode=ignore-dependencies",
        release.services.FRONTEND_SERVICE,
    ] in commands


def test_unit_restore_rejects_unsafe_unit_and_dropin_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    image_refs = dict(backend.image_refs)
    images = {service: None for service in release.BUILD_SERVICES}
    safe_units = {
        unit_name: {"base": None, "drop_ins": {}, "active": False, "enabled": False}
        for unit_name in release.MANAGED_UNIT_NAMES
    }
    unsafe_unit = {
        "images": images,
        "image_refs": image_refs,
        "units": {
            **safe_units,
            "../foreign.service": {"base": None, "drop_ins": {}, "active": False},
        },
    }
    with pytest.raises(release.ReleaseRollbackError, match="unsafe unit"):
        backend.restore_known_good(unsafe_unit)

    unsafe_dropin = {
        "images": images,
        "image_refs": image_refs,
        "units": {
            **safe_units,
            release.services.FRONTEND_SERVICE: {
                **safe_units[release.services.FRONTEND_SERVICE],
                "drop_ins": {"../escape.conf": ""},
            },
        },
    }
    with pytest.raises(release.ReleaseRollbackError, match="unsafe drop-in"):
        backend.restore_known_good(unsafe_dropin)


def _identity() -> release.BuildIdentity:
    return release.BuildIdentity(
        revision="0123456789abcdef0123456789abcdef01234567",
        build_id="release-operator-7",
        build_time="2026-07-27T15:04:05Z",
    )


def test_operator_frontend_unit_binds_exact_candidate_root_identity_and_prod_api(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "candidate-root").resolve()
    backend = release.ProductionReleaseBackend(
        repo_root=root,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    unit = backend.render_operator_frontend_unit(_identity())

    assert f"PartOf={release.services.TARGET_UNIT}" in unit
    assert (
        f"After=network-online.target {release.services.CORE_RUNTIME_SERVICE}" in unit
    )
    assert f"WantedBy={release.services.TARGET_UNIT}" in unit
    assert f"Environment=BMS_HOME={root}" in unit
    assert f"WorkingDirectory={root / 'platform/frontend'}" in unit
    assert f"ExecStartPre=/usr/bin/env python3 {root / 'scripts/rotate_biomodstack_logs.py'}" in unit
    assert f"ExecStart={root / 'platform/frontend/node_modules/.bin/vite'}" in unit
    assert "Environment=VITE_BMS_BUILD_SHA=0123456789abcdef0123456789abcdef01234567" in unit
    assert "Environment=VITE_BMS_BUILD_ID=release-operator-7" in unit
    assert "Environment=VITE_BMS_BUILD_TIME=2026-07-27T15:04:05Z" in unit
    assert "Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000" in unit
    assert "8002" not in unit
    assert release.services.DEV_TARGET_UNIT not in unit


def test_install_and_activation_use_candidate_frontend_only_after_old_owner_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    events: list[str] = []

    def fake_install(**kwargs):
        events.append("install-core")
        return []

    monkeypatch.setattr(release.services, "install_user_units", fake_install)
    monkeypatch.setattr(
        release.services,
        "assert_runtime_listener_preflight",
        lambda **kwargs: events.append("preflight-after-stop"),
    )
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "stop"]:
            events.append("stop-installed")
        elif command[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(command, 3, "inactive\n", "")
        elif command[:3] == ["systemctl", "--user", "daemon-reload"]:
            events.append("daemon-reload")
        elif command[:3] == ["systemctl", "--user", "enable"]:
            events.append("enable")
        elif command[:3] == ["systemctl", "--user", "start"]:
            events.append("start-candidate")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    backend.stop_installed_owner()
    backend.install_units(_identity())
    backend.start_candidate()

    assert events == [
        "stop-installed",
        "install-core",
        "daemon-reload",
        "preflight-after-stop",
        "enable",
        "start-candidate",
    ]
    installed = (systemd_dir / release.services.FRONTEND_SERVICE).read_text(encoding="utf-8")
    assert installed == backend.render_operator_frontend_unit(_identity())


def _health(identity: release.BuildIdentity, *, revision: str | None = None) -> bytes:
    return json.dumps(
        {
            "readiness": {"ready": True},
            "build": {"revision": revision or identity.revision},
        }
    ).encode()


def _transformed_identity(identity: release.BuildIdentity, *, revision: str | None = None) -> bytes:
    return (
        "const injected = "
        + json.dumps(
            {
                "layer": "frontend",
                "revision": revision or identity.revision,
                "buildId": identity.build_id,
                "buildTime": identity.build_time,
            },
            separators=(",", ":"),
        )
    ).encode()


def _validation_backend(tmp_path: Path, **kwargs) -> release.ProductionReleaseBackend:
    state_dir = kwargs.pop("state_dir", tmp_path / "state")
    return release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=state_dir,
        api_url="http://127.0.0.1:8000/api/health",
        browser_url="http://127.0.0.1:18080/bms/",
        operator_url="http://127.0.0.1:5173/",
        validation_retry_delay=0,
        **kwargs,
    )


def test_validation_rejects_container_browser_only_when_operator_origin_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    monkeypatch.setattr(backend, "_validate_effective_units_and_ownership", lambda identity: None)

    def fake_fetch(url: str):
        if url == backend.api_url:
            return 200, _health(identity)
        if url == backend.browser_url:
            return 200, b"<html>container</html>"
        return 503, b"offline"

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    with pytest.raises(release.ReleaseValidationError, match="operator frontend"):
        backend.validate_candidate_release(identity)


def test_validation_rejects_unknown_operator_vite_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    monkeypatch.setattr(backend, "_validate_effective_units_and_ownership", lambda identity: None)

    def fake_fetch(url: str):
        if url in {backend.api_url, backend.operator_api_url}:
            return 200, _health(identity)
        if url in {backend.browser_url, backend.operator_url}:
            return 200, b"<html>BioModStack</html>"
        if url == backend.operator_identity_url:
            return 200, _transformed_identity(identity, revision="unknown")
        raise AssertionError(url)

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    with pytest.raises(release.ReleaseValidationError, match="frontend identity"):
        backend.validate_candidate_release(identity)


def test_validation_accepts_exact_api_browser_operator_and_frontend_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    monkeypatch.setattr(backend, "_validate_effective_units_and_ownership", lambda identity: None)

    def fake_fetch(url: str):
        if url in {backend.api_url, backend.operator_api_url}:
            return 200, _health(identity)
        if url in {backend.browser_url, backend.operator_url}:
            return 200, b"<!doctype html><html>BioModStack</html>"
        if url == backend.operator_identity_url:
            return 200, _transformed_identity(identity)
        raise AssertionError(url)

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    monkeypatch.setattr(backend, "_candidate_running_image_ids", lambda: {})
    backend.validate_candidate_release(identity)


def test_validation_retries_transport_malformed_and_stale_before_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=4)
    identity = _identity()
    attempts = -1
    monkeypatch.setattr(backend, "_validate_effective_units_and_ownership", lambda identity: None)

    def fake_fetch(url: str):
        nonlocal attempts
        if url == backend.api_url:
            attempts += 1
            if attempts == 0:
                raise release.ReleaseValidationError("transport warming")
            if attempts == 1:
                return 200, b"not-json"
            if attempts == 2:
                return 200, _health(identity, revision="f" * 40)
            return 200, _health(identity)
        if url == backend.operator_api_url:
            return 200, _health(identity)
        if url in {backend.browser_url, backend.operator_url}:
            return 200, b"<html>BioModStack</html>"
        if url == backend.operator_identity_url:
            return 200, _transformed_identity(identity)
        raise AssertionError(url)

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    monkeypatch.setattr(backend, "_candidate_running_image_ids", lambda: {})
    backend.validate_candidate_release(identity)
    assert attempts == 3


def test_validation_requires_effective_candidate_units_proxy_and_listener_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    for unit_name in release.MANAGED_UNIT_NAMES:
        (systemd_dir / unit_name).write_text("managed\n", encoding="utf-8")
    (systemd_dir / release.services.FRONTEND_SERVICE).write_text(
        backend.render_operator_frontend_unit(identity), encoding="utf-8"
    )
    effective_proxy = "http://127.0.0.1:8000"

    def fake_run(command, **kwargs):
        unit_name = command[3]
        property_name = command[4].split("=", 1)[1]
        values = {
            "FragmentPath": str(systemd_dir / unit_name),
            "Environment": (
                f"BMS_HOME={tmp_path.resolve()} "
                f"BMS_DEV_API_PROXY_TARGET={effective_proxy} "
                f"VITE_BMS_BUILD_SHA={identity.revision} "
                f"VITE_BMS_BUILD_ID={identity.build_id} "
                f"VITE_BMS_BUILD_TIME={identity.build_time}"
            ),
            "WorkingDirectory": str(tmp_path.resolve() / "platform/frontend"),
            "ExecStart": str(
                tmp_path.resolve() / "platform/frontend/node_modules/.bin/vite"
            ),
        }
        return subprocess.CompletedProcess(command, 0, values[property_name] + "\n", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(
        release.services,
        "runtime_listener_preflight",
        lambda *args, **kwargs: {
            "components": {
                "workflow-adapter": {"ok": True},
                "api": {"ok": True},
                "frontend": {"ok": True},
            }
        },
    )
    monkeypatch.setattr(
        release.services,
        "runtime_listener_ownership",
        lambda *args, **kwargs: {"ok": True},
    )
    backend._validate_effective_units_and_ownership(identity)

    effective_proxy = "http://127.0.0.1:8002"
    with pytest.raises(release.ReleaseValidationError, match="proxy target"):
        backend._validate_effective_units_and_ownership(identity)


def test_candidate_validation_failure_restores_and_revalidates_exact_prior_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_root = (tmp_path / "candidate-root").resolve()
    old_root = (tmp_path / "known-good-root").resolve()
    candidate_root.mkdir()
    old_root.mkdir()
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)

    old_identity = release.BuildIdentity(
        revision="a" * 40,
        build_id="known-good-11",
        build_time="2026-07-26T10:00:00Z",
    )
    candidate_identity = _identity()
    old_frontend = release.ProductionReleaseBackend(
        repo_root=old_root,
        state_dir=tmp_path / "old-state",
        allow_first_install=True,
    ).render_operator_frontend_unit(old_identity)
    prior_bases = {
        unit_name: (
            old_frontend.encode()
            if unit_name == release.services.FRONTEND_SERVICE
            else f"old unit {unit_name} root={old_root}\n".encode()
        )
        for unit_name in release.MANAGED_UNIT_NAMES
    }
    for unit_name, content in prior_bases.items():
        (systemd_dir / unit_name).write_bytes(content)
    prior_dropin = b"[Service]\nEnvironment=KNOWN_GOOD=1\n"
    prior_dropin_path = (
        systemd_dir / f"{release.services.FRONTEND_SERVICE}.d" / "20-known-good.conf"
    )
    prior_dropin_path.parent.mkdir()
    prior_dropin_path.write_bytes(prior_dropin)

    backend = _validation_backend(
        candidate_root,
        state_dir=tmp_path / "state",
        validation_attempts=1,
    )
    runtime = {
        "units": "old",
        "surface": "old",
        "active": {
            release.services.TARGET_UNIT: True,
            release.services.FRONTEND_SERVICE: True,
            release.services.WORKFLOW_ADAPTER_SERVICE: False,
            release.services.CORE_RUNTIME_SERVICE: True,
        },
        "enabled": {
            release.services.TARGET_UNIT: True,
            release.services.FRONTEND_SERVICE: False,
            release.services.WORKFLOW_ADAPTER_SERVICE: False,
            release.services.CORE_RUNTIME_SERVICE: False,
        },
    }
    events: list[str] = []
    ownership_roots: list[Path] = []

    def fake_install_user_units(**kwargs):
        assert kwargs["project_root"] == candidate_root
        for unit_name in (
            release.services.TARGET_UNIT,
            release.services.WORKFLOW_ADAPTER_SERVICE,
            release.services.CORE_RUNTIME_SERVICE,
        ):
            (systemd_dir / unit_name).write_bytes(f"candidate {unit_name}\n".encode())
        return []

    monkeypatch.setattr(release.services, "install_user_units", fake_install_user_units)

    def fake_listener_preflight(root, mode):
        ownership_roots.append(Path(root))
        return {
            "components": {
                "workflow-adapter": {"ok": True},
                "api": {"ok": True},
                "frontend": {"ok": True},
            }
        }

    monkeypatch.setattr(release.services, "runtime_listener_preflight", fake_listener_preflight)
    monkeypatch.setattr(
        release.services,
        "assert_runtime_listener_preflight",
        lambda *, project_root, runtime_mode: ownership_roots.append(Path(project_root)),
    )
    monkeypatch.setattr(
        release.services,
        "runtime_listener_ownership",
        lambda component, port, owner_kind, project_root: (
            ownership_roots.append(Path(project_root)) or {"ok": True}
        ),
    )

    container_by_service = {
        service: f"container-{service}" for service in release.BUILD_SERVICES
    }

    def current_root() -> Path:
        return candidate_root if runtime["units"] == "candidate" else old_root

    def fake_run(command, **kwargs):
        if command[:3] == ["docker", "compose", "-f"] and "ps" in command:
            service = command[-1]
            return subprocess.CompletedProcess(command, 0, container_by_service[service] + "\n", "")
        if command[:2] == ["docker", "inspect"]:
            container_name = command[-1]
            service = next(
                service
                for service, expected_name in release.CONTAINER_NAMES.items()
                if expected_name == container_name
            )
            payload = _managed_container_inspect(old_root, service)
            payload["Image"] = "sha256:" + str(release.BUILD_SERVICES.index(service) + 1) * 64
            return subprocess.CompletedProcess(command, 0, json.dumps([payload]), "")
        if command[:3] == ["docker", "image", "tag"]:
            events.append(f"tag:{command[3]}:{command[4]}")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            unit_name = command[-1]
            active = runtime["active"][unit_name]
            return subprocess.CompletedProcess(
                command, 0 if active else 3, "active\n" if active else "inactive\n", ""
            )
        if command[:3] == ["systemctl", "--user", "is-enabled"]:
            unit_name = command[-1]
            enabled = runtime["enabled"][unit_name]
            return subprocess.CompletedProcess(
                command, 0 if enabled else 1, "enabled\n" if enabled else "disabled\n", ""
            )
        if command[:3] == ["systemctl", "--user", "show"]:
            unit_name = command[3]
            property_name = command[4].split("=", 1)[1]
            root = current_root()
            properties = {
                "FragmentPath": str(systemd_dir / unit_name),
                "Environment": f"BMS_HOME={root} BMS_RUNTIME_MODE=container",
                "WorkingDirectory": str(root / "platform/frontend"),
                "ExecStart": str(root / "scripts" / f"run-{unit_name}"),
            }
            return subprocess.CompletedProcess(command, 0, properties[property_name] + "\n", "")
        if command[:3] == ["systemctl", "--user", "daemon-reload"]:
            restored = (
                systemd_dir / release.services.FRONTEND_SERVICE
            ).read_bytes() == prior_bases[release.services.FRONTEND_SERVICE]
            runtime["units"] = "old" if restored else "candidate"
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "enable"]:
            runtime["enabled"][command[-1]] = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "disable"]:
            runtime["enabled"][command[-1]] = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "stop"]:
            events.append("stop:" + ",".join(command[3:]))
            for unit_name in command[3:]:
                runtime["active"][unit_name] = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "start"]:
            events.append("start:" + ",".join(command[3:]))
            runtime["active"][command[-1]] = True
            runtime["surface"] = "old" if runtime["units"] == "old" else "candidate"
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)

    def fake_fetch(url: str):
        observed_identity = old_identity if runtime["surface"] == "old" else candidate_identity
        if url in {backend.api_url, backend.operator_api_url}:
            return 200, _health(observed_identity)
        if url in {backend.browser_url, backend.operator_url}:
            return 200, b"<html>BioModStack</html>"
        if url == backend.operator_identity_url:
            if runtime["surface"] == "candidate":
                return 200, _transformed_identity(candidate_identity).replace(
                    candidate_identity.build_id.encode(), b"wrong-candidate"
                )
            return 200, _transformed_identity(old_identity)
        raise AssertionError(url)

    monkeypatch.setattr(backend, "_fetch", fake_fetch)
    monkeypatch.setattr(backend, "build_images", lambda identity: events.append("build"))
    monkeypatch.setattr(
        backend, "verify_generated_ownership", lambda: events.append("verify-ownership")
    )
    monkeypatch.setattr(
        backend, "verify_image_provenance", lambda identity: events.append("verify-images")
    )

    original_start_candidate = backend.start_candidate

    def start_candidate():
        runtime["units"] = "candidate"
        runtime["surface"] = "candidate"
        original_start_candidate()

    monkeypatch.setattr(backend, "start_candidate", start_candidate)
    candidate_validations = 0
    original_candidate_validator = backend.validate_candidate_release

    def validate_candidate(identity):
        nonlocal candidate_validations
        candidate_validations += 1
        original_candidate_validator(identity)

    monkeypatch.setattr(backend, "validate_candidate_release", validate_candidate)

    with pytest.raises(release.ReleaseValidationError, match="frontend identity"):
        release.execute_release(backend, candidate_identity)

    assert candidate_validations == 1
    assert runtime["units"] == "old"
    assert (systemd_dir / release.services.FRONTEND_SERVICE).read_bytes() == prior_bases[
        release.services.FRONTEND_SERVICE
    ]
    assert prior_dropin_path.read_bytes() == prior_dropin
    assert not (
        systemd_dir / f"{release.services.FRONTEND_SERVICE}.d" / "99-candidate.conf"
    ).exists()
    expected_active = {
        release.services.TARGET_UNIT,
        release.services.FRONTEND_SERVICE,
        release.services.CORE_RUNTIME_SERVICE,
    }
    assert {unit for unit, active in runtime["active"].items() if active} == expected_active
    rollback_starts = [
        "start:--job-mode=ignore-dependencies," + unit_name
        for unit_name in (
            release.services.TARGET_UNIT,
            release.services.FRONTEND_SERVICE,
            release.services.CORE_RUNTIME_SERVICE,
        )
    ]
    start_events = [event for event in events if event.startswith("start:")]
    assert start_events[-3:] == rollback_starts
    assert all(
        release.services.WORKFLOW_ADAPTER_SERVICE not in event
        for event in start_events[-3:]
    )
    assert old_root in ownership_roots
    assert ownership_roots[-1] == old_root
    for index, service in enumerate(release.BUILD_SERVICES, start=1):
        assert f"tag:sha256:{str(index) * 64}:{backend.image_refs[service]}" in events


@pytest.mark.parametrize("stop_method", ["stop_installed_owner", "stop_candidate"])
def test_managed_stop_fails_on_nonzero_and_reads_back_every_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop_method: str
) -> None:
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    observed: list[str] = []

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "stop"]:
            return subprocess.CompletedProcess(command, 1, "", "stop failed")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            observed.append(command[-1])
            return subprocess.CompletedProcess(command, 3, "inactive\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    with pytest.raises(release.ReleaseValidationError, match="failed to stop managed units"):
        getattr(backend, stop_method)()
    assert observed == list(release.MANAGED_UNIT_NAMES)


def test_managed_stop_fails_when_any_unit_reads_back_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    observed: list[str] = []

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "stop"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            unit_name = command[-1]
            observed.append(unit_name)
            active = unit_name == release.services.WORKFLOW_ADAPTER_SERVICE
            return subprocess.CompletedProcess(
                command, 0 if active else 3, "active\n" if active else "inactive\n", ""
            )
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    with pytest.raises(release.ReleaseValidationError, match="remained active"):
        backend.stop_installed_owner()
    assert observed == list(release.MANAGED_UNIT_NAMES)


class _FirstInstallCandidateStopFailureBackend(_FailingValidationBackend):
    def snapshot_known_good(self):
        self.events.append("snapshot")
        return {"images": {service: None for service in release.BUILD_SERVICES}, "units": {}}

    def stop_candidate(self):
        self.events.append("stop-candidate")
        raise release.ReleaseValidationError("candidate remained active")


def test_first_install_cleanup_failure_reports_rollback_error_and_preserves_original() -> None:
    backend = _FirstInstallCandidateStopFailureBackend()
    identity = _identity()

    with pytest.raises(release.ReleaseRollbackError, match="candidate remained active") as captured:
        release.execute_release(backend, identity)

    assert isinstance(captured.value.__cause__, release.ReleaseValidationError)
    assert "new runtime failed readiness" in str(captured.value)
    assert "restore" not in ",".join(backend.events)


def test_snapshot_and_rollback_restore_exact_enabled_and_dependency_safe_active_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    prior_active = {
        release.services.TARGET_UNIT,
        release.services.FRONTEND_SERVICE,
        release.services.CORE_RUNTIME_SERVICE,
    }
    prior_enabled = {
        release.services.TARGET_UNIT,
        release.services.WORKFLOW_ADAPTER_SERVICE,
    }
    active = set(prior_active)
    enabled = set(prior_enabled)
    events: list[str] = []
    prior_bytes = {}
    for unit_name in release.MANAGED_UNIT_NAMES:
        content = f"known-good {unit_name}\n".encode()
        prior_bytes[unit_name] = content
        (systemd_dir / unit_name).write_bytes(content)

    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
    )

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "is-active"]:
            is_active = command[-1] in active
            return subprocess.CompletedProcess(
                command, 0 if is_active else 3,
                "active\n" if is_active else "inactive\n", ""
            )
        if command[:3] == ["systemctl", "--user", "is-enabled"]:
            is_enabled = command[-1] in enabled
            return subprocess.CompletedProcess(
                command, 0 if is_enabled else 1,
                "enabled\n" if is_enabled else "disabled\n", ""
            )
        if command[:3] == ["systemctl", "--user", "stop"]:
            assert any(
                (systemd_dir / unit_name).read_bytes().startswith(b"candidate")
                for unit_name in release.MANAGED_UNIT_NAMES
            )
            events.append("stop-all")
            active.clear()
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "daemon-reload"]:
            events.append("daemon-reload")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "enable"]:
            enabled.add(command[-1])
            events.append(f"enable:{command[-1]}")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "disable"]:
            enabled.discard(command[-1])
            events.append(f"disable:{command[-1]}")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "start"]:
            unit_name = command[-1]
            if "--job-mode=ignore-dependencies" not in command:
                active.update(release.MANAGED_UNIT_NAMES)
            else:
                active.add(unit_name)
            events.append(f"start:{unit_name}")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "image", "tag"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    units = backend._unit_snapshot()
    assert {name for name, record in units.items() if record["active"]} == prior_active
    assert {name for name, record in units.items() if record["enabled"]} == prior_enabled
    snapshot = {
        "images": {
            service: "sha256:" + str(index) * 64
            for index, service in enumerate(release.BUILD_SERVICES, start=1)
        },
        "image_refs": dict(backend.image_refs),
        "units": units,
        "validation": {
            "unit_roots": {},
            "runtime_roots": [],
            "effective_units": {},
            "surfaces": None,
        },
    }

    for unit_name in release.MANAGED_UNIT_NAMES:
        (systemd_dir / unit_name).write_bytes(f"candidate {unit_name}\n".encode())
    active.update(release.MANAGED_UNIT_NAMES)
    enabled.clear()

    backend.restore_known_good(snapshot)
    backend.restart_known_good(snapshot)
    backend._validate_known_good_once(snapshot)

    assert events[0] == "stop-all"
    assert active == prior_active
    assert enabled == prior_enabled
    assert [event for event in events if event.startswith("start:")] == [
        f"start:{unit_name}"
        for unit_name in release.MANAGED_UNIT_NAMES
        if unit_name in prior_active
    ]
    assert all((systemd_dir / name).read_bytes() == prior_bytes[name] for name in prior_bytes)


def test_known_good_validation_records_distinct_active_unit_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = {
        release.services.FRONTEND_SERVICE: Path("/home/dalab/biomodstack/dev-test-canonical"),
        release.services.WORKFLOW_ADAPTER_SERVICE: Path(
            "/home/dalab/worktrees/bms-tailnet-production-03dea8c"
        ),
        release.services.CORE_RUNTIME_SERVICE: Path(
            "/home/dalab/worktrees/bms-tailnet-environment-selector-20260726"
        ),
    }
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
    )
    units = {
        unit_name: {
            "base": base64.b64encode(b"unit\n").decode(),
            "drop_ins": {},
            "active": True,
            "enabled": unit_name == release.services.TARGET_UNIT,
        }
        for unit_name in release.MANAGED_UNIT_NAMES
    }

    def fake_property(unit_name: str, property_name: str) -> str:
        if property_name == "FragmentPath":
            return f"/tmp/systemd/{unit_name}"
        if property_name == "Environment":
            return f"BMS_HOME={roots[unit_name]} BMS_RUNTIME_MODE=container"
        if property_name == "WorkingDirectory":
            return str(roots[unit_name] / "platform/frontend")
        if property_name == "ExecStart":
            return str(roots[unit_name] / "scripts/run")
        raise AssertionError(property_name)

    monkeypatch.setattr(backend, "_systemd_property", fake_property)
    monkeypatch.setattr(backend, "_validate_snapshot_listener_ownership", lambda roots: None)
    monkeypatch.setattr(
        backend, "_observe_runtime_surfaces", lambda *args, **kwargs: {"ready": True}
    )

    validation = backend._snapshot_known_good_validation(units)

    assert validation["unit_roots"] == {
        unit_name: str(root) for unit_name, root in roots.items()
    }
    assert set(validation["runtime_roots"]) == {str(root) for root in roots.values()}


def test_snapshot_captures_exact_multi_root_container_topology_without_selector_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend_root = Path("/home/dalab/biomodstack/dev-test-canonical")
    workflow_root = Path("/home/dalab/worktrees/bms-tailnet-production-03dea8c")
    core_root = Path("/home/dalab/worktrees/bms-tailnet-environment-selector-20260726")
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, state_dir=tmp_path / "state")
    monkeypatch.setattr(
        release.services, "runtime_listener_ownership", lambda *args, **kwargs: {"ok": True}
    )
    monkeypatch.setattr(
        backend,
        "_unit_snapshot",
        lambda: {
            unit_name: {"base": None, "drop_ins": {}, "active": True, "enabled": False}
            for unit_name in release.MANAGED_UNIT_NAMES
        },
    )
    monkeypatch.setattr(
        backend,
        "_snapshot_known_good_validation",
        lambda units: {
            "unit_roots": {
                release.services.FRONTEND_SERVICE: str(frontend_root),
                release.services.WORKFLOW_ADAPTER_SERVICE: str(workflow_root),
                release.services.CORE_RUNTIME_SERVICE: str(core_root),
            },
            "runtime_roots": [str(frontend_root), str(workflow_root), str(core_root)],
            "effective_units": {},
            "surfaces": {"ready": True},
        },
    )
    selector_lookups: list[str] = []

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            container_name = command[-1]
            service = {
                "biomodstack-api": "bms-api",
                "biomodstack-host-agent": "bms-host-agent",
                "biomodstack-cpu-power": "bms-cpu-power",
                "biomodstack-web": "bms-web",
            }[container_name]
            root = frontend_root if service == "bms-web" else core_root
            project = "biomodstack-web" if service == "bms-web" else "biomodstack-core-runtime"
            return subprocess.CompletedProcess(
                command, 0, json.dumps([_managed_container_inspect(root, service, project=project)]), ""
            )
        if command[:3] == ["docker", "image", "inspect"]:
            selector_lookups.append(command[-1])
            return subprocess.CompletedProcess(command, 0, "sha256:" + "f" * 64 + "\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    snapshot = backend.snapshot_known_good()

    assert selector_lookups == []
    assert snapshot["containers"]["bms-web"] == {
        "id": "container-bms-web",
        "service": "bms-web",
        "project": "biomodstack-web",
        "config_file": str(frontend_root / "compose.core-runtime.yml"),
        "image_id": "sha256:" + "4" * 64,
        "root": str(frontend_root),
    }
    assert snapshot["containers"]["bms-api"]["root"] == str(core_root)


@pytest.mark.parametrize("bad_project", [None, "", "other project"])
def test_running_container_capture_requires_exact_compose_project_and_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_project: str | None
) -> None:
    owner_root = (tmp_path / "owner").resolve()
    owner_root.mkdir()
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    payload = _managed_container_inspect(owner_root, "bms-api")
    config = payload["Config"]
    assert isinstance(config, dict)
    labels = config["Labels"]
    assert isinstance(labels, dict)
    if bad_project is None:
        labels.pop("com.docker.compose.project")
    else:
        labels["com.docker.compose.project"] = bad_project
    if bad_project == "other project":
        payload["Image"] = "sha256:not-an-id"

    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps([payload]), ""
        ),
    )
    with pytest.raises(RuntimeError, match="expected running managed service"):
        backend._running_container_snapshot("bms-api")


def _candidate_image_inspect(
    service: str, identity: release.BuildIdentity, *, image_id: str | None = None
) -> list[dict[str, object]]:
    return [
        {
            "Id": image_id
            or "sha256:" + str(release.BUILD_SERVICES.index(service) + 5) * 64,
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": identity.revision,
                    "org.opencontainers.image.version": identity.build_id,
                    "org.opencontainers.image.created": identity.build_time,
                }
            },
        }
    ]


@pytest.mark.parametrize(
    ("label", "wrong_value", "message"),
    [
        ("org.opencontainers.image.revision", "f" * 40, "revision"),
        ("org.opencontainers.image.version", "wrong-build", "build id"),
        ("org.opencontainers.image.created", "2020-01-01T00:00:00Z", "build time"),
    ],
)
def test_candidate_image_preflight_requires_full_identity_and_snapshots_immutable_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    wrong_value: str,
    message: str,
) -> None:
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    identity = _identity()

    def fake_run(command, **kwargs):
        assert command[:3] == ["docker", "image", "inspect"]
        image_ref = command[-1]
        service = next(service for service, ref in backend.image_refs.items() if ref == image_ref)
        payload = _candidate_image_inspect(service, identity)
        payload[0]["Config"]["Labels"][label] = wrong_value
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(backend, "_run", fake_run)
    with pytest.raises(release.ReleaseValidationError, match=message):
        backend.verify_image_provenance(identity)

    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                _candidate_image_inspect(
                    next(
                        service
                        for service, ref in backend.image_refs.items()
                        if ref == command[-1]
                    ),
                    identity,
                )
            ),
            "",
        ),
    )
    backend.verify_image_provenance(identity)
    assert backend.candidate_image_ids == {
        service: "sha256:" + str(index + 4) * 64
        for index, service in enumerate(release.BUILD_SERVICES, start=1)
    }


def test_candidate_validation_and_commit_require_exact_running_preflight_image_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "runtime.env"
    env_file.write_text("COMPOSE_PROJECT_NAME=candidate-project\n", encoding="utf-8")
    monkeypatch.setenv("BMS_CORE_RUNTIME_ENV_FILE", str(env_file))
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        allow_first_install=True,
        validation_attempts=1,
    )
    identity = _identity()
    expected_ids = {
        service: "sha256:" + str(index + 4) * 64
        for index, service in enumerate(release.BUILD_SERVICES, start=1)
    }
    backend.candidate_image_ids = dict(expected_ids)
    running_ids = dict(expected_ids)
    selector_inspects: list[list[str]] = []

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            service = {
                container_name: service
                for service, container_name in release.CONTAINER_NAMES.items()
            }[command[-1]]
            payload = _managed_container_inspect(
                tmp_path.resolve(), service, project="candidate-project"
            )
            payload["Image"] = running_ids[service]
            return subprocess.CompletedProcess(command, 0, json.dumps([payload]), "")
        if command[:3] == ["docker", "image", "inspect"]:
            selector_inspects.append(command)
            raise AssertionError("mutable selector must not authorize candidate commit")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(backend, "_observe_runtime_surfaces", lambda expected: {})
    monkeypatch.setattr(backend, "_validate_effective_units_and_ownership", lambda expected: None)

    running_ids["bms-web"] = "sha256:" + "e" * 64
    with pytest.raises(release.ReleaseValidationError, match="running image"):
        backend.validate_candidate_release(identity)

    running_ids["bms-web"] = expected_ids["bms-web"]
    backend.validate_candidate_release(identity)
    backend.commit_known_good({"previous": True}, identity)

    payload = json.loads((tmp_path / "state" / "known-good.json").read_text(encoding="utf-8"))
    assert payload["images"] == expected_ids
    assert selector_inspects == []

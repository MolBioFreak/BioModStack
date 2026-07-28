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


def test_default_validation_window_covers_slow_container_startup(tmp_path: Path) -> None:
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)

    assert backend.validation_attempts * backend.validation_retry_delay >= 60


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


def test_unit_snapshot_and_restore_include_core_dropins_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    unit = release.services.CORE_RUNTIME_SERVICE
    dropin_dir = systemd_dir / f"{unit}.d"
    dropin_dir.mkdir(parents=True)
    base = systemd_dir / unit
    base.write_bytes(b"old core unit\n")
    override = dropin_dir / "20-owner.conf"
    override.write_bytes(b"[Service]\nEnvironment=OWNER=old\n")
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    commands = []
    active = set()

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["systemctl", "--user", "stop"]:
            active.clear()
        if command[:3] == ["systemctl", "--user", "is-active"]:
            ok = command[-1] in active
            return subprocess.CompletedProcess(
                command, 0 if ok else 3, "active\n" if ok else "inactive\n", ""
            )
        if command[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(command, 1, "disabled\n", "")
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
    record = snapshot["units"][unit]
    assert record["active"] is False and set(record["drop_ins"]) == {"20-owner.conf"}
    base.write_bytes(b"candidate core\n")
    override.write_bytes(b"candidate override\n")
    (dropin_dir / "99-candidate.conf").write_bytes(b"extra\n")
    backend.restore_known_good(snapshot)
    backend.restart_known_good(snapshot)
    assert (
        base.read_bytes() == b"old core unit\n"
        and override.read_bytes() == b"[Service]\nEnvironment=OWNER=old\n"
    )
    assert not (dropin_dir / "99-candidate.conf").exists()


def test_unit_restore_rejects_unsafe_unit_and_dropin_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    images = {service: None for service in release.BUILD_SERVICES}
    refs = dict(backend.image_refs)
    safe = {
        unit: {"base": None, "drop_ins": {}, "active": False, "enabled": False}
        for unit in release.MANAGED_UNIT_NAMES
    }
    with pytest.raises(release.ReleaseRollbackError, match="unsafe unit"):
        backend.restore_known_good(
            {
        "images": images,
                "image_refs": refs,
        "units": {
                    **safe,
                    "../foreign.service": {
                        "base": None,
                        "drop_ins": {},
                        "active": False,
            },
        },
    }
        )
    unit = release.services.CORE_RUNTIME_SERVICE
    unsafe = {**safe, unit: {**safe[unit], "drop_ins": {"../escape.conf": ""}}}
    with pytest.raises(release.ReleaseRollbackError, match="unsafe drop-in"):
        backend.restore_known_good(
            {"images": images, "image_refs": refs, "units": unsafe}
        )


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


def test_install_and_activation_touch_only_production_core_after_old_owner_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    protected = {
        u: f"protected {u}\n".encode()
        for u in (
            release.services.API_SERVICE,
            release.services.FRONTEND_SERVICE,
            release.services.WORKFLOW_ADAPTER_SERVICE,
            release.services.TARGET_UNIT,
        )
    }
    for u, b in protected.items():
        (systemd_dir / u).write_bytes(b)
    monkeypatch.setattr(
        release.services,
        "render_user_units",
        lambda *a, **k: {release.services.CORE_RUNTIME_SERVICE: "candidate core\n"},
    )
    events = []
    monkeypatch.setattr(
        release.services,
        "assert_production_core_listener_preflight",
        lambda **k: events.append("preflight"),
    )
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "stop"]:
            events.append("stop")
        elif command[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(command, 3, "inactive\n", "")
        elif command[:3] == ["docker", "rm", "--force"]:
            events.append("remove")
        elif command[:4] == ["docker", "inspect", "--format", "{{.State.Running}}"]:
            return subprocess.CompletedProcess(command, 1, "", "missing")
        elif command[:3] == ["systemctl", "--user", "daemon-reload"]:
            events.append("reload")
        elif command[:3] == ["systemctl", "--user", "enable"]:
            events.append("enable")
        elif command[:3] == ["systemctl", "--user", "start"]:
            events.append("start")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    backend.stop_installed_owner()
    backend.install_units(_identity())
    backend.start_candidate()
    assert events == ["stop", "remove", "reload", "preflight", "enable", "start"]
    assert (
        systemd_dir / release.services.CORE_RUNTIME_SERVICE
    ).read_text() == "candidate core\n"
    assert all((systemd_dir / u).read_bytes() == b for u, b in protected.items())
    assert release.MANAGED_UNIT_NAMES == (release.services.CORE_RUNTIME_SERVICE,)


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


def test_frontend_identity_accepts_vite_transformed_import_meta_env() -> None:
    body = b'''import.meta.env = {"VITE_BMS_BUILD_ID":"release-1","VITE_BMS_BUILD_SHA":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","VITE_BMS_BUILD_TIME":"2026-07-27T21:45:00Z"};const injected = {
  revision: import.meta.env.VITE_BMS_BUILD_SHA,
  buildId: import.meta.env.VITE_BMS_BUILD_ID,
  buildTime: import.meta.env.VITE_BMS_BUILD_TIME
};
export const buildIdentity = Object.freeze({ layer: "frontend", revision: injected.revision });'''
    assert release.ProductionReleaseBackend._frontend_identity(body) == {
        "layer": "frontend",
        "revision": "a" * 40,
        "buildId": "release-1",
        "buildTime": "2026-07-27T21:45:00Z",
    }


def test_validation_rejects_when_production_container_browser_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    monkeypatch.setattr(
        backend, "_validate_effective_units_and_ownership", lambda identity: None
    )
    monkeypatch.setattr(backend, "_candidate_running_image_ids", lambda: {})
    monkeypatch.setattr(
        backend,
        "_fetch",
        lambda url: (
            (200, _health(identity)) if url == backend.api_url else (503, b"offline")
        ),
    )
    with pytest.raises(release.ReleaseValidationError, match="container browser"):
        backend.validate_candidate_release(identity)


def test_production_surface_validation_never_probes_development_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    requested = []

    def fetch(url):
        requested.append(url)
        if url == backend.api_url:
            return 200, _health(identity)
        if url == backend.browser_url:
            return 200, b"<html>production</html>"
        raise AssertionError(f"unowned development URL probed: {url}")

    monkeypatch.setattr(backend, "_fetch", fetch)
    observed = backend._observe_runtime_surfaces(identity)
    assert requested == [backend.api_url, backend.browser_url]
    assert observed == {"api_revision": identity.revision, "browser_html": True}


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


def test_validation_requires_effective_candidate_core_root_and_listener_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _validation_backend(tmp_path, validation_attempts=1)
    identity = _identity()
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    unit = release.services.CORE_RUNTIME_SERVICE
    (systemd_dir / unit).write_text("managed\n")
    effective_root = tmp_path.resolve()

    def fake_run(command, **kwargs):
        prop = command[4].split("=", 1)[1]
        value = (
            str(systemd_dir / command[3])
            if prop == "FragmentPath"
            else f"BMS_HOME={effective_root} BMS_RUNTIME_MODE=container"
        )
        return subprocess.CompletedProcess(command, 0, value + "\n", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(
        release.services,
        "production_core_listener_preflight",
        lambda *a, **k: {
            "components": {
                n: {"ok": True} for n in ("api", "frontend", "cpu-power", "host-agent")
            }
        },
    )
    backend._validate_effective_units_and_ownership(identity)
    effective_root = tmp_path / "foreign"
    with pytest.raises(release.ReleaseValidationError, match="production core root"):
        backend._validate_effective_units_and_ownership(identity)


def test_candidate_failure_restores_core_without_touching_development_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    unit = release.services.CORE_RUNTIME_SERVICE
    protected = {
        u: f"protected {u}\n".encode()
        for u in (
            release.services.API_SERVICE,
            release.services.FRONTEND_SERVICE,
            release.services.WORKFLOW_ADAPTER_SERVICE,
            release.services.TARGET_UNIT,
        )
        }
    for u, b in protected.items():
        (systemd_dir / u).write_bytes(b)
    old = b"old production core\n"
    (systemd_dir / unit).write_bytes(old)
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            3 if command[:3] == ["systemctl", "--user", "is-active"] else 0,
            "inactive\n" if command[:3] == ["systemctl", "--user", "is-active"] else "",
            "",
        ),
    )
    snapshot = {
        "images": {s: None for s in release.BUILD_SERVICES},
        "image_refs": dict(backend.image_refs),
        "units": {
            unit: {
                "base": base64.b64encode(old).decode(),
                "drop_ins": {},
                "active": False,
                "enabled": False,
            }
        },
        "validation": {
            "unit_roots": {},
            "runtime_roots": [],
            "effective_units": {},
            "surfaces": None,
        },
    }
    (systemd_dir / unit).write_bytes(b"failed candidate\n")
    backend.restore_known_good(snapshot)
    assert (systemd_dir / unit).read_bytes() == old and all(
        (systemd_dir / u).read_bytes() == b for u, b in protected.items()
    )


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


def test_managed_stop_fails_when_production_core_reads_back_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    observed = []

    def fake_run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "stop"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            observed.append(command[-1])
            return subprocess.CompletedProcess(command, 0, "active\n", "")
        if command[:3] == ["docker", "rm", "--force"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(backend, "_run", fake_run)
    with pytest.raises(release.ReleaseValidationError, match="remained active"):
        backend.stop_installed_owner()
    assert observed == [release.services.CORE_RUNTIME_SERVICE]


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


def test_snapshot_and_rollback_restore_exact_core_enabled_and_active_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    unit = release.services.CORE_RUNTIME_SERVICE
    prior = b"known-good core\n"
    (systemd_dir / unit).write_bytes(prior)
    active = {unit}
    enabled = set()
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state"
    )

    def run(command, **kwargs):
        if command[:3] == ["systemctl", "--user", "is-active"]:
            ok = command[-1] in active
            return subprocess.CompletedProcess(
                command, 0 if ok else 3, "active\n" if ok else "inactive\n", ""
            )
        if command[:3] == ["systemctl", "--user", "is-enabled"]:
            ok = command[-1] in enabled
            return subprocess.CompletedProcess(
                command, 0 if ok else 1, "enabled\n" if ok else "disabled\n", ""
            )
        if command[:3] == ["systemctl", "--user", "stop"]:
            active.clear()
        elif command[:3] == ["systemctl", "--user", "enable"]:
            enabled.add(command[-1])
        elif command[:3] == ["systemctl", "--user", "disable"]:
            enabled.discard(command[-1])
        elif command[:3] == ["systemctl", "--user", "start"]:
            active.add(command[-1])
        elif command[:3] == ["docker", "image", "tag"]:
            pass
        elif command[:3] != ["systemctl", "--user", "daemon-reload"]:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backend, "_run", run)
    units = backend._unit_snapshot()
    snapshot = {
        "images": {
            s: "sha256:" + str(i) * 64 for i, s in enumerate(release.BUILD_SERVICES, 1)
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
    (systemd_dir / unit).write_bytes(b"candidate\n")
    enabled.add(unit)
    backend.restore_known_good(snapshot)
    backend.restart_known_good(snapshot)
    backend._validate_known_good_once(snapshot)
    assert (
        active == {unit}
        and enabled == set()
        and (systemd_dir / unit).read_bytes() == prior
    )


def test_known_good_validation_records_only_production_core_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path("/home/dalab/biomodstack/prod-main-canonical")
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path, state_dir=tmp_path / "state", allow_first_install=True
    )
    unit = release.services.CORE_RUNTIME_SERVICE
    units = {
        unit: {
            "base": base64.b64encode(b"unit\n").decode(),
            "drop_ins": {},
            "active": True,
            "enabled": True,
        }
    }

    def prop(name, key):
        return (
            f"/tmp/systemd/{name}"
            if key == "FragmentPath"
            else (
                f"BMS_HOME={root} BMS_RUNTIME_MODE=container"
                if key == "Environment"
                else str(root / "scripts/run")
            )
    )

    monkeypatch.setattr(backend, "_systemd_property", prop)
    monkeypatch.setattr(
        backend, "_validate_snapshot_listener_ownership", lambda roots: None
    )
    monkeypatch.setattr(
        backend, "_observe_runtime_surfaces", lambda *a, **k: {"ready": True}
    )
    validation = backend._snapshot_known_good_validation(units)
    assert validation["unit_roots"] == {unit: str(root)} and validation[
        "runtime_roots"
    ] == [str(root)]


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

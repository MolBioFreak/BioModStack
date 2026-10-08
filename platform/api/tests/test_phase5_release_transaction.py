from __future__ import annotations

import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace

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
        "render-install-units",
        "restart-container-runtime",
        "validate-api-readiness-and-provenance",
        "validate-browser-health",
        "commit-known-good",
    )


def test_release_snapshots_operator_frontend_unit(tmp_path: Path, monkeypatch) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / release.services.CORE_RUNTIME_SERVICE).write_text("core-old\n")
    (systemd_dir / release.services.FRONTEND_SERVICE).write_text("frontend-old\n")
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(
        release.services,
        "render_user_units",
        lambda *args, **kwargs: (
            {release.services.FRONTEND_SERVICE: "Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002\n"}
            if kwargs.get("runtime_mode") == release.services.DEV_RUNTIME_MODE
            else {release.services.CORE_RUNTIME_SERVICE: "core-new\n"}
        ),
    )
    monkeypatch.setattr(
        release.services,
        "runtime_api_url",
        lambda mode, project_root=None: (
            "http://127.0.0.1:8000" if mode == "container" else "http://127.0.0.1:18002"
        ),
    )
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)

    snapshot = backend._unit_snapshot()

    assert set(snapshot) == {
        release.services.CORE_RUNTIME_SERVICE,
        release.services.FRONTEND_SERVICE,
    }


def test_release_installs_operator_frontend_for_managed_api(tmp_path: Path, monkeypatch) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: systemd_dir)
    monkeypatch.setattr(
        release.services,
        "install_user_units",
        lambda *args, **kwargs: calls.append("install-container"),
    )
    monkeypatch.setattr(
        release.services,
        "daemon_reload",
        lambda *args, **kwargs: calls.append("daemon-reload"),
    )
    monkeypatch.setattr(
        release.services,
        "render_user_units",
        lambda *args, **kwargs: {
            release.services.FRONTEND_SERVICE: (
                "Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002\n"
                "ExecStart=/release/scripts/run_biomodstack_frontend.sh\n"
            )
        },
    )
    monkeypatch.setattr(
        release.services,
        "runtime_api_url",
        lambda mode, project_root=None: (
            "http://127.0.0.1:8000" if mode == "container" else "http://127.0.0.1:18002"
        ),
    )
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)

    backend.install_units()

    frontend = (systemd_dir / release.services.FRONTEND_SERVICE).read_text()
    assert "BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000" in frontend
    assert "BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002" not in frontend
    assert calls == ["install-container", "daemon-reload"]


def test_release_restarts_operator_frontend_after_container_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        release.services,
        "restart_all",
        lambda *args, **kwargs: calls.append("restart-container"),
    )
    monkeypatch.setattr(
        release.services,
        "run_systemctl",
        lambda *args, **kwargs: calls.append(("systemctl", args))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        release.services,
        "assert_runtime_listener_preflight",
        lambda root, mode: calls.append(("preflight", mode)),
    )
    monkeypatch.setattr(
        release.services,
        "wait_for_http",
        lambda url, timeout_seconds: calls.append(("wait", url)),
    )
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)

    backend.restart_runtime()

    assert calls == [
        "restart-container",
        ("systemctl", ("stop", release.services.FRONTEND_SERVICE)),
        ("preflight", release.services.DEV_RUNTIME_MODE),
        ("systemctl", ("start", release.services.FRONTEND_SERVICE)),
        ("wait", release.OPERATOR_FRONTEND_URL),
    ]


def test_release_validates_operator_frontend_proxy_and_owner(tmp_path: Path, monkeypatch) -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    identity = release.BuildIdentity(revision, "operator", "2026-07-26T00:00:00Z")
    backend = release.ProductionReleaseBackend(
        repo_root=tmp_path,
        api_url="http://api/health",
        browser_url="http://stable/bms/",
        allow_first_install=True,
    )
    payload = (
        '{"readiness":{"ready":true},"build":{"revision":"' + revision + '"}}'
    ).encode()
    responses = {
        "http://api/health": (200, payload),
        "http://stable/bms/": (200, b"<html>stable</html>"),
        release.OPERATOR_FRONTEND_URL: (200, b"<html>operator</html>"),
        release.OPERATOR_API_HEALTH_URL: (200, payload),
    }
    monkeypatch.setattr(backend, "_fetch", lambda url: responses[url])
    monkeypatch.setattr(
        release.services,
        "runtime_listener_preflight",
        lambda *args, **kwargs: {
            "components": {
                "frontend": {
                    "ok": True,
                    "listeners": [{"owner": "managed-dev-frontend"}],
                }
            }
        },
    )

    backend.validate_runtime(identity)


class _FailingValidationBackend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def snapshot_known_good(self):
        self.events.append("snapshot")
        return {"images": {"bms-api": "sha256:old"}, "units": {}}

    def build_images(self, identity):
        self.events.append(f"build:{identity.revision}")

    def verify_generated_ownership(self):
        self.events.append("verify-ownership")

    def verify_image_provenance(self, identity):
        self.events.append("verify-images")

    def install_units(self):
        self.events.append("install-units")

    def restart_runtime(self):
        self.events.append("restart")

    def validate_runtime(self, identity=None):
        if identity is not None:
            self.events.append("validate-new")
            raise release.ReleaseValidationError("new runtime failed readiness")
        self.events.append("validate-rollback")

    def restore_known_good(self, snapshot):
        self.events.append(f"restore:{snapshot['images']['bms-api']}")

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
        "install-units",
        "restart",
        "validate-new",
        "restore:sha256:old",
        "restart",
        "validate-rollback",
    ]
    assert "commit" not in backend.events


def test_first_install_restore_stops_partial_operator_frontend(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    backend = release.ProductionReleaseBackend(repo_root=tmp_path, allow_first_install=True)
    monkeypatch.setattr(release.services, "get_user_systemd_dir", lambda: tmp_path / "systemd")
    monkeypatch.setattr(release.services, "daemon_reload", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, **kwargs: calls.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    backend.restore_known_good({"images": {}, "image_refs": {}, "units": {}})

    assert calls == [
        [
            "systemctl",
            "--user",
            "stop",
            release.services.TARGET_UNIT,
            release.services.FRONTEND_SERVICE,
            release.services.WORKFLOW_ADAPTER_SERVICE,
            release.services.CORE_RUNTIME_SERVICE,
        ]
    ]


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

    def install_units(self):
        raise AssertionError("not reached")

    def restart_runtime(self):
        raise AssertionError("first install has no runtime to restart")

    def validate_runtime(self, identity=None):
        raise AssertionError("first install has no runtime to validate")

    def restore_known_good(self, snapshot):
        self.events.append("restore-units")

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

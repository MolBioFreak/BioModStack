from __future__ import annotations

import sys
import subprocess
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
        "render-install-units",
        "restart-container-runtime",
        "validate-api-readiness-and-provenance",
        "validate-browser-health",
        "commit-known-good",
    )


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

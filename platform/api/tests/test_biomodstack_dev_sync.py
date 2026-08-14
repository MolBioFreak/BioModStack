from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "biomodstack_dev_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("biomodstack_dev_sync", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_units_make_sixty_second_policy_obvious() -> None:
    sync = load_module()

    units = sync.render_sync_units(REPO_ROOT)

    timer = units["biomodstack-dev-sync.timer"]
    service = units["biomodstack-dev-sync.service"]
    assert "Description=BioModStack Development origin/test sync every 60 seconds" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert f"ExecStart=/usr/bin/env python3 {MODULE_PATH} --once" in service


def test_install_sync_units_uses_a_stable_libexec_copy(tmp_path: Path, monkeypatch) -> None:
    sync = load_module()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(sync, "_run", lambda _cwd, *command, **kwargs: calls.append(command))

    systemd_dir = tmp_path / "systemd"
    libexec_dir = tmp_path / "libexec"
    sync.install_sync_units(
        REPO_ROOT,
        systemd_dir,
        state_dir=tmp_path / "state",
        libexec_dir=libexec_dir,
    )

    installed_script = libexec_dir / "biomodstack_dev_sync.py"
    assert installed_script.read_bytes() == MODULE_PATH.read_bytes()
    service = (systemd_dir / "biomodstack-dev-sync.service").read_text(encoding="utf-8")
    assert f"ExecStart=/usr/bin/env python3 {installed_script} --once" in service
    assert ("systemctl", "--user", "enable", "--now", "biomodstack-dev-sync.timer") in calls


def test_plan_sync_fast_forwards_when_remote_is_newer() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
    )

    assert decision == "fast-forward-deploy"


def test_plan_sync_redeploys_current_remote_when_live_identity_is_stale() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
    )

    assert decision == "deploy-current"


def test_plan_sync_does_not_restart_current_services_when_health_is_temporarily_unavailable() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision=None,
        remote_descends_from_local=True,
    )

    assert decision == "blocked-health-unavailable"


def test_plan_sync_blocks_dirty_or_diverged_canonical_tree() -> None:
    sync = load_module()

    assert sync.plan_sync(
        dirty=True,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
    ) == "blocked-dirty"
    assert sync.plan_sync(
        dirty=False,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=False,
    ) == "blocked-diverged"


def test_plan_sync_is_idle_only_when_remote_and_live_identity_match() -> None:
    sync = load_module()

    assert sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision="b" * 40,
        remote_descends_from_local=True,
    ) == "idle"

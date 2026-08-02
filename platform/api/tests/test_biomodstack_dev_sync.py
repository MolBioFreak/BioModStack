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

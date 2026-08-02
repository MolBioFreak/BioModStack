#!/usr/bin/env python3
"""Fail-closed origin/test synchronizer for canonical native Development.

Policy: poll every 60 seconds. Canonical Development is deployment-owned; a
human or agent must use an isolated worktree and push a fast-forward update to
origin/test rather than editing the canonical checkout.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from textwrap import dedent
from typing import Literal

SYNC_SERVICE = "biomodstack-dev-sync.service"
SYNC_TIMER = "biomodstack-dev-sync.timer"
SYNC_INTERVAL_SECONDS = 60
DEFAULT_CANONICAL_ROOT = Path("/home/dalab/biomodstack/dev-test-canonical")
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "biomodstack"

SyncDecision = Literal[
    "blocked-dirty",
    "blocked-diverged",
    "fast-forward-deploy",
    "deploy-current",
    "idle",
]


def plan_sync(
    *,
    dirty: bool,
    local_revision: str,
    remote_revision: str,
    deployed_revision: str | None,
    remote_descends_from_local: bool,
) -> SyncDecision:
    if dirty:
        return "blocked-dirty"
    if not remote_descends_from_local:
        return "blocked-diverged"
    if local_revision != remote_revision:
        return "fast-forward-deploy"
    if deployed_revision != remote_revision:
        return "deploy-current"
    return "idle"


def render_sync_units(project_root: Path) -> dict[str, str]:
    root = project_root.resolve()
    script = root / "scripts" / "biomodstack_dev_sync.py"
    service = dedent(
        f"""\
        [Unit]
        Description=BioModStack Development origin/test sync transaction
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=oneshot
        Environment=BMS_DEV_CANONICAL_ROOT={root}
        ExecStart=/usr/bin/env python3 {script} --once
        TimeoutStartSec=180
        """
    )
    timer = dedent(
        f"""\
        [Unit]
        Description=BioModStack Development origin/test sync every 60 seconds

        [Timer]
        OnBootSec={SYNC_INTERVAL_SECONDS}s
        OnUnitActiveSec={SYNC_INTERVAL_SECONDS}s
        AccuracySec=1s
        Persistent=true
        Unit={SYNC_SERVICE}

        [Install]
        WantedBy=timers.target
        """
    )
    return {SYNC_SERVICE: service, SYNC_TIMER: timer}


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run(
        list(args),
        cwd=root,
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = _run(root, "git", *args, check=check)
    return result.stdout.strip()


def _deployed_revision(root: Path) -> str | None:
    sys.path.insert(0, str(root))
    import biomodstack_services as services  # pylint: disable=import-outside-toplevel

    url = services.runtime_api_health_url(services.DEV_RUNTIME_MODE, project_root=root)
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.load(response)
    except Exception:
        return None
    build = payload.get("build") if isinstance(payload, dict) else None
    revision = build.get("revision") if isinstance(build, dict) else None
    return revision if isinstance(revision, str) and len(revision) == 40 else None


def _write_receipt(state_dir: Path, payload: dict[str, object]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "dev-sync.json"
    fd, temporary_name = tempfile.mkstemp(prefix=".dev-sync-", dir=state_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def sync_once(root: Path, state_dir: Path) -> SyncDecision:
    root = root.resolve()
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise RuntimeError(f"canonical Development is not a Git worktree: {root}")

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dev-sync.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        dirty = bool(_git(root, "status", "--porcelain"))
        local = _git(root, "rev-parse", "HEAD")
        _git(root, "fetch", "--quiet", "origin", "+refs/heads/test:refs/remotes/origin/test")
        remote = _git(root, "rev-parse", "refs/remotes/origin/test")
        ancestry = _run(
            root,
            "git",
            "merge-base",
            "--is-ancestor",
            local,
            remote,
            check=False,
        ).returncode == 0
        deployed = _deployed_revision(root)
        decision = plan_sync(
            dirty=dirty,
            local_revision=local,
            remote_revision=remote,
            deployed_revision=deployed,
            remote_descends_from_local=ancestry,
        )
        receipt: dict[str, object] = {
            "decision": decision,
            "local_revision": local,
            "remote_revision": remote,
            "deployed_revision_before": deployed,
            "poll_interval_seconds": SYNC_INTERVAL_SECONDS,
        }
        if decision.startswith("blocked-"):
            _write_receipt(state_dir, receipt)
            raise RuntimeError(f"Development sync {decision}: canonical={local} origin/test={remote}")
        if decision == "idle":
            _write_receipt(state_dir, receipt)
            return decision
        if decision == "fast-forward-deploy":
            _git(root, "merge", "--ff-only", "refs/remotes/origin/test")

        manager = root / "scripts" / "manage_desktop_services.py"
        _run(root, sys.executable, str(manager), "restart", "--runtime", "dev")
        deployed_after = _deployed_revision(root)
        if deployed_after != remote:
            raise RuntimeError(
                f"Development exposure mismatch after deployment: expected {remote}, got {deployed_after or 'unavailable'}"
            )
        receipt["deployed_revision_after"] = deployed_after
        receipt["status"] = "deployed"
        _write_receipt(state_dir, receipt)
        return decision


def install_units(root: Path, systemd_dir: Path) -> list[Path]:
    systemd_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in render_sync_units(root).items():
        path = systemd_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync canonical BioModStack Development from origin/test every 60 seconds")
    parser.add_argument("--once", action="store_true", help="run one synchronization transaction")
    parser.add_argument("--install", action="store_true", help="install and enable the 60-second user timer")
    parser.add_argument("--root", type=Path, default=Path(os.getenv("BMS_DEV_CANONICAL_ROOT", DEFAULT_CANONICAL_ROOT)))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--systemd-dir", type=Path, default=Path.home() / ".config" / "systemd" / "user")
    args = parser.parse_args()
    try:
        if args.install:
            install_units(args.root, args.systemd_dir)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", SYNC_TIMER], check=True)
            print(f"Installed {SYNC_TIMER}: origin/test is checked every {SYNC_INTERVAL_SECONDS} seconds")
            return 0
        if args.once:
            decision = sync_once(args.root, args.state_dir)
            print(json.dumps({"decision": decision, "poll_interval_seconds": SYNC_INTERVAL_SECONDS}, sort_keys=True))
            return 0
        parser.error("one of --once or --install is required")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

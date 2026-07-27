from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "manage_ai_environment.py"


def load_manager_module():
    spec = importlib.util.spec_from_file_location("manage_ai_environment", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_systemd_environment_honors_profile_configured_ports() -> None:
    manager = load_manager_module()

    parsed = manager.parse_systemd_environment(
        'BMS_HOME=/tmp/test BMS_API_BIND_PORT=18002 BMS_QUOTED="value with spaces"'
    )

    assert parsed["BMS_API_BIND_PORT"] == "18002"
    assert parsed["BMS_QUOTED"] == "value with spaces"


def run(*args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env=merged,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def seeded_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    canonical = tmp_path / "canonical-test"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "test", str(source)], check=True, capture_output=True)
    git(source, "config", "user.name", "Test User")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "README.md").write_text("seed\n", encoding="utf-8")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "seed")
    git(source, "branch", "main")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "origin", "main", "test")
    subprocess.run(["git", "clone", "--branch", "test", str(remote), str(canonical)], check=True, capture_output=True)
    git(canonical, "config", "user.name", "Test User")
    git(canonical, "config", "user.email", "test@example.invalid")
    return remote, canonical


def test_create_allocates_isolated_ports_state_and_local_only_branch(tmp_path: Path) -> None:
    remote, canonical = seeded_repo(tmp_path)
    state_root = tmp_path / "state"
    worktree_root = tmp_path / "worktrees"
    env = {
        "BMS_AI_STATE_ROOT": str(state_root),
        "BMS_AI_WORKTREE_ROOT": str(worktree_root),
    }

    result = run("create", "--id", "demo", "--repo", str(canonical), env=env)
    receipt = json.loads(result.stdout)
    worktree = Path(receipt["worktree"])

    assert receipt["branch"] == "ai/demo"
    assert receipt["base_branch"] == "origin/test"
    assert receipt["api_port"] != receipt["web_port"]
    assert receipt["state_root"].startswith(str(state_root))
    assert git(worktree, "branch", "--show-current") == "ai/demo"
    assert git(worktree, "status", "--porcelain") == ""
    assert git(canonical, "ls-remote", "--heads", "origin", "ai/demo") == ""


def test_unpromoted_environment_cannot_close_without_explicit_discard(tmp_path: Path) -> None:
    _, canonical = seeded_repo(tmp_path)
    env = {
        "BMS_AI_STATE_ROOT": str(tmp_path / "state"),
        "BMS_AI_WORKTREE_ROOT": str(tmp_path / "worktrees"),
    }
    run("create", "--id", "demo", "--repo", str(canonical), env=env)

    result = run("close", "--id", "demo", "--repo", str(canonical), env=env, check=False)

    assert result.returncode != 0
    assert "not promoted to origin/test" in result.stderr


def test_promote_pushes_test_updates_canonical_and_allows_close(tmp_path: Path) -> None:
    _, canonical = seeded_repo(tmp_path)
    state_root = tmp_path / "state"
    worktree_root = tmp_path / "worktrees"
    env = {
        "BMS_AI_STATE_ROOT": str(state_root),
        "BMS_AI_WORKTREE_ROOT": str(worktree_root),
    }
    receipt = json.loads(run("create", "--id", "demo", "--repo", str(canonical), env=env).stdout)
    worktree = Path(receipt["worktree"])
    (worktree / "feature.txt").write_text("done\n", encoding="utf-8")
    git(worktree, "add", "feature.txt")
    git(worktree, "commit", "-m", "feat: demo")
    tip = git(worktree, "rev-parse", "HEAD")

    promoted = json.loads(
        run("promote", "--id", "demo", "--repo", str(canonical), "--no-deploy", env=env).stdout
    )

    assert promoted["promoted_revision"] == tip
    assert git(canonical, "rev-parse", "HEAD") == tip
    assert git(canonical, "ls-remote", "origin", "refs/heads/test").split()[0] == tip

    closed = json.loads(run("close", "--id", "demo", "--repo", str(canonical), env=env).stdout)
    assert closed["status"] == "closed"
    assert not worktree.exists()
    assert "ai/demo" not in git(canonical, "branch", "--list", "ai/demo")

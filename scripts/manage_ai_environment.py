#!/usr/bin/env python3
"""Create, run, promote, and close bounded BioModStack AI environments."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
PORT_START = 20000
PORT_END = 29998


class EnvironmentError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise EnvironmentError(f"{' '.join(command)}: {detail}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> str:
    return run(["git", "-C", str(repo), *args], check=check).stdout.strip()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_id(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise EnvironmentError("environment id must match [a-z0-9][a-z0-9-]{0,47}")
    return value


def state_root() -> Path:
    return Path(os.environ.get("BMS_AI_STATE_ROOT", Path.home() / ".local/state/biomodstack/ai-environments")).resolve()


def worktree_root() -> Path:
    return Path(os.environ.get("BMS_AI_WORKTREE_ROOT", "/home/dalab/worktrees/bms-ai")).resolve()


def receipt_path(environment_id: str) -> Path:
    return state_root() / f"{environment_id}.json"


def load_receipt(environment_id: str) -> dict[str, object]:
    path = receipt_path(environment_id)
    if not path.is_file():
        raise EnvironmentError(f"unknown AI environment: {environment_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_receipt(receipt: dict[str, object]) -> None:
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    path = receipt_path(str(receipt["id"]))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def lifecycle_lock() -> Iterator[None]:
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lifecycle.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def local_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def occupied_receipt_ports() -> set[int]:
    ports: set[int] = set()
    root = state_root()
    if not root.exists():
        return ports
    for path in root.glob("*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("status") == "closed":
            continue
        for key in ("api_port", "web_port"):
            value = receipt.get(key)
            if isinstance(value, int):
                ports.add(value)
    return ports


def allocate_port_pair() -> tuple[int, int]:
    occupied = occupied_receipt_ports()
    for api_port in range(PORT_START, PORT_END, 2):
        web_port = api_port + 1
        if api_port in occupied or web_port in occupied:
            continue
        if local_port_available(api_port) and local_port_available(web_port):
            return api_port, web_port
    raise EnvironmentError("no free AI environment port pair is available")


def assert_canonical_test(repo: Path) -> str:
    if git(repo, "branch", "--show-current") != "test":
        raise EnvironmentError("--repo must be the canonical test worktree on branch test")
    if git(repo, "status", "--porcelain"):
        raise EnvironmentError("canonical test worktree is dirty")
    git(repo, "fetch", "origin", "test")
    local = git(repo, "rev-parse", "test")
    remote = git(repo, "rev-parse", "origin/test")
    if local != remote:
        raise EnvironmentError("canonical test must exactly match origin/test")
    return remote


def command_create(args: argparse.Namespace) -> dict[str, object]:
    environment_id = validate_id(args.id)
    repo = Path(args.repo).resolve()
    with lifecycle_lock():
        existing = receipt_path(environment_id)
        if existing.exists() and load_receipt(environment_id).get("status") != "closed":
            raise EnvironmentError(f"AI environment already exists: {environment_id}")
        base = assert_canonical_test(repo)
        branch = f"ai/{environment_id}"
        if git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False) == "":
            probe = run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
            if probe.returncode == 0:
                raise EnvironmentError(f"local branch already exists: {branch}")
        remote_probe = git(repo, "ls-remote", "--heads", "origin", branch)
        if remote_probe:
            raise EnvironmentError(f"remote AI branch is forbidden and already exists: {branch}")
        worktree = worktree_root() / environment_id
        if worktree.exists():
            raise EnvironmentError(f"worktree path already exists: {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        api_port, web_port = allocate_port_pair()
        git(repo, "worktree", "add", "-b", branch, str(worktree), "origin/test")
        isolated_state = state_root() / "runtime" / environment_id
        isolated_state.mkdir(parents=True, exist_ok=True)
        receipt: dict[str, object] = {
            "id": environment_id,
            "status": "created",
            "branch": branch,
            "base_branch": "origin/test",
            "base_revision": base,
            "worktree": str(worktree),
            "state_root": str(isolated_state),
            "api_port": api_port,
            "web_port": web_port,
            "api_url": f"http://127.0.0.1:{api_port}",
            "web_url": f"http://127.0.0.1:{web_port}",
            "tailnet_exposed": False,
            "bioxp_mutations_enabled": False,
            "created_at": now(),
        }
        save_receipt(receipt)
        return receipt


def unit_names(environment_id: str) -> tuple[str, str]:
    return f"bms-ai-{environment_id}-api", f"bms-ai-{environment_id}-web"


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["systemctl", "--user", *args], check=check)


def parse_systemd_environment(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for entry in shlex.split(value):
        key, separator, item = entry.partition("=")
        if separator and key:
            parsed[key] = item
    return parsed


def service_environment(unit: str) -> dict[str, str]:
    value = systemctl("show", unit, "--property=Environment", "--value").stdout.strip()
    return parse_systemd_environment(value)


def installed_dev_urls() -> tuple[str, str]:
    api_environment = service_environment("biomodstack-api.service")
    web_environment = service_environment("biomodstack-frontend.service")
    api_port = api_environment.get("BMS_API_BIND_PORT")
    web_port = web_environment.get("BMS_DEV_WEB_HOST_PORT")
    if not api_port or not api_port.isdigit() or not web_port or not web_port.isdigit():
        raise EnvironmentError("installed development units do not publish valid API/web ports")
    return f"http://127.0.0.1:{api_port}/api/health", f"http://127.0.0.1:{web_port}/"


def wait_http(url: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
                error = f"HTTP {response.status}"
        except Exception as exc:  # readiness loop reports the final reason
            error = str(exc)
        time.sleep(1)
    raise EnvironmentError(f"readiness timeout for {url}: {error}")


def command_start(args: argparse.Namespace) -> dict[str, object]:
    environment_id = validate_id(args.id)
    receipt = load_receipt(environment_id)
    worktree = Path(str(receipt["worktree"]))
    if not worktree.is_dir():
        raise EnvironmentError("AI worktree is missing")
    api_port_value = receipt.get("api_port")
    web_port_value = receipt.get("web_port")
    if not isinstance(api_port_value, int) or not isinstance(web_port_value, int):
        raise EnvironmentError("AI environment receipt has invalid ports")
    api_port = api_port_value
    web_port = web_port_value
    isolated = Path(str(receipt["state_root"]))
    for child in ("inputs", "work", "weights", "msa_cache", "sabdab_cache"):
        (isolated / child).mkdir(parents=True, exist_ok=True)
    frontend = worktree / "platform/frontend"
    if not (frontend / "node_modules").exists():
        run(["npm", "ci"], cwd=frontend)
    api_unit, web_unit = unit_names(environment_id)
    systemctl("stop", api_unit, web_unit, check=False)
    systemctl("reset-failed", api_unit, web_unit, check=False)
    api_command = [
        "systemd-run", "--user", f"--unit={api_unit}", "--collect",
        f"--working-directory={worktree}",
        f"--setenv=BMS_HOME={worktree}", "--setenv=BMS_RUNTIME_MODE=dev",
        "--setenv=BMS_API_MODE=dev", f"--setenv=BMS_API_BIND_PORT={api_port}",
        f"--setenv=BMS_DATA={isolated}", f"--setenv=BMS_STATE_DIR={isolated}",
        f"--setenv=BMS_INPUTS={isolated / 'inputs'}", f"--setenv=BMS_DB_PATH={isolated / 'biomodstack.db'}",
        f"--setenv=BMS_WORK={isolated / 'work'}", f"--setenv=BMS_WEIGHTS={isolated / 'weights'}",
        f"--setenv=BMS_MSA_CACHE={isolated / 'msa_cache'}", f"--setenv=BMS_SABDAB_CACHE={isolated / 'sabdab_cache'}",
        "--setenv=BMS_CPU_POWER_STRICT=0", "--setenv=BMS_BIOXP_MUTATIONS_ENABLED=0",
        str(worktree / "scripts/run_biomodstack_api.sh"),
    ]
    run(api_command)
    wait_http(f"http://127.0.0.1:{api_port}/api/health")
    web_command = [
        "systemd-run", "--user", f"--unit={web_unit}", "--collect",
        f"--working-directory={frontend}",
        f"--setenv=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:{api_port}",
        "--setenv=BMS_BIOXP_MUTATIONS_ENABLED=0",
        "npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(web_port),
    ]
    run(web_command)
    wait_http(f"http://127.0.0.1:{web_port}/")
    receipt["status"] = "running"
    receipt["started_at"] = now()
    save_receipt(receipt)
    return receipt


def command_stop(args: argparse.Namespace) -> dict[str, object]:
    environment_id = validate_id(args.id)
    receipt = load_receipt(environment_id)
    api_unit, web_unit = unit_names(environment_id)
    systemctl("stop", api_unit, web_unit, check=False)
    systemctl("reset-failed", api_unit, web_unit, check=False)
    if receipt.get("status") != "closed":
        receipt["status"] = "stopped"
        receipt["stopped_at"] = now()
        save_receipt(receipt)
    return receipt


def command_promote(args: argparse.Namespace) -> dict[str, object]:
    environment_id = validate_id(args.id)
    repo = Path(args.repo).resolve()
    with lifecycle_lock():
        receipt = load_receipt(environment_id)
        worktree = Path(str(receipt["worktree"]))
        if git(worktree, "status", "--porcelain"):
            raise EnvironmentError("AI worktree is dirty; commit the completed spec before promotion")
        current_branch = git(worktree, "branch", "--show-current")
        if current_branch != receipt["branch"]:
            raise EnvironmentError("AI worktree is not on its recorded branch")
        assert_canonical_test(repo)
        git(worktree, "fetch", "origin", "test")
        remote = git(worktree, "rev-parse", "origin/test")
        tip = git(worktree, "rev-parse", "HEAD")
        ancestor = run(["git", "-C", str(worktree), "merge-base", "--is-ancestor", remote, tip], check=False)
        if ancestor.returncode != 0:
            raise EnvironmentError("origin/test moved or diverged; rebase the AI branch onto origin/test and rerun spec gates")
        git(worktree, "push", "origin", "HEAD:refs/heads/test")
        git(repo, "fetch", "origin", "test")
        git(repo, "merge", "--ff-only", "origin/test")
        deployed = False
        if not args.no_deploy:
            manager = repo / "scripts/manage_desktop_services.py"
            run([sys.executable, str(manager), "restart", "--runtime", "dev"], cwd=repo)
            api_health_url, frontend_url = installed_dev_urls()
            wait_http(api_health_url)
            wait_http(frontend_url)
            deployed = True
        receipt["status"] = "promoted"
        receipt["promoted_revision"] = tip
        receipt["promoted_at"] = now()
        receipt["dev_deployed"] = deployed
        save_receipt(receipt)
        return receipt


def command_close(args: argparse.Namespace) -> dict[str, object]:
    environment_id = validate_id(args.id)
    repo = Path(args.repo).resolve()
    with lifecycle_lock():
        receipt = load_receipt(environment_id)
        worktree = Path(str(receipt["worktree"]))
        if worktree.exists() and git(worktree, "status", "--porcelain"):
            raise EnvironmentError("AI worktree is dirty; commit/promote it or use --discard intentionally")
        git(repo, "fetch", "origin", "test")
        promoted = receipt.get("status") == "promoted"
        if worktree.exists() and promoted:
            tip = git(worktree, "rev-parse", "HEAD")
            promoted = run(["git", "-C", str(repo), "merge-base", "--is-ancestor", tip, "origin/test"], check=False).returncode == 0
        if not promoted and not args.discard:
            raise EnvironmentError("AI environment is not promoted to origin/test; promote it or close with --discard")
        api_unit, web_unit = unit_names(environment_id)
        systemctl("stop", api_unit, web_unit, check=False)
        systemctl("reset-failed", api_unit, web_unit, check=False)
        if worktree.exists():
            git(repo, "worktree", "remove", str(worktree))
        branch = str(receipt["branch"])
        if git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False) == "":
            probe = run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
            if probe.returncode == 0:
                git(repo, "branch", "-D" if args.discard else "-d", branch)
        receipt["status"] = "closed"
        receipt["closed_at"] = now()
        receipt["discarded"] = bool(args.discard and not promoted)
        save_receipt(receipt)
        return receipt


def command_status(args: argparse.Namespace) -> dict[str, object]:
    receipt = load_receipt(validate_id(args.id))
    api_unit, web_unit = unit_names(str(receipt["id"]))
    receipt["api_unit_active"] = systemctl("is-active", "--quiet", api_unit, check=False).returncode == 0
    receipt["web_unit_active"] = systemctl("is-active", "--quiet", web_unit, check=False).returncode == 0
    return receipt


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)
    for name in ("create", "start", "stop", "status", "promote", "close"):
        command = sub.add_parser(name)
        command.add_argument("--id", required=True)
        if name in {"create", "promote", "close"}:
            command.add_argument("--repo", required=True, help="canonical test worktree")
        if name == "promote":
            command.add_argument("--no-deploy", action="store_true", help="push test without restarting canonical dev")
        if name == "close":
            command.add_argument("--discard", action="store_true", help="destroy an unpromoted clean environment")
    return value


def main() -> int:
    args = parser().parse_args()
    commands = {
        "create": command_create,
        "start": command_start,
        "stop": command_stop,
        "status": command_status,
        "promote": command_promote,
        "close": command_close,
    }
    try:
        result = commands[args.command](args)
    except EnvironmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

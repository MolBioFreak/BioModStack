"""Explicit locked Development frontend bootstrap; launch/verify are offline.

Only node_modules is written in source. Node/npm are operator prerequisites;
this module never installs a system runtime or runs package lifecycle scripts.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from biomodstack_python_prerequisites import PrerequisiteError, save

PNPM_VERSION = "10.11.0"  # docker/web.Dockerfile authority; existing workspace lock format
SCHEMA = "bms.frontend-prerequisites.v1"
ACTIONS = {"frontend-plan", "frontend-bootstrap", "frontend-verify"}
NODE_REQUIREMENT = "^20.19.0 || >=22.12.0 (Node 22 recommended)"


def location(project_root: Path) -> Path:
    source = project_root.resolve()
    default = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    root = Path(os.environ.get("BMS_FRONTEND_ROOT", str(default / "biomodstack/frontend" / hashlib.sha256(str(source).encode()).hexdigest()[:16]))).expanduser()
    if not root.is_absolute():
        raise PrerequisiteError("external_root_invalid", "BMS_FRONTEND_ROOT/XDG_DATA_HOME must be absolute")
    root = root.resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise PrerequisiteError("external_root_invalid", "Frontend root must be separate from source")
    return root


def node_runtime(expected: str | None = None) -> str:
    node = os.environ.get("BMS_FRONTEND_NODE") or expected or shutil.which("node")
    if node and (not Path(node).is_absolute() or not os.access(node, os.X_OK)):
        raise PrerequisiteError("node_invalid", "Frontend Node must be an existing absolute executable")
    if node and expected and Path(node).resolve() != Path(expected):
        raise PrerequisiteError("node_identity_mismatch", "Selected Node differs from the bootstrapped frontend interpreter")
    if not node:
        raise PrerequisiteError("node_missing", "Install Node " + NODE_REQUIREMENT + " explicitly; bootstrap does not install Node")
    output = subprocess.check_output([node, "--version"], text=True, timeout=15).strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", output)
    version = tuple(map(int, match.groups())) if match else (0, 0, 0)
    if not ((20, 19, 0) <= version < (21, 0, 0) or version >= (22, 12, 0)):
        raise PrerequisiteError("node_incompatible", f"Found {output}; require {NODE_REQUIREMENT} for locked Vite/plugin-react")
    return str(Path(node).resolve())


def identity(project_root: Path) -> dict:
    source = project_root.resolve()
    paths = [source / name for name in ("pnpm-lock.yaml", "pnpm-workspace.yaml", "platform/frontend/package.json", "docker/web.Dockerfile")]
    paths += list(source.glob("packages/*/package.json")) + list(source.glob("platform/desktop-electron/package.json"))
    paths += [p for p in (source / "patches").rglob("*") if p.is_file()]
    paths += [source / name for name in ("package.json", ".npmrc", ".pnpmfile.cjs") if (source / name).exists()]
    return {"source_root": str(source), "manifests": {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}, "pnpm_version": PNPM_VERSION, "policy": "frontend-filter-frozen-ignore-scripts-v1"}


def validate_lock_compatibility(source: Path) -> None:
    """Require the reviewed workspace format and aligned build-tool authority.

    pnpm 10 consumes the existing SHA-256 patchedDependencies directly. Do not
    regenerate the dependency lock or translate patches during installation.
    """
    lock = (source / "pnpm-lock.yaml").read_text()
    if not re.search(r"^lockfileVersion: ['\"]?9\.0['\"]?\s*$", lock, re.M):
        raise PrerequisiteError("pnpm_lock_incompatible", "Pinned frontend setup requires the reviewed pnpm lock format 9.0")
    dockerfile = (source / "docker/web.Dockerfile").read_text()
    if f"pnpm@{PNPM_VERSION}" not in dockerfile:
        raise PrerequisiteError("pnpm_authority_mismatch", "Frontend setup and web build package-manager pins disagree")


def source_guard(source: Path, writable: bool = False) -> None:
    # Workspace links created *inside* node_modules are expected; the directory
    # itself must not redirect writes to a different checkout or system store.
    dirs = [source, source / "platform/frontend", *sorted((source / "packages").glob("*"))]
    for directory in dirs:
        if not directory.is_dir():
            continue
        modules = directory / "node_modules"
        if directory.is_symlink() or not directory.resolve().is_relative_to(source) or modules.is_symlink():
            raise PrerequisiteError("unsafe_source_path", f"Source/node_modules must not be redirected: {directory}")
        if writable:
            try:
                if not directory.stat().st_mode & 0o222:
                    raise PermissionError(str(directory))
                modules.mkdir(exist_ok=True)
                with tempfile.TemporaryFile(dir=modules):
                    pass
            except OSError as exc:
                raise PrerequisiteError("source_not_writable", f"Development frontend requires writable source node_modules: {modules}; use a writable source checkout (no source-copy workaround)") from exc


def environment(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.lower().startswith(("npm_config_", "pnpm_")) and k not in {"NODE_OPTIONS", "NODE_PATH"}}
    env.update(npm_config_cache=str(root / "cache/npm"), npm_config_userconfig=str(root / "npm-user.conf"), npm_config_globalconfig=str(root / "npm-global.conf"), ELECTRON_SKIP_BINARY_DOWNLOAD="1", CI="true")
    return env


def read_state(root: Path, expected: dict) -> dict | None:
    for name in ("npm-user.conf", "npm-global.conf"):
        if (root / name).exists() or (root / name).is_symlink():
            raise PrerequisiteError("unsafe_path", "Managed npm configuration must remain absent: " + name)
    for name in ("state.json", "operation.lock", "toolchain", "cache", "pinned-pnpm.log", "locked-install.log"):
        path = root / name
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
            raise PrerequisiteError("unsafe_path", f"Managed path is redirected: {path}")
    path = root / "state.json"
    if not path.exists():
        if root.exists() and any(p.name != "operation.lock" for p in root.iterdir()):
            raise PrerequisiteError("state_missing", "Nonempty frontend root lacks receipt; choose a new BMS_FRONTEND_ROOT")
        return None
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA or state.get("identity") != expected:
        raise PrerequisiteError("identity_mismatch", "Frontend source/manifests/policy changed; choose a new external BMS_FRONTEND_ROOT and explicitly bootstrap")
    return state


def check_environment(source: Path, root: Path) -> dict:
    recorded = json.loads((root / "state.json").read_text()).get("node")
    if not isinstance(recorded, str) or not Path(recorded).is_absolute():
        raise PrerequisiteError("node_identity_missing", "Frontend receipt lacks its absolute Node interpreter; run explicit frontend-bootstrap")
    node = node_runtime(recorded)
    source_guard(source)
    validate_lock_compatibility(source)
    pnpm = root / "toolchain/node_modules/pnpm/bin/pnpm.cjs"
    vite = source / "platform/frontend/node_modules/vite/bin/vite.js"
    if not pnpm.is_file() or not vite.is_file():
        raise PrerequisiteError("environment_missing", "Pinned pnpm/Vite missing; run frontend-bootstrap")
    if not pnpm.resolve().is_relative_to(root) or not vite.resolve().is_relative_to(source / "node_modules"):
        raise PrerequisiteError("unsafe_path", "Pinned pnpm/Vite escapes its owned dependency directory")
    env = environment(root)
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", os.defpath)
    for command, prefix in (([node, str(pnpm), "--version"], PNPM_VERSION), ([node, str(vite), "--version"], "vite/")):
        p = subprocess.run(command, cwd=source / "platform/frontend", env=env, capture_output=True, text=True, timeout=30)
        if p.returncode or (p.stdout.strip() != PNPM_VERSION if prefix == PNPM_VERSION else not p.stdout.strip().startswith(prefix)):
            raise PrerequisiteError("environment_invalid", f"Offline check failed: {p.stdout} {p.stderr}")
    # Prove optional native packages work with lifecycle scripts disabled.
    script = "const {createRequire}=require('module');const r=createRequire(process.argv[1]);r('esbuild').transformSync('let x=1');r('rollup');"
    p = subprocess.run([node, "-e", script, str(vite.resolve())], env=env, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise PrerequisiteError("native_dependency_missing", "Locked optional native dependencies unavailable (no lifecycle fallback): " + p.stderr[-2000:])
    modules_lock = source / "node_modules/.pnpm/lock.yaml"
    fingerprint = hashlib.sha256(modules_lock.read_bytes() + pnpm.read_bytes() + vite.read_bytes()).hexdigest()
    return {"node": node, "pnpm": str(pnpm), "vite": str(vite.resolve()), "root": str(root), "cwd": str(source / "platform/frontend"), "env": env, "inventory_sha256": fingerprint}


def resolve_frontend_environment(project_root: Path) -> dict | None:
    """Offline consumer API. None only if unmanaged; errors MUST NOT fall back.

    Execute [result['node'], result['vite'], ...] in result['cwd'] with
    result['env']. Never invoke npm/npx or install during launch.
    """
    try:
        root = location(project_root)
        if not root.exists():
            return None
        state = read_state(root, identity(project_root))
        if state is None:
            return None
        if state.get("status") != "complete":
            raise PrerequisiteError("bootstrap_incomplete", "Run explicit frontend-bootstrap to resume")
        result = check_environment(project_root.resolve(), root)
        if result["inventory_sha256"] != state.get("inventory_sha256"):
            raise PrerequisiteError("environment_changed", "Installed lock changed; run explicit frontend-bootstrap")
        return result
    except PrerequisiteError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PrerequisiteError("environment_unavailable", str(exc)) from exc


def prerequisite_report(action: str, *, project_root: Path) -> dict:
    report = dict(schema_version=SCHEMA, action=action, status="blocked", ready=False, scientifically_qualified=False, dependencies_installed=False, steps=[], errors=[], read_only=action != "frontend-bootstrap", node_requirement=NODE_REQUIREMENT)
    lock = None
    source_lock = None
    state = None
    try:
        if action not in ACTIONS:
            raise PrerequisiteError("action_invalid", "Unsupported frontend prerequisite action")
        source = project_root.resolve()
        root = location(source)
        expected = identity(source)
        report.update(root=str(root), identity=expected, resume_command="./start_ui.sh frontend-bootstrap --json")
        state = read_state(root, expected)
        report["operation_status"] = state.get("status") if state else "absent"
        node = node_runtime(state.get("node") if state and isinstance(state.get("node"), str) else None)
        npm = shutil.which("npm")
        if action != "frontend-verify" and not npm:
            raise PrerequisiteError("npm_missing", "Node npm CLI required for explicit pinned pnpm bootstrap; no global install is performed")
        source_guard(source)
        validate_lock_compatibility(source)
        if action == "frontend-plan":
            report.update(status="planned", steps=[{"step": "pinned-pnpm", "version": PNPM_VERSION, "network": True}, {"step": "locked-install", "filter": "frontend...", "flags": ["--frozen-lockfile", "--ignore-scripts"], "network": True}, {"step": "offline-check", "network": False}])
            return report
        if action == "frontend-verify":
            if not resolve_frontend_environment(source):
                raise PrerequisiteError("bootstrap_incomplete", "Run explicit frontend-bootstrap")
            report.update(status="verified", dependencies_installed=True)
            return report
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (root / "operation.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.close()
            lock = None
            raise PrerequisiteError("operation_busy", "Another frontend bootstrap owns this root") from exc
        state = read_state(root, expected)
        if state and state.get("status") == "complete":
            try:
                resolve_frontend_environment(source)
                report.update(status="already-installed", dependencies_installed=True)
                return report
            except PrerequisiteError:
                pass
        source_guard(source, writable=True)
        # Different external roots still share this source node_modules tree.
        lock_path = source / "node_modules/.bms-frontend-bootstrap.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        source_lock = os.fdopen(fd, "r+")
        lock_stat = os.fstat(source_lock.fileno())
        if lock_stat.st_nlink != 1 or not stat.S_ISREG(lock_stat.st_mode):
            raise PrerequisiteError("unsafe_source_path", "Frontend source operation lock must be a single-link regular file")
        try:
            fcntl.flock(source_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PrerequisiteError("source_operation_busy", "Another frontend bootstrap owns this source node_modules tree") from exc
        state = dict(schema_version=SCHEMA, identity=expected, status="running", steps=[], node=node)
        save(root, state)
        env = environment(root)
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", os.defpath)
        pnpm = root / "toolchain/node_modules/pnpm/bin/pnpm.cjs"
        commands = [
            ("pinned-pnpm", [npm, "install", "--prefix", str(root / "toolchain"), "--ignore-scripts", "--no-audit", "--no-fund", "--package-lock=false", "--registry=https://registry.npmjs.org", f"pnpm@{PNPM_VERSION}"], root),
            ("locked-install", [node, str(pnpm), "--filter", "frontend...", "install", "--frozen-lockfile", "--ignore-scripts", "--ignore-pnpmfile", "--store-dir", str(root / "cache/store"), "--config.manage-package-manager-versions=false"], source),
        ]
        for step, command, cwd in commands:
            row = dict(step=step, command=command, log=str(root / (step + ".log")), status="running")
            state["steps"].append(row)
            save(root, state)
            with open(row["log"], "w") as log:
                p = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            row.update(exit_code=p.returncode, status="complete" if not p.returncode else "failed")
            if p.returncode:
                raise PrerequisiteError(step + "_failed", f"Exit {p.returncode}; see {row['log']}; re-run frontend-bootstrap after correcting blocker")
        if identity(source) != expected:
            raise PrerequisiteError("source_changed", "Source manifest identity changed during bootstrap")
        resolved = check_environment(source, root)
        state.update(status="complete", inventory_sha256=resolved["inventory_sha256"])
        save(root, state)
        report.update(status="installed", dependencies_installed=True, steps=state["steps"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = {"code": getattr(exc, "code", "prerequisite_failed"), "message": str(exc)}
        report["errors"].append(error)
        if lock and state and error["code"] != "source_operation_busy":
            state.update(status="failed", error=error)
            save(root, state)
            report["steps"] = state.get("steps", [])
    finally:
        if source_lock:
            source_lock.close()
        if lock:
            lock.close()
    return report


def launch(project_root: Path, args: list[str]) -> None:
    resolved = resolve_frontend_environment(project_root)
    if resolved is None:
        # No auto installation, including in unmanaged legacy mode.
        node = node_runtime()
        source_guard(project_root.resolve())
        vite = project_root.resolve() / "platform/frontend/node_modules/vite/bin/vite.js"
        if not vite.is_file():
            raise PrerequisiteError("environment_missing", "Vite missing; run ./start_ui.sh frontend-bootstrap")
        resolved = dict(node=node, vite=str(vite), cwd=str(vite.parents[3]), env=environment(location(project_root)))
    os.chdir(resolved["cwd"])
    os.execve(resolved["node"], [resolved["node"], resolved["vite"], *args], resolved["env"])


if __name__ == "__main__":
    try:
        launch(Path(os.environ.get("BMS_HOME", str(Path(__file__).resolve().parent))), sys.argv[1:])
    except PrerequisiteError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        sys.exit(2)

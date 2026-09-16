"""Explicit, stdlib-only locked Python prerequisite installer (not readiness).

Owns only an external Python environment, never model/image stores or services.
The source uv.lock remains the dependency authority. Re-run python-bootstrap to
resume an interrupted same-identity operation; no automatic network on dispatch.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

UV_VERSION = "0.8.22"
SCHEMA = "bms.python-prerequisites.v1"
ACTIONS = {"python-plan", "python-bootstrap", "python-verify"}
SETUP_ACTIONS = {"discover", "plan", "configure-preview", "configure", "recover",
                 "resume", "provision-plan", "provision", "verify"}
MANIFESTS = ("pyproject.toml", "uv.lock", "README.md")


class PrerequisiteError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def location(project_root: Path) -> Path:
    source = project_root.resolve()
    default = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    value = os.environ.get("BMS_PYTHON_ROOT")
    root = Path(value).expanduser() if value else default / "biomodstack/python" / hashlib.sha256(str(source).encode()).hexdigest()[:16]
    if not root.is_absolute():
        raise PrerequisiteError("external_root_invalid", "BMS_PYTHON_ROOT/XDG_DATA_HOME must be absolute")
    root = root.resolve()
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise PrerequisiteError("external_root_invalid", "Python prerequisite root must be separate from the source checkout")
    return root


def identity(project_root: Path) -> dict:
    source = project_root.resolve()
    return {"source_root": str(source), "manifests": {
        name: hashlib.sha256((source / "platform/api" / name).read_bytes()).hexdigest()
        for name in MANIFESTS}, "uv_version": UV_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "policy": "frozen-no-dev-no-build-no-install-project-v1"}


def validate_owned_paths(root: Path) -> None:
    """Reject redirected installer files; never follow alien state/lock links."""
    for name in ("state.json", "operation.lock", "manifests", "toolchain", "cache", "environment",
                 "pinned-uv.log", "locked-sync.log"):
        if (root / name).is_file() and (root / name).stat().st_nlink > 1:
            raise PrerequisiteError("unsafe_path", f"Managed control file has multiple hard links: {root / name}")
        if (root / name).is_symlink():
            raise PrerequisiteError("unsafe_path", f"Managed path is a symlink: {root / name}")
    for name in MANIFESTS:
        if (root / "manifests" / name).is_symlink() or ((root / "manifests" / name).is_file() and (root / "manifests" / name).stat().st_nlink > 1):
            raise PrerequisiteError("unsafe_path", "Staged manifest must not be a symlink or hard link")
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_symlink():
                continue
            target = path.resolve()
            if target.is_relative_to(root):
                continue
            if path.parent == root / "environment/bin" and path.name in {"python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}"} and target.is_relative_to(Path(sys.base_prefix).resolve()):
                continue  # uv's base-interpreter links, not writable state.
            raise PrerequisiteError("unsafe_path", f"Managed link escapes the external environment: {path}")


def read_state(root: Path, expected: dict) -> dict | None:
    validate_owned_paths(root)
    path = root / "state.json"
    if not path.exists():
        if root.exists() and any(p.name != "operation.lock" for p in root.iterdir()):
            raise PrerequisiteError("state_missing", "Nonempty prerequisite root has no state; select a new external BMS_PYTHON_ROOT (nothing was deleted)")
        return None
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        raise PrerequisiteError("state_invalid", "Python prerequisite state must be a JSON object")
    if state.get("schema_version") != SCHEMA or state.get("identity") != expected:
        raise PrerequisiteError("identity_mismatch", "Source, manifests, Python or policy changed; select a new external BMS_PYTHON_ROOT. Existing environment is preserved, never downgraded or silently reused")
    return state


def save(root: Path, state: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".state-", dir=root)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / "state.json")
        fd = os.open(root, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def subprocess_env(root: Path) -> dict:
    # Neither inherited package indexes nor an activated developer venv may
    # redirect this operation. No interpreter download or source builds.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("UV_", "PIP_", "PYTHON")) and k != "VIRTUAL_ENV"}
    env.update(UV_PYTHON_DOWNLOADS="never", UV_NO_BUILD="1",
               UV_CACHE_DIR=str(root / "cache"),
               UV_PROJECT_ENVIRONMENT=str(root / "environment"),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def check_environment(root: Path) -> str:
    python = root / "environment/bin/python"
    uv = root / "toolchain/bin/uv"
    if not python.is_file() or not uv.is_file():
        raise PrerequisiteError("environment_missing", "Managed Python/uv missing; run python-bootstrap to repair")
    env = subprocess_env(root)
    commands = [[str(uv), "--version"],
                [str(python), "-I", "-B", "-c", "import yaml, fastapi, pydantic, jsonschema, rfc8785"],
                [str(uv), "pip", "check", "--python", str(python)],
                [str(python), "-I", "-B", "-c", "import importlib.metadata as m, json; print(json.dumps(sorted((d.metadata['Name'].lower(), d.version) for d in m.distributions())))"]]
    for index, command in enumerate(commands):
        p = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        if p.returncode or (index == 0 and p.stdout.strip() != f"uv {UV_VERSION}"):
            raise PrerequisiteError("environment_invalid", f"Offline environment check failed: {command!r}: {p.stdout[-2000:]} {p.stderr[-2000:]}")

    return hashlib.sha256(p.stdout.encode()).hexdigest()


def prerequisite_report(action: str, *, project_root: Path) -> dict:
    report = {"schema_version": SCHEMA, "action": action, "status": "blocked",
              "ready": False, "scientifically_qualified": False,
              "dependencies_installed": False, "steps": [], "errors": [],
              "read_only": action != "python-bootstrap"}
    state = None
    locked = None
    try:
        if action not in ACTIONS:
            raise PrerequisiteError("action_invalid", "Unsupported Python prerequisite action")
        root = location(project_root)
        expected = identity(project_root)
        report.update(root=str(root), identity=expected,
                      resume_command="./start_ui.sh python-bootstrap --json")
        state = read_state(root, expected)
        report["operation_status"] = state.get("status") if state else "absent"
        if action == "python-plan":
            report.update(status="planned", steps=[
                {"step": "pinned-uv", "version": UV_VERSION, "network": True},
                {"step": "locked-sync", "flags": ["--frozen", "--no-dev", "--no-build", "--no-install-project"], "network": True},
                {"step": "offline-check", "network": False}])
            return report
        if action == "python-verify":
            if not state or state.get("status") != "complete":
                raise PrerequisiteError("bootstrap_incomplete", "Run explicit python-bootstrap; verification never installs dependencies")
            resolve_python_environment(project_root)
            report.update(status="verified", dependencies_installed=True)
            return report
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        locked = (root / "operation.lock").open("a")
        try:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PrerequisiteError("operation_busy", "Another Python bootstrap owns this external environment")
        # Re-read under the lock, without mistaking our new lock for alien data.
        if (root / "state.json").exists():
            state = read_state(root, expected)
        if state and state.get("status") == "complete":
            try:
                resolve_python_environment(project_root)
                report.update(status="already-installed", dependencies_installed=True,
                              steps=[{"step": "offline-check", "status": "complete"}])
                return report
            except PrerequisiteError:
                pass  # Explicit bootstrap may repair same-identity dependencies.
        state = {"schema_version": SCHEMA, "identity": expected, "status": "running", "steps": []}
        save(root, state)
        staging = root / "manifests"
        staging.mkdir(exist_ok=True)
        for name in MANIFESTS:
            data = (project_root / "platform/api" / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected["manifests"][name]:
                raise PrerequisiteError("source_changed", "Source manifests changed during bootstrap")
            (staging / name).write_bytes(data)
        env = subprocess_env(root)
        def run(step: str, command: list[str], timeout: int) -> None:
            row = {"step": step, "status": "running", "command": command,
                   "log": str(root / (step + ".log"))}
            state["steps"].append(row)
            save(root, state)
            with open(row["log"], "w") as log:
                p = subprocess.run(command, cwd=staging, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=timeout)
            row.update(exit_code=p.returncode, status="complete" if p.returncode == 0 else "failed")
            save(root, state)
            if p.returncode:
                raise PrerequisiteError(step + "_failed", f"Exit {p.returncode}; see {row['log']}. Re-run python-bootstrap after correcting the blocker")
        # pip is an explicit base-Python prerequisite; never touch host/site packages.
        run("pinned-uv", [sys.executable, "-I", "-B", "-m", "pip", "--isolated", "install",
                          "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:",
                          "--no-deps", "--upgrade", "--index-url", "https://pypi.org/simple",
                          "--target", str(root / "toolchain"), f"uv=={UV_VERSION}"], 180)
        run("locked-sync", [str(root / "toolchain/bin/uv"), "sync", "--frozen",
                            "--no-dev", "--no-build", "--no-install-project",
                            "--python", sys.executable, "--no-python-downloads"], 900)
        fingerprint = check_environment(root)
        if identity(project_root) != expected:
            raise PrerequisiteError("source_changed", "Source identity changed during bootstrap")
        state.update(status="complete", inventory_sha256=fingerprint)
        state["steps"].append({"step": "offline-check", "status": "complete"})
        save(root, state)
        report.update(status="installed", dependencies_installed=True, steps=state["steps"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = {"code": getattr(exc, "code", "prerequisite_failed"), "message": str(exc)}
        report["errors"].append(error)
        if state and locked and getattr(exc, "code", "") != "operation_busy":
            state.update(status="failed", error=error)
            try:
                save(root, state)
                report["steps"] = state.get("steps", [])
            except OSError:
                pass
    finally:
        if locked:
            locked.close()
    return report


def resolve_python_environment(project_root: Path) -> dict | None:
    """Validated native consumer API; all invalid states raise PrerequisiteError."""
    try:
        return _resolve_python_environment(project_root)
    except PrerequisiteError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise PrerequisiteError("environment_unavailable", str(exc)) from exc


def _resolve_python_environment(project_root: Path) -> dict | None:
    """Offline authority for native consumers; None means preserve legacy mode.

    Returns absolute ``python``, ``uv``, ``root`` strings and a subprocess ``env``
    mapping. Raises PrerequisiteError on any managed-but-invalid installation;
    callers must not catch that error and fall back to an unmanaged environment.
    """
    root = location(project_root)
    # Legacy consumers may use synthetic configuration roots without manifests.
    if not root.exists():
        return None
    if not (root / "state.json").exists() and not any(root.iterdir()):
        return None
    state = read_state(root, identity(project_root))
    if state is None:
        return None
    if state.get("status") != "complete":
        raise PrerequisiteError("bootstrap_incomplete", "Python bootstrap interrupted/failed; re-run python-bootstrap to resume")
    fingerprint = check_environment(root)
    if state.get("inventory_sha256") != fingerprint:
        raise PrerequisiteError("environment_changed", "Installed distribution inventory changed; run explicit python-bootstrap to repair")
    env = subprocess_env(root)
    env["PATH"] = str(root / "environment/bin") + os.pathsep + str(root / "toolchain/bin") + os.pathsep + env.get("PATH", os.defpath)
    env["VIRTUAL_ENV"] = str(root / "environment")
    return {"python": str(root / "environment/bin/python"),
            "uv": str(root / "toolchain/bin/uv"), "root": str(root), "env": env}


def dispatch_setup(project_root: Path) -> None:
    """Re-exec through the offline-validated environment, never downgrade."""
    resolved = resolve_python_environment(project_root)
    if resolved is None:
        return
    if Path(sys.prefix).resolve() == (Path(resolved["root"]) / "environment").resolve():
        return
    python = resolved["python"]
    os.execve(python, [python, "-B", *sys.argv], resolved["env"])

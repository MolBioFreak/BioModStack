"""Explicit, stdlib-only locked Python prerequisite installer (not readiness).

Owns only an external Python environment, never model/image stores or services.
The source uv.lock remains the dependency authority. Re-run python-bootstrap to
resume an interrupted same-identity operation; no automatic network on dispatch.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

UV_VERSION = "0.13.10"
UV_RELEASE_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
UV_RELEASE_SHA256 = {
    "x86_64-unknown-linux-gnu": "bf65ac1a0af10f92c522d91ac096c7c0a39657647b155577e7836c2cbbb4526c",
    "aarch64-unknown-linux-gnu": "14590f644805a6234ffe071154340669e38137f38e60aa9629690119704107d2",
    "x86_64-unknown-linux-musl": "5597970380083a340b7983af05a84824b4db1c62b7059c796935b7fd6dc95fd2",
    "aarch64-unknown-linux-musl": "68174084fe40b6300361c5b2038066120dc62c92920c22a0f31f6fc74b066b57",
}
UV_ARCHIVE_MAX_BYTES = 80 * 1024 * 1024
SCHEMA = "bms.python-prerequisites.v1"
ACTIONS = {"python-plan", "python-bootstrap", "python-verify"}
SETUP_ACTIONS = {"discover", "plan", "configure-preview", "configure", "recover",
                 "resume", "provision-plan", "provision", "verify"}
MANIFESTS = ("pyproject.toml", "uv.lock", "README.md")
DOCOPT_SOURCE = {
    "name": "docopt", "version": "0.6.2",
    "url": "https://files.pythonhosted.org/packages/a2/55/8f8cab2afd404cf578136ef2cc5dfb50baa1761b68c9da1fb1e4eed343c9/docopt-0.6.2.tar.gz",
    "hash": "sha256:49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491",
}


def locked_build_policy(lock: Path) -> tuple[list[str], list[dict]]:
    """Allow only the reviewed source-only dependency; otherwise forbid builds.

    Read the canonical uv-generated package stanzas without adding a TOML
    dependency to the stdlib-only Python 3.10 bootstrap. Unknown formatting
    retains the stricter global no-build policy. uv still validates the full
    frozen lock and its distribution hashes before installation.
    """
    blocks = re.findall(r"(?ms)^\[\[package\]\]\n(.*?)(?=^\[\[package\]\]|\Z)", lock.read_text())
    packages = []
    for block in blocks:
        names = re.findall(r'^name = "([a-z0-9][a-z0-9._-]*)"$', block, re.M)
        if len(names) != 1:
            return ["--no-build"], []
        packages.append((names[0], block))
    candidates = [block for name, block in packages if name == "docopt"]
    if len(candidates) != 1:
        return ["--no-build"], []
    block = candidates[0]
    versions = re.findall(r'^version = "([^"]+)"$', block, re.M)
    sources = re.findall(r'^source = (.+)$', block, re.M)
    sdists = re.findall(r'^sdist = (.+)$', block, re.M)
    if (versions != [DOCOPT_SOURCE["version"]]
            or sources != ['{ registry = "https://pypi.org/simple" }']
            or len(sdists) != 1
            or re.findall(r'\burl = "([^"]+)"', sdists[0]) != [DOCOPT_SOURCE["url"]]
            or re.findall(r'\bhash = "([^"]+)"', sdists[0]) != [DOCOPT_SOURCE["hash"]]):
        return ["--no-build"], []
    # The first-party package is source shipped with this checkout, not a
    # registry source-build exception. Keep other distributions wheel-only.
    local_alignment = any(
        name == "bms-alignment-core"
        and re.findall(r'^source = (.+)$', stanza, re.M)
        == ['{ directory = "../../packages/bms-alignment-core" }']
        for name, stanza in packages
    )
    buildable = {"docopt"} | ({"bms-alignment-core"} if local_alignment else set())
    flags = [part for name in sorted({name for name, _ in packages} - buildable)
             for part in ("--no-build-package", name)]
    # A lone/empty synthetic lock must not turn into unrestricted source builds.
    if not flags:
        return ["--no-build"], []
    return flags, [dict(DOCOPT_SOURCE)]


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
    _, approved = locked_build_policy(source / "platform/api/uv.lock")
    return {"source_root": str(source), "manifests": {
        name: hashlib.sha256((source / "platform/api" / name).read_bytes()).hexdigest()
        for name in MANIFESTS}, "uv_version": UV_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "policy": ("frozen-no-dev-docopt-source-no-install-project-v2" if approved
                   else "frozen-no-dev-no-build-no-install-project-v1")}


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
    # redirect this operation. Source builds stay off unless locked-sync
    # explicitly applies the reviewed package policy.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("UV_", "PIP_", "PYTHON")) and k != "VIRTUAL_ENV"}
    env.update(UV_PYTHON_DOWNLOADS="never", UV_NO_BUILD="1",
               UV_CACHE_DIR=str(root / "cache"),
               UV_PROJECT_ENVIRONMENT=str(root / "environment"),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def uv_version_matches(output: str, version: str = UV_VERSION) -> bool:
    """Match both 'uv X.Y.Z' and standalone 'uv X.Y.Z (target/commit)' output."""
    lines = (output or "").strip().splitlines()
    if not lines:
        return False
    parts = lines[0].split()
    return len(parts) >= 2 and parts[0] == "uv" and parts[1] == version


def uv_release_asset(*, system: str | None = None, machine: str | None = None,
                     libc: str | None = None) -> dict[str, str]:
    """Resolve the pinned official standalone uv Linux release asset and SHA-256."""
    sys_name = system if system is not None else platform.system()
    if sys_name != "Linux":
        raise PrerequisiteError("uv_platform_unsupported", f"Pinned standalone uv bootstrap supports Linux only (found {sys_name!r})")
    raw_machine = (machine if machine is not None else platform.machine()).strip().lower()
    arch_map = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
    arch = arch_map.get(raw_machine)
    if not arch:
        raise PrerequisiteError("uv_platform_unsupported", f"Unsupported Linux architecture for pinned uv {UV_VERSION}: {raw_machine!r}")
    if libc is None:
        detected_libc = (platform.libc_ver()[0] or "").strip().lower()
        resolved_libc = "gnu" if detected_libc == "glibc" else "musl"
    else:
        resolved_libc = libc.strip().lower()
    target = f"{arch}-unknown-linux-{resolved_libc}"
    expected_sha = UV_RELEASE_SHA256.get(target)
    if not expected_sha:
        raise PrerequisiteError("uv_platform_unsupported", f"No pinned uv {UV_VERSION} checksum for target {target!r}")
    filename = f"uv-{target}.tar.gz"
    return {
        "version": UV_VERSION,
        "target": target,
        "filename": filename,
        "url": f"{UV_RELEASE_BASE}/{filename}",
        "sha256": expected_sha,
    }


def _download_uv_archive(url: str, *, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"BioModStack/{SCHEMA}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(UV_ARCHIVE_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PrerequisiteError("pinned-uv_failed", f"Failed to download pinned uv {UV_VERSION} from {url}: {exc}") from exc
    if len(data) > UV_ARCHIVE_MAX_BYTES:
        raise PrerequisiteError("pinned-uv_failed", f"Pinned uv archive exceeded {UV_ARCHIVE_MAX_BYTES} byte limit: {url}")
    return data


def _write_atomic_bytes(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def install_pinned_uv(toolchain_dir: Path, *, env: dict[str, str] | None = None,
                      log_path: Path | None = None, timeout: int = 180,
                      libc: str | None = None) -> dict:
    """Acquire or reuse a SHA-256-verified standalone uv binary without host pip."""
    bin_dir = toolchain_dir / "bin"
    uv_bin = bin_dir / "uv"
    receipt_path = bin_dir / ".uv-release.json"
    probe_env = env if env is not None else os.environ.copy()
    asset = uv_release_asset(libc=libc)

    def write_log(lines: list[str]) -> None:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if uv_bin.is_file() and not uv_bin.is_symlink() and receipt_path.is_file() and not receipt_path.is_symlink():
        try:
            recorded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recorded = None
        if (isinstance(recorded, dict)
                and recorded.get("version") == UV_VERSION
                and recorded.get("target") == asset["target"]
                and recorded.get("archive_sha256") == asset["sha256"]
                and isinstance(recorded.get("binary_sha256"), str)
                and hashlib.sha256(uv_bin.read_bytes()).hexdigest() == recorded["binary_sha256"]):
            try:
                probe = subprocess.run([str(uv_bin), "--version"], env=probe_env, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                probe = None
            probe_out = (probe.stdout or "").strip() if probe is not None else ""
            if probe is not None and probe.returncode == 0 and uv_version_matches(probe_out, UV_VERSION):
                write_log([
                    "method=standalone-tarball-sha256",
                    "reused=true",
                    f"uv={uv_bin}",
                    f"version={probe_out}",
                    f"target={asset['target']}",
                    f"archive_sha256={asset['sha256']}",
                    f"binary_sha256={recorded['binary_sha256']}",
                ])
                return {
                    "uv": str(uv_bin),
                    "version": UV_VERSION,
                    "target": asset["target"],
                    "url": recorded.get("url", asset["url"]),
                    "archive_sha256": asset["sha256"],
                    "binary_sha256": recorded["binary_sha256"],
                    "reused": True,
                    "stdout": probe_out,
                }

    log_lines = [
        "method=standalone-tarball-sha256",
        f"version={asset['version']}",
        f"target={asset['target']}",
        f"url={asset['url']}",
        f"expected_sha256={asset['sha256']}",
    ]
    try:
        archive_bytes = _download_uv_archive(asset["url"], timeout=timeout)
    except PrerequisiteError as exc:
        log_lines.append(f"error={exc}")
        write_log(log_lines)
        raise

    actual_sha = hashlib.sha256(archive_bytes).hexdigest()
    log_lines.extend([f"bytes={len(archive_bytes)}", f"actual_sha256={actual_sha}"])
    if actual_sha != asset["sha256"]:
        msg = f"Pinned uv {UV_VERSION} archive SHA-256 mismatch for {asset['filename']}: expected {asset['sha256']}, got {actual_sha}"
        log_lines.append(f"error={msg}")
        write_log(log_lines)
        raise PrerequisiteError("pinned-uv_failed", msg)

    extracted: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for member in archive.getmembers():
                member_parts = Path(member.name).parts
                if ".." in member_parts or any(part.startswith("/") for part in member_parts):
                    raise PrerequisiteError("pinned-uv_failed", f"Unsafe path in uv archive: {member.name!r}")
                if not member.isfile():
                    continue
                leaf = Path(member.name).name
                if leaf in {"uv", "uvx"}:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise PrerequisiteError("pinned-uv_failed", f"Unable to extract {member.name!r} from uv archive")
                    extracted[leaf] = stream.read()
    except PrerequisiteError as exc:
        log_lines.append(f"error={exc}")
        write_log(log_lines)
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        msg = f"Failed to unpack pinned uv {UV_VERSION} archive: {exc}"
        log_lines.append(f"error={msg}")
        write_log(log_lines)
        raise PrerequisiteError("pinned-uv_failed", msg) from exc

    if "uv" not in extracted:
        msg = f"Pinned uv {UV_VERSION} archive {asset['filename']} missing uv binary"
        log_lines.append(f"error={msg}")
        write_log(log_lines)
        raise PrerequisiteError("pinned-uv_failed", msg)

    for leaf, binary_bytes in extracted.items():
        _write_atomic_bytes(bin_dir / leaf, binary_bytes, mode=0o755)
    binary_sha = hashlib.sha256(extracted["uv"]).hexdigest()

    probe = subprocess.run([str(uv_bin), "--version"], env=probe_env, capture_output=True, text=True, timeout=30)
    probe_out = (probe.stdout or "").strip()
    probe_err = (probe.stderr or "").strip()
    log_lines.extend([
        f"binary_sha256={binary_sha}",
        f"probe_exit_code={probe.returncode}",
        f"probe_stdout={probe_out}",
    ])
    if probe_err:
        log_lines.append(f"probe_stderr={probe_err}")
    if probe.returncode or not uv_version_matches(probe_out, UV_VERSION):
        msg = f"Extracted uv binary failed version verification (expected uv {UV_VERSION}): {probe_out or probe_err}"
        log_lines.append(f"error={msg}")
        write_log(log_lines)
        raise PrerequisiteError("pinned-uv_failed", msg)

    receipt = {
        "version": UV_VERSION,
        "target": asset["target"],
        "filename": asset["filename"],
        "url": asset["url"],
        "archive_sha256": actual_sha,
        "binary_sha256": binary_sha,
    }
    _write_atomic_bytes(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"), mode=0o644)
    log_lines.append("reused=false")
    write_log(log_lines)
    return {
        "uv": str(uv_bin),
        "version": UV_VERSION,
        "target": asset["target"],
        "url": asset["url"],
        "archive_sha256": actual_sha,
        "binary_sha256": binary_sha,
        "reused": False,
        "stdout": probe_out,
    }


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
        if p.returncode or (index == 0 and not uv_version_matches(p.stdout, UV_VERSION)):
            raise PrerequisiteError("environment_invalid", f"Offline environment check failed: {command!r}: {p.stdout[-2000:]} {p.stderr[-2000:]}")

    return hashlib.sha256(p.stdout.encode()).hexdigest()


def prerequisite_report(action: str, *, project_root: Path) -> dict:
    report = {"schema_version": SCHEMA, "action": action, "status": "blocked",
              "ready": False, "scientifically_qualified": False,
              "dependencies_installed": False, "steps": [], "errors": [],
              "read_only": action != "python-bootstrap"}
    state = None
    locked = None
    def publish_support(root):
        try:
            publish_installed_support(root)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            # The installed interpreter remains usable if this optional storage
            # representation cannot be published. Do not add a bootstrap gate.
            print(f"BioModStack optional support publication unavailable: {exc}", file=sys.stderr)
    try:
        if action not in ACTIONS:
            raise PrerequisiteError("action_invalid", "Unsupported Python prerequisite action")
        root = location(project_root)
        expected = identity(project_root)
        build_flags, approved_builds = locked_build_policy(project_root / "platform/api/uv.lock")
        report.update(root=str(root), identity=expected,
                      approved_source_builds=approved_builds,
                      resume_command="./start_ui.sh python-bootstrap --json")
        state = read_state(root, expected)
        report["operation_status"] = state.get("status") if state else "absent"
        if action == "python-plan":
            uv_asset = uv_release_asset()
            report.update(status="planned", steps=[
                {"step": "pinned-uv", "version": UV_VERSION, "method": "standalone-tarball-sha256",
                 "target": uv_asset["target"], "url": uv_asset["url"], "sha256": uv_asset["sha256"], "network": True},
                {"step": "locked-sync", "flags": ["--frozen", "--no-dev", *build_flags, "--no-install-project"], "network": True},
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
                receipt = refresh_installed_support(root, expected, refresh=False)
                if receipt is not None and receipt["payload_id"] != (state.get("support_payload") or {}).get("payload_id"):
                    state["support_payload"] = receipt
                    save(root, state)
                report.update(status="already-installed", dependencies_installed=True,
                              steps=[{"step": "offline-check", "status": "complete"}])
                locked.close()
                locked = None
                publish_support(root)
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
        def run(step: str, command: list[str], timeout: int, *, approved_source: bool = False) -> None:
            row = {"step": step, "status": "running", "command": command,
                   "log": str(root / (step + ".log"))}
            state["steps"].append(row)
            save(root, state)
            with open(row["log"], "w") as log:
                command_env = {**env, "UV_NO_BUILD": "false"} if approved_source else env
                # Local path dependencies are relative to the API checkout;
                # staging only the lock manifests loses their package tree.
                cwd = project_root / "platform/api" if step == "locked-sync" else staging
                p = subprocess.run(command, cwd=cwd, env=command_env, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=timeout)
            row.update(exit_code=p.returncode, status="complete" if p.returncode == 0 else "failed")
            save(root, state)
            if p.returncode:
                raise PrerequisiteError(step + "_failed", f"Exit {p.returncode}; see {row['log']}. Re-run python-bootstrap after correcting the blocker")
        # Acquire pinned standalone uv directly with SHA-256 verification; never invoke host pip.
        uv_asset = uv_release_asset()
        uv_row = {
            "step": "pinned-uv",
            "status": "running",
            "method": "standalone-tarball-sha256",
            "version": UV_VERSION,
            "target": uv_asset["target"],
            "url": uv_asset["url"],
            "sha256": uv_asset["sha256"],
            "log": str(root / "pinned-uv.log"),
        }
        state["steps"].append(uv_row)
        save(root, state)
        try:
            uv_info = install_pinned_uv(
                root / "toolchain", env=env, log_path=Path(uv_row["log"]), timeout=180
            )
        except Exception as exc:
            uv_row.update(status="failed", error=str(exc))
            save(root, state)
            if isinstance(exc, PrerequisiteError):
                raise
            raise PrerequisiteError(
                "pinned-uv_failed",
                f"Pinned uv {UV_VERSION} bootstrap failed: {exc}; see {uv_row['log']}. Re-run python-bootstrap after correcting the blocker",
            ) from exc
        uv_row.update(
            status="complete",
            reused=uv_info["reused"],
            binary_sha256=uv_info["binary_sha256"],
        )
        save(root, state)
        run("locked-sync", [str(root / "toolchain/bin/uv"), "sync", "--frozen",
                            "--no-dev", *build_flags, "--no-install-project",
                            "--python", sys.executable, "--no-python-downloads"], 900,
            approved_source=bool(approved_builds))
        fingerprint = check_environment(root)
        if identity(project_root) != expected:
            raise PrerequisiteError("source_changed", "Source identity changed during bootstrap")
        state.update(status="complete", inventory_sha256=fingerprint)
        receipt = refresh_installed_support(root, expected)
        if receipt is not None:
            state["support_payload"] = receipt
        state["steps"].append({"step": "offline-check", "status": "complete"})
        save(root, state)
        locked.close()
        locked = None
        publish_support(root)
        report.update(status="installed", dependencies_installed=True, steps=state["steps"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = {"code": getattr(exc, "code", "prerequisite_failed"), "message": str(exc)}
        report["errors"].append(error)
        report["status"] = "blocked"
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
    support_payload = state.get("support_payload")
    if not isinstance(support_payload, dict):
        support_payload = None
    python = root / "environment/bin/python"
    if isinstance(support_payload, dict) and support_payload.get("python"):
        selected = Path(support_payload["python"])
        if selected.is_file():
            python = selected
        else:
            support_payload = None  # optional representation, not a new refusal
    env["PATH"] = str(python.parent) + os.pathsep + str(root / "toolchain/bin") + os.pathsep + env.get("PATH", os.defpath)
    env["VIRTUAL_ENV"] = str(python.parent.parent)
    if isinstance(support_payload, dict) and support_payload.get("payload_root"):
        env["BMS_API_PYTHON"] = str(python)
        env["BMS_SUPPORT_PAYLOAD_RECEIPT"] = str(Path(support_payload["payload_root"]) / "support-payload.json")
    return {"python": str(python), "source_python": str(root / "environment/bin/python"),
            "uv": str(root / "toolchain/bin/uv"), "root": str(root), "env": env,
            "support_payload": support_payload}


def publish_installed_support(root: Path) -> dict | None:
    """One-time managed adoption/publication, independent of optional CM images."""
    runtime = (root / "environment/bin/python").resolve().parent.parent
    if runtime in {Path(p) for p in ("/", "/usr", "/usr/local", "/opt", "/home", "/bin", "/lib", "/lib64")} or not (runtime / "lib").is_dir():
        return None  # Preserve supported host-system-Python setup.
    from scripts.cm_api_support_runtime import publish_installed_payload
    with (root / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((root / "state.json").read_text())
        receipt = state.get("support_payload")
        receipt = receipt if isinstance(receipt, dict) else {}
        if receipt.get("payload_root") and Path(receipt["payload_root"], "support-payload.json").is_file():
            return receipt
        refresh_installed_support(root, state["identity"], refresh=False)
    return publish_installed_payload(root / "environment", runtime)


def refresh_installed_support(root: Path, expected: dict, *, refresh=True) -> dict | None:
    """Explicit install/refresh boundary; system-Python setup remains supported."""
    runtime = (root / "environment/bin/python").resolve().parent.parent
    if runtime in {Path(p) for p in ("/", "/usr", "/usr/local", "/opt", "/home", "/bin", "/lib", "/lib64")} or not (runtime / "lib").is_dir():
        return None
    return support_payload_receipt(root / "environment", runtime,
        selection={"groups": [], "extras": [], "install_project": False,
                   "policy": expected["policy"]}, installer_version=UV_VERSION, refresh=refresh)


def support_payload_receipt(venv: Path, runtime: Path, *, selection: dict,
                            installer_version: str, refresh: bool = False,
                            state_root: Path | None = None) -> dict:
    """Inventory at the install/sync boundary, never at each Job or restart.

    Called under the support provision lock. Mutable input is copied, not used
    as a hardlink donor. Its optional receipt is observation, not admission.
    Explicit bootstrap/uv mutations refresh it; arbitrary developer edits are
    deliberately not continuously attested.
    """
    receipt_path = venv / ".bms-support-payload.json"
    executable = (venv / "bin/python").resolve()
    stat = executable.stat()
    interpreter_link = venv / "bin/python"
    observation = {"selection": selection, "installer_version": installer_version,
                   "interpreter_reference": os.readlink(interpreter_link) if interpreter_link.is_symlink() else None,
                   "interpreter": [str(executable), stat.st_dev, stat.st_ino,
                                   stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]}
    previous = None
    try:
        previous = json.loads(receipt_path.read_text())
    except (OSError, ValueError):
        pass
    payload_id = previous.get("payload_id") if isinstance(previous, dict) else None
    if (not refresh and isinstance(previous, dict) and previous.get("observation") == observation
            and isinstance(payload_id, str) and len(payload_id) == 64
            and all(char in "0123456789abcdef" for char in payload_id)
            and isinstance(previous.get("members"), list)
            and isinstance(previous.get("interpreter"), dict)):
        return previous
    probe = subprocess.run([str(venv / "bin/python"), "-I", "-B", "-c",
        "import json,platform,sys,sysconfig; print(json.dumps(dict("
        "implementation=platform.python_implementation(),version=sys.version,"
        "build=platform.python_build(),platform=sys.platform,machine=platform.machine(),"
        "soabi=sysconfig.get_config_var('SOABI'))))"], check=True,
        capture_output=True, text=True)
    interpreter = json.loads(probe.stdout)
    members = support_members(venv, runtime, normalize=True)
    member_sha = hashlib.sha256(json.dumps(members, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    identity_data = {"selection": selection, "installer_version": installer_version,
                     "interpreter": interpreter, "member_manifest_sha256": member_sha}
    receipt = {**identity_data, "payload_id": hashlib.sha256(json.dumps(
        identity_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "members": members, "observation": observation}
    _write_support_json(receipt_path, receipt)
    if state_root is not None:
        state = json.loads((state_root / "state.json").read_text())
        state["support_payload"] = receipt
        save(state_root, state)
    return receipt


def _write_support_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".support-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def support_regenerable_bytecode(path: Path) -> bool:
    if path.suffix != ".pyc":
        return False
    if path.parent.name == "__pycache__":
        return (path.parent.parent / (path.name.split(".")[0] + ".py")).is_file()
    return path.with_suffix(".py").is_file()


def support_members(venv: Path, runtime: Path, *, normalize: bool) -> list[dict]:
    """Regular bodies include standalone stdlib and native interpreter libraries.

    Only installer-owned relocation fields are normalized. Final output bytes
    have their own member inventory at publication for existing transports.
    """
    members = []
    roots = (("python-runtime", runtime), ("venv", venv))
    for prefix, root in roots:
        for path in sorted(root.rglob("*")):
            if path == venv / ".bms-support-payload.json" or support_regenerable_bytecode(path):
                continue
            relative = path.relative_to(root).as_posix()
            row: dict = {"path": prefix + "/" + relative}
            if path.is_symlink():
                target = os.readlink(path)
                if normalize:
                    reference = os.path.normpath(str(path.parent / target))
                    if not any(reference == str(donor) or reference.startswith(str(donor) + "/")
                               for _, donor in roots):
                        # uv's minor-version prefix can alias its concrete build.
                        try:
                            reference = str(Path(reference).resolve())
                        except (OSError, RuntimeError):
                            pass  # Unrelated legacy links keep their old spelling.
                    for label, donor in roots:
                        if reference == str(donor) or reference.startswith(str(donor) + "/"):
                            target = "${" + label + "}" + reference[len(str(donor)):]
                            break
                row.update(kind="symlink", target=target, mode=0o777, size=0)
            elif path.is_dir():
                row.update(kind="directory", mode=0o555, size=0)
            elif path.is_file():
                digest = hashlib.sha256()
                size = 0
                if normalize and (relative == "pyvenv.cfg" or relative.startswith("bin/")):
                    data = support_relocated_bytes(relative, path.read_bytes(), venv, runtime,
                                                   "${venv}", "${python-runtime}")
                    digest.update(data)
                    size = len(data)
                else:
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            size += len(chunk)
                row.update(kind="file", size=size, sha256=digest.hexdigest(),
                           mode=0o555 if path.stat().st_mode & 0o111 else 0o444)
            else:
                continue
            members.append(row)
    return members


def support_relocated_bytes(relative: str, data: bytes, venv: Path, runtime: Path,
                            target_venv: str, target_runtime: str) -> bytes:
    if relative == "pyvenv.cfg":
        lines = data.decode().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith(("home =", "executable =")):
                key, _, value = line.partition("=")
                try:
                    resolved = Path(value.strip()).resolve()
                    for donor, target in ((runtime, target_runtime), (venv, target_venv)):
                        if resolved.is_relative_to(donor):
                            suffix = resolved.relative_to(donor).as_posix()
                            ending = line[len(line.rstrip("\r\n")):]
                            line = f"{key}= {target}/{suffix}{ending}"
                            break
                except (OSError, RuntimeError):
                    pass
            if line.startswith(("home =", "executable =", "command =")):
                line = line.replace(str(runtime), target_runtime).replace(str(venv), target_venv)
            lines[index] = line
        return "".join(lines).encode()
    if relative in {"bin/activate", "bin/activate.csh", "bin/activate.fish", "bin/activate.nu",
                    "bin/activate.ps1", "bin/Activate.ps1", "bin/activate_this.py"}:
        return data.replace(str(venv).encode(), target_venv.encode())
    if relative.startswith("bin/") and data.startswith(b"#!"):
        # uv entrypoint shebangs (including the /bin/sh space-path trampoline).
        lines = data.splitlines(keepends=True)
        for index in range(min(3, len(lines))):
            lines[index] = lines[index].replace(str(venv).encode(), target_venv.encode()).replace(
                str(runtime).encode(), target_runtime.encode())
        return b"".join(lines)
    return data


def dispatch_setup(project_root: Path) -> None:
    """Re-exec through the offline-validated environment, never downgrade."""
    # Select the retained interpreter before comparing its ABI-sensitive receipt.
    # The re-executed process still performs all source, policy and inventory
    # validation below; a changed shell Python must not invalidate that receipt.
    root = location(project_root)
    managed = root / "environment/bin/python"
    # This is interpreter selection only; the receipt is validated after exec.
    state_path = root / "state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        support = state.get("support_payload") if isinstance(state, dict) else None
        if isinstance(support, dict) and support.get("python") and Path(support["python"]).is_file():
            managed = Path(support["python"])
    if managed.is_file() and Path(sys.prefix).resolve() != managed.parent.parent.resolve():
        os.execve(str(managed), [str(managed), "-B", *sys.argv], subprocess_env(root))
        return
    resolved = resolve_python_environment(project_root)
    if resolved is None:
        return
    python = resolved["python"]
    if Path(sys.prefix).resolve() == Path(python).parent.parent.resolve():
        os.environ.clear()
        os.environ.update(resolved["env"])
        return
    os.execve(python, [python, "-B", *sys.argv], resolved["env"])

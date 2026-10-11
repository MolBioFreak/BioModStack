"""CM support image selection and installed-payload publication metadata.

Image lookup remains read-only, not scientific admission. Payload helpers run
under the existing provision/install locks; there is no separate runtime store
or service. Portable Python publication does not require a scientific image.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def support_image(project_root: Path) -> Path | None:
    from biomodstack_runtime_profile import resolve_runtime_paths
    from scripts.lib.runtime_image_lifecycle import object_path
    from select_workflow_adapter_image import select_probe_image
    from scripts.lib.shared_runtime_images import verify_image
    container_dir = Path(str(resolve_runtime_paths(project_root=project_root)["container_dir"]))
    root = Path(os.environ.get("BMS_RUNTIME_IMAGE_STORE", "").strip() or container_dir / ".image-store")
    path = select_probe_image(containers=container_dir)
    if path != container_dir / "protenix.sif":
        return path  # shared selector already verified retained identity/bytes

    # Preserve installed legacy support-host behavior, without registering or
    # approving either image. Frustra-only installations do not need Protenix.
    frustra = os.environ.get("BMS_FRUSTRAMPNN_SIF", "").strip()
    if frustra:
        path = Path(frustra)
        # Reuse the immutable object constraints. This is only a support import
        # probe; the Frustra registry still authenticates its pinned digest,
        # executable and checkpoint at scientific admission/execution.
        digest = path.parent.name
        if path != object_path(root, digest):
            raise ValueError("configured Frustra image is not a canonical shared object")
        verify_image(path, digest)
        return path
    for name in ("protenix.sif", "frustrampnn.sif"):
        path = container_dir / name
        if path.exists() or path.is_symlink():
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"legacy support image must be a regular file: {path}")
            return path
    return None


def portable_runtime(source_python: Path) -> Path:
    """Reject system interpreter prefixes before any recursive copy occurs."""
    runtime = source_python.resolve(strict=True).parent.parent
    if runtime in {Path(p) for p in ("/", "/usr", "/usr/local", "/opt", "/home", "/bin", "/lib", "/lib64")}:
        raise ValueError("CM support runtime requires a standalone managed Python, not a system prefix")
    if not (runtime / "bin").is_dir() or not list((runtime / "lib").glob("python3.*/os.py")):
        raise ValueError("CM support runtime requires a self-contained Python distribution")
    return runtime


def selected_payload(venv: Path, runtime: Path, project: Path, sync_log: Path | None) -> dict:
    """Use the actual managed or legacy sync selection, not a source-release key."""
    import json
    import re
    import subprocess
    from biomodstack_python_prerequisites import support_payload_receipt, UV_VERSION
    state_root = venv.parent if venv.name == "environment" and (venv.parent / "state.json").is_file() else None
    if state_root:
        selection = {"groups": [], "extras": [], "install_project": False,
                     "policy": "frozen-no-dev-no-build-no-install-project-v1"}
        version = UV_VERSION
        refresh = False
    else:
        # The launcher host can be Python 3.10; parse with the selected runtime,
        # not an undeclared host tomli dependency. A legacy parser-less Python
        # retains observed selection text rather than becoming a new refusal.
        selection_probe = subprocess.check_output([str(venv / "bin/python"), "-I", "-B", "-c", r"""
import json, pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None
if tomllib is not None:
    print(json.dumps(tomllib.loads(text)))
else:
    sections = re.findall(r'(?ms)^\[(?:tool\.uv|dependency-groups|build-system)\].*?(?=^\[|\Z)', text)
    print(json.dumps({'observed_selection': sections}))
""", str(project / "platform/api/pyproject.toml")], text=True)
        manifest = json.loads(selection_probe)
        uv_config = manifest.get("tool", {}).get("uv", {})
        groups = uv_config.get("default-groups", ["dev"] if "dev" in manifest.get("dependency-groups", {}) else [])
        if groups == "all":
            groups = sorted(manifest.get("dependency-groups", {}))
        selection = {"groups": sorted(groups), "extras": [],
                     "install_project": uv_config.get("package", "build-system" in manifest),
                     "policy": "legacy-uv-sync-locked-observed"}
        def enabled(name):
            return os.environ.get(name, "").lower() in {"1", "true", "yes"}
        if enabled("UV_NO_DEFAULT_GROUPS"):
            selection["groups"] = []
        excluded_groups = os.environ.get("UV_NO_GROUP", "").replace(",", " ").split()
        if enabled("UV_NO_DEV"):
            excluded_groups.append("dev")
        selection["groups"] = [group for group in selection["groups"] if group not in excluded_groups]
        if enabled("UV_NO_INSTALL_PROJECT") or enabled("UV_NO_INSTALL_WORKSPACE"):
            selection["install_project"] = False
        # Preserve additional observed uv selection controls even on older uv
        # versions. They are provenance of this sync, not invented defaults.
        selection["uv_selection_environment"] = {key: os.environ[key] for key in (
            "UV_NO_GROUP", "UV_NO_INSTALL_LOCAL", "UV_NO_EDITABLE",
            "UV_NO_CONFIG") if key in os.environ}
        if "observed_selection" in manifest:
            selection = {"policy": "legacy-uv-sync-locked-observed",
                         "groups": "unknown", "extras": [], "install_project": "unknown",
                         "observed_selection": manifest["observed_selection"]}
        version = subprocess.check_output(["uv", "--version"], text=True).strip()
        # uv's normal stderr reports actual install/remove operations. No invented
        # report flag; an unchanged sync says Resolved/Audited only.
        report = re.sub(r"\x1b\[[0-9;]*m", "", sync_log.read_text()) if sync_log else ""
        refresh = bool(re.search(r"(?m)^(?:Installed|Uninstalled|Reinstalled) \d+ package", report))
    if state_root:
        import fcntl
        with (state_root / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return support_payload_receipt(venv, runtime, selection=selection,
                installer_version=version, refresh=refresh, state_root=state_root)
    return support_payload_receipt(venv, runtime, selection=selection,
                                   installer_version=version, refresh=refresh)


def record_publication(venv: Path, destination: Path) -> None:
    """Expose the concrete receipt through the existing completed-install state."""
    import fcntl
    import json
    from biomodstack_python_prerequisites import save
    root = venv.parent
    if venv.name != "environment" or not (root / "state.json").is_file():
        return
    with (root / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((root / "state.json").read_text())
        receipt = json.loads((destination / "support-payload.json").read_text())
        if state.get("support_payload") != receipt:
            state["support_payload"] = receipt
            save(root, state)


def prepare_payload(stage: Path, destination: Path, venv: Path, runtime: Path) -> None:
    """Relocate only private copied files, then record final transport bytes."""
    import json
    from biomodstack_python_prerequisites import (
        support_members, support_relocated_bytes, support_regenerable_bytecode,
        _write_support_json,
    )
    receipt = json.loads((venv / ".bms-support-payload.json").read_text())
    for label, donor in (("venv", venv), ("python-runtime", runtime)):
        root = stage / label
        for path in sorted(root.rglob("*")):
            if path == stage / "venv/.bms-support-payload.json" or support_regenerable_bytecode(path):
                path.unlink()
            elif path.is_symlink():
                target = os.readlink(path)
                reference = os.path.normpath(str(donor / path.relative_to(root).parent / target))
                for part, source in (("venv", venv), ("python-runtime", runtime)):
                    if reference == str(source) or reference.startswith(str(source) + "/"):
                        target = str(destination / part) + reference[len(str(source)):]
                        break
                # Internal absolute links become relative to the final layout.
                if target.startswith(str(destination) + "/"):
                    target = os.path.relpath(target, destination / label / path.relative_to(root).parent)
                path.unlink()
                path.symlink_to(target)
            elif path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative == "pyvenv.cfg" or relative.startswith("bin/"):
                    data = path.read_bytes()
                    relocated = support_relocated_bytes(relative, data, venv, runtime,
                        str(destination / "venv"), str(destination / "python-runtime"))
                    if relocated != data:
                        path.write_bytes(relocated)
    receipt.update(payload_root=str(destination), python=str(destination / "venv/bin/python"),
                   members=support_members(stage / "venv", stage / "python-runtime", normalize=False))
    # member_manifest_sha256 identifies normalized source bodies. The final
    # manifest separately identifies relocated native transport bytes.
    import hashlib
    receipt["published_member_manifest_sha256"] = hashlib.sha256(json.dumps(
        receipt["members"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write_support_json(stage / "support-payload.json", receipt)
    for path in sorted(stage.rglob("*"), reverse=True):
        if not path.is_symlink():
            path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    stage.chmod(0o555)


def publish_installed_payload(venv: Path, runtime: Path) -> dict:
    """Image-independent publication by the existing bootstrap/launch owner.

    Caller releases operation.lock before entering this provision lock. State
    publication happens under operation.lock inside the provision lock, matching
    the adapter's lock ordering and preventing concurrent installer mutation.
    Mutable installation bodies are copied only on a cold content publication.
    """
    import fcntl
    import json
    import shutil
    import tempfile
    root = Path(os.environ.get("BMS_CM_API_RUNTIME_DIR") or
                Path(os.environ.get("BMS_DATA", "/mnt/BioModStack")) / "runtime/cm-api-python")
    from contextlib import ExitStack
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        lock = locks.enter_context((root / ".provision.lock").open("a"))
        fcntl.flock(lock, fcntl.LOCK_EX)
        managed = venv.name == "environment" and (venv.parent / "state.json").is_file()
        if managed:
            operation = locks.enter_context((venv.parent / "operation.lock").open("a"))
            fcntl.flock(operation, fcntl.LOCK_EX)
        for abandoned in root.glob(".stage.*"):
            if abandoned.is_dir() and not abandoned.is_symlink():
                _remove_unpublished(abandoned)
        receipt = json.loads((venv / ".bms-support-payload.json").read_text())
        destination = root / "releases" / ("runtime-" + receipt["payload_id"])
        destination.parent.mkdir(exist_ok=True)
        if not (destination / "support-payload.json").is_file():
            stage = Path(tempfile.mkdtemp(prefix=".stage.", dir=root))
            try:
                shutil.copytree(runtime, stage / "python-runtime", symlinks=True)
                shutil.copytree(venv, stage / "venv", symlinks=True)
                prepare_payload(stage, destination, venv, runtime)
                if destination.is_dir():
                    # Only incomplete publications are recoverable, never a
                    # completed generation or its frozen legacy current alias.
                    _remove_unpublished(destination)
                stage.chmod(0o755)  # rename across parents updates the directory's '..'
                os.replace(stage, destination)
                destination.chmod(0o555)
            finally:
                if stage.exists():
                    _remove_unpublished(stage)
        current = root / "current"
        if not current.exists() and not current.is_symlink():
            current.symlink_to(destination.relative_to(root))
        published = json.loads((destination / "support-payload.json").read_text())
        if managed:
            from biomodstack_python_prerequisites import save
            state = json.loads((venv.parent / "state.json").read_text())
            if state.get("support_payload") != published:
                state["support_payload"] = published
                save(venv.parent, state)
    return published


def _remove_unpublished(path: Path) -> None:
    import shutil
    for member in path.rglob("*"):
        if not member.is_symlink() and member.is_dir():
            member.chmod(0o700)
    path.chmod(0o700)
    shutil.rmtree(path)


if __name__ == "__main__":
    try:
        if sys.argv[1] == "image":
            selected = support_image(Path(sys.argv[2]))
            print(selected or "")
        elif sys.argv[1] == "python-runtime":
            print(portable_runtime(Path(sys.argv[2])))
        elif sys.argv[1] == "receipt":
            print(selected_payload(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]),
                                   Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None)["payload_id"])
        elif sys.argv[1] == "published":
            record_publication(Path(sys.argv[2]), Path(sys.argv[3]))
        elif sys.argv[1] == "prepare":
            prepare_payload(*(Path(value) for value in sys.argv[2:6]))
        else:
            raise ValueError("unknown support-runtime operation")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        label = "workflow adapter Protenix probe image is unavailable" if sys.argv[1] == "image" else "CM support runtime is blocked"
        print(f"{label}: {exc}", file=sys.stderr)
        sys.exit(78)

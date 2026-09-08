"""Read-only bootstrap observations, not installation or scientific admission.

Uses the runtime profile, local budget and reviewed model dependency authorities.
No probes execute external programs: executable presence is not qualification.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from biomodstack_local_resources import configured_local_policy, detect_local_capacity
from biomodstack_runtime_profile import (
    get_install_profile_path, normalize_install_profile, resolve_runtime_paths,
    validate_install_profile_raw, managed_runtime_storage_paths,
)

SCHEMA_VERSION = "bms.bootstrap.v1"
BLOCKED_EXIT = 3


def _disk(path: Path) -> dict:
    """Observe nearest existing directory without creating a proposed root."""
    ancestor = path
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise ValueError(f"Storage ancestor is not a directory: {ancestor}")
    usage = shutil.disk_usage(ancestor)
    return {"path": str(path), "observed_at": str(ancestor),
            "device": ancestor.stat().st_dev, "free_bytes": usage.free,
            "total_bytes": usage.total,
            "writable_hint": os.access(ancestor, os.W_OK | os.X_OK),
            "required_peak_bytes": None, "sufficient": None}


def bootstrap_report(action: str, *, project_root: Path, runtime: str | None = None,
                     models: tuple[str, ...] = ()) -> dict:
    if action not in {"discover", "plan"}:
        raise ValueError("Unsupported bootstrap action")
    mode = runtime or os.environ.get("BMS_RUNTIME_MODE", "container")
    report = {"schema_version": SCHEMA_VERSION, "action": action,
              "status": "blocked", "ready": False, "read_only": True,
              "runtime_mode": mode, "selected_models": sorted(set(models)),
              "observations": {}, "dependencies": [], "blockers": [],
              "effects_scope": "bootstrap application operations; excludes interpreter startup and user site hooks",
              "interpreter_startup": {"bytecode_disabled": bool(sys.flags.dont_write_bytecode)},
              "effects": {"writes": False, "downloads": False,
                          "service_changes": False, "registration": False}}
    blockers = report["blockers"]
    def block(code, message):
        blockers.append({"code": code, "message": message})
    if mode not in {"dev", "container"}:
        block("runtime_invalid", "BMS_RUNTIME_MODE must be dev or container")
    observations = report["observations"]
    observations["host"] = {"system": platform.system(), "machine": platform.machine()}
    if platform.system() != "Linux":
        block("host_unsupported", "Managed bootstrap currently targets Linux")
    profile = {}
    profile_valid = True
    try:
        from biomodstack_configuration import assert_configuration_readable
        assert_configuration_readable()
        profile_path = get_install_profile_path()
        observations["profile"] = {"path": str(profile_path), "exists": profile_path.exists()}
        if profile_path.exists():
            raw = json.loads(profile_path.read_text(encoding="utf-8"))
            validate_install_profile_raw(raw)
            profile = normalize_install_profile(raw)

    except (OSError, ValueError, TypeError, RuntimeError, OverflowError) as exc:
        profile_valid = False
        profile = {}
        block("profile_invalid", str(exc))
    try:
        observations["capacity"] = asdict(detect_local_capacity())
        observations["local_budget"] = asdict(configured_local_policy(profile))
    except (ValueError, OverflowError) as exc:
        block("local_resources_invalid", str(exc))
    paths = {}
    if profile_valid:
        try:
            paths = resolve_runtime_paths(project_root=project_root, profile=profile)
            storage = managed_runtime_storage_paths(paths, mode)
            observations["storage"] = []
            for key, destination in storage.items():
                path = Path(destination)
                try:
                    disk = _disk(path.parent if key.endswith("db_path") else path)
                    disk["path"] = str(path)
                    disk["kind"] = "file" if key.endswith("db_path") else "directory"
                    observations["storage"].append({"role": key, **disk})
                    if disk["free_bytes"] == 0:
                        block("disk_full", f"No free space for {key}")
                    if not disk["writable_hint"]:
                        block("storage_not_writable", f"No write/search access hint for {key}")
                except (OSError, ValueError, RuntimeError) as exc:
                    block("storage_unavailable", f"{key}: {exc}")
                if path.is_relative_to(project_root.resolve()):
                    block("storage_in_source", f"{key} resolves inside checkout; configure external storage before installation")
        except (OSError, ValueError, TypeError, RuntimeError, OverflowError) as exc:
            block("profile_resolution_failed", str(exc))
    tools = {"python3", "git", "systemctl"}
    tools.update({"uv", "node", "npm"} if mode == "dev" else {"docker"})
    if models:
        tools.add("apptainer")
    observations["tools"] = [{"name": name, "path": shutil.which(name),
                               "qualified": False} for name in sorted(tools)]
    for tool in observations["tools"]:
        if not tool["path"]:
            block("tool_missing", f"Required executable not on PATH: {tool['name']}")
    observations["gpu"] = {"probe_tool": shutil.which("nvidia-smi"),
                            "compatibility": "not_checked"}
    observations["privileges"] = {"euid": os.geteuid(), "service_access": "not_checked"}
    observations["tailnet"] = {"requested": False, "state": "not_checked"}
    if models:
        try:
            api_root = str(project_root / "platform" / "api")
            if api_root not in sys.path:
                sys.path.insert(0, api_root)
            from model_registry import model_runtime_dependencies
            for model in sorted(set(models)):
                try:
                    for ref in model_runtime_dependencies(model):
                        root = paths.get("container_dir" if ref.kind == "image" else "weights_root")
                        report["dependencies"].append({
                            "model_id": model, "kind": ref.kind, "relative_path": ref.relative_path,
                            "path": str(Path(str(root)) / ref.relative_path) if root else None,
                            "acquisition": "unavailable", "qualification": "not_checked"})
                except ValueError as exc:
                    block("dependency_closure_unavailable", f"{model}: {exc}")
        except (ImportError, OSError, ValueError) as exc:
            block("dependency_authority_unavailable", str(exc))
        block("scientific_qualification_not_run", "Existing scientific validators/admission remain authoritative; no runtime was qualified or registered")
        observations["weight_licensing"] = {
            "applicability": "unknown", "acceptance": "not_checked",
            "reason": "Dependency references do not establish license requirements; no reviewed license applicability authority is wired into bootstrap"}
        block("license_applicability_unknown", "Selected-model license applicability has not been established; this does not assert that every selection needs licensed weights")
    observations["provisioning"] = {"plan_command": "provision-plan", "execute_command": "provision",
                                   "resume_command": "resume --expect-plan-sha256 DIGEST",
                                   "qualification": "not_checked"}
    block("acquisition_unavailable", "Discover/plan do not acquire bytes. Use provision-plan --model MODEL for registry-specific metadata blockers and provision for approved pinned bytes; existing files are not qualification evidence")
    block("disk_requirement_unknown", "Authoritative acquisition/staging/expansion sizes are unavailable; free space is not a sufficient-disk verdict")
    block("prerequisite_qualification_not_run", "Tool versions, GPU compatibility, service privileges and locked dependencies have not been qualified")
    block("installation_readiness_not_verified", "Discover/plan are read-only; configure/recover do not acquire, qualify, register or verify installation readiness")
    if action == "plan":
        report["plan"] = {"executable": False, "steps": [
            {"action": step, "state": "blocked"} for step in
            ("configure", "acquire_pinned_runtime", "resolve_applicable_weight_licenses",
             "verify_register", "verify_readiness")], "restart_impact": "none: no execution"}
    return report


def render_report(report: dict) -> str:
    lines = [f"BioModStack bootstrap {report['action']}: {report['status']} ({SCHEMA_VERSION})",
             "Read-only observations; installation ready: no"]
    for tool in report["observations"].get("tools", []):
        lines.append(f"Tool {tool['name']}: {tool['path'] or 'missing'} (not qualified)")
    for storage in report["observations"].get("storage", []):
        lines.append(f"Storage {storage['role']}: {storage['path']} — {storage['free_bytes']} bytes free; required peak unknown")
    lines.extend(f"BLOCKED [{b['code']}]: {b['message']}" for b in report["blockers"])
    return "\n".join(lines)

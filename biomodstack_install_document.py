"""Strict clean-install input boundary; runtime profile remains the authority.

This is NOT the on-disk legacy install_profile.json format or an apply API.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import biomodstack_runtime_profile as profiles
from biomodstack_local_resources import configured_local_policy

SCHEMA_VERSION = "bms.install.v1"
# Deliberately bounded v1 surface. Other legacy settings require a future schema.
PATH_FIELDS = tuple(k for k in profiles._PATH_FIELDS if not k.endswith("project_root"))
PORT_FIELDS = profiles._INT_FIELDS
FEATURE_FIELDS = tuple(profiles._FEATURE_DEFAULTS)
PROFILE_FIELDS = set(PATH_FIELDS + PORT_FIELDS + (
    "features", "core_runtime_mode", "local_cpu_threads", "local_memory_gib"))


def _object(value: object, keys: set[str], name: str) -> dict:
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    if set(value) - keys:
        raise ValueError(f"{name} contains unsupported fields: {sorted(set(value) - keys, key=str)}")
    return value


def _path(value: object, name: str, *, file: bool = False) -> Path:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty absolute path string")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} contains control characters")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be absolute without parent traversal")
    # Missing roots are allowed, but dangling symlinks and non-directory
    # ancestors are not. No mkdir or access/qualification claim is made.
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() and not ancestor.exists():
            raise ValueError(f"{name} contains a dangling symlink")
        if ancestor.exists():
            if ancestor == path and file:
                if not ancestor.is_file():
                    raise ValueError(f"{name} must be a regular file path")
            elif not ancestor.is_dir():
                raise ValueError(f"{name} has a non-directory component")
    return path.resolve()


def validate_install_document(raw: object) -> dict:
    """Validate raw values BEFORE calling the permissive legacy normalizer."""
    doc = _object(raw, {"schema_version", "profile", "ingress"}, "document")
    if set(doc) != {"schema_version", "profile", "ingress"}:
        raise ValueError("schema_version, profile and ingress are required")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    profile = _object(doc["profile"], PROFILE_FIELDS, "profile")
    ingress = _object(doc["ingress"], {"mode", "target"}, "ingress")
    if ingress.get("mode") == "local-only":
        if set(ingress) != {"mode"}:
            raise ValueError("local-only ingress forbids target")
    elif ingress.get("mode") == "tailnet":
        if type(ingress.get("target")) is not str or ingress["target"] not in ("development", "production"):
            raise ValueError("tailnet ingress requires target development or production")
    else:
        raise ValueError("ingress.mode must be local-only or tailnet")
    for key, value in profile.items():
        if key in PATH_FIELDS:
            _path(value, key, file=key == "db_path")
        elif key in PORT_FIELDS or key == "local_cpu_threads":
            if type(value) is not int:
                raise ValueError(f"{key} must be an integer (not boolean)")
        elif key == "local_memory_gib":
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("local_memory_gib must be a finite positive number")
        elif key == "core_runtime_mode":
            if type(value) is not bool:
                raise ValueError("core_runtime_mode must be boolean")
        elif key == "features":
            features = _object(value, set(FEATURE_FIELDS), "features")
            if any(type(v) is not bool for v in features.values()):
                raise ValueError("feature values must be boolean")
    # Reject legacy aliases such as 5173 before normalization can migrate them.
    profiles.validate_runtime_port_contract(profile)
    configured_local_policy(profile)
    return {"schema_version": SCHEMA_VERSION, "profile": dict(profile), "ingress": dict(ingress)}


def load_install_document(path: Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    if not path.is_file():
        raise ValueError("install document must be an existing regular file")
    return validate_install_document(json.loads(path.read_text(encoding="utf-8"),
                                                object_pairs_hook=pairs, parse_constant=constant))


def configuration_preview(raw: object, *, project_root: Path) -> dict:
    doc = validate_install_document(raw)
    root = project_root.resolve()
    # Explicit HOME is required: HOME='' must not resolve relative to checkout.
    home = _path(os.environ.get("HOME"), "HOME")
    state = _path(os.environ.get("XDG_STATE_HOME") or str(home / ".local/state"), "XDG_STATE_HOME")
    _path(os.environ.get("XDG_CONFIG_HOME") or str(home / ".config"), "XDG_CONFIG_HOME")
    candidate = {"data_root": str(state / "biomodstack"),
                 "dev_data_root": str(state / "biomodstack-dev"), **doc["profile"]}
    normalized = profiles.normalize_install_profile(candidate)
    resolved = profiles.resolve_runtime_paths(project_root=root, profile=normalized, environ={})
    profiles.validate_runtime_port_contract(resolved)
    policy = configured_local_policy(normalized)
    resolved.update(local_cpu_threads=policy.cpu_threads, local_memory_bytes=policy.memory_bytes)
    destinations = {"profile": str(profiles.get_install_profile_path()),
                    "core_runtime_env": str(profiles.get_core_runtime_env_path()),
                    "compat_env": str(profiles.get_compat_env_path())}
    # Include derived Development state and export destinations, not just input keys.
    storage = {k: v for k, v in resolved.items() if k in PATH_FIELDS or
               (k.startswith("dev_") and isinstance(v, str))}
    for key, value in {**storage, **destinations}.items():
        path = _path(value, key, file=key.endswith("db_path") or key in destinations)
        if path.is_relative_to(root) or root.is_relative_to(path):
            raise ValueError(f"{key} overlaps source checkout")
    prod, dev = Path(str(resolved["data_root"])), Path(str(resolved["dev_data_root"]))
    if prod.is_relative_to(dev) or dev.is_relative_to(prod):
        raise ValueError("Production and Development data roots must not overlap")
    existing = profiles.get_install_profile_path()
    return {"schema_version": "bms.configuration-preview.v1", "action": "configure-preview",
            "status": "preview", "valid": True, "ready": False, "read_only": True,
            "apply_available": False, "profile": normalized, "resolved": resolved,
            "ingress": {**doc["ingress"], "applied": False, "qualified": False},
            "destinations": destinations,
            "compatibility": {"existing_profile_present": existing.exists() or existing.is_symlink(),
                              "policy": "candidate_only_no_merge_no_migration",
                              "legacy_profile_api": "unchanged"},
            "environment_policy": "HOME/XDG locations only; runtime overrides ignored",
            "effects": {"writes": False, "downloads": False, "service_changes": False, "registration": False},
            "blockers": [{"code": "transactional_apply_unavailable",
                          "message": "No configure execution: profile and exports lack a crash-consistent generation transaction"},
                         {"code": "installation_readiness_not_verified",
                          "message": "Preview is not provisioning, ingress enforcement, scientific qualification or readiness"}]}


def preview_report(path: Path, *, project_root: Path) -> dict:
    try:
        return configuration_preview(load_install_document(path), project_root=project_root)
    except (ValueError, TypeError, OSError, RuntimeError, OverflowError) as exc:
        return {"schema_version": "bms.configuration-preview.v1", "action": "configure-preview",
                "status": "invalid", "valid": False, "ready": False, "read_only": True,
                "apply_available": False, "blockers": [{"code": "install_document_invalid", "message": str(exc)}]}


def render_preview(report: dict) -> str:
    return "BioModStack configuration preview (read-only; installation ready: no)\n" + json.dumps(report, indent=2, sort_keys=True)

"""Shared, non-submitting MSA provider setup and admission checks.

Provider credentials and cache roots are deployment-owned, never job settings.
Configured is not authenticated or scientifically accepted.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

PROVIDERS = ("colabfold_api", "neurosnap_api")
NEUROSNAP_FIELDS = frozenset({
    "msa_neurosnap_coverage_percent", "msa_neurosnap_identity_percent",
    "msa_neurosnap_max_sequences", "msa_neurosnap_force_uppercase",
    "msa_neurosnap_pad_sequences",
})


def selected_provider(params: dict) -> str:
    from biomodstack_msa_policy import resolve_search_backend
    value = params.get("msa_provider")
    backend = params.get("protenix_msa_backend")
    if value is None and backend not in (None, "auto", "none", "esm"):
        value = backend
    provider = resolve_search_backend(value)
    if backend not in (None, "auto", "none", "esm") and resolve_search_backend(backend) != provider:
        raise ValueError("MSA provider and model backend selections disagree")
    return provider


def provider_settings(params: dict) -> dict:
    """Only provider scientific controls; local-search tuning is inapplicable.

System paths, model inference controls and secrets must not enter provider cache
identity or HTTP fields. Each provider validates its own closed settings schema.
"""
    aliases = {f"colabfold_{key}": key for key in (
        "use_env", "use_filter", "use_templates", "pairing_mode", "pairing_strategy")}
    result = {aliases.get(key, key): value for key, value in params.items()
              if key in NEUROSNAP_FIELDS or key.startswith("colabfold_")}
    # Nextflow argv parsing yields strings; restore the same typed identity as
    # browser/API JSON, rejecting unknown representations rather than truthiness.
    for key in ("use_env", "use_filter", "use_templates",
                "msa_neurosnap_force_uppercase", "msa_neurosnap_pad_sequences"):
        if key in result and isinstance(result[key], str):
            if result[key].lower() not in ("true", "false"):
                raise ValueError(f"Invalid boolean MSA setting: {key}")
            result[key] = result[key].lower() == "true"
    for key in ("msa_neurosnap_coverage_percent", "msa_neurosnap_identity_percent",
                "msa_neurosnap_max_sequences"):
        if key in result and isinstance(result[key], str):
            value = float(result[key])
            result[key] = int(value) if value.is_integer() else value
    # Other-provider defaults may coexist in a typed model request. They are
    # explicitly inapplicable, not sent as this provider's scientific controls.
    provider = selected_provider(params)
    return {key: value for key, value in result.items()
            if (key.startswith("msa_neurosnap_") if provider == "neurosnap_api"
                else not key.startswith("msa_neurosnap_"))}


def cache_root() -> Path:
    from paths import get_msa_cache_dir
    root = Path(get_msa_cache_dir())
    if not root.is_absolute():
        raise ValueError("Configured MSA cache root must be absolute")
    return root / "provider_api"


def credential_file() -> Path | None:
    value = os.environ.get("BMS_NEUROSNAP_API_KEY_FILE")
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("BMS_NEUROSNAP_API_KEY_FILE must be an absolute protected credential path")
    return path


def _credential_configured() -> bool:
    path = credential_file()
    if path is None:
        return False
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_size > 0
            and not info.st_mode & 0o077 and os.access(path, os.R_OK))


def provider_readiness() -> dict:
    """Inspect configuration without reading keys or making provider requests."""
    blockers: list[str] = []
    try:
        root = cache_root()
        if any(path.is_symlink() for path in (root, *root.parents)):
            blockers.append("MSA cache must not traverse symlinks")
        if root.exists():
            info = root.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                blockers.append("MSA API cache must be service-owned and not group/world writable")
        parent = root
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            blockers.append("MSA cache parent is not writable by the service")
    except (OSError, ValueError):
        blockers.append("MSA cache configuration is invalid")
    result = {}
    for provider in PROVIDERS:
        errors = list(blockers)
        configured = None
        if provider == "neurosnap_api":
            try:
                configured = _credential_configured()
            except ValueError:
                configured = False
            if not configured:
                errors.append("Configure BMS_NEUROSNAP_API_KEY_FILE as a readable private nonempty credential file")
        result[provider] = {
            "configured": not errors,
            "credential_configured": configured,
            "authentication": "not_checked" if provider == "neurosnap_api" else "not_required",
            "live_acceptance": "not_run",
            "blockers": errors,
        }
    return {"default_provider": "colabfold_api", "local_search_enabled": False,
            "submission_location": "controller", "cache_identity": "provider-query-effective-settings",
            "providers": result}


def preflight_msa_provider(model_id: str, params: dict) -> None:
    """Fail missing provider setup before queue insertion, never submit science."""
    from biomodstack_msa_policy import requires_msa_search
    if not requires_msa_search(model_id, params):
        return
    from biomodstack_msa_api import validate_settings
    provider = selected_provider(params)
    validate_settings(provider, provider_settings(params))
    readiness = provider_readiness()["providers"][provider]
    if not readiness["configured"]:
        raise ValueError("; ".join(readiness["blockers"]))

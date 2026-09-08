"""Versioned MSA provider selection and scientific validation, without IO.

The shared JSON inventory supplies browser controls and model-registry parity.
Requested values remain separate from this copied, default-expanded effective
request. Artifact verification and consumer capability checks remain mandatory.
"""
from typing import Any, Mapping

import json
from pathlib import Path

POLICY = json.loads((Path(__file__).resolve().parent / "schemas" / "msa_search_policy.json").read_text())
LOCAL_DISABLED = POLICY["local_disabled"]
DISCLOSURE = POLICY["disclosure"]

def reject_local_search() -> None:
    raise ValueError(LOCAL_DISABLED)


def resolve_search_backend(value: Any) -> str:
    backend = str(value or "auto").strip().lower()
    if backend == "local":
        reject_local_search()
    if backend == "auto":
        return "colabfold_api"
    if backend in POLICY["enabled_search_backends"]:
        return backend
    raise ValueError(f"Unsupported MSA search backend {value!r}. {LOCAL_DISABLED}")


def requires_msa_search(model_id: str, params: Mapping[str, Any]) -> bool:
    """Admission scope only; never proves an arbitrary path is a verified MSA."""
    flag = {"boltz2": "boltz_use_msa", "protenix": "protenix_use_msa"}.get(model_id)
    if flag and params.get(flag) in (False, "false", "0", 0):
        return False
    if model_id == "protenix" and params.get("protenix_msa_backend") in {"none", "esm"}:
        return False
    # Cache-only is an explicit fail-on-miss operation, not permission to search.
    if params.get("msa_cache_only") in (True, "true", "1", 1):
        return False
    return params.get("msa_provider", params.get("protenix_msa_backend")) in {"auto", *POLICY["enabled_search_backends"]}


def apply_msa_policy(model_id: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy/compile selections; never rewrite an explicit local saved request.

    Do not interpret file paths as proof of alignment identity, change pairing,
    or invent no-MSA support. Existing model validators own those contracts.
    """
    effective = dict(params or {})
    if effective.get("msa_allow_empty_fallback") in (True, "true", "1", 1):
        raise ValueError("MSA failure cannot silently disable MSA. Disable msa_allow_empty_fallback and re-preview; choose an explicitly model-supported no-MSA mode instead.")
    for key in ("msa_provider", "protenix_msa_backend"):
        if key in effective:
            if key == "protenix_msa_backend" and effective[key] in {"none", "esm"}:
                # Existing global model schema owns these non-search modes.
                continue
            effective[key] = resolve_search_backend(effective[key])
    if model_id == "msa_batch":
        # This launcher still runs local batch search. It is NOT an API adapter.
        reject_local_search()
    if model_id in {"boltz2", "rf3", "protenix", "boltz_cp_experimental"}:
        effective.setdefault("msa_provider", "colabfold_api")
    if model_id == "protenix":
        if "msa_provider" not in (params or {}) and effective.get("protenix_msa_backend") in POLICY["enabled_search_backends"]:
            effective["msa_provider"] = effective["protenix_msa_backend"]
        effective.setdefault("protenix_msa_backend", effective["msa_provider"])
        if effective["protenix_msa_backend"] in POLICY["enabled_search_backends"] and effective["msa_provider"] != effective["protenix_msa_backend"]:
            raise ValueError("Conflicting msa_provider and protenix_msa_backend selections; explicitly select the same provider and re-preview.")
    for key in effective:
        if key.startswith("colabfold_") and not key.startswith("colabfold_api_") and key not in POLICY["colabfold_settings"]:
            raise ValueError(f"Unknown ColabFold MSA setting: {key}")
        if key.startswith("msa_neurosnap_") and key not in POLICY["neurosnap_settings"]:
            raise ValueError(f"Unknown Neurosnap MSA setting: {key}")
    if "msa_use_env" in effective and effective.get("msa_provider") == "colabfold_api":
        if "colabfold_use_env" in effective and effective["colabfold_use_env"] != effective["msa_use_env"]:
            raise ValueError("Conflicting msa_use_env and colabfold_use_env values")
        effective.setdefault("colabfold_use_env", effective["msa_use_env"])
    scientific_fields = {**POLICY["neurosnap_settings"], **POLICY["colabfold_settings"]}
    for key, field in scientific_fields.items():
        if effective.get("msa_provider") == field["provider"]:
            effective.setdefault(key, field["default"])
        if key not in effective:
            continue
        value = effective[key]
        if field["type"] == "boolean":
            valid = type(value) is bool
        elif field["type"] == "string":
            valid = type(value) is str and value in field["enum"]
        else:
            valid = type(value) in (int, float) and field["minimum"] <= value <= field["maximum"]
            if field["type"] == "integer":
                valid = valid and type(value) is int
        if not valid:
            raise ValueError(f"Invalid {key}: expected {field['type']} with documented bounds; values are never coerced.")
    if model_id == "protenix" and effective.get("msa_provider") == "neurosnap_api" and requires_msa_search(model_id, effective):
        for key in ("msa_neurosnap_force_uppercase", "msa_neurosnap_pad_sequences"):
            if effective[key]:
                raise ValueError(f"{key}=true is unsupported for Protenix A3M consumption; saved intent is not reset.")
    return effective

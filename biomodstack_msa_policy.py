"""BMS 1.0 interim MSA selection policy (no provider or network operations).

Reuse the existing global msa_provider/protenix_msa_backend keys. This is
system-owned admission policy, not a second scientific parameter schema.
Artifact verification and model-specific no-MSA validation remain authoritative.
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
    if backend in {"auto", "colabfold_api"}:
        return "colabfold_api"
    raise ValueError(f"Unsupported MSA search backend {value!r}. {LOCAL_DISABLED}")


def requires_msa_search(model_id: str, params: Mapping[str, Any]) -> bool:
    """Admission scope only; never proves an arbitrary path is a verified MSA."""
    flag = {"boltz2": "boltz_use_msa", "protenix": "protenix_use_msa"}.get(model_id)
    if flag and params.get(flag) in (False, "false", "0", 0):
        return False
    # Cache-only is an explicit fail-on-miss operation, not permission to search.
    if params.get("msa_cache_only") in (True, "true", "1", 1):
        return False
    return params.get("msa_provider") == "colabfold_api"


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
        effective.setdefault("protenix_msa_backend", "colabfold_api")
    return effective

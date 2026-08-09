"""Canonical, typed request handling for the RFD3 local-redesign workflow.

The module is shared by the API and the workflow preparation scripts.  It keeps
product metadata beside the exact native RFD3 input specification and makes the
request digest deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


REQUEST_SCHEMA = "bms.rfd3.local-redesign.request.v1"
RESULT_SCHEMA = "bms.rfd3.local-redesign.result.v1"
SUPPORTED_MODES = frozenset({"partial_diffusion", "minimal_insertion", "packing_shell"})
SUPPORTED_PROFILES = frozenset({"generic_local_redesign_v1", "drt4_datp_gate_v1"})
_FORBIDDEN_FREE_FORM_KEYS = frozenset(
    {
        "rfd3_extra_config",
        "rfd3_yaml",
        "rfd3_raw_config",
        "custom_rfd3_config",
    }
)


class ContractError(ValueError):
    """Raised when a local-redesign request cannot be normalized safely."""


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used for request hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_sha256(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()


def load_profile_registry() -> dict[str, Any]:
    configured = os.environ.get("BMS_RFD3_LOCAL_REDESIGN_PROFILE_REGISTRY")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().parents[2] / "config" / "rfd3" / "local_redesign_profiles.json",
    ]
    registry_path = next((path for path in candidates if path is not None and path.is_file()), None)
    if registry_path is None:
        raise ContractError("RFD3 local-redesign profile registry is unavailable")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("RFD3 local-redesign profile registry is malformed") from exc
    if (
        not isinstance(registry, dict)
        or registry.get("schema") != "bms.rfd3.local-redesign.profile-registry.v1"
        or registry.get("schema_version") != 1
        or not isinstance(registry.get("profiles"), dict)
    ):
        raise ContractError("RFD3 local-redesign profile registry schema is invalid")
    return registry


def profile_registry_sha256(registry: Mapping[str, Any] | None = None) -> str:
    payload = registry if registry is not None else load_profile_registry()
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def get_profile(profile_id: str, registry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    loaded = registry if registry is not None else load_profile_registry()
    profiles = loaded.get("profiles") if isinstance(loaded, Mapping) else None
    profile = profiles.get(profile_id) if isinstance(profiles, Mapping) else None
    if not isinstance(profile, Mapping):
        raise ContractError(f"unsupported local-redesign profile '{profile_id}'")
    return dict(profile)

def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ContractError(f"{field} is required")
        return None
    result = str(value).strip()
    if not result:
        if required:
            raise ContractError(f"{field} is required")
        return None
    return result


def _positive_int(value: Any, field: str, *, default: int | None = None) -> int:
    if value is None:
        if default is not None:
            return default
        raise ContractError(f"{field} is required")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field} must be an integer") from exc
    if result < 1:
        raise ContractError(f"{field} must be greater than zero")
    return result


def _nonnegative_float(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None:
        if default is not None:
            return default
        raise ContractError(f"{field} is required")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field} must be numeric") from exc
    if result < 0:
        raise ContractError(f"{field} must be non-negative")
    return result


def _bool(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ContractError(f"{field} must be boolean")


def _string_list(value: Any, field: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[,\n]+", value) if item.strip()]
    elif isinstance(value, (list, tuple)):
        items = []
        for item in value:
            item_text = _text(item, field, required=True)
            if item_text:
                items.append(item_text)
    else:
        raise ContractError(f"{field} must be a string or list of strings")
    return items


def _normalize_residue_selector(value: Any, field: str) -> str | None:
    values = _string_list(value, field)
    return ",".join(values) if values else None


def _normalize_atom_value(value: Any, field: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        tokens = [token.strip().upper() for token in re.split(r"[,\s]+", value) if token.strip()]
    elif isinstance(value, (list, tuple)):
        tokens = []
        for token in value:
            token_text = _text(token, field, required=True)
            if token_text:
                tokens.append(token_text.upper())
    else:
        raise ContractError(f"{field} values must be [], a string, or a list of atom names")
    allowed_special = {"BKBN", "ALL", "TIP"}
    if any(token in allowed_special for token in tokens) and len(tokens) > 1:
        raise ContractError(f"{field} cannot combine BKBN, ALL, or TIP with atom names")
    return tokens


def _normalize_atom_map(value: Any, field: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object keyed by chain/residue selection")
    normalized: dict[str, list[str]] = {}
    for key, atoms in value.items():
        key_text = _text(key, field, required=True)
        assert key_text is not None
        normalized[key_text] = _normalize_atom_value(atoms, f"{field}.{key_text}")
    return dict(sorted(normalized.items()))


def _normalize_selector_map(value: Any, field: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object keyed by target selection")
    normalized: dict[str, list[str]] = {}
    for key, targets in value.items():
        key_text = _text(key, field, required=True)
        assert key_text is not None
        normalized[key_text] = _string_list(targets, f"{field}.{key_text}")
    return dict(sorted(normalized.items()))


def _normalize_contig(value: Any, field: str = "contig") -> str | None:
    text = _text(value, field)
    if text is None:
        return None
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    tokens = [token.strip() for token in re.split(r"[/\s,]+", text) if token.strip()]
    if not tokens:
        return None
    normalized = ["/0" if token == "0" else token for token in tokens]
    return ",".join(normalized)


def _normalize_length(value: Any) -> int | list[int] | None:
    if value is None or value == "":
        return None
    raw_values = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value))
    lengths: list[int] = []
    for raw in raw_values:
        if str(raw).strip() == "":
            continue
        try:
            length = int(raw)
        except (TypeError, ValueError) as exc:
            raise ContractError("length must contain integer values") from exc
        if length < 1:
            raise ContractError("length values must be positive")
        lengths.append(length)
    if not lengths:
        return None
    return lengths[0] if len(lengths) == 1 else lengths


def _normalize_state_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContractError("evaluation_states must be a list of typed state objects")
    states: list[dict[str, Any]] = []
    for index, state in enumerate(value):
        if not isinstance(state, Mapping):
            raise ContractError(f"evaluation_states[{index}] must be an object")
        state_id = _text(state.get("id"), f"evaluation_states[{index}].id", required=True)
        role = _text(state.get("role"), f"evaluation_states[{index}].role", required=True)
        assert state_id is not None and role is not None
        item = {str(key): state[key] for key in state}
        item["id"] = state_id
        item["role"] = role
        states.append(item)
    return states


def _input_path(params: Mapping[str, Any]) -> str:
    for key in ("input_structure", "input_pdb", "input_cif", "input"):
        value = _text(params.get(key), key)
        if value:
            return value
    raise ContractError("input_structure is required")


def _build_native_spec(params: Mapping[str, Any], input_path: str, mode: str) -> dict[str, Any]:
    fixed_atoms = _normalize_atom_map(params.get("select_fixed_atoms"), "select_fixed_atoms")
    contig = _normalize_contig(params.get("contig"))
    unfixed_sequence = _normalize_residue_selector(
        params.get("select_unfixed_sequence"), "select_unfixed_sequence"
    )

    if mode == "partial_diffusion":
        if not fixed_atoms:
            raise ContractError(
                "partial_diffusion requires select_fixed_atoms with at least one selected residue"
            )
        if contig is not None:
            raise ContractError("partial_diffusion uses the supplied input structure and does not accept contig")
    elif mode == "minimal_insertion":
        if not contig:
            raise ContractError("minimal_insertion requires a dialect-2 contig")
        if unfixed_sequence:
            raise ContractError(
                "minimal_insertion sequence freedom must be expressed by the inserted region, not select_unfixed_sequence"
            )
    elif mode == "packing_shell" and not unfixed_sequence:
        raise ContractError("packing_shell requires an explicit select_unfixed_sequence set")

    native: dict[str, Any] = {
        "input": input_path,
    }
    if contig is not None:
        native["contig"] = contig
    if fixed_atoms:
        native["select_fixed_atoms"] = fixed_atoms
    if unfixed_sequence:
        native["select_unfixed_sequence"] = unfixed_sequence

    scalar_fields = (
        "ligand",
        "select_buried",
        "select_exposed",
        "select_partially_buried",
        "ori_token",
        "unindex",
    )
    for field in scalar_fields:
        value = params.get(field)
        if value is not None and value != "":
            native[field] = value

    list_fields = ("select_hotspots", "select_hbond_donor", "select_hbond_acceptor")
    for field in list_fields:
        values = params.get(field)
        if values is not None and values != "":
            normalized = (
                _normalize_selector_map(values, field)
                if isinstance(values, Mapping)
                else _string_list(values, field)
            )
            if normalized:
                native[field] = normalized

    partial_t = params.get("partial_t")
    if mode == "partial_diffusion" or partial_t is not None:
        native["partial_t"] = _nonnegative_float(
            partial_t, "partial_t", default=2.0 if mode == "partial_diffusion" else None
        )

    length = _normalize_length(params.get("length"))
    if length is not None:
        native["length"] = length
    return native


def build_request(
    params: Mapping[str, Any],
    *,
    job_name: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Normalize product parameters and construct the exact native RFD3 spec."""
    if not isinstance(params, Mapping):
        raise ContractError("local-redesign params must be an object")
    forbidden = sorted(_FORBIDDEN_FREE_FORM_KEYS.intersection(params))
    if forbidden:
        raise ContractError(f"free-form RFD3 configuration is forbidden: {', '.join(forbidden)}")

    input_path = _input_path(params)
    mode = _text(params.get("redesign_mode"), "redesign_mode") or "partial_diffusion"
    if mode not in SUPPORTED_MODES:
        raise ContractError(f"unsupported redesign_mode '{mode}'")
    registry = load_profile_registry()
    profile_id = _text(params.get("profile_id"), "profile_id") or "generic_local_redesign_v1"
    profile = get_profile(profile_id, registry)
    profile_registry_digest = profile_registry_sha256(registry)

    native = _build_native_spec(params, input_path, mode)
    num_designs = _positive_int(params.get("num_designs"), "num_designs", default=8)
    seed_value = params.get("seed", params.get("rfd3_seed"))
    seed = int(seed_value) if seed_value is not None else None
    if seed is not None and seed < 0:
        raise ContractError("seed must be non-negative")

    sequence_policy = _text(params.get("sequence_policy"), "sequence_policy")
    if sequence_policy is None:
        if mode == "minimal_insertion":
            sequence_policy = "insert_only"
        elif _normalize_residue_selector(params.get("select_unfixed_sequence"), "select_unfixed_sequence"):
            sequence_policy = "explicit_positions"
        else:
            sequence_policy = "preserve"
    if sequence_policy not in {"preserve", "insert_only", "explicit_positions", "external", "skip"}:
        raise ContractError(f"unsupported sequence_policy '{sequence_policy}'")
    if mode == "partial_diffusion" and sequence_policy == "preserve" and "select_unfixed_sequence" in native:
        raise ContractError("preserve sequence_policy cannot include select_unfixed_sequence")

    selection: dict[str, Any] = {}
    for field in ("region_mode", "insertion_anchor", "redesign_ranges"):
        value = _text(params.get(field), field)
        if value is not None:
            selection[field] = value
    for field in ("design_chains", "context_chains"):
        values = _string_list(params.get(field), field)
        if values:
            selection[field] = values
    residue_identities = params.get("source_residue_identities")
    if residue_identities is not None:
        if not isinstance(residue_identities, list) or any(not isinstance(item, Mapping) for item in residue_identities):
            raise ContractError("source_residue_identities must be a list of typed chain objects")
        selection["source_residue_identities"] = [dict(item) for item in residue_identities]
    insertion_min = params.get("insertion_min_length")
    insertion_max = params.get("insertion_max_length")
    if insertion_min is not None or insertion_max is not None:
        normalized_min = _positive_int(insertion_min, "insertion_min_length")
        normalized_max = _positive_int(insertion_max, "insertion_max_length")
        if normalized_max < normalized_min:
            raise ContractError("insertion_max_length must be greater than or equal to insertion_min_length")
        selection["insertion_min_length"] = normalized_min
        selection["insertion_max_length"] = normalized_max

    request: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "schema_version": 1,
        "workflow": "protein_local_redesign",
        "job_name": _text(job_name, "job_name") or "local_redesign",
        "profile_id": profile_id,
        "profile_registry_sha256": profile_registry_digest,
        "profile": profile,
        "redesign_mode": mode,
        "contig_dialect": 2,
        "sequence_policy": sequence_policy,
        "selection": selection,
        "input": {
            "path": input_path,
            "sha256": _text(source_sha256, "source_sha256"),
        },
        "rfd3": native,
        "execution": {
            "num_designs": num_designs,
            "seed": seed,
            "dump_trajectories": _bool(
                params.get("dump_trajectories", params.get("rfd3_dump_trajectories")),
                "dump_trajectories",
                default=False,
            ),
            "write_full_json": _bool(
                params.get("write_full_json", params.get("rfd3_output_full_json")),
                "write_full_json",
                default=True,
            ),
        },
        "evaluation": {
            "plan": {
                "positive_state": profile.get("positive_state"),
                "negative_states": profile.get("negative_states", []),
                "negative_design_policy": profile.get("negative_design_policy"),
            },
            "states": _normalize_state_list(params.get("evaluation_states")),
            "acceptance_context": params.get("acceptance_context") or {},
        },
    }
    return request


def write_request(path: str | Path, request: Mapping[str, Any]) -> str:
    """Write one canonical request and return its SHA-256 digest."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(request)
    destination.write_text(encoded + "\n", encoding="utf-8")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

"""Residue-identity-aware FrustraMPNN landscape comparisons."""

from __future__ import annotations

from collections import Counter
import copy
import math
from typing import Any, Mapping, Sequence

from .analytics import comparison_compatibility_id
from .contracts import canonical_sha256


class ComparisonValidationError(ValueError):
    """Raised when a landscape cannot participate in a comparison."""


class ComparisonCompatibilityError(ComparisonValidationError):
    """Raised when persisted comparison authority fails closed."""

    def __init__(self, metadata: Mapping[str, Any]):
        self.metadata = copy.deepcopy(dict(metadata))
        super().__init__(
            "FrustraMPNN comparison compatibility conflict: "
            f"{self.metadata['compatibility_status']}"
        )


_MISSING = object()


def _json_safe(value: Any) -> Any:
    """Return a deterministic JSON-safe projection for conflict details."""
    if value is _MISSING:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def _basis_differences(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []

    def visit(left_value: Any, right_value: Any, field_path: str) -> None:
        if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
            keys = sorted(
                set(left_value) | set(right_value), key=lambda item: str(item)
            )
            for key in keys:
                child_path = f"{field_path}.{key}" if field_path else str(key)
                visit(
                    left_value.get(key, _MISSING),
                    right_value.get(key, _MISSING),
                    child_path,
                )
            return
        if isinstance(left_value, (list, tuple)) and isinstance(
            right_value, (list, tuple)
        ):
            for index in range(max(len(left_value), len(right_value))):
                visit(
                    left_value[index] if index < len(left_value) else _MISSING,
                    right_value[index] if index < len(right_value) else _MISSING,
                    f"{field_path}[{index}]",
                )
            return
        if left_value is not _MISSING and right_value is not _MISSING:
            if left_value == right_value:
                return
        differences.append(
            {
                "field_path": field_path,
                "left": _json_safe(left_value),
                "right": _json_safe(right_value),
            }
        )

    visit(left, right, "")
    return differences


_BASIS_KEYS = {
    "schema_name",
    "schema_version",
    "raw_score_semantics",
    "classification_policy",
}
_RAW_KEYS = {
    "model",
    "tool",
    "capability",
    "output_schema",
    "canonical_amino_acid_order",
    "normalization",
}
_NESTED_BASIS_KEYS = {
    "model": {"checkpoint_id", "checkpoint_sha256"},
    "tool": {"tool_id", "tool_version"},
    "capability": {"schema_name", "schema_version", "content_sha256"},
    "output_schema": {
        "component_id",
        "component_contract_version",
        "landscape_schema_name",
        "landscape_schema_version",
        "score_field",
    },
    "normalization": {
        "normalizer_version",
        "identity_authority",
        "identity_domain",
        "selected_source_model",
        "altloc_policy",
        "normalization_policy_id",
        "normalization_policy_version",
    },
}
_CLASSIFICATION_KEYS = {"policy_id", "policy_sha256", "policy"}
_POLICY_KEYS = {"mode", "high_max", "minimal_min"}


def _closed_basis_v2(value: Mapping[str, Any]) -> bool:
    if set(value) != _BASIS_KEYS:
        return False
    if (
        value.get("schema_name")
        != "frustrampnn_comparison_compatibility_basis"
        or value.get("schema_version") != 2
    ):
        return False
    raw = value.get("raw_score_semantics")
    policy = value.get("classification_policy")
    if not isinstance(raw, Mapping) or set(raw) != _RAW_KEYS:
        return False
    for field, keys in _NESTED_BASIS_KEYS.items():
        nested = raw.get(field)
        if not isinstance(nested, Mapping) or set(nested) != keys:
            return False
    if not isinstance(raw.get("canonical_amino_acid_order"), str):
        return False
    if not isinstance(policy, Mapping) or set(policy) != _CLASSIFICATION_KEYS:
        return False
    policy_value = policy.get("policy")
    return isinstance(policy_value, Mapping) and set(policy_value) == _POLICY_KEYS


def _identity_projection(identity: tuple[Any, ...]) -> dict[str, Any]:
    return dict(
        zip(
            (
                "entity_instance_id",
                "source_entity_id",
                "label_asym_id",
                "auth_asym_id",
                "auth_seq_id",
                "insertion_code",
                "sequence_index",
                "wt",
            ),
            identity,
        )
    )


def _identity_alignment(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    left_set = {_source_residue_key(row) for row in left.get("residues", [])}
    right_set = {_source_residue_key(row) for row in right.get("residues", [])}
    shared = left_set & right_set
    if left_set == right_set:
        status = "exact"
        reasons = ["exact_source_authoritative_identity_membership"]
    elif shared:
        status = "partial"
        reasons = ["partial_source_authoritative_identity_membership"]
    else:
        status = "none"
        reasons = ["no_shared_source_authoritative_identity"]
    differences = [
        {"side": "reference_only", "identity": _identity_projection(identity)}
        for identity in sorted(left_set - right_set)
    ] + [
        {"side": "target_only", "identity": _identity_projection(identity)}
        for identity in sorted(right_set - left_set)
    ]
    return {
        "status": status,
        "reasons": reasons,
        "differences": differences,
        "reference_identity_count": len(left_set),
        "target_identity_count": len(right_set),
        "aligned_identity_count": len(shared),
    }


def _unknown_domains(
    left: Mapping[str, Any], right: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    unknown = {"status": "unknown", "reasons": [reason], "differences": []}
    return {
        "raw_score": dict(unknown),
        "classification": dict(unknown),
        "identity_alignment": _identity_alignment(left, right),
    }


def _assess_comparison_compatibility(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    left_id = left.get("comparison_compatibility_id")
    right_id = right.get("comparison_compatibility_id")
    left_basis = left.get("comparison_compatibility_basis")
    right_basis = right.get("comparison_compatibility_basis")
    ids_present = all(isinstance(value, str) and bool(value) for value in (left_id, right_id))
    bases_present = isinstance(left_basis, Mapping) and isinstance(right_basis, Mapping)

    if not ids_present or not bases_present:
        domains = _unknown_domains(left, right, "persisted_compatibility_authority_unavailable")
        status = "unknown"
        differences: list[dict[str, Any]] = []
    elif not _closed_basis_v2(left_basis) or not _closed_basis_v2(right_basis):
        domains = _unknown_domains(left, right, "compatibility_basis_not_closed_v2")
        domains["raw_score"] = {
            "status": "hard_incompatible",
            "reasons": ["compatibility_basis_not_closed_v2"],
            "differences": _basis_differences(left_basis, right_basis),
        }
        status = "incompatible"
        differences = domains["raw_score"]["differences"]
    else:
        left_bound = left_id == comparison_compatibility_id(left_basis)
        right_bound = right_id == comparison_compatibility_id(right_basis)
        raw_differences = [
            {
                **difference,
                "field_path": f"raw_score_semantics.{difference['field_path']}",
            }
            for difference in _basis_differences(
                left_basis["raw_score_semantics"],
                right_basis["raw_score_semantics"],
            )
        ]
        classification_differences = [
            {
                **difference,
                "field_path": f"classification_policy.{difference['field_path']}",
            }
            for difference in _basis_differences(
                left_basis["classification_policy"],
                right_basis["classification_policy"],
            )
        ]
        if not left_bound or not right_bound:
            raw_status = "hard_incompatible"
            raw_reasons = ["compatibility_basis_self_binding_invalid"]
        elif raw_differences:
            raw_status = "hard_incompatible"
            raw_reasons = ["raw_score_semantics_different"]
        else:
            raw_status = "compatible"
            raw_reasons = ["raw_score_semantics_equal"]
        domains = {
            "raw_score": {
                "status": raw_status,
                "reasons": raw_reasons,
                "differences": raw_differences,
            },
            "classification": {
                "status": (
                    "compatible"
                    if not classification_differences and left_bound and right_bound
                    else "policy_different"
                    if left_bound and right_bound
                    else "unknown"
                ),
                "reasons": [
                    "classification_policy_equal"
                    if not classification_differences and left_bound and right_bound
                    else "classification_policy_different"
                    if left_bound and right_bound
                    else "compatibility_basis_self_binding_invalid"
                ],
                "differences": classification_differences,
            },
            "identity_alignment": _identity_alignment(left, right),
        }
        status = "compatible" if raw_status == "compatible" else "incompatible"
        differences = raw_differences

    return {
        "compatibility_status": status,
        "left_comparison_compatibility_id": left_id if isinstance(left_id, str) else None,
        "right_comparison_compatibility_id": right_id if isinstance(right_id, str) else None,
        "override_used": False,
        "compatibility_differences": differences,
        "compatibility_domains": domains,
    }


def comparison_compatibility(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    allow_incompatible: bool = False,
) -> dict[str, Any]:
    """Assess raw, classification, and alignment authority independently."""
    metadata = _assess_comparison_compatibility(left, right)
    raw_status = metadata["compatibility_domains"]["raw_score"]["status"]
    metadata["override_used"] = bool(allow_incompatible and raw_status != "compatible")
    if raw_status != "compatible" and not allow_incompatible:
        raise ComparisonCompatibilityError(metadata)
    return metadata


def comparison_set_compatibility(
    reference: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    *,
    allow_incompatible: bool = False,
) -> dict[str, Any]:
    """Assess every reference-target pair and retain every field-level outcome."""
    if not targets:
        raise ComparisonValidationError("at least one target landscape is required")
    pair_metadata = []
    for index, target in enumerate(targets, start=1):
        item = _assess_comparison_compatibility(reference, target)
        item["target_label"] = f"target-{index:04d}"
        item["target_id"] = target.get("target_id")
        item["target_landscape_sha256"] = _landscape_hash(target)
        item["target_configuration_sha256"] = _configuration_sha256(target)
        pair_metadata.append(item)

    raw_statuses = [
        item["compatibility_domains"]["raw_score"]["status"]
        for item in pair_metadata
    ]
    if "hard_incompatible" in raw_statuses:
        compatibility_status = "incompatible"
    elif "unknown" in raw_statuses:
        compatibility_status = "unknown"
    else:
        compatibility_status = "compatible"

    differences_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, (target, item) in enumerate(zip(targets, pair_metadata)):
        target_id = str(target.get("target_id") or f"target-{index}")
        for difference in item["compatibility_differences"]:
            labelled = {**difference, "field_path": f"{target_id}:{difference['field_path']}"}
            key = (
                labelled["field_path"],
                canonical_sha256(labelled["left"]),
                canonical_sha256(labelled["right"]),
            )
            differences_by_key[key] = labelled

    right_ids = {item["right_comparison_compatibility_id"] for item in pair_metadata}
    metadata = {
        "compatibility_status": compatibility_status,
        "left_comparison_compatibility_id": pair_metadata[0]["left_comparison_compatibility_id"],
        "right_comparison_compatibility_id": next(iter(right_ids)) if len(right_ids) == 1 else None,
        "override_used": bool(allow_incompatible and compatibility_status != "compatible"),
        "compatibility_differences": [differences_by_key[key] for key in sorted(differences_by_key)],
        "pair_compatibility": pair_metadata,
    }
    if compatibility_status != "compatible" and not allow_incompatible:
        raise ComparisonCompatibilityError(metadata)
    return metadata


def _source_residue_key(residue: Mapping[str, Any]) -> tuple[Any, ...]:
    """Use the complete source-authoritative residue identity for alignment."""
    try:
        return (
            str(residue["entity_instance_id"]),
            str(residue.get("source_entity_id") or ""),
            str(residue.get("label_asym_id") or ""),
            str(residue["auth_asym_id"]),
            int(residue["auth_seq_id"]),
            str(residue.get("insertion_code") or ""),
            int(residue["sequence_index"]),
            str(residue["wt"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonValidationError("landscape residue identity is invalid") from exc


def _residue_key(residue: Mapping[str, Any]) -> tuple[str, str, int, str]:
    """Compatibility helper retained for guidance's explicit region contract."""
    try:
        return (
            str(residue["entity_instance_id"]),
            str(residue["auth_asym_id"]),
            int(residue["auth_seq_id"]),
            str(residue.get("insertion_code") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonValidationError("landscape residue identity is invalid") from exc


def _slot_map(landscape: Mapping[str, Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
    slots: dict[tuple[Any, ...], dict[str, Any]] = {}
    for residue in landscape.get("residues", []):
        residue_key = _source_residue_key(residue)
        for slot in residue.get("slots", []):
            mutation = str(slot.get("mutation_aa", ""))
            if len(mutation) != 1:
                raise ComparisonValidationError("landscape substitution identity is invalid")
            key = (*residue_key, mutation)
            if key in slots:
                raise ComparisonValidationError(f"duplicate landscape slot: {key}")
            slots[key] = {"residue": dict(residue), "slot": dict(slot)}
    return slots


def _landscape_hash(landscape: Mapping[str, Any]) -> str:
    return str(landscape.get("landscape_sha256") or canonical_sha256(dict(landscape)))


def _configuration_sha256(landscape: Mapping[str, Any]) -> Any:
    return landscape.get("execution_configuration_sha256") or landscape.get(
        "configuration_sha256"
    )


def _compatibility(
    reference: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    allow_incompatible: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = comparison_compatibility(
        reference, target, allow_incompatible=allow_incompatible
    )
    reasons: list[str] = []
    if metadata["compatibility_status"] == "unknown":
        reasons.append("comparison_compatibility_unknown")
    elif metadata["compatibility_status"] == "incompatible":
        reasons.append("comparison_compatibility_id_or_basis")
    reasons.extend(
        f"comparison_compatibility_basis:{item['field_path']}"
        for item in metadata["compatibility_differences"]
    )
    if metadata["override_used"]:
        reasons.append("compatibility_override_used")
    return {
        "status": (
            "comparable"
            if metadata["compatibility_status"] == "compatible"
            else "incompatible"
        ),
        "reasons": reasons,
        "reference_configuration_id": reference.get("configuration_id"),
        "target_configuration_id": target.get("configuration_id"),
        "reference_configuration_sha256": _configuration_sha256(reference),
        "target_configuration_sha256": _configuration_sha256(target),
    }, metadata


def _scoreable(entry: Mapping[str, Any] | None) -> bool:
    return bool(entry and entry.get("slot", {}).get("scoreable") and entry["slot"].get("score") is not None)


def _missingness(reference: Mapping[str, Any] | None, target: Mapping[str, Any] | None) -> str:
    if reference is None and target is None:
        return "both_unmapped"
    if reference is None:
        return "reference_unmapped"
    if target is None:
        return "target_unmapped"
    ref_ok = _scoreable(reference)
    target_ok = _scoreable(target)
    if ref_ok and target_ok:
        return "none"
    if not ref_ok and not target_ok:
        return "both_missing"
    return "reference_missing" if not ref_ok else "target_missing"


def _transition(reference_class: Any, target_class: Any) -> str | None:
    if reference_class is None or target_class is None or reference_class == target_class:
        return None
    return f"{reference_class}_to_{target_class}"


def compare_landscapes(
    reference: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    comparison_id: str | None = None,
    allow_incompatible: bool = False,
) -> dict[str, Any]:
    """Compare two immutable landscapes without joining by row order or position."""
    if not isinstance(reference, Mapping) or not isinstance(target, Mapping):
        raise ComparisonValidationError("reference and target landscapes are required")
    ref_slots = _slot_map(reference)
    target_slots = _slot_map(target)
    compatibility, compatibility_metadata = _compatibility(
        reference, target, allow_incompatible=allow_incompatible
    )
    raw_domain = compatibility_metadata["compatibility_domains"]["raw_score"]
    classification_domain = compatibility_metadata["compatibility_domains"][
        "classification"
    ]
    numerical_comparison_allowed = raw_domain["status"] == "compatible"
    classification_comparison_allowed = (
        numerical_comparison_allowed
        and classification_domain["status"] == "compatible"
    )
    rows: list[dict[str, Any]] = []
    for key in sorted(set(ref_slots) | set(target_slots)):
        ref_entry = ref_slots.get(key)
        target_entry = target_slots.get(key)
        missingness = _missingness(ref_entry, target_entry)
        mapping_state = "mapped" if ref_entry is not None and target_entry is not None else "unmapped"
        ref_slot = ref_entry["slot"] if ref_entry else None
        target_slot = target_entry["slot"] if target_entry else None
        delta = None
        biological_status = "unmapped" if mapping_state == "unmapped" else "missing"
        if not numerical_comparison_allowed:
            biological_status = "incompatible"
        elif missingness == "none":
            delta = float(target_slot["score"]) - float(ref_slot["score"])
            biological_status = "biologically_scored"
        residue = (target_entry or ref_entry)["residue"]
        rows.append({
            "residue_key": {
                "entity_instance_id": residue["entity_instance_id"],
                "auth_asym_id": residue["auth_asym_id"],
                "auth_seq_id": residue["auth_seq_id"],
                "insertion_code": residue.get("insertion_code") or "",
            },
            "sequence_index": residue.get("sequence_index"),
            "mutation_aa": key[-1],
            "wt": residue.get("wt"),
            "mapping_state": mapping_state,
            "missingness_state": missingness,
            "biological_status": biological_status,
            "reference": {
                "sequence_index": ref_entry["residue"].get("sequence_index") if ref_entry else None,
                "auth_seq_id": ref_entry["residue"].get("auth_seq_id") if ref_entry else None,
                "score": ref_slot.get("score") if ref_slot else None,
                "class": ref_slot.get("class") if ref_slot else None,
                "scoreable": bool(ref_slot and ref_slot.get("scoreable")),
                "status": ref_slot.get("status") if ref_slot else "unmapped",
            },
            "target": {
                "sequence_index": target_entry["residue"].get("sequence_index") if target_entry else None,
                "auth_seq_id": target_entry["residue"].get("auth_seq_id") if target_entry else None,
                "score": target_slot.get("score") if target_slot else None,
                "class": target_slot.get("class") if target_slot else None,
                "scoreable": bool(target_slot and target_slot.get("scoreable")),
                "status": target_slot.get("status") if target_slot else "unmapped",
            },
            "raw_score_delta": delta,
            "classification_transition": (
                _transition(ref_slot.get("class"), target_slot.get("class"))
                if (
                    biological_status == "biologically_scored"
                    and classification_comparison_allowed
                )
                else None
            ),
        })
    counts = Counter(row["missingness_state"] for row in rows)
    summary = {
        "total_rows": len(rows),
        "biologically_scored": sum(row["biological_status"] == "biologically_scored" for row in rows),
        "incompatible": sum(row["biological_status"] == "incompatible" for row in rows),
        "unmapped": sum(row["biological_status"] == "unmapped" for row in rows),
        "missing_reference": counts["reference_missing"],
        "missing_target": counts["target_missing"],
        "missing_both": counts["both_missing"],
        "transitions": sum(row["classification_transition"] is not None for row in rows),
    }
    payload = {
        "schema_name": "frustrampnn_comparison",
        "schema_version": 1,
        "comparison_id": comparison_id,
        "reference_landscape_sha256": _landscape_hash(reference),
        "target_landscape_sha256": _landscape_hash(target),
        "configuration_id": reference.get("configuration_id"),
        "configuration_sha256": _configuration_sha256(reference),
        "reference_configuration_sha256": _configuration_sha256(reference),
        "target_configuration_sha256": _configuration_sha256(target),
        "comparability": compatibility,
        "compatibility_domains": compatibility_metadata["compatibility_domains"],
        "summary": summary,
        "rows": rows,
    }
    payload["comparison_sha256"] = canonical_sha256(payload)
    return payload


def compare_landscape_set(
    reference: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    *,
    comparison_id: str | None = None,
    allow_incompatible: bool = False,
) -> dict[str, Any]:
    """Compare one reference landscape against multiple target states."""
    compatibility_metadata = comparison_set_compatibility(
        reference,
        targets,
        allow_incompatible=allow_incompatible,
    )
    pairwise = [
        compare_landscapes(
            reference, target, allow_incompatible=allow_incompatible
        )
        for target in targets
    ]
    row_maps: list[dict[tuple[str, str, int, str, str], dict[str, Any]]] = []
    for payload in pairwise:
        row_maps.append({
            (
                row["residue_key"]["entity_instance_id"],
                row["residue_key"]["auth_asym_id"],
                int(row["residue_key"]["auth_seq_id"]),
                row["residue_key"].get("insertion_code", ""),
                row["mutation_aa"],
            ): row
            for row in payload["rows"]
        })
    keys = sorted(set().union(*(mapping.keys() for mapping in row_maps)), key=lambda value: (value[:4], value[4]))
    comparable = compatibility_metadata["compatibility_status"] == "compatible"
    reasons = sorted({f"target_{index}:{reason}" for index, payload in enumerate(pairwise) for reason in payload["comparability"]["reasons"]})
    rows: list[dict[str, Any]] = []
    for key in keys:
        states = [mapping.get(key) for mapping in row_maps]
        reference_row = next((state for state in states if state is not None), None)
        missingness_by_target = [state["missingness_state"] if state else "target_unmapped" for state in states]
        biological_states = [state["biological_status"] if state else "unmapped" for state in states]
        if all(value == "incompatible" for value in biological_states):
            biological_status = "incompatible"
        elif all(value == "biologically_scored" for value in biological_states):
            biological_status = "biologically_scored"
        elif any(value == "biologically_scored" for value in biological_states):
            biological_status = "partially_scored"
        elif any(value == "missing" for value in biological_states):
            biological_status = "missing"
        else:
            biological_status = "unmapped"
        rows.append({
            "residue_key": reference_row["residue_key"] if reference_row else {
                "entity_instance_id": key[0], "auth_asym_id": key[1], "auth_seq_id": key[2], "insertion_code": key[3]
            },
            "sequence_index": reference_row.get("sequence_index") if reference_row else None,
            "mutation_aa": key[4],
            "mapping_state": "mapped" if all(state and state["mapping_state"] == "mapped" for state in states) else "unmapped",
            "missingness_state": "none" if all(value == "none" for value in missingness_by_target) else "per_target",
            "missingness_by_target": missingness_by_target,
            "biological_status": biological_status,
            "reference": reference_row["reference"] if reference_row else None,
            "targets": [state["target"] if state else None for state in states],
            "raw_score_deltas": [state["raw_score_delta"] if state else None for state in states],
            "classification_transitions": [state["classification_transition"] if state else None for state in states],
        })
    payload = {
        "schema_name": "frustrampnn_multistate_comparison",
        "schema_version": 1,
        "comparison_mode": "multi_state",
        "comparison_id": comparison_id,
        "reference_landscape_sha256": _landscape_hash(reference),
        "target_landscape_sha256": _landscape_hash(targets[0]),
        "target_landscape_sha256s": [_landscape_hash(target) for target in targets],
        "target_labels": [f"target-{index:04d}" for index in range(1, len(targets) + 1)],
        "configuration_id": reference.get("configuration_id"),
        "configuration_sha256": _configuration_sha256(reference),
        "reference_configuration_sha256": _configuration_sha256(reference),
        "target_configuration_sha256s": [
            _configuration_sha256(target) for target in targets
        ],
        "pair_compatibility": compatibility_metadata["pair_compatibility"],
        "comparability": {
            "status": "comparable" if comparable else "incompatible",
            "reasons": reasons,
            "target_count": len(targets),
            **compatibility_metadata,
        },
        "summary": {
            "target_count": len(targets),
            "total_rows": len(rows),
            "biologically_scored": sum(row["biological_status"] == "biologically_scored" for row in rows),
            "partially_scored": sum(row["biological_status"] == "partially_scored" for row in rows),
            "missing": sum(row["biological_status"] == "missing" for row in rows),
            "unmapped": sum(row["biological_status"] == "unmapped" for row in rows),
            "incompatible": sum(row["biological_status"] == "incompatible" for row in rows),
            "transitions": sum(any(value is not None for value in row["classification_transitions"]) for row in rows),
        },
        "rows": rows,
    }
    payload["comparison_sha256"] = canonical_sha256(payload)
    return payload


__all__ = [
    "ComparisonCompatibilityError",
    "ComparisonValidationError",
    "compare_landscape_set",
    "compare_landscapes",
    "comparison_compatibility",
    "comparison_set_compatibility",
]

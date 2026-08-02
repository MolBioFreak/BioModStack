"""Residue-identity-aware FrustraMPNN landscape comparisons."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .contracts import canonical_sha256


class ComparisonValidationError(ValueError):
    """Raised when a landscape cannot participate in a comparison."""


def _residue_key(residue: Mapping[str, Any]) -> tuple[str, str, int, str]:
    try:
        return (
            str(residue["entity_instance_id"]),
            str(residue["auth_asym_id"]),
            int(residue["auth_seq_id"]),
            str(residue.get("insertion_code") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonValidationError("landscape residue lacks stable identity") from exc


def _slot_map(landscape: Mapping[str, Any]) -> dict[tuple[str, str, int, str, str], dict[str, Any]]:
    slots: dict[tuple[str, str, int, str, str], dict[str, Any]] = {}
    for residue in landscape.get("residues", []):
        residue_key = _residue_key(residue)
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


def _compatibility(reference: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    ref_config = reference.get("configuration_sha256")
    target_config = target.get("configuration_sha256")
    if not ref_config or not target_config:
        reasons.append("configuration_sha256_missing")
    elif ref_config != target_config:
        reasons.append("configuration_sha256")
    if reference.get("configuration_id") != target.get("configuration_id"):
        reasons.append("configuration_id")
    ref_policy = reference.get("threshold_policy_sha256")
    target_policy = target.get("threshold_policy_sha256")
    if ref_policy and target_policy and ref_policy != target_policy:
        reasons.append("threshold_policy_sha256")
    if reference.get("threshold_policy") != target.get("threshold_policy"):
        reasons.append("threshold_policy")
    return {
        "status": "comparable" if not reasons else "incompatible",
        "reasons": sorted(set(reasons)),
        "reference_configuration_id": reference.get("configuration_id"),
        "target_configuration_id": target.get("configuration_id"),
        "reference_configuration_sha256": ref_config,
        "target_configuration_sha256": target_config,
    }


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
) -> dict[str, Any]:
    """Compare two immutable landscapes without joining by row order or position."""
    if not isinstance(reference, Mapping) or not isinstance(target, Mapping):
        raise ComparisonValidationError("reference and target landscapes are required")
    ref_slots = _slot_map(reference)
    target_slots = _slot_map(target)
    compatibility = _compatibility(reference, target)
    rows: list[dict[str, Any]] = []
    for key in sorted(set(ref_slots) | set(target_slots), key=lambda value: (value[:4], value[4])):
        ref_entry = ref_slots.get(key)
        target_entry = target_slots.get(key)
        missingness = _missingness(ref_entry, target_entry)
        mapping_state = "mapped" if ref_entry is not None and target_entry is not None else "unmapped"
        ref_slot = ref_entry["slot"] if ref_entry else None
        target_slot = target_entry["slot"] if target_entry else None
        delta = None
        biological_status = "unmapped" if mapping_state == "unmapped" else "missing"
        if compatibility["status"] == "incompatible":
            biological_status = "incompatible"
        elif missingness == "none":
            delta = float(target_slot["score"]) - float(ref_slot["score"])
            biological_status = "biologically_scored"
        rows.append({
            "residue_key": {
                "entity_instance_id": key[0],
                "auth_asym_id": key[1],
                "auth_seq_id": key[2],
                "insertion_code": key[3],
            },
            "sequence_index": (
                target_entry or ref_entry
            )["residue"].get("sequence_index"),
            "mutation_aa": key[4],
            "wt": ((target_entry or ref_entry)["residue"].get("wt")),
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
                if biological_status == "biologically_scored" else None
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
        "configuration_sha256": reference.get("configuration_sha256"),
        "comparability": compatibility,
        "summary": summary,
        "rows": rows,
    }
    payload["comparison_sha256"] = canonical_sha256(payload)
    return payload


__all__ = ["ComparisonValidationError", "compare_landscapes"]

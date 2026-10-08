"""Immutable, explicit state-conditioned comparisons of canonical FrustraMPNN landscapes."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .contracts import AA_ORDER, canonical_sha256, validate_schema


class StateLandscapeAnalysisError(ValueError):
    """Canonical state landscapes cannot be compared under the supplied policy."""


_FORMULA = {
    "version": "cm_state_landscape_analysis_v1",
    "native_score": "native_score_b - native_score_a",
    "high_non_native_highly_frustrated_fraction": "count(non_native_class_high) / 19; b - a",
    "maximum_non_native_substitution_delta_relative_to_native": "max(non_native_score - native_score); b - a",
    "native_class": "native_class_a_to_native_class_b",
}

_THREE_TO_ONE = {
    "ALA": "A", "CYS": "C", "ASP": "D", "GLU": "E", "PHE": "F", "GLY": "G",
    "HIS": "H", "ILE": "I", "LYS": "K", "LEU": "L", "MET": "M", "ASN": "N",
    "PRO": "P", "GLN": "Q", "ARG": "R", "SER": "S", "THR": "T", "VAL": "V",
    "TRP": "W", "TYR": "Y",
}


def _residue_key(row: Mapping[str, Any]) -> tuple[str, str, int, str, int]:
    return (
        str(row["entity_instance_id"]), str(row["auth_asym_id"]), int(row["auth_seq_id"]),
        str(row.get("insertion_code") or ""), int(row["sequence_index"]),
    )


def _source_by_candidate(
    values: Sequence[Mapping[str, Any]], *, schema_key: str, label: str,
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for value in values:
        validate_schema(schema_key, value)
        candidate_id = str(value["candidate_id"])
        if candidate_id in indexed:
            raise StateLandscapeAnalysisError(f"duplicate {label} candidate: {candidate_id}")
        indexed[candidate_id] = value
    return indexed


def _comparison_pairs(comparison: Mapping[str, Any]) -> tuple[str, str | None, list[dict[str, str]]]:
    mode = comparison.get("mode")
    if mode == "pairwise":
        raw_pairs = comparison.get("pairs")
        if not isinstance(raw_pairs, list) or not raw_pairs:
            raise StateLandscapeAnalysisError("pairwise comparison requires explicit nonempty pairs")
        pairs: list[dict[str, str]] = []
        for raw in raw_pairs:
            if not isinstance(raw, Mapping):
                raise StateLandscapeAnalysisError("pairwise comparison pair must be an object")
            pair_id = raw.get("pair_id")
            candidate_a_id = raw.get("candidate_a_id")
            candidate_b_id = raw.get("candidate_b_id")
            if not all(isinstance(value, str) and value for value in (pair_id, candidate_a_id, candidate_b_id)):
                raise StateLandscapeAnalysisError("pairwise comparison requires pair and candidate identities")
            if candidate_a_id == candidate_b_id:
                raise StateLandscapeAnalysisError("pairwise comparison candidates must differ")
            pairs.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id})
        if len({item["pair_id"] for item in pairs}) != len(pairs):
            raise StateLandscapeAnalysisError("pairwise comparison pair IDs must be unique")
        return mode, None, sorted(pairs, key=lambda item: item["pair_id"])
    if mode == "reference":
        reference = comparison.get("reference_candidate_id")
        if not isinstance(reference, str) or not reference:
            raise StateLandscapeAnalysisError("reference comparison requires explicit reference_candidate_id")
        candidates = comparison.get("candidate_ids")
        if not isinstance(candidates, list) or not candidates:
            raise StateLandscapeAnalysisError("reference comparison requires explicit candidate_ids")
        if any(not isinstance(value, str) or not value for value in candidates):
            raise StateLandscapeAnalysisError("reference comparison candidate IDs are invalid")
        if reference in candidates or len(set(candidates)) != len(candidates):
            raise StateLandscapeAnalysisError("reference comparison candidates must be unique and exclude reference")
        return mode, reference, [
            {"pair_id": f"{reference}__{candidate}", "candidate_a_id": reference, "candidate_b_id": candidate}
            for candidate in sorted(candidates)
        ]
    raise StateLandscapeAnalysisError("comparison mode must be explicit reference or pairwise")


def _map_rows(structure_map: Mapping[str, Any]) -> dict[tuple[str, str, int, str, int], str]:
    rows: dict[tuple[str, str, int, str, int], str] = {}
    for row in structure_map["rows"]:
        if row["status"] != "mapped":
            continue
        wt = _THREE_TO_ONE.get(str(row["residue_name"]).upper())
        if wt is None:
            continue
        key = _residue_key(row)
        if key in rows:
            raise StateLandscapeAnalysisError("structure map has duplicate mapped comparison identity")
        rows[key] = wt
    return rows


def _unavailable_numeric(reason: str) -> dict[str, Any]:
    return {"a": None, "b": None, "delta_b_minus_a": None, "status": "unavailable", "reason": reason}


def _unavailable_class(reason: str) -> dict[str, Any]:
    return {"a": None, "b": None, "transition": None, "status": "unavailable", "reason": reason}


def _slot(row: Mapping[str, Any], amino_acid: str) -> Mapping[str, Any] | None:
    return next((slot for slot in row["slots"] if slot["mutation_aa"] == amino_acid), None)


def _score(slot: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    if slot is None:
        return None, "missing_slot"
    value = slot.get("score")
    if (
        slot.get("status") != "ok" or slot.get("scoreable") is not True
        or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
    ):
        return None, "nonfinite_or_unavailable_slot"
    return float(value), None


def _numeric_metrics(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    native_a, native_a_reason = _score(_slot(a, str(a["wt"])))
    native_b, native_b_reason = _score(_slot(b, str(b["wt"])))
    native_reason = native_a_reason or native_b_reason
    if native_reason:
        native_metric = _unavailable_numeric(native_reason)
        class_metric = _unavailable_class(native_reason)
    else:
        assert native_a is not None and native_b is not None
        native_metric = {"a": native_a, "b": native_b, "delta_b_minus_a": native_b - native_a, "status": "ok", "reason": None}
        class_a = _slot(a, str(a["wt"]))["class"]
        class_b = _slot(b, str(b["wt"]))["class"]
        class_metric = {
            "a": class_a, "b": class_b, "transition": f"{class_a}_to_{class_b}", "status": "ok", "reason": None,
        }

    non_native_a = [_slot(a, aa) for aa in AA_ORDER if aa != a["wt"]]
    non_native_b = [_slot(b, aa) for aa in AA_ORDER if aa != b["wt"]]
    nonnative_scores_a = [_score(slot) for slot in non_native_a]
    nonnative_scores_b = [_score(slot) for slot in non_native_b]
    nonnative_reason = next((reason for _, reason in [*nonnative_scores_a, *nonnative_scores_b] if reason), None)
    if nonnative_reason:
        fraction_metric = _unavailable_numeric(nonnative_reason)
    else:
        high_a = sum(slot["class"] == "high" for slot in non_native_a)
        high_b = sum(slot["class"] == "high" for slot in non_native_b)
        fraction_a, fraction_b = high_a / 19, high_b / 19
        fraction_metric = {
            "a": fraction_a, "b": fraction_b, "delta_b_minus_a": fraction_b - fraction_a,
            "status": "ok", "reason": None,
        }
    if native_reason or nonnative_reason:
        maximum_metric = _unavailable_numeric(native_reason or nonnative_reason or "missing_slot")
    else:
        assert native_a is not None and native_b is not None
        maximum_a = max(float(score) - native_a for score, _ in nonnative_scores_a)
        maximum_b = max(float(score) - native_b for score, _ in nonnative_scores_b)
        maximum_metric = {
            "a": maximum_a, "b": maximum_b, "delta_b_minus_a": maximum_b - maximum_a,
            "status": "ok", "reason": None,
        }
    return {
        "native_score": native_metric,
        "high_non_native_highly_frustrated_fraction": fraction_metric,
        "maximum_non_native_substitution_delta_relative_to_native": maximum_metric,
        "native_class": class_metric,
    }


def _identity(target_id: str, key: tuple[str, str, int, str, int], wt: str) -> dict[str, Any]:
    return {
        "target_id": target_id, "entity_instance_id": key[0], "auth_asym_id": key[1],
        "auth_seq_id": key[2], "insertion_code": key[3], "sequence_index": key[4], "validated_wt": wt,
    }


def derive_state_landscape_analysis(
    ensemble: Mapping[str, Any],
    landscapes: Sequence[Mapping[str, Any]],
    structure_maps: Sequence[Mapping[str, Any]],
    *,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare only explicitly selected canonical state candidates without imputation."""

    if not isinstance(ensemble, Mapping):
        raise StateLandscapeAnalysisError("ensemble must be a canonical object")
    mode, reference_candidate_id, pairs = _comparison_pairs(comparison)
    landscape_by_candidate = _source_by_candidate(
        landscapes, schema_key="cm_frustration_landscape_v1", label="landscape",
    )
    map_by_candidate = _source_by_candidate(
        structure_maps, schema_key="cm_structure_map_v1", label="structure map",
    )
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    support: list[dict[str, Any]] = []
    for pair in pairs:
        pair_id, candidate_a_id, candidate_b_id = pair["pair_id"], pair["candidate_a_id"], pair["candidate_b_id"]
        pair_exclusions_before = len(exclusions)
        missing_landscape = next(
            (candidate_id for candidate_id in (candidate_a_id, candidate_b_id) if candidate_id not in landscape_by_candidate), None,
        )
        if missing_landscape is not None:
            exclusions.append({
                "pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id,
                "identity": None, "reason": "candidate_analysis_unavailable", "detail": "candidate has no canonical landscape",
            })
            support.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "eligible_row_count": 0, "excluded_row_count": 1})
            continue
        missing_map = next(
            (candidate_id for candidate_id in (candidate_a_id, candidate_b_id) if candidate_id not in map_by_candidate), None,
        )
        if missing_map is not None:
            exclusions.append({
                "pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id,
                "identity": None, "reason": "missing_map", "detail": "candidate has no authoritative structure map",
            })
            support.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "eligible_row_count": 0, "excluded_row_count": 1})
            continue
        landscape_a, landscape_b = landscape_by_candidate[candidate_a_id], landscape_by_candidate[candidate_b_id]
        map_a, map_b = map_by_candidate[candidate_a_id], map_by_candidate[candidate_b_id]
        rows_a = {_residue_key(row): row for row in landscape_a["residues"]}
        rows_b = {_residue_key(row): row for row in landscape_b["residues"]}
        maps_a, maps_b = _map_rows(map_a), _map_rows(map_b)
        all_keys = sorted(set(rows_a) | set(rows_b))
        eligible = 0
        provenance_match = all(
            landscape_a[field] == landscape_b[field]
            for field in ("checkpoint_id", "checkpoint_sha256", "tool_id", "tool_sha256", "threshold_policy_id", "threshold_policy_sha256")
        )
        for key in all_keys:
            row_a, row_b = rows_a.get(key), rows_b.get(key)
            target_id = str(landscape_a["target_id"] if row_a is not None else landscape_b["target_id"])
            provisional_wt = str(row_a["wt"] if row_a is not None else row_b["wt"])
            identity = _identity(target_id, key, provisional_wt)
            if row_a is None or row_b is None:
                exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": "missing_row", "detail": "residue is absent from one candidate landscape"})
                continue
            if key not in maps_a or key not in maps_b or str(map_a["target_id"]) != str(landscape_a["target_id"]) or str(map_b["target_id"]) != str(landscape_b["target_id"]):
                exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": "identity_mismatch", "detail": "landscape residue is not bound to both authoritative maps"})
                continue
            if row_a["wt"] != row_b["wt"] or row_a["wt"] != maps_a[key] or row_b["wt"] != maps_b[key]:
                exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": "wt_mismatch", "detail": "validated WT disagrees across state landscapes or structure maps"})
                rows.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "metrics": {key: _unavailable_numeric("wt_mismatch") for key in ("native_score", "high_non_native_highly_frustrated_fraction", "maximum_non_native_substitution_delta_relative_to_native")} | {"native_class": _unavailable_class("wt_mismatch")}})
                continue
            identity = _identity(str(landscape_a["target_id"]), key, str(row_a["wt"]))
            if str(landscape_a["target_id"]) != str(landscape_b["target_id"]):
                exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": "identity_mismatch", "detail": "candidate target IDs disagree"})
                continue
            if not provenance_match:
                exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": "provenance_mismatch", "detail": "checkpoint, tool, or threshold provenance differs"})
                rows.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "metrics": {key: _unavailable_numeric("provenance_mismatch") for key in ("native_score", "high_non_native_highly_frustrated_fraction", "maximum_non_native_substitution_delta_relative_to_native")} | {"native_class": _unavailable_class("provenance_mismatch")}})
                continue
            metrics = _numeric_metrics(row_a, row_b)
            for metric in metrics.values():
                if metric["status"] == "unavailable":
                    exclusions.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "reason": metric["reason"], "detail": "required canonical landscape slot is unavailable"})
            rows.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "identity": identity, "metrics": metrics})
            eligible += 1
        support.append({"pair_id": pair_id, "candidate_a_id": candidate_a_id, "candidate_b_id": candidate_b_id, "eligible_row_count": eligible, "excluded_row_count": len(exclusions) - pair_exclusions_before})

    rows.sort(key=lambda row: (row["pair_id"], row["identity"]["target_id"], row["identity"]["entity_instance_id"], row["identity"]["auth_asym_id"], row["identity"]["auth_seq_id"], row["identity"]["insertion_code"], row["identity"]["sequence_index"]))
    exclusions.sort(key=lambda row: (row["pair_id"], "" if row["identity"] is None else canonical_sha256(row["identity"]), row["reason"], row["detail"]))
    support.sort(key=lambda row: row["pair_id"])
    source_landscapes = {candidate_id: landscape_by_candidate[candidate_id] for candidate_id in sorted(landscape_by_candidate)}
    source_maps = {candidate_id: map_by_candidate[candidate_id] for candidate_id in sorted(map_by_candidate)}
    comparison_payload = {"mode": mode, "reference_candidate_id": reference_candidate_id, "pairs": pairs}
    policy = {"strict_required_slots": "status_ok_scoreable_finite", "no_imputation": True, "identity": "target_entity_auth_residue_insertion_sequence_wt"}
    artifact_without_id = {
        "source_ensemble_sha256": canonical_sha256(ensemble), "source_landscape_sha256": canonical_sha256(source_landscapes),
        "source_structure_map_sha256": canonical_sha256(source_maps), "comparison": comparison_payload,
        "formula": _FORMULA, "policy": policy, "rows": rows, "support_ledger": support, "exclusion_ledger": exclusions,
    }
    artifact = {
        "schema_name": "cm_state_landscape_analysis", "schema_version": 1,
        "analysis_id": "cm_state_landscape_analysis_" + canonical_sha256(artifact_without_id)[:32],
        "source_ensemble_sha256": artifact_without_id["source_ensemble_sha256"],
        "source_landscape_sha256": artifact_without_id["source_landscape_sha256"],
        "source_structure_map_sha256": artifact_without_id["source_structure_map_sha256"],
        "comparison_mode": mode, "reference_candidate_id": reference_candidate_id,
        "comparison_sha256": canonical_sha256(comparison_payload), "formula_version": "cm_state_landscape_analysis_v1",
        "formula_sha256": canonical_sha256(_FORMULA), "policy_sha256": canonical_sha256(policy),
        "rows": rows, "support_ledger": support, "exclusion_ledger": exclusions,
    }
    validate_schema("cm_state_landscape_analysis_v1", artifact)
    return artifact

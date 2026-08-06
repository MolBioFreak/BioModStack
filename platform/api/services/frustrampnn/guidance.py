"""Explicit objective and immutable FrustraMPNN guidance plans."""

from __future__ import annotations

import copy
from statistics import mean, median
from typing import Any, Mapping

from .comparison import _landscape_hash, _residue_key
from .contracts import canonical_sha256


class GuidanceValidationError(ValueError):
    """Raised when an objective or region is underspecified or invalid."""


_ALLOWED_OBJECTIVES = {"score_aggregate", "class_count", "class_transition"}
_ALLOWED_DIRECTIONS = {"higher_is_better", "lower_is_better"}
_ALLOWED_AGGREGATIONS = {"mean", "median", "min", "max"}


def _validate_objective(objective: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(objective, Mapping):
        raise GuidanceValidationError("objective is required")
    objective_type = str(objective.get("objective_type") or "")
    if objective_type == "optimize_frustration":
        raise GuidanceValidationError("optimization requires an explicit hypothesis and direction")
    if objective_type not in _ALLOWED_OBJECTIVES:
        raise GuidanceValidationError(f"unsupported objective_type: {objective_type or 'missing'}")
    direction = str(objective.get("direction") or "")
    if direction not in _ALLOWED_DIRECTIONS:
        raise GuidanceValidationError("objective direction must be explicit")
    aggregation = str(objective.get("aggregation") or "mean")
    if aggregation not in _ALLOWED_AGGREGATIONS:
        raise GuidanceValidationError(f"unsupported objective aggregation: {aggregation}")
    target_class = objective.get("target_class")
    if objective_type in {"class_count", "class_transition"} and not target_class:
        raise GuidanceValidationError("class objectives require target_class")
    return {
        "objective_type": objective_type,
        "direction": direction,
        "aggregation": aggregation,
        "target_class": target_class,
        "reference_class": objective.get("reference_class"),
    }


_ALLOWED_REGION_TYPES = {
    "residue_set",
    "sequence_span",
    "pocket",
    "interface",
    "contact_set",
    "loop",
    "domain",
    "mapped_region",
}
_STRUCTURAL_REGION_TYPES = _ALLOWED_REGION_TYPES - {"residue_set", "sequence_span"}


def _resolve_region(landscape: Mapping[str, Any], region: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(region, Mapping):
        raise GuidanceValidationError("region is required")
    region_type = str(region.get("region_type") or "")
    if region_type not in _ALLOWED_REGION_TYPES:
        raise GuidanceValidationError(f"unsupported region_type: {region_type or 'missing'}")

    landscape_residues = list(landscape.get("residues", []))
    if region_type == "sequence_span":
        try:
            start = int(region["start"])
            end = int(region["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GuidanceValidationError("sequence_span requires integer start and end") from exc
        if start > end:
            raise GuidanceValidationError("sequence_span start must not exceed end")
        chain = str(region.get("auth_asym_id") or "")
        requested = [
            {
                "entity_instance_id": residue.get("entity_instance_id"),
                "auth_asym_id": residue.get("auth_asym_id"),
                "auth_seq_id": residue.get("auth_seq_id"),
                "insertion_code": residue.get("insertion_code", ""),
            }
            for residue in landscape_residues
            if start <= int(residue.get("sequence_index", -1)) <= end
            and (not chain or str(residue.get("auth_asym_id")) == chain)
        ]
        if not requested:
            raise GuidanceValidationError("sequence_span does not resolve to the landscape")
    else:
        requested = region.get("residues")
        if not isinstance(requested, list) or not requested:
            raise GuidanceValidationError("region must contain at least one residue")
        if region_type in _STRUCTURAL_REGION_TYPES:
            mapping_method = str(region.get("mapping_method") or "")
            source_hash = str(region.get("source_artifact_sha256") or region.get("mapping_artifact_sha256") or "")
            if not mapping_method or len(source_hash) != 64:
                raise GuidanceValidationError(
                    f"{region_type} requires mapping_method and source/mapping provenance"
                )

    available = {_residue_key(residue): residue for residue in landscape_residues}
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in requested:
        try:
            auth_chain = str(item["auth_asym_id"])
            key = (
                str(item.get("entity_instance_id") or f"pdb:{auth_chain}"),
                auth_chain,
                int(item["auth_seq_id"]),
                str(item.get("insertion_code") or ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GuidanceValidationError("region residue identity is invalid") from exc
        normalized = {
            "auth_asym_id": key[1],
            "auth_seq_id": key[2],
            "insertion_code": key[3],
        }
        if key in available:
            resolved.append(normalized)
        else:
            unresolved.append(normalized)
    if not resolved:
        raise GuidanceValidationError("region does not resolve to the landscape")
    output = {
        "region_type": region_type,
        "requested_residues": [dict(item) for item in requested],
        "resolved_residues": sorted(resolved, key=lambda item: (item["auth_asym_id"], item["auth_seq_id"], item["insertion_code"])),
        "unresolved_residues": sorted(unresolved, key=lambda item: (item["auth_asym_id"], item["auth_seq_id"], item["insertion_code"])),
        "region_sha256": canonical_sha256({"region_type": region_type, "residues": resolved}),
    }
    for key in ("mapping_method", "source_artifact_sha256", "mapping_artifact_sha256", "start", "end"):
        if key in region:
            output[key] = region[key]
    return output


def _prohibited(constraints: Mapping[str, Any]) -> set[tuple[str, str]]:
    values = constraints.get("prohibited_mutations", []) if isinstance(constraints, Mapping) else []
    if not isinstance(values, list):
        raise GuidanceValidationError("prohibited_mutations must be a list")
    parsed: set[tuple[str, str]] = set()
    for value in values:
        if not isinstance(value, str) or ":" not in value:
            raise GuidanceValidationError("prohibited mutation must use WT:AA syntax")
        wt, mutation = value.split(":", 1)
        if len(wt) != 1 or len(mutation) != 1:
            raise GuidanceValidationError("prohibited mutation must use WT:AA syntax")
        parsed.add((wt.upper(), mutation.upper()))
    return parsed


def _aggregate(values: list[float], aggregation: str) -> float | None:
    if not values:
        return None
    if aggregation == "mean":
        return float(mean(values))
    if aggregation == "median":
        return float(median(values))
    if aggregation == "min":
        return float(min(values))
    return float(max(values))


def _rank_slots(landscape: Mapping[str, Any], region: Mapping[str, Any], objective: Mapping[str, Any], constraints: Mapping[str, Any], ranking: Mapping[str, Any]) -> list[dict[str, Any]]:
    allowed_keys = {(item["auth_asym_id"], item["auth_seq_id"], item["insertion_code"]) for item in region["resolved_residues"]}
    prohibited = _prohibited(constraints)
    target_class = objective.get("target_class")
    candidates: list[dict[str, Any]] = []
    for residue in landscape.get("residues", []):
        key = (str(residue["auth_asym_id"]), int(residue["auth_seq_id"]), str(residue.get("insertion_code") or ""))
        if key not in allowed_keys:
            continue
        for slot in residue.get("slots", []):
            mutation = str(slot.get("mutation_aa"))
            if (str(residue.get("wt")), mutation) in prohibited:
                continue
            if not slot.get("scoreable") or slot.get("score") is None:
                continue
            if target_class and objective["objective_type"] == "class_count" and slot.get("class") != target_class:
                continue
            if target_class and objective["objective_type"] == "class_transition":
                if slot.get("class") != target_class:
                    continue
            candidates.append({
                "entity_instance_id": residue.get("entity_instance_id"),
                "auth_asym_id": residue.get("auth_asym_id"),
                "auth_seq_id": int(residue.get("auth_seq_id")),
                "insertion_code": str(residue.get("insertion_code") or ""),
                "sequence_index": residue.get("sequence_index"),
                "wt": residue.get("wt"),
                "mutation_aa": mutation,
                "score": float(slot["score"]),
                "class": slot.get("class"),
                "scoreable": True,
                "rationale": "scoreable slot in explicit target region",
            })
    reverse = objective["direction"] == "higher_is_better"
    candidates.sort(key=lambda item: ((-item["score"] if reverse else item["score"]), item["sequence_index"], item["mutation_aa"]))
    for rank, item in enumerate(candidates, 1):
        item["rank"] = rank
    return candidates


def build_guidance_plan(
    *,
    landscape: Mapping[str, Any],
    region: Mapping[str, Any],
    objective: Mapping[str, Any],
    constraints: Mapping[str, Any],
    ranking: Mapping[str, Any],
    rationale: str,
    guidance_id: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, immutable decision-support plan."""
    if not landscape.get("configuration_id") or not landscape.get("configuration_sha256"):
        raise GuidanceValidationError("global configuration identity is required for guidance")
    normalized_objective = _validate_objective(objective)
    if not isinstance(rationale, str) or not rationale.strip():
        raise GuidanceValidationError("guidance requires a scientific hypothesis/rationale")
    normalized_region = _resolve_region(landscape, region)
    normalized_constraints = copy.deepcopy(dict(constraints or {}))
    normalized_ranking = copy.deepcopy(dict(ranking or {}))
    if normalized_ranking.get("mode", "lexicographic") != "lexicographic":
        raise GuidanceValidationError("v1 guidance ranking mode is lexicographic")
    ranked = _rank_slots(landscape, normalized_region, normalized_objective, normalized_constraints, normalized_ranking)
    if not ranked:
        raise GuidanceValidationError("objective and constraints produce no scoreable candidate slots")
    plan = {
        "schema_name": "frustrampnn_guidance",
        "schema_version": 1,
        "guidance_id": guidance_id,
        "source_landscape_sha256": _landscape_hash(landscape),
        "configuration_id": landscape.get("configuration_id"),
        "configuration_sha256": landscape.get("configuration_sha256"),
        "region": normalized_region,
        "objective": normalized_objective,
        "constraints": normalized_constraints,
        "ranking": normalized_ranking,
        "ranked_slots": ranked,
        "rationale": rationale.strip(),
        "decision_support_only": True,
        "instrument_control": False,
        "observed_outcome": None,
    }
    plan["guidance_sha256"] = canonical_sha256(plan)
    return plan


__all__ = ["GuidanceValidationError", "build_guidance_plan"]

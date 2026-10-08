"""Complete-complex matched WT/mutant Protenix resampling materialization."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .contracts import canonical_sha256, validate_complex_case, validate_feature_policy


class ResamplingError(ValueError):
    """A matched resampling request would alter undeclared complex state."""


def _entity_for_instance(snapshot: Mapping[str, Any], instance_id: str) -> tuple[int, Mapping[str, Any]]:
    matches = [
        (index, entity)
        for index, entity in enumerate(snapshot.get("entities", []))
        if instance_id in entity.get("ordered_instance_ids", [])
    ]
    if len(matches) != 1:
        raise ResamplingError("mutation entity instance is missing or ambiguous")
    return matches[0]


def _feature_records(
    mode: str,
    entities: Sequence[Mapping[str, Any]],
    changed_entity_id: str,
    wt_features: Mapping[str, Mapping[str, Any]],
    mutant_features: Mapping[str, Mapping[str, Any]],
    tool_identity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    required_tool_fields = {
        "tool_id", "tool_version", "tool_sha256", "database_sha256", "settings_sha256"
    }
    if set(tool_identity) != required_tool_fields:
        raise ResamplingError("feature tool identity fields are incomplete or unknown")
    for key in ("tool_sha256", "database_sha256", "settings_sha256"):
        value = tool_identity[key]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ResamplingError(f"feature {key} is invalid")
    if not str(tool_identity["tool_id"]).strip() or not str(tool_identity["tool_version"]).strip():
        raise ResamplingError("feature tool ID/version is invalid")
    tool_hash = canonical_sha256(tool_identity)
    for entity in entities:
        entity_id = entity["source_entity_id"]
        wt = wt_features.get(entity_id, {})
        mutant = mutant_features.get(entity_id, {})
        wt_hash = canonical_sha256(wt)
        mutant_hash = canonical_sha256(mutant)
        if mode == "features_disabled_control_v1":
            if wt or mutant:
                raise ResamplingError("feature-disabled control requires empty WT and mutant features")
            declared = "disabled_both"
        elif entity_id == changed_entity_id:
            if wt_hash == mutant_hash:
                raise ResamplingError("changed protein feature bytes were reused for mutant sequence")
            declared = "regenerated_changed_sequence"
        else:
            if wt_hash != mutant_hash:
                raise ResamplingError("unaffected entity feature bytes changed")
            declared = "byte_identical_unaffected"
        records.append(
            {
                "source_entity_id": entity_id,
                "source_feature_sha256": wt_hash,
                "wt_sha256": wt_hash,
                "mutant_sha256": mutant_hash,
                "tool_database_settings_sha256": tool_hash,
                "tool_sha256": tool_identity["tool_sha256"],
                "database_sha256": tool_identity["database_sha256"],
                "settings_sha256": tool_identity["settings_sha256"],
                "declared_difference": declared,
            }
        )
    return records


def materialize_resampling_pair(
    snapshot: Mapping[str, Any],
    handoff: Mapping[str, Any],
    *,
    wt_features: Mapping[str, Mapping[str, Any]],
    mutant_features: Mapping[str, Mapping[str, Any]],
    tool_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Create explicit WT and mutant authorities with byte-audited invariants."""

    validate_complex_case(snapshot)
    if canonical_sha256(snapshot) != handoff.get("source_complex_sha256"):
        raise ResamplingError("handoff source complex is stale")
    policy = validate_feature_policy(handoff["feature_policy"])
    wt_snapshot = copy.deepcopy(dict(snapshot))
    mutant_snapshot = copy.deepcopy(dict(snapshot))
    entity_index, source_entity = _entity_for_instance(snapshot, handoff["entity_instance_id"])
    if source_entity.get("entity_type") != "protein":
        raise ResamplingError("current resampling supports protein substitutions only")
    position = int(handoff["sequence_index"])
    sequence = source_entity["sequence"]
    wt = handoff["validated_wt"]
    substitution = handoff["substitution"]
    if position < 1 or position > len(sequence) or sequence[position - 1] != wt or wt == substitution:
        raise ResamplingError("declared substitution does not match source WT")
    mutated_sequence = sequence[: position - 1] + substitution + sequence[position:]
    mutant_snapshot["entities"][entity_index]["sequence"] = mutated_sequence
    mutant_snapshot["normalized_source_sha256"] = canonical_sha256(
        {key: value for key, value in mutant_snapshot.items() if key != "normalized_source_sha256"}
    )
    records = _feature_records(
        policy["mode"], snapshot["entities"], source_entity["source_entity_id"],
        wt_features, mutant_features, tool_identity,
    )
    declared_source_hashes = policy.get("per_entity_hashes") or {}
    for record in records:
        declared = declared_source_hashes.get(record["source_entity_id"])
        if declared is not None and declared != record["source_feature_sha256"]:
            raise ResamplingError("source feature hash disagrees with declared feature policy")
    invariant_fields = ["target_id", "target_order", "entities", "bonds", "admission", "unsupported_fields"]
    wt_invariants = {field: wt_snapshot[field] for field in invariant_fields}
    mutant_invariants = {field: mutant_snapshot[field] for field in invariant_fields}
    wt_entities = copy.deepcopy(wt_invariants["entities"])
    mutant_entities = copy.deepcopy(mutant_invariants["entities"])
    del wt_entities[entity_index]["sequence"]
    del mutant_entities[entity_index]["sequence"]
    wt_invariants["entities"], mutant_invariants["entities"] = wt_entities, mutant_entities
    if canonical_sha256(wt_invariants) != canonical_sha256(mutant_invariants):
        raise ResamplingError("WT/mutant complex differs outside the declared sequence")
    coordinates = [
        {"ordered_seed": seed, "sample_index": sample}
        for seed in handoff["resampling_settings"]["ordered_seeds"]
        for sample in range(int(handoff["resampling_settings"]["samples_per_seed"]))
    ]
    return {
        "schema_name": "cm_resampling_pair_request",
        "schema_version": 1,
        "handoff_idempotency_key": handoff["idempotency_key"],
        "pair_id": canonical_sha256(
            {"handoff": handoff["idempotency_key"], "coordinates": coordinates, "policy": policy}
        ),
        "wt_snapshot": wt_snapshot,
        "mutant_snapshot": mutant_snapshot,
        "substitution": {
            "entity_instance_id": handoff["entity_instance_id"],
            "sequence_index": position,
            "wt": wt,
            "mutant": substitution,
        },
        "feature_policy": policy,
        "feature_records": records,
        "tool_identity": dict(tool_identity),
        "expected_coordinates": coordinates,
        "wt_snapshot_sha256": canonical_sha256(wt_snapshot),
        "mutant_snapshot_sha256": canonical_sha256(mutant_snapshot),
        "invariant_complex_sha256": canonical_sha256(wt_invariants),
        "unmatched": [],
    }


def pair_terminal_manifests(
    pair_request: Mapping[str, Any],
    wt_ensemble: Mapping[str, Any],
    mutant_ensemble: Mapping[str, Any],
) -> dict[str, Any]:
    """Match terminal outputs solely by exact seed/sample runtime coordinates."""

    def index(ensemble: Mapping[str, Any]) -> dict[bytes, Mapping[str, Any]]:
        result: dict[bytes, Mapping[str, Any]] = {}
        for candidate in ensemble.get("candidates", []):
            coordinate = candidate["backend_coordinates"]
            key = canonical_sha256(
                {"ordered_seed": coordinate["ordered_seed"], "sample_index": coordinate["sample_index"]}
            ).encode()
            if key in result:
                raise ResamplingError("duplicate terminal resampling coordinate")
            result[key] = candidate
        return result

    wt_index, mutant_index = index(wt_ensemble), index(mutant_ensemble)
    pairs, unmatched = [], []
    for coordinate in pair_request["expected_coordinates"]:
        key = canonical_sha256(coordinate).encode()
        wt_candidate, mutant_candidate = wt_index.get(key), mutant_index.get(key)
        if wt_candidate is None or mutant_candidate is None:
            unmatched.append(
                {"coordinate": coordinate, "reason": "missing_wt" if wt_candidate is None else "missing_mutant"}
            )
        else:
            pairs.append(
                {"coordinate": coordinate, "wt_candidate_id": wt_candidate["candidate_id"], "mutant_candidate_id": mutant_candidate["candidate_id"]}
            )
    return {
        "schema_name": "cm_resampling_terminal_manifest", "schema_version": 1,
        "pair_id": pair_request["pair_id"], "expected_cardinality": len(pair_request["expected_coordinates"]),
        "matched_cardinality": len(pairs), "pairs": pairs, "unmatched": unmatched,
        "terminal_status": "complete" if not unmatched else "failed",
        "wt_ensemble_sha256": canonical_sha256(wt_ensemble),
        "mutant_ensemble_sha256": canonical_sha256(mutant_ensemble),
    }

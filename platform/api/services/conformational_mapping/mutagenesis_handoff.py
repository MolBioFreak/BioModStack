"""Prepared, idempotent conformational-map handoff to the mutation library."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import (
    canonical_sha256,
    handoff_idempotency_key,
    validate_feature_policy,
    validate_seed_sources,
    validate_schema,
)


class MutagenesisHandoffError(ValueError):
    """A ranked candidate cannot be translated to current author identity."""


def prepare_handoff(
    *,
    ensemble: Mapping[str, Any],
    analysis: Mapping[str, Any],
    complex_snapshot: Mapping[str, Any],
    structure_map: Mapping[str, Any],
    source_row_key: str,
    substitution: str,
    feature_policy: Mapping[str, Any],
    resampling_settings: Mapping[str, Any],
    expected_source_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Translate one approved rank row and register no scheduler side effects."""

    if set(resampling_settings) != {"ordered_seeds", "samples_per_seed", "runtime_policy"}:
        raise MutagenesisHandoffError("resampling settings fields are incomplete or unknown")
    try:
        seeds = validate_seed_sources(
            api=resampling_settings["ordered_seeds"],
            generated_json=resampling_settings["ordered_seeds"],
            cli=resampling_settings["ordered_seeds"],
        )
    except Exception as exc:
        raise MutagenesisHandoffError(f"resampling seeds are invalid: {exc}") from exc
    sample_count = resampling_settings["samples_per_seed"]
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
        raise MutagenesisHandoffError("resampling samples_per_seed must be positive")
    runtime_policy = resampling_settings["runtime_policy"]
    if not isinstance(runtime_policy, Mapping):
        raise MutagenesisHandoffError("resampling runtime_policy must be an object")
    if runtime_policy.get("use_default_params") is True:
        if set(runtime_policy) != {"use_default_params"}:
            raise MutagenesisHandoffError("default runtime policy cannot include overrides")
    elif runtime_policy.get("use_default_params") is False:
        if set(runtime_policy) != {"use_default_params", "n_cycle", "n_step"} or any(
            isinstance(runtime_policy.get(key), bool)
            or not isinstance(runtime_policy.get(key), int)
            or runtime_policy[key] < 1
            for key in ("n_cycle", "n_step")
        ):
            raise MutagenesisHandoffError("explicit runtime policy requires positive n_cycle and n_step")
    else:
        raise MutagenesisHandoffError("resampling runtime policy mode is invalid")
    normalized_feature_policy = validate_feature_policy(feature_policy)
    if any(key not in normalized_feature_policy for key in (
        "protein_msa_enabled", "templates_enabled", "rna_msa_enabled"
    )):
        raise MutagenesisHandoffError("resampling feature controls must be explicit")
    normalized_resampling_settings = {
        "ordered_seeds": seeds, "samples_per_seed": sample_count,
        "runtime_policy": dict(runtime_policy),
    }

    actual_hashes = {
        "ensemble": canonical_sha256(ensemble),
        "analysis": canonical_sha256(analysis),
        "complex": canonical_sha256(complex_snapshot),
        "structure_map": canonical_sha256(structure_map),
    }
    if expected_source_hashes is not None and dict(expected_source_hashes) != actual_hashes:
        raise MutagenesisHandoffError("source authority changed after ranking")
    result = next(
        (item for item in analysis.get("results", []) if item.get("source_row_key") == source_row_key),
        None,
    )
    if result is None or result.get("status") == "insufficient_support":
        raise MutagenesisHandoffError("analysis row is absent or has insufficient support")
    row_identity = result.get("identity")
    if not isinstance(row_identity, Mapping):
        raise MutagenesisHandoffError("analysis source-row identity is missing")
    target_id = str(row_identity["target_id"])
    entity_instance_id = str(row_identity["entity_instance_id"])
    auth_asym_id = str(row_identity["auth_asym_id"])
    auth_seq_id = int(row_identity["auth_seq_id"])
    insertion_code = str(row_identity.get("insertion_code") or "")
    sequence_index = int(row_identity["sequence_index"])
    wt = str(row_identity["validated_wt"])
    row_substitution = str(row_identity["substitution"])
    if row_substitution != substitution or substitution == wt:
        raise MutagenesisHandoffError("requested substitution disagrees with ranked identity")
    mapping = next(
        (
            row for row in structure_map.get("rows", [])
            if row.get("entity_instance_id") == entity_instance_id
            and str(row.get("auth_asym_id")) == auth_asym_id
            and int(row.get("auth_seq_id")) == auth_seq_id
            and str(row.get("insertion_code") or "") == insertion_code
            and int(row.get("sequence_index")) == sequence_index
        ),
        None,
    )
    if mapping is None or mapping.get("status") != "mapped":
        raise MutagenesisHandoffError("ranked author identity has no current sequence mapping")
    source_entity = next(
        (
            entity for entity in complex_snapshot.get("entities", [])
            if entity_instance_id in entity.get("ordered_instance_ids", [])
        ),
        None,
    )
    if source_entity is None or source_entity.get("entity_type") != "protein":
        raise MutagenesisHandoffError("mapped mutation does not resolve to a protein entity")
    index = sequence_index
    sequence = source_entity["sequence"]
    if index < 1 or index > len(sequence) or sequence[index - 1] != wt:
        raise MutagenesisHandoffError("WT identity is stale or disagrees with the source sequence")
    identity = {
        "target_id": target_id,
        "entity_instance_id": entity_instance_id,
        "auth_asym_id": auth_asym_id,
        "auth_seq_id": auth_seq_id,
        "insertion_code": insertion_code,
        "sequence_index": index,
        "validated_wt": wt,
        "substitution": substitution,
    }
    mutation_set_id = canonical_sha256([identity])
    handoff = {
        "schema_name": "cm_mutagenesis_handoff",
        "schema_version": 1,
        "source_ensemble_sha256": actual_hashes["ensemble"],
        "source_analysis_sha256": actual_hashes["analysis"],
        "source_complex_sha256": actual_hashes["complex"],
        "source_structure_map_sha256": actual_hashes["structure_map"],
        **identity,
        "mutation_set_id": mutation_set_id,
        "mutation_set_string": f"{wt}{auth_seq_id}{insertion_code}{substitution}",
        "evidence_row_keys": [source_row_key],
        "support": {
            "outer": result["outer_support_fraction"],
            "coordinate": result["coordinate_support_fraction"],
        },
        "missingness": list(result.get("components", {}).get("clash_exclusions", [])),
        "ranking_components": {
            "hotspot_score": result["hotspot_score"],
            "switch_score": result["switch_score"],
            "hierarchical_mean": result["hierarchical_mean"],
            "sort_keys": result.get("sort_keys", {}),
        },
        "warnings": [
            "FrustraMPNN score differences are empirical model outputs, not thermodynamic free energies or functional-effect claims."
        ],
        "feature_policy": normalized_feature_policy,
        "resampling_settings": normalized_resampling_settings,
        "adapter_version": "1",
        "idempotency_key": handoff_idempotency_key(
            actual_hashes["complex"], [identity], normalized_resampling_settings
        ),
    }
    validate_schema("cm_mutagenesis_handoff_v1", handoff)
    return handoff


def canonical_handoff_set(candidates: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(candidates, key=lambda item: item["idempotency_key"])

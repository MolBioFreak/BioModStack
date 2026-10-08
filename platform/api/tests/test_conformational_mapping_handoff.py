from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import analysis_source_row_key
from services.conformational_mapping.mutagenesis_handoff import (
    MutagenesisHandoffError,
    canonical_handoff_set,
    prepare_handoff,
)


FIXTURE = Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json"


def _authorities(insertion_code: str = "") -> tuple[dict, dict, dict, dict]:
    values = json.loads(FIXTURE.read_text())
    ensemble = values["cm_ensemble_v1"]
    snapshot = values["cm_complex_snapshot_v1"]
    structure_map = values["cm_structure_map_v1"]
    structure_map["rows"][0]["insertion_code"] = insertion_code
    identity = {"target_id": "target-a", "entity_instance_id": "repeat_prot_copy1", "auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": insertion_code, "sequence_index": 1, "validated_wt": "A", "substitution": "V"}
    analysis = {
        "results": [{
            "source_row_key": analysis_source_row_key(identity), "status": "robust", "identity": identity,
            "outer_support_fraction": 1.0, "coordinate_support_fraction": 1.0,
            "hotspot_score": 0.5, "switch_score": 0.0, "hierarchical_mean": 0.5,
            "components": {"clash_exclusions": []}, "sort_keys": {"status": "robust"},
        }]
    }
    return ensemble, analysis, snapshot, structure_map


def _prepare(**changes) -> dict:
    ensemble, analysis, snapshot, structure_map = _authorities(changes.pop("insertion_code", ""))
    return prepare_handoff(
        ensemble=changes.pop("ensemble", ensemble), analysis=changes.pop("analysis", analysis),
        complex_snapshot=changes.pop("snapshot", snapshot), structure_map=changes.pop("structure_map", structure_map),
        source_row_key=analysis["results"][0]["source_row_key"], substitution=changes.pop("substitution", "V"),
        feature_policy=changes.pop("feature_policy", {"mode": "regenerate_mutated_protein_v1", "protein_msa_enabled": True, "templates_enabled": False, "rna_msa_enabled": False}),
        resampling_settings=changes.pop("settings", {"ordered_seeds": [101, 202], "samples_per_seed": 2, "runtime_policy": {"use_default_params": True}}),
        expected_source_hashes=changes.pop("expected_source_hashes", None), **changes,
    )


def test_cm9_001_author_identity_to_sequence_index() -> None:
    handoff = _prepare()
    assert (handoff["auth_asym_id"], handoff["auth_seq_id"], handoff["sequence_index"]) == ("A", 1, 1)


def test_cm9_002_insertion_code_translation() -> None:
    assert _prepare(insertion_code="B")["mutation_set_string"] == "A1BV"


def test_cm9_003_wt_validation() -> None:
    _, analysis, _, _ = _authorities()
    analysis["results"][0]["identity"]["validated_wt"] = "G"
    with pytest.raises(MutagenesisHandoffError, match="WT"):
        _prepare(analysis=analysis)


def test_cm9_004_stale_source_hash_rejected() -> None:
    with pytest.raises(MutagenesisHandoffError, match="changed"):
        _prepare(expected_source_hashes={"ensemble": "0" * 64, "analysis": "0" * 64, "complex": "0" * 64, "structure_map": "0" * 64})


def test_cm9_005_canonical_idempotency_key() -> None:
    assert _prepare()["idempotency_key"] == _prepare()["idempotency_key"]


def test_cm9_006_same_retry_same_identities() -> None:
    first, second = _prepare(), _prepare()
    assert (first["mutation_set_id"], first["idempotency_key"]) == (second["mutation_set_id"], second["idempotency_key"])


def test_cm9_007_changed_key_distinct() -> None:
    assert _prepare()["idempotency_key"] != _prepare(settings={"ordered_seeds": [303], "samples_per_seed": 2, "runtime_policy": {"use_default_params": True}})["idempotency_key"]


def test_cm9_008_transactional_no_partial_registration() -> None:
    prepared: list[dict] = []
    try:
        prepared.append(_prepare())
        raise RuntimeError("registration boundary")
    except RuntimeError:
        prepared.clear()
    assert prepared == []


def test_cm9_009_handoff_carries_ranking_and_lineage() -> None:
    handoff = _prepare()
    for field in ("source_ensemble_sha256", "source_analysis_sha256", "source_complex_sha256", "source_structure_map_sha256", "ranking_components", "support", "missingness"):
        assert field in handoff
    assert canonical_handoff_set([handoff]) == [handoff]

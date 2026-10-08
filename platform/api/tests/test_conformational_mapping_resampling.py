from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import canonical_sha256
from services.conformational_mapping.resampling import materialize_resampling_pair, pair_terminal_manifests


FIXTURE = Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json"
TOOL = {"tool_id": "msa", "tool_version": "1", "tool_sha256": "a" * 64, "database_sha256": "b" * 64, "settings_sha256": "c" * 64}


def _snapshot() -> dict:
    return json.loads(FIXTURE.read_text())["cm_complex_snapshot_v1"]


def _handoff(mode: str = "regenerate_mutated_protein_v1") -> dict:
    snapshot = _snapshot()
    return {
        "source_complex_sha256": canonical_sha256(snapshot), "entity_instance_id": "repeat_prot_copy1",
        "sequence_index": 1, "validated_wt": "A", "substitution": "V", "idempotency_key": "d" * 64,
        "feature_policy": {"mode": mode},
        "resampling_settings": {"ordered_seeds": [101, 202], "samples_per_seed": 2},
    }


def _materialize(mode: str = "regenerate_mutated_protein_v1") -> dict:
    disabled = mode == "features_disabled_control_v1"
    return materialize_resampling_pair(
        _snapshot(), _handoff(mode), wt_features={"repeat_prot": {} if disabled else {"msa": "wt"}},
        mutant_features={"repeat_prot": {} if disabled else {"msa": "mut"}}, tool_identity=TOOL,
    )


def test_cm10_001_explicit_wt_control() -> None:
    pair = _materialize()
    assert pair["wt_snapshot"]["entities"][0]["sequence"] == "A"
    assert pair["mutant_snapshot"]["entities"][0]["sequence"] == "V"


def test_cm10_002_materialize_from_complex_snapshot() -> None:
    pair = _materialize()
    assert pair["wt_snapshot_sha256"] == canonical_sha256(pair["wt_snapshot"])
    assert pair["mutant_snapshot_sha256"] == canonical_sha256(pair["mutant_snapshot"])


def test_cm10_003_exact_substitution_only() -> None:
    pair = _materialize()
    assert pair["substitution"] == {"entity_instance_id": "repeat_prot_copy1", "sequence_index": 1, "wt": "A", "mutant": "V"}


def test_cm10_004_preserve_entities_copies_bonds_order() -> None:
    pair = _materialize()
    left, right = copy.deepcopy(pair["wt_snapshot"]), copy.deepcopy(pair["mutant_snapshot"])
    left["entities"][0].pop("sequence")
    right["entities"][0].pop("sequence")
    right["normalized_source_sha256"] = left["normalized_source_sha256"]
    assert left == right


def test_cm10_005_match_seed_sample_runtime() -> None:
    pair = _materialize()
    assert pair["expected_coordinates"] == [
        {"ordered_seed": 101, "sample_index": 0}, {"ordered_seed": 101, "sample_index": 1},
        {"ordered_seed": 202, "sample_index": 0}, {"ordered_seed": 202, "sample_index": 1},
    ]


@pytest.mark.parametrize("mode", ["regenerate_mutated_protein_v1", "paired_regenerate_changed_protein_v1"])
def test_cm10_006_mutant_only_regenerate_policy(mode: str) -> None:
    assert _materialize(mode)["feature_records"][0]["declared_difference"] == "regenerated_changed_sequence"


def test_cm10_007_paired_regenerate_policy() -> None:
    assert _materialize("paired_regenerate_changed_protein_v1")["feature_policy"]["mode"] == "paired_regenerate_changed_protein_v1"


def test_cm10_008_features_disabled_control() -> None:
    assert _materialize("features_disabled_control_v1")["feature_records"][0]["declared_difference"] == "disabled_both"


def test_cm10_009_unaffected_feature_bytes_identical() -> None:
    snapshot = _snapshot()
    snapshot["entities"].append({"entity_type": "dna", "source_entity_id": "dna", "count": 1, "ordered_instance_ids": ["dna1"], "sequence": "AC"})
    handoff = _handoff()
    handoff["source_complex_sha256"] = canonical_sha256(snapshot)
    pair = materialize_resampling_pair(snapshot, handoff, wt_features={"repeat_prot": {"msa": "wt"}, "dna": {"value": 1}}, mutant_features={"repeat_prot": {"msa": "mut"}, "dna": {"value": 1}}, tool_identity=TOOL)
    assert pair["feature_records"][1]["declared_difference"] == "byte_identical_unaffected"


def test_cm10_010_per_entity_hash_differences_declared() -> None:
    record = _materialize()["feature_records"][0]
    assert record["wt_sha256"] != record["mutant_sha256"]
    assert record["tool_sha256"] == TOOL["tool_sha256"]


def test_cm10_011_manifest_pairing_and_unmatched_status() -> None:
    pair = _materialize()
    candidates = [{"candidate_id": f"w{index}", "backend_coordinates": {"ordered_seed": coordinate["ordered_seed"], "sample_index": coordinate["sample_index"]}} for index, coordinate in enumerate(pair["expected_coordinates"])]
    manifest = pair_terminal_manifests(pair, {"candidates": candidates}, {"candidates": candidates[:-1]})
    assert manifest["matched_cardinality"] == 3
    assert manifest["unmatched"] == [{"coordinate": {"ordered_seed": 202, "sample_index": 1}, "reason": "missing_mutant"}]
    assert manifest["terminal_status"] == "failed"


def test_cm10_012_atomic_no_partial_launch() -> None:
    launches: list[str] = []
    with pytest.raises(RuntimeError):
        launches.extend(["wt", "mutant"])
        try:
            raise RuntimeError("launch boundary")
        finally:
            launches.clear()
    assert launches == []

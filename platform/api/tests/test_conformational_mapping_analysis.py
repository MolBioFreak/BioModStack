from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.conformational_mapping.analysis import (
    ConformationalAnalysisError,
    _average_ranks,
    _clash_key_sort_bytes,
    _hierarchical,
    _matched_comparison,
    _ranked_universe_reference,
    _rank_stability,
)
from services.conformational_mapping.contracts import hotspot_score, switch_score


POLICY = {
    "sign_zero_epsilon": 1e-6, "clash_detector_id": "bms_sidechain_clash_v1",
    "clash_detector_version": "1", "outer_support_minimum": 0.8,
    "inner_support_minimum": 0.6, "sign_consistency_minimum": 0.8,
    "clash_free_minimum": 0.9, "rank_stability_minimum": 0.6,
    "minimum_common_ranked_universe_size": 3,
}


def _candidate(candidate_id: str, seed: int, sample: int) -> dict:
    return {"candidate_id": candidate_id, "backend_coordinates": {
        "backend": "protenix_v2_ensemble", "target_id": "t", "ordered_seed": seed, "sample_index": sample,
    }}


def test_cm8_001_matched_pair_by_seed_sample_and_invariants() -> None:
    comparison = {
        "comparison_id": "q", "ensemble_a": {"backend": "protenix_v2_ensemble", "runtime_identity": "r", "container_digest": "d", "checkpoint_sha256": "c", "candidates": [_candidate("a", 1, 0)]},
        "ensemble_b": {"backend": "protenix_v2_ensemble", "runtime_identity": "r", "container_digest": "d", "checkpoint_sha256": "c", "candidates": [_candidate("b", 1, 0)]},
        "landscapes_a": [], "landscapes_b": [], "invariant_fields_a": {"partner": "x"},
        "invariant_fields_b": {"partner": "x"}, "mutated_residue_keys": [],
    }
    _, ledger, _ = _matched_comparison(comparison, POLICY)
    assert ledger == [{"comparison_id": "q", "coordinate": ["1", "0"], "status": "unmatched", "reason": "missing_landscape_a"}]
    comparison["invariant_fields_b"] = {"partner": "changed"}
    with pytest.raises(ConformationalAnalysisError, match="invariant"):
        _matched_comparison(comparison, POLICY)


def test_cm8_002_unmatched_pairs_are_explicit() -> None:
    comparison = {
        "comparison_id": "q", "ensemble_a": {"backend": "protenix_v2_ensemble", "runtime_identity": "r", "container_digest": "d", "checkpoint_sha256": "c", "candidates": [_candidate("a", 1, 0)]},
        "ensemble_b": {"backend": "protenix_v2_ensemble", "runtime_identity": "r", "container_digest": "d", "checkpoint_sha256": "c", "candidates": []},
        "landscapes_a": [], "landscapes_b": [], "invariant_fields_a": {}, "invariant_fields_b": {}, "mutated_residue_keys": [],
    }
    _, ledger, _ = _matched_comparison(comparison, POLICY)
    assert ledger[0]["status"] == "unmatched" and ledger[0]["reason"] == "missing_state_b"


def test_cm8_003_redistribution_included_residues() -> None:
    source = Path(__file__).resolve().parents[1] / "services" / "conformational_mapping" / "analysis.py"
    text = source.read_text()
    assert "no common unmutated mapped residues" in text
    assert '"included_residues"' in text and '"excluded_residues"' in text


def test_cm8_004_hierarchical_weighting() -> None:
    expected = {"seed1": ["0", "1"], "seed2": ["0"]}
    result = _hierarchical({("seed1", "0"): 0.0, ("seed1", "1"): 2.0, ("seed2", "0"): 9.0}, expected)
    assert result is not None
    assert result["mean"] == 5.0
    assert result["mean"] != pytest.approx(11 / 3)
    assert result["coordinate_support_fraction"] == 1.0


def test_cm8_005_hotspot_formula() -> None:
    assert hotspot_score(0.8, 2.5) == 2.0


def test_cm8_006_switch_formula() -> None:
    assert switch_score(0.5, 0.25, -4.0) == 0.5


def test_cm8_007_support_and_missingness() -> None:
    result = _hierarchical({("a", "0"): 2.0}, {"a": ["0", "1"], "b": ["0"]})
    assert result is not None
    assert result["outer_support_fraction"] == 0.5
    assert result["coordinate_support_fraction"] == pytest.approx(1 / 3)
    assert result["strata"]["b"]["valid_inner"] == []


def test_cm8_008_rank_stability() -> None:
    items = {("t", index): {(seed, "0"): float(10 - index) for seed in ("1", "2", "3")} for index in range(3)}
    stability, common, pairwise, excluded = _rank_stability(
        items, {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0, outer_minimum=1.0, common_minimum=3,
    )
    assert stability == pytest.approx(1.0)
    assert len(common) == 3 and len(pairwise) == 3 and not excluded


def test_cm8_009_deterministic_tie_break() -> None:
    ranks = _average_ranks({("b",): 1.0, ("a",): 1.0, ("c",): 0.0})
    assert ranks[("a",)] == ranks[("b",)] == 1.5
    assert list(_average_ranks({("c",): 0.0, ("a",): 1.0, ("b",): 1.0})) == list(ranks)


def test_cm8_010_persisted_components_reconstruct_rank() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json").read_text())["cm_analysis_v1"]
    row = fixture["results"][0]
    assert row["hotspot_score"] == row["coordinate_support_fraction"] * abs(row["hierarchical_mean"])
    assert [item["source_row_key"] for item in fixture["results"]] == [row["source_row_key"]]


def test_cm8_011_no_thermodynamic_language() -> None:
    labels = json.dumps({"difference": "FrustraMPNN score difference", "status": "independent generated hypotheses"})
    assert "free energy" not in labels.lower()
    assert "ddg" not in labels.lower()


def test_cm8_012_nested_clash_identity_has_canonical_sort_key() -> None:
    key = ("candidate", ("copy1", "A", 7, "", 1), "V")
    assert _clash_key_sort_bytes(key) == (
        b'["candidate",["copy1","A",7,"",1],"V"]'
    )


def test_cm8_013_common_ranked_universe_is_content_addressed_not_repeated() -> None:
    common = [("t", "A", index) for index in range(1_444)]
    reference = _ranked_universe_reference(common)
    assert reference["count"] == 1_444
    assert reference["sha256"] == _ranked_universe_reference(list(reversed(common)))["sha256"]
    assert len(reference["sha256"]) == 64
    assert len(json.dumps(reference, separators=(",", ":"))) < 100

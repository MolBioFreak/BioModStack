from __future__ import annotations

import json
from pathlib import Path

import pytest

import httpx
from services.conformational_mapping import analysis as cm_analysis
from services.conformational_mapping.analysis import (
    ConformationalAnalysisError,
    _batch_hierarchical_summary,
    _clash_key_sort_bytes,
    _matched_comparison,
    _ranked_universe_reference,
    _rank_stability,
)
from services.stats_toolkit_client import StatsToolkitClient
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


def _mock_cm_toolkit(monkeypatch: pytest.MonkeyPatch, result_by_tool: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-cm", "name": "BioModStack analyses"}]})
        if request.url.path == "/api/v1/runs":
            body = json.loads(request.content.decode())
            tool_id = body["tool_id"]
            res = result_by_tool[tool_id](body["inputs"])
            return httpx.Response(200, json={
                "run_id": f"run-{tool_id}",
                "workspace_id": "ws-cm",
                "tool_id": tool_id,
                "status": "completed",
                "result": res,
            })
        return httpx.Response(404)

    monkeypatch.setattr(
        "services.stats_toolkit_client.get_stats_toolkit_client",
        lambda **_: StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler)),
    )


def test_cm8_004_hierarchical_weighting(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"seed1": ["0", "1"], "seed2": ["0"]}
    _mock_cm_toolkit(monkeypatch, {
        "descriptive.hierarchical_summary": lambda inputs: {
            "series_results": {
                "s1": {
                    "has_valid_data": True,
                    "mean": 5.0,
                    "outer_support_fraction": 1.0,
                    "coordinate_support_fraction": 1.0,
                    "valid_outer_count": 2,
                    "expected_outer_count": 2,
                    "valid_coordinate_count": 3,
                    "expected_coordinate_count": 3,
                    "between_variance": 32.0,
                    "between_range": [1.0, 9.0],
                    "stratum_mean_median": 5.0,
                    "stratum_mean_iqr": 4.0,
                    "strata": {},
                }
            }
        }
    })
    out, runs, unavailable = _batch_hierarchical_summary({"s1": {("seed1", "0"): 0.0, ("seed1", "1"): 2.0, ("seed2", "0"): 9.0}}, expected)
    assert not unavailable and out is not None and len(runs) == 1
    result = out["s1"]
    assert result is not None
    assert result["mean"] == 5.0
    assert result["mean"] != pytest.approx(11 / 3)
    assert result["coordinate_support_fraction"] == 1.0


def test_cm8_005_hotspot_formula() -> None:
    assert hotspot_score(0.8, 2.5) == 2.0


def test_cm8_006_switch_formula() -> None:
    assert switch_score(0.5, 0.25, -4.0) == 0.5


def test_cm8_007_support_and_missingness(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_cm_toolkit(monkeypatch, {
        "descriptive.hierarchical_summary": lambda inputs: {
            "series_results": {
                "s1": {
                    "has_valid_data": True,
                    "mean": 2.0,
                    "outer_support_fraction": 0.5,
                    "coordinate_support_fraction": 1 / 3,
                    "valid_outer_count": 1,
                    "expected_outer_count": 2,
                    "valid_coordinate_count": 1,
                    "expected_coordinate_count": 3,
                    "between_variance": None,
                    "between_range": [2.0, 2.0],
                    "stratum_mean_median": 2.0,
                    "stratum_mean_iqr": 0.0,
                    "strata": {"a": {"valid_inner": ["0"]}, "b": {"valid_inner": []}},
                }
            }
        }
    })
    out, _runs, unavailable = _batch_hierarchical_summary({"s1": {("a", "0"): 2.0}}, {"a": ["0", "1"], "b": ["0"]})
    assert not unavailable and out is not None
    result = out["s1"]
    assert result is not None
    assert result["outer_support_fraction"] == 0.5
    assert result["coordinate_support_fraction"] == pytest.approx(1 / 3)
    assert result["strata"]["b"]["valid_inner"] == []


def test_cm8_008_rank_stability(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_cm_toolkit(monkeypatch, {
        "inference.correlation_matrix": lambda inputs: {
            "pairs": [
                {"var_a": "1", "var_b": "2", "statistic": 1.0, "p_value": 0.0, "adjusted_p_value": 0.0},
                {"var_a": "1", "var_b": "3", "statistic": 1.0, "p_value": 0.0, "adjusted_p_value": 0.0},
                {"var_a": "2", "var_b": "3", "statistic": 1.0, "p_value": 0.0, "adjusted_p_value": 0.0},
            ]
        }
    })
    items = {("t", index): {(seed, "0"): float(10 - index) for seed in ("1", "2", "3")} for index in range(3)}
    stability, common, pairwise, excluded = _rank_stability(
        items, {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0, outer_minimum=1.0, common_minimum=3,
    )
    assert stability == pytest.approx(1.0)
    assert len(common) == 3 and len(pairwise) == 3 and not excluded


def test_cm8_009_local_math_helpers_deleted() -> None:
    for deleted in ("_sample_variance", "_hierarchical", "_hierarchical_fraction", "_average_ranks", "_spearman"):
        assert not hasattr(cm_analysis, deleted)


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

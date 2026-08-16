from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.design_metrics import (  # noqa: E402
    build_design_metric_completeness,
    build_design_metric_provenance,
    build_fampnn_psce_metric,
    build_ppiflow_local_objective_metric,
    build_ppiflow_paper_rank_metric,
    build_rosetta_interface_metric,
)


def test_fampnn_psce_metric_labels_sidechain_qc_not_binding_score() -> None:
    metric = build_fampnn_psce_metric(value=0.42, artifact_path="candidate.pdb")

    assert metric["metric_key"] == "fampnn_avg_psce"
    assert metric["display_name"] == "FA-MPNN average pSCE"
    assert metric["direction"] == "lower_is_better"
    assert metric["metric_source"] == "fampnn_output_pdb_bfactor"
    assert metric["confidence_level"] == "qc_gate"
    assert metric["is_final_rank_metric"] is False
    assert "not binding" in metric["notes"].lower()


def test_ppiflow_objective_metric_is_bms_local_not_paper_rank() -> None:
    metric = build_ppiflow_local_objective_metric(
        value=-1.25,
        objective_mode="balanced",
        artifact_path="maturation_score.json",
    )

    assert metric["metric_key"] == "bms_ppiflow_local_objective_score"
    assert metric["display_name"] == "BMS local PPIFlow objective"
    assert metric["direction"] == "lower_is_better"
    assert metric["metric_source"] == "ppiflow_local_score_json"
    assert metric["is_final_rank_metric"] is False
    assert metric["provenance"]["upstream_ppiflow_paper_rank_used"] is False


def test_rosetta_interface_metric_preserves_sign_and_excludes_dockq_refold() -> None:
    metric = build_rosetta_interface_metric(value=-42.5, artifact_path="maturation_score.json", interface_id="HL_A")

    assert metric["metric_key"] == "rosetta_interface_score"
    assert metric["direction"] == "more_negative_is_better"
    assert metric["unit"] == "REU"
    assert metric["provenance"]["score_direction"] == "more_negative_is_better"
    assert metric["provenance"]["dockq_used"] is False
    assert metric["provenance"]["refold_comparison_used"] is False


def test_ppiflow_paper_rank_metric_uses_subtract_formula_and_excludes_dockq_refold() -> None:
    metric = build_ppiflow_paper_rank_metric(
        validator_iptm=0.8,
        rosetta_interface_score=-60.0,
        artifact_path="maturation_score.json",
    )

    assert metric["metric_key"] == "ppiflow_paper_rank_score"
    assert metric["value"] == 140.0
    assert metric["direction"] == "higher_is_better"
    assert metric["is_final_rank_metric"] is True
    assert metric["formula"] == "100 * validator_iptm - rosetta_interface_score"
    assert metric["provenance"]["dockq_used"] is False
    assert metric["provenance"]["refold_comparison_used"] is False


def test_design_metric_provenance_adds_rosetta_and_paper_rank_when_inputs_exist() -> None:
    provenance = build_design_metric_provenance(
        {
            "ppiflow_objective_score": -1.25,
            "ppiflow_objective_mode": "balanced",
            "iptm": 0.8,
            "rosetta_interface_score": -60.0,
            "rosetta_interface_id": "HL_A",
            "json_path": "maturation_score.json",
        }
    )

    metrics = {metric["metric_key"]: metric for metric in provenance["metrics"]}
    assert metrics["bms_ppiflow_local_objective_score"]["is_final_rank_metric"] is False
    assert metrics["rosetta_interface_score"]["value"] == -60.0
    assert metrics["ppiflow_paper_rank_score"]["value"] == 140.0


def test_metric_completeness_does_not_treat_af3score_backend_as_validator() -> None:
    design = SimpleNamespace(
        ppiflow_objective_score=-2.0,
        confidence_metrics={"validator_backend": "af3score"},
        rosetta_interface_score=-42.0,
    )

    completeness = build_design_metric_completeness(design)

    assert completeness["ppiflow"]["validator_confidence_available"] is False
    assert completeness["ppiflow"]["paper_rank_available"] is False
    assert "ppiflow_validator_confidence" in completeness["missing"]


def test_metric_completeness_marks_paper_rank_complete_without_dockq_or_refold() -> None:
    design = SimpleNamespace(
        ppiflow_objective_score=-2.0,
        iptm=0.82,
        rosetta_interface_score=-44.0,
        confidence_metrics=None,
    )

    completeness = build_design_metric_completeness(design)

    assert completeness["ppiflow"]["validator_confidence_available"] is True
    assert completeness["ppiflow"]["rosetta_interface_score_available"] is True
    assert completeness["ppiflow"]["paper_rank_available"] is True
    assert "dockq" not in " ".join(completeness["missing"]).lower()
    assert "refold" not in " ".join(completeness["missing"]).lower()

def test_metric_completeness_flags_missing_upstream_fampnn_and_ppiflow_metrics() -> None:
    design = SimpleNamespace(
        fampnn_psce=0.23,
        confidence_metrics=None,
        ppiflow_objective_score=-2.0,
        ptm=None,
        iptm=None,
        pair_chains_iptm=None,
    )

    completeness = build_design_metric_completeness(design)

    assert completeness["overall_status"] == "partial"
    assert completeness["fampnn"]["pse_available"] is True
    assert completeness["fampnn"]["seq_probs_available"] is False
    assert "fampnn_seq_probs" in completeness["missing"]
    assert completeness["ppiflow"]["local_objective_available"] is True
    assert completeness["ppiflow"]["paper_rank_available"] is False
    assert "ppiflow_validator_confidence" in completeness["missing"]
    assert "ppiflow_rosetta_interface_score" in completeness["missing"]

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.design_metrics import (  # noqa: E402
    build_design_metric_completeness,
    build_fampnn_psce_metric,
    build_ppiflow_local_objective_metric,
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

from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = API_ROOT.parents[1] / "scripts"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from routers.jobs import _normalize_antibody_job_params
from screen_rfantibody_backbones import (
    annotate_loop_metrics,
    build_loop_screening_summary,
    choose_headline_scope,
    normalize_screen_reference_scope,
)


def test_normalize_antibody_job_params_normalizes_screen_reference_scope() -> None:
    normalized = _normalize_antibody_job_params({
        "rfantibody_screen_reference_scope": "framework_inclusive",
    })
    assert normalized["rfantibody_screen_reference_scope"] == "whole_antibody"

    normalized = _normalize_antibody_job_params({
        "rfantibody_screen_reference_scope": "cdr_loops",
    })
    assert normalized["rfantibody_screen_reference_scope"] == "cdr_loops"


def test_normalize_screen_reference_scope_defaults_to_cdr_loops() -> None:
    assert normalize_screen_reference_scope(None) == "cdr_loops"
    assert normalize_screen_reference_scope("whole") == "whole_antibody"
    assert normalize_screen_reference_scope("framework") == "whole_antibody"
    assert normalize_screen_reference_scope("unexpected") == "cdr_loops"


def test_choose_headline_scope_falls_back_when_loop_metrics_are_missing() -> None:
    metrics_by_scope = {
        "cdr_loops": {"reference_residue_count": 0, "target_contact_count": 0},
        "whole_antibody": {"reference_residue_count": 133, "target_contact_count": 35},
    }

    effective_scope, metrics, fallback_reason = choose_headline_scope(
        requested_scope="cdr_loops",
        metrics_by_scope=metrics_by_scope,
    )

    assert effective_scope == "whole_antibody"
    assert metrics["target_contact_count"] == 35
    assert fallback_reason == "missing_loop_annotations"


def test_annotate_loop_metrics_marks_detached_and_off_epitope_loops() -> None:
    annotated = annotate_loop_metrics(
        {
            "H1": {
                "epitope_contact_count": 2,
                "epitope_min_distance": 6.1,
                "target_contact_count": 4,
                "target_min_distance": 5.5,
            },
            "H2": {
                "epitope_contact_count": 0,
                "epitope_min_distance": 10.5,
                "target_contact_count": 3,
                "target_min_distance": 6.2,
            },
            "H3": {
                "epitope_contact_count": 0,
                "epitope_min_distance": None,
                "target_contact_count": 0,
                "target_min_distance": None,
            },
        },
        epitope_contact_distance_threshold=8.0,
        target_contact_distance_threshold=12.0,
    )

    assert annotated["H1"]["engagement_label"] == "engaged"
    assert annotated["H1"]["redesign_candidate"] is False
    assert annotated["H2"]["engagement_label"] == "off_epitope"
    assert annotated["H2"]["redesign_candidate"] is True
    assert annotated["H2"]["redesign_reason"] == "no_epitope_contacts,epitope_far"
    assert annotated["H3"]["engagement_label"] == "detached"
    assert annotated["H3"]["redesign_candidate"] is True


def test_build_loop_screening_summary_tracks_framework_delta_and_redesign_candidates() -> None:
    loop_metrics = {
        "H1": {"engagement_label": "engaged", "redesign_candidate": False, "epitope_contact_count": 3, "epitope_min_distance": 5.0, "target_contact_count": 8, "target_min_distance": 4.0},
        "H2": {"engagement_label": "off_epitope", "redesign_candidate": True, "epitope_contact_count": 0, "epitope_min_distance": 12.0, "target_contact_count": 2, "target_min_distance": 6.0},
        "H3": {"engagement_label": "proximal", "redesign_candidate": False, "epitope_contact_count": 1, "epitope_min_distance": 7.5, "target_contact_count": 5, "target_min_distance": 3.5},
    }
    metrics_by_scope = {
        "cdr_loops": {"target_contact_count": 15, "epitope_contact_count": 4},
        "whole_antibody": {"target_contact_count": 21, "epitope_contact_count": 6},
    }

    summary = build_loop_screening_summary(
        loop_metrics=loop_metrics,
        metrics_by_scope=metrics_by_scope,
        requested_scope="cdr_loops",
        effective_scope="cdr_loops",
        scope_fallback_reason=None,
    )

    assert summary["framework_only_target_contact_count"] == 6
    assert summary["framework_only_epitope_contact_count"] == 2
    assert summary["engaged_loops"] == ["H1"]
    assert summary["redesign_candidate_loops"] == ["H2"]
    assert summary["best_epitope_loop"] == "H1"
    assert summary["closest_target_loop"] == "H3"

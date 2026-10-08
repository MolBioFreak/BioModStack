from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from migrations.add_ont_signal_comparisons import migrate
from migrations.runner import MIGRATIONS
from routers.ont_signal_workbench import (
    ComparisonCreate,
    ComparisonPreviewCreate,
    ComparisonReviewCreate,
)
from services import ont_signal_workbench as service


def _request() -> dict[str, object]:
    return {
        "viewer_session_id": "viewer-1",
        "expected_viewer_revision": 3,
        "mapping_artifact_id": "mapping-artifact-1",
        "selected_read_id": "read-1",
        "reference_contig": "plasmid",
        "reference_start": 10,
        "reference_end": 40,
        "simulation_settings": {"profile_id": "dna-r10-min", "seed": 7},
        "render_params": {
            "scale": "none", "point_size": 0.5, "fixed_width": False,
            "base_width": 10, "base_limit": 1000, "signal_sample_limit": 100000,
            "show_samples": True, "show_base_colours": True,
            "remove_signal_outliers": False,
        },
    }


def test_closed_comparison_request_defaults_and_seed_zero_rejection() -> None:
    request = ComparisonPreviewCreate.model_validate(_request())
    assert request.simulation_settings.seed == 7
    assert request.simulation_settings.profile_id == "dna-r10-min"
    unknown = {**_request(), "command": ["squigulator"]}
    with pytest.raises(ValidationError):
        ComparisonPreviewCreate.model_validate(unknown)
    invalid = _request()
    invalid["simulation_settings"] = {"profile_id": "dna-r10-min", "seed": 0}
    with pytest.raises(ValidationError):
        ComparisonPreviewCreate.model_validate(invalid)
    with pytest.raises(ValidationError):
        ComparisonCreate.model_validate({**_request(), "preview_digest": "not-a-digest"})


def test_effective_settings_include_profile_fixed_and_workflow_fixed_values() -> None:
    compiled = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, _request()["render_params"]
    )
    assert compiled["profile"]["sample_rate"] == 5000
    assert compiled["profile"]["dwell_mean"] == 13.0
    assert compiled["workflow_fixed"] == {
        "simulation_mode": "ideal", "full_contigs": True,
        "amplitude_noise_factor": 0, "dwell_noise": 0, "prefix": False,
        "input_sequence_count": 1, "simulated_signal_record_count": 1,
        "threads": 1, "batch_size": 1, "signal_units": "pA",
        "real_read_count": 1, "reference_hypothesis_count": 1,
        "sequence_basis": "managed_reference",
    }
    assert compiled["compatibility_floor"] == "approximate_profile"
    assert service.comparison_request_fingerprint({**compiled, "x": 1}) != service.comparison_request_fingerprint({**compiled, "x": 2})


def test_review_uses_existing_manual_criterion_vocabulary() -> None:
    approved = ComparisonReviewCreate(
        review_question="Does the real trace visually agree with the ideal expectation?",
        required_outcome="approve", note="Agreement across the selected interval.",
        reviewed_start=10, reviewed_end=40, predecessor_review_id=None,
    )
    assert approved.required_outcome == "approve"
    with pytest.raises(ValidationError):
        ComparisonReviewCreate(
            review_question="question", required_outcome="pass", note="note",
            reviewed_start=10, reviewed_end=40, predecessor_review_id=None,
        )


def _parents(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE ont_instrument_runs (id VARCHAR(80) PRIMARY KEY);
    CREATE TABLE ont_raw_signal_representations (id VARCHAR(96) PRIMARY KEY);
    CREATE TABLE ont_signal_mapping_artifacts (id VARCHAR(96) PRIMARY KEY);
    CREATE TABLE ont_signal_viewer_sessions (id VARCHAR(96) PRIMARY KEY);
    """)


def test_router_exposes_complete_comparison_lifecycle() -> None:
    from fastapi.routing import APIRoute
    from routers import ont_signal_workbench as comparison_router

    routes = {(route.path, tuple(sorted(route.methods or ()))) for route in comparison_router.router.routes if isinstance(route, APIRoute)}
    expected_paths = {
        "/comparisons/preview", "/comparisons", "/comparisons/{comparison_job_id}",
        "/comparisons/{comparison_job_id}/cancel", "/comparisons/{comparison_job_id}/fresh-attempt",
        "/comparisons/{comparison_job_id}/artifacts/{artifact_id}",
        "/comparisons/{comparison_job_id}/reviews",
    }
    assert expected_paths <= {path for path, _methods in routes}


def test_migration_42_registers_immutable_comparison_ledgers(tmp_path: Path) -> None:
    db = tmp_path / "comparison.db"
    with sqlite3.connect(db) as connection:
        _parents(connection)
    migrate(str(db)); migrate(str(db))
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"ont_signal_comparison_jobs", "ont_signal_comparison_events", "ont_signal_comparison_artifacts", "ont_signal_manual_reviews"} <= tables
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"trg_ont_signal_comparison_job_identity_no_update", "trg_ont_signal_comparison_job_terminal_no_update", "trg_ont_signal_comparison_artifact_no_update", "trg_ont_signal_comparison_events_no_update", "trg_ont_signal_manual_reviews_no_update"} <= triggers
    registration = [(item.version, item.name) for item in MIGRATIONS if item.name == "add_ont_signal_comparisons"]
    assert registration == [(42, "add_ont_signal_comparisons")]

from __future__ import annotations

from pathlib import Path

from database import Design
from routers.designs import _design_to_response


def _design(**values) -> Design:
    defaults = {
        "id": "design-1",
        "job_id": "job-1",
        "name": "candidate-1",
        "pdb_path": "/private/candidate-1.pdb",
    }
    defaults.update(values)
    return Design(**defaults)


def test_design_response_discriminates_canonical_frustrampnn_authority() -> None:
    response = _design_to_response(
        _design(
            frustrampnn_contract_version="1.0",
            frustrampnn_status="succeeded",
            frustrampnn_source_sha256="1" * 64,
            frustrampnn_manifest_relpath="frustrampnn_result_manifest_v1.json",
            frustrampnn_landscape_relpath="frustrampnn_landscape_v1.json",
            frustrampnn_summary_relpath="frustrampnn_summary_v1.json",
            frustrampnn_runtime_sha256="2" * 64,
            frustration_high_count=99,
        )
    )

    assert response.frustrampnn is not None
    assert response.frustrampnn.authority == "canonical"
    assert response.frustrampnn.canonical is not None
    assert response.frustrampnn.canonical.manifest_relpath == (
        "frustrampnn_result_manifest_v1.json"
    )
    assert response.frustrampnn.legacy_summary is None
    # Historical fields remain readable but never become canonical authority.
    assert response.frustration_high_count == 99


def test_design_response_marks_historical_summary_without_fabricating_landscape() -> None:
    response = _design_to_response(
        _design(
            frustration_high_count=3,
            frustration_min_count=2,
            frustration_pct_high=15.0,
            frustration_csv_path="/private/legacy_frustration.csv",
            frustration_residues=[{"pos": 1, "chain": "A"}],
        )
    )

    assert response.frustrampnn is not None
    assert response.frustrampnn.authority == "legacy_summary"
    assert response.frustrampnn.canonical is None
    assert response.frustrampnn.legacy_summary is not None
    assert response.frustrampnn.legacy_summary.high_count == 3
    assert response.frustrampnn.legacy_summary.min_count == 2
    assert response.frustrampnn.legacy_summary.pct_high == 15.0
    assert response.frustrampnn.legacy_summary.csv_relpath is None
    assert not hasattr(response.frustrampnn.legacy_summary, "landscape")


def test_design_response_without_frustrampnn_data_has_no_projection() -> None:
    response = _design_to_response(_design())
    assert response.frustrampnn is None


def test_frontend_design_type_exposes_discriminated_frustrampnn_projection() -> None:
    source = (
        Path(__file__).parents[2] / "frontend" / "src" / "lib" / "api.ts"
    ).read_text(encoding="utf-8")
    assert "export interface DesignFrustraMPNNProjection" in source
    assert "authority: 'canonical' | 'legacy_summary';" in source
    assert "frustrampnn?: DesignFrustraMPNNProjection | null;" in source

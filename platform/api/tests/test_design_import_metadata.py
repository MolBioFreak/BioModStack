from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Design
from routers.designs import _design_to_response, _enrich_design_responses_from_sources


def test_design_response_surfaces_generic_import_metadata_for_esmfold_rows() -> None:
    design = Design(
        id="external-esmfold-design-1",
        job_id="external-import-job-1",
        name="external_import_001",
        stage_family="validation",
        stage_mode="bundle_import",
        artifact_class="imported_structure",
        provenance={
            "dataset_name": "Selected submissions",
        },
        confidence_metrics={
            "esmfold_plddt": 42.09418604651162,
            "structure_prediction_url": "https://example.org/esmfold-default.cif",
        },
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is True
    assert response.import_source == "external"
    assert response.import_method == "esmfold"
    assert response.import_label == "Imported • ESMFold"


def test_design_response_inferrs_boltz2_import_method_from_generic_metrics() -> None:
    design = Design(
        id="external-boltz-design-1",
        job_id="external-import-job-1",
        name="external_import_002",
        stage_family="validation",
        stage_mode="bundle_import",
        artifact_class="imported_structure",
        provenance={
            "dataset_name": "Selected submissions",
        },
        confidence_metrics={
            "boltz2_ptm": 0.66,
            "boltz2_iptm": 0.89,
            "boltz2_complex_iplddt": 0.75,
            "boltz2_complex_pde": 0.60,
            "boltz2_ipsae": 0.58,
        },
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is True
    assert response.import_source == "external"
    assert response.import_method == "boltz2"
    assert response.import_label == "Imported • Boltz2"


def test_design_response_leaves_native_validation_rows_unflagged() -> None:
    design = Design(
        id="native-validation-design-1",
        job_id="native-validation-job-1",
        name="native_validation_001",
        stage_family="validation",
        stage_mode="post_structure_validation",
        ptm=0.66,
        iptm=0.89,
        complex_iplddt=0.75,
        complex_ipde=0.60,
        ipsae=0.58,
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.is_imported is False
    assert response.import_source is None
    assert response.import_method is None
    assert response.import_label is None


def test_design_response_backfills_sequence_result_set_from_stage_contract() -> None:
    design = Design(
        id="fampnn-design-1",
        job_id="sequence-stage-job-1",
        name="fampnn_seq_001",
        stage_family="fampnn",
        stage_mode="post_fampnn",
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.artifact_class == "sequence_designed_complex"
    assert response.artifact_schema_version == 1
    assert response.result_set == "sequence_designs"
    assert response.result_set_label == "Sequence designs"


def test_design_response_separates_ppiflow_pass_and_reject_result_sets() -> None:
    passed = Design(
        id="ppiflow-design-pass",
        job_id="ppiflow-job-1",
        name="fampnn_seq_001_ppiflow_sample0",
        stage_family="ppiflow",
        stage_mode="maturation",
        ppiflow_filter_passed=True,
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )
    rejected = Design(
        id="ppiflow-design-reject",
        job_id="ppiflow-job-1",
        name="fampnn_seq_001_ppiflow_sample1",
        stage_family="ppiflow",
        stage_mode="maturation",
        ppiflow_filter_passed=False,
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    assert _design_to_response(passed).result_set == "ppiflow_passed"
    assert _design_to_response(passed).result_set_label == "PPIFlow passed"
    assert _design_to_response(rejected).result_set == "ppiflow_rejected"
    assert _design_to_response(rejected).result_set_label == "PPIFlow rejected"


def test_design_response_fails_closed_for_unknown_model_contract() -> None:
    design = Design(
        id="unknown-model-design-1",
        job_id="unknown-model-job-1",
        name="new_model_candidate_001",
        stage_family="new_public_model",
        stage_mode="predict",
        artifact_class="novel_structure",
        fampnn_psce=0.7,
        confidence_metrics={
            "plddt": 81.2,
            "ranking_score": 0.93,
            "fampnn": {"fampnn_avg_psce": 0.7},
        },
        provenance={"model_id": "new_public_model"},
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.result_set is None
    assert response.result_set_label is None
    assert response.analysis_contract_id is None
    assert response.supported_analyzers == []
    assert response.viewer_capabilities == []
    assert response.result_contract_source == "unsupported"


def test_design_response_exposes_registry_capabilities_for_known_streams() -> None:
    design = Design(
        id="ppiflow-contract-design-1",
        job_id="ppiflow-contract-job-1",
        name="candidate_ppiflow_sample0",
        stage_family="ppiflow",
        stage_mode="maturation",
        ppiflow_filter_passed=True,
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    response = _design_to_response(design)

    assert response.result_set == "ppiflow_passed"
    assert response.analysis_contract_id == "ppiflow_maturation_v1"
    assert response.supported_analyzers == ["ppiflow_maturation_v1"]
    assert "ppiflow_maturation_metrics" in response.viewer_capabilities
    assert "rosetta_interface_score" in response.required_fields
    assert response.result_contract_schema_version == 1
    assert response.result_contract_source == "registry"


def test_ppiflow_children_inherit_source_binder_length_from_embedded_source_uuid() -> None:
    source = Design(
        id="39c69ef1-3e4f-465f-870a-bbf79f5363cf",
        job_id="source-job",
        name="job0_001_f731565d-9c96-4721-8953-0f7af8a2084a_seq_0",
        binder_length=111,
        cdr_h1_length=8,
        cdr_h2_length=7,
        cdr_h3_length=13,
        artifact_class="sequence_designed_complex",
        stage_family="fampnn",
        stage_mode="post_fampnn",
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )
    child = Design(
        id="49e43d95-e26a-44ac-a6d0-e5f9076c5fcf",
        job_id="child-job",
        name="job0_001_f731565d-9c96-4721-8953-0f7af8a2084a_seq_0_ppiflow_seq_3_sample0",
        binder_length=None,
        cdr_h1_length=None,
        cdr_h2_length=None,
        cdr_h3_length=None,
        artifact_class="sequence_designed_complex",
        stage_family="ppiflow",
        stage_mode="maturation",
        ppiflow_filter_passed=False,
        provenance={"maturation_filter_passed": False},
        is_favorite=False,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    responses = [_design_to_response(source), _design_to_response(child)]
    _enrich_design_responses_from_sources(responses)

    assert responses[1].binder_length == 111
    assert responses[1].cdr_h1_length == 8
    assert responses[1].cdr_h2_length == 7
    assert responses[1].cdr_h3_length == 13

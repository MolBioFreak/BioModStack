from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.result_contracts import get_result_contract_definitions, resolve_result_contract


def test_result_contract_registry_definitions_are_explicit_and_inspectable() -> None:
    definitions = {definition.contract_id: definition for definition in get_result_contract_definitions()}

    assert definitions["antibody_backbone_v1"].model_dump() == {
        "contract_id": "antibody_backbone_v1",
        "schema_version": 1,
        "model_ids": ["rfantibody"],
        "stage_families": ["rfantibody"],
        "stage_modes": [],
        "artifact_classes": ["backbone_complex"],
        "result_sets": ["rfantibody_backbones"],
        "supported_analyzers": ["antibody_backbone_v1"],
        "viewer_capabilities": ["result_filter", "structure_viewer", "antibody_backbone_metrics"],
        "required_fields": ["artifact_class", "result_set"],
        "required_artifacts": ["structure"],
        "notes": "RFantibody/backbone generation outputs.",
    }
    assert definitions["sequence_design_v1"].result_sets == ["sequence_designs"]
    assert "fampnn" in definitions["sequence_design_v1"].stage_families
    assert "proteinmpnn" in definitions["sequence_design_v1"].stage_families
    assert definitions["ppiflow_maturation_v1"].result_sets == [
        "ppiflow_candidates",
        "ppiflow_passed",
        "ppiflow_rejected",
    ]
    assert "rosetta_interface_score" in definitions["ppiflow_maturation_v1"].required_fields


def test_result_contract_registry_maps_known_result_sets_to_analyzers_and_capabilities() -> None:
    assert resolve_result_contract(result_set="rfantibody_backbones").model_dump() == {
        "analysis_contract_id": "antibody_backbone_v1",
        "supported_analyzers": ["antibody_backbone_v1"],
        "viewer_capabilities": ["result_filter", "structure_viewer", "antibody_backbone_metrics"],
        "required_fields": ["artifact_class", "result_set"],
        "required_artifacts": ["structure"],
        "schema_version": 1,
        "contract_source": "registry",
    }
    assert resolve_result_contract(result_set="sequence_designs").analysis_contract_id == "sequence_design_v1"
    assert resolve_result_contract(result_set="ppiflow_passed").analysis_contract_id == "ppiflow_maturation_v1"


def test_result_contract_registry_fails_closed_for_unknown_model_with_metric_shaped_payload() -> None:
    contract = resolve_result_contract(
        stage_family="new_public_model",
        stage_mode="predict",
        artifact_class="novel_structure",
        provenance={"model_id": "new_public_model"},
    )

    assert contract.analysis_contract_id is None
    assert contract.supported_analyzers == []
    assert contract.viewer_capabilities == []
    assert contract.required_fields == []
    assert contract.required_artifacts == []
    assert contract.contract_source == "unsupported"


def test_result_contract_registry_pins_known_streams_without_metric_name_guessing() -> None:
    cases = [
        ("rfantibody", "backbone_generation", "backbone_complex", None, "antibody_backbone_v1"),
        ("fampnn", "post_fampnn", "sequence_designed_complex", None, "sequence_design_v1"),
        ("proteinmpnn", "sequence_design", "sequence_designed_complex", None, "sequence_design_v1"),
        ("antifold", "sequence_design", "sequence_designed_complex", None, "sequence_design_v1"),
        ("caliby", "sequence_design", "sequence_designed_complex", None, "sequence_design_v1"),
        ("ppiflow", "maturation", "sequence_designed_complex", "ppiflow_candidates", "ppiflow_maturation_v1"),
        ("ppiflow", "maturation", "sequence_designed_complex", "ppiflow_passed", "ppiflow_maturation_v1"),
        ("ppiflow", "maturation", "sequence_designed_complex", "ppiflow_rejected", "ppiflow_maturation_v1"),
        ("boltz2", "validation", "validated_complex", None, "structure_prediction_v1"),
        ("protenix", "validation", "validated_complex", None, "structure_prediction_v1"),
        ("esmfold2", "validation", "validated_complex", None, "structure_prediction_v1"),
        ("confornets", "monomer_analysis", "monomer_conformation", None, "confornets_monomer_v1"),
    ]

    for family, mode, artifact, result_set, expected_contract in cases:
        contract = resolve_result_contract(
            result_set=result_set,
            stage_family=family,
            stage_mode=mode,
            artifact_class=artifact,
            provenance={"model_id": family},
        )
        assert contract.analysis_contract_id == expected_contract, (family, mode, artifact, result_set)


def test_result_contract_registry_rejects_unknown_even_with_known_metric_names() -> None:
    contract = resolve_result_contract(
        stage_family="external_new_model",
        stage_mode="predict",
        artifact_class="novel_complex",
        provenance={
            "model_id": "external_new_model",
            "plddt": 99.1,
            "fampnn_psce": 0.12,
            "ppiflow_objective_score": -42.0,
        },
    )

    assert contract.analysis_contract_id is None
    assert contract.supported_analyzers == []
    assert contract.contract_source == "unsupported"

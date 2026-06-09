from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.result_contracts import resolve_result_contract


def test_result_contract_registry_maps_known_result_sets_to_analyzers() -> None:
    assert resolve_result_contract(result_set="rfantibody_backbones").model_dump() == {
        "analysis_contract_id": "antibody_backbone_v1",
        "supported_analyzers": ["antibody_backbone_v1"],
    }
    assert resolve_result_contract(result_set="sequence_designs").model_dump() == {
        "analysis_contract_id": "sequence_design_v1",
        "supported_analyzers": ["sequence_design_v1"],
    }
    assert resolve_result_contract(result_set="ppiflow_passed").model_dump() == {
        "analysis_contract_id": "ppiflow_maturation_v1",
        "supported_analyzers": ["ppiflow_maturation_v1"],
    }


def test_result_contract_registry_fails_closed_for_unknown_model_with_metric_shaped_payload() -> None:
    contract = resolve_result_contract(
        stage_family="new_public_model",
        stage_mode="predict",
        artifact_class="novel_structure",
        provenance={"model_id": "new_public_model"},
    )

    assert contract.analysis_contract_id is None
    assert contract.supported_analyzers == []

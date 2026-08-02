from __future__ import annotations

import copy
from pathlib import Path

import pytest

from services.frustrampnn.configuration import global_configuration, request_parameters
from services.frustrampnn.contracts import ContractValidationError, validate_schema


API_ROOT = Path(__file__).resolve().parents[1]


def _request() -> dict[str, object]:
    source_hash = "a" * 64
    return {
        "schema_name": "workflow_component_request",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "invocation_id": "invoke-config-1",
        "parent_job_id": "job-config-1",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-config-1",
        "source_artifact": {
            "relative_path": "inputs/candidate.pdb",
            "sha256": source_hash,
            "media_type": "chemical/x-pdb",
            "producer_stage": "prediction",
            "artifact_id": None,
        },
        "requiredness": "required",
        "identity_authority": "pdb_coordinates",
        "protein_selection": {"mode": "all_protein_entities"},
        "parameters": request_parameters(),
        "requested_outputs": ["structure_map", "raw_csv", "landscape", "summary", "execution_receipt"],
    }


def test_canonical_request_binds_global_configuration_identity() -> None:
    request = _request()
    validate_schema("workflow_component_request_v1", request)
    assert request["parameters"]["configuration_id"] == "frustrampnn_global_v1"
    assert request["parameters"]["configuration_sha256"] == global_configuration()["configuration_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: request["parameters"].pop("configuration_id"),
        lambda request: request["parameters"].pop("configuration_sha256"),
        lambda request: request["parameters"].update({"configuration_id": "other_v1"}),
        lambda request: request["parameters"].update({"configuration_sha256": "0" * 64}),
    ],
)
def test_partial_or_foreign_configuration_identity_is_rejected(mutation) -> None:
    request = copy.deepcopy(_request())
    mutation(request)
    with pytest.raises(ContractValidationError, match="configuration"):
        validate_schema("workflow_component_request_v1", request)

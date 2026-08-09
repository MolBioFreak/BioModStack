from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from services.frustrampnn.configuration import (
    configuration_sha256,
    execution_configuration,
    global_configuration,
    request_parameters,
)
from services.frustrampnn.contracts import ContractValidationError, validate_schema
from services.frustrampnn.settings import (
    FrustraMPNNResolutionIdentity,
    FrustraMPNNResolvedChainSelection,
    FrustraMPNNResolvedResidue,
    default_settings,
    _build_effective_settings,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCHEMA_ROOT = REPO_ROOT / "schemas/frustrampnn"


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
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }


def _effective():
    requested = default_settings()
    residue = FrustraMPNNResolvedResidue.model_validate(
        {
            "entity_instance_id": "entity-1",
            "source_entity_id": "1",
            "label_asym_id": "A",
            "auth_asym_id": "A",
            "auth_seq_id": 10,
            "insertion_code": "",
            "sequence_index": 10,
            "wt": "L",
            "pdb_chain_id": "A",
            "model_position": 9,
        }
    )
    chain = FrustraMPNNResolvedChainSelection.model_validate(
        {
            "entity": {
                "entity_instance_id": "entity-1",
                "source_entity_id": "1",
                "label_asym_id": "A",
                "auth_asym_id": "A",
            },
            "pdb_chain_id": "A",
            "residues": [residue.model_dump(mode="json")],
        }
    )
    identity = FrustraMPNNResolutionIdentity.model_validate(
        {
            "source_artifact_sha256": "a" * 64,
            "structure_map_sha256": "b" * 64,
            "normalized_pdb_sha256": "c" * 64,
        }
    )
    return _build_effective_settings(
        requested,
        resolved_chains=(chain,),
        resolution_identity=identity,
    )


def _request_v2() -> dict[str, object]:
    effective = _effective()
    requested = effective.requested_settings
    configuration = execution_configuration(effective)
    return {
        "schema_name": "workflow_component_request",
        "schema_version": 2,
        "component_id": "frustrampnn",
        "component_contract_version": "2.0",
        "invocation_id": "invoke-config-v2",
        "parent_job_id": "job-config-v2",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-config-v2",
        "source_artifact": {
            "relative_path": "inputs/candidate.pdb",
            "sha256": "a" * 64,
            "media_type": "chemical/x-pdb",
            "producer_stage": "prediction",
            "artifact_id": None,
        },
        "requiredness": "required",
        "identity_authority": "pdb_coordinates",
        "settings_value_origin": requested.settings_value_origin,
        "requested_settings": requested.model_dump(mode="json"),
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings": effective.model_dump(mode="json"),
        "effective_settings_sha256": effective.effective_settings_sha256,
        "classification_policy_sha256": effective.threshold_policy_sha256,
        "capability_inventory_byte_sha256": (
            effective.capability_inventory_byte_sha256
        ),
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "structure_map_sha256": effective.resolution_identity.structure_map_sha256,
        "normalized_pdb_sha256": effective.resolution_identity.normalized_pdb_sha256,
        "execution_configuration": configuration.model_dump(mode="json"),
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }


def _assert_closed_object_schemas(node: object, location: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, location
        for key, value in node.items():
            _assert_closed_object_schemas(value, f"{location}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_closed_object_schemas(value, f"{location}/{index}")


def test_phase1_uses_separate_closed_draft_2020_12_schemas() -> None:
    expected = {
        "settings_v1.schema.json": "settings_v1",
        "effective_settings_v1.schema.json": "effective_settings_v1",
        "execution_configuration_v2.schema.json": "execution_configuration_v2",
        "workflow_component_request_v2.schema.json": "workflow_component_request_v2",
    }
    for filename, title in expected.items():
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{filename}")
        assert schema["title"] == title
        _assert_closed_object_schemas(schema)


def test_requested_settings_schema_enforces_closed_mode_and_canonical_threshold_semantics() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "settings_v1.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    invalid_selection = default_settings().model_dump(mode="json")
    invalid_selection["protein_selection"]["entities"] = [
        {
            "entity_instance_id": "entity-1",
            "source_entity_id": "1",
            "label_asym_id": "A",
            "auth_asym_id": "A",
        }
    ]
    assert list(validator.iter_errors(invalid_selection))

    invalid_threshold = default_settings().model_dump(mode="json")
    invalid_threshold["classification_policy"].update(
        {"high_max": -0.5, "minimal_min": 0.25}
    )
    assert list(validator.iter_errors(invalid_threshold))


def test_v2_request_carries_exact_execution_configuration_and_all_typed_receipts() -> None:
    request = _request_v2()

    validate_schema("frustrampnn_requested_settings_v1", request["requested_settings"])
    validate_schema("frustrampnn_effective_settings_v1", request["effective_settings"])
    validate_schema(
        "frustrampnn_execution_configuration_v2",
        request["execution_configuration"],
    )
    validate_schema("workflow_component_request_v2", request)

    expected = execution_configuration(_effective()).model_dump(mode="json")
    assert request["execution_configuration"] == expected
    assert request["execution_configuration_sha256"] == expected[
        "configuration_sha256"
    ]
    assert request["source_artifact"]["sha256"] == request[
        "effective_settings"
    ]["resolution_identity"]["source_artifact_sha256"]
    assert "configuration_receipt" not in request
    assert "complete_effective_configuration_sha256" not in request


def test_v2_request_keeps_v1_identity_source_and_output_contract() -> None:
    v1 = _request()
    v2 = _request_v2()
    retained = {
        "component_id",
        "invocation_id",
        "parent_job_id",
        "parent_workflow_id",
        "candidate_id",
        "source_artifact",
        "requiredness",
        "identity_authority",
        "requested_outputs",
    }
    assert retained <= set(v2)
    assert v2["requested_outputs"] == v1["requested_outputs"]
    assert "parameters" not in v2
    assert "runtime" not in v2
    assert "storage" not in v2


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda request: request.update({"requested_settings_sha256": "0" * 64}),
            "requested settings",
        ),
        (
            lambda request: request.update({"effective_settings_sha256": "0" * 64}),
            "effective settings",
        ),
        (
            lambda request: request.update(
                {"classification_policy_sha256": "0" * 64}
            ),
            "classification policy",
        ),
        (
            lambda request: request.update(
                {"capability_inventory_byte_sha256": "0" * 64}
            ),
            "capability inventory",
        ),
        (
            lambda request: request.update({"runtime_identity_sha256": "0" * 64}),
            "runtime identity",
        ),
        (
            lambda request: request.update({"structure_map_sha256": "0" * 64}),
            "structure map",
        ),
        (
            lambda request: request.update({"normalized_pdb_sha256": "0" * 64}),
            "normalized PDB",
        ),
        (
            lambda request: request.update(
                {"execution_configuration_sha256": "0" * 64}
            ),
            "execution configuration",
        ),
    ],
)
def test_v2_request_rejects_every_broken_duplicate_cross_binding(
    mutation, message
) -> None:
    request = copy.deepcopy(_request_v2())
    mutation(request)
    with pytest.raises(ContractValidationError, match=message):
        validate_schema("workflow_component_request_v2", request)


def test_v2_request_rejects_nested_tampering_after_outer_hash_recompute() -> None:
    request = copy.deepcopy(_request_v2())
    configuration = request["execution_configuration"]
    configuration["runtime"]["checkpoint_id"] = "other.ckpt"
    configuration["configuration_sha256"] = configuration_sha256(configuration)
    request["execution_configuration_sha256"] = configuration[
        "configuration_sha256"
    ]

    with pytest.raises(ContractValidationError, match="runtime|configuration"):
        validate_schema("workflow_component_request_v2", request)


def test_v2_schemas_and_models_reject_unknown_fields_and_bool_numbers() -> None:
    request = copy.deepcopy(_request_v2())
    request["effective_settings"]["unexpected"] = True
    with pytest.raises(ContractValidationError):
        validate_schema("workflow_component_request_v2", request)

    request = copy.deepcopy(_request_v2())
    request["requested_settings"]["source_structure"]["selected_model_number"] = True
    with pytest.raises(ContractValidationError):
        validate_schema("workflow_component_request_v2", request)

    request = copy.deepcopy(_request_v2())
    request["requested_settings"]["classification_policy"]["high_max"] = True
    with pytest.raises(ContractValidationError):
        validate_schema("workflow_component_request_v2", request)


def test_v2_request_rejects_caller_runtime_storage_or_raw_cli_values() -> None:
    for field, value in (
        ("runtime", {"container": "/tmp/evil.sif"}),
        ("storage", {"output_root": "/tmp/evil"}),
        ("parameters", {"chains": "A,B", "positions": "1,2"}),
    ):
        request = copy.deepcopy(_request_v2())
        request[field] = value
        with pytest.raises(ContractValidationError):
            validate_schema("workflow_component_request_v2", request)


def test_v1_historical_request_validation_remains_readable_unchanged() -> None:
    request = _request()
    validate_schema("workflow_component_request_v1", request)
    assert request["parameters"]["configuration_id"] == "frustrampnn_global_v1"
    assert request["parameters"]["configuration_sha256"] == global_configuration()[
        "configuration_sha256"
    ]

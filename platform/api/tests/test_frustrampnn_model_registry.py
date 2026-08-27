from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_registry import get_registry
from routers import models as models_router
from services.frustrampnn.settings import default_settings, load_capability_inventory


FORBIDDEN_PUBLIC_KEY_PARTS = {
    "path",
    "command",
    "container",
    "executable",
    "scheduler",
    "storage",
    "device",
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(models_router.router, prefix="/api/models")
    return TestClient(app)


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_frustrampnn_integration_exposes_exact_bounded_capability_and_settings_metadata() -> None:
    response = _client().get("/api/models/frustrampnn/integration")

    assert response.status_code == 200, response.text
    payload = response.json()
    inventory, byte_sha256 = load_capability_inventory()
    expected_public_options = [
        option["option_key"]
        for option in inventory["predict_options"]
        if option["product_control"]["status"] == "typed_product_control"
    ]

    assert payload["capability_inventory_byte_sha256"] == byte_sha256
    assert payload["capability_inventory_content_sha256"] == inventory["content_sha256"]
    assert payload["capability_inventory"]["content_sha256"] == inventory["content_sha256"]
    assert [
        option["option_key"] for option in payload["capability_inventory"]["predict_options"]
    ] == expected_public_options == ["pdb", "chains", "positions"]
    assert payload["canonical_defaults"] == default_settings().model_dump(mode="json")
    assert payload["settings_schema"]["required"] == [
        "schema_name",
        "schema_version",
        "batching_enabled",
        "structures_per_job",
        "protein_selection",
        "source_structure",
        "classification_policy",
    ]
    assert payload["settings_schema"]["properties"]["schema_version"]["const"] == 2
    assert payload["settings_schema"]["properties"]["batching_enabled"]["default"] is False
    assert payload["settings_schema"]["properties"]["structures_per_job"] == {
        "default": 1,
        "maximum": 250,
        "minimum": 1,
        "title": "Structures Per Job",
        "type": "integer",
    }
    definitions = payload["settings_schema"]["$defs"]
    assert definitions["FrustraMPNNProteinSelection"]["required"] == [
        "mode",
        "entities",
        "regions",
        "residues",
    ]
    assert definitions["FrustraMPNNRegionSelector"]["required"] == [
        "entity_instance_id",
        "source_entity_id",
        "label_asym_id",
        "auth_asym_id",
        "sequence_start",
        "sequence_end",
    ]
    assert definitions["FrustraMPNNSourceStructureSettings"]["required"] == [
        "selected_model_number",
        "preferred_altloc",
    ]
    assert definitions["FrustraMPNNClassificationPolicy"]["required"] == [
        "mode",
        "high_max",
        "minimal_min",
    ]
    assert "insertion_code" in definitions["FrustraMPNNResidueSelector"][
        "required"
    ]

    descriptor_ids = [item["field"] for item in payload["parameter_descriptors"]]
    assert descriptor_ids == [
        "source_artifact",
        "batching_enabled",
        "structures_per_job",
        "protein_selection.mode",
        "protein_selection.entities",
        "protein_selection.regions",
        "protein_selection.residues",
        "source_structure.selected_model_number",
        "source_structure.preferred_altloc",
        "classification_policy.mode",
        "classification_policy.high_max",
        "classification_policy.minimal_min",
    ]
    descriptors = {item["field"]: item for item in payload["parameter_descriptors"]}
    assert descriptors["batching_enabled"] == {
        "field": "batching_enabled",
        "api_type": "boolean",
        "ownership": "workflow_structure_grouping",
        "control_kind": "checkbox",
        "backing": "predict_batch",
        "default_source": "bms_default",
        "minimum": None,
        "maximum": None,
        "applicability": None,
    }
    assert descriptors["structures_per_job"] == {
        "field": "structures_per_job",
        "api_type": "integer",
        "ownership": "workflow_structure_grouping",
        "control_kind": "slider_with_numeric_input",
        "backing": "predict_batch",
        "default_source": "bms_default",
        "minimum": 1,
        "maximum": 250,
        "applicability": {"field": "batching_enabled", "equals": True},
    }
    assert set(payload["field_ownership"]) == set(descriptor_ids)
    assert set(payload["control_kind_hints"]) == set(descriptor_ids)
    assert {rule["rule_id"] for rule in payload["compatibility_rules"]} == {
        "structure_map_schema",
        "source_model_exact_match",
        "preferred_altloc_exact_match",
        "selector_exact_coverage",
        "mapped_residues_only",
        "classification_threshold_order",
        "queue_reresolution_required",
    }
    selector_rule = next(
        rule
        for rule in payload["compatibility_rules"]
        if rule["rule_id"] == "selector_exact_coverage"
    )
    assert selector_rule["fields"] == [
        "protein_selection.entities",
        "protein_selection.regions",
        "protein_selection.residues",
    ]

    for key, value in _walk(payload):
        lowered = key.lower()
        assert not any(part in lowered for part in FORBIDDEN_PUBLIC_KEY_PARTS), key
        if isinstance(value, str):
            assert not value.startswith(("/mnt/", "/opt/", "/home/")), value
            assert "cuda:0" not in value
    assert "runtime_identity" not in payload["capability_inventory"]
    assert "evidence" not in payload["capability_inventory"]


def test_frustrampnn_metadata_uses_the_installed_integration_and_no_absent_model_controls() -> None:
    registry = get_registry()
    model = registry.get_internal_model_definition("frustrampnn")
    assert model is not None and model.integration is not None

    payload = _client().get("/api/models/frustrampnn/integration").json()

    assert payload["model_version"] == model.version
    assert payload["stage_parameter"] == model.integration.stage_parameter
    assert all(
        descriptor["backing"] in {
            "pdb",
            "chains",
            "positions",
            "predict_batch",
            "bms_source_interpretation",
            "bms_classification_interpretation",
        }
        for descriptor in payload["parameter_descriptors"]
    )
    serialized = str(payload).lower()
    for absent in ("temperature", "seed", "batch_size", "num_samples", "recycles"):
        assert absent not in serialized


def test_frustrampnn_integration_openapi_is_dedicated_and_closed_recursively() -> None:
    app = FastAPI()
    app.include_router(models_router.router, prefix="/api/models")
    schema = app.openapi()
    operation = schema["paths"]["/api/models/{model_id}/integration"]["get"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    refs = [response["$ref"]] if "$ref" in response else [
        item["$ref"] for item in response.get("anyOf", [])
    ]
    assert refs
    response_models = {
        ref.rsplit("/", 1)[1]: schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        for ref in refs
    }
    dedicated_name = next(name for name in response_models if "FrustraMPNN" in name)
    dedicated = response_models[dedicated_name]
    assert dedicated["additionalProperties"] is False
    assert {
        "capability_inventory", "canonical_defaults", "settings_schema",
        "parameter_descriptors", "field_ownership", "control_kind_hints",
        "compatibility_rules",
    } <= set(dedicated["properties"])
    for name in (
        "FrustraMPNNCapabilityInventoryResponse",
        "FrustraMPNNPublicPredictOptionResponse",
        "FrustraMPNNParameterDescriptorResponse",
        "FrustraMPNNCompatibilityRuleResponse",
        "ModelIntegrationWorkflowResponse",
    ):
        assert schema["components"]["schemas"][name]["additionalProperties"] is False

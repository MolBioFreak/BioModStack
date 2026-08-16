"""Focused Phase 0 contract for the pinned FrustraMPNN capability inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator

from services.frustrampnn.runtime import FRUSTRAMPNN_RUNTIME_IDENTITY


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas/frustrampnn/capability_inventory_v1.schema.json"
INVENTORY_PATH = API_ROOT / "config/models/frustrampnn_capability_inventory_v1.json"
REPORT_PATH = REPO_ROOT / "docs/reports/frustrampnn-pinned-capability-inventory.md"
HASH_SEMANTICS = "sha256(rfc8785(document_without_top_level_content_sha256))"
EXPECTED_OPTIONS = {
    "pdb": {
        "cli_forms": ["-p", "--pdb"],
        "ownership_class": "workflow_source",
        "control_status": "typed_product_control",
        "control_kind": "governed_source_selector_or_upload",
        "api_type": "governed_artifact_reference",
        "disposition": "governed_input",
    },
    "checkpoint": {
        "cli_forms": ["-c", "--checkpoint"],
        "ownership_class": "system_runtime",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "system_pinned",
    },
    "output": {
        "cli_forms": ["-o", "--output"],
        "ownership_class": "system_storage",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "scheduler_allocated",
    },
    "chains": {
        "cli_forms": ["--chains"],
        "ownership_class": "scientific_operator",
        "control_status": "typed_product_control",
        "control_kind": "entity_or_chain_multi_selector",
        "api_type": "array_of_stable_entity_or_chain_references",
        "disposition": "operator_exposed",
    },
    "positions": {
        "cli_forms": ["--positions"],
        "ownership_class": "scientific_operator",
        "control_status": "typed_product_control",
        "control_kind": "residue_multi_selector",
        "api_type": "array_of_stable_residue_references",
        "disposition": "operator_exposed",
    },
    "device": {
        "cli_forms": ["--device"],
        "ownership_class": "scheduler_runtime",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "scheduler_assigned",
    },
    "config": {
        "cli_forms": ["--config"],
        "ownership_class": "system_compatibility",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "not_applicable_to_pinned_checkpoint",
    },
    "quiet": {
        "cli_forms": ["-q", "--quiet"],
        "ownership_class": "system_diagnostics",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "system_fixed",
    },
    "help": {
        "cli_forms": ["--help"],
        "ownership_class": "cli_diagnostics",
        "control_status": "no_product_control",
        "control_kind": None,
        "api_type": None,
        "disposition": "documentation_only",
    },
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


def test_pinned_frustrampnn_capability_inventory_is_complete_and_content_bound() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    report = REPORT_PATH.read_text(encoding="utf-8")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(inventory)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/capability_inventory_v1.schema.json")
    assert schema["properties"]["schema_version"]["const"] == 1
    _assert_closed_object_schemas(schema)

    assert inventory["schema_name"] == "frustrampnn_capability_inventory"
    assert inventory["schema_version"] == 1
    assert inventory["content_hash_semantics"] == HASH_SEMANTICS
    hash_preimage = dict(inventory)
    recorded_sha256 = hash_preimage.pop("content_sha256")
    assert recorded_sha256 == hashlib.sha256(rfc8785.dumps(hash_preimage)).hexdigest()

    runtime = inventory["runtime_identity"]
    assert runtime == {
        "image_path": FRUSTRAMPNN_RUNTIME_IDENTITY.configured_sif_path,
        "image_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        "executable_path": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path,
        "executable_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256,
        "checkpoint_id": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        "checkpoint_path": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
        "checkpoint_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
        "package_version": FRUSTRAMPNN_RUNTIME_IDENTITY.package_version,
        "source_commit": FRUSTRAMPNN_RUNTIME_IDENTITY.source_commit,
    }

    evidence_by_id = {entry["evidence_id"]: entry for entry in inventory["evidence"]}
    help_evidence = evidence_by_id["pinned_sif_predict_help"]
    assert help_evidence["image_sha256"] == FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256
    assert help_evidence["command"] == [
        "apptainer",
        "exec",
        "/mnt/BioModStack/apptainer/frustrampnn.sif",
        "/opt/venv/bin/frustrampnn",
        "predict",
        "--help",
    ]
    assert help_evidence["observed_option_keys"] == list(EXPECTED_OPTIONS)

    options = inventory["predict_options"]
    assert [option["option_key"] for option in options] == list(EXPECTED_OPTIONS)
    for option in options:
        expected = EXPECTED_OPTIONS[option["option_key"]]
        assert option["cli_forms"] == expected["cli_forms"]
        assert option["ownership_class"] == expected["ownership_class"]
        assert option["product_control"]["status"] == expected["control_status"]
        assert option["product_control"]["control_kind"] == expected["control_kind"]
        assert option["product_control"]["api_type"] == expected["api_type"]
        assert option["disposition"] == expected["disposition"]
        assert option["default_source"]
        assert "pinned_sif_predict_help" in option["validation_evidence"]
        assert option["disposition_reason"]

    for required_text in (
        "apptainer exec /mnt/BioModStack/apptainer/frustrampnn.sif /opt/venv/bin/frustrampnn predict --help",
        FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path,
        FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
        recorded_sha256,
        "No model inference was run",
    ):
        assert required_text in report
    for expected in EXPECTED_OPTIONS.values():
        assert ", ".join(f"`{form}`" for form in expected["cli_forms"]) in report

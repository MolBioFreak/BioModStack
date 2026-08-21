#!/usr/bin/env python3
"""Run authorized static/schema/hash checks for the NGS/MolBio N0 package."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker, validators
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas/ngs_molbio"
CONFIG_DIR = ROOT / "platform/api/config/ngs_molbio"
REPORT = ROOT / "docs/reports/ngs-molbio-phase-n0-contract-freeze.md"
RECEIPT = ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
LOADER = ROOT / "platform/api/services/ngs_molbio_capabilities.py"
SCRIPT = Path(__file__).resolve()

EXPECTED_COUNTS = {
    "capabilities": 21,
    "plannable": 0,
    "schemas": 77,
    "adapters": 27,
    "events": 12,
    "datasets": 16,
}
EXPECTED_OPEN_GATES = {
    "n1_binding_persistence",
    "n2_ordered_connector_persistence",
    "n3_workflow_wrappers_and_dispatch",
    "n4_operator_ui_and_agent_parity",
    "n5_payload_scanner_and_retained_audit",
    "n6_runtime_release_acceptance",
}
EXPECTED_BRANCH_GATES = {
    "n0-gate:ordered-connector-persistence",
    "n0-gate:payload-scanner-retained-audit",
}
EXPECTED_EVENT_FAMILIES = {
    "molbio_ngs.binding.acknowledged",
    "molbio_ngs.binding.health_published",
    "molbio_ngs.domain_state.initialized",
    "molbio_ngs.domain_state.revision_saved",
    "molbio_ngs.member_receipt.published",
    "molbio_ngs.sample.created",
    "molbio_ngs.sample.revision_saved",
    "molbio_ngs.reference.created",
    "molbio_ngs.reference.revision_saved",
    "molbio_ngs.reference.archived",
    "molbio_ngs.instrument_run_evidence.attached",
    "molbio_ngs.evidence.assessed",
}
EXPECTED_PROTEIN_DATASETS = {
    "protein.target_set.v1",
    "protein.template_motif_partner_control_set.v1",
    "protein.generated_candidate_cohort.v1",
    "protein.selected_finalist_cohort.v1",
    "protein.structure_prediction_validation_result_cohort.v1",
    "protein.cm_ensemble_conformer_cohort.v1",
    "protein.md_replica_analysis_cohort.v1",
    "protein.frustrampnn_landscape_guidance_cohort.v1",
    "protein.compatible_comparison_cohort.v1",
    "protein.saved_review_filter_selection.v1",
}
NEGATIVE_CASES: list[str] = []


def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AssertionError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    assert type(value) is dict, path
    return value


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: dict[str, Any], field: str = "content_sha256") -> str:
    preimage = copy.deepcopy(value)
    preimage.pop(field, None)
    return hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def expect_rejected(label: str, callback: Callable[[], Any]) -> None:
    try:
        callback()
    except Exception:
        NEGATIVE_CASES.append(label)
        return
    raise AssertionError(f"expected rejection: {label}")


def semantic_tuple(item: dict[str, Any], fields: list[str]) -> tuple[bytes, ...]:
    return tuple(rfc8785.dumps(item.get(field)) for field in fields)


def unique_by(validator: Any, fields: Any, instance: Any, schema: dict[str, Any]):
    del validator, schema
    if not isinstance(instance, list) or not isinstance(fields, list):
        return
    seen: set[tuple[bytes, ...]] = set()
    for item in instance:
        if not isinstance(item, dict):
            continue
        key = semantic_tuple(item, fields)
        if key in seen:
            from jsonschema import ValidationError

            yield ValidationError(f"duplicate semantic identity for fields {fields}")
        seen.add(key)


def unique_field(validator: Any, field: Any, instance: Any, schema: dict[str, Any]):
    if isinstance(field, str):
        yield from unique_by(validator, [field], instance, schema)


def unique_ordinal(validator: Any, enabled: Any, instance: Any, schema: dict[str, Any]):
    if enabled is True:
        yield from unique_by(validator, ["ordinal"], instance, schema)


ContractValidator = validators.extend(
    Draft202012Validator,
    {
        "x-bms-unique-by": unique_by,
        "x-bms-unique-field": unique_field,
        "x-bms-unique-ordinal": unique_ordinal,
    },
)


def registry_from(schemas: dict[str, dict[str, Any]]) -> Registry:
    registry = Registry()
    for schema_id, schema in schemas.items():
        registry = registry.with_resource(schema_id, Resource.from_contents(schema))
    return registry


def validate(value: dict[str, Any], schema: dict[str, Any], registry: Registry) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        ContractValidator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        raise AssertionError(errors[0].message)


def references(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            found.append(ref)
        for item in value.values():
            found.extend(references(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(references(item))
    return found


def payload_paths() -> list[Path]:
    paths = [REPORT, LOADER, SCRIPT]
    paths.extend(sorted(CONFIG_DIR.glob("*.json")))
    paths.extend(sorted(SCHEMA_DIR.glob("*.schema.json")))
    return sorted({path.resolve() for path in paths if path.resolve() != RECEIPT.resolve()})


def verification_command() -> list[str]:
    return ["uv", "run", "--project", "platform/api", "python", "scripts/verify_ngs_molbio_phase_n0.py"]


def main() -> int:
    os.chdir(ROOT)
    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")

    configs = {path.name: load(path) for path in sorted(CONFIG_DIR.glob("*.json"))}
    for name, document in configs.items():
        assert document["content_sha256"] == canonical_sha256(document), name

    source_pin = configs["source_pin_v1.json"]
    assert source_pin["baseline_commit"] == head
    assert source_pin["baseline_tree"] == tree
    assert git("rev-parse", f"{head}^{{tree}}") == tree
    authority_paths: set[str] = set()
    for row in source_pin["authorities"]:
        relative = row["path"]
        assert relative not in authority_paths
        authority_paths.add(relative)
        installed = ROOT / relative
        assert raw_sha256(installed) == row["sha256"]
        blob = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{head}:{relative}"])
        assert hashlib.sha256(blob).hexdigest() == row["sha256"]

    schema_registry = configs["schema_registry_v1.json"]
    assert schema_registry["baseline_source_commit"] == head
    assert schema_registry["baseline_source_tree"] == tree
    schemas: dict[str, dict[str, Any]] = {}
    schema_paths: set[str] = set()
    for row in schema_registry["entries"]:
        assert row["schema_id"] not in schemas
        assert row["path"] not in schema_paths
        schema_paths.add(row["path"])
        path = ROOT / row["path"]
        schema = load(path)
        assert schema["$id"] == row["schema_id"]
        assert raw_sha256(path) == row["schema_sha256"]
        assert hashlib.sha256(rfc8785.dumps(schema)).hexdigest() == row["schema_canonical_sha256"]
        Draft202012Validator.check_schema(schema)
        schemas[row["schema_id"]] = schema
    assert len(schemas) == EXPECTED_COUNTS["schemas"]
    registry = registry_from(schemas)
    for schema_id, schema in schemas.items():
        for reference in references(schema):
            registry.resolver().lookup(reference)

    registry_schema_ids = {
        "source_pin_v1.json": "bms.ngs-molbio.source-pin.v1",
        "schema_registry_v1.json": "bms.ngs-molbio.schema-registry.v1",
        "adapter_registry_v1.json": "bms.ngs-molbio.adapter-registry.v1",
        "event_registry_v1.json": "bms.ngs-molbio.event-registry.v1",
        "dataset_kind_registry_v1.json": "bms.ngs-molbio.dataset-kind-registry.v1",
        "constraint_payload_registry_v1.json": "bms.protein.constraint-payload-registry.v1",
        "branch_closure_v1.json": "bms.ngs-molbio.branch-closure.v1",
        "payload_ownership_manifest_v1.json": "bms.payload-ownership-manifest.v1",
        "capability_inventory_v1.json": "bms.ngs-molbio.capability-inventory.v1",
    }
    for filename, schema_id in registry_schema_ids.items():
        validate(configs[filename], schemas[schema_id], registry)

    inventory = configs["capability_inventory_v1.json"]
    bindings = {
        "source_pin_sha256": "source_pin_v1.json",
        "schema_registry_sha256": "schema_registry_v1.json",
        "adapter_registry_sha256": "adapter_registry_v1.json",
        "event_registry_sha256": "event_registry_v1.json",
        "dataset_registry_sha256": "dataset_kind_registry_v1.json",
        "constraint_payload_registry_sha256": "constraint_payload_registry_v1.json",
        "branch_closure_sha256": "branch_closure_v1.json",
        "payload_ownership_manifest_sha256": "payload_ownership_manifest_v1.json",
    }
    for field, filename in bindings.items():
        assert inventory[field] == raw_sha256(CONFIG_DIR / filename)
    assert len(inventory["capabilities"]) == EXPECTED_COUNTS["capabilities"]
    assert sum(1 for row in inventory["capabilities"] if row["plannable"]) == EXPECTED_COUNTS["plannable"]
    capability_ids: set[str] = set()
    schema_rows = {row["schema_id"]: row for row in schema_registry["entries"]}
    for capability in inventory["capabilities"]:
        assert capability["capability_id"] not in capability_ids
        capability_ids.add(capability["capability_id"])
        assert capability["inventory_sha256"] == canonical_sha256(capability, "inventory_sha256")
        assert schema_rows[capability["parameter_schema_id"]]["schema_sha256"] == capability["parameter_schema_sha256"]
        observed = set(capability["observed_parameter_keys"])
        classified = set(capability["classified_parameter_keys"])
        server = set(capability["server_owned_parameter_keys"])
        unsupported = set(capability["unsupported_parameter_keys"])
        unclassified = set(capability["unclassified_parameter_keys"])
        assert observed == classified | server | unsupported | unclassified
        assert not (classified & server or classified & unsupported or classified & unclassified or server & unsupported or server & unclassified or unsupported & unclassified)

    adapter_registry = configs["adapter_registry_v1.json"]
    assert len(adapter_registry["entries"]) == EXPECTED_COUNTS["adapters"]
    binding = adapter_registry["binding_adapter"]
    assert binding["adapter_id"] == inventory["contract_ids"]["binding_adapter"]
    assert binding["baseline_state"] == "missing"
    assert binding["implementation_owner"] is None
    binding_schema = schemas[binding["binding_receipt_schema_id"]]
    assert binding_schema["properties"]["adapter_id"]["const"] == binding["adapter_id"]
    assert binding_schema["properties"]["adapter_version"]["const"] == binding["adapter_version"]
    adapters = {row["adapter_id"]: row for row in adapter_registry["entries"]}
    assert len(adapters) == EXPECTED_COUNTS["adapters"]
    current_head = adapters["bms.ngs.ont-run-reference.adapter.v1"]
    assert current_head["allowed_dataset_roles"] == []
    assert current_head["reopen_contract"]["head_resolution_forbidden"] is False

    dataset_registry = configs["dataset_kind_registry_v1.json"]
    assert len(dataset_registry["entries"]) == EXPECTED_COUNTS["datasets"]
    dataset_ids = {row["dataset_kind"] for row in dataset_registry["entries"]}
    assert EXPECTED_PROTEIN_DATASETS <= dataset_ids
    protein_rows = [
        row for row in dataset_registry["entries"] if row["dataset_kind"] in EXPECTED_PROTEIN_DATASETS
    ]
    assert sum(row["owner_contract_state"] == "closed" for row in protein_rows) == 7
    assert sum(row["owner_contract_state"] == "unavailable" for row in protein_rows) == 3
    common_protein_rules = {
        "same_project_domain_authority",
        "exact_immutable_revision_only",
        "adapter_role_intersection",
        "no_current_head_resolution_during_preparation",
        "exact_historical_reopen",
    }
    for row in dataset_registry["entries"]:
        if row["owner_contract_state"] == "unavailable":
            assert row["enabled"] is False
            assert row["allowed_members"] == []
            assert row["compatibility_rules"] == ["no_immutable_producer_native_member_contract"]
            continue
        if row["dataset_kind"] in EXPECTED_PROTEIN_DATASETS:
            assert row["enabled"] is False
            assert common_protein_rules <= set(row["compatibility_rules"])
        assert row["allowed_members"]
        for member in row["allowed_members"]:
            adapter = adapters[member["adapter_id"]]
            assert adapter["entity_kind"] == member["receipt_kind"]
            assert set(member["allowed_roles"]) <= set(adapter["allowed_dataset_roles"])
            assert member["compatibility_rule"] in row["compatibility_rules"]

    event_registry = configs["event_registry_v1.json"]
    assert len(event_registry["entries"]) == EXPECTED_COUNTS["events"]
    event_types = {row["event_type"] for row in event_registry["entries"]}
    assert event_types == EXPECTED_EVENT_FAMILIES
    for row in event_registry["entries"]:
        assert schema_rows[row["payload_schema_id"]]["schema_sha256"] == row["payload_schema_sha256"]
        assert row["required_contract_state"] == "frozen"
        if row["event_type"] in {
            "molbio_ngs.binding.acknowledged",
            "molbio_ngs.binding.health_published",
            "molbio_ngs.member_receipt.published",
        }:
            assert row["baseline_state"] == "missing"
            assert row["implementation_owner"] is None
        else:
            assert row["baseline_state"] == "partial"

    domain_schema = schemas["bms.domain-experiment.v2"]
    refs = set(references(domain_schema))
    assert "bms.ngs-molbio-experiment.v2" in refs
    assert "bms.protein-in-silico-experiment.v2" in refs
    constraint_registry = configs["constraint_payload_registry_v1.json"]
    assert constraint_registry["entries"] == []
    assert schemas["bms.protein-constraint.v1"]["x-bms-payload-registry-state"] == "closed_empty"
    assert schemas["bms.protein-in-silico-experiment.v2"]["properties"]["design_constraints"]["maxItems"] == 0

    payload_manifest = configs["payload_ownership_manifest_v1.json"]
    assert len(payload_manifest["classes"]) == 5
    assert payload_manifest["scanner_baseline_state"] == "missing"
    assert payload_manifest["retained_audit_baseline_state"] == "missing"

    page_schema = schemas["bms.opaque-keyset-page.v1"]
    policy = page_schema["x-bms-pagination-policy"]
    assert policy["default_limit"] == 50
    assert policy["maximum_limit"] == 100
    assert policy["summary_limit_bytes"] == 262144
    assert policy["response_limit_bytes"] == 1048576
    assert policy["oversize_error"] == "response_too_large"
    assert "total" not in page_schema["required"]

    deep_link = schemas["bms.ngs-molbio.deep-link.v1"]
    selectors = {
        branch["properties"]["surface"]["const"]: branch["properties"]["selector"]["required"]
        for branch in deep_link["oneOf"]
    }
    assert selectors["pcr_experiment_revision"] == ["pcr_experiment_id", "pcr_revision_id"]
    assert selectors["sample_revision"] == ["sample_id", "sample_revision_id"]
    assert selectors["managed_reference_revision"] == ["reference_id", "reference_revision_id"]
    assert all("x-bms-deep-link-contract" in branch for branch in deep_link["oneOf"])

    branch_closure = configs["branch_closure_v1.json"]
    branch_gates = {
        row["candidate_id"]
        for row in branch_closure["entries"]
        if row["candidate_id"].startswith("n0-gate:")
    }
    assert branch_gates == EXPECTED_BRANCH_GATES

    # Static negative cases. These validate contract rejection without importing
    # the application or executing any service path.
    unavailable_row = next(
        row for row in dataset_registry["entries"] if row["owner_contract_state"] == "unavailable"
    )
    unsafe_unavailable = copy.deepcopy(dataset_registry)
    unsafe_target = next(
        row for row in unsafe_unavailable["entries"] if row["dataset_kind"] == unavailable_row["dataset_kind"]
    )
    unsafe_target["enabled"] = True
    expect_rejected(
        "unavailable_dataset_enabled",
        lambda: validate(unsafe_unavailable, schemas["bms.ngs-molbio.dataset-kind-registry.v1"], registry),
    )

    wrong_domain = copy.deepcopy(dataset_registry)
    wrong_domain["entries"][0]["allowed_domain_kinds"] = ["protein_in_silico"]
    expect_rejected(
        "ngs_dataset_wrong_domain",
        lambda: validate(wrong_domain, schemas["bms.ngs-molbio.dataset-kind-registry.v1"], registry),
    )

    protein_domain = {
        "schema": "bms.domain-experiment.v2",
        "domain_kind": "protein_in_silico",
        "domain_contract_version": "2",
        "name": "Protein contract probe",
        "objective": "Static schema dispatch probe.",
        "status": "draft",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_revision_ids": [],
        "created_by": "n0-verifier",
        "change_summary": "Probe shared dispatch.",
        "domain_payload": {
            "schema": "bms.protein-in-silico-experiment.v2",
            "experiment_mode": "analysis",
            "scientific_objective": "Probe.",
            "targets": [],
            "design_constraints": [],
            "planned_capability_ids": [],
            "comparison_groups": [],
            "validation_capability_ids": [],
            "acceptance_criteria": [],
            "evidence_plan": [],
            "source_dataset_revision_ids": [],
        },
    }
    expect_rejected(
        "protein_requires_target",
        lambda: validate(protein_domain, domain_schema, registry),
    )

    wrong_binding = copy.deepcopy(adapter_registry)
    wrong_binding["binding_adapter"]["adapter_id"] = "wrong.adapter"
    expect_rejected(
        "binding_adapter_identity",
        lambda: validate(wrong_binding, schemas["bms.ngs-molbio.adapter-registry.v1"], registry),
    )

    # Runtime loader has no Git process dependency. Build-time verifier retains
    # Git checks above.
    loader_text = LOADER.read_text(encoding="utf-8")
    assert "import subprocess" not in loader_text
    assert "def _git_value" not in loader_text
    assert "def _git_blob" not in loader_text

    rows = []
    fingerprint = hashlib.sha256()
    for path in payload_paths():
        relative = str(path.relative_to(ROOT))
        digest = raw_sha256(path)
        rows.append({"path": relative, "sha256": digest, "size_bytes": path.stat().st_size})
        fingerprint.update(f"{relative}\0{digest}\n".encode("utf-8"))

    receipt = {
        "schema": "bms.ngs-molbio.phase-n0-verification-receipt.v1",
        "baseline_commit": head,
        "baseline_tree": tree,
        "payload_files": rows,
        "payload_fingerprint_sha256": fingerprint.hexdigest(),
        "verification_command": verification_command(),
        "executed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": {
            "python": platform.python_version(),
            "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
            "git": subprocess.check_output(["git", "--version"], text=True).strip(),
            "platform": platform.platform(),
        },
        "result": {
            "verification": "not_run_by_operator_instruction",
            "n0_status": "static_contract_freeze_complete",
            **EXPECTED_COUNTS,
            "open_contract_gates": sorted(EXPECTED_OPEN_GATES),
            "negative_cases": sorted(NEGATIVE_CASES),
        },
    }
    receipt["content_sha256"] = canonical_sha256(receipt)
    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    validate(receipt, schemas["bms.ngs-molbio.phase-n0-verification-receipt.v1"], registry)
    assert receipt["content_sha256"] == canonical_sha256(receipt)

    print(
        json.dumps(
            {
                "verification": "not_run_by_operator_instruction",
                "n0_status": "static_contract_freeze_complete",
                "baseline_commit": head,
                "baseline_tree": tree,
                **EXPECTED_COUNTS,
                "open_contract_gates": sorted(EXPECTED_OPEN_GATES),
                "negative_cases": sorted(NEGATIVE_CASES),
                "payload_fingerprint_sha256": receipt["payload_fingerprint_sha256"],
                "receipt_content_sha256": receipt["content_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from experiment_services import ValidationFailure
from services.global_experiments.result_surfaces import _validate_result_surface


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "docs" / "specs" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _typed_payload(schema_id: str, payload: dict) -> dict:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "schema_id": schema_id,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "canonical_size_bytes": len(encoded),
        "payload": payload,
    }


def _route(path: str) -> dict:
    return {"template_id": "bms.route.project-manager-test.v1", "path": path, "query": {}}


def _project() -> dict:
    return {
        "schema": "bms.project.v1",
        "name": "Protein campaign",
        "description": "A bounded research project",
        "research_objective": "Understand the target interface",
        "owner": "operator",
        "contributors": ["scientist"],
        "tags": ["protein"],
        "status": "draft",
        "start_date": "2026-08-09",
        "target_end_date": None,
        "external_references": [
            {"kind": "ticket", "value": "BMS-1", "label": "tracking"}
        ],
        "created_by": "operator",
        "change_summary": "initial project",
        "needs_metadata_review": False,
    }


def _global_experiment() -> dict:
    return {
        "schema": "bms.global-experiment.v1",
        "name": "Interface study",
        "objective": "Compare candidate interfaces",
        "scientific_question": "Which interface is stable?",
        "hypothesis": None,
        "description": "An exploratory study",
        "status": "draft",
        "priority": "normal",
        "tags": ["screen"],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "comparison_plan": None,
        "success_criteria": ["Review the evidence"],
        "review_summary": None,
        "conclusion": None,
        "created_by": "operator",
        "change_summary": "initial experiment",
        "needs_metadata_review": False,
    }


def _domain_experiment() -> dict:
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": "protein_in_silico",
        "domain_contract_version": "1",
        "name": "Protein screen",
        "objective": "Generate candidates",
        "status": "draft",
        "tags": ["candidate"],
        "source_receipt_ids": [],
        "dataset_ids": [],
        "created_by": "operator",
        "change_summary": "initial domain experiment",
        "domain_payload": {
            "schema": "bms.protein-in-silico-experiment.v1",
            "experiment_mode": "design",
            "targets": [
                {
                    "target_id": "target-1",
                    "label": "Target 1",
                    "entity_receipt_ids": [],
                    "role": "target",
                }
            ],
            "scientific_objective": "Generate candidates",
            "design_constraints": [],
            "planned_capabilities": ["rfd3_local_redesign"],
            "comparison_groups": [],
            "validation_strategy": ["boltz2"],
        },
    }


def _activity_receipt() -> dict:
    return {
        "schema": "bms.activity-receipt.v1",
        "receipt_id": "receipt-activity",
        "store_id": "core",
        "entity_kind": "job",
        "entity_id": "job-1",
        "entity_revision_id": None,
        "content_digest": "a" * 64,
        "contract_digest": None,
        "source_build_revision": "build-1",
        "verified_at": "2026-08-09T12:00:00Z",
        "verifier_id": "server.adapter",
        "reopen_uri": "/api/jobs/job-1",
        "canonical_state": "completed",
        "normalized_state": "completed",
        "observed_at": "2026-08-09T12:00:00Z",
        "metadata": {},
    }


def _result_receipt() -> dict:
    return {
        "schema": "bms.result-receipt.v1",
        "receipt_id": "receipt-result",
        "store_id": "core",
        "entity_kind": "protein_result",
        "entity_id": "result-1",
        "entity_revision_id": "revision-1",
        "content_digest": "b" * 64,
        "contract_digest": "c" * 64,
        "source_build_revision": "build-1",
        "verified_at": "2026-08-09T12:00:00Z",
        "verifier_id": "server.adapter",
        "reopen_uri": "/api/results/result-1",
        "availability": "available",
        "metadata": {},
    }


def _surface() -> dict:
    return {
        "schema": "bms.result-surface.v1",
        "receipt_id": "receipt-result",
        "entity_kind": "protein_result",
        "entity_id": "result-1",
        "contract_id": "bms.protein.result.v1",
        "content_digest": "b" * 64,
        "surface_kind": "protein_design",
        "route": _route("/results/result-1"),
        "readiness": "ready",
        "native_summary": _typed_payload("bms.result-summary.test.v1", {}),
        "scientific_acceptance": {"state": "review", "reason": None},
        "provenance": _typed_payload("bms.result-provenance.test.v1", {}),
        "comparison": {"state": "not_applicable", "reason": None, "authority": None},
        "available_actions": ["open"],
    }


def _external_receipt() -> dict:
    return {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "rfd3_job",
        "entity_id": "job-1",
        "entity_revision_id": "request-1",
        "content_digest": None,
        "contract_digest": "d" * 64,
        "source_build_revision": "build-1",
        "verified_at": "2026-08-09T12:00:00Z",
        "verifier_id": "global.rfd3.v1",
        "reopen_route": _route("/designs/job-1"),
        "metadata": _typed_payload("bms.external-entity-metadata.test.v1", {}),
    }


def _run_clone_request() -> dict:
    return {
        "schema": "bms.run-clone-request.v1",
        "source_run_id": "run-1",
        "source_attempt_id": "attempt-1",
        "new_workflow_name": "Cloned ubiquitin intent",
        "change_summary": "Clone exact immutable intent",
        "expected_domain_revision_id": "domain-revision-1",
        "expected_run_group_generation": 3,
        "idempotency_key": "12345678-1234-4567-8901-123456789012",
    }


def _read_model() -> dict:
    return {
        "schema": "bms.project-manager.read-model.v1",
        "subject_id": "project-1",
        "subject_generation": 1,
        "assembled_at": "2026-08-09T12:00:00Z",
        "source_receipt_ids": [],
        "source_digest_set_sha256": "e" * 64,
        "adapter_versions": [],
        "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
        "counts": {},
        "status_summary": {},
        "recent_activity": [],
        "result_previews": [],
        "pagination": {},
        "project": {
            "id": "project-1",
            "project_scope": "global",
            "name": "Project",
            "objective": "Objective",
            "lifecycle_state": "active",
            "head_generation": 1,
            "current_revision_id": "revision-1",
            "updated_at": "2026-08-09T12:00:00Z",
        },
        "tree": {"nodes": []},
        "map": {"focus_node_key": "project:project-1", "nodes": [], "edges": [], "truncated": False, "next_cursor": None},
        "selection": {
            "node_key": "project:project-1",
            "node_type": "project",
            "title": "Project",
            "subtitle": None,
            "canonical_identity": {},
            "summary": {},
            "relationship": {},
            "scientific_context": {},
            "reconciliation": {},
            "available_actions": [],
            "canonical_surface": None,
        },
        "runs": {"items": [], "next_cursor": None},
        "warnings": [],
        "allowed_actions": [],
    }


def _launch_context() -> dict:
    return {
        "schema": "bms.launch-context.v1",
        "launch_context_id": "launch-context-1",
        "project_id": "project-1",
        "global_experiment_id": "experiment-1",
        "domain_experiment_id": "domain-1",
        "workflow_id": None,
        "workflow_revision_id": None,
        "return_uri": "/projects/project-1",
        "source_receipt_id": "receipt-1",
        "state": "issued",
        "issued_at": "2026-08-09T12:00:00Z",
        "expires_at": "2026-08-09T12:05:00Z",
    }


@pytest.mark.parametrize(
    ("filename", "document"),
    [
        ("project-v1.schema.json", _project),
        ("global-experiment-v1.schema.json", _global_experiment),
        ("domain-experiment-v1.schema.json", _domain_experiment),
        ("external-entity-receipt-v1.schema.json", _external_receipt),
        ("project-manager-read-model-v1.schema.json", _read_model),
        ("activity-receipt-v1.schema.json", _activity_receipt),
        ("result-receipt-v1.schema.json", _result_receipt),
        ("result-surface-v1.schema.json", _surface),
        ("launch-context-v1.schema.json", _launch_context),
        ("run-clone-request-v1.schema.json", _run_clone_request),
    ],
)
def test_phase_zero_contract_schema_is_valid_and_closed(filename: str, document) -> None:
    schema = _schema(filename)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({**document(), "unexpected": True})


def test_domain_schema_freezes_supported_kinds() -> None:
    schema = _schema("domain-experiment-v1.schema.json")
    invalid = {**_domain_experiment(), "domain_kind": "liquid_handler"}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


def test_runtime_result_surface_validation_fails_closed() -> None:
    malformed = {**_surface(), "surface_kind": "invented_viewer"}
    with pytest.raises(ValidationFailure):
        _validate_result_surface(malformed)
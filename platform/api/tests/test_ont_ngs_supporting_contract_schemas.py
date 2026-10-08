from __future__ import annotations

import json
import copy
import hashlib
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = REPO_ROOT / "schemas" / "ngs"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "ont_fastq_qc_result_retry3_v1.json"
SPEC_PACKAGE_PATH = REPO_ROOT / "docs" / "specs" / "2026-08-20-ont-fastq-qc-result-recovery-spec-package.json"


def test_spec_package_binds_every_current_normative_byte() -> None:
    package = json.loads(SPEC_PACKAGE_PATH.read_text(encoding="utf-8"))
    for record in package["records"]:
        raw = (REPO_ROOT / record["path"]).read_bytes()
        assert record["size_bytes"] == len(raw), record["path"]
        assert record["sha256"] == hashlib.sha256(raw).hexdigest(), record["path"]
    preimage = {key: value for key, value in package.items() if key != "package_sha256"}
    assert package["package_sha256"] == hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


@pytest.mark.parametrize(
    ("name", "schema_id"),
    [
        (
            "ont_fastq_qc_reconciliation_receipt_v1.schema.json",
            "https://biomodstack.local/schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json",
        ),
        (
            "ont_alignment_session_v1.schema.json",
            "https://biomodstack.local/schemas/ngs/ont_alignment_session_v1.schema.json",
        ),
        (
            "ont_ngs_error_v1.schema.json",
            "https://biomodstack.local/schemas/ngs/ont_ngs_error_v1.schema.json",
        ),
        (
            "ont_fastq_qc_final_acceptance_receipt_v1.schema.json",
            "https://biomodstack.local/schemas/ngs/ont_fastq_qc_final_acceptance_receipt_v1.schema.json",
        ),
        ("ont_ngs_rotation_success_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_ngs_rotation_success_v1.schema.json"),
        ("ont_ngs_capability_revocation_success_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_ngs_capability_revocation_success_v1.schema.json"),
        ("ont_fastq_qc_evidence_bundle_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_fastq_qc_evidence_bundle_v1.schema.json"),
        ("ont_fastq_qc_gate_evidence_body_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_fastq_qc_gate_evidence_body_v1.schema.json"),
        ("ont_fastq_qc_browser_evidence_manifest_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_fastq_qc_browser_evidence_manifest_v1.schema.json"),
        ("ont_fastq_qc_independent_review_receipt_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_fastq_qc_independent_review_receipt_v1.schema.json"),
        ("ont_fastq_qc_deployment_receipt_v1.schema.json", "https://biomodstack.local/schemas/ngs/ont_fastq_qc_deployment_receipt_v1.schema.json"),
    ],
)
def test_supporting_contract_schema_is_present_closed_and_valid(name: str, schema_id: str) -> None:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == schema_id
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_runtime_error_model_matches_the_normative_closed_enums() -> None:
    from routers.ngs_alignment_sessions import OntNgsErrorV1

    normative = _schema("ont_ngs_error_v1.schema.json")
    runtime = OntNgsErrorV1.model_json_schema(by_alias=True)
    assert runtime["properties"]["code"]["enum"] == normative["properties"]["code"]["enum"]
    assert runtime["properties"]["resource"]["enum"] == normative["properties"]["resource"]["enum"]


def test_governed_error_contract_rejects_unknown_codes_and_extra_fields() -> None:
    validator = Draft202012Validator(_schema("ont_ngs_error_v1.schema.json"), format_checker=FormatChecker())
    value = {
        "schema": "bms.ngs.error.v1",
        "code": "NGS_CAPABILITY_DENIED",
        "message": "Alignment access denied.",
        "job_id": "31f02bd5-830f-4558-aa78-3873c515de68",
        "resource": "result",
        "retryable": True,
    }
    assert list(validator.iter_errors(value)) == []
    assert list(validator.iter_errors({**value, "code": "UNKNOWN"}))
    assert list(validator.iter_errors({**value, "path": "/private/result"}))


def test_reconciliation_receipt_schema_requires_acyclic_digest_fields() -> None:
    schema = _schema("ont_fastq_qc_reconciliation_receipt_v1.schema.json")
    required = set(schema["required"])
    assert {
        "completed_stages_preimage_sha256",
        "stage_outputs_preimage_sha256",
        "provenance_preimage_sha256",
        "completed_stages_postimage_sha256",
        "stage_outputs_postimage_sha256",
        "receipt_free_provenance_postimage_sha256",
        "receipt_sha256",
    }.issubset(required)
    assert "provenance_postimage_sha256" not in required


def test_alignment_session_schema_has_ready_and_unavailable_state_branches() -> None:
    schema = _schema("ont_alignment_session_v1.schema.json")
    session = schema["$defs"]["alignmentSession"]
    assert len(session["oneOf"]) == 2


def test_final_acceptance_receipt_requires_all_seventeen_ordered_gate_rows() -> None:
    schema = _schema("ont_fastq_qc_final_acceptance_receipt_v1.schema.json")
    gates = schema["properties"]["gates"]
    assert gates["minItems"] == 17
    assert gates["maxItems"] == 17
    assert len(gates["prefixItems"]) == 17


def test_current_spec_package_closes_new_review_findings() -> None:
    result = _schema("ont_fastq_qc_result_v1.schema.json")
    alignment = _schema("ont_alignment_session_v1.schema.json")
    final = _schema("ont_fastq_qc_final_acceptance_receipt_v1.schema.json")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert result["x-bms-semantic-validators"] == [
        "bms.ngs.fastq-qc-result-construction-validator.v1",
        "bms.ngs.fastq-qc-result-wire-validator.v1",
    ]
    assert fixture["alignment_sessions"][0]["session_id"] == "de4e4d2b2062fbf21ddfee65"
    assert fixture["coverage"]["construction_attestation"]["source_row_count"] == 5570
    assert [row["display_order"] for row in fixture["artifacts"]] == list(range(1, 37))
    assert all("filename_extension" in row for row in fixture["artifacts"])
    assert "alignment_pair_sha256" in alignment["$defs"]["readySession"]["required"]
    sessions = alignment["$defs"]["listEnvelope"]["properties"]["sessions"]
    assert sessions["items"] is False
    assert len(sessions["prefixItems"]) == 2
    assert final["x-bms-semantic-validator"] == "bms.ont-fastq-qc-final-acceptance-verifier.v1"
    assert final["properties"]["evidence_bundles"]["minItems"] == 17
    assert final["properties"]["reviews"]["minItems"] == 3
    assert final["properties"]["reviews"]["maxItems"] == 3

    validator = Draft202012Validator(result, format_checker=FormatChecker())
    wrong_stage = copy.deepcopy(fixture)
    wrong_stage["stages"][0]["output_count"] = 4
    assert list(validator.iter_errors(wrong_stage))
    dimer_only = copy.deepcopy(fixture)
    dimer_only["alignment_sessions"][0]["mode"] = "dimer_candidates"
    assert list(validator.iter_errors(dimer_only))
    truncated = copy.deepcopy(fixture)
    truncated["artifacts"] = []
    assert list(validator.iter_errors(truncated))

    profile = fixture["verification"]["threshold_profile"]
    encoded = json.dumps(profile["values"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == profile["sha256"]

    gate_bodies = _schema("ont_fastq_qc_gate_evidence_body_v1.schema.json")
    by_name = {
        branch["properties"]["evidence_name"]["const"]: branch["properties"]["observed"]
        for branch in gate_bodies["oneOf"]
    }
    assert by_name["result_response"]["properties"]["response_schema"]["const"] == "bms.ngs.fastq-qc-result.v1"
    rotation = by_name["rotation_trace"]
    assert rotation["properties"]["rotation_count"]["const"] == 1
    assert [event["event"] for event in rotation["properties"]["events"]["const"]] == [
        "initial_result_denied",
        "rotation_succeeded",
        "protected_result_retry_succeeded",
        "sessions_list_loaded",
        "primary_session_loaded",
        "artifact_head_loaded",
        "capability_revoked",
    ]
    authorization = by_name["authorization_matrix"]
    assert authorization["properties"]["positive_count"]["const"] == 2
    assert authorization["properties"]["negative_count"]["const"] == 24
    assert len(authorization["properties"]["cases"]["const"]) == 26
    assert sum(case["operation"] == "rotate" for case in authorization["properties"]["cases"]["const"]) == 12
    full_download = by_name["full_download"]["properties"]
    assert full_download["artifact_sha256"]["const"] == full_download["body_sha256"]["const"]
    assert full_download["artifact_id"]["const"] != full_download["artifact_sha256"]["const"]
    assert full_download["content_length"]["const"] == full_download["size_bytes"]["const"] == 305396924
    assert full_download["etag"]["const"] == f'"sha256:{full_download["artifact_sha256"]["const"]}"'

    assert all(item["artifact_id"] != item["sha256"] for item in fixture["artifacts"] if item["state"] == "present")
    assert all(item["url"].endswith(item["artifact_id"]) for item in fixture["artifacts"] if item["state"] == "present")
    assert [item["owner_scope"] for item in fixture["artifacts"] if item["kind"] == "source_reads_fastq"] == [
        "managed_input_snapshot"
    ]
    assert all(
        item["owner_scope"] == "result_root"
        for item in fixture["artifacts"]
        if item["kind"] != "source_reads_fastq"
    )

    package_manifest = by_name["package_manifest"]["properties"]
    assert len(package_manifest["content"]["const"]["descriptors"]) == 36
    assert package_manifest["content"]["const"]["job_id"] == fixture["job"]["id"]
    a3 = by_name["result_response"]["properties"]
    assert a3["job_id"]["const"] == fixture["job"]["id"]
    response_contract = a3["response"]["const"]
    normative_result_schema = json.loads(
        (SCHEMA_ROOT / "ont_fastq_qc_result_v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(
        normative_result_schema,
        format_checker=FormatChecker(),
    ).validate(response_contract)
    assert a3["body_sha256"]["const"] == hashlib.sha256(
        json.dumps(response_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert "receipt" in by_name["reconciliation_receipt"]["properties"]
    assert "trace" in by_name["transfer_audit"]["properties"]
    isolation_required = set(by_name["isolation_audit"]["required"])
    assert {
        "jobs_before",
        "jobs_after",
        "artifacts_before",
        "artifacts_after",
        "production_audit",
        "worktree_before",
        "worktree_after",
    } <= isolation_required
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import ont_ngs_results as service  # noqa: E402

FIXTURE_PATH = Path(__file__).parent / "fixtures/ont_fastq_qc_result_retry3_v1.json"
SCHEMA_PATH = REPO_ROOT / "schemas/ngs/ont_fastq_qc_result_v1.schema.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_file_projection_is_producer_bound_and_contract_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_root = Path(
        "/home/dalab/.biomodstack-dev/bms_results/"
        "BFX6NB_Q2-01_ont_fastq_qc_acceptance_retry3_20260816_175924"
    )
    source_fastq = Path(
        "/home/dalab/.biomodstack-dev/inputs/ngs/bfx6nb-fastq-qc/"
        "BFX6NB_1_JAN26-EL-Q2-01.fastq.gz"
    )
    runtime_sif = Path("/mnt/BioModStack/dev/apptainer/dorado-v1.3.1-samtools-v1.24.sif")
    if not result_root.is_dir() or not source_fastq.is_file() or not runtime_sif.is_file():
        pytest.skip("Development retry3 acceptance package is unavailable")
    monkeypatch.setenv("BMS_RESULTS_DIR", str(result_root.parent))
    monkeypatch.setenv("BMS_RESULTS_ROOT", str(result_root.parent))
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(runtime_sif))

    job_id = "31f02bd5-830f-4558-aa78-3873c515de68"
    reference_sha256 = "0185e3475f9e04c996d2bd2667f83d8655fb12b1e426bc5b674261ac4b2f3be4"
    source_sha256 = "957a1c7fb5a4f10089f52b8b26cee37527176575b99ecc5e81a139c1374d8fff"
    authority = {
        "artifact_set_sha256": "e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650",
        "declared_artifact_count": 36,
        "present_artifact_count": 34,
        "unavailable_artifact_count": 2,
        "sequence_qc_manifest_sha256": "e37f0225c2c7db017b5a3be95bc3a1fb83797918268c3a838a390d1d5378b06b",
        "construct_verification_manifest_sha256": "3d2aa73270c11fe692ed8116aeb86d0f9fd96496da45933fcffb8c8de8a42a38",
        "reference_sequence_sha256": reference_sha256,
    }
    stage_counts = {"fastq_align": 5, "dimer_qc": 6, "fastq_qc": 8, "construct_verification": 6}
    stage_outputs = {
        stage: [f"bms_results/{result_root.name}/{stage}/output-{index}" for index in range(count)]
        for stage, count in stage_counts.items()
    }
    reconciliation = {
        "schema": "bms.ont-fastq-qc-reconciliation.v1",
        "job_id": job_id,
        "workflow_id": "ont_fastq_qc",
        "input_mode": "fastq",
        "reference_sequence_sha256": reference_sha256,
        "source_fastq_sha256": source_sha256,
        "resource_evidence_status": "historical_unavailable",
        "sequence_qc_manifest_sha256": authority["sequence_qc_manifest_sha256"],
        "verification_manifest_sha256": authority["construct_verification_manifest_sha256"],
        "artifact_set_sha256": authority["artifact_set_sha256"],
        "declared_artifact_count": 36,
        "present_artifact_count": 34,
        "unavailable_artifact_count": 2,
    }
    job = SimpleNamespace(
        id=job_id,
        model_id="nanopore",
        mode="fastq",
        name="BFX6NB_Q2-01_ont_fastq_qc_acceptance_retry3",
        status="completed",
        queue_status="completed",
        created_at=datetime.fromisoformat("2026-08-16T22:59:24.795156+00:00"),
        started_at=datetime.fromisoformat("2026-08-16T22:59:26.899086+00:00"),
        completed_at=datetime.fromisoformat("2026-08-16T23:11:45.354530+00:00"),
        error_message=None,
        output_dir=str(result_root),
        child_output_dir=None,
        assigned_gpu=None,
        params={
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "reference_sequence_sha256": reference_sha256,
            "fastq_path": str(source_fastq),
        },
        provenance={
            "coverage_construction_attestation": {
                "projection_sha256": "ab6715d94f85b4fb2d36c435a485e1bcdd6cc924f8ee45a27487babb355cb068",
                "source_row_count": 5570,
                "source_rows_sha256": "faee870c42c54e38ac79b7c022b167f2af56afe6e65cc157da4105e9f313cdd9",
                "validated_at": "2026-08-16T23:07:27Z",
                "validator": "bms.ngs.fastq-qc-result-construction-validator.v1",
            },
            "ont_fastq_qc_reconciliation_v1": reconciliation,
            "stage_terminal_states": {
                stage: {"status": "complete", "outputs": outputs}
                for stage, outputs in stage_outputs.items()
            },
        },
        completed_stages=list(stage_counts),
        stage_outputs=stage_outputs,
    )
    monkeypatch.setattr(service, "validate_persisted_reconciliation_receipt", lambda _receipt: None)

    projection = service._build_file_projection(job)

    assert projection["schema"] == "bms.ngs.fastq-qc-result.v1"
    assert projection["authority"]["artifact_set_sha256"] == authority["artifact_set_sha256"]
    assert len(projection["artifacts"]) == 36
    assert {session["mode"] for session in projection["alignment_sessions"]} == {
        "primary",
        "dimer_candidates",
    }


def test_retry3_projection_fixture_conforms_to_normative_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(_fixture()))
    assert errors == []


def test_retry3_fixture_exposes_historical_resource_evidence_as_a_discriminated_branch() -> None:
    resources = _fixture()["execution_resources"]
    assert resources["evidence_status"] == "historical_unavailable"
    assert resources["receipt_schema"] is None
    assert resources["receipt_id"] is None
    assert resources["receipt_sha256"] is None


def test_schema_rejects_timezone_free_lifecycle_timestamp() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    value = _fixture()
    value["job"]["completed_at"] = "2026-08-16T23:11:45.354530"

    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    assert errors


def test_schema_rejects_unavailable_artifact_without_a_reason() -> None:
    value = _fixture()
    artifact = next(item for item in value["artifacts"] if item["state"] != "present")
    artifact["unavailable_reason"] = None

    with pytest.raises(service.OntNgsResultError, match="result schema is invalid"):
        service.validate_ont_fastq_qc_result_contract(value)


def test_schema_rejects_present_artifact_with_an_unavailable_reason() -> None:
    value = _fixture()
    artifact = next(item for item in value["artifacts"] if item["state"] == "present")
    artifact["unavailable_reason"] = "invented absence"

    with pytest.raises(service.OntNgsResultError, match="result schema is invalid"):
        service.validate_ont_fastq_qc_result_contract(value)


def test_schema_rejects_ready_session_with_an_unavailable_reason() -> None:
    value = _fixture()
    session = next(item for item in value["alignment_sessions"] if item["ready"] is True)
    session["unavailable_reason"] = "contradictory"

    with pytest.raises(service.OntNgsResultError, match="result schema is invalid"):
        service.validate_ont_fastq_qc_result_contract(value)


Mutation = Callable[[dict], None]


def _artifact_count_drift(value: dict) -> None:
    value["authority"]["present_artifact_count"] -= 1


def _foreign_artifact_url(value: dict) -> None:
    artifact = next(item for item in value["artifacts"] if item["state"] == "present")
    artifact["url"] = artifact["url"].replace(value["job"]["id"], "00000000-0000-0000-0000-000000000000")


def _histogram_count_drift(value: dict) -> None:
    value["read_length_histogram"]["bins"][0]["read_count"] += 1


def _histogram_edge_drift(value: dict) -> None:
    value["read_length_histogram"]["bins"][1]["start_bp"] += 1


def _coverage_order_drift(value: dict) -> None:
    value["coverage"]["points"][0], value["coverage"]["points"][1] = (
        value["coverage"]["points"][1],
        value["coverage"]["points"][0],
    )


def _coverage_minimum_drift(value: dict) -> None:
    value["coverage"]["minimum_depth"] += 1


def _coverage_bucket_width_drift(value: dict) -> None:
    value["coverage"]["bucket_width_rows"] += 1


def _reference_identity_drift(value: dict) -> None:
    value["verification"]["summary"]["reference_name"] = "foreign_contig"


def _variant_count_drift(value: dict) -> None:
    value["verification"]["summary"]["variant_count"] += 1


def _variant_interval_drift(value: dict) -> None:
    value["verification"]["variants"][0]["affected_end_1based"] = 1


def _variant_cross_origin_drift(value: dict) -> None:
    variant = value["verification"]["variants"][0]
    variant["record_end_1based"] = value["summary"]["reference_length"] + 1
    variant["affected_end_1based"] = value["summary"]["reference_length"] + 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_artifact_count_drift, "artifact.count"),
        (_foreign_artifact_url, "artifact URL"),
        (_histogram_count_drift, "histogram count"),
        (_histogram_edge_drift, "histogram bins"),
        (_coverage_order_drift, "coverage order"),
        (_coverage_minimum_drift, "coverage minimum"),
        (_coverage_bucket_width_drift, "coverage bucket width"),
        (_reference_identity_drift, "reference identity"),
        (_variant_count_drift, "variant count"),
        (_variant_interval_drift, "variant interval"),
        (_variant_cross_origin_drift, "variant interval"),
    ],
)
def test_runtime_contract_rejects_cross_field_drift(mutate: Mutation, message: str) -> None:
    validate = getattr(service, "validate_ont_fastq_qc_result_contract", None)
    assert callable(validate)
    value = copy.deepcopy(_fixture())
    mutate(value)

    with pytest.raises(service.OntNgsResultError, match=message):
        validate(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("screen_basis", "taxonomic_contamination_screen"),
        ("organism_identity_claimed", True),
    ],
)
def test_schema_rejects_expected_reference_screen_overclaim(field: str, replacement: object) -> None:
    value = _fixture()
    value["verification"]["checks"]["expected_reference_screen"]["metrics"][field] = replacement

    with pytest.raises(service.OntNgsResultError, match="result schema is invalid"):
        service.validate_ont_fastq_qc_result_contract(value)


def test_ngs_result_openapi_publishes_the_normative_closed_contract() -> None:
    from fastapi import FastAPI
    from routers import ngs_alignment_sessions as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    document = app.openapi()
    response_schema = document["paths"]["/api/jobs/{job_id}/ngs-result"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    reference = response_schema.get("$ref")
    assert isinstance(reference, str) and reference.startswith("#/components/schemas/")
    component_name = reference.rsplit("/", 1)[-1]
    component = document["components"]["schemas"][component_name]
    assert component["$id"] == "https://biomodstack.local/schemas/ngs/ont_fastq_qc_result_v1.schema.json"
    assert component["additionalProperties"] is False
    assert set(component["required"]) == {
        "schema", "job", "authority", "summary", "alignment", "read_length_histogram",
        "coverage", "verification", "stages", "execution_resources", "artifacts", "alignment_sessions",
    }
    assert "$defs" in component

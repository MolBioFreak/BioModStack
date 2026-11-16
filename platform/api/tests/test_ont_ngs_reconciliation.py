from __future__ import annotations

import importlib
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Job

JOB_ID = "31f02bd5-830f-4558-aa78-3873c515de68"
REFERENCE_SHA256 = "0185e3475f9e04c996d2bd2667f83d8655fb12b1e426bc5b674261ac4b2f3be4"
SOURCE_FASTQ_SHA256 = "957a1c7fb5a4f10089f52b8b26cee37527176575b99ecc5e81a139c1374d8fff"
SEQUENCE_QC_MANIFEST_SHA256 = "e37f0225c2c7db017b5a3be95bc3a1fb83797918268c3a838a390d1d5378b06b"
VERIFICATION_MANIFEST_SHA256 = "3d2aa73270c11fe692ed8116aeb86d0f9fd96496da45933fcffb8c8de8a42a38"
ARTIFACT_SET_SHA256 = "e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650"
STAGES = ["fastq_align", "dimer_qc", "fastq_qc", "construct_verification"]
RESULT_PREFIX = "bms_results/retry3"
SOURCE_SNAPSHOT = {
    "schema": "bms.sqlite-backup-source-preimage.v1",
    "database_identity_sha256": "8" * 64,
    "source_size_bytes": 8192,
    "source_sha256": "a" * 64,
    "page_size": 4096,
    "page_count": 2,
    "schema_version": 7,
    "data_version": 3,
    "integrity_check": "ok",
    "foreign_key_violations": 0,
}
STAGE_OUTPUTS = {
    "fastq_align": [
        f"{RESULT_PREFIX}/align/aligned.bam",
        f"{RESULT_PREFIX}/align/aligned.bam.bai",
        f"{RESULT_PREFIX}/align/reference.fasta",
        f"{RESULT_PREFIX}/align/reference.fasta.fai",
        f"{RESULT_PREFIX}/align/fastq_align.log",
    ],
    "dimer_qc": [
        f"{RESULT_PREFIX}/multimer_qc/dimer_breakpoint_call.tsv",
        f"{RESULT_PREFIX}/multimer_qc/dimer_evidence_by_position.tsv",
        f"{RESULT_PREFIX}/multimer_qc/dimer_read_events.tsv",
        f"{RESULT_PREFIX}/multimer_qc/dimer_breakpoint_sequences.tsv",
        f"{RESULT_PREFIX}/multimer_qc/dimer_secondary_anomalies.tsv",
        f"{RESULT_PREFIX}/multimer_qc/dimer_secondary_summary.tsv",
    ],
    "fastq_qc": [
        f"{RESULT_PREFIX}/fastq_qc/read_lengths.tsv",
        f"{RESULT_PREFIX}/fastq_qc/fastq_qc_summary.tsv",
        f"{RESULT_PREFIX}/fastq_qc/fastq_alignment_stats.tsv",
        f"{RESULT_PREFIX}/fastq_qc/fastq_coverage.tsv",
        f"{RESULT_PREFIX}/fastq_qc/per_base_support.tsv",
        f"{RESULT_PREFIX}/fastq_qc/qc_manifest.json",
        f"{RESULT_PREFIX}/fastq_qc/igv_report.html",
        f"{RESULT_PREFIX}/fastq_qc/fastq_consensus.fasta",
    ],
    "construct_verification": [
        f"{RESULT_PREFIX}/verification/qc_manifest.json",
        f"{RESULT_PREFIX}/verification/verification_summary.tsv",
        f"{RESULT_PREFIX}/verification/variants.vcf",
        f"{RESULT_PREFIX}/verification/per_base_metrics.tsv",
        f"{RESULT_PREFIX}/verification/evidence.html",
        f"{RESULT_PREFIX}/verification/topology_evidence.json",
    ],
}


def _hierarchy_record() -> dict:
    document = {
        "schema": "biomodstack.ont-fastq-qc-hierarchy-authority.v1",
        "job": {"id": JOB_ID, "workflow_id": "ont_fastq_qc", "input_mode": "fastq"},
        "project": {"id": "4af72c1d-27d8-4e14-8f39-4259a80494a0"},
        "global_experiment": {"id": "9a10c5a8-b233-4bf3-af14-9c2880525278"},
        "domain_experiment": {
            "id": "916a611b-6879-486f-bf9e-e1b5a796e01c",
            "state_revision_id": "molbio_ngs_state_revision_5922d66c-d4fe-44e8-bdc1-1b81c26449c1",
        },
        "member": {"receipt_id": "195b526d-35b3-40e4-b400-e8e4232a98fc"},
        "sample": {"revision_id": "molbio_ngs_sample_revision_b81cd561-e18d-4fab-9c48-f8aa40f45e19"},
        "reference": {
            "revision_id": "molbio_ngs_reference_revision_1f508b7f-15f1-482a-9148-c3b2054ca56d",
            "normalized_sequence_sha256": REFERENCE_SHA256,
        },
        "source_fastq": {
            "sha256": SOURCE_FASTQ_SHA256,
            "artifact_set_sha256": ARTIFACT_SET_SHA256,
            "sequence_qc_manifest_sha256": SEQUENCE_QC_MANIFEST_SHA256,
            "verification_manifest_sha256": VERIFICATION_MANIFEST_SHA256,
        },
    }
    return {
        "schema": "biomodstack.alignment-hierarchy-authority.v1",
        "digest": hashlib.sha256(rfc8785.dumps(document)).hexdigest(),
        "document": document,
    }


def _service():
    return importlib.import_module("services.ont_ngs_reconciliation")


def _job(**overrides):
    values = {
        "id": JOB_ID,
        "status": "completed",
        "queue_status": "completed",
        "awaiting_input": False,
        "awaiting_stage": None,
        "awaiting_payload": {},
        "model_id": "nanopore",
        "mode": "fastq",
        "params": {
            "ont_workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "reference_sequence_sha256": REFERENCE_SHA256,
        },
        "output_dir": "/result/retry3",
        "completed_stages": ["dimer_qc"],
        "stage_outputs": {},
        "provenance": {
            "existing_history": {"immutable": True},
            "stage_terminal_states": {stage: {"status": "complete", "outputs": STAGE_OUTPUTS[stage]} for stage in STAGES},
        },
        "current_stage": "dimer_qc",
        "stage_progress": None,
        "error_message": None,
        "started_at": datetime(2026, 8, 16, 17, 59, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 8, 16, 18, 4, tzinfo=timezone.utc),

    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_protected_row_digest_uses_only_explicit_reconciliation_fields() -> None:
    from services import ont_ngs_reconciliation as service

    job = _job()
    preimage = service._protected_preimage(job)
    assert tuple(preimage) == (
        "schema",
        "job_id",
        "status",
        "queue_status",
        "awaiting_input",
        "paused",
        "completed_at",
        "params",
        "provenance",
        "completed_stages",
        "stage_outputs",
        "output_dir",
        "error_message",
    )
    before = service.reconciliation_authority_digest("protected-row-preimage", preimage)
    job.name = "presentation-only-change"
    assert service.reconciliation_authority_digest(
        "protected-row-preimage", service._protected_preimage(job)
    ) == before
    job.status = "failed"
    assert service.reconciliation_authority_digest(
        "protected-row-preimage", service._protected_preimage(job)
    ) != before


FIXTURE = Path(__file__).parent / "fixtures" / "ont_fastq_qc_reconciliation_receipt_v1.json"
PACKAGE_KEYS = {
    "workflow_id", "input_mode", "reference_sequence_sha256", "source_fastq_sha256",
    "resource_evidence_status", "sequence_qc_manifest_sha256", "verification_manifest_sha256",
    "artifact_set_sha256", "declared_artifact_count", "present_artifact_count",
    "unavailable_artifact_count", "result_root_identity_sha256",
}


def _saved():
    saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    job = SimpleNamespace(**saved["job"])
    receipt = job.provenance["ont_fastq_qc_reconciliation_v1"]
    return job, receipt, saved["provenance_preimage"]


def test_a_saved_receipt_still_reads_against_its_result() -> None:
    service = _service()
    job, receipt, preimage = _saved()
    package = {key: receipt[key] for key in PACKAGE_KEYS}
    service.validate_persisted_reconciliation_receipt(receipt, job=job, expected_package=package, provenance_preimage=preimage)
    # Access-token rotation after the receipt was written does not unbind it.
    job.provenance = {
        key: value for key, value in job.provenance.items()
        if key not in {"ont_fastq_qc_hierarchy_v1", "alignment_access_token_sha256"}
    } | {"alignment_access_scheme": "opaque_job_capability_v1", "alignment_access_rotation_count": 20, "alignment_access_revoked": True}
    service.validate_persisted_reconciliation_receipt(receipt, job=job, expected_package=package, provenance_preimage=preimage)


@pytest.mark.parametrize("change", ["outputs", "package", "status", "receipt"])
def test_a_saved_receipt_that_no_longer_matches_its_result_is_refused(change: str) -> None:
    service = _service()
    job, receipt, preimage = _saved()
    package = {key: receipt[key] for key in PACKAGE_KEYS}
    if change == "outputs":
        job.stage_outputs = {**job.stage_outputs, "fastq_align": ["foreign/output"]}
    elif change == "package":
        package["artifact_set_sha256"] = "0" * 64
    elif change == "status":
        job.status = "failed"
    else:
        receipt = {**receipt, "job_id": "another-job"}
    with pytest.raises(service.OntFastqQcReconciliationError):
        service.validate_persisted_reconciliation_receipt(receipt, job=job, expected_package=package, provenance_preimage=preimage)


def test_nothing_writes_reconciliation_receipts_any_more() -> None:
    service = _service()
    for name in ("build_ont_fastq_qc_reconciliation_plan", "bind_ont_fastq_qc_reconciliation_backup",
                 "apply_ont_fastq_qc_reconciliation_plan", "collect_ont_fastq_qc_reconciliation_evidence"):
        assert not hasattr(service, name), name
    assert not (Path(__file__).resolve().parents[1] / "scripts" / "reconcile_ont_fastq_qc_job.py").exists()


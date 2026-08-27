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


def _build_plan(service, job=None, *, applied_at=None):
    return service.build_ont_fastq_qc_reconciliation_plan(
        job or _job(),
        _evidence(service),
        hierarchy_record=_hierarchy_record(),
        database_identity_sha256="8" * 64,
        source_revision="f" * 40,
        source_tree="e" * 40,
        applied_at=applied_at or datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc),
        principal="uid:1000:tester",
        authorization_class="development_service_owner",
    )


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


def _evidence(service):
    return service.OntFastqQcReconciliationEvidence(
        completed_stages=tuple(STAGES),
        stage_outputs={key: tuple(value) for key, value in STAGE_OUTPUTS.items()},
        workflow_id="ont_fastq_qc",
        input_mode="fastq",
        reference_sequence_sha256=REFERENCE_SHA256,
        source_fastq_sha256=SOURCE_FASTQ_SHA256,
        resource_evidence_status="historical_unavailable",
        sequence_qc_manifest_sha256=SEQUENCE_QC_MANIFEST_SHA256,
        verification_manifest_sha256=VERIFICATION_MANIFEST_SHA256,
        artifact_set_sha256=ARTIFACT_SET_SHA256,
        declared_artifact_count=36,
        present_artifact_count=34,
        unavailable_artifact_count=2,
        result_root_identity_sha256="e" * 64,
    )


def test_reconciliation_plan_changes_only_query_mirrors_and_append_only_receipt() -> None:
    service = _service()
    job = _job()
    original_provenance = job.provenance

    plan = _build_plan(service, job)

    assert plan.requires_write is True
    assert plan.completed_stages == tuple(STAGES)
    assert plan.stage_outputs == {key: tuple(value) for key, value in STAGE_OUTPUTS.items()}
    assert plan.provenance["existing_history"] == original_provenance["existing_history"]
    assert plan.provenance["stage_terminal_states"] == original_provenance["stage_terminal_states"]
    assert plan.provenance["alignment_hierarchy_authority_v1"] == _hierarchy_record()
    receipt = plan.provenance["ont_fastq_qc_reconciliation_v1"]
    assert receipt["schema"] == "bms.ont-fastq-qc-reconciliation.v1"
    assert receipt["job_id"] == JOB_ID
    assert receipt["source_commit"] == "f" * 40
    assert receipt["source_tree"] == "e" * 40
    assert receipt["principal"] == "uid:1000:tester"
    assert receipt["authorization_class"] == "development_service_owner"
    assert receipt["project_id"] == "4af72c1d-27d8-4e14-8f39-4259a80494a0"
    assert receipt["global_experiment_id"] == "9a10c5a8-b233-4bf3-af14-9c2880525278"
    assert receipt["domain_experiment_id"] == "916a611b-6879-486f-bf9e-e1b5a796e01c"
    assert receipt["state_revision_id"].startswith("molbio_ngs_state_revision_")
    assert receipt["member_receipt_id"] == "195b526d-35b3-40e4-b400-e8e4232a98fc"
    assert receipt["sequence_qc_manifest_sha256"] == SEQUENCE_QC_MANIFEST_SHA256
    assert receipt["verification_manifest_sha256"] == VERIFICATION_MANIFEST_SHA256
    assert receipt["reference_sequence_sha256"] == REFERENCE_SHA256
    assert receipt["source_fastq_sha256"] == SOURCE_FASTQ_SHA256
    assert receipt["resource_evidence_status"] == "historical_unavailable"
    assert receipt["artifact_set_sha256"] == ARTIFACT_SET_SHA256
    assert receipt["declared_artifact_count"] == 36
    assert receipt["present_artifact_count"] == 34
    assert receipt["unavailable_artifact_count"] == 2
    assert receipt["result_root_identity_sha256"] == "e" * 64
    assert receipt["protected_row_preimage_sha256"] == plan.protected_preimage_sha256
    assert receipt["database_identity_sha256"] == "8" * 64
    for field in (
        "normalized_request_sha256",
        "completed_stages_preimage_sha256",
        "stage_outputs_preimage_sha256",
        "provenance_preimage_sha256",
        "completed_stages_postimage_sha256",
        "stage_outputs_postimage_sha256",
        "receipt_free_provenance_postimage_sha256",
    ):
        assert len(receipt[field]) == 64
    assert receipt["backup"] is None
    assert receipt["receipt_sha256"] is None
    assert receipt["compute_invoked"] is False
    assert receipt["scientific_artifacts_modified"] is False
    assert not hasattr(plan, "all_stages")


def test_reconciliation_plan_is_idempotent_after_exact_replay() -> None:
    service = _service()
    first = _build_plan(service)
    first = _bind_backup(service, first)
    replay_job = _job(
        completed_stages=list(first.completed_stages),
        stage_outputs={key: list(value) for key, value in first.stage_outputs.items()},
        provenance=first.provenance,
    )

    replay = _build_plan(
        service,
        replay_job,
        applied_at=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
    )

    assert replay.requires_write is False
    assert replay.provenance == first.provenance


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "running"},
        {"queue_status": "running"},
        {"awaiting_input": True},
        {"model_id": "another_workflow"},
        {"params": {"input_mode": "pod5"}},
    ],
)
def test_reconciliation_plan_rejects_noncanonical_or_nonterminal_jobs(changes: dict) -> None:
    service = _service()
    with pytest.raises(service.OntFastqQcReconciliationError):
        _build_plan(service, _job(**changes))


@pytest.mark.asyncio
async def test_evidence_collection_uses_a_detached_job_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _service()
    collect = getattr(service, "collect_ont_fastq_qc_reconciliation_evidence", None)
    assert callable(collect)
    result_root = tmp_path / "retry3"
    result_root.mkdir()
    original = _job()
    original_completed = list(original.completed_stages)
    original_provenance = original.provenance

    async def fake_validate(
        detached_job,
        *,
        historical_reconciliation=False,
        pinned_result_root=None,
    ):
        assert detached_job is not original
        assert historical_reconciliation is True
        assert pinned_result_root is not None
        assert pinned_result_root.readlink() == result_root
        detached_job.completed_stages = list(STAGES)
        detached_job.stage_outputs = {key: list(value) for key, value in STAGE_OUTPUTS.items()}
        detached_job.provenance = {**detached_job.provenance, "completion-only-on-copy": True}
        return {
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "reference_sequence_sha256": REFERENCE_SHA256,
            "source_fastq_sha256": SOURCE_FASTQ_SHA256,
            "resource_evidence_status": "historical_unavailable",
            "sequence_qc_manifest_sha256": SEQUENCE_QC_MANIFEST_SHA256,
            "construct_verification_manifest_sha256": VERIFICATION_MANIFEST_SHA256,
            "artifact_set_sha256": ARTIFACT_SET_SHA256,
            "declared_artifact_count": 36,
            "present_artifact_count": 34,
            "unavailable_artifact_count": 2,
        }

    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda job: result_root)
    monkeypatch.setattr(service, "validate_and_prepare_ont_fastq_qc_completion", fake_validate)
    evidence = await collect(original)

    assert evidence.completed_stages == tuple(STAGES)
    assert evidence.stage_outputs == {key: tuple(value) for key, value in STAGE_OUTPUTS.items()}
    assert evidence.result_root_identity_sha256 != str(result_root)
    assert len(evidence.result_root_identity_sha256) == 64
    assert original.completed_stages == original_completed
    assert original.provenance is original_provenance
    assert "completion-only-on-copy" not in original.provenance


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


@pytest.mark.asyncio
async def test_evidence_collection_reads_through_pinned_root_across_aba_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ont_ngs_reconciliation as service

    result_root = tmp_path / "retry3"
    result_root.mkdir()
    (result_root / "identity.txt").write_text("original", encoding="utf-8")
    replacement_source = tmp_path / "replacement"
    replacement_source.mkdir()
    (replacement_source / "identity.txt").write_text("replacement", encoding="utf-8")
    job = _job()
    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: result_root)

    async def aba_validate(
        detached_job,
        *,
        historical_reconciliation=False,
        pinned_result_root=None,
    ):
        assert historical_reconciliation is True
        assert pinned_result_root is not None
        original_path = tmp_path / "original"
        result_root.rename(original_path)
        replacement_source.rename(result_root)
        assert (pinned_result_root / "identity.txt").read_text(encoding="utf-8") == "original"
        result_root.rename(replacement_source)
        original_path.rename(result_root)
        detached_job.completed_stages = list(STAGES)
        detached_job.stage_outputs = {key: list(value) for key, value in STAGE_OUTPUTS.items()}
        return {
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "reference_sequence_sha256": REFERENCE_SHA256,
            "source_fastq_sha256": SOURCE_FASTQ_SHA256,
            "resource_evidence_status": "historical_unavailable",
            "sequence_qc_manifest_sha256": SEQUENCE_QC_MANIFEST_SHA256,
            "construct_verification_manifest_sha256": VERIFICATION_MANIFEST_SHA256,
            "artifact_set_sha256": ARTIFACT_SET_SHA256,
            "declared_artifact_count": 36,
            "present_artifact_count": 34,
            "unavailable_artifact_count": 2,
        }

    monkeypatch.setattr(service, "validate_and_prepare_ont_fastq_qc_completion", aba_validate)

    evidence = await service.collect_ont_fastq_qc_reconciliation_evidence(job)
    assert evidence.artifact_set_sha256 == ARTIFACT_SET_SHA256
    assert (result_root / "identity.txt").read_text(encoding="utf-8") == "original"


async def _sqlite_job_sessions(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Job.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    job_values = vars(_job()).copy()
    job_values.update({"name": "retry3", "paused": False})
    async with sessions() as session:
        session.add(Job(**job_values))
        await session.commit()
    return engine, sessions


def _bind_backup(service, plan):
    return service.bind_ont_fastq_qc_reconciliation_backup(
        plan,
        service.OntFastqQcReconciliationBackup(
            backup_id="retry3-pre-reconciliation.sqlite",
            sha256="9" * 64,
            size_bytes=8192,
            integrity_check="ok",
            foreign_key_violations=0,
            source_snapshot=SOURCE_SNAPSHOT,
        ),
    )


def test_reconciliation_backup_binding_is_path_opaque_and_required() -> None:
    service = _service()
    plan = _build_plan(service)

    bound = _bind_backup(service, plan)

    assert bound.provenance["ont_fastq_qc_reconciliation_v1"]["backup"] == {
        "backup_id": "retry3-pre-reconciliation.sqlite",
        "sha256": "9" * 64,
        "size_bytes": 8192,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
    }
    assert bound.provenance["ont_fastq_qc_reconciliation_v1"]["source_snapshot"] == SOURCE_SNAPSHOT
    receipt = bound.provenance["ont_fastq_qc_reconciliation_v1"]
    assert "/" not in receipt["backup"]["backup_id"]
    assert receipt["backup_source_preimage_sha256"] == service.reconciliation_authority_digest(
        "backup-source-preimage", SOURCE_SNAPSHOT
    )
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    expected_digest = hashlib.sha256(
        b"bms.ont-fastq-qc-reconciliation.v1\0receipt\0" + rfc8785.dumps(payload)
    ).hexdigest()
    assert receipt["receipt_sha256"] == expected_digest
    schema_path = Path(__file__).resolve().parents[3] / "schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)


@pytest.mark.asyncio
async def test_reconciliation_apply_loses_cas_without_erasing_concurrent_provenance(tmp_path: Path) -> None:
    service = _service()
    apply_plan = getattr(service, "apply_ont_fastq_qc_reconciliation_plan", None)
    assert callable(apply_plan)
    engine, sessions = await _sqlite_job_sessions(tmp_path)
    try:
        async with sessions() as first, sessions() as second:
            loaded = await first.get(Job, JOB_ID)
            assert loaded is not None
            plan = _build_plan(service, loaded)
            plan = _bind_backup(service, plan)
            winner = await second.get(Job, JOB_ID)
            assert winner is not None
            winner.provenance = {**winner.provenance, "concurrent_operator_edit": True}
            await second.commit()

            with pytest.raises(service.OntFastqQcReconciliationError, match="CAS"):
                await apply_plan(
                    first,
                    loaded,
                    plan,
                    current_source_snapshot=SOURCE_SNAPSHOT,
                    current_database_identity_sha256="8" * 64,
                )
            await first.rollback()

        async with sessions() as verification:
            persisted = await verification.get(Job, JOB_ID)
            assert persisted is not None
            assert persisted.provenance["concurrent_operator_edit"] is True
            assert persisted.completed_stages == ["dimer_qc"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_apply_updates_only_mirrors_and_receipt(tmp_path: Path) -> None:
    service = _service()
    engine, sessions = await _sqlite_job_sessions(tmp_path)
    try:
        async with sessions() as session:
            loaded = await session.get(Job, JOB_ID)
            assert loaded is not None
            original_params = loaded.params
            original_provenance = json.loads(json.dumps(loaded.provenance))
            original_current_stage = loaded.current_stage
            original_status = (loaded.status, loaded.queue_status, loaded.completed_at)
            plan = _build_plan(service, loaded)
            plan = _bind_backup(service, plan)
            changed = await service.apply_ont_fastq_qc_reconciliation_plan(
                session,
                loaded,
                plan,
                current_source_snapshot=SOURCE_SNAPSHOT,
                current_database_identity_sha256="8" * 64,
            )
            await session.commit()
            assert changed is True

        async with sessions() as verification:
            persisted = await verification.get(Job, JOB_ID)
            assert persisted is not None
            assert persisted.completed_stages == STAGES
            assert persisted.stage_outputs == STAGE_OUTPUTS
            assert persisted.provenance["existing_history"] == {"immutable": True}
            assert persisted.provenance["alignment_hierarchy_authority_v1"] == _hierarchy_record()
            assert "ont_fastq_qc_reconciliation_v1" in persisted.provenance
            assert persisted.params == original_params
            assert persisted.current_stage == original_current_stage
            assert (persisted.status, persisted.queue_status, persisted.completed_at) == original_status
            receipt = persisted.provenance["ont_fastq_qc_reconciliation_v1"]
            package_keys = {
                "workflow_id", "input_mode", "reference_sequence_sha256", "source_fastq_sha256",
                "resource_evidence_status", "sequence_qc_manifest_sha256", "verification_manifest_sha256",
                "artifact_set_sha256", "declared_artifact_count", "present_artifact_count",
                "unavailable_artifact_count", "result_root_identity_sha256",
            }
            service.validate_persisted_reconciliation_receipt(
                receipt,
                job=persisted,
                expected_package={key: receipt[key] for key in package_keys},
                provenance_preimage=original_provenance,
            )
            persisted.stage_outputs = {**persisted.stage_outputs, "fastq_align": ["foreign/output"]}
            with pytest.raises(service.OntFastqQcReconciliationError, match="current authority"):
                service.validate_persisted_reconciliation_receipt(
                    receipt,
                    job=persisted,
                    expected_package={key: receipt[key] for key in package_keys},
                    provenance_preimage=original_provenance,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_apply_rejects_source_snapshot_drift_before_cas(tmp_path: Path) -> None:
    service = _service()
    engine, sessions = await _sqlite_job_sessions(tmp_path)
    try:
        async with sessions() as session:
            loaded = await session.get(Job, JOB_ID)
            assert loaded is not None
            plan = _bind_backup(service, _build_plan(service, loaded))
            changed_snapshot = {**SOURCE_SNAPSHOT, "page_count": SOURCE_SNAPSHOT["page_count"] + 1}
            with pytest.raises(service.OntFastqQcReconciliationConflict, match="source preimage"):
                await service.apply_ont_fastq_qc_reconciliation_plan(
                    session,
                    loaded,
                    plan,
                    current_source_snapshot=changed_snapshot,
                    current_database_identity_sha256="8" * 64,
                )
            await session.rollback()
    finally:
        await engine.dispose()

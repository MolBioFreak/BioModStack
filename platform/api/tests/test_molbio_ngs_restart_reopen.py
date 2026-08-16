from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _verification_manifest(
    *,
    job_id: str,
    verdict: str,
    profile: dict[str, object],
) -> dict[str, object]:
    check_status = "pass" if verdict == "PASS" else "not_evaluated"
    inputs = {
        role: {
            "state": "present",
            "role": role,
            "semantic_validation": {
                "status": "valid",
                "validator": "phase2b-fixture",
                "reason": None,
            },
        }
        for role in (
            "reference",
            "observed",
            "support",
            "alignment",
            "alignment_index",
            "topology",
        )
    }
    inputs["observed"]["independent_from_expected"] = True
    return {
        "artifact_schema_version": 2,
        "schema": "biomodstack.construct_verification.v2",
        "job_id": job_id,
        "execution": {"status": "SUCCEEDED", "exit_code": 0},
        "verdict": verdict,
        "reason_codes": ["ALL_CHECKS_PASS"] if verdict == "PASS" else ["REVIEW_REQUIRED"],
        "threshold_profile": profile,
        "provenance": {
            "workflow": {
                "name": "ConstructVerify",
                "module": "modules/ngs/construct_verify.nf",
                "version": "2",
            },
            "commands": [{"argv": ["phase2b-fixture"]}],
        },
        "checks": {
            name: {"status": check_status, "reason_codes": [], "metrics": {}}
            for name in (
                "sequence_identity",
                "read_support",
                "coverage",
                "contamination",
                "topology",
            )
        },
        "inputs": inputs,
        "artifacts": [],
    }


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_phase2b_sample_reference_state_survive_restart_and_reopen(
    tmp_path: Path, monkeypatch
) -> None:
    from database import Base, Job
    from molbio_database import create_molbio_engine, make_molbio_session_factory
    from molbio_models import MolecularDocument, MolecularRevision, MolBioBase
    from molbio_ngs_database import (
        create_molbio_ngs_engine,
        create_molbio_ngs_session_factory,
    )
    from molbio_ngs_migrations import health, run_all
    from molbio_ngs_models import MolBioNGSEvidenceAssessment, MolBioNGSMemberReceipt
    from molbio_ngs_services import (
        InternalVerifiedGlobalBinding,
        StateMember,
        append_sample_revision,
        create_sample,
        get_sample,
        get_sample_revision,
        get_state_revision,
        initialize_domain_state,
        save_state_revision,
        verify_state_revision_integrity,
    )
    from routers.molbio_ngs_experiments import (
        AttachJobEvidenceRequest,
        EvidenceAssessmentRequest,
        CreateSampleRequest,
    )
    from services.molbio_ngs_evidence import (
        create_evidence_assessment,
        get_evidence_assessment,
        list_evidence_assessments,
        resolve_evidence_assessment_receipt,
    )
    from services.molbio_ngs_member_receipts import (
        persist_member_receipt,
        resolve_molecular_revision_receipt,
        resolve_ngs_job_receipt,
        resolve_ngs_result_manifest_receipt,
    )
    from services.molbio_ngs_references import (
        append_reference_revision,
        archive_reference,
        create_reference_from_molbio_revision,
        get_reference_resource,
        get_reference_revision,
        read_reference_artifact_bytes,
        resolve_managed_reference_for_launch,
        resolve_ngs_reference_revision_receipt,
    )

    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "molbio_ngs"
            / "phase2b_restart_reopen.json"
        ).read_text(encoding="utf-8")
    )
    domain_spec = fixture["domain"]
    sample_spec = fixture["sample"]
    molecular_spec = fixture["molecular_document"]
    reference_spec = fixture["reference"]
    evidence_spec = fixture["evidence"]

    domain_path = tmp_path / "molbio_ngs.db"
    molbio_path = tmp_path / "molbio.db"
    core_path = tmp_path / "core.db"
    results_root = tmp_path / "results"
    result_root = results_root / evidence_spec["output_dir"]
    manifest_path = result_root / evidence_spec["manifest_relative_path"]
    reference_root = tmp_path / "managed-reference-root"
    results_root.mkdir()
    manifest_path.parent.mkdir(parents=True)
    monkeypatch.setenv("BMS_MOLBIO_NGS_REFERENCE_ROOT", str(reference_root))
    import services.job_result_roots as job_result_roots
    import services.sequence_qc_manifest as sequence_qc_manifest

    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: results_root)
    run_all(domain_path)

    domain_engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{domain_path}")
    domain_factory = create_molbio_ngs_session_factory(domain_engine)
    molbio_engine = create_molbio_engine(f"sqlite+aiosqlite:///{molbio_path}")
    molbio_factory = make_molbio_session_factory(molbio_engine)
    core_engine = create_async_engine(
        f"sqlite+aiosqlite:///{core_path}", connect_args={"timeout": 30}
    )
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    async with molbio_engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    async with core_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime(2026, 8, 8, 22, 0, 0)
    sequence = molecular_spec["sequence"]
    sequence_sha256 = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    async with molbio_factory() as molbio_session:
        document = MolecularDocument(
            id=molecular_spec["id"],
            document_kind="nucleotide_sequence",
            name=molecular_spec["name"],
            current_revision_id=None,
            created_at=now,
        )
        molbio_session.add(document)
        await molbio_session.flush()
        molecular_revision = MolecularRevision(
            id=molecular_spec["historical_revision_id"],
            document_id=document.id,
            revision_number=1,
            change_kind="create",
            content_sha256=sequence_sha256,
            content_length=len(sequence),
            snapshot={
                "sequence": sequence,
                "sequence_type": "dna",
                "name": molecular_spec["name"],
            },
            provenance={"fixture": "phase2b_restart_reopen"},
            created_at=now,
        )
        molbio_session.add(molecular_revision)
        await molbio_session.flush()
        document.current_revision_id = molecular_revision.id
        await molbio_session.commit()

    binding = InternalVerifiedGlobalBinding(
        global_domain_experiment_id=domain_spec["id"],
        global_domain_experiment_revision_id=domain_spec["global_revision_id"],
        global_domain_experiment_revision_digest="a" * 64,
        project_id=domain_spec["project_id"],
        project_generation="1",
        project_digest="b" * 64,
        project_receipt_id="project-receipt-phase2b",
        project_reopen_destination={
            "surface": "project",
            "params": {"project_id": domain_spec["project_id"]},
        },
        global_experiment_id=domain_spec["global_experiment_id"],
        global_experiment_generation="1",
        global_experiment_digest="c" * 64,
        global_experiment_receipt_id="global-experiment-receipt-phase2b",
        global_experiment_reopen_destination={
            "surface": "global-experiment",
            "params": {
                "project_id": domain_spec["project_id"],
                "experiment_id": domain_spec["global_experiment_id"],
            },
        },
        verified_at="2026-08-08T22:00:00+00:00",
    )

    async with domain_factory() as domain_session, molbio_factory() as molbio_session:
        await initialize_domain_state(
            domain_session,
            binding,
            idempotency_key="phase2b-initialize",
        )
        sample, sample_revision_1 = await create_sample(
            domain_session,
            global_domain_experiment_id=domain_spec["id"],
            payload=sample_spec["first"],
            idempotency_key="phase2b-sample-create",
            created_by="fixture",
        )
        reference, reference_revision_1 = await create_reference_from_molbio_revision(
            domain_session,
            molbio_session,
            global_domain_experiment_id=domain_spec["id"],
            sequence_id=molecular_spec["id"],
            molecular_revision_id=molecular_spec["historical_revision_id"],
            name=reference_spec["name"],
            molecule_type=reference_spec["molecule_type"],
            topology=reference_spec["topology"],
            coordinate_contract=reference_spec["coordinate_contract"],
            idempotency_key="phase2b-reference-create",
            created_by="fixture",
        )
        reference_receipt = await persist_member_receipt(
            domain_session,
            await resolve_ngs_reference_revision_receipt(
                domain_session,
                global_domain_experiment_id=domain_spec["id"],
                reference_id=reference.id,
                revision_id=reference_revision_1.id,
            ),
        )
        state_payload = {
            "schema": "bms.molbio-ngs.domain-state-revision.v1",
            "design": {
                "sample_revision_ids": [sample_revision_1.id],
                "conditions": [],
                "replicates": [],
                "expected_molecule_roles": ["ngs_reference"],
            },
            "reference_policy": {
                "required_roles": ["ngs_reference"],
                "coordinate_policy": "exact_revision",
            },
            "acquisition_policy": {
                "platform": "ont",
                "required_terminal_manifest": True,
            },
            "analysis_policy": {
                "allowed_workflow_ids": ["ont_fastq_qc", "ont_plasmid_qc"],
                "required_manifest_schemas": ["biomodstack.construct_verification.v2"],
            },
            "assessment_policy": {
                "rule_id": "server-owned-rule",
                "completion_is_scientific_pass": False,
            },
            "notes": fixture["state_notes"],
        }
        state_revision = await save_state_revision(
            domain_session,
            global_domain_experiment_id=domain_spec["id"],
            global_domain_experiment_revision_id=domain_spec["global_revision_id"],
            payload=state_payload,
            members=[
                StateMember(
                    receipt_id=reference_receipt.receipt_id,
                    role="ngs_reference",
                    ordinal=0,
                    sample_revision_id=sample_revision_1.id,
                )
            ],
            expected_head_generation=0,
            parent_revision_id=None,
            idempotency_key="phase2b-state-save",
            created_by="fixture",
        )
        await domain_session.commit()

        managed_launch = await resolve_managed_reference_for_launch(
            domain_session,
            global_domain_experiment_id=domain_spec["id"],
            molbio_ngs_state_revision_id=state_revision.id,
            ngs_reference_revision_id=reference_revision_1.id,
        )
        assert managed_launch.ngs_reference_id == reference.id
        assert managed_launch.ngs_reference_revision_id == reference_revision_1.id
        assert managed_launch.state_membership_receipt_id == reference_receipt.receipt_id
        assert managed_launch.expected_reference_fasta_sha256 == reference_revision_1.canonical_fasta_sha256
        assert managed_launch.expected_reference_fasta_size_bytes == reference_revision_1.canonical_fasta_size_bytes
        assert managed_launch.reference_fasta_path.read_bytes() == await read_reference_artifact_bytes(
            domain_session, reference_revision_1
        )

        import routers.ont_runs as ont_runs
        from fastapi import BackgroundTasks, Request, Response
        from schemas import JobCreate, JobResponse, JobStatus
        from typing import cast

        reads_path = tmp_path / "managed-reference-launch.fastq"
        reads_path.write_text("@read-1\nACGT\n+\nIIII\n", encoding="ascii")
        captured_launch: dict[str, object] = {}

        def confine_launch_path(value, label, **_kwargs):
            if label != "fastq_path":
                raise AssertionError(f"unexpected caller path confinement: {label}")
            return str(value)

        async def create_managed_launch(job, *_args, **kwargs):
            captured_launch["job"] = job
            captured_launch["commit"] = kwargs["commit"]
            return JobResponse(
                id="managed-reference-launch-job",
                name=job.name,
                status=JobStatus.QUEUED,
                model_id=job.model_id,
                mode=job.mode,
                params=dict(job.params),
            )

        monkeypatch.setattr(ont_runs, "_confine_submitted_path", confine_launch_path)
        monkeypatch.setattr(ont_runs, "_create_pipeline_job", create_managed_launch)
        async with core_factory() as launch_core_session:
            launch_response = await ont_runs.ont_submit_ngs_workflow(
                "ont_fastq_qc",
                ont_runs.OntNgsSubmitRequest(
                    params={"fastq_path": str(reads_path)},
                    managed_reference=ont_runs.OntManagedReferenceRequest(
                        global_domain_experiment_id=domain_spec["id"],
                        molbio_ngs_state_revision_id=state_revision.id,
                        ngs_reference_revision_id=reference_revision_1.id,
                    ),
                ),
                BackgroundTasks(),
                Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "scheme": "https",
                        "path": "/api/ont/ngs/ont_fastq_qc/submit",
                        "headers": [],
                    }
                ),
                Response(),
                launch_core_session,
                domain_session,
            )
        launched_job = cast(JobCreate, captured_launch["job"])
        assert captured_launch["commit"] is True
        launched_reference_path = Path(str(launched_job.params["reference_fasta"]))
        assert launched_reference_path != managed_launch.reference_fasta_path
        assert launched_reference_path.read_bytes() == await read_reference_artifact_bytes(
            domain_session, reference_revision_1
        )
        assert {
            key: launched_job.params[key]
            for key in ont_runs.ONT_MANAGED_REFERENCE_EVIDENCE_PARAMS
        } == {
            "expected_result_manifest_schema": "biomodstack.construct_verification.v2",
            "global_domain_experiment_id": domain_spec["id"],
            "molbio_ngs_state_revision_id": state_revision.id,
            "ngs_reference_artifact_id": managed_launch.ngs_reference_artifact_id,
            "ngs_reference_id": managed_launch.ngs_reference_id,
            "ngs_reference_revision_id": reference_revision_1.id,
            "selected_reference_sha256": managed_launch.selected_reference_sha256,
            "state_membership_receipt_id": managed_launch.state_membership_receipt_id,
            "expected_reference_fasta_sha256": reference_revision_1.canonical_fasta_sha256,
            "managed_reference_snapshot_sha256": managed_launch.launch_snapshot_sha256,
            "managed_reference_snapshot_size_bytes": managed_launch.launch_snapshot_size_bytes,
        }
        assert "reference_fasta" not in launch_response.params

        first_sample_payload = sample_revision_1.canonical_payload
        first_reference_payload = reference_revision_1.canonical_payload
        first_fasta_bytes = await read_reference_artifact_bytes(
            domain_session, reference_revision_1
        )
        artifact_health = health(domain_path)
        artifact_attestation = artifact_health["attestation"]
        assert isinstance(artifact_attestation, dict)
        assert artifact_health["status"] == "healthy"
        assert artifact_attestation["artifact_errors"] == []

        eligible_profile_values = {
            key: value
            for key, value in evidence_spec["eligible_profile"].items()
            if key != "id"
        }
        profile_path = tmp_path / "construct_verify_profiles.json"
        profile_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "profiles": {
                        evidence_spec["eligible_profile"]["id"]: eligible_profile_values
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(sequence_qc_manifest, "CANONICAL_PROFILE_PATH", profile_path)
        eligible_profile = {
            "id": evidence_spec["eligible_profile"]["id"],
            "version": eligible_profile_values["version"],
            "calibration_status": eligible_profile_values["calibration_status"],
            "public_accuracy_validated": eligible_profile_values[
                "public_accuracy_validated"
            ],
            "sha256": hashlib.sha256(
                json.dumps(
                    eligible_profile_values,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
            "values": eligible_profile_values,
        }
        review_manifest_bytes = _canonical_bytes(
            _verification_manifest(
                job_id=evidence_spec["job_id"],
                verdict="REVIEW",
                profile={
                    "id": "review-only",
                    "version": "1",
                    "calibration_status": "experimental",
                    "public_accuracy_validated": False,
                    "sha256": "0" * 64,
                    "values": {"automatic_pass_eligible": False},
                },
            )
        )
        manifest_path.write_bytes(review_manifest_bytes)

        async with core_factory() as core_session:
            job = Job(
                id=evidence_spec["job_id"],
                name=evidence_spec["job_name"],
                status="completed",
                queue_status="completed",
                model_id="nanopore",
                mode="plasmid_qc",
                params={
                    "ont_workflow_id": "ont_plasmid_qc",
                    "global_domain_experiment_id": domain_spec["id"],
                    "molbio_ngs_state_revision_id": state_revision.id,
                    "ngs_reference_revision_id": reference_revision_1.id,
                    "expected_reference_fasta_sha256": (
                        reference_revision_1.canonical_fasta_sha256
                    ),
                    "molbio_revision_binding": {
                        "sequence_id": molecular_spec["id"],
                        "revision_id": molecular_spec["historical_revision_id"],
                        "revision_sha256": sequence_sha256,
                    },
                },
                output_dir=evidence_spec["output_dir"],
                created_at=now,
            )
            core_session.add(job)
            await core_session.commit()

            molecular_receipt = await persist_member_receipt(
                domain_session,
                await resolve_molecular_revision_receipt(
                    molbio_session,
                    sequence_id=molecular_spec["id"],
                    revision_id=molecular_spec["historical_revision_id"],
                ),
            )
            job_receipt = await persist_member_receipt(
                domain_session,
                await resolve_ngs_job_receipt(core_session, job_id=job.id),
            )
            review_manifest_receipt = await persist_member_receipt(
                domain_session,
                await resolve_ngs_result_manifest_receipt(core_session, job_id=job.id),
            )
            review_assessment = await create_evidence_assessment(
                domain_session,
                core_session,
                molbio_session,
                global_domain_experiment_id=domain_spec["id"],
                state_revision_id=state_revision.id,
                sample_revision_id=sample_revision_1.id,
                ngs_job_receipt_id=job_receipt.receipt_id,
                ngs_result_manifest_receipt_id=review_manifest_receipt.receipt_id,
                ngs_reference_revision_receipt_id=reference_receipt.receipt_id,
                molecular_revision_receipt_id=molecular_receipt.receipt_id,
                assessment_rule_id=evidence_spec["assessment_rule_id"],
                notes="Completion alone cannot become scientific PASS.",
                idempotency_key="phase2b-evidence-review",
            )
            await domain_session.commit()
            assert review_assessment.job_lifecycle_state == "completed"
            assert review_assessment.manifest_integrity == "valid"
            assert review_assessment.requested_assessment == "REVIEW"
            assert review_assessment.scientific_assessment == "REVIEW"
            assert review_assessment.created_by == "system:molbio-api"

            pass_manifest_bytes = _canonical_bytes(
                _verification_manifest(
                    job_id=evidence_spec["job_id"],
                    verdict="PASS",
                    profile=eligible_profile,
                )
            )
            manifest_path.write_bytes(pass_manifest_bytes)
            pass_manifest_receipt = await persist_member_receipt(
                domain_session,
                await resolve_ngs_result_manifest_receipt(core_session, job_id=job.id),
            )
            pass_assessment = await create_evidence_assessment(
                domain_session,
                core_session,
                molbio_session,
                global_domain_experiment_id=domain_spec["id"],
                state_revision_id=state_revision.id,
                sample_revision_id=sample_revision_1.id,
                ngs_job_receipt_id=job_receipt.receipt_id,
                ngs_result_manifest_receipt_id=pass_manifest_receipt.receipt_id,
                ngs_reference_revision_receipt_id=reference_receipt.receipt_id,
                molecular_revision_receipt_id=molecular_receipt.receipt_id,
                assessment_rule_id=evidence_spec["assessment_rule_id"],
                notes="Exact eligible evidence satisfies the server-owned rule.",
                idempotency_key="phase2b-evidence-pass",
            )
            await domain_session.commit()
            assert pass_assessment.job_lifecycle_state == "completed"
            assert pass_assessment.manifest_integrity == "valid"
            assert pass_assessment.requested_assessment == "PASS"
            assert pass_assessment.scientific_assessment == "PASS"
            assert pass_assessment.created_by == "system:molbio-api"
            assert pass_assessment.raw_manifest_sha256 == hashlib.sha256(
                pass_manifest_bytes
            ).hexdigest()
            pass_wrapper = json.loads(pass_assessment.canonical_wrapper)
            assert pass_wrapper["schema"] == "bms.molbio-ngs.ngs-evidence-receipt.v1"
            assert pass_wrapper["requested_assessment"] == pass_wrapper["scientific_assessment"]
            assert pass_wrapper["scientific_assessment"] == "PASS"
            assert pass_wrapper["job_lifecycle_state"] == "completed"
            assert pass_wrapper["manifest_integrity"] == "valid"
            assert pass_wrapper["raw_manifest_sha256"] == pass_assessment.raw_manifest_sha256
            assert pass_wrapper["receipt_ids"] == {
                "molecular_revision": molecular_receipt.receipt_id,
                "ngs_comparison_panel": None,
                "ngs_job": job_receipt.receipt_id,
                "ngs_reference_revision": reference_receipt.receipt_id,
                "ngs_result_manifest": pass_manifest_receipt.receipt_id,
                "ont_instrument_run": None,
            }
            typed_assessment_receipt = await resolve_evidence_assessment_receipt(
                domain_session,
                global_domain_experiment_id=domain_spec["id"],
                evidence_id=pass_assessment.evidence_id,
            )
            typed_assessment_receipt = await persist_member_receipt(
                domain_session, typed_assessment_receipt
            )
            await domain_session.commit()

        sample_payload_2 = {
            **sample_spec["first"],
            "notes": sample_spec["second_notes"],
        }
        sample_revision_2 = await append_sample_revision(
            domain_session,
            global_domain_experiment_id=domain_spec["id"],
            sample_id=sample.id,
            payload=sample_payload_2,
            expected_head_generation=1,
            parent_revision_id=sample_revision_1.id,
            idempotency_key="phase2b-sample-revision-2",
            created_by="fixture",
        )
        reference_revision_2 = await append_reference_revision(
            domain_session,
            reference_id=reference.id,
            raw_fasta=reference_spec["second_fasta"].encode("utf-8"),
            molecule_type=reference_spec["molecule_type"],
            topology=reference_spec["topology"],
            coordinate_contract=reference_spec["coordinate_contract"],
            source_provenance={"kind": "inline_fasta", "fixture": True},
            expected_head_generation=1,
            parent_revision_id=reference_revision_1.id,
            idempotency_key="phase2b-reference-revision-2",
            created_by="fixture",
        )
        await archive_reference(
            domain_session,
            reference_id=reference.id,
            expected_head_generation=2,
            idempotency_key="phase2b-reference-archive",
            archived_by="fixture",
        )
        await domain_session.commit()

        assert sample_revision_2.id != sample_revision_1.id
        assert reference_revision_2.id != reference_revision_1.id
        assert sample_revision_1.canonical_payload == first_sample_payload
        assert reference_revision_1.canonical_payload == first_reference_payload
        assert await read_reference_artifact_bytes(
            domain_session, reference_revision_1
        ) == first_fasta_bytes

        durable_ids = {
            "sample_id": sample.id,
            "sample_revision_1_id": sample_revision_1.id,
            "sample_revision_2_id": sample_revision_2.id,
            "reference_id": reference.id,
            "reference_revision_1_id": reference_revision_1.id,
            "reference_revision_2_id": reference_revision_2.id,
            "state_revision_id": state_revision.id,
            "reference_receipt_id": reference_receipt.receipt_id,
            "review_evidence_id": review_assessment.evidence_id,
            "pass_evidence_id": pass_assessment.evidence_id,
            "typed_assessment_receipt_id": typed_assessment_receipt.receipt_id,
        }
        durable_digests = {
            "sample_payload": sample_revision_1.payload_sha256,
            "reference_payload": reference_revision_1.payload_sha256,
            "fasta": reference_revision_1.canonical_fasta_sha256,
            "state_payload": state_revision.payload_sha256,
            "membership": state_revision.membership_graph_sha256,
            "review_wrapper": review_assessment.wrapper_sha256,
            "pass_wrapper": pass_assessment.wrapper_sha256,
            "pass_manifest": pass_assessment.raw_manifest_sha256,
            "typed_assessment_receipt": typed_assessment_receipt.receipt_sha256,
        }

    await domain_engine.dispose()
    await molbio_engine.dispose()
    await core_engine.dispose()

    with __import__("sqlite3").connect(domain_path) as connection:
        with pytest.raises(__import__("sqlite3").IntegrityError, match="evidence assessment is immutable"):
            connection.execute(
                "UPDATE molbio_ngs_evidence_assessments SET scientific_assessment='FAIL' "
                "WHERE evidence_id=?",
                (durable_ids["pass_evidence_id"],),
            )
        with pytest.raises(__import__("sqlite3").IntegrityError, match="evidence assessment is immutable"):
            connection.execute(
                "DELETE FROM molbio_ngs_evidence_assessments WHERE evidence_id=?",
                (durable_ids["pass_evidence_id"],),
            )

    reopened_domain_engine = create_molbio_ngs_engine(
        f"sqlite+aiosqlite:///{domain_path}"
    )
    reopened_domain_factory = create_molbio_ngs_session_factory(reopened_domain_engine)
    reopened_molbio_engine = create_molbio_engine(f"sqlite+aiosqlite:///{molbio_path}")
    try:
        async with reopened_domain_factory() as reopened_session:
            reopened_sample = await get_sample(
                reopened_session, domain_spec["id"], durable_ids["sample_id"]
            )
            reopened_sample_revision = await get_sample_revision(
                reopened_session,
                domain_spec["id"],
                durable_ids["sample_id"],
                durable_ids["sample_revision_1_id"],
            )
            reopened_reference = await get_reference_resource(
                reopened_session, durable_ids["reference_id"]
            )
            reopened_reference_revision = await get_reference_revision(
                reopened_session,
                durable_ids["reference_id"],
                durable_ids["reference_revision_1_id"],
            )
            reopened_state_revision = await get_state_revision(
                reopened_session,
                domain_spec["id"],
                durable_ids["state_revision_id"],
            )
            state_body, graph = await verify_state_revision_integrity(
                reopened_session, reopened_state_revision
            )
            reopened_receipt = await resolve_ngs_reference_revision_receipt(
                reopened_session,
                global_domain_experiment_id=domain_spec["id"],
                reference_id=durable_ids["reference_id"],
                revision_id=durable_ids["reference_revision_1_id"],
            )
            reopened_review = await get_evidence_assessment(
                reopened_session,
                domain_spec["id"],
                durable_ids["review_evidence_id"],
            )
            reopened_pass = await get_evidence_assessment(
                reopened_session,
                domain_spec["id"],
                durable_ids["pass_evidence_id"],
            )
            evidence_history = await list_evidence_assessments(
                reopened_session, domain_spec["id"]
            )
            reopened_typed_receipt = (
                await reopened_session.execute(
                    select(MolBioNGSMemberReceipt).where(
                        MolBioNGSMemberReceipt.receipt_id
                        == durable_ids["typed_assessment_receipt_id"]
                    )
                )
            ).scalar_one()

            assert reopened_sample.id == durable_ids["sample_id"]
            assert reopened_sample.current_revision_id == durable_ids["sample_revision_2_id"]
            assert reopened_sample_revision.payload_sha256 == durable_digests["sample_payload"]
            assert reopened_reference.id == durable_ids["reference_id"]
            assert reopened_reference.current_revision_id == durable_ids["reference_revision_2_id"]
            assert reopened_reference.archived_at is not None
            assert reopened_reference_revision.payload_sha256 == durable_digests["reference_payload"]
            assert reopened_reference_revision.canonical_fasta_sha256 == durable_digests["fasta"]
            assert await read_reference_artifact_bytes(
                reopened_session, reopened_reference_revision
            ) == first_fasta_bytes
            assert reopened_state_revision.payload_sha256 == durable_digests["state_payload"]
            assert reopened_state_revision.membership_graph_sha256 == durable_digests["membership"]
            assert state_body["design"]["sample_revision_ids"] == [
                durable_ids["sample_revision_1_id"]
            ]
            assert graph[0]["sample_revision_id"] == durable_ids["sample_revision_1_id"]
            assert reopened_receipt.content_digest == durable_digests["fasta"]
            assert reopened_receipt.reopen_destination == {
                "surface": "molbio-ngs-reference-revision",
                "params": {
                    "reference_id": durable_ids["reference_id"],
                    "revision_id": durable_ids["reference_revision_1_id"],
                },
            }
            assert reference_root.resolve() in (
                reference_root / reopened_reference_revision.artifact_id
            ).parents or reference_root.exists()
            assert reopened_review.scientific_assessment == "REVIEW"
            assert reopened_review.job_lifecycle_state == "completed"
            assert reopened_review.manifest_integrity == "valid"
            assert reopened_review.wrapper_sha256 == durable_digests["review_wrapper"]
            assert reopened_pass.scientific_assessment == "PASS"
            assert reopened_pass.job_lifecycle_state == "completed"
            assert reopened_pass.manifest_integrity == "valid"
            assert reopened_pass.raw_manifest_sha256 == durable_digests["pass_manifest"]
            assert reopened_pass.wrapper_sha256 == durable_digests["pass_wrapper"]
            assert hashlib.sha256(reopened_pass.canonical_wrapper.encode("utf-8")).hexdigest() == (
                durable_digests["pass_wrapper"]
            )
            assert {assessment.evidence_id for assessment in evidence_history} == {
                durable_ids["review_evidence_id"],
                durable_ids["pass_evidence_id"],
            }
            assert len(
                (
                    await reopened_session.execute(select(MolBioNGSEvidenceAssessment))
                ).scalars().all()
            ) == 2
            assert reopened_typed_receipt.entity_kind == "ngs_evidence_assessment"
            assert reopened_typed_receipt.entity_id == durable_ids["pass_evidence_id"]
            assert reopened_typed_receipt.source_store_id == "molbio-ngs-domain"
            assert reopened_typed_receipt.content_digest == durable_digests["pass_wrapper"]
            assert reopened_typed_receipt.receipt_sha256 == durable_digests[
                "typed_assessment_receipt"
            ]
            assert json.loads(reopened_typed_receipt.reopen_destination) == {
                "surface": "molbio-ngs-evidence-assessment",
                "params": {
                    "global_domain_experiment_id": domain_spec["id"],
                    "evidence_id": durable_ids["pass_evidence_id"],
                },
            }

        with pytest.raises(ValidationError):
            CreateSampleRequest.model_validate(
                {
                    "payload": sample_spec["first"],
                    "idempotency_key": "strict-proof",
                    "unexpected": True,
                }
            )
        with pytest.raises(ValidationError):
            AttachJobEvidenceRequest.model_validate(
                {
                    "job_id": evidence_spec["job_id"],
                    "content_digest": "0" * 64,
                }
            )
        with pytest.raises(ValidationError):
            EvidenceAssessmentRequest.model_validate(
                {
                    "state_revision_id": durable_ids["state_revision_id"],
                    "ngs_job_receipt_id": "job-receipt",
                    "ngs_result_manifest_receipt_id": "manifest-receipt",
                    "ngs_reference_revision_receipt_id": "reference-receipt",
                    "assessment_rule_id": evidence_spec["assessment_rule_id"],
                    "requested_assessment": "PASS",
                    "idempotency_key": "strict-evidence-proof",
                }
            )
    finally:
        await reopened_domain_engine.dispose()
        await reopened_molbio_engine.dispose()

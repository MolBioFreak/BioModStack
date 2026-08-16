from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _domain_binding(domain_id: str, revision_id: str):
    from molbio_ngs_services import InternalVerifiedGlobalBinding

    return InternalVerifiedGlobalBinding(
        global_domain_experiment_id=domain_id,
        global_domain_experiment_revision_id=revision_id,
        global_domain_experiment_revision_digest="a" * 64,
        project_id="project-receipt-authority",
        project_generation="1",
        project_digest="b" * 64,
        project_receipt_id="project-receipt-authority",
        project_reopen_destination={
            "surface": "project",
            "params": {"project_id": "project-receipt-authority"},
        },
        global_experiment_id="global-experiment-receipt-authority",
        global_experiment_generation="1",
        global_experiment_digest="c" * 64,
        global_experiment_receipt_id="experiment-receipt-authority",
        global_experiment_reopen_destination={
            "surface": "global-experiment",
            "params": {
                "project_id": "project-receipt-authority",
                "experiment_id": "global-experiment-receipt-authority",
            },
        },
        verified_at="2026-08-09T00:00:00+00:00",
    )


def _state_payload() -> dict[str, object]:
    return {
        "schema": "bms.molbio-ngs.domain-state-revision.v1",
        "design": {
            "sample_revision_ids": [],
            "conditions": [],
            "replicates": [],
            "expected_molecule_roles": ["molecular_expected_construct"],
        },
        "reference_policy": {
            "required_roles": ["molecular_expected_construct"],
            "coordinate_policy": "exact_revision",
        },
        "acquisition_policy": {
            "platform": "ont",
            "required_terminal_manifest": True,
        },
        "analysis_policy": {
            "allowed_workflow_ids": ["ont_plasmid_qc"],
            "required_manifest_schemas": ["biomodstack.construct_verification.v2"],
        },
        "assessment_policy": {
            "rule_id": "server-owned-rule",
            "completion_is_scientific_pass": False,
        },
        "notes": "Receipt ownership fixture.",
    }


@pytest.mark.asyncio
async def test_phase2_fixture_lineage_is_digest_bound(tmp_path: Path, monkeypatch) -> None:
    """One bounded lineage fixture proves server-issued cross-plane authority.

    The global production migration does not yet admit ``domain_experiment``;
    this fixture therefore exercises the already-approved local integration
    contract without changing or pretending to qualify the global schema.
    """

    from database import Base, Job
    from molbio_database import create_molbio_engine, make_molbio_session_factory
    from molbio_models import (
        MolecularDocument,
        MolecularRevision,
        MolBioBase,
    )
    from molbio_ngs_database import (
        create_molbio_ngs_engine,
        create_molbio_ngs_session_factory,
    )
    from molbio_ngs_migrations import run_all
    from molbio_ngs_models import (
        MolBioNGSDomainStateMember,
        MolBioNGSMemberReceipt,
    )
    from molbio_ngs_services import (
        InternalVerifiedGlobalBinding,
        StateMember,
        initialize_domain_state,
        save_state_revision,
    )
    from routers.molbio_ngs_experiments import StateMemberInput
    from routers.molbio_ops import _resolve_owned_molecular_revision
    from services.job_result_roots import resolve_persisted_job_result_root
    from services.molbio_ngs_member_receipts import (
        persist_member_receipt,
        resolve_molecular_revision_receipt,
        resolve_ngs_job_receipt,
        resolve_ngs_result_manifest_receipt,
    )
    from services.molbio_ngs_receipts import (
        build_molbio_revision_binding,
        consume_molbio_ngs_receipt,
        issue_molbio_ngs_receipt,
    )

    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "molbio_ngs"
            / "phase2_lineage.json"
        ).read_text(encoding="utf-8")
    )
    document_spec = fixture["molecular_document"]
    domain_spec = fixture["domain"]
    job_spec = fixture["job"]

    results_root = tmp_path / "bms_results"
    input_root = tmp_path / "inputs"
    result_root = results_root / job_spec["output_dir"]
    manifest_path = result_root / job_spec["manifest_relative_path"]
    manifest_path.parent.mkdir(parents=True)
    manifest_payload = {
        **fixture["manifest"],
        "execution": {"status": "SUCCEEDED", "exit_code": 0},
        "threshold_profile": {
            "calibration_status": "experimental",
            "public_accuracy_validated": False,
        },
        "provenance": {
            "workflow": {"name": "fixture", "module": "fixture", "version": "1"},
            "commands": [{"argv": ["fixture"]}],
        },
        "checks": {
            name: {"status": "not_evaluated"}
            for name in (
                "sequence_identity",
                "read_support",
                "coverage",
                "contamination",
                "topology",
            )
        },
        "inputs": {},
        "artifacts": [],
    }
    manifest_bytes = (
        json.dumps(
            manifest_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)

    import services.job_result_roots as job_result_roots
    import services.molbio_ngs_receipts as molbio_ngs_receipts

    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(molbio_ngs_receipts, "get_inputs_dir", lambda: input_root)

    molbio_engine = create_molbio_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}"
    )
    molbio_factory = make_molbio_session_factory(molbio_engine)
    core_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'core.db'}",
        connect_args={"timeout": 30},
    )
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    domain_path = tmp_path / "molbio_ngs.db"
    run_all(domain_path)
    domain_engine = create_molbio_ngs_engine(
        f"sqlite+aiosqlite:///{domain_path}"
    )
    domain_factory = create_molbio_ngs_session_factory(domain_engine)

    try:
        async with molbio_engine.begin() as connection:
            await connection.run_sync(MolBioBase.metadata.create_all)
        async with core_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        historical_sha = hashlib.sha256(
            document_spec["historical_sequence"].encode("utf-8")
        ).hexdigest()
        current_sha = hashlib.sha256(
            document_spec["current_sequence"].encode("utf-8")
        ).hexdigest()
        now = datetime(2026, 8, 8, 22, 0, 0)
        async with molbio_factory() as molbio_session:
            document = MolecularDocument(
                id=document_spec["id"],
                document_kind="nucleotide_sequence",
                name="Phase 2 construct",
                current_revision_id=None,
                created_at=now,
            )
            molbio_session.add(document)
            await molbio_session.flush()
            historical = MolecularRevision(
                id=document_spec["historical_revision_id"],
                document_id=document.id,
                revision_number=1,
                change_kind="create",
                content_sha256=historical_sha,
                content_length=len(document_spec["historical_sequence"]),
                snapshot={
                    "sequence": document_spec["historical_sequence"],
                    "sequence_type": "dna",
                    "name": "historical",
                },
                provenance={"fixture": "phase2"},
                created_at=now,
            )
            current = MolecularRevision(
                id=document_spec["current_revision_id"],
                document_id=document.id,
                revision_number=2,
                change_kind="edit",
                content_sha256=current_sha,
                content_length=len(document_spec["current_sequence"]),
                snapshot={
                    "sequence": document_spec["current_sequence"],
                    "sequence_type": "dna",
                    "name": "current",
                },
                provenance={"fixture": "phase2"},
                created_at=now,
            )
            molbio_session.add_all([historical, current])
            await molbio_session.flush()
            document.current_revision_id = current.id
            await molbio_session.commit()

            resolved_historical = await _resolve_owned_molecular_revision(
                molbio_session,
                document.id,
                historical.id,
            )
            assert resolved_historical.id == historical.id
            assert resolved_historical.id != document.current_revision_id
            molecular_member = await resolve_molecular_revision_receipt(
                molbio_session,
                sequence_id=document.id,
                revision_id=historical.id,
            )

        async with core_factory() as core_session:
            receipt = await issue_molbio_ngs_receipt(
                core_session,
                sequence_id=document_spec["id"],
                revision=resolved_historical,
            )
            binding = build_molbio_revision_binding(receipt)
            assert binding == {
                "sequence_id": document_spec["id"],
                "revision_id": document_spec["historical_revision_id"],
                "revision_sha256": historical_sha,
                "reference_snapshot_sha256": receipt.reference_snapshot_sha256,
                "receipt_id": receipt.id,
                "receipt_schema": "bms.molbio.ngs-receipt.v2",
                "binding_source": "server_consumed_receipt",
            }

            job = Job(
                id=job_spec["id"],
                name=job_spec["name"],
                status="completed",
                model_id="nanopore",
                mode="plasmid_qc",
                params={
                    "ont_workflow_id": "ont_plasmid_qc",
                    "molbio_revision_binding": binding,
                },
                output_dir=job_spec["output_dir"],
                created_at=now,
            )
            claimed_receipt = await consume_molbio_ngs_receipt(
                core_session,
                receipt_id=receipt.id,
            )
            core_session.add(job)
            claimed_receipt.consumed_job_id = job.id
            await core_session.commit()
            with pytest.raises(ValueError, match="already used"):
                await consume_molbio_ngs_receipt(core_session, receipt_id=receipt.id)
            await core_session.rollback()
            await core_session.refresh(job)
            assert resolve_persisted_job_result_root(job) == result_root.resolve()


        async with domain_factory() as domain_session:
            molecular_receipt = await persist_member_receipt(
                domain_session, molecular_member
            )
            binding_authority = InternalVerifiedGlobalBinding(
                global_domain_experiment_id=domain_spec["id"],
                global_domain_experiment_revision_id=domain_spec[
                    "global_revision_id"
                ],
                global_domain_experiment_revision_digest="a" * 64,
                project_id="project-phase2",
                project_generation="1",
                project_digest="b" * 64,
                project_receipt_id="project-receipt-phase2",
                project_reopen_destination={
                    "surface": "project",
                    "params": {"project_id": "project-phase2"},
                },
                global_experiment_id="global-experiment-phase2",
                global_experiment_generation="1",
                global_experiment_digest="c" * 64,
                global_experiment_receipt_id="experiment-receipt-phase2",
                global_experiment_reopen_destination={
                    "surface": "global-experiment",
                    "params": {
                        "project_id": "project-phase2",
                        "experiment_id": "global-experiment-phase2",
                    },
                },
                verified_at="2026-08-08T22:00:00+00:00",
            )
            await initialize_domain_state(
                domain_session,
                binding_authority,
                idempotency_key="phase2-init",
            )
            payload = {
                "schema": "bms.molbio-ngs.domain-state-revision.v1",
                "design": {
                    "sample_revision_ids": [],
                    "conditions": [],
                    "replicates": [],
                    "expected_molecule_roles": ["molecular_expected_construct"],
                },
                "reference_policy": {
                    "required_roles": ["molecular_expected_construct"],
                    "coordinate_policy": "exact_revision",
                },
                "acquisition_policy": {
                    "platform": "ont",
                    "required_terminal_manifest": True,
                },
                "analysis_policy": {
                    "allowed_workflow_ids": ["ont_plasmid_qc"],
                    "required_manifest_schemas": ["biomodstack.construct_verification.v2"],
                },
                "assessment_policy": {
                    "rule_id": "server-owned-rule",
                    "completion_is_scientific_pass": False,
                },
                "notes": "Digest-bound Phase 2 fixture.",
            }
            initial_revision = await save_state_revision(
                domain_session,
                global_domain_experiment_id=domain_spec["id"],
                global_domain_experiment_revision_id=domain_spec[
                    "global_revision_id"
                ],
                payload={**payload, "notes": "Pre-launch Phase 2 fixture state."},
                members=[
                    StateMember(
                        receipt_id=molecular_receipt.receipt_id,
                        role="molecular_expected_construct",
                        ordinal=0,
                    )
                ],
                expected_head_generation=0,
                parent_revision_id=None,
                idempotency_key="phase2-pre-launch-state",
            )
            initial_revision_id = initial_revision.id

            async with core_factory() as authority_session:
                authoritative_job = await authority_session.get(Job, job_spec["id"])
                assert authoritative_job is not None
                authoritative_job.params = {
                    **authoritative_job.params,
                    "global_domain_experiment_id": domain_spec["id"],
                    "molbio_ngs_state_revision_id": initial_revision_id,
                }
                await authority_session.commit()
                job_member = await resolve_ngs_job_receipt(
                    authority_session,
                    job_id=authoritative_job.id,
                )
                manifest_member = await resolve_ngs_result_manifest_receipt(
                    authority_session,
                    job_id=authoritative_job.id,
                )
                assert manifest_member.content_digest == hashlib.sha256(
                    manifest_bytes
                ).hexdigest()
                assert manifest_member.source_schema == fixture["manifest"]["schema"]
                persisted = [
                    molecular_receipt,
                    await persist_member_receipt(domain_session, job_member),
                    await persist_member_receipt(domain_session, manifest_member),
                ]
                persisted_receipt_ids = [item.receipt_id for item in persisted]
                revision = await save_state_revision(
                    domain_session,
                    core_session=authority_session,
                    global_domain_experiment_id=domain_spec["id"],
                    global_domain_experiment_revision_id=domain_spec[
                        "global_revision_id"
                    ],
                    payload=payload,
                    members=[
                        StateMember(
                            receipt_id=persisted_receipt_ids[0],
                            role="molecular_expected_construct",
                            ordinal=0,
                        ),
                        StateMember(
                            receipt_id=persisted_receipt_ids[1],
                            role="ngs_analysis_job",
                            ordinal=1,
                        ),
                        StateMember(
                            receipt_id=persisted_receipt_ids[2],
                            role="ngs_analysis_result_manifest",
                            ordinal=2,
                        ),
                    ],
                    expected_head_generation=1,
                    parent_revision_id=initial_revision_id,
                    idempotency_key="phase2-state",
                )
            await domain_session.commit()

            stored_receipts = (
                await domain_session.execute(select(MolBioNGSMemberReceipt))
            ).scalars().all()
            assert len(stored_receipts) == 3
            for stored in stored_receipts:
                assert stored.receipt_sha256 == hashlib.sha256(
                    stored.canonical_receipt.encode("utf-8")
                ).hexdigest()
            stored_members = (
                await domain_session.execute(
                    select(MolBioNGSDomainStateMember).where(
                        MolBioNGSDomainStateMember.state_revision_id == revision.id
                    )
                )
            ).scalars().all()
            assert {member.receipt_id for member in stored_members} == set(
                persisted_receipt_ids
            )
            assert all(not hasattr(member, "member_digest") for member in stored_members)

        with pytest.raises(ValidationError):
            StateMemberInput.model_validate(
                {
                    "receipt_id": persisted_receipt_ids[0],
                    "role": "molecular_expected_construct",
                    "ordinal": 0,
                    "member_digest": "0" * 64,
                }
            )
        with sqlite3.connect(domain_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="member receipt is immutable"):
                connection.execute(
                    "UPDATE molbio_ngs_member_receipts SET availability='unavailable' "
                    "WHERE receipt_id=?",
                    (persisted_receipt_ids[0],),
                )
    finally:
        await molbio_engine.dispose()
        await core_engine.dispose()
        await domain_engine.dispose()


@pytest.mark.asyncio
async def test_optional_evidence_receipts_require_exact_job_launch_bindings(
    tmp_path: Path, monkeypatch
) -> None:
    from database import Job
    from molbio_ngs_database import (
        create_molbio_ngs_engine,
        create_molbio_ngs_session_factory,
    )
    from molbio_ngs_migrations import run_all
    from molbio_ngs_services import StateIntegrityError
    import services.molbio_ngs_evidence as evidence
    from services.molbio_ngs_member_receipts import (
        build_external_member_receipt,
        persist_member_receipt,
    )

    domain_path = tmp_path / "molbio_ngs.db"
    run_all(domain_path)
    domain_engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{domain_path}")
    domain_factory = create_molbio_ngs_session_factory(domain_engine)
    receipts = {
        "molecular": build_external_member_receipt(
            source_store_id="molbio",
            entity_kind="molecular_revision",
            entity_id="molecular-revision-1",
            source_generation_or_revision="7",
            content_digest="a" * 64,
            source_schema="bms.molbio.molecular-revision.v1",
            availability="available",
            reopen_destination={
                "surface": "molbio-sequence-revision",
                "params": {
                    "sequence_id": "sequence-1",
                    "revision_id": "molecular-revision-1",
                },
            },
        ),
        "ont": build_external_member_receipt(
            source_store_id="core-ngs",
            entity_kind="ont_instrument_run",
            entity_id="ont-run-1",
            source_generation_or_revision="3",
            content_digest="b" * 64,
            source_schema="bms.ont.instrument-run-observation.v1",
            availability="available",
            reopen_destination={
                "surface": "ont-instrument-run",
                "params": {"run_id": "ont-run-1", "observed_generation": 3},
            },
        ),
        "comparison": build_external_member_receipt(
            source_store_id="core-ngs",
            entity_kind="ngs_comparison_panel",
            entity_id="panel-1",
            source_generation_or_revision="2",
            content_digest="c" * 64,
            source_schema="bms.ngs.comparison-panel.v1",
            availability="available",
            reopen_destination={
                "surface": "ngs-comparison-panel",
                "params": {"panel_id": "panel-1", "panel_version": 2},
            },
        ),
    }

    async def resolved_molecular(*_args, **_kwargs):
        return receipts["molecular"]

    async def resolved_ont(*_args, **_kwargs):
        return receipts["ont"]

    async def resolved_comparison(*_args, **_kwargs):
        return receipts["comparison"]

    monkeypatch.setattr(evidence, "resolve_molecular_revision_receipt", resolved_molecular)
    monkeypatch.setattr(evidence, "resolve_ont_instrument_run_receipt", resolved_ont)
    monkeypatch.setattr(evidence, "resolve_approved_comparison_panel_receipt", resolved_comparison)

    try:
        async with domain_factory() as session:
            stored = {
                name: await persist_member_receipt(session, receipt)
                for name, receipt in receipts.items()
            }
            await session.commit()
            lanes = {
                "molecular": {
                    "kwargs": {"molecular_revision_receipt_id": stored["molecular"].receipt_id},
                    "wrong": {
                        "molbio_revision_binding": {
                            "sequence_id": "sequence-other",
                            "revision_id": "molecular-revision-1",
                            "revision_sha256": "a" * 64,
                        }
                    },
                    "exact": {
                        "molbio_revision_binding": {
                            "sequence_id": "sequence-1",
                            "revision_id": "molecular-revision-1",
                            "revision_sha256": "a" * 64,
                        }
                    },
                },
                "ont": {
                    "kwargs": {"ont_instrument_run_receipt_id": stored["ont"].receipt_id},
                    "wrong": {
                        "ont_instrument_run_binding": {
                            "run_id": "ont-run-other",
                            "observed_generation": 3,
                            "observation_sha256": "b" * 64,
                        }
                    },
                    "exact": {
                        "ont_instrument_run_binding": {
                            "run_id": "ont-run-1",
                            "observed_generation": 3,
                            "observation_sha256": "b" * 64,
                        }
                    },
                },
                "comparison": {
                    "kwargs": {"ngs_comparison_panel_receipt_id": stored["comparison"].receipt_id},
                    "wrong": {
                        "comparison_panel_binding": {
                            "panel_id": "panel-other",
                            "panel_version": 2,
                            "panel_snapshot_sha256": "c" * 64,
                        }
                    },
                    "exact": {
                        "comparison_panel_binding": {
                            "panel_id": "panel-1",
                            "panel_version": 2,
                            "panel_snapshot_sha256": "c" * 64,
                        }
                    },
                },
            }
            defaults = {
                "ont_instrument_run_receipt_id": None,
                "molecular_revision_receipt_id": None,
                "ngs_comparison_panel_receipt_id": None,
            }
            for label, lane in lanes.items():
                kwargs = {**defaults, **lane["kwargs"]}
                job = Job(
                    id=f"{label}-job",
                    name=f"{label} launch binding proof",
                    status="completed",
                    model_id="nanopore",
                    mode="plasmid_qc",
                    params={},
                    created_at=datetime(2026, 8, 8, 22, 0, 0),
                )
                with pytest.raises(StateIntegrityError, match=f"{label}.*launch binding"):
                    await evidence._verify_optional_receipts(
                        session, object(), object(), job=job, **kwargs
                    )
                job.params = lane["wrong"]
                with pytest.raises(StateIntegrityError, match=f"{label}.*launch binding"):
                    await evidence._verify_optional_receipts(
                        session, object(), object(), job=job, **kwargs
                    )
                job.params = lane["exact"]
                await evidence._verify_optional_receipts(
                    session, object(), object(), job=job, **kwargs
                )
    finally:
        await domain_engine.dispose()


@pytest.mark.asyncio
async def test_state_save_rejects_cross_domain_job_and_result_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    from database import Base, Job
    from molbio_ngs_database import (
        create_molbio_ngs_engine,
        create_molbio_ngs_session_factory,
    )
    from molbio_ngs_migrations import run_all
    from molbio_ngs_services import (
        StateMember,
        StateValidationError,
        initialize_domain_state,
        save_state_revision,
    )
    from services.molbio_ngs_member_receipts import (
        build_external_member_receipt,
        persist_member_receipt,
        resolve_ngs_job_receipt,
        resolve_ngs_result_manifest_receipt,
    )

    results_root = tmp_path / "results"
    monkeypatch.setattr(
        "services.job_result_roots.get_results_dir", lambda: results_root
    )
    result_root = results_root / "domain-2-job"
    result_root.mkdir(parents=True)
    manifest = {
        "artifact_schema_version": 1,
        "job_id": "job-domain-2",
        "workflow_status": "completed",
        "verification_status": "review",
        "artifacts": [],
    }
    (result_root / "qc_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    domain_path = tmp_path / "domain.db"
    run_all(domain_path)
    domain_engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{domain_path}")
    domain_factory = create_molbio_ngs_session_factory(domain_engine)
    try:
        async with core_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with core_factory() as core_session:
            core_session.add(
                Job(
                    id="job-domain-2",
                    name="Domain 2 job",
                    status="completed",
                    queue_status="completed",
                    model_id="nanopore",
                    mode="plasmid_qc",
                    params={
                        "global_domain_experiment_id": "domain-2",
                        "molbio_ngs_state_revision_id": "state-domain-2",
                    },
                    output_dir="domain-2-job",
                    created_at=datetime(2026, 8, 9, 0, 0, 0),
                )
            )
            await core_session.commit()
            job_receipt = await resolve_ngs_job_receipt(
                core_session, job_id="job-domain-2"
            )
            result_receipt = await resolve_ngs_result_manifest_receipt(
                core_session, job_id="job-domain-2"
            )

            async with domain_factory() as domain_session:
                await initialize_domain_state(
                    domain_session,
                    _domain_binding("domain-1", "global-domain-rev-1"),
                    idempotency_key="init-domain-1",
                )
                await initialize_domain_state(
                    domain_session,
                    _domain_binding("domain-2", "global-domain-rev-2"),
                    idempotency_key="init-domain-2",
                )
                external = await persist_member_receipt(
                    domain_session,
                    build_external_member_receipt(
                        source_store_id="molbio",
                        entity_kind="molecular_revision",
                        entity_id="external-revision",
                        source_generation_or_revision="1",
                        content_digest="d" * 64,
                        source_schema="bms.molbio.molecular-revision.v1",
                        availability="available",
                        reopen_destination={
                            "surface": "molbio-sequence-revision",
                            "params": {
                                "sequence_id": "external-sequence",
                                "revision_id": "external-revision",
                            },
                        },
                    ),
                )
                domain_2_state = await save_state_revision(
                    domain_session,
                    global_domain_experiment_id="domain-2",
                    global_domain_experiment_revision_id="global-domain-rev-2",
                    payload=_state_payload(),
                    members=[
                        StateMember(
                            receipt_id=external.receipt_id,
                            role="molecular_expected_construct",
                            ordinal=0,
                        )
                    ],
                    expected_head_generation=0,
                    parent_revision_id=None,
                    idempotency_key="domain-2-base",
                )
                domain_2_state_2 = await save_state_revision(
                    domain_session,
                    global_domain_experiment_id="domain-2",
                    global_domain_experiment_revision_id="global-domain-rev-2",
                    payload={**_state_payload(), "notes": "Domain 2 second state."},
                    members=[
                        StateMember(
                            receipt_id=external.receipt_id,
                            role="molecular_expected_construct",
                            ordinal=0,
                        )
                    ],
                    expected_head_generation=1,
                    parent_revision_id=domain_2_state.id,
                    idempotency_key="domain-2-second-state",
                )
                domain_2_state_id = domain_2_state.id
                domain_2_state_2_id = domain_2_state_2.id
                job = await core_session.get(Job, "job-domain-2")
                assert job is not None
                job.params = {
                    **job.params,
                    "molbio_ngs_state_revision_id": domain_2_state_id,
                }
                await core_session.commit()
                wrong_state_job = await persist_member_receipt(
                    domain_session,
                    await resolve_ngs_job_receipt(core_session, job_id=job.id),
                )
                wrong_state_result = await persist_member_receipt(
                    domain_session,
                    await resolve_ngs_result_manifest_receipt(
                        core_session, job_id=job.id
                    ),
                )
                wrong_state_receipts = (
                    (wrong_state_job.receipt_id, "ngs_analysis_job"),
                    (
                        wrong_state_result.receipt_id,
                        "ngs_analysis_result_manifest",
                    ),
                )
                await domain_session.commit()

                for index, (receipt_id, role) in enumerate(wrong_state_receipts):
                    with pytest.raises(StateValidationError, match="exact state binding"):
                        await save_state_revision(
                            domain_session,
                            core_session=core_session,
                            global_domain_experiment_id="domain-2",
                            global_domain_experiment_revision_id="global-domain-rev-2",
                            payload=_state_payload(),
                            members=[
                                StateMember(
                                    receipt_id=receipt_id, role=role, ordinal=0
                                )
                            ],
                            expected_head_generation=2,
                            parent_revision_id=domain_2_state_2_id,
                            idempotency_key=f"reject-wrong-state-job-{index}",
                        )
                    await domain_session.rollback()

                job.params = {
                    **job.params,
                    "molbio_ngs_state_revision_id": domain_2_state_2_id,
                }
                await core_session.commit()
                stored_job = await persist_member_receipt(
                    domain_session,
                    await resolve_ngs_job_receipt(core_session, job_id=job.id),
                )
                stored_result = await persist_member_receipt(
                    domain_session,
                    await resolve_ngs_result_manifest_receipt(
                        core_session, job_id=job.id
                    ),
                )
                stored_job_receipt_id = stored_job.receipt_id
                stored_result_receipt_id = stored_result.receipt_id
                await domain_session.commit()

                for index, (receipt_id, role) in enumerate(
                    (
                        (stored_job_receipt_id, "ngs_analysis_job"),
                        (
                            stored_result_receipt_id,
                            "ngs_analysis_result_manifest",
                        ),
                    )
                ):
                    with pytest.raises(StateValidationError, match="Domain Experiment"):
                        await save_state_revision(
                            domain_session,
                            core_session=core_session,
                            global_domain_experiment_id="domain-1",
                            global_domain_experiment_revision_id="global-domain-rev-1",
                            payload=_state_payload(),
                            members=[
                                StateMember(
                                    receipt_id=receipt_id, role=role, ordinal=0
                                )
                            ],
                            expected_head_generation=0,
                            parent_revision_id=None,
                            idempotency_key=f"reject-cross-domain-job-{index}",
                        )
                    await domain_session.rollback()

                accepted = await save_state_revision(
                    domain_session,
                    core_session=core_session,
                    global_domain_experiment_id="domain-2",
                    global_domain_experiment_revision_id="global-domain-rev-2",
                    payload=_state_payload(),
                    members=[
                        StateMember(
                            receipt_id=stored_job_receipt_id,
                            role="ngs_analysis_job",
                            ordinal=0,
                        ),
                        StateMember(
                            receipt_id=stored_result_receipt_id,
                            role="ngs_analysis_result_manifest",
                            ordinal=1,
                        ),
                    ],
                    expected_head_generation=2,
                    parent_revision_id=domain_2_state_2_id,
                    idempotency_key="accept-domain-2-job-result",
                )
                assert accepted.global_domain_experiment_id == "domain-2"
    finally:
        await core_engine.dispose()
        await domain_engine.dispose()


@pytest.mark.asyncio
async def test_instrument_run_attachment_requires_exact_same_domain_state_binding(
    tmp_path: Path,
) -> None:
    from database import Base, OntInstrumentRun, OntInstrumentRunEvent
    from molbio_ngs_database import (
        create_molbio_ngs_engine,
        create_molbio_ngs_session_factory,
    )
    from molbio_ngs_migrations import run_all
    from molbio_ngs_services import (
        StateIntegrityError,
        StateMember,
        StateValidationError,
        initialize_domain_state,
        save_state_revision,
    )
    from services.molbio_ngs_evidence import attach_instrument_run_evidence
    from services.molbio_ngs_member_receipts import (
        build_external_member_receipt,
        persist_member_receipt,
        resolve_ont_instrument_run_receipt,
    )

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core-ont.db'}")
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    domain_path = tmp_path / "domain-ont.db"
    run_all(domain_path)
    domain_engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{domain_path}")
    domain_factory = create_molbio_ngs_session_factory(domain_engine)
    observed_at = datetime(2026, 8, 9, 1, 0, 0)
    try:
        async with core_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with core_factory() as core_session:
            core_session.add(
                OntInstrumentRun(
                    id="ont-run-authority",
                    position_id="position-1",
                    state="completed",
                    observed_at=observed_at,
                    observed_generation=3,
                    output_directories={},
                    output_files={},
                    handoff_ready=True,
                    created_at=observed_at,
                )
            )
            core_session.add(
                OntInstrumentRunEvent(
                    id="ont-run-authority-event-3",
                    run_id="ont-run-authority",
                    event_type="completed",
                    state="completed",
                    observed_at=observed_at,
                    observed_generation=3,
                    minknow_payload={},
                    output_files={},
                )
            )
            await core_session.commit()

            async with domain_factory() as domain_session:
                await initialize_domain_state(
                    domain_session,
                    _domain_binding("domain-1", "global-domain-rev-1"),
                    idempotency_key="init-domain-1-ont",
                )
                await initialize_domain_state(
                    domain_session,
                    _domain_binding("domain-2", "global-domain-rev-2"),
                    idempotency_key="init-domain-2-ont",
                )
                external = await persist_member_receipt(
                    domain_session,
                    build_external_member_receipt(
                        source_store_id="molbio",
                        entity_kind="molecular_revision",
                        entity_id="external-revision-ont",
                        source_generation_or_revision="1",
                        content_digest="e" * 64,
                        source_schema="bms.molbio.molecular-revision.v1",
                        availability="available",
                        reopen_destination={
                            "surface": "molbio-sequence-revision",
                            "params": {
                                "sequence_id": "external-sequence-ont",
                                "revision_id": "external-revision-ont",
                            },
                        },
                    ),
                )
                state_1 = await save_state_revision(
                    domain_session,
                    global_domain_experiment_id="domain-1",
                    global_domain_experiment_revision_id="global-domain-rev-1",
                    payload=_state_payload(),
                    members=[
                        StateMember(
                            receipt_id=external.receipt_id,
                            role="molecular_expected_construct",
                            ordinal=0,
                        )
                    ],
                    expected_head_generation=0,
                    parent_revision_id=None,
                    idempotency_key="domain-1-base-ont",
                )
                state_2 = await save_state_revision(
                    domain_session,
                    global_domain_experiment_id="domain-2",
                    global_domain_experiment_revision_id="global-domain-rev-2",
                    payload={**_state_payload(), "notes": "Domain 2 ONT base."},
                    members=[
                        StateMember(
                            receipt_id=external.receipt_id,
                            role="molecular_expected_construct",
                            ordinal=0,
                        )
                    ],
                    expected_head_generation=0,
                    parent_revision_id=None,
                    idempotency_key="domain-2-base-ont",
                )
                state_1_id = state_1.id
                state_2_id = state_2.id
                await domain_session.commit()

                with pytest.raises(StateIntegrityError, match="Domain Experiment"):
                    await attach_instrument_run_evidence(
                        domain_session,
                        core_session,
                        global_domain_experiment_id="domain-1",
                        state_revision_id=state_2_id,
                        run_id="ont-run-authority",
                        observed_generation=3,
                        idempotency_key="reject-wrong-domain-ont",
                    )
                await domain_session.rollback()

                unbound = await persist_member_receipt(
                    domain_session,
                    await resolve_ont_instrument_run_receipt(
                        core_session,
                        run_id="ont-run-authority",
                        observed_generation=3,
                    ),
                )
                unbound_receipt_id = unbound.receipt_id
                await domain_session.commit()
                with pytest.raises(StateValidationError, match="persisted association"):
                    await save_state_revision(
                        domain_session,
                        core_session=core_session,
                        global_domain_experiment_id="domain-1",
                        global_domain_experiment_revision_id="global-domain-rev-1",
                        payload=_state_payload(),
                        members=[
                            StateMember(
                                receipt_id=unbound_receipt_id,
                                role="ngs_instrument_run",
                                ordinal=0,
                            )
                        ],
                        expected_head_generation=1,
                        parent_revision_id=state_1_id,
                        idempotency_key="reject-unbound-ont",
                    )
                await domain_session.rollback()

                attached = await attach_instrument_run_evidence(
                    domain_session,
                    core_session,
                    global_domain_experiment_id="domain-1",
                    state_revision_id=state_1_id,
                    run_id="ont-run-authority",
                    observed_generation=3,
                    idempotency_key="attach-domain-1-ont",
                )
                accepted = await save_state_revision(
                    domain_session,
                    core_session=core_session,
                    global_domain_experiment_id="domain-1",
                    global_domain_experiment_revision_id="global-domain-rev-1",
                    payload=_state_payload(),
                    members=[
                        StateMember(
                            receipt_id=attached.receipt_id,
                            role="ngs_instrument_run",
                            ordinal=0,
                        )
                    ],
                    expected_head_generation=1,
                    parent_revision_id=state_1_id,
                    idempotency_key="accept-bound-ont",
                )
                assert accepted.global_domain_experiment_id == "domain-1"
    finally:
        await core_engine.dispose()
        await domain_engine.dispose()

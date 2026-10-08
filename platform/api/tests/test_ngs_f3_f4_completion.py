"""F3/F4 regression definitions. No managed services or historical migration.

The fixture seeds the existing local binding owner; the native producer receipt,
receipt persistence, immutable state membership, adapter and outbox are real.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
import rfc8785
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job
from molbio_ngs_models import (
    MolBioNGSBase, MolBioNGSDomainState, MolBioNGSGlobalBinding,
    MolBioNGSDomainStateRevision, MolBioNGSMemberReceipt, MolBioNGSOutboxEvent,
)
from molbio_ngs_services import save_state_revision, list_revision_members, StateValidationError
from services import molbio_ngs_evidence as evidence
from services.molbio_ngs_member_receipts import (
    ngs_job_eligibility, is_ngs_job_identity, ngs_result_manifest_identities,
    build_external_member_receipt, persist_member_receipt,
)
from services.ont_ngs_native_settings import seal_native_settings


def sha(value):
    return hashlib.sha256(value).hexdigest()


def payload():
    return {
        "schema": "bms.molbio-ngs.domain-state-revision.v1",
        "design": {"sample_revision_ids": [], "conditions": [], "replicates": [], "expected_molecule_roles": []},
        "reference_policy": {"required_roles": [], "coordinate_policy": "exact_revision"},
        "acquisition_policy": {"platform": "ont", "required_terminal_manifest": True},
        "analysis_policy": {"allowed_workflow_ids": ["ont_plasmid_qc"], "required_manifest_schemas": ["biomodstack.construct_verification.v2"]},
        "assessment_policy": {"rule_id": "server-owned-rule", "completion_is_scientific_pass": False},
        "notes": "Native membership is not a QC verdict.",
    }


@pytest_asyncio.fixture
async def stores():
    engines = [create_async_engine("sqlite+aiosqlite:///:memory:") for _ in range(2)]
    for engine, metadata in zip(engines, (Base.metadata, MolBioNGSBase.metadata)):
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
    factories = [sessionmaker(e, class_=AsyncSession, expire_on_commit=False) for e in engines]
    try:
        async with factories[0]() as core, factories[1]() as domain:
            binding = MolBioNGSGlobalBinding(
                binding_revision_id="binding", global_domain_experiment_id="domain", revision_number=1,
                global_domain_experiment_revision_id="global-rev", global_domain_experiment_revision_digest=sha(b"domain"),
                project_id="project", project_generation="1", project_digest=sha(b"project"), project_receipt_id="project-receipt",
                project_reopen_destination="{}", global_experiment_id="experiment", global_experiment_generation="1",
                global_experiment_digest=sha(b"experiment"), global_experiment_receipt_id="experiment-receipt",
                global_experiment_reopen_destination="{}", binding_state="acknowledged",
            )
            domain.add(binding)
            domain.add(MolBioNGSDomainState(global_domain_experiment_id="domain", current_binding_revision_id="binding", head_generation=0))
            await domain.flush()
            initial = await save_state_revision(domain, global_domain_experiment_id="domain",
                global_domain_experiment_revision_id="global-rev", payload=payload(), members=[],
                expected_head_generation=0, parent_revision_id=None, idempotency_key="initial")
            await domain.commit()
            yield core, domain, initial
    finally:
        for engine in engines:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_native_attach_replay_membership_and_delivery(stores, tmp_path, mixed):
    core, domain, initial = stores
    files = {"native.json": b'{"native":"fixture"}'}
    if mixed:
        # Discovery is not QC verification: the native selector must not parse
        # this separate fixture artifact or manufacture its QC assessment.
        files["qc_manifest.json"] = b'{"fixture":"separate QC producer boundary"}'
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    artifacts = [{"path": name, "sha256": sha(content), "size_bytes": len(content)} for name, content in files.items()]
    params = {"ont_workflow_id": "ont_basecall_dna", "ont_input_mode": "pod5",
              "global_domain_experiment_id": "domain", "molbio_ngs_state_revision_id": initial.id}
    receipt = {"result_kind": "ont_native_basecall", "workflow_id": "ont_basecall_dna", "input_mode": "pod5",
               "state": "validated", "partial": False, "artifacts": artifacts,
               "artifact_set_sha256": sha(rfc8785.dumps(artifacts)), **seal_native_settings(params)}
    job = Job(id="native-job", name="Native fixture", model_id="nanopore", mode="ont_basecall_dna",
              params=params, provenance={"result_integrity": receipt}, output_dir=str(tmp_path),
              status="completed", queue_status="completed", awaiting_input=False)
    core.add(job)
    await core.commit()
    assert ngs_result_manifest_identities(job) == (("sequence-qc-manifest", "native-scientific-result") if mixed else ("native-scientific-result",))
    if mixed:
        with pytest.raises(StateValidationError, match="select the exact"):
            await evidence.attach_job_evidence(domain, core, global_domain_experiment_id="domain", job_id=job.id, idempotency_key="ambiguous")
    revisions = []
    receipt_ids = []
    for key in ("lost-response", "lost-response", "fresh-click"):
        job_row, manifest = await evidence.attach_job_evidence(domain, core,
            global_domain_experiment_id="domain", job_id=job.id, idempotency_key=key,
            manifest_identity="native-scientific-result")
        attached = await evidence.attach_job_membership(domain, core, domain_id="domain",
            job_receipt=job_row, manifest_receipt=manifest, parent_revision_id=initial.id)
        await domain.commit()
        revisions.append(attached.id)
        receipt_ids.append((job_row.receipt_id, manifest.receipt_id))
    assert len(set(revisions)) == len(set(receipt_ids)) == 1
    assert await domain.scalar(select(func.count()).select_from(MolBioNGSMemberReceipt)) == 2
    assert await domain.scalar(select(func.count()).select_from(MolBioNGSDomainStateRevision)) == 2
    assert len(await list_revision_members(domain, attached.id)) == 2
    assert json.loads(attached.canonical_payload) == payload()
    assert job.params["molbio_ngs_state_revision_id"] == initial.id
    assert job.provenance["result_integrity"] == receipt
    assert await evidence.job_attachment_delivery(domain, attached.id, manifest.receipt_id) == "pending"
    # The adapter's exact local ownership proof accepts the persisted native
    # receipt without a QC-manifest identity or scientific-state head rewrite.
    from services.global_experiments.adapters import _exact_local_member_authority
    from services.molbio_ngs_member_receipts import resolve_ngs_result_manifest_receipt
    resolved = await resolve_ngs_result_manifest_receipt(core, job_id=job.id, manifest_identity="native-scientific-result")
    assert await _exact_local_member_authority(domain, member=resolved) == ("domain", manifest.receipt_id)
    duplicate = build_external_member_receipt(**{key: getattr(resolved, key) for key in (
        "source_store_id", "entity_kind", "entity_id", "source_generation_or_revision",
        "content_digest", "source_schema", "availability", "reopen_destination")}, receipt_id="legacy-wrapper")
    await persist_member_receipt(domain, duplicate)
    await domain.commit()
    assert await _exact_local_member_authority(domain, member=resolved) == ("domain", manifest.receipt_id)
    again_job, again_manifest = await evidence.attach_job_evidence(domain, core,
        global_domain_experiment_id="domain", job_id=job.id, idempotency_key="after-legacy-wrapper",
        manifest_identity="native-scientific-result")
    again = await evidence.attach_job_membership(domain, core, domain_id="domain",
        job_receipt=again_job, manifest_receipt=again_manifest, parent_revision_id=initial.id)
    assert again.id == attached.id
    reopened = await evidence.reopen_job_member(domain, core, domain_id="domain", receipt_id=manifest.receipt_id)
    assert reopened["job_id"] == job.id
    assert reopened["manifest_identity"] == "native-scientific-result"
    assert reopened["receipt_sha256"] == manifest.receipt_sha256
    from services.ngs_molbio_connector import _event_authority_spec
    spec = _event_authority_spec(event_type="molbio_ngs.member_receipt.published",
        payload={"receipt_id": manifest.receipt_id, "receipt_kind": "ngs_result_manifest", "native_generation": attached.revision_number,
                 "receipt_sha256": manifest.receipt_sha256}, payload_sha256=sha(b"fixture event"), domain_id="domain")
    assert "native_member_receipt_id=" in spec["reopen_uri"]
    assert spec["content_digest"] == manifest.receipt_sha256


    event = (await domain.scalars(select(MolBioNGSOutboxEvent).where(
        MolBioNGSOutboxEvent.state_revision_id == attached.id,
        MolBioNGSOutboxEvent.event_stream == f"member:ngs_result_manifest:{resolved.entity_id}"))).one()
    event.status = "conflict"
    assert await evidence.job_attachment_delivery(domain, attached.id, manifest.receipt_id) == "conflict"


@pytest.mark.asyncio
async def test_eligible_discovery_filters_before_limit(stores):
    core, _, _initial = stores
    identities = [(None, "unrelated", {}), (None, "legacy", {"workflow_id": "ont_basecall_dna"}),
                  ("unrelated", "ont_basecall_dna", {}), ("ont_methylation_analysis", "native", {})]
    for index, (model, mode, params) in enumerate(identities):
        core.add(Job(id=f"job-{index}", name="fixture", model_id=model, mode=mode, params=params,
                     status="completed", created_at=datetime(2026, 1, index + 1, tzinfo=timezone.utc)))
    await core.commit()
    rows = list((await core.scalars(select(Job).where(ngs_job_eligibility()).order_by(Job.id).limit(1))).all())
    assert [row.id for row in rows] == ["job-1"]
    assert all(is_ngs_job_identity(row) for row in rows)


def test_pooled_native_identity_is_not_catalog_eligibility():
    from services.ngs_native_alignment_sources import is_native, is_native_result
    job = SimpleNamespace(provenance={"result_integrity": {"result_kind": "ont_native_pooled_assignment"}})
    assert is_native_result(job) is True
    assert is_native(job) is False
    assert ngs_result_manifest_identities(job) == ("native-scientific-result",)


@pytest.mark.parametrize("count,target,max_records,expected,reason", [
    (0, 4, 20, "empty", "no_mapped_primary_reads"),
    (6, 4, 20, "capped", "read_limit"),
    (6, 4, 1, "reduced", "record_limit"),
])
def test_preview_producer_persists_admission_population(tmp_path, monkeypatch, count, target, max_records, expected, reason):
    import pysam
    from services import ngs_alignment_product_builder as builder
    bam = tmp_path / "source.bam"
    header = pysam.AlignmentHeader.from_dict({"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "ref", "LN": 1000}]})
    with pysam.AlignmentFile(str(bam), "wb", header=header) as output:
        for index in range(count):
            record = pysam.AlignedSegment(header)
            record.query_name = f"read-{index}"
            record.query_sequence = "ACGT"
            record.query_qualities = [30] * 4
            record.reference_id = 0
            record.reference_start = index * 4
            record.mapping_quality = 60
            record.cigarstring = "4M"
            output.write(record)
    catalog = tmp_path / "catalog"
    preview = tmp_path / "preview"
    catalog.mkdir(); preview.mkdir()
    allocation = SimpleNamespace(dram_bytes=64 * 1024 * 1024, disk_bytes=256 * 1024 * 1024)
    builder._catalog_tables(catalog, str(bam), lambda: None, allocation)
    policy = {**builder.resolved_preview_policy(), "target_reads": target, "max_records": max_records}
    monkeypatch.setattr(builder, "resolved_preview_policy", lambda: policy)
    _header, _metadata, selected, stats = builder._preview_plan(preview, catalog, str(bam), policy, sha(b"source"), lambda: None, allocation)
    assert stats["population_state"] == expected
    assert reason in stats["population_reasons"]
    assert stats["eligible_read_count"] == count
    assert stats["selected_read_count"] == len(selected)


@pytest.mark.parametrize("state,selected,eligible,reasons", [
    ("empty", 0, 0, ["no_mapped_primary_reads"]),
    ("reduced", 2, 8, ["long_cigar_exclusion", "byte_limit"]),
    ("capped", 5000, 6000, ["read_limit"]),
])
def test_ready_preview_reader_preserves_population(monkeypatch, state, selected, eligible, reasons):
    from services import ngs_alignment_product_reader as reader
    statistics = {"selected_read_count": selected, "selected_record_count": selected,
        "eligible_read_count": eligible, "target_read_count": min(eligible, 5000),
        "population_state": state, "population_reasons": reasons, "excluded_long_cigar_reads": 0}
    @contextmanager
    def snapshot(*args):
        yield None, {"statistics": statistics, "authority": {"policy": {}, "artifacts": {
            role: {"sha256": sha(role.encode()), "size_bytes": 28} for role in ("bam", "index")}}}
    monkeypatch.setattr(reader, "preview_snapshot", snapshot)
    result = reader.preview_response(SimpleNamespace(id="job"),
        SimpleNamespace(session_id="session", source_identity={}, authority_sha256=sha(b"catalog")),
        SimpleNamespace(id="preview", authority_sha256=sha(b"preview")), None)
    assert result["population"]["population_state"] == state
    assert result["population"]["population_reasons"] == reasons
    assert result["selected_read_count"] == selected

"""Tests for Slice 1 Project Hub, Project Sequences, scope fallback, and echo suppression."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base as CoreBase
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentBase,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
)
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision, NucleotideSequence
from molbio_ngs_models import (
    MolBioNGSBase,
    MolBioNGSDomainState,
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSMemberReceipt,
)
from services.project_hub import (
    get_project_hub_payload,
    get_project_sequences_payload,
    get_projects_summary_counts,
)


async def _make_sessions(tmp_path: Path):
    e_exp = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'experiments.db'}")
    e_core = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    e_ngs = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ngs.db'}")
    e_mol = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mol.db'}")
    async with e_exp.begin() as conn:
        await conn.run_sync(ExperimentBase.metadata.create_all)
    async with e_core.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    async with e_ngs.begin() as conn:
        await conn.run_sync(MolBioNGSBase.metadata.create_all)
    async with e_mol.begin() as conn:
        await conn.run_sync(MolBioBase.metadata.create_all)
    return (
        (e_exp, e_core, e_ngs, e_mol),
        async_sessionmaker(e_exp, class_=AsyncSession, expire_on_commit=False),
        async_sessionmaker(e_core, class_=AsyncSession, expire_on_commit=False),
        async_sessionmaker(e_ngs, class_=AsyncSession, expire_on_commit=False),
        async_sessionmaker(e_mol, class_=AsyncSession, expire_on_commit=False),
    )


async def _seed_aggregate(
    session: AsyncSession,
    *,
    aggregate_id: str,
    aggregate_kind: str,
    workspace_id: str,
    display_name: str,
    payload: dict,
    parent_id: str | None = None,
    dataset_kind: str | None = None,
    lifecycle_state: str = "active",
) -> None:
    rev_id = f"rev-{uuid.uuid4().hex[:10]}"
    rev = ExperimentRevision(
        resource_id=rev_id,
        subject_id=aggregate_id,
        revision_number=1,
        parent_revision_id=None,
        schema_name=payload.get("schema", "bms.project.v2"),
        schema_version="2.0",
        canonical_payload=json.dumps(payload, sort_keys=True),
        payload_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        dependency_graph_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        provenance_json="{}",
    )
    head = ExperimentAggregateHead(
        aggregate_id=aggregate_id,
        aggregate_kind=aggregate_kind,
        workspace_id=workspace_id,
        parent_id=parent_id,
        current_revision_id=rev_id,
        head_generation=1,
        lifecycle_state=lifecycle_state,
        display_name=display_name,
        description="",
        dataset_kind=dataset_kind,
    )
    session.add(rev)
    session.add(head)
    await session.flush()


@pytest.mark.asyncio
async def test_project_sequences_zero_one_and_multi_domain_dedup(tmp_path: Path):
    engines, SExp, SCore, SNgs, SMol = await _make_sessions(tmp_path)
    try:
        async with SExp() as s_exp, SNgs() as s_ngs, SMol() as s_mol:
            # Project 0: 0 domains + NULL project_scope -> [] and global fallback
            await _seed_aggregate(
                s_exp,
                aggregate_id="proj-zero",
                aggregate_kind="workspace",
                workspace_id="proj-zero",
                display_name="Zero Domain Project",
                payload={"schema": "bms.project.v1", "name": "Zero Domain Project"},
            )
            # Project M: 2 NGS/MolBio domains sharing 1 sequence + 1 unique sequence
            await _seed_aggregate(
                s_exp,
                aggregate_id="proj-multi",
                aggregate_kind="workspace",
                workspace_id="proj-multi",
                display_name="Multi Domain Project",
                payload={"schema": "bms.project.v2", "name": "Multi Domain Project", "project_scope": "ngs_molbio_local"},
            )
            await _seed_aggregate(
                s_exp,
                aggregate_id="exp-1",
                aggregate_kind="global_experiment",
                workspace_id="proj-multi",
                parent_id="proj-multi",
                display_name="Exp 1",
                payload={"schema": "bms.global-experiment.v2", "name": "Exp 1"},
            )
            for dom_id in ("dom-1", "dom-2"):
                await _seed_aggregate(
                    s_exp,
                    aggregate_id=dom_id,
                    aggregate_kind="domain_experiment",
                    workspace_id="proj-multi",
                    parent_id="exp-1",
                    display_name=f"Domain {dom_id}",
                    payload={"schema": "bms.domain-experiment.v4", "name": f"Domain {dom_id}", "domain_kind": "ngs_molbio"},
                )
            await s_exp.commit()

            seq_a = NucleotideSequence(
                id="seq-a",
                name="pShared",
                sequence="ATGCGTACGT",
                sequence_type="dna",
                is_circular=True,
                length=10,
                gc_content=50.0,
            )
            seq_b = NucleotideSequence(
                id="seq-b",
                name="pUnique",
                sequence="GGGGCCCC",
                sequence_type="dna",
                is_circular=False,
                length=8,
                gc_content=100.0,
            )
            rev_a_old = MolecularRevision(
                id="mrev-a-1",
                document_id="seq-a",
                revision_number=1,
                change_kind="create",
                content_sha256="a" * 64,
                content_length=10,
                snapshot={"name": "pShared", "sequence": "ATGCGTACGT", "is_circular": True, "sequence_type": "dna"},
            )
            rev_a_head = MolecularRevision(
                id="mrev-a-2",
                document_id="seq-a",
                revision_number=2,
                change_kind="edit",
                content_sha256="b" * 64,
                content_length=10,
                snapshot={"name": "pShared", "sequence": "ATGCGTACGT", "is_circular": True, "sequence_type": "dna"},
            )
            rev_b_head = MolecularRevision(
                id="mrev-b-1",
                document_id="seq-b",
                revision_number=1,
                change_kind="create",
                content_sha256="c" * 64,
                content_length=8,
                snapshot={"name": "pUnique", "sequence": "GGGGCCCC", "is_circular": False, "sequence_type": "dna"},
            )
            doc_a = MolecularDocument(
                id="seq-a",
                document_kind="nucleotide_sequence",
                name="pShared",
                current_revision_id="mrev-a-2",
            )
            doc_b = MolecularDocument(
                id="seq-b",
                document_kind="nucleotide_sequence",
                name="pUnique",
                current_revision_id="mrev-b-1",
            )
            s_mol.add_all([seq_a, seq_b, rev_a_old, rev_a_head, rev_b_head, doc_a, doc_b])
            await s_mol.commit()

            s_ngs.add_all([
                MolBioNGSDomainState(
                    global_domain_experiment_id="dom-1",
                    current_state_revision_id="srev-1",
                    project_id="proj-multi",
                    global_experiment_id="exp-1",
                    global_domain_experiment_revision_id="gdom-1",
                    head_generation=1,
                ),
                MolBioNGSDomainState(
                    global_domain_experiment_id="dom-2",
                    current_state_revision_id="srev-2",
                    project_id="proj-multi",
                    global_experiment_id="exp-1",
                    global_domain_experiment_revision_id="gdom-2",
                    head_generation=1,
                ),
                MolBioNGSDomainStateRevision(
                    id="srev-1",
                    global_domain_experiment_id="dom-1",
                    global_domain_experiment_revision_id="gdom-1",
                    project_id="proj-multi",
                    global_experiment_id="exp-1",
                    revision_number=1,
                    schema_name="bms.molbio-ngs.domain-state.v2",
                    schema_version="2.0",
                    canonical_payload="{}",
                    payload_sha256="1" * 64,
                    membership_graph_sha256="1" * 64,
                ),
                MolBioNGSDomainStateRevision(
                    id="srev-2",
                    global_domain_experiment_id="dom-2",
                    global_domain_experiment_revision_id="gdom-2",
                    project_id="proj-multi",
                    global_experiment_id="exp-1",
                    revision_number=1,
                    schema_name="bms.molbio-ngs.domain-state.v2",
                    schema_version="2.0",
                    canonical_payload="{}",
                    payload_sha256="2" * 64,
                    membership_graph_sha256="2" * 64,
                ),
                MolBioNGSMemberReceipt(
                    receipt_id="rcpt-a-head",
                    source_store_id="molbio",
                    entity_kind="molecular_revision",
                    entity_id="mrev-a-2",
                    source_generation_or_revision="2",
                    content_digest="b" * 64,
                    schema_name="bms.molbio-ngs.member-receipt.v1",
                    schema_version="1.0",
                    availability="available",
                    reopen_destination=json.dumps({"route": "/designer", "params": {"sequence_id": "seq-a", "revision_id": "mrev-a-2"}}),
                    canonical_receipt=json.dumps({"sequence_id": "seq-a", "revision_id": "mrev-a-2"}),
                    receipt_sha256="b" * 64,
                ),
                MolBioNGSMemberReceipt(
                    receipt_id="rcpt-a-old",
                    source_store_id="molbio",
                    entity_kind="molecular_revision",
                    entity_id="mrev-a-1",
                    source_generation_or_revision="1",
                    content_digest="a" * 64,
                    schema_name="bms.molbio-ngs.member-receipt.v1",
                    schema_version="1.0",
                    availability="available",
                    reopen_destination=json.dumps({"route": "/designer", "params": {"sequence_id": "seq-a", "revision_id": "mrev-a-1"}}),
                    canonical_receipt=json.dumps({"sequence_id": "seq-a", "revision_id": "mrev-a-1"}),
                    receipt_sha256="a" * 64,
                ),
                MolBioNGSMemberReceipt(
                    receipt_id="rcpt-b",
                    source_store_id="molbio",
                    entity_kind="molecular_revision",
                    entity_id="mrev-b-1",
                    source_generation_or_revision="1",
                    content_digest="c" * 64,
                    schema_name="bms.molbio-ngs.member-receipt.v1",
                    schema_version="1.0",
                    availability="available",
                    reopen_destination=json.dumps({"route": "/designer", "params": {"sequence_id": "seq-b", "revision_id": "mrev-b-1"}}),
                    canonical_receipt=json.dumps({"sequence_id": "seq-b", "revision_id": "mrev-b-1"}),
                    receipt_sha256="c" * 64,
                ),
                MolBioNGSDomainStateMember(
                    state_revision_id="srev-1",
                    receipt_id="rcpt-a-head",
                    role="molecular_expected_construct",
                    ordinal=0,
                ),
                MolBioNGSDomainStateMember(
                    state_revision_id="srev-2",
                    receipt_id="rcpt-a-old",
                    role="molecular_expected_construct",
                    ordinal=0,
                ),
                MolBioNGSDomainStateMember(
                    state_revision_id="srev-2",
                    receipt_id="rcpt-b",
                    role="molecular_expected_construct",
                    ordinal=1,
                ),
            ])
            await s_ngs.commit()

            # 1. Zero-domain project returns [] and normalizes NULL scope -> global
            zero_seqs = await get_project_sequences_payload(s_exp, s_ngs, s_mol, project_id="proj-zero")
            assert zero_seqs is not None and zero_seqs["items"] == []
            async with SCore() as s_core:
                zero_hub = await get_project_hub_payload(None, s_exp, s_core, s_ngs, s_mol, project_id="proj-zero")
                assert zero_hub is not None
                assert zero_hub["project"]["project_scope"] == "global"
                assert zero_hub["counts"]["molecular_sequences"] == 0

            # 2. Multi-domain project returns deduplicated union without error
            multi_seqs = await get_project_sequences_payload(s_exp, s_ngs, s_mol, project_id="proj-multi")
            assert multi_seqs is not None
            by_id = {item["id"]: item for item in multi_seqs["items"]}
            assert set(by_id.keys()) == {"seq-a", "seq-b"}
            assert by_id["seq-a"]["is_head_editable"] is True
            assert by_id["seq-b"]["is_head_editable"] is True

            async with SCore() as core:
                args = (None, s_exp, core, s_ngs, s_mol)
                first = await get_project_hub_payload(*args, project_id="proj-multi", limit=1)
                assert first.get("next_cursor")
                second = await get_project_hub_payload(*args, project_id="proj-multi", limit=1, cursor=first["next_cursor"])
                assert second["next_cursor"] is None
                assert {p["sections"]["molecular_sequences"][0]["id"] for p in (first, second)} == {"seq-a", "seq-b"}

            # 3. Pinned domain/state_revision_id preserves historical non-head editability
            pinned = await get_project_sequences_payload(
                s_exp, s_ngs, s_mol,
                project_id="proj-multi",
                domain_experiment_id="dom-2",
                state_revision_id="srev-2",
            )
            assert pinned is not None
            pinned_by_id = {item["id"]: item for item in pinned["items"]}
            assert pinned_by_id["seq-a"]["revision_id"] == "mrev-a-1"
            assert pinned_by_id["seq-a"]["is_head_editable"] is False

            # 4. Summary counts match hub counts
            counts = await get_projects_summary_counts(s_exp, s_ngs, s_mol, ["proj-zero", "proj-multi"])
            assert counts["proj-zero"]["molecular_sequences"] == 0
            assert counts["proj-multi"]["molecular_sequences"] == 2
    finally:
        for eng in engines:
            await eng.dispose()




@pytest.mark.asyncio
async def test_project_record_scope_includes_children_without_crossing_projects(tmp_path):
    from experiment_models import ExperimentResearchRecord
    from experiment_services import ValidationFailure
    from routers.projects import _records
    engines, store, *_ = await _make_sessions(tmp_path)
    try:
        async with store() as session:
            for name, owner, subject in [("p-note", "p", "p"), ("e-note", "p", "e"), ("d-note", "p", "d"), ("other", "q", "q")]:
                session.add(ExperimentResearchRecord(resource_id=name, workspace_id=owner, subject_resource_id=subject,
                                                     body=name, record_kind="note", created_at="2026-01-01"))
            await session.flush()
            assert [r["id"] for r in (await _records(session, "p", None, None, 50))["items"]] == ["p-note"]
            first = await _records(session, "p", None, None, 2, include_descendants=True)
            last = await _records(session, "p", None, first["next_cursor"], 2, include_descendants=True)
            assert last["next_cursor"] is None
            assert {(r["id"], r["subject_resource_id"]) for r in first["items"] + last["items"]} == {
                ("p-note", "p"), ("e-note", "e"), ("d-note", "d")}
            with pytest.raises(ValidationFailure):
                await _records(session, "p", None, "other", 2, include_descendants=True)
    finally:
        for eng in engines:
            await eng.dispose()


def test_project_capabilities_follow_install_switches_even_with_mounted_routes(monkeypatch):
    from fastapi import FastAPI, Request
    from services.project_hub import resolve_hub_capabilities
    monkeypatch.setenv("BMS_FEATURE_PROTEIN_COMPUTE", "0")
    monkeypatch.setenv("BMS_FEATURE_NGS_ONT", "0")
    app = FastAPI()
    app.add_api_route("/api/jobs", lambda: None)
    app.add_api_route("/api/ngs", lambda: None)
    app.state.mounted_features = {"ngs_ont": True, "protein_compute": True}
    for request in (None, Request({"type": "http", "app": app})):
        assert resolve_hub_capabilities(request) == {"has_in_silico": False, "has_molbio": True, "has_ngs_ont": False, "has_stats": False}

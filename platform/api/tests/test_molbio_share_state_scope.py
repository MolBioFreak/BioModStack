"""Exact Domain-state export against isolated, real native SQLAlchemy stores."""
from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_models import (
    ExperimentAggregateHead, ExperimentBase, ExperimentExternalEntityReceipt,
    ExperimentLineageEdge, ExperimentResource, ExperimentRevision,
)
from molbio_models import (
    MolBioBase, MolecularDocument, MolecularOperation, MolecularOperationInput,
    MolecularOperationOutput, MolecularRevision, ProjectPlasmidMetadata,
)
from molbio_ngs_models import (
    MolBioNGSBase, MolBioNGSDomainState, MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision, MolBioNGSGlobalBinding, MolBioNGSMemberReceipt,
)
from services.molbio_share_export import build_share_bundle, encode_bundle


@pytest_asyncio.fixture
async def stores(tmp_path):
    engines = []
    async with AsyncExitStack() as stack:
        sessions = []
        try:
            for name, base in (("molbio", MolBioBase), ("experiment", ExperimentBase), ("native", MolBioNGSBase)):
                engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / (name + '.sqlite')}")
                engines.append(engine)
                async with engine.begin() as connection:
                    await connection.run_sync(base.metadata.create_all)
                sessions.append(await stack.enter_async_context(async_sessionmaker(engine, expire_on_commit=False)()))
            molbio, experiment, native = sessions
            for doc, head in (("doc", "r2"), ("input", "a"), ("root", "g"), ("parent", "p"), ("extra", "x")):
                molbio.add(MolecularDocument(id=doc, document_kind="sequence", name=doc, current_revision_id=head))
            for op in ("ancient", "derived", "member", "later"):
                molbio.add(MolecularOperation(id=op, operation_kind="assembly", implementation="fixture"))
            for rid, doc, num, op, day, parent in (
                ("g", "root", 1, None, 1, None),
                ("p", "parent", 1, None, 1, None),
                ("a", "input", 1, "ancient", 2, None),
                ("r1", "doc", 1, "derived", 3, "parent"),
                ("r2", "doc", 2, "later", 4, None),
                ("x", "extra", 1, "member", 3, None),
            ):
                molbio.add(MolecularRevision(
                    id=rid, document_id=doc, revision_number=num, operation_id=op,
                    change_kind="create" if num == 1 else "edit", content_sha256="a" * 64,
                    content_length=4, created_at=datetime(2026, 1, day),
                    snapshot={"name": rid, "sequence": "ACGT", "sequence_type": "dna",
                              "parent_id": parent, "source_file": r"C:\private\science\sample.gb"},
                ))
            for op, source, result in (("ancient", "g", "a"), ("derived", "a", "r1"),
                                       ("member", "a", "x"), ("later", "r1", "r2")):
                molbio.add(MolecularOperationInput(id=op + "-in", operation_id=op, revision_id=source, role="template", position=0))
                molbio.add(MolecularOperationOutput(id=op + "-out", operation_id=op, revision_id=result, role="product", position=0))
            molbio.add(ProjectPlasmidMetadata(
                id="meta", project_id="project", domain_experiment_id="domain", sequence_id="doc",
                molecular_revision_id="r1", active_state_revision_id="old", tags=["old tag"],
                notes="old notes", idempotency_key="meta", request_fingerprint="a" * 64,
            ))
            await molbio.commit()

            for rid, kind in (("project", "workspace"), ("global", "experiment"),
                              ("domain", "domain_experiment"), ("domain-rev", "revision"),
                              ("later-receipt", "external_entity_receipt")):
                experiment.add(ExperimentResource(id=rid, kind=kind, workspace_id="project"))
            experiment.add_all([
                ExperimentAggregateHead(aggregate_id="project", aggregate_kind="workspace", workspace_id="project", display_name="Project"),
                ExperimentAggregateHead(aggregate_id="domain", aggregate_kind="domain_experiment", workspace_id="project",
                                        parent_id="global", current_revision_id="domain-rev", display_name="Domain"),
                ExperimentRevision(resource_id="domain-rev", subject_id="domain", revision_number=1,
                                   schema_name="domain", schema_version="1", canonical_payload='{"domain_kind":"ngs_molbio"}',
                                   payload_sha256="a" * 64, dependency_graph_sha256="a" * 64),
                ExperimentExternalEntityReceipt(id="later-receipt", workspace_id="project", resource_id="domain", store_id="molbio",
                                                entity_kind="molecular_operation", entity_id="later", generation_or_revision="event",
                                                content_digest="a" * 64),
                ExperimentLineageEdge(id="later-edge", workspace_id="project", source_resource_id="domain",
                                      target_resource_id="later-receipt", edge_mode="reference", edge_key="later"),
            ])
            await experiment.commit()

            native.add(MolBioNGSDomainState(global_domain_experiment_id="domain", current_state_revision_id="new", current_binding_revision_id="binding"))
            native.add(MolBioNGSGlobalBinding(
                binding_revision_id="binding", global_domain_experiment_id="domain", revision_number=1,
                global_domain_experiment_revision_id="domain-rev", global_domain_experiment_revision_digest="a" * 64,
                project_id="project", project_generation="1", project_digest="a" * 64,
                project_receipt_id="project-receipt", project_reopen_destination="/project",
                global_experiment_id="global", global_experiment_generation="1", global_experiment_digest="a" * 64,
                global_experiment_receipt_id="global-receipt", global_experiment_reopen_destination="/global",
            ))
            for sid, number in (("old", 1), ("new", 2)):
                native.add(MolBioNGSDomainStateRevision(
                    id=sid, global_domain_experiment_id="domain", global_domain_experiment_revision_id="domain-rev",
                    binding_revision_id="binding", revision_number=number, parent_revision_id="old" if number == 2 else None,
                    schema_name="ngs_molbio.domain_state.v1", schema_version="1", canonical_payload="{}",
                    payload_sha256=str(number) * 64, membership_graph_sha256=str(number) * 64,
                ))
            for sid, kind, entity, role, ordinal in (
                ("old", "molecular_revision", "r1", "molecular_expected_construct", 0),
                ("old", "molecular_operation", "member", "molecular_operation", 1),
                ("new", "molecular_revision", "r2", "molecular_expected_construct", 0),
                ("new", "molecular_operation", "later", "molecular_operation", 1),
            ):
                receipt_id = sid + "-" + entity
                native.add(MolBioNGSMemberReceipt(
                    receipt_id=receipt_id, source_store_id="molbio", entity_kind=kind, entity_id=entity,
                    source_generation_or_revision="event" if kind == "molecular_operation" else "1",
                    content_digest="a" * 64, schema_name="bms.molbio.member-receipt.v1", schema_version="1",
                    availability="available", reopen_destination="/molbio", canonical_receipt="{}", receipt_sha256="a" * 64,
                ))
                native.add(MolBioNGSDomainStateMember(state_revision_id=sid, receipt_id=receipt_id, role=role, ordinal=ordinal))
            # Two roles for the same operation must not inflate operation_count.
            native.add(MolBioNGSDomainStateMember(state_revision_id="old", receipt_id="old-member", role="evidence", ordinal=2))
            await native.commit()
            yield {"molbio": molbio, "experiment": experiment, "native": native}
        finally:
            await stack.aclose()
            for engine in engines:
                await engine.dispose()


def domain_scope(**overrides):
    return {"kind": "project_domain", "project_id": "project", "global_experiment_id": "global",
            "domain_experiment_id": "domain", **overrides}


def revision_roles(bundle):
    return {row["revision_id"]: row["role"] for row in bundle["sequences"]}


def operation_roles(bundle):
    return {row["operation_id"]: row["role"] for row in bundle["operations"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_historical_state_exports_attached_revision_not_live_head(stores, deleted):
    if deleted:
        document = await stores["molbio"].get(MolecularDocument, "doc")
        document.deleted_at = datetime(2026, 1, 5)
        await stores["molbio"].commit()
    bundle, summary = await build_share_bundle(domain_scope(state_revision_id="old"), **stores)
    assert revision_roles(bundle) == {"r1": "scoped", "a": "ancestor", "g": "ancestor", "p": "ancestor"}
    assert operation_roles(bundle) == {"member": "scoped", "derived": "ancestor", "ancient": "ancestor"}
    # Later Project receipts, the newer head and unattached member-operation
    # outputs are all outside this state. Ancestors remain complete records.
    assert "r2" not in revision_roles(bundle) and "x" not in revision_roles(bundle)
    assert all(row["record"]["sequence"] == "ACGT" for row in bundle["sequences"])
    scoped = next(row for row in bundle["sequences"] if row["revision_id"] == "r1")
    assert scoped["project_tags"] == ["old tag"] and scoped["project_notes"] == "old notes"
    detail = summary["domains"][0]
    assert detail["state_revision_id"] == "old"
    assert detail["sequence_count"] == 1 and detail["operation_count"] == 1
    assert detail["sequence_selection"] == "exact_state_member_revisions"
    assert detail["operation_selection"] == "exact_state_member_operations"
    assert "Full ancestor sequence records and operations" in summary["lineage_disclosure"]
    assert summary["counts"] == {"scoped_sequences": 1, "ancestor_sequences": 3,
                                  "scoped_operations": 1, "ancestor_operations": 2}
    assert b"private" not in encode_bundle(bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [domain_scope(), {"kind": "project", "project_id": "project"}])
async def test_current_state_and_project_use_current_native_members_not_document_expansion(stores, scope):
    bundle, summary = await build_share_bundle(scope, **stores)
    assert revision_roles(bundle) == {"r2": "scoped", "r1": "ancestor", "a": "ancestor", "g": "ancestor", "p": "ancestor"}
    assert operation_roles(bundle) == {"later": "scoped", "derived": "ancestor", "ancient": "ancestor"}
    assert summary["domains"][0]["state_revision_id"] == "new"
    assert all(not row["project_tags"] and not row["project_notes"] for row in bundle["sequences"])


@pytest.mark.asyncio
async def test_state_without_operation_members_keeps_only_derivation_operations(stores):
    from sqlalchemy import delete

    await stores["native"].execute(delete(MolBioNGSDomainStateMember).where(MolBioNGSDomainStateMember.receipt_id == "old-member"))
    await stores["native"].commit()
    bundle, summary = await build_share_bundle(domain_scope(state_revision_id="old"), **stores)
    assert operation_roles(bundle) == {"derived": "ancestor", "ancient": "ancestor"}
    assert summary["domains"][0]["operation_count"] == 0
    assert revision_roles(bundle) == {"r1": "scoped", "a": "ancestor", "g": "ancestor", "p": "ancestor"}


@pytest.mark.asyncio
async def test_items_still_select_document_heads_and_operation_outputs(stores):
    bundle, _ = await build_share_bundle({"kind": "items", "sequence_ids": ["doc"], "operation_ids": ["member"]}, **stores)
    assert revision_roles(bundle) == {"r2": "scoped", "x": "scoped", "r1": "ancestor", "a": "ancestor", "g": "ancestor", "p": "ancestor"}
    assert operation_roles(bundle) == {"member": "scoped", "later": "ancestor", "derived": "ancestor", "ancient": "ancestor"}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [r"C:\private\science\sample.gb", "/private/science/sample.gb", r"\\server\private\sample.gb", r"C:\private/science\sample.gb", "sample.gb", None])
async def test_source_file_basename_is_platform_independent(stores, source):
    revision = await stores["molbio"].get(MolecularRevision, "r2")
    revision.snapshot = {**revision.snapshot, "source_file": source}
    await stores["molbio"].commit()
    bundle, _ = await build_share_bundle({"kind": "items", "sequence_ids": ["doc"]}, **stores)
    record = next(row["record"] for row in bundle["sequences"] if row["revision_id"] == "r2")
    assert record["source_file"] == ("sample.gb" if source else None)

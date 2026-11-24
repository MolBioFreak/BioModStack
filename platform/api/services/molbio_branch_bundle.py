"""Portable branch bundle: export a derivation branch, import it into another store.

Revisions and operations are immutable, so export is a pure read and import mints new
ids (old ids are kept under ``imported_from``). Digest result rows are never recreated.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import (
    MolecularDocument,
    MolecularImportBatch,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    NucleotideSequence,
)
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.molbio_persistence import (
    IdempotencyConflictError,
    begin_immediate_molbio_write,
    canonical_request_fingerprint,
    create_operation,
)

BUNDLE_FORMAT = "molbio-branch"
BUNDLE_VERSION = 1
_PROJECTION_SKIP = {"id", "created_at", "updated_at", "parent_id"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _edge(row: MolecularOperationInput | MolecularOperationOutput) -> dict[str, Any]:
    return {"operation_id": row.operation_id, "revision_id": row.revision_id, "role": row.role,
            "ordinal": row.position, "snapshot": row.snapshot}


async def export_branch(session: AsyncSession, revision_ids: list[str]) -> dict[str, Any]:
    revisions: dict[str, MolecularRevision] = {}
    operations: dict[str, MolecularOperation] = {}
    edges_in: list[MolecularOperationInput] = []
    edges_out: list[MolecularOperationOutput] = []

    async def load(ids: set[str]) -> list[MolecularRevision]:
        rows = (await session.scalars(select(MolecularRevision).where(MolecularRevision.id.in_(ids)))).all()
        fresh = [row for row in rows if row.id not in revisions]
        revisions.update({row.id: row for row in fresh})
        return fresh

    missing = set(revision_ids) - {row.id for row in await load(set(revision_ids))}
    if missing:
        raise LookupError(f"revision not found: {', '.join(sorted(missing))}")
    frontier = list(revisions.values())
    while frontier:
        ids = {row.id for row in frontier}
        produced = (await session.scalars(
            select(MolecularOperationOutput).where(MolecularOperationOutput.revision_id.in_(ids)))).all()
        op_ids = {row.operation_id for row in produced if row.operation_id not in operations}
        if not op_ids:
            break
        for operation in (await session.scalars(
                select(MolecularOperation).where(MolecularOperation.id.in_(op_ids)))).all():
            operations[operation.id] = operation
        edges_in += (await session.scalars(
            select(MolecularOperationInput).where(MolecularOperationInput.operation_id.in_(op_ids)))).all()
        edges_out += (await session.scalars(
            select(MolecularOperationOutput).where(MolecularOperationOutput.operation_id.in_(op_ids)))).all()
        frontier = await load({row.revision_id for row in edges_in + edges_out if row.operation_id in op_ids})

    documents = {row.id: row for row in (await session.scalars(select(MolecularDocument).where(
        MolecularDocument.id.in_({row.document_id for row in revisions.values()})))).all()}
    shelf = {row.id: row for row in (await session.scalars(select(NucleotideSequence).where(
        NucleotideSequence.id.in_(set(documents))))).all()}

    def projection(document_id: str) -> dict[str, Any] | None:
        row = shelf.get(document_id)
        if row is None:
            return None
        return {column.name: getattr(row, column.name) for column in NucleotideSequence.__table__.columns
                if column.name not in _PROJECTION_SKIP}

    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "selected": list(revision_ids),
        "documents": [
            {"id": doc.id, "document_kind": doc.document_kind, "name": doc.name,
             "current_revision_id": doc.current_revision_id, "projection": projection(doc.id)}
            for doc in sorted(documents.values(), key=lambda d: d.id)
        ],
        "revisions": [
            {"id": rev.id, "document_id": rev.document_id, "revision_number": rev.revision_number,
             "change_kind": rev.change_kind, "content_sha256": rev.content_sha256,
             "content_length": rev.content_length, "snapshot": rev.snapshot, "provenance": rev.provenance,
             "operation_id": rev.operation_id, "created_at": _iso(rev.created_at)}
            for rev in sorted(revisions.values(), key=lambda r: r.id)
        ],
        "operations": [
            {"id": op.id, "kind": op.operation_kind, "implementation": op.implementation,
             "implementation_version": op.implementation_version, "status": op.status,
             "parameters": op.parameters, "warnings": op.warnings, "provenance": op.provenance,
             "created_at": _iso(op.created_at)}
            for op in sorted(operations.values(), key=lambda o: o.id)
        ],
        "inputs": sorted((_edge(row) for row in edges_in), key=lambda e: (e["operation_id"], e["ordinal"])),
        "outputs": sorted((_edge(row) for row in edges_out), key=lambda e: (e["operation_id"], e["ordinal"])),
    }


def _check_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("format") != BUNDLE_FORMAT or bundle.get("version") != BUNDLE_VERSION:
        raise ValueError("not a molbio-branch version 1 bundle")
    revision_ids = {row["id"] for row in bundle["revisions"]}
    document_ids = {row["id"] for row in bundle["documents"]}
    operation_ids = {row["id"] for row in bundle["operations"]}
    for edge in bundle["inputs"] + bundle["outputs"]:
        if edge["revision_id"] not in revision_ids or edge["operation_id"] not in operation_ids:
            raise ValueError(f"bundle edge references a revision or operation that is not in the bundle: "
                             f"{edge['revision_id']}")
    if any(rev["document_id"] not in document_ids for rev in bundle["revisions"]):
        raise ValueError("bundle revision references a document that is not in the bundle")
    if any(sel not in revision_ids for sel in bundle["selected"]):
        raise ValueError("bundle selects a revision that is not in the bundle")


def _remap(value: Any, mapping: dict[str, str]) -> Any:
    """Replace stored ids inside a snapshot-shaped dict (shallow keys that hold ids)."""
    return {key: mapping.get(item, item) if isinstance(item, str) else item for key, item in value.items()}


async def import_branch(
    session: AsyncSession, bundle: dict[str, Any], *, idempotency_key: str | None = None,
) -> dict[str, Any]:
    fingerprint = canonical_request_fingerprint(bundle)
    key = idempotency_key or f"branch-import:{fingerprint}"

    async def earlier() -> MolecularImportBatch | None:
        by_key = (await session.execute(
            select(MolecularImportBatch).where(MolecularImportBatch.idempotency_key == key))).scalar_one_or_none()
        if by_key is not None:
            if by_key.request_fingerprint != fingerprint:
                raise IdempotencyConflictError("idempotency key is already bound to a different bundle")
            return by_key
        return (await session.execute(select(MolecularImportBatch).where(
            MolecularImportBatch.source_format == "branch",
            MolecularImportBatch.request_fingerprint == fingerprint,
        ).order_by(MolecularImportBatch.created_at, MolecularImportBatch.id))).scalars().first()

    found = await earlier()
    if found is not None:
        await session.commit()
        return {**found.result, "already_imported": True}

    _check_bundle(bundle)
    await begin_immediate_molbio_write(session)
    found = await earlier()
    if found is not None:
        await session.commit()
        return {**found.result, "already_imported": True}

    doc_map = {row["id"]: str(uuid.uuid4()) for row in bundle["documents"]}
    rev_map = {row["id"]: str(uuid.uuid4()) for row in bundle["revisions"]}
    op_map = {row["id"]: str(uuid.uuid4()) for row in bundle["operations"]}
    ids = {**doc_map, **rev_map, **op_map}

    def when(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    for row in bundle["documents"]:
        session.add(MolecularDocument(id=doc_map[row["id"]], document_kind=row["document_kind"], name=row["name"]))
    for row in bundle["operations"]:
        session.add(MolecularOperation(
            id=op_map[row["id"]], operation_kind=row["kind"], implementation=row["implementation"],
            implementation_version=row["implementation_version"], status=row["status"],
            parameters=row["parameters"], warnings=row["warnings"],
            provenance={**row["provenance"], "imported_from": {"operation_id": row["id"]}},
            created_at=when(row["created_at"]) or datetime.utcnow(), created_by=SERVER_OWNED_ACTOR,
        ))
    await session.flush()

    def snapshot_for(row: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(row["snapshot"])
        snapshot["id"] = doc_map[row["document_id"]]
        snapshot["parent_id"] = None
        params = snapshot.get("operation_params")
        if isinstance(params, dict) and params.get("operation_id") in op_map:
            snapshot["operation_params"] = {**params, "operation_id": op_map[params["operation_id"]]}
        return snapshot

    for row in bundle["revisions"]:
        session.add(MolecularRevision(
            id=rev_map[row["id"]], document_id=doc_map[row["document_id"]], revision_number=row["revision_number"],
            change_kind=row["change_kind"], content_sha256=row["content_sha256"],
            content_length=row["content_length"], snapshot=snapshot_for(row),
            provenance={**row["provenance"], "imported_from": {
                "revision_id": row["id"], "document_id": row["document_id"], "operation_id": row["operation_id"]}},
            operation_id=op_map.get(row["operation_id"]),
            created_at=when(row["created_at"]) or datetime.utcnow(), created_by=SERVER_OWNED_ACTOR,
        ))
    await session.flush()

    newest: dict[str, dict[str, Any]] = {}
    for row in bundle["revisions"]:
        if row["document_id"] not in newest or row["revision_number"] > newest[row["document_id"]]["revision_number"]:
            newest[row["document_id"]] = row
    for row in bundle["documents"]:
        document = await session.get(MolecularDocument, doc_map[row["id"]])
        current = rev_map.get(row["current_revision_id"]) or rev_map[newest[row["id"]]["id"]]
        document.current_revision_id = current
        if row["projection"] is not None:
            session.add(NucleotideSequence(**{
                **row["projection"], "id": doc_map[row["id"]], "parent_id": None,
                "operation_params": snapshot_for(newest[row["id"]]).get("operation_params"),
            }))

    for kind, model in (("inputs", MolecularOperationInput), ("outputs", MolecularOperationOutput)):
        for edge in bundle[kind]:
            session.add(model(
                id=str(uuid.uuid4()), operation_id=op_map[edge["operation_id"]],
                revision_id=rev_map[edge["revision_id"]], role=edge["role"], position=edge["ordinal"],
                snapshot=_remap(edge["snapshot"], ids),
            ))

    operation = await create_operation(
        session, operation_kind="branch_import", implementation="molbio_branch_bundle",
        parameters={"source_sha256": fingerprint, "selected": bundle["selected"]},
        provenance={"authority": "branch_bundle"}, idempotency_key=key, request_fingerprint=fingerprint,
    )
    result = {
        "batch_id": str(uuid.uuid4()),
        "id_map": {"documents": doc_map, "revisions": rev_map, "operations": op_map},
        "created": {"documents": len(doc_map), "revisions": len(rev_map), "operations": len(op_map)},
        "selected": [rev_map[item] for item in bundle["selected"]],
    }
    session.add(MolecularImportBatch(
        id=result["batch_id"], operation_id=operation.id, idempotency_key=key, request_fingerprint=fingerprint,
        source_format="branch", source_sha256=fingerprint, result=result, created_by=SERVER_OWNED_ACTOR,
    ))
    await session.flush()
    await session.commit()
    return {**result, "already_imported": False}

"""Read-only revision relatives and fragment chooser over the existing operation edge tables.

Names, topology and length come from immutable revision snapshots, so deleted documents
still display. Lists are paged by ``(created_at, id)`` with an opaque cursor.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    NucleotideSequence,
)
from services.assembly.physical_ends import end_to_record, read_physical_ends

RELATIVES_DEFAULT_LIMIT = 25
RELATIVES_MAX_LIMIT = 100
CHOOSER_DEFAULT_LIMIT = 50
CHOOSER_MAX_LIMIT = 200


def encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), row_id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return datetime.fromisoformat(created), str(row_id)
    except Exception as exc:  # malformed opaque value from the client
        raise ValueError("invalid cursor") from exc


_NAME = func.json_extract(MolecularRevision.snapshot, "$.name")
_TOPOLOGY = func.json_extract(MolecularRevision.snapshot, "$.topology")
_CIRCULAR = func.json_extract(MolecularRevision.snapshot, "$.is_circular")


def _topology(topology: Any, circular: Any) -> str:
    if topology in ("linear", "circular"):
        return topology
    return "circular" if circular in (1, True) else "linear"


def _revision_columns():
    return (
        MolecularRevision.id,
        MolecularRevision.document_id,
        MolecularRevision.revision_number,
        MolecularRevision.content_length,
        func.coalesce(_NAME, MolecularDocument.name).label("name"),
        _TOPOLOGY.label("topology"),
        _CIRCULAR.label("circular"),
    )


async def _revision_info(session: AsyncSession, revision_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not revision_ids:
        return {}
    rows = (await session.execute(
        select(*_revision_columns())
        .join(MolecularDocument, MolecularDocument.id == MolecularRevision.document_id)
        .where(MolecularRevision.id.in_(revision_ids))
    )).all()
    return {
        row.id: {
            "revision_id": row.id,
            "document_id": row.document_id,
            "document_name": row.name,
            "revision_number": row.revision_number,
            "topology": _topology(row.topology, row.circular),
            "length": row.content_length,
        }
        for row in rows
    }


async def _operations_with_edges(session: AsyncSession, operations: list[MolecularOperation]) -> list[dict[str, Any]]:
    ids = [operation.id for operation in operations]
    if not ids:
        return []
    inputs = (await session.scalars(
        select(MolecularOperationInput).where(MolecularOperationInput.operation_id.in_(ids))
        .order_by(MolecularOperationInput.position, MolecularOperationInput.id)
    )).all()
    outputs = (await session.scalars(
        select(MolecularOperationOutput).where(MolecularOperationOutput.operation_id.in_(ids))
        .order_by(MolecularOperationOutput.position, MolecularOperationOutput.id)
    )).all()
    info = await _revision_info(session, {edge.revision_id for edge in (*inputs, *outputs)})

    def edge(row: MolecularOperationInput | MolecularOperationOutput) -> dict[str, Any]:
        return {**info.get(row.revision_id, {"revision_id": row.revision_id}), "role": row.role, "position": row.position}

    return [
        {
            "operation_id": operation.id,
            "operation_kind": operation.operation_kind,
            "created_at": operation.created_at.isoformat() if operation.created_at else None,
            "inputs": [edge(row) for row in inputs if row.operation_id == operation.id],
            "outputs": [edge(row) for row in outputs if row.operation_id == operation.id],
        }
        for operation in operations
    ]


async def revision_relatives(
    session: AsyncSession, revision_id: str, *, cursor: str | None = None, limit: int = RELATIVES_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Operations that produced this revision (sources) and that consumed it (derived, paged)."""
    info = (await _revision_info(session, {revision_id})).get(revision_id)
    if info is None:
        raise LookupError("molecular revision not found")
    source_ops = (await session.scalars(
        select(MolecularOperation)
        .where(MolecularOperation.id.in_(
            select(MolecularOperationOutput.operation_id).where(MolecularOperationOutput.revision_id == revision_id)))
        .order_by(MolecularOperation.created_at, MolecularOperation.id)
    )).all()
    query = (
        select(MolecularOperation)
        .where(MolecularOperation.id.in_(
            select(MolecularOperationInput.operation_id).where(MolecularOperationInput.revision_id == revision_id)))
        .order_by(MolecularOperation.created_at, MolecularOperation.id)
        .limit(limit + 1)
    )
    if cursor:
        created_at, row_id = decode_cursor(cursor)
        query = query.where(or_(
            MolecularOperation.created_at > created_at,
            and_(MolecularOperation.created_at == created_at, MolecularOperation.id > row_id),
        ))
    derived_ops = list((await session.scalars(query)).all())
    next_cursor = None
    if len(derived_ops) > limit:
        derived_ops = derived_ops[:limit]
        last = derived_ops[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return {
        "revision": {
            "revision_id": info["revision_id"],
            "document_id": info["document_id"],
            "document_name": info["document_name"],
            "revision_number": info["revision_number"],
        },
        "sources": await _operations_with_edges(session, list(source_ops)),
        "derived": {"items": await _operations_with_edges(session, derived_ops), "next_cursor": next_cursor},
    }


async def chooser_items(
    session: AsyncSession, *, q: str = "", kinds: list[str] | None = None,
    cursor: str | None = None, limit: int = CHOOSER_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Shelf molecules (current revision) plus retained operation outputs (every revision), newest first."""
    first_output = (
        select(MolecularOperationOutput.id)
        .where(MolecularOperationOutput.revision_id == MolecularRevision.id)
        .order_by(MolecularOperationOutput.id).limit(1).correlate(MolecularRevision).scalar_subquery()
    )
    query = (
        select(
            *_revision_columns(),
            MolecularRevision.created_at,
            MolecularRevision.provenance,
            NucleotideSequence.id.label("sequence_id"),
            (MolecularDocument.current_revision_id == MolecularRevision.id).label("is_current"),
            MolecularOperationOutput.operation_id.label("producer_id"),
            MolecularOperationOutput.role.label("producer_role"),
            MolecularOperation.operation_kind.label("producer_kind"),
        )
        .join(MolecularDocument, MolecularDocument.id == MolecularRevision.document_id)
        .outerjoin(NucleotideSequence, NucleotideSequence.id == MolecularDocument.id)
        .outerjoin(MolecularOperationOutput, MolecularOperationOutput.id == first_output)
        .outerjoin(MolecularOperation, MolecularOperation.id == MolecularOperationOutput.operation_id)
        .where(or_(
            and_(NucleotideSequence.id.is_not(None), MolecularDocument.current_revision_id == MolecularRevision.id),
            and_(NucleotideSequence.id.is_(None), MolecularOperationOutput.id.is_not(None)),
        ))
        .order_by(MolecularRevision.created_at.desc(), MolecularRevision.id.desc())
        .limit(limit + 1)
    )
    if q.strip():
        needle = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(func.lower(func.coalesce(_NAME, MolecularDocument.name)).like(needle, escape="\\"))
    if kinds:
        query = query.where(MolecularOperation.operation_kind.in_(kinds))
    if cursor:
        created_at, row_id = decode_cursor(cursor)
        query = query.where(or_(
            MolecularRevision.created_at < created_at,
            and_(MolecularRevision.created_at == created_at, MolecularRevision.id < row_id),
        ))
    rows = (await session.execute(query)).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    items = []
    for row in rows:
        left, right = read_physical_ends(row.provenance if isinstance(row.provenance, dict) else None)
        items.append({
            "document_id": row.document_id,
            "revision_id": row.id,
            "revision_number": row.revision_number,
            "sequence_id": row.sequence_id,
            "name": row.name,
            "topology": _topology(row.topology, row.circular),
            "length": row.content_length,
            "is_current": bool(row.is_current),
            "origin": "shelf" if row.sequence_id else "retained",
            "produced_by": (
                {"operation_id": row.producer_id, "operation_kind": row.producer_kind, "role": row.producer_role}
                if row.producer_id else None
            ),
            "left_end": end_to_record(left),
            "right_end": end_to_record(right),
        })
    return {"items": items, "next_cursor": next_cursor}

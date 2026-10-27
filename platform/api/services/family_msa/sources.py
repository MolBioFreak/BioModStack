"""Read pinned Project Dataset resources without assuming they are Designs.

This is a resource inventory, not a sequence parser or execution authority.
Callers must resolve each resource with its producer-owned reader before using
it as an alignment input. No paths or payloads are inferred from labels.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentDatasetRevisionMember,
    ExperimentExternalEntityReceipt,
    ExperimentRevision,
)
from experiment_services import NotFound, ValidationFailure


async def _dataset(session: AsyncSession, project_id: str, dataset_id: str, revision_id: str):
    head = await session.get(ExperimentAggregateHead, dataset_id)
    revision = await session.get(ExperimentRevision, revision_id)
    if (head is None or head.aggregate_kind != "dataset" or head.workspace_id != project_id
            or revision is None or revision.subject_id != dataset_id):
        raise NotFound("Exact Dataset revision not found in Project")
    return revision


def _render(row: ExperimentDatasetRevisionMember, receipt: ExperimentExternalEntityReceipt | None) -> dict[str, Any]:
    value = json.loads(row.value_json)
    return {
        "ordinal": row.ordinal,
        "role": row.role,
        "member_identity": row.semantic_identity,
        "member_sha256": row.content_sha256,
        "media_type": row.media_type,
        "value": value,
        "receipt": None if receipt is None else {
            "receipt_id": receipt.id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "adapter_id": receipt.verification_authority,
            "revision": receipt.generation_or_revision,
            "content_digest": receipt.content_digest,
            "availability": receipt.availability,
        },
    }


def _statement(project_id: str, revision_id: str):
    member = ExperimentDatasetRevisionMember
    receipt = ExperimentExternalEntityReceipt
    return (select(member, receipt)
            .outerjoin(receipt, (receipt.id == member.semantic_identity) & (receipt.workspace_id == project_id))
            .where(member.revision_id == revision_id)
            .order_by(member.ordinal))


async def browse_dataset_resources(session: AsyncSession, *, project_id: str, dataset_id: str,
                                   revision_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Page all pinned members, including unknown and non-Design receipts."""
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValidationFailure("invalid page")
    revision = await _dataset(session, project_id, dataset_id, revision_id)
    statement = _statement(project_id, revision_id)
    total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = (await session.execute(statement.offset(offset).limit(limit))).all()
    return {"schema": "bms.family-msa.dataset-resources.v1", "project_id": project_id,
            "dataset_id": dataset_id, "revision_id": revision_id,
            "revision_sha256": revision.payload_sha256, "items": [_render(*row) for row in rows],
            "offset": offset, "limit": limit, "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None}


async def freeze_dataset_resources(session: AsyncSession, *, project_id: str, dataset_id: str,
                                   revision_id: str, ordinals: list[int] | None = None) -> dict[str, Any]:
    """Freeze resource occurrences in revision order; None means all members.

    This returns a value to be persisted by the calling owner, not a database
    write. It deliberately does not assert producer bytes remain available.
    """
    revision = await _dataset(session, project_id, dataset_id, revision_id)
    statement = _statement(project_id, revision_id)
    if ordinals is not None:
        if len(ordinals) != len(set(ordinals)) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in ordinals):
            raise ValidationFailure("invalid member ordinals")
        statement = statement.where(ExperimentDatasetRevisionMember.ordinal.in_(ordinals))
    rows = (await session.execute(statement)).all()
    if ordinals is not None and len(rows) != len(ordinals):
        raise NotFound("Dataset member ordinal not found in exact revision")
    return {"schema": "bms.family-msa.dataset-resource-selection.v1", "project_id": project_id,
            "dataset_id": dataset_id, "revision_id": revision_id,
            "revision_sha256": revision.payload_sha256, "members": [_render(*row) for row in rows]}

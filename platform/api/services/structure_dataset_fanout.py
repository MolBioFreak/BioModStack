"""Generic scheduler-visible fan-out for independent structure datasets."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Mapping, Sequence, TypeVar

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job

PayloadT = TypeVar("PayloadT")
FANOUT_PROVENANCE_KEY = "structure_dataset_fanout_v1"
FANOUT_SCHEMA = "bms.structure-dataset-fanout.v1"


class StructureDatasetFanoutError(ValueError):
    """The requested fan-out conflicts with persisted ownership or lineage."""


@dataclass(frozen=True)
class StructureDatasetMember(Generic[PayloadT]):
    """One independently schedulable structure and its immutable lineage."""

    structure_id: str
    lineage: Mapping[str, Any]
    payload: PayloadT


@dataclass(frozen=True)
class StructureDatasetBatch(Generic[PayloadT]):
    ordinal: int
    child_job_id: str
    members: tuple[StructureDatasetMember[PayloadT], ...]


@dataclass(frozen=True)
class StructureDatasetFanoutResult:
    fanout_id: str
    parent_job_id: str
    selected_structure_count: int
    structures_per_job: int
    effective_structures_per_job: int
    child_jobs: tuple[Job, ...]
    replayed: bool


ChildFactory = Callable[[StructureDatasetBatch[PayloadT]], Awaitable[Job]]
ChildDiscard = Callable[[Job], None]


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StructureDatasetFanoutError("fan-out identity and lineage must be canonical JSON") from exc


def _fanout_plan(
    *,
    workflow_id: str,
    parent_job_id: str,
    members: Sequence[StructureDatasetMember[Any]],
    batching_enabled: bool,
    structures_per_job: int,
    request_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if not workflow_id.strip():
        raise StructureDatasetFanoutError("workflow_id is required")
    if not isinstance(batching_enabled, bool):
        raise StructureDatasetFanoutError("batching_enabled must be a boolean")
    if isinstance(structures_per_job, bool) or not isinstance(structures_per_job, int) or structures_per_job < 1:
        raise StructureDatasetFanoutError("structures_per_job must be a positive integer")
    if not members:
        raise StructureDatasetFanoutError("at least one structure member is required")
    identities = [member.structure_id for member in members]
    if any(not identity.strip() for identity in identities) or len(set(identities)) != len(identities):
        raise StructureDatasetFanoutError("structure identities must be a non-empty ordered set")
    return {
        "schema_name": FANOUT_SCHEMA,
        "schema_version": 1,
        "workflow_id": workflow_id,
        "parent_job_id": parent_job_id,
        "batching_enabled": batching_enabled,
        "structures_per_job": structures_per_job,
        "effective_structures_per_job": structures_per_job if batching_enabled else 1,
        "request_identity": dict(request_identity),
        "members": [
            {"structure_id": member.structure_id, "lineage": dict(member.lineage)}
            for member in members
        ],
    }


def _child_id(fanout_id: str, ordinal: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:structure-dataset-fanout:{fanout_id}:{ordinal}"))


async def fan_out_structure_dataset(
    session: AsyncSession,
    *,
    workflow_id: str,
    parent_job: Job,
    members: Sequence[StructureDatasetMember[PayloadT]],
    batching_enabled: bool,
    structures_per_job: int,
    request_identity: Mapping[str, Any],
    create_child: ChildFactory[PayloadT],
    discard_child: ChildDiscard,
) -> StructureDatasetFanoutResult:
    """Create all scheduler Jobs in one DB transaction, or reconcile exact replay.

    The domain callback materializes one scheduler Job per batch without committing.
    The generic owner contract supplies deterministic IDs, exact member lineage,
    atomic database publication, replay validation, and artifact cleanup on failure.
    """

    parent_job_id = str(parent_job.id)
    plan = _fanout_plan(
        workflow_id=workflow_id,
        parent_job_id=parent_job_id,
        members=members,
        batching_enabled=batching_enabled,
        structures_per_job=structures_per_job,
        request_identity=request_identity,
    )
    plan_bytes = _canonical_bytes(plan)
    fanout_id = hashlib.sha256(plan_bytes).hexdigest()
    effective_structures_per_job = structures_per_job if batching_enabled else 1
    expected_count = (
        len(members) + effective_structures_per_job - 1
    ) // effective_structures_per_job
    expected_child_ids = [_child_id(fanout_id, ordinal) for ordinal in range(expected_count)]

    async def reconcile_exact_replay() -> StructureDatasetFanoutResult | None:
        await session.refresh(parent_job)
        current_provenance = dict(parent_job.provenance or {})
        current_fanouts = dict(current_provenance.get(FANOUT_PROVENANCE_KEY) or {})
        existing_contract = current_fanouts.get(fanout_id)
        if existing_contract is None:
            return None
        if not isinstance(existing_contract, dict) or existing_contract.get("plan") != plan:
            raise StructureDatasetFanoutError("persisted fan-out authority conflicts with this request")
        if existing_contract.get("child_job_ids") != expected_child_ids:
            raise StructureDatasetFanoutError("persisted fan-out child ownership is incomplete")
        children: list[Job] = []
        for child_id in expected_child_ids:
            child = await session.get(Job, child_id)
            child_fanout = (child.provenance or {}).get(FANOUT_PROVENANCE_KEY) if child else None
            if (
                child is None
                or str(child.parent_job_id or "") != parent_job_id
                or not isinstance(child_fanout, dict)
                or child_fanout.get("fanout_id") != fanout_id
            ):
                raise StructureDatasetFanoutError("persisted fan-out child ownership is incomplete")
            children.append(child)
        return StructureDatasetFanoutResult(
            fanout_id=fanout_id,
            parent_job_id=parent_job_id,
            selected_structure_count=len(members),
            structures_per_job=structures_per_job,
            effective_structures_per_job=effective_structures_per_job,
            child_jobs=tuple(children),
            replayed=True,
        )

    # Acquire the parent's database write lock before examining or materializing
    # its fan-out authority. On SQLite this no-op UPDATE obtains the RESERVED
    # writer lock; concurrent identical submissions wait, then refresh and replay
    # the winner instead of racing deterministic child roots.
    locked = await session.execute(
        update(Job).where(Job.id == parent_job_id).values(provenance=Job.provenance)
    )
    if locked.rowcount != 1:
        raise StructureDatasetFanoutError("fan-out parent Job no longer exists")
    replay = await reconcile_exact_replay()
    if replay is not None:
        await session.commit()
        return replay

    provenance = dict(parent_job.provenance or {})
    fanouts = dict(provenance.get(FANOUT_PROVENANCE_KEY) or {})

    created: list[Job] = []
    try:
        for ordinal, start in enumerate(
            range(0, len(members), effective_structures_per_job)
        ):
            batch_members = tuple(
                members[start : start + effective_structures_per_job]
            )
            batch = StructureDatasetBatch(
                ordinal=ordinal,
                child_job_id=expected_child_ids[ordinal],
                members=batch_members,
            )
            child = await create_child(batch)
            if str(child.id) != batch.child_job_id or str(child.parent_job_id or "") != parent_job_id:
                raise StructureDatasetFanoutError("child factory violated fan-out Job ownership")
            child_provenance = dict(child.provenance or {})
            child_provenance[FANOUT_PROVENANCE_KEY] = {
                "schema_name": FANOUT_SCHEMA,
                "schema_version": 1,
                "fanout_id": fanout_id,
                "parent_job_id": parent_job_id,
                "batch_ordinal": ordinal,
                "structure_ids": [member.structure_id for member in batch_members],
                "member_lineage": [dict(member.lineage) for member in batch_members],
            }
            child.provenance = child_provenance
            created.append(child)

        fanouts[fanout_id] = {
            "schema_name": FANOUT_SCHEMA,
            "schema_version": 1,
            "plan": plan,
            "child_job_ids": expected_child_ids,
        }
        provenance[FANOUT_PROVENANCE_KEY] = fanouts
        parent_job.provenance = provenance
        await session.flush()
        await session.commit()
    except Exception:
        await session.rollback()
        # A deterministic collision can still occur on databases whose locking
        # semantics differ from SQLite. Reconcile an exact committed winner
        # before considering artifact cleanup.
        try:
            replay = await reconcile_exact_replay()
        except Exception:
            replay = None
        if replay is not None:
            return replay
        for child in created:
            # Never delete a root that a concurrent winner now owns.
            if await session.get(Job, str(child.id)) is None:
                discard_child(child)
        raise

    return StructureDatasetFanoutResult(
        fanout_id=fanout_id,
        parent_job_id=parent_job_id,
        selected_structure_count=len(members),
        structures_per_job=structures_per_job,
        effective_structures_per_job=effective_structures_per_job,
        child_jobs=tuple(created),
        replayed=False,
    )


__all__ = [
    "FANOUT_PROVENANCE_KEY",
    "FANOUT_SCHEMA",
    "StructureDatasetBatch",
    "StructureDatasetFanoutError",
    "StructureDatasetFanoutResult",
    "StructureDatasetMember",
    "fan_out_structure_dataset",
]

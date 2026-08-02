from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, FrustraMPNNLandscapeRow, FrustraMPNNResult, Job

AnalyticsLevel = Literal["result", "residue", "mutation"]

IDENTITY_DIMENSIONS = [
    {"id": "dataset_id", "kind": "identifier", "description": "Stable workflow-result dataset identity (parent job ID)."},
    {"id": "workflow_family", "kind": "category", "description": "Persisted parent workflow family."},
    {"id": "job_id", "kind": "identifier", "description": "Scheduler-owned parent job identity."},
    {"id": "design_id", "kind": "identifier", "description": "Persisted source Design identity."},
    {"id": "invocation_id", "kind": "identifier", "description": "FrustraMPNN invocation identity within the parent job."},
    {"id": "configuration_id", "kind": "identifier", "description": "Global FrustraMPNN configuration identity."},
    {"id": "configuration_sha256", "kind": "identifier", "description": "Content hash of the global FrustraMPNN configuration."},
    {"id": "threshold_policy_id", "kind": "identifier", "description": "Versioned classification threshold policy."},
]
RESULT_METRICS = [
    {"id": "mean_score", "kind": "number", "unit": "FrustraMPNN score", "formula": "mean of finite scoreable persisted slots"},
    {"id": "native_score", "kind": "number", "unit": "FrustraMPNN score", "formula": "mean of persisted native slots"},
    {"id": "high_fraction", "kind": "fraction", "unit": "fraction", "formula": "high-class scoreable slots / scoreable slots"},
    {"id": "minimal_fraction", "kind": "fraction", "unit": "fraction", "formula": "minimal-class scoreable slots / scoreable slots"},
    {"id": "scoreable_fraction", "kind": "fraction", "unit": "fraction", "formula": "scoreable slots / persisted slots"},
    {"id": "slot_count", "kind": "count", "unit": "slots", "formula": "persisted landscape rows"},
    {"id": "residue_count", "kind": "count", "unit": "residues", "formula": "distinct exact author residue identities"},
]
RESIDUE_METRICS = [
    {"id": "native_score", "kind": "number", "unit": "FrustraMPNN score", "formula": "persisted WT→WT slot"},
    {"id": "alternative_mean_score", "kind": "number", "unit": "FrustraMPNN score", "formula": "mean across finite scoreable non-native slots"},
    {"id": "best_alternative_delta", "kind": "number", "unit": "score delta", "formula": "max(non-native score) − native score"},
    {"id": "worst_alternative_delta", "kind": "number", "unit": "score delta", "formula": "min(non-native score) − native score"},
    {"id": "high_alternative_fraction", "kind": "fraction", "unit": "fraction", "formula": "high-class scoreable non-native slots / scoreable non-native slots"},
    {"id": "minimal_alternative_fraction", "kind": "fraction", "unit": "fraction", "formula": "minimal-class scoreable non-native slots / scoreable non-native slots"},
    {"id": "alternative_count", "kind": "count", "unit": "slots", "formula": "finite scoreable non-native slots"},
]
MUTATION_METRICS = [
    {"id": "score", "kind": "number", "unit": "FrustraMPNN score", "formula": "persisted exact substitution score"},
    {"id": "scoreable", "kind": "boolean", "formula": "persisted scoreability"},
]


def parse_dataset_ids(raw: str | None) -> list[str]:
    if raw is None:
        return []
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values or len(values) > 20 or len(values) != len(set(values)):
        raise HTTPException(status_code=422, detail="dataset_ids must contain 1-20 unique parent job IDs")
    return values


def _workflow_family(parent_metadata: Any, job_params: Any, parent_workflow_id: str) -> str:
    for payload in (parent_metadata, job_params):
        if isinstance(payload, dict) and isinstance(payload.get("workflow_family"), str) and payload["workflow_family"].strip():
            return payload["workflow_family"].strip()
    return parent_workflow_id


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    return float(numerator) / float(denominator) if numerator is not None and denominator else None


def _base_identity(row: Any) -> dict[str, Any]:
    return {
        "dataset_id": row.parent_job_id,
        "workflow_family": _workflow_family(row.parent_metadata_json, row.job_params, row.parent_workflow_id),
        "job_id": row.parent_job_id,
        "design_id": row.design_id,
        "candidate_id": row.candidate_id,
        "invocation_id": row.invocation_id,
        "source_artifact_sha256": row.source_artifact_sha256,
        "checkpoint_sha256": (row.runtime_identity_json or {}).get("checkpoint_sha256"),
        "configuration_id": (row.summary_json or {}).get("configuration_id"),
        "configuration_sha256": (row.summary_json or {}).get("configuration_sha256"),
        "threshold_policy_id": ((row.summary_json or {}).get("threshold_policy") or {}).get("id")
            or ((row.summary_json or {}).get("threshold_policy") or {}).get("policy_id"),
    }


def _joined_columns():
    return (
        FrustraMPNNResult.parent_job_id,
        FrustraMPNNResult.invocation_id,
        FrustraMPNNResult.parent_workflow_id,
        FrustraMPNNResult.candidate_id,
        FrustraMPNNResult.design_id,
        FrustraMPNNResult.source_artifact_sha256,
        FrustraMPNNResult.runtime_identity_json,
        FrustraMPNNResult.summary_json,
        FrustraMPNNResult.parent_metadata_json,
        Job.params.label("job_params"),
    )


def _apply_datasets(statement, dataset_ids: list[str]):
    return statement.where(FrustraMPNNResult.parent_job_id.in_(dataset_ids)) if dataset_ids else statement


async def multidimensional_points(
    session: AsyncSession,
    *,
    level: AnalyticsLevel,
    dataset_ids: list[str],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if level in {"residue", "mutation"} and not dataset_ids:
        raise HTTPException(status_code=422, detail=f"dataset_ids is required for {level}-level analytics")
    if level == "result":
        items, total = await _result_points(session, dataset_ids, limit, offset)
        dimensions = IDENTITY_DIMENSIONS + RESULT_METRICS
    elif level == "residue":
        items, total = await _residue_points(session, dataset_ids, limit, offset)
        dimensions = IDENTITY_DIMENSIONS + [
            {"id": name, "kind": "identifier", "description": "Exact persisted residue identity."}
            for name in ("target_id", "entity_instance_id", "auth_asym_id", "auth_seq_id", "insertion_code", "sequence_index", "wt")
        ] + RESIDUE_METRICS
    else:
        items, total = await _mutation_points(session, dataset_ids, limit, offset)
        dimensions = IDENTITY_DIMENSIONS + [
            {"id": name, "kind": "identifier", "description": "Exact persisted residue/substitution identity."}
            for name in ("target_id", "entity_instance_id", "auth_asym_id", "auth_seq_id", "insertion_code", "sequence_index", "wt", "mutation_aa", "score_class", "status")
        ] + MUTATION_METRICS
    return {
        "schema_version": "frustrampnn_multidimensional_v1",
        "level": level,
        "dimensions": dimensions,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
        "items": items,
    }


async def _result_points(session: AsyncSession, dataset_ids: list[str], limit: int, offset: int):
    L = FrustraMPNNLandscapeRow
    residue_key = L.target_id + "|" + L.entity_instance_id + "|" + L.auth_asym_id + "|" + L.auth_seq_id + "|" + L.insertion_code + "|" + cast(L.sequence_index, type_=Float)
    scoreable = case((L.scoreable.is_(True), 1), else_=0)
    native_score = case((L.mutation_aa == L.wt, L.score), else_=None)
    aggregate_columns = (
        func.count(L.id).label("slot_count"),
        func.count(func.distinct(residue_key)).label("residue_count"),
        func.sum(scoreable).label("scoreable_count"),
        func.avg(case((L.scoreable.is_(True), L.score), else_=None)).label("mean_score"),
        func.avg(native_score).label("native_score"),
        func.sum(case((L.scoreable.is_(True) & (L.score_class == "high"), 1), else_=0)).label("high_count"),
        func.sum(case((L.scoreable.is_(True) & (L.score_class == "minimal"), 1), else_=0)).label("minimal_count"),
    )
    group_columns = _joined_columns()
    base = select(*group_columns, *aggregate_columns).join(L, (L.parent_job_id == FrustraMPNNResult.parent_job_id) & (L.invocation_id == FrustraMPNNResult.invocation_id)).join(Job, Job.id == FrustraMPNNResult.parent_job_id).group_by(*group_columns)
    base = _apply_datasets(base, dataset_ids)
    total = int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (await session.execute(base.order_by(FrustraMPNNResult.parent_job_id, FrustraMPNNResult.invocation_id).offset(offset).limit(limit))).all()
    items = []
    for row in rows:
        identity = _base_identity(row)
        identity.update({
            "point_id": f"{row.parent_job_id}:{row.invocation_id}",
            "metrics": {
                "mean_score": row.mean_score,
                "native_score": row.native_score,
                "high_fraction": _ratio(row.high_count, row.scoreable_count),
                "minimal_fraction": _ratio(row.minimal_count, row.scoreable_count),
                "scoreable_fraction": _ratio(row.scoreable_count, row.slot_count),
                "slot_count": row.slot_count,
                "residue_count": row.residue_count,
            },
        })
        items.append(identity)
    return items, total


async def _residue_points(session: AsyncSession, dataset_ids: list[str], limit: int, offset: int):
    L = FrustraMPNNLandscapeRow
    exact_columns = (L.target_id, L.entity_instance_id, L.auth_asym_id, L.auth_seq_id, L.insertion_code, L.sequence_index, L.wt)
    alt = (L.mutation_aa != L.wt) & L.scoreable.is_(True)
    native = L.mutation_aa == L.wt
    aggregates = (
        func.max(case((native, L.score), else_=None)).label("native_score"),
        func.avg(case((alt, L.score), else_=None)).label("alternative_mean_score"),
        func.max(case((alt, L.score), else_=None)).label("best_alternative_score"),
        func.min(case((alt, L.score), else_=None)).label("worst_alternative_score"),
        func.sum(case((alt, 1), else_=0)).label("alternative_count"),
        func.sum(case((alt & (L.score_class == "high"), 1), else_=0)).label("high_count"),
        func.sum(case((alt & (L.score_class == "minimal"), 1), else_=0)).label("minimal_count"),
    )
    group_columns = _joined_columns() + exact_columns
    base = select(*group_columns, *aggregates).join(L, (L.parent_job_id == FrustraMPNNResult.parent_job_id) & (L.invocation_id == FrustraMPNNResult.invocation_id)).join(Job, Job.id == FrustraMPNNResult.parent_job_id).group_by(*group_columns)
    base = _apply_datasets(base, dataset_ids)
    total = int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (await session.execute(base.order_by(FrustraMPNNResult.parent_job_id, FrustraMPNNResult.invocation_id, L.target_id, L.entity_instance_id, L.auth_asym_id, L.sequence_index, L.insertion_code).offset(offset).limit(limit))).all()
    items = []
    for row in rows:
        identity = _base_identity(row)
        residue_parts = [row.target_id, row.entity_instance_id, row.auth_asym_id, row.auth_seq_id, row.insertion_code, str(row.sequence_index)]
        identity.update({
            "point_id": ":".join([row.parent_job_id, row.invocation_id, *residue_parts]),
            "target_id": row.target_id, "entity_instance_id": row.entity_instance_id,
            "auth_asym_id": row.auth_asym_id, "auth_seq_id": row.auth_seq_id,
            "insertion_code": row.insertion_code, "sequence_index": row.sequence_index, "wt": row.wt,
            "metrics": {
                "native_score": row.native_score,
                "alternative_mean_score": row.alternative_mean_score,
                "best_alternative_delta": row.best_alternative_score - row.native_score if row.best_alternative_score is not None and row.native_score is not None else None,
                "worst_alternative_delta": row.worst_alternative_score - row.native_score if row.worst_alternative_score is not None and row.native_score is not None else None,
                "high_alternative_fraction": _ratio(row.high_count, row.alternative_count),
                "minimal_alternative_fraction": _ratio(row.minimal_count, row.alternative_count),
                "alternative_count": row.alternative_count,
            },
        })
        items.append(identity)
    return items, total


async def _mutation_points(session: AsyncSession, dataset_ids: list[str], limit: int, offset: int):
    L = FrustraMPNNLandscapeRow
    base = select(*_joined_columns(), L.target_id, L.entity_instance_id, L.auth_asym_id, L.auth_seq_id, L.insertion_code, L.sequence_index, L.wt, L.mutation_aa, L.score, L.score_class, L.scoreable, L.status, L.reason).join(L, (L.parent_job_id == FrustraMPNNResult.parent_job_id) & (L.invocation_id == FrustraMPNNResult.invocation_id)).join(Job, Job.id == FrustraMPNNResult.parent_job_id)
    base = _apply_datasets(base, dataset_ids)
    total = int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (await session.execute(base.order_by(FrustraMPNNResult.parent_job_id, FrustraMPNNResult.invocation_id, L.target_id, L.entity_instance_id, L.auth_asym_id, L.sequence_index, L.insertion_code, L.mutation_aa, L.id).offset(offset).limit(limit))).all()
    items = []
    for row in rows:
        identity = _base_identity(row)
        residue_parts = [row.target_id, row.entity_instance_id, row.auth_asym_id, row.auth_seq_id, row.insertion_code, str(row.sequence_index), row.mutation_aa]
        identity.update({
            "point_id": ":".join([row.parent_job_id, row.invocation_id, *residue_parts]),
            "target_id": row.target_id, "entity_instance_id": row.entity_instance_id,
            "auth_asym_id": row.auth_asym_id, "auth_seq_id": row.auth_seq_id,
            "insertion_code": row.insertion_code, "sequence_index": row.sequence_index,
            "wt": row.wt, "mutation_aa": row.mutation_aa, "score_class": row.score_class,
            "status": row.status, "reason": row.reason,
            "metrics": {"score": row.score, "scoreable": row.scoreable},
        })
        items.append(identity)
    return items, total

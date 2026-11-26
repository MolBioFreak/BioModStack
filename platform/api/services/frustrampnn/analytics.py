from __future__ import annotations

import copy
import base64
import hashlib
import math
from collections import Counter
from typing import Any, Literal, Mapping, Sequence

import rfc8785
from fastapi import HTTPException
from sqlalchemy import and_, exists, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import FrustraMPNNResult, Job, ScientificArtifactReceipt
from .contracts import (
    AA_ORDER,
    ContractValidationError,
    canonical_json_loads,
    canonical_sha256,
    validate_schema,
)
from services import stats_toolkit_client as _stats_toolkit_client
from services.stats_toolkit_client import (
    STATS_TOOLKIT_UNAVAILABLE_REASON,
    StatsToolkitClient,
    StatsToolkitError,
    StatsToolkitNotFoundError,
    StatsToolkitUnavailableError,
    get_stats_toolkit_client,
)
from .persistence import landscape_page

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
        "configuration_id": (row.summary_json or {}).get("execution_configuration_id")
            or (row.summary_json or {}).get("configuration_id"),
        "configuration_sha256": (row.summary_json or {}).get("execution_configuration_sha256")
            or (row.summary_json or {}).get("configuration_sha256"),
        "threshold_policy_id": (row.summary_json or {}).get("threshold_policy_id")
            or ((row.summary_json or {}).get("threshold_policy") or {}).get("id")
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


def _artifact_backed_results(dataset_ids: list[str]):
    receipt = ScientificArtifactReceipt
    owner_id = (
        FrustraMPNNResult.parent_job_id
        + literal(":")
        + FrustraMPNNResult.invocation_id
    )
    has_landscape = exists(
        select(receipt.artifact_id).where(
            receipt.owner_id == owner_id,
            receipt.availability == "available",
            or_(
                and_(
                    receipt.owner_kind == "frustrampnn_result",
                    receipt.role == "landscape",
                ),
                and_(
                    receipt.owner_kind == "frustrampnn_landscape",
                    receipt.role == "rows",
                ),
            ),
        )
    )
    statement = (
        select(*_joined_columns())
        .join(Job, Job.id == FrustraMPNNResult.parent_job_id)
        .where(has_landscape)
    )
    return _apply_datasets(statement, dataset_ids)


async def _selected_results(
    session: AsyncSession, dataset_ids: list[str]
) -> list[Any]:
    return (
        await session.execute(
            _artifact_backed_results(dataset_ids).order_by(
                FrustraMPNNResult.parent_job_id,
                FrustraMPNNResult.invocation_id,
            )
        )
    ).all()


async def _artifact_rows_for_result(
    session: AsyncSession, result: Any
) -> list[dict[str, Any]]:
    page = await landscape_page(
        session,
        result.parent_job_id,
        result.invocation_id,
        limit=500,
    )
    rows = list(page["items"])
    total = int(page["total"])
    while len(rows) < total:
        next_page = await landscape_page(
            session,
            result.parent_job_id,
            result.invocation_id,
            limit=500,
            offset=len(rows),
        )
        if not next_page["items"]:
            raise HTTPException(
                status_code=503,
                detail="FrustraMPNN landscape artifact paging is incomplete",
            )
        rows.extend(next_page["items"])
    return rows


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _finite_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


async def _result_points(
    session: AsyncSession, dataset_ids: list[str], limit: int, offset: int
):
    results = await _selected_results(session, dataset_ids)
    total = len(results)
    items = []
    for result in results[offset:offset + limit]:
        rows = await _artifact_rows_for_result(session, result)
        scoreable_rows = [row for row in rows if row["scoreable"] is True]
        scoreable_scores = [
            score
            for row in scoreable_rows
            if (score := _finite_score(row["score"])) is not None
        ]
        native_scores = [
            score
            for row in rows
            if row["mutation_aa"] == row["wt"]
            and (score := _finite_score(row["score"])) is not None
        ]
        residue_count = len({
            (
                row["target_id"],
                row["entity_instance_id"],
                row["auth_asym_id"],
                row["auth_seq_id"],
                row["insertion_code"],
                row["sequence_index"],
            )
            for row in rows
        })
        high_count = sum(
            row["score_class"] == "high" for row in scoreable_rows
        )
        minimal_count = sum(
            row["score_class"] == "minimal" for row in scoreable_rows
        )
        identity = _base_identity(result)
        identity.update({
            "point_id": f"{result.parent_job_id}:{result.invocation_id}",
            "metrics": {
                "mean_score": _mean(scoreable_scores),
                "native_score": _mean(native_scores),
                "high_fraction": _ratio(high_count, len(scoreable_rows)),
                "minimal_fraction": _ratio(minimal_count, len(scoreable_rows)),
                "scoreable_fraction": _ratio(len(scoreable_rows), len(rows)),
                "slot_count": len(rows),
                "residue_count": residue_count,
            },
        })
        items.append(identity)
    return items, total


async def _residue_points(
    session: AsyncSession, dataset_ids: list[str], limit: int, offset: int
):
    items: list[dict[str, Any]] = []
    for result in await _selected_results(session, dataset_ids):
        rows = await _artifact_rows_for_result(session, result)
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in rows:
            key = (
                row["target_id"], row["entity_instance_id"], row["auth_asym_id"],
                row["auth_seq_id"], row["insertion_code"], row["sequence_index"],
                row["wt"],
            )
            grouped.setdefault(key, []).append(row)
        for key, residue_rows in grouped.items():
            target_id, entity_instance_id, auth_asym_id, auth_seq_id, insertion_code, sequence_index, wt = key
            native_scores = [
                score
                for row in residue_rows
                if row["mutation_aa"] == row["wt"]
                and (score := _finite_score(row["score"])) is not None
            ]
            alternatives = [
                row
                for row in residue_rows
                if row["mutation_aa"] != row["wt"] and row["scoreable"] is True
            ]
            alternative_scores = [
                score
                for row in alternatives
                if (score := _finite_score(row["score"])) is not None
            ]
            native_score = max(native_scores) if native_scores else None
            best = max(alternative_scores) if alternative_scores else None
            worst = min(alternative_scores) if alternative_scores else None
            high_count = sum(row["score_class"] == "high" for row in alternatives)
            minimal_count = sum(row["score_class"] == "minimal" for row in alternatives)
            identity = _base_identity(result)
            residue_parts = [
                target_id, entity_instance_id, auth_asym_id, auth_seq_id,
                insertion_code, str(sequence_index),
            ]
            identity.update({
                "point_id": ":".join([
                    result.parent_job_id, result.invocation_id, *residue_parts,
                ]),
                "target_id": target_id,
                "entity_instance_id": entity_instance_id,
                "auth_asym_id": auth_asym_id,
                "auth_seq_id": auth_seq_id,
                "insertion_code": insertion_code,
                "sequence_index": sequence_index,
                "wt": wt,
                "metrics": {
                    "native_score": native_score,
                    "alternative_mean_score": _mean(alternative_scores),
                    "best_alternative_delta": (
                        best - native_score
                        if best is not None and native_score is not None else None
                    ),
                    "worst_alternative_delta": (
                        worst - native_score
                        if worst is not None and native_score is not None else None
                    ),
                    "high_alternative_fraction": _ratio(high_count, len(alternatives)),
                    "minimal_alternative_fraction": _ratio(minimal_count, len(alternatives)),
                    "alternative_count": len(alternatives),
                },
            })
            items.append(identity)
    items.sort(key=lambda item: (
        item["dataset_id"], item["invocation_id"], item["target_id"],
        item["entity_instance_id"], item["auth_asym_id"], item["sequence_index"],
        item["insertion_code"],
    ))
    total = len(items)
    return items[offset:offset + limit], total


async def _mutation_points(
    session: AsyncSession, dataset_ids: list[str], limit: int, offset: int
):
    items: list[dict[str, Any]] = []
    for result in await _selected_results(session, dataset_ids):
        for row in await _artifact_rows_for_result(session, result):
            identity = _base_identity(result)
            residue_parts = [
                row["target_id"], row["entity_instance_id"], row["auth_asym_id"],
                row["auth_seq_id"], row["insertion_code"],
                str(row["sequence_index"]), row["mutation_aa"],
            ]
            identity.update({
                "point_id": ":".join([
                    result.parent_job_id, result.invocation_id, *residue_parts,
                ]),
                "target_id": row["target_id"],
                "entity_instance_id": row["entity_instance_id"],
                "auth_asym_id": row["auth_asym_id"],
                "auth_seq_id": row["auth_seq_id"],
                "insertion_code": row["insertion_code"],
                "sequence_index": row["sequence_index"],
                "wt": row["wt"],
                "mutation_aa": row["mutation_aa"],
                "score_class": row["score_class"],
                "status": row["status"],
                "reason": row["reason"],
                "metrics": {"score": row["score"], "scoreable": row["scoreable"]},
                "_row_id": row["id"],
            })
            items.append(identity)
    items.sort(key=lambda item: (
        item["dataset_id"], item["invocation_id"], item["target_id"],
        item["entity_instance_id"], item["auth_asym_id"], item["sequence_index"],
        item["insertion_code"], item["mutation_aa"], item["_row_id"],
    ))
    total = len(items)
    page = items[offset:offset + limit]
    for item in page:
        item.pop("_row_id", None)
    return page, total


_STATISTIC_CLASSES = ("high", "neutral", "minimal")
_RESIDUE_IDENTITY_FIELDS = (
    "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
    "auth_seq_id", "insertion_code", "sequence_index", "wt", "pdb_chain_id",
    "model_position",
)


def _rfc8785_sha256(value: Any) -> str:
    try:
        return hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ContractValidationError(
            f"statistics authority is not finite RFC 8785 JSON: {exc}"
        ) from exc


_DISTRIBUTION_SCALARS = (
    "count", "mean", "median", "sample_sd", "min", "max", "q1", "q3", "iqr",
)


def _denominator(kind: str, count: int) -> dict[str, Any]:
    return {"kind": kind, "count": count}


def _fraction_metric(numerator: int, denominator: int, kind: str) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "denominator": _denominator(kind, denominator),
        "missingness_reason": None if denominator else "zero_denominator",
    }


def _distribution_from_toolkit_column(
    column: Mapping[str, Any],
    *,
    denominator_kind: str = "input_values",
    denominator_count: int | None = None,
    missingness_reason: str | None = None,
) -> dict[str, Any]:
    """Format one Stats Toolkit descriptive.summary column into the FrustraMPNN distribution shape.

    All distribution scalars (mean, median, sample_sd, min, max, q1, q3, iqr)
    come directly from the Stats Toolkit descriptive.summary run.
    """
    if not isinstance(column, Mapping):
        raise ContractValidationError("Stats Toolkit descriptive.summary column must be an object")
    raw_count = column.get("count")
    if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
        raise ContractValidationError("Stats Toolkit descriptive.summary count must be a non-negative integer")
    count = int(raw_count)
    expected_count = count if denominator_count is None else denominator_count
    if expected_count < count:
        raise ContractValidationError("statistics denominator cannot be smaller than value count")
    support_reason = missingness_reason or (
        "partial_support" if count < expected_count else None
    )
    unavailable_reason = support_reason or (
        "no_observed_values" if expected_count else "zero_denominator"
    )
    denominators = {
        "count": _denominator(denominator_kind, expected_count),
        **{
            name: _denominator("finite_scoreable_values", count)
            for name in ("mean", "median", "min", "max", "q1", "q3", "iqr")
        },
        "sample_sd": _denominator(
            "sample_degrees_of_freedom_n_minus_1", count - 1 if count >= 2 else 0
        ),
    }
    reasons = {
        name: (support_reason if count else unavailable_reason)
        for name in _DISTRIBUTION_SCALARS
    }
    reasons["count"] = support_reason
    reasons["sample_sd"] = (
        "insufficient_support_n_lt_2" if count < 2 else support_reason
    )
    if not count:
        return {
            **{key: 0 if key == "count" else None for key in _DISTRIBUTION_SCALARS},
            "denominators": denominators,
            "missingness_reasons": reasons,
        }

    def _finite_opt(raw: Any, field: str, *, allow_none: bool = False) -> float | None:
        if raw is None and allow_none:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ContractValidationError(f"Stats Toolkit descriptive.summary {field} must be numeric")
        val = float(raw)
        if not math.isfinite(val):
            raise ContractValidationError(f"Stats Toolkit descriptive.summary {field} must be finite")
        return val

    return {
        "count": count,
        "mean": _finite_opt(column.get("mean"), "mean"),
        "median": _finite_opt(column.get("median"), "median"),
        "sample_sd": _finite_opt(column.get("std"), "std", allow_none=True) if count >= 2 else None,
        "min": _finite_opt(column.get("min"), "min"),
        "max": _finite_opt(column.get("max"), "max"),
        "q1": _finite_opt(column.get("q1"), "q1"),
        "q3": _finite_opt(column.get("q3"), "q3"),
        "iqr": _finite_opt(column.get("iqr"), "iqr"),
        "denominators": denominators,
        "missingness_reasons": reasons,
    }


def comparison_compatibility_id(basis: Mapping[str, Any]) -> str:
    if not isinstance(basis, Mapping):
        raise ContractValidationError("comparison compatibility basis must be an object")
    return _rfc8785_sha256({
        "schema_name": "frustrampnn_comparison_compatibility",
        "schema_version": 1,
        "basis": copy.deepcopy(dict(basis)),
    })


def _identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in _RESIDUE_IDENTITY_FIELDS}


def _identity_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple("" if row[field] is None else row[field] for field in _RESIDUE_IDENTITY_FIELDS)


def _slot_class(score: float, policy: Mapping[str, Any]) -> str:
    return "high" if score <= policy["high_max"] else (
        "minimal" if score >= policy["minimal_min"] else "neutral"
    )


def _class_burden(
    classes: Sequence[str], *, denominator_kind: str,
    denominator_count: int | None = None, missingness_reason: str | None = None,
) -> dict[str, Any]:
    counts, denominator = Counter(classes), len(classes)
    expected_count = denominator if denominator_count is None else denominator_count
    if expected_count < denominator:
        raise ContractValidationError("class-burden denominator cannot be smaller than support")
    reason = missingness_reason or (
        "partial_support" if denominator < expected_count else None
    )
    if not denominator:
        reason = reason or ("no_observed_values" if expected_count else "zero_denominator")
    return {
        "support_count": denominator,
        "counts": {name: counts[name] for name in _STATISTIC_CLASSES},
        "fractions": {
            name: counts[name] / denominator if denominator else None
            for name in _STATISTIC_CLASSES
        },
        "denominator": _denominator(denominator_kind, expected_count),
        "missingness_reason": reason,
    }


def _group_support(residues: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    slots_count = sum(len(residue["slots"]) for residue in residues)
    count = len(residues)
    return {
        "selected_residue_count": count,
        "observed_residue_count": count,
        "fully_scoreable_residue_count": count,
        "expected_slot_count": count * len(AA_ORDER),
        "observed_slot_count": slots_count,
        "scoreable_slot_count": slots_count,
    }


def _validate_statistics_inputs(
    *, request: Mapping[str, Any], execution_receipt: Mapping[str, Any],
    landscape: Mapping[str, Any], structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any], capability_inventory_bytes: bytes,
    allow_legacy_external_authority: bool = False,
) -> None:
    for name, value in (
        ("request", request), ("execution receipt", execution_receipt),
        ("landscape", landscape), ("structure map", structure_map),
        ("capability inventory", capability_inventory),
    ):
        if not isinstance(value, Mapping):
            raise ContractValidationError(f"{name} must be an object")
    if not isinstance(capability_inventory_bytes, bytes):
        raise ContractValidationError("capability inventory bytes must be exact bytes")
    schema_request = request
    if (
        allow_legacy_external_authority
        and request.get("identity_authority") in {"producer_manifest", "cm_complex_snapshot"}
        and "bytes" not in request.get("identity_authority_artifact", {})
    ):
        schema_request = dict(request)
        envelope = dict(request["identity_authority_artifact"])
        envelope["bytes"] = len(base64.b64decode(envelope["canonical_json_base64"], validate=True))
        schema_request["identity_authority_artifact"] = envelope
    request_generation = request.get("schema_version")
    if request_generation not in {2, 3}:
        raise ContractValidationError("statistics require a modern request generation")
    validate_schema(
        f"workflow_component_request_v{request_generation}", schema_request
    )
    validate_schema(
        f"frustrampnn_execution_receipt_v{request_generation}", execution_receipt
    )
    validate_schema(
        f"frustrampnn_landscape_v{request_generation}", landscape
    )
    validate_schema("frustrampnn_structure_map_v1", structure_map)
    validate_schema("capability_inventory_v1", capability_inventory)

    if canonical_json_loads(capability_inventory_bytes) != dict(capability_inventory):
        raise ContractValidationError("capability inventory bytes/content mismatch")
    byte_sha = hashlib.sha256(capability_inventory_bytes).hexdigest()
    if byte_sha != request["capability_inventory_byte_sha256"]:
        raise ContractValidationError("capability inventory byte SHA-256 mismatch")
    inventory_preimage = dict(capability_inventory)
    content_sha = inventory_preimage.pop("content_sha256")
    if content_sha != _rfc8785_sha256(inventory_preimage):
        raise ContractValidationError("capability inventory content SHA-256 mismatch")

    effective = request["effective_settings"]
    configuration = request["execution_configuration"]
    resolution = effective["resolution_identity"]
    if execution_receipt["invocation_id"] != request["invocation_id"]:
        raise ContractValidationError("execution receipt invocation identity mismatch")
    for field in ("parent_job_id", "candidate_id"):
        if landscape[field] != request[field] or structure_map[field] != request[field]:
            raise ContractValidationError(f"{field} identity binding mismatch")
    if landscape["target_id"] != structure_map["target_id"]:
        raise ContractValidationError("target identity binding mismatch")
    expected_map_authority = {
        "pdb_coordinates": "pdb_self_identity_v1",
        "mmcif_atom_site": "mmcif_atom_site_v1",
        "producer_manifest": "producer_manifest_v1",
        "cm_complex_snapshot": "producer_manifest_v1",
    }[request["identity_authority"]]
    if structure_map["identity_authority"] != expected_map_authority:
        raise ContractValidationError("request/structure-map identity authority binding mismatch")
    bindings = {
        "execution_configuration_sha256": request["execution_configuration_sha256"],
        "requested_settings_sha256": request["requested_settings_sha256"],
        "effective_settings_sha256": request["effective_settings_sha256"],
        "runtime_identity_sha256": request["runtime_identity_sha256"],
        "source_artifact_sha256": request["source_artifact"]["sha256"],
        "structure_map_sha256": request["structure_map_sha256"],
        "normalized_pdb_sha256": request["normalized_pdb_sha256"],
    }
    for field, expected in bindings.items():
        if execution_receipt[field] != expected:
            raise ContractValidationError(f"execution receipt {field} binding mismatch")
        if field in landscape and landscape[field] != expected:
            raise ContractValidationError(f"landscape {field} binding mismatch")
    if execution_receipt["landscape_sha256"] != canonical_sha256(landscape):
        raise ContractValidationError("execution receipt landscape SHA-256 mismatch")
    if canonical_sha256(structure_map) != request["structure_map_sha256"]:
        raise ContractValidationError("structure map SHA-256 binding mismatch")
    if structure_map["source_sha256"] != resolution["source_artifact_sha256"]:
        raise ContractValidationError("structure map source identity binding mismatch")
    if structure_map["normalized_pdb_sha256"] != resolution["normalized_pdb_sha256"]:
        raise ContractValidationError("structure map normalized PDB binding mismatch")
    policy = request["requested_settings"]["classification_policy"]
    if landscape["threshold_policy"] != policy or landscape[
        "threshold_policy_sha256"
    ] != request["classification_policy_sha256"]:
        raise ContractValidationError("landscape classification policy binding mismatch")
    if landscape["execution_configuration_id"] != configuration["configuration_id"]:
        raise ContractValidationError("landscape execution configuration identity mismatch")

    selected = [r for chain in effective["resolved_chains"] for r in chain["residues"]]
    selected_keys = [_identity_key(row) for row in selected]
    landscape_keys = [_identity_key(row) for row in landscape["residues"]]
    if len(selected_keys) != len(set(selected_keys)):
        raise ContractValidationError("effective selection has duplicate residue identity")
    if landscape_keys != selected_keys:
        raise ContractValidationError(
            "landscape residue identity/order does not match effective selection"
        )
    mapped_rows = [row for row in structure_map["rows"] if row["status"] == "mapped"]
    map_by_key = {_identity_key(row): row for row in mapped_rows}
    if len(map_by_key) != len(mapped_rows):
        raise ContractValidationError("structure map has duplicate mapped identity")
    if any(key not in map_by_key for key in selected_keys):
        raise ContractValidationError("structure map is missing selected residue identity")
    selected_set = set(selected_keys)
    map_order = [_identity_key(row) for row in structure_map["rows"] if _identity_key(row) in selected_set]
    if map_order != selected_keys:
        raise ContractValidationError("structure map selected rows are not in canonical order")

    expected_slots = len(selected) * len(AA_ORDER)
    observed_slots = sum(len(row["slots"]) for row in landscape["residues"])
    if observed_slots != expected_slots:
        raise ContractValidationError("successful landscape must have exact 20-slot support")
    for residue in landscape["residues"]:
        if residue["wt"] not in AA_ORDER:
            raise ContractValidationError("landscape contains unknown WT amino acid")
        if [slot["mutation_aa"] for slot in residue["slots"]] != list(AA_ORDER):
            raise ContractValidationError("landscape slots are not unique canonical order")
        for slot in residue["slots"]:
            score = slot["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ContractValidationError("landscape score must be finite")
            if slot["status"] != "ok" or slot["scoreable"] is not True or slot["reason"] is not None:
                raise ContractValidationError("successful v2 landscape is incomplete")
            if slot["class"] != _slot_class(float(score), policy):
                raise ContractValidationError("landscape score class binding mismatch")


def _compatibility_basis(
    request: Mapping[str, Any], structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any], capability_inventory_bytes: bytes,
) -> dict[str, Any]:
    """Return the closed scientific basis used by field-level comparison.

    Dataset identity, selected membership, source bytes, normalized coordinates,
    execution/configuration digests, and invocation identity are deliberately not
    raw-score semantics. Residue membership is assessed independently by the
    comparison alignment domain.
    """
    del capability_inventory_bytes
    effective = request["effective_settings"]
    configuration = request["execution_configuration"]
    runtime = configuration["runtime"]
    return {
        "schema_name": "frustrampnn_comparison_compatibility_basis",
        "schema_version": 2,
        "raw_score_semantics": {
            "model": {
                "checkpoint_id": runtime["checkpoint_id"],
                "checkpoint_sha256": runtime["checkpoint_sha256"],
            },
            "tool": {
                "tool_id": configuration["tool_id"],
                "tool_version": configuration["tool_version"],
            },
            "capability": {
                "schema_name": capability_inventory["schema_name"],
                "schema_version": capability_inventory["schema_version"],
                "content_sha256": capability_inventory["content_sha256"],
            },
            "output_schema": {
                "component_id": request["component_id"],
                "component_contract_version": request["component_contract_version"],
                "landscape_schema_name": "frustrampnn_landscape",
                "landscape_schema_version": request["schema_version"],
                "score_field": "score",
            },
            "canonical_amino_acid_order": AA_ORDER,
            "normalization": {
                "normalizer_version": structure_map["normalizer_version"],
                "identity_authority": structure_map["identity_authority"],
                "identity_domain": structure_map["identity_domain"],
                "selected_source_model": structure_map["selected_source_model"],
                "altloc_policy": structure_map["altloc_policy"],
                "normalization_policy_id": effective["normalization_policy_id"],
                "normalization_policy_version": effective[
                    "normalization_policy_version"
                ],
            },
        },
        "classification_policy": {
            "policy_id": effective["threshold_policy_id"],
            "policy_sha256": request["classification_policy_sha256"],
            "policy": copy.deepcopy(
                request["requested_settings"]["classification_policy"]
            ),
        },
    }


def _extract_deterministic_receipt_context(
    *,
    request: Mapping[str, Any],
    execution_receipt: Mapping[str, Any],
    landscape: Mapping[str, Any],
    structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any],
    capability_inventory_bytes: bytes,
    allow_legacy_external_authority: bool = False,
) -> dict[str, Any]:
    """Compute all deterministic non-statistics fields and Stats Toolkit input columns."""
    _validate_statistics_inputs(
        request=request,
        execution_receipt=execution_receipt,
        landscape=landscape,
        structure_map=structure_map,
        capability_inventory=capability_inventory,
        capability_inventory_bytes=capability_inventory_bytes,
        allow_legacy_external_authority=allow_legacy_external_authority,
    )
    residues = list(landscape["residues"])
    all_slots = [slot for residue in residues for slot in residue["slots"]]
    native_slots = [slot for slot in all_slots if slot["native"]]
    non_native_slots = [slot for slot in all_slots if not slot["native"]]
    selected = [
        residue for chain in request["effective_settings"]["resolved_chains"]
        for residue in chain["residues"]
    ]
    selected_keys = {_identity_key(row) for row in selected}
    exclusion_counts: Counter[tuple[str, str, str, str]] = Counter()
    mapping_missing_counts: Counter[tuple[str, str, str, str]] = Counter()
    for row in structure_map["rows"]:
        if row["status"] == "mapped" and _identity_key(row) in selected_keys:
            continue
        if row["status"] == "mapped":
            reason_code = "not_selected"
            reason = "mapped residue is outside the effective selection"
            status = "excluded"
        else:
            reason_code = row["status"]
            reason = row["reason"] or f"structure-map status {row['status']}"
            status = row["status"]
        target_counts = (
            mapping_missing_counts
            if status in {"missing_backbone", "nonstandard_residue"}
            else exclusion_counts
        )
        target_counts[("structure_map_row", status, reason_code, reason)] += 1
    for record in structure_map["excluded_records"]:
        target_counts = (
            mapping_missing_counts
            if record["reason_code"] in {"missing_backbone", "nonstandard_residue"}
            else exclusion_counts
        )
        target_counts[(
            "excluded_record", "excluded", record["reason_code"], record["reason"],
        )] += 1
    exclusion_reasons = [
        {
            "authority": authority, "status": status, "reason_code": reason_code,
            "reason": reason, "count": reason_count,
        }
        for (authority, status, reason_code, reason), reason_count
        in sorted(exclusion_counts.items())
    ]
    mapping_missing_reasons = [
        {
            "authority": authority, "status": status, "reason_code": reason_code,
            "reason": reason, "count": reason_count,
        }
        for (authority, status, reason_code, reason), reason_count
        in sorted(mapping_missing_counts.items())
    ]
    count = len(selected)
    source_count = len(structure_map["rows"]) + len(structure_map["excluded_records"])
    observed_count = len(residues)
    fully_scoreable_count = sum(
        all(slot["scoreable"] for slot in residue["slots"]) for residue in residues
    )
    partially_scoreable_count = sum(
        any(slot["scoreable"] for slot in residue["slots"])
        and not all(slot["scoreable"] for slot in residue["slots"])
        for residue in residues
    )
    scoreable_residue_count = fully_scoreable_count + partially_scoreable_count
    excluded_count = sum(item["count"] for item in exclusion_reasons)
    mapping_missing_count = sum(item["count"] for item in mapping_missing_reasons)
    selected_missing_count = count - observed_count
    missing_count = mapping_missing_count + selected_missing_count
    expected_slots = count * len(AA_ORDER)
    observed_slots = len(all_slots)
    scoreable_slots = sum(slot["scoreable"] for slot in all_slots)
    missing_slots = expected_slots - observed_slots
    support = {
        "source_residue_count": source_count,
        "selected_residue_count": count, "observed_residue_count": observed_count,
        "scoreable_residue_count": scoreable_residue_count,
        "excluded_residue_count": excluded_count, "missing_residue_count": missing_count,
        "mapping_missing_residue_count": mapping_missing_count,
        "selected_missing_residue_count": selected_missing_count,
        "fully_scoreable_residue_count": fully_scoreable_count,
        "partially_scoreable_residue_count": partially_scoreable_count,
        "expected_slot_count": expected_slots, "observed_slot_count": observed_slots,
        "scoreable_slot_count": scoreable_slots,
        "excluded_slot_count": excluded_count * len(AA_ORDER),
        "mapping_missing_slot_count": mapping_missing_count * len(AA_ORDER),
        "missing_slot_count": missing_slots,
        "residue_fractions": {
            "selected": _fraction_metric(count, source_count, "structure_map_source_residues"),
            "observed": _fraction_metric(observed_count, source_count, "structure_map_source_residues"),
            "scoreable": _fraction_metric(scoreable_residue_count, source_count, "structure_map_source_residues"),
            "excluded": _fraction_metric(excluded_count, source_count, "structure_map_source_residues"),
            "missing": _fraction_metric(missing_count, source_count, "structure_map_source_residues"),
            "selected_missing": _fraction_metric(
                selected_missing_count, count, "effective_selected_residues"
            ),
        },
        "slot_fractions": {
            "observed": _fraction_metric(observed_slots, expected_slots, "expected_selected_slots"),
            "scoreable": _fraction_metric(scoreable_slots, expected_slots, "expected_selected_slots"),
            "excluded": _fraction_metric(
                excluded_count * len(AA_ORDER), source_count * len(AA_ORDER),
                "source_residue_slots",
            ),
            "missing": _fraction_metric(
                mapping_missing_count * len(AA_ORDER), source_count * len(AA_ORDER),
                "source_residue_slots",
            ),
            "selected_missing": _fraction_metric(
                missing_slots, expected_slots, "expected_selected_slots"
            ),
        },
        "exclusion_reasons": exclusion_reasons,
        "missing_reasons": mapping_missing_reasons + ([{
            "authority": "landscape",
            "status": "missing",
            "reason_code": "landscape_missing_selected_residue",
            "reason": "effective selected residue has no landscape row",
            "count": selected_missing_count,
        }] if selected_missing_count else []),
    }

    columns: dict[str, list[float]] = {
        "overall": [float(slot["score"]) for slot in all_slots],
        "native": [float(slot["score"]) for slot in native_slots],
        "non_native": [float(slot["score"]) for slot in non_native_slots],
    }
    per_residue_meta: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    deltas: list[float] = []
    for idx, residue in enumerate(residues):
        native = next(slot for slot in residue["slots"] if slot["native"])
        non_native = [slot for slot in residue["slots"] if not slot["native"]]
        columns[f"residue:{idx}:all"] = [float(slot["score"]) for slot in residue["slots"]]
        columns[f"residue:{idx}:non_native"] = [float(slot["score"]) for slot in non_native]
        per_residue_meta.append({
            **_identity(residue),
            "native_score": native["score"],
            "native_class": native["class"],
            "alternative_class_burden": _class_burden(
                [slot["class"] for slot in non_native],
                denominator_kind="selected_residue_non_native_slots",
                denominator_count=len(AA_ORDER) - 1,
            ),
        })
        for slot in non_native:
            delta = float(slot["score"]) - float(native["score"])
            deltas.append(delta)
            alternatives.append({
                **_identity(residue), "mutation_aa": slot["mutation_aa"],
                "score": slot["score"], "score_class": slot["class"],
                "native_score": native["score"], "delta": delta,
            })
    columns["delta"] = deltas

    per_aa_meta: list[dict[str, Any]] = []
    for aa in AA_ORDER:
        aa_slots = [
            slot for residue in residues for slot in residue["slots"]
            if slot["mutation_aa"] == aa and not slot["native"]
        ]
        aa_denominator = sum(residue["wt"] != aa for residue in residues)
        columns[f"aa:{aa}"] = [float(slot["score"]) for slot in aa_slots]
        per_aa_meta.append({
            "mutation_aa": aa,
            "denominator_count": aa_denominator,
            "class_composition": _class_burden(
                [slot["class"] for slot in aa_slots],
                denominator_kind="applicable_non_native_amino_acid_slots",
                denominator_count=aa_denominator,
            ),
        })

    chain_groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    entity_groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for residue in residues:
        entity_key = (residue["entity_instance_id"], residue["source_entity_id"], residue["label_asym_id"])
        chain_key = (*entity_key, residue["auth_asym_id"], residue["pdb_chain_id"])
        entity_groups.setdefault(entity_key, []).append(residue)
        chain_groups.setdefault(chain_key, []).append(residue)
    sort_group = lambda item: tuple("" if value is None else value for value in item[0])
    sorted_chains = sorted(chain_groups.items(), key=sort_group)
    sorted_entities = sorted(entity_groups.items(), key=sort_group)

    per_chain_meta: list[dict[str, Any]] = []
    for idx, (key, group) in enumerate(sorted_chains):
        g_slots = [slot for residue in group for slot in residue["slots"]]
        g_native = [slot for slot in g_slots if slot["native"]]
        g_non_native = [slot for slot in g_slots if not slot["native"]]
        columns[f"chain:{idx}:all"] = [float(slot["score"]) for slot in g_slots]
        columns[f"chain:{idx}:native"] = [float(slot["score"]) for slot in g_native]
        columns[f"chain:{idx}:non_native"] = [float(slot["score"]) for slot in g_non_native]
        per_chain_meta.append({
            "entity_instance_id": key[0], "source_entity_id": key[1],
            "label_asym_id": key[2], "auth_asym_id": key[3], "pdb_chain_id": key[4],
            "support": _group_support(group),
        })

    per_entity_meta: list[dict[str, Any]] = []
    for idx, (key, group) in enumerate(sorted_entities):
        g_slots = [slot for residue in group for slot in residue["slots"]]
        g_native = [slot for slot in g_slots if slot["native"]]
        g_non_native = [slot for slot in g_slots if not slot["native"]]
        columns[f"entity:{idx}:all"] = [float(slot["score"]) for slot in g_slots]
        columns[f"entity:{idx}:native"] = [float(slot["score"]) for slot in g_native]
        columns[f"entity:{idx}:non_native"] = [float(slot["score"]) for slot in g_non_native]
        per_entity_meta.append({
            "entity_instance_id": key[0], "source_entity_id": key[1],
            "label_asym_id": key[2],
            "support": _group_support(group),
        })

    regions: list[dict[str, Any]] = []
    current, previous = None, None
    for residue in residues:
        native = next(slot for slot in residue["slots"] if slot["native"])
        same = (
            current is not None and previous is not None
            and residue["entity_instance_id"] == previous["entity_instance_id"]
            and residue["auth_asym_id"] == previous["auth_asym_id"]
            and residue["pdb_chain_id"] == previous["pdb_chain_id"]
            and residue["sequence_index"] == previous["sequence_index"] + 1
            and native["class"] == current["native_class"]
        )
        if not same:
            current = {
                "entity_instance_id": residue["entity_instance_id"],
                "source_entity_id": residue["source_entity_id"],
                "label_asym_id": residue["label_asym_id"],
                "auth_asym_id": residue["auth_asym_id"], "pdb_chain_id": residue["pdb_chain_id"],
                "native_class": native["class"], "start": _identity(residue),
                "end": _identity(residue), "length": 1,
            }
            regions.append(current)
        else:
            current["end"], current["length"] = _identity(residue), current["length"] + 1
        previous = residue

    aa_rank = {aa: index for index, aa in enumerate(AA_ORDER)}
    tie = lambda row: (_identity_key(row), aa_rank[row["mutation_aa"]])
    best = sorted(alternatives, key=lambda row: (-row["score"], *tie(row)))
    worst_source = sorted(alternatives, key=lambda row: (row["score"], *tie(row)))
    best = [{**row, "rank": rank} for rank, row in enumerate(best, 1)]
    worst = [{**row, "rank": rank} for rank, row in enumerate(worst_source, 1)]

    basis = _compatibility_basis(request, structure_map, capability_inventory, capability_inventory_bytes)
    class_burden = {
        "all": _class_burden(
            [slot["class"] for slot in all_slots],
            denominator_kind="selected_substitution_slots",
            denominator_count=expected_slots,
        ),
        "native": _class_burden(
            [slot["class"] for slot in native_slots],
            denominator_kind="selected_native_slots", denominator_count=count,
        ),
        "non_native": _class_burden(
            [slot["class"] for slot in non_native_slots],
            denominator_kind="selected_non_native_slots",
            denominator_count=count * (len(AA_ORDER) - 1),
        ),
    }
    statistics_schema_version = 2 if request["schema_version"] == 3 else 1
    metadata = {
        "schema_name": "frustrampnn_statistics",
        "schema_version": statistics_schema_version,
        "hash_semantics": "sha256(rfc8785(document_without_top_level_statistics_sha256))",
        "invocation_id": request["invocation_id"],
        "parent_job_id": request["parent_job_id"],
        "candidate_id": request["candidate_id"],
        "target_id": landscape["target_id"],
        "landscape_sha256": canonical_sha256(landscape),
        "source_artifact_sha256": request["source_artifact"]["sha256"],
        "normalized_pdb_sha256": request["normalized_pdb_sha256"],
        "structure_map": {
            "schema_name": structure_map["schema_name"],
            "schema_version": structure_map["schema_version"],
            "sha256": request["structure_map_sha256"],
        },
        "settings_sha256": request["requested_settings_sha256"],
        "effective_settings_sha256": request["effective_settings_sha256"],
        "capability_inventory_content_sha256": capability_inventory["content_sha256"],
        "capability_inventory_byte_sha256": request["capability_inventory_byte_sha256"],
        "configuration_sha256": request["execution_configuration_sha256"],
        "runtime_identity_sha256": request["runtime_identity_sha256"],
        "classification_policy_sha256": request["classification_policy_sha256"],
        "execution_plan_sha256": execution_receipt["command_plan"]["plan_sha256"],
        "output_contract_version": request["component_contract_version"],
        "canonical_amino_acid_order": AA_ORDER,
        "comparison_compatibility_basis": basis,
        "comparison_compatibility_id": comparison_compatibility_id(basis),
    }
    return {
        "metadata": metadata,
        "statistics_schema_version": statistics_schema_version,
        "count": count,
        "expected_slots": expected_slots,
        "support": support,
        "columns": columns,
        "per_residue_meta": per_residue_meta,
        "per_aa_meta": per_aa_meta,
        "per_chain_meta": per_chain_meta,
        "per_entity_meta": per_entity_meta,
        "regions": regions,
        "ranked_non_native_alternatives": {
            "support_count": len(alternatives),
            "omitted_count": (excluded_count + missing_count) * (len(AA_ORDER) - 1),
            "best_to_worst": best,
            "worst_to_best": worst,
        },
        "class_burden": class_burden,
    }


def _assemble_distributions_from_toolkit(
    ctx: Mapping[str, Any],
    summary_columns: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(summary_columns, Mapping):
        raise ContractValidationError("Stats Toolkit descriptive.summary output missing columns")
    count = int(ctx["count"])
    expected_slots = int(ctx["expected_slots"])

    def _col(name: str) -> Mapping[str, Any]:
        col = summary_columns.get(name)
        if not isinstance(col, Mapping):
            raise ContractValidationError(f"Stats Toolkit descriptive.summary missing column {name!r}")
        return col

    overall_distribution = _distribution_from_toolkit_column(
        _col("overall"),
        denominator_kind="selected_substitution_slots",
        denominator_count=expected_slots,
    )
    native_distribution = _distribution_from_toolkit_column(
        _col("native"),
        denominator_kind="selected_native_slots",
        denominator_count=count,
    )
    non_native_distribution = _distribution_from_toolkit_column(
        _col("non_native"),
        denominator_kind="selected_non_native_slots",
        denominator_count=count * (len(AA_ORDER) - 1),
    )
    delta_distribution = _distribution_from_toolkit_column(
        _col("delta"),
        denominator_kind="paired_native_non_native_slots",
        denominator_count=count * (len(AA_ORDER) - 1),
    )
    per_residue = [
        {
            **meta,
            "all": _distribution_from_toolkit_column(
                _col(f"residue:{idx}:all"),
                denominator_kind="selected_residue_substitution_slots",
                denominator_count=len(AA_ORDER),
            ),
            "non_native": _distribution_from_toolkit_column(
                _col(f"residue:{idx}:non_native"),
                denominator_kind="selected_residue_non_native_slots",
                denominator_count=len(AA_ORDER) - 1,
            ),
        }
        for idx, meta in enumerate(ctx["per_residue_meta"])
    ]
    per_aa = [
        {
            "mutation_aa": meta["mutation_aa"],
            "distribution": _distribution_from_toolkit_column(
                _col(f"aa:{meta['mutation_aa']}"),
                denominator_kind="applicable_non_native_amino_acid_slots",
                denominator_count=int(meta["denominator_count"]),
            ),
            "class_composition": meta["class_composition"],
        }
        for meta in ctx["per_aa_meta"]
    ]
    per_chain = []
    for idx, meta in enumerate(ctx["per_chain_meta"]):
        g_count = int(meta["support"]["selected_residue_count"])
        per_chain.append({
            **meta,
            "all": _distribution_from_toolkit_column(
                _col(f"chain:{idx}:all"),
                denominator_kind="selected_substitution_slots",
                denominator_count=g_count * len(AA_ORDER),
            ),
            "native": _distribution_from_toolkit_column(
                _col(f"chain:{idx}:native"),
                denominator_kind="selected_native_slots",
                denominator_count=g_count,
            ),
            "non_native": _distribution_from_toolkit_column(
                _col(f"chain:{idx}:non_native"),
                denominator_kind="selected_non_native_slots",
                denominator_count=g_count * (len(AA_ORDER) - 1),
            ),
        })
    per_entity = []
    for idx, meta in enumerate(ctx["per_entity_meta"]):
        g_count = int(meta["support"]["selected_residue_count"])
        per_entity.append({
            **meta,
            "all": _distribution_from_toolkit_column(
                _col(f"entity:{idx}:all"),
                denominator_kind="selected_substitution_slots",
                denominator_count=g_count * len(AA_ORDER),
            ),
            "native": _distribution_from_toolkit_column(
                _col(f"entity:{idx}:native"),
                denominator_kind="selected_native_slots",
                denominator_count=g_count,
            ),
            "non_native": _distribution_from_toolkit_column(
                _col(f"entity:{idx}:non_native"),
                denominator_kind="selected_non_native_slots",
                denominator_count=g_count * (len(AA_ORDER) - 1),
            ),
        })
    return {
        "distributions": {
            "overall": overall_distribution,
            "native": native_distribution,
            "non_native": non_native_distribution,
        },
        "per_residue": per_residue,
        "per_mutation_amino_acid": per_aa,
        "per_chain": per_chain,
        "per_entity": per_entity,
        "native_vs_alternative": {
            "native_mean": native_distribution["mean"],
            "alternative_mean": non_native_distribution["mean"],
            "alternative_minus_native": delta_distribution,
            "denominators": {
                "native_mean": native_distribution["denominators"]["mean"],
                "alternative_mean": non_native_distribution["denominators"]["mean"],
            },
            "missingness_reasons": {
                "native_mean": native_distribution["missingness_reasons"]["mean"],
                "alternative_mean": non_native_distribution["missingness_reasons"]["mean"],
            },
        },
    }


def build_statistics_receipt(
    *, request: Mapping[str, Any], execution_receipt: Mapping[str, Any],
    landscape: Mapping[str, Any], structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any], capability_inventory_bytes: bytes,
    analysis_receipt: Mapping[str, Any] | None = None,
    allow_legacy_external_authority: bool = False,
    stats_client: StatsToolkitClient | None = None,
) -> dict[str, Any]:
    """Build immutable statistics via one Stats Toolkit descriptive.summary durable run."""
    ctx = _extract_deterministic_receipt_context(
        request=request,
        execution_receipt=execution_receipt,
        landscape=landscape,
        structure_map=structure_map,
        capability_inventory=capability_inventory,
        capability_inventory_bytes=capability_inventory_bytes,
        allow_legacy_external_authority=allow_legacy_external_authority,
    )
    statistics_schema_version = ctx["statistics_schema_version"]
    if statistics_schema_version == 2 and not isinstance(analysis_receipt, Mapping):
        raise ContractValidationError(
            "v3 core statistics require an immutable analysis receipt"
        )
    if statistics_schema_version == 1 and analysis_receipt is not None:
        raise ContractValidationError(
            "historical statistics cannot carry successor analysis authority"
        )

    client = stats_client if stats_client is not None else _stats_toolkit_client.get_stats_toolkit_client()
    run_ref, outputs = client.run_tool(
        None,
        "descriptive.summary",
        {"columns": ctx["columns"]},
    )
    summary_columns = outputs.get("columns")
    assembled = _assemble_distributions_from_toolkit(ctx, summary_columns)

    payload: dict[str, Any] = {
        **ctx["metadata"],
        "support": ctx["support"],
        "distributions": assembled["distributions"],
        "per_residue": assembled["per_residue"],
        "per_mutation_amino_acid": assembled["per_mutation_amino_acid"],
        "per_chain": assembled["per_chain"],
        "per_entity": assembled["per_entity"],
        "native_vs_alternative": assembled["native_vs_alternative"],
        "contiguous_native_class_regions": ctx["regions"],
        "ranked_non_native_alternatives": ctx["ranked_non_native_alternatives"],
        "class_burden": ctx["class_burden"],
        "stats_run": {
            "run_id": str(run_ref["run_id"]),
            "workspace_id": str(run_ref["workspace_id"]),
            "tool_id": str(run_ref["tool_id"]),
            "tool_version": str(run_ref["tool_version"]),
            "method_version": run_ref.get("method_version"),
        },
    }
    if statistics_schema_version == 2:
        payload["analysis_receipt"] = copy.deepcopy(dict(analysis_receipt or {}))
    payload["statistics_sha256"] = _rfc8785_sha256(payload)
    validate_statistics_receipt(payload)
    return payload


def _scalars_match(recorded: Any, expected: Any, *, tol: float = 1e-12) -> bool:
    if recorded is None and expected is None:
        return True
    if recorded is None or expected is None:
        return False
    if isinstance(recorded, bool) or isinstance(expected, bool):
        return False
    if isinstance(recorded, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(recorded), float(expected), rel_tol=tol, abs_tol=tol)
    return recorded == expected


def _distribution_matches_toolkit_column(
    recorded: Mapping[str, Any],
    column: Mapping[str, Any],
) -> bool:
    if not isinstance(recorded, Mapping) or not isinstance(column, Mapping):
        return False
    if recorded.get("count") != column.get("count"):
        return False
    mapping = (
        ("mean", "mean"),
        ("median", "median"),
        ("sample_sd", "std"),
        ("min", "min"),
        ("max", "max"),
        ("q1", "q1"),
        ("q3", "q3"),
        ("iqr", "iqr"),
    )
    for rec_key, col_key in mapping:
        if not _scalars_match(recorded.get(rec_key), column.get(col_key)):
            return False
    return True


def verify_statistics_receipt_with_toolkit(
    receipt: Mapping[str, Any],
    *,
    stats_client: StatsToolkitClient | None = None,
    strict_mismatch: bool = True,
) -> dict[str, Any]:
    """Verify a receipt's statistics block read-only against its stored Stats Toolkit run.

    - Old receipts without stats_run are marked 'not rechecked' without recomputation.
    - New receipts with stats_run call read_run_outputs(run_id) (never creating a run).
    - If the Toolkit is unreachable, returns 'not rechecked' without failing.
    """
    stats_run = receipt.get("stats_run") if isinstance(receipt, Mapping) else None
    if not isinstance(stats_run, Mapping) or not str(stats_run.get("run_id") or "").strip():
        return {
            "status": "not_rechecked",
            "label": "not rechecked",
            "reason": "legacy_receipt_without_stats_run",
            "stats_run_id": None,
        }
    run_id = str(stats_run["run_id"]).strip()
    client = stats_client if stats_client is not None else _stats_toolkit_client.get_stats_toolkit_client()
    try:
        outputs = client.read_run_outputs(run_id)
    except StatsToolkitUnavailableError:
        return {
            "status": "not_rechecked",
            "label": "not rechecked",
            "reason": STATS_TOOLKIT_UNAVAILABLE_REASON,
            "stats_run_id": run_id,
        }
    except (StatsToolkitNotFoundError, StatsToolkitError) as exc:
        if strict_mismatch:
            raise ContractValidationError(
                f"stored Stats Toolkit run {run_id!r} could not be read: {exc}"
            ) from exc
        return {
            "status": "not_rechecked",
            "label": "not rechecked",
            "reason": "stats_run_not_found",
            "stats_run_id": run_id,
        }

    summary_columns = outputs.get("columns")
    if not isinstance(summary_columns, Mapping):
        if strict_mismatch:
            raise ContractValidationError("stored Stats Toolkit run output missing columns")
        return {
            "status": "mismatch",
            "label": "mismatch",
            "reason": "stats_toolkit_output_mismatch",
            "stats_run_id": run_id,
        }

    distributions = receipt.get("distributions") if isinstance(receipt.get("distributions"), Mapping) else {}
    native_vs_alt = (
        receipt.get("native_vs_alternative")
        if isinstance(receipt.get("native_vs_alternative"), Mapping)
        else {}
    )
    checks_ok = (
        _distribution_matches_toolkit_column(distributions.get("overall", {}), summary_columns.get("overall", {}))
        and _distribution_matches_toolkit_column(distributions.get("native", {}), summary_columns.get("native", {}))
        and _distribution_matches_toolkit_column(distributions.get("non_native", {}), summary_columns.get("non_native", {}))
        and _distribution_matches_toolkit_column(
            native_vs_alt.get("alternative_minus_native", {}), summary_columns.get("delta", {})
        )
        and _scalars_match(native_vs_alt.get("native_mean"), (summary_columns.get("native") or {}).get("mean"))
        and _scalars_match(native_vs_alt.get("alternative_mean"), (summary_columns.get("non_native") or {}).get("mean"))
    )

    if checks_ok:
        per_residue = receipt.get("per_residue") or []
        for idx, row in enumerate(per_residue):
            non_native_col = summary_columns.get(f"residue:{idx}:non_native")
            if not isinstance(non_native_col, Mapping):
                checks_ok = False
                break
            if not _distribution_matches_toolkit_column(row.get("non_native", {}), non_native_col):
                checks_ok = False
                break
            all_col = summary_columns.get(f"residue:{idx}:all")
            if isinstance(all_col, Mapping) and not _distribution_matches_toolkit_column(row.get("all", {}), all_col):
                checks_ok = False
                break

    if checks_ok:
        for row in receipt.get("per_mutation_amino_acid") or []:
            aa_col = summary_columns.get(f"aa:{row.get('mutation_aa')}")
            if isinstance(aa_col, Mapping) and not _distribution_matches_toolkit_column(row.get("distribution", {}), aa_col):
                checks_ok = False
                break

    if checks_ok:
        for idx, row in enumerate(receipt.get("per_chain") or []):
            for field, suffix in (("all", "all"), ("native", "native"), ("non_native", "non_native")):
                col = summary_columns.get(f"chain:{idx}:{suffix}")
                if isinstance(col, Mapping) and not _distribution_matches_toolkit_column(row.get(field, {}), col):
                    checks_ok = False
                    break
            if not checks_ok:
                break

    if checks_ok:
        for idx, row in enumerate(receipt.get("per_entity") or []):
            for field, suffix in (("all", "all"), ("native", "native"), ("non_native", "non_native")):
                col = summary_columns.get(f"entity:{idx}:{suffix}")
                if isinstance(col, Mapping) and not _distribution_matches_toolkit_column(row.get(field, {}), col):
                    checks_ok = False
                    break
            if not checks_ok:
                break

    if not checks_ok:
        if strict_mismatch:
            raise ContractValidationError("statistics receipt disagrees with stored Stats Toolkit run outputs")
        return {
            "status": "mismatch",
            "label": "mismatch",
            "reason": "stats_toolkit_output_mismatch",
            "stats_run_id": run_id,
        }
    return {
        "status": "verified",
        "label": "verified",
        "reason": None,
        "stats_run_id": run_id,
    }


def verify_statistics_receipt_against_bundle(
    *,
    statistics: Mapping[str, Any],
    statistics_bytes: bytes | None = None,
    request: Mapping[str, Any],
    execution_receipt: Mapping[str, Any],
    landscape: Mapping[str, Any],
    structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any],
    capability_inventory_bytes: bytes,
    allow_legacy_external_authority: bool = False,
    stats_client: StatsToolkitClient | None = None,
) -> dict[str, Any]:
    """Validate deterministic non-stats receipt fields locally and verify stats read-only via Toolkit."""
    validate_statistics_receipt(statistics)
    if statistics_bytes is not None and statistics_bytes != rfc8785.dumps(dict(statistics)):
        raise ContractValidationError("statistics receipt bytes are not canonical RFC 8785 JSON")

    ctx = _extract_deterministic_receipt_context(
        request=request,
        execution_receipt=execution_receipt,
        landscape=landscape,
        structure_map=structure_map,
        capability_inventory=capability_inventory,
        capability_inventory_bytes=capability_inventory_bytes,
        allow_legacy_external_authority=allow_legacy_external_authority,
    )
    for key, expected_val in ctx["metadata"].items():
        if statistics.get(key) != expected_val:
            raise ContractValidationError(f"statistics receipt metadata mismatch for {key}")
    if statistics.get("support") != ctx["support"]:
        raise ContractValidationError("statistics receipt support mismatch")
    if statistics.get("class_burden") != ctx["class_burden"]:
        raise ContractValidationError("statistics receipt class burden mismatch")
    if statistics.get("contiguous_native_class_regions") != ctx["regions"]:
        raise ContractValidationError("statistics receipt contiguous native class regions mismatch")
    if statistics.get("ranked_non_native_alternatives") != ctx["ranked_non_native_alternatives"]:
        raise ContractValidationError("statistics receipt ranked non-native alternatives mismatch")

    per_residue = statistics.get("per_residue")
    if not isinstance(per_residue, list) or len(per_residue) != len(ctx["per_residue_meta"]):
        raise ContractValidationError("statistics receipt per_residue cardinality mismatch")
    for actual, expected in zip(per_residue, ctx["per_residue_meta"]):
        for key, exp_val in expected.items():
            if actual.get(key) != exp_val:
                raise ContractValidationError(f"statistics receipt per_residue mismatch for {key}")

    per_aa = statistics.get("per_mutation_amino_acid")
    if not isinstance(per_aa, list) or len(per_aa) != len(ctx["per_aa_meta"]):
        raise ContractValidationError("statistics receipt per_mutation_amino_acid cardinality mismatch")
    for actual, expected in zip(per_aa, ctx["per_aa_meta"]):
        if actual.get("mutation_aa") != expected["mutation_aa"] or actual.get("class_composition") != expected["class_composition"]:
            raise ContractValidationError("statistics receipt per_mutation_amino_acid metadata mismatch")

    per_chain = statistics.get("per_chain")
    if not isinstance(per_chain, list) or len(per_chain) != len(ctx["per_chain_meta"]):
        raise ContractValidationError("statistics receipt per_chain cardinality mismatch")
    for actual, expected in zip(per_chain, ctx["per_chain_meta"]):
        for key, exp_val in expected.items():
            if actual.get(key) != exp_val:
                raise ContractValidationError(f"statistics receipt per_chain mismatch for {key}")

    per_entity = statistics.get("per_entity")
    if not isinstance(per_entity, list) or len(per_entity) != len(ctx["per_entity_meta"]):
        raise ContractValidationError("statistics receipt per_entity cardinality mismatch")
    for actual, expected in zip(per_entity, ctx["per_entity_meta"]):
        for key, exp_val in expected.items():
            if actual.get(key) != exp_val:
                raise ContractValidationError(f"statistics receipt per_entity mismatch for {key}")

    return verify_statistics_receipt_with_toolkit(
        statistics,
        stats_client=stats_client,
        strict_mismatch=True,
    )


def validate_statistics_receipt(receipt: Mapping[str, Any]) -> None:
    if not isinstance(receipt, Mapping):
        raise ContractValidationError("statistics receipt must be an object")
    payload = copy.deepcopy(dict(receipt))
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        raise ContractValidationError("statistics schema version is unsupported")
    validate_schema(f"frustrampnn_statistics_v{schema_version}", payload)
    recorded = payload.pop("statistics_sha256")
    if recorded != _rfc8785_sha256(payload):
        raise ContractValidationError("statistics SHA-256 does not match receipt content")
    if receipt["comparison_compatibility_id"] != comparison_compatibility_id(
        receipt["comparison_compatibility_basis"]
    ):
        raise ContractValidationError("comparison compatibility ID does not match basis")

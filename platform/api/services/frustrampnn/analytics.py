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


def statistics_distribution(
    values: Sequence[int | float], *, denominator_kind: str = "input_values",
    denominator_count: int | None = None, missingness_reason: str | None = None,
) -> dict[str, Any]:
    """Type-7 quartiles and sample SD over finite scoreable values.

    ``sample_sd`` is null for n < 2. Every scalar names its arithmetic
    denominator and records why support is absent or partial.
    """
    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractValidationError("statistics distribution values must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ContractValidationError("statistics distribution values must be finite")
        normalized.append(number)
    normalized.sort()
    count = len(normalized)
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

    def percentile(p: float) -> float:
        h = (count - 1) * p
        lower, upper = math.floor(h), math.ceil(h)
        return normalized[lower] if lower == upper else normalized[lower] + (
            h - lower
        ) * (normalized[upper] - normalized[lower])

    mean = math.fsum(normalized) / count
    q1, q3 = percentile(0.25), percentile(0.75)
    return {
        "count": count, "mean": mean, "median": percentile(0.5),
        "sample_sd": math.sqrt(
            math.fsum((value - mean) ** 2 for value in normalized) / (count - 1)
        ) if count >= 2 else None,
        "min": normalized[0], "max": normalized[-1], "q1": q1, "q3": q3,
        "iqr": q3 - q1,
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


def _group_summary(residues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    slots = [slot for residue in residues for slot in residue["slots"]]
    native = [slot for slot in slots if slot["native"]]
    non_native = [slot for slot in slots if not slot["native"]]
    count = len(residues)
    return {
        "support": {
            "selected_residue_count": count, "observed_residue_count": count,
            "fully_scoreable_residue_count": count,
            "expected_slot_count": count * len(AA_ORDER),
            "observed_slot_count": len(slots), "scoreable_slot_count": len(slots),
        },
        "all": statistics_distribution(
            [slot["score"] for slot in slots],
            denominator_kind="selected_substitution_slots", denominator_count=count * len(AA_ORDER),
        ),
        "native": statistics_distribution(
            [slot["score"] for slot in native],
            denominator_kind="selected_native_slots", denominator_count=count,
        ),
        "non_native": statistics_distribution(
            [slot["score"] for slot in non_native],
            denominator_kind="selected_non_native_slots",
            denominator_count=count * (len(AA_ORDER) - 1),
        ),
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


def build_statistics_receipt(
    *, request: Mapping[str, Any], execution_receipt: Mapping[str, Any],
    landscape: Mapping[str, Any], structure_map: Mapping[str, Any],
    capability_inventory: Mapping[str, Any], capability_inventory_bytes: bytes,
    analysis_receipt: Mapping[str, Any] | None = None,
    allow_legacy_external_authority: bool = False,
) -> dict[str, Any]:
    """Build immutable statistics from complete physical v2 authority."""
    _validate_statistics_inputs(
        request=request, execution_receipt=execution_receipt, landscape=landscape,
        structure_map=structure_map, capability_inventory=capability_inventory,
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
    per_residue, alternatives, deltas = [], [], []
    for residue in residues:
        native = next(slot for slot in residue["slots"] if slot["native"])
        non_native = [slot for slot in residue["slots"] if not slot["native"]]
        per_residue.append({
            **_identity(residue), "native_score": native["score"],
            "native_class": native["class"],
            "all": statistics_distribution(
                [slot["score"] for slot in residue["slots"]],
                denominator_kind="selected_residue_substitution_slots",
                denominator_count=len(AA_ORDER),
            ),
            "non_native": statistics_distribution(
                [slot["score"] for slot in non_native],
                denominator_kind="selected_residue_non_native_slots",
                denominator_count=len(AA_ORDER) - 1,
            ),
            "alternative_class_burden": _class_burden(
                [slot["class"] for slot in non_native],
                denominator_kind="selected_residue_non_native_slots",
                denominator_count=len(AA_ORDER) - 1,
            ),
        })
        for slot in non_native:
            delta = slot["score"] - native["score"]
            deltas.append(delta)
            alternatives.append({
                **_identity(residue), "mutation_aa": slot["mutation_aa"],
                "score": slot["score"], "score_class": slot["class"],
                "native_score": native["score"], "delta": delta,
            })
    per_aa = []
    for aa in AA_ORDER:
        aa_slots = [
            slot for residue in residues for slot in residue["slots"]
            if slot["mutation_aa"] == aa and not slot["native"]
        ]
        aa_denominator = sum(residue["wt"] != aa for residue in residues)
        per_aa.append({
            "mutation_aa": aa,
            "distribution": statistics_distribution(
                [slot["score"] for slot in aa_slots],
                denominator_kind="applicable_non_native_amino_acid_slots",
                denominator_count=aa_denominator,
            ),
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
    per_chain = [{
        "entity_instance_id": key[0], "source_entity_id": key[1],
        "label_asym_id": key[2], "auth_asym_id": key[3], "pdb_chain_id": key[4],
        **_group_summary(group),
    } for key, group in sorted(chain_groups.items(), key=sort_group)]
    per_entity = [{
        "entity_instance_id": key[0], "source_entity_id": key[1],
        "label_asym_id": key[2], **_group_summary(group),
    } for key, group in sorted(entity_groups.items(), key=sort_group)]

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
    overall_distribution = statistics_distribution(
        [slot["score"] for slot in all_slots],
        denominator_kind="selected_substitution_slots", denominator_count=expected_slots,
    )
    native_distribution = statistics_distribution(
        [slot["score"] for slot in native_slots],
        denominator_kind="selected_native_slots", denominator_count=count,
    )
    non_native_distribution = statistics_distribution(
        [slot["score"] for slot in non_native_slots],
        denominator_kind="selected_non_native_slots",
        denominator_count=count * (len(AA_ORDER) - 1),
    )
    delta_distribution = statistics_distribution(
        deltas, denominator_kind="paired_native_non_native_slots",
        denominator_count=count * (len(AA_ORDER) - 1),
    )
    statistics_schema_version = 2 if request["schema_version"] == 3 else 1
    if statistics_schema_version == 2 and not isinstance(analysis_receipt, Mapping):
        raise ContractValidationError(
            "v3 core statistics require an immutable analysis receipt"
        )
    if statistics_schema_version == 1 and analysis_receipt is not None:
        raise ContractValidationError(
            "historical statistics cannot carry successor analysis authority"
        )
    payload: dict[str, Any] = {
        "schema_name": "frustrampnn_statistics",
        "schema_version": statistics_schema_version,
        "hash_semantics": "sha256(rfc8785(document_without_top_level_statistics_sha256))",
        "invocation_id": request["invocation_id"], "parent_job_id": request["parent_job_id"],
        "candidate_id": request["candidate_id"], "target_id": landscape["target_id"],
        "landscape_sha256": canonical_sha256(landscape),
        "source_artifact_sha256": request["source_artifact"]["sha256"],
        "normalized_pdb_sha256": request["normalized_pdb_sha256"],
        "structure_map": {"schema_name": structure_map["schema_name"],
            "schema_version": structure_map["schema_version"], "sha256": request["structure_map_sha256"]},
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
        "support": support,
        "distributions": {
            "overall": overall_distribution,
            "native": native_distribution,
            "non_native": non_native_distribution,
        },
        "per_residue": per_residue, "per_mutation_amino_acid": per_aa,
        "per_chain": per_chain, "per_entity": per_entity,
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
        "contiguous_native_class_regions": regions,
        "ranked_non_native_alternatives": {
            "support_count": len(alternatives),
            "omitted_count": (excluded_count + missing_count) * (len(AA_ORDER) - 1),
            "best_to_worst": best, "worst_to_best": worst,
        },
        "class_burden": {
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
        },
    }
    if statistics_schema_version == 2:
        payload["analysis_receipt"] = copy.deepcopy(dict(analysis_receipt or {}))
    payload["statistics_sha256"] = _rfc8785_sha256(payload)
    validate_statistics_receipt(payload)
    return payload


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

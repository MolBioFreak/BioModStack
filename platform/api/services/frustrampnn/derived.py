"""Database materialization for immutable FrustraMPNN comparisons and guidance."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    FrustraMPNNComparison,
    FrustraMPNNComparisonRow,
    FrustraMPNNGuidancePlan,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
)

from .contracts import canonical_sha256


class DerivedPersistenceError(ValueError):
    """Raised when a derived artifact is missing or conflicts with immutable state."""


def _result_landscape_metadata(result: FrustraMPNNResult, first_row: FrustraMPNNLandscapeRow) -> dict[str, Any]:
    summary = dict(result.summary_json or {})
    provenance = dict(first_row.provenance_json or {})
    policy = provenance.get("threshold_policy") or summary.get("threshold_policy")
    if isinstance(policy, dict) and "policy_id" in policy and "id" not in policy:
        policy = {"id": policy["policy_id"], **{key: value for key, value in policy.items() if key != "policy_id"}}
    return {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 1,
        "configuration_id": summary.get("configuration_id"),
        "configuration_sha256": summary.get("configuration_sha256"),
        "target_id": first_row.target_id,
        "parent_job_id": result.parent_job_id,
        "candidate_id": result.candidate_id,
        "structure_map_sha256": provenance.get("structure_map_sha256"),
        "normalized_pdb_sha256": provenance.get("normalized_pdb_sha256"),
        "model_ready_sequence_sha256": summary.get("model_ready_sequence_sha256"),
        "raw_csv_sha256": provenance.get("raw_csv_sha256"),
        "threshold_policy": policy,
        "threshold_policy_sha256": provenance.get("threshold_policy_sha256") or summary.get("threshold_policy_sha256"),
    }


async def load_persisted_landscape(session: AsyncSession, result: FrustraMPNNResult) -> dict[str, Any]:
    """Reconstruct a canonical landscape from immutable persisted rows."""
    rows = (
        await session.execute(
            select(FrustraMPNNLandscapeRow)
            .where(
                FrustraMPNNLandscapeRow.parent_job_id == result.parent_job_id,
                FrustraMPNNLandscapeRow.invocation_id == result.invocation_id,
            )
            .order_by(
                FrustraMPNNLandscapeRow.entity_instance_id.asc(),
                FrustraMPNNLandscapeRow.sequence_index.asc(),
                FrustraMPNNLandscapeRow.mutation_aa.asc(),
                FrustraMPNNLandscapeRow.id.asc(),
            )
        )
    ).scalars().all()
    if not rows:
        raise DerivedPersistenceError("persisted FrustraMPNN landscape rows are missing")
    metadata = _result_landscape_metadata(result, rows[0])
    grouped: OrderedDict[tuple[str, str, int, str, int], dict[str, Any]] = OrderedDict()
    for row in rows:
        stored = dict(row.row_json or {})
        residue = dict(stored.get("residue") or {})
        residue.setdefault("entity_instance_id", row.entity_instance_id)
        residue.setdefault("source_entity_id", None)
        residue.setdefault("label_asym_id", None)
        residue.setdefault("auth_asym_id", row.auth_asym_id)
        residue.setdefault("label_seq_id", None)
        residue.setdefault("auth_seq_id", int(row.auth_seq_id))
        residue.setdefault("insertion_code", row.insertion_code or "")
        residue.setdefault("sequence_index", row.sequence_index)
        residue.setdefault("pdb_chain_id", row.auth_asym_id)
        residue.setdefault("pdb_residue_id", int(row.auth_seq_id))
        residue.setdefault("pdb_insertion_code", row.insertion_code or "")
        residue.setdefault("model_position", max(int(row.sequence_index) - 1, 0))
        residue.setdefault("residue_name", "UNK")
        residue.setdefault("wt", row.wt)
        key = (
            row.entity_instance_id,
            row.auth_asym_id,
            int(row.auth_seq_id),
            row.insertion_code or "",
            int(row.sequence_index),
        )
        if key not in grouped:
            residue["slots"] = []
            grouped[key] = residue
        slot = dict(stored.get("slot") or {})
        slot.setdefault("mutation_aa", row.mutation_aa)
        slot.setdefault("score", row.score)
        slot.setdefault("class", row.score_class)
        slot.setdefault("scoreable", row.scoreable)
        slot.setdefault("status", row.status)
        slot.setdefault("reason", row.reason)
        slot.setdefault("native", row.mutation_aa == row.wt)
        grouped[key]["slots"].append(slot)
    landscape = {**metadata, "residues": list(grouped.values())}
    landscape["landscape_sha256"] = canonical_sha256(landscape)
    return landscape


def _require_id(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise DerivedPersistenceError(f"{field} is required for immutable persistence")
    return value


def _content_hash(payload: Mapping[str, Any], field: str) -> str:
    body = dict(payload)
    supplied = body.pop(field, None)
    observed = canonical_sha256(body)
    if supplied != observed:
        raise DerivedPersistenceError(f"{field} does not match immutable payload content")
    return observed


async def persist_comparison(
    session: AsyncSession,
    payload: Mapping[str, Any],
    *,
    reference_result: FrustraMPNNResult,
    target_result: FrustraMPNNResult,
) -> FrustraMPNNComparison:
    comparison_id = _require_id(payload, "comparison_id")
    comparison_sha256 = _content_hash(payload, "comparison_sha256")
    existing = await session.get(FrustraMPNNComparison, comparison_id)
    if existing is not None:
        if existing.comparison_sha256 != comparison_sha256:
            raise DerivedPersistenceError("immutable comparison conflict")
        return existing
    comparability = dict(payload.get("comparability") or {})
    model = FrustraMPNNComparison(
        comparison_id=comparison_id,
        reference_parent_job_id=reference_result.parent_job_id,
        reference_invocation_id=reference_result.invocation_id,
        target_parent_job_id=target_result.parent_job_id,
        target_invocation_id=target_result.invocation_id,
        reference_landscape_sha256=str(payload["reference_landscape_sha256"]),
        target_landscape_sha256=str(payload["target_landscape_sha256"]),
        configuration_id=payload.get("configuration_id"),
        configuration_sha256=payload.get("configuration_sha256"),
        status=str(comparability.get("status") or "review"),
        comparison_sha256=comparison_sha256,
        payload_json=dict(payload),
    )
    session.add(model)
    await session.flush()
    for index, row in enumerate(payload.get("rows") or []):
        identity = dict(row.get("residue_key") or {})
        reference = dict(row.get("reference") or {})
        target = dict(row.get("target") or {})
        session.add(FrustraMPNNComparisonRow(
            id=canonical_sha256(["frustrampnn-comparison-row-v1", comparison_id, index]),
            comparison_id=comparison_id,
            row_index=index,
            entity_instance_id=str(identity.get("entity_instance_id") or "unknown"),
            auth_asym_id=str(identity.get("auth_asym_id") or ""),
            auth_seq_id=str(identity.get("auth_seq_id")),
            insertion_code=str(identity.get("insertion_code") or ""),
            sequence_index=row.get("sequence_index"),
            mutation_aa=str(row.get("mutation_aa")),
            mapping_state=str(row.get("mapping_state")),
            missingness_state=str(row.get("missingness_state")),
            biological_status=str(row.get("biological_status")),
            reference_score=reference.get("score"),
            target_score=target.get("score"),
            raw_score_delta=row.get("raw_score_delta"),
            reference_class=reference.get("class"),
            target_class=target.get("class"),
            classification_transition=row.get("classification_transition"),
            row_json=dict(row),
        ))
    return model


async def persist_guidance_plan(
    session: AsyncSession,
    payload: Mapping[str, Any],
    *,
    source_result: FrustraMPNNResult | None = None,
) -> FrustraMPNNGuidancePlan:
    guidance_id = _require_id(payload, "guidance_id")
    guidance_sha256 = _content_hash(payload, "guidance_sha256")
    existing = await session.get(FrustraMPNNGuidancePlan, guidance_id)
    if existing is not None:
        if existing.guidance_sha256 != guidance_sha256:
            raise DerivedPersistenceError("immutable guidance conflict")
        return existing
    model = FrustraMPNNGuidancePlan(
        guidance_id=guidance_id,
        source_landscape_sha256=str(payload["source_landscape_sha256"]),
        source_comparison_id=payload.get("source_comparison_id"),
        source_parent_job_id=source_result.parent_job_id if source_result else None,
        source_invocation_id=source_result.invocation_id if source_result else None,
        configuration_id=payload.get("configuration_id"),
        configuration_sha256=payload.get("configuration_sha256"),
        guidance_sha256=guidance_sha256,
        payload_json=dict(payload),
    )
    session.add(model)
    await session.flush()
    return model


__all__ = [
    "DerivedPersistenceError",
    "load_persisted_landscape",
    "persist_comparison",
    "persist_guidance_plan",
]

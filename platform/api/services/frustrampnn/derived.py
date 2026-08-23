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
    FrustraMPNNResult,
)

from .contracts import canonical_sha256, validate_schema
from .persistence import FrustraMPNNPersistenceError, landscape_page
from services.scientific_artifacts import publish_json_payload


class DerivedPersistenceError(ValueError):
    """Raised when a derived artifact is missing or conflicts with immutable state."""


def _result_landscape_metadata(
    result: FrustraMPNNResult, first_row: Mapping[str, Any]
) -> dict[str, Any]:
    summary = dict(result.summary_json or {})
    provenance = dict(first_row["provenance"] or {})
    policy = provenance.get("threshold_policy") or summary.get("threshold_policy")
    schema_version = 2 if summary.get("schema_version") == 2 else 1
    common = {
        "schema_name": "frustrampnn_landscape",
        "schema_version": schema_version,
        "target_id": first_row["target_id"],
        "parent_job_id": result.parent_job_id,
        "candidate_id": result.candidate_id,
        "structure_map_sha256": (
            summary.get("structure_map_sha256")
            or provenance.get("structure_map_sha256")
        ),
        "normalized_pdb_sha256": (
            summary.get("normalized_pdb_sha256")
            or provenance.get("normalized_pdb_sha256")
        ),
        "raw_csv_sha256": provenance.get("raw_csv_sha256"),
        "threshold_policy": policy,
        "threshold_policy_sha256": (
            summary.get("threshold_policy_sha256")
            or provenance.get("threshold_policy_sha256")
        ),
    }
    if schema_version == 2:
        return {
            **common,
            "execution_configuration_id": summary.get(
                "execution_configuration_id"
            ),
            "execution_configuration_sha256": summary.get(
                "execution_configuration_sha256"
            ),
            "requested_settings_sha256": summary.get("requested_settings_sha256"),
            "effective_settings_sha256": summary.get("effective_settings_sha256"),
            "runtime_identity_sha256": summary.get("runtime_identity_sha256"),
            "source_artifact_sha256": (
                summary.get("source_artifact_sha256")
                or result.source_artifact_sha256
            ),
            "threshold_policy_id": summary.get("threshold_policy_id"),
        }
    if isinstance(policy, dict) and "policy_id" in policy and "id" not in policy:
        common["threshold_policy"] = {
            "id": policy["policy_id"],
            **{key: value for key, value in policy.items() if key != "policy_id"},
        }
    return {
        **common,
        "configuration_id": summary.get("configuration_id"),
        "configuration_sha256": summary.get("configuration_sha256"),
        "model_ready_sequence_sha256": summary.get("model_ready_sequence_sha256"),
    }


async def load_persisted_landscape(
    session: AsyncSession, result: FrustraMPNNResult
) -> dict[str, Any]:
    """Reconstruct a canonical landscape through bounded verified artifact pages."""
    try:
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
                raise DerivedPersistenceError(
                    "persisted FrustraMPNN landscape artifact paging is incomplete"
                )
            rows.extend(next_page["items"])
    except FrustraMPNNPersistenceError as exc:
        raise DerivedPersistenceError(str(exc)) from exc
    if not rows:
        raise DerivedPersistenceError("persisted FrustraMPNN landscape rows are missing")
    metadata = _result_landscape_metadata(result, rows[0])
    grouped: OrderedDict[tuple[str, str, int, str, int], dict[str, Any]] = OrderedDict()
    for row in rows:
        stored = dict(row["row"] or {})
        residue = dict(stored.get("residue") or {})
        residue.setdefault("entity_instance_id", row["entity_instance_id"])
        residue.setdefault("source_entity_id", None)
        residue.setdefault("label_asym_id", None)
        residue.setdefault("auth_asym_id", row["auth_asym_id"])
        residue.setdefault("label_seq_id", None)
        residue.setdefault("auth_seq_id", int(row["auth_seq_id"]))
        residue.setdefault("insertion_code", row["insertion_code"] or "")
        residue.setdefault("sequence_index", row["sequence_index"])
        residue.setdefault("pdb_chain_id", row["auth_asym_id"])
        residue.setdefault("pdb_residue_id", int(row["auth_seq_id"]))
        residue.setdefault("pdb_insertion_code", row["insertion_code"] or "")
        residue.setdefault("model_position", max(int(row["sequence_index"]) - 1, 0))
        residue.setdefault("residue_name", "UNK")
        residue.setdefault("wt", row["wt"])
        key = (
            row["entity_instance_id"],
            row["auth_asym_id"],
            int(row["auth_seq_id"]),
            row["insertion_code"] or "",
            int(row["sequence_index"]),
        )
        if key not in grouped:
            residue["slots"] = []
            grouped[key] = residue
        slot = dict(stored.get("slot") or {})
        slot.setdefault("mutation_aa", row["mutation_aa"])
        slot.setdefault("score", row["score"])
        slot.setdefault("class", row["score_class"])
        slot.setdefault("scoreable", row["scoreable"])
        slot.setdefault("status", row["status"])
        slot.setdefault("reason", row["reason"])
        slot.setdefault("native", row["mutation_aa"] == row["wt"])
        grouped[key]["slots"].append(slot)
    landscape = {**metadata, "residues": list(grouped.values())}
    persisted_hashes = {
        str(value)
        for row in rows
        if (value := dict(row["provenance"] or {}).get("landscape_sha256"))
    }
    summary_hash = dict(result.summary_json or {}).get("landscape_sha256")
    if summary_hash:
        persisted_hashes.add(str(summary_hash))
    if len(persisted_hashes) > 1:
        raise DerivedPersistenceError(
            "persisted FrustraMPNN landscape identity is inconsistent"
        )
    landscape["landscape_sha256"] = (
        next(iter(persisted_hashes))
        if persisted_hashes
        else canonical_sha256(landscape)
    )
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


def _validate_multistate_persistence(
    payload: Mapping[str, Any],
    *,
    reference_result: FrustraMPNNResult,
    target_result: FrustraMPNNResult,
) -> None:
    """Enforce target-parallel cardinality, order, and persisted identities."""

    try:
        validate_schema("frustrampnn_multistate_comparison_v1", payload)
    except Exception as exc:
        raise DerivedPersistenceError(
            f"multistate comparison schema validation failed: {exc}"
        ) from exc

    def parallel(name: str, target_count: int) -> list[Any]:
        value = payload.get(name)
        if not isinstance(value, list) or len(value) != target_count:
            raise DerivedPersistenceError(
                f"multistate target cardinality mismatch for {name}"
            )
        return value

    comparability = payload.get("comparability")
    summary = payload.get("summary")
    if not isinstance(comparability, Mapping) or not isinstance(summary, Mapping):
        raise DerivedPersistenceError("multistate comparison cardinality authority is missing")
    target_count = comparability.get("target_count")
    if (
        isinstance(target_count, bool)
        or not isinstance(target_count, int)
        or not 1 <= target_count <= 8
        or summary.get("target_count") != target_count
    ):
        raise DerivedPersistenceError(
            "multistate target_count must be 1..8 and equal in comparability and summary"
        )

    target_labels = parallel("target_labels", target_count)
    expected_labels = [f"target-{index:04d}" for index in range(1, target_count + 1)]
    if target_labels != expected_labels:
        raise DerivedPersistenceError("multistate target label order is not canonical")
    target_landscapes = parallel("target_landscape_sha256s", target_count)
    target_configurations = parallel("target_configuration_sha256s", target_count)
    pair_compatibility = parallel("pair_compatibility", target_count)
    comparability_pairs = comparability.get("pair_compatibility")
    if not isinstance(comparability_pairs, list) or comparability_pairs != pair_compatibility:
        raise DerivedPersistenceError(
            "multistate pair compatibility cardinality/order is inconsistent"
        )
    if payload.get("target_landscape_sha256") != target_landscapes[0]:
        raise DerivedPersistenceError(
            "multistate first target landscape does not match target_landscape_sha256"
        )
    for index, pair in enumerate(pair_compatibility):
        if not isinstance(pair, Mapping) or (
            pair.get("target_label") != target_labels[index]
            or pair.get("target_landscape_sha256") != target_landscapes[index]
            or pair.get("target_configuration_sha256") != target_configurations[index]
        ):
            raise DerivedPersistenceError(
                "multistate pair compatibility target order/binding is inconsistent"
            )

    references = payload.get("source_result_references")
    if not isinstance(references, list) or len(references) != target_count + 1:
        raise DerivedPersistenceError(
            "multistate target reference cardinality must equal target_count plus reference"
        )
    reference = references[0]
    if not isinstance(reference, Mapping) or (
        reference.get("role") != "reference"
        or reference.get("target_label") is not None
        or reference.get("parent_job_id") != reference_result.parent_job_id
        or reference.get("invocation_id") != reference_result.invocation_id
        or reference.get("landscape_sha256") != payload.get("reference_landscape_sha256")
        or reference.get("configuration_sha256")
        != payload.get("reference_configuration_sha256")
    ):
        raise DerivedPersistenceError(
            "multistate reference identity/order does not match persisted reference authority"
        )
    reference_identity = (reference.get("parent_job_id"), reference.get("invocation_id"))
    target_identities: list[tuple[Any, Any]] = []
    for index, target_reference in enumerate(references[1:]):
        if not isinstance(target_reference, Mapping) or (
            target_reference.get("role") != "target"
            or target_reference.get("target_label") != target_labels[index]
            or target_reference.get("landscape_sha256") != target_landscapes[index]
            or target_reference.get("configuration_sha256") != target_configurations[index]
        ):
            raise DerivedPersistenceError(
                "multistate target reference order/binding is inconsistent"
            )
        identity = (
            target_reference.get("parent_job_id"),
            target_reference.get("invocation_id"),
        )
        if identity == reference_identity:
            raise DerivedPersistenceError("multistate target must not equal its reference")
        target_identities.append(identity)
    if len(target_identities) != len(set(target_identities)):
        raise DerivedPersistenceError("multistate target references contain duplicates")
    if target_identities[0] != (
        target_result.parent_job_id,
        target_result.invocation_id,
    ):
        raise DerivedPersistenceError(
            "multistate first target identity does not match persistence target authority"
        )

    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise DerivedPersistenceError("multistate rows are missing")
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise DerivedPersistenceError(f"multistate row {row_index} is not an object")
        for field in (
            "targets",
            "raw_score_deltas",
            "classification_transitions",
            "missingness_by_target",
        ):
            value = row.get(field)
            if not isinstance(value, list) or len(value) != target_count:
                raise DerivedPersistenceError(
                    f"multistate row {row_index} target cardinality mismatch for {field}"
                )


async def persist_comparison(
    session: AsyncSession,
    payload: Mapping[str, Any],
    *,
    reference_result: FrustraMPNNResult,
    target_result: FrustraMPNNResult,
) -> FrustraMPNNComparison:
    if payload.get("comparison_mode") == "multi_state":
        _validate_multistate_persistence(
            payload,
            reference_result=reference_result,
            target_result=target_result,
        )
    comparison_id = _require_id(payload, "comparison_id")
    comparison_sha256 = _content_hash(payload, "comparison_sha256")
    existing = await session.get(FrustraMPNNComparison, comparison_id)
    if existing is not None:
        if existing.comparison_sha256 != comparison_sha256:
            raise DerivedPersistenceError("immutable comparison conflict")
        return existing
    comparability = dict(payload.get("comparability") or {})
    payload_reference = await publish_json_payload(
        session,
        owner_kind="frustrampnn_comparison",
        owner_id=comparison_id,
        role="payload",
        schema_id="bms.frustrampnn-comparison.v1",
        payload=dict(payload),
        source_sha256=comparison_sha256,
    )
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
        payload_json=payload_reference,
    )
    session.add(model)
    await session.flush()
    for index, row in enumerate(payload.get("rows") or []):
        identity = dict(row.get("residue_key") or {})
        reference = dict(row.get("reference") or {})
        target = dict(row.get("target") or {})
        if not target and isinstance(row.get("targets"), list) and row["targets"]:
            target = dict(row["targets"][0] or {})
        raw_score_delta = row.get("raw_score_delta")
        if raw_score_delta is None and isinstance(row.get("raw_score_deltas"), list) and row["raw_score_deltas"]:
            raw_score_delta = row["raw_score_deltas"][0]
        classification_transition = row.get("classification_transition")
        if classification_transition is None and isinstance(row.get("classification_transitions"), list) and row["classification_transitions"]:
            classification_transition = row["classification_transitions"][0]
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
            raw_score_delta=raw_score_delta,
            reference_class=reference.get("class"),
            target_class=target.get("class"),
            classification_transition=classification_transition,
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

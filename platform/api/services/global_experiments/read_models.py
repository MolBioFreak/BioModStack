"""Bounded server-owned Project Manager presentation read model."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResearchRecord,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
    ExperimentWorkflowSetupContext,
)
from experiment_services import (
    NotFound,
    ValidationFailure,
    canonical_json,
    public_workflow_payload,
    sha256_text,
)
from services.global_experiments.result_surfaces import result_surface_for_receipt
from services.protein_project_capabilities import protein_capability_record


MAX_TREE_NODES = 1_000
MAX_MAP_NODES = 100
MAX_PAGE_ITEMS = 100
DEFAULT_MAP_NODES = 50
DEFAULT_RUNS = 25
DEFAULT_PAGE_ITEMS = 25
VIRTUAL_FOLDERS = ("plans", "runs", "results", "datasets", "notes", "decisions", "activity")
ATTACHMENT_MODES = ("references", "uses_input", "produced", "validated_by")
RESULT_ATTACHMENT_MODES = ("produced",)
SOURCE_REVERIFICATION_TTL = timedelta(hours=24)
SOURCE_REVERIFICATION_FUTURE_SKEW = timedelta(minutes=5)
MAX_REVERIFICATION_SCAN_ROWS = 10_000
SOURCE_REVERIFICATION_RECEIPT_KEYS = frozenset({
    "schema",
    "reverification_receipt_id",
    "project_id",
    "global_experiment_id",
    "domain_experiment_id",
    "adapter_id",
    "adapter_version",
    "source_receipt_id",
    "source_digest",
    "verified_at",
    "valid_until",
    "normalized_request_sha256",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _payload(session: AsyncSession, head: ExperimentAggregateHead) -> dict[str, Any]:
    if head.current_revision_id is None:
        return {}
    revision = await session.get(ExperimentRevision, head.current_revision_id)
    return json.loads(revision.canonical_payload) if revision is not None else {}


def _key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def _tree_node(
    *,
    node_key: str,
    node_type: str,
    subject_id: str | None,
    parent_node_key: str | None,
    label: str,
    lifecycle_state: str | None,
    counts: dict[str, int] | None = None,
    has_children: bool = False,
    allowed_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_key": node_key,
        "node_type": node_type,
        "subject_id": subject_id,
        "parent_node_key": parent_node_key,
        "label": label,
        "lifecycle_state": lifecycle_state,
        "counts": counts or {},
        "has_children": has_children,
        "allowed_actions": allowed_actions or [],
    }


def _head_summary(head: ExperimentAggregateHead, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": head.aggregate_id,
        "project_scope": payload.get("project_scope", "global"),
        "name": str(payload.get("name") or head.display_name),
        "objective": str(payload.get("research_objective") or payload.get("objective") or ""),
        "lifecycle_state": head.lifecycle_state,
        "head_generation": head.head_generation,
        "current_revision_id": head.current_revision_id,
        "updated_at": head.updated_at,
    }


def _encode_cursor(family: str, created_at: str, identity: str) -> str:
    raw = canonical_json([created_at, identity]).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{family}:{token}"


def _decode_cursor(cursor: str | None, family: str) -> tuple[str, str] | None:
    if cursor is None:
        return None
    prefix = f"{family}:"
    if not cursor.startswith(prefix):
        raise ValidationFailure(f"{family} cursor is invalid")
    token = cursor[len(prefix) :]
    try:
        padding = "=" * (-len(token) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"{family} cursor is invalid") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(value, str) and value for value in decoded)
    ):
        raise ValidationFailure(f"{family} cursor is invalid")
    return decoded[0], decoded[1]


def _after_cursor(created_column: Any, id_column: Any, cursor: tuple[str, str] | None) -> Any:
    if cursor is None:
        return None
    created_at, identity = cursor
    return or_(
        created_column < created_at,
        and_(created_column == created_at, id_column < identity),
    )


def _validate_limit(name: str, value: int, maximum: int = MAX_PAGE_ITEMS) -> None:
    if value < 1 or value > maximum:
        raise ValidationFailure(f"{name} limit must be between 1 and {maximum}")


def _attachment_predicates(
    project_id: str,
    focused_domain_ids: list[str],
    edge_modes: tuple[str, ...] = ATTACHMENT_MODES,
) -> list[Any]:
    return [
        ExperimentLineageEdge.workspace_id == project_id,
        ExperimentLineageEdge.source_resource_id.in_(focused_domain_ids),
        ExperimentLineageEdge.edge_mode.in_(edge_modes),
        ExperimentExternalEntityReceipt.workspace_id == project_id,
    ]


async def _attachment_page(
    session: AsyncSession,
    *,
    project_id: str,
    focused_domain_ids: list[str],
    family: str,
    cursor: str | None,
    limit: int,
    edge_modes: tuple[str, ...] = ATTACHMENT_MODES,
) -> tuple[list[tuple[ExperimentLineageEdge, ExperimentExternalEntityReceipt]], str | None]:
    if not focused_domain_ids:
        if cursor is not None:
            _decode_cursor(cursor, family)
        return [], None
    decoded = _decode_cursor(cursor, family)
    statement = (
        select(ExperimentLineageEdge, ExperimentExternalEntityReceipt)
        .join(
            ExperimentExternalEntityReceipt,
            ExperimentExternalEntityReceipt.id == ExperimentLineageEdge.target_resource_id,
        )
        .where(*_attachment_predicates(project_id, focused_domain_ids, edge_modes))
    )
    after = _after_cursor(ExperimentLineageEdge.created_at, ExperimentLineageEdge.id, decoded)
    if after is not None:
        statement = statement.where(after)
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentLineageEdge.created_at.desc(),
                ExperimentLineageEdge.id.desc(),
            ).limit(limit + 1)
        )
    ).all()
    page_rows = [(row[0], row[1]) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit and page_rows:
        edge = page_rows[-1][0]
        next_cursor = _encode_cursor(family, edge.created_at, edge.id)
    return page_rows, next_cursor


async def _attachment_count(
    session: AsyncSession,
    *,
    project_id: str,
    focused_domain_ids: list[str],
) -> int:
    if not focused_domain_ids:
        return 0
    return int(
        (
            await session.execute(
                select(func.count(ExperimentLineageEdge.id))
                .select_from(ExperimentLineageEdge)
                .join(
                    ExperimentExternalEntityReceipt,
                    ExperimentExternalEntityReceipt.id == ExperimentLineageEdge.target_resource_id,
                )
                .where(*_attachment_predicates(project_id, focused_domain_ids))
            )
        ).scalar_one()
    )


async def _complete_attachment_receipts(
    session: AsyncSession,
    *,
    project_id: str,
    focused_domain_ids: list[str],
) -> list[ExperimentExternalEntityReceipt]:
    if not focused_domain_ids:
        return []
    receipts = list(
        await session.scalars(
            select(ExperimentExternalEntityReceipt)
            .join(
                ExperimentLineageEdge,
                ExperimentLineageEdge.target_resource_id == ExperimentExternalEntityReceipt.id,
            )
            .where(*_attachment_predicates(project_id, focused_domain_ids))
            .distinct()
            .order_by(
                ExperimentExternalEntityReceipt.created_at.desc(),
                ExperimentExternalEntityReceipt.id.desc(),
            )
            .limit(MAX_TREE_NODES + 1)
        )
    )
    if len(receipts) > MAX_TREE_NODES:
        raise ValidationFailure("Focused source receipt set exceeds the supported bound")
    return receipts


def _receipt_acknowledgement(receipt: ExperimentExternalEntityReceipt) -> dict[str, Any] | None:
    try:
        acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
    except json.JSONDecodeError:
        return None
    return acknowledgement if isinstance(acknowledgement, dict) else None


def _verified_at(acknowledgement: dict[str, Any] | None) -> str | None:
    if acknowledgement is None:
        return None
    value = acknowledgement.get("verified_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validated_reverification_payload(
    row: ExperimentDomainAdapterReceipt,
    payload: Any,
    *,
    source_receipt: ExperimentExternalEntityReceipt,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or set(payload) != SOURCE_REVERIFICATION_RECEIPT_KEYS:
        return None
    expected = {
        "schema": "bms.global.source-reverification-receipt.v1",
        "reverification_receipt_id": row.resource_id,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_experiment_id,
        "adapter_id": row.adapter_id,
        "adapter_version": row.adapter_version,
        "source_receipt_id": source_receipt.id,
        "source_digest": source_receipt.content_digest,
        "verified_at": row.created_at,
        "normalized_request_sha256": row.normalized_request_sha256,
    }
    if any(payload.get(field) != value for field, value in expected.items()):
        return None
    if row.workspace_id != project_id or row.domain_experiment_id != domain_experiment_id:
        return None
    if row.adapter_id != source_receipt.verification_authority:
        return None
    return payload


def _receipt_reconciliation(
    receipt: ExperimentExternalEntityReceipt,
    reverification: dict[str, Any] | None = None,
    *,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    acknowledgement = _receipt_acknowledgement(receipt)
    last_verified_at = _verified_at(acknowledgement)
    authority = str(receipt.verification_authority or "").strip()
    if not authority or authority in {"legacy_unverified", "caller_unverified"} or authority.startswith("unverified:"):
        return {
            "state": "pending",
            "last_verified_at": last_verified_at,
            "reason": "source receipt has no durable server verification authority",
        }
    if acknowledgement is None:
        return {
            "state": "pending",
            "last_verified_at": None,
            "reason": "source receipt verification acknowledgement is missing or malformed",
        }
    expected_acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": receipt.store_id,
        "entity_kind": receipt.entity_kind,
        "entity_id": receipt.entity_id,
        "entity_revision_id": receipt.generation_or_revision,
        "availability": receipt.availability,
        "verifier_id": authority,
    }
    if any(
        str(acknowledgement.get(field) or "") != str(expected)
        for field, expected in expected_acknowledgement.items()
    ):
        return {
            "state": "pending",
            "last_verified_at": last_verified_at,
            "reason": "source receipt acknowledgement does not match persisted verification authority",
        }
    acknowledged_digest = acknowledgement.get("content_digest")
    if isinstance(acknowledged_digest, str) and acknowledged_digest != receipt.content_digest:
        return {
            "state": "digest_mismatch",
            "last_verified_at": last_verified_at,
            "reason": "persisted receipt digest disagrees with its verification acknowledgement",
        }
    if (
        not isinstance(acknowledged_digest, str)
        or len(acknowledged_digest) != 64
        or any(character not in "0123456789abcdef" for character in acknowledged_digest)
    ):
        return {
            "state": "pending",
            "last_verified_at": last_verified_at,
            "reason": "source receipt has no valid immutable digest",
        }
    if last_verified_at is None:
        return {
            "state": "pending",
            "last_verified_at": None,
            "reason": "source receipt has no valid stored verification timestamp",
        }
    if receipt.availability == "unavailable":
        return {
            "state": "source_unavailable",
            "last_verified_at": last_verified_at,
            "reason": "persisted source receipt is unavailable",
        }
    if receipt.availability != "available":
        return {
            "state": "pending",
            "last_verified_at": last_verified_at,
            "reason": "source receipt availability has not been verified",
        }
    if reverification is not None:
        reverified_at = _parse_utc_timestamp(reverification.get("verified_at"))
        valid_until = _parse_utc_timestamp(reverification.get("valid_until"))
        reverified_digest = reverification.get("source_digest")
        if reverified_digest != receipt.content_digest:
            return {
                "state": "digest_mismatch",
                "last_verified_at": reverification.get("verified_at"),
                "reason": "source re-verification digest disagrees with the persisted receipt",
            }
        now = current_time or datetime.now(timezone.utc)
        if (
            reverification.get("schema") != "bms.global.source-reverification-receipt.v1"
            or reverification.get("source_receipt_id") != receipt.id
            or reverified_at is None
            or valid_until is None
            or reverified_at > now + SOURCE_REVERIFICATION_FUTURE_SKEW
            or valid_until <= reverified_at
            or valid_until - reverified_at > SOURCE_REVERIFICATION_TTL
        ):
            return {
                "state": "pending",
                "last_verified_at": reverification.get("verified_at"),
                "reason": "source re-verification receipt is malformed or unbounded",
            }
        if valid_until > now:
            return {
                "state": "current",
                "last_verified_at": str(reverification["verified_at"]),
                "reason": None,
            }
        return {
            "state": "stale",
            "last_verified_at": str(reverification["verified_at"]),
            "reason": "source re-verification receipt has expired",
        }
    return {
        "state": "stale",
        "last_verified_at": last_verified_at,
        "reason": "persisted verification is historical and has no bounded freshness or re-verification receipt",
    }


def _source_reconciliation(
    receipts: list[ExperimentExternalEntityReceipt],
    reverifications: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    if not receipts:
        return {"state": "current", "last_verified_at": None, "reason": None}
    current_reverifications = reverifications or {}
    reconciliations = [
        _receipt_reconciliation(receipt, current_reverifications.get(receipt.id))
        for receipt in receipts
    ]
    priority = {
        "current": 0,
        "stale": 1,
        "pending": 2,
        "source_unavailable": 3,
        "digest_mismatch": 4,
    }
    dominant = max(reconciliations, key=lambda item: priority[str(item["state"])])
    return {
        "state": dominant["state"],
        "last_verified_at": dominant["last_verified_at"],
        "reason": dominant["reason"],
    }


def _receipt_map_node(receipt: ExperimentExternalEntityReceipt) -> dict[str, Any]:
    acknowledgement = _receipt_acknowledgement(receipt) or {}
    metadata_value = acknowledgement.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    return {
        "node_key": _key("external_entity_receipt", receipt.id),
        "node_type": "external_entity_receipt",
        "label": str(acknowledgement.get("entity_kind") or receipt.entity_kind),
        "normalized_state": str(metadata.get("canonical_state") or receipt.availability),
        "canonical_identity": {
            "store_id": receipt.store_id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "receipt_id": receipt.id,
            "content_digest": receipt.content_digest,
        },
        "counts": {},
        "reconciliation": _receipt_reconciliation(receipt),
        "allowed_actions": ["open"],
    }


def _lineage_item(edge: ExperimentLineageEdge, receipt: ExperimentExternalEntityReceipt) -> dict[str, Any]:
    return {
        "id": edge.id,
        "edge_key": edge.edge_key,
        "source_resource_id": edge.source_resource_id,
        "target_resource_id": edge.target_resource_id,
        "edge_mode": edge.edge_mode,
        "metadata": json.loads(edge.metadata_json or "{}"),
        "created_at": edge.created_at,
        "receipt_id": receipt.id,
        "content_digest": receipt.content_digest,
    }


async def _result_items(
    session: AsyncSession,
    *,
    project_id: str,
    rows: list[tuple[ExperimentLineageEdge, ExperimentExternalEntityReceipt]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for edge, receipt in rows:
        try:
            surface = await result_surface_for_receipt(
                session,
                project_id=project_id,
                receipt_id=receipt.id,
            )
        except ValidationFailure as exc:
            surface = {
                "receipt_id": receipt.id,
                "entity_kind": receipt.entity_kind,
                "entity_id": receipt.entity_id,
                "content_digest": receipt.content_digest,
                "canonical_surface": None,
                "unavailable_reason": str(exc),
            }
        items.append({**surface, "lineage_edge_key": edge.edge_key})
    return items


async def _record_page(
    session: AsyncSession,
    *,
    project_id: str,
    subject_resource_ids: list[str] | None,
    record_kind: str,
    family: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    decoded = _decode_cursor(cursor, family)
    statement = select(ExperimentResearchRecord).where(
        ExperimentResearchRecord.workspace_id == project_id,
        ExperimentResearchRecord.record_kind == record_kind,
    )
    if subject_resource_ids is not None:
        statement = statement.where(ExperimentResearchRecord.subject_resource_id.in_(subject_resource_ids))
    after = _after_cursor(
        ExperimentResearchRecord.created_at,
        ExperimentResearchRecord.resource_id,
        decoded,
    )
    if after is not None:
        statement = statement.where(after)
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentResearchRecord.created_at.desc(),
                ExperimentResearchRecord.resource_id.desc(),
            ).limit(limit + 1)
        )
    ).scalars().all()
    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page_rows:
        row = page_rows[-1]
        next_cursor = _encode_cursor(family, row.created_at, row.resource_id)
    return [
        {
            "resource_id": row.resource_id,
            "subject_resource_id": row.subject_resource_id,
            "record_kind": row.record_kind,
            "body": row.body,
            "author": row.author,
            "source_receipt_ids": json.loads(row.source_receipt_ids_json or "[]"),
            "supersedes_record_id": row.supersedes_record_id,
            "created_at": row.created_at,
        }
        for row in page_rows
    ], next_cursor


async def _activity_page(
    session: AsyncSession,
    *,
    project_id: str,
    resource_ids: list[str] | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    decoded = _decode_cursor(cursor, "activity")
    statement = select(ExperimentAuditEvent).where(
        ExperimentAuditEvent.workspace_id == project_id
    )
    if resource_ids is not None:
        statement = statement.where(ExperimentAuditEvent.resource_id.in_(resource_ids))
    after = _after_cursor(ExperimentAuditEvent.created_at, ExperimentAuditEvent.id, decoded)
    if after is not None:
        statement = statement.where(after)
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentAuditEvent.created_at.desc(),
                ExperimentAuditEvent.id.desc(),
            ).limit(limit + 1)
        )
    ).scalars().all()
    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page_rows:
        row = page_rows[-1]
        next_cursor = _encode_cursor("activity", row.created_at, row.id)
    return [
        {
            "id": row.id,
            "resource_id": row.resource_id,
            "event_type": row.event_type,
            "generation": row.generation,
            "payload": json.loads(row.payload_json),
            "created_at": row.created_at,
        }
        for row in page_rows
    ], next_cursor


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else {}


def _first_value(objects: list[dict[str, Any]], *keys: str) -> Any:
    for obj in objects:
        for key in keys:
            value = obj.get(key)
            if value is not None:
                return value
    return None


def _public_receipt(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _public_receipt(child)
            for key, child in value.items()
            if not any(
                token in str(key).lower()
                for token in ("path", "directory", "output_dir", "command", "executable")
            )
            and str(key).lower() not in {
                "input",
                "input_structure",
                "input_pdb",
                "input_cif",
                "plr_input_pdb",
            }
        }
    if isinstance(value, list):
        return [_public_receipt(child) for child in value]
    return value


def _attempt_item(attempt: ExperimentRunAttempt) -> dict[str, Any]:
    return {
        "attempt_id": attempt.resource_id,
        "attempt_number": attempt.attempt_number,
        "canonical_job_id": attempt.scheduler_job_id,
        "canonical_state": attempt.state,
        "binding_receipt": _public_receipt(_json_object(attempt.external_binding_receipt_json)) or None,
        "runtime_identity": _public_receipt(_json_object(attempt.runtime_identity_json)) or None,
        "terminal_receipt": _public_receipt(_json_object(attempt.terminal_receipt_json)) or None,
    }


def _run_item(
    run: ExperimentWorkflowRun,
    preparation: ExperimentWorkflowPreparation,
    revision: ExperimentRevision,
    attempts: list[ExperimentRunAttempt],
) -> dict[str, Any]:
    workflow = _json_object(revision.canonical_payload)
    normalized_request = _json_object(preparation.normalized_request_json)
    scheduler = _json_object(preparation.scheduler_payload_json)
    scheduler_params_value = scheduler.get("params")
    scheduler_params: dict[str, Any] = scheduler_params_value if isinstance(scheduler_params_value, dict) else {}
    latest = attempts[-1] if attempts else None
    binding: dict[str, Any] = _public_receipt(_json_object(latest.external_binding_receipt_json)) if latest is not None else {}
    runtime: dict[str, Any] = _public_receipt(_json_object(latest.runtime_identity_json)) if latest is not None else {}
    terminal: dict[str, Any] = _public_receipt(_json_object(latest.terminal_receipt_json)) if latest is not None else {}
    source_objects: list[dict[str, Any]] = [binding, terminal, runtime]

    progress_value = _first_value(source_objects, "progress")
    if (
        not isinstance(progress_value, dict)
        or progress_value.get("kind") not in {"fraction", "elapsed", "indeterminate"}
        or (
            progress_value.get("value") is not None
            and (not isinstance(progress_value.get("value"), (int, float)) or isinstance(progress_value.get("value"), bool))
        )
    ):
        progress = {"kind": "indeterminate", "value": None}
    else:
        progress = {"kind": progress_value["kind"], "value": progress_value.get("value")}

    elapsed_value = _first_value(source_objects, "elapsed_seconds")
    elapsed_seconds = (
        elapsed_value
        if isinstance(elapsed_value, (int, float)) and not isinstance(elapsed_value, bool) and elapsed_value >= 0
        else 0
    )
    replica_value = _first_value(source_objects, "replica_index")
    replica_index = replica_value if isinstance(replica_value, int) and not isinstance(replica_value, bool) else None
    output_value = _first_value([terminal, binding], "output_count")
    output_count = output_value if isinstance(output_value, int) and not isinstance(output_value, bool) and output_value >= 0 else 0

    condition_value = _first_value(source_objects, "condition")
    if isinstance(condition_value, dict) and condition_value.get("severity") in {"none", "warning", "failure"}:
        condition = {
            "severity": condition_value["severity"],
            "code": condition_value.get("code") if isinstance(condition_value.get("code"), str) else None,
            "message": condition_value.get("message") if isinstance(condition_value.get("message"), str) else None,
        }
    elif (latest.state if latest is not None else run.state) in {"failed", "cancelled"}:
        failure_message = _first_value(source_objects, "error_message", "message")
        condition = {
            "severity": "failure",
            "code": str(_first_value(source_objects, "failure_code", "code") or (latest.state if latest is not None else run.state)),
            "message": failure_message if isinstance(failure_message, str) else None,
        }
    else:
        condition = {"severity": "none", "code": None, "message": None}

    workflow_type = _first_value(
        [workflow, normalized_request, scheduler_params, scheduler],
        "workflow_type",
        "workflow_family",
        "family",
        "mode",
    )
    target_label = _first_value(
        [workflow, normalized_request, scheduler_params, scheduler],
        "target_label",
        "operator_label",
        "label",
        "name",
    )
    canonical_state = _first_value(source_objects, "canonical_state", "state", "status")
    stage = _first_value(source_objects, "stage")
    started_at = _first_value(source_objects, "started_at")
    receipt_id = _first_value(source_objects, "receipt_id", "source_receipt_id")
    output_receipt_ids_value = terminal.get("output_receipt_ids")
    output_receipt_ids = (
        [str(value) for value in output_receipt_ids_value if isinstance(value, str) and value]
        if isinstance(output_receipt_ids_value, list)
        else []
    )
    if receipt_id is None and output_receipt_ids:
        receipt_id = output_receipt_ids[0]
    adapter_id = _first_value([terminal, binding, scheduler_params, workflow], "adapter_id", "workflow_adapter")
    effective_state = latest.state if latest is not None else run.state
    available_actions = ["view_lineage"]
    if latest is not None:
        available_actions.append("clone")
    if effective_state == "completed" and output_receipt_ids:
        available_actions.insert(0, "open_results")
    if effective_state == "failed":
        available_actions.extend(["retry", "resubmit"])

    return {
        "run_id": run.resource_id,
        "workflow_id": revision.subject_id,
        "canonical_job_id": latest.scheduler_job_id if latest is not None else None,
        "workflow_type": str(workflow_type or "unknown"),
        "target_label": str(target_label or run.node_id),
        "canonical_state": str(canonical_state or (latest.state if latest is not None else run.state)),
        "normalized_state": run.state,
        "stage": str(stage) if stage is not None else None,
        "progress": progress,
        "started_at": str(started_at) if started_at is not None else None,
        "elapsed_seconds": elapsed_seconds,
        "replica_index": replica_index,
        "batch_or_run_group_id": run.run_group_id,
        "output_count": output_count,
        "condition": condition,
        "receipt_id": str(receipt_id) if receipt_id is not None else None,
        "output_receipt_ids": output_receipt_ids,
        "adapter_id": str(adapter_id) if adapter_id is not None else None,
        "available_actions": available_actions,
        "canonical_surface": None,
        "attempts": [_attempt_item(attempt) for attempt in attempts],
    }


async def _attempts_by_run(
    session: AsyncSession,
    run_ids: list[str],
) -> dict[str, list[ExperimentRunAttempt]]:
    result: dict[str, list[ExperimentRunAttempt]] = {}
    if not run_ids:
        return result
    rows = (
        await session.execute(
            select(ExperimentRunAttempt)
            .where(ExperimentRunAttempt.workflow_run_id.in_(run_ids))
            .order_by(
                ExperimentRunAttempt.workflow_run_id,
                ExperimentRunAttempt.attempt_number,
            )
        )
    ).scalars().all()
    for attempt in rows:
        result.setdefault(attempt.workflow_run_id, []).append(attempt)
    return result


async def _dataset_page(
    session: AsyncSession,
    *,
    project_id: str,
    domain_ids: list[str],
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    decoded = _decode_cursor(cursor, "datasets")
    if not domain_ids:
        return [], None
    statement = select(ExperimentAggregateHead).where(
        ExperimentAggregateHead.workspace_id == project_id,
        ExperimentAggregateHead.aggregate_kind == "dataset",
        ExperimentAggregateHead.parent_id.in_(domain_ids),
    )
    after = _after_cursor(
        ExperimentAggregateHead.created_at,
        ExperimentAggregateHead.aggregate_id,
        decoded,
    )
    if after is not None:
        statement = statement.where(after)
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentAggregateHead.created_at.desc(),
                ExperimentAggregateHead.aggregate_id.desc(),
            ).limit(limit + 1)
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    for head in rows[:limit]:
        revision = await session.get(ExperimentRevision, head.current_revision_id) if head.current_revision_id else None
        items.append({
            "id": _key("dataset", f"{head.aggregate_id}:{head.current_revision_id}"),
            "dataset_id": head.aggregate_id,
            "node_key": _key("dataset", f"{head.aggregate_id}:{head.current_revision_id}"),
            "label": head.display_name,
            "domain_experiment_id": head.parent_id,
            "lifecycle_state": head.lifecycle_state,
            "current_revision_id": head.current_revision_id,
            "revision_number": revision.revision_number if revision is not None else None,
            "payload_sha256": revision.payload_sha256 if revision is not None else None,
            "dependency_graph_sha256": revision.dependency_graph_sha256 if revision is not None else None,
        })
    next_cursor = None
    if len(rows) > limit and items:
        last = rows[limit - 1]
        next_cursor = _encode_cursor("datasets", last.created_at, last.aggregate_id)
    return items, next_cursor


async def _run_page(
    session: AsyncSession,
    *,
    project_id: str,
    workflow_ids: list[str],
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str], str | None]:
    if not workflow_ids:
        if cursor is not None:
            _decode_cursor(cursor, "runs")
        return [], {}, None
    decoded = _decode_cursor(cursor, "runs")
    statement = (
        select(ExperimentWorkflowRun, ExperimentWorkflowPreparation, ExperimentRevision)
        .join(
            ExperimentWorkflowPreparation,
            ExperimentWorkflowPreparation.resource_id == ExperimentWorkflowRun.preparation_id,
        )
        .join(
            ExperimentRevision,
            ExperimentRevision.resource_id == ExperimentWorkflowPreparation.workflow_revision_id,
        )
        .where(
            ExperimentWorkflowRun.workspace_id == project_id,
            ExperimentRevision.subject_id.in_(workflow_ids),
        )
    )
    after = _after_cursor(
        ExperimentWorkflowRun.created_at,
        ExperimentWorkflowRun.resource_id,
        decoded,
    )
    if after is not None:
        statement = statement.where(after)
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentWorkflowRun.created_at.desc(),
                ExperimentWorkflowRun.resource_id.desc(),
            ).limit(limit + 1)
        )
    ).all()
    page_rows = rows[:limit]
    attempts_by_run = await _attempts_by_run(session, [row[0].resource_id for row in page_rows])
    items = [
        _run_item(run, preparation, revision, attempts_by_run.get(run.resource_id, []))
        for run, preparation, revision in page_rows
    ]
    for item in items:
        surfaces: list[dict[str, Any]] = []
        for receipt_id in item["output_receipt_ids"]:
            try:
                surfaces.append(
                    await result_surface_for_receipt(
                        session,
                        project_id=project_id,
                        receipt_id=receipt_id,
                    )
                )
            except (NotFound, ValidationFailure):
                continue
        item["canonical_surface"] = surfaces[0] if surfaces else None
        item["canonical_surfaces"] = surfaces
        if not surfaces and "open_results" in item["available_actions"]:
            item["available_actions"].remove("open_results")
    workflow_by_run = {run.resource_id: revision.subject_id for run, _preparation, revision in page_rows}
    next_cursor = None
    if len(rows) > limit and page_rows:
        last = page_rows[-1][0]
        next_cursor = _encode_cursor("runs", last.created_at, last.resource_id)
    return items, workflow_by_run, next_cursor


def _count_states(heads: list[ExperimentAggregateHead]) -> dict[str, int]:
    result: dict[str, int] = {}
    for head in heads:
        result[head.lifecycle_state] = result.get(head.lifecycle_state, 0) + 1
    return result


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if node["node_key"] not in seen:
            seen.add(node["node_key"])
            result.append(node)
    return result


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in edges:
        if edge["edge_key"] not in seen:
            seen.add(edge["edge_key"])
            result.append(edge)
    return result


async def build_project_manager_read_model(
    session: AsyncSession,
    *,
    project_id: str,
    focus_id: str | None = None,
    selected_node_key: str | None = None,
    map_cursor: str | None = None,
    run_cursor: str | None = None,
    result_cursor: str | None = None,
    lineage_cursor: str | None = None,
    note_cursor: str | None = None,
    decision_cursor: str | None = None,
    dataset_cursor: str | None = None,
    activity_cursor: str | None = None,
    map_limit: int = DEFAULT_MAP_NODES,
    run_limit: int = DEFAULT_RUNS,
    result_limit: int = DEFAULT_PAGE_ITEMS,
    lineage_limit: int = DEFAULT_PAGE_ITEMS,
    note_limit: int = DEFAULT_PAGE_ITEMS,
    decision_limit: int = DEFAULT_PAGE_ITEMS,
    dataset_limit: int = DEFAULT_PAGE_ITEMS,
    activity_limit: int = DEFAULT_PAGE_ITEMS,
) -> dict[str, Any]:
    _validate_limit("map", map_limit, MAX_MAP_NODES)
    _validate_limit("run", run_limit)
    _validate_limit("result", result_limit)
    _validate_limit("lineage", lineage_limit)
    _validate_limit("note", note_limit)
    _validate_limit("decision", decision_limit)
    _validate_limit("dataset", dataset_limit)
    _validate_limit("activity", activity_limit)

    project = await session.get(ExperimentAggregateHead, project_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound(f"project not found: {project_id}")
    project_payload = await _payload(session, project)
    global_heads = (
        await session.execute(
            select(ExperimentAggregateHead)
            .where(
                ExperimentAggregateHead.workspace_id == project_id,
                ExperimentAggregateHead.aggregate_kind == "experiment",
            )
            .order_by(ExperimentAggregateHead.updated_at.desc(), ExperimentAggregateHead.aggregate_id)
            .limit(MAX_TREE_NODES + 1)
        )
    ).scalars().all()
    if len(global_heads) > MAX_TREE_NODES:
        raise ValidationFailure("Project hierarchy exceeds the supported complete-tree bound")
    global_ids = [head.aggregate_id for head in global_heads]
    domain_heads: list[ExperimentAggregateHead] = []
    if global_ids:
        domain_heads = (
            await session.execute(
                select(ExperimentAggregateHead)
                .where(
                    ExperimentAggregateHead.workspace_id == project_id,
                    ExperimentAggregateHead.aggregate_kind == "domain_experiment",
                    ExperimentAggregateHead.parent_id.in_(global_ids),
                )
                .order_by(ExperimentAggregateHead.created_at, ExperimentAggregateHead.aggregate_id)
                .limit(MAX_TREE_NODES + 1)
            )
        ).scalars().all()
    projected_tree_nodes = 1 + len(global_heads) + len(domain_heads) * (1 + len(VIRTUAL_FOLDERS))
    if projected_tree_nodes > MAX_TREE_NODES:
        raise ValidationFailure("Project hierarchy exceeds the supported complete-tree bound")

    global_payloads = {head.aggregate_id: await _payload(session, head) for head in global_heads}
    domain_payloads = {head.aggregate_id: await _payload(session, head) for head in domain_heads}
    globals_by_id = {head.aggregate_id: head for head in global_heads}
    domains_by_parent: dict[str, list[ExperimentAggregateHead]] = {}
    for head in domain_heads:
        domains_by_parent.setdefault(str(head.parent_id), []).append(head)
    if focus_id is None:
        focus = next((head for head in global_heads if head.lifecycle_state != "archived"), None)
    elif focus_id == project_id:
        focus = None
    else:
        focus = globals_by_id.get(focus_id)
        if focus is None:
            raise ValidationFailure("focus_id does not identify this Project or one of its Global Experiments")
    focused_domains = domain_heads if focus is None else domains_by_parent.get(focus.aggregate_id, [])
    focused_domain_ids = [head.aggregate_id for head in focused_domains]
    folder_domain_id = None
    if selected_node_key and selected_node_key.startswith("virtual_folder:"):
        parts = selected_node_key.split(":", 2)
        if len(parts) == 3 and parts[1] in focused_domain_ids:
            folder_domain_id = parts[1]
    collection_domain_ids = [folder_domain_id] if folder_domain_id else focused_domain_ids

    tree_nodes = [
        _tree_node(
            node_key=_key("project", project_id),
            node_type="project",
            subject_id=project_id,
            parent_node_key=None,
            label=str(project_payload.get("name") or project.display_name),
            lifecycle_state=project.lifecycle_state,
            counts={"global_experiments": len(global_heads), "domain_experiments": len(domain_heads)},
            has_children=bool(global_heads),
            allowed_actions=["edit", "archive"] if project.lifecycle_state != "archived" else ["restore"],
        )
    ]
    for global_head in global_heads:
        global_key = _key("global_experiment", global_head.aggregate_id)
        children = domains_by_parent.get(global_head.aggregate_id, [])
        tree_nodes.append(
            _tree_node(
                node_key=global_key,
                node_type="global_experiment",
                subject_id=global_head.aggregate_id,
                parent_node_key=_key("project", project_id),
                label=str(global_payloads[global_head.aggregate_id].get("name") or global_head.display_name),
                lifecycle_state=global_head.lifecycle_state,
                counts={"domain_experiments": len(children)},
                has_children=bool(children),
                allowed_actions=["edit", "archive"] if global_head.lifecycle_state != "archived" else ["restore"],
            )
        )
        for domain_head in children:
            domain_key = _key("domain_experiment", domain_head.aggregate_id)
            domain_payload = domain_payloads[domain_head.aggregate_id]
            domain_actions = ["attach", "add_note", "archive"]
            if domain_payload.get("domain_kind") == "protein_in_silico":
                domain_actions.insert(0, "edit")
            tree_nodes.append(
                _tree_node(
                    node_key=domain_key,
                    node_type="domain_experiment",
                    subject_id=domain_head.aggregate_id,
                    parent_node_key=global_key,
                    label=str(domain_payload.get("name") or domain_head.display_name),
                    lifecycle_state=domain_head.lifecycle_state,
                    has_children=True,
                    allowed_actions=domain_actions
                    if domain_head.lifecycle_state != "archived"
                    else ["restore"],
                )
            )
            for folder in VIRTUAL_FOLDERS:
                tree_nodes.append(
                    _tree_node(
                        node_key=f"virtual_folder:{domain_head.aggregate_id}:{folder}",
                        node_type="virtual_folder",
                        subject_id=None,
                        parent_node_key=domain_key,
                        label=folder.replace("_", " ").title(),
                        lifecycle_state=None,
                    )
                )

    context_globals = global_heads
    stable_map_nodes: list[dict[str, Any]] = [
        {
            "node_key": _key("project", project_id),
            "node_type": "project",
            "label": str(project_payload.get("name") or project.display_name),
            "normalized_state": project.lifecycle_state,
            "canonical_identity": {"store_id": "global", "entity_id": project_id},
            "counts": {"global_experiments": len(global_heads)},
            "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
            "allowed_actions": ["edit"],
        }
    ]
    stable_map_edges: list[dict[str, Any]] = []

    ngs_project_links = list(
        (
            await session.scalars(
                select(ExperimentLineageEdge)
                .where(
                    ExperimentLineageEdge.workspace_id == project_id,
                    ExperimentLineageEdge.source_resource_id == project_id,
                    ExperimentLineageEdge.edge_mode == "references",
                    ExperimentLineageEdge.edge_key.like("ngs-molbio-project-link:%"),
                )
                .order_by(ExperimentLineageEdge.created_at, ExperimentLineageEdge.id)
                .limit(MAX_MAP_NODES + 1)
            )
        ).all()
    )
    if len(ngs_project_links) > MAX_MAP_NODES:
        raise ValidationFailure("NGS/MolBio Project links exceed the supported relationship-map bound")
    linked_local_ids = sorted({edge.target_resource_id for edge in ngs_project_links})
    stable_map_nodes[0]["counts"]["linked_ngs_molbio_projects"] = len(linked_local_ids)
    linked_local_heads = list(
        (
            await session.scalars(
                select(ExperimentAggregateHead).where(
                    ExperimentAggregateHead.aggregate_id.in_(linked_local_ids),
                    ExperimentAggregateHead.aggregate_kind == "workspace",
                )
            )
        ).all()
    ) if linked_local_ids else []
    linked_local_payloads = {
        head.aggregate_id: await _payload(session, head) for head in linked_local_heads
    }
    linked_local_by_id = {head.aggregate_id: head for head in linked_local_heads}
    link_metadata = {edge.id: json.loads(edge.metadata_json) for edge in ngs_project_links}
    shared_experiment_ids = sorted({
        experiment_id
        for metadata in link_metadata.values()
        for experiment_id in metadata.get("experiment_ids", [])
    })
    shared_result_ids = sorted({
        result_id
        for metadata in link_metadata.values()
        for result_id in metadata.get("result_ids", [])
    })
    shared_experiment_heads = list(
        (
            await session.scalars(
                select(ExperimentAggregateHead).where(
                    ExperimentAggregateHead.aggregate_id.in_(shared_experiment_ids)
                )
            )
        ).all()
    ) if shared_experiment_ids else []
    shared_experiment_by_id = {head.aggregate_id: head for head in shared_experiment_heads}
    shared_result_receipts = list(
        (
            await session.scalars(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(shared_result_ids)
                )
            )
        ).all()
    ) if shared_result_ids else []
    shared_result_by_id = {receipt.id: receipt for receipt in shared_result_receipts}
    for edge in ngs_project_links:
        local_head = linked_local_by_id.get(edge.target_resource_id)
        if local_head is None:
            continue
        metadata = link_metadata[edge.id]
        local_key = _key("local_ngs_molbio_project", local_head.aggregate_id)
        local_link_metadata = [
            link_metadata[candidate.id]
            for candidate in ngs_project_links
            if candidate.target_resource_id == local_head.aggregate_id
        ]
        local_experiment_ids = {
            experiment_id
            for candidate_metadata in local_link_metadata
            for experiment_id in candidate_metadata.get("experiment_ids", [])
        }
        local_result_ids = {
            result_id
            for candidate_metadata in local_link_metadata
            for result_id in candidate_metadata.get("result_ids", [])
        }
        stable_map_nodes.append(
            {
                "node_key": local_key,
                "node_type": "local_ngs_molbio_project",
                "label": str(linked_local_payloads[local_head.aggregate_id].get("name") or local_head.display_name),
                "normalized_state": local_head.lifecycle_state,
                "canonical_identity": {"store_id": "global", "entity_id": local_head.aggregate_id},
                "counts": {
                    "shared_experiments": len(local_experiment_ids),
                    "shared_results": len(local_result_ids),
                },
                "reconciliation": {"state": "current", "last_verified_at": edge.created_at, "reason": None},
                "allowed_actions": ["select", "open"],
            }
        )
        stable_map_edges.append(
            {
                "source_node_key": _key("project", project_id),
                "target_node_key": local_key,
                "lineage_mode": "references",
                "edge_key": edge.edge_key,
                "accessible_label": "Broader Project references selected Experiments and Results from local NGS/MolBio Project",
            }
        )
        for experiment_id in metadata.get("experiment_ids", []):
            experiment_head = shared_experiment_by_id.get(experiment_id)
            if experiment_head is None:
                continue
            experiment_key = _key("shared_ngs_molbio_experiment", experiment_id)
            stable_map_nodes.append(
                {
                    "node_key": experiment_key,
                    "node_type": "shared_ngs_molbio_experiment",
                    "label": experiment_head.display_name,
                    "normalized_state": experiment_head.lifecycle_state,
                    "canonical_identity": {"store_id": "global", "entity_id": experiment_id},
                    "counts": {},
                    "reconciliation": {"state": "current", "last_verified_at": edge.created_at, "reason": None},
                    "allowed_actions": ["select", "open"],
                }
            )
            stable_map_edges.append(
                {
                    "source_node_key": local_key,
                    "target_node_key": experiment_key,
                    "lineage_mode": "exposes",
                    "edge_key": f"{edge.edge_key}:experiment:{experiment_id}",
                    "accessible_label": "Local NGS/MolBio Project exposes contained Experiment",
                }
            )
        for result_id in metadata.get("result_ids", []):
            result_receipt = shared_result_by_id.get(result_id)
            if result_receipt is None:
                continue
            result_key = _key("shared_ngs_molbio_result", result_id)
            stable_map_nodes.append(
                {
                    "node_key": result_key,
                    "node_type": "shared_ngs_molbio_result",
                    "label": f"{result_receipt.entity_kind}: {result_receipt.entity_id}",
                    "normalized_state": result_receipt.availability,
                    "canonical_identity": {"store_id": result_receipt.store_id, "entity_id": result_receipt.entity_id},
                    "counts": {},
                    "reconciliation": {"state": "current", "last_verified_at": edge.created_at, "reason": None},
                    "allowed_actions": ["select", "open"],
                }
            )
            stable_map_edges.append(
                {
                    "source_node_key": local_key,
                    "target_node_key": result_key,
                    "lineage_mode": "shares_result",
                    "edge_key": f"{edge.edge_key}:result:{result_id}",
                    "accessible_label": "Local NGS/MolBio Project exposes governed native Result reference",
                }
            )
    for global_head in context_globals:
        node_key = _key("global_experiment", global_head.aggregate_id)
        stable_map_nodes.append(
            {
                "node_key": node_key,
                "node_type": "global_experiment",
                "label": str(global_payloads[global_head.aggregate_id].get("name") or global_head.display_name),
                "normalized_state": global_head.lifecycle_state,
                "canonical_identity": {"store_id": "global", "entity_id": global_head.aggregate_id},
                "counts": {"domain_experiments": len(domains_by_parent.get(global_head.aggregate_id, []))},
                "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
                "allowed_actions": ["select", "edit"],
            }
        )
        stable_map_edges.append(
            {
                "source_node_key": _key("project", project_id),
                "target_node_key": node_key,
                "lineage_mode": "contains",
                "edge_key": f"contains:{project_id}:{global_head.aggregate_id}",
                "accessible_label": "Project contains Global Experiment",
            }
        )
    for domain_head in focused_domains:
        node_key = _key("domain_experiment", domain_head.aggregate_id)
        stable_map_nodes.append(
            {
                "node_key": node_key,
                "node_type": "domain_experiment",
                "label": str(domain_payloads[domain_head.aggregate_id].get("name") or domain_head.display_name),
                "normalized_state": domain_head.lifecycle_state,
                "canonical_identity": {"store_id": "global", "entity_id": domain_head.aggregate_id},
                "counts": {},
                "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
                "allowed_actions": ["select", "attach"],
            }
        )
        stable_map_edges.append(
            {
                "source_node_key": _key("global_experiment", str(domain_head.parent_id)),
                "target_node_key": node_key,
                "lineage_mode": "contains",
                "edge_key": f"contains:{domain_head.parent_id}:{domain_head.aggregate_id}",
                "accessible_label": "Global Experiment contains Domain Experiment",
            }
        )

    workflow_parent_ids = collection_domain_ids + [head.aggregate_id for head in context_globals]
    workflows: list[ExperimentAggregateHead] = []
    if workflow_parent_ids:
        workflows = (
            await session.execute(
                select(ExperimentAggregateHead)
                .where(
                    ExperimentAggregateHead.workspace_id == project_id,
                    ExperimentAggregateHead.aggregate_kind == "workflow",
                    ExperimentAggregateHead.parent_id.in_(workflow_parent_ids),
                )
                .order_by(ExperimentAggregateHead.created_at, ExperimentAggregateHead.aggregate_id)
                .limit(MAX_TREE_NODES + 1)
            )
        ).scalars().all()
    if len(workflows) > MAX_TREE_NODES:
        raise ValidationFailure("Focused workflow hierarchy exceeds the supported bound")
    workflow_payloads = {head.aggregate_id: await _payload(session, head) for head in workflows}
    workflow_ids = [head.aggregate_id for head in workflows]
    workflow_nodes: list[dict[str, Any]] = []
    workflow_edges: list[dict[str, Any]] = []
    for workflow in workflows:
        parent_kind = "domain_experiment" if workflow.parent_id in collection_domain_ids else "global_experiment"
        workflow_nodes.append(
            {
                "node_key": _key("workflow", workflow.aggregate_id),
                "node_type": "workflow",
                "label": str(workflow_payloads[workflow.aggregate_id].get("name") or workflow.display_name),
                "normalized_state": workflow.lifecycle_state,
                "canonical_identity": {"store_id": "global", "entity_id": workflow.aggregate_id},
                "counts": {},
                "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
                "allowed_actions": ["select", "edit"],
                "parent_node_key": _key(parent_kind, str(workflow.parent_id)),
            }
        )
        workflow_edges.append(
            {
                "source_node_key": _key(parent_kind, str(workflow.parent_id)),
                "target_node_key": _key("workflow", workflow.aggregate_id),
                "lineage_mode": "contains",
                "edge_key": f"contains:{workflow.parent_id}:{workflow.aggregate_id}",
                "accessible_label": "Experiment contains Workflow",
            }
        )

    run_items, workflow_by_run, run_next_cursor = await _run_page(
        session,
        project_id=project_id,
        workflow_ids=workflow_ids,
        cursor=run_cursor,
        limit=run_limit,
    )
    run_nodes = [
        {
            "node_key": _key("workflow_run", item["run_id"]),
            "node_type": "workflow_run",
            "label": item["target_label"],
            "normalized_state": item["normalized_state"],
            "canonical_identity": {"store_id": "global", "entity_id": item["run_id"]},
            "counts": {"attempts": len(item["attempts"])},
            "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
            "allowed_actions": ["select", *item["available_actions"]],
            "parent_node_key": _key("workflow", workflow_by_run[item["run_id"]]),
        }
        for item in run_items
    ]
    run_edges = [
        {
            "source_node_key": _key("workflow", workflow_by_run[item["run_id"]]),
            "target_node_key": _key("workflow_run", item["run_id"]),
            "lineage_mode": "launched",
            "edge_key": f"launched:{workflow_by_run[item['run_id']]}:{item['run_id']}",
            "accessible_label": "Workflow launched run",
        }
        for item in run_items
    ]

    native_lineage_rows = list(
        (
            await session.scalars(
                select(ExperimentLineageEdge)
                .where(
                    ExperimentLineageEdge.workspace_id == project_id,
                    func.json_extract(ExperimentLineageEdge.metadata_json, "$.native") == 1,
                )
                .order_by(ExperimentLineageEdge.created_at, ExperimentLineageEdge.id)
                .limit(MAX_MAP_NODES + 1)
            )
        ).all()
    )
    native_lineage_truncated = len(native_lineage_rows) > MAX_MAP_NODES
    native_lineage_rows = native_lineage_rows[:MAX_MAP_NODES]
    native_receipt_ids = {
        receipt_id
        for edge in native_lineage_rows
        for receipt_id in (edge.source_resource_id, edge.target_resource_id)
    }
    native_receipts = list(
        (
            await session.scalars(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.workspace_id == project_id,
                    ExperimentExternalEntityReceipt.id.in_(native_receipt_ids),
                )
            )
        ).all()
    ) if native_receipt_ids else []
    native_receipt_nodes = [_receipt_map_node(receipt) for receipt in native_receipts]
    native_map_edges = [
        {
            "source_node_key": _key("external_entity_receipt", edge.source_resource_id),
            "target_node_key": _key("external_entity_receipt", edge.target_resource_id),
            "lineage_mode": edge.edge_mode,
            "edge_key": edge.edge_key,
            "accessible_label": edge.edge_mode.removeprefix("native_").replace("_", " ").title(),
        }
        for edge in native_lineage_rows
    ]
    stable_map_edges.extend(native_map_edges)
    all_context_map_nodes = _dedupe_nodes(
        stable_map_nodes + workflow_nodes + run_nodes + native_receipt_nodes
    )
    context_budget = max(0, map_limit - 1)
    context_map_nodes = all_context_map_nodes[:context_budget]
    map_item_limit = max(1, map_limit - len(context_map_nodes))
    map_rows, map_next_cursor = await _attachment_page(
        session,
        project_id=project_id,
        focused_domain_ids=collection_domain_ids,
        family="map",
        cursor=map_cursor,
        limit=map_item_limit,
    )
    if not map_rows:
        context_map_nodes = all_context_map_nodes[:map_limit]
    lineage_rows, lineage_next_cursor = await _attachment_page(
        session,
        project_id=project_id,
        focused_domain_ids=collection_domain_ids,
        family="lineage",
        cursor=lineage_cursor,
        limit=lineage_limit,
    )
    result_rows, result_next_cursor = await _attachment_page(
        session,
        project_id=project_id,
        focused_domain_ids=collection_domain_ids,
        family="results",
        cursor=result_cursor,
        limit=result_limit,
        edge_modes=RESULT_ATTACHMENT_MODES,
    )
    dataset_items, dataset_next_cursor = await _dataset_page(
        session,
        project_id=project_id,
        domain_ids=collection_domain_ids,
        cursor=dataset_cursor,
        limit=dataset_limit,
    )
    note_items, note_next_cursor = await _record_page(
        session,
        project_id=project_id,
        subject_resource_ids=collection_domain_ids,
        record_kind="note",
        family="notes",
        cursor=note_cursor,
        limit=note_limit,
    )
    decision_items, decision_next_cursor = await _record_page(
        session,
        project_id=project_id,
        subject_resource_ids=collection_domain_ids,
        record_kind="decision",
        family="decisions",
        cursor=decision_cursor,
        limit=decision_limit,
    )
    activity_items, activity_next_cursor = await _activity_page(
        session,
        project_id=project_id,
        resource_ids=collection_domain_ids,
        cursor=activity_cursor,
        limit=activity_limit,
    )
    result_items = await _result_items(session, project_id=project_id, rows=result_rows)

    receipt_nodes = [_receipt_map_node(receipt) for _edge, receipt in map_rows]
    receipt_edges = [
        {
            "source_node_key": _key("domain_experiment", edge.source_resource_id),
            "target_node_key": _key("external_entity_receipt", receipt.id),
            "lineage_mode": edge.edge_mode,
            "edge_key": edge.edge_key,
            "accessible_label": edge.edge_mode.replace("_", " "),
        }
        for edge, receipt in map_rows
    ]
    all_map_nodes = _dedupe_nodes(context_map_nodes + receipt_nodes)
    all_map_edges = _dedupe_edges(stable_map_edges + workflow_edges + run_edges + receipt_edges)
    default_selection = _key("global_experiment", focus.aggregate_id) if focus is not None else _key("project", project_id)
    selection_key = selected_node_key or default_selection
    map_nodes_truncated = native_lineage_truncated or len(all_context_map_nodes) > len(context_map_nodes)
    map_nodes = all_map_nodes
    visible_map_keys = {node["node_key"] for node in map_nodes}
    map_edges = [
        edge
        for edge in all_map_edges
        if edge["source_node_key"] in visible_map_keys
        and edge["target_node_key"] in visible_map_keys
    ]

    node_index = {node["node_key"]: node for node in tree_nodes}
    node_index.update({node["node_key"]: node for node in map_nodes})
    selected_payload: dict[str, Any] = {}
    selected = node_index.get(selection_key)
    selected_run_item = next(
        (item for item in run_items if _key("workflow_run", item["run_id"]) == selection_key),
        None,
    )
    if selected is None and selection_key.startswith("workflow_run:") and workflow_ids:
        selected_run_id = selection_key.split(":", 1)[1]
        row = (
            await session.execute(
                select(ExperimentWorkflowRun, ExperimentWorkflowPreparation, ExperimentRevision)
                .join(
                    ExperimentWorkflowPreparation,
                    ExperimentWorkflowPreparation.resource_id == ExperimentWorkflowRun.preparation_id,
                )
                .join(
                    ExperimentRevision,
                    ExperimentRevision.resource_id == ExperimentWorkflowPreparation.workflow_revision_id,
                )
                .where(
                    ExperimentWorkflowRun.workspace_id == project_id,
                    ExperimentWorkflowRun.resource_id == selected_run_id,
                    ExperimentRevision.subject_id.in_(workflow_ids),
                )
            )
        ).one_or_none()
        if row is not None:
            run, preparation, revision = row
            selected_attempts = await _attempts_by_run(session, [run.resource_id])
            selected_run_item = _run_item(
                run,
                preparation,
                revision,
                selected_attempts.get(run.resource_id, []),
            )
            selected_surfaces: list[dict[str, Any]] = []
            for receipt_id in selected_run_item["output_receipt_ids"]:
                try:
                    selected_surfaces.append(
                        await result_surface_for_receipt(
                            session,
                            project_id=project_id,
                            receipt_id=receipt_id,
                        )
                    )
                except (NotFound, ValidationFailure):
                    continue
            selected_run_item["canonical_surface"] = selected_surfaces[0] if selected_surfaces else None
            selected_run_item["canonical_surfaces"] = selected_surfaces
            if not selected_surfaces and "open_results" in selected_run_item["available_actions"]:
                selected_run_item["available_actions"].remove("open_results")
            selected = {
                "node_key": selection_key,
                "node_type": "workflow_run",
                "label": selected_run_item["target_label"],
                "canonical_identity": {"store_id": "global", "entity_id": run.resource_id},
                "parent_node_key": _key("workflow", str(revision.subject_id)),
                "allowed_actions": selected_run_item["available_actions"],
                "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
            }
    if selected is None and selection_key.startswith("external_entity_receipt:"):
        receipt_id = selection_key.split(":", 1)[1]
        row = (
            await session.execute(
                select(ExperimentLineageEdge, ExperimentExternalEntityReceipt)
                .join(
                    ExperimentExternalEntityReceipt,
                    ExperimentExternalEntityReceipt.id == ExperimentLineageEdge.target_resource_id,
                )
                .where(
                    *_attachment_predicates(project_id, focused_domain_ids),
                    ExperimentExternalEntityReceipt.id == receipt_id,
                )
                .limit(1)
            )
        ).one_or_none()
        if row is not None:
            selected = _receipt_map_node(row[1])
            selected["parent_node_key"] = _key("domain_experiment", row[0].source_resource_id)
    if selected is None and selection_key.startswith("dataset:"):
        identity = selection_key.split(":", 1)[1]
        dataset_id, separator, revision_id = identity.partition(":")
        dataset = await session.get(ExperimentAggregateHead, dataset_id)
        if (
            dataset is not None
            and separator
            and revision_id
            and dataset.workspace_id == project_id
            and dataset.aggregate_kind == "dataset"
            and dataset.parent_id in collection_domain_ids
        ):
            revision = await session.get(ExperimentRevision, revision_id)
            if revision is not None and revision.subject_id == dataset.aggregate_id:
                members = (
                    await session.execute(
                        select(ExperimentDatasetRevisionMember)
                        .where(ExperimentDatasetRevisionMember.revision_id == revision.resource_id)
                        .order_by(ExperimentDatasetRevisionMember.ordinal)
                    )
                ).scalars().all()
                verified = all(sha256_text(member.value_json) == member.content_sha256 for member in members)
                if not verified:
                    raise ValidationFailure("dataset revision member digest verification failed")
                selected = {
                    "node_key": selection_key,
                    "node_type": "dataset",
                    "label": dataset.display_name,
                    "canonical_identity": {
                        "store_id": "global",
                        "entity_id": dataset.aggregate_id,
                        "revision_id": revision.resource_id,
                        "payload_sha256": revision.payload_sha256,
                    },
                    "parent_node_key": _key("domain_experiment", str(dataset.parent_id)),
                    "allowed_actions": [],
                    "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
                }
                selected_payload = {
                    "revision": json.loads(revision.canonical_payload),
                    "canonical_members": [
                        {
                            "ordinal": member.ordinal,
                            "role": member.role,
                            "semantic_identity": member.semantic_identity,
                            "value": json.loads(member.value_json),
                            "content_sha256": member.content_sha256,
                            "size_bytes": member.size_bytes,
                            "media_type": member.media_type,
                        }
                        for member in members
                    ],
                    "membership_authority": {
                        "revision_id": revision.resource_id,
                        "payload_sha256": revision.payload_sha256,
                        "member_count": len(members),
                        "all_member_digests_verified": True,
                    },
                }
    if selected is None and selection_key.startswith("research_record:"):
        record_id = selection_key.split(":", 1)[1]
        record = await session.get(ExperimentResearchRecord, record_id)
        if record is not None and record.workspace_id == project_id:
            selected = {
                "node_key": selection_key,
                "node_type": "research_record",
                "label": record.record_kind,
                "canonical_identity": {"store_id": "global", "entity_id": record.resource_id},
                "parent_node_key": None,
                "allowed_actions": [],
                "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
            }
            selected_payload = {
                "id": record.resource_id,
                "record_kind": record.record_kind,
                "body": record.body,
                "author": record.author,
                "created_at": record.created_at,
            }
    if selected is None:
        raise ValidationFailure("selected_node_key is unavailable in this Project")

    selected_subject_id = selected.get("subject_id") or (selected.get("canonical_identity") or {}).get("entity_id")
    canonical_surface = None
    if selected.get("node_type") == "external_entity_receipt":
        receipt_id = str((selected.get("canonical_identity") or {}).get("receipt_id") or "")
        try:
            canonical_surface = await result_surface_for_receipt(
                session,
                project_id=project_id,
                receipt_id=receipt_id,
            )
        except ValidationFailure:
            canonical_surface = None
    if selected.get("node_type") == "project":
        selected_payload = project_payload
    elif selected.get("node_type") == "global_experiment" and selected_subject_id in global_payloads:
        selected_payload = global_payloads[str(selected_subject_id)]
    elif selected.get("node_type") == "domain_experiment" and selected_subject_id in domain_payloads:
        selected_payload = domain_payloads[str(selected_subject_id)]
    elif selected.get("node_type") == "workflow" and selected_subject_id in workflow_payloads:
        selected_payload = public_workflow_payload(workflow_payloads[str(selected_subject_id)])
    elif selected.get("node_type") == "workflow_run" and selected_run_item is not None:
        selected_payload = selected_run_item

    attached_count = await _attachment_count(
        session,
        project_id=project_id,
        focused_domain_ids=collection_domain_ids,
    )
    receipt_domain_ids = collection_domain_ids
    selected_domain_id: str | None = None
    if selected.get("node_type") == "domain_experiment":
        candidate = str((selected.get("canonical_identity") or {}).get("entity_id") or "")
        if candidate in collection_domain_ids:
            selected_domain_id = candidate
    else:
        parent_node_key = str(selected.get("parent_node_key") or "")
        if parent_node_key.startswith("domain_experiment:"):
            candidate = parent_node_key.split(":", 1)[1]
            if candidate in collection_domain_ids:
                selected_domain_id = candidate
    if selected_domain_id is not None:
        receipt_domain_ids = [selected_domain_id]

    source_receipts = await _complete_attachment_receipts(
        session,
        project_id=project_id,
        focused_domain_ids=receipt_domain_ids,
    )
    source_receipts_by_id = {receipt.id: receipt for receipt in source_receipts}
    reverifications_by_receipt: dict[str, dict[str, Any] | None] = {}
    if source_receipts:
        reverification_rows = list(
            (
                await session.scalars(
                    select(ExperimentDomainAdapterReceipt)
                    .where(
                        ExperimentDomainAdapterReceipt.workspace_id == project_id,
                        ExperimentDomainAdapterReceipt.domain_experiment_id.in_(receipt_domain_ids),
                        ExperimentDomainAdapterReceipt.operation_kind == "reverify_source",
                    )
                    .order_by(
                        ExperimentDomainAdapterReceipt.created_at.desc(),
                        ExperimentDomainAdapterReceipt.resource_id.desc(),
                    )
                    .limit(MAX_REVERIFICATION_SCAN_ROWS + 1)
                )
            ).all()
        )
        target_receipt_ids = set(source_receipts_by_id)
        domain_global_ids = {
            head.aggregate_id: str(head.parent_id or "")
            for head in domain_heads
            if head.aggregate_id in receipt_domain_ids
        }
        for row in reverification_rows:
            try:
                payload = json.loads(row.receipt_json)
            except json.JSONDecodeError as exc:
                raise ValidationFailure("Stored source re-verification receipt is malformed") from exc
            source_receipt_id = payload.get("source_receipt_id") if isinstance(payload, dict) else None
            if not isinstance(source_receipt_id, str):
                raise ValidationFailure("Stored source re-verification receipt has no source identity")
            if source_receipt_id not in target_receipt_ids or source_receipt_id in reverifications_by_receipt:
                continue
            expected_global_id = domain_global_ids.get(row.domain_experiment_id)
            if expected_global_id is None:
                continue
            reverifications_by_receipt[source_receipt_id] = _validated_reverification_payload(
                row,
                payload,
                source_receipt=source_receipts_by_id[source_receipt_id],
                project_id=project_id,
                global_experiment_id=expected_global_id,
                domain_experiment_id=row.domain_experiment_id,
            )
            if len(reverifications_by_receipt) == len(target_receipt_ids):
                break
        if (
            len(reverification_rows) > MAX_REVERIFICATION_SCAN_ROWS
            and len(reverifications_by_receipt) < len(target_receipt_ids)
        ):
            raise ValidationFailure("Source re-verification history exceeds the supported scan bound")
    source_reconciliation = _source_reconciliation(
        source_receipts,
        reverifications_by_receipt,
    )
    source_receipts_by_id = {receipt.id: receipt for receipt in source_receipts}
    for node in map_nodes:
        if node.get("node_type") != "external_entity_receipt":
            continue
        identity = node.get("canonical_identity")
        receipt_id = identity.get("receipt_id") if isinstance(identity, dict) else None
        receipt = source_receipts_by_id.get(str(receipt_id)) if receipt_id is not None else None
        if receipt is not None:
            node["reconciliation"] = _receipt_reconciliation(
                receipt,
                reverifications_by_receipt.get(receipt.id),
            )
    if selected.get("node_type") == "external_entity_receipt":
        identity = selected.get("canonical_identity")
        selected_receipt_id = identity.get("receipt_id") if isinstance(identity, dict) else None
        selected_receipt = (
            source_receipts_by_id.get(str(selected_receipt_id))
            if selected_receipt_id is not None
            else None
        )
        if selected_receipt is not None:
            selected["reconciliation"] = _receipt_reconciliation(
                selected_receipt,
                reverifications_by_receipt.get(selected_receipt.id),
            )
    digest_set = sorted({receipt.content_digest for receipt in source_receipts})
    source_digest_set_sha256 = hashlib.sha256(canonical_json(digest_set).encode("utf-8")).hexdigest()
    adapter_versions = sorted(
        {
            (
                str(
                    (_receipt_acknowledgement(receipt) or {}).get("verifier_id")
                    or receipt.verification_authority
                    or "unknown"
                ),
                "1",
            )
            for receipt in source_receipts
        }
    )
    page_context_keys = [node["node_key"] for node in context_map_nodes]

    setup_rows = list(
        (
            await session.scalars(
                select(ExperimentWorkflowSetupContext)
                .where(ExperimentWorkflowSetupContext.project_id == project_id)
                .order_by(
                    ExperimentWorkflowSetupContext.updated_at.desc(),
                    ExperimentWorkflowSetupContext.setup_context_id.desc(),
                )
                .limit(MAX_TREE_NODES + 1)
            )
        ).all()
    )
    if len(setup_rows) > MAX_TREE_NODES:
        raise ValidationFailure("Project workflow setup task list exceeds the supported bound")
    workflow_heads_by_id = {head.aggregate_id: head for head in workflows}
    latest_run_by_workflow: dict[str, dict[str, Any]] = {}
    for run_item in run_items:
        latest_run_by_workflow.setdefault(str(run_item["workflow_id"]), run_item)
    task_items: list[dict[str, Any]] = []
    for setup in setup_rows:
        global_head = globals_by_id.get(setup.global_experiment_id)
        workflow_head = workflow_heads_by_id.get(setup.workflow_id)
        if global_head is None or workflow_head is None:
            raise ValidationFailure("Project workflow setup task ownership is incomplete")
        capability = protein_capability_record(setup.capability_id)
        latest_run = latest_run_by_workflow.get(setup.workflow_id)
        actions: list[str] = []
        if setup.lifecycle_state == "open":
            actions = ["resume", "edit", "delete"]
            if setup.validation_state == "ready":
                actions.append("prepare_launch")
        elif setup.lifecycle_state == "submitted":
            actions = ["resume", "open_launch"]
            if latest_run is not None:
                actions.extend(action for action in latest_run["available_actions"] if action not in actions)
        task_items.append(
            {
                "setup_context_id": setup.setup_context_id,
                "global_experiment_id": setup.global_experiment_id,
                "experiment_name": global_head.display_name,
                "workflow_id": setup.workflow_id,
                "workflow_name": workflow_head.display_name,
                "relationship_kind": setup.relationship_kind,
                "workflow_label": str(capability["label"]),
                "setup_state": setup.lifecycle_state,
                "validation_state": setup.validation_state,
                "latest_run_state": latest_run["normalized_state"] if latest_run is not None else None,
                "result_count": len(latest_run["output_receipt_ids"]) if latest_run is not None else 0,
                "reopen_route": (
                    f"{setup.setup_destination}{'&' if '?' in setup.setup_destination else '?'}"
                    f"setup_context_id={quote(setup.setup_context_id, safe='')}"
                    f"&project_id={quote(project_id, safe='')}"
                ),
                "allowed_actions": actions,
            }
        )

    return {
        "schema": "bms.project-manager.read-model.v1",
        "subject_id": project_id,
        "subject_generation": project.head_generation,
        "assembled_at": _utc_now(),
        "source_receipt_ids": [receipt.id for receipt in source_receipts],
        "source_digest_set_sha256": source_digest_set_sha256,
        "adapter_versions": [
            {"adapter_id": adapter_id, "version": version} for adapter_id, version in adapter_versions
        ],
        "reconciliation": source_reconciliation,
        "counts": {
            "global_experiments": len(global_heads),
            "domain_experiments": len(domain_heads),
            "attached_entities": attached_count,
        },
        "status_summary": {
            "projects": {project.lifecycle_state: 1},
            "global_experiments": _count_states(global_heads),
            "domain_experiments": _count_states(domain_heads),
        },
        "recent_activity": activity_items,
        "result_previews": [item for item in result_items if item.get("canonical_surface", True) is not None],
        "pagination": {
            "map_next_cursor": map_next_cursor,
            "run_next_cursor": run_next_cursor,
            "result_next_cursor": result_next_cursor,
            "lineage_next_cursor": lineage_next_cursor,
            "note_next_cursor": note_next_cursor,
            "decision_next_cursor": decision_next_cursor,
            "dataset_next_cursor": dataset_next_cursor,
            "activity_next_cursor": activity_next_cursor,
            "map": {
                "items": [_lineage_item(edge, receipt) for edge, receipt in map_rows],
                "next_cursor": map_next_cursor,
                "repeated_context_node_keys": page_context_keys,
            },
            "runs": {"items": run_items, "next_cursor": run_next_cursor},
            "results": {"items": result_items, "next_cursor": result_next_cursor},
            "lineage": {
                "items": [_lineage_item(edge, receipt) for edge, receipt in lineage_rows],
                "next_cursor": lineage_next_cursor,
            },
            "notes": {"items": note_items, "next_cursor": note_next_cursor},
            "decisions": {"items": decision_items, "next_cursor": decision_next_cursor},
            "datasets": {"items": dataset_items, "next_cursor": dataset_next_cursor},
            "activity": {"items": activity_items, "next_cursor": activity_next_cursor},
        },
        "project": _head_summary(project, project_payload),
        "tasks": task_items,
        "tree": {"nodes": tree_nodes},
        "map": {
            "focus_node_key": _key("global_experiment", focus.aggregate_id)
            if focus is not None
            else _key("project", project_id),
            "nodes": map_nodes,
            "edges": map_edges,
            "truncated": map_nodes_truncated or map_next_cursor is not None,
            "next_cursor": map_next_cursor,
        },
        "selection": {
            "node_key": selection_key,
            "node_type": selected.get("node_type"),
            "title": str(selected.get("label") or selected_payload.get("name") or "Selection"),
            "subtitle": selected_payload.get("objective") or selected_payload.get("scientific_question"),
            "canonical_identity": selected.get("canonical_identity")
            or {"store_id": "global", "entity_id": selected_subject_id},
            "summary": selected_payload,
            "relationship": {"parent_node_key": selected.get("parent_node_key")},
            "scientific_context": selected_payload.get("domain_payload") or {},
            "reconciliation": selected.get("reconciliation")
            or {"state": "current", "last_verified_at": None, "reason": None},
            "available_actions": selected.get("allowed_actions") or [],
            "canonical_surface": canonical_surface,
        },
        "runs": {"items": run_items, "next_cursor": run_next_cursor},
        "warnings": [],
        "allowed_actions": ["create_global_experiment", "edit_project", "archive_project"]
        if project.lifecycle_state != "archived"
        else ["restore_project"],
    }


__all__ = [
    "DEFAULT_MAP_NODES",
    "DEFAULT_PAGE_ITEMS",
    "DEFAULT_RUNS",
    "MAX_MAP_NODES",
    "MAX_PAGE_ITEMS",
    "MAX_TREE_NODES",
    "build_project_manager_read_model",
]

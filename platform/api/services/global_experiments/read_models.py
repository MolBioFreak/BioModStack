"""Bounded server-owned Project Manager presentation read model."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import NotFound, ValidationFailure, canonical_json
from services.global_experiments.result_surfaces import result_surface_for_receipt


MAX_TREE_NODES = 1_000
MAX_MAP_NODES = 100
DEFAULT_MAP_NODES = 50
DEFAULT_RUNS = 25
VIRTUAL_FOLDERS = ("plans", "runs", "results", "datasets", "notes", "decisions", "activity")


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
        "name": str(payload.get("name") or head.display_name),
        "objective": str(payload.get("research_objective") or payload.get("objective") or ""),
        "lifecycle_state": head.lifecycle_state,
        "head_generation": head.head_generation,
        "current_revision_id": head.current_revision_id,
        "updated_at": head.updated_at,
    }


def _decode_map_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.startswith("map:") or not cursor[4:].isdigit():
        raise ValidationFailure("map cursor is invalid")
    return int(cursor[4:])


async def build_project_manager_read_model(
    session: AsyncSession,
    *,
    project_id: str,
    focus_id: str | None = None,
    selected_node_key: str | None = None,
    map_cursor: str | None = None,
    map_limit: int = DEFAULT_MAP_NODES,
    run_limit: int = DEFAULT_RUNS,
) -> dict[str, Any]:
    if map_limit < 1 or map_limit > MAX_MAP_NODES:
        raise ValidationFailure(f"map limit must be between 1 and {MAX_MAP_NODES}")
    if run_limit < 1 or run_limit > 100:
        raise ValidationFailure("run limit must be between 1 and 100")
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
    domain_heads = []
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
    domains_by_id = {head.aggregate_id: head for head in domain_heads}
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
            tree_nodes.append(
                _tree_node(
                    node_key=domain_key,
                    node_type="domain_experiment",
                    subject_id=domain_head.aggregate_id,
                    parent_node_key=global_key,
                    label=str(domain_payloads[domain_head.aggregate_id].get("name") or domain_head.display_name),
                    lifecycle_state=domain_head.lifecycle_state,
                    has_children=True,
                    allowed_actions=["attach", "add_note", "archive"] if domain_head.lifecycle_state != "archived" else ["restore"],
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
    base_map_nodes: list[dict[str, Any]] = [
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
    base_map_edges: list[dict[str, Any]] = []
    for global_head in global_heads:
        node_key = _key("global_experiment", global_head.aggregate_id)
        base_map_nodes.append(
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
        base_map_edges.append(
            {
                "source_node_key": _key("project", project_id),
                "target_node_key": node_key,
                "lineage_mode": "contains",
                "edge_key": f"contains:{project_id}:{global_head.aggregate_id}",
                "accessible_label": "Project contains Global Experiment",
            }
        )
    focused_domains = domains_by_parent.get(focus.aggregate_id, []) if focus is not None else []
    for domain_head in focused_domains:
        node_key = _key("domain_experiment", domain_head.aggregate_id)
        base_map_nodes.append(
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
        base_map_edges.append(
            {
                "source_node_key": _key("global_experiment", str(domain_head.parent_id)),
                "target_node_key": node_key,
                "lineage_mode": "contains",
                "edge_key": f"contains:{domain_head.parent_id}:{domain_head.aggregate_id}",
                "accessible_label": "Global Experiment contains Domain Experiment",
            }
        )
    focused_domain_ids = [head.aggregate_id for head in focused_domains]
    attached_edges = []
    if focused_domain_ids:
        attached_edges = (
            await session.execute(
                select(ExperimentLineageEdge)
                .where(
                    ExperimentLineageEdge.workspace_id == project_id,
                    ExperimentLineageEdge.source_resource_id.in_(focused_domain_ids),
                    ExperimentLineageEdge.edge_mode.in_(("references", "uses_input", "produced", "validated_by")),
                )
                .order_by(ExperimentLineageEdge.created_at.desc(), ExperimentLineageEdge.id.desc())
                .limit(MAX_MAP_NODES + 1)
            )
        ).scalars().all()
    attached_receipt_ids = [edge.target_resource_id for edge in attached_edges[:MAX_MAP_NODES]]
    external_by_id: dict[str, ExperimentExternalEntityReceipt] = {}
    if attached_receipt_ids:
        external_rows = (
            await session.execute(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(attached_receipt_ids),
                    ExperimentExternalEntityReceipt.workspace_id == project_id,
                )
            )
        ).scalars().all()
        external_by_id = {row.id: row for row in external_rows}
    for edge in attached_edges[:MAX_MAP_NODES]:
        receipt = external_by_id.get(edge.target_resource_id)
        if receipt is None:
            continue
        acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
        node_key = _key("external_entity_receipt", receipt.id)
        base_map_nodes.append(
            {
                "node_key": node_key,
                "node_type": "external_entity_receipt",
                "label": str(acknowledgement.get("entity_kind") or receipt.entity_kind),
                "normalized_state": str((acknowledgement.get("metadata") or {}).get("canonical_state") or receipt.availability),
                "canonical_identity": {
                    "store_id": receipt.store_id,
                    "entity_kind": receipt.entity_kind,
                    "entity_id": receipt.entity_id,
                    "receipt_id": receipt.id,
                    "content_digest": receipt.content_digest,
                },
                "counts": {},
                "reconciliation": {
                    "state": "current" if receipt.availability == "available" else "source_unavailable",
                    "last_verified_at": acknowledgement.get("verified_at"),
                    "reason": None if receipt.availability == "available" else "source receipt is unavailable",
                },
                "allowed_actions": ["open"],
            }
        )
        base_map_edges.append(
            {
                "source_node_key": _key("domain_experiment", edge.source_resource_id),
                "target_node_key": node_key,
                "lineage_mode": edge.edge_mode,
                "edge_key": edge.edge_key,
                "accessible_label": edge.edge_mode.replace("_", " "),
            }
        )
    offset = _decode_map_cursor(map_cursor)
    map_page = base_map_nodes[offset : offset + map_limit]
    map_truncated = offset + map_limit < len(base_map_nodes)
    map_node_keys = {node["node_key"] for node in map_page}
    map_edges = [
        edge
        for edge in base_map_edges
        if edge["source_node_key"] in map_node_keys and edge["target_node_key"] in map_node_keys
    ]
    node_index = {node["node_key"]: node for node in tree_nodes}
    node_index.update({node["node_key"]: node for node in base_map_nodes})
    default_selection = _key("global_experiment", focus.aggregate_id) if focus is not None else _key("project", project_id)
    selection_key = selected_node_key or default_selection
    selected = node_index.get(selection_key)
    if selected is None:
        raise ValidationFailure("selected_node_key is unavailable in this Project")
    selected_subject_id = selected.get("subject_id") or (selected.get("canonical_identity") or {}).get("entity_id")
    canonical_surface = None
    if selected.get("node_type") == "external_entity_receipt":
        receipt_id = str((selected.get("canonical_identity") or {}).get("receipt_id") or "")
        canonical_surface = await result_surface_for_receipt(session, project_id=project_id, receipt_id=receipt_id)
    selected_payload: dict[str, Any] = {}
    if selected.get("node_type") == "project":
        selected_payload = project_payload
    elif selected.get("node_type") == "global_experiment" and selected_subject_id in global_payloads:
        selected_payload = global_payloads[str(selected_subject_id)]
    elif selected.get("node_type") == "domain_experiment" and selected_subject_id in domain_payloads:
        selected_payload = domain_payloads[str(selected_subject_id)]
    runs: list[dict[str, Any]] = []
    selected_domain_id = str(selected_subject_id) if selected.get("node_type") == "domain_experiment" else None
    if selected_domain_id is not None:
        workflows = (
            await session.execute(
                select(ExperimentAggregateHead).where(
                    ExperimentAggregateHead.workspace_id == project_id,
                    ExperimentAggregateHead.aggregate_kind == "workflow",
                    ExperimentAggregateHead.parent_id == selected_domain_id,
                )
            )
        ).scalars().all()
        workflow_ids = [head.aggregate_id for head in workflows]
        if workflow_ids:
            run_rows = (
                await session.execute(
                    select(ExperimentWorkflowRun)
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
                    .order_by(ExperimentWorkflowRun.created_at.desc())
                    .limit(run_limit)
                )
            ).scalars().all()
            run_ids = [row.resource_id for row in run_rows]
            attempts_by_run: dict[str, list[ExperimentRunAttempt]] = {}
            if run_ids:
                attempt_rows = (
                    await session.execute(
                        select(ExperimentRunAttempt)
                        .where(ExperimentRunAttempt.workflow_run_id.in_(run_ids))
                        .order_by(ExperimentRunAttempt.attempt_number)
                    )
                ).scalars().all()
                for attempt in attempt_rows:
                    attempts_by_run.setdefault(attempt.workflow_run_id, []).append(attempt)
            for row in run_rows:
                runs.append(
                    {
                        "workflow_run_id": row.resource_id,
                        "run_group_id": row.run_group_id,
                        "node_id": row.node_id,
                        "requiredness": row.requiredness,
                        "state": row.state,
                        "generation": row.generation,
                        "created_at": row.created_at,
                        "replicas": [
                            {
                                "attempt_id": attempt.resource_id,
                                "attempt_number": attempt.attempt_number,
                                "scheduler_job_id": attempt.scheduler_job_id,
                                "state": attempt.state,
                                "runtime_identity": json.loads(attempt.runtime_identity_json) if attempt.runtime_identity_json else None,
                                "terminal_receipt": json.loads(attempt.terminal_receipt_json) if attempt.terminal_receipt_json else None,
                            }
                            for attempt in attempts_by_run.get(row.resource_id, [])
                        ],
                    }
                )
    recent_activity_rows = (
        await session.execute(
            select(ExperimentAuditEvent)
            .where(ExperimentAuditEvent.workspace_id == project_id)
            .order_by(ExperimentAuditEvent.created_at.desc(), ExperimentAuditEvent.id.desc())
            .limit(10)
        )
    ).scalars().all()
    result_previews = []
    for receipt_id in attached_receipt_ids[:10]:
        try:
            result_previews.append(await result_surface_for_receipt(session, project_id=project_id, receipt_id=receipt_id))
        except ValidationFailure:
            continue
    source_receipts = list(external_by_id.values())
    digest_set = sorted({receipt.content_digest for receipt in source_receipts})
    source_digest_set_sha256 = hashlib.sha256(canonical_json(digest_set).encode("utf-8")).hexdigest()
    adapter_versions = sorted(
        {
            (
                str(json.loads(receipt.acknowledgement_json or "{}").get("verifier_id") or "unknown"),
                "1",
            )
            for receipt in source_receipts
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
        "reconciliation": {"state": "current", "last_verified_at": None, "reason": None},
        "counts": {
            "global_experiments": len(global_heads),
            "domain_experiments": len(domain_heads),
            "attached_entities": len(attached_edges),
        },
        "status_summary": {
            "projects": {project.lifecycle_state: 1},
            "global_experiments": _count_states(global_heads),
            "domain_experiments": _count_states(domain_heads),
        },
        "recent_activity": [
            {
                "id": row.id,
                "resource_id": row.resource_id,
                "event_type": row.event_type,
                "generation": row.generation,
                "payload": json.loads(row.payload_json),
                "created_at": row.created_at,
            }
            for row in recent_activity_rows
        ],
        "result_previews": result_previews,
        "pagination": {
            "map_next_cursor": f"map:{offset + map_limit}" if map_truncated else None,
            "run_next_cursor": None,
        },
        "project": _head_summary(project, project_payload),
        "tree": {"nodes": tree_nodes},
        "map": {
            "focus_node_key": _key("global_experiment", focus.aggregate_id) if focus is not None else _key("project", project_id),
            "nodes": map_page,
            "edges": map_edges,
            "truncated": map_truncated,
            "next_cursor": f"map:{offset + map_limit}" if map_truncated else None,
        },
        "selection": {
            "node_key": selection_key,
            "node_type": selected.get("node_type"),
            "title": str(selected.get("label") or selected_payload.get("name") or "Selection"),
            "subtitle": selected_payload.get("objective") or selected_payload.get("scientific_question"),
            "canonical_identity": selected.get("canonical_identity") or {"store_id": "global", "entity_id": selected_subject_id},
            "summary": selected_payload,
            "relationship": {"parent_node_key": selected.get("parent_node_key")},
            "scientific_context": selected_payload.get("domain_payload") or {},
            "reconciliation": selected.get("reconciliation") or {"state": "current", "last_verified_at": None, "reason": None},
            "available_actions": selected.get("allowed_actions") or [],
            "canonical_surface": canonical_surface,
        },
        "runs": {"items": runs, "next_cursor": None},
        "warnings": [
            "Attached entity map is truncated" for _ in [0] if len(attached_edges) > MAX_MAP_NODES
        ],
        "allowed_actions": ["create_global_experiment", "edit_project", "archive_project"] if project.lifecycle_state != "archived" else ["restore_project"],
    }


def _count_states(heads: list[ExperimentAggregateHead]) -> dict[str, int]:
    result: dict[str, int] = {}
    for head in heads:
        result[head.lifecycle_state] = result.get(head.lifecycle_state, 0) + 1
    return result


__all__ = [
    "DEFAULT_MAP_NODES",
    "DEFAULT_RUNS",
    "MAX_MAP_NODES",
    "MAX_TREE_NODES",
    "build_project_manager_read_model",
]

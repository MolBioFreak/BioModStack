"""Fail-closed immutable input authority for workflow preparation and dispatch."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentDatasetRevisionMember,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRevisionEdge,
)
from services.global_experiments.adapters import AdapterError, registry
from services.ngs_molbio_capabilities import (
    NgsMolBioCapabilityError,
    contract_registry,
    validate_domain_experiment,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\.v[1-9][0-9]*$")
_MAX_DATASETS = 128
_MAX_MEMBERS = 10_000
_MAX_TOTAL_MEMBERS = 10_000
_MAX_RECEIPT_CONTRACTS = 32
_MAX_SCHEMA_ID_LENGTH = 255
_MAX_DATASET_REVISION_CHAIN = 10_000
_ATTACHMENT_OPERATION_BY_EDGE_MODE = {
    "references": "attach_reference",
    "uses_input": "bind_input",
    "produced": "link_output",
    "validated_by": "attach_evidence",
}
_ATTACHMENT_METADATA_FIELDS = {
    "adapter_id",
    "adapter_version",
    "source_digest",
    "operation",
    "note",
}
_PROJECT_STATUSES = {"draft", "active", "on_hold", "completed", "archived"}
_EXPERIMENT_STATUSES = {
    "draft",
    "planned",
    "active",
    "analysis",
    "review",
    "completed",
    "blocked",
    "archived",
}
_DOMAIN_KINDS = {"protein_in_silico", "ngs_molbio"}
_HIERARCHY_SCHEMA_VERSIONS = {
    "bms.project.v1": "1",
    "bms.project.v2": "2",
    "bms.global-experiment.v1": "1",
    "bms.global-experiment.v2": "2",
    "bms.domain-experiment.v1": "1",
    "bms.domain-experiment.v2": "1",
    "bms.domain-experiment.v3": "2",
    "bms.domain-experiment.v4": "3",
}


class PreparationInputAuthorityError(ValueError):
    """Immutable preparation input authority is absent, ambiguous, or divergent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PreparationInputAuthorityError(f"{label} is not a lowercase SHA-256")
    return value


def _parse_canonical(raw: Any, label: str, expected_type: type[Any]) -> Any:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PreparationInputAuthorityError(f"{label} is malformed") from exc
    if not isinstance(value, expected_type) or _canonical(value) != raw:
        raise PreparationInputAuthorityError(f"{label} is not closed canonical JSON")
    return value


def _validate_hierarchy_producer_payload(
    aggregate_kind: str,
    payload: dict[str, Any],
) -> None:
    """Mirror experiment_services hierarchy admission without importing it circularly."""
    if not isinstance(payload, dict):
        raise PreparationInputAuthorityError("aggregate payload must be an object")
    allowed_schemas = {
        "workspace": {"bms.project.v1", "bms.project.v2"},
        "experiment": {"bms.global-experiment.v1", "bms.global-experiment.v2"},
        "domain_experiment": {
            "bms.domain-experiment.v1",
            "bms.domain-experiment.v2",
            "bms.domain-experiment.v3",
            "bms.domain-experiment.v4",
        },
    }[aggregate_kind]
    if payload.get("schema") not in allowed_schemas:
        raise PreparationInputAuthorityError(
            f"{aggregate_kind} payload schema is not a supported immutable hierarchy contract"
        )
    required = {
        "workspace": {
            "name",
            "description",
            "research_objective",
            "status",
            "needs_metadata_review",
        },
        "experiment": {
            "name",
            "objective",
            "scientific_question",
            "description",
            "status",
            "priority",
            "success_criteria",
            "needs_metadata_review",
        },
        "domain_experiment": {
            "domain_kind",
            "domain_contract_version",
            "name",
            "objective",
            "status",
            "domain_payload",
        },
    }[aggregate_kind]
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise PreparationInputAuthorityError(
            f"{aggregate_kind} payload missing required fields: {', '.join(missing)}"
        )
    statuses = _PROJECT_STATUSES if aggregate_kind == "workspace" else _EXPERIMENT_STATUSES
    if payload.get("status") not in statuses:
        raise PreparationInputAuthorityError(f"invalid {aggregate_kind} lifecycle status")
    if aggregate_kind == "experiment":
        if payload.get("status") == "active":
            criteria = payload.get("success_criteria")
            if not isinstance(criteria, list) or not criteria:
                raise PreparationInputAuthorityError(
                    "active global experiments require success criteria"
                )
        if payload.get("status") == "completed" and (
            not str(payload.get("review_summary") or "").strip()
            or not str(payload.get("conclusion") or "").strip()
        ):
            raise PreparationInputAuthorityError(
                "completed global experiments require review_summary and conclusion"
            )
    if aggregate_kind != "domain_experiment":
        return

    domain_kind = payload.get("domain_kind")
    if domain_kind not in _DOMAIN_KINDS:
        raise PreparationInputAuthorityError(
            "domain_kind must be protein_in_silico or ngs_molbio"
        )
    contract_version = payload.get("domain_contract_version")
    if contract_version in {"2", "3"}:
        expected_schema = {
            "2": "bms.domain-experiment.v2",
            "3": "bms.domain-experiment.v4",
        }[contract_version]
        if payload.get("schema") != expected_schema:
            raise PreparationInputAuthorityError(
                f"Domain contract v{contract_version} requires {expected_schema}"
            )
        try:
            validate_domain_experiment(payload)
        except NgsMolBioCapabilityError as exc:
            raise PreparationInputAuthorityError(str(exc)) from exc
        return
    if (
        payload.get("domain_contract_version") != "1"
        or payload.get("schema") != "bms.domain-experiment.v1"
    ):
        raise PreparationInputAuthorityError("unsupported domain_contract_version")
    domain_payload = payload.get("domain_payload")
    if not isinstance(domain_payload, dict):
        raise PreparationInputAuthorityError("domain_payload must be an object")
    if domain_kind == "ngs_molbio":
        if domain_payload != {"schema": "bms.ngs-molbio-experiment.v1"}:
            raise PreparationInputAuthorityError(
                "ngs_molbio domain_payload has unsupported or unknown fields"
            )
        return

    expected_keys = {
        "schema",
        "experiment_mode",
        "targets",
        "scientific_objective",
        "design_constraints",
        "planned_capabilities",
        "comparison_groups",
        "validation_strategy",
    }
    if set(domain_payload) != expected_keys:
        raise PreparationInputAuthorityError(
            "protein_in_silico domain_payload fields do not match the frozen contract"
        )
    if domain_payload.get("schema") != "bms.protein-in-silico-experiment.v1":
        raise PreparationInputAuthorityError(
            "protein_in_silico domain_payload schema is invalid"
        )
    if domain_payload.get("experiment_mode") not in {
        "exploration",
        "design",
        "redesign",
        "prediction",
        "validation",
        "comparison",
        "simulation",
        "analysis",
    }:
        raise PreparationInputAuthorityError(
            "protein_in_silico experiment_mode is invalid"
        )
    targets = domain_payload.get("targets")
    if not isinstance(targets, list):
        raise PreparationInputAuthorityError("protein_in_silico targets must be an array")
    target_keys = {"target_id", "label", "entity_receipt_ids", "role"}
    target_roles = {
        "target",
        "binder",
        "partner",
        "template",
        "reference",
        "control",
        "other",
    }
    for target in targets:
        if not isinstance(target, dict) or set(target) != target_keys:
            raise PreparationInputAuthorityError(
                "protein_in_silico target fields do not match the frozen contract"
            )
        if (
            not str(target.get("target_id") or "").strip()
            or target.get("role") not in target_roles
        ):
            raise PreparationInputAuthorityError(
                "protein_in_silico target identity or role is invalid"
            )
        receipt_ids = target.get("entity_receipt_ids")
        if not isinstance(receipt_ids, list) or any(
            not isinstance(value, str) or not value for value in receipt_ids
        ):
            raise PreparationInputAuthorityError(
                "protein_in_silico target receipt IDs are invalid"
            )
    if not isinstance(domain_payload.get("scientific_objective"), str):
        raise PreparationInputAuthorityError(
            "protein_in_silico scientific_objective must be a string"
        )
    if domain_payload.get("design_constraints") != []:
        raise PreparationInputAuthorityError(
            "Protein constraints are unavailable because the closed payload registry is empty"
        )
    comparison_groups = domain_payload.get("comparison_groups")
    if not isinstance(comparison_groups, list) or any(
        not isinstance(value, dict) for value in comparison_groups
    ):
        raise PreparationInputAuthorityError(
            "protein_in_silico comparison_groups must contain objects"
        )
    for field in ("planned_capabilities", "validation_strategy"):
        values = domain_payload.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise PreparationInputAuthorityError(
                f"protein_in_silico {field} must contain non-empty capability IDs"
            )


def _normalized_receipt_contracts(
    receipt_contracts: list[str] | None,
    *,
    required: bool,
) -> list[str]:
    if receipt_contracts is None:
        normalized: list[str] = []
    elif isinstance(receipt_contracts, list):
        normalized = list(receipt_contracts)
    else:
        raise PreparationInputAuthorityError("workflow receipt contracts must be a closed list")
    if (
        len(normalized) > _MAX_RECEIPT_CONTRACTS
        or (required and not normalized)
        or any(
            not isinstance(value, str)
            or len(value) > _MAX_SCHEMA_ID_LENGTH
            or _SCHEMA_ID.fullmatch(value) is None
            for value in normalized
        )
        or len(normalized) != len(set(normalized))
    ):
        raise PreparationInputAuthorityError(
            "workflow receipt contracts are empty, duplicated, unbounded, or not canonical schema IDs"
        )
    return normalized


def _unique_registry_row(document: Mapping[str, Any], key: str, value: str, label: str) -> dict[str, Any]:
    rows = [row for row in document.get("entries", []) if isinstance(row, dict) and row.get(key) == value]
    if len(rows) != 1:
        raise PreparationInputAuthorityError(f"{label} is absent or ambiguous: {value}")
    return rows[0]


def _registry_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return contract_registry("dataset"), contract_registry("adapter")
    except NgsMolBioCapabilityError as exc:
        raise PreparationInputAuthorityError("NGS/MolBio input contract authority is unavailable") from exc


def _dataset_contract(dataset_registry: Mapping[str, Any], dataset_kind: Any) -> dict[str, Any]:
    if not isinstance(dataset_kind, str) or not dataset_kind:
        raise PreparationInputAuthorityError("Dataset kind authority is null")
    record = _unique_registry_row(dataset_registry, "dataset_kind", dataset_kind, "Dataset kind contract")
    if (
        not dataset_kind.startswith("ngs_molbio.")
        or record.get("dataset_kind") != dataset_kind
        or record.get("enabled") is not True
        or record.get("owner_contract_state") != "closed"
        or record.get("allowed_domain_kinds") != ["ngs_molbio"]
        or not isinstance(record.get("allowed_members"), list)
        or not isinstance(record.get("minimum_members"), int)
        or isinstance(record.get("minimum_members"), bool)
        or not isinstance(record.get("maximum_members"), int)
        or isinstance(record.get("maximum_members"), bool)
        or record["minimum_members"] < 0
        or record["maximum_members"] < record["minimum_members"]
        or record["maximum_members"] > _MAX_MEMBERS
    ):
        raise PreparationInputAuthorityError("unsupported_dataset_kind")
    return record


def _adapter_contract(adapter_registry: Mapping[str, Any], adapter_id: Any, entity_kind: Any) -> dict[str, Any]:
    if not isinstance(adapter_id, str) or not adapter_id:
        raise PreparationInputAuthorityError("receipt has no declared producer-native adapter")
    record = _unique_registry_row(adapter_registry, "adapter_id", adapter_id, "adapter contract")
    if (
        record.get("baseline_state") != "present"
        or record.get("entity_kind") != entity_kind
        or not isinstance(record.get("adapter_version"), int)
        or isinstance(record.get("adapter_version"), bool)
        or not isinstance(record.get("implementation_owner"), str)
        or not record["implementation_owner"]
        or record.get("reopen_contract", {}).get("head_resolution_forbidden") is not True
        or not isinstance(record.get("source_contracts"), list)
        or not record["source_contracts"]
    ):
        raise PreparationInputAuthorityError("receipt adapter contract is not exact and closed")
    try:
        implementation = registry.get(adapter_id)
    except AdapterError as exc:
        raise PreparationInputAuthorityError("declared receipt adapter is not installed") from exc
    if (
        implementation.adapter_id != adapter_id
        or implementation.adapter_version != record["adapter_version"]
        or implementation.entity_kind != entity_kind
    ):
        raise PreparationInputAuthorityError("installed receipt adapter diverges from its declared contract")
    return record


def _source_contract(adapter_record: Mapping[str, Any], fresh_metadata: Mapping[str, Any]) -> dict[str, Any]:
    source_schema = fresh_metadata.get("source_schema")
    matches: list[dict[str, Any]] = []
    for row in adapter_record.get("source_contracts", []):
        if not isinstance(row, dict):
            continue
        presence = row.get("schema_presence")
        if presence == "required" and source_schema == row.get("source_schema"):
            matches.append(row)
        elif presence == "absent" and source_schema is None:
            matches.append(row)
    if len(matches) != 1:
        raise PreparationInputAuthorityError("fresh receipt does not resolve one declared source schema contract")
    return matches[0]


async def _workflow_scope(
    session: AsyncSession,
    workflow_revision_id: str,
) -> tuple[ExperimentRevision, ExperimentAggregateHead, str, str | None, str | None]:
    revision = await session.get(ExperimentRevision, workflow_revision_id)
    head = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
    resource = await session.get(ExperimentResource, head.aggregate_id if head else "")
    if (
        revision is None
        or head is None
        or head.aggregate_kind != "workflow"
        or resource is None
        or resource.kind != "workflow"
        or resource.id != head.aggregate_id
        or resource.workspace_id != head.workspace_id
        or revision.subject_id != head.aggregate_id
    ):
        raise PreparationInputAuthorityError("workflow input scope authority is unavailable")
    project_id = str(head.workspace_id)
    domain = await session.get(ExperimentAggregateHead, head.parent_id or "")
    if domain is None or domain.aggregate_kind != "domain_experiment" or domain.workspace_id != project_id:
        return revision, head, project_id, None, None
    experiment = await session.get(ExperimentAggregateHead, domain.parent_id or "")
    if (
        experiment is None
        or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id
        or experiment.parent_id != project_id
    ):
        raise PreparationInputAuthorityError("workflow Global Experiment parent authority is unavailable")
    return revision, head, project_id, str(experiment.aggregate_id), str(domain.aggregate_id)


def _hierarchy_revision_reference_ids(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    receipt_references: list[tuple[str, str]] = []
    dataset_references: list[tuple[str, str]] = []
    dataset_revision_references: list[tuple[str, str]] = []

    def collect(
        source: Mapping[str, Any],
        field: str,
        role: str,
        target: list[tuple[str, str]],
    ) -> None:
        values = source.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise PreparationInputAuthorityError(
                "hierarchy revision dependency identities are not a closed list of resource IDs"
            )
        target.extend((role, value) for value in values)

    schema = payload.get("schema")
    if schema in {"bms.global-experiment.v1", "bms.global-experiment.v2"}:
        collect(payload, "shared_source_receipt_ids", "shared_source_receipt", receipt_references)
        collect(payload, "shared_dataset_ids", "shared_dataset", dataset_references)
    elif schema in {
        "bms.domain-experiment.v1",
        "bms.domain-experiment.v2",
        "bms.domain-experiment.v3",
        "bms.domain-experiment.v4",
    }:
        collect(payload, "source_receipt_ids", "source_receipt", receipt_references)
        if schema == "bms.domain-experiment.v1":
            collect(payload, "dataset_ids", "dataset", dataset_references)
        else:
            collect(payload, "dataset_revision_ids", "dataset_revision", dataset_revision_references)
        domain_payload = payload.get("domain_payload")
        if isinstance(domain_payload, Mapping):
            targets = domain_payload.get("targets", [])
            if not isinstance(targets, list):
                raise PreparationInputAuthorityError("hierarchy revision target dependencies are malformed")
            for target_index, target_payload in enumerate(targets):
                if isinstance(target_payload, Mapping):
                    if domain_payload.get("schema") == "bms.protein-in-silico-experiment.v3":
                        collect(
                            target_payload,
                            "source_receipt_ids",
                            f"target_source_receipt:{target_index}",
                            receipt_references,
                        )
                    else:
                        collect(
                            target_payload,
                            "entity_receipt_ids",
                            f"target_entity_receipt:{target_index}",
                            receipt_references,
                        )

    return receipt_references + dataset_references + dataset_revision_references


async def _current_hierarchy_revision_authority(
    session: AsyncSession,
    *,
    head: ExperimentAggregateHead,
    project_id: str,
    expected_schema_names: frozenset[str],
) -> dict[str, Any]:
    revision_id = head.current_revision_id
    revision = await session.get(ExperimentRevision, revision_id or "")
    revision_resource = await session.get(ExperimentResource, revision_id or "")
    if (
        not revision_id
        or revision is None
        or revision_resource is None
        or revision.resource_id != revision_id
        or revision.subject_id != head.aggregate_id
        or head.current_revision_id != revision.resource_id
        or not isinstance(head.head_generation, int)
        or isinstance(head.head_generation, bool)
        or head.head_generation < 1
        or (
            head.aggregate_kind != "workspace"
            and revision.revision_number != head.head_generation
        )
        or revision.schema_name not in expected_schema_names
        or revision.schema_version != _HIERARCHY_SCHEMA_VERSIONS.get(revision.schema_name)
        or revision_resource.id != revision.resource_id
        or revision_resource.kind != "revision"
        or revision_resource.workspace_id != project_id
        or revision_resource.lifecycle_owner_id != head.aggregate_id
        or revision_resource.archived_at is not None
    ):
        raise PreparationInputAuthorityError("current hierarchy revision is not exact subject-bound authority")

    payload = _parse_canonical(revision.canonical_payload, "current hierarchy revision", dict)
    _validate_hierarchy_producer_payload(head.aggregate_kind, payload)
    payload_sha256 = _require_sha256(revision.payload_sha256, "current hierarchy revision payload digest")
    dependency_sha256 = _require_sha256(
        revision.dependency_graph_sha256,
        "current hierarchy revision dependency digest",
    )
    if (
        payload.get("schema") != revision.schema_name
        or payload.get("status") != head.lifecycle_state
        or hashlib.sha256(revision.canonical_payload.encode("utf-8")).hexdigest() != payload_sha256
    ):
        raise PreparationInputAuthorityError("current hierarchy revision bytes or schema metadata diverge")

    reference_ids = _hierarchy_revision_reference_ids(payload)
    rows = list(
        (
            await session.scalars(
                select(ExperimentRevisionEdge)
                .where(ExperimentRevisionEdge.revision_id == revision.resource_id)
                .order_by(
                    ExperimentRevisionEdge.role,
                    ExperimentRevisionEdge.ordinal,
                    ExperimentRevisionEdge.target_resource_id,
                )
            )
        ).all()
    )
    rows_by_identity = {
        (row.role, row.ordinal, row.target_resource_id): row
        for row in rows
    }
    if len(rows_by_identity) != len(rows):
        raise PreparationInputAuthorityError("current hierarchy revision dependencies are duplicated")

    ordinals: dict[str, int] = {}
    references: list[dict[str, Any]] = []
    expected_identities: set[tuple[str, int, str]] = set()
    for role, target_resource_id in reference_ids:
        ordinal = ordinals.get(role, 0)
        ordinals[role] = ordinal + 1
        identity = (role, ordinal, target_resource_id)
        if identity in expected_identities:
            raise PreparationInputAuthorityError("current hierarchy revision dependency identity is duplicated")
        expected_identities.add(identity)
        row = rows_by_identity.get(identity)
        if (
            row is None
            or row.revision_id != revision.resource_id
            or row.metadata_json != _canonical({"authority": "server_resolved"})
        ):
            raise PreparationInputAuthorityError("current hierarchy revision dependency linkage diverges")
        expected_sha256 = _require_sha256(
            row.expected_sha256,
            "current hierarchy revision dependency target digest",
        )
        references.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": target_resource_id,
                "expected_sha256": expected_sha256,
            }
        )
    if set(rows_by_identity) != expected_identities:
        raise PreparationInputAuthorityError("current hierarchy revision dependencies are missing or extraneous")

    expected_graph = _canonical(
        {
            "nodes": payload.get("nodes", []),
            "edges": payload.get("edges", []),
            "references": references,
        }
    )
    if hashlib.sha256(expected_graph.encode("utf-8")).hexdigest() != dependency_sha256:
        raise PreparationInputAuthorityError("current hierarchy revision dependency digest diverges")
    return payload


async def _active_input_hierarchy(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str | None,
    domain_id: str | None,
) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, ExperimentAggregateHead]:
    if global_experiment_id is None or domain_id is None:
        raise PreparationInputAuthorityError("preparation inputs require an exact Project/Global Experiment/Domain scope")
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_id)
    heads = (project, experiment, domain)
    if (
        project is None
        or project.aggregate_id != project_id
        or project.aggregate_kind != "workspace"
        or project.workspace_id != project_id
        or project.parent_id is not None
        or experiment is None
        or experiment.aggregate_id != global_experiment_id
        or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id
        or experiment.parent_id != project_id
        or domain is None
        or domain.aggregate_id != domain_id
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id
        or domain.parent_id != global_experiment_id
        or any(head.lifecycle_state != "active" or not head.current_revision_id for head in heads if head is not None)
    ):
        raise PreparationInputAuthorityError("preparation input ancestors are not exact and active")

    resource_contracts = (
        (project, "workspace", None, None),
        (experiment, "experiment", project_id, project_id),
        (domain, "domain_experiment", project_id, global_experiment_id),
    )
    for head, expected_kind, expected_workspace_id, expected_owner_id in resource_contracts:
        resource = await session.get(ExperimentResource, head.aggregate_id)
        if (
            resource is None
            or resource.id != head.aggregate_id
            or resource.kind != expected_kind
            or resource.workspace_id != expected_workspace_id
            or resource.lifecycle_owner_id != expected_owner_id
            or resource.archived_at is not None
        ):
            raise PreparationInputAuthorityError("preparation input ancestor resource authority is unavailable")

    await _current_hierarchy_revision_authority(
        session,
        head=project,
        project_id=project_id,
        expected_schema_names=frozenset({"bms.project.v1", "bms.project.v2"}),
    )
    await _current_hierarchy_revision_authority(
        session,
        head=experiment,
        project_id=project_id,
        expected_schema_names=frozenset({"bms.global-experiment.v1", "bms.global-experiment.v2"}),
    )
    domain_payload = await _current_hierarchy_revision_authority(
        session,
        head=domain,
        project_id=project_id,
        expected_schema_names=frozenset({
            "bms.domain-experiment.v1",
            "bms.domain-experiment.v2",
            "bms.domain-experiment.v3",
            "bms.domain-experiment.v4",
        }),
    )
    domain_contract = (
        domain_payload.get("domain_kind"),
        domain_payload.get("domain_contract_version"),
        domain_payload.get("schema"),
    )
    if domain_contract not in {
        ("ngs_molbio", "2", "bms.domain-experiment.v2"),
        ("protein_in_silico", "3", "bms.domain-experiment.v4"),
    }:
        raise PreparationInputAuthorityError(
            "preparation input Domain is not an exact supported package-local authority"
        )
    return project, experiment, domain


async def _receipt_domain_owner(
    session: AsyncSession,
    *,
    receipt: ExperimentExternalEntityReceipt,
    project_id: str,
    expected_domain_id: str,
    adapter_record: Mapping[str, Any],
) -> str:
    edges = list(
        (
            await session.scalars(
                select(ExperimentLineageEdge)
                .join(
                    ExperimentAggregateHead,
                    ExperimentLineageEdge.source_resource_id == ExperimentAggregateHead.aggregate_id,
                )
                .where(
                    ExperimentLineageEdge.workspace_id == project_id,
                    ExperimentLineageEdge.target_resource_id == receipt.id,
                    ExperimentAggregateHead.aggregate_kind == "domain_experiment",
                    ExperimentAggregateHead.workspace_id == project_id,
                )
                .order_by(ExperimentLineageEdge.id)
            )
        ).all()
    )
    if len(edges) != 1:
        raise PreparationInputAuthorityError("receipt does not have exactly one canonical Domain attachment")

    edge = edges[0]
    metadata = _parse_canonical(edge.metadata_json, "receipt attachment metadata", dict)
    expected_operation = _ATTACHMENT_OPERATION_BY_EDGE_MODE.get(edge.edge_mode)
    note = metadata.get("note")
    if (
        edge.workspace_id != project_id
        or edge.source_resource_id != expected_domain_id
        or edge.target_resource_id != receipt.id
        or expected_operation is None
        or edge.edge_key
        != f"attachment:{receipt.verification_authority}:{receipt.id}:{edge.edge_mode}"
        or set(metadata) != _ATTACHMENT_METADATA_FIELDS
        or metadata.get("adapter_id") != receipt.verification_authority
        or metadata.get("adapter_version") != adapter_record.get("adapter_version")
        or metadata.get("source_digest") != receipt.content_digest
        or metadata.get("operation") != expected_operation
        or (
            note is not None
            and (not isinstance(note, str) or not note or note.strip() != note)
        )
    ):
        raise PreparationInputAuthorityError("receipt Domain attachment authority is not exact and canonical")
    return expected_domain_id


async def _fresh_receipt_authority(
    session: AsyncSession,
    core_session: AsyncSession | None,
    *,
    receipt_id: str,
    project_id: str,
    domain_id: str,
    adapter_registry: Mapping[str, Any],
    required_adapter_id: str | None = None,
    required_entity_kind: str | None = None,
    required_role: str | None = None,
    adapter_allowed_roles: set[str] | None = None,
    pinned_receipt_contracts: list[str] | None = None,
) -> dict[str, Any]:
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    resource = await session.get(ExperimentResource, receipt_id)
    if (
        receipt is None
        or resource is None
        or resource.kind != "external_entity_receipt"
        or resource.id != receipt.id
        or receipt.resource_id != receipt.id
        or receipt.workspace_id != project_id
        or resource.workspace_id != project_id
        or resource.archived_at is not None
        or receipt.availability != "available"
        or receipt.verification_authority in {"legacy_unverified", "caller_unverified"}
    ):
        raise PreparationInputAuthorityError("persisted producer-native receipt authority is unavailable")
    if required_adapter_id is not None and receipt.verification_authority != required_adapter_id:
        raise PreparationInputAuthorityError("generic or foreign receipt adapter cannot substitute for the declared member adapter")
    if required_entity_kind is not None and receipt.entity_kind != required_entity_kind:
        raise PreparationInputAuthorityError("receipt kind diverges from the Dataset contract")
    if required_role is not None and (adapter_allowed_roles is None or required_role not in adapter_allowed_roles):
        raise PreparationInputAuthorityError("Dataset member role is not declared for its exact adapter")

    adapter_record = _adapter_contract(adapter_registry, receipt.verification_authority, receipt.entity_kind)
    if required_role is not None and required_role not in set(adapter_record.get("allowed_dataset_roles") or []):
        raise PreparationInputAuthorityError("Dataset member role is not permitted by the adapter contract")
    if core_session is None:
        raise PreparationInputAuthorityError("fresh producer-native receipt revalidation requires the core source session")
    adapter = registry.get(receipt.verification_authority)
    try:
        fresh = await adapter.verify(core_session, receipt.entity_id)
    except AdapterError as exc:
        raise PreparationInputAuthorityError(f"producer-native receipt revalidation failed: {exc}") from exc
    if not isinstance(fresh, dict):
        raise PreparationInputAuthorityError("producer-native adapter returned no closed receipt")
    fresh_metadata = fresh.get("metadata")
    if not isinstance(fresh_metadata, dict):
        raise PreparationInputAuthorityError("fresh producer-native receipt metadata is malformed")
    source_contract = _source_contract(adapter_record, fresh_metadata)
    if pinned_receipt_contracts is not None:
        source_schema = fresh_metadata.get("source_schema")
        pinned_matches = [value for value in pinned_receipt_contracts if value == source_schema]
        adapter_matches = [
            row
            for row in adapter_record.get("source_contracts", [])
            if isinstance(row, dict)
            and row.get("schema_presence") == "required"
            and row.get("source_schema") == source_schema
        ]
        if (
            not isinstance(source_schema, str)
            or len(source_schema) > _MAX_SCHEMA_ID_LENGTH
            or _SCHEMA_ID.fullmatch(source_schema) is None
            or len(pinned_matches) != 1
            or len(adapter_matches) != 1
            or source_contract != adapter_matches[0]
        ):
            raise PreparationInputAuthorityError(
                "fresh receipt producer contract is undeclared, ambiguous, generic, legacy, or foreign"
            )
    acknowledgement = _parse_canonical(receipt.acknowledgement_json, "persisted receipt acknowledgement", dict)
    exact_fields = (
        "schema",
        "store_id",
        "entity_kind",
        "entity_id",
        "entity_revision_id",
        "content_digest",
        "contract_digest",
        "verifier_id",
        "availability",
        "reopen_uri",
    )
    if (
        fresh.get("schema") != "bms.global.external-entity-receipt.v1"
        or any(fresh.get(field) != acknowledgement.get(field) for field in exact_fields)
        or fresh.get("store_id") != receipt.store_id
        or fresh.get("entity_kind") != receipt.entity_kind
        or fresh.get("entity_id") != receipt.entity_id
        or str(fresh.get("entity_revision_id")) != receipt.generation_or_revision
        or fresh.get("content_digest") != receipt.content_digest
        or fresh.get("availability") != receipt.availability
        or fresh.get("verifier_id") != receipt.verification_authority
        or fresh_metadata.get("adapter_version") != adapter_record["adapter_version"]
    ):
        raise PreparationInputAuthorityError("fresh producer-native receipt diverges from persisted immutable authority")
    content_digest = _require_sha256(fresh.get("content_digest"), "fresh receipt content digest")
    contract_digest = _require_sha256(fresh.get("contract_digest"), "fresh receipt contract digest")
    stable_metadata_fields = (
        "adapter_version",
        "source_schema",
        "global_domain_experiment_id",
        "native_entity_id",
        "native_revision_or_generation",
    )
    persisted_metadata = acknowledgement.get("metadata")
    if not isinstance(persisted_metadata, dict) or any(
        fresh_metadata.get(field) != persisted_metadata.get(field) for field in stable_metadata_fields
    ):
        raise PreparationInputAuthorityError("persisted receipt metadata diverges from fresh immutable authority")
    owner = await _receipt_domain_owner(
        session,
        receipt=receipt,
        project_id=project_id,
        expected_domain_id=domain_id,
        adapter_record=adapter_record,
    )
    fresh_owner = fresh_metadata.get("global_domain_experiment_id")
    if fresh_owner is not None and fresh_owner != owner:
        raise PreparationInputAuthorityError("fresh producer-native receipt names a foreign Domain owner")
    native_member_receipt_id = fresh_metadata.get("native_member_receipt_id")
    if adapter_record.get("contract_class") == "local_member_adapter" and (
        fresh_owner != owner or not isinstance(native_member_receipt_id, str) or not native_member_receipt_id
    ):
        raise PreparationInputAuthorityError("local member adapter omitted exact persisted Domain ownership authority")
    return {
        "schema": "bms.preparation-input-receipt-authority.v1",
        "receipt_id": receipt.id,
        "adapter_id": receipt.verification_authority,
        "adapter_version": adapter_record["adapter_version"],
        "adapter_contract_sha256": _digest(adapter_record),
        "store_id": receipt.store_id,
        "entity_kind": receipt.entity_kind,
        "entity_id": receipt.entity_id,
        "native_revision_or_generation": receipt.generation_or_revision,
        "content_sha256": content_digest,
        "contract_sha256": contract_digest,
        "availability": "available",
        "reopen_uri": fresh["reopen_uri"],
        "source_schema": fresh_metadata.get("source_schema"),
        "source_contract_sha256": _digest(source_contract),
        "global_domain_experiment_id": owner,
        "native_member_receipt_id": native_member_receipt_id,
    }


def _validate_dataset_revision_bytes(revision: ExperimentRevision) -> dict[str, Any]:
    payload = _parse_canonical(revision.canonical_payload, "Dataset revision payload", dict)
    expected_schema_name = str(payload.get("schema") or "bms.workflow.dataset.v1")
    expected_schema_version = str(payload.get("contract_version") or "1")
    expected_graph = _canonical(
        {"nodes": payload.get("nodes", []), "edges": payload.get("edges", []), "references": []}
    )
    payload_sha256 = _require_sha256(revision.payload_sha256, "Dataset revision payload digest")
    dependency_sha256 = _require_sha256(
        revision.dependency_graph_sha256,
        "Dataset revision dependency digest",
    )
    if (
        revision.schema_name != expected_schema_name
        or revision.schema_version != expected_schema_version
        or hashlib.sha256(revision.canonical_payload.encode("utf-8")).hexdigest() != payload_sha256
        or hashlib.sha256(expected_graph.encode("utf-8")).hexdigest() != dependency_sha256
    ):
        raise PreparationInputAuthorityError("Dataset revision bytes, schema, or dependency digest diverge")
    return payload


async def _selected_dataset_revision_chain(
    session: AsyncSession,
    *,
    head: ExperimentAggregateHead,
    selected_revision: ExperimentRevision,
) -> dict[str, Any]:
    selected_number = selected_revision.revision_number
    head_number = head.head_generation
    chain_length = head_number - selected_number + 1
    if chain_length < 1 or chain_length > _MAX_DATASET_REVISION_CHAIN:
        raise PreparationInputAuthorityError("Dataset revision chain is absent or exceeds its traversal bound")

    minimum_number = selected_number - 1 if selected_number > 1 else 1
    revisions = list(
        (
            await session.scalars(
                select(ExperimentRevision)
                .where(
                    ExperimentRevision.subject_id == head.aggregate_id,
                    ExperimentRevision.revision_number >= minimum_number,
                    ExperimentRevision.revision_number <= head_number,
                )
                .order_by(ExperimentRevision.revision_number)
                .limit(_MAX_DATASET_REVISION_CHAIN + 2)
            )
        ).all()
    )
    by_number: dict[int, list[ExperimentRevision]] = {}
    for candidate in revisions:
        by_number.setdefault(candidate.revision_number, []).append(candidate)

    selected_payload: dict[str, Any] | None = None
    seen_revision_ids: set[str] = set()
    for expected_number in range(head_number, selected_number - 1, -1):
        matches = by_number.get(expected_number, [])
        if len(matches) != 1:
            raise PreparationInputAuthorityError(
                "Dataset revision chain contains a missing or forked subject revision number"
            )
        candidate = matches[0]
        if (
            candidate.subject_id != head.aggregate_id
            or not isinstance(candidate.revision_number, int)
            or isinstance(candidate.revision_number, bool)
            or candidate.revision_number != expected_number
            or candidate.resource_id in seen_revision_ids
            or (expected_number == head_number and candidate.resource_id != head.current_revision_id)
            or (
                expected_number == selected_number
                and candidate.resource_id != selected_revision.resource_id
            )
        ):
            raise PreparationInputAuthorityError("Dataset revision chain diverges from the selected current lineage")
        seen_revision_ids.add(candidate.resource_id)
        payload = _validate_dataset_revision_bytes(candidate)
        if expected_number == selected_number:
            selected_payload = payload

        if expected_number == 1:
            if candidate.parent_revision_id is not None:
                raise PreparationInputAuthorityError("first Dataset revision has an invalid parent revision")
            continue
        parent_matches = by_number.get(expected_number - 1, [])
        if len(parent_matches) != 1:
            raise PreparationInputAuthorityError(
                "Dataset revision chain contains a missing or forked parent revision number"
            )
        parent = parent_matches[0]
        if (
            parent.subject_id != head.aggregate_id
            or not isinstance(parent.revision_number, int)
            or isinstance(parent.revision_number, bool)
            or parent.revision_number != expected_number - 1
            or candidate.parent_revision_id != parent.resource_id
            or parent.resource_id == candidate.resource_id
            or parent.resource_id in seen_revision_ids
        ):
            raise PreparationInputAuthorityError("Dataset revision chain parent authority is inconsistent")

    if selected_payload is None:
        raise PreparationInputAuthorityError("selected Dataset revision is not on the current revision chain")
    return selected_payload


async def _dataset_authority(
    session: AsyncSession,
    core_session: AsyncSession | None,
    *,
    revision_id: str,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    dataset_registry: Mapping[str, Any],
    adapter_registry: Mapping[str, Any],
) -> dict[str, Any]:
    revision = await session.get(ExperimentRevision, revision_id)
    revision_resource = await session.get(ExperimentResource, revision_id)
    head = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
    dataset_resource = await session.get(ExperimentResource, head.aggregate_id if head else "")
    if (
        revision is None
        or revision_resource is None
        or revision_resource.id != revision.resource_id
        or revision_resource.kind != "revision"
        or revision_resource.workspace_id != project_id
        or revision_resource.lifecycle_owner_id != revision.subject_id
        or revision_resource.archived_at is not None
        or head is None
        or head.aggregate_kind != "dataset"
        or head.workspace_id != project_id
        or head.parent_id != domain_id
        or head.lifecycle_state != "active"
        or not head.current_revision_id
        or dataset_resource is None
        or dataset_resource.id != head.aggregate_id
        or dataset_resource.kind != "dataset"
        or dataset_resource.workspace_id != project_id
        or dataset_resource.lifecycle_owner_id != domain_id
        or dataset_resource.archived_at is not None
        or revision.subject_id != head.aggregate_id
        or not isinstance(revision.revision_number, int)
        or isinstance(revision.revision_number, bool)
        or revision.revision_number < 1
        or not isinstance(head.head_generation, int)
        or isinstance(head.head_generation, bool)
        or revision.revision_number > head.head_generation
    ):
        raise PreparationInputAuthorityError("Dataset revision does not have exact active parent authority")
    payload = await _selected_dataset_revision_chain(
        session,
        head=head,
        selected_revision=revision,
    )
    provenance = _parse_canonical(revision.provenance_json, "Dataset revision provenance", dict)
    del provenance
    if payload.get("schema") != "bms.dataset-revision.v1" or set(payload) != {"schema", "change_summary", "members"}:
        raise PreparationInputAuthorityError("Dataset revision payload is not the closed NGS/MolBio Dataset contract")
    payload_members = payload.get("members")
    if not isinstance(payload_members, list) or len(payload_members) > _MAX_MEMBERS:
        raise PreparationInputAuthorityError("Dataset revision member cardinality is invalid")

    dataset_contract = _dataset_contract(dataset_registry, head.dataset_kind)
    if not dataset_contract["minimum_members"] <= len(payload_members) <= dataset_contract["maximum_members"]:
        raise PreparationInputAuthorityError("Dataset member cardinality violates its declared registry contract")
    member_contracts: dict[str, dict[str, Any]] = {}
    for row in dataset_contract["allowed_members"]:
        adapter_id = row.get("adapter_id") if isinstance(row, dict) else None
        if not isinstance(adapter_id, str) or adapter_id in member_contracts:
            raise PreparationInputAuthorityError("Dataset member adapter contract is absent or ambiguous")
        member_contracts[adapter_id] = row

    rows = list(
        (
            await session.scalars(
                select(ExperimentDatasetRevisionMember)
                .where(ExperimentDatasetRevisionMember.revision_id == revision.resource_id)
                .order_by(ExperimentDatasetRevisionMember.ordinal)
                .limit(_MAX_MEMBERS + 1)
            )
        ).all()
    )
    if len(rows) != len(payload_members) or [row.ordinal for row in rows] != list(range(len(rows))):
        raise PreparationInputAuthorityError("ordered Dataset member rows diverge from the canonical revision payload")
    authorities: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for ordinal, (payload_member, row) in enumerate(zip(payload_members, rows, strict=True)):
        if not isinstance(payload_member, dict) or set(payload_member) != {"role", "identity", "value", "size_bytes", "media_type"}:
            raise PreparationInputAuthorityError("Dataset member payload is not closed")
        value = payload_member.get("value")
        if not isinstance(value, dict) or set(value) != {
            "schema", "receipt_id", "adapter_id", "store_id", "entity_kind", "entity_id",
            "native_revision_or_generation", "native_content_sha256", "role", "ordinal",
            "media_type", "metadata", "reopen_uri",
        }:
            raise PreparationInputAuthorityError("Dataset member value is not the canonical member contract")
        if value.get("schema") != "bms.dataset-member.v1" or value.get("ordinal") != ordinal:
            raise PreparationInputAuthorityError("Dataset member schema or ordinal is invalid")
        role = value.get("role")
        adapter_id = value.get("adapter_id")
        if not isinstance(adapter_id, str) or not isinstance(role, str):
            raise PreparationInputAuthorityError("Dataset member adapter or role is not an exact string")
        member_contract = member_contracts.get(adapter_id)
        if (
            member_contract is None
            or role not in member_contract.get("allowed_roles", [])
            or payload_member.get("role") != role
            or payload_member.get("identity") != value.get("receipt_id")
            or row.role != role
            or row.semantic_identity != value.get("receipt_id")
            or row.size_bytes != payload_member.get("size_bytes")
            or row.media_type != payload_member.get("media_type")
            or value.get("media_type") != payload_member.get("media_type")
            or not isinstance(value.get("metadata"), dict)
        ):
            raise PreparationInputAuthorityError("Dataset member row and canonical value diverge")
        pair = (str(value.get("receipt_id") or ""), str(role or ""))
        if not pair[0] or not pair[1] or pair in seen_pairs:
            raise PreparationInputAuthorityError("Dataset members contain duplicate or empty receipt/role authority")
        seen_pairs.add(pair)
        value_json = _canonical(value)
        if row.value_json != value_json or row.content_sha256 != hashlib.sha256(value_json.encode("utf-8")).hexdigest():
            raise PreparationInputAuthorityError("Dataset member value bytes or content digest diverge")
        receipt_authority = await _fresh_receipt_authority(
            session,
            core_session,
            receipt_id=pair[0],
            project_id=project_id,
            domain_id=domain_id,
            adapter_registry=adapter_registry,
            required_adapter_id=str(adapter_id),
            required_entity_kind=str(member_contract.get("receipt_kind") or ""),
            required_role=str(role),
            adapter_allowed_roles=set(member_contract.get("allowed_roles") or []),
        )
        if (
            value.get("store_id") != receipt_authority["store_id"]
            or value.get("entity_kind") != receipt_authority["entity_kind"]
            or value.get("entity_id") != receipt_authority["entity_id"]
            or value.get("native_revision_or_generation") != receipt_authority["native_revision_or_generation"]
            or value.get("native_content_sha256") != receipt_authority["content_sha256"]
            or value.get("reopen_uri") != receipt_authority["reopen_uri"]
        ):
            raise PreparationInputAuthorityError("persisted Dataset member authority diverges from its fresh native receipt")
        authorities.append(
            {
                "ordinal": ordinal,
                "role": role,
                "semantic_identity": row.semantic_identity,
                "member_content_sha256": row.content_sha256,
                "size_bytes": row.size_bytes,
                "media_type": row.media_type,
                "receipt": receipt_authority,
            }
        )
    return {
        "schema": "bms.preparation-dataset-input-authority.v1",
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_id": domain_id,
        "dataset_id": head.aggregate_id,
        "dataset_kind": head.dataset_kind,
        "dataset_contract_sha256": _digest(dataset_contract),
        "dataset_revision_id": revision.resource_id,
        "revision_number": revision.revision_number,
        "revision_payload_sha256": revision.payload_sha256,
        "revision_dependency_sha256": revision.dependency_graph_sha256,
        "member_count": len(authorities),
        "members": authorities,
    }


async def build_preparation_input_authority(
    session: AsyncSession,
    core_session: AsyncSession | None,
    *,
    workflow_revision_id: str,
    dataset_revision_ids: list[str],
    source_receipt_ids: list[str],
    receipt_contracts: list[str] | None = None,
) -> dict[str, Any]:
    """Freshly resolve the canonical ordered authority for every preparation input."""
    if (
        not isinstance(dataset_revision_ids, list)
        or len(dataset_revision_ids) > _MAX_DATASETS
        or any(not isinstance(value, str) or not value for value in dataset_revision_ids)
        or len(dataset_revision_ids) != len(set(dataset_revision_ids))
    ):
        raise PreparationInputAuthorityError("input Dataset revision identities are invalid or duplicated")
    if (
        not isinstance(source_receipt_ids, list)
        or len(source_receipt_ids) > _MAX_DATASETS
        or any(not isinstance(value, str) or not value for value in source_receipt_ids)
        or len(source_receipt_ids) != len(set(source_receipt_ids))
    ):
        raise PreparationInputAuthorityError("workflow source receipt identities are invalid or duplicated")
    normalized_receipt_contracts = _normalized_receipt_contracts(
        receipt_contracts,
        required=bool(source_receipt_ids),
    )
    _revision, _head, project_id, global_experiment_id, domain_id = await _workflow_scope(
        session, workflow_revision_id
    )
    dataset_registry, adapter_registry = _registry_documents()
    if dataset_revision_ids or source_receipt_ids:
        await _active_input_hierarchy(
            session,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            domain_id=domain_id,
        )
    if (dataset_revision_ids or source_receipt_ids) and domain_id is None:
        raise PreparationInputAuthorityError("preparation inputs require an exact Domain parent")
    datasets: list[dict[str, Any]] = []
    total_members = 0
    for revision_id in dataset_revision_ids:
        dataset = await _dataset_authority(
            session,
            core_session,
            revision_id=revision_id,
            project_id=project_id,
            global_experiment_id=str(global_experiment_id),
            domain_id=str(domain_id),
            dataset_registry=dataset_registry,
            adapter_registry=adapter_registry,
        )
        total_members += int(dataset["member_count"])
        if total_members > _MAX_TOTAL_MEMBERS:
            raise PreparationInputAuthorityError("preparation input authority exceeds 10000 total Dataset members")
        datasets.append(dataset)
    sources = [
        await _fresh_receipt_authority(
            session,
            core_session,
            receipt_id=receipt_id,
            project_id=project_id,
            domain_id=str(domain_id),
            adapter_registry=adapter_registry,
            pinned_receipt_contracts=normalized_receipt_contracts,
        )
        for receipt_id in source_receipt_ids
    ]
    return {
        "schema": "bms.preparation-input-authority.v1",
        "workflow_revision_id": workflow_revision_id,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_id": domain_id,
        "dataset_inputs": datasets,
        "receipt_contracts": normalized_receipt_contracts,
        "receipt_contracts_sha256": _digest(normalized_receipt_contracts),
        "workflow_source_receipts": sources,
    }


__all__ = [
    "PreparationInputAuthorityError",
    "build_preparation_input_authority",
]

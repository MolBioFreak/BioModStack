"""SQLAlchemy models for the global experiment/workspace control store."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import declarative_base, reconstructor


ExperimentBase = declarative_base()


def _timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="microseconds") + "Z"


class ExperimentResource(ExperimentBase):
    __tablename__ = "resources"

    id = Column(String(128), primary_key=True)
    kind = Column(String(64), nullable=False)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    lifecycle_owner_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    archived_at = Column(String(64), nullable=True)


class ExperimentAggregateHead(ExperimentBase):
    __tablename__ = "aggregate_heads"

    aggregate_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    aggregate_kind = Column(String(32), nullable=False)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    parent_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    current_revision_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    head_generation = Column(Integer, nullable=False, default=0)
    lifecycle_state = Column(String(32), nullable=False, default="draft")
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    dataset_kind = Column(String(255), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.aggregate_id


class ExperimentRevision(ExperimentBase):
    __tablename__ = "revisions"
    __table_args__ = (
        UniqueConstraint("subject_id", "revision_number"),
        UniqueConstraint("subject_id", "payload_sha256", "dependency_graph_sha256"),
    )

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    subject_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    revision_number = Column(Integer, nullable=False)
    parent_revision_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    canonical_payload = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    dependency_graph_sha256 = Column(String(64), nullable=False)
    provenance_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentRevisionEdge(ExperimentBase):
    __tablename__ = "revision_edges"

    revision_id = Column(String(128), ForeignKey("revisions.resource_id"), primary_key=True)
    target_resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    role = Column(String(128), primary_key=True)
    ordinal = Column(Integer, primary_key=True, default=0)
    expected_sha256 = Column(String(64), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")


class ExperimentWorkflowRevisionNode(ExperimentBase):
    __tablename__ = "workflow_revision_nodes"

    revision_id = Column(String(128), ForeignKey("revisions.resource_id"), primary_key=True)
    ordinal = Column(Integer, primary_key=True)
    node_id = Column(String(128), primary_key=True)
    node_kind = Column(String(128), nullable=False)
    node_json = Column(Text, nullable=False)


class ExperimentWorkflowRevisionEdge(ExperimentBase):
    __tablename__ = "workflow_revision_edges"

    revision_id = Column(String(128), ForeignKey("revisions.resource_id"), primary_key=True)
    ordinal = Column(Integer, primary_key=True)
    source_node_id = Column(String(128), primary_key=True)
    target_node_id = Column(String(128), primary_key=True)
    edge_json = Column(Text, nullable=False)


class ExperimentLineageEdge(ExperimentBase):
    __tablename__ = "lineage_edges"
    __table_args__ = (UniqueConstraint("source_resource_id", "target_resource_id", "edge_mode", "edge_key"),)

    id = Column(String(128), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    source_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    target_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    edge_mode = Column(String(64), nullable=False)
    edge_key = Column(String(255), nullable=False)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentWorkflowDraft(ExperimentBase):
    __tablename__ = "workflow_drafts"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workflow_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    base_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=True)
    canonical_payload = Column(Text, nullable=False, default="{}")
    generation = Column(Integer, nullable=False, default=0)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentDatasetRevisionMember(ExperimentBase):
    __tablename__ = "dataset_revision_members"

    revision_id = Column(String(128), ForeignKey("revisions.resource_id"), primary_key=True)
    ordinal = Column(Integer, primary_key=True)
    role = Column(String(128), nullable=False)
    semantic_identity = Column(String(255), nullable=False)
    value_json = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=True)
    media_type = Column(String(128), nullable=True)


class ExperimentWorkflowPreparation(ExperimentBase):
    __tablename__ = "workflow_preparations"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    workflow_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=False)
    normalized_request_json = Column(Text, nullable=False)
    normalized_request_sha256 = Column(String(64), nullable=False)
    scheduler_payload_json = Column(Text, nullable=False, default="{}")
    validation_status = Column(String(32), nullable=False)
    validation_receipt_json = Column(Text, nullable=False)
    validation_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    expected_cardinality = Column(Integer, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    prepared_at = Column(String(64), nullable=True)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentWorkflowPlanAuthority(ExperimentBase):
    """Immutable Domain revision and capability contract pinned by one Plan."""

    __tablename__ = "workflow_plan_authority"

    workflow_id = Column(String(128), ForeignKey("aggregate_heads.aggregate_id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    expected_domain_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=False)
    capability_contract_json = Column(Text, nullable=False)
    capability_contract_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentWorkflowSetupContext(ExperimentBase):
    """Durable Project-owned editable context preceding immutable preparation."""

    __tablename__ = "workflow_setup_contexts"
    __table_args__ = (
        UniqueConstraint("project_id", "workflow_id"),
        Index("ix_experiment_workflow_setups_project_updated", "project_id", "updated_at"),
        Index("ix_experiment_workflow_setups_experiment", "global_experiment_id", "created_at"),
    )

    setup_context_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    project_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    global_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    workflow_id = Column(String(128), ForeignKey("aggregate_heads.aggregate_id"), nullable=False)
    relationship_kind = Column(String(16), nullable=False)
    capability_id = Column(String(255), nullable=False)
    capability_contract_json = Column(Text, nullable=False)
    capability_contract_sha256 = Column(String(64), nullable=False)
    setup_destination = Column(String(1000), nullable=False)
    draft_json = Column(Text, nullable=False, default="{}")
    draft_sha256 = Column(String(64), nullable=False)
    generation = Column(Integer, nullable=False, default=0)
    validation_state = Column(String(16), nullable=False, default="incomplete")
    lifecycle_state = Column(String(16), nullable=False, default="open")
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)
    submitted_at = Column(String(64), nullable=True)
    deleted_at = Column(String(64), nullable=True)

    @property
    def id(self) -> str:
        return self.setup_context_id


class ExperimentRunGroup(ExperimentBase):
    __tablename__ = "run_groups"
    __table_args__ = (UniqueConstraint("workspace_id", "launch_idempotency_key"),)

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    launch_idempotency_key = Column(String(255), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="dispatch_pending")
    generation = Column(Integer, nullable=False, default=0)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentRunControlCommand(ExperimentBase):
    """Durable cancellation command authority for one run group."""

    __tablename__ = "run_control_commands"
    __table_args__ = (
        UniqueConstraint("request_scope", "idempotency_key"),
        Index(
            "uq_run_control_active_command",
            "run_group_id",
            "command_type",
            unique=True,
            sqlite_where=text("status IN ('pending','leased','retryable','applied')"),
        ),
    )

    command_id = Column(String(128), primary_key=True)
    request_scope = Column(String(512), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    command_type = Column(String(32), nullable=False, default="cancel")
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    run_group_id = Column(String(128), ForeignKey("run_groups.resource_id"), nullable=False)
    expected_generation = Column(Integer, nullable=False)
    request_json = Column(Text, nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    target_snapshot_json = Column(Text, nullable=False)
    target_snapshot_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    lease_owner = Column(String(255), nullable=True)
    lease_token = Column(String(128), nullable=True)
    lease_expires_at = Column(String(64), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(String(64), nullable=True)
    progress_json = Column(Text, nullable=False, default="{}")
    progress_sha256 = Column(String(64), nullable=False)
    acknowledgement_json = Column(Text, nullable=True)
    acknowledgement_sha256 = Column(String(64), nullable=True)
    conflict_json = Column(Text, nullable=True)
    conflict_sha256 = Column(String(64), nullable=True)
    last_error_code = Column(String(128), nullable=True)
    last_error_message = Column(String(2000), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)
    applied_at = Column(String(64), nullable=True)


class ExperimentRunGroupPreparation(ExperimentBase):
    __tablename__ = "run_group_preparations"

    run_group_id = Column(String(128), ForeignKey("run_groups.resource_id"), primary_key=True)
    preparation_id = Column(String(128), ForeignKey("workflow_preparations.resource_id"), primary_key=True)
    ordinal = Column(Integer, nullable=False)


class ExperimentWorkflowRun(ExperimentBase):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("run_group_id", "preparation_id", "node_id"),)

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    run_group_id = Column(String(128), ForeignKey("run_groups.resource_id"), nullable=False)
    preparation_id = Column(String(128), ForeignKey("workflow_preparations.resource_id"), nullable=False)
    node_id = Column(String(128), nullable=False)
    requiredness = Column(String(16), nullable=False, default="required")
    state = Column(String(32), nullable=False, default="dispatch_pending")
    generation = Column(Integer, nullable=False, default=0)
    created_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentRunAttempt(ExperimentBase):
    __tablename__ = "run_attempts"
    __table_args__ = (UniqueConstraint("workflow_run_id", "attempt_number"), UniqueConstraint("scheduler_job_id"))

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    workflow_run_id = Column(String(128), ForeignKey("workflow_runs.resource_id"), nullable=False)
    preparation_id = Column(
        String(128), ForeignKey("workflow_preparations.resource_id"), nullable=False
    )
    attempt_number = Column(Integer, nullable=False, default=1)
    scheduler_job_id = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    external_binding_receipt_json = Column(Text, nullable=True)
    runtime_identity_json = Column(Text, nullable=True)
    terminal_receipt_json = Column(Text, nullable=True)
    terminal_receipt_sha256 = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)

    @property
    def id(self) -> str:
        return self.resource_id


class ExperimentDispatchOutbox(ExperimentBase):
    __tablename__ = "dispatch_outbox"
    __table_args__ = (UniqueConstraint("event_type", "run_attempt_id"),)

    id = Column(String(128), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    run_attempt_id = Column(String(128), ForeignKey("run_attempts.resource_id"), nullable=False)
    event_type = Column(String(128), nullable=False)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(128), nullable=True)
    lease_owner = Column(String(255), nullable=True)
    lease_acquired_at = Column(String(64), nullable=True)
    lease_expires_at = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    acknowledgement_json = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentRunEvent(ExperimentBase):
    __tablename__ = "run_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    workflow_run_id = Column(String(128), ForeignKey("workflow_runs.resource_id"), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    expected_generation = Column(Integer, nullable=False, default=0)
    resulting_generation = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(255), nullable=False)
    event_type = Column(String(128), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentIdempotencyClaim(ExperimentBase):
    __tablename__ = "idempotency_claims"

    @staticmethod
    def _response_authority(response_json: str) -> tuple[str, str]:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("idempotency response contains duplicate JSON keys")
                value[key] = item
            return value

        parsed = json.loads(response_json, object_pairs_hook=unique_object)
        if type(parsed) is not dict:
            raise ValueError("idempotency response must be a JSON object")
        canonical = json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return canonical, digest

    def __init__(self, **kwargs: Any) -> None:
        response_json = kwargs.get("response_json")
        if not isinstance(response_json, str):
            raise ValueError("idempotency response_json is required")
        canonical, expected_digest = self._response_authority(response_json)
        supplied_digest = kwargs.get("response_sha256")
        if supplied_digest is not None and supplied_digest != expected_digest:
            raise ValueError("idempotency response digest diverges from canonical bytes")
        kwargs["response_json"] = canonical
        kwargs["response_sha256"] = expected_digest
        super().__init__(**kwargs)

    @reconstructor
    def _validate_loaded_authority(self) -> None:
        canonical, expected_digest = self._response_authority(self.response_json)
        if canonical != self.response_json or self.response_sha256 != expected_digest:
            raise ValueError("persisted idempotency response authority is invalid")

    scope = Column(String(128), primary_key=True)
    idempotency_key = Column(String(255), primary_key=True)
    request_sha256 = Column(String(64), nullable=False)
    result_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    response_json = Column(Text, nullable=False)
    response_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentExternalEntityReceipt(ExperimentBase):
    __tablename__ = "external_entity_receipts"

    id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    store_id = Column(String(128), nullable=False)
    entity_kind = Column(String(128), nullable=False)
    entity_id = Column(String(255), nullable=False)
    generation_or_revision = Column(String(255), nullable=False)
    content_digest = Column(String(64), nullable=False)
    availability = Column(String(32), nullable=False, default="unknown")
    verification_authority = Column(String(255), nullable=False, default="legacy_unverified")
    acknowledgement_json = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentResearchRecord(ExperimentBase):
    __tablename__ = "research_records"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    subject_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    record_kind = Column(String(32), nullable=False)
    body = Column(Text, nullable=False)
    author = Column(String(255), nullable=True)
    source_receipt_ids_json = Column(Text, nullable=False, default="[]")
    supersedes_record_id = Column(String(128), ForeignKey("research_records.resource_id"), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentDomainAdapterReceipt(ExperimentBase):
    __tablename__ = "domain_adapter_receipts"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    adapter_id = Column(String(255), nullable=False)
    adapter_version = Column(String(64), nullable=False)
    operation_kind = Column(String(64), nullable=False)
    normalized_request_sha256 = Column(String(64), nullable=False)
    receipt_json = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentLaunchContext(ExperimentBase):
    """Short-lived server-owned handoff from a Domain Experiment to a typed launcher."""

    __tablename__ = "launch_contexts"

    launch_context_id = Column(String(128), primary_key=True)
    project_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    global_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    workflow_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    workflow_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=True)
    preparation_id = Column(
        String(128), ForeignKey("workflow_preparations.resource_id"), nullable=True
    )
    run_attempt_id = Column(String(128), ForeignKey("run_attempts.resource_id"), nullable=True, unique=True)
    contract_version = Column(String(16), nullable=False, default="1")
    normalized_request_sha256 = Column(String(64), nullable=True)
    validation_receipt_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    validation_receipt_sha256 = Column(String(64), nullable=True)
    source_receipt_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    return_uri = Column(String(1000), nullable=False)
    state = Column(String(32), nullable=False, default="issued")
    claim_token = Column(String(128), nullable=True)
    canonical_job_id = Column(String(128), nullable=True, unique=True)
    binding_receipt_json = Column(Text, nullable=True)
    issued_at = Column(String(64), nullable=False, default=_timestamp)
    expires_at = Column(String(64), nullable=False)
    claimed_at = Column(String(64), nullable=True)
    consumed_at = Column(String(64), nullable=True)

    @property
    def id(self) -> str:
        return self.launch_context_id


class ExperimentDomainConnectorCommand(ExperimentBase):
    """Durable global-to-domain command for the NGS/MolBio connector lane."""

    __tablename__ = "domain_connector_commands"
    __table_args__ = (
        UniqueConstraint("request_scope", "idempotency_key"),
    )

    command_id = Column(String(128), primary_key=True)
    request_scope = Column(String(512), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    idempotency_request_sha256 = Column(String(64), nullable=False)
    operation = Column(String(32), nullable=False)
    project_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    project_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=False)
    global_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    global_experiment_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_revision_id = Column(String(128), ForeignKey("revisions.resource_id"), nullable=False)
    domain_revision_sha256 = Column(String(64), nullable=False)
    prior_binding_revision_id = Column(String(128), nullable=True)
    global_receipt_id = Column(String(128), ForeignKey("domain_adapter_receipts.resource_id"), nullable=False)
    global_receipt_sha256 = Column(String(64), nullable=False)
    command_json = Column(Text, nullable=False)
    command_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    lease_owner = Column(String(255), nullable=True)
    lease_token = Column(String(128), nullable=True)
    lease_expires_at = Column(String(64), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    acknowledgement_id = Column(String(128), nullable=True)
    acknowledgement_json = Column(Text, nullable=True)
    acknowledgement_sha256 = Column(String(64), nullable=True)
    binding_revision_id = Column(String(128), nullable=True)
    conflict_json = Column(Text, nullable=True)
    conflict_sha256 = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentDomainConnectorInbox(ExperimentBase):
    """Idempotent ordered ingestion record for one domain outbox event."""

    __tablename__ = "domain_connector_inbox"
    __table_args__ = (
        UniqueConstraint(
            "domain_experiment_id", "binding_revision_id", "event_stream", "stream_generation"
        ),
    )

    event_id = Column(String(128), primary_key=True)
    source_store_id = Column(String(128), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    binding_revision_id = Column(String(128), nullable=False)
    state_revision_id = Column(String(128), nullable=True)
    event_type = Column(String(128), nullable=False)
    event_stream = Column(String(512), nullable=False)
    stream_generation = Column(Integer, nullable=False)
    source_generation = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    envelope_json = Column(Text, nullable=False)
    envelope_sha256 = Column(String(64), nullable=False)
    disposition = Column(String(32), nullable=False)
    acknowledgement_json = Column(Text, nullable=False)
    acknowledgement_sha256 = Column(String(64), nullable=False)
    conflict_json = Column(Text, nullable=True)
    conflict_sha256 = Column(String(64), nullable=True)
    occurred_at = Column(String(64), nullable=False)
    received_at = Column(String(64), nullable=False, default=_timestamp)
    applied_at = Column(String(64), nullable=True)


class ExperimentDomainConnectorStream(ExperimentBase):
    __tablename__ = "domain_connector_streams"

    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    binding_revision_id = Column(String(128), primary_key=True)
    event_stream = Column(String(512), primary_key=True)
    last_applied_stream_generation = Column(Integer, nullable=False, default=0)
    last_event_id = Column(String(128), nullable=True)
    last_payload_sha256 = Column(String(64), nullable=True)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentDomainConnectorConflict(ExperimentBase):
    __tablename__ = "domain_connector_conflicts"

    conflict_id = Column(String(128), primary_key=True)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    binding_revision_id = Column(String(128), nullable=False)
    event_stream = Column(String(512), nullable=False)
    stream_generation = Column(Integer, nullable=False)
    event_id = Column(String(128), nullable=False)
    conflict_json = Column(Text, nullable=False)
    conflict_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentArtifactBlob(ExperimentBase):
    __tablename__ = "artifact_blobs"

    sha256 = Column(String(64), primary_key=True)
    size_bytes = Column(Integer, nullable=False)
    media_type = Column(String(255), nullable=False)
    storage_key = Column(String(1000), nullable=False, unique=True)
    state = Column(String(32), nullable=False, default="staged")
    verified_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentArtifact(ExperimentBase):
    __tablename__ = "artifacts"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    blob_sha256 = Column(String(64), ForeignKey("artifact_blobs.sha256"), nullable=False)
    logical_role = Column(String(255), nullable=False)
    logical_key = Column(String(255), nullable=False)
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    provenance_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentValidation(ExperimentBase):
    __tablename__ = "validations"

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    subject_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    validator_name = Column(String(255), nullable=False)
    validator_version = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False)
    input_graph_sha256 = Column(String(64), nullable=False)
    receipt_json = Column(Text, nullable=False)
    receipt_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentLogStream(ExperimentBase):
    __tablename__ = "log_streams"
    __table_args__ = (UniqueConstraint("attempt_id", "stream_name"),)

    resource_id = Column(String(128), ForeignKey("resources.id"), primary_key=True)
    attempt_id = Column(String(128), ForeignKey("run_attempts.resource_id"), nullable=False)
    stream_name = Column(String(255), nullable=False)
    state = Column(String(32), nullable=False, default="open")
    created_at = Column(String(64), nullable=False, default=_timestamp)
    closed_at = Column(String(64), nullable=True)


class ExperimentLogChunk(ExperimentBase):
    __tablename__ = "log_chunks"

    stream_id = Column(String(128), ForeignKey("log_streams.resource_id"), primary_key=True)
    sequence_number = Column(Integer, primary_key=True)
    content_sha256 = Column(String(64), nullable=False)
    artifact_blob_sha256 = Column(String(64), ForeignKey("artifact_blobs.sha256"), nullable=True)
    content_text = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentAuditEvent(ExperimentBase):
    __tablename__ = "audit_events"

    id = Column(String(128), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    event_type = Column(String(255), nullable=False)
    generation = Column(Integer, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentSyncState(ExperimentBase):
    __tablename__ = "sync_state"

    state_key = Column(String(255), primary_key=True)
    local_generation = Column(Integer, nullable=False, default=0)
    remote_generation = Column(Integer, nullable=True)
    pending_changes = Column(Integer, nullable=False, default=0)
    last_success_at = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentResourceAdmission(ExperimentBase):
    """Durable aggregate resource reservation for one canonical workflow attempt."""

    __tablename__ = "resource_admissions"
    __table_args__ = (UniqueConstraint("run_attempt_id"), UniqueConstraint("canonical_job_id"))

    admission_id = Column(String(128), primary_key=True)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    domain_experiment_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    plan_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    preparation_id = Column(String(128), ForeignKey("workflow_preparations.resource_id"), nullable=False)
    run_attempt_id = Column(String(128), ForeignKey("run_attempts.resource_id"), nullable=True)
    canonical_job_id = Column(String(128), nullable=True)
    state = Column(String(32), nullable=False)
    cpu_threads = Column(Integer, nullable=False)
    dram_bytes = Column(Integer, nullable=False)
    gpu_index = Column(Integer, nullable=True)
    gpu_uuid = Column(String(255), nullable=True)
    policy_source = Column(String(255), nullable=False)
    policy_version = Column(String(64), nullable=False)
    owner = Column(String(255), nullable=False)
    lease_token = Column(String(128), nullable=True)
    refusal_code = Column(String(128), nullable=True)
    refusal_reason = Column(Text, nullable=True)
    release_reason = Column(Text, nullable=True)
    recovery_evidence_json = Column(Text, nullable=True)
    admitted_at = Column(String(64), nullable=True)
    queued_at = Column(String(64), nullable=True)
    released_at = Column(String(64), nullable=True)
    reconciled_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentResourceAdmissionPolicy(ExperimentBase):
    """Singleton write fence and persisted policy identity for atomic admission."""

    __tablename__ = "resource_admission_policy"

    policy_id = Column(String(64), primary_key=True)
    policy_version = Column(String(64), nullable=False)
    cpu_thread_limit = Column(Integer, nullable=False)
    dram_byte_limit = Column(Integer, nullable=False)
    lock_generation = Column(Integer, nullable=False, default=0)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class ExperimentOperationalReceipt(ExperimentBase):
    """Bounded provenance pointer for backup, export, restoration, and audit operations."""

    __tablename__ = "operational_receipts"

    receipt_id = Column(String(128), primary_key=True)
    operation_kind = Column(String(64), nullable=False)
    workspace_id = Column(String(128), ForeignKey("resources.id"), nullable=True)
    native_identity = Column(String(255), nullable=False)
    state = Column(String(32), nullable=False)
    receipt_json = Column(Text, nullable=False)
    receipt_sha256 = Column(String(64), nullable=False)
    source_revision = Column(String(160), nullable=True)
    occurred_at = Column(String(64), nullable=False)
    verified_at = Column(String(64), nullable=True)

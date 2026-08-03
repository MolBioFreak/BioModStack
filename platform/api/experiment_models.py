"""SQLAlchemy models for the global experiment/workspace control store."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base


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
    attempt_number = Column(Integer, nullable=False, default=1)
    scheduler_job_id = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    external_binding_receipt_json = Column(Text, nullable=True)
    runtime_identity_json = Column(Text, nullable=True)
    terminal_receipt_json = Column(Text, nullable=True)
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

    scope = Column(String(128), primary_key=True)
    idempotency_key = Column(String(255), primary_key=True)
    request_sha256 = Column(String(64), nullable=False)
    result_resource_id = Column(String(128), ForeignKey("resources.id"), nullable=False)
    response_json = Column(Text, nullable=False)
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
    acknowledgement_json = Column(Text, nullable=True)
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

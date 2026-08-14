"""Versioned migrations for the global experiment/workspace SQLite store."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone

from migrations.sqlite_sha256 import register_sqlite_sha256


LEGACY_MIGRATION_VERSION = 1
LEGACY_MIGRATION_NAME = "global_experiment_workspace_foundation"
LEGACY_MIGRATION_CHECKSUM = "987620af4200932c8fffb282c5655d21aefc29a1a98cbfc3b54f3a734dfe6c10"
MIGRATION_V2_VERSION = 2
MIGRATION_V2_NAME = "global_experiment_workspace_receipts_and_projections"
MIGRATION_V3_VERSION = 3
MIGRATION_V3_NAME = "global_project_hierarchy_and_research_records"
MIGRATION_V4_VERSION = 4
MIGRATION_V4_NAME = "immutable_external_entity_receipts"
MIGRATION_V5_VERSION = 5
MIGRATION_V5_NAME = "aggregate_head_revision_lifecycle_consistency"
MIGRATION_V6_VERSION = 6
MIGRATION_V6_NAME = "domain_owned_workflow_and_dataset_aggregates"
MIGRATION_V7_VERSION = 7
MIGRATION_V7_NAME = "project_scoped_revisioned_external_entity_receipts"
MIGRATION_V8_VERSION = 8
MIGRATION_V8_NAME = "opaque_launch_context_receipts"
MIGRATION_V9_VERSION = 9
MIGRATION_V9_NAME = "immutable_preparations_and_dataset_members"
MIGRATION_VERSION = 10
MIGRATION_NAME = "scientific_lineage_vocabulary"
MIGRATION_V11_VERSION = 11
MIGRATION_V11_NAME = "attempt_launch_context_dataset_authority"
MIGRATION_V12_VERSION = 12
MIGRATION_V12_NAME = "ngs_molbio_domain_connector_authority"
MIGRATION_V13_VERSION = 13
MIGRATION_V13_NAME = "ngs_molbio_dataset_admission_operations"
MIGRATION_V14_VERSION = 14
MIGRATION_V14_NAME = "workflow_plan_authority"
MIGRATION_V15_VERSION = 15
MIGRATION_V15_NAME = "idempotency_response_digest_authority"
MIGRATION_V16_VERSION = 16
MIGRATION_V16_NAME = "run_control_cancellation_authority"
LATEST_MIGRATION_VERSION = MIGRATION_V16_VERSION
MIGRATION_V2_CHECKSUM = "db24d1ef056e560f10eb2fe9f8ef4dac0d4e4dbe90fd0a49efed88f0d111935c"
MIGRATION_V3_CHECKSUM = "46f1a1d28a02334e87d628070e2bd9c6d78e158caa23d583951fdc582e7b11d2"
MIGRATION_V4_CHECKSUM = "ec2966efee9129f8890019bee0d569de2cdf8d2a9fc4bb2e05138839880f375b"
MIGRATION_V5_CHECKSUM = "6df15ae6c5e2761070ff9714a48ff44aad1e47aed590558f6f1d6d3af9fc2eec"
MIGRATION_V6_CHECKSUM = "b93ba493759c7b8ba14820500f14ef7588308651e892fdfaccf388ed4330d705"
MIGRATION_V7_CHECKSUM = "828bcc8e2b8cde1e131ec2f1ced9193ed001e1dfcb2b607e499a6a994acad1d9"
MIGRATION_V8_CHECKSUM = "bf11980a720aad2b0f62fcbea055a3d2e1181b1b41449cc62c0455b70b9c92dc"
MIGRATION_V9_CHECKSUM = "469240fb73fb6d7be9bf80412ccfd52cc81d14e91bf6bedb8881ffeb1c9b7bd0"
MIGRATION_V10_CHECKSUM = "b36656f82945b839e573990255ecedbeb902608d2b329df0ebb0ce85dbea73e8"
FINAL_SCHEMA_MANIFEST_CHECKSUM = "53959a558aa10198b3af475820275bce4d966fbcf43043d0138098aa918dfe3a"
LEGACY_FINAL_SCHEMA_MANIFEST_CHECKSUM = "88dc6501b92d0119b5e4bdca9df48275834e51a669c472e716c8a8adfbde5ceb"

MIGRATION_SQL = r'''
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    workspace_id TEXT REFERENCES resources(id),
    lifecycle_owner_id TEXT REFERENCES resources(id),
    created_at TEXT NOT NULL,
    archived_at TEXT,
    CHECK (
        (kind = 'workspace' AND workspace_id IS NULL AND lifecycle_owner_id IS NULL)
        OR
        (kind <> 'workspace' AND workspace_id IS NOT NULL AND lifecycle_owner_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_experiment_resources_workspace_kind
    ON resources(workspace_id, kind, archived_at);
CREATE INDEX IF NOT EXISTS ix_experiment_resources_owner
    ON resources(lifecycle_owner_id, kind);

CREATE TABLE IF NOT EXISTS aggregate_heads (
    aggregate_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    aggregate_kind TEXT NOT NULL CHECK (aggregate_kind IN ('workspace', 'experiment', 'workflow', 'dataset')),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    parent_id TEXT REFERENCES resources(id),
    current_revision_id TEXT REFERENCES resources(id),
    head_generation INTEGER NOT NULL DEFAULT 0 CHECK (head_generation >= 0),
    lifecycle_state TEXT NOT NULL DEFAULT 'draft',
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_aggregate_heads_workspace_kind
    ON aggregate_heads(workspace_id, aggregate_kind, lifecycle_state);

CREATE TABLE IF NOT EXISTS revisions (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    subject_id TEXT NOT NULL REFERENCES resources(id),
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    parent_revision_id TEXT REFERENCES resources(id),
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    dependency_graph_sha256 TEXT NOT NULL CHECK (length(dependency_graph_sha256) = 64),
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(subject_id, revision_number),
    UNIQUE(subject_id, payload_sha256, dependency_graph_sha256)
);
CREATE INDEX IF NOT EXISTS ix_experiment_revisions_subject
    ON revisions(subject_id, revision_number);

CREATE TABLE IF NOT EXISTS revision_edges (
    revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    target_resource_id TEXT NOT NULL REFERENCES resources(id),
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    expected_sha256 TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(revision_id, role, ordinal, target_resource_id)
);

CREATE TABLE IF NOT EXISTS workflow_drafts (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workflow_id TEXT NOT NULL REFERENCES resources(id),
    base_revision_id TEXT REFERENCES revisions(resource_id),
    canonical_payload TEXT NOT NULL DEFAULT '{}',
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_workflow_drafts_workflow
    ON workflow_drafts(workflow_id);

CREATE TABLE IF NOT EXISTS dataset_revision_members (
    revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    role TEXT NOT NULL,
    semantic_identity TEXT NOT NULL,
    value_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    size_bytes INTEGER,
    media_type TEXT,
    PRIMARY KEY(revision_id, ordinal)
);

CREATE TABLE IF NOT EXISTS workflow_preparations (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    workflow_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    normalized_request_json TEXT NOT NULL,
    normalized_request_sha256 TEXT NOT NULL CHECK (length(normalized_request_sha256) = 64),
    scheduler_payload_json TEXT NOT NULL DEFAULT '{}',
    validation_status TEXT NOT NULL CHECK (validation_status IN ('pending', 'valid', 'invalid')),
    validation_receipt_json TEXT NOT NULL,
    validation_resource_id TEXT REFERENCES resources(id),
    expected_cardinality INTEGER,
    created_at TEXT NOT NULL,
    prepared_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_experiment_preparations_workspace_status
    ON workflow_preparations(workspace_id, validation_status, created_at);

CREATE TABLE IF NOT EXISTS run_groups (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    launch_idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('dispatch_pending', 'dispatching', 'dispatched', 'partially_dispatched', 'completed', 'failed')),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, launch_idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_experiment_run_groups_workspace_state
    ON run_groups(workspace_id, state, created_at);

CREATE TABLE IF NOT EXISTS run_group_preparations (
    run_group_id TEXT NOT NULL REFERENCES run_groups(resource_id),
    preparation_id TEXT NOT NULL REFERENCES workflow_preparations(resource_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY(run_group_id, preparation_id),
    UNIQUE(run_group_id, ordinal)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    run_group_id TEXT NOT NULL REFERENCES run_groups(resource_id),
    preparation_id TEXT NOT NULL REFERENCES workflow_preparations(resource_id),
    node_id TEXT NOT NULL,
    requiredness TEXT NOT NULL CHECK (requiredness IN ('required', 'optional')),
    state TEXT NOT NULL CHECK (state IN ('dispatch_pending', 'dispatched', 'running', 'completed', 'failed', 'cancelled')),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(run_group_id, preparation_id, node_id)
);
CREATE INDEX IF NOT EXISTS ix_experiment_workflow_runs_group_state
    ON workflow_runs(run_group_id, state);

CREATE TABLE IF NOT EXISTS run_attempts (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(resource_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    scheduler_job_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'dispatching', 'dispatched', 'running', 'completed', 'failed', 'cancelled')),
    external_binding_receipt_json TEXT,
    created_at TEXT NOT NULL,
    runtime_identity_json TEXT,
    terminal_receipt_json TEXT,
    UNIQUE(workflow_run_id, attempt_number),
    UNIQUE(scheduler_job_id)
);

CREATE TABLE IF NOT EXISTS dispatch_outbox (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    run_attempt_id TEXT NOT NULL REFERENCES run_attempts(resource_id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    status TEXT NOT NULL CHECK (status IN ('pending', 'dispatching', 'acknowledged', 'failed')),
    dispatch_attempts INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_attempts >= 0),
    lease_token TEXT,
    last_error TEXT,
    acknowledgement_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_type, run_attempt_id)
);
CREATE INDEX IF NOT EXISTS ix_experiment_outbox_status_created
    ON dispatch_outbox(status, created_at);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(resource_id),
    sequence_number INTEGER NOT NULL,
    expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
    resulting_generation INTEGER NOT NULL CHECK (resulting_generation >= 0),
    idempotency_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_run_id, sequence_number),
    UNIQUE(workflow_run_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_run_events_idempotency
    ON run_events(workflow_run_id, idempotency_key);

CREATE TABLE IF NOT EXISTS idempotency_claims (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    result_resource_id TEXT NOT NULL REFERENCES resources(id),
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS external_entity_receipts (
    id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    resource_id TEXT NOT NULL REFERENCES resources(id),
    store_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    generation_or_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    availability TEXT NOT NULL CHECK (availability IN ('unknown', 'available', 'unavailable')),
    verification_authority TEXT NOT NULL DEFAULT 'legacy_unverified' CHECK (length(verification_authority) > 0),
    acknowledgement_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, entity_kind, entity_id, generation_or_revision, content_digest)
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    source_resource_id TEXT NOT NULL REFERENCES resources(id),
    target_resource_id TEXT NOT NULL REFERENCES resources(id),
    edge_mode TEXT NOT NULL CHECK (edge_mode IN ('owns', 'pins', 'derives_from', 'contains', 'consumes', 'produces', 'retry_of', 'refines', 'validates', 'promotes_to_dataset', 'imports_from', 'forked_from')),
    edge_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_resource_id, target_resource_id, edge_mode, edge_key)
);
CREATE INDEX IF NOT EXISTS ix_experiment_lineage_edges_source ON lineage_edges(source_resource_id, edge_mode);
CREATE INDEX IF NOT EXISTS ix_experiment_lineage_edges_target ON lineage_edges(target_resource_id, edge_mode);

CREATE TABLE IF NOT EXISTS workflow_revision_nodes (
    revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    node_id TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    node_json TEXT NOT NULL,
    PRIMARY KEY(revision_id, node_id),
    UNIQUE(revision_id, ordinal)
);

CREATE TABLE IF NOT EXISTS workflow_revision_edges (
    revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_json TEXT NOT NULL,
    PRIMARY KEY(revision_id, ordinal),
    UNIQUE(revision_id, source_node_id, target_node_id)
);

CREATE TABLE IF NOT EXISTS artifact_blobs (
    sha256 TEXT PRIMARY KEY NOT NULL CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    media_type TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('staged', 'present', 'quarantined', 'purged')),
    verified_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    blob_sha256 TEXT NOT NULL REFERENCES artifact_blobs(sha256),
    logical_role TEXT NOT NULL,
    logical_key TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(resource_id, logical_role, logical_key)
);

CREATE TABLE IF NOT EXISTS validations (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    subject_resource_id TEXT NOT NULL REFERENCES resources(id),
    validator_name TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('valid', 'invalid', 'incomplete', 'unavailable')),
    input_graph_sha256 TEXT NOT NULL CHECK (length(input_graph_sha256) = 64),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log_streams (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    attempt_id TEXT NOT NULL REFERENCES run_attempts(resource_id),
    stream_name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'closed', 'unavailable')),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE(attempt_id, stream_name)
);

CREATE TABLE IF NOT EXISTS log_chunks (
    stream_id TEXT NOT NULL REFERENCES log_streams(resource_id),
    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    artifact_blob_sha256 TEXT REFERENCES artifact_blobs(sha256),
    content_text TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(stream_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    resource_id TEXT NOT NULL REFERENCES resources(id),
    event_type TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_audit_events_resource ON audit_events(resource_id, created_at);

CREATE TABLE IF NOT EXISTS sync_state (
    state_key TEXT PRIMARY KEY NOT NULL,
    local_generation INTEGER NOT NULL DEFAULT 0,
    remote_generation INTEGER,
    pending_changes INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_experiment_resource_owner_same_workspace_insert
BEFORE INSERT ON resources
WHEN NEW.kind <> 'workspace'
 AND (
   CASE
     WHEN (SELECT kind FROM resources WHERE id = NEW.lifecycle_owner_id) = 'workspace'
       THEN NEW.lifecycle_owner_id
     ELSE (SELECT workspace_id FROM resources WHERE id = NEW.lifecycle_owner_id)
   END IS NOT NEW.workspace_id
 )
BEGIN
    SELECT RAISE(ABORT, 'resource lifecycle owner must belong to workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_resource_owner_same_workspace_update
BEFORE UPDATE OF workspace_id, lifecycle_owner_id, kind ON resources
WHEN NEW.kind <> 'workspace'
 AND (
   CASE
     WHEN (SELECT kind FROM resources WHERE id = NEW.lifecycle_owner_id) = 'workspace'
       THEN NEW.lifecycle_owner_id
     ELSE (SELECT workspace_id FROM resources WHERE id = NEW.lifecycle_owner_id)
   END IS NOT NEW.workspace_id
 )
BEGIN
    SELECT RAISE(ABORT, 'resource lifecycle owner must belong to workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_resource_identity_immutable
BEFORE UPDATE OF id, kind, workspace_id, lifecycle_owner_id ON resources
BEGIN
    SELECT RAISE(ABORT, 'resource identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_revision_digest_insert
BEFORE INSERT ON revisions
WHEN sha256(NEW.canonical_payload) <> lower(NEW.payload_sha256)
BEGIN
    SELECT RAISE(ABORT, 'revision payload digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_revision_immutable_update
BEFORE UPDATE ON revisions
BEGIN
    SELECT RAISE(ABORT, 'immutable revision');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_revision_immutable_delete
BEFORE DELETE ON revisions
BEGIN
    SELECT RAISE(ABORT, 'immutable revision');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_revision_edge_immutable_update
BEFORE UPDATE ON revision_edges
BEGIN
    SELECT RAISE(ABORT, 'immutable revision edge');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_revision_edge_immutable_delete
BEFORE DELETE ON revision_edges
BEGIN
    SELECT RAISE(ABORT, 'immutable revision edge');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_lineage_same_workspace
BEFORE INSERT ON lineage_edges
WHEN NEW.source_resource_id = NEW.target_resource_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.source_resource_id) = 'workspace' THEN NEW.source_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.source_resource_id) END) IS NOT NEW.workspace_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.target_resource_id) = 'workspace' THEN NEW.target_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.target_resource_id) END) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'lineage edge is self-referential or crosses workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_lineage_owns_no_cycle
BEFORE INSERT ON lineage_edges
WHEN NEW.edge_mode = 'owns'
 AND EXISTS (
   WITH RECURSIVE reachable(id) AS (
       SELECT target_resource_id FROM lineage_edges WHERE source_resource_id = NEW.target_resource_id AND edge_mode = 'owns'
       UNION ALL
       SELECT lineage_edges.target_resource_id
       FROM lineage_edges JOIN reachable ON lineage_edges.source_resource_id = reachable.id
       WHERE lineage_edges.edge_mode = 'owns'
   )
   SELECT 1 FROM reachable WHERE id = NEW.source_resource_id
 )
BEGIN
    SELECT RAISE(ABORT, 'lineage ownership cycle');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_preparation_digest_insert
BEFORE INSERT ON workflow_preparations
WHEN sha256(NEW.normalized_request_json) <> lower(NEW.normalized_request_sha256)
BEGIN
    SELECT RAISE(ABORT, 'preparation request digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_outbox_digest_insert
BEFORE INSERT ON dispatch_outbox
WHEN sha256(NEW.payload_json) <> lower(NEW.payload_sha256)
BEGIN
    SELECT RAISE(ABORT, 'dispatch outbox payload digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_validation_receipt_digest_insert
BEFORE INSERT ON validations
WHEN sha256(NEW.receipt_json) <> lower(NEW.receipt_sha256)
BEGIN
    SELECT RAISE(ABORT, 'validation receipt digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_audit_immutable_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit event is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_audit_immutable_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit event is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_log_chunk_immutable_update
BEFORE UPDATE ON log_chunks
BEGIN
    SELECT RAISE(ABORT, 'log chunk is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_log_chunk_immutable_delete
BEFORE DELETE ON log_chunks
BEGIN
    SELECT RAISE(ABORT, 'log chunk is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_artifact_identity_immutable_update
BEFORE UPDATE ON artifacts
BEGIN
    SELECT RAISE(ABORT, 'artifact identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_outbox_payload_immutable
BEFORE UPDATE OF id, workspace_id, run_attempt_id, event_type, payload_json, payload_sha256 ON dispatch_outbox
BEGIN
    SELECT RAISE(ABORT, 'dispatch outbox payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_validation_immutable_update
BEFORE UPDATE ON validations
BEGIN
    SELECT RAISE(ABORT, 'validation receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_validation_immutable_delete
BEFORE DELETE ON validations
BEGIN
    SELECT RAISE(ABORT, 'validation receipt is immutable');
END;
'''

MIGRATION_V2_SQL = r'''
ALTER TABLE experiment_schema_migrations ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE revisions ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workflow_preparations ADD COLUMN validation_resource_id TEXT REFERENCES resources(id);
ALTER TABLE run_attempts ADD COLUMN runtime_identity_json TEXT;
ALTER TABLE run_attempts ADD COLUMN terminal_receipt_json TEXT;
ALTER TABLE run_events ADD COLUMN expected_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_events ADD COLUMN resulting_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_events ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT '';
UPDATE run_events SET idempotency_key = 'legacy:' || id WHERE idempotency_key = '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_run_events_idempotency
    ON run_events(workflow_run_id, idempotency_key);
'''

MIGRATION_V3_SQL = r'''
DROP INDEX IF EXISTS ix_experiment_aggregate_heads_workspace_kind;
ALTER TABLE aggregate_heads RENAME TO aggregate_heads_v2;
CREATE TABLE aggregate_heads (
    aggregate_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    aggregate_kind TEXT NOT NULL CHECK (aggregate_kind IN ('workspace', 'experiment', 'domain_experiment', 'workflow', 'dataset')),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    parent_id TEXT REFERENCES resources(id),
    current_revision_id TEXT REFERENCES resources(id),
    head_generation INTEGER NOT NULL DEFAULT 0 CHECK (head_generation >= 0),
    lifecycle_state TEXT NOT NULL DEFAULT 'draft',
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO aggregate_heads(
    aggregate_id, aggregate_kind, workspace_id, parent_id, current_revision_id,
    head_generation, lifecycle_state, display_name, description, created_at, updated_at
)
SELECT
    aggregate_id, aggregate_kind, workspace_id, parent_id, current_revision_id,
    head_generation, lifecycle_state, display_name, description, created_at, updated_at
FROM aggregate_heads_v2;
DROP TABLE aggregate_heads_v2;
CREATE INDEX IF NOT EXISTS ix_experiment_aggregate_heads_workspace_kind
    ON aggregate_heads(workspace_id, aggregate_kind, lifecycle_state);

DROP TRIGGER IF EXISTS trg_experiment_lineage_same_workspace;
DROP TRIGGER IF EXISTS trg_experiment_lineage_owns_no_cycle;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_source;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_target;
ALTER TABLE lineage_edges RENAME TO lineage_edges_v2;
CREATE TABLE lineage_edges (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    source_resource_id TEXT NOT NULL REFERENCES resources(id),
    target_resource_id TEXT NOT NULL REFERENCES resources(id),
    edge_mode TEXT NOT NULL CHECK (edge_mode IN (
        'owns', 'pins', 'derives_from', 'contains', 'consumes', 'produces', 'retry_of',
        'refines', 'validates', 'promotes_to_dataset', 'imports_from', 'forked_from',
        'references', 'uses_input', 'produced', 'validated_by'
    )),
    edge_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_resource_id, target_resource_id, edge_mode, edge_key)
);
INSERT INTO lineage_edges(
    id, workspace_id, source_resource_id, target_resource_id, edge_mode,
    edge_key, metadata_json, created_at
)
SELECT
    id, workspace_id, source_resource_id, target_resource_id, edge_mode,
    edge_key, metadata_json, created_at
FROM lineage_edges_v2;
DROP TABLE lineage_edges_v2;
CREATE INDEX ix_experiment_lineage_edges_source ON lineage_edges(source_resource_id, edge_mode);
CREATE INDEX ix_experiment_lineage_edges_target ON lineage_edges(target_resource_id, edge_mode);
CREATE TRIGGER trg_experiment_lineage_same_workspace
BEFORE INSERT ON lineage_edges
WHEN NEW.source_resource_id = NEW.target_resource_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.source_resource_id) = 'workspace' THEN NEW.source_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.source_resource_id) END) IS NOT NEW.workspace_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.target_resource_id) = 'workspace' THEN NEW.target_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.target_resource_id) END) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'lineage edge is self-referential or crosses workspace');
END;
CREATE TRIGGER trg_experiment_lineage_owns_no_cycle
BEFORE INSERT ON lineage_edges
WHEN NEW.edge_mode = 'owns'
 AND EXISTS (
   WITH RECURSIVE reachable(id) AS (
       SELECT target_resource_id FROM lineage_edges WHERE source_resource_id = NEW.target_resource_id AND edge_mode = 'owns'
       UNION ALL
       SELECT lineage_edges.target_resource_id
       FROM lineage_edges JOIN reachable ON lineage_edges.source_resource_id = reachable.id
       WHERE lineage_edges.edge_mode = 'owns'
   )
   SELECT 1 FROM reachable WHERE id = NEW.source_resource_id
 )
BEGIN
    SELECT RAISE(ABORT, 'lineage ownership cycle');
END;

CREATE TEMP TABLE hierarchy_migration_payloads (
    aggregate_id TEXT PRIMARY KEY,
    schema_name TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    legacy_lifecycle_state TEXT NOT NULL
);
INSERT INTO hierarchy_migration_payloads(aggregate_id, schema_name, canonical_payload, legacy_lifecycle_state)
SELECT
    aggregate_id,
    'bms.project.v1',
    json_object(
        'schema', 'bms.project.v1',
        'name', display_name,
        'description', description,
        'research_objective', '',
        'owner', NULL,
        'contributors', json('[]'),
        'tags', json('[]'),
        'status', CASE WHEN lifecycle_state IN ('draft', 'active', 'on_hold', 'completed', 'archived') THEN lifecycle_state ELSE 'draft' END,
        'start_date', NULL,
        'target_end_date', NULL,
        'external_references', json('[]'),
        'created_by', NULL,
        'change_summary', 'migrated from legacy workspace',
        'needs_metadata_review', json('true')
    ),
    lifecycle_state
FROM aggregate_heads
WHERE aggregate_kind = 'workspace' AND current_revision_id IS NULL;
INSERT INTO hierarchy_migration_payloads(aggregate_id, schema_name, canonical_payload, legacy_lifecycle_state)
SELECT
    aggregate_id,
    'bms.global-experiment.v1',
    json_object(
        'schema', 'bms.global-experiment.v1',
        'name', display_name,
        'objective', '',
        'scientific_question', description,
        'hypothesis', NULL,
        'description', description,
        'status', CASE
            WHEN lifecycle_state = 'completed' THEN 'review'
            WHEN lifecycle_state IN ('draft', 'planned', 'active', 'analysis', 'review', 'blocked', 'archived') THEN lifecycle_state
            ELSE 'draft'
        END,
        'priority', 'normal',
        'tags', json('[]'),
        'shared_source_receipt_ids', json('[]'),
        'shared_dataset_ids', json('[]'),
        'comparison_plan', NULL,
        'success_criteria', json('[]'),
        'review_summary', NULL,
        'conclusion', NULL,
        'created_by', NULL,
        'change_summary', 'migrated from legacy experiment',
        'needs_metadata_review', json('true')
    ),
    lifecycle_state
FROM aggregate_heads
WHERE aggregate_kind = 'experiment' AND current_revision_id IS NULL;
INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at)
SELECT
    'migration-v3-revision:' || payload.aggregate_id,
    'revision',
    head.workspace_id,
    payload.aggregate_id,
    head.created_at
FROM hierarchy_migration_payloads AS payload
JOIN aggregate_heads AS head ON head.aggregate_id = payload.aggregate_id;
INSERT INTO revisions(
    resource_id, subject_id, revision_number, parent_revision_id, schema_name,
    schema_version, canonical_payload, payload_sha256, dependency_graph_sha256,
    provenance_json, created_at
)
SELECT
    'migration-v3-revision:' || payload.aggregate_id,
    payload.aggregate_id,
    1,
    NULL,
    payload.schema_name,
    '1',
    payload.canonical_payload,
    sha256(payload.canonical_payload),
    sha256('{"edges":[],"nodes":[]}'),
    json_object(
        'legacy_lifecycle_state', payload.legacy_lifecycle_state,
        'migration', 'v3',
        'needs_metadata_review', json('true')
    ),
    head.created_at
FROM hierarchy_migration_payloads AS payload
JOIN aggregate_heads AS head ON head.aggregate_id = payload.aggregate_id;
UPDATE aggregate_heads
SET current_revision_id = 'migration-v3-revision:' || aggregate_id,
    head_generation = 1,
    lifecycle_state = json_extract(
        (SELECT canonical_payload FROM hierarchy_migration_payloads WHERE aggregate_id = aggregate_heads.aggregate_id),
        '$.status'
    )
WHERE aggregate_id IN (SELECT aggregate_id FROM hierarchy_migration_payloads);
INSERT INTO audit_events(id, workspace_id, resource_id, event_type, generation, payload_json, created_at)
SELECT
    'migration-v3-audit:' || payload.aggregate_id,
    head.workspace_id,
    payload.aggregate_id,
    'hierarchy_revision_migrated',
    1,
    json_object(
        'revision_id', 'migration-v3-revision:' || payload.aggregate_id,
        'needs_metadata_review', json('true'),
        'legacy_lifecycle_state', payload.legacy_lifecycle_state,
        'migrated_lifecycle_state', json_extract(payload.canonical_payload, '$.status')
    ),
    head.created_at
FROM hierarchy_migration_payloads AS payload
JOIN aggregate_heads AS head ON head.aggregate_id = payload.aggregate_id;
DROP TABLE hierarchy_migration_payloads;

CREATE TABLE IF NOT EXISTS research_records (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    subject_resource_id TEXT NOT NULL REFERENCES resources(id),
    record_kind TEXT NOT NULL CHECK (record_kind IN ('note', 'observation', 'decision', 'conclusion')),
    body TEXT NOT NULL,
    author TEXT,
    source_receipt_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_record_id TEXT REFERENCES research_records(resource_id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_research_records_subject_created
    ON research_records(subject_resource_id, created_at, resource_id);
CREATE INDEX IF NOT EXISTS ix_experiment_research_records_workspace_kind
    ON research_records(workspace_id, record_kind, created_at);

CREATE TABLE IF NOT EXISTS domain_adapter_receipts (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    normalized_request_sha256 TEXT NOT NULL CHECK (length(normalized_request_sha256) = 64),
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_domain_adapter_receipts_domain_created
    ON domain_adapter_receipts(domain_experiment_id, created_at, resource_id);
CREATE INDEX IF NOT EXISTS ix_experiment_domain_adapter_receipts_workspace
    ON domain_adapter_receipts(workspace_id, created_at);

CREATE TRIGGER IF NOT EXISTS trg_experiment_aggregate_parent_integrity_insert
BEFORE INSERT ON aggregate_heads
WHEN (NEW.aggregate_kind = 'workspace' AND NEW.parent_id IS NOT NULL)
  OR (NEW.aggregate_kind <> 'workspace' AND NEW.parent_id IS NULL)
  OR (
      NEW.parent_id IS NOT NULL
      AND (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.parent_id) = 'workspace'
                THEN NEW.parent_id
                ELSE (SELECT workspace_id FROM resources WHERE id = NEW.parent_id)
           END) IS NOT NEW.workspace_id
  )
  OR (
      NEW.aggregate_kind = 'experiment'
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) <> 'workspace'
  )
  OR (
      NEW.aggregate_kind = 'domain_experiment'
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) <> 'experiment'
  )
  OR (
      NEW.aggregate_kind IN ('workflow', 'dataset')
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) NOT IN ('workspace', 'experiment')
  )
BEGIN
    SELECT RAISE(ABORT, 'aggregate parent is invalid or crosses workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_aggregate_parent_immutable_update
BEFORE UPDATE OF aggregate_id, aggregate_kind, workspace_id, parent_id ON aggregate_heads
WHEN NEW.aggregate_id IS NOT OLD.aggregate_id
  OR NEW.aggregate_kind IS NOT OLD.aggregate_kind
  OR NEW.workspace_id IS NOT OLD.workspace_id
  OR NEW.parent_id IS NOT OLD.parent_id
BEGIN
    SELECT RAISE(ABORT, 'aggregate identity and parent are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_research_record_same_workspace_insert
BEFORE INSERT ON research_records
WHEN (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.subject_resource_id) = 'workspace'
           THEN NEW.subject_resource_id
           ELSE (SELECT workspace_id FROM resources WHERE id = NEW.subject_resource_id)
      END) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'research record subject must belong to workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_research_record_replacement_same_subject
BEFORE INSERT ON research_records
WHEN NEW.supersedes_record_id IS NOT NULL
 AND (SELECT subject_resource_id FROM research_records WHERE resource_id = NEW.supersedes_record_id) IS NOT NEW.subject_resource_id
BEGIN
    SELECT RAISE(ABORT, 'research record replacement must keep the same subject');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_research_record_immutable_update
BEFORE UPDATE ON research_records
BEGIN
    SELECT RAISE(ABORT, 'research record is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_research_record_immutable_delete
BEFORE DELETE ON research_records
BEGIN
    SELECT RAISE(ABORT, 'research record is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_domain_adapter_receipt_same_workspace_insert
BEFORE INSERT ON domain_adapter_receipts
WHEN (SELECT workspace_id FROM resources WHERE id = NEW.domain_experiment_id) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'domain adapter receipt must belong to workspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_domain_adapter_receipt_immutable_update
BEFORE UPDATE ON domain_adapter_receipts
BEGIN
    SELECT RAISE(ABORT, 'domain adapter receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiment_domain_adapter_receipt_immutable_delete
BEFORE DELETE ON domain_adapter_receipts
BEGIN
    SELECT RAISE(ABORT, 'domain adapter receipt is immutable');
END;
'''

MIGRATION_V4_SQL = r'''
CREATE TRIGGER trg_experiment_external_entity_receipt_immutable_update
BEFORE UPDATE ON external_entity_receipts
BEGIN
    SELECT RAISE(ABORT, 'external entity receipt is immutable');
END;

CREATE TRIGGER trg_experiment_external_entity_receipt_immutable_delete
BEFORE DELETE ON external_entity_receipts
BEGIN
    SELECT RAISE(ABORT, 'external entity receipt is immutable');
END;
'''

MIGRATION_V5_SQL = r'''
CREATE TEMP TABLE aggregate_head_revision_consistency_guard (
    consistent INTEGER NOT NULL CHECK (consistent = 1)
);
INSERT INTO aggregate_head_revision_consistency_guard(consistent)
SELECT 0
FROM aggregate_heads AS head
LEFT JOIN revisions AS revision ON revision.resource_id = head.current_revision_id
WHERE head.aggregate_kind IN ('workspace', 'experiment', 'domain_experiment')
  AND (
      head.current_revision_id IS NULL
      OR revision.subject_id IS NOT head.aggregate_id
      OR CASE
             WHEN json_valid(revision.canonical_payload)
             THEN json_extract(revision.canonical_payload, '$.status')
             ELSE NULL
         END IS NOT head.lifecycle_state
  )
LIMIT 1;
DROP TABLE aggregate_head_revision_consistency_guard;

CREATE TRIGGER trg_experiment_aggregate_head_revision_consistency_insert
BEFORE INSERT ON aggregate_heads
WHEN NEW.aggregate_kind IN ('workspace', 'experiment', 'domain_experiment')
 AND NEW.current_revision_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1
     FROM revisions AS revision
     WHERE revision.resource_id = NEW.current_revision_id
       AND revision.subject_id = NEW.aggregate_id
       AND CASE
               WHEN json_valid(revision.canonical_payload)
               THEN json_extract(revision.canonical_payload, '$.status')
               ELSE NULL
           END IS NEW.lifecycle_state
 )
BEGIN
    SELECT RAISE(ABORT, 'aggregate head lifecycle must match current revision status');
END;

CREATE TRIGGER trg_experiment_aggregate_head_revision_consistency_update
BEFORE UPDATE ON aggregate_heads
WHEN NEW.aggregate_kind IN ('workspace', 'experiment', 'domain_experiment')
 AND (
     NEW.current_revision_id IS NULL
     OR NOT EXISTS (
         SELECT 1
         FROM revisions AS revision
         WHERE revision.resource_id = NEW.current_revision_id
           AND revision.subject_id = NEW.aggregate_id
           AND CASE
                   WHEN json_valid(revision.canonical_payload)
                   THEN json_extract(revision.canonical_payload, '$.status')
                   ELSE NULL
               END IS NEW.lifecycle_state
     )
 )
BEGIN
    SELECT RAISE(ABORT, 'aggregate head lifecycle must match current revision status');
END;
'''

MIGRATION_V6_SQL = r'''
DROP TRIGGER trg_experiment_aggregate_parent_integrity_insert;
CREATE TRIGGER trg_experiment_aggregate_parent_integrity_insert
BEFORE INSERT ON aggregate_heads
WHEN (NEW.aggregate_kind = 'workspace' AND NEW.parent_id IS NOT NULL)
  OR (NEW.aggregate_kind <> 'workspace' AND NEW.parent_id IS NULL)
  OR (
      NEW.parent_id IS NOT NULL
      AND (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.parent_id) = 'workspace'
                THEN NEW.parent_id
                ELSE (SELECT workspace_id FROM resources WHERE id = NEW.parent_id)
           END) IS NOT NEW.workspace_id
  )
  OR (
      NEW.aggregate_kind = 'experiment'
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) <> 'workspace'
  )
  OR (
      NEW.aggregate_kind = 'domain_experiment'
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) <> 'experiment'
  )
  OR (
      NEW.aggregate_kind IN ('workflow', 'dataset')
      AND (SELECT kind FROM resources WHERE id = NEW.parent_id) <> 'domain_experiment'
  )
BEGIN
    SELECT RAISE(ABORT, 'aggregate parent is invalid or crosses workspace');
END;
'''

MIGRATION_V7_SQL = r'''
DROP TRIGGER trg_experiment_external_entity_receipt_immutable_update;
DROP TRIGGER trg_experiment_external_entity_receipt_immutable_delete;
ALTER TABLE external_entity_receipts RENAME TO external_entity_receipts_v6;
CREATE TABLE external_entity_receipts (
    id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    resource_id TEXT NOT NULL REFERENCES resources(id),
    store_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    generation_or_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    availability TEXT NOT NULL CHECK (availability IN ('unknown', 'available', 'unavailable')),
    verification_authority TEXT NOT NULL DEFAULT 'legacy_unverified' CHECK (length(verification_authority) > 0),
    acknowledgement_json TEXT,
    contract_digest TEXT GENERATED ALWAYS AS (
        CASE
            WHEN json_valid(acknowledgement_json)
            THEN COALESCE(
                CAST(json_extract(acknowledgement_json, '$.contract_digest') AS TEXT),
                CAST(json_extract(acknowledgement_json, '$.claimed_contract_digest') AS TEXT),
                ''
            )
            ELSE ''
        END
    ) STORED,
    created_at TEXT NOT NULL,
    UNIQUE(
        workspace_id, store_id, entity_kind, entity_id,
        generation_or_revision, content_digest, contract_digest, availability
    )
);
INSERT INTO external_entity_receipts(
    id, workspace_id, resource_id, store_id, entity_kind, entity_id,
    generation_or_revision, content_digest, availability,
    verification_authority, acknowledgement_json, created_at
)
SELECT
    id, workspace_id, resource_id, store_id, entity_kind, entity_id,
    generation_or_revision, content_digest, availability,
    verification_authority, acknowledgement_json, created_at
FROM external_entity_receipts_v6;
DROP TABLE external_entity_receipts_v6;
CREATE TRIGGER trg_experiment_external_entity_receipt_immutable_update
BEFORE UPDATE ON external_entity_receipts
BEGIN
    SELECT RAISE(ABORT, 'external entity receipt is immutable');
END;
CREATE TRIGGER trg_experiment_external_entity_receipt_immutable_delete
BEFORE DELETE ON external_entity_receipts
BEGIN
    SELECT RAISE(ABORT, 'external entity receipt is immutable');
END;
'''

MIGRATION_V8_SQL = r'''
ALTER TABLE dispatch_outbox ADD COLUMN lease_owner TEXT;
ALTER TABLE dispatch_outbox ADD COLUMN lease_acquired_at TEXT;
ALTER TABLE dispatch_outbox ADD COLUMN lease_expires_at TEXT;
ALTER TABLE run_attempts ADD COLUMN terminal_receipt_sha256 TEXT
    CHECK (terminal_receipt_sha256 IS NULL OR length(terminal_receipt_sha256) = 64);
UPDATE run_attempts
SET terminal_receipt_sha256 = sha256(terminal_receipt_json)
WHERE terminal_receipt_json IS NOT NULL;

CREATE TRIGGER trg_experiment_run_attempt_terminal_receipt_insert
BEFORE INSERT ON run_attempts
WHEN (NEW.terminal_receipt_json IS NULL AND NEW.terminal_receipt_sha256 IS NOT NULL)
  OR (
      NEW.terminal_receipt_json IS NOT NULL
      AND (
          NEW.terminal_receipt_sha256 IS NULL
          OR NEW.terminal_receipt_sha256 != sha256(NEW.terminal_receipt_json)
      )
  )
BEGIN
    SELECT RAISE(ABORT, 'terminal receipt digest mismatch');
END;

CREATE TRIGGER trg_experiment_run_attempt_terminal_receipt_update
BEFORE UPDATE OF terminal_receipt_json, terminal_receipt_sha256 ON run_attempts
WHEN (
    OLD.terminal_receipt_json IS NOT NULL
    AND (
        NEW.terminal_receipt_json IS NOT OLD.terminal_receipt_json
        OR NEW.terminal_receipt_sha256 IS NOT OLD.terminal_receipt_sha256
    )
) OR (
    OLD.terminal_receipt_json IS NULL
    AND (
        (NEW.terminal_receipt_json IS NULL AND NEW.terminal_receipt_sha256 IS NOT NULL)
        OR (
            NEW.terminal_receipt_json IS NOT NULL
            AND (
                NEW.terminal_receipt_sha256 IS NULL
                OR NEW.terminal_receipt_sha256 != sha256(NEW.terminal_receipt_json)
            )
        )
    )
)
BEGIN
    SELECT RAISE(ABORT, 'terminal receipt is immutable or digest-mismatched');
END;

CREATE TRIGGER trg_experiment_run_events_immutable_update
BEFORE UPDATE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run event is immutable');
END;

CREATE TRIGGER trg_experiment_run_events_immutable_delete
BEFORE DELETE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run event is immutable');
END;

DROP TRIGGER IF EXISTS trg_experiment_lineage_same_workspace;
DROP TRIGGER IF EXISTS trg_experiment_lineage_owns_no_cycle;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_source;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_target;
ALTER TABLE lineage_edges RENAME TO lineage_edges_v7;
CREATE TABLE lineage_edges (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    source_resource_id TEXT NOT NULL REFERENCES resources(id),
    target_resource_id TEXT NOT NULL REFERENCES resources(id),
    edge_mode TEXT NOT NULL CHECK (edge_mode IN (
        'owns', 'pins', 'derives_from', 'contains', 'consumes', 'produces', 'retry_of',
        'refines', 'validates', 'promotes_to_dataset', 'imports_from', 'forked_from',
        'references', 'uses_input', 'produced', 'validated_by',
        'retried_from', 'resubmitted_from'
    )),
    edge_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_resource_id, target_resource_id, edge_mode, edge_key)
);
INSERT INTO lineage_edges(
    id, workspace_id, source_resource_id, target_resource_id, edge_mode,
    edge_key, metadata_json, created_at
)
SELECT
    id, workspace_id, source_resource_id, target_resource_id, edge_mode,
    edge_key, metadata_json, created_at
FROM lineage_edges_v7;
DROP TABLE lineage_edges_v7;
CREATE INDEX ix_experiment_lineage_edges_source ON lineage_edges(source_resource_id, edge_mode);
CREATE INDEX ix_experiment_lineage_edges_target ON lineage_edges(target_resource_id, edge_mode);
CREATE TRIGGER trg_experiment_lineage_same_workspace
BEFORE INSERT ON lineage_edges
WHEN NEW.source_resource_id = NEW.target_resource_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.source_resource_id) = 'workspace' THEN NEW.source_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.source_resource_id) END) IS NOT NEW.workspace_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.target_resource_id) = 'workspace' THEN NEW.target_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.target_resource_id) END) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'lineage edge is self-referential or crosses workspace');
END;
CREATE TRIGGER trg_experiment_lineage_owns_no_cycle
BEFORE INSERT ON lineage_edges
WHEN NEW.edge_mode = 'owns'
 AND EXISTS (
   WITH RECURSIVE reachable(id) AS (
       SELECT target_resource_id FROM lineage_edges WHERE source_resource_id = NEW.target_resource_id AND edge_mode = 'owns'
       UNION ALL
       SELECT lineage_edges.target_resource_id
       FROM lineage_edges JOIN reachable ON lineage_edges.source_resource_id = reachable.id
       WHERE lineage_edges.edge_mode = 'owns'
   )
   SELECT 1 FROM reachable WHERE id = NEW.source_resource_id
 )
BEGIN
    SELECT RAISE(ABORT, 'lineage ownership cycle');
END;

CREATE TABLE launch_contexts (
    launch_context_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES resources(id),
    global_experiment_id TEXT NOT NULL REFERENCES resources(id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    workflow_id TEXT REFERENCES resources(id),
    workflow_revision_id TEXT REFERENCES revisions(resource_id),
    source_receipt_id TEXT NOT NULL REFERENCES resources(id),
    return_uri TEXT NOT NULL CHECK (length(return_uri) > 0),
    state TEXT NOT NULL DEFAULT 'issued' CHECK (state IN ('issued', 'claimed', 'consumed')),
    claim_token TEXT,
    canonical_job_id TEXT UNIQUE,
    binding_receipt_json TEXT,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    consumed_at TEXT,
    CHECK (expires_at > issued_at),
    CHECK (workflow_revision_id IS NULL OR workflow_id IS NOT NULL),
    CHECK (
        (state = 'issued' AND claim_token IS NULL AND claimed_at IS NULL
            AND canonical_job_id IS NULL AND binding_receipt_json IS NULL AND consumed_at IS NULL)
        OR
        (state = 'claimed' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL
            AND canonical_job_id IS NULL AND binding_receipt_json IS NULL AND consumed_at IS NULL)
        OR
        (state = 'consumed' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL
            AND canonical_job_id IS NOT NULL AND binding_receipt_json IS NOT NULL
            AND json_valid(binding_receipt_json) AND consumed_at IS NOT NULL)
    )
);
CREATE INDEX ix_experiment_launch_contexts_state_expiry
    ON launch_contexts(state, expires_at);
CREATE INDEX ix_experiment_launch_contexts_domain_issued
    ON launch_contexts(domain_experiment_id, issued_at);

CREATE TRIGGER trg_experiment_launch_context_identity_immutable
BEFORE UPDATE OF launch_context_id, project_id, global_experiment_id, domain_experiment_id,
                 workflow_id, workflow_revision_id, source_receipt_id, return_uri, issued_at, expires_at
ON launch_contexts
BEGIN
    SELECT RAISE(ABORT, 'launch context identity is immutable');
END;

CREATE TRIGGER trg_experiment_launch_context_state_transition
BEFORE UPDATE OF state ON launch_contexts
WHEN NOT (
    (OLD.state = 'issued' AND NEW.state = 'claimed')
    OR (OLD.state = 'claimed' AND NEW.state = 'issued')
    OR (OLD.state = 'claimed' AND NEW.state = 'consumed')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid launch context state transition');
END;

CREATE TRIGGER trg_experiment_launch_context_consumed_immutable
BEFORE UPDATE ON launch_contexts
WHEN OLD.state = 'consumed'
BEGIN
    SELECT RAISE(ABORT, 'consumed launch context is immutable');
END;

CREATE TRIGGER trg_experiment_launch_context_delete_forbidden
BEFORE DELETE ON launch_contexts
BEGIN
    SELECT RAISE(ABORT, 'launch contexts are durable receipts');
END;
'''

MIGRATION_V9_SQL = r'''
CREATE TRIGGER trg_experiment_preparation_immutable_update
BEFORE UPDATE ON workflow_preparations
WHEN OLD.validation_resource_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'workflow preparation is immutable');
END;
CREATE TRIGGER trg_experiment_preparation_immutable_delete
BEFORE DELETE ON workflow_preparations
BEGIN
    SELECT RAISE(ABORT, 'workflow preparation is immutable');
END;
CREATE TRIGGER trg_experiment_dataset_member_digest_insert
BEFORE INSERT ON dataset_revision_members
WHEN sha256(NEW.value_json) <> lower(NEW.content_sha256)
BEGIN
    SELECT RAISE(ABORT, 'dataset member digest mismatch');
END;
CREATE TRIGGER trg_experiment_dataset_member_immutable_update
BEFORE UPDATE ON dataset_revision_members
BEGIN
    SELECT RAISE(ABORT, 'dataset revision member is immutable');
END;
CREATE TRIGGER trg_experiment_dataset_member_immutable_delete
BEFORE DELETE ON dataset_revision_members
BEGIN
    SELECT RAISE(ABORT, 'dataset revision member is immutable');
END;
'''

MIGRATION_V10_SQL = r'''
DROP TRIGGER IF EXISTS trg_experiment_lineage_same_workspace;
DROP TRIGGER IF EXISTS trg_experiment_lineage_owns_no_cycle;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_source;
DROP INDEX IF EXISTS ix_experiment_lineage_edges_target;
ALTER TABLE lineage_edges RENAME TO lineage_edges_v10;
CREATE TABLE lineage_edges (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    source_resource_id TEXT NOT NULL REFERENCES resources(id),
    target_resource_id TEXT NOT NULL REFERENCES resources(id),
    edge_mode TEXT NOT NULL CHECK (edge_mode IN (
        'owns', 'pins', 'derives_from', 'derived_from', 'contains', 'consumes', 'produces', 'retry_of',
        'refines', 'validates', 'promotes_to_dataset', 'imports_from', 'forked_from',
        'references', 'uses_input', 'produced', 'validated_by', 'compared_with',
        'retried_from', 'resubmitted_from'
    )),
    edge_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_resource_id, target_resource_id, edge_mode, edge_key)
);
INSERT INTO lineage_edges SELECT * FROM lineage_edges_v10;
DROP TABLE lineage_edges_v10;
CREATE INDEX ix_experiment_lineage_edges_source ON lineage_edges(source_resource_id, edge_mode);
CREATE INDEX ix_experiment_lineage_edges_target ON lineage_edges(target_resource_id, edge_mode);
CREATE TRIGGER trg_experiment_lineage_same_workspace
BEFORE INSERT ON lineage_edges
WHEN NEW.source_resource_id = NEW.target_resource_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.source_resource_id) = 'workspace' THEN NEW.source_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.source_resource_id) END) IS NOT NEW.workspace_id
  OR (CASE WHEN (SELECT kind FROM resources WHERE id = NEW.target_resource_id) = 'workspace' THEN NEW.target_resource_id ELSE (SELECT workspace_id FROM resources WHERE id = NEW.target_resource_id) END) IS NOT NEW.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'lineage edge is self-referential or crosses workspace');
END;
CREATE TRIGGER trg_experiment_lineage_owns_no_cycle
BEFORE INSERT ON lineage_edges
WHEN NEW.edge_mode = 'owns'
 AND EXISTS (
   WITH RECURSIVE reachable(id) AS (
       SELECT target_resource_id FROM lineage_edges WHERE source_resource_id = NEW.target_resource_id AND edge_mode = 'owns'
       UNION ALL
       SELECT lineage_edges.target_resource_id
       FROM lineage_edges JOIN reachable ON lineage_edges.source_resource_id = reachable.id
       WHERE lineage_edges.edge_mode = 'owns'
   )
   SELECT 1 FROM reachable WHERE id = NEW.source_resource_id
 )
BEGIN
    SELECT RAISE(ABORT, 'lineage ownership cycle');
END;
'''

MIGRATION_V11_SQL = r'''
ALTER TABLE aggregate_heads ADD COLUMN dataset_kind TEXT;
UPDATE aggregate_heads
   SET dataset_kind = description
 WHERE aggregate_kind = 'dataset'
   AND description IN (
       'ngs_molbio.molecular_construct_cohort.v1',
       'ngs_molbio.sample_cohort.v1',
       'ngs_molbio.reference_comparison_panel_cohort.v1',
       'ngs_molbio.acquisition_run_input_cohort.v1',
       'ngs_molbio.qc_analysis_result_cohort.v1',
       'ngs_molbio.saved_review_comparison_cohort.v1'
   );
CREATE INDEX ix_experiment_aggregate_heads_dataset_kind
    ON aggregate_heads(workspace_id, parent_id, dataset_kind, lifecycle_state);
CREATE TRIGGER trg_experiment_dataset_kind_insert
BEFORE INSERT ON aggregate_heads
WHEN (NEW.aggregate_kind = 'dataset' AND NEW.dataset_kind IS NULL)
  OR (NEW.aggregate_kind <> 'dataset' AND NEW.dataset_kind IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'new Datasets require a kind and non-Datasets cannot have one');
END;
CREATE TRIGGER trg_experiment_dataset_kind_update
BEFORE UPDATE OF dataset_kind, aggregate_kind ON aggregate_heads
WHEN (NEW.aggregate_kind = 'dataset' AND NEW.dataset_kind IS NULL AND OLD.dataset_kind IS NOT NULL)
  OR (NEW.aggregate_kind <> 'dataset' AND NEW.dataset_kind IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'invalid Dataset kind authority');
END;

CREATE TEMP TABLE v11_attempt_backfill_attestation(
    value INTEGER NOT NULL,
    CONSTRAINT v11_attempt_backfill_exact_dispatch_authority CHECK(value = 0)
);
INSERT INTO v11_attempt_backfill_attestation
SELECT count(*)
  FROM run_attempts AS attempt
 WHERE (
    SELECT count(*)
      FROM dispatch_outbox AS dispatch
      JOIN workflow_preparations AS preparation
        ON preparation.workspace_id = attempt.workspace_id
       AND preparation.workflow_revision_id = json_extract(dispatch.payload_json, '$.workflow_revision_id')
       AND json(preparation.scheduler_payload_json) = json(json_extract(dispatch.payload_json, '$.scheduler'))
     WHERE dispatch.run_attempt_id = attempt.resource_id
       AND dispatch.event_type = 'materialize_scheduler_job'
       AND dispatch.payload_sha256 = sha256(dispatch.payload_json)
       AND json_extract(dispatch.payload_json, '$.workflow_run_id') = attempt.workflow_run_id
       AND json_extract(dispatch.payload_json, '$.attempt_id') = attempt.resource_id
       AND json_extract(dispatch.payload_json, '$.scheduler_job_id') = attempt.scheduler_job_id
 ) <> 1;
DROP TABLE v11_attempt_backfill_attestation;
DROP TRIGGER trg_experiment_run_attempt_terminal_receipt_insert;
DROP TRIGGER trg_experiment_run_attempt_terminal_receipt_update;
DROP TRIGGER trg_experiment_outbox_digest_insert;
DROP TRIGGER trg_experiment_outbox_payload_immutable;
DROP INDEX ix_experiment_outbox_status_created;
ALTER TABLE dispatch_outbox RENAME TO dispatch_outbox_v10;
ALTER TABLE run_attempts RENAME TO run_attempts_v10;
CREATE TABLE run_attempts (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(resource_id),
    preparation_id TEXT NOT NULL REFERENCES workflow_preparations(resource_id),
    attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
    scheduler_job_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','dispatching','dispatched','running','completed','failed','cancelled')),
    external_binding_receipt_json TEXT,
    created_at TEXT NOT NULL,
    runtime_identity_json TEXT,
    terminal_receipt_json TEXT,
    terminal_receipt_sha256 TEXT CHECK(terminal_receipt_sha256 IS NULL OR length(terminal_receipt_sha256) = 64),
    UNIQUE(workflow_run_id, attempt_number),
    UNIQUE(scheduler_job_id)
);
INSERT INTO run_attempts(
    resource_id, workspace_id, workflow_run_id, preparation_id, attempt_number,
    scheduler_job_id, state, external_binding_receipt_json, created_at,
    runtime_identity_json, terminal_receipt_json, terminal_receipt_sha256
)
SELECT attempt.resource_id, attempt.workspace_id, attempt.workflow_run_id,
       (
           SELECT preparation.resource_id
             FROM dispatch_outbox_v10 AS dispatch
             JOIN workflow_preparations AS preparation
               ON preparation.workspace_id = attempt.workspace_id
              AND preparation.workflow_revision_id = json_extract(dispatch.payload_json, '$.workflow_revision_id')
              AND json(preparation.scheduler_payload_json) = json(json_extract(dispatch.payload_json, '$.scheduler'))
            WHERE dispatch.run_attempt_id = attempt.resource_id
              AND dispatch.event_type = 'materialize_scheduler_job'
              AND dispatch.payload_sha256 = sha256(dispatch.payload_json)
              AND json_extract(dispatch.payload_json, '$.workflow_run_id') = attempt.workflow_run_id
              AND json_extract(dispatch.payload_json, '$.attempt_id') = attempt.resource_id
              AND json_extract(dispatch.payload_json, '$.scheduler_job_id') = attempt.scheduler_job_id
       ),
       attempt.attempt_number, attempt.scheduler_job_id,
       attempt.state, attempt.external_binding_receipt_json, attempt.created_at,
       attempt.runtime_identity_json, attempt.terminal_receipt_json,
       attempt.terminal_receipt_sha256
  FROM run_attempts_v10 AS attempt;
CREATE TABLE dispatch_outbox (
    id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    run_attempt_id TEXT NOT NULL REFERENCES run_attempts(resource_id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    status TEXT NOT NULL CHECK(status IN ('pending','dispatching','acknowledged','failed')),
    dispatch_attempts INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_attempts >= 0),
    lease_token TEXT,
    last_error TEXT,
    acknowledgement_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_acquired_at TEXT,
    lease_expires_at TEXT,
    UNIQUE(event_type, run_attempt_id)
);
INSERT INTO dispatch_outbox(
    id, workspace_id, run_attempt_id, event_type, payload_json, payload_sha256,
    status, dispatch_attempts, lease_token, last_error, acknowledgement_json,
    created_at, updated_at, lease_owner, lease_acquired_at, lease_expires_at
)
SELECT id, workspace_id, run_attempt_id, event_type, payload_json, payload_sha256,
       status, dispatch_attempts, lease_token, last_error, acknowledgement_json,
       created_at, updated_at, lease_owner, lease_acquired_at, lease_expires_at
  FROM dispatch_outbox_v10;
DROP TABLE dispatch_outbox_v10;
DROP TABLE run_attempts_v10;
CREATE INDEX ix_experiment_outbox_status_created ON dispatch_outbox(status, created_at);
CREATE TRIGGER trg_experiment_outbox_digest_insert
BEFORE INSERT ON dispatch_outbox
WHEN sha256(NEW.payload_json) <> lower(NEW.payload_sha256)
BEGIN SELECT RAISE(ABORT, 'dispatch outbox payload digest mismatch'); END;
CREATE TRIGGER trg_experiment_outbox_payload_immutable
BEFORE UPDATE OF id, workspace_id, run_attempt_id, event_type, payload_json, payload_sha256 ON dispatch_outbox
BEGIN SELECT RAISE(ABORT, 'dispatch outbox payload is immutable'); END;
CREATE TRIGGER trg_experiment_run_attempt_terminal_receipt_insert
BEFORE INSERT ON run_attempts
WHEN (NEW.terminal_receipt_json IS NULL AND NEW.terminal_receipt_sha256 IS NOT NULL)
  OR (NEW.terminal_receipt_json IS NOT NULL AND (
      NEW.terminal_receipt_sha256 IS NULL
      OR NEW.terminal_receipt_sha256 != sha256(NEW.terminal_receipt_json)))
BEGIN SELECT RAISE(ABORT, 'terminal receipt digest mismatch'); END;
CREATE TRIGGER trg_experiment_run_attempt_terminal_receipt_update
BEFORE UPDATE OF terminal_receipt_json, terminal_receipt_sha256 ON run_attempts
WHEN (OLD.terminal_receipt_json IS NOT NULL AND (
        NEW.terminal_receipt_json IS NOT OLD.terminal_receipt_json
        OR NEW.terminal_receipt_sha256 IS NOT OLD.terminal_receipt_sha256))
  OR (OLD.terminal_receipt_json IS NULL AND (
        (NEW.terminal_receipt_json IS NULL AND NEW.terminal_receipt_sha256 IS NOT NULL)
        OR (NEW.terminal_receipt_json IS NOT NULL AND (
            NEW.terminal_receipt_sha256 IS NULL
            OR NEW.terminal_receipt_sha256 != sha256(NEW.terminal_receipt_json)))))
BEGIN SELECT RAISE(ABORT, 'terminal receipt is immutable or digest-mismatched'); END;
CREATE INDEX ix_experiment_run_attempts_preparation ON run_attempts(preparation_id, created_at);
CREATE TRIGGER trg_experiment_run_attempt_preparation_insert
BEFORE INSERT ON run_attempts
WHEN NEW.preparation_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'run attempt requires direct immutable preparation authority');
END;
CREATE TRIGGER trg_experiment_run_attempt_preparation_immutable
BEFORE UPDATE OF preparation_id ON run_attempts
BEGIN
    SELECT RAISE(ABORT, 'run attempt preparation is immutable');
END;

DROP TRIGGER trg_experiment_launch_context_identity_immutable;
DROP TRIGGER trg_experiment_launch_context_state_transition;
DROP TRIGGER trg_experiment_launch_context_consumed_immutable;
DROP TRIGGER trg_experiment_launch_context_delete_forbidden;
DROP INDEX ix_experiment_launch_contexts_state_expiry;
DROP INDEX ix_experiment_launch_contexts_domain_issued;
ALTER TABLE launch_contexts RENAME TO launch_contexts_v10;
CREATE TABLE launch_contexts (
    launch_context_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES resources(id),
    global_experiment_id TEXT NOT NULL REFERENCES resources(id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    workflow_id TEXT REFERENCES resources(id),
    workflow_revision_id TEXT REFERENCES revisions(resource_id),
    preparation_id TEXT REFERENCES workflow_preparations(resource_id),
    run_attempt_id TEXT REFERENCES run_attempts(resource_id),
    contract_version TEXT NOT NULL CHECK(contract_version IN ('1','2')),
    normalized_request_sha256 TEXT,
    validation_receipt_id TEXT REFERENCES resources(id),
    validation_receipt_sha256 TEXT,
    source_receipt_id TEXT NOT NULL REFERENCES resources(id),
    return_uri TEXT NOT NULL CHECK(length(return_uri) > 0),
    state TEXT NOT NULL CHECK(state IN ('issued','claimed','reserved','consumed')),
    claim_token TEXT,
    canonical_job_id TEXT UNIQUE,
    binding_receipt_json TEXT,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    consumed_at TEXT,
    CHECK(expires_at > issued_at),
    CHECK(workflow_revision_id IS NULL OR workflow_id IS NOT NULL),
    CHECK(
        (contract_version = '1' AND preparation_id IS NULL AND run_attempt_id IS NULL
         AND normalized_request_sha256 IS NULL AND validation_receipt_id IS NULL
         AND validation_receipt_sha256 IS NULL AND (
            (state = 'issued' AND claim_token IS NULL AND claimed_at IS NULL
             AND canonical_job_id IS NULL AND binding_receipt_json IS NULL AND consumed_at IS NULL)
            OR (state = 'claimed' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL
                AND canonical_job_id IS NULL AND binding_receipt_json IS NULL AND consumed_at IS NULL)
            OR (state = 'consumed' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL
                AND canonical_job_id IS NOT NULL AND binding_receipt_json IS NOT NULL
                AND json_valid(binding_receipt_json) AND consumed_at IS NOT NULL)
         ))
        OR
        (contract_version = '2' AND preparation_id IS NOT NULL
         AND normalized_request_sha256 IS NOT NULL AND length(normalized_request_sha256) = 64
         AND validation_receipt_id IS NOT NULL AND validation_receipt_sha256 IS NOT NULL
         AND length(validation_receipt_sha256) = 64 AND (
            (state = 'issued' AND run_attempt_id IS NULL AND canonical_job_id IS NULL
             AND binding_receipt_json IS NULL AND consumed_at IS NULL)
            OR (state = 'reserved' AND run_attempt_id IS NOT NULL AND canonical_job_id IS NULL
                AND binding_receipt_json IS NULL AND consumed_at IS NULL)
            OR (state = 'consumed' AND run_attempt_id IS NOT NULL AND canonical_job_id IS NOT NULL
                AND binding_receipt_json IS NOT NULL AND json_valid(binding_receipt_json)
                AND consumed_at IS NOT NULL)
         ))
    )
);
INSERT INTO launch_contexts(
    launch_context_id, project_id, global_experiment_id, domain_experiment_id,
    workflow_id, workflow_revision_id, preparation_id, run_attempt_id,
    contract_version, normalized_request_sha256, validation_receipt_id,
    validation_receipt_sha256, source_receipt_id, return_uri, state, claim_token,
    canonical_job_id, binding_receipt_json, issued_at, expires_at, claimed_at, consumed_at
)
SELECT launch_context_id, project_id, global_experiment_id, domain_experiment_id,
       workflow_id, workflow_revision_id, NULL, NULL, '1', NULL, NULL, NULL,
       source_receipt_id, return_uri, state, claim_token, canonical_job_id,
       binding_receipt_json, issued_at, expires_at, claimed_at, consumed_at
  FROM launch_contexts_v10;
DROP TABLE launch_contexts_v10;
CREATE INDEX ix_experiment_launch_contexts_state_expiry ON launch_contexts(state, expires_at);
CREATE INDEX ix_experiment_launch_contexts_domain_issued ON launch_contexts(domain_experiment_id, issued_at);
CREATE UNIQUE INDEX ux_experiment_launch_context_attempt ON launch_contexts(run_attempt_id) WHERE run_attempt_id IS NOT NULL;
CREATE TRIGGER trg_experiment_launch_context_identity_immutable
BEFORE UPDATE OF launch_context_id, project_id, global_experiment_id, domain_experiment_id,
 workflow_id, workflow_revision_id, source_receipt_id, return_uri, issued_at, expires_at
ON launch_contexts BEGIN SELECT RAISE(ABORT, 'launch context identity is immutable'); END;
CREATE TRIGGER trg_experiment_launch_context_state_transition
BEFORE UPDATE OF state ON launch_contexts
WHEN OLD.contract_version = '1' AND NOT (
    (OLD.state = 'issued' AND NEW.state = 'claimed')
    OR (OLD.state = 'claimed' AND NEW.state = 'issued')
    OR (OLD.state = 'claimed' AND NEW.state = 'consumed')
)
BEGIN SELECT RAISE(ABORT, 'invalid launch context state transition'); END;
CREATE TRIGGER trg_experiment_launch_context_consumed_immutable
BEFORE UPDATE ON launch_contexts WHEN OLD.state = 'consumed'
BEGIN SELECT RAISE(ABORT, 'consumed launch context is immutable'); END;
CREATE TRIGGER trg_experiment_launch_context_delete_forbidden
BEFORE DELETE ON launch_contexts
BEGIN SELECT RAISE(ABORT, 'launch contexts are durable receipts'); END;
CREATE TRIGGER trg_experiment_launch_context_v2_identity_immutable
BEFORE UPDATE OF preparation_id, contract_version, normalized_request_sha256,
 validation_receipt_id, validation_receipt_sha256 ON launch_contexts
BEGIN SELECT RAISE(ABORT, 'launch context v2 identity is immutable'); END;
CREATE TRIGGER trg_experiment_launch_context_v2_insert
BEFORE INSERT ON launch_contexts
WHEN NEW.contract_version NOT IN ('1', '2')
  OR (NEW.contract_version = '2' AND (
      NEW.preparation_id IS NULL OR NEW.normalized_request_sha256 IS NULL
      OR length(NEW.normalized_request_sha256) <> 64
      OR NEW.validation_receipt_id IS NULL OR NEW.validation_receipt_sha256 IS NULL
      OR length(NEW.validation_receipt_sha256) <> 64 OR NEW.state <> 'issued'
  ))
BEGIN
    SELECT RAISE(ABORT, 'invalid launch context v2 authority');
END;
CREATE TRIGGER trg_experiment_launch_context_v2_state
BEFORE UPDATE OF state, run_attempt_id ON launch_contexts
WHEN NEW.contract_version = '2' AND NOT (
    (OLD.state = 'issued' AND NEW.state = 'reserved' AND NEW.run_attempt_id IS NOT NULL)
    OR (OLD.state = 'reserved' AND NEW.state = 'consumed' AND NEW.run_attempt_id = OLD.run_attempt_id)
)
BEGIN
    SELECT RAISE(ABORT, 'invalid launch context v2 lifecycle');
END;
'''

MIGRATION_V12_SQL = r'''
CREATE TABLE domain_connector_commands (
    command_id TEXT PRIMARY KEY NOT NULL,
    request_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    idempotency_request_sha256 TEXT NOT NULL CHECK(length(idempotency_request_sha256) = 64),
    operation TEXT NOT NULL CHECK(operation IN ('initialize', 'reverify')),
    project_id TEXT NOT NULL REFERENCES resources(id),
    project_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    global_experiment_id TEXT NOT NULL REFERENCES resources(id),
    global_experiment_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    domain_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    domain_revision_sha256 TEXT NOT NULL CHECK(length(domain_revision_sha256) = 64),
    prior_binding_revision_id TEXT,
    global_receipt_id TEXT NOT NULL REFERENCES domain_adapter_receipts(resource_id),
    global_receipt_sha256 TEXT NOT NULL CHECK(length(global_receipt_sha256) = 64),
    command_json TEXT NOT NULL,
    command_sha256 TEXT NOT NULL CHECK(length(command_sha256) = 64),
    status TEXT NOT NULL CHECK(status IN ('pending','leased','applied','duplicate','retryable','conflicted')),
    lease_owner TEXT, lease_token TEXT, lease_expires_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    next_retry_at TEXT, last_error TEXT,
    acknowledgement_id TEXT, acknowledgement_json TEXT, acknowledgement_sha256 TEXT,
    binding_revision_id TEXT, conflict_json TEXT, conflict_sha256 TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(request_scope, idempotency_key),
    CHECK(sha256(command_json) = lower(command_sha256)),
    CHECK((status = 'leased') = (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK((acknowledgement_json IS NULL AND acknowledgement_sha256 IS NULL AND acknowledgement_id IS NULL)
       OR (acknowledgement_json IS NOT NULL AND acknowledgement_sha256 IS NOT NULL AND acknowledgement_id IS NOT NULL
           AND sha256(acknowledgement_json) = lower(acknowledgement_sha256))),
    CHECK((conflict_json IS NULL AND conflict_sha256 IS NULL)
       OR (conflict_json IS NOT NULL AND conflict_sha256 IS NOT NULL AND sha256(conflict_json) = lower(conflict_sha256))),
    CHECK((status IN ('applied','duplicate')) = (acknowledgement_id IS NOT NULL)),
    CHECK((status = 'conflicted') = (conflict_json IS NOT NULL))
);
CREATE INDEX ix_experiment_connector_commands_ready
    ON domain_connector_commands(status, next_retry_at, created_at);
CREATE INDEX ix_experiment_connector_commands_domain
    ON domain_connector_commands(domain_experiment_id, created_at);

CREATE TABLE domain_connector_streams (
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    binding_revision_id TEXT NOT NULL,
    event_stream TEXT NOT NULL,
    last_applied_stream_generation INTEGER NOT NULL DEFAULT 0 CHECK(last_applied_stream_generation >= 0),
    last_event_id TEXT, last_payload_sha256 TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(domain_experiment_id, binding_revision_id, event_stream)
);
CREATE TABLE domain_connector_inbox (
    event_id TEXT PRIMARY KEY NOT NULL,
    source_store_id TEXT NOT NULL,
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    binding_revision_id TEXT NOT NULL,
    state_revision_id TEXT,
    event_type TEXT NOT NULL,
    event_stream TEXT NOT NULL,
    stream_generation INTEGER NOT NULL CHECK(stream_generation > 0),
    source_generation INTEGER,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    envelope_json TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL CHECK(length(envelope_sha256) = 64),
    disposition TEXT NOT NULL CHECK(disposition IN ('applied','duplicate','deferred_gap','conflicted')),
    acknowledgement_json TEXT NOT NULL,
    acknowledgement_sha256 TEXT NOT NULL CHECK(length(acknowledgement_sha256) = 64),
    conflict_json TEXT, conflict_sha256 TEXT,
    occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, applied_at TEXT,
    UNIQUE(domain_experiment_id, binding_revision_id, event_stream, stream_generation),
    CHECK(sha256(payload_json) = lower(payload_sha256)),
    CHECK(sha256(envelope_json) = lower(envelope_sha256)),
    CHECK(sha256(acknowledgement_json) = lower(acknowledgement_sha256)),
    CHECK((conflict_json IS NULL AND conflict_sha256 IS NULL)
       OR (conflict_json IS NOT NULL AND conflict_sha256 IS NOT NULL AND sha256(conflict_json) = lower(conflict_sha256)))
);
CREATE INDEX ix_experiment_connector_inbox_deferred
    ON domain_connector_inbox(domain_experiment_id, binding_revision_id, event_stream, disposition, stream_generation);
CREATE TABLE domain_connector_conflicts (
    conflict_id TEXT PRIMARY KEY NOT NULL,
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    binding_revision_id TEXT NOT NULL, event_stream TEXT NOT NULL,
    stream_generation INTEGER NOT NULL, event_id TEXT NOT NULL,
    conflict_json TEXT NOT NULL, conflict_sha256 TEXT NOT NULL CHECK(length(conflict_sha256)=64),
    created_at TEXT NOT NULL,
    CHECK(sha256(conflict_json)=lower(conflict_sha256))
);
CREATE INDEX ix_experiment_connector_conflicts_stream
    ON domain_connector_conflicts(domain_experiment_id, binding_revision_id, event_stream, stream_generation);
CREATE TRIGGER trg_experiment_connector_command_identity_immutable
BEFORE UPDATE OF command_id, request_scope, idempotency_key, idempotency_request_sha256, operation,
 project_id, project_revision_id, global_experiment_id, global_experiment_revision_id,
 domain_experiment_id, domain_revision_id, domain_revision_sha256, prior_binding_revision_id,
 global_receipt_id, global_receipt_sha256, command_json, command_sha256, created_at
ON domain_connector_commands BEGIN SELECT RAISE(ABORT, 'connector command identity is immutable'); END;
CREATE TRIGGER trg_experiment_connector_inbox_immutable_update
BEFORE UPDATE OF event_id, source_store_id, domain_experiment_id, binding_revision_id,
 state_revision_id, event_type, event_stream, stream_generation, source_generation,
 payload_json, payload_sha256, envelope_json, envelope_sha256, occurred_at, received_at
ON domain_connector_inbox
BEGIN SELECT RAISE(ABORT, 'connector inbox identity is immutable'); END;
CREATE TRIGGER trg_experiment_connector_inbox_state_transition
BEFORE UPDATE OF disposition, acknowledgement_json, acknowledgement_sha256, applied_at
ON domain_connector_inbox
WHEN OLD.disposition <> 'deferred_gap' OR NEW.disposition <> 'applied'
  OR NEW.applied_at IS NULL OR NEW.acknowledgement_json IS OLD.acknowledgement_json
BEGIN SELECT RAISE(ABORT, 'invalid deferred connector inbox transition'); END;
CREATE TRIGGER trg_experiment_connector_inbox_immutable_delete BEFORE DELETE ON domain_connector_inbox
BEGIN SELECT RAISE(ABORT, 'connector inbox is immutable'); END;
CREATE TRIGGER trg_experiment_connector_stream_no_regression
BEFORE UPDATE OF last_applied_stream_generation ON domain_connector_streams
WHEN NEW.last_applied_stream_generation <= OLD.last_applied_stream_generation
BEGIN SELECT RAISE(ABORT, 'connector stream cursor cannot regress'); END;
CREATE TRIGGER trg_experiment_connector_conflict_immutable_update BEFORE UPDATE ON domain_connector_conflicts
BEGIN SELECT RAISE(ABORT, 'connector conflict is immutable'); END;
CREATE TRIGGER trg_experiment_connector_conflict_immutable_delete BEFORE DELETE ON domain_connector_conflicts
BEGIN SELECT RAISE(ABORT, 'connector conflict is immutable'); END;
'''

MIGRATION_V13_SQL = r'''
CREATE TABLE resource_admission_policy (
    policy_id TEXT PRIMARY KEY NOT NULL,
    policy_version TEXT NOT NULL,
    cpu_thread_limit INTEGER NOT NULL CHECK(cpu_thread_limit > 0),
    dram_byte_limit INTEGER NOT NULL CHECK(dram_byte_limit > 0),
    lock_generation INTEGER NOT NULL DEFAULT 0 CHECK(lock_generation >= 0),
    updated_at TEXT NOT NULL
);
INSERT INTO resource_admission_policy(
    policy_id, policy_version, cpu_thread_limit, dram_byte_limit, lock_generation, updated_at
) VALUES ('managed-workflows', 'bms.resource-admission-policy.v1', 24, 103079215104, 0,
          strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE resource_admissions (
    admission_id TEXT PRIMARY KEY NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    plan_id TEXT NOT NULL REFERENCES resources(id),
    preparation_id TEXT NOT NULL REFERENCES workflow_preparations(resource_id),
    run_attempt_id TEXT REFERENCES run_attempts(resource_id) UNIQUE,
    canonical_job_id TEXT UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('admitted','queued','refused','released')),
    cpu_threads INTEGER NOT NULL CHECK(cpu_threads > 0 AND cpu_threads <= 24),
    dram_bytes INTEGER NOT NULL CHECK(dram_bytes > 0 AND dram_bytes <= 103079215104),
    gpu_index INTEGER CHECK(gpu_index IS NULL OR gpu_index >= 0),
    gpu_uuid TEXT,
    policy_source TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    owner TEXT NOT NULL,
    lease_token TEXT,
    refusal_code TEXT,
    refusal_reason TEXT,
    release_reason TEXT,
    recovery_evidence_json TEXT,
    admitted_at TEXT,
    queued_at TEXT,
    released_at TEXT,
    reconciled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((state = 'refused') = (refusal_code IS NOT NULL AND refusal_reason IS NOT NULL)),
    CHECK((state = 'released') = (released_at IS NOT NULL AND release_reason IS NOT NULL)),
    CHECK((gpu_index IS NULL) = (gpu_uuid IS NULL))
);
CREATE INDEX ix_experiment_resource_admissions_state
    ON resource_admissions(state, created_at, admission_id);
CREATE INDEX ix_experiment_resource_admissions_scope
    ON resource_admissions(workspace_id, domain_experiment_id, created_at, admission_id);
CREATE TRIGGER trg_experiment_resource_admission_identity_immutable
BEFORE UPDATE OF admission_id, workspace_id, domain_experiment_id, plan_id, preparation_id,
 run_attempt_id, canonical_job_id, cpu_threads, dram_bytes, gpu_index, gpu_uuid,
 policy_source, policy_version, owner, created_at ON resource_admissions
WHEN OLD.run_attempt_id IS NOT NULL OR NEW.run_attempt_id IS NULL
BEGIN SELECT RAISE(ABORT, 'resource admission identity is immutable after attempt binding'); END;
CREATE TRIGGER trg_experiment_resource_admission_transition
BEFORE UPDATE OF state ON resource_admissions
WHEN NOT ((OLD.state = 'admitted' AND NEW.state IN ('queued','released'))
       OR (OLD.state = 'queued' AND NEW.state = 'released')
       OR OLD.state = NEW.state)
BEGIN SELECT RAISE(ABORT, 'invalid resource admission transition'); END;

CREATE TABLE operational_receipts (
    receipt_id TEXT PRIMARY KEY NOT NULL,
    operation_kind TEXT NOT NULL CHECK(operation_kind IN ('backup','export','restoration','payload_audit','package_acceptance')),
    workspace_id TEXT REFERENCES resources(id),
    native_identity TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('created','verified','failed')),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256) = 64),
    source_revision TEXT,
    occurred_at TEXT NOT NULL,
    verified_at TEXT,
    CHECK(sha256(receipt_json) = lower(receipt_sha256))
);
CREATE INDEX ix_experiment_operational_receipts_kind_time
    ON operational_receipts(operation_kind, occurred_at, receipt_id);
CREATE TRIGGER trg_experiment_operational_receipt_immutable_update
BEFORE UPDATE ON operational_receipts
BEGIN SELECT RAISE(ABORT, 'operational receipt is immutable'); END;
CREATE TRIGGER trg_experiment_operational_receipt_immutable_delete
BEFORE DELETE ON operational_receipts
BEGIN SELECT RAISE(ABORT, 'operational receipt is immutable'); END;
'''

MIGRATION_V14_SQL = r'''
DROP TRIGGER IF EXISTS trg_experiment_preparation_immutable_update;
CREATE TRIGGER trg_experiment_preparation_immutable_update
BEFORE UPDATE ON workflow_preparations
BEGIN
    SELECT RAISE(ABORT, 'workflow preparation is immutable');
END;
CREATE TABLE workflow_plan_authority (
    workflow_id TEXT PRIMARY KEY NOT NULL REFERENCES aggregate_heads(aggregate_id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    domain_experiment_id TEXT NOT NULL REFERENCES resources(id),
    expected_domain_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
    capability_contract_json TEXT NOT NULL,
    capability_contract_sha256 TEXT NOT NULL CHECK(length(capability_contract_sha256) = 64),
    created_at TEXT NOT NULL
);
CREATE INDEX ix_experiment_workflow_plan_authority_scope
    ON workflow_plan_authority(workspace_id, domain_experiment_id, created_at, workflow_id);
CREATE TRIGGER trg_experiment_workflow_plan_authority_scope_insert
BEFORE INSERT ON workflow_plan_authority
FOR EACH ROW
WHEN
    NOT EXISTS (
        SELECT 1
          FROM aggregate_heads AS workflow
         WHERE workflow.aggregate_id = NEW.workflow_id
           AND workflow.aggregate_kind = 'workflow'
           AND workflow.workspace_id = NEW.workspace_id
           AND workflow.parent_id = NEW.domain_experiment_id
           AND workflow.description = json_extract(NEW.capability_contract_json, '$.capability.capability_id')
    )
    OR NOT EXISTS (
        SELECT 1
          FROM aggregate_heads AS domain
         WHERE domain.aggregate_id = NEW.domain_experiment_id
           AND domain.aggregate_kind = 'domain_experiment'
           AND domain.workspace_id = NEW.workspace_id
           AND domain.current_revision_id = NEW.expected_domain_revision_id
    )
    OR NOT EXISTS (
        SELECT 1
          FROM revisions AS revision
         WHERE revision.resource_id = NEW.expected_domain_revision_id
           AND revision.subject_id = NEW.domain_experiment_id
    )
    OR json_valid(NEW.capability_contract_json) <> 1
    OR json_extract(NEW.capability_contract_json, '$.schema') <> 'bms.workflow-plan-capability-contract.v1'
BEGIN SELECT RAISE(ABORT, 'workflow Plan authority scope mismatch'); END;
CREATE TRIGGER trg_experiment_workflow_plan_authority_digest_insert
BEFORE INSERT ON workflow_plan_authority
WHEN sha256(NEW.capability_contract_json) <> lower(NEW.capability_contract_sha256)
BEGIN SELECT RAISE(ABORT, 'workflow Plan capability contract digest mismatch'); END;
CREATE TRIGGER trg_experiment_workflow_plan_authority_immutable_update
BEFORE UPDATE ON workflow_plan_authority
BEGIN SELECT RAISE(ABORT, 'workflow Plan authority is immutable'); END;
CREATE TRIGGER trg_experiment_workflow_plan_authority_immutable_delete
BEFORE DELETE ON workflow_plan_authority
BEGIN SELECT RAISE(ABORT, 'workflow Plan authority is immutable'); END;
'''

MIGRATION_V15_SQL = r'''
ALTER TABLE idempotency_claims ADD COLUMN response_sha256 TEXT
    CHECK (
        response_sha256 IS NULL
        OR (
            length(response_sha256) = 64
            AND response_sha256 = lower(response_sha256)
            AND response_sha256 NOT GLOB '*[^0-9a-f]*'
            AND canonical_object_json(response_json) IS NOT NULL
            AND response_json = canonical_object_json(response_json)
            AND response_sha256 = sha256(response_json)
        )
    );
CREATE TRIGGER trg_experiment_idempotency_response_digest_insert
BEFORE INSERT ON idempotency_claims
WHEN NEW.response_sha256 IS NULL
    OR canonical_object_json(NEW.response_json) IS NULL
    OR NEW.response_json != canonical_object_json(NEW.response_json)
    OR NEW.response_sha256 != sha256(NEW.response_json)
BEGIN SELECT RAISE(ABORT, 'canonical idempotency response digest is required'); END;
CREATE TRIGGER trg_experiment_idempotency_response_digest_update
BEFORE UPDATE ON idempotency_claims
WHEN NEW.response_sha256 IS NULL
    OR canonical_object_json(NEW.response_json) IS NULL
    OR NEW.response_json != canonical_object_json(NEW.response_json)
    OR NEW.response_sha256 != sha256(NEW.response_json)
BEGIN SELECT RAISE(ABORT, 'canonical idempotency response digest is required'); END;
CREATE TRIGGER trg_experiment_idempotency_response_immutable_update
BEFORE UPDATE ON idempotency_claims
WHEN OLD.response_sha256 IS NOT NULL AND (
    NEW.response_json != OLD.response_json
    OR NEW.response_sha256 != OLD.response_sha256
)
BEGIN SELECT RAISE(ABORT, 'idempotency response authority is immutable'); END;
'''

MIGRATION_V16_SQL = r'''
DROP INDEX IF EXISTS ix_experiment_run_groups_workspace_state;
PRAGMA legacy_alter_table = ON;
ALTER TABLE run_groups RENAME TO run_groups_v15;
PRAGMA legacy_alter_table = OFF;
CREATE TABLE run_groups (
    resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    launch_idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    state TEXT NOT NULL CHECK(state IN (
        'dispatch_pending', 'dispatching', 'dispatched', 'partially_dispatched',
        'queued', 'running', 'completed', 'failed', 'cancelled'
    )),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, launch_idempotency_key)
);
INSERT INTO run_groups(
    resource_id, workspace_id, launch_idempotency_key, request_sha256,
    state, generation, created_at, updated_at
)
SELECT resource_id, workspace_id, launch_idempotency_key, request_sha256,
       state, generation, created_at, updated_at
  FROM run_groups_v15;
DROP TABLE run_groups_v15;
CREATE INDEX ix_experiment_run_groups_workspace_state
    ON run_groups(workspace_id, state, created_at);

CREATE TABLE run_control_commands (
    command_id TEXT PRIMARY KEY NOT NULL,
    request_scope TEXT NOT NULL CHECK(length(request_scope) BETWEEN 1 AND 512),
    idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 255),
    command_type TEXT NOT NULL CHECK(command_type = 'cancel'),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    run_group_id TEXT NOT NULL REFERENCES run_groups(resource_id),
    expected_generation INTEGER NOT NULL CHECK(expected_generation >= 0),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(
        length(request_sha256) = 64
        AND request_sha256 = lower(request_sha256)
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    target_snapshot_json TEXT NOT NULL,
    target_snapshot_sha256 TEXT NOT NULL CHECK(
        length(target_snapshot_sha256) = 64
        AND target_snapshot_sha256 = lower(target_snapshot_sha256)
        AND target_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK(status IN ('pending','leased','applied','retryable','conflicted')),
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_retry_at TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    progress_sha256 TEXT NOT NULL CHECK(
        length(progress_sha256) = 64
        AND progress_sha256 = lower(progress_sha256)
        AND progress_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    acknowledgement_json TEXT,
    acknowledgement_sha256 TEXT CHECK(
        acknowledgement_sha256 IS NULL OR (
            length(acknowledgement_sha256) = 64
            AND acknowledgement_sha256 = lower(acknowledgement_sha256)
            AND acknowledgement_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    conflict_json TEXT,
    conflict_sha256 TEXT CHECK(
        conflict_sha256 IS NULL OR (
            length(conflict_sha256) = 64
            AND conflict_sha256 = lower(conflict_sha256)
            AND conflict_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    last_error_code TEXT CHECK(last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 128),
    last_error_message TEXT CHECK(last_error_message IS NULL OR length(last_error_message) <= 2000),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE(request_scope, idempotency_key),
    CHECK(json_valid(request_json) = 1 AND json(request_json) = request_json
          AND sha256(request_json) = request_sha256),
    CHECK(json_valid(target_snapshot_json) = 1 AND json(target_snapshot_json) = target_snapshot_json
          AND sha256(target_snapshot_json) = target_snapshot_sha256),
    CHECK(json_valid(progress_json) = 1 AND json(progress_json) = progress_json
          AND json_type(progress_json) IN ('object','array')
          AND sha256(progress_json) = progress_sha256),
    CHECK((status = 'leased') =
          (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK(
        (acknowledgement_json IS NULL AND acknowledgement_sha256 IS NULL)
        OR (acknowledgement_json IS NOT NULL AND acknowledgement_sha256 IS NOT NULL
            AND json_valid(acknowledgement_json) = 1
            AND json(acknowledgement_json) = acknowledgement_json
            AND sha256(acknowledgement_json) = acknowledgement_sha256)
    ),
    CHECK(
        (conflict_json IS NULL AND conflict_sha256 IS NULL)
        OR (conflict_json IS NOT NULL AND conflict_sha256 IS NOT NULL
            AND json_valid(conflict_json) = 1
            AND json(conflict_json) = conflict_json
            AND sha256(conflict_json) = conflict_sha256)
    ),
    CHECK(
        (status = 'applied' AND acknowledgement_json IS NOT NULL
         AND conflict_json IS NULL AND applied_at IS NOT NULL)
        OR (status = 'conflicted' AND acknowledgement_json IS NULL
            AND conflict_json IS NOT NULL AND applied_at IS NULL)
        OR (status IN ('pending','leased','retryable')
            AND acknowledgement_json IS NULL AND conflict_json IS NULL AND applied_at IS NULL)
    )
);
CREATE INDEX ix_experiment_run_control_commands_ready
    ON run_control_commands(status, next_retry_at, created_at, command_id);
CREATE INDEX ix_experiment_run_control_commands_group
    ON run_control_commands(run_group_id, created_at, command_id);
CREATE UNIQUE INDEX uq_run_control_active_command
    ON run_control_commands(run_group_id, command_type)
    WHERE status IN ('pending','leased','retryable','applied');
CREATE TRIGGER trg_experiment_run_control_command_scope_insert
BEFORE INSERT ON run_control_commands
WHEN NOT EXISTS (
    SELECT 1 FROM run_groups
     WHERE resource_id = NEW.run_group_id AND workspace_id = NEW.workspace_id
)
BEGIN SELECT RAISE(ABORT, 'run control command workspace mismatch'); END;
CREATE TRIGGER trg_experiment_run_control_command_identity_immutable
BEFORE UPDATE OF command_id, request_scope, idempotency_key, command_type,
 workspace_id, run_group_id, expected_generation, request_json, request_sha256,
 target_snapshot_json, target_snapshot_sha256, created_at
ON run_control_commands
BEGIN SELECT RAISE(ABORT, 'run control command identity is immutable'); END;
CREATE TRIGGER trg_experiment_run_control_command_state_transition
BEFORE UPDATE OF status ON run_control_commands
WHEN OLD.status <> NEW.status AND NOT (
    (OLD.status = 'pending' AND NEW.status IN ('leased','conflicted'))
    OR (OLD.status = 'leased' AND NEW.status IN ('applied','retryable','conflicted'))
    OR (OLD.status = 'retryable' AND NEW.status IN ('leased','conflicted'))
)
BEGIN SELECT RAISE(ABORT, 'invalid run control command state transition'); END;
CREATE TRIGGER trg_experiment_run_control_command_terminal_immutable
BEFORE UPDATE ON run_control_commands
WHEN OLD.status IN ('applied','conflicted')
BEGIN SELECT RAISE(ABORT, 'terminal run control command is immutable'); END;
CREATE TRIGGER trg_experiment_run_control_command_delete_forbidden
BEFORE DELETE ON run_control_commands
BEGIN SELECT RAISE(ABORT, 'run control commands are durable authority'); END;
PRAGMA foreign_key_check;
'''

MIGRATION_V16_CHECKSUM = hashlib.sha256(MIGRATION_V16_SQL.encode("utf-8")).hexdigest()

_MIGRATION_TRIGGER_NAMES = (
    "trg_experiment_resource_owner_same_workspace_insert",
    "trg_experiment_resource_owner_same_workspace_update",
    "trg_experiment_resource_identity_immutable",
    "trg_experiment_revision_digest_insert",
    "trg_experiment_revision_immutable_update",
    "trg_experiment_revision_immutable_delete",
    "trg_experiment_revision_edge_immutable_update",
    "trg_experiment_revision_edge_immutable_delete",
    "trg_experiment_lineage_same_workspace",
    "trg_experiment_lineage_owns_no_cycle",
    "trg_experiment_preparation_digest_insert",
    "trg_experiment_outbox_digest_insert",
    "trg_experiment_validation_receipt_digest_insert",
    "trg_experiment_audit_immutable_update",
    "trg_experiment_audit_immutable_delete",
    "trg_experiment_log_chunk_immutable_update",
    "trg_experiment_log_chunk_immutable_delete",
    "trg_experiment_artifact_identity_immutable_update",
    "trg_experiment_outbox_payload_immutable",
    "trg_experiment_validation_immutable_update",
    "trg_experiment_validation_immutable_delete",
    "trg_experiment_research_record_same_workspace_insert",
    "trg_experiment_research_record_replacement_same_subject",
    "trg_experiment_research_record_immutable_update",
    "trg_experiment_research_record_immutable_delete",
    "trg_experiment_domain_adapter_receipt_same_workspace_insert",
    "trg_experiment_domain_adapter_receipt_immutable_update",
    "trg_experiment_domain_adapter_receipt_immutable_delete",
    "trg_experiment_aggregate_parent_integrity_insert",
    "trg_experiment_aggregate_parent_immutable_update",
    "trg_experiment_external_entity_receipt_immutable_update",
    "trg_experiment_external_entity_receipt_immutable_delete",
    "trg_experiment_aggregate_head_revision_consistency_insert",
    "trg_experiment_aggregate_head_revision_consistency_update",
    "trg_experiment_run_attempt_terminal_receipt_insert",
    "trg_experiment_run_attempt_terminal_receipt_update",
    "trg_experiment_run_events_immutable_update",
    "trg_experiment_run_events_immutable_delete",
    "trg_experiment_launch_context_identity_immutable",
    "trg_experiment_launch_context_state_transition",
    "trg_experiment_launch_context_consumed_immutable",
    "trg_experiment_launch_context_delete_forbidden",
    "trg_experiment_preparation_immutable_update",
    "trg_experiment_preparation_immutable_delete",
    "trg_experiment_dataset_member_digest_insert",
    "trg_experiment_dataset_member_immutable_update",
    "trg_experiment_dataset_member_immutable_delete",
    "trg_experiment_dataset_kind_insert",
    "trg_experiment_dataset_kind_update",
    "trg_experiment_run_attempt_preparation_insert",
    "trg_experiment_run_attempt_preparation_immutable",
    "trg_experiment_launch_context_v2_insert",
    "trg_experiment_launch_context_v2_state",
    "trg_experiment_launch_context_v2_identity_immutable",
    "trg_experiment_connector_command_identity_immutable",
    "trg_experiment_connector_inbox_immutable_update",
    "trg_experiment_connector_inbox_state_transition",
    "trg_experiment_connector_inbox_immutable_delete",
    "trg_experiment_connector_stream_no_regression",
    "trg_experiment_connector_conflict_immutable_update",
    "trg_experiment_connector_conflict_immutable_delete",
    "trg_experiment_resource_admission_identity_immutable",
    "trg_experiment_resource_admission_transition",
    "trg_experiment_operational_receipt_immutable_update",
    "trg_experiment_operational_receipt_immutable_delete",
    "trg_experiment_workflow_plan_authority_scope_insert",
    "trg_experiment_workflow_plan_authority_digest_insert",
    "trg_experiment_workflow_plan_authority_immutable_update",
    "trg_experiment_workflow_plan_authority_immutable_delete",
    "trg_experiment_idempotency_response_digest_insert",
    "trg_experiment_idempotency_response_digest_update",
    "trg_experiment_idempotency_response_immutable_update",
    "trg_experiment_run_control_command_scope_insert",
    "trg_experiment_run_control_command_identity_immutable",
    "trg_experiment_run_control_command_state_transition",
    "trg_experiment_run_control_command_terminal_immutable",
    "trg_experiment_run_control_command_delete_forbidden",
)


def migration_checksum() -> str:
    return MIGRATION_V10_CHECKSUM


def _migration_v11_checksum() -> str:
    return hashlib.sha256(MIGRATION_V11_SQL.encode("utf-8")).hexdigest()


def _migration_v12_checksum() -> str:
    return hashlib.sha256(MIGRATION_V12_SQL.encode("utf-8")).hexdigest()


def _migration_v13_checksum() -> str:
    return hashlib.sha256(MIGRATION_V13_SQL.encode("utf-8")).hexdigest()


def _migration_v14_checksum() -> str:
    return hashlib.sha256(MIGRATION_V14_SQL.encode("utf-8")).hexdigest()


def _migration_v15_checksum() -> str:
    return hashlib.sha256(MIGRATION_V15_SQL.encode("utf-8")).hexdigest()


def _migration_v16_checksum() -> str:
    return MIGRATION_V16_CHECKSUM


def _migration_v9_checksum() -> str:
    return MIGRATION_V9_CHECKSUM


def _migration_v8_checksum() -> str:
    return MIGRATION_V8_CHECKSUM


def _migration_v7_checksum() -> str:
    return MIGRATION_V7_CHECKSUM


def _migration_v6_checksum() -> str:
    return MIGRATION_V6_CHECKSUM


def _migration_v5_checksum() -> str:
    return MIGRATION_V5_CHECKSUM


def _migration_v4_checksum() -> str:
    return MIGRATION_V4_CHECKSUM


def _migration_v3_checksum() -> str:
    return MIGRATION_V3_CHECKSUM


def _migration_v2_checksum() -> str:
    """Return the frozen checksum for the pre-hierarchy migration."""
    return MIGRATION_V2_CHECKSUM


def _verify_frozen_migration_sources() -> None:
    issued = (
        (2, MIGRATION_V2_SQL, MIGRATION_V2_CHECKSUM),
        (3, MIGRATION_V3_SQL, MIGRATION_V3_CHECKSUM),
        (4, MIGRATION_V4_SQL, MIGRATION_V4_CHECKSUM),
        (5, MIGRATION_V5_SQL, MIGRATION_V5_CHECKSUM),
        (6, MIGRATION_V6_SQL, MIGRATION_V6_CHECKSUM),
        (7, MIGRATION_V7_SQL, MIGRATION_V7_CHECKSUM),
        (8, MIGRATION_V8_SQL, MIGRATION_V8_CHECKSUM),
        (9, MIGRATION_V9_SQL, MIGRATION_V9_CHECKSUM),
        (10, MIGRATION_V10_SQL, MIGRATION_V10_CHECKSUM),
    )
    for version, sql, expected in issued:
        actual = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if actual != expected:
            raise RuntimeError(f"frozen migration v{version} checksum mismatch")


_REQUIRED_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "experiment_schema_migrations": {"version", "name", "checksum", "description", "applied_at"},
    "resources": {"id", "kind", "workspace_id", "lifecycle_owner_id", "created_at", "archived_at"},
    "aggregate_heads": {"aggregate_id", "aggregate_kind", "workspace_id", "parent_id", "current_revision_id", "head_generation", "lifecycle_state", "display_name", "description", "dataset_kind", "created_at", "updated_at"},
    "revisions": {"resource_id", "subject_id", "revision_number", "parent_revision_id", "schema_name", "schema_version", "canonical_payload", "payload_sha256", "dependency_graph_sha256", "provenance_json", "created_at"},
    "revision_edges": {"revision_id", "role", "ordinal", "target_resource_id", "expected_sha256", "metadata_json"},
    "workflow_drafts": {"resource_id", "workflow_id", "base_revision_id", "canonical_payload", "generation", "created_at", "updated_at"},
    "dataset_revision_members": {"revision_id", "ordinal", "role", "semantic_identity", "value_json", "content_sha256", "size_bytes", "media_type"},
    "workflow_preparations": {"resource_id", "workspace_id", "workflow_revision_id", "normalized_request_json", "normalized_request_sha256", "scheduler_payload_json", "validation_status", "validation_receipt_json", "validation_resource_id", "expected_cardinality", "created_at", "prepared_at"},
    "run_groups": {"resource_id", "workspace_id", "launch_idempotency_key", "request_sha256", "state", "generation", "created_at", "updated_at"},
    "run_control_commands": {"command_id", "request_scope", "idempotency_key", "command_type", "workspace_id", "run_group_id", "expected_generation", "request_json", "request_sha256", "target_snapshot_json", "target_snapshot_sha256", "status", "lease_owner", "lease_token", "lease_expires_at", "attempt_count", "next_retry_at", "progress_json", "progress_sha256", "acknowledgement_json", "acknowledgement_sha256", "conflict_json", "conflict_sha256", "last_error_code", "last_error_message", "created_at", "updated_at", "applied_at"},
    "run_group_preparations": {"run_group_id", "preparation_id", "ordinal"},
    "workflow_runs": {"resource_id", "workspace_id", "run_group_id", "preparation_id", "node_id", "requiredness", "state", "generation", "created_at"},
    "run_attempts": {"resource_id", "workspace_id", "workflow_run_id", "preparation_id", "attempt_number", "scheduler_job_id", "state", "external_binding_receipt_json", "runtime_identity_json", "terminal_receipt_json", "terminal_receipt_sha256", "created_at"},
    "dispatch_outbox": {"id", "workspace_id", "run_attempt_id", "event_type", "payload_json", "payload_sha256", "status", "dispatch_attempts", "lease_token", "lease_owner", "lease_acquired_at", "lease_expires_at", "last_error", "acknowledgement_json", "created_at", "updated_at"},
    "run_events": {"id", "workspace_id", "workflow_run_id", "sequence_number", "expected_generation", "resulting_generation", "idempotency_key", "event_type", "payload_json", "created_at"},
    "idempotency_claims": {"scope", "idempotency_key", "request_sha256", "result_resource_id", "response_json", "response_sha256", "created_at"},
    "external_entity_receipts": {"id", "workspace_id", "resource_id", "store_id", "entity_kind", "entity_id", "generation_or_revision", "content_digest", "contract_digest", "availability", "verification_authority", "acknowledgement_json", "created_at"},
    "lineage_edges": {"id", "workspace_id", "source_resource_id", "target_resource_id", "edge_mode", "edge_key", "metadata_json", "created_at"},
    "workflow_revision_nodes": {"revision_id", "ordinal", "node_id", "node_kind", "node_json"},
    "workflow_revision_edges": {"revision_id", "ordinal", "source_node_id", "target_node_id", "edge_json"},
    "artifact_blobs": {"sha256", "size_bytes", "media_type", "storage_key", "state", "verified_at", "created_at"},
    "artifacts": {"resource_id", "blob_sha256", "logical_role", "logical_key", "schema_name", "schema_version", "provenance_json", "created_at"},
    "validations": {"resource_id", "subject_resource_id", "validator_name", "validator_version", "outcome", "input_graph_sha256", "receipt_json", "receipt_sha256", "created_at"},
    "log_streams": {"resource_id", "attempt_id", "stream_name", "state", "created_at", "closed_at"},
    "log_chunks": {"stream_id", "sequence_number", "content_sha256", "artifact_blob_sha256", "content_text", "created_at"},
    "audit_events": {"id", "workspace_id", "resource_id", "event_type", "generation", "payload_json", "created_at"},
    "sync_state": {"state_key", "local_generation", "remote_generation", "pending_changes", "last_success_at", "last_error", "updated_at"},
    "research_records": {"resource_id", "workspace_id", "subject_resource_id", "record_kind", "body", "author", "source_receipt_ids_json", "supersedes_record_id", "created_at"},
    "domain_adapter_receipts": {"resource_id", "workspace_id", "domain_experiment_id", "adapter_id", "adapter_version", "operation_kind", "normalized_request_sha256", "receipt_json", "created_at"},
    "launch_contexts": {"launch_context_id", "project_id", "global_experiment_id", "domain_experiment_id", "workflow_id", "workflow_revision_id", "preparation_id", "run_attempt_id", "contract_version", "normalized_request_sha256", "validation_receipt_id", "validation_receipt_sha256", "source_receipt_id", "return_uri", "state", "claim_token", "canonical_job_id", "binding_receipt_json", "issued_at", "expires_at", "claimed_at", "consumed_at"},
    "domain_connector_commands": {"command_id", "request_scope", "idempotency_key", "idempotency_request_sha256", "operation", "project_id", "project_revision_id", "global_experiment_id", "global_experiment_revision_id", "domain_experiment_id", "domain_revision_id", "domain_revision_sha256", "prior_binding_revision_id", "global_receipt_id", "global_receipt_sha256", "command_json", "command_sha256", "status", "lease_owner", "lease_token", "lease_expires_at", "retry_count", "next_retry_at", "last_error", "acknowledgement_id", "acknowledgement_json", "acknowledgement_sha256", "binding_revision_id", "conflict_json", "conflict_sha256", "created_at", "updated_at"},
    "domain_connector_streams": {"domain_experiment_id", "binding_revision_id", "event_stream", "last_applied_stream_generation", "last_event_id", "last_payload_sha256", "updated_at"},
    "domain_connector_inbox": {"event_id", "source_store_id", "domain_experiment_id", "binding_revision_id", "state_revision_id", "event_type", "event_stream", "stream_generation", "source_generation", "payload_json", "payload_sha256", "envelope_json", "envelope_sha256", "disposition", "acknowledgement_json", "acknowledgement_sha256", "conflict_json", "conflict_sha256", "occurred_at", "received_at", "applied_at"},
    "domain_connector_conflicts": {"conflict_id", "domain_experiment_id", "binding_revision_id", "event_stream", "stream_generation", "event_id", "conflict_json", "conflict_sha256", "created_at"},
    "resource_admission_policy": {"policy_id", "policy_version", "cpu_thread_limit", "dram_byte_limit", "lock_generation", "updated_at"},
    "resource_admissions": {"admission_id", "workspace_id", "domain_experiment_id", "plan_id", "preparation_id", "run_attempt_id", "canonical_job_id", "state", "cpu_threads", "dram_bytes", "gpu_index", "gpu_uuid", "policy_source", "policy_version", "owner", "lease_token", "refusal_code", "refusal_reason", "release_reason", "recovery_evidence_json", "admitted_at", "queued_at", "released_at", "reconciled_at", "created_at", "updated_at"},
    "operational_receipts": {"receipt_id", "operation_kind", "workspace_id", "native_identity", "state", "receipt_json", "receipt_sha256", "source_revision", "occurred_at", "verified_at"},
    "workflow_plan_authority": {"workflow_id", "workspace_id", "domain_experiment_id", "expected_domain_revision_id", "capability_contract_json", "capability_contract_sha256", "created_at"},
}
_REQUIRED_INDEXES = {
    "ux_experiment_run_events_idempotency",
    "ix_experiment_research_records_subject_created",
    "ix_experiment_domain_adapter_receipts_domain_created",
    "ix_experiment_launch_contexts_state_expiry",
    "ix_experiment_launch_contexts_domain_issued",
    "ix_experiment_aggregate_heads_dataset_kind",
    "ix_experiment_run_attempts_preparation",
    "ux_experiment_launch_context_attempt",
    "ix_experiment_connector_commands_ready",
    "ix_experiment_connector_commands_domain",
    "ix_experiment_connector_inbox_deferred",
    "ix_experiment_connector_conflicts_stream",
    "ix_experiment_resource_admissions_state",
    "ix_experiment_resource_admissions_scope",
    "ix_experiment_operational_receipts_kind_time",
    "ix_experiment_workflow_plan_authority_scope",
    "ix_experiment_run_control_commands_ready",
    "ix_experiment_run_control_commands_group",
}


def _accepted_migration_ledgers() -> tuple[list[tuple[int, str, str]], ...]:
    v2 = (MIGRATION_V2_VERSION, MIGRATION_V2_NAME, _migration_v2_checksum())
    v3 = (MIGRATION_V3_VERSION, MIGRATION_V3_NAME, _migration_v3_checksum())
    v4 = (MIGRATION_V4_VERSION, MIGRATION_V4_NAME, _migration_v4_checksum())
    v5 = (MIGRATION_V5_VERSION, MIGRATION_V5_NAME, _migration_v5_checksum())
    v6 = (MIGRATION_V6_VERSION, MIGRATION_V6_NAME, _migration_v6_checksum())
    v7 = (MIGRATION_V7_VERSION, MIGRATION_V7_NAME, _migration_v7_checksum())
    v8 = (MIGRATION_V8_VERSION, MIGRATION_V8_NAME, _migration_v8_checksum())
    v9 = (MIGRATION_V9_VERSION, MIGRATION_V9_NAME, _migration_v9_checksum())
    v10 = (MIGRATION_VERSION, MIGRATION_NAME, migration_checksum())
    v11 = (MIGRATION_V11_VERSION, MIGRATION_V11_NAME, _migration_v11_checksum())
    v12 = (MIGRATION_V12_VERSION, MIGRATION_V12_NAME, _migration_v12_checksum())
    v13 = (MIGRATION_V13_VERSION, MIGRATION_V13_NAME, _migration_v13_checksum())
    v14 = (MIGRATION_V14_VERSION, MIGRATION_V14_NAME, _migration_v14_checksum())
    v15 = (MIGRATION_V15_VERSION, MIGRATION_V15_NAME, _migration_v15_checksum())
    v16 = (MIGRATION_V16_VERSION, MIGRATION_V16_NAME, _migration_v16_checksum())
    v1 = (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM)
    return (
        [v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16],
        [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16],
    )


def _normalize_schema_sql(sql: str) -> str:
    quoted_sql = re.compile(
        r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\])",
        re.DOTALL,
    )
    normalized_parts: list[str] = []
    for index, part in enumerate(quoted_sql.split(sql.strip())):
        if index % 2:
            normalized_parts.append(part)
            continue
        syntax = re.sub(r"\s+", " ", part).lower()
        normalized_parts.append(re.sub(r"\s*([(),])\s*", r"\1", syntax))
    return "".join(normalized_parts)


def _schema_definition_manifest(connection: sqlite3.Connection) -> dict[str, str]:
    table_names = tuple(sorted(_REQUIRED_SCHEMA_COLUMNS))
    placeholders = ",".join("?" for _ in table_names)
    rows = connection.execute(
        f"""
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND (
              (type = 'table' AND name IN ({placeholders}))
              OR (type = 'trigger' AND name LIKE 'trg_experiment_%')
              OR (type = 'index' AND (name LIKE 'ix_experiment_%' OR name LIKE 'ux_experiment_%'))
          )
        ORDER BY type, name
        """,
        table_names,
    ).fetchall()
    return {
        f"{row[0]}:{row[1]}:{row[2]}": hashlib.sha256(
            _normalize_schema_sql(str(row[3])).encode("utf-8")
        ).hexdigest()
        for row in rows
    }


def _schema_manifest_checksum(manifest: dict[str, str]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_LEGACY_FINAL_TABLE_SQL = {
    "experiment_schema_migrations": """CREATE TABLE experiment_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    )""",
    "revisions": """CREATE TABLE revisions (
        resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
        subject_id TEXT NOT NULL REFERENCES resources(id),
        revision_number INTEGER NOT NULL CHECK (revision_number > 0),
        parent_revision_id TEXT REFERENCES resources(id),
        schema_name TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        canonical_payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
        dependency_graph_sha256 TEXT NOT NULL CHECK (length(dependency_graph_sha256) = 64),
        created_at TEXT NOT NULL,
        "provenance_json" TEXT NOT NULL DEFAULT '{}',
        UNIQUE(subject_id, revision_number),
        UNIQUE(subject_id, payload_sha256, dependency_graph_sha256)
    )""",
    "workflow_preparations": """CREATE TABLE workflow_preparations (
        resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
        workspace_id TEXT NOT NULL REFERENCES resources(id),
        workflow_revision_id TEXT NOT NULL REFERENCES revisions(resource_id),
        normalized_request_json TEXT NOT NULL,
        normalized_request_sha256 TEXT NOT NULL CHECK (length(normalized_request_sha256) = 64),
        scheduler_payload_json TEXT NOT NULL DEFAULT '{}',
        validation_status TEXT NOT NULL CHECK (validation_status IN ('pending', 'valid', 'invalid')),
        validation_receipt_json TEXT NOT NULL,
        expected_cardinality INTEGER,
        created_at TEXT NOT NULL,
        prepared_at TEXT,
        "validation_resource_id" TEXT REFERENCES resources(id)
    )""",
    "run_attempts": """CREATE TABLE run_attempts (
        resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
        workspace_id TEXT NOT NULL REFERENCES resources(id),
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(resource_id),
        preparation_id TEXT NOT NULL REFERENCES workflow_preparations(resource_id),
        attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
        scheduler_job_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','dispatching','dispatched','running','completed','failed','cancelled')),
        external_binding_receipt_json TEXT,
        created_at TEXT NOT NULL,
        runtime_identity_json TEXT,
        terminal_receipt_json TEXT,
        terminal_receipt_sha256 TEXT CHECK(terminal_receipt_sha256 IS NULL OR length(terminal_receipt_sha256) = 64),
        UNIQUE(workflow_run_id, attempt_number),
        UNIQUE(scheduler_job_id)
    )""",
    "run_events": """CREATE TABLE run_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL REFERENCES resources(id),
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(resource_id),
        sequence_number INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        "expected_generation" INTEGER NOT NULL DEFAULT 0,
        "resulting_generation" INTEGER NOT NULL DEFAULT 0,
        "idempotency_key" TEXT NOT NULL DEFAULT '',
        UNIQUE(workflow_run_id, sequence_number)
    )""",
}


@lru_cache(maxsize=2)
def _expected_schema_definition_manifest(*, legacy_lineage: bool = False) -> dict[str, str]:
    expected = sqlite3.connect(":memory:")
    register_sqlite_sha256(expected)
    expected.execute("PRAGMA foreign_keys = ON")
    try:
        expected.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        expected.executescript(MIGRATION_SQL)
        _apply_hierarchy_upgrade(expected)
        _apply_receipt_immutability_upgrade(expected)
        _apply_lifecycle_consistency_upgrade(expected)
        _apply_domain_owned_aggregate_upgrade(expected)
        _apply_receipt_identity_upgrade(expected)
        _apply_launch_context_upgrade(expected)
        _apply_authority_immutability_upgrade(expected)
        _apply_scientific_lineage_upgrade(expected)
        _apply_additive_migration(
            expected, MIGRATION_V11_VERSION, MIGRATION_V11_NAME,
            _migration_v11_checksum(), "Attempt, launch-context v2, and Dataset kind authority",
            MIGRATION_V11_SQL,
        )
        _apply_additive_migration(
            expected, MIGRATION_V12_VERSION, MIGRATION_V12_NAME,
            _migration_v12_checksum(), "NGS/MolBio connector command and inbox authority",
            MIGRATION_V12_SQL,
        )
        _apply_additive_migration(
            expected, MIGRATION_V13_VERSION, MIGRATION_V13_NAME,
            _migration_v13_checksum(), "NGS/MolBio Dataset, admission, and operations authority",
            MIGRATION_V13_SQL,
        )
        _apply_additive_migration(
            expected, MIGRATION_V14_VERSION, MIGRATION_V14_NAME,
            _migration_v14_checksum(), "Immutable Workflow Plan and workflow preparation authority",
            MIGRATION_V14_SQL,
        )
        _apply_idempotency_response_digest_upgrade(expected)
        _apply_run_control_upgrade(expected)
        manifest = _schema_definition_manifest(expected)
        if legacy_lineage:
            for table_name, definition in _LEGACY_FINAL_TABLE_SQL.items():
                key = f"table:{table_name}:{table_name}"
                manifest[key] = hashlib.sha256(
                    _normalize_schema_sql(definition).encode("utf-8")
                ).hexdigest()
        return manifest
    finally:
        expected.close()


def attest_schema(connection: sqlite3.Connection) -> dict[str, object]:
    """Verify the exact ledger and complete migration-owned SQLite definitions."""
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    for table, columns in _REQUIRED_SCHEMA_COLUMNS.items():
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if table_exists is None:
            missing_tables.append(table)
            continue
        actual = _table_columns(connection, table)
        missing = sorted(columns - actual)
        if missing:
            missing_columns[table] = missing
    actual_indexes = {
        row[1]
        for table in _REQUIRED_SCHEMA_COLUMNS
        for row in connection.execute(f'PRAGMA index_list("{table}")')
    }
    missing_indexes = sorted(_REQUIRED_INDEXES - actual_indexes)
    actual_triggers = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    missing_triggers = sorted(set(_MIGRATION_TRIGGER_NAMES) - actual_triggers)
    foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    ledger = [
        (int(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        )
    ]
    ledger_valid = ledger in _accepted_migration_ledgers()
    legacy_lineage = bool(ledger and ledger[0][0] == LEGACY_MIGRATION_VERSION)
    expected_definitions = _expected_schema_definition_manifest(legacy_lineage=legacy_lineage)
    actual_definitions = _schema_definition_manifest(connection)
    actual_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    unexpected_tables = sorted(actual_tables - set(_REQUIRED_SCHEMA_COLUMNS))
    missing_definitions = sorted(set(expected_definitions) - set(actual_definitions))
    unexpected_definitions = sorted(set(actual_definitions) - set(expected_definitions))
    mismatched_definitions = sorted(
        name
        for name in set(expected_definitions) & set(actual_definitions)
        if expected_definitions[name] != actual_definitions[name]
    )
    definition_errors = [
        *(f"unexpected table: {name}" for name in unexpected_tables),
        *(f"missing definition: {name}" for name in missing_definitions),
        *(f"unexpected definition: {name}" for name in unexpected_definitions),
        *(f"definition digest mismatch: {name}" for name in mismatched_definitions),
    ]
    # V1-V10 constants remain frozen. V11-V16 are additive successors whose
    # exact expected manifest is derived from their immutable SQL bodies.
    frozen_manifest_checksum = _schema_manifest_checksum(expected_definitions)
    if _schema_manifest_checksum(actual_definitions) != frozen_manifest_checksum:
        definition_errors.append("frozen schema definition manifest checksum mismatch")
    ok = not (
        missing_tables
        or missing_columns
        or missing_indexes
        or missing_triggers
        or foreign_key_errors
        or definition_errors
        or not ledger_valid
    )
    return {
        "ok": ok,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "missing_triggers": missing_triggers,
        "foreign_key_errors": foreign_key_errors,
        "migration_ledger": [list(row) for row in ledger],
        "migration_ledger_valid": ledger_valid,
        "definition_errors": definition_errors,
        "expected_definition_manifest_sha256": hashlib.sha256(
            "\n".join(f"{name}:{digest}" for name, digest in sorted(expected_definitions.items())).encode("utf-8")
        ).hexdigest(),
        "actual_definition_manifest_sha256": hashlib.sha256(
            "\n".join(f"{name}:{digest}" for name, digest in sorted(actual_definitions.items())).encode("utf-8")
        ).hexdigest(),
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_xinfo("{table}")')}


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    register_sqlite_sha256(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _apply_legacy_upgrade(connection: sqlite3.Connection) -> None:
    """Upgrade the originally shipped v1 schema without discarding rows."""
    legacy_receipts: list[tuple[object, ...]] = []
    legacy_receipts_table_renamed = False
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_entity_receipts'"
    ).fetchone():
        legacy_receipts = connection.execute(
            """
            SELECT id, workspace_id, resource_id, store_id, entity_kind, entity_id,
                   generation_or_revision, content_digest, availability,
                   acknowledgement_json, created_at
            FROM external_entity_receipts
            """
        ).fetchall()
        connection.execute("ALTER TABLE external_entity_receipts RENAME TO external_entity_receipts_v1")
        legacy_receipts_table_renamed = True

    for trigger_name in _MIGRATION_TRIGGER_NAMES:
        connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
    connection.executescript(MIGRATION_SQL)

    existing_ledger_columns = _table_columns(connection, "experiment_schema_migrations")
    if "description" not in existing_ledger_columns:
        connection.execute(
            "ALTER TABLE experiment_schema_migrations ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    existing_columns = _table_columns
    for table, column, definition in (
        ("revisions", "provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("workflow_preparations", "validation_resource_id", "TEXT REFERENCES resources(id)"),
        ("run_attempts", "runtime_identity_json", "TEXT"),
        ("run_attempts", "terminal_receipt_json", "TEXT"),
        ("run_events", "expected_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("run_events", "resulting_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("run_events", "idempotency_key", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_columns(connection, table):
            connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    connection.execute(
        "UPDATE run_events SET idempotency_key = 'legacy:' || id WHERE idempotency_key = ''"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_run_events_idempotency "
        "ON run_events(workflow_run_id, idempotency_key)"
    )

    for receipt in legacy_receipts:
        (
            receipt_id,
            workspace_id,
            resource_id,
            store_id,
            entity_kind,
            entity_id,
            generation_or_revision,
            content_digest,
            availability,
            acknowledgement_json,
            created_at,
        ) = receipt
        resource = connection.execute(
            "SELECT kind, workspace_id, lifecycle_owner_id FROM resources WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if resource is None:
            connection.execute(
                """
                INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at)
                VALUES (?, 'external_entity_receipt', ?, ?, ?)
                """,
                (receipt_id, workspace_id, workspace_id, created_at),
            )
        elif resource[0] != "external_entity_receipt":
            raise RuntimeError(
                f"cannot migrate external receipt {receipt_id!r}: resource identity is already owned"
            )
        connection.execute(
            """
            INSERT INTO external_entity_receipts(
                id, workspace_id, resource_id, store_id, entity_kind, entity_id,
                generation_or_revision, content_digest, availability,
                verification_authority, acknowledgement_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*receipt[:8], "unavailable", "legacy_unverified", receipt[9], receipt[10]),
        )
    if legacy_receipts_table_renamed:
        connection.execute("DROP TABLE external_entity_receipts_v1")


def _cleanup_legacy_receipt_table(connection: sqlite3.Connection) -> None:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_entity_receipts_v1'"
    ).fetchone():
        return
    count = connection.execute("SELECT count(*) FROM external_entity_receipts_v1").fetchone()[0]
    if count:
        raise RuntimeError(
            "external_entity_receipts_v1 contains rows and requires the v1-to-v2 receipt migration"
        )
    connection.execute("DROP TABLE external_entity_receipts_v1")


def _upgrade_v2_receipt_authority(connection: sqlite3.Connection) -> None:
    """Rebuild the genuine v2 receipt table with fail-closed server authority."""
    if "verification_authority" in _table_columns(connection, "external_entity_receipts"):
        return
    script = r'''
BEGIN IMMEDIATE;
ALTER TABLE external_entity_receipts RENAME TO external_entity_receipts_v2;
CREATE TABLE IF NOT EXISTS external_entity_receipts (
    id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id),
    workspace_id TEXT NOT NULL REFERENCES resources(id),
    resource_id TEXT NOT NULL REFERENCES resources(id),
    store_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    generation_or_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    availability TEXT NOT NULL CHECK (availability IN ('unknown', 'available', 'unavailable')),
    verification_authority TEXT NOT NULL DEFAULT 'legacy_unverified' CHECK (length(verification_authority) > 0),
    acknowledgement_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, entity_kind, entity_id, generation_or_revision, content_digest)
);
INSERT INTO external_entity_receipts(
    id, workspace_id, resource_id, store_id, entity_kind, entity_id,
    generation_or_revision, content_digest, availability,
    verification_authority, acknowledgement_json, created_at
)
SELECT
    id, workspace_id, resource_id, store_id, entity_kind, entity_id,
    generation_or_revision, content_digest, availability,
    'legacy_unverified', acknowledgement_json, created_at
FROM external_entity_receipts_v2;
DROP TABLE external_entity_receipts_v2;
COMMIT;
'''
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_hierarchy_upgrade(connection: sqlite3.Connection) -> None:
    """Apply hierarchy tables and the v3 ledger row in one SQLite transaction."""
    checksum = _migration_v3_checksum()
    description = "Global Project hierarchy and append-only research records"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V3_SQL
        + "\nINSERT OR IGNORE INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V3_VERSION}, '{MIGRATION_V3_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_receipt_immutability_upgrade(connection: sqlite3.Connection) -> None:
    """Install exact append-only receipt guards and ledger them atomically."""
    checksum = _migration_v4_checksum()
    description = "Immutable external entity receipts at the SQLite boundary"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V4_SQL
        + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V4_VERSION}, '{MIGRATION_V4_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_lifecycle_consistency_upgrade(connection: sqlite3.Connection) -> None:
    """Install aggregate-head/current-revision lifecycle consistency guards atomically."""
    checksum = _migration_v5_checksum()
    description = "Aggregate head lifecycle matches canonical current revision status"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V5_SQL
        + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V5_VERSION}, '{MIGRATION_V5_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_domain_owned_aggregate_upgrade(connection: sqlite3.Connection) -> None:
    """Allow workflow and dataset aggregates to belong to Domain Experiments."""
    checksum = _migration_v6_checksum()
    description = "Domain-owned workflow and dataset aggregate parents"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V6_SQL
        + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V6_VERSION}, '{MIGRATION_V6_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _migration_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _migrate_classifiable_domain_ownership(connection: sqlite3.Connection) -> None:
    """Bind registered CM workflows; leave every unclassifiable legacy row explicit."""
    cm_adapter_ids = (
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
        "bms.cm.frustrampnn.adapter.v1",
        "bms.cm.comparison.adapter.v1",
    )
    placeholders = ",".join("?" for _ in cm_adapter_ids)
    rows = connection.execute(
        f"""
        SELECT head.aggregate_id, head.workspace_id, head.parent_id
        FROM aggregate_heads AS head
        JOIN resources AS parent ON parent.id = head.parent_id
        WHERE head.aggregate_kind = 'workflow'
          AND parent.kind = 'experiment'
          AND (
              lower(head.description) IN ({placeholders})
              OR EXISTS (
                  SELECT 1
                  FROM workflow_revision_nodes AS node
                  WHERE node.revision_id = head.current_revision_id
                    AND json_extract(node.node_json, '$.adapter_id') IN ({placeholders})
              )
          )
        ORDER BY head.parent_id, head.aggregate_id
        """,
        (*cm_adapter_ids, *cm_adapter_ids),
    ).fetchall()
    now_value = datetime.now(timezone.utc).isoformat()
    domains: dict[str, str] = {}
    for workflow_id, project_id, global_experiment_id in rows:
        domain_id = domains.get(global_experiment_id)
        if domain_id is None:
            domain_id = _migration_id("domain-cm", project_id, global_experiment_id)
            revision_id = _migration_id("revision-cm", domain_id)
            payload = {
                "schema": "bms.domain-experiment.v1",
                "domain_kind": "protein_in_silico",
                "domain_contract_version": "1",
                "name": "Migrated Conformational Mapping",
                "objective": "Preserve deterministic ownership for an existing Conformational Mapping workflow",
                "status": "draft",
                "tags": ["migration", "conformational_mapping"],
                "source_receipt_ids": [],
                "dataset_ids": [],
                "domain_payload": {
                    "schema": "bms.protein-in-silico-experiment.v1",
                    "experiment_mode": "analysis",
                    "targets": [],
                    "scientific_objective": "Preserve the existing Conformational Mapping workflow",
                    "design_constraints": {},
                    "planned_capabilities": ["conformational_mapping"],
                    "comparison_groups": [],
                    "validation_strategy": [],
                },
                "created_by": "migration:v7",
                "change_summary": "Deterministically bound existing CM workflow ownership",
            }
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            empty_graph = json.dumps(
                {"edges": [], "nodes": [], "references": []},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            connection.execute(
                "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'domain_experiment', ?, ?, ?)",
                (domain_id, project_id, global_experiment_id, now_value),
            )
            connection.execute(
                "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'revision', ?, ?, ?)",
                (revision_id, project_id, domain_id, now_value),
            )
            connection.execute(
                """
                INSERT INTO revisions(
                    resource_id, subject_id, revision_number, parent_revision_id,
                    schema_name, schema_version, canonical_payload, payload_sha256,
                    dependency_graph_sha256, provenance_json, created_at
                ) VALUES (?, ?, 1, NULL, 'bms.domain-experiment.v1', '1', ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    domain_id,
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    hashlib.sha256(empty_graph.encode("utf-8")).hexdigest(),
                    json.dumps({"migration": "v7", "authority": "registered_cm_adapter"}, sort_keys=True, separators=(",", ":")),
                    now_value,
                ),
            )
            connection.execute(
                """
                INSERT INTO aggregate_heads(
                    aggregate_id, aggregate_kind, workspace_id, parent_id,
                    current_revision_id, head_generation, lifecycle_state,
                    display_name, description, created_at, updated_at
                ) VALUES (?, 'domain_experiment', ?, ?, ?, 1, 'draft', ?, ?, ?, ?)
                """,
                (
                    domain_id,
                    project_id,
                    global_experiment_id,
                    revision_id,
                    payload["name"],
                    payload["objective"],
                    now_value,
                    now_value,
                ),
            )
            connection.execute(
                """
                INSERT INTO lineage_edges(
                    id, workspace_id, source_resource_id, target_resource_id,
                    edge_mode, edge_key, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'owns', 'migration:v7:cm-domain', ?, ?)
                """,
                (
                    _migration_id("lineage-cm-domain", global_experiment_id),
                    project_id,
                    global_experiment_id,
                    domain_id,
                    json.dumps({"migration": "v7", "classification": "registered_cm_adapter"}, sort_keys=True, separators=(",", ":")),
                    now_value,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events(id, workspace_id, resource_id, event_type, generation, payload_json, created_at)
                VALUES (?, ?, ?, 'legacy_cm_domain_created', 1, ?, ?)
                """,
                (
                    _migration_id("audit-cm-domain", domain_id),
                    project_id,
                    domain_id,
                    json.dumps({"global_experiment_id": global_experiment_id}, sort_keys=True, separators=(",", ":")),
                    now_value,
                ),
            )
            domains[global_experiment_id] = domain_id

        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_resource_identity_immutable")
        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_aggregate_parent_immutable_update")
        connection.execute(
            "UPDATE resources SET lifecycle_owner_id = ? WHERE id = ? AND kind = 'workflow'",
            (domain_id, workflow_id),
        )
        connection.execute(
            "UPDATE aggregate_heads SET parent_id = ? WHERE aggregate_id = ? AND aggregate_kind = 'workflow'",
            (domain_id, workflow_id),
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_experiment_resource_identity_immutable
            BEFORE UPDATE OF id, kind, workspace_id, lifecycle_owner_id ON resources
            BEGIN
                SELECT RAISE(ABORT, 'resource identity is immutable');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_experiment_aggregate_parent_immutable_update
            BEFORE UPDATE OF aggregate_id, aggregate_kind, workspace_id, parent_id ON aggregate_heads
            WHEN NEW.aggregate_id IS NOT OLD.aggregate_id
              OR NEW.aggregate_kind IS NOT OLD.aggregate_kind
              OR NEW.workspace_id IS NOT OLD.workspace_id
              OR NEW.parent_id IS NOT OLD.parent_id
            BEGIN
                SELECT RAISE(ABORT, 'aggregate identity and parent are immutable');
            END
            """
        )
        connection.execute(
            """
            INSERT INTO lineage_edges(
                id, workspace_id, source_resource_id, target_resource_id,
                edge_mode, edge_key, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'owns', 'migration:v7:cm-workflow', ?, ?)
            """,
            (
                _migration_id("lineage-cm-workflow", workflow_id),
                project_id,
                domain_id,
                workflow_id,
                json.dumps({"migration": "v7", "previous_parent_id": global_experiment_id}, sort_keys=True, separators=(",", ":")),
                now_value,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events(id, workspace_id, resource_id, event_type, generation, payload_json, created_at)
            VALUES (?, ?, ?, 'legacy_cm_workflow_bound', 0, ?, ?)
            """,
            (
                _migration_id("audit-cm-workflow", workflow_id),
                project_id,
                workflow_id,
                json.dumps({"domain_experiment_id": domain_id, "previous_parent_id": global_experiment_id}, sort_keys=True, separators=(",", ":")),
                now_value,
            ),
        )

    connection.execute(
        """
        UPDATE aggregate_heads
        SET lifecycle_state = 'needs_domain_assignment'
        WHERE aggregate_kind IN ('workflow', 'dataset')
          AND (SELECT kind FROM resources WHERE id = aggregate_heads.parent_id) IS NOT 'domain_experiment'
        """
    )


def _apply_receipt_identity_upgrade(connection: sqlite3.Connection) -> None:
    """Scope immutable receipt identity and bind classifiable legacy workflows."""
    checksum = _migration_v7_checksum()
    description = "Project-scoped immutable external receipt identity and deterministic legacy domain assignment"
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATION_V7_SQL)
        _migrate_classifiable_domain_ownership(connection)
        connection.execute(
            """
            INSERT INTO experiment_schema_migrations(
                version, name, checksum, description, applied_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                MIGRATION_V7_VERSION,
                MIGRATION_V7_NAME,
                checksum,
                description,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_launch_context_upgrade(connection: sqlite3.Connection) -> None:
    """Install durable opaque launcher handoff receipts atomically."""
    checksum = _migration_v8_checksum()
    description = "Durable opaque launch contexts with claim, consume, and canonical Job bindings"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V8_SQL
        + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V8_VERSION}, '{MIGRATION_V8_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_authority_immutability_upgrade(connection: sqlite3.Connection) -> None:
    """Make accepted preparations and dataset membership append-only."""
    checksum = _migration_v9_checksum()
    description = "Immutable workflow preparations and digest-bound dataset revision members"
    script = (
        "BEGIN IMMEDIATE;\n" + MIGRATION_V9_SQL
        + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_V9_VERSION}, '{MIGRATION_V9_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_scientific_lineage_upgrade(connection: sqlite3.Connection) -> None:
    """Add exact scientific lineage vocabulary without rewriting V1-V9."""
    checksum = migration_checksum()
    description = "Exact derived_from and compared_with scientific lineage modes"
    script = ("BEGIN IMMEDIATE;\n" + MIGRATION_V10_SQL + "\nINSERT INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_VERSION}, '{MIGRATION_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\nCOMMIT;\n")
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_additive_migration(
    connection: sqlite3.Connection,
    version: int,
    name: str,
    checksum: str,
    description: str,
    sql: str,
) -> None:
    script = (
        "BEGIN IMMEDIATE;\n" + sql
        + "\nINSERT INTO experiment_schema_migrations(version, name, checksum, description, applied_at) VALUES ("
        + f"{version}, '{name}', '{checksum}', '{description}', "
        + f"'{datetime.now(timezone.utc).isoformat()}');\nCOMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_idempotency_response_digest_upgrade(
    connection: sqlite3.Connection,
) -> None:
    """Backfill exact response-byte digests before sealing V15."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise sqlite3.IntegrityError(
                    "V15 found duplicate JSON keys in an idempotency response"
                )
            value[key] = item
        return value

    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATION_V15_SQL)
        rows = connection.execute(
            "SELECT scope, idempotency_key, response_json "
            "FROM idempotency_claims ORDER BY scope, idempotency_key"
        ).fetchall()
        for scope, key, response_json in rows:
            parsed = json.loads(
                str(response_json), object_pairs_hook=unique_object
            )
            if type(parsed) is not dict:
                raise sqlite3.IntegrityError(
                    "V15 found a non-object idempotency response"
                )
            canonical = json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            response_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE idempotency_claims SET response_json=?, response_sha256=? "
                "WHERE scope=? AND idempotency_key=? AND response_sha256 IS NULL",
                (canonical, response_sha256, scope, key),
            )
        connection.execute(
            "INSERT INTO experiment_schema_migrations("
            "version,name,checksum,description,applied_at) VALUES (?,?,?,?,?)",
            (
                MIGRATION_V15_VERSION,
                MIGRATION_V15_NAME,
                _migration_v15_checksum(),
                "Exact canonical idempotency response digest authority",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_run_control_upgrade(connection: sqlite3.Connection) -> None:
    """Rebuild run-group authority and install durable cancellation commands."""
    foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    legacy_alter_table = int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0])
    checksum = _migration_v16_checksum()
    description = "Durable cancellation command and complete run-group state authority"
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
            raise RuntimeError("V16 requires foreign_keys disabled before the run_groups rebuild")
        if int(connection.execute("PRAGMA legacy_alter_table").fetchone()[0]) != 1:
            raise RuntimeError("V16 requires legacy_alter_table fencing for the run_groups rename")
        connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATION_V16_SQL)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"V16 foreign-key violations: {violations!r}")
        connection.execute(
            """
            INSERT INTO experiment_schema_migrations(
                version, name, checksum, description, applied_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                MIGRATION_V16_VERSION,
                MIGRATION_V16_NAME,
                checksum,
                description,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA legacy_alter_table = {legacy_alter_table}")
        connection.execute(f"PRAGMA foreign_keys = {foreign_keys}")


def run_all(db_path: str | Path) -> None:
    _verify_frozen_migration_sources()
    path = Path(db_path).expanduser().resolve()
    connection = _connect(path)
    v1 = (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM)
    v2 = (MIGRATION_V2_VERSION, MIGRATION_V2_NAME, _migration_v2_checksum())
    v3 = (MIGRATION_V3_VERSION, MIGRATION_V3_NAME, _migration_v3_checksum())
    v4 = (MIGRATION_V4_VERSION, MIGRATION_V4_NAME, _migration_v4_checksum())
    v5 = (MIGRATION_V5_VERSION, MIGRATION_V5_NAME, _migration_v5_checksum())
    v6 = (MIGRATION_V6_VERSION, MIGRATION_V6_NAME, _migration_v6_checksum())
    v7 = (MIGRATION_V7_VERSION, MIGRATION_V7_NAME, _migration_v7_checksum())
    v8 = (MIGRATION_V8_VERSION, MIGRATION_V8_NAME, _migration_v8_checksum())
    v9 = (MIGRATION_V9_VERSION, MIGRATION_V9_NAME, _migration_v9_checksum())
    v10 = (MIGRATION_VERSION, MIGRATION_NAME, migration_checksum())
    v11 = (MIGRATION_V11_VERSION, MIGRATION_V11_NAME, _migration_v11_checksum())
    v12 = (MIGRATION_V12_VERSION, MIGRATION_V12_NAME, _migration_v12_checksum())
    v13 = (MIGRATION_V13_VERSION, MIGRATION_V13_NAME, _migration_v13_checksum())
    v14 = (MIGRATION_V14_VERSION, MIGRATION_V14_NAME, _migration_v14_checksum())
    v15 = (MIGRATION_V15_VERSION, MIGRATION_V15_NAME, _migration_v15_checksum())
    v16 = (MIGRATION_V16_VERSION, MIGRATION_V16_NAME, _migration_v16_checksum())
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if not rows:
            connection.executescript(MIGRATION_SQL)
            connection.execute(
                """
                INSERT INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    *v2,
                    "Global workspace/experiment receipts and projections",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        elif rows == [v1]:
            _apply_legacy_upgrade(connection)
            connection.execute(
                "UPDATE experiment_schema_migrations SET description = ? WHERE version = ?",
                ("Global workspace/experiment metadata foundation (legacy v1)", LEGACY_MIGRATION_VERSION),
            )
            connection.execute(
                """
                INSERT INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    *v2,
                    "Global workspace/experiment receipts and projections",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2], [v1, v2]):
            _upgrade_v2_receipt_authority(connection)
            _apply_hierarchy_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3], [v1, v2, v3]):
            _cleanup_legacy_receipt_table(connection)
            connection.commit()
            _apply_receipt_immutability_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4], [v1, v2, v3, v4]):
            _apply_lifecycle_consistency_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5], [v1, v2, v3, v4, v5]):
            _apply_domain_owned_aggregate_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6], [v1, v2, v3, v4, v5, v6]):
            _apply_receipt_identity_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7], [v1, v2, v3, v4, v5, v6, v7]):
            _apply_launch_context_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8], [v1, v2, v3, v4, v5, v6, v7, v8]):
            _apply_authority_immutability_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9], [v1, v2, v3, v4, v5, v6, v7, v8, v9]):
            _apply_scientific_lineage_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9, v10], [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10]):
            _apply_additive_migration(
                connection, MIGRATION_V11_VERSION, MIGRATION_V11_NAME,
                _migration_v11_checksum(), "Attempt, launch-context v2, and Dataset kind authority",
                MIGRATION_V11_SQL,
            )

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9, v10, v11], [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11]):
            _apply_additive_migration(
                connection, MIGRATION_V12_VERSION, MIGRATION_V12_NAME,
                _migration_v12_checksum(), "NGS/MolBio connector command and inbox authority",
                MIGRATION_V12_SQL,
            )

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12], [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12]):
            _apply_additive_migration(
                connection, MIGRATION_V13_VERSION, MIGRATION_V13_NAME,
                _migration_v13_checksum(), "NGS/MolBio Dataset, admission, and operations authority",
                MIGRATION_V13_SQL,
            )

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13], [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13]):
            _apply_additive_migration(
                connection, MIGRATION_V14_VERSION, MIGRATION_V14_NAME,
                _migration_v14_checksum(), "Immutable Workflow Plan and workflow preparation authority",
                MIGRATION_V14_SQL,
            )

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in ([v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14], [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14]):
            _apply_idempotency_response_digest_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows in (
            [v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15],
            [v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15],
        ):
            _apply_run_control_upgrade(connection)

        rows = connection.execute(
            "SELECT version, name, checksum FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        if rows not in _accepted_migration_ledgers():
            raise RuntimeError(f"experiment migration ledger mismatch: {rows!r}")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_run_events_idempotency "
            "ON run_events(workflow_run_id, idempotency_key)"
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"experiment foreign-key violations: {violations!r}")
        attestation = attest_schema(connection)
        if not attestation["ok"]:
            raise sqlite3.IntegrityError(f"experiment schema attestation failed: {attestation!r}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def health(db_path: str | Path) -> dict[str, object]:
    path = Path(db_path).expanduser().resolve()
    connection = _connect(path)
    try:
        migration = connection.execute(
            "SELECT version, name, checksum, description, applied_at FROM experiment_schema_migrations "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return {
            "path": str(path),
            "exists": path.exists(),
            "latest_migration_version": LATEST_MIGRATION_VERSION,
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
            "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
            "attestation": attest_schema(connection),
            "migration": (
                {
                    "version": migration[0],
                    "name": migration[1],
                    "checksum": migration[2],
                    "description": migration[3],
                    "applied_at": migration[4],
                }
                if migration
                else None
            ),
        }
    finally:
        connection.close()


__all__ = [
    "MIGRATION_VERSION",
    "MIGRATION_NAME",
    "MIGRATION_V8_VERSION",
    "MIGRATION_V8_NAME",
    "MIGRATION_V9_VERSION",
    "MIGRATION_V9_NAME",
    "MIGRATION_V11_VERSION",
    "MIGRATION_V11_NAME",
    "MIGRATION_V12_VERSION",
    "MIGRATION_V12_NAME",
    "MIGRATION_V13_VERSION",
    "MIGRATION_V13_NAME",
    "MIGRATION_V14_VERSION",
    "MIGRATION_V14_NAME",
    "MIGRATION_V15_VERSION",
    "MIGRATION_V15_NAME",
    "MIGRATION_V16_VERSION",
    "MIGRATION_V16_NAME",
    "MIGRATION_V16_CHECKSUM",
    "LATEST_MIGRATION_VERSION",
    "MIGRATION_V4_VERSION",
    "MIGRATION_V4_NAME",
    "MIGRATION_SQL",
    "MIGRATION_V2_SQL",
    "MIGRATION_V3_SQL",
    "MIGRATION_V4_SQL",
    "MIGRATION_V5_SQL",
    "MIGRATION_V6_SQL",
    "MIGRATION_V7_SQL",
    "MIGRATION_V8_SQL",
    "MIGRATION_V9_SQL",
    "MIGRATION_V10_SQL",
    "MIGRATION_V11_SQL",
    "MIGRATION_V12_SQL",
    "MIGRATION_V13_SQL",
    "MIGRATION_V14_SQL",
    "MIGRATION_V15_SQL",
    "MIGRATION_V16_SQL",
    "migration_checksum",
    "attest_schema",
    "run_all",
    "health",
]

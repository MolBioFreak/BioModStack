"""Versioned migrations for the global experiment/workspace SQLite store."""
from __future__ import annotations

import hashlib
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
MIGRATION_VERSION = 3
MIGRATION_NAME = "global_project_hierarchy_and_research_records"

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
    runtime_identity_json TEXT,
    terminal_receipt_json TEXT,
    created_at TEXT NOT NULL,
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
)


def migration_checksum() -> str:
    return hashlib.sha256(MIGRATION_V3_SQL.encode("utf-8")).hexdigest()


def _migration_v2_checksum() -> str:
    """Return the frozen checksum for the pre-hierarchy migration."""
    return hashlib.sha256(MIGRATION_V2_SQL.encode("utf-8")).hexdigest()


_REQUIRED_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "experiment_schema_migrations": {"version", "name", "checksum", "description", "applied_at"},
    "resources": {"id", "kind", "workspace_id", "lifecycle_owner_id", "created_at", "archived_at"},
    "aggregate_heads": {"aggregate_id", "aggregate_kind", "workspace_id", "parent_id", "current_revision_id", "head_generation", "lifecycle_state", "display_name", "description", "created_at", "updated_at"},
    "revisions": {"resource_id", "subject_id", "revision_number", "parent_revision_id", "schema_name", "schema_version", "canonical_payload", "payload_sha256", "dependency_graph_sha256", "provenance_json", "created_at"},
    "revision_edges": {"revision_id", "role", "ordinal", "target_resource_id", "expected_sha256", "metadata_json"},
    "workflow_drafts": {"resource_id", "workflow_id", "base_revision_id", "canonical_payload", "generation", "created_at", "updated_at"},
    "dataset_revision_members": {"revision_id", "ordinal", "role", "semantic_identity", "value_json", "content_sha256", "size_bytes", "media_type"},
    "workflow_preparations": {"resource_id", "workspace_id", "workflow_revision_id", "normalized_request_json", "normalized_request_sha256", "scheduler_payload_json", "validation_status", "validation_receipt_json", "validation_resource_id", "expected_cardinality", "created_at", "prepared_at"},
    "run_groups": {"resource_id", "workspace_id", "launch_idempotency_key", "request_sha256", "state", "generation", "created_at", "updated_at"},
    "run_group_preparations": {"run_group_id", "preparation_id", "ordinal"},
    "workflow_runs": {"resource_id", "workspace_id", "run_group_id", "preparation_id", "node_id", "requiredness", "state", "generation", "created_at"},
    "run_attempts": {"resource_id", "workspace_id", "workflow_run_id", "attempt_number", "scheduler_job_id", "state", "external_binding_receipt_json", "runtime_identity_json", "terminal_receipt_json", "created_at"},
    "dispatch_outbox": {"id", "workspace_id", "run_attempt_id", "event_type", "payload_json", "payload_sha256", "status", "dispatch_attempts", "lease_token", "last_error", "acknowledgement_json", "created_at", "updated_at"},
    "run_events": {"id", "workspace_id", "workflow_run_id", "sequence_number", "expected_generation", "resulting_generation", "idempotency_key", "event_type", "payload_json", "created_at"},
    "idempotency_claims": {"scope", "idempotency_key", "request_sha256", "result_resource_id", "response_json", "created_at"},
    "external_entity_receipts": {"id", "workspace_id", "resource_id", "store_id", "entity_kind", "entity_id", "generation_or_revision", "content_digest", "availability", "verification_authority", "acknowledgement_json", "created_at"},
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
}
_REQUIRED_INDEXES = {
    "ux_experiment_run_events_idempotency",
    "ix_experiment_research_records_subject_created",
    "ix_experiment_domain_adapter_receipts_domain_created",
}


def _accepted_migration_ledgers() -> tuple[list[tuple[int, str, str]], ...]:
    v2 = (MIGRATION_V2_VERSION, MIGRATION_V2_NAME, _migration_v2_checksum())
    v3 = (MIGRATION_VERSION, MIGRATION_NAME, migration_checksum())
    v1 = (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM)
    return ([v2, v3], [v1, v2, v3])


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
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        scheduler_job_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'dispatching', 'dispatched', 'running', 'completed', 'failed', 'cancelled')),
        external_binding_receipt_json TEXT,
        created_at TEXT NOT NULL,
        "runtime_identity_json" TEXT,
        "terminal_receipt_json" TEXT,
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
    expected_definitions = _expected_schema_definition_manifest(
        legacy_lineage=bool(ledger and ledger[0][0] == LEGACY_MIGRATION_VERSION)
    )
    actual_definitions = _schema_definition_manifest(connection)
    missing_definitions = sorted(set(expected_definitions) - set(actual_definitions))
    unexpected_definitions = sorted(set(actual_definitions) - set(expected_definitions))
    mismatched_definitions = sorted(
        name
        for name in set(expected_definitions) & set(actual_definitions)
        if expected_definitions[name] != actual_definitions[name]
    )
    definition_errors = [
        *(f"missing definition: {name}" for name in missing_definitions),
        *(f"unexpected definition: {name}" for name in unexpected_definitions),
        *(f"definition digest mismatch: {name}" for name in mismatched_definitions),
    ]
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
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


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


def _apply_hierarchy_upgrade(connection: sqlite3.Connection) -> None:
    """Apply hierarchy tables and the v3 ledger row in one SQLite transaction."""
    checksum = migration_checksum()
    description = "Global Project hierarchy and append-only research records"
    script = (
        "BEGIN IMMEDIATE;\n"
        + MIGRATION_V3_SQL
        + "\nINSERT OR IGNORE INTO experiment_schema_migrations("
        + "version, name, checksum, description, applied_at) VALUES ("
        + f"{MIGRATION_VERSION}, '{MIGRATION_NAME}', '{checksum}', "
        + f"'{description}', '{datetime.now(timezone.utc).isoformat()}');\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def run_all(db_path: str | Path) -> None:
    path = Path(db_path).expanduser().resolve()
    connection = _connect(path)
    v2_checksum = _migration_v2_checksum()
    v3_checksum = migration_checksum()
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
                INSERT OR IGNORE INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_V2_VERSION,
                    MIGRATION_V2_NAME,
                    v2_checksum,
                    "Global workspace/experiment receipts and projections",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            _apply_hierarchy_upgrade(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_VERSION,
                    MIGRATION_NAME,
                    v3_checksum,
                    "Global Project hierarchy and append-only research records",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        elif rows == [(LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM)]:
            _apply_legacy_upgrade(connection)
            connection.execute(
                "UPDATE experiment_schema_migrations SET description = ? WHERE version = ?",
                ("Global workspace/experiment metadata foundation (legacy v1)", LEGACY_MIGRATION_VERSION),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_V2_VERSION,
                    MIGRATION_V2_NAME,
                    v2_checksum,
                    "Global workspace/experiment receipts and projections",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            _apply_hierarchy_upgrade(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_VERSION,
                    MIGRATION_NAME,
                    v3_checksum,
                    "Global Project hierarchy and append-only research records",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        elif rows in (
            [(MIGRATION_V2_VERSION, MIGRATION_V2_NAME, v2_checksum)],
            [
                (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM),
                (MIGRATION_V2_VERSION, MIGRATION_V2_NAME, v2_checksum),
            ],
        ):
            _apply_hierarchy_upgrade(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_VERSION,
                    MIGRATION_NAME,
                    v3_checksum,
                    "Global Project hierarchy and append-only research records",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            _cleanup_legacy_receipt_table(connection)
            connection.commit()
        elif rows in (
            [(MIGRATION_V2_VERSION, MIGRATION_V2_NAME, v2_checksum), (MIGRATION_VERSION, MIGRATION_NAME, v3_checksum)],
            [
                (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM),
                (MIGRATION_V2_VERSION, MIGRATION_V2_NAME, v2_checksum),
                (MIGRATION_VERSION, MIGRATION_NAME, v3_checksum),
            ],
        ):
            _cleanup_legacy_receipt_table(connection)
            connection.commit()
        else:
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
    "MIGRATION_SQL",
    "MIGRATION_V2_SQL",
    "MIGRATION_V3_SQL",
    "migration_checksum",
    "attest_schema",
    "run_all",
    "health",
]

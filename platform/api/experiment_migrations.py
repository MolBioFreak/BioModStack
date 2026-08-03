"""Versioned migrations for the global experiment/workspace SQLite store."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from migrations.sqlite_sha256 import register_sqlite_sha256


LEGACY_MIGRATION_VERSION = 1
LEGACY_MIGRATION_NAME = "global_experiment_workspace_foundation"
LEGACY_MIGRATION_CHECKSUM = "987620af4200932c8fffb282c5655d21aefc29a1a98cbfc3b54f3a734dfe6c10"
MIGRATION_VERSION = 2
MIGRATION_NAME = "global_experiment_workspace_receipts_and_projections"

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
)


def migration_checksum() -> str:
    return hashlib.sha256(MIGRATION_V2_SQL.encode("utf-8")).hexdigest()


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
                acknowledgement_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            receipt,
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


def run_all(db_path: str | Path) -> None:
    path = Path(db_path).expanduser().resolve()
    connection = _connect(path)
    checksum = migration_checksum()
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
                    MIGRATION_VERSION,
                    MIGRATION_NAME,
                    checksum,
                    "Global workspace/experiment receipts and projections",
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
                INSERT INTO experiment_schema_migrations(
                    version, name, checksum, description, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MIGRATION_VERSION,
                    MIGRATION_NAME,
                    checksum,
                    "Global workspace/experiment receipts and projections",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        elif rows in (
            [(MIGRATION_VERSION, MIGRATION_NAME, checksum)],
            [
                (LEGACY_MIGRATION_VERSION, LEGACY_MIGRATION_NAME, LEGACY_MIGRATION_CHECKSUM),
                (MIGRATION_VERSION, MIGRATION_NAME, checksum),
            ],
        ):
            _cleanup_legacy_receipt_table(connection)
        else:
            raise RuntimeError(f"experiment migration ledger mismatch: {rows!r}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"experiment foreign-key violations: {violations!r}")
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


__all__ = ["MIGRATION_VERSION", "MIGRATION_NAME", "MIGRATION_SQL", "migration_checksum", "run_all", "health"]

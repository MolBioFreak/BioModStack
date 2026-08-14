"""Versioned migrations for the dedicated MolBio/NGS domain-state store."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from migrations.sqlite_sha256 import register_sqlite_sha256
from paths import get_molbio_ngs_reference_root


MIGRATION_VERSION = 1
MIGRATION_NAME = "molbio_ngs_domain_state_v1"
MIGRATION_V2_VERSION = 2
MIGRATION_V2_NAME = "molbio_ngs_samples_references_v2"
MIGRATION_V3_VERSION = 3
MIGRATION_V3_NAME = "molbio_ngs_immutable_evidence_assessments_v3"
MIGRATION_V4_VERSION = 4
MIGRATION_V4_NAME = "molbio_ngs_versioned_bindings_ordered_outbox_v4"
LATEST_MIGRATION_VERSION = MIGRATION_V4_VERSION
BACKUP_MANIFEST_SCHEMA = "bms.molbio-ngs.domain-state-backup-manifest.v1"

LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS molbio_ngs_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

MIGRATION_SQL = r'''
CREATE TABLE IF NOT EXISTS molbio_ngs_domain_states (
    global_domain_experiment_id TEXT PRIMARY KEY,
    current_state_revision_id TEXT REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL DEFAULT 0 CHECK (head_generation >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS molbio_ngs_global_bindings (
    global_domain_experiment_id TEXT PRIMARY KEY
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    global_domain_experiment_revision_id TEXT NOT NULL,
    global_domain_experiment_revision_digest TEXT NOT NULL CHECK(length(global_domain_experiment_revision_digest) = 64),
    project_id TEXT NOT NULL,
    project_generation TEXT NOT NULL,
    project_digest TEXT NOT NULL CHECK(length(project_digest) = 64),
    project_receipt_id TEXT NOT NULL,
    project_reopen_destination TEXT NOT NULL,
    project_acknowledgement TEXT NOT NULL DEFAULT '{}',
    global_experiment_id TEXT NOT NULL,
    global_experiment_generation TEXT NOT NULL,
    global_experiment_digest TEXT NOT NULL CHECK(length(global_experiment_digest) = 64),
    global_experiment_receipt_id TEXT NOT NULL,
    global_experiment_reopen_destination TEXT NOT NULL,
    global_experiment_acknowledgement TEXT NOT NULL DEFAULT '{}',
    binding_state TEXT NOT NULL CHECK(binding_state IN (
        'unbound', 'acknowledged', 'stale', 'digest_mismatch', 'missing', 'unavailable',
        'degraded'
    )),
    last_verified_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS molbio_ngs_domain_state_revisions (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    global_domain_experiment_revision_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    parent_revision_id TEXT REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    membership_graph_sha256 TEXT NOT NULL CHECK(length(membership_graph_sha256) = 64),
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE(global_domain_experiment_id, revision_number),
    UNIQUE(global_domain_experiment_id, payload_sha256, membership_graph_sha256)
);

CREATE INDEX IF NOT EXISTS ix_molbio_ngs_state_revisions_domain_created
    ON molbio_ngs_domain_state_revisions(global_domain_experiment_id, created_at);

CREATE TABLE IF NOT EXISTS molbio_ngs_member_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_store_id TEXT NOT NULL CHECK(source_store_id IN (
        'molbio', 'core-ngs', 'molbio-ngs-domain'
    )),
    entity_kind TEXT NOT NULL CHECK(entity_kind IN (
        'molecular_revision', 'primer_revision', 'pcr_experiment_revision',
        'molecular_operation', 'ont_instrument_run', 'ngs_job',
        'ngs_result_manifest', 'ngs_comparison_panel',
        'ngs_reference_revision', 'ngs_evidence_assessment'
    )),
    entity_id TEXT NOT NULL,
    source_generation_or_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    availability TEXT NOT NULL CHECK(availability IN ('available', 'unavailable', 'unknown')),
    reopen_destination TEXT NOT NULL,
    canonical_receipt TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_molbio_ngs_member_receipts_source_identity
    ON molbio_ngs_member_receipts(
        source_store_id, entity_kind, entity_id, source_generation_or_revision
    );

CREATE TABLE IF NOT EXISTS molbio_ngs_domain_state_members (
    state_revision_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    sample_revision_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(state_revision_id, receipt_id, role),
    UNIQUE(state_revision_id, ordinal)
);

CREATE INDEX IF NOT EXISTS ix_molbio_ngs_state_members_identity
    ON molbio_ngs_domain_state_members(receipt_id);

CREATE TABLE IF NOT EXISTS molbio_ngs_idempotency_claims (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed')),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    result_resource_id TEXT NOT NULL,
    response_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK(
        (status = 'pending' AND response_json IS NULL AND completed_at IS NULL)
        OR (status = 'completed' AND response_json IS NOT NULL AND completed_at IS NOT NULL)
    ),
    PRIMARY KEY(scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS molbio_ngs_audit_events (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    resource_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at TEXT NOT NULL,
    created_by TEXT
);

CREATE INDEX IF NOT EXISTS ix_molbio_ngs_audit_domain_created
    ON molbio_ngs_audit_events(global_domain_experiment_id, created_at);

CREATE TABLE IF NOT EXISTS molbio_ngs_outbox_events (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    state_revision_id TEXT REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'leased', 'acknowledged', 'retryable_error', 'conflict'
    )),
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    next_retry_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    last_error TEXT,
    acknowledgement_json TEXT,
    acknowledgement_sha256 TEXT CHECK(
        acknowledgement_sha256 IS NULL OR length(acknowledgement_sha256) = 64
    ),
    conflict_json TEXT,
    conflict_sha256 TEXT CHECK(conflict_sha256 IS NULL OR length(conflict_sha256) = 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_molbio_ngs_outbox_status_created
    ON molbio_ngs_outbox_events(status, created_at);

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_state_revision_digest_insert
BEFORE INSERT ON molbio_ngs_domain_state_revisions
WHEN NEW.payload_sha256 != sha256(NEW.canonical_payload)
BEGIN
    SELECT RAISE(ABORT, 'state revision payload digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_state_revision_immutable_update
BEFORE UPDATE ON molbio_ngs_domain_state_revisions
BEGIN
    SELECT RAISE(ABORT, 'state revision is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_state_revision_immutable_delete
BEFORE DELETE ON molbio_ngs_domain_state_revisions
BEGIN
    SELECT RAISE(ABORT, 'state revision is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_member_receipt_digest_insert
BEFORE INSERT ON molbio_ngs_member_receipts
WHEN NEW.receipt_sha256 != sha256(NEW.canonical_receipt)
BEGIN
    SELECT RAISE(ABORT, 'member receipt digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_member_receipt_authority_insert
BEFORE INSERT ON molbio_ngs_member_receipts
WHEN json_valid(NEW.canonical_receipt) != 1
  OR json_extract(NEW.canonical_receipt, '$.schema') != 'bms.molbio-ngs.external-member-receipt.v1'
  OR json_extract(NEW.canonical_receipt, '$.receipt_id') IS NOT NEW.receipt_id
  OR json_extract(NEW.canonical_receipt, '$.source_store_id') IS NOT NEW.source_store_id
  OR json_extract(NEW.canonical_receipt, '$.entity_kind') IS NOT NEW.entity_kind
  OR json_extract(NEW.canonical_receipt, '$.entity_id') IS NOT NEW.entity_id
  OR CAST(json_extract(NEW.canonical_receipt, '$.source_generation_or_revision') AS TEXT)
       IS NOT NEW.source_generation_or_revision
  OR json_extract(NEW.canonical_receipt, '$.content_digest') IS NOT NEW.content_digest
  OR json_extract(NEW.canonical_receipt, '$.availability') IS NOT NEW.availability
  OR json(json_extract(NEW.canonical_receipt, '$.reopen_destination'))
       IS NOT json(NEW.reopen_destination)
  OR json_extract(NEW.canonical_receipt, '$.created_at') IS NOT NEW.created_at
  OR NEW.schema_name != 'bms.molbio-ngs.external-member-receipt'
  OR NEW.schema_version != '1'
BEGIN
    SELECT RAISE(ABORT, 'member receipt authority mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_member_receipt_immutable_update
BEFORE UPDATE ON molbio_ngs_member_receipts
BEGIN
    SELECT RAISE(ABORT, 'member receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_member_receipt_immutable_delete
BEFORE DELETE ON molbio_ngs_member_receipts
BEGIN
    SELECT RAISE(ABORT, 'member receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_state_member_immutable_update
BEFORE UPDATE ON molbio_ngs_domain_state_members
BEGIN
    SELECT RAISE(ABORT, 'state revision member is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_state_member_immutable_delete
BEFORE DELETE ON molbio_ngs_domain_state_members
BEGIN
    SELECT RAISE(ABORT, 'state revision member is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_binding_authority_immutable_update
BEFORE UPDATE ON molbio_ngs_global_bindings
WHEN OLD.global_domain_experiment_id IS NOT NEW.global_domain_experiment_id
  OR OLD.global_domain_experiment_revision_id IS NOT NEW.global_domain_experiment_revision_id
  OR OLD.global_domain_experiment_revision_digest IS NOT NEW.global_domain_experiment_revision_digest
  OR OLD.project_id IS NOT NEW.project_id
  OR OLD.project_generation IS NOT NEW.project_generation
  OR OLD.project_digest IS NOT NEW.project_digest
  OR OLD.project_receipt_id IS NOT NEW.project_receipt_id
  OR OLD.project_reopen_destination IS NOT NEW.project_reopen_destination
  OR OLD.project_acknowledgement IS NOT NEW.project_acknowledgement
  OR OLD.global_experiment_id IS NOT NEW.global_experiment_id
  OR OLD.global_experiment_generation IS NOT NEW.global_experiment_generation
  OR OLD.global_experiment_digest IS NOT NEW.global_experiment_digest
  OR OLD.global_experiment_receipt_id IS NOT NEW.global_experiment_receipt_id
  OR OLD.global_experiment_reopen_destination IS NOT NEW.global_experiment_reopen_destination
  OR OLD.global_experiment_acknowledgement IS NOT NEW.global_experiment_acknowledgement
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'global binding authority is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_domain_current_revision_validate_insert
BEFORE INSERT ON molbio_ngs_domain_states
WHEN (NEW.current_state_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_state_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_domain_state_revisions AS revision
       WHERE revision.id = NEW.current_state_revision_id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'domain state current revision authority mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_domain_current_revision_validate_update
BEFORE UPDATE OF current_state_revision_id, head_generation, global_domain_experiment_id
ON molbio_ngs_domain_states
WHEN (NEW.current_state_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_state_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_domain_state_revisions AS revision
       WHERE revision.id = NEW.current_state_revision_id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'domain state current revision authority mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_idempotency_state_insert
BEFORE INSERT ON molbio_ngs_idempotency_claims
WHEN (NEW.status = 'pending' AND (NEW.response_json IS NOT NULL OR NEW.completed_at IS NOT NULL))
  OR (NEW.status = 'completed' AND (NEW.response_json IS NULL OR NEW.completed_at IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'idempotency claim state is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_idempotency_identity_immutable_update
BEFORE UPDATE ON molbio_ngs_idempotency_claims
WHEN OLD.scope IS NOT NEW.scope
  OR OLD.idempotency_key IS NOT NEW.idempotency_key
  OR OLD.request_sha256 IS NOT NEW.request_sha256
  OR OLD.result_resource_id IS NOT NEW.result_resource_id
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'idempotency claim identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_idempotency_complete_once_update
BEFORE UPDATE ON molbio_ngs_idempotency_claims
WHEN OLD.scope IS NEW.scope
  AND OLD.idempotency_key IS NEW.idempotency_key
  AND OLD.request_sha256 IS NEW.request_sha256
  AND OLD.result_resource_id IS NEW.result_resource_id
  AND OLD.created_at IS NEW.created_at
  AND (
      OLD.status = 'completed'
      OR OLD.status != 'pending'
      OR NEW.status != 'completed'
      OR OLD.response_json IS NOT NULL
      OR NEW.response_json IS NULL
      OR OLD.completed_at IS NOT NULL
      OR NEW.completed_at IS NULL
  )
BEGIN
    SELECT RAISE(ABORT, 'completed idempotency claim is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_idempotency_immutable_delete
BEFORE DELETE ON molbio_ngs_idempotency_claims
BEGIN
    SELECT RAISE(ABORT, 'idempotency claim deletion is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_audit_digest_insert
BEFORE INSERT ON molbio_ngs_audit_events
WHEN NEW.payload_sha256 != sha256(NEW.payload_json)
BEGIN
    SELECT RAISE(ABORT, 'audit event payload digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_audit_immutable_update
BEFORE UPDATE ON molbio_ngs_audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit event is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_audit_immutable_delete
BEFORE DELETE ON molbio_ngs_audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit event is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_outbox_digest_insert
BEFORE INSERT ON molbio_ngs_outbox_events
WHEN NEW.payload_sha256 != sha256(NEW.payload_json)
BEGIN
    SELECT RAISE(ABORT, 'outbox event payload digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_outbox_evidence_digest_insert
BEFORE INSERT ON molbio_ngs_outbox_events
WHEN (NEW.acknowledgement_json IS NULL) != (NEW.acknowledgement_sha256 IS NULL)
  OR (NEW.acknowledgement_json IS NOT NULL
      AND NEW.acknowledgement_sha256 != sha256(NEW.acknowledgement_json))
  OR (NEW.conflict_json IS NULL) != (NEW.conflict_sha256 IS NULL)
  OR (NEW.conflict_json IS NOT NULL AND NEW.conflict_sha256 != sha256(NEW.conflict_json))
BEGIN
    SELECT RAISE(ABORT, 'outbox evidence digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_outbox_evidence_digest_update
BEFORE UPDATE ON molbio_ngs_outbox_events
WHEN (NEW.acknowledgement_json IS NULL) != (NEW.acknowledgement_sha256 IS NULL)
  OR (NEW.acknowledgement_json IS NOT NULL
      AND NEW.acknowledgement_sha256 != sha256(NEW.acknowledgement_json))
  OR (NEW.conflict_json IS NULL) != (NEW.conflict_sha256 IS NULL)
  OR (NEW.conflict_json IS NOT NULL AND NEW.conflict_sha256 != sha256(NEW.conflict_json))
BEGIN
    SELECT RAISE(ABORT, 'outbox evidence digest mismatch');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_outbox_payload_immutable_update
BEFORE UPDATE ON molbio_ngs_outbox_events
WHEN OLD.id IS NOT NEW.id
  OR OLD.global_domain_experiment_id IS NOT NEW.global_domain_experiment_id
  OR OLD.state_revision_id IS NOT NEW.state_revision_id
  OR OLD.event_type IS NOT NEW.event_type
  OR OLD.payload_json IS NOT NEW.payload_json
  OR OLD.payload_sha256 IS NOT NEW.payload_sha256
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'outbox event payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_molbio_ngs_outbox_immutable_delete
BEFORE DELETE ON molbio_ngs_outbox_events
BEGIN
    SELECT RAISE(ABORT, 'outbox event is immutable');
END;
'''

MIGRATION_V2_SQL = r'''
CREATE TABLE molbio_ngs_samples (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    current_revision_id TEXT REFERENCES molbio_ngs_sample_revisions(id) ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL DEFAULT 0 CHECK(head_generation >= 0),
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(global_domain_experiment_id, id)
);

CREATE INDEX ix_molbio_ngs_samples_domain_created
    ON molbio_ngs_samples(global_domain_experiment_id, created_at);

CREATE TABLE molbio_ngs_sample_revisions (
    id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL REFERENCES molbio_ngs_samples(id) ON DELETE RESTRICT,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    parent_revision_id TEXT REFERENCES molbio_ngs_sample_revisions(id) ON DELETE RESTRICT,
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE(sample_id, revision_number),
    UNIQUE(sample_id, payload_sha256),
    FOREIGN KEY(global_domain_experiment_id, sample_id)
        REFERENCES molbio_ngs_samples(global_domain_experiment_id, id) ON DELETE RESTRICT
);

CREATE INDEX ix_molbio_ngs_sample_revisions_sample_created
    ON molbio_ngs_sample_revisions(sample_id, created_at);

CREATE TABLE molbio_ngs_reference_resources (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    current_revision_id TEXT
        REFERENCES molbio_ngs_reference_revisions(id) ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL DEFAULT 0 CHECK(head_generation >= 0),
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(global_domain_experiment_id, id)
);

CREATE INDEX ix_molbio_ngs_reference_resources_domain_created
    ON molbio_ngs_reference_resources(global_domain_experiment_id, created_at);

CREATE TABLE molbio_ngs_reference_artifacts (
    id TEXT PRIMARY KEY,
    reference_id TEXT NOT NULL
        REFERENCES molbio_ngs_reference_resources(id) ON DELETE RESTRICT,
    managed_relative_path TEXT NOT NULL UNIQUE CHECK(
        managed_relative_path NOT LIKE '/%'
        AND managed_relative_path NOT LIKE '%..%'
    ),
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE molbio_ngs_reference_revisions (
    id TEXT PRIMARY KEY,
    reference_id TEXT NOT NULL
        REFERENCES molbio_ngs_reference_resources(id) ON DELETE RESTRICT,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    parent_revision_id TEXT
        REFERENCES molbio_ngs_reference_revisions(id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL
        REFERENCES molbio_ngs_reference_artifacts(id) ON DELETE RESTRICT,
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    canonical_fasta_sha256 TEXT NOT NULL CHECK(length(canonical_fasta_sha256) = 64),
    canonical_fasta_size_bytes INTEGER NOT NULL CHECK(canonical_fasta_size_bytes > 0),
    contig_manifest_sha256 TEXT NOT NULL CHECK(length(contig_manifest_sha256) = 64),
    normalized_sequence_sha256 TEXT CHECK(
        normalized_sequence_sha256 IS NULL OR length(normalized_sequence_sha256) = 64
    ),
    molecule_type TEXT NOT NULL CHECK(molecule_type IN ('dna', 'rna')),
    topology TEXT NOT NULL CHECK(topology IN ('linear', 'circular', 'mixed', 'unknown')),
    coordinate_contract TEXT NOT NULL,
    source_provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE(reference_id, revision_number),
    UNIQUE(reference_id, payload_sha256),
    FOREIGN KEY(global_domain_experiment_id, reference_id)
        REFERENCES molbio_ngs_reference_resources(global_domain_experiment_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX ix_molbio_ngs_reference_revisions_resource_created
    ON molbio_ngs_reference_revisions(reference_id, created_at);

ALTER TABLE molbio_ngs_domain_state_members RENAME TO molbio_ngs_domain_state_members_v1;

CREATE TABLE molbio_ngs_domain_state_members (
    state_revision_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    sample_revision_id TEXT
        REFERENCES molbio_ngs_sample_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(state_revision_id, receipt_id, role),
    UNIQUE(state_revision_id, ordinal)
);

INSERT INTO molbio_ngs_domain_state_members(
    state_revision_id, receipt_id, role, ordinal, sample_revision_id, created_at
)
SELECT state_revision_id, receipt_id, role, ordinal, sample_revision_id, created_at
  FROM molbio_ngs_domain_state_members_v1;

DROP TABLE molbio_ngs_domain_state_members_v1;

CREATE INDEX ix_molbio_ngs_state_members_identity
    ON molbio_ngs_domain_state_members(receipt_id);

CREATE TRIGGER trg_molbio_ngs_state_member_immutable_update
BEFORE UPDATE ON molbio_ngs_domain_state_members
BEGIN
    SELECT RAISE(ABORT, 'state revision member is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_state_member_immutable_delete
BEFORE DELETE ON molbio_ngs_domain_state_members
BEGIN
    SELECT RAISE(ABORT, 'state revision member is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_sample_revision_digest_insert
BEFORE INSERT ON molbio_ngs_sample_revisions
WHEN NEW.payload_sha256 != sha256(NEW.canonical_payload)
BEGIN
    SELECT RAISE(ABORT, 'sample revision payload digest mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_sample_revision_immutable_update
BEFORE UPDATE ON molbio_ngs_sample_revisions
BEGIN
    SELECT RAISE(ABORT, 'sample revision is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_sample_revision_immutable_delete
BEFORE DELETE ON molbio_ngs_sample_revisions
BEGIN
    SELECT RAISE(ABORT, 'sample revision is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_sample_current_revision_validate_insert
BEFORE INSERT ON molbio_ngs_samples
WHEN (NEW.current_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_sample_revisions AS revision
       WHERE revision.id = NEW.current_revision_id
         AND revision.sample_id = NEW.id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'sample current revision authority mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_sample_current_revision_validate_update
BEFORE UPDATE OF current_revision_id, head_generation, id, global_domain_experiment_id
ON molbio_ngs_samples
WHEN (NEW.current_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_sample_revisions AS revision
       WHERE revision.id = NEW.current_revision_id
         AND revision.sample_id = NEW.id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'sample current revision authority mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_reference_artifact_immutable_update
BEFORE UPDATE ON molbio_ngs_reference_artifacts
BEGIN
    SELECT RAISE(ABORT, 'reference artifact is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_reference_artifact_immutable_delete
BEFORE DELETE ON molbio_ngs_reference_artifacts
BEGIN
    SELECT RAISE(ABORT, 'reference artifact is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_reference_revision_digest_insert
BEFORE INSERT ON molbio_ngs_reference_revisions
WHEN NEW.payload_sha256 != sha256(NEW.canonical_payload)
  OR json_valid(NEW.canonical_payload) != 1
  OR json_extract(NEW.canonical_payload, '$.canonical_fasta.sha256') IS NOT NEW.canonical_fasta_sha256
  OR json_extract(NEW.canonical_payload, '$.canonical_fasta.size_bytes') IS NOT NEW.canonical_fasta_size_bytes
  OR json_extract(NEW.canonical_payload, '$.contig_manifest_sha256') IS NOT NEW.contig_manifest_sha256
BEGIN
    SELECT RAISE(ABORT, 'reference revision authority mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_reference_revision_immutable_update
BEFORE UPDATE ON molbio_ngs_reference_revisions
BEGIN
    SELECT RAISE(ABORT, 'reference revision is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_reference_revision_immutable_delete
BEFORE DELETE ON molbio_ngs_reference_revisions
BEGIN
    SELECT RAISE(ABORT, 'reference revision is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_reference_current_revision_validate_insert
BEFORE INSERT ON molbio_ngs_reference_resources
WHEN (NEW.current_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_reference_revisions AS revision
       WHERE revision.id = NEW.current_revision_id
         AND revision.reference_id = NEW.id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'reference current revision authority mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_reference_current_revision_validate_update
BEFORE UPDATE OF current_revision_id, head_generation, id, global_domain_experiment_id
ON molbio_ngs_reference_resources
WHEN (NEW.current_revision_id IS NULL AND NEW.head_generation != 0)
  OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM molbio_ngs_reference_revisions AS revision
       WHERE revision.id = NEW.current_revision_id
         AND revision.reference_id = NEW.id
         AND revision.global_domain_experiment_id = NEW.global_domain_experiment_id
         AND revision.revision_number = NEW.head_generation
  ))
BEGIN
    SELECT RAISE(ABORT, 'reference current revision authority mismatch');
END;
'''

MIGRATION_V3_SQL = r'''
CREATE UNIQUE INDEX uq_molbio_ngs_state_revision_domain_identity
    ON molbio_ngs_domain_state_revisions(global_domain_experiment_id, id);

CREATE UNIQUE INDEX uq_molbio_ngs_sample_revision_domain_identity
    ON molbio_ngs_sample_revisions(global_domain_experiment_id, id);

CREATE TABLE molbio_ngs_evidence_assessments (
    evidence_id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL
        REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    state_revision_id TEXT NOT NULL,
    sample_revision_id TEXT,
    ngs_job_receipt_id TEXT NOT NULL
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_result_manifest_receipt_id TEXT NOT NULL
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_reference_revision_receipt_id TEXT NOT NULL
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ont_instrument_run_receipt_id TEXT
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    molecular_revision_receipt_id TEXT
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_comparison_panel_receipt_id TEXT
        REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    assessment_rule_id TEXT NOT NULL,
    requested_assessment TEXT NOT NULL
        CHECK(requested_assessment IN ('PASS', 'FAIL', 'REVIEW')),
    scientific_assessment TEXT NOT NULL
        CHECK(scientific_assessment IN ('PASS', 'FAIL', 'REVIEW')),
    job_lifecycle_state TEXT NOT NULL
        CHECK(job_lifecycle_state IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    manifest_integrity TEXT NOT NULL
        CHECK(manifest_integrity IN ('valid', 'invalid', 'unavailable')),
    raw_manifest_sha256 TEXT NOT NULL CHECK(
        length(raw_manifest_sha256) = 64
        AND raw_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    notes TEXT,
    canonical_wrapper TEXT NOT NULL CHECK(json_valid(canonical_wrapper) = 1),
    wrapper_sha256 TEXT NOT NULL CHECK(
        length(wrapper_sha256) = 64
        AND wrapper_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    created_by TEXT,
    FOREIGN KEY(global_domain_experiment_id, state_revision_id)
        REFERENCES molbio_ngs_domain_state_revisions(global_domain_experiment_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY(global_domain_experiment_id, sample_revision_id)
        REFERENCES molbio_ngs_sample_revisions(global_domain_experiment_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX ix_molbio_ngs_evidence_domain_created
    ON molbio_ngs_evidence_assessments(global_domain_experiment_id, created_at, evidence_id);
CREATE INDEX ix_molbio_ngs_evidence_state_revision
    ON molbio_ngs_evidence_assessments(state_revision_id);
CREATE INDEX ix_molbio_ngs_evidence_sample_revision
    ON molbio_ngs_evidence_assessments(sample_revision_id);

CREATE TRIGGER trg_molbio_ngs_evidence_digest_insert
BEFORE INSERT ON molbio_ngs_evidence_assessments
WHEN NEW.wrapper_sha256 != sha256(NEW.canonical_wrapper)
BEGIN
    SELECT RAISE(ABORT, 'evidence assessment wrapper digest mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_evidence_authority_insert
BEFORE INSERT ON molbio_ngs_evidence_assessments
WHEN json_extract(NEW.canonical_wrapper, '$.schema') IS NOT 'bms.molbio-ngs.ngs-evidence-receipt.v1'
  OR json_extract(NEW.canonical_wrapper, '$.evidence_id') IS NOT NEW.evidence_id
  OR json_extract(NEW.canonical_wrapper, '$.global_domain_experiment_id') IS NOT NEW.global_domain_experiment_id
  OR json_extract(NEW.canonical_wrapper, '$.state_revision_id') IS NOT NEW.state_revision_id
  OR json_extract(NEW.canonical_wrapper, '$.sample_revision_id') IS NOT NEW.sample_revision_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_job') IS NOT NEW.ngs_job_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_result_manifest') IS NOT NEW.ngs_result_manifest_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_reference_revision') IS NOT NEW.ngs_reference_revision_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ont_instrument_run') IS NOT NEW.ont_instrument_run_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.molecular_revision') IS NOT NEW.molecular_revision_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_comparison_panel') IS NOT NEW.ngs_comparison_panel_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.assessment_rule_id') IS NOT NEW.assessment_rule_id
  OR json_extract(NEW.canonical_wrapper, '$.requested_assessment') IS NOT NEW.requested_assessment
  OR json_extract(NEW.canonical_wrapper, '$.scientific_assessment') IS NOT NEW.scientific_assessment
  OR json_extract(NEW.canonical_wrapper, '$.job_lifecycle_state') IS NOT NEW.job_lifecycle_state
  OR json_extract(NEW.canonical_wrapper, '$.manifest_integrity') IS NOT NEW.manifest_integrity
  OR json_extract(NEW.canonical_wrapper, '$.raw_manifest_sha256') IS NOT NEW.raw_manifest_sha256
  OR json_extract(NEW.canonical_wrapper, '$.notes') IS NOT NEW.notes
  OR json_extract(NEW.canonical_wrapper, '$.created_at') IS NOT NEW.created_at
  OR json_extract(NEW.canonical_wrapper, '$.created_by') IS NOT NEW.created_by
BEGIN
    SELECT RAISE(ABORT, 'evidence assessment wrapper authority mismatch');
END;

CREATE TRIGGER trg_molbio_ngs_evidence_immutable_update
BEFORE UPDATE ON molbio_ngs_evidence_assessments
BEGIN
    SELECT RAISE(ABORT, 'evidence assessment is immutable');
END;

CREATE TRIGGER trg_molbio_ngs_evidence_immutable_delete
BEFORE DELETE ON molbio_ngs_evidence_assessments
BEGIN
    SELECT RAISE(ABORT, 'evidence assessment is immutable');
END;
'''

MIGRATION_V4_SQL = r'''
DROP TRIGGER trg_molbio_ngs_evidence_digest_insert;
DROP TRIGGER trg_molbio_ngs_evidence_authority_insert;
DROP TRIGGER trg_molbio_ngs_evidence_immutable_update;
DROP TRIGGER trg_molbio_ngs_evidence_immutable_delete;
DROP TRIGGER trg_molbio_ngs_state_member_immutable_update;
DROP TRIGGER trg_molbio_ngs_state_member_immutable_delete;
DROP TRIGGER trg_molbio_ngs_member_receipt_digest_insert;
DROP TRIGGER trg_molbio_ngs_member_receipt_authority_insert;
DROP TRIGGER trg_molbio_ngs_member_receipt_immutable_update;
DROP TRIGGER trg_molbio_ngs_member_receipt_immutable_delete;
DROP INDEX ix_molbio_ngs_evidence_domain_created;
DROP INDEX ix_molbio_ngs_evidence_state_revision;
DROP INDEX ix_molbio_ngs_evidence_sample_revision;
DROP INDEX ix_molbio_ngs_state_members_identity;
DROP INDEX ix_molbio_ngs_member_receipts_source_identity;
ALTER TABLE molbio_ngs_evidence_assessments RENAME TO molbio_ngs_evidence_assessments_v3;
ALTER TABLE molbio_ngs_domain_state_members RENAME TO molbio_ngs_domain_state_members_v3;
ALTER TABLE molbio_ngs_member_receipts RENAME TO molbio_ngs_member_receipts_v3;
CREATE TABLE molbio_ngs_member_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_store_id TEXT NOT NULL CHECK(source_store_id IN ('molbio','core-ngs','molbio-ngs-domain')),
    entity_kind TEXT NOT NULL CHECK(entity_kind IN (
        'molecular_revision','primer_revision','pcr_experiment_revision',
        'molecular_operation','ont_instrument_run','ngs_job','ngs_result_manifest',
        'ngs_comparison_panel','ngs_reference_revision','ngs_evidence_assessment',
        'sample_revision','ngs_molbio_state_revision'
    )),
    entity_id TEXT NOT NULL,
    source_generation_or_revision TEXT NOT NULL,
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    availability TEXT NOT NULL CHECK(availability IN ('available','unavailable','unknown')),
    reopen_destination TEXT NOT NULL,
    canonical_receipt TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256) = 64),
    created_at TEXT NOT NULL
);
INSERT INTO molbio_ngs_member_receipts
SELECT * FROM molbio_ngs_member_receipts_v3;
CREATE TABLE molbio_ngs_domain_state_members (
    state_revision_id TEXT NOT NULL REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    sample_revision_id TEXT REFERENCES molbio_ngs_sample_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(state_revision_id, receipt_id, role),
    UNIQUE(state_revision_id, ordinal)
);
INSERT INTO molbio_ngs_domain_state_members
SELECT * FROM molbio_ngs_domain_state_members_v3;
CREATE TABLE molbio_ngs_evidence_assessments (
    evidence_id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    state_revision_id TEXT NOT NULL,
    sample_revision_id TEXT,
    ngs_job_receipt_id TEXT NOT NULL REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_result_manifest_receipt_id TEXT NOT NULL REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_reference_revision_receipt_id TEXT NOT NULL REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ont_instrument_run_receipt_id TEXT REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    molecular_revision_receipt_id TEXT REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    ngs_comparison_panel_receipt_id TEXT REFERENCES molbio_ngs_member_receipts(receipt_id) ON DELETE RESTRICT,
    assessment_rule_id TEXT NOT NULL,
    requested_assessment TEXT NOT NULL CHECK(requested_assessment IN ('PASS','FAIL','REVIEW')),
    scientific_assessment TEXT NOT NULL CHECK(scientific_assessment IN ('PASS','FAIL','REVIEW')),
    job_lifecycle_state TEXT NOT NULL CHECK(job_lifecycle_state IN ('queued','running','completed','failed','cancelled')),
    manifest_integrity TEXT NOT NULL CHECK(manifest_integrity IN ('valid','invalid','unavailable')),
    raw_manifest_sha256 TEXT NOT NULL CHECK(length(raw_manifest_sha256) = 64 AND raw_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    notes TEXT,
    canonical_wrapper TEXT NOT NULL CHECK(json_valid(canonical_wrapper) = 1),
    wrapper_sha256 TEXT NOT NULL CHECK(length(wrapper_sha256) = 64 AND wrapper_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    created_by TEXT,
    FOREIGN KEY(global_domain_experiment_id, state_revision_id)
        REFERENCES molbio_ngs_domain_state_revisions(global_domain_experiment_id, id) ON DELETE RESTRICT,
    FOREIGN KEY(global_domain_experiment_id, sample_revision_id)
        REFERENCES molbio_ngs_sample_revisions(global_domain_experiment_id, id) ON DELETE RESTRICT
);
INSERT INTO molbio_ngs_evidence_assessments
SELECT * FROM molbio_ngs_evidence_assessments_v3;
DROP TABLE molbio_ngs_evidence_assessments_v3;
DROP TABLE molbio_ngs_domain_state_members_v3;
DROP TABLE molbio_ngs_member_receipts_v3;
CREATE INDEX ix_molbio_ngs_member_receipts_source_identity
    ON molbio_ngs_member_receipts(source_store_id, entity_kind, entity_id, source_generation_or_revision);
CREATE INDEX ix_molbio_ngs_state_members_identity ON molbio_ngs_domain_state_members(receipt_id);
CREATE INDEX ix_molbio_ngs_evidence_domain_created
    ON molbio_ngs_evidence_assessments(global_domain_experiment_id, created_at, evidence_id);
CREATE INDEX ix_molbio_ngs_evidence_state_revision ON molbio_ngs_evidence_assessments(state_revision_id);
CREATE INDEX ix_molbio_ngs_evidence_sample_revision ON molbio_ngs_evidence_assessments(sample_revision_id);
CREATE TRIGGER trg_molbio_ngs_member_receipt_digest_insert
BEFORE INSERT ON molbio_ngs_member_receipts
WHEN NEW.receipt_sha256 != sha256(NEW.canonical_receipt)
BEGIN SELECT RAISE(ABORT, 'member receipt digest mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_member_receipt_authority_insert
BEFORE INSERT ON molbio_ngs_member_receipts
WHEN json_valid(NEW.canonical_receipt) != 1
  OR json_extract(NEW.canonical_receipt, '$.schema') != 'bms.molbio-ngs.external-member-receipt.v1'
  OR json_extract(NEW.canonical_receipt, '$.receipt_id') IS NOT NEW.receipt_id
  OR json_extract(NEW.canonical_receipt, '$.source_store_id') IS NOT NEW.source_store_id
  OR json_extract(NEW.canonical_receipt, '$.entity_kind') IS NOT NEW.entity_kind
  OR json_extract(NEW.canonical_receipt, '$.entity_id') IS NOT NEW.entity_id
  OR CAST(json_extract(NEW.canonical_receipt, '$.source_generation_or_revision') AS TEXT) IS NOT NEW.source_generation_or_revision
  OR json_extract(NEW.canonical_receipt, '$.content_digest') IS NOT NEW.content_digest
  OR json_extract(NEW.canonical_receipt, '$.availability') IS NOT NEW.availability
  OR json(json_extract(NEW.canonical_receipt, '$.reopen_destination')) IS NOT json(NEW.reopen_destination)
  OR json_extract(NEW.canonical_receipt, '$.created_at') IS NOT NEW.created_at
  OR NEW.schema_name != 'bms.molbio-ngs.external-member-receipt' OR NEW.schema_version != '1'
BEGIN SELECT RAISE(ABORT, 'member receipt authority mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_member_receipt_immutable_update BEFORE UPDATE ON molbio_ngs_member_receipts
BEGIN SELECT RAISE(ABORT, 'member receipt is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_member_receipt_immutable_delete BEFORE DELETE ON molbio_ngs_member_receipts
BEGIN SELECT RAISE(ABORT, 'member receipt is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_state_member_immutable_update BEFORE UPDATE ON molbio_ngs_domain_state_members
BEGIN SELECT RAISE(ABORT, 'state revision member is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_state_member_immutable_delete BEFORE DELETE ON molbio_ngs_domain_state_members
BEGIN SELECT RAISE(ABORT, 'state revision member is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_evidence_digest_insert
BEFORE INSERT ON molbio_ngs_evidence_assessments
WHEN NEW.wrapper_sha256 != sha256(NEW.canonical_wrapper)
BEGIN SELECT RAISE(ABORT, 'evidence assessment wrapper digest mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_evidence_authority_insert
BEFORE INSERT ON molbio_ngs_evidence_assessments
WHEN json_extract(NEW.canonical_wrapper, '$.schema') IS NOT 'bms.molbio-ngs.ngs-evidence-receipt.v1'
  OR json_extract(NEW.canonical_wrapper, '$.evidence_id') IS NOT NEW.evidence_id
  OR json_extract(NEW.canonical_wrapper, '$.global_domain_experiment_id') IS NOT NEW.global_domain_experiment_id
  OR json_extract(NEW.canonical_wrapper, '$.state_revision_id') IS NOT NEW.state_revision_id
  OR json_extract(NEW.canonical_wrapper, '$.sample_revision_id') IS NOT NEW.sample_revision_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_job') IS NOT NEW.ngs_job_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_result_manifest') IS NOT NEW.ngs_result_manifest_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_reference_revision') IS NOT NEW.ngs_reference_revision_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ont_instrument_run') IS NOT NEW.ont_instrument_run_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.molecular_revision') IS NOT NEW.molecular_revision_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.receipt_ids.ngs_comparison_panel') IS NOT NEW.ngs_comparison_panel_receipt_id
  OR json_extract(NEW.canonical_wrapper, '$.assessment_rule_id') IS NOT NEW.assessment_rule_id
  OR json_extract(NEW.canonical_wrapper, '$.requested_assessment') IS NOT NEW.requested_assessment
  OR json_extract(NEW.canonical_wrapper, '$.scientific_assessment') IS NOT NEW.scientific_assessment
  OR json_extract(NEW.canonical_wrapper, '$.job_lifecycle_state') IS NOT NEW.job_lifecycle_state
  OR json_extract(NEW.canonical_wrapper, '$.manifest_integrity') IS NOT NEW.manifest_integrity
  OR json_extract(NEW.canonical_wrapper, '$.raw_manifest_sha256') IS NOT NEW.raw_manifest_sha256
  OR json_extract(NEW.canonical_wrapper, '$.notes') IS NOT NEW.notes
  OR json_extract(NEW.canonical_wrapper, '$.created_at') IS NOT NEW.created_at
  OR json_extract(NEW.canonical_wrapper, '$.created_by') IS NOT NEW.created_by
BEGIN SELECT RAISE(ABORT, 'evidence assessment wrapper authority mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_evidence_immutable_update BEFORE UPDATE ON molbio_ngs_evidence_assessments
BEGIN SELECT RAISE(ABORT, 'evidence assessment is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_evidence_immutable_delete BEFORE DELETE ON molbio_ngs_evidence_assessments
BEGIN SELECT RAISE(ABORT, 'evidence assessment is immutable'); END;

CREATE TABLE molbio_ngs_global_binding_revisions (
    binding_revision_id TEXT PRIMARY KEY NOT NULL,
    global_domain_experiment_id TEXT NOT NULL REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) DEFERRABLE INITIALLY DEFERRED,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    supersedes_binding_revision_id TEXT REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id),
    global_domain_experiment_revision_id TEXT NOT NULL,
    global_domain_experiment_revision_digest TEXT NOT NULL CHECK(length(global_domain_experiment_revision_digest) = 64),
    project_id TEXT NOT NULL, project_generation TEXT NOT NULL,
    project_digest TEXT NOT NULL CHECK(length(project_digest) = 64),
    project_receipt_id TEXT NOT NULL, project_reopen_destination TEXT NOT NULL,
    project_acknowledgement TEXT NOT NULL,
    global_experiment_id TEXT NOT NULL, global_experiment_generation TEXT NOT NULL,
    global_experiment_digest TEXT NOT NULL CHECK(length(global_experiment_digest) = 64),
    global_experiment_receipt_id TEXT NOT NULL,
    global_experiment_reopen_destination TEXT NOT NULL,
    global_experiment_acknowledgement TEXT NOT NULL,
    global_binding_receipt_id TEXT,
    global_binding_receipt_json TEXT,
    global_binding_receipt_sha256 TEXT,
    connector_command_id TEXT UNIQUE,
    binding_state TEXT NOT NULL CHECK(binding_state IN ('needs_reverification','acknowledged','stale','conflicted')),
    last_verified_at TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT,
    UNIQUE(global_domain_experiment_id, revision_number),
    CHECK((global_binding_receipt_id IS NULL AND global_binding_receipt_json IS NULL AND global_binding_receipt_sha256 IS NULL)
       OR (global_binding_receipt_id IS NOT NULL AND global_binding_receipt_json IS NOT NULL
           AND global_binding_receipt_sha256 IS NOT NULL
           AND sha256(global_binding_receipt_json) = lower(global_binding_receipt_sha256)))
);
ALTER TABLE molbio_ngs_domain_states ADD COLUMN current_binding_revision_id TEXT REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE molbio_ngs_domain_state_revisions ADD COLUMN binding_revision_id TEXT REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id);
DROP TRIGGER trg_molbio_ngs_state_revision_immutable_update;
CREATE TRIGGER trg_molbio_ngs_state_revision_immutable_update
BEFORE UPDATE ON molbio_ngs_domain_state_revisions
WHEN OLD.binding_revision_id IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'state revision is immutable'); END;
ALTER TABLE molbio_ngs_outbox_events ADD COLUMN binding_revision_id TEXT REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id);
ALTER TABLE molbio_ngs_outbox_events ADD COLUMN event_stream TEXT;
ALTER TABLE molbio_ngs_outbox_events ADD COLUMN stream_generation INTEGER;
ALTER TABLE molbio_ngs_outbox_events ADD COLUMN source_generation INTEGER;
CREATE UNIQUE INDEX ux_molbio_ngs_outbox_stream_generation
    ON molbio_ngs_outbox_events(global_domain_experiment_id, binding_revision_id, event_stream, stream_generation);
CREATE INDEX ix_molbio_ngs_outbox_delivery
    ON molbio_ngs_outbox_events(status, next_retry_at, created_at);
CREATE TABLE molbio_ngs_outbox_streams (
    global_domain_experiment_id TEXT NOT NULL REFERENCES molbio_ngs_domain_states(global_domain_experiment_id),
    binding_revision_id TEXT NOT NULL REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id),
    event_stream TEXT NOT NULL,
    next_stream_generation INTEGER NOT NULL CHECK(next_stream_generation > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(global_domain_experiment_id, binding_revision_id, event_stream)
);
CREATE TABLE molbio_ngs_connector_acknowledgements (
    acknowledgement_id TEXT PRIMARY KEY NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    binding_revision_id TEXT NOT NULL REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id),
    disposition TEXT NOT NULL CHECK(disposition IN ('applied','duplicate','retryable','conflicted','deferred_gap')),
    acknowledgement_json TEXT NOT NULL,
    acknowledgement_sha256 TEXT NOT NULL CHECK(length(acknowledgement_sha256) = 64),
    created_at TEXT NOT NULL,
    CHECK(sha256(acknowledgement_json) = lower(acknowledgement_sha256))
);
CREATE TRIGGER trg_molbio_ngs_binding_revision_immutable_update
BEFORE UPDATE OF binding_revision_id, global_domain_experiment_id, revision_number,
 supersedes_binding_revision_id, global_domain_experiment_revision_id,
 global_domain_experiment_revision_digest, project_id, project_generation, project_digest,
 project_receipt_id, project_reopen_destination, project_acknowledgement,
 global_experiment_id, global_experiment_generation, global_experiment_digest,
 global_experiment_receipt_id, global_experiment_reopen_destination,
 global_experiment_acknowledgement, global_binding_receipt_id,
 global_binding_receipt_json, global_binding_receipt_sha256, connector_command_id, created_at
ON molbio_ngs_global_binding_revisions
BEGIN SELECT RAISE(ABORT, 'binding revision authority is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_binding_revision_delete_forbidden
BEFORE DELETE ON molbio_ngs_global_binding_revisions
BEGIN SELECT RAISE(ABORT, 'binding revisions are append-only'); END;
CREATE TRIGGER trg_molbio_ngs_state_binding_required_insert
BEFORE INSERT ON molbio_ngs_domain_states WHEN NEW.current_binding_revision_id IS NULL
BEGIN SELECT RAISE(ABORT, 'Domain state requires a current binding revision'); END;
CREATE TRIGGER trg_molbio_ngs_state_binding_required_update
BEFORE UPDATE OF current_binding_revision_id ON molbio_ngs_domain_states
WHEN NEW.current_binding_revision_id IS NULL
BEGIN SELECT RAISE(ABORT, 'Domain state requires a current binding revision'); END;
CREATE TRIGGER trg_molbio_ngs_state_revision_binding_required_insert
BEFORE INSERT ON molbio_ngs_domain_state_revisions WHEN NEW.binding_revision_id IS NULL
BEGIN SELECT RAISE(ABORT, 'state revision requires a binding revision'); END;
CREATE TRIGGER trg_molbio_ngs_state_revision_binding_immutable
BEFORE UPDATE OF binding_revision_id ON molbio_ngs_domain_state_revisions
BEGIN SELECT RAISE(ABORT, 'state revision binding is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_ordering_required_insert
BEFORE INSERT ON molbio_ngs_outbox_events
WHEN NEW.binding_revision_id IS NULL OR NEW.event_stream IS NULL OR NEW.stream_generation IS NULL OR NEW.stream_generation < 1
BEGIN SELECT RAISE(ABORT, 'ordered outbox authority is required'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_ordering_immutable
BEFORE UPDATE OF binding_revision_id, event_stream, stream_generation, source_generation ON molbio_ngs_outbox_events
WHEN OLD.binding_revision_id IS NOT NULL OR OLD.event_stream IS NOT NULL OR OLD.stream_generation IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'outbox ordering authority is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_lease_fence
BEFORE UPDATE OF status, acknowledgement_json, acknowledgement_sha256, conflict_json, conflict_sha256 ON molbio_ngs_outbox_events
WHEN (NEW.status = 'leased' AND (
        NEW.lease_owner IS NULL OR NEW.lease_token IS NULL OR NEW.lease_expires_at IS NULL
     ))
  OR (NEW.status <> 'leased' AND (
        NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
     ))
  OR (NEW.status = 'acknowledged' AND (
        NEW.acknowledgement_json IS NULL OR NEW.acknowledgement_sha256 IS NULL
        OR NEW.conflict_json IS NOT NULL OR NEW.conflict_sha256 IS NOT NULL
     ))
  OR (NEW.status = 'conflict' AND (
        NEW.conflict_json IS NULL OR NEW.conflict_sha256 IS NULL
        OR NEW.acknowledgement_json IS NOT NULL OR NEW.acknowledgement_sha256 IS NOT NULL
     ))
BEGIN SELECT RAISE(ABORT, 'invalid token-fenced outbox delivery state'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_delivery_state_insert
BEFORE INSERT ON molbio_ngs_outbox_events
WHEN (NEW.status = 'leased' AND (
        NEW.lease_owner IS NULL OR NEW.lease_token IS NULL OR NEW.lease_expires_at IS NULL
     ))
  OR (NEW.status <> 'leased' AND (
        NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
     ))
  OR (NEW.status = 'acknowledged' AND (
        NEW.acknowledgement_json IS NULL OR NEW.acknowledgement_sha256 IS NULL
        OR NEW.conflict_json IS NOT NULL OR NEW.conflict_sha256 IS NOT NULL
     ))
  OR (NEW.status = 'conflict' AND (
        NEW.conflict_json IS NULL OR NEW.conflict_sha256 IS NULL
        OR NEW.acknowledgement_json IS NOT NULL OR NEW.acknowledgement_sha256 IS NOT NULL
     ))
BEGIN SELECT RAISE(ABORT, 'invalid outbox delivery state'); END;
CREATE TRIGGER trg_molbio_ngs_connector_ack_immutable_update BEFORE UPDATE ON molbio_ngs_connector_acknowledgements
BEGIN SELECT RAISE(ABORT, 'connector acknowledgement is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_connector_ack_immutable_delete BEFORE DELETE ON molbio_ngs_connector_acknowledgements
BEGIN SELECT RAISE(ABORT, 'connector acknowledgement is immutable'); END;
'''

MIGRATION_V4_FINALIZE_SQL = r'''
DROP TRIGGER trg_molbio_ngs_binding_authority_immutable_update;
DROP TABLE molbio_ngs_global_bindings;
DROP TRIGGER trg_molbio_ngs_outbox_digest_insert;
DROP TRIGGER trg_molbio_ngs_outbox_evidence_digest_insert;
DROP TRIGGER trg_molbio_ngs_outbox_evidence_digest_update;
DROP TRIGGER trg_molbio_ngs_outbox_payload_immutable_update;
DROP TRIGGER trg_molbio_ngs_outbox_immutable_delete;
DROP TRIGGER trg_molbio_ngs_outbox_ordering_required_insert;
DROP TRIGGER trg_molbio_ngs_outbox_ordering_immutable;
DROP TRIGGER trg_molbio_ngs_outbox_lease_fence;
DROP TRIGGER trg_molbio_ngs_outbox_delivery_state_insert;
DROP INDEX ix_molbio_ngs_outbox_status_created;
DROP INDEX ux_molbio_ngs_outbox_stream_generation;
DROP INDEX ix_molbio_ngs_outbox_delivery;
ALTER TABLE molbio_ngs_outbox_events RENAME TO molbio_ngs_outbox_events_v4_stage;
CREATE TABLE molbio_ngs_outbox_events (
    id TEXT PRIMARY KEY,
    global_domain_experiment_id TEXT NOT NULL REFERENCES molbio_ngs_domain_states(global_domain_experiment_id) ON DELETE RESTRICT,
    state_revision_id TEXT REFERENCES molbio_ngs_domain_state_revisions(id) ON DELETE RESTRICT,
    binding_revision_id TEXT NOT NULL REFERENCES molbio_ngs_global_binding_revisions(binding_revision_id),
    event_type TEXT NOT NULL,
    event_stream TEXT NOT NULL,
    stream_generation INTEGER NOT NULL CHECK(stream_generation > 0),
    source_generation INTEGER,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','leased','acknowledged','retryable_error','conflict')),
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    next_retry_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    last_error TEXT,
    acknowledgement_json TEXT,
    acknowledgement_sha256 TEXT CHECK(acknowledgement_sha256 IS NULL OR length(acknowledgement_sha256) = 64),
    conflict_json TEXT,
    conflict_sha256 TEXT CHECK(conflict_sha256 IS NULL OR length(conflict_sha256) = 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(global_domain_experiment_id, binding_revision_id, event_stream, stream_generation),
    CHECK((status = 'leased') = (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK((acknowledgement_json IS NULL) = (acknowledgement_sha256 IS NULL)),
    CHECK((conflict_json IS NULL) = (conflict_sha256 IS NULL)),
    CHECK(status <> 'acknowledged' OR (acknowledgement_json IS NOT NULL AND conflict_json IS NULL)),
    CHECK(status <> 'conflict' OR (conflict_json IS NOT NULL AND acknowledgement_json IS NULL))
);
INSERT INTO molbio_ngs_outbox_events(
    id, global_domain_experiment_id, state_revision_id, binding_revision_id,
    event_type, event_stream, stream_generation, source_generation, payload_json,
    payload_sha256, status, lease_owner, lease_token, lease_expires_at,
    next_retry_at, retry_count, last_error, acknowledgement_json,
    acknowledgement_sha256, conflict_json, conflict_sha256, created_at, updated_at
)
SELECT id, global_domain_experiment_id, state_revision_id, binding_revision_id,
       event_type, event_stream, stream_generation, source_generation, payload_json,
       payload_sha256, status, lease_owner, lease_token, lease_expires_at,
       next_retry_at, retry_count, last_error, acknowledgement_json,
       acknowledgement_sha256, conflict_json, conflict_sha256, created_at, updated_at
  FROM molbio_ngs_outbox_events_v4_stage;
DROP TABLE molbio_ngs_outbox_events_v4_stage;
CREATE INDEX ix_molbio_ngs_outbox_status_created ON molbio_ngs_outbox_events(status, created_at);
CREATE UNIQUE INDEX ux_molbio_ngs_outbox_stream_generation
    ON molbio_ngs_outbox_events(global_domain_experiment_id, binding_revision_id, event_stream, stream_generation);
CREATE INDEX ix_molbio_ngs_outbox_delivery ON molbio_ngs_outbox_events(status, next_retry_at, created_at);
CREATE TRIGGER trg_molbio_ngs_outbox_digest_insert BEFORE INSERT ON molbio_ngs_outbox_events
WHEN NEW.payload_sha256 != sha256(NEW.payload_json)
BEGIN SELECT RAISE(ABORT, 'outbox event payload digest mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_evidence_digest_insert BEFORE INSERT ON molbio_ngs_outbox_events
WHEN (NEW.acknowledgement_json IS NULL) != (NEW.acknowledgement_sha256 IS NULL)
  OR (NEW.acknowledgement_json IS NOT NULL AND NEW.acknowledgement_sha256 != sha256(NEW.acknowledgement_json))
  OR (NEW.conflict_json IS NULL) != (NEW.conflict_sha256 IS NULL)
  OR (NEW.conflict_json IS NOT NULL AND NEW.conflict_sha256 != sha256(NEW.conflict_json))
BEGIN SELECT RAISE(ABORT, 'outbox evidence digest mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_evidence_digest_update BEFORE UPDATE ON molbio_ngs_outbox_events
WHEN (NEW.acknowledgement_json IS NULL) != (NEW.acknowledgement_sha256 IS NULL)
  OR (NEW.acknowledgement_json IS NOT NULL AND NEW.acknowledgement_sha256 != sha256(NEW.acknowledgement_json))
  OR (NEW.conflict_json IS NULL) != (NEW.conflict_sha256 IS NULL)
  OR (NEW.conflict_json IS NOT NULL AND NEW.conflict_sha256 != sha256(NEW.conflict_json))
BEGIN SELECT RAISE(ABORT, 'outbox evidence digest mismatch'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_payload_immutable_update BEFORE UPDATE ON molbio_ngs_outbox_events
WHEN OLD.id IS NOT NEW.id OR OLD.global_domain_experiment_id IS NOT NEW.global_domain_experiment_id
  OR OLD.state_revision_id IS NOT NEW.state_revision_id OR OLD.binding_revision_id IS NOT NEW.binding_revision_id
  OR OLD.event_type IS NOT NEW.event_type OR OLD.event_stream IS NOT NEW.event_stream
  OR OLD.stream_generation IS NOT NEW.stream_generation OR OLD.source_generation IS NOT NEW.source_generation
  OR OLD.payload_json IS NOT NEW.payload_json OR OLD.payload_sha256 IS NOT NEW.payload_sha256
  OR OLD.created_at IS NOT NEW.created_at
BEGIN SELECT RAISE(ABORT, 'outbox event payload is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_immutable_delete BEFORE DELETE ON molbio_ngs_outbox_events
BEGIN SELECT RAISE(ABORT, 'outbox event is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_ordering_required_insert BEFORE INSERT ON molbio_ngs_outbox_events
WHEN NEW.binding_revision_id IS NULL OR NEW.event_stream IS NULL OR NEW.stream_generation IS NULL OR NEW.stream_generation < 1
BEGIN SELECT RAISE(ABORT, 'ordered outbox authority is required'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_ordering_immutable
BEFORE UPDATE OF binding_revision_id, event_stream, stream_generation, source_generation ON molbio_ngs_outbox_events
BEGIN SELECT RAISE(ABORT, 'outbox ordering authority is immutable'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_lease_fence
BEFORE UPDATE OF status, acknowledgement_json, acknowledgement_sha256, conflict_json, conflict_sha256 ON molbio_ngs_outbox_events
WHEN (NEW.status = 'leased' AND (NEW.lease_owner IS NULL OR NEW.lease_token IS NULL OR NEW.lease_expires_at IS NULL))
  OR (NEW.status <> 'leased' AND (NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL))
  OR (NEW.status = 'acknowledged' AND (NEW.acknowledgement_json IS NULL OR NEW.acknowledgement_sha256 IS NULL OR NEW.conflict_json IS NOT NULL))
  OR (NEW.status = 'conflict' AND (NEW.conflict_json IS NULL OR NEW.conflict_sha256 IS NULL OR NEW.acknowledgement_json IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'invalid token-fenced outbox delivery state'); END;
CREATE TRIGGER trg_molbio_ngs_outbox_delivery_state_insert BEFORE INSERT ON molbio_ngs_outbox_events
WHEN (NEW.status = 'leased' AND (NEW.lease_owner IS NULL OR NEW.lease_token IS NULL OR NEW.lease_expires_at IS NULL))
  OR (NEW.status <> 'leased' AND (NEW.lease_owner IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL))
  OR (NEW.status = 'acknowledged' AND (NEW.acknowledgement_json IS NULL OR NEW.acknowledgement_sha256 IS NULL OR NEW.conflict_json IS NOT NULL))
  OR (NEW.status = 'conflict' AND (NEW.conflict_json IS NULL OR NEW.conflict_sha256 IS NULL OR NEW.acknowledgement_json IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'invalid outbox delivery state'); END;
'''


def migration_checksum() -> str:
    return hashlib.sha256(MIGRATION_SQL.encode("utf-8")).hexdigest()


def migration_v2_checksum() -> str:
    return hashlib.sha256(MIGRATION_V2_SQL.encode("utf-8")).hexdigest()


def migration_v3_checksum() -> str:
    return hashlib.sha256(MIGRATION_V3_SQL.encode("utf-8")).hexdigest()


def migration_v4_checksum() -> str:
    return hashlib.sha256(
        (MIGRATION_V4_SQL + MIGRATION_V4_FINALIZE_SQL).encode("utf-8")
    ).hexdigest()


def _migration_registry() -> list[tuple[int, str, str, str, str]]:
    """Return ordered immutable migrations as version, name, checksum, description, SQL."""
    return [
        (
            MIGRATION_VERSION,
            MIGRATION_NAME,
            migration_checksum(),
            "Global-keyed MolBio/NGS scientific state foundation",
            MIGRATION_SQL,
        ),
        (
            MIGRATION_V2_VERSION,
            MIGRATION_V2_NAME,
            migration_v2_checksum(),
            "Stable sample revisions and managed immutable NGS references",
            MIGRATION_V2_SQL,
        ),
        (
            MIGRATION_V3_VERSION,
            MIGRATION_V3_NAME,
            migration_v3_checksum(),
            "Immutable exact NGS evidence assessments and typed receipts",
            MIGRATION_V3_SQL,
        ),
        (
            MIGRATION_V4_VERSION,
            MIGRATION_V4_NAME,
            migration_v4_checksum(),
            "Append-only binding revisions and ordered token-fenced outbox authority",
            MIGRATION_V4_SQL + MIGRATION_V4_FINALIZE_SQL,
        ),
    ]


def _expected_ledger_rows() -> list[tuple[int, str, str]]:
    return [(version, name, checksum) for version, name, checksum, _, _ in _migration_registry()]


def _legacy_binding_revision_id(row: sqlite3.Row | tuple[object, ...]) -> str:
    seed = (
        "bms:molbio-ngs:legacy-binding-v1:"
        + ":".join(str(value) for value in (row[0], row[1], row[2], row[5], row[11]))
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _legacy_event_authority(
    connection: sqlite3.Connection, domain_id: str, event_type: str, payload: dict[str, object]
) -> tuple[str, int | None]:
    expected_schemas = {
        "molbio_ngs.domain_state.initialized": "bms.molbio-ngs.domain-state-initialized.v1",
        "molbio_ngs.domain_state.revision_saved": "bms.molbio-ngs.domain-state-revision-saved.v1",
        "molbio_ngs.sample.created": "bms.molbio-ngs.sample-created.v1",
        "molbio_ngs.sample.revision_saved": "bms.molbio-ngs.sample-revision-saved.v1",
        "molbio_ngs.reference.created": "bms.molbio-ngs.reference-created.v1",
        "molbio_ngs.reference.revision_saved": "bms.molbio-ngs.reference-revision-saved.v1",
        "molbio_ngs.reference.archived": "bms.molbio-ngs.reference-archived.v1",
        "molbio_ngs.instrument_run_evidence.attached": "bms.molbio-ngs.instrument-run-evidence-attached.v1",
        "molbio_ngs.evidence.assessed": "bms.molbio-ngs.evidence-assessed.v1",
    }
    if payload.get("schema") != expected_schemas.get(event_type):
        raise sqlite3.IntegrityError(f"v4_event_attestation_error:event_schema:{event_type}")

    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            raise sqlite3.IntegrityError(f"v4_event_attestation_error:{event_type}:{name}")
        return value

    if event_type == "molbio_ngs.domain_state.initialized":
        binding = connection.execute(
            "SELECT global_domain_experiment_revision_id, global_domain_experiment_revision_digest, project_id, project_generation, project_digest, global_experiment_id, global_experiment_generation, global_experiment_digest FROM molbio_ngs_global_bindings WHERE global_domain_experiment_id=?",
            (domain_id,),
        ).fetchone()
        expected = (
            payload.get("global_domain_experiment_revision_id"),
            payload.get("global_domain_experiment_revision_digest"),
            payload.get("project_id"), str(payload.get("project_generation")),
            payload.get("project_digest"), payload.get("global_experiment_id"),
            str(payload.get("global_experiment_generation")), payload.get("global_experiment_digest"),
        )
        if binding is None or tuple(str(value) for value in binding) != tuple(str(value) for value in expected):
            raise sqlite3.IntegrityError("v4_event_attestation_error:initialization_binding")
        return "binding", 0
    if event_type == "molbio_ngs.domain_state.revision_saved":
        revision_id = required_text("state_revision_id")
        row = connection.execute(
            "SELECT revision_number, payload_sha256, membership_graph_sha256 FROM molbio_ngs_domain_state_revisions WHERE id=? AND global_domain_experiment_id=?",
            (revision_id, domain_id),
        ).fetchone()
        if row is None or payload.get("state_revision_number") != row[0] or payload.get("payload_sha256") != row[1] or payload.get("membership_graph_sha256") != row[2]:
            raise sqlite3.IntegrityError("v4_event_attestation_error:state_revision")
        return "state", int(row[0])
    if event_type in {"molbio_ngs.sample.created", "molbio_ngs.sample.revision_saved"}:
        sample_id = required_text("sample_id")
        revision_id = required_text("sample_revision_id")
        row = connection.execute(
            "SELECT revision_number, payload_sha256 FROM molbio_ngs_sample_revisions WHERE id=? AND sample_id=? AND global_domain_experiment_id=?",
            (revision_id, sample_id, domain_id),
        ).fetchone()
        if row is None or payload.get("sample_revision_number") != row[0] or payload.get("payload_sha256") != row[1]:
            raise sqlite3.IntegrityError("v4_event_attestation_error:sample_revision")
        return f"sample:{sample_id}", int(row[0])
    if event_type in {"molbio_ngs.reference.created", "molbio_ngs.reference.revision_saved"}:
        reference_id = required_text("reference_id")
        revision_id = required_text("reference_revision_id")
        row = connection.execute(
            "SELECT revision_number, canonical_fasta_sha256 FROM molbio_ngs_reference_revisions WHERE id=? AND reference_id=? AND global_domain_experiment_id=?",
            (revision_id, reference_id, domain_id),
        ).fetchone()
        if row is None or payload.get("reference_revision_number") != row[0] or payload.get("canonical_fasta_sha256") != row[1]:
            raise sqlite3.IntegrityError("v4_event_attestation_error:reference_revision")
        return f"reference:{reference_id}", int(row[0])
    if event_type == "molbio_ngs.reference.archived":
        reference_id = required_text("reference_id")
        row = connection.execute(
            "SELECT head_generation, archived_at FROM molbio_ngs_reference_resources WHERE id=? AND global_domain_experiment_id=?",
            (reference_id, domain_id),
        ).fetchone()
        if row is None or payload.get("head_generation") != row[0] or payload.get("archived_at") != row[1]:
            raise sqlite3.IntegrityError("v4_event_attestation_error:reference_archive")
        return f"reference:{reference_id}", int(row[0])
    if event_type == "molbio_ngs.instrument_run_evidence.attached":
        run_id = required_text("run_id")
        receipt_id = required_text("receipt_id")
        row = connection.execute(
            "SELECT entity_kind, entity_id, source_generation_or_revision, content_digest FROM molbio_ngs_member_receipts WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        observed = payload.get("observed_generation")
        if row is None or row[0] != "ont_instrument_run" or row[1] != run_id or str(row[2]) != str(observed) or row[3] != payload.get("observation_sha256"):
            raise sqlite3.IntegrityError("v4_event_attestation_error:instrument_evidence")
        return f"member:ont_instrument_run:{run_id}", int(observed)
    if event_type == "molbio_ngs.evidence.assessed":
        evidence_id = required_text("evidence_id")
        row = connection.execute(
            "SELECT wrapper_sha256, scientific_assessment FROM molbio_ngs_evidence_assessments WHERE evidence_id=? AND global_domain_experiment_id=?",
            (evidence_id, domain_id),
        ).fetchone()
        if row is None or row[0] != payload.get("wrapper_sha256") or row[1] != payload.get("scientific_assessment"):
            raise sqlite3.IntegrityError("v4_event_attestation_error:evidence")
        return f"evidence:{evidence_id}", None
    raise sqlite3.IntegrityError(f"v4_event_attestation_error:unknown_event_type:{event_type}")


def _v4_upgrade_plan(connection: sqlite3.Connection) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    bindings = connection.execute(
        "SELECT global_domain_experiment_id, global_domain_experiment_revision_id, global_domain_experiment_revision_digest, project_id, project_generation, project_digest, project_receipt_id, project_reopen_destination, project_acknowledgement, global_experiment_id, global_experiment_generation, global_experiment_digest, global_experiment_receipt_id, global_experiment_reopen_destination, global_experiment_acknowledgement, last_verified_at, last_error, created_at, updated_at FROM molbio_ngs_global_bindings ORDER BY global_domain_experiment_id"
    ).fetchall()
    binding_plan = [( _legacy_binding_revision_id(row), *row) for row in bindings]
    binding_by_domain = {str(row[1]): str(row[0]) for row in binding_plan}
    events = connection.execute(
        "SELECT id, global_domain_experiment_id, event_type, payload_json, payload_sha256, status, lease_owner, lease_token, lease_expires_at, next_retry_at, retry_count, last_error, acknowledgement_json, acknowledgement_sha256, conflict_json, conflict_sha256, created_at FROM molbio_ngs_outbox_events ORDER BY created_at, id"
    ).fetchall()
    counters: dict[tuple[str, str, str], int] = {}
    event_plan: list[tuple[object, ...]] = []
    for row in events:
        if row[5] != "pending" or any(row[index] is not None for index in (6, 7, 8, 9, 11, 12, 13, 14, 15)) or row[10] != 0:
            raise sqlite3.IntegrityError("untrusted_legacy_delivery_state")
        try:
            payload = json.loads(str(row[3]))
        except json.JSONDecodeError as exc:
            raise sqlite3.IntegrityError("v4_event_attestation_error:invalid_json") from exc
        if not isinstance(payload, dict) or hashlib.sha256(str(row[3]).encode("utf-8")).hexdigest() != row[4]:
            raise sqlite3.IntegrityError("v4_event_attestation_error:payload_digest")
        domain_id = str(row[1])
        binding_id = binding_by_domain.get(domain_id)
        if binding_id is None:
            raise sqlite3.IntegrityError("v4_event_attestation_error:missing_binding")
        stream, source_generation = _legacy_event_authority(connection, domain_id, str(row[2]), payload)
        key = (domain_id, binding_id, stream)
        generation = counters.get(key, 0) + 1
        counters[key] = generation
        event_plan.append((str(row[0]), binding_id, stream, generation, source_generation))
    return binding_plan, event_plan


def _execute_transactional_script(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                connection.execute(sql)
    if statement.strip():
        raise sqlite3.IntegrityError("incomplete migration SQL statement")


def _materialize_v4_upgrade(
    connection: sqlite3.Connection,
    bindings: list[tuple[object, ...]],
    events: list[tuple[object, ...]],
) -> None:
    _execute_transactional_script(connection, MIGRATION_V4_SQL)
    for item in bindings:
        binding_id, domain_id, domain_revision_id, domain_digest, project_id, project_generation, project_digest, project_receipt_id, project_reopen, project_ack, global_id, global_generation, global_digest, global_receipt_id, global_reopen, global_ack, last_verified, last_error, created_at, updated_at = item
        connection.execute(
            "INSERT INTO molbio_ngs_global_binding_revisions(binding_revision_id, global_domain_experiment_id, revision_number, supersedes_binding_revision_id, global_domain_experiment_revision_id, global_domain_experiment_revision_digest, project_id, project_generation, project_digest, project_receipt_id, project_reopen_destination, project_acknowledgement, global_experiment_id, global_experiment_generation, global_experiment_digest, global_experiment_receipt_id, global_experiment_reopen_destination, global_experiment_acknowledgement, global_binding_receipt_id, global_binding_receipt_json, global_binding_receipt_sha256, connector_command_id, binding_state, last_verified_at, last_error, created_at, updated_at) VALUES (?,?,1,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,'needs_reverification',?,?,?,?)",
            (binding_id, domain_id, domain_revision_id, domain_digest, project_id, project_generation, project_digest, project_receipt_id, project_reopen, project_ack, global_id, global_generation, global_digest, global_receipt_id, global_reopen, global_ack, last_verified, last_error, created_at, updated_at),
        )
        connection.execute("UPDATE molbio_ngs_domain_states SET current_binding_revision_id=? WHERE global_domain_experiment_id=?", (binding_id, domain_id))
        connection.execute("UPDATE molbio_ngs_domain_state_revisions SET binding_revision_id=? WHERE global_domain_experiment_id=?", (binding_id, domain_id))
    for event_id, binding_id, stream, generation, source_generation in events:
        connection.execute("UPDATE molbio_ngs_outbox_events SET binding_revision_id=?, event_stream=?, stream_generation=?, source_generation=? WHERE id=?", (binding_id, stream, generation, source_generation, event_id))
    connection.execute(
        "INSERT INTO molbio_ngs_outbox_streams(global_domain_experiment_id, binding_revision_id, event_stream, next_stream_generation, updated_at) SELECT global_domain_experiment_id, binding_revision_id, event_stream, max(stream_generation)+1, max(updated_at) FROM molbio_ngs_outbox_events GROUP BY global_domain_experiment_id, binding_revision_id, event_stream"
    )
    _execute_transactional_script(connection, MIGRATION_V4_FINALIZE_SQL)


def _v4_materialized_mapping(connection: sqlite3.Connection) -> bytes:
    mapping = {
        "bindings": connection.execute(
            "SELECT global_domain_experiment_id, binding_revision_id, revision_number, binding_state FROM molbio_ngs_global_binding_revisions ORDER BY global_domain_experiment_id, revision_number"
        ).fetchall(),
        "events": connection.execute(
            "SELECT id, global_domain_experiment_id, binding_revision_id, event_stream, stream_generation, source_generation FROM molbio_ngs_outbox_events ORDER BY id"
        ).fetchall(),
        "streams": connection.execute(
            "SELECT global_domain_experiment_id, binding_revision_id, event_stream, next_stream_generation FROM molbio_ngs_outbox_streams ORDER BY global_domain_experiment_id, binding_revision_id, event_stream"
        ).fetchall(),
    }
    return json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _v4_rolled_back_dry_run(connection: sqlite3.Connection) -> bytes:
    connection.execute("BEGIN IMMEDIATE")
    try:
        bindings, events = _v4_upgrade_plan(connection)
        _materialize_v4_upgrade(connection, bindings, events)
        return _v4_materialized_mapping(connection)
    finally:
        connection.rollback()


def _apply_v4_upgrade(connection: sqlite3.Connection) -> None:
    first = _v4_rolled_back_dry_run(connection)
    second = _v4_rolled_back_dry_run(connection)
    if first != second:
        raise sqlite3.IntegrityError("v4_nondeterministic_rolled_back_mapping")
    try:
        connection.execute("BEGIN IMMEDIATE")
        bindings, events = _v4_upgrade_plan(connection)
        _materialize_v4_upgrade(connection, bindings, events)
        if _v4_materialized_mapping(connection) != first:
            raise sqlite3.IntegrityError("v4_committing_mapping_diverged_from_dry_run")
        checksum = migration_v4_checksum()
        connection.execute(
            "INSERT INTO molbio_ngs_schema_migrations(version,name,checksum,description,applied_at) VALUES (?,?,?,?,?)",
            (MIGRATION_V4_VERSION, MIGRATION_V4_NAME, checksum, "Append-only binding revisions and ordered token-fenced outbox authority", datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"file:{quote(str(path), safe='/')}?mode=ro", uri=True, timeout=30
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
    register_sqlite_sha256(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _normalize_schema_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).rstrip(";")


def _schema_objects(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    objects: dict[str, dict[str, str]] = {}
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
          FROM sqlite_master
         WHERE type IN ('table', 'index', 'trigger')
           AND name NOT LIKE 'sqlite_%'
         ORDER BY type, name
        """
    ).fetchall()
    for object_type, name, table_name, sql in rows:
        normalized = _normalize_schema_sql(str(sql or ""))
        objects[f"{object_type}:{name}"] = {
            "type": str(object_type),
            "name": str(name),
            "table": str(table_name),
            "sql_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        }
    return objects


def _expected_schema_objects() -> dict[str, dict[str, str]]:
    connection = sqlite3.connect(":memory:")
    register_sqlite_sha256(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(LEDGER_SQL)
        for _version, _name, _checksum, _description, sql in _migration_registry():
            connection.executescript(sql)
        return _schema_objects(connection)
    finally:
        connection.close()


def _authority_coherence_errors(connection: sqlite3.Connection) -> list[dict[str, object]]:
    checks = (
        (
            "domain_state_current_binding",
            """
            SELECT state.global_domain_experiment_id, state.current_binding_revision_id
              FROM molbio_ngs_domain_states AS state
              LEFT JOIN molbio_ngs_global_binding_revisions AS binding
                ON binding.binding_revision_id = state.current_binding_revision_id
               AND binding.global_domain_experiment_id = state.global_domain_experiment_id
             WHERE state.current_binding_revision_id IS NULL OR binding.binding_revision_id IS NULL
             ORDER BY state.global_domain_experiment_id
            """,
        ),
        (
            "state_revision_binding",
            """
            SELECT revision.id, revision.binding_revision_id
              FROM molbio_ngs_domain_state_revisions AS revision
              LEFT JOIN molbio_ngs_global_binding_revisions AS binding
                ON binding.binding_revision_id = revision.binding_revision_id
               AND binding.global_domain_experiment_id = revision.global_domain_experiment_id
             WHERE revision.binding_revision_id IS NULL OR binding.binding_revision_id IS NULL
             ORDER BY revision.id
            """,
        ),
        (
            "ordered_outbox_binding",
            """
            SELECT event.id, event.binding_revision_id, event.event_stream, event.stream_generation
              FROM molbio_ngs_outbox_events AS event
              LEFT JOIN molbio_ngs_global_binding_revisions AS binding
                ON binding.binding_revision_id = event.binding_revision_id
               AND binding.global_domain_experiment_id = event.global_domain_experiment_id
             WHERE binding.binding_revision_id IS NULL
                OR event.event_stream IS NULL OR event.stream_generation IS NULL
                OR event.stream_generation < 1
             ORDER BY event.id
            """,
        ),
        (
            "domain_state_current_revision",
            """
            SELECT state.global_domain_experiment_id, state.current_state_revision_id,
                   state.head_generation
              FROM molbio_ngs_domain_states AS state
              LEFT JOIN molbio_ngs_domain_state_revisions AS revision
                ON revision.id = state.current_state_revision_id
               AND revision.global_domain_experiment_id = state.global_domain_experiment_id
               AND revision.revision_number = state.head_generation
             WHERE (state.current_state_revision_id IS NULL AND state.head_generation != 0)
                OR (state.current_state_revision_id IS NOT NULL AND revision.id IS NULL)
             ORDER BY state.global_domain_experiment_id
            """,
        ),
        (
            "sample_current_revision",
            """
            SELECT sample.id, sample.current_revision_id, sample.head_generation
              FROM molbio_ngs_samples AS sample
              LEFT JOIN molbio_ngs_sample_revisions AS revision
                ON revision.id = sample.current_revision_id
               AND revision.sample_id = sample.id
               AND revision.global_domain_experiment_id = sample.global_domain_experiment_id
               AND revision.revision_number = sample.head_generation
             WHERE (sample.current_revision_id IS NULL AND sample.head_generation != 0)
                OR (sample.current_revision_id IS NOT NULL AND revision.id IS NULL)
             ORDER BY sample.id
            """,
        ),
        (
            "reference_current_revision",
            """
            SELECT resource.id, resource.current_revision_id, resource.head_generation
              FROM molbio_ngs_reference_resources AS resource
              LEFT JOIN molbio_ngs_reference_revisions AS revision
                ON revision.id = resource.current_revision_id
               AND revision.reference_id = resource.id
               AND revision.global_domain_experiment_id = resource.global_domain_experiment_id
               AND revision.revision_number = resource.head_generation
             WHERE (resource.current_revision_id IS NULL AND resource.head_generation != 0)
                OR (resource.current_revision_id IS NOT NULL AND revision.id IS NULL)
             ORDER BY resource.id
            """,
        ),
    )
    errors: list[dict[str, object]] = []
    for check, sql in checks:
        try:
            rows = connection.execute(sql).fetchall()
        except sqlite3.Error as exc:
            errors.append({"check": check, "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        errors.extend({"check": check, "row": list(row)} for row in rows)
    return errors


def _managed_artifact_inventory(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        {
            "artifact_id": str(artifact_id),
            "managed_relative_path": str(relative),
            "size_bytes": int(size_bytes),
            "sha256": str(sha256),
        }
        for artifact_id, relative, size_bytes, sha256 in connection.execute(
            """
            SELECT id, managed_relative_path, size_bytes, sha256
              FROM molbio_ngs_reference_artifacts
             ORDER BY id
            """
        )
    ]


def _validate_managed_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("managed reference artifact path is not canonical and relative")
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("managed reference artifact path is not canonical and relative")
    return relative


def _artifact_errors(
    connection: sqlite3.Connection, artifact_root: Path | None = None
) -> list[dict[str, object]]:
    try:
        inventory = _managed_artifact_inventory(connection)
    except sqlite3.Error as exc:
        return [{"error": f"{exc.__class__.__name__}: {exc}"}]
    root = (artifact_root or get_molbio_ngs_reference_root()).expanduser().absolute()
    errors: list[dict[str, object]] = []
    for item in inventory:
        try:
            relative = _validate_managed_relative_path(item["managed_relative_path"])
            candidate = root / relative
            _assert_regular_file(candidate)
            size = os.lstat(candidate).st_size
            if size != item["size_bytes"]:
                raise ValueError(
                    f"size mismatch: expected {item['size_bytes']}, observed {size}"
                )
            digest = _sha256_file(candidate)
            if digest != item["sha256"]:
                raise ValueError(
                    f"digest mismatch: expected {item['sha256']}, observed {digest}"
                )
        except (OSError, ValueError) as exc:
            errors.append(
                {
                    "artifact_id": item["artifact_id"],
                    "managed_relative_path": item["managed_relative_path"],
                    "error": str(exc),
                }
            )
    return errors


def attest_schema(
    connection: sqlite3.Connection, *, artifact_root: Path | None = None
) -> dict[str, object]:
    """Attest the exact migration ledger and every user schema object's SQL."""

    expected_objects = _expected_schema_objects()
    actual_objects = _schema_objects(connection)
    expected_keys = set(expected_objects)
    actual_keys = set(actual_objects)
    missing_objects = [expected_objects[key] for key in sorted(expected_keys - actual_keys)]
    extra_objects = [actual_objects[key] for key in sorted(actual_keys - expected_keys)]
    changed_objects = [
        {
            "type": expected_objects[key]["type"],
            "name": expected_objects[key]["name"],
            "table": actual_objects[key]["table"],
            "expected_sql_sha256": expected_objects[key]["sql_sha256"],
            "actual_sql_sha256": actual_objects[key]["sql_sha256"],
        }
        for key in sorted(expected_keys & actual_keys)
        if expected_objects[key] != actual_objects[key]
    ]
    try:
        ledger_rows = connection.execute(
            "SELECT version, name, checksum FROM molbio_ngs_schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        ledger_rows = []
        ledger_error = f"{exc.__class__.__name__}: {exc}"
    else:
        ledger_error = None
    expected_ledger = _expected_ledger_rows()
    foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    authority_coherence_errors = _authority_coherence_errors(connection)
    artifact_errors = _artifact_errors(connection, artifact_root)
    ledger_matches = ledger_rows == expected_ledger and ledger_error is None
    return {
        "ok": not (
            missing_objects
            or extra_objects
            or changed_objects
            or foreign_key_errors
            or authority_coherence_errors
            or artifact_errors
            or not ledger_matches
        ),
        "expected_migration_ledger": [list(row) for row in expected_ledger],
        "actual_migration_ledger": [list(row) for row in ledger_rows],
        "migration_ledger_error": ledger_error,
        "missing_objects": missing_objects,
        "extra_objects": extra_objects,
        "changed_objects": changed_objects,
        "foreign_key_errors": foreign_key_errors,
        "authority_coherence_errors": authority_coherence_errors,
        "artifact_errors": artifact_errors,
    }


def run_all(db_path: str | Path) -> None:
    path = Path(db_path).expanduser().resolve()
    connection = _connect(path)
    try:
        connection.execute(LEDGER_SQL)
        rows = connection.execute(
            "SELECT version, name, checksum FROM molbio_ngs_schema_migrations ORDER BY version"
        ).fetchall()
        registry = _migration_registry()
        expected_rows = _expected_ledger_rows()
        if rows != expected_rows[: len(rows)]:
            raise RuntimeError(f"MolBio/NGS migration ledger mismatch: {rows!r}")
        for version, name, checksum, description, migration_sql in registry[len(rows) :]:
            if version == MIGRATION_V4_VERSION:
                _apply_v4_upgrade(connection)
                continue
            applied_at = datetime.now(timezone.utc).isoformat()
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + migration_sql
                + "\nINSERT INTO molbio_ngs_schema_migrations("
                "version, name, checksum, description, applied_at) VALUES ("
                f"{version}, {_sql_literal(name)}, {_sql_literal(checksum)}, "
                f"{_sql_literal(description)}, {_sql_literal(applied_at)});\nCOMMIT;"
            )
        rows = connection.execute(
            "SELECT version, name, checksum FROM molbio_ngs_schema_migrations ORDER BY version"
        ).fetchall()
        if rows != expected_rows:
            raise RuntimeError(f"MolBio/NGS migration ledger mismatch: {rows!r}")
        attestation = attest_schema(connection)
        if not attestation["ok"]:
            raise sqlite3.IntegrityError(f"MolBio/NGS schema attestation failed: {attestation!r}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def health(db_path: str | Path) -> dict[str, object]:
    """Return fail-closed health details; malformed databases never escape the probe."""

    path = Path(db_path).expanduser().resolve()
    base: dict[str, object] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return {
            **base,
            "status": "error",
            "error": "database does not exist",
            "attestation": {"ok": False, "error": "database does not exist"},
            "migration": None,
        }
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(path, read_only=True)
        attestation = attest_schema(connection)
        migration = connection.execute(
            "SELECT version, name, checksum, description, applied_at "
            "FROM molbio_ngs_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return {
            **base,
            "status": "healthy" if attestation["ok"] else "degraded",
            "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
            "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
            "attestation": attestation,
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
    except Exception as exc:  # noqa: BLE001 - health must degrade, not crash callers.
        return {
            **base,
            "status": "error",
            "error": f"{exc.__class__.__name__}: {exc}",
            "attestation": {
                "ok": False,
                "error": f"{exc.__class__.__name__}: {exc}",
            },
            "migration": None,
        }
    finally:
        if connection is not None:
            connection.close()


def _absolute_unresolved(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def _assert_no_symlinks(path: Path, *, allow_missing_leaf: bool) -> None:
    absolute = _absolute_unresolved(path)
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if allow_missing_leaf:
                return
            raise
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"symlink paths are forbidden: {current}")
        if index == len(parts) - 1:
            return


def _assert_regular_file(path: Path) -> None:
    _assert_no_symlinks(path, allow_missing_leaf=False)
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"regular file required: {path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _verify_database(
    connection: sqlite3.Connection, *, artifact_root: Path | None = None
) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise sqlite3.IntegrityError(f"SQLite integrity_check failed: {integrity!r}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise sqlite3.IntegrityError(
            f"MolBio/NGS foreign-key violations: {foreign_key_errors!r}"
        )
    attestation = attest_schema(connection, artifact_root=artifact_root)
    if not attestation["ok"]:
        raise sqlite3.IntegrityError(f"MolBio/NGS schema attestation failed: {attestation!r}")


def _group_counts(
    connection: sqlite3.Connection, table: str, field: str
) -> dict[str, int]:
    return {
        str(key): int(count)
        for key, count in connection.execute(
            f'SELECT "{field}", count(*) FROM "{table}" GROUP BY "{field}" ORDER BY "{field}"'
        )
    }


def _database_inventory(connection: sqlite3.Connection) -> dict[str, object]:
    ledger = [
        {"version": int(version), "name": str(name), "checksum": str(checksum)}
        for version, name, checksum in connection.execute(
            "SELECT version, name, checksum FROM molbio_ngs_schema_migrations ORDER BY version"
        )
    ]
    receipt_count = int(
        connection.execute("SELECT count(*) FROM molbio_ngs_member_receipts").fetchone()[0]
    )
    outbox_count = int(
        connection.execute("SELECT count(*) FROM molbio_ngs_outbox_events").fetchone()[0]
    )
    artifact_inventory = _managed_artifact_inventory(connection)
    return {
        "schema_version": LATEST_MIGRATION_VERSION,
        "migration_ledger": ledger,
        "member_receipts": {
            "count": receipt_count,
            "by_kind": _group_counts(connection, "molbio_ngs_member_receipts", "entity_kind"),
        },
        "outbox_events": {
            "count": outbox_count,
            "by_status": _group_counts(connection, "molbio_ngs_outbox_events", "status"),
        },
        "managed_reference_artifacts": artifact_inventory,
    }


def _manifest_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.manifest.json")


def _artifact_bundle_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.artifacts")


def _copy_verified_artifact(
    source: Path, destination: Path, item: dict[str, object]
) -> None:
    _assert_regular_file(source)
    if os.lstat(source).st_size != item["size_bytes"]:
        raise ValueError(f"managed reference artifact size mismatch: {item['artifact_id']}")
    if _sha256_file(source) != item["sha256"]:
        raise ValueError(f"managed reference artifact digest mismatch: {item['artifact_id']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(destination.parent, allow_missing_leaf=False)
    _assert_no_symlinks(destination, allow_missing_leaf=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(source_descriptor, "rb") as source_handle, os.fdopen(
            descriptor, "wb"
        ) as destination_handle:
            source_descriptor = -1
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                destination_handle.write(chunk)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        if (
            os.lstat(temporary).st_size != item["size_bytes"]
            or _sha256_file(temporary) != item["sha256"]
        ):
            raise ValueError(f"copied managed reference artifact mismatch: {item['artifact_id']}")
        os.replace(temporary, destination)
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        temporary.unlink(missing_ok=True)


def _bundle_metadata(
    artifact_bundle: Path, inventory: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "directory_name": artifact_bundle.name,
        "count": len(inventory),
        "size_bytes": sum(int(item["size_bytes"]) for item in inventory),
        "inventory_sha256": hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _atomic_publish_bytes(path: Path, payload: bytes) -> None:
    _assert_no_symlinks(path, allow_missing_leaf=True)
    if path.exists():
        raise FileExistsError(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_no_symlinks(path, allow_missing_leaf=True)
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def backup_database(source_path: str | Path, destination_path: str | Path) -> dict[str, object]:
    """Create and attest a database backup and digest-bound managed-artifact bundle."""

    source = _absolute_unresolved(source_path)
    destination = _absolute_unresolved(destination_path)
    manifest_path = _manifest_path(destination)
    artifact_bundle = _artifact_bundle_path(destination)
    _assert_regular_file(source)
    _assert_no_symlinks(destination, allow_missing_leaf=True)
    _assert_no_symlinks(manifest_path, allow_missing_leaf=True)
    _assert_no_symlinks(artifact_bundle, allow_missing_leaf=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(destination.parent, allow_missing_leaf=False)
    if destination.exists() or manifest_path.exists() or artifact_bundle.exists():
        raise FileExistsError(
            "backup, artifact bundle, and manifest destinations must not already exist"
        )
    source_health = health(source)
    if source_health.get("status") != "healthy":
        raise sqlite3.IntegrityError(
            f"source MolBio/NGS database is not healthy: {source_health!r}"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".backup", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary_bundle = Path(
        tempfile.mkdtemp(prefix=f".{artifact_bundle.name}.", dir=destination.parent)
    )
    published = False
    bundle_published = False
    try:
        source_connection = _connect(source, read_only=True)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
        os.chmod(temporary, 0o600)
        verification = _connect(temporary, read_only=True)
        try:
            _verify_database(verification)
            inventory = _database_inventory(verification)
        finally:
            verification.close()
        artifact_inventory = inventory["managed_reference_artifacts"]
        if not isinstance(artifact_inventory, list):
            raise sqlite3.IntegrityError("managed reference artifact inventory is invalid")
        reference_root = get_molbio_ngs_reference_root().expanduser().absolute()
        for item in artifact_inventory:
            if not isinstance(item, dict):
                raise sqlite3.IntegrityError(
                    "managed reference artifact inventory row is invalid"
                )
            relative = _validate_managed_relative_path(item["managed_relative_path"])
            _copy_verified_artifact(
                reference_root / relative,
                temporary_bundle / relative,
                item,
            )
        backup_size = os.lstat(temporary).st_size
        backup_sha256 = _sha256_file(temporary)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(temporary, flags)
        try:
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        _assert_no_symlinks(artifact_bundle, allow_missing_leaf=True)
        os.replace(temporary_bundle, artifact_bundle)
        bundle_published = True
        _assert_no_symlinks(destination, allow_missing_leaf=True)
        os.link(temporary, destination)
        published = True
        manifest: dict[str, object] = {
            "schema": BACKUP_MANIFEST_SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backup_filename": destination.name,
            "backup_sha256": backup_sha256,
            "backup_size_bytes": int(backup_size),
            **inventory,
            "artifact_bundle": _bundle_metadata(artifact_bundle, artifact_inventory),
        }
        _atomic_publish_bytes(
            manifest_path,
            (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            ),
        )
        return manifest
    except Exception:
        if published:
            destination.unlink(missing_ok=True)
        if bundle_published:
            shutil.rmtree(artifact_bundle, ignore_errors=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(temporary_bundle, ignore_errors=True)


_MANIFEST_KEYS = {
    "schema",
    "created_at",
    "backup_filename",
    "backup_sha256",
    "backup_size_bytes",
    "schema_version",
    "migration_ledger",
    "member_receipts",
    "outbox_events",
    "managed_reference_artifacts",
    "artifact_bundle",
}


def _load_manifest(path: Path) -> dict[str, object]:
    _assert_regular_file(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    finally:
        os.close(descriptor)
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("backup manifest schema fields are invalid")
    if manifest.get("schema") != BACKUP_MANIFEST_SCHEMA:
        raise ValueError("backup manifest schema is unsupported")
    if manifest.get("schema_version") != LATEST_MIGRATION_VERSION:
        raise ValueError("backup manifest schema version is unsupported")
    digest = manifest.get("backup_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("backup manifest digest is invalid")
    size = manifest.get("backup_size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("backup manifest size is invalid")
    artifact_inventory = manifest.get("managed_reference_artifacts")
    if not isinstance(artifact_inventory, list):
        raise ValueError("backup manifest artifact inventory is invalid")
    for item in artifact_inventory:
        if not isinstance(item, dict) or set(item) != {
            "artifact_id",
            "managed_relative_path",
            "size_bytes",
            "sha256",
        }:
            raise ValueError("backup manifest artifact inventory row is invalid")
        _validate_managed_relative_path(item["managed_relative_path"])
        if not isinstance(item["artifact_id"], str) or not item["artifact_id"]:
            raise ValueError("backup manifest artifact ID is invalid")
        if (
            not isinstance(item["size_bytes"], int)
            or isinstance(item["size_bytes"], bool)
            or item["size_bytes"] < 0
        ):
            raise ValueError("backup manifest artifact size is invalid")
        if not isinstance(item["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", item["sha256"]
        ):
            raise ValueError("backup manifest artifact digest is invalid")
    artifact_bundle = manifest.get("artifact_bundle")
    if not isinstance(artifact_bundle, dict) or set(artifact_bundle) != {
        "directory_name",
        "count",
        "size_bytes",
        "inventory_sha256",
    }:
        raise ValueError("backup manifest artifact bundle is invalid")
    expected_bundle = _bundle_metadata(
        Path(str(artifact_bundle.get("directory_name"))), artifact_inventory
    )
    if artifact_bundle != expected_bundle:
        raise ValueError("backup manifest artifact bundle metadata mismatch")
    return manifest


def _validate_artifact_bundle(
    artifact_bundle: Path, inventory: list[dict[str, object]]
) -> None:
    _assert_no_symlinks(artifact_bundle, allow_missing_leaf=False)
    if not stat.S_ISDIR(os.lstat(artifact_bundle).st_mode):
        raise ValueError("backup artifact bundle must be a directory")
    expected_paths: set[str] = set()
    for item in inventory:
        relative = _validate_managed_relative_path(item["managed_relative_path"])
        expected_paths.add(relative.as_posix())
        candidate = artifact_bundle / relative
        _assert_regular_file(candidate)
        if os.lstat(candidate).st_size != item["size_bytes"]:
            raise ValueError(f"backup artifact bundle size mismatch: {item['artifact_id']}")
        if _sha256_file(candidate) != item["sha256"]:
            raise ValueError(f"backup artifact bundle digest mismatch: {item['artifact_id']}")
    observed_paths: set[str] = set()
    for directory, directory_names, file_names in os.walk(artifact_bundle, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            candidate = directory_path / name
            if stat.S_ISLNK(os.lstat(candidate).st_mode):
                raise ValueError(f"backup artifact bundle contains a symlink: {candidate}")
        for name in file_names:
            candidate = directory_path / name
            _assert_regular_file(candidate)
            observed_paths.add(candidate.relative_to(artifact_bundle).as_posix())
    if observed_paths != expected_paths:
        raise ValueError("backup artifact bundle file inventory mismatch")
    metadata = _bundle_metadata(artifact_bundle, inventory)
    if metadata["size_bytes"] != sum(
        os.lstat(artifact_bundle / _validate_managed_relative_path(item["managed_relative_path"])).st_size
        for item in inventory
    ):
        raise ValueError("backup artifact bundle total size mismatch")


def _receipt_reconciliation_inventory(connection: sqlite3.Connection) -> dict[str, object]:
    receipts = []
    for row in connection.execute(
        """
        SELECT receipt_id, source_store_id, entity_kind, entity_id,
               availability, reopen_destination
          FROM molbio_ngs_member_receipts
         ORDER BY receipt_id
        """
    ):
        try:
            reopen_destination = json.loads(str(row[5]))
        except json.JSONDecodeError as exc:
            raise sqlite3.IntegrityError(
                f"invalid receipt reopen destination for {row[0]}"
            ) from exc
        receipts.append(
            {
                "receipt_id": str(row[0]),
                "source_store_id": str(row[1]),
                "entity_kind": str(row[2]),
                "entity_id": str(row[3]),
                "availability": str(row[4]),
                "reopen_destination": reopen_destination,
            }
        )
    return {
        "mode": "inventory_only_no_receipt_rewrites",
        "count": len(receipts),
        "by_availability": _group_counts(
            connection, "molbio_ngs_member_receipts", "availability"
        ),
        "receipts": receipts,
    }


_RESTORE_LOCK_SCHEMA = "bms.molbio-ngs.restore-lock.v1"
_RESTORE_JOURNAL_SCHEMA = "bms.molbio-ngs.restore-journal.v1"
_RESTORE_JOURNAL_KEYS = {
    "schema",
    "operation_id",
    "target_path",
    "backup_sha256",
    "target_existed",
    "target_original_sha256",
    "target_original_size_bytes",
    "database_staging_path",
    "database_rollback_path",
    "reference_root",
    "artifact_staging_path",
    "artifact_rollback_path",
    "artifact_inventory",
    "phase",
}
_RESTORE_ARTIFACT_KEYS = {
    "artifact_id",
    "managed_relative_path",
    "size_bytes",
    "sha256",
    "destination_existed",
    "destination_sha256",
    "destination_size_bytes",
}


def _path_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree_directories(root: Path) -> None:
    directories = [Path(directory) for directory, _, _ in os.walk(root, followlinks=False)]
    for directory in reversed(directories):
        _fsync_directory(directory)


def _unlink_regular_file(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"restore authority path is not a regular file: {path}")
    os.unlink(path)
    _fsync_directory(path.parent)


def _remove_tree_strict(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"restore authority path is not a directory: {path}")
    for directory, directory_names, file_names in os.walk(path, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            candidate = directory_path / name
            if not stat.S_ISDIR(os.lstat(candidate).st_mode):
                raise ValueError(f"restore authority tree contains a non-directory: {candidate}")
        for name in file_names:
            candidate = directory_path / name
            if not stat.S_ISREG(os.lstat(candidate).st_mode):
                raise ValueError(f"restore authority tree contains a non-regular file: {candidate}")
    shutil.rmtree(path)
    _fsync_directory(path.parent)


def _canonical_record_bytes(record: dict[str, object]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _publish_exclusive_record(path: Path, record: dict[str, object]) -> None:
    _assert_no_symlinks(path, allow_missing_leaf=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_record_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
        _assert_no_symlinks(path, allow_missing_leaf=True)
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        _unlink_regular_file(temporary)


def _atomic_replace_record(path: Path, record: dict[str, object]) -> None:
    _assert_regular_file(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_record_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
        _assert_regular_file(path)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        _unlink_regular_file(temporary)


def _restore_lock_path(target: Path) -> Path:
    return Path(f"{target}.restore.lock")


def _restore_journal_path(target: Path) -> Path:
    return Path(f"{target}.restore-journal.json")


def _target_sidecars(target: Path) -> tuple[Path, Path]:
    return Path(f"{target}-wal"), Path(f"{target}-shm")


def _require_offline_target(target: Path) -> None:
    if target.name.endswith(("-wal", "-shm")):
        raise ValueError("restore target must not be a SQLite -wal or -shm sidecar")
    present = [str(path) for path in _target_sidecars(target) if _path_exists(path)]
    if present:
        raise ValueError(f"offline restore refuses existing SQLite sidecars: {present!r}")


def _remove_sqlite_sidecars(target: Path) -> None:
    for sidecar in _target_sidecars(target):
        _unlink_regular_file(sidecar)


def _admit_existing_target(target: Path) -> tuple[sqlite3.Connection | None, tuple[int, int] | None]:
    if not _path_exists(target):
        return None, None
    _assert_regular_file(target)
    metadata = os.lstat(target)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(target), safe='/')}?mode=rw", uri=True, timeout=0
        )
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as exc:
        if connection is not None:
            connection.close()
        raise RuntimeError("restore target is busy; exclusive SQLite admission failed") from exc
    return connection, (metadata.st_dev, metadata.st_ino)


def _recheck_admitted_target(target: Path, identity: tuple[int, int] | None) -> None:
    _assert_no_symlinks(target.parent, allow_missing_leaf=False)
    if identity is None:
        if _path_exists(target):
            raise RuntimeError("restore target appeared after exclusive admission")
    else:
        _assert_regular_file(target)
        metadata = os.lstat(target)
        if (metadata.st_dev, metadata.st_ino) != identity:
            raise RuntimeError("restore target changed after exclusive admission")
    _require_offline_target(target)


def _valid_restore_phase(phase: object, artifact_count: int) -> bool:
    if phase in {
        "prepared",
        "database_rollback_pending",
        "database_install_pending",
        "database_installed",
        "verified",
        "rolling_back",
        "complete",
    }:
        return True
    if not isinstance(phase, str):
        return False
    match = re.fullmatch(r"artifact:(\d+):(rollback_pending|install_pending|installed)", phase)
    return bool(match and int(match.group(1)) < artifact_count)


def _validate_restore_journal(target: Path, journal: dict[str, object]) -> dict[str, object]:
    if set(journal) != _RESTORE_JOURNAL_KEYS or journal.get("schema") != _RESTORE_JOURNAL_SCHEMA:
        raise ValueError("restore journal schema fields are invalid")
    operation_id = journal.get("operation_id")
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("restore journal operation ID is invalid")
    if journal.get("target_path") != str(target):
        raise ValueError("restore journal target binding is invalid")
    backup_sha256 = journal.get("backup_sha256")
    if not isinstance(backup_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", backup_sha256):
        raise ValueError("restore journal backup digest is invalid")
    if not isinstance(journal.get("target_existed"), bool):
        raise ValueError("restore journal target-existence binding is invalid")
    target_existed = journal["target_existed"]
    target_original_sha256 = journal.get("target_original_sha256")
    target_original_size = journal.get("target_original_size_bytes")
    if target_existed:
        if (
            not isinstance(target_original_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", target_original_sha256)
            or not isinstance(target_original_size, int)
            or isinstance(target_original_size, bool)
            or target_original_size < 0
        ):
            raise ValueError("restore journal original-target authority is invalid")
    elif target_original_sha256 is not None or target_original_size is not None:
        raise ValueError("restore journal original-target absence is invalid")

    reference_root = get_molbio_ngs_reference_root().expanduser().absolute()
    expected_paths = {
        "database_staging_path": target.parent / f".{target.name}.restore-{operation_id}.db",
        "database_rollback_path": target.parent / f".{target.name}.restore-rollback-{operation_id}.db",
        "reference_root": reference_root,
        "artifact_staging_path": reference_root.parent / f".molbio-ngs-artifact-restore-{operation_id}",
        "artifact_rollback_path": reference_root.parent / f".molbio-ngs-artifact-rollback-{operation_id}",
    }
    for key, expected in expected_paths.items():
        if journal.get(key) != str(expected):
            raise ValueError(f"restore journal {key} binding is invalid")
        _assert_no_symlinks(expected, allow_missing_leaf=True)

    inventory = journal.get("artifact_inventory")
    if not isinstance(inventory, list):
        raise ValueError("restore journal artifact inventory is invalid")
    observed_paths: set[str] = set()
    observed_ids: set[str] = set()
    for item in inventory:
        if not isinstance(item, dict) or set(item) != _RESTORE_ARTIFACT_KEYS:
            raise ValueError("restore journal artifact inventory row is invalid")
        relative = _validate_managed_relative_path(item["managed_relative_path"])
        artifact_id = item["artifact_id"]
        size = item["size_bytes"]
        digest = item["sha256"]
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or artifact_id in observed_ids
            or relative.as_posix() in observed_paths
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(item["destination_existed"], bool)
        ):
            raise ValueError("restore journal artifact inventory authority is invalid")
        destination_sha256 = item["destination_sha256"]
        destination_size = item["destination_size_bytes"]
        if item["destination_existed"]:
            if (
                not isinstance(destination_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", destination_sha256)
                or not isinstance(destination_size, int)
                or isinstance(destination_size, bool)
                or destination_size < 0
            ):
                raise ValueError("restore journal original-artifact authority is invalid")
        elif destination_sha256 is not None or destination_size is not None:
            raise ValueError("restore journal original-artifact absence is invalid")
        observed_ids.add(artifact_id)
        observed_paths.add(relative.as_posix())
    if not _valid_restore_phase(journal.get("phase"), len(inventory)):
        raise ValueError("restore journal phase is invalid")
    return journal


def _load_restore_journal(target: Path, journal_path: Path) -> dict[str, object]:
    _assert_regular_file(journal_path)
    if os.lstat(journal_path).st_size > 1024 * 1024:
        raise ValueError("restore journal is oversized")
    descriptor = os.open(journal_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    finally:
        os.close(descriptor)
    try:
        journal = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("restore journal is not valid JSON") from exc
    if not isinstance(journal, dict):
        raise ValueError("restore journal must be an object")
    return _validate_restore_journal(target, journal)


def _journal_phase(journal_path: Path, journal: dict[str, object], phase: str) -> None:
    inventory = journal["artifact_inventory"]
    if not isinstance(inventory, list):
        raise ValueError("restore journal artifact inventory is invalid")
    if not _valid_restore_phase(phase, len(inventory)):
        raise ValueError(f"invalid restore journal phase: {phase}")
    journal["phase"] = phase
    _atomic_replace_record(journal_path, journal)


def _cleanup_restore_work(journal: dict[str, object]) -> None:
    database_staging = Path(str(journal["database_staging_path"]))
    database_rollback = Path(str(journal["database_rollback_path"]))
    _remove_sqlite_sidecars(database_staging)
    _remove_sqlite_sidecars(database_rollback)
    _unlink_regular_file(database_staging)
    _unlink_regular_file(database_rollback)
    _remove_tree_strict(Path(str(journal["artifact_staging_path"])))
    _remove_tree_strict(Path(str(journal["artifact_rollback_path"])))


def _rollback_restore(target: Path, journal_path: Path, journal: dict[str, object]) -> None:
    _journal_phase(journal_path, journal, "rolling_back")
    database_staging = Path(str(journal["database_staging_path"]))
    database_rollback = Path(str(journal["database_rollback_path"]))
    target_existed = bool(journal["target_existed"])
    if _path_exists(database_rollback):
        _assert_regular_file(database_rollback)
        _remove_sqlite_sidecars(target)
        _unlink_regular_file(target)
        os.replace(database_rollback, target)
        _fsync_directory(target.parent)
    elif target_existed:
        _require_offline_target(target)
        _assert_regular_file(target)
        if (
            os.lstat(target).st_size != journal["target_original_size_bytes"]
            or _sha256_file(target) != journal["target_original_sha256"]
        ):
            raise RuntimeError("restore rollback database authority is missing")
    elif _path_exists(database_staging):
        _require_offline_target(target)
        if _path_exists(target):
            raise RuntimeError("unexpected restore target appeared during rollback")
    else:
        _remove_sqlite_sidecars(target)
        _unlink_regular_file(target)

    reference_root = Path(str(journal["reference_root"]))
    artifact_staging = Path(str(journal["artifact_staging_path"]))
    artifact_rollback = Path(str(journal["artifact_rollback_path"]))
    inventory = journal["artifact_inventory"]
    if not isinstance(inventory, list):
        raise ValueError("restore rollback artifact inventory is invalid")
    for item in reversed(inventory):
        if not isinstance(item, dict):
            raise ValueError("restore rollback artifact row is invalid")
        relative = _validate_managed_relative_path(item["managed_relative_path"])
        destination = reference_root / relative
        staged = artifact_staging / relative
        previous = artifact_rollback / relative
        if _path_exists(previous):
            _assert_regular_file(previous)
            _unlink_regular_file(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_symlinks(destination.parent, allow_missing_leaf=False)
            os.replace(previous, destination)
            _fsync_directory(destination.parent)
            _fsync_directory(previous.parent)
        elif item["destination_existed"]:
            _assert_regular_file(destination)
            if (
                os.lstat(destination).st_size != item["destination_size_bytes"]
                or _sha256_file(destination) != item["destination_sha256"]
            ):
                raise RuntimeError(f"restore rollback artifact authority is missing: {relative}")
        elif _path_exists(staged):
            if _path_exists(destination):
                raise RuntimeError(f"unexpected artifact appeared during rollback: {relative}")
        else:
            _unlink_regular_file(destination)
    _cleanup_restore_work(journal)


def _recover_restore_journal(target: Path, journal_path: Path) -> None:
    if not _path_exists(journal_path):
        return
    journal = _load_restore_journal(target, journal_path)
    admitted_connection, target_identity = _admit_existing_target(target)
    if admitted_connection is not None:
        admitted_connection.rollback()
        admitted_connection.close()
        _assert_regular_file(target)
        metadata = os.lstat(target)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            raise RuntimeError("restore target changed during journal recovery admission")
    if journal["phase"] == "complete":
        recovered_health = health(target)
        if recovered_health.get("status") != "healthy":
            raise sqlite3.IntegrityError(
                f"completed restore journal target is not healthy: {recovered_health!r}"
            )
        _remove_sqlite_sidecars(target)
        _cleanup_restore_work(journal)
    else:
        _rollback_restore(target, journal_path, journal)
    _unlink_regular_file(journal_path)


def restore_database(backup_path: str | Path, target_path: str | Path) -> dict[str, object]:
    """Offline, journaled restore of a database and digest-bound managed artifacts."""

    backup = _absolute_unresolved(backup_path)
    manifest_path = _manifest_path(backup)
    artifact_bundle = _artifact_bundle_path(backup)
    target = _absolute_unresolved(target_path)
    if target.name.endswith(("-wal", "-shm")):
        raise ValueError("restore target must not be a SQLite -wal or -shm sidecar")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(target.parent, allow_missing_leaf=False)
    _assert_no_symlinks(target, allow_missing_leaf=True)

    operation_id = uuid.uuid4().hex
    lock_path = _restore_lock_path(target)
    journal_path = _restore_journal_path(target)
    try:
        _publish_exclusive_record(
            lock_path,
            {
                "schema": _RESTORE_LOCK_SCHEMA,
                "operation_id": operation_id,
                "target_path": str(target),
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except FileExistsError as exc:
        raise RuntimeError("restore target is busy; restore lock already exists") from exc

    source_connection: sqlite3.Connection | None = None
    admitted_connection: sqlite3.Connection | None = None
    journal: dict[str, object] | None = None
    database_staging: Path | None = None
    database_rollback: Path | None = None
    artifact_staging: Path | None = None
    artifact_rollback: Path | None = None
    database_staging_created = False
    artifact_staging_created = False
    artifact_rollback_created = False
    try:
        _recover_restore_journal(target, journal_path)
        _require_offline_target(target)
        _assert_regular_file(backup)
        _assert_regular_file(manifest_path)
        manifest = _load_manifest(manifest_path)
        if manifest["backup_filename"] != backup.name:
            raise ValueError("backup filename does not match manifest")
        bundle_metadata = manifest["artifact_bundle"]
        if (
            not isinstance(bundle_metadata, dict)
            or bundle_metadata.get("directory_name") != artifact_bundle.name
        ):
            raise ValueError("backup artifact bundle name does not match manifest")
        actual_size = os.lstat(backup).st_size
        if actual_size != manifest["backup_size_bytes"]:
            raise ValueError("backup size mismatch")
        backup_sha256 = _sha256_file(backup)
        if backup_sha256 != manifest["backup_sha256"]:
            raise ValueError("backup digest mismatch")

        admitted_connection, target_identity = _admit_existing_target(target)
        target_original_sha256 = _sha256_file(target) if target_identity is not None else None
        target_original_size = os.lstat(target).st_size if target_identity is not None else None
        source_connection = _connect(backup, read_only=True)
        backup_inventory = _database_inventory(source_connection)
        for key in (
            "schema_version",
            "migration_ledger",
            "member_receipts",
            "outbox_events",
            "managed_reference_artifacts",
        ):
            if backup_inventory[key] != manifest[key]:
                raise ValueError(f"backup manifest inventory mismatch: {key}")
        artifact_inventory = backup_inventory["managed_reference_artifacts"]
        if not isinstance(artifact_inventory, list):
            raise ValueError("backup database artifact inventory is invalid")
        _validate_artifact_bundle(artifact_bundle, artifact_inventory)
        if bundle_metadata != _bundle_metadata(artifact_bundle, artifact_inventory):
            raise ValueError("backup artifact bundle metadata mismatch")
        _verify_database(source_connection, artifact_root=artifact_bundle)

        database_staging = target.parent / f".{target.name}.restore-{operation_id}.db"
        database_rollback = target.parent / f".{target.name}.restore-rollback-{operation_id}.db"
        descriptor = os.open(
            database_staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        database_staging_created = True
        _assert_no_symlinks(database_rollback, allow_missing_leaf=True)
        if _path_exists(database_rollback):
            raise FileExistsError(database_rollback)
        destination_connection = sqlite3.connect(database_staging)
        try:
            source_connection.backup(destination_connection)
            destination_connection.commit()
        finally:
            destination_connection.close()
        verification = _connect(database_staging, read_only=True)
        try:
            _verify_database(verification, artifact_root=artifact_bundle)
            restored_inventory = _database_inventory(verification)
            reconciliation = _receipt_reconciliation_inventory(verification)
        finally:
            verification.close()
        if restored_inventory != backup_inventory:
            raise sqlite3.IntegrityError("restored database inventory changed")
        _remove_sqlite_sidecars(database_staging)
        staging_descriptor = os.open(
            database_staging, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(staging_descriptor)
        finally:
            os.close(staging_descriptor)

        reference_root = get_molbio_ngs_reference_root().expanduser().absolute()
        reference_root.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlinks(reference_root.parent, allow_missing_leaf=False)
        _assert_no_symlinks(reference_root, allow_missing_leaf=True)
        artifact_staging = reference_root.parent / f".molbio-ngs-artifact-restore-{operation_id}"
        artifact_rollback = reference_root.parent / f".molbio-ngs-artifact-rollback-{operation_id}"
        os.mkdir(artifact_staging, 0o700)
        artifact_staging_created = True
        os.mkdir(artifact_rollback, 0o700)
        artifact_rollback_created = True
        artifact_plan: list[dict[str, object]] = []
        for item in artifact_inventory:
            relative = _validate_managed_relative_path(item["managed_relative_path"])
            _copy_verified_artifact(
                artifact_bundle / relative,
                artifact_staging / relative,
                item,
            )
            destination = reference_root / relative
            _assert_no_symlinks(destination, allow_missing_leaf=True)
            destination_existed = _path_exists(destination)
            if destination_existed:
                _assert_regular_file(destination)
            artifact_plan.append(
                {
                    **item,
                    "destination_existed": destination_existed,
                    "destination_sha256": (
                        _sha256_file(destination) if destination_existed else None
                    ),
                    "destination_size_bytes": (
                        os.lstat(destination).st_size if destination_existed else None
                    ),
                }
            )
        _fsync_tree_directories(artifact_staging)
        _fsync_tree_directories(artifact_rollback)

        journal = {
            "schema": _RESTORE_JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "target_path": str(target),
            "backup_sha256": backup_sha256,
            "target_existed": target_identity is not None,
            "target_original_sha256": target_original_sha256,
            "target_original_size_bytes": target_original_size,
            "database_staging_path": str(database_staging),
            "database_rollback_path": str(database_rollback),
            "reference_root": str(reference_root),
            "artifact_staging_path": str(artifact_staging),
            "artifact_rollback_path": str(artifact_rollback),
            "artifact_inventory": artifact_plan,
            "phase": "prepared",
        }
        _validate_restore_journal(target, journal)
        _publish_exclusive_record(journal_path, journal)

        if admitted_connection is not None:
            admitted_connection.rollback()
            admitted_connection.close()
            admitted_connection = None
        _recheck_admitted_target(target, target_identity)
        if _sha256_file(backup) != backup_sha256:
            raise RuntimeError("backup changed immediately before restore mutation")
        _validate_artifact_bundle(artifact_bundle, artifact_inventory)

        for index, item in enumerate(artifact_plan):
            relative = _validate_managed_relative_path(item["managed_relative_path"])
            destination = reference_root / relative
            staged = artifact_staging / relative
            previous = artifact_rollback / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_symlinks(destination.parent, allow_missing_leaf=False)
            _assert_no_symlinks(previous.parent, allow_missing_leaf=False)
            if _path_exists(destination) != item["destination_existed"]:
                raise RuntimeError(f"managed artifact changed before restore mutation: {relative}")
            if item["destination_existed"]:
                _assert_regular_file(destination)
                if (
                    os.lstat(destination).st_size != item["destination_size_bytes"]
                    or _sha256_file(destination) != item["destination_sha256"]
                ):
                    raise RuntimeError(f"managed artifact changed before restore mutation: {relative}")
                _journal_phase(journal_path, journal, f"artifact:{index}:rollback_pending")
                os.replace(destination, previous)
                _fsync_directory(destination.parent)
                _fsync_directory(previous.parent)
            _journal_phase(journal_path, journal, f"artifact:{index}:install_pending")
            os.replace(staged, destination)
            _fsync_directory(staged.parent)
            _fsync_directory(destination.parent)
            _journal_phase(journal_path, journal, f"artifact:{index}:installed")

        if target_identity is not None:
            _journal_phase(journal_path, journal, "database_rollback_pending")
            os.replace(target, database_rollback)
            _fsync_directory(target.parent)
        _journal_phase(journal_path, journal, "database_install_pending")
        os.replace(database_staging, target)
        _fsync_directory(target.parent)
        _journal_phase(journal_path, journal, "database_installed")

        post_restore_health = health(target)
        if post_restore_health.get("status") != "healthy":
            raise sqlite3.IntegrityError(
                f"post-restore MolBio/NGS health failed: {post_restore_health!r}"
            )
        _journal_phase(journal_path, journal, "verified")
        _remove_sqlite_sidecars(target)
        _journal_phase(journal_path, journal, "complete")
        _cleanup_restore_work(journal)
        _unlink_regular_file(journal_path)
        journal = None
        return {
            "target_path": str(target),
            "post_restore_health": post_restore_health,
            "external_receipt_availability_reconciliation": reconciliation,
        }
    except Exception as exc:
        if journal is not None and _path_exists(journal_path):
            try:
                _rollback_restore(target, journal_path, journal)
                _unlink_regular_file(journal_path)
                journal = None
            except Exception as rollback_exc:
                raise RuntimeError(
                    f"restore failed and journal rollback did not complete: {rollback_exc}"
                ) from exc
        else:
            if database_staging is not None and database_staging_created:
                _remove_sqlite_sidecars(database_staging)
                _unlink_regular_file(database_staging)
            if artifact_staging is not None and artifact_staging_created:
                _remove_tree_strict(artifact_staging)
            if artifact_rollback is not None and artifact_rollback_created:
                _remove_tree_strict(artifact_rollback)
        raise
    finally:
        if admitted_connection is not None:
            admitted_connection.rollback()
            admitted_connection.close()
        if source_connection is not None:
            source_connection.close()
        _unlink_regular_file(lock_path)


__all__ = [
    "BACKUP_MANIFEST_SCHEMA",
    "MIGRATION_NAME",
    "MIGRATION_SQL",
    "MIGRATION_VERSION",
    "attest_schema",
    "backup_database",
    "health",
    "migration_checksum",
    "restore_database",
    "run_all",
]

"""Backup-first extraction of legacy Mol Bio rows into the owned SQLite store."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
import uuid

import rfc8785
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import (
    MolecularDocument,
    MolecularRevision,
    NucleotideSequence,
    Primer,
    PrimerRevision,
    RestrictionDigestResult,
)
from services.sqlite_backup import backup_sqlite_database
from services.nucleotide_validation import canonicalize_nucleotide_sequence
from services.sqlite_schema_attestation import sqlite_master_sql_identity


class MigrationConflictError(RuntimeError):
    pass


class MigrationVerificationError(RuntimeError):
    pass


RESTRICTION_DIGEST_MIGRATION_VERSION = "0007_restriction_digest_results"
RESTRICTION_DIGEST_MIGRATION_NAME = "immutable exact restriction digest results"
_RESTRICTION_DIGEST_COLUMNS = (
    ("id", "VARCHAR(36)", False, 1),
    ("operation_id", "VARCHAR(36)", False, 0),
    ("source_revision_id", "VARCHAR(36)", False, 0),
    ("catalog_id", "VARCHAR(128)", False, 0),
    ("catalog_sha256", "VARCHAR(64)", False, 0),
    ("request_sha256", "VARCHAR(64)", False, 0),
    ("result_sha256", "VARCHAR(64)", False, 0),
    ("result", "TEXT", False, 0),
    ("created_at", "DATETIME", False, 0),
)
_RESTRICTION_DIGEST_FOREIGN_KEYS = (
    ("operation_id", "molecular_operations", "id", "NO ACTION", "RESTRICT"),
    ("source_revision_id", "molecular_revisions", "id", "NO ACTION", "RESTRICT"),
)
_RESTRICTION_DIGEST_INDEXES = (
    (
        "ix_restriction_digest_results_source_created", False, "c", False,
        ("source_revision_id", "created_at"),
    ),
    ("sqlite_autoindex_restriction_digest_results_1", True, "pk", False, ("id",)),
    ("sqlite_autoindex_restriction_digest_results_2", True, "u", False, ("operation_id",)),
)


def _normalize_restriction_digest_sql(sql: object) -> str:
    return sqlite_master_sql_identity(sql)


def _restriction_digest_immutable_trigger_sql(action: str) -> str:
    table_name = "restriction_digest_results"
    trigger_name = f"molbio_immutable_{table_name}_{action.lower()}"
    return f'''CREATE TRIGGER "{trigger_name}"
                    BEFORE {action} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END'''


def restriction_digest_json_equal(left: object, right: object) -> int:
    """SQLite UDF: compare two JSON values structurally, independent of key order."""

    try:
        if not isinstance(left, str) or not isinstance(right, str):
            return 0
        return int(json.loads(left) == json.loads(right))
    except Exception:
        return 0


def validate_restriction_digest_result(
    result_text: object,
    operation_id: object,
    source_revision_id: object,
    catalog_id: object,
    catalog_sha256: object,
    request_sha256: object,
    result_sha256: object,
) -> int:
    """SQLite UDF: strict JCS, closed models, digest, and row-binding validation."""

    try:
        from services.restriction_digest import DigestSimulation

        if not isinstance(result_text, str):
            return 0
        payload = json.loads(result_text)
        if not isinstance(payload, dict) or set(payload) != {
            "schema", "operation_id", "source_revision_id", "catalog_id",
            "catalog_sha256", "request_sha256", "result_sha256", "simulation", "outputs",
        }:
            return 0
        if rfc8785.dumps(payload).decode("utf-8") != result_text:
            return 0
        expected = {
            "operation_id": str(operation_id), "source_revision_id": str(source_revision_id),
            "catalog_id": str(catalog_id), "catalog_sha256": str(catalog_sha256),
            "request_sha256": str(request_sha256), "result_sha256": str(result_sha256),
        }
        if payload.get("schema") != "bms.molbio.restriction-digest-saved-result.v1" or any(
            payload.get(key) != value for key, value in expected.items()
        ):
            return 0
        simulation = DigestSimulation.model_validate(payload.get("simulation"))
        if (
            simulation.source.kind != "molecular_revision"
            or simulation.source.revision_id != str(source_revision_id)
            or simulation.catalog.catalog_id != str(catalog_id)
            or simulation.catalog.catalog_sha256 != str(catalog_sha256)
            or simulation.request_sha256 != str(request_sha256)
            or simulation.simulation_sha256 != str(result_sha256)
            or hashlib.sha256(simulation.canonical_unsigned_bytes()).hexdigest() != str(result_sha256)
        ):
            return 0
        outputs = payload.get("outputs")
        output_keys = {
            "fragment_index", "document_id", "revision_id", "output_edge_id",
            "name", "topology", "content_sha256", "content_length",
        }
        if not isinstance(outputs, list):
            return 0
        if outputs and len(outputs) != len(simulation.fragments):
            return 0
        identity_fields = ("document_id", "revision_id", "output_edge_id")
        if any(
            len({str(output.get(field)) for output in outputs}) != len(outputs)
            for field in identity_fields
        ):
            return 0
        for ordinal, output in enumerate(outputs):
            fragment = simulation.fragments[ordinal]
            fragment_bytes = fragment.top_strand_sequence.encode("ascii")
            if (
                not isinstance(output, dict) or set(output) != output_keys
                or output.get("fragment_index") != ordinal
                or output.get("topology") != fragment.topology
                or not isinstance(output.get("content_length"), int)
                or output.get("content_length") != len(fragment_bytes)
                or output.get("content_sha256")
                != hashlib.sha256(fragment_bytes).hexdigest()
                or any(not isinstance(output.get(field), str) or not output[field] for field in identity_fields)
            ):
                return 0
        return 1
    except Exception:
        return 0


_RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL = """
CREATE TRIGGER molbio_restriction_digest_results_integrity_insert
BEFORE INSERT ON restriction_digest_results
WHEN bms_restriction_digest_result_valid(
       NEW.result, NEW.operation_id, NEW.source_revision_id, NEW.catalog_id,
       NEW.catalog_sha256, NEW.request_sha256, NEW.result_sha256
     ) != 1
  OR json_valid(NEW.result) != 1
  OR json(NEW.result) != NEW.result
  OR json_type(NEW.result) != 'object'
  OR (SELECT count(*) FROM json_each(NEW.result)) != 9
  OR json_extract(NEW.result, '$.schema') != 'bms.molbio.restriction-digest-saved-result.v1'
  OR json_extract(NEW.result, '$.operation_id') IS NOT NEW.operation_id
  OR json_extract(NEW.result, '$.source_revision_id') IS NOT NEW.source_revision_id
  OR json_extract(NEW.result, '$.catalog_id') IS NOT NEW.catalog_id
  OR json_extract(NEW.result, '$.catalog_sha256') IS NOT NEW.catalog_sha256
  OR json_extract(NEW.result, '$.request_sha256') IS NOT NEW.request_sha256
  OR json_extract(NEW.result, '$.result_sha256') IS NOT NEW.result_sha256
  OR json_extract(NEW.result, '$.simulation.schema') != 'bms.molbio.restriction-digest-simulation.v1'
  OR json_extract(NEW.result, '$.simulation.simulation_sha256') IS NOT NEW.result_sha256
  OR json_type(NEW.result, '$.outputs') != 'array'
  OR length(NEW.catalog_sha256) != 64
  OR length(NEW.request_sha256) != 64
  OR length(NEW.result_sha256) != 64
  OR NOT EXISTS (
       SELECT 1
       FROM molecular_operations AS operation
       WHERE operation.id = NEW.operation_id
         AND operation.operation_kind = 'restriction_digest'
         AND operation.implementation = 'services.restriction_digest.simulate_digest'
         AND operation.implementation_version
             IS json_extract(NEW.result, '$.simulation.digest_algorithm_version')
         AND operation.status = 'completed'
         AND json_valid(operation.parameters) = 1
         AND json_type(operation.parameters) = 'object'
         AND (SELECT count(*) FROM json_each(operation.parameters)) = 6
         AND json_extract(operation.parameters, '$.schema')
             = 'bms.molbio.restriction-digest-operation-parameters.v1'
         AND json_extract(operation.parameters, '$.selected_enzyme_ids')
             IS json_extract(NEW.result, '$.simulation.selected_enzyme_ids')
         AND json_extract(operation.parameters, '$.simulation_sha256') IS NEW.result_sha256
         AND json_type(operation.parameters, '$.save_request_receipt') = 'text'
         AND bms_restriction_digest_save_receipt_valid(
               json_extract(operation.parameters, '$.save_request_receipt'),
               operation.idempotency_key,
               operation.request_fingerprint
             ) = 1
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.schema'
             ) = 'bms.molbio.restriction-digest-save-request.v1'
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.source.kind'
             ) = 'molecular_revision'
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.source.sequence_id'
             ) IS json_extract(NEW.result, '$.simulation.source.sequence_id')
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.source.revision_id'
             ) IS NEW.source_revision_id
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'),
               '$.source.expected_content_sha256'
             ) IS json_extract(NEW.result, '$.simulation.source.content_sha256')
         AND (
               json_type(
                 json_extract(operation.parameters, '$.save_request_receipt'), '$.source.topology'
               ) = 'null'
               OR json_extract(
                    json_extract(operation.parameters, '$.save_request_receipt'), '$.source.topology'
                  ) IS json_extract(NEW.result, '$.simulation.source.topology')
             )
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.catalog.catalog_id'
             ) IS NEW.catalog_id
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'),
               '$.catalog.expected_catalog_sha256'
             ) IS NEW.catalog_sha256
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.enzyme_ids'
             ) IS json_extract(NEW.result, '$.simulation.selected_enzyme_ids')
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.simulation_sha256'
             ) IS NEW.result_sha256
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.idempotency_key'
             ) IS operation.idempotency_key
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'), '$.persistence_mode'
             ) IS json_extract(operation.parameters, '$.persistence_mode')
         AND json_extract(
               json_extract(operation.parameters, '$.save_request_receipt'),
               '$.fragment_name_prefix'
             ) IS json_extract(operation.parameters, '$.fragment_name_prefix')
         AND json_type(operation.parameters, '$.fragment_name_prefix') IN ('null', 'text')
         AND (
               json_type(operation.parameters, '$.fragment_name_prefix') = 'null'
               OR length(trim(json_extract(
                    operation.parameters, '$.fragment_name_prefix'
                  ))) > 0
             )
         AND (
               (
                 json_extract(operation.parameters, '$.persistence_mode') = 'operation_only'
                 AND json_array_length(NEW.result, '$.outputs') = 0
               )
               OR (
                 json_extract(operation.parameters, '$.persistence_mode')
                     = 'operation_and_fragments'
                 AND json_array_length(NEW.result, '$.outputs')
                     = json_array_length(NEW.result, '$.simulation.fragments')
               )
             )
         AND json_valid(operation.warnings) = 1
         AND json_type(operation.warnings) = 'array'
         AND json(operation.warnings)
             IS json(json_extract(NEW.result, '$.simulation.warnings'))
         AND json_valid(operation.provenance) = 1
         AND json_type(operation.provenance) = 'object'
         AND (SELECT count(*) FROM json_each(operation.provenance)) = 4
         AND json_extract(operation.provenance, '$.source_revision_id')
             IS NEW.source_revision_id
         AND json_extract(operation.provenance, '$.catalog_id') IS NEW.catalog_id
         AND json_extract(operation.provenance, '$.catalog_sha256') IS NEW.catalog_sha256
         AND json_extract(operation.provenance, '$.request_sha256') IS NEW.request_sha256
         AND typeof(operation.idempotency_key) = 'text'
         AND length(trim(operation.idempotency_key)) > 0
         AND length(operation.request_fingerprint) = 64
         AND operation.request_fingerprint NOT GLOB '*[^0-9a-f]*'
     )
  OR (SELECT count(*) FROM molecular_operation_inputs
      WHERE operation_id = NEW.operation_id) != 1
  OR NOT EXISTS (
       SELECT 1
       FROM molecular_operation_inputs AS input
       WHERE input.operation_id = NEW.operation_id
         AND input.revision_id = NEW.source_revision_id
         AND input.role = 'digest_source'
         AND input.position = 0
         AND json_valid(input.snapshot) = 1
         AND json_type(input.snapshot) = 'object'
         AND (SELECT count(*) FROM json_each(input.snapshot)) = 3
         AND json_extract(input.snapshot, '$.content_sha256')
             IS json_extract(NEW.result, '$.simulation.source.content_sha256')
         AND json_extract(input.snapshot, '$.name')
             IS json_extract(NEW.result, '$.simulation.source.name')
         AND json_extract(input.snapshot, '$.sequence_id')
             IS json_extract(NEW.result, '$.simulation.source.sequence_id')
     )
  OR NOT EXISTS (
       SELECT 1
       FROM molecular_revisions AS revision
       JOIN molecular_documents AS document ON document.id = revision.document_id
       WHERE revision.id = NEW.source_revision_id
         AND revision.document_id
             IS json_extract(NEW.result, '$.simulation.source.sequence_id')
         AND revision.revision_number
             IS json_extract(NEW.result, '$.simulation.source.revision_number')
         AND revision.content_sha256
             IS json_extract(NEW.result, '$.simulation.source.content_sha256')
         AND revision.content_length
             IS json_extract(NEW.result, '$.simulation.source.content_length')
         AND document.document_kind = 'dna'
         AND (
               (
                 json_type(revision.snapshot, '$.is_circular') = 'true'
                 AND json_extract(NEW.result, '$.simulation.source.topology') = 'circular'
               )
               OR (
                 json_type(revision.snapshot, '$.is_circular') = 'false'
                 AND json_extract(NEW.result, '$.simulation.source.topology') = 'linear'
               )
               OR (
                 coalesce(json_type(revision.snapshot, '$.is_circular'), 'missing')
                     NOT IN ('true', 'false')
                 AND json_extract(revision.snapshot, '$.topology')
                     IS json_extract(NEW.result, '$.simulation.source.topology')
               )
             )
     )
  OR (SELECT count(*) FROM molecular_operation_outputs
      WHERE operation_id = NEW.operation_id)
     != json_array_length(NEW.result, '$.outputs')
  OR EXISTS (
       SELECT 1
       FROM json_each(NEW.result, '$.outputs') AS identity
       LEFT JOIN molecular_operation_outputs AS edge
         ON edge.id = json_extract(identity.value, '$.output_edge_id')
       LEFT JOIN molecular_revisions AS revision
         ON revision.id = json_extract(identity.value, '$.revision_id')
       LEFT JOIN molecular_documents AS document
         ON document.id = json_extract(identity.value, '$.document_id')
       WHERE edge.id IS NULL
          OR edge.operation_id != NEW.operation_id
          OR edge.revision_id IS NOT json_extract(identity.value, '$.revision_id')
          OR edge.role != 'digest_fragment'
          OR edge.position IS NOT CAST(identity.key AS INTEGER)
          OR json_valid(edge.snapshot) != 1
          OR json_type(edge.snapshot) != 'object'
          OR (SELECT count(*) FROM json_each(edge.snapshot)) != 3
          OR json_extract(edge.snapshot, '$.fragment_index')
             IS NOT CAST(identity.key AS INTEGER)
          OR json_extract(edge.snapshot, '$.name')
             IS NOT json_extract(identity.value, '$.name')
          OR json_extract(edge.snapshot, '$.simulation_sha256') IS NOT NEW.result_sha256
          OR revision.id IS NULL
          OR revision.document_id IS NOT json_extract(identity.value, '$.document_id')
          OR revision.revision_number != 1
          OR revision.change_kind != 'restriction_digest_fragment'
          OR revision.content_sha256
             IS NOT json_extract(identity.value, '$.content_sha256')
          OR revision.content_length
             IS NOT json_extract(identity.value, '$.content_length')
          OR revision.operation_id != NEW.operation_id
          OR revision.created_by IS NOT NULL
          OR json_valid(revision.snapshot) != 1
          OR json_type(revision.snapshot) != 'object'
          OR (SELECT count(*) FROM json_each(revision.snapshot)) != 5
          OR json_extract(revision.snapshot, '$.sequence_type') != 'dna'
          OR json_extract(revision.snapshot, '$.sequence') IS NOT json_extract(
               NEW.result,
               '$.simulation.fragments[' || CAST(identity.key AS INTEGER)
               || '].top_strand_sequence'
             )
          OR json_extract(revision.snapshot, '$.topology')
             IS NOT json_extract(identity.value, '$.topology')
          OR json_extract(revision.snapshot, '$.name')
             IS NOT json_extract(identity.value, '$.name')
          OR json_extract(revision.snapshot, '$.is_circular')
             IS NOT (json_extract(identity.value, '$.topology') = 'circular')
          OR json_valid(revision.provenance) != 1
          OR json_type(revision.provenance) != 'object'
          OR (SELECT count(*) FROM json_each(revision.provenance)) != 6
          OR json_extract(revision.provenance, '$.schema')
             != 'bms.molbio.restriction-digest-fragment-provenance.v1'
          OR json_extract(revision.provenance, '$.source_revision_id')
             != NEW.source_revision_id
          OR json_extract(revision.provenance, '$.operation_id') != NEW.operation_id
          OR json_extract(revision.provenance, '$.simulation_sha256') != NEW.result_sha256
          OR json_extract(revision.provenance, '$.fragment_index')
             IS NOT CAST(identity.key AS INTEGER)
          OR bms_restriction_digest_json_equal(
               json_extract(revision.provenance, '$.geometry'),
               json_extract(
                 NEW.result,
                 '$.simulation.fragments[' || CAST(identity.key AS INTEGER) || ']'
               )
             ) != 1
          OR document.id IS NULL
          OR document.document_kind != 'dna'
          OR json_extract(identity.value, '$.fragment_index')
             IS NOT CAST(identity.key AS INTEGER)
          OR json_extract(identity.value, '$.topology') IS NOT json_extract(
               NEW.result,
               '$.simulation.fragments[' || CAST(identity.key AS INTEGER) || '].topology'
             )
          OR json_extract(identity.value, '$.name') IS NOT (
               CASE
                 WHEN json_type(
                        (SELECT parameters FROM molecular_operations
                         WHERE id = NEW.operation_id),
                        '$.fragment_name_prefix'
                      ) = 'text'
                 THEN trim(json_extract(
                        (SELECT parameters FROM molecular_operations
                         WHERE id = NEW.operation_id),
                        '$.fragment_name_prefix'
                      ))
                 ELSE json_extract(NEW.result, '$.simulation.source.name')
                      || ' digest fragment'
               END || ' ' || (CAST(identity.key AS INTEGER) + 1)
             )
     )
BEGIN
  SELECT RAISE(ABORT, 'restriction digest result integrity violation');
END
""".strip()
def restriction_digest_integrity_trigger_sql() -> str:
    return _RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL


def _restriction_digest_expected_triggers() -> dict[str, str]:
    return {
        "molbio_immutable_restriction_digest_results_delete": (
            _restriction_digest_immutable_trigger_sql("DELETE")
        ),
        "molbio_immutable_restriction_digest_results_update": (
            _restriction_digest_immutable_trigger_sql("UPDATE")
        ),
        "molbio_restriction_digest_results_integrity_insert": (
            _RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL
        ),
    }


def restriction_digest_physical_schema_issues(  # noqa: ANN001
    sync_connection, *, allow_missing_triggers: bool = False,
) -> list[str]:
    """Compare every attested 0007 SQLite object without repairing drift."""

    table_name = "restriction_digest_results"
    table_exists = sync_connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,),
    ).scalar_one_or_none()
    if table_exists is None:
        return ["attested table is missing"]

    issues: list[str] = []
    columns = tuple(
        (
            str(row[1]), str(row[2]).upper(), not bool(row[3]), int(row[5]),
        )
        for row in sync_connection.exec_driver_sql(
            'PRAGMA table_info("restriction_digest_results")'
        ).fetchall()
    )
    if columns != _RESTRICTION_DIGEST_COLUMNS:
        issues.append("attested columns, types, nullability, or primary-key ordinals differ")

    foreign_keys = tuple(sorted(
        (
            str(row[3]), str(row[2]), str(row[4]),
            str(row[5]).upper(), str(row[6]).upper(),
        )
        for row in sync_connection.exec_driver_sql(
            'PRAGMA foreign_key_list("restriction_digest_results")'
        ).fetchall()
    ))
    if foreign_keys != tuple(sorted(_RESTRICTION_DIGEST_FOREIGN_KEYS)):
        issues.append("attested foreign keys differ")

    index_rows = sync_connection.exec_driver_sql(
        'PRAGMA index_list("restriction_digest_results")'
    ).fetchall()
    observed_indexes = {
        str(row[1]): (bool(row[2]), str(row[3]), bool(row[4]))
        for row in index_rows
    }
    expected_indexes = {
        name: (unique, origin, partial)
        for name, unique, origin, partial, _columns in _RESTRICTION_DIGEST_INDEXES
    }
    if observed_indexes != expected_indexes:
        issues.append("attested index identities, uniqueness, or origins differ")
    else:
        for name, _unique, _origin, _partial, expected_columns in _RESTRICTION_DIGEST_INDEXES:
            quoted_name = name.replace('"', '""')
            actual_columns = tuple(
                str(row[2]) for row in sync_connection.exec_driver_sql(
                    f'PRAGMA index_info("{quoted_name}")'
                ).fetchall()
            )
            if actual_columns != expected_columns:
                issues.append(f"attested index columns differ: {name}")

    trigger_rows = sync_connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
        (table_name,),
    ).fetchall()
    observed_triggers = {str(row[0]): str(row[1] or "") for row in trigger_rows}
    expected_triggers = _restriction_digest_expected_triggers()
    unexpected_triggers = set(observed_triggers).difference(expected_triggers)
    missing_triggers = set(expected_triggers).difference(observed_triggers)
    allow_fresh_trigger_bootstrap = (
        allow_missing_triggers
        and missing_triggers == {
            "molbio_restriction_digest_results_integrity_insert"
        }
        and set(observed_triggers) == {
            "molbio_immutable_restriction_digest_results_delete",
            "molbio_immutable_restriction_digest_results_update",
        }
    )
    if unexpected_triggers or (missing_triggers and not allow_fresh_trigger_bootstrap):
        issues.append("attested trigger identities differ")
    for name in set(observed_triggers).intersection(expected_triggers):
        if (
            _normalize_restriction_digest_sql(observed_triggers[name])
            != _normalize_restriction_digest_sql(expected_triggers[name])
        ):
            issues.append(f"attested trigger SQL differs: {name}")
    return issues


def restriction_digest_migration_attestation() -> dict[str, object]:
    """Return the complete deterministic Phase 3 migration identity."""

    return {
        "schema": "bms.molbio.restriction-digest-migration-attestation.v1",
        "version": RESTRICTION_DIGEST_MIGRATION_VERSION,
        "name": RESTRICTION_DIGEST_MIGRATION_NAME,
        "table": {
            "name": "restriction_digest_results",
            "columns": [list(column) for column in _RESTRICTION_DIGEST_COLUMNS],
            "foreign_keys": [
                ["operation_id", "molecular_operations", "id", "NO ACTION", "RESTRICT"],
                ["source_revision_id", "molecular_revisions", "id", "NO ACTION", "RESTRICT"],
            ],
            "unique": [["operation_id"]],
            "indexes": [["source_revision_id", "created_at"]],
        },
        "objects": {
            "tables": ["restriction_digest_results"],
            "indexes": [
                "ix_restriction_digest_results_source_created",
                "sqlite_autoindex_restriction_digest_results_1",
                "sqlite_autoindex_restriction_digest_results_2",
            ],
            "triggers": [
                "molbio_immutable_restriction_digest_results_delete",
                "molbio_immutable_restriction_digest_results_update",
                "molbio_restriction_digest_results_integrity_insert",
            ],
        },
        "integrity_trigger_sha256": hashlib.sha256(
            _RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL.encode("utf-8")
        ).hexdigest(),
        "trigger_sha256": {
            name: hashlib.sha256(sql.encode("utf-8")).hexdigest()
            for name, sql in _restriction_digest_expected_triggers().items()
        },
    }


RESTRICTION_DIGEST_MIGRATION_CHECKSUM = hashlib.sha256(
    rfc8785.dumps(restriction_digest_migration_attestation())
).hexdigest()


async def apply_restriction_digest_result_migration(connection: AsyncConnection) -> None:
    """Add the exact immutable digest result table and insert-time binding guard."""

    existing_table = (
        await connection.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='restriction_digest_results'"
        ))
    ).scalar_one_or_none()
    if existing_table is not None:
        issues = await connection.run_sync(
            lambda sync_connection: restriction_digest_physical_schema_issues(
                sync_connection, allow_missing_triggers=True,
            )
        )
        if issues:
            raise RuntimeError(
                "counterfeit restriction digest schema blocks migration: " + "; ".join(issues)
            )
        existing_integrity_trigger = (
            await connection.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                "AND name='molbio_restriction_digest_results_integrity_insert'"
            ))
        ).scalar_one_or_none()
        if existing_integrity_trigger is None:
            await connection.execute(text(_RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL))
        return
    await connection.run_sync(
        lambda sync_connection: RestrictionDigestResult.__table__.create(
            sync_connection, checkfirst=False,
        )
    )
    await connection.execute(text(_RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL))
    violations = (await connection.execute(text("PRAGMA foreign_key_check"))).fetchall()
    if violations:
        raise MigrationVerificationError(
            "Restriction digest migration foreign-key verification failed"
        )


async def _acquire_destination_extraction_lock(destination: Path) -> int:
    """Acquire a cooperative process-wide lock for one destination database."""

    lock_path = destination.with_suffix(destination.suffix + ".extract.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                await asyncio.sleep(0.01)
    except BaseException:
        os.close(descriptor)
        raise


def _release_destination_extraction_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


@dataclass(frozen=True)
class LegacyMolBioMigrationReport:
    source_path: Path
    destination_path: Path
    backup_path: Path
    backup_sha256: str
    source_manifest_sha256: str
    source_sequence_count: int
    destination_sequence_count: int
    source_primer_count: int
    destination_primer_count: int
    total_bases: int
    sequence_checksums: dict[str, str]
    copied_sequences: int
    skipped_sequences: int
    copied_primers: int
    skipped_primers: int
    foreign_key_violations: list[tuple[Any, ...]]
    orphan_parent_sequences: list[str]
    orphan_primer_targets: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("source_path", "destination_path", "backup_path"):
            payload[key] = str(payload[key])
        return payload


_SEQUENCE_FIELDS = (
    "id",
    "name",
    "description",
    "sequence",
    "sequence_type",
    "molecule_strandedness",
    "molecule_orientation",
    "is_circular",
    "length",
    "features",
    "primers",
    "analysis_tracks",
    "organism",
    "accession",
    "source_file",
    "parent_id",
    "operation",
    "operation_params",
    "version",
    "gc_content",
    "created_at",
    "updated_at",
)

_PRIMER_FIELDS = (
    "id",
    "name",
    "sequence",
    "sequence_type",
    "length",
    "tm",
    "gc_percent",
    "tm_algorithm",
    "tm_salt_correction",
    "tm_settings",
    "primer_type",
    "description",
    "target_sequence_id",
    "binding_start",
    "binding_end",
    "binding_strand",
    "tags",
    "is_favorite",
    "created_at",
    "updated_at",
)

_JSON_FIELDS = {"features", "primers", "analysis_tracks", "operation_params", "tm_settings", "tags"}
_BOOL_FIELDS = {"is_circular", "is_favorite"}
_DATETIME_FIELDS = {"created_at", "updated_at"}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _coerce_record(row: sqlite3.Row, fields: Iterable[str]) -> dict[str, Any]:
    available = set(row.keys())
    record: dict[str, Any] = {}
    for field in fields:
        value = row[field] if field in available else None
        if field in _JSON_FIELDS:
            value = _parse_json(value)
        elif field in _BOOL_FIELDS:
            value = bool(value)
        elif field in _DATETIME_FIELDS:
            value = _parse_datetime(value)
        record[field] = value
    return record


def _normalizable(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is not None:
            normalized = normalized.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {key: _normalizable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalizable(item) for item in value]
    return value


def _canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(_normalizable(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _model_record(model: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {field: getattr(model, field) for field in fields}


def _sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _normalize_legacy_sequence_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    sequence_type = str(normalized.get("sequence_type") or "dna").lower()
    if sequence_type not in {"dna", "rna"}:
        raise MigrationVerificationError(
            f"Legacy sequence {normalized.get('id')} has unsupported type {sequence_type!r}"
        )
    try:
        sequence = canonicalize_nucleotide_sequence(
            str(normalized.get("sequence") or ""),
            sequence_type,
            allow_empty=False,
        )
    except ValueError as error:
        raise MigrationVerificationError(
            f"Legacy sequence {normalized.get('id')} is invalid: {error}"
        ) from error
    if normalized.get("length") not in (None, len(sequence)):
        raise MigrationVerificationError(
            f"Legacy sequence {normalized.get('id')} length metadata does not match content"
        )
    normalized.update(
        sequence=sequence,
        sequence_type=sequence_type,
        molecule_strandedness=normalized.get("molecule_strandedness") or "unknown",
        molecule_orientation=normalized.get("molecule_orientation") or "unknown",
        is_circular=bool(normalized.get("is_circular")),
        length=len(sequence),
        features=normalized.get("features") or [],
        primers=normalized.get("primers") or [],
        analysis_tracks=normalized.get("analysis_tracks") or [],
        version=int(normalized.get("version") or 1),
        created_at=normalized.get("created_at") or datetime(1970, 1, 1),
    )
    return normalized


def _normalize_legacy_primer_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    sequence_type = str(normalized.get("sequence_type") or "dna").lower()
    if sequence_type not in {"dna", "rna"}:
        raise MigrationVerificationError(
            f"Legacy primer {normalized.get('id')} has unsupported type {sequence_type!r}"
        )
    try:
        sequence = canonicalize_nucleotide_sequence(
            str(normalized.get("sequence") or ""),
            sequence_type,
            allow_empty=False,
        )
    except ValueError as error:
        raise MigrationVerificationError(
            f"Legacy primer {normalized.get('id')} is invalid: {error}"
        ) from error
    if normalized.get("length") not in (None, len(sequence)):
        raise MigrationVerificationError(
            f"Legacy primer {normalized.get('id')} length metadata does not match content"
        )
    normalized.update(
        sequence=sequence,
        sequence_type=sequence_type,
        length=len(sequence),
        primer_type=normalized.get("primer_type") or "general",
        binding_strand=int(normalized.get("binding_strand") or 1),
        is_favorite=bool(normalized.get("is_favorite")),
        created_at=normalized.get("created_at") or datetime(1970, 1, 1),
    )
    return normalized


def _legacy_manifest_sha256(
    sequence_rows: list[dict[str, Any]],
    primer_rows: list[dict[str, Any]],
) -> str:
    entries = [
        {
            "kind": "sequence",
            "id": str(record["id"]),
            "content_sha256": hashlib.sha256(
                _canonical_record(record).encode("utf-8")
            ).hexdigest(),
        }
        for record in sequence_rows
    ]
    entries.extend(
        {
            "kind": "primer",
            "id": str(record["id"]),
            "content_sha256": hashlib.sha256(
                _canonical_record(record).encode("utf-8")
            ).hexdigest(),
        }
        for record in primer_rows
    )
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def legacy_molbio_manifest_sha256(source_path: Path) -> str:
    """Build the immutable canonical manifest that an operator must approve."""

    sequence_rows, primer_rows = _read_legacy_rows(Path(source_path).expanduser().resolve())
    normalized_sequences = [
        _normalize_legacy_sequence_record(record) for record in sequence_rows
    ]
    normalized_sequences = _order_sequences_by_parent_dependency(normalized_sequences)
    normalized_primers = [_normalize_legacy_primer_record(record) for record in primer_rows]
    return _legacy_manifest_sha256(normalized_sequences, normalized_primers)


def _sequence_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalizable(value) for key, value in record.items()}


def _read_legacy_rows(source: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0) as connection:
        connection.row_factory = sqlite3.Row
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "nucleotide_sequences" not in table_names:
            raise MigrationVerificationError("Legacy source has no nucleotide_sequences table")
        sequence_rows = [
            _coerce_record(row, _SEQUENCE_FIELDS)
            for row in connection.execute("SELECT * FROM nucleotide_sequences ORDER BY id").fetchall()
        ]
        primer_rows: list[dict[str, Any]] = []
        if "primers" in table_names:
            primer_rows = [
                _coerce_record(row, _PRIMER_FIELDS)
                for row in connection.execute("SELECT * FROM primers ORDER BY id").fetchall()
            ]
    return sequence_rows, primer_rows


def _order_sequences_by_parent_dependency(
    sequence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a stable parent-before-child order, rejecting orphans and cycles."""

    by_id = {str(record["id"]): record for record in sequence_rows}
    if len(by_id) != len(sequence_rows):
        raise MigrationVerificationError("Legacy sequence graph contains duplicate IDs")

    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(sequence_id: str) -> None:
        if sequence_id in visited:
            return
        if sequence_id in visiting:
            raise MigrationVerificationError(
                f"Legacy sequence parent graph contains a cycle at {sequence_id}"
            )
        visiting.add(sequence_id)
        record = by_id[sequence_id]
        parent_value = record.get("parent_id")
        if parent_value not in (None, ""):
            parent_id = str(parent_value)
            if parent_id not in by_id:
                raise MigrationVerificationError(
                    f"Legacy sequence {sequence_id} references missing parent {parent_id}"
                )
            visit(parent_id)
        visiting.remove(sequence_id)
        visited.add(sequence_id)
        ordered.append(record)

    for record in sequence_rows:
        visit(str(record["id"]))
    return ordered


async def _verify_legacy_history_graph(
    session: AsyncSession,
    sequence_rows: list[dict[str, Any]],
    primer_rows: list[dict[str, Any]],
    source_checksums: dict[str, str],
    backup_sha256: str,
) -> None:
    """Fail closed unless the destination has exactly the expected legacy history graph."""

    expected_sequence_ids = {str(record["id"]) for record in sequence_rows}
    expected_primer_ids = {str(record["id"]) for record in primer_rows}
    documents = (await session.execute(select(MolecularDocument))).scalars().all()
    molecular_revisions = (await session.execute(select(MolecularRevision))).scalars().all()
    primer_revisions = (await session.execute(select(PrimerRevision))).scalars().all()

    document_ids = {str(document.id) for document in documents}
    revision_document_ids = {str(revision.document_id) for revision in molecular_revisions}
    revision_primer_ids = {str(revision.primer_id) for revision in primer_revisions}
    if (
        document_ids != expected_sequence_ids
        or revision_document_ids != expected_sequence_ids
        or len(molecular_revisions) != len(expected_sequence_ids)
        or revision_primer_ids != expected_primer_ids
        or len(primer_revisions) != len(expected_primer_ids)
    ):
        raise MigrationVerificationError(
            "Destination immutable history graph has missing or surplus documents/revisions"
        )

    documents_by_id = {str(document.id): document for document in documents}
    revisions_by_document = {
        str(revision.document_id): revision for revision in molecular_revisions
    }
    expected_provenance_base = {
        "source": "legacy_core_sqlite",
        "source_database_backup_sha256": backup_sha256,
    }

    for record in sequence_rows:
        sequence_id = str(record["id"])
        document = documents_by_id[sequence_id]
        revision = revisions_by_document[sequence_id]
        expected_provenance = {
            **expected_provenance_base,
            "source_row_id": record["id"],
        }
        source_created_at = record.get("created_at")
        if (
            document.current_revision_id != revision.id
            or document.document_kind != (record["sequence_type"] or "dna")
            or document.name != record["name"]
            or document.deleted_at is not None
            or (source_created_at is not None and document.created_at != source_created_at)
            or revision.revision_number != int(record["version"] or 1)
            or revision.change_kind != "legacy_import"
            or revision.content_sha256 != source_checksums[sequence_id]
            or revision.content_length != len(str(record["sequence"]))
            or _canonical_record(revision.snapshot) != _canonical_record(record)
            or revision.provenance != expected_provenance
            or revision.operation_id is not None
            or revision.created_by is not None
            or (source_created_at is not None and revision.created_at != source_created_at)
        ):
            raise MigrationVerificationError(
                f"Incomplete or corrupt sequence history graph for {sequence_id}"
            )

    primer_revisions_by_id = {
        str(revision.primer_id): revision for revision in primer_revisions
    }
    for record in primer_rows:
        primer_id = str(record["id"])
        revision = primer_revisions_by_id[primer_id]
        expected_provenance = {
            **expected_provenance_base,
            "source_row_id": record["id"],
        }
        source_created_at = record.get("created_at")
        if (
            revision.revision_number != 1
            or revision.change_kind != "legacy_import"
            or revision.sequence_sha256 != _sequence_sha256(str(record["sequence"]))
            or _canonical_record(revision.snapshot) != _canonical_record(record)
            or revision.provenance != expected_provenance
            or revision.created_by is not None
            or (source_created_at is not None and revision.created_at != source_created_at)
        ):
            raise MigrationVerificationError(
                f"Incomplete or corrupt primer history graph for {primer_id}"
            )


async def extract_legacy_molbio_data(
    source_path: Path,
    destination_path: Path,
    *,
    backup_path: Path,
    expected_manifest_sha256: str | None = None,
    expected_sequence_count: int | None = None,
    expected_total_bases: int | None = None,
    expected_primer_count: int | None = None,
) -> LegacyMolBioMigrationReport:
    """Back up, copy, and independently verify legacy Mol Bio records.

    The source database is never modified. Existing matching destination rows
    are skipped; any same-ID difference is an explicit conflict.
    """

    source = Path(source_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    paths = (("source", source), ("destination", destination), ("backup", backup))
    for index, (left_label, left) in enumerate(paths[:-1]):
        for right_label, right in paths[index + 1 :]:
            aliases = left == right
            if not aliases and left.exists() and right.exists():
                aliases = os.path.samefile(left, right)
            if aliases:
                raise ValueError(
                    f"Mol Bio extraction {left_label} and {right_label} paths must be distinct "
                    "and must not alias the same file"
                )
    backup_report = backup_sqlite_database(source, backup)
    # Extract from the verified online-backup snapshot so a concurrent legacy
    # writer cannot make the copied rows diverge from the provenance artifact.
    sequence_rows, primer_rows = _read_legacy_rows(backup_report.backup_path)
    sequence_rows = [
        _normalize_legacy_sequence_record(record) for record in sequence_rows
    ]
    sequence_rows = _order_sequences_by_parent_dependency(sequence_rows)
    primer_rows = [_normalize_legacy_primer_record(record) for record in primer_rows]
    source_manifest_sha256 = _legacy_manifest_sha256(sequence_rows, primer_rows)
    if expected_manifest_sha256 is None:
        raise MigrationVerificationError(
            "Legacy extraction requires an operator-approved canonical manifest SHA-256"
        )
    if source_manifest_sha256.lower() != expected_manifest_sha256.strip().lower():
        raise MigrationVerificationError(
            "Legacy canonical manifest changed before extraction: "
            f"expected={expected_manifest_sha256} observed={source_manifest_sha256}"
        )
    source_checksums = {
        str(row["id"]): _sequence_sha256(str(row["sequence"])) for row in sequence_rows
    }
    source_total_bases = sum(len(str(row["sequence"])) for row in sequence_rows)
    observed_inventory = (len(sequence_rows), source_total_bases, len(primer_rows))
    expected_inventory = (
        expected_sequence_count,
        expected_total_bases,
        expected_primer_count,
    )
    for label, expected, observed in zip(
        ("sequence count", "total bases", "primer count"),
        expected_inventory,
        observed_inventory,
        strict=True,
    ):
        if expected is not None and expected != observed:
            raise MigrationVerificationError(
                f"Legacy {label} changed before extraction: expected={expected} observed={observed}"
            )

    lock_descriptor = await _acquire_destination_extraction_lock(destination)
    try:
        engine = create_molbio_engine(f"sqlite+aiosqlite:///{destination}")
    except BaseException:
        _release_destination_extraction_lock(lock_descriptor)
        raise
    copied_sequences = 0
    skipped_sequences = 0
    copied_primers = 0
    skipped_primers = 0
    try:
        await init_molbio_db(engine=engine)
        factory = make_molbio_session_factory(engine)
        async with factory() as session:
            async with session.begin():
                for record in sequence_rows:
                    existing = await session.get(NucleotideSequence, record["id"])
                    if existing is not None:
                        existing_payload = _canonical_record(_model_record(existing, _SEQUENCE_FIELDS))
                        if existing_payload != _canonical_record(record):
                            raise MigrationConflictError(
                                f"Destination sequence {record['id']} differs from the legacy source"
                            )
                        skipped_sequences += 1
                        continue

                    sequence = NucleotideSequence(**record)
                    document = MolecularDocument(
                        id=record["id"],
                        document_kind=record["sequence_type"] or "dna",
                        name=record["name"],
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    revision = MolecularRevision(
                        id=str(uuid.uuid4()),
                        document_id=record["id"],
                        revision_number=int(record["version"] or 1),
                        change_kind="legacy_import",
                        content_sha256=source_checksums[str(record["id"])],
                        content_length=len(str(record["sequence"])),
                        snapshot=_sequence_snapshot(record),
                        provenance={
                            "source": "legacy_core_sqlite",
                            "source_database_backup_sha256": backup_report.sha256,
                            "source_row_id": record["id"],
                        },
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    session.add_all([sequence, document, revision])
                    await session.flush()
                    document.current_revision_id = revision.id
                    copied_sequences += 1

                await session.flush()
                for record in primer_rows:
                    existing = await session.get(Primer, record["id"])
                    if existing is not None:
                        existing_payload = _canonical_record(_model_record(existing, _PRIMER_FIELDS))
                        if existing_payload != _canonical_record(record):
                            raise MigrationConflictError(
                                f"Destination primer {record['id']} differs from the legacy source"
                            )
                        skipped_primers += 1
                        continue

                    target_id = record.get("target_sequence_id")
                    if target_id and await session.get(NucleotideSequence, target_id) is None:
                        raise MigrationVerificationError(
                            f"Primer {record['id']} references missing sequence {target_id}"
                        )
                    primer = Primer(**record)
                    primer_revision = PrimerRevision(
                        id=str(uuid.uuid4()),
                        primer_id=record["id"],
                        revision_number=1,
                        change_kind="legacy_import",
                        sequence_sha256=_sequence_sha256(str(record["sequence"])),
                        snapshot=_sequence_snapshot(record),
                        provenance={
                            "source": "legacy_core_sqlite",
                            "source_database_backup_sha256": backup_report.sha256,
                            "source_row_id": record["id"],
                        },
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    session.add_all([primer, primer_revision])
                    copied_primers += 1

                await session.flush()
                destination_sequence_count = int(
                    (
                        await session.execute(select(func.count()).select_from(NucleotideSequence))
                    ).scalar_one()
                )
                destination_primer_count = int(
                    (await session.execute(select(func.count()).select_from(Primer))).scalar_one()
                )
                destination_rows = (
                    await session.execute(select(NucleotideSequence.id, NucleotideSequence.sequence))
                ).all()
                destination_checksums = {
                    str(row.id): _sequence_sha256(str(row.sequence)) for row in destination_rows
                }
                destination_quick_check = [
                    str(row[0])
                    for row in (
                        await session.execute(text("PRAGMA quick_check"))
                    ).fetchall()
                ]
                foreign_key_violations = [
                    tuple(row)
                    for row in (await session.execute(text("PRAGMA foreign_key_check"))).fetchall()
                ]
                orphan_parent_sequences = [
                    str(row[0])
                    for row in (
                        await session.execute(
                            text(
                                "SELECT child.id FROM nucleotide_sequences child "
                                "LEFT JOIN nucleotide_sequences parent ON parent.id = child.parent_id "
                                "WHERE child.parent_id IS NOT NULL AND parent.id IS NULL ORDER BY child.id"
                            )
                        )
                    ).fetchall()
                ]
                orphan_primer_targets = [
                    str(row[0])
                    for row in (
                        await session.execute(
                            text(
                                "SELECT primer.id FROM primers primer "
                                "LEFT JOIN nucleotide_sequences target ON target.id = primer.target_sequence_id "
                                "WHERE primer.target_sequence_id IS NOT NULL AND target.id IS NULL ORDER BY primer.id"
                            )
                        )
                    ).fetchall()
                ]

                if destination_sequence_count != len(sequence_rows):
                    raise MigrationVerificationError(
                        "Sequence count mismatch: "
                        f"source={len(sequence_rows)} destination={destination_sequence_count}"
                    )
                if destination_primer_count != len(primer_rows):
                    raise MigrationVerificationError(
                        "Primer count mismatch: "
                        f"source={len(primer_rows)} destination={destination_primer_count}"
                    )
                if destination_checksums != source_checksums:
                    raise MigrationVerificationError("Per-sequence SHA-256 verification failed")
                if destination_quick_check != ["ok"]:
                    raise MigrationVerificationError(
                        "Destination PRAGMA quick_check failed: "
                        + "; ".join(destination_quick_check)
                    )
                if foreign_key_violations or orphan_parent_sequences or orphan_primer_targets:
                    raise MigrationVerificationError(
                        "Destination has foreign-key violations or orphan molecular references"
                    )
                await _verify_legacy_history_graph(
                    session,
                    sequence_rows,
                    primer_rows,
                    source_checksums,
                    backup_report.sha256,
                )

        return LegacyMolBioMigrationReport(
            source_path=source,
            destination_path=destination,
            backup_path=backup_report.backup_path,
            backup_sha256=backup_report.sha256,
            source_manifest_sha256=source_manifest_sha256,
            source_sequence_count=len(sequence_rows),
            destination_sequence_count=destination_sequence_count,
            source_primer_count=len(primer_rows),
            destination_primer_count=destination_primer_count,
            total_bases=source_total_bases,
            sequence_checksums=source_checksums,
            copied_sequences=copied_sequences,
            skipped_sequences=skipped_sequences,
            copied_primers=copied_primers,
            skipped_primers=skipped_primers,
            foreign_key_violations=foreign_key_violations,
            orphan_parent_sequences=orphan_parent_sequences,
            orphan_primer_targets=orphan_primer_targets,
        )
    finally:
        try:
            await engine.dispose()
        finally:
            _release_destination_extraction_lock(lock_descriptor)

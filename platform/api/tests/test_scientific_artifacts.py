from __future__ import annotations

import importlib
import json
import sqlite3

import pyarrow as pa
import pytest

from migrations.add_scientific_artifact_receipts import migrate
import scripts.migrate_json_payloads_to_artifacts as migration
from services.scientific_artifacts import (
    artifact_row_reference,
    envelope_rows,
    install_parquet_rows,
    query_rows,
    reconstruct_envelope,
    resolve_json_value,
    verify_artifact,
)
from services.scientific_artifacts.writer import ScientificArtifactError


def test_parquet_artifact_round_trips_exact_envelope_and_duckdb_page(tmp_path):
    payload = {
        "scalar": {"nested": [3, 2, 1]},
        "list": [{"b": 2, "a": 1}, {"value": 0}],
    }
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="cm_record",
        owner_id="request/one",
        role="payload",
        schema_id="bms.json-envelope.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=envelope_rows(payload),
        schema=schema,
    )

    assert verify_artifact(installed, root=tmp_path) == installed.storage_path
    rows = query_rows(
        installed.reference(),
        columns=["key", "item_index", "payload_json"],
        limit=10,
        root=str(tmp_path),
    )
    assert reconstruct_envelope(rows) == payload


def test_row_reference_is_compact_and_resolves_value(tmp_path):
    payload = {"value": {"nested": [1, 2]}}
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="cm_record",
        owner_id="request/one",
        role="payload",
        schema_id="bms.json-envelope.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=envelope_rows(payload),
        schema=schema,
    )
    reference = artifact_row_reference(installed.reference(), 0, value_field="payload_json")
    assert len(json.dumps(reference, separators=(",", ":"))) < 450
    assert resolve_json_value(reference, root=tmp_path) == {"nested": [1, 2]}


def test_redundant_frustra_indexes_are_removed():
    index_migration = importlib.import_module("migrations.add_frustrampnn_landscape_index_slimming")
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE frustrampnn_landscape_rows (id TEXT PRIMARY KEY, parent_job_id TEXT, invocation_id TEXT, target_id TEXT, entity_instance_id TEXT, status TEXT);
        CREATE INDEX ix_frustrampnn_landscape_rows_parent_job_id ON frustrampnn_landscape_rows(parent_job_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_invocation_id ON frustrampnn_landscape_rows(invocation_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_target_id ON frustrampnn_landscape_rows(target_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_entity_instance_id ON frustrampnn_landscape_rows(entity_instance_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_status ON frustrampnn_landscape_rows(status);
        """
    )
    index_migration.migrate(connection)
    remaining = {row[1] for row in connection.execute('PRAGMA index_list("frustrampnn_landscape_rows")')}
    assert remaining == {"ix_frustrampnn_landscape_rows_status", "sqlite_autoindex_frustrampnn_landscape_rows_1"}



    assert migration.design_field_rows("design-1", "confidence_metrics", {"b": 2, "a": 1}) == {
        "row_index": 0,
        "design_id": "design-1",
        "field_name": "confidence_metrics",
        "payload_json": "{\"a\":1,\"b\":2}",
    }


def test_design_field_rows_preserve_nan_values():
    row = migration.design_field_rows("design-1", "confidence_metrics", {"score": float("nan")})
    assert row["payload_json"] == "{\"score\":NaN}"


def test_statistics_source_sha_excludes_self_digest() -> None:
    payload = {"value": 3, "statistics_sha256": ""}
    payload["statistics_sha256"] = migration.statistics_source_sha(payload, None)
    assert migration.statistics_source_sha(payload, payload["statistics_sha256"]) == payload["statistics_sha256"]


def test_envelope_reconstructs_empty_lists():
    payload = {"empty": [], "scalar": "ok"}
    assert reconstruct_envelope(envelope_rows(payload)) == payload


def test_artifact_verification_rejects_tampered_bytes(tmp_path):
    schema = pa.schema([("value", pa.int64())])
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="test",
        owner_id="one",
        role="values",
        schema_id="bms.test.v1",
        schema_version=1,
        source_sha256="b" * 64,
        rows=[{"value": 1}],
        schema=schema,
    )
    installed.storage_path.write_bytes(installed.storage_path.read_bytes() + b"tamper")

    with pytest.raises(ScientificArtifactError, match="do not match"):
        verify_artifact(installed, root=tmp_path)


def test_scientific_receipt_migration_is_idempotent_and_foreign_key_bound(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    migrate(db_path)
    migrate(db_path)

    connection = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "scientific_artifact_receipts",
        "scientific_payload_migrations",
    } <= tables
    connection.execute(
        "INSERT INTO scientific_artifact_receipts "
        "(artifact_id, owner_kind, owner_id, role, schema_id, artifact_schema_version, "
        "content_sha256, size_bytes, row_count, column_schema_sha256, storage_root, "
        "relative_path, media_type, availability, source_receipts_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            "artifact-1", "test", "one", "values", "bms.test.v1", 1,
            "c" * 64, 1, 1, "d" * 64, "test-root", "test/one.parquet",
            "application/vnd.apache.parquet", "available", json.dumps({}),
        ),
    )
    connection.execute(
        "INSERT INTO scientific_payload_migrations "
        "(migration_id, source_store, source_table, source_column, source_key, source_sha256, "
        "artifact_id, state, attempt_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            "migration-1", "core", "test", "payload_json", "one", "e" * 64,
            "artifact-1", "completed", 1,
        ),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()

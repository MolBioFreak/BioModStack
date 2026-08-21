"""Migrate oversized scientific JSON payloads into immutable Parquet artifacts.

The source database is a read-only snapshot. The target receives one artifact,
receipt, ledger row, and compact reference only after equivalence checks pass.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Mapping

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import pyarrow as pa
import pyarrow.parquet as pq
import rfc8785

from migrations.add_scientific_artifact_receipts import migrate as migrate_core_receipts
from services.scientific_artifacts.contracts import (
    artifact_row_reference,
    canonical_json_bytes,
    canonical_sha256,
    envelope_rows,
    reconstruct_envelope,
)
from services.scientific_artifacts.resolve import resolve_json_value
from services.scientific_artifacts.writer import (
    InstalledArtifact,
    artifact_reference,
    install_parquet_rows,
    read_rows,
)

ENVELOPE_SCHEMA = pa.schema(
    [
        ("key", pa.string()),
        ("item_index", pa.int64()),
        ("payload_json", pa.string()),
    ]
)
FRUSTRA_LANDSCAPE_SCHEMA = pa.schema(
    [
        ("row_index", pa.int64()),
        ("id", pa.string()),
        ("target_id", pa.string()),
        ("entity_instance_id", pa.string()),
        ("auth_asym_id", pa.string()),
        ("auth_seq_id", pa.string()),
        ("insertion_code", pa.string()),
        ("sequence_index", pa.int64()),
        ("wt", pa.string()),
        ("mutation_aa", pa.string()),
        ("score", pa.float64()),
        ("score_class", pa.string()),
        ("scoreable", pa.bool_()),
        ("status", pa.string()),
        ("reason", pa.string()),
        ("row_json", pa.string()),
        ("provenance_json", pa.string()),
    ]
)
CM_PROVENANCE_SCHEMA = pa.schema(
    [("row_index", pa.int64()), ("row_id", pa.string()), ("provenance_json", pa.string())]
)
DESIGN_ARTIFACT_FIELDS = (
    "confidence_metrics",
    "residue_plddt",
    "rfa_loop_metrics",
    "rfa_hotspot_metrics",
    "provenance",
    "review_artifact_manifest",
    "review_role_map",
    "chain_metrics",
    "frustration_residues",
    "rfa_design_loops",
    "rfa_hotspots",
    "ppiflow_loop_metrics",
    "metric_provenance",
    "metric_completeness",
    "pair_chains_iptm",
    "chains_ptm",
)
DESIGN_PAYLOAD_SCHEMA = pa.schema(
    [
        ("row_index", pa.int64()),
        ("design_id", pa.string()),
        ("field_name", pa.string()),
        ("payload_json", pa.string()),
    ]
)
TELEMETRY_SCHEMA = pa.schema(
    [
        ("row_index", pa.int64()),
        ("timestamp_ms", pa.int64()),
        ("bucket_ms", pa.int64()),
        ("sample_count", pa.int64()),
        ("timestamp", pa.string()),
        ("payload_json", pa.string()),
        ("cpu_utilization", pa.float64()),
        ("ram_utilization", pa.float64()),
        ("gpu_utilization", pa.list_(pa.float64())),
        ("gpu_memory_used_mb", pa.list_(pa.float64())),
        ("gpu_names", pa.list_(pa.string())),
    ]
)


def json_value(raw: Any) -> Any:
    if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
        return raw
    return json.loads(str(raw))


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def statistics_source_sha(payload: Mapping[str, Any], stored_sha256: Any) -> str:
    """Return FrustraMPNN's hash of statistics excluding its self-digest field."""
    without_self = dict(payload)
    without_self.pop("statistics_sha256", None)
    computed = hashlib.sha256(rfc8785.dumps(without_self)).hexdigest()
    if stored_sha256 not in (None, "") and str(stored_sha256) != computed:
        raise ValueError("stored FrustraMPNN statistics digest does not match RFC 8785 source bytes")
    return str(stored_sha256 or computed)


def design_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)


def design_field_rows(
    design_id: str, field_name: str, payload: Any, *, row_index: int = 0
) -> dict[str, Any]:
    if not design_id or field_name not in DESIGN_ARTIFACT_FIELDS:
        raise ValueError("invalid Design artifact field")
    return {
        "row_index": int(row_index),
        "design_id": str(design_id),
        "field_name": str(field_name),
        "payload_json": design_json_text(payload),
    }


def digest_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json_bytes(dict(row)))
        digest.update(b"\n")
    return digest.hexdigest()


def artifact_source_info(
    *, source_store: str, source_table: str, source_column: str, source_key: str, source_sha256: str
) -> dict[str, str]:
    return {
        "source_store": source_store,
        "source_table": source_table,
        "source_column": source_column,
        "source_key": source_key,
        "source_sha256": source_sha256,
    }


def ensure_receipt_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scientific_artifact_receipts (
            artifact_id TEXT PRIMARY KEY NOT NULL,
            owner_kind TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            role TEXT NOT NULL,
            schema_id TEXT NOT NULL,
            artifact_schema_version INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            column_schema_sha256 TEXT NOT NULL,
            storage_root TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            availability TEXT NOT NULL,
            source_receipts_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(storage_root, relative_path),
            UNIQUE(owner_kind, owner_id, role, content_sha256)
        );
        CREATE TABLE IF NOT EXISTS scientific_payload_migrations (
            migration_id TEXT PRIMARY KEY NOT NULL,
            source_store TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_column TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            artifact_id TEXT,
            artifact_sha256 TEXT,
            equivalence_sha256 TEXT,
            state TEXT NOT NULL,
            diagnostic TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_store, source_table, source_column, source_key, source_sha256)
        );
        CREATE INDEX IF NOT EXISTS ix_scientific_artifact_owner
            ON scientific_artifact_receipts(owner_kind, owner_id, role);
        CREATE INDEX IF NOT EXISTS ix_scientific_payload_migration_state
            ON scientific_payload_migrations(state, updated_at);
        """
    )


def install_receipt(
    connection: sqlite3.Connection,
    artifact: InstalledArtifact,
    source_info: Mapping[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO scientific_artifact_receipts(
            artifact_id, owner_kind, owner_id, role, schema_id,
            artifact_schema_version, content_sha256, size_bytes, row_count,
            column_schema_sha256, storage_root, relative_path, media_type,
            availability, source_receipts_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, datetime('now'))
        """,
        (
            artifact.artifact_id,
            artifact.owner_kind,
            artifact.owner_id,
            artifact.role,
            artifact.schema_id,
            artifact.schema_version,
            artifact.content_sha256,
            artifact.size_bytes,
            artifact.row_count,
            artifact.column_schema_sha256,
            "scientific_artifact_root",
            artifact.relative_path,
            artifact.media_type,
            json_text(dict(source_info)),
        ),
    )
    row = connection.execute(
        "SELECT artifact_id, content_sha256, size_bytes, row_count, relative_path "
        "FROM scientific_artifact_receipts WHERE artifact_id = ?",
        (artifact.artifact_id,),
    ).fetchone()
    expected = (
        artifact.artifact_id,
        artifact.content_sha256,
        artifact.size_bytes,
        artifact.row_count,
        artifact.relative_path,
    )
    if tuple(row or ()) != expected:
        raise RuntimeError(f"artifact receipt conflict for {artifact.artifact_id}")


def install_ledger(
    connection: sqlite3.Connection,
    *,
    source_info: Mapping[str, Any],
    artifact: InstalledArtifact,
    equivalence_sha256: str,
) -> None:
    source_key = str(source_info["source_key"])
    migration_id = "payload_" + hashlib.sha256(
        canonical_json_bytes(
            [
                source_info["source_store"],
                source_info["source_table"],
                source_info["source_column"],
                source_key,
                source_info["source_sha256"],
            ]
        )
    ).hexdigest()
    connection.execute(
        """
        INSERT OR IGNORE INTO scientific_payload_migrations(
            migration_id, source_store, source_table, source_column, source_key,
            source_sha256, artifact_id, artifact_sha256, equivalence_sha256,
            state, attempt_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', 1, datetime('now'), datetime('now'))
        """,
        (
            migration_id,
            source_info["source_store"],
            source_info["source_table"],
            source_info["source_column"],
            source_key,
            source_info["source_sha256"],
            artifact.artifact_id,
            artifact.content_sha256,
            equivalence_sha256,
        ),
    )
    row = connection.execute(
        "SELECT state, artifact_id, equivalence_sha256 FROM scientific_payload_migrations WHERE migration_id = ?",
        (migration_id,),
    ).fetchone()
    if tuple(row or ()) != ("completed", artifact.artifact_id, equivalence_sha256):
        raise RuntimeError(f"payload migration ledger conflict for {migration_id}")


def publish_group(
    connection: sqlite3.Connection,
    *,
    artifact: InstalledArtifact,
    source_info: Mapping[str, Any],
    equivalence_sha256: str,
    updates: Iterable[tuple[str, tuple[Any, ...]]],
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        install_receipt(connection, artifact, source_info)
        for statement, parameters in updates:
            connection.execute(statement, parameters)
        install_ledger(connection, source_info=source_info, artifact=artifact, equivalence_sha256=equivalence_sha256)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _telemetry_columns(table: str) -> str:
    if table == "raw_samples":
        return """
            timestamp_ms INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL,
            cpu_utilization REAL NOT NULL DEFAULT 0.0,
            ram_utilization REAL NOT NULL DEFAULT 0.0,
            gpu_utilization_json TEXT NOT NULL DEFAULT '[]',
            gpu_memory_used_json TEXT NOT NULL DEFAULT '[]',
            gpu_names_json TEXT NOT NULL DEFAULT '[]',
            staging_relative_path TEXT,
            staging_row_locator INTEGER
        """
    if table == "minute_aggregates":
        return """
            bucket_ms INTEGER PRIMARY KEY,
            sample_count INTEGER NOT NULL CHECK (sample_count > 0),
            payload_json TEXT NOT NULL,
            cpu_utilization REAL NOT NULL DEFAULT 0.0,
            ram_utilization REAL NOT NULL DEFAULT 0.0,
            gpu_utilization_json TEXT NOT NULL DEFAULT '[]',
            gpu_memory_used_json TEXT NOT NULL DEFAULT '[]',
            gpu_names_json TEXT NOT NULL DEFAULT '[]'
        """
    raise ValueError(table)


def _recreate_telemetry_guard_triggers(connection: sqlite3.Connection, table: str) -> None:
    connection.executescript(
        f"""
        CREATE TRIGGER {table}_guard_delete
        BEFORE DELETE ON {table} WHEN telemetry_retention_authorized() != 1
        BEGIN SELECT RAISE(ABORT, '{table.replace('_', ' ')} is immutable'); END;
        CREATE TRIGGER {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table.replace('_', ' ')} is immutable'); END;
        """
    )


def publish_telemetry_group(
    connection: sqlite3.Connection,
    *,
    table: str,
    rows: list[dict[str, Any]],
    artifact: InstalledArtifact,
    source_info: Mapping[str, Any],
    equivalence_sha256: str,
) -> None:
    key = "timestamp_ms" if table == "raw_samples" else "bucket_ms"
    references = [
        json_text(artifact_row_reference(artifact.reference(), int(row["row_index"]), value_field="payload_json"))
        for row in rows
    ]
    connection.execute("BEGIN IMMEDIATE")
    legacy = f"{table}__legacy_json_repair"
    rebuilt = f"{table}__rebuilt"
    try:
        connection.execute(f"DROP TRIGGER IF EXISTS {table}_guard_delete")
        connection.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        connection.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')
        connection.execute(f'CREATE TABLE "{rebuilt}" ({_telemetry_columns(table)}) WITHOUT ROWID')
        insert_columns = (
            "timestamp_ms, payload_json, cpu_utilization, ram_utilization, gpu_utilization_json, "
            "gpu_memory_used_json, gpu_names_json, staging_relative_path, staging_row_locator"
            if table == "raw_samples" else
            "bucket_ms, sample_count, payload_json, cpu_utilization, ram_utilization, "
            "gpu_utilization_json, gpu_memory_used_json, gpu_names_json"
        )
        insert_sql = f'INSERT INTO "{rebuilt}" ({insert_columns}) VALUES ({", ".join("?" for _ in insert_columns.split(","))})'
        values = []
        for reference, row in zip(references, rows, strict=True):
            common = (
                row[key],
                reference,
                row["cpu_utilization"],
                row["ram_utilization"],
                json_text(row["gpu_utilization"]),
                json_text(row["gpu_memory_used_mb"]),
                json_text(row["gpu_names"]),
            )
            values.append(common + (None, None) if table == "raw_samples" else common[:1] + (row["sample_count"],) + common[1:])
        connection.executemany(insert_sql, values)
        connection.execute(f'DROP TABLE "{legacy}"')
        connection.execute(f'ALTER TABLE "{rebuilt}" RENAME TO "{table}"')
        _recreate_telemetry_guard_triggers(connection, table)
        install_receipt(connection, artifact, source_info)
        install_ledger(connection, source_info=source_info, artifact=artifact, equivalence_sha256=equivalence_sha256)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def backfill_cm_records(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    count = 0
    rows = source.execute(
        "SELECT id, request_id, record_type, record_key, content_sha256, payload_json "
        "FROM conformational_mapping_records WHERE payload_json IS NOT NULL ORDER BY id"
    )
    for row in rows:
        payload = json_value(row[5])
        if not isinstance(payload, dict):
            raise ValueError(f"CM record {row[0]} is not a JSON object")
        source_sha256 = str(row[4])
        if canonical_sha256(payload) != source_sha256:
            raise ValueError(f"CM record {row[0]} source digest mismatch")
        owner_id = f"{row[1]}:{row[2]}:{row[3]}"
        source_info = artifact_source_info(source_store="core", source_table="conformational_mapping_records", source_column="payload_json", source_key=str(row[0]), source_sha256=source_sha256)
        artifact = install_parquet_rows(root=root, owner_kind="conformational_mapping_record", owner_id=owner_id, role="payload", schema_id="bms.cm-json-envelope.v1", schema_version=1, source_sha256=source_sha256, rows=envelope_rows(payload), schema=ENVELOPE_SCHEMA)
        reconstructed = reconstruct_envelope(read_rows(artifact, root=root))
        if canonical_json_bytes(reconstructed) != canonical_json_bytes(payload):
            raise ValueError(f"CM record {row[0]} artifact equivalence failed")
        publish_group(target, artifact=artifact, source_info=source_info, equivalence_sha256=canonical_sha256(reconstructed), updates=[("UPDATE conformational_mapping_records SET payload_json = ? WHERE id = ?", (json_text(artifact.reference()), row[0]))])
        count += 1
    return count


def backfill_frustrampnn_statistics(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    count = 0
    for row in source.execute("SELECT parent_job_id, invocation_id, statistics_sha256, statistics_json FROM frustrampnn_results WHERE statistics_json IS NOT NULL ORDER BY parent_job_id, invocation_id"):
        payload = json_value(row[3])
        if not isinstance(payload, dict):
            raise ValueError(f"FrustraMPNN statistics {row[0]}:{row[1]} is not an object")
        source_sha256 = statistics_source_sha(payload, row[2])
        source_info = artifact_source_info(source_store="core", source_table="frustrampnn_results", source_column="statistics_json", source_key=f"{row[0]}:{row[1]}", source_sha256=source_sha256)
        artifact = install_parquet_rows(root=root, owner_kind="frustrampnn_result", owner_id=f"{row[0]}:{row[1]}", role="statistics", schema_id="bms.frustrampnn-statistics-envelope.v1", schema_version=1, source_sha256=source_sha256, rows=envelope_rows(payload), schema=ENVELOPE_SCHEMA)
        reconstructed = reconstruct_envelope(read_rows(artifact, root=root))
        if canonical_json_bytes(reconstructed) != canonical_json_bytes(payload):
            raise ValueError(f"FrustraMPNN statistics {row[0]}:{row[1]} artifact equivalence failed")
        publish_group(target, artifact=artifact, source_info=source_info, equivalence_sha256=canonical_sha256(reconstructed), updates=[("UPDATE frustrampnn_results SET statistics_json = ? WHERE parent_job_id = ? AND invocation_id = ?", (json_text(artifact.reference()), row[0], row[1]))])
        count += 1
    return count


def backfill_design_payloads(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    total = 0
    for field_name in DESIGN_ARTIFACT_FIELDS:
        source_rows = []
        for design_id, raw in source.execute(f'SELECT id, "{field_name}" FROM designs WHERE "{field_name}" IS NOT NULL ORDER BY id'):
            source_rows.append(design_field_rows(str(design_id), field_name, json_value(raw), row_index=len(source_rows)))
        if not source_rows:
            continue
        source_sha256 = digest_rows(source_rows)
        artifact = install_parquet_rows(root=root, owner_kind="design_json_field", owner_id=field_name, role="payload", schema_id="bms.design-json-field.v1", schema_version=1, source_sha256=source_sha256, rows=source_rows, schema=DESIGN_PAYLOAD_SCHEMA)
        artifact_rows = read_rows(artifact, root=root)
        if artifact_rows != source_rows:
            raise ValueError(f"Design field {field_name} artifact equivalence failed")
        updates = [(f'UPDATE designs SET "{field_name}" = ? WHERE id = ?', (json_text(artifact_row_reference(artifact.reference(), int(row["row_index"]), value_field="payload_json")), str(row["design_id"]))) for row in source_rows]
        publish_group(target, artifact=artifact, source_info=artifact_source_info(source_store="core", source_table="designs", source_column=field_name, source_key=f"field:{field_name}", source_sha256=source_sha256), equivalence_sha256=digest_rows(artifact_rows), updates=updates)
        total += len(source_rows)
    return total


def backfill_frustrampnn_landscape(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    source.row_factory = sqlite3.Row
    for row in source.execute("SELECT * FROM frustrampnn_landscape_rows ORDER BY parent_job_id, invocation_id, id"):
        groups[(str(row["parent_job_id"]), str(row["invocation_id"]))].append(row)
    checked = 0
    for (parent_job_id, invocation_id), group in groups.items():
        rows = []
        for index, row in enumerate(group):
            rows.append({"row_index": index, "id": str(row["id"]), "target_id": str(row["target_id"]), "entity_instance_id": str(row["entity_instance_id"]), "auth_asym_id": str(row["auth_asym_id"]), "auth_seq_id": str(row["auth_seq_id"]), "insertion_code": str(row["insertion_code"]), "sequence_index": int(row["sequence_index"]), "wt": str(row["wt"]), "mutation_aa": str(row["mutation_aa"]), "score": None if row["score"] is None else float(row["score"]), "score_class": str(row["score_class"]), "scoreable": bool(row["scoreable"]), "status": str(row["status"]), "reason": None if row["reason"] is None else str(row["reason"]), "row_json": str(row["row_json"]), "provenance_json": str(row["provenance_json"])})
        source_sha256 = digest_rows(rows)
        artifact = install_parquet_rows(root=root, owner_kind="frustrampnn_landscape", owner_id=f"{parent_job_id}:{invocation_id}", role="rows", schema_id="bms.frustrampnn-landscape.v1", schema_version=1, source_sha256=source_sha256, rows=rows, schema=FRUSTRA_LANDSCAPE_SCHEMA)
        if read_rows(artifact, root=root) != rows:
            raise ValueError(f"FrustraMPNN landscape {parent_job_id}:{invocation_id} artifact equivalence failed")
        updates = []
        for row in rows:
            row_reference = json_text(artifact_row_reference(artifact.reference(), row["row_index"], value_field="row_json"))
            provenance_reference = json_text(artifact_row_reference(artifact.reference(), row["row_index"], value_field="provenance_json"))
            updates.append(("UPDATE frustrampnn_landscape_rows SET row_json = ?, provenance_json = ? WHERE id = ?", (row_reference, provenance_reference, row["id"])))
        publish_group(target, artifact=artifact, source_info=artifact_source_info(source_store="core", source_table="frustrampnn_landscape_rows", source_column="row_json+provenance_json", source_key=f"{parent_job_id}:{invocation_id}", source_sha256=source_sha256), equivalence_sha256=source_sha256, updates=updates)
        checked += len(rows)
    return checked


def backfill_cm_landscape_provenance(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    source.row_factory = sqlite3.Row
    for row in source.execute("SELECT id, request_id, candidate_id, provenance_json FROM conformational_mapping_landscape_rows ORDER BY request_id, candidate_id, id"):
        groups[(str(row["request_id"]), str(row["candidate_id"]))].append(row)
    checked = 0
    for (request_id, candidate_id), group in groups.items():
        rows = [{"row_index": index, "row_id": str(row["id"]), "provenance_json": str(row["provenance_json"])} for index, row in enumerate(group)]
        source_sha256 = digest_rows(rows)
        artifact = install_parquet_rows(root=root, owner_kind="conformational_mapping_landscape", owner_id=f"{request_id}:{candidate_id}", role="provenance", schema_id="bms.cm-landscape-provenance.v1", schema_version=1, source_sha256=source_sha256, rows=rows, schema=CM_PROVENANCE_SCHEMA)
        if read_rows(artifact, root=root) != rows:
            raise ValueError(f"CM provenance {request_id}:{candidate_id} artifact equivalence failed")
        updates = [("UPDATE conformational_mapping_landscape_rows SET provenance_json = ? WHERE id = ?", (json_text(artifact_row_reference(artifact.reference(), row["row_index"], value_field="provenance_json")), row["row_id"])) for row in rows]
        publish_group(target, artifact=artifact, source_info=artifact_source_info(source_store="core", source_table="conformational_mapping_landscape_rows", source_column="provenance_json", source_key=f"{request_id}:{candidate_id}", source_sha256=source_sha256), equivalence_sha256=source_sha256, updates=updates)
        checked += len(rows)
    return checked


def telemetry_rows(
    source: sqlite3.Connection,
    table: str,
    source_artifact_root: Path | None = None,
) -> list[dict[str, Any]]:
    source.row_factory = sqlite3.Row
    root = source_artifact_root.resolve() if source_artifact_root else None
    result: list[dict[str, Any]] = []
    artifact_cache: dict[int, dict[int, str]] = {}
    receipt_columns = {row[1] for row in source.execute("PRAGMA table_info(scientific_artifact_receipts)")}

    def raw_artifact_payloads(bucket: int) -> dict[int, str]:
        if root is None or bucket in artifact_cache:
            return artifact_cache.get(bucket, {})
        receipt = source.execute(
            "SELECT * FROM scientific_artifact_receipts WHERE owner_kind = 'telemetry_bucket' AND owner_id = ? AND role = 'raw_history'",
            (f"raw:{bucket}",),
        ).fetchone()
        if receipt is None:
            return {}
        version_key = "artifact_schema_version" if "artifact_schema_version" in receipt_columns else "schema_version"
        artifact = InstalledArtifact(
            artifact_id=str(receipt["artifact_id"]), owner_kind=str(receipt["owner_kind"]), owner_id=str(receipt["owner_id"]), role=str(receipt["role"]), schema_id=str(receipt["schema_id"]), schema_version=int(receipt[version_key]), relative_path=str(receipt["relative_path"]), storage_path=(root / str(receipt["relative_path"])).resolve(), content_sha256=str(receipt["content_sha256"]), size_bytes=int(receipt["size_bytes"]), row_count=int(receipt["row_count"]), column_schema_sha256=str(receipt["column_schema_sha256"]), media_type=str(receipt["media_type"])
        )
        artifact_cache[bucket] = {int(item["timestamp_ms"]): str(item["payload_json"]) for item in read_rows(artifact, root=root)}
        return artifact_cache[bucket]

    key = "timestamp_ms" if table == "raw_samples" else "bucket_ms"
    for index, row in enumerate(source.execute(f'SELECT * FROM "{table}" ORDER BY {key}')):
        raw_payload = str(row["payload_json"])
        if table == "raw_samples" and raw_payload == "{}" and row["staging_relative_path"] and root:
            staging = (root / str(row["staging_relative_path"])).resolve()
            staging.relative_to(root)
            try:
                raw_payload = staging.read_text(encoding="utf-8").splitlines()[int(row["staging_row_locator"])]
            except FileNotFoundError:
                raw_payload = raw_artifact_payloads((int(row["timestamp_ms"]) // 60_000) * 60_000).get(int(row["timestamp_ms"]), raw_payload)
                if raw_payload == "{}":
                    raise FileNotFoundError(f"missing telemetry staging and raw artifact for {row['timestamp_ms']}")
        payload_value = json_value(raw_payload)
        if isinstance(payload_value, dict) and payload_value.get("schema") in {"bms.scientific-artifact-reference.v1", "bms.scientific-artifact-row-reference.v1"}:
            payload_value = resolve_json_value(payload_value, root=root)
            raw_payload = json_text(payload_value)
        payload = payload_value if isinstance(payload_value, dict) else {}
        gpus = payload.get("gpus") if isinstance(payload.get("gpus"), list) else []
        result.append({"row_index": index, "timestamp_ms": int(payload.get("timestamp_ms", row[key] if table == "raw_samples" else 0)), "bucket_ms": int(row["bucket_ms"] if table == "minute_aggregates" else 0), "sample_count": int(row["sample_count"] if table == "minute_aggregates" else 1), "timestamp": str(payload.get("timestamp", "")), "payload_json": raw_payload, "cpu_utilization": float((payload.get("cpu") or {}).get("utilization", 0.0) or 0.0), "ram_utilization": float((payload.get("ram") or {}).get("utilization", 0.0) or 0.0), "gpu_utilization": [float((gpu or {}).get("utilization", 0.0) or 0.0) for gpu in gpus], "gpu_memory_used_mb": [float((gpu or {}).get("memory_used_mb", 0.0) or 0.0) for gpu in gpus], "gpu_names": [str((gpu or {}).get("name", "")) for gpu in gpus]})
    return result


def backfill_telemetry_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    root: Path,
    table: str,
    source_artifact_root: Path | None = None,
) -> int:
    rows = telemetry_rows(source, table, source_artifact_root=source_artifact_root)
    source_sha256 = digest_rows(rows)
    artifact = install_parquet_rows(root=root, owner_kind="telemetry", owner_id=table, role="history", schema_id="bms.telemetry-history.v1", schema_version=1, source_sha256=source_sha256, rows=rows, schema=TELEMETRY_SCHEMA)
    if read_rows(artifact, root=root) != rows:
        raise ValueError(f"telemetry {table} artifact equivalence failed")
    publish_telemetry_group(target, table=table, rows=rows, artifact=artifact, source_info=artifact_source_info(source_store="telemetry", source_table=table, source_column="payload_json", source_key=table, source_sha256=source_sha256), equivalence_sha256=source_sha256)
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--source-artifact-root", type=Path)
    parser.add_argument("--store", choices=["core", "telemetry"], required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.apply:
        raise SystemExit("refusing to mutate without --apply")
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    if args.store == "core":
        migrate_core_receipts(args.target)
    source = sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(args.target, timeout=60)
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA busy_timeout=60000")
    try:
        ensure_receipt_tables(target)
        if args.store == "core":
            counts = {"cm_records": backfill_cm_records(source, target, args.artifact_root), "frustrampnn_statistics": backfill_frustrampnn_statistics(source, target, args.artifact_root), "design_payload_rows": backfill_design_payloads(source, target, args.artifact_root), "frustrampnn_landscape_rows": backfill_frustrampnn_landscape(source, target, args.artifact_root), "cm_landscape_provenance_rows": backfill_cm_landscape_provenance(source, target, args.artifact_root)}
        else:
            counts = {"raw_samples": backfill_telemetry_table(source, target, args.artifact_root, "raw_samples", source_artifact_root=args.source_artifact_root or args.artifact_root), "minute_aggregates": backfill_telemetry_table(source, target, args.artifact_root, "minute_aggregates", source_artifact_root=args.source_artifact_root or args.artifact_root)}
        print(json.dumps(counts, sort_keys=True))
        return 0
    finally:
        target.close()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())

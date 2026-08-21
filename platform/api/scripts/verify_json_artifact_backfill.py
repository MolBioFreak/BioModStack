"""Verify a completed core JSON-to-Parquet backfill without mutating state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from services.scientific_artifacts.contracts import (
    ARTIFACT_ROW_REFERENCE_SCHEMA,
    canonical_json_bytes,
    require_artifact_reference,
    require_row_reference,
    reconstruct_envelope,
)
from services.scientific_artifacts.writer import read_rows
from scripts.migrate_json_payloads_to_artifacts import DESIGN_ARTIFACT_FIELDS


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(raw: Any) -> Any:
    if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
        return raw
    return json.loads(str(raw))


def verify_receipt(connection: sqlite3.Connection, reference: dict[str, Any], root: Path) -> dict[str, Any]:
    receipt = connection.execute(
        "SELECT content_sha256, size_bytes, row_count, relative_path FROM scientific_artifact_receipts WHERE artifact_id = ?",
        (reference["artifact_id"],),
    ).fetchone()
    if receipt is None:
        raise AssertionError(f"missing receipt {reference['artifact_id']}")
    path = root / str(receipt[3])
    if not path.is_file():
        raise AssertionError(f"missing artifact {path}")
    if path.stat().st_size != int(receipt[1]) or file_sha256(path) != receipt[0]:
        raise AssertionError(f"receipt mismatch {path}")
    if int(reference["row_count"]) != int(receipt[2]):
        raise AssertionError(f"reference/receipt row-count mismatch {reference['artifact_id']}")
    return {"path": path, "row_count": int(receipt[2])}


def verify_envelopes(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    root: Path,
    table: str,
    key_columns: tuple[str, ...],
    payload_column: str,
) -> int:
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    source_rows = source.execute(
        f"SELECT {', '.join(key_columns)}, {payload_column} FROM {table} WHERE {payload_column} IS NOT NULL ORDER BY {', '.join(key_columns)}"
    ).fetchall()
    count = 0
    for row in source_rows:
        where = " AND ".join(f"{column} = ?" for column in key_columns)
        target_row = target.execute(
            f"SELECT {payload_column} FROM {table} WHERE {where}",
            tuple(row[column] for column in key_columns),
        ).fetchone()
        if target_row is None:
            raise AssertionError(f"missing target row {table} {tuple(row[column] for column in key_columns)}")
        reference = dict(json_value(target_row[payload_column]))
        require_artifact_reference(reference)
        verify_receipt(target, reference, root)
        reconstructed = reconstruct_envelope(read_rows(reference, root=root))
        expected = json_value(row[payload_column])
        if canonical_json_bytes(reconstructed) != canonical_json_bytes(expected):
            raise AssertionError(f"envelope mismatch {table} {tuple(row[column] for column in key_columns)}")
        count += 1
    return count


def verify_frustrampnn_landscape(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    source_groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in source.execute("SELECT * FROM frustrampnn_landscape_rows ORDER BY parent_job_id, invocation_id, id"):
        source_groups[(str(row["parent_job_id"]), str(row["invocation_id"]))].append(row)
    checked = 0
    for group, source_rows in source_groups.items():
        target_rows = target.execute(
            "SELECT * FROM frustrampnn_landscape_rows WHERE parent_job_id = ? AND invocation_id = ? ORDER BY id",
            group,
        ).fetchall()
        if len(target_rows) != len(source_rows):
            raise AssertionError(f"landscape count mismatch {group}")
        refs = [dict(json_value(row["row_json"])) for row in target_rows]
        provenance_refs = [dict(json_value(row["provenance_json"])) for row in target_rows]
        if {ref["artifact_id"] for ref in refs} != {provenance_refs[0]["artifact_id"]}:
            raise AssertionError(f"landscape artifact identity mismatch {group}")
        for ref in refs + provenance_refs:
            require_row_reference(ref)
        reference = refs[0]
        receipt = verify_receipt(target, reference, root)
        artifact_rows = read_rows(reference, root=root)
        if len(artifact_rows) != len(source_rows):
            raise AssertionError(f"landscape artifact row count mismatch {group}")
        by_id = {str(row["id"]): row for row in artifact_rows}
        for source_row, target_row, row_ref, provenance_ref in zip(source_rows, target_rows, refs, provenance_refs, strict=True):
            artifact_row = by_id.get(str(source_row["id"]))
            if artifact_row is None:
                raise AssertionError(f"missing landscape artifact row {source_row['id']}")
            if int(artifact_row["row_index"]) != int(row_ref["row_locator"]):
                raise AssertionError(f"landscape row locator mismatch {source_row['id']}")
            if json_value(artifact_row["row_json"]) != json_value(source_row["row_json"]):
                raise AssertionError(f"landscape row payload mismatch {source_row['id']}")
            if json_value(artifact_row["provenance_json"]) != json_value(source_row["provenance_json"]):
                raise AssertionError(f"landscape provenance mismatch {source_row['id']}")
            if provenance_ref["artifact_id"] != reference["artifact_id"]:
                raise AssertionError(f"landscape provenance artifact mismatch {source_row['id']}")
            checked += 1
        if receipt["row_count"] != len(source_rows):
            raise AssertionError(f"receipt count mismatch {group}")
    return checked


def verify_cm_provenance(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in source.execute("SELECT id, request_id, candidate_id, provenance_json FROM conformational_mapping_landscape_rows ORDER BY request_id, candidate_id, id"):
        groups[(str(row["request_id"]), str(row["candidate_id"]))].append(row)
    checked = 0
    for group, source_rows in groups.items():
        target_rows = target.execute(
            "SELECT id, provenance_json FROM conformational_mapping_landscape_rows WHERE request_id = ? AND candidate_id = ? ORDER BY id",
            group,
        ).fetchall()
        if len(target_rows) != len(source_rows):
            raise AssertionError(f"CM provenance count mismatch {group}")
        first = dict(json_value(target_rows[0]["provenance_json"]))
        require_row_reference(first)
        verify_receipt(target, first, root)
        artifact_rows = read_rows(first, root=root)
        by_id = {str(row["row_id"]): row for row in artifact_rows}
        for source_row, target_row in zip(source_rows, target_rows, strict=True):
            reference = dict(json_value(target_row["provenance_json"]))
            require_row_reference(reference)
            artifact_row = by_id.get(str(source_row["id"]))
            if artifact_row is None or json_value(artifact_row["provenance_json"]) != json_value(source_row["provenance_json"]):
                raise AssertionError(f"CM provenance payload mismatch {source_row['id']}")
            checked += 1
    return checked


def design_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True).encode()


def verify_design_payloads(source: sqlite3.Connection, target: sqlite3.Connection, root: Path) -> int:
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    checked = 0
    for field_name in DESIGN_ARTIFACT_FIELDS:
        source_rows = source.execute(
            f'SELECT id, "{field_name}" FROM designs WHERE "{field_name}" IS NOT NULL ORDER BY id'
        ).fetchall()
        if not source_rows:
            continue
        target_rows = target.execute(
            f'SELECT id, "{field_name}" FROM designs WHERE "{field_name}" IS NOT NULL ORDER BY id'
        ).fetchall()
        if len(target_rows) != len(source_rows):
            raise AssertionError(f"Design field count mismatch {field_name}")
        refs = [dict(json_value(row[field_name])) for row in target_rows]
        for reference in refs:
            require_row_reference(reference)
        if len({ref["artifact_id"] for ref in refs}) != 1:
            raise AssertionError(f"Design field artifact identity mismatch {field_name}")
        artifact_rows = read_rows(refs[0], root=root)
        if len(artifact_rows) != len(source_rows):
            raise AssertionError(f"Design field artifact count mismatch {field_name}")
        by_id = {str(row["design_id"]): row for row in artifact_rows}
        verify_receipt(target, refs[0], root)
        for source_row, target_row, reference in zip(source_rows, target_rows, refs, strict=True):
            if str(source_row["id"]) != str(target_row["id"]):
                raise AssertionError(f"Design identity drift {field_name}")
            artifact_row = by_id.get(str(source_row["id"]))
            if artifact_row is None or artifact_row["field_name"] != field_name:
                raise AssertionError(f"Design artifact row missing {field_name}:{source_row['id']}")
            if int(artifact_row["row_index"]) != int(reference["row_locator"]):
                raise AssertionError(f"Design locator mismatch {field_name}:{source_row['id']}")
            if design_json_bytes(json_value(artifact_row["payload_json"])) != design_json_bytes(json_value(source_row[field_name])):
                raise AssertionError(f"Design payload mismatch {field_name}:{source_row['id']}")
            checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    source = sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(f"file:{args.target.resolve()}?mode=ro", uri=True)
    try:
        checks = {
            "cm_records": verify_envelopes(source, target, args.artifact_root, "conformational_mapping_records", ("id",), "payload_json"),
            "frustrampnn_statistics": verify_envelopes(source, target, args.artifact_root, "frustrampnn_results", ("parent_job_id", "invocation_id"), "statistics_json"),
            "frustrampnn_landscape_rows": verify_frustrampnn_landscape(source, target, args.artifact_root),
            "cm_landscape_provenance_rows": verify_cm_provenance(source, target, args.artifact_root),
            "design_payload_rows": verify_design_payloads(source, target, args.artifact_root),
        }
        quick = target.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
        refs = target.execute("SELECT COUNT(*) FROM scientific_artifact_receipts").fetchone()[0]
        migrations = target.execute("SELECT COUNT(*) FROM scientific_payload_migrations WHERE state = 'completed'").fetchone()[0]
        artifact_bytes = sum(path.stat().st_size for path in args.artifact_root.rglob("*.parquet"))
        print(json.dumps({
            "checks": checks,
            "receipt_count": refs,
            "completed_migration_count": migrations,
            "quick_check": quick,
            "foreign_key_errors": len(foreign_keys),
            "artifact_file_count": sum(1 for _ in args.artifact_root.rglob("*.parquet")),
            "artifact_bytes": artifact_bytes,
            "target_bytes": args.target.stat().st_size,
        }, sort_keys=True))
        if quick != "ok" or foreign_keys:
            raise AssertionError("target SQLite integrity failed")
        return 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())

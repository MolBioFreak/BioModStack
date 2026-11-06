#!/usr/bin/env python3
"""Retire bulky runtime inventories on inactive execution targets.

A remote worker's ``provider_metadata`` accumulates its full managed inventory,
preload ledger, critical-runtime manifest and artifact inventory. Retired
workers keep those records forever, so every query that touches
``execution_targets`` reads and parses hundreds of megabytes (Development:
715 MB across 68 retired rows). The row identity, endpoint, host-key binding and
small runtime binding stay; the bulk inventories are replaced by a compact
retirement record that states what was dropped and how large it was.

Job, attempt and result receipts are not touched. Nothing here applies to an
active target, a leased target, or a target with a nonterminal Job.

Default is a dry run. ``--apply`` requires ``--backup`` and takes the same
discipline as the rest of the maintenance scripts: back up first, verify
afterwards, then VACUUM separately with the API stopped.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BULK_METADATA_KEYS = (
    "artifact_inventory",
    "managed_inventory",
    "preload",
    "critical_runtime_manifest",
)
BULK_CAPABILITY_KEYS = ("critical_runtime",)
RETAINED_METADATA_KEYS = (
    "attachment",
    "critical_runtime_endpoint_sha256",
    "inventory",
    "job_progress",
    "managed_boot_id",
    "preload_artifact_summary",
    "preload_cached_artifact_count",
    "progress",
    "setup",
)
RETAINED_CAPABILITY_KEYS = (
    "critical_runtime_binding",
    "gpu_count",
    "gpu_name",
    "gpu_vram_mb",
    "nextflow_launcher_sha256",
    "provider_verified",
    "readiness",
    "runner_sha256",
)
TERMINAL_JOB_STATES = ("completed", "failed", "cancelled", "canceled", "awaiting_input")
RETIREMENT_SCHEMA = "bms.execution-target-retirement.v1"


def _json_object(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _bytes(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":")))


def compact_target(row: sqlite3.Row, retired_at: str) -> tuple[dict, dict, dict] | None:
    """Return (metadata, capabilities, report) for one retired row, or None."""
    metadata = _json_object(row["provider_metadata"])
    capabilities = _json_object(row["capabilities"])
    dropped_metadata = {key: _bytes(metadata[key]) for key in BULK_METADATA_KEYS if key in metadata}
    dropped_capabilities = {key: _bytes(capabilities[key]) for key in BULK_CAPABILITY_KEYS if key in capabilities}
    if not dropped_metadata and not dropped_capabilities:
        return None
    compact_metadata = {key: metadata[key] for key in RETAINED_METADATA_KEYS if key in metadata}
    compact_metadata["retired_runtime"] = {
        "schema": RETIREMENT_SCHEMA,
        "retired_at": retired_at,
        "reason": "inactive target: bulk runtime inventory is not a current worker record",
        "dropped_metadata_bytes": dropped_metadata,
        "dropped_capability_bytes": dropped_capabilities,
    }
    compact_capabilities = {key: capabilities[key] for key in RETAINED_CAPABILITY_KEYS if key in capabilities}
    report = {
        "id": row["id"],
        "state": row["state"],
        "before_bytes": _bytes(metadata) + _bytes(capabilities),
        "after_bytes": _bytes(compact_metadata) + _bytes(compact_capabilities),
        "dropped": sorted([*dropped_metadata, *dropped_capabilities]),
    }
    return compact_metadata, compact_capabilities, report


def select_retired(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = connection.execute(
        "SELECT id, state, active, leased_job_id, provider_metadata, capabilities FROM execution_targets"
    ).fetchall()
    selected = []
    for row in rows:
        if int(row["active"] or 0) != 0 or row["leased_job_id"]:
            continue
        live = connection.execute(
            "SELECT 1 FROM jobs WHERE execution_target_id = ? AND status NOT IN (%s) LIMIT 1"
            % ",".join("?" * len(TERMINAL_JOB_STATES)),
            (row["id"], *TERMINAL_JOB_STATES),
        ).fetchone()
        if live is not None:
            continue
        selected.append(row)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compact retired execution-target inventories.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="write the compaction (default: dry run)")
    parser.add_argument("--backup", type=Path, help="required with --apply: consistent SQLite backup path")
    args = parser.parse_args(argv)
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup")
    path = args.database.expanduser().resolve()
    if not path.is_file():
        parser.error(f"database not found: {path}")

    connection = sqlite3.connect(str(path), timeout=60)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=60000")
        retired_at = datetime.now(timezone.utc).isoformat()
        rows = select_retired(connection)
        plans = []
        for row in rows:
            plan = compact_target(row, retired_at)
            if plan is not None:
                plans.append((row["id"], *plan))
        before = sum(plan[3]["before_bytes"] for plan in plans)
        after = sum(plan[3]["after_bytes"] for plan in plans)
        summary = {
            "database": str(path),
            "candidates": len(rows),
            "compacted": len(plans),
            "before_mb": round(before / 1e6, 1),
            "after_mb": round(after / 1e6, 1),
            "applied": False,
        }
        if args.apply and plans:
            backup = args.backup.expanduser()
            target = sqlite3.connect(str(backup))
            connection.backup(target)
            target.close()
            summary["backup"] = str(backup)
            with connection:
                for identifier, metadata, capabilities, _report in plans:
                    connection.execute(
                        "UPDATE execution_targets SET provider_metadata = ?, capabilities = ? WHERE id = ?",
                        (json.dumps(metadata, separators=(",", ":")),
                         json.dumps(capabilities, separators=(",", ":")), identifier),
                    )
            remaining = connection.execute(
                "SELECT count(*) FROM execution_targets WHERE "
                "json_extract(provider_metadata, '$.retired_runtime.schema') IS NULL AND "
                "length(provider_metadata) + length(capabilities) > 1000000"
            ).fetchone()[0]
            summary["applied"] = True
            summary["uncompacted_bulk_rows"] = remaining
        print(json.dumps(summary, sort_keys=True))
        for _identifier, _metadata, _capabilities, report in plans[:5]:
            print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from inspect import signature
from typing import Callable, List
import sqlite3

from paths import get_db_path

from migrations.add_orchestrator_fields import run_migration as migrate_orchestrator_fields
from migrations.add_antibody_fields import run_migration as migrate_antibody_fields
from migrations.add_antibody_artifact_contract import migrate as migrate_antibody_artifact_contract
from migrations.add_cdr_length_columns import migrate as migrate_cdr_lengths
from migrations.add_rfd_rog import migrate as migrate_rfd_rog
from migrations.add_saved_selection_sets import migrate as migrate_saved_selection_sets
from migrations.add_external_result_imports import migrate as migrate_external_result_imports
from migrations.add_sequence_provenance import migrate as migrate_sequence_provenance
from migrations.add_conformational_mapping import migrate as migrate_conformational_mapping
from migrations.add_viewer_snapshots import migrate as migrate_viewer_snapshots
from migrations.add_state_landscape_analysis_projection import migrate as migrate_state_landscape_analysis_projection
from migrations.enforce_state_landscape_analysis_pair_row_integrity import (
    migrate as migrate_state_landscape_analysis_pair_row_integrity,
)
from migrations.add_state_landscape_analysis_page_order_index import (
    migrate as migrate_state_landscape_analysis_page_order_index,
)
from migrations.add_molbio_ngs_receipts import migrate as migrate_molbio_ngs_receipts
from migrations.add_approved_ngs_comparison_panels import migrate as migrate_approved_ngs_comparison_panels
from migrations.add_md_lifecycle import migrate as migrate_md_lifecycle
from migrations.add_ont_instrument_run_ledger import migrate as migrate_ont_instrument_run_ledger
from migrations.add_ont_protocol_preflight import migrate as migrate_ont_protocol_preflight
from migrations.add_ont_terminal_artifact_manifests import migrate as migrate_ont_terminal_artifact_manifests
from migrations.enforce_ont_terminal_artifact_manifest_immutability import (
    migrate as enforce_ont_terminal_artifact_manifest_immutability,
)
from migrations.sqlite_sha256 import register_sqlite_sha256
from run_migration import migrate as migrate_stage_tracking


@dataclass
class Migration:
    version: int
    name: str
    fn: Callable[..., None]


MIGRATIONS: List[Migration] = [
    Migration(1, "add_orchestrator_fields", migrate_orchestrator_fields),
    Migration(2, "add_antibody_fields", migrate_antibody_fields),
    Migration(3, "add_cdr_length_columns", migrate_cdr_lengths),
    Migration(4, "add_rfd_rog", migrate_rfd_rog),
    Migration(5, "add_sequence_provenance", migrate_sequence_provenance),
    Migration(6, "add_stage_tracking", migrate_stage_tracking),
    Migration(7, "add_antibody_artifact_contract", migrate_antibody_artifact_contract),
    Migration(8, "add_saved_selection_sets", migrate_saved_selection_sets),
    Migration(9, "add_external_result_imports", migrate_external_result_imports),
    Migration(10, "add_conformational_mapping", migrate_conformational_mapping),
    Migration(11, "add_viewer_snapshots", migrate_viewer_snapshots),
    Migration(12, "add_state_landscape_analysis_projection", migrate_state_landscape_analysis_projection),
    Migration(
        13,
        "enforce_state_landscape_analysis_pair_row_integrity",
        migrate_state_landscape_analysis_pair_row_integrity,
    ),
    Migration(
        14,
        "add_state_landscape_analysis_page_order_index",
        migrate_state_landscape_analysis_page_order_index,
    ),
    Migration(15, "add_molbio_ngs_receipts", migrate_molbio_ngs_receipts),
    Migration(16, "add_approved_ngs_comparison_panels", migrate_approved_ngs_comparison_panels),
    Migration(17, "add_md_lifecycle", migrate_md_lifecycle),
    Migration(18, "add_ont_instrument_run_ledger", migrate_ont_instrument_run_ledger),
    Migration(19, "add_ont_protocol_preflight", migrate_ont_protocol_preflight),
    Migration(20, "add_ont_terminal_artifact_manifests", migrate_ont_terminal_artifact_manifests),
    Migration(21, "enforce_ont_terminal_artifact_manifest_immutability", enforce_ont_terminal_artifact_manifest_immutability),
]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    register_sqlite_sha256(conn)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


_LEGACY_ONT_TARGET_VERSIONS = {
    "add_ont_instrument_run_ledger": 18,
    "add_ont_protocol_preflight": 19,
    "add_ont_terminal_artifact_manifests": 20,
    "enforce_ont_terminal_artifact_manifest_immutability": 21,
}


def _reconcile_legacy_ont_migration_versions(conn: sqlite3.Connection) -> None:
    """Move the unpublished ONT 17..20 ledger to 18..21 before MD owns version 17."""
    rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    by_name: dict[str, int] = {}
    by_version: dict[int, str] = {}
    for raw_version, raw_name in rows:
        version = int(raw_version)
        name = str(raw_name)
        if name in by_name:
            raise RuntimeError(f"duplicate schema migration name: {name}")
        by_name[name] = version
        by_version[version] = name

    moves: list[tuple[str, int, int]] = []
    migrating_names = set(_LEGACY_ONT_TARGET_VERSIONS)
    for name, target in _LEGACY_ONT_TARGET_VERSIONS.items():
        current = by_name.get(name)
        if current is None or current == target:
            continue
        if current != target - 1:
            raise RuntimeError(
                f"unexpected legacy migration version for {name}: {current}; expected {target - 1} or {target}"
            )
        occupant = by_version.get(target)
        if occupant is not None and occupant not in migrating_names:
            raise RuntimeError(
                f"cannot remap {name} to migration version {target}; occupied by {occupant}"
            )
        moves.append((name, current, target))

    if not moves:
        return

    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, current, target in moves:
            cursor = conn.execute(
                "UPDATE schema_migrations SET version = ? WHERE version = ? AND name = ?",
                (-target, current, name),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"failed to reserve remapped migration version for {name}")
        for name, _current, target in moves:
            cursor = conn.execute(
                "UPDATE schema_migrations SET version = ? WHERE version = ? AND name = ?",
                (target, -target, name),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"failed to publish remapped migration version for {name}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _run_migration(migration: Migration, db_path: str) -> None:
    """Use an explicit database for migrations that support it; preserve legacy no-argument migrations."""
    if "db_path" in signature(migration.fn).parameters:
        migration.fn(db_path)
    else:
        migration.fn()


def run_all(db_path: str | None = None) -> None:
    db_path = db_path or str(get_db_path())
    conn = _connect(db_path)
    try:
        _ensure_migrations_table(conn)
        _reconcile_legacy_ont_migration_versions(conn)
        applied = _get_applied_versions(conn)

        for mig in MIGRATIONS:
            if mig.version in applied:
                continue
            _run_migration(mig, db_path)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (mig.version, mig.name, datetime.utcnow().isoformat()),
            )
            conn.commit()
            print(f"[migrations] applied {mig.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    run_all()

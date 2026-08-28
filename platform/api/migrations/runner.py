from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from inspect import getmodule, signature
from pathlib import Path
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
from migrations.relax_shape_geometry_hash_uniqueness import (
    migrate as relax_shape_geometry_hash_uniqueness,
)
from migrations.sqlite_sha256 import register_sqlite_sha256
from migrations.add_frustrampnn_persistence import migrate as migrate_frustrampnn_persistence
from migrations.add_ngs_reference_sets import migrate as migrate_ngs_reference_sets
from migrations.add_pooled_ont_reference_assignment import migrate as migrate_pooled_ont_reference_assignment
from migrations.add_frustrampnn_statistics import migrate as migrate_frustrampnn_statistics
from migrations.add_frustrampnn_statistics_analyses import (
    migrate as migrate_frustrampnn_statistics_analyses,
)
from migrations.add_frustrampnn_statistics_claim_leases import (
    migrate as migrate_frustrampnn_statistics_claim_leases,
)
from migrations.add_frustrampnn_reviews import migrate as migrate_frustrampnn_reviews
from migrations.add_ont_raw_signal_ledger import migrate as migrate_ont_raw_signal_ledger
from migrations.add_ont_external_registration_identity import (
    migrate as migrate_ont_external_registration_identity,
)
from migrations.enforce_ont_external_registration_immutability import (
    migrate as migrate_ont_external_registration_immutability,
)
from migrations.seal_ont_external_source_identity import (
    migrate as seal_ont_external_source_identity,
)
from migrations.add_ont_signal_workbench import migrate as migrate_ont_signal_workbench
from migrations.add_ont_external_move_bam_receipts import (
    MIGRATION_33_TRIGGER_SQL_DIGESTS,
    migration_33_trigger_sql_digest,
    migrate as migrate_ont_external_move_bam_receipts,
)
from migrations.add_ont_move_source_attempt_lineage import (
    _OLD_COLUMNS as MIGRATION_33_SOURCE_COLUMNS,
    migrate as migrate_ont_move_source_attempt_lineage,
)
from migrations.add_scientific_artifact_receipts import migrate as migrate_scientific_artifact_receipts
from migrations.add_frustrampnn_landscape_index_slimming import migrate as migrate_frustrampnn_landscape_index_slimming
from migrations.retire_scientific_landscape_projections import migrate as migrate_retire_scientific_landscape_projections
from migrations.seal_ont_move_source_terminal_immutability import migrate as seal_ont_move_source_terminal_immutability
from migrations.seal_ont_external_move_bam_receipt_binding import migrate as seal_ont_external_move_bam_receipt_binding
from migrations.seal_ont_raw_signal_lookup_terminal_immutability import (
    migrate as seal_ont_raw_signal_lookup_terminal_immutability,
)
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
    Migration(22, "relax_shape_geometry_hash_uniqueness", relax_shape_geometry_hash_uniqueness),
    Migration(23, "add_frustrampnn_persistence", migrate_frustrampnn_persistence),
    Migration(24, "add_ngs_reference_sets", migrate_ngs_reference_sets),
    Migration(25, "add_pooled_ont_reference_assignment", migrate_pooled_ont_reference_assignment),
    Migration(26, "add_frustrampnn_statistics", migrate_frustrampnn_statistics),
    Migration(27, "add_frustrampnn_reviews", migrate_frustrampnn_reviews),
    Migration(28, "add_ont_raw_signal_ledger", migrate_ont_raw_signal_ledger),
    Migration(29, "add_ont_external_registration_identity", migrate_ont_external_registration_identity),
    Migration(30, "enforce_ont_external_registration_immutability", migrate_ont_external_registration_immutability),
    Migration(31, "seal_ont_external_source_identity", seal_ont_external_source_identity),
    Migration(32, "add_ont_signal_workbench", migrate_ont_signal_workbench),
    Migration(33, "add_ont_external_move_bam_receipts", migrate_ont_external_move_bam_receipts),
    Migration(34, "add_ont_move_source_attempt_lineage", migrate_ont_move_source_attempt_lineage),
    Migration(35, "add_scientific_artifact_receipts", migrate_scientific_artifact_receipts),
    Migration(36, "add_frustrampnn_landscape_index_slimming", migrate_frustrampnn_landscape_index_slimming),
    Migration(37, "retire_scientific_landscape_projections", migrate_retire_scientific_landscape_projections),
    Migration(38, "seal_ont_move_source_terminal_immutability", seal_ont_move_source_terminal_immutability),
    Migration(39, "seal_ont_external_move_bam_receipt_binding", seal_ont_external_move_bam_receipt_binding),
    Migration(40, "seal_ont_raw_signal_lookup_terminal_immutability", seal_ont_raw_signal_lookup_terminal_immutability),
    Migration(
        41,
        "add_frustrampnn_statistics_analyses",
        migrate_frustrampnn_statistics_analyses,
    ),
    Migration(
        42,
        "add_frustrampnn_statistics_claim_leases",
        migrate_frustrampnn_statistics_claim_leases,
    ),
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
            applied_at TEXT NOT NULL,
            content_sha256 TEXT
        )
        """
    )
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info('schema_migrations')")
    }
    if "content_sha256" not in columns:
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN content_sha256 TEXT")
    conn.commit()


_LEGACY_ONT_TARGET_VERSIONS = {
    "add_ont_instrument_run_ledger": 18,
    "add_ont_protocol_preflight": 19,
    "add_ont_terminal_artifact_manifests": 20,
    "enforce_ont_terminal_artifact_manifest_immutability": 21,
}


def _has_exact_legacy_ont_prefix(conn: sqlite3.Connection) -> bool:
    """Recognize only the unpublished contiguous ledger that needs MD insertion."""
    rows = [
        (int(version), str(name))
        for version, name in conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]
    canonical_prefix = [
        (migration.version, migration.name) for migration in MIGRATIONS[:16]
    ]
    legacy_tail = [
        (target - 1, name) for name, target in _LEGACY_ONT_TARGET_VERSIONS.items()
    ]
    if len(rows) <= len(canonical_prefix):
        return False
    expected = canonical_prefix + legacy_tail[: len(rows) - len(canonical_prefix)]
    return rows == expected


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
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at, content_sha256)
            VALUES (?, ?, ?, ?)
            """,
            (
                17,
                "add_md_lifecycle",
                datetime.utcnow().isoformat(),
                None,
            ),
        )
        _validate_applied_migration_identities(_get_applied_migrations(conn))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _get_applied_migrations(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, name FROM schema_migrations").fetchall()
    return {int(version): str(name) for version, name in rows}


def _validate_applied_migration_identities(applied: dict[int, str]) -> None:
    expected_by_version = {migration.version: migration.name for migration in MIGRATIONS}
    expected_by_name = {migration.name: migration.version for migration in MIGRATIONS}
    for version, name in applied.items():
        expected_name = expected_by_version.get(version)
        if expected_name is not None and name != expected_name:
            raise RuntimeError(
                f"schema migration version {version} is recorded as {name!r}; expected {expected_name!r}"
            )
        expected_version = expected_by_name.get(name)
        if expected_version is not None and version != expected_version:
            raise RuntimeError(
                f"schema migration {name!r} is recorded at version {version}; expected version {expected_version}"
            )
    expected_prefix = {
        migration.version: migration.name for migration in MIGRATIONS[: len(applied)]
    }
    if applied != expected_prefix:
        raise RuntimeError(
            "schema migration ledger must be a contiguous exact prefix of the registered migration history"
        )


def _migration_content_sha256(migration: Migration) -> str:
    module = getmodule(migration.fn)
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise RuntimeError(
            f"schema migration version {migration.version} has no readable module byte authority"
        )
    try:
        content = Path(module_file).read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"schema migration version {migration.version} module bytes are unreadable"
        ) from exc
    return hashlib.sha256(content).hexdigest()


_MIGRATION_33_CANONICAL_CONTENT_SHA256 = (
    "74892ab3d6747d40696d77df9ca3b7d2d6d0cd04a32edcc9638ea68087b0df28"
)
_MIGRATION_33_RECEIPT_TABLE_SQL = """
    CREATE TABLE ont_external_move_bam_registration_receipts (
        id VARCHAR(128) PRIMARY KEY NOT NULL,
        candidate_id VARCHAR(64) NOT NULL,
        run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
        observed_generation INTEGER NOT NULL,
        raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
        server_relative_path TEXT NOT NULL,
        root_device INTEGER NOT NULL,
        root_inode INTEGER NOT NULL,
        file_device INTEGER NOT NULL,
        file_inode INTEGER NOT NULL,
        file_mtime_ns INTEGER NOT NULL,
        file_ctime_ns INTEGER NOT NULL,
        artifact_sha256 VARCHAR(64) NOT NULL,
        artifact_size_bytes INTEGER NOT NULL CHECK (artifact_size_bytes > 0),
        molecule_type VARCHAR(16) NOT NULL CHECK (molecule_type IN ('dna','rna')),
        created_at VARCHAR NOT NULL,
        CONSTRAINT uq_ont_external_move_bam_registration
            UNIQUE (run_id, observed_generation, raw_representation_id, candidate_id, molecule_type)
    )
"""
_MIGRATION_33_RECEIPT_INDEX_SQL = """
    CREATE INDEX ix_ont_external_move_bam_registration_generation
        ON ont_external_move_bam_registration_receipts(run_id, observed_generation)
"""


def _normalized_schema_sql(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").split()).replace(
        " IF NOT EXISTS", "", 1
    )


def _attest_exact_migration_33_schema(conn: sqlite3.Connection) -> None:
    source_columns = tuple(
        str(row[1])
        for row in conn.execute("PRAGMA table_info('ont_move_table_sources')")
    )
    if source_columns != MIGRATION_33_SOURCE_COLUMNS:
        raise RuntimeError("migration 33 move-source predecessor schema diverged")
    receipt_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        ("ont_external_move_bam_registration_receipts",),
    ).fetchone()
    receipt_sql = "" if receipt_sql_row is None else str(receipt_sql_row[0] or "")
    if _normalized_schema_sql(receipt_sql) != _normalized_schema_sql(
        _MIGRATION_33_RECEIPT_TABLE_SQL
    ):
        raise RuntimeError("migration 33 receipt schema diverged")
    index_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        ("ix_ont_external_move_bam_registration_generation",),
    ).fetchone()
    index_sql = "" if index_sql_row is None else str(index_sql_row[0] or "")
    if _normalized_schema_sql(index_sql) != _normalized_schema_sql(
        _MIGRATION_33_RECEIPT_INDEX_SQL
    ):
        raise RuntimeError("migration 33 receipt index diverged")
    trigger_rows = conn.execute(
        """
        SELECT name, sql FROM sqlite_master
        WHERE type='trigger' AND tbl_name IN (
            'ont_external_move_bam_registration_receipts',
            'ont_move_table_sources'
        )
        """
    ).fetchall()
    trigger_sql = {str(name): str(sql or "") for name, sql in trigger_rows}
    prior_source_triggers = {
        "trg_ont_move_source_no_delete",
        "trg_ont_move_source_identity_no_update",
        "trg_ont_move_source_terminal_no_update",
    }
    if set(trigger_sql) != set(MIGRATION_33_TRIGGER_SQL_DIGESTS) | prior_source_triggers:
        raise RuntimeError("migration 33 trigger set diverged")
    observed_trigger_digests = {
        name: migration_33_trigger_sql_digest(trigger_sql[name])
        for name in MIGRATION_33_TRIGGER_SQL_DIGESTS
    }
    if observed_trigger_digests != MIGRATION_33_TRIGGER_SQL_DIGESTS:
        raise RuntimeError("migration 33 trigger authority diverged")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("migration 33 foreign-key authority diverged")


def _transition_exact_null_migration_33_checksum(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name, content_sha256 FROM schema_migrations WHERE version=33"
    ).fetchone()
    if row is None or row[1] is not None:
        return
    migration = next(
        (migration for migration in MIGRATIONS if migration.version == 33),
        None,
    )
    if migration is None:
        raise RuntimeError("registered migration 33 authority is unavailable")
    observed_checksum = _migration_content_sha256(migration)
    if observed_checksum != _MIGRATION_33_CANONICAL_CONTENT_SHA256:
        raise RuntimeError("registered migration 33 module bytes are not canonical")
    try:
        conn.execute("BEGIN IMMEDIATE")
        locked_row = conn.execute(
            "SELECT name, content_sha256 FROM schema_migrations WHERE version=33"
        ).fetchone()
        if locked_row != (migration.name, None):
            raise RuntimeError("migration 33 null-checksum transition authority diverged")
        _attest_exact_migration_33_schema(conn)
        cursor = conn.execute(
            """
            UPDATE schema_migrations SET content_sha256=?
            WHERE version=33 AND name=? AND content_sha256 IS NULL
            """,
            (observed_checksum, migration.name),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("migration 33 checksum transition lost atomic authority")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _validate_applied_migration_content(conn: sqlite3.Connection) -> None:
    expected_by_version = {migration.version: migration for migration in MIGRATIONS}
    rows = conn.execute(
        "SELECT version, content_sha256 FROM schema_migrations ORDER BY version"
    ).fetchall()
    for raw_version, raw_content_sha256 in rows:
        version = int(raw_version)
        migration = expected_by_version.get(version)
        if migration is None:
            continue
        recorded = None if raw_content_sha256 is None else str(raw_content_sha256)
        if recorded in {None, "legacy_unknown"}:
            if version >= 33:
                raise RuntimeError(
                    f"schema migration content checksum is missing for version {version}"
                )
            continue
        observed = _migration_content_sha256(migration)
        if recorded != observed:
            raise RuntimeError(
                f"schema migration content changed after application for version {version}"
            )


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
        if _has_exact_legacy_ont_prefix(conn):
            # Apply the additive MD schema before atomically publishing its ledger
            # identity alongside the ONT version remap. If this fails, the ledger
            # remains byte-for-byte unchanged and a retry is safe.
            _run_migration(MIGRATIONS[16], db_path)
        _reconcile_legacy_ont_migration_versions(conn)
        applied = _get_applied_migrations(conn)
        _validate_applied_migration_identities(applied)
        _transition_exact_null_migration_33_checksum(conn)
        _validate_applied_migration_content(conn)

        for mig in MIGRATIONS:
            if mig.version in applied:
                continue
            content_sha256 = _migration_content_sha256(mig)
            _run_migration(mig, db_path)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at, content_sha256)
                VALUES (?, ?, ?, ?)
                """,
                (
                    mig.version,
                    mig.name,
                    datetime.utcnow().isoformat(),
                    content_sha256,
                ),
            )
            conn.commit()
            print(f"[migrations] applied {mig.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    run_all()

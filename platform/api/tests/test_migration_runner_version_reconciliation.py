from __future__ import annotations

import sqlite3

import pytest

from migrations import runner

from migrations.runner import (
    MIGRATIONS,
    _ensure_migrations_table,
    _reconcile_legacy_ont_migration_versions,
)


LEGACY_ONT_ROWS = (
    (17, "add_ont_instrument_run_ledger"),
    (18, "add_ont_protocol_preflight"),
    (19, "add_ont_terminal_artifact_manifests"),
    (20, "enforce_ont_terminal_artifact_manifest_immutability"),
)


def _insert_rows(connection: sqlite3.Connection, rows: tuple[tuple[int, str], ...]) -> None:
    connection.executemany(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, 'legacy')",
        rows,
    )
    connection.commit()


def test_migration_versions_are_unique_with_md_before_ont() -> None:
    observed = [(migration.version, migration.name) for migration in MIGRATIONS if migration.version >= 17]
    assert observed == [
        (17, "add_md_lifecycle"),
        (18, "add_ont_instrument_run_ledger"),
        (19, "add_ont_protocol_preflight"),
        (20, "add_ont_terminal_artifact_manifests"),
        (21, "enforce_ont_terminal_artifact_manifest_immutability"),
        (22, "relax_shape_geometry_hash_uniqueness"),
        (23, "add_frustrampnn_persistence"),
        (24, "add_ngs_reference_sets"),
        (25, "add_pooled_ont_reference_assignment"),
    ]
    assert len({migration.version for migration in MIGRATIONS}) == len(MIGRATIONS)


def _canonical_prefix_through_16() -> tuple[tuple[int, str], ...]:
    return tuple((migration.version, migration.name) for migration in MIGRATIONS[:16])


def test_legacy_ont_17_to_20_ledger_is_transactionally_shifted_with_md_17() -> None:
    connection = sqlite3.connect(":memory:")
    _ensure_migrations_table(connection)
    _insert_rows(connection, _canonical_prefix_through_16() + LEGACY_ONT_ROWS)

    _reconcile_legacy_ont_migration_versions(connection)

    assert connection.execute(
        "SELECT version, name FROM schema_migrations WHERE version >= 17 ORDER BY version"
    ).fetchall() == [
        (17, "add_md_lifecycle"),
        (18, "add_ont_instrument_run_ledger"),
        (19, "add_ont_protocol_preflight"),
        (20, "add_ont_terminal_artifact_manifests"),
        (21, "enforce_ont_terminal_artifact_manifest_immutability"),
    ]


def test_full_runner_upgrades_complete_legacy_ont_history_to_canonical_v21(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "legacy-ont.db"
    connection = sqlite3.connect(db_path)
    _ensure_migrations_table(connection)
    _insert_rows(connection, _canonical_prefix_through_16() + LEGACY_ONT_ROWS)
    connection.close()

    migrations_through_v21 = [migration for migration in MIGRATIONS if migration.version <= 21]
    monkeypatch.setattr(runner, "MIGRATIONS", migrations_through_v21)
    runner.run_all(str(db_path))

    connection = sqlite3.connect(db_path)
    assert connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall() == [(migration.version, migration.name) for migration in migrations_through_v21]
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'md_runs'"
    ).fetchone() == (1,)
    connection.close()


def test_legacy_reconciliation_rolls_back_the_entire_ledger_on_validation_failure(monkeypatch) -> None:
    connection = sqlite3.connect(":memory:")
    _ensure_migrations_table(connection)
    original = _canonical_prefix_through_16() + LEGACY_ONT_ROWS
    _insert_rows(connection, original)
    monkeypatch.setattr(
        runner,
        "_validate_applied_migration_identities",
        lambda _applied: (_ for _ in ()).throw(RuntimeError("forced validation failure")),
    )

    with pytest.raises(RuntimeError, match="forced validation failure"):
        _reconcile_legacy_ont_migration_versions(connection)

    assert connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall() == list(original)


def test_legacy_ont_remap_fails_closed_and_rolls_back_on_unrelated_collision() -> None:
    connection = sqlite3.connect(":memory:")
    _ensure_migrations_table(connection)
    _insert_rows(
        connection,
        (
            (17, "add_ont_instrument_run_ledger"),
            (18, "unrelated_migration"),
        ),
    )

    with pytest.raises(RuntimeError, match="occupied by unrelated_migration"):
        _reconcile_legacy_ont_migration_versions(connection)

    assert connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall() == [
        (17, "add_ont_instrument_run_ledger"),
        (18, "unrelated_migration"),
    ]


def test_runner_fails_closed_when_applied_version_has_wrong_name(tmp_path) -> None:
    db_path = tmp_path / "wrong-migration-name.db"
    connection = sqlite3.connect(db_path)
    _ensure_migrations_table(connection)
    _insert_rows(
        connection,
        tuple(
            (migration.version, "unrelated_migration" if migration.version == 18 else migration.name)
            for migration in MIGRATIONS
        ),
    )
    connection.close()

    with pytest.raises(RuntimeError, match="version 18.*unrelated_migration.*add_ont_instrument_run_ledger"):
        runner.run_all(str(db_path))


@pytest.mark.parametrize(
    ("applied", "message"),
    [
        ({24: "unrelated_migration"}, "expected 'add_ngs_reference_sets'"),
        ({25: "unrelated_migration"}, "expected 'add_pooled_ont_reference_assignment'"),
        ({21: "enforce_ont_terminal_artifact_manifest_immutability"}, "contiguous exact prefix"),
    ],
)
def test_migration_ledger_must_be_an_exact_contiguous_known_prefix(
    applied: dict[int, str], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        runner._validate_applied_migration_identities(applied)


def test_v21_shape_schema_is_rebuilt_for_provenance_distinct_canonical_geometry(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shape-v21.db"
    connection = sqlite3.connect(db_path)
    _ensure_migrations_table(connection)
    _insert_rows(
        connection,
        tuple(
            (migration.version, migration.name)
            for migration in MIGRATIONS
            if migration.version < 22
        ),
    )
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE shape_cad_sources (
            source_id VARCHAR(40) PRIMARY KEY,
            source_sha256 VARCHAR(64) NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            relative_path VARCHAR(500) NOT NULL,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE shape_design_geometries (
            geometry_id VARCHAR(41) PRIMARY KEY,
            source_id VARCHAR(40) NOT NULL REFERENCES shape_cad_sources(source_id),
            geometry_sha256 VARCHAR(64) NOT NULL UNIQUE,
            conversion_sha256 VARCHAR(64) NOT NULL,
            angstrom_per_unit FLOAT NOT NULL,
            vertex_count INTEGER NOT NULL,
            face_count INTEGER NOT NULL,
            point_count INTEGER NOT NULL,
            manifest JSON NOT NULL,
            artifacts JSON NOT NULL,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_shape_geometry_conversion UNIQUE (source_id, conversion_sha256)
        );
        CREATE TABLE shape_design_requests (
            request_id VARCHAR(42) PRIMARY KEY,
            geometry_id VARCHAR(41) NOT NULL REFERENCES shape_design_geometries(geometry_id),
            request_sha256 VARCHAR(64) NOT NULL UNIQUE,
            request_spec JSON NOT NULL,
            stage_relative_path VARCHAR(500) NOT NULL,
            job_id VARCHAR(36),
            created_at DATETIME NOT NULL
        );
        CREATE INDEX ix_shape_design_geometries_source_id ON shape_design_geometries (source_id);
        CREATE UNIQUE INDEX ix_shape_design_geometries_geometry_sha256 ON shape_design_geometries (geometry_sha256);
        CREATE INDEX ix_shape_design_requests_geometry_id ON shape_design_requests (geometry_id);
        CREATE UNIQUE INDEX ix_shape_design_requests_request_sha256 ON shape_design_requests (request_sha256);
        CREATE UNIQUE INDEX ix_shape_design_requests_job_id ON shape_design_requests (job_id);
        INSERT INTO shape_cad_sources VALUES ('cad_a', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1, 'a.obj', 'sources/a', '2026-01-01');
        INSERT INTO shape_design_geometries VALUES ('geom_a', 'cad_a', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', 1.0, 4, 4, 16, '{}', '{}', '2026-01-01');
        INSERT INTO shape_design_requests VALUES ('shape_a', 'geom_a', 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', '{}', 'requests/a', NULL, '2026-01-01');
        """
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        runner,
        "MIGRATIONS",
        [migration for migration in MIGRATIONS if migration.version <= 22],
    )
    runner.run_all(str(db_path))

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        "INSERT INTO shape_design_geometries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ('geom_b', 'cad_a', 'b' * 64, 'e' * 64, 1.0, 4, 4, 16, '{}', '{}', '2026-01-02'),
    )
    assert connection.execute("SELECT geometry_id FROM shape_design_requests").fetchall() == [('geom_a',)]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone() == (22, "relax_shape_geometry_hash_uniqueness")
    connection.close()

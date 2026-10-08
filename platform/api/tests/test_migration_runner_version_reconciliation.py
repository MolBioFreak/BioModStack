from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from database import Base, _attest_sqlite_migration_33, _attest_sqlite_migration_34
from migrations import add_ont_external_move_bam_receipts as migration_33
from migrations import add_ont_move_source_attempt_lineage as migration_34
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
        (26, "add_frustrampnn_statistics"),
        (27, "add_frustrampnn_reviews"),
        (28, "add_ont_raw_signal_ledger"),
        (29, "add_ont_external_registration_identity"),
        (30, "enforce_ont_external_registration_immutability"),
        (31, "seal_ont_external_source_identity"),
        (32, "add_ont_signal_workbench"),
        (33, "add_ont_external_move_bam_receipts"),
        (34, "add_ont_move_source_attempt_lineage"),
        (35, "add_scientific_artifact_receipts"),
        (36, "add_frustrampnn_landscape_index_slimming"),
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
        "SELECT DISTINCT content_sha256 FROM schema_migrations ORDER BY content_sha256"
    ).fetchall() == [(None,)]
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


def test_runner_attests_migration_module_bytes_captured_before_execution(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "pre-execution-attestation.db"
    module_path = tmp_path / "mutable_migration.py"
    original_bytes = b"MIGRATION_CONTENT = 'reviewed'\n"
    changed_bytes = b"MIGRATION_CONTENT = 'mutated-during-execution'\n"
    module_path.write_bytes(original_bytes)

    def mutating_migration(db_path: str) -> None:
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE migration_side_effect (id INTEGER PRIMARY KEY)")
        module_path.write_bytes(changed_bytes)

    migration = runner.Migration(1, "mutating_migration", mutating_migration)
    monkeypatch.setattr(runner, "MIGRATIONS", [migration])
    monkeypatch.setattr(
        runner,
        "getmodule",
        lambda fn: SimpleNamespace(__file__=str(module_path)) if fn is mutating_migration else None,
    )

    runner.run_all(str(db_path))

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT content_sha256 FROM schema_migrations WHERE version = 1"
        ).fetchone() == (hashlib.sha256(original_bytes).hexdigest(),)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_side_effect'"
        ).fetchone() == ("migration_side_effect",)


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


@pytest.fixture(scope="module")
def exact_v33_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    database = tmp_path_factory.mktemp("migration-v33") / "exact-v33.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE ont_move_table_sources")
        connection.execute("DROP TABLE ont_external_move_bam_registration_receipts")
        connection.commit()
    runner._run_migration(MIGRATIONS[31], str(database))
    runner._run_migration(MIGRATIONS[32], str(database))
    with sqlite3.connect(database) as connection:
        _ensure_migrations_table(connection)
        connection.executemany(
            """
            INSERT INTO schema_migrations(version, name, applied_at, content_sha256)
            VALUES (?, ?, 'live-shaped-v33', ?)
            """,
            (
                (
                    migration.version,
                    migration.name,
                    None
                    if migration.version == 33
                    else runner._migration_content_sha256(migration),
                )
                for migration in MIGRATIONS
                if migration.version <= 33
            ),
        )
    return database


def _copy_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)


def _rebuild_receipt_table_without_constraint(database: Path, omission: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for trigger_name in migration_33.MIGRATION_33_TRIGGER_SQL:
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        table_name = "ont_external_move_bam_registration_receipts"
        old_table_name = f"{table_name}_old"
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        if omission == "unique":
            table_sql = table_sql.replace(
                ",\n                CONSTRAINT uq_ont_external_move_bam_registration\n"
                "                    UNIQUE (run_id, observed_generation, raw_representation_id, candidate_id, molecule_type)\n",
                "",
            )
        elif omission == "foreign_key":
            table_sql = table_sql.replace(
                "REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT",
                "",
            )
        elif omission == "check":
            table_sql = table_sql.replace("CHECK (artifact_size_bytes > 0)", "")
        else:
            raise AssertionError(omission)
        connection.execute(f"ALTER TABLE {table_name} RENAME TO {old_table_name}")
        connection.execute(table_sql)
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info('{old_table_name}')")]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f"INSERT INTO {table_name} ({quoted_columns}) SELECT {quoted_columns} FROM {old_table_name}"
        )
        connection.execute(f"DROP TABLE {old_table_name}")
        for trigger_sql in migration_33.MIGRATION_33_TRIGGER_SQL.values():
            connection.execute(trigger_sql)
        connection.commit()


def _rebuild_source_table_without_claim_token_unique(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for trigger_name in (*migration_34.MIGRATION_34_TRIGGER_SQL, *migration_33.MIGRATION_33_TRIGGER_SQL):
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        table_name = "ont_move_table_sources"
        old_table_name = f"{table_name}_old"
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        table_sql = table_sql.replace("claim_token VARCHAR(96) UNIQUE,", "claim_token VARCHAR(96),")
        connection.execute(f"ALTER TABLE {table_name} RENAME TO {old_table_name}")
        connection.execute(table_sql)
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info('{old_table_name}')")]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f"INSERT INTO {table_name} ({quoted_columns}) SELECT {quoted_columns} FROM {old_table_name}"
        )
        connection.execute(f"DROP TABLE {old_table_name}")
        migration_34._create_indexes_and_triggers(connection)
        connection.commit()


@pytest.mark.parametrize("omission", ("unique", "foreign_key", "check"))
def test_migration_33_attestation_rejects_missing_receipt_constraints(
    exact_v33_database: Path,
    tmp_path: Path,
    omission: str,
) -> None:
    database = tmp_path / f"missing-v33-{omission}.db"
    _copy_database(exact_v33_database, database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=33",
            (runner._migration_content_sha256(MIGRATIONS[32]),),
        )
        connection.commit()
    _rebuild_receipt_table_without_constraint(database, omission)

    with pytest.raises(RuntimeError, match="migration 33 startup attestation failed"):
        _attest_sqlite_migration_33(str(database))


def test_migration_34_attestation_rejects_missing_claim_token_unique_constraint(
    exact_v33_database: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-v34-claim-token-unique.db"
    _copy_database(exact_v33_database, database)
    runner.run_all(str(database))
    _rebuild_source_table_without_claim_token_unique(database)
    with sqlite3.connect(database) as connection:
        rebuilt_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ont_move_table_sources'"
        ).fetchone()[0]
    assert "claim_token VARCHAR(96) UNIQUE" not in rebuilt_sql

    with pytest.raises(RuntimeError, match="migration 34 startup attestation failed"):
        _attest_sqlite_migration_34(str(database))


def test_runner_truthfully_transitions_exact_null_v33_before_v34(
    exact_v33_database: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "upgrade.db"
    _copy_database(exact_v33_database, database)

    runner.run_all(str(database))

    expected = {
        migration.version: runner._migration_content_sha256(migration)
        for migration in MIGRATIONS
        if migration.version in {33, 34}
    }
    with sqlite3.connect(database) as connection:
        assert dict(
            connection.execute(
                "SELECT version, content_sha256 FROM schema_migrations WHERE version IN (33, 34)"
            )
        ) == expected
        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version=33"
        ).fetchone() == ("add_ont_external_move_bam_receipts",)


@pytest.mark.parametrize(
    "mutation",
    ("wrong_name", "missing_trigger", "tampered_trigger", "tampered_schema"),
)
def test_runner_rejects_null_v33_without_exact_name_schema_and_triggers(
    exact_v33_database: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / f"{mutation}.db"
    _copy_database(exact_v33_database, database)
    with sqlite3.connect(database) as connection:
        if mutation == "wrong_name":
            connection.execute(
                "UPDATE schema_migrations SET name='wrong_v33_name' WHERE version=33"
            )
        elif mutation == "missing_trigger":
            connection.execute(
                "DROP TRIGGER trg_ont_external_move_bam_receipt_no_update"
            )
        elif mutation == "tampered_trigger":
            connection.execute(
                "DROP TRIGGER trg_ont_external_move_bam_receipt_no_update"
            )
            connection.execute(
                """
                CREATE TRIGGER trg_ont_external_move_bam_receipt_no_update
                BEFORE UPDATE ON ont_external_move_bam_registration_receipts
                BEGIN SELECT RAISE(ABORT, 'tampered authority'); END
                """
            )
        else:
            connection.execute(
                "ALTER TABLE ont_external_move_bam_registration_receipts ADD COLUMN tampered TEXT"
            )

    with pytest.raises(RuntimeError):
        runner.run_all(str(database))
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT content_sha256 FROM schema_migrations WHERE version=33"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=34"
        ).fetchone() == (0,)


def test_runner_still_rejects_divergent_non_null_v33_checksum(
    exact_v33_database: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "divergent-v33.db"
    _copy_database(exact_v33_database, database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=33",
            ("0" * 64,),
        )

    with pytest.raises(RuntimeError, match="content changed after application for version 33"):
        runner.run_all(str(database))
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT content_sha256 FROM schema_migrations WHERE version=33"
        ).fetchone() == ("0" * 64,)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=34"
        ).fetchone() == (0,)

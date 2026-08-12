from __future__ import annotations

import importlib
from inspect import signature
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import database
from migrations import runner


TABLES = {
    "frustrampnn_results",
    "frustrampnn_artifacts",
    "frustrampnn_landscape_rows",
}

STATISTICS_COLUMNS = {
    "settings_sha256": "VARCHAR(64)",
    "effective_settings_sha256": "VARCHAR(64)",
    "effective_settings_json": "JSON",
    "capability_inventory_sha256": "VARCHAR(64)",
    "statistics_sha256": "VARCHAR(64)",
    "statistics_json": "JSON",
    "comparison_compatibility_id": "VARCHAR(64)",
}


def _migration_module():
    path = Path(__file__).resolve().parents[1] / "migrations" / "add_frustrampnn_persistence.py"
    assert path.is_file(), "FrustraMPNN persistence migration is missing"
    return importlib.import_module("migrations.add_frustrampnn_persistence")


def _statistics_migration_module():
    path = Path(__file__).resolve().parents[1] / "migrations" / "add_frustrampnn_statistics.py"
    assert path.is_file(), "FrustraMPNN statistics migration is missing"
    return importlib.import_module("migrations.add_frustrampnn_statistics")


def _legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                name VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                model_id VARCHAR(50) NOT NULL,
                mode VARCHAR(100) NOT NULL,
                params JSON NOT NULL
            );
            CREATE TABLE designs (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
                name VARCHAR(255) NOT NULL,
                pdb_path VARCHAR(500) NOT NULL,
                frustration_high_count INTEGER,
                frustration_min_count INTEGER,
                frustration_pct_high FLOAT,
                frustration_residues JSON,
                frustration_csv_path VARCHAR(500)
            );
            INSERT INTO jobs (id, name, status, model_id, mode, params)
            VALUES ('job-legacy', 'legacy', 'completed', 'boltz2', 'monomer', '{}');
            INSERT INTO designs (
                id, job_id, name, pdb_path, frustration_high_count,
                frustration_min_count, frustration_pct_high,
                frustration_residues, frustration_csv_path
            ) VALUES (
                'design-legacy', 'job-legacy', 'candidate-old', '/old/candidate.pdb',
                7, 11, 12.5, '[{"pos": 1}]', '/old/frustration.csv'
            );
            """
        )


def _v23_database(path: Path) -> None:
    _legacy_database(path)
    _migration_module().migrate(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO frustrampnn_results (
                parent_job_id, invocation_id, parent_workflow_id, candidate_id,
                design_id, requiredness, request_sha256, source_artifact_id,
                source_artifact_sha256, manifest_sha256, manifest_json,
                summary_sha256, summary_json, runtime_identity_json,
                assigned_gpu_json, terminal_result_json, parent_metadata_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-legacy", "invocation-v1", "workflow-v1", "candidate-old",
                "design-legacy", "required", "a" * 64, "source-v1",
                "b" * 64, "c" * 64, '{"schema":"manifest-v1"}',
                "d" * 64, '{"schema":"summary-v1"}', '{"tool":"frustrampnn"}',
                '{"gpu":0}', '{"status":"succeeded"}', '{"legacy":true}',
                "2026-08-08 12:34:56.123456",
            ),
        )
        connection.execute(
            """
            INSERT INTO frustrampnn_artifacts (
                artifact_id, parent_job_id, invocation_id, role, relative_path,
                storage_path, content_sha256, size_bytes, media_type,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "artifact-v1", "job-legacy", "invocation-v1", "landscape",
                "landscape.json", "/results/landscape.json", "e" * 64, 123,
                "application/json", '{"legacy":true}', "2026-08-08 12:35:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO frustrampnn_landscape_rows (
                id, parent_job_id, invocation_id, target_id, entity_instance_id,
                auth_asym_id, auth_seq_id, insertion_code, sequence_index, wt,
                mutation_aa, score, score_class, scoreable, status, reason,
                row_json, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "row-v1", "job-legacy", "invocation-v1", "target-v1", "entity-v1",
                "A", "10", "", 9, "G", "A", -0.25, "neutral", 1, "ok", None,
                '{"score":-0.25}', '{"source":"legacy"}',
            ),
        )


def _schema_signature(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, list[tuple]]:
    indexes = []
    for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        index_name = row[1]
        indexes.append(
            (
                index_name,
                row[2],
                row[3],
                tuple(
                    index_row[2]
                    for index_row in connection.execute(
                        f'PRAGMA index_info("{index_name}")'
                    ).fetchall()
                ),
            )
        )
    return {
        "columns": connection.execute(f'PRAGMA table_info("{table}")').fetchall(),
        "foreign_keys": connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall(),
        "indexes": sorted(indexes),
    }


def test_database_declares_dedicated_frustrampnn_models() -> None:
    for name in ("FrustraMPNNResult", "FrustraMPNNArtifact", "FrustraMPNNLandscapeRow"):
        assert hasattr(database, name), f"database.{name} is missing"


def test_migration_creates_required_tables_constraints_and_indexes(tmp_path: Path) -> None:
    database_path = tmp_path / "bms.db"
    _legacy_database(database_path)

    _migration_module().migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert TABLES <= tables
        design_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(designs)")
        }
        assert {
            "frustrampnn_contract_version", "frustrampnn_status",
            "frustrampnn_source_sha256", "frustrampnn_manifest_relpath",
            "frustrampnn_landscape_relpath", "frustrampnn_summary_relpath",
            "frustrampnn_runtime_sha256", "frustrampnn_failure_class",
            "frustrampnn_failure_detail",
        } <= design_columns

        result_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(frustrampnn_results)")
        }
        assert {
            "invocation_id", "parent_job_id", "parent_workflow_id", "candidate_id",
            "design_id", "requiredness", "request_sha256", "source_artifact_id",
            "source_artifact_sha256", "manifest_sha256", "manifest_json",
            "summary_sha256", "summary_json", "runtime_identity_json",
            "assigned_gpu_json", "terminal_result_json", "created_at",
        } <= result_columns.keys()
        assert result_columns["parent_job_id"][5] == 1
        assert result_columns["invocation_id"][5] == 2

        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(frustrampnn_artifacts)")
        }
        assert {
            "artifact_id", "parent_job_id", "invocation_id", "role", "relative_path", "storage_path",
            "content_sha256", "size_bytes", "media_type", "metadata_json", "created_at",
        } <= artifact_columns

        landscape_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(frustrampnn_landscape_rows)")
        }
        assert {
            "id", "parent_job_id", "invocation_id", "target_id", "entity_instance_id", "auth_asym_id",
            "auth_seq_id", "insertion_code", "sequence_index", "wt", "mutation_aa",
            "score", "score_class", "scoreable", "status", "reason", "row_json",
            "provenance_json",
        } <= landscape_columns

        result_fks = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(frustrampnn_results)")
        }
        assert ("parent_job_id", "jobs", "id") in result_fks
        assert ("design_id", "designs", "id") in result_fks
        artifact_fks = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(frustrampnn_artifacts)")
        }
        landscape_fks = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(frustrampnn_landscape_rows)")
        }
        assert ("invocation_id", "frustrampnn_results", "invocation_id") in artifact_fks
        assert ("parent_job_id", "frustrampnn_results", "parent_job_id") in artifact_fks
        assert ("invocation_id", "frustrampnn_results", "invocation_id") in landscape_fks
        assert ("parent_job_id", "frustrampnn_results", "parent_job_id") in landscape_fks

        result_indexes = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA index_list(frustrampnn_results)")
        }
        artifact_indexes = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA index_list(frustrampnn_artifacts)")
        }
        landscape_indexes = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA index_list(frustrampnn_landscape_rows)")
        }
        assert "ix_frustrampnn_results_job_candidate" in result_indexes
        assert result_indexes["uq_frustrampnn_results_job_invocation"] == 1
        assert artifact_indexes["uq_frustrampnn_artifacts_invocation_path"] == 1
        assert landscape_indexes["uq_frustrampnn_landscape_slot"] == 1
        assert "ix_frustrampnn_landscape_page_order" in landscape_indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_is_idempotent_and_preserves_legacy_frustration_data(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    _legacy_database(database_path)
    migration = _migration_module()

    migration.migrate(database_path)
    migration.migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """
            SELECT id, job_id, name, pdb_path, frustration_high_count,
                   frustration_min_count, frustration_pct_high,
                   frustration_residues, frustration_csv_path
            FROM designs
            """
        ).fetchall() == [
            (
                "design-legacy", "job-legacy", "candidate-old", "/old/candidate.pdb",
                7, 11, 12.5, '[{"pos": 1}]', "/old/frustration.csv",
            )
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_runner_registers_frustrampnn_reviews_as_version_27_last() -> None:
    identities = [(migration.version, migration.name) for migration in runner.MIGRATIONS]
    assert identities[-5:] == [
        (23, "add_frustrampnn_persistence"),
        (24, "add_ngs_reference_sets"),
        (25, "add_pooled_ont_reference_assignment"),
        (26, "add_frustrampnn_statistics"),
        (27, "add_frustrampnn_reviews"),
    ]
    assert [version for version, _name in identities] == sorted(
        version for version, _name in identities
    )
    assert len({version for version, _name in identities}) == len(identities)
    assert list(signature(runner.MIGRATIONS[-1].fn).parameters) == ["db_path"]


def test_v26_migration_preserves_v23_rows_constraints_dependencies_and_indexes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "frustrampnn-v23.db"
    _v23_database(database_path)
    with sqlite3.connect(database_path) as connection:
        result_before = _schema_signature(connection, "frustrampnn_results")
        dependencies_before = {
            table: _schema_signature(connection, table)
            for table in ("frustrampnn_artifacts", "frustrampnn_landscape_rows")
        }

    _statistics_migration_module().migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        result_after = _schema_signature(connection, "frustrampnn_results")
        dependencies_after = {
            table: _schema_signature(connection, table)
            for table in ("frustrampnn_artifacts", "frustrampnn_landscape_rows")
        }
        columns = {row[1]: row for row in result_after["columns"]}
        for name, sql_type in STATISTICS_COLUMNS.items():
            assert columns[name][2] == sql_type
            assert columns[name][3] == 0
            assert columns[name][4] is None
        assert [(row[1], row[5]) for row in result_after["columns"] if row[5]] == [
            ("parent_job_id", 1),
            ("invocation_id", 2),
        ]
        assert result_after["foreign_keys"] == result_before["foreign_keys"]
        assert result_after["indexes"] == result_before["indexes"]
        assert dependencies_after == dependencies_before
        assert connection.execute(
            """
            SELECT parent_job_id, invocation_id, candidate_id, design_id,
                   request_sha256, manifest_json, summary_json, parent_metadata_json,
                   created_at, settings_sha256, effective_settings_sha256,
                   effective_settings_json, capability_inventory_sha256,
                   statistics_sha256, statistics_json, comparison_compatibility_id
            FROM frustrampnn_results
            """
        ).fetchall() == [
            (
                "job-legacy", "invocation-v1", "candidate-old", "design-legacy",
                "a" * 64, '{"schema":"manifest-v1"}', '{"schema":"summary-v1"}',
                '{"legacy":true}', "2026-08-08 12:34:56.123456",
                None, None, None, None, None, None, None,
            )
        ]
        assert connection.execute(
            "SELECT artifact_id, content_sha256, created_at FROM frustrampnn_artifacts"
        ).fetchall() == [("artifact-v1", "e" * 64, "2026-08-08 12:35:00")]
        assert connection.execute(
            "SELECT id, score, row_json, provenance_json FROM frustrampnn_landscape_rows"
        ).fetchall() == [
            ("row-v1", -0.25, '{"score":-0.25}', '{"source":"legacy"}')
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v26_migration_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "frustrampnn-idempotent.db"
    _v23_database(database_path)
    migration = _statistics_migration_module()

    migration.migrate(database_path)
    with sqlite3.connect(database_path) as connection:
        first_columns = connection.execute(
            "PRAGMA table_info(frustrampnn_results)"
        ).fetchall()
    migration.migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "PRAGMA table_info(frustrampnn_results)"
        ).fetchall() == first_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM frustrampnn_results"
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fresh_sqlalchemy_schema_matches_nullable_v26_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "frustrampnn-fresh.db"
    sqlalchemy_engine = create_engine(f"sqlite:///{database_path}")
    try:
        database.Base.metadata.create_all(sqlalchemy_engine)
    finally:
        sqlalchemy_engine.dispose()

    model_columns = database.FrustraMPNNResult.__table__.columns
    with sqlite3.connect(database_path) as connection:
        sqlite_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(frustrampnn_results)")
        }
    for name, sql_type in STATISTICS_COLUMNS.items():
        assert name in model_columns
        assert model_columns[name].nullable is True
        assert str(model_columns[name].type) == sql_type
        assert sqlite_columns[name][2] == sql_type
        assert sqlite_columns[name][3] == 0
        assert sqlite_columns[name][4] is None


def test_runner_retries_v26_when_columns_exist_but_ledger_row_is_missing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "frustrampnn-ledger-gap.db"
    _v23_database(database_path)
    with sqlite3.connect(database_path) as connection:
        runner._ensure_migrations_table(connection)
        connection.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, 'fixture')",
            [
                (migration.version, migration.name)
                for migration in runner.MIGRATIONS
                if migration.version <= 23
            ],
        )
        connection.commit()

    _statistics_migration_module().migrate(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == (23,)

    runner.run_all(str(database_path))

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (migration.version, migration.name) for migration in runner.MIGRATIONS
        ]
        assert connection.execute(
            """
            SELECT settings_sha256, effective_settings_sha256,
                   effective_settings_json, capability_inventory_sha256,
                   statistics_sha256, statistics_json, comparison_compatibility_id
            FROM frustrampnn_results
            """
        ).fetchone() == (None, None, None, None, None, None, None)


def test_v26_migration_rolls_back_all_columns_on_foreign_key_violation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "frustrampnn-invalid-v23.db"
    _v23_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO frustrampnn_artifacts (
                artifact_id, parent_job_id, invocation_id, role, relative_path,
                storage_path, content_sha256, size_bytes, media_type,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "orphan-artifact", "missing-job", "missing-invocation", "landscape",
                "orphan.json", "/results/orphan.json", "f" * 64, 1,
                "application/json", "{}", "2026-08-08 12:36:00",
            ),
        )

    with pytest.raises(sqlite3.IntegrityError, match="foreign-key violations"):
        _statistics_migration_module().migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        column_names = {
            row[1] for row in connection.execute("PRAGMA table_info(frustrampnn_results)")
        }
        assert not STATISTICS_COLUMNS.keys() & column_names
        assert connection.execute(
            "SELECT parent_job_id, invocation_id FROM frustrampnn_results"
        ).fetchall() == [("job-legacy", "invocation-v1")]
        assert connection.execute(
            "SELECT artifact_id FROM frustrampnn_artifacts ORDER BY artifact_id"
        ).fetchall() == [("artifact-v1",), ("orphan-artifact",)]

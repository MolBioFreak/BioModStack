from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import database
from migrations import runner


TABLES = {
    "frustrampnn_results",
    "frustrampnn_artifacts",
    "frustrampnn_landscape_rows",
}


def _migration_module():
    path = Path(__file__).resolve().parents[1] / "migrations" / "add_frustrampnn_persistence.py"
    assert path.is_file(), "FrustraMPNN persistence migration is missing"
    return importlib.import_module("migrations.add_frustrampnn_persistence")


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


def test_runner_registers_frustrampnn_persistence_after_current_head() -> None:
    names = [migration.name for migration in runner.MIGRATIONS]
    assert names[-2:] == [
        "relax_shape_geometry_hash_uniqueness",
        "add_frustrampnn_persistence",
    ]
    versions = [migration.version for migration in runner.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))

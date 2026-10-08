from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def test_previous_schema_migrates_without_changing_job_status_or_provenance(tmp_path: Path) -> None:
    from migrations.add_ngs_alignment_presentation_jobs import migrate

    database = tmp_path / "bms.db"
    provenance = {"result_integrity": {"state": "validated", "artifact_set_sha256": "a" * 64}}
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE jobs (
                id VARCHAR(36) PRIMARY KEY,
                status VARCHAR(50) NOT NULL,
                queue_status VARCHAR(20) NOT NULL,
                provenance JSON
            )
            """
        )
        connection.execute(
            "INSERT INTO jobs(id,status,queue_status,provenance) VALUES (?,?,?,?)",
            ("job-1", "completed", "completed", json.dumps(provenance, sort_keys=True)),
        )
        connection.commit()

    migrate(database)
    migrate(database)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        columns = {
            str(row[1]) for row in connection.execute(
                "PRAGMA table_info(ngs_alignment_presentation_jobs)"
            )
        }
        indexes = {
            str(row[1]): bool(row[2]) for row in connection.execute(
                "PRAGMA index_list(ngs_alignment_presentation_jobs)"
            )
        }
        job = connection.execute(
            "SELECT status,queue_status,provenance FROM jobs WHERE id='job-1'"
        ).fetchone()
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(ngs_alignment_presentation_jobs)"
        ).fetchall()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert {
        "id", "job_id", "session_id", "mode", "source_authority_sha256",
        "source_manifest_sha256", "source_artifact_set_sha256", "policy_version",
        "state", "attempt_count", "manual_retry_count", "claim_token",
        "lease_expires_at", "authority_sha256", "manifest_sha256",
        "error_code", "created_at", "updated_at",
    } == columns
    assert indexes["uq_ngs_alignment_presentation_source"] is True
    assert any(row[2] == "jobs" and row[3] == "job_id" and row[4] == "id" for row in foreign_keys)
    assert job is not None
    assert job[:2] == ("completed", "completed")
    assert json.loads(job[2]) == provenance
    assert violations == []


def test_presentation_migration_is_registered_after_current_head() -> None:
    from migrations.runner import MIGRATIONS

    assert (MIGRATIONS[-1].version, MIGRATIONS[-1].name) == (
        46,
        "add_ngs_alignment_presentation_jobs",
    )

from __future__ import annotations

import sqlite3

from migrations.add_viewer_snapshots import migrate


def _legacy_database(path: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL)")
        connection.execute("INSERT INTO jobs (id) VALUES ('job-1')")
        connection.commit()


def test_viewer_snapshot_migration_is_idempotent_and_preserves_existing_jobs(tmp_path):
    database = tmp_path / "legacy.db"
    _legacy_database(str(database))

    migrate(str(database))
    migrate(str(database))

    with sqlite3.connect(database) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(viewer_snapshots)")}
        assert set(columns) == {
            "id", "job_id", "label", "created_by", "schema_version",
            "snapshot_sha256", "snapshot_json", "created_at",
        }
        assert columns["id"][5] == 1
        assert columns["job_id"][3] == 1
        assert connection.execute("SELECT id FROM jobs").fetchall() == [("job-1",)]
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(viewer_snapshots)")}
        assert "ix_viewer_snapshots_job_id" in indexes
        assert "ix_viewer_snapshots_snapshot_sha256" in indexes

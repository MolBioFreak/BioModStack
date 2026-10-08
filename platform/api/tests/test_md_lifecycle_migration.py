from __future__ import annotations

import sqlite3

from migrations.add_md_lifecycle import migrate


def _legacy_database(path: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL, name TEXT NOT NULL)")
        connection.execute("INSERT INTO jobs (id, name) VALUES ('job-1', 'preserved')")
        connection.commit()


def test_md_lifecycle_migration_is_idempotent_and_preserves_jobs(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    _legacy_database(str(database))

    migrate(str(database))
    migrate(str(database))

    expected_tables = {
        "md_runs", "md_replica_runs", "md_attempt_segments", "md_checkpoints",
        "job_artifacts", "md_events", "md_reconciler_leases",
    }
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert expected_tables <= tables
        assert connection.execute("SELECT id, name FROM jobs").fetchall() == [("job-1", "preserved")]
        active_index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_md_replica_active'"
        ).fetchone()
        assert active_index is not None and "WHERE active = 1" in active_index[0]
        event_indexes = list(connection.execute("PRAGMA index_list(md_events)"))
        assert any(
            row[2] == 1
            and [column[2] for column in connection.execute(f"PRAGMA index_info('{row[1]}')")] == ["idempotency_key"]
            for row in event_indexes
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

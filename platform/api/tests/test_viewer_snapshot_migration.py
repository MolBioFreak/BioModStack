from __future__ import annotations

from pathlib import Path
import sqlite3

from migrations.add_viewer_snapshots import migrate
from migrations import runner


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


def test_runner_passes_requested_database_to_path_aware_migration(tmp_path, monkeypatch):
    database = tmp_path / "runner.db"
    observed: list[str] = []

    def migration(db_path: str | None = None) -> None:
        observed.append(str(db_path))

    monkeypatch.setattr(runner, "MIGRATIONS", [runner.Migration(1, "probe", migration)])

    runner.run_all(str(database))

    assert observed == [str(database)]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version, name FROM schema_migrations").fetchall() == [(1, "probe")]


def test_api_image_runs_migrations_before_starting_uvicorn():
    dockerfile = (Path(__file__).resolve().parents[3] / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    assert "run_migrations.py" in command
    assert command.index("run_migrations.py") < command.index("uvicorn")


def test_native_api_migrates_before_server_exec():
    launcher = (Path(__file__).resolve().parents[3] / "scripts" / "run_biomodstack_api.sh").read_text(
        encoding="utf-8"
    )

    assert "run_migrations.py" in launcher
    assert launcher.index("run_migrations.py") < launcher.index("uvicorn")

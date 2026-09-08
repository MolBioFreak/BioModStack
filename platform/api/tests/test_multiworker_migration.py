"""Data-preserving upgrade through the production runner, isolated SQLite."""
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base, ExecutionTarget, Job
from migrations import runner

LEGACY_45_SHA256 = "4a7e8cc1b0708d5a8c8dce438fa970ddd821ba9c06f9de624192e43ecacb44d7"


def legacy_database(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ExecutionTarget(id="vast:1", provider="vast", provider_instance_id="1",
            active=True, state="ready", leased_job_id="old-job", provider_metadata={"evidence": "retain"}))
        session.add(Job(id="old-job", name="old-job", model_id="cpu-only", mode="run", params={"retain": True},
            execution_target_id="vast:1", remote_attempt_id="old-attempt", remote_state="start_uncertain",
            status="running", queue_status="running"))
        session.commit()
    engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE UNIQUE INDEX uq_execution_targets_one_active ON execution_targets(active) WHERE active = 1")
        connection.execute("UPDATE execution_targets SET lease_acquired_at='2026-09-01 00:00:00'")
        runner._ensure_migrations_table(connection)
        connection.executemany(
            "INSERT INTO schema_migrations(version,name,applied_at,content_sha256) VALUES (?,?,?,?)",
            [(m.version, m.name, "historical", LEGACY_45_SHA256 if m.version == 45 else runner._migration_content_sha256(m))
             for m in runner.MIGRATIONS if m.version <= 45])


def snapshot(connection):
    return {table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("execution_targets", "jobs")}


def test_runner_upgrades_old_database_without_rewriting_active_lease_jobs_or_ledger(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        before = snapshot(connection)
        ledger = connection.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        indexes = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND name != 'uq_execution_targets_one_active' ORDER BY name").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO execution_targets(id,provider,provider_instance_id,state,active,remote_root,capabilities,pricing,provider_metadata,created_at,updated_at) SELECT 'vast:2',provider,'2',state,1,remote_root,capabilities,pricing,provider_metadata,created_at,updated_at FROM execution_targets")
    runner.run_all(str(path))
    runner.run_all(str(path))
    with sqlite3.connect(path) as connection:
        assert snapshot(connection) == before
        assert connection.execute("SELECT * FROM schema_migrations WHERE version <= 45 ORDER BY version").fetchall() == ledger
        assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name").fetchall() == indexes
        assert connection.execute("SELECT name FROM schema_migrations WHERE version=46").fetchone() == ("enable_multiple_execution_targets",)
        connection.execute("INSERT INTO execution_targets(id,provider,provider_instance_id,state,active,remote_root,capabilities,pricing,provider_metadata,created_at,updated_at) SELECT 'vast:2',provider,'2',state,1,remote_root,capabilities,pricing,provider_metadata,created_at,updated_at FROM execution_targets")
        assert connection.execute("SELECT COUNT(*) FROM execution_targets WHERE active=1").fetchone() == (2,)
        assert connection.execute("SELECT leased_job_id,lease_acquired_at FROM execution_targets WHERE id='vast:2'").fetchone() == (None, None)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # Startup attestation is independent of the migration runner; both must
    # accept historical ledger bytes after the forward-only migration.
    from database import _attest_sqlite_migration_ledger
    _attest_sqlite_migration_ledger(str(path))
    assert runner._migration_content_sha256(runner.MIGRATIONS[44]) == LEGACY_45_SHA256


@pytest.mark.parametrize("tamper", ["recorded", "module"])
def test_legacy_checksum_exception_is_exact_not_a_general_bypass(tmp_path, monkeypatch, tamper):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    if tamper == "recorded":
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE schema_migrations SET content_sha256=? WHERE version=45", ("f" * 64,))
    else:
        original = runner._migration_content_sha256
        monkeypatch.setattr(runner, "_migration_content_sha256", lambda m: "f" * 64 if m.version == 45 else original(m))
    with pytest.raises(RuntimeError, match="content changed.*45"):
        runner.run_all(str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='uq_execution_targets_one_active'").fetchone()
        assert connection.execute("SELECT version FROM schema_migrations WHERE version=46").fetchone() is None

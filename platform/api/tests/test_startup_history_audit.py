"""Real isolated SQLite evidence for startup versus explicit integrity owners."""
import importlib
import sqlite3
import time

import pytest


@pytest.mark.parametrize("name", ["experiment", "molbio_ngs"])
def test_reopen_schema_only_and_full_audit_defaults(tmp_path, monkeypatch, name):
    migrations = importlib.import_module(f"{name}_migrations")
    path = tmp_path / f"{name}.db"
    traces = []
    connect = migrations._connect

    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(traces.append)
        return connection

    monkeypatch.setattr(migrations, "_connect", traced)
    migrations.run_all(path, verify_data=False)
    assert any(sql.upper() == "PRAGMA FOREIGN_KEY_CHECK" for sql in traces)
    with connect(path) as connection:
        if name == "experiment":
            connection.executemany(
                "INSERT INTO resources(id, kind, created_at) VALUES (?, 'workspace', '2026-01-01')",
                [(f"project-{i}",) for i in range(2000)],
            )
        else:
            from molbio_ngs_managed_fixture import seed_migrated_domain
            for i in range(100):
                seed_migrated_domain(connection, f"domain-{i}", f"revision-{i}")
        connection.commit()
        before = list(connection.iterdump())
    measurements = {}
    for verify_data in (False, True):
        traces.clear()
        start = time.perf_counter()
        migrations.run_all(path, verify_data=verify_data)
        measurements[str(verify_data)] = time.perf_counter() - start
        checks = sum(sql.upper() == "PRAGMA FOREIGN_KEY_CHECK" for sql in traces)
        assert checks == int(verify_data)
    with connect(path) as connection:
        assert list(connection.iterdump()) == before
        report = migrations.attest_schema(connection, verify_data=False)
        assert report["ok"] and report["data_integrity_checked"] is False
        assert report["foreign_key_errors"] is None
    print(f"{name} populated reopen seconds {measurements}")
    traces.clear()
    migrations.run_all(path)  # Explicit callers retain the full audit by default.
    assert sum(sql.upper() == "PRAGMA FOREIGN_KEY_CHECK" for sql in traces) == 1


def test_experiment_corrupt_history_detected_by_full_audit(tmp_path):
    import experiment_migrations as migrations
    path = tmp_path / "experiments.db"
    migrations.run_all(path)
    # Deliberate offline corruption; never disable constraints on a live store.
    with sqlite3.connect(path) as connection:
        triggers = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='resources'"
        ).fetchall()
        for name, _sql in triggers:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('orphan', 'dataset', 'missing', 'missing', '2026-01-01')"
        )
        for _name, sql in triggers:
            connection.execute(sql)
    migrations.run_all(path, verify_data=False)
    report = migrations.health(path)["attestation"]
    assert isinstance(report, dict) and report["foreign_key_errors"]
    with pytest.raises(sqlite3.IntegrityError, match="schema attestation failed"):
        migrations.run_all(path)


@pytest.mark.parametrize("name", ["experiment", "molbio_ngs"])
def test_schema_only_reopen_still_rejects_ledger_and_definition_drift(tmp_path, name):
    migrations = importlib.import_module(f"{name}_migrations")
    path = tmp_path / f"{name}.db"
    migrations.run_all(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unexpected_history(id TEXT)")
    with pytest.raises(sqlite3.IntegrityError, match="schema attestation failed"):
        migrations.run_all(path, verify_data=False)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE unexpected_history")
        connection.execute(f"UPDATE {name}_schema_migrations SET checksum='corrupt'")
    with pytest.raises(RuntimeError, match="ledger mismatch"):
        migrations.run_all(path, verify_data=False)


@pytest.mark.parametrize("corrupt", [False, True])
def test_ngs_v3_upgrade_always_verifies_history(tmp_path, monkeypatch, corrupt):
    import molbio_ngs_migrations as migrations
    path = tmp_path / "v3.db"
    with migrations._connect(path) as connection:
        connection.execute(migrations.LEDGER_SQL)
        for version, name, checksum, description, sql in migrations._migration_registry()[:3]:
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO molbio_ngs_schema_migrations VALUES (?, ?, ?, ?, ?)",
                (version, name, checksum, description, "2026-01-01"),
            )
            connection.commit()
    if corrupt:
        with sqlite3.connect(path) as connection:
            triggers = connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='molbio_ngs_domain_states'"
            ).fetchall()
            for name, _sql in triggers:
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.execute(
                "INSERT INTO molbio_ngs_domain_states VALUES ('orphan', 'missing', 1, 'now', 'now')"
            )
            for _name, sql in triggers:
                connection.execute(sql)
    audits = []
    attest = migrations.attest_schema

    def record(connection, **kwargs):
        audits.append(kwargs["verify_data"])
        return attest(connection, **kwargs)

    monkeypatch.setattr(migrations, "attest_schema", record)
    if corrupt:
        with pytest.raises(sqlite3.IntegrityError):
            migrations.run_all(path, verify_data=False)
    else:
        migrations.run_all(path, verify_data=False)
        migrations.run_all(path, verify_data=False)
        assert audits == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["experiment", "molbio_ngs"])
async def test_api_init_uses_schema_only_reopen(tmp_path, monkeypatch, name):
    database = importlib.import_module(f"{name}_database")
    path = tmp_path / f"{name}.db"
    monkeypatch.setattr(database, f"get_{name}_db_url", lambda: f"sqlite+aiosqlite:///{path}")
    calls = []
    run_all = database.run_all

    def record(path, **kwargs):
        calls.append(kwargs)
        return run_all(path, **kwargs)

    monkeypatch.setattr(database, "run_all", record)
    await getattr(database, f"init_{name}_db")()
    await getattr(database, f"init_{name}_db")()
    assert calls == [{"verify_data": False}, {"verify_data": False}]

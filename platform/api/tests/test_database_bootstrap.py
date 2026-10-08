"""Real SQLite acceptance for the supported first-install migration lifecycle."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

API = Path(__file__).resolve().parents[1]


def environment(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("BMS_") and key != "DATABASE_URL"}
    env.update(HOME=str(tmp_path), BMS_HOME=str(API.parents[1]),
               BMS_DATA=str(tmp_path), BMS_DB_PATH=str(tmp_path / "core.db"))
    return env


def run(tmp_path, code=None):
    args = [sys.executable, "-c", code] if code else [sys.executable, "run_migrations.py"]
    return subprocess.run(args, cwd=API, env=environment(tmp_path),
                          capture_output=True, text=True, timeout=90)


ATTEST = '''
import asyncio
from sqlalchemy import select
from database import Base, engine, init_db
async def check():
    await init_db()
    async with engine.connect() as conn:
        for table in Base.metadata.tables.values():
            await conn.execute(select(table).limit(1))
    await engine.dispose()
asyncio.run(check())
'''


def test_full_clean_lifecycle_and_repeat(tmp_path):
    first = run(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    result = run(tmp_path, ATTEST)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(tmp_path / "core.db") as conn:
        ledger = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in ledger] == list(range(1, 47))
        assert all(len(row[3]) == 64 for row in ledger)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (name,) in tables:
            if name != "schema_migrations":
                assert conn.execute(f'SELECT count(*) FROM "{name}"').fetchone() == (0,)
    repeated = run(tmp_path)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    with sqlite3.connect(tmp_path / "core.db") as conn:
        assert conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == ledger


@pytest.mark.parametrize("tamper", [
    "UPDATE schema_migrations SET content_sha256='changed' WHERE version=34",
    "DROP TRIGGER trg_ont_move_source_no_delete",
])
def test_existing_authority_tampering_still_fails(tmp_path, tamper):
    assert run(tmp_path).returncode == 0
    with sqlite3.connect(tmp_path / "core.db") as conn:
        conn.execute(tamper)
    # The lifecycle cannot repair authority; either migration checks or startup
    # attestation must reject it, without replacing the missing/changed evidence.
    migrated = run(tmp_path)
    attested = run(tmp_path, ATTEST)
    assert migrated.returncode != 0 or attested.returncode != 0


def test_explicit_split_database_path_rejected(tmp_path):
    result = run(tmp_path, '''
from pathlib import Path
from paths import get_db_path
from database_bootstrap import migrate_database
other = Path(get_db_path()).with_name('other.db')
try:
    migrate_database(str(other))
except ValueError:
    pass
else:
    raise AssertionError('split database accepted')
assert not other.exists()
''')
    assert result.returncode == 0, result.stderr


def test_concurrent_clean_lifecycle(tmp_path):
    processes = [subprocess.Popen([sys.executable, "run_migrations.py"], cwd=API,
                 env=environment(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 text=True) for _ in range(2)]
    for process in processes:
        out, err = process.communicate(timeout=90)
        assert process.returncode == 0, out + err
    assert run(tmp_path, ATTEST).returncode == 0


def test_existing_unknown_is_not_bootstrapped(tmp_path):
    with sqlite3.connect(tmp_path / "core.db") as conn:
        conn.execute("CREATE TABLE unknown(value TEXT)")
        conn.execute("INSERT INTO unknown VALUES ('preserve')")
    result = run(tmp_path)
    assert result.returncode != 0
    with sqlite3.connect(tmp_path / "core.db") as conn:
        assert conn.execute("SELECT * FROM unknown").fetchall() == [("preserve",)]
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchall()


def test_committed_base_resumes_without_fabricated_ledger(tmp_path):
    result = run(tmp_path, '''
from pathlib import Path
from paths import get_db_path
from database_bootstrap import _create_base_if_empty
_create_base_if_empty(Path(get_db_path()))
''')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(tmp_path / "core.db") as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchall()
    assert run(tmp_path).returncode == 0
    assert run(tmp_path, ATTEST).returncode == 0


@pytest.mark.parametrize("version", [1, 33, 34])
def test_interrupted_migration_replays_actual_content(tmp_path, version):
    result = run(tmp_path, f'''
import os
import migrations.runner as runner
from database_bootstrap import migrate_database
original = runner._run_migration
def interrupted(migration, path):
    original(migration, path)
    if migration.version == {version}:
        os._exit(73)
runner._run_migration = interrupted
migrate_database()
''')
    assert result.returncode == 73, result.stdout + result.stderr
    with sqlite3.connect(tmp_path / "core.db") as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == (version - 1 or None)
    resumed = run(tmp_path)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert run(tmp_path, ATTEST).returncode == 0


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_redirected_or_nonregular_migration_lock_is_rejected(tmp_path, kind):
    other = tmp_path / "unrelated"
    other.write_text("preserve")
    lock = tmp_path / "core.db.migration.lock"
    if kind == "symlink":
        lock.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, lock)
    else:
        os.mkfifo(lock)
    result = run(tmp_path)
    assert result.returncode != 0
    assert other.read_text() == "preserve"
    assert not (tmp_path / "core.db").exists()


def test_bootstrap_ddl_rolls_back_on_failure(tmp_path):
    result = run(tmp_path, '''
from pathlib import Path
from paths import get_db_path
import database_bootstrap as bootstrap
bootstrap.BASE_TABLES = ('jobs', 'not_a_table')
try:
    bootstrap._create_base_if_empty(Path(get_db_path()))
except KeyError:
    pass
else:
    raise AssertionError('expected failure')
''')
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(tmp_path / "core.db") as conn:
        assert not conn.execute("SELECT name FROM sqlite_master").fetchall()
    assert run(tmp_path).returncode == 0

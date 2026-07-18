"""Owned engine, session, migrations, and health checks for Mol Bio SQLite."""
from __future__ import annotations

import asyncio
import fcntl
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from molbio_models import IMMUTABLE_TABLES, MolBioBase
from paths import get_data_root


MOLBIO_BUSY_TIMEOUT_MS = 30_000
Migration = tuple[str, str, Callable[[AsyncConnection], Awaitable[None]]]


def get_molbio_path() -> Path:
    """Resolve the independently configurable canonical Mol Bio database path."""

    configured = os.getenv("BMS_MOLBIO_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (get_data_root() / "molbio.db").resolve()


def get_molbio_database_url(path: Path | None = None) -> str:
    target = (path or get_molbio_path()).expanduser().resolve()
    return f"sqlite+aiosqlite:///{target}"


def _sqlite_path_from_url(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def create_molbio_engine(database_url: str | None = None) -> AsyncEngine:
    """Create a Mol Bio engine with invariants installed on every connection."""

    resolved_url = database_url or get_molbio_database_url()
    parsed = make_url(resolved_url)
    if not parsed.drivername.startswith("sqlite"):
        raise ValueError("The canonical Mol Bio store currently supports SQLite only")

    target = _sqlite_path_from_url(resolved_url)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        resolved_url,
        echo=False,
        connect_args={"timeout": MOLBIO_BUSY_TIMEOUT_MS / 1000},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={MOLBIO_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


def make_molbio_session_factory(target_engine: AsyncEngine):
    return sessionmaker(target_engine, class_=AsyncSession, expire_on_commit=False)


async def _migration_initial(connection: AsyncConnection) -> None:
    await connection.run_sync(MolBioBase.metadata.create_all)


async def _migration_append_only_guards(connection: AsyncConnection) -> None:
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Mol Bio append-only guards are defined for SQLite")
    for table_name in IMMUTABLE_TABLES:
        for action in ("UPDATE", "DELETE"):
            trigger_name = f"molbio_immutable_{table_name}_{action.lower()}"
            await connection.execute(
                text(
                    f'''CREATE TRIGGER IF NOT EXISTS "{trigger_name}"
                    BEFORE {action} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END'''
                )
            )


async def _migration_idempotency_and_soft_delete(connection: AsyncConnection) -> None:
    """Add request binding and soft-delete projection columns to existing stores."""

    columns: dict[str, set[str]] = {}
    for table_name in ("primers", "molecular_operations", "pcr_experiment_revisions"):
        rows = await connection.execute(text(f'PRAGMA table_info("{table_name}")'))
        columns[table_name] = {str(row[1]) for row in rows.fetchall()}
    if "deleted_at" not in columns["primers"]:
        await connection.execute(text("ALTER TABLE primers ADD COLUMN deleted_at DATETIME"))
    if "request_fingerprint" not in columns["molecular_operations"]:
        await connection.execute(
            text("ALTER TABLE molecular_operations ADD COLUMN request_fingerprint VARCHAR(64)")
        )
    if "request_fingerprint" not in columns["pcr_experiment_revisions"]:
        await connection.execute(
            text("ALTER TABLE pcr_experiment_revisions ADD COLUMN request_fingerprint VARCHAR(64)")
        )


async def _migration_sequence_parent_foreign_key(connection: AsyncConnection) -> None:
    """Rebuild the sequence projection with a real self-FK for parent lineage."""

    fk_rows = (
        await connection.execute(text("PRAGMA foreign_key_list(nucleotide_sequences)"))
    ).fetchall()
    if any(
        str(row[2]) == "nucleotide_sequences"
        and str(row[3]) == "parent_id"
        and str(row[4]) == "id"
        and str(row[6]).upper() == "RESTRICT"
        for row in fk_rows
    ):
        return

    orphan_rows = (
        await connection.execute(
            text(
                "SELECT child.id, child.parent_id FROM nucleotide_sequences child "
                "LEFT JOIN nucleotide_sequences parent ON parent.id = child.parent_id "
                "WHERE child.parent_id IS NOT NULL AND parent.id IS NULL ORDER BY child.id"
            )
        )
    ).fetchall()
    if orphan_rows:
        raise RuntimeError(
            "Cannot add nucleotide sequence parent foreign key while orphan lineage exists: "
            f"{[(str(row[0]), str(row[1])) for row in orphan_rows]}"
        )

    schema_sql = (
        await connection.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='nucleotide_sequences'"
            )
        )
    ).scalar_one_or_none()
    if not schema_sql or not str(schema_sql).rstrip().endswith(")"):
        raise RuntimeError("Cannot safely rebuild nucleotide_sequences: schema SQL is unavailable")

    original_sql = str(schema_sql).rstrip()
    replacement_sql = re.sub(
        r"^CREATE\s+TABLE\s+(?:\"nucleotide_sequences\"|`nucleotide_sequences`|"
        r"\[nucleotide_sequences\]|nucleotide_sequences)",
        "CREATE TABLE nucleotide_sequences_with_parent_fk",
        original_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if replacement_sql == original_sql:
        raise RuntimeError("Cannot safely rebuild nucleotide_sequences: unexpected CREATE TABLE SQL")
    replacement_sql = (
        replacement_sql[:-1]
        + ",\n\tFOREIGN KEY(parent_id) REFERENCES nucleotide_sequences_with_parent_fk (id) "
        "ON DELETE RESTRICT\n)"
    )

    await connection.execute(text(replacement_sql))
    await connection.execute(
        text("INSERT INTO nucleotide_sequences_with_parent_fk SELECT * FROM nucleotide_sequences")
    )
    await connection.execute(text("DROP TABLE nucleotide_sequences"))
    await connection.execute(
        text("ALTER TABLE nucleotide_sequences_with_parent_fk RENAME TO nucleotide_sequences")
    )

    violations = (await connection.execute(text("PRAGMA foreign_key_check"))).fetchall()
    if violations:
        raise RuntimeError(
            "Cannot safely rebuild nucleotide_sequences: foreign-key verification failed"
        )


MOLBIO_MIGRATIONS: tuple[Migration, ...] = (
    ("0001_initial", "create Mol Bio owned schema", _migration_initial),
    ("0002_append_only_guards", "enforce append-only scientific history", _migration_append_only_guards),
    (
        "0003_idempotency_and_soft_delete",
        "bind idempotency requests and preserve deleted primer history",
        _migration_idempotency_and_soft_delete,
    ),
    (
        "0004_sequence_parent_foreign_key",
        "enforce sequence parent lineage with a restricting self foreign key",
        _migration_sequence_parent_foreign_key,
    ),
)


_memory_migration_lock = asyncio.Lock()


@asynccontextmanager
async def _migration_lock(engine: AsyncEngine):
    """Serialize schema migration across engines and processes for one SQLite file."""

    target = _sqlite_path_from_url(str(engine.url))
    if target is None:
        async with _memory_migration_lock:
            yield
        return
    lock_path = target.with_suffix(target.suffix + ".migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.01)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


async def run_molbio_migrations(*, engine: AsyncEngine) -> list[str]:
    """Apply ordered, idempotent Mol Bio migrations and return applied versions."""

    async with _migration_lock(engine), engine.connect() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.execute(text(f"PRAGMA busy_timeout={MOLBIO_BUSY_TIMEOUT_MS}"))
        await connection.commit()

        async with connection.begin():
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS molbio_schema_migrations (
                        version VARCHAR(64) PRIMARY KEY NOT NULL,
                        description VARCHAR(255) NOT NULL,
                        applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        rows = await connection.execute(
            text("SELECT version FROM molbio_schema_migrations ORDER BY version")
        )
        applied = {str(row[0]) for row in rows.fetchall()}
        await connection.commit()
        known = {version for version, _description, _apply in MOLBIO_MIGRATIONS}
        unknown = applied - known
        if unknown:
            raise RuntimeError(f"Mol Bio database has unknown migrations: {sorted(unknown)}")

        for version, description, apply_migration in MOLBIO_MIGRATIONS:
            if version in applied:
                continue
            rebuilds_parent_table = version == "0004_sequence_parent_foreign_key"
            if rebuilds_parent_table:
                # SQLite cannot toggle FK enforcement inside a transaction. Its documented
                # table-rebuild procedure requires FK enforcement off while the old parent
                # table is replaced; the migration itself performs foreign_key_check before
                # the migration ledger is committed.
                await connection.execute(text("PRAGMA foreign_keys=OFF"))
                await connection.commit()
            try:
                async with connection.begin():
                    await apply_migration(connection)
                    await connection.execute(
                        text(
                            "INSERT INTO molbio_schema_migrations(version, description, applied_at) "
                            "VALUES (:version, :description, CURRENT_TIMESTAMP)"
                        ),
                        {"version": version, "description": description},
                    )
            finally:
                if rebuilds_parent_table:
                    await connection.execute(text("PRAGMA foreign_keys=ON"))
                    await connection.commit()
                    if int(
                        (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
                    ) != 1:
                        raise RuntimeError(
                            "Foreign-key enforcement could not be restored after Mol Bio migration"
                        )
                    await connection.commit()
            applied.add(version)

    return sorted(applied)


async def init_molbio_db(*, engine: AsyncEngine | None = None) -> list[str]:
    return await run_molbio_migrations(engine=engine or molbio_engine)


async def get_applied_molbio_migrations(*, engine: AsyncEngine | None = None) -> list[str]:
    target_engine = engine or molbio_engine
    async with target_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT version FROM molbio_schema_migrations ORDER BY version")
        )
        return [str(row[0]) for row in rows.fetchall()]


async def molbio_health(*, engine: AsyncEngine | None = None) -> dict[str, object]:
    target_engine = engine or molbio_engine
    expected_versions = [version for version, _description, _apply in MOLBIO_MIGRATIONS]
    try:
        async with target_engine.connect() as connection:
            quick_check = str((await connection.execute(text("PRAGMA quick_check"))).scalar_one())
            foreign_key_rows = (await connection.execute(text("PRAGMA foreign_key_check"))).fetchall()
            migrations = (
                await connection.execute(
                    text("SELECT version FROM molbio_schema_migrations ORDER BY version")
                )
            ).fetchall()
            trigger_rows = (
                await connection.execute(
                    text(
                        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger' "
                        "AND name LIKE 'molbio_immutable_%'"
                    )
                )
            ).fetchall()
        versions = [str(row[0]) for row in migrations]
        migrations_current = versions == expected_versions
        expected_triggers = {
            f"molbio_immutable_{table_name}_{operation.lower()}": (table_name, operation)
            for table_name in IMMUTABLE_TABLES
            for operation in ("UPDATE", "DELETE")
        }
        observed_triggers = {str(row[0]): row for row in trigger_rows}
        triggers_current = set(observed_triggers) == set(expected_triggers) and all(
            str(observed_triggers[name][1]) == table_name
            and f"BEFORE {operation}" in " ".join(
                str(observed_triggers[name][2] or "").upper().split()
            )
            and f"RAISE(ABORT, '{table_name.upper()} IS IMMUTABLE')" in " ".join(
                str(observed_triggers[name][2] or "").upper().split()
            )
            for name, (table_name, operation) in expected_triggers.items()
        )
        healthy = (
            quick_check.lower() == "ok"
            and not foreign_key_rows
            and migrations_current
            and triggers_current
        )
        return {
            "owner": "molbio",
            "database_kind": "sqlite",
            "status": "healthy" if healthy else "degraded",
            "quick_check": quick_check,
            "foreign_key_violations": len(foreign_key_rows),
            "migration_count": len(versions),
            "latest_migration": versions[-1] if versions else None,
            "migrations_current": migrations_current,
            "immutable_trigger_count": len(trigger_rows),
            "immutable_triggers_current": triggers_current,
        }
    except Exception as exc:  # health endpoints must report, not propagate
        return {
            "owner": "molbio",
            "database_kind": "sqlite",
            "status": "degraded",
            "quick_check": "error",
            "foreign_key_violations": None,
            "migration_count": 0,
            "latest_migration": None,
            "migrations_current": False,
            "immutable_trigger_count": 0,
            "immutable_triggers_current": False,
            "error": f"{type(exc).__name__}: database health query failed",
        }


molbio_engine = create_molbio_engine()
molbio_session = make_molbio_session_factory(molbio_engine)


async def get_molbio_session() -> AsyncIterator[AsyncSession]:
    async with molbio_session() as session:
        yield session

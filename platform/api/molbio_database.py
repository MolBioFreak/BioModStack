"""Owned engine, session, migrations, and health checks for Mol Bio SQLite."""
from __future__ import annotations

import asyncio
from collections import Counter
import fcntl
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence

from sqlalchemy import UniqueConstraint, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from molbio_models import IMMUTABLE_TABLES, MolBioBase, MolecularImportBatch, ProjectPlasmidMetadata
from paths import get_data_root
from services.ngs_molbio_quiescence import NgsMolBioQuiescedSession


MOLBIO_BUSY_TIMEOUT_MS = 30_000
Migration = tuple[str, str, Callable[[AsyncConnection], Awaitable[None]]]


def _immutable_trigger_sql(
    table_name: str,
    action: str,
    *,
    if_not_exists: bool,
) -> str:
    trigger_name = f"molbio_immutable_{table_name}_{action.lower()}"
    existence_guard = " IF NOT EXISTS" if if_not_exists else ""
    return f'''CREATE TRIGGER{existence_guard} "{trigger_name}"
                    BEFORE {action} ON "{table_name}"
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is immutable');
                    END'''


def _normalize_sql(sql: str) -> str:
    """Normalize insignificant formatting while retaining every SQL token."""

    return " ".join(sql.strip().rstrip(";").split()).casefold()


def _expected_immutable_triggers() -> dict[str, tuple[str, str]]:
    return {
        f"molbio_immutable_{table_name}_{operation.lower()}": (
            table_name,
            _normalize_sql(
                _immutable_trigger_sql(
                    table_name,
                    operation,
                    if_not_exists=False,
                )
            ),
        )
        for table_name in IMMUTABLE_TABLES
        for operation in ("UPDATE", "DELETE")
    }


def _immutable_triggers_are_current(rows: Iterable[Sequence[object]]) -> bool:
    expected = _expected_immutable_triggers()
    observed = {str(row[0]): row for row in rows}
    return set(observed) == set(expected) and all(
        str(observed[name][1]) == table_name
        and _normalize_sql(str(observed[name][2] or "")) == expected_sql
        for name, (table_name, expected_sql) in expected.items()
    )


def _foreign_key_signature(payload: Mapping[str, Any]) -> tuple[object, ...]:
    options = payload.get("options")
    normalized_options = options if isinstance(options, Mapping) else {}
    return (
        tuple(str(column) for column in payload.get("constrained_columns") or ()),
        str(payload.get("referred_table") or ""),
        tuple(str(column) for column in payload.get("referred_columns") or ()),
        str(normalized_options.get("ondelete") or "").upper(),
        str(normalized_options.get("onupdate") or "").upper(),
    )


def _expected_foreign_key_signatures(table) -> Counter[tuple[object, ...]]:  # noqa: ANN001
    signatures: Counter[tuple[object, ...]] = Counter()
    for constraint in table.foreign_key_constraints:
        elements = list(constraint.elements)
        signatures[
            (
                tuple(str(element.parent.name) for element in elements),
                str(elements[0].column.table.name),
                tuple(str(element.column.name) for element in elements),
                str(constraint.ondelete or "").upper(),
                str(constraint.onupdate or "").upper(),
            )
        ] += 1
    return signatures


def _molbio_schema_issues_sync(sync_connection) -> list[str]:  # noqa: ANN001
    """Return structural drift from the SQLAlchemy-owned SQLite contract."""

    inspector = inspect(sync_connection)
    actual_tables = set(inspector.get_table_names())
    issues: list[str] = []
    for table_name, table in MolBioBase.metadata.tables.items():
        if table_name not in actual_tables:
            issues.append(f"required table missing: {table_name}")
            continue

        actual_columns = {str(column["name"]): column for column in inspector.get_columns(table_name)}
        expected_columns = {str(column.name): column for column in table.columns}
        for column_name, expected_column in expected_columns.items():
            actual_column = actual_columns.get(column_name)
            if actual_column is None:
                issues.append(f"required column missing: {table_name}.{column_name}")
                continue
            if not expected_column.primary_key and bool(actual_column.get("nullable")) != bool(expected_column.nullable):
                issues.append(f"column nullability differs: {table_name}.{column_name}")
        unexpected_columns = sorted(set(actual_columns).difference(expected_columns))
        if unexpected_columns:
            issues.append(f"unexpected columns in {table_name}: {unexpected_columns}")

        expected_foreign_keys = _expected_foreign_key_signatures(table)
        actual_foreign_keys = Counter(
            _foreign_key_signature(foreign_key)
            for foreign_key in inspector.get_foreign_keys(table_name)
        )
        if actual_foreign_keys != expected_foreign_keys:
            issues.append(f"foreign keys differ: {table_name}")

        actual_indexes = {
            (
                str(index.get("name") or ""),
                tuple(str(column) for column in index.get("column_names") or ()),
                bool(index.get("unique")),
            )
            for index in inspector.get_indexes(table_name)
        }
        for index in table.indexes:
            expected_index = (
                str(index.name or ""),
                tuple(str(column.name) for column in index.columns),
                bool(index.unique),
            )
            if expected_index not in actual_indexes:
                issues.append(f"required index differs or is missing: {table_name}.{index.name}")

        expected_unique_columns = {
            tuple(str(column.name) for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_unique_columns = {
            tuple(str(column) for column in constraint.get("column_names") or ())
            for constraint in inspector.get_unique_constraints(table_name)
        }
        missing_unique = expected_unique_columns.difference(actual_unique_columns)
        if missing_unique:
            issues.append(
                f"required unique constraint differs or is missing: {table_name} {sorted(missing_unique)}"
            )
    return issues


async def _molbio_schema_issues(connection: AsyncConnection) -> list[str]:
    return await connection.run_sync(_molbio_schema_issues_sync)


def _without_sequence_parent_foreign_keys(create_table_sql: str) -> str:
    """Remove table-level and inline FK clauses that involve ``parent_id``.

    SQLite cannot alter a foreign key in place.  Rebuilds therefore preserve all
    top-level table clauses except parent-lineage constraints, then append the one
    authoritative standalone self-FK.  Splitting only on top-level commas avoids
    confusing composite column lists or commas inside quoted defaults.
    """

    opening = create_table_sql.find("(")
    closing = create_table_sql.rfind(")")
    if opening < 0 or closing <= opening:
        raise RuntimeError("Cannot safely rebuild nucleotide_sequences: malformed CREATE TABLE SQL")

    body = create_table_sql[opening + 1:closing]
    clauses: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(body):
        character = body[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "[":
            quote = "]"
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise RuntimeError(
                    "Cannot safely rebuild nucleotide_sequences: malformed CREATE TABLE SQL"
                )
        elif character == "," and depth == 0:
            clauses.append(body[start:index])
            start = index + 1
        index += 1
    if quote is not None or depth != 0:
        raise RuntimeError("Cannot safely rebuild nucleotide_sequences: malformed CREATE TABLE SQL")
    clauses.append(body[start:])

    retained: list[str] = []
    for clause in clauses:
        foreign_key = re.search(
            r"\bFOREIGN\s+KEY\s*\(([^)]*)\)",
            clause,
            flags=re.IGNORECASE,
        )
        if foreign_key:
            local_columns = {
                column.strip().strip('"`[]').casefold()
                for column in foreign_key.group(1).split(",")
            }
            if "parent_id" in local_columns:
                continue
        inline_parent_column = re.match(
            r'^\s*(?:"parent_id"|`parent_id`|\[parent_id\]|parent_id)(?:\s|$)',
            clause,
            flags=re.IGNORECASE,
        )
        if inline_parent_column and re.search(
            r"\bREFERENCES\b",
            clause,
            flags=re.IGNORECASE,
        ):
            identifier = r'(?:"(?:""|[^"])+"|`(?:``|[^`])+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)'
            inline_reference = (
                r"\s+REFERENCES\s+" + identifier
                + r"\s*(?:\(\s*" + identifier + r"\s*\))?"
                + r"(?:\s+(?:ON\s+(?:DELETE|UPDATE)\s+"
                + r"(?:SET\s+(?:NULL|DEFAULT)|CASCADE|RESTRICT|NO\s+ACTION)"
                + r"|MATCH\s+" + identifier + r"))*"
                + r"(?:\s+(?:NOT\s+)?DEFERRABLE"
                + r"(?:\s+INITIALLY\s+(?:DEFERRED|IMMEDIATE))?)?"
            )
            clause, replacements = re.subn(
                inline_reference,
                "",
                clause,
                count=1,
                flags=re.IGNORECASE,
            )
            if replacements != 1 or re.search(
                r"\bREFERENCES\b",
                clause,
                flags=re.IGNORECASE,
            ):
                raise RuntimeError(
                    "Cannot safely rebuild nucleotide_sequences: unsupported inline parent foreign key"
                )
        retained.append(clause)

    return create_table_sql[:opening + 1] + ",".join(retained) + create_table_sql[closing:]


def _has_sequence_parent_foreign_key(
    rows: Iterable[Sequence[object]],
) -> bool:
    constraints: dict[object, list[Sequence[object]]] = {}
    for row in rows:
        if len(row) < 7:
            continue
        constraints.setdefault(row[0], []).append(row)

    parent_constraints = [
        constraint_rows
        for constraint_rows in constraints.values()
        if any(str(row[3]) == "parent_id" for row in constraint_rows)
    ]
    if len(parent_constraints) != 1 or len(parent_constraints[0]) != 1:
        return False

    row = parent_constraints[0][0]
    return (
        str(row[1]) == "0"
        and str(row[2]) == "nucleotide_sequences"
        and str(row[3]) == "parent_id"
        and str(row[4]) == "id"
        and str(row[5]).upper() == "NO ACTION"
        and str(row[6]).upper() == "RESTRICT"
    )


def _sequence_parent_cycles(
    rows: Iterable[Sequence[object]],
) -> list[tuple[str, ...]]:
    """Return each cycle in a single-parent lineage graph exactly once."""

    parents = {
        str(row[0]): str(row[1]) if row[1] is not None else None
        for row in rows
    }
    complete: set[str] = set()
    cycles: set[tuple[str, ...]] = set()
    for origin in sorted(parents):
        trail: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = origin
        while current is not None and current in parents and current not in complete:
            if current in positions:
                cycle = trail[positions[current]:]
                rotations = [
                    tuple(cycle[index:] + cycle[:index])
                    for index in range(len(cycle))
                ]
                cycles.add(min(rotations))
                break
            positions[current] = len(trail)
            trail.append(current)
            current = parents[current]
        complete.update(trail)
    return sorted(cycles)


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
    return sessionmaker(
        target_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        sync_session_class=NgsMolBioQuiescedSession,
    )


async def _migration_initial(connection: AsyncConnection) -> None:
    await connection.run_sync(MolBioBase.metadata.create_all)


async def _migration_append_only_guards(connection: AsyncConnection) -> None:
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Mol Bio append-only guards are defined for SQLite")
    for table_name in IMMUTABLE_TABLES:
        for action in ("UPDATE", "DELETE"):
            trigger_name = f"molbio_immutable_{table_name}_{action.lower()}"
            await connection.execute(text(f'DROP TRIGGER IF EXISTS "{trigger_name}"'))
            await connection.execute(
                text(_immutable_trigger_sql(table_name, action, if_not_exists=False))
            )
    trigger_rows = (
        await connection.execute(
            text(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'molbio_immutable_%'"
            )
        )
    ).fetchall()
    if not _immutable_triggers_are_current(trigger_rows):
        raise RuntimeError(
            "Cannot enforce append-only scientific history: immutable trigger postcondition failed"
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
    lineage_rows = (
        await connection.execute(
            text("SELECT id, parent_id FROM nucleotide_sequences ORDER BY id")
        )
    ).fetchall()
    parent_cycles = _sequence_parent_cycles(lineage_rows)
    if parent_cycles:
        raise RuntimeError(
            "Cannot add nucleotide sequence parent foreign key while cyclic lineage exists: "
            f"{parent_cycles}"
        )
    if _has_sequence_parent_foreign_key(fk_rows):
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
    replacement_sql = _without_sequence_parent_foreign_keys(replacement_sql)
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

    repaired_fk_rows = (
        await connection.execute(text("PRAGMA foreign_key_list(nucleotide_sequences)"))
    ).fetchall()
    if not _has_sequence_parent_foreign_key(repaired_fk_rows):
        raise RuntimeError(
            "Cannot safely rebuild nucleotide_sequences: parent foreign-key postcondition failed"
        )
    violations = (await connection.execute(text("PRAGMA foreign_key_check"))).fetchall()
    if violations:
        raise RuntimeError(
            "Cannot safely rebuild nucleotide_sequences: foreign-key verification failed"
        )


async def _migration_authoritative_import_batches(connection: AsyncConnection) -> None:
    """Add the immutable idempotency/result ledger for sequence imports."""

    await connection.run_sync(
        lambda sync_connection: MolecularImportBatch.__table__.create(
            sync_connection,
            checkfirst=True,
        )
    )
    # Existing stores already passed 0002 before this table existed. Re-run the
    # guarded trigger migration so the new result ledger receives the same
    # append-only protection as the other scientific-history tables.
    await _migration_append_only_guards(connection)


async def _migration_project_plasmid_metadata(connection: AsyncConnection) -> None:
    """Add project-local metadata used by the governed project-hub changeover."""

    await connection.run_sync(
        lambda sync_connection: ProjectPlasmidMetadata.__table__.create(
            sync_connection,
            checkfirst=True,
        )
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
    (
        "0005_authoritative_import_batches",
        "persist immutable authoritative sequence-import results and idempotency bindings",
        _migration_authoritative_import_batches,
    ),
    (
        "0006_project_plasmid_metadata",
        "persist project-local plasmid tags and notes behind exact state activation",
        _migration_project_plasmid_metadata,
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

        schema_issues = await _molbio_schema_issues(connection)
        if schema_issues:
            raise RuntimeError(
                "Mol Bio schema drift detected after migration: " + "; ".join(schema_issues)
            )

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
            schema_issues = await _molbio_schema_issues(connection)
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
            sequence_parent_fk_rows = (
                await connection.execute(
                    text('PRAGMA foreign_key_list("nucleotide_sequences")')
                )
            ).fetchall()
            sequence_parent_rows = (
                await connection.execute(
                    text("SELECT id, parent_id FROM nucleotide_sequences ORDER BY id")
                )
            ).fetchall()
        versions = [str(row[0]) for row in migrations]
        migrations_current = versions == expected_versions
        triggers_current = _immutable_triggers_are_current(trigger_rows)
        sequence_parent_fk_current = _has_sequence_parent_foreign_key(
            sequence_parent_fk_rows
        )
        sequence_parent_cycles = _sequence_parent_cycles(sequence_parent_rows)
        healthy = (
            quick_check.lower() == "ok"
            and not foreign_key_rows
            and migrations_current
            and not schema_issues
            and triggers_current
            and sequence_parent_fk_current
            and not sequence_parent_cycles
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
            "database_schema_current": not schema_issues,
            "database_schema_issue_count": len(schema_issues),
            "immutable_trigger_count": len(trigger_rows),
            "immutable_triggers_current": triggers_current,
            "sequence_parent_foreign_key_current": sequence_parent_fk_current,
            "sequence_parent_cycle_count": len(sequence_parent_cycles),
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
            "database_schema_current": False,
            "database_schema_issue_count": None,
            "immutable_trigger_count": 0,
            "immutable_triggers_current": False,
            "sequence_parent_foreign_key_current": False,
            "sequence_parent_cycle_count": None,
            "error": f"{type(exc).__name__}: database health query failed",
        }


molbio_engine = create_molbio_engine()
molbio_session = make_molbio_session_factory(molbio_engine)


async def get_molbio_session() -> AsyncIterator[AsyncSession]:
    async with molbio_session() as session:
        yield session

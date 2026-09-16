"""Explicit first-install lifecycle for the durable core SQLite database.

Startup remains attest-only. Only a genuinely empty SQLite schema receives the
ORM-owned base tables; migration-owned tables must be built by their historical
migrations (not today's ORM definitions, which can violate predecessor checks).
"""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os
import sqlite3
import stat

from paths import get_db_path


# These tables have no CREATE authority in the registered migration history.
# Additive migrations may extend them; their idempotent column checks are retained.
BASE_TABLES = (
    "analysis_runs", "designs", "frustrampnn_comparison_rows",
    "frustrampnn_comparisons", "frustrampnn_guidance_plans", "input_files",
    "jobs", "nucleotide_sequences", "primers", "rfd3_local_redesign_artifacts",
    "rfd3_local_redesign_candidates", "rfd3_local_redesign_requests",
    "shape_cad_sources", "shape_design_geometries", "shape_design_requests",
    "user_sequences", "user_templates",
)


@contextmanager
def _lifecycle_lock(path: Path):
    # Never unlink: waiters must continue to lock the same inode. Process death
    # releases flock, unlike a stale sentinel. Hold across migrations too, since
    # historical migrations open/commit their own SQLite connections.
    descriptor = os.open(str(path) + ".migration.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        observed = os.fstat(lock.fileno())
        if observed.st_nlink != 1 or not stat.S_ISREG(observed.st_mode):
            raise RuntimeError("Migration lock must be a single-link regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _create_base_if_empty(path: Path) -> None:
    from database import Base
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    with sqlite3.connect(path, timeout=30) as connection:
        # Explicit transaction includes DDL. A crash cannot publish half a base.
        connection.execute("BEGIN IMMEDIATE")
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE substr(name, 1, 7) != 'sqlite_'"
        ).fetchall()
        if objects:
            # No repair, guessing, or creating missing tables on an existing DB.
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"jobs", "designs"}.issubset(tables):
                raise RuntimeError(
                    "Existing core database lacks its base schema; refusing fresh-install "
                    "bootstrap on nonempty state. Restore or explicitly recover this database."
                )
            return
        for name in BASE_TABLES:
            table = Base.metadata.tables[name]
            connection.execute(str(CreateTable(table).compile(dialect=dialect())))
            for index in sorted(table.indexes, key=lambda index: index.name):
                connection.execute(str(CreateIndex(index).compile(dialect=dialect())))


def migrate_database(db_path: str | None = None) -> None:
    """Bootstrap only pristine state, then run every real registered migration."""
    from migrations.runner import run_all

    raw_path = db_path if db_path is not None else str(get_db_path())
    if not raw_path or raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("Core migrations require a durable filesystem database path")
    path = Path(raw_path).expanduser().resolve()
    # The oldest migrations bind get_db_path() at import time. Refuse split
    # authority rather than migrating a second database through those functions.
    if path != Path(get_db_path()).expanduser().resolve():
        raise ValueError("Migration path must match the configured core database")
    from inspect import getmodule
    from migrations.runner import MIGRATIONS
    for migration in MIGRATIONS:
        bound_path = getattr(getmodule(migration.fn), "DB_PATH", None)
        if bound_path is not None and Path(bound_path).expanduser().resolve() != path:
            raise ValueError("Legacy migration path differs from the configured core database")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lifecycle_lock(path):
        _create_base_if_empty(path)
        run_all(str(path))

"""SQLite online-backup helpers shared by owned persistence subsystems."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SQLiteBackupReport:
    source_path: Path
    backup_path: Path
    size_bytes: int
    sha256: str
    quick_check: str
    foreign_key_violations: int


def backup_sqlite_database(source_path: Path, backup_path: Path) -> SQLiteBackupReport:
    """Create and verify a consistent backup, including committed WAL pages.

    This intentionally uses ``sqlite3.Connection.backup`` rather than copying
    files so a live WAL-mode source is captured as one consistent snapshot.
    Existing backup files are never overwritten.
    """

    source = Path(source_path).expanduser().resolve()
    destination = Path(backup_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {source}")
    if source == destination or (destination.exists() and os.path.samefile(source, destination)):
        raise ValueError("SQLite backup source and destination must be distinct, non-alias paths")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing SQLite backup: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    staging = Path(staging_name)
    source_uri = f"file:{source}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True, timeout=30.0) as source_connection:
            with sqlite3.connect(staging, timeout=30.0) as backup_connection:
                source_connection.backup(backup_connection)
                backup_connection.commit()
        with sqlite3.connect(
            f"file:{staging}?mode=ro", uri=True, timeout=30.0
        ) as check_connection:
            quick_check = str(check_connection.execute("PRAGMA quick_check").fetchone()[0])
            foreign_key_violations = len(
                check_connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        if quick_check.lower() != "ok":
            raise RuntimeError(f"SQLite backup failed quick_check: {quick_check}")
        if foreign_key_violations:
            raise RuntimeError(
                "SQLite backup failed foreign key validation: "
                f"{foreign_key_violations} violation(s)"
            )
        os.link(staging, destination)
        staging.unlink()
    except Exception:
        staging.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return SQLiteBackupReport(
        source_path=source,
        backup_path=destination,
        size_bytes=destination.stat().st_size,
        sha256=digest,
        quick_check=quick_check,
        foreign_key_violations=foreign_key_violations,
    )

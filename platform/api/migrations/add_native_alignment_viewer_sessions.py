"""Extend saved viewers without rewriting migration 32 or historical rows.

SQLite requires a table rebuild to relax NOT NULL. Retain the installed table's
constraints, indexes and triggers rather than recreating it from current ORM.
This module is migration source only; installation belongs to the managed runner.
"""
from __future__ import annotations

import re
import sqlite3


_TABLE = "ont_signal_viewer_sessions"
_REPLACEMENT = "ont_signal_viewer_sessions_native_upgrade"


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        # Disable before BEGIN: children retain their references to the original
        # name, and DROP cannot invoke cascading foreign-key actions.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
        ).fetchone()
        if row is None:
            raise RuntimeError("saved-view predecessor table is missing")
        columns = {item[1]: item for item in connection.execute(
            'PRAGMA table_info("ont_signal_viewer_sessions")'
        )}
        required = {"dataset_id", "run_id", "observed_generation", "selected_read_id"}
        if not required <= columns.keys():
            raise RuntimeError("saved-view predecessor columns are missing")
        sql = row[0]
        for name in ("dataset_id", "run_id", "observed_generation"):
            if columns[name][3]:
                pattern = rf'(\b{name}\s+(?:VARCHAR\(\d+\)|INTEGER))\s+NOT\s+NULL\b'
                sql, count = re.subn(pattern, r'\1', sql, flags=re.IGNORECASE)
                if count != 1:
                    raise RuntimeError("unsupported saved-view column definition: " + name)
        sql, count = re.subn(
            r'\bselected_read_id\s+VARCHAR\(\d+\)',
            'selected_read_id VARCHAR(254)', sql, flags=re.IGNORECASE,
        )
        if count != 1:
            raise RuntimeError("unsupported saved-view selected-read definition")
        if "alignment_source_identity" not in columns:
            start = sql.index("(") + 1
            sql = sql[:start] + "alignment_source_identity JSON, " + sql[start:]
        sql, count = re.subn(
            r'(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)[\"`\[]?ont_signal_viewer_sessions[\"`\]]?',
            r'\1"' + _REPLACEMENT + '"', sql, count=1, flags=re.IGNORECASE,
        )
        if count != 1:
            raise RuntimeError("unsupported saved-view table definition")
        retained = [item[0] for item in connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name",
            (_TABLE,),
        )]
        connection.execute(sql)
        names = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        connection.execute(f'INSERT INTO "{_REPLACEMENT}" ({names}) SELECT {names} FROM "{_TABLE}"')
        connection.execute(f'DROP TABLE "{_TABLE}"')
        connection.execute(f'ALTER TABLE "{_REPLACEMENT}" RENAME TO "{_TABLE}"')
        for statement in retained:
            connection.execute(statement)
        # New native rows have no invented run; existing managed rows and all
        # their original FK/index constraints survive the same transaction.
        if connection.execute(f'PRAGMA foreign_key_check("{_TABLE}")').fetchone():
            raise RuntimeError("saved-view foreign-key integrity blocks migration")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

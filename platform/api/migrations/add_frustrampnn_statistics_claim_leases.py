"""Add durable ownership leases to FrustraMPNN statistics claims."""

from __future__ import annotations

import sqlite3
from pathlib import Path


_COLUMNS = {
    "claim_token": "VARCHAR(36)",
    "claim_owner": "VARCHAR(128)",
    "lease_expires_at": "DATETIME",
    "heartbeat_at": "DATETIME",
}


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        existing = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(frustrampnn_statistics_analyses)"
            ).fetchall()
        }
        if not existing:
            raise sqlite3.OperationalError(
                "frustrampnn_statistics_analyses must exist before claim leases"
            )
        for name, sql_type in _COLUMNS.items():
            if name not in existing:
                connection.execute(
                    f'ALTER TABLE frustrampnn_statistics_analyses ADD COLUMN "{name}" {sql_type}'
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_statistics_analysis_lease
            ON frustrampnn_statistics_analyses(state, lease_expires_at)
            """
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "FrustraMPNN statistics claim-lease migration foreign-key violations: "
                f"{violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]

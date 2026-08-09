"""Add immutable FrustraMPNN authority and derived-statistics columns."""

from __future__ import annotations

import sqlite3
from pathlib import Path


_COLUMNS = {
    "settings_sha256": "VARCHAR(64)",
    "effective_settings_sha256": "VARCHAR(64)",
    "effective_settings_json": "JSON",
    "capability_inventory_sha256": "VARCHAR(64)",
    "statistics_sha256": "VARCHAR(64)",
    "statistics_json": "JSON",
    "comparison_compatibility_id": "VARCHAR(64)",
}


def migrate(db_path: str | Path) -> None:
    """Add nullable v2 receipt columns while leaving historical rows untouched."""
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        existing_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(frustrampnn_results)"
            ).fetchall()
        }
        for name, sql_type in _COLUMNS.items():
            if name not in existing_columns:
                connection.execute(
                    f'ALTER TABLE frustrampnn_results ADD COLUMN "{name}" {sql_type}'
                )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "FrustraMPNN statistics migration foreign-key violations: "
                f"{violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]

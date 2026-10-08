"""Add historical ownership to existing derived intent, never scientific rows."""
import sqlite3
from pathlib import Path
from paths import get_db_path


def migrate(db_path: str | Path | None = None) -> None:
    connection = sqlite3.connect(str(db_path if db_path is not None else get_db_path()), timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ngs_alignment_derived_products)")}
        if not columns:
            raise sqlite3.IntegrityError("install split products migration before historical ownership")
        if "historical_owner" not in columns:
            connection.execute("ALTER TABLE ngs_alignment_derived_products ADD COLUMN historical_owner JSON")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

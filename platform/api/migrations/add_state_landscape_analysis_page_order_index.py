"""Add the canonical unfiltered state-analysis page-order index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_INDEX_NAME = "ix_cm_state_analysis_rows_page_order"
_ROWS = "conformational_mapping_state_landscape_analysis_rows"
_PAGE_ORDER_COLUMNS = (
    "request_id, analysis_id, pair_id, target_id, entity_instance_id, "
    "auth_asym_id, auth_seq_id, insertion_code, sequence_index, validated_wt, id"
)


def migrate(db_path: str | None = None) -> None:
    """Index request/analysis pages in their persisted canonical output order."""

    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON {_ROWS} ({_PAGE_ORDER_COLUMNS})"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

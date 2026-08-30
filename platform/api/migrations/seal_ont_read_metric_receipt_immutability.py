"""Seal literature-backed ONT read-metric artifact receipts."""
from __future__ import annotations

import sqlite3
from pathlib import Path


TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_ont_read_metric_receipt_no_update
    BEFORE UPDATE ON scientific_artifact_receipts
    WHEN (OLD.owner_kind = 'ont_raw_signal_representation'
          AND OLD.role = 'literature_backed_read_metrics')
      OR (NEW.owner_kind = 'ont_raw_signal_representation'
          AND NEW.role = 'literature_backed_read_metrics')
    BEGIN
        SELECT RAISE(ABORT, 'literature-backed ONT read metric receipt is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_ont_read_metric_receipt_no_delete
    BEFORE DELETE ON scientific_artifact_receipts
    WHEN OLD.owner_kind = 'ont_raw_signal_representation'
      AND OLD.role = 'literature_backed_read_metrics'
    BEGIN
        SELECT RAISE(ABORT, 'literature-backed ONT read metric receipt is immutable');
    END
    """,
)


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scientific_artifact_receipts'"
        ).fetchone() is not None:
            for sql in TRIGGERS:
                connection.execute(sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["TRIGGERS", "migrate"]

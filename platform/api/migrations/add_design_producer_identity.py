"""Persist future native Design producer identity; no historical inference."""
import sqlite3


def migrate(db_path: str) -> None:
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(designs)")}
        if "producer_model_id" not in columns:
            connection.execute("ALTER TABLE designs ADD COLUMN producer_model_id VARCHAR(64)")

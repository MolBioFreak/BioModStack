from __future__ import annotations

import sqlite3
from pathlib import Path

from migrations.add_external_result_imports import migrate


def test_external_result_import_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "bms.db"
    migrate(database)
    migrate(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(external_result_imports)")}
        indexes = list(connection.execute("PRAGMA index_list(external_result_imports)"))
    assert {"provider_id", "resource_type", "provider_job_id", "source_fingerprint", "bms_job_id"} <= columns
    assert any(row[2] == 1 for row in indexes)

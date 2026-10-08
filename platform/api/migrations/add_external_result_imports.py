from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


def migrate(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS external_result_imports (
                id VARCHAR(36) PRIMARY KEY,
                provider_id VARCHAR(64) NOT NULL,
                resource_type VARCHAR(128) NOT NULL,
                provider_job_id VARCHAR(128) NOT NULL,
                state VARCHAR(32) NOT NULL DEFAULT 'discovered',
                source_path VARCHAR(1000) NOT NULL,
                source_fingerprint VARCHAR(64) NOT NULL,
                run_metadata_sha256 VARCHAR(64) NOT NULL,
                archive_sha256 VARCHAR(64),
                normalized_manifest_path VARCHAR(1000),
                bms_job_id VARCHAR(36),
                dataset_name VARCHAR(255) NOT NULL,
                job_name VARCHAR(255),
                failure_code VARCHAR(64),
                failure_message TEXT,
                provider_metadata JSON NOT NULL DEFAULT '{}',
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                imported_at DATETIME,
                CONSTRAINT uq_external_result_import_identity
                    UNIQUE (provider_id, resource_type, provider_job_id),
                FOREIGN KEY(bms_job_id) REFERENCES jobs(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_external_result_imports_provider_id ON external_result_imports(provider_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_external_result_imports_resource_type ON external_result_imports(resource_type)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_external_result_imports_provider_job_id ON external_result_imports(provider_job_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_external_result_imports_state ON external_result_imports(state)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_external_result_imports_bms_job_id ON external_result_imports(bms_job_id)"
        )
        connection.commit()


if __name__ == "__main__":
    migrate()

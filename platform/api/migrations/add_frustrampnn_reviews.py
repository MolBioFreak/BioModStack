"""Add durable, job-scoped FrustraMPNN saved reviews."""

from __future__ import annotations

import sqlite3
from pathlib import Path


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS frustrampnn_reviews (
        review_id VARCHAR(36) PRIMARY KEY NOT NULL,
        parent_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        created_by VARCHAR(128) NOT NULL,
        title VARCHAR(160) NOT NULL,
        notes TEXT NOT NULL,
        result_references_json JSON NOT NULL,
        selected_residues_json JSON NOT NULL,
        filters_json JSON NOT NULL,
        viewer_state_json JSON NOT NULL,
        tags_json JSON NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_parent_job_id ON frustrampnn_reviews (parent_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_owner_job ON frustrampnn_reviews (created_by, parent_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_updated_at ON frustrampnn_reviews (updated_at)",
    """
    CREATE TABLE IF NOT EXISTS frustrampnn_exports (
        export_id VARCHAR(36) PRIMARY KEY NOT NULL,
        parent_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        invocation_id VARCHAR(128) NOT NULL,
        created_by VARCHAR(128) NOT NULL,
        format VARCHAR(8) NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        row_count INTEGER NOT NULL,
        total_matching_rows INTEGER NOT NULL,
        complete BOOLEAN NOT NULL,
        payload_json JSON NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(parent_job_id, invocation_id)
            REFERENCES frustrampnn_results(parent_job_id, invocation_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_exports_owner_job ON frustrampnn_exports (created_by, parent_job_id)",
    """
    CREATE TABLE IF NOT EXISTS frustrampnn_review_artifacts (
        artifact_id VARCHAR(36) PRIMARY KEY NOT NULL,
        review_id VARCHAR(36) NOT NULL REFERENCES frustrampnn_reviews(review_id),
        parent_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        created_by VARCHAR(128) NOT NULL,
        role VARCHAR(32) NOT NULL,
        media_type VARCHAR(64) NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        size_bytes INTEGER NOT NULL,
        payload_blob BLOB NOT NULL,
        generation_json JSON NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_review_artifacts_owner_review ON frustrampnn_review_artifacts (created_by, review_id)",
)


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _STATEMENTS:
            connection.execute(statement)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"FrustraMPNN review migration foreign-key violations: {violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]

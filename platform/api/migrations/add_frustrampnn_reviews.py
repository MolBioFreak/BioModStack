"""Add durable, job-scoped FrustraMPNN saved reviews."""

from __future__ import annotations

import sqlite3
from pathlib import Path


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS frustrampnn_reviews (
        review_id VARCHAR(36) PRIMARY KEY NOT NULL,
        parent_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        invocation_id VARCHAR(128) NOT NULL,
        landscape_sha256 VARCHAR(64) NOT NULL,
        effective_settings_sha256 VARCHAR(64) NOT NULL,
        review_sha256 VARCHAR(64) NOT NULL UNIQUE,
        supersedes_review_id VARCHAR(36) REFERENCES frustrampnn_reviews(review_id),
        created_by VARCHAR(128) NOT NULL,
        title VARCHAR(160) NOT NULL,
        notes TEXT NOT NULL,
        result_references_json JSON NOT NULL,
        selected_residues_json JSON NOT NULL,
        filters_json JSON NOT NULL,
        viewer_state_json JSON NOT NULL,
        tags_json JSON NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(parent_job_id, invocation_id)
            REFERENCES frustrampnn_results(parent_job_id, invocation_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_parent_job_id ON frustrampnn_reviews (parent_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_owner_job ON frustrampnn_reviews (created_by, parent_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_frustrampnn_reviews_created_at ON frustrampnn_reviews (created_at)",
    """
    CREATE TABLE IF NOT EXISTS frustrampnn_exports (
        export_id VARCHAR(36) PRIMARY KEY NOT NULL,
        review_id VARCHAR(36) NOT NULL REFERENCES frustrampnn_reviews(review_id),
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

REQUIRED_COLUMNS = {
    "frustrampnn_reviews": {"review_id", "parent_job_id", "invocation_id", "landscape_sha256", "effective_settings_sha256", "review_sha256", "supersedes_review_id", "created_by", "title", "notes", "result_references_json", "selected_residues_json", "filters_json", "viewer_state_json", "tags_json", "created_at"},
    "frustrampnn_exports": {"export_id", "review_id", "parent_job_id", "invocation_id", "created_by", "format", "content_sha256", "row_count", "total_matching_rows", "complete", "payload_json", "created_at"},
    "frustrampnn_review_artifacts": {"artifact_id", "review_id", "parent_job_id", "created_by", "role", "media_type", "content_sha256", "size_bytes", "payload_blob", "generation_json", "created_at"},
}
REQUIRED_PRIMARY_KEYS = {"frustrampnn_reviews": ("review_id",), "frustrampnn_exports": ("export_id",), "frustrampnn_review_artifacts": ("artifact_id",)}
REQUIRED_TYPES = {
    "frustrampnn_reviews": {"review_id": "VARCHAR(36)", "parent_job_id": "VARCHAR(36)", "invocation_id": "VARCHAR(128)", "landscape_sha256": "VARCHAR(64)", "effective_settings_sha256": "VARCHAR(64)", "review_sha256": "VARCHAR(64)", "supersedes_review_id": "VARCHAR(36)", "created_by": "VARCHAR(128)", "title": "VARCHAR(160)", "notes": "TEXT", "result_references_json": "JSON", "selected_residues_json": "JSON", "filters_json": "JSON", "viewer_state_json": "JSON", "tags_json": "JSON", "created_at": "DATETIME"},
    "frustrampnn_exports": {"export_id": "VARCHAR(36)", "review_id": "VARCHAR(36)", "parent_job_id": "VARCHAR(36)", "invocation_id": "VARCHAR(128)", "created_by": "VARCHAR(128)", "format": "VARCHAR(8)", "content_sha256": "VARCHAR(64)", "row_count": "INTEGER", "total_matching_rows": "INTEGER", "complete": "BOOLEAN", "payload_json": "JSON", "created_at": "DATETIME"},
    "frustrampnn_review_artifacts": {"artifact_id": "VARCHAR(36)", "review_id": "VARCHAR(36)", "parent_job_id": "VARCHAR(36)", "created_by": "VARCHAR(128)", "role": "VARCHAR(32)", "media_type": "VARCHAR(64)", "content_sha256": "VARCHAR(64)", "size_bytes": "INTEGER", "payload_blob": "BLOB", "generation_json": "JSON", "created_at": "DATETIME"},
}
REQUIRED_NOT_NULL = {table: columns - ({"supersedes_review_id"} if table == "frustrampnn_reviews" else set()) for table, columns in REQUIRED_COLUMNS.items()}
REQUIRED_INDEX_COLUMNS = {
    "ix_frustrampnn_reviews_parent_job_id": ("parent_job_id",),
    "ix_frustrampnn_reviews_owner_job": ("created_by", "parent_job_id"),
    "ix_frustrampnn_reviews_created_at": ("created_at",),
    "ix_frustrampnn_exports_owner_job": ("created_by", "parent_job_id"),
    "ix_frustrampnn_review_artifacts_owner_review": ("created_by", "review_id"),
}
REQUIRED_UNIQUE_COLUMNS = {"frustrampnn_reviews": {("review_sha256",)}}
REQUIRED_FOREIGN_KEYS = {
    "frustrampnn_reviews": {(("parent_job_id", "invocation_id"), "frustrampnn_results", ("parent_job_id", "invocation_id"), "NO ACTION", "NO ACTION"), (("parent_job_id",), "jobs", ("id",), "NO ACTION", "NO ACTION"), (("supersedes_review_id",), "frustrampnn_reviews", ("review_id",), "NO ACTION", "NO ACTION")},
    "frustrampnn_exports": {(("parent_job_id", "invocation_id"), "frustrampnn_results", ("parent_job_id", "invocation_id"), "NO ACTION", "NO ACTION"), (("parent_job_id",), "jobs", ("id",), "NO ACTION", "NO ACTION"), (("review_id",), "frustrampnn_reviews", ("review_id",), "NO ACTION", "NO ACTION")},
    "frustrampnn_review_artifacts": {(("parent_job_id",), "jobs", ("id",), "NO ACTION", "NO ACTION"), (("review_id",), "frustrampnn_reviews", ("review_id",), "NO ACTION", "NO ACTION")},
}


def physical_schema_errors(connection: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    for table, required_columns in REQUIRED_COLUMNS.items():
        info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        by_name = {str(row[1]): row for row in info}
        if set(by_name) != required_columns:
            errors.append(f"{table}:columns")
            continue
        primary_key = tuple(name for _, name in sorted((int(row[5]), str(row[1])) for row in info if int(row[5]) > 0))
        if primary_key != REQUIRED_PRIMARY_KEYS[table]:
            errors.append(f"{table}:primary_key")
        if any(str(by_name[column][2]).upper() != declared_type for column, declared_type in REQUIRED_TYPES[table].items()):
            errors.append(f"{table}:types")
        if any(int(by_name[column][3]) != 1 for column in REQUIRED_NOT_NULL[table]):
            errors.append(f"{table}:not_null")
    for index, expected_columns in REQUIRED_INDEX_COLUMNS.items():
        actual = tuple(str(row[2]) for row in connection.execute(f'PRAGMA index_info("{index}")').fetchall())
        if actual != expected_columns:
            errors.append(f"{index}:columns")
    for table, expected_unique_columns in REQUIRED_UNIQUE_COLUMNS.items():
        unique_columns = {
            tuple(str(column[2]) for column in connection.execute(f'PRAGMA index_info("{row[1]}")').fetchall())
            for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall()
            if int(row[2]) == 1
        }
        if not expected_unique_columns.issubset(unique_columns):
            errors.append(f"{table}:unique")
    for table, expected in REQUIRED_FOREIGN_KEYS.items():
        rows = connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        grouped: dict[int, list[tuple]] = {}
        for row in rows:
            grouped.setdefault(int(row[0]), []).append(row)
        actual = {(tuple(str(row[3]) for row in sorted(group, key=lambda item: int(item[1]))), str(group[0][2]), tuple(str(row[4]) for row in sorted(group, key=lambda item: int(item[1]))), str(group[0][5]).upper(), str(group[0][6]).upper()) for group in grouped.values()}
        if actual != expected:
            errors.append(f"{table}:foreign_keys")
    return errors


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        existing_objects = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            ).fetchall()
        }
        target_objects = set(REQUIRED_COLUMNS) | set(REQUIRED_INDEX_COLUMNS)
        if existing_objects & target_objects:
            preflight_errors = physical_schema_errors(connection)
            if preflight_errors:
                raise sqlite3.IntegrityError(
                    f"FrustraMPNN review migration physical-schema mismatch: {preflight_errors!r}"
                )
        for statement in _STATEMENTS:
            connection.execute(statement)
        schema_errors = physical_schema_errors(connection)
        if schema_errors:
            raise sqlite3.IntegrityError(
                f"FrustraMPNN review migration physical-schema mismatch: {schema_errors!r}"
            )
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


__all__ = ["migrate", "physical_schema_errors"]

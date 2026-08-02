"""Allow provenance-distinct Shape records to share canonical geometry bytes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_GEOMETRIES = "shape_design_geometries"
_REQUESTS = "shape_design_requests"
_LEGACY_GEOMETRIES = f"{_GEOMETRIES}__pre_provenance_identity"
_LEGACY_REQUESTS = f"{_REQUESTS}__pre_provenance_identity"


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _geometry_hash_is_unique(connection: sqlite3.Connection) -> bool:
    for _, index_name, unique, *_ in connection.execute(f"PRAGMA index_list({_GEOMETRIES})"):
        columns = [row[2] for row in connection.execute(f'PRAGMA index_info("{index_name}")')]
        if unique and columns == ["geometry_sha256"]:
            return True
    return False


def _create_geometries(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE {_GEOMETRIES} (
            geometry_id VARCHAR(41) PRIMARY KEY,
            source_id VARCHAR(40) NOT NULL REFERENCES shape_cad_sources(source_id),
            geometry_sha256 VARCHAR(64) NOT NULL,
            conversion_sha256 VARCHAR(64) NOT NULL,
            angstrom_per_unit FLOAT NOT NULL,
            vertex_count INTEGER NOT NULL,
            face_count INTEGER NOT NULL,
            point_count INTEGER NOT NULL,
            manifest JSON NOT NULL,
            artifacts JSON NOT NULL,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_shape_geometry_conversion UNIQUE (source_id, conversion_sha256)
        )
        """
    )
    connection.execute(
        f"CREATE INDEX ix_shape_design_geometries_source_id ON {_GEOMETRIES} (source_id)"
    )
    connection.execute(
        f"CREATE INDEX ix_shape_design_geometries_geometry_sha256 ON {_GEOMETRIES} (geometry_sha256)"
    )


def _create_requests(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE {_REQUESTS} (
            request_id VARCHAR(42) PRIMARY KEY,
            geometry_id VARCHAR(41) NOT NULL REFERENCES {_GEOMETRIES}(geometry_id),
            request_sha256 VARCHAR(64) NOT NULL UNIQUE,
            request_spec JSON NOT NULL,
            stage_relative_path VARCHAR(500) NOT NULL,
            job_id VARCHAR(36) UNIQUE REFERENCES jobs(id),
            created_at DATETIME NOT NULL
        )
        """
    )
    connection.execute(
        f"CREATE INDEX ix_shape_design_requests_geometry_id ON {_REQUESTS} (geometry_id)"
    )
    connection.execute(
        f"CREATE UNIQUE INDEX ix_shape_design_requests_request_sha256 ON {_REQUESTS} (request_sha256)"
    )
    connection.execute(
        f"CREATE UNIQUE INDEX ix_shape_design_requests_job_id ON {_REQUESTS} (job_id)"
    )


def migrate(db_path: str | None = None) -> None:
    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        if not _table_exists(connection, _GEOMETRIES) or not _geometry_hash_is_unique(connection):
            return
        requests_exist = _table_exists(connection, _REQUESTS)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        if requests_exist:
            connection.execute(f"ALTER TABLE {_REQUESTS} RENAME TO {_LEGACY_REQUESTS}")
        connection.execute(f"ALTER TABLE {_GEOMETRIES} RENAME TO {_LEGACY_GEOMETRIES}")
        for index_name in (
            "ix_shape_design_geometries_source_id",
            "ix_shape_design_geometries_geometry_sha256",
            "ix_shape_design_requests_geometry_id",
            "ix_shape_design_requests_request_sha256",
            "ix_shape_design_requests_job_id",
        ):
            connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
        _create_geometries(connection)
        connection.execute(
            f"INSERT INTO {_GEOMETRIES} "
            "(geometry_id, source_id, geometry_sha256, conversion_sha256, angstrom_per_unit, "
            "vertex_count, face_count, point_count, manifest, artifacts, created_at) "
            f"SELECT geometry_id, source_id, geometry_sha256, conversion_sha256, angstrom_per_unit, "
            f"vertex_count, face_count, point_count, manifest, artifacts, created_at FROM {_LEGACY_GEOMETRIES}"
        )
        if requests_exist:
            _create_requests(connection)
            connection.execute(
                f"INSERT INTO {_REQUESTS} "
                "(request_id, geometry_id, request_sha256, request_spec, stage_relative_path, job_id, created_at) "
                f"SELECT request_id, geometry_id, request_sha256, request_spec, stage_relative_path, job_id, created_at FROM {_LEGACY_REQUESTS}"
            )
            connection.execute(f"DROP TABLE {_LEGACY_REQUESTS}")
        connection.execute(f"DROP TABLE {_LEGACY_GEOMETRIES}")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "Shape geometry identity migration produced foreign-key violations: "
                f"{violations!r}"
            )
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

"""Add and constrain immutable canonical terminal artifact manifests in the ONT ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from migrations.sqlite_sha256 import register_sqlite_sha256


_TERMINAL_SCHEMA = "bms.ont.instrument-terminal-artifacts.v1"
_TERMINAL_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "run_id",
        "minknow_run_id_sha256",
        "terminal_state",
        "observed_generation",
        "artifacts",
    }
)
_ARTIFACT_FIELDS = frozenset({"kind", "path", "bytes", "sha256"})
_ARTIFACT_KINDS = frozenset({"fastq", "pod5", "bam"})
_TERMINAL_STATES = frozenset({"stopped", "completed", "failed"})


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _drop_manifest_triggers(connection: sqlite3.Connection) -> None:
    for trigger in (
        "ont_run_terminal_manifest_pair_insert",
        "ont_run_terminal_manifest_pair_update",
        "ont_run_terminal_manifest_canonical_insert_invalid_json",
        "ont_run_terminal_manifest_canonical_insert",
        "ont_run_terminal_manifest_canonical_update_invalid_json",
        "ont_run_terminal_manifest_canonical_update",
        "ont_run_terminal_manifest_immutable",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical_manifest_for_row(row: tuple[Any, ...]) -> tuple[str, str] | None:
    """Return a strict canonical replacement for one pre-trigger legacy row."""
    run_id, minknow_run_id, state, generation, raw_manifest, raw_digest = row
    if (raw_manifest is None) != (raw_digest is None) or raw_manifest is None:
        return None
    try:
        manifest = json.loads(str(raw_manifest))
    except (TypeError, ValueError):
        return None
    if not isinstance(manifest, dict) or set(manifest) != _TERMINAL_FIELDS:
        return None
    observed_generation = manifest.get("observed_generation")
    expected_minknow_digest = hashlib.sha256(str(minknow_run_id or "").encode("utf-8")).hexdigest()
    if (
        manifest.get("schema") != _TERMINAL_SCHEMA
        or manifest.get("schema_version") != 1
        or manifest.get("run_id") != run_id
        or state not in _TERMINAL_STATES
        or manifest.get("terminal_state") != state
        or isinstance(observed_generation, bool)
        or not isinstance(observed_generation, int)
        or observed_generation != generation
        or observed_generation < 1
        or manifest.get("minknow_run_id_sha256") != expected_minknow_digest
    ):
        return None
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return None
    previous_order: tuple[str, str] | None = None
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != _ARTIFACT_FIELDS:
            return None
        kind = artifact.get("kind")
        path = artifact.get("path")
        size = artifact.get("bytes")
        digest = artifact.get("sha256")
        if (
            kind not in _ARTIFACT_KINDS
            or not isinstance(path, str)
            or not path
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not _is_sha256(digest)
        ):
            return None
        artifact_order = (kind, path)
        if previous_order is not None and artifact_order <= previous_order:
            return None
        previous_order = artifact_order
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill_manifest_rows(connection: sqlite3.Connection) -> None:
    """Canonicalize valid legacy evidence and remove pairs that cannot be trusted."""
    rows = connection.execute(
        "SELECT id, minknow_run_id, state, observed_generation, "
        "terminal_artifact_manifest, terminal_artifact_manifest_sha256 "
        "FROM ont_instrument_runs WHERE terminal_artifact_manifest IS NOT NULL "
        "OR terminal_artifact_manifest_sha256 IS NOT NULL"
    ).fetchall()
    for row in rows:
        replacement = _canonical_manifest_for_row(row)
        if replacement is None:
            connection.execute(
                "UPDATE ont_instrument_runs SET terminal_artifact_manifest = NULL, "
                "terminal_artifact_manifest_sha256 = NULL WHERE id = ?",
                (row[0],),
            )
        else:
            connection.execute(
                "UPDATE ont_instrument_runs SET terminal_artifact_manifest = ?, "
                "terminal_artifact_manifest_sha256 = ? WHERE id = ?",
                (*replacement, row[0]),
            )


def _create_manifest_triggers(connection: sqlite3.Connection) -> None:
    """Install DB-level checks for byte-canonical, row-bound terminal evidence."""
    for operation, update_clause in (
        ("insert", ""),
        ("update", " OF terminal_artifact_manifest, terminal_artifact_manifest_sha256, id, minknow_run_id, state, observed_generation"),
    ):
        connection.execute(
            f"""
            CREATE TRIGGER ont_run_terminal_manifest_pair_{operation}
            BEFORE {operation.upper()}{update_clause} ON ont_instrument_runs
            WHEN (NEW.terminal_artifact_manifest IS NULL) IS NOT (NEW.terminal_artifact_manifest_sha256 IS NULL)
            BEGIN
                SELECT RAISE(ABORT, 'terminal artifact manifest and digest must be written together');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER ont_run_terminal_manifest_canonical_{operation}_invalid_json
            BEFORE {operation.upper()}{update_clause} ON ont_instrument_runs
            WHEN NEW.terminal_artifact_manifest IS NOT NULL
              AND json_valid(NEW.terminal_artifact_manifest) = 0
            BEGIN
                SELECT RAISE(ABORT, 'terminal artifact manifest must be valid JSON');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER ont_run_terminal_manifest_canonical_{operation}
            BEFORE {operation.upper()}{update_clause} ON ont_instrument_runs
            WHEN NEW.terminal_artifact_manifest IS NOT NULL
              AND json_valid(NEW.terminal_artifact_manifest) = 1
              AND (
                NEW.terminal_artifact_manifest_sha256 IS NOT sha256(NEW.terminal_artifact_manifest)
                OR NEW.terminal_artifact_manifest != json_object(
                    'artifacts', json((
                        SELECT json_group_array(json_object(
                            'bytes', json_extract(artifact.value, '$.bytes'),
                            'kind', json_extract(artifact.value, '$.kind'),
                            'path', json_extract(artifact.value, '$.path'),
                            'sha256', json_extract(artifact.value, '$.sha256')
                        ))
                        FROM json_each(NEW.terminal_artifact_manifest, '$.artifacts') AS artifact
                    )),
                    'minknow_run_id_sha256', json_extract(NEW.terminal_artifact_manifest, '$.minknow_run_id_sha256'),
                    'observed_generation', json_extract(NEW.terminal_artifact_manifest, '$.observed_generation'),
                    'run_id', json_extract(NEW.terminal_artifact_manifest, '$.run_id'),
                    'schema', json_extract(NEW.terminal_artifact_manifest, '$.schema'),
                    'schema_version', json_extract(NEW.terminal_artifact_manifest, '$.schema_version'),
                    'terminal_state', json_extract(NEW.terminal_artifact_manifest, '$.terminal_state')
                )
                OR json_type(NEW.terminal_artifact_manifest) IS NOT 'object'
                OR (SELECT count(*) FROM json_each(NEW.terminal_artifact_manifest)) != 7
                OR json_type(NEW.terminal_artifact_manifest, '$.schema') IS NOT 'text'
                OR json_extract(NEW.terminal_artifact_manifest, '$.schema') IS NOT '{_TERMINAL_SCHEMA}'
                OR json_type(NEW.terminal_artifact_manifest, '$.schema_version') IS NOT 'integer'
                OR json_extract(NEW.terminal_artifact_manifest, '$.schema_version') IS NOT 1
                OR json_type(NEW.terminal_artifact_manifest, '$.run_id') IS NOT 'text'
                OR json_extract(NEW.terminal_artifact_manifest, '$.run_id') IS NOT NEW.id
                OR json_type(NEW.terminal_artifact_manifest, '$.terminal_state') IS NOT 'text'
                OR json_extract(NEW.terminal_artifact_manifest, '$.terminal_state') IS NOT NEW.state
                OR NEW.state NOT IN ('stopped', 'completed', 'failed')
                OR json_type(NEW.terminal_artifact_manifest, '$.observed_generation') IS NOT 'integer'
                OR json_extract(NEW.terminal_artifact_manifest, '$.observed_generation') IS NOT NEW.observed_generation
                OR json_extract(NEW.terminal_artifact_manifest, '$.observed_generation') < 1
                OR json_type(NEW.terminal_artifact_manifest, '$.minknow_run_id_sha256') IS NOT 'text'
                OR json_extract(NEW.terminal_artifact_manifest, '$.minknow_run_id_sha256') IS NOT sha256(COALESCE(NEW.minknow_run_id, ''))
                OR json_type(NEW.terminal_artifact_manifest, '$.artifacts') IS NOT 'array'
                OR json_array_length(NEW.terminal_artifact_manifest, '$.artifacts') < 1
                OR EXISTS (
                    SELECT 1
                    FROM json_each(NEW.terminal_artifact_manifest, '$.artifacts') AS artifact
                    WHERE json_type(artifact.value) IS NOT 'object'
                       OR (SELECT count(*) FROM json_each(artifact.value)) != 4
                       OR json_type(artifact.value, '$.kind') IS NOT 'text'
                       OR json_extract(artifact.value, '$.kind') NOT IN ('fastq', 'pod5', 'bam')
                       OR json_type(artifact.value, '$.path') IS NOT 'text'
                       OR json_extract(artifact.value, '$.path') = ''
                       OR json_type(artifact.value, '$.bytes') IS NOT 'integer'
                       OR json_extract(artifact.value, '$.bytes') < 0
                       OR json_type(artifact.value, '$.sha256') IS NOT 'text'
                       OR length(json_extract(artifact.value, '$.sha256')) != 64
                       OR json_extract(artifact.value, '$.sha256') GLOB '*[^0-9a-f]*'
                )
                OR EXISTS (
                    SELECT 1
                    FROM json_each(NEW.terminal_artifact_manifest, '$.artifacts') AS previous
                    JOIN json_each(NEW.terminal_artifact_manifest, '$.artifacts') AS current
                      ON CAST(current.key AS INTEGER) = CAST(previous.key AS INTEGER) + 1
                    WHERE json_extract(current.value, '$.kind') < json_extract(previous.value, '$.kind')
                       OR (
                           json_extract(current.value, '$.kind') = json_extract(previous.value, '$.kind')
                           AND json_extract(current.value, '$.path') <= json_extract(previous.value, '$.path')
                       )
                )
              )
            BEGIN
                SELECT RAISE(ABORT, 'terminal artifact manifest is not canonical for this run');
            END
            """
        )
    connection.execute(
        """
        CREATE TRIGGER ont_run_terminal_manifest_immutable
        BEFORE UPDATE OF terminal_artifact_manifest, terminal_artifact_manifest_sha256 ON ont_instrument_runs
        WHEN (OLD.terminal_artifact_manifest IS NOT NULL OR OLD.terminal_artifact_manifest_sha256 IS NOT NULL)
          AND (NEW.terminal_artifact_manifest IS NOT OLD.terminal_artifact_manifest
               OR NEW.terminal_artifact_manifest_sha256 IS NOT OLD.terminal_artifact_manifest_sha256)
        BEGIN
            SELECT RAISE(ABORT, 'terminal artifact manifest is immutable');
        END
        """
    )


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        register_sqlite_sha256(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        columns = _columns(connection, "ont_instrument_runs")
        if not columns:
            raise RuntimeError("ont_instrument_runs must exist before terminal artifact manifest migration")
        if "terminal_artifact_manifest" not in columns:
            connection.execute("ALTER TABLE ont_instrument_runs ADD COLUMN terminal_artifact_manifest JSON")
        if "terminal_artifact_manifest_sha256" not in columns:
            connection.execute("ALTER TABLE ont_instrument_runs ADD COLUMN terminal_artifact_manifest_sha256 VARCHAR(64)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_ont_instrument_runs_terminal_artifact_manifest_sha256 "
            "ON ont_instrument_runs(terminal_artifact_manifest_sha256)"
        )
        _drop_manifest_triggers(connection)
        _backfill_manifest_rows(connection)
        _create_manifest_triggers(connection)
        connection.commit()
    finally:
        connection.close()

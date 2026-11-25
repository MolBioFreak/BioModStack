"""Remove sha256() UDF calls from ONT terminal manifest triggers (R23)."""

from __future__ import annotations

import sqlite3

_TERMINAL_SCHEMA = "bms.ont.terminal-artifact-manifest.v1"


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ont_instrument_runs'"
        ).fetchone()
        if table is None:
            return
        for operation in ("insert", "update"):
            update_clause = (
                " OF terminal_artifact_manifest, terminal_artifact_manifest_sha256, state, observed_generation, minknow_run_id"
                if operation == "update"
                else ""
            )
            connection.execute(
                f"DROP TRIGGER IF EXISTS ont_run_terminal_manifest_canonical_{operation}"
            )
            connection.execute(
                f"""
                CREATE TRIGGER ont_run_terminal_manifest_canonical_{operation}
                BEFORE {operation.upper()}{update_clause} ON ont_instrument_runs
                WHEN NEW.terminal_artifact_manifest IS NOT NULL
                  AND json_valid(NEW.terminal_artifact_manifest) = 1
                  AND (
                    NEW.terminal_artifact_manifest_sha256 IS NULL
                    OR length(NEW.terminal_artifact_manifest_sha256) != 64
                    OR NEW.terminal_artifact_manifest_sha256 GLOB '*[^0-9a-f]*'
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
                    OR length(json_extract(NEW.terminal_artifact_manifest, '$.minknow_run_id_sha256')) != 64
                    OR json_extract(NEW.terminal_artifact_manifest, '$.minknow_run_id_sha256') GLOB '*[^0-9a-f]*'
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
        connection.commit()
    finally:
        connection.close()

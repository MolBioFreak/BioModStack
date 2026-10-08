"""Bind state-analysis projection rows to their exact resolved candidate pair."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_PAIRS = "conformational_mapping_state_landscape_analysis_pairs"
_ROWS = "conformational_mapping_state_landscape_analysis_rows"
_HEADERS = "conformational_mapping_state_landscape_analysis_headers"
_LEGACY_PAIRS = f"{_PAIRS}__pre_pair_row_integrity"
_LEGACY_ROWS = f"{_ROWS}__pre_pair_row_integrity"


def _create_pairs(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE {_PAIRS} (
            request_id VARCHAR(36) NOT NULL,
            analysis_id VARCHAR(80) NOT NULL,
            pair_id VARCHAR(300) NOT NULL,
            candidate_a_id VARCHAR(128) NOT NULL,
            candidate_b_id VARCHAR(128) NOT NULL,
            PRIMARY KEY (request_id, analysis_id, pair_id),
            CONSTRAINT uq_cm_state_analysis_pair_candidates UNIQUE (
                request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id
            ),
            FOREIGN KEY(request_id, analysis_id)
              REFERENCES {_HEADERS}(request_id, analysis_id)
        )
        """
    )


def _create_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE {_ROWS} (
            id VARCHAR(96) PRIMARY KEY NOT NULL,
            request_id VARCHAR(36) NOT NULL,
            analysis_id VARCHAR(80) NOT NULL,
            pair_id VARCHAR(300) NOT NULL,
            candidate_a_id VARCHAR(128) NOT NULL,
            candidate_b_id VARCHAR(128) NOT NULL,
            target_id VARCHAR(128) NOT NULL,
            entity_instance_id VARCHAR(128) NOT NULL,
            auth_asym_id VARCHAR(128) NOT NULL,
            auth_seq_id INTEGER NOT NULL,
            insertion_code VARCHAR(16) NOT NULL,
            sequence_index INTEGER NOT NULL,
            validated_wt VARCHAR(1) NOT NULL,
            metrics_json JSON NOT NULL,
            availability_json JSON NOT NULL,
            CONSTRAINT uq_cm_state_analysis_row_identity UNIQUE(
                request_id, analysis_id, pair_id, entity_instance_id, auth_asym_id,
                auth_seq_id, insertion_code, sequence_index, validated_wt
            ),
            FOREIGN KEY(request_id, analysis_id)
              REFERENCES {_HEADERS}(request_id, analysis_id),
            FOREIGN KEY(request_id, analysis_id, pair_id)
              REFERENCES {_PAIRS}(request_id, analysis_id, pair_id),
            FOREIGN KEY(
                request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id
            ) REFERENCES {_PAIRS}(
                request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id
            )
        )
        """
    )


def _reject_invalid_existing_rows(connection: sqlite3.Connection) -> None:
    invalid = connection.execute(
        f"""
        SELECT rows.id
        FROM {_ROWS} AS rows
        LEFT JOIN {_PAIRS} AS pairs
          ON pairs.request_id = rows.request_id
         AND pairs.analysis_id = rows.analysis_id
         AND pairs.pair_id = rows.pair_id
        WHERE pairs.pair_id IS NULL
           OR pairs.candidate_a_id != rows.candidate_a_id
           OR pairs.candidate_b_id != rows.candidate_b_id
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError(
            "state-analysis pair-row integrity migration rejected invalid existing row "
            f"{invalid[0]!r}"
        )


def migrate(db_path: str | None = None) -> None:
    """Rebuild normalized projection tables with fail-closed pair-row foreign keys."""

    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        _reject_invalid_existing_rows(connection)

        connection.execute(f"ALTER TABLE {_ROWS} RENAME TO {_LEGACY_ROWS}")
        connection.execute(f"ALTER TABLE {_PAIRS} RENAME TO {_LEGACY_PAIRS}")
        _create_pairs(connection)
        connection.execute(
            f"INSERT INTO {_PAIRS} "
            "(request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id) "
            f"SELECT request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id FROM {_LEGACY_PAIRS}"
        )
        _create_rows(connection)
        connection.execute(
            f"INSERT INTO {_ROWS} "
            "(id, request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id, target_id, "
            "entity_instance_id, auth_asym_id, auth_seq_id, insertion_code, sequence_index, "
            "validated_wt, metrics_json, availability_json) "
            "SELECT id, request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id, target_id, "
            "entity_instance_id, auth_asym_id, auth_seq_id, insertion_code, sequence_index, "
            f"validated_wt, metrics_json, availability_json FROM {_LEGACY_ROWS}"
        )
        connection.execute(f"DROP TABLE {_LEGACY_ROWS}")
        connection.execute(f"DROP TABLE {_LEGACY_PAIRS}")
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_request ON {_ROWS} (request_id)"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_analysis ON {_ROWS} (analysis_id)"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_pair ON {_ROWS} (pair_id)"
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "state-analysis pair-row integrity migration produced foreign-key violations: "
                f"{violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from migrations.add_ont_instrument_run_ledger import migrate
from migrations.add_ont_protocol_preflight import migrate as migrate_preflight
from migrations.add_ont_terminal_artifact_manifests import migrate as migrate_terminal_artifacts
from migrations.sqlite_sha256 import register_sqlite_sha256


def _manifest_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    register_sqlite_sha256(connection)
    return connection


def _canonical_terminal_manifest(
    run_id: str,
    *,
    state: str = "completed",
    generation: int = 1,
    minknow_run_id: str = "",
    artifacts: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps(
        {
            "schema": "bms.ont.instrument-terminal-artifacts.v1",
            "schema_version": 1,
            "run_id": run_id,
            "minknow_run_id_sha256": hashlib.sha256(minknow_run_id.encode("utf-8")).hexdigest(),
            "terminal_state": state,
            "observed_generation": generation,
            "artifacts": artifacts
            if artifacts is not None
            else [{"kind": "fastq", "path": "/trusted/reads.fastq", "bytes": 12, "sha256": "a" * 64}],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _manifest_digest(manifest: str) -> str:
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _insert_manifest_row(connection: sqlite3.Connection, *, run_id: str, manifest: str, digest: str, minknow_run_id: str | None = None) -> None:
    connection.execute(
        """
        INSERT INTO ont_instrument_runs (
            id, position_id, minknow_run_id, state, observed_at, observed_generation,
            output_directories, output_files, handoff_ready, created_at,
            terminal_artifact_manifest, terminal_artifact_manifest_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            "X1",
            minknow_run_id,
            "completed",
            "2026-07-31T00:00:00Z",
            1,
            "{}",
            "{}",
            0,
            "2026-07-31T00:00:00Z",
            manifest,
            digest,
        ),
    )


def test_ont_instrument_run_ledger_migration_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"

    migrate(str(database_path))
    migrate(str(database_path))

    with _manifest_connection(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ont_instrument_runs)")
        }
        event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ont_instrument_run_events)")
        }
        unique_index_columns = {
            tuple(column[2] for column in connection.execute(f"PRAGMA index_info({row[1]!r})"))
            for row in connection.execute("PRAGMA index_list(ont_instrument_run_events)")
            if row[2]
        }

    assert {"ont_instrument_runs", "ont_instrument_run_events"} <= tables
    assert {"id", "position_id", "minknow_run_id", "state", "observed_at", "observed_generation"} <= run_columns
    assert {"run_id", "event_type", "state", "observed_at", "observed_generation"} <= event_columns
    assert ("run_id", "observed_generation") in unique_index_columns


def test_terminal_artifact_manifest_migration_is_idempotent_and_preserves_ledger_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    migrate_terminal_artifacts(str(database_path))
    migrate_terminal_artifacts(str(database_path))

    with _manifest_connection(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ont_instrument_runs)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(ont_instrument_runs)")}

    assert {"terminal_artifact_manifest", "terminal_artifact_manifest_sha256"} <= columns
    assert "ix_ont_instrument_runs_terminal_artifact_manifest_sha256" in indexes



def test_terminal_artifact_manifest_migration_backfills_canonical_rows_and_clears_invalid_legacy_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    canonical = _canonical_terminal_manifest("run-legacy", minknow_run_id="MNK-ORIGINAL")
    noncanonical = json.dumps(json.loads(canonical), sort_keys=True, ensure_ascii=True)

    with _manifest_connection(database_path) as connection:
        connection.execute("ALTER TABLE ont_instrument_runs ADD COLUMN terminal_artifact_manifest JSON")
        connection.execute("ALTER TABLE ont_instrument_runs ADD COLUMN terminal_artifact_manifest_sha256 VARCHAR(64)")
        _insert_manifest_row(
            connection,
            run_id="run-legacy",
            minknow_run_id="MNK-ORIGINAL",
            manifest=noncanonical,
            digest=_manifest_digest(canonical),
        )
        _insert_manifest_row(
            connection,
            run_id="run-invalid-legacy",
            manifest="not-json",
            digest="a" * 64,
        )

    migrate_terminal_artifacts(str(database_path))

    with _manifest_connection(database_path) as connection:
        assert connection.execute(
            "SELECT terminal_artifact_manifest, terminal_artifact_manifest_sha256 "
            "FROM ont_instrument_runs WHERE id = 'run-legacy'"
        ).fetchone() == (canonical, _manifest_digest(canonical))
        assert connection.execute(
            "SELECT terminal_artifact_manifest, terminal_artifact_manifest_sha256 "
            "FROM ont_instrument_runs WHERE id = 'run-invalid-legacy'"
        ).fetchone() == (None, None)


def test_terminal_artifact_manifest_database_guards_reject_fully_rehashed_mutants(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    migrate_terminal_artifacts(str(database_path))

    def manifest_for(run_id: str, *, artifacts: list[dict[str, object]] | None = None) -> dict[str, object]:
        return json.loads(
            _canonical_terminal_manifest(run_id, minknow_run_id="MNK-ORIGINAL", artifacts=artifacts)
        )

    artifacts = [
        {"kind": "fastq", "path": "/trusted/a.fastq", "bytes": 12, "sha256": "a" * 64},
        {"kind": "fastq", "path": "/trusted/b.fastq", "bytes": 13, "sha256": "b" * 64},
    ]
    malformed: list[tuple[str, dict[str, object], str | None]] = []

    forged = manifest_for("run-forged")
    malformed.append(("run-forged", forged, "0" * 64))

    rebound = manifest_for("run-rebound")
    rebound["minknow_run_id_sha256"] = hashlib.sha256(b"MNK-REBOUND").hexdigest()
    malformed.append(("run-rebound", rebound, None))

    artifact_cases: dict[str, list[dict[str, object]]] = {
        "run-placeholder-artifact": [{}],
        "run-extra-artifact-field": [{**artifacts[0], "forged": True}],
        "run-missing-artifact-field": [{key: value for key, value in artifacts[0].items() if key != "sha256"}],
        "run-invalid-kind": [{**artifacts[0], "kind": "fasta"}],
        "run-invalid-path": [{**artifacts[0], "path": ""}],
        "run-invalid-size": [{**artifacts[0], "bytes": -1}],
        "run-invalid-artifact-hash": [{**artifacts[0], "sha256": "A" * 64}],
        "run-empty-artifacts": [],
        "run-duplicate-artifact": [artifacts[0], dict(artifacts[0])],
        "run-out-of-order-artifacts": list(reversed(artifacts)),
    }
    for run_id, invalid_artifacts in artifact_cases.items():
        malformed.append((run_id, manifest_for(run_id, artifacts=invalid_artifacts), None))

    extra_top_level = manifest_for("run-extra-top-level")
    extra_top_level["forged"] = True
    malformed.append(("run-extra-top-level", extra_top_level, None))
    missing_top_level = manifest_for("run-missing-top-level")
    missing_top_level.pop("schema_version")
    malformed.append(("run-missing-top-level", missing_top_level, None))

    with _manifest_connection(database_path) as connection:
        for run_id, payload, supplied_digest in malformed:
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            with pytest.raises(sqlite3.IntegrityError):
                _insert_manifest_row(
                    connection,
                    run_id=run_id,
                    minknow_run_id="MNK-ORIGINAL",
                    manifest=serialized,
                    digest=supplied_digest or _manifest_digest(serialized),
                )

        noncanonical = json.dumps(manifest_for("run-noncanonical"), sort_keys=True, ensure_ascii=True)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_manifest_row(
                connection,
                run_id="run-noncanonical",
                minknow_run_id="MNK-ORIGINAL",
                manifest=noncanonical,
                digest=_manifest_digest(noncanonical),
            )

        nested_noncanonical_payload = manifest_for("run-nested-noncanonical")
        nested_noncanonical_payload["artifacts"] = [{
                "sha256": "a" * 64,
                "path": "/trusted/reads.fastq",
                "kind": "fastq",
                "bytes": 12,
            }]
        nested_noncanonical = json.dumps(nested_noncanonical_payload, separators=(",", ":"), ensure_ascii=True)
        assert nested_noncanonical != json.dumps(nested_noncanonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_manifest_row(
                connection,
                run_id="run-nested-noncanonical",
                minknow_run_id="MNK-ORIGINAL",
                manifest=nested_noncanonical,
                digest=_manifest_digest(nested_noncanonical),
            )

        valid = _canonical_terminal_manifest("run-valid", minknow_run_id="MNK-ORIGINAL")
        _insert_manifest_row(
            connection,
            run_id="run-valid",
            minknow_run_id="MNK-ORIGINAL",
            manifest=valid,
            digest=_manifest_digest(valid),
        )
        assert connection.execute(
            "SELECT terminal_artifact_manifest_sha256 FROM ont_instrument_runs WHERE id = 'run-valid'"
        ).fetchone() == (_manifest_digest(valid),)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ont_instrument_runs SET minknow_run_id = 'MNK-REBOUND' WHERE id = 'run-valid'"
            )


def test_terminal_artifact_manifest_schema_rejects_rewrites(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    migrate_terminal_artifacts(str(database_path))

    with _manifest_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO ont_instrument_runs (
                id, position_id, state, observed_at, observed_generation,
                output_directories, output_files, handoff_ready, created_at,
                terminal_artifact_manifest, terminal_artifact_manifest_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-freeze",
                "X1",
                "completed",
                "2026-07-31T00:00:00Z",
                1,
                "{}",
                "{}",
                0,
                "2026-07-31T00:00:00Z",
                _canonical_terminal_manifest("run-freeze"),
                _manifest_digest(_canonical_terminal_manifest("run-freeze")),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ont_instrument_runs SET terminal_artifact_manifest = ?, terminal_artifact_manifest_sha256 = ? WHERE id = ?",
                (_canonical_terminal_manifest("run-freeze").replace("reads.fastq", "replacement.fastq"), "b" * 64, "run-freeze"),
            )


def test_terminal_artifact_manifest_migration_rejects_noncanonical_json_and_cross_row_bindings(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    migrate_terminal_artifacts(str(database_path))

    malformed = "not-json"
    missing_version = json.loads(_canonical_terminal_manifest("run-missing-version"))
    missing_version.pop("schema_version")
    wrong_run = json.loads(_canonical_terminal_manifest("run-other"))
    wrong_run["run_id"] = "a-different-run"
    wrong_state = json.loads(_canonical_terminal_manifest("run-wrong-state"))
    wrong_state["terminal_state"] = "failed"
    wrong_generation = json.loads(_canonical_terminal_manifest("run-wrong-generation"))
    wrong_generation["observed_generation"] = 2
    empty_artifacts = json.loads(_canonical_terminal_manifest("run-empty"))
    empty_artifacts["artifacts"] = []

    invalid_manifests = [
        ("run-malformed", malformed),
        ("run-missing-version", json.dumps(missing_version)),
        ("run-other", json.dumps(wrong_run)),
        ("run-wrong-state", json.dumps(wrong_state)),
        ("run-wrong-generation", json.dumps(wrong_generation)),
        ("run-empty", json.dumps(empty_artifacts)),
    ]
    with _manifest_connection(database_path) as connection:
        for run_id, manifest in invalid_manifests:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO ont_instrument_runs (
                        id, position_id, state, observed_at, observed_generation,
                        output_directories, output_files, handoff_ready, created_at,
                        terminal_artifact_manifest, terminal_artifact_manifest_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, "X1", "completed", "2026-07-31T00:00:00Z", 1, "{}", "{}", 0,
                     "2026-07-31T00:00:00Z", manifest, "a" * 64),
                )


    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))

    with _manifest_connection(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO ont_instrument_runs (
                id, position_id, minknow_run_id, state, observed_at,
                observed_generation, output_directories, output_files,
                handoff_ready, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "X1", "MNK-1", "completed", "2026-07-31T00:00:00Z", 1, "{}", "{}", 0, "2026-07-31T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO ont_instrument_run_events (
                id, run_id, event_type, state, observed_at,
                observed_generation, minknow_payload, output_files
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("event-1", "run-1", "start_observed", "completed", "2026-07-31T00:00:00Z", 1, "{}", "{}"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE ont_instrument_run_events SET state = 'failed' WHERE id = 'event-1'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM ont_instrument_run_events WHERE id = 'event-1'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM ont_instrument_runs WHERE id = 'run-1'")

        assert connection.execute("SELECT state FROM ont_instrument_run_events WHERE id = 'event-1'").fetchone() == ("completed",)
        assert connection.execute("SELECT run_id FROM ont_instrument_run_events WHERE id = 'event-1'").fetchone() == ("run-1",)


def test_ont_protocol_preflight_migration_creates_receipts_linked_to_ledger(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.db"
    migrate(str(database_path))
    migrate_preflight(str(database_path))
    migrate_preflight(str(database_path))

    with _manifest_connection(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        receipt_columns = {row[1] for row in connection.execute("PRAGMA table_info(ont_protocol_option_receipts)")}
        preflight_columns = {row[1] for row in connection.execute("PRAGMA table_info(ont_instrument_run_preflights)")}
        preflight_fks = {
            row[2]
            for row in connection.execute("PRAGMA foreign_key_list(ont_instrument_run_preflights)")
        }

    assert {"ont_protocol_option_receipts", "ont_instrument_run_preflights"} <= tables
    assert {"option_id", "position_id", "flow_cell_identity_sha256", "expires_at", "consumed_at"} <= receipt_columns
    assert {"run_id", "option_receipt_id", "source_snapshot", "invalidation_reason"} <= preflight_columns
    assert {"ont_instrument_runs", "ont_protocol_option_receipts"} <= preflight_fks

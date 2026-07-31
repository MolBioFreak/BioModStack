from __future__ import annotations

import sqlite3

import pytest

from migrations import runner

from migrations.runner import (
    MIGRATIONS,
    _ensure_migrations_table,
    _reconcile_legacy_ont_migration_versions,
)


LEGACY_ONT_ROWS = (
    (17, "add_ont_instrument_run_ledger"),
    (18, "add_ont_protocol_preflight"),
    (19, "add_ont_terminal_artifact_manifests"),
    (20, "enforce_ont_terminal_artifact_manifest_immutability"),
)


def _insert_rows(connection: sqlite3.Connection, rows: tuple[tuple[int, str], ...]) -> None:
    connection.executemany(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, 'legacy')",
        rows,
    )
    connection.commit()


def test_migration_versions_are_unique_with_md_before_ont() -> None:
    observed = [(migration.version, migration.name) for migration in MIGRATIONS if migration.version >= 17]
    assert observed == [
        (17, "add_md_lifecycle"),
        (18, "add_ont_instrument_run_ledger"),
        (19, "add_ont_protocol_preflight"),
        (20, "add_ont_terminal_artifact_manifests"),
        (21, "enforce_ont_terminal_artifact_manifest_immutability"),
    ]
    assert len({migration.version for migration in MIGRATIONS}) == len(MIGRATIONS)


def test_legacy_ont_17_to_20_ledger_is_transactionally_shifted_for_md_17() -> None:
    connection = sqlite3.connect(":memory:")
    _ensure_migrations_table(connection)
    _insert_rows(connection, LEGACY_ONT_ROWS)

    _reconcile_legacy_ont_migration_versions(connection)
    connection.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (17, 'add_md_lifecycle', 'now')"
    )
    connection.commit()

    assert connection.execute(
        "SELECT version, name FROM schema_migrations WHERE version >= 17 ORDER BY version"
    ).fetchall() == [
        (17, "add_md_lifecycle"),
        (18, "add_ont_instrument_run_ledger"),
        (19, "add_ont_protocol_preflight"),
        (20, "add_ont_terminal_artifact_manifests"),
        (21, "enforce_ont_terminal_artifact_manifest_immutability"),
    ]


def test_legacy_ont_remap_fails_closed_and_rolls_back_on_unrelated_collision() -> None:
    connection = sqlite3.connect(":memory:")
    _ensure_migrations_table(connection)
    _insert_rows(
        connection,
        (
            (17, "add_ont_instrument_run_ledger"),
            (18, "unrelated_migration"),
        ),
    )

    with pytest.raises(RuntimeError, match="occupied by unrelated_migration"):
        _reconcile_legacy_ont_migration_versions(connection)

    assert connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall() == [
        (17, "add_ont_instrument_run_ledger"),
        (18, "unrelated_migration"),
    ]


def test_runner_fails_closed_when_applied_version_has_wrong_name(tmp_path) -> None:
    db_path = tmp_path / "wrong-migration-name.db"
    connection = sqlite3.connect(db_path)
    _ensure_migrations_table(connection)
    _insert_rows(
        connection,
        tuple(
            (migration.version, "unrelated_migration" if migration.version == 18 else migration.name)
            for migration in MIGRATIONS
        ),
    )
    connection.close()

    with pytest.raises(RuntimeError, match="version 18.*unrelated_migration.*add_ont_instrument_run_ledger"):
        runner.run_all(str(db_path))


@pytest.mark.parametrize(
    "applied",
    [
        {22: "unrelated_migration"},
        {21: "enforce_ont_terminal_artifact_manifest_immutability"},
    ],
)
def test_migration_ledger_must_be_an_exact_contiguous_known_prefix(applied: dict[int, str]) -> None:
    with pytest.raises(RuntimeError, match="contiguous exact prefix"):
        runner._validate_applied_migration_identities(applied)

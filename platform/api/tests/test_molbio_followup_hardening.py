from __future__ import annotations

import asyncio
import fcntl
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import (  # noqa: E402
    create_molbio_engine,
    init_molbio_db,
    molbio_health,
)
from molbio_migrations import (  # noqa: E402
    MigrationVerificationError,
    extract_legacy_molbio_data,
)

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_molbio_database import _create_legacy_source  # noqa: E402


@pytest.mark.asyncio
async def test_health_and_migration_reject_cyclic_sequence_parent_lineage(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cyclic-lineage.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    await init_molbio_db(engine=engine)
    await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for sequence_id in ("cycle-a", "cycle-b"):
            connection.execute(
                "INSERT INTO nucleotide_sequences "
                "(id, name, sequence, sequence_type, molecule_strandedness, "
                "molecule_orientation, is_circular, length, parent_id, version, created_at) "
                "VALUES (?, ?, 'ACGT', 'dna', 'double', 'unknown', 0, 4, NULL, 1, CURRENT_TIMESTAMP)",
                (sequence_id, sequence_id),
            )
        connection.execute(
            "UPDATE nucleotide_sequences SET parent_id='cycle-b' WHERE id='cycle-a'"
        )
        connection.execute(
            "UPDATE nucleotide_sequences SET parent_id='cycle-a' WHERE id='cycle-b'"
        )
        connection.commit()

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        health = await molbio_health(engine=engine)
        assert health["sequence_parent_cycle_count"] == 1
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM molbio_schema_migrations "
            "WHERE version='0004_sequence_parent_foreign_key'"
        )
        connection.commit()

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        with pytest.raises(RuntimeError, match="cyclic|cycle"):
            await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_identical_extractions_serialize_per_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "concurrent-source.db"
    destination = tmp_path / "concurrent-destination.db"
    _create_legacy_source(source)

    reports = await asyncio.gather(
        extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / "concurrent-backup-a.db",
        ),
        extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / "concurrent-backup-b.db",
        ),
    )

    assert sorted(report.copied_sequences for report in reports) == [0, 2]
    assert sorted(report.skipped_sequences for report in reports) == [0, 2]
    assert sorted(report.copied_primers for report in reports) == [0, 1]
    assert sorted(report.skipped_primers for report in reports) == [0, 1]


def _assert_destination_extraction_lock_available(destination: Path) -> None:
    lock_path = destination.with_suffix(destination.suffix + ".extract.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@pytest.mark.asyncio
async def test_extraction_releases_destination_lock_when_engine_disposal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    source = tmp_path / "dispose-failure-source.db"
    destination = tmp_path / "dispose-failure-destination.db"
    _create_legacy_source(source)

    async def fail_disposal(self) -> None:
        raise RuntimeError("forced engine disposal failure")

    monkeypatch.setattr(AsyncEngine, "dispose", fail_disposal)

    with pytest.raises(RuntimeError, match="forced engine disposal failure"):
        await extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / "dispose-failure-backup.db",
        )

    _assert_destination_extraction_lock_available(destination)


@pytest.mark.asyncio
async def test_extraction_releases_destination_lock_when_cancelled_during_engine_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    source = tmp_path / "dispose-cancel-source.db"
    destination = tmp_path / "dispose-cancel-destination.db"
    _create_legacy_source(source)
    disposal_entered = asyncio.Event()
    never_finish = asyncio.Event()

    async def block_disposal(self) -> None:
        disposal_entered.set()
        await never_finish.wait()

    monkeypatch.setattr(AsyncEngine, "dispose", block_disposal)
    extraction = asyncio.create_task(
        extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / "dispose-cancel-backup.db",
        )
    )
    await asyncio.wait_for(disposal_entered.wait(), timeout=5)
    extraction.cancel()
    with pytest.raises(asyncio.CancelledError):
        await extraction

    _assert_destination_extraction_lock_available(destination)


@pytest.mark.asyncio
async def test_health_rejects_same_name_conditional_noop_immutable_trigger(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conditional-trigger.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await init_molbio_db(engine=engine)
        async with engine.begin() as connection:
            await connection.execute(
                text("DROP TRIGGER molbio_immutable_primer_revisions_update")
            )
            await connection.execute(
                text(
                    """
                    CREATE TRIGGER molbio_immutable_primer_revisions_update
                    BEFORE UPDATE ON primer_revisions
                    WHEN 0
                    BEGIN
                        SELECT RAISE(ABORT, 'primer_revisions is immutable');
                    END
                    """
                )
            )

        health = await molbio_health(engine=engine)
        assert health["immutable_trigger_count"] == 20
        assert health["immutable_triggers_current"] is False
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_health_rejects_claimed_migration_when_sequence_self_fk_is_missing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-self-fk.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    await init_molbio_db(engine=engine)
    await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nucleotide_sequences'"
        ).fetchone()[0]
        replacement_sql = table_sql.replace(
            "CREATE TABLE nucleotide_sequences",
            "CREATE TABLE nucleotide_sequences_without_parent_fk",
            1,
        )
        replacement_sql = re.sub(
            r",\s*FOREIGN KEY\(parent_id\) REFERENCES nucleotide_sequences \(id\) "
            r"ON DELETE RESTRICT",
            "",
            replacement_sql,
        )
        connection.execute(replacement_sql)
        connection.execute("DROP TABLE nucleotide_sequences")
        connection.execute(
            "ALTER TABLE nucleotide_sequences_without_parent_fk RENAME TO nucleotide_sequences"
        )
        connection.commit()
        assert not any(
            row[2] == "nucleotide_sequences"
            and row[3] == "parent_id"
            and row[4] == "id"
            for row in connection.execute(
                "PRAGMA foreign_key_list(nucleotide_sequences)"
            ).fetchall()
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM molbio_schema_migrations "
            "WHERE version='0004_sequence_parent_foreign_key'"
        ).fetchone() == (1,)

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        health = await molbio_health(engine=engine)
        assert health["migrations_current"] is True
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_health_rejects_sequence_self_fk_with_wrong_on_update_action(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wrong-parent-on-update.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    await init_molbio_db(engine=engine)
    await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nucleotide_sequences'"
        ).fetchone()[0]
        replacement_sql = table_sql.replace(
            "CREATE TABLE nucleotide_sequences",
            "CREATE TABLE nucleotide_sequences_wrong_on_update",
            1,
        ).replace(
            "ON DELETE RESTRICT",
            "ON UPDATE CASCADE ON DELETE RESTRICT",
            1,
        )
        connection.execute(replacement_sql)
        connection.execute("DROP TABLE nucleotide_sequences")
        connection.execute(
            "ALTER TABLE nucleotide_sequences_wrong_on_update RENAME TO nucleotide_sequences"
        )
        connection.commit()
        parent_fk = next(
            row
            for row in connection.execute(
                "PRAGMA foreign_key_list(nucleotide_sequences)"
            ).fetchall()
            if row[2] == "nucleotide_sequences"
            and row[3] == "parent_id"
            and row[4] == "id"
        )
        assert parent_fk[5] == "CASCADE"
        assert parent_fk[6] == "RESTRICT"
        assert connection.execute(
            "SELECT COUNT(*) FROM molbio_schema_migrations "
            "WHERE version='0004_sequence_parent_foreign_key'"
        ).fetchone() == (1,)

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        health = await molbio_health(engine=engine)
        assert health["migrations_current"] is True
        assert health["sequence_parent_foreign_key_current"] is False
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_replaces_composite_counterfeit_sequence_parent_fk(
    tmp_path: Path,
) -> None:
    database = tmp_path / "composite-counterfeit-parent-fk.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    await init_molbio_db(engine=engine)
    await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nucleotide_sequences'"
        ).fetchone()[0]
        replacement_sql = table_sql.replace(
            "CREATE TABLE nucleotide_sequences",
            "CREATE TABLE nucleotide_sequences_composite_parent_fk",
            1,
        )
        replacement_sql = re.sub(
            r"FOREIGN KEY\(parent_id\) REFERENCES nucleotide_sequences \(id\) "
            r"ON DELETE RESTRICT",
            "UNIQUE (id, parent_id), "
            "FOREIGN KEY(parent_id, id) REFERENCES nucleotide_sequences (id, parent_id) "
            "ON DELETE RESTRICT",
            replacement_sql,
            count=1,
        )
        assert "FOREIGN KEY(parent_id, id)" in replacement_sql
        connection.execute(replacement_sql)
        connection.execute("DROP TABLE nucleotide_sequences")
        connection.execute(
            "ALTER TABLE nucleotide_sequences_composite_parent_fk "
            "RENAME TO nucleotide_sequences"
        )
        connection.execute(
            "DELETE FROM molbio_schema_migrations "
            "WHERE version='0004_sequence_parent_foreign_key'"
        )
        connection.commit()
        counterfeit_rows = connection.execute(
            "PRAGMA foreign_key_list(nucleotide_sequences)"
        ).fetchall()
        assert len(counterfeit_rows) == 2
        assert counterfeit_rows[0][0] == counterfeit_rows[1][0]
        assert counterfeit_rows[0][1] == 0
        assert counterfeit_rows[0][2:7] == (
            "nucleotide_sequences",
            "parent_id",
            "id",
            "NO ACTION",
            "RESTRICT",
        )

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await init_molbio_db(engine=engine)
        health = await molbio_health(engine=engine)
        assert health["sequence_parent_foreign_key_current"] is True
        assert health["status"] == "healthy"
    finally:
        await engine.dispose()

    with sqlite3.connect(database) as connection:
        repaired_rows = connection.execute(
            "PRAGMA foreign_key_list(nucleotide_sequences)"
        ).fetchall()
        assert len(repaired_rows) == 1
        assert repaired_rows[0][0:7] == (
            0,
            0,
            "nucleotide_sequences",
            "parent_id",
            "id",
            "NO ACTION",
            "RESTRICT",
        )


@pytest.mark.asyncio
async def test_extraction_orders_parent_before_lexically_earlier_child(
    tmp_path: Path,
) -> None:
    source = tmp_path / "child-first-source.db"
    destination = tmp_path / "child-first-destination.db"
    _create_legacy_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE nucleotide_sequences SET id='z-parent' WHERE id='seq-1'"
        )
        connection.execute(
            "UPDATE nucleotide_sequences SET id='a-child', parent_id='z-parent' "
            "WHERE id='seq-2'"
        )
        connection.execute(
            "UPDATE primers SET target_sequence_id='z-parent' "
            "WHERE target_sequence_id='seq-1'"
        )
        connection.commit()

    report = await extract_legacy_molbio_data(
        source,
        destination,
        backup_path=tmp_path / "child-first-backup.db",
    )

    assert report.copied_sequences == 2
    assert report.copied_primers == 1
    with sqlite3.connect(destination) as connection:
        assert connection.execute(
            "SELECT id, parent_id FROM nucleotide_sequences ORDER BY id"
        ).fetchall() == [("a-child", "z-parent"), ("z-parent", None)]


def _clone_row(
    connection: sqlite3.Connection,
    table: str,
    where_column: str,
    where_value: str,
    **overrides: object,
) -> None:
    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    original = connection.execute(
        f'SELECT * FROM "{table}" WHERE "{where_column}"=? LIMIT 1',
        (where_value,),
    ).fetchone()
    assert original is not None
    values = dict(zip(columns, original, strict=True))
    values.update(overrides)
    placeholders = ", ".join("?" for _ in columns)
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
        [values[column] for column in columns],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "document_name",
        "surplus_sequence_revision",
        "surplus_document",
        "surplus_primer_revision",
    ],
)
async def test_idempotent_extraction_rejects_surplus_or_corrupt_complete_history_graph(
    tmp_path: Path,
    corruption: str,
) -> None:
    source = tmp_path / "history-source.db"
    destination = tmp_path / "history-destination.db"
    _create_legacy_source(source)
    await extract_legacy_molbio_data(
        source,
        destination,
        backup_path=tmp_path / "history-first-backup.db",
    )

    with sqlite3.connect(destination) as connection:
        if corruption == "document_name":
            connection.execute(
                "UPDATE molecular_documents SET name='tampered' WHERE id='seq-1'"
            )
        elif corruption == "surplus_sequence_revision":
            _clone_row(
                connection,
                "molecular_revisions",
                "document_id",
                "seq-1",
                id="surplus-sequence-revision",
                revision_number=99,
                change_kind="surplus",
            )
        elif corruption == "surplus_document":
            connection.execute(
                "INSERT INTO molecular_documents "
                "(id, document_kind, name, current_revision_id, created_at, deleted_at) "
                "VALUES ('surplus-document', 'dna', 'surplus', NULL, CURRENT_TIMESTAMP, NULL)"
            )
        else:
            _clone_row(
                connection,
                "primer_revisions",
                "primer_id",
                "primer-1",
                id="surplus-primer-revision",
                revision_number=0,
                change_kind="surplus",
            )
        connection.commit()

    with pytest.raises(MigrationVerificationError, match="history|graph"):
        await extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / f"history-{corruption}-backup.db",
        )


@pytest.mark.asyncio
async def test_extraction_rejects_non_ok_destination_quick_check_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    source = tmp_path / "quick-check-source.db"
    destination = tmp_path / "quick-check-destination.db"
    _create_legacy_source(source)
    original_execute = AsyncSession.execute

    async def poison_quick_check(self, statement, *args, **kwargs):
        if str(statement).strip().casefold() == "pragma quick_check":
            class FailedQuickCheck:
                @staticmethod
                def fetchall():
                    return [("database disk image is malformed",)]

            return FailedQuickCheck()
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", poison_quick_check)

    with pytest.raises(MigrationVerificationError, match="quick_check"):
        await extract_legacy_molbio_data(
            source,
            destination,
            backup_path=tmp_path / "quick-check-backup.db",
        )

    with sqlite3.connect(destination) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM nucleotide_sequences"
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM primers").fetchone() == (0,)


def _create_marker_database(path: Path, marker: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES (?)", (marker,))
        connection.commit()


def test_backup_rejects_source_swapped_only_during_sqlite_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sqlite_backup as backup_module

    source = tmp_path / "source.db"
    replacement = tmp_path / "replacement.db"
    destination = tmp_path / "backup.db"
    parked_source = tmp_path / "parked-source.db"
    parked_replacement = tmp_path / "parked-replacement.db"
    _create_marker_database(source, "original")
    _create_marker_database(replacement, "counterfeit")
    source_uri = f"file:{source.resolve()}?mode=ro"
    real_connect = backup_module.sqlite3.connect
    swapped = False

    def swap_for_open(database, *args, **kwargs):
        nonlocal swapped
        if database == source_uri and not swapped:
            swapped = True
            os.replace(source, parked_source)
            os.replace(replacement, source)
            connection = real_connect(database, *args, **kwargs)
            os.replace(source, parked_replacement)
            os.replace(parked_source, source)
            return connection
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(backup_module.sqlite3, "connect", swap_for_open)

    with pytest.raises(RuntimeError, match="source|identity"):
        backup_module.backup_sqlite_database(source, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.db.tmp-*"))


def test_backup_rejects_byte_identical_publication_inode_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sqlite_backup as backup_module

    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    counterfeit = tmp_path / "counterfeit.db"
    displaced_publication = tmp_path / "displaced-publication.db"
    _create_marker_database(source, "original")
    real_link = backup_module.os.link
    swapped = False

    def link_then_swap(source_path, destination_path, *args, **kwargs):
        nonlocal swapped
        result = real_link(source_path, destination_path, *args, **kwargs)
        if not swapped:
            swapped = True
            shutil.copy2(destination, counterfeit)
            os.replace(destination, displaced_publication)
            os.replace(counterfeit, destination)
        return result

    monkeypatch.setattr(backup_module.os, "link", link_then_swap)

    with pytest.raises(RuntimeError, match="publication|identity"):
        backup_module.backup_sqlite_database(source, destination)


def test_backup_removes_own_publication_after_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.sqlite_backup as backup_module

    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    _create_marker_database(source, "original")
    real_link = backup_module.os.link

    def link_then_fail(source_path, destination_path, *args, **kwargs):
        real_link(source_path, destination_path, *args, **kwargs)
        raise RuntimeError("forced post-publication verification failure")

    monkeypatch.setattr(backup_module.os, "link", link_then_fail)

    with pytest.raises(RuntimeError, match="forced post-publication"):
        backup_module.backup_sqlite_database(source, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.db.tmp-*"))

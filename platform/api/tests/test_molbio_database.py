from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from stat import S_IMODE
import sys
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


async def _extract_approved(source: Path, destination: Path, **kwargs):
    from molbio_migrations import (
        extract_legacy_molbio_data as _extract_legacy_molbio_data,
        legacy_molbio_manifest_sha256,
    )

    kwargs.setdefault("expected_manifest_sha256", legacy_molbio_manifest_sha256(source))
    return await _extract_legacy_molbio_data(source, destination, **kwargs)


def _create_legacy_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE nucleotide_sequences (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                sequence TEXT NOT NULL,
                sequence_type TEXT NOT NULL,
                molecule_strandedness TEXT NOT NULL DEFAULT 'unknown',
                molecule_orientation TEXT NOT NULL DEFAULT 'unknown',
                is_circular INTEGER,
                length INTEGER NOT NULL,
                features JSON,
                primers JSON,
                analysis_tracks JSON,
                organism TEXT,
                accession TEXT,
                source_file TEXT,
                parent_id TEXT,
                operation TEXT,
                operation_params JSON,
                version INTEGER NOT NULL DEFAULT 1,
                gc_content REAL,
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE TABLE primers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sequence TEXT NOT NULL,
                sequence_type TEXT NOT NULL DEFAULT 'dna',
                length INTEGER NOT NULL,
                tm REAL,
                gc_percent REAL,
                tm_algorithm TEXT,
                tm_salt_correction TEXT,
                tm_settings JSON,
                primer_type TEXT,
                description TEXT,
                target_sequence_id TEXT,
                binding_start INTEGER,
                binding_end INTEGER,
                binding_strand INTEGER,
                tags JSON,
                is_favorite INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            );
            """
        )
        sequences = [
            (
                "seq-1",
                "Template A",
                "first",
                "ATGCGCAT",
                "dna",
                "double",
                "not_applicable",
                1,
                8,
                json.dumps([{"id": "f1", "name": "feature", "start": 0, "end": 4}]),
                json.dumps([]),
                json.dumps([]),
                "test organism",
                "ACC001",
                "a.gb",
                None,
                None,
                None,
                1,
                50.0,
                "2026-07-01 10:00:00",
                None,
            ),
            (
                "seq-2",
                "Template B",
                None,
                "AUGC",
                "rna",
                "single",
                "positive",
                0,
                4,
                json.dumps([]),
                json.dumps([]),
                json.dumps([{"id": "track-1", "values": [0.1, 0.2, 0.3, 0.4]}]),
                None,
                None,
                "b.fasta",
                "seq-1",
                "transcribe",
                json.dumps({"source": "test"}),
                2,
                50.0,
                "2026-07-02T10:00:00Z",
                "2026-07-03T10:00:00Z",
            ),
        ]
        connection.executemany(
            "INSERT INTO nucleotide_sequences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            sequences,
        )
        connection.execute(
            "INSERT INTO primers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "primer-1",
                "Forward",
                "ATGCGC",
                "dna",
                6,
                58.5,
                66.67,
                "Tm_NN",
                "saltcorr=5",
                json.dumps({"Na": 50.0}),
                "forward",
                "test primer",
                "seq-1",
                0,
                6,
                1,
                json.dumps(["test"]),
                1,
                "2026-07-04 10:00:00",
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_molbio_path_and_metadata_are_independently_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "owned" / "molbio-custom.db"
    monkeypatch.setenv("BMS_MOLBIO_DB_PATH", str(override))

    from database import Base
    from molbio_database import get_molbio_database_url, get_molbio_path
    from molbio_models import MolBioBase

    assert get_molbio_path() == override.resolve()
    assert get_molbio_database_url() == f"sqlite+aiosqlite:///{override.resolve()}"
    assert MolBioBase.metadata is not Base.metadata
    assert {
        "molbio_schema_migrations",
        "nucleotide_sequences",
        "primers",
        "molecular_documents",
        "molecular_revisions",
        "molecular_operations",
        "molecular_operation_inputs",
        "molecular_operation_outputs",
        "molecular_import_batches",
        "primer_revisions",
        "pcr_experiments",
        "pcr_experiment_revisions",
        "tm_models",
        "tm_model_revisions",
        "polymerase_presets",
        "polymerase_preset_revisions",
        "molbio_audit_events",
        "molbio_outbox_events",
    }.issubset(MolBioBase.metadata.tables)


@pytest.mark.asyncio
async def test_initialization_applies_ordered_migrations_and_sqlite_invariants(tmp_path: Path) -> None:
    from molbio_database import (
        create_molbio_engine,
        get_applied_molbio_migrations,
        init_molbio_db,
        molbio_health,
    )

    database = tmp_path / "molbio.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await init_molbio_db(engine=engine)
        assert await get_applied_molbio_migrations(engine=engine) == [
            "0001_initial",
            "0002_append_only_guards",
            "0003_idempotency_and_soft_delete",
            "0004_sequence_parent_foreign_key",
            "0005_authoritative_import_batches",
            "0006_project_plasmid_metadata",
        ]
        async with engine.connect() as connection:
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one()
        assert foreign_keys == 1
        assert str(journal_mode).lower() == "wal"
        assert busy_timeout == 30000
        assert await molbio_health(engine=engine) == {
            "owner": "molbio",
            "database_kind": "sqlite",
            "status": "healthy",
            "quick_check": "ok",
            "foreign_key_violations": 0,
            "migration_count": 6,
            "latest_migration": "0006_project_plasmid_metadata",
            "migrations_current": True,
            "database_schema_current": True,
            "database_schema_issue_count": 0,
            "immutable_trigger_count": 22,
            "immutable_triggers_current": True,
            "sequence_parent_foreign_key_current": True,
            "sequence_parent_cycle_count": 0,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_immutable_revision_rows_reject_update_and_delete(tmp_path: Path) -> None:
    from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
    from molbio_models import MolecularDocument, MolecularRevision

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'immutable.db'}")
    await init_molbio_db(engine=engine)
    factory = make_molbio_session_factory(engine)
    try:
        async with factory() as session:
            document = MolecularDocument(id="document-1", document_kind="dna", name="Template")
            revision = MolecularRevision(
                id="revision-1",
                document_id=document.id,
                revision_number=1,
                change_kind="create",
                content_sha256=hashlib.sha256(b"ATGC").hexdigest(),
                content_length=4,
                snapshot={"sequence": "ATGC"},
                provenance={"source": "test"},
            )
            session.add_all([document, revision])
            await session.flush()
            document.current_revision_id = revision.id
            await session.commit()

            with pytest.raises(DatabaseError, match="immutable"):
                await session.execute(
                    text("UPDATE molecular_revisions SET snapshot = '{}' WHERE id = 'revision-1'")
                )
                await session.commit()
            await session.rollback()

            with pytest.raises(DatabaseError, match="immutable"):
                await session.execute(text("DELETE FROM molecular_revisions WHERE id = 'revision-1'"))
                await session.commit()
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_api_health_aggregates_molbio_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    import main as api_main

    async def healthy():
        return {
            "status": "healthy",
            "quick_check": "ok",
            "foreign_key_violations": 0,
            "migration_count": 6,
            "latest_migration": "0006_project_plasmid_metadata",
            "migrations_current": True,
            "database_schema_current": True,
            "database_schema_issue_count": 0,
            "immutable_trigger_count": 22,
            "immutable_triggers_current": True,
            "sequence_parent_foreign_key_current": True,
            "sequence_parent_cycle_count": 0,
        }

    monkeypatch.setattr(api_main, "molbio_health", healthy)
    payload = await api_main.health_check()
    assert payload["status"] == "healthy"
    assert payload["molbio"]["latest_migration"] == "0006_project_plasmid_metadata"

    async def degraded():
        return {
            "status": "degraded",
            "quick_check": "ok",
            "foreign_key_violations": 1,
            "migration_count": 3,
            "latest_migration": "0003_idempotency_and_soft_delete",
            "migrations_current": True,
            "immutable_trigger_count": 22,
            "immutable_triggers_current": True,
        }

    monkeypatch.setattr(api_main, "molbio_health", degraded)
    payload = await api_main.health_check()
    assert payload["status"] == "degraded"


def test_online_backup_captures_consistent_wal_database(tmp_path: Path) -> None:
    from services.sqlite_backup import backup_sqlite_database

    source = tmp_path / "source.db"
    backup = tmp_path / "backups" / "source-before-migration.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO records(value) VALUES (?)", [("one",), ("two",), ("three",)])
        connection.commit()
    finally:
        connection.close()

    report = backup_sqlite_database(source, backup)

    with sqlite3.connect(backup) as restored:
        assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT value FROM records ORDER BY id").fetchall() == [
            ("one",),
            ("two",),
            ("three",),
        ]
    assert report.source_path == source.resolve()
    assert report.backup_path == backup.resolve()
    assert report.size_bytes == backup.stat().st_size
    assert S_IMODE(backup.stat().st_mode) == 0o600
    assert report.sha256 == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert report.integrity_check == "ok"
    assert report.quick_check == "ok"
    assert report.source_snapshot["schema"] == "bms.sqlite-backup-source-preimage.v1"
    assert report.source_snapshot["database_identity_sha256"]
    assert report.source_snapshot["source_size_bytes"] == source.stat().st_size
    assert report.source_snapshot["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report.source_snapshot["page_size"] == 4096
    assert report.source_snapshot["page_count"] >= 1
    assert report.source_snapshot["schema_version"] >= 0
    assert report.source_snapshot["data_version"] >= 0
    assert report.source_snapshot["integrity_check"] == "ok"
    assert report.source_snapshot["foreign_key_violations"] == 0


def test_reconciliation_backup_checkpoints_wal_before_source_hash(tmp_path: Path) -> None:
    from services.sqlite_backup import backup_sqlite_database, inspect_sqlite_source_snapshot

    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE records (value TEXT NOT NULL)")
        writer.execute("INSERT INTO records VALUES ('committed-in-wal')")
        writer.commit()
        wal_path = Path(f"{source}-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0

        report = backup_sqlite_database(
            source,
            backup,
            database_identity_sha256="8" * 64,
            checkpoint_wal=True,
        )
    finally:
        writer.close()

    assert not wal_path.exists() or wal_path.stat().st_size == 0
    assert report.source_snapshot["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report.source_snapshot == inspect_sqlite_source_snapshot(
        source,
        database_identity_sha256="8" * 64,
    )
    with sqlite3.connect(backup) as restored:
        assert restored.execute("SELECT value FROM records").fetchall() == [("committed-in-wal",)]


def test_reconciliation_backup_retains_source_connection_under_writer_reservation(tmp_path: Path) -> None:
    from services.sqlite_backup import (
        backup_sqlite_database,
        checkpoint_sqlite_wal,
        open_attested_sqlite_readonly_connection,
    )

    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    with sqlite3.connect(source) as setup:
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE records (value TEXT NOT NULL)")
        setup.execute("INSERT INTO records VALUES ('before-reservation')")
        setup.commit()

    retained = open_attested_sqlite_readonly_connection(source)
    owner = sqlite3.connect(source, timeout=0.05)
    contender = sqlite3.connect(source, timeout=0.05)
    try:
        owner.execute("BEGIN IMMEDIATE")
        checkpoint_sqlite_wal(source, mode="PASSIVE")
        report = backup_sqlite_database(
            source,
            backup,
            database_identity_sha256="8" * 64,
            source_connection=retained,
        )
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            contender.execute("INSERT INTO records VALUES ('raced-writer')")
        owner.rollback()
    finally:
        contender.close()
        owner.close()
        retained.close()

    assert report.source_snapshot["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    with sqlite3.connect(backup) as restored:
        assert restored.execute("SELECT value FROM records").fetchall() == [("before-reservation",)]


@pytest.mark.asyncio
async def test_backup_first_legacy_extraction_is_verified_and_idempotent(tmp_path: Path) -> None:
    from molbio_database import create_molbio_engine, make_molbio_session_factory
    from molbio_migrations import extract_legacy_molbio_data
    from molbio_models import MolecularRevision, NucleotideSequence, PrimerRevision

    source = tmp_path / "biomodstack.db"
    destination = tmp_path / "molbio.db"
    backup = tmp_path / "backups" / "biomodstack.before-molbio.db"
    _create_legacy_source(source)

    first = await _extract_approved(source, destination, backup_path=backup)
    second = await _extract_approved(
        source,
        destination,
        backup_path=tmp_path / "backups" / "biomodstack.before-second-pass.db",
    )

    expected_checksums = {
        "seq-1": hashlib.sha256(b"ATGCGCAT").hexdigest(),
        "seq-2": hashlib.sha256(b"AUGC").hexdigest(),
    }
    assert first.source_sequence_count == 2
    assert first.destination_sequence_count == 2
    assert first.total_bases == 12
    assert first.sequence_checksums == expected_checksums
    assert first.copied_sequences == 2
    assert first.copied_primers == 1
    assert first.foreign_key_violations == []
    assert first.orphan_primer_targets == []
    assert first.backup_path == backup.resolve()
    assert backup.exists()
    assert second.copied_sequences == 0
    assert second.skipped_sequences == 2
    assert second.copied_primers == 0
    assert second.skipped_primers == 1

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{destination}")
    factory = make_molbio_session_factory(engine)
    try:
        async with factory() as session:
            sequences = (await session.execute(select(NucleotideSequence).order_by(NucleotideSequence.id))).scalars().all()
            revisions = (await session.execute(select(MolecularRevision))).scalars().all()
            primer_revisions = (await session.execute(select(PrimerRevision))).scalars().all()
        assert [sequence.id for sequence in sequences] == ["seq-1", "seq-2"]
        assert len(revisions) == 2
        assert len(primer_revisions) == 1
    finally:
        await engine.dispose()

    with sqlite3.connect(source) as source_connection:
        assert source_connection.execute("SELECT COUNT(*) FROM nucleotide_sequences").fetchone()[0] == 2
        assert source_connection.execute("SELECT COUNT(*) FROM primers").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_legacy_extraction_fails_explicitly_on_destination_conflict(tmp_path: Path) -> None:
    from molbio_migrations import MigrationConflictError, extract_legacy_molbio_data

    source = tmp_path / "biomodstack.db"
    destination = tmp_path / "molbio.db"
    _create_legacy_source(source)
    await _extract_approved(source, destination, backup_path=tmp_path / "first-backup.db")

    with sqlite3.connect(destination) as connection:
        connection.execute("UPDATE nucleotide_sequences SET sequence = 'AAAA', length = 4 WHERE id = 'seq-1'")
        connection.commit()

    with pytest.raises(MigrationConflictError, match="seq-1"):
        await _extract_approved(source, destination, backup_path=tmp_path / "second-backup.db")


@pytest.mark.asyncio
async def test_legacy_extraction_verification_failure_rolls_back_all_copies(tmp_path: Path) -> None:
    """Post-copy verification is part of the write transaction, not an after-commit check."""
    from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
    from molbio_migrations import MigrationVerificationError, extract_legacy_molbio_data
    from molbio_models import NucleotideSequence
    from services.molbio_persistence import record_sequence_revision

    source = tmp_path / "biomodstack.db"
    destination = tmp_path / "molbio.db"
    _create_legacy_source(source)

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{destination}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    async with sessions() as session:
        unrelated = NucleotideSequence(
            id="destination-only",
            name="unrelated",
            sequence="AAAA",
            sequence_type="dna",
            molecule_strandedness="double",
            molecule_orientation="not_applicable",
            is_circular=False,
            length=4,
            features=[],
            primers=[],
            analysis_tracks=[],
            version=1,
            gc_content=0.0,
        )
        session.add(unrelated)
        await record_sequence_revision(session, unrelated, change_kind="create")
        await session.commit()
    await engine.dispose()

    with pytest.raises(MigrationVerificationError, match="count"):
        await _extract_approved(
            source,
            destination,
            backup_path=tmp_path / "verification-failure-backup.db",
        )

    with sqlite3.connect(destination) as connection:
        ids = {
            row[0]
            for row in connection.execute("SELECT id FROM nucleotide_sequences").fetchall()
        }
        assert ids == {"destination-only"}
        assert connection.execute(
            "SELECT COUNT(*) FROM molecular_revisions WHERE document_id IN ('seq-1', 'seq-2')"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["sequence_head", "primer_revision"])
async def test_idempotent_extraction_fails_closed_on_incomplete_history_graph(
    tmp_path: Path,
    corruption: str,
) -> None:
    from molbio_migrations import MigrationVerificationError, extract_legacy_molbio_data

    source = tmp_path / "biomodstack.db"
    destination = tmp_path / "molbio.db"
    _create_legacy_source(source)
    await _extract_approved(source, destination, backup_path=tmp_path / "first-backup.db")

    with sqlite3.connect(destination) as connection:
        if corruption == "sequence_head":
            connection.execute(
                "UPDATE molecular_documents SET current_revision_id = NULL WHERE id = 'seq-1'"
            )
        else:
            connection.execute("DROP TRIGGER molbio_immutable_primer_revisions_delete")
            connection.execute("DELETE FROM primer_revisions WHERE primer_id = 'primer-1'")
        connection.commit()

    with pytest.raises(MigrationVerificationError, match="history"):
        await _extract_approved(
            source,
            destination,
            backup_path=tmp_path / f"{corruption}-backup.db",
        )


@pytest.mark.asyncio
async def test_existing_database_migration_adds_restricting_sequence_parent_foreign_key(
    tmp_path: Path,
) -> None:
    """The parent FK migration rebuilds an old populated table without losing rows."""
    from molbio_database import create_molbio_engine, get_applied_molbio_migrations, init_molbio_db

    database = tmp_path / "existing.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    await init_molbio_db(engine=engine)
    await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.executemany(
            """
            INSERT INTO nucleotide_sequences (
                id, name, sequence, sequence_type, molecule_strandedness,
                molecule_orientation, is_circular, length, features, primers,
                analysis_tracks, parent_id, version, gc_content, created_at
            ) VALUES (?, ?, 'ATGC', 'dna', 'double', 'not_applicable', 0, 4,
                      '[]', '[]', '[]', ?, 1, 50.0, '2026-07-17 00:00:00')
            """,
            [("parent", "parent", None), ("child", "child", "parent")],
        )
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='nucleotide_sequences'"
        ).fetchone()[0]
        old_table_sql = table_sql.replace(
            "CREATE TABLE nucleotide_sequences",
            "CREATE TABLE nucleotide_sequences_oldstyle",
            1,
        )
        old_table_sql = re.sub(
            r",\s*FOREIGN KEY\(parent_id\) REFERENCES nucleotide_sequences \(id\) ON DELETE RESTRICT",
            "",
            old_table_sql,
        )
        connection.execute(old_table_sql)
        connection.execute("INSERT INTO nucleotide_sequences_oldstyle SELECT * FROM nucleotide_sequences")
        connection.execute("DROP TABLE nucleotide_sequences")
        connection.execute("ALTER TABLE nucleotide_sequences_oldstyle RENAME TO nucleotide_sequences")
        connection.execute(
            "DELETE FROM molbio_schema_migrations WHERE version = '0004_sequence_parent_foreign_key'"
        )
        connection.commit()

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await init_molbio_db(engine=engine)
        assert (await get_applied_molbio_migrations(engine=engine))[-1] == (
            "0006_project_plasmid_metadata"
        )
    finally:
        await engine.dispose()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        parent_fks = [
            row
            for row in connection.execute("PRAGMA foreign_key_list(nucleotide_sequences)").fetchall()
            if row[2] == "nucleotide_sequences" and row[3] == "parent_id" and row[4] == "id"
        ]
        assert len(parent_fks) == 1
        assert parent_fks[0][6].upper() == "RESTRICT"
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM nucleotide_sequences WHERE id = 'parent'")
        assert connection.execute(
            "SELECT parent_id FROM nucleotide_sequences WHERE id = 'child'"
        ).fetchone() == ("parent",)


@pytest.mark.asyncio
async def test_health_rejects_counterfeit_immutable_trigger_with_matching_prefix_count(
    tmp_path: Path,
) -> None:
    from molbio_database import create_molbio_engine, init_molbio_db, molbio_health

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'trigger-health.db'}")
    try:
        await init_molbio_db(engine=engine)
        async with engine.begin() as connection:
            await connection.execute(text("DROP TRIGGER molbio_immutable_primer_revisions_update"))
            await connection.execute(
                text(
                    """
                    CREATE TRIGGER molbio_immutable_counterfeit_update
                    BEFORE UPDATE ON primers
                    BEGIN
                        SELECT RAISE(ABORT, 'counterfeit');
                    END
                    """
                )
            )

        health = await molbio_health(engine=engine)
        assert health["immutable_trigger_count"] == 22
        assert health["immutable_triggers_current"] is False
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_health_rejects_same_name_noop_immutable_trigger(tmp_path: Path) -> None:
    from molbio_database import create_molbio_engine, init_molbio_db, molbio_health

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'noop-trigger.db'}")
    try:
        await init_molbio_db(engine=engine)
        async with engine.begin() as connection:
            await connection.execute(text("DROP TRIGGER molbio_immutable_primer_revisions_update"))
            await connection.execute(
                text(
                    """
                    CREATE TRIGGER molbio_immutable_primer_revisions_update
                    BEFORE UPDATE ON primer_revisions
                    BEGIN
                        SELECT 1;
                    END
                    """
                )
            )

        health = await molbio_health(engine=engine)
        assert health["immutable_trigger_count"] == 22
        assert health["immutable_triggers_current"] is False
        assert health["status"] == "degraded"
    finally:
        await engine.dispose()

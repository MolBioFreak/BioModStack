from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from Bio.Seq import Seq
from fastapi import HTTPException
from sqlalchemy import select, text

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory  # noqa: E402
from molbio_migrations import extract_legacy_molbio_data  # noqa: E402
from molbio_models import (  # noqa: E402
    MolecularOperation,
    MolecularRevision,
    NucleotideSequence,
    Primer,
    PrimerRevision,
    TmModelRevision,
)
from routers.molbio_ops import (  # noqa: E402
    PCRRequest,
    PrimerCreate,
    PrimerTmSettings,
    create_primer,
    delete_primer,
    get_primer,
    list_primers,
    pcr,
)
from services.molbio_persistence import record_sequence_revision  # noqa: E402
from services.sqlite_backup import backup_sqlite_database  # noqa: E402


def _legacy_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE nucleotide_sequences (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                sequence TEXT NOT NULL, sequence_type TEXT NOT NULL,
                molecule_strandedness TEXT NOT NULL DEFAULT 'unknown',
                molecule_orientation TEXT NOT NULL DEFAULT 'unknown',
                is_circular INTEGER, length INTEGER NOT NULL, features JSON,
                primers JSON, analysis_tracks JSON, organism TEXT, accession TEXT,
                source_file TEXT, parent_id TEXT, operation TEXT, operation_params JSON,
                version INTEGER NOT NULL DEFAULT 1, gc_content REAL,
                created_at DATETIME, updated_at DATETIME
            );
            CREATE TABLE primers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, sequence TEXT NOT NULL,
                sequence_type TEXT NOT NULL DEFAULT 'dna', length INTEGER NOT NULL,
                tm REAL, gc_percent REAL, tm_algorithm TEXT, tm_salt_correction TEXT,
                tm_settings JSON, primer_type TEXT, description TEXT,
                target_sequence_id TEXT, binding_start INTEGER, binding_end INTEGER,
                binding_strand INTEGER, tags JSON, is_favorite INTEGER,
                created_at DATETIME, updated_at DATETIME
            );
            INSERT INTO nucleotide_sequences VALUES
                ('seq-1','Template',NULL,'ATGCGCAT','dna','double','both',0,8,
                 '[]','[]','[]',NULL,NULL,NULL,NULL,NULL,NULL,1,50.0,NULL,NULL);
            """
        )


async def _seed_template(session, template_id: str = "template-1") -> NucleotideSequence:
    sequence = "ATGCGTACGTTAGCTAGCTAGGCTAACCGGTTACGATCGATCGTACGTTAGC"
    template = NucleotideSequence(
        id=template_id,
        name="PCR template",
        sequence=sequence,
        sequence_type="dna",
        molecule_strandedness="double",
        molecule_orientation="both",
        is_circular=False,
        length=len(sequence),
        features=[],
        primers=[],
        analysis_tracks=[],
        version=1,
    )
    session.add(template)
    await record_sequence_revision(session, template, change_kind="create", provenance={"source": "test"})
    await session.commit()
    return template


@pytest.mark.asyncio
@pytest.mark.parametrize("alias_pair", ["source-destination", "source-backup", "destination-backup"])
async def test_extraction_rejects_pairwise_path_aliases_before_write(tmp_path: Path, alias_pair: str) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    backup = tmp_path / "backup.db"
    _legacy_db(source)
    if alias_pair == "source-destination":
        destination = source
    elif alias_pair == "source-backup":
        backup = source
    else:
        destination = backup
    source_before = source.read_bytes()
    with pytest.raises(ValueError, match="distinct|alias"):
        await extract_legacy_molbio_data(source, destination, backup_path=backup)
    assert source.read_bytes() == source_before
    if alias_pair == "destination-backup":
        assert not destination.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias_kind", ["hardlink", "symlink"])
async def test_extraction_rejects_inode_aliases(tmp_path: Path, alias_kind: str) -> None:
    source = tmp_path / "source.db"
    alias = tmp_path / "alias.db"
    _legacy_db(source)
    if alias_kind == "hardlink":
        os.link(source, alias)
    else:
        alias.symlink_to(source)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="distinct|alias"):
        await extract_legacy_molbio_data(source, alias, backup_path=tmp_path / "backup.db")
    assert source.read_bytes() == before


@pytest.mark.asyncio
async def test_pcr_replay_returns_stored_payload_and_rejects_changed_request(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            base = PCRRequest(
                sequence_id=template.id,
                primer_fwd=template.sequence[:12],
                primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                save=True,
                persist_experiment=True,
                idempotency_key="same-key",
                reaction_settings={"volume_uL": 25},
            )
            first = await pcr(base, session)
            same = await pcr(base, session)
            assert same.reused is True
            assert same.product == first.product
            assert same.sequence is not None and first.sequence is not None
            assert same.sequence.id == first.sequence.id

            mutations = [
                {"primer_fwd": template.sequence[1:13]},
                {"tm_settings": {"algorithm": "wallace"}},
                {"reaction_settings": {"volume_uL": 50}},
                {"save": False},
            ]
            for mutation in mutations:
                changed = base.model_copy(update=mutation)
                with pytest.raises(HTTPException) as error:
                    await pcr(changed, session)
                assert error.value.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_pcr_same_key_converges_on_one_stored_experiment(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as seed_session:
            template = await _seed_template(seed_session)
            request = PCRRequest(
                sequence_id=template.id,
                primer_fwd=template.sequence[:12],
                primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                save=True,
                persist_experiment=True,
                idempotency_key="concurrent-same-key",
                reaction_settings={"volume_uL": 25},
            )

        async def invoke():
            async with sessions() as session:
                return await pcr(request, session)

        first, second = await asyncio.gather(invoke(), invoke())
        assert {first.reused, second.reused} == {False, True}
        assert first.operation_id == second.operation_id
        assert first.experiment_id == second.experiment_id
        assert first.experiment_revision_id == second.experiment_revision_id
        assert first.product == second.product
        assert first.sequence is not None and second.sequence is not None
        assert first.sequence.id == second.sequence.id

        async with sessions() as audit_session:
            for table in (
                "molecular_operations",
                "pcr_experiments",
                "pcr_experiment_revisions",
            ):
                count = (
                    await audit_session.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    )
                ).scalar_one()
                assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pcr_tm_revision_matches_actual_requested_algorithm_and_settings(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            settings = PrimerTmSettings(
                algorithm="nn_santalucia_hicks_2004",
                salt_correction="owczarzy_2008",
                na_mM=75.0,
                mg_mM=2.0,
            )
            response = await pcr(
                PCRRequest(
                    sequence_id=template.id,
                    primer_fwd=template.sequence[:12],
                    primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                    save=False,
                    idempotency_key="tm-provenance",
                    tm_settings=settings.model_dump(),
                ),
                session,
            )
            pcr_revision = await session.execute(
                text("SELECT tm_model_revision_id FROM pcr_experiment_revisions WHERE id=:id"),
                {"id": response.experiment_revision_id},
            )
            tm_revision = await session.get(TmModelRevision, pcr_revision.scalar_one())
            assert tm_revision is not None
            assert tm_revision.source["algorithm"] == "nn_santalucia_hicks_2004"
            assert tm_revision.source["algorithm_definition"]["nn_table_name"] == "DNA_NN4"
            assert tm_revision.source["settings"]["na_mM"] == 75.0
            assert tm_revision.source["settings"]["mg_mM"] == 2.0
            assert "DNA_NN2" not in str(tm_revision.source)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_experiment_pcr_operation_id_is_operation_row(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            response = await pcr(
                PCRRequest(
                    sequence_id=template.id,
                    primer_fwd=template.sequence[:12],
                    primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                    save=True,
                    persist_experiment=False,
                ),
                session,
            )
            assert await session.get(MolecularOperation, response.operation_id) is not None
            assert await session.get(MolecularRevision, response.operation_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_primer_delete_is_soft_and_preserves_immutable_history(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            created = await create_primer(PrimerCreate(name="primer", sequence="ATGCGTACGTTAGCTA"), session)
            await delete_primer(created.id, session)
            primer = await session.get(Primer, created.id)
            assert primer is not None and primer.deleted_at is not None
            assert len((await session.execute(select(PrimerRevision).where(PrimerRevision.primer_id == created.id))).scalars().all()) == 2
            assert await list_primers(session=session) == []
            with pytest.raises(HTTPException) as error:
                await get_primer(created.id, session)
            assert error.value.status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_schema_initialization_is_serialized(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}"
    engines = [create_molbio_engine(url), create_molbio_engine(url)]
    try:
        await asyncio.gather(*(init_molbio_db(engine=engine) for engine in engines))
        async with engines[0].connect() as connection:
            assert (await connection.execute(text("SELECT COUNT(*) FROM molbio_schema_migrations"))).scalar_one() >= 2
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines))


@pytest.mark.asyncio
async def test_health_reports_missing_migrations_and_connection_errors(tmp_path: Path) -> None:
    from molbio_database import molbio_health

    stale = tmp_path / "stale.db"
    with sqlite3.connect(stale) as connection:
        connection.execute("CREATE TABLE molbio_schema_migrations (migration_id TEXT PRIMARY KEY, applied_at DATETIME)")
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{stale}")
    try:
        snapshot = await molbio_health(engine=engine)
        assert snapshot["status"] == "degraded"
        assert snapshot["migrations_current"] is False
    finally:
        await engine.dispose()

    missing_parent = tmp_path / "missing" / "db.sqlite"
    broken = create_molbio_engine(f"sqlite+aiosqlite:///{missing_parent}")
    try:
        snapshot = await molbio_health(engine=broken)
        assert snapshot["status"] == "degraded"
        assert "error" in snapshot and str(missing_parent) not in snapshot["error"]
    finally:
        await broken.dispose()


def test_backup_rejects_foreign_key_violations_and_removes_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
            INSERT INTO child VALUES (1, 99);
            """
        )
    with pytest.raises(RuntimeError, match="foreign key"):
        backup_sqlite_database(source, destination)
    assert not destination.exists()

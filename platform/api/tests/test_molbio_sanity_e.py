"""Lane E: real isolated SQLite regressions and deterministic work counters."""
import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory, molbio_health
from molbio_models import MolecularRevision, NucleotideSequence
from routers import nucleotide_sequences as routes
from services import molbio_sequence_import as imports
from services.molbio_persistence import record_sequence_revision


def request(key, source=">original\nACGT\n", topology="circular"):
    return imports.SequenceImportRequest(source_format="fasta", source_text=source,
        topology_default=topology, idempotency_key=key)


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    await init_molbio_db(engine=engine)
    try:
        yield engine, make_molbio_session_factory(engine)
    finally:
        await engine.dispose()


def test_text_record_and_feature_admission(monkeypatch):
    monkeypatch.setattr(imports, "MAX_IMPORT_RECORDS", 2)
    preview = imports.build_sequence_import_preview(request("caps", ">a\nA\n>b\nC\n>c\nG\n>d\nT\n"))
    assert preview["record_count"] == 2
    assert not preview["valid"]
    assert preview["errors"][0]["code"] == "too_many_records"
    monkeypatch.setattr(imports, "MAX_IMPORT_FEATURES_PER_RECORD", 0)
    with pytest.raises(imports.SequenceImportInputError, match="feature count"):
        imports._features_from_seqrecord(SimpleNamespace(features=[object()]), ordinal=1, sequence_length=4)


@pytest.mark.asyncio
async def test_reuse_immutable_bytes_after_head_edit_and_deletion(store):
    engine, sessions = store
    async with sessions() as session:
        first = (await imports.commit_sequence_import(session, request("first")))["records"][0]
        seq = await session.get(NucleotideSequence, first["sequence_id"])
        seq.sequence = "TTTT"
        seq.name = "changed"
        seq.is_circular = False
        await record_sequence_revision(session, seq, change_kind="update")
        await session.commit()
        for key in ("edited", "deleted"):
            if key == "deleted":
                await routes.delete_sequence(first["sequence_id"], session)
            reused = (await imports.commit_sequence_import(session, request(key, topology="linear")))["records"][0]
            assert reused["revision_id"] == first["revision_id"]
            assert reused["sequence"] == "ACGT"
            assert reused["name"] == "original"
            assert reused["topology"] == "circular"
            assert hashlib.sha256(reused["sequence"].encode()).hexdigest() == reused["content_sha256"]


@pytest.mark.asyncio
async def test_parse_once_off_thread_without_writer_and_single_digest_lookup(store, monkeypatch):
    engine, sessions = store
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda c, cur, sql, params, ctx, many: statements.append(sql))
    original = imports.build_sequence_import_preview
    calls = []
    main_thread = threading.get_ident()
    def preview(*args, **kwargs):
        assert threading.get_ident() != main_thread
        assert not any("BEGIN IMMEDIATE" in sql.upper() for sql in statements)
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(imports, "build_sequence_import_preview", preview)
    payload = request("batch", ">a\nA\n>b\nC\n>duplicate\nA\n")
    async with sessions() as session:
        result = await imports.commit_sequence_import(session, payload)
        assert len(calls) == 1
        assert result["records"][2]["revision_id"] == result["records"][0]["revision_id"]
        assert await imports.commit_sequence_import(session, payload) == result
        assert len(calls) == 1
    lookups = [sql for sql in statements if "row_number() OVER" in sql]
    assert len(lookups) == 1
    async with engine.connect() as conn:
        plan = (await conn.execute(text("EXPLAIN QUERY PLAN SELECT id FROM molecular_revisions WHERE content_sha256='x' ORDER BY created_at, id LIMIT 1"))).all()
        assert any("ix_molecular_revisions_digest_created" in str(row) for row in plan)


@pytest.mark.asyncio
async def test_equal_put_no_history_and_metadata_skips_scientific_validation(store, monkeypatch):
    _, sessions = store
    async with sessions() as session:
        created = await routes.create_sequence(routes.NucleotideSequenceCreate(name="same", sequence="ACGT"), session)
        sequence_id = created.id
        await routes.update_sequence(sequence_id, routes.NucleotideSequenceUpdate(name="same", sequence="ac gt"), session)
        assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 1
        await session.rollback()
        def forbidden(*args, **kwargs):
            raise AssertionError("metadata-only update revalidated scientific payload")
        monkeypatch.setattr(routes, "clean_sequence", forbidden)
        feature_calls = []
        original_features = routes.normalize_feature_payloads
        def features(*args, **kwargs):
            feature_calls.append(1)
            return original_features(*args, **kwargs)
        monkeypatch.setattr(routes, "normalize_feature_payloads", features)
        monkeypatch.setattr(routes, "normalize_primer_payloads", forbidden)
        updated = await routes.update_sequence(sequence_id, routes.NucleotideSequenceUpdate(description="metadata"), session)
        assert updated.version == 2
        # Historical response adapter remains; redundant writer-side pass is gone.
        assert len(feature_calls) == 1
        assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 2


@pytest.mark.asyncio
async def test_summary_sql_projection_paging_and_history_offset(store):
    engine, sessions = store
    async with sessions() as session:
        created = []
        for _ in range(4):
            created.append(await routes.create_sequence(routes.NucleotideSequenceCreate(name="same", sequence="ACGT"), session))
        statements = []
        event.listen(engine.sync_engine, "before_cursor_execute", lambda c, cur, sql, params, ctx, many: statements.append(sql))
        page = await routes.list_sequences(session=session, search=None, sequence_type=None, topology="all", sort_by="name", sort_desc=False, limit=2, offset=1)
        assert [item.id for item in page] == sorted(item.id for item in created)[1:3]
        sql = statements[0]
        assert "LIMIT" in sql and "OFFSET" in sql
        assert "nucleotide_sequences.sequence," not in sql
        assert "nucleotide_sequences.analysis_tracks" not in sql
        sequence_id = created[0].id
        for name in ("revision2", "revision3"):
            await routes.update_sequence(sequence_id, routes.NucleotideSequenceUpdate(name=name), session)
        history = await routes.list_molecular_revisions(sequence_id, session, limit=1, offset=2)
        assert len(history) == 1
        assert history[0].revision_number == 1


@pytest.mark.asyncio
async def test_light_health_has_no_scientific_scans_and_deep_still_checks(store):
    engine, _ = store
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda c, cur, sql, params, ctx, many: statements.append(sql))
    light = await molbio_health(engine=engine, deep=False)
    assert light["status"] == "healthy"
    assert light["quick_check"] == "not_run"
    assert light["foreign_key_violations"] is None
    assert light["sequence_parent_cycle_count"] is None
    assert light["database_schema_current"] is None
    assert not any("quick_check" in sql or "PRAGMA foreign_key_check" in sql or "SELECT id, parent_id" in sql for sql in statements)
    assert len(statements) == 4
    deep = await molbio_health(engine=engine)
    assert deep["status"] == "healthy"
    assert deep["quick_check"] == "ok"
    assert deep["integrity_checks_run"] is True


@pytest.mark.asyncio
async def test_concurrent_same_key_claim_rechecked_after_parse(store):
    _, sessions = store
    async def commit():
        async with sessions() as session:
            return await imports.commit_sequence_import(session, request("race"))
    first, second = await asyncio.gather(commit(), commit())
    assert first == second
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 1


@pytest.mark.asyncio
async def test_forward_digest_index_preserves_historical_bytes(store):
    engine, sessions = store
    async with sessions() as session:
        first = await imports.commit_sequence_import(session, request("preserved"))
    async with engine.begin() as connection:
        before = (await connection.execute(text("SELECT id, snapshot, content_sha256 FROM molecular_revisions ORDER BY id"))).all()
        await connection.execute(text("DROP INDEX ix_molecular_revisions_digest_created"))
        await connection.execute(text("DELETE FROM molbio_schema_migrations WHERE version='0008_revision_digest_index'"))
    await init_molbio_db(engine=engine)
    async with engine.connect() as connection:
        after = (await connection.execute(text("SELECT id, snapshot, content_sha256 FROM molecular_revisions ORDER BY id"))).all()
        assert after == before
    async with sessions() as session:
        assert await imports.commit_sequence_import(session, request("preserved")) == first


@pytest.mark.asyncio
async def test_dna_import_does_not_reuse_rna_digest(store):
    _, sessions = store
    async with sessions() as session:
        rna = await routes.create_sequence(routes.NucleotideSequenceCreate(name="rna", sequence="ACG", sequence_type="rna"), session)
        dna = (await imports.commit_sequence_import(session, request("dna", ">dna\nACG\n")))["records"][0]
        assert dna["sequence_type"] == "dna"
        assert dna["sequence_id"] != rna.id


def test_genbank_feature_cap_applies_to_text_parser(monkeypatch):
    from io import StringIO
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio.SeqFeature import SeqFeature, SimpleLocation
    record = SeqRecord(Seq("ACGT"), id="features", annotations={"molecule_type": "DNA"})
    record.features = [SeqFeature(SimpleLocation(0, 1), type="misc_feature") for _ in range(3)]
    source = StringIO()
    SeqIO.write([record], source, "genbank")
    monkeypatch.setattr(imports, "MAX_IMPORT_FEATURES_PER_RECORD", 2)
    preview = imports.build_sequence_import_preview(imports.SequenceImportRequest(
        source_format="genbank", source_text=source.getvalue(), topology_default="linear",
    ))
    assert not preview["valid"]
    assert preview["errors"][0]["code"] == "too_many_features"

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from Bio.Seq import Seq
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select, text

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory  # noqa: E402
from molbio_migrations import (  # noqa: E402
    extract_legacy_molbio_data as _extract_legacy_molbio_data,
    legacy_molbio_manifest_sha256,
)
from molbio_models import (  # noqa: E402
    MolBioAuditEvent,
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularRevision,
    NucleotideSequence,
    PCRExperiment,
    PCRExperimentRevision,
    Primer,
    PrimerRevision,
    TmModelRevision,
)
from routers.molbio_ops import (  # noqa: E402
    AlignmentSettingsSchema,
    AssemblyFragmentEndSchema,
    AssemblyFragmentSchema,

    LigationAssemblyRequest,
    MutationSchema,
    MutagenesisRequest,
    PCRRequest,
    PCRReviewStateRequest,
    PrimerCreate,
    PrimerUpdate,
    SequenceAlignmentRequest,
    PrimerTmSettings,
    create_primer,
    align_molecular_sequences,
    delete_primer,

    get_primer,
    list_primers,
    mutagenesis,
    pcr,
    save_ligation_assembly,
    update_pcr_experiment_review_state,
    update_primer,
)
from routers.nucleotide_sequences import (  # noqa: E402
    AnalysisTrackSchema,
    NucleotideSequenceUpdate,
    delete_sequence,
    normalize_feature_payloads,
    update_sequence,
)
from services.assembly.golden_gate import resolve_golden_gate_enzyme, simulate_golden_gate  # noqa: E402
from services.assembly.types import AssemblyFragment, FragmentEnd  # noqa: E402
from services.restriction_catalog import catalog_authority  # noqa: E402
from services.molbio_ops import pcr_product  # noqa: E402
from services.molbio_persistence import record_sequence_revision  # noqa: E402
from services.primer_qc import evaluate_primer_pair_qc  # noqa: E402
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


async def _extract_approved(source: Path, destination: Path, **kwargs):
    kwargs.setdefault("expected_manifest_sha256", legacy_molbio_manifest_sha256(source))
    return await _extract_legacy_molbio_data(source, destination, **kwargs)


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


def test_feature_normalization_preserves_compound_traversal_segment_order() -> None:
    segments = [
        {"start": 12, "end": 16},
        {"start": 0, "end": 4},
    ]

    normalized = normalize_feature_payloads(
        [
            {
                "name": "origin-spanning feature",
                "type": "misc_feature",
                "segments": segments,
            }
        ],
        sequence_length=16,
    )

    assert normalized[0]["segments"] == segments
    assert normalized[0]["start"] == 0
    assert normalized[0]["end"] == 16


@pytest.mark.asyncio
async def test_sequence_update_recanonicalizes_type_and_clears_explicit_nulls(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'sequence-update.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            template.description = "clear me"
            template.organism = "synthetic"
            await session.commit()

            updated = await update_sequence(
                template.id,
                NucleotideSequenceUpdate(
                    sequence_type="rna",
                    description=None,
                    organism=None,
                ),
                session,
            )
            assert updated.sequence_type == "rna"
            assert "T" not in updated.sequence and "U" in updated.sequence
            assert updated.description is None
            assert updated.organism is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_primer_create_update_validate_sequence_geometry_and_explicit_nulls(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'primer-validation.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            for payload in (
                PrimerCreate(name="empty", sequence=""),
                PrimerCreate(name="invalid", sequence="!!!!"),
                PrimerCreate(
                    name="geometry",
                    sequence="ATGCGTAC",
                    target_sequence_id=template.id,
                    binding_start=-99,
                    binding_end=999,
                    binding_strand=7,
                ),
            ):
                with pytest.raises(HTTPException) as error:
                    await create_primer(payload, session)
                assert error.value.status_code == 400

            created = await create_primer(
                PrimerCreate(
                    name="valid",
                    sequence="ATGCGTAC",
                    description="clear me",
                    target_sequence_id=template.id,
                    binding_start=0,
                    binding_end=8,
                    binding_strand=1,
                ),
                session,
            )
            with pytest.raises(HTTPException) as error:
                await update_primer(created.id, PrimerUpdate(sequence="!!!!"), session)
            assert error.value.status_code == 400

            cleared = await update_primer(
                created.id,
                PrimerUpdate(
                    description=None,
                    target_sequence_id=None,
                    binding_start=None,
                    binding_end=None,
                ),
                session,
            )
            assert cleared.description is None
            assert cleared.target_sequence_id is None
            assert cleared.binding_start is None
            assert cleared.binding_end is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sequence_delete_clears_soft_deleted_primer_reference(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'soft-delete-reference.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            primer = await create_primer(
                PrimerCreate(
                    name="dependent",
                    sequence="ATGCGTAC",
                    target_sequence_id=template.id,
                    binding_start=0,
                    binding_end=8,
                ),
                session,
            )
            await delete_primer(primer.id, session)
            result = await delete_sequence(template.id, session)
            assert result == {"status": "deleted", "id": template.id}
            persisted_primer = await session.get(Primer, primer.id)
            assert persisted_primer is not None
            assert persisted_primer.target_sequence_id is None
            assert persisted_primer.binding_start is None
            assert persisted_primer.binding_end is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mutagenesis_rejects_non_residue_and_preserves_molecular_metadata(
    tmp_path: Path,
) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'mutagenesis.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            template.molecule_strandedness = "double"
            template.molecule_orientation = "both"
            await session.commit()

            with pytest.raises(HTTPException) as error:
                await mutagenesis(
                    MutagenesisRequest(
                        sequence_id=template.id,
                        mutations=[MutationSchema(pos=2, **{"from": "T"}, to="XYZ")],
                    ),
                    session,
                )
            assert error.value.status_code == 400

            result = await mutagenesis(
                MutagenesisRequest(
                    sequence_id=template.id,
                    mutations=[MutationSchema(pos=2, **{"from": "T"}, to="C")],
                ),
                session,
            )
            assert result.sequence is not None
            persisted = await session.get(NucleotideSequence, result.sequence.id)
            assert persisted is not None
            assert persisted.molecule_strandedness == "double"
            assert persisted.molecule_orientation == "both"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pcr_rejects_invalid_primers_before_snapshot_persistence(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'pcr-invalid.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            reverse = str(Seq(template.sequence[-12:]).reverse_complement())
            with pytest.raises(HTTPException) as error:
                await pcr(
                    PCRRequest(
                        sequence_id=template.id,
                        primer_fwd="!!!" + template.sequence[:12],
                        primer_rev=reverse,
                        save=False,
                        persist_experiment=True,
                    ),
                    session,
                )
            assert error.value.status_code == 400
            assert await session.scalar(select(func.count()).select_from(PCRExperiment)) == 0
    finally:
        await engine.dispose()


def test_pcr_rejects_ambiguous_binding_site_pairs() -> None:
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        pcr_product("A" * 20, "A" * 8, "T" * 8, circular=False)


def test_scientific_numeric_schemas_reject_nonfinite_and_impossible_values() -> None:
    invalid_factories = (
        lambda: PrimerTmSettings(na_mM=-50),
        lambda: PrimerTmSettings(dmso_percent=float("nan")),
        lambda: AlignmentSettingsSchema(match_score=float("nan")),
        lambda: AnalysisTrackSchema(
            id="track",
            name="track",
            values=[0.0, float("nan")],
        ),
    )
    for factory in invalid_factories:
        with pytest.raises(ValidationError):
            factory()


@pytest.mark.asyncio
async def test_alignment_route_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time
    import routers.molbio_ops as molbio_router

    original = molbio_router.align_sequences

    def slow_alignment(*args, **kwargs):
        time.sleep(0.1)
        return original(*args, **kwargs)

    monkeypatch.setattr(molbio_router, "align_sequences", slow_alignment)
    task = asyncio.create_task(
        align_molecular_sequences(
            SequenceAlignmentRequest(
                reference_name="reference",
                reference_sequence="ACGTACGT",
                query_name="query",
                query_sequence="ACGTACGT",
                settings=AlignmentSettingsSchema(),
            )
        )
    )
    await asyncio.sleep(0.02)
    assert task.done() is False
    response = await task
    assert response.identity_pct == 100.0


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
        await _extract_approved(source, destination, backup_path=backup)
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
        await _extract_approved(source, alias, backup_path=tmp_path / "backup.db")
    assert source.read_bytes() == before


@pytest.mark.asyncio
async def test_extraction_rejects_same_inventory_content_substitution(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _legacy_db(source)
    approved_manifest = legacy_molbio_manifest_sha256(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE nucleotide_sequences SET sequence = 'TTTTAAAA' WHERE id = 'seq-1'"
        )
        connection.commit()

    with pytest.raises(Exception, match="manifest changed"):
        await _extract_legacy_molbio_data(
            source,
            tmp_path / "destination.db",
            backup_path=tmp_path / "backup.db",
            expected_manifest_sha256=approved_manifest,
            expected_sequence_count=1,
            expected_total_bases=8,
            expected_primer_count=0,
        )


@pytest.mark.asyncio
async def test_extraction_is_idempotent_after_nullable_legacy_default_normalization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _legacy_db(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            INSERT INTO primers (
                id, name, sequence, sequence_type, length, primer_type,
                target_sequence_id, binding_start, binding_end, binding_strand,
                is_favorite, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "primer-1",
                "nullable default",
                "ATGCGTAC",
                "dna",
                8,
                None,
                "seq-1",
                0,
                8,
                1,
                0,
                "2026-01-01T00:00:00",
            ),
        )
        connection.commit()

    first = await _extract_approved(
        source,
        destination,
        backup_path=tmp_path / "first-backup.db",
    )
    second = await _extract_approved(
        source,
        destination,
        backup_path=tmp_path / "second-backup.db",
    )
    assert first.copied_primers == 1
    assert second.copied_primers == 0
    assert second.skipped_primers == 1


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


@pytest.mark.asyncio
async def test_concurrent_sequence_mutations_serialize_revision_allocation(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'sequence-concurrency.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as seed_session:
            template = await _seed_template(seed_session)
            template_id = template.id

        async def mutate(index: int):
            async with sessions() as session:
                return await update_sequence(
                    template_id,
                    NucleotideSequenceUpdate(description=f"concurrent-{index}"),
                    session,
                )

        await asyncio.gather(*(mutate(index) for index in range(4)))

        async with sessions() as audit_session:
            sequence = await audit_session.get(NucleotideSequence, template_id)
            revisions = (
                await audit_session.execute(
                    select(MolecularRevision)
                    .where(MolecularRevision.document_id == template_id)
                    .order_by(MolecularRevision.revision_number)
                )
            ).scalars().all()
            assert sequence is not None and sequence.version == 5
            assert [revision.revision_number for revision in revisions] == [1, 2, 3, 4, 5]
            assert revisions[-1].snapshot["version"] == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_primer_mutations_serialize_revision_allocation(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'primer-concurrency.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as seed_session:
            created = await create_primer(
                PrimerCreate(name="primer", sequence="ATGCGTACGTTAGCTA"),
                seed_session,
            )
            primer_id = created.id

        async def mutate(index: int):
            async with sessions() as session:
                return await update_primer(
                    primer_id,
                    PrimerUpdate(description=f"concurrent-{index}"),
                    session,
                )

        await asyncio.gather(*(mutate(index) for index in range(4)))

        async with sessions() as audit_session:
            revisions = (
                await audit_session.execute(
                    select(PrimerRevision)
                    .where(PrimerRevision.primer_id == primer_id)
                    .order_by(PrimerRevision.revision_number)
                )
            ).scalars().all()
            assert [revision.revision_number for revision in revisions] == [1, 2, 3, 4, 5]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_pcr_review_mutations_serialize_revision_allocation(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-concurrency.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as seed_session:
            template = await _seed_template(seed_session)
            created = await pcr(
                PCRRequest(
                    sequence_id=template.id,
                    primer_fwd=template.sequence[:12],
                    primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                    save=False,
                    persist_experiment=True,
                    idempotency_key="review-concurrency",
                ),
                seed_session,
            )
            experiment_id = created.experiment_id

        async def review(index: int):
            async with sessions() as session:
                return await update_pcr_experiment_review_state(
                    experiment_id,
                    PCRReviewStateRequest(
                        review_state="in_review" if index % 2 == 0 else "draft",
                        notes=f"concurrent-{index}",
                    ),
                    session,
                )

        await asyncio.gather(*(review(index) for index in range(4)))

        async with sessions() as audit_session:
            revisions = (
                await audit_session.execute(
                    select(PCRExperimentRevision)
                    .where(PCRExperimentRevision.experiment_id == experiment_id)
                    .order_by(PCRExperimentRevision.revision_number)
                )
            ).scalars().all()
            assert [revision.revision_number for revision in revisions] == [1, 2, 3, 4, 5]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_persists_server_owned_revision_and_audit_actor(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit-actor.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            created = await pcr(
                PCRRequest(
                    sequence_id=template.id,
                    primer_fwd=template.sequence[:12],
                    primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                    save=False,
                    persist_experiment=True,
                    idempotency_key="audit-actor",
                ),
                session,
            )
            response = await update_pcr_experiment_review_state(
                created.experiment_id,
                PCRReviewStateRequest(
                    review_state="in_review",
                    provenance={"client": "test"},
                ),
                session,
            )
            revision = await session.get(PCRExperimentRevision, response["id"])
            event = (
                await session.execute(
                    select(MolBioAuditEvent)
                    .where(MolBioAuditEvent.event_kind == "pcr.review_state_changed")
                    .order_by(MolBioAuditEvent.occurred_at.desc())
                )
            ).scalars().first()

            assert revision is not None
            assert revision.created_by == "system:molbio-api"
            assert event is not None and event.actor == "system:molbio-api"
            assert revision.provenance["client"] == "test"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_requires_authenticated_reviewer_and_persists_server_actor(
    tmp_path: Path,
) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'approval-actor.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            template = await _seed_template(session)
            created = await pcr(
                PCRRequest(
                    sequence_id=template.id,
                    primer_fwd=template.sequence[:12],
                    primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
                    save=False,
                    persist_experiment=True,
                    idempotency_key="approval-actor",
                ),
                session,
            )
            assert created.experiment_id is not None
            experiment_id = created.experiment_id
            request = PCRReviewStateRequest(
                review_state="approved",
            )
            with pytest.raises(HTTPException) as error:
                await update_pcr_experiment_review_state(
                    experiment_id,
                    request,
                    session,
                )
            assert error.value.status_code == 403

            response = await update_pcr_experiment_review_state(
                experiment_id,
                request,
                session,
                "reviewer:alice",
            )
            revision = await session.get(PCRExperimentRevision, response["id"])
            assert revision is not None
            assert revision.review_state == "approved"
            assert revision.created_by == "system:molbio-api"
    finally:
        await engine.dispose()


def test_three_prime_heterodimer_anchors_both_physical_three_prime_ends() -> None:
    metrics = evaluate_primer_pair_qc(
        "AAAAAAAAAAAACCCC",
        "GGGGTTTTTTTTTTTT",
    )
    assert metrics.heterodimer_complement == 16
    assert metrics.three_prime_heterodimer == 0


def test_assembly_schemas_and_golden_gate_reject_unsupported_or_ambiguous_ends() -> None:
    catalog = catalog_authority.require()
    enzyme = resolve_golden_gate_enzyme(
        enzyme_id="BsaI",
        catalog_id=catalog.catalog_id,
        expected_catalog_sha256=catalog.content_sha256,
    )
    with pytest.raises(ValidationError):
        AssemblyFragmentEndSchema(type="sticky", overhang="AAAA")

    fragments = [
        AssemblyFragment(
            id="one",
            name="one",
            sequence="GGTCTCAAAAGGTCTC",
            left_end=FragmentEnd(type="sticky_5", overhang="AAAA"),
            right_end=FragmentEnd(type="sticky_5", overhang="CCCC"),
        ),
        AssemblyFragment(
            id="two",
            name="two",
            sequence="TTTTGGGG",
            left_end=FragmentEnd(type="sticky_5", overhang="GGGG"),
            right_end=FragmentEnd(type="sticky_5", overhang="TTTT"),
        ),
    ]
    with pytest.raises(ValueError, match="recognition site"):
        simulate_golden_gate(fragments, enzyme=enzyme, circular=False)

    reverse_site_fragments = [
        AssemblyFragment(
            id="reverse-one",
            name="reverse-one",
            sequence="AAAAGAGACCTTTT",
            left_end=FragmentEnd(type="sticky_5", overhang="AAAA"),
            right_end=FragmentEnd(type="sticky_5", overhang="CCCC"),
        ),
        fragments[1],
    ]
    with pytest.raises(ValueError, match="recognition site"):
        simulate_golden_gate(reverse_site_fragments, enzyme=enzyme, circular=False)

    junction_fragments = [
        AssemblyFragment(
            id="junction-one",
            name="junction-one",
            sequence="AAAAGAGA",
            left_end=FragmentEnd(type="sticky_5", overhang="AAAA"),
            right_end=FragmentEnd(type="sticky_5", overhang="CCCC"),
        ),
        AssemblyFragment(
            id="junction-two",
            name="junction-two",
            sequence="CCTTTT",
            left_end=FragmentEnd(type="sticky_5", overhang="GGGG"),
            right_end=FragmentEnd(type="sticky_5", overhang="TTTT"),
        ),
    ]
    product = simulate_golden_gate(junction_fragments, enzyme=enzyme, circular=False)
    assert "GAGACC" in product.sequence
    assert any("either orientation" in warning for warning in product.warnings)
    assert any("catalog enzyme BsaI" in note for note in product.validation_notes)


@pytest.mark.asyncio
async def test_saved_assembly_rejects_forged_source_slice_before_persistence(
    tmp_path: Path,
) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'forged-lineage.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            source = await _seed_template(session, "forged-source")
            blunt = AssemblyFragmentEndSchema(type="blunt")
            request = LigationAssemblyRequest(
                fragments=[
                    AssemblyFragmentSchema(
                        id="forged",
                        name="forged",
                        sequence="TTTT",
                        source_sequence_id=source.id,
                        source_start=0,
                        source_end=4,
                        left_end=blunt,
                        right_end=blunt,
                    ),
                    AssemblyFragmentSchema(
                        id="inline",
                        name="inline",
                        sequence="AAAA",
                        left_end=blunt,
                        right_end=blunt,
                    ),
                ],
                circular=False,
            )
            with pytest.raises(HTTPException) as error:
                await save_ligation_assembly(request, session)
            assert error.value.status_code == 409
            count = await session.scalar(
                select(func.count()).select_from(MolecularOperation).where(
                    MolecularOperation.operation_kind == "ligation"
                )
            )
            assert count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_assembly_records_every_ordered_immutable_source_revision(tmp_path: Path) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'assembly-lineage.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            first = await _seed_template(session, "assembly-source-1")
            second = await _seed_template(session, "assembly-source-2")
            source_revisions = {
                first.id: await session.scalar(
                    select(MolecularRevision).where(MolecularRevision.document_id == first.id)
                ),
                second.id: await session.scalar(
                    select(MolecularRevision).where(MolecularRevision.document_id == second.id)
                ),
            }
            blunt = AssemblyFragmentEndSchema(type="blunt")
            await save_ligation_assembly(
                LigationAssemblyRequest(
                    fragments=[
                        AssemblyFragmentSchema(
                            id="fragment-1",
                            name="first",
                            sequence=first.sequence,
                            source_sequence_id=first.id,
                            source_start=0,
                            source_end=len(first.sequence),
                            left_end=blunt,
                            right_end=blunt,
                        ),
                        AssemblyFragmentSchema(
                            id="fragment-2",
                            name="second",
                            sequence=second.sequence,
                            source_sequence_id=second.id,
                            source_start=0,
                            source_end=len(second.sequence),
                            left_end=blunt,
                            right_end=blunt,
                        ),
                    ],
                    circular=False,
                    new_name="two-input-product",
                ),
                session,
            )
            operation = await session.scalar(
                select(MolecularOperation).where(MolecularOperation.operation_kind == "ligation")
            )
            edges = (
                await session.execute(
                    select(MolecularOperationInput)
                    .where(MolecularOperationInput.operation_id == operation.id)
                    .order_by(MolecularOperationInput.position)
                )
            ).scalars().all()

            assert [edge.position for edge in edges] == [0, 1]
            assert [edge.revision_id for edge in edges] == [
                source_revisions[first.id].id,
                source_revisions[second.id].id,
            ]
            assert [edge.snapshot["fragment"]["id"] for edge in edges] == [
                "fragment-1",
                "fragment-2",
            ]
            assert [edge.snapshot["revision_sha256"] for edge in edges] == [
                source_revisions[first.id].content_sha256,
                source_revisions[second.id].content_sha256,
            ]
    finally:
        await engine.dispose()

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from database import Base  # noqa: E402
from molbio_database import (  # noqa: E402
    create_molbio_engine,
    init_molbio_db,
    make_molbio_session_factory,
)
from molbio_models import (  # noqa: E402
    MolecularImportBatch,
    MolecularRevision,
    NucleotideSequence,
)
from routers.molbio_ops import (  # noqa: E402
    NgsReceiptRequest,
    get_sequence_revision,
    issue_sequence_ngs_receipt,
    list_sequence_revisions,
)
from services.molbio_ngs_receipts import sha256_text  # noqa: E402
from services.molbio_persistence import (  # noqa: E402
    IdempotencyConflictError,
    record_sequence_revision,
)
from services.molbio_sequence_import import (  # noqa: E402
    SequenceImportInputError,
    SequenceImportRequest,
    build_sequence_import_preview,
    commit_sequence_import,
)


GENBANK_SOURCE = """LOCUS       RECONE                 12 bp    DNA     linear   SYN 01-JAN-2000
DEFINITION  first construct.
ACCESSION   TEST001
FEATURES             Location/Qualifiers
     source          1..12
                     /organism="synthetic"
     CDS             complement(join(1..3,7..9))
                     /gene="first"
ORIGIN
        1 atgcatgcatgc
//
LOCUS       RECTWO                  6 bp    DNA     linear   SYN 01-JAN-2000
DEFINITION  second construct.
FEATURES             Location/Qualifiers
     misc_feature    2..5
                     /label="second"
ORIGIN
        1 aaaaaa
//
"""


async def _molbio_session(tmp_path: Path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    return engine, make_molbio_session_factory(engine)


@pytest.mark.asyncio
async def test_preview_is_server_authoritative_for_multi_record_formats_and_duplicates(
    tmp_path: Path,
) -> None:
    request = SequenceImportRequest(
        source_format="fasta",
        source_text=">first description\nacgt\n>duplicate\nACGT\n",
        topology_default="linear",
        topology_overrides={2: "circular"},
    )

    preview = build_sequence_import_preview(request)

    assert preview["valid"] is True
    assert preview["source_digest"] == hashlib.sha256(request.source_text.encode()).hexdigest()
    assert [record["record_ordinal"] for record in preview["records"]] == [1, 2]
    assert [record["topology"] for record in preview["records"]] == ["linear", "circular"]
    assert preview["records"][1]["exact_duplicate_of"] == 1
    assert preview["exact_duplicates"] == [
        {
            "record_ordinal": 2,
            "duplicate_of": 1,
            "canonical_digest": preview["records"][0]["canonical_digest"],
        }
    ]

    genbank = build_sequence_import_preview(
        SequenceImportRequest(
            source_format="genbank",
            source_text=GENBANK_SOURCE,
            topology_default="linear",
            topology_overrides={1: "circular"},
        )
    )
    assert genbank["valid"] is True
    assert [record["record_ordinal"] for record in genbank["records"]] == [1, 2]
    assert genbank["records"][0]["topology"] == "circular"
    assert genbank["records"][0]["features"][1]["segments"] == [
        {"start": 6, "end": 9},
        {"start": 0, "end": 3},
    ]
    assert genbank["records"][0]["features"][1]["qualifiers"]["gene"] == ["first"]


@pytest.mark.asyncio
async def test_invalid_batch_has_zero_writes_and_strict_raw_rows_preserve_features(
    tmp_path: Path,
) -> None:
    engine, sessions = await _molbio_session(tmp_path)
    try:
        invalid = SequenceImportRequest(
            source_format="raw_dna",
            raw_rows=[
                {"name": "valid", "sequence": "ATGC"},
                {"name": "invalid", "sequence": "ATG!"},
            ],
            topology_default="linear",
            idempotency_key="invalid-batch",
        )
        preview = build_sequence_import_preview(invalid)
        assert preview["valid"] is False
        assert preview["errors"][0]["record_ordinal"] == 2

        async with sessions() as session:
            with pytest.raises(SequenceImportInputError):
                await commit_sequence_import(session, invalid)
            assert await session.scalar(select(func.count()).select_from(NucleotideSequence)) == 0
            assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 0
            assert await session.scalar(select(func.count()).select_from(MolecularImportBatch)) == 0

        with pytest.raises(ValidationError):
            SequenceImportRequest(
                source_format="raw_dna",
                raw_rows=[{"name": "x", "sequence": "ATGC", "path": "/tmp/not-authority"}],
                topology_default="linear",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commit_is_atomic_and_idempotent_with_conflicts_failing_closed(tmp_path: Path) -> None:
    engine, sessions = await _molbio_session(tmp_path)
    try:
        request = SequenceImportRequest(
            source_format="raw_dna",
            raw_rows=[
                {
                    "name": "construct-a",
                    "sequence": "ATGC",
                    "features": [{"name": "gene", "type": "CDS", "start": 0, "end": 4}],
                },
                {"name": "construct-b", "sequence": "GGCC"},
            ],
            topology_default="circular",
            topology_overrides={2: "linear"},
            idempotency_key="same-import",
        )
        async with sessions() as session:
            first = await commit_sequence_import(session, request)
            retry = await commit_sequence_import(session, request)
            assert retry == first
            assert first["record_count"] == 2
            assert [record["topology"] for record in first["records"]] == ["circular", "linear"]
            assert await session.scalar(select(func.count()).select_from(NucleotideSequence)) == 2
            assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 2
            assert await session.scalar(select(func.count()).select_from(MolecularImportBatch)) == 1

            conflict = SequenceImportRequest(
                source_format="raw_dna",
                raw_rows=[{"name": "changed", "sequence": "ATGC"}],
                topology_default="circular",
                topology_overrides={2: "linear"},
                idempotency_key="same-import",
            )
            with pytest.raises(IdempotencyConflictError):
                await commit_sequence_import(session, conflict)
            assert await session.scalar(select(func.count()).select_from(NucleotideSequence)) == 2
            assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ngs_import_reuses_exact_revision_and_records_new_source_operation(tmp_path: Path) -> None:
    engine, sessions = await _molbio_session(tmp_path)
    try:
        async with sessions() as session:
            first = await commit_sequence_import(
                session,
                SequenceImportRequest(
                    source_format="raw_dna",
                    raw_rows=[{"name": "library-reference", "sequence": "ATGCGGTT"}],
                    topology_default="circular",
                    idempotency_key="library-origin",
                    origin_surface="molbio",
                    source_provider="library",
                    source_id="library-entry",
                ),
            )
            imported = await commit_sequence_import(
                session,
                SequenceImportRequest(
                    source_format="fasta",
                    source_text=">ngs-upload\nATGCGGTT\n",
                    topology_default="linear",
                    idempotency_key="ngs-origin",
                    origin_surface="ngs",
                    source_provider="upload",
                    source_id="expected.fasta",
                ),
            )
            assert imported["records"][0]["reused_existing_revision"] is True
            assert imported["records"][0]["sequence_id"] == first["records"][0]["sequence_id"]
            assert imported["records"][0]["revision_id"] == first["records"][0]["revision_id"]
            assert imported["origin_surface"] == "ngs"
            assert imported["source_provider"] == "upload"
            assert imported["source_id"] == "expected.fasta"
            assert await session.scalar(select(func.count()).select_from(NucleotideSequence)) == 1
            assert await session.scalar(select(func.count()).select_from(MolecularRevision)) == 1
            assert await session.scalar(select(func.count()).select_from(MolecularImportBatch)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_apis_are_exact_and_historical_receipts_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    molbio_engine, molbio_sessions = await _molbio_session(tmp_path)
    main_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with main_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    main_sessions = sessionmaker(main_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with molbio_sessions() as molbio_session:
            imported = await commit_sequence_import(
                molbio_session,
                SequenceImportRequest(
                    source_format="raw_dna",
                    raw_rows=[{"name": "historical", "sequence": "ATGC"}],
                    topology_default="linear",
                    idempotency_key="revision-import",
                ),
            )
            sequence_id = imported["records"][0]["sequence_id"]
            first_revision_id = imported["records"][0]["revision_id"]
            sequence = await molbio_session.get(NucleotideSequence, sequence_id)
            assert sequence is not None
            sequence.sequence = "GGCC"
            sequence.length = 4
            second_revision = await record_sequence_revision(
                molbio_session,
                sequence,
                change_kind="update",
                provenance={"source": "focused-test"},
            )
            await molbio_session.commit()

            revisions = await list_sequence_revisions(sequence_id, molbio_session)
            assert [item["revision_number"] for item in revisions] == [2, 1]
            assert revisions[0]["is_current"] is True
            assert [item["topology"] for item in revisions] == ["linear", "linear"]
            detail = await get_sequence_revision(sequence_id, first_revision_id, molbio_session)
            assert detail["revision_id"] == first_revision_id
            assert detail["snapshot"]["sequence"] == "ATGC"
            assert detail["is_current"] is False

            monkeypatch.setattr(
                "services.molbio_ngs_receipts.get_inputs_dir",
                lambda: tmp_path / "inputs",
            )
            async with main_sessions() as main_session:
                receipt_payload = NgsReceiptRequest(revision_id=first_revision_id)
                receipt = await issue_sequence_ngs_receipt(
                    sequence_id,
                    receipt_payload,
                    molbio_session,
                    main_session,
                )
                assert receipt["revision_id"] == first_revision_id
                assert receipt["revision_sha256"] == sha256_text("ATGC")

                with pytest.raises(ValidationError):
                    NgsReceiptRequest()
                with pytest.raises(HTTPException) as wrong_sequence:
                    await issue_sequence_ngs_receipt(
                        "not-the-sequence",
                        NgsReceiptRequest(revision_id=first_revision_id),
                        molbio_session,
                        main_session,
                    )
                assert wrong_sequence.value.status_code == 404
    finally:
        await main_engine.dispose()
        await molbio_engine.dispose()

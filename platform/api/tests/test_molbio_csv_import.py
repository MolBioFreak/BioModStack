"""CSV uses the authoritative preview/atomic commit lane for expected sequences."""
import hashlib

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import MolecularImportBatch, MolecularRevision, NucleotideSequence
from services import molbio_sequence_import as imports


def request(text, **kwargs):
    return imports.SequenceImportRequest(
        source_format="csv", source_text=text, topology_default="circular",
        origin_surface="ngs", source_provider="upload", source_id="references.csv", **kwargs,
    )


def test_csv_spreadsheet_headers_quotes_newlines_and_metadata():
    source = '\ufeffPlasmid Name,DNA Sequence,Topology,Description,Accession,Organism,Barcode\r\n"A, clone","acgt\r\nry",LINEAR,"A ""quoted"" note",REF1,synthetic,barcode01\r\n\r\nB,GGCC,,,,,barcode02\r\n'
    result = imports.build_sequence_import_preview(request(source))
    assert result["valid"], result["errors"]
    assert result["source_format"] == "csv"
    assert result["source_digest"] == hashlib.sha256(source.encode()).hexdigest()
    first, second = result["records"]
    assert first["name"] == "A, clone"
    assert first["sequence"] == "ACGTRY"
    assert first["description"] == 'A "quoted" note'
    assert first["accession"] == "REF1"
    assert first["organism"] == "synthetic"
    assert [first["topology"], second["topology"]] == ["linear", "circular"]
    assert [first["record_ordinal"], second["record_ordinal"]] == [1, 2]
    assert "barcode" not in first


def test_csv_topology_can_be_changed_in_preview_without_rewriting_source():
    result = imports.build_sequence_import_preview(request(
        "name,sequence,topology\nA,ACGT,linear\nB,TGCA,\n", topology_overrides={1: "circular", 2: "linear"},
    ))
    assert result["valid"], result["errors"]
    assert [row["topology"] for row in result["records"]] == ["circular", "linear"]


@pytest.mark.parametrize("source,code", [
    ("A,ACGT\n", "csv_header"),
    ("name,bases,sequence\nA,ACGT,ACGT\n", "csv_header"),
    ("name,plasmid,sequence\nA,A,ACGT\n", "csv_header"),
    ("name,sequence\n", "empty_source"),
    ("name,sequence\nA,ACGT,extra\n", "invalid_record"),
    ("name,sequence\nA\n", "invalid_record"),
    ("name,sequence\n ,ACGT\n", "invalid_record"),
    ("name,sequence\nA,\n", "invalid_record"),
    ("name,sequence\nA,ACGT!\n", "invalid_record"),
    ("name,sequence,topology\nA,ACGT,round\n", "invalid_record"),
    ('name,sequence\nA,"ACGT\n', "parse_error"),
])
def test_csv_rejects_ambiguous_or_invalid_rows(source, code):
    result = imports.build_sequence_import_preview(request(source))
    assert not result["valid"]
    assert result["errors"][0]["code"] == code
    if code == "invalid_record":
        assert "CSV record 1 (line 2)" in result["errors"][0]["message"]


def test_csv_large_fields_use_sequence_limits_not_csv_library_default():
    result = imports.build_sequence_import_preview(request("name,sequence\nA," + "A" * 150_000))
    assert result["valid"], result["errors"]
    assert result["records"][0]["length"] == 150_000
    result = imports.build_sequence_import_preview(request("name,sequence\nA," + "A" * (imports.MAX_IMPORT_RECORD_BASES + 1)))
    assert not result["valid"]
    assert result["errors"][0]["record_ordinal"] == 1


def test_csv_record_total_and_source_bounds(monkeypatch):
    monkeypatch.setattr(imports, "MAX_IMPORT_RECORDS", 2)
    result = imports.build_sequence_import_preview(request("name,sequence\nA,A\nB,C\nC,G\n"))
    assert not result["valid"]
    assert result["errors"][0]["code"] == "too_many_records"
    monkeypatch.setattr(imports, "MAX_IMPORT_TOTAL_BASES", 3)
    result = imports.build_sequence_import_preview(request("name,sequence\nA,AA\nB,CC\n"))
    assert not result["valid"]
    assert result["errors"][0]["code"] == "batch_too_large"
    monkeypatch.setattr(imports, "MAX_IMPORT_SOURCE_BYTES", 10)
    result = imports.build_sequence_import_preview(request("name,sequence\nA,A\n"))
    assert not result["valid"]
    assert result["errors"][0]["code"] == "source_too_large"


def test_csv_request_requires_text_and_forbids_parallel_raw_rows():
    with pytest.raises(ValidationError, match="requires source_text"):
        imports.SequenceImportRequest(source_format="csv", topology_default="linear")
    with pytest.raises(ValidationError, match="forbids raw_rows"):
        request("name,sequence\nA,ACGT", raw_rows=[{"name": "B", "sequence": "GGCC"}])


@pytest.mark.asyncio
async def test_csv_commit_is_atomic_idempotent_and_preserves_source_and_versions(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'csv.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    try:
        async with sessions() as session:
            invalid = request("name,sequence\nA,ACGT\nB,ACGT!\n", idempotency_key="bad-csv")
            with pytest.raises(imports.SequenceImportInputError):
                await imports.commit_sequence_import(session, invalid)
            for model in (NucleotideSequence, MolecularRevision, MolecularImportBatch):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            payload = request("name,sequence,topology\nA,ACGT,linear\nB,GGCC,\n", idempotency_key="valid-csv")
            result = await imports.commit_sequence_import(session, payload)
            assert await imports.commit_sequence_import(session, payload) == result
            assert result["source_format"] == "csv"
            assert result["source_id"] == "references.csv"
            assert result["source_provider"] == "upload"
            assert [row["name"] for row in result["records"]] == ["A", "B"]
            assert [row["topology"] for row in result["records"]] == ["linear", "circular"]
            for row in result["records"]:
                revision = await session.get(MolecularRevision, row["revision_id"])
                assert revision is not None
                assert revision.content_sha256 == row["content_sha256"]
            assert await session.scalar(select(func.count()).select_from(NucleotideSequence)) == 2
            assert await session.scalar(select(func.count()).select_from(MolecularImportBatch)) == 1
    finally:
        await engine.dispose()

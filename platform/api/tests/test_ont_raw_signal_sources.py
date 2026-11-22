"""The Raw signal tab's reads: what a Project holds, which reads a file has, and which were opened."""
import json
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base, InputFile, Job, OntInstrumentRun, OntMoveTableSource, OntRawSignalDerivationJob, OntRawSignalLookup, OntRawSignalRepresentation,
    _canonical_sqlite_json,
)
from services import ont_raw_signal as raw


def _index_bytes(read_ids: list[str]) -> bytes:
    body = b"".join(len(r).to_bytes(2, "little") + r.encode() + bytes(16) for r in read_ids)
    return b"SLOW5IDX" + bytes(56) + body + b"XDI5WOLS"


def _run(run_id: str, project: str, *, minknow: str | None = None, name: str | None = None, when: int = 1) -> OntInstrumentRun:
    return OntInstrumentRun(
        id=run_id, position_id="X1" if minknow else "external", minknow_run_id=minknow, state="completed",
        observed_at=datetime(2026, 11, when), observed_generation=1, experiment_group=project,
        external_source_relative_path=name, output_directories={}, output_files={}, created_at=datetime(2026, 11, when))


def _representation(rep_id: str, run_id: str, role: str, fmt: str, artifacts: list[dict], *, state="ready", receipts=None, reads=None):
    manifest = {"schema": "bms.ont.raw-signal-artifacts.v1", "run_id": run_id, "observed_generation": 1, "format": fmt, "artifacts": artifacts}
    return OntRawSignalRepresentation(
        id=rep_id, run_id=run_id, observed_generation=1, role=role, source_kind="external_native" if role == "source" else "pod5_to_blow5",
        format=fmt, state=state, reason_code="ok", artifact_manifest=manifest, manifest_sha256=raw._digest(manifest),
        validation_receipts=receipts or {}, read_count=reads, created_at=datetime(2026, 11, 1))


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'signal.db'}", json_serializer=_canonical_sqlite_json)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_project_lists_its_sequenced_runs_and_imported_files_with_their_signal(factory, tmp_path) -> None:
    async with factory() as session:
        session.add_all([
            _run("run-imported", "project-a", name="plasmids/lib_A.pod5", when=2),
            _run("run-sequenced", "project-a", minknow="7f3c1a2b", when=3),
            _run("run-plain", "project-a", when=4),
            _run("run-other", "project-b", name="other.pod5"),
        ])
        await session.flush()
        session.add_all([
            _representation("src-a", "run-imported", "source", "pod5", [{"kind": "pod5", "bytes": 3_000_000_000, "path": "/x/lib_A.pod5"}]),
            _representation("b5-a", "run-imported", "derived", "blow5", [{"kind": "blow5", "bytes": 5, "path": "/x/a.blow5"}],
                            receipts={"adjacent_index": True}, reads=1200),
            _representation("src-other", "run-other", "source", "pod5", [{"kind": "pod5", "bytes": 1, "path": "/x/other.pod5"}]),
        ])
        await session.flush()
        session.add(OntRawSignalDerivationJob(
            id="job-a", run_id="run-imported", observed_generation=1, source_representation_id="src-a", requested_preference="auto",
            consumer_id="test", profile_id="p", state="ready", reason_code="indexed_blow5_ready", resource_snapshot={}, stage_receipts={}))
        session.add_all([
            Job(id="result-1", name="lib A basecall", status="completed", model_id="nanopore", mode="ont",
                params={"source_instrument_run_id": "run-imported", "source_instrument_observed_generation": 1, "dataset_id": "ds"}, created_at=datetime(2026, 11, 5)),
            Job(id="result-2", name="lib A failed", status="failed", model_id="nanopore", mode="ont", params={"source_instrument_run_id": "run-imported"}),
            Job(id="result-3", name="unrelated", status="completed", model_id="nanopore", mode="ont", params={"source_instrument_run_id": "run-other"}),
        ])
        session.add(InputFile(id="bam-a", filename="lib_A.bam", file_type="bam", directory="/x"))
        await session.flush()
        session.add(OntMoveTableSource(
            id="moves-a", input_file_id="bam-a", run_id="run-imported", observed_generation=1, raw_representation_id="b5-a", artifact_sha256="f" * 64,
            artifact_size_bytes=10, unique_read_count=1100, basecall_model_id="dna_r10.4.1_e8.2_400bps_sup@v4.3.0", molecule_type="dna",
            external_registration_receipt_id="receipt-a", source_runtime_identity={}, validation_state="ready",
            reason_code="move_source_exact_read_set_ready", validation_receipt={}, attempt_number=1))
        await session.commit()
        sources = await raw.list_signal_sources(session, experiment_group="project-a")

    # A run that neither came from MinKNOW nor holds signal is not listed; other Projects never are.
    assert [(item["run_id"], item["origin"], item["name"]) for item in sources] == [
        ("run-sequenced", "sequenced", "run-sequenced"), ("run-imported", "imported", "lib_A.pod5")]
    sequenced, imported = sources
    assert sequenced["source"] is None and sequenced["blow5"] is None and sequenced["results"] == [] and sequenced["basecalls"] is None
    assert imported["basecalls"] == {"move_source_id": "moves-a", "state": "ready", "reason_code": "move_source_exact_read_set_ready",
                                     "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0", "read_count": 1100, "external": True}
    assert imported["source"] == {"representation_id": "src-a", "format": "pod5", "bytes": 3_000_000_000, "removed": False}
    # Its BLOW5 files are not where the record says, so it is reported as not readable rather than failing later.
    assert imported["blow5"] == {"representation_id": "b5-a", "read_count": 1200, "readable": False}
    assert imported["conversion"] == {"job_id": "job-a", "state": "ready", "reason_code": "indexed_blow5_ready"}
    assert imported["results"] == [{"job_id": "result-1", "name": "lib A basecall", "created_at": "2026-11-05T00:00:00", "signal_workbench": True}]
    assert "/x/" not in json.dumps(sources)


@pytest.mark.asyncio
@pytest.mark.parametrize("partitioned", [False, True])
async def test_reads_of_a_ready_blow5_are_listed_sorted_paged_and_searchable(factory, tmp_path, partitioned) -> None:
    read_ids = [f"{n:04d}-read" for n in (7, 3, 12, 1, 9)]
    blow5 = tmp_path / "reads.blow5"; blow5.write_bytes(b"signal")
    # With a routing table the read list comes from it, so the index here names a different read.
    index = tmp_path / "reads.blow5.idx"; index.write_bytes(_index_bytes(["zzzz-not-listed"] if partitioned else read_ids))
    artifacts = [raw._file_artifact(blow5, "a-blow5", kind="blow5"), raw._file_artifact(index, "a-index", kind="blow5_index")]
    if partitioned:
        routing = tmp_path / "routing.json"
        routing.write_text(json.dumps({"read_to_group": {read_id: "g" for read_id in read_ids}, "groups": {}}))
        artifacts += [raw._file_artifact(routing, "a-routing", kind="read_routing"), {"kind": "blow5", "path": "/second", "bytes": 1}]
    async with factory() as session:
        session.add(_run("run-1", "project-a", name="reads.pod5"))
        await session.flush()
        session.add(_representation("b5", "run-1", "derived", "blow5", artifacts, receipts={"adjacent_index": True}))
        session.add(_representation("b5-pending", "run-1", "derived", "blow5", [{"kind": "blow5", "path": "/y", "bytes": 2}], state="preparable"))
        await session.commit()
        raw._READ_ID_CACHE.clear()
        assert (await raw.list_signal_sources(session, experiment_group="project-a"))[0]["blow5"]["readable"] is True
        first = await raw.list_representation_reads(session, run_id="run-1", observed_generation=1, representation_id="b5", limit=2)
        second = await raw.list_representation_reads(session, run_id="run-1", observed_generation=1, representation_id="b5", offset=4, limit=2)
        found = await raw.list_representation_reads(session, run_id="run-1", observed_generation=1, representation_id="b5", query="0012")
        with pytest.raises(KeyError):
            await raw.list_representation_reads(session, run_id="run-2", observed_generation=1, representation_id="b5")
        with pytest.raises(ValueError, match="once the indexed BLOW5 is ready"):
            await raw.list_representation_reads(session, run_id="run-1", observed_generation=1, representation_id="b5-pending")
    assert first == {"representation_id": "b5", "total": 5, "matched": 5, "offset": 0, "read_ids": ["0001-read", "0003-read"]}
    assert second["read_ids"] == ["0012-read"]
    assert found["matched"] == 1 and found["read_ids"] == ["0012-read"] and found["total"] == 5


@pytest.mark.asyncio
async def test_a_changed_index_is_refused_rather_than_listed(factory, tmp_path) -> None:
    blow5 = tmp_path / "reads.blow5"; blow5.write_bytes(b"signal")
    index = tmp_path / "reads.blow5.idx"; index.write_bytes(_index_bytes(["a-read"]))
    artifacts = [raw._file_artifact(blow5, "a-blow5", kind="blow5"), raw._file_artifact(index, "a-index", kind="blow5_index")]
    index.write_bytes(_index_bytes(["b-read"]))
    async with factory() as session:
        session.add(_run("run-1", "project-a"))
        await session.flush()
        session.add(_representation("b5", "run-1", "derived", "blow5", artifacts, receipts={"adjacent_index": True}))
        await session.commit()
        raw._READ_ID_CACHE.clear()
        with pytest.raises(ValueError, match="diverged"):
            await raw.list_representation_reads(session, run_id="run-1", observed_generation=1, representation_id="b5")


@pytest.mark.asyncio
async def test_opened_reads_are_listed_newest_first_without_their_samples(factory) -> None:
    async with factory() as session:
        session.add(_run("run-1", "project-a"))
        await session.flush()
        session.add(_representation("b5", "run-1", "derived", "blow5", [], receipts={"adjacent_index": True}))
        await session.flush()
        for day, read_id, state in ((1, "old-read", "ready"), (3, "new-read", "ready"), (2, "bad-read", "failed")):
            session.add(OntRawSignalLookup(
                id=f"lookup-{read_id}", run_id="run-1", observed_generation=1, representation_id="b5", read_id=read_id, state=state,
                reason_code="ok", sample_count=500 if state == "ready" else None, samples=[1.0, 2.0] if state == "ready" else None,
                receipt={}, created_at=datetime(2026, 11, day), updated_at=datetime(2026, 11, day)))
        await session.commit()
        lookups = await raw.list_waveform_lookups(session, run_id="run-1", observed_generation=1)
    assert [(item["read_id"], item["state"], item["sample_count"]) for item in lookups] == [
        ("new-read", "ready", 500), ("bad-read", "failed", None), ("old-read", "ready", 500)]
    assert all("samples" not in item for item in lookups)

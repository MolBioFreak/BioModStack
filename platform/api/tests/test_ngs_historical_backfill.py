"""Deterministic definitions only: never use managed DB/artifacts in this suite."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Job, NgsAlignmentDerivedProduct as Product
from services import ngs_alignment_derived_products as lifecycle
from services import ngs_historical_backfill as history


def source(job_id="fixture"):
    return lifecycle.source_identity(job_id=job_id, session_id="session", mode="primary",
        reference={"contig": "ref", "length_bp": 100, "topology": "linear",
            "normalized_sequence_sha256": "a" * 64, "fasta_sha256": "b" * 64, "fai_sha256": "c" * 64},
        source_manifest_sha256="d" * 64, source_artifact_set_sha256="e" * 64,
        package_artifact_set_sha256="f" * 64, alignment_pair_sha256="1" * 64,
        alignment_sha256="2" * 64, alignment_size_bytes=100,
        alignment_index_sha256="3" * 64, alignment_index_size_bytes=100)


def job(job_id="fixture", state="completed"):
    return Job(id=job_id, name="fixture", model_id="nanopore", mode="analysis",
        status=state, queue_status=state, awaiting_input=False,
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={"result_integrity": {"state": "validated", "partial": False,
            "workflow_id": "ont_fastq_qc", "input_mode": "fastq", "scientific_score": 0.125}})


async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fixture.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([job(), job(history.PROTECTED_JOB), job("cancelled", "cancelled"), job("failed", "failed")])
        await session.commit()
        await session.execute(text("CREATE TRIGGER no_scientific_updates BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT,'science is immutable'); END"))
        await session.commit()
    monkeypatch.setattr(history, "resolve_persisted_job_result_root", lambda _job: tmp_path)
    def discover(candidate):
        history.eligibility(candidate)
        return [(source(str(candidate.id)), {})]
    monkeypatch.setattr(history, "discover_sources", discover)
    return engine, sessions


@pytest.mark.asyncio
async def test_repeat_and_lost_response_preserve_science_and_failed_products(tmp_path, monkeypatch):
    engine, sessions = await database(tmp_path, monkeypatch)
    try:
        async with sessions() as session:
            plan = await history.inventory(session, ["fixture", history.PROTECTED_JOB, "cancelled", "failed"])
            before = (await session.execute(text("SELECT * FROM jobs ORDER BY id"))).all()
            assert [r["reason"] for r in plan["rows"] if r["disposition"] == "excluded"] == ["not_completed", "protected", "not_completed"]
            await history.backfill(session, plan)
            await session.commit()  # response can be lost here
        async with sessions() as session:
            catalog = await session.get(Product, lifecycle.catalog_request_id(source()))
            preview = await session.get(Product, lifecycle.default_preview_request_id(catalog))
            preview.state, preview.error_code = "failed", "cancelled"
            await session.commit()
            preimage = (catalog.id, preview.id, preview.state, preview.error_code, preview.attempt_count)
            await history.backfill(session, plan)
            await session.commit()
            assert preimage == (catalog.id, preview.id, preview.state, preview.error_code, preview.attempt_count)
            assert len((await session.scalars(select(Product))).all()) == 2
            assert (await session.execute(text("SELECT * FROM jobs ORDER BY id"))).all() == before
            assert history.owns_request(await session.get(Job, "fixture"), catalog)
            assert history.owns_request(await session.get(Job, "fixture"), preview)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError, RuntimeError])
async def test_cancel_or_crash_before_commit_rolls_back_intent_and_ownership(tmp_path, monkeypatch, error):
    engine, sessions = await database(tmp_path, monkeypatch)
    async def interrupted(*args, **kwargs):
        raise error("injected before preview intent")
    monkeypatch.setattr(lifecycle, "request_preview", interrupted)
    try:
        async with sessions() as session:
            plan = await history.inventory(session, ["fixture"])
            with pytest.raises(error):
                try:
                    await history.backfill(session, plan)
                finally:
                    await session.rollback()
        async with sessions() as session:
            assert list(await session.scalars(select(Product))) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_drift_blocks_whole_plan_without_reinterpreting_science(tmp_path, monkeypatch):
    engine, sessions = await database(tmp_path, monkeypatch)
    try:
        async with sessions() as session:
            plan = await history.inventory(session, ["fixture"])
            changed = source()
            changed["alignment_sha256"] = "9" * 64
            monkeypatch.setattr(history, "discover_sources", lambda _job: [(changed, {})])
            with pytest.raises(history.Blocked, match="fresh inventory differs"):
                await history.backfill(session, plan)
            await session.rollback()
            assert list(await session.scalars(select(Product))) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crash_expiry_requires_explicit_retry_and_old_claim_cannot_publish(tmp_path, monkeypatch):
    from services.ngs_alignment_presentation import PresentationClaimLost
    engine, sessions = await database(tmp_path, monkeypatch)
    now = datetime(2026, 9, 1)
    try:
        async with sessions() as session:
            await history.backfill(session, await history.inventory(session, ["fixture"]))
            await session.commit()
            row = await lifecycle.claim_next_product(session, product="catalog", claim_token="crashed", now=now, lease_seconds=1)
            assert await lifecycle.recover_expired_products(session, now=now + timedelta(seconds=2)) == 1
            with pytest.raises(PresentationClaimLost):
                await lifecycle.publish_product(session, row, "crashed", source_authority_sha256=row.source_authority_sha256,
                    authority_sha256="a" * 64, manifest_sha256="b" * 64, now=now + timedelta(seconds=2))
            assert await lifecycle.claim_next_product(session, product="catalog", claim_token="no-auto-retry", now=now + timedelta(seconds=3)) is None
            await history.backfill(session, await history.inventory(session, ["fixture"]))
            await session.commit()
            await session.refresh(row)
            assert row.state == "failed" and row.error_code == "infrastructure_failed"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("state", ["requested", "running", "ready", "failed"])
def test_catalog_readiness_independent_of_preview_and_cutover_requires_terminal(state):
    catalog = SimpleNamespace(id="catalog", product="catalog", state="ready", attempt_count=1,
        manual_retry_count=0, request_sha256="a" * 64, authority_sha256="b" * 64, manifest_sha256="c" * 64)
    preview = SimpleNamespace(id="preview", product="preview", state=state, attempt_count=1,
        manual_retry_count=0, request_sha256="d" * 64, authority_sha256="e" * 64,
        manifest_sha256="f" * 64, error_code="resource_limit")
    status = history.activation_disposition(catalog, preview)
    assert status["complete_reads_ready"] is True
    assert status["cutover_terminal"] is (state in {"ready", "failed"})


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled", "running"])
def test_protected_id_always_excluded_without_assuming_transcript_state(state):
    with pytest.raises(history.Blocked) as blocked:
        history.eligibility(job(history.PROTECTED_JOB, state))
    assert blocked.value.reason == "protected"


def test_historical_binding_is_exact_and_does_not_modify_receipt(monkeypatch, tmp_path):
    candidate = job()
    monkeypatch.setattr(history, "resolve_persisted_job_result_root", lambda _: tmp_path)
    before = deepcopy(candidate.provenance)
    row = SimpleNamespace(source_identity=source(), historical_owner=history.ownership(candidate, source()))
    assert history.owns_request(candidate, row)
    assert candidate.provenance == before
    candidate.provenance["result_integrity"]["scientific_score"] = 0.5
    assert not history.owns_request(candidate, row)


def test_additive_ownership_migration_preserves_original_rows_and_registry_ordinals(tmp_path):
    from migrations.add_ngs_alignment_presentation_jobs import migrate as legacy
    from migrations.add_ngs_alignment_derived_products import migrate as split
    from migrations.add_ngs_historical_request_ownership import migrate
    from migrations.runner import MIGRATIONS, _validate_applied_migration_identities
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, queue_status TEXT, provenance TEXT)")
        connection.execute("INSERT INTO jobs VALUES (?,?,?,?)", (history.PROTECTED_JOB, "cancelled", "cancelled", '{ "untouched": true }'))
    legacy(path)
    split(path)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT * FROM jobs").fetchall()
        old_sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='ngs_alignment_presentation_jobs'").fetchone()
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM jobs").fetchall() == before
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name='ngs_alignment_presentation_jobs'").fetchone() == old_sql
        assert [row[1] for row in connection.execute("PRAGMA table_info(ngs_alignment_derived_products)")].count("historical_owner") == 1
    installed = {entry.version: entry.name for entry in MIGRATIONS}
    assert installed[46] == "add_ngs_alignment_presentation_jobs"
    assert installed[47] == "add_ngs_alignment_derived_products"
    assert installed[48] == "add_native_alignment_viewer_sessions"
    assert installed[49] == "add_ngs_historical_request_ownership"
    _validate_applied_migration_identities(installed)
    incompatible = dict(installed)
    incompatible[49] = "different_migration"
    with pytest.raises(RuntimeError):
        _validate_applied_migration_identities(incompatible)


def test_builder_adopts_only_new_contract_and_preserves_predecessor_bytes(tmp_path, monkeypatch):
    import pysam
    from services import ngs_alignment_product_builder as builder
    bam = tmp_path / "source.bam"
    with pysam.AlignmentFile(bam, "wb", header={"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "ref", "LN": 100}]}) as handle:
        read = pysam.AlignedSegment(handle.header)
        read.query_name = "literal/read"
        read.query_sequence = "ACGT"
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 2
        read.mapping_quality = 60
        read.cigarstring = "4M"
        read.query_qualities = pysam.qualitystring_to_array("IIII")
        handle.write(read)
    pysam.index(str(bam))
    bai = tmp_path / "source.bam.bai"
    source_id = source()
    for path, prefix in ((bam, "alignment"), (bai, "alignment_index")):
        source_id[prefix + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        source_id[prefix + "_size_bytes"] = path.stat().st_size
    contract = lifecycle.catalog_contract(source_id)
    request = SimpleNamespace(id=lifecycle.catalog_request_id(source_id), product="catalog", intent_sha256=lifecycle.identity_sha256(contract),
        state="running", claim_token="claim", authority_sha256=None, manifest_sha256=None,
        source_identity=source_id, source_authority_sha256=lifecycle.identity_sha256(source_id),
        request_sha256=lifecycle.identity_sha256(contract))
    from services.global_resource_admission import SCHEMA
    units = {"cpu_threads": 1, "disk_bytes": 16 * 1024 * 1024, "dram_bytes": 16 * 1024 * 1024}
    receipt = {key: "fixture" for key in ("reservation_id", "target_id", "machine_id", "policy_id",
        "policy_version", "storage_device", "owner")}
    receipt.update(schema=SCHEMA, policy_generation=1, requested=units, effective=units, storage_path=str(tmp_path))
    allocation = SimpleNamespace(disk_bytes=units["disk_bytes"], dram_bytes=units["dram_bytes"], receipt=receipt)
    inputs = {"alignment_path": bam, "index_path": bai, "result_root": tmp_path}
    old = tmp_path / ".alignment-presentations"
    old.mkdir()
    predecessor = old / "retained-v3.bin"
    predecessor.write_bytes(b"not a v2/v6 artifact")
    monkeypatch.setattr(builder.storage, "_creation_authority", lambda: ("fixture", "fixture"))
    first = builder._build_product(request, inputs, lambda: None, allocation=allocation)
    sealed = tmp_path / ".alignment-products" / request.id / "sealed"
    preimage = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in sealed.iterdir()}
    second = builder._build_product(request, inputs, lambda: None, allocation=allocation)
    assert second == first
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in sealed.iterdir()} == preimage
    assert predecessor.read_bytes() == b"not a v2/v6 artifact"
    (sealed / "manifest.json").write_bytes(b'{"schema":"old-v3"}')
    with pytest.raises(builder.Failure):
        builder._build_product(request, inputs, lambda: None, allocation=allocation)
    assert predecessor.read_bytes() == b"not a v2/v6 artifact"
    assert hashlib.sha256(bam.read_bytes()).hexdigest() == source_id["alignment_sha256"]


@pytest.mark.asyncio
async def test_legacy_selection_refuses_ambiguous_newest_and_binds_explicit_authority():
    from services.ngs_alignment_presentation import get_session_presentation, PresentationSourceStale
    class Session:
        def __init__(self, rows):
            self.rows = rows
            self.statement = None
        async def scalars(self, statement):
            self.statement = statement
            return SimpleNamespace(all=lambda: self.rows)
    ambiguous = Session([SimpleNamespace(id="old"), SimpleNamespace(id="new")])
    with pytest.raises(PresentationSourceStale):
        await get_session_presentation(ambiguous, job_id="job", session_id="session")
    exact = Session([SimpleNamespace(id="old")])
    result = await get_session_presentation(exact, job_id="job", session_id="session", authority_sha256="a" * 64)
    assert result.id == "old"
    assert "authority_sha256" in str(exact.statement)
    assert "ORDER BY" not in str(exact.statement)


@pytest.mark.parametrize("message,reason", [
    ("native reference topology authority is missing", "missing_reference"),
    ("native artifact-set integrity mismatch", "digest_mismatch"),
    ("native single-reference catalog is unsupported", "unsupported_source"),
])
def test_owner_blockers_are_typed_and_never_accepted(message, reason):
    assert history.failure_reason(history.storage.AlignmentSessionError(message)) == reason


def test_accepted_fixture_requirements_are_not_observed_acceptance():
    assert history.ACCEPTED_FIXTURE != history.PROTECTED_JOB
    expected = history.ACCEPTED_FIXTURE_REQUIREMENTS
    assert expected["preview_read_ids"] == 5000
    assert expected["preview_mapped_projections"] == 9522
    assert expected["read_id"] == "b0aabdbd-d617-4392-83db-9c0c7083e688"
    assert expected["waveform_source_samples"] == 84356
    assert expected["waveform_displayed_points"] == 16872

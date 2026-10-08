"""Worker regressions for independent products, not retired combined jobs.

Injected builders isolate lifecycle/CAS behavior; they are not scientific package
validation evidence. The legacy package regression below uses real sealed bytes.
All cooperative test builders have a hard test-only deadline and cleanup joins.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, NgsAlignmentDerivedProduct as Product
from tests.ngs_resource_fixture import ngs_resources  # noqa: F401
from services import ngs_alignment_derived_products as lifecycle
from services.ngs_alignment_presentation import PresentationClaimLost, PresentationSourceStale
from services.ngs_alignment_presentation_worker import (
    NgsAlignmentPresentationWorker, PresentationBuildFailure,
)


async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'presentation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _job(job_id: str = "job-1") -> Job:
    return Job(id=job_id, name="NGS", model_id="nanopore", mode="analysis", params={},
               status="completed", queue_status="completed",
               provenance={"result_integrity": {"state": "validated"}})


def _source():
    return lifecycle.source_identity(
        job_id="job-1", session_id="session-1", mode="primary",
        reference={"contig": "ref", "length_bp": 1000, "topology": "linear",
                   "normalized_sequence_sha256": "1" * 64,
                   "fasta_sha256": "2" * 64, "fai_sha256": "3" * 64},
        source_manifest_sha256="a" * 64, source_artifact_set_sha256="b" * 64,
        package_artifact_set_sha256="c" * 64, alignment_pair_sha256="d" * 64,
        alignment_sha256="e" * 64, alignment_size_bytes=100,
        alignment_index_sha256="f" * 64, alignment_index_size_bytes=80,
    )


def _package(row):
    return {"source_authority_sha256": row.source_authority_sha256,
            "authority_sha256": "4" * 64, "manifest_sha256": "5" * 64}


def _worker(sessions, **kwargs):
    return NgsAlignmentPresentationWorker(
        sessions, resolve_source_authority=kwargs.pop(
            "resolve_source_authority", lambda row: row.source_authority_sha256), **kwargs)


async def _run(worker):
    return await asyncio.wait_for(worker.run_once(), timeout=5)


async def _stored(sessions, row):
    async with sessions() as session:
        return await session.get(Product, row.id)


async def _claim(sessions, row, *, token="claim-1", now=None, lease_seconds=30):
    async with sessions() as session:
        claimed = await lifecycle.claim_next_product(
            session, product=row.product, claim_token=token, now=now,
            lease_seconds=lease_seconds)
    assert claimed is not None and claimed.id == row.id
    return claimed


@pytest_asyncio.fixture(params=["catalog", "preview"])
async def product_case(tmp_path, request):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            catalog = await lifecycle.request_catalog(session, _source())
            await session.commit()
        row = catalog
        if request.param == "preview":
            catalog = await _claim(sessions, catalog)
            async with sessions() as session:
                await lifecycle.publish_product(session, catalog, "claim-1", **_package(catalog))
            async with sessions() as session:
                catalog = await session.get(Product, catalog.id)
                row = await lifecycle.request_default_preview(session, catalog)
                await session.commit()
        yield sessions, row
        # A derived disposition must never rewrite accepted scientific status.
        async with sessions() as session:
            job = await session.get(Job, "job-1")
            assert (job.status, job.queue_status) == ("completed", "completed")
            assert job.provenance == {"result_integrity": {"state": "validated"}}
            if row.product == "preview":
                dependency = await session.get(Product, row.catalog_request_id)
                assert dependency.state == "ready"
                assert dependency.claim_token is None
                assert dependency.authority_sha256 == "4" * 64
                assert dependency.manifest_sha256 == "5" * 64
                assert dependency.attempt_count == 1
                assert dependency.manual_retry_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_requested_product_claim_is_atomic_and_increments_attempt(product_case):
    sessions, row = product_case
    now = datetime(2026, 9, 1, 12)
    claimed = await _claim(sessions, row, now=now)
    async with sessions() as session:
        lost = await lifecycle.claim_next_product(
            session, product=row.product, now=now, claim_token="loser")
    stored = await _stored(sessions, row)
    assert lost is None
    assert claimed.state == stored.state == "running"
    assert claimed.claim_token == stored.claim_token == "claim-1"
    assert stored.attempt_count == 1
    assert stored.lease_expires_at == now + timedelta(seconds=30)
    if row.product == "preview":
        assert claimed.catalog_authority_sha256 == "4" * 64
        assert claimed.request_sha256 is not None


@pytest.mark.asyncio
async def test_renewal_and_publication_require_live_claim_cas(product_case):
    sessions, row = product_case
    now = datetime(2026, 9, 1, 12)
    row = await _claim(sessions, row, now=now)
    async with sessions() as session:
        await lifecycle.renew_product_lease(session, row, "claim-1", now=now + timedelta(seconds=10), lease_seconds=40)
    assert (await _stored(sessions, row)).lease_expires_at == now + timedelta(seconds=50)
    for operation in (lifecycle.renew_product_lease, lifecycle.publish_product):
        async with sessions() as session:
            with pytest.raises(PresentationClaimLost):
                await operation(session, row, "loser", now=now + timedelta(seconds=20),
                                **(_package(row) if operation == lifecycle.publish_product else {}))
    async with sessions() as session:
        await lifecycle.publish_product(session, row, "claim-1", now=now + timedelta(seconds=20), **_package(row))
    stored = await _stored(sessions, row)
    assert stored.state == "ready"
    assert stored.claim_token is stored.lease_expires_at is None
    assert stored.authority_sha256 == "4" * 64
    assert stored.manifest_sha256 == "5" * 64


@pytest.mark.asyncio
async def test_expiry_rejects_old_owner_and_never_automatically_requeues(product_case):
    sessions, row = product_case
    old = datetime(2020, 1, 1)
    row = await _claim(sessions, row, now=old, lease_seconds=1)
    for operation in (lifecycle.renew_product_lease, lifecycle.publish_product, lifecycle.fail_product):
        async with sessions() as session:
            kwargs = _package(row) if operation == lifecycle.publish_product else (
                {"error_code": "cancelled"} if operation == lifecycle.fail_product else {})
            with pytest.raises(PresentationClaimLost):
                await operation(session, row, "claim-1", now=old + timedelta(seconds=2), **kwargs)
    worker = _worker(sessions, build=lambda row: pytest.fail("expired work must not build"))
    assert await _run(worker) is None  # Poll recovery, without a service restart.
    assert await _run(worker) is None
    stored = await _stored(sessions, row)
    assert stored.state == "failed" and stored.error_code == "infrastructure_failed"
    assert stored.attempt_count == 1
    assert stored.claim_token is stored.lease_expires_at is None
    assert stored.authority_sha256 is stored.manifest_sha256 is None
    async with sessions() as session:
        assert await lifecycle.recover_expired_products(session) == 0


@pytest.mark.asyncio
async def test_failure_is_terminal_and_only_explicit_retry_runs_again(product_case):
    sessions, row = product_case
    def fail(_row):
        raise PresentationBuildFailure("publication_failed", retryable=True)
    worker = _worker(sessions, build=fail)
    assert await _run(worker) == row.id
    assert await _run(worker) is None
    stored = await _stored(sessions, row)
    assert stored.state == "failed" and stored.error_code == "publication_failed"
    assert stored.attempt_count == 1
    assert stored.claim_token is stored.lease_expires_at is None
    async with sessions() as session:
        current = await session.get(Product, row.id)
        with pytest.raises(PresentationSourceStale):
            await lifecycle.retry_product(session, current, current_source_authority_sha256="0" * 64)
        retried = await lifecycle.retry_product(session, current, current_source_authority_sha256=row.source_authority_sha256)
        assert retried.state == "requested"
        assert retried.attempt_count == 1  # Attempt history is not reset by manual retry.
        assert retried.manual_retry_count == 1 and retried.error_code is None
        replay = await lifecycle.retry_product(session, retried, current_source_authority_sha256=row.source_authority_sha256)
        assert replay.manual_retry_count == 1
    worker._build = _package
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "ready" and stored.attempt_count == 2
    assert stored.manual_retry_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_build", "before_publication", "package"])
async def test_worker_refuses_stale_source_without_scientific_mutation(product_case, phase):
    sessions, row = product_case
    calls = []
    def resolve(request):
        calls.append("resolve")
        if phase == "before_build" or (phase == "before_publication" and calls.count("resolve") == 2):
            return "0" * 64
        return request.source_authority_sha256
    def build(request):
        calls.append("build")
        package = _package(request)
        if phase == "package":
            package["source_authority_sha256"] = "0" * 64
        return package
    worker = _worker(sessions, build=build, resolve_source_authority=resolve)
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "failed" and stored.error_code == "source_invalid"
    assert stored.authority_sha256 is stored.manifest_sha256 is None
    assert ("build" in calls) == (phase != "before_build")
    assert await _run(worker) is None


@pytest.mark.asyncio
async def test_resolver_exception_terminalizes_without_build_or_automatic_retry(product_case):
    sessions, row = product_case
    def resolve(_row):
        raise OSError("resolver failed")
    worker = _worker(sessions, build=lambda row: pytest.fail("must not build"), resolve_source_authority=resolve)
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "failed" and stored.error_code == "infrastructure_failed"
    assert stored.claim_token is stored.lease_expires_at is None
    assert stored.attempt_count == 1
    assert await _run(worker) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(lifecycle.ERROR_CODES) + ["build_timeout"])
async def test_closed_failure_taxonomy_is_terminal_even_with_retryable_hint(product_case, code):
    sessions, row = product_case
    def build(_row):
        if code == "cancelled":
            from services.ngs_alignment_presentation_worker import PresentationBuildAborted
            raise PresentationBuildAborted("builder observed revocation")
        raise PresentationBuildFailure(code, retryable=True)
    worker = _worker(sessions, build=build)
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "failed"
    assert stored.error_code == (code if code in lifecycle.ERROR_CODES else "integrity_mismatch")
    assert stored.attempt_count == 1
    assert stored.claim_token is stored.lease_expires_at is None
    assert stored.authority_sha256 is stored.manifest_sha256 is None
    assert await _run(worker) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["authority_sha256", "manifest_sha256"])
async def test_invalid_package_digest_never_publishes_authority(product_case, field):
    sessions, row = product_case
    def build(request):
        return {**_package(request), field: "not-a-digest"}
    worker = _worker(sessions, build=build)
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "failed" and stored.error_code == "integrity_mismatch"
    assert stored.authority_sha256 is stored.manifest_sha256 is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["resolve", "build"])
async def test_worker_renews_lease_during_source_resolution_and_build(product_case, phase):
    sessions, row = product_case
    entered, release = threading.Event(), threading.Event()
    async def resolve(request):
        if phase == "resolve" and not release.is_set():
            entered.set()
            assert await asyncio.to_thread(release.wait, 3)
        return request.source_authority_sha256
    def build(request):
        if phase == "build":
            entered.set()
            assert release.wait(3), "test build was not released"
        return _package(request)
    worker = _worker(sessions, build=build, resolve_source_authority=resolve, lease_seconds=1)
    task = asyncio.create_task(_run(worker))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        original = (await _stored(sessions, row)).lease_expires_at
        await asyncio.sleep(1.15)  # Outlive the original one-second lease.
        running = await _stored(sessions, row)
        assert running.state == "running"
        assert running.lease_expires_at > original
        assert running.lease_expires_at > datetime.utcnow()
        release.set()
        assert await task == row.id
        stored = await _stored(sessions, row)
        assert stored.state == "ready" and stored.attempt_count == 1
        assert stored.authority_sha256 == "4" * 64
        assert stored.manifest_sha256 == "5" * 64
    finally:
        release.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["claim_token", "source_authority_sha256", "request_sha256"])
async def test_publication_cas_never_overwrites_changed_owner_or_identity(product_case, changed):
    sessions, row = product_case
    resolves = 0
    async def resolve(request):
        nonlocal resolves
        resolves += 1
        if resolves == 2:
            async with sessions() as session:
                await session.execute(update(Product).where(Product.id == row.id).values(
                    **{changed: "winner-claim" if changed == "claim_token" else "0" * 64}))
                await session.commit()
        return request.source_authority_sha256
    worker = _worker(sessions, build=_package, resolve_source_authority=resolve)
    assert await _run(worker) == row.id
    stored = await _stored(sessions, row)
    assert stored.state == "running"
    assert getattr(stored, changed) == ("winner-claim" if changed == "claim_token" else "0" * 64)
    assert stored.authority_sha256 is stored.manifest_sha256 is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "cancel", "repeated_cancel", "claim_loss"])
async def test_revocation_joins_cooperative_builder_and_prevents_late_writes(product_case, tmp_path, action):
    sessions, row = product_case
    started, quiesced, observed_abort, release = (threading.Event() for _ in range(4))
    late = tmp_path / "forbidden-late-write"
    def build(_row, abort):
        started.set()
        deadline = time.monotonic() + 3
        try:
            while not abort.is_set() and not release.wait(0.005):
                assert time.monotonic() < deadline, "worker never revoked build"
            if abort.is_set():
                observed_abort.set()
                assert release.wait(3), "test failed to release quiescing builder"
            abort.checkpoint()
            late.write_text("forbidden", encoding="utf-8")
            return _package(_row)
        finally:
            quiesced.set()
    worker = _worker(sessions, build=build, lease_seconds=1, poll_interval=0.05)
    task = asyncio.create_task(worker.run_once())
    stop_task = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if action == "stop":
            # stop() joins the lifecycle task normally owned by start().
            worker._task = task
            stop_task = asyncio.create_task(worker.stop())
        elif action in {"cancel", "repeated_cancel"}:
            task.cancel()
        else:
            async with sessions() as session:
                await session.execute(update(Product).where(Product.id == row.id).values(claim_token="winner-claim"))
                await session.commit()
        assert await asyncio.to_thread(observed_abort.wait, 2)
        assert not task.done() and not quiesced.is_set()
        if action == "repeated_cancel":
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        release.set()
        if action in {"cancel", "repeated_cancel"}:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        else:
            assert await asyncio.wait_for(task, 3) == row.id
        if stop_task:
            await asyncio.wait_for(stop_task, 3)
        assert quiesced.is_set() and not late.exists()
        await asyncio.sleep(0.05)
        assert not late.exists()
        stored = await _stored(sessions, row)
        if action == "claim_loss":
            assert stored.state == "running" and stored.claim_token == "winner-claim"
        else:
            assert stored.state == "failed" and stored.error_code == "cancelled"
            assert stored.claim_token is stored.lease_expires_at is None
            assert await _run(worker) is None
        assert stored.authority_sha256 is stored.manifest_sha256 is None
        assert worker._active_abort is None
        assert row.id not in worker._build_inputs
    finally:
        if worker._active_abort is not None:
            worker._active_abort.abort()
        release.set()
        await asyncio.wait_for(asyncio.gather(task, *([stop_task] if stop_task else []), return_exceptions=True), 5)


@pytest.mark.asyncio
async def test_preview_waits_without_lease_then_worker_alternates_independent_products(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            catalog = await lifecycle.request_catalog(session, _source())
            preview = await lifecycle.request_default_preview(session, catalog)
            await session.commit()
        async with sessions() as session:
            assert await lifecycle.claim_next_product(session, product="preview", claim_token="too-early") is None
        waiting = await _stored(sessions, preview)
        assert waiting.state == "requested" and waiting.attempt_count == 0
        assert waiting.claim_token is waiting.lease_expires_at is waiting.request_sha256 is None
        calls = []
        def build(row):
            calls.append(row.product)
            return _package(row)
        worker = _worker(sessions, build=build)
        assert await _run(worker) == catalog.id
        assert await _run(worker) == preview.id
        assert await _run(worker) is None
        assert calls == ["catalog", "preview"]
        for row in (catalog, preview):
            stored = await _stored(sessions, row)
            assert stored.state == "ready" and stored.attempt_count == 1
            assert stored.claim_token is stored.lease_expires_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["requested", "running", "ready", "failed"])
async def test_worker_leaves_all_legacy_combined_rows_untouched(tmp_path, state):
    from services.ngs_alignment_presentation import request_presentation
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            row = await request_presentation(
                session, job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5)
            row.state = state
            if state == "running":
                row.claim_token = "legacy-expired-owner"
                row.lease_expires_at = datetime(2020, 1, 1)
                row.attempt_count = 1
            if state == "ready":
                row.authority_sha256, row.manifest_sha256 = "d" * 64, "e" * 64
            await session.commit()
            before = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        worker = _worker(sessions, build=lambda row: pytest.fail("legacy rows must not build"))
        await asyncio.wait_for(worker.start(), 5)
        try:
            assert await _run(worker) is None
        finally:
            await asyncio.wait_for(worker.stop(), 5)
        async with sessions() as session:
            stored = await session.get(type(row), row.id)
            assert {column.name: getattr(stored, column.name) for column in row.__table__.columns} == before
            assert (await session.scalars(select(Product))).all() == []
    finally:
        await engine.dispose()


def test_product_lifecycle_has_no_deadline_or_automatic_retry_schedule_contract():
    from database import NgsAlignmentPresentationJob
    for table in (Product, NgsAlignmentPresentationJob):
        assert "next_retry_at" not in table.__table__.columns
    assert "max_build_seconds" not in inspect.signature(NgsAlignmentPresentationWorker.__init__).parameters


@pytest.mark.asyncio
@pytest.mark.native_http
async def test_worker_preserves_legacy_combined_packages_without_adoption_or_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ngs_resources,
    native_http,
) -> None:
    from tests.test_ngs_alignment_sessions import _write_governed_alignment_fixture
    from services import ngs_alignment_sessions
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    result_root = tmp_path / "result"
    result_root.mkdir()
    source = result_root / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    source_authority = "a" * 64
    source_manifest = "b" * 64
    artifact_set = "c" * 64
    monkeypatch.setattr(
        ngs_alignment_sessions, "_creation_authority", lambda: ("1" * 40, "2" * 40)
    )
    session_id = "1" * 24
    package = ngs_alignment_sessions.build_alignment_presentation(
        source,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=index,
        index_sha256=bai_sha,
        index_size_bytes=bai_size,
        source_manifest_sha256=source_manifest,
        source_alignment_relative_path="source.bam",
        source_index_relative_path="source.bam.bai",
        source_authority_sha256=source_authority,
        job_id="job-1",
        session_id=session_id,
        mode="primary",
        artifact_set_sha256=artifact_set,
        alignment_pair_sha256="d" * 64,
        cache_root=result_root / ".alignment-presentations",
    )
    published = package["manifest_path"].parent
    foreign_package = ngs_alignment_sessions.build_alignment_presentation(
        source,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=index,
        index_sha256=bai_sha,
        index_size_bytes=bai_size,
        source_manifest_sha256=source_manifest,
        source_alignment_relative_path="source.bam",
        source_index_relative_path="source.bam.bai",
        source_authority_sha256="e" * 64,
        job_id="job-foreign",
        session_id=session_id,
        mode="primary",
        artifact_set_sha256=artifact_set,
        alignment_pair_sha256="d" * 64,
        cache_root=result_root / ".alignment-presentations",
    )
    foreign_source = foreign_package["manifest_path"].parent
    foreign_destination = published.parent / foreign_source.name
    foreign_source.rename(foreign_destination)
    invalid_destination = published.parent / ("f" * 64)
    invalid_destination.mkdir()
    (invalid_destination / "manifest.json").write_text("{}", encoding="utf-8")
    before = {
        path.relative_to(published).as_posix(): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.read_bytes(),
        )
        for path in published.iterdir()
        if path.is_file()
    }
    from services.ngs_alignment_presentation_v5 import verify_package_against_source
    verify_package_against_source(package, source)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id=session_id,
                mode="primary",
                source_authority_sha256=source_authority,
                source_manifest_sha256=source_manifest,
                source_artifact_set_sha256=artifact_set,
                policy_version=5,
            )
            await session.commit()

        build_calls = 0

        def reject_rebuild(_request):
            nonlocal build_calls
            build_calls += 1
            raise AssertionError("restart adoption rebuilt an already-published package")

        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=reject_rebuild,
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        worker._build_inputs[request.id] = {
            "result_root": result_root,
            "alignment_path": source,
        }
        assert await asyncio.wait_for(worker.run_once(), 5) is None

        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None and stored.state == "requested"
            assert stored.attempt_count == 0
            assert stored.claim_token is None
            assert stored.authority_sha256 is None
            assert stored.manifest_sha256 is None
        after = {
            path.relative_to(published).as_posix(): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
                path.read_bytes(),
            )
            for path in published.iterdir()
            if path.is_file()
        }
        assert after == before
        assert foreign_destination.is_dir()
        assert invalid_destination.is_dir()
        assert build_calls == 0
    finally:
        await engine.dispose()




@pytest.mark.asyncio
async def test_concurrent_manual_retry_has_one_cas_winner_and_preserves_attempts(product_case):
    sessions, row = product_case
    row = await _claim(sessions, row)
    async with sessions() as session:
        await lifecycle.fail_product(session, row, "claim-1", error_code="publication_failed")
    arrived = 0
    release = asyncio.Event()
    class BarrierSession:
        def __init__(self, real):
            self.real = real
        def __getattr__(self, name):
            return getattr(self.real, name)
        async def execute(self, statement, *args, **kwargs):
            nonlocal arrived
            if statement.__class__.__name__ == "Update":
                arrived += 1
                if arrived == 2:
                    release.set()
                await asyncio.wait_for(release.wait(), 2)
            return await self.real.execute(statement, *args, **kwargs)
    async def retry(session):
        current = await session.get(Product, row.id)
        return await lifecycle.retry_product(BarrierSession(session), current,
            current_source_authority_sha256=row.source_authority_sha256)
    async with sessions() as first, sessions() as second:
        results = await asyncio.wait_for(asyncio.gather(retry(first), retry(second), return_exceptions=True), 5)
    assert sum(isinstance(result, Product) for result in results) == 1
    assert sum(isinstance(result, PresentationClaimLost) for result in results) == 1
    stored = await _stored(sessions, row)
    assert stored.state == "requested"
    assert stored.manual_retry_count == 1 and stored.attempt_count == 1
    assert stored.claim_token is stored.lease_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["requested", "running"])
async def test_retry_replay_preserves_active_row_but_still_requires_current_source(product_case, state):
    sessions, row = product_case
    if state == "running":
        row = await _claim(sessions, row)
    async with sessions() as session:
        current = await session.get(Product, row.id)
        with pytest.raises(PresentationSourceStale):
            await lifecycle.retry_product(session, current, current_source_authority_sha256="0" * 64)
        replay = await lifecycle.retry_product(session, current,
            current_source_authority_sha256=row.source_authority_sha256)
        assert replay.state == state and replay.manual_retry_count == 0
        assert replay.attempt_count == (1 if state == "running" else 0)


@pytest.mark.asyncio
@pytest.mark.native_http
@pytest.mark.parametrize("kind", ["catalog", "preview"])
async def test_explicit_retry_semantically_adopts_sealed_product_without_rewriting(
    tmp_path, monkeypatch, native_http, ngs_resources, kind,
):
    import hashlib
    import pysam
    from services import ngs_alignment_product_builder as builder
    from services.verified_native_reads import run_in_threadpool

    root = ngs_resources / "job-1"
    root.mkdir()
    bam = root / "source.bam"
    with pysam.AlignmentFile(bam, "wb", header={"HD": {"VN": "1.6", "SO": "coordinate"},
                                              "SQ": [{"SN": "ref", "LN": 1000}]}) as output:
        record = pysam.AlignedSegment(output.header)
        record.query_name, record.query_sequence = "literal/read", "ACGT"
        record.flag, record.reference_id, record.reference_start = 0, 0, 2
        record.mapping_quality, record.cigarstring = 60, "4M"
        record.query_qualities = pysam.qualitystring_to_array("IIII")
        output.write(record)
    pysam.index(str(bam))
    bai = root / "source.bam.bai"
    source = _source()
    for path, prefix in ((bam, "alignment"), (bai, "alignment_index")):
        source[prefix + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        source[prefix + "_size_bytes"] = path.stat().st_size
    inputs = {"alignment_path": bam, "index_path": bai, "result_root": root}
    monkeypatch.setattr(builder.storage, "_creation_authority", lambda: ("1" * 40, "2" * 40))
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            catalog = await lifecycle.request_catalog(session, source)
            await session.commit()
        row = await _claim(sessions, catalog)
        package = await asyncio.wait_for(run_in_threadpool(builder.build_product, row, inputs, lambda: None), 20)
        if kind == "preview":
            async with sessions() as session:
                await lifecycle.publish_product(session, row, "claim-1", **package)
            async with sessions() as session:
                catalog = await session.get(Product, catalog.id)
                preview = await lifecycle.request_default_preview(session, catalog)
                await session.commit()
            row = await _claim(sessions, preview)
            package = await asyncio.wait_for(run_in_threadpool(builder.build_product, row, inputs,
                lambda: None, catalog_request=catalog), 20)
        sealed = root / ".alignment-products" / row.id / "sealed"
        def preimage():
            return {path.name: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
                    for path in sealed.iterdir()}
        before = preimage()
        # Simulate a crash after atomic rename but before DB publication.
        async with sessions() as session:
            await session.execute(update(Product).where(Product.id == row.id).values(
                lease_expires_at=datetime(2020, 1, 1)))
            await session.commit()
        worker = _worker(sessions)
        assert await _run(worker) is None
        stored = await _stored(sessions, row)
        assert stored.state == "failed" and stored.error_code == "infrastructure_failed"
        assert stored.authority_sha256 is stored.manifest_sha256 is None
        assert preimage() == before
        async with sessions() as session:
            current = await session.get(Product, row.id)
            await lifecycle.retry_product(session, current,
                current_source_authority_sha256=row.source_authority_sha256)
        worker._build_inputs[row.id] = inputs
        # The default production builder rechecks source hashes, contract and
        # semantic rows before adoption. No scientific validators are replaced.
        assert await asyncio.wait_for(worker.run_once(), 20) == row.id
        stored = await _stored(sessions, row)
        assert stored.state == "ready", stored.error_code
        assert stored.attempt_count == 2 and stored.manual_retry_count == 1
        assert stored.authority_sha256 == package["authority_sha256"]
        assert stored.manifest_sha256 == package["manifest_sha256"]
        assert preimage() == before
        assert not list(sealed.parent.glob(".attempt-*"))
        assert hashlib.sha256(bam.read_bytes()).hexdigest() == source["alignment_sha256"]
    finally:
        await engine.dispose()

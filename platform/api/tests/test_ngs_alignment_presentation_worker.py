from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job


async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'presentation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _job(job_id: str = "job-1") -> Job:
    return Job(
        id=job_id,
        name="NGS",
        model_id="nanopore",
        mode="analysis",
        params={},
        status="completed",
        queue_status="completed",
        provenance={"result_integrity": {"state": "validated"}},
    )


@pytest.mark.asyncio
async def test_requested_presentation_claim_is_atomic_and_increments_attempt(tmp_path: Path) -> None:
    from database import NgsAlignmentPresentationJob
    from services.ngs_alignment_presentation import claim_next_presentation, request_presentation

    engine, sessions = await _database(tmp_path)
    now = datetime(2026, 9, 1, 12, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        async with sessions() as first:
            claimed = await claim_next_presentation(
                first, now=now, lease_seconds=30, claim_token="claim-1"
            )
        async with sessions() as second:
            lost = await claim_next_presentation(
                second, now=now, lease_seconds=30, claim_token="claim-2"
            )
            stored = (
                await second.execute(
                    select(NgsAlignmentPresentationJob).where(
                        NgsAlignmentPresentationJob.id == request.id
                    )
                )
            ).scalar_one()

        assert claimed is not None
        assert claimed.id == request.id
        assert claimed.state == "running"
        assert claimed.claim_token == "claim-1"
        assert claimed.attempt_count == 1
        assert claimed.lease_expires_at == now + timedelta(seconds=30)
        assert lost is None
        assert stored.state == "running"
        assert stored.claim_token == "claim-1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lease_renewal_and_ready_publication_require_live_claim_cas(tmp_path: Path) -> None:
    from database import NgsAlignmentPresentationJob
    from services.ngs_alignment_presentation import (
        PresentationClaimLost,
        claim_next_presentation,
        mark_presentation_ready,
        renew_presentation_lease,
        request_presentation,
    )

    engine, sessions = await _database(tmp_path)
    now = datetime(2026, 9, 1, 12, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(
                session, now=now, lease_seconds=30, claim_token="claim-1"
            )
        async with sessions() as session:
            renewed = await renew_presentation_lease(
                session,
                request.id,
                "claim-1",
                now=now + timedelta(seconds=10),
                lease_seconds=40,
            )
            assert renewed == now + timedelta(seconds=50)
        async with sessions() as session:
            with pytest.raises(PresentationClaimLost):
                await mark_presentation_ready(
                    session,
                    request.id,
                    "losing-claim",
                    source_authority_sha256="a" * 64,
                    authority_sha256="d" * 64,
                    manifest_sha256="e" * 64,
                    now=now + timedelta(seconds=20),
                )
        async with sessions() as session:
            ready = await mark_presentation_ready(
                session,
                request.id,
                "claim-1",
                source_authority_sha256="a" * 64,
                authority_sha256="d" * 64,
                manifest_sha256="e" * 64,
                now=now + timedelta(seconds=20),
            )
            assert ready.state == "ready"
            assert ready.claim_token is None
            assert ready.lease_expires_at is None
            assert ready.authority_sha256 == "d" * 64
        async with sessions() as session:
            stored = await session.get(NgsAlignmentPresentationJob, request.id)
            assert stored is not None and stored.manifest_sha256 == "e" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_running_claim_fails_terminally_with_cas(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        recover_expired_presentations,
        request_presentation,
    )

    engine, sessions = await _database(tmp_path)
    now = datetime(2026, 9, 1, 12, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(
                session, now=now, lease_seconds=10, claim_token="claim-1"
            )
        async with sessions() as session:
            assert await recover_expired_presentations(
                session, now=now + timedelta(seconds=11)
            ) == 1
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "failed"
            assert stored.attempt_count == 1
            assert stored.claim_token is None
            assert stored.lease_expires_at is None
            assert stored.error_code == "infrastructure_failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_claim_is_terminal_on_first_attempt_and_not_claimed_again(
    tmp_path: Path,
) -> None:
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        recover_expired_presentations,
        request_presentation,
    )

    engine, sessions = await _database(tmp_path)
    now = datetime(2026, 9, 1, 12, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        async with sessions() as session:
            claimed = await claim_next_presentation(
                session, now=now, lease_seconds=1, claim_token="claim-1"
            )
            assert claimed is not None and claimed.attempt_count == 1
        async with sessions() as session:
            assert await recover_expired_presentations(
                session, now=now + timedelta(seconds=2)
            ) == 1
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            another = await claim_next_presentation(
                session, now=now + timedelta(minutes=10), claim_token="claim-2"
            )
            assert stored is not None
            assert stored.state == "failed"
            assert stored.attempt_count == 1
            assert stored.error_code == "infrastructure_failed"
            assert another is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_infrastructure_failure_retries_twice_then_fails_terminally(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        record_presentation_failure,
        request_presentation,
    )

    engine, sessions = await _database(tmp_path)
    now = datetime(2026, 9, 1, 12, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        for attempt in range(1, 4):
            attempt_now = now + timedelta(minutes=attempt)
            async with sessions() as session:
                claimed = await claim_next_presentation(
                    session,
                    now=attempt_now,
                    lease_seconds=30,
                    claim_token=f"claim-{attempt}",
                )
                assert claimed is not None
            async with sessions() as session:
                failed = await record_presentation_failure(
                    session,
                    request.id,
                    f"claim-{attempt}",
                    error_code="infrastructure_failed",
                    retryable=True,
                    now=attempt_now + timedelta(seconds=1),
                    retry_delay_seconds=10,
                )
                assert failed.attempt_count == attempt
                if attempt < 3:
                    assert failed.state == "requested"
                    assert failed.next_retry_at == attempt_now + timedelta(seconds=11)
                else:
                    assert failed.state == "failed"
                    assert failed.next_retry_at is None
                    assert failed.error_code == "infrastructure_failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_refuses_stale_source_without_changing_scientific_job(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    build_calls = 0

    def build(_request):
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("stale source must not be built")

    async def current_source(_request):
        return "f" * 64

    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=build,
            resolve_source_authority=current_source,
            poll_interval=0.05,
        )
        assert await worker.run_once() == request.id

        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            job = await session.get(Job, "job-1")
            assert stored is not None and stored.state == "failed"
            assert stored.error_code == "source_invalid"
            assert job is not None
            assert (job.status, job.queue_status) == ("completed", "completed")
            assert job.provenance == {"result_integrity": {"state": "validated"}}
        assert build_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_resolver_exception_releases_claim_through_retry_budget(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session, job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda _row: pytest.fail("resolver failure must prevent build"),
            resolve_source_authority=lambda _row: (_ for _ in ()).throw(OSError("resolver failed")),
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "requested"
            assert stored.attempt_count == 1
            assert stored.claim_token is None
            assert stored.lease_expires_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_poll_iteration_recovers_expired_claim_without_service_restart(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import claim_next_presentation, request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    old = datetime(2020, 1, 1)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session, job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(
                session, now=old, lease_seconds=1, claim_token="abandoned-claim"
            )
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda row: {
                "source_authority_sha256": row.source_authority_sha256,
                "authority_sha256": "d" * 64,
                "manifest_sha256": "e" * 64,
            },
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None and stored.state == "ready"
            assert stored.attempt_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deferred_adoption_renews_lease_before_build(tmp_path: Path) -> None:
    import asyncio
    import threading

    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session, job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda row: {
                "source_authority_sha256": row.source_authority_sha256,
                "authority_sha256": "d" * 64,
                "manifest_sha256": "e" * 64,
            },
            resolve_source_authority=lambda row: row.source_authority_sha256,
            lease_seconds=1,
        )

        def delayed_adoption(_request, abort):
            entered.set()
            while not release.wait(0.01):
                abort.checkpoint()
            return None

        worker._adopt_current_presentation = delayed_adoption  # type: ignore[method-assign]
        task = asyncio.create_task(worker.run_once())
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0.45)
        async with sessions() as session:
            running = await session.get(type(request), request.id)
            assert running is not None
            assert running.lease_expires_at is not None
            assert running.lease_expires_at > datetime.utcnow() + timedelta(seconds=0.3)
        release.set()
        assert await task == request.id
    finally:
        release.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_renews_lease_during_threadpool_build_and_publishes_ready(tmp_path: Path) -> None:
    import time

    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)

    def build(request):
        time.sleep(1.2)
        return {
            "source_authority_sha256": request.source_authority_sha256,
            "authority_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
        }

    async def current_source(request):
        return request.source_authority_sha256

    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=build,
            resolve_source_authority=current_source,
            lease_seconds=1,
            poll_interval=0.05,
        )
        assert await worker.run_once() == request.id

        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "ready"
            assert stored.attempt_count == 1
            assert stored.authority_sha256 == "d" * 64
            assert stored.manifest_sha256 == "e" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_claim_loss_never_publishes_package_authority(tmp_path: Path) -> None:
    from sqlalchemy import update

    from database import NgsAlignmentPresentationJob
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    resolves = 0

    def build(request):
        return {
            "source_authority_sha256": request.source_authority_sha256,
            "authority_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
        }

    async def current_source(request):
        nonlocal resolves
        resolves += 1
        if resolves == 2:
            async with sessions() as session:
                await session.execute(
                    update(NgsAlignmentPresentationJob)
                    .where(NgsAlignmentPresentationJob.id == request.id)
                    .values(claim_token="winner-claim")
                )
                await session.commit()
        return request.source_authority_sha256

    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions, build=build, resolve_source_authority=current_source
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(NgsAlignmentPresentationJob, request.id)
            assert stored is not None
            assert stored.state == "running"
            assert stored.claim_token == "winner-claim"
            assert stored.authority_sha256 is None
            assert stored.manifest_sha256 is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_manual_retry_resets_failed_request_once_for_same_source(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        record_presentation_failure,
        request_presentation,
        retry_failed_presentation,
    )

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(session, claim_token="claim-1")
        async with sessions() as session:
            await record_presentation_failure(
                session,
                request.id,
                "claim-1",
                error_code="integrity_mismatch",
            )
        async with sessions() as session:
            retried = await retry_failed_presentation(
                session,
                request.id,
                current_source_authority_sha256="a" * 64,
            )
            assert retried.state == "requested"
            assert retried.attempt_count == 0
            assert retried.manual_retry_count == 1
            assert retried.error_code is None
        async with sessions() as session:
            replay = await retry_failed_presentation(
                session,
                request.id,
                current_source_authority_sha256="a" * 64,
            )
            assert replay.state == "requested"
            assert replay.manual_retry_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_manual_retry_replays_the_winning_requested_row(tmp_path: Path) -> None:
    import asyncio

    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        record_presentation_failure,
        request_presentation,
        retry_failed_presentation,
    )

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session, job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(session, claim_token="claim-1")
        async with sessions() as session:
            await record_presentation_failure(
                session, request.id, "claim-1", error_code="source_invalid"
            )

        arrived = 0
        release = asyncio.Event()
        lock = asyncio.Lock()

        class BarrierSession:
            def __init__(self, real):
                self.real = real
                self.waited = False

            async def get(self, *args, **kwargs):
                return await self.real.get(*args, **kwargs)

            async def execute(self, statement, *args, **kwargs):
                nonlocal arrived
                if not self.waited and statement.__class__.__name__ == "Update":
                    self.waited = True
                    async with lock:
                        arrived += 1
                        if arrived == 2:
                            release.set()
                    await release.wait()
                return await self.real.execute(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.real, name)

        async with sessions() as first, sessions() as second:
            results = await asyncio.gather(
                retry_failed_presentation(
                    BarrierSession(first), request.id,
                    current_source_authority_sha256="a" * 64,
                ),
                retry_failed_presentation(
                    BarrierSession(second), request.id,
                    current_source_authority_sha256="a" * 64,
                ),
            )
        assert [row.state for row in results] == ["requested", "requested"]
        assert [row.manual_retry_count for row in results] == [1, 1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["requested", "running"])
async def test_retry_replay_returns_active_state_without_comparing_current_source(
    tmp_path: Path,
    state: str,
) -> None:
    from database import NgsAlignmentPresentationJob
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        request_presentation,
        retry_failed_presentation,
    )

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        if state == "running":
            async with sessions() as session:
                await claim_next_presentation(session, claim_token="claim-1")

        async with sessions() as session:
            replay = await retry_failed_presentation(
                session,
                request.id,
                current_source_authority_sha256="f" * 64,
            )
            assert replay.state == state
            assert replay.manual_retry_count == 0
        async with sessions() as session:
            stored = await session.get(NgsAlignmentPresentationJob, request.id)
            assert stored is not None and stored.state == state
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable", "expected_state", "expected_error"),
    [
        ("source_invalid", False, "failed", "source_invalid"),
        ("resource_limit", False, "failed", "resource_limit"),
        ("build_timeout", False, "failed", "build_timeout"),
        ("publication_failed", True, "requested", None),
        ("integrity_mismatch", False, "failed", "integrity_mismatch"),
        ("infrastructure_failed", True, "requested", None),
    ],
)
async def test_worker_uses_closed_typed_failure_taxonomy_and_retry_policy(
    tmp_path: Path,
    code: str,
    retryable: bool,
    expected_state: str,
    expected_error: str | None,
) -> None:
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import (
        NgsAlignmentPresentationWorker,
        PresentationBuildFailure,
    )

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()

        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda _row: (_ for _ in ()).throw(
                PresentationBuildFailure(code, retryable=retryable)
            ),
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == expected_state
            assert stored.error_code == expected_error
            assert stored.attempt_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_publication_failure_retries_until_third_attempt_then_terminalizes(
    tmp_path: Path,
) -> None:
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import (
        NgsAlignmentPresentationWorker,
        PresentationBuildFailure,
    )

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda _row: (_ for _ in ()).throw(
                PresentationBuildFailure("publication_failed", retryable=True)
            ),
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        for attempt in range(1, 4):
            assert await worker.run_once() == request.id
            async with sessions() as session:
                stored = await session.get(type(request), request.id)
                assert stored is not None and stored.attempt_count == attempt
                if attempt < 3:
                    assert stored.state == "requested"
                    stored.next_retry_at = None
                    await session.commit()
                else:
                    assert stored.state == "failed"
                    assert stored.error_code == "publication_failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_marks_invalid_package_digests_as_terminal_integrity_failure(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda row: {
                "source_authority_sha256": row.source_authority_sha256,
                "authority_sha256": "not-a-digest",
                "manifest_sha256": "e" * 64,
            },
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "failed"
            assert stored.error_code == "integrity_mismatch"
            assert stored.authority_sha256 is None
            assert stored.manifest_sha256 is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restart_recovery_reuses_already_published_package_then_marks_ready(tmp_path: Path) -> None:
    from services.ngs_alignment_presentation import (
        claim_next_presentation,
        recover_expired_presentations,
        request_presentation,
    )
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    old = datetime(2020, 1, 1, 0, 0, 0)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        async with sessions() as session:
            await claim_next_presentation(
                session, now=old, lease_seconds=1, claim_token="crashed-claim"
            )
        async with sessions() as session:
            assert await recover_expired_presentations(
                session, now=old + timedelta(seconds=2)
            ) == 1

        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda row: {
                "source_authority_sha256": row.source_authority_sha256,
                "authority_sha256": "d" * 64,
                "manifest_sha256": "e" * 64,
            },
            resolve_source_authority=lambda row: row.source_authority_sha256,
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "ready"
            assert stored.attempt_count == 2
            assert stored.authority_sha256 == "d" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restart_adopts_atomically_renamed_valid_package_without_rewrite_or_reindex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        assert await worker.run_once() == request.id

        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None and stored.state == "ready"
            assert stored.authority_sha256 == package["manifest"]["authority_sha256"]
            assert stored.manifest_sha256 == package["manifest_metadata"]["sha256"]
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
async def test_worker_build_timeout_is_terminal_and_never_publishes_ready(tmp_path: Path) -> None:
    import time

    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1",
                session_id="session-1",
                mode="primary",
                source_authority_sha256="a" * 64,
                source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64,
                policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=lambda row: time.sleep(0.2) or {
                "source_authority_sha256": row.source_authority_sha256,
                "authority_sha256": "d" * 64,
                "manifest_sha256": "e" * 64,
            },
            resolve_source_authority=lambda row: row.source_authority_sha256,
            max_build_seconds=0.05,
        )
        assert await worker.run_once() == request.id
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None
            assert stored.state == "failed"
            assert stored.error_code == "build_timeout"
            assert stored.authority_sha256 is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stop_waits_for_cooperative_builder_quiescence_and_prevents_late_writes(
    tmp_path: Path,
) -> None:
    import asyncio
    import threading
    import time

    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    started = threading.Event()
    stopped = threading.Event()
    late = tmp_path / "late-stop-write"

    def build(_row, abort):
        started.set()
        try:
            while not abort.is_set():
                time.sleep(0.005)
            abort.checkpoint()
            late.write_text("late", encoding="utf-8")
        finally:
            stopped.set()

    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            await request_presentation(
                session,
                job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=build,
            resolve_source_authority=lambda row: row.source_authority_sha256,
            poll_interval=0.01,
        )
        await worker.start()
        assert await asyncio.to_thread(started.wait, 2)
        await worker.stop()
        assert stopped.is_set()
        assert not late.exists()
        await asyncio.sleep(0.05)
        assert not late.exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_timeout_aborts_and_joins_builder_before_terminalizing(
    tmp_path: Path,
) -> None:
    import threading
    import time

    from services.ngs_alignment_presentation import request_presentation
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    engine, sessions = await _database(tmp_path)
    stopped = threading.Event()
    late = tmp_path / "late-timeout-write"

    def build(_row, abort):
        try:
            while not abort.is_set():
                time.sleep(0.005)
            time.sleep(0.02)
            abort.checkpoint()
            late.write_text("late", encoding="utf-8")
        finally:
            stopped.set()

    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = NgsAlignmentPresentationWorker(
            sessions,
            build=build,
            resolve_source_authority=lambda row: row.source_authority_sha256,
            max_build_seconds=0.03,
        )
        assert await worker.run_once() == request.id
        assert stopped.is_set()
        assert not late.exists()
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None and stored.state == "failed"
            assert stored.error_code == "build_timeout"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lease_claim_loss_aborts_and_joins_builder_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading
    import time

    from services.ngs_alignment_presentation import PresentationClaimLost, request_presentation
    from services import ngs_alignment_presentation_worker as worker_module

    engine, sessions = await _database(tmp_path)
    started = threading.Event()
    quiesced = threading.Event()
    post_loss_write = tmp_path / "post-loss-write"

    def build(_row, abort):
        started.set()
        try:
            while not abort.is_set():
                time.sleep(0.005)
            time.sleep(0.02)
            abort.checkpoint()
            post_loss_write.write_text("forbidden", encoding="utf-8")
        finally:
            quiesced.set()

    async def lose_claim(*_args, **_kwargs):
        assert started.is_set()
        raise PresentationClaimLost("forced lease loss")

    monkeypatch.setattr(worker_module, "renew_presentation_lease", lose_claim)
    try:
        async with sessions() as session:
            session.add(_job())
            await session.flush()
            request = await request_presentation(
                session,
                job_id="job-1", session_id="session-1", mode="primary",
                source_authority_sha256="a" * 64, source_manifest_sha256="b" * 64,
                source_artifact_set_sha256="c" * 64, policy_version=5,
            )
            await session.commit()
        worker = worker_module.NgsAlignmentPresentationWorker(
            sessions,
            build=build,
            resolve_source_authority=lambda row: row.source_authority_sha256,
            lease_seconds=1,
        )
        assert await asyncio.wait_for(worker.run_once(), timeout=2) == request.id
        assert quiesced.is_set()
        assert not post_loss_write.exists()
        async with sessions() as session:
            stored = await session.get(type(request), request.id)
            assert stored is not None and stored.state == "running"
            assert stored.authority_sha256 is None
            assert stored.manifest_sha256 is None
    finally:
        await engine.dispose()


def test_v5_lifecycle_has_no_deadline_or_retry_schedule_contract() -> None:
    import inspect

    from database import NgsAlignmentPresentationJob
    from services.ngs_alignment_presentation_worker import NgsAlignmentPresentationWorker

    assert "next_retry_at" not in NgsAlignmentPresentationJob.__table__.columns
    assert "max_build_seconds" not in inspect.signature(
        NgsAlignmentPresentationWorker.__init__
    ).parameters

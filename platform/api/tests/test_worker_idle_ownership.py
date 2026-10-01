"""Real scratch SQLite regressions for read probes and owned statistics threads."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (Base, FrustraMPNNStatisticsAnalysis, Job, MdAttemptSegment,
                      MdReconcilerLease, MdRun)
from services.frustrampnn import statistics_jobs as statistics
from services.md import reconcile as md
from services.md.state import create_md_run, create_replica_attempt
from test_frustrampnn_persistence import _v3_bundle, _seed_v3_child_job, _persistence
from test_md_reconcile import _request


@pytest_asyncio.fixture
async def store(tmp_path):
    path = tmp_path / "idle.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 0.2})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute",
                 lambda conn, cursor, statement, parameters, context, many: statements.append(statement))
    try:
        yield path, async_sessionmaker(engine, expire_on_commit=False), statements
    finally:
        await engine.dispose()


def _dml(statements):
    return [sql for sql in statements if sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}]


async def _seed_statistics(sessions, tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _, terminal = _v3_bundle(root, monkeypatch)
    await _seed_v3_child_job(sessions, root)
    async with sessions() as session:
        await _persistence().ingest_result_bundle(session, root, parent_job_id="job-v2", terminal_envelope=terminal)
        child = (await session.scalars(select(FrustraMPNNStatisticsAnalysis))).one()
        await session.commit()
        return child.analysis_id


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_kind", ["statistics", "md"])
@pytest.mark.parametrize("retained", [False, True])
async def test_empty_polls_emit_no_dml_under_other_writer(store, worker_kind, retained):
    path, sessions, statements = store
    if retained:
        async with sessions() as session:
            if worker_kind == "md":
                parent = Job(id="settled", name="settled", model_id="md", mode="molecular_dynamics", params={}, status="completed")
                session.add(parent)
                await session.flush()
                run = await create_md_run(session, job=parent, normalized_request=_request())
                run.phase = "completed"
                assert await md.acquire_reconciler_lease(session, owner_id="previous-owner")
            else:
                # A live claim is not recovery work (no inference/artifacts needed).
                session.add(FrustraMPNNStatisticsAnalysis(
                    analysis_id="live", parent_job_id="parent", invocation_id="invoke",
                    core_artifact_id="artifact", core_bundle_relative_path="bundle",
                    core_landscape_sha256="a" * 64, core_manifest_sha256="b" * 64,
                    state="running", claim_token="live-token", claim_owner="other-owner",
                    lease_expires_at=datetime.utcnow() + timedelta(minutes=10),
                    formula_version=statistics.FORMULA_VERSION, policy_version=statistics.POLICY_VERSION,
                    package_version=statistics.PACKAGE_VERSION, schema_version=1))
            await session.commit()
    worker = statistics.FrustraMPNNStatisticsWorker(sessions) if worker_kind == "statistics" else md.MdReconcilerWorker(sessions, owner_id="idle-owner")
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        statements.clear()
        for _ in range(3):
            if worker_kind == "statistics":
                assert await asyncio.wait_for(worker.run_pending_once(), 1) is None
            else:
                receipt = await asyncio.wait_for(worker.run_once(), 1)
                assert receipt["changes"] == [] and receipt["applied"]
        assert _dml(statements) == []
        assert writer.in_transaction
        writer.rollback()
    if retained and worker_kind == "md":
        async with sessions() as session:
            assert (await session.get(MdReconcilerLease, "md-lifecycle")).owner_id == "previous-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_statistics_arrival_and_crash_recovery_after_empty_poll(store, tmp_path, monkeypatch, expired):
    _, sessions, _ = store
    worker = statistics.FrustraMPNNStatisticsWorker(sessions)
    assert await worker.run_pending_once() is None
    identity = await _seed_statistics(sessions, tmp_path, monkeypatch)
    if expired:
        async with sessions() as session:
            child = await statistics.claim_statistics_child(session, analysis_id=identity, claim_owner="dead-owner")
            child.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
            await session.commit()
    assert await worker.run_pending_once() == identity
    async with sessions() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, identity)
        assert child.state == "completed"
        assert child.attempt_count == (2 if expired else 1)
    assert await worker.run_pending_once() is None


@pytest.mark.asyncio
async def test_recovery_probe_rechecks_renewed_lease(store, tmp_path, monkeypatch):
    _, sessions, _ = store
    identity = await _seed_statistics(sessions, tmp_path, monkeypatch)
    now = datetime.utcnow()
    async with sessions() as session:
        child = await statistics.claim_statistics_child(session, analysis_id=identity, claim_owner="live-owner")
        original_expiry = child.lease_expires_at
        token = child.claim_token
        await session.commit()
    async with sessions() as recovery:
        scalar = recovery.scalar
        async def probe_then_renew(statement, *args, **kwargs):
            found = await scalar(statement, *args, **kwargs)
            assert found == identity
            async with sessions() as heartbeat:
                assert await statistics.heartbeat_statistics_child(heartbeat, analysis_id=identity, claim_token=token, lease_seconds=300)
                await heartbeat.commit()
            return found
        monkeypatch.setattr(recovery, "scalar", probe_then_renew)
        assert await statistics.recover_abandoned_statistics_claims(recovery, stale_before=original_expiry) == 0
        await recovery.commit()
    async with sessions() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, identity)
        assert child.state == "running" and child.claim_token == token
        assert child.lease_expires_at > now


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_run", [False, True])
async def test_md_arrival_and_terminal_segment_recovery_preserve_lease(store, terminal_run):
    _, sessions, _ = store
    worker = md.MdReconcilerWorker(sessions, owner_id="new-owner")
    assert (await worker.run_once())["change_count"] == 0
    async with sessions() as session:
        parent = Job(id="parent", name="MD", model_id="md", mode="molecular_dynamics", params={}, status="completed")
        child = Job(id="child", name="replica", model_id="md", mode="molecular_dynamics", params={}, status="completed")
        session.add_all([parent, child])
        await session.flush()
        run = await create_md_run(session, job=parent, normalized_request=_request())
        run.phase = "replicas_running"
        replica, segment = await create_replica_attempt(session, job_id=parent.id, replica_index=0, attempt=0,
            engine="gromacs", execution_plan_sha256="b" * 64, compatibility_key="c" * 64, child_job_id=child.id)
        segment_id = segment.id
        if terminal_run:
            run.phase = "completed"
        assert await md.acquire_reconciler_lease(session, owner_id="old-owner")
        await session.commit()
    with pytest.raises(RuntimeError, match="MD_RECONCILER_LEASE_UNAVAILABLE"):
        await worker.run_once()
    async with sessions() as session:
        lease = await session.get(MdReconcilerLease, "md-lifecycle")
        lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    receipt = await worker.run_once()
    assert any(change["kind"] == "segment_state" for change in receipt["changes"])
    async with sessions() as session:
        assert (await session.get(MdAttemptSegment, segment_id)).state == "completed"
        assert (await session.get(MdRun, "parent")).phase == "completed"
        assert (await session.get(MdReconcilerLease, "md-lifecycle")).owner_id == "new-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["bundle", "analytics"])
@pytest.mark.parametrize("late_failure", [False, True])
async def test_statistics_thread_keeps_heartbeat_and_session_until_repeated_cancel_join(
    store, tmp_path, monkeypatch, boundary, late_failure,
):
    _, sessions, _ = store
    identity = await _seed_statistics(sessions, tmp_path, monkeypatch)
    from services.frustrampnn import persistence, analytics
    module, name = (persistence, "load_and_validate_result_bundle") if boundary == "bundle" else (analytics, "build_statistics_receipt")
    original = getattr(module, name)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    def blocked(*args, **kwargs):
        assert threading.get_ident() != main_thread
        started.set()
        try:
            assert release.wait(10), "test did not release thread"
            if late_failure:
                raise ValueError("late reader failure")
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(module, name, blocked)
    heartbeat_observed = asyncio.Event()
    heartbeat = statistics.heartbeat_statistics_child
    async def observed_heartbeat(*args, **kwargs):
        result = await heartbeat(*args, **kwargs)
        heartbeat_observed.set()
        return result
    monkeypatch.setattr(statistics, "heartbeat_statistics_child", observed_heartbeat)
    session_scopes = []
    class OwnedSession:
        def __init__(self):
            self.session = sessions()
            session_scopes.append(self)
            self.closed = False
        async def __aenter__(self):
            return await self.session.__aenter__()
        async def __aexit__(self, *exc):
            if len(session_scopes) > 1 and self is session_scopes[1]:  # computation, not claim/heartbeat
                assert finished.is_set()
            self.closed = True
            return await self.session.__aexit__(*exc)
    worker = statistics.FrustraMPNNStatisticsWorker(OwnedSession, lease_seconds=3)
    task = asyncio.create_task(worker.run_pending_once())
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.wait_for(heartbeat_observed.wait(), 3)
        assert not task.done() and not finished.is_set()
        assert not session_scopes[1].closed
        async with sessions() as session:
            child = await session.get(FrustraMPNNStatisticsAnalysis, identity)
            assert child.state == "running" and child.lease_expires_at > datetime.utcnow()
        task.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set() and session_scopes[1].closed
    async with sessions() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, identity)
        assert child.state == "running"  # Cancellation remains recoverable by lease expiry.
        assert child.statistics_sha256 is None


@pytest.mark.asyncio
async def test_threaded_statistics_equal_direct_scientific_output(store, tmp_path, monkeypatch, record_property):
    _, sessions, _ = store
    identity = await _seed_statistics(sessions, tmp_path, monkeypatch)
    from services.frustrampnn import analytics
    from services.frustrampnn.contracts import canonical_sha256
    original = analytics.build_statistics_receipt
    inputs = {}
    def capture(**kwargs):
        inputs.update(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(analytics, "build_statistics_receipt", capture)
    async with sessions() as session:
        child = await statistics.claim_statistics_child(session, analysis_id=identity, claim_owner="equivalence")
        token = child.claim_token
        await session.commit()
        actual = await statistics.run_statistics_child_once(session, analysis_id=identity, claim_token=token)
        await session.commit()
    assert actual == original(**inputs)
    record_property("statistics_canonical_sha256", canonical_sha256(actual))


@pytest.mark.asyncio
async def test_statistics_workers_racing_after_read_probe_keep_one_claim(store, tmp_path, monkeypatch):
    _, sessions, _ = store
    identity = await _seed_statistics(sessions, tmp_path, monkeypatch)
    claim = statistics.claim_statistics_child
    both_selected = asyncio.Event()
    selections = []
    async def race_claim(session, **kwargs):
        selections.append(kwargs["analysis_id"])
        if len(selections) == 2:
            both_selected.set()
        await asyncio.wait_for(both_selected.wait(), 5)
        return await claim(session, **kwargs)
    monkeypatch.setattr(statistics, "claim_statistics_child", race_claim)
    workers = [statistics.FrustraMPNNStatisticsWorker(sessions) for _ in range(2)]
    outcomes = await asyncio.gather(*(worker.run_pending_once() for worker in workers), return_exceptions=True)
    assert selections == [identity, identity]
    assert outcomes.count(identity) == 1
    loser = next(value for value in outcomes if value != identity)
    assert isinstance(loser, statistics.FrustraMPNNStatisticsJobError)
    assert str(loser) == "only queued statistics children can run"
    async with sessions() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, identity)
        assert child.state == "completed" and child.attempt_count == 1

"""Interrupted remote attempts recover to a terminal, explained state (BMS-DEV-56).

These cases run offline: the worker is a mocked transport, and the assertions
are about durable BMS state — the Job's own deadline/evidence record, its
terminal reason, and the target lease.
"""
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, ExecutionTarget, Job
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteAttemptStatus


def _iso(value):
    return value.isoformat() + "Z"


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ex, "async_session", factory)
    monkeypatch.setattr(ex, "get_data_root", lambda: tmp_path)
    monkeypatch.setenv("BMS_REMOTE_ATTEMPT_PROGRESS_DEADLINE_SECONDS", "60")
    monkeypatch.setenv("BMS_REMOTE_ATTEMPT_STALL_CONFIRMATION_SECONDS", "60")
    monkeypatch.delenv("BMS_REMOTE_STAGING_RECOVERY_GRACE_SECONDS", raising=False)
    async with factory() as session:
        session.add(Job(
            id="job", name="job", model_id="boltz2", mode="predict", params={},
            status="queued", queue_status="preparing", execution_target_id="target",
            execution_source_revision="a" * 40, execution_source_tree="b" * 40,
            execution_bundle_sha256="c" * 64, nextflow_run_id="remote:attempt",
            remote_attempt_id="attempt", remote_state="staging",
        ))
        session.add(ExecutionTarget(
            id="target", provider="vast", provider_instance_id="1",
            leased_job_id="job", lease_acquired_at=datetime.utcnow(),
        ))
        await session.commit()
    yield factory
    await engine.dispose()


def staging_claim(session_factory, claimed_at, *, progress_updated_at=None, progress_phase="transferring"):
    """Persist one staging attempt whose durable progress anchor is known."""
    async def apply():
        async with session_factory() as session:
            job = await session.get(Job, "job")
            job.provenance = {"remote_execution_assignment": {
                "claimed_at": _iso(claimed_at), "lease_id": "lease", "root_job_id": "job",
            }}
            await session.commit()
            if progress_updated_at is not None:
                target = await session.get(ExecutionTarget, "target")
                target.provider_metadata = {"progress": {
                    "operation_id": "attempt", "job_id": "job", "phase": progress_phase,
                    "artifact": None, "message": "staging", "updated_at": _iso(progress_updated_at),
                }}
                await session.commit()
    return apply()


def worker_is_unreachable(monkeypatch, message="ssh: connect to host worker port 22: No route to host"):
    """Both the status observation and the prepare resume lane fail."""
    calls = []

    async def run_remote(_connection, argv, **_kwargs):
        calls.append(list(argv))
        raise ex.RemoteTransportError(message)

    monkeypatch.setattr(ex, "run_remote", run_remote)
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_worker_argv", lambda _connection, *args: list(args))
    return calls


def prepared_receipt(**overrides):
    values = dict(job_id="job", attempt_id="attempt", state="prepared", boot_id="boot",
                  quiescent=False, started_at=None, completed_at=None)
    values.update(overrides)
    return RemoteAttemptStatus(**values).model_dump_json()


@pytest.mark.asyncio
async def test_stalled_staging_attempt_reaches_terminal_explained_state(store, monkeypatch):
    await staging_claim(store, datetime.utcnow() - timedelta(hours=2))
    calls = worker_is_unreachable(monkeypatch)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job")) is True
    async with store() as session:
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        assert (job.status, job.queue_status, job.remote_state) == ("failed", "failed", "launch_failed")
        assert job.completed_at is not None
        # The reason is the deadline and the evidence, not a log line.
        assert "no forward progress" in job.error_message
        assert record["deadline_at"] in job.error_message
        assert record["state"] == "abandoned"
        assert record["anchor_source"] == "attempt_claim"
        assert record["deadline_seconds"] == 60.0
        assert record["last_progress"] is None
        assert "No route to host" in record["observed_error"]
        assert [call[0] for call in calls] == ["status", "prepare"]
        # Abandonment releases the target lease: nothing is left reserved.
        assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
        # ... and the loop terminates instead of reconciling forever.
        assert await ex.reconcile_remote_job(session, job) is False
        assert (await session.get(Job, "job")).remote_state == "launch_failed"


@pytest.mark.asyncio
async def test_stall_inside_the_confirmation_window_is_recorded_not_abandoned(store, monkeypatch):
    """The evidence is recorded first; abandonment waits out the extra window."""
    await staging_claim(store, datetime.utcnow() - timedelta(seconds=75))
    worker_is_unreachable(monkeypatch)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job")) is True
    async with store() as session:
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        assert (job.status, job.queue_status, job.remote_state) == ("queued", "preparing", "staging")
        assert job.error_message is None
        assert record["state"] == "expired" and record["expired"] is True
        assert "No route to host" in record["observed_error"]
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
async def test_quiet_staging_attempt_records_its_deadline_and_keeps_its_lease(store, monkeypatch):
    await staging_claim(store, datetime.utcnow())
    worker_is_unreachable(monkeypatch)
    async with store() as session:
        job = await session.get(Job, "job")
        assert await ex.reconcile_remote_job(session, job) is True
    async with store() as session:
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        # The deadline is observable in the Job's own state before it expires.
        assert (job.status, job.queue_status, job.remote_state) == ("queued", "preparing", "staging")
        assert job.error_message is None
        assert record["state"] == "awaiting_progress"
        assert record["deadline_at"] is not None
        assert record["expired"] is False
        assert "No route to host" in record["observed_error"]
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"
        observed_at = record["observed_at"]
        # A quiet attempt is not rewritten (or re-logged) on every poll.
        assert await ex.reconcile_remote_job(session, job) is False
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.provenance["remote_attempt_recovery"]["observed_at"] == observed_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_age,progress_age,expired",
    [(7200, 0, False), (0, 7200, True)],
)
async def test_progress_activity_not_claim_age_owns_the_deadline(store, monkeypatch, claim_age, progress_age, expired):
    """A healthy attempt keeps its window open; a quiet one cannot reset it."""
    now = datetime.utcnow()
    await staging_claim(store, now - timedelta(seconds=claim_age), progress_updated_at=now - timedelta(seconds=progress_age))
    worker_is_unreachable(monkeypatch)
    for _ in range(2):
        async with store() as session:
            await ex.reconcile_remote_job(session, await session.get(Job, "job"))
    async with store() as session:
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        assert record["anchor_source"] == "attempt_activity"
        if expired:
            assert job.remote_state == "launch_failed" and record["state"] == "abandoned"
            assert record["last_progress"]["phase"] == "transferring"
            assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
        else:
            assert job.remote_state == "staging" and record["state"] == "awaiting_progress"
            assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
async def test_stalled_attempt_resumes_from_the_worker_prepared_receipt(store, monkeypatch):
    await staging_claim(store, datetime.utcnow() - timedelta(minutes=5))
    calls = []

    async def run_remote(_connection, argv, **_kwargs):
        calls.append(list(argv))
        if argv[0] == "status":
            raise ex.RemoteTransportError("ssh: connection reset by peer")
        return type("Result", (), {"stdout": prepared_receipt()})()

    monkeypatch.setattr(ex, "run_remote", run_remote)
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_worker_argv", lambda _connection, *args: list(args))
    async with store() as session:
        observed = await ex.remote_status(session, await session.get(Job, "job"))
    # The resume lane republishes the durable prepared receipt; nothing is
    # re-uploaded and no successor attempt is issued.
    assert observed.state == "prepared"
    assert [call[0] for call in calls] == ["status", "prepare"]


@pytest.mark.asyncio
async def test_recovered_attempt_is_recorded_as_resumed_on_the_job(store, monkeypatch):
    """A stall that later resumes must not leave the job looking abandoned."""
    await staging_claim(store, datetime.utcnow())
    async with store() as session:
        job = await session.get(Job, "job")
        # JSON columns compare by value: rebuild nested objects so the resource
        # admission is actually persisted, not silently treated as unchanged.
        provenance = json.loads(json.dumps(job.provenance))
        provenance["remote_execution_assignment"]["resources"] = {
            "required": {"cpus": 1, "memory_bytes": 1024, "scratch_bytes": 0, "gpu_ids": []},
            "admission": {"devices": [{"gpu_index": 0}]}, "gpu_ids": [],
        }
        job.provenance = provenance
        await session.commit()

    worker_is_unreachable(monkeypatch)
    async with store() as session:
        job = await session.get(Job, "job")
        assert await ex.reconcile_remote_job(session, job) is True
    async with store() as session:
        assert (await session.get(Job, "job")).provenance["remote_attempt_recovery"]["state"] == "awaiting_progress"

    async def run_remote(_connection, argv, **_kwargs):
        if argv[0] == "status":
            raise ex.RemoteTransportError("ssh: connection reset by peer")
        if argv[0] == "prepare":
            return type("Result", (), {"stdout": prepared_receipt()})()
        return type("Result", (), {"stdout": prepared_receipt(state="running", started_at=datetime.utcnow())})()

    async def verified(*_args, **_kwargs):
        return None

    async def admitted(*_args, **_kwargs):
        return {"devices": [{"gpu_index": 0}]}

    monkeypatch.setattr(ex, "run_remote", run_remote)
    monkeypatch.setattr(ex, "_verify_launch_runner", verified)
    from services.remote_execution import targets
    monkeypatch.setattr(targets, "admit_target_resources", admitted)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job")) is True
    async with store() as session:
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        assert record["state"] == "resumed"
        assert (job.status, job.queue_status) == ("running", "running")
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
async def test_terminal_attempt_without_identity_reclaims_its_lease_and_records_why(store):
    """BMS-DEV-60: a retired row must not reserve the target forever."""
    async with store() as session:
        job = await session.get(Job, "job")
        job.status, job.queue_status = "failed", "failed"
        job.remote_state = "launch_failed"
        job.remote_attempt_id = None
        job.nextflow_run_id = None
        job.error_message = "Retired by operator: staging abandoned after API restart (BMS-DEV-56)"
        await session.commit()
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job")) is True
    async with store() as session:
        target = await session.get(ExecutionTarget, "target")
        job = await session.get(Job, "job")
        record = job.provenance["remote_attempt_recovery"]
        assert target.leased_job_id is None and target.lease_acquired_at is None
        assert record["state"] == "lease_reclaimed"
        assert "no longer exists" in record["detail"]
        # The operator's own terminal reason is never rewritten.
        assert job.error_message.startswith("Retired by operator")
        assert not await ex.reconcile_remote_job(session, job)


@pytest.mark.asyncio
async def test_reclaim_never_steals_a_lease_another_attempt_holds(store):
    async with store() as session:
        job = await session.get(Job, "job")
        job.status, job.queue_status = "failed", "failed"
        job.remote_attempt_id = None
        job.nextflow_run_id = None
        target = await session.get(ExecutionTarget, "target")
        target.leased_job_id = "successor"
        await session.commit()
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job")) is False
    async with store() as session:
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "successor"
        assert "remote_attempt_recovery" not in ((await session.get(Job, "job")).provenance or {})

"""Offline startup tests: no container, service, remote worker or network launch."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, OntSignalCalibrationJob
from services import ont_signal_worker as workers


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_jobs(factory):
    # Persistence-only fixtures, not approved scientific input. No handler runs.
    async with factory() as session:
        for state in ("requested", "running", "ready", "failed", "cancelled"):
            session.add(OntSignalCalibrationJob(
                id=state, run_id="run", observed_generation=1,
                raw_representation_id="raw", move_source_id="moves", sample_count=1,
                request_fingerprint=state, state=state,
                attempt=1 if state == "running" else 0,
                claim_token="persisted-owner" if state == "running" else None,
                lease_expires_at=datetime.utcnow() - timedelta(hours=1) if state == "running" else None,
                stage_receipts={"existing": state},
            ))
        await session.commit()


async def snapshot(factory):
    async with factory() as session:
        return [dict(row) for row in (await session.execute(
            select(OntSignalCalibrationJob.__table__).order_by(OntSignalCalibrationJob.id)
        )).mappings()]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["docker", "podman"])
@pytest.mark.parametrize("persisted", [False, True], ids=["empty-db", "persisted-jobs"])
async def test_absent_backend_leaves_recovery_and_dispatch_blocked(store, monkeypatch, caplog, runtime, persisted):
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", runtime)
    if persisted:
        await seed_jobs(store)
    before = await snapshot(store)
    spawn = AsyncMock(side_effect=FileNotFoundError(2, "No such file or directory", runtime))
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", spawn)
    worker = workers.OntSignalWorker(store, store)
    recover = AsyncMock(side_effect=AssertionError("must not reclaim leases"))
    claim = AsyncMock(side_effect=AssertionError("must not claim scientific work"))
    monkeypatch.setattr(worker, "_recover_expired", recover)
    monkeypatch.setattr(worker, "_claim", claim)
    for _ in range(2):
        assert await worker.start() is False
        await asyncio.sleep(0)
        assert worker._task is None
        assert worker._stop.is_set()
        assert await snapshot(store) == before
    await worker.stop()
    recover.assert_not_awaited()
    claim.assert_not_awaited()
    assert spawn.await_count == 2
    assert spawn.call_args.args[:3] == (runtime, "ps", "-aq")
    assert "recovery and dispatch withheld" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [PermissionError("denied"), OSError("I/O error"), RuntimeError("unexpected")])
async def test_backend_spawn_errors_other_than_absence_propagate(monkeypatch, failure):
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", AsyncMock(side_effect=failure))
    worker = workers.OntSignalWorker(None, None)
    with pytest.raises(type(failure), match=str(failure)):
        await worker.start()
    assert worker._task is None


@pytest.mark.asyncio
async def test_discovery_failure_is_not_backend_absence(monkeypatch):
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    process = AsyncMock()
    process.communicate.return_value = (b"", b"daemon unavailable or access denied")
    process.returncode = 1
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    worker = workers.OntSignalWorker(None, None)
    with pytest.raises(workers.ContainerCleanupError, match="daemon unavailable"):
        await worker.start()
    assert worker._task is None


@pytest.mark.asyncio
async def test_restart_requires_container_recovery_before_lease_recovery_and_dispatch(monkeypatch):
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    spawn = AsyncMock(side_effect=FileNotFoundError(2, "missing", "docker"))
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", spawn)
    worker = workers.OntSignalWorker(None, None)
    assert await worker.start() is False
    events = []
    process = AsyncMock()
    process.returncode = 0

    async def discover():
        events.append("discover")
        return b"stale-owned-container\n", b""

    async def cleanup():
        assert worker._active_container == ("docker", "stale-owned-container")
        events.append("cleanup")
        worker._active_container = None

    async def recover():
        events.append("leases")

    async def run():
        events.append("dispatch")
        await worker._stop.wait()

    process.communicate.side_effect = discover
    spawn.side_effect = None
    spawn.return_value = process
    monkeypatch.setattr(worker, "_remove_active_container", cleanup)
    monkeypatch.setattr(worker, "_recover_expired", recover)
    monkeypatch.setattr(worker, "_run", run)
    assert await worker.start() is True
    await asyncio.sleep(0)
    assert events == ["discover", "cleanup", "leases", "dispatch"]
    assert await worker.start() is True
    assert events == ["discover", "cleanup", "leases", "dispatch"]
    # Restore harmless shutdown behavior after the owned-container assertion.
    monkeypatch.setattr(worker, "_remove_active_container", AsyncMock())
    await worker.stop()


@pytest.mark.asyncio
async def test_missing_file_during_lease_recovery_is_not_swallowed(monkeypatch):
    worker = workers.OntSignalWorker(None, None)
    monkeypatch.setattr(worker, "_recover_stale_containers", AsyncMock())
    monkeypatch.setattr(worker, "_recover_expired", AsyncMock(side_effect=FileNotFoundError("database")))
    with pytest.raises(FileNotFoundError, match="database"):
        await worker.start()
    assert worker._task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [False, True], ids=["empty-db", "persisted-jobs"])
async def test_api_lifespan_yields_with_absent_backend(store, monkeypatch, caplog, persisted):
    # Exercise the real lifespan and ONT startup against isolated SQLite. Other
    # workers/controllers are inert doubles: this is not a host service launch.
    import main
    from services.remote_execution import targets, preloading
    from services.remote_execution.telemetry import remote_telemetry

    if persisted:
        await seed_jobs(store)
    before = await snapshot(store)
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    monkeypatch.delenv("BMS_ONT_SLOW5TOOLS_IMAGE", raising=False)
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", workers.APPROVED_OCI_DIGEST)
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", workers.APPROVED_OCI_DIGEST.removeprefix("sha256:"))
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError(2, "missing", "docker")))
    for name in ("init_db", "init_experiment_db", "init_molbio_db", "init_molbio_ngs_db"):
        monkeypatch.setattr(main, name, AsyncMock())
    for name in ("async_session", "experiment_session_factory", "molbio_ngs_session_factory"):
        monkeypatch.setattr(main, name, store)
    monkeypatch.setattr(main, "reconcile_startup_admissions", AsyncMock(return_value=0))
    monkeypatch.setattr(main, "install_feature_enabled", lambda _: False)

    class IdleWorker:
        def __init__(self, *args, **kwargs):
            pass
        async def start(self):
            pass
        async def stop(self):
            pass
        async def recover(self):
            pass
        async def close(self):
            pass

    for name in ("AnalysisWorker", "ExternalImportWorker", "BoltzApiJobWorker", "FrustraMPNNStatisticsWorker"):
        monkeypatch.setattr(main, name, IdleWorker)
    monkeypatch.setattr(targets, "AttachmentController", IdleWorker)
    monkeypatch.setattr(preloading, "PreloadController", IdleWorker)
    monkeypatch.setattr(targets, "invalidate_vast_inventory", AsyncMock())
    monkeypatch.setattr(targets, "run_vast_inventory_refresh", AsyncMock())
    monkeypatch.setattr(remote_telemetry, "run", AsyncMock())
    async with main.lifespan(main.app):
        assert main._ont_signal_worker is not None
        assert main._ont_signal_worker._task is None
        assert main._ont_signal_worker._stop.is_set()
        assert main._orchestrator is None
        assert await snapshot(store) == before
    assert await snapshot(store) == before
    assert "backend unavailable: worker blocked" in caplog.text
    assert "governed ONT signal-workbench worker started" not in caplog.text

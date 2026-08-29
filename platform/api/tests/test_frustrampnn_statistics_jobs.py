from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNResult, FrustraMPNNStatisticsAnalysis, Job


def test_api_lifespan_owns_statistics_worker() -> None:
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "FrustraMPNNStatisticsWorker" in source
    assert "_frustrampnn_statistics_worker = FrustraMPNNStatisticsWorker(async_session)" in source
    assert "await _frustrampnn_statistics_worker.start()" in source
    assert "await _frustrampnn_statistics_worker.stop()" in source


def test_statistics_child_migration_is_idempotent_and_foreign_key_bound(tmp_path) -> None:
    try:
        migration = importlib.import_module(
            "migrations.add_frustrampnn_statistics_analyses"
        )
        lease_migration = importlib.import_module(
            "migrations.add_frustrampnn_statistics_claim_leases"
        )
    except ModuleNotFoundError:
        pytest.fail("FrustraMPNN statistics child migration is not implemented")
        return
    database_path = tmp_path / "statistics-analysis-migration.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE frustrampnn_results (
                parent_job_id VARCHAR(36) NOT NULL,
                invocation_id VARCHAR(128) NOT NULL,
                PRIMARY KEY (parent_job_id, invocation_id),
                FOREIGN KEY (parent_job_id) REFERENCES jobs(id)
            );
            """
        )

    migration.migrate(database_path)
    migration.migrate(database_path)
    lease_migration.migrate(database_path)
    lease_migration.migrate(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(frustrampnn_statistics_analyses)"
            )
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(frustrampnn_statistics_analyses)"
        ).fetchall()
    assert {
        "analysis_id",
        "parent_job_id",
        "invocation_id",
        "core_bundle_relative_path",
        "core_landscape_sha256",
        "core_manifest_sha256",
        "state",
        "attempt_count",
        "artifact_relative_path",
        "artifact_sha256",
        "statistics_sha256",
        "diagnostic",
        "claim_token",
        "claim_owner",
        "lease_expires_at",
        "heartbeat_at",
    } <= columns
    assert {(row[2], row[3], row[4]) for row in foreign_keys} >= {
        ("frustrampnn_results", "parent_job_id", "parent_job_id"),
        ("frustrampnn_results", "invocation_id", "invocation_id"),
    }
    runner = importlib.import_module("migrations.runner")
    assert (runner.MIGRATIONS[-1].version, runner.MIGRATIONS[-1].name) == (
        42,
        "add_frustrampnn_statistics_claim_leases",
    )


@pytest.mark.asyncio
async def test_failed_statistics_child_retries_without_changing_core_inference(tmp_path) -> None:
    try:
        jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    except ModuleNotFoundError:
        pytest.fail("FrustraMPNN statistics child lifecycle is not implemented")
        return

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'statistics-jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        session.add(
            Job(
                id="core-job",
                name="core-job",
                status="completed",
                queue_status="completed",
                model_id="frustrampnn",
                mode="analyze",
                params={},
                output_dir=str(tmp_path),
            )
        )
        result = FrustraMPNNResult(
            parent_job_id="core-job",
            invocation_id="invoke-1",
            parent_workflow_id="frustrampnn_analysis",
            candidate_id="candidate-1",
            requiredness="required",
            request_sha256="1" * 64,
            source_artifact_sha256="2" * 64,
            manifest_sha256="3" * 64,
            manifest_json={"schema_name": "frustrampnn_result_manifest", "schema_version": 3},
            summary_sha256="4" * 64,
            summary_json={"landscape_sha256": "5" * 64},
            runtime_identity_json={},
            assigned_gpu_json={},
            terminal_result_json={"component_contract_version": "3.0", "status": "succeeded"},
            settings_sha256="6" * 64,
            effective_settings_sha256="7" * 64,
            effective_settings_json={"schema_name": "frustrampnn_effective_settings"},
            capability_inventory_sha256="8" * 64,
        )
        session.add(result)
        await session.commit()

        first = await jobs.ensure_statistics_child(
            session,
            result=result,
            core_artifact_id="artifact-1",
            core_bundle_relative_path="bundle",
        )
        replay = await jobs.ensure_statistics_child(
            session,
            result=result,
            core_artifact_id="artifact-1",
            core_bundle_relative_path="bundle",
        )
        assert replay.analysis_id == first.analysis_id
        assert first.state == "queued"
        assert first.core_landscape_sha256 == "5" * 64
        assert first.core_manifest_sha256 == "3" * 64
        await session.commit()

        running = await jobs.claim_statistics_child(
            session,
            analysis_id=first.analysis_id,
            claim_owner="test-worker-1",
            lease_seconds=60,
        )
        assert running.state == "running"
        assert running.attempt_count == 1
        await jobs.fail_statistics_child(
            session,
            analysis_id=first.analysis_id,
            claim_token=running.claim_token,
            diagnostic="bounded analysis failure",
        )
        await session.commit()

        core_before_retry = (
            result.request_sha256,
            result.manifest_sha256,
            result.terminal_result_json,
        )
        result.terminal_result_json = {
            "component_contract_version": "1.0",
            "status": "succeeded",
        }
        await session.commit()
        with pytest.raises(
            jobs.FrustraMPNNStatisticsJobError,
            match="exact successful v3 core result",
        ):
            await jobs.retry_statistics_child(session, analysis_id=first.analysis_id)
        result.terminal_result_json = core_before_retry[2]
        await session.commit()
        retried = await jobs.retry_statistics_child(session, analysis_id=first.analysis_id)
        assert retried.state == "queued"
        assert retried.attempt_count == 1
        assert retried.diagnostic is None
        assert (
            result.request_sha256,
            result.manifest_sha256,
            result.terminal_result_json,
        ) == core_before_retry

        second = await jobs.claim_statistics_child(
            session,
            analysis_id=first.analysis_id,
            claim_owner="test-worker-2",
            lease_seconds=60,
        )
        assert second.state == "running"
        assert second.attempt_count == 2
        second.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
        recovered = await jobs.recover_abandoned_statistics_claims(
            session,
            stale_before=datetime.utcnow(),
        )
        assert recovered == 1
        await session.commit()
        await session.refresh(second)
        assert second.state == "queued"
        assert second.attempt_count == 2


@pytest.mark.asyncio
async def test_two_sessions_have_one_claim_winner_and_live_lease_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim-owner.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    analysis_id = "44444444-4444-4444-8444-444444444444"

    async with sessions() as seed:
        seed.add(
            Job(
                id="claim-owner-job",
                name="claim-owner-job",
                status="completed",
                queue_status="completed",
                model_id="frustrampnn",
                mode="analyze",
                params={},
            )
        )
        seed.add(
            FrustraMPNNResult(
                parent_job_id="claim-owner-job",
                invocation_id="claim-owner-invocation",
                parent_workflow_id="frustrampnn_analysis",
                candidate_id="claim-owner-candidate",
                requiredness="required",
                request_sha256="1" * 64,
                source_artifact_sha256="2" * 64,
                manifest_sha256="3" * 64,
                manifest_json={},
                summary_sha256="4" * 64,
                summary_json={"landscape_sha256": "5" * 64},
                runtime_identity_json={},
                assigned_gpu_json={},
                terminal_result_json={
                    "component_contract_version": "3.0",
                    "status": "succeeded",
                },
            )
        )
        await seed.flush()
        seed.add(
            FrustraMPNNStatisticsAnalysis(
                analysis_id=analysis_id,
                parent_job_id="claim-owner-job",
                invocation_id="claim-owner-invocation",
                core_artifact_id="claim-owner-artifact",
                core_bundle_relative_path="bundle",
                core_landscape_sha256="5" * 64,
                core_manifest_sha256="3" * 64,
                state="queued",
                attempt_count=0,
                formula_version=jobs.FORMULA_VERSION,
                policy_version=jobs.POLICY_VERSION,
                package_version=jobs.PACKAGE_VERSION,
                schema_version=jobs.SCHEMA_VERSION,
            )
        )
        await seed.commit()

    async with sessions() as owner:
        claimed = await jobs.claim_statistics_child(
            owner,
            analysis_id=analysis_id,
            claim_owner="owner-a",
            lease_seconds=3,
        )
        owner_token = claimed.claim_token
        original_expiry = claimed.lease_expires_at
        await owner.commit()

    async with sessions() as heartbeat:
        assert await jobs.heartbeat_statistics_child(
            heartbeat,
            analysis_id=analysis_id,
            claim_token=owner_token,
            lease_seconds=300,
        )
        await heartbeat.commit()

    async with sessions() as contender:
        with pytest.raises(
            jobs.FrustraMPNNStatisticsJobError,
            match="only queued statistics children can run",
        ):
            await jobs.claim_statistics_child(
                contender,
                analysis_id=analysis_id,
                claim_owner="owner-b",
                lease_seconds=300,
            )
        await contender.rollback()
        assert await jobs.recover_abandoned_statistics_claims(
            contender,
            stale_before=original_expiry + timedelta(seconds=1),
        ) == 0
        await contender.commit()

    async with sessions() as observer:
        persisted = await observer.get(FrustraMPNNStatisticsAnalysis, analysis_id)
        assert persisted is not None
        assert persisted.state == "running"
        assert persisted.attempt_count == 1
        assert persisted.claim_owner == "owner-a"
        assert persisted.claim_token == owner_token


@pytest.mark.asyncio
async def test_expired_claim_is_reclaimed_and_stale_worker_cannot_publish(
    tmp_path: Path,
) -> None:
    jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim-reclaim.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    analysis_id = "55555555-5555-4555-8555-555555555555"

    async with sessions() as seed:
        seed.add(Job(id="reclaim-job", name="reclaim-job", status="completed", queue_status="completed", model_id="frustrampnn", mode="analyze", params={}))
        seed.add(FrustraMPNNResult(
            parent_job_id="reclaim-job", invocation_id="reclaim-invocation",
            parent_workflow_id="frustrampnn_analysis", candidate_id="candidate",
            requiredness="required", request_sha256="1" * 64,
            source_artifact_sha256="2" * 64, manifest_sha256="3" * 64,
            manifest_json={}, summary_sha256="4" * 64,
            summary_json={"landscape_sha256": "5" * 64}, runtime_identity_json={},
            assigned_gpu_json={}, terminal_result_json={"component_contract_version": "3.0", "status": "succeeded"},
        ))
        await seed.flush()
        seed.add(FrustraMPNNStatisticsAnalysis(
            analysis_id=analysis_id, parent_job_id="reclaim-job",
            invocation_id="reclaim-invocation", core_artifact_id="artifact",
            core_bundle_relative_path="bundle", core_landscape_sha256="5" * 64,
            core_manifest_sha256="3" * 64, state="queued", attempt_count=0,
            formula_version=jobs.FORMULA_VERSION, policy_version=jobs.POLICY_VERSION,
            package_version=jobs.PACKAGE_VERSION, schema_version=jobs.SCHEMA_VERSION,
        ))
        await seed.commit()

    async with sessions() as old_owner:
        old = await jobs.claim_statistics_child(
            old_owner, analysis_id=analysis_id, claim_owner="old-owner", lease_seconds=60
        )
        old_token = old.claim_token
        old.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await old_owner.commit()

    async with sessions() as recovery:
        assert await jobs.recover_abandoned_statistics_claims(
            recovery, stale_before=datetime.utcnow()
        ) == 1
        await recovery.commit()

    async with sessions() as new_owner:
        new = await jobs.claim_statistics_child(
            new_owner, analysis_id=analysis_id, claim_owner="new-owner", lease_seconds=60
        )
        new_token = new.claim_token
        await new_owner.commit()
    assert new_token != old_token

    async with sessions() as stale:
        with pytest.raises(jobs.FrustraMPNNStatisticsJobError, match="no longer owns"):
            await jobs.complete_statistics_child(
                stale, analysis_id=analysis_id, claim_token=old_token,
                statistics_sha256="6" * 64,
                artifact_relative_path="statistics.json", artifact_sha256="7" * 64,
                statistics_reference={"relative_path": "statistics.json", "content_sha256": "7" * 64},
                comparison_compatibility_id="8" * 64,
            )
        await stale.rollback()

    async with sessions() as observer:
        child = await observer.get(FrustraMPNNStatisticsAnalysis, analysis_id)
        core = await observer.get(FrustraMPNNResult, ("reclaim-job", "reclaim-invocation"))
        assert child is not None and child.state == "running"
        assert child.claim_token == new_token
        assert core is not None and core.statistics_sha256 is None


@pytest.mark.asyncio
async def test_retry_accepts_expired_claim_and_preserves_immutable_core_result(tmp_path: Path) -> None:
    jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expired-retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    analysis_id = "66666666-6666-4666-8666-666666666666"
    async with sessions() as session:
        session.add(Job(id="retry-job", name="retry-job", status="completed", queue_status="completed", model_id="frustrampnn", mode="analyze", params={}))
        core = FrustraMPNNResult(
            parent_job_id="retry-job", invocation_id="retry-invocation",
            parent_workflow_id="frustrampnn_analysis", candidate_id="candidate",
            requiredness="required", request_sha256="1" * 64,
            source_artifact_sha256="2" * 64, manifest_sha256="3" * 64,
            manifest_json={}, summary_sha256="4" * 64,
            summary_json={"landscape_sha256": "5" * 64}, runtime_identity_json={},
            assigned_gpu_json={}, terminal_result_json={"component_contract_version": "3.0", "status": "succeeded"},
        )
        session.add(core)
        await session.flush()
        session.add(FrustraMPNNStatisticsAnalysis(
            analysis_id=analysis_id, parent_job_id="retry-job", invocation_id="retry-invocation",
            core_artifact_id="artifact", core_bundle_relative_path="bundle",
            core_landscape_sha256="5" * 64, core_manifest_sha256="3" * 64,
            state="running", attempt_count=1, formula_version=jobs.FORMULA_VERSION,
            policy_version=jobs.POLICY_VERSION, package_version=jobs.PACKAGE_VERSION,
            schema_version=jobs.SCHEMA_VERSION, claim_token="expired-token",
            claim_owner="dead-worker", heartbeat_at=datetime.utcnow() - timedelta(minutes=2),
            lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
        ))
        await session.commit()
        immutable_before = (core.request_sha256, core.manifest_sha256, dict(core.terminal_result_json))

        retried = await jobs.retry_statistics_child(session, analysis_id=analysis_id)
        await session.commit()
        assert retried.state == "queued"
        assert retried.claim_token is None
        assert retried.claim_owner is None
        assert (core.request_sha256, core.manifest_sha256, core.terminal_result_json) == immutable_before



@pytest.mark.asyncio
async def test_worker_commits_conditional_claim_before_cpu_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim-boundary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    analysis_id = "33333333-3333-4333-8333-333333333333"
    async with sessions() as session:
        session.add(
            Job(
                id="claim-job",
                name="claim-job",
                status="completed",
                queue_status="completed",
                model_id="frustrampnn",
                mode="analyze",
                params={},
            )
        )
        session.add(
            FrustraMPNNResult(
                parent_job_id="claim-job",
                invocation_id="invoke-claim",
                parent_workflow_id="frustrampnn_analysis",
                candidate_id="candidate-claim",
                requiredness="required",
                request_sha256="1" * 64,
                source_artifact_sha256="2" * 64,
                manifest_sha256="3" * 64,
                manifest_json={},
                summary_sha256="4" * 64,
                summary_json={"landscape_sha256": "5" * 64},
                runtime_identity_json={},
                assigned_gpu_json={},
                terminal_result_json={"component_contract_version": "3.0", "status": "succeeded"},
            )
        )
        await session.flush()
        session.add(
            FrustraMPNNStatisticsAnalysis(
                analysis_id=analysis_id,
                parent_job_id="claim-job",
                invocation_id="invoke-claim",
                core_artifact_id="artifact-claim",
                core_bundle_relative_path="bundle",
                core_landscape_sha256="5" * 64,
                core_manifest_sha256="3" * 64,
                state="queued",
                attempt_count=0,
                formula_version=jobs.FORMULA_VERSION,
                policy_version=jobs.POLICY_VERSION,
                package_version=jobs.PACKAGE_VERSION,
                schema_version=jobs.SCHEMA_VERSION,
            )
        )
        await session.commit()

    async def compute_after_committed_claim(
        session, *, analysis_id: str, claim_token: str, lease_seconds: int
    ):
        assert claim_token
        assert lease_seconds >= 3
        async with sessions() as observer:
            observed = await observer.get(FrustraMPNNStatisticsAnalysis, analysis_id)
            assert observed is not None
            assert observed.state == "running"
            assert observed.attempt_count == 1
        child = await session.get(FrustraMPNNStatisticsAnalysis, analysis_id)
        assert child is not None and child.state == "running"
        child.state = "completed"
        await session.flush()
        return {"completed": True}

    monkeypatch.setattr(jobs, "run_statistics_child_once", compute_after_committed_claim)
    worker = jobs.FrustraMPNNStatisticsWorker(sessions)
    assert await worker.run_pending_once() == analysis_id
    async with sessions() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, analysis_id)
        assert child is not None and child.state == "completed"
    await engine.dispose()

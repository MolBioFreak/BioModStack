from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNResult, Job


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
    } <= columns
    assert {(row[2], row[3], row[4]) for row in foreign_keys} >= {
        ("frustrampnn_results", "parent_job_id", "parent_job_id"),
        ("frustrampnn_results", "invocation_id", "invocation_id"),
    }
    runner = importlib.import_module("migrations.runner")
    assert (runner.MIGRATIONS[-1].version, runner.MIGRATIONS[-1].name) == (
        41,
        "add_frustrampnn_statistics_analyses",
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

        running = await jobs.claim_statistics_child(session, analysis_id=first.analysis_id)
        assert running.state == "running"
        assert running.attempt_count == 1
        await jobs.fail_statistics_child(
            session,
            analysis_id=first.analysis_id,
            diagnostic="bounded analysis failure",
        )
        await session.commit()

        core_before_retry = (
            result.request_sha256,
            result.manifest_sha256,
            result.terminal_result_json,
        )
        retried = await jobs.retry_statistics_child(session, analysis_id=first.analysis_id)
        assert retried.state == "queued"
        assert retried.attempt_count == 1
        assert retried.diagnostic is None
        assert (
            result.request_sha256,
            result.manifest_sha256,
            result.terminal_result_json,
        ) == core_before_retry

        second = await jobs.claim_statistics_child(session, analysis_id=first.analysis_id)
        assert second.state == "running"
        assert second.attempt_count == 2

    await engine.dispose()

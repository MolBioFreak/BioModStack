from __future__ import annotations

import importlib
import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(autouse=True)
def _fresh_telemetry_readiness(monkeypatch):
    readiness = importlib.import_module("readiness")
    monkeypatch.setattr(
        readiness,
        "telemetry_collection_readiness",
        lambda: _async_telemetry_result(True, "fresh"),
        raising=False,
    )


@pytest.mark.asyncio
async def test_native_readiness_does_not_require_workflow_adapter(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://frontend.test/bms/")
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))
    monkeypatch.setattr(readiness, "core_migration_readiness", lambda: _async_migration_result(True, "at_head"))
    monkeypatch.setattr(readiness, "http_readiness", lambda _url: _async_result(True, "ready"))

    result = await readiness.collect_runtime_readiness(
        molbio={"status": "healthy", "ready": True},
    )

    assert result["mode"] == "native"
    assert result["checks"]["workflow_adapter"] == {
        "required": False,
        "ready": True,
        "status": "not_required",
    }
    assert result["checks"]["workflow_launch"]["allowed"] is True
    assert result["ready"] is True


@pytest.mark.asyncio
async def test_container_readiness_requires_reachable_adapter(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://frontend.test/bms/")
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))
    monkeypatch.setattr(readiness, "core_migration_readiness", lambda: _async_migration_result(True, "at_head"))

    async def fake_http(url: str):
        if "adapter.test" in url:
            return False, "unreachable"
        return True, "ready"

    monkeypatch.setattr(readiness, "http_readiness", fake_http)

    result = await readiness.collect_runtime_readiness(
        molbio={"status": "healthy", "ready": True},
    )

    assert result["checks"]["workflow_adapter"]["required"] is True
    assert result["checks"]["workflow_adapter"]["ready"] is False
    assert result["checks"]["workflow_launch"]["allowed"] is False
    assert result["ready"] is False


@pytest.mark.asyncio
async def test_version_endpoint_reports_build_identity(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "fedcba9876543210fedcba9876543210fedcba98")
    monkeypatch.setenv("BMS_BUILD_ID", "phase5-test")
    monkeypatch.setenv("BMS_BUILD_TIME", "2026-07-18T04:00:00Z")
    main = importlib.import_module("main")

    response = await main.api_version()

    assert response["service"] == "biomodstack-api"
    assert response["build"]["revision"] == "fedcba9876543210fedcba9876543210fedcba98"


def test_api_route_signatures_are_unique() -> None:
    main = importlib.import_module("main")
    signatures: list[tuple[str, tuple[str, ...]]] = []
    for route in main.app.routes:
        methods = tuple(sorted(getattr(route, "methods", set()) or set()))
        if methods:
            signatures.append((route.path, methods))

    duplicates = sorted({signature for signature in signatures if signatures.count(signature) > 1})
    assert duplicates == []


async def _async_result(ready: bool, status: str):
    return ready, status


async def _async_migration_result(ready: bool, status: str):
    return ready, status, {
        "expected_version": 27, "expected_name": "add_frustrampnn_reviews",
        "applied_version": 27 if ready else 26,
        "applied_name": "add_frustrampnn_reviews" if ready else "add_frustrampnn_statistics",
    }


async def _async_telemetry_result(ready: bool, status: str):
    return ready, status, {
        "latest_timestamp_ms": 1_700_000_000_000,
        "age_ms": 1_000 if ready else 15_001,
        "stale_after_ms": 15_000,
    }


@pytest.mark.asyncio
async def test_runtime_readiness_degrades_when_telemetry_collection_is_stale(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.delenv("BMS_FRONTEND_HEALTH_URL", raising=False)
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))
    monkeypatch.setattr(readiness, "core_migration_readiness", lambda: _async_migration_result(True, "at_head"))
    monkeypatch.setattr(
        readiness,
        "telemetry_collection_readiness",
        lambda: _async_telemetry_result(False, "stale"),
        raising=False,
    )

    result = await readiness.collect_runtime_readiness(molbio={"status": "healthy", "ready": True})

    assert result["ready"] is False
    assert result["checks"]["telemetry_collection"] == {
        "required": True,
        "ready": False,
        "status": "stale",
        "latest_timestamp_ms": 1_700_000_000_000,
        "age_ms": 15_001,
        "stale_after_ms": 15_000,
    }


@pytest.mark.asyncio
async def test_runtime_readiness_degrades_when_core_migrations_are_behind(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.delenv("BMS_FRONTEND_HEALTH_URL", raising=False)
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))
    monkeypatch.setattr(readiness, "core_migration_readiness", lambda: _async_migration_result(False, "behind"))

    result = await readiness.collect_runtime_readiness(molbio={"status": "healthy", "ready": True})

    assert result["ready"] is False
    assert result["checks"]["core_database"]["ready"] is True
    assert result["checks"]["core_schema_migrations"]["status"] == "behind"
    assert result["checks"]["core_schema_migrations"]["expected_version"] == 27


@pytest.mark.asyncio
async def test_migration_readiness_rejects_head_ledger_without_required_schema_objects(tmp_path, monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    candidate_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'forged-head.db'}")
    async with candidate_engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        for migration in readiness.MIGRATIONS:
            await connection.exec_driver_sql(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
    monkeypatch.setattr(readiness, "engine", candidate_engine)

    ready, status, metadata = await readiness.core_migration_readiness()

    assert ready is False
    assert status == "schema_objects_missing"
    assert "frustrampnn_reviews" in metadata["missing_schema_objects"]
    await candidate_engine.dispose()


@pytest.mark.asyncio
async def test_migration_readiness_rejects_named_but_malformed_head_schema(tmp_path, monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    candidate_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'forged-physical-head.db'}")
    async with candidate_engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        for migration in readiness.MIGRATIONS:
            await connection.exec_driver_sql("INSERT INTO schema_migrations(version, name) VALUES (?, ?)", (migration.version, migration.name))
        for table in ("frustrampnn_reviews", "frustrampnn_exports", "frustrampnn_review_artifacts"):
            await connection.exec_driver_sql(f'CREATE TABLE "{table}" (id TEXT)')
        await connection.exec_driver_sql("CREATE INDEX ix_frustrampnn_reviews_parent_job_id ON frustrampnn_reviews(id)")
        await connection.exec_driver_sql("CREATE INDEX ix_frustrampnn_reviews_owner_job ON frustrampnn_reviews(id)")
        await connection.exec_driver_sql("CREATE INDEX ix_frustrampnn_reviews_created_at ON frustrampnn_reviews(id)")
        await connection.exec_driver_sql("CREATE INDEX ix_frustrampnn_exports_owner_job ON frustrampnn_exports(id)")
        await connection.exec_driver_sql("CREATE INDEX ix_frustrampnn_review_artifacts_owner_review ON frustrampnn_review_artifacts(id)")
    monkeypatch.setattr(readiness, "engine", candidate_engine)

    ready, status, metadata = await readiness.core_migration_readiness()

    assert ready is False
    assert status == "physical_schema_invalid"
    assert "frustrampnn_reviews:columns" in metadata["physical_schema_errors"]
    await candidate_engine.dispose()


def test_review_migration_rejects_preexisting_malformed_schema(tmp_path) -> None:
    migration = importlib.import_module("migrations.add_frustrampnn_reviews")
    db_path = tmp_path / "malformed-before-migration.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL);
            CREATE TABLE frustrampnn_results (
                parent_job_id VARCHAR(36) NOT NULL,
                invocation_id VARCHAR(128) NOT NULL,
                UNIQUE(parent_job_id, invocation_id)
            );
            CREATE TABLE frustrampnn_reviews (review_id TEXT);
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO schema_migrations(version, name) VALUES (26, 'add_frustrampnn_statistics');
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="physical-schema mismatch"):
        migration.migrate(db_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 26
        assert [row[1] for row in connection.execute("PRAGMA table_info(frustrampnn_reviews)")] == ["review_id"]

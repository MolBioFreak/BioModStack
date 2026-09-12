"""Ordinary health checks must not audit all retained molecular data."""
from __future__ import annotations

import sqlite3

import pytest

import molbio_ngs_migrations as migrations


def test_light_health_skips_retained_data_checks_but_reports_scope(tmp_path, monkeypatch):
    path = tmp_path / "domain.db"
    migrations.run_all(path)
    statements: list[str] = []
    original_connect = migrations._connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    def forbidden(*args, **kwargs):
        pytest.fail("normal health must not walk retained native rows/artifacts")

    monkeypatch.setattr(migrations, "_connect", traced_connect)
    monkeypatch.setattr(migrations, "_authority_coherence_errors", forbidden)
    monkeypatch.setattr(migrations, "_artifact_errors", forbidden)
    result = migrations.health(path, deep=False)
    assert result["status"] == "healthy"
    assert result["attestation"]["ok"] is True
    assert result["attestation"]["data_integrity_checked"] is False
    for key in ("foreign_key_errors", "authority_coherence_errors", "artifact_errors"):
        assert result["attestation"][key] is None, "not checked must not claim zero errors"
    assert not any("foreign_key_check" in sql.lower() for sql in statements)
    assert not any("quick_check" in sql.lower() for sql in statements)


def test_light_health_still_rejects_schema_drift(tmp_path):
    path = tmp_path / "domain.db"
    migrations.run_all(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unexpected_source (value TEXT)")
    result = migrations.health(path, deep=False)
    assert result["status"] == "degraded"
    assert result["attestation"]["ok"] is False
    assert any(row["name"] == "unexpected_source" for row in result["attestation"]["extra_objects"])


def test_default_deep_health_keeps_native_integrity_checks(tmp_path, monkeypatch):
    path = tmp_path / "domain.db"
    migrations.run_all(path)
    calls: list[str] = []

    def coherence(connection):
        calls.append("coherence")
        return []

    def artifacts(connection, artifact_root=None):
        calls.append("artifacts")
        return [{"error": "test artifact digest mismatch"}]

    monkeypatch.setattr(migrations, "_authority_coherence_errors", coherence)
    monkeypatch.setattr(migrations, "_artifact_errors", artifacts)
    result = migrations.health(path)
    assert calls == ["coherence", "artifacts"]
    assert result["status"] == "degraded"
    assert result["attestation"]["data_integrity_checked"] is True
    assert result["attestation"]["artifact_errors"] == [{"error": "test artifact digest mismatch"}]


def test_light_health_does_not_create_missing_database(tmp_path):
    path = tmp_path / "not-there.db"
    result = migrations.health(path, deep=False)
    assert result["status"] == "error"
    assert result["attestation"]["ok"] is False
    assert not path.exists()


@pytest.mark.asyncio
async def test_async_health_forwards_explicit_integrity_mode(tmp_path, monkeypatch):
    import molbio_ngs_database as database

    path = tmp_path / "domain.db"
    calls = []
    monkeypatch.setattr(database, "get_molbio_ngs_db_path", lambda: path)

    def probe(selected_path, *, deep=True):
        calls.append((selected_path, deep))
        return {"status": "healthy", "deep": deep}

    monkeypatch.setattr(database, "health", probe)
    assert (await database.molbio_ngs_health(deep=False))["deep"] is False
    assert (await database.molbio_ngs_health())["deep"] is True
    assert calls == [(path, False), (path, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("digest_status,owner_status,expected", [
    ({"ready": True}, "healthy", True),
    ({"ready": False}, "healthy", False),
    (None, "healthy", False),
    ({"ready": True}, "degraded", False),
])
async def test_readiness_consumes_owner_status_without_revalidating_policy(
    monkeypatch, digest_status, owner_status, expected,
):
    import readiness
    from services import restriction_digest

    async def ready():
        return True, "ready"

    async def ready_metadata():
        return True, "ready", {}

    def forbidden():
        pytest.fail("readiness must not rebuild the owning service's policy")

    monkeypatch.setattr(readiness, "core_runtime_mode_enabled", lambda: False)
    monkeypatch.setattr(readiness, "workflow_adapter_base_url", lambda: "")
    monkeypatch.setattr(readiness, "workflow_launches_allowed", lambda: True)
    monkeypatch.delenv("BMS_FRONTEND_HEALTH_URL", raising=False)
    monkeypatch.setattr(readiness, "core_database_readiness", ready)
    monkeypatch.setattr(readiness, "core_migration_readiness", ready_metadata)
    monkeypatch.setattr(readiness, "telemetry_collection_readiness", ready_metadata)
    monkeypatch.setattr(readiness.catalog_authority, "readiness", lambda: {"required": True, "ready": True})
    monkeypatch.setattr(readiness.product_authority, "readiness", lambda: {"required": True, "ready": True})
    monkeypatch.setattr(restriction_digest, "resource_policy_receipt", forbidden)
    result = await readiness.collect_runtime_readiness(
        molbio={"status": owner_status, "restriction_digest": digest_status},
    )
    assert result["checks"]["restriction_digest"]["ready"] is expected
    assert result["ready"] is expected


def test_health_query_work_does_not_grow_with_domain_history(tmp_path, monkeypatch, record_property):
    from statistics import median
    from time import perf_counter

    path = tmp_path / "domain.db"
    migrations.run_all(path)
    connection = migrations._connect(path)
    try:
        connection.execute("BEGIN")
        connection.executemany(
            "INSERT INTO molbio_ngs_domain_states "
            "(global_domain_experiment_id, head_generation, current_binding_revision_id, created_at, updated_at) "
            "VALUES (?,0,?,?,?)",
            [(f"domain-{i}", f"binding-{i}", "2026-09-11T00:00:00Z", "2026-09-11T00:00:00Z") for i in range(2000)],
        )
        connection.executemany(
            "INSERT INTO molbio_ngs_global_binding_revisions ("
            "binding_revision_id, global_domain_experiment_id, revision_number, "
            "global_domain_experiment_revision_id, global_domain_experiment_revision_digest, "
            "project_id, project_generation, project_digest, project_receipt_id, "
            "project_reopen_destination, project_acknowledgement, global_experiment_id, "
            "global_experiment_generation, global_experiment_digest, global_experiment_receipt_id, "
            "global_experiment_reopen_destination, global_experiment_acknowledgement, binding_state, created_at"
            ") VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f"binding-{i}", f"domain-{i}", f"revision-{i}", "0" * 64,
              "project", "1", "0" * 64, "project-receipt", "{}", "{}",
              "experiment", "1", "0" * 64, "experiment-receipt", "{}", "{}",
              "needs_reverification", "2026-09-11T00:00:00Z") for i in range(2000)],
        )
        connection.commit()
    finally:
        connection.close()
    counts = {"coherence": 0, "artifacts": 0}
    original_coherence = migrations._authority_coherence_errors
    original_artifacts = migrations._artifact_errors

    def coherence(connection):
        counts["coherence"] += 1
        return original_coherence(connection)

    def artifacts(connection, artifact_root=None):
        counts["artifacts"] += 1
        return original_artifacts(connection, artifact_root)

    monkeypatch.setattr(migrations, "_authority_coherence_errors", coherence)
    monkeypatch.setattr(migrations, "_artifact_errors", artifacts)
    timings = {}
    for deep in (False, True):
        elapsed = []
        for _ in range(5):
            started = perf_counter()
            report = migrations.health(path, deep=deep)
            elapsed.append((perf_counter() - started) * 1000)
            assert report["status"] == "healthy", report
        timings["deep_ms" if deep else "light_ms"] = median(elapsed)
        assert counts == ({"coherence": 5, "artifacts": 5} if deep else {"coherence": 0, "artifacts": 0})
    record_property("domain_rows", 2000)
    for name, value in timings.items():
        record_property(name, round(value, 3))
    record_property("light_native_data_walks", 0)
    print({"domain_rows": 2000, **timings, "light_native_data_walks": 0})


@pytest.mark.asyncio
async def test_http_health_defaults_to_light_with_explicit_deep_diagnostic(monkeypatch):
    import httpx
    import main

    calls = []

    async def core_health(*, deep=True):
        calls.append(("core", deep))
        return {"status": "healthy", "data_integrity_checked": deep}

    async def domain_health(*, deep=True):
        calls.append(("domain", deep))
        return {"status": "healthy", "data_integrity_checked": deep}

    async def readiness(*, molbio, molbio_ngs):
        assert molbio["data_integrity_checked"] == molbio_ngs["data_integrity_checked"]
        return {"ready": True, "checks": {}}

    monkeypatch.setattr(main, "molbio_health", core_health)
    monkeypatch.setattr(main, "molbio_ngs_health", domain_health)
    monkeypatch.setattr(main, "collect_runtime_readiness", readiness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        light = await client.get("/api/health")
        deep = await client.get("/api/health", params={"deep": "true"})
        invalid = await client.get("/api/health", params={"deep": "not-a-boolean"})
    assert light.status_code == deep.status_code == 200
    assert light.json()["molbio_ngs"]["data_integrity_checked"] is False
    assert deep.json()["molbio_ngs"]["data_integrity_checked"] is True
    assert invalid.status_code == 422
    assert calls == [("core", False), ("domain", False), ("core", True), ("domain", True)]

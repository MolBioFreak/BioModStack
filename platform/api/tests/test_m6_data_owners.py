"""Targeted M6 data-owner tests (R17, R18, R35 / C66)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace


def test_r18_migration_51_imports_legacy_molbio_and_drops_8_tables(tmp_path, monkeypatch):
    from migrations.drop_retired_and_legacy_molbio_tables import (
        _RETIRED_AND_LEGACY_TABLES,
        migrate_drop_retired_and_legacy_molbio_tables,
    )

    core_db = tmp_path / "biomodstack.db"
    molbio_db = tmp_path / "molbio.db"
    monkeypatch.setenv("BMS_MOLBIO_DB_PATH", str(molbio_db))

    conn = sqlite3.connect(core_db)
    try:
        for table in _RETIRED_AND_LEGACY_TABLES:
            if table == "nucleotide_sequences":
                conn.execute(
                    "CREATE TABLE nucleotide_sequences ("
                    "id TEXT PRIMARY KEY, name TEXT, sequence TEXT, topology TEXT, "
                    "annotations TEXT, created_at TEXT, updated_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO nucleotide_sequences VALUES "
                    "('seq-1', 'pTest', 'ATGCATGC', 'circular', '[]', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            elif table == "primers":
                conn.execute(
                    "CREATE TABLE primers ("
                    "id TEXT PRIMARY KEY, name TEXT, sequence TEXT, created_at TEXT)"
                )
            else:
                conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    migrate_drop_retired_and_legacy_molbio_tables(str(core_db))

    conn = sqlite3.connect(core_db)
    try:
        remaining = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert remaining.isdisjoint(set(_RETIRED_AND_LEGACY_TABLES))
    finally:
        conn.close()

    mconn = sqlite3.connect(molbio_db)
    try:
        rows = mconn.execute("SELECT id, name FROM nucleotide_sequences").fetchall()
        assert ("seq-1", "pTest") in rows
    finally:
        mconn.close()


def test_r17_and_r35_schema_migrations_drop_retired_tables(tmp_path):
    import experiment_migrations as em
    import molbio_ngs_migrations as nm

    exp_db = tmp_path / "experiments.db"
    ngs_db = tmp_path / "molbio_ngs.db"
    em.run_all(exp_db)
    nm.run_all(ngs_db)

    ec = sqlite3.connect(exp_db)
    try:
        assert ec.execute("SELECT MAX(version) FROM experiment_schema_migrations").fetchone()[0] == 28
        exp_tables = {row[0] for row in ec.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for retired in (
            "domain_connector_commands",
            "domain_connector_streams",
            "domain_connector_inbox",
            "domain_connector_conflicts",
            "run_control_commands",
            "resource_admissions",
            "resource_admission_policy",
            "log_streams",
            "log_chunks",
        ):
            assert retired not in exp_tables
        assert em.attest_schema(ec)["ok"] is True
    finally:
        ec.close()

    nc = sqlite3.connect(ngs_db)
    try:
        assert nc.execute("SELECT MAX(version) FROM molbio_ngs_schema_migrations").fetchone()[0] == 6
        ngs_tables = {row[0] for row in nc.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for retired in (
            "molbio_ngs_global_binding_revisions",
            "molbio_ngs_outbox_events",
            "molbio_ngs_connector_acks",
        ):
            assert retired not in ngs_tables
        assert nm.attest_schema(nc)["ok"] is True
    finally:
        nc.close()


def test_r35_c66_local_resource_placement_budget_and_release(monkeypatch):
    import biomodstack_local_resources as resources
    from services import local_resource_placement as placement

    monkeypatch.setattr(
        placement,
        "applied_local_policy",
        lambda: resources.LocalCapacity(cpu_threads=16, memory_bytes=32 * 1024**3),
    )

    job_a = SimpleNamespace(params={"resources": {"cpu_threads": 8, "dram_gb": 16}}, execution_target_id=None)
    job_b = SimpleNamespace(params={"resources": {"cpu_threads": 8, "dram_gb": 16}}, execution_target_id=None)
    job_c = SimpleNamespace(params={"resources": {"cpu_threads": 4, "dram_gb": 8}}, execution_target_id=None)
    remote_job = SimpleNamespace(params={"resources": {"cpu_threads": 64, "dram_gb": 256}}, execution_target_id="vast-1")

    req_b = placement.extract_job_resource_request(job_b.params)
    req_c = placement.extract_job_resource_request(job_c.params)
    assert placement.extract_job_resource_request(remote_job.params, execution_target_id="vast-1") == placement.LocalResourceRequest(0, 0)

    # Both fitting concurrent jobs fit; a third over-budget job is deferred.
    used_1 = placement.aggregate_local_resource_usage([job_a, remote_job])
    assert placement.fits_local_resource_budget(used_1, req_b) is True
    used_2 = placement.aggregate_local_resource_usage([job_a, job_b, remote_job])
    assert placement.fits_local_resource_budget(used_2, req_c) is False

    # After job_a finishes (or after restart with only job_b active), job_c fits.
    used_after_release = placement.aggregate_local_resource_usage([job_b])
    assert placement.fits_local_resource_budget(used_after_release, req_c) is True

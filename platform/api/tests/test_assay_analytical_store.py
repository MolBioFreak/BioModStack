from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def test_analytical_store_defaults_to_separate_postgres_url_without_reusing_protein_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_ANALYTICAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("BMS_ANALYTICAL_DB_HOST", "fatboy.local")
    monkeypatch.setenv("BMS_ANALYTICAL_DB_PORT", "5432")
    monkeypatch.setenv("BMS_ANALYTICAL_DB_NAME", "bms_analytical_data")
    monkeypatch.setenv("BMS_ANALYTICAL_DB_USER", "bms_assay")
    monkeypatch.setenv("BMS_ANALYTICAL_DB_PASSWORD", "secret password")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////tmp/protein-workflow.db")

    from services.assay_analytical_store import build_analytical_database_url, analytical_store_settings

    settings = analytical_store_settings()
    url = build_analytical_database_url(settings)
    parsed = make_url(url)

    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.host == "fatboy.local"
    assert parsed.port == 5432
    assert parsed.database == "bms_analytical_data"
    assert parsed.username == "bms_assay"
    assert parsed.password == "secret password"
    assert "protein-workflow" not in url


def test_analytical_schema_uses_own_metadata_and_core_assay_tables() -> None:
    from services.assay_analytical_store import AnalyticalBase

    assert AnalyticalBase.metadata is not None
    assert "jobs" not in AnalyticalBase.metadata.tables
    assert "designs" not in AnalyticalBase.metadata.tables

    expected_tables = {
        "analytical_imports",
        "analytical_source_files",
        "assay_runs",
        "sample_registry",
        "qpcr_wells",
        "qpcr_standard_curves",
        "chrom_injections",
        "chrom_peaks",
        "chrom_calibration_curves",
        "assay_comparisons",
        "qc_trends",
    }
    assert expected_tables.issubset(set(AnalyticalBase.metadata.tables))


def test_analytical_store_status_reports_separate_source_and_connection_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BMS_ANALYTICAL_DATABASE_URL",
        "postgresql+asyncpg://bms_assay:top-secret@fatboy.local:5432/bms_analytical_data",
    )

    from services.assay_analytical_store import analytical_store_status

    status = analytical_store_status()

    assert status["enabled"] is True
    assert status["database_kind"] == "postgresql"
    assert status["separate_from_protein_workflow_db"] is True
    assert status["schema_owner"] == "assay-analytics"
    assert status["database_name"] == "bms_analytical_data"
    assert status["host"] == "fatboy.local"
    assert "top-secret" not in str(status)
    assert status["url_preview"] == "postgresql+asyncpg://bms_assay:***@fatboy.local:5432/bms_analytical_data"

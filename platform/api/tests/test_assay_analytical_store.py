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


def test_analytical_schema_supports_db_native_datasets_source_blobs_and_reanalysis() -> None:
    from services.assay_analytical_store import AnalyticalBase

    tables = AnalyticalBase.metadata.tables
    assert {"analytical_datasets", "analytical_dataset_members", "analytical_analysis_runs"}.issubset(tables)

    source_columns = set(tables["analytical_source_files"].columns.keys())
    assert {"content_bytes", "original_relative_path", "file_extension"}.issubset(source_columns)

    import_columns = set(tables["analytical_imports"].columns.keys())
    assert {"source_fingerprint", "dataset_label", "status"}.issubset(import_columns)

    archive_columns = set(tables["analytical_archive_members"].columns.keys())
    assert {"content_bytes", "content_text"}.issubset(archive_columns)


@pytest.mark.asyncio
async def test_qpcr_persistence_stores_source_bytes_creates_dataset_and_deduplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'assay-analytics.db'}"
    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", db_url)

    from sqlalchemy import select
    from services.assay_analytical_store import (
        AnalyticalDataset,
        AnalyticalImport,
        AnalyticalSourceFile,
        create_analytical_session_factory,
        persist_qpcr_import_response,
    )

    response = {
        "import_engine": "test_qpcr_parser",
        "instrument_format": "QuantStudio XLSX",
        "n_wells": 1,
        "targets": ["GOI"],
        "samples": ["sample-1"],
        "wells": [{"well_position": "A1", "sample_name": "sample-1", "target_name": "GOI", "cq": 21.5, "quantity": 10.0}],
    }
    source_bytes = b"real qpcr workbook bytes"

    first = await persist_qpcr_import_response(response, source_bytes=source_bytes, filename="run.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    second = await persist_qpcr_import_response(response, source_bytes=source_bytes, filename="run.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    assert first["analytical_import_id"] == second["analytical_import_id"]
    assert second["duplicate_detected"] is True
    assert first["dataset_id"] == second["dataset_id"]

    session_factory = create_analytical_session_factory(db_url)
    async with session_factory() as session:
        imports = (await session.execute(select(AnalyticalImport))).scalars().all()
        sources = (await session.execute(select(AnalyticalSourceFile))).scalars().all()
        datasets = (await session.execute(select(AnalyticalDataset))).scalars().all()

    assert len(imports) == 1
    assert imports[0].source_fingerprint == sources[0].sha256
    assert sources[0].content_bytes == source_bytes
    assert sources[0].file_extension == "xlsx"
    assert len(datasets) == 1
    assert datasets[0].assay_type == "qpcr"


@pytest.mark.asyncio
async def test_chrom_persistence_stores_trace_peaks_archive_members_analysis_dataset_and_deduplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'assay-analytics.db'}"
    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", db_url)

    from sqlalchemy import select
    from services.assay_analytical_store import (
        AnalyticalAnalysisRun,
        AnalyticalArchiveMember,
        AnalyticalDataset,
        AnalyticalSourceFile,
        ChromInjection,
        ChromPeak,
        ChromTracePoint,
        create_analytical_session_factory,
        load_analytical_dataset,
    )
    from services.assay_chrom_persistence import persist_empower_import

    source_bytes = b"real empower zip bytes"
    cdf_bytes = b"cdf member bytes"
    arw_bytes = b"SampleName\tRun001\nInjection Volume\t10.0\n"
    injections = [
        {
            "id": 1,
            "sample_name": "Run001",
            "sample_id": "sample-001",
            "sample_type": "UNKNOWN",
            "sample_role": "unknown",
            "injection_number": "1",
            "run_date": "2026-04-22T10:00:00",
            "injection_volume": 10.0,
            "method_name": "Plasmid Isoform Method",
            "system_name": "Alliance",
            "acquired_by": "analyst",
            "source_file": "Run001.cdf",
            "paired_arw_file": "Run001.arw",
            "source_format": "empower_aia_cdf",
            "detector_name": "UV 260",
            "detector_unit": "AU",
            "retention_unit": "minutes",
            "chromatogram_points": 3,
            "chromatogram": {"time_min": [0.0, 0.5, 1.0], "signal": [0.01, 0.42, 0.02]},
            "primary_peak_area": 1000.0,
            "primary_peak_percent": 80.0,
            "primary_peak_rt": 0.5,
            "peaks": [
                {
                    "peak_id": 1,
                    "peak_name": "supercoiled",
                    "isoform_class": "supercoiled",
                    "retention_time_min": 0.5,
                    "area": 1000.0,
                    "height": 0.42,
                    "area_percent": 80.0,
                    "peak_source": "empower_aia_cdf_native_peak_table",
                }
            ],
        }
    ]
    analytics = {
        "empower_summary": {"n_injections": 1, "source_format_counts": {"empower_aia_cdf": 1}},
        "peak_region_summary": [{"injection_id": 1, "primary_area_percent": 80.0}],
        "peak_table": [{"injection_id": 1, "peak_name": "supercoiled"}],
        "chromatogram_plotly_json": {"data": [{"x": [0.0, 0.5, 1.0], "y": [0.01, 0.42, 0.02]}], "layout": {"title": "Empower Chromatograms"}},
        "qc_plotly_json": {"data": [], "layout": {"title": "Empower Batch QC"}},
        "composition_plotly_json": {"data": [], "layout": {"title": "Plasmid isoform composition"}},
    }

    first = await persist_empower_import(
        import_session_id=42,
        source_files=[
            {
                "filename": "empower-export.zip",
                "content_type": "application/zip",
                "size_bytes": len(source_bytes),
                "sha256": "source-digest",
                "content_bytes": source_bytes,
                "archive_members": [
                    {"member_name": "Run001.cdf", "extension": "cdf", "content_bytes": cdf_bytes, "member_role": "empower_aia_cdf"},
                    {"member_name": "Run001.arw", "extension": "arw", "content_bytes": arw_bytes, "member_role": "empower_arw_metadata"},
                ],
            }
        ],
        injections=injections,
        analytics=analytics,
    )
    second = await persist_empower_import(
        import_session_id=43,
        source_files=[{"filename": "empower-export.zip", "sha256": "source-digest", "content_bytes": source_bytes, "size_bytes": len(source_bytes)}],
        injections=injections,
        analytics=analytics,
    )

    assert second["duplicate_detected"] is True
    assert first["analytical_import_id"] == second["analytical_import_id"]
    assert first["dataset_id"] == second["dataset_id"]

    session_factory = create_analytical_session_factory(db_url)
    async with session_factory() as session:
        sources = (await session.execute(select(AnalyticalSourceFile))).scalars().all()
        archive_members = (await session.execute(select(AnalyticalArchiveMember))).scalars().all()
        datasets = (await session.execute(select(AnalyticalDataset))).scalars().all()
        analysis_runs = (await session.execute(select(AnalyticalAnalysisRun))).scalars().all()
        chrom_injections = (await session.execute(select(ChromInjection))).scalars().all()
        trace_points = (await session.execute(select(ChromTracePoint))).scalars().all()
        peaks = (await session.execute(select(ChromPeak))).scalars().all()

    assert sources[0].content_bytes == source_bytes
    assert {member.member_path for member in archive_members} == {"Run001.cdf", "Run001.arw"}
    assert next(member for member in archive_members if member.member_path == "Run001.arw").content_text.startswith("SampleName")
    assert len(datasets) == 1 and datasets[0].assay_type == "chromatography"
    assert len(analysis_runs) == 1
    assert analysis_runs[0].analysis_kind == "empower_import_review"
    assert analysis_runs[0].plotly_json["chromatogram_plotly_json"]["data"]
    assert len(chrom_injections) == 1
    assert chrom_injections[0].chromatogram_point_count == 3
    assert chrom_injections[0].chromatogram_sha256
    assert chrom_injections[0].raw_trace_sha256 == chrom_injections[0].chromatogram_sha256
    assert len(trace_points) == 3
    assert len(peaks) == 1
    assert peaks[0].isoform_class == "supercoiled"

    dataset_detail = await load_analytical_dataset(first["dataset_id"])
    assert dataset_detail is not None
    assert dataset_detail["chromatography_summary"]["n_injections"] == 1
    assert dataset_detail["chromatography_summary"]["n_peaks"] == 1
    assert dataset_detail["chromatography_injections"][0]["sample_name"] == "Run001"
    assert dataset_detail["chromatography_injections"][0]["trace_point_count"] == 3

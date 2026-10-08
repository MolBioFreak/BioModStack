from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


@pytest.mark.asyncio
async def test_empower_persistence_flushes_assay_run_before_dataset_for_strict_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = os.environ.get("BMS_LIVE_ANALYTICAL_DATABASE_URL")
    if not db_url:
        pytest.skip("Set BMS_LIVE_ANALYTICAL_DATABASE_URL to run strict PostgreSQL FK-order regression")

    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", db_url)

    from services.assay_chrom_persistence import persist_empower_import

    unique = uuid.uuid4().hex
    result = await persist_empower_import(
        import_session_id=9001,
        source_files=[
            {
                "filename": f"strict-order-{unique}.cdf",
                "content_type": "application/x-netcdf",
                "size_bytes": 16,
                "sha256": f"strict-order-source-{unique}",
                "content_bytes": b"real-cdf-bytes",
            }
        ],
        injections=[
            {
                "id": 1,
                "sample_name": "StrictOrder",
                "sample_id": f"sample-{unique}",
                "sample_type": "UNKNOWN",
                "injection_number": "1",
                "run_date": "2026-04-22T10:00:00",
                "source_file": f"strict-order-{unique}.cdf",
                "source_format": "empower_aia_cdf",
                "detector_name": "UV 260",
                "detector_unit": "AU",
                "retention_unit": "minutes",
                "chromatogram": {"time_min": [0.0, 0.5], "signal": [0.01, 0.02]},
                "peaks": [
                    {
                        "peak_id": 1,
                        "peak_name": "supercoiled",
                        "isoform_class": "supercoiled",
                        "retention_time_min": 0.5,
                        "area": 1.0,
                    }
                ],
            }
        ],
        analytics={"empower_summary": {"n_injections": 1, "source_format_counts": {"empower_aia_cdf": 1}}},
    )

    assert result["analytical_import_id"]
    assert result["assay_run_id"]
    assert result["dataset_id"]
    assert result["action"] == "created"


@pytest.mark.asyncio
async def test_qpcr_persistence_flushes_parent_rows_before_dataset_for_strict_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    db_url = os.environ.get("BMS_LIVE_ANALYTICAL_DATABASE_URL")
    if not db_url:
        pytest.skip("Set BMS_LIVE_ANALYTICAL_DATABASE_URL to run strict PostgreSQL FK-order regression")

    monkeypatch.setenv("BMS_ANALYTICAL_DATABASE_URL", db_url)

    from services.assay_analytical_store import persist_qpcr_import_response

    unique = uuid.uuid4().hex
    response = {
        "filename": f"strict-qpcr-{unique}.csv",
        "import_engine": "csv",
        "instrument_format": "QuantStudio/StepOnePlus CSV",
        "n_wells": 4,
        "targets": ["GOI"],
        "samples": ["STD_1", "STD_2", "UNK_1", "NTC"],
        "wells": [
            {"well_position": "A1", "sample_name": "STD_1", "target_name": "GOI", "task": "STANDARD", "cq": 18.0, "quantity": 1000.0},
            {"well_position": "A2", "sample_name": "STD_2", "target_name": "GOI", "task": "STANDARD", "cq": 21.32, "quantity": 100.0},
            {"well_position": "A3", "sample_name": "UNK_1", "target_name": "GOI", "task": "UNKNOWN", "cq": 24.5, "quantity": 10.0},
            {"well_position": "A4", "sample_name": "NTC", "target_name": "GOI", "task": "NTC", "ct_status": "undetermined"},
        ],
        "standard_curve_stats_by_target": {"GOI": {"slope": -3.32, "intercept": 27.96, "r_squared": 0.999, "efficiency_percent": 100.0, "n_points": 2}},
    }
    result = await persist_qpcr_import_response(
        response,
        source_bytes=f"strict qpcr bytes {unique}".encode("utf-8"),
        filename=f"strict-qpcr-{unique}.csv",
        content_type="text/csv",
    )

    assert result["analytical_import_id"]
    assert result["assay_run_id"]
    assert result["dataset_id"]
    assert result["action"] == "created"

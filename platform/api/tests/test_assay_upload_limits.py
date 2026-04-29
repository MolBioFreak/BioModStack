from __future__ import annotations

import re
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import main as bms_api_main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _nginx_size_to_bytes(value: str) -> int:
    match = re.fullmatch(r"(?P<num>\d+)(?P<unit>[kKmMgG]?)", value.strip())
    assert match is not None, f"Unexpected nginx size value: {value!r}"
    number = int(match.group("num"))
    unit = match.group("unit").lower()
    if unit == "k":
        return number * 1024
    if unit == "m":
        return number * 1024 * 1024
    if unit == "g":
        return number * 1024 * 1024 * 1024
    return number


def test_nginx_proxy_allows_large_assay_instrument_uploads() -> None:
    config = (REPO_ROOT / "docker/web/nginx.conf").read_text(encoding="utf-8")
    match = re.search(r"client_max_body_size\s+([^;]+);", config)
    assert match is not None, "nginx must explicitly raise client_max_body_size for EDS/Empower uploads"
    assert _nginx_size_to_bytes(match.group(1)) >= 256 * 1024 * 1024
    assert "proxy_request_buffering off;" in config
    assert re.search(r"proxy_read_timeout\s+300s;", config)
    assert re.search(r"proxy_send_timeout\s+300s;", config)


def test_backend_accepts_multi_megabyte_qpcr_csv_without_synthetic_rows() -> None:
    # This bypasses nginx and proves the FastAPI assay route itself can parse a
    # large real instrument-style CSV payload once the reverse-proxy cap is lifted.
    row = "A1,Sample_001,GOI,UNKNOWN,24.7,\n"
    body = ("Well,Sample Name,Target Name,Task,Ct,Quantity\n" + row * 70000).encode("utf-8")
    assert len(body) > 2 * 1024 * 1024

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-csv",
        files={"file": ("large_quantstudio_export.csv", body, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["n_wells"] == 70000
    assert payload["samples"] == ["Sample_001"]
    assert payload["targets"] == ["GOI"]
    assert payload["import_engine"] == "csv"


def test_empower_native_database_upload_hard_errors_without_fake_rows() -> None:
    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/hplc/empower/import",
        files={"files": ("project_export.mdb", b"not-a-real-empower-database", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "Native Empower database/RAW files are not currently parsed by BMS" in response.json()["detail"]

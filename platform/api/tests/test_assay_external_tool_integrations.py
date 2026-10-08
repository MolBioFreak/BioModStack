from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import main as bms_api_main  # noqa: E402
from services import assay_tool_integrations  # noqa: E402


PROMISED_PYTHON_TOOLS = {
    "mocca2": "mocca2",
    "pydoe3": "pyDOE3",
    "statsmodels": "statsmodels",
    "scikit-learn": "sklearn",
    "bofire": "bofire",
    "qpcr": "qpcr",
    "qslib": "qslib",
    "openpyxl": "openpyxl",
    "xlrd": "xlrd",
}

PROMISED_R_TOOLS = {
    "chromConverter",
    "RDML",
    "qpcR",
    "chipPCR",
    "qPCRtools",
    "RQdeltaCT",
    "tidyqpcr",
    "HTqPCR",
    "DoE.base",
    "FrF2",
    "rsm",
    "AlgDesign",
    "DoE.wrapper",
    "qcc",
    "emmeans",
    "lme4",
    "desirability",
}


def test_promised_python_assay_tools_are_real_importable_packages() -> None:
    registry = assay_tool_integrations.assay_tool_registry()
    by_id = {tool["id"]: tool for tool in registry}

    for tool_id, import_name in PROMISED_PYTHON_TOOLS.items():
        assert tool_id in by_id
        assert by_id[tool_id]["adapter_type"] == "python_package"
        assert by_id[tool_id]["package"] == import_name
        assert by_id[tool_id]["category"] in {"chromatography", "qpcr", "doe_statistics", "import"}
        assert by_id[tool_id]["integration_status"] == "integrated"
        assert importlib.util.find_spec(import_name) is not None, f"{tool_id} / {import_name} is not importable"


def test_promised_r_assay_tools_have_runtime_install_manifest() -> None:
    registry = assay_tool_integrations.assay_tool_registry()
    r_tools = {tool["package"] for tool in registry if tool["adapter_type"] == "r_package"}
    assert PROMISED_R_TOOLS.issubset(r_tools)

    install_script = REPO_ROOT / "docker" / "install_assay_r_packages.R"
    dockerfile = REPO_ROOT / "docker" / "api.Dockerfile"
    assert install_script.exists()
    assert dockerfile.exists()

    install_text = install_script.read_text()
    docker_text = dockerfile.read_text()
    assert "r-base" in docker_text or "r-base-core" in docker_text
    assert "Rscript" in docker_text
    for package_name in PROMISED_R_TOOLS:
        assert package_name in install_text


def test_capabilities_exposes_external_tool_registry_not_in_house_claims() -> None:
    client = TestClient(bms_api_main.app)
    response = client.get("/api/assay-analytics/capabilities")
    assert response.status_code == 200
    payload = response.json()

    assert payload["source_of_truth"] == "BMS API /api/assay-analytics"
    assert payload["not_used"] == "legacy standalone parser service"
    assert "external_tools" in payload

    external_tools = payload["external_tools"]
    categories = {tool["category"] for tool in external_tools}
    assert {"chromatography", "qpcr", "doe_statistics"}.issubset(categories)

    by_id = {tool["id"]: tool for tool in external_tools}
    for tool_id in [*PROMISED_PYTHON_TOOLS.keys(), "chromconverter", "rdml", "qpcR", "rqdeltact", "doe_base", "frf2", "rsm", "algdesign"]:
        assert tool_id in by_id
        assert by_id[tool_id]["integration_status"] == "integrated"
        assert by_id[tool_id]["adapter_type"] in {"python_package", "r_package", "external_api"}


def test_doe_design_endpoint_uses_pydoe3_as_engine() -> None:
    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/doe/design",
        json={"design_type": "box_behnken", "n_factors": 3, "center_points": 2},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_engine"] == "pyDOE3"
    assert payload["engine_package"] == "pyDOE3"
    assert payload["n_runs"] == len(payload["design_matrix"])
    assert payload["metadata"]["design_generator"] == "pyDOE3.bbdesign"


def test_regression_endpoint_uses_statsmodels_engine() -> None:
    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/regression/simple",
        json={"x": [0, 1, 2, 3, 4], "y": [1, 3, 5, 7, 9], "x_name": "dose", "y_name": "response"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_engine"] == "statsmodels.OLS"
    assert payload["coefficients"]["dose"] == pytest.approx(2.0)
    assert payload["coefficients"]["intercept"] == pytest.approx(1.0)
    assert payload["n_obs"] == 5
    assert "aic" in payload
    assert "bic" in payload


def test_chromatography_analysis_uses_mocca2_engine_for_peak_picking() -> None:
    client = TestClient(bms_api_main.app)
    time = [i / 10 for i in range(101)]
    signal = [100 + 2 * t + 150 * __import__("math").exp(-((t - 3.0) ** 2) / 0.06) + 80 * __import__("math").exp(-((t - 7.0) ** 2) / 0.12) for t in time]
    response = client.post(
        "/api/assay-analytics/analysis/hplc/analyze",
        json={"time": time, "signal": signal, "baseline_method": "mocca2_flatfit", "peak_prominence": 0.05},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_engine"] == "MOCCA2"
    assert payload["engine_package"] == "mocca2"
    assert payload["baseline_method"] == "mocca2_flatfit"
    assert payload["n_peaks"] >= 2
    assert all(peak["peak_engine"] == "MOCCA2" for peak in payload["peaks"])

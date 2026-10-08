from __future__ import annotations

import math
import sys
from io import BytesIO
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import assay_analytics  # noqa: E402
import main as bms_api_main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_assay_analytics_router_is_mounted_under_bms_api_namespace() -> None:
    paths = bms_api_main.app.openapi()["paths"]
    assert "/api/assay-analytics/capabilities" in paths
    assert "/api/assay-analytics/analysis/qpcr/standard-curve" in paths
    assert "/api/assay-analytics/analysis/hplc/empower/import" in paths
    assert "/api/assay-analytics/analysis/doe/design" in paths


def test_frontend_payload_contracts_match_assay_router_endpoints() -> None:
    client = TestClient(bms_api_main.app)

    qpcr_curve = client.post(
        "/api/assay-analytics/analysis/qpcr/standard-curve",
        json={
            "concentrations": [1_000_000, 100_000, 10_000, 1_000],
            "cq_values": [18.0, 21.322, 24.644, 27.966],
            "gene": "GOI",
            "unit": "copies/uL",
        },
    )
    assert qpcr_curve.status_code == 200
    qpcr_curve_json = qpcr_curve.json()
    assert qpcr_curve_json["efficiency"] == qpcr_curve_json["efficiency_percent"]
    assert qpcr_curve_json["plotly_json"]["data"]

    qpcr_quant = client.post(
        "/api/assay-analytics/analysis/qpcr/quantify",
        json={
            "std_concentrations": [1_000_000, 100_000, 10_000, 1_000],
            "std_cq_values": [18.0, 21.322, 24.644, 27.966],
            "sample_cq_values": [24.644, 24.744],
            "sample_ids": ["s1", "s2"],
            "unit": "copies/uL",
        },
    )
    assert qpcr_quant.status_code == 200
    qpcr_quant_json = qpcr_quant.json()
    assert qpcr_quant_json["standard_curve"]["r_squared"] > 0.999
    assert qpcr_quant_json["quantities"][0]["sample_id"] == "s1"

    hplc_quant = client.post(
        "/api/assay-analytics/analysis/hplc/quantify",
        json={
            "cal_concentrations": [10, 25, 50, 100],
            "cal_areas": [1000, 2500, 5000, 10000],
            "sample_areas": [3000, 7000],
            "sample_ids": ["a", "b"],
            "unit": "ng/uL",
        },
    )
    assert hplc_quant.status_code == 200
    hplc_quant_json = hplc_quant.json()
    assert hplc_quant_json["curve_stats"]["r_squared"] > 0.999
    assert hplc_quant_json["samples"][0]["id"] == "a"

    dunnett = client.post(
        "/api/assay-analytics/analysis/qpcr/anova-dunnett",
        json={"groups": [[10.2, 9.8, 10.5], [12.1, 11.8, 12.3]], "group_names": ["Control", "Treatment"], "alpha": 0.05},
    )
    assert dunnett.status_code == 200
    assert dunnett.json()["anova_f"] > 0

    capability = client.post(
        "/api/assay-analytics/analysis/capability",
        json={"data": [25.02, 25.05, 24.98, 25.01, 25.03, 24.99, 25.00, 25.04, 24.97, 25.02], "usl": 25.1, "lsl": 24.9, "target": 25.0},
    )
    assert capability.status_code == 200
    assert {"is_capable", "is_centered", "pp", "ppk", "std_within", "std_overall", "plotly_json"}.issubset(capability.json())

    regression = client.post(
        "/api/assay-analytics/analysis/regression/simple",
        json={"x": [1, 2, 3, 4], "y": [2, 4, 6, 8], "x_name": "X", "y_name": "Y"},
    )
    assert regression.status_code == 200
    regression_json = regression.json()
    assert regression_json["coefficients"]["X"] > 0
    assert regression_json["scatter_plot"]["data"]


def test_qpcr_standard_curve_quantification_and_delta_delta_cq_metrics() -> None:
    curve = assay_analytics.qpcr_standard_curve(
        assay_analytics.StandardCurveRequest(
            points=[
                assay_analytics.StandardCurvePoint(cq=18.0, quantity=1_000_000),
                assay_analytics.StandardCurvePoint(cq=21.322, quantity=100_000),
                assay_analytics.StandardCurvePoint(cq=24.644, quantity=10_000),
                assay_analytics.StandardCurvePoint(cq=27.966, quantity=1_000),
            ]
        )
    )

    assert curve["slope"] < 0
    assert curve["r_squared"] > 0.999
    assert 99.0 <= curve["efficiency_percent"] <= 101.0
    assert "MIQE" in " ".join(curve["qc_flags"])

    quantified = assay_analytics.qpcr_quantify(
        assay_analytics.QuantifyRequest(
            slope=curve["slope"],
            intercept=curve["intercept"],
            cq_values=[24.644, 24.744, 24.544],
        )
    )
    assert math.isclose(quantified["mean_quantity"], 10_000, rel_tol=0.08)
    assert quantified["replicate_cv_percent"] < 10

    dd = assay_analytics.qpcr_delta_delta_cq(
        assay_analytics.DeltaDeltaCqRequest(
            rows=[
                assay_analytics.CqRow(sample="ctrl-1", gene="GOI", cq=20.0, group="control"),
                assay_analytics.CqRow(sample="ctrl-1", gene="REF", cq=18.0, group="control"),
                assay_analytics.CqRow(sample="treated-1", gene="GOI", cq=18.0, group="treated"),
                assay_analytics.CqRow(sample="treated-1", gene="REF", cq=18.0, group="treated"),
            ],
            reference_genes=["REF"],
            target_genes=["GOI"],
            control_group="control",
        )
    )
    treated = next(row for row in dd["results"] if row["group"] == "treated")
    assert math.isclose(treated["delta_delta_cq"], -2.0, abs_tol=1e-6)
    assert math.isclose(treated["fold_change"], 4.0, rel_tol=1e-6)


def test_chromatography_peak_detection_and_plasmid_isoform_summary() -> None:
    time = [i / 10 for i in range(0, 101)]
    signal = [
        0.05
        + 10 * math.exp(-0.5 * ((t - 2.0) / 0.12) ** 2)
        + 7 * math.exp(-0.5 * ((t - 4.0) / 0.16) ** 2)
        + 3 * math.exp(-0.5 * ((t - 7.0) / 0.20) ** 2)
        for t in time
    ]

    analyzed = assay_analytics.chromatography_analyze(
        assay_analytics.ChromatographyAnalyzeRequest(
            time=time,
            signal=signal,
            baseline_method="rolling_min",
            peak_prominence=0.5,
            fit_model="gaussian",
        )
    )

    assert len(analyzed["peaks"]) >= 3
    assert analyzed["total_area"] > 0
    assert {"retention_time", "area", "height", "area_percent"}.issubset(analyzed["peaks"][0])

    isoforms = assay_analytics.chromatography_plasmid_isoforms(
        assay_analytics.PlasmidIsoformRequest(
            peaks=analyzed["peaks"],
            windows={"open_circular": [1.5, 2.5], "linear": [3.4, 4.6], "supercoiled": [6.3, 7.8]},
        )
    )
    assert isoforms["total_assigned_area_percent"] > 95
    assert isoforms["isoforms"]["open_circular"]["area_percent"] > 0
    assert isoforms["isoforms"]["linear"]["area_percent"] > 0
    assert isoforms["isoforms"]["supercoiled"]["area_percent"] > 0


def test_doe_design_generation_has_factor_table_and_jmp_style_metadata() -> None:
    design = assay_analytics.doe_design(
        assay_analytics.DoEDesignRequest(
            design_type="full_factorial",
            n_factors=3,
            center_points=2,
        )
    )

    assert len(design["design_matrix"]) == 10
    assert len(design["factors"]) == 3
    assert design["metadata"]["analysis_family"] == "JMP-compatible DOE"
    assert "main effects" in " ".join(design["metadata"]["supported_models"]).lower()


def test_qpcr_excel_upload_accepts_quantstudio_steponeplus_workbooks() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Results"
    sheet.append(["Well", "Sample Name", "Target Name", "Task", "Ct", "Quantity"])
    sheet.append(["A1", "Std_1", "GOI", "STANDARD", 18.1, 1_000_000])
    sheet.append(["A2", "Std_2", "GOI", "STANDARD", 21.4, 100_000])
    sheet.append(["B1", "Sample_1", "GOI", "UNKNOWN", 24.7, None])

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-excel",
        files={"file": ("quantstudio_results.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["n_wells"] == 3
    assert payload["import_engine"] == "openpyxl"
    assert payload["instrument_format"] in {"QuantStudio/StepOnePlus Excel", "generic qPCR Excel"}
    assert "GOI" in payload["targets"]
    assert "Sample_1" in payload["samples"]

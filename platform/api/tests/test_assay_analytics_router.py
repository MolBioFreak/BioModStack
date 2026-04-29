from __future__ import annotations

import math
import sys
import types
import zipfile

import pytest
from io import BytesIO
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import assay_analytics  # noqa: E402
from services import assay_tool_integrations  # noqa: E402
import main as bms_api_main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_assay_analytics_router_is_mounted_under_bms_api_namespace() -> None:
    paths = bms_api_main.app.openapi()["paths"]
    assert "/api/assay-analytics/capabilities" in paths
    assert "/api/assay-analytics/analysis/qpcr/standard-curve" in paths
    assert "/api/assay-analytics/analysis/hplc/empower/import" in paths
    assert "/api/assay-analytics/analysis/doe/design" in paths
    assert "/api/assay-analytics/datasets/seed" not in paths


def test_assay_router_does_not_fabricate_fake_defaults_or_seed_data() -> None:
    source = Path(assay_analytics.__file__).read_text(encoding="utf-8")
    registry_source = Path(assay_tool_integrations.__file__).read_text(encoding="utf-8")
    forbidden_fragments = [
        'f"Sample_{idx + 1}"',
        'or "Unknown"',
        'or "Target"',
        'or "UNKNOWN"',
        'or f"A{idx}"',
        'or f"Injection {row_idx}"',
        '"/datasets/seed"',
        'No fake/example datasets seeded',
        'allow_empty=True',
        'group: str = "default"',
        'f"Group_{i + 1}"',
        'f"Group_{idx + 1}"',
        'request.control_group or',
        'p.get("area") or 0.0',
        'p.get("retention_time") or 0.0',
        'default_factory=lambda: {"open_circular"',
        'row.get(c, 0.0)',
        'optimum_vec = np.zeros',
    ]
    violations = [fragment for fragment in forbidden_fragments if fragment in source]
    assert violations == []
    assert "fallback" not in (source + registry_source).lower()


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
        json={
            "groups": [[10.2, 9.8, 10.5], [12.1, 11.8, 12.3]],
            "group_names": ["Control", "Treatment"],
            "control_group": "Control",
            "alpha": 0.05,
        },
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


def test_hplc_calibration_endpoint_returns_plotly_fit_payload() -> None:
    client = TestClient(bms_api_main.app)

    response = client.post(
        "/api/assay-analytics/analysis/hplc/calibration-curve",
        json={
            "points": [
                {"concentration": 10, "area": 1000},
                {"concentration": 25, "area": 2500},
                {"concentration": 50, "area": 5000},
                {"concentration": 100, "area": 10000},
            ],
            "analyte_name": "SC plasmid",
            "unit": "ug/mL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["r_squared"] > 0.999
    assert payload["plotly_json"]["data"][0]["name"] == "Calibration standards"
    assert payload["plotly_json"]["data"][1]["name"] == "Linear fit"
    assert payload["plotly_json"]["layout"]["xaxis"]["title"] == "Concentration (ug/mL)"
    assert payload["plotly_json"]["layout"]["yaxis"]["title"] == "Peak area"


def test_qpcr_and_hplc_quantification_require_real_sample_ids() -> None:
    client = TestClient(bms_api_main.app)

    qpcr_response = client.post(
        "/api/assay-analytics/analysis/qpcr/quantify",
        json={
            "std_concentrations": [1_000_000, 100_000, 10_000],
            "std_cq_values": [18.0, 21.322, 24.644],
            "sample_cq_values": [24.644],
        },
    )
    assert qpcr_response.status_code == 400
    assert "sample_ids" in qpcr_response.json()["detail"]

    hplc_response = client.post(
        "/api/assay-analytics/analysis/hplc/quantify",
        json={
            "cal_concentrations": [10, 25, 50],
            "cal_areas": [1000, 2500, 5000],
            "sample_areas": [3000],
        },
    )
    assert hplc_response.status_code == 400
    assert "sample_ids" in hplc_response.json()["detail"]


def test_qpcr_import_rejects_missing_sample_or_target_metadata() -> None:
    client = TestClient(bms_api_main.app)
    bad_csv = b"Well,Ct\nA1,24.7\n"
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-csv",
        files={"file": ("missing_metadata.csv", bad_csv, "text/csv")},
    )
    assert response.status_code == 400
    assert "Sample" in response.json()["detail"] or "Target" in response.json()["detail"]

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Results"
    sheet.append(["Well", "Ct"])
    sheet.append(["A1", 24.7])
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    excel_response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-excel",
        files={"file": ("missing_metadata.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert excel_response.status_code == 400
    assert "Sample" in excel_response.json()["detail"] or "Target" in excel_response.json()["detail"]


def test_qpcr_csv_accepts_delta_delta_cq_table_without_fabricating_wells() -> None:
    client = TestClient(bms_api_main.app)
    ddct_csv = b"sample,group,gene,Cq\nctrl-1,control,GOI,20.1\nctrl-1,control,REF,18.2\n"
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-csv",
        files={"file": ("click_qpcr_template.csv", ddct_csv, "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_wells"] == 2
    assert payload["instrument_format"] == "qPCR Cq table CSV"
    assert payload["wells"][0]["well_position"] is None
    assert payload["wells"][0]["task"] is None
    assert payload["wells"][0]["group"] == "control"
    assert "GOI" in payload["targets"]
    assert "ctrl-1" in payload["samples"]


def test_qpcr_workbook_prefers_instrument_well_label_over_numeric_well_position() -> None:
    parsed = assay_analytics._row_to_qpcr_well(
        {
            "Well": "A1",
            "Well Position": 1.0,
            "Sample Name": "STD 3000",
            "Target Name": "E Coli",
            "Task": "STANDARD",
            "Ct": 18.9481,
            "Quantity": 3000,
        },
        1,
        "QuantStudio Results sheet",
    )

    assert parsed["well_position"] == "A1"
    assert parsed["ct"] == 18.9481
    assert parsed["quantity"] == 3000


def test_quantstudio_eds_plate_setup_concentration_is_not_result_quantity_for_unknowns() -> None:
    plate_setup_xml = b'''<PlateSetup>
        <Columns>12</Columns>
        <Rows>8</Rows>
        <PassiveReferenceDye>ROX</PassiveReferenceDye>
        <FeatureMap>
          <Feature><Id>sample</Id></Feature>
          <FeatureValue><Index>0</Index><FeatureItem><Sample><Name>unknown_sample</Name></Sample></FeatureItem></FeatureValue>
          <FeatureValue><Index>1</Index><FeatureItem><Sample><Name>std_sample</Name></Sample></FeatureItem></FeatureValue>
        </FeatureMap>
        <FeatureMap>
          <Feature><Id>detector-task</Id></Feature>
          <FeatureValue><Index>0</Index><FeatureItem><DetectorTaskList><DetectorTask><Task>UNKNOWN</Task><Concentration>1.0</Concentration><Detector><Name>E Coli</Name><Reporter>FAM</Reporter><Quencher>NFQ</Quencher></Detector></DetectorTask></DetectorTaskList></FeatureItem></FeatureValue>
          <FeatureValue><Index>1</Index><FeatureItem><DetectorTaskList><DetectorTask><Task>STANDARD</Task><Concentration>3000</Concentration><Detector><Name>E Coli</Name><Reporter>FAM</Reporter><Quencher>NFQ</Quencher></Detector></DetectorTask></DetectorTaskList></FeatureItem></FeatureValue>
        </FeatureMap>
    </PlateSetup>'''
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("apldbio/sds/plate_setup.xml", plate_setup_xml)

    wells, summary, available_data = assay_analytics._parse_quantstudio_eds_zip_xml(buffer.getvalue(), qslib_error="test qslib unavailable")

    unknown = next(well for well in wells if well["task"] == "UNKNOWN")
    standard = next(well for well in wells if well["task"] == "STANDARD")
    assert unknown["quantity"] is None
    assert standard["quantity"] == 3000
    assert summary["ct_values_are_authoritative"] is False
    assert "Excel Results" in summary["recommendation"]
    assert "plate_setup.xml" in available_data


def test_qpcr_csv_accepts_quantstudio_steponeplus_results_export_with_preamble_and_cq_mean() -> None:
    client = TestClient(bms_api_main.app)
    instrument_csv = b'''Applied Biosystems QuantStudio Design & Analysis Software v2.7\nExperiment Name,16Apr25 pRC9\nBlock Type,96-Well\n\nResults\nWell,Well Position,Omit,Sample Name,Target Name,Task,Reporter,Quencher,Cq,Cq Mean,Cq SD,Starting Quantity (SQ)\n1,A01,false,STD_1e6,GOI,STANDARD,FAM,NFQ-MGB,18.100,18.120,0.030,1000000\n2,A02,false,STD_1e5,GOI,STANDARD,FAM,NFQ-MGB,,21.400,0.040,100000\n13,B01,false,Sample_1,GOI,UNKNOWN,FAM,NFQ-MGB,24.700,,,,\n14,B02,false,NTC,GOI,NTC,FAM,NFQ-MGB,Undetermined,,,,\n'''

    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-csv",
        files={"file": ("quantstudio_results_export.csv", instrument_csv, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["instrument_format"] == "QuantStudio/StepOnePlus CSV"
    assert payload["n_wells"] == 4
    assert payload["wells"][0]["well_position"] == "A1"
    assert payload["wells"][1]["well_position"] == "A2"
    assert payload["wells"][1]["ct"] == 21.4
    assert payload["wells"][1]["quantity"] == 100000
    assert payload["wells"][2]["well_position"] == "B1"
    assert payload["wells"][3]["ct_status"] == "undetermined"
    assert payload["standard_curve_stats_by_target"]["GOI"]["n_points"] == 2
    assert payload["standard_curve_plotly_json"]["data"]
    assert payload["results_plotly_json"]["data"][0]["z"][0][0] == 18.1


def test_anova_dunnett_requires_explicit_real_group_names_and_control_group() -> None:
    client = TestClient(bms_api_main.app)

    missing_names = client.post(
        "/api/assay-analytics/analysis/qpcr/anova-dunnett",
        json={"groups": [[10.2, 9.8], [12.1, 11.8]], "control_group": "Control", "alpha": 0.05},
    )
    assert missing_names.status_code == 400
    assert "group_names" in missing_names.json()["detail"]

    missing_control = client.post(
        "/api/assay-analytics/analysis/qpcr/anova-dunnett",
        json={"groups": [[10.2, 9.8], [12.1, 11.8]], "group_names": ["Control", "Treatment"], "alpha": 0.05},
    )
    assert missing_control.status_code in {400, 422}


def test_plasmid_isoform_analysis_requires_explicit_windows_and_finite_peak_fields() -> None:
    client = TestClient(bms_api_main.app)

    missing_windows = client.post(
        "/api/assay-analytics/analysis/hplc/plasmid/isoforms",
        json={"peaks": [{"retention_time": 2.0, "area": 100.0}]},
    )
    assert missing_windows.status_code == 422

    missing_peak_area = client.post(
        "/api/assay-analytics/analysis/hplc/plasmid/isoforms",
        json={"peaks": [{"retention_time": 2.0}], "windows": {"open_circular": [1.5, 2.5]}},
    )
    assert missing_peak_area.status_code == 400
    assert "peak row" in missing_peak_area.json()["detail"]


def test_qpcr_upload_response_uses_target_specific_plate_heatmaps_and_experimental_curve_points() -> None:
    wells = [
        {"well_position": "A1", "sample_name": "STD 1000", "target_name": "GOI", "task": "STANDARD", "ct": 18.0, "quantity": 1000.0},
        {"well_position": "A1", "sample_name": "STD 1000", "target_name": "IPC", "task": "UNKNOWN", "ct": 25.0, "quantity": 1.0},
        {"well_position": "B1", "sample_name": "STD 100", "target_name": "GOI", "task": "STANDARD", "ct": 21.3, "quantity": 100.0},
        {"well_position": "B1", "sample_name": "STD 100", "target_name": "IPC", "task": "UNKNOWN", "ct": 25.2, "quantity": 1.0},
        {"well_position": "C1", "sample_name": "Experimental A", "target_name": "GOI", "task": "UNKNOWN", "ct": 24.6, "quantity": 1.0},
        {"well_position": "C1", "sample_name": "Experimental A", "target_name": "IPC", "task": "UNKNOWN", "ct": 25.4, "quantity": 1.0},
    ]

    payload = assay_analytics._build_qpcr_upload_response(wells, "multiplex.csv", "csv", "QuantStudio/StepOnePlus CSV")

    heatmap = payload["results_plotly_json"]
    assert heatmap["layout"]["yaxis"]["autorange"] == "reversed"
    assert heatmap["layout"]["updatemenus"][0]["buttons"][0]["label"] == "GOI"
    heatmap_by_name = {trace["name"]: trace for trace in heatmap["data"]}
    assert set(heatmap_by_name) == {"GOI Ct", "IPC Ct"}
    assert heatmap_by_name["GOI Ct"]["z"][0][0] == 18.0
    assert heatmap_by_name["IPC Ct"]["z"][0][0] == 25.0
    assert heatmap_by_name["GOI Ct"]["visible"] is True
    assert heatmap_by_name["IPC Ct"]["visible"] is False

    assert payload["standard_curve_stats"]["target_name"] == "GOI"
    assert payload["standard_curve_stats_by_target"]["GOI"]["n_points"] == 2
    curve_trace_names = [trace["name"] for trace in payload["standard_curve_plotly_json"]["data"]]
    assert "GOI standards" in curve_trace_names
    assert "GOI fit" in curve_trace_names
    assert "GOI experimentals" in curve_trace_names
    experimental_trace = next(trace for trace in payload["standard_curve_plotly_json"]["data"] if trace["name"] == "GOI experimentals")
    assert experimental_trace["text"] == ["C1 Experimental A"]
    assert payload["assay_summary"]["quantities"][0]["sample_name"] == "Experimental A"



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


def test_qpcr_excel_upload_accepts_legacy_xls_via_xlrd(monkeypatch) -> None:
    class FakeSheet:
        name = "Results"
        nrows = 4
        ncols = 6

        def row_values(self, idx: int) -> list[object]:
            return [
                ["Well", "Sample Name", "Target Name", "Task", "Ct", "Quantity"],
                ["A1", "Std_1", "GOI", "STANDARD", 18.1, 1_000_000],
                ["H10", "NTC", "GOI", "NTC", "Undetermined", ""],
                ["B1", "Sample_1", "GOI", "UNKNOWN", 24.7, ""],
            ][idx]

    class FakeBook:
        nsheets = 1

        def sheet_names(self) -> list[str]:
            return ["Results"]

        def sheets(self) -> list[FakeSheet]:
            return [FakeSheet()]

    fake_xlrd = types.SimpleNamespace(open_workbook=lambda file_contents, on_demand=True: FakeBook())
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-excel",
        files={"file": ("quantstudio_results.xls", b"legacy-biff-xls-bytes", "application/vnd.ms-excel")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["n_wells"] == 3
    assert payload["import_engine"] == "xlrd"
    assert payload["instrument_format"] == "QuantStudio/StepOnePlus legacy XLS"
    assert payload["wells"][1]["ct"] is None
    assert payload["wells"][1]["ct_status"] == "undetermined"
    assert "GOI" in payload["targets"]
    assert "Sample_1" in payload["samples"]


def test_qpcr_eds_upload_uses_direct_zip_xml_reader_when_qslib_rejects_schema(monkeypatch) -> None:
    class RejectingExperiment:
        @staticmethod
        def from_file(_stream: object) -> object:
            raise ValueError("missing field `CustomProperty`")

    fake_qslib = types.SimpleNamespace(Experiment=RejectingExperiment)
    monkeypatch.setitem(sys.modules, "qslib", fake_qslib)

    plate_setup = b"""
    <Plate>
      <RowCount>8</RowCount>
      <ColumnCount>12</ColumnCount>
      <FeatureMap>
        <Feature><Id>sample</Id><Name>sample</Name></Feature>
        <FeatureValue><Index>0</Index><FeatureItem><Sample><Name>STD 3000</Name></Sample></FeatureItem></FeatureValue>
      </FeatureMap>
      <FeatureMap>
        <Feature><Id>detector-task</Id><Name>detector-task</Name></Feature>
        <FeatureValue><Index>0</Index><FeatureItem><DetectorTaskList><DetectorTask><Task>STANDARD</Task><Concentration>3000.0</Concentration><Detector><Name>E Coli</Name><Reporter>FAM</Reporter><Quencher>NFQ-MGB</Quencher></Detector></DetectorTask></DetectorTaskList></FeatureItem></FeatureValue>
      </FeatureMap>
    </Plate>
    """
    multicomponent = b"""
    <MulticomponentData>
      <WellCount>96</WellCount>
      <CycleCount>5</CycleCount>
      <DyeData WellIndex="0"><DyeList>[FAM,ROX,VIC]</DyeList></DyeData>
      <SignalData WellIndex="0">
        <CycleData>[1.0,1.0,1.1,1.4,1.8]</CycleData>
        <CycleData>[1.0,1.0,1.0,1.0,1.0]</CycleData>
        <CycleData>[0.1,0.1,0.1,0.1,0.1]</CycleData>
      </SignalData>
    </MulticomponentData>
    """
    generic = b'{"threshold":0.2,"defaultBaseLineStart":1,"defaultBaseLineEnd":2,"ctSettingsDetailsDTOS":[{"target":"E Coli","threshold":0.2,"baselineStart":1,"baselineEnd":2}]}'
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("apldbio/sds/plate_setup.xml", plate_setup)
        archive.writestr("apldbio/sds/multicomponentdata.xml", multicomponent)
        archive.writestr("apldbio/sds/generic_properties.json", generic)
    buffer.seek(0)

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-eds",
        files={"file": ("quantstudio.eds", buffer.getvalue(), "application/octet-stream")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["import_engine"] == "quantstudio_eds_zip_xml"
    assert payload["instrument_format"] == "QuantStudio EDS ZIP/XML"
    assert payload["n_wells"] == 1
    assert payload["wells"][0]["well_position"] == "A1"
    assert payload["wells"][0]["sample_name"] == "STD 3000"
    assert payload["wells"][0]["target_name"] == "E Coli"
    assert math.isclose(payload["wells"][0]["ct"], 3.3333333333333335)
    assert payload["wells"][0]["ct_source"] == "multicomponentdata_linear_baseline_linear_threshold"
    assert payload["wells"][0]["reporter"] == "FAM"
    assert payload["eds_summary"]["cycle_count"] == 5
    assert payload["eds_summary"]["ct_values_calculated_from_multicomponentdata"] == 1
    assert payload["eds_summary"]["dyes"] == ["FAM", "ROX", "VIC"]
    assert payload["amplification_plotly_json"]["data"]



def _require_real_quantstudio_eds_xls_pair() -> tuple[Path, Path]:
    eds_path = Path("/home/dalab/Downloads/16Apr25_pRC9 products0.7710199216467184.eds")
    xls_path = Path("/home/dalab/Downloads/16Apr25_pRC9 products.xls")
    if not eds_path.exists() or not xls_path.exists():
        pytest.skip("real QuantStudio EDS/XLS pair is not present on this workstation")
    return eds_path, xls_path


def test_real_quantstudio_eds_matches_real_excel_upload_for_all_result_rows_when_pair_present() -> None:
    eds_path, xls_path = _require_real_quantstudio_eds_xls_pair()

    client = TestClient(bms_api_main.app)
    eds_response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-eds",
        files={"file": (eds_path.name, eds_path.read_bytes(), "application/octet-stream")},
    )
    xls_response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-excel",
        files={"file": (xls_path.name, xls_path.read_bytes(), "application/vnd.ms-excel")},
    )

    assert xls_response.status_code == 200, xls_response.text
    assert eds_response.status_code == 200, eds_response.text
    eds_payload = eds_response.json()
    xls_payload = xls_response.json()

    assert eds_payload["n_wells"] == xls_payload["n_wells"] == 132
    assert eds_payload["targets"] == xls_payload["targets"] == ["E Coli", "IPC"]
    assert eds_payload["eds_summary"]["ct_result_table_detected"] is False
    assert eds_payload["eds_summary"]["ct_values_are_authoritative"] is False
    assert eds_payload["eds_summary"]["ct_algorithm"] == "reporter/passive Rn + auto-linear-baseline + Savitzky-Golay smoothing + cubic threshold interpolation"

    eds_rows = {(row["well_position"], row["target_name"]): row for row in eds_payload["wells"]}
    xls_rows = {(row["well_position"], row["target_name"]): row for row in xls_payload["wells"]}
    assert set(eds_rows) == set(xls_rows)

    finite_ct_errors: list[float] = []
    for key, xls_row in xls_rows.items():
        eds_row = eds_rows[key]
        assert eds_row["sample_name"] == xls_row["sample_name"], key
        assert eds_row["task"] == xls_row["task"], key
        if xls_row["ct"] is None:
            assert eds_row["ct"] is None, key
        else:
            assert eds_row["ct"] is not None, key
            finite_ct_errors.append(float(eds_row["ct"]) - float(xls_row["ct"]))
        if str(xls_row["task"]).upper() == "STANDARD":
            assert eds_row["quantity"] == pytest.approx(xls_row["quantity"], rel=5e-4, abs=1e-4), key
        else:
            # This EDS archive carries standard input quantities in plate_setup.xml but no scalar
            # result Quantity table for unknowns; upload code must not copy setup concentration
            # into UNKNOWN result quantities.
            assert eds_row["quantity"] is None, key

    rmse = math.sqrt(sum(error * error for error in finite_ct_errors) / len(finite_ct_errors))
    assert rmse < 0.13
    assert max(abs(error) for error in finite_ct_errors) < 1.0


def test_real_quantstudio_eds_standard_curve_matches_excel_known_curve_when_pair_present() -> None:
    eds_path, _xls_path = _require_real_quantstudio_eds_xls_pair()

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-eds",
        files={"file": (eds_path.name, eds_path.read_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    curve = response.json()["standard_curve_stats_by_target"]["E Coli"]
    assert curve["n_points"] == 14
    assert curve["quantities"] == pytest.approx(
        [0.3, 0.3, 3.0, 3.0, 3.0, 30.0, 30.0, 30.0, 300.0, 300.0, 300.0, 3000.0, 3000.0, 3000.0],
        abs=1e-12,
    )
    expected_ct_values = [31.8838, 31.7322, 28.6725, 28.797, 28.7141, 25.6912, 25.6624, 25.6773, 22.2669, 22.3377, 22.3685, 18.9481, 18.9256, 19.059]
    ct_errors = [observed - expected for observed, expected in zip(curve["ct_values"], expected_ct_values)]
    assert math.sqrt(sum(error * error for error in ct_errors) / len(ct_errors)) < 0.12
    assert max(abs(error) for error in ct_errors) < 0.25
    assert curve["slope"] == pytest.approx(-3.2154222222222217, abs=0.02)
    assert curve["intercept"] == pytest.approx(30.26150739623126, abs=0.15)
    assert curve["r_squared"] == pytest.approx(0.999293033632272, abs=5e-4)
    assert curve["efficiency_percent"] == pytest.approx(104.64500426971188, abs=1.5)


def test_real_quantstudio_eds_import_calculates_ct_from_multicomponent_curves() -> None:
    eds_path = Path("/home/dalab/Downloads/16Apr25_pRC9 products0.7710199216467184.eds")
    if not eds_path.exists():
        pytest.skip("real QuantStudio EDS file is not present on this workstation")

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/qpcr/upload-eds",
        files={"file": (eds_path.name, eds_path.read_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["import_engine"] == "quantstudio_eds_zip_xml"
    assert payload["n_wells"] == 132
    assert payload["eds_summary"]["cycle_count"] == 40
    assert payload["eds_summary"]["ct_values_calculated_from_multicomponentdata"] >= 129
    assert payload["assay_summary"]["standard_curves"]["E Coli"]["n_points"] >= 14
    e_coli_a1 = next(row for row in payload["wells"] if row["well_position"] == "A1" and row["target_name"] == "E Coli")
    ipc_a1 = next(row for row in payload["wells"] if row["well_position"] == "A1" and row["target_name"] == "IPC")
    assert 18.7 <= e_coli_a1["ct"] <= 19.1
    assert ipc_a1["ct"] == pytest.approx(25.9646, abs=0.02)
    assert payload["amplification_plotly_json"]["data"]
    assert payload["standard_curve_plotly_json"]["data"]

    heatmap = payload["results_plotly_json"]
    assert heatmap["layout"]["yaxis"]["autorange"] == "reversed"
    heatmap_by_name = {trace["name"]: trace for trace in heatmap["data"]}
    assert set(heatmap_by_name) == {"E Coli Ct", "IPC Ct"}
    assert heatmap_by_name["E Coli Ct"]["visible"] is True
    assert heatmap_by_name["IPC Ct"]["visible"] is False
    assert math.isclose(heatmap_by_name["E Coli Ct"]["z"][0][0], e_coli_a1["ct"], rel_tol=1e-9)
    assert math.isclose(heatmap_by_name["IPC Ct"]["z"][0][0], ipc_a1["ct"], rel_tol=1e-9)

    assert payload["standard_curve_stats"]["target_name"] == "E Coli"
    standard_curve = payload["standard_curve_plotly_json"]
    curve_trace_names = [trace["name"] for trace in standard_curve["data"]]
    assert {"E Coli standards", "E Coli fit", "E Coli experimentals"}.issubset(set(curve_trace_names))
    standards_trace = next(trace for trace in standard_curve["data"] if trace["name"] == "E Coli standards")
    fit_trace = next(trace for trace in standard_curve["data"] if trace["name"] == "E Coli fit")
    experimental_trace = next(trace for trace in standard_curve["data"] if trace["name"] == "E Coli experimentals")
    assert standards_trace["marker"]["color"] == "#38bdf8"
    assert standards_trace["marker"]["line"]["color"] == "#e0f2fe"
    assert standards_trace["marker"]["size"] >= 11
    assert standards_trace["hovertemplate"].endswith("<extra></extra>")
    assert fit_trace["line"]["color"] == "#38bdf8"
    assert fit_trace["line"]["width"] >= 3
    assert experimental_trace["marker"]["color"] == "#fb923c"
    assert standard_curve["layout"]["plot_bgcolor"] == "rgba(15, 23, 42, 0.35)"
    assert standard_curve["layout"]["xaxis"]["showgrid"] is True
    assert standard_curve["layout"]["yaxis"]["showgrid"] is True
    assert "A4 DevRun001 1x" in experimental_trace["text"]
    assert any(row["sample_name"] == "DevRun001 1x" for row in payload["assay_summary"]["quantities"])


def test_real_empower_db_export_zip_imports_cdf_arw_chromatograms_and_native_peaks() -> None:
    zip_path = Path("/home/dalab/Downloads/DB export testing.zip")
    if not zip_path.exists():
        pytest.skip("real Empower DB export ZIP is not present on this workstation")

    client = TestClient(bms_api_main.app)
    response = client.post(
        "/api/assay-analytics/analysis/hplc/empower/import",
        files=[("files", (zip_path.name, zip_path.read_bytes(), "application/zip"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["n_injections"] == 62
    assert payload["source_format_counts"]["empower_aia_cdf"] == 62
    first = payload["injections"][0]
    assert first["sample_name"] == "12JAN25_Gagpol SST_001"
    assert first["chromatogram_points"] == 24001
    assert first["native_peak_count"] >= 2
    assert first["total_area"] > 0
    assert 16.0 <= first["primary_peak_rt"] <= 16.2
    assert first["primary_peak_area"] > 1_000_000
    assert first["chromatogram"]["time_min"]
    assert first["peaks"][0]["peak_source"] == "empower_aia_cdf_native_peak_table"
    assert payload["chromatogram_plotly_json"]["data"]

    summary = payload["empower_summary"]
    assert summary["n_injections"] == 62
    assert summary["n_chromatograms"] == 62
    assert summary["total_peak_rows"] == 124
    assert summary["sample_role_counts"]["sst"] == 3
    assert summary["sample_role_counts"]["blank"] == 8
    assert sum(summary["sample_role_counts"].values()) == payload["n_injections"]
    assert summary["flagged_injection_count"] > 0
    assert "low_primary_percent" in summary["flag_counts"]
    assert "rt_drift" in summary["flag_counts"]
    assert len(payload["peak_table"]) == summary["total_peak_rows"]
    assert payload["peak_table"][0]["injection_id"] == first["id"]
    assert payload["peak_table"][0]["sample_name"] == first["sample_name"]
    assert payload["peak_table"][0]["area_percent"] == first["peaks"][0]["area_percent"]
    assert len(payload["peak_region_summary"]) == 62
    first_regions = payload["peak_region_summary"][0]
    assert math.isclose(first_regions["primary_area_percent"], first["primary_peak_percent"], rel_tol=1e-9)
    assert first_regions["post_primary_area_percent"] > 0
    assert payload["qc_plotly_json"]["layout"]["title"] == "Empower Batch QC"
    qc_trace_names = {trace["name"] for trace in payload["qc_plotly_json"]["data"]}
    assert {"Primary % area", "Primary RT", "Total area", "Peak count"}.issubset(qc_trace_names)
    composition_trace_names = {trace["name"] for trace in payload["composition_plotly_json"]["data"]}
    assert {"Pre-primary area %", "Primary area %", "Post-primary area %"}.issubset(composition_trace_names)



def test_real_empower_db_export_folder_upload_groups_cdf_arw_pairs() -> None:
    zip_path = Path("/home/dalab/Downloads/DB export testing.zip")
    if not zip_path.exists():
        pytest.skip("real Empower DB export ZIP is not present on this workstation")

    files = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in sorted(archive.namelist()):
            if member.endswith("/") or not member.lower().endswith((".cdf", ".arw")):
                continue
            # Browser folder uploads arrive as many file parts, not as the original ZIP.
            # The importer must still pair each CDF chromatogram with its ARW metadata
            # instead of creating duplicate CDF + ARW injection rows.
            files.append(("files", (Path(member).name, archive.read(member), "application/octet-stream")))

    client = TestClient(bms_api_main.app)
    response = client.post("/api/assay-analytics/analysis/hplc/empower/import", files=files)

    assert response.status_code == 200
    payload = response.json()
    assert payload["n_injections"] == 62
    assert payload["source_format_counts"]["empower_aia_cdf"] == 62
    assert "empower_arw_text_chromatogram" not in payload["source_format_counts"]
    first = payload["injections"][0]
    assert first["source_file"].endswith("Mass Download Test33706.cdf")
    assert first["paired_arw_file"].endswith("Mass Download Test33706.arw")
    assert first["method_name"] == "01Apr25_Gradient"
    assert first["chromatogram_points"] == 24001
    assert first["native_peak_count"] >= 2
    assert first["primary_peak_area"] > 1_000_000
    assert payload["chromatogram_plotly_json"]["data"]

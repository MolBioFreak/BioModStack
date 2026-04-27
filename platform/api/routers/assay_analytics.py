from __future__ import annotations

import csv
import io
import math
import statistics
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scipy import stats
from scipy.signal import find_peaks, peak_widths

from services.assay_tool_integrations import assay_tool_registry, tools_by_category

router = APIRouter()

# BioModStack-native assay analytics router.
# It intentionally mirrors the prototype analysis-module API paths under
# /api/assay-analytics so the migrated React panels do not point at the defunct
# Document Parser service.


def _finite_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_json_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_clean(value.tolist())
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def _mean(values: Iterable[float]) -> float:
    vals = [float(v) for v in values]
    return float(np.mean(vals)) if vals else 0.0


def _std(values: Iterable[float], ddof: int = 1) -> float:
    vals = [float(v) for v in values]
    if len(vals) <= ddof:
        return 0.0
    return float(np.std(vals, ddof=ddof))


def _cv_percent(values: Iterable[float]) -> float:
    vals = [float(v) for v in values if _finite_float(v) is not None]
    if not vals:
        return 0.0
    m = _mean(vals)
    return abs(_std(vals) / m * 100.0) if m else 0.0


def _linear_fit(x: List[float], y: List[float]) -> Dict[str, float]:
    if len(x) != len(y) or len(x) < 2:
        raise HTTPException(status_code=400, detail="At least two paired finite points are required")
    slope, intercept, r_value, p_value, stderr = stats.linregress(x, y)
    fitted = [slope * xi + intercept for xi in x]
    residuals = [yi - fi for yi, fi in zip(y, fitted)]
    return _json_clean(
        {
            "slope": slope,
            "intercept": intercept,
            "r_squared": r_value**2,
            "p_value": p_value,
            "std_err": stderr,
            "residual_std": _std(residuals),
        }
    )


class StandardCurvePoint(BaseModel):
    cq: float
    quantity: float


class StandardCurveRequest(BaseModel):
    points: List[StandardCurvePoint] = Field(default_factory=list)
    concentrations: List[float] = Field(default_factory=list)
    cq_values: List[float] = Field(default_factory=list)
    gene: Optional[str] = None
    unit: Optional[str] = None
    log_base: float = 10.0


class QuantifyRequest(BaseModel):
    slope: Optional[float] = None
    intercept: Optional[float] = None
    cq_values: List[float] = Field(default_factory=list)
    std_concentrations: List[float] = Field(default_factory=list)
    std_cq_values: List[float] = Field(default_factory=list)
    sample_cq_values: List[float] = Field(default_factory=list)
    sample_ids: List[str] = Field(default_factory=list)
    unit: str = "quantity"
    log_base: float = 10.0


class CqRow(BaseModel):
    sample: str
    gene: str
    cq: float
    group: str = "default"


class DeltaCqRequest(BaseModel):
    rows: List[CqRow] = Field(alias="data", default_factory=list)
    reference_genes: List[str]
    target_genes: List[str]

    class Config:
        populate_by_name = True


class DeltaDeltaCqRequest(DeltaCqRequest):
    control_group: str


class AnovaDunnettRequest(BaseModel):
    # The migrated React panel posts groups as an array-of-arrays plus group_names,
    # while the original direct API accepted a dict plus explicit control_group.
    groups: Any
    group_names: List[str] = Field(default_factory=list)
    control_group: Optional[str] = None
    alpha: float = 0.05


def _standard_curve_points(request: StandardCurveRequest) -> List[StandardCurvePoint]:
    if request.points:
        return [p for p in request.points if p.quantity > 0 and math.isfinite(p.quantity) and math.isfinite(p.cq)]
    if len(request.concentrations) != len(request.cq_values):
        raise HTTPException(status_code=400, detail="concentrations and cq_values must have the same length")
    points: List[StandardCurvePoint] = []
    for quantity, cq in zip(request.concentrations, request.cq_values):
        q = _finite_float(quantity)
        c = _finite_float(cq)
        if q is not None and q > 0 and c is not None:
            points.append(StandardCurvePoint(quantity=q, cq=c))
    return points


@router.post("/analysis/qpcr/standard-curve")
def qpcr_standard_curve(request: StandardCurveRequest) -> Dict[str, Any]:
    points = _standard_curve_points(request)
    if len(points) < 2:
        raise HTTPException(status_code=400, detail="At least two standard points with positive quantity are required")
    x = [math.log(p.quantity, request.log_base) for p in points]
    y = [p.cq for p in points]
    fit = _linear_fit(x, y)
    slope = fit["slope"]
    efficiency = ((request.log_base ** (-1.0 / slope)) - 1.0) * 100.0 if slope else 0.0
    qc_flags: List[str] = []
    if not (-3.9 <= slope <= -2.9):
        qc_flags.append("MIQE slope outside common -3.9 to -2.9 range")
    if not (90 <= efficiency <= 110):
        qc_flags.append("MIQE efficiency outside 90-110% range")
    else:
        qc_flags.append("MIQE efficiency within 90-110% range")
    if fit["r_squared"] < 0.98:
        qc_flags.append("R² below 0.98")
    return _json_clean(
        {
            **fit,
            "efficiency_percent": efficiency,
            "efficiency": efficiency,
            "gene": request.gene,
            "unit": request.unit,
            "n_points": len(points),
            "x_log_quantity": x,
            "concentrations": [p.quantity for p in points],
            "cq_values": y,
            "qc_flags": qc_flags,
            "plotly_json": {
                "data": [
                    {"type": "scatter", "mode": "markers", "x": x, "y": y, "name": "Standards"},
                    {
                        "type": "scatter",
                        "mode": "lines",
                        "x": x,
                        "y": [fit["slope"] * xi + fit["intercept"] for xi in x],
                        "name": "Fit",
                    },
                ],
                "layout": {"title": "qPCR Standard Curve", "xaxis": {"title": "log10 quantity"}, "yaxis": {"title": "Cq"}},
            },
        }
    )


@router.post("/analysis/qpcr/quantify")
def qpcr_quantify(request: QuantifyRequest) -> Dict[str, Any]:
    if request.std_concentrations and request.std_cq_values:
        curve = qpcr_standard_curve(
            StandardCurveRequest(
                concentrations=request.std_concentrations,
                cq_values=request.std_cq_values,
                unit=request.unit,
                log_base=request.log_base,
            )
        )
        sample_cq_values = [float(v) for v in (request.sample_cq_values or request.cq_values) if math.isfinite(float(v))]
        if not sample_cq_values:
            raise HTTPException(status_code=400, detail="At least one finite sample Cq value is required")
        slope = float(curve["slope"])
        intercept = float(curve["intercept"])
        min_std = min(request.std_concentrations)
        max_std = max(request.std_concentrations)
        sample_rows = []
        quantities = []
        for idx, cq in enumerate(sample_cq_values):
            quantity = request.log_base ** ((cq - intercept) / slope)
            quantities.append(quantity)
            sample_id = request.sample_ids[idx] if idx < len(request.sample_ids) and request.sample_ids[idx] else f"Sample_{idx + 1}"
            within_range = min_std <= quantity <= max_std
            sample_rows.append(
                {
                    "sample_id": sample_id,
                    "cq_value": cq,
                    "quantity": quantity,
                    "quantity_formatted": f"{quantity:.4g} {request.unit}",
                    "unit": request.unit,
                    "within_range": within_range,
                    "extrapolated": not within_range,
                }
            )
        return _json_clean(
            {
                "standard_curve": curve,
                "quantities": sample_rows,
                "mean_quantity": _mean(quantities),
                "sd_quantity": _std(quantities),
                "replicate_cv_percent": _cv_percent(quantities),
                "summary": f"Quantified {len(sample_rows)} qPCR sample(s) using {len(request.std_concentrations)} standards.",
                "plotly_json": {
                    "data": [
                        curve["plotly_json"]["data"][0],
                        curve["plotly_json"]["data"][1],
                        {
                            "type": "scatter",
                            "mode": "markers+text",
                            "x": [math.log(q, request.log_base) for q in quantities if q > 0],
                            "y": sample_cq_values,
                            "text": [row["sample_id"] for row in sample_rows],
                            "name": "Samples",
                        },
                    ],
                    "layout": {"title": "qPCR Absolute Quantification", "xaxis": {"title": "log10 quantity"}, "yaxis": {"title": "Cq"}},
                },
            }
        )

    cq_values = [float(v) for v in request.cq_values if math.isfinite(float(v))]
    if not cq_values:
        raise HTTPException(status_code=400, detail="At least one finite Cq value is required")
    if request.slope is None or request.intercept is None:
        raise HTTPException(status_code=400, detail="slope and intercept are required unless standard curve arrays are provided")
    if request.slope == 0:
        raise HTTPException(status_code=400, detail="Slope cannot be zero")
    quantities = [request.log_base ** ((cq - request.intercept) / request.slope) for cq in cq_values]
    return _json_clean(
        {
            "cq_values": cq_values,
            "quantities": quantities,
            "mean_cq": _mean(cq_values),
            "sd_cq": _std(cq_values),
            "replicate_cv_percent": _cv_percent(quantities),
            "mean_quantity": _mean(quantities),
            "sd_quantity": _std(quantities),
            "qc_flags": ["replicate CV above 20%"] if _cv_percent(quantities) > 20 else [],
        }
    )


def _cq_group(rows: List[CqRow]) -> Dict[Tuple[str, str, str], List[float]]:
    grouped: Dict[Tuple[str, str, str], List[float]] = {}
    for row in rows:
        if math.isfinite(row.cq):
            grouped.setdefault((row.sample, row.group, row.gene), []).append(row.cq)
    return grouped


def _sample_delta_cq(rows: List[CqRow], reference_genes: List[str], target_genes: List[str]) -> List[Dict[str, Any]]:
    grouped = _cq_group(rows)
    by_sample_group: Dict[Tuple[str, str], Dict[str, float]] = {}
    for (sample, group, gene), values in grouped.items():
        by_sample_group.setdefault((sample, group), {})[gene] = _mean(values)
    results: List[Dict[str, Any]] = []
    for (sample, group), gene_means in by_sample_group.items():
        ref_values = [gene_means[g] for g in reference_genes if g in gene_means]
        if not ref_values:
            continue
        ref_mean = _mean(ref_values)
        for target in target_genes:
            if target not in gene_means:
                continue
            delta = gene_means[target] - ref_mean
            results.append(
                {
                    "sample": sample,
                    "group": group,
                    "target_gene": target,
                    "reference_cq_mean": ref_mean,
                    "target_cq_mean": gene_means[target],
                    "delta_cq": delta,
                    "relative_expression": 2 ** (-delta),
                }
            )
    return results


@router.post("/analysis/qpcr/delta-cq")
def qpcr_delta_cq(request: DeltaCqRequest) -> Dict[str, Any]:
    results = _sample_delta_cq(request.rows, request.reference_genes, request.target_genes)
    return {"results": _json_clean(results), "n_results": len(results)}


@router.post("/analysis/qpcr/delta-delta-cq")
def qpcr_delta_delta_cq(request: DeltaDeltaCqRequest) -> Dict[str, Any]:
    delta_rows = _sample_delta_cq(request.rows, request.reference_genes, request.target_genes)
    controls: Dict[str, List[float]] = {}
    for row in delta_rows:
        if row["group"] == request.control_group:
            controls.setdefault(row["target_gene"], []).append(row["delta_cq"])
    control_means = {target: _mean(vals) for target, vals in controls.items()}
    results = []
    for row in delta_rows:
        baseline = control_means.get(row["target_gene"])
        if baseline is None:
            continue
        dd = row["delta_cq"] - baseline
        results.append({**row, "control_delta_cq_mean": baseline, "delta_delta_cq": dd, "fold_change": 2 ** (-dd)})
    return {"results": _json_clean(results), "control_group": request.control_group, "control_means": _json_clean(control_means)}


@router.post("/analysis/qpcr/anova-dunnett")
def qpcr_anova_dunnett(request: AnovaDunnettRequest) -> Dict[str, Any]:
    if isinstance(request.groups, dict):
        named_groups = {
            str(k): [float(v) for v in vals if _finite_float(v) is not None]
            for k, vals in request.groups.items()
            if isinstance(vals, list)
        }
    elif isinstance(request.groups, list):
        names = request.group_names or ["Control", *[f"Group_{i + 1}" for i in range(1, len(request.groups))]]
        named_groups = {}
        for idx, vals in enumerate(request.groups):
            if not isinstance(vals, list):
                continue
            name = names[idx] if idx < len(names) and names[idx] else f"Group_{idx + 1}"
            named_groups[name] = [float(v) for v in vals if _finite_float(v) is not None]
    else:
        raise HTTPException(status_code=400, detail="groups must be a mapping or array of arrays")
    valid = {k: vals for k, vals in named_groups.items() if len(vals) >= 2}
    control_group = request.control_group or (next(iter(valid.keys())) if valid else None)
    if not control_group or control_group not in valid:
        raise HTTPException(status_code=400, detail="Control group must have at least two values")
    f_stat, p_value = stats.f_oneway(*valid.values()) if len(valid) >= 2 else (0.0, 1.0)
    control = valid[control_group]
    comparisons = []
    for name, vals in valid.items():
        if name == control_group:
            continue
        t_stat, p = stats.ttest_ind(vals, control, equal_var=False)
        difference = _mean(vals) - _mean(control)
        comparisons.append(
            {
                "group": name,
                "control_group": control_group,
                "mean_delta": difference,
                "difference": difference,
                "diff": difference,
                "t_statistic": t_stat,
                "p_value": p,
                "significant": p < request.alpha,
            }
        )
    return _json_clean(
        {
            "f_statistic": f_stat,
            "p_value": p_value,
            "anova_f": f_stat,
            "anova_p": p_value,
            "alpha": request.alpha,
            "control_group": control_group,
            "comparisons": comparisons,
            "dunnett_results": comparisons,
            "summary": f"One-way ANOVA across {len(valid)} groups; Dunnett-style comparisons versus {control_group}.",
        }
    )


def _parse_qpcr_csv(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for idx, row in enumerate(reader, start=1):
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        well = lower.get("well") or lower.get("well_position") or lower.get("position") or f"A{idx}"
        sample = lower.get("sample") or lower.get("sample_name") or lower.get("sample name") or "Unknown"
        target = lower.get("target") or lower.get("target_name") or lower.get("target name") or lower.get("gene") or "Target"
        task = lower.get("task") or lower.get("type") or "UNKNOWN"
        ct = _finite_float(lower.get("ct") or lower.get("cq") or lower.get("cт") or lower.get("cycle threshold"))
        qty = _finite_float(lower.get("quantity") or lower.get("copies") or lower.get("copy_number"))
        rows.append({"well_position": well, "sample_name": sample, "target_name": target, "task": task, "ct": ct, "quantity": qty})
    return rows


def _build_qpcr_upload_response(wells: List[Dict[str, Any]], filename: Optional[str], import_engine: str, instrument_format: str, allow_empty: bool = False) -> Dict[str, Any]:
    if not wells and not allow_empty:
        raise HTTPException(status_code=400, detail="No qPCR result rows found. Export with Well/Sample/Target/Ct columns.")
    targets = sorted({w["target_name"] for w in wells})
    samples = sorted({w["sample_name"] for w in wells})
    ct_rows = [w for w in wells if w.get("ct") is not None]
    heatmap_z = [[None for _ in range(12)] for _ in range(8)]
    for w in ct_rows:
        pos = str(w["well_position"]).upper()
        if len(pos) >= 2 and pos[0] in "ABCDEFGH":
            try:
                col = int(pos[1:]) - 1
            except ValueError:
                continue
            row = ord(pos[0]) - ord("A")
            if 0 <= row < 8 and 0 <= col < 12:
                heatmap_z[row][col] = w["ct"]
    return _json_clean(
        {
            "filename": filename,
            "import_engine": import_engine,
            "instrument_format": instrument_format,
            "n_wells": len(wells),
            "targets": targets,
            "samples": samples,
            "wells": wells,
            "results_plotly_json": {
                "data": [{"type": "heatmap", "z": heatmap_z, "x": list(range(1, 13)), "y": list("ABCDEFGH"), "name": "Ct"}],
                "layout": {"title": "qPCR Plate Ct Heatmap"},
            },
            "amplification_plotly_json": {"data": [], "layout": {"title": "Amplification curves require raw curve export/RDML/EDS"}},
            "standard_curve_plotly_json": None,
            "assay_summary": {"standard_curves": {}, "quantities": [], "replicate_qc": [], "ntc_qc": [], "spike_recovery": [], "flag_counts": {}},
        }
    )


async def _qpcr_upload_common(file: UploadFile) -> Dict[str, Any]:
    data = await file.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    wells = _parse_qpcr_csv(text)
    return _build_qpcr_upload_response(wells, file.filename, "csv", "QuantStudio/StepOnePlus CSV")


def _row_to_qpcr_well(row: Dict[str, Any], idx: int) -> Dict[str, Any]:
    lower = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    well = lower.get("well") or lower.get("well_position") or lower.get("well position") or lower.get("position") or f"A{idx}"
    sample = lower.get("sample") or lower.get("sample_name") or lower.get("sample name") or "Unknown"
    target = lower.get("target") or lower.get("target_name") or lower.get("target name") or lower.get("gene") or "Target"
    task = lower.get("task") or lower.get("type") or "UNKNOWN"
    ct = _finite_float(lower.get("ct") or lower.get("cq") or lower.get("cycle threshold") or lower.get("cт"))
    qty = _finite_float(lower.get("quantity") or lower.get("copies") or lower.get("copy_number") or lower.get("starting quantity"))
    return {"well_position": well, "sample_name": sample, "target_name": target, "task": task, "ct": ct, "quantity": qty}


def _parse_qpcr_excel(data: bytes) -> Tuple[List[Dict[str, Any]], str, str]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is now required by pyproject
        raise HTTPException(status_code=500, detail="openpyxl is not installed in the BMS API environment") from exc

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet_names = workbook.sheetnames
    wells: List[Dict[str, Any]] = []
    for sheet in workbook.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        header_idx: Optional[int] = None
        headers: List[str] = []
        for idx, values in enumerate(rows):
            candidate = [str(v).strip() if v is not None else "" for v in values]
            lower = {v.lower() for v in candidate}
            if ({"well", "well position", "position"} & lower) and ({"ct", "cq", "cycle threshold"} & lower):
                header_idx = idx
                headers = candidate
                break
        if header_idx is None:
            continue
        for row_idx, values in enumerate(rows[header_idx + 1 :], start=1):
            row = {headers[i]: values[i] if i < len(values) else None for i in range(len(headers)) if headers[i]}
            if not any(value not in (None, "") for value in row.values()):
                continue
            wells.append(_row_to_qpcr_well(row, row_idx))
    instrument_format = "QuantStudio/StepOnePlus Excel" if any(name.lower() in {"results", "amplification data", "melt region derivative data", "sample setup"} for name in sheet_names) else "generic qPCR Excel"
    return wells, "openpyxl", instrument_format


@router.post("/analysis/qpcr/upload-csv")
async def upload_qpcr_csv(file: UploadFile = File(...)) -> Dict[str, Any]:
    return await _qpcr_upload_common(file)


@router.post("/analysis/qpcr/upload-excel")
async def upload_qpcr_excel(file: UploadFile = File(...)) -> Dict[str, Any]:
    data = await file.read()
    try:
        wells, import_engine, instrument_format = _parse_qpcr_excel(data)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to parse qPCR Excel workbook: {exc}") from exc
    return _build_qpcr_upload_response(wells, file.filename, import_engine, instrument_format)


@router.post("/analysis/qpcr/upload-eds")
async def upload_qpcr_eds(file: UploadFile = File(...)) -> Dict[str, Any]:
    data = await file.read()
    try:
        import qslib
    except ImportError as exc:  # pragma: no cover - dependency is now required by pyproject
        raise HTTPException(status_code=500, detail="qslib is not installed in the BMS API environment") from exc
    try:
        experiment = qslib.Experiment.from_file(io.BytesIO(data))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to parse QuantStudio EDS with qslib: {exc}") from exc

    wells: List[Dict[str, Any]] = []
    try:
        plate_setup = getattr(experiment, "plate_setup", None) or getattr(experiment, "platesetup", None)
        if plate_setup is not None and hasattr(plate_setup, "to_table"):
            table = plate_setup.to_table()
            for idx, row in enumerate(table if isinstance(table, list) else [], start=1):
                if isinstance(row, dict):
                    wells.append(_row_to_qpcr_well(row, idx))
    except Exception:
        wells = []

    response = _build_qpcr_upload_response(wells, file.filename, "qslib", "QuantStudio EDS", allow_empty=True)
    response["eds_summary"] = getattr(experiment, "summary", None)
    response["available_data"] = list(getattr(experiment, "available_data", []) or [])
    if not wells:
        response["parse_warnings"] = ["qslib opened the EDS file, but no plate Cq table was found in the exported experiment package."]
    return response


class ChromatographyAnalyzeRequest(BaseModel):
    time: List[float]
    signal: List[float]
    baseline_method: str = "rolling_min"
    peak_prominence: float = 0.05
    fit_model: str = "gaussian"


class CalibrationPoint(BaseModel):
    concentration: float
    area: float


class HplcCalibrationRequest(BaseModel):
    points: List[CalibrationPoint]
    analyte_name: str = "Analyte"
    unit: str = "ng/uL"
    force_through_origin: bool = False


class HplcQuantifyRequest(BaseModel):
    area: Optional[float] = None
    slope: Optional[float] = None
    intercept: float = 0.0
    dilution_factor: float = 1.0
    cal_concentrations: List[float] = Field(default_factory=list)
    cal_areas: List[float] = Field(default_factory=list)
    sample_areas: List[float] = Field(default_factory=list)
    sample_ids: List[str] = Field(default_factory=list)
    unit: str = "ng/uL"


class PlasmidIsoformRequest(BaseModel):
    peaks: List[Dict[str, Any]]
    windows: Dict[str, Tuple[float, float]] = Field(default_factory=lambda: {"open_circular": (0.0, 3.0), "linear": (3.0, 6.0), "supercoiled": (6.0, 99.0)})


@router.post("/analysis/hplc/analyze")
def chromatography_analyze(request: ChromatographyAnalyzeRequest) -> Dict[str, Any]:
    if len(request.time) != len(request.signal) or len(request.time) < 3:
        raise HTTPException(status_code=400, detail="time and signal arrays must be same length with at least 3 points")
    time = np.asarray(request.time, dtype=float)
    signal = np.asarray(request.signal, dtype=float)
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(signal)):
        raise HTTPException(status_code=400, detail="time and signal must be finite")
    analysis_engine = "scipy.signal"
    engine_package = "scipy"
    if request.baseline_method == "none":
        baseline = np.zeros_like(signal)
    elif request.baseline_method == "linear":
        baseline = np.linspace(signal[0], signal[-1], len(signal))
    elif request.baseline_method.startswith("mocca2"):
        try:
            import mocca2
        except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
            raise HTTPException(status_code=500, detail="mocca2 is not installed in the BMS API environment") from exc
        method = request.baseline_method.removeprefix("mocca2_") or "flatfit"
        if method not in {"flatfit", "arpls", "asls"}:
            method = "flatfit"
        baseline = np.asarray(mocca2.estimate_baseline(signal, method=method), dtype=float)
        analysis_engine = "MOCCA2"
        engine_package = "mocca2"
    else:
        window = max(3, min(len(signal), len(signal) // 20 or 3))
        baseline = np.array([np.min(signal[max(0, i - window): min(len(signal), i + window + 1)]) for i in range(len(signal))])
    corrected = signal - baseline
    prominence = max(float(request.peak_prominence), float(np.nanmax(corrected)) * 0.02 if len(corrected) else 0.0)

    peak_spans: List[Dict[str, Any]] = []
    if analysis_engine == "MOCCA2":
        import mocca2
        mocca_peaks = mocca2.find_peaks(corrected, min_rel_height=max(float(request.peak_prominence), 0.0), min_height=0.0)
        for peak in mocca_peaks:
            idx = int(peak.maximum)
            peak_spans.append(
                {
                    "idx": idx,
                    "left": int(max(0, peak.left)),
                    "right": int(min(len(time) - 1, peak.right)),
                    "height": float(peak.height),
                    "prominence": float(getattr(peak, "prominence", peak.height)),
                    "peak_engine": "MOCCA2",
                }
            )
    else:
        peak_indices, props = find_peaks(corrected, prominence=prominence)
        widths = peak_widths(corrected, peak_indices, rel_height=0.5) if len(peak_indices) else ([], [], [], [])
        for rank, idx in enumerate(peak_indices):
            left = int(max(0, math.floor(widths[2][rank]))) if len(peak_indices) else int(idx)
            right = int(min(len(time) - 1, math.ceil(widths[3][rank]))) if len(peak_indices) else int(idx)
            peak_spans.append(
                {
                    "idx": int(idx),
                    "left": left,
                    "right": right,
                    "height": float(corrected[idx]),
                    "prominence": float(props.get("prominences", [None] * len(peak_indices))[rank]) if len(peak_indices) else None,
                    "peak_engine": "scipy.signal.find_peaks",
                }
            )

    total_positive_area = float(np.trapezoid(np.maximum(corrected, 0), time))
    peaks = []
    for rank, span in enumerate(peak_spans):
        idx = int(span["idx"])
        left = int(span["left"])
        right = int(span["right"])
        if right <= left:
            left = max(0, idx - 1)
            right = min(len(time) - 1, idx + 1)
        area = float(np.trapezoid(np.maximum(corrected[left : right + 1], 0), time[left : right + 1]))
        width_time = float(time[right] - time[left]) if right > left else 0.0
        height = float(span["height"])
        rt = float(time[idx])
        plates = 5.54 * (rt / width_time) ** 2 if width_time > 0 else None
        peaks.append(
            {
                "peak_id": rank + 1,
                "retention_time": rt,
                "area": area,
                "height": height,
                "width": width_time,
                "start_time": float(time[left]),
                "end_time": float(time[right]),
                "area_percent": (area / total_positive_area * 100.0) if total_positive_area else 0.0,
                "plates": plates,
                "resolution": None,
                "tailing_factor": None,
                "fit_model": request.fit_model,
                "peak_engine": span["peak_engine"],
                "prominence": span.get("prominence"),
            }
        )
    peaks.sort(key=lambda p: p["retention_time"])
    for i in range(1, len(peaks)):
        prev = peaks[i - 1]
        cur = peaks[i]
        denom = (prev.get("width") or 0) + (cur.get("width") or 0)
        cur["resolution"] = 2 * (cur["retention_time"] - prev["retention_time"]) / denom if denom else None
    return _json_clean(
        {
            "analysis_engine": analysis_engine,
            "engine_package": engine_package,
            "baseline_method": request.baseline_method,
            "fit_model": request.fit_model,
            "n_peaks": len(peaks),
            "peaks": peaks,
            "total_area": total_positive_area,
            "plotly_json": {
                "data": [
                    {"type": "scatter", "mode": "lines", "x": time.tolist(), "y": signal.tolist(), "name": "Raw"},
                    {"type": "scatter", "mode": "lines", "x": time.tolist(), "y": baseline.tolist(), "name": "Baseline"},
                    {"type": "scatter", "mode": "lines", "x": time.tolist(), "y": corrected.tolist(), "name": "Corrected"},
                ],
                "layout": {"title": "Chromatogram Analysis", "xaxis": {"title": "Time"}, "yaxis": {"title": "Signal"}},
            },
        }
    )


@router.post("/analysis/hplc/quick-analyze")
def chromatography_quick_analyze(request: ChromatographyAnalyzeRequest) -> Dict[str, Any]:
    return chromatography_analyze(request)


@router.post("/analysis/hplc/calibration-curve")
def hplc_calibration_curve(request: HplcCalibrationRequest) -> Dict[str, Any]:
    points = [p for p in request.points if math.isfinite(p.concentration) and math.isfinite(p.area)]
    if len(points) < 2:
        raise HTTPException(status_code=400, detail="At least two calibration points required")
    x = [p.concentration for p in points]
    y = [p.area for p in points]
    if request.force_through_origin:
        denom = sum(v * v for v in x)
        slope = sum(xi * yi for xi, yi in zip(x, y)) / denom if denom else 0.0
        intercept = 0.0
        fitted = [slope * xi for xi in x]
        ss_res = sum((yi - fi) ** 2 for yi, fi in zip(y, fitted))
        ss_tot = sum((yi - _mean(y)) ** 2 for yi in y)
        fit = {"slope": slope, "intercept": intercept, "r_squared": 1 - ss_res / ss_tot if ss_tot else 1.0, "p_value": None, "std_err": None}
    else:
        fit = _linear_fit(x, y)
    return _json_clean({**fit, "analyte_name": request.analyte_name, "unit": request.unit, "n_points": len(points), "points": [p.model_dump() for p in points]})


@router.post("/analysis/hplc/quantify")
def hplc_quantify(request: HplcQuantifyRequest) -> Dict[str, Any]:
    if request.cal_concentrations and request.cal_areas:
        if len(request.cal_concentrations) != len(request.cal_areas):
            raise HTTPException(status_code=400, detail="cal_concentrations and cal_areas must have the same length")
        curve = hplc_calibration_curve(
            HplcCalibrationRequest(
                points=[CalibrationPoint(concentration=c, area=a) for c, a in zip(request.cal_concentrations, request.cal_areas)],
                unit=request.unit,
            )
        )
        slope = float(curve["slope"])
        intercept = float(curve["intercept"])
        if slope == 0:
            raise HTTPException(status_code=400, detail="Calibration slope cannot be zero")
        sample_rows = []
        for idx, area in enumerate(request.sample_areas):
            a = _finite_float(area)
            if a is None:
                continue
            sample_id = request.sample_ids[idx] if idx < len(request.sample_ids) and request.sample_ids[idx] else f"Sample_{idx + 1}"
            concentration = (a - intercept) / slope * request.dilution_factor
            sample_rows.append({"id": sample_id, "area": a, "concentration": concentration, "unit": request.unit})
        return _json_clean(
            {
                "curve_stats": curve,
                "samples": sample_rows,
                "summary": f"Quantified {len(sample_rows)} HPLC sample(s) against {len(request.cal_concentrations)} calibration levels.",
                "plotly_json": {
                    "data": [
                        {"type": "scatter", "mode": "markers", "x": request.cal_concentrations, "y": request.cal_areas, "name": "Calibration"},
                        {
                            "type": "scatter",
                            "mode": "lines",
                            "x": request.cal_concentrations,
                            "y": [slope * x + intercept for x in request.cal_concentrations],
                            "name": "Fit",
                        },
                    ],
                    "layout": {"title": "HPLC Calibration", "xaxis": {"title": f"Concentration ({request.unit})"}, "yaxis": {"title": "Area"}},
                },
            }
        )

    if request.area is None or request.slope is None:
        raise HTTPException(status_code=400, detail="area and slope are required unless calibration/sample arrays are provided")
    if request.slope == 0:
        raise HTTPException(status_code=400, detail="Slope cannot be zero")
    concentration = (request.area - request.intercept) / request.slope
    return _json_clean({"area": request.area, "concentration": concentration, "dilution_corrected_concentration": concentration * request.dilution_factor})


@router.post("/analysis/hplc/plasmid/isoforms")
@router.post("/analysis/hplc/empower/plasmid-isoforms")
def chromatography_plasmid_isoforms(request: PlasmidIsoformRequest) -> Dict[str, Any]:
    total_area = sum(float(p.get("area") or 0.0) for p in request.peaks)
    isoforms: Dict[str, Dict[str, Any]] = {}
    assigned = 0.0
    for name, (lo, hi) in request.windows.items():
        matched = [p for p in request.peaks if lo <= float(p.get("retention_time") or 0.0) <= hi]
        area = sum(float(p.get("area") or 0.0) for p in matched)
        assigned += area
        isoforms[name] = {"area": area, "area_percent": (area / total_area * 100.0) if total_area else 0.0, "peak_count": len(matched), "window": [lo, hi]}
    return _json_clean({"isoforms": isoforms, "total_area": total_area, "assigned_area": assigned, "total_assigned_area_percent": (assigned / total_area * 100.0) if total_area else 0.0})


# Minimal in-memory Empower review cache for UI sessions. This keeps BMS source-truth local;
# regulated Empower remains the acquisition/integration source of truth.
_EMPOWER_IMPORTS: Dict[int, Dict[str, Any]] = {}
_NEXT_IMPORT_ID = 1


def _sst_summary(injections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    for inj in injections:
        if inj.get("is_excluded"):
            continue
        by_group.setdefault(str(inj.get("sample_type") or "UNKNOWN"), []).append(inj)
    out = []
    for group, rows in sorted(by_group.items()):
        areas = [float(r.get("primary_peak_area") or r.get("total_area") or 0) for r in rows]
        pcts = [float(r.get("primary_peak_percent") or 0) for r in rows]
        rts = [float(r.get("primary_peak_rt") or 0) for r in rows]
        res = [float(r.get("primary_peak_resolution") or 0) for r in rows if r.get("primary_peak_resolution") is not None]
        out.append(
            {
                "sst_group": group,
                "n_injections": len(rows),
                "area_mean": _mean(areas),
                "area_rsd": _cv_percent(areas),
                "percent_primary_mean": _mean(pcts),
                "percent_primary_rsd": _cv_percent(pcts),
                "rt_mean": _mean(rts),
                "rt_rsd": _cv_percent(rts),
                "resolution_mean": _mean(res),
                "resolution_rsd": _cv_percent(res),
            }
        )
    return _json_clean(out)


@router.post("/analysis/hplc/empower/import")
async def empower_import(
    files: List[UploadFile] = File(...),
    persist: bool = Form(True),
    baseline_method: str = Form("rolling_min"),
    peak_prominence: float = Form(0.05),
) -> Dict[str, Any]:
    global _NEXT_IMPORT_ID
    import_id = _NEXT_IMPORT_ID
    _NEXT_IMPORT_ID += 1
    injections: List[Dict[str, Any]] = []
    errors: List[str] = []
    for file_idx, file in enumerate(files, start=1):
        payload = await file.read()
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = payload.decode("latin-1", errors="ignore")
        lines = text.splitlines()
        rows = list(csv.DictReader(io.StringIO(text))) if lines and "," in lines[0] else []
        if rows:
            for row_idx, row in enumerate(rows, start=1):
                lower = {str(k).strip().lower(): v for k, v in row.items()}
                area = _finite_float(lower.get("area") or lower.get("total area") or lower.get("primary_peak_area"), 0.0) or 0.0
                rt = _finite_float(lower.get("rt") or lower.get("retention_time") or lower.get("retention time"), 0.0) or 0.0
                injections.append(
                    {
                        "id": len(injections) + 1,
                        "import_id": import_id,
                        "sample_name": lower.get("sample") or lower.get("sample_name") or lower.get("sample name") or f"Injection {row_idx}",
                        "sample_type": (lower.get("sample_type") or lower.get("sample type") or "UNKNOWN").upper(),
                        "injection_number": lower.get("injection") or str(row_idx),
                        "method_name": lower.get("method") or None,
                        "run_date": lower.get("date") or None,
                        "source_file": file.filename,
                        "total_area": area,
                        "primary_peak_area": area,
                        "primary_peak_percent": 100.0 if area else 0.0,
                        "primary_peak_rt": rt,
                        "primary_peak_resolution": _finite_float(lower.get("resolution")),
                        "is_excluded": False,
                        "note": None,
                        "flag": None,
                    }
                )
        else:
            errors.append(f"{file.filename}: parsed as binary/non-CSV Empower export; upload Empower CSV/ASCII export for BMS lightweight review")
            injections.append(
                {
                    "id": len(injections) + 1,
                    "import_id": import_id,
                    "sample_name": file.filename or f"Injection {file_idx}",
                    "sample_type": "UNKNOWN",
                    "injection_number": str(file_idx),
                    "source_file": file.filename,
                    "total_area": 0.0,
                    "primary_peak_area": 0.0,
                    "primary_peak_percent": 0.0,
                    "primary_peak_rt": 0.0,
                    "primary_peak_resolution": None,
                    "is_excluded": False,
                    "note": "Needs Empower ASCII/CSV export for metric parsing",
                    "flag": "PARSE_REVIEW",
                }
            )
    _EMPOWER_IMPORTS[import_id] = {"injections": injections, "errors": errors}
    return {"import_id": import_id, "injections": injections, "sst_summary": _sst_summary(injections), "errors": errors}


@router.get("/analysis/hplc/empower/sst")
def list_empower_sst(import_id: int) -> List[Dict[str, Any]]:
    record = _EMPOWER_IMPORTS.get(import_id)
    if not record:
        return []
    return _sst_summary(record["injections"])


@router.put("/analysis/hplc/empower/injections/{injection_id}")
def update_empower_injection(injection_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    for record in _EMPOWER_IMPORTS.values():
        for inj in record["injections"]:
            if inj.get("id") == injection_id:
                inj.update({k: v for k, v in payload.items() if k in {"sample_name", "sample_type", "injection_number", "is_excluded", "note", "flag"}})
                return inj
    raise HTTPException(status_code=404, detail="Injection not found")


def _empower_csv(import_id: int) -> str:
    record = _EMPOWER_IMPORTS.get(import_id, {"injections": []})
    output = io.StringIO()
    fieldnames = ["id", "sample_name", "sample_type", "injection_number", "source_file", "total_area", "primary_peak_area", "primary_peak_percent", "primary_peak_rt", "primary_peak_resolution", "is_excluded", "flag", "note"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in record["injections"]:
        writer.writerow({k: row.get(k) for k in fieldnames})
    return output.getvalue()


@router.get("/analysis/hplc/empower/exports/sst-master")
def export_empower_sst_master(import_id: int) -> Response:
    return Response(_empower_csv(import_id), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sst_master.csv"})


@router.get("/analysis/hplc/empower/exports/plasmid-tracking")
def export_empower_plasmid_tracking(import_id: int) -> Response:
    return Response(_empower_csv(import_id), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=plasmid_tracking.csv"})


class ControlChartRequest(BaseModel):
    data: List[float]
    subgroup_size: int = 1


class CapabilityRequest(BaseModel):
    data: List[float]
    usl: float
    lsl: float
    target: Optional[float] = None
    subgroup_size: int = 1


class DoEDesignRequest(BaseModel):
    design_type: str = "full_factorial"
    n_factors: int = 2
    center_points: int = 0


class RsmRequest(BaseModel):
    design_matrix: List[Dict[str, float]]
    response: List[float]


class HypothesisRequest(BaseModel):
    data: Optional[List[float]] = None
    pop_mean: Optional[float] = None
    group1: Optional[List[float]] = None
    group2: Optional[List[float]] = None
    before: Optional[List[float]] = None
    after: Optional[List[float]] = None
    groups: Optional[List[List[float]]] = None
    alpha: float = 0.05


class RegressionRequest(BaseModel):
    x: List[float]
    y: List[float]
    x_name: str = "X"
    y_name: str = "Y"


@router.post("/analysis/control-chart")
def control_chart(request: ControlChartRequest) -> Dict[str, Any]:
    data = [float(v) for v in request.data if math.isfinite(float(v))]
    if len(data) < 2:
        raise HTTPException(status_code=400, detail="At least two data points required")
    mean = _mean(data)
    sigma = _std(data)
    ucl = mean + 3 * sigma
    lcl = mean - 3 * sigma
    violations = [i for i, v in enumerate(data) if v > ucl or v < lcl]
    return _json_clean({"center_line": mean, "ucl": ucl, "lcl": lcl, "sigma": sigma, "violations": violations, "n": len(data), "plotly_json": {"data": [{"type": "scatter", "mode": "lines+markers", "y": data, "name": "Value"}], "layout": {"title": "Individuals Control Chart"}}})


@router.post("/analysis/capability")
def capability(request: CapabilityRequest) -> Dict[str, Any]:
    data = [float(v) for v in request.data if math.isfinite(float(v))]
    if len(data) < 2:
        raise HTTPException(status_code=400, detail="At least two data points required")
    mean = _mean(data)
    std_overall = _std(data)
    # Lightweight within-subgroup estimate: for individual data, use moving-range sigma;
    # for larger subgroups, pool within subgroup standard deviations when possible.
    subgroup_size = max(1, int(request.subgroup_size or 1))
    if subgroup_size <= 1 and len(data) >= 2:
        moving_ranges = [abs(b - a) for a, b in zip(data, data[1:])]
        std_within = _mean(moving_ranges) / 1.128 if moving_ranges else std_overall
    elif subgroup_size > 1:
        subgroup_stds = [_std(data[i : i + subgroup_size]) for i in range(0, len(data), subgroup_size) if len(data[i : i + subgroup_size]) > 1]
        std_within = _mean(subgroup_stds) if subgroup_stds else std_overall
    else:
        std_within = std_overall
    sigma = std_within or std_overall
    cp = (request.usl - request.lsl) / (6 * sigma) if sigma else None
    cpu = (request.usl - mean) / (3 * sigma) if sigma else None
    cpl = (mean - request.lsl) / (3 * sigma) if sigma else None
    cpk = min(v for v in [cpu, cpl] if v is not None) if sigma else None
    pp = (request.usl - request.lsl) / (6 * std_overall) if std_overall else None
    ppu = (request.usl - mean) / (3 * std_overall) if std_overall else None
    ppl = (mean - request.lsl) / (3 * std_overall) if std_overall else None
    ppk = min(v for v in [ppu, ppl] if v is not None) if std_overall else None
    nonconforming_count = sum(1 for v in data if v < request.lsl or v > request.usl)
    target = request.target if request.target is not None else (request.usl + request.lsl) / 2
    is_centered = abs(mean - target) <= max(sigma or 0.0, (request.usl - request.lsl) * 0.05)
    return _json_clean(
        {
            "mean": mean,
            "std_dev": sigma,
            "std_within": std_within,
            "std_overall": std_overall,
            "cp": cp,
            "cpu": cpu,
            "cpl": cpl,
            "cpk": cpk,
            "pp": pp,
            "ppu": ppu,
            "ppl": ppl,
            "ppk": ppk,
            "is_capable": bool(cpk is not None and cpk >= 1.33),
            "is_centered": bool(is_centered),
            "ppm_total": nonconforming_count / len(data) * 1_000_000,
            "n": len(data),
            "nonconforming_count": nonconforming_count,
            "plotly_json": {
                "data": [{"type": "histogram", "x": data, "name": "Observed"}],
                "layout": {
                    "title": "Process Capability",
                    "xaxis": {"title": "Value"},
                    "shapes": [
                        {"type": "line", "x0": request.lsl, "x1": request.lsl, "y0": 0, "y1": 1, "xref": "x", "yref": "paper", "line": {"color": "red", "dash": "dash"}},
                        {"type": "line", "x0": request.usl, "x1": request.usl, "y0": 0, "y1": 1, "xref": "x", "yref": "paper", "line": {"color": "red", "dash": "dash"}},
                        {"type": "line", "x0": target, "x1": target, "y0": 0, "y1": 1, "xref": "x", "yref": "paper", "line": {"color": "green", "dash": "dot"}},
                    ],
                },
            },
        }
    )


@router.post("/analysis/doe/design")
def doe_design(request: DoEDesignRequest) -> Dict[str, Any]:
    try:
        import pyDOE3
    except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
        raise HTTPException(status_code=500, detail="pyDOE3 is not installed in the BMS API environment") from exc

    n = max(1, min(int(request.n_factors), 8))
    factor_names = [f"X{i + 1}" for i in range(n)]
    dtype = request.design_type.lower()
    center_points = max(0, int(request.center_points))
    generator = "pyDOE3.ff2n"

    if dtype == "central_composite":
        matrix = pyDOE3.ccdesign(n, center=(center_points, center_points), alpha="orthogonal", face="circumscribed")
        generator = "pyDOE3.ccdesign"
    elif dtype == "box_behnken":
        if n < 3:
            matrix = pyDOE3.ff2n(n)
            generator = "pyDOE3.ff2n"
        else:
            matrix = pyDOE3.bbdesign(n, center=center_points)
            generator = "pyDOE3.bbdesign"
    elif dtype == "plackett_burman":
        matrix = pyDOE3.pbdesign(n)
        generator = "pyDOE3.pbdesign"
        if center_points:
            matrix = np.vstack([matrix, np.zeros((center_points, n))])
    elif dtype == "fractional_factorial" and n >= 3:
        base = pyDOE3.ff2n(n - 1)
        generated = []
        for row in base:
            last = float(np.prod(row))
            generated.append([*row.tolist(), last])
        matrix = np.asarray(generated, dtype=float)
        generator = "pyDOE3.ff2n(generator=product)"
        if center_points:
            matrix = np.vstack([matrix, np.zeros((center_points, n))])
    else:
        matrix = pyDOE3.ff2n(n)
        generator = "pyDOE3.ff2n"
        if center_points:
            matrix = np.vstack([matrix, np.zeros((center_points, n))])

    runs = [dict(zip(factor_names, [float(v) for v in row])) for row in np.asarray(matrix, dtype=float).tolist()]
    factors = [{"name": name, "low": -1, "center": 0, "high": 1, "type": "continuous"} for name in factor_names]
    return _json_clean(
        {
            "analysis_engine": "pyDOE3",
            "engine_package": "pyDOE3",
            "design_type": request.design_type,
            "n_runs": len(runs),
            "n_factors": n,
            "factors": factors,
            "design_matrix": runs,
            "summary": f"{request.design_type} DOE generated with {generator}: {len(runs)} runs, {n} factors, and {center_points} requested center points.",
            "metadata": {
                "analysis_family": "JMP-compatible DOE",
                "design_generator": generator,
                "supported_models": ["main effects", "two-factor interactions", "quadratic/RSM terms", "desirability optimization"],
            },
        }
    )


@router.post("/analysis/doe/rsm")
def rsm_analysis(request: RsmRequest) -> Dict[str, Any]:
    try:
        import statsmodels.api as sm
    except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
        raise HTTPException(status_code=500, detail="statsmodels is not installed in the BMS API environment") from exc

    if len(request.design_matrix) != len(request.response) or not request.design_matrix:
        raise HTTPException(status_code=400, detail="Design matrix rows must match response values")
    cols = list(request.design_matrix[0].keys())
    x = np.asarray([[float(row.get(c, 0.0)) for c in cols] for row in request.design_matrix], dtype=float)
    y = np.asarray(request.response, dtype=float)
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise HTTPException(status_code=400, detail="RSM inputs must be finite numeric values")

    term_arrays = [np.ones(len(x))]
    term_names = ["Intercept"]
    for i, c in enumerate(cols):
        term_arrays.append(x[:, i])
        term_names.append(c)
    for i, c in enumerate(cols):
        term_arrays.append(x[:, i] ** 2)
        term_names.append(f"{c}^2")
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            term_arrays.append(x[:, i] * x[:, j])
            term_names.append(f"{cols[i]}:{cols[j]}")

    X = np.vstack(term_arrays).T
    model = sm.OLS(y, X).fit()
    beta = np.asarray(model.params, dtype=float)
    coeffs = dict(zip(term_names, beta.tolist()))

    linear = np.asarray([coeffs.get(c, 0.0) for c in cols], dtype=float)
    hessian = np.zeros((len(cols), len(cols)), dtype=float)
    for i, c in enumerate(cols):
        hessian[i, i] = 2.0 * coeffs.get(f"{c}^2", 0.0)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            value = coeffs.get(f"{cols[i]}:{cols[j]}", 0.0)
            hessian[i, j] = value
            hessian[j, i] = value
    try:
        optimum_vec = -np.linalg.solve(hessian, linear)
        if not np.all(np.isfinite(optimum_vec)):
            raise np.linalg.LinAlgError("non-finite stationary point")
    except np.linalg.LinAlgError:
        optimum_vec = np.zeros(len(cols), dtype=float)
    optimum_vec = np.clip(optimum_vec, -2.0, 2.0)
    optimal = {c: float(optimum_vec[i]) for i, c in enumerate(cols)}

    def predict_row(values: np.ndarray) -> float:
        terms = [1.0]
        terms.extend(values.tolist())
        terms.extend((values**2).tolist())
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                terms.append(float(values[i] * values[j]))
        return float(np.dot(np.asarray(terms, dtype=float), beta))

    predicted_optimum = predict_row(optimum_vec)
    p_values = {name: (float(p) if math.isfinite(float(p)) else None) for name, p in zip(term_names, model.pvalues)}

    contour_plot: Optional[Dict[str, Any]] = None
    surface_plot: Optional[Dict[str, Any]] = None
    if len(cols) >= 2:
        x1_grid = np.linspace(-1.5, 1.5, 31)
        x2_grid = np.linspace(-1.5, 1.5, 31)
        z_grid = []
        for yv in x2_grid:
            row_values = []
            for xv in x1_grid:
                values = optimum_vec.copy()
                values[0] = xv
                values[1] = yv
                row_values.append(predict_row(values))
            z_grid.append(row_values)
        contour_plot = {
            "data": [{"type": "contour", "x": x1_grid.tolist(), "y": x2_grid.tolist(), "z": z_grid, "colorscale": "Viridis"}],
            "layout": {"title": "RSM Contour", "xaxis": {"title": cols[0]}, "yaxis": {"title": cols[1]}},
        }
        surface_plot = {
            "data": [{"type": "surface", "x": x1_grid.tolist(), "y": x2_grid.tolist(), "z": z_grid, "colorscale": "Viridis"}],
            "layout": {"title": "RSM Surface", "scene": {"xaxis": {"title": cols[0]}, "yaxis": {"title": cols[1]}, "zaxis": {"title": "Predicted response"}}},
        }

    return _json_clean(
        {
            "analysis_engine": "statsmodels.OLS",
            "engine_package": "statsmodels",
            "summary": "Response-surface model fit with statsmodels OLS: intercept, linear, quadratic, and two-factor interaction terms.",
            "terms": term_names,
            "coefficients": coeffs,
            "p_values": p_values,
            "r_squared": float(model.rsquared) if math.isfinite(float(model.rsquared)) else None,
            "adj_r_squared": float(model.rsquared_adj) if math.isfinite(float(model.rsquared_adj)) else None,
            "aic": float(model.aic) if math.isfinite(float(model.aic)) else None,
            "bic": float(model.bic) if math.isfinite(float(model.bic)) else None,
            "optimal_point": optimal,
            "predicted_optimum": predicted_optimum,
            "contour_plot": contour_plot,
            "surface_plot": surface_plot,
        }
    )


@router.post("/analysis/hypothesis/t-test/one-sample")
def hypothesis_one_sample(request: HypothesisRequest) -> Dict[str, Any]:
    data = request.data or []
    if request.pop_mean is None or len(data) < 2:
        raise HTTPException(status_code=400, detail="data and pop_mean required")
    t, p = stats.ttest_1samp(data, request.pop_mean)
    return _json_clean({"test": "one_sample_t", "statistic": t, "p_value": p, "significant": p < request.alpha, "mean": _mean(data), "alpha": request.alpha})


@router.post("/analysis/hypothesis/t-test/two-sample")
def hypothesis_two_sample(request: HypothesisRequest) -> Dict[str, Any]:
    if not request.group1 or not request.group2:
        raise HTTPException(status_code=400, detail="group1 and group2 required")
    t, p = stats.ttest_ind(request.group1, request.group2, equal_var=False)
    return _json_clean({"test": "welch_t", "statistic": t, "p_value": p, "significant": p < request.alpha, "mean_group1": _mean(request.group1), "mean_group2": _mean(request.group2), "alpha": request.alpha})


@router.post("/analysis/hypothesis/t-test/paired")
def hypothesis_paired(request: HypothesisRequest) -> Dict[str, Any]:
    if not request.before or not request.after or len(request.before) != len(request.after):
        raise HTTPException(status_code=400, detail="paired before/after vectors of equal length required")
    t, p = stats.ttest_rel(request.before, request.after)
    return _json_clean({"test": "paired_t", "statistic": t, "p_value": p, "significant": p < request.alpha, "alpha": request.alpha})


@router.post("/analysis/hypothesis/anova")
def hypothesis_anova(request: HypothesisRequest) -> Dict[str, Any]:
    groups = request.groups or []
    if len(groups) < 2:
        raise HTTPException(status_code=400, detail="At least two groups required")
    f, p = stats.f_oneway(*groups)
    return _json_clean({"test": "one_way_anova", "statistic": f, "p_value": p, "significant": p < request.alpha, "alpha": request.alpha})


@router.post("/analysis/regression/simple")
def regression_simple(request: RegressionRequest) -> Dict[str, Any]:
    try:
        import statsmodels.api as sm
    except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
        raise HTTPException(status_code=500, detail="statsmodels is not installed in the BMS API environment") from exc

    x = np.asarray([float(v) for v in request.x], dtype=float)
    y = np.asarray([float(v) for v in request.y], dtype=float)
    if len(x) != len(y) or len(x) < 2:
        raise HTTPException(status_code=400, detail="At least two paired finite points are required")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise HTTPException(status_code=400, detail="Regression inputs must be finite numeric values")

    X = sm.add_constant(x, has_constant="add")
    model = sm.OLS(y, X).fit()
    intercept = float(model.params[0])
    slope = float(model.params[1])
    fitted = model.fittedvalues.astype(float).tolist()
    residuals = model.resid.astype(float).tolist()
    r2 = float(model.rsquared) if math.isfinite(float(model.rsquared)) else None
    adj_r2 = float(model.rsquared_adj) if math.isfinite(float(model.rsquared_adj)) else r2
    f_stat = float(model.fvalue) if model.fvalue is not None and math.isfinite(float(model.fvalue)) else float("inf")
    f_pvalue = float(model.f_pvalue) if model.f_pvalue is not None and math.isfinite(float(model.f_pvalue)) else 0.0
    slope_p = float(model.pvalues[1]) if len(model.pvalues) > 1 and math.isfinite(float(model.pvalues[1])) else None
    intercept_p = float(model.pvalues[0]) if len(model.pvalues) > 0 and math.isfinite(float(model.pvalues[0])) else None
    n = int(model.nobs)
    equation = f"{request.y_name} = {intercept:.4g} + {slope:.4g}*{request.x_name}"
    return _json_clean(
        {
            "analysis_engine": "statsmodels.OLS",
            "engine_package": "statsmodels",
            "slope": slope,
            "intercept": intercept,
            "r_squared": r2,
            "p_value": slope_p,
            "std_err": float(model.bse[1]) if len(model.bse) > 1 and math.isfinite(float(model.bse[1])) else None,
            "x_name": request.x_name,
            "y_name": request.y_name,
            "equation": equation,
            "coefficients": {"intercept": intercept, request.x_name: slope, "X": slope},
            "adj_r_squared": adj_r2,
            "f_statistic": f_stat,
            "f_pvalue": f_pvalue,
            "aic": float(model.aic) if math.isfinite(float(model.aic)) else None,
            "bic": float(model.bic) if math.isfinite(float(model.bic)) else None,
            "n_obs": n,
            "p_values": {"intercept": intercept_p, request.x_name: slope_p, "X": slope_p},
            "scatter_plot": {
                "data": [
                    {"type": "scatter", "mode": "markers", "x": x.tolist(), "y": y.tolist(), "name": "Observed"},
                    {"type": "scatter", "mode": "lines", "x": x.tolist(), "y": fitted, "name": "Fit"},
                ],
                "layout": {"title": "Simple Linear Regression", "xaxis": {"title": request.x_name}, "yaxis": {"title": request.y_name}},
            },
            "diagnostics_plot": {
                "data": [{"type": "scatter", "mode": "markers", "x": fitted, "y": residuals, "name": "Residuals"}],
                "layout": {"title": "Residuals vs Fitted", "xaxis": {"title": "Fitted"}, "yaxis": {"title": "Residual"}},
            },
        }
    )


@router.get("/datasets")
def datasets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    return []


@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: int) -> Dict[str, Any]:
    raise HTTPException(status_code=404, detail="No built-in or example assay datasets are shipped. Import real assay data before loading a dataset.")


@router.get("/datasets/seed")
def seed_datasets() -> Dict[str, Any]:
    return {"message": "No fake/example datasets seeded. Import real assay data through qPCR, chromatography, or statistics upload workflows."}


@router.get("/tools")
def external_tools() -> Dict[str, Any]:
    return _json_clean({"tools": assay_tool_registry(), "by_category": tools_by_category()})


@router.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    tools = assay_tool_registry()
    return _json_clean(
        {
            "service": "BioModStack Assay Analytics",
            "surfaces": ["qPCR QuantStudio/StepOnePlus", "Waters/Empower chromatography", "plasmid DNA isoform analysis", "JMP-like DOE/statistics", "Plotly visualization"],
            "source_of_truth": "BMS API /api/assay-analytics",
            "not_used": "Document Parser service",
            "external_tools": tools,
            "external_tools_by_category": tools_by_category(),
        }
    )

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from services.assay_analytical_store import (
    AnalyticalImport,
    AnalyticalSourceFile,
    AssayRun,
    ChromInjection,
    ChromPeak,
    ChromTracePoint,
    create_analytical_session_factory,
    init_analytical_store,
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None

    def normalize(parsed: datetime) -> datetime:
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    candidates = [
        "%Y%m%d%H%M%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
    ]
    normalized = text.replace("Z", "+0000")
    for fmt in candidates:
        try:
            return normalize(datetime.strptime(normalized, fmt))
        except ValueError:
            pass
    try:
        return normalize(datetime.fromisoformat(text))
    except ValueError:
        return None


def _common_text(injections: list[dict[str, Any]], key: str) -> str | None:
    values = {_text(injection.get(key)) for injection in injections if _text(injection.get(key))}
    return next(iter(values)) if len(values) == 1 else None


def _source_role(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "cdf":
        return "empower_aia_cdf"
    if ext == "arw":
        return "empower_arw_metadata"
    if ext == "zip":
        return "empower_zip_batch"
    if ext in {"csv", "txt"}:
        return "empower_csv_ascii_export"
    return "empower_upload"


async def persist_empower_import(
    *,
    import_session_id: int,
    source_files: list[dict[str, Any]],
    injections: list[dict[str, Any]],
    analytics: dict[str, Any],
) -> dict[str, str]:
    """Persist a parsed Empower import into the analytical PostgreSQL store.

    The parser/review payload remains the API response source of truth for the UI;
    this function adds durable analytical IDs and relational rows for cross-run
    chromatography analytics.
    """

    if not injections:
        raise ValueError("Cannot persist an Empower import without parsed injections")

    analytical_import_id = _new_id()
    assay_run_id = _new_id()
    source_hashes = [str(item.get("sha256") or "") for item in source_files if item.get("sha256")]
    batch_hash = hashlib.sha256("|".join(sorted(source_hashes)).encode("utf-8")).hexdigest() if source_hashes else None
    run_datetimes = [_parse_datetime(injection.get("run_date")) for injection in injections]
    run_datetimes = [value for value in run_datetimes if value is not None]

    await init_analytical_store()
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        import_record = AnalyticalImport(
            id=analytical_import_id,
            assay_type="chromatography",
            source_filename=", ".join([_text(item.get("filename")) or "unnamed" for item in source_files])[:512] if source_files else None,
            source_file_hash=batch_hash,
            parser_engine="empower_cdf_arw_csv",
            parser_version=None,
            instrument_format="waters_empower_export",
            metadata_json={
                "review_import_id": import_session_id,
                "n_source_files": len(source_files),
                "n_injections": len(injections),
                "source_format_counts": analytics.get("empower_summary", {}).get("source_format_counts", {}),
                "empower_summary": analytics.get("empower_summary", {}),
            },
        )
        session.add(import_record)

        for item in source_files:
            filename = _text(item.get("filename")) or "unnamed-upload"
            source_file_id = _new_id()
            session.add(
                AnalyticalSourceFile(
                    id=source_file_id,
                    import_id=analytical_import_id,
                    filename=filename,
                    content_type=_text(item.get("content_type")),
                    sha256=str(item.get("sha256") or ""),
                    storage_uri=f"analytical-store://imports/{analytical_import_id}/source-files/{source_file_id}/{filename}",
                    size_bytes=int(item.get("size_bytes") or 0),
                    role=_source_role(filename),
                    metadata_json={"original_index": item.get("index")},
                )
            )

        run = AssayRun(
            id=assay_run_id,
            import_id=analytical_import_id,
            assay_type="chromatography",
            run_label=f"Empower import {import_session_id}",
            instrument=_common_text(injections, "system_name") or _common_text(injections, "detector_name"),
            method_name=_common_text(injections, "method_name"),
            operator=_common_text(injections, "acquired_by"),
            run_started_at=min(run_datetimes) if run_datetimes else None,
            run_completed_at=max(run_datetimes) if run_datetimes else None,
            metadata_json={
                "review_import_id": import_session_id,
                "source_format_counts": analytics.get("empower_summary", {}).get("source_format_counts", {}),
                "sample_role_counts": analytics.get("empower_summary", {}).get("sample_role_counts", {}),
            },
        )
        session.add(run)
        await session.flush()

        for injection in injections:
            injection_db_id = _new_id()
            injection["analytical_injection_id"] = injection_db_id
            chromatogram = injection.get("chromatogram") if isinstance(injection.get("chromatogram"), dict) else {}
            session.add(
                ChromInjection(
                    id=injection_db_id,
                    run_id=assay_run_id,
                    injection_name=_text(injection.get("injection_number")) or _text(injection.get("sample_name")),
                    sample_id=_text(injection.get("sample_id")),
                    sample_name=_text(injection.get("sample_name")),
                    sample_amount=_float(injection.get("sample_amount")),
                    sample_comment=_text(injection.get("sample_comment")),
                    vial=_text(injection.get("vial")),
                    injection_index=_int(injection.get("injection_number")) or _int(injection.get("id")),
                    injection_time=_parse_datetime(injection.get("run_date")),
                    injection_volume=_float(injection.get("injection_volume")),
                    sample_type=_text(injection.get("sample_type")),
                    column_name=_text(injection.get("column_name")),
                    column_serial_number=_text(injection.get("column_serial_number")),
                    system_name=_text(injection.get("system_name")),
                    acquired_by=_text(injection.get("acquired_by")),
                    acq_method_set=_text(injection.get("method_name")),
                    instrument_method_id=_text(injection.get("instrument_method_id")),
                    channel=_text(injection.get("detector_name")),
                    channel_description=_text(injection.get("channel_description")),
                    detector=_text(injection.get("detector_name")),
                    detector_unit=_text(injection.get("detector_unit")),
                    retention_unit=_text(injection.get("retention_unit")),
                    detector_minimum_value=_float(injection.get("detector_minimum_value")),
                    detector_maximum_value=_float(injection.get("detector_maximum_value")),
                    actual_run_time_length=_float(injection.get("actual_run_time_length")),
                    actual_sampling_interval=_float(injection.get("actual_sampling_interval")),
                    actual_delay_time=_float(injection.get("actual_delay_time")),
                    method=_text(injection.get("method_name")),
                    chromatogram_storage_uri=f"analytical-store://chrom-injections/{injection_db_id}/trace-points" if chromatogram else None,
                    chromatogram_sha256=None,
                    chromatogram_point_count=_int(injection.get("chromatogram_points")) or _int(chromatogram.get("points")),
                    raw_trace_storage_uri=f"analytical-store://chrom-injections/{injection_db_id}/raw-trace" if chromatogram else None,
                    raw_trace_sha256=None,
                    metadata_json={
                        "review_import_id": import_session_id,
                        "review_injection_id": injection.get("id"),
                        "source_file": injection.get("source_file"),
                        "paired_arw_file": injection.get("paired_arw_file"),
                        "source_format": injection.get("source_format"),
                        "sample_role": injection.get("sample_role"),
                        "sample_role_source": injection.get("sample_role_source"),
                        "qc_flags": injection.get("qc_flags", []),
                        "native_peak_count": injection.get("native_peak_count"),
                        "primary_peak_area": injection.get("primary_peak_area"),
                        "primary_peak_percent": injection.get("primary_peak_percent"),
                        "primary_peak_rt": injection.get("primary_peak_rt"),
                    },
                )
            )

            time_values = chromatogram.get("time_min", []) if isinstance(chromatogram, dict) else []
            signal_values = chromatogram.get("signal", []) if isinstance(chromatogram, dict) else []
            for point_index, (retention_time, signal_value) in enumerate(zip(time_values, signal_values)):
                session.add(
                    ChromTracePoint(
                        id=_new_id(),
                        injection_id=injection_db_id,
                        point_index=point_index,
                        retention_time=_float(retention_time),
                        signal_value=_float(signal_value),
                        retention_unit="minutes",
                        signal_unit=_text(injection.get("detector_unit")),
                        metadata_json={"downsampled": chromatogram.get("downsampled")},
                    )
                )

            for peak in injection.get("peaks", []) or []:
                peak_db_id = _new_id()
                peak["analytical_peak_id"] = peak_db_id
                session.add(
                    ChromPeak(
                        id=peak_db_id,
                        injection_id=injection_db_id,
                        source_peak_index=_int(peak.get("peak_id")),
                        peak_name=_text(peak.get("peak_name")),
                        analyte=_text(peak.get("analyte")),
                        isoform_class=_text(peak.get("isoform_class")),
                        rt=_float(peak.get("retention_time_min", peak.get("retention_time"))),
                        start_rt=_float(peak.get("start_rt")),
                        end_rt=_float(peak.get("end_rt")),
                        area=_float(peak.get("area")),
                        height=_float(peak.get("height")),
                        amount=_float(peak.get("amount")),
                        width=_float(peak.get("width")),
                        asymmetry=_float(peak.get("asymmetry")),
                        tailing=_float(peak.get("tailing_factor", peak.get("tailing"))),
                        resolution=_float(peak.get("resolution")),
                        plates=_float(peak.get("plates")),
                        signal_to_noise=_float(peak.get("signal_to_noise")),
                        percent_area=_float(peak.get("area_percent")),
                        concentration=_float(peak.get("concentration")),
                        integration_flags_json=peak.get("integration_flags", []) or [],
                        metadata_json={
                            "review_import_id": import_session_id,
                            "review_injection_id": injection.get("id"),
                            "peak_source": peak.get("peak_source"),
                            "retention_time_sec": peak.get("retention_time_sec"),
                        },
                    )
                )

        await session.commit()

    return {"analytical_import_id": analytical_import_id, "assay_run_id": assay_run_id}

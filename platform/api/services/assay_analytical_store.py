from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship


AnalyticalBase = declarative_base()


@dataclass(frozen=True)
class AnalyticalStoreSettings:
    """Connection settings for the assay/chromatography analytical store.

    This store is intentionally separate from the main BioModStack protein/workflow
    database.  It defaults to PostgreSQL so qPCR, Empower/HPLC, DOE, and QC trend
    data can grow into a central lab-data service without coupling migrations to
    the protein workflow schema.
    """

    database_url: str | None
    host: str
    port: int
    database: str
    username: str
    password: str


def analytical_store_settings() -> AnalyticalStoreSettings:
    return AnalyticalStoreSettings(
        database_url=(os.getenv("BMS_ANALYTICAL_DATABASE_URL") or "").strip() or None,
        host=(os.getenv("BMS_ANALYTICAL_DB_HOST") or "127.0.0.1").strip(),
        port=int(os.getenv("BMS_ANALYTICAL_DB_PORT") or "5432"),
        database=(os.getenv("BMS_ANALYTICAL_DB_NAME") or "bms_analytical_data").strip(),
        username=(os.getenv("BMS_ANALYTICAL_DB_USER") or "bms_assay").strip(),
        password=os.getenv("BMS_ANALYTICAL_DB_PASSWORD") or "bms_assay_dev",
    )


def build_analytical_database_url(settings: AnalyticalStoreSettings | None = None) -> str:
    settings = settings or analytical_store_settings()
    if settings.database_url:
        return settings.database_url
    return (
        make_url("postgresql+asyncpg://")
        .set(
            username=settings.username,
            password=settings.password,
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )
        .render_as_string(hide_password=False)
    )


def _redacted_url(url: str) -> str:
    parsed = make_url(url)
    return str(parsed.set(password="***")) if parsed.password is not None else str(parsed)


def analytical_store_status() -> dict[str, Any]:
    url = build_analytical_database_url()
    parsed = make_url(url)
    database_kind = "postgresql" if parsed.drivername.startswith("postgresql") else parsed.get_backend_name()
    return {
        "enabled": True,
        "schema_owner": "assay-analytics",
        "database_kind": database_kind,
        "driver": parsed.drivername,
        "database_name": parsed.database,
        "host": parsed.host,
        "port": parsed.port,
        "username": parsed.username,
        "url_preview": _redacted_url(url),
        "separate_from_protein_workflow_db": True,
        "protein_workflow_database_env": "DATABASE_URL",
        "analytical_database_env": "BMS_ANALYTICAL_DATABASE_URL",
        "schema_tables": sorted(AnalyticalBase.metadata.tables.keys()),
    }


def create_analytical_engine(database_url: str | None = None):
    url = database_url or build_analytical_database_url()
    return create_async_engine(url, echo=False, pool_pre_ping=True)


def create_analytical_session_factory(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_analytical_engine(database_url), expire_on_commit=False)


async def init_analytical_store(database_url: str | None = None) -> None:
    engine = create_analytical_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(AnalyticalBase.metadata.create_all)
    await engine.dispose()


class AnalyticalImport(AnalyticalBase):
    __tablename__ = "analytical_imports"

    id = Column(String(36), primary_key=True)
    assay_type = Column(String(64), nullable=False, index=True)
    source_filename = Column(String(512), nullable=True)
    source_file_hash = Column(String(128), nullable=True, index=True)
    parser_engine = Column(String(128), nullable=False)
    parser_version = Column(String(128), nullable=True)
    instrument_format = Column(String(128), nullable=True, index=True)
    imported_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    imported_by = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    source_files = relationship("AnalyticalSourceFile", back_populates="import_record", cascade="all, delete-orphan")
    assay_runs = relationship("AssayRun", back_populates="import_record", cascade="all, delete-orphan")


class AnalyticalSourceFile(AnalyticalBase):
    __tablename__ = "analytical_source_files"

    id = Column(String(36), primary_key=True)
    import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    content_type = Column(String(255), nullable=True)
    sha256 = Column(String(128), nullable=False, index=True)
    storage_uri = Column(String(1024), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    role = Column(String(128), nullable=False, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    import_record = relationship("AnalyticalImport", back_populates="source_files")


class SampleRegistryEntry(AnalyticalBase):
    __tablename__ = "sample_registry"

    sample_id = Column(String(255), primary_key=True)
    sample_label = Column(String(512), nullable=True)
    sample_type = Column(String(128), nullable=True, index=True)
    linked_entity_type = Column(String(128), nullable=True, index=True)
    linked_entity_id = Column(String(255), nullable=True, index=True)
    batch_id = Column(String(255), nullable=True, index=True)
    construct_id = Column(String(255), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AssayRun(AnalyticalBase):
    __tablename__ = "assay_runs"

    id = Column(String(36), primary_key=True)
    import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="SET NULL"), nullable=True, index=True)
    assay_type = Column(String(64), nullable=False, index=True)
    run_label = Column(String(512), nullable=True)
    instrument = Column(String(255), nullable=True, index=True)
    instrument_id = Column(String(255), nullable=True, index=True)
    method_name = Column(String(512), nullable=True, index=True)
    operator = Column(String(255), nullable=True, index=True)
    run_started_at = Column(DateTime, nullable=True, index=True)
    run_completed_at = Column(DateTime, nullable=True)
    batch_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    sample_set_id = Column(String(255), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    import_record = relationship("AnalyticalImport", back_populates="assay_runs")


class QpcrWell(AnalyticalBase):
    __tablename__ = "qpcr_wells"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    plate_id = Column(String(255), nullable=True, index=True)
    well = Column(String(16), nullable=False, index=True)
    sample_id = Column(String(255), nullable=True, index=True)
    target = Column(String(255), nullable=True, index=True)
    dye = Column(String(64), nullable=True)
    task = Column(String(64), nullable=True, index=True)
    cq = Column(Float, nullable=True)
    cq_mean = Column(Float, nullable=True)
    cq_sd = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    starting_quantity = Column(Float, nullable=True)
    dilution = Column(Float, nullable=True)
    replicate_group = Column(String(255), nullable=True, index=True)
    flags_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)


class QpcrStandardCurve(AnalyticalBase):
    __tablename__ = "qpcr_standard_curves"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    target = Column(String(255), nullable=False, index=True)
    model_type = Column(String(128), nullable=False, default="linear_cq_vs_log_quantity")
    slope = Column(Float, nullable=True)
    intercept = Column(Float, nullable=True)
    r_squared = Column(Float, nullable=True)
    efficiency_percent = Column(Float, nullable=True)
    lod = Column(Float, nullable=True)
    loq = Column(Float, nullable=True)
    n_points = Column(Integer, nullable=False, default=0)
    n_excluded = Column(Integer, nullable=False, default=0)
    fit_json = Column(JSON, nullable=False, default=dict)
    residuals_json = Column(JSON, nullable=False, default=list)
    qc_flags_json = Column(JSON, nullable=False, default=list)


class ChromInjection(AnalyticalBase):
    __tablename__ = "chrom_injections"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    injection_name = Column(String(512), nullable=True, index=True)
    sample_id = Column(String(255), nullable=True, index=True)
    vial = Column(String(128), nullable=True)
    injection_index = Column(Integer, nullable=True, index=True)
    injection_time = Column(DateTime, nullable=True, index=True)
    injection_volume = Column(Float, nullable=True)
    sample_type = Column(String(128), nullable=True, index=True)
    channel = Column(String(255), nullable=True)
    detector = Column(String(255), nullable=True)
    method = Column(String(512), nullable=True, index=True)
    chromatogram_storage_uri = Column(String(1024), nullable=True)
    chromatogram_sha256 = Column(String(128), nullable=True)
    chromatogram_point_count = Column(Integer, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ChromPeak(AnalyticalBase):
    __tablename__ = "chrom_peaks"

    id = Column(String(36), primary_key=True)
    injection_id = Column(String(36), ForeignKey("chrom_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    peak_name = Column(String(255), nullable=True, index=True)
    analyte = Column(String(255), nullable=True, index=True)
    isoform_class = Column(String(128), nullable=True, index=True)
    rt = Column(Float, nullable=True, index=True)
    start_rt = Column(Float, nullable=True)
    end_rt = Column(Float, nullable=True)
    area = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    width = Column(Float, nullable=True)
    asymmetry = Column(Float, nullable=True)
    tailing = Column(Float, nullable=True)
    resolution = Column(Float, nullable=True)
    plates = Column(Float, nullable=True)
    signal_to_noise = Column(Float, nullable=True)
    percent_area = Column(Float, nullable=True)
    concentration = Column(Float, nullable=True)
    integration_flags_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ChromCalibrationCurve(AnalyticalBase):
    __tablename__ = "chrom_calibration_curves"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    analyte = Column(String(255), nullable=False, index=True)
    response_type = Column(String(128), nullable=False, default="area")
    concentration_unit = Column(String(128), nullable=True)
    model_type = Column(String(128), nullable=False, default="linear")
    weighting = Column(String(64), nullable=True)
    slope = Column(Float, nullable=True)
    intercept = Column(Float, nullable=True)
    r_squared = Column(Float, nullable=True)
    lod = Column(Float, nullable=True)
    loq = Column(Float, nullable=True)
    fit_json = Column(JSON, nullable=False, default=dict)
    residuals_json = Column(JSON, nullable=False, default=list)
    qc_flags_json = Column(JSON, nullable=False, default=list)


class AssayComparison(AnalyticalBase):
    __tablename__ = "assay_comparisons"

    id = Column(String(36), primary_key=True)
    comparison_type = Column(String(128), nullable=False, index=True)
    assay_type = Column(String(64), nullable=False, index=True)
    selected_run_ids_json = Column(JSON, nullable=False, default=list)
    grouping_json = Column(JSON, nullable=False, default=dict)
    model_spec_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    engine = Column(String(128), nullable=False)
    engine_version = Column(String(128), nullable=True)
    result_json = Column(JSON, nullable=False, default=dict)
    plotly_json = Column(JSON, nullable=True)
    report_uri = Column(String(1024), nullable=True)


class QcTrend(AnalyticalBase):
    __tablename__ = "qc_trends"

    id = Column(String(36), primary_key=True)
    assay_type = Column(String(64), nullable=False, index=True)
    metric_name = Column(String(255), nullable=False, index=True)
    entity_type = Column(String(128), nullable=True, index=True)
    entity_id = Column(String(255), nullable=True, index=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    value = Column(Float, nullable=True)
    centerline = Column(Float, nullable=True)
    lcl = Column(Float, nullable=True)
    ucl = Column(Float, nullable=True)
    warning_low = Column(Float, nullable=True)
    warning_high = Column(Float, nullable=True)
    status = Column(String(64), nullable=True, index=True)
    rules_triggered_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)


Index("ix_qpcr_wells_run_target_sample", QpcrWell.run_id, QpcrWell.target, QpcrWell.sample_id)
Index("ix_chrom_peaks_injection_analyte_isoform", ChromPeak.injection_id, ChromPeak.analyte, ChromPeak.isoform_class)
Index("ix_qc_trends_metric_entity_time", QcTrend.metric_name, QcTrend.entity_type, QcTrend.entity_id, QcTrend.observed_at)

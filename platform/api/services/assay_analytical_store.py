from __future__ import annotations

import hashlib
import os
import uuid
import zipfile
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, inspect, text, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.schema import CreateColumn


AnalyticalBase = declarative_base()


class AnalyticalStoreUnavailable(RuntimeError):
    """Raised when the optional BMS DB service is unavailable for assay analytics."""


DB_SERVICE_OFFLINE_MESSAGE = "db_service_offline — use BMS DB service → Start"


def _analytical_store_unavailable(context: str, exc: BaseException) -> AnalyticalStoreUnavailable:
    message = str(exc).strip() or exc.__class__.__name__
    return AnalyticalStoreUnavailable(f"{DB_SERVICE_OFFLINE_MESSAGE}; {context}: {message}")


async def ensure_analytical_store_available(context: str = "analytical store operation") -> None:
    try:
        await init_analytical_store()
    except Exception as exc:  # noqa: BLE001 - converted to explicit degraded DB-service status at router boundary.
        raise _analytical_store_unavailable(context, exc) from exc


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


def _sync_missing_analytical_columns_and_indexes(sync_conn) -> None:
    """Non-destructively align existing analytical tables with the model metadata.

    SQLAlchemy create_all creates missing tables but intentionally does not add
    columns to tables that already exist.  This lightweight bootstrapper is safe
    for the young assay-analytics store: it only ADD COLUMNs that are absent and
    creates absent indexes, never drops or rewrites existing data.
    """

    inspector = inspect(sync_conn)
    preparer = sync_conn.dialect.identifier_preparer
    existing_tables = set(inspector.get_table_names())

    for table in AnalyticalBase.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            ddl = f"ALTER TABLE {preparer.format_table(table)} ADD COLUMN {CreateColumn(column).compile(dialect=sync_conn.dialect)}"
            sync_conn.execute(text(ddl))

    # Refresh after column additions so indexes on newly-added columns can be created.
    inspector = inspect(sync_conn)
    for table in AnalyticalBase.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name and index.name not in existing_indexes:
                index.create(bind=sync_conn)


async def init_analytical_store(database_url: str | None = None) -> None:
    engine = create_analytical_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(AnalyticalBase.metadata.create_all)
        await conn.run_sync(_sync_missing_analytical_columns_and_indexes)
    await engine.dispose()


class AnalyticalImport(AnalyticalBase):
    __tablename__ = "analytical_imports"

    id = Column(String(36), primary_key=True)
    assay_type = Column(String(64), nullable=False, index=True)
    source_filename = Column(String(512), nullable=True)
    source_file_hash = Column(String(128), nullable=True, index=True)
    source_fingerprint = Column(String(128), nullable=True, index=True)
    dataset_label = Column(String(512), nullable=True, index=True)
    parser_engine = Column(String(128), nullable=False)
    parser_version = Column(String(128), nullable=True)
    instrument_format = Column(String(128), nullable=True, index=True)
    imported_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    imported_by = Column(String(255), nullable=True)
    status = Column(String(64), nullable=True, default="imported", index=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    source_files = relationship("AnalyticalSourceFile", back_populates="import_record", cascade="all, delete-orphan")
    assay_runs = relationship("AssayRun", back_populates="import_record", cascade="all, delete-orphan")


class AnalyticalSourceFile(AnalyticalBase):
    __tablename__ = "analytical_source_files"

    id = Column(String(36), primary_key=True)
    import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    original_relative_path = Column(String(1024), nullable=True, index=True)
    file_extension = Column(String(32), nullable=True, index=True)
    content_type = Column(String(255), nullable=True)
    sha256 = Column(String(128), nullable=False, index=True)
    storage_uri = Column(String(1024), nullable=False)
    content_bytes = Column(LargeBinary, nullable=True)
    size_bytes = Column(Integer, nullable=False)
    role = Column(String(128), nullable=False, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    import_record = relationship("AnalyticalImport", back_populates="source_files")


class AnalyticalArchiveMember(AnalyticalBase):
    __tablename__ = "analytical_archive_members"

    id = Column(String(36), primary_key=True)
    source_file_id = Column(String(36), ForeignKey("analytical_source_files.id", ondelete="CASCADE"), nullable=False, index=True)
    member_path = Column(String(1024), nullable=False, index=True)
    member_role = Column(String(128), nullable=True, index=True)
    extension = Column(String(32), nullable=True, index=True)
    sha256 = Column(String(128), nullable=True, index=True)
    size_bytes = Column(Integer, nullable=True)
    storage_uri = Column(String(1024), nullable=True)
    content_bytes = Column(LargeBinary, nullable=True)
    content_text = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class AnalyticalDataset(AnalyticalBase):
    __tablename__ = "analytical_datasets"

    id = Column(String(36), primary_key=True)
    assay_type = Column(String(64), nullable=False, index=True)
    dataset_label = Column(String(512), nullable=False, index=True)
    description = Column(Text, nullable=True)
    primary_import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="SET NULL"), nullable=True, index=True)
    primary_assay_run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    batch_id = Column(String(255), nullable=True, index=True)
    construct_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    archived = Column(Boolean, nullable=False, default=False, index=True)
    tags_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)


class AnalyticalDatasetMember(AnalyticalBase):
    __tablename__ = "analytical_dataset_members"

    id = Column(String(36), primary_key=True)
    dataset_id = Column(String(36), ForeignKey("analytical_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="CASCADE"), nullable=True, index=True)
    assay_run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    role = Column(String(128), nullable=False, default="primary", index=True)
    order_index = Column(Integer, nullable=False, default=0)
    metadata_json = Column(JSON, nullable=False, default=dict)


class AnalyticalAnalysisRun(AnalyticalBase):
    __tablename__ = "analytical_analysis_runs"

    id = Column(String(36), primary_key=True)
    import_id = Column(String(36), ForeignKey("analytical_imports.id", ondelete="SET NULL"), nullable=True, index=True)
    dataset_id = Column(String(36), ForeignKey("analytical_datasets.id", ondelete="SET NULL"), nullable=True, index=True)
    assay_run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    assay_type = Column(String(64), nullable=False, index=True)
    analysis_kind = Column(String(128), nullable=False, index=True)
    analysis_engine = Column(String(128), nullable=False)
    engine_package = Column(String(128), nullable=True)
    engine_version = Column(String(128), nullable=True)
    parameters_json = Column(JSON, nullable=False, default=dict)
    input_selection_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(64), nullable=False, default="completed", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    result_summary_json = Column(JSON, nullable=False, default=dict)
    plotly_json = Column(JSON, nullable=True)
    report_uri = Column(String(1024), nullable=True)


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


class AssayRunSetting(AnalyticalBase):
    __tablename__ = "assay_run_settings"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(128), nullable=False, index=True)
    setting_key = Column(String(255), nullable=False, index=True)
    setting_value = Column(Text, nullable=True)
    numeric_value = Column(Float, nullable=True, index=True)
    unit = Column(String(128), nullable=True, index=True)
    source_member = Column(String(1024), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class QpcrWell(AnalyticalBase):
    __tablename__ = "qpcr_wells"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    plate_id = Column(String(255), nullable=True, index=True)
    well = Column(String(16), nullable=False, index=True)
    well_position = Column(String(16), nullable=True, index=True)
    sample_id = Column(String(255), nullable=True, index=True)
    sample_name = Column(String(512), nullable=True, index=True)
    sample_color = Column(String(64), nullable=True)
    target = Column(String(255), nullable=True, index=True)
    target_name = Column(String(255), nullable=True, index=True)
    target_color = Column(String(64), nullable=True)
    dye = Column(String(64), nullable=True)
    reporter = Column(String(64), nullable=True, index=True)
    quencher = Column(String(64), nullable=True, index=True)
    task = Column(String(64), nullable=True, index=True)
    cq = Column(Float, nullable=True)
    ct = Column(Float, nullable=True)
    cq_mean = Column(Float, nullable=True)
    ct_mean = Column(Float, nullable=True)
    ct_cv_percent = Column(Float, nullable=True)
    ct_sd = Column(Float, nullable=True)
    cq_sd = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    quantity_mean = Column(Float, nullable=True)
    quantity_sd = Column(Float, nullable=True)
    quantity_cv_percent = Column(Float, nullable=True)
    starting_quantity = Column(Float, nullable=True)
    dilution = Column(Float, nullable=True)
    dilution_factor = Column(Float, nullable=True)
    dilution_adjusted = Column(Float, nullable=True)
    percent_recovery = Column(Float, nullable=True)
    r_squared = Column(Float, nullable=True)
    slope = Column(Float, nullable=True)
    y_intercept = Column(Float, nullable=True)
    total_dna_per_ml = Column(Float, nullable=True)
    total_dna_per_protein_concentration = Column(Float, nullable=True)
    comments = Column(Text, nullable=True)
    is_omitted = Column(Boolean, nullable=True, index=True)
    baseline_start = Column(Integer, nullable=True)
    baseline_stop = Column(Integer, nullable=True)
    threshold = Column(Float, nullable=True)
    replicate_group = Column(String(255), nullable=True, index=True)
    flags_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)


class QpcrAmplificationPoint(AnalyticalBase):
    __tablename__ = "qpcr_amplification_points"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    well = Column(String(16), nullable=False, index=True)
    well_position = Column(String(16), nullable=True, index=True)
    cycle = Column(Integer, nullable=False, index=True)
    target_name = Column(String(255), nullable=True, index=True)
    rn = Column(Float, nullable=True)
    delta_rn = Column(Float, nullable=True)
    source_member = Column(String(1024), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class QpcrMulticomponentPoint(AnalyticalBase):
    __tablename__ = "qpcr_multicomponent_points"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    well = Column(String(16), nullable=False, index=True)
    well_position = Column(String(16), nullable=True, index=True)
    cycle = Column(Integer, nullable=False, index=True)
    dye = Column(String(64), nullable=False, index=True)
    signal_value = Column(Float, nullable=True)
    source_member = Column(String(1024), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class QpcrRawFluorescencePoint(AnalyticalBase):
    __tablename__ = "qpcr_raw_fluorescence_points"

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("assay_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    well = Column(String(16), nullable=False, index=True)
    well_position = Column(String(16), nullable=True, index=True)
    cycle = Column(Integer, nullable=False, index=True)
    channel = Column(String(128), nullable=False, index=True)
    fluorescence_value = Column(Float, nullable=True)
    source_member = Column(String(1024), nullable=True)
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
    sample_name = Column(String(512), nullable=True, index=True)
    sample_amount = Column(Float, nullable=True)
    sample_comment = Column(Text, nullable=True)
    vial = Column(String(128), nullable=True)
    injection_index = Column(Integer, nullable=True, index=True)
    injection_time = Column(DateTime, nullable=True, index=True)
    injection_volume = Column(Float, nullable=True)
    sample_type = Column(String(128), nullable=True, index=True)
    column_name = Column(String(255), nullable=True, index=True)
    column_serial_number = Column(String(255), nullable=True, index=True)
    system_name = Column(String(255), nullable=True, index=True)
    acquired_by = Column(String(255), nullable=True, index=True)
    acq_method_set = Column(String(512), nullable=True, index=True)
    instrument_method_id = Column(String(255), nullable=True, index=True)
    channel = Column(String(255), nullable=True)
    channel_description = Column(String(512), nullable=True)
    detector = Column(String(255), nullable=True)
    detector_unit = Column(String(128), nullable=True)
    retention_unit = Column(String(128), nullable=True)
    detector_minimum_value = Column(Float, nullable=True)
    detector_maximum_value = Column(Float, nullable=True)
    actual_run_time_length = Column(Float, nullable=True)
    actual_sampling_interval = Column(Float, nullable=True)
    actual_delay_time = Column(Float, nullable=True)
    method = Column(String(512), nullable=True, index=True)
    chromatogram_storage_uri = Column(String(1024), nullable=True)
    chromatogram_sha256 = Column(String(128), nullable=True)
    chromatogram_point_count = Column(Integer, nullable=True)
    raw_trace_storage_uri = Column(String(1024), nullable=True)
    raw_trace_sha256 = Column(String(128), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ChromTracePoint(AnalyticalBase):
    __tablename__ = "chrom_trace_points"

    id = Column(String(36), primary_key=True)
    injection_id = Column(String(36), ForeignKey("chrom_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    point_index = Column(Integer, nullable=False, index=True)
    retention_time = Column(Float, nullable=True, index=True)
    signal_value = Column(Float, nullable=True)
    retention_unit = Column(String(128), nullable=True)
    signal_unit = Column(String(128), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ChromPeak(AnalyticalBase):
    __tablename__ = "chrom_peaks"

    id = Column(String(36), primary_key=True)
    injection_id = Column(String(36), ForeignKey("chrom_injections.id", ondelete="CASCADE"), nullable=False, index=True)
    source_peak_index = Column(Integer, nullable=True, index=True)
    peak_name = Column(String(255), nullable=True, index=True)
    analyte = Column(String(255), nullable=True, index=True)
    isoform_class = Column(String(128), nullable=True, index=True)
    rt = Column(Float, nullable=True, index=True)
    start_rt = Column(Float, nullable=True)
    end_rt = Column(Float, nullable=True)
    area = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
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


def _new_id() -> str:
    return str(uuid.uuid4())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_extension(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower().strip()
    return ext or None


def _decode_text_maybe(data: bytes) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            text_value = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text_value[:1000]:
            return text_value
    return None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_metadata(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _archive_member_role(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith("plate_setup.xml"):
        return "plate_setup"
    if lower.endswith("analysis_protocol.xml"):
        return "analysis_protocol"
    if lower.endswith("multicomponentdata.xml"):
        return "multicomponent_curves"
    if lower.endswith("generic_properties.json"):
        return "generic_properties"
    if lower.endswith(".quant"):
        return "quant_raw_cycle_file"
    return None


async def persist_qpcr_import_response(
    response: dict[str, Any],
    *,
    source_bytes: bytes,
    filename: str | None,
    content_type: str | None = None,
    imported_by: str | None = None,
    notes: str | None = None,
) -> dict[str, str]:
    """Persist a parsed qPCR import into the analytical store and return durable IDs."""

    await ensure_analytical_store_available("qPCR import persistence")
    import_id = _new_id()
    source_file_id = _new_id()
    run_id = _new_id()
    dataset_id = _new_id()
    filename = filename or "qpcr-upload"
    digest = _sha256(source_bytes)
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(AnalyticalImport, AssayRun, AnalyticalDataset)
                .join(AssayRun, AssayRun.import_id == AnalyticalImport.id)
                .join(AnalyticalDataset, AnalyticalDataset.primary_import_id == AnalyticalImport.id)
                .where(AnalyticalImport.assay_type == "qpcr", AnalyticalImport.source_fingerprint == digest)
                .limit(1)
            )
        ).first()
        if existing is not None:
            imp, run, dataset = existing
            return {
                "analytical_import_id": imp.id,
                "assay_run_id": run.id,
                "analytical_source_file_id": (await session.execute(select(AnalyticalSourceFile.id).where(AnalyticalSourceFile.import_id == imp.id).limit(1))).scalar_one_or_none(),
                "dataset_id": dataset.id,
                "duplicate_detected": True,
                "action": "loaded_existing",
            }
        import_record = AnalyticalImport(
            id=import_id,
            assay_type="qpcr",
            source_filename=filename,
            source_file_hash=digest,
            source_fingerprint=digest,
            dataset_label=filename,
            parser_engine=str(response.get("import_engine") or "unknown"),
            instrument_format=response.get("instrument_format"),
            imported_by=imported_by,
            status="imported",
            notes=notes,
            metadata_json={
                "n_wells": response.get("n_wells"),
                "targets": response.get("targets", []),
                "samples": response.get("samples", []),
                "eds_summary": response.get("eds_summary"),
                "assay_summary": response.get("assay_summary"),
                "standard_curve_plotly_json": response.get("standard_curve_plotly_json"),
                "results_plotly_json": response.get("results_plotly_json"),
                "amplification_plotly_json": response.get("amplification_plotly_json"),
            },
        )
        session.add(import_record)
        await session.flush()

        source_file = AnalyticalSourceFile(
            id=source_file_id,
            import_id=import_id,
            filename=filename,
            original_relative_path=filename,
            file_extension=_file_extension(filename),
            content_type=content_type,
            sha256=digest,
            storage_uri=f"sha256:{digest}",
            content_bytes=source_bytes,
            size_bytes=len(source_bytes),
            role="source_qpcr_import",
            metadata_json={"instrument_format": response.get("instrument_format")},
        )
        run = AssayRun(
            id=run_id,
            import_id=import_id,
            assay_type="qpcr",
            run_label=filename,
            instrument=response.get("instrument_format"),
            metadata_json={"import_engine": response.get("import_engine"), "available_data": response.get("available_data", [])},
        )
        session.add(source_file)
        session.add(run)
        await session.flush()

        dataset = AnalyticalDataset(
            id=dataset_id,
            assay_type="qpcr",
            dataset_label=filename,
            primary_import_id=import_id,
            primary_assay_run_id=run_id,
            metadata_json={"source_fingerprint": digest, "instrument_format": response.get("instrument_format")},
        )
        session.add(dataset)
        await session.flush()

        dataset_member = AnalyticalDatasetMember(
            id=_new_id(),
            dataset_id=dataset_id,
            import_id=import_id,
            assay_run_id=run_id,
            role="primary",
            order_index=0,
            metadata_json={},
        )
        session.add(dataset_member)
        await session.flush()

        if zipfile.is_zipfile(io.BytesIO(source_bytes)):
            with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    name = member.filename
                    member_bytes = archive.read(name)
                    ext = os.path.splitext(name)[1].lower().lstrip(".") or None
                    session.add(
                        AnalyticalArchiveMember(
                            id=_new_id(),
                            source_file_id=source_file_id,
                            member_path=name,
                            member_role=_archive_member_role(name),
                            extension=ext,
                            sha256=_sha256(member_bytes),
                            size_bytes=len(member_bytes),
                            storage_uri=f"sha256:{_sha256(member_bytes)}",
                            content_bytes=member_bytes,
                            content_text=_decode_text_maybe(member_bytes),
                            metadata_json={},
                        )
                    )

        eds_summary = response.get("eds_summary") if isinstance(response.get("eds_summary"), dict) else {}
        settings = eds_summary.get("analysis_protocol_settings") if isinstance(eds_summary.get("analysis_protocol_settings"), dict) else {}
        for key, value in settings.items():
            numeric = _as_float(value)
            session.add(
                AssayRunSetting(
                    id=_new_id(),
                    run_id=run_id,
                    category="analysis_protocol",
                    setting_key=str(key),
                    setting_value=None if value is None else str(value),
                    numeric_value=numeric,
                    source_member="analysis_protocol.xml",
                    metadata_json={},
                )
            )

        wells = response.get("wells") if isinstance(response.get("wells"), list) else []
        for idx, well in enumerate(wells, start=1):
            if not isinstance(well, dict):
                continue
            well_label = str(well.get("well_position") or well.get("well") or idx)
            target_name = well.get("target_name") or well.get("target")
            qpcr_well_id = _new_id()
            session.add(
                QpcrWell(
                    id=qpcr_well_id,
                    run_id=run_id,
                    well=well_label,
                    well_position=well.get("well_position"),
                    sample_id=well.get("sample_id") or well.get("sample_name"),
                    sample_name=well.get("sample_name"),
                    target=target_name,
                    target_name=target_name,
                    reporter=well.get("reporter") or well.get("dye"),
                    quencher=well.get("quencher"),
                    task=well.get("task"),
                    cq=_as_float(well.get("cq")),
                    ct=_as_float(well.get("ct")),
                    cq_mean=_as_float(well.get("cq_mean")),
                    ct_mean=_as_float(well.get("ct_mean")),
                    ct_cv_percent=_as_float(well.get("ct_cv_percent")),
                    ct_sd=_as_float(well.get("ct_sd")),
                    cq_sd=_as_float(well.get("cq_sd")),
                    quantity=_as_float(well.get("quantity")),
                    quantity_mean=_as_float(well.get("quantity_mean")),
                    quantity_sd=_as_float(well.get("quantity_sd")),
                    quantity_cv_percent=_as_float(well.get("quantity_cv_percent")),
                    starting_quantity=_as_float(well.get("starting_quantity")),
                    dilution=_as_float(well.get("dilution")),
                    dilution_factor=_as_float(well.get("dilution_factor")),
                    percent_recovery=_as_float(well.get("percent_recovery")),
                    r_squared=_as_float(well.get("r_squared")),
                    slope=_as_float(well.get("slope")),
                    y_intercept=_as_float(well.get("y_intercept")),
                    comments=well.get("comments"),
                    is_omitted=well.get("is_omitted"),
                    baseline_start=_as_int(well.get("baseline_start")),
                    baseline_stop=_as_int(well.get("baseline_stop") or well.get("baseline_end")),
                    threshold=_as_float(well.get("threshold")),
                    replicate_group=well.get("group") or well.get("replicate_group"),
                    flags_json=[well.get("ct_status")] if well.get("ct_status") else [],
                    metadata_json={k: _clean_metadata(v) for k, v in well.items() if k not in {"amplification_curve"}},
                )
            )
            curve = well.get("amplification_curve") if isinstance(well.get("amplification_curve"), dict) else None
            if curve:
                cycles = curve.get("cycle") or []
                rn = curve.get("rn") or []
                delta_rn = curve.get("delta_rn") or []
                for point_idx, cycle in enumerate(cycles):
                    session.add(
                        QpcrAmplificationPoint(
                            id=_new_id(),
                            run_id=run_id,
                            well=well_label,
                            well_position=well.get("well_position"),
                            cycle=_as_int(cycle) or point_idx + 1,
                            target_name=target_name,
                            rn=_as_float(rn[point_idx]) if point_idx < len(rn) else None,
                            delta_rn=_as_float(delta_rn[point_idx]) if point_idx < len(delta_rn) else None,
                            source_member="multicomponentdata.xml",
                            metadata_json={},
                        )
                    )

        for target, fit in (response.get("standard_curve_stats_by_target") or {}).items():
            if not isinstance(fit, dict):
                continue
            session.add(
                QpcrStandardCurve(
                    id=_new_id(),
                    run_id=run_id,
                    target=str(target),
                    slope=_as_float(fit.get("slope")),
                    intercept=_as_float(fit.get("intercept")),
                    r_squared=_as_float(fit.get("r_squared")),
                    efficiency_percent=_as_float(fit.get("efficiency_percent")),
                    n_points=int(fit.get("n_points") or 0),
                    fit_json=_clean_metadata(fit),
                    qc_flags_json=fit.get("flags") or fit.get("qc_flags") or [],
                )
            )
        await session.commit()
    return {
        "analytical_import_id": import_id,
        "assay_run_id": run_id,
        "analytical_source_file_id": source_file_id,
        "dataset_id": dataset_id,
        "duplicate_detected": False,
        "action": "created",
    }


async def persist_empower_import_response(response: dict[str, Any]) -> dict[str, str]:
    """Persist parsed Empower/HPLC review rows into the analytical store."""

    await ensure_analytical_store_available("Empower import persistence")
    import_id = _new_id()
    run_id = _new_id()
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        import_record = AnalyticalImport(
            id=import_id,
            assay_type="chromatography",
            source_filename="empower-import",
            source_file_hash=None,
            parser_engine=str(response.get("import_engine") or "empower_cdf_arw_csv"),
            instrument_format="Waters Empower/AIA/ARW/CSV",
            metadata_json={"n_injections": response.get("n_injections"), "source_format_counts": response.get("source_format_counts", {})},
        )
        run = AssayRun(
            id=run_id,
            import_id=import_id,
            assay_type="chromatography",
            run_label="Empower import",
            instrument="Waters Empower",
            metadata_json={"sst_summary": response.get("sst_summary")},
        )
        session.add_all([import_record, run])
        await session.flush()
        for injection in response.get("injections", []) or []:
            if not isinstance(injection, dict):
                continue
            injection_id = _new_id()
            injection["analytical_injection_id"] = injection_id
            chrom = injection.get("chromatogram") if isinstance(injection.get("chromatogram"), dict) else {}
            session.add(
                ChromInjection(
                    id=injection_id,
                    run_id=run_id,
                    injection_name=injection.get("injection_number"),
                    sample_id=injection.get("sample_id") or injection.get("sample_name"),
                    sample_name=injection.get("sample_name"),
                    sample_type=injection.get("sample_type"),
                    injection_index=_as_int(injection.get("injection_number") or injection.get("id")),
                    method=injection.get("method_name"),
                    chromatogram_point_count=len(chrom.get("time_min") or []),
                    metadata_json=_clean_metadata(injection),
                )
            )
            times = chrom.get("time_min") or []
            signals = chrom.get("signal") or []
            for point_idx, time_value in enumerate(times):
                session.add(
                    ChromTracePoint(
                        id=_new_id(),
                        injection_id=injection_id,
                        point_index=point_idx,
                        retention_time=_as_float(time_value),
                        signal_value=_as_float(signals[point_idx]) if point_idx < len(signals) else None,
                    )
                )
            for peak_idx, peak in enumerate(injection.get("peaks", []) or [], start=1):
                if not isinstance(peak, dict):
                    continue
                peak_id = _new_id()
                peak["analytical_peak_id"] = peak_id
                session.add(
                    ChromPeak(
                        id=peak_id,
                        injection_id=injection_id,
                        source_peak_index=_as_int(peak.get("peak_id") or peak_idx),
                        peak_name=peak.get("name") or peak.get("peak_name"),
                        rt=_as_float(peak.get("retention_time") or peak.get("retention_time_min") or peak.get("rt")),
                        area=_as_float(peak.get("area")),
                        height=_as_float(peak.get("height")),
                        amount=_as_float(peak.get("amount")),
                        resolution=_as_float(peak.get("resolution")),
                        percent_area=_as_float(peak.get("area_percent") or peak.get("percent_area")),
                        metadata_json=_clean_metadata(peak),
                    )
                )
        await session.commit()
    return {"analytical_import_id": import_id, "assay_run_id": run_id}


async def list_analytical_datasets(assay_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    await ensure_analytical_store_available("analytical dataset listing")
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        stmt = select(AnalyticalDataset).where(AnalyticalDataset.archived == False)  # noqa: E712
        if assay_type:
            stmt = stmt.where(AnalyticalDataset.assay_type == assay_type)
        rows = (await session.execute(stmt.order_by(AnalyticalDataset.created_at.desc()).limit(limit))).scalars().all()
        return [
            {
                "dataset_id": row.id,
                "assay_type": row.assay_type,
                "dataset_label": row.dataset_label,
                "description": row.description,
                "primary_import_id": row.primary_import_id,
                "primary_assay_run_id": row.primary_assay_run_id,
                "project_id": row.project_id,
                "batch_id": row.batch_id,
                "construct_id": row.construct_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "tags": row.tags_json or [],
                "metadata": row.metadata_json or {},
            }
            for row in rows
        ]


async def load_analytical_dataset(dataset_id: str) -> dict[str, Any] | None:
    await ensure_analytical_store_available("analytical dataset load")
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        dataset = (await session.execute(select(AnalyticalDataset).where(AnalyticalDataset.id == dataset_id, AnalyticalDataset.archived == False))).scalar_one_or_none()  # noqa: E712
        if dataset is None:
            return None
        members = (await session.execute(select(AnalyticalDatasetMember).where(AnalyticalDatasetMember.dataset_id == dataset.id).order_by(AnalyticalDatasetMember.order_index))).scalars().all()
        sources = []
        if dataset.primary_import_id:
            source_rows = (await session.execute(select(AnalyticalSourceFile).where(AnalyticalSourceFile.import_id == dataset.primary_import_id))).scalars().all()
            sources = [
                {
                    "source_file_id": source.id,
                    "filename": source.filename,
                    "original_relative_path": source.original_relative_path,
                    "file_extension": source.file_extension,
                    "content_type": source.content_type,
                    "sha256": source.sha256,
                    "size_bytes": source.size_bytes,
                    "role": source.role,
                    "stored_in_db": source.content_bytes is not None,
                }
                for source in source_rows
            ]
        chromatography_injections: list[dict[str, Any]] = []
        chromatography_summary: dict[str, Any] | None = None
        qpcr_import: dict[str, Any] | None = None
        qpcr_summary: dict[str, Any] | None = None
        analysis_runs: list[dict[str, Any]] = []
        if dataset.primary_assay_run_id:
            analysis_rows = (
                await session.execute(
                    select(AnalyticalAnalysisRun)
                    .where(AnalyticalAnalysisRun.assay_run_id == dataset.primary_assay_run_id)
                    .order_by(AnalyticalAnalysisRun.created_at.desc())
                )
            ).scalars().all()
            analysis_runs = [
                {
                    "analysis_run_id": row.id,
                    "analysis_kind": row.analysis_kind,
                    "analysis_engine": row.analysis_engine,
                    "engine_package": row.engine_package,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "result_summary": row.result_summary_json or {},
                    "plotly_json": row.plotly_json or {},
                    "has_plotly_json": row.plotly_json is not None,
                }
                for row in analysis_rows
            ]
        if dataset.assay_type == "qpcr" and dataset.primary_import_id and dataset.primary_assay_run_id:
            import_row = (
                await session.execute(
                    select(AnalyticalImport).where(
                        AnalyticalImport.id == dataset.primary_import_id,
                        AnalyticalImport.assay_type == "qpcr",
                    )
                )
            ).scalar_one_or_none()
            well_rows = (
                await session.execute(
                    select(QpcrWell)
                    .where(QpcrWell.run_id == dataset.primary_assay_run_id)
                    .order_by(QpcrWell.well)
                )
            ).scalars().all()
            curve_rows = (
                await session.execute(select(QpcrStandardCurve).where(QpcrStandardCurve.run_id == dataset.primary_assay_run_id))
            ).scalars().all()
            metadata = import_row.metadata_json if import_row is not None and import_row.metadata_json else {}
            targets = metadata.get("targets", [])
            samples = metadata.get("samples", [])
            wells_payload = [
                {
                    "analytical_well_id": well.id,
                    "well_position": well.well_position,
                    "sample_name": well.sample_name,
                    "target_name": well.target_name,
                    "task": well.task,
                    "ct": well.ct,
                    "cq": well.cq,
                    "quantity": well.quantity,
                    "ct_status": (well.flags_json or [None])[0] if well.flags_json else None,
                    "reporter": well.reporter,
                    "quencher": well.quencher,
                }
                for well in well_rows
            ]
            qpcr_import = {
                "analytical_import_id": import_row.id if import_row is not None else dataset.primary_import_id,
                "assay_run_id": dataset.primary_assay_run_id,
                "filename": import_row.source_filename if import_row is not None else None,
                "import_engine": import_row.parser_engine if import_row is not None else None,
                "instrument_format": import_row.instrument_format if import_row is not None else None,
                "n_wells": len(well_rows),
                "targets": targets,
                "samples": samples,
                "wells": wells_payload,
                "standard_curve_stats_by_target": {curve.target: curve.fit_json for curve in curve_rows},
                "assay_summary": metadata.get("assay_summary"),
                "eds_summary": metadata.get("eds_summary"),
                "results_plotly_json": metadata.get("results_plotly_json"),
                "standard_curve_plotly_json": metadata.get("standard_curve_plotly_json"),
                "amplification_plotly_json": metadata.get("amplification_plotly_json"),
            }
            qpcr_summary = {
                "n_wells": len(well_rows),
                "n_standard_curves": len(curve_rows),
                "targets": targets,
                "samples": samples,
            }
        if dataset.assay_type == "chromatography" and dataset.primary_assay_run_id:
            chrom_rows = (
                await session.execute(
                    select(ChromInjection)
                    .where(ChromInjection.run_id == dataset.primary_assay_run_id)
                    .order_by(ChromInjection.injection_index, ChromInjection.injection_name)
                )
            ).scalars().all()
            peak_rows = []
            if chrom_rows:
                peak_rows = (await session.execute(select(ChromPeak).where(ChromPeak.injection_id.in_([row.id for row in chrom_rows])))).scalars().all()
            peaks_by_injection: dict[str, list[ChromPeak]] = {}
            for peak in peak_rows:
                peaks_by_injection.setdefault(peak.injection_id, []).append(peak)
            chromatography_injections = [
                {
                    "chrom_injection_id": row.id,
                    "injection_name": row.injection_name,
                    "sample_id": row.sample_id,
                    "sample_name": row.sample_name,
                    "sample_type": row.sample_type,
                    "injection_index": row.injection_index,
                    "injection_time": row.injection_time.isoformat() if row.injection_time else None,
                    "injection_volume": row.injection_volume,
                    "system_name": row.system_name,
                    "method": row.method,
                    "channel": row.channel,
                    "detector_unit": row.detector_unit,
                    "retention_unit": row.retention_unit,
                    "trace_point_count": row.chromatogram_point_count,
                    "chromatogram_sha256": row.chromatogram_sha256,
                    "peak_count": len(peaks_by_injection.get(row.id, [])),
                    "primary_peak_area": (row.metadata_json or {}).get("primary_peak_area"),
                    "primary_peak_percent": (row.metadata_json or {}).get("primary_peak_percent"),
                    "primary_peak_rt": (row.metadata_json or {}).get("primary_peak_rt"),
                }
                for row in chrom_rows
            ]
            chromatography_summary = {
                "n_injections": len(chrom_rows),
                "n_peaks": len(peak_rows),
                "n_trace_points": sum(int(row.chromatogram_point_count or 0) for row in chrom_rows),
                "sample_types": sorted({row.sample_type for row in chrom_rows if row.sample_type}),
                "methods": sorted({row.method for row in chrom_rows if row.method}),
            }
        payload = {
            "dataset_id": dataset.id,
            "assay_type": dataset.assay_type,
            "dataset_label": dataset.dataset_label,
            "description": dataset.description,
            "primary_import_id": dataset.primary_import_id,
            "primary_assay_run_id": dataset.primary_assay_run_id,
            "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
            "tags": dataset.tags_json or [],
            "metadata": dataset.metadata_json or {},
            "members": [
                {
                    "dataset_member_id": member.id,
                    "import_id": member.import_id,
                    "assay_run_id": member.assay_run_id,
                    "role": member.role,
                    "order_index": member.order_index,
                    "metadata": member.metadata_json or {},
                }
                for member in members
            ],
            "source_files": sources,
            "analysis_runs": analysis_runs,
        }
        if qpcr_summary is not None:
            payload["qpcr_summary"] = qpcr_summary
            payload["qpcr_import"] = qpcr_import
        if chromatography_summary is not None:
            payload["chromatography_summary"] = chromatography_summary
            payload["chromatography_injections"] = chromatography_injections
            payload["peak_table"] = [
                {
                    "analytical_peak_id": peak.id,
                    "analytical_injection_id": peak.injection_id,
                    "peak_id": peak.source_peak_index,
                    "peak_name": peak.peak_name,
                    "analyte": peak.analyte,
                    "isoform_class": peak.isoform_class,
                    "retention_time": peak.rt,
                    "retention_time_min": peak.rt,
                    "start_rt": peak.start_rt,
                    "end_rt": peak.end_rt,
                    "area": peak.area,
                    "area_percent": peak.percent_area,
                    "height": peak.height,
                    "amount": peak.amount,
                    "resolution": peak.resolution,
                    "width": peak.width,
                    "asymmetry": peak.asymmetry,
                    "tailing": peak.tailing,
                    "plates": peak.plates,
                    "signal_to_noise": peak.signal_to_noise,
                    "concentration": peak.concentration,
                    "peak_source": (peak.metadata_json or {}).get("peak_source"),
                }
                for peak in sorted(peak_rows, key=lambda item: ((item.injection_id or ""), item.source_peak_index or 0, item.rt or 0.0))
            ]
        return payload


async def list_qpcr_imports(limit: int = 50) -> list[dict[str, Any]]:
    await ensure_analytical_store_available("qPCR import listing")
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AnalyticalImport, AssayRun)
                .join(AssayRun, AssayRun.import_id == AnalyticalImport.id)
                .where(AnalyticalImport.assay_type == "qpcr")
                .order_by(AnalyticalImport.imported_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "analytical_import_id": imp.id,
                "assay_run_id": run.id,
                "source_filename": imp.source_filename,
                "instrument_format": imp.instrument_format,
                "parser_engine": imp.parser_engine,
                "imported_at": imp.imported_at.isoformat() if imp.imported_at else None,
                "n_wells": (imp.metadata_json or {}).get("n_wells"),
                "targets": (imp.metadata_json or {}).get("targets", []),
                "samples": (imp.metadata_json or {}).get("samples", []),
            }
            for imp, run in rows
        ]


async def load_qpcr_import(import_id: str) -> dict[str, Any] | None:
    await ensure_analytical_store_available("qPCR import load")
    session_factory = create_analytical_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                select(AnalyticalImport, AssayRun)
                .join(AssayRun, AssayRun.import_id == AnalyticalImport.id)
                .where(AnalyticalImport.id == import_id, AnalyticalImport.assay_type == "qpcr")
            )
        ).first()
        if row is None:
            return None
        imp, run = row
        wells = (await session.execute(select(QpcrWell).where(QpcrWell.run_id == run.id).order_by(QpcrWell.well))).scalars().all()
        curves = (await session.execute(select(QpcrStandardCurve).where(QpcrStandardCurve.run_id == run.id))).scalars().all()
        metadata = imp.metadata_json or {}
        return {
            "analytical_import_id": imp.id,
            "assay_run_id": run.id,
            "filename": imp.source_filename,
            "import_engine": imp.parser_engine,
            "instrument_format": imp.instrument_format,
            "n_wells": len(wells),
            "targets": metadata.get("targets", []),
            "samples": metadata.get("samples", []),
            "wells": [
                {
                    "analytical_well_id": well.id,
                    "well_position": well.well_position,
                    "sample_name": well.sample_name,
                    "target_name": well.target_name,
                    "task": well.task,
                    "ct": well.ct,
                    "cq": well.cq,
                    "quantity": well.quantity,
                    "ct_status": (well.flags_json or [None])[0] if well.flags_json else None,
                    "reporter": well.reporter,
                    "quencher": well.quencher,
                }
                for well in wells
            ],
            "standard_curve_stats_by_target": {curve.target: curve.fit_json for curve in curves},
            "assay_summary": metadata.get("assay_summary"),
            "eds_summary": metadata.get("eds_summary"),
            "results_plotly_json": metadata.get("results_plotly_json"),
            "standard_curve_plotly_json": metadata.get("standard_curve_plotly_json"),
            "amplification_plotly_json": metadata.get("amplification_plotly_json"),
        }


Index("ix_qpcr_wells_run_target_sample", QpcrWell.run_id, QpcrWell.target, QpcrWell.sample_id)
Index("ix_analytical_imports_assay_fingerprint", AnalyticalImport.assay_type, AnalyticalImport.source_fingerprint)
Index("ix_analytical_datasets_assay_created", AnalyticalDataset.assay_type, AnalyticalDataset.created_at)
Index("ix_analytical_dataset_members_dataset_role", AnalyticalDatasetMember.dataset_id, AnalyticalDatasetMember.role)
Index("ix_analytical_analysis_runs_dataset_kind", AnalyticalAnalysisRun.dataset_id, AnalyticalAnalysisRun.analysis_kind)
Index("ix_archive_members_source_role_ext", AnalyticalArchiveMember.source_file_id, AnalyticalArchiveMember.member_role, AnalyticalArchiveMember.extension)
Index("ix_assay_run_settings_run_category_key", AssayRunSetting.run_id, AssayRunSetting.category, AssayRunSetting.setting_key)
Index("ix_qpcr_amp_points_run_well_cycle", QpcrAmplificationPoint.run_id, QpcrAmplificationPoint.well, QpcrAmplificationPoint.cycle)
Index("ix_qpcr_multicomponent_run_well_cycle_dye", QpcrMulticomponentPoint.run_id, QpcrMulticomponentPoint.well, QpcrMulticomponentPoint.cycle, QpcrMulticomponentPoint.dye)
Index("ix_qpcr_raw_fluorescence_run_well_cycle_channel", QpcrRawFluorescencePoint.run_id, QpcrRawFluorescencePoint.well, QpcrRawFluorescencePoint.cycle, QpcrRawFluorescencePoint.channel)
Index("ix_chrom_trace_points_injection_point", ChromTracePoint.injection_id, ChromTracePoint.point_index)
Index("ix_chrom_trace_points_injection_rt", ChromTracePoint.injection_id, ChromTracePoint.retention_time)
Index("ix_chrom_peaks_injection_analyte_isoform", ChromPeak.injection_id, ChromPeak.analyte, ChromPeak.isoform_class)
Index("ix_qc_trends_metric_entity_time", QcTrend.metric_name, QcTrend.entity_type, QcTrend.entity_id, QcTrend.observed_at)

"""
Database models and initialization for BioModStack Control Platform.

Uses SQLAlchemy with async SQLite.
"""

from sqlalchemy import CheckConstraint, Column, String, Text, Integer, Float, Boolean, DateTime, Index, JSON, LargeBinary, ForeignKey, ForeignKeyConstraint, UniqueConstraint, text, event, func, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import Session, sessionmaker, declarative_base, relationship
from sqlalchemy.types import TypeDecorator
from datetime import datetime
from contextvars import ContextVar
import json
from types import SimpleNamespace
from paths import get_db_path, get_db_url
from migrations.sqlite_sha256 import register_sqlite_sha256
from services.ngs_molbio_quiescence import NgsMolBioQuiescedSession


# Database path - resolved via paths helper (supports env overrides)
DEFAULT_DB_PATH = get_db_path()
DATABASE_URL = get_db_url()
current_launch_context_id: ContextVar[str | None] = ContextVar("bms_launch_context_id", default=None)
LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY = "launch_context_binding"
LAUNCH_CONTEXT_BINDING_PROVENANCE_SCHEMA = "bms.launch-context-core-binding.v1"


def launch_context_binding_ready(job: object) -> bool:
    provenance_value = getattr(job, "provenance", None)
    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    launch_context_id = provenance.get("launch_context_id")
    if not isinstance(launch_context_id, str) or not launch_context_id:
        return True
    marker = provenance.get(LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY)
    if not isinstance(marker, dict) or set(marker) != {
        "schema", "launch_context_id", "run_attempt_id", "canonical_job_id",
        "binding_receipt_sha256",
    }:
        return False
    digest = marker.get("binding_receipt_sha256")
    return bool(
        marker.get("schema") == LAUNCH_CONTEXT_BINDING_PROVENANCE_SCHEMA
        and marker.get("launch_context_id") == launch_context_id
        and isinstance(marker.get("run_attempt_id"), str)
        and marker.get("run_attempt_id")
        and marker.get("canonical_job_id") == str(getattr(job, "id", ""))
        and isinstance(digest, str)
        and len(digest) == 64
        and digest == digest.lower()
        and all(character in "0123456789abcdef" for character in digest)
    )

def _canonical_sqlite_json(value: object) -> str:
    """Serialize JSON evidence exactly as the SHA-256 persistence contract requires."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


engine_kwargs: dict[str, object] = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"timeout": 30}
    engine_kwargs["json_serializer"] = _canonical_sqlite_json
engine = create_async_engine(DATABASE_URL, **engine_kwargs)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        """SQLite foreign-key constraints and deterministic manifest hashing per connection."""
        register_sqlite_sha256(dbapi_connection)
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    sync_session_class=NgsMolBioQuiescedSession,
)

Base = declarative_base()


class LenientSQLiteDateTime(TypeDecorator):
    """SQLite datetime adapter tolerant of legacy RFC3339 ``Z`` strings.

    SQLAlchemy's SQLite ``DateTime`` processor accepts its own emitted
    ``YYYY-MM-DD HH:MM:SS.ffffff`` form but rejects rows imported as
    ``YYYY-MM-DDTHH:MM:SS.ffffffZ``. One such imported job row must not brick
    the whole jobs list.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, datetime):
            return value
        raw = str(value)
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromisoformat(raw)


class Job(Base):
    """Pipeline job record."""
    __tablename__ = "jobs"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="queued")
    model_id = Column(String(50), nullable=False)
    mode = Column(String(100), nullable=False)  # monomer_denovo, binder_denovo, etc.
    params = Column(JSON, nullable=False)
    
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow)
    started_at = Column(LenientSQLiteDateTime, nullable=True)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)
    
    output_dir = Column(String(500), nullable=True)
    nextflow_run_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GPU ORCHESTRATOR: Queue Management
    # ═══════════════════════════════════════════════════════════════════════════
    batch_id = Column(String(36), nullable=True, index=True)  # Groups related jobs (UUID)
    batch_name = Column(String(255), nullable=True)  # Human-readable, auto-generated or user-set
    lineage_root_job_id = Column(String(36), nullable=True, index=True)  # Root job for this lineage branch
    stage_family = Column(String(64), nullable=True, index=True)  # e.g. rfantibody, fampnn, ppiflow
    stage_mode = Column(String(64), nullable=True, index=True)  # e.g. backbone_refine, maturation
    source_stage_job_id = Column(String(36), nullable=True, index=True)
    source_stage_family = Column(String(64), nullable=True, index=True)
    source_stage_mode = Column(String(64), nullable=True, index=True)
    source_selection_manifest_path = Column(String(500), nullable=True)
    source_selection_count = Column(Integer, nullable=True)
    selected_input_artifact_class = Column(String(64), nullable=True, index=True)
    selected_input_schema_version = Column(Integer, nullable=True)
    selection_source_type = Column(String(64), nullable=True)  # saved_dataset, selected_designs, etc.
    selection_source_job_id = Column(String(36), nullable=True, index=True)
    selection_dataset_name = Column(String(255), nullable=True)
    selected_loop_scope = Column(JSON, nullable=True)  # Selected loops / stage scope for re-orchestration
    provenance = Column(JSON, nullable=True)  # Optional lineage/provenance snapshot for the job
    saved_selection_sets = Column(JSON, default=list)
    queue_status = Column(String(20), nullable=False, default="queued")  # queued|running|completed|failed|paused
    paused = Column(Boolean, default=False)  # User manually paused this job
    pinned_gpu = Column(Integer, nullable=True)  # User override: force job to specific GPU (0-3)
    assigned_gpu = Column(Integer, nullable=True)  # Actual GPU when running
    priority = Column(Integer, default=0)  # Higher = runs first
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GPU ORCHESTRATOR: VRAM Estimation
    # ═══════════════════════════════════════════════════════════════════════════
    vram_estimate_mb = Column(Integer, nullable=True)  # Predicted VRAM need based on model + sequence
    sequence_length = Column(Integer, nullable=True)  # For VRAM calculation (longest chain)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GPU ORCHESTRATOR: OOM Recovery
    # ═══════════════════════════════════════════════════════════════════════════
    retry_count = Column(Integer, default=0)  # How many times this job has been retried
    max_retries = Column(Integer, default=2)  # User-configurable retry limit
    oom_tolerance = Column(String(20), default="allow")  # 'allow' = auto-retry, 'approve' = wait for user
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH: Parent-Child Job Linking
    # ═══════════════════════════════════════════════════════════════════════════
    parent_job_id = Column(String(36), nullable=True, index=True)  # MSA job that spawned this inference job
    job_phase = Column(String(20), default="inference")  # 'msa_generation' or 'inference'
    msa_sequences = Column(JSON, nullable=True)  # For MSA batch jobs: list of sequences to process
    msa_manifest_path = Column(String(500), nullable=True)  # Path to MSA outputs manifest
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ORCHESTRATOR CHILD JOBS: Spawn-Wait-Aggregate Pattern
    # ═══════════════════════════════════════════════════════════════════════════
    child_stage = Column(String(50), nullable=True)  # Stage this child handles: 'rfantibody', 'fampnn', 'boltz2'
    child_output_dir = Column(String(500), nullable=True)  # Absolute path to child's outputs for aggregation
    aggregated_by_parent = Column(Boolean, default=False)  # Flag to prevent double-collection
    child_design_count = Column(Integer, nullable=True)  # Number of designs/sequences this child processed
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STAGE CHECKPOINTING: Multi-stage pipeline tracking
    # ═══════════════════════════════════════════════════════════════════════════
    current_stage = Column(String(50), nullable=True)  # Currently running stage: 'rfantibody', 'fampnn', etc.
    stage_progress = Column(String(20), nullable=True)  # Granular progress: '5/30', '12/100', etc.
    stage_work_dir = Column(String(500), nullable=True)  # Current Nextflow work directory for log parsing
    completed_stages = Column(JSON, default=list)  # List of completed stages: ['rfantibody', 'fampnn']
    stage_outputs = Column(JSON, default=dict)  # Stage output paths: {'rfantibody': ['path/to/design_0.pdb', ...]}
    awaiting_input = Column(Boolean, default=False)
    awaiting_stage = Column(String(50), nullable=True)
    awaiting_payload = Column(JSON, default=dict)
    decision_history = Column(JSON, default=list)
    
    # Relationship to designs
    designs = relationship("Design", back_populates="job", cascade="all, delete-orphan")


class OntInstrumentRun(Base):
    """Current BMS-owned projection of one Mk1D/MinKNOW acquisition run."""

    __tablename__ = "ont_instrument_runs"
    __table_args__ = (
        CheckConstraint(
            "(terminal_artifact_manifest IS NULL) = (terminal_artifact_manifest_sha256 IS NULL)",
            name="ck_ont_run_terminal_manifest_pair",
        ),
        UniqueConstraint("external_registration_key", name="uq_ont_run_external_registration_key"),
    )

    id = Column(String(80), primary_key=True)
    position_id = Column(String(255), nullable=False, index=True)
    minknow_run_id = Column(String(255), nullable=True, unique=True, index=True)
    state = Column(String(32), nullable=False)
    observed_at = Column(LenientSQLiteDateTime, nullable=False)
    observed_generation = Column(Integer, nullable=False)
    sample_id = Column(String(255), nullable=True)
    experiment_group = Column(String(255), nullable=True)
    external_registration_key = Column(String(64), nullable=True)
    external_source_device = Column(Integer, nullable=True)
    external_source_inode = Column(Integer, nullable=True)
    external_source_bytes = Column(Integer, nullable=True)
    external_source_mtime_ns = Column(Integer, nullable=True)
    external_source_ctime_ns = Column(Integer, nullable=True)
    external_source_root_device = Column(Integer, nullable=True)
    external_source_root_inode = Column(Integer, nullable=True)
    external_source_relative_path = Column(String(1024), nullable=True)
    kit = Column(String(255), nullable=True)
    output_directories = Column(JSON, nullable=False, default=dict)
    output_files = Column(JSON, nullable=False, default=dict)
    handoff_ready = Column(Boolean, nullable=False, default=False)
    last_minknow_payload = Column(JSON, nullable=True)
    terminal_artifact_manifest = Column(JSON, nullable=True)
    terminal_artifact_manifest_sha256 = Column(String(64), nullable=True, index=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntInstrumentRunEvent(Base):
    """Append-only observation history for a BMS-owned ONT instrument run."""

    __tablename__ = "ont_instrument_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "observed_generation", name="uq_ont_run_event_generation"),
        Index("ix_ont_run_events_run_generation", "run_id", "observed_generation"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    state = Column(String(32), nullable=False)
    observed_at = Column(LenientSQLiteDateTime, nullable=False)
    observed_generation = Column(Integer, nullable=False)
    minknow_payload = Column(JSON, nullable=True)
    output_files = Column(JSON, nullable=False, default=dict)


class OntRawSignalRepresentation(Base):
    """Immutable-format representation bound to one exact ONT run generation."""

    __tablename__ = "ont_raw_signal_representations"
    __table_args__ = (
        UniqueConstraint("run_id", "observed_generation", "manifest_sha256", name="uq_ont_raw_signal_rep_manifest"),
        CheckConstraint("format IN ('pod5','slow5','blow5')", name="ck_ont_raw_signal_rep_format"),
        CheckConstraint("role IN ('source','derived')", name="ck_ont_raw_signal_rep_role"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    role = Column(String(16), nullable=False)
    source_kind = Column(String(32), nullable=False)
    format = Column(String(16), nullable=False, index=True)
    source_fidelity = Column(String(64), nullable=False, default="unknown")
    state = Column(String(32), nullable=False, index=True)
    reason_code = Column(String(96), nullable=False)
    artifact_manifest = Column(JSON, nullable=False)
    manifest_sha256 = Column(String(64), nullable=False, index=True)
    parent_representation_ids = Column(JSON, nullable=False, default=list)
    parent_manifest_sha256s = Column(JSON, nullable=False, default=list)
    compression = Column(JSON, nullable=False, default=dict)
    runtime_identity = Column(JSON, nullable=False, default=dict)
    validation_receipts = Column(JSON, nullable=False, default=dict)
    profile_id = Column(String(128), nullable=True)
    acquisition_id = Column(String(255), nullable=True)
    read_count = Column(Integer, nullable=True)
    published_at = Column(LenientSQLiteDateTime, nullable=True)
    retention_pinned_at = Column(LenientSQLiteDateTime, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntRawSignalDerivationJob(Base):
    """Durable leased request for one governed raw-signal derivation."""

    __tablename__ = "ont_raw_signal_derivation_jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "observed_generation", "source_representation_id", "profile_id", name="uq_ont_raw_signal_derivation"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    source_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    output_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=True)
    requested_preference = Column(String(16), nullable=False)
    consumer_id = Column(String(128), nullable=False)
    profile_id = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="requested", index=True)
    reason_code = Column(String(96), nullable=False, default="conversion_requested")
    resource_snapshot = Column(JSON, nullable=False, default=dict)
    attempt = Column(Integer, nullable=False, default=0)
    claim_token = Column(String(96), nullable=True, unique=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True, index=True)
    cancel_requested_at = Column(LenientSQLiteDateTime, nullable=True)
    stage_receipts = Column(JSON, nullable=False, default=dict)
    failure_code = Column(String(96), nullable=True)
    failure_message = Column(Text, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class OntRawSignalDerivationEvent(Base):
    """Append-only transition receipt for one raw-signal derivation request."""

    __tablename__ = "ont_raw_signal_derivation_events"

    id = Column(String(96), primary_key=True)
    job_id = Column(String(96), ForeignKey("ont_raw_signal_derivation_jobs.id", ondelete="RESTRICT"), nullable=False, index=True)
    state = Column(String(32), nullable=False)
    reason_code = Column(String(96), nullable=False)
    receipt = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntRawSignalLookup(Base):
    """Bounded selected-read waveform lookup outside HTTP execution."""

    __tablename__ = "ont_raw_signal_lookups"
    __table_args__ = (
        UniqueConstraint("representation_id", "read_id", name="uq_ont_raw_signal_lookup_read"),
        Index("ix_ont_raw_signal_lookups_state", "state"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    read_id = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="requested")
    reason_code = Column(String(96), nullable=False, default="requested")
    claim_token = Column(String(96), unique=True, nullable=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True)
    sample_count = Column(Integer, nullable=True)
    samples = Column(JSON, nullable=True)
    receipt = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class OntMoveTableSource(Base):
    """Immutable, fully validated move-tag BAM authority for one run generation."""

    __tablename__ = "ont_move_table_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "observed_generation", "artifact_sha256", name="uq_ont_move_source_artifact"),
        CheckConstraint("molecule_type IN ('dna','rna')", name="ck_ont_move_source_molecule"),
        CheckConstraint("validation_state IN ('requested','running','ready','failed')", name="ck_ont_move_source_state"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    raw_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    input_file_id = Column(String(36), ForeignKey("input_files.id", ondelete="RESTRICT"), nullable=False)
    source_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True)
    external_registration_receipt_id = Column(String(128), nullable=True)
    artifact_sha256 = Column(String(64), nullable=False)
    artifact_size_bytes = Column(Integer, nullable=False)
    bam_header_sha256 = Column(String(64), nullable=True)
    record_count = Column(Integer, nullable=True)
    unique_read_count = Column(Integer, nullable=True)
    mv_tag_count = Column(Integer, nullable=True)
    ts_tag_count = Column(Integer, nullable=True)
    ns_tag_count = Column(Integer, nullable=True)
    basecall_model_id = Column(String(255), nullable=True)
    molecule_type = Column(String(16), nullable=False)
    source_runtime_identity = Column(JSON, nullable=False, default=dict)
    read_inventory_sha256 = Column(String(64), nullable=True)
    validation_state = Column(String(32), nullable=False, default="requested", index=True)
    reason_code = Column(String(96), nullable=False, default="move_source_validation_requested")
    validation_receipt = Column(JSON, nullable=False, default=dict)
    claim_token = Column(String(96), nullable=True, unique=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    validated_at = Column(LenientSQLiteDateTime, nullable=True)


class OntSignalCalibrationArtifact(Base):
    __tablename__ = "ont_signal_calibration_artifacts"

    id = Column(String(96), primary_key=True)
    raw_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    move_source_id = Column(String(96), ForeignKey("ont_move_table_sources.id", ondelete="RESTRICT"), nullable=False)
    basecall_model_id = Column(String(255), nullable=False)
    sample_selection = Column(JSON, nullable=False)
    recommended_kmer_length = Column(Integer, nullable=False)
    recommended_signal_move_offset = Column(Integer, nullable=False)
    score_evidence = Column(JSON, nullable=False)
    runtime_identity = Column(JSON, nullable=False)
    parent_sha256s = Column(JSON, nullable=False)
    artifact_sha256 = Column(String(64), nullable=False, unique=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntSignalCalibrationJob(Base):
    """Leased deterministic calibration request for one exact signal/move authority."""

    __tablename__ = "ont_signal_calibration_jobs"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", name="uq_ont_signal_calibration_request"),
        CheckConstraint("state IN ('requested','running','ready','failed','cancelled')", name="ck_ont_signal_calibration_state"),
        CheckConstraint("sample_count >= 1 AND sample_count <= 100", name="ck_ont_signal_calibration_sample_count"),
        CheckConstraint("failure_message IS NULL OR length(failure_message) <= 4000", name="ck_ont_signal_calibration_failure_message"),
    )

    id = Column(String(96), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    raw_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    move_source_id = Column(String(96), ForeignKey("ont_move_table_sources.id", ondelete="RESTRICT"), nullable=False)
    sample_count = Column(Integer, nullable=False)
    request_fingerprint = Column(String(64), nullable=False, unique=True)
    state = Column(String(32), nullable=False, default="requested", index=True)
    reason_code = Column(String(96), nullable=False, default="calibration_requested")
    attempt = Column(Integer, nullable=False, default=0)
    claim_token = Column(String(96), nullable=True, unique=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True, index=True)
    cancel_requested_at = Column(LenientSQLiteDateTime, nullable=True)
    resource_snapshot = Column(JSON, nullable=False, default=dict)
    stage_receipts = Column(JSON, nullable=False, default=dict)
    calibration_artifact_id = Column(String(96), ForeignKey("ont_signal_calibration_artifacts.id", ondelete="RESTRICT"), nullable=True, unique=True)
    failure_code = Column(String(96), nullable=True)
    failure_message = Column(Text, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class OntSignalMappingProfile(Base):
    """Operator-approved, model-exact reform and alignment policy."""

    __tablename__ = "ont_signal_mapping_profiles"
    __table_args__ = (
        CheckConstraint("molecule_type IN ('dna','rna')", name="ck_ont_signal_profile_molecule"),
        CheckConstraint("parameter_source = 'approved_calibration'", name="ck_ont_signal_profile_calibrated_only"),
        CheckConstraint("calibration_artifact_id IS NOT NULL", name="ck_ont_signal_profile_calibration_required"),
        CheckConstraint("primary_alignment_policy = 'primary_only'", name="ck_ont_signal_profile_primary_only"),
        CheckConstraint("minimum_mapq = 0", name="ck_ont_signal_profile_mapq_zero"),
        CheckConstraint("include_supplementary = 0", name="ck_ont_signal_profile_no_supplementary"),
        CheckConstraint("read_set_selection = 'immutable_full_set'", name="ck_ont_signal_profile_full_set"),
        UniqueConstraint(
            "basecall_model_id", "molecule_type", "kmer_length", "signal_move_offset",
            "calibration_artifact_id", "minimum_mapq", "read_set_selection",
            name="uq_ont_signal_profile_calibration",
        ),
    )

    id = Column(String(96), primary_key=True)
    name = Column(String(255), nullable=False)
    molecule_type = Column(String(16), nullable=False)
    basecall_model_id = Column(String(255), nullable=False)
    kmer_length = Column(Integer, nullable=False)
    signal_move_offset = Column(Integer, nullable=False)
    parameter_source = Column(String(32), nullable=False)
    calibration_artifact_id = Column(String(96), ForeignKey("ont_signal_calibration_artifacts.id", ondelete="RESTRICT"), nullable=False)
    primary_alignment_policy = Column(String(32), nullable=False, default="primary_only")
    minimum_mapq = Column(Integer, nullable=False, default=0)
    include_supplementary = Column(Boolean, nullable=False, default=False)
    read_set_selection = Column(String(32), nullable=False, default="immutable_full_set")
    approval_receipt = Column(JSON, nullable=False)
    approved_at = Column(LenientSQLiteDateTime, nullable=False)
    approved_by = Column(String(255), nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntSignalMappingJob(Base):
    """Leased reusable reform/realign derivation with exact governed parents."""

    __tablename__ = "ont_signal_mapping_jobs"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", name="uq_ont_signal_mapping_request"),
        CheckConstraint("state IN ('requested','running','ready','failed','cancelled')", name="ck_ont_signal_mapping_state"),
        CheckConstraint("mode IN ('signal_to_read','signal_to_reference')", name="ck_ont_signal_mapping_mode"),
    )

    id = Column(String(96), primary_key=True)
    mode = Column(String(32), nullable=False)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False, index=True)
    raw_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=False)
    move_source_id = Column(String(96), ForeignKey("ont_move_table_sources.id", ondelete="RESTRICT"), nullable=False)
    mapping_profile_id = Column(String(96), ForeignKey("ont_signal_mapping_profiles.id", ondelete="RESTRICT"), nullable=False)
    reference_revision_id = Column(String(128), nullable=True)
    alignment_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True)
    alignment_session_id = Column(String(96), nullable=True)
    parent_mapping_job_id = Column(String(96), ForeignKey("ont_signal_mapping_jobs.id", ondelete="RESTRICT"), nullable=True)
    request_fingerprint = Column(String(64), nullable=False, unique=True)
    state = Column(String(32), nullable=False, default="requested", index=True)
    reason_code = Column(String(96), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    claim_token = Column(String(96), nullable=True, unique=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True)
    cancel_requested_at = Column(LenientSQLiteDateTime, nullable=True)
    resource_snapshot = Column(JSON, nullable=False, default=dict)
    stage_receipts = Column(JSON, nullable=False, default=dict)
    failure_code = Column(String(96), nullable=True)
    failure_message = Column(Text, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class OntSignalMappingEvent(Base):
    __tablename__ = "ont_signal_mapping_events"

    id = Column(String(96), primary_key=True)
    job_id = Column(String(96), ForeignKey("ont_signal_mapping_jobs.id", ondelete="RESTRICT"), nullable=False, index=True)
    state = Column(String(32), nullable=False)
    reason_code = Column(String(96), nullable=False)
    receipt = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntSignalMappingArtifact(Base):
    __tablename__ = "ont_signal_mapping_artifacts"

    id = Column(String(96), primary_key=True)
    mapping_job_id = Column(String(96), ForeignKey("ont_signal_mapping_jobs.id", ondelete="RESTRICT"), nullable=False, index=True)
    kind = Column(String(64), nullable=False)
    managed_relative_path = Column(Text, nullable=False, unique=True)
    media_type = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    parent_identities = Column(JSON, nullable=False)
    runtime_identity = Column(JSON, nullable=False)
    validation_receipt = Column(JSON, nullable=False)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntSquigualiserViewJob(Base):
    __tablename__ = "ont_squigualiser_view_jobs"

    id = Column(String(96), primary_key=True)
    mapping_artifact_id = Column(String(96), ForeignKey("ont_signal_mapping_artifacts.id", ondelete="RESTRICT"), nullable=False)
    mode = Column(String(32), nullable=False)
    read_id = Column(String(128), nullable=True)
    reference_contig = Column(String(255), nullable=True)
    reference_start = Column(Integer, nullable=True)
    reference_end = Column(Integer, nullable=True)
    render_params = Column(JSON, nullable=False)
    request_fingerprint = Column(String(64), nullable=False, unique=True)
    state = Column(String(32), nullable=False, default="requested", index=True)
    reason_code = Column(String(96), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    claim_token = Column(String(96), nullable=True, unique=True)
    lease_expires_at = Column(LenientSQLiteDateTime, nullable=True)
    cancel_requested_at = Column(LenientSQLiteDateTime, nullable=True)
    output_manifest = Column(JSON, nullable=False, default=dict)
    render_receipt = Column(JSON, nullable=False, default=dict)
    failure_code = Column(String(96), nullable=True)
    failure_message = Column(Text, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class OntSignalViewerSession(Base):
    __tablename__ = "ont_signal_viewer_sessions"

    id = Column(String(96), primary_key=True)
    dataset_id = Column(String(128), nullable=False)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    observed_generation = Column(Integer, nullable=False)
    alignment_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True)
    alignment_session_id = Column(String(96), nullable=True)
    reference_revision_id = Column(String(128), nullable=True)
    raw_representation_id = Column(String(96), ForeignKey("ont_raw_signal_representations.id", ondelete="RESTRICT"), nullable=True)
    move_source_id = Column(String(96), ForeignKey("ont_move_table_sources.id", ondelete="RESTRICT"), nullable=True)
    mapping_profile_id = Column(String(96), ForeignKey("ont_signal_mapping_profiles.id", ondelete="RESTRICT"), nullable=True)
    contig = Column(String(255), nullable=True)
    locus_start = Column(Integer, nullable=True)
    locus_end = Column(Integer, nullable=True)
    selected_read_id = Column(String(128), nullable=True)
    igv_state = Column(JSON, nullable=False, default=dict)
    signal_state = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntProtocolOptionReceipt(Base):
    """Expiring server-owned receipt for one normalized MinKNOW protocol option."""

    __tablename__ = "ont_protocol_option_receipts"

    id = Column(String(80), primary_key=True)
    option_id = Column(String(80), nullable=False, unique=True)
    position_id = Column(String(255), nullable=False, index=True)
    flow_cell_identity_sha256 = Column(String(64), nullable=False)
    source_digest = Column(String(64), nullable=False)
    capability_digest = Column(String(64), nullable=False)
    source_snapshot = Column(JSON, nullable=False)
    expires_at = Column(LenientSQLiteDateTime, nullable=False, index=True)
    consumed_at = Column(LenientSQLiteDateTime, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class OntInstrumentRunPreflight(Base):
    """Immutable protocol/flow-cell preflight bound to one durable ONT ledger row."""

    __tablename__ = "ont_instrument_run_preflights"

    id = Column(String(80), primary_key=True)
    run_id = Column(String(80), ForeignKey("ont_instrument_runs.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True)
    option_receipt_id = Column(String(80), ForeignKey("ont_protocol_option_receipts.id", ondelete="RESTRICT"), nullable=False, unique=True)
    selected_option_id = Column(String(80), nullable=False)
    flow_cell_identity_sha256 = Column(String(64), nullable=False)
    source_digest = Column(String(64), nullable=False)
    capability_digest = Column(String(64), nullable=False)
    source_snapshot = Column(JSON, nullable=False)
    expires_at = Column(LenientSQLiteDateTime, nullable=False)
    invalidated_at = Column(LenientSQLiteDateTime, nullable=True)
    invalidation_reason = Column(String(255), nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class ShapeCadSource(Base):
    """Shared immutable CAD source bytes for the internal Shape workflow."""

    __tablename__ = "shape_cad_sources"

    source_id = Column(String(40), primary_key=True)
    source_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    size_bytes = Column(Integer, nullable=False)
    original_filename = Column(String(255), nullable=False)
    relative_path = Column(String(500), nullable=False)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class ShapeDesignGeometry(Base):
    """Deterministic canonical geometry derived from one CAD source."""

    __tablename__ = "shape_design_geometries"
    __table_args__ = (
        UniqueConstraint("source_id", "conversion_sha256", name="uq_shape_geometry_conversion"),
    )

    geometry_id = Column(String(41), primary_key=True)
    source_id = Column(String(40), ForeignKey("shape_cad_sources.source_id"), nullable=False, index=True)
    geometry_sha256 = Column(String(64), nullable=False, index=True)
    conversion_sha256 = Column(String(64), nullable=False)
    angstrom_per_unit = Column(Float, nullable=False)
    vertex_count = Column(Integer, nullable=False)
    face_count = Column(Integer, nullable=False)
    point_count = Column(Integer, nullable=False)
    manifest = Column(JSON, nullable=False)
    artifacts = Column(JSON, nullable=False)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class ShapeDesignRequest(Base):
    """Immutable Shape scientific intent linked to the existing Job lifecycle."""

    __tablename__ = "shape_design_requests"

    request_id = Column(String(42), primary_key=True)
    geometry_id = Column(String(41), ForeignKey("shape_design_geometries.geometry_id"), nullable=False, index=True)
    request_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    request_spec = Column(JSON, nullable=False)
    stage_relative_path = Column(String(500), nullable=False)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True, unique=True, index=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class MolBioNgsReceipt(Base):
    """One-time server-issued binding from an immutable MolBio revision to ONT input."""

    __tablename__ = "molbio_ngs_receipts"

    id = Column(String(36), primary_key=True)
    sequence_id = Column(String(36), nullable=False, index=True)
    revision_id = Column(String(36), nullable=False, index=True)
    revision_sha256 = Column(String(64), nullable=False)
    reference_snapshot_path = Column(String(1000), nullable=False)
    reference_snapshot_sha256 = Column(String(64), nullable=False)
    expires_at = Column(LenientSQLiteDateTime, nullable=False, index=True)
    consumed_at = Column(LenientSQLiteDateTime, nullable=True)
    consumed_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True, unique=True)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class ApprovedNgsComparisonPanel(Base):
    """Server-owned immutable comparison reference panel version."""

    __tablename__ = "approved_ngs_comparison_panels"

    id = Column(String(36), primary_key=True)
    version = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="APPROVED")
    label = Column(String(255), nullable=False)
    manifest_path = Column(String(1000), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(255), nullable=False)


class NgsComparisonPanelReceipt(Base):
    """One-time server receipt for an APPROVED panel snapshot."""

    __tablename__ = "ngs_comparison_panel_receipts"

    id = Column(String(36), primary_key=True)
    panel_id = Column(String(36), ForeignKey("approved_ngs_comparison_panels.id"), nullable=False, index=True)
    panel_version = Column(Integer, nullable=False)
    panel_snapshot_path = Column(String(1000), nullable=False)
    panel_snapshot_sha256 = Column(String(64), nullable=False)
    expected_receipt_id = Column(String(36), ForeignKey("molbio_ngs_receipts.id"), nullable=False)
    expires_at = Column(LenientSQLiteDateTime, nullable=False, index=True)
    consumed_at = Column(LenientSQLiteDateTime, nullable=True)
    consumed_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True, unique=True)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class NgsReferenceSetManifest(Base):
    """Immutable server-owned reference-set launch authority for one barcode batch."""

    __tablename__ = "ngs_reference_set_manifests"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ngs_reference_set_idempotency"),
        Index("ix_ngs_reference_set_manifests_source_job_id", "source_job_id"),
        Index("ix_ngs_reference_set_manifests_manifest_sha256", "manifest_sha256"),
    )

    id = Column(String(36), primary_key=True)
    manifest_schema = Column(String(80), nullable=False)
    mode = Column(String(32), nullable=False)
    source_job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    target_workflow = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    manifest_path = Column(String(1000), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    manifest_json = Column(JSON, nullable=False)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class NgsReferenceSetMapping(Base):
    """Immutable barcode-to-revision binding within one NGS reference set."""

    __tablename__ = "ngs_reference_set_mappings"
    __table_args__ = (
        UniqueConstraint(
            "reference_set_id",
            "unit_id",
            name="uq_ngs_reference_set_mapping_unit",
        ),
        UniqueConstraint("receipt_id", name="uq_ngs_reference_set_mapping_receipt"),
        UniqueConstraint("child_job_id", name="uq_ngs_reference_set_mapping_child_job"),
        Index("ix_ngs_reference_set_mappings_reference_set_id", "reference_set_id"),
        Index("ix_ngs_reference_set_mappings_unit_id", "unit_id"),
    )

    id = Column(String(36), primary_key=True)
    reference_set_id = Column(
        String(36),
        ForeignKey("ngs_reference_set_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    child_job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    unit_id = Column(String(32), nullable=False)
    sample_alias = Column(String(255), nullable=True)
    sequence_id = Column(String(36), nullable=False)
    revision_id = Column(String(36), nullable=False)
    revision_sha256 = Column(String(64), nullable=False)
    receipt_id = Column(
        String(36), ForeignKey("molbio_ngs_receipts.id", ondelete="RESTRICT"), nullable=False
    )
    fasta_snapshot_sha256 = Column(String(64), nullable=False)
    source_bam_path = Column(String(1000), nullable=False)
    source_bam_sha256 = Column(String(64), nullable=False)
    source_calls_sha256 = Column(String(64), nullable=False)
    preflight_sha256 = Column(String(64), nullable=False)
    demux_manifest_sha256 = Column(String(64), nullable=False)
    unit_manifest_sha256 = Column(String(64), nullable=False)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class NgsPooledReferenceTarget(Base):
    """Immutable target identity within a pooled reference-set manifest."""

    __tablename__ = "ngs_pooled_reference_targets"
    __table_args__ = (
        UniqueConstraint(
            "reference_set_id",
            "target_id",
            name="uq_ngs_pooled_reference_target_id",
        ),
        UniqueConstraint("receipt_id", name="uq_ngs_pooled_reference_target_receipt"),
        Index("ix_ngs_pooled_reference_targets_reference_set_id", "reference_set_id"),
        Index("ix_ngs_pooled_reference_targets_target_id", "target_id"),
    )

    id = Column(String(36), primary_key=True)
    reference_set_id = Column(
        String(36),
        ForeignKey("ngs_reference_set_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_id = Column(String(128), nullable=False)
    label = Column(String(255), nullable=False)
    indistinguishable_group = Column(String(128), nullable=True)
    sequence_id = Column(String(128), nullable=False)
    revision_id = Column(String(128), nullable=False)
    revision_sha256 = Column(String(64), nullable=False)
    receipt_id = Column(
        String(36), ForeignKey("molbio_ngs_receipts.id", ondelete="RESTRICT"), nullable=False
    )
    fasta_path = Column(String(512), nullable=False)
    fasta_sha256 = Column(String(64), nullable=False)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class NgsPooledAssignmentRelease(Base):
    """Append-only operator release of reviewed pooled-assignment evidence."""

    __tablename__ = "ngs_pooled_assignment_releases"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ngs_pooled_assignment_release_idempotency"),
        Index("ix_ngs_pooled_assignment_releases_assignment_job_id", "assignment_job_id"),
        Index("ix_ngs_pooled_assignment_releases_reference_set_id", "reference_set_id"),
    )

    id = Column(String(36), primary_key=True)
    assignment_job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    reference_set_id = Column(
        String(36),
        ForeignKey("ngs_reference_set_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key = Column(String(255), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    target_workflow = Column(String(64), nullable=False)
    assignment_summary_path = Column(String(1000), nullable=False)
    assignment_summary_sha256 = Column(String(64), nullable=False)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class NgsPooledAssignmentReleaseTarget(Base):
    """Immutable target-to-child binding within one pooled assignment release."""

    __tablename__ = "ngs_pooled_assignment_release_targets"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "target_id",
            name="uq_ngs_pooled_assignment_release_target",
        ),
        UniqueConstraint("child_job_id", name="uq_ngs_pooled_assignment_release_child"),
        Index("ix_ngs_pooled_assignment_release_targets_release_id", "release_id"),
        Index("ix_ngs_pooled_assignment_release_targets_assignment_job_id", "assignment_job_id"),
    )

    id = Column(String(36), primary_key=True)
    release_id = Column(
        String(36),
        ForeignKey("ngs_pooled_assignment_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignment_job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    reference_set_id = Column(
        String(36),
        ForeignKey("ngs_reference_set_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_id = Column(String(128), nullable=False)
    child_job_id = Column(
        String(36), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_id = Column(String(128), nullable=False)
    revision_id = Column(String(128), nullable=False)
    revision_sha256 = Column(String(64), nullable=False)
    receipt_id = Column(
        String(36), ForeignKey("molbio_ngs_receipts.id", ondelete="RESTRICT"), nullable=False
    )
    fasta_path = Column(String(1000), nullable=False)
    fasta_sha256 = Column(String(64), nullable=False)
    assigned_fastq_path = Column(String(1000), nullable=False)
    assigned_fastq_sha256 = Column(String(64), nullable=False)
    assigned_read_count = Column(Integer, nullable=False)
    created_at = Column(LenientSQLiteDateTime, default=datetime.utcnow, nullable=False)


class ViewerSnapshotRecord(Base):
    """Immutable, job-owned M6A viewer snapshot metadata and canonical JSON."""

    __tablename__ = "viewer_snapshots"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False, index=True)
    label = Column(String(120), nullable=False)
    created_by = Column(String(128), nullable=False)
    schema_version = Column(Integer, nullable=False, default=2)
    snapshot_sha256 = Column(String(64), nullable=False, index=True)
    snapshot_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ExternalResultImport(Base):
    """Durable state for importing one immutable external-provider result."""

    __tablename__ = "external_result_imports"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "resource_type",
            "provider_job_id",
            name="uq_external_result_import_identity",
        ),
    )

    id = Column(String(36), primary_key=True)
    provider_id = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(128), nullable=False, index=True)
    provider_job_id = Column(String(128), nullable=False, index=True)
    state = Column(String(32), nullable=False, default="discovered", index=True)
    source_path = Column(String(1000), nullable=False)
    source_fingerprint = Column(String(64), nullable=False)
    run_metadata_sha256 = Column(String(64), nullable=False)
    archive_sha256 = Column(String(64), nullable=True)
    normalized_manifest_path = Column(String(1000), nullable=True)
    bms_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True, index=True)
    dataset_name = Column(String(255), nullable=False)
    job_name = Column(String(255), nullable=True)
    failure_code = Column(String(64), nullable=True)
    failure_message = Column(Text, nullable=True)
    provider_metadata = Column(JSON, nullable=False, default=dict)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    imported_at = Column(DateTime, nullable=True)


@event.listens_for(Session, "before_flush")
def _bind_new_jobs_to_launch_context(session: Session, _flush_context, _instances) -> None:
    for record in session.dirty:
        if not isinstance(record, Job):
            continue
        history = inspect(record).attrs.provenance.history
        if not history.has_changes() or not history.deleted:
            continue
        previous = dict(history.deleted[0] or {})
        current = dict(record.provenance or {})
        previous_context = previous.get("launch_context_id")
        if previous_context is not None and current.get("launch_context_id") != previous_context:
            raise ValueError("Job launch-context provenance cannot change")
        previous_binding = previous.get(LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY)
        if (
            previous_binding is not None
            and current.get(LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY) != previous_binding
        ):
            raise ValueError("Job launch-context binding cannot change")

    launch_context_id = current_launch_context_id.get()
    if launch_context_id is None:
        return
    for record in session.new:
        if isinstance(record, Job):
            provenance = dict(record.provenance or {})
            existing = provenance.get("launch_context_id")
            if existing is not None and existing != launch_context_id:
                raise ValueError("Job launch-context provenance cannot change")
            existing_job_id = session.execute(
                select(Job.id)
                .where(func.json_extract(Job.provenance, "$.launch_context_id") == launch_context_id)
                .limit(1)
            ).scalar_one_or_none()
            if existing_job_id is not None and existing_job_id != record.id:
                raise ValueError("launch context is already bound to a canonical Job")
            provenance["launch_context_id"] = launch_context_id
            record.provenance = provenance


class ScientificArtifactJSON(TypeDecorator):
    """JSON column that resolves governed Parquet references on ORM reads."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or isinstance(value, dict):
            return value
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, dict) and value.get("schema") in {
            "bms.scientific-artifact-reference.v1",
            "bms.scientific-artifact-row-reference.v1",
        }:
            from services.scientific_artifacts import resolve_json_value
            return resolve_json_value(value)
        return value


class Design(Base):
    """Individual protein design result."""
    __tablename__ = "designs"
    
    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    pdb_path = Column(String(500), nullable=False)
    json_path = Column(String(500), nullable=True)
    lineage_root_job_id = Column(String(36), nullable=True, index=True)
    parent_design_id = Column(String(36), nullable=True, index=True)
    origin_design_id = Column(String(36), nullable=True, index=True)
    origin_job_id = Column(String(36), nullable=True, index=True)
    origin_backbone_design_id = Column(String(36), nullable=True, index=True)
    stage_family = Column(String(64), nullable=True, index=True)
    stage_mode = Column(String(64), nullable=True, index=True)
    source_stage_job_id = Column(String(36), nullable=True, index=True)
    source_stage_family = Column(String(64), nullable=True, index=True)
    source_stage_mode = Column(String(64), nullable=True, index=True)
    source_pdb_path = Column(String(500), nullable=True)
    source_design_name = Column(String(255), nullable=True)
    artifact_class = Column(String(64), nullable=True, index=True)
    artifact_schema_version = Column(Integer, nullable=True)

    # Authoritative Data Review identity and typed artifact/role envelope.
    # Producer-declared values win; legacy rows are explicitly marked as
    # ingestion/backfill rather than silently reinterpreted by the frontend.
    review_profile_id = Column(String(64), nullable=True, index=True)
    review_contract_version = Column(Integer, nullable=True)
    review_contract_source = Column(String(32), nullable=True)
    review_artifact_manifest = Column(ScientificArtifactJSON, nullable=True)
    review_role_map = Column(ScientificArtifactJSON, nullable=True)
    selected_loop_scope = Column(JSON, nullable=True)
    provenance = Column(ScientificArtifactJSON, nullable=True)
    
    # Structural metrics (predicted structures)
    num_helices = Column(Integer, nullable=True)
    num_strands = Column(Integer, nullable=True)
    rog = Column(Float, nullable=True)  # Radius of gyration (predicted)

    # RFdiffusion backbone metrics
    rfd_rog = Column(Float, nullable=True)  # Radius of gyration (backbone)
    
    # Sequence design metrics
    mpnn_score = Column(Float, nullable=True)
    fampnn_psce = Column(Float, nullable=True)
    
    # Structure prediction metrics
    plddt_overall = Column(Float, nullable=True)
    plddt_binder = Column(Float, nullable=True)
    plddt_target = Column(Float, nullable=True)
    pae_interaction = Column(Float, nullable=True)
    pae_overall = Column(Float, nullable=True)
    rmsd_overall = Column(Float, nullable=True)
    rmsd_binder = Column(Float, nullable=True)
    rmsd_target = Column(Float, nullable=True)
    
    # Boltz-2 specific
    conf_score = Column(Float, nullable=True)
    ptm = Column(Float, nullable=True)
    ligand_iptm = Column(Float, nullable=True)
    
    # Interface metrics (critical for complexes)
    iptm = Column(Float, nullable=True)  # Interface pTM score
    protein_iptm = Column(Float, nullable=True)  # Protein-protein interface
    complex_iplddt = Column(Float, nullable=True)  # Interface pLDDT
    complex_ipde = Column(Float, nullable=True)  # Interface PDE
    chains_ptm = Column(ScientificArtifactJSON, nullable=True)  # {"0": 0.76, "1": 0.51} per-chain pTM
    pair_chains_iptm = Column(ScientificArtifactJSON, nullable=True)  # NxN chain matrix for heatmap
    disorder = Column(Float, nullable=True)  # Protenix disorder score/probability
    num_recycles = Column(Integer, nullable=True)  # Recycling iterations reported by model
    has_clash = Column(Boolean, nullable=True)  # Steric clash flag from confidence output
    confidence_metrics = Column(ScientificArtifactJSON, nullable=True)  # Raw model confidence JSON payload
    aligned_error_path = Column(String(500), nullable=True)
    aligned_error_format = Column(String(64), nullable=True)
    aligned_error_key = Column(String(128), nullable=True)
    ipsae = Column(Float, nullable=True)
    ipsae_binder_to_target = Column(Float, nullable=True)
    ipsae_target_to_binder = Column(Float, nullable=True)
    ipsae_d0chn = Column(Float, nullable=True)
    ipsae_d0dom = Column(Float, nullable=True)
    ipsae_chain_pair = Column(String(64), nullable=True)
    ipsae_pae_cutoff = Column(Float, nullable=True)
    ipsae_dist_cutoff = Column(Float, nullable=True)
    
    # Binding Affinity (Boltz-2)
    affinity_score = Column(Float, nullable=True)  # log(IC50)
    binder_probability = Column(Float, nullable=True)  # 0-1
    
    # Per-residue metrics (stored as JSON arrays)
    # Analytics
    chain_metrics = Column(ScientificArtifactJSON, nullable=True)  # {"A": {"type": "protein", ...}}
    residue_plddt = Column(ScientificArtifactJSON, nullable=True)  # [85.2, 91.3, ...] per residue
    pae_matrix = Column(JSON, nullable=True)     # [[0.2, ...], ...]
    
    # User annotations
    is_favorite = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # BACKBONE GROUPING & EPITOPE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    backbone_id = Column(Integer, nullable=True, index=True)  # Job number from design name (antibody_job_X)
    epitope_contact_count = Column(Integer, nullable=True)    # CDR residues within 8Å of epitope
    epitope_min_distance = Column(Float, nullable=True)       # Closest CDR-epitope distance (Å)
    epitope_min_atom_distance = Column(Float, nullable=True)  # Closest atom-atom antibody-epitope distance (Å)
    epitope_nearest_antibody_residue = Column(String(64), nullable=True)
    epitope_nearest_target_residue = Column(String(64), nullable=True)
    epitope_nearest_antibody_atom = Column(String(64), nullable=True)
    epitope_nearest_target_atom = Column(String(64), nullable=True)
    epitope_mapping_mode = Column(String(64), nullable=True)
    epitope_centroid_distance = Column(Float, nullable=True)
    target_contact_count = Column(Integer, nullable=True)     # Total target-contact count from RFA screening
    target_min_distance = Column(Float, nullable=True)        # Closest CDR-to-any-target-residue CA distance (Å)
    target_min_atom_distance = Column(Float, nullable=True)   # Closest atom-atom antibody-target distance (Å)
    target_nearest_antibody_residue = Column(String(64), nullable=True)
    target_nearest_target_residue = Column(String(64), nullable=True)
    target_nearest_antibody_atom = Column(String(64), nullable=True)
    target_nearest_target_atom = Column(String(64), nullable=True)
    target_centroid_distance = Column(Float, nullable=True)
    detected_antibody_chains = Column(String(64), nullable=True)
    detected_target_chain = Column(String(16), nullable=True)
    antibody_residue_count = Column(Integer, nullable=True)
    target_residue_count = Column(Integer, nullable=True)
    epitope_residue_count = Column(Integer, nullable=True)
    passed_screen = Column(Boolean, nullable=True)
    screening_reason = Column(String(255), nullable=True)     # RFantibody screening pass/fail summary
    source_stage = Column(String(64), nullable=True, index=True)   # review-stage rows (e.g. post_rfantibody)
    artifact_group = Column(String(64), nullable=True)             # candidate/raw/filtered/final
    rfa_loop_metrics = Column(ScientificArtifactJSON, nullable=True)
    rfa_hotspot_metrics = Column(ScientificArtifactJSON, nullable=True)
    rfa_hotspot_covered_count = Column(Integer, nullable=True)
    rfa_hotspot_min_distance = Column(Float, nullable=True)
    rfa_hotspot_avg_min_distance = Column(Float, nullable=True)
    rfa_runtime_seconds = Column(Float, nullable=True)
    rfa_device = Column(String(128), nullable=True)
    rfa_diffusion_steps = Column(Integer, nullable=True)
    rfa_noise_scale_ca = Column(Float, nullable=True)
    rfa_noise_scale_frame = Column(Float, nullable=True)
    rfa_guide_scale = Column(Float, nullable=True)
    rfa_plddt_initial = Column(Float, nullable=True)
    rfa_plddt_final = Column(Float, nullable=True)
    rfa_plddt_delta = Column(Float, nullable=True)
    rfa_plddt_selected = Column(Float, nullable=True)
    rfa_plddt_nonselected = Column(Float, nullable=True)
    rfa_design_loops = Column(ScientificArtifactJSON, nullable=True)
    rfa_hotspots = Column(ScientificArtifactJSON, nullable=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # ANTIBODY / DISCOVERY METRICS
    # ═══════════════════════════════════════════════════════════════════════════
    # CDR Sequences (IMGT numbering by default)
    cdr_h1 = Column(String(100), nullable=True)
    cdr_h2 = Column(String(100), nullable=True)
    cdr_h3 = Column(String(100), nullable=True)
    cdr_l1 = Column(String(100), nullable=True)
    cdr_l2 = Column(String(100), nullable=True)
    cdr_l3 = Column(String(100), nullable=True)
    numbering_scheme = Column(String(20), default="imgt")
    
    # CDR Loop Lengths (for sorting/filtering)
    binder_length = Column(Integer, nullable=True)  # Total AA count of designed binder
    cdr_h1_length = Column(Integer, nullable=True)
    cdr_h2_length = Column(Integer, nullable=True)
    cdr_h3_length = Column(Integer, nullable=True)
    cdr_l1_length = Column(Integer, nullable=True)  # NULL for VHH/nanobodies
    cdr_l2_length = Column(Integer, nullable=True)
    cdr_l3_length = Column(Integer, nullable=True)
    antibody_type = Column(String(20), nullable=True)  # vhh, fab, scfv, or NULL
    
    # Framework Contact Hotspots (IMGT positions, Zavrtanik et al. 2018)
    # These FR positions mediate antigen contacts in nanobodies
    fr2_contacts = Column(String(20), nullable=True)   # IMGT 37, 42, 44, 45, 47
    de_loop = Column(String(10), nullable=True)        # IMGT 72-75
    fr3_contacts = Column(String(15), nullable=True)   # IMGT 82-87
    fr4_contacts = Column(String(10), nullable=True)   # IMGT 101-103
    
    # Antibody Properties
    humanness_score = Column(Float, nullable=True)  # OAS/ANARCII derived
    developability_flags = Column(JSON, nullable=True)  # TAP-like warnings
    
    # Stability / Inverse Folding Data
    stability_data = Column(JSON, nullable=True)  # ThermoMPNN ddG matrix
    antifold_logits_path = Column(String(500), nullable=True)  # Path to probabilities.csv for heatmap
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FRUSTRATION ANALYSIS (FrustraMPNN)
    # ═══════════════════════════════════════════════════════════════════════════
    # Canonical summary projection used by shared Design analytics. Numerical
    # thresholds and classifications remain owned by the persisted backend
    # threshold policy; these columns do not reclassify scores.
    frustration_high_count = Column(Integer, nullable=True)
    frustration_min_count = Column(Integer, nullable=True)
    frustration_pct_high = Column(Float, nullable=True)        # 0..100 percentage
    frustration_residues = Column(ScientificArtifactJSON, nullable=True)         # Historical read-only per-residue projection
    frustration_csv_path = Column(String(500), nullable=True)  # Historical read-only CSV path
    # Canonical manifest-first FrustraMPNN authority fields. The three scalar
    # columns above are deterministic summary projections; these retain the
    # immutable contract/artifact lineage needed to interpret them.
    frustrampnn_contract_version = Column(String(32), nullable=True)
    frustrampnn_status = Column(String(32), nullable=True)
    frustrampnn_source_sha256 = Column(String(64), nullable=True)
    frustrampnn_manifest_relpath = Column(String(1000), nullable=True)
    frustrampnn_landscape_relpath = Column(String(1000), nullable=True)
    frustrampnn_summary_relpath = Column(String(1000), nullable=True)
    frustrampnn_runtime_sha256 = Column(String(64), nullable=True)
    frustrampnn_failure_class = Column(String(64), nullable=True)
    frustrampnn_failure_detail = Column(String(1000), nullable=True)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PPIFLOW MATURATION METRICS
    # ═══════════════════════════════════════════════════════════════════════════
    maturation_delta_interface = Column(Float, nullable=True)   # delta_interface_score (REU, more negative = better)
    maturation_interface_score = Column(Float, nullable=True)   # interface_score_matured (REU)
    maturation_rmsd = Column(Float, nullable=True)              # rmsd_backbone (Å, matured vs original)
    maturation_selected_delta_interface = Column(Float, nullable=True)   # selected_delta_interface_score (REU)
    maturation_selected_interface_score = Column(Float, nullable=True)   # selected_interface_score_matured (REU)
    maturation_selected_rmsd = Column(Float, nullable=True)              # selected_rmsd_backbone (Å)
    maturation_nonselected_rmsd = Column(Float, nullable=True)           # nonselected_rmsd_backbone (Å)
    ppiflow_primary_loop = Column(String(32), nullable=True)
    ppiflow_primary_loop_rmsd = Column(Float, nullable=True)
    ppiflow_primary_loop_target_contact_delta = Column(Integer, nullable=True)
    ppiflow_primary_loop_target_distance_delta = Column(Float, nullable=True)
    ppiflow_primary_loop_epitope_contact_delta = Column(Integer, nullable=True)
    ppiflow_primary_loop_epitope_distance_delta = Column(Float, nullable=True)
    ppiflow_objective_mode = Column(String(64), nullable=True)
    ppiflow_objective_score = Column(Float, nullable=True)
    ppiflow_filter_passed = Column(Boolean, nullable=True)
    ppiflow_filter_reason = Column(String(255), nullable=True)
    ppiflow_loop_metrics = Column(ScientificArtifactJSON, nullable=True)

    # Metric provenance/completeness: explicit source/formula/direction for model and BMS-derived scores.
    metric_provenance = Column(ScientificArtifactJSON, nullable=True)
    metric_completeness = Column(ScientificArtifactJSON, nullable=True)
        
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to job
    job = relationship("Job", back_populates="designs")


@event.listens_for(Design, "before_insert")
def _finalize_design_review_contract_before_insert(_mapper, _connection, target):
    from services.result_contracts import apply_review_contract_to_design

    apply_review_contract_to_design(target)


@event.listens_for(Design, "before_update")
def _refresh_inferred_design_review_contract_before_update(_mapper, _connection, target):
    from services.result_contracts import apply_review_contract_to_design

    if getattr(target, "review_contract_source", None) != "producer":
        target.review_artifact_manifest = None
    apply_review_contract_to_design(target)


class AnalysisRun(Base):
    """Persisted on-demand analysis run for a design or job subject."""
    __tablename__ = "analysis_runs"

    id = Column(String(36), primary_key=True)
    subject_kind = Column(String(32), nullable=False, index=True)
    subject_id = Column(String(64), nullable=False, index=True)
    analysis_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    resource_class = Column(String(32), nullable=False, default="cpu_heavy", index=True)

    params_json = Column(JSON, nullable=False, default=dict)
    params_hash = Column(String(64), nullable=False, index=True)
    input_signature = Column(String(128), nullable=False)
    code_version = Column(String(64), nullable=False)
    cache_key = Column(String(128), nullable=False, index=True)

    summary_json = Column(JSON, nullable=True)
    result_inline_json = Column(JSON, nullable=True)
    artifact_manifest = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    requested_by = Column(String(64), nullable=True)
    reuse_count = Column(Integer, default=0)
    supersedes_run_id = Column(String(36), nullable=True, index=True)

    queued_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_accessed_at = Column(DateTime, nullable=True)


class ScientificArtifactReceipt(Base):
    """Receipt for one immutable Parquet artifact in the shared data plane."""

    __tablename__ = "scientific_artifact_receipts"
    __table_args__ = (
        UniqueConstraint(
            "storage_root", "relative_path", name="uq_scientific_artifact_path"
        ),
        UniqueConstraint(
            "owner_kind", "owner_id", "role", "content_sha256",
            name="uq_scientific_artifact_content",
        ),
        Index("ix_scientific_artifact_owner", "owner_kind", "owner_id", "role"),
        Index("ix_scientific_artifact_hash", "content_sha256"),
    )

    artifact_id = Column(String(128), primary_key=True)
    owner_kind = Column(String(96), nullable=False)
    owner_id = Column(String(255), nullable=False)
    role = Column(String(128), nullable=False)
    schema_id = Column(String(160), nullable=False)
    artifact_schema_version = Column(Integer, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    row_count = Column(Integer, nullable=False)
    column_schema_sha256 = Column(String(64), nullable=False)
    storage_root = Column(String(96), nullable=False)
    relative_path = Column(String(2000), nullable=False)
    media_type = Column(String(160), nullable=False)
    availability = Column(String(32), nullable=False, default="available", index=True)
    source_receipts_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)




_DESIGN_ARTIFACT_FIELDS = (
    "confidence_metrics", "residue_plddt", "rfa_loop_metrics", "rfa_hotspot_metrics",
    "provenance", "review_artifact_manifest", "review_role_map", "chain_metrics",
    "frustration_residues", "rfa_design_loops", "rfa_hotspots", "ppiflow_loop_metrics",
    "metric_provenance", "metric_completeness", "pair_chains_iptm", "chains_ptm",
)
_DESIGN_INLINE_LIMIT = 256 * 1024


@event.listens_for(Session, "before_flush")
def _externalize_large_design_payloads(session, _flush_context, _instances):
    import hashlib
    import pyarrow as pa
    from services.scientific_artifacts import artifact_row_reference, install_parquet_rows, artifact_root

    schema = pa.schema([
        ("row_index", pa.int64()), ("design_id", pa.string()),
        ("field_name", pa.string()), ("payload_json", pa.string()),
    ])
    for target in tuple(session.new) + tuple(session.dirty):
        if not isinstance(target, Design):
            continue
        for field_name in _DESIGN_ARTIFACT_FIELDS:
            value = getattr(target, field_name, None)
            if not isinstance(value, dict) or value.get("schema") in {
                "bms.scientific-artifact-reference.v1",
                "bms.scientific-artifact-row-reference.v1",
            }:
                continue
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)
            if len(encoded.encode("utf-8")) <= _DESIGN_INLINE_LIMIT:
                continue
            source_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            row = {"row_index": 0, "design_id": str(target.id), "field_name": field_name, "payload_json": encoded}
            artifact = install_parquet_rows(
                root=artifact_root(), owner_kind="design_field", owner_id=f"{target.id}:{field_name}",
                role="payload", schema_id="bms.design.field.v1", schema_version=1,
                source_sha256=source_sha, rows=[row], schema=schema,
            )
            session.add(ScientificArtifactReceipt(
                artifact_id=artifact.artifact_id, owner_kind=artifact.owner_kind, owner_id=artifact.owner_id,
                role=artifact.role, schema_id=artifact.schema_id, artifact_schema_version=artifact.schema_version,
                content_sha256=artifact.content_sha256, size_bytes=artifact.size_bytes, row_count=artifact.row_count,
                column_schema_sha256=artifact.column_schema_sha256, storage_root="scientific_artifacts",
                relative_path=artifact.relative_path, media_type=artifact.media_type, availability="available",
                source_receipts_json={"source_table": "designs", "source_column": field_name, "source_key": str(target.id)},
            ))
            setattr(target, field_name, artifact_row_reference(artifact.reference(), 0, value_field="payload_json"))

class ScientificPayloadMigration(Base):
    """Idempotent source-to-artifact equivalence ledger."""

    __tablename__ = "scientific_payload_migrations"
    __table_args__ = (
        UniqueConstraint(
            "source_store", "source_table", "source_column", "source_key", "source_sha256",
            name="uq_scientific_payload_migration_source",
        ),
        Index("ix_scientific_payload_migration_state", "state", "updated_at"),
        ForeignKeyConstraint(
            ["artifact_id"], ["scientific_artifact_receipts.artifact_id"],
        ),
    )

    migration_id = Column(String(160), primary_key=True)
    source_store = Column(String(96), nullable=False)
    source_table = Column(String(160), nullable=False)
    source_column = Column(String(160), nullable=False)
    source_key = Column(String(512), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    artifact_id = Column(String(128), nullable=True)
    artifact_sha256 = Column(String(64), nullable=True)
    equivalence_sha256 = Column(String(64), nullable=True)
    state = Column(String(32), nullable=False, default="planned", index=True)
    diagnostic = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class FrustraMPNNResult(Base):
    """Immutable manifest-backed authority for one FrustraMPNN invocation."""

    __tablename__ = "frustrampnn_results"
    __table_args__ = (
        UniqueConstraint(
            "parent_job_id",
            "invocation_id",
            name="uq_frustrampnn_results_job_invocation",
        ),
        Index(
            "ix_frustrampnn_results_job_candidate",
            "parent_job_id",
            "candidate_id",
        ),
    )

    parent_job_id = Column(
        String(36), ForeignKey("jobs.id"), primary_key=True, nullable=False, index=True
    )
    invocation_id = Column(String(128), primary_key=True)
    parent_workflow_id = Column(String(128), nullable=False)
    candidate_id = Column(String(128), nullable=False, index=True)
    design_id = Column(
        String(36), ForeignKey("designs.id"), nullable=True, index=True
    )
    requiredness = Column(String(16), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    source_artifact_id = Column(String(128), nullable=True)
    source_artifact_sha256 = Column(String(64), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    manifest_json = Column(JSON, nullable=False)
    summary_sha256 = Column(String(64), nullable=False)
    summary_json = Column(JSON, nullable=False)
    runtime_identity_json = Column(JSON, nullable=False)
    assigned_gpu_json = Column(JSON, nullable=False)
    terminal_result_json = Column(JSON, nullable=False)
    parent_metadata_json = Column(JSON, nullable=True)
    settings_sha256 = Column(String(64), nullable=True)
    effective_settings_sha256 = Column(String(64), nullable=True)
    effective_settings_json = Column(JSON, nullable=True)
    capability_inventory_sha256 = Column(String(64), nullable=True)
    statistics_sha256 = Column(String(64), nullable=True)
    statistics_json = Column(ScientificArtifactJSON, nullable=True)
    comparison_compatibility_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNReview(Base):
    """Durable operator interpretation bound to persisted FrustraMPNN results."""

    __tablename__ = "frustrampnn_reviews"
    __table_args__ = (
        Index("ix_frustrampnn_reviews_parent_job_id", "parent_job_id"),
        Index("ix_frustrampnn_reviews_owner_job", "created_by", "parent_job_id"),
        Index("ix_frustrampnn_reviews_created_at", "created_at"),
    )

    review_id = Column(String(36), primary_key=True)
    parent_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False)
    invocation_id = Column(String(128), nullable=False)
    landscape_sha256 = Column(String(64), nullable=False)
    effective_settings_sha256 = Column(String(64), nullable=False)
    review_sha256 = Column(String(64), nullable=False, unique=True)
    supersedes_review_id = Column(String(36), ForeignKey("frustrampnn_reviews.review_id"), nullable=True)
    created_by = Column(String(128), nullable=False)
    title = Column(String(160), nullable=False)
    notes = Column(Text, nullable=False, default="")
    result_references_json = Column(JSON, nullable=False)
    selected_residues_json = Column(JSON, nullable=False, default=list)
    filters_json = Column(JSON, nullable=False, default=dict)
    viewer_state_json = Column(JSON, nullable=False, default=dict)
    tags_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)



class FrustraMPNNExport(Base):
    """Persisted bounded export over authoritative FrustraMPNN rows."""

    __tablename__ = "frustrampnn_exports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_job_id", "invocation_id"],
            ["frustrampnn_results.parent_job_id", "frustrampnn_results.invocation_id"],
            name="fk_frustrampnn_exports_result",
        ),
        Index("ix_frustrampnn_exports_owner_job", "created_by", "parent_job_id"),
    )

    export_id = Column(String(36), primary_key=True)
    review_id = Column(String(36), ForeignKey("frustrampnn_reviews.review_id"), nullable=False)
    parent_job_id = Column(String(36), nullable=False)
    invocation_id = Column(String(128), nullable=False)
    created_by = Column(String(128), nullable=False)
    format = Column(String(8), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    row_count = Column(Integer, nullable=False)
    total_matching_rows = Column(Integer, nullable=False)
    complete = Column(Boolean, nullable=False)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNReviewArtifact(Base):
    """Verified capture bytes bound to one saved FrustraMPNN review."""

    __tablename__ = "frustrampnn_review_artifacts"
    __table_args__ = (
        Index("ix_frustrampnn_review_artifacts_owner_review", "created_by", "review_id"),
    )

    artifact_id = Column(String(36), primary_key=True)
    review_id = Column(String(36), ForeignKey("frustrampnn_reviews.review_id"), nullable=False)
    parent_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False)
    created_by = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    media_type = Column(String(64), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    payload_blob = Column(LargeBinary, nullable=False)
    generation_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNArtifact(Base):
    """One exact manifest-governed artifact for a FrustraMPNN result."""

    __tablename__ = "frustrampnn_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_job_id", "invocation_id"],
            ["frustrampnn_results.parent_job_id", "frustrampnn_results.invocation_id"],
            name="fk_frustrampnn_artifacts_result",
        ),
        UniqueConstraint(
            "parent_job_id",
            "invocation_id",
            "relative_path",
            name="uq_frustrampnn_artifacts_invocation_path",
        ),
    )

    artifact_id = Column(String(96), primary_key=True)
    parent_job_id = Column(String(36), nullable=False, index=True)
    invocation_id = Column(String(128), nullable=False, index=True)
    role = Column(String(64), nullable=False, index=True)
    relative_path = Column(String(1000), nullable=False)
    storage_path = Column(String(2000), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    media_type = Column(String(128), nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNLandscapeRow(Base):
    """Exact residue/substitution row from one canonical landscape artifact."""

    __tablename__ = "frustrampnn_landscape_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_job_id", "invocation_id"],
            ["frustrampnn_results.parent_job_id", "frustrampnn_results.invocation_id"],
            name="fk_frustrampnn_landscape_result",
        ),
        UniqueConstraint(
            "parent_job_id",
            "invocation_id",
            "target_id",
            "entity_instance_id",
            "auth_asym_id",
            "auth_seq_id",
            "insertion_code",
            "sequence_index",
            "wt",
            "mutation_aa",
            name="uq_frustrampnn_landscape_slot",
        ),
        Index(
            "ix_frustrampnn_landscape_page_order",
            "parent_job_id",
            "invocation_id",
            "target_id",
            "entity_instance_id",
            "auth_asym_id",
            "auth_seq_id",
            "insertion_code",
            "sequence_index",
            "mutation_aa",
            "id",
        ),
    )

    id = Column(String(96), primary_key=True)
    parent_job_id = Column(String(36), nullable=False, index=True)
    invocation_id = Column(String(128), nullable=False)
    target_id = Column(String(128), nullable=False)
    entity_instance_id = Column(String(128), nullable=False)
    auth_asym_id = Column(String(128), nullable=False)
    auth_seq_id = Column(String(64), nullable=False)
    insertion_code = Column(String(16), nullable=False, default="")
    sequence_index = Column(Integer, nullable=False)
    wt = Column(String(1), nullable=False)
    mutation_aa = Column(String(1), nullable=False)
    score = Column(Float, nullable=True)
    score_class = Column(String(32), nullable=False)
    scoreable = Column(Boolean, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    row_json = Column(ScientificArtifactJSON, nullable=False)
    provenance_json = Column(ScientificArtifactJSON, nullable=False)


class FrustraMPNNComparison(Base):
    """Immutable residue-aligned comparison derived from two persisted landscapes."""

    __tablename__ = "frustrampnn_comparisons"
    __table_args__ = (
        UniqueConstraint("comparison_sha256", name="uq_frustrampnn_comparison_sha256"),
        Index("ix_frustrampnn_comparison_sources", "reference_parent_job_id", "target_parent_job_id"),
    )

    comparison_id = Column(String(96), primary_key=True)
    reference_parent_job_id = Column(String(36), nullable=False, index=True)
    reference_invocation_id = Column(String(128), nullable=False)
    target_parent_job_id = Column(String(36), nullable=False, index=True)
    target_invocation_id = Column(String(128), nullable=False)
    reference_landscape_sha256 = Column(String(64), nullable=False, index=True)
    target_landscape_sha256 = Column(String(64), nullable=False, index=True)
    configuration_id = Column(String(128), nullable=True)
    configuration_sha256 = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, index=True)
    comparison_sha256 = Column(String(64), nullable=False)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNComparisonRow(Base):
    """Immutable row-level evidence for one comparison."""

    __tablename__ = "frustrampnn_comparison_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["comparison_id"], ["frustrampnn_comparisons.comparison_id"],
            name="fk_frustrampnn_comparison_rows_comparison",
        ),
        UniqueConstraint("comparison_id", "row_index", name="uq_frustrampnn_comparison_row_index"),
        Index("ix_frustrampnn_comparison_row_identity", "comparison_id", "auth_asym_id", "auth_seq_id", "mutation_aa"),
    )

    id = Column(String(96), primary_key=True)
    comparison_id = Column(String(96), nullable=False, index=True)
    row_index = Column(Integer, nullable=False)
    entity_instance_id = Column(String(128), nullable=False)
    auth_asym_id = Column(String(128), nullable=False)
    auth_seq_id = Column(String(64), nullable=False)
    insertion_code = Column(String(16), nullable=False, default="")
    sequence_index = Column(Integer, nullable=True)
    mutation_aa = Column(String(1), nullable=False)
    mapping_state = Column(String(32), nullable=False, index=True)
    missingness_state = Column(String(32), nullable=False, index=True)
    biological_status = Column(String(32), nullable=False, index=True)
    reference_score = Column(Float, nullable=True)
    target_score = Column(Float, nullable=True)
    raw_score_delta = Column(Float, nullable=True)
    reference_class = Column(String(32), nullable=True)
    target_class = Column(String(32), nullable=True)
    classification_transition = Column(String(64), nullable=True)
    row_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FrustraMPNNGuidancePlan(Base):
    """Immutable decision-support guidance derived from a landscape/comparison."""

    __tablename__ = "frustrampnn_guidance_plans"
    __table_args__ = (
        UniqueConstraint("guidance_sha256", name="uq_frustrampnn_guidance_sha256"),
        Index("ix_frustrampnn_guidance_source", "source_landscape_sha256"),
    )

    guidance_id = Column(String(96), primary_key=True)
    source_landscape_sha256 = Column(String(64), nullable=False, index=True)
    source_comparison_id = Column(String(96), nullable=True, index=True)
    source_parent_job_id = Column(String(36), nullable=True, index=True)
    source_invocation_id = Column(String(128), nullable=True)
    configuration_id = Column(String(128), nullable=True)
    configuration_sha256 = Column(String(64), nullable=True)
    guidance_sha256 = Column(String(64), nullable=False)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConformationalMappingRequest(Base):
    """Durable canonical request and lifecycle authority."""

    __tablename__ = "conformational_mapping_requests"

    request_id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False, unique=True, index=True)
    principal_id = Column(String(255), nullable=False, index=True)
    backend = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    request_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    coordinate_plan_sha256 = Column(String(64), nullable=False)
    resume_key = Column(String(64), nullable=False, index=True)
    result_contract_id = Column(String(64), nullable=False)
    request_json = Column(JSON, nullable=False)
    coordinate_plan_json = Column(JSON, nullable=False)
    progress_json = Column(JSON, nullable=False, default=dict)
    failure_receipt_json = Column(JSON, nullable=True)
    retry_of_request_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    terminal_at = Column(DateTime, nullable=True)


class ConformationalMappingSource(Base):
    """Server-owned source registry for snapshots, uploads and references."""

    __tablename__ = "conformational_mapping_sources"

    source_id = Column(String(80), primary_key=True)
    principal_id = Column(String(255), nullable=False, index=True)
    source_kind = Column(String(64), nullable=False, index=True)
    storage_root = Column(String(2000), nullable=False)
    relative_path = Column(String(1000), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    immutable = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConformationalMappingRecord(Base):
    """Content-addressed canonical records for every CM product plane."""

    __tablename__ = "conformational_mapping_records"
    __table_args__ = (
        UniqueConstraint("request_id", "record_type", "record_key", name="uq_cm_record_identity"),
    )

    id = Column(String(36), primary_key=True)
    request_id = Column(
        String(36),
        ForeignKey("conformational_mapping_requests.request_id"),
        nullable=False,
        index=True,
    )
    record_type = Column(String(64), nullable=False, index=True)
    record_key = Column(String(255), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    payload_json = Column(ScientificArtifactJSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConformationalMappingArtifact(Base):
    """Registered native/canonical file identity; paths are never public authority."""

    __tablename__ = "conformational_mapping_artifacts"
    __table_args__ = (
        UniqueConstraint("request_id", "relative_path", name="uq_cm_artifact_path"),
    )

    artifact_id = Column(String(96), primary_key=True)
    request_id = Column(
        String(36),
        ForeignKey("conformational_mapping_requests.request_id"),
        nullable=False,
        index=True,
    )
    candidate_id = Column(String(128), nullable=True, index=True)
    role = Column(String(64), nullable=False, index=True)
    relative_path = Column(String(1000), nullable=False)
    storage_path = Column(String(2000), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    media_type = Column(String(128), nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConformationalMappingLandscapeRow(Base):
    """Range/page-backed exact substitution row storage."""

    __tablename__ = "conformational_mapping_landscape_rows"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "candidate_id", "entity_instance_id", "auth_asym_id",
            "auth_seq_id", "insertion_code", "sequence_index", "mutation_aa",
            name="uq_cm_landscape_slot",
        ),
    )

    id = Column(String(36), primary_key=True)
    request_id = Column(
        String(36),
        ForeignKey("conformational_mapping_requests.request_id"),
        nullable=False,
        index=True,
    )
    candidate_id = Column(String(128), nullable=False, index=True)
    entity_instance_id = Column(String(128), nullable=False, index=True)
    auth_asym_id = Column(String(128), nullable=False)
    auth_seq_id = Column(String(64), nullable=False)
    insertion_code = Column(String(16), nullable=False, default="")
    sequence_index = Column(Integer, nullable=False)
    wt = Column(String(1), nullable=False)
    mutation_aa = Column(String(1), nullable=False)
    score = Column(Float, nullable=True)
    score_class = Column(String(32), nullable=True)
    scoreable = Column(Boolean, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    provenance_json = Column(ScientificArtifactJSON, nullable=False, default=dict)


class ConformationalMappingStateLandscapeAnalysisHeader(Base):
    """Immutable query projection header; canonical artifact bytes remain authority."""

    __tablename__ = "conformational_mapping_state_landscape_analysis_headers"

    request_id = Column(
        String(36),
        ForeignKey("conformational_mapping_requests.request_id"),
        primary_key=True,
    )
    analysis_id = Column(String(80), primary_key=True)
    content_sha256 = Column(String(64), nullable=False, index=True)
    source_ensemble_sha256 = Column(String(64), nullable=False)
    source_landscape_sha256 = Column(String(64), nullable=False)
    source_structure_map_sha256 = Column(String(64), nullable=False)
    comparison_sha256 = Column(String(64), nullable=False)
    formula_version = Column(String(80), nullable=False)
    formula_sha256 = Column(String(64), nullable=False)
    policy_sha256 = Column(String(64), nullable=False)
    comparison_mode = Column(String(32), nullable=False)
    comparison_target_id = Column(String(128), nullable=False)
    comparison_scope = Column(String(64), nullable=False)
    reference_backend_coordinates_json = Column(JSON, nullable=True)
    reference_candidate_id = Column(String(128), nullable=True)
    pair_count = Column(Integer, nullable=False)
    row_count = Column(Integer, nullable=False)
    exclusion_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConformationalMappingStateLandscapeAnalysisPair(Base):
    """Resolved candidate pair from one immutable state-analysis artifact."""

    __tablename__ = "conformational_mapping_state_landscape_analysis_pairs"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "analysis_id", "pair_id", "candidate_a_id", "candidate_b_id",
            name="uq_cm_state_analysis_pair_candidates",
        ),
        ForeignKeyConstraint(
            ("request_id", "analysis_id"),
            (
                "conformational_mapping_state_landscape_analysis_headers.request_id",
                "conformational_mapping_state_landscape_analysis_headers.analysis_id",
            ),
        ),
    )

    request_id = Column(String(36), primary_key=True)
    analysis_id = Column(String(80), primary_key=True)
    pair_id = Column(String(300), primary_key=True)
    candidate_a_id = Column(String(128), nullable=False)
    candidate_b_id = Column(String(128), nullable=False)


class ConformationalMappingStateLandscapeAnalysisRow(Base):
    """Exact artifact row payload and availability, never recomputed from source data."""

    __tablename__ = "conformational_mapping_state_landscape_analysis_rows"
    __table_args__ = (
        Index(
            "ix_cm_state_analysis_rows_page_order",
            "request_id", "analysis_id", "pair_id", "target_id", "entity_instance_id",
            "auth_asym_id", "auth_seq_id", "insertion_code", "sequence_index", "validated_wt", "id",
        ),
        UniqueConstraint(
            "request_id", "analysis_id", "pair_id", "entity_instance_id", "auth_asym_id",
            "auth_seq_id", "insertion_code", "sequence_index", "validated_wt",
            name="uq_cm_state_analysis_row_identity",
        ),
        ForeignKeyConstraint(
            ("request_id", "analysis_id"),
            (
                "conformational_mapping_state_landscape_analysis_headers.request_id",
                "conformational_mapping_state_landscape_analysis_headers.analysis_id",
            ),
        ),
        ForeignKeyConstraint(
            ("request_id", "analysis_id", "pair_id"),
            (
                "conformational_mapping_state_landscape_analysis_pairs.request_id",
                "conformational_mapping_state_landscape_analysis_pairs.analysis_id",
                "conformational_mapping_state_landscape_analysis_pairs.pair_id",
            ),
        ),
        ForeignKeyConstraint(
            ("request_id", "analysis_id", "pair_id", "candidate_a_id", "candidate_b_id"),
            (
                "conformational_mapping_state_landscape_analysis_pairs.request_id",
                "conformational_mapping_state_landscape_analysis_pairs.analysis_id",
                "conformational_mapping_state_landscape_analysis_pairs.pair_id",
                "conformational_mapping_state_landscape_analysis_pairs.candidate_a_id",
                "conformational_mapping_state_landscape_analysis_pairs.candidate_b_id",
            ),
        ),
    )

    id = Column(String(96), primary_key=True)
    request_id = Column(String(36), nullable=False, index=True)
    analysis_id = Column(String(80), nullable=False, index=True)
    pair_id = Column(String(300), nullable=False, index=True)
    candidate_a_id = Column(String(128), nullable=False)
    candidate_b_id = Column(String(128), nullable=False)
    target_id = Column(String(128), nullable=False)
    entity_instance_id = Column(String(128), nullable=False)
    auth_asym_id = Column(String(128), nullable=False)
    auth_seq_id = Column(Integer, nullable=False)
    insertion_code = Column(String(16), nullable=False)
    sequence_index = Column(Integer, nullable=False)
    validated_wt = Column(String(1), nullable=False)
    metrics_json = Column(JSON, nullable=False)
    availability_json = Column(JSON, nullable=False)


class InputFile(Base):
    """Tracked input file (PDB, FASTA, etc.)."""
    __tablename__ = "input_files"
    
    id = Column(String(36), primary_key=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)  # pdb, fasta, yaml, pt
    directory = Column(String(500), nullable=False)
    size_bytes = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class UserSequence(Base):
    """User-defined amino acid sequence."""
    __tablename__ = "user_sequences"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    sequence = Column(Text, nullable=False)
    description = Column(String(500), nullable=True)
    length = Column(Integer, nullable=False)
    organism = Column(String(255), nullable=True)  # Optional organism info
    uniprot_id = Column(String(50), nullable=True)  # Optional UniProt reference
    ncbi_id = Column(String(50), nullable=True)  # Optional NCBI reference (gene ID or accession)
    is_preset = Column(Boolean, default=False)  # True if migrated from YAML presets
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)


class UserTemplate(Base):
    """User-defined run template (job configuration snapshot)."""
    __tablename__ = "user_templates"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    icon = Column(String(50), default="bookmark")
    color = Column(String(20), default="#6B7280")
    base_template_id = Column(String(100), nullable=True)  # Original system template ID if cloned
    model_id = Column(String(50), nullable=True)  # Associated model (rfdiffusion, boltz2, etc.)
    mode = Column(String(100), nullable=True)  # Workflow mode (binder_denovo, predict, etc.)
    params = Column(JSON, nullable=False)  # Full parameter snapshot
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)


class NucleotideSequence(Base):
    """Nucleotide sequence for BioDesigner (DNA/RNA with features)."""
    __tablename__ = "nucleotide_sequences"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Sequence data
    sequence = Column(Text, nullable=False)  # Raw nucleotide sequence (ATCG/AUCG)
    sequence_type = Column(String(10), nullable=False, default="dna")  # dna, rna
    molecule_strandedness = Column(String(16), nullable=False, default="unknown")  # single, double, unknown
    molecule_orientation = Column(String(24), nullable=False, default="unknown")  # positive, negative, ambisense, not_applicable, unknown
    is_circular = Column(Boolean, default=False)  # Circular (plasmid) or linear
    length = Column(Integer, nullable=False)
    
    # Features/annotations stored as JSON array
    # Format: [{"id": "f1", "name": "AmpR", "type": "CDS", "start": 0, "end": 100, "strand": 1, "color": "#F00", "notes": {...}}]
    features = Column(JSON, nullable=True)
    
    # Primers associated with this sequence
    # Format: [{"id": "p1", "name": "Fwd", "sequence": "ATCG...", "start": 0, "end": 20, "tm": 58.5}]
    primers = Column(JSON, nullable=True)

    # Analysis/evidence tracks aligned to the nucleotide sequence
    # Format: [{"id": "t1", "name": "SHAPE", "kind": "reactivity", "values": [0.1, 0.2, ...]}]
    analysis_tracks = Column(JSON, nullable=True)
    
    # Metadata
    organism = Column(String(255), nullable=True)
    accession = Column(String(100), nullable=True)  # GenBank accession
    source_file = Column(String(255), nullable=True)  # Original filename if imported

    # Provenance
    parent_id = Column(String(36), nullable=True)
    operation = Column(String(50), nullable=True)  # digest, pcr, ligate, mutagenesis, gibson, goldengate
    operation_params = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    
    # GC content cached
    gc_content = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)


class Primer(Base):
    """Primer library entry for MolBio Toolkit."""
    __tablename__ = "primers"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    sequence = Column(String(500), nullable=False)  # 5' to 3' sequence
    sequence_type = Column(String(10), nullable=False, default="dna")  # dna, rna
    
    # Calculated properties (cached for filtering)
    length = Column(Integer, nullable=False)
    tm = Column(Float, nullable=True)  # Melting temperature (°C)
    gc_percent = Column(Float, nullable=True)  # GC content percentage
    tm_algorithm = Column(String(100), nullable=True)
    tm_salt_correction = Column(String(100), nullable=True)
    tm_settings = Column(JSON, nullable=True)
    
    # Primer type and usage
    primer_type = Column(String(50), default="general")  # general, forward, reverse, sequencing, quantitative PCR
    description = Column(Text, nullable=True)
    
    # Target binding info (optional)
    target_sequence_id = Column(String(36), ForeignKey("nucleotide_sequences.id"), nullable=True)
    binding_start = Column(Integer, nullable=True)  # 0-indexed binding position
    binding_end = Column(Integer, nullable=True)
    binding_strand = Column(Integer, default=1)  # 1 = forward, -1 = reverse
    
    # Tags for organization
    tags = Column(JSON, nullable=True)  # ["cloning", "mutagenesis", "sequencing"]
    
    # Metadata
    is_favorite = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    
    # Relationship
    target_sequence = relationship("NucleotideSequence", foreign_keys=[target_sequence_id])


class MdRun(Base):
    """Authoritative MD parent state; generic Job remains the scheduler projection."""
    __tablename__ = "md_runs"
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    normalized_request = Column(JSON, nullable=False)
    request_sha256 = Column(String(64), nullable=False, index=True)
    phase = Column(String(32), nullable=False, default="validating", index=True)
    state_version = Column(Integer, nullable=False, default=0)
    chemistry_profile_id = Column(String(128), nullable=False, index=True)
    chemistry_profile_sha256 = Column(String(64), nullable=False)
    chemistry_assurance = Column(String(32), nullable=False)
    verification_status = Column(String(32), nullable=False, default="not_run")
    controls_blocked = Column(Boolean, nullable=False, default=False)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MdReplicaRun(Base):
    __tablename__ = "md_replica_runs"
    __table_args__ = (
        UniqueConstraint("md_job_id", "replica_index", "attempt", name="uq_md_replica_attempt"),
        Index("uq_md_replica_active", "md_job_id", "replica_index", unique=True, sqlite_where=text("active = 1")),
    )
    id = Column(String(36), primary_key=True)
    child_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, unique=True)
    md_job_id = Column(String(36), ForeignKey("md_runs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    replica_index = Column(Integer, nullable=False)
    attempt = Column(Integer, nullable=False)
    state = Column(String(32), nullable=False, default="queued", index=True)
    active = Column(Boolean, nullable=False, default=True)
    engine = Column(String(32), nullable=False)
    failure = Column(JSON, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class MdAttemptSegment(Base):
    __tablename__ = "md_attempt_segments"
    __table_args__ = (UniqueConstraint("replica_run_id", "segment_index", name="uq_md_attempt_segment"),)
    id = Column(String(36), primary_key=True)
    replica_run_id = Column(String(36), ForeignKey("md_replica_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_index = Column(Integer, nullable=False)
    state = Column(String(32), nullable=False, default="queued", index=True)
    source_segment_id = Column(String(36), ForeignKey("md_attempt_segments.id"), nullable=True)
    source_checkpoint_id = Column(String(36), ForeignKey("md_checkpoints.id"), nullable=True)
    execution_plan_sha256 = Column(String(64), nullable=False)
    compatibility_key = Column(String(64), nullable=False, index=True)
    launch_identity = Column(JSON, nullable=True)
    reservation_token = Column(String(128), nullable=True, unique=True)
    start_step = Column(Integer, nullable=True)
    end_step = Column(Integer, nullable=True)
    start_time_ps = Column(Float, nullable=True)
    end_time_ps = Column(Float, nullable=True)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(LenientSQLiteDateTime, nullable=True)


class MdCheckpoint(Base):
    __tablename__ = "md_checkpoints"
    __table_args__ = (UniqueConstraint("segment_id", "logical_role", "relative_path", name="uq_md_checkpoint_path"),)
    id = Column(String(36), primary_key=True)
    segment_id = Column(String(36), ForeignKey("md_attempt_segments.id", ondelete="CASCADE"), nullable=False, index=True)
    logical_role = Column(String(32), nullable=False)
    relative_path = Column(String(1000), nullable=False)
    sha256 = Column(String(64), nullable=False)
    bytes = Column(Integer, nullable=False)
    step = Column(Integer, nullable=False)
    time_ps = Column(Float, nullable=False)
    compatibility_key = Column(String(64), nullable=False, index=True)
    accepted = Column(Boolean, nullable=False, default=False)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (UniqueConstraint("owner_job_id", "attempt", "logical_path", name="uq_job_artifact_logical"),)
    id = Column(String(36), primary_key=True)
    owner_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=0)
    logical_path = Column(String(1000), nullable=False)
    storage_path = Column(String(1000), nullable=False)
    sha256 = Column(String(64), nullable=False)
    bytes = Column(Integer, nullable=False)
    media_type = Column(String(128), nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class RFD3LocalRedesignRequest(Base):
    """Immutable canonical request and lifecycle projection for local redesign."""

    __tablename__ = "rfd3_local_redesign_requests"

    request_id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    request_sha256 = Column(String(64), nullable=False, index=True)
    profile_id = Column(String(128), nullable=False, index=True)
    profile_registry_sha256 = Column(String(64), nullable=False, index=True)
    redesign_mode = Column(String(64), nullable=False, index=True)
    sequence_policy = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    request_json = Column(JSON, nullable=False)
    preparation_receipt_json = Column(JSON, nullable=True)
    runtime_identity_json = Column(JSON, nullable=True)
    result_manifest_sha256 = Column(String(64), nullable=True, index=True)
    failure_receipt_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    terminal_at = Column(DateTime, nullable=True)


class RFD3LocalRedesignCandidate(Base):
    """Typed candidate projection backed by one native RFD3 result manifest."""

    __tablename__ = "rfd3_local_redesign_candidates"
    __table_args__ = (
        UniqueConstraint("request_id", "candidate_id", name="uq_rfd3_local_redesign_candidate"),
        Index("ix_rfd3_local_redesign_candidate_status", "request_id", "status"),
    )

    id = Column(String(96), primary_key=True)
    request_id = Column(
        String(36), ForeignKey("rfd3_local_redesign_requests.request_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    candidate_id = Column(String(128), nullable=False)
    result_set = Column(String(128), nullable=False, default="rfd3_local_redesign_candidates")
    stage = Column(String(64), nullable=False, default="backbone")
    status = Column(String(32), nullable=False, index=True)
    artifact_manifest_sha256 = Column(String(64), nullable=False, index=True)
    metrics_json = Column(JSON, nullable=False, default=dict)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RFD3LocalRedesignArtifact(Base):
    """One exact native or derived artifact for a local-redesign candidate."""

    __tablename__ = "rfd3_local_redesign_artifacts"
    __table_args__ = (
        UniqueConstraint("request_id", "relative_path", name="uq_rfd3_local_redesign_artifact_path"),
        Index("ix_rfd3_local_redesign_artifact_role", "request_id", "role"),
    )

    artifact_id = Column(String(96), primary_key=True)
    request_id = Column(
        String(36), ForeignKey("rfd3_local_redesign_requests.request_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    candidate_id = Column(String(128), nullable=True, index=True)
    role = Column(String(96), nullable=False, index=True)
    relative_path = Column(String(1000), nullable=False)
    storage_path = Column(String(2000), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    media_type = Column(String(128), nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MdEvent(Base):
    __tablename__ = "md_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_md_event_idempotency"),)
    id = Column(String(36), primary_key=True)
    md_job_id = Column(String(36), ForeignKey("md_runs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False, index=True)
    expected_state_version = Column(Integer, nullable=False)
    resulting_state_version = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


class MdReconcilerLease(Base):
    __tablename__ = "md_reconciler_leases"
    name = Column(String(64), primary_key=True)
    owner_id = Column(String(128), nullable=False)
    expires_at = Column(LenientSQLiteDateTime, nullable=False)
    updated_at = Column(LenientSQLiteDateTime, nullable=False, default=datetime.utcnow)


# MSACache removed - now using file-based caching (see BMS_MSA_CACHE).


async def init_db():
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_schema(conn)


async def _ensure_schema(conn):
    """Add missing columns for legacy SQLite databases."""
    if engine.dialect.name != "sqlite":
        return
    
    await _ensure_table_columns(conn, "jobs", Job.__table__.columns)
    await _ensure_table_columns(conn, "designs", Design.__table__.columns)
    await _ensure_table_columns(conn, "analysis_runs", AnalysisRun.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_requests", ConformationalMappingRequest.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_sources", ConformationalMappingSource.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_records", ConformationalMappingRecord.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_artifacts", ConformationalMappingArtifact.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_landscape_rows", ConformationalMappingLandscapeRow.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_state_landscape_analysis_headers", ConformationalMappingStateLandscapeAnalysisHeader.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_state_landscape_analysis_pairs", ConformationalMappingStateLandscapeAnalysisPair.__table__.columns)
    await _ensure_table_columns(conn, "conformational_mapping_state_landscape_analysis_rows", ConformationalMappingStateLandscapeAnalysisRow.__table__.columns)
    await _ensure_table_columns(conn, "rfd3_local_redesign_requests", RFD3LocalRedesignRequest.__table__.columns)
    await _ensure_table_columns(conn, "rfd3_local_redesign_candidates", RFD3LocalRedesignCandidate.__table__.columns)
    await _ensure_table_columns(conn, "rfd3_local_redesign_artifacts", RFD3LocalRedesignArtifact.__table__.columns)
    await _ensure_table_columns(conn, "nucleotide_sequences", NucleotideSequence.__table__.columns)
    await _ensure_table_columns(conn, "primers", Primer.__table__.columns)
    await _backfill_frustrampnn_summary_projections(conn)
    await _backfill_design_review_contracts(conn)
    await _ensure_sqlite_indexes(conn)
    await _ensure_ngs_reference_set_immutability(conn)


async def _backfill_frustrampnn_summary_projections(conn):
    """Repair shared Design analytics from validated immutable summaries."""
    from services.frustrampnn.contracts import project_summary_artifact

    result = await conn.execute(text(
        "SELECT d.id AS design_id, r.invocation_id, r.summary_json "
        "FROM designs d JOIN frustrampnn_results r ON r.design_id = d.id "
        "WHERE d.frustrampnn_status = 'succeeded' AND ("
        "d.frustration_high_count IS NULL OR d.frustration_min_count IS NULL "
        "OR d.frustration_pct_high IS NULL) "
        "ORDER BY d.id, r.created_at DESC, r.invocation_id DESC"
    ))
    projected: set[str] = set()
    for row in result.mappings().all():
        design_id = str(row["design_id"])
        if design_id in projected:
            continue
        summary = row["summary_json"]
        if isinstance(summary, str):
            summary = json.loads(summary)
        summary = project_summary_artifact(summary)
        await conn.execute(
            text(
                "UPDATE designs SET frustration_high_count = :high_count, "
                "frustration_min_count = :minimal_count, "
                "frustration_pct_high = :high_percent WHERE id = :design_id"
            ),
            {
                "design_id": design_id,
                "high_count": int(summary["native_slot_counts"]["high"]),
                "minimal_count": int(summary["native_slot_counts"]["minimal"]),
                "high_percent": float(summary["native_slot_fractions"]["high"]) * 100.0,
            },
        )
        projected.add(design_id)


async def _backfill_design_review_contracts(conn):
    """Persist deterministic compatibility profiles; ambiguous rows fail closed."""
    from services.result_contracts import (
        REVIEW_CONTRACT_VERSION,
        build_review_artifact_manifest,
        resolve_result_contract,
    )

    result = await conn.execute(text(
        "SELECT d.id, d.stage_family, d.stage_mode, d.artifact_class, d.pdb_path, "
        "d.aligned_error_path, d.aligned_error_format, "
        "d.review_profile_id, d.review_contract_version, d.review_contract_source, "
        "d.review_artifact_manifest, d.review_role_map, "
        "j.model_id AS job_model_id, j.mode AS job_mode, "
        "j.stage_family AS job_stage_family, j.stage_mode AS job_stage_mode "
        "FROM designs d LEFT JOIN jobs j ON j.id = d.job_id "
        "WHERE d.review_profile_id IS NULL "
        "OR d.review_profile_id = 'unsupported_legacy' "
        "OR d.review_artifact_manifest IS NULL"
    ))
    for row in result.mappings().all():
        values = dict(row)
        stage_family = values.get("stage_family") or values.get("job_stage_family")
        stage_mode = values.get("stage_mode") or values.get("job_stage_mode") or values.get("job_mode")
        persisted_profile = values.get("review_profile_id")
        stale_unsupported = persisted_profile in (None, "", "unsupported_legacy")
        contract = resolve_result_contract(
            review_profile_id=None if stale_unsupported else persisted_profile,
            model_type=values.get("job_model_id") or stage_family,
            stage_family=stage_family,
            stage_mode=stage_mode,
            artifact_class=values.get("artifact_class"),
            provenance={"model_id": values.get("job_model_id")},
        )
        profile_id = (
            contract.analysis_contract_id or "unsupported_legacy"
            if stale_unsupported
            else persisted_profile
        )
        role_map = values.get("review_role_map")
        if isinstance(role_map, str):
            try:
                role_map = json.loads(role_map)
            except json.JSONDecodeError:
                role_map = None
        design_values = {
            **values,
            "review_profile_id": profile_id,
            "review_role_map": role_map if isinstance(role_map, dict) else {},
            "pae_matrix": None,
        }
        design = SimpleNamespace(**design_values)
        manifest = build_review_artifact_manifest(design)
        await conn.execute(
            text(
                "UPDATE designs SET review_profile_id = :profile_id, "
                "review_contract_version = :contract_version, "
                "review_contract_source = CASE "
                "WHEN review_contract_source IS NULL OR review_contract_source = 'unsupported_legacy' "
                "THEN :contract_source ELSE review_contract_source END, "
                "review_artifact_manifest = :artifact_manifest, "
                "review_role_map = COALESCE(review_role_map, :role_map) WHERE id = :design_id"
            ),
            {
                "profile_id": profile_id,
                "contract_version": values.get("review_contract_version") or REVIEW_CONTRACT_VERSION,
                "contract_source": "legacy_backfill" if contract.analysis_contract_id else "unsupported_legacy",
                "artifact_manifest": json.dumps(manifest, sort_keys=True),
                "role_map": json.dumps(design.review_role_map, sort_keys=True),
                "design_id": values["id"],
            },
        )


async def _ensure_sqlite_indexes(conn):
    """Install indexes required by high-volume list/count paths on legacy DBs."""
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_designs_job_id ON designs (job_id)"))
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_manifests_source_job_id "
            "ON ngs_reference_set_manifests (source_job_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_manifests_manifest_sha256 "
            "ON ngs_reference_set_manifests (manifest_sha256)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_mappings_reference_set_id "
            "ON ngs_reference_set_mappings (reference_set_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_mappings_unit_id "
            "ON ngs_reference_set_mappings (unit_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_reference_targets_reference_set_id "
            "ON ngs_pooled_reference_targets (reference_set_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_reference_targets_target_id "
            "ON ngs_pooled_reference_targets (target_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_releases_assignment_job_id "
            "ON ngs_pooled_assignment_releases (assignment_job_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_releases_reference_set_id "
            "ON ngs_pooled_assignment_releases (reference_set_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_release_targets_release_id "
            "ON ngs_pooled_assignment_release_targets (release_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_release_targets_assignment_job_id "
            "ON ngs_pooled_assignment_release_targets (assignment_job_id)"
        )
    )


async def _ensure_ngs_reference_set_immutability(conn):
    """Install append-only guards for the server-owned NGS reference-set rows."""
    for table_name, label in (
        ("ngs_reference_set_manifests", "NGS reference-set manifests"),
        ("ngs_reference_set_mappings", "NGS reference-set mappings"),
        ("ngs_pooled_reference_targets", "NGS pooled reference targets"),
        ("ngs_pooled_assignment_releases", "NGS pooled assignment releases"),
        ("ngs_pooled_assignment_release_targets", "NGS pooled assignment release targets"),
    ):
        await conn.execute(
            text(
                f"""
                CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_no_update"
                BEFORE UPDATE ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{label} are immutable');
                END
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_no_delete"
                BEFORE DELETE ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{label} are immutable');
                END
                """
            )
        )


async def _ensure_table_columns(conn, table_name: str, columns):
    """Ensure all nullable columns exist in a SQLite table."""
    pragma = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    existing_cols = {row[1] for row in pragma.fetchall()}
    
    dialect = engine.dialect
    
    for col in columns:
        if col.name in existing_cols:
            continue
        if col.primary_key:
            continue
        
        # Only auto-add nullable columns or ones with explicit defaults
        has_default = col.default is not None or col.server_default is not None
        if not col.nullable and not has_default:
            continue
        
        col_type = col.type.compile(dialect=dialect)
        default_clause = ""
        if col.server_default is not None:
            default_clause = f" DEFAULT {col.server_default.arg}"
        elif col.default is not None:
            default_arg = getattr(col.default, "arg", None)
            if default_arg is not None and not callable(default_arg):
                if isinstance(default_arg, str):
                    default_clause = f" DEFAULT '{default_arg}'"
                elif isinstance(default_arg, bool):
                    default_clause = f" DEFAULT {1 if default_arg else 0}"
                else:
                    default_clause = f" DEFAULT {default_arg}"
        
        null_clause = "" if col.nullable else " NOT NULL"
        await conn.execute(
            text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}{null_clause}{default_clause}')
        )


async def get_session() -> AsyncSession:
    """Dependency for getting database session."""
    async with async_session() as session:
        yield session

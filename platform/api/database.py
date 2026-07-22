"""
Database models and initialization for BioModStack Control Platform.

Uses SQLAlchemy with async SQLite.
"""

from sqlalchemy import Column, String, Text, Integer, Float, Boolean, DateTime, JSON, ForeignKey, UniqueConstraint, text, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.types import TypeDecorator
from datetime import datetime
import json
from types import SimpleNamespace
from paths import get_db_path, get_db_url

# Database path - resolved via paths helper (supports env overrides)
DEFAULT_DB_PATH = get_db_path()
DATABASE_URL = get_db_url()

engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"timeout": 30}
engine = create_async_engine(DATABASE_URL, **engine_kwargs)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
    review_artifact_manifest = Column(JSON, nullable=True)
    review_role_map = Column(JSON, nullable=True)

    selected_loop_scope = Column(JSON, nullable=True)
    provenance = Column(JSON, nullable=True)
    
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
    chains_ptm = Column(JSON, nullable=True)  # {"0": 0.76, "1": 0.51} per-chain pTM
    pair_chains_iptm = Column(JSON, nullable=True)  # NxN chain matrix for heatmap
    disorder = Column(Float, nullable=True)  # Protenix disorder score/probability
    num_recycles = Column(Integer, nullable=True)  # Recycling iterations reported by model
    has_clash = Column(Boolean, nullable=True)  # Steric clash flag from confidence output
    confidence_metrics = Column(JSON, nullable=True)  # Raw model confidence JSON payload
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
    chain_metrics = Column(JSON, nullable=True)  # {"A": {"type": "protein", ...}}
    residue_plddt = Column(JSON, nullable=True)  # [85.2, 91.3, ...] per residue
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
    rfa_loop_metrics = Column(JSON, nullable=True)
    rfa_hotspot_metrics = Column(JSON, nullable=True)
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
    rfa_design_loops = Column(JSON, nullable=True)
    rfa_hotspots = Column(JSON, nullable=True)

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
    frustration_high_count = Column(Integer, nullable=True)    # Residues with frust <= -1.0
    frustration_min_count = Column(Integer, nullable=True)     # Residues with frust >= 0.58
    frustration_pct_high = Column(Float, nullable=True)        # Percent highly frustrated
    frustration_residues = Column(JSON, nullable=True)         # Per-residue: [{pos, chain, frust, class}]
    frustration_csv_path = Column(String(500), nullable=True)  # Path to full CSV
    
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
    ppiflow_loop_metrics = Column(JSON, nullable=True)

    # Metric provenance/completeness: explicit source/formula/direction for model and BMS-derived scores.
    metric_provenance = Column(JSON, nullable=True)
    metric_completeness = Column(JSON, nullable=True)
        
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
    payload_json = Column(JSON, nullable=False)
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
    provenance_json = Column(JSON, nullable=False, default=dict)


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
    await _ensure_table_columns(conn, "nucleotide_sequences", NucleotideSequence.__table__.columns)
    await _ensure_table_columns(conn, "primers", Primer.__table__.columns)
    await _backfill_design_review_contracts(conn)
    await _ensure_sqlite_indexes(conn)


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

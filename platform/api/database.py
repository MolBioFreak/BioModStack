"""
Database models and initialization for ProteinDJ Control Platform.

Uses SQLAlchemy with async SQLite.
"""

from sqlalchemy import Column, String, Text, Integer, Float, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
import os

# Database path - relative to project root
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "sqlite+aiosqlite:///./biomodstack.db"
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


class Job(Base):
    """Pipeline job record."""
    __tablename__ = "jobs"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="queued")
    model_id = Column(String(50), nullable=False)
    mode = Column(String(100), nullable=False)  # monomer_denovo, binder_denovo, etc.
    params = Column(JSON, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    output_dir = Column(String(500), nullable=True)
    nextflow_run_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GPU ORCHESTRATOR: Queue Management
    # ═══════════════════════════════════════════════════════════════════════════
    batch_id = Column(String(36), nullable=True, index=True)  # Groups related jobs (UUID)
    batch_name = Column(String(255), nullable=True)  # Human-readable, auto-generated or user-set
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
    
    # Relationship to designs
    designs = relationship("Design", back_populates="job", cascade="all, delete-orphan")


class Design(Base):
    """Individual protein design result."""
    __tablename__ = "designs"
    
    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False)
    name = Column(String(255), nullable=False)
    pdb_path = Column(String(500), nullable=False)
    json_path = Column(String(500), nullable=True)
    
    # RFdiffusion metrics
    num_helices = Column(Integer, nullable=True)
    num_strands = Column(Integer, nullable=True)
    rog = Column(Float, nullable=True)  # Radius of gyration
    
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
    humanness_score = Column(Float, nullable=True)  # OAS/ANARCI derived
    developability_flags = Column(JSON, nullable=True)  # TAP-like warnings
    
    # Stability / Inverse Folding Data
    stability_data = Column(JSON, nullable=True)  # ThermoMPNN ddG matrix
    antifold_logits_path = Column(String(500), nullable=True)  # Path to probabilities.csv for heatmap
        
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to job
    job = relationship("Job", back_populates="designs")


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
    is_circular = Column(Boolean, default=False)  # Circular (plasmid) or linear
    length = Column(Integer, nullable=False)
    
    # Features/annotations stored as JSON array
    # Format: [{"id": "f1", "name": "AmpR", "type": "CDS", "start": 0, "end": 100, "strand": 1, "color": "#F00", "notes": {...}}]
    features = Column(JSON, nullable=True)
    
    # Primers associated with this sequence
    # Format: [{"id": "p1", "name": "Fwd", "sequence": "ATCG...", "start": 0, "end": 20, "tm": 58.5}]
    primers = Column(JSON, nullable=True)
    
    # Metadata
    organism = Column(String(255), nullable=True)
    accession = Column(String(100), nullable=True)  # GenBank accession
    source_file = Column(String(255), nullable=True)  # Original filename if imported
    
    # GC content cached
    gc_content = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)


# MSACache removed - now using file-based caching in /mnt/BioModStack/msa_cache


async def init_db():
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Dependency for getting database session."""
    async with async_session() as session:
        yield session

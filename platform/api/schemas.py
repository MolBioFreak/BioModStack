"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


def serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format with Z suffix for UTC."""
    if dt is None:
        return None
    # Ensure UTC times have Z suffix so JavaScript parses correctly
    return dt.isoformat() + 'Z'


class JobStatus(str, Enum):
    """Job status enum."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_INPUT = "awaiting_input"
    FAILED = "failed"
    CANCELLED = "cancelled"



# --- Job Schemas ---

class JobCreate(BaseModel):
    """Request schema for creating a new job."""
    name: str = Field(..., min_length=1, max_length=255)
    model_id: str = Field(..., description="ID of the model to use (e.g., rfdiffusion)")
    mode: str = Field(..., description="Mode ID for the selected model")
    params: dict = Field(default_factory=dict)
    pinned_gpu: Optional[int] = Field(None, description="Optional: Pin job to specific GPU (0-3)")
    # Child job tracking (spawn-wait-collect pattern)
    parent_job_id: Optional[str] = Field(None, description="Parent job ID for child jobs")
    child_stage: Optional[str] = Field(None, description="Stage identifier (rfantibody, fampnn, boltz2)")
    batch_id: Optional[str] = Field(None, description="Batch ID for grouping")
    batch_name: Optional[str] = Field(None, description="Human-readable batch name")
    sequence_length: Optional[int] = Field(None, description="Sequence length for VRAM estimation")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "binder_test_001",
                "model_id": "rfdiffusion",
                "mode": "binder_denovo",
                "params": {
                    "rfd_num_designs": 8,
                    "seqs_per_design": 4,
                    "rfd_contigs": "[A17-131/0 60-100]",
                    "rfd_input_pdb": "./benchmarkdata/5o45_pd-l1.pdb"
                },
                "pinned_gpu": 0
            }
        }
    )


class JobResponse(BaseModel):
    """Response schema for a job."""
    id: str
    name: str
    status: JobStatus
    model_id: str
    mode: str
    params: dict
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output_dir: Optional[str] = None
    error_message: Optional[str] = None
    design_count: int = 0
    requested_design_count: Optional[int] = None
    # Batch grouping for job sets
    batch_id: Optional[str] = None
    batch_name: Optional[str] = None
    # Parent-child tracking (SWA pattern)
    parent_job_id: Optional[str] = None
    child_stage: Optional[str] = None
    lineage_root_job_id: Optional[str] = None
    stage_family: Optional[str] = None
    stage_mode: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    source_stage_family: Optional[str] = None
    source_stage_mode: Optional[str] = None
    source_selection_manifest_path: Optional[str] = None
    source_selection_count: Optional[int] = None
    selected_input_artifact_class: Optional[str] = None
    selected_input_schema_version: Optional[int] = None
    selection_source_type: Optional[str] = None
    selection_source_job_id: Optional[str] = None
    selection_dataset_name: Optional[str] = None
    selected_loop_scope: Optional[dict] = None
    provenance: Optional[dict] = None
    saved_selection_sets: Optional[List[dict]] = None
    # GPU assignment
    assigned_gpu: Optional[int] = None
    vram_estimate_mb: Optional[int] = None
    # Stage tracking for multi-stage pipelines
    current_stage: Optional[str] = None
    completed_stages: Optional[List[str]] = None
    stage_outputs: Optional[dict] = None
    awaiting_input: Optional[bool] = None
    awaiting_stage: Optional[str] = None
    awaiting_payload: Optional[dict] = None
    decision_history: Optional[List[dict]] = None
    
    model_config = ConfigDict(from_attributes=True)
    
    from pydantic import field_serializer
    
    @field_serializer('created_at', 'started_at', 'completed_at')
    @classmethod
    def serialize_datetime(cls, dt: Optional[datetime]) -> Optional[str]:
        """Serialize datetime with Z suffix for UTC."""
        if dt is None:
            return None
        return dt.isoformat() + 'Z'


class JobList(BaseModel):
    """Response schema for job list."""
    jobs: List[JobResponse]
    total: int


class ExternalImportPreviewRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=1000)
    provider_hint: str = Field(default="boltz_api", max_length=64)


class ExternalImportCreateRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=1000)
    provider: str = Field(default="boltz_api", max_length=64)
    preview_fingerprint: str = Field(..., min_length=64, max_length=64)
    dataset_name: str = Field(..., min_length=1, max_length=255)
    job_name: Optional[str] = Field(default=None, max_length=255)


class ExternalImportPreviewResponse(BaseModel):
    provider: str
    resource_type: str
    provider_job_id: str
    model: Optional[str] = None
    model_version: Optional[str] = None
    status: str
    sample_count: int
    entities: List[dict]
    source_fingerprint: str
    run_metadata_sha256: str
    archive_sha256: Optional[str] = None
    importable: bool
    error_code: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    provider_metadata: dict = Field(default_factory=dict)


class ExternalImportResponse(BaseModel):
    id: str
    provider_id: str
    resource_type: str
    provider_job_id: str
    state: str
    source_fingerprint: str
    bms_job_id: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    imported_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Design Schemas ---

class DesignResponse(BaseModel):
    """Response schema for a design."""
    id: str
    job_id: str
    name: str
    pdb_path: str
    
    # Metrics
    plddt_overall: Optional[float] = None
    plddt_binder: Optional[float] = None
    pae_interaction: Optional[float] = None
    rmsd_binder: Optional[float] = None
    conf_score: Optional[float] = None
    mpnn_score: Optional[float] = None
    ligand_iptm: Optional[float] = None
    affinity_score: Optional[float] = None
    binder_probability: Optional[float] = None

    # Antibody Specific
    cdr_h1: Optional[str] = None
    cdr_h2: Optional[str] = None
    cdr_h3: Optional[str] = None
    cdr_l1: Optional[str] = None
    cdr_l2: Optional[str] = None
    cdr_l3: Optional[str] = None
    
    # CDR Loop Lengths (for sorting/filtering)
    binder_length: Optional[int] = None
    cdr_h1_length: Optional[int] = None
    cdr_h2_length: Optional[int] = None
    cdr_h3_length: Optional[int] = None
    cdr_l1_length: Optional[int] = None  # NULL for VHH/nanobodies
    cdr_l2_length: Optional[int] = None
    cdr_l3_length: Optional[int] = None
    antibody_type: Optional[str] = None  # vhh, fab, scfv
    
    humanness_score: Optional[float] = None
    stability_data: Optional[Any] = None  # JSON blob for heatmaps
        
    # User data
    is_favorite: bool = False
    notes: Optional[str] = None

    # Lineage / provenance
    lineage_root_job_id: Optional[str] = None
    parent_design_id: Optional[str] = None
    origin_design_id: Optional[str] = None
    origin_job_id: Optional[str] = None
    origin_backbone_design_id: Optional[str] = None
    stage_family: Optional[str] = None
    stage_mode: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    source_stage_family: Optional[str] = None
    source_stage_mode: Optional[str] = None
    source_pdb_path: Optional[str] = None
    source_design_name: Optional[str] = None
    artifact_class: Optional[str] = None
    artifact_schema_version: Optional[int] = None
    review_profile_id: Optional[str] = None
    review_contract_version: Optional[int] = None
    review_contract_source: Optional[str] = None
    review_artifact_manifest: Optional[Any] = None
    review_role_map: Optional[Any] = None
    selected_loop_scope: Optional[Any] = None
    provenance: Optional[Any] = None
    
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class DesignUpdate(BaseModel):
    """Request schema for updating a design."""
    is_favorite: Optional[bool] = None
    notes: Optional[str] = None


class DesignList(BaseModel):
    """Response schema for design list."""
    designs: List[DesignResponse]
    total: int


# --- GPU Schemas ---

class GPUStatus(BaseModel):
    """Status of a single GPU."""
    index: int
    name: str
    utilization: int  # percentage
    memory_used_mb: int
    memory_total_mb: int
    temperature: int  # celsius
    current_task: Optional[str] = None


class GPUStatusResponse(BaseModel):
    """Response schema for GPU status."""
    gpus: List[GPUStatus]
    timestamp: datetime


# --- File Schemas ---

class DirectoryEntry(BaseModel):
    """Entry in a directory listing."""
    name: str
    path: str
    is_directory: bool
    size_bytes: Optional[int] = None
    modified_at: Optional[datetime] = None


class DirectoryListing(BaseModel):
    """Response schema for directory listing."""
    path: str
    entries: List[DirectoryEntry]

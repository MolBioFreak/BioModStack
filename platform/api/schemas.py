"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, List, Any, Literal
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
    execution_target_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=160,
        description="Explicit execution target. Omit for local execution.",
    )
    # Child job tracking (spawn-wait-collect pattern)
    parent_job_id: Optional[str] = Field(None, description="Parent job ID for child jobs")
    child_stage: Optional[str] = Field(None, description="Stage identifier (rfantibody, fampnn, boltz2)")
    batch_id: Optional[str] = Field(None, description="Batch ID for grouping")
    batch_name: Optional[str] = Field(None, description="Human-readable batch name")
    sequence_length: Optional[int] = Field(None, description="Sequence length for VRAM estimation")
    launch_context_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=128,
        description="Opaque server-owned Project launch context; hierarchy identity is never accepted here",
    )

    @model_validator(mode="before")
    @classmethod
    def require_native_rfd3_gpu_pin(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        model_id = str(data.get("model_id") or "").strip().lower()
        mode = str(data.get("mode") or "").strip().lower()
        if model_id == "protein_local_redesign" and mode == "local_redesign":
            pinned_gpu = data.get("pinned_gpu")
            if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
                raise ValueError("native RFD3 local redesign requires one explicit non-negative integer pinned_gpu")
        return data
    
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
    pinned_gpu: Optional[int] = None
    assigned_gpu: Optional[int] = None
    vram_estimate_mb: Optional[int] = None
    execution_target_id: Optional[str] = None
    execution_source_revision: Optional[str] = None
    execution_source_tree: Optional[str] = None
    execution_bundle_sha256: Optional[str] = None
    remote_attempt_id: Optional[str] = None
    remote_state: Optional[str] = None
    # Stage tracking for multi-stage pipelines
    current_stage: Optional[str] = None
    completed_stages: Optional[List[str]] = None
    stage_outputs: Optional[dict] = None
    awaiting_input: Optional[bool] = None
    awaiting_stage: Optional[str] = None
    awaiting_payload: Optional[dict] = None
    decision_history: Optional[List[dict]] = None
    launch_context_id: Optional[str] = None
    launch_context_binding: Optional[dict] = None
    return_uri: Optional[str] = None
    frustrampnn_result_count: int = 0
    frustrampnn_reopen_destination: Optional[dict] = None
    conformational_mapping_request_id: Optional[str] = None
    
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


class LaunchContextCreateRequest(BaseModel):
    """Request a server-owned launcher handoff within the hierarchy in the URL."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    workflow_revision_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    return_uri: str = Field(min_length=1, max_length=1000)


class LaunchContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["bms.launch-context.v1", "bms.launch-context.v2"] = Field(
        alias="schema", serialization_alias="schema"
    )
    launch_context_id: str
    project_id: str
    global_experiment_id: str
    domain_experiment_id: str
    workflow_id: Optional[str]
    workflow_revision_id: Optional[str]
    preparation_id: Optional[str] = None
    run_attempt_id: Optional[str] = None
    normalized_request_sha256: Optional[str] = None
    validation_receipt_id: Optional[str] = None
    validation_receipt_sha256: Optional[str] = None
    pinned_gpu: Optional[int]
    return_uri: str
    source_receipt_id: str
    state: Literal["issued", "reserved", "claimed", "consumed"]
    canonical_job_id: Optional[str] = None
    recovery_job_id: Optional[str] = None
    binding_receipt: Optional[dict] = None
    pinned_scheduler: Optional[dict] = None
    issued_at: datetime
    expires_at: datetime


class BoltzApiComplexComponent(BaseModel):
    """Strict provider-native complex entity; UI aliases never cross this boundary."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["protein", "dna", "rna", "ligand_ccd", "ligand_smiles"]
    value: str = Field(min_length=1, max_length=100000)
    chain_ids: List[str] = Field(min_length=1, max_length=8)


class BoltzApiStructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    client_request_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    model: str = Field(default="boltz-2.1", max_length=64)
    sequence: str = Field(..., min_length=1, max_length=100000)
    primary_chain_id: str = Field(default="A", min_length=1, max_length=8)
    complex_components: List[BoltzApiComplexComponent] = Field(default_factory=list, max_length=64)
    num_samples: int = Field(default=1, ge=1, le=10)
    use_msa: bool = True


class BoltzApiEstimateResponse(BaseModel):
    model: str
    provider_input: dict
    estimate: dict
    estimate_fingerprint: str


class BoltzApiSubmitRequest(BoltzApiStructureRequest):
    approved_estimate_fingerprint: str = Field(..., min_length=64, max_length=64)


class BoltzApiEntitiesCapability(BaseModel):
    status: Literal["supported"]
    types: List[Literal["protein", "dna", "rna", "ligand_ccd", "ligand_smiles"]]


class BoltzApiMsaCapability(BaseModel):
    status: Literal["supported"]
    provider_default: Literal["omit"]
    disable_value: dict[Literal["type"], Literal["empty"]]


class BoltzApiSampleRangeCapability(BaseModel):
    status: Literal["supported"]
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)


class BoltzApiTemplatesCapability(BaseModel):
    status: Literal["unavailable_pending_schema_verification"]


class BoltzApiUnsupportedLocalControls(BaseModel):
    diffusion_sampling_steps: Literal["unsupported"]
    recycling_steps: Literal["unsupported"]
    potentials: Literal["unsupported"]
    denoiser_chunking: Literal["unsupported"]
    gpu_pinning: Literal["unsupported"]
    parallelism: Literal["unsupported"]
    oom_retry: Literal["unsupported"]
    conditioning: Literal["unsupported"]


class BoltzApiProviderCapabilities(BaseModel):
    contract_version: Literal["bms.boltz_api.capabilities.v1"]
    entities: BoltzApiEntitiesCapability
    msa: BoltzApiMsaCapability
    num_samples: BoltzApiSampleRangeCapability
    templates: BoltzApiTemplatesCapability
    unsupported_local_controls: BoltzApiUnsupportedLocalControls


class BoltzApiCliUpdateStatus(BaseModel):
    check_status: Literal[
        "current", "update_available", "unavailable", "unavailable_pending_official_feed_verification"
    ]
    installed_version: Optional[str] = None
    latest_version: Optional[str] = None
    source: Literal["boltz_api_static_cli"]
    release_feed_url: Optional[str] = None
    release_url: Optional[str] = None
    checked_at: Optional[datetime] = None


class BoltzApiProviderStatusResponse(BaseModel):
    available: bool
    cli_available: bool
    credential_configured: bool
    model: str
    message: str
    capabilities: BoltzApiProviderCapabilities
    cli_update: BoltzApiCliUpdateStatus


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

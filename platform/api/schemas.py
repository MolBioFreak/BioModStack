"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    """Job status enum."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"



# --- Job Schemas ---

class JobCreate(BaseModel):
    """Request schema for creating a new job."""
    name: str = Field(..., min_length=1, max_length=255)
    model_id: str = Field(..., description="ID of the model to use (e.g., rfdiffusion)")
    mode: str = Field(..., description="Mode ID for the selected model")
    params: dict = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "binder_test_001",
                "model_id": "rfdiffusion",
                "mode": "binder_denovo",
                "params": {
                    "rfd_num_designs": 8,
                    "seqs_per_design": 4,
                    "rfd_contigs": "[A17-131/0 60-100]",
                    "rfd_input_pdb": "./benchmarkdata/5o45_pd-l1.pdb"
                }
            }
        }


class JobResponse(BaseModel):
    """Response schema for a job."""
    id: str
    name: str
    status: JobStatus
    model_id: str
    mode: str
    params: dict
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output_dir: Optional[str] = None
    error_message: Optional[str] = None
    design_count: int = 0
    # Batch grouping for job sets
    batch_id: Optional[str] = None
    batch_name: Optional[str] = None
    # GPU assignment
    assigned_gpu: Optional[int] = None
    vram_estimate_mb: Optional[int] = None
    # Stage tracking for multi-stage pipelines
    current_stage: Optional[str] = None
    completed_stages: Optional[List[str]] = None
    stage_outputs: Optional[dict] = None
    
    class Config:
        from_attributes = True


class JobList(BaseModel):
    """Response schema for job list."""
    jobs: List[JobResponse]
    total: int


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
    humanness_score: Optional[float] = None
    stability_data: Optional[Any] = None  # JSON blob for heatmaps
        
    # User data
    is_favorite: bool = False
    notes: Optional[str] = None
    
    created_at: datetime
    
    class Config:
        from_attributes = True


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

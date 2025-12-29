"""
Designs API router - Query and manage protein designs.

Provides endpoints for listing, filtering, and managing designs
stored in the SQLite database after pipeline ingestion.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from database import get_session, Design, Job


router = APIRouter()


# --- Pydantic Schemas ---


class ChainMetric(BaseModel):
    type: str
    length: int
    avg_plddt: Optional[float]
    plddt: Optional[List[float]]
    residue_numbers: Optional[List[int]]

class ChainMetricsResponse(BaseModel):
    design_id: str
    chains: Dict[str, ChainMetric]


class DesignResponse(BaseModel):
    id: str
    job_id: str
    name: str
    pdb_path: Optional[str]
    
    # Structural metrics
    num_helices: Optional[int]
    num_strands: Optional[int]
    rog: Optional[float]
    
    # Sequence metrics
    mpnn_score: Optional[float]
    fampnn_psce: Optional[float]
    
    # Prediction metrics
    plddt_overall: Optional[float]
    plddt_binder: Optional[float]
    pae_interaction: Optional[float]
    pae_overall: Optional[float]
    rmsd_binder: Optional[float]
    
    # Boltz-2 specific
    conf_score: Optional[float]
    ptm: Optional[float]
    ligand_iptm: Optional[float]
    affinity_score: Optional[float]
    binder_probability: Optional[float]
    
    # Interface metrics (complexes)
    iptm: Optional[float] = None
    protein_iptm: Optional[float] = None
    complex_iplddt: Optional[float] = None
    complex_ipde: Optional[float] = None
    chains_ptm: Optional[dict] = None  # {"0": 0.76, "1": 0.51}
    pair_chains_iptm: Optional[dict] = None  # NxN chain matrix
    
    # Per-residue metrics (for charts)
    residue_plddt: Optional[List[float]] = None
    chain_metrics: Optional[Dict[str, ChainMetric]] = None
    
    # User annotations
    is_favorite: bool
    notes: Optional[str]
    
    created_at: datetime
    
    class Config:
        from_attributes = True


class DesignList(BaseModel):
    designs: List[DesignResponse]
    total: int


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class NotesUpdate(BaseModel):
    notes: str


# --- Endpoints ---

@router.get("", response_model=DesignList)
async def list_designs(
    job_id: Optional[str] = None,
    plddt_min: Optional[float] = Query(None, description="Minimum pLDDT score"),
    pae_max: Optional[float] = Query(None, description="Maximum pAE score"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """
    List designs with optional filtering.
    
    Filters:
    - job_id: Filter by specific job
    - plddt_min: Minimum pLDDT threshold
    - pae_max: Maximum pAE threshold
    - favorites_only: Show only favorited designs
    """
    query = select(Design).order_by(Design.created_at.desc())
    
    # Apply filters
    conditions = []
    if job_id:
        conditions.append(Design.job_id == job_id)
    if plddt_min is not None:
        conditions.append(Design.plddt_overall >= plddt_min)
    if pae_max is not None:
        conditions.append(Design.pae_overall <= pae_max)
    if favorites_only:
        conditions.append(Design.is_favorite == True)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Get total count
    count_query = select(func.count(Design.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total = (await session.execute(count_query)).scalar()
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    return DesignList(
        designs=[DesignResponse.model_validate(d) for d in designs],
        total=total
    )


@router.get("/{design_id}", response_model=DesignResponse)
async def get_design(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific design by ID."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    return DesignResponse.model_validate(design)


@router.get("/{design_id}/pdb")
async def get_design_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Download the PDB file for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No PDB file for this design")
    
    pdb_path = Path(design.pdb_path)
    if not pdb_path.exists():
        raise HTTPException(status_code=404, detail="PDB file not found on disk")
    
    return FileResponse(
        path=pdb_path,
        filename=f"{design.name}.pdb",
        media_type="text/plain"  # Changed from chemical/x-pdb for Mol* compatibility
    )


class ResidueMetrics(BaseModel):
    """Per-residue metrics for charting."""
    design_id: str
    design_name: str
    residue_numbers: List[int]
    plddt: List[float]
    length: int


@router.get("/{design_id}/residue-metrics", response_model=ResidueMetrics)
async def get_residue_metrics(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get per-residue metrics for a design (for line charts)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.residue_plddt:
        raise HTTPException(status_code=404, detail="No per-residue data available for this design")
    
    plddt_values = design.residue_plddt
    residue_numbers = list(range(1, len(plddt_values) + 1))
    
    return ResidueMetrics(
        design_id=design.id,
        design_name=design.name,
        residue_numbers=residue_numbers,
        plddt=plddt_values,
        length=len(plddt_values)
    )


@router.get("/{design_id}/chain-metrics")
async def get_chain_metrics(design_id: str, session: AsyncSession = Depends(get_session)):
    """Return per-chain pLDDT and type information."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
        
    # Compute on-the-fly if not cached
    if not design.chain_metrics and design.pdb_path:
        try:
            from services.structure_utils import get_per_chain_metrics
            metrics = get_per_chain_metrics(design.pdb_path)
            if metrics:
                design.chain_metrics = metrics
                await session.commit()
        except Exception as e:
            print(f"Failed to compute chain metrics: {e}")
            # Don't fail the request, just return empty
    
    return design.chain_metrics or {}


@router.post("/{design_id}/favorite")
async def toggle_favorite(
    design_id: str,
    update: FavoriteUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Toggle favorite status for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.is_favorite = update.is_favorite
    await session.commit()
    
    return {"message": "Favorite updated", "is_favorite": design.is_favorite}


@router.patch("/{design_id}/notes")
async def update_notes(
    design_id: str,
    update: NotesUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update notes for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.notes = update.notes
    await session.commit()
    
    return {"message": "Notes updated", "notes": design.notes}


@router.get("/by-job/{job_id}", response_model=DesignList)
async def get_designs_for_job(
    job_id: str,
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """Get all designs for a specific job."""
    # Verify job exists
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    if not job_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")
    
    query = select(Design).where(Design.job_id == job_id).order_by(Design.name)
    query = query.limit(limit).offset(offset)
    
    result = await session.execute(query)
    designs = result.scalars().all()
    
    # Count total
    count_query = select(func.count(Design.id)).where(Design.job_id == job_id)
    total = (await session.execute(count_query)).scalar()
    
    return DesignList(
        designs=[DesignResponse.model_validate(d) for d in designs],
        total=total
    )


# --- Phase 3: Biotite-Powered Structure Analysis Endpoints ---

class StructureAnalysis(BaseModel):
    """Computed structure analysis metrics (via Biotite)."""
    design_id: str
    design_name: str
    residue_count: int
    chain_ids: List[str]
    gyration_radius: Optional[float]
    secondary_structure: dict  # {"helix": n, "sheet": n, "coil": n}


@router.get("/{design_id}/structure-analysis", response_model=StructureAnalysis)
async def get_structure_analysis(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get computed structure analysis for a design.
    
    Uses Biotite to compute:
    - Residue count
    - Chain IDs
    - Radius of gyration
    - Secondary structure (helix/sheet/coil counts)
    """
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    structure_path = Path(design.pdb_path)
    if not structure_path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")
    
    # Import Biotite utilities
    try:
        from services.structure_utils import (
            get_residue_count, get_chain_ids, 
            compute_gyration_radius, get_secondary_structure
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="Structure analysis module not available")
    
    return StructureAnalysis(
        design_id=design.id,
        design_name=design.name,
        residue_count=get_residue_count(structure_path),
        chain_ids=[str(c) for c in get_chain_ids(structure_path)],
        gyration_radius=compute_gyration_radius(structure_path),
        secondary_structure=get_secondary_structure(structure_path)
    )


class StructureComparison(BaseModel):
    """RMSD comparison between two structures."""
    design_id: str
    other_design_id: str
    rmsd_backbone: Optional[float]
    rmsd_all_atom: Optional[float]


@router.get("/{design_id}/compare/{other_design_id}", response_model=StructureComparison)
async def compare_structures(
    design_id: str,
    other_design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Compute RMSD between two design structures.
    
    Returns backbone RMSD (N, CA, C atoms) and all-atom RMSD.
    """
    # Get both designs
    result1 = await session.execute(select(Design).where(Design.id == design_id))
    design1 = result1.scalar_one_or_none()
    
    result2 = await session.execute(select(Design).where(Design.id == other_design_id))
    design2 = result2.scalar_one_or_none()
    
    if not design1:
        raise HTTPException(status_code=404, detail=f"Design {design_id} not found")
    if not design2:
        raise HTTPException(status_code=404, detail=f"Design {other_design_id} not found")
    
    if not design1.pdb_path or not design2.pdb_path:
        raise HTTPException(status_code=404, detail="One or both designs missing structure files")
    
    path1, path2 = Path(design1.pdb_path), Path(design2.pdb_path)
    if not path1.exists() or not path2.exists():
        raise HTTPException(status_code=404, detail="Structure files not found on disk")
    
    try:
        from services.structure_utils import compute_rmsd
    except ImportError:
        raise HTTPException(status_code=500, detail="Structure analysis module not available")
    
    return StructureComparison(
        design_id=design_id,
        other_design_id=other_design_id,
        rmsd_backbone=compute_rmsd(path1, path2, backbone_only=True),
        rmsd_all_atom=compute_rmsd(path1, path2, backbone_only=False)
    )


class PAEData(BaseModel):
    """PAE matrix data for heatmap visualization."""
    design_id: str
    design_name: str
    pae_matrix: List[List[float]]  # 2D matrix
    size: int  # Matrix dimension


@router.get("/{design_id}/pae", response_model=PAEData)
async def get_pae_data(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get PAE matrix data for heatmap visualization.
    
    Searches for *_confidences.json files associated with the design's PDB path.
    """
    import json
    import os
    
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    # Find confidences.json near the PDB/CIF file
    pdb_path = Path(design.pdb_path)
    parent_dir = pdb_path.parent
    
    # Look for *_confidences.json in same directory or parent
    confidence_files = list(parent_dir.glob("*_confidences.json"))
    if not confidence_files:
        confidence_files = list(parent_dir.parent.glob("*_confidences.json"))
    
    if not confidence_files:
        raise HTTPException(status_code=404, detail="No PAE data found for this design")
    
    # Read the first confidence file found
    try:
        with open(confidence_files[0], 'r') as f:
            data = json.load(f)
        
        pae_matrix = data.get('pae')
        if not pae_matrix:
            raise HTTPException(status_code=404, detail="PAE matrix not found in confidence file")
        
        # Downsample if too large (for rendering performance)
        size = len(pae_matrix)
        if size > 200:
            # Downsample by taking every Nth element
            step = size // 200
            pae_matrix = [[pae_matrix[i][j] for j in range(0, size, step)] for i in range(0, size, step)]
            size = len(pae_matrix)
        
        return PAEData(
            design_id=design.id,
            design_name=design.name,
            pae_matrix=pae_matrix,
            size=size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read PAE data: {str(e)}")


class AntibodyData(BaseModel):
    """Aggregate antibody metrics."""
    design_id: str
    cdrs: Dict[str, Optional[str]]
    humanness_score: Optional[float]
    stability_data: Optional[Dict[str, Any]]
    imgt_pdb_url: Optional[str]
    
@router.get("/{design_id}/antibody", response_model=AntibodyData)
async def get_antibody_data(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get antibody-specific data (CDRs, humanness, stability)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    imgt_url = None
    if design.pdb_path:
        pdb_path = Path(design.pdb_path)
        # Check for _imgt.pdb variant
        # If original is "X.pdb", look for "X_imgt.pdb"
        # If original is "X_imgt.pdb", we are good.
        if "_imgt" in pdb_path.name:
             imgt_url = f"/api/designs/{design.id}/pdb"
        else:
             imgt_chk = pdb_path.parent / f"{pdb_path.stem}_imgt.pdb"
             if imgt_chk.exists():
                 imgt_url = f"/api/designs/{design.id}/pdb-imgt"

    return AntibodyData(
        design_id=design.id,
        cdrs={
            "H1": design.cdr_h1, "H2": design.cdr_h2, "H3": design.cdr_h3,
            "L1": design.cdr_l1, "L2": design.cdr_l2, "L3": design.cdr_l3
        },
        humanness_score=design.humanness_score,
        stability_data=design.stability_data,
        imgt_pdb_url=imgt_url
    )

@router.get("/{design_id}/pdb-imgt")
async def get_design_imgt_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Download the IMGT-renumbered PDB file for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design or not design.pdb_path:
        raise HTTPException(status_code=404, detail="Design not found or no PDB")
    
    pdb_path = Path(design.pdb_path)
    imgt_path = pdb_path.parent / f"{pdb_path.stem}_imgt.pdb"
    
    if not imgt_path.exists():
        raise HTTPException(status_code=404, detail="IMGT renumbered PDB not found")
    
    return FileResponse(
        path=imgt_path,
        filename=f"{design.name}_imgt.pdb",
        media_type="text/plain"
    )

@router.get("/{design_id}/antifold-logits")
async def get_antifold_logits(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get AntiFold probability CSV data (if available)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design or not design.antifold_logits_path:
        raise HTTPException(status_code=404, detail="No AntiFold data for this design")
        
    path = Path(design.antifold_logits_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Logits file not found")
        
    return FileResponse(path, media_type="text/csv", filename=f"{design.name}_logits.csv")

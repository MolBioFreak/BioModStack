"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
import uuid
from datetime import datetime
from pathlib import Path

from database import get_session, Job, Design
from schemas import JobCreate, JobResponse, JobList, JobStatus
from services.nextflow import launch_nextflow_job, cancel_nextflow_job

from model_registry import get_registry

router = APIRouter()

# Project root for resolving relative output paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def count_structure_files(output_dir: str) -> int:
    """Count PDB and CIF structure files in a job output directory."""
    try:
        output_path = PROJECT_ROOT / output_dir
        if not output_path.exists():
            return 0
        
        pdb_count = len(list(output_path.glob("**/*.pdb")))
        cif_count = len(list(output_path.glob("**/*.cif")))
        return pdb_count + cif_count
    except Exception:
        return 0


@router.get("", response_model=JobList)
async def list_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session)
):
    """List all jobs with optional status filter."""
    query = select(Job).order_by(Job.created_at.desc())
    
    if status:
        query = query.where(Job.status == status.value)
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    jobs = result.scalars().all()
    
    # Get total count
    count_query = select(func.count(Job.id))
    if status:
        count_query = count_query.where(Job.status == status.value)
    total = (await session.execute(count_query)).scalar()
    
    # Get design counts for each job
    job_responses = []
    for job in jobs:
        design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
        design_count = (await session.execute(design_count_query)).scalar() or 0
        
        # For structure prediction jobs (or any job with 0 designs but completed status),
        # fall back to counting structure files in the output directory
        if design_count == 0 and job.status == JobStatus.COMPLETED.value and job.output_dir:
            design_count = count_structure_files(job.output_dir)
        
        job_responses.append(JobResponse(
            id=job.id,
            name=job.name,
            status=job.status,
            model_id=job.model_id,
            mode=job.mode,
            params=job.params,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            output_dir=job.output_dir,
            error_message=job.error_message,
            design_count=design_count
        ))
    
    return JobList(jobs=job_responses, total=total)


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Create and queue a new pipeline job."""
    registry = get_registry()
    
    # Skip validation for template jobs (they have pre-validated params)
    if not job_data.model_id.startswith('template_'):
        # Validate model and mode
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, job_data.params)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
    
    job_id = str(uuid.uuid4())
    
    # Create output directory name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"pdj_results/{job_data.name}_{timestamp}"
    
    # Create job record
    job = Job(
        id=job_id,
        name=job_data.name,
        status=JobStatus.QUEUED.value,
        model_id=job_data.model_id,
        mode=job_data.mode,
        params=job_data.params,
        output_dir=output_dir
    )
    
    session.add(job)
    await session.commit()
    await session.refresh(job)
    
    # Launch job in background
    background_tasks.add_task(
        launch_nextflow_job,
        job_id=job_id,
        model_id=job_data.model_id,
        mode=job_data.mode,
        params=job_data.params,
        output_dir=output_dir
    )
    
    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=job.params,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=job.output_dir,
        error_message=job.error_message,
        design_count=0
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get details of a specific job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get design count
    design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
    design_count = (await session.execute(design_count_query)).scalar()
    
    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=job.params,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=job.output_dir,
        error_message=job.error_message,
        design_count=design_count or 0
    )


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a running or queued job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in [JobStatus.QUEUED.value, JobStatus.RUNNING.value]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel job with status: {job.status}"
        )
    
    # Cancel the Nextflow process if running
    if job.nextflow_run_id:
        await cancel_nextflow_job(job.nextflow_run_id)
    
    job.status = JobStatus.CANCELLED.value
    job.completed_at = datetime.utcnow()
    await session.commit()
    
    return {"message": "Job cancelled", "job_id": job_id}


@router.get("/{job_id}/structure-files")
async def list_structure_files(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """List all PDB and CIF structure files for a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        return {"structures": []}
    
    output_path = PROJECT_ROOT / job.output_dir
    if not output_path.exists():
        return {"structures": []}
    
    structures = []
    
    # Find all PDB files
    for pdb_file in output_path.glob("**/*.pdb"):
        rel_path = str(pdb_file.relative_to(PROJECT_ROOT))
        structures.append({
            "name": pdb_file.stem,
            "filename": pdb_file.name,
            "path": rel_path,
            "type": "pdb",
            "size_bytes": pdb_file.stat().st_size,
        })
    
    # Find all CIF files
    for cif_file in output_path.glob("**/*.cif"):
        rel_path = str(cif_file.relative_to(PROJECT_ROOT))
        structures.append({
            "name": cif_file.stem,
            "filename": cif_file.name,
            "path": rel_path,
            "type": "cif",
            "size_bytes": cif_file.stat().st_size,
        })
    
    # Sort by name
    structures.sort(key=lambda x: x["name"])
    
    return {"structures": structures, "count": len(structures)}


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    tail: int = 100,
    session: AsyncSession = Depends(get_session)
):
    """Get log output for a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # TODO: Read from Nextflow log file
    log_path = f"{job.output_dir}/.nextflow.log" if job.output_dir else None
    
    if log_path:
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                return {"logs": "".join(lines[-tail:])}
        except FileNotFoundError:
            return {"logs": "Log file not yet available"}
    
    return {"logs": "No log file path configured"}


@router.get("/{job_id}/docking-results")
async def get_docking_results(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get docking SDF results for a completed DiffDock job.
    
    Returns list of SDF files with confidence scores, sorted by rank.
    """
    import re
    from pathlib import Path
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        return {"sdfs": [], "message": "No output directory configured"}
    
    # Look for DiffDock results
    # Structure: output_dir/run/diffdock/results/complex_name/*.sdf
    from services.nextflow import PROJECT_ROOT
    results_dir = PROJECT_ROOT / job.output_dir / "run" / "diffdock" / "results"
    
    if not results_dir.exists():
        return {"sdfs": [], "message": "No docking results found"}
    
    sdfs = []
    for sdf_file in results_dir.rglob("*.sdf"):
        # Parse confidence from filename like "rank1_confidence-1.92.sdf"
        confidence = None
        match = re.search(r'confidence(-?\d+\.?\d*)', sdf_file.name)
        if match:
            confidence = float(match.group(1))
        
        # Parse rank
        rank = None
        rank_match = re.search(r'rank(\d+)', sdf_file.name)
        if rank_match:
            rank = int(rank_match.group(1))
        
        sdfs.append({
            "name": sdf_file.name,
            "path": str(sdf_file.relative_to(PROJECT_ROOT)),
            "absolute_path": str(sdf_file),
            "confidence": confidence,
            "rank": rank,
            "complex_name": sdf_file.parent.name
        })
    
    # Sort by rank (handle None and 0 correctly)
    sdfs = sorted(sdfs, key=lambda x: x.get('rank') if x.get('rank') is not None else 999)
    
    return {
        "sdfs": sdfs,
        "total": len(sdfs),
        "job_id": job_id,
        "output_dir": job.output_dir
    }


@router.get("/{job_id}/docking-results/{filename}")
async def get_sdf_content(
    job_id: str,
    filename: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get the content of a specific SDF file for 3D visualization.
    """
    from pathlib import Path
    from fastapi.responses import PlainTextResponse
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Security: validate filename
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    from services.nextflow import PROJECT_ROOT
    results_dir = PROJECT_ROOT / job.output_dir / "run" / "diffdock" / "results"
    
    # Find the file (it may be in a subdirectory)
    sdf_files = list(results_dir.rglob(filename))
    if not sdf_files:
        raise HTTPException(status_code=404, detail="SDF file not found")
    
    content = sdf_files[0].read_text()
    return PlainTextResponse(content, media_type="chemical/x-mdl-sdfile")


@router.get("/{job_id}/protein-pdb")
async def get_protein_pdb(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get the protein PDB content for a docking job.
    Looks for PDB in inputs directory or from job params.
    """
    from pathlib import Path
    from fastapi.responses import PlainTextResponse
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    from services.nextflow import PROJECT_ROOT
    
    # Try multiple locations for the PDB file
    pdb_content = None
    
    # 1. Check job params for explicit protein_pdb path
    if job.params and job.params.get('protein_pdb'):
        pdb_path = Path(job.params['protein_pdb'])
        if not pdb_path.is_absolute():
            pdb_path = PROJECT_ROOT / pdb_path
        if pdb_path.exists():
            pdb_content = pdb_path.read_text()
    
    # 2. Check inputs directory in output_dir
    if not pdb_content and job.output_dir:
        inputs_dir = PROJECT_ROOT / job.output_dir / "inputs"
        if inputs_dir.exists():
            pdb_files = list(inputs_dir.glob("*.pdb"))
            if pdb_files:
                pdb_content = pdb_files[0].read_text()
    
    # 3. Check diffdock prep directory
    if not pdb_content and job.output_dir:
        prep_dir = PROJECT_ROOT / job.output_dir / "run" / "diffdock" / "prep"
        if prep_dir.exists():
            pdb_files = list(prep_dir.rglob("*.pdb"))
            if pdb_files:
                pdb_content = pdb_files[0].read_text()
    
    if not pdb_content:
        raise HTTPException(status_code=404, detail="Protein PDB not found")
    
    return PlainTextResponse(pdb_content, media_type="chemical/x-pdb")

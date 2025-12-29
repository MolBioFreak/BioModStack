"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
import uuid
import os
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
            design_count=design_count,
            batch_id=job.batch_id,
            batch_name=job.batch_name,
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
    
    # DEBUG: Trace complex_components in incoming request
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"DEBUG jobs.py: Received job_data.params keys: {list(job_data.params.keys())}")
    logger.warning(f"DEBUG jobs.py: num_parallel_jobs = {job_data.params.get('num_parallel_jobs', 'NOT SET')}")
    if 'complex_components' in job_data.params:
        logger.warning(f"DEBUG jobs.py: complex_components found with {len(job_data.params['complex_components'])} items")
    else:
        logger.warning("DEBUG jobs.py: complex_components NOT in job_data.params!")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # JOB MULTIPLIER: Create N separate jobs for multi-GPU distribution
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Check for mutagenesis batch submission (variants array)
    # This allows a single API call to create MSA batch job + N inference jobs
    mutagenesis_variants = job_data.params.pop('mutagenesis_variants', None)
    
    # Extract num_parallel_jobs (job multiplier) and remove from params passed to Nextflow
    # This way each individual Nextflow run does 1 simulation, but we create N jobs
    num_jobs = job_data.params.pop('num_parallel_jobs', 1)
    if mutagenesis_variants and len(mutagenesis_variants) > 0:
        # Mutagenesis mode: num_jobs = number of variants
        num_jobs = len(mutagenesis_variants)
        logger.info(f"[MUTAGENESIS] Detected {num_jobs} variants in batch submission")
    elif num_jobs is None or num_jobs < 1:
        num_jobs = 1
    
    # Create output directory (base for all jobs in batch)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_output_dir = str(PROJECT_ROOT / "pdj_results" / f"{job_data.name}_{timestamp}")
    os.makedirs(base_output_dir, exist_ok=True)
    
    # Extract sequence length for VRAM estimation (same for all jobs in batch)
    sequence_length = None
    if 'sequence_input' in job_data.params and job_data.params['sequence_input']:
        sequence_length = len(job_data.params['sequence_input'])
    elif 'complex_components' in job_data.params:
        # For complexes, use the longest chain
        max_len = 0
        for comp in job_data.params['complex_components']:
            if comp.get('type') == 'protein' and comp.get('sequence'):
                max_len = max(max_len, len(comp['sequence']))
        if max_len > 0:
            sequence_length = max_len
    
    if sequence_length is None:
        sequence_length = 300  # Default fallback
    
    # Estimate VRAM based on model type
    from services.gpu_orchestrator import estimate_vram
    vram_estimate = estimate_vram(job_data.model_id, sequence_length)
    
    # Generate batch_id if creating multiple jobs
    batch_id = str(uuid.uuid4()) if num_jobs > 1 else None
    batch_name = job_data.name if num_jobs > 1 else None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH JOB: For multi-sequence jobs, create MSA job first
    # ═══════════════════════════════════════════════════════════════════════════
    msa_job = None
    needs_msa = False
    sequences_for_msa = []
    
    # DEBUG: Log what we're checking
    logger.warning(f"[MSA DEBUG] num_jobs={num_jobs}, has_msa_reference={job_data.params.get('msa_reference_sequence') is not None}")
    logger.warning(f"[MSA DEBUG] params keys: {list(job_data.params.keys())}")
    
    # Check if this is a mutagenesis batch (has msa_reference_sequence)
    # or any batch with multiple unique sequences that need MSA
    if job_data.params.get('msa_reference_sequence') and num_jobs > 1:
        # Mutagenesis: all variants use same MSA (reference sequence)
        needs_msa = True
        # Only need MSA for the reference sequence
        sequences_for_msa = [{
            'name': 'reference_msa',
            'sequence': job_data.params['msa_reference_sequence']
        }]
        logger.info(f"[MSA BATCH] Mutagenesis mode: 1 reference MSA for {num_jobs} variants")
    elif num_jobs > 1 and 'sequence_input' in job_data.params:
        # Multiple inference jobs with potentially different sequences
        # For now, skip MSA batching if all use same sequence (normal parallel runs)
        pass
    elif 'boltz_use_msa' in job_data.params and job_data.params.get('boltz_use_msa', True):
        # Single job that needs MSA - handled by normal flow (no separate MSA job)
        pass
    
    if needs_msa and sequences_for_msa:
        import json as json_lib
        msa_job_id = str(uuid.uuid4())
        msa_output_dir = str(Path(base_output_dir) / "msa_batch")
        os.makedirs(msa_output_dir, exist_ok=True)
        
        msa_job = Job(
            id=msa_job_id,
            name=f"{job_data.name}_msa",
            model_id='msa_batch',
            mode='msa_generation',
            params={
                'sequences': sequences_for_msa,
                'sequences_json': json_lib.dumps(sequences_for_msa),
                'reference_sequence': job_data.params.get('msa_reference_sequence'),
            },
            output_dir=msa_output_dir,
            status=JobStatus.QUEUED.value,
            batch_id=batch_id,
            batch_name=batch_name,
            queue_status='queued',
            vram_estimate_mb=3000,  # MSA uses ~3GB VRAM
            sequence_length=len(sequences_for_msa[0]['sequence']) if sequences_for_msa else 300,
            priority=10,  # HIGH priority - unblocks inference jobs
            job_phase='msa_generation',
            msa_sequences=sequences_for_msa,
        )
        session.add(msa_job)
        logger.info(f"[MSA BATCH] Created MSA batch job {msa_job_id[:8]}... for {len(sequences_for_msa)} sequences")
    
    logger.info(f"[QUEUE] Creating {num_jobs} job(s) for '{job_data.name}': model={job_data.model_id}, seq_len={sequence_length}, vram_est={vram_estimate}MB")
    
    created_jobs = []
    first_job = None
    
    for i in range(num_jobs):
        job_id = str(uuid.uuid4())
        
        # For multiple jobs: use sim_1, sim_2, etc. subdirectories (or variant names for mutagenesis)
        if mutagenesis_variants and i < len(mutagenesis_variants):
            # Mutagenesis mode: use variant name and sequence
            variant = mutagenesis_variants[i]
            job_name = f"{job_data.name}_{variant.get('name', f'var_{i+1}')}"
            output_dir = str(Path(base_output_dir) / variant.get('name', f'var_{i+1}'))
            # Override sequence with variant-specific sequence
            job_params = {**job_data.params}
            job_params['sequence'] = variant.get('sequence')
            job_params['sequence_name'] = variant.get('name', f'var_{i+1}')
        elif num_jobs > 1:
            job_name = f"{job_data.name}_sim{i+1}"
            output_dir = str(Path(base_output_dir) / f"sim_{i+1}")
            job_params = job_data.params
        else:
            job_name = job_data.name
            output_dir = base_output_dir
            job_params = job_data.params
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Determine queue status: if MSA job exists, this job waits for it
        initial_queue_status = 'pending_msa' if msa_job else 'queued'
        parent_msa_id = msa_job.id if msa_job else None
        
        # Create job record with queue fields
        job = Job(
            id=job_id,
            name=job_name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_params,  # Variant-specific params for mutagenesis
            output_dir=output_dir,
            status=JobStatus.QUEUED.value,
            # Batch grouping for job sets
            batch_id=batch_id,
            batch_name=batch_name,
            # GPU Orchestrator fields
            queue_status=initial_queue_status,
            vram_estimate_mb=vram_estimate,
            sequence_length=sequence_length,
            priority=0,  # Default priority
            paused=False,
            retry_count=0,
            max_retries=2,
            oom_tolerance='allow',
            # MSA parent-child linking
            parent_job_id=parent_msa_id,
            job_phase='inference',
        )
        session.add(job)
        created_jobs.append(job)
        
        if first_job is None:
            first_job = job
    
    await session.commit()
    
    # Refresh first job for response
    await session.refresh(first_job)
    
    if num_jobs > 1:
        logger.info(f"[BATCH] Created batch {batch_id[:8]}... with {num_jobs} jobs: {[j.name for j in created_jobs]}")
    
    # NOTE: Jobs are now launched by the GPU Orchestrator, not directly here
    # The orchestrator polls for queued jobs and assigns them to GPUs
    # based on VRAM availability and bin-packing algorithm.
    # Each job in the batch gets its own GPU assignment.
    
    return JobResponse(
        id=first_job.id,
        name=first_job.name,
        status=first_job.status,
        model_id=first_job.model_id,
        mode=first_job.mode,
        params=first_job.params,
        created_at=first_job.created_at,
        started_at=first_job.started_at,
        completed_at=first_job.completed_at,
        output_dir=first_job.output_dir,
        error_message=first_job.error_message,
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


@router.post("/{job_id}/resubmit")
async def resubmit_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):

    """
    Resubmit a failed or cancelled job with the same parameters.
    Creates a new job with a fresh ID but copies all settings from the original.
    """
    # Find original job
    result = await session.execute(select(Job).where(Job.id == job_id))
    original_job = result.scalar_one_or_none()
    
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow resubmit for failed or cancelled jobs
    if original_job.status not in [JobStatus.FAILED.value, JobStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resubmit job with status: {original_job.status}. Only failed or cancelled jobs can be resubmitted."
        )
    
    # Build resubmit name
    resubmit_suffix = "_resubmit"
    base_name = original_job.name
    # Handle multiple resubmits by not doubling suffix
    if base_name.endswith(resubmit_suffix):
        new_name = base_name
    else:
        new_name = f"{base_name}{resubmit_suffix}"
    
    # Create new job with same params - GPU orchestrator will pick it up
    import uuid
    import os
    from pathlib import Path
    
    # Create new output directory for resubmitted job
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    output_dir = str(PROJECT_ROOT / "pdj_results" / f"{new_name}_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)
    
    new_job = Job(
        id=str(uuid.uuid4()),
        name=new_name,
        model_id=original_job.model_id,
        mode=original_job.mode,
        params=original_job.params or {},
        status=JobStatus.QUEUED.value,
        created_at=datetime.utcnow(),
        output_dir=output_dir,
        # Preserve batch info if any
        batch_id=original_job.batch_id,
        batch_name=original_job.batch_name,
        # GPU Orchestrator fields - let orchestrator pick it up
        queue_status='queued',
        vram_estimate_mb=original_job.vram_estimate_mb,
        sequence_length=original_job.sequence_length,
        priority=0,
        paused=False,
        retry_count=0,
        max_retries=2,
    )

    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)
    
    # No need to manually queue - GPU orchestrator picks up jobs with queue_status='queued'
    
    return {
        "message": "Job resubmitted successfully",
        "original_job_id": job_id,
        "new_job_id": new_job.id,
        "new_job_name": new_job.name
    }




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
    Get docking results for a completed DiffDock or Uni-Dock job.
    
    Returns list of pose files with scores, sorted by rank.
    Handles both DiffDock (SDF with confidence) and Uni-Dock (PDB with affinity).
    """
    import re
    import json
    from pathlib import Path
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        return {"sdfs": [], "message": "No output directory configured"}
    
    from services.nextflow import PROJECT_ROOT
    
    # Check both DiffDock and Uni-Dock directories
    diffdock_dir = PROJECT_ROOT / job.output_dir / "run" / "diffdock" / "results"
    unidock_dir = PROJECT_ROOT / job.output_dir / "run" / "unidock" / "filtered"
    
    sdfs = []
    engines_used = []
    
    # Parse DiffDock results
    if diffdock_dir.exists():
        engines_used.append('diffdock')
        for sdf_file in diffdock_dir.rglob("*.sdf"):
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
                "engine": "diffdock",
                "name": sdf_file.name,
                "path": str(sdf_file.relative_to(PROJECT_ROOT)),
                "absolute_path": str(sdf_file),
                "confidence": confidence,
                "affinity": None,
                "rank": rank,
                "complex_name": sdf_file.parent.name
            })
    
    # Parse Uni-Dock results
    if unidock_dir.exists():
        engines_used.append('unidock')
        scores_file = unidock_dir / "scores.json"
        
        # Load scores if available
        score_map = {}
        if scores_file.exists():
            try:
                scores_data = json.loads(scores_file.read_text())
                for entry in scores_data:
                    score_map[entry['pdb_file']] = entry
            except Exception as e:
                print(f"Warning: Failed to parse Uni-Dock scores: {e}")
        
        for pdb_file in unidock_dir.glob("*.pdb"):
            entry = score_map.get(pdb_file.name, {})
            
            sdfs.append({
                "engine": "unidock",
                "name": pdb_file.name,
                "path": str(pdb_file.relative_to(PROJECT_ROOT)),
                "absolute_path": str(pdb_file),
                "confidence": None,
                "affinity": entry.get('affinity_kcal_mol'),
                "rank": entry.get('rank'),
                "ligand": entry.get('ligand'),
                "pose": entry.get('pose'),
            })
    
    if not sdfs:
        return {"sdfs": [], "message": "No docking results found"}
    
    # Sort by rank (handle None correctly)
    sdfs = sorted(sdfs, key=lambda x: x.get('rank') if x.get('rank') is not None else 999)
    
    return {
        "sdfs": sdfs,
        "total": len(sdfs),
        "engines": engines_used,
        "is_dual_mode": len(engines_used) == 2,
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
    Get the content of a specific docking result file for 3D visualization.
    Handles both DiffDock SDF files and Uni-Dock PDB files.
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
    
    # Search both DiffDock and Uni-Dock directories
    diffdock_dir = PROJECT_ROOT / job.output_dir / "run" / "diffdock" / "results"
    unidock_dir = PROJECT_ROOT / job.output_dir / "run" / "unidock" / "filtered"
    
    found_files = []
    
    # Search DiffDock results
    if diffdock_dir.exists():
        found_files.extend(list(diffdock_dir.rglob(filename)))
    
    # Search Uni-Dock results
    if unidock_dir.exists():
        found_files.extend(list(unidock_dir.glob(filename)))
    
    if not found_files:
        raise HTTPException(status_code=404, detail="Docking result file not found")
    
    file_path = found_files[0]
    content = file_path.read_text()
    
    # Set appropriate media type based on file extension
    if file_path.suffix.lower() == ".sdf":
        media_type = "chemical/x-mdl-sdfile"
    elif file_path.suffix.lower() == ".pdb":
        media_type = "chemical/x-pdb"
    else:
        media_type = "text/plain"
    
    return PlainTextResponse(content, media_type=media_type)


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


@router.get("/{job_id}/docking-comparison")
async def get_docking_comparison(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get docking comparison data for a dual-docking job.
    
    Returns comparison JSON with RMSD values, agreement status, and consensus poses.
    """
    import json
    from pathlib import Path
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output directory configured")
    
    from services.nextflow import PROJECT_ROOT
    
    # Look for comparison.json from dual docking
    comparison_file = PROJECT_ROOT / job.output_dir / "run" / "docking_comparison" / "comparison.json"
    
    if not comparison_file.exists():
        raise HTTPException(status_code=404, detail="No comparison data found for this job")
    
    try:
        comparison_data = json.loads(comparison_file.read_text())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse comparison data: {e}")
    
    return {
        "comparison": comparison_data,
        "job_id": job_id
    }

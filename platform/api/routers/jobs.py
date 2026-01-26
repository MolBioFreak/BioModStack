"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List
import uuid
import os
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

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
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    include_children: bool = False,  # New param: show child jobs if True
    session: AsyncSession = Depends(get_session)
):
    """List all jobs with optional status filter and search query."""
    # Optimized query: fetch jobs and design counts in one go
    # This replaces the N+1 query loop with a single GROUP BY query
    query = (
        select(Job, func.count(Design.id).label("design_count"))
        .outerjoin(Design, Design.job_id == Job.id)
        .group_by(Job.id)
        .order_by(Job.created_at.desc())
    )
    
    # Filter out child jobs by default (show only parent/top-level jobs)
    if not include_children:
        query = query.where(Job.parent_job_id == None)
    
    if status:
        query = query.where(Job.status == status.value)
    
    if q:
        query = query.where(Job.name.ilike(f"%{q}%"))
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    rows = result.all()
    
    # Get total count (for pagination) - also exclude children
    count_query = select(func.count(Job.id))
    if not include_children:
        count_query = count_query.where(Job.parent_job_id == None)
    if status:
        count_query = count_query.where(Job.status == status.value)
    if q:
        count_query = count_query.where(Job.name.ilike(f"%{q}%"))
    total = (await session.execute(count_query)).scalar()

    
    job_responses = []
    for job, design_count in rows:
        # Fallback for structure/PDB jobs that don't have Design entries
        if design_count == 0 and job.status == JobStatus.COMPLETED.value and job.output_dir:
            # Note: This file system check is still "slow" per job, but only runs for 
            # jobs with 0 designs in DB. For pure design jobs, it's skipped.
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
            design_count=design_count,  # Now joined from DB
            batch_id=job.batch_id,
            batch_name=job.batch_name,
            current_stage=job.current_stage,
            completed_stages=job.completed_stages,
            stage_outputs=job.stage_outputs,
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
    
    # Skip validation for template jobs and mutagenesis batches
    # Mutagenesis uses mutagenesis_variants array instead of top-level sequence
    is_mutagenesis = 'mutagenesis_variants' in job_data.params
    if not job_data.model_id.startswith('template_') and not is_mutagenesis:
        # Validate model and mode
        errors = registry.validate_job_params(job_data.model_id, job_data.mode, job_data.params)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
    
    # Detect complex components for logging (info level)
    if 'complex_components' in job_data.params:
        logger.info(f"Job contains {len(job_data.params['complex_components'])} complex components")
    
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
    # PRIORITY: 1) job_data.sequence_length (explicit), 2) extract from params, 3) fallback
    sequence_length = job_data.sequence_length  # May be explicitly set by spawn scripts
    
    if sequence_length is None:
        # Try to extract from params
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
    
    # Logging MSA batch intent

    
    # Mutagenesis: generate per-variant MSAs when using MSA
    if mutagenesis_variants and num_jobs > 1:
        use_msa = job_data.params.get('boltz_use_msa', True) or job_data.params.get('rf3_use_msa', False)
        if use_msa:
            sequences_for_msa = []
            for idx, variant in enumerate(mutagenesis_variants):
                seq = variant.get('sequence')
                if not seq:
                    continue
                name = variant.get('name', f'var_{idx + 1}')
                sequences_for_msa.append({'name': name, 'sequence': seq})
            # Always regenerate MSAs for mutagenesis variants
            job_data.params['msa_force_refresh'] = True
            # Ensure no shared reference MSA is used
            job_data.params.pop('msa_reference_sequence', None)
            needs_msa = len(sequences_for_msa) > 0
            if needs_msa:
                logger.info(f"[MSA BATCH] Mutagenesis mode: {len(sequences_for_msa)} per-variant MSAs")
    # Legacy: single reference MSA for multi-sequence batches
    if not needs_msa and job_data.params.get('msa_reference_sequence') and num_jobs > 1:
        needs_msa = True
        sequences_for_msa = [{
            'name': 'reference_msa',
            'sequence': job_data.params['msa_reference_sequence']
        }]
        logger.info(f"[MSA BATCH] Reference MSA for {num_jobs} variants")
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
                'msa_force_refresh': job_data.params.get('msa_force_refresh', False),
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
            
            # Construct complex_components for BoltzFromComplex if any non-protein components present
            # The ligands array contains ALL complex components: ligands, ions, DNA, RNA, peptides
            ligand_components = job_params.pop('ligands', [])
            
            # Check if any components need the complex workflow (DNA, RNA, ligands, ions, peptides)
            if ligand_components:
                # Build complex_components array: protein + all other components
                complex_comps = [
                    {'type': 'protein', 'id': 'A', 'sequence': variant.get('sequence')}
                ]
                # Add all components from ligands array (DNA, RNA, ligands, ions, peptides)
                for comp in ligand_components:
                    comp_type = comp.get('type', 'ligand')
                    comp_entry = {
                        'type': comp_type,
                        'id': comp.get('id', 'X'),
                    }
                    # Add sequence for nucleic acids and peptides
                    if comp_type in ('dna', 'rna', 'peptide', 'protein') and comp.get('sequence'):
                        comp_entry['sequence'] = comp.get('sequence')
                    # Add CCD for standard ligands/ions
                    if comp.get('ccd'):
                        comp_entry['ccd'] = comp.get('ccd')
                    # Add SMILES for custom ligands
                    if comp.get('smiles'):
                        comp_entry['smiles'] = comp.get('smiles')
                    complex_comps.append(comp_entry)
                
                job_params['complex_components'] = complex_comps
                logger.info(f"[MUTAGENESIS] Built complex_components with {len(complex_comps)} entries for variant {variant.get('name')}")
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
        
        # Use parent_job_id from request (spawn-wait-collect) or MSA parent
        effective_parent_id = job_data.parent_job_id or parent_msa_id
        
        # Use batch_id/batch_name from request if provided (child jobs), else use local values
        effective_batch_id = job_data.batch_id or batch_id
        effective_batch_name = job_data.batch_name or batch_name
        
        # Use sequence_length from request if provided (child jobs)
        effective_seq_length = job_data.sequence_length or sequence_length
        
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
            batch_id=effective_batch_id,
            batch_name=effective_batch_name,
            # GPU Orchestrator fields
            queue_status=initial_queue_status,
            vram_estimate_mb=vram_estimate,
            sequence_length=effective_seq_length,
            pinned_gpu=job_data.pinned_gpu,  # User-specified GPU pin from frontend
            priority=0,  # Default priority
            paused=False,
            retry_count=0,
            max_retries=2,
            oom_tolerance='allow',
            # Parent-child linking (spawn-wait-collect or MSA)
            parent_job_id=effective_parent_id,
            child_stage=job_data.child_stage,  # Stage identifier for filtering
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


@router.delete("/{job_id}/permanent")
async def delete_job_permanently(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Permanently delete a job and ALL its data.
    
    DEBUG FEATURE - Removes:
    - Job from database
    - All child jobs from database
    - All designs from database  
    - Output directory (pdj_results/...)
    - Work directory (work/...)
    
    This is irreversible!
    """
    import shutil
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel if running
    if job.status == JobStatus.RUNNING.value and job.nextflow_run_id:
        try:
            await cancel_nextflow_job(job.nextflow_run_id)
        except:
            pass  # Continue with deletion even if cancel fails
    
    job_name = job.name
    output_dir = job.output_dir
    
    # Delete designs associated with this job
    from database import Design
    await session.execute(
        Design.__table__.delete().where(Design.job_id == job_id)
    )
    
    # Delete child jobs first
    child_result = await session.execute(
        select(Job).where(Job.parent_job_id == job_id)
    )
    child_jobs = child_result.scalars().all()
    
    child_output_dirs = []
    for child in child_jobs:
        if child.output_dir:
            child_output_dirs.append(child.output_dir)
        # Delete child's designs
        await session.execute(
            Design.__table__.delete().where(Design.job_id == child.id)
        )
        await session.delete(child)
    
    # Delete the job itself
    await session.delete(job)
    await session.commit()
    
    # Delete output directories
    deleted_paths = []
    if output_dir:
        output_path = PROJECT_ROOT / output_dir if not Path(output_dir).is_absolute() else Path(output_dir)
        if output_path.exists():
            try:
                shutil.rmtree(output_path)
                deleted_paths.append(str(output_path))
            except Exception as e:
                print(f"Warning: Failed to delete output dir {output_path}: {e}")
    
    for child_dir in child_output_dirs:
        child_path = PROJECT_ROOT / child_dir if not Path(child_dir).is_absolute() else Path(child_dir)
        if child_path.exists():
            try:
                shutil.rmtree(child_path)
                deleted_paths.append(str(child_path))
            except Exception as e:
                print(f"Warning: Failed to delete child output dir {child_path}: {e}")
    
    return {
        "message": f"Job '{job_name}' permanently deleted",
        "job_id": job_id,
        "children_deleted": len(child_jobs),
        "directories_deleted": deleted_paths
    }


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


# ═══════════════════════════════════════════════════════════════════════════════
# RE-INGESTION ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/{job_id}/reingest")
async def reingest_job_results(
    job_id: str,
    include_children: bool = Query(True, description="Include child jobs when re-ingesting"),
    session: AsyncSession = Depends(get_session)
):
    """
    Re-ingest design results for a job.
    
    Deletes all existing Design entries for this job and re-runs the result
    ingester to pick up any new metrics from confidence JSON files.
    
    Useful when:
    - Job was ingested with old code that didn't extract all metrics
    - New metric extraction logic was added
    - Need to refresh design data without re-running the pipeline
    """
    from sqlalchemy import delete
    from services.result_ingester import ingest_job_results
    
    # Fetch job
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build job list (parent + children)
    job_ids = [job_id]
    jobs_to_ingest = {job_id: job}
    if include_children:
        child_result = await session.execute(select(Job).where(Job.parent_job_id == job_id))
        child_jobs = child_result.scalars().all()
        for child in child_jobs:
            job_ids.append(child.id)
            jobs_to_ingest[child.id] = child
    
    total_deleted = 0
    total_created = 0
    
    for jid in job_ids:
        target_job = jobs_to_ingest.get(jid)
        if not target_job or not target_job.output_dir:
            logger.warning(f"[REINGEST] Skipping job {jid}: no output_dir")
            continue
        
        existing_count = (await session.execute(
            select(func.count(Design.id)).where(Design.job_id == jid)
        )).scalar()
        
        await session.execute(delete(Design).where(Design.job_id == jid))
        await session.commit()
        total_deleted += existing_count or 0
        
        try:
            new_count = await ingest_job_results(jid, target_job.output_dir, session)
            total_created += new_count
            logger.info(f"[REINGEST] Re-ingested {new_count} designs for job {jid}")
        except Exception as e:
            logger.error(f"[REINGEST] Error re-ingesting job {jid}: {e}")
    
    return {
        "message": f"Re-ingested {total_created} designs (deleted {total_deleted} old entries)",
        "job_id": job_id,
        "designs_deleted": total_deleted,
        "designs_created": total_created
    }


@router.post("/{job_id}/annotate-cdrs")
async def annotate_cdr_regions(
    job_id: str,
    include_children: bool = Query(True, description="Include designs from child jobs (for parent exploration jobs)"),
    session: AsyncSession = Depends(get_session)
):
    """
    Annotate CDR regions for all designs in a job using ANARCII.
    
    If include_children is True (default), also annotates designs from child jobs
    that have this job as their parent. This is needed for exploration mode where
    designs are spread across spawned child validation jobs.
    
    This runs in the background (~1-2 minutes for large jobs).
    Returns immediately with status "started".
    """
    import threading
    from services.cdr_annotator import batch_annotate_pdbs
    
    # Fetch job
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get designs - include children if requested
    if include_children:
        # Get all child job IDs for this parent
        child_query = select(Job.id).where(Job.parent_job_id == job_id)
        child_result = await session.execute(child_query)
        child_job_ids = [row[0] for row in child_result.all()]
        
        # Include both parent and children
        all_job_ids = [job_id] + child_job_ids
        designs_result = await session.execute(
            select(Design).where(Design.job_id.in_(all_job_ids))
        )
    else:
        designs_result = await session.execute(
            select(Design).where(Design.job_id == job_id)
        )
    designs = designs_result.scalars().all()
    
    if not designs:
        raise HTTPException(status_code=400, detail="No designs found for this job")
    
    # Always re-annotate ALL designs (allows fixing bad annotations)
    designs_to_annotate = list(designs)
    
    # Collect PDB paths and design IDs for background processing
    pdb_paths = [d.pdb_path for d in designs_to_annotate if d.pdb_path]
    design_ids = [d.id for d in designs_to_annotate if d.pdb_path]
    total_count = len(designs)
    
    logger.info(f"[CDR ANNOTATE] Starting background annotation on {len(pdb_paths)} designs for job {job_id}")
    
    # Background worker function
    def run_annotation_sync():
        import sqlite3
        
        # Run ANARCII batch annotation
        annotations = batch_annotate_pdbs(pdb_paths, batch_size=500)
        
        # Update database directly with sqlite (sync)
        db_path = Path(__file__).parent.parent / "biomodstack.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        annotated = 0
        for pdb_path, design_id in zip(pdb_paths, design_ids):
            annot = annotations.get(pdb_path)
            if annot:
                cursor.execute("""
                    UPDATE designs SET
                        antibody_type = ?,
                        binder_length = ?,
                        cdr_h1 = ?, cdr_h2 = ?, cdr_h3 = ?,
                        cdr_l1 = ?, cdr_l2 = ?, cdr_l3 = ?,
                        cdr_h1_length = ?, cdr_h2_length = ?, cdr_h3_length = ?,
                        cdr_l1_length = ?, cdr_l2_length = ?, cdr_l3_length = ?,
                        fr2_contacts = ?, de_loop = ?, fr3_contacts = ?, fr4_contacts = ?
                    WHERE id = ?
                """, (
                    annot.antibody_type, annot.binder_length,
                    annot.cdr_h1, annot.cdr_h2, annot.cdr_h3,
                    annot.cdr_l1, annot.cdr_l2, annot.cdr_l3,
                    annot.cdr_h1_length, annot.cdr_h2_length, annot.cdr_h3_length,
                    annot.cdr_l1_length, annot.cdr_l2_length, annot.cdr_l3_length,
                    annot.fr2_contacts, annot.de_loop, annot.fr3_contacts, annot.fr4_contacts,
                    design_id
                ))
                annotated += 1
        
        conn.commit()
        conn.close()
        logger.info(f"[CDR ANNOTATE] Completed: {annotated}/{len(pdb_paths)} designs annotated for job {job_id}")
    
    # Start background thread
    thread = threading.Thread(target=run_annotation_sync, daemon=True)
    thread.start()
    
    return {
        "message": f"CDR annotation started for {len(pdb_paths)} designs (running in background)",
        "job_id": job_id,
        "status": "started",
        "pending": len(pdb_paths),
        "total": total_count
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE CHECKPOINTING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/{job_id}/stage-complete")
async def report_stage_complete(
    job_id: str,
    stage: str,
    outputs: List[str] = [],
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has completed.
    Called by Nextflow workflows after each stage finishes.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Update completed stages
    completed = job.completed_stages or []
    if stage not in completed:
        completed.append(stage)
    job.completed_stages = completed
    
    # Update stage outputs
    stage_outputs = job.stage_outputs or {}
    stage_outputs[stage] = outputs
    job.stage_outputs = stage_outputs
    
    # Clear current stage (will be set when next stage starts)
    job.current_stage = None
    
    await session.commit()
    
    logger.info(f"Job {job_id}: Stage '{stage}' completed with {len(outputs)} outputs")
    
    return {
        "message": f"Stage '{stage}' marked complete",
        "job_id": job_id,
        "completed_stages": completed,
        "outputs_count": len(outputs)
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CHILD JOB TRACKING (Spawn-Wait-Aggregate Pattern)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{parent_id}/children/status")
async def get_children_status(
    parent_id: str,
    stage: Optional[str] = None,
    batch_name: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Get aggregate status of all children spawned by a parent job.
    
    Used by wait_for_children.py to poll until all children complete.
    
    Args:
        parent_id: Parent job ID to search for children
        stage: Optional filter by child_stage (e.g., 'rfantibody', 'fampnn', 'boltz2')
        batch_name: Optional batch_name to search (for resume scenarios where new parent
                    ID differs from original but batch_name is preserved)
    
    Resume Support:
        When a job is resumed, it gets a new parent_id but keeps the same batch_name.
        By passing batch_name, we can find children from the original run even when
        the parent_id has changed.
    
    Returns:
        - total: Total child count
        - completed/failed/running/pending: Status breakdown
        - all_done: True when all children finished (completed or failed)
        - child_output_dirs: List of output directories for aggregation
        - success_rate: Percentage of children that completed successfully
    """
    from sqlalchemy import or_
    
    # Build query for children - match by parent_id OR batch_name
    if batch_name:
        # Resume mode: search by batch_name to find children from original run
        query = select(Job).where(
            or_(
                Job.parent_job_id == parent_id,
                Job.batch_name == batch_name
            )
        )
    else:
        # Normal mode: just search by parent_id
        query = select(Job).where(Job.parent_job_id == parent_id)
    
    if stage:
        query = query.where(Job.child_stage == stage)
    
    result = await session.execute(query)
    children = result.scalars().all()
    
    if not children:
        return {
            "parent_id": parent_id,
            "stage": stage,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "running": 0,
            "pending": 0,
            "all_done": True,
            "child_output_dirs": [],
            "success_rate": 100.0
        }
    
    completed = [c for c in children if c.status == "completed"]
    failed = [c for c in children if c.status == "failed"]
    running = [c for c in children if c.status == "running"]
    pending = [c for c in children if c.status in ["queued", "pending"]]
    
    all_done = all(c.status in ["completed", "failed", "cancelled"] for c in children)
    
    # Collect output directories from completed children
    output_dirs = [
        c.child_output_dir or c.output_dir 
        for c in completed 
        if (c.child_output_dir or c.output_dir) and not c.aggregated_by_parent
    ]
    
    total = len(children)
    success_rate = (len(completed) / total * 100) if total > 0 else 0
    
    return {
        "parent_id": parent_id,
        "stage": stage,
        "total": total,
        "completed": len(completed),
        "failed": len(failed),
        "running": len(running),
        "pending": len(pending),
        "all_done": all_done,
        "child_output_dirs": output_dirs,
        "success_rate": round(success_rate, 1),
        "child_ids": [c.id for c in children]
    }


@router.post("/{parent_id}/children/mark-aggregated")
async def mark_children_aggregated(
    parent_id: str,
    stage: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Mark all completed children as aggregated by parent.
    Prevents double-collection when polling multiple times.
    """
    query = select(Job).where(
        Job.parent_job_id == parent_id,
        Job.status == "completed",
        Job.aggregated_by_parent == False
    )
    
    if stage:
        query = query.where(Job.child_stage == stage)
    
    result = await session.execute(query)
    children = result.scalars().all()
    
    for child in children:
        child.aggregated_by_parent = True
    
    await session.commit()
    
    return {
        "marked_count": len(children),
        "parent_id": parent_id,
        "stage": stage
    }


@router.post("/{job_id}/stage-start")
async def report_stage_start(
    job_id: str,
    stage: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Report that a workflow stage has started.
    Called by Nextflow workflows when entering a new stage.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.current_stage = stage
    await session.commit()
    
    logger.info(f"Job {job_id}: Stage '{stage}' started")
    
    return {"message": f"Stage '{stage}' started", "job_id": job_id}


@router.get("/{job_id}/stages")
async def get_job_stages(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get stage progress for a job.
    Returns current stage, completed stages, and output paths.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    display_stages = []
    
    if job.mode in ["antibody_denovo", "antibody_denovo_pipeline"]:
        # Dynamic stage construction for antibody workflow
        display_stages.append("rfantibody")
        
        # Check params for sequence design steps (default to true if not present, matching nextflow logic)
        params = job.params or {}
        
        # Note: In nextflow 'null' means true for these flags due to how they are processed
        run_fampnn = params.get("seq_design_fampnn")
        if run_fampnn is None or run_fampnn is True:
            display_stages.append("fampnn")
            
        run_antifold = params.get("seq_design_antifold")
        if run_antifold is None or run_antifold is True:
            display_stages.append("antifold")
            
        run_proteinmpnn = params.get("seq_design_proteinmpnn")
        if run_proteinmpnn is None or run_proteinmpnn is True:
            display_stages.append("proteinmpnn")
            
        # Validation stages
        if params.get("run_structure_validation") is not False:
            display_stages.append("boltz2")
            
        if params.get("run_immunogenicity_scoring") is not False:
             display_stages.append("antiberty")
             
        if params.get("run_stability_scoring") is not False:
             display_stages.append("thermompnn")
             
    else:
        # Fallback for other modes
        all_stages_map = {
            "binder_denovo": ["rfdiffusion", "proteinmpnn", "boltz2"],
            "monomer_denovo": ["rfdiffusion", "proteinmpnn", "af2"],
        }
        display_stages = all_stages_map.get(job.mode, [])

    all_stages = display_stages
    completed = job.completed_stages or []
    
    return {
        "job_id": job_id,
        "mode": job.mode,
        "all_stages": all_stages,
        "current_stage": job.current_stage,
        "completed_stages": completed,
        "stage_outputs": job.stage_outputs or {},
        # Allow resume if failed/cancelled, even if no stages fully completed (rely on cache)
        "can_resume": job.status in ["failed", "cancelled"]
    }


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: str,
    from_stage: str = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Resume a failed job from a checkpoint.
    
    If from_stage is specified, restarts from that stage using existing outputs.
    If not specified, resumes from the last completed stage.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ["failed", "cancelled"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume job with status: {job.status}"
        )
    
    completed = job.completed_stages or []
    # Relaxed restriction: Allow resume even if no stages completed (start from scratch with cache)
    
    # Determine work directory for resumption
    # We use the shared 'work' directory in project root by default
    # This allows Nextflow to find cached tasks from the previous run
    resume_work_dir = "work"
    
    # Create new job with resume info
    import uuid
    new_job_id = str(uuid.uuid4())
    base_name = job.name.replace("_resubmit", "").replace("_resumed", "")
    new_name = f"{base_name}_resumed"
    
    # Generate output directory for the resumed job
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = str(PROJECT_ROOT / "pdj_results" / f"{new_name}_{timestamp}")
    os.makedirs(output_dir, exist_ok=True)
    
    new_job = Job(
        id=new_job_id,
        name=new_name,
        status="queued",
        model_id=job.model_id,
        mode=job.mode,
        params={
            **job.params,
            "resume_job_id": job_id,
            "resume_work_dir": resume_work_dir,
            # We don't need manual stage skipping params because we use -resume
        },
        output_dir=output_dir,
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        # Don't copy completed_stages/stage_outputs - they will be re-populated
        # as the resumed workflow re-emits cached results
        completed_stages=[], 
        stage_outputs={},
        
        # GPU Orchestrator fields - critical for scheduling
        queue_status='queued',
        vram_estimate_mb=job.vram_estimate_mb,
        sequence_length=job.sequence_length,
        priority=job.priority,
    )
    
    session.add(new_job)
    await session.commit()
    
    logger.info(f"Job {job_id} resumed as {new_job_id} using work dir '{resume_work_dir}'")
    
    return {
        "message": f"Job resumed. Checking cache in '{resume_work_dir}'",
        "original_job_id": job_id,
        "new_job_id": new_job_id,
        "new_job_name": new_name,
        "resume_from_stage": from_stage or "auto",
        "preserved_stages": []
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
    tail: int = 200,
    session: AsyncSession = Depends(get_session)
):
    """
    Get log output for a job.
    
    Searches for Nextflow work directory logs (.command.log, .command.err)
    and returns structured log data with error extraction.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    logs_data = {
        "job_id": job_id,
        "job_name": job.name,
        "status": job.status,
        "command_log": None,
        "command_err": None,
        "nextflow_log": None,
        "exit_code": None,
        "parsed_error": None,
    }
    
    # Try to find work directory logs
    # Work dirs are under PROJECT_ROOT/work/ with hash-based paths
    work_dir = PROJECT_ROOT / "work"
    
    if work_dir.exists():
        # Find most recent .command.log files (search by modification time)
        import subprocess
        try:
            # Find command logs modified in last hour, sorted by time
            result = subprocess.run(
                ["find", str(work_dir), "-name", ".command.log", "-mmin", "-60", 
                 "-exec", "stat", "--format=%Y %n", "{}", ";"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse output and get most recent log
                logs_with_time = []
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        try:
                            mtime = int(parts[0])
                            path = parts[1]
                            logs_with_time.append((mtime, path))
                        except ValueError:
                            continue
                
                # Sort by time descending (most recent first)
                logs_with_time.sort(reverse=True)
                
                # Read most recent log that might belong to this job
                # For now, just take most recent - could match by job name in future
                if logs_with_time:
                    log_path = Path(logs_with_time[0][1])
                    log_dir = log_path.parent
                    
                    # Read .command.log
                    if log_path.exists():
                        with open(log_path, 'r') as f:
                            lines = f.readlines()
                            logs_data["command_log"] = "".join(lines[-tail:])
                    
                    # Read .command.err if exists
                    err_path = log_dir / ".command.err"
                    if err_path.exists():
                        with open(err_path, 'r') as f:
                            logs_data["command_err"] = f.read()
                    
                    # Read exit code
                    exit_path = log_dir / ".exitcode"
                    if exit_path.exists():
                        with open(exit_path, 'r') as f:
                            try:
                                logs_data["exit_code"] = int(f.read().strip())
                            except ValueError:
                                pass
                    
                    # Parse error from logs
                    logs_data["parsed_error"] = extract_error_from_logs(
                        logs_data["command_log"], 
                        logs_data["command_err"]
                    )
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.warning(f"Error searching work dir: {e}")
    
    # Fallback: Read .nextflow.log from output_dir
    if job.output_dir:
        # Check for explicitly saved nextflow.log first (from nextflow.py update)
        nf_log_path = Path(job.output_dir) / "nextflow.log"
        
        if not nf_log_path.exists():
            nf_log_path = Path(job.output_dir) / ".nextflow.log"
            
        if not nf_log_path.exists():
            nf_log_path = PROJECT_ROOT / ".nextflow.log"
        
        if nf_log_path.exists():
            try:
                with open(nf_log_path, 'r') as f:
                    lines = f.readlines()
                    logs_data["nextflow_log"] = "".join(lines[-50:])
            except Exception:
                pass
    
    return logs_data


def extract_error_from_logs(command_log: str = None, command_err: str = None) -> str:
    """
    Extract meaningful error message from log files.
    
    Looks for common error patterns:
    - Python tracebacks
    - CUDA errors
    - OOM messages
    - Permission errors
    """
    if not command_log and not command_err:
        return None
    
    combined = (command_log or "") + "\n" + (command_err or "")
    lines = combined.split('\n')
    
    # Priority patterns to search for
    error_patterns = [
        "CUDA out of memory",
        "CUDA error:",
        "RuntimeError:",
        "OSError:",
        "FileNotFoundError:",
        "ModuleNotFoundError:",
        "OOM",
        "Killed",
        "Traceback (most recent call last):",
        "Error:",
        "bad substitution",
    ]
    
    # Find the most relevant error
    for pattern in error_patterns:
        for i, line in enumerate(lines):
            if pattern in line:
                # Return context around the error (5 lines before, 10 after)
                start = max(0, i - 2)
                end = min(len(lines), i + 8)
                return "\n".join(lines[start:end]).strip()
    
    # Fallback: return last 5 non-empty lines
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        return "\n".join(non_empty[-5:]).strip()
    
    return None


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
